use serde_json::{json, Value};
use std::{
    fs::{self, OpenOptions},
    io::{BufRead, BufReader, Write},
    path::{Path, PathBuf},
    process::{Child, ChildStdin, Command, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        mpsc, Mutex,
    },
    time::Duration,
};
use tauri::Manager;
use tauri_plugin_dialog::DialogExt;

const METHODS: &[&str] = &[
    "bootstrap",
    "discover",
    "get_environment",
    "save_environment",
    "load_environment",
    "open_slavesim",
    "check_slavesim",
    "check_master104",
    "poll_master104",
    "start",
    "poll",
    "stop",
    "history",
    "history_detail",
    "case_detail",
    "shutdown",
];
fn allowed_method(method: &str) -> bool {
    METHODS.contains(&method)
}

struct BridgeProcess {
    child: Child,
    input: ChildStdin,
    output: mpsc::Receiver<Result<String, String>>,
    sequence: u64,
}

impl BridgeProcess {
    fn spawn(root: &Path) -> Result<Self, String> {
        let python = root.join(".venv/Scripts/python.exe");
        if !python.is_file() {
            return Err("未找到测试内核，请先运行 scripts/setup.ps1".into());
        }
        fs::create_dir_all(root.join("local")).map_err(|e| format!("创建日志目录失败：{e}"))?;
        let errors = OpenOptions::new()
            .create(true)
            .append(true)
            .open(root.join("local/bridge.log"))
            .map_err(|e| format!("打开桥接日志失败：{e}"))?;
        let mut command = Command::new(python);
        command
            .args(["-u", "-m", "testbench.bridge", "--root"])
            .arg(root)
            .current_dir(root)
            .env("PYTHONUTF8", "1")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(errors);
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000);
        }
        let mut child = command
            .spawn()
            .map_err(|e| format!("启动 Python 测试内核失败：{e}"))?;
        let input = child.stdin.take().ok_or("无法创建内核输入通道")?;
        let stdout = child.stdout.take().ok_or("无法创建内核输出通道")?;
        let (tx, output) = mpsc::channel();
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines() {
                if tx
                    .send(line.map_err(|e| format!("读取内核响应失败：{e}")))
                    .is_err()
                {
                    return;
                }
            }
            let _ = tx.send(Err("Python 测试内核已退出，请查看 local/bridge.log".into()));
        });
        Ok(Self {
            child,
            input,
            output,
            sequence: 0,
        })
    }

    fn call(&mut self, method: &str, payload: Value) -> Result<Value, String> {
        if self
            .child
            .try_wait()
            .map_err(|e| format!("查询内核状态失败：{e}"))?
            .is_some()
        {
            return Err("测试内核已退出，请重新打开测试上位机".into());
        }
        self.sequence += 1;
        let id = self.sequence;
        writeln!(
            self.input,
            "{}",
            json!({"id":id,"method":method,"payload":payload})
        )
        .map_err(|e| format!("发送内核请求失败：{e}"))?;
        self.input
            .flush()
            .map_err(|e| format!("刷新内核通道失败：{e}"))?;
        let line = self
            .output
            .recv_timeout(Duration::from_secs(90))
            .map_err(|_| "测试内核响应超时，请查看日志或等待当前任务结束")??;
        let response: Value =
            serde_json::from_str(&line).map_err(|e| format!("内核协议错误：{e}"))?;
        if response.get("id").and_then(Value::as_u64) != Some(id) {
            return Err("测试内核响应编号不匹配，请重新打开测试上位机".into());
        }
        if response.get("ok").and_then(Value::as_bool) == Some(true) {
            Ok(response.get("result").cloned().unwrap_or(Value::Null))
        } else {
            Err(response
                .get("error")
                .and_then(Value::as_str)
                .unwrap_or("测试内核返回错误")
                .into())
        }
    }
}

struct ShellState {
    root: PathBuf,
    bridge: Mutex<Option<BridgeProcess>>,
    closing: AtomicBool,
}

impl ShellState {
    fn call(&self, method: &str, payload: Value) -> Result<Value, String> {
        if !allowed_method(method) {
            return Err("不支持的测试操作".into());
        }
        let mut bridge = self.bridge.lock().map_err(|_| "内核状态锁不可用")?;
        if bridge.is_none() {
            if method == "shutdown" {
                return Ok(json!({"closed":true}));
            }
            *bridge = Some(BridgeProcess::spawn(&self.root)?);
        }
        bridge.as_mut().unwrap().call(method, payload)
    }
}

fn repository_root() -> Result<PathBuf, String> {
    let path = std::env::var_os("TESTBENCH_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .parent()
                .unwrap()
                .to_path_buf()
        });
    let root = path
        .canonicalize()
        .map_err(|e| format!("测试仓库路径不可用：{e}"))?;
    if !root.join("testbench/bridge.py").is_file() {
        return Err("测试仓库缺少 testbench/bridge.py".into());
    }
    // Windows canonicalize 使用扩展路径前缀，传给 Python 和界面前恢复常规表示。
    Ok(normal_path(&root))
}

fn normal_path(path: &Path) -> PathBuf {
    #[cfg(windows)]
    {
        let text = path.to_string_lossy();
        if let Some(unc) = text.strip_prefix(r"\\?\UNC\") {
            return PathBuf::from(format!(r"\\{unc}"));
        }
        if let Some(local) = text.strip_prefix(r"\\?\") {
            return PathBuf::from(local);
        }
    }
    path.to_path_buf()
}

fn artifact_path(root: &Path, path: &str) -> Result<PathBuf, String> {
    let artifacts = root
        .join("artifacts")
        .canonicalize()
        .map_err(|_| "测试产物目录不存在")?;
    let target = Path::new(path)
        .canonicalize()
        .map_err(|_| "报告或截图不存在")?;
    if !target.starts_with(&artifacts) {
        return Err("只允许打开测试产物目录内的报告和截图".into());
    }
    if target.is_file() {
        let extension = target
            .extension()
            .and_then(|s| s.to_str())
            .unwrap_or("")
            .to_ascii_lowercase();
        if !["md", "json", "xml", "txt", "log", "png", "jpg", "jpeg"].contains(&extension.as_str())
        {
            return Err("不支持打开该类型的测试产物".into());
        }
    }
    Ok(target)
}

#[tauri::command]
async fn bridge_call(
    app: tauri::AppHandle,
    method: String,
    payload: Option<Value>,
) -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        app.state::<ShellState>()
            .call(&method, payload.unwrap_or_else(|| json!({})))
    })
    .await
    .map_err(|e| format!("内核操作失败：{e}"))?
}

#[tauri::command]
async fn choose_path(app: tauri::AppHandle, kind: String) -> Result<Option<String>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let dialog = app.dialog().file();
        let selected = match kind.as_str() {
            "directory" => dialog.set_title("选择测试目录").blocking_pick_folder(),
            "exe" => dialog
                .set_title("选择可执行程序")
                .add_filter("Windows 程序", &["exe"])
                .blocking_pick_file(),
            "environment" => dialog
                .set_title("选择环境配置")
                .add_filter("环境配置", &["json"])
                .blocking_pick_file(),
            _ => return Err("不支持的文件选择类型".into()),
        };
        selected
            .map(|path| {
                path.into_path()
                    .map(|p| p.to_string_lossy().into_owned())
                    .map_err(|_| "无法读取所选路径".into())
            })
            .transpose()
    })
    .await
    .map_err(|e| format!("打开选择窗口失败：{e}"))?
}

#[tauri::command]
async fn open_artifact(app: tauri::AppHandle, path: String) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || {
        let target = artifact_path(&app.state::<ShellState>().root, &path)?;
        #[cfg(windows)]
        {
            // 系统关联打开使用参数传递，不把用户路径拼接到 shell 脚本。
            use std::os::windows::process::CommandExt;
            Command::new("explorer.exe")
                .arg(normal_path(&target))
                .creation_flags(0x08000000)
                .spawn()
                .map_err(|e| format!("打开测试产物失败：{e}"))?;
        }
        #[cfg(not(windows))]
        {
            let _ = target;
            return Err("当前桌面版仅支持 Windows".into());
        }
        Ok(())
    })
    .await
    .map_err(|e| format!("打开产物失败：{e}"))?
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let root = repository_root().map_err(std::io::Error::other)?;
            app.manage(ShellState {
                root,
                bridge: Mutex::new(None),
                closing: AtomicBool::new(false),
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            bridge_call,
            choose_path,
            open_artifact
        ])
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let app = window.app_handle().clone();
                if app
                    .state::<ShellState>()
                    .closing
                    .swap(true, Ordering::SeqCst)
                {
                    return;
                }
                let window = window.clone();
                tauri::async_runtime::spawn_blocking(move || {
                    match app.state::<ShellState>().call("shutdown", json!({})) {
                        Ok(result)
                            if result.get("closed").and_then(Value::as_bool) == Some(true) =>
                        {
                            let _ = window.destroy();
                        }
                        result => {
                            app.state::<ShellState>()
                                .closing
                                .store(false, Ordering::SeqCst);
                            let message = result
                                .err()
                                .unwrap_or_else(|| "测试任务仍在清理，请稍后再退出".into());
                            app.dialog()
                                .message(message)
                                .title("暂时无法退出")
                                .blocking_show();
                        }
                    }
                });
            }
        })
        .run(tauri::generate_context!())
        .expect("测试上位机运行失败");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn real_python_bridge_round_trip_and_shutdown() {
        let root = repository_root().unwrap();
        let mut bridge = BridgeProcess::spawn(&root).unwrap();
        let environment = bridge.call("get_environment", json!({})).unwrap();
        assert!(environment.get("app_path").is_some());
        assert!(bridge.call("get_environment", json!({})).is_ok());
        let response = bridge.call("shutdown", json!({})).unwrap();
        assert_eq!(response["closed"], true);
        assert!(bridge.child.wait().unwrap().success());
    }

    #[test]
    fn only_documented_bridge_methods_are_allowed() {
        assert!(allowed_method("bootstrap"));
        assert!(allowed_method("shutdown"));
        assert!(allowed_method("open_slavesim"));
        assert!(allowed_method("check_slavesim"));
        assert!(allowed_method("check_master104"));
        assert!(allowed_method("poll_master104"));
        assert!(!allowed_method("execute_shell"));
    }

    #[test]
    fn artifact_open_rejects_source_and_executable() {
        let root = std::env::temp_dir().join(format!("testbench-rust-{}", std::process::id()));
        std::fs::create_dir_all(root.join("artifacts")).unwrap();
        std::fs::write(root.join("source.py"), "").unwrap();
        std::fs::write(root.join("artifacts/program.exe"), "").unwrap();
        std::fs::write(root.join("artifacts/report.md"), "").unwrap();
        assert!(artifact_path(&root, root.join("source.py").to_str().unwrap()).is_err());
        assert!(
            artifact_path(&root, root.join("artifacts/program.exe").to_str().unwrap()).is_err()
        );
        assert!(artifact_path(&root, root.join("artifacts/report.md").to_str().unwrap()).is_ok());
    }
}
