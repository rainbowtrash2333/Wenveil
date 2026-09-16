# OCR 外层生命周期 Bug 审计报告

> 版本：V0.1（2026-09-16）｜状态：生效（根因待下一次异常复现）
>
> 本报告只记录安全 ID、页数、计数、耗时、配置摘要和哈希，不包含用户文档原文、原始文件名或 OCR 文本。

## 1. 审计结论

### 1.1 已确认

1. 32 页扫描 PDF 实际执行 **32 次 OCR**，没有重复 OCR。
2. 异常运行的额外耗时位于文档内部计时结束之后、`_convert_files` 返回之前的生命周期窗口。
3. 直接调用 `DocumentConverter.convert()` 时没有出现该长尾，PDF 预检、渲染、OCR 推理和 Markdown 组装不是 47 分钟异常的来源。
4. 页级 worker 设为 1 时仍能吃满 CPU，说明“单 worker”不是“进程单线程”；当前 ONNX Runtime 配置为自动 CPU 线程，RapidOCR 和 OpenCV/NumPy 也会使用原生线程。

### 1.2 尚未确认

以下具体阻塞点尚无异常样本的埋点证据：

- `converter.convert()` 完成后的析构或 native 资源回收；
- `Future` 状态通知与主线程收集之间的等待；
- 外层 `ThreadPoolExecutor.shutdown(wait=True)`；
- ONNX Runtime、pypdfium2 与嵌套线程池的资源释放交互。

因此，当前最准确的结论是：

> **已确认外层生命周期存在异常时间窗口；嵌套线程池和 ONNX Runtime 资源竞争是最高优先级怀疑，但尚未证明其为唯一根因。**

## 2. 审计范围与环境

- 样本：授权 32 页扫描 PDF，内容 SHA-256 前缀为 `91eb0ac9c059a6db`；
- 运行环境：Python 3.13.12、Windows 11；
- ONNX Runtime provider：`AzureExecutionProvider`、`CPUExecutionProvider`；
- 未发现可用的 DirectML/CUDA provider；
- 默认页级 OCR 配置：`fast_scan_workers=2`、ONNX Runtime `intra/inter_op_num_threads=-1`；
- 所有 profile 均只写入 `test-artifacts/logs/`，不进入版本库。

## 3. 异常样本证据

异常 profile：[`ocr-profile-32.json`](test-artifacts/logs/ocr-profile-32.json)

| 指标 | 数值 |
|---|---:|
| 流水线总耗时 | 3034.867 s |
| 项目 `files.convert` | 3034.800 s |
| 文档内部耗时 | 187.049 s |
| `pdf.fast_path` | 186.954 s |
| 额外未解释时间 | 2847.751 s（约 47 分 28 秒） |
| PDF 页数 | 32 |
| OCR 调用次数 | 32 |
| OCR 结果条数 | 799 |
| 输出字符数 | 21908 |

`files.convert - document elapsed` 的差值说明异常不在 OCR 页级阶段；但由于旧 profile 没有记录函数返回边界，尚不能区分 worker 返回前的尾部等待与 executor shutdown。

## 4. 对照实验

### 4.1 绕过项目流水线

profile：[`ocr-profile-direct-32.json`](test-artifacts/logs/ocr-profile-direct-32.json)

- 转换器调用总耗时：165.517 s；
- 文档内部耗时：165.468 s；
- 外层差值：约 49 ms；
- OCR 调用次数：32；输出字符数：21908。

这证明绕过项目/文件级调度后，异常等待消失。

### 4.2 新埋点完整流水线

profile：[`ocr-profile-audit-normal.json`](test-artifacts/logs/ocr-profile-audit-normal.json)

| 阶段 | 时间间隔 |
|---|---:|
| `document.profile_finish → pipeline.worker_return` | 0.024 ms |
| `pipeline.worker_return → pipeline.future_done` | 0.242 ms |
| `pipeline.future_done → executor_shutdown_before` | 0.106 ms |
| `executor_shutdown_before → after` | 1.006 ms |
| 流水线总耗时 | 178.271 s |

该次没有出现外层长尾。

### 4.3 并发配置对照

| 文件级 worker | 页级 OCR worker | 总耗时 | OCR 累计耗时 | shutdown | 结果哈希 |
|---:|---:|---:|---:|---:|---|
| 3 | 2 | 178.271 s | 332.142 s | 1.006 ms | `4A5C1DE1…D2316` |
| 1 | 2 | 167.807 s | 307.937 s | 0.896 ms | `4A5C1DE1…D2316` |
| 1 | 1 | 151.549 s | 150.132 s | 0.728 ms | `4A5C1DE1…D2316` |

页级 worker 为 1 的样本反而更快，支持“两个 RapidOCR/ONNX CPU 线程池相互竞争”的假设；但这仍是性能相关性，不是根因证明。

### 4.4 同一进程连续运行

profile：[`repeat-1`](test-artifacts/logs/ocr-profile-audit-repeat-1.json)、[`repeat-2`](test-artifacts/logs/ocr-profile-audit-repeat-2.json)

| 次数 | 总耗时 | shutdown | OCR 调用次数 |
|---:|---:|---:|---:|
| 1 | 168.486 s | 0.610 ms | 32 |
| 2 | 147.087 s | 0.262 ms | 32 |

未发现同一进程连续调用导致的稳定性恶化。

### 4.5 旧式外层提交对照

使用旧式 `executor.submit(converter.convert, path)` 结构再次运行，耗时约 174.982 s，Future 正常完成，未复现 3035 秒异常。因此目前没有证据表明 `_convert_one` 包装或显式 shutdown 本身修复了问题。

## 5. 代码路径审计

当前纯扫描 PDF 的并发结构为：

```text
项目流水线
└─ 文件级 ThreadPoolExecutor（concurrency.max_workers）
   └─ DocumentConverter.convert
      └─ PDF 快速路径
         └─ 页级 ThreadPoolExecutor（docling.fast_scan_workers）
            └─ RapidOCR
               └─ 多个 ONNX Runtime CPU 推理会话和内部线程池
```

该结构存在资源过度竞争的合理风险，尤其是配置使用 `-1` 自动线程数时。但当前新 profile 的正常运行中，worker 返回、Future 完成和 shutdown 都是连续发生的，因此不能把该风险直接写成已证实的死锁或泄漏。

另一个重要观测限制是：`DocumentProfile.finish()` 在 `converter.convert()` 真正返回前执行。新埋点因此加入了 `worker_convert_done`、`worker_return`、`future_done` 和 executor shutdown 前后事件，下一次异常复现时可精确切分时间窗口。

## 6. 已加入的诊断能力

仅在开启 `--profile` 时记录以下安全事件：

- `pipeline.submit_before/after`；
- `pipeline.worker_enter`、`worker_convert_before/done/return`；
- `document.profile_finish`、`document.record_before/after`；
- `pipeline.future_wait_begin`、`future_done`、`future_result_before/after`；
- `pipeline.executor_shutdown_before/after`。

事件只包含事件名、相对时间、线程名和安全 document ID，不包含路径、文件名、原文或 OCR 文本。普通 OCR 默认关闭 profile，执行路径不增加这些观测开销。

## 7. 下一次异常的判定规则

| 异常间隔 | 结论方向 |
|---|---|
| `profile_finish → worker_convert_done` 很长 | `convert()` 内部尾部或资源释放 |
| `worker_convert_done → worker_return` 很长 | 返回前析构、Python/native 边界 |
| `worker_return → future_done` 很长 | Future/线程池状态通知异常 |
| `future_done → shutdown_after` 很长 | executor shutdown 或 native 线程退出 |
| 上述均正常但 `files.convert` 仍很长 | `_convert_files` 外层收集、进度回调或其他未埋点逻辑 |

## 8. 当前建议

在根因证据出现前，不将“全改单线程”作为最终修复。针对单个长 PDF 的临时稳定配置可使用：

```yaml
concurrency:
  max_workers: 1

docling:
  fast_scan_workers: 1
```

注意：这只是减少任务级并发，不能限制 ONNX Runtime 的原生 CPU 线程。是否将 `intra_op_num_threads` 和 `inter_op_num_threads` 改为固定值，应另做独立 A/B 测试。

## 9. 验证状态

- `pytest -q`：通过；
- `python -m compileall -q common desensitize ocr organize training`：通过；
- `git diff --check`：通过（仅有换行格式提示）；
- 异常 3035 秒问题：已确认时间边界，尚未在新埋点下再次复现具体阻塞点。
