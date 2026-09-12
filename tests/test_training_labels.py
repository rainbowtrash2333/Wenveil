import pytest

from desensitize.models import Span
from training.labels import (
    LabelValidationError,
    parse_tagged_text,
    spans_to_labels,
    validate_bilou_labels,
    validate_bio_labels,
)
from training.samples import sample_from_spans


def test_rule_span_metadata_can_generate_lossless_bio_and_bilou_labels():
    text = "张三在中国人保"
    spans = [
        Span(0, 2, "PERSON", "张三", score=0.91, source="person_rule"),
        Span(3, 7, "ORG", "中国人保", priority=94, source="dictionary"),
    ]

    bio = spans_to_labels(text, spans, scheme="BIO")
    bilou = spans_to_labels(text, spans, scheme="BILOU")

    assert validate_bio_labels(bio, text=text).text == text
    assert validate_bilou_labels(bilou, text=text).text == text
    sample = sample_from_spans(text, spans, document_id="doc-1", scheme="BILOU")
    assert [span.entity_type for span in sample.spans] == ["PERSON", "ORG"]


def test_bio_entity_must_not_resume_after_o():
    with pytest.raises(LabelValidationError):
        validate_bio_labels(
            ["B-PERSON", "O", "I-PERSON"],
            text="张在三",
        )


def test_tagged_teacher_output_is_lossless_and_rejects_rewritten_text():
    tagged = parse_tagged_text("<ORG>中国人保</ORG>委派<PERSON>张三</PERSON>")
    assert tagged.text == "中国人保委派张三"
    assert [(span.start, span.end, span.entity_type) for span in tagged.spans] == [
        (0, 4, "ORG"),
        (6, 8, "PERSON"),
    ]
    with pytest.raises(LabelValidationError):
        parse_tagged_text("<PERSON>张三</PERSON>", original_text="张四")


def test_token_offsets_allow_special_zero_length_units():
    result = validate_bio_labels(
        ["O", "B-PERSON", "I-PERSON", "O"],
        text="张三",
        offsets=[(0, 0), (0, 1), (1, 2), (0, 0)],
    )
    assert [(span.start, span.end) for span in result.spans] == [(0, 2)]
