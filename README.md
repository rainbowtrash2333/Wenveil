# Wenveil（文隐）— OCR、文本整理与脱敏

这是一个离线的中文文档处理工具，包含 OCR、OCR 文本整理、脱敏三个可独立调用的模块。
推荐处理顺序为：

Wenveil (文隐) is the public project name and `wenveil` is the Python distribution name. The repository
is designed for local, offline processing of authorized documents; it is not a place to publish source
documents, filenames, masked outputs, mappings, passwords, or client-specific rule dictionaries.

```text
OCR 文本 → 规整化 → 多路识别 → Span 冲突解析 → 一次性替换 → 可选加密映射
```

识别器不会修改文本；所有偏移量都指向规整后的文本。可恢复模式还原时校验映射中的哈希，发现密码错误、映射被篡改、文本被替换或 Token 缺失会直接失败；无密码模式不提供还原能力。

模块也可以单独调用：

```text
ocr-convert   文档/图片/邮件/归档 → Markdown
organize-text OCR Markdown/纯文本 → 整理后的 Markdown
desense       Markdown/纯文本 → 脱敏文件 + 可选加密映射
```

## 安装与运行

```powershell
python -m pip install -e .
python -m desensitize tests/fixtures/financial_desensitization_sample.md --password "change-me"
```

需要 OCR 转换能力时安装可选依赖：

```powershell
python -m pip install -e ".[ocr]"
```

### OCR 转换

默认读取 `config/ocr.yaml`，授权原始输入建议放在 `test-artifacts/ocr-inputs/`，输出放在
`test-artifacts/ocr-outputs/`；输出文件名使用安全 ID。

```powershell
python -m ocr --help
python -m ocr --config config/ocr.yaml --root-dir .\test-artifacts\ocr-inputs --no-progress
```

OCR 还支持 `.doc/.xls/.ppt` 旧版 Office、`.msg` 邮件以及 `.zip/.rar/.7z` 等常见归档：旧版 Office 在
Windows 上通过本机 Office COM 转成 `.docx/.xlsx/.pptx`，MSG 提取为 Markdown，归档在临时目录中
最多递归展开 3 层。RAR/7z 需要安装 7-Zip；Windows Office 转换需要 Microsoft Office 和 `pywin32`。

### OCR 文本整理

整理只做 Unicode、OCR 空格、结构字段、断行、分页标记和 Markdown 噪声整理，不识别实体，也不执行脱敏。

```powershell
python -m organize --help
python -m organize .\test-artifacts\ocr-outputs\document-<safe-id>.ocr.md
```

默认整理输出到 `test-artifacts/organized-outputs/`，可用 `-o` 指定文件或目录。

### 桌面端开发版

桌面端位于 [`desktop/`](desktop/)，使用 Tauri + React 调用 Python Sidecar；它覆盖文件选择、处理、可逆恢复、进度、结果和设置。

```powershell
npm --prefix desktop install
npm --prefix desktop run typecheck
npm --prefix desktop run build
cargo check --manifest-path desktop/src-tauri/Cargo.toml
```

需要浏览器验收时，先启动 `python desktop/bridge/http_dev_server.py`，再运行
`npm --prefix desktop run dev:http -- --host 127.0.0.1 --port 5173`。HTTP 适配器只用于本机开发；桌面发布使用目录分发（PyInstaller onedir sidecar 与模型目录组装），安装包签名和升级/回滚尚未进行。

也可以通过环境变量提供密码：

```powershell
$env:DESENSE_PASSWORD = "change-me"
python -m desensitize tests/fixtures/financial_desensitization_sample.md
```

密码是可选的。省略 `--password` 或 `DESENSE_PASSWORD` 时仍会执行脱敏，但只输出不可恢复的
`masked.md` 和安全报告，不生成 `mapping.enc`；需要恢复时必须在脱敏时设置密码。

```powershell
python -m desensitize mask tests/fixtures/financial_desensitization_sample.md
```

无密码模式默认输出到 `test-artifacts/desensitization-outputs/`：

```text
document-<safe-id>.masked.md
document-<safe-id>.report.json
```

设置密码的可恢复模式还会生成 `document-<safe-id>.normalized.md` 和
`document-<safe-id>.mapping.enc`；未设置密码时不会生成这两个文件。

输出文件名使用不可逆的安全 ID，不沿用输入文件名。可恢复模式下原始文件名仅作为加密映射中的可选元数据，只有显式使用
`--restore-filename` 才会恢复；普通还原仍输出安全文件名。授权原始 OCR 输入可放在
`test-artifacts/desensitization-inputs/`，该目录与输出目录均不入库。

还原和检查：

```powershell
python -m desensitize restore test-artifacts/desensitization-outputs/document-<safe-id>.masked.md test-artifacts/desensitization-outputs/document-<safe-id>.mapping.enc --password "change-me"
python -m desensitize inspect tests/fixtures/financial_desensitization_sample.md
python -m desensitize benchmark tests/fixtures/financial_desensitization_sample.md
python -m desensitize audit test-artifacts/desensitization-outputs/document-<safe-id>.masked.md
```

`audit` 只输出行号、类别和安全摘要，不回显残留原文；空结果表示未发现当前审计规则覆盖的高风险字段或 Token/表格结构问题。

## Qwen 小模型

`model.enabled` 默认关闭。推荐将本地 Qwen3.5 Token Classification checkpoint 导出为
ONNX 部署目录，并把 `model.backend` 设为 `onnx`、`model.path` 指向该目录后开启；Windows
运行时优先尝试 DirectML，不可用时回退 CPU，且不导入 PyTorch。模型只输出候选实体 Span，
最终替换仍由规则引擎、白名单、Resolver 和（可恢复模式下的）加密 mapping 完成。完整下载、训练、独立评估和
导出命令见 [`training/README.md`](training/README.md)。

机构简称/别名采用两阶段逻辑：第一阶段由规则、词典和可选 NER 模型找出全称、简称、子公司/分公司候选；第二阶段只对候选提及、局部上下文和注册表 Top-K 候选做实体链接。两阶段可以共享同一个 Qwen 主干和适配器，不需要再训练一个完整模型。当前占位符使用短语义格式：`⟦人员1⟧`、`⟦机构1⟧`、`⟦机构1-别名1⟧`、`⟦机构1-子公司1⟧`；可恢复模式将真实全称、别名和关系写入加密 mapping，无密码模式不保存这些值。若简称在同一文档中无法唯一链接，仍使用普通机构 Token 脱敏，不因歧义保留原简称，也不写入错误的主体关系。

名单、联系人、董事会成员和部门名册中已经确认的短姓名，在同一文档的重复无标签提及时会精确复用同一人员 Token；该传播只接受名单类高置信来源且要求原文至少出现两次，以降低职务词误判。

无模型权重时，可先使用 `training/README.md` 中的规则弱标注、BIO/BIOES/BILOU 校验、OCR 增强、文档级切分和本地训练入口准备数据。该训练目录不会在导入时加载 PyTorch 或 Transformers。

授权外部 NER 数据通过训练入口的 `--data-dir` 只读接入（默认相对目录 `data`），不复制到仓库或重新随机切分。适配器以外部记录的 `text` 为 canonical text；`clean_text`、实体/关系 ID、子类型和 relations 不进入 NER 标签。固定 `train/dev` 分别承担训练与 Trainer validation/best checkpoint，`test/hard_test` 仅由训练后的独立 exact-span 评估读取。旧的 `--input`、`--train`、`--validation`、`--prepare-dir` 合成流程继续兼容，详见 [`training/README.md`](training/README.md)。

## 配置

默认配置在 [`config/default.yaml`](config/default.yaml)。`detect` 控制是否识别，`anonymize` 控制是否替换，`protect_when_disabled` 用于避免金额等已识别实体被普通数字规则再次命中。

### 公共机构白名单

默认公共机构白名单位于 [`rules/organization_whitelist.txt`](rules/organization_whitelist.txt)，用于保留金融监管机关、宏观管理部门和公开市场基础设施的名称。配置入口如下：

```yaml
whitelist:
  organizations: ../rules/organization_whitelist.txt
```

白名单采用精确词典匹配，并以高优先级受保护 Span 进入统一冲突解析，不生成脱敏 Token。若白名单名称只是更长非白名单机构名称的一部分，则不会放行该子串，更长机构仍按普通 ORG 规则整体脱敏。新增或删除白名单名称只需修改词典文件，无需重新训练模型。

### 公开仓库数据边界

公开仓库只保留代码、通用规则、合成测试夹具和不含原文的文档。`rules/projects.txt` 是刻意留空的公开模板；
客户、交易、项目和文档名称必须放在仓库外的本地配置中，不能通过提交脱敏文件、masked 文件名或 mapping
来规避这一规则。发布前应同时检查当前工作树和 Git 可达历史，确保没有原始文档、原始文件名、OCR 输出、
脱敏输出、mapping、密码或本地路径。

固定词典使用内置 Aho–Corasick 实现；自定义规则支持 `literal`、`dictionary`、`regex` 和 `field`：

```yaml
custom:
  - name: contract_id
    type: CONTRACT_ID
    matcher: regex
    patterns: ['HT-[0-9]{4}-[0-9]+']
    anonymize: true
    priority: 95
  - name: project_name
    type: PROJECT
    matcher: field
    labels: [项目名称, 项目名]
    anonymize: true
    priority: 95
```

### 开启金额和数字脱敏

```yaml
entities:
  AMOUNT: {detect: true, anonymize: true, protect_when_disabled: true, priority: 80}
  NUMBER: {detect: true, anonymize: true, priority: 10}
```

运行回归测试：

```powershell
pytest
```

## License

Wenveil（文隐） is released under the [Apache License 2.0](LICENSE). Optional OCR and model
dependencies may carry their own license terms; review those terms before redistribution or commercial use.
