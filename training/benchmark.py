"""Small deterministic, non-user benchmark for independent NER checks."""

from __future__ import annotations

import argparse

from .samples import sample_from_spans
from .types import TrainingSample


def _marked_sample(
    document_id: str,
    text: str,
    surface: str | None,
    entity_type: str | None,
    group: str,
) -> TrainingSample:
    spans = ()
    if surface is not None and entity_type is not None:
        start = text.index(surface)
        spans = ((start, start + len(surface), entity_type),)
    return sample_from_spans(
        text,
        spans,
        document_id=document_id,
        metadata={"evaluation_group": group, "synthetic": True},
    )


def build_benchmark_samples() -> list[TrainingSample]:
    """Return safe fixtures covering the required independent subsets.

    These examples are deliberately synthetic and are intended to validate
    metric plumbing and window behavior, not to represent production quality.
    """

    long_text = "风险说明与投资限制。" * 45
    long_text += "澄明资产管理有限公司"
    long_text += "。后续条款继续说明。" * 8
    return [
        _marked_sample(
            "benchmark-unseen-entity",
            "曜石资本管理有限公司负责本次投资。",
            "曜石资本管理有限公司",
            "ORG_FULL",
            "unseen_entity",
        ),
        _marked_sample(
            "benchmark-unseen-alias",
            "以下简称“星澈资管”的机构负责复核。",
            "星澈资管",
            "ORG_ALIAS",
            "unseen_alias",
        ),
        _marked_sample(
            "benchmark-similar-name",
            "曜石基金与曜石基金管理有限公司签署协议。",
            "曜石基金管理有限公司",
            "ORG_FULL",
            "similar_name",
        ),
        _marked_sample(
            "benchmark-ocr-noise",
            "曜 石 基 金 管 理 有 限 公 司 负责复核。",
            "曜 石 基 金 管 理 有 限 公 司",
            "ORG_FULL",
            "ocr_noise",
        ),
        _marked_sample(
            "benchmark-long-text",
            long_text,
            "澄明资产管理有限公司",
            "ORG_FULL",
            "long_text",
        ),
        _marked_sample(
            "benchmark-no-entity",
            "本段只描述投资流程、期限和风险，不包含机构名称。",
            None,
            None,
            "no_entity",
        ),
        _marked_sample(
            "benchmark-hard-negative",
            "本项目不涉及任何公司名称，也没有机构简称。",
            None,
            None,
            "hard_negative",
        ),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="write the safe independent NER benchmark")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    from .build_dataset import write_jsonl

    print(write_jsonl(args.output, build_benchmark_samples()))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI exercised separately
    raise SystemExit(main())
