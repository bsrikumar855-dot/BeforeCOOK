"""Deterministic PDF text-layer extraction (stdlib only: zlib, ASCII85, ToUnicode CMaps).

All four PDFs in the dataset carry a text layer, so no model is needed for
them. A PDF without a text layer is reported as NEEDS_EXTRACTION.
"""
from __future__ import annotations

import base64
import re
import zlib

from ..identity import EMAIL_RE, IdentityUniverse
from ..schema import EvidenceFile, Identifier, Observation
from .parsed_file import ParsedFile

BS = b"\x5c"
TEXT_OPS = rb"/([^\s]+)\s+[\d.]+\s+Tf|\(((?:[^()\x5c]|\x5c.)*)\)\s*Tj|<([0-9A-Fa-f]+)>\s*Tj|\bET\b|(T\*|Td|TD)"


def _blocks(data: bytes, start: re.Pattern, end: bytes) -> list[tuple[re.Match, bytes]]:
    """(start match, body up to the next `end`) in order. A linear scan: a lazy `start(.*?)end` regex rescans to the
    end of the input from every start that has no `end` after it (G8: > 30 s on a 1 MB input)."""
    out, pos = [], 0
    while True:
        m = start.search(data, pos)
        if not m:
            return out
        stop = data.find(end, m.end())
        if stop < 0:
            return out
        out.append((m, data[m.end():stop].strip()))
        pos = stop + len(end)


OBJ_START = re.compile(rb"(?<!\d)(\d{1,10}) 0 obj")


def _objects(raw: bytes) -> dict[int, bytes]:
    return {int(m.group(1)): body for m, body in _blocks(raw, OBJ_START, b"endobj")}


def _stream(obj: bytes) -> bytes:
    m = re.match(rb"<<(.*)>>\s*stream\r?\n(.*?)\r?\n?endstream", obj, re.S)
    if not m:
        return b""
    meta, data = m.group(1), m.group(2)
    for flt in re.findall(rb"/(ASCII85Decode|FlateDecode)", meta):
        if flt == b"ASCII85Decode":
            data = data.strip()
            data = base64.a85decode(data[:-2] if data.endswith(b"~>") else data)
        else:
            data = zlib.decompress(data)
    return data


def _cmap(data: bytes) -> dict[int, str]:
    out: dict[int, str] = {}
    for _, blk in _blocks(data, re.compile(rb"beginbfchar"), b"endbfchar"):
        for a, u in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", blk):
            out[int(a, 16)] = bytes.fromhex(u.decode()).decode("utf-16-be")
    for _, blk in _blocks(data, re.compile(rb"beginbfrange"), b"endbfrange"):
        for a, z, u in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", blk):
            for i in range(int(a, 16), int(z, 16) + 1):
                out[i] = chr(int(u, 16) + i - int(a, 16))
    return out


def text_layer(raw: bytes) -> str:
    objs = _objects(raw)
    fonts: dict[bytes, dict[int, str] | None] = {}
    for o in objs.values():
        if re.search(rb"/Type\s*/Font[\s>/]", o) and b"/Name /" in o:
            name = re.search(rb"/Name /([^\s/]+)", o).group(1)
            tu = re.search(rb"/ToUnicode (\d+) 0 R", o)
            fonts[name] = _cmap(_stream(objs[int(tu.group(1))])) if tu and int(tu.group(1)) in objs else None
    lines: list[str] = []
    for key in sorted(objs):
        o = objs[key]
        if b"/Type /Page" not in o or b"/Contents" not in o:
            continue
        ref = int(re.search(rb"/Contents (\d+) 0 R", o).group(1))
        content = _stream(objs.get(ref, b""))
        cur: list[str] = []
        font = None
        for tok in re.finditer(TEXT_OPS, content, re.S):
            if tok.group(1):
                font = tok.group(1)
            elif tok.group(2) is not None or tok.group(3):
                raw_text = (tok.group(2).replace(BS + b"(", b"(").replace(BS + b")", b")").replace(BS + BS, BS)
                            if tok.group(2) is not None else bytes.fromhex(tok.group(3).decode()))
                fmap = fonts.get(font)
                cur.append("".join(fmap.get(c, "?") for c in raw_text) if fmap else raw_text.decode("latin-1"))
            elif cur:
                lines.append("".join(cur))
                cur = []
        if cur:
            lines.append("".join(cur))
    return "\n".join(lines)


def parse_pdf(ef: EvidenceFile, raw: bytes, universe: IdentityUniverse, known_prefixes: set[str],
              aliases: dict[str, str]) -> ParsedFile:
    text = text_layer(raw)
    out = ParsedFile(text=text)
    if not text.strip():
        ef.status = "NEEDS_EXTRACTION"
        return out
    ef.status = "TEXT_LAYER"
    doc_ids: list[Identifier] = []
    for e in sorted({m.lower() for m in EMAIL_RE.findall(text)}):
        doc_ids.append(Identifier("email", e, "pdf_text", "DOCUMENT_COMENTION"))
    for token in sorted(set(re.findall(r"[A-Za-z0-9_.\-]+", text))):
        if token in universe.user_ids:
            doc_ids.append(Identifier("user_id", token, "pdf_text", "DOCUMENT_COMENTION"))
    resources: dict[str, str] = {}
    for m in re.finditer(r"\b([a-z]+(?:_[a-z]+)*)_(\d+)\b", text):
        if m.group(1) in known_prefixes and m.group(0) not in universe.user_ids:
            resources[m.group(0)] = m.group(0)
    for alias, rid in aliases.items():
        if alias in text:
            resources[rid] = alias
    doc_kind = "attachment" if "attachment" in text.lower() else "document"
    for i, (rid, via) in enumerate(sorted(resources.items())):
        obs = Observation(f"{ef.file_id}#pdf{i}", ef.file_id, ef.captured_at, None, "PDF_TEXT", None, "SINGLE",
                          "pdf_text", rid, {"doc_kind": doc_kind, "matched_token": via})
        obs.identifiers.extend(doc_ids)
        out.observations.append(obs)
    return out
