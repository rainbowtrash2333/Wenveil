"""Build a complete Windows directory release with bundled local models.

The output intentionally remains a directory distribution rather than a single
exe.  The script refuses to build a "full" release when either model input is
missing, so a partial release cannot be mistaken for the offline product.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from package_directory import _find_ocr, _find_qwen


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DESKTOP_ROOT = PROJECT_ROOT / "desktop"
BRIDGE_ROOT = DESKTOP_ROOT / "bridge"
PACKAGE_SCRIPT = BRIDGE_ROOT / "package_directory.py"
SIDECAR_SCRIPT = BRIDGE_ROOT / "build_sidecar.py"


def _command(name: str) -> str:
    """Resolve a Windows command while keeping subprocess invocation structured."""

    if os.name == "nt":
        resolved = shutil.which(f"{name}.cmd")
        if resolved:
            return resolved
    return shutil.which(name) or name


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a Wenveil Windows directory release with OCR and Qwen models"
    )
    parser.add_argument(
        "--qwen-model",
        default=os.environ.get("WENVEIL_QWEN_MODEL_PATH"),
        help="Qwen fine-tuned checkpoint directory; may also be set via WENVEIL_QWEN_MODEL_PATH",
    )
    parser.add_argument(
        "--ocr-models",
        default=os.environ.get("WENVEIL_OCR_MODEL_PATH"),
        help="RapidOCR model directory; may also be set via WENVEIL_OCR_MODEL_PATH",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="release directory; defaults to desktop/release/Wenveil",
    )
    args = parser.parse_args()

    qwen = _find_qwen(args.qwen_model)
    ocr = _find_ocr(args.ocr_models)
    missing = []
    if qwen is None:
        missing.append("Qwen checkpoint（config.json + 权重文件）")
    if ocr is None:
        missing.append("RapidOCR 模型目录（ONNX/Paddle 权重）")
    if missing:
        raise SystemExit("无法构建完整模型发布包，缺少：" + "、".join(missing))

    build_env = os.environ.copy()
    build_env["WENVEIL_QWEN_MODEL_PATH"] = str(qwen)
    build_env["WENVEIL_OCR_MODEL_PATH"] = str(ocr)

    subprocess.run(
        [
            sys.executable,
            str(SIDECAR_SCRIPT),
            "--qwen-model",
            str(qwen),
            "--require-qwen",
        ],
        cwd=DESKTOP_ROOT,
        env=build_env,
        check=True,
    )
    subprocess.run(
        [_command("npx"), "tauri", "build", "--no-bundle"],
        cwd=DESKTOP_ROOT,
        env=build_env,
        check=True,
    )

    package_command = [
        sys.executable,
        str(PACKAGE_SCRIPT),
        "--qwen-model",
        str(qwen),
        "--ocr-models",
        str(ocr),
        "--require-models",
    ]
    if args.output:
        package_command.extend(["--output", str(args.output)])
    subprocess.run(package_command, cwd=DESKTOP_ROOT, env=build_env, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
