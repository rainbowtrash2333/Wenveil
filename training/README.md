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

## Qwen3.5 本地训练与部署链路

### 授权外部固定切分

授权 JSONL 不需要复制到仓库，也不经过合成数据的随机切分。适配器只把每条记录的
`text` 及 `start/end/entity_type/mention` 转成 NER `TrainingSample`；`clean_text` 不是
canonical text，`financial_subtype`、`industry_subtype`、实体 ID 和 relations 不进入 BIO
标签。关系只做结构校验和安全计数，允许 target 指向当前记录之外的实体。

四个固定文件的职责如下：`train.jsonl` 只用于训练，`dev.jsonl` 只用于 Trainer validation
和 best checkpoint，`test.jsonl` 与 `hard_test.jsonl` 只在训练完成后由独立 exact-span
评估读取。四个 split 的 document ID 必须互不交叉。

先做只读安全校验（摘要仅包含 split、数量、标签/关系类型计数和 SHA-256）：

```powershell
python scripts/validate_dataset.py --data-dir .\data --split all
```

真实数据目录只通过 `--data-dir` 传入，默认值为相对当前目录的 `data`，不写入或复制数据：

```powershell
python -m training.train_token_classifier --data-dir .\data --model models\qwen3.5-0.8b-base --output-dir training-output\qwen-external
python -m training.evaluate --data-dir .\data --split test --model training-output\qwen-external
python -m training.evaluate --data-dir .\data --split hard_test --model training-output\qwen-external
python -m training.evaluate --data-dir .\data --split all --model training-output\qwen-external
```

外部模式先校验全部四个 fixed split 及 document-id 隔离，但明确只把 train/dev 传给 Trainer；
不会把 test/hard_test 纳入标签、模型选择或 checkpoint 决策，也不会调用
`prepare_document_splits`、随机切分或 OCR 增强。
训练元数据只记录安全来源类型、split、文件哈希、行数和标签计数，不记录绝对路径、原文、
`clean_text`、relations 或真实 ID。旧的 `--input`、`--train`、`--validation` 和
`--prepare-dir` 合成/兼容流程仍保留。

当前默认基座是官方 `Qwen/Qwen3.5-0.8B`。模型权重不入库，先显式下载到本地：

```powershell
python -m pip install -e ".[model]"
python -m training.download_qwen `
  --repository Qwen/Qwen3.5-0.8B `
  --output-dir models/qwen3.5-0.8b-base
```

下载脚本会检查 `config.json`、fast tokenizer 和 safetensors 权重，并写入被忽略的
`download_manifest.json`。训练和评估默认使用 `local_files_only=true`；断网时不会回退到
Hub 或缓存中的其他模型。

已有下载目录可用 `python -m training.download_qwen --output-dir models/qwen3.5-0.8b-base --verify-only`
离线复核，不会访问网络。

先按文档 ID 切分，再训练。滑窗使用 tokenizer 的 overflow offsets；窗口边缘只覆盖实体
一部分时，该部分 token 设为 `-100`，直到某个窗口完整覆盖实体才生成 BIO/BIOES/BILOU 监督：

```powershell
python -m training.train_token_classifier `
  --input training/generated/financial_alias/financial_alias_ner.jsonl `
  --prepare-dir training-data/splits `
  --augmentation-copies 1 `
  --seed 42

python -m training.train_token_classifier `
  --train training-data/splits/train.jsonl `
  --validation training-data/splits/validation.jsonl `
  --model models/qwen3.5-0.8b-base `
  --output-dir training-output/qwen3.5-0.8b-ner `
  --max-length 1024 `
  --overlap 128 `
  --seed 42
```

训练会保存 tokenizer、label mapping、配置、checkpoint，并以 validation loss 保留
`best_model_checkpoint`；可用 `--resume-from-checkpoint` 继续训练。当前 Transformers 未
提供原生 `Qwen3_5ForTokenClassification` 时，代码使用 Qwen3.5 文本骨干加 PyTorch
Linear token head，模型只输出候选标签，不改写文本。

独立评估 test 集（不调用 Trainer 的训练指标）：

```powershell
python -m training.evaluate `
  --input training-data/splits/test.jsonl `
  --model training-output/qwen3.5-0.8b-ner `
  --output test-artifacts/qwen3.5-test-metrics.json
```

报告包含 overall/per-entity Precision、Recall、F1、漏识别率和误识别率；带有
`evaluation_group` 元数据的未见实体、未见别名、相似名称、OCR 噪声、长文本、无实体和
hard-negative 子集会分别统计。仓库内合成数据只能验证流程，不能代替授权真实语料。

可用内置安全 benchmark 验证这些子集是否都进入报告：

```powershell
python -m training.benchmark --output training-data/qwen35-independent-benchmark.jsonl
python -m training.evaluate `
  --input training-data/qwen35-independent-benchmark.jsonl `
  --model training-output/qwen3.5-0.8b-ner `
  --output test-artifacts/qwen35-independent-metrics.json
```

将 best checkpoint 导出为 ONNX 并做正常、长文本、OCR 噪声和多实体 parity：

```powershell
python -m training.export_onnx `
  --checkpoint training-output/qwen3.5-0.8b-ner `
  --output-dir models/qwen3.5-0.8b-ner-onnx
```

`models/qwen3.5-0.8b-ner-onnx/` 只需交付 `model.onnx`（若导出器生成 external-data
伴随文件也一并交付）、tokenizer、`label_mapping.json` 和运行配置。脱敏配置将
`model.backend` 设为 `onnx`、`model.path` 指向该目录即可；Windows 自动优先尝试
`DmlExecutionProvider`，不可用时回退 `CPUExecutionProvider`。运行时不加载 PyTorch、
训练代码或 checkpoint optimizer 状态，ONNX 结果仍进入现有 Span Resolver、mapping 和
audit 流程。

## 无模型权重的离线流程

模型只产生 token 标签，原文替换仍由现有脱敏核心负责。
