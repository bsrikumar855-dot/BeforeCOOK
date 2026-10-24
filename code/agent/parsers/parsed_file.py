"""Container returned by every evidence parser."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..schema import Observation


@dataclass
class PendingSnapshot:
    """A full table/collection dump. system_id may be resolved later from the rows' resource prefixes."""

    system_id: str | None
    container: str
    resource_ids: list[str]
    captured_at: str


@dataclass
class ParsedFile:
    text: str = ""                                            # searchable text (mention index)
    observations: list[Observation] = field(default_factory=list)
    snapshots: list[PendingSnapshot] = field(default_factory=list)
