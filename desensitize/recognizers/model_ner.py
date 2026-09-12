from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from ..models import Span


class ModelNERRecognizer:
    """Optional local token-classification recognizer for a fine-tuned Qwen model.

    The adapter is deliberately lazy: importing the desensitizer never loads
    PyTorch or a model.  A model is loaded only when ``model.enabled`` is true
    and ``recognize`` is called.  It consumes local files only by default so a
    Windows/offline deployment cannot silently download weights.
    """

    DEFAULT_TYPES = {"PERSON", "ORG", "PROJECT", "DEPARTMENT", "ADDRESS"}
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
        return sorted(all_spans.values(), key=lambda span: (span.start, span.end, span.entity_type))

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
            raise FileNotFoundError(f"local NER model path does not exist: {path}")
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
        encoded = self._tokenizer(
            text,
            return_offsets_mapping=True,
            return_overflowing_tokens=True,
            truncation=True,
            max_length=chunk_size,
            stride=overlap,
            padding=False,
        )
        keys = [key for key in ("input_ids", "attention_mask", "token_type_ids", "offset_mapping") if key in encoded]
        chunks: list[dict[str, Any]] = []
        count = len(encoded["input_ids"])
        for index in range(count):
            chunks.append({key: encoded[key][index] for key in keys})
        return chunks

    def _predict_chunk(self, text: str, chunk: dict[str, Any]) -> list[Span]:
        tensor_inputs: dict[str, Any] = {}
        for key in ("input_ids", "attention_mask", "token_type_ids"):
            if key in chunk:
                tensor_inputs[key] = self._torch.tensor([chunk[key]], device=self._model.device)
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
        return self._rows_to_spans(text, token_rows)

    def _rows_to_spans(
        self,
        text: str,
        rows: list[tuple[int, int, str | None, float] | tuple[int, int, str | None, float, str]],
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
        if entity_type in {"PER", "PERSON_NAME"}:
            entity_type = "PERSON"
        elif entity_type in {"ORG_NAME", "ORGANIZATION", "INSTITUTION"}:
            entity_type = "ORG"
        elif entity_type in {"PROJ", "PROJECT_NAME"}:
            entity_type = "PROJECT"
        elif entity_type in {"DEPT", "DEPARTMENT_NAME"}:
            entity_type = "DEPARTMENT"
        elif entity_type in {"LOC", "LOCATION"}:
            entity_type = "ADDRESS"
        allowed = set(self.options.get("entity_types") or self.DEFAULT_TYPES)
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
