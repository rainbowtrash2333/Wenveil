# ADR-0005：统一工作流服务与 SQLite 作业状态库

> 版本：V0.3（2026-09-17）｜状态：已接受
> 关联：[统一工作流实施方案](../UNIFIED_WORKFLOW_SQLITE_IMPLEMENTATION_PLAN.md)、[ARCHITECTURE.md](../ARCHITECTURE.md)、[MODULES.md](../MODULES.md)

## 背景

Wenveil 当前的 OCR、文本整理和脱敏模块可以独立运行，桌面 Sidecar 负责临时串联它们，但没有统一的作业模型和持久化状态。
进程退出后无法查询处理到哪个文件、哪个阶段失败，也无法可靠地从最近成功阶段恢复。

用户需要一个与 UI 解耦的主接口，将 OCR、整理、合并、脱敏、审计和恢复统一编排，并在离线环境中保存安全的作业元数据。

## 决策

提议新增顶层 `workflow/` 编排层和 `WorkflowService`：

1. `WorkflowService` 统一提供 process、restore、resume、cancel 和 status 接口。
2. OCR、organize、desensitize 仍是独立业务模块，workflow 只调用其公开能力。
3. 使用 Python 标准库 SQLite 保存作业、文件项、阶段、事件、日志路径和产物元数据。
4. 数据库不保存原文、OCR/整理文本、密码、mapping 明文或 Token 原值。
5. 为实现文件级断点恢复，使用应用私有临时 checkpoint；任务完成或过期后清理。
6. Sidecar、CLI 和未来 UI 均只作为统一服务的适配器。

## 理由

- SQLite 适合离线桌面应用，不需要额外服务和部署运维。
- 将状态机放在 Python workflow 层，可以让 UI、CLI 和未来调度器共享同一语义。
- 只保存元数据和文件哈希，能够满足恢复、审计和历史查询，同时避免把用户文档复制进数据库。
- checkpoint 解决“只存状态无法重建后续输入”的实际矛盾；临时生命周期限制了额外敏感数据留存。

## 影响

- 新增 `workflow/` 包、SQLite migration、状态机、Repository 和安全日志。
- `desktop/bridge/sidecar.py` 将从业务编排器变为 JSON Lines 适配器。
- 现有独立 CLI 保留，但统一工作流将成为组合处理的推荐入口。
- 实施后必须同步 `ARCHITECTURE.md`、`MODULES.md`、`DEV-TOOLCHAIN.md`、`APP-VERSION.md` 和 `ROADMAP.md`。
- 同一作业需要租约和心跳，避免多个执行器并发恢复。
- 自动恢复需要本地输入路径；如安全策略禁止保存路径，必须由调用方重新绑定输入。

## 备选方案

1. **继续由 UI 编排**：状态机和恢复逻辑会复制到每个 UI，不采用。
2. **只写日志，不使用数据库**：无法可靠查询、过滤和事务性恢复，不采用。
3. **把原文或 OCR 结果存入 SQLite**：恢复简单但扩大敏感数据留存面，不采用。
4. **将 SQLite 作为 mapping 存储**：会混淆作业状态与加密映射边界，不采用。
5. **引入 PostgreSQL/Redis 等服务**：不符合当前离线桌面和轻量部署目标，不采用。
