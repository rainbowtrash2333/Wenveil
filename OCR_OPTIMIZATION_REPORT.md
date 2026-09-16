# OCR 性能优化报告

> 版本：V0.1（2026-09-16）｜状态：生效
> 本报告只记录安全 ID、页数、计数、耗时和哈希，不包含用户文档原文。

## 1. 结论

当前 OCR 路径是 `pypdfium2/Docling + 自定义 RapidOCR + ONNX Runtime`。主要耗时有两类：

1. 纯扫描 PDF 不需要 Docling 的版面、表格和阅读顺序模型，但基线中仍完整执行了这些阶段。
2. 确实没有文本层的扫描页仍必须 OCR；在当前环境中 ONNX Runtime 实际使用 CPU，OCR 本身是主要瓶颈。

本轮已实现页级预检、纯扫描快速路径和 CPU 页级并发。基准总耗时从约 251.9 秒降至约 178.2 秒，约减少 29.3%，整体约 1.41 倍；10 页扫描子集约 2.49 倍。

## 2. 当前技术路径

```text
PDF
 └─ pypdfium2 页级预检
    ├─ 有足够文本层/复杂混合版式 → Docling
    │  └─ 页面解析 → 布局 → 表格 → 自定义 RapidOCR（只对需要的图像区域）
    └─ 无有效文本层且大面积扫描图 → 逐页快速 OCR
       └─ pypdfium2 渲染 → RapidOCR PP-OCRv4 mobile → Markdown
```

现有 RapidOCR 使用 ONNX Runtime，配置了中文识别、检测/识别阈值和可选 DirectML/CUDA。
本次运行环境报告的 provider 为 `AzureExecutionProvider`、`CPUExecutionProvider`，没有实际可用的
`DmlExecutionProvider` 或 `CUDAExecutionProvider`，所以本次结果是 CPU 基准。

## 3. 基线与优化后结果

测试集包含三个安全类别：10 页扫描子集、32 页扫描文档、6 页原生文本文档。10 页子集是从
170 页授权输入派生出的独立 PDF；本次 OCR 只处理这 10 页。

| 类别 | 页数 | OCR 页数 | 基线耗时 | 优化后耗时 | 加速比 | OCR 结果字符数（前→后） |
|---|---:|---:|---:|---:|---:|---:|
| 扫描子集 | 10 | 10 | 79.685 s | 31.996 s | 2.49x | 5,442 → 5,442 |
| 扫描文档 | 32 | 32 | 148.381 s | 123.244 s | 1.20x | 20,628 → 20,628 |
| 原生文本 | 6 | 1 | 23.824 s | 22.967 s | 1.04x | 496 → 496 |
| 合计 | 48 | 43 | 251.890 s | 178.207 s | 1.41x | 26,566 → 26,566 |

原生文本文档前后输出哈希一致；扫描类快速路径的 Markdown 结构哈希不同，这是因为它不再生成
Docling 的版面/表格结构，而是按页输出 OCR 文本。逐页 OCR 条数和字符数在本次样本中一致，
但扫描表格、双栏和复杂阅读顺序仍应使用人工抽样验收。

## 4. 已实施改动

- `ocr/pdf_preflight.py`：读取文本对象、图片对象和面积信号，不渲染页面、不保存原文。
- `ocr/ocr_engine.py`：新增 `needs_ocr()`，先判断有效文本层，再判断图片覆盖率；完整文本页跳过 OCR，小图/水印不会单独触发 OCR。
- `ocr/pdf_fast.py`：对确认的纯扫描 PDF 绕过 Docling 版面/表格阶段，按页渲染并调用 RapidOCR；纯白页在渲染后跳过。
- CPU 页级并发按实际 ONNX Runtime provider 生效：当前 CPU 默认 2 worker；真实 GPU provider 自动限制为 1，避免多个推理会话争抢显存。
- `ocr/rapid_ocr.py`：透传检测/识别阈值及 ONNX Runtime 线程配置。
- `config/ocr.yaml`：增加 `scan_fast_path`、`min_valid_text_chars`、`scan_bitmap_threshold`、`fast_scan_workers` 等配置。
- `tests/test_pdf_preflight.py`：覆盖文本噪声、纯扫描判定、有效文本优先和空白页判定。

快速路径默认条件为：没有页面达到 64 个有效字符、至少一页有大面积图片、且大面积图片页占比不低于 80%。
可将 `docling.scan_fast_path` 设为 `false` 回退到原 Docling PDF 路径。

## 5. 瓶颈分析

- 10 页扫描子集基线中，Docling `table_structure` 约 60.9 秒、`layout` 约 21.2 秒，OCR 约 37.9 秒；快速路径主要消除了前两项。
- 32 页纯扫描基线中，OCR 约 141.0 秒、`layout` 约 74.1 秒；优化后版面阶段消失，但 OCR 仍需逐页执行，因此收益低于 10 页子集。
- 原生文本文档只有 1 页含整页图像，OCR 页数保持为 1；其余 5 页直接复用文本层，说明页级跳过没有扩大 OCR 范围。
- 将 CPU worker 提高到 4 个实测变慢；将页面缩放降到 0.75 没有提速且 OCR 字符数下降，因此没有写入默认配置。

## 6. 下一步优先级

1. **页级 OCR 缓存（最高优先级）**：以 PDF 内容哈希、页号、OCR 配置指纹和模型版本作为键，缓存命中时完全跳过该页 OCR，文件变化时只重跑变化页。这是解决“同一个 PDF 多次 OCR”最直接的方式。由于缓存可能包含原文，应设计为显式启用、权限受控，必要时加密保存。
2. **启用真实硬件 provider**：当前配置虽请求 DirectML，但环境实际回退 CPU。安装与当前 ONNX Runtime/驱动匹配的 DirectML 或 CUDA 依赖后，先确认 `get_available_providers()`，再测相同基准；只改配置开关不能产生 GPU 加速。
3. **按文档方向建立质量档位**：对确认全为正向文字的文档，可试验关闭方向分类；对旋转文字、印章和复杂扫描件保留完整模式。当前小样本未证明关闭分类有稳定收益，所以暂不改默认值。
4. **复杂 PDF 的结构化优化**：对仍走 Docling 的混合/表格 PDF，再针对 layout、table mode 和页面并发做单独基准；不能把纯扫描快速路径的 Markdown 结果当作结构化表格结果。

## 7. 验证产物

- [benchmark_before.json](benchmark_before.json)
- [benchmark_after.json](benchmark_after.json)
- [前 10 页派生 PDF](test-artifacts/PDF_DEMO/scan_first10.pdf)

验证命令：`pytest -q`、`python -m compileall -q common desensitize ocr organize training`，均通过。

## 8. 全生命周期 profile

新增 `ocr/profiling.py` 和 CLI `--profile`，默认关闭。启用后，profile JSON 按文档、项目和页面记录
墙钟耗时与阶段累计耗时，覆盖输入指纹、预检、PDF 打开/关闭、页面渲染、空白检测、图像压缩、
OCR 排队、模型初始化、OCR 推理、Markdown 组装、合并和写盘。输出只保留安全 ID、计数、配置摘要
和 SHA-256，不含原文、文件名或 OCR 文本。

对 32 页扫描 PDF 的一次 profile（CPU，2 个页级 worker）显示：文档内部墙钟耗时约 187.0 秒，
其中 OCR 推理累计约 346.0 秒，页面渲染约 1.52 秒，预检约 72 ms，模型初始化约 721 ms，
Markdown 组装约 0.1 ms；这确认 OCR 推理是算法主瓶颈。项目层另报告约 3035 秒的文件转换等待，
与文档内部计时不一致，属于需要继续隔离的外层线程等待/运行时回收异常，不能直接归因于 OCR。

对 10 页扫描子集的复测（加入外层 worker/Future 拆分后）显示：总耗时约 38.9 秒，项目文件转换约
38.8 秒，OCR 推理累计约 74.2 秒，页面渲染约 534 ms，预检约 84 ms，模型初始化约 726 ms，
合并约 0.3 ms，写盘约 1.6 ms；worker 执行时间与 Future 等待一致，未发现额外外层等待。

profile 命令示例：

```powershell
python -m ocr --config config/ocr.yaml --root-dir .\test-artifacts\ocr-inputs `
  --profile --profile-output .\test-artifacts\logs\ocr-profile.json --no-progress
```

外层线程生命周期异常的精准埋点、对照实验和当前根因判定见
[OCR_BUG_AUDIT_REPORT.md](OCR_BUG_AUDIT_REPORT.md)。
