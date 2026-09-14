use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;

use tauri::{AppHandle, Manager, State};

struct SidecarProcess {
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
}

impl Drop for SidecarProcess {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

struct SidecarState {
    process: Mutex<Option<SidecarProcess>>,
    cancel_requested: AtomicBool,
}

impl Default for SidecarState {
    fn default() -> Self {
        Self {
            process: Mutex::new(None),
            cancel_requested: AtomicBool::new(false),
        }
    }
}

fn project_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("..")
}

fn start_sidecar(app: &AppHandle) -> Result<SidecarProcess, String> {
    let packaged_name = if cfg!(target_os = "windows") {
        "wenveil-sidecar.exe"
    } else {
        "wenveil-sidecar"
    };
    let mut packaged_candidates = Vec::new();
    if let Ok(resource_dir) = app.path().resource_dir() {
        packaged_candidates.push(resource_dir);
    }
    if let Ok(executable_dir) = std::env::current_exe()
        .and_then(|path| path.parent().map(PathBuf::from).ok_or_else(|| std::io::Error::other("no executable directory")))
    {
        packaged_candidates.push(executable_dir);
    }
    for base_dir in packaged_candidates {
        let packaged = base_dir.join("wenveil-sidecar").join(packaged_name);
        if packaged.is_file() {
            return spawn_sidecar(packaged, base_dir);
        }
    }

    let root = project_root();
    let script = root.join("desktop").join("bridge").join("sidecar.py");
    let python = std::env::var("WENVEIL_PYTHON").unwrap_or_else(|_| "python".to_string());
    let mut command = Command::new(python);
    command.arg(&script);
    spawn_sidecar_command(command, root)
}

fn spawn_sidecar(executable: PathBuf, current_dir: PathBuf) -> Result<SidecarProcess, String> {
    let command = Command::new(executable);
    spawn_sidecar_command(command, current_dir)
}

fn spawn_sidecar_command(mut command: Command, current_dir: PathBuf) -> Result<SidecarProcess, String> {
    let mut child = command
        .current_dir(current_dir)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|_| "无法启动 Python sidecar，请确认 Python 已安装并可执行".to_string())?;
    let stdin = child
        .stdin
        .take()
        .ok_or_else(|| "Python sidecar 标准输入不可用".to_string())?;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "Python sidecar 标准输出不可用".to_string())?;
    Ok(SidecarProcess {
        child,
        stdin,
        stdout: BufReader::new(stdout),
    })
}

#[tauri::command]
fn sidecar_request(app: AppHandle, state: State<'_, SidecarState>, request: String) -> Result<String, String> {
    state.cancel_requested.store(false, Ordering::SeqCst);
    let mut guard = state
        .process
        .lock()
        .map_err(|_| "sidecar 状态锁不可用".to_string())?;
    if guard.is_none() {
        *guard = Some(start_sidecar(&app)?);
    }
    let process = guard.as_mut().expect("sidecar initialized");
    writeln!(process.stdin, "{request}")
        .map_err(|_| "无法向 Python sidecar 发送请求".to_string())?;
    process
        .stdin
        .flush()
        .map_err(|_| "无法刷新 Python sidecar 请求".to_string())?;

    let mut response = String::new();
    loop {
        if state.cancel_requested.load(Ordering::SeqCst) {
            let _ = process.child.kill();
            *guard = None;
            return Err("处理已取消".to_string());
        }
        let mut line = String::new();
        let read = process
            .stdout
            .read_line(&mut line)
            .map_err(|_| "无法读取 Python sidecar 响应".to_string())?;
        if read == 0 {
            let _ = process.child.kill();
            *guard = None;
            return Err("Python sidecar 意外退出".to_string());
        }
        let is_terminal = serde_json::from_str::<serde_json::Value>(&line)
            .ok()
            .and_then(|value| value.get("type").and_then(|kind| kind.as_str()).map(|kind| kind == "result" || kind == "error"))
            .unwrap_or(false);
        response.push_str(&line);
        if is_terminal {
            break;
        }
    }
    if state.cancel_requested.load(Ordering::SeqCst) {
        let _ = process.child.kill();
        *guard = None;
        return Err("处理已取消".to_string());
    }
    Ok(response)
}

#[tauri::command]
fn cancel_sidecar_request(state: State<'_, SidecarState>) -> Result<(), String> {
    state.cancel_requested.store(true, Ordering::SeqCst);
    Ok(())
}

#[tauri::command]
fn open_directory(path: String) -> Result<(), String> {
    let target = PathBuf::from(path);
    if !target.is_dir() {
        return Err("输出位置不存在或不可访问".to_string());
    }

    #[cfg(target_os = "windows")]
    let result = Command::new("explorer").arg(&target).spawn();
    #[cfg(target_os = "macos")]
    let result = Command::new("open").arg(&target).spawn();
    #[cfg(all(unix, not(target_os = "macos")))]
    let result = Command::new("xdg-open").arg(&target).spawn();

    result
        .map(|_| ())
        .map_err(|_| "无法打开输出位置".to_string())
}

#[tauri::command]
fn open_file(path: String) -> Result<(), String> {
    let target = PathBuf::from(path);
    if !target.is_file() {
        return Err("恢复文件不存在或不可访问".to_string());
    }

    #[cfg(target_os = "windows")]
    let result = Command::new("explorer").arg(&target).spawn();
    #[cfg(target_os = "macos")]
    let result = Command::new("open").arg(&target).spawn();
    #[cfg(all(unix, not(target_os = "macos")))]
    let result = Command::new("xdg-open").arg(&target).spawn();

    result
        .map(|_| ())
        .map_err(|_| "无法打开恢复文件".to_string())
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(SidecarState::default())
        .invoke_handler(tauri::generate_handler![sidecar_request, cancel_sidecar_request, open_directory, open_file])
        .run(tauri::generate_context!())
        .expect("error while running Wenveil desktop application");
}
