# 架构决策记录（ADR）

> 版本：V0.3（2026-09-12）｜状态：生效

本目录存放 AICanRead 的重大技术决策记录。**技术路线变更必须先在此记录讨论，
再实施**（见 [../DOCUMENTATION-GUIDE.md](../DOCUMENTATION-GUIDE.md) §5.4）。

## 命名与格式

- 文件名：`NNNN-kebab-case-title.md`，编号从 `0001` 递增，不重复、不复用。
- ADR **只增不改**：决策被推翻时，新增一份 ADR 并在旧的那份把状态标为 `Superseded by NNNN`。

## 模板

```markdown
# ADR-NNNN：<决策标题>

> 状态：提议中 / 已接受 / 已废弃 / 被 ADR-NNNN 取代
> 日期：YYYY-MM-DD
> 关联：<相关文档链接>

## 背景
<面临的问题、约束、触发因素。>

## 决策
<最终选择做什么。>

## 理由
<为什么选它，而非备选；列出关键权衡。>

## 影响
<正面/负面影响；需要同步的文档与代码；回滚方式。>

## 备选方案
<考虑过但未采用的方案及原因。>
```

## 索引

| ADR | 主题 | 状态 |
|-----|------|------|
| [ADR-0001](./0001-hybrid-reversible-desensitization.md) | 混合式确定性可逆脱敏、加密映射与公共机构白名单边界 | 已接受 |
| [ADR-0002](./0002-independent-processing-modules.md) | OCR、文本整理与脱敏模块独立化 | 已接受 |
