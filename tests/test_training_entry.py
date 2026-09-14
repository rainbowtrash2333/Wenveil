import json
from pathlib import Path

import pytest

from desensitize.config import load_config
from desensitize.models import Span
from training.train_token_classifier import (
    FeatureAlignmentError,
    TrainingConfig,
    align_offsets_to_labels,
    label_vocabulary,
    prepare_training_rows,
    validate_dataset,
)
from training.benchmark import build_benchmark_samples
from training.build_dataset import write_jsonl
from training.download_qwen import (
    DEFAULT_OUTPUT,
    DEFAULT_REPOSITORY,
    main as download_qwen_main,
)
from training.evaluate import EntityMetrics, metrics_report
from training.samples import sample_from_spans


class FakeFastTokenizer:
    def __call__(self, text, **kwargs):
        return {
            "input_ids": list(range(len(text) + 2)),
            "attention_mask": [1] * (len(text) + 2),
            "offset_mapping": [(0, 0)] + [(index, index + 1) for index in range(len(text))] + [(0, 0)],
        }


class OverflowFastTokenizer:
    def __call__(self, text, **kwargs):
        return {
            "input_ids": [[0, 1, 2, 3, 0], [0, 2, 3, 4, 0]],
            "attention_mask": [[1, 1, 1, 1, 1], [1, 1, 1, 1, 1]],
            "offset_mapping": [
                [(0, 0), (0, 1), (1, 2), (2, 3), (0, 0)],
                [(0, 0), (1, 2), (2, 3), (3, 4), (0, 0)],
            ],
            "overflow_to_sample_mapping": [0, 0],
        }


def test_training_entry_aligns_offsets_without_optional_dependencies():
    labels = align_offsets_to_labels(
        [(0, 0), (0, 1), (1, 2), (2, 4), (0, 0)],
        [Span(0, 2, "PERSON", "张三"), Span(2, 4, "ORG", "中国")],
        text="张三中国",
        scheme="BILOU",
    )
    assert labels == [
        -100,
        "B-PERSON",
        "L-PERSON",
        "U-ORG",
        -100,
    ]
    bioes_labels = align_offsets_to_labels(
        [(0, 0), (0, 1), (1, 2), (0, 0)],
        [Span(0, 2, "PERSON", "张三")],
        text="张三",
        scheme="BIOES",
    )
    assert bioes_labels == [-100, "B-PERSON", "E-PERSON", -100]
    rows = prepare_training_rows(
        [sample_from_spans("张三", [Span(0, 2, "PERSON", "张三")])],
        FakeFastTokenizer(),
    )
    assert rows[0]["labels"][1:3] == ["B-PERSON", "I-PERSON"]

    # Token labels may have fewer units than characters when offsets are
    # supplied; special-token offsets are intentionally excluded from text.
    assert validate_dataset([sample_from_spans("张三", [Span(0, 2, "PERSON", "张三")])])


def test_training_metadata_and_alignment_fail_loudly():
    assert label_vocabulary(["ORG", "PERSON"], "BIO") == [
        "O",
        "B-ORG",
        "I-ORG",
        "B-PERSON",
        "I-PERSON",
    ]
    sample = sample_from_spans("张三", [Span(0, 2, "PERSON", "张三")])
    assert validate_dataset([sample])["entities"] == {"PERSON": 1}
    with pytest.raises(FeatureAlignmentError):
        align_offsets_to_labels(
            [(0, 0), (0, 1), (0, 0)],
            sample.spans,
            text=sample.text,
        )


def test_overflow_windows_ignore_partial_edges_and_cover_full_span():
    sample = sample_from_spans(
        "甲乙丙丁",
        [Span(1, 4, "PROJECT", "乙丙丁")],
        document_id="long-doc",
    )
    rows = prepare_training_rows(
        [sample],
        OverflowFastTokenizer(),
        max_length=5,
        stride=2,
    )
    assert len(rows) == 2
    assert rows[0]["labels"][1:4] == ["O", -100, -100]
    assert rows[1]["labels"][1:4] == ["B-PROJECT", "I-PROJECT", "I-PROJECT"]


def test_training_config_validates_without_loading_model_weights(tmp_path: Path):
    config = TrainingConfig(model_name_or_path=str(tmp_path / "missing-model"))
    config.validate()


def test_evaluation_report_includes_per_entity_and_error_rates():
    report = metrics_report(
        {
            "PERSON": EntityMetrics("PERSON", 3, 1, 2),
        }
    )
    assert report["overall"]["precision"] == 0.75
    assert report["overall"]["recall"] == 0.6
    assert report["overall"]["miss_rate"] == 0.4
    assert report["per_entity"]["PERSON"]["false_detection_rate"] == 0.25


def test_independent_benchmark_declares_all_required_subsets():
    samples = build_benchmark_samples()
    assert {sample.metadata["evaluation_group"] for sample in samples} == {
        "unseen_entity",
        "unseen_alias",
        "similar_name",
        "ocr_noise",
        "long_text",
        "no_entity",
        "hard_negative",
    }


def test_validate_only_cli_path_never_needs_model_weights(tmp_path: Path, capsys):
    path = tmp_path / "input.jsonl"
    write_jsonl(path, [sample_from_spans("张三", [Span(0, 2, "PERSON", "张三")])])
    from training.train_token_classifier import main

    assert main(["--input", str(path), "--validate-only"]) == 0
    assert '"samples": 1' in capsys.readouterr().out


def test_download_verify_only_writes_safe_local_manifest(tmp_path: Path, capsys):
    for name in ("config.json", "tokenizer.json", "tokenizer_config.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    (tmp_path / "model.safetensors").write_bytes(b"synthetic-weight")

    assert download_qwen_main(["--output-dir", str(tmp_path), "--verify-only"]) == 0
    manifest = json.loads((tmp_path / "download_manifest.json").read_text(encoding="utf-8"))
    assert manifest["repository"] == "local"
    assert "model_family" in capsys.readouterr().out


def test_qwen_download_defaults_match_disabled_onnx_configs():
    project_config = load_config("config/default.yaml")
    package_config = load_config("desensitize/config/default.yaml")

    assert DEFAULT_REPOSITORY == "Qwen/Qwen3.5-2B"
    assert DEFAULT_OUTPUT == Path("models/qwen3.5-2b-base")
    assert project_config.model["path"].endswith("qwen3.5-2b-ner-onnx")
    assert package_config.model["path"].endswith("qwen3.5-2b-ner-onnx")
    assert project_config.model["enabled"] is False
    assert package_config.model["enabled"] is False
    assert project_config.model["backend"] == package_config.model["backend"] == "onnx"
