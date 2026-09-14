# ADR-0004：Qwen3.5 Token Classification 与 ONNX 离线部署

> 版本：V0.1（2026-09-14）｜状态：已接受

## 背景

项目需要把语义实体识别接入现有 `Span → Resolver → Mapping` 脱敏链路，并在
Windows 上支持不携带 PyTorch 的 ONNX Runtime 推理。实施时实际检查当前环境的
Transformers 版本：`transformers 5.8.0` 没有暴露 `Qwen3_5ForTokenClassification`，但
能够加载 `Qwen3.5` 的文本骨干 `Qwen3_5Model`。

## 决策

1. 基座采用官方 `Qwen/Qwen3.5-0.8B`，下载到本地 `models/` 目录；训练、评估和
   运行时默认 `local_files_only=true`。
2. 训练代码优先探测 Transformers 原生 `Qwen3_5ForTokenClassification`。当前版本
   走最小兼容实现：`AutoModel` 文本骨干加 PyTorch `Linear` token head，输出 BIO/BIOES/BILOU
   标签，不生成文本。
3. 兼容 checkpoint 自包含保存 Qwen3.5 配置、骨干与 token head 权重、tokenizer 和
   label mapping，保留 `best_model_checkpoint`，支持 Trainer 断点恢复。
4. 导出使用 `torch.onnx.export`，部署使用 ONNX Runtime；Windows 优先尝试
   `DmlExecutionProvider`，不可用时使用 `CPUExecutionProvider`。部署目录只包含
   ONNX 图、tokenizer、label mapping 和必要配置。
5. 模型候选仍由现有 `ModelNERRecognizer`/`OnnxNERRecognizer` 转换为 `Span`，不修改
   原文，不绕过白名单、Resolver、Mapping、Audit 或恢复哈希校验。

## 取舍与限制

- 当前 Qwen3.5 checkpoint 是多模态基础模型；实现只调用文本骨干，视觉权重不参与
  NER。模型体积和 ONNX 导出时间较小模型更高。
- token overflow 使用滑窗；窗口边缘只覆盖实体一部分的 token 使用 `-100` 忽略，
  只有完整覆盖窗口才承担实体标签，避免截断产生错误负样本。
- Qwen3.5 原生 token-classification 类在未来 Transformers 版本出现时，代码可直接
  切换到原生类；自定义 fallback 仍用于兼容已生成 checkpoint。

## 验证

验收要求包括本地断网加载、loss 更新、独立 test exact-span 指标、best checkpoint
重载、PyTorch/ONNX logits 与 token label parity、DirectML/CPU provider 实测以及
接入现有 Resolver 的完整回归。实际数值只记录在不含原文的本地测试产物或版本说明中。
