from desensitize.models import Span
from training.augment_ocr import OCRAugmentationConfig, augment_dataset, augment_ocr, augment_sample
from training.samples import sample_from_spans
from training.splitting import split_by_document


def test_ocr_space_insertion_adjusts_entity_offsets():
    text = "张三负责中国人保"
    spans = (
        Span(0, 2, "PERSON", "张三"),
        Span(4, 8, "ORG", "中国人保"),
    )
    augmented, adjusted = augment_ocr(
        text,
        spans,
        config=OCRAugmentationConfig(
            space_probability=1.0,
            newline_probability=0.0,
            within_entities_only=True,
            max_insertions_per_span=1,
        ),
        seed=1,
    )
    assert "张 三" in augmented
    assert any(" " in span.surface for span in adjusted if span.entity_type == "ORG")
    for span in adjusted:
        span.validate_against(augmented)


def test_augmentation_is_optional_and_does_not_change_zero_copy_dataset():
    sample = sample_from_spans("中国人保", [Span(0, 4, "ORG", "中国人保")], document_id="doc")
    assert augment_sample(
        sample,
        config=OCRAugmentationConfig(space_probability=0, newline_probability=0),
        seed=2,
    ).text == sample.text
    output = augment_dataset([sample], copies=0)
    assert output == [sample]


def test_augmented_copies_stay_in_one_document_split():
    sample = sample_from_spans("中国人保", [Span(0, 4, "ORG", "中国人保")], document_id="doc")
    augmented = augment_dataset(
        [sample],
        copies=2,
        config=OCRAugmentationConfig(space_probability=0, newline_probability=0),
    )
    splits = split_by_document(augmented, train_ratio=1, validation_ratio=0, test_ratio=0)
    assert len(splits.train) == 3
    assert not splits.validation and not splits.test
