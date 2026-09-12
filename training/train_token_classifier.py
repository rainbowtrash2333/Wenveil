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

from .build_dataset import read_jsonl, validate_jsonl, write_jsonl
from .labels import normalize_scheme, spans_to_labels
from .samples import chunk_sample
from .splitting import DatasetSplits, split_by_document
from .types import EntityAnnotation, TrainingSample


class OptionalTrainingDependencyError(RuntimeError):
    """Raised when actual model training is requested without optional deps."""


class FeatureAlignmentError(ValueError):
    """Raised when tokenizer offsets cannot represent an annotated span."""


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
    seed: int = 42
    local_files_only: bool = True
    fp16: bool = False
    gradient_accumulation_steps: int = 1

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


def label_vocabulary(entity_types: Iterable[str], scheme: str = "BIO") -> list[str]:
    normalized = normalize_scheme(scheme)
    types = sorted({str(entity_type) for entity_type in entity_types if str(entity_type)})
    labels = ["O"]
    prefixes = ("B", "I") if normalized == "BIO" else ("B", "I", "L", "U")
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
) -> list[str | int]:
    """Align fast-tokenizer offsets to flat entity spans.

    Special tokens conventionally have ``(0, 0)`` offsets and receive -100.
    Every annotated span must overlap at least one encoded token when
    ``strict`` is true; this prevents silent labels disappearing through
    truncation.
    """

    normalized = normalize_scheme(scheme)
    annotations = [EntityAnnotation.from_value(span, text) for span in spans]
    annotations.sort(key=lambda span: (span.start, span.end))
    previous_end = -1
    for span in annotations:
        span.validate_against(text)
        if span.start < previous_end:
            raise FeatureAlignmentError("overlapping spans cannot be token-aligned")
        previous_end = span.end

    token_indices: list[list[int]] = [[] for _ in annotations]
    labels: list[str | int] = ["O"] * len(offsets)
    for token_index, (start, end) in enumerate(offsets):
        start, end = int(start), int(end)
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
        elif len(indices) == 1:
            labels[indices[0]] = f"U-{span.entity_type}"
        else:
            labels[indices[0]] = f"B-{span.entity_type}"
            for token_index in indices[1:-1]:
                labels[token_index] = f"I-{span.entity_type}"
            labels[indices[-1]] = f"L-{span.entity_type}"
    return labels


def _tokenizer_output(tokenizer: Any, text: str, *, max_length: int) -> Mapping[str, Any]:
    try:
        output = tokenizer(
            text,
            return_offsets_mapping=True,
            truncation=True,
            max_length=max_length,
            padding=False,
        )
    except TypeError as exc:
        raise FeatureAlignmentError(
            "tokenizer must be a fast tokenizer supporting return_offsets_mapping"
        ) from exc
    if "offset_mapping" not in output:
        raise FeatureAlignmentError("tokenizer output has no offset_mapping")
    return output


def prepare_training_rows(
    samples: Iterable[TrainingSample],
    tokenizer: Any,
    *,
    scheme: str = "BIO",
    max_length: int = 1024,
    strict_alignment: bool = True,
) -> list[dict[str, Any]]:
    """Tokenize samples and attach aligned labels without importing torch."""

    normalized = normalize_scheme(scheme)
    rows: list[dict[str, Any]] = []
    for sample in samples:
        sample.validate()
        output = dict(_tokenizer_output(tokenizer, sample.text, max_length=max_length))
        offsets = [tuple(item) for item in output.pop("offset_mapping")]
        output["labels"] = align_offsets_to_labels(
            offsets,
            sample.spans,
            text=sample.text,
            scheme=normalized,
            strict=strict_alignment,
        )
        output["document_id"] = sample.document_id
        rows.append(output)
    return rows


class ListDataset:
    """Small list-backed dataset understood by Transformers Trainer."""

    def __init__(self, rows: Sequence[Mapping[str, Any]]):
        self._rows = [dict(row) for row in rows]

    def __len__(self) -> int:
        return len(self._rows)

    def __getitem__(self, index: int) -> dict[str, Any]:
        row = dict(self._rows[index])
        row.pop("document_id", None)
        return row


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
) -> Any:
    """Train a Qwen-compatible token classifier when optional deps are present."""

    config.validate()
    train_list = list(train_samples)
    validation_list = list(validation_samples)
    if not train_list:
        raise ValueError("at least one training sample is required")
    validate_dataset(train_list, scheme=config.scheme)
    validate_dataset(validation_list, scheme=config.scheme)

    _, transformers = _import_training_dependencies()
    try:
        tokenizer = transformers.AutoTokenizer.from_pretrained(
            config.model_name_or_path,
            use_fast=True,
            local_files_only=config.local_files_only,
        )
    except Exception as exc:
        mode = "local" if config.local_files_only else "configured"
        raise RuntimeError(f"could not load {mode} tokenizer: {config.model_name_or_path}") from exc

    entity_types = entity_types_from_samples([*train_list, *validation_list])
    labels = label_vocabulary(entity_types, config.scheme)
    label_to_id = {label: index for index, label in enumerate(labels)}
    id_to_label = {index: label for label, index in label_to_id.items()}
    train_rows = prepare_training_rows(
        train_list,
        tokenizer,
        scheme=config.scheme,
        max_length=config.max_length,
    )
    validation_rows = prepare_training_rows(
        validation_list,
        tokenizer,
        scheme=config.scheme,
        max_length=config.max_length,
    )
    for row in [*train_rows, *validation_rows]:
        row["labels"] = [
            special if isinstance(special, int) else label_to_id[special]
            for special in row["labels"]
        ]

    try:
        model = transformers.AutoModelForTokenClassification.from_pretrained(
            config.model_name_or_path,
            num_labels=len(labels),
            id2label=id_to_label,
            label2id=label_to_id,
            local_files_only=config.local_files_only,
            ignore_mismatched_sizes=True,
        )
    except Exception as exc:
        raise RuntimeError(f"could not load token-classification model: {config.model_name_or_path}") from exc

    argument_kwargs: dict[str, Any] = {
        "output_dir": config.output_dir,
        "num_train_epochs": config.epochs,
        "per_device_train_batch_size": config.train_batch_size,
        "per_device_eval_batch_size": config.eval_batch_size,
        "learning_rate": config.learning_rate,
        "weight_decay": config.weight_decay,
        "logging_steps": config.logging_steps,
        "save_total_limit": config.save_total_limit,
        "seed": config.seed,
        "fp16": config.fp16,
        "gradient_accumulation_steps": config.gradient_accumulation_steps,
        "report_to": [],
        "remove_unused_columns": False,
    }
    argument_parameters = inspect.signature(transformers.TrainingArguments.__init__).parameters
    if "eval_strategy" in argument_parameters:
        argument_kwargs["eval_strategy"] = "epoch" if validation_rows else "no"
    elif "evaluation_strategy" in argument_parameters:
        argument_kwargs["evaluation_strategy"] = "epoch" if validation_rows else "no"
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
    trainer = transformers.Trainer(**trainer_kwargs)
    trainer.train()
    trainer.save_model(config.output_dir)
    tokenizer.save_pretrained(config.output_dir)
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
    parser.add_argument("--validate-only", action="store_true", help="do not import training dependencies")
    parser.add_argument("--scheme", choices=("BIO", "BILOU"), default="BIO")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-length", type=int, default=1024)
    parser.add_argument("--augmentation-copies", type=int, default=0)
    parser.add_argument("--allow-network", action="store_true", help="allow Transformers cache/network lookup")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
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
            seed=args.seed,
            local_files_only=not args.allow_network,
        ),
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the CLI
    raise SystemExit(main())
