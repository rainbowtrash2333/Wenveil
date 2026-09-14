"""Adapter for the fixed external NER dataset schema.

The external dataset is deliberately kept separate from ``build_dataset``:
the latter supports the repository's lossless synthetic/teacher formats,
whereas this module accepts only the authorized fixed-split contract.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
import hashlib
import json
from pathlib import Path
from typing import Any

from .labels import validate_entity_type
from .types import EntityAnnotation, TrainingSample

FIXED_SPLITS = ("train", "dev", "test", "hard_test")
_TOP_LEVEL_FIELDS = {
    "clean_text",
    "document_id",
    "entities",
    "metadata",
    "relations",
    "text",
}
_ENTITY_FIELDS = {
    "start",
    "end",
    "entity_type",
    "mention",
}
_ENTITY_OPTIONAL_FIELDS = {"entity_id", "financial_subtype", "industry_subtype"}
_RELATION_FIELDS = {"relation_type", "source_mention", "target_entity_id"}


class ExternalDatasetError(ValueError):
    """Raised when a fixed external record is not safe to adapt."""


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _require_fields(
    value: Mapping[str, Any],
    required: set[str],
    *,
    kind: str,
    line_number: int,
    allowed: set[str] | None = None,
) -> None:
    missing = required - set(value)
    if missing:
        raise ExternalDatasetError(
            f"line {line_number} {kind} is missing required fields"
        )
    if allowed is not None and set(value) - allowed:
        raise ExternalDatasetError(
            f"line {line_number} {kind} contains unsupported fields"
        )


def _safe_document_id(document_id: str) -> str:
    digest = hashlib.sha256(document_id.encode("utf-8")).hexdigest()[:16]
    return f"external-{digest}"


def _validate_entity(
    entity: Any,
    *,
    text: str,
    line_number: int,
    entity_index: int,
) -> EntityAnnotation:
    if not isinstance(entity, Mapping):
        raise ExternalDatasetError(
            f"line {line_number} entity {entity_index} is not an object"
        )
    _require_fields(
        entity,
        _ENTITY_FIELDS,
        kind=f"entity {entity_index}",
        line_number=line_number,
        allowed=_ENTITY_FIELDS | _ENTITY_OPTIONAL_FIELDS,
    )
    if not _is_int(entity["start"]) or not _is_int(entity["end"]):
        raise ExternalDatasetError(
            f"line {line_number} entity {entity_index} offsets are invalid"
        )
    if not isinstance(entity["mention"], str):
        raise ExternalDatasetError(
            f"line {line_number} entity {entity_index} mention is invalid"
        )
    if not isinstance(entity["entity_type"], str):
        raise ExternalDatasetError(
            f"line {line_number} entity {entity_index} type is invalid"
        )
    try:
        validate_entity_type(entity["entity_type"])
    except ValueError:
        raise ExternalDatasetError(
            f"line {line_number} entity {entity_index} type is invalid"
        ) from None
    for field_name in _ENTITY_OPTIONAL_FIELDS:
        if field_name not in entity:
            continue
        if field_name == "entity_id" and not isinstance(entity[field_name], str):
            raise ExternalDatasetError(
                f"line {line_number} entity {entity_index} field types are invalid"
            )
        if (
            field_name != "entity_id"
            and entity[field_name] is not None
            and not isinstance(entity[field_name], str)
        ):
            raise ExternalDatasetError(
                f"line {line_number} entity {entity_index} field types are invalid"
            )
    start, end = entity["start"], entity["end"]
    # Python string indexing is Unicode code-point indexing, which is the
    # contract used by the external offsets.
    if start < 0 or end <= start or end > len(text):
        raise ExternalDatasetError(
            f"line {line_number} entity {entity_index} span bounds are invalid"
        )
    if text[start:end] != entity["mention"]:
        raise ExternalDatasetError(
            f"line {line_number} entity {entity_index} mention does not match its span"
        )
    return EntityAnnotation(start, end, entity["entity_type"], entity["mention"])


def _validate_relation(relation: Any, *, line_number: int, relation_index: int) -> str:
    if not isinstance(relation, Mapping):
        raise ExternalDatasetError(
            f"line {line_number} relation {relation_index} is not an object"
        )
    _require_fields(
        relation,
        _RELATION_FIELDS,
        kind=f"relation {relation_index}",
        line_number=line_number,
        allowed=_RELATION_FIELDS,
    )
    for field_name in _RELATION_FIELDS:
        if (
            not isinstance(relation[field_name], str)
            or not relation[field_name].strip()
        ):
            raise ExternalDatasetError(
                f"line {line_number} relation {relation_index} field types are invalid"
            )
    # ``target_entity_id`` is intentionally not resolved here.  The source
    # data permits valid references to entities outside the current record.
    return relation["relation_type"]


def _validate_record(
    record: Any,
    *,
    line_number: int,
) -> tuple[str, list[EntityAnnotation], Counter[str]]:
    if not isinstance(record, Mapping):
        raise ExternalDatasetError(f"line {line_number} must contain an object")
    _require_fields(
        record,
        _TOP_LEVEL_FIELDS,
        kind="record",
        line_number=line_number,
        allowed=_TOP_LEVEL_FIELDS,
    )
    for field_name in ("clean_text", "document_id", "text"):
        if not isinstance(record[field_name], str):
            raise ExternalDatasetError(
                f"line {line_number} text fields have invalid types"
            )
    if not record["document_id"].strip():
        raise ExternalDatasetError(f"line {line_number} document id is invalid")
    if not isinstance(record["entities"], list) or not isinstance(
        record["relations"], list
    ):
        raise ExternalDatasetError(
            f"line {line_number} entity/relation fields have invalid types"
        )
    if not isinstance(record["metadata"], dict):
        raise ExternalDatasetError(f"line {line_number} metadata has an invalid type")

    spans = [
        _validate_entity(
            entity,
            text=record["text"],
            line_number=line_number,
            entity_index=index,
        )
        for index, entity in enumerate(record["entities"])
    ]
    ordered = sorted(enumerate(spans), key=lambda item: (item[1].start, item[1].end))
    for (_, previous), (_, current) in zip(ordered, ordered[1:]):
        if current.start < previous.end:
            raise ExternalDatasetError(
                f"line {line_number} contains overlapping entities"
            )

    relation_types = Counter(
        _validate_relation(relation, line_number=line_number, relation_index=index)
        for index, relation in enumerate(record["relations"])
    )
    return record["document_id"], spans, relation_types


def read_external_split(
    path_or_data_dir: str | Path,
    split: str | None = None,
) -> tuple[list[TrainingSample], dict[str, Any]]:
    """Read one fixed split and return samples plus a safe summary.

    ``path_or_data_dir`` may be a split JSONL path or a directory.  When a
    directory is provided, ``split`` is required and maps to ``<split>.jsonl``.
    Only ``text`` and NER ``start/end/entity_type/mention`` are retained in
    samples; clean text, metadata, relations, and source identifiers are not.
    """

    if split is not None and split not in FIXED_SPLITS:
        raise ExternalDatasetError("split must be one of train, dev, test, hard_test")
    candidate = Path(path_or_data_dir)
    if split is not None and not candidate.is_file():
        candidate = candidate / f"{split}.jsonl"
    actual_split = split or candidate.stem
    if actual_split not in FIXED_SPLITS:
        raise ExternalDatasetError("split must be one of train, dev, test, hard_test")
    if not candidate.is_file():
        raise ExternalDatasetError("external split file does not exist")

    samples: list[TrainingSample] = []
    entity_counts: Counter[str] = Counter()
    relation_counts: Counter[str] = Counter()
    seen_ids: set[str] = set()
    try:
        handle = candidate.open("r", encoding="utf-8")
    except OSError:
        raise ExternalDatasetError("external split file cannot be read") from None
    try:
        with handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ExternalDatasetError(
                        f"line {line_number} is not valid JSON"
                    ) from exc
                source_id, spans, record_relations = _validate_record(
                    record,
                    line_number=line_number,
                )
                if source_id in seen_ids:
                    raise ExternalDatasetError(f"line {line_number} repeats a document")
                seen_ids.add(source_id)
                safe_id = _safe_document_id(source_id)
                sample = TrainingSample(safe_id, record["text"], tuple(spans), {})
                try:
                    sample.validate()
                except ValueError:
                    raise ExternalDatasetError(
                        f"line {line_number} adapted sample is invalid"
                    ) from None
                samples.append(sample)
                entity_counts.update(span.entity_type for span in spans)
                relation_counts.update(record_relations)
    except OSError:
        raise ExternalDatasetError("external split file cannot be read") from None

    try:
        digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
    except OSError:
        raise ExternalDatasetError("external split file cannot be read") from None
    summary = {
        "split": actual_split,
        "rows": len(samples),
        "entities": sum(entity_counts.values()),
        "labels": dict(sorted(entity_counts.items())),
        "relation_types": dict(sorted(relation_counts.items())),
        "sha256": digest,
    }
    return samples, summary


def read_external_splits(
    data_dir: str | Path,
    splits: Iterable[str] = FIXED_SPLITS,
) -> tuple[dict[str, list[TrainingSample]], dict[str, dict[str, Any]]]:
    """Read fixed splits and reject document-id overlap across split files."""

    requested = tuple(splits)
    if not requested or any(split not in FIXED_SPLITS for split in requested):
        raise ExternalDatasetError(
            "splits must be chosen from train, dev, test, hard_test"
        )
    result: dict[str, list[TrainingSample]] = {}
    summaries: dict[str, dict[str, Any]] = {}
    source_ids: dict[str, str] = {}
    for split in requested:
        samples, summary = read_external_split(data_dir, split)
        result[split] = samples
        summaries[split] = summary
        for sample in samples:
            if sample.document_id in source_ids:
                raise ExternalDatasetError("document ids overlap across fixed splits")
            source_ids[sample.document_id] = split
    return result, summaries


def validate_external_dataset(
    data_dir: str | Path,
    splits: Iterable[str] = FIXED_SPLITS,
) -> dict[str, Any]:
    """Return safe summaries without writing or copying dataset content."""

    _, summaries = read_external_splits(data_dir, splits)
    return {"splits": {split: summaries[split] for split in summaries}}


# Explicit aliases make the adapter discoverable to callers that use
# ``load_*`` terminology while keeping one implementation.
load_external_split = read_external_split
load_external_splits = read_external_splits
