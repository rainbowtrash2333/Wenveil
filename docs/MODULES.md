# 模块边界（MODULES）

> 版本：V0.7（2026-09-17）｜状态：生效
> 本文档定义 Wenveil（文隐）的模块职责、边界与依赖规则；分层总览见
> [ARCHITECTURE.md](./ARCHITECTURE.md)。

## 1. 模块清单

| 模块 | 职责 | 代码位置 |
|------|------|----------|
| Common | 共享确定性文本规整与安全 ID，不识别实体 | `common/` |
| OCR | 文档/图片/文本转换为 Markdown，页级预检、纯扫描快速路径、Docling + RapidOCR；可选安全性能 profile | `ocr/` |
| File Converter | OCR 前置转换：旧版 Office、MSG、ZIP/RAR/7z；临时目录、递归深度和归档预算控制 | `ocr/file_converter.py` |
| Organize | OCR Markdown/纯文本整理，不执行识别或脱敏 | `organize/` |
| Desensitize CLI | 参数解析、安全文件名、mask/restore/inspect/audit/benchmark；密码可选 | `desensitize/cli.py` |
| Pipeline | 编排规整、识别、白名单嵌套过滤、解析、替换和报告 | `desensitize/pipeline.py` |
| Normalizer | Unicode、OCR 空格、断行及结构字段规整 | `common/text_normalizer.py`、`desensitize/normalizer/` |
| Recognizers | 规则、词典、机构关系，以及 Qwen3.5 Transformers/ONNX Runtime 候选识别 | `desensitize/recognizers/` |
| Resolver | 确定性冲突消解与保护 Span 选择 | `desensitize/resolver.py` |
| Mapping | compact Token、可恢复模式 AES-GCM 映射、哈希校验和恢复 | `desensitize/mapping.py` |
| Config/Rules | YAML 配置、实体词典、关系注册表、公共机构白名单 | `config/`、`rules/` |
| Audit | masked 文档残留与 Markdown 结构只读审计 | `desensitize/audit.py` |
| Workflow | 统一 OCR、整理、合并、脱敏、审计、恢复、SQLite 状态和 checkpoint | `workflow/` |
| Desktop UI/Sidecar | 文件选择、设置、进度和结果展示；JSON Lines 适配 `WorkflowService` | `desktop/` |
| Training | 合成数据、标签校验、OCR 增强、评估和可选模型训练 | `training/` |
| Tests | 单元与集成回归 | `tests/` |

## 2. 各模块职责

- CLI 只负责交互与文件落盘，不实现实体识别规则。
- OCR、Organize、Desensitize 各自有独立 CLI；模块之间通过 Markdown/纯文本文件契约组合。
- OCR 前置转换只属于 `ocr/`：旧版 `.doc/.xls/.ppt` 转为 `.docx/.xlsx/.pptx`，`.msg` 转为 Markdown，
  `.zip/.rar/.7z` 及常见归档最多递归展开 3 层，然后统一进入现有 OCR 处理器。
- OCR PDF 先按页判断有效文本层和图片覆盖率；纯扫描 PDF 可走逐页 RapidOCR 快速路径，混合/复杂 PDF 保留 Docling 结构化路径。
- OCR 的 Docling/RapidOCR 依赖属于可选依赖，不能在导入脱敏或整理模块时强制加载。
- 每个功能模块只保留一个编排入口；Recognizer 不得自行改写文本或写文件。
- Normalizer 只做确定性规整，不判断业务实体。
- Resolver 不读取配置文件或 mapping，只对 Span 进行确定性选择。
- Mapping 不重新识别实体；有密码时生成的 mapping 必须加密并绑定 masked/normalized 哈希；无密码脱敏不落盘 mapping 且不可恢复。
- Training 不进入生产运行链路，生产包不得依赖可选训练框架。
- ONNX recognizer 只依赖 `tokenizers`、`numpy` 和 ONNX Runtime；不导入 PyTorch、训练代码或 checkpoint optimizer 状态。
- Audit 不回显命中的敏感文本，只输出类别、行号和固定摘要。
- Workflow 只做跨模块编排和状态持久化，不把文本内容写入 SQLite；中间文本只写入应用私有 checkpoint，成功后清理。
- Workflow 的密码只存在调用栈中；数据库、事件、日志和快照不写入密码、原文、Token 映射值或原始文件名。
- File Converter 的 Office COM、`extract-msg` 和 7-Zip 均为 OCR 前置依赖；中间文件只写入任务临时目录，
  不写回原始目录，不将归档成员原名写入日志或输出标题。
- Desktop UI/Sidecar 不实现实体识别、冲突解析或映射恢复规则；前端只传递文件、可选密码和设置，并展示可恢复/不可恢复结果。浏览器 HTTP 适配器仅监听 loopback，Tauri 负责桌面窗口和 Sidecar 生命周期。

## 3. 依赖规则（强制）

- `ocr/cli → ocr/pipeline`、`organize/cli → organize/core`、`desensitize/cli → desensitize/pipeline`；禁止跨功能模块反向依赖。
- `ocr/file_converter → common/safety` 以及可选的 Office COM、`extract-msg`、7-Zip 进程；前置层只能回调 OCR
  已支持格式处理器，不得调用脱敏或整理业务实现。
- `desktop/bridge → workflow/api`；`workflow → ocr/organize/desensitize/common`；桌面端不得反向修改或复制业务实现。
- 三个功能模块可依赖 `common/`；禁止 `ocr/`、`organize/` 依赖 `desensitize/pipeline`。
- `Recognizers → models.Span`；Recognizer 之间不得互相改写结果，通过 Pipeline 汇合。
- `OnnxNERRecognizer → tokenizers/onnxruntime`；模型输出仍必须经过 whitelist filter、Resolver、Mapping 和 Audit。
- `training → desensitize.models` 可接受；`desensitize → training` 禁止。
- 生产代码不得读取 `test-artifacts/desensitization-inputs/`、`test-artifacts/desensitization-outputs/` 或原始用户资料作为隐式配置。
- 白名单、机构关系和词典必须来自配置/规则文件，不得散落硬编码在业务流程。

**评审时按本节判定依赖违规。**

## 4. 关键测试目标

- Normalizer：幂等、OCR 空格/断行、结构字段修复。
- Recognizers/Resolver：边界、优先级、白名单、别名与重叠实体。
- Mapping：有密码时加密、篡改、密码错误、Token 碰撞、完全恢复；无密码时不生成 mapping 且只验证 masked 输出。
- Audit：PII 形态、Token 完整性和 Markdown 表格结构。
- Desktop：JSON Lines 请求校验、文件/目录输入、步骤组合、进度与恢复流程；浏览器开发模式用 Playwright 验收，Tauri 用 Cargo 检查原生桥接。
- Workflow：SQLite migration、状态机、阶段重试、checkpoint 哈希校验、取消、恢复和敏感字段边界；见 `tests/test_workflow_integration.py`。
- Training：标签无损、文档级切分、增强一致性、可选依赖延迟加载。

## 5. 构建与测试

```powershell
python -m compileall -q common desensitize ocr organize training workflow
pytest -q
```
