# 总体架构（ARCHITECTURE）

> 版本：V0.8（2026-09-17）｜状态：生效
> 本文档描述 Wenveil（文隐）的分层、核心概念、数据流与目录结构；模块职责与依赖
> 规则见 [MODULES.md](./MODULES.md)。

## 1. 架构目标与约束

1. 在可恢复模式下，`restore(mask(normalize(source))) == normalize(source)` 必须成立；模型不能改写文本。
2. 所有识别器只返回不可变的 `Span`；重叠、优先级和保护策略统一由 Resolver 决定。
3. 规则处理确定性字段，词典/注册表/可选本地 NER 处理语义实体；无模型时仍可离线运行。模型运行时可选 PyTorch Transformers 或不依赖 PyTorch 的 ONNX Runtime。
4. 可恢复模式的 mapping 必须加密并绑定 masked 哈希；无密码模式不落盘 mapping，报告、日志和文件名不得泄露原始敏感值。
5. 公共机构白名单是受保护 Span；白名单子串不能放行更长的非白名单机构。
6. OCR、文本整理、脱敏分别提供独立 CLI；组合处理统一进入 `workflow/`，不把业务逻辑复制到 UI 或 Sidecar。
7. 桌面端只负责文件选择、用户设置、进度和结果展示，通过 JSON Lines Sidecar 调用 `WorkflowService`。
8. SQLite 只保存作业状态、事件、路径、哈希和产物元数据；OCR/整理文本只在私有 checkpoint 中短暂保存。
9. PDF OCR 必须先做页级预检；纯扫描文档可跳过不必要的结构化阶段，混合/复杂文档仍保留 Docling 兜底。
10. OCR 进入 Docling/RapidOCR 前，旧版 Office、MSG 和归档统一经过受控的文件前置转换层；归档最多递归展开 3 层。

## 2. 分层结构

```text
┌─────────────────────────────────────────────────────┐
│ 桌面接入层  desktop/ React + Tauri                  │
│ 独立接入层  ocr/cli.py | organize/cli.py            │
│             desensitize/cli.py                      │
├─────────────────────────────────────────────────────┤
│ 统一工作流层 workflow/ → SQLite 状态 + checkpoint    │
│ OCR 前置层  ocr/file_converter.py                  │
│ OCR 编排层  ocr/pipeline.py → Markdown              │
│ 整理编排层  organize/core.py → 稳定文本             │
│ 脱敏编排层  desensitize/pipeline.py → masked/(mapping) │
├─────────────────────────────────────────────────────┤
│ 共享基础层  common/text_normalizer.py / safety.py   │
│ 规则基础层  config / rules / mapping / optional NER  │
└─────────────────────────────────────────────────────┘
```

依赖方向：`desktop/bridge → workflow/api`、`workflow → ocr/organize/desensitize/common`、
`ocr/cli → ocr/pipeline`、
`organize/cli → organize/core`、`desensitize/cli → desensitize/pipeline`；三个功能模块均可依赖
`common/`，但不得互相导入业务实现。桌面端不包含实体识别规则，Python Sidecar 只输出安全状态、计数、行号和安全文件名。
`training/` 可以复用 `desensitize.models.Span` 等稳定数据结构；生产包不得反向导入 `training/`、
`tests/`、`test-artifacts/` 或用户资料目录。

## 3. 核心概念

| 概念 | 说明 | 代码位置 |
|------|------|----------|
| `Span` | 规整文本上的不可变实体候选 | `desensitize/models.py` |
| Recognizer | 只读识别并返回候选 Span | `desensitize/recognizers/` |
| Model recognizer | 本地 Qwen3.5 token logits 解码为候选 Span；不改写原文 | `desensitize/recognizers/model_ner.py`、`onnx_ner.py` |
| Resolver | 按优先级、置信度和长度选择不重叠 Span | `desensitize/resolver.py` |
| Whitelist | 保留公开机构名称的受保护 ORG Span | `rules/organization_whitelist.txt` |
| MappingVault | 可恢复模式加密保存 Token 与原值并绑定哈希 | `desensitize/mapping.py` |
| OrganizationRegistry | 维护全称、别名、子公司/分公司关系 | `desensitize/organization_registry.py` |
| WorkflowService | 统一处理、恢复、取消、查询和 SQLite 状态 | `workflow/api.py`、`workflow/runner.py` |
| WorkflowStore | 作业、文件、阶段、事件和产物元数据仓储 | `workflow/store.py` |
| FileConverter | 旧版 Office/MSG 转换与 ZIP/RAR/7z 及常见归档受控展开 | `ocr/file_converter.py` |

## 4. 数据流

```text
Input documents/images/archives → file_converter → OCR → organize → merge → mask → audit
                                      │              │       │       │       │
                                      │              └───────┴───────┴───────┴── SQLite 状态/事件
                                      └── 临时 Office 输出 / 归档展开目录（任务结束清理）
Tauri/React → JSON Lines Sidecar → WorkflowService
             ↓
Markdown/OCR → Normalizer → Recognizers → Candidate Span[]
             (rules + optional local Qwen3.5 / ONNX Runtime)
             → whitelist nesting filter → Resolver → Accepted Span[]
             → one-pass compact Token replacement → masked.md + report.json
                                                ↘ 有密码时再写 mapping.enc

masked.md + mapping.enc → hash/authentication check → token restore → normalized.md
masked.md（无 mapping）→ 仅可阅读，不支持恢复
```

PDF 的 OCR 路由由 `ocr/pdf_preflight.py` 提供文本层/图片覆盖率信号：没有有效文本层且以大面积扫描图为主时，
`ocr/pdf_fast.py` 按页调用 RapidOCR；其他 PDF 继续使用 Docling 的布局、表格和阅读顺序流水线。

OCR 性能观测由可选的 `ocr/profiling.py` 提供。启用时按文档、项目和页面记录墙钟耗时与阶段累计耗时，
并只输出安全 ID、计数、配置摘要和哈希；默认关闭，不改变正常处理路径的数据契约。

文件前置转换由 `ocr/file_converter.py` 负责：`.doc/.xls/.ppt` 使用本机 Office COM 生成临时新格式，
`.msg` 提取为 Markdown，`.zip/.rar/.7z` 及常见归档在临时目录展开并最多递归三层。ZIP 成员逐项校验路径和链接，
RAR/7z 由 7-Zip 预检成员元数据后展开；归档成员数和累计未压缩体积受配置预算限制。

## 5. 目录结构

```text
Wenveil/
├── AGENTS.md
├── docs/
├── common/              # 共享确定性文本规整与安全 ID
├── ocr/                 # Docling + RapidOCR 文档转换
├── organize/            # OCR Markdown/纯文本整理
├── workflow/            # 统一处理接口、SQLite 状态、checkpoint 和 CLI
├── desktop/            # Tauri + React UI 与 Python Sidecar 适配
├── test-artifacts/     # 中间产物（不入库）
├── desensitize/        # 生产包
├── training/           # 离线训练脚手架
├── config/ + rules/    # 可部署配置和词典
├── tests/              # pytest
└── skills/             # AI 调用规范
```

## 6. 关键设计决策

- [ADR-0001：混合式确定性可逆脱敏架构](./adr/0001-hybrid-reversible-desensitization.md)
- [ADR-0002：OCR、文本整理与脱敏模块独立化](./adr/0002-independent-processing-modules.md)
- [ADR-0003：Tauri 桌面端与 Python Sidecar](./adr/0003-tauri-desktop-ui.md)
- [ADR-0004：Qwen3.5 Token Classification 与 ONNX 离线部署](./adr/0004-qwen35-token-classification-onnx.md)
- [ADR-0005：统一工作流服务与 SQLite 作业状态库](./adr/0005-unified-workflow-sqlite-state.md)
- [ADR-0006：OCR 前置文件转换与受控归档展开](./adr/0006-file-conversion-preprocessing.md)
- 脱敏识别、Resolver、白名单与验收的详细规格见 [脱敏设计规格](./ocr_desensitization_implementation_plan.md)。
