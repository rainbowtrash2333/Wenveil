from __future__ import annotations

import json
from pathlib import Path

import pytest

from training.external_dataset import (
    ExternalDatasetError,
    read_external_split,
    read_external_splits,
    validate_external_dataset,
)
from training.train_token_classifier import (
    FeatureAlignmentError,
    ListDataset,
    TrainingConfig,
    _build_training_metadata,
    _persist_checkpoint_artifacts,
    _safe_source_metadata,
    _validate_resume_compatibility,
    prepare_training_rows,
    validate_dataset,
)
from training.samples import sample_from_spans
from desensitize.models import Span
from desensitize.recognizers.model_ner import ModelNERRecognizer


def _record(
    document_id: str,
    *,
    text: str = "甲乙在丙",
    clean_text: str = "不同的规整文本",
    mention: str = "甲乙",
    start: int = 0,
    relation_target: str = "missing-id",
) -> dict:
    return {
        "clean_text": clean_text,
        "document_id": document_id,
        "entities": [
            {
                "start": start,
                "end": start + len(mention),
                "entity_id": "entity-1",
                "entity_type": "PERSON",
                "financial_subtype": "",
                "industry_subtype": "",
                "mention": mention,
            }
        ],
        "metadata": {"private": "must not be retained"},
        "relations": [
            {
                "relation_type": "RELATED_TO",
                "source_mention": mention,
                "target_entity_id": relation_target,
            }
        ],
        "text": text,
    }


def _write_split(data_dir: Path, split: str, records: list[dict]) -> None:
    (data_dir / f"{split}.jsonl").write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def test_external_adapter_uses_text_and_discards_non_ner_fields(tmp_path: Path):
    _write_split(tmp_path, "train", [_record("doc-1")])

    samples, summary = read_external_split(tmp_path, "train")

    assert samples[0].text == "甲乙在丙"
    assert samples[0].spans[0].surface == "甲乙"
    assert samples[0].metadata == {}
    assert samples[0].document_id != "doc-1"
    assert summary["split"] == "train"
    assert summary["rows"] == 1
    assert summary["entities"] == 1
    assert summary["labels"] == {"PERSON": 1}
    assert summary["relation_types"] == {"RELATED_TO": 1}
    encoded = json.dumps(summary, ensure_ascii=False)
    assert "private" not in encoded
    assert "doc-1" not in encoded


@pytest.mark.parametrize(
    "mutate",
    [
        lambda record: record["entities"][0].update(start=-1),
        lambda record: record["entities"][0].update(mention="不存在"),
        lambda record: record["entities"].append(dict(record["entities"][0], start=1, end=3)),
        lambda record: record["relations"][0].update(target_entity_id=3),
    ],
)
def test_external_adapter_rejects_invalid_spans_overlap_and_relation_types(
    tmp_path: Path,
    mutate,
):
    record = _record("doc-1")
    mutate(record)
    _write_split(tmp_path, "train", [record])

    with pytest.raises(ExternalDatasetError):
        read_external_split(tmp_path, "train")


def test_external_adapter_checks_fixed_split_isolation(tmp_path: Path):
    _write_split(tmp_path, "train", [_record("same-doc")])
    _write_split(tmp_path, "dev", [_record("same-doc")])
    _write_split(tmp_path, "test", [])
    _write_split(tmp_path, "hard_test", [])

    with pytest.raises(ExternalDatasetError, match="overlap"):
        read_external_splits(tmp_path)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda record: record.update(document_id="  "),
        lambda record: record["entities"][0].update(entity_type="  "),
        lambda record: record["relations"][0].update(relation_type="  "),
        lambda record: record["relations"][0].update(source_mention="  "),
        lambda record: record["relations"][0].update(target_entity_id="  "),
    ],
)
def test_external_adapter_rejects_blank_identifiers_and_relation_fields(
    tmp_path: Path,
    mutate,
):
    record = _record("doc-1")
    mutate(record)
    _write_split(tmp_path, "train", [record])

    with pytest.raises(ExternalDatasetError):
        read_external_split(tmp_path, "train")


@pytest.mark.parametrize(
    "entity_type",
    ["", "  ", "PERSON TYPE", "PERSON\n", "1PERSON", "PERSON\x00"],
)
def test_external_entity_type_uses_shared_label_grammar(
    tmp_path: Path,
    entity_type: str,
):
    record = _record("doc-1")
    record["entities"][0]["entity_type"] = entity_type
    _write_split(tmp_path, "train", [record])

    with pytest.raises(ExternalDatasetError):
        read_external_split(tmp_path, "train")


def test_shared_entity_type_rejection_matches_training_precheck(tmp_path: Path):
    record = _record("doc-1")
    record["entities"][0]["entity_type"] = "PERSON\x00"
    _write_split(tmp_path, "train", [record])
    with pytest.raises(ExternalDatasetError):
        read_external_split(tmp_path, "train")

    from training.types import EntityAnnotation, TrainingSample

    sample = TrainingSample(
        "safe-doc",
        "甲乙",
        (EntityAnnotation(0, 2, "PERSON\x00", "甲乙"),),
    )
    with pytest.raises(ValueError):
        validate_dataset([sample])


def test_external_adapter_rejects_unknown_fixed_schema_fields(tmp_path: Path):
    record = _record("doc-1")
    record["unsupported"] = "extension"
    _write_split(tmp_path, "train", [record])

    with pytest.raises(ExternalDatasetError, match="unsupported"):
        read_external_split(tmp_path, "train")


def test_external_adapter_hides_file_read_oserror(tmp_path: Path, monkeypatch):
    _write_split(tmp_path, "train", [_record("doc-1")])

    def fail_open(*args, **kwargs):
        raise OSError("local path must not escape")

    monkeypatch.setattr(Path, "open", fail_open)
    with pytest.raises(ExternalDatasetError) as error:
        read_external_split(tmp_path, "train")
    assert error.value.__cause__ is None
    assert "local path" not in str(error.value)


def test_external_validate_summary_is_safe_and_does_not_write_copies(tmp_path: Path):
    _write_split(tmp_path, "train", [_record("doc-1")])
    summary = validate_external_dataset(tmp_path, ("train",))

    assert set(summary) == {"splits"}
    assert summary["splits"]["train"]["sha256"]
    assert list(tmp_path.iterdir()) == [tmp_path / "train.jsonl"]


def test_fixed_split_validate_cli_reads_only_requested_split(tmp_path: Path, capsys):
    _write_split(tmp_path, "train", [_record("doc-1")])

    from scripts.validate_dataset import main

    assert main(["--data-dir", str(tmp_path), "--split", "train"]) == 0
    output = capsys.readouterr().out
    assert '"split": "train"' in output
    assert "doc-1" not in output


def test_external_training_validate_mode_reads_only_train_and_dev(
    tmp_path: Path,
    capsys,
):
    _write_split(tmp_path, "train", [_record("train-doc")])
    _write_split(tmp_path, "dev", [_record("dev-doc")])
    _write_split(tmp_path, "test", [])
    _write_split(tmp_path, "hard_test", [])

    from training.train_token_classifier import main

    assert main(["--data-dir", str(tmp_path), "--validate-only"]) == 0
    output = capsys.readouterr().out
    assert '"train"' in output
    assert '"dev"' in output
    assert '"test"' in output
    assert '"hard_test"' in output


def test_external_training_validates_all_but_passes_only_train_dev(
    tmp_path: Path,
    monkeypatch,
):
    for split in ("train", "dev", "test", "hard_test"):
        _write_split(tmp_path, split, [_record(f"{split}-doc")])

    from training import train_token_classifier as module

    seen = {}

    def fake_train(train_samples, validation_samples, *, config, source_metadata):
        seen["train"] = len(list(train_samples))
        seen["dev"] = len(list(validation_samples))
        seen["source_splits"] = source_metadata["splits"]

    monkeypatch.setattr(module, "train_token_classifier", fake_train)
    assert module.main(["--data-dir", str(tmp_path), "--model", "model"]) == 0
    assert seen == {"train": 1, "dev": 1, "source_splits": ["train", "dev"]}


def test_external_evaluation_all_keeps_reports_split_specific(tmp_path: Path, monkeypatch):
    _write_split(tmp_path, "train", [_record("train-doc")])
    for split in ("dev", "test", "hard_test"):
        _write_split(tmp_path, split, [_record(f"{split}-doc")])

    from training import evaluate

    seen: list[str] = []

    def fake_evaluate(samples, **kwargs):
        seen.append(samples[0].document_id)
        return {"overall": {"f1": 1.0}}

    monkeypatch.setattr(evaluate, "evaluate_model_checkpoint", fake_evaluate)
    report = evaluate.evaluate_external_splits(
        tmp_path,
        split="all",
        model_path="model",
    )

    assert set(report) == {"dev", "test", "hard_test"}
    assert [report[split]["split"] for split in ("dev", "test", "hard_test")] == [
        "dev",
        "test",
        "hard_test",
    ]
    assert len(set(seen)) == 3


def test_external_single_split_evaluation_checks_train_isolation(
    tmp_path: Path,
    monkeypatch,
):
    _write_split(tmp_path, "train", [_record("same-doc")])
    _write_split(tmp_path, "test", [_record("same-doc")])

    from training import evaluate

    monkeypatch.setattr(
        evaluate,
        "evaluate_model_checkpoint",
        lambda *args, **kwargs: {"overall": {"f1": 0.0}},
    )
    with pytest.raises(ExternalDatasetError, match="overlap"):
        evaluate.evaluate_external_splits(
            tmp_path,
            split="test",
            model_path="model",
        )


def test_trainer_dataset_exposes_only_native_model_features():
    row = {
        "input_ids": [1, 2],
        "attention_mask": [1, 1],
        "labels": [0, 1],
        "document_id": "safe-doc",
        "window_start": 0,
        "window_end": 2,
    }

    payload = ListDataset([row])[0]

    assert payload == {
        "input_ids": [1, 2],
        "attention_mask": [1, 1],
        "labels": [0, 1],
    }

    def native_like_forward(**kwargs):
        assert set(kwargs) == {"input_ids", "attention_mask", "labels"}

    native_like_forward(**payload)


def test_exact_metrics_treat_missing_predictions_as_empty_and_reject_duplicates():
    from training.evaluate import EntityMetrics, exact_span_metrics, subset_metrics

    expected = [
        sample_from_spans(
            "甲",
            [Span(0, 1, "PERSON", "甲")],
            document_id="doc-1",
            metadata={"evaluation_group": "hard"},
        )
    ]
    assert exact_span_metrics(expected, []) == {
        "PERSON": EntityMetrics("PERSON", 0, 0, 1),
    }
    assert subset_metrics(expected, {})["hard"]["overall"]["false_negative"] == 1
    with pytest.raises(ValueError, match="duplicate"):
        exact_span_metrics(expected + expected, [])

    extra = sample_from_spans(
        "乙",
        [Span(0, 1, "ORG", "乙")],
        document_id="extra-doc",
    )
    extra_report = subset_metrics(expected, [extra])["extra_predictions"]
    assert extra_report["overall"]["false_positive"] == 1
    assert extra_report["predicted_samples"] == 1


def test_training_metadata_source_and_resume_compatibility_are_whitelisted():
    source = {
        "kind": "external_fixed_splits",
        "splits": ["train", "dev"],
        "files": {
            "train": {"sha256": "a" * 64, "rows": 1, "labels": {"PERSON": 1}},
            "dev": {"sha256": "b" * 64, "rows": 1, "labels": {"PERSON": 1}},
        },
        "unexpected": "discard me",
    }
    assert "unexpected" not in _safe_source_metadata(source)
    with pytest.raises(ValueError, match="train and dev only"):
        _safe_source_metadata({**source, "splits": ["train", "dev", "test"]})

    config = TrainingConfig(model_name_or_path="model", max_length=128)
    with pytest.raises(ValueError, match="max_length"):
        _validate_resume_compatibility(
            {"config": {"scheme": "BIO", "max_length": 256, "overlap": 8}},
            config,
            ["O"],
        )


def test_external_resume_requires_and_compares_source_provenance(tmp_path: Path):
    source = {
        "kind": "external_fixed_splits",
        "splits": ["train", "dev"],
        "files": {
            "train": {"sha256": "a" * 64, "rows": 2, "labels": {"PERSON": 2}},
            "dev": {"sha256": "b" * 64, "rows": 1, "labels": {"PERSON": 1}},
        },
    }
    config = TrainingConfig(
        model_name_or_path="model",
        resume_from_checkpoint=str(tmp_path / "checkpoint-1"),
    )

    with pytest.raises(ValueError, match="requires checkpoint metadata"):
        _validate_resume_compatibility(
            None,
            config,
            ["O", "B-PERSON", "I-PERSON"],
            source_metadata=source,
        )

    metadata = {
        "model_family": "Qwen3.5",
        "model_name": "model",
        "config": {"scheme": "BIO", "max_length": 1024, "overlap": 128},
        "label_mapping": {
            "id2label": {
                "0": "O",
                "1": "B-PERSON",
                "2": "I-PERSON",
            }
        },
        "source": _safe_source_metadata(source),
    }
    _validate_resume_compatibility(
        metadata,
        config,
        ["O", "B-PERSON", "I-PERSON"],
        source_metadata=source,
    )

    changed = {
        **source,
        "files": {
            **source["files"],
            "train": {
                **source["files"]["train"],
                "sha256": "c" * 64,
            },
        },
    }
    with pytest.raises(ValueError, match="source is incompatible"):
        _validate_resume_compatibility(
            metadata,
            config,
            ["O", "B-PERSON", "I-PERSON"],
            source_metadata=changed,
        )


def test_checkpoint_artifacts_include_safe_training_and_model_metadata(tmp_path: Path):
    class FakeTokenizer:
        def save_pretrained(self, destination):
            (Path(destination) / "tokenizer_config.json").write_text("{}", encoding="utf-8")

    class FakeConfig:
        def save_pretrained(self, destination):
            (Path(destination) / "config.json").write_text("{}", encoding="utf-8")

    class FakeModel:
        config = FakeConfig()
        backbone = object()
        num_labels = 1
        id2label = {0: "O"}
        label2id = {"O": 0}

    checkpoint = tmp_path / "checkpoint-1"
    _persist_checkpoint_artifacts(
        checkpoint,
        model=FakeModel(),
        tokenizer=FakeTokenizer(),
        metadata_text='{"source":{"kind":"external_fixed_splits"}}\n',
    )

    assert (checkpoint / "tokenizer_config.json").is_file()
    assert (checkpoint / "config.json").is_file()
    assert (checkpoint / "wenveil_token_classifier.json").is_file()
    assert (checkpoint / "training_config.json").read_text(encoding="utf-8") == (
        '{"source":{"kind":"external_fixed_splits"}}\n'
    )


class DuplicateOwnershipTokenizer:
    def __call__(self, text, **kwargs):
        offsets = [(0, 0), (0, 2), (2, 3), (0, 0)]
        return {
            "input_ids": [[0, 1, 2, 0], [0, 1, 2, 0]],
            "attention_mask": [[1] * 4, [1] * 4],
            "offset_mapping": [offsets, offsets],
        }


def test_overlapping_windows_have_one_positive_owner():
    sample = sample_from_spans("甲乙丙", [Span(0, 2, "PERSON", "甲乙")])
    rows = prepare_training_rows(
        [sample], DuplicateOwnershipTokenizer(), max_length=4, stride=1
    )

    assert rows[0]["labels"] == [-100, "B-PERSON", "O", -100]
    assert rows[1]["labels"] == [-100, -100, "O", -100]


def test_token_crossing_entity_boundary_fails_strictly():
    with pytest.raises(FeatureAlignmentError):
        from training.train_token_classifier import align_offsets_to_labels

        align_offsets_to_labels(
            [(0, 2)],
            [Span(0, 1, "PERSON", "甲"), Span(1, 2, "ORG", "乙")],
            text="甲乙",
        )


def test_model_decoder_drops_unclosed_nonterminal_window_span():
    recognizer = ModelNERRecognizer()
    rows = [(0, 1, "PERSON", 0.9, "B"), (1, 2, "PERSON", 0.9, "I")]

    assert recognizer._rows_to_spans(
        "甲乙",
        rows,
        flush_trailing=False,
    ) == []
    assert [span.surface for span in recognizer._rows_to_spans("甲乙", rows)] == ["甲乙"]


class WordIdTokenizerOutput(dict):
    def word_ids(self, batch_index=0):
        return [None, 0, 1, 2, None, None]


class UnicodeWordIdTokenizer:
    def __call__(self, text, **kwargs):
        return WordIdTokenizerOutput(
            input_ids=[0, 1, 2, 3, 0, 0],
            attention_mask=[1, 1, 1, 1, 0, 0],
            offset_mapping=[
                (0, 0),
                (0, 1),
                (0, 1),
                (0, 1),
                (0, 0),
                (0, 0),
            ],
        )


def test_alignment_maps_word_ids_to_unicode_codepoint_offsets_and_padding():
    sample = sample_from_spans("A🙂中", [Span(1, 3, "ORG", "🙂中")])
    rows = prepare_training_rows([sample], UnicodeWordIdTokenizer())

    assert rows[0]["labels"] == [-100, "O", "B-ORG", "I-ORG", -100, -100]
