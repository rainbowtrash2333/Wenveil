"""Deterministic document-level dataset splitting."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import random
from typing import Iterator

from .types import TrainingSample


@dataclass(frozen=True, slots=True)
class DatasetSplits:
    train: tuple[TrainingSample, ...]
    validation: tuple[TrainingSample, ...]
    test: tuple[TrainingSample, ...]

    @property
    def val(self) -> tuple[TrainingSample, ...]:
        return self.validation

    def as_dict(self) -> dict[str, tuple[TrainingSample, ...]]:
        return {"train": self.train, "validation": self.validation, "test": self.test}

    def __getitem__(self, name: str) -> tuple[TrainingSample, ...]:
        if name == "val":
            name = "validation"
        return self.as_dict()[name]

    def __iter__(self) -> Iterator[tuple[TrainingSample, ...]]:
        yield self.train
        yield self.validation
        yield self.test


def _validate_ratios(train_ratio: float, validation_ratio: float, test_ratio: float) -> tuple[float, ...]:
    ratios = (float(train_ratio), float(validation_ratio), float(test_ratio))
    if any(ratio < 0 for ratio in ratios):
        raise ValueError("split ratios cannot be negative")
    if sum(ratios) <= 0:
        raise ValueError("at least one split ratio must be positive")
    total = sum(ratios)
    return tuple(ratio / total for ratio in ratios)


def _largest_remainder_counts(number: int, ratios: tuple[float, ...]) -> list[int]:
    raw = [number * ratio for ratio in ratios]
    counts = [int(value) for value in raw]
    remaining = number - sum(counts)
    order = sorted(range(len(raw)), key=lambda index: (raw[index] - counts[index], -index), reverse=True)
    for index in order[:remaining]:
        counts[index] += 1
    return counts


def _document_group(sample: TrainingSample) -> str:
    """Return the source-document key shared by chunks and augmentations."""

    value = sample.metadata.get("source_document_id", sample.metadata.get("document_group_id"))
    return str(value) if value is not None else sample.document_id


def split_by_document(
    samples: Iterable[TrainingSample],
    *,
    train_ratio: float = 0.70,
    validation_ratio: float = 0.15,
    test_ratio: float = 0.15,
    val_ratio: float | None = None,
    seed: int = 42,
    shuffle: bool = True,
) -> DatasetSplits:
    """Split complete document groups; chunks from one document never leak."""

    if val_ratio is not None:
        validation_ratio = val_ratio
    ratios = _validate_ratios(train_ratio, validation_ratio, test_ratio)
    sample_list = list(samples)
    for sample in sample_list:
        if not isinstance(sample, TrainingSample):
            raise TypeError("split_by_document expects TrainingSample values")
        sample.validate()

    by_document: dict[str, list[TrainingSample]] = defaultdict(list)
    document_order: list[str] = []
    for sample in sample_list:
        group = _document_group(sample)
        if group not in by_document:
            document_order.append(group)
        by_document[group].append(sample)

    if shuffle:
        random.Random(seed).shuffle(document_order)
    counts = _largest_remainder_counts(len(document_order), ratios)
    boundaries = (counts[0], counts[0] + counts[1])
    train_ids = set(document_order[: boundaries[0]])
    validation_ids = set(document_order[boundaries[0] : boundaries[1]])
    test_ids = set(document_order[boundaries[1] :])

    output: dict[str, list[TrainingSample]] = {"train": [], "validation": [], "test": []}
    for sample in sample_list:
        group = _document_group(sample)
        if group in train_ids:
            output["train"].append(sample)
        elif group in validation_ids:
            output["validation"].append(sample)
        elif group in test_ids:
            output["test"].append(sample)
        else:
            raise RuntimeError(f"document group {group!r} was not assigned to a split")
    return DatasetSplits(tuple(output["train"]), tuple(output["validation"]), tuple(output["test"]))


def split_documents(
    samples: Iterable[TrainingSample],
    *,
    train_ratio: float = 0.70,
    val_ratio: float = 0.15,
    test_ratio: float = 0.15,
    seed: int = 42,
    shuffle: bool = True,
) -> DatasetSplits:
    return split_by_document(
        samples,
        train_ratio=train_ratio,
        validation_ratio=val_ratio,
        test_ratio=test_ratio,
        seed=seed,
        shuffle=shuffle,
    )
