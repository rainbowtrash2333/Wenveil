"""
配置管理模块。

负责从 YAML 配置文件加载所有配置项，并提供类型安全的 dataclass 封装。
配置分为五个部分：应用配置、OCR 配置、并发配置、输出配置、日志配置。
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import yaml


@dataclass
class AppConfig:
    """应用基本配置。

    Attributes:
        root_dir: 根目录路径，其下一级子目录将被识别为"项目目录"。
        supported_extensions: 支持的文件扩展名列表（含点号前缀，大小写不敏感）。
    """
    root_dir: str = "./test-artifacts/ocr-inputs"
    supported_extensions: List[str] = field(default_factory=lambda: [
        ".pdf", ".docx", ".pptx", ".xlsx",
        ".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".gif",
        ".txt", ".md", ".rtf", ".html", ".htm", ".xml", ".json", ".csv",
    ])


@dataclass
class OcrConfig:
    """本地 RapidOCR 引擎配置。

    使用本地 RapidOCR（基于 ONNX Runtime）进行 OCR 识别，
    无需 Docker 或外部 HTTP 服务。

    Attributes:
        enabled: 是否启用 OCR 识别。
        lang: OCR 语言列表，默认中文。支持 "ch"、"en" 等。
        use_angle_cls: 是否启用文字方向分类，有助于处理旋转文本。
        use_gpu: 是否使用 NVIDIA CUDA GPU 加速。
        use_dml: 是否使用 DirectML GPU 加速（AMD / Intel 集显）。
                 与 use_gpu 互斥，use_dml 优先。
        text_score: 文本识别置信度阈值，低于此值的结果将被丢弃。
        box_score: 文本检测置信度阈值。
        model_dir: RapidOCR 本地模型目录。为空时使用 RapidOCR 默认模型目录。
    """
    enabled: bool = True
    lang: List[str] = field(default_factory=lambda: ["ch"])
    use_angle_cls: bool = True
    use_gpu: bool = False
    use_dml: bool = False
    text_score: float = 0.5
    box_score: float = 0.3
    model_dir: Optional[str] = None
    max_image_size: int = 3072
    image_scale: float = 1.0   # PDF OCR 前页面图像放大倍率：越大精度越高、耗时越长（默认1.0均衡）


@dataclass
class DoclingConfig:
    """Docling PDF 流水线配置（内存敏感参数）。

    Attributes:
        images_scale: 页面位图渲染缩放比例。渲染内存与缩放值的平方成正比，
            降低该值可显著减少预处理阶段的内存峰值（std::bad_alloc 主要源于此）。
            注意：仅影响布局分析用的位图分辨率，OCR 识别仍按 2x 独立渲染。
        num_threads: Docling 推理/预处理线程数，降低可减少并发内存占用。
        queue_max_size: 页面处理队列容量，降低可限制内存中的待处理页数量。
    """
    images_scale: float = 0.5
    num_threads: int = 2
    queue_max_size: int = 10
    pdf_backend: str = "docling_parse"   # docling_parse | pypdfium2
                                          # docling_parse 在部分 PDF 上会抛 std::bad_alloc，
                                          # 切换 pypdfium2 可绕过该问题（内容解析精度相当）
    table_mode: str = "accurate"          # accurate | fast
                                          # TableFormer 表格识别的推理模式。fast 显著加速，
                                          # 但个别扫描件在 accurate 下可能陷入极慢路径
    document_timeout: Optional[float] = None  # 单文档处理超时（秒）。超时抛异常并跳过，
                                              # 防止个别异常文档卡死整个流水线


@dataclass
class ConcurrencyConfig:
    """并发处理配置。

    Attributes:
        max_workers: 线程池最大 worker 数，控制同时转换的文件数量。
        show_progress: 是否显示 tqdm 进度条。
    """
    max_workers: int = 2
    show_progress: bool = True


@dataclass
class OutputConfig:
    """输出文件配置。

    Attributes:
        directory: 合并 Markdown 的输出目录，与原始输入目录分离。
        merged_filename_template: 合并文件名模板，支持 {safe_id} 占位符。
        encoding: 输出文件的字符编码。
        file_separator: 文件间分隔符模板，保留兼容配置字段。
    """
    directory: str = "test-artifacts/ocr-outputs"
    merged_filename_template: str = "document-{safe_id}.ocr.md"
    encoding: str = "utf-8"
    file_separator: str = "\n\n----- {filename} -----\n\n"


@dataclass
class LoggingConfig:
    """日志配置。

    Attributes:
        level: 日志级别（DEBUG/INFO/WARNING/ERROR/CRITICAL）。
        format: 日志格式化字符串。
        file: 日志输出文件路径，为 None 时仅输出到控制台。
    """
    level: str = "DEBUG"
    format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    file: Optional[str] = "test-artifacts/logs/ocr.log"


@dataclass
class Config:
    """顶层配置容器，聚合所有子配置。

    Attributes:
        app: 应用基本配置。
        ocr: OCR 配置。
        concurrency: 并发配置。
        output: 输出配置。
        logging: 日志配置。
    """
    app: AppConfig = field(default_factory=AppConfig)
    ocr: OcrConfig = field(default_factory=OcrConfig)
    docling: DoclingConfig = field(default_factory=DoclingConfig)
    concurrency: ConcurrencyConfig = field(default_factory=ConcurrencyConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


def load_config(config_path: str) -> Config:
    """从 YAML 文件加载配置。

    读取指定路径的 YAML 配置文件，将各节内容反序列化为对应的 dataclass 实例。
    如果某节缺失，则使用默认值。

    Args:
        config_path: YAML 配置文件的路径。

    Returns:
        完整的 Config 对象。

    Raises:
        FileNotFoundError: 配置文件不存在时抛出。
    """
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"配置文件未找到: {config_path}")

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    return Config(
        app=AppConfig(**raw.get("app", {})),
        ocr=OcrConfig(**raw.get("ocr", {})),
        docling=DoclingConfig(**raw.get("docling", {})),
        concurrency=ConcurrencyConfig(**raw.get("concurrency", {})),
        output=OutputConfig(**raw.get("output", {})),
        logging=LoggingConfig(**raw.get("logging", {})),
    )
