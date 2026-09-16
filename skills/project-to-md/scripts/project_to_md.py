"""Run the unified workflow for every project below an input directory.

The script deliberately keeps orchestration small: document conversion and
state persistence remain in ``workflow.WorkflowService``.  It only discovers
project folders, validates the supported-file boundary, and invokes one merge
job per project.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from common.safety import safe_id  # noqa: E402
from ocr.formats import (  # noqa: E402
    SUPPORTED_FILE_EXTENSION_SET,
    TEXT_EXTENSIONS as OCR_TEXT_EXTENSIONS,
)
from workflow import ProcessRequest, WorkflowService, WorkflowSteps  # noqa: E402
from workflow.runner import WorkflowError  # noqa: E402


SUPPORTED_EXTENSIONS = SUPPORTED_FILE_EXTENSION_SET
TEXT_EXTENSIONS = OCR_TEXT_EXTENSIONS
DEFAULT_IGNORED_DIRS = frozenset({"merged", ".wenveil"})


@dataclass(frozen=True, slots=True)
class ProjectResult:
    """Safe, serializable summary for one project."""

    project_id: str
    status: str
    job_id: str | None
    file_count: int
    unsupported_count: int
    output_path: str | None = None
    error_code: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "projectId": self.project_id,
            "status": self.status,
            "jobId": self.job_id,
            "fileCount": self.file_count,
            "unsupportedCount": self.unsupported_count,
            "outputPath": self.output_path,
            "errorCode": self.error_code,
        }


def _is_hidden_or_ignored(path: Path, project_root: Path) -> bool:
    relative = path.relative_to(project_root)
    return any(part.startswith(".") for part in relative.parts) or any(
        part in DEFAULT_IGNORED_DIRS for part in relative.parts
    )


def collect_project_files(
    project_root: Path,
    *,
    use_ocr: bool = True,
) -> tuple[list[Path], int]:
    """Return supported files and the count of files outside the input contract."""

    allowed = SUPPORTED_EXTENSIONS if use_ocr else TEXT_EXTENSIONS
    supported: list[Path] = []
    unsupported_count = 0
    for candidate in sorted(
        project_root.rglob("*"),
        key=lambda item: item.relative_to(project_root).as_posix().casefold(),
    ):
        if not candidate.is_file() or candidate.is_symlink():
            continue
        if _is_hidden_or_ignored(candidate, project_root):
            continue
        if candidate.name.startswith(("~$", ".~")):
            continue
        if candidate.suffix.lower() in allowed:
            supported.append(candidate)
        else:
            unsupported_count += 1
    return supported, unsupported_count


def discover_projects(root: Path) -> list[Path]:
    """Discover non-hidden first-level project directories deterministically."""

    return [
        entry
        for entry in sorted(root.iterdir(), key=lambda item: item.name.casefold())
        if entry.is_dir()
        and not entry.is_symlink()
        and not entry.name.startswith(".")
        and entry.name not in DEFAULT_IGNORED_DIRS
    ]


def _safe_output_path(output_dir: Path, project_root: Path) -> Path:
    return output_dir / f"document-{safe_id(project_root.name)}.merged.md"


def process_root(
    root: str | Path,
    *,
    output_dir: str | Path | None = None,
    db_path: str | Path | None = None,
    state_dir: str | Path | None = None,
    use_ocr: bool = True,
    organize: bool = True,
    resume: bool = False,
    allow_unsupported: bool = False,
    device: str = "auto",
    ocr_mode: str = "auto",
) -> list[ProjectResult]:
    """Process all project directories and return safe per-project summaries."""

    input_root = Path(root).expanduser().resolve()
    if not input_root.is_dir():
        raise NotADirectoryError(f"输入根路径不是目录: {input_root}")

    resolved_output = Path(output_dir or input_root / "merged").expanduser().resolve()
    resolved_state = Path(state_dir or input_root / ".wenveil").expanduser().resolve()
    resolved_db = Path(db_path or resolved_state / "workflow.sqlite3").expanduser().resolve()
    resolved_output.mkdir(parents=True, exist_ok=True)
    resolved_state.mkdir(parents=True, exist_ok=True)

    results: list[ProjectResult] = []
    projects = discover_projects(input_root)
    with WorkflowService(
        db_path=resolved_db,
        state_dir=resolved_state,
        checkpoint_root=resolved_state / "checkpoints",
    ) as service:
        for project_root in projects:
            files, unsupported_count = collect_project_files(project_root, use_ocr=use_ocr)
            output_path = _safe_output_path(resolved_output, project_root)
            project_id = safe_id(project_root.name)

            if not files:
                results.append(
                    ProjectResult(
                        project_id=project_id,
                        status="empty" if not unsupported_count else "unsupported",
                        job_id=None,
                        file_count=0,
                        unsupported_count=unsupported_count,
                        error_code="input_empty" if not unsupported_count else "unsupported_input",
                    )
                )
                continue

            if unsupported_count and not allow_unsupported:
                results.append(
                    ProjectResult(
                        project_id=project_id,
                        status="unsupported",
                        job_id=None,
                        file_count=len(files),
                        unsupported_count=unsupported_count,
                        error_code="unsupported_input",
                    )
                )
                continue

            if resume and output_path.is_file():
                results.append(
                    ProjectResult(
                        project_id=project_id,
                        status="skipped" if not unsupported_count else "partial",
                        job_id=None,
                        file_count=len(files),
                        unsupported_count=unsupported_count,
                        output_path=str(output_path),
                    )
                )
                continue

            try:
                snapshot = service.process(
                    ProcessRequest(
                        inputs=tuple(files),
                        output_dir=resolved_output,
                        output_name=output_path.name,
                        steps=WorkflowSteps(
                            ocr=use_ocr,
                            organize=organize,
                            merge=True,
                            mask=False,
                            audit=False,
                        ),
                        ocr_mode=ocr_mode,
                        device=device,
                        allow_partial=False,
                    )
                )
            except (WorkflowError, ValueError, FileNotFoundError, OSError) as error:
                results.append(
                    ProjectResult(
                        project_id=project_id,
                        status="failed",
                        job_id=None,
                        file_count=len(files),
                        unsupported_count=unsupported_count,
                        error_code=getattr(error, "code", type(error).__name__),
                    )
                )
                continue

            status = snapshot.status.value
            if unsupported_count and status in {"succeeded", "attention"}:
                status = "partial"
            results.append(
                ProjectResult(
                    project_id=project_id,
                    status=status,
                    job_id=snapshot.job_id,
                    file_count=len(files),
                    unsupported_count=unsupported_count,
                    output_path=str(output_path) if output_path.is_file() else None,
                    error_code=snapshot.last_error_code,
                )
            )

    return results


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="将输入根目录下每个项目的受支持文件转换并合并为一个 Markdown"
    )
    parser.add_argument("root", type=Path, help="包含 project_A、project_B 等一级项目目录的根目录")
    parser.add_argument("-o", "--output-dir", type=Path, help="merged 输出目录，默认是 <root>/merged")
    parser.add_argument("--db", type=Path, help="SQLite 状态库路径，默认是 <root>/.wenveil/workflow.sqlite3")
    parser.add_argument("--state-dir", type=Path, help="日志和 checkpoint 目录，默认是 <root>/.wenveil")
    parser.add_argument("--resume", action="store_true", help="已有安全 merged 文件时跳过该项目")
    parser.add_argument(
        "--allow-unsupported",
        action="store_true",
        help="仍处理受支持文件，但对含不支持文件的项目返回非零结果",
    )
    parser.add_argument("--no-ocr", action="store_true", help="仅处理 Markdown/TXT/RTF，不加载 OCR 依赖")
    parser.add_argument("--no-organize", action="store_true", help="跳过文本整理")
    parser.add_argument("--ocr-mode", choices=("auto", "fast", "enhanced"), default="auto")
    parser.add_argument("--device", choices=("auto", "cpu", "gpu"), default="auto")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        results = process_root(
            args.root,
            output_dir=args.output_dir,
            db_path=args.db,
            state_dir=args.state_dir,
            use_ocr=not args.no_ocr,
            organize=not args.no_organize,
            resume=args.resume,
            allow_unsupported=args.allow_unsupported,
            device=args.device,
            ocr_mode=args.ocr_mode,
        )
    except (FileNotFoundError, NotADirectoryError, ValueError, OSError) as error:
        print(json.dumps({"error": type(error).__name__}, ensure_ascii=False), file=sys.stderr)
        return 2

    payload = {
        "outputDir": str(Path(args.output_dir or args.root / "merged").expanduser().resolve()),
        "projectCount": len(results),
        "completedCount": sum(
            item.status in {"succeeded", "skipped"} and item.unsupported_count == 0
            for item in results
        ),
        "results": [item.as_dict() for item in results],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if results and all(
        item.status in {"succeeded", "skipped"} and item.unsupported_count == 0
        for item in results
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
