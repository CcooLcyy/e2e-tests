mod catalog;

use crate::shell::ShellState;
use base64::{engine::general_purpose::STANDARD, Engine};
use catalog::{
    Catalog, Package, PackageMetadata, CHANNEL, DOWNLOAD_TIMEOUT, MAX_CHUNK, PLATFORM, PRODUCT,
};
use http::uri::Authority;
use minisign_verify::{PublicKey, Signature};
use semver::Version;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{cmp::Ordering, time::Duration};
use tauri::{async_runtime::Mutex, AppHandle, Manager};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons};
use tempfile::{Builder, TempPath};
use tokio::{fs::File, io::AsyncWriteExt, time::timeout};
use tonic::transport::Endpoint;

const MAX_INSTALLER_SIZE: u64 = 512 * 1024 * 1024;
const STREAM_IDLE_TIMEOUT: Duration = Duration::from_secs(60);

#[derive(Default)]
pub(crate) struct PendingUpdate(Mutex<Option<Candidate>>);

#[derive(PartialEq)]
struct Candidate {
    id: String,
    version: Version,
    metadata: PackageMetadata,
}

impl Candidate {
    fn from_package(package: Package) -> Result<Self, String> {
        let metadata = package.metadata.ok_or("平台软件包缺少元数据")?;
        let version = Version::parse(&metadata.version).map_err(|_| "平台更新版本不合法")?;
        let suffix = format!("_{version}_x64-setup.exe");
        if package.id.is_empty()
            || !package.ever_published
            || !package.channels.iter().any(|channel| channel == CHANNEL)
            || metadata.product != PRODUCT
            || metadata.platform != PLATFORM
            || !matches!(metadata.channel.as_str(), "" | CHANNEL)
            || !version.pre.is_empty()
            || !version.build.is_empty()
            || !metadata.file_name.ends_with(&suffix)
            || metadata.size == 0
            || metadata.size > MAX_INSTALLER_SIZE
            || metadata.sha256.len() != 64
            || !metadata.sha256.bytes().all(|byte| byte.is_ascii_hexdigit())
            || metadata.signature.trim().is_empty()
            || metadata.signature.len() > 32768
        {
            return Err("平台软件包身份、大小、摘要或签名不合法".into());
        }
        Ok(Self {
            id: package.id,
            version,
            metadata,
        })
    }
}

fn update_config(app: &AppHandle) -> Option<(Endpoint, &str)> {
    if !cfg!(all(target_os = "windows", target_arch = "x86_64"))
        || !app.state::<ShellState>().installed
    {
        return None;
    }
    let config = app.config().plugins.0.get("testbenchUpdate")?;
    let value = config.get("endpoint")?.as_str()?;
    let authority_text = value.strip_prefix("http://")?;
    let authority = authority_text.parse::<Authority>().ok()?;
    if authority.as_str() != authority_text
        || authority_text.contains(['@', '/', '?', '#'])
        || authority.host().is_empty()
        || !authority.port_u16().is_some_and(|port| port != 0)
    {
        return None;
    }
    let endpoint = Endpoint::from_shared(value.to_owned()).ok()?;
    let key = config.get("pubkey")?.as_str()?.trim();
    (!key.is_empty()).then_some((endpoint, key))
}

#[tauri::command]
pub(crate) fn update_info(app: AppHandle) -> Value {
    json!({
        "version": app.package_info().version.to_string(),
        "installed": app.state::<ShellState>().installed,
        "enabled": update_config(&app).is_some(),
    })
}

#[tauri::command]
pub(crate) async fn check_update(app: AppHandle) -> Result<Option<Value>, String> {
    let (endpoint, _) = update_config(&app).ok_or("当前版本未配置平台更新")?;
    let state = app.state::<PendingUpdate>();
    let mut pending = state.0.try_lock().map_err(|_| "更新操作正在进行")?;
    *pending = None;
    let mut catalog = Catalog::connect(endpoint).await?;
    let mut packages = catalog.list().await?;
    // Stable 由平台指向唯一的当前包。
    if packages.len() > 1 {
        return Err("平台返回了多个 Stable 更新包".into());
    }
    let current = &app.package_info().version;
    let update = packages
        .pop()
        .map(Candidate::from_package)
        .transpose()?
        .filter(|candidate| candidate.version.cmp_precedence(current) == Ordering::Greater);
    let info = update.as_ref().map(|update| {
        json!({
            "version": update.metadata.version,
            "notes": update.metadata.notes,
        })
    });
    *pending = update;
    Ok(info)
}

#[tauri::command]
pub(crate) async fn install_update(app: AppHandle, version: String) -> Result<bool, String> {
    let (endpoint, key) = update_config(&app).ok_or("当前版本未配置平台更新")?;
    let state = app.state::<PendingUpdate>();
    let pending = state.0.try_lock().map_err(|_| "更新操作正在进行")?;
    let candidate = pending
        .as_ref()
        .filter(|candidate| candidate.metadata.version == version)
        .ok_or("更新候选版本已变化，请重新检查更新")?;
    let key = decode_base64(key)
        .and_then(|key| PublicKey::decode(&key).ok())
        .ok_or("平台更新公钥格式不合法")?;
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

    let mut catalog = Catalog::connect(endpoint).await?;
    let latest = Candidate::from_package(catalog.get(candidate.id.clone()).await?)?;
    if latest != *candidate {
        return Err("平台软件包身份已变化，请重新检查更新".into());
    }
    let installer = timeout(DOWNLOAD_TIMEOUT, download(&mut catalog, &latest, &key))
        .await
        .map_err(|_| "平台更新下载超过总时限")??;
    // 安装前让内核确认测试已空闲并正常退出，启动失败时由原流程恢复接收请求。
    let install_app = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        install_app
            .state::<ShellState>()
            .with_update_shutdown(|| launch_installer(installer))
    })
    .await
    .map_err(|e| format!("安装更新失败：{e}"))??;
    Ok(true)
}

fn decode_base64(value: &str) -> Option<String> {
    String::from_utf8(STANDARD.decode(value.trim()).ok()?).ok()
}

async fn download(
    catalog: &mut Catalog,
    candidate: &Candidate,
    key: &PublicKey,
) -> Result<TempPath, String> {
    let metadata = &candidate.metadata;
    let signature = decode_base64(&metadata.signature)
        .and_then(|signature| Signature::decode(&signature).ok())
        .ok_or("平台更新包签名格式不合法")?;
    let mut verifier = key
        .verify_stream(&signature)
        .map_err(|_| "平台更新包的签名算法或公钥不匹配")?;

    let mut stream = timeout(STREAM_IDLE_TIMEOUT, catalog.download(candidate.id.clone()))
        .await
        .map_err(|_| "平台更新数据流响应超时")??;
    let (file, path) = Builder::new()
        .prefix("mskdsp-testbench-update-")
        .suffix("-setup.exe")
        .tempfile()
        .map_err(|e| format!("创建临时安装包失败：{e}"))?
        .into_parts();
    let mut file = File::from_std(file);
    let mut sha256 = Sha256::new();
    let mut offset = 0;
    let mut eof = false;
    while let Some(chunk) = timeout(STREAM_IDLE_TIMEOUT, stream.message())
        .await
        .map_err(|_| "平台更新数据流空闲超时")?
        .map_err(|status| format!("读取平台更新数据流失败：{}", status.code()))?
    {
        let end = offset + chunk.data.len() as u64;
        if eof
            || chunk.offset != offset
            || chunk.total_size != metadata.size
            || !chunk.sha256.eq_ignore_ascii_case(&metadata.sha256)
            || chunk.data.is_empty()
            || chunk.data.len() > MAX_CHUNK
            || end > metadata.size
            || chunk.eof != (end == metadata.size)
        {
            return Err("平台更新数据流偏移、大小、摘要或结束标记不合法".into());
        }
        file.write_all(&chunk.data)
            .await
            .map_err(|e| format!("写入临时安装包失败：{e}"))?;
        sha256.update(&chunk.data);
        verifier.update(&chunk.data);
        offset = end;
        eof = chunk.eof;
    }
    if !eof || !format!("{:x}", sha256.finalize()).eq_ignore_ascii_case(&metadata.sha256) {
        return Err("平台更新包完整大小或 SHA-256 校验失败".into());
    }
    verifier
        .finalize()
        .map_err(|_| "平台更新包的 Tauri 签名校验失败")?;
    verify_signed_identity(&signature, &metadata.version)?;
    file.flush()
        .await
        .map_err(|e| format!("保存临时安装包失败：{e}"))?;
    Ok(path)
}

fn verify_signed_identity(signature: &Signature, version: &str) -> Result<(), String> {
    let comment = signature.trusted_comment();
    let mut versions = comment
        .split('\t')
        .filter_map(|field| field.strip_prefix("version:"));
    let mut filenames = comment
        .split('\t')
        .filter_map(|field| field.strip_prefix("file:"));
    let suffix = format!("_{version}_x64-setup.exe");
    if versions.next() != Some(version)
        || versions.next().is_some()
        || !filenames.next().is_some_and(|name| name.ends_with(&suffix))
        || filenames.next().is_some()
    {
        return Err("平台更新签名中的安装包身份或版本与目录不一致".into());
    }
    Ok(())
}

#[cfg(windows)]
fn launch_installer(installer: TempPath) -> Result<(), String> {
    let installer = installer
        .keep()
        .map_err(|e| format!("保留待执行安装包失败：{e}"))?;
    let child = std::process::Command::new(&installer)
        .args(["/P", "/R", "/UPDATE"])
        .spawn();
    if let Err(error) = child {
        let _ = std::fs::remove_file(&installer);
        return Err(format!("启动更新安装程序失败：{error}"));
    }
    std::process::exit(0);
}

#[cfg(not(windows))]
fn launch_installer(_installer: TempPath) -> Result<(), String> {
    Err("平台更新仅支持 Windows x64 安装版".into())
}
