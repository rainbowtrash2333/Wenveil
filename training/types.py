"""Shared lightweight types used by the offline training pipeline.

The production recognizers return ``desensitize.models.Span`` objects.  The
training package intentionally accepts those objects through a small duck
typed adapter instead of importing the desensitizer internals everywhere.
This also makes it possible to use JSON/dictionary rule results in data
preparation scripts.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


def _value(value: Any, *names: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
        return default
    for name in names:
        if hasattr(value, name):
            return getattr(value, name)
    return default


@dataclass(frozen=True, slots=True)
class EntityAnnotation:
    """An entity interval in the immutable source text.

    ``start`` and ``end`` use normal Python string offsets and ``end`` is
    exclusive.  ``surface`` is optional at construction time and is filled by
    :meth:`from_value` when source text is available.
    """

    start: int
    end: int
    entity_type: str
    surface: str = ""
    score: float = 1.0
    priority: int = 0
    source: str = ""
    rule_id: str = ""

    @classmethod
    def from_value(cls, value: Any, text: str | None = None) -> "EntityAnnotation":
        """Convert a core ``Span``, mapping, or compatible object.

        Accepted type keys are ``entity_type``, ``label`` and ``type``; the
        surface keys are ``surface`` and ``text``.  A three-item tuple of
        ``(start, end, entity_type)`` is also accepted for small scripts.
        """

        if isinstance(value, cls):
            annotation = value
        elif isinstance(value, (tuple, list)) and len(value) >= 3:
            annotation = cls(int(value[0]), int(value[1]), str(value[2]))
        else:
            start = _value(value, "start")
            end = _value(value, "end")
            entity_type = _value(value, "entity_type", "label", "type")
            if start is None or end is None or entity_type is None:
                raise TypeError("a span must provide start, end, and entity_type/label/type")
            surface = _value(value, "surface", "text", default="")
            annotation = cls(
                start=int(start),
                end=int(end),
                entity_type=str(entity_type),
                surface="" if surface is None else str(surface),
                score=float(_value(value, "score", "confidence", default=1.0)),
                priority=int(_value(value, "priority", default=0)),
                source=str(_value(value, "source", default="") or ""),
                rule_id=str(_value(value, "rule_id", default="") or ""),
            )

        if text is not None and not annotation.surface:
            if 0 <= annotation.start <= annotation.end <= len(text):
                annotation = cls(
                    start=annotation.start,
                    end=annotation.end,
                    entity_type=annotation.entity_type,
                    surface=text[annotation.start : annotation.end],
                    score=annotation.score,
                    priority=annotation.priority,
                    source=annotation.source,
                    rule_id=annotation.rule_id,
                )
        return annotation

    @property
    def length(self) -> int:
        return self.end - self.start

    def validate_against(self, text: str) -> None:
        if not isinstance(self.start, int) or not isinstance(self.end, int):
            raise ValueError("span offsets must be integers")
        if self.start < 0 or self.end <= self.start or self.end > len(text):
            raise ValueError(f"invalid span bounds: {self.start}:{self.end}")
        if not self.entity_type or any(char.isspace() for char in self.entity_type):
            raise ValueError("entity_type must be a non-empty whitespace-free string")
        expected = text[self.start : self.end]
        if self.surface and self.surface != expected:
            raise ValueError(
                f"span surface does not match source text at {self.start}:{self.end}"
            )

    def as_dict(self, text: str | None = None) -> dict[str, Any]:
        surface = self.surface
        if text is not None:
            surface = text[self.start : self.end]
        return {
            "start": self.start,
            "end": self.end,
            "label": self.entity_type,
            "surface": surface,
            "score": self.score,
            "priority": self.priority,
            "source": self.source,
            "rule_id": self.rule_id,
        }


@dataclass(slots=True)
class TrainingSample:
    """A document or document chunk with validated span annotations."""

    document_id: str
    text: str
    spans: tuple[EntityAnnotation, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.document_id, str) or not self.document_id:
            raise ValueError("document_id must be a non-empty string")
        if not isinstance(self.text, str):
            raise TypeError("text must be a string")
        self.spans = tuple(EntityAnnotation.from_value(span, self.text) for span in self.spans)

    def validate(self) -> None:
        previous_end = -1
        previous_start = -1
        for span in sorted(self.spans, key=lambda item: (item.start, item.end)):
            span.validate_against(self.text)
            if span.start < previous_end:
                raise ValueError(
                    f"overlapping spans at {previous_start}:{previous_end} and "
                    f"{span.start}:{span.end}"
                )
            previous_start, previous_end = span.start, span.end

    def sorted_spans(self) -> tuple[EntityAnnotation, ...]:
        return tuple(sorted(self.spans, key=lambda item: (item.start, item.end, item.entity_type)))

    def with_metadata(self, **values: Any) -> "TrainingSample":
        metadata = dict(self.metadata)
        metadata.update(values)
        return TrainingSample(self.document_id, self.text, self.spans, metadata)

    def to_dict(self, scheme: str = "BIO") -> dict[str, Any]:
        # Import lazily to keep this module usable during package bootstrap.
        from .labels import spans_to_labels

        self.validate()
        labels = spans_to_labels(self.text, self.spans, scheme=scheme)
        return {
            "document_id": self.document_id,
            "text": self.text,
            "spans": [span.as_dict(self.text) for span in self.sorted_spans()],
            "units": list(self.text),
            "labels": labels,
            "scheme": scheme.upper(),
            "metadata": dict(self.metadata),
        }
