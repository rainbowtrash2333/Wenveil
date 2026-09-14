from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from ..models import Span


def _merge_window_spans(spans: list[Span]) -> list[Span]:
    """Prefer a complete same-type span when windows emit partial spans."""

    result: list[Span] = []
    for span in sorted(spans, key=lambda item: (-item.length, -item.score, item.start)):
        overlaps = [
            item
            for item in result
            if item.entity_type == span.entity_type and item.overlaps(span)
        ]
        if any(item.start <= span.start and span.end <= item.end for item in overlaps):
            continue
        result = [
            item
            for item in result
            if not (item.start <= span.start and span.end <= item.end)
        ]
        result.append(span)
    return sorted(result, key=lambda span: (span.start, span.end, span.entity_type))


class ModelNERRecognizer:
    """Optional local token-classification recognizer for a fine-tuned Qwen model.

    The adapter is deliberately lazy: importing the desensitizer never loads
    PyTorch or a model.  A model is loaded only when ``model.enabled`` is true
    and ``recognize`` is called.  It consumes local files only by default so a
    Windows/offline deployment cannot silently download weights.
    """

    DEFAULT_TYPES = {"PERSON", "ORG", "PROJECT", "DEPARTMENT", "ADDRESS"}
    MODEL_LABEL_TYPES = {
        "ORG_FULL",
        "ORG_ALIAS",
        "ORG_SUBSIDIARY",
        "ORG_BRANCH",
        "LOCATION",
    }
    DEFAULT_PRIORITIES = {
        "PERSON": 80,
        "ORG": 85,
        "PROJECT": 84,
        "DEPARTMENT": 78,
        "ADDRESS": 75,
    }

    def __init__(
        self,
        options: dict[str, Any] | None = None,
        *,
        base_dir: Path | None = None,
        entity_options: dict[str, Any] | None = None,
    ):
        self.options = dict(options or {})
        self.base_dir = base_dir or Path.cwd()
        self.entity_options = entity_options or {}
        self.enabled = bool(self.options.get("enabled", False))
        self._tokenizer = None
        self._model = None
        self._torch = None
        self._id2label: dict[int, str] = {}

    def recognize(self, text: str) -> list[Span]:
        if not self.enabled:
            return []
        self._ensure_loaded()
        chunks = self._tokenize(text)
        all_spans: dict[tuple[int, int, str], Span] = {}
        for chunk in chunks:
            for span in self._predict_chunk(text, chunk):
                key = (span.start, span.end, span.entity_type)
                old = all_spans.get(key)
                if old is None or span.score > old.score:
                    all_spans[key] = span
        return _merge_window_spans(list(all_spans.values()))

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        model_path = self.options.get("path")
        if not model_path:
            raise RuntimeError("model.enabled=true requires model.path")
        path = Path(str(model_path))
        if not path.is_absolute():
            path = self.base_dir / path
        if not path.exists():
            raise FileNotFoundError("local NER model path does not exist")
        try:
            import torch
            from transformers import AutoModelForTokenClassification, AutoTokenizer
        except ImportError as exc:  # pragma: no cover - optional runtime dependency
            raise RuntimeError("torch and transformers are required for the local model recognizer") from exc

        local_only = bool(self.options.get("local_files_only", True))
        trust_remote_code = bool(self.options.get("trust_remote_code", False))
        self._tokenizer = AutoTokenizer.from_pretrained(
            path,
            local_files_only=local_only,
            trust_remote_code=trust_remote_code,
            use_fast=True,
        )
        if not getattr(self._tokenizer, "is_fast", False):
            raise RuntimeError("the local NER tokenizer must be a fast tokenizer with offset mappings")
        self._model = AutoModelForTokenClassification.from_pretrained(
            path,
            local_files_only=local_only,
            trust_remote_code=trust_remote_code,
        )
        device_name = str(self.options.get("device", "auto"))
        if device_name == "auto":
            device_name = "cuda" if torch.cuda.is_available() else "cpu"
        self._model.to(device_name)
        self._model.eval()
        self._torch = torch
        raw_id2label = getattr(self._model.config, "id2label", {}) or {}
        self._id2label = {int(index): str(label) for index, label in raw_id2label.items()}

    def _tokenize(self, text: str) -> list[dict[str, Any]]:
        chunk_size = max(32, int(self.options.get("chunk_size", 1024)))
        overlap = max(0, min(int(self.options.get("overlap", 128)), chunk_size - 1))
        fixed_length = self.options.get("fixed_sequence_length")
        if fixed_length:
            chunk_size = int(fixed_length)
            overlap = max(0, min(overlap, chunk_size - 1))
        padding = "max_length" if fixed_length else False
        # Qwen's BPE may merge a Chinese character at an entity boundary with
        # the following punctuation.  Pretokenizing into one-character words
        # keeps every source character representable while retaining Qwen's
        # vocabulary and model inputs.  A non-fast/fake tokenizer can fall
        # back to ordinary text tokenization for compatibility with tests.
        try:
            encoded = self._tokenizer(
                list(text),
                is_split_into_words=True,
                return_offsets_mapping=True,
                return_overflowing_tokens=True,
                truncation=True,
                max_length=chunk_size,
                stride=overlap,
                padding=padding,
            )
        except (TypeError, ValueError):
            encoded = self._tokenizer(
                text,
                return_offsets_mapping=True,
                return_overflowing_tokens=True,
                truncation=True,
                max_length=chunk_size,
                stride=overlap,
                padding=padding,
            )
        keys = [
            key
            for key in (
                "input_ids",
                "attention_mask",
                "token_type_ids",
                "position_ids",
                "offset_mapping",
            )
            if key in encoded
        ]
        chunks: list[dict[str, Any]] = []
        input_ids = encoded["input_ids"]
        is_batched = bool(
            input_ids
            and isinstance(input_ids[0], (list, tuple))
        )
        normalized_values = {
            key: encoded[key] if is_batched else [encoded[key]]
            for key in keys
        }
        raw_offsets = normalized_values["offset_mapping"]
        count = len(normalized_values["input_ids"])
        for index in range(count):
            chunk = {
                key: normalized_values[key][index]
                for key in keys
                if key != "offset_mapping"
            }
            offsets = raw_offsets[index]
            if hasattr(encoded, "word_ids"):
                word_ids = encoded.word_ids(batch_index=index)
                offsets = [
                    (word_id + int(offset[0]), word_id + int(offset[1]))
                    if word_id is not None and int(offset[1]) > int(offset[0])
                    else (0, 0)
                    for word_id, offset in zip(word_ids, offsets)
                ]
            chunk["offset_mapping"] = offsets
            chunk["window_start"] = min(
                (int(start) for start, end in offsets if int(end) > int(start)),
                default=0,
            )
            chunk["window_end"] = max(
                (int(end) for start, end in offsets if int(end) > int(start)),
                default=0,
            )
            chunk["is_document_end"] = chunk["window_end"] >= len(text)
            chunks.append(chunk)
        return chunks

    def _predict_chunk(self, text: str, chunk: dict[str, Any]) -> list[Span]:
        tensor_inputs: dict[str, Any] = {}
        for key in ("input_ids", "attention_mask", "token_type_ids", "position_ids"):
            if key in chunk:
                device = getattr(self._model, "device", None)
                if device is None:
                    try:
                        device = next(self._model.parameters()).device
                    except (AttributeError, StopIteration):
                        device = "cpu"
                tensor_inputs[key] = self._torch.tensor([chunk[key]], device=device)
        with self._torch.no_grad():
            logits = self._model(**tensor_inputs).logits[0]
            probabilities = self._torch.softmax(logits, dim=-1)
            scores, labels = self._torch.max(probabilities, dim=-1)
        token_rows: list[tuple[int, int, str | None, float, str]] = []
        offsets = chunk["offset_mapping"]
        for offset, label_id, score in zip(offsets, labels.tolist(), scores.tolist()):
            start, end = int(offset[0]), int(offset[1])
            if end <= start:
                continue
            label = self._id2label.get(int(label_id), "O")
            entity_type, prefix = self._parse_label(label)
            if entity_type is None:
                token_rows.append((start, end, None, float(score), "O"))
            else:
                threshold = self._threshold(entity_type)
                token_rows.append(
                    (start, end, entity_type if score >= threshold else None, float(score), prefix)
                )
        return self._rows_to_spans(
            text,
            token_rows,
            allow_leading=not bool(chunk.get("window_start", 0)),
            flush_trailing=bool(chunk.get("is_document_end", True)),
        )

    def _rows_to_spans(
        self,
        text: str,
        rows: list[tuple[int, int, str | None, float] | tuple[int, int, str | None, float, str]],
        *,
        allow_leading: bool = True,
        flush_trailing: bool = True,
    ) -> list[Span]:
        result: list[Span] = []
        active_type: str | None = None
        active_start = 0
        active_end = 0
        active_scores: list[float] = []

        def flush() -> None:
            nonlocal active_type, active_start, active_end, active_scores
            if active_type is not None and active_end > active_start:
                surface = text[active_start:active_end]
                if surface.strip() and any(char.isalnum() or "\u3400" <= char <= "\u9fff" for char in surface):
                    score = sum(active_scores) / len(active_scores)
                    result.append(
                        Span(
                            active_start,
                            active_end,
                            active_type,
                            surface,
                            score=score,
                            priority=self._priority(active_type),
                            source="qwen_token_classifier",
                            rule_id="model_ner",
                            anonymize=self._anonymize(active_type),
                            protect=self._protect(active_type),
                        )
                    )
            active_type = None
            active_start = active_end = 0
            active_scores = []

        for row in rows:
            if len(row) == 5:
                start, end, entity_type, score, prefix = row
            else:
                start, end, entity_type, score = row
                # Legacy/internal callers that do not provide a scheme use
                # adjacent same-type tokens as one entity.
                prefix = "I"
            if entity_type is None:
                flush()
                continue
            prefix = str(prefix).upper()
            if not allow_leading and active_type is None and prefix in {"I", "L", "E"}:
                # The window begins inside an entity whose B-token belongs to
                # an earlier window.  Do not emit a truncated pseudo-entity.
                continue
            if prefix in {"U", "S"}:
                flush()
                active_type = entity_type
                active_start = start
                active_end = end
                active_scores = [score]
                flush()
                continue
            if prefix in {"B"} or active_type != entity_type or start > active_end + 1:
                flush()
                active_type = entity_type
                active_start = start
                active_end = end
                active_scores = [score]
                continue
            if active_type == entity_type and start <= active_end + 1:
                active_end = max(active_end, end)
                active_scores.append(score)
                if prefix in {"L", "E"}:
                    flush()
                continue
            flush()
            active_type = entity_type
            active_start = start
            active_end = end
            active_scores = [score]
        if flush_trailing:
            flush()
        return result

    def _parse_label(self, label: str) -> tuple[str | None, str]:
        value = label.upper().strip()
        if value in {"", "O", "0", "LABEL_0"}:
            return None, "O"
        if "-" in value:
            prefix, raw_type = value.split("-", 1)
        else:
            prefix, raw_type = "B", value
        label_map = {str(key).upper(): str(value).upper() for key, value in (self.options.get("label_map") or {}).items()}
        entity_type = label_map.get(raw_type, raw_type)
        original_type = entity_type
        if entity_type in {"PER", "PERSON_NAME"}:
            entity_type = "PERSON"
        elif entity_type in {
            "ORG_NAME",
            "ORG_FULL",
            "ORG_ALIAS",
            "ORG_SUBSIDIARY",
            "ORG_BRANCH",
            "ORGANIZATION",
            "INSTITUTION",
        }:
            entity_type = "ORG"
        elif entity_type in {"PROJ", "PROJECT_NAME"}:
            entity_type = "PROJECT"
        elif entity_type in {"DEPT", "DEPARTMENT_NAME"}:
            entity_type = "DEPARTMENT"
        elif entity_type in {"LOC", "LOCATION", "ADDRESS_NAME"}:
            entity_type = "ADDRESS"
        allowed = set(self.options.get("entity_types") or self.DEFAULT_TYPES)
        allowed.update(self.MODEL_LABEL_TYPES)
        if self.options.get("preserve_label_types") and original_type in self.MODEL_LABEL_TYPES:
            entity_type = original_type
        return (entity_type, prefix) if entity_type in allowed else (None, "O")

    def _threshold(self, entity_type: str) -> float:
        thresholds = self.options.get("thresholds") or {}
        return float(thresholds.get(entity_type, 0.8))

    def _priority(self, entity_type: str) -> int:
        priorities = self.options.get("priorities") or {}
        return int(priorities.get(entity_type, self.DEFAULT_PRIORITIES.get(entity_type, 75)))

    def _anonymize(self, entity_type: str) -> bool:
        value = self.entity_options.get(entity_type)
        return True if value is None else bool(value.anonymize)

    def _protect(self, entity_type: str) -> bool:
        value = self.entity_options.get(entity_type)
        return False if value is None else (not value.anonymize and value.protect_when_disabled)
