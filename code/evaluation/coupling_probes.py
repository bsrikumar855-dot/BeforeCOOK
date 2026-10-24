"""G1: dataset-coupling probes (skill: orchestrate-test-fix/references/dataset-coupling-auditor.md).

Each probe builds a perturbed copy of dataset/ (and of code/cache/) in a temp directory, runs the pipeline on it in a
child process with --from-cache, and compares the result with the unperturbed baseline run. dataset/ and code/cache/
are never modified.

  (a) id bijection: every resource/user/case/file/activity/audit id renamed (same prefix, new number) in every text
      source (CSV cells incl. free text, JSON, logs, SQL, txt) and evidence files renamed. Content that cannot be
      rewritten gets the same bijection where the pipeline reads it: cached screenshot replies and OCR text (in the
      cache copy, keyed by the unchanged image hash) and the PDF text layer (a read-time hook in the child process).
  (b) row order: every CSV shuffled (header kept) and every JSON array shuffled, seeded.
  (c) timestamp format: every CSV timestamp ending in Z rewritten as +00:00 (loaders.parse_ts accepts both).
  (d) email case: every email upper-cased in the request email column, the identity tables and all evidence.
  (e) drop one evidence file (the most-cited one): only cases whose trace references it may change.

Expected: (a)-(d) the output is identical after mapping (a) back; (e) changes stay inside the dependent cases.
Comparison canonicalises step order with the spec's ordering rule (typed here, not imported), because renaming ids
legitimately changes string order inside one (phase, operation, system) group.

Run:  python code/evaluation/coupling_probes.py            (writes code/evaluation/coupling_probes.json)
"""
from __future__ import annotations

import csv
import io
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATASET = REPO / "dataset"
CACHE = REPO / "code" / "cache"
TEXT_EXT = {".csv", ".json", ".log", ".sql", ".txt"}
TOKEN = re.compile(r"(?<![A-Za-z0-9_])([a-z]+(?:_[a-z]+)*)_(\d+)(?![A-Za-z0-9])")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
TS_Z = re.compile(r"\b(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?)Z\b")
# Spec ordering (problem_statement.md, Allowed Operations table order), typed independently of the code.
PHASES = ["FINDING", "REMEDIATION", "VERIFICATION"]
OPS = ["REPORT_REMAINING_RECORD", "REPORT_EXPOSED_PII", "REPORT_ACTIVE_DATA_FLOW", "REPORT_RESTORE_RISK",
       "DISABLE_DATA_FLOW", "BLOCK_RESTORE", "DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD",
       "VERIFY_RECORD_ABSENT", "VERIFY_RECORD_PRESENT", "VERIFY_RECORD_REDACTED", "VERIFY_RECORD_ANONYMIZED",
       "VERIFY_DATA_FLOW_DISABLED", "VERIFY_RESTORE_BLOCKED"]


# ----------------------------------------------------------------------------------- id bijection
def id_prefixes() -> set[str]:
    """Prefixes of the values in the dataset's id columns (the values, not a guessed pattern)."""
    cols = {"deletion_requests.csv": ["case_id"], "sample_deletion_requests.csv": ["case_id"],
            "sample_output.csv": ["case_id"], "user_identities.csv": ["user_id"],
            "user_audit_log.csv": ["audit_id", "user_id"], "system_activity.csv": ["activity_id", "resource_id"],
            "evidence_files.csv": ["file_id"]}
    out = set()
    for f, cs in cols.items():
        for r in csv.DictReader(open(DATASET / f, encoding="utf-8")):
            for c in cs:
                m = TOKEN.fullmatch(r[c])
                if m:
                    out.add(m.group(1))
    return out


def text_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in TEXT_EXT)


def build_bijection(prefixes: set[str]) -> dict[str, str]:
    """Every id token with a known prefix, in every text source, -> same prefix + a new 7-digit number.
    Numbers keep their numeric order within a prefix and cannot collide with any original id."""
    seen: dict[str, set[str]] = {}
    for p in text_files(DATASET):        # standalone tokens only, so file stems like export_20260129_... are not ids
        for pre, num in TOKEN.findall(p.read_text(encoding="utf-8", errors="replace")):
            if pre in prefixes:
                seen.setdefault(pre, set()).add(num)
    mapping = {}
    for pre, nums in seen.items():
        for rank, num in enumerate(sorted(nums, key=lambda n: (int(n), n))):
            mapping[f"{pre}_{num}"] = f"{pre}_{7000000 + rank}"
    return mapping


def id_pattern(mapping: dict[str, str]) -> re.Pattern:
    """Known prefixes only, longest first; an underscore may precede the id, so ids joined to another token
    (`user_11_account_5762` in a model reply) are renamed too."""
    prefixes = sorted({k.rsplit("_", 1)[0] for k in mapping}, key=lambda p: (-len(p), p))
    return re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(map(re.escape, prefixes)) + r")_\d+(?![A-Za-z0-9])")


_PATTERNS: dict[int, re.Pattern] = {}


def rename_ids(text: str, mapping: dict[str, str]) -> str:
    pat = _PATTERNS.setdefault(id(mapping), id_pattern(mapping))
    return pat.sub(lambda m: mapping.get(m.group(0), m.group(0)), text)


def rename_ocr(text: str, mapping: dict[str, str]) -> str:
    """OCR renders an id's underscores as space, period or nothing; rename those renderings too."""
    def sub(m: re.Match) -> str:
        canon = re.sub(r"[ .]", "_", m.group(1)) + "_" + m.group(2)
        return mapping.get(canon, m.group(0))
    return re.sub(r"(?<![A-Za-z0-9_])([a-z]+(?:[_ .][a-z]+)*)[_ .]?(\d+)(?![0-9])", sub, rename_ids(text, mapping))


# ----------------------------------------------------------------------------------- fixture builders
def copy_tree(dst: Path) -> tuple[Path, Path]:
    ds, cache = dst / "dataset", dst / "cache"
    shutil.copytree(DATASET, ds)
    shutil.copytree(CACHE, cache)
    return ds, cache


def transform_text_files(root: Path, fn) -> None:
    for p in text_files(root):
        raw = p.read_text(encoding="utf-8", errors="replace")
        new = fn(raw, p)
        if new != raw:
            p.write_text(new, encoding="utf-8", newline="")


def transform_cache(cache: Path, fn, ocr_fn=None) -> None:
    """Apply fn to cached screenshot replies and ocr_fn (default fn) to cached OCR text; intent replies hold no ids."""
    for p in sorted((cache / "vision").glob("*.json")):
        e = json.loads(p.read_text(encoding="utf-8"))
        e["response"] = json.loads(fn(json.dumps(e["response"], ensure_ascii=False)))
        p.write_text(json.dumps(e, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    for p in sorted((cache / "ocr").glob("*.json")):
        e = json.loads(p.read_text(encoding="utf-8"))
        e["text"] = (ocr_fn or fn)(e["text"])
        p.write_text(json.dumps(e, indent=1) + "\n", encoding="utf-8", newline="\n")


def rename_evidence_files(ds: Path) -> None:
    """Rename every evidence file to an opaque name (extension kept) and update the manifest."""
    rows = list(csv.DictReader(open(ds / "evidence_files.csv", encoding="utf-8")))
    for i, r in enumerate(rows):
        old = Path(r["file_path"])
        new = old.with_name(f"item_{i:03d}{old.suffix}")
        (ds / old).rename(ds / new)
        r["file_path"] = new.as_posix()
    write_csv(ds / "evidence_files.csv", rows)


def write_csv(path: Path, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def shuffle_json(obj, rng: random.Random):
    if isinstance(obj, list):
        items = [shuffle_json(x, rng) for x in obj]
        rng.shuffle(items)
        return items
    if isinstance(obj, dict):
        return {k: shuffle_json(v, rng) for k, v in obj.items()}
    return obj


# ----------------------------------------------------------------------------------- probes
def probe_a(dst: Path, mapping: dict[str, str]) -> dict:
    ds, cache = copy_tree(dst)
    transform_text_files(ds, lambda t, p: rename_ids(t, mapping))
    rename_evidence_files(ds)
    transform_cache(cache, lambda t: rename_ids(t, mapping), lambda t: rename_ocr(t, mapping))
    # Fixture validation: no original id left anywhere in the text sources; every renamed id is a new-range id.
    leftovers, renamed = Counter(), 0
    prefixes = {k.rsplit("_", 1)[0] for k in mapping}
    pat = id_pattern(mapping)
    inverse_new = set(mapping.values())
    for p in text_files(ds):
        for tok in pat.findall(p.read_text(encoding="utf-8", errors="replace")):
            if tok in inverse_new:
                renamed += 1
            elif tok in mapping:                      # an original id survived the rewrite
                leftovers[tok] += 1
    return {"ds": ds, "cache": cache, "pdf_map": mapping,
            "fixture": {"ids_in_bijection": len(mapping), "prefixes": sorted(prefixes),
                        "renamed_tokens": renamed, "unrenamed_tokens": sum(leftovers.values()),
                        "unrenamed_examples": sorted(leftovers)[:10],
                        "coverage": f"{renamed}/{renamed + sum(leftovers.values())}"}}


def probe_b(dst: Path) -> dict:
    ds, cache = copy_tree(dst)
    rng = random.Random(20261025)
    for p in sorted(ds.rglob("*.csv")):
        text = p.read_text(encoding="utf-8")
        rows = list(csv.reader(io.StringIO(text)))
        head, body = rows[0], rows[1:]
        rng.shuffle(body)
        with open(p, "w", encoding="utf-8", newline="") as fh:
            csv.writer(fh, lineterminator="\n").writerows([head] + body)
    n_json = 0
    for p in sorted(ds.rglob("*.json")):
        p.write_text(json.dumps(shuffle_json(json.loads(p.read_text(encoding="utf-8")), rng), indent=2),
                     encoding="utf-8", newline="\n")
        n_json += 1
    return {"ds": ds, "cache": cache, "fixture": {"csv_files": len(list(ds.rglob('*.csv'))), "json_files": n_json}}


def probe_c(dst: Path) -> dict:
    ds, cache = copy_tree(dst)
    n = 0
    for p in sorted(ds.glob("*.csv")):
        text = p.read_text(encoding="utf-8")
        new, k = TS_Z.subn(r"\1+00:00", text)
        n += k
        p.write_text(new, encoding="utf-8", newline="")
    return {"ds": ds, "cache": cache, "fixture": {"timestamps_rewritten": n, "scope": "top-level CSV files only"}}


def probe_d(dst: Path) -> dict:
    ds, cache = copy_tree(dst)
    upper = lambda t: EMAIL.sub(lambda m: m.group(0).upper(), t)
    n = Counter()

    def fn(text: str, p: Path) -> str:
        if p.name in ("deletion_requests.csv", "sample_deletion_requests.csv"):
            rows = list(csv.DictReader(io.StringIO(text)))
            for r in rows:
                r["email"] = r["email"].upper()       # request_text untouched: it is not identity data
                n["request_emails"] += 1
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=list(rows[0]), lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
            return buf.getvalue()
        n["files"] += 1
        return upper(text)

    transform_text_files(ds, fn)
    transform_cache(cache, upper)
    return {"ds": ds, "cache": cache, "pdf_upper": True, "fixture": dict(n)}


def probe_e(dst: Path, file_id: str) -> dict:
    ds, cache = copy_tree(dst)
    rows = list(csv.DictReader(open(ds / "evidence_files.csv", encoding="utf-8")))
    keep = [r for r in rows if r["file_id"] != file_id]
    (ds / next(r for r in rows if r["file_id"] == file_id)["file_path"]).unlink()
    write_csv(ds / "evidence_files.csv", keep)
    return {"ds": ds, "cache": cache, "fixture": {"dropped": file_id}}


# ----------------------------------------------------------------------------------- run + compare
def run_child(out: Path, ds: Path, cache: Path, pdf_map: dict | None = None, pdf_upper: bool = False) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    hook = out / "hook.json"
    hook.write_text(json.dumps({"map": pdf_map or {}, "upper": pdf_upper}), encoding="utf-8")
    env = dict(os.environ, ORCHESTRATE_CACHE_DIR=str(cache), PYTHONHASHSEED="1")
    for k in [k for k in env if k.endswith("_API_KEY") or "_API_KEY_" in k]:
        env.pop(k)
    cmd = [sys.executable, str(Path(__file__)), "--child", str(hook), "--dataset", str(ds),
           "--output", str(out / "output.csv"), "--sample-output", str(out / "samples.csv"),
           "--traces", str(out / "traces"), "--ledger", str(out / "ledger.json"), "--from-cache", "--allow-degraded"]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(REPO))
    path = out / "output.csv"
    if not path.exists():
        path = out / "output.csv.degraded.csv" if (out / "output.csv.degraded.csv").exists() else out / "output.degraded.csv"
    return {"returncode": proc.returncode, "stderr": proc.stderr.strip()[-400:], "output": path, "traces": out / "traces"}


def child_main(argv: list[str]) -> int:
    """Child process: install the PDF text hook, then run code/main.py's main()."""
    hook = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    sys.path.insert(0, str(REPO / "code"))
    import agent.parsers.pdf_text as pdf_text
    original = pdf_text.text_layer

    def hooked(raw: bytes) -> str:
        text = original(raw)
        if hook["map"]:
            text = rename_ids(text, hook["map"])
        if hook["upper"]:
            text = EMAIL.sub(lambda m: m.group(0).upper(), text)
        return text
    pdf_text.text_layer = hooked
    import main as entry
    return entry.main(argv[1:])


def read_output(path: Path) -> dict[str, tuple[str, list]]:
    out = {}
    if not path.exists():
        return out
    for r in csv.DictReader(open(path, encoding="utf-8")):
        out[r["case_id"]] = (r["deletion_status"], json.loads(r["steps_json"]))
    return out


def canonical(rows: dict[str, tuple[str, list]], mapping: dict[str, str] | None = None) -> dict[str, tuple]:
    m = mapping or {}
    canon = {}
    for cid, (status, steps) in rows.items():
        mapped = [{**s, "system_id": s["system_id"], "resource_id": m.get(s["resource_id"], s["resource_id"]),
                   "evidence_id": m.get(s["evidence_id"], s["evidence_id"])} for s in steps]
        mapped.sort(key=lambda s: (PHASES.index(s["phase"]), OPS.index(s["operation"]), s["system_id"], s["resource_id"]))
        canon[m.get(cid, cid)] = (status, tuple((s["phase"], s["system_id"], s["resource_id"], s["operation"], s["state"],
                                                 s["evidence_id"]) for s in mapped),
                                  [s["step_no"] for s in steps] == list(range(1, len(steps) + 1)))
    return canon


def diff(base: dict, got: dict) -> list[str]:
    return sorted(c for c in set(base) | set(got) if base.get(c) != got.get(c))


def dependent_cases(traces: Path, file_id: str) -> set[str]:
    pat = re.compile(r'"' + re.escape(file_id) + r'(#[^"]*)?"')
    return {p.stem for p in traces.glob("case_*.json") if pat.search(p.read_text(encoding="utf-8"))}


def media_and_rule_firings(traces: Path, changed_a: set[str]) -> dict:
    """Share of cases depending on content that cannot be rewritten (PNG/PDF), tested separately in probe (a); and
    every decision rule (note, category basis, attribution rationale kind) that fires on exactly one case."""
    manifest = {r["file_id"]: r["file_path"] for r in csv.DictReader(open(DATASET / "evidence_files.csv", encoding="utf-8"))}
    media = {f for f, path in manifest.items() if path.lower().endswith((".png", ".pdf"))}
    dep, firings = set(), Counter()
    for t in sorted(traces.glob("case_*.json")):
        text = t.read_text(encoding="utf-8")
        if any(re.search(r'"' + re.escape(f) + r'(#[^"]*)?"', text) for f in media):
            dep.add(t.stem)
        tr = json.loads(text)
        kinds = set()
        for r in tr.get("resources", []):
            kinds.add("note:" + (r.get("note") or "").split(":")[0])
            kinds.add("category_basis:" + re.sub(r"(?<![A-Za-z0-9])(evidence|account|search)_\d+", "<id>", r.get("category_basis", "")))
        for a in tr.get("attribution", []):
            kinds.add("attribution:" + a["label"] + ":" + re.sub(r"[^A-Z_, ]", "", a.get("rationale", "")).strip()[:60])
        kinds.add("intent:" + str((tr.get("intent") or {}).get("path")))
        firings.update(k for k in kinds if not k.endswith(":"))
    return {"cases_depending_on_png_or_pdf": len(dep), "of_cases": len(list(traces.glob("case_*.json"))),
            "png_pdf_cases_changed_under_a": sorted(dep & changed_a),
            "single_case_rules": sorted(k for k, v in firings.items() if v == 1)}


def literal_audit() -> dict:
    """Corpus literals in executable code (tests, fixtures, analysis and evaluation scripts excluded)."""
    pat = re.compile(r"\b(case_\d+|evidence_\d+|user_\d+|(?:account|search|analytics|support|export|backup|data_flow|"
                     r"audit_record|tombstone|marketing|object|legal_hold)_\d+)\b|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.(?:com|net|org|test)")
    hits = []
    for p in sorted((REPO / "code" / "agent").rglob("*.py")) + [REPO / "code" / "main.py"] + \
            sorted((REPO / "code" / "prompts").glob("*.md")):
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            for m in pat.finditer(line):
                code = line.split("#", 1)[0] if p.suffix == ".py" else line
                in_code = m.group(0) in code and not code.lstrip().startswith(('"""', "'"))
                hits.append({"file": p.relative_to(REPO).as_posix(), "line": i, "literal": m.group(0),
                             "kind": "code" if in_code and p.suffix == ".py" else "comment/docstring/prompt"})
    return {"hits": hits}


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        return child_main(sys.argv[2:])
    results: dict = {}
    keep = Path(sys.argv[sys.argv.index("--keep") + 1]) if "--keep" in sys.argv else None   # debugging: keep outputs
    with tempfile.TemporaryDirectory() as tmp:
        tmp = keep or Path(tmp)
        base = run_child(tmp / "base_out", DATASET, CACHE)
        base_rows = canonical(read_output(base["output"]))
        base_samples = canonical(read_output(tmp / "base_out" / "samples.csv"))
        results["baseline"] = {"returncode": base["returncode"], "rows": len(base_rows)}
        keep_traces = REPO / "code" / "evaluation" / ".probe_base_traces"
        shutil.rmtree(keep_traces, ignore_errors=True)
        shutil.copytree(base["traces"], keep_traces)
        results["_base_traces"] = str(keep_traces)
        mapping = build_bijection(id_prefixes())
        inverse = {v: k for k, v in mapping.items()}
        cites = Counter()
        for _, steps in read_output(base["output"]).values():
            cites.update({s["evidence_id"] for s in steps if s["evidence_id"].startswith("evidence_")})
        drop = sorted(cites.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        for name, build in [("a_id_bijection", lambda d: probe_a(d, mapping)), ("b_row_order", probe_b),
                            ("c_timestamp_format", probe_c), ("d_email_case", probe_d),
                            ("e_drop_evidence_file", lambda d: probe_e(d, drop))]:
            fx = build(tmp / name)
            run = run_child(tmp / f"{name}_out", fx["ds"], fx["cache"], fx.get("pdf_map"), fx.get("pdf_upper", False))
            inv = inverse if name.startswith("a_") else None
            got = canonical(read_output(run["output"]), inv)
            got_s = canonical(read_output(tmp / f"{name}_out" / "samples.csv"), inv)
            changed, changed_s = diff(base_rows, got), diff(base_samples, got_s)
            r = {"returncode": run["returncode"], "fixture": fx["fixture"], "eval_rows_changed": len(changed),
                 "changed_cases": changed[:30], "sample_rows_changed": len(changed_s), "changed_samples": changed_s[:30],
                 "stderr": run["stderr"] if run["returncode"] not in (0,) else ""}
            if name.startswith("e_"):
                dep = dependent_cases(base["traces"], drop)
                outside = sorted((set(changed) | set(changed_s)) - dep)
                r.update({"dependent_cases": len(dep), "changed_outside_dependents": outside,
                          "pass": not outside})
            else:
                r["pass"] = run["returncode"] == 0 and not changed and not changed_s
            results[name] = r
            print(f"{name}: {'PASS' if r['pass'] else 'FAIL'} exit={run['returncode']} eval_changed={len(changed)} "
                  f"samples_changed={len(changed_s)} fixture={fx['fixture']}")
            if keep is None:
                shutil.rmtree(tmp / name, ignore_errors=True)
    results["literals"] = literal_audit()
    results["media_and_rules"] = media_and_rule_firings(Path(results.pop("_base_traces")),
                                                        set(results["a_id_bijection"]["changed_cases"]))
    print("media/rules:", json.dumps(results["media_and_rules"])[:900])
    code_hits = [h for h in results["literals"]["hits"] if h["kind"] == "code"]
    print(f"corpus literals in executable code: {len(code_hits)} {code_hits[:10]}")
    (REPO / "code" / "evaluation" / "coupling_probes.json").write_text(json.dumps(results, indent=1, default=str) + "\n",
                                                                        encoding="utf-8")
    return 0 if all(v.get("pass", True) for k, v in results.items() if k[:2] in ("a_", "b_", "c_", "d_", "e_")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
