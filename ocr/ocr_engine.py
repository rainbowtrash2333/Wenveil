"""
Docling OCR 引擎适配器模块。

此模块是 RapidOCR 与 Docling 之间的桥梁。通过实现 Docling 的插件化
OCR 引擎接口，将本地 RapidOCR 引擎无缝嵌入 Docling 的 PDF 处理流水线。

核心组件：
- RapidOcrOptions: 继承 Docling 的 OcrOptions，声明引擎类型为 "rapidocr"
- RapidOcrModel: 继承 BaseOcrModel，实现完整的 __call__ 流水线阶段
- register_rapidocr_engine(): 将自定义引擎注册到 Docling 的 OcrFactory
"""

import logging
from collections.abc import Iterable
from pathlib import Path
from typing import ClassVar, List, Literal, Optional, Type

import numpy as np
from docling_core.types.doc import BoundingBox, CoordOrigin
from docling_core.types.doc.page import BoundingRectangle, TextCell

from docling.datamodel.accelerator_options import AcceleratorOptions
from docling.datamodel.base_models import Page
from docling.datamodel.document import ConversionResult
from docling.datamodel.pipeline_options import OcrOptions
from docling.models.base_ocr_model import BaseOcrModel

from ocr.rapid_ocr import LocalOcrEngine

_log = logging.getLogger("aicanread.ocr")


class RapidOcrOptions(OcrOptions):
    """RapidOCR 引擎专属配置。

    继承 Docling 基础 OcrOptions，声明引擎类型为 "rapidocr"。

    Attributes:
        kind: OCR 引擎标识符，固定为 "rapidocr"。
        lang: OCR 语言列表，默认中文。
    """
    kind: ClassVar[Literal["rapidocr"]] = "rapidocr"

    lang: List[str] = ["ch"]
    use_dml: bool = False
    use_gpu: bool = False
    image_scale: float = 2.0   # OCR 前页面图像放大倍率（越大精度越高、耗时越长）


class RapidOcrModel(BaseOcrModel):
    """RapidOCR 引擎模型 —— Docling 流水线中的 OCR 阶段。

    继承 BaseOcrModel 获得 OCR 矩形区域计算（get_ocr_rects）和
    后处理（post_process_cells）能力。__call__ 方法在 PDF 处理
    流水线中被逐页调用，完成 OCR 识别并将文本单元格添加到页面对象。

    Docling 流水线调用约定：
        __call__(conv_res, page_batch) -> 逐页 yield 已 OCR 的 Page 对象
    """

    def __init__(
        self,
        *,
        enabled: bool,
        artifacts_path: Optional[Path],
        options: RapidOcrOptions,
        accelerator_options: AcceleratorOptions,
    ):
        """初始化 RapidOCR 引擎。

        参数签名遵循 Docling OcrFactory 通过 create_instance 传入的约定。

        Args:
            enabled: 是否启用此 OCR 引擎。
            artifacts_path: 模型资源路径（本引擎使用 RapidOCR 自动管理）。
            options: RapidOCR 配置选项。
            accelerator_options: 加速器配置（GPU/CPU）。
        """
        super().__init__(
            enabled=enabled,
            artifacts_path=artifacts_path,
            options=options,
            accelerator_options=accelerator_options,
        )
        self.options: RapidOcrOptions

        # OCR 前页面图像放大倍率（配置化：scale 越大识别精度越高，但耗时按面积比增长）
        self.scale = getattr(options, "image_scale", 2)

        if self.enabled:
            from ocr.config import OcrConfig

            ocr_config = OcrConfig(
                enabled=True,
                lang=options.lang,
                use_angle_cls=True,
                use_dml=options.use_dml,
                use_gpu=options.use_gpu,
            )
            self.ocr_engine = LocalOcrEngine(ocr_config)
            _log.info("RapidOCR 本地引擎已初始化（语言: %s）", options.lang)

    @classmethod
    def get_options_type(cls) -> Type[OcrOptions]:
        """返回此引擎对应的配置选项类。

        Docling OcrFactory 通过此方法建立 Options 类型到 Model 类的映射。

        Returns:
            RapidOcrOptions 类型。
        """
        return RapidOcrOptions

    def __call__(
        self, conv_res: ConversionResult, page_batch: Iterable[Page]
    ) -> Iterable[Page]:
        """OCR 流水线阶段入口。

        对传入的页面批次逐页执行 OCR 识别：
        1. 调用 get_ocr_rects() 获取需要 OCR 的图像区域
        2. 通过 PDF 后端渲染每个区域的高分辨率图像
        3. 调用本地 RapidOCR 引擎识别文字
        4. 将识别结果转换为 TextCell 对象
        5. 调用 post_process_cells() 合并并过滤文本单元格

        Args:
            conv_res: 当前转换结果上下文。
            page_batch: 待处理的页面迭代器。

        Yields:
            已附加 OCR 文本单元格的 Page 对象。
        """
        if not self.enabled:
            yield from page_batch
            return

        for page in page_batch:
            assert page._backend is not None
            if not page._backend.is_valid():
                yield page
                continue

            # 获取需要 OCR 处理的图像矩形区域（由位图覆盖率和配置阈值决定）
            ocr_rects = self.get_ocr_rects(page)
            all_ocr_cells: List[TextCell] = []

            for ocr_rect in ocr_rects:
                if ocr_rect.area() == 0:
                    continue

                # 从 PDF 后端渲染指定区域的高分辨率图像
                high_res_image = page._backend.get_page_image(
                    scale=self.scale, cropbox=ocr_rect
                )

                try:
                    ocr_results = self.ocr_engine.ocr(high_res_image)
                except Exception as e:
                    _log.warning(
                        "RapidOCR 对页面 %d 区域识别失败 type=%s",
                        page.page_no, type(e).__name__,
                    )
                    del high_res_image
                    continue

                # 将 RapidOCR 返回的包围框坐标转换为页面坐标系的 TextCell
                for ix, result in enumerate(ocr_results):
                    bbox = result.bbox
                    if not bbox or len(bbox) < 4:
                        continue

                    # 坐标转换：OCR 区域内的相对坐标 → 页面绝对坐标
                    # scale 是图像放大倍数，需要还原到原始页面尺寸
                    x0 = (bbox[0][0] / self.scale) + ocr_rect.l
                    y0 = (bbox[0][1] / self.scale) + ocr_rect.t
                    x1 = (bbox[2][0] / self.scale) + ocr_rect.l
                    y1 = (bbox[2][1] / self.scale) + ocr_rect.t

                    text = result.text.strip()
                    if not text:
                        continue

                    # 构建 Docling 标准的 TextCell 对象
                    cell = TextCell(
                        index=ix,
                        text=text,
                        orig=text,
                        confidence=result.confidence,
                        from_ocr=True,  # 标记为 OCR 生成
                        rect=BoundingRectangle.from_bounding_box(
                            BoundingBox.from_tuple(
                                coord=(x0, y0, x1, y1),
                                origin=CoordOrigin.TOPLEFT,
                            )
                        ),
                    )
                    all_ocr_cells.append(cell)

                del high_res_image

            # 后处理：过滤与已有程序化单元格重叠的 OCR 单元格，合并并排序
            self.post_process_cells(all_ocr_cells, page)
            yield page


def register_rapidocr_engine() -> None:
    """将 RapidOCR 引擎注册到 Docling 的 OCR 工厂。

    调用 Docling 内部的 get_ocr_factory() 获取全局工厂实例，
    将 RapidOcrModel 注册为 "rapidocr" 类别的 OCR 引擎。
    注册后，当 PdfPipelineOptions.ocr_options 被设置为
    RapidOcrOptions 实例时，Docling 将自动使用本引擎。

    多次调用安全：如果已注册则跳过。
    """
    from docling.pipeline.standard_pdf_pipeline import get_ocr_factory

    factory = get_ocr_factory(allow_external_plugins=False)
    if RapidOcrOptions not in factory.classes:
        factory.register(RapidOcrModel, "aicanread_ocr", "ocr.ocr_engine")
        _log.info("已将 RapidOCR 引擎注册到 Docling OCR 工厂")
