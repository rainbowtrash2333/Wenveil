"""Offline ONNX Runtime token-classification recognizer.

This module intentionally does not import PyTorch.  A deployed model directory
contains only the ONNX graph, tokenizer files, label mapping, and runtime
configuration; the returned values remain candidate ``Span`` objects for the
existing Pipeline/Resolver.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .model_ner import ModelNERRecognizer


class OnnxNERRecognizer(ModelNERRecognizer):
    """Run the same span decoder through ONNX Runtime."""

    def __init__(
        self,
        options: dict[str, Any] | None = None,
        *,
        base_dir: Path | None = None,
        entity_options: dict[str, Any] | None = None,
    ):
        super().__init__(options, base_dir=base_dir, entity_options=entity_options)
        self._session = None
        self._np = None
        self.execution_provider = ""
        self.available_providers: tuple[str, ...] = ()

    def _ensure_loaded(self) -> None:
        if self._session is not None:
            return
        try:
            import numpy as np
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as exc:  # pragma: no cover - optional runtime dependency
            raise RuntimeError(
                "onnxruntime, numpy, and the tokenizers package are required "
                "for the ONNX model recognizer"
            ) from exc

        configured_path = self.options.get("path")
        if not configured_path:
            raise RuntimeError("model.enabled=true with backend=onnx requires model.path")
        path = Path(str(configured_path))
        if not path.is_absolute():
            path = self.base_dir / path
        if path.is_dir():
            runtime_config_path = path / "runtime_config.json"
            if runtime_config_path.is_file():
                runtime_config = json.loads(runtime_config_path.read_text(encoding="utf-8"))
                if isinstance(runtime_config, dict):
                    for key, value in runtime_config.items():
                        self.options.setdefault(key, value)
            model_path = path / str(self.options.get("onnx_file", "model.onnx"))
            tokenizer_path = path / str(self.options.get("tokenizer_dir", "."))
            mapping_path = path / str(self.options.get("label_mapping", "label_mapping.json"))
        else:
            model_path = path
            tokenizer_path = path.parent / str(self.options.get("tokenizer_dir", "."))
            mapping_path = path.parent / str(self.options.get("label_mapping", "label_mapping.json"))
        if not model_path.is_file():
            raise FileNotFoundError(f"local ONNX model does not exist: {model_path}")
        if not mapping_path.is_file():
            raise FileNotFoundError(f"ONNX label mapping does not exist: {mapping_path}")

        tokenizer_file = tokenizer_path / "tokenizer.json"
        if not tokenizer_file.is_file():
            raise FileNotFoundError(f"ONNX tokenizer.json does not exist: {tokenizer_file}")
        self._tokenizer = Tokenizer.from_file(str(tokenizer_file))

        mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
        raw_id2label = mapping.get("id2label", mapping)
        self._id2label = {int(index): str(label) for index, label in raw_id2label.items()}
        configured = str(self.options.get("execution_provider", "auto")).lower()
        available = tuple(str(value) for value in ort.get_available_providers())
        self.available_providers = available
        directml = "DmlExecutionProvider"
        if configured in {"cpu", "cpuexecutionprovider"}:
            selected = ["CPUExecutionProvider"]
        elif configured in {"directml", "dmlexecutionprovider"}:
            if directml not in available:
                raise RuntimeError("DirectMLExecutionProvider is not available")
            selected = [directml, "CPUExecutionProvider"]
        else:
            selected = [directml, "CPUExecutionProvider"] if directml in available else ["CPUExecutionProvider"]
        try:
            self._session = ort.InferenceSession(str(model_path), providers=selected)
        except Exception:
            if configured not in {"auto", ""} or selected == ["CPUExecutionProvider"]:
                raise
            self._session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        self.execution_provider = str(self._session.get_providers()[0])
        self._np = np

    def _tokenize(self, text: str) -> list[dict[str, Any]]:
        """Tokenize without importing Transformers or PyTorch at runtime."""

        if self._tokenizer is None:
            raise RuntimeError("ONNX tokenizer is not loaded")
        chunk_size = max(32, int(self.options.get("chunk_size", 1024)))
        overlap = max(0, min(int(self.options.get("overlap", 128)), chunk_size - 1))
        fixed_length = self.options.get("fixed_sequence_length")
        if fixed_length:
            chunk_size = int(fixed_length)
            overlap = max(0, min(overlap, chunk_size - 1))
            self._tokenizer.enable_padding(length=chunk_size)
        else:
            self._tokenizer.no_padding()
        self._tokenizer.enable_truncation(max_length=chunk_size, stride=overlap)
        encoded = self._tokenizer.encode(list(text), is_pretokenized=True)
        encodings = [encoded, *encoded.overflowing]
        chunks: list[dict[str, Any]] = []
        for item in encodings:
            offsets = [
                (word_id + int(offset[0]), word_id + int(offset[1]))
                if word_id is not None and int(offset[1]) > int(offset[0])
                else (0, 0)
                for word_id, offset in zip(item.word_ids, item.offsets)
            ]
            window_start = min(
                (int(start) for start, end in offsets if int(end) > int(start)),
                default=0,
            )
            window_end = max(
                (int(end) for start, end in offsets if int(end) > int(start)),
                default=0,
            )
            chunks.append(
                {
                    "input_ids": list(item.ids),
                    "attention_mask": list(item.attention_mask),
                    "offset_mapping": offsets,
                    "window_start": window_start,
                    "window_end": window_end,
                    "is_document_end": window_end >= len(text),
                }
            )
        return chunks

    def _predict_chunk(self, text: str, chunk: dict[str, Any]) -> list[Any]:
        if self._session is None or self._np is None:
            raise RuntimeError("ONNX session is not loaded")
        np = self._np
        input_names = {item.name for item in self._session.get_inputs()}
        input_ids = chunk.get("input_ids")
        if input_ids is None:
            raise RuntimeError("ONNX graph requires input_ids")
        sequence_length = len(input_ids)
        ort_inputs: dict[str, Any] = {}
        for name in input_names:
            if name == "input_ids":
                ort_inputs[name] = np.asarray([input_ids], dtype=np.int64)
            elif name == "attention_mask":
                ort_inputs[name] = np.asarray(
                    [chunk.get("attention_mask", [1] * sequence_length)],
                    dtype=np.int64,
                )
            elif name == "position_ids":
                ort_inputs[name] = np.arange(sequence_length, dtype=np.int64)[None, :]
            elif name in chunk:
                ort_inputs[name] = np.asarray([chunk[name]], dtype=np.int64)
        outputs = self._session.run(None, ort_inputs)
        logits = next((value for value in outputs if getattr(value, "ndim", 0) == 3), None)
        if logits is None:
            raise RuntimeError("ONNX graph returned no rank-3 token logits")
        logits = logits[0]
        logits = logits - np.max(logits, axis=-1, keepdims=True)
        probabilities = np.exp(logits)
        probabilities /= np.sum(probabilities, axis=-1, keepdims=True)
        labels = np.argmax(probabilities, axis=-1)
        scores = np.max(probabilities, axis=-1)
        rows: list[tuple[int, int, str | None, float, str]] = []
        for offset, label_id, score in zip(chunk["offset_mapping"], labels.tolist(), scores.tolist()):
            start, end = int(offset[0]), int(offset[1])
            if end <= start:
                continue
            label = self._id2label.get(int(label_id), "O")
            entity_type, prefix = self._parse_label(label)
            if entity_type is None:
                rows.append((start, end, None, float(score), "O"))
                continue
            threshold = self._threshold(entity_type)
            rows.append((start, end, entity_type if score >= threshold else None, float(score), prefix))
        return self._rows_to_spans(
            text,
            rows,
            allow_leading=not bool(chunk.get("window_start", 0)),
            flush_trailing=bool(chunk.get("is_document_end", True)),
        )
