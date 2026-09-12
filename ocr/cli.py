"""OCR 文档转换 CLI。"""

import argparse
import sys
from pathlib import Path

from ocr.config import load_config
from ocr.logger import setup_logging


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ocr-convert",
        description="AICanRead OCR - 基于 Docling + 本地 RapidOCR 的批量文档转换工具",
    )
    parser.add_argument(
        "-c", "--config",
        default="config/ocr.yaml",
        help="YAML 配置文件路径（默认: config/ocr.yaml）",
    )
    parser.add_argument(
        "-r", "--root-dir",
        default=None,
        help="覆盖配置文件中的根目录路径",
    )
    parser.add_argument(
        "-w", "--workers",
        type=int,
        default=None,
        help="覆盖配置文件中的最大并发 worker 数",
    )
    parser.add_argument(
        "--no-ocr",
        action="store_true",
        help="完全禁用 OCR 识别",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="禁用进度条显示",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="断点续跑：保留已有输出并跳过已完成项目（长任务中断后继续）",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    """加载配置并运行 OCR 转换流水线。"""

    args = _build_parser().parse_args(argv)

    # 加载 YAML 配置文件
    try:
        config = load_config(args.config)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # 命令行参数覆盖配置文件
    if args.root_dir:
        config.app.root_dir = args.root_dir
    if args.workers is not None:
        config.concurrency.max_workers = args.workers
    if args.no_ocr:
        config.ocr.enabled = False
    if args.no_progress:
        config.concurrency.show_progress = False

    # 初始化日志系统
    logger = setup_logging(config.logging)
    logger.info("AICanRead OCR 模块启动")
    logger.info("输入目录已配置")
    logger.info("最大并发数: %d", config.concurrency.max_workers)
    logger.info("OCR 状态: %s", "启用" if config.ocr.enabled else "禁用")

    # 启动流水线
    try:
        # OCR 依赖是可选项；只有真正运行转换时才加载 Docling/Pillow/OpenCV。
        from ocr.pipeline import run_pipeline

        output_paths = run_pipeline(config, resume=args.resume)
        if output_paths:
            print("\n生成的文件:")
            for name, path in output_paths.items():
                print(f"  {name}: {path}")
            sys.exit(0)
        else:
            print("未生成任何输出文件。", file=sys.stderr)
            sys.exit(1)
    except KeyboardInterrupt:
        logger.info("用户中断执行")
        sys.exit(130)
    except Exception as e:
        logger.error("流水线执行失败 type=%s", type(e).__name__)
        sys.exit(1)


if __name__ == "__main__":
    main()
