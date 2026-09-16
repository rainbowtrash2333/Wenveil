---
name: project-to-md
description: 将一个输入根目录下各一级项目的受支持文档递归转换为 Markdown，并为每个项目生成一个 merged 文件；适用于批量 OCR、文本整理和项目级合并。
---

# 项目文档合并

当用户提供一个包含多个项目子目录的根目录，并要求“所有文件转成一个 md”“批量合并项目文档”或“每个项目生成 merged 文档”时，使用本 skill。

## 运行方式

在 Wenveil 项目根目录执行：

```powershell
python skills/project-to-md/scripts/project_to_md.py <输入根目录>
```

例如：

```powershell
python skills/project-to-md/scripts/project_to_md.py .\test-artifacts\test_docs
```

脚本会把输入根目录的一级子目录视为项目，递归处理每个项目中的受支持文件，并生成：

```text
<输入根目录>/merged/document-<safe-id>.merged.md
```

每个项目对应一个 Markdown 文件。输出文件名使用不可逆安全 ID，不回显原始项目名或文件名。

## 工作流和状态

- 每个项目通过 `workflow.WorkflowService` 执行 OCR、文本整理和合并；OCR 转换前会自动调用
  `ocr/file_converter.py` 处理旧版 Office、MSG 和归档。
- 脱敏与审计在此 skill 中关闭；需要脱敏时，应使用统一工作流或 `ocr-desensitization` skill。
- SQLite 状态库默认位于 `<输入根目录>/.wenveil/workflow.sqlite3`。
- 日志和私有 checkpoint 位于 `<输入根目录>/.wenveil/`，不写入 Markdown 内容。
- `--resume` 可按已存在的安全合并文件跳过已完成项目；不使用该参数时会重新处理并覆盖同名安全输出。

## 输入边界

当前支持 PDF、DOCX、PPTX、XLSX、DOC、XLS、PPT、MSG、常见图片、TXT、Markdown、RTF、HTML、XML、JSON、CSV，
以及 ZIP、RAR、7z、TAR、GZ、BZ2、XZ、CAB、ISO 等归档。旧版 Office 会在 Windows 上通过 Office COM
（或配置的 LibreOffice）转换为新格式；MSG 会提取正文和支持格式附件；归档会在隔离临时目录中按安全预算展开，
并递归转换其中的支持文件。隐藏目录、Office 临时文件和 `merged` 输出目录不会作为输入。
发现不支持的文件时，默认将该项目标记为未完成，避免错误宣称“全部处理完成”；确认可以忽略时可使用
`--allow-unsupported`，但脚本会以非零退出码提示仍有未处理文件。

旧版 Office 转换需要 Windows 本机 Office 与 `pywin32`（或显式配置 LibreOffice）；MSG 优先需要 `extract-msg`，
RAR/7z 等归档需要 PATH 中的 `7z`、`7zz` 或 `7za`。缺少这些可选依赖时，对应文件会生成固定的安全失败占位，
并在日志中记录不含原文的错误摘要；批处理仍会继续，作业状态和产物路径保留在 SQLite 中供检查。

所有处理均为本地离线操作。终端输出只包含项目安全 ID、计数、状态、作业 ID 和输出路径，不打印原文、OCR 片段或密码。
