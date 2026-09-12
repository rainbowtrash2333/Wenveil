# AICanRead 文档索引（INDEX）

> 版本：V0.3（2026-09-12）｜状态：生效
> 本文档是 `docs/` 下全部项目文档的唯一入口。AI 助手开始任务前先阅读本文档，再按任务
> 场景阅读对应文档。

## 1. 快速决策表

| 任务场景 | 必读 | 补充 |
|----------|------|------|
| 了解项目全貌、当前阶段 | [../AGENTS.md](../AGENTS.md) + [ROADMAP.md](./ROADMAP.md) | [APP-VERSION.md](./APP-VERSION.md) |
| 架构分层、依赖规则、数据流 | [ARCHITECTURE.md](./ARCHITECTURE.md) + [MODULES.md](./MODULES.md) | [ADR-0001](./adr/0001-hybrid-reversible-desensitization.md) |
| 修改脱敏识别、白名单或占位符 | [ocr_desensitization_implementation_plan.md](./ocr_desensitization_implementation_plan.md) | [ARCHITECTURE.md](./ARCHITECTURE.md)、[MODULES.md](./MODULES.md) |
| 编写/评审代码与测试 | [DEVELOPMENT-GUIDELINES.md](./DEVELOPMENT-GUIDELINES.md) | [MODULES.md](./MODULES.md) |
| 构建/安装/调试 | [DEV-TOOLCHAIN.md](./DEV-TOOLCHAIN.md) | [../README.md](../README.md) |
| Git 分支/提交 | [GIT-GUIDELINES.md](./GIT-GUIDELINES.md) | [DEVELOPMENT-GUIDELINES.md](./DEVELOPMENT-GUIDELINES.md) |
| 版本信息/已实现功能/已知限制 | [APP-VERSION.md](./APP-VERSION.md) | [ROADMAP.md](./ROADMAP.md) |
| 训练数据与可选小模型 | [../training/README.md](../training/README.md) | [ocr_desensitization_implementation_plan.md](./ocr_desensitization_implementation_plan.md) |
| 修改/新增文档 | [DOCUMENTATION-GUIDE.md](./DOCUMENTATION-GUIDE.md) | 本文档 |

## 2. 项目级文档（docs/ 根目录）

| 文档 | 作用 | 何时阅读 |
|------|------|----------|
| [DOCUMENTATION-GUIDE.md](./DOCUMENTATION-GUIDE.md) | 文档组织、写作、同步和冲突处理规范 | 新增/修改/删除文档前 |
| [ARCHITECTURE.md](./ARCHITECTURE.md) | 分层、核心概念、数据流和安全边界 | 架构或跨模块改动 |
| [MODULES.md](./MODULES.md) | 模块职责、边界、依赖和测试目标 | 新增代码或调整模块 |
| [DEVELOPMENT-GUIDELINES.md](./DEVELOPMENT-GUIDELINES.md) | Python 开发、测试和质量门禁 | 修改代码或测试 |
| [GIT-GUIDELINES.md](./GIT-GUIDELINES.md) | 分支、提交和禁止入库内容 | 任何提交前 |
| [DEV-TOOLCHAIN.md](./DEV-TOOLCHAIN.md) | 安装、编译、测试和 CLI 命令 | 构建、调试或验收 |
| [APP-VERSION.md](./APP-VERSION.md) | 版本、能力、验证结果和限制 | 确认当前实现状态 |
| [ROADMAP.md](./ROADMAP.md) | 阶段目标、里程碑和 ADR 索引 | 规划或变更路线 |
| [ocr_desensitization_implementation_plan.md](./ocr_desensitization_implementation_plan.md) | OCR 金融文档脱敏实施方案与验收标准 | 脱敏策略、模型和数据集改动 |

## 3. 专项文档与目录

- [../skills/ocr-desensitization/SKILL.md](../skills/ocr-desensitization/SKILL.md)：AI 调用脱敏、审计、恢复和敏感信息输出约束。
- [../training/README.md](../training/README.md)：金融领域合成数据、标签校验、OCR 增强和可选 Qwen 训练入口。
- [adr/](./adr/)：已接受的架构决策记录。
- [archive/](./archive/)：历史设计资料，仅作背景参考；当前实施以本索引和实施方案为准。

项目没有 GUI 或前端页面，因此不建立 `docs/design/` 设计稿目录，也不启用页面设计角色。

## 4. 推荐阅读顺序

新接触项目：`AGENTS.md` → `docs/index.md` → `ARCHITECTURE.md` → `MODULES.md` →
`APP-VERSION.md` → `ROADMAP.md`。

修改脱敏逻辑：再读 `ocr_desensitization_implementation_plan.md`、项目 skill、相关规则和测试。

## 5. 维护约定

- 新增、改名、移动或删除 `docs/` 下文档时，必须同步更新本文档及受影响的相对链接。
- 每份生效文档顶部必须有版本、日期和状态；现状、规划和限制必须明确区分。
- 原始用户文档、脱敏输出、映射密文、训练生成数据和测试产物不纳入 Git；规则、代码、测试和可复现脚本可以纳入 Git。
- 授权原始 OCR 输入统一放在 `test-artifacts/desensitization-inputs/`，脱敏、恢复和审计产物统一放在 `test-artifacts/desensitization-outputs/`；两个目录均不入库。
- 写作规范、同步核对和冲突处理见 [DOCUMENTATION-GUIDE.md](./DOCUMENTATION-GUIDE.md)。
- 文档与代码冲突时，先以可验证的代码行为为事实，再按同步矩阵修正文档。
