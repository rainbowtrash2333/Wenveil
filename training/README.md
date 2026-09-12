# Training scaffold

本目录提供领域 NER 的离线数据准备和可选 Qwen Token Classification 训练入口。
它不修改 `desensitize/` 核心，也不要求安装 PyTorch、Transformers 或下载模型才能完成
标签验证、Span 转样本、OCR 增强、JSONL 读写和文档级切分。

## 从规则 Span 生成样本

```python
from desensitize.models import Span
from training.samples import sample_from_spans

sample = sample_from_spans(
    "张三负责中国人保项目",
    [
        Span(0, 2, "PERSON", "张三"),
        Span(4, 8, "ORG", "中国人保"),
    ],
    document_id="contract-001",
)
```

也可以直接传入现有脱敏结果对象；脚手架会使用其
`normalized_text` 和 `accepted_spans`，不会重新改写文本。

## 无模型权重的离线流程

```python
from training.build_dataset import write_jsonl
from training.train_token_classifier import prepare_document_splits

write_jsonl("training-data/all.jsonl", [sample])
prepare_document_splits(
    [sample],
    output_dir="training-data/splits",
    augmentation_copies=1,
)
```

或使用命令行只做验证：

```powershell
python -m training.train_token_classifier `
  --input training-data/all.jsonl `
  --validate-only
```

`read_jsonl`、`write_jsonl` 和所有标签转换都会检查：去掉标签后的文本必须与原文一致。
切分按源文档分组；chunk 和 OCR 增强副本不会跨 train/validation/test 泄漏。

## 金融领域简称与主体关系样本

可用内置生成器创建不含真实机构名称的金融领域合成样本：

```powershell
python -m training.generate_financial_alias_data `
  --output-dir training/generated/financial_alias `
  --samples 600
python -m training.train_token_classifier `
  --input training/generated/financial_alias/financial_alias_ner.jsonl `
  --validate-only
```

生成目录同时包含实体识别 JSONL、主体链接 JSONL 和带哈希的 manifest。识别任务学习 `ORG_FULL`、`ORG_ALIAS`、`ORG_SUBSIDIARY`、`ORG_BRANCH` 等 span；链接任务学习 `ALIAS_OF`、`SUBSIDIARY_OF`、`BRANCH_OF` 关系。合成数据用于流程和标签验证，不能替代授权真实语料的独立评估。

## 可选训练

实际训练时才需要本地安装 `torch` 和 `transformers`，并提供本地 Qwen checkpoint：

```powershell
python -m training.train_token_classifier `
  --train training-data/splits/train.jsonl `
  --validation training-data/splits/validation.jsonl `
  --model models/qwen3-1.7b-pii `
  --output-dir training-output/qwen-ner
```

默认 `--model` 只查本地文件；使用 `--allow-network` 才允许 Transformers 查找网络/缓存。
模型只产生 token 标签，原文替换仍由现有脱敏核心负责。
