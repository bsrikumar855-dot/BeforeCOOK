"""G3: CONSTANTS.md must cover every numeric literal in code/ (tests, 0/1/-1 excluded), every config key, and the
spec enums must equal the code's. Re-derived here independently of code/analysis/constants_register.py."""
import ast
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
TEXT = (ROOT / "CONSTANTS.md").read_text(encoding="utf-8")


def register() -> dict[str, set[float]]:
    out: dict[str, set[float]] = {}
    for m in re.finditer(r"^\| `(code/[^`]+\.py)` \| ([^|]+) \|", TEXT, re.M):
        out.setdefault(m.group(1), set()).update(float(v) for v in m.group(2).split(","))
    return out


def test_every_numeric_literal_is_registered():
    reg = register()
    missing = []
    for p in sorted((ROOT / "code").rglob("*.py")):
        if "tests" in p.parts or "cache" in p.parts:
            continue
        rel = p.relative_to(ROOT).as_posix()
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(n, ast.Constant) and type(n.value) in (int, float) and n.value not in (0, 1, -1):
                if float(n.value) not in reg.get(rel, set()):
                    missing.append(f"{rel}:{n.lineno} {n.value!r}")
    assert not missing, "literals missing from CONSTANTS.md (run code/analysis/constants_register.py):\n" + "\n".join(missing)


def _keys(obj, prefix=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _keys(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(obj, list):
        for v in obj:
            yield from _keys(v, prefix + "[]")
    else:
        yield prefix


def test_every_config_key_is_listed():
    for cfg in sorted((ROOT / "code" / "config").glob("*.yaml")):
        for key in set(_keys(yaml.safe_load(cfg.read_text(encoding="utf-8")))):
            assert f"`{cfg.name}:{key}`" in TEXT, f"{cfg.name}:{key} missing from CONSTANTS.md"


def _enum(name: str) -> list[str]:
    m = re.search(rf"^- {name}[^:]*: (.+)$", TEXT, re.M)
    return [v.strip() for v in m.group(1).split(",")]


def test_spec_enums_match_code():
    from agent import schema
    assert tuple(_enum("STATUSES")) == schema.STATUSES
    assert tuple(_enum("PHASES")) == schema.PHASES
    assert tuple(_enum("STEP_KEYS")) == schema.STEP_KEYS
    assert tuple(_enum("INTENT_LABELS")) == schema.INTENT_LABELS
    assert tuple(_enum("ATTRIBUTION_LABELS")) == schema.ATTRIBUTION_LABELS
    ops = []
    for phase in schema.PHASES:
        m = re.search(rf"^  - {phase}: (.+)$", TEXT, re.M)
        for item in m.group(1).split(","):
            op, state = item.strip().split("=")
            ops.append(op)
            assert schema.OPERATION_PHASE[op] == phase and schema.OPERATION_STATE[op] == state, op
    assert tuple(ops) == schema.OPERATION_ORDER


def test_no_unlabelled_main_table_row():
    allowed = {"MEASURED", "SPEC", "SPEC-DERIVED", "STANDARD", "BOUND", "JUDGEMENT"}
    main = TEXT.split("## Main table", 1)[1].split("## Spec enums", 1)[0]
    rows = [r for r in main.splitlines() if r.startswith("| ") and not r.startswith("| Constant") and "---" not in r]
    assert rows
    for r in rows:
        label = r.split("|")[5].strip()
        assert {w.strip(" ()") for w in re.split(r"[ /]+", label)} & allowed, r[:80]
    reg_labels = re.findall(r"^\| `code/[^|]+\|[^|]+\|[^|]+\| ([A-Z]+) \|", TEXT, re.M)
    assert reg_labels and set(reg_labels) <= allowed
