# OCR 文档转换模块

> 版本：V0.3（2026-09-17）｜状态：生效

`ocr/` 是独立的文档转换模块，移植自项目外部的 `document-converter` 工具。它在 OCR 主链路前增加
文件前置转换：旧版 Office、MSG 邮件和归档先转换/展开，再交给 Docling、RapidOCR 或文本读取器，
最终将项目文档合并为 Markdown。

## 安装

```powershell
python -m pip install -e ".[ocr]"
```

Windows 旧版 Office 转换还需要本机安装 Microsoft Word、Excel、PowerPoint，并由 `pywin32` 调用；
MSG 提取默认使用 `extract-msg`，解析失败时尝试 Outlook。RAR/7z 解压需要在 PATH 中安装
7-Zip（`7z`、`7zz` 或 `7za`），也可在 `config/ocr.yaml` 的 `file_converter.archive_tool` 中指定路径。
DirectML 和 CUDA 后端分别安装 `.[ocr-directml]`、`.[ocr-cuda]`。OCR 依赖是可选的，不会影响
`organize` 或 `desensitize` 的基础调用。

## 使用

```powershell
python -m ocr --help
python -m ocr --config config/ocr.yaml --root-dir .\test-artifacts\ocr-inputs --no-progress
```

默认配置：

- 输入：`test-artifacts/ocr-inputs/`，一级子目录作为项目；
- 输出：`test-artifacts/ocr-outputs/`；
- 日志：`test-artifacts/logs/ocr.log`；
- 输出文件：`document-<safe-id>.ocr.md`，不会使用项目名或输入文件名。

`--no-ocr` 可验证纯文本/电子文档转换链路；`--resume` 可在长任务中跳过已有安全输出。

需要定位单个批处理任务的耗时时，可启用安全性能 profile：

```powershell
python -m ocr --config config/ocr.yaml --root-dir .\test-artifacts\ocr-inputs `
  --profile --profile-output .\test-artifacts\logs\ocr-profile.json --no-progress
```

profile 会记录发现、文件转换、PDF 预检、页面渲染、图像压缩、OCR 排队/推理、模型初始化、
Markdown 组装、项目合并和写盘的耗时，并可选记录单页耗时。默认关闭；输出只包含安全 ID、
计数、配置摘要和 SHA-256，不包含原文、文件名或 OCR 文本。启用时还会记录 worker 返回、Future
完成和 executor shutdown 的安全时间线，用于定位外层线程生命周期长尾。

## PDF 性能路径

PDF 会先执行页级预检：有足够有效文本层的页面不重复 OCR；没有有效文本层且以大面积扫描图为主的 PDF，
默认走逐页 RapidOCR 快速路径，跳过 Docling 的版面/表格阶段。需要保留扫描表格结构化结果时，可在
`config/ocr.yaml` 中设置 `docling.scan_fast_path: false`。

CPU 默认使用 2 个页级 OCR worker；实际检测到 DirectML/CUDA provider 时自动限制为单 worker，以避免多个
推理会话争抢显存。性能基线和后续优化见 [OCR_OPTIMIZATION_REPORT.md](../OCR_OPTIMIZATION_REPORT.md)。

## 支持格式

### 直接转换

PDF、DOCX、PPTX、XLSX、JPG、JPEG、PNG、BMP、TIF、TIFF、GIF、TXT、MD、MARKDOWN、RTF、
HTML、XML、JSON 和 CSV。

### 前置转换

- `.doc` → `.docx`，`.xls` → `.xlsx`，`.ppt` → `.pptx`：使用隐藏、只读的 Office COM；
  不执行宏、不更新外部链接，转换文件只存在于任务临时目录。
- `.msg` → Markdown：提取主题、收发件人、日期和正文；其中的支持格式附件也会继续转换。
- `.zip`、`.rar`、`.7z` 及常见 `.tar/.gz/.bz2/.xz/.cab/.iso`：在隔离临时目录展开，外层归档算第 1 层，最多递归展开 3 层；
  压缩包中的不支持格式会跳过，达到深度上限的嵌套压缩包不会继续展开。

归档展开默认限制成员总数为 10,000、累计未压缩体积为 2 GiB，并拒绝绝对路径、`..` 路径和链接成员。
可在 `config/ocr.yaml` 的 `file_converter` 节调整安全预算；最大递归深度不能超过 3。

OCR 模块只负责转换，不做文本实体识别、白名单判断或脱敏。转换后可将安全命名的 Markdown
交给 `python -m organize`，再交给 `python -m desensitize`。
