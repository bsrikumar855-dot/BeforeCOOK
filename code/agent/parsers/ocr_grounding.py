"""Local OCR as a deterministic grounding check for screenshot extraction (no model calls).

A resource id proposed by the vision model is accepted only if it appears in that image's OCR text; otherwise it is
UNGROUNDED, dropped and counted. OCR is used only to *check* ids, never to add them: Tesseract also reads dates and
mangled digits as ids, so OCR-only ids are listed in the report and not auto-added.

C10: one base-resolution pass missed ids that are plainly visible (small table text), so every image is read with
several passes and the texts are united (PASSES; provenance in CONSTANTS.md). Each pass is cached by
sha256(image bytes + engine version + pass config), so repeated runs are byte-identical and need no OCR engine
once the cache exists.

Matching is exact on every letter and digit. The one normalisation: in table rows Tesseract renders an id's
underscore as a space, a period or nothing ("export 1261", "data_flow.9846", "data_flow1121"), so each "_" in the
id may match one of "_", " ", "." or nothing. No character is ever substituted (no l/1 or O/0 swaps, no edit distance).
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
# (name, upscale factor, autocontrast, tesseract config); every pass starts from the grayscale image.
PASSES = (("base", 1, False, ""), ("x2", 2, False, ""), ("x2_psm11", 2, False, "--psm 11"),
          ("x3_autocontrast_psm6", 3, True, "--psm 6"))
SEP = r"[_ .]?"                                   # how OCR renders an underscore inside an id
# The separator inside the prefix is mandatory (an optional one backtracked exponentially: the first C10 run
# hung) and the prefix is at most 4 words of 32 letters (unbounded, a long run of words was quadratic: G8).
TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])([a-z]{1,32}(?:[_ .][a-z]{1,32}){0,3})[_ .]?(\d{1,12})(?![0-9])")


class OcrUnavailable(CacheMiss):
    """No cached OCR text and no OCR engine: fail loudly instead of accepting ungrounded ids."""


def _engine():
    import pytesseract
    cmd = os.environ.get("TESSERACT_CMD") or shutil.which("tesseract") or (str(WINDOWS_DEFAULT) if WINDOWS_DEFAULT.exists() else None)
    if not cmd:
        raise OcrUnavailable("Tesseract binary not found (set TESSERACT_CMD or install tesseract-ocr)")
    pytesseract.pytesseract.tesseract_cmd = cmd
    return pytesseract, f"tesseract {pytesseract.get_tesseract_version()}"


def pass_config(p: tuple) -> str:
    name, scale, contrast, cfg = p
    return f"{name}:gray:scale={scale}:lanczos:autocontrast={int(contrast)}:config={cfg}"


class OcrReader:
    def __init__(self, cache_root: Path = CACHE_DIR / "ocr"):
        self.root = cache_root
        self.engines_used: set[str] = set()
        self._engine = None

    def _cached(self, image_sha: str, config: str) -> dict | None:
        for p in sorted(self.root.glob("*.json")):
            entry = json.loads(p.read_text(encoding="utf-8"))
            if entry.get("image_sha256") == image_sha and entry.get("pass") == config:
                return entry
        return None

    def text(self, file_id: str, raw: bytes) -> str:
        """United OCR text of all passes for an image, from the content-addressed cache when present."""
        self.root.mkdir(parents=True, exist_ok=True)
        image_sha = hashlib.sha256(raw).hexdigest()
        texts = []
        for p in PASSES:
            config = pass_config(p)
            entry = self._cached(image_sha, config)
            if entry is None:
                entry = self._run(file_id, raw, image_sha, p, config)
            self.engines_used.add(entry["engine"])
            texts.append(entry["text"])
        return "\n".join(texts)

    def _run(self, file_id: str, raw: bytes, image_sha: str, p: tuple, config: str) -> dict:
        try:
            pytesseract, engine = self._engine or _engine()
        except ImportError as exc:
            raise OcrUnavailable(f"pytesseract not installed: {exc}") from exc
        self._engine = (pytesseract, engine)
        from io import BytesIO

        from PIL import Image, ImageOps
        _, scale, contrast, cfg = p
        img = Image.open(BytesIO(raw)).convert("L")
        if scale != 1:
            img = img.resize((img.width * scale, img.height * scale), Image.LANCZOS)
        if contrast:
            img = ImageOps.autocontrast(img)
        text = pytesseract.image_to_string(img, config=cfg)
        key = hashlib.sha256(raw + engine.encode() + config.encode()).hexdigest()
        entry = {"file_id": file_id, "image_sha256": image_sha, "engine": engine, "pass": config, "text": text}
        (self.root / f"{key}.json").write_text(json.dumps(entry, indent=1) + "\n", encoding="utf-8", newline="\n")
        return entry


def token_pattern(token: str) -> re.Pattern:
    body = SEP.join(re.escape(part) for part in token.split("_"))
    return re.compile(r"(?<![A-Za-z0-9_])" + body + r"(?![A-Za-z0-9_])")


def grounded(token: str, ocr_text: str) -> bool:
    return token_pattern(token).search(ocr_text) is not None


def ocr_ids(ocr_text: str, known_prefixes: set[str]) -> set[str]:
    """Ids in canonical form (underscores restored) whose prefix is a known resource prefix."""
    out = set()
    for prefix, digits in TOKEN_RE.findall(ocr_text):
        canon = re.sub(r"[ .]", "_", prefix)
        if canon in known_prefixes:
            out.add(f"{canon}_{digits}")
    return out
