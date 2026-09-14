# Wenveil desktop UI

这是与现有 Python 包隔离的 React + TypeScript + Vite UI。界面只通过 `src/bridge.ts` 的桥接接口调用处理能力。

## 启动

```powershell
cd desktop
npm install
npm run dev:demo
```

`dev:demo` 是明确标记的浏览器演示适配器，只用于检查交互状态，不会声称连接 Python。Tauri 宿主会使用同一桥接接口调用已注册的 Rust `sidecar_request` 命令；生产发布采用目录分发，由 PyInstaller onedir sidecar 与模型目录一起组装，不生成单一 exe 安装包。

需要在浏览器中实际调用本仓库 Python 模块时，开两个终端：

```powershell
python desktop/bridge/http_dev_server.py
cd desktop
npm run dev:http
```

`http_dev_server.py` 仅监听 `127.0.0.1:8765`，浏览器选择的文件会通过本地 HTTP 发送到临时目录，再交给 `desktop/bridge/sidecar.py`。不会记录请求体、原文、密码或映射内容。

Tauri 原生开发：

```powershell
npm run tauri:dev
```

## JSON Lines sidecar

```powershell
python desktop/bridge/sidecar.py
```

sidecar 从 stdin 读取一行 JSON，向 stdout 返回 `progress`、`result` 或 `error` 事件。`process` 使用现有 OCR、organize、desensitize 和 audit 模块；`restore` 复用加密映射的完整性校验。桌面宿主只需把文件选择器得到的本地绝对路径放进 `files[].path`，并把这些事件转发给前端。

## 验证

```powershell
npm run typecheck
npm run build
npm test        # 当前等同 npm run typecheck，尚无 JS 测试运行器
```
