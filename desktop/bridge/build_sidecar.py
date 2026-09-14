"""Build the production Python JSON Lines sidecar with PyInstaller."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import argparse
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DESKTOP_ROOT = PROJECT_ROOT / "desktop"
DIST_ROOT = DESKTOP_ROOT / "sidecar-dist"
WORK_ROOT = DESKTOP_ROOT / "sidecar-build"
SPEC_ROOT = DESKTOP_ROOT / "sidecar-spec"


def _has_qwen_checkpoint(model_path: str | None = None) -> bool:
    candidates = []
    explicit = model_path or os.environ.get("WENVEIL_QWEN_MODEL_PATH")
    if explicit:
        candidates.append(Path(explicit).expanduser())
    candidates.extend((PROJECT_ROOT / "models" / "qwen3-1.7b-pii", DESKTOP_ROOT / "models" / "qwen3-1.7b-pii"))
    for candidate in candidates:
        if candidate.is_dir() and (candidate / "config.json").is_file() and any(
            candidate.glob(pattern) for pattern in ("*.safetensors", "pytorch_model*.bin", "*.pt", "*.pth")
        ):
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Wenveil Python sidecar")
    parser.add_argument("--qwen-model", default=os.environ.get("WENVEIL_QWEN_MODEL_PATH"))
    parser.add_argument(
        "--require-qwen",
        action="store_true",
        help="require a complete local Qwen checkpoint instead of building the baseline sidecar",
    )
    args = parser.parse_args()

    has_qwen = _has_qwen_checkpoint(args.qwen_model)
    if args.require_qwen and not has_qwen:
        raise SystemExit(
            "完整模型发布需要有效的 Qwen checkpoint：请提供包含 config.json 与权重文件的目录。"
        )

    output_dir = DIST_ROOT / "wenveil-sidecar"
    if DIST_ROOT.exists():
        shutil.rmtree(DIST_ROOT)
    if WORK_ROOT.exists():
        shutil.rmtree(WORK_ROOT)
    if SPEC_ROOT.exists():
        shutil.rmtree(SPEC_ROOT)

    separator = os.pathsep
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--console",
        "--name",
        "wenveil-sidecar",
        "--distpath",
        str(DIST_ROOT),
        "--workpath",
        str(WORK_ROOT),
        "--specpath",
        str(SPEC_ROOT),
        "--paths",
        str(PROJECT_ROOT),
        "--add-data",
        f"{PROJECT_ROOT / 'config'}{separator}config",
        "--add-data",
        f"{PROJECT_ROOT / 'rules'}{separator}rules",
        "--add-data",
        f"{PROJECT_ROOT / 'desensitize' / 'config'}{separator}desensitize/config",
        "--add-data",
        f"{PROJECT_ROOT / 'desensitize' / 'rules'}{separator}desensitize/rules",
        str(DESKTOP_ROOT / "bridge" / "sidecar.py"),
    ]
    if not has_qwen:
        # ModelNERRecognizer imports these lazily.  Keep the no-model baseline
        # small and fast; a real checkpoint automatically gets the full stack.
        command[command.index(str(DESKTOP_ROOT / "bridge" / "sidecar.py")):command.index(str(DESKTOP_ROOT / "bridge" / "sidecar.py"))] = [
            "--exclude-module", "torch",
            "--exclude-module", "transformers",
            "--exclude-module", "torchvision",
        ]
        print("No complete Qwen checkpoint found; excluding optional model runtime.")
    build_env = os.environ.copy()
    if args.qwen_model:
        build_env["WENVEIL_QWEN_MODEL_PATH"] = str(Path(args.qwen_model).expanduser().resolve())
    subprocess.run(command, cwd=PROJECT_ROOT, env=build_env, check=True)
    executable = output_dir / ("wenveil-sidecar.exe" if os.name == "nt" else "wenveil-sidecar")
    if not executable.is_file():
        raise FileNotFoundError(f"PyInstaller did not produce {executable}")
    print(f"Built {executable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
