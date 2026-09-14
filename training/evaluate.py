"""Dependency-free exact-span evaluation helpers."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import argparse
import json
from pathlib import Path
from typing import Any

from .types import EntityAnnotation, TrainingSample
from .external_dataset import FIXED_SPLITS, read_external_splits


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


def _index_samples(
    samples: Iterable[TrainingSample],
    *,
    role: str,
) -> dict[str, TrainingSample]:
    indexed: dict[str, TrainingSample] = {}
    for sample in samples:
        if sample.document_id in indexed:
            raise ValueError(f"duplicate {role} document_id")
        indexed[sample.document_id] = sample
    return indexed


def _empty_sample(document_id: str) -> TrainingSample:
    return TrainingSample(document_id, "")


def exact_span_metrics(
    expected: Iterable[TrainingSample],
    predicted: Iterable[TrainingSample],
) -> dict[str, EntityMetrics]:
    """Compute exact ``(start, end, entity_type)`` metrics by document id."""

    expected_by_id = _index_samples(expected, role="expected")
    predicted_by_id = _index_samples(predicted, role="predicted")
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for document_id in set(expected_by_id) | set(predicted_by_id):
        expected_keys = {
            _key(span)
            for span in expected_by_id.get(document_id, _empty_sample(document_id)).spans
        }
        predicted_keys = {
            _key(span)
            for span in predicted_by_id.get(document_id, _empty_sample(document_id)).spans
        }
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


def metrics_report(metrics: Mapping[str, EntityMetrics]) -> dict[str, Any]:
    """Serialize exact-span metrics with miss and false-detection rates."""

    true_positive = sum(item.true_positive for item in metrics.values())
    false_positive = sum(item.false_positive for item in metrics.values())
    false_negative = sum(item.false_negative for item in metrics.values())

    def rates(tp: int, fp: int, fn: int) -> dict[str, Any]:
        precision_denominator = tp + fp
        recall_denominator = tp + fn
        precision = tp / precision_denominator if precision_denominator else 0.0
        recall = tp / recall_denominator if recall_denominator else 0.0
        f1_denominator = precision + recall
        return {
            "true_positive": tp,
            "false_positive": fp,
            "false_negative": fn,
            "precision": precision,
            "recall": recall,
            "f1": 2 * precision * recall / f1_denominator if f1_denominator else 0.0,
            "miss_rate": fn / recall_denominator if recall_denominator else 0.0,
            "false_detection_rate": fp / precision_denominator if precision_denominator else 0.0,
        }

    return {
        "overall": rates(true_positive, false_positive, false_negative),
        "per_entity": {
            entity_type: rates(
                item.true_positive,
                item.false_positive,
                item.false_negative,
            )
            for entity_type, item in sorted(metrics.items())
        },
    }


def subset_metrics(
    expected: Iterable[TrainingSample],
    predicted: Iterable[TrainingSample],
    *,
    metadata_key: str = "evaluation_group",
) -> dict[str, dict[str, Any]]:
    """Evaluate subsets and report predicted-only docs in ``extra_predictions``."""

    expected_list = list(expected)
    expected_by_id = _index_samples(expected_list, role="expected")
    predicted_by_id = _index_samples(predicted, role="predicted")
    groups = sorted(
        {
            str(sample.metadata[metadata_key])
            for sample in expected_list
            if sample.metadata.get(metadata_key)
        }
    )
    result: dict[str, dict[str, Any]] = {}
    for group in groups:
        group_expected = [
            sample for sample in expected_list if str(sample.metadata.get(metadata_key)) == group
        ]
        group_predicted = [
            predicted_by_id.get(
                sample.document_id,
                _empty_sample(sample.document_id),
            )
            for sample in group_expected
        ]
        report = metrics_report(exact_span_metrics(group_expected, group_predicted))
        report["samples"] = len(group_expected)
        result[group] = report
    extra_predictions = [
        sample
        for document_id, sample in predicted_by_id.items()
        if document_id not in expected_by_id
    ]
    if extra_predictions:
        extra_report = metrics_report(exact_span_metrics([], extra_predictions))
        extra_report["samples"] = 0
        extra_report["predicted_samples"] = len(extra_predictions)
        result["extra_predictions"] = extra_report
    return result


def evaluate_model_checkpoint(
    samples: Iterable[TrainingSample],
    *,
    model_path: str | Path,
    max_length: int = 1024,
    overlap: int = 128,
    thresholds: Mapping[str, float] | None = None,
    device: str = "cpu",
    local_files_only: bool = True,
) -> dict[str, Any]:
    """Run independent exact-span evaluation from a local best checkpoint."""

    try:
        import torch
        import transformers
    except ImportError as exc:  # pragma: no cover - optional dependency path
        raise RuntimeError("model evaluation requires local torch and transformers") from exc
    from .model_prediction import predict_samples
    from .qwen35_token_classifier import load_token_classifier_checkpoint

    sample_list = list(samples)
    tokenizer = transformers.AutoTokenizer.from_pretrained(
        model_path,
        use_fast=True,
        local_files_only=local_files_only,
    )
    model = load_token_classifier_checkpoint(
        model_path,
        torch=torch,
        transformers=transformers,
        local_files_only=local_files_only,
    )
    model.to(device)
    predicted = predict_samples(
        sample_list,
        tokenizer,
        model,
        torch,
        max_length=max_length,
        overlap=overlap,
        thresholds=thresholds,
    )
    report = metrics_report(exact_span_metrics(sample_list, predicted))
    report["samples"] = len(sample_list)
    report["subsets"] = subset_metrics(sample_list, predicted)
    return report


def evaluate_external_splits(
    data_dir: str | Path,
    *,
    split: str,
    model_path: str | Path,
    max_length: int = 1024,
    overlap: int = 128,
    thresholds: Mapping[str, float] | None = None,
    device: str = "cpu",
    local_files_only: bool = True,
) -> dict[str, Any]:
    """Evaluate one fixed external split, or each requested split separately."""

    if split != "all" and split not in FIXED_SPLITS[1:]:
        raise ValueError("split must be dev, test, hard_test, or all")
    requested = FIXED_SPLITS[1:] if split == "all" else (split,)
    # The train split is only a provenance/isolation reference.  It is never
    # passed to model evaluation or included in any metric report.
    validation_splits = ("train", *requested)
    loaded, summaries = read_external_splits(data_dir, validation_splits)
    result: dict[str, Any] = {}
    for split_name in requested:
        samples = loaded[split_name]
        source = summaries[split_name]
        report = evaluate_model_checkpoint(
            samples,
            model_path=model_path,
            max_length=max_length,
            overlap=overlap,
            thresholds=thresholds,
            device=device,
            local_files_only=local_files_only,
        )
        report["split"] = split_name
        report["source"] = {
            "kind": "external_fixed_split",
            "sha256": source["sha256"],
            "rows": source["rows"],
            "labels": source["labels"],
        }
        result[split_name] = report
    return result if split == "all" else result[requested[0]]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate a local Qwen3.5 NER checkpoint")
    parser.add_argument("--input", help="legacy JSONL with lossless spans/labels")
    parser.add_argument(
        "--data-dir",
        default="data",
        help="fixed external data directory containing train/dev/test/hard_test JSONL",
    )
    parser.add_argument(
        "--split",
        choices=("dev", "test", "hard_test", "all"),
        default="test",
        help="external split to evaluate independently",
    )
    parser.add_argument("--model", required=True, help="local best checkpoint directory")
    parser.add_argument("--scheme", choices=("BIO", "BIOES", "BILOU"), default="BIO")
    parser.add_argument("--max-length", type=int, default=1024)
    parser.add_argument("--overlap", type=int, default=128)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--output", help="optional JSON report path")
    parser.add_argument("--allow-network", action="store_true")
    args = parser.parse_args(argv)
    if args.input:
        from .build_dataset import read_jsonl

        samples = read_jsonl(args.input, scheme=args.scheme)
        report = evaluate_model_checkpoint(
            samples,
            model_path=args.model,
            max_length=args.max_length,
            overlap=args.overlap,
            device=args.device,
            local_files_only=not args.allow_network,
        )
    else:
        report = evaluate_external_splits(
            args.data_dir,
            split=args.split,
            model_path=args.model,
            max_length=args.max_length,
            overlap=args.overlap,
            device=args.device,
            local_files_only=not args.allow_network,
        )
    encoded = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI exercised separately
    raise SystemExit(main())
