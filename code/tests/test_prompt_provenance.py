"""Round 6 R5a: every cached model reply was produced by a prompt that ships in code/prompts/."""
import json
from pathlib import Path

from agent import pipeline
from agent.llm_client import CACHE_DIR, LLMClient
from agent.loaders import DEFAULT_DATASET_DIR
from agent.parsers.png_vlm import LEGACY_PROMPTS, ImageExtractor
from agent.rules import DATA_FLOW, Policy

ROOT = Path(__file__).resolve().parents[2]


def test_every_cache_entry_references_a_shipped_prompt():
    rr = pipeline.run(DEFAULT_DATASET_DIR, from_cache=True)
    client = LLMClient(from_cache=True)
    text_versions = {client.prompt(t)[1] for t in ("intent", "investigator")}
    categories = sorted(set(Policy(rr.dataset.rules).action) - {DATA_FLOW}) + [DATA_FLOW]
    extractor = ImageExtractor(client, None, None, categories)
    vision_versions = extractor.shipped_versions(rr.index.prefix_system)
    assert len(vision_versions) == 1 + len(LEGACY_PROMPTS)
    for name in LEGACY_PROMPTS:
        assert (ROOT / "code" / "prompts" / name).is_file()
    stray = []
    for p in sorted(CACHE_DIR.rglob("*.json")):
        if p.parent.name == "ocr":
            continue
        v = json.loads(p.read_text(encoding="utf-8")).get("prompt_version")
        allowed = vision_versions if p.parent.name == "vision" else text_versions
        if v not in allowed:
            stray.append(f"{p.relative_to(CACHE_DIR)}: {v}")
    assert not stray, stray
