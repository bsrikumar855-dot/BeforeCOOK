"""G6/G7 step 4: clean-folder reproduction (skill: orchestrate-submit/references/release-engineer.md).

Unzips code.zip into an EMPTY temporary folder, copies in only dataset/, creates a fresh virtualenv with the pinned
requirements, and follows only the README: `python code/main.py --from-cache` with no API keys, then
`python code/evaluation/main.py --from-cache`. Passes when output.csv is byte-identical to the repository's and the
evaluation prints the same metrics line.

  python code/evaluation/clean_room.py [--zip code.zip]      (writes code/evaluation/clean_room.json)
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def pct(n: int, d: int) -> str:
    return f"{n}/{d} ({100.0 * n / d:.1f}%)" if d else f"{n}/0"


def metrics_line(r: dict) -> str:
    """The line code/evaluation/main.py prints, rebuilt from its committed results."""
    m, custom = r["samples"], r["custom"]
    return (f"status {pct(m['status_correct'], m['cases'])}; steps recall {pct(m['steps_matched'], m['steps_expected'])}; "
            f"precision {pct(m['steps_matched'], m['steps_predicted'])}; sequence {pct(m['sequence_exact'], m['cases'])}; "
            f"custom {sum(c['pass'] for c in custom)}/{len(custom)} pass")


def main() -> int:
    zpath = Path(sys.argv[sys.argv.index("--zip") + 1]) if "--zip" in sys.argv else REPO / "code.zip"
    expected_metrics = metrics_line(json.loads((REPO / "code" / "evaluation" / "evaluation_results.json")
                                               .read_text(encoding="utf-8")))     # read, never regenerated here
    result: dict = {"zip_sha256": sha(zpath), "repo_output_sha256": sha(REPO / "output.csv")}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "clean"
        root.mkdir()
        assert not any(root.iterdir())
        zipfile.ZipFile(zpath).extractall(root)
        shutil.copytree(REPO / "dataset", root / "dataset")
        venv = Path(tmp) / "venv"
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
        py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        assert (root / "requirements.txt").is_file(), "code.zip must ship requirements.txt"   # only the zip's copy
        pip = subprocess.run([str(py), "-m", "pip", "install", "-q", "-r", str(root / "requirements.txt")],
                             capture_output=True, text=True)
        result["pip_install_exit"] = pip.returncode
        env = {k: v for k, v in os.environ.items() if "API_KEY" not in k and k not in ("PYTHONPATH", "TESSERACT_CMD")}
        run = subprocess.run([str(py), "code/main.py", "--from-cache"], capture_output=True, text=True, cwd=str(root), env=env)
        out = root / "output.csv"
        result.update({"run_exit": run.returncode, "run_summary": run.stdout.strip().splitlines()[-1].split(" output=")[0] if run.stdout else "",
                       "clean_output_sha256": sha(out) if out.exists() else None})
        result["output_byte_identical"] = result["clean_output_sha256"] == result["repo_output_sha256"]
        ev = subprocess.run([str(py), "code/evaluation/main.py", "--from-cache"], capture_output=True, text=True,
                            cwd=str(root), env=env)
        got = ev.stdout.strip().splitlines()[-1] if ev.stdout.strip() else ev.stderr.strip()[-300:]
        result.update({"evaluation_exit": ev.returncode, "evaluation_metrics": got, "repo_metrics": expected_metrics,
                       "metrics_identical": got == expected_metrics})
    (REPO / "code" / "evaluation" / "clean_room.json").write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=1))
    ok = result["pip_install_exit"] == 0 and result["run_exit"] == 0 and result["output_byte_identical"] \
        and result["metrics_identical"]
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
