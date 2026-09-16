# Wenveil（文隐）Web GPT 项目上下文提示

> 版本：V0.1（2026-09-16）｜状态：生效
> 用途：将本文作为项目背景提示提供给 Web GPT，使其在不通读整个仓库的情况下理解 Wenveil 的目标、技术路线、目录职责和协作约束。

---

## 给 GPT 的协作指令

你正在协助开发 **Wenveil（文隐）**。请先将下方项目说明视为当前项目背景。回答和提出实现方案时：

1. 尊重已经确定的模块边界、数据流和安全约束，不要擅自改用生成式改写、联网服务或新的技术路线。
2. 区分“已实现”“实验性可选能力”和“规划中能力”；不要把路线图写成当前事实。
3. 只在任务涉及的范围内要求查看文件。若需要核对代码，只请求相关入口、实现和测试文件，不要求用户上传整个仓库；没有源码证据时明确标注假设。
4. Python 模块之间通过文件/文本契约组合，不互相调用对方的业务实现。桌面前端通过 Sidecar 复用 Python 能力，不复制识别或替换逻辑。
5. 不索要或复述真实客户文档、原始敏感字段、密码、mapping 明文或用户专属词典。需要示例时使用合成数据；不要建议把真实输入、mapping、密码、原始文件名或测试产物提交到 Git。
6. 涉及代码改动时，新增业务逻辑应配有有意义的测试，并同步相关文档。涉及文档改动时，维护 `docs/index.md` 索引。
7. 给出具体、可审查的结果和适当验证方式；不确定之处先指出，并基于当前上下文继续可独立完成的工作。

---

## 项目概览

Wenveil（文隐）是一个面向中文 OCR 金融、保险和投资文档的离线处理工具，公开项目名为 Wenveil，Python 发行名为 `wenveil`。项目提供三项可独立调用的能力：

- **OCR 转换**：文档、图片转 Markdown。
- **OCR 文本整理**：确定性修复 OCR 空格、断行、结构字段和 Markdown 噪声。
- **脱敏**：在保留文档结构的前提下遮蔽敏感实体；设置密码时通过加密映射授权恢复，省略密码时生成不可恢复结果。

`desktop/` 提供 Tauri + React 桌面入口。项目重点是本地处理、可追溯的确定性行为、脱敏后语义可读，以及能够验证的完整恢复。基础 OCR、整理和脱敏模块彼此独立；模型是可选增强，不是安全边界。

## 技术路线与关键不变量

### 三条独立处理链

```text
文档/图片 ──> ocr/ ──> Markdown ──> organize/ ──> 整理后的文本
                                                       │
                                                       ▼
                         desensitize/：规整 -> 识别候选 -> 冲突解析 -> 替换 -> masked
                                                               └ 有密码时生成加密 mapping

desktop/（React + Tauri）── JSON Lines Sidecar ──> 以上既有 Python 模块
```

OCR、整理、脱敏各有自己的 CLI 和编排入口，可以分开使用，也可以用 Markdown/纯文本文件契约顺序组合。桌面端只负责文件选择、设置、进度和结果展示，通过 Python Sidecar 调用这些既有模块。

### 可逆脱敏的处理方式

核心实现位于 `desensitize/`，处理顺序如下：

1. **确定性规整**：Normalizer 统一 Unicode、OCR 空格、断行和配置覆盖的结构字段。识别器的字符偏移均指向规整后的文本。
2. **多路候选识别**：正则/格式规则、词典、机构关系注册表和可选本地 NER 各自只返回不可变 `Span`。Recognizer 不写文件，也不修改原文。
3. **白名单保护和冲突解析**：公共机构白名单产生受保护 Span；白名单名称若只是更长私有机构候选的子串，则该白名单子 Span 不得放行更长机构。随后 Resolver 按优先级、分数、长度和位置确定不重叠结果。
4. **一次性替换**：按已接受 Span 从左到右构造 masked 文本。短语义 Token 例如 `⟦人员1⟧`、`⟦机构1⟧`、`⟦机构1-别名1⟧`，让下游仍能理解部分实体类别和机构关系。
5. **可选加密映射**：设置密码时，真实 surface、关系等保存在密码保护的 mapping 中；mapping 使用 AES-GCM，密码通过 scrypt 派生密钥，并绑定原文、规整文本和 masked 文本的 SHA-256 哈希。恢复前校验 masked 哈希，恢复后校验规整文本哈希；密码错误、密文损坏、文本变更或映射缺项会失败。省略密码时不生成 mapping，结果不可恢复。
6. **安全报告与审计**：报告以类型和计数为主。Audit 检查已定义的高风险残留形态、Token 完整性和 Markdown 结构，不回显命中的敏感文本。

设置密码的可恢复模式须保持关键关系：`restore(mask(normalize(source))) == normalize(source)`；无密码模式只保证 masked 输出不含已识别的敏感实体，不提供恢复能力。模型不能改写文本；规则、白名单、Resolver、映射校验和审计共同构成处理链的安全边界。

### 可选模型与训练

- Qwen3.5 Token Classification 是实验性可选候选识别能力，默认关闭。支持 Transformers/PyTorch 路径，以及 ONNX Runtime 路径；Windows 尝试 DirectML，不可用时回退 CPU。
- 模型只预测实体标签并转换成 `Span`，其输出仍必须经过白名单过滤、Resolver、mapping 和 audit。
- 训练、数据适配、标签验证、OCR 增强、独立评估和 ONNX 导出位于 `training/`。授权外部数据应从仓库外只读接入；固定 train/dev/test/hard_test 的职责须保持隔离。训练数据和模型权重不应因此进入 Git。
- 合成数据用于验证流程，不足以证明真实金融文档上的识别质量。没有授权真实语料的独立评估时，不得宣称生产精度或上线质量已经验证。

## 主要目录结构

```text
Wenveil/
├── common/                  # OCR 文本规整、安全 ID 等共享确定性工具
├── ocr/                     # OCR 转换；CLI、发现、引擎、转换、合并和流水线
├── organize/                # OCR Markdown/纯文本的确定性整理
├── desensitize/             # 脱敏 CLI、流水线、Span、识别器、Resolver、mapping、audit
│   ├── recognizers/         # 结构规则、词典、机构/人员、可选 Transformers 和 ONNX NER
│   ├── normalizer/          # 脱敏兼容入口；共享规整逻辑在 common/
│   ├── config/              # 包内默认配置
│   └── rules/               # 包内规则副本
├── desktop/                 # Tauri 2 + React/TypeScript/Vite 桌面应用
│   ├── src/                 # 前端页面、类型和 bridge
│   ├── src-tauri/           # Rust/Tauri 原生壳
│   └── bridge/              # Python JSON Lines Sidecar、HTTP 开发桥和打包脚本
├── training/                # 合成数据、外部数据适配、标签、训练、评估和导出
├── config/                  # 项目默认 YAML 配置（含 OCR 配置）
├── rules/                   # 公共规则、机构注册表和白名单模板
├── tests/                   # pytest 回归及非用户数据 fixtures
├── docs/                    # 架构、规范、ADR、路线图及本提示文档
├── skills/                  # 项目级 AI 脱敏调用规范
├── test-artifacts/          # 本地测试/调试产物，禁止提交
├── pyproject.toml           # Python 包元数据、依赖 extra、CLI 和测试配置
└── AGENTS.md                # 仓库内 AI 助手项目约定
```

关键代码入口：`ocr/cli.py` → `ocr/pipeline.py`；`organize/cli.py` → `organize/core.py`；`desensitize/cli.py` → `desensitize/pipeline.py`。桌面 JSON Lines 协议和调用编排在 `desktop/bridge/sidecar.py`。以上路径用于定位相关代码，不代表必须通读整个目录。

## 模块和依赖边界

- `common/` 提供纯确定性共享能力，不识别业务实体。
- `ocr/`、`organize/`、`desensitize/` 可以依赖 `common/`，但不得互相导入业务实现。组合发生在文件/文本契约层或桌面 Sidecar 编排层。
- CLI 负责参数、交互和安全落盘；实体识别和流水线逻辑留在各自模块。
- `desensitize/recognizers/` 只提出候选；`resolver.py` 只做 Span 冲突选择；`mapping.py` 只负责 Token 映射的加密、校验和恢复。
- `training/` 可复用稳定的脱敏数据结构，但生产脱敏代码不反向依赖训练代码。OCR 重依赖、Transformers、PyTorch、ONNX Runtime 均为可选依赖，导入基础脱敏/整理功能时不应被强制加载。
- `desktop/` 不实现识别、替换、冲突解析或恢复算法。浏览器 HTTP 开发适配器只监听 loopback；生产桌面宿主经 Tauri 调用 JSON Lines Sidecar。

## 技术栈与常用入口

- Python 3.10+；基础运行时依赖 PyYAML 与 cryptography；测试使用 pytest。
- OCR 重依赖通过 `[ocr]` extra 安装；模型/训练依赖通过 `[model]` 等 extra 安装。
- 桌面端使用 Tauri 2、React、TypeScript、Vite，Rust 原生壳通过 Cargo 构建/检查。
- 独立 CLI：`python -m ocr`、`python -m organize`、`python -m desensitize`；安装脚本入口分别为 `ocr-convert`、`organize-text`、`desense`。

常用验证命令：

```powershell
python -m pip install -e .
python -m compileall -q common desensitize ocr organize training
pytest -q
python -m ocr --help
python -m organize --help
python -m desensitize --help

cd desktop
npm install
npm run typecheck
npm run build
cargo check --manifest-path src-tauri/Cargo.toml
```

桌面浏览器用户流程与 Tauri 原生桥接是不同验收范围；浏览器开发模式测试不能替代 Cargo 检查或原生打包验收。按改动范围选择相关测试，再运行必要的完整回归。

## 安全与数据边界

- 真实输入、OCR/整理结果、脱敏输出、mapping、密码、含用户资料的日志和原始文件名不能提交到 Git。
- `test-artifacts/` 用于本地产物，不入库。授权输入和各阶段产物按仓库 `AGENTS.md`、开发指南的子目录约定存放。
- 日志、异常、报告和 UI 消息不得包含原始敏感 surface、密码、mapping 明文、原始文件名或内部堆栈；诊断优先保留安全 ID、计数、行号、哈希和固定摘要。
- 项目公共规则文件是模板和通用规则的边界。客户/项目专属词典应通过本地配置提供，不能写进公开仓库。
- 安全文件名采用不可逆 `document-<safe-id>`，不要沿用输入文件名。mapping 含可恢复数据，必须按敏感文件处理。
- Web GPT 是在线服务。若任务涉及用户文档，应优先使用合成样例或已经批准的脱敏材料；不要把未经授权的原文、mapping 或密码放入提示词。

## 当前状态与限制

以下状态依据仓库 `docs/APP-VERSION.md` 和 `docs/ROADMAP.md`（截至 2026-09-14）：

- Python 发行版本为 `0.2.0`。独立 OCR、文本整理和可逆脱敏已实现；Tauri + React 桌面端处于开发版。
- 规则/词典链可在不启用模型时离线运行。Qwen3.5 与 ONNX 推理属于实验性可选能力；模型权重不在仓库中。
- 合成集、固定切分适配、训练/评估/ONNX 导出路径已实现，但授权真实语料验证和正式质量门槛仍待完成。不要以合成数据指标替代真实使用质量结论。
- 桌面 Sidecar 与目录分发工具已具备；安装包签名、升级/回滚演练及更完整的发布验收仍未完成。
- 批量目录处理、性能基线等仍在路线图中。白名单为精确词典，需要人工评审和维护；Audit 只覆盖已定义的风险形态，不能证明不存在所有残留。

若后续仓库文件与本摘要不一致，以当前可验证代码和仓库 `docs/APP-VERSION.md`、`docs/ROADMAP.md` 为准，并指出摘要可能过时。不要静默把规划项写成已完成。

## 详细资料的按需定位

如果用户能够提供仓库文件，先按任务范围只读最相关的文件：

| 任务 | 优先查看 |
|------|----------|
| 项目分层或跨模块设计 | `docs/ARCHITECTURE.md`、`docs/MODULES.md`、相关 ADR |
| 脱敏识别/恢复行为 | `desensitize/pipeline.py`、相关 recognizer、`desensitize/resolver.py`、`desensitize/mapping.py`、对应测试 |
| OCR 或文本整理 | 对应模块的 `README.md`、编排入口和相关测试 |
| 桌面端 | `docs/adr/0003-tauri-desktop-ui.md`、`desktop/README.md`、相关 bridge/前端文件与桌面测试 |
| 模型/训练 | `training/README.md`、`docs/adr/0004-qwen35-token-classification-onnx.md`、涉及脚本及测试 |
| 构建、命令、开发流程 | `docs/DEV-TOOLCHAIN.md`、`docs/DEVELOPMENT-GUIDELINES.md` |
| 当前能力和待办 | `docs/APP-VERSION.md`、`docs/ROADMAP.md` |

若做不到读取仓库，只根据用户明确提供的文件和本提示回答；需要改动时给出可落地的补丁内容，并标出尚未验证的部分。
