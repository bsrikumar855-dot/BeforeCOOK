"""G9: the Mermaid diagram in DESIGN.md and README.md matches the real import graph (every arrow is an import, every
shipped module appears, and the two copies are identical)."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CODE = ROOT / "code"
UTILITIES = {"agent.schema", "agent.loaders", "agent.identity", "agent.parsers.parsed_file"}


def import_graph() -> set[tuple[str, str]]:
    edges = set()
    for p in [CODE / "main.py"] + sorted((CODE / "agent").rglob("*.py")):
        parts = ["main"] if p == CODE / "main.py" else list(p.relative_to(CODE).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        pkg = parts if p.name == "__init__.py" else parts[:-1]
        mod = ".".join(parts)
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if not isinstance(n, ast.ImportFrom):
                continue
            base = pkg[:len(pkg) - n.level + 1] if n.level else []
            target = ".".join(base + ([n.module] if n.module else [])) if n.level else (n.module or "")
            if not target.startswith("agent"):
                continue
            for a in n.names:
                sub = f"{target}.{a.name}"
                edges.add((mod, sub if (CODE / Path(*sub.split("."))).with_suffix(".py").exists() else target))
    return edges


def diagram(text: str) -> tuple[str, set[tuple[str, str]]]:
    block = re.search(r"```mermaid\n(.*?)```", text, re.S).group(1)
    names = dict(re.findall(r'(\w+)\["([^"]+)"\]', block))
    edges = {(names[a], names[b]) for a, b in re.findall(r"(\w+)(?:\[\"[^\"]+\"\])? --> (\w+)", block)}
    return block, edges


def test_every_arrow_is_an_import_and_every_module_is_drawn():
    real = import_graph()
    block, drawn = diagram((ROOT / "DESIGN.md").read_text(encoding="utf-8"))
    assert drawn, "no edges parsed"
    assert drawn <= real, f"arrows that are not imports: {sorted(drawn - real)}"
    modules = {m for e in real for m in e} - UTILITIES
    shown = {m for e in drawn for m in e}
    assert modules <= shown, f"modules missing from the diagram: {sorted(modules - shown)}"
    readme_block, _ = diagram((ROOT / "README.md").read_text(encoding="utf-8"))
    assert readme_block == block
