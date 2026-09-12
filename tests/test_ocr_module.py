from pathlib import Path

from ocr.cli import _build_parser
from ocr.config import load_config
from ocr.merger import merge_results, write_merged_output
from ocr.safety import safe_id


def test_ocr_cli_uses_project_config_by_default() -> None:
    args = _build_parser().parse_args([])

    assert args.config == "config/ocr.yaml"


def test_ocr_config_separates_input_and_output_directories() -> None:
    config = load_config("config/ocr.yaml")

    assert config.app.root_dir.endswith("test-artifacts/ocr-inputs")
    assert config.output.directory.endswith("test-artifacts/ocr-outputs")
    assert "{safe_id}" in config.output.merged_filename_template


def test_ocr_merge_hides_project_and_source_names(tmp_path: Path) -> None:
    project_root = tmp_path / "private-project"
    source = project_root / "confidential-report.md"
    source.parent.mkdir()
    source.write_text("正文", encoding="utf-8")

    merged = merge_results("private-project", project_root, [(source, "正文")])
    output = write_merged_output(
        "private-project",
        tmp_path / "outputs",
        merged,
        load_config("config/ocr.yaml").output,
    )

    assert "private-project" not in merged
    assert "confidential-report.md" not in merged
    assert output.name == f"document-{safe_id('private-project')}.ocr.md"
