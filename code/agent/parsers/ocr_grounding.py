"""Local OCR as a deterministic grounding check for screenshot extraction (no model calls).

A resource id proposed by the vision model is accepted only if the exact token appears in that image's OCR
text; otherwise it is UNGROUNDED, dropped and counted. OCR is used only to *check* ids, never to add them:
Tesseract reads ids reliably but mangles emails and some digits and produces false hits such as dates, so
OCR-only ids are listed in the report and not auto-added. OCR text is cached by sha256(image bytes + engine
version), so repeated runs are byte-identical and need no OCR engine once the cache exists.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from pathlib import Path

from ..llm_client import CACHE_DIR, CacheMiss

WINDOWS_DEFAULT = Path("C:/Program Files/Tesseract-OCR/tesseract.exe")
TOKEN_RE = re.compile(r"(?<![a-z0-9])[a-z]+(?:_[a-z]+)*_\d+(?![0-9])")


class OcrUnavailable(CacheMiss):
    """No cached OCR text and no OCR engine: fail loudly instead of accepting ungrounded ids."""


def _engine():
    import pytesseract
    cmd = os.environ.get("TESSERACT_CMD") or shutil.which("tesseract") or (str(WINDOWS_DEFAULT) if WINDOWS_DEFAULT.exists() else None)
    if not cmd:
        raise OcrUnavailable("Tesseract binary not found (set TESSERACT_CMD or install tesseract-ocr)")
    pytesseract.pytesseract.tesseract_cmd = cmd
    return pytesseract, f"tesseract {pytesseract.get_tesseract_version()}"


class OcrReader:
    def __init__(self, cache_root: Path = CACHE_DIR / "ocr"):
        self.root = cache_root
        self.engines_used: set[str] = set()

    def text(self, file_id: str, raw: bytes) -> str:
        """OCR text for an image, from the content-addressed cache when present."""
        self.root.mkdir(parents=True, exist_ok=True)
        image_sha = hashlib.sha256(raw).hexdigest()
        for p in sorted(self.root.glob("*.json")):
            entry = json.loads(p.read_text(encoding="utf-8"))
            if entry.get("image_sha256") == image_sha:
                self.engines_used.add(entry["engine"])
                return entry["text"]
        try:
            pytesseract, engine = _engine()
        except ImportError as exc:
            raise OcrUnavailable(f"pytesseract not installed: {exc}") from exc
        from io import BytesIO

        from PIL import Image
        text = pytesseract.image_to_string(Image.open(BytesIO(raw)))
        key = hashlib.sha256(raw + engine.encode()).hexdigest()
        (self.root / f"{key}.json").write_text(json.dumps({"file_id": file_id, "image_sha256": image_sha,
                                                            "engine": engine, "text": text}, indent=1) + "\n",
                                               encoding="utf-8", newline="\n")
        self.engines_used.add(engine)
        return text


def grounded(token: str, ocr_text: str) -> bool:
    return re.search(r"(?<![A-Za-z0-9_])" + re.escape(token) + r"(?![A-Za-z0-9_])", ocr_text) is not None


def ocr_ids(ocr_text: str, known_prefixes: set[str]) -> set[str]:
    return {t for t in TOKEN_RE.findall(ocr_text) if t.rsplit("_", 1)[0] in known_prefixes}
