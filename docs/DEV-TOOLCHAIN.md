# 开发工具链（DEV-TOOLCHAIN）

> 版本：V0.3（2026-09-12）｜状态：生效
> 本文档记录 AICanRead 的安装、编译、测试、调试和 CLI 验收命令。

## 1. 环境要求

| 项 | 版本 | 说明 |
|----|------|------|
| Python | 3.10+ | 当前验证环境为 Python 3.13 |
| PyYAML | >=6.0 | 运行时依赖 |
| cryptography | >=41.0 | AES-GCM mapping |
| pytest | 当前环境 | 测试依赖；以项目环境实际安装版本为准 |
| Transformers/PyTorch | 可选 | 只有本地 Qwen 训练或推理时需要 |

## 2. 安装与构建检查

```powershell
python -m pip install -e .
python -m compileall -q common desensitize ocr organize training
```

项目为 Python 包，没有前端构建步骤；可安装包产物由 `pyproject.toml` 定义，构建目录不入库。
需要 OCR 转换时额外安装 `python -m pip install -e ".[ocr]"`；DirectML/CUDA 使用对应可选依赖。

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
python -m desensitize mask .\tests\fixtures\sample.md -o .\test-artifacts\desensitization-outputs
python -m desensitize audit .\test-artifacts\desensitization-outputs\document-<safe-id>.masked.md
python -m desensitize restore .\test-artifacts\desensitization-outputs\document-<safe-id>.masked.md .\test-artifacts\desensitization-outputs\document-<safe-id>.mapping.enc -o .\test-artifacts\desensitization-outputs\restored.md
```

仓库当前没有 `tests/fixtures/` 固定夹具；真实验收必须使用临时目录和授权输入，禁止把用户资料写入 Git。

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
4. 测试日志、审计报告和截图写入 `test-artifacts/`，该目录不入库。
6. OCR 原始输入/输出分别使用 `test-artifacts/ocr-inputs/`、`test-artifacts/ocr-outputs/`；整理输出使用
   `test-artifacts/organized-outputs/`；这些目录均不入库。
7. 恢复失败先检查 mapping 密码、masked 文件是否被改动以及 mapping 中绑定的哈希，不绕过校验。

## 6. 训练入口

```powershell
python -m training.generate_financial_alias_data --output-dir training/generated/financial_alias --samples 600
python -m training.train_token_classifier --input training/generated/financial_alias/financial_alias_ner.jsonl --validate-only
```

实际 Qwen 训练需要本地模型、PyTorch 和 Transformers；`--allow-network` 才允许模型组件访问网络。
