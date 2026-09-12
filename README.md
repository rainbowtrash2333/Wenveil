# Local OCR document desensitizer

这是一个离线、可逆的 Markdown 文档脱敏引擎。处理顺序固定为：

```text
OCR 文本 → 规整化 → 多路识别 → Span 冲突解析 → 一次性替换 → 加密映射
```

识别器不会修改文本；所有偏移量都指向规整后的文本。还原时校验映射中的哈希，发现密码错误、映射被篡改、文本被替换或 Token 缺失会直接失败。

## 安装与运行

```powershell
python -m pip install -e .
python -m desensitize xxx.md --password "change-me"
```

也可以通过环境变量提供密码：

```powershell
$env:DESENSE_PASSWORD = "change-me"
python -m desensitize xxx.md
```

默认输出到 `test-artifacts/desensitization-outputs/`：

```text
document-<safe-id>.normalized.md
document-<safe-id>.masked.md
document-<safe-id>.mapping.enc
document-<safe-id>.report.json
```

输出文件名使用不可逆的安全 ID，不沿用输入文件名。原始文件名仅作为加密映射中的可选元数据，只有显式使用 `--restore-filename` 才会恢复；普通还原仍输出安全文件名。授权原始 OCR 输入可放在 `test-artifacts/desensitization-inputs/`，该目录与输出目录均不入库。

还原和检查：

```powershell
python -m desensitize restore test-artifacts/desensitization-outputs/xxx.masked.md test-artifacts/desensitization-outputs/xxx.mapping.enc --password "change-me"
python -m desensitize inspect xxx.md
python -m desensitize benchmark xxx.md
python -m desensitize audit test-artifacts/desensitization-outputs/xxx.masked.md
```

`audit` 只输出行号、类别和安全摘要，不回显残留原文；空结果表示未发现当前审计规则覆盖的高风险字段或 Token/表格结构问题。

## Qwen 小模型

`model.enabled` 默认关闭。将本地 Qwen Token Classification checkpoint 放到配置的 `model.path` 后开启即可；模型只输出实体 Span，最终替换仍由规则引擎和加密映射完成。

机构简称/别名采用两阶段逻辑：第一阶段由规则、词典和可选 NER 模型找出全称、简称、子公司/分公司候选；第二阶段只对候选提及、局部上下文和注册表 Top-K 候选做实体链接。两阶段可以共享同一个 Qwen 主干和适配器，不需要再训练一个完整模型。当前占位符使用短语义格式：`⟦人员1⟧`、`⟦机构1⟧`、`⟦机构1-别名1⟧`、`⟦机构1-子公司1⟧`；真实全称、别名和关系只写入加密 mapping。若简称在同一文档中无法唯一链接，仍使用普通机构 Token 脱敏，不因歧义保留原简称，也不写入错误的主体关系。

名单、联系人、董事会成员和部门名册中已经确认的短姓名，在同一文档的重复无标签提及时会精确复用同一人员 Token；该传播只接受名单类高置信来源且要求原文至少出现两次，以降低职务词误判。

无模型权重时，可先使用 `training/README.md` 中的规则弱标注、BIO/BILOU 校验、OCR 增强、文档级切分和本地训练入口准备数据。该训练目录不会在导入时加载 PyTorch 或 Transformers。

## 配置

默认配置在 [`config/default.yaml`](config/default.yaml)。`detect` 控制是否识别，`anonymize` 控制是否替换，`protect_when_disabled` 用于避免金额等已识别实体被普通数字规则再次命中。

### 公共机构白名单

默认公共机构白名单位于 [`rules/organization_whitelist.txt`](rules/organization_whitelist.txt)，用于保留金融监管机关、宏观管理部门和公开市场基础设施的名称。配置入口如下：

```yaml
whitelist:
  organizations: ../rules/organization_whitelist.txt
```

白名单采用精确词典匹配，并以高优先级受保护 Span 进入统一冲突解析，不生成脱敏 Token。若白名单名称只是更长非白名单机构名称的一部分，则不会放行该子串，更长机构仍按普通 ORG 规则整体脱敏。新增或删除白名单名称只需修改词典文件，无需重新训练模型。

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
