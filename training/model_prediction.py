"""Shared local model prediction helpers for evaluation and export checks."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from desensitize.models import Span
from desensitize.recognizers.model_ner import ModelNERRecognizer

from .types import EntityAnnotation, TrainingSample


def _windows(tokenizer: Any, text: str, *, max_length: int, overlap: int) -> list[dict[str, Any]]:
    decoder = ModelNERRecognizer({"chunk_size": max_length, "overlap": overlap})
    decoder._tokenizer = tokenizer
    return decoder._tokenize(text)


def _label_map(model: Any) -> dict[int, str]:
    raw = getattr(getattr(model, "config", None), "id2label", {}) or {}
    return {int(index): str(label) for index, label in raw.items()}


def predict_spans(
    text: str,
    tokenizer: Any,
    model: Any,
    torch: Any,
    *,
    max_length: int = 1024,
    overlap: int = 128,
    thresholds: Mapping[str, float] | None = None,
) -> list[Span]:
    """Run a local token classifier and return immutable candidate spans."""

    if overlap < 0 or overlap >= max_length:
        raise ValueError("overlap must satisfy 0 <= overlap < max_length")
    decoder = ModelNERRecognizer(
        {
            "thresholds": dict(thresholds or {}),
            "preserve_label_types": True,
        }
    )
    decoder._torch = torch
    decoder._model = model
    decoder._id2label = _label_map(model)
    model.eval()
    candidates: dict[tuple[int, int, str], Span] = {}
    for chunk in _windows(tokenizer, text, max_length=max_length, overlap=overlap):
        prediction = decoder._predict_chunk(text, chunk)
        for span in prediction:
            key = (span.start, span.end, span.entity_type)
            previous = candidates.get(key)
            if previous is None or span.score > previous.score:
                candidates[key] = span
    return _merge_window_spans(candidates.values())


def _merge_window_spans(spans: Iterable[Span]) -> list[Span]:
    """Prefer a complete span when overlapping windows emit partial spans."""

    ordered = sorted(
        spans,
        key=lambda span: (
            span.start,
            span.end,
            span.entity_type,
        ),
    )
    result: list[Span] = []
    for span in sorted(ordered, key=lambda item: (-item.length, -item.score, item.start)):
        overlaps = [item for item in result if item.entity_type == span.entity_type and item.overlaps(span)]
        if any(item.start <= span.start and span.end <= item.end for item in overlaps):
            continue
        result = [
            item
            for item in result
            if not (item.start <= span.start and span.end <= item.end)
        ]
        result.append(span)
    return sorted(result, key=lambda span: (span.start, span.end, span.entity_type))


def predict_samples(
    samples: Iterable[TrainingSample],
    tokenizer: Any,
    model: Any,
    torch: Any,
    *,
    max_length: int = 1024,
    overlap: int = 128,
    thresholds: Mapping[str, float] | None = None,
) -> list[TrainingSample]:
    result: list[TrainingSample] = []
    for sample in samples:
        spans = predict_spans(
            sample.text,
            tokenizer,
            model,
            torch,
            max_length=max_length,
            overlap=overlap,
            thresholds=thresholds,
        )
        annotations = tuple(
            EntityAnnotation(
                span.start,
                span.end,
                span.entity_type,
                span.surface,
                score=span.score,
                priority=span.priority,
                source=span.source,
                rule_id=span.rule_id,
            )
            for span in spans
        )
        result.append(TrainingSample(sample.document_id, sample.text, annotations, dict(sample.metadata)))
    return result
