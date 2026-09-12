# 总体架构（ARCHITECTURE）

> 版本：V0.3（2026-09-12）｜状态：生效
> 本文档描述 Wenveil（文隐）的分层、核心概念、数据流与目录结构；模块职责与依赖
> 规则见 [MODULES.md](./MODULES.md)。

## 1. 架构目标与约束

1. `restore(mask(normalize(source))) == normalize(source)` 必须成立，模型不能改写文本。
2. 所有识别器只返回不可变的 `Span`；重叠、优先级和保护策略统一由 Resolver 决定。
3. 规则处理确定性字段，词典/注册表/可选本地 NER 处理语义实体；无模型时仍可离线运行。
4. mapping 必须加密并绑定 masked 哈希；报告、日志和文件名不得泄露原始敏感值。
5. 公共机构白名单是受保护 Span；白名单子串不能放行更长的非白名单机构。
6. OCR、文本整理、脱敏分别提供独立 CLI；模块之间只通过文件/文本契约组合，不互相调用业务逻辑。

## 2. 分层结构

```text
┌─────────────────────────────────────────────────────┐
│ 独立接入层  ocr/cli.py | organize/cli.py            │
│             desensitize/cli.py                      │
├─────────────────────────────────────────────────────┤
│ OCR 编排层  ocr/pipeline.py → Markdown              │
│ 整理编排层  organize/core.py → 稳定文本             │
│ 脱敏编排层  desensitize/pipeline.py → masked/mapping │
├─────────────────────────────────────────────────────┤
│ 共享基础层  common/text_normalizer.py / safety.py   │
│ 规则基础层  config / rules / mapping / optional NER  │
└─────────────────────────────────────────────────────┘
```

依赖方向：`ocr/cli → ocr/pipeline`、`organize/cli → organize/core`、
`desensitize/cli → desensitize/pipeline`；三个功能模块均可依赖 `common/`，但不得互相导入业务实现。
`training/` 可以复用 `desensitize.models.Span` 等稳定数据结构；生产包不得反向导入 `training/`、
`tests/`、`test-artifacts/` 或用户资料目录。

## 3. 核心概念

| 概念 | 说明 | 代码位置 |
|------|------|----------|
| `Span` | 规整文本上的不可变实体候选 | `desensitize/models.py` |
| Recognizer | 只读识别并返回候选 Span | `desensitize/recognizers/` |
| Resolver | 按优先级、置信度和长度选择不重叠 Span | `desensitize/resolver.py` |
| Whitelist | 保留公开机构名称的受保护 ORG Span | `rules/organization_whitelist.txt` |
| MappingVault | 加密保存 Token 与原值，绑定哈希 | `desensitize/mapping.py` |
| OrganizationRegistry | 维护全称、别名、子公司/分公司关系 | `desensitize/organization_registry.py` |

## 4. 数据流

```text
OCR documents/images → ocr/ → Markdown → organize/ → stable text
                                           │
                                           ▼
Markdown/OCR → Normalizer → Recognizers → Candidate Span[]
             → whitelist nesting filter → Resolver → Accepted Span[]
             → one-pass compact Token replacement → masked.md + mapping.enc + report.json

masked.md + mapping.enc → hash/authentication check → token restore → normalized.md
```

## 5. 目录结构

```text
Wenveil/
├── AGENTS.md
├── docs/
├── common/              # 共享确定性文本规整与安全 ID
├── ocr/                 # Docling + RapidOCR 文档转换
├── organize/            # OCR Markdown/纯文本整理
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
- 详细演进方案见 [OCR 脱敏实施方案](./ocr_desensitization_implementation_plan.md)。
