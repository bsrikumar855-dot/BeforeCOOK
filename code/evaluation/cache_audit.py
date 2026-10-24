"""G6/G7 step 2: cache completeness (skill: orchestrate-submit/references/release-engineer.md).

Every run below is keyless (all *_API_KEY* variables removed), uses --from-cache, and has `pytesseract` made
unimportable, so it proves that neither a model nor Tesseract is needed to reproduce the outputs:

  1. the full dataset run (518 eval + 32 samples);
  2. every custom, category and adversarial fixture (X1-X19);
  3. the coupling-probe fixtures (code/evaluation/coupling_probes.py), unless --skip-probes.

Then: cache misses (must be 0), the prompt version of every entry against the prompts in the package, orphan entries
(read by none of the runs above and not an experiment artifact), and the total cache size.

  python code/evaluation/cache_audit.py [--skip-probes]     (writes code/evaluation/cache_audit.json)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CODE = REPO / "code"
CACHE = CODE / "cache"
EXPERIMENT_DIRS = {"investigator", "compare_gemini-3.5-flash", "compare_gemini-3.5-flash-lite"}   # E2 and C4 replays


REPO_PATH = REPO

def redact(text: str, *dirs) -> str:
    """Captured subprocess output without local paths (temp dirs, the repo, the home directory)."""
    for d in sorted({str(x) for x in dirs} | {str(REPO_PATH), str(Path.home())}, key=len, reverse=True):
        text = text.replace(d, "<" + ("repo" if d == str(REPO_PATH) else "home" if d == str(Path.home()) else "tmp") + ">")
        text = text.replace(d.replace("\\", "\\\\"), "<path>")
    return text


def keyless_env(block_dir: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if "API_KEY" not in k and k != "TESSERACT_CMD"}
    env["PYTHONPATH"] = str(block_dir) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONHASHSEED"] = "1"
    return env


FIXTURE_RUNNER = r'''
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from agent.pipeline import run
from agent.llm_client import CacheMiss
out = {}
for d in sys.argv[2:]:
    try:
        rr = run(Path(d), from_cache=True, include_samples=False)
        out[Path(d).name] = {"ok": True, "hits": [[h["task"], h["key"]] for h in rr.ledger.cache_hits],
                             "calls": len(rr.ledger.calls)}
    except CacheMiss as exc:
        out[Path(d).name] = {"ok": False, "error": str(exc)[:200]}
print(json.dumps(out))
'''


def main() -> int:
    skip_probes = "--skip-probes" in sys.argv
    result: dict = {}
    used: set[tuple[str, str]] = set()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        block = tmp / "block"
        block.mkdir()
        (block / "pytesseract.py").write_text("raise ImportError('blocked by cache_audit: Tesseract must not be needed')\n")
        env = keyless_env(block)

        # 1. dataset run
        out = tmp / "run"
        proc = subprocess.run([sys.executable, str(CODE / "main.py"), "--from-cache", "--output", str(out / "output.csv"),
                               "--sample-output", str(out / "samples.csv"), "--traces", str(out / "traces"),
                               "--ledger", str(out / "ledger.json")], capture_output=True, text=True, env=env, cwd=str(REPO))
        ledger = json.loads((out / "ledger.json").read_text(encoding="utf-8")) if (out / "ledger.json").exists() else {}
        used |= {(h["task"], h["key"]) for h in ledger.get("cache_hits", [])}
        same = (out / "output.csv").exists() and (out / "output.csv").read_bytes() == (REPO / "output.csv").read_bytes()
        result["dataset_run"] = {"exit": proc.returncode, "live_calls": len(ledger.get("calls", [])),
                                 "cache_hits": len(ledger.get("cache_hits", [])), "output_identical_to_repo": same,
                                 "stderr": redact(proc.stderr.strip(), tmp)[-300:]}

        # 2. fixtures
        sys.path.insert(0, str(CODE / "evaluation" / "custom_cases"))
        from build_fixtures import CASES, build
        build()
        dirs = [str(CODE / "evaluation" / "custom_cases" / n) for n in CASES]
        script = tmp / "fixtures.py"
        script.write_text(FIXTURE_RUNNER, encoding="utf-8")
        proc = subprocess.run([sys.executable, str(script), str(CODE)] + dirs, capture_output=True, text=True, env=env,
                              cwd=str(REPO))
        fx = json.loads(proc.stdout.strip().splitlines()[-1]) if proc.returncode == 0 else {}
        for v in fx.values():
            used |= {tuple(h) for h in v.get("hits", [])}
        result["fixtures"] = {"exit": proc.returncode, "cases": len(fx), "ok": sum(v["ok"] for v in fx.values()),
                              "misses": {k: v["error"] for k, v in fx.items() if not v["ok"]},
                              "stderr": redact(proc.stderr.strip(), tmp)[-300:] if proc.returncode else ""}

    # 3. coupling probes (each child is keyless, --from-cache; renamed cache copies)
    if not skip_probes:
        block = Path(tempfile.mkdtemp())
        (block / "pytesseract.py").write_text("raise ImportError('blocked')\n")
        proc = subprocess.run([sys.executable, str(CODE / "evaluation" / "coupling_probes.py")], capture_output=True,
                              text=True, env=keyless_env(block), cwd=str(REPO))
        probes = json.loads((CODE / "evaluation" / "coupling_probes.json").read_text(encoding="utf-8"))
        result["coupling_probes"] = {"exit": proc.returncode,
                                     "child_exits": {k: v.get("returncode") for k, v in probes.items() if isinstance(v, dict)
                                                     and "returncode" in v}}

    # prompt versions, OCR, orphans, size
    sys.path.insert(0, str(CODE))
    from agent.llm_client import LLMClient
    client = LLMClient(from_cache=True)
    current = {t: client.prompt(t)[1] for t in ("intent", "investigator")}
    versions: dict = {}
    orphans, legacy = [], []
    for p in sorted(CACHE.rglob("*.json")):
        top, task = p.relative_to(CACHE).parts[0], p.parent.name
        e = json.loads(p.read_text(encoding="utf-8"))
        if task == "ocr":
            continue
        pv = e.get("prompt_version")
        versions.setdefault(top, {}).setdefault(pv, 0)
        versions[top][pv] += 1
        if task in current and pv != current[task]:
            legacy.append(p.relative_to(CACHE).as_posix())
        if top not in EXPERIMENT_DIRS and (task, p.stem) not in used:
            orphans.append({"entry": f"{task}/{p.name}", "file_id": e.get("file_id"), "prompt_version": pv})
    vision_used = {k for t, k in used if t == "vision"}
    vision_versions = {json.loads((CACHE / "vision" / f"{k}.json").read_text(encoding="utf-8"))["prompt_version"]
                       for k in vision_used}
    ocr = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((CACHE / "ocr").glob("*.json"))]
    size = sum(p.stat().st_size for p in CACHE.rglob("*") if p.is_file())
    result.update({
        "prompt_versions": {"current_text_prompts": current, "entries": versions,
                            "text_entries_not_matching_current_prompt": legacy,
                            "vision_versions_read_by_the_run": sorted(vision_versions)},
        "ocr": {"entries": len(ocr), "images": len({o["image_sha256"] for o in ocr}),
                "passes": sorted({o["pass"].split(":")[0] for o in ocr}), "engines": sorted({o["engine"] for o in ocr})},
        "orphans": orphans, "cache_files": sum(1 for p in CACHE.rglob("*.json")), "cache_bytes": size,
    })
    misses = (result["dataset_run"]["exit"] == 3) + len(result["fixtures"]["misses"]) + \
        sum(1 for v in result.get("coupling_probes", {}).get("child_exits", {}).values() if v == 3)
    result["cache_misses"] = misses
    (CODE / "evaluation" / "cache_audit.json").write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "prompt_versions"}, indent=1)[:2500])
    print("prompt versions:", json.dumps(result["prompt_versions"])[:800])
    ok = misses == 0 and result["dataset_run"]["exit"] == 0 and result["dataset_run"]["output_identical_to_repo"] \
        and result["fixtures"]["ok"] == result["fixtures"]["cases"]
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
