from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import statistics
import sys
import time
from pathlib import Path

from .audit import audit_file
from .config import load_config
from .mapping import MappingVault, restore_text
from .pipeline import Desensitizer


COMMANDS = {"mask", "restore", "inspect", "benchmark", "audit"}


def _password(value: str | None, *, parser: argparse.ArgumentParser) -> str:
    password = value or os.environ.get("DESENSE_PASSWORD")
    if password:
        return password
    if sys.stdin.isatty():
        return getpass.getpass("Mapping password: ")
    parser.error("--password or DESENSE_PASSWORD is required for encrypted mappings")
    raise AssertionError("argparse.error exits")


def _config_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-c", "--config", type=Path, help="YAML configuration file")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="desense", description="OCR-aware reversible local document desensitizer")
    subparsers = parser.add_subparsers(dest="command", required=True)

    mask = subparsers.add_parser("mask", help="normalize and desensitize a Markdown document")
    mask.add_argument("input", type=Path)
    _config_arg(mask)
    mask.add_argument("-o", "--output", type=Path, default=Path("output"))
    mask.add_argument("--password", help="mapping encryption password (or DESENSE_PASSWORD)")

    restore = subparsers.add_parser("restore", help="restore a masked document")
    restore.add_argument("masked", type=Path)
    restore.add_argument("mapping", type=Path)
    restore.add_argument("-o", "--output", type=Path)
    restore.add_argument("--password", help="mapping encryption password (or DESENSE_PASSWORD)")
    restore.add_argument(
        "--restore-filename",
        action="store_true",
        help="restore the original filename from the encrypted mapping",
    )

    inspect = subparsers.add_parser("inspect", help="report detected entity counts without exposing surfaces")
    inspect.add_argument("input", type=Path)
    _config_arg(inspect)

    benchmark = subparsers.add_parser("benchmark", help="measure local processing throughput")
    benchmark.add_argument("input", type=Path)
    _config_arg(benchmark)
    benchmark.add_argument("--repeat", type=int, default=3)

    audit = subparsers.add_parser("audit", help="audit a masked Markdown document for residual PII and format damage")
    audit.add_argument("input", type=Path)
    return parser


def _mask(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    engine = Desensitizer(load_config(args.config))
    result = engine.anonymize_file(args.input)
    args.output.mkdir(parents=True, exist_ok=True)
    file_digest = hashlib.sha256(
        f"{result.vault.job_id}\0{result.vault.source_name}".encode("utf-8")
    ).hexdigest()[:12]
    stem = f"document-{file_digest}"
    normalized_path = args.output / f"{stem}.normalized.md"
    masked_path = args.output / f"{stem}.masked.md"
    mapping_path = args.output / f"{stem}.mapping.enc"
    report_path = args.output / f"{stem}.report.json"
    password = _password(args.password, parser=parser)
    normalized_path.write_text(result.normalized_text, encoding="utf-8")
    masked_path.write_text(result.masked_text, encoding="utf-8")
    result.vault.save(mapping_path, password)
    report_path.write_text(json.dumps(result.report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "normalized": str(normalized_path),
        "masked": str(masked_path),
        "mapping": str(mapping_path),
        "report": str(report_path),
        "entities": result.report["entities"],
    }, ensure_ascii=False, indent=2))


def _restore(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    password = _password(args.password, parser=parser)
    vault = MappingVault.load(args.mapping, password)
    masked = args.masked.read_text(encoding="utf-8-sig")
    restored = restore_text(masked, vault)
    output = args.output or args.masked.with_name(f"{args.masked.stem}.restored.md")
    if args.restore_filename:
        if not vault.source_name:
            parser.error("mapping does not contain an original filename")
        output = output.with_name(Path(vault.source_name).name)
    output.write_text(restored, encoding="utf-8")
    print(str(output))


def _inspect(args: argparse.Namespace) -> None:
    engine = Desensitizer(load_config(args.config))
    result = engine.anonymize_file(args.input)
    print(json.dumps(result.report, ensure_ascii=False, indent=2))


def _benchmark(args: argparse.Namespace) -> None:
    if args.repeat < 1:
        raise SystemExit("--repeat must be at least 1")
    engine = Desensitizer(load_config(args.config))
    source = args.input.read_text(encoding="utf-8-sig")
    durations: list[float] = []
    chars = len(source)
    for _ in range(args.repeat):
        started = time.perf_counter()
        engine.anonymize(source)
        durations.append((time.perf_counter() - started) * 1000)
    median_ms = statistics.median(durations)
    print(json.dumps({
        "input": str(args.input),
        "chars": chars,
        "repeat": args.repeat,
        "median_ms": round(median_ms, 3),
        "chars_per_second": round(chars / (median_ms / 1000), 2) if median_ms else None,
    }, ensure_ascii=False, indent=2))


def _audit(args: argparse.Namespace) -> None:
    issues = audit_file(args.input)
    by_category: dict[str, int] = {}
    for issue in issues:
        by_category[issue.category] = by_category.get(issue.category, 0) + 1
    print(json.dumps({
        "input": str(args.input),
        "issues": len(issues),
        "by_category": dict(sorted(by_category.items())),
        "findings": [issue.to_dict() for issue in issues],
    }, ensure_ascii=False, indent=2))
    if issues:
        raise SystemExit(1)


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        argv = ["--help"]
    elif argv[0] not in COMMANDS and not argv[0].startswith("-"):
        argv.insert(0, "mask")
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "mask":
        _mask(args, parser)
    elif args.command == "restore":
        _restore(args, parser)
    elif args.command == "inspect":
        _inspect(args)
    elif args.command == "benchmark":
        _benchmark(args)
    elif args.command == "audit":
        _audit(args)
