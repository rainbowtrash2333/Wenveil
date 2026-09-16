from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

from common.safety import safe_id


SCRIPT_PATH = Path(__file__).parents[1] / "skills" / "project-to-md" / "scripts" / "project_to_md.py"
SPEC = importlib.util.spec_from_file_location("project_to_md_skill", SCRIPT_PATH)
assert SPEC and SPEC.loader
project_to_md = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = project_to_md
SPEC.loader.exec_module(project_to_md)


def test_collect_project_files_is_recursive_and_skips_non_inputs(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "nested").mkdir(parents=True)
    (project / ".hidden").mkdir()
    (project / "a.md").write_text("a", encoding="utf-8")
    (project / "nested" / "b.JSON").write_text("{}", encoding="utf-8")
    (project / ".hidden" / "secret.txt").write_text("hidden", encoding="utf-8")
    (project / "data.bin").write_bytes(b"binary")
    (project / "~$lock.docx").write_bytes(b"temporary")
    (project / "legacy.doc").write_bytes(b"legacy office")
    (project / "bundle.zip").write_bytes(b"archive")

    files, unsupported_count = project_to_md.collect_project_files(project)

    assert [path.relative_to(project).as_posix() for path in files] == [
        "a.md",
        "bundle.zip",
        "legacy.doc",
        "nested/b.JSON",
    ]
    assert unsupported_count == 1


def test_process_root_creates_one_merged_markdown_per_project_and_can_resume(tmp_path: Path) -> None:
    root = tmp_path / "test_docs"
    project_a = root / "project_A"
    project_b = root / "project_B"
    project_a.mkdir(parents=True)
    project_b.mkdir(parents=True)
    (project_a / "readme.md").write_text("Project A text", encoding="utf-8")
    (project_a / "nested.txt").write_text("Nested A text", encoding="utf-8")
    (project_b / "config.txt").write_text("Project B text", encoding="utf-8")

    results = project_to_md.process_root(root, use_ocr=False)

    assert [result.status for result in results] == ["succeeded", "succeeded"]
    output_dir = root / "merged"
    output_paths = sorted(output_dir.glob("document-*.merged.md"))
    assert [path.name for path in output_paths] == [
        f"document-{safe_id('project_A')}.merged.md",
        f"document-{safe_id('project_B')}.merged.md",
    ]
    merged_text = "\n".join(path.read_text(encoding="utf-8") for path in output_paths)
    assert "Project A text" in merged_text
    assert "Project B text" in merged_text
    assert (root / ".wenveil" / "workflow.sqlite3").is_file()

    resumed = project_to_md.process_root(root, use_ocr=False, resume=True)
    assert [result.status for result in resumed] == ["skipped", "skipped"]


def test_process_root_does_not_claim_completion_when_unsupported_files_exist(tmp_path: Path) -> None:
    root = tmp_path / "test_docs"
    project = root / "project"
    project.mkdir(parents=True)
    (project / "readme.txt").write_text("supported", encoding="utf-8")
    (project / "archive.bin").write_bytes(b"not a document")

    results = project_to_md.process_root(root, use_ocr=False)

    assert len(results) == 1
    assert results[0].status == "unsupported"
    assert results[0].unsupported_count == 1
    assert not (root / "merged" / f"document-{safe_id('project')}.merged.md").exists()


def test_allow_unsupported_marks_project_partial_even_when_supported_files_finish(tmp_path: Path) -> None:
    root = tmp_path / "test_docs"
    project = root / "project"
    project.mkdir(parents=True)
    (project / "readme.txt").write_text("supported", encoding="utf-8")
    (project / "archive.bin").write_bytes(b"not a document")

    results = project_to_md.process_root(root, use_ocr=False, allow_unsupported=True)

    assert results[0].status == "partial"
    assert results[0].output_path is not None
