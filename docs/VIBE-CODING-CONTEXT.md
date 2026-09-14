# Web 端 AI 项目上下文与 Vibe Coding 提示词

> 版本：V0.2（2026-09-14）｜状态：生效
> 本文面向需要在 Web 端 AI 中介绍 Wenveil（文隐）的维护者，汇总当前项目事实、目录结构、custom agents、
> 协作约束和可直接复制的 Vibe Coding 提示词。它是外部 AI 的上下文摘要，不替代项目内部的详细规范。

## 1. 一句话介绍

Wenveil（文隐）是一个面向中文金融、保险和投资文档的离线 Python 工具，按
`OCR → OCR 文本整理 → 可逆脱敏 → 审计/恢复` 的文件契约处理文档；三个功能模块可独立调用，并提供开发版 Tauri + React 桌面 UI。

公开项目名是 `Wenveil`，中文名是“文隐”，Python 发行名是 `wenveil`，当前版本号是 `0.2.0`。

## 2. 当前事实快照

| 项目 | 当前事实 |
|------|----------|
| 语言与版本 | Python 3.10+，当前文档记录的验证环境为 Python 3.13 |
| 运行时依赖 | `PyYAML`、`cryptography` |
| 测试 | `pytest`；`docs/APP-VERSION.md` 记录最近验证为 84 个单元/集成测试通过 |
| OCR | Docling + RapidOCR，可选安装，不影响基础整理/脱敏模块 |
| 模型 | Qwen3.5 Token Classification 训练/评估与 ONNX Runtime 推理均为实验性可选能力；仓库不包含权重 |
| 构建 | setuptools / `pyproject.toml`；桌面端使用 Vite、Tauri 2、Node.js 20+ 和 stable Rust |
| 许可证 | Apache-2.0 |
| 当前分支口径 | 项目文档以 `dev` 作为开发/发布边界说明；实际工作树分支以本地 Git 状态为准 |
| UI 状态 | `desktop/` 提供开发版 Tauri + React UI、JSON Lines Sidecar 和 loopback HTTP 适配器；浏览器适配器用 Playwright 验收，Tauri 原生桥接用 Cargo 检查 |

当前路线图：阶段 1（确定性可逆核心）和阶段 2（机构关系、白名单、短 Token、安全文件名）已完成；阶段 3 已完成 Qwen3.5 训练入口、独立评估和基础模型适配，但授权金融语料、歧义机构 linking 和上线质量门槛仍在进行；阶段 4 已完成独立 OCR/整理模块、桌面开发 UI 与 ONNX parity/CPU 回退验证，批量处理、性能基线和离线发布交付仍在进行。

## 3. 核心处理链路

```text
授权文档/图片
    ↓
ocr/：Docling + RapidOCR，转换为安全命名的 Markdown
    ↓
organize/：确定性修复 Unicode、OCR 空格、断行、分页和 Markdown 噪声
    ↓
desensitize/：规整 → 多路识别 → 白名单过滤 → Span 冲突解析 → 一次性替换
    ↓
normalized.md + masked.md + mapping.enc + report.json
    ↓
audit：检查 PII 残留、Token 完整性和 Markdown 结构
restore：校验密码、mapping、masked 哈希并恢复为规范化文本
```

必须保持的核心等式：

```text
restore(mask(normalize(source))) == normalize(source)
```

模型或识别器只能提出候选 `Span`，不能改写原文；所有重叠、优先级、保护和最终替换都由统一 Pipeline/Resolver 决定。

## 4. 目录结构与关键文件

```text
Wenveil/
├── AGENTS.md                         # 项目级 AI 开工指南；要求先读 docs/index.md
├── .codex/agents/                    # Codex custom agent 定义
│   ├── planner.toml                  # 方案拆解
│   ├── architect.toml                # 架构与实施方案
│   ├── coder.toml                    # 受限范围内编码
│   ├── codeview.toml                 # 只读代码评审
│   ├── tester.toml                   # 测试与 CLI 验收
│   ├── explorer.toml                 # 只读侦察、调用链和引用定位
│   └── maintainer.toml               # 死代码与重复逻辑清理
├── docs/                             # 架构、规范、路线图、ADR 和本上下文文档
├── common/                           # 共享确定性文本规整与安全 ID
├── ocr/                              # 独立 OCR/文档转换模块
├── organize/                         # 独立 OCR 文本整理模块
├── desktop/                          # Tauri + React 桌面 UI、JSON Lines Sidecar 与桥接适配器
├── desensitize/                      # 生产脱敏包：识别、解析、映射、审计、CLI
├── training/                         # 离线数据、标签校验、增强、评估和可选训练入口
├── config/                           # 项目默认 YAML 配置，含 config/ocr.yaml
├── rules/                            # 公共词典、机构关系注册表、公共机构白名单模板
├── tests/                            # pytest 单元/集成回归和合成夹具
├── skills/                           # 项目级 AI skill；当前有脱敏专项 skill
├── test-artifacts/                   # 授权输入、输出、日志和审计中间产物；不入库
├── pyproject.toml                    # 包元数据、依赖、CLI 入口和 pytest 配置
├── README.md                         # 用户级安装、运行和安全边界说明
├── SECURITY.md                       # 安全报告与公开仓库数据边界
└── CONTRIBUTING.md                   # 贡献、测试和发布前检查
```

生产代码中最重要的文件/目录：

| 路径 | 作用 |
|------|------|
| `common/text_normalizer.py`、`common/safety.py` | 共享确定性规整和安全 ID，不识别实体 |
| `ocr/cli.py`、`ocr/pipeline.py`、`ocr/converter.py` | OCR CLI、编排和文档转换 |
| `organize/cli.py`、`organize/core.py` | OCR 文本整理 CLI 和核心逻辑 |
| `desensitize/cli.py`、`desensitize/pipeline.py` | 脱敏 CLI 和总编排入口 |
| `desensitize/models.py` | 不可变 `Span` 等核心数据结构 |
| `desensitize/recognizers/` | 结构化、词典、机构、人员、地址，以及 Qwen3.5 Transformers/ONNX 候选识别器 |
| `desensitize/recognizers/model_ner.py`、`onnx_ner.py` | Qwen3.5 Transformers/ONNX 推理适配；只产生候选 Span |
| `desensitize/resolver.py` | 重叠、优先级、置信度和受保护 Span 的确定性解析 |
| `desensitize/mapping.py` | compact Token、AES-GCM 映射、哈希绑定和恢复 |
| `desensitize/audit.py` | PII 残留、Token 和 Markdown 结构的只读审计 |
| `desktop/bridge/sidecar.py`、`desktop/src-tauri/` | JSON Lines 请求编排、进度/取消、安全错误与 Tauri 原生桥接 |
| `config/`、`rules/`、`desensitize/config/`、`desensitize/rules/` | 配置、词典、机构关系和公共机构白名单 |
| `training/train_token_classifier.py`、`evaluate.py`、`export_onnx.py` | Qwen3.5 训练、独立 exact-span 评估和 ONNX 导出；生产代码不得反向依赖它 |
| `tests/` | 规整、识别、解析、映射、审计、CLI、OCR、整理和训练回归 |

当前工作树可能出现 `dist/`、`output/`、`test-output/`、`training-output/`、`models/`、`*.egg-info/` 以及 `desktop/sidecar-dist/`、`desktop/sidecar-build/` 等构建/本地产物；它们不是核心源代码结构。`test-artifacts/`、原始文档、OCR/脱敏输出、mapping、密码和模型权重均不得提交。

## 5. 架构与不可违反的边界

### 5.1 依赖方向

```text
ocr/cli         → ocr/pipeline
organize/cli    → organize/core
desensitize/cli → desensitize/pipeline
三个功能模块    → common/
desktop/bridge  → 各 Python 模块公开编排接口
training/       → 稳定数据结构（可复用 desensitize.models.Span）
```

禁止 OCR、Organize、Desensitize 互相导入业务实现；桌面端不得复制识别、解析、映射或恢复规则；生产代码不得导入 `training/`、`tests/`、`test-artifacts/`，不得把用户资料目录当隐式配置。

### 5.2 脱敏安全边界

- 识别器只读文本并返回候选 Span，不直接替换、不写文件。
- Qwen3.5 Transformers/ONNX 适配器只能提供候选 Span；ONNX 运行时不导入 PyTorch，所有模型候选仍必须经过白名单嵌套过滤、Resolver、Mapping 和 Audit。
- Resolver 统一处理重叠、优先级、置信度、保护和白名单嵌套边界。
- mapping 使用 AES-GCM，并绑定 masked 文本哈希；密码错误、篡改、Token 缺失或哈希不一致必须失败。
- 公共机构白名单是精确词典；白名单名称作为受保护 Span 保留，但不能因为它是更长私有机构的子串而放行更长机构。
- 机构全称、简称、子公司/分公司保留关系语义但不泄露原值，例如 `⟦机构1⟧`、`⟦机构1-别名1⟧`。
- 审计、日志、报告和 AI 回复只允许安全 ID、类别、计数、行号、哈希或固定摘要，不得出现原始敏感值、原文上下文、mapping 明文、密码或本地绝对路径。
- 项目专属客户/交易/项目规则只能通过仓库外的本地配置加载；仓库中的 `rules/projects.txt` 是空模板。
- 桌面桥接可以接收本地路径和密码用于当前请求，但不得把密码放进命令行、日志、持久化配置、报告或截图；对外只序列化安全文件名、状态、计数、行号和固定错误摘要。

## 6. 当前 custom agents

配置位置是 `.codex/agents/*.toml`。当前有 7 个角色：

| Agent | 当前模型/推理 | 主要职责 | 写入边界 |
|-------|---------------|----------|----------|
| `planner` | `gpt-5.6-terra` / medium | 把需求拆成可执行、分层、可测试的计划，区分事实/假设/不做项 | 默认只读和输出计划 |
| `architect` | `gpt-5.6-terra` / medium | 设计数据流、模块归属、接口、异常、测试矩阵和 ADR 需求 | 只有任务明确授权才实施代码 |
| `coder` | `gpt-5.6-luna` / high | 在授权范围内实现代码和测试，保持脱敏安全边界 | 可修改授权范围内代码/测试/文档 |
| `codeview` | `gpt-5.6-luna` / max | 只读代码/方案评审，按阻塞、高、中、低严重度报告问题 | 不修改文件 |
| `tester` | `gpt-5.6-luna` / medium | 执行 compile、pytest、CLI、mask/audit/restore，以及桌面 typecheck/build/Cargo/用户流程验收 | 不修改生产代码、测试或配置；允许安全测试产物进 `test-artifacts/` |
| `explorer` | `gpt-5.6-luna` / low | 只读定位符号、调用链、引用和配置边界，提供文件/行号证据 | 不创建、修改、删除文件 |
| `maintainer` | `gpt-5.6-luna` / max | 清理已确认的死代码、重复逻辑和过期文档 | 仅处理明确授权范围；删除前必须全仓查引用 |

推荐协作环：

```text
explorer（查事实）
    → planner（拆任务）
    → architect（跨模块/技术路线设计）
    → coder（实现）
    → codeview（只读评审）
    → tester（验证）
    → maintainer（必要时清理债务）
```

### 6.1 当前 agent 配置的已知治理项

截至 2026-09-14，`planner`、`architect`、`coder`、`codeview`、`explorer`、`maintainer` 的 TOML 注释或角色描述仍有旧项目名 `AICanRead`，`tester` 已使用 Wenveil；`coder` 和 `maintainer` 的编译指令仍偏向 `desensitize training`，而现行项目规范要求覆盖 `common desensitize ocr organize training`。部分旧提示词还把项目描述成没有 GUI，或没有写明 OCR/Organize/desktop 边界。因此 Web AI 设计新提示词或改写现有 agent 时，应：

1. 保留上述角色分工和只读边界；
2. 将旧项目名全部替换为 Wenveil（文隐）；
3. 把 OCR、Organize 的独立模块边界和 CLI 验收加入相关 agent；
4. 让所有 agent 以 `AGENTS.md → docs/index.md → 按场景精读` 为开工顺序；
5. 把 `desktop/` 的 JSON Lines Sidecar、浏览器 HTTP 适配器和 Tauri 原生桥接纳入相关角色；不新增独立 `designer` agent，UI 验收交给 `tester`；
6. 把 Qwen3.5/ONNX 标记为实验性可选能力，要求模型候选仍经过现有安全链路；
7. 不把本次上下文摘要当成取代架构文档的唯一事实源。

## 7. 文档、skill 与日常工作约定

- 开工先读 `AGENTS.md`，然后读 `docs/index.md` 的快速决策表。
- 架构/依赖读 `docs/ARCHITECTURE.md`、`docs/MODULES.md`；代码读 `docs/DEVELOPMENT-GUIDELINES.md`；构建读 `docs/DEV-TOOLCHAIN.md`；版本与阶段读 `docs/APP-VERSION.md`、`docs/ROADMAP.md`。
- 桌面端任务再读 `docs/UI/UI设计方案.md`、`docs/adr/0003-tauri-desktop-ui.md`；Qwen/ONNX 任务再读 `docs/adr/0004-qwen35-token-classification-onnx.md`。
- 修改文档前读 `docs/DOCUMENTATION-GUIDE.md`；新增/改名/删除 `docs/` 文档必须同步 `docs/index.md`。
- 重大技术路线变化先写 `docs/adr/`，再实施并同步架构、模块和路线图。
- 新增业务逻辑必须有单元/集成测试；核心流程用 CLI 冒烟，不伪造 GUI 测试。
- 所有中间文件放 `test-artifacts/`，不能把真实文档或敏感输出写入 Git。

常用质量门禁：

```powershell
python -m pip install -e .
python -m compileall -q common desensitize ocr organize training
pytest -q
python -m ocr --help
python -m organize --help
python -m desensitize --help
git diff --check
npm --prefix desktop run typecheck
npm --prefix desktop run build
cargo check --manifest-path desktop/src-tauri/Cargo.toml
```

最后三项仅在涉及桌面端时执行；浏览器 HTTP 适配器还必须用 Playwright 验收，不能用浏览器结果替代 Tauri 原生检查。

## 8. 可直接粘贴给 Web 端 AI 的项目上下文

以下内容适合放在 Web AI 的第一条消息中：

```text
你正在协助维护 Wenveil（文隐），它是以 Python 3.10+ 离线处理模块为核心、同时提供 Tauri + React 桌面入口的中文文档处理工具。

项目目标：把授权的中文金融/保险/投资文档按 OCR → 文本整理 → 可逆脱敏 → 审计/恢复处理。公开项目名 Wenveil（文隐），Python 发行名 wenveil，当前版本 0.2.0，许可证 Apache-2.0。最近一次记录为 84 个 pytest 单元/集成测试通过。`desktop/` 是开发版 Tauri + React 桌面入口。

仓库结构：
- common/：共享确定性文本规整和安全 ID。
- ocr/：独立文档/图片转 Markdown，Docling + RapidOCR 为可选重依赖。
- organize/：独立 OCR Markdown/纯文本整理，不识别实体、不脱敏。
- desktop/：Tauri + React 桌面 UI，通过 JSON Lines Sidecar 调用 Python 模块；提供 loopback HTTP 浏览器开发适配器；不复制业务规则。
- desensitize/：生产脱敏包，包含 CLI、pipeline、normalizer、recognizers、resolver、mapping、audit。
- training/：离线合成数据、标签校验、OCR 增强、Qwen3.5 训练/评估和 ONNX 导出脚手架；生产代码不能依赖它。
- config/、rules/：配置、词典、机构注册表、公共机构白名单。
- tests/：pytest 单元/集成回归和合成夹具。
- docs/：唯一的项目知识库入口是 docs/index.md；架构、模块、开发规范、工具链、版本、路线图和 ADR 都在这里。
- .codex/agents/：现有 planner、architect、coder、codeview、tester、explorer、maintainer 七个 custom agents。

必须保持的架构：CLI → 各自 Pipeline；desktop/bridge 只调用 Python 模块公开编排接口；OCR、Organize、Desensitize 不互相导入业务实现；三个模块可以依赖 common；training 不进入生产链路；ONNX recognizer 不依赖 PyTorch。

必须保持的安全不变量：restore(mask(normalize(source))) == normalize(source)。识别器只能返回不可变 Span 候选，不能改写原文；Qwen3.5/ONNX 只能增强候选召回，仍必须经过白名单嵌套过滤、Resolver、Mapping 和 Audit；mapping 使用 AES-GCM 并绑定 masked 哈希；公共机构白名单必须精确匹配且不能放行更长私有机构；日志、报告、回复、文件名不能泄露原文、敏感值、mapping 明文、密码或本地绝对路径。

当前进度：阶段 1/2 已完成；阶段 3 已有 Qwen3.5 Token Classification 训练入口、best/resume、独立 exact-span 评估和合成回归，但授权金融语料、歧义机构 linking 和上线质量门槛仍待完成；阶段 4 已有独立 OCR/Organize CLI、桌面开发 UI、ONNX 导出与 PyTorch/ORT parity、DirectML 优先/CPU 回退，但批量处理、固定长度性能基线、Python Sidecar 最终 PyInstaller/安装包捆绑、签名和回滚演练仍待完成。没有本地 checkpoint 或部署模型目录时不得假装模型已训练或已启用，继续使用规则/词典基线并明确说明。

开工顺序：先读 AGENTS.md，再读 docs/index.md，并按任务精读 docs/ARCHITECTURE.md、docs/MODULES.md、docs/DEVELOPMENT-GUIDELINES.md、docs/DEV-TOOLCHAIN.md、docs/APP-VERSION.md、docs/ROADMAP.md 及相关专项/ADR。代码修改必须配测试；文档修改必须更新 docs/index.md；重大技术路线变更先写 ADR；中间产物只能放 test-artifacts/。

注意：截至 2026-09-14，`planner`、`architect`、`coder`、`codeview`、`explorer`、`maintainer` 的 `.codex/agents/*.toml` 仍有旧项目名或旧边界描述，`tester` 已更新为 Wenveil 版本；后续提示词应统一项目名，补齐 OCR/Organize/desktop/Qwen-ONNX 边界和全量质量门禁。
```

## 9. 请求 Web AI 设计 Vibe Coding 提示词的模板

```text
请基于上面的 Wenveil 项目上下文，为我设计一套可长期维护的 Vibe Coding 提示词和 custom-agent 协作规范。

目标：
1. 不重新臆造项目结构，基于现有 .codex/agents/ 做 retrofit，而不是从空仓库脚手架化。
2. 保留 planner、architect、coder、codeview、tester、explorer、maintainer 七个角色；桌面 UI 由 coder 实现、tester 验收，不增加独立 designer。
3. 为每个角色给出：职责、必须先读的文件、允许读写范围、禁止事项、固定输出格式、使用模型/推理档位建议。
4. 设计一条适合本项目的协作循环：侦察 → 规划 → 架构判断 → 编码 → 只读评审 → 测试验收 → 必要时维护清理。
5. 把 Wenveil 的 Span/Resolver、AES-GCM mapping、masked 哈希、公共机构白名单嵌套边界、桌面 UI、离线优先和 test-artifacts 数据边界写进关键角色提示词。
6. 为常见任务提供提示词模板：新增识别规则、修复漏脱敏、修复误脱敏、改 OCR/整理模块、训练数据准备、文档/ADR 更新、代码评审和发布前安全审计。
7. 明确哪些操作必须先获得任务授权，哪些问题必须先停下来创建 ADR 或向我确认。
8. 检查现有 agent 配置中的旧项目名 AICanRead、过时测试命令和缺失的 OCR/Organize 约束，并给出最小修改建议。

输出格式：
- 先给出对当前仓库的事实核对和发现的问题；
- 再给出推荐的 agent prompt 版本；
- 再给出统一的任务输入模板和完成报告模板；
- 最后给出不修改代码即可执行的落地步骤，以及需要修改哪些 .toml/docs 文件。

请不要把任何真实文档、原文片段、密码、mapping、客户名或本地绝对路径写进提示词示例。
```

## 10. 单个开发任务的 Vibe Coding 输入模板

```text
任务：<一句话描述>
目标行为：<输入、输出、成功条件>
范围：<允许修改的模块/文件；明确不改什么>
证据：<现有测试、文档、命令或复现编号；不要粘贴真实敏感文本>
约束：保持 restore(mask(normalize(source))) == normalize(source)，不泄露敏感值，不跨模块导入，不绕过 Resolver/mapping/audit。
期望流程：先用 explorer/planner 给出事实和计划；涉及跨模块或技术路线变化时先由 architect 判断；再由 coder 实现；最后由 codeview 和 tester 独立检查。
完成定义：相关测试通过、全量 pytest 通过、CLI 冒烟通过、文档/ADR 已同步、git diff --check 通过，且 `test-artifacts/` 外没有中间产物；若涉及桌面端，再通过 `npm run typecheck`、`npm run build` 和 `cargo check`，并按需完成 Playwright 浏览器验收。
```

## 11. 事实来源

本摘要根据以下仓库文件整理；若发生冲突，以可验证代码行为和项目内部规范为准：

- `AGENTS.md`
- `docs/index.md`
- `docs/ARCHITECTURE.md`
- `docs/MODULES.md`
- `docs/DEVELOPMENT-GUIDELINES.md`
- `docs/DEV-TOOLCHAIN.md`
- `docs/APP-VERSION.md`
- `docs/ROADMAP.md`
- `docs/UI/UI设计方案.md`、`docs/adr/0003-tauri-desktop-ui.md`、`docs/adr/0004-qwen35-token-classification-onnx.md`
- `.codex/agents/*.toml`
- `skills/ocr-desensitization/SKILL.md`
- `desktop/README.md`、`training/README.md`
- `README.md`、`pyproject.toml`、`SECURITY.md`、`CONTRIBUTING.md`
