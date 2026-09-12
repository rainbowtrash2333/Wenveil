---
name: ocr-desensitization
description: 对 OCR 后的 Markdown 或纯文本执行可逆、可审计的本地脱敏。用户提到脱敏、漏脱敏、误脱敏、残留审计、查看脱敏效果、恢复原文或训练脱敏 NER 模型时使用。
---

# OCR 文档脱敏

本 skill 适用于 Wenveil（文隐）仓库的任意本地检出目录。目标不是只替换少量手机号，而是对 OCR 文本完成“识别—脱敏—审计—阅读—恢复—验证”的闭环，并且不把原始敏感值写入日志、回复或版本控制。

公开仓库边界：原始文档、原始文件名、客户/项目专属规则、OCR 输出、脱敏输出、mapping、密码和本地绝对路径
都不得进入 Git、issue、PR、日志或回复。`rules/projects.txt` 是公共空模板；项目专属词典只能通过仓库外的
本地配置加载。发布前必须同时审计当前工作树和全部可达 Git 历史，不能只删除最新文件。

## 触发条件

当用户要求以下任一事项时调用本 skill：

- 对 OCR、扫描件转写或 Markdown 文档进行脱敏；
- 检查脱敏结果是否仍有大量漏脱敏或误脱敏；
- 查看、解释或对比脱敏效果；
- 恢复脱敏文档并验证可逆性；
- 调整识别规则、配置或本地 NER 模型；
- 为脱敏流程准备弱标注、训练数据、评估或蒸馏方案。

## 必须遵守的工作流

1. 先确认输入文件、输出目录、密码来源和用户要求的脱敏边界。输入可能含真实敏感信息，命令输出只报告统计量，不打印原文片段。
2. 阅读 `docs/ocr_desensitization_implementation_plan.md` 和 `config/default.yaml`；需要修改识别行为时，同时检查 `desensitize/config/default.yaml`、`desensitize/recognizers/` 与 `desensitize/resolver.py`。
3. 使用项目 CLI 执行脱敏，不直接对文件做无审计的正则替换：

   ```powershell
   $env:DESENSE_PASSWORD = '<strong-local-password>'
   python -m desensitize mask '<input.md>' -o '.\test-artifacts\desensitization-outputs' --password $env:DESENSE_PASSWORD
   ```

   授权原始 OCR 输入统一放在 `test-artifacts/desensitization-inputs/`，生成的脱敏、恢复、审计和
   mapping 产物统一放在 `test-artifacts/desensitization-outputs/`；两个目录均不入库。

   项目还提供两个可独立调用的前置模块：`python -m ocr` 负责文档/图片转 Markdown，
   `python -m organize` 负责 OCR 文本整理；需要时按 `ocr → organize → desensitize` 的文件契约串联，
   不要在脱敏模块中直接加载 OCR 重依赖。OCR 输入/输出分别使用 `test-artifacts/ocr-inputs/` 和
   `test-artifacts/ocr-outputs/`，整理输出使用 `test-artifacts/organized-outputs/`。

4. 脱敏后必须阅读生成的 `*.masked.md`，重点抽查标题、段落、表格、列表、页眉页脚、OCR 断行和实体相邻文本。阅读时不得把未脱敏原文复制到对话中。
5. 对掩码文件执行审计：

   ```powershell
   python -m desensitize audit '.\test-artifacts\desensitization-outputs\<name>.masked.md'
   ```

   审计结果必须无残留 PII、无 Markdown 结构损坏、无占位符冲突；如果有问题，先定位识别器/配置/解析边界，再修复并重新运行全流程。
6. 对需要交付的结果执行恢复验证：

   ```powershell
   python -m desensitize restore '.\test-artifacts\desensitization-outputs\<name>.masked.md' '.\test-artifacts\desensitization-outputs\<name>.mapping.enc' -o '.\test-artifacts\desensitization-outputs\<name>.restored.md' --password $env:DESENSE_PASSWORD
   ```

   将恢复文件与规范化原文比较；应报告哈希或字节比较结果，不输出原文。只有恢复一致时，才宣称“可逆”。
7. 修改代码后运行相关测试；完整交付前运行 `pytest -q`。若结果供用户阅读，明确给出掩码文件、审计报告和（如用户有权限）恢复验证结果的路径。

8. 文件名也属于脱敏边界：交付目录必须使用 `document-<safe-id>.*` 这类安全文件名，不得把输入文件名复制到 masked、mapping 或 report 文件名中。原文件名只能作为加密 mapping 元数据保存；只有用户明确要求且具备 mapping 权限时，才使用 `--restore-filename` 恢复文件名。

## 识别与策略原则

- 规则识别器负责高精度结构化信息：手机号、座机、邮箱、身份证/证件号、银行卡/账号、统一社会信用代码、合同编号、项目编号等。
- 词典、别名和上下文识别器负责姓名、机构、部门、地址、项目和合同实体；要检查 OCR 断词、空格、标点和表格单元格边界。
- 对名单、联系人、董事会成员和部门名册中已确认的短姓名，同文档后续重复出现时可做精确表面传播；仅使用名单类高置信来源且要求至少重复两次，避免把职务词传播成姓名。
- 本地 NER 模型只负责提出候选 span；最终替换必须经过 `desensitize/resolver.py` 的统一冲突消解和策略决策。没有真实本地 checkpoint 时，不得假装模型已训练完成，也不要开启模型路径配置。
- 金额、日期、普通序号和普通数字默认是“检测/保护”，不是一律匿名化。若用户要求处理它们，先调整配置并在报告中说明范围，避免破坏文档语义。
- 统一使用稳定、可读、无碰撞的占位符；同一实体需要保持一致映射，映射文件必须加密保存，不能提交到公开版本库。
- 机构全称、简称和子公司/分公司要保留关系语义但不泄露原值：使用 `⟦机构1⟧`、`⟦机构1-别名1⟧`、`⟦机构1-子公司1⟧` 这类短占位符；真实值和 `ALIAS_OF`/`SUBSIDIARY_OF`/`BRANCH_OF` 关系只放入加密 mapping。
- 机构简称若能唯一链接到主体，必须标记为 `⟦机构N-别名N⟧`；若同一文档存在多个可能主体，仍必须用普通 `⟦机构N⟧` 脱敏，但不得伪造错误的主体关系，且不能因链接歧义而保留原简称。
- 公共监管机构白名单由 `rules/organization_whitelist.txt` 配置，命中后作为高优先级受保护 ORG Span 保留原文；若白名单名称只是更长非白名单机构的子串，必须让更长机构继续脱敏，禁止按子串部分放行。
- 机构关系采用两阶段逻辑而非完整文档重复推理：第一阶段找候选 Span，第二阶段只对候选、局部上下文和注册表 Top-K 做链接。优先规则/注册表确定，高歧义样本才交给同一 Qwen 主干的轻量 linking 头或适配器。
- 脱敏是防止泄露，不是删除证据。保留必要的格式、段落、表格和文档语义，便于用户继续阅读。

## 代码和训练入口

- CLI：`desensitize/cli.py`；命令包括 `mask`、`restore`、`inspect`、`benchmark`、`audit`。
- 流程：`desensitize/normalizer/pipeline.py`、`desensitize/pipeline.py`、`desensitize/resolver.py`。
- 识别器：`desensitize/recognizers/`；规则词典位于 `desensitize/rules/` 和项目 `rules/`。
- 模型接口：`desensitize/recognizers/model_ner.py`。它应保持可选、离线、可禁用。
- 训练脚手架：先阅读 `training/README.md`，再使用 `training/teacher_label.py`、`build_dataset.py`、`validate_labels.py`、`evaluate.py` 和 `train_token_classifier.py`。训练数据只允许使用已脱敏或经授权的文本。

## 失败处理

- 审计发现漏脱敏：不要只扩大一个正则。先保留最小复现样本，判断是 OCR 归一化、识别器、候选合并、优先级还是替换器的问题，然后补测试。
- 审计发现误脱敏：检查上下文阈值、词典命中和实体类型策略；对金额、日期、普通数字尤其谨慎。
- 表格损坏：先检查 Markdown 管道符、转义、单元格内换行和 span 是否跨结构边界；优先采用结构安全的替换策略。
- 模型不可用、依赖未安装或没有 checkpoint：继续使用规则/词典基线，明确报告“模型未启用”，不得伪造推理或训练指标。
- 密码缺失：暂停生成可恢复映射，提示设置 `DESENSE_PASSWORD`；不要把密码写入源码、skill、日志或最终回复。
- 文件名残留：不要依赖人工改名；重新运行 CLI 生成安全文件名，并检查交付目录中不存在输入文件名片段。

## 完成标准

只有同时满足以下条件，才算完成脱敏任务：

- `audit` 无残留敏感信息和格式问题；
- 针对手机号、座机、邮箱、证件号、账号、信用代码等的残留扫描为零，或用户明确接受例外；
- 关键 Markdown 结构保持可读；
- 恢复验证通过，规范化原文与恢复结果一致；
- 测试通过，且最终回复只包含路径、统计、风险和未覆盖范围，不包含敏感原文。
- 交付目录中的文件名已脱敏，且 masked 文本中的主体关系占位符对下游大模型可读但不含真实主体名。
- 配置的公共机构白名单保持原文，且白名单子串不会使更长的非白名单机构逃逸脱敏。
- 已确认的人员、机构简称和歧义别名不能以原文残留在 masked 文本中；关系不确定时宁可降低关系语义，也不能降低脱敏覆盖率。
