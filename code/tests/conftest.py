"""Unit tests cover pure code only. No model calls, no network, no keys.

An autouse fixture removes provider keys so no test can reach the network by accident.
"""
import sys
from pathlib import Path

import pytest

CODE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_DIR))


@pytest.fixture(autouse=True)
def _no_keys(monkeypatch):
    for name in ("GEMINI_API_KEY", "GEMINI_API_KEY_FALLBACK_1", "GEMINI_API_KEY_FALLBACK_2",
                 "GEMINI_API_KEY_FALLBACK_3"):
        monkeypatch.delenv(name, raising=False)
