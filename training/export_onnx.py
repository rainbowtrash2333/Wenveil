"""Export a best Qwen3.5 token-classifier checkpoint and verify ORT parity."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable


PARITY_CASES = {
    "normal": "安澜保险集团股份有限公司委派李四负责清源投资计划。",
    "long": "正文说明。" * 80 + "新曜基金管理有限公司负责项目复核。",
    "ocr_noise": "安 澜 保 险 集 团 股 份 有 限 公 司 委 派 李 四。",
    "multi_entity": "东岳证券股份有限公司与远景银行股份有限公司签署受托合同。",
}


def _optional_dependencies() -> tuple[Any, Any, Any, Any]:
    try:
        import numpy as np
        import onnx
        import onnxruntime as ort
        import torch
        import transformers
    except ImportError as exc:  # pragma: no cover - optional command dependency
        raise RuntimeError("ONNX export requires torch, transformers, onnx, and onnxruntime") from exc
    return np, onnx, ort, (torch, transformers)


def _ort_inputs(session: Any, encoded: dict[str, Any], np: Any) -> dict[str, Any]:
    names = {item.name for item in session.get_inputs()}
    input_ids = encoded["input_ids"]
    sequence_length = len(input_ids)
    result: dict[str, Any] = {}
    for name in names:
        if name == "input_ids":
            result[name] = np.asarray([input_ids], dtype=np.int64)
        elif name == "attention_mask":
            result[name] = np.asarray(
                [encoded.get("attention_mask", [1] * sequence_length)],
                dtype=np.int64,
            )
        elif name == "position_ids":
            result[name] = np.arange(sequence_length, dtype=np.int64)[None, :]
    return result


def _parity(
    model: Any,
    tokenizer: Any,
    onnx_path: Path,
    *,
    cases: Iterable[tuple[str, str]],
    torch: Any,
    np: Any,
    ort: Any,
    max_length: int,
) -> dict[str, Any]:
    session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    from desensitize.recognizers.onnx_ner import OnnxNERRecognizer

    from .model_prediction import predict_spans

    overlap = min(128, max(0, max_length // 8))
    onnx_recognizer = OnnxNERRecognizer(
        {
            "enabled": True,
            "path": str(onnx_path.parent),
            "execution_provider": "cpu",
            "chunk_size": max_length,
            "fixed_sequence_length": max_length,
            "overlap": overlap,
            "preserve_label_types": True,
        }
    )
    results: dict[str, Any] = {}
    model.eval()
    for case_name, text in cases:
        encoded = tokenizer(
            text,
            return_tensors="pt",
            truncation=True,
            max_length=max_length,
            padding="max_length",
        )
        model_inputs = {
            "input_ids": encoded["input_ids"],
            "attention_mask": encoded["attention_mask"],
        }
        model_inputs["position_ids"] = torch.arange(
            model_inputs["input_ids"].shape[1], dtype=torch.long
        ).unsqueeze(0)
        with torch.no_grad():
            torch_logits = model(**model_inputs).logits.detach().cpu().float().numpy()
        ort_logits = session.run(None, _ort_inputs(session, {key: value[0].tolist() for key, value in encoded.items()}, np))[0]
        max_abs = float(np.max(np.abs(torch_logits - ort_logits)))
        torch_labels = np.argmax(torch_logits, axis=-1).tolist()
        ort_labels = np.argmax(ort_logits, axis=-1).tolist()
        pytorch_spans = predict_spans(
            text,
            tokenizer,
            model,
            torch,
            max_length=max_length,
            overlap=overlap,
        )
        onnx_spans = onnx_recognizer.recognize(text)
        pytorch_span_keys = {
            (span.start, span.end, span.entity_type) for span in pytorch_spans
        }
        onnx_span_keys = {
            (span.start, span.end, span.entity_type) for span in onnx_spans
        }
        results[case_name] = {
            "tokens": int(torch_logits.shape[1]),
            "max_abs_logit_diff": max_abs,
            "allclose": bool(np.allclose(torch_logits, ort_logits, rtol=1e-3, atol=1e-3)),
            "token_labels_equal": torch_labels == ort_labels,
            "pytorch_span_count": len(pytorch_span_keys),
            "onnx_span_count": len(onnx_span_keys),
            "span_keys_equal": pytorch_span_keys == onnx_span_keys,
        }
    return {
        "provider": "CPUExecutionProvider",
        "cases": results,
        "allclose": all(item["allclose"] for item in results.values()),
        "token_labels_equal": all(item["token_labels_equal"] for item in results.values()),
        "span_keys_equal": all(item["span_keys_equal"] for item in results.values()),
    }


def _patch_transformers_mask_for_export(torch: Any, transformers: Any) -> None:
    """Work around Transformers 5.8 passing a scalar traced q_length tensor."""

    masking_utils = getattr(transformers, "masking_utils", None)
    if masking_utils is None:
        return
    registry = getattr(masking_utils, "ALL_MASK_ATTENTION_FUNCTIONS", None)
    mapping = getattr(registry, "_global_mapping", None)
    original = mapping.get("sdpa") if mapping is not None else None
    if original is None or getattr(original, "_wenveil_export_patch", False):
        return

    def stable_sdpa_mask(*args: Any, **kwargs: Any) -> Any:
        q_length = kwargs.get("q_length")
        if isinstance(q_length, torch.Tensor) and q_length.ndim == 0:
            kwargs["q_length"] = int(q_length.item())
        return original(*args, **kwargs)

    stable_sdpa_mask._wenveil_export_patch = True
    mapping["sdpa"] = stable_sdpa_mask


def _external_data_bytes(onnx: Any, onnx_path: Path, destination: Path) -> int:
    """Count only external tensor files referenced by the exported graph."""

    graph = onnx.load(str(onnx_path), load_external_data=False)
    locations = {
        item.value
        for initializer in graph.graph.initializer
        for item in initializer.external_data
        if item.key == "location"
    }
    return sum(
        (destination / location).stat().st_size
        for location in locations
        if (destination / location).is_file()
    )


def export_token_classifier(
    checkpoint: str | Path,
    output_dir: str | Path,
    *,
    max_length: int = 1024,
    parity_cases: Iterable[tuple[str, str]] | None = None,
    local_files_only: bool = True,
) -> dict[str, Any]:
    """Export a local best checkpoint and return a safe deployment manifest."""

    np, onnx, ort, (torch, transformers) = _optional_dependencies()
    from .qwen35_token_classifier import load_token_classifier_checkpoint

    checkpoint_path = Path(checkpoint)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    tokenizer = transformers.AutoTokenizer.from_pretrained(
        checkpoint_path,
        use_fast=True,
        local_files_only=local_files_only,
    )
    model = load_token_classifier_checkpoint(
        checkpoint_path,
        torch=torch,
        transformers=transformers,
        local_files_only=local_files_only,
    )
    model.to("cpu")
    model.float()
    model.eval()
    _patch_transformers_mask_for_export(torch, transformers)

    class LogitsOnly(torch.nn.Module):
        def __init__(self, wrapped: Any) -> None:
            super().__init__()
            self.wrapped = wrapped

        def forward(self, input_ids: Any, attention_mask: Any, position_ids: Any) -> Any:
            return self.wrapped(
                input_ids=input_ids,
                attention_mask=attention_mask,
                position_ids=position_ids,
            ).logits

    wrapper = LogitsOnly(model)
    export_sequence_length = max(32, int(max_length))
    encoded = tokenizer(
        PARITY_CASES["normal"],
        return_tensors="pt",
        truncation=True,
        max_length=export_sequence_length,
        padding="max_length",
    )
    encoded["position_ids"] = torch.arange(
        encoded["input_ids"].shape[1], dtype=torch.long
    ).unsqueeze(0)
    onnx_path = destination / "model.onnx"
    torch.onnx.export(
        wrapper,
        (encoded["input_ids"], encoded["attention_mask"], encoded["position_ids"]),
        str(onnx_path),
        input_names=["input_ids", "attention_mask", "position_ids"],
        output_names=["logits"],
        opset_version=18,
        dynamo=False,
        external_data=True,
        do_constant_folding=True,
    )
    onnx.checker.check_model(str(onnx_path))
    tokenizer.save_pretrained(destination)
    id2label = {
        str(index): str(label)
        for index, label in (getattr(getattr(model, "config", None), "id2label", {}) or {}).items()
    }
    label2id = {
        str(label): int(index)
        for label, index in (getattr(getattr(model, "config", None), "label2id", {}) or {}).items()
    }
    (destination / "label_mapping.json").write_text(
        json.dumps(
            {"schema_version": 1, "id2label": id2label, "label2id": label2id},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (destination / "runtime_config.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "model_family": "Qwen3.5",
                "backend": "onnx",
                "onnx_file": "model.onnx",
                "label_mapping": "label_mapping.json",
                "chunk_size": export_sequence_length,
                "fixed_sequence_length": export_sequence_length,
                "overlap": min(128, max(0, export_sequence_length // 8)),
                "execution_provider": "auto",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    parity = _parity(
        model,
        tokenizer,
        onnx_path,
        cases=parity_cases or PARITY_CASES.items(),
        torch=torch,
        np=np,
        ort=ort,
        max_length=export_sequence_length,
    )
    manifest = {
        "schema_version": 1,
        "model_family": "Qwen3.5",
        "checkpoint": checkpoint_path.name,
        "onnx_file": "model.onnx",
        "onnx_bytes": onnx_path.stat().st_size,
        "external_data_bytes": _external_data_bytes(onnx, onnx_path, destination),
        "label_count": len(id2label),
        "parity": parity,
    }
    (destination / "export_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="export a local Qwen3.5 NER checkpoint to ONNX")
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--max-length", type=int, default=1024)
    parser.add_argument("--allow-network", action="store_true")
    args = parser.parse_args(argv)
    result = export_token_classifier(
        args.checkpoint,
        args.output_dir,
        max_length=args.max_length,
        local_files_only=not args.allow_network,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
