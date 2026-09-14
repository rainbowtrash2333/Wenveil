"""Assemble a reproducible Windows directory distribution."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DESKTOP_ROOT = PROJECT_ROOT / "desktop"
TAURI_RELEASE = DESKTOP_ROOT / "src-tauri" / "target" / "release"
SIDECAR_RELEASE = DESKTOP_ROOT / "sidecar-dist" / "wenveil-sidecar"
OUTPUT_ROOT = DESKTOP_ROOT / "release" / "Wenveil"


def _weight_files(path: Path) -> list[Path]:
    return [file for pattern in ("*.safetensors", "pytorch_model*.bin", "*.pt", "*.pth") for file in path.glob(pattern) if file.is_file()]


def _valid_qwen(path: Path) -> bool:
    return path.is_dir() and (path / "config.json").is_file() and bool(_weight_files(path))


def _find_qwen(explicit: str | None) -> Path | None:
    if explicit:
        candidate = Path(explicit).expanduser()
        return candidate.resolve() if _valid_qwen(candidate) else None
    candidates: list[Path] = []
    candidates.extend((PROJECT_ROOT / "models" / "qwen3-1.7b-pii", DESKTOP_ROOT / "models" / "qwen3-1.7b-pii"))
    return next((path.resolve() for path in candidates if _valid_qwen(path)), None)


def _find_ocr(explicit: str | None) -> Path | None:
    if explicit:
        candidate = Path(explicit).expanduser()
        if candidate.is_dir() and any(
            file.is_file()
            for pattern in ("*.onnx", "*.pdmodel", "*.pdiparams")
            for file in candidate.rglob(pattern)
        ):
            return candidate.resolve()
        return None
    candidates: list[Path] = []
    candidates.extend(
        (
            PROJECT_ROOT / "models" / "ocr",
            PROJECT_ROOT / "models" / "rapidocr",
            DESKTOP_ROOT / "models" / "ocr",
        )
    )
    try:
        import rapidocr

        candidates.append(Path(rapidocr.__file__).resolve().parent / "models")
    except (ImportError, AttributeError):
        pass
    for path in candidates:
        if path.is_dir() and any(
            file.is_file()
            for pattern in ("*.onnx", "*.pdmodel", "*.pdiparams")
            for file in path.rglob(pattern)
        ):
            return path.resolve()
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Assemble Wenveil Windows directory release")
    parser.add_argument("--qwen-model", default=os.environ.get("WENVEIL_QWEN_MODEL_PATH"))
    parser.add_argument("--ocr-models", default=os.environ.get("WENVEIL_OCR_MODEL_PATH"))
    parser.add_argument(
        "--require-models",
        action="store_true",
        help="require both Qwen and OCR models; fail instead of creating a partial release",
    )
    parser.add_argument("--output", type=Path, default=OUTPUT_ROOT)
    args = parser.parse_args()

    app_executable = TAURI_RELEASE / "wenveil-desktop.exe"
    sidecar_executable = SIDECAR_RELEASE / "wenveil-sidecar.exe"
    if not app_executable.is_file():
        raise FileNotFoundError(f"Tauri executable not found: {app_executable}")
    if not sidecar_executable.is_file():
        raise FileNotFoundError(f"Sidecar executable not found: {sidecar_executable}")

    qwen = _find_qwen(args.qwen_model)
    ocr = _find_ocr(args.ocr_models)
    if args.require_models and qwen is None:
        raise SystemExit(
            "完整模型发布失败：未找到有效的 Qwen checkpoint（需要 config.json 与权重文件）。"
        )
    if args.require_models and ocr is None:
        raise SystemExit(
            "完整模型发布失败：未找到有效的 RapidOCR 模型目录（需要 ONNX 或 Paddle 权重文件）。"
        )
    output_root = args.output.resolve()
    for model_path in (qwen, ocr):
        if model_path and (output_root == model_path or output_root in model_path.parents):
            raise SystemExit("发布输出目录不能位于模型源目录内。")
    if output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True)
    shutil.copy2(app_executable, output_root / "Wenveil 文隐.exe")
    shutil.copytree(SIDECAR_RELEASE, output_root / "wenveil-sidecar")
    models_root = output_root / "models"
    models_root.mkdir()
    if qwen:
        shutil.copytree(qwen, models_root / "qwen3-1.7b-pii")
    if ocr:
        shutil.copytree(ocr, models_root / "ocr")

    manifest = {
        "product": "Wenveil 文隐",
        "distribution": "windows-directory",
        "single_exe": False,
        "qwen": {"included": qwen is not None, "default_enabled": qwen is not None, "source": "build-input" if qwen else None},
        "ocr_weights": {"included": ocr is not None, "source": "build-input" if ocr else None},
        "components": {"application": "Wenveil 文隐.exe", "sidecar": "wenveil-sidecar/", "models": "models/"},
    }
    (output_root / "发布清单.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    qwen_note = "已包含，启动后默认启用。" if qwen else "未包含：未找到完整 qwen3-1.7b-pii checkpoint；需要用户提供后重新打包。"
    ocr_note = "已包含。" if ocr else "未包含独立 OCR 权重；当前 PyInstaller sidecar 仅包含已安装运行时依赖。"
    (output_root / "模型说明.txt").write_text(
        "Wenveil 文隐 Windows 目录版\n\n"
        "应用、PyInstaller onedir sidecar 与模型目录分离，不是单一 exe。\n"
        f"Qwen：{qwen_note}\nOCR 权重：{ocr_note}\n\n"
        "若需启用 Qwen，请将完整 checkpoint 放入 models/qwen3-1.7b-pii，并重新运行打包脚本；程序不会联网下载模型。\n",
        encoding="utf-8",
    )
    print(f"Packaged directory: {output_root}")
    print(f"Qwen included: {qwen is not None}")
    print(f"OCR weights included: {ocr is not None}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
