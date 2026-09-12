from __future__ import annotations

import math

import pytest

from desensitize.models import Span
from desensitize.recognizers.model_ner import ModelNERRecognizer


class _FakeTensor:
    def __init__(self, values):
        self._values = values

    def tolist(self):
        return self._values


class _FakeNoGrad:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class _FakeTorch:
    """Small torch-shaped adapter for exercising decoding without model weights."""

    @staticmethod
    def tensor(values, device=None):
        return values

    @staticmethod
    def no_grad():
        return _FakeNoGrad()

    @staticmethod
    def softmax(rows, dim=-1):
        probabilities = []
        for row in rows:
            largest = max(row)
            exponentials = [math.exp(value - largest) for value in row]
            total = sum(exponentials)
            probabilities.append([value / total for value in exponentials])
        return probabilities

    @staticmethod
    def max(rows, dim=-1):
        scores = [max(row) for row in rows]
        labels = [max(range(len(row)), key=row.__getitem__) for row in rows]
        return _FakeTensor(scores), _FakeTensor(labels)


class _FakeLogits:
    def __init__(self, batched_rows):
        self._batched_rows = batched_rows

    def __getitem__(self, index):
        return self._batched_rows[index]


class _FakeOutput:
    def __init__(self, batched_rows):
        self.logits = _FakeLogits(batched_rows)


class _FakeModel:
    device = "cpu"

    def __init__(self, rows):
        self._rows = rows

    def __call__(self, **inputs):
        return _FakeOutput([self._rows])


def _predict_tagged_tokens(
    text: str,
    labels: list[str],
    *,
    margins: list[float] | None = None,
    options: dict | None = None,
) -> list[Span]:
    """Run one synthetic chunk through the real prediction/decode path."""

    label_names = list(dict.fromkeys(labels))
    label_to_id = {label: index for index, label in enumerate(label_names)}
    margins = margins or [8.0] * len(labels)
    assert len(margins) == len(labels)

    logits = []
    for label, margin in zip(labels, margins):
        row = [0.0] * len(label_names)
        row[label_to_id[label]] = margin
        logits.append(row)

    recognizer = ModelNERRecognizer(options or {})
    recognizer._torch = _FakeTorch
    recognizer._model = _FakeModel(logits)
    recognizer._id2label = {
        index: label for index, label in enumerate(label_names)
    }
    chunk = {
        "input_ids": list(range(len(labels))),
        "attention_mask": [1] * len(labels),
        "offset_mapping": [(index, index + 1) for index in range(len(labels))],
    }
    return recognizer._predict_chunk(text, chunk)


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("B-PER", ("PERSON", "B")),
        ("I-PERSON_NAME", ("PERSON", "I")),
        ("L-ORG_NAME", ("ORG", "L")),
        ("U-PROJECT_NAME", ("PROJECT", "U")),
        ("B-DEPT", ("DEPARTMENT", "B")),
        ("I-LOC", ("ADDRESS", "I")),
        ("PERSON", ("PERSON", "B")),
        ("O", (None, "O")),
        ("LABEL_0", (None, "O")),
    ],
)
def test_parse_label_supports_bio_bilou_and_builtin_aliases(label, expected):
    recognizer = ModelNERRecognizer()

    assert recognizer._parse_label(label) == expected


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("B-HUMAN", ("PERSON", "B")),
        ("I-COMPANY", ("ORG", "I")),
        ("U-TASK", ("PROJECT", "U")),
        ("L-TEAM", ("DEPARTMENT", "L")),
    ],
)
def test_parse_label_applies_configured_aliases(label, expected):
    recognizer = ModelNERRecognizer(
        {
            "label_map": {
                "HUMAN": "PER",
                "COMPANY": "ORGANIZATION",
                "TASK": "PROJECT_NAME",
                "TEAM": "DEPT",
            }
        }
    )

    assert recognizer._parse_label(label) == expected


def test_bio_b_boundaries_produce_separate_spans():
    spans = _predict_tagged_tokens(
        "甲乙丙",
        ["B-PER", "I-PER", "B-PER"],
    )

    assert [(span.start, span.end, span.surface) for span in spans] == [
        (0, 2, "甲乙"),
        (2, 3, "丙"),
    ]


def test_bilou_unit_and_begin_end_spans_produce_separate_spans():
    spans = _predict_tagged_tokens(
        "甲乙丙",
        ["U-ORG", "B-ORG", "L-ORG"],
    )

    assert [(span.start, span.end, span.surface) for span in spans] == [
        (0, 1, "甲"),
        (1, 3, "乙丙"),
    ]


def test_confidence_threshold_filters_low_scored_entity_tokens():
    spans = _predict_tagged_tokens(
        "甲乙丙",
        ["O", "B-PER", "B-PER"],
        margins=[4.0, 2.0, 3.0],
        options={"thresholds": {"PERSON": 0.9}},
    )

    assert [(span.start, span.end, span.surface) for span in spans] == [
        (2, 3, "丙"),
    ]
    assert spans[0].score > 0.9


def test_rows_to_spans_flushes_when_a_token_is_not_an_entity():
    recognizer = ModelNERRecognizer()

    spans = recognizer._rows_to_spans(
        "甲乙丙",
        [
            (0, 1, "PERSON", 0.91),
            (1, 2, None, 0.99),
            (2, 3, "PERSON", 0.93),
        ],
    )

    assert [(span.start, span.end, span.surface) for span in spans] == [
        (0, 1, "甲"),
        (2, 3, "丙"),
    ]


def test_recognize_deduplicates_exact_spans_across_chunks(monkeypatch):
    recognizer = ModelNERRecognizer({"enabled": True})
    chunks = [{"chunk": 1}, {"chunk": 2}]
    predictions = {
        1: [
            Span(0, 2, "PERSON", "张三", score=0.80),
            Span(4, 6, "ORG", "华电", score=0.91),
        ],
        2: [
            Span(0, 2, "PERSON", "张三", score=0.95),
            Span(8, 10, "PROJECT", "项目名称", score=0.89),
        ],
    }

    monkeypatch.setattr(recognizer, "_ensure_loaded", lambda: None)
    monkeypatch.setattr(recognizer, "_tokenize", lambda text: chunks)
    monkeypatch.setattr(
        recognizer,
        "_predict_chunk",
        lambda text, chunk: predictions[chunk["chunk"]],
    )

    spans = recognizer.recognize("张三负责华电投资项目名称")

    assert [
        (span.start, span.end, span.entity_type, span.score) for span in spans
    ] == [
        (0, 2, "PERSON", 0.95),
        (4, 6, "ORG", 0.91),
        (8, 10, "PROJECT", 0.89),
    ]
