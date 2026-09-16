"""Command line adapter for the unified workflow service."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .api import WorkflowService
from .models import ProcessRequest, RestoreRequest, WorkflowSteps
from .runner import WorkflowError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m workflow", description="Wenveil unified offline document workflow")
    parser.add_argument("--db", type=Path, help="SQLite state database")
    sub = parser.add_subparsers(dest="command", required=True)

    process = sub.add_parser("process", help="run OCR/organize/merge/mask/audit")
    process.add_argument("inputs", nargs="+", type=Path)
    process.add_argument("-o", "--output-dir", required=True, type=Path)
    process.add_argument("--output-name", help="合并模式下使用的安全单层 Markdown 文件名")
    process.add_argument("--config", type=Path)
    process.add_argument("--entities", nargs="*", default=())
    process.add_argument("--password")
    process.add_argument("--no-ocr", action="store_true")
    process.add_argument("--no-organize", action="store_true")
    process.add_argument("--no-merge", action="store_true")
    process.add_argument("--no-mask", action="store_true")
    process.add_argument("--no-audit", action="store_true")
    process.add_argument("--ocr-mode", choices=("auto", "fast", "enhanced"), default="auto")
    process.add_argument("--device", choices=("auto", "cpu", "gpu"), default="auto")
    process.add_argument("--keep-intermediate", action="store_true")
    process.add_argument("--allow-partial", action="store_true")

    restore = sub.add_parser("restore", help="restore a masked document")
    restore.add_argument("masked", type=Path)
    restore.add_argument("mapping", type=Path)
    restore.add_argument("-o", "--output-dir", required=True, type=Path)
    restore.add_argument("--password")
    restore.add_argument("--restore-filename", action="store_true")

    resume = sub.add_parser("resume", help="resume an interrupted or waiting job")
    resume.add_argument("job_id")
    resume.add_argument("--password")

    status = sub.add_parser("status", help="show one job")
    status.add_argument("job_id")

    jobs = sub.add_parser("list", help="list recent jobs")
    jobs.add_argument("--limit", type=int, default=50)
    jobs.add_argument("--status")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    try:
        with WorkflowService(db_path=args.db) as service:
            if args.command == "process":
                steps = WorkflowSteps(
                    ocr=not args.no_ocr,
                    organize=not args.no_organize,
                    merge=not args.no_merge,
                    mask=not args.no_mask,
                    audit=not args.no_audit,
                )
                snapshot = service.process(
                    ProcessRequest(
                        inputs=tuple(args.inputs),
                        output_dir=args.output_dir,
                        steps=steps,
                        output_name=args.output_name,
                        config_path=args.config,
                        entities=tuple(args.entities),
                        ocr_mode=args.ocr_mode,
                        device=args.device,
                        retain_intermediate=args.keep_intermediate,
                        allow_partial=args.allow_partial,
                    ),
                    password=args.password,
                )
                print(json.dumps(snapshot.as_dict(), ensure_ascii=False, indent=2))
            elif args.command == "restore":
                snapshot = service.restore(
                    RestoreRequest(
                        masked_path=args.masked,
                        mapping_path=args.mapping,
                        output_dir=args.output_dir,
                        password=args.password,
                        restore_filename=args.restore_filename,
                    )
                )
                print(json.dumps(snapshot.as_dict(), ensure_ascii=False, indent=2))
            elif args.command == "resume":
                print(json.dumps(service.resume(args.job_id, password=args.password).as_dict(), ensure_ascii=False, indent=2))
            elif args.command == "status":
                print(json.dumps(service.get_status(args.job_id).as_dict(), ensure_ascii=False, indent=2))
            else:
                print(json.dumps([job.as_dict() for job in service.list_jobs(limit=args.limit, status=args.status)], ensure_ascii=False, indent=2))
    except (WorkflowError, ValueError, FileNotFoundError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
