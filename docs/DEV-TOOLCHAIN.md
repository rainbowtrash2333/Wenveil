# 开发工具链（DEV-TOOLCHAIN）

> 版本：V0.4（2026-09-14）｜状态：生效
> 本文档记录 Wenveil（文隐）的安装、编译、测试、调试和 CLI 验收命令。

## 1. 环境要求

| 项 | 版本 | 说明 |
|----|------|------|
| Python | 3.10+ | 当前验证环境为 Python 3.13 |
| PyYAML | >=6.0 | 运行时依赖 |
| cryptography | >=41.0 | AES-GCM mapping |
| pytest | 当前环境 | 测试依赖；以项目环境实际安装版本为准 |
| Transformers/PyTorch | 可选 | Qwen 本地训练或 PyTorch 推理时需要；ONNX 部署不需要 |
| tokenizers/ONNX Runtime | 可选 | ONNX 部署与 CPU/DirectML provider 选择 |
| Node.js/npm | 20+ | 构建 `desktop/` React + Tauri 前端 |
| Rust/Cargo | stable | 检查 Tauri 原生壳；发布打包另需配置 Python Sidecar |

## 2. 安装与构建检查

```powershell
python -m pip install -e .
python -m compileall -q common desensitize ocr organize training
```

Python 包的可安装产物由 `pyproject.toml` 定义，构建目录不入库。桌面开发构建：

```powershell
cd desktop
npm install
npm run typecheck
npm run build
cargo check --manifest-path src-tauri/Cargo.toml
```

浏览器开发验收使用本地 HTTP 适配器（只监听 loopback），需要同时启动 Python 桥接和 Vite：

```powershell
python desktop/bridge/http_dev_server.py
npm --prefix desktop run dev:http -- --host 127.0.0.1 --port 5173
```

演示空壳只用于没有 Python 服务时检查页面交互：`npm --prefix desktop run dev:demo`。Tauri 发布采用目录分发：Python Sidecar 与模型目录由专用脚本组装，不生成单一 exe 安装包。
需要 OCR 转换时额外安装 `python -m pip install -e ".[ocr]"`；DirectML/CUDA 使用对应可选依赖。

完整离线模型目录发布（不生成单一 exe）：

```powershell
cd desktop
python bridge/build_release.py `
  --qwen-model D:\models\qwen3-1.7b-pii `
  --ocr-models D:\models\rapidocr
```

脚本会在构建前校验 Qwen 微调 checkpoint（`config.json` 与权重文件）和 RapidOCR
权重目录；任一缺失都会终止，不会生成标称“满血版”的残缺发布包。输出目录为
`desktop/release/Wenveil/`，其中模型分别位于 `models/qwen3-1.7b-pii/` 和
`models/ocr/`。程序启动时只从该目录读取模型，不联网下载。
`npm run tauri:build` 已指向这一完整模型发布流程；`tauri:build:dir` 仅保留为不带模型的开发基线构建。

## 3. 测试与 CLI 冒烟

```powershell
pytest -q
python -m desensitize --help
python -m ocr --help
python -m organize --help
```

使用安全测试夹具进行完整 CLI 验收时：

```powershell
$env:DESENSE_PASSWORD = "<local-only-password>"
python -m desensitize mask .\tests\fixtures\financial_desensitization_sample.md -o .\test-artifacts\desensitization-outputs
python -m desensitize audit .\test-artifacts\desensitization-outputs\document-<safe-id>.masked.md
python -m desensitize restore .\test-artifacts\desensitization-outputs\document-<safe-id>.masked.md .\test-artifacts\desensitization-outputs\document-<safe-id>.mapping.enc -o .\test-artifacts\desensitization-outputs\restored.md
```

仓库内的 `tests/fixtures/financial_desensitization_sample.md` 仅用于确定性回归；真实验收仍必须使用临时目录和授权输入，禁止把用户资料写入 Git。

三模块可单独调用，也可按文件契约串联：

```powershell
python -m ocr --config config/ocr.yaml --root-dir .\test-artifacts\ocr-inputs --no-progress
python -m organize .\test-artifacts\ocr-outputs -o .\test-artifacts\organized-outputs
python -m desensitize mask .\test-artifacts\organized-outputs\document-<safe-id>.organized.md --password $env:DESENSE_PASSWORD
```

## 4. 配置、规则与脱敏运行

- 脱敏默认配置：`config/default.yaml`；包内部署副本：`desensitize/config/default.yaml`。
- OCR 默认配置：`config/ocr.yaml`；OCR 模块代码位于 `ocr/`。
- 公共机构白名单：`rules/organization_whitelist.txt`；包内副本：`desensitize/rules/organization_whitelist.txt`。
- 常用运行方式：`python -m desensitize mask <input.md> --password <local-only-password>`。
- 输出使用 `document-<safe-id>.<kind>`，输出目录默认是 `test-artifacts/desensitization-outputs/`，该目录已忽略；授权原始输入统一放在 `test-artifacts/desensitization-inputs/`。

## 5. 调试与失败排查

1. 先执行 `python -m compileall -q common desensitize ocr organize training`，排除语法和导入问题。
2. 再执行与改动相关的 pytest 文件，最后执行 `pytest -q`。
3. CLI 失败时只保留退出码、类别、行号、哈希和固定摘要；原文、映射明文和密码不得写日志。
4. 测试日志、审计报告和截图写入 `test-artifacts/`，该目录不入库；桌面浏览器验收脚本也只允许写入该目录。
6. OCR 原始输入/输出分别使用 `test-artifacts/ocr-inputs/`、`test-artifacts/ocr-outputs/`；整理输出使用
   `test-artifacts/organized-outputs/`；这些目录均不入库。
7. 恢复失败先检查 mapping 密码、masked 文件是否被改动以及 mapping 中绑定的哈希，不绕过校验。

## 6. 训练入口

授权外部数据保持在仓库外，通过相对或用户指定的 `--data-dir` 只读传入；默认目录为
`data`，不会把数据复制到仓库。固定 split 的职责是：train 训练、dev Trainer validation
及 best checkpoint、test/hard_test 训练后独立 exact-span 评估。关系只做 adapter 结构校验，
不属于 NER BIO 标签。先运行安全摘要校验：

```powershell
python scripts/validate_dataset.py --data-dir .\data --split all
python -m training.train_token_classifier --data-dir .\data --model models\qwen-base --output-dir training-output\qwen-external
python -m training.evaluate --data-dir .\data --split all --model training-output\qwen-external
```

校验和训练元数据只输出 split、数量、标签/关系类型计数和 SHA-256 等安全统计；不记录原文、
`clean_text`、真实 ID 或绝对路径。`scripts/validate_dataset.py` 不生成 JSONL 副本。
旧的 `--input`、`--train`、`--validation`、`--prepare-dir` 入口继续用于合成/兼容流程。

```powershell
python -m training.generate_financial_alias_data --output-dir training/generated/financial_alias --samples 600
python -m training.train_token_classifier --input training/generated/financial_alias/financial_alias_ner.jsonl --validate-only
```

实际 Qwen 训练需要本地模型、PyTorch 和 Transformers；`--allow-network` 才允许模型组件访问网络。

Qwen3.5 的本地训练、独立评估和 ONNX 导出：

```powershell
python -m pip install -e ".[model]"
python -m training.download_qwen --repository Qwen/Qwen3.5-0.8B --output-dir models/qwen3.5-0.8b-base
python -m training.train_token_classifier --input training/generated/financial_alias/financial_alias_ner.jsonl --prepare-dir training-data/splits --seed 42
python -m training.train_token_classifier --train training-data/splits/train.jsonl --validation training-data/splits/validation.jsonl --model models/qwen3.5-0.8b-base --output-dir training-output/qwen3.5-0.8b-ner --seed 42
python -m training.evaluate --input training-data/splits/test.jsonl --model training-output/qwen3.5-0.8b-ner --output test-artifacts/qwen35-test-metrics.json
python -m training.export_onnx --checkpoint training-output/qwen3.5-0.8b-ner --output-dir models/qwen3.5-0.8b-ner-onnx
```

ONNX 运行时从部署目录读取 `model.onnx`、external-data 文件、`tokenizer.json`、标签映射和运行配置；Windows `auto` 先尝试 `DmlExecutionProvider`，不可用时回退 CPU。当前导出图为固定序列长度，导出器会在 manifest 中记录 PyTorch/ORT parity；正式上线前仍需用授权语料完成质量门槛。
