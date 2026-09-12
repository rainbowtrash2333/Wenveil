"""Teacher/weak-label helpers based on deterministic rule spans."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .labels import parse_tagged_text
from .samples import sample_from_spans, samples_from_rule_results
from .types import TrainingSample


def label_with_spans(
    text: str,
    spans: Iterable[Any],
    *,
    document_id: str = "document-0",
    scheme: str = "BIO",
    metadata: Mapping[str, Any] | None = None,
) -> TrainingSample:
    """Create a lossless weakly supervised sample from recognizer spans."""

    return sample_from_spans(
        text,
        spans,
        document_id=document_id,
        scheme=scheme,
        metadata=metadata,
    )


def label_documents(
    documents: Iterable[Any],
    *,
    recognizer: Any | None = None,
    scheme: str = "BIO",
) -> list[TrainingSample]:
    return samples_from_rule_results(documents, recognizer=recognizer, scheme=scheme)


def label_tagged_text(
    tagged_text: str,
    *,
    original_text: str | None = None,
    document_id: str = "document-0",
    scheme: str = "BIO",
) -> TrainingSample:
    """Convert lossless ``<TYPE>text</TYPE>`` teacher output to a sample."""

    parsed = parse_tagged_text(tagged_text, original_text=original_text)
    return sample_from_spans(
        parsed.text,
        parsed.spans,
        document_id=document_id,
        scheme=scheme,
    )
