"""OCR 文本整理 CLI。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .core import OrganizeOptions, organize_path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="organize-text",
        description="整理 OCR 生成的 Markdown/纯文本，不执行脱敏",
    )
    parser.add_argument("input", type=Path, help="输入文件或目录")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("test-artifacts/organized-outputs"),
        help="输出文件或目录",
    )
    parser.add_argument(
        "--max-blank-lines",
        type=int,
        default=2,
        help="连续空行上限（默认：2）",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    if not args.input.exists():
        raise SystemExit(f"输入路径不存在: {args.input}")
    outputs = organize_path(
        args.input,
        args.output,
        OrganizeOptions(max_blank_lines=args.max_blank_lines),
    )
    print(json.dumps({"files": len(outputs), "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
