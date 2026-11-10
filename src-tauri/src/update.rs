mod catalog;

use crate::shell::ShellState;
use base64::{engine::general_purpose::STANDARD, Engine};
use catalog::{Catalog, Package, PackageMetadata, DOWNLOAD_TIMEOUT, MAX_CHUNK, PLATFORM, PRODUCT};
use http::uri::Authority;
use minisign_verify::{PublicKey, Signature};
use semver::Version;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    cmp::Ordering,
    fs,
    io::Write,
    path::{Path, PathBuf},
    sync::Arc,
    time::Duration,
};
use tauri::{async_runtime::Mutex, AppHandle, Manager};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons};
use tempfile::{Builder, NamedTempFile, TempPath};
use tokio::{
    fs::File,
    io::{AsyncReadExt, AsyncWriteExt},
    time::timeout,
};
use tonic::transport::Endpoint;

const MAX_INSTALLER_SIZE: u64 = 512 * 1024 * 1024;
const STREAM_IDLE_TIMEOUT: Duration = Duration::from_secs(60);

#[derive(Default)]
pub(crate) struct PendingUpdate {
    available: Mutex<Option<AvailableUpdate>>,
    download: Mutex<()>,
}

struct AvailableUpdate {
    candidate: Candidate,
    installer: Option<Arc<TempPath>>,
}

#[derive(Clone, Copy, Default, PartialEq, Eq)]
enum UpdateChannel {
    #[default]
    Stable,
    Ci,
}

impl UpdateChannel {
    fn parse(channel: &str) -> Result<Self, String> {
        match channel {
            "stable" => Ok(Self::Stable),
            "ci" => Ok(Self::Ci),
            _ => Err("不支持的更新通道".into()),
        }
    }

    fn as_str(self) -> &'static str {
        match self {
            Self::Stable => "stable",
            Self::Ci => "ci",
        }
    }

    fn label(self) -> &'static str {
        match self {
            Self::Stable => "Stable",
            Self::Ci => "CI",
        }
    }

    fn accepts_version(self, version: &Version) -> bool {
        if !version.build.is_empty() {
            return false;
        }
        match self {
            Self::Stable => version.pre.is_empty(),
            Self::Ci => {
                let Some(suffix) = version.pre.as_str().strip_prefix("ci.") else {
                    return false;
                };
                match suffix.split_once('.') {
                    Some((build, attempt)) => [build, attempt].into_iter().all(|number| {
                        !number.is_empty()
                            && number != "0"
                            && number.bytes().all(|byte| byte.is_ascii_digit())
                    }),
                    None => suffix.len() == 17 && suffix.bytes().all(|byte| byte.is_ascii_digit()),
                }
            }
        }
    }
}

#[derive(Clone, PartialEq)]
struct Candidate {
    channel: UpdateChannel,
    id: String,
    version: Version,
    metadata: PackageMetadata,
}

impl Candidate {
    fn from_package(package: Package, channel: UpdateChannel) -> Result<Self, String> {
        let metadata = package.metadata.ok_or("平台软件包缺少元数据")?;
        let version = Version::parse(&metadata.version).map_err(|_| "平台更新版本不合法")?;
        let file_name = format!("{PRODUCT}_{version}_x64-setup.exe");
        let metadata_channel_matches = metadata.channel == channel.as_str()
            || channel == UpdateChannel::Stable && metadata.channel.is_empty();
        if package.id.is_empty()
            || !package.ever_published
            || !package.channels.iter().any(|value| value == channel.as_str())
            || metadata.product != PRODUCT
            || metadata.platform != PLATFORM
            || !metadata_channel_matches
            || !channel.accepts_version(&version)
            || metadata.file_name != file_name
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
            channel,
            id: package.id,
            version,
            metadata,
        })
    }
}

fn channel_path(app: &AppHandle) -> Result<PathBuf, String> {
    app.path()
        .app_local_data_dir()
        .map(|path| path.join("local/update-channel"))
        .map_err(|e| format!("无法访问更新通道配置目录：{e}"))
}

fn selected_channel(app: &AppHandle) -> UpdateChannel {
    if !app.state::<ShellState>().installed {
        return UpdateChannel::Stable;
    }
    channel_path(app)
        .ok()
        .and_then(|path| fs::read_to_string(path).ok())
        .and_then(|value| UpdateChannel::parse(value.trim()).ok())
        .unwrap_or_default()
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
        "channel": selected_channel(&app).as_str(),
    })
}

#[tauri::command]
pub(crate) fn set_update_channel(app: AppHandle, channel: String) -> Result<(), String> {
    let channel = UpdateChannel::parse(&channel)?;
    update_config(&app).ok_or("当前版本未配置平台更新")?;
    let state = app.state::<PendingUpdate>();
    let mut pending = state
        .available
        .try_lock()
        .map_err(|_| "更新操作正在进行")?;
    let path = channel_path(&app)?;
    let dir = path.parent().ok_or("无法定位更新通道配置目录")?;
    let save = || -> std::io::Result<()> {
        fs::create_dir_all(dir)?;
        let mut file = NamedTempFile::new_in(dir)?;
        file.write_all(channel.as_str().as_bytes())?;
        file.persist(&path)?;
        Ok(())
    };
    save().map_err(|e| format!("保存更新通道失败：{e}"))?;
    *pending = None;
    Ok(())
}

#[tauri::command]
pub(crate) async fn check_update(app: AppHandle) -> Result<Option<Value>, String> {
    let (endpoint, _) = update_config(&app).ok_or("当前版本未配置平台更新")?;
    let state = app.state::<PendingUpdate>();
    let mut pending = state
        .available
        .try_lock()
        .map_err(|_| "更新操作正在进行")?;
    let channel = selected_channel(&app);
    let mut catalog = Catalog::connect(endpoint).await?;
    let mut packages = catalog.list(channel.as_str()).await?;
    // 每个通道由平台指向唯一的当前包。
    if packages.len() > 1 {
        return Err(format!("平台返回了多个 {} 更新包", channel.label()));
    }
    let current = &app.package_info().version;
    let update = packages
        .pop()
        .map(|package| Candidate::from_package(package, channel))
        .transpose()?
        .filter(|candidate| candidate.version.cmp_precedence(current) == Ordering::Greater);
    if pending.as_ref().map(|update| &update.candidate) != update.as_ref() {
        *pending = update.map(|candidate| AvailableUpdate {
            candidate,
            installer: None,
        });
    }
    Ok(pending.as_ref().map(|update| {
        json!({
            "id": update.candidate.id,
            "version": update.candidate.metadata.version,
            "notes": update.candidate.metadata.notes,
            "ready": update.installer.is_some(),
        })
    }))
}

#[tauri::command]
pub(crate) async fn download_update(app: AppHandle, id: String) -> Result<bool, String> {
    let (endpoint, key) = update_config(&app).ok_or("当前版本未配置平台更新")?;
    let state = app.state::<PendingUpdate>();
    let _download = state.download.lock().await;
    let candidate = {
        let pending = state.available.lock().await;
        let Some(update) = pending.as_ref().filter(|update| update.candidate.id == id) else {
            return Ok(false);
        };
        if update.installer.is_some() {
            return Ok(true);
        }
        update.candidate.clone()
    };
    let key = decode_base64(key)
        .and_then(|key| PublicKey::decode(&key).ok())
        .ok_or("平台更新公钥格式不合法")?;
    let mut catalog = Catalog::connect(endpoint).await?;
    let installer = timeout(DOWNLOAD_TIMEOUT, download(&mut catalog, &candidate, &key))
        .await
        .map_err(|_| "平台更新下载超过总时限")??;
    let mut pending = state.available.lock().await;
    let Some(update) = pending
        .as_mut()
        .filter(|update| update.candidate == candidate)
    else {
        return Ok(false);
    };
    update.installer = Some(Arc::new(installer));
    Ok(true)
}

#[tauri::command]
pub(crate) async fn install_update(app: AppHandle, id: String) -> Result<bool, String> {
    let (endpoint, _) = update_config(&app).ok_or("当前版本未配置平台更新")?;
    let state = app.state::<PendingUpdate>();
    let mut pending = state
        .available
        .try_lock()
        .map_err(|_| "更新操作正在进行")?;
    let channel = selected_channel(&app);
    let update = pending
        .as_mut()
        .filter(|update| update.candidate.channel == channel && update.candidate.id == id)
        .ok_or("更新候选版本已变化，请重新检查更新")?;
    let installer = update.installer.clone().ok_or("更新包尚未下载完成")?;
    let version = update.candidate.metadata.version.clone();
    let dialog_app = app.clone();
    let channel_label = channel.label();
    let confirmed = tauri::async_runtime::spawn_blocking(move || {
        dialog_app
            .dialog()
            .message(format!(
                "安装 {channel_label} 通道的 {version} 将退出测试上位机。请先保存环境配置。"
            ))
            .title("确认安装更新")
            .buttons(MessageDialogButtons::OkCancelCustom(
                "退出并安装".into(),
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
    let latest = Candidate::from_package(catalog.get(id).await?, channel)?;
    if latest != update.candidate {
        return Err("平台软件包身份已变化，请重新检查更新".into());
    }
    if let Err(error) = verify_cached_installer(installer.as_ref().as_ref(), &latest.metadata).await {
        update.installer = None;
        return Err(error);
    }
    // 安装前让内核确认测试已空闲并正常退出，启动失败时由原流程恢复接收请求。
    let install_app = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        install_app
            .state::<ShellState>()
            .with_update_shutdown(|| launch_installer(installer.as_ref().as_ref()))
    })
    .await
    .map_err(|e| format!("安装更新失败：{e}"))??;
    Ok(true)
}

async fn verify_cached_installer(path: &Path, metadata: &PackageMetadata) -> Result<(), String> {
    let mut file = File::open(path)
        .await
        .map_err(|e| format!("读取已下载的更新包失败：{e}"))?
        .take(metadata.size + 1);
    let mut digest = Sha256::new();
    let mut buffer = vec![0; MAX_CHUNK];
    let mut size = 0;
    loop {
        let read = file
            .read(&mut buffer)
            .await
            .map_err(|e| format!("读取已下载的更新包失败：{e}"))?;
        if read == 0 {
            break;
        }
        size += read as u64;
        digest.update(&buffer[..read]);
    }
    if size != metadata.size
        || !format!("{:x}", digest.finalize()).eq_ignore_ascii_case(&metadata.sha256)
    {
        return Err("已下载的更新包完整性校验失败，等待重新下载".into());
    }
    Ok(())
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
fn launch_installer(installer: &Path) -> Result<(), String> {
    std::process::Command::new(installer)
        .args(["/P", "/R", "/UPDATE"])
        .spawn()
        .map_err(|error| format!("启动更新安装程序失败：{error}"))?;
    // 直接退出以释放安装目录，保留已经启动的临时安装包。
    std::process::exit(0);
}

#[cfg(not(windows))]
fn launch_installer(_installer: &Path) -> Result<(), String> {
    Err("平台更新仅支持 Windows x64 安装版".into())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn update_versions_are_exclusive_to_each_channel() {
        let stable = Version::parse("0.1.1").unwrap();
        let ci = Version::parse("0.1.1-ci.12.1").unwrap();
        let timed_ci = Version::parse("0.1.1-ci.20261110143025123.1").unwrap();
        let timestamp_only_ci = Version::parse("0.1.1-ci.20261110143025124").unwrap();
        assert!(UpdateChannel::Stable.accepts_version(&stable));
        assert!(!UpdateChannel::Ci.accepts_version(&stable));
        assert!(UpdateChannel::Ci.accepts_version(&ci));
        assert!(!UpdateChannel::Stable.accepts_version(&ci));
        assert!(UpdateChannel::Ci.accepts_version(&timed_ci));
        assert_eq!(timed_ci.cmp_precedence(&ci), Ordering::Greater);
        assert!(UpdateChannel::Ci.accepts_version(&timestamp_only_ci));
        assert!(!UpdateChannel::Stable.accepts_version(&timestamp_only_ci));
        assert_eq!(
            timestamp_only_ci.cmp_precedence(&timed_ci),
            Ordering::Greater
        );
        assert_eq!(stable.cmp_precedence(&timestamp_only_ci), Ordering::Greater);
        for invalid in [
            "0.1.1-beta.12.1",
            "0.1.1-ci.0.1",
            "0.1.1-ci.12.0",
            "0.1.1-ci.12",
            "0.1.1-ci.12.1.2",
            "0.1.1-ci.12.1+build",
            "0.1.1-ci.2026111014302512x",
            "0.1.1-ci.20261110143025124+build",
        ] {
            assert!(!UpdateChannel::Ci.accepts_version(&Version::parse(invalid).unwrap()));
        }
        assert!(UpdateChannel::parse("beta").is_err());
    }

    #[test]
    fn candidate_requires_channel_membership_metadata_and_archive_name() {
        let mut package = Package {
            id: "ci-package".into(),
            channels: vec!["ci".into()],
            ever_published: true,
            metadata: Some(PackageMetadata {
                product: PRODUCT.into(),
                platform: PLATFORM.into(),
                channel: "ci".into(),
                version: "0.1.1-ci.12.1".into(),
                file_name: "mskdsp-testbench_0.1.1-ci.12.1_x64-setup.exe".into(),
                size: 1,
                sha256: "0".repeat(64),
                signature: "signature".into(),
                ..Default::default()
            }),
        };
        assert!(Candidate::from_package(package.clone(), UpdateChannel::Ci).is_ok());
        assert!(Candidate::from_package(package.clone(), UpdateChannel::Stable).is_err());
        package.channels.clear();
        assert!(Candidate::from_package(package.clone(), UpdateChannel::Ci).is_err());
        package.channels.push("ci".into());
        package.metadata.as_mut().unwrap().channel = "stable".into();
        assert!(Candidate::from_package(package.clone(), UpdateChannel::Ci).is_err());
        let metadata = package.metadata.as_mut().unwrap();
        metadata.channel = "ci".into();
        metadata.file_name = "other-product_0.1.1-ci.12.1_x64-setup.exe".into();
        assert!(Candidate::from_package(package, UpdateChannel::Ci).is_err());
    }
}
