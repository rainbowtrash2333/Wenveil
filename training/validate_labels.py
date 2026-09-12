"""Compatibility-facing label validation API.

Keeping this small module makes the planned ``training/validate_labels.py``
entry point usable from scripts while the implementation lives in
``training.labels``.
"""

from .labels import (
    LabelValidationError,
    LabelValidationResult,
    TaggedTextResult,
    labels_to_spans,
    parse_tagged_text,
    reconstruct_text,
    spans_to_labels,
    strip_tags,
    validate_bilou_labels,
    validate_bio_labels,
    validate_label_sequence,
    validate_labels,
    validate_tagged_text,
)

__all__ = [
    "LabelValidationError",
    "LabelValidationResult",
    "TaggedTextResult",
    "labels_to_spans",
    "parse_tagged_text",
    "reconstruct_text",
    "spans_to_labels",
    "strip_tags",
    "validate_bilou_labels",
    "validate_bio_labels",
    "validate_label_sequence",
    "validate_labels",
    "validate_tagged_text",
]
