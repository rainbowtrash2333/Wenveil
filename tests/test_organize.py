from pathlib import Path

from organize.core import OrganizeOptions, TextOrganizer, organize_path


def test_organizer_repairs_ocr_spacing_and_collapses_noise() -> None:
    organizer = TextOrganizer(OrganizeOptions(max_blank_lines=1))
    source = "中国 人 民\n\n\n<!-- image -->\n第 1 页\n身份证：110105 1949 1231 002X\n"

    organized = organizer.organize_text(source)

    assert "中国人民" in organized
    assert "<!-- image -->" not in organized
    assert "第 1 页" not in organized
    assert "---" in organized
    assert "11010519491231002X" in organized
    assert "\n\n\n" not in organized


def test_organize_path_writes_safe_output_name(tmp_path: Path) -> None:
    source = tmp_path / "原始报告.md"
    source.write_text("标题\n\n正文", encoding="utf-8")

    outputs = organize_path(source, tmp_path / "organized")

    assert len(outputs) == 1
    assert outputs[0].name.startswith("document-")
    assert outputs[0].name.endswith(".organized.md")
    assert source.name not in outputs[0].name
    assert "标题" in outputs[0].read_text(encoding="utf-8")
