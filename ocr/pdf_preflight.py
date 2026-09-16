"""PDF 页级轻量预检与快速扫描判定。

预检只读取 PDF 的文本对象和图片对象元数据，不渲染页面，也不保留页面文本。
它用于在进入 Docling 前判断是否存在足够有效的文本层，以及文档是否适合
走逐页 RapidOCR 快速路径。
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_MIN_VALID_TEXT_CHARS = 64
DEFAULT_SCAN_BITMAP_THRESHOLD = 0.6
DEFAULT_SCAN_PAGE_RATIO = 0.8


@dataclass(frozen=True)
class PdfPageSignal:
    """单页预检信号，不包含原始文本。"""

    page_no: int
    width: float
    height: float
    text_chars: int
    image_count: int
    image_area_fraction: float

    def has_valid_text(self, min_chars: int) -> bool:
        """判断页面是否有足够的可用文本层。"""

        return self.text_chars >= max(1, min_chars)

    def has_large_bitmap(self, threshold: float) -> bool:
        """判断页面是否主要由一张或多张图片组成。"""

        return self.image_area_fraction >= max(0.0, threshold)


@dataclass(frozen=True)
class PdfPreflight:
    """PDF 页级预检结果。"""

    pages: tuple[PdfPageSignal, ...]
    min_valid_text_chars: int
    scan_bitmap_threshold: float

    @property
    def total_pages(self) -> int:
        return len(self.pages)

    @property
    def valid_text_pages(self) -> tuple[int, ...]:
        return tuple(
            page.page_no
            for page in self.pages
            if page.has_valid_text(self.min_valid_text_chars)
        )

    @property
    def large_bitmap_pages(self) -> tuple[int, ...]:
        return tuple(
            page.page_no
            for page in self.pages
            if page.has_large_bitmap(self.scan_bitmap_threshold)
        )

    @property
    def scan_fast_candidate(self) -> bool:
        """判断是否可以绕过 Docling 的版面/表格阶段，逐页 OCR。

        必须满足：
        1. 没有任何页面拥有足够的有效文本层；
        2. 至少 80% 页面是大面积图片；
        3. 至少存在一页大面积图片。

        少量空白页允许存在，空白页由快速路径在渲染后继续过滤。
        """

        if not self.pages or self.valid_text_pages:
            return False
        large_count = len(self.large_bitmap_pages)
        if large_count == 0:
            return False
        return large_count / len(self.pages) >= DEFAULT_SCAN_PAGE_RATIO


def meaningful_char_count(value: str) -> int:
    """统计文本层中的字母/数字/汉字等有效字符数量。

    空白、标点、替换字符和控制字符不计入，避免页码、水印或解析噪声
    被误判为完整文本层。
    """

    count = 0
    for char in value:
        if char == "\ufffd":
            continue
        category = unicodedata.category(char)
        if category.startswith(("L", "N")):
            count += 1
    return count


def _text_signal(page: Any) -> int:
    text_page = page.get_textpage()
    try:
        rect_count = int(text_page.count_rects())
        char_count = 0
        for index in range(rect_count):
            text = text_page.get_text_bounded(*text_page.get_rect(index)) or ""
            char_count += meaningful_char_count(text)
        return char_count
    finally:
        close = getattr(text_page, "close", None)
        if close is not None:
            close()


def _image_signal(page: Any, width: float, height: float) -> tuple[int, float]:
    import pypdfium2.raw as pdfium_c

    page_area = max(width * height, 1.0)
    image_count = 0
    image_area = 0.0
    for obj in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE]):
        bounds = obj.get_bounds()
        left, top, right, bottom = (float(value) for value in bounds)
        image_width = max(0.0, right - left)
        image_height = max(0.0, bottom - top)
        image_area += image_width * image_height
        image_count += 1
    return image_count, min(1.0, image_area / page_area)


def inspect_pdf(
    path: Path,
    *,
    min_valid_text_chars: int = DEFAULT_MIN_VALID_TEXT_CHARS,
    scan_bitmap_threshold: float = DEFAULT_SCAN_BITMAP_THRESHOLD,
) -> PdfPreflight:
    """读取 PDF 页面信号并释放 PDF 句柄。"""

    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(str(path))
    pages: list[PdfPageSignal] = []
    try:
        for index, page in enumerate(document, start=1):
            width = float(page.get_width())
            height = float(page.get_height())
            text_chars = _text_signal(page)
            image_count, image_area_fraction = _image_signal(page, width, height)
            pages.append(
                PdfPageSignal(
                    page_no=index,
                    width=width,
                    height=height,
                    text_chars=text_chars,
                    image_count=image_count,
                    image_area_fraction=round(image_area_fraction, 6),
                )
            )
    finally:
        close = getattr(document, "close", None)
        if close is not None:
            close()
    return PdfPreflight(
        pages=tuple(pages),
        min_valid_text_chars=min_valid_text_chars,
        scan_bitmap_threshold=scan_bitmap_threshold,
    )


def is_blank_rendered_image(
    image: Any,
    *,
    dark_fraction_threshold: float = 0.0005,
    ink_fraction_threshold: float = 0.01,
) -> bool:
    """用已渲染页面判断纯空白/极轻水印页面，避免额外渲染。

    阈值故意保守：只有几乎没有深色像素且整体没有明显前景像素时才跳过，
    不把浅色正文或含真实文字的整页扫描件误判为空白。
    """

    import numpy as np

    gray_image = image.convert("L")
    try:
        gray = np.asarray(gray_image)
        if gray.size == 0:
            return True
        dark_fraction = float(np.mean(gray < 180))
        ink_fraction = float(np.mean(gray < 245))
        return (
            dark_fraction <= dark_fraction_threshold
            and ink_fraction <= ink_fraction_threshold
        )
    finally:
        close = getattr(gray_image, "close", None)
        if close is not None:
            close()
