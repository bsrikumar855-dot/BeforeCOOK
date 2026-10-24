"""G8: security / robustness audit (skills: orchestrate-submit/references/security-auditor.md, robustness.md).

Part 1, regex battery. Every regular expression in the product code (code/main.py, code/agent/**) is found by AST
(literal patterns passed to re.*, and module-level compiled patterns), plus the patterns built at run time
(ocr_grounding.token_pattern). Each runs, in its own child process with a hard timeout, against a battery of 1 MB
adversarial lines: uniform runs of the pattern's character classes, separator-heavy runs, and the pattern's own
literal prefix repeated with filler (the shape that makes anchored-but-unbounded patterns quadratic). A pattern
passes if every input finishes within BOUND_SECONDS.

Part 2, hostile evidence fixtures. Temporary dataset copies with one defect class each, run end to end with
--from-cache --allow-degraded: path traversal (relative and absolute), wrong extension both ways, truncated SQL,
corrupt PNG, missing file. Asserted for each: the file is rejected with the right status; exit code 4; every case
still has a row; output.csv is untouched; and FAIL CLOSED: no COMPLETE row cites a proof captured before a rejected
file that was captured at or before that case's audit_at.

  python code/evaluation/security_audit.py      (writes code/evaluation/security_audit.json; exit 1 on any failure)
"""
from __future__ import annotations

import ast
import csv
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CODE = REPO / "code"
BOUND_SECONDS = 2.0
LINE_BYTES = 1_000_000
RE_FUNCS = {"compile", "search", "match", "fullmatch", "finditer", "findall", "sub", "subn", "split"}


# ----------------------------------------------------------------------------------- part 1: regexes
def product_files() -> list[Path]:
    return [CODE / "main.py"] + sorted((CODE / "agent").rglob("*.py"))


def collect_patterns() -> list[dict]:
    found = []
    for p in product_files():
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in RE_FUNCS \
                    and isinstance(n.func.value, ast.Name) and n.func.value.id == "re" and n.args:
                a = n.args[0]
                flags = 0
                for kw in n.keywords:
                    if kw.arg == "flags":
                        flags = eval(compile(ast.Expression(kw.value), "f", "eval"), {"re": re})
                if len(n.args) > (2 if n.func.attr in ("compile",) else 3) - 1 and n.func.attr == "compile":
                    flags = eval(compile(ast.Expression(n.args[1]), "f", "eval"), {"re": re})
                if isinstance(a, ast.Constant) and isinstance(a.value, (str, bytes)):
                    found.append({"where": f"{p.relative_to(REPO).as_posix()}:{n.lineno}", "pattern": a.value,
                                  "flags": flags})
                elif isinstance(a, ast.BinOp) or isinstance(a, ast.JoinedStr):
                    found.append({"where": f"{p.relative_to(REPO).as_posix()}:{n.lineno}", "pattern": None,
                                  "dynamic": ast.unparse(a)[:80]})
    uniq, seen = [], set()
    for f in found:
        key = (f["pattern"], f.get("flags"), f.get("dynamic"))
        if key not in seen:
            seen.add(key)
            uniq.append(f)
    # Patterns built at run time: exercise the real builders with a representative token.
    uniq.append({"where": "code/agent/parsers/ocr_grounding.py token_pattern('audit_record_002')", "builder": "token_pattern"})
    return uniq


def battery(pattern: str | bytes) -> dict[str, str | bytes]:
    """1 MB adversarial lines for one pattern."""
    is_bytes = isinstance(pattern, bytes)
    text = pattern.decode("latin-1") if is_bytes else pattern
    literal = re.match(r"(?:\\.|[^\\\[\](){}.*+?^$|])+", text.lstrip("^(?:<!=)"))
    prefix = re.sub(r"\\(.)", r"\1", literal.group(0)) if literal else "a"
    rng = random.Random(7)
    lines = {
        "letters": "a" * LINE_BYTES,
        "letters_spaces": "ab " * (LINE_BYTES // 3),
        "words_underscores": "ab_cd_" * (LINE_BYTES // 6),
        "digits": "1" * LINE_BYTES,
        "id_like": "account_1 " * (LINE_BYTES // 10),
        "separators": "_ ." * (LINE_BYTES // 3),
        "parens_backslashes": "(\\)" * (LINE_BYTES // 3),
        "email_like": "a.b@c." * (LINE_BYTES // 6),
        "random_printable": "".join(rng.choice("abcz_ .:=-@()[]{}\"'/0189") for _ in range(LINE_BYTES)),
        "prefix_repeated": (prefix + " a ") * max(1, LINE_BYTES // (len(prefix) + 3)),
    }
    return {k: (v.encode("latin-1", errors="replace") if is_bytes else v) for k, v in lines.items()}


def _child(spec_json: str) -> None:
    spec = json.loads(spec_json)
    sys.path.insert(0, str(CODE))
    if spec.get("builder") == "token_pattern":
        from agent.parsers.ocr_grounding import token_pattern
        rx = token_pattern("audit_record_002")
        pattern = rx.pattern
    else:
        pattern = spec["pattern"].encode("latin-1") if spec.get("is_bytes") else spec["pattern"]
        rx = re.compile(pattern, spec.get("flags", 0))
    out = {}
    for name, line in battery(pattern).items():
        t = time.perf_counter()
        list(rx.finditer(line))
        rx.sub("" if isinstance(line, str) else b"", line)
        out[name] = round(time.perf_counter() - t, 3)
    print(json.dumps(out))


def run_regex_battery() -> list[dict]:
    results = []
    for spec in collect_patterns():
        if spec.get("pattern") is None and not spec.get("builder"):
            results.append({**spec, "result": "DYNAMIC: covered by its builder or not applicable", "max_seconds": None})
            continue
        payload = dict(spec)
        if isinstance(spec.get("pattern"), bytes):
            payload["pattern"], payload["is_bytes"] = spec["pattern"].decode("latin-1"), True
        n_inputs = 10
        try:
            proc = subprocess.run([sys.executable, str(Path(__file__)), "--child", json.dumps(payload)],
                                  capture_output=True, text=True, timeout=BOUND_SECONDS * n_inputs + 10)
            times = json.loads(proc.stdout.strip().splitlines()[-1]) if proc.returncode == 0 else {}
            worst = max(times.values()) if times else None
            ok = proc.returncode == 0 and worst is not None and worst <= BOUND_SECONDS
            res = "PASS" if ok else (f"FAIL: {proc.stderr.strip()[-200:]}" if proc.returncode else "FAIL: over bound")
        except subprocess.TimeoutExpired:
            times, worst, res = {}, None, f"FAIL: timeout > {BOUND_SECONDS * n_inputs + 10:.0f}s"
        shown = spec.get("pattern")
        shown = shown.decode("latin-1") if isinstance(shown, bytes) else shown
        results.append({"where": spec["where"], "pattern": (shown or spec.get("builder", ""))[:90],
                        "max_seconds": worst, "slowest_input": max(times, key=times.get) if times else None,
                        "result": res})
        print(f"{res[:4]} {worst}s {spec['where']}")
    return results


# ----------------------------------------------------------------------------------- part 2: fixtures
def _copy_dataset(dst: Path) -> Path:
    ds = dst / "dataset"
    shutil.copytree(REPO / "dataset", ds)
    return ds


def _manifest(ds: Path) -> list[dict]:
    return list(csv.DictReader(open(ds / "evidence_files.csv", encoding="utf-8")))


def _write_manifest(ds: Path, rows: list[dict]) -> None:
    with open(ds / "evidence_files.csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def _first(rows: list[dict], suffix: str) -> dict:
    return next(r for r in rows if r["file_path"].endswith(suffix))


def fixtures(tmp: Path) -> dict[str, tuple[Path, dict[str, str]]]:
    """name -> (dataset dir, {file_id: expected status})."""
    out = {}

    ds = _copy_dataset(tmp / "path_traversal")
    rows = _manifest(ds)
    a, b = rows[0], rows[1]
    a["file_path"], b["file_path"] = "../../etc/passwd", ("C:/Windows/win.ini" if os.name == "nt" else "/etc/passwd")
    _write_manifest(ds, rows)
    out["path_traversal"] = (ds, {a["file_id"]: "REJECTED_PATH", b["file_id"]: "REJECTED_PATH"})

    ds = _copy_dataset(tmp / "wrong_extension")
    rows = _manifest(ds)
    png, js = _first(rows, ".png"), _first(rows, ".json")
    for r, ext in ((png, ".json"), (js, ".png")):
        new = str(Path(r["file_path"]).with_suffix(ext).as_posix()).replace("/", "/renamed_", 1)
        (ds / new).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ds / r["file_path"], ds / new)
        r["file_path"] = new
    _write_manifest(ds, rows)
    out["wrong_extension"] = (ds, {png["file_id"]: "WRONG_TYPE", js["file_id"]: "WRONG_TYPE"})

    ds = _copy_dataset(tmp / "truncated_sql")
    rows = _manifest(ds)
    sql = _first(rows, ".sql")
    text = (ds / sql["file_path"]).read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    (ds / sql["file_path"]).write_text("".join(lines[: len(lines) * 3 // 5]), encoding="utf-8", newline="")
    out["truncated_sql"] = (ds, {sql["file_id"]: "TRUNCATED"})

    ds = _copy_dataset(tmp / "corrupt_png")
    rows = _manifest(ds)
    png = _first(rows, ".png")
    raw = bytearray((ds / png["file_path"]).read_bytes())
    mid = len(raw) // 2
    raw[mid:mid + 64] = bytes((x ^ 0xFF) for x in raw[mid:mid + 64])
    (ds / png["file_path"]).write_bytes(bytes(raw))
    out["corrupt_png"] = (ds, {png["file_id"]: "CORRUPT"})

    ds = _copy_dataset(tmp / "missing_file")
    rows = _manifest(ds)
    gone = sorted(rows, key=lambda r: r["captured_at"])[len(rows) // 2]       # a mid-timeline file
    (ds / gone["file_path"]).unlink()
    out["missing_file"] = (ds, {gone["file_id"]: "MISSING"})
    return out


def run_fixture(name: str, ds: Path, expected: dict[str, str], tmp: Path) -> dict:
    out = tmp / f"{name}_out"
    out.mkdir()
    before = hashlib.sha256((REPO / "output.csv").read_bytes()).hexdigest()
    env = {k: v for k, v in os.environ.items() if "API_KEY" not in k}
    proc = subprocess.run([sys.executable, str(CODE / "main.py"), "--dataset", str(ds), "--output", str(out / "output.csv"),
                           "--sample-output", str(out / "samples.csv"), "--traces", str(out / "traces"),
                           "--ledger", str(out / "ledger.json"), "--from-cache", "--allow-degraded"],
                          capture_output=True, text=True, env=env, cwd=str(REPO))
    after = hashlib.sha256((REPO / "output.csv").read_bytes()).hexdigest()
    degraded_csv = out / "output.degraded.csv"
    rows = list(csv.DictReader(open(degraded_csv, encoding="utf-8"))) if degraded_csv.exists() else []
    summary = json.loads((out / "traces" / "_run_summary.json").read_text(encoding="utf-8")) \
        if (out / "traces" / "_run_summary.json").exists() else {}
    rejected = {r["file_id"]: r["status"] for r in summary.get("evidence_rejected", [])}
    # FAIL CLOSED check
    manifest = {r["file_id"]: r["captured_at"] for r in _manifest(ds)}
    req = {r["case_id"]: r["audit_at"] for r in csv.DictReader(open(ds / "deletion_requests.csv", encoding="utf-8"))}
    ts = lambda v: v.replace("Z", "+00:00")
    violations, fail_closed = [], []
    for r in rows:
        cid = r["case_id"]
        dropped = [f for f in rejected if manifest.get(f) and ts(manifest[f]) <= ts(req[cid])]
        trace = out / "traces" / f"{cid}.json"
        if trace.exists() and json.loads(trace.read_text(encoding="utf-8")).get("reason_code") == "DEPENDS_ON_DROPPED_EVIDENCE":
            fail_closed.append(cid)
        if r["deletion_status"] != "COMPLETE":
            continue
        for s in json.loads(r["steps_json"]):
            ev = s["evidence_id"]
            if ev in manifest and any(ts(manifest[f]) > ts(manifest[ev]) for f in dropped):
                violations.append((cid, ev))
    checks = {
        "rejected_as_expected": all(rejected.get(f) == st for f, st in expected.items()),
        "exit_4": proc.returncode == 4,
        "every_case_has_a_row": len(rows) == len(req),
        "output_csv_untouched": before == after,
        "fail_closed_holds": not violations,
    }
    return {"expected": expected, "rejected": rejected, "exit": proc.returncode, "rows": len(rows),
            "cases_failed_closed": len(fail_closed), "fail_closed_examples": fail_closed[:5],
            "complete_rows_citing_pre_drop_proof": violations[:5], "checks": checks, "pass": all(checks.values()),
            "stderr": proc.stderr.strip()[-300:]}


def main() -> int:
    if len(sys.argv) > 2 and sys.argv[1] == "--child":
        _child(sys.argv[2])
        return 0
    results = {"bound_seconds": BOUND_SECONDS, "line_bytes": LINE_BYTES, "regex": run_regex_battery(), "fixtures": {}}
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for name, (ds, expected) in fixtures(tmp).items():
            r = run_fixture(name, ds, expected, tmp)
            results["fixtures"][name] = r
            print(f"{'PASS' if r['pass'] else 'FAIL'} fixture {name}: {r['checks']} rejected={r['rejected']} "
                  f"failed_closed={r['cases_failed_closed']}")
    (CODE / "evaluation" / "security_audit.json").write_text(json.dumps(results, indent=1) + "\n", encoding="utf-8")
    ok = all(r["result"].startswith(("PASS", "DYNAMIC")) for r in results["regex"]) and \
        all(f["pass"] for f in results["fixtures"].values())
    print(f"regex: {sum(r['result'] == 'PASS' for r in results['regex'])}/{len(results['regex'])} pass; "
          f"fixtures: {sum(f['pass'] for f in results['fixtures'].values())}/{len(results['fixtures'])} pass")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
