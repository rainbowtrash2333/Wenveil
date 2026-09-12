"""Optional, deterministic OCR-noise augmentation for NER samples."""

from __future__ import annotations

from dataclasses import dataclass, replace
import random
import unicodedata

from .samples import sample_from_spans
from .types import EntityAnnotation, TrainingSample


@dataclass(frozen=True, slots=True)
class OCRAugmentationConfig:
    """Conservative augmentation probabilities.

    Inserted whitespace is included inside an entity only when the insertion
    is strictly inside its original span, so annotations remain correct.
    Markdown delimiters, table pipes, and line boundaries are never modified.
    """

    space_probability: float = 0.12
    newline_probability: float = 0.025
    fullwidth_probability: float = 0.0
    max_insertions_per_span: int = 2
    within_entities_only: bool = False

    def validate(self) -> None:
        for name in ("space_probability", "newline_probability", "fullwidth_probability"):
            value = getattr(self, name)
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.max_insertions_per_span < 0:
            raise ValueError("max_insertions_per_span cannot be negative")


def _is_cjk(char: str) -> bool:
    if not char:
        return False
    name = unicodedata.name(char, "")
    return "CJK UNIFIED" in name or "CJK COMPATIBILITY" in name


def _safe_boundary(text: str, index: int) -> bool:
    if index <= 0 or index >= len(text):
        return False
    left, right = text[index - 1], text[index]
    if left in "\r\n|`#<>[]{}" or right in "\r\n|`#<>[]{}":
        return False
    return _is_cjk(left) and _is_cjk(right)


def augment_ocr(
    text: str,
    spans: tuple[EntityAnnotation, ...] | list[EntityAnnotation] = (),
    *,
    config: OCRAugmentationConfig | None = None,
    seed: int | None = None,
) -> tuple[str, tuple[EntityAnnotation, ...]]:
    """Return one augmented text and offset-adjusted spans.

    The original characters are never deleted or reordered.  This makes the
    transformation useful for model training while keeping the target spans
    auditable and reversible back to the augmented training example.
    """

    cfg = config or OCRAugmentationConfig()
    cfg.validate()
    annotations = tuple(EntityAnnotation.from_value(span, text) for span in spans)
    for span in annotations:
        span.validate_against(text)
    rng = random.Random(seed)
    interior_counts: dict[int, int] = {index: 0 for index in range(len(annotations))}
    insertion_at: dict[int, str] = {}

    def entity_at_boundary(index: int) -> int | None:
        for span_index, span in enumerate(annotations):
            if span.start < index < span.end:
                return span_index
        return None

    for index in range(1, len(text)):
        if not _safe_boundary(text, index):
            continue
        owner = entity_at_boundary(index)
        if cfg.within_entities_only and owner is None:
            continue
        if owner is not None and interior_counts[owner] >= cfg.max_insertions_per_span:
            continue
        if rng.random() >= cfg.space_probability:
            continue
        separator = "\n" if rng.random() < cfg.newline_probability else " "
        insertion_at[index] = separator
        if owner is not None:
            interior_counts[owner] += 1

    new_parts: list[str] = []
    starts: list[int] = [0] * len(text)
    ends: list[int] = [0] * len(text)
    for index, char in enumerate(text):
        if index in insertion_at:
            new_parts.append(insertion_at[index])
        starts[index] = sum(len(part) for part in new_parts)
        output_char = char
        if cfg.fullwidth_probability and rng.random() < cfg.fullwidth_probability:
            candidate = unicodedata.normalize("NFKC", char)
            if len(candidate) == 1 and candidate.isascii() and char.isascii() and char.isalnum():
                # Full-width ASCII is a useful OCR variant and remains one
                # code point, so offsets can still be mapped exactly.
                output_char = chr(ord(char) + 0xFEE0)
        new_parts.append(output_char)
        ends[index] = sum(len(part) for part in new_parts)
    augmented_text = "".join(new_parts)

    adjusted: list[EntityAnnotation] = []
    for span in annotations:
        start = starts[span.start]
        end = ends[span.end - 1]
        adjusted.append(
            EntityAnnotation(
                start,
                end,
                span.entity_type,
                augmented_text[start:end],
                span.score,
                span.priority,
                span.source,
                span.rule_id,
            )
        )
    return augmented_text, tuple(adjusted)


def augment_sample(
    sample: TrainingSample,
    *,
    config: OCRAugmentationConfig | None = None,
    seed: int | None = None,
    suffix: str = "#ocr-aug-0001",
) -> TrainingSample:
    sample.validate()
    text, spans = augment_ocr(sample.text, sample.spans, config=config, seed=seed)
    metadata = dict(sample.metadata)
    metadata.update(
        {
            "source_document_id": sample.metadata.get("source_document_id", sample.document_id),
            "augmentation": "ocr",
            "augmentation_seed": seed,
        }
    )
    return sample_from_spans(
        text,
        spans,
        document_id=f"{sample.document_id}{suffix}",
        metadata=metadata,
        scheme="BIO",
    )


def augment_dataset(
    samples: list[TrainingSample] | tuple[TrainingSample, ...],
    *,
    copies: int = 1,
    config: OCRAugmentationConfig | None = None,
    seed: int = 42,
    include_original: bool = True,
) -> list[TrainingSample]:
    """Add zero or more augmented copies while preserving document grouping.

    ``copies=0`` is a no-op (apart from returning a new list).  Each copy gets
    a distinct document id so augmented text cannot leak across a later
    document-level split.
    """

    if copies < 0:
        raise ValueError("copies cannot be negative")
    output = list(samples) if include_original else []
    for copy_index in range(copies):
        for sample_index, sample in enumerate(samples):
            local_seed = seed + copy_index * 1_000_003 + sample_index
            suffix = f"#ocr-aug-{copy_index + 1:04d}"
            output.append(augment_sample(sample, config=config, seed=local_seed, suffix=suffix))
    return output
