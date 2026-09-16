# ADR-0006：OCR 前置文件转换与受控归档展开

> 版本：V0.1（2026-09-17）｜状态：已接受
> 日期：2026-09-17
> 关联：[ARCHITECTURE.md](../ARCHITECTURE.md)、[MODULES.md](../MODULES.md)、[DEV-TOOLCHAIN.md](../DEV-TOOLCHAIN.md)、[OCR README](../../ocr/README.md)

## 背景

Wenveil 当前的 OCR 入口直接接收 Docling、RapidOCR 和文本读取器能够处理的文件。实际金融、保险和投资资料中仍常见旧版 Office 文档、Outlook 邮件和压缩包：

- `.doc/.xls/.ppt` 需要先转换为新 Office Open XML 格式；
- `.msg` 需要提取邮件正文，并尽可能处理其中的支持格式附件；
- `.zip/.rar/.7z` 及常见 7-Zip 可读归档需要展开后再交给现有 OCR 路径，且不能因为嵌套归档或恶意成员路径越出临时目录。

这些能力属于 OCR 输入适配，不应放入脱敏、整理或桌面端业务层。归档展开还需要递归深度、文件数和解压后体积限制，避免归档炸弹和路径穿越。

## 决策

在 `ocr/file_converter.py` 增加独立的前置转换层，并在 `DocumentConverter` 调度到现有格式处理器之前调用它：

1. `.doc/.xls/.ppt` 在 Windows 上优先使用本机 Microsoft Office COM，分别输出临时 `.docx/.xlsx/.pptx`，然后继续走现有 Docling 路径。COM 在每个线程内初始化，Office 应用隐藏运行、只读打开、不更新链接、不执行宏。
2. `.msg` 优先使用可选的 `extract-msg` 提取正文和附件；不可用或解析失败时，在 Windows 上回退到 Outlook COM。邮件正文输出为临时 Markdown，支持格式附件继续递归进入同一转换层。
3. `.zip` 使用 Python 标准库安全展开；`.rar/.7z` 及常见归档使用配置的 7-Zip 命令行工具展开。归档展开在任务临时目录中进行，外层归档算第 1 层，最多展开 3 层；更深的归档只产生安全提示，不再展开。
4. 归档成员必须通过绝对路径、驱动器路径、`..`、符号链接和输出目录边界检查；同时限制归档成员数和累计未压缩字节数。日志和错误只记录安全 ID、类型和固定摘要。
5. OCR、统一 Workflow 和默认配置同步扩展输入白名单；Organize 与 Desensitize 仍只接收 Markdown/纯文本，不直接解析 Office、邮件或归档文件。

## 理由

- 复用现有 Docling/RapidOCR/Markdown 链路，避免为每种外部格式复制 OCR 和安全处理逻辑。
- Office COM 能较好保持旧版 Word、Excel、PowerPoint 的版面和表格信息；输出临时文件不会污染原始目录。
- 7-Zip 同时覆盖 ZIP、RAR 和 7z，Windows 部署路径明确；ZIP 仍使用标准库以便进行逐成员路径和体积校验。
- 前置层只产生文本或新格式文件，不改变脱敏模型“只能提出候选、不能改写原文”的边界。

## 影响

- OCR 可选依赖增加 `extract-msg`；Windows Office 转换增加 `pywin32` 和本机 Microsoft Office 要求。
- RAR/7z 输入要求系统可执行 `7z`、`7zz` 或 `7za`，也可在 `config/ocr.yaml` 中指定路径；没有工具时仅该文件安全失败，不影响同批其他文件。
- 压缩包中的不支持格式会被跳过；支持格式会合并为一个 Markdown 结果。附件和归档不会恢复为用户原始目录结构。
- 新增单元测试覆盖递归深度、路径穿越、预算限制、Office 调度和 MSG 文本提取；实际 Office/7-Zip 版本矩阵仍需在目标机器上验收。
- 回滚方式是移除前置调度并恢复旧输入白名单；现有 PDF、DOCX、PPTX、XLSX、图片和文本路径不受影响。

## 备选方案

1. **只依赖 LibreOffice**：在当前 Windows 交付环境中不能保证安装，且 `.msg` 仍需单独处理，不采用为默认方案。
2. **把归档展开放入桌面端**：会造成 CLI、Workflow 和桌面端行为不一致，并扩大 UI 的文件系统权限边界，不采用。
3. **无深度限制地递归解压**：存在归档炸弹和嵌套输入失控风险，不采用。
