"""
文档转换器模块。

根据文件扩展名将转换请求路由到对应的处理策略。支持四种转换路径：
1. Docling 转换 — PDF/DOCX/PPTX/XLSX 使用 Docling 库 + 本地 RapidOCR
2. 图片 OCR    — JPG/PNG/BMP/TIFF/GIF 使用本地 RapidOCR 引擎识别
3. 文本直读    — TXT/MD/RTF 直接读取文件内容
4. 代码/数据   — HTML/XML/JSON 包裹为 Markdown 代码块
5. CSV 表格    — 解析为 Markdown 表格
"""

import csv
import gc
import io
import logging
from pathlib import Path
from typing import Optional

from PIL import Image

from ocr.config import Config
from ocr.rapid_ocr import LocalOcrEngine, compress_image
from ocr.safety import safe_id

logger = logging.getLogger("wenveil.ocr")

# ─── 扩展名分类 ────────────────────────────────────────────────

IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".gif",
}
"""图片文件扩展名 —— 使用本地 RapidOCR 引擎进行文字识别。"""

TEXT_EXTENSIONS = {
    ".txt", ".md", ".rtf",
}
"""纯文本文件扩展名 —— 直接读取文件内容不做转换。"""

CODE_EXTENSIONS = {
    ".html", ".htm", ".xml", ".json",
}
"""代码/结构数据文件扩展名 —— 包裹在 Markdown 代码块中。"""

DOCLING_EXTENSIONS = {
    ".pdf", ".docx", ".pptx", ".xlsx",
}
"""文档文件扩展名 —— 使用 Docling 库进行格式转换，PDF 的 OCR 使用本地 RapidOCR 后端。"""


class DocumentConverter:
    """文档转换器 —— 按文件类型路由到对应处理器。

    初始化时创建 Docling 转换器实例和本地 OCR 引擎。
    convert() 方法根据文件扩展名自动选择处理策略。

    Attributes:
        config: 全局配置对象。
        docling_converter: Docling 库的 DocumentConverter 实例。
        ocr_engine: 本地 RapidOCR 引擎实例。
    """

    def __init__(self, config: Config):
        """初始化文档转换器。

        Args:
            config: 全局配置对象。
        """
        self.config = config
        # Docling 很重；只有遇到 PDF/DOCX/PPTX/XLSX 时才初始化。
        self.docling_converter = None
        # 创建本地 OCR 引擎（用于图片文件的 OCR 识别）
        self.ocr_engine = LocalOcrEngine(config.ocr)

    def _create_docling_converter(self):
        """创建并配置 Docling 转换器。

        通过 Docling 的工厂注册系统将本地 RapidOCR 引擎注入 PDF 处理流水线。
        构造 PdfPipelineOptions 并指定 RapidOcrOptions 为 OCR 配置，
        然后仅对 PDF 格式应用这些选项（DOCX/PPTX/XLSX 不涉及 OCR）。

        Returns:
            配置完成的 Docling DocumentConverter 实例。
        """
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import (
            PdfPipelineOptions,
            TableFormerMode,
            TableStructureOptions,
        )
        from docling.document_converter import DocumentConverter as Dc, PdfFormatOption

        # 选择 PDF 解析后端。docling_parse 在部分 PDF 上会抛 std::bad_alloc
        # （C++ 解析器分配失败），可切换 pypdfium2（pdfium 内核）绕开。
        if self.config.docling.pdf_backend == "pypdfium2":
            from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
            pdf_backend = PyPdfiumDocumentBackend
        else:
            from docling.backend.docling_parse_backend import DoclingParseDocumentBackend
            pdf_backend = DoclingParseDocumentBackend

        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = self.config.ocr.enabled
        pipeline_options.allow_external_plugins = False

        # 内存敏感参数从配置读取（默认保持原值），
        # 降低 images_scale 可显著减少预处理阶段的内存峰值
        pipeline_options.images_scale = self.config.docling.images_scale
        pipeline_options.ocr_batch_size = 1
        pipeline_options.layout_batch_size = 1
        pipeline_options.queue_max_size = self.config.docling.queue_max_size
        pipeline_options.accelerator_options.num_threads = self.config.docling.num_threads

        # 单文档超时：防止个别异常文档（如 TableFormer 极端慢路径）卡死整个流水线
        if self.config.docling.document_timeout:
            pipeline_options.document_timeout = self.config.docling.document_timeout

        # 表格识别模式：accurate 质量高但个别文档极慢；fast 更快但结构略粗
        table_mode = str(self.config.docling.table_mode).lower()
        pipeline_options.table_structure_options = TableStructureOptions(
            do_cell_matching=True,
            mode=TableFormerMode.FAST if table_mode == "fast" else TableFormerMode.ACCURATE,
        )

        if self.config.ocr.enabled:
            from ocr.ocr_engine import RapidOcrOptions, register_rapidocr_engine

            # 将自定义 RapidOCR 引擎注册到 Docling 的 OCR 工厂中
            register_rapidocr_engine()

            # 指定使用 RapidOCR 作为 PDF 流水线的 OCR 后端
            pipeline_options.ocr_options = RapidOcrOptions(
                lang=self.config.ocr.lang,
                use_dml=self.config.ocr.use_dml,
                use_gpu=self.config.ocr.use_gpu,
                image_scale=self.config.ocr.image_scale,
            )
            logger.info(
                "Docling 已配置使用本地 RapidOCR 后端（语言: %s）",
                self.config.ocr.lang,
            )
        else:
            logger.info("文档转换未启用 OCR")

        # 仅对 PDF 格式应用自定义流水线选项（其他格式不需要 OCR）
        format_options = {
            InputFormat.PDF: PdfFormatOption(
                pipeline_options=pipeline_options,
                backend=pdf_backend,
            ),
        }

        return Dc(format_options=format_options)

    def convert(self, filepath: Path) -> Optional[str]:
        """转换单个文件为 Markdown 文本。

        根据文件扩展名自动选择转换策略。转换失败时返回错误信息字符串
        而非抛出异常，确保单个文件失败不影响批量处理。

        Args:
            filepath: 待转换文件的路径。

        Returns:
            文件的 Markdown 表示。转换失败时返回带有错误信息的文本。
        """
        ext = filepath.suffix.lower()
        logger.info("正在转换 file_id=%s", safe_id(filepath.name))

        try:
            if ext in DOCLING_EXTENSIONS:
                return self._convert_with_docling(filepath)
            elif ext in IMAGE_EXTENSIONS:
                return self._convert_image(filepath)
            elif ext in TEXT_EXTENSIONS:
                return self._convert_text(filepath)
            elif ext in CODE_EXTENSIONS:
                return self._convert_code(filepath)
            elif ext == ".csv":
                return self._convert_csv(filepath)
            else:
                # 未分类的扩展名作为纯文本处理
                return self._convert_text(filepath)
        except Exception as e:
            logger.error(
                "转换失败 file_id=%s type=%s",
                safe_id(filepath.name), type(e).__name__,
            )
            return "> *[转换失败，详见安全日志摘要]*\n"

    def _convert_with_docling(self, filepath: Path) -> str:
        """使用 Docling 库转换文档文件。

        将 PDF/DOCX/PPTX/XLSX 文件通过 Docling 转换为结构化文档，
        然后导出为 Markdown。PDF 文件中的扫描图像将由本地 RapidOCR 处理。

        Args:
            filepath: 文档文件路径。

        Returns:
            Markdown 文本。

        Raises:
            RuntimeError: docling 未安装时抛出。
        """
        if self.docling_converter is None:
            self.docling_converter = self._create_docling_converter()

        result = self.docling_converter.convert(str(filepath))
        markdown = result.document.export_to_markdown()
        gc.collect()
        return markdown

    def _convert_image(self, filepath: Path) -> str:
        """使用本地 RapidOCR 识别图片中的文字。

        打开图片文件，通过本地 OCR 引擎进行文字检测和识别，
        将结果按行整理为 Markdown 输出。

        Args:
            filepath: 图片文件路径。

        Returns:
            包含 OCR 文本的 Markdown 字符串。
        """
        image = Image.open(filepath)
        image = compress_image(image, self.config.ocr.max_image_size)

        try:
            ocr_results = self.ocr_engine.ocr(image)
        except Exception:
            logger.warning("OCR 对 file_id=%s 识别失败，返回占位内容", safe_id(filepath.name))
            file_id = safe_id(filepath.name)
            return f"![document-{file_id}](document-{file_id})\n\n*[OCR 不可用]*\n"

        if not ocr_results:
            file_id = safe_id(filepath.name)
            return f"![document-{file_id}](document-{file_id})\n\n*[未检测到文字]*\n"

        lines = [f"### 图片: document-{safe_id(filepath.name)}\n"]
        for result in ocr_results:
            if result.text.strip():
                lines.append(f"{result.text}")
        lines.append("")
        return "\n".join(lines)

    def _convert_text(self, filepath: Path) -> str:
        """直接读取纯文本文件内容。

        优先使用配置的编码，失败时回退到 latin-1。

        Args:
            filepath: 文本文件路径。

        Returns:
            文件文本内容。
        """
        try:
            return filepath.read_text(encoding=self.config.output.encoding)
        except UnicodeDecodeError:
            return filepath.read_text(encoding="latin-1")

    def _convert_code(self, filepath: Path) -> str:
        """将代码/数据文件包裹为 Markdown 代码块。

        根据扩展名自动选择语言标注（html/xml/json），
        便于 Markdown 渲染器进行语法高亮。

        Args:
            filepath: 代码文件路径。

        Returns:
            带有语言标注的 Markdown 代码块字符串。
        """
        content = self._convert_text(filepath)
        ext = filepath.suffix.lstrip(".").lower()
        lang_map = {
            "html": "html", "htm": "html",
            "xml": "xml", "json": "json",
        }
        language = lang_map.get(ext, "")
        return f"```{language}\n{content}\n```\n"

    def _convert_csv(self, filepath: Path) -> str:
        """将 CSV 文件转换为 Markdown 表格。

        第一行作为表头，后续行作为数据行。自动补齐
        列数不一致的空缺。

        Args:
            filepath: CSV 文件路径。

        Returns:
            Markdown 表格字符串。空文件返回提示文本。
        """
        content = filepath.read_text(encoding=self.config.output.encoding)
        reader = csv.reader(io.StringIO(content))
        rows = list(reader)
        if not rows:
            return "*[空 CSV 文件]*\n"

        md_lines = []
        # 第一行为表头
        header = rows[0]
        md_lines.append("| " + " | ".join(header) + " |")
        md_lines.append("| " + " | ".join("---" for _ in header) + " |")
        # 后续行为数据
        for row in rows[1:]:
            padded = row + [""] * (len(header) - len(row))
            md_lines.append("| " + " | ".join(padded[:len(header)]) + " |")
        return "\n".join(md_lines) + "\n"
