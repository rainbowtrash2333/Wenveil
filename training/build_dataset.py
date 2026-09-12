"""Offline dataset I/O and preparation helpers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import json
from pathlib import Path
from typing import Any

from .labels import labels_to_spans, spans_to_labels, validate_label_sequence
from .samples import samples_from_span_records
from .types import EntityAnnotation, TrainingSample


def build_samples_from_span_records(
    records: Iterable[Any],
    *,
    scheme: str = "BIO",
) -> list[TrainingSample]:
    return samples_from_span_records(records, scheme=scheme)


def build_samples_from_rule_results(
    documents: Iterable[Any],
    *,
    recognizer: Any | None = None,
    scheme: str = "BIO",
) -> list[TrainingSample]:
    from .samples import samples_from_rule_results

    return samples_from_rule_results(documents, recognizer=recognizer, scheme=scheme)


def _sample_from_record(record: Mapping[str, Any], index: int, default_scheme: str) -> TrainingSample:
    if "text" not in record:
        raise ValueError(f"JSONL record {index} has no text")
    text = str(record["text"])
    document_id = str(record.get("document_id", record.get("id", index)))
    scheme = str(record.get("scheme", default_scheme))
    metadata = dict(record.get("metadata", {}))

    if "spans" in record or "entities" in record or "annotations" in record:
        raw_spans = record.get("spans", record.get("entities", record.get("annotations", ())))
        annotations = tuple(EntityAnnotation.from_value(span, text) for span in raw_spans)
        sample = TrainingSample(document_id, text, annotations, metadata)
        sample.validate()
        generated = spans_to_labels(text, sample.spans, scheme=scheme)
        if "labels" in record:
            stored = [str(label) for label in record["labels"]]
            units = record.get("units")
            validate_label_sequence(
                stored,
                scheme=scheme,
                text=text,
                units=units,
                offsets=record.get("offsets"),
            )
            if stored != generated:
                raise ValueError(f"JSONL record {index} has labels inconsistent with spans")
        return sample

    if "labels" not in record:
        raise ValueError(f"JSONL record {index} needs spans or labels")
    labels = [str(label) for label in record["labels"]]
    units = record.get("units")
    offsets = record.get("offsets")
    result = validate_label_sequence(
        labels,
        scheme=scheme,
        text=text,
        units=units,
        offsets=offsets,
    )
    return TrainingSample(document_id, text, result.spans, metadata)


def read_jsonl(path: str | Path, *, scheme: str = "BIO") -> list[TrainingSample]:
    """Read lossless JSONL samples and validate every record."""

    samples: list[TrainingSample] = []
    file_path = Path(path)
    with file_path.open("r", encoding="utf-8") as handle:
        for index, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON on line {index} of {file_path}") from exc
            if not isinstance(record, Mapping):
                raise ValueError(f"JSONL line {index} must contain an object")
            samples.append(_sample_from_record(record, index, scheme))
    return samples


def write_jsonl(
    path: str | Path,
    samples: Iterable[TrainingSample],
    *,
    scheme: str = "BIO",
) -> int:
    """Write validated samples, one JSON object per line, without raw logs."""

    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with file_path.open("w", encoding="utf-8", newline="\n") as handle:
        for sample in samples:
            sample.validate()
            handle.write(json.dumps(sample.to_dict(scheme), ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
            count += 1
    return count


def validate_jsonl(path: str | Path, *, scheme: str = "BIO") -> dict[str, Any]:
    samples = read_jsonl(path, scheme=scheme)
    entity_counts: dict[str, int] = {}
    for sample in samples:
        for span in sample.spans:
            entity_counts[span.entity_type] = entity_counts.get(span.entity_type, 0) + 1
    return {
        "samples": len(samples),
        "documents": len({sample.document_id for sample in samples}),
        "characters": sum(len(sample.text) for sample in samples),
        "entities": dict(sorted(entity_counts.items())),
        "scheme": scheme.upper(),
    }
