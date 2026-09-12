# AGENTS.md — AI 助手项目指南

> 本文档供 AI 编码助手在本项目中工作时阅读，统一项目背景、约定与工作方式。
> 版本：V0.3（2026-09-12）｜状态：生效

## 1. 项目简介

**AICanRead** 是面向中文 OCR 金融、保险与投资文档的离线文档处理工具，包含三个可独立调用的
功能模块：`ocr/` 负责文档/图片转 Markdown，`organize/` 负责 OCR 文本确定性整理，
`desensitize/` 负责可逆脱敏。脱敏系统以多路 Span 识别、统一冲突解析、短语义 Token 和
AES-GCM 加密映射为核心；模型只能提出候选实体，不能改写原文。任何日志、报告和提交都不得泄露
用户原始文档或映射密码。

## 2. 开工前必读（重要）

**任何任务开始前，先阅读 [docs/index.md](docs/index.md)（文档索引）**，在其「快速决策表」
中定位本次任务所需文档，再按需精读后实施。

- 涉及**架构/模块/技术路线**：必须先读对应文档，避免违背既定决策。
- 涉及**代码修改**：必须遵守 [docs/DEVELOPMENT-GUIDELINES.md](docs/DEVELOPMENT-GUIDELINES.md)。
- 涉及**Git 提交**：必须遵守 [docs/GIT-GUIDELINES.md](docs/GIT-GUIDELINES.md)。
- 涉及**文档修改**：先读 [docs/DOCUMENTATION-GUIDE.md](docs/DOCUMENTATION-GUIDE.md)。

| 场景 | 必读 |
|------|------|
| 了解全貌/当前阶段 | [docs/index.md](docs/index.md) + [docs/ROADMAP.md](docs/ROADMAP.md) |
| 架构分层/依赖规则 | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) + [docs/MODULES.md](docs/MODULES.md) |
| 开发与测试规范 | [docs/DEVELOPMENT-GUIDELINES.md](docs/DEVELOPMENT-GUIDELINES.md) |
| Git 分支/提交规范 | [docs/GIT-GUIDELINES.md](docs/GIT-GUIDELINES.md) |
| 构建/安装/调试 | [docs/DEV-TOOLCHAIN.md](docs/DEV-TOOLCHAIN.md) |
| 版本/已实现能力/限制 | [docs/APP-VERSION.md](docs/APP-VERSION.md) |

## 3. 技术栈与版本

| 项 | 值 |
|----|-----|
| 语言 | Python 3.10+（当前验证环境 Python 3.13） |
| 运行时 | 标准库 + PyYAML + cryptography；可选 Transformers/PyTorch |
| 构建/安装 | setuptools / `pyproject.toml` / `python -m pip install -e .` |
| 测试 | pytest |

## 4. 目录约定

```text
AICanRead/
├── AGENTS.md
├── docs/            # 架构与决策文档，入口 docs/index.md
│   ├── adr/         # 架构决策记录
│   └── archive/     # 历史设计资料
├── common/          # 三个功能模块共享的纯确定性工具（文本规整、安全 ID）
├── ocr/             # 独立 OCR/文档转换模块：Docling + RapidOCR + CLI
├── organize/        # 独立 OCR 文本整理模块：Markdown/纯文本规整 + CLI
├── desensitize/     # 独立可逆脱敏模块：识别、解析、替换、恢复、审计、CLI
├── training/        # 离线训练数据、增强、验证与可选 Qwen 训练脚手架
├── config/          # 项目默认配置（含 config/ocr.yaml）
├── rules/           # 词典、机构关系注册表和公共机构白名单
├── tests/           # pytest 测试
├── skills/          # 项目级 AI skill
├── test-artifacts/  # 测试/调试及脱敏过程产物（不入库）
│   ├── ocr-inputs/               # 授权 OCR 输入（不入库）
│   ├── ocr-outputs/              # OCR 转换输出（不入库）
│   ├── organized-outputs/        # OCR 文本整理输出（不入库）
│   ├── desensitization-inputs/   # 授权脱敏输入（不入库）
│   └── desensitization-outputs/  # 脱敏、恢复、审计与映射产物（不入库）
└── pyproject.toml   # setuptools / 项目元数据
```

## 5. 代码规范

- Python 遵循 PEP 8；模块、函数和变量使用 `snake_case`，类使用 `PascalCase`，类型提示覆盖公开接口。
- 注释解释约束和原因，不复述代码；日志和异常不得包含敏感 surface 或原始上下文。
- **分层依赖**：严格遵守 [docs/MODULES.md](docs/MODULES.md) 的依赖规则；禁止反向依赖，跨层经接口。
- **测试（强制）**：所有新增业务逻辑必须有单元测试；核心用户流程用 CLI 集成冒烟或集成测试验证。本项目没有 GUI，不伪造页面端到端测试。
- **中间产物**：截图、日志、崩溃堆栈、审计记录一律放 `test-artifacts/`，严禁提交（见 [docs/DEVELOPMENT-GUIDELINES.md](docs/DEVELOPMENT-GUIDELINES.md)）。

## 6. 构建与测试命令

```powershell
# 安装/构建检查
python -m pip install -e .
python -m compileall -q common desensitize ocr organize training

# 单元与集成回归
pytest -q

# CLI 冒烟
python -m ocr --help
python -m organize --help
python -m desensitize --help
```

## 7. AI 助手工作约定

1. **先读文档**：开工前先读 docs/index.md 定位，涉及架构/模块再精读对应文档。
2. **不擅自改变技术路线**：既定决策如需变更，先在 `docs/adr/` 记录讨论，再实施。
3. **小步提交**：每个里程碑完成后再提交，提交信息说明改动与影响；分支与格式见 GIT-GUIDELINES。
4. **改代码必须同步文档**：避免文档失真；代码与文档冲突时以代码为最终事实修正文档。
5. **中间产物不入库**：统一放 `test-artifacts/`。
6. **数据安全红线**：不得提交 `docs/*_merged.md`、`test-artifacts/ocr-inputs/`、
   `test-artifacts/ocr-outputs/`、`test-artifacts/organized-outputs/`、
   `test-artifacts/desensitization-inputs/`、`test-artifacts/desensitization-outputs/`、mapping、密码、
   原始日志或其他用户数据；诊断输出只允许安全 ID、计数、行号、哈希和固定摘要。
