# GitHub 公开发布清单

> 版本：V0.1（2026-09-12）｜状态：生效
> 适用项目：Wenveil（文隐）；Python 发行名：`wenveil`

## 发布边界

允许提交：

- 生产代码、通用配置、公共机构白名单和经过评审的通用规则；
- 不含真实业务内容的合成测试夹具；
- 架构、开发、贡献、安全和 skill 文档。

禁止提交：

- 原始文档、OCR 文本、脱敏文本、恢复文本和任何能识别客户/交易/项目的文件名；
- mapping、密码、密钥、模型权重、日志、截图、崩溃堆栈和本地绝对路径；
- 客户或项目专属的机构关系、项目词典和自定义配置。

## 发布前检查

1. 检查当前树：

   ```powershell
   git status --short
   git ls-files
   rg -n --hidden --glob '!test-artifacts/**' --glob '!.git/**' 'mapping|password|secret|private.?key'
   ```

2. 检查历史：

   ```powershell
   git log --all --name-only --format=
   git log --all -S '<private-surface>' --oneline
   ```

   `<private-surface>` 只在本地审计命令中替换，不能写入公开文档、提交信息或审计报告。

3. 检查包和命令：

   ```powershell
   python -m pip install -e .
   python -m compileall -q common desensitize ocr organize training
   pytest -q
   python -m ocr --help
   python -m organize --help
   python -m desensitize --help
   ```

4. 检查规则：`rules/projects.txt` 和包内副本必须保持公共空模板；项目专属规则通过本地配置加载，
   不修改并提交公共模板。

5. 检查许可证：根目录存在 `LICENSE`，`pyproject.toml` 声明 Apache-2.0，README 提供许可证入口；
   可选 OCR/模型依赖的许可证需单独核验。

6. 首次推送前检查全部可达历史。若发现敏感内容，先清理历史并删除本地备份引用，再创建远端；
   仅删除最新文件不足以阻止 Git 历史恢复。

## 事故处理

如果敏感内容已经推送，先暂停继续发布，撤销或轮换相关凭据，保存只含路径/提交号/哈希的证据，
再通过私下渠道处理历史清理和访问控制。不要在 issue、PR、日志或最终回复中复制原文。
