# 版本与能力说明（APP-VERSION）

> 版本：V0.12（2026-09-20）｜状态：生效
> 本文档记录 Wenveil（文隐）的当前版本、已实现能力、验证结果和已知限制。
> 阶段规划见 [ROADMAP.md](./ROADMAP.md)。

## 1. 当前版本

| 项 | 值 |
|----|-----|
| 版本号 | 0.2.0 |
| 公开项目名 | Wenveil（文隐） |
| Python 发行名 | `wenveil` |
| 更新时间 | 2026-09-20 |
| 对应提交 | `dev`，统一工作流、OCR 文件前置转换、项目级批量合并与显式原名模式 |

## 2. 已实现能力

| 能力 | 状态 | 证据 / 说明 |
|------|------|-------------|
| 独立 OCR 文档转换 | 已实现 | `ocr/`，移植 Docling + RapidOCR，入口为 `python -m ocr`；重依赖为可选 extra |
| OCR 文件前置转换 | 已实现（开发版） | `ocr/file_converter.py` 支持旧版 `.doc/.xls/.ppt`、`.msg`、`.zip/.rar/.7z` 及常见归档；Office 转换、MSG 提取和归档展开均在临时目录完成 |
| PDF 页级 OCR 预检与纯扫描快速路径 | 已实现 | `ocr/pdf_preflight.py`、`ocr/pdf_fast.py`；完整文本页跳过 OCR，纯扫描页跳过 Docling 版面/表格阶段 |
| OCR 全生命周期性能 profile | 已实现 | `--profile` 输出安全 JSON，覆盖文档、项目和页级阶段耗时；默认关闭，不记录原文 |
| 独立 OCR 文本整理 | 已实现 | `organize/`，入口为 `python -m organize`；不执行实体识别或脱敏 |
| OCR 文本规整 | 已实现 | `common/text_normalizer.py`，`desensitize/normalizer/` 保留兼容导出 |
| 规则、词典、机构关系识别 | 已实现 | `desensitize/recognizers/`、`rules/` |
| 机构全称、简称、子公司/分公司关系 | 已实现 | 机构注册表和 compact-v1 关系 Token |
| 公共机构白名单 | 已实现 | `rules/organization_whitelist.txt`，精确匹配并防止白名单子串放行私有机构 |
| 公开规则边界 | 已实现 | `rules/projects.txt` 及包内副本为空模板，项目专属规则只允许通过本地配置加载 |
| 人员名单重复表面传播 | 已实现 | 仅限名单/会议/部门等高置信上下文，并要求重复出现 |
| 短语义占位符 | 已实现 | `⟦人员N⟧`、`⟦机构N⟧`、`⟦机构N-别名N⟧` 等；最长 13 个字符 |
| 加密映射与完整恢复 | 已实现 | 设置密码时使用 AES-GCM、masked 哈希绑定、密码/篡改/Token 缺失校验 |
| 无密码不可逆脱敏 | 已实现 | 未设置密码仍可脱敏；只输出 masked 和安全报告，不生成 mapping |
| 审计与安全文件名 | 已实现 | audit 检查 PII、Token、表格结构；CLI 使用不可逆 `document-<safe-id>` |
| 金融领域训练与外部数据适配 | 已实现 | 合成数据兼容流程、固定 train/dev/test/hard_test 适配、安全 schema 校验、标签校验和可选本地 Qwen 入口 |
| Qwen3.5 Token Classification | 已实现（实验性） | 默认官方 `Qwen/Qwen3.5-2B` 本地下载、BIO 滑窗、best checkpoint、resume、独立 exact-span 评估；权重不入库 |
| ONNX Runtime NER 部署 | 已实现（实验性） | 固定长度 ONNX 导出、PyTorch/ORT parity、DirectML 优先/CPU 回退；运行时不导入 PyTorch |
| Tauri + React 桌面 UI | 开发版已实现 | `desktop/` 覆盖文件选择、处理/恢复、进度、结果、设置和浏览器 HTTP 开发适配器；Python 业务仍由 Sidecar 调用 |
| 统一工作流与 SQLite 作业状态 | 已实现（开发版） | `workflow/` 提供 `WorkflowService`、SQLite 状态、阶段事件、私有 checkpoint、断点恢复、取消、统一 CLI；Sidecar 已迁移为适配器 |
| 按项目批量转换并合并 Markdown | 已实现（开发版） | `skills/project-to-md/scripts/project_to_md.py` 递归处理一级项目目录；默认生成 `merged/document-<safe-id>.merged.md`，显式 `--preserve-names` 时使用项目原名和输入文件原名，并复用统一工作流状态库 |

## 3. 验证结果

| 类型 | 数量 | 结果 | 备注 |
|------|------|------|------|
| pytest 单元/集成测试 | 168 | 通过 | 另含统一工作流 SQLite、合并脱敏、无密码、等待密码、阶段故障恢复、checkpoint 篡改、租约互斥、取消、项目批量合并、文件前置转换和原名模式保留回归 |
| OCR 性能基准 | 48 页 / 3 类 PDF | 通过 | 基线约 251.9 秒；优化后约 178.2 秒；OCR 页数和样本逐页结果计数保持一致 |
| CLI 脱敏/审计/恢复验收 | 3 份输入 | 通过 | 三份 docs 输入均使用安全文件名；审计 0 问题，恢复哈希一致 |
| 公共机构白名单回归 | 2 个测试场景 | 通过 | 正常白名单保留；白名单子串不放行更长私有机构 |
| Qwen3.5 正式合成 test exact-span | 180 样本 / 362 spans | 通过 | 当前合成集 P/R/F1=1.0；不能替代授权真实语料 |
| Qwen3.5 ONNX parity | 4 类安全合成输入 | 通过 | normal/long/OCR noise/multi-entity 均 logits allclose、token labels 一致且解码 Span 键一致 |
| ONNX 无 PyTorch 实际推理 | 1 次部署进程 | 通过 | `CPUExecutionProvider`；当前环境无 `DmlExecutionProvider`，auto 正确回退 CPU |

## 4. 已知限制

- 当前默认离线规则链已经可用；Qwen3.5/ONNX 仍是实验性可选适配器，仓库不包含模型、checkpoint 或 external-data 权重。
- 无密码模式只生成不可恢复的 masked 文本和安全报告；若需要恢复，必须重新处理原文并设置密码。
- 默认 2B 基座相比更小模型需要更多磁盘和运行内存；模型仍保持可选且默认不启用。
- 外部训练数据必须通过 `--data-dir` 从仓库外只读接入；adapter 以 `text` 为 canonical text，关系只做结构校验，不进入 NER 标签。
- 当前模型指标来自安全合成语料；独立 benchmark 已暴露未见实体/OCR 噪声下的误检和漏检，正式上线前必须补充授权真实语料并设残留率/误脱敏率门槛。
- 白名单是精确词典制度；新增机构名称需要人工评审后修改规则文件，不使用模糊通配。
- OCR 模块需要额外安装 Docling、RapidOCR、Pillow、OpenCV 和 ONNX Runtime；旧版 Office 还需要 Windows
  本机 Microsoft Office 与 `pywin32`，MSG 默认需要 `extract-msg`，RAR/7z 需要系统 7-Zip；基础脱敏/整理安装不拉取这些依赖。
- 文件前置转换不会修改原始文件；ZIP/RAR/7z 及常见归档默认最多展开 3 层，并限制成员数和累计未压缩体积。归档中的不支持格式会跳过，
  Office/Outlook/7-Zip 版本差异可能导致单个输入转换失败，正式发布前需要目标机器矩阵验收。
- OCR 模块当前按一级子目录识别项目；图片和扫描 PDF 的效果取决于原始分辨率与模型。
- 纯扫描 PDF 快速路径牺牲 Docling 的版面/表格结构化，只适合已通过质量抽样的扫描文档；需要结构化表格时可关闭 `docling.scan_fast_path`。
- 当前基准环境没有可用的 DirectML/CUDA provider，`use_dml` 请求会回退 CPU；下一步应验证真实硬件 provider 和页级 OCR 缓存。
- 桌面 UI 当前是开发版；浏览器 HTTP 适配器和 Tauri 原生桥接已验证，Python Sidecar 已能以 PyInstaller onedir 打包并与模型目录组装为目录分发，但安装包签名与升级/回滚演练尚未进行。
- 统一工作流当前已完成 Python/Sidecar 主链路；并发 worker、长任务心跳调度、checkpoint 保留策略的完整产品化配置和 UI 作业历史仍待后续迭代。
- 审计只能覆盖已定义的高风险形态和 Token/Markdown 结构，正式上线仍需用授权真实语料做独立残留评估。
- 公共仓库审计只覆盖已提交内容和可达 Git 历史；推送后如发现敏感内容，必须按 `SECURITY.md` 处理，不能只删除最新文件。

## 5. 版本变更记录

| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2026-09-12 | 完成混合式可逆脱敏核心、公共机构白名单、短 Token、审计恢复和项目治理整理 |
| 0.2.0 | 2026-09-12 | 接入独立 OCR 转换、OCR 文本整理、共享安全工具和可选 OCR 依赖 |
| 0.2.0-dev | 2026-09-12 | 增加 Tauri + React 桌面开发 UI、JSON Lines Sidecar、恢复流程和浏览器验收适配器 |
| 0.2.0-dev2 | 2026-09-14 | 接入 Qwen3.5 token classification 训练、独立评估、ONNX Runtime 导出与现有 Resolver 链路 |
| 0.2.0-dev3 | 2026-09-16 | 增加 PDF 页级预检、纯扫描快速 OCR、CPU 页级并发、性能基准、安全报告和可选生命周期 profile |
| 0.2.0-dev4 | 2026-09-16 | 支持无密码不可逆脱敏；可恢复模式仍使用加密 mapping |
| 0.2.0-dev5 | 2026-09-16 | 增加统一 WorkflowService、SQLite 作业状态、私有 checkpoint 断点恢复和 Sidecar 适配 |
| 0.2.0-dev6 | 2026-09-17 | 完成五阶段故障恢复验收、checkpoint 完整性校验、输入指纹保护和作业租约互斥测试 |
| 0.2.0-dev7 | 2026-09-17 | 增加 OCR 文件前置转换：旧版 Office、MSG、ZIP/RAR/7z 归档和最多三层递归展开 |
| 0.2.0-dev8 | 2026-09-17 | 增加 `skills/project-to-md` 批量脚本：按一级项目递归转换受支持文件并分别生成 merged Markdown，复用统一工作流 SQLite 状态 |
| 0.2.0-dev9 | 2026-09-20 | 项目级 merged 输出新增显式原名模式（默认关闭）：`--preserve-names`/`ProcessRequest.preserve_names` 覆盖 merged 标题、分段标题、归档成员和 MSG 附件名称；安全 ID 模式为默认行为 |
