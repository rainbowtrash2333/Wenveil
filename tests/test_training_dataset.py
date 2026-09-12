from pathlib import Path
from types import SimpleNamespace

import pytest

from desensitize.models import Span
from training.build_dataset import read_jsonl, write_jsonl
from training.generate_financial_alias_data import generate
from training.samples import chunk_sample, sample_from_spans, samples_from_rule_results
from training.splitting import split_by_document


def test_jsonl_round_trip_preserves_text_and_labels(tmp_path: Path):
    samples = [
        sample_from_spans(
            "张三负责项目A",
            [Span(0, 2, "PERSON", "张三"), Span(4, 7, "PROJECT", "项目A")],
            document_id="doc-a",
        ),
        sample_from_spans("没有实体的正文", [], document_id="doc-b"),
    ]
    path = tmp_path / "dataset.jsonl"
    assert write_jsonl(path, samples) == 2
    loaded = read_jsonl(path)
    assert [sample.text for sample in loaded] == [sample.text for sample in samples]
    assert [[span.entity_type for span in sample.spans] for sample in loaded] == [
        ["PERSON", "PROJECT"],
        [],
    ]

    broken = tmp_path / "broken.jsonl"
    broken.write_text(
        '{"document_id":"bad","text":"张三","spans":[{"start":0,"end":2,"label":"PERSON"}],'
        '"labels":["B-PERSON","O"]}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        read_jsonl(broken)


def test_document_level_split_never_leaks_document_chunks():
    samples = []
    for doc_index in range(6):
        samples.append(sample_from_spans(f"文档{doc_index}-块0", [], document_id=f"doc-{doc_index}"))
        samples.append(sample_from_spans(f"文档{doc_index}-块1", [], document_id=f"doc-{doc_index}"))
    splits = split_by_document(samples, train_ratio=0.5, validation_ratio=0.25, test_ratio=0.25, seed=7)
    membership = {}
    for split_name, values in splits.as_dict().items():
        for sample in values:
            assert sample.document_id not in membership or membership[sample.document_id] == split_name
            membership[sample.document_id] = split_name
    assert set(membership) == {f"doc-{index}" for index in range(6)}


def test_chunker_expands_window_to_keep_entity_intact():
    text = "前文" + "张三科技有限公司" + "后文很长"
    start = 2
    sample = sample_from_spans(
        text,
        [Span(start, start + 8, "ORG", "张三科技有限公司")],
        document_id="doc",
    )
    chunks = chunk_sample(sample, max_characters=7, overlap=2)
    assert any(any(span.surface == "张三科技有限公司" for span in chunk.spans) for chunk in chunks)
    for chunk in chunks:
        chunk.validate()


def test_existing_anonymization_result_shape_can_seed_training_sample():
    result = SimpleNamespace(
        normalized_text="张三在中国人保",
        accepted_spans=[
            Span(0, 2, "PERSON", "张三"),
            Span(3, 7, "ORG", "中国人保"),
        ],
    )
    samples = samples_from_rule_results([result])
    assert samples[0].text == result.normalized_text
    assert [span.entity_type for span in samples[0].spans] == ["PERSON", "ORG"]


def test_financial_alias_generator_keeps_ner_spans_lossless_and_non_overlapping():
    samples, linking = generate(12, seed=7)

    assert len(samples) == 12
    assert len(linking) == 12
    assert {record["relation"] for record in linking} == {
        "ALIAS_OF",
        "SUBSIDIARY_OF",
        "BRANCH_OF",
    }
    for sample in samples:
        sample.validate()
