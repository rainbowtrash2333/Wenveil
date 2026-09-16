from types import SimpleNamespace

from PIL import Image

from ocr.ocr_engine import needs_ocr
from ocr.pdf_preflight import (
    PdfPageSignal,
    PdfPreflight,
    is_blank_rendered_image,
    meaningful_char_count,
)
from ocr.config import OcrConfig
from ocr.rapid_ocr import _select_backend


class _Rect:
    def __init__(self, area: float) -> None:
        self._area = area

    def area(self) -> float:
        return self._area


def _page(*, text: str, bitmap_area: float) -> SimpleNamespace:
    backend = SimpleNamespace(
        is_valid=lambda: True,
        get_bitmap_rects=lambda: [_Rect(bitmap_area)] if bitmap_area else [],
    )
    return SimpleNamespace(
        _backend=backend,
        cells=[SimpleNamespace(text=text)] if text else [],
        size=SimpleNamespace(width=100.0, height=100.0),
    )


def test_meaningful_char_count_ignores_noise() -> None:
    assert meaningful_char_count("保单 A-12，\ufffd\n") == 5


def test_scan_fast_candidate_requires_no_valid_text_layer() -> None:
    scan_pages = tuple(
        PdfPageSignal(i, 100, 100, 0, 1, 0.9)
        for i in range(1, 4)
    )
    assert PdfPreflight(scan_pages, 64, 0.6).scan_fast_candidate is True

    mixed_pages = (
        PdfPageSignal(1, 100, 100, 80, 1, 0.9),
        scan_pages[1],
        scan_pages[2],
    )
    assert PdfPreflight(mixed_pages, 64, 0.6).scan_fast_candidate is False


def test_needs_ocr_prefers_valid_text_and_ignores_small_images() -> None:
    assert needs_ocr(_page(text="完整文本" * 20, bitmap_area=9000)) is False
    assert needs_ocr(_page(text="少量", bitmap_area=9000)) is True
    assert needs_ocr(_page(text="少量", bitmap_area=100)) is False
    assert needs_ocr(_page(text="少量", bitmap_area=0)) is False


def test_blank_rendered_image_is_conservative() -> None:
    assert is_blank_rendered_image(Image.new("RGB", (100, 100), "white")) is True

    image = Image.new("RGB", (100, 100), "white")
    for x in range(100):
        image.putpixel((x, 50), (0, 0, 0))
    assert is_blank_rendered_image(image) is False


def test_backend_selection_uses_available_provider() -> None:
    config = OcrConfig(use_dml=True, use_gpu=False)
    assert _select_backend(config, {"CPUExecutionProvider"}) == "cpu"
    assert _select_backend(config, {"DmlExecutionProvider", "CPUExecutionProvider"}) == "dml"
