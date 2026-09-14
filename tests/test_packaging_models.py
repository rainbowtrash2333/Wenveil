from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType

from desktop.bridge import package_directory
from desktop.bridge.package_directory import _find_ocr, _find_qwen
from ocr.config import OcrConfig
from ocr.rapid_ocr import LocalOcrEngine


def test_qwen_checkpoint_requires_config_and_weight(tmp_path: Path) -> None:
    checkpoint = tmp_path / "qwen3-1.7b-pii"
    checkpoint.mkdir()
    (checkpoint / "config.json").write_text("{}", encoding="utf-8")

    assert _find_qwen(str(checkpoint)) is None

    (checkpoint / "model.safetensors").write_bytes(b"synthetic-weight")
    assert _find_qwen(str(checkpoint)) == checkpoint.resolve()


def test_ocr_model_directory_requires_supported_weight(tmp_path: Path) -> None:
    model_dir = tmp_path / "ocr"
    model_dir.mkdir()
    assert _find_ocr(str(model_dir)) is None

    (model_dir / "det.onnx").write_bytes(b"synthetic-weight")
    assert _find_ocr(str(model_dir)) == model_dir.resolve()


def test_local_ocr_passes_external_model_root(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class FakeRapidOCR:
        def __init__(self, *, params):
            captured.update(params)

    fake_module = ModuleType("rapidocr")
    fake_module.RapidOCR = FakeRapidOCR
    monkeypatch.setitem(sys.modules, "rapidocr", fake_module)

    model_dir = tmp_path / "ocr"
    model_dir.mkdir()
    engine = LocalOcrEngine(OcrConfig(model_dir=str(model_dir)))
    engine._get_engine()

    assert captured["Global.model_root_dir"] == str(model_dir)


def test_directory_packager_copies_both_models(tmp_path: Path, monkeypatch) -> None:
    tauri_release = tmp_path / "tauri-release"
    sidecar_release = tmp_path / "sidecar-release"
    output = tmp_path / "release"
    tauri_release.mkdir()
    sidecar_release.mkdir()
    (tauri_release / "wenveil-desktop.exe").write_bytes(b"app")
    (sidecar_release / "wenveil-sidecar.exe").write_bytes(b"sidecar")
    (sidecar_release / "_internal").mkdir()
    (sidecar_release / "_internal" / "config.yaml").write_text("safe", encoding="utf-8")

    qwen = tmp_path / "qwen"
    qwen.mkdir()
    (qwen / "config.json").write_text("{}", encoding="utf-8")
    (qwen / "model.safetensors").write_bytes(b"qwen")
    ocr = tmp_path / "ocr"
    ocr.mkdir()
    (ocr / "det.onnx").write_bytes(b"ocr")

    monkeypatch.setattr(package_directory, "TAURI_RELEASE", tauri_release)
    monkeypatch.setattr(package_directory, "SIDECAR_RELEASE", sidecar_release)
    monkeypatch.setattr(package_directory, "OUTPUT_ROOT", output)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "package_directory.py",
            "--qwen-model",
            str(qwen),
            "--ocr-models",
            str(ocr),
            "--require-models",
        ],
    )

    assert package_directory.main() == 0
    assert (output / "models" / "qwen3-1.7b-pii" / "model.safetensors").is_file()
    assert (output / "models" / "ocr" / "det.onnx").is_file()
    manifest = json.loads((output / "发布清单.json").read_text(encoding="utf-8"))
    assert manifest["single_exe"] is False
    assert manifest["qwen"]["included"] is True
    assert manifest["ocr_weights"]["included"] is True
