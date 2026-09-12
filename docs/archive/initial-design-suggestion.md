> 版本：V0.1（2026-09-12）｜状态：历史归档
> 当前实施以 [../ocr_desensitization_implementation_plan.md](../ocr_desensitization_implementation_plan.md)、[../index.md](../index.md) 和代码为准。

研究后，我建议不要把它做成“几个正则 + replace”的小工具，而是按一个**可逆、可解释、面向 OCR 文档的本地脱敏引擎**来设计。

微软 Presidio 的核心思路非常适合借鉴：把“PII 识别”和“匿名化操作”分离，识别阶段只产生实体区间，后续统一解决重叠并执行替换；同时支持自定义 recognizer/operator 和 deanonymize。Presidio 本身也明确支持 regex、NER、规则、上下文等多种识别方式。([微软 GitHub][1])
但我不建议直接基于 Presidio 二次开发，因为它并不是针对中文 OCR 审计材料设计的，我们自己实现会更轻、更快、更可控。

## 一、最终架构

建议整个程序按下面的数据流运行：

```text
OCR 原始文件
    ↓
[1] OCR 规整 Normalizer
    ↓
规整文本 normalized.md
    ↓
[2] 多路实体识别 Recognizers
    ├── 身份证规则
    ├── 公司名规则/词典
    ├── 人名规则/词典
    ├── 金额规则
    ├── 普通数字规则
    ├── 自定义字段
    ├── 自定义正则
    └── 可选 NER
    ↓
Candidate Span[]
    ↓
[3] 冲突解析 Resolver
    ↓
Accepted Span[]
    ↓
[4] Token/Placeholder 生成
    ↓
脱敏文本 masked.md
    +
加密映射 mapping.enc
    +
脱敏报告 report.json
    ↓
[5] Restore Engine
    ↓
完整还原 normalized.md
```

最重要的设计原则是：

> **识别阶段绝对不修改文本。**

所有识别器统一返回：

```text
start
end
entity_type
surface
score
priority
source
rule_id
```

比如：

```json
{
  "start": 1562,
  "end": 1580,
  "entity_type": "ID_CARD",
  "surface": "11010119900101123X",
  "score": 1.0,
  "priority": 100,
  "source": "id_card_rule",
  "rule_id": "cn_id_18"
}
```

最终只由 Resolver 决定哪些区间真正替换。

这会解决以后几乎所有扩展问题。

---

# 二、第一阶段：OCR 文本规整

这个阶段实际上会决定后面脱敏准确率的一半。

不能简单：

```python
text.replace(" ", "")
text.replace("\n", "")
```

否则表格、金额、日期、段落都会被破坏。

我建议 Normalizer 分成 6 个步骤。

### 1. Unicode 基础规整

处理：

```text
全角数字 → 半角数字
全角英文字母 → 半角
NBSP → 普通空格
\r\n / \r → \n
连续 Tab → 空格
异常 Unicode 空白字符
```

使用 Unicode NFKC，但必须做成可配置。

例如：

```text
１２３４５６
→
123456
```

---

### 2. Markdown/OCR 块分类

因为你的 `xxx.md` 很可能包含：

```text
标题
正文
表格
字段：值
列表
分页符
```

不能全部一起处理。

先把每一行分类成：

```text
HEADING
PARAGRAPH
TABLE
LIST
FIELD
PAGE_BREAK
CODE
EMPTY
```

例如：

```text
# 投资项目报告
```

保持原样。

```text
| 投资人 | 金额 |
| 张三 | 100万元 |
```

按 Cell 单独规整。

正文：

```text
中国人民财产保险股份
有限公司于2025年……
```

允许进行智能合并。

---

### 3. 中文异常空格修复

例如 OCR：

```text
中国 人 民 财 产 保 险 股 份 有 限 公 司
```

恢复：

```text
中国人民财产保险股份有限公司
```

但不能直接删除所有空格。

只处理明确的：

```text
汉字 + 空格 + 汉字
```

同时设置保护区：

```text
表格列
英文句子
代码
编号
```

---

### 4. OCR 断行恢复

例如：

```text
本公司于2025年1月向中国人民财产保险股份
有限公司支付保险费用。
```

恢复：

```text
本公司于2025年1月向中国人民财产保险股份有限公司支付保险费用。
```

建议采用规则判断：

```text
上一行不是：
。！？；：
标题
列表
表格

并且

下一行不是：
标题
编号
列表
字段名
```

才进行合并。

这比直接删换行安全很多。

---

### 5. 结构化字段专项修复

这个非常值得单独实现。

身份证：

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

电话号码：

```text
138 0013 8000
```

可根据上下文修复。

这里不是“全局删除空格”，而是：

> 发现疑似结构化实体 → 局部重组 → 校验 → 确认后修改。

---

### 6. Normalizer 必须幂等

必须保证：

```text
normalize(normalize(text))
==
normalize(text)
```

这是以后非常重要的测试条件。

---

# 三、第二阶段：实体识别

这里不要只使用一种技术。

推荐：

```text
高精度规则
+
高速词典
+
上下文识别
+
可选 NER
```

---

## 1. 身份证

这是最简单也最应该做到 100% 准确的一项。

不要只写：

```regex
\d{17}[\dXx]
```

需要：

```text
正则匹配
↓
出生年月校验
↓
行政区基本合法性
↓
身份证校验位算法
↓
确认
```

这样：

```text
123456789012345678
```

不会因为碰巧是 18 位数字就被识别为身份证。

优先级建议：

```text
ID_CARD = 100
```

支持：

```text
18位身份证
15位老身份证（可选）
OCR 中有空格的身份证
```

---

# 四、公司名识别

这是实际项目里的重点。

建议使用三层。

## 第一层：已知公司词典

例如：

```text
中国人民保险集团股份有限公司
中国人保资产管理有限公司
华电资本控股有限公司
```

这里不要循环：

```python
for company in companies:
    text.find(company)
```

而使用 **Aho-Corasick**。

`pyahocorasick` 本身就是 C 实现，可以一次扫描同时搜索成千上万个关键词，并支持 Windows/Linux，Automaton 还能预先构建后持久化，因此非常适合你的公司库、自定义项目名、机构名等固定实体。([GitHub][2])

所以最初根本没有必要自己写 C。

复杂度接近：

```text
O(文本长度 + 匹配结果数量)
```

而不是：

```text
O(文本长度 × 关键词数量)
```

---

## 第二层：公司后缀规则

识别未知公司：

```text
xxxx有限公司
xxxx股份有限公司
xxxx集团有限公司
xxxx集团
xxxx银行
xxxx保险股份有限公司
xxxx资产管理有限公司
xxxx证券有限公司
xxxx基金管理有限公司
```

不要使用一个超级贪婪 Regex：

```regex
.*有限公司
```

正确方案应该是：

```text
发现“有限公司”
       ↓
向左寻找合法公司名称边界
       ↓
遇到标点/标题/字段边界停止
       ↓
产生 ORG Candidate
```

这样：

```text
根据协议，中国人民保险集团股份有限公司与……
```

只会取：

```text
中国人民保险集团股份有限公司
```

而不会把：

```text
根据协议，
```

一起拿走。

---

# 五、非常建议增加“简称学习”

这个对于审计文件特别有价值。

比如：

```text
中国人保资产管理有限公司
（以下简称“人保资产”）
```

识别：

```text
全称：
中国人保资产管理有限公司

简称：
人保资产
```

程序动态加入本文件临时词典。

于是后面：

```text
人保资产于2025年……
```

会自动识别。

支持：

```text
以下简称
简称
下称
以下称
简称为
```

这种功能比纯 NER 在审计文件中实际价值可能更高。

---

# 六、人名识别

人名是整个项目中最难的。

不能使用：

```text
2~4个汉字 = 人名
```

否则误报会非常夸张。

建议三层。

### 第一层：自定义人员词典

比如用户配置：

```text
张三
李四
王五
```

还是使用 Aho-Corasick。

准确率最高。

---

### 第二层：上下文识别

例如：

```text
姓名：张三
法定代表人：李四
负责人：王五
联系人：赵六
经办人：
董事长：
总经理：
被保险人：
投保人：
受托人：
```

规则：

```text
字段关键词
+
中国姓氏
+
2~4字姓名
```

这种在结构化审计资料里准确率会非常高。

---

### 第三层：NER

如果要求：

> 文档中没有上下文的“张三”也必须识别。

那么必须引入 NER。

我建议做成：

```yaml
ner:
  enabled: true
  provider: paddlenlp
```

而不是写死。

候选方案包括：

```text
PaddleNLP
HanLP
自训练 ONNX NER
其他模型
```

HanLP 确实提供中文 NR（人名）、NT（机构）等 NER 能力，公开示例中也能直接识别公司名称和人名。([GitHub][3])

但这里有一个许可证问题需要特别注意：HanLP **源代码**是 Apache-2.0，但官方说明部分预训练模型具有非商业许可限制；LTP 对企事业单位商业使用也有额外授权条件。([GitHub][4])

所以我的建议是：

> **架构支持 NER，但核心程序绝不依赖某个 NER。**

第一版先：

```text
词典
+
上下文
+
规则
```

再用 `xxx.md` 测漏检情况。

如果漏检明显，再接 NER。

---

# 七、金额

金额默认：

```yaml
amount:
  enabled: false
```

但识别器仍然应该运行。

这是一个非常关键的设计。

比如配置：

```text
数字：脱敏
金额：不脱敏
```

原文：

```text
投资金额1000000元
```

如果金额识别器完全关闭，那么 NumberRecognizer 会得到：

```text
1000000
```

最终仍然被脱敏。

这是错误的。

所以要区分：

```text
detect
```

和：

```text
mask
```

金额即使 `enabled=false` 仍然生成：

```text
KEEP / PROTECTED SPAN
```

于是：

```text
1000000元
```

不会被普通数字识别器覆盖。

配置实际上应该是：

```yaml
amount:
  detect: true
  anonymize: false
  protect_when_disabled: true
```

同理适用于：

```text
身份证
公司
手机号
日期
```

这会让配置行为非常符合人的直觉。

---

# 八、普通数字

普通数字必须最后处理。

优先级：

```text
NUMBER = 10
```

例如：

```text
身份证        100
自定义字段      95
公司词典        90
公司规则        85
金额            80
姓名            70
NER            60
普通数字        10
```

所以：

```text
身份证 11010119900101123X
```

永远不会被拆成很多 NUMBER。

---

# 九、自定义字段设计

我建议不要只支持：

```text
关键词
正则
```

而是一次设计好 4 种。

### literal

```yaml
- name: project
  type: PROJECT
  matcher: literal
  values:
    - 华电资本增资扩股引战项目
    - XX项目
```

大量 literal 自动进入 Aho-Corasick。

---

### dictionary

```yaml
- name: companies
  type: ORG
  matcher: dictionary
  file: rules/companies.txt
```

适合：

```text
1万
10万
100万
```

甚至百万级词库。

---

### regex

```yaml
- name: contract_id
  type: CONTRACT_ID
  matcher: regex
  patterns:
    - 'HT-\d{4}-\d+'
```

对于用户提供的正则，我建议优先使用 **RE2**。

RE2 的设计目标之一就是保证匹配时间相对于输入长度为线性，避免某些回溯型正则出现灾难性性能问题。([GitHub][5])

而且当前 `google-re2` 已提供 Windows x64 Python wheel。([PyPI][6])

---

### field

这个我认为非常值得加入。

```yaml
- name: project_name
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

直接脱敏 value。

用户以后可以自己添加：

```text
客户名称
交易对手
账户名称
合同编号
项目编号
```

不需要写 Regex。

---

# 十、配置文件建议

最终建议类似：

```yaml
version: 1

normalization:
  unicode_nfkc: true
  remove_han_spaces: true
  merge_broken_lines: true
  repair_id_card: true
  repair_amount: true

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

  AMOUNT:
    detect: true
    anonymize: false
    protect_when_disabled: true

  NUMBER:
    detect: true
    anonymize: false

custom:

  - name: 项目名称
    type: PROJECT
    matcher: field
    labels:
      - 项目名称
      - 项目名
    anonymize: true
    priority: 95

  - name: 内部机构
    type: DEPARTMENT
    matcher: dictionary
    file: rules/departments.txt
    anonymize: true

  - name: 合同编号
    type: CONTRACT_ID
    matcher: regex
    patterns:
      - 'HT-[0-9]{4}-[0-9]+'
    anonymize: true

ner:
  enabled: false
```

---

# 十一、脱敏 Token 设计

不推荐：

```text
***
XXXX
张某
某某公司
```

因为无法可靠还原。

建议：

```text
⟦PERSON:000001⟧
⟦ORG:000001⟧
⟦ID:000001⟧
⟦PROJECT:000001⟧
```

例如：

```text
张三代表中国人保资产管理有限公司签署协议。
```

变为：

```text
⟦PERSON:000001⟧代表⟦ORG:000001⟧签署协议。
```

对于后续给 LLM 做审计分析，这种形式其实非常好。

模型仍然知道：

```text
这是一个人
这是一个公司
```

但不知道实际名称。

---

# 十二、同实体保持一致

比如：

```text
张三与李四签署协议。
张三负责该项目。
```

必须：

```text
⟦PERSON:000001⟧与⟦PERSON:000002⟧签署协议。
⟦PERSON:000001⟧负责该项目。
```

映射：

```json
{
  "PERSON": {
    "张三": "000001",
    "李四": "000002"
  }
}
```

这里要注意：

> 一致性的 Key 应当优先基于“规整后的原始值”。

不要随意：

```text
张三 == 张 三 == 张三先生
```

否则可能导致还原无法做到 byte-for-byte。

---

# 十三、映射文件不能明文保存

这一点我认为应该从第一版就解决。

否则：

```text
masked.md
```

虽然安全，

旁边却放：

```text
mapping.json
```

里面：

```json
{
  "张三": "PERSON001",
  "身份证": "110101..."
}
```

那整个脱敏几乎没有意义。

建议：

```text
xxx.masked.md
xxx.mapping.enc
```

Mapping 使用：

```text
AES-256-GCM
```

里面保存：

```text
schema_version
job_id
source_hash
normalized_hash
masked_hash
config_hash
mapping
```

例如：

```json
{
  "schema": 1,
  "job_id": "7FK29A",
  "entities": {
    "⟦PERSON:000001⟧": "张三",
    "⟦ORG:000001⟧": "中国人保资产管理有限公司"
  }
}
```

密码模式可以：

```text
scrypt(password)
→ AES key
```

或者内部环境直接使用独立 key 文件。

---

# 十四、还原实现

不要：

```python
for key, value in mapping:
    text = text.replace(key, value)
```

这种方式数据量大之后效率差，还容易产生嵌套替换问题。

直接一次扫描：

```regex
⟦([A-Z_]+):([0-9]+)⟧
```

然后：

```python
mapping[token]
```

线性恢复。

最核心的自动化测试：

```python
normalized = normalize(original)

masked, mapping = anonymize(normalized)

restored = restore(masked, mapping)

assert restored == normalized
```

必须 **100% 字节相等**。

这是整个项目最高优先级测试。

---

# 十五、重叠实体解决方案

例如：

```text
张三科技有限公司
```

识别结果可能有：

```text
张三                  PERSON
张三科技有限公司        ORG
```

不能两个都换。

Resolver：

```text
1. priority 高者优先
2. priority 相同，confidence 高者
3. confidence 相同，长实体优先
4. 最后按 start 排序
```

于是：

```text
ORG 90
PERSON 70
```

公司整体获胜。

再比如：

```text
人民币100万元
```

同时产生：

```text
AMOUNT   100万元
NUMBER   100
```

Amount 优先。

这种机制比给每种规则互相写 if/else 干净得多。

Presidio 自己也需要专门解决 full overlap、contained span、partial overlap 等实体冲突，这也是值得借鉴的地方。([微软 GitHub][1])

---

# 十六、性能方案

第一版我建议：

```text
Python 3.12
+
pyahocorasick
+
google-re2
```

这实际上已经大量用了 C/C++：

```text
Python
  ↓
控制流程

pyahocorasick
  ↓
C

RE2
  ↓
C++
```

所以没必要自己维护 C 扩展。

Hyperscan 虽然非常适合一次扫描大量正则，它可以同时匹配上万条表达式。([GitHub][7])

但目前 Windows 支持历史上一直偏 experimental，所以你这个 Windows 优先项目，我不会把它作为第一选择。([GitHub][8])

推荐：

```text
固定关键词
    ↓
Aho-Corasick

规则
    ↓
RE2

NER
    ↓
可选

文本构建
    ↓
Python 单次线性 append
```

理论上规则模式核心流程基本都是：

```text
O(n)
```

或：

```text
O(n + k log k)
```

其中 k 为候选实体数量。

---

# 十七、不要不断修改字符串

错误方案：

```python
text = text.replace(...)
text = text.replace(...)
text = text.replace(...)
...
```

正确方案：

找到最终 Span：

```text
[100:102 PERSON]
[300:320 ORG]
[800:818 ID]
```

然后只扫描文本一次：

```text
0 → 100       append原文
100 → 102     append Token
102 → 300     append原文
300 → 320     append Token
...
```

复杂度：

```text
O(n)
```

并且 offset 永远不会乱。

---

# 十八、建议目录

我会把项目整理成：

```text
desensitize/
│
├─ config/
│  └─ default.yaml
│
├─ rules/
│  ├─ surnames.txt
│  ├─ org_suffixes.txt
│  ├─ companies.txt
│  └─ departments.txt
│
├─ src/
│  ├─ normalizer/
│  │  ├─ pipeline.py
│  │  ├─ unicode.py
│  │  ├─ paragraphs.py
│  │  ├─ whitespace.py
│  │  └─ structured_repair.py
│  │
│  ├─ recognizers/
│  │  ├─ base.py
│  │  ├─ id_card.py
│  │  ├─ person.py
│  │  ├─ organization.py
│  │  ├─ amount.py
│  │  ├─ number.py
│  │  ├─ dictionary.py
│  │  ├─ field.py
│  │  ├─ regex.py
│  │  ├─ alias.py
│  │  └─ ner.py
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
├─ tests/
│  ├─ test_normalizer.py
│  ├─ test_id_card.py
│  ├─ test_person.py
│  ├─ test_org.py
│  ├─ test_overlap.py
│  ├─ test_restore.py
│  └─ test_xxx_md.py
│
├─ benchmarks/
│  └─ benchmark.py
│
└─ xxx.md
```

---

# 十九、输出文件

对：

```text
xxx.md
```

执行一次之后生成：

```text
output/
├─ xxx.normalized.md
├─ xxx.masked.md
├─ xxx.mapping.enc
└─ xxx.report.json
```

其中 report 不保存完整敏感内容，只保存：

```json
{
  "PERSON": 21,
  "ORG": 13,
  "ID_CARD": 8,
  "AMOUNT": {
    "detected": 32,
    "masked": 0
  },
  "NUMBER": {
    "detected": 867,
    "masked": 0
  },
  "custom": 17,
  "processing_ms": 182
}
```

尤其注意：

> 日志里绝不能把检测到的身份证、人名等直接 `logger.debug()` 打出来。

否则脱敏程序自己的日志会成为泄密源。

---

# 二十、CLI 最终形态

最好最终非常简单：

```bash
desense xxx.md
```

得到脱敏文件。

恢复：

```bash
desense restore xxx.masked.md xxx.mapping.enc
```

检查识别情况：

```bash
desense inspect xxx.md
```

性能测试：

```bash
desense benchmark xxx.md
```

指定配置：

```bash
desense xxx.md -c myconfig.yaml
```

---

# 二十一、必须做的测试

我建议 `xxx.md` 不只是“跑一下”。

建立 Golden Test。

必须长期保证下面几个性质：

```text
normalize(normalize(x))
==
normalize(x)
```

以及最重要的：

```text
restore(anonymize(normalize(x)))
==
normalize(x)
```

另外专门测试：

```text
身份证中的数字不会被 NUMBER 抢占
金额不脱敏时 NUMBER 不会脱敏金额
公司名中的人名不会单独脱敏
同一个名字多次出现 Token 相同
不同名字 Token 不同
自定义字段正常工作
Token 本身出现在原文时不发生冲突
Mapping 被篡改后拒绝恢复
错误密码不能恢复
缺少 Token 映射时直接报错而不是静默跳过
```

---

# 二十二、实际开发顺序

不要一开始把全部功能都做出来。

我建议 Codex 按这个顺序推进：

**第一版核心：**

```text
Normalizer
↓
Span 数据结构
↓
身份证
↓
公司名
↓
自定义 Literal
↓
Aho-Corasick
↓
金额
↓
数字
↓
Resolver
↓
Token
↓
Mapping
↓
Restore
```

做到：

```text
restore(mask(normalize(xxx.md)))
==
normalize(xxx.md)
```

以后，再实现：

```text
人名上下文
↓
公司简称自动学习
↓
Field matcher
↓
自定义 Regex
↓
映射加密
↓
NER
↓
批量处理
```

最后 benchmark 后，**只有真的出现性能瓶颈才考虑自己写 C**。

目前看，固定词匹配已经可以直接利用 C 实现的 pyahocorasick；正则可以利用 C++ RE2。FlashText 也是类似的 Trie/Aho-Corasick 思路，项目本身就是为大规模关键词提取和替换设计的。([GitHub][9])

## 我最终推荐的技术路线

核心可以概括成：

```text
Python orchestration
        │
        ├── OCR-aware Normalizer
        │
        ├── Rule Recognizers
        │       └── RE2
        │
        ├── Dictionary Recognizer
        │       └── pyahocorasick / C
        │
        ├── Context Recognizer
        │
        ├── Optional NER
        │
        ├── Span Resolver
        │
        ├── Stable Typed Tokens
        │
        └── AES-GCM Mapping Vault
```

对你的场景，我认为最大的优先级依次不是“上更大的 AI 模型”，而是：

**OCR 规整正确 → 规则高精度 → 公司/自定义词典高速匹配 → Span 冲突机制 → 100% 可逆 → 最后才用 NER 补召回。**

这样最终会是一个非常适合审计资料、可完全离线运行、能安全把材料交给本地/外部 LLM 分析的脱敏前处理引擎。

[1]: https://microsoft.github.io/presidio/anonymizer/?utm_source=chatgpt.com "Presidio Anonymizer - Presidio"
[2]: https://github.com/WojciechMula/pyahocorasick?utm_source=chatgpt.com "GitHub - WojciechMula/pyahocorasick: Python module (C extension and plain python) implementing Aho-Corasick algorithm · GitHub"
[3]: https://github.com/hankcs/HanLP/wiki/README.zh-CN?utm_source=chatgpt.com "README.zh CN · hankcs/HanLP Wiki · GitHub"
[4]: https://github.com/hankcs/HanLP?utm_source=chatgpt.com "GitHub - hankcs/HanLP: 中文分词 词性标注 命名实体识别 依存句法分析 成分句法分析 语义依存分析 语义角色标注 指代消解 风格转换 语义相似度 新词发现 关键词短语提取 自动摘要 文本分类聚类 拼音简繁转换 自然语言处理 · GitHub"
[5]: https://github.com/google/re2/wiki/WhyRE2/ab922b9c078ad03192913e274be11f12f5b20136?utm_source=chatgpt.com "WhyRE2 · google/re2 Wiki · GitHub"
[6]: https://pypi.org/project/google-re2/1.1.20250805/?utm_source=chatgpt.com "google-re2 · PyPI"
[7]: https://github.com/intel/hyperscan/blob/master/README.md?utm_source=chatgpt.com "hyperscan/README.md at master · intel/hyperscan · GitHub"
[8]: https://github.com/VectorCamp/vectorscan/blob/develop/CHANGELOG.md?utm_source=chatgpt.com "vectorscan/CHANGELOG.md at develop · VectorCamp/vectorscan · GitHub"
[9]: https://github.com/vi3k6i5/flashtext?utm_source=chatgpt.com "GitHub - vi3k6i5/flashtext: Extract Keywords from sentence or Replace keywords in sentences. · GitHub"
