"""Minimal Qwen3.5 token-classification adapter.

Transformers releases do not all expose a native ``Qwen3_5ForTokenClassification``
class.  The adapter therefore prefers that class when it is available and falls
back to ``AutoModel`` plus a PyTorch token-classification head.  The fallback is
still a token classifier: it never generates or edits source text.

The fallback checkpoint is self-contained.  It stores the Qwen3.5 config, the
backbone/head state dictionary, and the label mapping so a best checkpoint can
be reloaded without access to the original model directory.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


METADATA_FILE = "wenveil_token_classifier.json"
WEIGHTS_FILE = "pytorch_model.bin"


def _hidden_size(config: Any) -> int:
    value = getattr(config, "hidden_size", None)
    if value is None:
        text_config = getattr(config, "text_config", None)
        value = getattr(text_config, "hidden_size", None)
    if value is None:
        raise ValueError("Qwen3.5 configuration has no hidden_size")
    return int(value)


def native_qwen35_token_classification_available(transformers: Any) -> bool:
    """Return whether the installed Transformers exposes the native class."""

    return hasattr(transformers, "Qwen3_5ForTokenClassification")


def _metadata(
    *,
    num_labels: int,
    id2label: Mapping[int, str],
    label2id: Mapping[str, int],
    native: bool,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "model_family": "Qwen3.5",
        "architecture": "native-token-classification" if native else "text-backbone-linear-head",
        "num_labels": int(num_labels),
        "id2label": {str(int(key)): str(value) for key, value in id2label.items()},
        "label2id": {str(key): int(value) for key, value in label2id.items()},
    }


def make_fallback_model(torch: Any, transformers: Any) -> type:
    """Create the optional-dependency model class without importing torch eagerly."""

    TokenClassifierOutput = transformers.modeling_outputs.TokenClassifierOutput

    class Qwen35TokenClassifier(torch.nn.Module):
        """Qwen3.5 text backbone with a token-classification head."""

        def __init__(
            self,
            backbone: Any,
            *,
            num_labels: int,
            id2label: Mapping[int, str],
            label2id: Mapping[str, int],
        ) -> None:
            super().__init__()
            self.backbone = backbone
            self.dropout = torch.nn.Dropout(0.1)
            self.classifier = torch.nn.Linear(_hidden_size(backbone.config), int(num_labels))
            self.num_labels = int(num_labels)
            self.id2label = {int(key): str(value) for key, value in id2label.items()}
            self.label2id = {str(key): int(value) for key, value in label2id.items()}
            # Trainer, export tooling, and downstream inspection use config as
            # the public label metadata surface.
            self.config = backbone.config
            self.config.num_labels = self.num_labels
            self.config.id2label = dict(self.id2label)
            self.config.label2id = dict(self.label2id)

        def forward(
            self,
            input_ids: Any = None,
            attention_mask: Any = None,
            labels: Any = None,
            position_ids: Any = None,
            **_: Any,
        ) -> Any:
            backbone_kwargs: dict[str, Any] = {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
                "use_cache": False,
            }
            if position_ids is not None:
                backbone_kwargs["position_ids"] = position_ids
            output = self.backbone(**backbone_kwargs)
            hidden = output.last_hidden_state
            hidden = self.dropout(hidden).to(dtype=self.classifier.weight.dtype)
            logits = self.classifier(hidden)
            loss = None
            if labels is not None:
                loss = torch.nn.functional.cross_entropy(
                    logits.reshape(-1, self.num_labels),
                    labels.reshape(-1),
                    ignore_index=-100,
                )
            return TokenClassifierOutput(
                loss=loss,
                logits=logits,
                hidden_states=getattr(output, "hidden_states", None),
                attentions=getattr(output, "attentions", None),
            )

        def save_pretrained(self, save_directory: str | Path, **_: Any) -> None:
            destination = Path(save_directory)
            destination.mkdir(parents=True, exist_ok=True)
            torch.save(self.state_dict(), destination / WEIGHTS_FILE)
            self.config.save_pretrained(destination)
            metadata = _metadata(
                num_labels=self.num_labels,
                id2label=self.id2label,
                label2id=self.label2id,
                native=False,
            )
            (destination / METADATA_FILE).write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

    Qwen35TokenClassifier.__name__ = "Qwen35TokenClassifier"
    return Qwen35TokenClassifier


def _read_metadata(path: Path) -> dict[str, Any] | None:
    metadata_path = path / METADATA_FILE
    if not metadata_path.is_file():
        return None
    raw = json.loads(metadata_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("model_family") != "Qwen3.5":
        raise ValueError(f"invalid token-classifier metadata: {metadata_path}")
    return raw


def write_checkpoint_metadata(model: Any, save_directory: str | Path) -> None:
    """Add self-contained config/label metadata around a Trainer checkpoint."""

    destination = Path(save_directory)
    destination.mkdir(parents=True, exist_ok=True)
    config = getattr(model, "config", None)
    if config is None or not hasattr(model, "backbone"):
        return
    config.save_pretrained(destination)
    id2label = getattr(model, "id2label", getattr(config, "id2label", {}))
    label2id = getattr(model, "label2id", getattr(config, "label2id", {}))
    metadata = _metadata(
        num_labels=int(getattr(model, "num_labels", len(id2label))),
        id2label=id2label,
        label2id=label2id,
        native=False,
    )
    (destination / METADATA_FILE).write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _label_maps(
    metadata: Mapping[str, Any] | None,
    *,
    num_labels: int | None = None,
    id2label: Mapping[int, str] | None = None,
    label2id: Mapping[str, int] | None = None,
) -> tuple[int, dict[int, str], dict[str, int]]:
    if metadata is not None:
        raw_id2label = metadata.get("id2label", {})
        raw_label2id = metadata.get("label2id", {})
        resolved_id2label = {int(key): str(value) for key, value in raw_id2label.items()}
        resolved_label2id = {str(key): int(value) for key, value in raw_label2id.items()}
        resolved_num_labels = int(metadata.get("num_labels", len(resolved_id2label)))
        return resolved_num_labels, resolved_id2label, resolved_label2id
    resolved_id2label = {int(key): str(value) for key, value in (id2label or {}).items()}
    resolved_label2id = {str(key): int(value) for key, value in (label2id or {}).items()}
    resolved_num_labels = int(num_labels or len(resolved_id2label))
    return resolved_num_labels, resolved_id2label, resolved_label2id


def load_token_classifier_from_base(
    model_path: str | Path,
    *,
    torch: Any,
    transformers: Any,
    num_labels: int,
    id2label: Mapping[int, str],
    label2id: Mapping[str, int],
    local_files_only: bool = True,
) -> Any:
    """Load a native Qwen3.5 token classifier or the minimal fallback."""

    path = Path(model_path)
    if native_qwen35_token_classification_available(transformers):
        native_cls = transformers.Qwen3_5ForTokenClassification
        return native_cls.from_pretrained(
            path,
            num_labels=num_labels,
            id2label=dict(id2label),
            label2id=dict(label2id),
            local_files_only=local_files_only,
            ignore_mismatched_sizes=True,
        )

    backbone = transformers.AutoModel.from_pretrained(
        path,
        local_files_only=local_files_only,
    )
    model_cls = make_fallback_model(torch, transformers)
    return model_cls(
        backbone,
        num_labels=num_labels,
        id2label=id2label,
        label2id=label2id,
    )


def load_token_classifier_checkpoint(
    checkpoint_path: str | Path,
    *,
    torch: Any,
    transformers: Any,
    local_files_only: bool = True,
) -> Any:
    """Reload a self-contained fallback or a native HF checkpoint."""

    path = Path(checkpoint_path)
    metadata = _read_metadata(path) if path.is_dir() else None
    if metadata is not None:
        config = transformers.AutoConfig.from_pretrained(
            path,
            local_files_only=local_files_only,
        )
        backbone = transformers.AutoModel.from_config(config)
        num_labels, id2label, label2id = _label_maps(metadata)
        model_cls = make_fallback_model(torch, transformers)
        model = model_cls(
            backbone,
            num_labels=num_labels,
            id2label=id2label,
            label2id=label2id,
        )
        weights_path = path / WEIGHTS_FILE
        if weights_path.is_file():
            state = torch.load(weights_path, map_location="cpu", weights_only=True)
        elif (path / "model.safetensors").is_file():
            try:
                from safetensors.torch import load_file
            except ImportError as exc:  # pragma: no cover - Transformers installs safetensors
                raise RuntimeError("safetensors is required to load this checkpoint") from exc
            state = load_file(str(path / "model.safetensors"), device="cpu")
        else:
            raise FileNotFoundError(f"token-classifier weights are missing: {path}")
        model.load_state_dict(state, strict=True)
        return model

    try:
        native = transformers.AutoModelForTokenClassification.from_pretrained(
            path,
            local_files_only=local_files_only,
        )
    except Exception as exc:
        raise RuntimeError(
            "checkpoint is not a self-contained Wenveil token classifier and "
            "could not be loaded by Transformers"
        ) from exc
    return native
