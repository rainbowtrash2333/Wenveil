# 模块边界（MODULES）

> 版本：V0.1（2026-09-12）｜状态：生效
> 本文档定义 AICanRead 的模块职责、边界与依赖规则；分层总览见
> [ARCHITECTURE.md](./ARCHITECTURE.md)。

## 1. 模块清单

| 模块 | 职责 | 代码位置 |
|------|------|----------|
| CLI | 参数解析、安全文件名、mask/restore/inspect/audit/benchmark | `desensitize/cli.py` |
| Pipeline | 编排规整、识别、白名单嵌套过滤、解析、替换和报告 | `desensitize/pipeline.py` |
| Normalizer | Unicode、OCR 空格、断行及结构字段规整 | `desensitize/normalizer/` |
| Recognizers | 规则、词典、机构关系和可选模型候选识别 | `desensitize/recognizers/` |
| Resolver | 确定性冲突消解与保护 Span 选择 | `desensitize/resolver.py` |
| Mapping | compact Token、AES-GCM 映射、哈希校验和恢复 | `desensitize/mapping.py` |
| Config/Rules | YAML 配置、实体词典、关系注册表、公共机构白名单 | `config/`、`rules/` |
| Audit | masked 文档残留与 Markdown 结构只读审计 | `desensitize/audit.py` |
| Training | 合成数据、标签校验、OCR 增强、评估和可选模型训练 | `training/` |
| Tests | 单元与集成回归 | `tests/` |

## 2. 各模块职责

- CLI 只负责交互与文件落盘，不实现实体识别规则。
- Pipeline 是唯一编排入口；Recognizer 不得自行改写文本或写文件。
- Normalizer 只做确定性规整，不判断业务实体。
- Resolver 不读取配置文件或 mapping，只对 Span 进行确定性选择。
- Mapping 不重新识别实体；恢复必须先校验密文和 masked/normalized 哈希。
- Training 不进入生产运行链路，生产包不得依赖可选训练框架。
- Audit 不回显命中的敏感文本，只输出类别、行号和固定摘要。

## 3. 依赖规则（强制）

- `CLI → Pipeline → Normalizer/Recognizers/Resolver/Mapping`；禁止反向依赖 CLI。
- `Recognizers → models.Span`；Recognizer 之间不得互相改写结果，通过 Pipeline 汇合。
- `training → desensitize.models` 可接受；`desensitize → training` 禁止。
- 生产代码不得读取 `output/`、`test-artifacts/` 或原始用户资料作为隐式配置。
- 白名单、机构关系和词典必须来自配置/规则文件，不得散落硬编码在业务流程。

**评审时按本节判定依赖违规。**

## 4. 关键测试目标

- Normalizer：幂等、OCR 空格/断行、结构字段修复。
- Recognizers/Resolver：边界、优先级、白名单、别名与重叠实体。
- Mapping：加密、篡改、密码错误、Token 碰撞、完全恢复。
- Audit：PII 形态、Token 完整性和 Markdown 表格结构。
- Training：标签无损、文档级切分、增强一致性、可选依赖延迟加载。

## 5. 构建与测试

```powershell
python -m compileall -q desensitize training
pytest -q
```
