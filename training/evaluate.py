"""Dependency-free exact-span evaluation helpers."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .types import EntityAnnotation, TrainingSample


@dataclass(frozen=True, slots=True)
class EntityMetrics:
    entity_type: str
    true_positive: int
    false_positive: int
    false_negative: int

    @property
    def precision(self) -> float:
        denominator = self.true_positive + self.false_positive
        return self.true_positive / denominator if denominator else 0.0

    @property
    def recall(self) -> float:
        denominator = self.true_positive + self.false_negative
        return self.true_positive / denominator if denominator else 0.0

    @property
    def f1(self) -> float:
        denominator = self.precision + self.recall
        return 2 * self.precision * self.recall / denominator if denominator else 0.0


def _key(span: EntityAnnotation) -> tuple[int, int, str]:
    return span.start, span.end, span.entity_type


def exact_span_metrics(
    expected: Iterable[TrainingSample],
    predicted: Iterable[TrainingSample],
) -> dict[str, EntityMetrics]:
    """Compute exact ``(start, end, entity_type)`` metrics by document id."""

    expected_by_id = {sample.document_id: sample for sample in expected}
    predicted_by_id = {sample.document_id: sample for sample in predicted}
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for document_id in set(expected_by_id) | set(predicted_by_id):
        expected_keys = {_key(span) for span in expected_by_id.get(document_id, TrainingSample(document_id, "")).spans}
        predicted_keys = {_key(span) for span in predicted_by_id.get(document_id, TrainingSample(document_id, "")).spans}
        for _, _, entity_type in expected_keys & predicted_keys:
            counts[entity_type][0] += 1
        for _, _, entity_type in predicted_keys - expected_keys:
            counts[entity_type][1] += 1
        for _, _, entity_type in expected_keys - predicted_keys:
            counts[entity_type][2] += 1
    return {
        entity_type: EntityMetrics(entity_type, *values)
        for entity_type, values in sorted(counts.items())
    }
