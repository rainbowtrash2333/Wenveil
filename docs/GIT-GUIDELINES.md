# Git 分支与提交规范（GIT-GUIDELINES）

> 版本：V0.1（2026-09-12）｜状态：生效
> 本文档与 `AGENTS.md`、`DEVELOPMENT-GUIDELINES.md` 配套使用；是 Git 相关最高约束。

## 1. 分支职责

- `dev`：本地开发分支，承载实现与验证；**AI 只在该分支工作**。
- `main`：发布/稳定分支，由项目维护者手动维护和推送。
- AI **不执行**向远端推送、合并到 `sync`/`main` 或重写历史的操作。

## 2. 提交要求

- 每个里程碑使用小步提交，提交信息说明"改动 + 影响"。
- 推荐格式：`<type>(<scope>): <summary>`，例如 `fix(core): 收紧启动校验`。
- `type` 使用 `feat`、`fix`、`test`、`docs`、`refactor`、`build` 或 `chore`。
- 提交前必须确认代码、测试和文档已经同步；未验证的能力不得写成已完成。
- 需要提交`.codex`下的配置

## 3. 禁止入库内容

不得提交：

- 构建产物、包管理器缓存、临时目录、日志、崩溃 dump、截图、设备抓取输出；
- 用户数据、密钥、签名文件、账号凭据、`.env`；
- `test-artifacts/` 下的测试产物（如需保留结论，提交经过脱敏的文档摘要）；
- 任何绕过版权/许可保护的实现材料。

## 4. 提交前检查

```bash
git status --short
git diff --check
python -m compileall -q common desensitize ocr organize training
pytest -q
```

涉及 CLI 行为时补跑 `python -m desensitize --help`、`python -m ocr --help`、
`python -m organize --help` 和本地无敏感测试夹具的流程冒烟。任何失败或环境缺失必须在交付说明中明确记录，不能用其他测试替代。
