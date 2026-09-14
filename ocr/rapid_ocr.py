"""
本地 RapidOCR 引擎模块。

使用 RapidOCR（基于 ONNX Runtime）在本地执行 OCR 识别，
无需 Docker 容器或外部 HTTP 服务。RapidOCR 是面向推理部署的轻量级方案，
支持 ONNX Runtime / PaddlePaddle / PyTorch / OpenVINO 多种推理后端。

核心类：
- OcrResult: OCR 识别结果数据类
- LocalOcrEngine: 本地 OCR 引擎，封装 RapidOCR 的初始化和调用
"""

import logging
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image

from ocr.config import OcrConfig

logger = logging.getLogger("wenveil.ocr")


def compress_image(image: Image.Image, max_size: int) -> Image.Image:
    """使用 OpenCV Lanczos4 对图片进行高质量压缩。

    将图片较长的边等比缩放到 max_size 以内，减少内存占用。
    采用 OpenCV 的 INTER_LANCZOS4 插值算法，保留更多文字边缘细节，
    优于 PIL 默认的 Lanczos3（6×6 核 vs 8×8 核）。

    Args:
        image: 待压缩的 PIL Image 对象。
        max_size: 最大边长（像素），长边超过此值时等比缩放。

    Returns:
        压缩后的 PIL Image 对象。若无需压缩则原样返回。
    """
    if max_size <= 0:
        return image

    w, h = image.size
    max_dim = max(w, h)
    if max_dim <= max_size:
        return image

    scale = max_size / max_dim
    new_w = int(w * scale)
    new_h = int(h * scale)

    img_bgr = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    img_bgr = cv2.resize(img_bgr, (new_w, new_h), interpolation=cv2.INTER_LANCZOS4)
    img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)

    logger.debug("图片已压缩: %s → %s (Lanczos4)", (w, h), (new_w, new_h))
    return Image.fromarray(img_rgb)


@dataclass
class OcrResult:
    """单条 OCR 识别结果。

    Attributes:
        text: 识别出的文字内容。
        confidence: 置信度，范围 0.0 ~ 1.0。
        bbox: 四点包围框坐标，格式为 [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]。
    """
    text: str
    confidence: float
    bbox: List[List[float]]

    @property
    def rectangle(self) -> Tuple[float, float, float, float]:
        """将四点包围框转换为轴对齐矩形 (x_min, y_min, x_max, y_max)。

        Returns:
            一个 (x0, y0, x1, y1) 四元组，如果 bbox 数据无效则返回全页面范围。
        """
        if not self.bbox or len(self.bbox) < 4:
            return (0.0, 0.0, 1.0, 1.0)
        xs = [p[0] for p in self.bbox]
        ys = [p[1] for p in self.bbox]
        return (min(xs), min(ys), max(xs), max(ys))


class LocalOcrEngine:
    """本地 RapidOCR 引擎。

    封装 RapidOCR（基于 ONNX Runtime 的 OCR 推理实现），
    提供统一的 OCR 接口。引擎在首次使用时自动下载模型文件。

    支持的硬件后端（通过 onnxruntime 执行提供器）：
    1. DirectML — AMD Radeon / Intel Arc 集显（需安装 onnxruntime-directml）
    2. CUDA    — NVIDIA GPU（需安装 onnxruntime-gpu + CUDA Toolkit）
    3. CPU     — 默认，跨平台免配置

    Attributes:
        enabled: 是否启用 OCR。
        engine: RapidOCR 实例（延迟初始化）。
    """

    def __init__(self, config: OcrConfig):
        """初始化本地 OCR 引擎。

        Args:
            config: OCR 配置对象，包含语言、GPU、阈值等设置。
        """
        self.enabled: bool = config.enabled
        self._config = config
        self._engine: Any = None  # 延迟初始化，避免在不启用 OCR 时加载模型

    def _get_engine(self):
        """获取或初始化 RapidOCR 引擎（懒加载）。

        首次调用时自动检测可用的推理后端并加载 OCR 模型。
        模型文件会在首次运行时自动从 ModelScope 下载到本地缓存。

        Returns:
            RapidOCR 实例。
        """
        if self._engine is not None:
            return self._engine

        if not self.enabled:
            return None

        from rapidocr import RapidOCR

        # 构建 RapidOCR 参数
        params = {
            "Global.text_score": self._config.text_score,
            "Det.box_thresh": self._config.box_score,
        }
        if self._config.model_dir:
            params["Global.model_root_dir"] = self._config.model_dir

        # 设备选择：DirectML > CUDA > CPU
        if self._config.use_dml:
            # AMD Radeon / Intel 集显 — 通过 DirectML 使用 GPU
            params["Det.use_dml"] = True
            params["Cls.use_dml"] = True
            params["Rec.use_dml"] = True
            params["Det.use_cuda"] = False
            params["Cls.use_cuda"] = False
            params["Rec.use_cuda"] = False
        elif self._config.use_gpu:
            # NVIDIA GPU — 通过 CUDA 使用 GPU
            params["Det.use_cuda"] = True
            params["Cls.use_cuda"] = True
            params["Rec.use_cuda"] = True
            params["Det.use_dml"] = False
            params["Cls.use_dml"] = False
            params["Rec.use_dml"] = False
        else:
            # CPU 推理
            params["Det.use_cuda"] = False
            params["Cls.use_cuda"] = False
            params["Rec.use_cuda"] = False
            params["Det.use_dml"] = False
            params["Cls.use_dml"] = False
            params["Rec.use_dml"] = False

        self._engine = RapidOCR(params=params)

        if self._config.use_dml:
            backend = "DirectML (AMD/Intel GPU)"
        elif self._config.use_gpu:
            backend = "CUDA (NVIDIA GPU)"
        else:
            backend = "CPU"
        logger.info(
            "本地 RapidOCR 引擎已初始化（语言: %s, 后端: %s）",
            self._config.lang, backend,
        )
        return self._engine

    def ocr(self, image: Image.Image) -> List[OcrResult]:
        """对单张图片执行本地 OCR 识别。

        将 PIL Image 转换为 numpy 数组，调用 RapidOCR 进行文字检测和识别，
        返回结构化的 OCR 结果列表。

        Args:
            image: 待识别的 PIL Image 对象。

        Returns:
            OCR 识别结果列表。如果 OCR 被禁用则返回空列表。

        Raises:
            RuntimeError: OCR 引擎初始化失败时抛出。
        """
        if not self.enabled:
            return []

        engine = self._get_engine()
        if engine is None:
            logger.warning("OCR 引擎未初始化，跳过识别")
            return []

        # PIL Image → numpy array (RapidOCR 接受 numpy 数组)
        img_array = np.array(image)

        # 调用 RapidOCR 执行文本检测 + 方向分类 + 文字识别
        try:
            result = engine(
                img_array,
                use_det=True,
                use_cls=self._config.use_angle_cls,
                use_rec=True,
            )
        except Exception as e:
            logger.error("本地 OCR 识别失败 type=%s", type(e).__name__)
            return []

        return self._parse_result(result)

    def ocr_batch(self, images: List[Image.Image]) -> List[List[OcrResult]]:
        """批量 OCR 识别。

        Args:
            images: PIL Image 列表。

        Returns:
            每张图片的 OCR 结果组成的列表。
        """
        return [self.ocr(img) for img in images]

    def _parse_result(self, result: Any) -> List[OcrResult]:
        """解析 RapidOCR 返回的识别结果。

        RapidOCR 返回 RapidOCROutput 命名元组，包含：
        - boxes: 文本框坐标 (N, 4, 2)，每个框四个角点
        - txts: 识别文本列表
        - scores: 置信度列表

        当没有检测到文字时，所有字段为 None。

        Args:
            result: RapidOCR 的 RapidOCROutput 对象。

        Returns:
            OcrResult 列表。
        """
        if result is None:
            return []

        boxes = getattr(result, "boxes", None)
        txts = getattr(result, "txts", None)
        scores = getattr(result, "scores", None)

        if boxes is None or txts is None:
            return []

        ocr_results: List[OcrResult] = []
        for i in range(len(txts)):
            text = txts[i].strip() if txts[i] else ""
            if not text:
                continue

            confidence = float(scores[i]) if scores is not None else 1.0

            # boxes[i] 是 shape (4,2) 的 numpy 数组，转为 Python list
            bbox = boxes[i].tolist() if isinstance(boxes, np.ndarray) else boxes[i]

            ocr_results.append(OcrResult(
                text=text,
                confidence=confidence,
                bbox=bbox,
            ))

        return ocr_results
