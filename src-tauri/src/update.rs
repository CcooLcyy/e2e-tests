use crate::shell::ShellState;
use serde_json::{json, Value};
use std::time::Duration;
use tauri::{async_runtime::Mutex, AppHandle, Manager};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons};
use tauri_plugin_updater::{Update, UpdaterExt};

#[derive(Default)]
pub(crate) struct PendingUpdate(Mutex<Option<Update>>);

pub(crate) fn configured(app: &AppHandle) -> bool {
    app.config()
        .plugins
        .0
        .get("updater")
        .and_then(|config| config.get("endpoints"))
        .and_then(Value::as_array)
        .is_some_and(|endpoints| !endpoints.is_empty())
}

fn enabled(app: &AppHandle) -> bool {
    app.state::<ShellState>().installed && configured(app)
}

#[tauri::command]
pub(crate) fn update_info(app: AppHandle) -> Value {
    json!({
        "version": app.package_info().version.to_string(),
        "installed": app.state::<ShellState>().installed,
        "enabled": enabled(&app),
    })
}

#[tauri::command]
pub(crate) async fn check_update(app: AppHandle) -> Result<Option<Value>, String> {
    if !enabled(&app) {
        return Err("当前版本未配置平台更新".into());
    }
    let state = app.state::<PendingUpdate>();
    let mut pending = state.0.try_lock().map_err(|_| "更新操作正在进行")?;
    *pending = None;
    let update = app
        .updater_builder()
        .target("windows-x86_64-nsis")
        .configure_client(|client| client.https_only(true))
        .timeout(Duration::from_secs(30))
        .build()
        .map_err(|e| format!("无法初始化平台更新：{e}"))?
        .check()
        .await
        .map_err(|e| format!("检查平台更新失败：{e}"))?;
    let info = update.as_ref().map(|update| {
        json!({
            "version": update.version,
            "notes": update.body,
        })
    });
    *pending = update;
    Ok(info)
}

#[tauri::command]
pub(crate) async fn install_update(app: AppHandle, version: String) -> Result<bool, String> {
    if !enabled(&app) {
        return Err("当前版本未配置平台更新".into());
    }
    let state = app.state::<PendingUpdate>();
    let pending = state.0.try_lock().map_err(|_| "更新操作正在进行")?;
    let mut update = pending
        .as_ref()
        .filter(|update| update.version == version)
        .cloned()
        .ok_or("更新候选版本已变化，请重新检查更新")?;
    let dialog_app = app.clone();
    let confirmed = tauri::async_runtime::spawn_blocking(move || {
        dialog_app
            .dialog()
            .message(format!(
                "安装 {version} 将退出测试上位机。请先保存环境配置。"
            ))
            .title("确认安装更新")
            .buttons(MessageDialogButtons::OkCancelCustom(
                "下载并安装".into(),
                "取消".into(),
            ))
            .blocking_show()
    })
    .await
    .map_err(|e| format!("打开更新确认窗口失败：{e}"))?;
    if !confirmed {
        return Ok(false);
    }

    update.timeout = Some(Duration::from_secs(600));
    let bytes = update
        .download(|_, _| {}, || {})
        .await
        .map_err(|e| format!("下载或验证更新包失败：{e}"))?;
    // Windows 的更新器启动安装程序后会直接退出进程，必须先确认测试空闲并收尾。
    let install_app = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let shell = install_app.state::<ShellState>();
        shell.with_update_shutdown(|| {
            update
                .install(bytes)
                .map_err(|e| format!("启动更新安装程序失败：{e}"))
        })
    })
    .await
    .map_err(|e| format!("安装更新失败：{e}"))??;
    Ok(true)
}
