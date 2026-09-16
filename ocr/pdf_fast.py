"""纯扫描 PDF 的轻量逐页 OCR 路径。

该路径只在页级预检确认 PDF 没有可复用文本层、且页面主要由扫描图片组成时启用。
它保留 RapidOCR，但跳过 Docling 的版面、表格和阅读顺序模型；复杂 PDF 仍由 Docling
负责，以避免把快速路径误用于原生文本或混合版式文档。
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

from ocr.config import Config
from ocr.pdf_preflight import PdfPreflight, is_blank_rendered_image
from ocr.profiling import DocumentProfile
from ocr.rapid_ocr import LocalOcrEngine, compress_image

logger = logging.getLogger("wenveil.ocr")


def _accelerator_is_available(config: Config) -> bool:
    """只在实际选定的 GPU provider 可用时限制为单 worker。"""

    if not (config.ocr.use_dml or config.ocr.use_gpu):
        return False
    try:
        import onnxruntime as ort

        providers = {name.lower() for name in ort.get_available_providers()}
    except Exception:
        return False
    if config.ocr.use_gpu and "cudaexecutionprovider" in providers:
        return True
    return config.ocr.use_dml and "dmlexecutionprovider" in providers


def _render_page(page: Any, width: float, height: float, scale: float) -> Any:
    """按 Docling 自定义 OCR 使用的分辨率渲染单页。"""

    from docling.utils.locks import pypdfium2_lock

    scale = max(float(scale), 0.1)
    with pypdfium2_lock:
        bitmap = page.render(scale=scale * 1.5, rotation=0)
        image = bitmap.to_pil().copy()
        bitmap.close()
    return image.resize((round(width * scale), round(height * scale)))


def convert_scanned_pdf(
    filepath: Path,
    config: Config,
    preflight: PdfPreflight,
    *,
    profile: Optional[DocumentProfile] = None,
) -> str:
    """对已确认的纯扫描 PDF 执行逐页 OCR。"""

    import pypdfium2 as pdfium

    worker_count = max(1, int(config.docling.fast_scan_workers))
    if _accelerator_is_available(config):
        # GPU provider 通常共享同一显存上下文，复制多个会话反而增加显存和初始化开销。
        worker_count = 1

    thread_state = threading.local()

    if profile is not None:
        profile.set_counter("fast_path_workers", worker_count)
        profile.set_counter("fast_path_pages", len(preflight.pages))

    def ocr_page(
        page_no: int,
        image: Any,
        page_profile: dict[str, Any],
        submitted_at: float,
    ) -> tuple[int, list[str], dict[str, Any]]:
        worker_started = time.perf_counter()
        page_profile["queue_wait_ms"] = round(
            (worker_started - submitted_at) * 1000,
            3,
        )
        engine = getattr(thread_state, "engine", None)
        if engine is None:
            engine = LocalOcrEngine(config.ocr)
            thread_state.engine = engine
        try:
            compress_started = time.perf_counter()
            image = compress_image(image, config.ocr.max_image_size)
            page_profile["image_compress_ms"] = round(
                (time.perf_counter() - compress_started) * 1000,
                3,
            )

            ocr_started = time.perf_counter()
            results = engine.ocr(image)
            page_profile["ocr_ms"] = round(
                (time.perf_counter() - ocr_started) * 1000,
                3,
            )
            page_profile["ocr_engine_init_ms"] = engine.last_initialization_ms
            texts = [result.text.strip() for result in results if result.text.strip()]
            page_profile["ocr_result_count"] = len(results)
            page_profile["ocr_result_chars"] = sum(len(text) for text in texts)
            page_profile["status"] = "success"
            return page_no, texts, page_profile
        finally:
            close = getattr(image, "close", None)
            if close is not None:
                close()

    page_texts: dict[int, list[str]] = {}
    if profile is None:
        document = pdfium.PdfDocument(str(filepath))
    else:
        with profile.stage("pdf.open"):
            document = pdfium.PdfDocument(str(filepath))
    try:
        with ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="wenveil-pdf-ocr",
        ) as executor:
            page_signals = list(preflight.pages)
            for start in range(0, len(page_signals), worker_count):
                batch = page_signals[start : start + worker_count]
                futures = []
                for signal in batch:
                    page = document[signal.page_no - 1]
                    page_started = time.perf_counter()
                    render_started = time.perf_counter()
                    image = _render_page(
                        page,
                        signal.width,
                        signal.height,
                        config.ocr.image_scale,
                    )
                    render_ms = round(
                        (time.perf_counter() - render_started) * 1000,
                        3,
                    )
                    page_profile: dict[str, Any] = {
                        "render_ms": render_ms,
                        "image_width": image.size[0],
                        "image_height": image.size[1],
                        "text_layer_chars": signal.text_chars,
                        "image_count": signal.image_count,
                        "image_area_fraction": signal.image_area_fraction,
                    }
                    blank_started = time.perf_counter()
                    blank = is_blank_rendered_image(image)
                    blank_check_ms = round(
                        (time.perf_counter() - blank_started) * 1000,
                        3,
                    )
                    page_profile["blank_check_ms"] = blank_check_ms
                    if blank:
                        page_profile["status"] = "blank"
                        page_profile["total_ms"] = round(
                            (time.perf_counter() - page_started) * 1000,
                            3,
                        )
                        page_texts[signal.page_no] = []
                        image.close()
                        if profile is not None:
                            profile.set_page_profile(signal.page_no, page_profile)
                            profile.add_stage("pdf.page_render", render_ms)
                            profile.add_stage("pdf.blank_check", blank_check_ms)
                        continue
                    submitted_at = time.perf_counter()
                    futures.append(
                        (
                            executor.submit(
                                ocr_page,
                                signal.page_no,
                                image,
                                page_profile,
                                submitted_at,
                            ),
                            signal.page_no,
                            page_profile,
                            page_started,
                        )
                    )

                for future, page_no, page_profile, page_started in futures:
                    try:
                        page_no, texts, page_profile = future.result()
                        page_texts[page_no] = texts
                    except Exception as error:
                        page_texts[page_no] = []
                        page_profile["status"] = "error"
                        page_profile["error_type"] = type(error).__name__
                    page_profile["total_ms"] = round(
                        (time.perf_counter() - page_started) * 1000,
                        3,
                    )
                    if profile is not None:
                        profile.set_page_profile(page_no, page_profile)
                        profile.add_stage(
                            "pdf.page_render",
                            page_profile.get("render_ms", 0.0),
                        )
                        profile.add_stage(
                            "pdf.blank_check",
                            page_profile.get("blank_check_ms", 0.0),
                        )
                        profile.add_stage(
                            "pdf.image_compress",
                            page_profile.get("image_compress_ms", 0.0),
                        )
                        profile.add_stage(
                            "ocr.inference",
                            page_profile.get("ocr_ms", 0.0),
                        )
                        profile.add_stage(
                            "ocr.engine_init",
                            page_profile.get("ocr_engine_init_ms", 0.0),
                        )
                        profile.add_stage(
                            "ocr.queue_wait",
                            page_profile.get("queue_wait_ms", 0.0),
                        )
                        profile.add_counter("ocr_calls", 1)
                        if page_profile.get("status") == "success":
                            profile.add_counter(
                                "ocr_results",
                                page_profile.get("ocr_result_count", 0),
                            )
                        else:
                            profile.add_counter("ocr_errors", 1)
    finally:
        close = getattr(document, "close", None)
        if close is not None:
            if profile is None:
                close()
            else:
                with profile.stage("pdf.close"):
                    close()

    if profile is None:
        lines = ["# PDF OCR", ""]
        for signal in preflight.pages:
            lines.append(f"### PDF 页面 {signal.page_no}")
            texts = page_texts.get(signal.page_no, [])
            lines.extend(texts or ["*[未检测到文字]*"])
            lines.append("")
    else:
        with profile.stage("markdown.build"):
            lines = ["# PDF OCR", ""]
            for signal in preflight.pages:
                lines.append(f"### PDF 页面 {signal.page_no}")
                texts = page_texts.get(signal.page_no, [])
                lines.extend(texts or ["*[未检测到文字]*"])
                lines.append("")
        profile.set_counter(
            "ocr_pages",
            sum(
                1
                for page_profile in profile.page_profiles.values()
                if page_profile.get("status") == "success"
            ),
        )
        profile.set_counter(
            "blank_pages",
            sum(
                1
                for page_profile in profile.page_profiles.values()
                if page_profile.get("status") == "blank"
            ),
        )
    logger.info(
        "纯扫描 PDF 使用逐页快速 OCR pages=%d workers=%d",
        len(preflight.pages), worker_count,
    )
    return "\n".join(lines)
