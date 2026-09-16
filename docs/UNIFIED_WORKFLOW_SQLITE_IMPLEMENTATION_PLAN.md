# 统一工作流服务与 SQLite 状态库实施方案

> 版本：V0.3（2026-09-17）｜状态：阶段 D 恢复验收完成，持续迭代中
> 本方案描述将 OCR、文本整理、文件合并、脱敏、审计和脱敏恢复统一到一个独立工作流服务中的实施方式。
> 现有模块职责与数据安全边界仍以 [ARCHITECTURE.md](./ARCHITECTURE.md)、[MODULES.md](./MODULES.md)
> 和 [ADR-0001](./adr/0001-hybrid-reversible-desensitization.md) 为准。

## 1. 目标与非目标

### 1.1 目标

新增一个不依赖 UI 的 `WorkflowService`，作为所有上层调用方的统一入口：

```text
CLI / Tauri Sidecar / HTTP Adapter / 后续新 UI
                         ↓
              workflow.WorkflowService
                         ↓
       输入解析 → OCR → 整理 → 合并 → 脱敏 → 审计
                         ↓
                 SQLite 作业状态库
```

统一服务需要能够：

1. 以一次请求描述完整处理流程，而不是由 UI 自己拼接步骤。
2. 保存作业、文件、阶段、进度、错误、日志路径和输出产物路径。
3. 在程序异常退出后，根据数据库和 checkpoint 从最近一个有效阶段继续。
4. 查询某个作业处理了哪些文件、当前进行到哪一步、输出保存在哪里。
5. 将恢复也作为可追踪的作业处理，但密码和映射明文不进入数据库。
6. 保留 `ocr/`、`organize/`、`desensitize/` 的独立 CLI 和可复用能力。

### 1.2 非目标

- 不把 OCR 文本、整理文本、原文、masked 原值或映射明文写入 SQLite。
- 不让 UI 实现业务编排、断点判断、重试和恢复规则。
- 不让数据库替代加密 mapping；可恢复模式的 mapping 仍由 `MappingVault` 生成并作为加密文件保存，无密码模式不生成 mapping。
- 不在本方案中重构现有 UI 视觉和交互。
- 不把训练模块接入生产工作流；模型仍只作为脱敏识别候选来源。

## 2. 首版实现前的缺口与当前剩余项

以下是首版实现前的缺口；统一工作流首版已经补齐，剩余项主要是并发调度、UI 历史页和更细的生产运维策略：

| 当前能力 | 现状 | 缺口 |
|---|---|---|
| OCR | `ocr/pipeline.py` 负责发现、并发转换、合并和写盘；`workflow/runner.py` 可调用 | 统一服务暂未拆出独立 OCR worker 调度器 |
| 文本整理 | `organize/core.py` 处理单文件或目录；workflow 已登记阶段状态 | UI 尚未展示完整作业历史 |
| 脱敏 | `desensitize/pipeline.py` 负责规整、识别、替换和 mapping；workflow 已支持重试 | 并发 worker 和长任务调度待后续 |
| 桌面编排 | `desktop/bridge/sidecar.py` 只做 JSON Lines 适配 | UI 仍使用兼容结果格式，尚未切换作业历史 API |
| 进度 | Sidecar 通过 JSON Lines 返回运行期事件，SQLite 保存历史状态 | 尚未提供独立后台调度进程 |
| 输出 | workflow 统一登记产物、哈希、大小和可见性 | 产物保留期限策略待后续 |

因此新层应放在现有三个业务模块之上，而不是把三个模块重新合并成一个包：

```text
workflow/                 # 新增统一编排层
  ├── api.py              # 稳定公开接口
  ├── runner.py           # 作业状态机和阶段执行
  ├── store.py            # SQLite Repository
  ├── models.py           # 请求、快照、阶段状态
  ├── checkpoints.py      # 临时 checkpoint 和原子写入
  └── logging.py          # job-scoped 安全日志

ocr/                      # 保持独立，可被 workflow 调用
organize/                 # 保持独立，可被 workflow 调用
desensitize/              # 保持独立，可被 workflow 调用
desktop/bridge/           # 只负责传输和进程生命周期
```

依赖方向调整为：`workflow → ocr/organize/desensitize/common`，而不是反向依赖。
实施完成后应同步更新 [ARCHITECTURE.md](./ARCHITECTURE.md) 和 [MODULES.md](./MODULES.md)。

## 3. 统一接口设计

### 3.1 请求对象

以下为拟定的 Python 公共接口，具体字段名在实现阶段冻结并加测试：

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True, slots=True)
class WorkflowSteps:
    ocr: bool = True
    organize: bool = True
    merge: bool = True
    mask: bool = True
    audit: bool = True


@dataclass(frozen=True, slots=True)
class ProcessRequest:
    inputs: tuple[Path, ...]
    output_dir: Path
    steps: WorkflowSteps = WorkflowSteps()
    config_path: Path | None = None
    entities: tuple[str, ...] = ()
    ai_enhanced: bool = True
    ocr_mode: Literal["auto", "fast", "enhanced"] = "auto"
    device: Literal["auto", "cpu", "gpu"] = "auto"
    retain_intermediate: bool = False
    allow_partial: bool = False
    output_name: str | None = None


@dataclass(frozen=True, slots=True)
class RestoreRequest:
    masked_path: Path
    mapping_path: Path
    output_dir: Path
    password: str
    restore_filename: bool = False
```

密码只存在于调用栈和进程内存中。`ProcessRequest` 的持久化快照必须先去除密码、文件内容和 base64 内容。

### 3.2 服务接口

```python
class WorkflowService:
    def create_process_job(self, request: ProcessRequest) -> JobSnapshot: ...
    def process(self, request: ProcessRequest, *, progress=None) -> JobSnapshot: ...
    def restore(self, request: RestoreRequest, *, progress=None) -> JobSnapshot: ...
    def resume(self, job_id: str, *, password: str | None = None, progress=None) -> JobSnapshot: ...
    def cancel(self, job_id: str) -> JobSnapshot: ...
    def get_status(self, job_id: str) -> JobSnapshot: ...
    def list_jobs(self, *, limit: int = 50, status: str | None = None) -> list[JobSnapshot]: ...
```

语义如下：

- `process()`：创建并执行一个完整作业，适合 CLI 和 Sidecar。
- `create_process_job()`：只创建 `queued` 作业，适合未来异步调度器。
- `resume()`：读取数据库状态，校验 checkpoint/产物后，从最早无效阶段继续。
- `cancel()`：只请求取消并落库；执行线程在阶段边界和文件边界检查取消标记。
- `restore()`：创建 `operation=restore` 的独立作业，沿用相同状态和日志机制。
- `JobSnapshot` 只返回安全状态、计数、阶段、错误摘要和产物引用，不返回原文、实体 surface 或 mapping 明文。
- `ItemSnapshot` 可返回本地 `source_path` 以便 UI/CLI 展示已处理文件；该路径只属于本地状态查询，不写入普通日志、安全报告或事件摘要。

### 3.3 依赖校验

统一服务在创建作业时一次性校验：

| 规则 | 行为 |
|---|---|
| 没有任何步骤 | 拒绝创建 |
| `audit=true` 但 `mask=false` | 拒绝创建 |
| `ocr=false` 且输入含 PDF/图片/Office | 拒绝创建 |
| `merge=true` | 先完成所有文件级 OCR/整理，再执行作业级合并；可用受校验的 `output_name` 指定单层 Markdown 文件名 |
| `mask=true` 且设置密码 | 合并结果作为一个文档脱敏，生成一套加密 mapping |
| `mask=true` 且未设置密码 | 合并结果作为一个文档脱敏，只生成不可恢复的 masked 输出 |
| `merge=false` 且 `mask=true` 且设置密码 | 每个输入文件独立脱敏，分别生成加密 mapping |
| `merge=false` 且 `mask=true` 且未设置密码 | 每个输入文件独立脱敏，只生成不可恢复的 masked 输出 |
| 某文件失败且 `allow_partial=false` | 不发布作业级 merged/masked 结果，作业标为 `partial_failed` |
| 恢复作业缺少密码 | 进入 `waiting_secret`，不记录密码，不伪造失败 |

默认把一批输入视为一个文档集合：文件级阶段可以并发，合并、脱敏和审计为作业级阶段。
这样可保证一个合并文档内的机构简称、人员重复提及和 Token 关系保持一致。

## 4. 阶段执行模型

### 4.1 处理作业

```text
创建作业
  ↓
输入展开与文件指纹
  ↓
文件级 OCR（可跳过）
  ↓
文件级整理（可跳过）
  ↓
作业级合并（可跳过）
  ↓
作业级规整/识别/Resolver/一次性替换
  ↓
写入 masked、mapping、report
  ↓
作业级 audit（可选）
  ↓
发布最终状态并清理临时 checkpoint
```

各阶段只调用现有模块：

| 工作流阶段 | 调用方向 | 产物 |
|---|---|---|
| `ocr` | `DocumentConverter` 及现有 PDF 快速路径 | 临时 `.ocr.checkpoint` |
| `organize` | `TextOrganizer.organize_text()` | 临时 `.organized.checkpoint` |
| `merge` | 稳定排序后调用现有 Markdown 合并逻辑或提取出的纯函数 | merged checkpoint 或最终 Markdown |
| `mask` | `Desensitizer.anonymize()` | `.normalized.md`、`.masked.md`、`.mapping.enc`、`.report.json` |
| `audit` | `audit_masked_text()` | 安全审计摘要，可选 `.audit.json` |
| `restore` | `MappingVault.load()` + `restore_text()` | `.restored.md` |

OCR 和整理结果默认不作为用户产物发布。它们仅在启用断点模式时写入应用私有 workspace，供后续阶段复用。

### 4.2 文件合并规则

合并必须确定性执行：

1. 输入文件按规范化相对路径和安全文件 ID 排序。
2. 每个文件生成安全标题，不把原始文件名写入日志或审计报告。
3. 文件内容只从已成功的文件级 checkpoint 读取。
4. 任一必要文件失败时，默认阻止作业级合并；只有 `allow_partial=true` 才允许显式生成部分结果。
5. 合并产物完成后使用临时文件写入，再通过原子重命名发布。

这一步与当前 OCR 的“项目内合并”职责相近，但统一服务需要把合并纳入同一个作业状态机和产物登记机制。

## 5. SQLite 状态库设计

### 5.1 存储位置

- 生产默认使用操作系统的 Wenveil 应用数据目录，例如 Windows 的 `%LOCALAPPDATA%/Wenveil/`。
- 测试和开发通过构造函数显式传入数据库路径，使用临时目录或被忽略的 `test-artifacts/`。
- 数据库使用 SQLite 标准库 `sqlite3`，不新增外部数据库服务或 ORM 依赖。
- 连接初始化必须执行 `PRAGMA foreign_keys=ON`、`journal_mode=WAL`、合理的 `busy_timeout`。

### 5.2 表结构

#### `schema_migrations`

记录已执行的 schema 版本，迁移脚本使用递增编号，不能修改已执行迁移。

#### `workflow_jobs`

| 字段 | 说明 |
|---|---|
| `job_id` | 随机、不可逆、稳定的作业 ID，主键 |
| `operation` | `process` 或 `restore` |
| `status` | 作业状态 |
| `workflow_version` | 工作流契约版本 |
| `config_hash` | 脱敏/OCR 配置摘要哈希 |
| `steps_json` | 去除密码和内容后的步骤快照 |
| `output_dir` | 本地输出位置，不能进入普通日志或安全报告 |
| `log_path` | 本次作业日志路径 |
| `created_at/started_at/updated_at/finished_at` | 生命周期时间 |
| `heartbeat_at` | 运行中的租约心跳 |
| `resume_count` | 恢复次数 |
| `last_error_code` | 固定错误类别 |
| `last_error_summary` | 不含原文的固定摘要 |

#### `workflow_items`

| 字段 | 说明 |
|---|---|
| `item_id` | 作业内文件项 ID；与 `job_id` 组成联合主键 |
| `job_id` | 所属作业 |
| `ordinal` | 稳定处理顺序 |
| `source_path` | 本地恢复所需的输入路径；仅在本地数据库保存，禁止进入日志/报告 |
| `source_path_hash` | 路径哈希，用于校验路径是否变化 |
| `extension` | 文件扩展名 |
| `size_bytes` | 输入大小 |
| `source_sha256` | 可选输入指纹 |
| `status` | `queued/running/succeeded/failed/interrupted` |

`source_path` 是自动恢复所必需的本地元数据，不是文档内容。若部署环境禁止保存原始路径，应提供
`store_source_paths=false` 模式；这种模式下 `resume()` 必须由调用方重新绑定输入文件。

#### `workflow_stages`

| 字段 | 说明 |
|---|---|
| `stage_id` | 主键 |
| `job_id/item_id` | 作业级或文件级阶段归属 |
| `stage_name` | `input/ocr/organize/merge/mask/audit/restore` |
| `status` | `pending/running/succeeded/failed/skipped/interrupted/waiting_secret` |
| `progress` | 0 到 1 |
| `attempt` | 执行次数 |
| `started_at/finished_at/heartbeat_at` | 阶段时间 |
| `artifact_id` | 成功阶段关联产物 |
| `error_code/error_summary` | 安全失败信息 |

#### `workflow_artifacts`

只保存产物元数据，不保存产物内容：

```text
artifact_id / job_id / item_id / kind
path / sha256 / size_bytes / visible / retained / created_at
```

`kind` 包括 `log`、`ocr_checkpoint`、`organized_checkpoint`、`merged`、`normalized`、`masked`、
`mapping`、`report`、`audit`、`restored`。

#### `workflow_events`

用于查询状态变化和恢复诊断：

```text
event_id / job_id / item_id / stage_id / sequence
event_type / status / progress / safe_summary / created_at
```

事件只允许安全 ID、阶段名、计数、耗时、固定摘要和错误类型；禁止写入原文、文件名、密码、Token 映射值或异常上下文。

### 5.3 状态机

```text
queued → running → succeeded
             ├──→ partial_failed
             ├──→ failed
             ├──→ cancel_requested → cancelled
             └──→ interrupted → resume → running

mask/restore 缺少密码：running → waiting_secret → running
```

状态更新必须和对应产物登记在同一个 SQLite 事务中：

1. 先将阶段置为 `running` 并增加 `attempt`。
2. 阶段结果写入 `.tmp` 文件。
3. 对文件计算大小和 SHA-256。
4. 原子重命名为正式产物。
5. 插入/更新 `workflow_artifacts`。
6. 将阶段置为 `succeeded`，提交事务。

如果进程在第 2–5 步之间退出，数据库中的旧状态不会被错误地标成成功；下次恢复时重新校验并重做该阶段。

## 6. 断点恢复设计

### 6.1 为什么需要临时 checkpoint

仅保存“某文件 OCR 已完成”的状态不足以恢复，因为 OCR 文本不在数据库中，后续合并阶段无法重建输入。
因此采用以下折中：

- SQLite 只保存状态和 checkpoint 的路径、大小、哈希；不保存 OCR/整理文本。
- OCR/整理结果写入应用私有、不可见的 job workspace。
- 作业成功、取消清理或超过保留期限后删除 checkpoint；数据库保留 `retained=0` 的元数据。
- 如果用户选择严格的 `status_only` 模式，则不保留 checkpoint，恢复时允许从最近可重建阶段重新执行，不能承诺跳过已完成 OCR。

### 6.2 恢复算法

1. 服务启动时扫描 `running` 作业；心跳超过租约阈值的作业标记为 `interrupted`。
2. `resume(job_id)` 获取数据库租约，禁止同一作业并发恢复。
3. 从最后一个 `succeeded` 阶段开始检查关联产物是否存在、大小和哈希是否匹配；输入 checkpoint 可用时仍校验源文件未被替换。
4. 任一产物缺失、哈希不匹配或源文件指纹变化时，将当前阶段及其下游阶段重做；源文件变化使用固定错误拒绝静默恢复。
5. 保留其他文件已成功的阶段，只重做最早失效的文件/作业阶段。
6. 若下一阶段需要密码而调用方没有重新提供，返回 `waiting_secret`，不把密码写入 DB、日志或事件。
7. 所有输出使用临时文件和原子替换，避免恢复过程中留下“看似完成”的半文件。

### 6.3 失败与部分成功

- 文件级 OCR/整理失败：记录文件级失败和安全错误摘要，继续处理其他文件。
- 作业级合并/脱敏失败：停止下游阶段，作业标为 `failed`。
- 默认不发布不完整 merged/masked 结果；只有请求明确 `allow_partial=true` 才允许部分结果。
- 恢复时先重试失败阶段，不重复已通过完整性校验的最终产物。

## 7. 日志、产物与安全边界

每个作业生成独立日志路径，例如：

```text
<app-data>/Wenveil/logs/job-<job-id>.log
```

日志和数据库只记录：

- `job_id`、`item_id`、阶段名和状态；
- 文件数量、字符数、页数、耗时、输出大小；
- SHA-256、配置哈希和错误类型；
- 固定的用户可理解错误摘要。

明确禁止记录：

- 原文、OCR 文本、整理文本、masked 文本片段；
- 原始文件名、客户名、项目名和路径上下文到普通日志；
- 密码、base64 文件内容、Token 到原值的映射；
- Python 异常中可能携带的原文或本地绝对路径。

最终产物统一登记到 `workflow_artifacts`。mapping 仍由现有 `MappingVault` 用 AES-GCM 加密，数据库只保存 mapping 文件路径、大小和哈希。

## 8. 拟新增与改造的代码位置

### 8.1 新增

```text
workflow/
├── __init__.py
├── __main__.py
├── api.py              # WorkflowService、请求和快照导出
├── models.py           # JobSnapshot、StageSnapshot、ArtifactRef
├── runner.py           # 状态机、租约、重试、取消、恢复和阶段适配器
├── store.py            # sqlite3 Repository、事务和查询
├── migrations/
│   ├── 001_initial.sql
│   └── 002_scope_items_to_job.sql
├── checkpoints.py      # 私有 workspace、哈希、原子写入和清理
└── logging.py          # job-scoped 安全日志
```

当前阶段适配器集中在 `runner.py`，以减少首版跨文件接口数量；当 OCR/合并阶段需要独立并发调度时，再按本方案拆分为 `stages/` 子包，公开 API 和 SQLite 契约不变。

### 8.2 改造

1. `desktop/bridge/sidecar.py`：删除其中的业务步骤串联，改为调用 `WorkflowService.process()` 和 `WorkflowService.restore()`。
2. `desktop/src/bridge.ts`：继续只传输请求和安全事件，不增加数据库逻辑。
3. `ocr/`、`organize/`、`desensitize/`：保持现有独立 CLI；必要时只提取可复用纯函数，不反向依赖 `workflow/`。
4. `pyproject.toml`：将 `workflow*` 纳入 Python 包；SQLite 使用标准库，不新增数据库依赖。
5. 统一服务稳定后，再同步 `ARCHITECTURE.md`、`MODULES.md`、`DEV-TOOLCHAIN.md`、`APP-VERSION.md` 和 `ROADMAP.md`。

## 9. 实施阶段

### 阶段 A：契约与数据库骨架（已完成）

- [x] 冻结 `ProcessRequest`、`RestoreRequest`、`JobSnapshot` 和状态枚举。
- [x] 实现 SQLite migration、Repository、事务和查询接口。
- [x] 实现安全日志和敏感字段过滤。
- [x] 增加数据库 schema、状态流转和迁移集成测试。

### 阶段 B：统一阶段执行器（核心链路已完成）

- [x] 实现输入展开、文件指纹和确定性排序。
- [x] 接入现有 OCR、organize、merge、desensitize、audit、restore 代码。
- [x] 实现私有 checkpoint、原子产物发布和清理策略。
- [x] 实现作业租约、心跳、取消、失败恢复和 `waiting_secret`。

### 阶段 C：入口迁移（核心入口已完成）

- [x] 新增 `python -m workflow` 和 `wenveil-workflow` CLI，作为无 UI 验收入口。
- [x] 让 Python Sidecar 只做 JSON Lines 适配和调用统一服务。
- [x] 保留三个旧 CLI，验证兼容行为不回退。
- [x] 不修改当前 UI；现有 UI 通过原桥接继续获得结果。

### 阶段 D：断点恢复验收（已完成）

- [x] 在 OCR、整理、合并、脱敏和审计阶段分别注入崩溃，验证恢复位置。
- [x] 验证缺失、篡改和哈希不匹配 checkpoint 会被重做。
- [x] 验证同一作业不能被两个执行器同时恢复。
- [x] 验证恢复需要密码时不会落库，重新提供密码后可以继续。
- [x] 验证成功/取消后 checkpoint 被清理，最终产物和日志路径仍可查询。

### 阶段 E：后续 UI 重构接口

- [ ] UI 只调用 `create/process/resume/cancel/get_status/list_jobs`。
- [ ] UI 不再维护步骤依赖、文件级重试或状态机。
- [ ] 可增加作业历史、失败重试、输出定位和恢复密码输入，而不改变 Python 业务层。

## 10. 测试与验收标准

拟新增测试：

```text
tests/test_workflow_store.py
tests/test_workflow_recovery.py
tests/test_workflow_state_machine.py
tests/test_workflow_resume.py
tests/test_workflow_checkpoints.py
tests/test_workflow_security.py
tests/test_workflow_integration.py
```

验收必须覆盖：

- 单文件、多文件、目录输入和确定性顺序；
- OCR 跳过、OCR 快速路径、文本直读和混合输入；
- 整理、合并、脱敏、审计、恢复的所有合法组合；
- 每个阶段的成功、失败、取消、进程中断和重复执行；
- SQLite 事务回滚、WAL 并发读、单作业租约和 schema migration；
- 数据库、日志和安全报告中不存在原文、密码、mapping 明文和原始上下文；
- final artifact 哈希与数据库记录一致；
- 恢复结果满足现有 `restore(mask(normalize(source))) == normalize(source)` 契约。

完成实现后的质量门禁仍使用项目现有命令，并增加统一入口回归：

```powershell
python -m compileall -q common desensitize ocr organize training workflow
pytest -q
python -m workflow --help
```

## 11. 建议冻结的决策

1. 统一工作流层新增为顶层 `workflow/`，现有三个业务模块保持独立。
2. SQLite 只保存作业元数据、状态、事件、产物路径和哈希，不保存任何文本内容或秘密。
3. 为实现真正的断点恢复，默认保留应用私有临时 checkpoint；成功后清理，不作为用户可见输出。
4. 合并模式下以一批输入为一个作业级文档；设置密码时统一生成一套加密脱敏 mapping，无密码时不生成 mapping。
5. 默认禁止部分输入生成作业级最终结果，必须显式开启 `allow_partial`。
6. 密码只由调用方在执行/恢复时提供，永不持久化；缺少密码时使用 `waiting_secret` 状态。
7. Sidecar、CLI 和未来 UI 都只调用 `WorkflowService`，不复制工作流逻辑。
