# 开发规范（DEVELOPMENT-GUIDELINES）

> 版本：V0.3（2026-09-12）｜状态：生效
> 本文档定义 AICanRead 的 Python 开发、测试、审计和质量门禁；代码评审与提交以此为准。

## 1. 总则

1. 所有新增或修改的业务逻辑必须同步单元测试；测试必须验证行为而不是仅执行代码。
2. 面向用户的核心流程必须有 CLI 集成冒烟或集成测试；本项目没有 GUI，不伪造页面端到端测试。
3. 优先采用 AAA（Arrange/Act/Assert）结构；复杂逻辑先写失败用例，再实现和重构。
4. 规则、配置、mapping、白名单和模型候选均必须经过统一 Pipeline/Resolver，不得在 CLI 中重复实现实体识别。
5. 测试不得访问真实网络、真实用户资料或外部设备；使用临时目录、内存对象和合成数据。
6. 不记录原始敏感 surface、原文上下文、mapping 明文或密码；诊断只允许安全 ID、类别、计数、行号和哈希。

## 2. 分层与代码规范

- CLI 只负责参数、路径和输出；核心逻辑位于各自的功能模块包。
- 依赖方向固定为 `CLI → Module Pipeline`；OCR、Organize、Desensitize 不得互相导入业务实现，均可依赖 `common/`；生产代码不得导入 `training/`、`tests/` 或读取用户输出目录。
- Python 遵循 PEP 8；公开函数、类和模块接口使用类型提示，命名采用 `snake_case`/`PascalCase`。
- `Span` 是识别器和解析器之间的主要数据结构；识别器只读文本并返回候选，不改写文本。
- 新规则优先放入 `config/` 或 `rules/`；新增白名单名称必须有回归测试和边界说明。
- 不为一次性需求引入未使用的抽象、兜底或第三方依赖。

## 3. 测试分层策略

| 层级 | 位置/命令 | 重点 |
|------|-----------|------|
| 单元测试 | `tests/`，`pytest -q` | Normalizer、Recognizer、Resolver、Mapping、Audit、训练标签 |
| 集成测试 | `tests/` | Pipeline 全链路、白名单/关系、恢复哈希和报告 |
| CLI 冒烟 | 本地临时目录 | `mask → audit → restore`，安全文件名和退出码 |

关键测试目标：

- 规整幂等，且恢复结果等于规范化原文。
- 实体重叠由 Resolver 统一处理；白名单精确保留，但不能放行更长私有机构。
- 机构全称、简称、子公司/分公司使用稳定且短的关系 Token。
- mapping 加密、篡改、错误密码、Token 缺失和文件哈希不一致均失败。
- 审计发现高风险残留、未知 Token、Token 冲突和 Markdown 结构损坏时给出非零结果。
- 训练标签去标签后与原文一致，并按文档 ID 切分防止泄漏。

## 4. 测试写法与质量门禁

- 测试文件使用 `test_*.py`；测试函数命名为行为和预期结果，例如 `test_whitelist_keeps_public_name`。
- 每个测试聚焦一个行为点；外部路径使用 `tmp_path`，不依赖当前机器的固定用户目录。
- 公开 API 变化必须同步 README、架构/模块文档和版本说明。
- 当前质量门禁是：`compileall` 通过、`pytest -q` 全绿、三个 CLI 关键流程通过、`git diff --check` 无错误。
- 不设置未经测量的覆盖率数字作为假验收；需要覆盖率时使用独立报告记录实际数值。

## 5. 运行命令

```powershell
python -m compileall -q common desensitize ocr organize training
pytest -q
python -m desensitize --help
python -m ocr --help
python -m organize --help
```

## 6. 测试产物与提交前清理

截图、日志、崩溃堆栈、QA 记录和审计中间报告统一放在 `test-artifacts/`，不提交。
OCR/整理/脱敏输出、mapping、原始 `docs/*_merged.md`、`test-artifacts/desensitization-inputs/`、
`test-artifacts/desensitization-outputs/`、`test-artifacts/ocr-inputs/`、`test-artifacts/ocr-outputs/`、
`test-artifacts/organized-outputs/`、训练生成数据也不提交；`.gitignore`
已经覆盖这些路径。若需要保存验收结论，只在 `docs/` 写不含敏感原值的摘要。

## 7. 提交前 Checklist

- [ ] 代码改动有对应单元/集成测试。
- [ ] 相关 CLI 流程已冒烟，失败路径没有绕过安全校验。
- [ ] 配置、规则、skill 和文档已同步。
- [ ] `python -m compileall -q common desensitize ocr organize training` 通过。
- [ ] `pytest -q` 通过。
- [ ] `git diff --check` 通过，`git status` 没有原始资料、输出或测试产物。
