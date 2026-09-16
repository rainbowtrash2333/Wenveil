from __future__ import annotations

import io
import shutil
import sys
import subprocess
import types
import zipfile
from types import SimpleNamespace
from pathlib import Path

import pytest

from ocr.config import FileConverterConfig, load_config
from ocr.file_converter import (
    ArchiveError,
    ArchiveSecurityError,
    FileConverter,
)
from ocr.formats import SUPPORTED_FILE_EXTENSION_SET


def _zip_bytes(filename: str, payload: bytes) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(filename, payload)
    return stream.getvalue()


def test_new_file_formats_are_in_the_shared_ocr_allowlist() -> None:
    config = load_config("config/ocr.yaml")

    for extension in (
        ".doc", ".xls", ".ppt", ".msg",
        ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".cab", ".iso",
    ):
        assert extension in config.app.supported_extensions
        assert extension in SUPPORTED_FILE_EXTENSION_SET


def test_zip_archives_are_expanded_recursively_but_stop_at_three_layers(
    tmp_path: Path,
) -> None:
    fourth_layer = _zip_bytes("too-deep.txt", "fourth-layer".encode())
    third_layer = io.BytesIO()
    with zipfile.ZipFile(third_layer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("third.txt", "third-layer")
        archive.writestr("fourth.zip", fourth_layer)
    second_layer = _zip_bytes("third.zip", third_layer.getvalue())
    outer = tmp_path / "outer.zip"
    with zipfile.ZipFile(outer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("second.zip", second_layer)

    converted: list[Path] = []

    def convert_supported(path: Path) -> str:
        converted.append(path)
        return path.read_text(encoding="utf-8")

    result = FileConverter().convert(outer, convert_supported)

    assert "third-layer" in result
    assert "fourth-layer" not in result
    assert "最大解压层数" in result
    assert [path.suffix for path in converted] == [".txt"]


def test_zip_path_traversal_is_rejected_before_writing_outside_workspace(
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../escaped.txt", "must not be written")

    with pytest.raises(ArchiveSecurityError):
        FileConverter().convert(archive_path, lambda path: path.read_text())

    assert not (tmp_path / "escaped.txt").exists()


def test_archive_budget_is_shared_across_nested_archives(tmp_path: Path) -> None:
    archive_path = tmp_path / "many.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("one.txt", "1")
        archive.writestr("two.txt", "2")

    converter = FileConverter(FileConverterConfig(archive_max_entries=1))
    with pytest.raises(ArchiveError, match="成员数量"):
        converter.convert(archive_path, lambda path: path.read_text())


@pytest.mark.parametrize("extension", [".rar", ".7z"])
def test_rar_and_7z_use_the_configured_7zip_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    extension: str,
) -> None:
    archive_path = tmp_path / f"bundle{extension}"
    archive_path.write_bytes(b"synthetic archive")
    listing = "Path = docs/note.txt\nSize = 9\nAttributes = A....\n\n"
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        calls.append(command)
        if command[1] == "x":
            output_argument = next(value for value in command if value.startswith("-o"))
            destination = Path(output_argument[2:]) / "docs" / "note.txt"
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text("seven-zip", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout=listing if command[1] == "l" else "")

    monkeypatch.setattr(shutil, "which", lambda _name: "7z.exe")
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = FileConverter().convert(
        archive_path,
        lambda path: path.read_text(encoding="utf-8"),
    )

    assert "seven-zip" in result
    assert [command[1] for command in calls] == ["l", "x"]


def test_legacy_office_file_is_converted_before_supported_callback(tmp_path: Path) -> None:
    source = tmp_path / "legacy.doc"
    source.write_bytes(b"synthetic legacy document")

    class FakeOfficeConverter:
        def convert(self, _source: Path, output_dir: Path) -> Path:
            output = output_dir / "converted.docx"
            output.write_text("converted office text", encoding="utf-8")
            return output

    seen: list[Path] = []
    result = FileConverter(office_converter=FakeOfficeConverter()).convert(
        source,
        lambda path: seen.append(path) or path.read_text(encoding="utf-8"),
    )

    assert result == "converted office text"
    assert seen and seen[0].suffix == ".docx"


def test_msg_body_and_supported_attachments_are_converted_to_markdown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "message.msg"
    source.write_bytes(b"synthetic msg")

    class FakeAttachment:
        longFilename = "attachment.txt"
        shortFilename = "attachment.txt"
        name = "attachment.txt"

        def save(self, *, customPath: str, customFilename: str, **_kwargs: object) -> None:
            Path(customPath, customFilename).write_text("attachment body", encoding="utf-8")

    class FakeMessage:
        body = ""
        htmlBody = b"<html><body><p>message body</p></body></html>"
        subject = "subject"
        sender = "sender"
        to = "recipient"
        date = "today"
        attachments = [FakeAttachment()]

        def __init__(self, _path: str) -> None:
            pass

        def __enter__(self) -> "FakeMessage":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    monkeypatch.setitem(sys.modules, "extract_msg", types.SimpleNamespace(Message=FakeMessage))

    result = FileConverter().convert(
        source,
        lambda path: path.read_text(encoding="utf-8"),
    )

    assert "message body" in result
    assert "<p>" not in result
    assert "attachment body" in result
