# 版本与能力说明（APP-VERSION）

> 版本：V0.5（2026-09-14）｜状态：生效
> 本文档记录 Wenveil（文隐）的当前版本、已实现能力、验证结果和已知限制。
> 阶段规划见 [ROADMAP.md](./ROADMAP.md)。

## 1. 当前版本

| 项 | 值 |
|----|-----|
| 版本号 | 0.2.0 |
| 公开项目名 | Wenveil（文隐） |
| Python 发行名 | `wenveil` |
| 更新时间 | 2026-09-14 |
| 对应提交 | `dev`，公开发布边界与独立 OCR/整理/脱敏模块 |

## 2. 已实现能力

| 能力 | 状态 | 证据 / 说明 |
|------|------|-------------|
| 独立 OCR 文档转换 | 已实现 | `ocr/`，移植 Docling + RapidOCR，入口为 `python -m ocr`；重依赖为可选 extra |
| 独立 OCR 文本整理 | 已实现 | `organize/`，入口为 `python -m organize`；不执行实体识别或脱敏 |
| OCR 文本规整 | 已实现 | `common/text_normalizer.py`，`desensitize/normalizer/` 保留兼容导出 |
| 规则、词典、机构关系识别 | 已实现 | `desensitize/recognizers/`、`rules/` |
| 机构全称、简称、子公司/分公司关系 | 已实现 | 机构注册表和 compact-v1 关系 Token |
| 公共机构白名单 | 已实现 | `rules/organization_whitelist.txt`，精确匹配并防止白名单子串放行私有机构 |
| 公开规则边界 | 已实现 | `rules/projects.txt` 及包内副本为空模板，项目专属规则只允许通过本地配置加载 |
| 人员名单重复表面传播 | 已实现 | 仅限名单/会议/部门等高置信上下文，并要求重复出现 |
| 短语义占位符 | 已实现 | `⟦人员N⟧`、`⟦机构N⟧`、`⟦机构N-别名N⟧` 等；最长 13 个字符 |
| 加密映射与完整恢复 | 已实现 | AES-GCM、masked 哈希绑定、密码/篡改/Token 缺失校验 |
| 审计与安全文件名 | 已实现 | audit 检查 PII、Token、表格结构；CLI 使用不可逆 `document-<safe-id>` |
| 金融领域训练与外部数据适配 | 已实现 | 合成数据兼容流程、固定 train/dev/test/hard_test 适配、安全 schema 校验、标签校验和可选本地 Qwen 入口 |
| Qwen3.5 Token Classification | 已实现（实验性） | 官方 `Qwen/Qwen3.5-0.8B` 本地下载、BIO 滑窗、best checkpoint、resume、独立 exact-span 评估；权重不入库 |
| ONNX Runtime NER 部署 | 已实现（实验性） | 固定长度 ONNX 导出、PyTorch/ORT parity、DirectML 优先/CPU 回退；运行时不导入 PyTorch |
| Tauri + React 桌面 UI | 开发版已实现 | `desktop/` 覆盖文件选择、处理/恢复、进度、结果、设置和浏览器 HTTP 开发适配器；Python 业务仍由 Sidecar 调用 |

## 3. 验证结果

| 类型 | 数量 | 结果 | 备注 |
|------|------|------|------|
| pytest 单元/集成测试 | 84 | 通过 | 包含 OCR 配置/安全文件名、文本整理、桌面 Sidecar、训练标签/滑窗和 `tests/fixtures/` 脱敏回归 |
| CLI 脱敏/审计/恢复验收 | 3 份输入 | 通过 | 三份 docs 输入均使用安全文件名；审计 0 问题，恢复哈希一致 |
| 公共机构白名单回归 | 2 个测试场景 | 通过 | 正常白名单保留；白名单子串不放行更长私有机构 |
| Qwen3.5 正式合成 test exact-span | 180 样本 / 362 spans | 通过 | 当前合成集 P/R/F1=1.0；不能替代授权真实语料 |
| Qwen3.5 ONNX parity | 4 类安全合成输入 | 通过 | normal/long/OCR noise/multi-entity 均 logits allclose、token labels 一致且解码 Span 键一致 |
| ONNX 无 PyTorch 实际推理 | 1 次部署进程 | 通过 | `CPUExecutionProvider`；当前环境无 `DmlExecutionProvider`，auto 正确回退 CPU |

## 4. 已知限制

- 当前默认离线规则链已经可用；Qwen3.5/ONNX 仍是实验性可选适配器，仓库不包含模型、checkpoint 或 external-data 权重。
- 外部训练数据必须通过 `--data-dir` 从仓库外只读接入；adapter 以 `text` 为 canonical text，关系只做结构校验，不进入 NER 标签。
- 当前模型指标来自安全合成语料；独立 benchmark 已暴露未见实体/OCR 噪声下的误检和漏检，正式上线前必须补充授权真实语料并设残留率/误脱敏率门槛。
- 白名单是精确词典制度；新增机构名称需要人工评审后修改规则文件，不使用模糊通配。
- OCR 模块需要额外安装 Docling、RapidOCR、Pillow、OpenCV 和 ONNX Runtime；基础脱敏/整理安装不拉取这些重依赖。
- OCR 模块当前按一级子目录识别项目；图片和扫描 PDF 的效果取决于原始分辨率与模型。
- 桌面 UI 当前是开发版；浏览器 HTTP 适配器和 Tauri 原生桥接已验证，但 Python Sidecar 的 PyInstaller/安装包捆绑尚未开启。
- 审计只能覆盖已定义的高风险形态和 Token/Markdown 结构，正式上线仍需用授权真实语料做独立残留评估。
- 公共仓库审计只覆盖已提交内容和可达 Git 历史；推送后如发现敏感内容，必须按 `SECURITY.md` 处理，不能只删除最新文件。

## 5. 版本变更记录

| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.0 | 2026-09-12 | 完成混合式可逆脱敏核心、公共机构白名单、短 Token、审计恢复和项目治理整理 |
| 0.2.0 | 2026-09-12 | 接入独立 OCR 转换、OCR 文本整理、共享安全工具和可选 OCR 依赖 |
| 0.2.0-dev | 2026-09-12 | 增加 Tauri + React 桌面开发 UI、JSON Lines Sidecar、恢复流程和浏览器验收适配器 |
| 0.2.0-dev2 | 2026-09-14 | 接入 Qwen3.5 token classification 训练、独立评估、ONNX Runtime 导出与现有 Resolver 链路 |
