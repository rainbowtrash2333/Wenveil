# ADR-0002：OCR、文本整理与脱敏模块独立化

> 版本：V0.2（2026-09-16）｜状态：已接受
> 日期：2026-09-12
> 关联：[ARCHITECTURE.md](../ARCHITECTURE.md)、[MODULES.md](../MODULES.md)、[DEV-TOOLCHAIN.md](../DEV-TOOLCHAIN.md)

## 背景

原有 `document-converter` 工具已经具备 Docling、RapidOCR、图片压缩、批量发现和 Markdown 合并能力，
但它位于项目外部，且 OCR、文本整理和脱敏边界没有形成统一的项目模块。三类任务的依赖和安全要求不同：
OCR 需要重量级可选依赖，文本整理应保持确定性，脱敏必须独立维护可恢复模式的加密映射和审计边界。

## 决策

将能力拆分为三个可独立调用的 CLI：

1. `python -m ocr` / `ocr-convert`：PDF、DOCX、PPTX、XLSX、图片和文本转换为 Markdown。
2. `python -m organize` / `organize-text`：对 OCR Markdown/纯文本执行 Unicode、空格、断行、分页标记和 Markdown 噪声整理。
3. `python -m desensitize` / `desense`：对整理后的文本执行识别、脱敏、审计和授权恢复。

三个模块通过文件/文本契约组合，不互相导入业务实现；共享的确定性文本规整和安全 ID 放在 `common/`。
OCR 的 Docling、RapidOCR、Pillow、OpenCV 和 ONNX Runtime 仅作为 `ocr` 可选依赖。

所有模块的本地输入和输出均放在 `test-artifacts/<module>-inputs|outputs/` 下，目录不入库；生成文件名使用
安全 ID，日志不得回显原始路径、项目名或文件名。

## 理由

- OCR 依赖较重且可能需要 GPU，不能影响脱敏核心和文本整理的离线轻量调用。
- 文本整理不应承担实体识别或脱敏责任，便于单独回归和重复使用。
- 脱敏的加密 mapping、白名单、关系 Token 和恢复校验必须保持独立安全边界；无密码模式不生成 mapping。
- 文件契约支持按需跳过任一阶段，也方便后续替换 OCR 引擎或整理策略。

## 影响

- 新增 `ocr/`、`organize/` 和 `common/` 包、两个 CLI 入口及 `config/ocr.yaml`。
- `desensitize/normalizer/` 保留兼容导出，实际共享实现迁移到 `common/text_normalizer.py`。
- 安装基础包不会拉取 OCR 重依赖；需要 OCR 时使用 `pip install -e ".[ocr]"`，GPU 后端使用对应 extra。
- OCR 输出和整理输出仍可能包含用户原文内容，只能留在被忽略的 `test-artifacts/`，不得提交或直接作为未脱敏 AI 输入。

## 备选方案

1. **把 OCR 直接并入脱敏 CLI**：会强制加载重依赖，且破坏模块独立边界，不采用。
2. **保留外部工具，通过脚本调用**：无法统一配置、测试、输出安全策略和 Python 包入口，不采用。
3. **为文本整理再训练模型**：当前需求是确定性 OCR 噪声清理，模型会增加不可控改写风险，暂不采用。
