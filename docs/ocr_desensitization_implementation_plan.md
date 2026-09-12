# OCR 审计文档可逆脱敏系统实施方案

> 版本：V0.2（2026-09-12）｜状态：生效
> 当前代码入口、项目约束和文档导航见 [index.md](index.md)、[../AGENTS.md](../AGENTS.md) 和 [ADR-0001](adr/0001-hybrid-reversible-desensitization.md)。

## 1. 项目目标

本项目用于处理 OCR 识别后的审计、投资、保险资管等文档，形成一个**高效、可配置、可逆、可离线运行**的脱敏系统。

核心目标：

1. 对 OCR 文本进行确定性规整，修复常见断行、异常空格、结构化字段拆分等问题。
2. 对敏感实体进行高精度识别和脱敏。
3. 支持脱敏后的文本完整还原。
4. 支持用户配置脱敏类型及自定义字段。
5. 引入一个经过领域微调/数据蒸馏的 Qwen 小模型，提高人名、公司名、项目名、机构名等语义型实体的识别效果。
6. 保留规则引擎处理身份证、金额、数字、手机号、银行账号、合同编号等确定性字段。
7. 保证模型只负责“识别”，不直接改写原文，避免数字、标点、OCR 内容被模型意外修改。
8. 支持 Windows 离线部署及后续批量处理。

---

# 2. 总体技术路线

最终采用：

```text
OCR 原文
   │
   ▼
确定性文本规整
Normalizer
   │
   ▼
┌───────────────────────────────┐
│          实体识别层            │
│                               │
│ 规则/词典识别器       Qwen小模型 │
│       │                 │      │
│ 身份证                  人名     │
│ 手机号                  公司名   │
│ 金额                    机构名   │
│ 普通数字                项目名   │
│ 银行账号                部门名   │
│ 合同编号                地址等   │
│ 自定义字段                       │
└─────────────┬─────────────────┘
              │
              ▼
         Candidate Span[]
              │
              ▼
          Span Resolver
              │
              ▼
        Accepted Span[]
              │
              ▼
      确定性 Token 替换
              │
      ┌───────┴────────┐
      ▼                ▼
masked.md        mapping.enc
      │
      ▼
后续 LLM / 审计分析

恢复：
masked.md + mapping.enc
          │
          ▼
      Restore Engine
          │
          ▼
     normalized.md
```

核心原则：

> **模型只负责识别实体，不负责重写文档。**

即：

```text
detect != rewrite
```

这样才能保证：

```text
restore(mask(normalize(original)))
==
normalize(original)
```

---

# 3. 为什么采用“小模型 + 规则”的混合方案

不建议把全部脱敏任务直接交给 1B/2B 小模型。

原因：

- 身份证、手机号、金额、银行账号等规则型字段，正则和校验算法更准确。
- 模型直接生成脱敏文本时可能改动 OCR 原文、数字、标点和换行。
- 模型无法保证严格可逆。
- 自定义字段变化频繁，不可能每增加一个字段就重新训练模型。
- 公司简称、项目名称、人名等实体依赖上下文，纯规则又很难覆盖。

因此采用：

## 规则引擎负责

- 身份证
- 手机号
- 银行账号
- 金额
- 普通数字
- 日期
- 合同编号
- 自定义正则
- 自定义固定词
- 自定义字段值

## Qwen 小模型负责

- PERSON：人名
- ORG：公司/机构
- PROJECT：项目名
- DEPARTMENT：部门
- ADDRESS：地址
- 其他需要上下文判断的实体

---

# 4. 推荐模型

第一版推荐：

```text
Qwen3-1.7B Base
```

不优先使用 0.6B。

原因：

- 审计材料实体边界复杂。
- 公司简称、项目名称、机构名称存在大量上下文依赖。
- 1.7B 仍足够小，适合本地部署。
- 可进行 LoRA / QLoRA / 全量微调。
- 可进一步转换为 ONNX、GGUF 或其他本地推理格式。

模型不需要承担聊天、推理或文本生成任务。

推荐最终训练为：

```text
Token Classification / NER
```

即：

```text
原文 Token
   ↓
Qwen3-1.7B
   ↓
BIO / BILOU 标签
```

例如：

```text
张   B-PERSON
三   I-PERSON

中   B-ORG
国   I-ORG
人   I-ORG
保   I-ORG
...
```

---

# 5. 数据处理流程

## 5.1 OCR 原始输入

例如：

```text
中国 人 保 资 产 管 理 有 限 公 司于2025年
委派张 三负责华电资本增资扩股
引战项目。
```

不能直接交给模型。

先进入 Normalizer。

---

# 6. OCR 文本规整模块

模块名称：

```text
normalizer/
```

建议包括：

```text
normalizer/
├─ pipeline.py
├─ unicode.py
├─ whitespace.py
├─ paragraphs.py
├─ structured_repair.py
└─ markdown.py
```

---

## 6.1 Unicode 规整

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

## 6.2 中文异常空格修复

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

## 6.3 OCR 断行恢复

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

## 6.4 结构化字段修复

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

## 6.5 幂等要求

必须保证：

```python
normalize(normalize(text)) == normalize(text)
```

这是自动测试硬要求。

---

# 7. 实体统一数据结构

所有识别器必须返回统一结构。

建议：

```python
@dataclass
class EntitySpan:
    start: int
    end: int
    entity_type: str
    text: str
    score: float
    priority: int
    source: str
    rule_id: str | None = None
```

示例：

```json
{
  "start": 1562,
  "end": 1580,
  "entity_type": "ID_CARD",
  "text": "11010119900101123X",
  "score": 1.0,
  "priority": 100,
  "source": "id_card_rule",
  "rule_id": "cn_id_18"
}
```

识别阶段：

> **禁止修改原文本。**

---

# 8. 规则识别器

目录：

```text
recognizers/
```

建议：

```text
recognizers/
├─ base.py
├─ id_card.py
├─ phone.py
├─ bank_account.py
├─ amount.py
├─ number.py
├─ organization_dictionary.py
├─ literal.py
├─ field.py
├─ regex.py
├─ alias.py
└─ model_ner.py
```

---

# 9. 身份证识别

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
生成 EntitySpan
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

# 10. 金额识别

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
⟦NUMBER:0001⟧万元
```

---

# 11. 普通数字识别

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

# 12. 公司和机构词典

已知公司、机构、自定义项目等固定词使用：

```text
Aho-Corasick
```

推荐库：

```text
pyahocorasick
```

优势：

- C 实现
- 一次扫描匹配大量关键词
- 适合数万、数十万固定实体
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

# 13. 公司简称自动学习

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

# 14. 自定义字段

必须支持用户自由扩展，不修改核心代码。

至少支持四种方式。

---

## 14.1 Literal

```yaml
custom:
  - name: 华电项目
    type: PROJECT
    matcher: literal
    values:
      - 华电资本增资扩股引战项目
```

---

## 14.2 Dictionary

```yaml
custom:
  - name: 内部公司
    type: ORG
    matcher: dictionary
    file: rules/companies.txt
```

---

## 14.3 Regex

```yaml
custom:
  - name: 合同编号
    type: CONTRACT_ID
    matcher: regex
    patterns:
      - 'HT-[0-9]{4}-[0-9]+'
```

优先考虑支持 RE2 风格正则，避免灾难性回溯。

---

## 14.4 Field

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

# 15. Qwen 小模型的职责

Qwen 模型只识别以下语义实体：

```text
PERSON
ORG
PROJECT
DEPARTMENT
ADDRESS
```

必要时可以扩展：

```text
PRODUCT
FUND
PLAN
TRUSTEE
CUSTODIAN
```

模型不直接负责：

```text
ID_CARD
PHONE
AMOUNT
NUMBER
BANK_ACCOUNT
```

---

# 16. Qwen 模型训练方式

第一阶段不建议复杂的 Logit Distillation。

推荐：

```text
Teacher 自动标注
+
程序自动校验
+
人工抽样修正
+
Qwen3-1.7B SFT / Token Classification
```

即所谓：

```text
数据蒸馏
```

---

# 17. Teacher 数据生产

原文：

```text
2025年3月，中国人保资产管理有限公司委派张三负责
华电资本增资扩股引战项目，投资金额为30000万元。
```

Teacher 输出：

```text
2025年3月，<ORG>中国人保资产管理有限公司</ORG>委派
<PERSON>张三</PERSON>负责
<PROJECT>华电资本增资扩股引战项目</PROJECT>，
投资金额为<AMOUNT>30000万元</AMOUNT>。
```

必须执行自动验证：

```python
remove_tags(labeled_text) == original_text
```

不满足则直接丢弃。

这样避免 Teacher：

- 改错别字
- 改数字
- 改标点
- 修改 OCR 内容
- 漏句
- 增加不存在内容

---

# 18. 训练标签

最终推荐训练 BIO/BILOU。

例如：

```text
中   B-ORG
国   I-ORG
人   I-ORG
保   I-ORG

张   B-PERSON
三   I-PERSON
```

推荐使用：

```text
Qwen3ForTokenClassification
```

而不是让模型生成 JSON。

---

# 19. 为什么优先 Token Classification

相比生成式 JSON：

```text
输入文本
→ 模型生成实体列表
```

Token Classification 有以下优势：

- 不需要自回归生成
- 推理更快
- 不会生成不存在的实体
- 不会修改原文
- 天然获得位置
- 更适合批量 OCR 文档
- 更容易做阈值控制

---

# 20. 模型分块

不要直接按整个长文档处理。

推荐：

```text
512 ~ 2048 tokens / chunk
```

采用：

```text
滑动窗口
```

例如：

```text
chunk_size = 1024
overlap = 128
```

合并重叠区域时：

```text
高置信度优先
完整实体优先
跨窗口实体拼接
```

---

# 21. 训练集规模

第一阶段建议：

```text
10,000 ～ 30,000 个高质量 chunk
```

示例：

```text
真实审计材料                 5,000
Teacher 自动标注            15,000
OCR 噪声增强                 5,000
Hard Negative               5,000
```

不需要第一版就追求百万级。

后续根据验证集效果扩展：

```text
50k
100k
```

---

# 22. OCR 数据增强

训练时主动制造 OCR 噪声。

例如：

```text
中国人民保险集团股份有限公司
```

变成：

```text
中国 人民保险集团 股份有限公司
```

或者：

```text
中国人民保险集团股份
有限公司
```

人名：

```text
张三
```

变成：

```text
张 三
```

身份证：

```text
11010119900101123X
```

变成：

```text
110101 19900101 123X
```

项目名：

```text
华电资本增资扩股引战项目
```

变成：

```text
华电资本增资扩股
引战项目
```

---

# 23. Hard Negative

必须专门构造困难负样本。

例如：

```text
张三科技有限公司
```

正确：

```text
ORG
```

不能识别为：

```text
PERSON = 张三
```

---

例如：

```text
华电资本增资扩股引战项目
```

正确：

```text
PROJECT
```

不能把：

```text
华电资本
```

单独识别成 ORG。

---

例如：

```text
投资金额30000万元
```

正确：

```text
AMOUNT
```

不能产生：

```text
NUMBER
+
AMOUNT
```

---

# 24. 模型置信度

模型返回：

```text
start
end
entity_type
score
```

例如：

```text
[20:22] PERSON 0.998
[38:50] ORG    0.972
```

配置：

```yaml
model:
  PERSON:
    threshold: 0.90

  ORG:
    threshold: 0.92

  PROJECT:
    threshold: 0.85

  DEPARTMENT:
    threshold: 0.90
```

推荐：

```text
>= 0.95
直接接受

0.80 ～ 0.95
进入候选或报告

< 0.80
默认丢弃
```

实际阈值必须根据验证集 Precision / Recall 调整。

---

# 25. Span Resolver

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

## 25.1 推荐优先级

```text
ID_CARD          100
CUSTOM_EXACT      98
BANK_ACCOUNT      96
CONTRACT_ID       95
ORG_DICTIONARY    94
CUSTOM_FIELD      93
AMOUNT            90
ORG_MODEL         85
PROJECT_MODEL     84
PERSON_MODEL      80
ADDRESS_MODEL     75
NUMBER            10
```

---

## 25.2 冲突规则

依次：

```text
1. priority 高者优先
2. priority 相同 → score 高者
3. score 相同 → 长实体优先
4. 最后按 start 排序
```

---

## 25.3 示例

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

# 26. Token 设计

不要使用：

```text
***
XXXX
某某公司
张某
```

因为无法稳定还原。

推荐：

```text
⟦PERSON:000001⟧
⟦ORG:000001⟧
⟦PROJECT:000001⟧
⟦ID_CARD:000001⟧
```

例如：

```text
张三代表中国人保资产管理有限公司负责华电资本增资扩股引战项目。
```

变成：

```text
⟦PERSON:000001⟧代表⟦ORG:000001⟧负责⟦PROJECT:000001⟧。
```

这种形式也方便后续 LLM：

```text
知道实体类型
但不知道真实值
```

---

# 27. 同实体保持一致

例如：

```text
张三与李四签署协议。
张三负责后续工作。
```

必须变成：

```text
⟦PERSON:000001⟧与⟦PERSON:000002⟧签署协议。
⟦PERSON:000001⟧负责后续工作。
```

映射：

```json
{
  "张三": "PERSON:000001",
  "李四": "PERSON:000002"
}
```

---

# 28. 确定性替换

绝对不要：

```python
for entity in entities:
    text = text.replace(...)
```

正确方式：

1. Resolver 输出最终 Span。
2. 按 start 排序。
3. 只扫描原文一次。
4. 使用 list append。
5. 最后 `"".join()`。

例如：

```text
[100:102] PERSON
[300:320] ORG
[800:818] ID_CARD
```

一次构造：

```text
0 → 100       append 原文
100 → 102     append Token
102 → 300     append 原文
300 → 320     append Token
...
```

---

# 29. Mapping

不能明文保存：

```text
mapping.json
```

否则脱敏意义大幅降低。

推荐：

```text
xxx.mapping.enc
```

内部：

```json
{
  "schema_version": 1,
  "job_id": "xxxx",
  "source_hash": "...",
  "normalized_hash": "...",
  "masked_hash": "...",
  "config_hash": "...",
  "entities": {
    "⟦PERSON:000001⟧": "张三",
    "⟦ORG:000001⟧": "中国人保资产管理有限公司"
  }
}
```

---

# 30. Mapping 加密

推荐：

```text
AES-256-GCM
```

密钥来源：

```text
密码
↓
scrypt / Argon2
↓
AES Key
```

或者内网环境：

```text
独立 key 文件
```

要求：

- mapping 不可明文。
- 文件被修改时必须检测。
- 密码错误必须直接失败。
- masked 文件与 mapping 不匹配时拒绝恢复。

---

# 31. Restore Engine

恢复时：

```text
masked.md
+
mapping.enc
```

一次扫描 Token。

例如匹配：

```regex
⟦([A-Z_]+):([0-9]+)⟧
```

映射：

```python
mapping[token]
```

要求：

```text
restore(mask(normalized))
==
normalized
```

必须达到字节级一致。

---

# 32. 配置文件

推荐：

```yaml
version: 1

normalization:
  unicode_nfkc: true
  remove_han_spaces: true
  merge_broken_lines: true
  repair_id_card: true
  repair_amount: true
  repair_phone: true

entities:

  PERSON:
    detect: true
    anonymize: true
    protect_when_disabled: true

  ID_CARD:
    detect: true
    anonymize: true
    protect_when_disabled: true

  ORG:
    detect: true
    anonymize: true
    protect_when_disabled: true

  PROJECT:
    detect: true
    anonymize: true

  DEPARTMENT:
    detect: true
    anonymize: true

  ADDRESS:
    detect: true
    anonymize: true

  AMOUNT:
    detect: true
    anonymize: false
    protect_when_disabled: true

  NUMBER:
    detect: true
    anonymize: false

model:
  enabled: true
  backend: transformers
  path: models/qwen3-1.7b-pii

  chunk_size: 1024
  overlap: 128

  thresholds:
    PERSON: 0.90
    ORG: 0.92
    PROJECT: 0.85
    DEPARTMENT: 0.90
    ADDRESS: 0.88

custom:

  - name: 项目名称
    type: PROJECT
    matcher: field
    labels:
      - 项目名称
      - 项目名
      - 投资项目
    anonymize: true
    priority: 95

  - name: 内部公司
    type: ORG
    matcher: dictionary
    file: rules/companies.txt
    anonymize: true

  - name: 合同编号
    type: CONTRACT_ID
    matcher: regex
    patterns:
      - 'HT-[0-9]{4}-[0-9]+'
    anonymize: true
```

---

# 33. 推荐目录结构

```text
desensitize/
│
├─ README.md
├─ pyproject.toml
├─ xxx.md
│
├─ config/
│  └─ default.yaml
│
├─ rules/
│  ├─ companies.txt
│  ├─ departments.txt
│  ├─ org_suffixes.txt
│  └─ surnames.txt
│
├─ models/
│  └─ qwen3-1.7b-pii/
│
├─ src/
│  ├─ normalizer/
│  │  ├─ pipeline.py
│  │  ├─ unicode.py
│  │  ├─ whitespace.py
│  │  ├─ paragraphs.py
│  │  ├─ markdown.py
│  │  └─ structured_repair.py
│  │
│  ├─ recognizers/
│  │  ├─ base.py
│  │  ├─ id_card.py
│  │  ├─ phone.py
│  │  ├─ bank_account.py
│  │  ├─ amount.py
│  │  ├─ number.py
│  │  ├─ dictionary.py
│  │  ├─ field.py
│  │  ├─ regex.py
│  │  ├─ alias.py
│  │  └─ model_ner.py
│  │
│  ├─ resolver.py
│  ├─ tokenizer.py
│  ├─ mapping.py
│  ├─ crypto.py
│  ├─ anonymizer.py
│  ├─ restorer.py
│  ├─ pipeline.py
│  └─ cli.py
│
├─ training/
│  ├─ teacher_label.py
│  ├─ validate_labels.py
│  ├─ build_dataset.py
│  ├─ augment_ocr.py
│  ├─ train_token_classifier.py
│  └─ evaluate.py
│
├─ tests/
│  ├─ test_normalizer.py
│  ├─ test_id_card.py
│  ├─ test_amount.py
│  ├─ test_number.py
│  ├─ test_dictionary.py
│  ├─ test_model.py
│  ├─ test_overlap.py
│  ├─ test_restore.py
│  └─ test_xxx_md.py
│
├─ benchmarks/
│  └─ benchmark.py
│
└─ output/
```

---

# 34. 输出文件

处理：

```text
xxx.md
```

得到：

```text
output/
├─ xxx.normalized.md
├─ xxx.masked.md
├─ xxx.mapping.enc
└─ xxx.report.json
```

报告：

```json
{
  "PERSON": {
    "detected": 21,
    "masked": 21
  },
  "ORG": {
    "detected": 13,
    "masked": 13
  },
  "ID_CARD": {
    "detected": 8,
    "masked": 8
  },
  "AMOUNT": {
    "detected": 32,
    "masked": 0
  },
  "NUMBER": {
    "detected": 867,
    "masked": 0
  },
  "processing_ms": 182
}
```

报告和日志不得直接输出敏感原文。

---

# 35. CLI

推荐：

```bash
desense xxx.md
```

指定配置：

```bash
desense xxx.md -c config/default.yaml
```

恢复：

```bash
desense restore \
  output/xxx.masked.md \
  output/xxx.mapping.enc
```

查看识别情况：

```bash
desense inspect xxx.md
```

性能测试：

```bash
desense benchmark xxx.md
```

---

# 36. 测试要求

## 36.1 Normalizer

必须：

```python
assert normalize(normalize(x)) == normalize(x)
```

---

## 36.2 可逆性

必须：

```python
normalized = normalize(original)

masked, mapping = anonymize(normalized)

restored = restore(masked, mapping)

assert restored == normalized
```

---

## 36.3 冲突测试

测试：

```text
身份证中的数字不能被 NUMBER 抢占
金额中的数字不能被 NUMBER 抢占
公司名中的人名不能单独脱敏
项目名内部的公司简称不能再次脱敏
```

---

## 36.4 一致性

```text
相同实体重复出现 → 相同 Token
不同实体 → 不同 Token
```

---

## 36.5 安全性

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

# 37. 模型评估指标

模型单独评估：

```text
Precision
Recall
F1
```

必须分别统计：

```text
PERSON
ORG
PROJECT
DEPARTMENT
ADDRESS
```

同时统计：

```text
Exact Span F1
```

而不是只统计 Token Accuracy。

---

# 38. 系统整体评估

最终系统重点看：

```text
敏感字段漏脱敏率
误脱敏率
可逆率
处理速度
内存占用
```

其中：

```text
可逆率必须 = 100%
```

---

# 39. Benchmark

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

# 40. C/C++ 使用原则

第一版不要主动自己写 C。

优先：

```text
Python
+
pyahocorasick
+
RE2
+
PyTorch / ONNX Runtime
```

这些底层已经大量使用 C/C++。

只有 benchmark 证明：

```text
Normalizer
Resolver
Token Builder
```

中的某一部分成为明显瓶颈，才单独改 C/C++。

---

# 41. 本地模型部署

开发阶段：

```text
PyTorch / Transformers
```

训练完成后根据硬件选择：

```text
PyTorch
ONNX Runtime
GGUF
其他推理后端
```

NER 模型建议优先评估：

```text
ONNX Runtime
```

原因：

- Windows 部署方便
- CPU / DirectML 可选
- 易打包
- 无需完整 Python AI 环境时可以继续优化

---

# 42. 开发阶段划分

## Phase 1：确定性核心

先完成：

```text
Normalizer
↓
EntitySpan
↓
身份证
↓
金额
↓
数字
↓
公司词典
↓
自定义 Literal
↓
Aho-Corasick
↓
Resolver
↓
Token
↓
Mapping
↓
Restore
```

验收条件：

```text
restore(mask(normalize(xxx.md)))
==
normalize(xxx.md)
```

---

## Phase 2：自定义能力

增加：

```text
Field Matcher
Regex Matcher
Dictionary Matcher
动态简称学习
配置文件
CLI
```

---

## Phase 3：Teacher 数据集

完成：

```text
Teacher 标注
↓
标签验证
↓
OCR 增强
↓
Hard Negative
↓
训练集
验证集
测试集
```

---

## Phase 4：Qwen3-1.7B NER

训练：

```text
PERSON
ORG
PROJECT
DEPARTMENT
ADDRESS
```

输出：

```text
EntitySpan[]
```

接入 Resolver。

---

## Phase 5：模型优化

比较：

```text
Qwen Token Classification
vs
Qwen 生成式实体提取
```

指标：

```text
Precision
Recall
F1
速度
内存
```

默认优先 Token Classification。

---

## Phase 6：性能与部署

进行：

```text
ONNX 导出
量化
Windows 离线部署
批量文件处理
Benchmark
```

---

# 43. 第一版不要做的事情

暂时不要：

```text
直接让 LLM 输出完整脱敏文档
自己写 C 扩展
加入大量复杂 NLP 框架
做 GUI
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

# 44. 第一版建议实体集合

模型：

```text
PERSON
ORG
PROJECT
DEPARTMENT
ADDRESS
```

规则：

```text
ID_CARD
PHONE
BANK_ACCOUNT
AMOUNT
NUMBER
DATE
CONTRACT_ID
```

自定义：

```text
CUSTOM_LITERAL
CUSTOM_DICTIONARY
CUSTOM_REGEX
CUSTOM_FIELD
```

---

# 45. 项目最终验收标准

第一阶段正式可用版本应达到：

## 功能

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

## 正确性

必须：

```text
restore(mask(normalize(x)))
==
normalize(x)
```

## 安全

- mapping 加密。
- 日志无敏感原文。
- mapping 与 masked 文件绑定。
- 篡改可以检测。

## 性能

- 不对大量关键词逐个全文扫描。
- 固定词使用 Aho-Corasick。
- 正则避免灾难性回溯。
- 最终文本只进行一次确定性重建。
- 小模型批量推理。

---

# 46. 最终推荐技术栈

```text
语言：
Python 3.12

OCR 规整：
Python

固定实体匹配：
pyahocorasick

规则：
Python regex / RE2

领域 NER：
Qwen3-1.7B

训练：
PyTorch + Transformers

模型形式：
Qwen3ForTokenClassification

部署：
PyTorch / ONNX Runtime

配置：
YAML

Mapping：
AES-256-GCM

CLI：
Typer / argparse

测试：
pytest

Benchmark：
pytest-benchmark / 自定义计时
```

---

# 47. 最终实施原则

整个系统按以下原则开发：

1. **原文不可由模型改写。**
2. **模型只负责语义实体识别。**
3. **规则负责确定性字段。**
4. **所有识别器统一输出 Span。**
5. **所有冲突由 Resolver 统一解决。**
6. **所有替换由 Anonymizer 一次完成。**
7. **所有映射均可逆且加密保存。**
8. **自定义字段不依赖重新训练模型。**
9. **优先使用现有 C/C++ 高性能库，不提前手写 C。**
10. **最终效果必须以真实 `xxx.md` 和领域验证集为准，而不是仅凭模型 benchmark。**

最终目标不是单独做一个“小模型”，而是形成：

```text
确定性 OCR 规整
+
规则识别
+
高速词典匹配
+
领域 Qwen 小模型
+
统一 Span Resolver
+
确定性可逆脱敏
+
加密 Mapping
```

这一组合既能获得专用领域模型对审计文档的语义理解能力，又能保留规则系统在准确性、安全性、速度和可逆性上的优势。

---

# 48. 机构简称、子公司关系与安全文件名实施补充

## 48.1 两阶段不是完整文档推理两次

机构关系采用“候选识别 → 局部实体链接”的两阶段逻辑：

1. 第一阶段由机构词典、`以下简称/简称` 声明、后缀规则和可选 Qwen NER 找出全称、简称、子公司/分公司等候选 Span。
2. 第二阶段只接收候选提及、有限局部上下文和注册表 Top-K 候选，判断 `ALIAS_OF`、`SUBSIDIARY_OF` 或 `BRANCH_OF`。明确声明和高置信注册表命中由规则直接确定，只有歧义候选才交给链接模型；在链接仍不确定时，必须回退为无关系的普通机构 Token，不能因为无法链接而留下原简称。

因此默认不需要两个完整模型。可以继续使用同一个 Qwen 主干：NER 头负责边界识别，轻量 linking head/adapter 负责候选分类；关系注册表独立保存，避免把易变的机构关系硬编码进权重。

## 48.2 AI 可读但不泄露的占位符

为了让下游大模型理解同一主体及其关系，占位符保留安全的伪主体 ID 和关系类别：

```text
⟦机构1⟧                  # 主体全称
⟦机构1-别名1⟧             # 机构1 的别名1
⟦机构1-子公司1⟧           # 机构1 的子公司/地域主体1
```

短占位符只在当前文档/任务内稳定，不表达真实公司名称。原始表面词、全称、别名、地域和关系写入加密 mapping；报告只输出数量和类别，不输出原值。唯一链接的别名保留 `机构N-别名N` 关系语义；歧义别名仍脱敏为普通 `机构N`，不伪造主体关系。这样既保留可确认的“同主体/从属主体”语义，又不会把真实名称放进供大模型读取的文本。新 mapping 使用 compact-v1；旧 mapping 仍可按旧 Token 格式恢复。

人员名单也采用同文档内的一致性策略：联系人、董事会成员和部门名册中已高置信识别的短姓名，后续重复出现时复用同一 `⟦人员N⟧`；传播只允许来自名单类规则且要求姓名至少重复出现两次，避免把普通职务词扩散为人员实体。

## 48.3 文件名脱敏

CLI 输出统一使用 `document-<safe-id>.<kind>.md`、`document-<safe-id>.mapping.enc` 和 `document-<safe-id>.report.json`。`safe-id` 由任务 ID 与输入文件名计算，不可逆且不包含原文件名。原文件名只作为加密 mapping 的字段保存；普通还原仍使用安全文件名，确有权限时才显式使用 `--restore-filename`。

## 48.4 金融领域训练与验收

训练脚手架提供可复现的金融领域合成样本，覆盖全称、简称、子公司、分公司、管理人、托管机构和关联主体。样本拆为 NER JSONL 与 linking JSONL，先做无模型标签/损失一致性校验，再接入本地 Qwen checkpoint 训练。合成数据只用于管线验证，正式上线前还要用授权的真实金融语料做文档级切分、别名链接准确率、子公司误连率和残留审计评估。

## 48.5 交付验收条件

- 三个 `docs` 输入文件均生成安全文件名的 masked、mapping 和 report 文件；
- masked 文件审计无残留 PII、Token 冲突和 Markdown 结构损坏；
- 全称、简称、子公司关系在报告中只体现为安全计数，在 masked 文本中体现为伪主体 ID；
- mapping 解密后恢复结果与规范化原文逐字节一致；
- 旧的带原始文件名测试产物不作为交付目录，交付目录只保留安全文件名。

---

# 49. 公共机构白名单制度

## 49.1 目标与范围

监管机关、宏观管理部门、行业自律组织和公开市场基础设施通常是公开背景信息，保留名称有助于下游大模型理解监管依据和事件语境。项目使用可配置的精确白名单，不把这类名称替换为机构 Token。

默认词典位于 `rules/organization_whitelist.txt`，包含正式名称及常用公开简称。配置入口为：

```yaml
whitelist:
  organizations: ../rules/organization_whitelist.txt
```

## 49.2 冲突与安全边界

白名单匹配生成 `anonymize=false`、`protect=true` 的 ORG Span，并以高优先级进入统一 Resolver。白名单只保护精确名称；若该名称严格包含在一个更长、需要脱敏的机构候选内，则丢弃该白名单子 Span，由更长机构正常脱敏。这样可以避免公共机构简称成为私有主体名称中的“放行片段”。

白名单不使用模糊前缀、任意地域通配或模型推断。新增名称通过词典评审完成，不需要重新训练 NER 模型；审计报告只记录命中、接受和保护数量，不回显其他敏感内容。

## 49.3 验收条件

- 白名单正式名称和公开简称在 masked 文本中保持不变；
- 非白名单机构仍生成 `⟦机构N⟧`；
- 含白名单子串的更长非白名单机构不会被部分放行；
- mapping 恢复结果仍与规范化原文一致；
- 项目配置与包内默认配置引用各自对应的白名单词典。
