import { ChangeEvent, DragEvent, FormEvent, useMemo, useRef, useState } from "react";
import { getBridge, openDirectory, openFile, pickDirectory, type ProgressEvent, type ProcessResult, type RestoreResult } from "./bridge";
import {
  DEFAULT_STEPS,
  ENTITY_OPTIONS,
  STEP_LABELS,
  type Page,
  type SelectedFile,
  type StepKey,
  type StepState,
  defaultStepsFor,
  extensionOf,
  formatBytes,
  mergeFiles,
  validatePassword,
} from "./domain";

type RunState = "idle" | "processing" | "success" | "error" | "cancelled";
type FileProgress = ProgressEvent & { step?: StepKey };

const initialEntities = new Set(["PERSON", "ORG", "ID_CARD", "PHONE", "BANK_ACCOUNT", "CUSTOM"]);

function Icon({ name }: { name: "file" | "folder" | "plus" | "trash" | "eye" | "eye-off" | "settings" | "restore" | "process" | "arrow" | "check" | "alert" | "close" }) {
  const paths: Record<string, string> = {
    file: "M6 2.8h7l4 4v14.4H6z M13 2.8v4h4",
    folder: "M3 7.5h7l1.7 2H21v10.8H3z M3 7.5V5.7h6l1.7 1.8",
    plus: "M12 5v14M5 12h14",
    trash: "M5 7h14M10 11v6M14 11v6M8 7V4h8v3m-10 0 1 13h10l1-13",
    eye: "M2.5 12s3.2-5 9.5-5 9.5 5 9.5 5-3.2 5-9.5 5-9.5-5-9.5-5Zm9.5 2.3a2.3 2.3 0 1 0 0-4.6 2.3 2.3 0 0 0 0 4.6Z",
    "eye-off": "m3 3 18 18M10.6 6.3A10.8 10.8 0 0 1 12 6c6.3 0 9.5 6 9.5 6a17 17 0 0 1-3 3.5M6.2 6.9C3.8 8.2 2.5 12 2.5 12s3.2 6 9.5 6c1.6 0 3-.4 4.2-1",
    settings: "M12 15.2a3.2 3.2 0 1 0 0-6.4 3.2 3.2 0 0 0 0 6.4ZM19.4 15a7.8 7.8 0 0 0 .1-1.5 7.8 7.8 0 0 0-.1-1.5l2-1.5-2-3.5-2.4 1a8.2 8.2 0 0 0-2.5-1.5L14.2 4H10l-.3 2.5a8.2 8.2 0 0 0-2.5 1.5l-2.4-1-2 3.5 2 1.5a7.8 7.8 0 0 0-.1 1.5 7.8 7.8 0 0 0 .1 1.5l-2 1.5 2 3.5 2.4-1a8.2 8.2 0 0 0 2.5 1.5L10 20h4l.3-2.5a8.2 8.2 0 0 0 2.5-1.5l2.4 1 2-3.5-1.8-1.5Z",
    restore: "M4 12a8 8 0 1 0 2.3-5.7M4 5v5h5",
    process: "M4 5h16M4 12h16M4 19h16",
    arrow: "M5 12h13m-5-5 5 5-5 5",
    check: "m5 12 4 4L19 6",
    alert: "M12 4 21 20H3L12 4Zm0 5v5m0 3h.01",
    close: "m6 6 12 12M18 6 6 18",
  };
  return <svg className="icon" viewBox="0 0 24 24" aria-hidden="true"><path d={paths[name]} /></svg>;
}

function Field({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) {
  return <label className="field"><span className="field-label">{label}</span>{children}{hint && <span className="field-hint">{hint}</span>}</label>;
}

function App() {
  const [page, setPage] = useState<Page>("process");
  const [files, setFiles] = useState<SelectedFile[]>([]);
  const [steps, setSteps] = useState<StepState>({ ...DEFAULT_STEPS });
  const [stepsTouched, setStepsTouched] = useState(false);
  const [entities, setEntities] = useState<Set<string>>(initialEntities);
  const [aiEnhanced, setAiEnhanced] = useState(true);
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [outputDir, setOutputDir] = useState("D:\\Wenveil\\Output");
  const [ocrMode, setOcrMode] = useState<"auto" | "fast" | "enhanced">("auto");
  const [device, setDevice] = useState<"auto" | "cpu" | "gpu">("auto");
  const [runState, setRunState] = useState<RunState>("idle");
  const [message, setMessage] = useState("");
  const [progress, setProgress] = useState<Record<string, FileProgress>>({});
  const [results, setResults] = useState<ProcessResult["files"]>([]);
  const [restoreMasked, setRestoreMasked] = useState("");
  const [restoreMapping, setRestoreMapping] = useState("");
  const [restoreMaskedSource, setRestoreMaskedSource] = useState<File | undefined>();
  const [restoreMappingSource, setRestoreMappingSource] = useState<File | undefined>();
  const [restorePassword, setRestorePassword] = useState("");
  const [restoreOutputDir, setRestoreOutputDir] = useState("D:\\Wenveil\\Output");
  const [restoreShowPassword, setRestoreShowPassword] = useState(false);
  const [restoreState, setRestoreState] = useState<RunState>("idle");
  const [restoreMessage, setRestoreMessage] = useState("");
  const [restoreResult, setRestoreResult] = useState<RestoreResult | null>(null);
  const [keepIntermediate, setKeepIntermediate] = useState(false);
  const [openOutput, setOpenOutput] = useState(true);
  const [theme, setTheme] = useState("system");
  const fileInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const restoreAbortRef = useRef<AbortController | null>(null);
  const restoreMaskedInput = useRef<HTMLInputElement>(null);
  const restoreMappingInput = useRef<HTMLInputElement>(null);
  const bridgeState = getBridge();

  const completedCount = results.filter((result) => result.status !== "error").length;
  const attentionCount = results.filter((result) => result.status === "attention").length;
  const overallProgress = useMemo(() => {
    if (!files.length) return 0;
    return Math.round((files.reduce((sum, file) => sum + (progress[file.id]?.progress ?? 0), 0) / files.length) * 100);
  }, [files, progress]);

  function makeSelectedFile(file: File): SelectedFile {
    const filePath = (file as File & { webkitRelativePath?: string }).webkitRelativePath || undefined;
    return {
      id: typeof crypto.randomUUID === "function" ? crypto.randomUUID().slice(0, 12) : `${file.name}-${file.size}`,
      name: filePath || file.name,
      path: filePath,
      size: file.size,
      extension: extensionOf(file.name),
      source: file,
    };
  }

  function addFiles(list: FileList | File[]) {
    const incoming = Array.from(list).map(makeSelectedFile);
    if (!incoming.length) return;
    setFiles((current) => {
      const merged = mergeFiles(current, incoming);
      if (!stepsTouched && current.length === 0) setSteps(defaultStepsFor(merged));
      return merged;
    });
    setRunState("idle");
    setMessage("");
  }

  function onInputChange(event: ChangeEvent<HTMLInputElement>) {
    if (event.target.files) addFiles(event.target.files);
    event.target.value = "";
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    event.currentTarget.classList.remove("is-dragging");
    if (event.dataTransfer.files.length) addFiles(event.dataTransfer.files);
  }

  function onRestoreFileChange(event: ChangeEvent<HTMLInputElement>, kind: "masked" | "mapping") {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (kind === "masked") {
      setRestoreMasked(file.name);
      setRestoreMaskedSource(file);
    } else {
      setRestoreMapping(file.name);
      setRestoreMappingSource(file);
    }
    setRestoreState("idle");
    setRestoreMessage("");
    setRestoreResult(null);
  }

  function updateRestoreMasked(value: string) {
    setRestoreMasked(value);
    setRestoreMaskedSource(undefined);
    setRestoreResult(null);
  }

  function updateRestoreMapping(value: string) {
    setRestoreMapping(value);
    setRestoreMappingSource(undefined);
    setRestoreResult(null);
  }

  async function chooseOutputDirectory(setter: (value: string) => void) {
    try {
      const selected = await pickDirectory();
      if (selected) setter(selected);
    } catch {
      setMessage("无法打开目录选择器，请直接输入输出位置。");
    }
  }

  async function chooseRestoreOutputDirectory() {
    try {
      const selected = await pickDirectory();
      if (selected) setRestoreOutputDir(selected);
    } catch {
      setRestoreMessage("无法打开目录选择器，请直接输入恢复位置。");
    }
  }

  async function openOutputFolder() {
    try {
      const opened = await openDirectory(outputDir);
      if (!opened) setPage("settings");
    } catch {
      setMessage("无法打开输出位置，请检查目录是否存在。");
    }
  }

  function toggleStep(step: StepKey) {
    setStepsTouched(true);
    setSteps((current) => ({ ...current, [step]: !current[step] }));
  }

  function clearFiles() {
    if (runState === "processing") return;
    setFiles([]);
    setProgress({});
    setResults([]);
    setSteps({ ...DEFAULT_STEPS });
    setStepsTouched(false);
    setRunState("idle");
    setMessage("");
  }

  function removeFile(fileId: string) {
    if (runState === "processing") return;
    setFiles((current) => current.filter((file) => file.id !== fileId));
    setProgress((current) => {
      const next = { ...current };
      delete next[fileId];
      return next;
    });
    setResults((current) => current.filter((result) => result.fileId !== fileId));
  }

  async function handleProcess() {
    if (runState === "processing") return;
    if (!files.length) {
      setRunState("error");
      setMessage("请先添加至少一个文档。");
      return;
    }
    if (!Object.values(steps).some(Boolean)) {
      setRunState("error");
      setMessage("请至少开启一个处理步骤。");
      return;
    }
    if (steps.mask) {
      const passwordError = validatePassword(password, confirmation);
      if (passwordError) {
        setRunState("error");
        setMessage(passwordError);
        return;
      }
    }
    if (!bridgeState.bridge) {
      setRunState("error");
      setMessage("尚未连接桌面端 Python 桥接。请使用 Tauri 宿主，或明确以 npm run dev:demo 启动浏览器演示。");
      return;
    }
    setRunState("processing");
    setMessage("");
    setResults([]);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const response = await bridgeState.bridge.process({ files, steps, password: steps.mask ? password : undefined, outputDir, entities: [...entities], aiEnhanced, ocrMode, device, keepIntermediate }, (event) => {
        setProgress((current) => ({ ...current, [event.fileId]: { ...current[event.fileId], ...event } }));
      }, controller.signal);
      setResults(response.files);
      const failedCount = response.files.filter((result) => result.status === "error").length;
      setRunState(response.files.length > 0 && failedCount === response.files.length ? "error" : "success");
      setMessage(failedCount ? `已完成 ${response.files.length - failedCount} 个文件，${failedCount} 个文件处理失败。` : `已处理 ${response.files.length} 个文件。`);
      if (openOutput && !failedCount) void openDirectory(outputDir).catch(() => undefined);
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        setRunState("cancelled");
        setMessage("处理已取消；已完成的输出可能已保留，请检查输出目录。");
      } else {
        setRunState("error");
        setMessage(error instanceof Error ? error.message : "处理失败，请检查输入文件和输出位置。");
      }
    } finally {
      abortRef.current = null;
    }
  }

  function cancelProcess() {
    if (runState === "processing") abortRef.current?.abort();
  }

  async function handleRestore(event: FormEvent) {
    event.preventDefault();
    if (restoreState === "processing") return;
    if (!restoreMasked || !restoreMapping || !restorePassword) {
      setRestoreState("error");
      setRestoreMessage("请选择脱敏文件、映射文件，并输入恢复密码。");
      setRestoreResult(null);
      return;
    }
    if (!bridgeState.bridge) {
      setRestoreState("error");
      setRestoreMessage("尚未连接桌面端 Python 桥接。请使用 Tauri 宿主，或明确以 npm run dev:demo 启动浏览器演示。");
      setRestoreResult(null);
      return;
    }
    setRestoreState("processing");
    setRestoreMessage("");
    const controller = new AbortController();
    restoreAbortRef.current = controller;
    try {
      const result = await bridgeState.bridge.restore({ maskedPath: restoreMasked, mappingPath: restoreMapping, password: restorePassword, outputDir: restoreOutputDir, maskedSource: restoreMaskedSource, mappingSource: restoreMappingSource }, controller.signal);
      setRestoreResult(result);
      setRestoreState("success");
      setRestoreMessage(`文件恢复完成：${result.outputName}`);
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        setRestoreState("cancelled");
        setRestoreMessage("恢复已取消。");
      } else {
        setRestoreState("error");
        setRestoreResult(null);
        setRestoreMessage(error instanceof Error ? error.message : "密码错误或文件完整性验证未通过。");
      }
    } finally {
      restoreAbortRef.current = null;
    }
  }

  async function openRestoreFile() {
    if (!restoreResult) return;
    const path = `${restoreOutputDir.replace(/[\\/]+$/, "")}/${restoreResult.outputName}`;
    try {
      if (!await openFile(path)) setRestoreMessage("浏览器开发模式无法直接打开本地文件，请从恢复目录查看输出。");
    } catch {
      setRestoreMessage("无法打开恢复文件，请从恢复目录查看输出。");
    }
  }

  async function openRestoreDirectory() {
    try {
      if (!await openDirectory(restoreOutputDir)) setRestoreMessage("浏览器开发模式无法直接打开目录，请手动查看恢复位置。");
    } catch {
      setRestoreMessage("无法打开恢复目录，请检查恢复位置是否存在。");
    }
  }

  return (
    <div className={`app-shell theme-${theme}`}>
      <aside className="sidebar">
        <div className="brand"><span className="brand-mark">文</span><div><strong>Wenveil</strong><span>文隐</span></div></div>
        <div className="sidebar-rule" />
        <nav className="nav" aria-label="主导航">
          <button className={page === "process" ? "nav-item active" : "nav-item"} onClick={() => setPage("process")}><Icon name="process" /><span>文档处理</span>{page === "process" && <i />}</button>
          <button className={page === "restore" ? "nav-item active" : "nav-item"} onClick={() => setPage("restore")}><Icon name="restore" /><span>脱敏恢复</span>{page === "restore" && <i />}</button>
          <button className={page === "settings" ? "nav-item active" : "nav-item"} onClick={() => setPage("settings")}><Icon name="settings" /><span>设置</span>{page === "settings" && <i />}</button>
        </nav>
        <div className="sidebar-footer"><span className={`connection-dot ${bridgeState.mode}`} />{bridgeState.mode === "native" ? "Python 已连接" : bridgeState.mode === "demo" ? "浏览器演示适配器" : "等待桌面桥接"}<small>本地离线处理</small></div>
      </aside>

      <main className="main-content">
        <header className="topbar"><span className="eyebrow">LOCAL DOCUMENT WORKBENCH</span><span className="topbar-status"><span className="status-led" />数据只在本机处理</span></header>
        {page === "process" && <ProcessPage {...{ files, steps, toggleStep, addFiles, onInputChange, onDrop, fileInput, folderInput, clearFiles, removeFile, password, setPassword, confirmation, setConfirmation, showPassword, setShowPassword, outputDir, setOutputDir, chooseOutputDirectory, ocrMode, setOcrMode, device, setDevice, entities, setEntities, aiEnhanced, setAiEnhanced, runState, message, progress, results, overallProgress, completedCount, attentionCount, handleProcess, cancelProcess, openOutputFolder, setPage, openOutput, setOpenOutput }} />}
        {page === "restore" && <RestorePage {...{ restoreMasked, setRestoreMasked: updateRestoreMasked, restoreMapping, setRestoreMapping: updateRestoreMapping, restorePassword, setRestorePassword, restoreOutputDir, setRestoreOutputDir, chooseRestoreOutputDirectory, restoreShowPassword, setRestoreShowPassword, restoreState, restoreMessage, restoreResult, openRestoreFile, openRestoreDirectory, handleRestore, restoreAbortRef, restoreMaskedInput, restoreMappingInput, onRestoreFileChange }} />}
        {page === "settings" && <SettingsPage {...{ outputDir, setOutputDir, chooseOutputDirectory, openOutput, setOpenOutput, keepIntermediate, setKeepIntermediate, ocrMode, setOcrMode, device, setDevice, entities, setEntities, aiEnhanced, setAiEnhanced, theme, setTheme }} />}
      </main>
    </div>
  );
}

type ProcessProps = {
  files: SelectedFile[]; steps: StepState; toggleStep: (step: StepKey) => void; addFiles: (list: FileList | File[]) => void; onInputChange: (event: ChangeEvent<HTMLInputElement>) => void; onDrop: (event: DragEvent<HTMLDivElement>) => void; fileInput: React.RefObject<HTMLInputElement | null>; folderInput: React.RefObject<HTMLInputElement | null>; clearFiles: () => void; removeFile: (fileId: string) => void; password: string; setPassword: (value: string) => void; confirmation: string; setConfirmation: (value: string) => void; showPassword: boolean; setShowPassword: (value: boolean) => void; outputDir: string; setOutputDir: (value: string) => void; chooseOutputDirectory: (setter: (value: string) => void) => void; ocrMode: "auto" | "fast" | "enhanced"; setOcrMode: (value: "auto" | "fast" | "enhanced") => void; device: "auto" | "cpu" | "gpu"; setDevice: (value: "auto" | "cpu" | "gpu") => void; entities: Set<string>; setEntities: (value: Set<string>) => void; aiEnhanced: boolean; setAiEnhanced: (value: boolean) => void; runState: RunState; message: string; progress: Record<string, FileProgress>; results: ProcessResult["files"]; overallProgress: number; completedCount: number; attentionCount: number; handleProcess: () => void; cancelProcess: () => void; openOutputFolder: () => void; setPage: (page: Page) => void; openOutput: boolean; setOpenOutput: (value: boolean) => void;
};

function ProcessPage(props: ProcessProps) {
  const hasMask = props.steps.mask;
  return <>
    <section className="page-heading"><div><span className="section-kicker">DOCUMENT PROCESSING</span><h1>文档处理</h1><p>把原始文档变成可阅读、可控、可恢复的工作副本。</p></div><div className="privacy-badge"><span className="shield">✦</span><div><strong>离线优先</strong><small>原文不离开本机</small></div></div></section>
    <section className="workspace-grid">
      <div className="primary-column">
        <div className={`drop-zone ${props.runState === "processing" ? "disabled" : ""}`} onDragOver={(event) => { event.preventDefault(); event.currentTarget.classList.add("is-dragging"); }} onDragLeave={(event) => event.currentTarget.classList.remove("is-dragging")} onDrop={props.onDrop}>
          <div className="drop-icon"><Icon name="file" /><span><Icon name="plus" /></span></div><h2>把文档放到这里</h2><p>支持 PDF、Word、图片、Markdown 和纯文本</p><div className="drop-actions"><button className="button primary" onClick={() => props.fileInput.current?.click()} disabled={props.runState === "processing"}><Icon name="plus" />选择文件</button><button className="button secondary" onClick={() => props.folderInput.current?.click()} disabled={props.runState === "processing"}><Icon name="folder" />选择文件夹</button></div><input ref={props.fileInput} type="file" multiple hidden onChange={props.onInputChange} /><input ref={props.folderInput} type="file" hidden multiple {...{ webkitdirectory: "" }} onChange={props.onInputChange} />
        </div>
        {props.files.length > 0 ? <div className="file-panel"><div className="panel-heading"><div><span className="section-kicker">QUEUE</span><h2>待处理文件 <em>{props.files.length}</em></h2></div><button className="text-button danger" onClick={props.clearFiles} disabled={props.runState === "processing"}><Icon name="trash" />清空</button></div><div className="file-list">{props.files.map((file) => { const item = props.progress[file.id]; const result = props.results.find((entry) => entry.fileId === file.id); return <div className="file-row" key={file.id}><div className="file-type"><Icon name="file" /><span>{file.extension.replace(".", "").toUpperCase() || "DOC"}</span></div><div className="file-name"><strong title={file.name}>{file.name}</strong><small>{formatBytes(file.size)}</small></div><div className="file-status">{result?.status === "attention" ? <><span className="status-icon attention"><Icon name="alert" /></span>建议检查</> : result?.status === "error" ? <><span className="status-icon error"><Icon name="alert" /></span>处理失败</> : result?.status === "done" ? <><span className="status-icon done"><Icon name="check" /></span>已完成</> : item?.status === "running" ? `${STEP_LABELS[item.step ?? "ocr"]} ${Math.round((item.progress ?? 0) * 100)}%` : item?.status === "queued" ? "等待中" : "待处理"}</div><button className="icon-button" aria-label={`移除 ${file.name}`} onClick={() => props.removeFile(file.id)} disabled={props.runState === "processing"}><Icon name="close" /></button></div>; })}</div></div> : <div className="empty-state"><div className="empty-dash" /><strong>还没有添加文档</strong><span>拖放文件，或从上方选择文件开始</span></div>}
        {props.runState === "processing" && <ProgressPanel {...props} />}
        {(props.runState === "success" || props.runState === "error" || props.runState === "cancelled") && <ResultPanel {...props} />}
      </div>
      <aside className="controls-column">
        <div className="control-card steps-card"><div className="panel-heading"><div><span className="section-kicker">AUTOMATION</span><h2>处理步骤</h2></div><span className="auto-pill">自动判断</span></div><p className="card-copy">根据文件类型预选步骤，你仍可以随时调整。</p><div className="step-list">{(Object.keys(STEP_LABELS) as StepKey[]).map((step, index) => <div className={`step-row ${props.steps[step] ? "enabled" : ""}`} key={step}><button className="check-toggle" aria-pressed={props.steps[step]} onClick={() => props.toggleStep(step)}><span>{props.steps[step] && <Icon name="check" />}</span></button><div><strong>{STEP_LABELS[step]}</strong><small>{step === "ocr" ? "从文档或图片中识别文字" : step === "organize" ? "整理空格、断行和 Markdown 结构" : step === "mask" ? "保护敏感信息并生成加密映射" : "检查可能残留的敏感信息"}</small></div><b>{String(index + 1).padStart(2, "0")}</b></div>)}</div>{props.steps.ocr && <details className="advanced-settings" open><summary><span>文字识别设置</span><span className="summary-arrow">⌄</span></summary><div className="inline-fields"><Field label="识别方式"><select value={props.ocrMode} onChange={(event) => props.setOcrMode(event.target.value as ProcessProps["ocrMode"])}><option value="auto">自动</option><option value="fast">快速识别</option><option value="enhanced">增强识别</option></select></Field><Field label="处理设备"><select value={props.device} onChange={(event) => props.setDevice(event.target.value as ProcessProps["device"])}><option value="auto">自动</option><option value="cpu">CPU</option><option value="gpu">GPU</option></select></Field></div></details>}</div>
        {hasMask && <div className="control-card mask-card"><div className="panel-heading"><div><span className="section-kicker">REVERSIBLE MASKING</span><h2>文档脱敏</h2></div><span className="lock-mark">⌁</span></div><div className="entity-grid">{ENTITY_OPTIONS.map(([key, label]) => <button className={`entity-chip ${props.entities.has(key) ? "selected" : ""} ${key === "CUSTOM" ? "custom" : ""}`} key={key} onClick={() => { const next = new Set(props.entities); next.has(key) ? next.delete(key) : next.add(key); props.setEntities(next); }}><span>{props.entities.has(key) ? "✓" : ""}</span>{label}</button>)}</div><label className="switch-row"><span><strong>使用 AI 增强敏感信息识别</strong><small>模型可用时增强识别，不影响基础能力</small></span><input type="checkbox" checked={props.aiEnhanced} onChange={(event) => props.setAiEnhanced(event.target.checked)} /><i /></label><div className="password-divider" /><Field label="脱敏恢复密码" hint="用于保护本次映射文件"><div className="password-input"><input type={props.showPassword ? "text" : "password"} value={props.password} onChange={(event) => props.setPassword(event.target.value)} placeholder="至少 8 个字符" /><button type="button" aria-label={props.showPassword ? "隐藏密码" : "显示密码"} onClick={() => props.setShowPassword(!props.showPassword)}><Icon name={props.showPassword ? "eye-off" : "eye"} /></button></div></Field><Field label="确认密码"><input type={props.showPassword ? "text" : "password"} value={props.confirmation} onChange={(event) => props.setConfirmation(event.target.value)} placeholder="再次输入密码" /></Field></div>}
        <div className="control-card output-card"><Field label="输出位置"><div className="path-input"><input value={props.outputDir} onChange={(event) => props.setOutputDir(event.target.value)} /><button type="button" aria-label="选择输出位置" onClick={() => props.chooseOutputDirectory(props.setOutputDir)}>选择</button></div></Field><button className="button process-button" onClick={props.runState === "processing" ? props.cancelProcess : props.handleProcess}>{props.runState === "processing" ? "取消处理" : props.runState === "success" ? "再次处理" : "开始处理"}<Icon name={props.runState === "processing" ? "close" : "arrow"} /></button>{props.message && <div className={`inline-message ${props.runState}`}><Icon name={props.runState === "error" ? "alert" : props.runState === "success" ? "check" : "close"} />{props.message}</div>}</div>
      </aside>
    </section>
  </>;
}

function ProgressPanel(props: ProcessProps) {
  const current = props.files.find((file) => props.progress[file.id]?.status === "running");
  return <div className="progress-panel"><div className="progress-top"><div><span className="section-kicker">IN PROGRESS</span><h2>正在处理{current ? `：${current.name}` : ""}</h2></div><strong>{props.overallProgress}%</strong></div><div className="overall-track"><span style={{ width: `${props.overallProgress}%` }} /></div><div className="batch-list">{props.files.map((file, index) => { const item = props.progress[file.id]; return <div className="batch-row" key={file.id}><span className="batch-index">{String(index + 1).padStart(2, "0")}</span><strong>{file.name}</strong><span className={`batch-state ${item?.status ?? "queued"}`}>{item?.status === "running" ? `${STEP_LABELS[item.step ?? "ocr"]} ${Math.round((item.progress ?? 0) * 100)}%` : item?.status === "done" ? "✓ 完成" : item?.status === "attention" ? "! 建议检查" : item?.status === "error" ? "× 失败" : "○ 等待"}</span></div>; })}</div></div>;
}

function ResultPanel(props: ProcessProps) {
  if (props.runState === "error" || props.runState === "cancelled") return <div className={`result-panel result-${props.runState}`}><span className="result-symbol"><Icon name={props.runState === "error" ? "alert" : "close"} /></span><div><span className="section-kicker">{props.runState === "error" ? "PROCESS FAILED" : "CANCELLED"}</span><h2>{props.runState === "error" ? "处理没有完成" : "处理已取消"}</h2><p>{props.message}</p></div></div>;
  const failedCount = props.results.filter((result) => result.status === "error").length;
  return <div className="result-panel result-success"><span className="result-symbol"><Icon name={failedCount ? "alert" : "check"} /></span><div><span className="section-kicker">{failedCount ? "PARTIAL COMPLETE" : "COMPLETE"}</span><h2>{failedCount ? "处理部分完成" : "处理完成"}</h2><p>已处理 {props.completedCount} 个文件{failedCount ? `，${failedCount} 个文件失败` : props.attentionCount ? `，${props.attentionCount} 个结果建议人工检查` : "，未发现明显的敏感信息残留"}。</p><div className="result-actions"><button className="button secondary" onClick={props.openOutputFolder}><Icon name="folder" />打开输出目录</button><button className="text-button" onClick={props.handleProcess}>再次处理 <Icon name="arrow" /></button></div></div></div>;
}

type RestoreProps = { restoreMasked: string; setRestoreMasked: (value: string) => void; restoreMapping: string; setRestoreMapping: (value: string) => void; restorePassword: string; setRestorePassword: (value: string) => void; restoreOutputDir: string; setRestoreOutputDir: (value: string) => void; chooseRestoreOutputDirectory: () => void; restoreShowPassword: boolean; setRestoreShowPassword: (value: boolean) => void; restoreState: RunState; restoreMessage: string; restoreResult: RestoreResult | null; openRestoreFile: () => void; openRestoreDirectory: () => void; handleRestore: (event: FormEvent) => void; restoreAbortRef: React.MutableRefObject<AbortController | null>; restoreMaskedInput: React.RefObject<HTMLInputElement | null>; restoreMappingInput: React.RefObject<HTMLInputElement | null>; onRestoreFileChange: (event: ChangeEvent<HTMLInputElement>, kind: "masked" | "mapping") => void };

function RestorePage(props: RestoreProps) {
  const processing = props.restoreState === "processing";
  return <><section className="page-heading restore-heading"><div><span className="section-kicker">REVERSIBLE RESTORE</span><h1>脱敏恢复</h1><p>使用脱敏文件、加密映射和密码，在本机恢复规范化原文。</p></div><div className="restore-mark">↶</div></section><form className="restore-layout" onSubmit={props.handleRestore}><div className="restore-card"><div className="restore-intro"><span className="restore-step">01</span><div><h2>选择恢复材料</h2><p>映射文件只用于本次恢复，不会上传或保存密码。</p></div></div><Field label="脱敏文件"><div className="path-input"><input value={props.restoreMasked} onChange={(event) => props.setRestoreMasked(event.target.value)} placeholder="document-xxxx.masked.md" /><button type="button" onClick={() => props.restoreMaskedInput.current?.click()}>选择</button><input ref={props.restoreMaskedInput} type="file" accept=".md,.markdown,.txt" hidden onChange={(event) => props.onRestoreFileChange(event, "masked")} /></div></Field><Field label="映射文件"><div className="path-input"><input value={props.restoreMapping} onChange={(event) => props.setRestoreMapping(event.target.value)} placeholder="document-xxxx.mapping.enc" /><button type="button" onClick={() => props.restoreMappingInput.current?.click()}>选择</button><input ref={props.restoreMappingInput} type="file" accept=".enc" hidden onChange={(event) => props.onRestoreFileChange(event, "mapping")} /></div></Field><div className="restore-divider" /><div className="restore-intro"><span className="restore-step">02</span><div><h2>输入恢复密码</h2><p>密码错误或文件被修改时，完整性校验会阻止恢复。</p></div></div><Field label="恢复密码"><div className="password-input"><input type={props.restoreShowPassword ? "text" : "password"} value={props.restorePassword} onChange={(event) => props.setRestorePassword(event.target.value)} placeholder="输入脱敏时设置的密码" /><button type="button" aria-label={props.restoreShowPassword ? "隐藏密码" : "显示密码"} onClick={() => props.setRestoreShowPassword(!props.restoreShowPassword)}><Icon name={props.restoreShowPassword ? "eye-off" : "eye"} /></button></div></Field><Field label="恢复到"><div className="path-input"><input value={props.restoreOutputDir} onChange={(event) => props.setRestoreOutputDir(event.target.value)} /><button type="button" onClick={props.chooseRestoreOutputDirectory}>选择</button></div></Field><button className="button process-button restore-button" type="submit" disabled={processing}>{processing ? "正在恢复…" : "开始恢复"}<Icon name="arrow" /></button>{props.restoreMessage && <div className={`inline-message ${props.restoreState}`}><Icon name={props.restoreState === "error" ? "alert" : props.restoreState === "success" ? "check" : "close"} />{props.restoreMessage}</div>}{props.restoreState === "success" && props.restoreResult && <div className="result-actions restore-actions"><button className="button secondary" type="button" onClick={props.openRestoreFile}>打开文件</button><button className="text-button" type="button" onClick={props.openRestoreDirectory}>打开所在目录 <Icon name="folder" /></button></div>}</div><aside className="restore-aside"><div className="aside-illustration"><div className="paper paper-back" /><div className="paper paper-front"><span>⟦人员1⟧</span><span>⟦机构1⟧</span><span>⟦电话1⟧</span></div><div className="restore-arrow">↶</div></div><h3>可逆，但不暴露</h3><p>Wenveil 将真实值放在 AES-GCM 加密映射中。脱敏文档本身不包含恢复所需的敏感原值。</p><div className="aside-note"><span className="status-led" />仅在本机校验哈希和恢复</div></aside></form></>;
}

type SettingsProps = { outputDir: string; setOutputDir: (value: string) => void; chooseOutputDirectory: (setter: (value: string) => void) => void; openOutput: boolean; setOpenOutput: (value: boolean) => void; keepIntermediate: boolean; setKeepIntermediate: (value: boolean) => void; ocrMode: "auto" | "fast" | "enhanced"; setOcrMode: (value: "auto" | "fast" | "enhanced") => void; device: "auto" | "cpu" | "gpu"; setDevice: (value: "auto" | "cpu" | "gpu") => void; entities: Set<string>; setEntities: (value: Set<string>) => void; aiEnhanced: boolean; setAiEnhanced: (value: boolean) => void; theme: string; setTheme: (value: string) => void };

function SettingsPage(props: SettingsProps) {
  return <><section className="page-heading"><div><span className="section-kicker">PREFERENCES</span><h1>设置</h1><p>把 Wenveil 调整成适合你的本地文档工作习惯。</p></div></section><div className="settings-layout"><div className="settings-main"><SettingsSection title="常规" note="输出与文件管理"><Field label="默认输出位置" hint="处理结果和加密映射将写入这里"><div className="path-input"><input value={props.outputDir} onChange={(event) => props.setOutputDir(event.target.value)} /><button type="button" onClick={() => props.chooseOutputDirectory(props.setOutputDir)}>选择</button></div></Field><Toggle label="完成后自动打开输出目录" description="处理完成时打开文件夹" checked={props.openOutput} onChange={props.setOpenOutput} /><Toggle label="保留中间文件" description="同时保存 OCR 和整理后的版本" checked={props.keepIntermediate} onChange={props.setKeepIntermediate} /></SettingsSection><SettingsSection title="文字识别" note="默认识别偏好"><Field label="默认识别方式"><select value={props.ocrMode} onChange={(event) => props.setOcrMode(event.target.value as SettingsProps["ocrMode"])}><option value="auto">自动</option><option value="fast">快速识别</option><option value="enhanced">增强识别</option></select></Field><Field label="默认处理设备"><select value={props.device} onChange={(event) => props.setDevice(event.target.value as SettingsProps["device"])}><option value="auto">自动</option><option value="cpu">CPU</option><option value="gpu">GPU</option></select></Field></SettingsSection><SettingsSection title="脱敏" note="默认保护范围"><div className="settings-entities">{ENTITY_OPTIONS.slice(0, 6).map(([key, label]) => <button className={`entity-chip ${props.entities.has(key) ? "selected" : ""}`} key={key} onClick={() => { const next = new Set(props.entities); next.has(key) ? next.delete(key) : next.add(key); props.setEntities(next); }}><span>{props.entities.has(key) ? "✓" : ""}</span>{label}</button>)}</div><Toggle label="AI 增强识别" description="模型可用时提高语义实体召回" checked={props.aiEnhanced} onChange={props.setAiEnhanced} /></SettingsSection></div><aside className="settings-side"><SettingsSection title="外观" note="界面显示"><div className="theme-picker">{[["system", "跟随系统", "◐"], ["light", "浅色", "☼"], ["dark", "深色", "◑"]].map(([value, label, icon]) => <button key={value} className={props.theme === value ? "theme-option selected" : "theme-option"} onClick={() => props.setTheme(value)}><span>{icon}</span>{label}</button>)}</div></SettingsSection><div className="about-card"><span className="brand-mark">文</span><strong>Wenveil <small>文隐</small></strong><p>本地 OCR、文本整理与可逆脱敏工具</p><span>桌面端 0.1.0 · Python bridge ready</span></div></aside></div></>;
}

function SettingsSection({ title, note, children }: { title: string; note: string; children: React.ReactNode }) { return <section className="settings-section"><div className="settings-section-heading"><div><h2>{title}</h2><p>{note}</p></div><span>⌘</span></div>{children}</section>; }
function Toggle({ label, description, checked, onChange }: { label: string; description: string; checked: boolean; onChange: (value: boolean) => void }) { return <label className="switch-row setting-toggle"><span><strong>{label}</strong><small>{description}</small></span><input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} /><i /></label>; }

export default App;
