from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Span:
    """An entity candidate or an accepted entity interval.

    Spans always refer to the immutable normalized text.  Recognizers never
    edit the input, which keeps offsets stable until the final one-pass
    replacement.
    """

    start: int
    end: int
    entity_type: str
    surface: str
    score: float = 1.0
    priority: int = 0
    source: str = ""
    rule_id: str = ""
    anonymize: bool = True
    protect: bool = False
    canonical_id: str = ""
    canonical_name: str = ""
    relation: str = ""
    alias_index: int = 0
    location: str = ""

    @property
    def length(self) -> int:
        return self.end - self.start

    def overlaps(self, other: "Span") -> bool:
        return self.start < other.end and other.start < self.end

    def validate_against(self, text: str) -> None:
        if self.start < 0 or self.end < self.start or self.end > len(text):
            raise ValueError(f"invalid span bounds: {self.start}:{self.end}")
        if text[self.start : self.end] != self.surface:
            raise ValueError("span surface does not match the source text")


def safe_entity_type(entity_type: str) -> str:
    """Return the token-safe representation of a configured entity type."""

    import re

    value = re.sub(r"[^A-Z0-9_]", "_", entity_type.upper()).strip("_")
    if not value:
        value = "ENTITY"
    if value[0].isdigit():
        value = f"E_{value}"
    return value
