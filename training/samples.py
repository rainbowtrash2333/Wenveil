"""Training sample construction from rule/Span results."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from typing import Any

from .labels import labels_to_spans, spans_to_labels, validate_label_sequence
from .types import EntityAnnotation, TrainingSample


def _record_parts(record: Any, index: int) -> tuple[str, str, Iterable[Any], dict[str, Any]]:
    if isinstance(record, TrainingSample):
        return record.document_id, record.text, record.spans, dict(record.metadata)
    if isinstance(record, Mapping):
        text = record.get("text")
        spans = record.get("spans", record.get("entities", record.get("annotations", ())))
        if text is None:
            raise ValueError("span record must contain text")
        document_id = str(record.get("document_id", record.get("id", index)))
        return document_id, str(text), spans, dict(record.get("metadata", {}))
    if isinstance(record, (tuple, list)):
        if len(record) == 3:
            return str(record[0]), str(record[1]), record[2], {}
        if len(record) == 2:
            return str(index), str(record[0]), record[1], {}
    normalized_text = getattr(record, "normalized_text", None)
    if normalized_text is not None:
        spans = getattr(record, "accepted_spans", None)
        if spans is None:
            spans = getattr(record, "candidates", ())
        vault = getattr(record, "vault", None)
        job_id = getattr(vault, "job_id", None) if vault is not None else None
        return str(job_id or index), str(normalized_text), spans, {}
    raise TypeError("record must be a TrainingSample, mapping, or (document_id, text, spans) tuple")


def sample_from_spans(
    text: str,
    spans: Iterable[Any],
    *,
    document_id: str = "document-0",
    metadata: Mapping[str, Any] | None = None,
    scheme: str = "BIO",
) -> TrainingSample:
    """Build and validate one sample from core ``Span``/rule results."""

    annotations = tuple(EntityAnnotation.from_value(span, text) for span in spans)
    sample = TrainingSample(document_id, text, annotations, dict(metadata or {}))
    sample.validate()
    labels = spans_to_labels(sample.text, sample.spans, scheme=scheme)
    # This second validation catches malformed boundaries and guarantees the
    # annotation target is lossless before it reaches a trainer.
    result = validate_label_sequence(labels, scheme=scheme, text=text)
    if len(result.spans) != len(sample.spans):
        raise ValueError("generated labels changed the number of source spans")
    return sample


def sample_from_labels(
    text: str,
    labels: Sequence[str],
    *,
    document_id: str = "document-0",
    scheme: str = "BIO",
    metadata: Mapping[str, Any] | None = None,
) -> TrainingSample:
    """Build a sample from character/token labels after strict validation."""

    spans = labels_to_spans(text, labels, scheme=scheme)
    return TrainingSample(document_id, text, spans, dict(metadata or {}))


def samples_from_span_records(
    records: Iterable[Any],
    *,
    scheme: str = "BIO",
) -> list[TrainingSample]:
    result: list[TrainingSample] = []
    for index, record in enumerate(records):
        if isinstance(record, TrainingSample):
            sample = record
            sample.validate()
            # Force the same lossless label check used for new records.
            spans_to_labels(sample.text, sample.spans, scheme=scheme)
        else:
            document_id, text, spans, metadata = _record_parts(record, index)
            sample = sample_from_spans(
                text,
                spans,
                document_id=document_id,
                metadata=metadata,
                scheme=scheme,
            )
        result.append(sample)
    return result


def samples_from_rule_results(
    documents: Iterable[Any],
    *,
    recognizer: Any | None = None,
    scheme: str = "BIO",
) -> list[TrainingSample]:
    """Create samples from documents and optional callable rule recognizer.

    Each document may be a mapping with ``text``/``document_id`` and optional
    ``spans``.  When ``recognizer`` is supplied and a record has no spans, it
    is called with the text and its result is used as the teacher annotation.
    """

    prepared: list[Any] = []
    for index, document in enumerate(documents):
        if isinstance(document, Mapping):
            item = dict(document)
            has_spans = any(key in item for key in ("spans", "entities", "annotations"))
            if not has_spans and recognizer is not None:
                text = str(item.get("text", ""))
                item["spans"] = recognizer(text)
            item.setdefault("document_id", str(index))
            prepared.append(item)
        elif isinstance(document, (tuple, list)) and len(document) == 2 and recognizer is not None:
            document_id, text = document
            prepared.append((document_id, text, recognizer(str(text))))
        else:
            prepared.append(document)
    return samples_from_span_records(prepared, scheme=scheme)


def chunk_sample(
    sample: TrainingSample,
    *,
    max_characters: int,
    overlap: int = 0,
) -> list[TrainingSample]:
    """Split a document into windows without cutting annotated entities.

    Windows may exceed ``max_characters`` when a single entity is longer than
    the requested size.  Keeping that entity intact is safer than silently
    producing a wrong BIO target.
    """

    sample.validate()
    if max_characters <= 0:
        raise ValueError("max_characters must be positive")
    if overlap < 0 or overlap >= max_characters:
        raise ValueError("overlap must satisfy 0 <= overlap < max_characters")
    if len(sample.text) <= max_characters:
        return [sample]

    result: list[TrainingSample] = []
    base_start = 0
    chunk_index = 0
    text_length = len(sample.text)
    while base_start < text_length:
        base_end = min(text_length, base_start + max_characters)
        start, end = base_start, base_end
        changed = True
        while changed:
            changed = False
            for span in sample.sorted_spans():
                if span.start < start < span.end:
                    start = span.start
                    changed = True
                if span.start < end < span.end:
                    end = span.end
                    changed = True
        window_spans = [
            EntityAnnotation(
                span.start - start,
                span.end - start,
                span.entity_type,
                sample.text[span.start : span.end],
                span.score,
                span.priority,
                span.source,
                span.rule_id,
            )
            for span in sample.sorted_spans()
            if start <= span.start and span.end <= end
        ]
        metadata = dict(sample.metadata)
        metadata.update(
            {
                "source_document_id": sample.metadata.get("source_document_id", sample.document_id),
                "chunk_index": chunk_index,
                "chunk_start": start,
                "chunk_end": end,
            }
        )
        result.append(
            TrainingSample(
                f"{sample.document_id}#chunk-{chunk_index:04d}",
                sample.text[start:end],
                tuple(window_spans),
                metadata,
            )
        )
        chunk_index += 1
        if end >= text_length:
            break
        next_start = max(base_start + 1, base_end - overlap)
        if next_start <= base_start:
            raise RuntimeError("chunker failed to advance")
        base_start = next_start
    return result
