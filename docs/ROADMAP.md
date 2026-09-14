# 路线图（ROADMAP）

> 版本：V0.4（2026-09-14）｜状态：生效
> 本文档定义 Wenveil（文隐）的阶段目标与里程碑；当前能力清单见
> [APP-VERSION.md](./APP-VERSION.md)。

## 1. 阶段总览

| 阶段 | 目标 | 状态 | 依赖 |
|------|------|------|------|
| 阶段 1 | 建立确定性规整、识别、解析、可逆替换和审计核心 | 已完成 | — |
| 阶段 2 | 完成机构关系、公共白名单、短 Token、安全文件名与三文档验收 | 已完成 | 阶段 1 |
| 阶段 3 | 用授权金融语料微调/蒸馏 Qwen 小模型并接入候选识别 | 进行中 | 阶段 1、阶段 2 |
| 阶段 4 | 批量处理、性能基线、模型导出和部署运维 | 进行中 | 阶段 3 |

## 2. 各阶段里程碑

### 阶段 1：确定性可逆核心（已完成）

- [x] Normalizer、规则/词典识别器、统一 Resolver 和一次性替换。
- [x] AES-GCM mapping、masked 哈希绑定、密码错误/篡改/Token 缺失失败。
- [x] Markdown 结构与残留 PII 审计。

### 阶段 2：金融主体关系与交付安全（已完成）

- [x] 机构全称、简称、子公司/分公司关系注册表和 compact-v1 Token。
- [x] 公共机构精确白名单及“白名单子串不放行更长私有机构”边界。
- [x] 文件名脱敏、三份真实输入的 masked/audit/restore 回归。
- [x] 项目级 skill、文档索引、ADR、Git 忽略与 `dev` 分支治理。

### 阶段 3：小模型增强（进行中）

- [x] 用合成数据建立文档级严格切分、滑窗标签和独立 benchmark；增加固定外部 train/dev/test/hard_test 适配与安全校验。
- [x] 完成 Qwen3.5 Token Classification 微调入口、best/resume 和独立 exact-span 评估；授权语料上的 PERSON/ORG/PROJECT/关系召回仍待验证。
- [ ] 仅对歧义机构候选启用轻量 linking head/adapter；规则和白名单仍为最终安全边界。
- [ ] 以残留率、误脱敏率、别名链接准确率和恢复一致性设定上线门槛。

### 阶段 4：批量与部署（进行中）

- [x] 移植独立 OCR 转换模块，提供 `ocr` CLI 和可选重依赖。
- [x] 提取独立 OCR 文本整理模块，提供 `organize` CLI；三个功能模块可单独调用。
- [x] 建立 Tauri + React 桌面开发 UI，覆盖处理、恢复、进度、结果和设置；通过 JSON Lines Sidecar 复用 Python 模块。
- [ ] 完成 Python Sidecar 的离线发布捆绑、安装包签名和升级/回滚演练。
- [ ] 批量目录处理、任务级安全 ID 和可观测计数。
- [ ] 建立不同文档规模的吞吐/内存基线，评估 ONNX 或量化部署。
- [x] 完成 Qwen3.5 ONNX 导出、PyTorch/ORT parity 和 DirectML 优先/CPU 回退适配；固定长度图的性能基线仍待建立。
- [ ] 增加离线交付包、配置版本锁定和回滚演练。

## 3. 决策记录

| ADR | 主题 | 状态 |
|-----|------|------|
| [ADR-0001](./adr/0001-hybrid-reversible-desensitization.md) | 混合式确定性可逆脱敏、加密映射与白名单边界 | 已接受 |
| [ADR-0002](./adr/0002-independent-processing-modules.md) | OCR、文本整理与脱敏模块独立化 | 已接受 |
| [ADR-0003](./adr/0003-tauri-desktop-ui.md) | Tauri 桌面端与 Python Sidecar | 已接受 |
| [ADR-0004](./adr/0004-qwen35-token-classification-onnx.md) | Qwen3.5 Token Classification 与 ONNX 离线部署 | 已接受 |

> 新增重大技术路线时，在 `docs/adr/` 建立递增编号的 ADR，并在本表登记。

## 4. 进度记录

| 日期 | 阶段 | 完成内容 |
|------|------|----------|
| 2026-09-12 | 阶段 1–2 | 完成核心脱敏、机构关系、公共白名单、三文档验收和项目治理整理 |
| 2026-09-12 | 阶段 3 | 保留本地 Qwen 数据/训练脚手架，等待授权语料和模型训练环境 |
| 2026-09-12 | 阶段 4 | 接入独立 OCR 转换和 OCR 文本整理模块，建立三模块文件契约 |
| 2026-09-12 | 阶段 4 | 建立 Tauri + React 桌面开发 UI、JSON Lines Sidecar 和浏览器 HTTP 验收适配器；发布捆绑待后续 |
| 2026-09-14 | 阶段 3–4 | 完成 Qwen3.5 本地下载、正式合成训练/best、独立 test 与 subset 指标、ONNX parity、CPU 回退及现有 Resolver/Mapping/restore 实测；授权语料与性能基线待后续 |
