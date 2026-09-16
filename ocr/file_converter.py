"""OCR 输入前置文件转换器。

该模块只负责把 OCR 主链路不能直接处理的容器和旧版文件变成临时的
支持格式。它不识别实体、不执行脱敏，也不把中间转换文件写回用户源目录。
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import stat
import subprocess
import tempfile
import threading
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Callable, Iterable, Optional

from common.safety import safe_id

from .config import FileConverterConfig
from .formats import (
    ARCHIVE_EXTENSIONS,
    LEGACY_OFFICE_EXTENSIONS,
    MESSAGE_EXTENSIONS,
    SUPPORTED_FILE_EXTENSION_SET,
)


logger = logging.getLogger("wenveil.ocr")

_MAX_CONTAINER_DEPTH = 8
_OFFICE_LOCK = threading.RLock()


class FileConversionError(RuntimeError):
    """文件前置转换失败。"""


class ArchiveError(FileConversionError):
    """归档无法读取、展开或通过安全检查。"""


class ArchiveSecurityError(ArchiveError):
    """归档成员违反路径或链接安全约束。"""


class OfficeConversionError(FileConversionError):
    """旧版 Office 文件无法转换。"""


class MessageConversionError(FileConversionError):
    """MSG 邮件无法提取。"""


@dataclass
class _ArchiveBudget:
    """跨整个顶层输入共享的归档展开预算。"""

    max_entries: int
    max_uncompressed_bytes: int
    entries: int = 0
    uncompressed_bytes: int = 0

    def reserve(self, entries: int, size: int) -> None:
        if entries < 0 or size < 0:
            raise ArchiveError("归档元数据无效")
        if self.entries + entries > self.max_entries:
            raise ArchiveError("归档成员数量超过安全上限")
        if self.uncompressed_bytes + size > self.max_uncompressed_bytes:
            raise ArchiveError("归档展开体积超过安全上限")
        self.entries += entries
        self.uncompressed_bytes += size


@dataclass(frozen=True)
class _ListedArchiveEntry:
    name: str
    size: int
    is_directory: bool
    is_link: bool


def _is_within(path: Path, root: Path) -> bool:
    """判断解析后的路径是否仍位于指定根目录内。"""

    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _safe_member_path(root: Path, member_name: str) -> Path:
    """将归档成员名解析到 root 内，拒绝路径穿越和绝对路径。"""

    normalized = str(member_name).replace("\\", "/")
    if not normalized or "\x00" in normalized:
        raise ArchiveSecurityError("归档成员路径无效")
    if normalized.startswith("/") or PureWindowsPath(normalized).drive:
        raise ArchiveSecurityError("归档成员不得使用绝对路径")

    parts = tuple(
        part
        for part in PurePosixPath(normalized).parts
        if part not in {"", "."}
    )
    if not parts or ".." in parts:
        raise ArchiveSecurityError("归档成员路径越出解压目录")

    root_resolved = root.resolve()
    destination = (root_resolved.joinpath(*parts)).resolve()
    if not _is_within(destination, root_resolved):
        raise ArchiveSecurityError("归档成员路径越出解压目录")
    return destination


def _safe_extension(filename: object) -> str:
    """只保留一个安全的临时附件扩展名。"""

    value = str(filename or "")
    suffix = Path(value.replace("\\", "/")).suffix.lower()
    if len(suffix) > 16 or not suffix.startswith("."):
        return ""
    if not suffix[1:].replace("-", "").isalnum():
        return ""
    return suffix


class ArchiveExtractor:
    """安全展开 ZIP、RAR、7z 和常见 7-Zip 可读归档。"""

    def __init__(self, config: FileConverterConfig):
        self.config = config

    def extract(
        self,
        source: Path,
        destination: Path,
        budget: _ArchiveBudget,
    ) -> Path:
        destination.mkdir(parents=True, exist_ok=True)
        extension = source.suffix.lower()
        if extension == ".zip":
            self._extract_zip(source, destination, budget)
        elif extension in ARCHIVE_EXTENSIONS:
            self._extract_with_7z(source, destination, budget)
        else:
            raise ArchiveError("归档格式不受支持")
        self._validate_tree(destination)
        return destination

    def _extract_zip(
        self,
        source: Path,
        destination: Path,
        budget: _ArchiveBudget,
    ) -> None:
        try:
            with zipfile.ZipFile(source) as archive:
                for info in archive.infolist():
                    target = _safe_member_path(destination, info.filename)
                    is_directory = info.is_dir() or info.filename.endswith(("/", "\\"))
                    budget.reserve(1, 0 if is_directory else info.file_size)
                    if is_directory:
                        target.mkdir(parents=True, exist_ok=True)
                        continue

                    mode = (info.external_attr >> 16) & 0xFFFF
                    if stat.S_ISLNK(mode):
                        raise ArchiveSecurityError("归档符号链接不允许展开")

                    target.parent.mkdir(parents=True, exist_ok=True)
                    actual_size = 0
                    with archive.open(info, "r") as source_stream, target.open("wb") as target_stream:
                        while True:
                            chunk = source_stream.read(1024 * 1024)
                            if not chunk:
                                break
                            actual_size += len(chunk)
                            if actual_size > info.file_size:
                                raise ArchiveError("归档文件大小校验失败")
                            target_stream.write(chunk)
                    if actual_size != info.file_size:
                        raise ArchiveError("归档文件大小校验失败")
        except ArchiveError:
            raise
        except (OSError, RuntimeError, zipfile.BadZipFile):
            raise ArchiveError("ZIP 归档无法读取") from None

    def _extract_with_7z(
        self,
        source: Path,
        destination: Path,
        budget: _ArchiveBudget,
    ) -> None:
        tool = self._find_7z()
        listing = self._run_7z(
            tool,
            ["l", "-slt", "-sccUTF-8", str(source)],
        )
        entries = self._parse_listing(listing)
        expected_bytes = 0
        expected_files = 0
        for entry in entries:
            _safe_member_path(destination, entry.name)
            if entry.is_link:
                raise ArchiveSecurityError("归档链接不允许展开")
            budget.reserve(1, entry.size)
            if not entry.is_directory:
                expected_files += 1
                expected_bytes += entry.size

        self._run_7z(
            tool,
            [
                "x",
                "-y",
                "-aoa",
                "-bd",
                "-sccUTF-8",
                f"-o{destination}",
                str(source),
            ],
        )
        actual_files = [path for path in destination.rglob("*") if path.is_file()]
        actual_bytes = sum(path.stat().st_size for path in actual_files)
        if len(actual_files) > expected_files or actual_bytes > expected_bytes:
            raise ArchiveError("归档展开结果超过预检范围")

    def _find_7z(self) -> str:
        configured = self.config.archive_tool
        if configured:
            configured_path = Path(configured).expanduser()
            if configured_path.is_file():
                return str(configured_path)
            found = shutil.which(configured)
            if found:
                return found
            raise ArchiveError("未找到配置的 7-Zip 解压工具")

        for name in ("7z", "7zz", "7za"):
            found = shutil.which(name)
            if found:
                return found
        raise ArchiveError("RAR/7z 处理需要安装 7-Zip 命令行工具")

    def _run_7z(self, tool: str, arguments: list[str]) -> str:
        try:
            completed = subprocess.run(
                [tool, *arguments],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.config.archive_timeout_seconds,
                check=False,
            )
        except FileNotFoundError:
            raise ArchiveError("未找到 7-Zip 解压工具") from None
        except subprocess.TimeoutExpired:
            raise ArchiveError("归档处理超时") from None
        except OSError:
            raise ArchiveError("归档处理工具无法启动") from None
        if completed.returncode != 0:
            raise ArchiveError("归档处理失败，可能已损坏、加密或缺少解码器")
        return completed.stdout or ""

    @staticmethod
    def _parse_listing(output: str) -> list[_ListedArchiveEntry]:
        """解析 7-Zip ``-slt`` 的安全元数据，不输出原始成员名。"""

        entries: list[_ListedArchiveEntry] = []
        block: dict[str, str] = {}

        def flush() -> None:
            if not block.get("Path") or "Size" not in block:
                block.clear()
                return
            try:
                size = int(block["Size"])
            except ValueError:
                raise ArchiveError("归档条目大小无效") from None
            if size < 0:
                raise ArchiveError("归档条目大小无效")
            attributes = block.get("Attributes", "")
            is_directory = attributes.upper().startswith("D") or block["Path"].endswith(("/", "\\"))
            is_link = "L" in attributes.upper()
            entries.append(
                _ListedArchiveEntry(
                    name=block["Path"],
                    size=0 if is_directory else size,
                    is_directory=is_directory,
                    is_link=is_link,
                )
            )
            block.clear()

        for raw_line in output.splitlines():
            line = raw_line.strip("\r\n")
            if not line.strip():
                flush()
                continue
            key, separator, value = line.partition(" = ")
            if separator:
                block[key.strip()] = value
        flush()
        return entries

    @staticmethod
    def _validate_tree(root: Path) -> None:
        root_resolved = root.resolve()
        for path in root.rglob("*"):
            if os.path.islink(path):
                raise ArchiveSecurityError("归档链接不允许展开")
            if not _is_within(path.resolve(), root_resolved):
                raise ArchiveSecurityError("归档展开结果越出解压目录")


class _HtmlTextExtractor(HTMLParser):
    """无第三方 HTML 解析依赖的 MSG 正文降级提取器。"""

    _BLOCK_TAGS = {"address", "article", "br", "div", "li", "p", "section", "tr"}
    _SKIP_TAGS = {"script", "style"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
        if self._skip_depth == 0 and tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._skip_depth and tag in self._SKIP_TAGS:
            self._skip_depth -= 1
        if self._skip_depth == 0 and tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self.parts.append(data)

    def text(self) -> str:
        lines = [line.strip() for line in "".join(self.parts).splitlines()]
        return "\n".join(line for line in lines if line).strip()


def _html_to_text(value: str) -> str:
    parser = _HtmlTextExtractor()
    try:
        parser.feed(value)
        parser.close()
    except Exception:
        return value.strip()
    return parser.text()


def _as_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip()
    return str(value).strip()


def _optional_attribute(obj: object, name: str) -> str:
    try:
        return _as_text(getattr(obj, name, ""))
    except Exception:
        return ""


class OfficeConverter:
    """使用本机 Office COM（或显式配置的 LibreOffice）转换旧版 Office。"""

    _TARGETS = {
        ".doc": ".docx",
        ".xls": ".xlsx",
        ".ppt": ".pptx",
    }

    def __init__(self, config: FileConverterConfig):
        self.config = config

    def convert(self, source: Path, output_dir: Path) -> Path:
        extension = source.suffix.lower()
        target_extension = self._TARGETS.get(extension)
        if target_extension is None:
            raise OfficeConversionError("旧版 Office 格式不受支持")
        if not self.config.enabled:
            raise OfficeConversionError("文件前置转换已禁用")

        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / f"converted-{safe_id(source.name)}{target_extension}"
        with _OFFICE_LOCK:
            if self.config.office_backend == "libreoffice":
                self._convert_with_libreoffice(source, output, target_extension)
            elif self.config.office_backend == "auto":
                try:
                    self._convert_with_com(source, output, extension)
                except OfficeConversionError:
                    if not (shutil.which("soffice") or shutil.which("libreoffice")):
                        raise
                    self._convert_with_libreoffice(source, output, target_extension)
            else:
                self._convert_with_com(source, output, extension)
        if not output.is_file() or output.stat().st_size == 0:
            raise OfficeConversionError("Office 转换未生成有效文件")
        return output

    def _convert_with_com(self, source: Path, output: Path, extension: str) -> None:
        if os.name != "nt":
            raise OfficeConversionError("Office COM 转换仅支持 Windows")
        try:
            import pythoncom
            import win32com.client as win32
        except ImportError:
            raise OfficeConversionError("Office 转换需要安装 pywin32") from None

        pythoncom.CoInitialize()
        application = None
        document = None
        try:
            if extension == ".doc":
                application = win32.DispatchEx("Word.Application")
                self._configure_office_application(application)
                document = application.Documents.Open(
                    str(source),
                    ConfirmConversions=False,
                    ReadOnly=True,
                    AddToRecentFiles=False,
                    Visible=False,
                )
                self._save_word_document(document, output)
            elif extension == ".xls":
                application = win32.DispatchEx("Excel.Application")
                self._configure_office_application(application)
                document = application.Workbooks.Open(
                    str(source),
                    UpdateLinks=0,
                    ReadOnly=True,
                    AddToMru=False,
                )
                document.SaveAs(str(output), FileFormat=51)
            elif extension == ".ppt":
                application = win32.DispatchEx("PowerPoint.Application")
                self._configure_office_application(application)
                document = application.Presentations.Open(
                    str(source),
                    ReadOnly=True,
                    Untitled=False,
                    WithWindow=False,
                )
                document.SaveAs(str(output), 24)
            else:
                raise OfficeConversionError("Office 文件类型不受支持")
        except OfficeConversionError:
            raise
        except Exception:
            raise OfficeConversionError("Office 转换失败，请检查本机 Office 安装和文件内容") from None
        finally:
            if document is not None:
                with contextlib.suppress(Exception):
                    document.Close(False)
            if application is not None:
                with contextlib.suppress(Exception):
                    application.Quit()
            with contextlib.suppress(Exception):
                pythoncom.CoUninitialize()

    @staticmethod
    def _configure_office_application(application: object) -> None:
        for name, value in (
            ("Visible", False),
            ("DisplayAlerts", False),
            ("AutomationSecurity", 3),  # msoAutomationSecurityForceDisable
        ):
            with contextlib.suppress(Exception):
                setattr(application, name, value)

    @staticmethod
    def _save_word_document(document: object, output: Path) -> None:
        if hasattr(document, "SaveAs2"):
            document.SaveAs2(str(output), FileFormat=16)  # wdFormatDocumentDefault
        else:
            document.SaveAs(str(output), FileFormat=16)

    def _convert_with_libreoffice(
        self,
        source: Path,
        output: Path,
        target_extension: str,
    ) -> None:
        tool = shutil.which("soffice") or shutil.which("libreoffice")
        if not tool:
            raise OfficeConversionError("未找到 Office 或 LibreOffice 转换工具")
        profile_dir = Path(tempfile.mkdtemp(prefix="wenveil-lo-profile-", dir=str(output.parent)))
        generated = output.parent / f"{source.stem}{target_extension}"
        try:
            completed = subprocess.run(
                [
                    tool,
                    f"-env:UserInstallation={profile_dir.as_uri()}",
                    "--headless",
                    "--convert-to",
                    target_extension.lstrip("."),
                    "--outdir",
                    str(output.parent),
                    str(source),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.config.office_timeout_seconds,
                check=False,
            )
        except FileNotFoundError:
            raise OfficeConversionError("未找到 LibreOffice 转换工具") from None
        except subprocess.TimeoutExpired:
            raise OfficeConversionError("Office 转换超时") from None
        except OSError:
            raise OfficeConversionError("Office 转换工具无法启动") from None
        finally:
            shutil.rmtree(profile_dir, ignore_errors=True)
        if completed.returncode != 0 or not generated.is_file():
            raise OfficeConversionError("LibreOffice 转换失败")
        generated.replace(output)


class FileConverter:
    """将归档、旧版 Office 和 MSG 预处理后交给 OCR 支持格式转换器。"""

    def __init__(
        self,
        config: FileConverterConfig | None = None,
        *,
        archive_extractor: ArchiveExtractor | None = None,
        office_converter: OfficeConverter | None = None,
    ):
        self.config = config or FileConverterConfig()
        self.archive_extractor = archive_extractor or ArchiveExtractor(self.config)
        self.office_converter = office_converter or OfficeConverter(self.config)

    def convert(
        self,
        source: str | Path,
        convert_supported: Callable[[Path], str],
    ) -> str:
        """转换一个特殊输入，并将最终支持格式交给回调。

        Args:
            source: 原始归档、旧版 Office 或 MSG 文件。
            convert_supported: 处理 PDF/DOCX/图片/文本等支持格式的回调。
        """

        source_path = Path(source)
        if not source_path.is_file():
            raise FileConversionError("输入文件不存在或不可读取")
        if not self.config.enabled:
            raise FileConversionError("文件前置转换已禁用")

        with tempfile.TemporaryDirectory(prefix="wenveil-file-converter-") as temp_name:
            workspace = Path(temp_name)
            budget = _ArchiveBudget(
                max_entries=self.config.archive_max_entries,
                max_uncompressed_bytes=self.config.archive_max_uncompressed_bytes,
            )
            return self._convert_path(
                source_path,
                workspace,
                archive_depth=0,
                container_depth=0,
                logical_key=source_path.name,
                budget=budget,
                convert_supported=convert_supported,
            )

    def _convert_path(
        self,
        source: Path,
        workspace: Path,
        *,
        archive_depth: int,
        container_depth: int,
        logical_key: str,
        budget: _ArchiveBudget,
        convert_supported: Callable[[Path], str],
    ) -> str:
        if container_depth > _MAX_CONTAINER_DEPTH:
            return "> *[嵌套容器超过安全层数，已跳过]*\n"

        extension = source.suffix.lower()
        if extension in ARCHIVE_EXTENSIONS:
            return self._convert_archive(
                source,
                workspace,
                archive_depth=archive_depth,
                container_depth=container_depth,
                logical_key=logical_key,
                budget=budget,
                convert_supported=convert_supported,
            )
        if extension in LEGACY_OFFICE_EXTENSIONS:
            office_dir = Path(tempfile.mkdtemp(prefix="office-", dir=str(workspace)))
            converted = self.office_converter.convert(source, office_dir)
            return self._convert_path(
                converted,
                workspace,
                archive_depth=archive_depth,
                container_depth=container_depth,
                logical_key=logical_key,
                budget=budget,
                convert_supported=convert_supported,
            )
        if extension in MESSAGE_EXTENSIONS:
            return self._convert_message(
                source,
                workspace,
                archive_depth=archive_depth,
                container_depth=container_depth,
                logical_key=logical_key,
                budget=budget,
                convert_supported=convert_supported,
            )
        if extension not in SUPPORTED_FILE_EXTENSION_SET:
            raise FileConversionError("文件类型不受支持")
        return convert_supported(source)

    def _convert_archive(
        self,
        source: Path,
        workspace: Path,
        *,
        archive_depth: int,
        container_depth: int,
        logical_key: str,
        budget: _ArchiveBudget,
        convert_supported: Callable[[Path], str],
    ) -> str:
        if archive_depth >= self.config.archive_max_depth:
            return "> *[嵌套压缩包达到最大解压层数，已跳过剩余内容]*\n"

        extraction_dir = Path(
            tempfile.mkdtemp(
                prefix=f"archive-{archive_depth + 1}-",
                dir=str(workspace),
            )
        )
        self.archive_extractor.extract(source, extraction_dir, budget)
        parts: list[str] = [f"## 归档内容 {safe_id(logical_key)}", ""]
        converted_count = 0
        for index, child in enumerate(self._iter_files(extraction_dir), start=1):
            if child.suffix.lower() not in SUPPORTED_FILE_EXTENSION_SET:
                continue
            child_key = f"{logical_key}/item-{index}{child.suffix.lower()}"
            try:
                content = self._convert_path(
                    child,
                    workspace,
                    archive_depth=archive_depth + 1,
                    container_depth=container_depth,
                    logical_key=child_key,
                    budget=budget,
                    convert_supported=convert_supported,
                )
            except ArchiveError:
                raise
            except FileConversionError:
                logger.warning(
                    "归档内文件转换失败 file_id=%s item=%d",
                    safe_id(logical_key),
                    index,
                )
                content = "> *[归档内单个文件转换失败，已跳过]*\n"
            if not content or not content.strip():
                continue
            converted_count += 1
            parts.extend([
                f"### 归档文件 {converted_count} ({safe_id(child_key)})",
                "",
                content.strip(),
                "",
            ])

        if converted_count == 0:
            return "> *[压缩包中没有可转换的支持格式文件]*\n"
        return "\n".join(parts)

    @staticmethod
    def _iter_files(root: Path) -> Iterable[Path]:
        return (
            path
            for path in sorted(root.rglob("*"), key=lambda item: str(item).lower())
            if path.is_file()
        )

    def _convert_message(
        self,
        source: Path,
        workspace: Path,
        *,
        archive_depth: int,
        container_depth: int,
        logical_key: str,
        budget: _ArchiveBudget,
        convert_supported: Callable[[Path], str],
    ) -> str:
        message_dir = Path(tempfile.mkdtemp(prefix="message-", dir=str(workspace)))
        try:
            message_path, attachments = self._extract_msg(source, message_dir)
        except MessageConversionError:
            raise
        except Exception:
            raise MessageConversionError("MSG 邮件提取失败") from None

        parts = [convert_supported(message_path).strip()]
        for index, attachment in enumerate(attachments, start=1):
            extension = attachment.suffix.lower()
            if extension not in SUPPORTED_FILE_EXTENSION_SET:
                continue
            attachment_key = f"{logical_key}/attachment-{index}{extension}"
            try:
                content = self._convert_path(
                    attachment,
                    workspace,
                    archive_depth=archive_depth,
                    container_depth=container_depth + 1,
                    logical_key=attachment_key,
                    budget=budget,
                    convert_supported=convert_supported,
                )
            except ArchiveError:
                raise
            except FileConversionError:
                logger.warning(
                    "MSG 附件转换失败 file_id=%s attachment=%d",
                    safe_id(logical_key),
                    index,
                )
                content = "> *[邮件附件转换失败，已跳过]*\n"
            if content and content.strip():
                parts.extend([
                    f"### 邮件附件 {index} ({safe_id(attachment_key)})",
                    "",
                    content.strip(),
                ])
        return "\n\n".join(part for part in parts if part)

    def _extract_msg(self, source: Path, message_dir: Path) -> tuple[Path, list[Path]]:
        try:
            import extract_msg
        except ImportError:
            return self._extract_msg_with_outlook(source, message_dir)

        try:
            with extract_msg.Message(str(source)) as message:
                body = _optional_attribute(message, "body")
                if not body:
                    body = _html_to_text(_optional_attribute(message, "htmlBody"))
                message_path = self._write_message_markdown(
                    message_dir / "message.md",
                    body=body,
                    metadata=(
                        ("主题", _optional_attribute(message, "subject")),
                        ("发件人", _optional_attribute(message, "sender")),
                        ("收件人", _optional_attribute(message, "to")),
                        ("日期", _optional_attribute(message, "date")),
                    ),
                )
                attachments = (
                    self._save_extract_msg_attachments(
                        message,
                        message_dir / "attachments",
                    )
                    if self.config.msg_include_attachments
                    else []
                )
            return message_path, attachments
        except Exception:
            # 某些 MSG 由 Outlook 生成的变体需要 Outlook 自己打开，保留安全边界后再回退。
            try:
                return self._extract_msg_with_outlook(source, message_dir)
            except FileConversionError:
                raise MessageConversionError("MSG 邮件无法解析") from None

    @staticmethod
    def _write_message_markdown(
        destination: Path,
        *,
        body: str,
        metadata: tuple[tuple[str, str], ...],
    ) -> Path:
        lines = ["# 邮件正文", ""]
        for label, value in metadata:
            if value:
                lines.append(f"- {label}：{value}")
        if metadata:
            lines.append("")
        lines.append(body.strip() if body.strip() else "> *[邮件正文为空]*")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        return destination

    @staticmethod
    def _save_extract_msg_attachments(message: object, directory: Path) -> list[Path]:
        if not getattr(message, "attachments", None):
            return []
        directory.mkdir(parents=True, exist_ok=True)
        saved: list[Path] = []
        for index, attachment in enumerate(list(message.attachments), start=1):
            suffix = _safe_extension(
                _optional_attribute(attachment, "longFilename")
                or _optional_attribute(attachment, "shortFilename")
                or _optional_attribute(attachment, "name")
            )
            target = directory / f"attachment-{index}{suffix}"
            try:
                attachment.save(
                    customPath=str(directory),
                    customFilename=target.name,
                    extractEmbedded=True,
                )
            except Exception:
                logger.warning("MSG 附件提取失败 attachment=%d", index)
                continue
            if target.is_file():
                saved.append(target)
        return saved

    def _extract_msg_with_outlook(
        self,
        source: Path,
        message_dir: Path,
    ) -> tuple[Path, list[Path]]:
        if os.name != "nt":
            raise MessageConversionError("MSG 提取需要 extract-msg 或 Windows Outlook")
        try:
            import pythoncom
            import win32com.client as win32
        except ImportError:
            raise MessageConversionError("MSG 提取需要安装 extract-msg 或 pywin32") from None

        pythoncom.CoInitialize()
        outlook = None
        message = None
        attachment_paths: list[Path] = []
        try:
            outlook = win32.DispatchEx("Outlook.Application")
            namespace = outlook.GetNamespace("MAPI")
            message = namespace.OpenSharedItem(str(source))
            body = _optional_attribute(message, "Body")
            message_path = self._write_message_markdown(
                message_dir / "message.md",
                body=body,
                metadata=(
                    ("主题", _optional_attribute(message, "Subject")),
                    ("发件人", _optional_attribute(message, "SenderName")),
                    ("收件人", _optional_attribute(message, "To")),
                    ("抄送", _optional_attribute(message, "CC")),
                    ("日期", _optional_attribute(message, "ReceivedTime")),
                ),
            )
            attachments = getattr(message, "Attachments", None)
            if self.config.msg_include_attachments and attachments is not None:
                directory = message_dir / "attachments"
                directory.mkdir(parents=True, exist_ok=True)
                for index in range(1, int(attachments.Count) + 1):
                    item = attachments.Item(index)
                    suffix = _safe_extension(_optional_attribute(item, "FileName"))
                    target = directory / f"attachment-{index}{suffix}"
                    try:
                        item.SaveAsFile(str(target))
                    except Exception:
                        logger.warning("Outlook MSG 附件提取失败 attachment=%d", index)
                        continue
                    if target.is_file():
                        attachment_paths.append(target)
            return message_path, attachment_paths
        except MessageConversionError:
            raise
        except Exception:
            raise MessageConversionError("Outlook 无法打开 MSG 邮件") from None
        finally:
            if message is not None:
                with contextlib.suppress(Exception):
                    message.Close(0)
            if outlook is not None:
                with contextlib.suppress(Exception):
                    outlook.Quit()
            with contextlib.suppress(Exception):
                pythoncom.CoUninitialize()


__all__ = [
    "ARCHIVE_EXTENSIONS",
    "ArchiveError",
    "ArchiveExtractor",
    "ArchiveSecurityError",
    "FileConversionError",
    "FileConverter",
    "LEGACY_OFFICE_EXTENSIONS",
    "MessageConversionError",
    "OfficeConversionError",
    "OfficeConverter",
]
