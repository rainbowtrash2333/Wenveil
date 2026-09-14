"""Optional Qwen token-classification training entry point.

Importing this module never imports torch or transformers.  Use
``validate_dataset``/``prepare_training_rows`` for offline work, and call
``train_token_classifier`` only in an environment that has the optional
training dependencies and a local model checkpoint.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import inspect
import json
from pathlib import Path
from typing import Any

from .build_dataset import read_jsonl, write_jsonl
from .external_dataset import read_external_splits
from .labels import normalize_scheme, spans_to_labels, validate_entity_type
from .qwen35_token_classifier import (
    load_token_classifier_from_base,
    write_checkpoint_metadata,
)
from .splitting import DatasetSplits, split_by_document
from .types import EntityAnnotation, TrainingSample


class OptionalTrainingDependencyError(RuntimeError):
    """Raised when actual model training is requested without optional deps."""


class FeatureAlignmentError(ValueError):
    """Raised when tokenizer offsets cannot represent an annotated span."""


def _safe_source_metadata(source_metadata: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Keep only the fixed external source fields allowed in checkpoints."""

    if not source_metadata:
        return None
    if source_metadata.get("kind") != "external_fixed_splits":
        raise ValueError("unsupported training source metadata")
    splits = source_metadata.get("splits")
    if set(splits or ()) != {"train", "dev"}:
        raise ValueError("external training source must contain train and dev only")
    files = source_metadata.get("files")
    if not isinstance(files, Mapping) or set(files) != {"train", "dev"}:
        raise ValueError("external training source metadata has invalid splits")
    safe_files: dict[str, dict[str, Any]] = {}
    for split in ("train", "dev"):
        value = files[split]
        if not isinstance(value, Mapping):
            raise ValueError("external training source metadata has invalid file data")
        digest = value.get("sha256")
        rows = value.get("rows")
        labels = value.get("labels")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest.lower())
            or not isinstance(rows, int)
            or isinstance(rows, bool)
            or rows < 0
            or not isinstance(labels, Mapping)
            or any(
                not isinstance(label, str)
                or not isinstance(count, int)
                or isinstance(count, bool)
                or count < 0
                for label, count in labels.items()
            )
        ):
            raise ValueError("external training source metadata has invalid statistics")
        safe_files[split] = {
            "sha256": digest.lower(),
            "rows": rows,
            "labels": dict(sorted(labels.items())),
        }
    return {
        "kind": "external_fixed_splits",
        "splits": ["train", "dev"],
        "files": safe_files,
    }


def _read_resume_metadata(resume_from_checkpoint: str | None) -> dict[str, Any] | None:
    if not resume_from_checkpoint:
        return None
    metadata_path = Path(resume_from_checkpoint) / "training_config.json"
    if not metadata_path.is_file():
        return None
    try:
        value = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("resume checkpoint metadata is invalid") from exc
    if not isinstance(value, dict):
        raise ValueError("resume checkpoint metadata is invalid")
    return value


def _validate_resume_compatibility(
    metadata: Mapping[str, Any] | None,
    config: "TrainingConfig",
    labels: Sequence[str],
    *,
    source_metadata: Mapping[str, Any] | None = None,
) -> None:
    external_resume = bool(
        config.resume_from_checkpoint
        and source_metadata
        and source_metadata.get("kind") == "external_fixed_splits"
    )
    if external_resume:
        if not metadata:
            raise ValueError("external resume requires checkpoint metadata")
        if metadata.get("model_family") != "Qwen3.5":
            raise ValueError("external resume checkpoint metadata is invalid")
        previous_source = metadata.get("source")
        try:
            safe_previous_source = _safe_source_metadata(previous_source)
        except (TypeError, ValueError):
            raise ValueError("external resume checkpoint source metadata is invalid") from None
        if safe_previous_source != dict(source_metadata):
            raise ValueError("external resume checkpoint source is incompatible")
    if not metadata:
        return
    current_model_name = Path(config.model_name_or_path).name
    previous_model_name = metadata.get("model_name")
    if external_resume and (
        not isinstance(previous_model_name, str) or not previous_model_name
    ):
        raise ValueError("external resume checkpoint metadata is incomplete")
    if previous_model_name is not None and previous_model_name != current_model_name:
        raise ValueError("resume checkpoint is incompatible with model")
    previous_config = metadata.get("config")
    if not isinstance(previous_config, Mapping):
        previous_config = metadata
    if external_resume and any(
        key not in previous_config for key in ("scheme", "max_length", "overlap")
    ):
        raise ValueError("external resume checkpoint metadata is incomplete")
    for key, current in (
        ("scheme", normalize_scheme(config.scheme)),
        ("max_length", config.max_length),
        ("overlap", config.overlap),
    ):
        if key in previous_config and previous_config[key] != current:
            raise ValueError(f"resume checkpoint is incompatible with {key}")
    previous_mapping = metadata.get("label_mapping")
    if external_resume and (
        not isinstance(previous_mapping, Mapping)
        or not isinstance(previous_mapping.get("id2label"), Mapping)
    ):
        raise ValueError("external resume checkpoint metadata is incomplete")
    if isinstance(previous_mapping, Mapping):
        previous_id2label = previous_mapping.get("id2label")
        if isinstance(previous_id2label, Mapping):
            previous_labels = [
                str(previous_id2label[key])
                for key in sorted(previous_id2label, key=lambda item: int(item))
            ]
            if previous_labels != list(labels):
                raise ValueError("resume checkpoint label mapping is incompatible")


@dataclass(frozen=True, slots=True)
class TrainingConfig:
    model_name_or_path: str
    output_dir: str = "training-output/qwen-ner"
    scheme: str = "BIO"
    max_length: int = 1024
    epochs: float = 3.0
    train_batch_size: int = 2
    eval_batch_size: int = 2
    learning_rate: float = 2e-5
    weight_decay: float = 0.01
    logging_steps: int = 10
    save_total_limit: int = 2
    warmup_ratio: float = 0.0
    save_strategy: str = "epoch"
    evaluation_strategy: str = "epoch"
    seed: int = 42
    local_files_only: bool = True
    fp16: bool = False
    gradient_accumulation_steps: int = 1
    overlap: int = 128
    resume_from_checkpoint: str | None = None

    def validate(self) -> None:
        normalize_scheme(self.scheme)
        if self.max_length <= 0:
            raise ValueError("max_length must be positive")
        if self.epochs <= 0:
            raise ValueError("epochs must be positive")
        if self.train_batch_size <= 0 or self.eval_batch_size <= 0:
            raise ValueError("batch sizes must be positive")
        if self.gradient_accumulation_steps <= 0:
            raise ValueError("gradient_accumulation_steps must be positive")
        if self.overlap < 0 or self.overlap >= self.max_length:
            raise ValueError("overlap must satisfy 0 <= overlap < max_length")
        if not 0 <= self.warmup_ratio < 1:
            raise ValueError("warmup_ratio must satisfy 0 <= warmup_ratio < 1")
        if self.save_strategy not in {"no", "steps", "epoch"}:
            raise ValueError("save_strategy must be no, steps, or epoch")
        if self.evaluation_strategy not in {"no", "steps", "epoch"}:
            raise ValueError("evaluation_strategy must be no, steps, or epoch")


def _build_training_metadata(
    config: TrainingConfig,
    *,
    source_metadata: Mapping[str, Any] | None,
    train_rows: int,
    validation_rows: int,
    id_to_label: Mapping[int, str],
    label_to_id: Mapping[str, int],
    global_step: int,
    best_model_checkpoint: str | None,
) -> dict[str, Any]:
    """Build the safe metadata shared by the output and every checkpoint."""

    metadata: dict[str, Any] = {
        "schema_version": 1,
        "model_family": "Qwen3.5",
        "model_name": Path(config.model_name_or_path).name,
        "output_name": Path(config.output_dir).name,
        "scheme": normalize_scheme(config.scheme),
        "max_length": config.max_length,
        "overlap": config.overlap,
        "seed": config.seed,
        "train_rows": train_rows,
        "validation_rows": validation_rows,
        "config": {
            "epochs": config.epochs,
            "train_batch_size": config.train_batch_size,
            "eval_batch_size": config.eval_batch_size,
            "learning_rate": config.learning_rate,
            "weight_decay": config.weight_decay,
            "logging_steps": config.logging_steps,
            "save_total_limit": config.save_total_limit,
            "gradient_accumulation_steps": config.gradient_accumulation_steps,
            "warmup_ratio": config.warmup_ratio,
            "save_strategy": config.save_strategy,
            "evaluation_strategy": config.evaluation_strategy,
            "seed": config.seed,
            "max_length": config.max_length,
            "overlap": config.overlap,
            "scheme": normalize_scheme(config.scheme),
            "fp16": config.fp16,
        },
        "resume": {
            "requested": bool(config.resume_from_checkpoint),
            "status": "resumed" if config.resume_from_checkpoint else "fresh",
            "checkpoint_name": (
                Path(config.resume_from_checkpoint).name
                if config.resume_from_checkpoint
                else None
            ),
            "global_step": int(global_step),
        },
        "best_model_checkpoint_name": (
            Path(best_model_checkpoint).name if best_model_checkpoint else None
        ),
        "label_mapping": {
            "id2label": {str(key): str(value) for key, value in id_to_label.items()},
            "label2id": {str(key): int(value) for key, value in label_to_id.items()},
        },
    }
    if source_metadata:
        metadata["source"] = _safe_source_metadata(source_metadata)
    return metadata


def _persist_checkpoint_artifacts(
    checkpoint_dir: str | Path,
    *,
    model: Any,
    tokenizer: Any,
    metadata_text: str,
) -> None:
    """Keep a recoverable checkpoint self-contained and provenance-safe."""

    destination = Path(checkpoint_dir)
    destination.mkdir(parents=True, exist_ok=True)
    tokenizer.save_pretrained(destination)
    write_checkpoint_metadata(model, destination)
    (destination / "training_config.json").write_text(
        metadata_text,
        encoding="utf-8",
    )


def label_vocabulary(entity_types: Iterable[str], scheme: str = "BIO") -> list[str]:
    normalized = normalize_scheme(scheme)
    types = sorted({validate_entity_type(entity_type) for entity_type in entity_types})
    labels = ["O"]
    if normalized == "BIO":
        prefixes = ("B", "I")
    elif normalized == "BIOES":
        prefixes = ("B", "I", "E", "S")
    else:
        prefixes = ("B", "I", "L", "U")
    for entity_type in types:
        labels.extend(f"{prefix}-{entity_type}" for prefix in prefixes)
    return labels


def entity_types_from_samples(samples: Iterable[TrainingSample]) -> list[str]:
    return sorted({span.entity_type for sample in samples for span in sample.spans})


def align_offsets_to_labels(
    offsets: Sequence[tuple[int, int]],
    spans: Iterable[EntityAnnotation | object],
    *,
    text: str,
    scheme: str = "BIO",
    special_label: int = -100,
    strict: bool = True,
    ignored_spans: Iterable[EntityAnnotation | object] = (),
) -> list[str | int]:
    """Align fast-tokenizer offsets to flat entity spans.

    Special tokens conventionally have ``(0, 0)`` offsets and receive -100.
    Every annotated span must overlap at least one encoded token when
    ``strict`` is true; this prevents silent labels disappearing through
    truncation.
    """

    normalized = normalize_scheme(scheme)
    annotations = [EntityAnnotation.from_value(span, text) for span in spans]
    ignored = [EntityAnnotation.from_value(span, text) for span in ignored_spans]
    annotations.sort(key=lambda span: (span.start, span.end))
    previous_end = -1
    for span in annotations:
        span.validate_against(text)
        if span.start < previous_end:
            raise FeatureAlignmentError("overlapping spans cannot be token-aligned")
        previous_end = span.end
    for span in ignored:
        span.validate_against(text)

    token_indices: list[list[int]] = [[] for _ in annotations]
    labels: list[str | int] = ["O"] * len(offsets)
    for token_index, (start, end) in enumerate(offsets):
        start, end = int(start), int(end)
        if start < 0 or end < start or end > len(text):
            raise FeatureAlignmentError("token offsets are outside the source text")
        if end <= start:
            labels[token_index] = special_label
            continue
        overlaps = [
            span_index
            for span_index, span in enumerate(annotations)
            if start < span.end and span.start < end
        ]
        if len(overlaps) > 1:
            raise FeatureAlignmentError(
                f"token offset {start}:{end} crosses multiple entities"
            )
        if overlaps:
            span = annotations[overlaps[0]]
            if start < span.start or end > span.end:
                raise FeatureAlignmentError(
                    f"token offset {start}:{end} crosses the boundary of "
                    f"{span.entity_type} at {span.start}:{span.end}"
                )
            token_indices[overlaps[0]].append(token_index)
        elif any(start < span.end and span.start < end for span in ignored):
            # A sliding-window edge may contain only part of an entity.  It is
            # neither a positive nor a negative example in this window.
            labels[token_index] = special_label

    if strict:
        missing: list[EntityAnnotation] = []
        for span, indices in zip(annotations, token_indices):
            if not indices:
                missing.append(span)
                continue
            covered = sorted(
                (int(offsets[token_index][0]), int(offsets[token_index][1]))
                for token_index in indices
            )
            cursor = span.start
            for covered_start, covered_end in covered:
                if covered_start > cursor:
                    break
                cursor = max(cursor, covered_end)
                if cursor >= span.end:
                    break
            if cursor < span.end:
                missing.append(span)
        if missing:
            locations = ", ".join(f"{span.start}:{span.end}" for span in missing)
            raise FeatureAlignmentError(f"encoded offsets omit annotated spans: {locations}")

    for span, indices in zip(annotations, token_indices):
        if not indices:
            continue
        if normalized == "BIO":
            labels[indices[0]] = f"B-{span.entity_type}"
            for token_index in indices[1:]:
                labels[token_index] = f"I-{span.entity_type}"
        elif normalized == "BIOES":
            if len(indices) == 1:
                labels[indices[0]] = f"S-{span.entity_type}"
            else:
                labels[indices[0]] = f"B-{span.entity_type}"
                for token_index in indices[1:-1]:
                    labels[token_index] = f"I-{span.entity_type}"
                labels[indices[-1]] = f"E-{span.entity_type}"
        elif len(indices) == 1:
            labels[indices[0]] = f"U-{span.entity_type}"
        else:
            labels[indices[0]] = f"B-{span.entity_type}"
            for token_index in indices[1:-1]:
                labels[token_index] = f"I-{span.entity_type}"
            labels[indices[-1]] = f"L-{span.entity_type}"
    return labels


def _tokenizer_output(
    tokenizer: Any,
    text: str,
    *,
    max_length: int,
    stride: int,
) -> Mapping[str, Any]:
    try:
        output = tokenizer(
            list(text),
            is_split_into_words=True,
            return_offsets_mapping=True,
            return_overflowing_tokens=True,
            truncation=True,
            max_length=max_length,
            stride=stride,
            padding=False,
        )
    except TypeError as exc:
        raise FeatureAlignmentError(
            "tokenizer must be a fast tokenizer supporting return_offsets_mapping"
        ) from exc
    if "offset_mapping" not in output:
        raise FeatureAlignmentError("tokenizer output has no offset_mapping")
    if hasattr(output, "word_ids"):
        raw_offsets = output["offset_mapping"]
        input_ids = output.get("input_ids")
        is_batched = bool(input_ids and not isinstance(input_ids[0], int))
        if not is_batched:
            raw_offsets = [raw_offsets]
        global_offsets: list[list[tuple[int, int]]] = []
        for batch_index, offsets in enumerate(raw_offsets):
            word_ids = output.word_ids(batch_index=batch_index)
            global_offsets.append(
                [
                    (word_id + int(offset[0]), word_id + int(offset[1]))
                    if word_id is not None and int(offset[1]) > int(offset[0])
                    else (0, 0)
                    for word_id, offset in zip(word_ids, offsets)
                ]
            )
        output = dict(output)
        output["offset_mapping"] = global_offsets if is_batched else global_offsets[0]
    return output


def prepare_training_rows(
    samples: Iterable[TrainingSample],
    tokenizer: Any,
    *,
    scheme: str = "BIO",
    max_length: int = 1024,
    stride: int = 128,
    strict_alignment: bool = True,
) -> list[dict[str, Any]]:
    """Tokenize samples and attach aligned labels without importing torch."""

    normalized = normalize_scheme(scheme)
    if stride < 0 or stride >= max_length:
        raise ValueError("stride must satisfy 0 <= stride < max_length")
    rows: list[dict[str, Any]] = []
    for sample in samples:
        sample.validate()
        raw_output = dict(
            _tokenizer_output(
                tokenizer,
                sample.text,
                max_length=max_length,
                stride=stride,
            )
        )
        raw_offsets = raw_output.pop("offset_mapping")
        raw_input_ids = raw_output.get("input_ids")
        if raw_input_ids and isinstance(raw_input_ids[0], int):
            # Some lightweight/fake tokenizers ignore overflow arguments.
            raw_output = {key: [value] for key, value in raw_output.items()}
            raw_offsets = [raw_offsets]
        # A span may be fully visible in several overflow windows.  Assign it
        # to the first eligible window so the Trainer sees one positive target
        # per source entity; all other occurrences are ignored (-100).
        window_records: list[tuple[list[tuple[int, int]], int, int]] = []
        for offsets_value in raw_offsets:
            offsets = [tuple(item) for item in offsets_value]
            positive_offsets = [
                (int(start), int(end))
                for start, end in offsets
                if int(end) > int(start)
            ]
            content_start = min((start for start, _ in positive_offsets), default=0)
            content_end = max((end for _, end in positive_offsets), default=0)
            window_records.append((offsets, content_start, content_end))
        owners: dict[tuple[int, int, str], int] = {}
        for span in sample.spans:
            key = (span.start, span.end, span.entity_type)
            eligible = [
                index
                for index, (_, content_start, content_end) in enumerate(window_records)
                if content_start <= span.start and span.end <= content_end
            ]
            if eligible:
                owners[key] = eligible[0]
        covered_spans: set[tuple[int, int, str]] = set()
        for chunk_index, (offsets, content_start, content_end) in enumerate(window_records):
            contained = [
                span
                for span in sample.spans
                if owners.get((span.start, span.end, span.entity_type)) == chunk_index
            ]
            partial = [
                span
                for span in sample.spans
                if span.start < content_end
                and content_start < span.end
                and span not in contained
            ]
            labels = align_offsets_to_labels(
                offsets,
                contained,
                text=sample.text,
                scheme=normalized,
                strict=strict_alignment,
                ignored_spans=partial,
            )
            output = {
                key: value[chunk_index]
                for key, value in raw_output.items()
                if key in {"input_ids", "attention_mask", "token_type_ids", "position_ids"}
                and isinstance(value, list)
                and len(value) == len(raw_offsets)
            }
            output["labels"] = labels
            output["document_id"] = (
                sample.document_id
                if len(raw_offsets) == 1
                else f"{sample.document_id}#window-{chunk_index:04d}"
            )
            output["window_start"] = content_start
            output["window_end"] = content_end
            rows.append(output)
            covered_spans.update((span.start, span.end, span.entity_type) for span in contained)
        required = {(span.start, span.end, span.entity_type) for span in sample.spans}
        missing = required - covered_spans
        if strict_alignment and missing:
            locations = ", ".join(f"{start}:{end}" for start, end, _ in sorted(missing))
            raise FeatureAlignmentError(f"sliding windows omit annotated spans: {locations}")
    return rows


class ListDataset:
    """Small list-backed dataset understood by Transformers Trainer."""

    MODEL_FEATURE_KEYS = frozenset(
        {"input_ids", "attention_mask", "token_type_ids", "position_ids", "labels"}
    )

    def __init__(self, rows: Sequence[Mapping[str, Any]]):
        self._rows = [dict(row) for row in rows]

    def __len__(self) -> int:
        return len(self._rows)

    def __getitem__(self, index: int) -> dict[str, Any]:
        row = dict(self._rows[index])
        return {
            key: row[key]
            for key in self.MODEL_FEATURE_KEYS
            if key in row
        }


def validate_dataset(samples: Iterable[TrainingSample], *, scheme: str = "BIO") -> dict[str, Any]:
    sample_list = list(samples)
    normalized = normalize_scheme(scheme)
    entities: dict[str, int] = {}
    for sample in sample_list:
        sample.validate()
        # Generation plus validation is the central lossless gate.
        labels = spans_to_labels(sample.text, sample.spans, scheme=normalized)
        if len(labels) != len(sample.text):
            raise ValueError(f"labels do not cover source text for {sample.document_id}")
        for span in sample.spans:
            entities[span.entity_type] = entities.get(span.entity_type, 0) + 1
    return {
        "samples": len(sample_list),
        "documents": len({sample.document_id for sample in sample_list}),
        "characters": sum(len(sample.text) for sample in sample_list),
        "entities": dict(sorted(entities.items())),
        "labels": label_vocabulary(entities, normalized),
        "scheme": normalized,
    }


def _import_training_dependencies() -> tuple[Any, Any]:
    try:
        import torch
    except ImportError as exc:
        raise OptionalTrainingDependencyError(
            "training requires torch; install the optional local training dependencies"
        ) from exc
    try:
        import transformers
    except ImportError as exc:
        raise OptionalTrainingDependencyError(
            "training requires transformers; data validation remains dependency-free"
        ) from exc
    return torch, transformers


def train_token_classifier(
    train_samples: Iterable[TrainingSample],
    validation_samples: Iterable[TrainingSample] = (),
    *,
    config: TrainingConfig,
    source_metadata: Mapping[str, Any] | None = None,
) -> Any:
    """Train a Qwen-compatible token classifier when optional deps are present."""

    config.validate()
    safe_source = _safe_source_metadata(source_metadata)
    resume_metadata = _read_resume_metadata(config.resume_from_checkpoint)
    train_list = list(train_samples)
    validation_list = list(validation_samples)
    if not train_list:
        raise ValueError("at least one training sample is required")
    if validation_list and (
        config.evaluation_strategy == "no"
        or config.save_strategy != config.evaluation_strategy
    ):
        raise ValueError("validation requires matching save and evaluation strategies")
    validate_dataset(train_list, scheme=config.scheme)
    validate_dataset(validation_list, scheme=config.scheme)

    torch, transformers = _import_training_dependencies()
    try:
        tokenizer = transformers.AutoTokenizer.from_pretrained(
            config.model_name_or_path,
            use_fast=True,
            local_files_only=config.local_files_only,
        )
    except Exception as exc:
        mode = "local" if config.local_files_only else "configured"
        raise RuntimeError(f"could not load {mode} tokenizer") from exc

    entity_types = entity_types_from_samples([*train_list, *validation_list])
    labels = label_vocabulary(entity_types, config.scheme)
    _validate_resume_compatibility(
        resume_metadata,
        config,
        labels,
        source_metadata=safe_source,
    )
    label_to_id = {label: index for index, label in enumerate(labels)}
    id_to_label = {index: label for label, index in label_to_id.items()}
    train_rows = prepare_training_rows(
        train_list,
        tokenizer,
        scheme=config.scheme,
        max_length=config.max_length,
        stride=config.overlap,
    )
    validation_rows = prepare_training_rows(
        validation_list,
        tokenizer,
        scheme=config.scheme,
        max_length=config.max_length,
        stride=config.overlap,
    )
    for row in [*train_rows, *validation_rows]:
        row["labels"] = [
            special if isinstance(special, int) else label_to_id[special]
            for special in row["labels"]
        ]

    try:
        model = load_token_classifier_from_base(
            config.model_name_or_path,
            torch=torch,
            transformers=transformers,
            num_labels=len(labels),
            id2label=id_to_label,
            label2id=label_to_id,
            local_files_only=config.local_files_only,
        )
    except Exception as exc:
        raise RuntimeError("could not load token-classification model") from exc

    def metadata_text(*, global_step: int, best_model_checkpoint: str | None) -> str:
        return json.dumps(
            _build_training_metadata(
                config,
                source_metadata=safe_source,
                train_rows=len(train_rows),
                validation_rows=len(validation_rows),
                id_to_label=id_to_label,
                label_to_id=label_to_id,
                global_step=global_step,
                best_model_checkpoint=best_model_checkpoint,
            ),
            ensure_ascii=False,
            indent=2,
        ) + "\n"

    def persist_checkpoint_metadata(
        checkpoint_dir: str | Path,
        *,
        global_step: int,
        best_model_checkpoint: str | None,
    ) -> None:
        _persist_checkpoint_artifacts(
            checkpoint_dir,
            model=model,
            tokenizer=tokenizer,
            metadata_text=metadata_text(
                global_step=global_step,
                best_model_checkpoint=best_model_checkpoint,
            ),
        )

    callback_base = getattr(transformers, "TrainerCallback", object)

    class CheckpointMetadataCallback(callback_base):
        def on_save(self, args: Any, state: Any, control: Any, **_: Any) -> Any:
            checkpoint_dir = getattr(state, "last_model_checkpoint", None)
            if not checkpoint_dir:
                checkpoint_dir = Path(args.output_dir) / (
                    f"checkpoint-{getattr(state, 'global_step', 0)}"
                )
            if checkpoint_dir:
                persist_checkpoint_metadata(
                    checkpoint_dir,
                    global_step=int(getattr(state, "global_step", 0)),
                    best_model_checkpoint=getattr(state, "best_model_checkpoint", None),
                )
            return control

    argument_kwargs: dict[str, Any] = {
        "output_dir": config.output_dir,
        "num_train_epochs": config.epochs,
        "per_device_train_batch_size": config.train_batch_size,
        "per_device_eval_batch_size": config.eval_batch_size,
        "learning_rate": config.learning_rate,
        "weight_decay": config.weight_decay,
        "logging_steps": config.logging_steps,
        "save_total_limit": config.save_total_limit,
        "warmup_ratio": config.warmup_ratio,
        "seed": config.seed,
        "fp16": config.fp16,
        "gradient_accumulation_steps": config.gradient_accumulation_steps,
        "report_to": [],
        "remove_unused_columns": False,
        "save_strategy": config.save_strategy,
    }
    argument_parameters = inspect.signature(transformers.TrainingArguments.__init__).parameters
    if "save_safetensors" in argument_parameters:
        argument_kwargs["save_safetensors"] = False
    if "eval_strategy" in argument_parameters:
        argument_kwargs["eval_strategy"] = (
            config.evaluation_strategy if validation_rows else "no"
        )
    elif "evaluation_strategy" in argument_parameters:
        argument_kwargs["evaluation_strategy"] = (
            config.evaluation_strategy if validation_rows else "no"
        )
    if validation_rows:
        argument_kwargs.update(
            {
                "load_best_model_at_end": True,
                "metric_for_best_model": "eval_loss",
                "greater_is_better": False,
            }
        )
    training_args = transformers.TrainingArguments(**argument_kwargs)
    collator = transformers.DataCollatorForTokenClassification(tokenizer=tokenizer)
    trainer_kwargs: dict[str, Any] = {
        "model": model,
        "args": training_args,
        "train_dataset": ListDataset(train_rows),
        "eval_dataset": ListDataset(validation_rows) if validation_rows else None,
        "data_collator": collator,
    }
    trainer_parameters = inspect.signature(transformers.Trainer.__init__).parameters
    if "processing_class" in trainer_parameters:
        trainer_kwargs["processing_class"] = tokenizer
    else:
        trainer_kwargs["tokenizer"] = tokenizer
    if "callbacks" in trainer_parameters:
        trainer_kwargs["callbacks"] = [CheckpointMetadataCallback()]
    trainer = transformers.Trainer(**trainer_kwargs)
    trainer.train(resume_from_checkpoint=config.resume_from_checkpoint)
    trainer.save_model(config.output_dir)
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    tokenizer.save_pretrained(output_dir)
    write_checkpoint_metadata(model, output_dir)
    encoded_metadata = metadata_text(
        global_step=int(getattr(trainer.state, "global_step", 0)),
        best_model_checkpoint=getattr(trainer.state, "best_model_checkpoint", None),
    )
    (output_dir / "training_config.json").write_text(
        encoded_metadata,
        encoding="utf-8",
    )
    checkpoint_dirs = sorted(
        path for path in output_dir.glob("checkpoint-*") if path.is_dir()
    )
    for checkpoint_dir in checkpoint_dirs:
        persist_checkpoint_metadata(
            checkpoint_dir,
            global_step=int(getattr(trainer.state, "global_step", 0)),
            best_model_checkpoint=getattr(trainer.state, "best_model_checkpoint", None),
        )
    best_checkpoint = getattr(trainer.state, "best_model_checkpoint", None)
    if best_checkpoint and Path(best_checkpoint) not in checkpoint_dirs:
        persist_checkpoint_metadata(
            best_checkpoint,
            global_step=int(getattr(trainer.state, "global_step", 0)),
            best_model_checkpoint=best_checkpoint,
        )
    return trainer


def prepare_document_splits(
    samples: Iterable[TrainingSample],
    *,
    output_dir: str | Path,
    scheme: str = "BIO",
    train_ratio: float = 0.70,
    validation_ratio: float = 0.15,
    test_ratio: float = 0.15,
    seed: int = 42,
    augmentation_copies: int = 0,
) -> DatasetSplits:
    """Validate and write document-level JSONL splits for offline workflows."""

    sample_list = list(samples)
    if augmentation_copies:
        from .augment_ocr import augment_dataset

        sample_list = augment_dataset(sample_list, copies=augmentation_copies, seed=seed)
    validate_dataset(sample_list, scheme=scheme)
    splits = split_by_document(
        sample_list,
        train_ratio=train_ratio,
        validation_ratio=validation_ratio,
        test_ratio=test_ratio,
        seed=seed,
    )
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    write_jsonl(destination / "train.jsonl", splits.train, scheme=scheme)
    write_jsonl(destination / "validation.jsonl", splits.validation, scheme=scheme)
    write_jsonl(destination / "test.jsonl", splits.test, scheme=scheme)
    return splits


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate/prepare/train a Qwen NER dataset")
    parser.add_argument("--input", help="single JSONL file for offline validation or splitting")
    parser.add_argument("--train", help="prepared training JSONL")
    parser.add_argument("--validation", help="prepared validation JSONL")
    parser.add_argument("--model", help="local Qwen checkpoint directory")
    parser.add_argument("--output-dir", default="training-output/qwen-ner")
    parser.add_argument("--prepare-dir", help="write document-level train/validation/test JSONL")
    parser.add_argument(
        "--data-dir",
        default="data",
        help="fixed external data directory containing train/dev/test/hard_test JSONL",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="do not import training dependencies",
    )
    parser.add_argument("--scheme", choices=("BIO", "BIOES", "BILOU"), default="BIO")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-length", type=int, default=1024)
    parser.add_argument("--overlap", type=int, default=128)
    parser.add_argument("--epochs", type=float, default=3.0)
    parser.add_argument("--train-batch-size", type=int, default=2)
    parser.add_argument("--eval-batch-size", type=int, default=2)
    parser.add_argument("--learning-rate", type=float, default=2e-5)
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--logging-steps", type=int, default=10)
    parser.add_argument("--save-total-limit", type=int, default=2)
    parser.add_argument("--warmup-ratio", type=float, default=0.0)
    parser.add_argument("--save-strategy", choices=("no", "steps", "epoch"), default="epoch")
    parser.add_argument(
        "--evaluation-strategy",
        choices=("no", "steps", "epoch"),
        default="epoch",
    )
    parser.add_argument("--fp16", action="store_true")
    parser.add_argument("--gradient-accumulation-steps", type=int, default=1)
    parser.add_argument("--resume-from-checkpoint")
    parser.add_argument("--augmentation-copies", type=int, default=0)
    parser.add_argument(
        "--allow-network",
        action="store_true",
        help="allow Transformers cache/network lookup",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    external_mode = not any(
        (args.input, args.train, args.validation, args.prepare_dir)
    )
    if args.data_dir != "data" and not external_mode:
        raise SystemExit("--data-dir cannot be combined with legacy dataset options")
    if external_mode:
        all_splits, summaries = read_external_splits(args.data_dir)
        train_dev_source = {
            "kind": "external_fixed_splits",
            "splits": ["train", "dev"],
            "files": {
                split: {
                    "sha256": summaries[split]["sha256"],
                    "rows": summaries[split]["rows"],
                    "labels": summaries[split]["labels"],
                }
                for split in ("train", "dev")
            },
        }
        if args.validate_only:
            safe_summary = {
                split: {
                    "sha256": summaries[split]["sha256"],
                    "rows": summaries[split]["rows"],
                    "entities": summaries[split]["entities"],
                    "labels": summaries[split]["labels"],
                    "relation_types": summaries[split]["relation_types"],
                }
                for split in ("train", "dev", "test", "hard_test")
            }
            print(
                json.dumps(
                    {
                        "kind": "external_fixed_splits",
                        "splits": safe_summary,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0
        if not args.model:
            raise SystemExit("--model is required for external training")
        train_token_classifier(
            all_splits["train"],
            all_splits["dev"],
            config=TrainingConfig(
                model_name_or_path=args.model,
                output_dir=args.output_dir,
                scheme=args.scheme,
                max_length=args.max_length,
                epochs=args.epochs,
                train_batch_size=args.train_batch_size,
                eval_batch_size=args.eval_batch_size,
                learning_rate=args.learning_rate,
                weight_decay=args.weight_decay,
                logging_steps=args.logging_steps,
                save_total_limit=args.save_total_limit,
                warmup_ratio=args.warmup_ratio,
                save_strategy=args.save_strategy,
                evaluation_strategy=args.evaluation_strategy,
                seed=args.seed,
                fp16=args.fp16,
                gradient_accumulation_steps=args.gradient_accumulation_steps,
                overlap=args.overlap,
                resume_from_checkpoint=args.resume_from_checkpoint,
                local_files_only=not args.allow_network,
            ),
            source_metadata=train_dev_source,
        )
        return 0
    input_path = args.input or args.train
    if not input_path:
        raise SystemExit("--input or --train is required")
    samples = read_jsonl(input_path, scheme=args.scheme)
    if args.validate_only or args.prepare_dir:
        if args.prepare_dir:
            splits = prepare_document_splits(
                samples,
                output_dir=args.prepare_dir,
                scheme=args.scheme,
                seed=args.seed,
                augmentation_copies=args.augmentation_copies,
            )
            summary = {
                "train": len(splits.train),
                "validation": len(splits.validation),
                "test": len(splits.test),
            }
        else:
            summary = validate_dataset(samples, scheme=args.scheme)
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0

    if not args.model:
        raise SystemExit("--model is required unless --validate-only or --prepare-dir is used")
    validation = read_jsonl(args.validation, scheme=args.scheme) if args.validation else []
    train_token_classifier(
        samples,
        validation,
        config=TrainingConfig(
            model_name_or_path=args.model,
            output_dir=args.output_dir,
            scheme=args.scheme,
            max_length=args.max_length,
            epochs=args.epochs,
            train_batch_size=args.train_batch_size,
            eval_batch_size=args.eval_batch_size,
            learning_rate=args.learning_rate,
            weight_decay=args.weight_decay,
            logging_steps=args.logging_steps,
            save_total_limit=args.save_total_limit,
            warmup_ratio=args.warmup_ratio,
            save_strategy=args.save_strategy,
            evaluation_strategy=args.evaluation_strategy,
            seed=args.seed,
            fp16=args.fp16,
            gradient_accumulation_steps=args.gradient_accumulation_steps,
            overlap=args.overlap,
            resume_from_checkpoint=args.resume_from_checkpoint,
            local_files_only=not args.allow_network,
        ),
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the CLI
    raise SystemExit(main())
