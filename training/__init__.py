"""Offline data and training helpers for the domain NER model.

The package deliberately has no import-time dependency on PyTorch,
Transformers, or a model checkpoint.  Data validation and dataset preparation
are usable in a minimal Python environment; the optional training dependency
is loaded only by :mod:`training.train_token_classifier` when training starts.
"""

from .augment_ocr import OCRAugmentationConfig, augment_dataset, augment_sample
from .build_dataset import (
    build_samples_from_span_records,
    read_jsonl,
    write_jsonl,
)
from .external_dataset import (
    FIXED_SPLITS,
    ExternalDatasetError,
    read_external_split,
    read_external_splits,
    validate_external_dataset,
)
from .labels import (
    LabelValidationError,
    validate_bilou_labels,
    validate_bio_labels,
    validate_tagged_text,
)
from .samples import EntityAnnotation, TrainingSample, sample_from_spans
from .splitting import DatasetSplits, split_by_document

__all__ = [
    "DatasetSplits",
    "EntityAnnotation",
    "ExternalDatasetError",
    "FIXED_SPLITS",
    "LabelValidationError",
    "OCRAugmentationConfig",
    "TrainingSample",
    "augment_dataset",
    "augment_sample",
    "build_samples_from_span_records",
    "read_jsonl",
    "read_external_split",
    "read_external_splits",
    "sample_from_spans",
    "split_by_document",
    "validate_bilou_labels",
    "validate_bio_labels",
    "validate_tagged_text",
    "validate_external_dataset",
    "write_jsonl",
]
