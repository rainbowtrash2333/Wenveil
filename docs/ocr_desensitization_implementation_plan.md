# Wenveil 脱敏设计规格

> 版本：V0.5（2026-09-16）｜状态：生效
> 本文原名“OCR 审计文档可逆脱敏系统实施方案”；方案已落地，因此只保留尚未被其他文档
> 覆盖的设计规格、测试要求与验收标准。现状、命令与阶段进度以 [index.md](./index.md)
> 指向的文档为准。
> 已移除章节的替代文档：技术路线与数据流 → [ARCHITECTURE.md](./ARCHITECTURE.md)；
> 模块与依赖 → [MODULES.md](./MODULES.md)；模型选型、训练与 ONNX 部署 →
> [adr/0004-qwen35-token-classification-onnx.md](./adr/0004-qwen35-token-classification-onnx.md)、
> [../training/README.md](../training/README.md)；Token、Mapping 与 Restore →
> [adr/0001-hybrid-reversible-desensitization.md](./adr/0001-hybrid-reversible-desensitization.md)；
> 配置、输出文件与 CLI → `config/default.yaml`、[DEV-TOOLCHAIN.md](./DEV-TOOLCHAIN.md)；
> 阶段划分 → [ROADMAP.md](./ROADMAP.md)。

---

# 1. 文本规整规格

## 1.1 Unicode 规整

处理：

- 全角数字转半角
- 全角英文字母转半角
- NBSP 转普通空格
- `\r\n` / `\r` 统一为 `\n`
- 异常 Unicode 空白字符清理
- 可选 NFKC

要求配置化：

```yaml
normalization:
  unicode_nfkc: true
```

---

## 1.2 中文异常空格修复

例如：

```text
中国 人 民 财 产 保 险 股 份 有 限 公 司
```

恢复：

```text
中国人民财产保险股份有限公司
```

但不能全局删除所有空格。

需要保护：

- 英文
- 表格
- 编号
- 日期
- Markdown 结构
- 代码块

---

## 1.3 OCR 断行恢复

例如：

```text
中国人保资产管理股份
有限公司于2025年……
```

恢复：

```text
中国人保资产管理股份有限公司于2025年……
```

只有在满足以下规则时才合并：

- 上一行不是完整句号结尾
- 下一行不是标题
- 下一行不是列表
- 下一行不是表格
- 下一行不是明显字段起始
- 当前区域属于正文

---

## 1.4 结构化字段修复

例如身份证：

```text
110101 1990 0101 123X
```

修复：

```text
11010119900101123X
```

金额：

```text
1, 234, 567. 89 元
```

修复：

```text
1,234,567.89元
```

手机号：

```text
138 0013 8000
```

修复：

```text
13800138000
```

必须采用：

```text
候选发现
→ 局部修复
→ 格式校验
→ 确认
```

不能简单删除数字之间所有空格。

---

## 1.5 幂等要求

必须保证：

```python
normalize(normalize(text)) == normalize(text)
```

这是自动测试硬要求。

---

# 2. 身份证识别

身份证使用确定性算法。

流程：

```text
正则
↓
长度检查
↓
出生日期检查
↓
行政区号基本校验
↓
18位身份证校验位算法
↓
生成 Span 候选
```

优先级：

```text
ID_CARD = 100
```

不能只靠：

```regex
\d{17}[\dXx]
```

否则普通 18 位编号也可能误识别。

---

# 3. 金额识别

金额由规则完成。

支持：

```text
100万元
1,234.56元
人民币500万元
USD 100,000
100亿美元
3000万港币
```

金额识别和金额脱敏必须分开。

例如默认配置：

```yaml
AMOUNT:
  detect: true
  anonymize: false
  protect_when_disabled: true
```

这样即使普通数字开启脱敏：

```text
30000万元
```

也不会被 NUMBER 识别器单独脱敏成：

```text
⟦数字1⟧万元
```

---

# 4. 普通数字识别

普通数字优先级最低。

推荐：

```text
NUMBER = 10
```

数字识别必须在 Span Resolver 中让位于：

- 身份证
- 金额
- 日期
- 银行账号
- 合同编号
- 其他结构化字段

---

# 5. 公司和机构词典

已知公司、机构、自定义项目等固定词统一使用内置的纯 Python Aho-Corasick 匹配
（`desensitize/recognizers/dictionary.py`）：

- 一次扫描匹配大量关键词
- 适合数万固定实体
- 避免逐词 `text.find()`

使用：

```text
公司词典
机构词典
部门词典
项目词典
自定义词典
```

统一构建 Automaton。

---

# 6. 公司简称自动学习

审计文档常见：

```text
中国人保资产管理有限公司（以下简称“人保资产”）
```

程序应自动学习：

```text
ORG_FULL:
中国人保资产管理有限公司

ORG_ALIAS:
人保资产
```

支持识别模式：

```text
以下简称
简称
下称
以下称
简称为
```

简称加入当前文档的动态词典。

后续：

```text
人保资产于2025年……
```

自动识别为 ORG。

---

# 7. 自定义字段

必须支持用户自由扩展，不修改核心代码。

至少支持四种方式。

---

## 7.1 Literal

```yaml
custom:
  - name: 华电项目
    type: PROJECT
    matcher: literal
    values:
      - 华电资本增资扩股引战项目
```

---

## 7.2 Dictionary

```yaml
custom:
  - name: 内部公司
    type: ORG
    matcher: dictionary
    file: ../rules/organizations.txt
```

词典路径相对配置文件所在目录解析；项目专属词典必须放在仓库外，通过本地配置加载，
不能写进公共模板（见 [PUBLIC-RELEASE-CHECKLIST.md](./PUBLIC-RELEASE-CHECKLIST.md)）。

---

## 7.3 Regex

```yaml
custom:
  - name: 合同编号
    type: CONTRACT_ID
    matcher: regex
    patterns:
      - 'HT-[0-9]{4}-[0-9]+'
```

实现直接用 Python `re` 编译规则中的 `patterns`；自定义正则必须避免灾难性回溯。

---

## 7.4 Field

例如：

```yaml
custom:
  - name: 项目名称
    type: PROJECT
    matcher: field
    labels:
      - 项目名称
      - 项目名
      - 投资项目
```

识别：

```text
项目名称：华电资本增资扩股引战项目
```

直接取字段值。

这个机制非常适合审计资料。

---

# 8. Span Resolver 与优先级

所有规则、词典、模型都统一产生：

```text
Candidate Span[]
```

例如：

```text
规则：
[100:118] ID_CARD 1.00

模型：
[20:22] PERSON 0.98

词典：
[30:45] ORG 1.00
```

统一进入 Resolver。

---

## 8.1 默认优先级

```text
ID_CARD           100
ADDRESS           100
PHONE              97
EMAIL              96
BANK_ACCOUNT       96
CONTRACT_ID        95
DATE               88
ORG                85
PROJECT            84
AMOUNT             80
DEPARTMENT         78
PERSON             70
NUMBER             10
公共机构白名单 Span 1000
```

以上为 `config/default.yaml` 的默认优先级，可按实体覆盖；自定义规则使用规则自身的
`priority`。识别器内部还会按来源小幅加减（词典命中、别名、分行支行等），最终以候选
`priority` 和 `score` 参与冲突消解。保护策略由 `Span.anonymize`/`protect` 在替换阶段
决定，Resolver 本身不区分“受保护”类型。

---

## 8.2 冲突规则

依次：

```text
1. priority 高者优先
2. priority 相同 → score 高者
3. score 相同 → 长实体优先
4. 最后按 start 排序
```

---

## 8.3 示例

```text
张三科技有限公司
```

可能得到：

```text
PERSON  [0:2]  0.96
ORG     [0:8]  0.99
```

ORG 胜出。

---

金额：

```text
100万元
```

得到：

```text
AMOUNT [0:5]
NUMBER [0:3]
```

AMOUNT 胜出。

---

# 9. 测试要求

## 9.1 Normalizer

必须：

```python
assert normalize(normalize(x)) == normalize(x)
```

---

## 9.2 可恢复模式可逆性

必须：

```python
normalized = normalize(original)

masked, mapping = anonymize(normalized)

restored = restore(masked, mapping)

assert restored == normalized
```

---

## 9.3 冲突测试

测试：

```text
身份证中的数字不能被 NUMBER 抢占
金额中的数字不能被 NUMBER 抢占
公司名中的人名不能单独脱敏
项目名内部的公司简称不能再次脱敏
```

---

## 9.4 一致性

```text
相同实体重复出现 → 相同 Token
不同实体 → 不同 Token
```

---

## 9.5 安全性

测试：

- mapping 被修改
- masked 文件被修改
- 密码错误
- mapping 与 masked 不对应
- token 缺失
- 重复 token
- 原文中存在类似 token 字符串

必须明确报错。

---

## 9.6 无密码不可逆模式

未设置密码时，`mask` 仍必须完成规整、识别、替换和审计，但不得生成未加密 mapping；输出只包含
脱敏文本和安全报告，不保留自动生成的规范化原文。报告和 CLI/Sidecar 结果必须明确标记为不可恢复，
恢复流程只能对带加密 mapping 的可恢复结果执行。

---

# 10. 性能基线目标（规划中）

尚未建立：当前只有 `training/benchmark.py` 的合成 NER 子集基准和 `desense benchmark`
的单文件计时，见 [ROADMAP.md](./ROADMAP.md) 阶段 4。以下为待建立的基线矩阵。

至少测试：

```text
1 MB
10 MB
50 MB
100 MB
```

分别记录：

```text
Normalizer 耗时
规则识别耗时
词典识别耗时
模型耗时
Resolver 耗时
替换耗时
恢复耗时
Peak RAM
```

只有确定某个模块存在明显瓶颈后，再考虑 C/C++。

---

# 11. 范围反目标

（原第一版限制；桌面端 UI 已在后续阶段实现，见 [APP-VERSION.md](./APP-VERSION.md)。）

暂时不要：

```text
直接让 LLM 输出完整脱敏文档
自己写 C 扩展
加入大量复杂 NLP 框架
支持几十种实体
训练超大模型
过早优化
```

先把：

```text
准确
可逆
可配置
高性能
```

做好。

---

# 12. 验收标准

以下标准已在当前版本达成，验证记录见 [APP-VERSION.md](./APP-VERSION.md)；正式上线前仍需用授权真实语料复核残留率和误脱敏率：

## 12.1 功能

- OCR 文本可规整。
- 人名可脱敏。
- 身份证可脱敏。
- 公司名可脱敏。
- 项目名可脱敏。
- 金额可配置是否脱敏。
- 普通数字可配置是否脱敏。
- 支持自定义字段。
- 支持自定义词典。
- 支持自定义正则。
- 支持恢复。
- 未设置密码仍可生成不可恢复的脱敏文本，不生成 mapping。

## 12.2 正确性

可恢复模式必须：

```text
restore(mask(normalize(x)))
==
normalize(x)
```

## 12.3 安全

- 可恢复模式的 mapping 使用 AES-GCM 加密；无密码模式不生成 mapping。
- 日志无敏感原文。
- mapping 与 masked 文件绑定。
- 篡改可以检测。

## 12.4 性能

- 不对大量关键词逐个全文扫描。
- 固定词使用 Aho-Corasick。
- 正则避免灾难性回溯。
- 最终文本只进行一次确定性重建。
- 小模型批量推理。

---

# 13. 机构关系、安全文件名与训练验收

## 13.1 两阶段不是完整文档推理两次

机构关系采用“候选识别 → 局部实体链接”的两阶段逻辑：

1. 第一阶段由机构词典、`以下简称/简称` 声明、后缀规则和可选 Qwen NER 找出全称、简称、子公司/分公司等候选 Span。
2. 第二阶段只接收候选提及、有限局部上下文和注册表 Top-K 候选，判断 `ALIAS_OF`、`SUBSIDIARY_OF` 或 `BRANCH_OF`。明确声明和高置信注册表命中由规则直接确定，只有歧义候选才交给链接模型；在链接仍不确定时，必须回退为无关系的普通机构 Token，不能因为无法链接而留下原简称。

因此默认不需要两个完整模型。可以继续使用同一个 Qwen 主干：NER 头负责边界识别，轻量 linking head/adapter 负责候选分类；关系注册表独立保存，避免把易变的机构关系硬编码进权重。歧义候选的 linking head/adapter
尚未实现，仍由规则、注册表和高置信声明确定关系（见 [ROADMAP.md](./ROADMAP.md) 阶段 3）。

## 13.2 AI 可读但不泄露的占位符

为了让下游大模型理解同一主体及其关系，占位符保留安全的伪主体 ID 和关系类别：

```text
⟦机构1⟧                  # 主体全称
⟦机构1-别名1⟧             # 机构1 的别名1
⟦机构1-子公司1⟧           # 机构1 的子公司/地域主体1
```

短占位符只在当前文档/任务内稳定，不表达真实公司名称。可恢复模式将原始表面词、全称、别名、地域和关系写入加密 mapping；无密码模式丢弃 mapping，因而不可恢复。报告只输出数量和类别，不输出原值。唯一链接的别名保留 `机构N-别名N` 关系语义；歧义别名仍脱敏为普通 `机构N`，不伪造主体关系。这样既保留可确认的“同主体/从属主体”语义，又不会把真实名称放进供大模型读取的文本。新 mapping 使用 compact-v1；旧 mapping 仍可按旧 Token 格式恢复。

人员名单也采用同文档内的一致性策略：联系人、董事会成员和部门名册中已高置信识别的短姓名，后续重复出现时复用同一 `⟦人员N⟧`；传播只允许来自名单类规则且要求姓名至少重复出现两次，避免把普通职务词扩散为人员实体。

## 13.3 文件名脱敏

CLI 输出统一使用 `document-<safe-id>.masked.md` 和 `document-<safe-id>.report.json`；设置密码的可恢复模式
另外生成 `document-<safe-id>.normalized.md` 和 `document-<safe-id>.mapping.enc`。`safe-id` 由任务 ID 与输入文件名计算，
不可逆且不包含原文件名。原文件名只作为加密 mapping 的字段保存；普通还原仍使用安全文件名，确有权限时才显式使用
`--restore-filename`。无密码模式不生成 mapping，也不能恢复。

## 13.4 金融领域训练与验收

训练脚手架提供可复现的金融领域合成样本，覆盖全称、简称、子公司、分公司、管理人、托管机构和关联主体。样本拆为 NER JSONL 与 linking JSONL，先做无模型标签/损失一致性校验，再接入本地 Qwen checkpoint 训练。合成数据只用于管线验证，正式上线前还要用授权的真实金融语料做文档级切分、别名链接准确率、子公司误连率和残留审计评估。

## 13.5 交付验收条件

- 可恢复模式生成安全文件名的 masked、mapping 和 report 文件；无密码模式只生成 masked 和 report 文件；
- masked 文件审计无残留 PII、Token 冲突和 Markdown 结构损坏；
- 全称、简称、子公司关系在报告中只体现为安全计数，在 masked 文本中体现为伪主体 ID；
- mapping 解密后恢复结果与规范化原文逐字节一致；
- 旧的带原始文件名测试产物不作为交付目录，交付目录只保留安全文件名。

---

# 14. 公共机构白名单制度

## 14.1 目标与范围

监管机关、宏观管理部门、行业自律组织和公开市场基础设施通常是公开背景信息，保留名称有助于下游大模型理解监管依据和事件语境。项目使用可配置的精确白名单，不把这类名称替换为机构 Token。

默认词典位于 `rules/organization_whitelist.txt`，包含正式名称及常用公开简称。配置入口为：

```yaml
whitelist:
  organizations: ../rules/organization_whitelist.txt
```

## 14.2 冲突与安全边界

白名单匹配生成 `anonymize=false`、`protect=true` 的 ORG Span，并以高优先级进入统一 Resolver。白名单只保护精确名称；若该名称严格包含在一个更长、需要脱敏的机构候选内，则丢弃该白名单子 Span，由更长机构正常脱敏。这样可以避免公共机构简称成为私有主体名称中的“放行片段”。

白名单不使用模糊前缀、任意地域通配或模型推断。新增名称通过词典评审完成，不需要重新训练 NER 模型；审计报告只记录命中、接受和保护数量，不回显其他敏感内容。

## 14.3 验收条件

- 白名单正式名称和公开简称在 masked 文本中保持不变；
- 非白名单机构仍生成 `⟦机构N⟧`；
- 含白名单子串的更长非白名单机构不会被部分放行；
- mapping 恢复结果仍与规范化原文一致；
- 项目配置与包内默认配置引用各自对应的白名单词典。
