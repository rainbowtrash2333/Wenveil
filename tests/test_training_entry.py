from pathlib import Path

import pytest

from desensitize.models import Span
from training.train_token_classifier import (
    FeatureAlignmentError,
    TrainingConfig,
    align_offsets_to_labels,
    label_vocabulary,
    prepare_training_rows,
    validate_dataset,
)
from training.build_dataset import write_jsonl
from training.samples import sample_from_spans


class FakeFastTokenizer:
    def __call__(self, text, **kwargs):
        return {
            "input_ids": list(range(len(text) + 2)),
            "attention_mask": [1] * (len(text) + 2),
            "offset_mapping": [(0, 0)] + [(index, index + 1) for index in range(len(text))] + [(0, 0)],
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


def test_training_config_validates_without_loading_model_weights(tmp_path: Path):
    config = TrainingConfig(model_name_or_path=str(tmp_path / "missing-model"))
    config.validate()


def test_validate_only_cli_path_never_needs_model_weights(tmp_path: Path, capsys):
    path = tmp_path / "input.jsonl"
    write_jsonl(path, [sample_from_spans("张三", [Span(0, 2, "PERSON", "张三")])])
    from training.train_token_classifier import main

    assert main(["--input", str(path), "--validate-only"]) == 0
    assert '"samples": 1' in capsys.readouterr().out
