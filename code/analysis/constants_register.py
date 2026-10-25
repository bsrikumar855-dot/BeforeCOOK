"""G3: regenerate the literal register and the config-key table in CONSTANTS.md (between the GENERATED markers).

Every numeric literal in code/ (tests and the trivial 0/1/-1 excluded) and every key in code/config/*.yaml gets a
row. Literals are classified by the explicit RULES below (context line or file), or by MANUAL entries; an
unclassified literal stops the script, so the register can never silently skip one. The meaningful constants are
explained by hand in the main table of CONSTANTS.md; this register is the completeness check behind it
(code/tests/test_constants_coverage.py re-derives the literal set independently).

Run:  python code/analysis/constants_register.py
"""
from __future__ import annotations

import ast
import re
import sys
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONSTANTS = ROOT / "CONSTANTS.md"
BEGIN, END = "<!-- BEGIN GENERATED REGISTER -->", "<!-- END GENERATED REGISTER -->"
HTTP = {200, 400, 401, 403, 404, 429, 500, 502, 503, 504}

# (file regex, line regex, role, label, what breaks if changed); first match wins.
RULES = [
    (r"analysis/constants_register\.py$", r"", "this register's own classification tables (values classified elsewhere)",
     "JUDGEMENT", "only the register"),
    (r"custom_cases/", r"", "fictional fixture data (case numbers, step numbers, hash filler, padding repeat)", "JUDGEMENT",
     "only that fixture; expected outputs are written next to it"),
    (r"", r"\.group\(|m\[\d\]", "regex capture-group index", "STANDARD", "the wrong capture is read"),
    (r"", r"hexdigest\(\)\[:\d+\]", "hash-prefix length (cache keys, prompt versions, nonces, call ids)", "JUDGEMENT",
     "existing cache keys / prompt versions no longer match: every cached reply misses"),
    (r"", r"parents\[\d\]", "repository-root depth of this file", "STANDARD", "paths resolve outside the repo"),
    (r"", r"int\([^)]*,\s*16\)", "hexadecimal base", "STANDARD", "CMap / hex decoding breaks"),
    (r"", r"indent=\d", "JSON pretty-print indent", "STANDARD", "only file formatting (cache files are byte-compared)"),
    (r"", r"timeout=\d", "network timeout in seconds (model listing / key probe)", "JUDGEMENT",
     "slow providers time out sooner or later; no decision changes"),
    (r"", r"struct\.pack|IHDR|crc32", "PNG chunk layout of the synthetic probe image", "BOUND",
     "PNG specification (ISO/IEC 15948); an invalid probe image"),
    (r"", r"total_seconds\(\)\s*/\s*(60|3600)|/\s*86400|\*\s*3600|//\s*60|/\s*1e6|100\.0\s*\*", "unit conversion (minutes, hours, days, millions, percent)",
     "STANDARD", "wrong units in printed numbers"),
    (r"", r"\[:-?\d+\]|\[-\d+:\]|\[\d+:\]", "slice: length cap on diagnostic text, or a fixed prefix/suffix strip", "JUDGEMENT",
     "only how much diagnostic text is kept (caps), or the strip of a fixed marker such as 'log:' or '~>'"),
]
# (file suffix, value) -> (role, label, breaks)
MANUAL = {
    ("code/agent/attribution.py", 3): ("±3 h deletion-burst half-width (timedelta(hours=3)); also 3 identifiers shown in a rationale",
                                       "JUDGEMENT", "candidate sets grow or shrink; see main table"),
    ("code/agent/intent.py", 5000): ("request-text classification cap (C7a)", "JUDGEMENT", "see main table"),
    ("code/agent/intent.py", 20): ("fallback batch size if models.yaml omits tasks.intent.batch_size (yaml sets 40)",
                                   "JUDGEMENT", "only when the yaml key is missing"),
    ("code/agent/llm_client.py", 60.0): ("seconds per minute in the RPM throttle", "STANDARD", "throttle interval wrong"),
    ("code/agent/llm_client.py", 10): ("fallback requests_per_minute if a model omits it", "JUDGEMENT", "only when the yaml key is missing"),
    ("code/agent/llm_client.py", 2): ("MAX_SCHEMA_RETRIES (mirrors models.yaml limits.schema_retries); also the "
                                      "exponential backoff multiplier (delay *= 2)", "JUDGEMENT", "see main table"),
    ("code/agent/llm_client.py", 3): ("MAX_HTTP_RETRIES (mirrors models.yaml limits.max_429_retries_per_attempt)",
                                      "JUDGEMENT", "see main table"),
    ("code/agent/llm_client.py", 60): ("MAX_BACKOFF_SECONDS (mirrors models.yaml limits.backoff_max_seconds)",
                                       "STANDARD", "see main table"),
    ("code/agent/parsers/ocr_grounding.py", 2): ("OCR pass upscale factor 2x", "JUDGEMENT", "see main table (OCR passes)"),
    ("code/agent/parsers/ocr_grounding.py", 3): ("OCR pass upscale factor 3x; F1 minimum detail-field labels (MIN_DETAIL_LABELS)",
                                                 "JUDGEMENT", "see main table (OCR passes, F1 absence proof)"),
    ("code/agent/schema.py", 4): ("index where REMEDIATION operations start in the spec operation table", "SPEC",
                                  "phase of every operation; validator rejects every row"),
    ("code/agent/schema.py", 9): ("index where VERIFICATION operations start in the spec operation table", "SPEC",
                                  "phase of every operation; validator rejects every row"),
    ("code/agent/state.py", 4): ("exposure rank DIRECT_IDENTIFIERS (most exposed wins across copies)", "JUDGEMENT", "which copy's exposure decides"),
    ("code/agent/state.py", 3): ("exposure rank STABLE_REFERENCE_ONLY", "JUDGEMENT", "which copy's exposure decides"),
    ("code/agent/state.py", 2): ("exposure rank REDACTED", "JUDGEMENT", "which copy's exposure decides"),
    ("code/agent/pipeline.py", 3): ("traceback lines kept in a degraded trace", "JUDGEMENT", "only diagnostic text"),
    ("code/agent/parsers/pdf_text.py", 2): ("regex group / '~>' strip in the PDF decoder", "STANDARD", "PDF text decoding"),
    ("code/agent/parsers/pdf_text.py", 3): ("regex group in the PDF decoder", "STANDARD", "PDF text decoding"),
    ("code/agent/explain.py", 25): ("items per list in --explain output", "JUDGEMENT", "only output length"),
    ("code/agent/explain.py", 40000): ("character cap of --explain output", "JUDGEMENT", "only output length"),
    ("code/agent/explain.py", 4): ("edges shown per attribution in --explain", "JUDGEMENT", "only output length"),
    ("code/agent/explain.py", 8): ("observations shown per resource in --explain", "JUDGEMENT", "only output length"),
    ("code/agent/explain.py", 64): ("max email local-part length", "BOUND", "RFC 5321 §4.5.3.1.1"),
    ("code/agent/explain.py", 255): ("max email domain length", "BOUND", "RFC 5321 §4.5.3.1.2"),
    ("code/agent/explain.py", 24): ("max TLD length accepted by the email shape check", "JUDGEMENT", "very long TLDs rejected"),
    ("code/main.py", 2): ("exit code: output validation failed / invalid explain input", "JUDGEMENT", "callers' exit-code handling (README table)"),
    ("code/main.py", 3): ("exit code: cache miss under --from-cache", "JUDGEMENT", "callers' exit-code handling (README table)"),
    ("code/main.py", 4): ("exit code: model failure / degraded run", "JUDGEMENT", "callers' exit-code handling (README table)"),
    ("code/analysis/e1_order_inference.py", 25): ("E1-B burst gap in minutes (first trough of the unlabelled gap histogram)",
                                                  "JUDGEMENT", "rejected experiment only; see main table"),
    ("code/analysis/e2_investigator.py", 8): ("fallback investigator tool-call cap (yaml max_tool_calls = 8)", "JUDGEMENT",
                                              "rejected experiment only"),
    ("code/analysis/e2_investigator.py", 2): ("strip of the 'c:' node prefix", "JUDGEMENT", "rejected experiment only"),
    ("code/analysis/e2_investigator_tools.py", 40): ("rows returned per investigator tool call", "JUDGEMENT", "rejected experiment only"),
    ("code/analysis/compare_intent.py", 2): ("a sentence used <= 2 times marks a free-text (non-template) request", "JUDGEMENT",
                                             "which texts the C4 comparison treats as free text"),
    ("code/analysis/profile_dataset.py", 2): ("forensics: minimum group size / histogram bucket / '~>' strip", "JUDGEMENT", "forensic printout only"),
    ("code/analysis/profile_dataset.py", 3): ("forensics: ±3 h window and minimum name-token length", "JUDGEMENT", "forensic printout only"),
    ("code/analysis/profile_dataset.py", 4): ("forensics: section number", "JUDGEMENT", "forensic printout only"),
    ("code/analysis/profile_dataset.py", 5): ("forensics: section number", "JUDGEMENT", "forensic printout only"),
    ("code/analysis/profile_dataset.py", 6): ("forensics: section number", "JUDGEMENT", "forensic printout only"),
    ("code/analysis/profile_dataset.py", 7): ("forensics: section number / candidate-count bucket 7+", "JUDGEMENT", "forensic printout only"),
    ("code/analysis/profile_dataset.py", 8): ("forensics: section number", "JUDGEMENT", "forensic printout only"),
    ("code/analysis/profile_dataset.py", 40): ("forensics: rows shown", "JUDGEMENT", "forensic printout only"),
    ("code/analysis/profile_dataset.py", 100): ("forensics: separator width", "JUDGEMENT", "forensic printout only"),
    ("code/analysis/probe_keys.py", 4): ("synthetic 4x4 probe image size", "JUDGEMENT", "key probe only"),
    ("code/analysis/probe_keys.py", 8): ("PNG bit depth of the probe image", "BOUND", "PNG specification"),
    ("code/analysis/probe_keys.py", 2): ("PNG colour type 2 (RGB) of the probe image", "BOUND", "PNG specification"),
    ("code/evaluation/main.py", 60): ("seconds per minute when printing band offsets", "STANDARD", "printed units"),
    ("code/evaluation/main.py", 2):("index of the 'correct' flag in an attribution tuple", "JUDGEMENT", "report arithmetic"),
    ("code/agent/parsers/file_checks.py", 8): ("PNG signature / chunk header length in bytes", "BOUND",
                                               "PNG specification (ISO/IEC 15948): chunk parsing breaks"),
    ("code/agent/parsers/file_checks.py", 4): ("PNG chunk CRC length in bytes", "BOUND", "PNG specification: CRC check breaks"),
    ("code/evaluation/security_audit.py", 2.0): ("time bound per regex per 1 MB input (seconds)", "JUDGEMENT",
                                                 "linear patterns take well under 1 s on 1 MB here; quadratic ones exceed 30 s"),
    ("code/evaluation/security_audit.py", 1000000): ("adversarial line length (1 MB)", "JUDGEMENT", "the G8 brief's 1 MB line"),
    ("code/evaluation/security_audit.py", 7): ("seed of the random-printable adversarial line", "JUDGEMENT", "which random line is tested"),
    ("code/evaluation/security_audit.py", 10): ("number of adversarial inputs / timeout slack / divisor of a 10-char repeat", "JUDGEMENT",
                                                "only the audit's timeout"),
    ("code/evaluation/security_audit.py", 3): ("repeat-unit length of 3-char adversarial lines / truncation to 3/5 / rounding", "JUDGEMENT",
                                               "only the audit inputs"),
    ("code/evaluation/security_audit.py", 6): ("repeat-unit length of 6-char adversarial lines", "JUDGEMENT", "only the audit inputs"),
    ("code/evaluation/security_audit.py", 5): ("keep 3/5 of a SQL file to truncate it", "JUDGEMENT", "only the fixture"),
    ("code/evaluation/security_audit.py", 2): ("half of a file / argv index / AST argument count", "JUDGEMENT", "only the audit"),
    ("code/evaluation/security_audit.py", 4): ("expected exit code of a degraded run", "SPEC", "C0 semantics: degraded → exit 4"),
    ("code/evaluation/security_audit.py", 64): ("bytes flipped to corrupt a PNG", "JUDGEMENT", "only the fixture"),
    ("code/evaluation/security_audit.py", 255): ("XOR mask used to flip bytes", "STANDARD", "only the fixture"),
    ("code/evaluation/cache_audit.py", 3): ("exit code of a cache miss", "SPEC", "README exit-code table: 3 = cache miss"),
    ("code/package.py", 2026): ("fixed zip entry timestamp (year) for a reproducible archive", "JUDGEMENT",
                                "only the archive bytes; any fixed date works"),
    ("code/package.py", 420): ("file mode 0o644 for zip entries", "STANDARD", "extracted file permissions"),
    ("code/package.py", 16): ("shift of the Unix mode into external_attr", "BOUND", "ZIP APPNOTE 4.4.15: modes unreadable"),
    ("code/agent/attribution.py", 10): ("A2: minimum CONFIRMED offsets per band group (BAND_MIN_N)", "JUDGEMENT",
                                        "fixed in the Round 5 brief before any accuracy number"),
    ("code/agent/attribution.py", 4): ("A2: band half-width multiplier on MAD (BAND_MAD_K); also a regex group index",
                                       "JUDGEMENT", "fixed in the Round 5 brief before any accuracy number"),
    ("code/agent/attribution.py", 120): ("A2: minimum band half-width in seconds (BAND_MIN_HALF_S)", "JUDGEMENT",
                                         "fixed in the Round 5 brief before any accuracy number"),
    ("code/agent/attribution.py", 0.1): ("A2: multimodality side share (MULTIMODAL_SIDE_SHARE)", "JUDGEMENT",
                                         "fixed before any accuracy number; see main table"),
    ("code/agent/attribution.py", 0.999): ("ceiling rounding of the multimodality side count", "STANDARD", "rounding only"),
    ("code/agent/attribution.py", 200000): ("A3: enumeration cap per batch (ENUMERATION_LIMIT)", "JUDGEMENT",
                                            "batches above it are reported as not enumerated"),
    ("code/agent/attribution.py", 2): ("median midpoint / band full width (2 x half) / filter stage index", "STANDARD",
                                       "median and band arithmetic"),
    ("code/evaluation/attribution_variants.py", 3): ("hours before reported_at in both falsification windows / tuple index",
                                                     "JUDGEMENT", "only the falsification report"),
    ("code/evaluation/attribution_variants.py", 1): ("+1 h window end of the brief's comparison window", "JUDGEMENT",
                                                     "only the falsification report"),
    ("code/evaluation/attribution_variants.py", 5): ("candidate-count histogram cap (5 = 5+)", "JUDGEMENT", "only the report"),
    ("code/evaluation/attribution_variants.py", 2): ("tuple index of resource_id in a step key", "STANDARD", "only the report"),
    ("code/evaluation/distribution_sanity.py", 2): ("decimal places when rounding means", "STANDARD", "report only"),
    ("code/evaluation/distribution_sanity.py", 3): ("decimal places when rounding shares", "STANDARD", "report only"),
    ("code/evaluation/rederivation_selection.py", 8): ("R2 quota: COMPLETE cases", "SPEC", "Round 6 brief"),
    ("code/evaluation/rederivation_selection.py", 12): ("R2 quota: INCOMPLETE cases (plus case_55 and case_63)", "SPEC",
                                                         "Round 6 brief"),
    ("code/evaluation/rederivation_selection.py", 2): ("R2 quota: NOT_APPLICABLE cases / the two fixed cases", "SPEC",
                                                        "Round 6 brief"),
    ("code/evaluation/rule_branch_audit.py", 2): ("raw evidence lines quoted per spot-check", "JUDGEMENT", "report only"),
    ("code/evaluation/rule_branch_audit.py", 3): ("spot-check instances listed per branch", "SPEC", "Round 6 brief: up to 3"),
    ("code/evaluation/spec_audit.py", 518): ("required data-row count", "SPEC", "problem_statement.md: exactly 518 data rows"),
    ("code/evaluation/spec_audit.py", 3): ("output columns per row; minimum steps of the case picked for the negative control",
                                           "SPEC", "problem_statement.md header has 3 columns"),
    ("code/evaluation/spec_audit.py", 2): ("column index of steps_json / minimum steps for the negative control", "SPEC",
                                           "the audit reads the wrong column"),
    ("code/evaluation/spec_audit.py", 7): ("deliberately wrong step_no injected by the negative control", "JUDGEMENT",
                                           "only the negative control"),
    ("code/evaluation/coupling_probes.py", 20261025): ("seed of the row-shuffle probe", "JUDGEMENT", "which permutation is tested"),
    ("code/evaluation/coupling_probes.py", 7000000): ("first number of the renamed id range (cannot collide with dataset ids)",
                                                      "JUDGEMENT", "collisions with original ids"),
    ("code/evaluation/coupling_probes.py", 2): ("regex group / argv offset in the probe harness", "STANDARD", "probe harness"),
    ("code/evaluation/coupling_probes.py", 10): ("examples listed in the probe report", "JUDGEMENT", "report length"),
    ("code/evaluation/coupling_probes.py", 30): ("changed cases listed in the probe report", "JUDGEMENT", "report length"),
    ("code/evaluation/coupling_probes.py", 60): ("characters of a rationale kept as a rule kind", "JUDGEMENT", "rule grouping"),
}


def literals(path: Path) -> list[tuple[int, object, str]]:
    src = path.read_text(encoding="utf-8")
    lines = src.splitlines()
    out = []
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Constant) and type(n.value) in (int, float) and n.value not in (0, 1, -1):
            out.append((n.lineno, n.value, lines[n.lineno - 1]))
    return out


def source_files() -> list[Path]:
    return sorted(p for p in (ROOT / "code").rglob("*.py") if "tests" not in p.parts and "cache" not in p.parts)


def classify(rel: str, value, line: str):
    if (rel, value) in MANUAL:
        return MANUAL[(rel, value)]
    if value in HTTP and re.search(r"\bstatus\b|\{4\d\d:", line):
        return ("HTTP status code", "BOUND", "RFC 9110 / provider API semantics: retry, quota and 404 handling")
    for frx, lrx, role, label, breaks in RULES:
        if re.search(frx, rel) and (not lrx or re.search(lrx, line)):
            return role, label, breaks
    return None


def fmt(v) -> str:
    return repr(v) if isinstance(v, float) else str(v)


def yaml_keys(obj, prefix="") -> list[tuple[str, object]]:
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out += yaml_keys(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(obj, list):
        for v in obj:
            out += yaml_keys(v, prefix + "[]")
    else:
        out.append((prefix, obj))
    return out


def main() -> int:
    groups: dict[tuple, set] = defaultdict(set)
    missing = []
    for p in source_files():
        rel = p.relative_to(ROOT).as_posix()
        for line_no, value, line in literals(p):
            c = classify(rel, value, line)
            if c is None:
                missing.append(f"{rel}:{line_no}: {value!r} | {line.strip()[:100]}")
                continue
            groups[(rel, *c)].add(value)
    if missing:
        print("UNCLASSIFIED literals (add a RULE or MANUAL entry):\n" + "\n".join(missing))
        return 1
    L = [BEGIN, "", "### Literal register (generated by `code/analysis/constants_register.py`)", "",
         "| File | Values | Role | Label | What breaks if changed |", "|---|---|---|---|---|"]
    for (rel, role, label, breaks), values in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        L.append(f"| `{rel}` | {', '.join(fmt(v) for v in sorted(values, key=float))} | {role} | {label} | {breaks} |")
    L += ["", "### Config keys (generated from `code/config/*.yaml`; list items collapsed to `[]`)", "",
          "| Key | Values |", "|---|---|"]
    for cfg in sorted((ROOT / "code" / "config").glob("*.yaml")):
        seen: dict[str, list] = defaultdict(list)
        for key, value in yaml_keys(yaml.safe_load(cfg.read_text(encoding="utf-8"))):
            if value not in seen[key]:
                seen[key].append(value)
        for key, values in seen.items():
            shown = ", ".join(str(v) for v in values[:4]) + (f" … ({len(values)} values)" if len(values) > 4 else "")
            L.append(f"| `{cfg.name}:{key}` | {shown} |")
    L += ["", END]
    text = CONSTANTS.read_text(encoding="utf-8")
    block = "\n".join(L)
    if BEGIN in text:
        text = text[:text.index(BEGIN)] + block + text[text.index(END) + len(END):]
    else:
        text = text.rstrip() + "\n\n" + block + "\n"
    CONSTANTS.write_text(text, encoding="utf-8", newline="\n")
    print(f"register: {len(groups)} rows; files: {len({g[0] for g in groups})}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
