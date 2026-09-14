# ADR-0003：Tauri 桌面端与 Python Sidecar

> 版本：V0.2（2026-09-14）｜状态：已接受

## 背景

Wenveil 已有可独立调用的 Python OCR、文本整理和可逆脱敏模块，但普通用户需要一个离线桌面入口来选择文件、组合处理步骤、查看进度，并执行恢复流程。`docs/UI/UI设计方案.md` 明确要求 Tauri 2、React、TypeScript 和 Python Sidecar。

## 决策

新增 `desktop/` 作为桌面端工程：

- React + TypeScript + Vite 负责页面状态、文件选择、步骤设置、进度和结果展示。
- Tauri 2 负责桌面窗口、原生文件对话框、文件拖放以及 Sidecar 生命周期。
- Python Sidecar 使用 JSON Lines 的 stdin/stdout 协议，把一次处理或恢复请求编排到现有 Python 模块；识别、规整、解析、映射和审计仍由原有模块负责。
- 浏览器开发模式使用本地 Python HTTP 适配器调用同一 Sidecar 服务入口，便于 Playwright 验收；该适配器只接受本机请求，不改变生产数据流。
- UI 不读取或显示原始敏感 surface、映射明文、密码或内部堆栈；用户可见错误只展示固定的可理解摘要。

## 影响

- Python 三模块仍保持独立，桌面端只依赖其公开 CLI/编排接口，不把业务识别逻辑复制到前端。
- 安装和构建增加 Node、Rust/Tauri 以及 Python Sidecar 打包步骤；当前已支持 PyInstaller onedir
  sidecar 与模型目录组装为 Windows 目录分发，基础 Python CLI 不受影响。
- 文档需明确桌面端当前为开发版，并同步记录其 Sidecar、浏览器适配器与原生桥接边界。
- 桌面端测试分为前端类型/组件检查、浏览器开发模式用户流程和 Python 既有全量回归；不把浏览器 mock 流程当作生产 Sidecar 验收。

## 现状与未决事项

- PyInstaller onedir 与模型目录的目录分发已具备；Tauri bundle/NSIS 仍未启用。
- 最终发布签名、升级/回滚演练和安装包矩阵留给后续交付里程碑；开发模式仍可使用仓库内 Python Sidecar。
