use super::{
    catalog::{Catalog, Package, PackageMetadata},
    decode_public_key, download_verified_package, platform_config, save_channel, selected_channel,
    verify_signed_identity, UpdateChannel,
};
use crate::shell::ShellState;
use semver::Version;
use serde_json::{json, Value};
use std::{cmp::Ordering, sync::Arc};
use tauri::{async_runtime::Mutex, AppHandle, Manager};
use tempfile::{Builder, TempPath};

const PRODUCT: &str = "mskdsp-testbench-cases";
const PLATFORM: &str = "python-zip";
const PLUGIN: &str = "testbenchCasesUpdate";
const CHANNEL_FILE: &str = "case-pack-update-channel";
const MAX_PACK_SIZE: u64 = 64 * 1024 * 1024;

#[derive(Default)]
pub(crate) struct PendingCasePack(Mutex<Option<PreparedPack>>);

struct PreparedPack {
    candidate: CasePackCandidate,
    archive: Arc<TempPath>,
}

#[derive(PartialEq)]
struct CasePackCandidate {
    id: String,
    metadata: PackageMetadata,
    version: Version,
}

impl CasePackCandidate {
    fn from_package(package: Package, channel: UpdateChannel) -> Result<Self, String> {
        let metadata = package.metadata.ok_or("平台用例包缺少元数据")?;
        let version = Version::parse(&metadata.version).map_err(|_| "平台用例包版本不合法")?;
        if package.id.is_empty()
            || !package.ever_published
            || !package.channels.iter().any(|value| value == channel.as_str())
            || metadata.channel != channel.as_str()
            || metadata.product != PRODUCT
            || metadata.platform != PLATFORM
            || !accepts_version(channel, &version)
            || metadata.file_name != format!("{PRODUCT}_{version}.zip")
            || !(1..=MAX_PACK_SIZE).contains(&metadata.size)
            || !is_hex(&metadata.sha256, 64)
            || !is_hex(&metadata.source_sha, 40)
            || metadata.source_sha.bytes().any(|byte| byte.is_ascii_uppercase())
            || metadata.signature.trim().is_empty()
            || metadata.signature.len() > 32768
        {
            return Err("平台用例包的频道、身份、大小、摘要或签名不合法".into());
        }
        Ok(Self {
            id: package.id,
            metadata,
            version,
        })
    }

    fn is_update(&self, current: &Value, channel: UpdateChannel) -> Result<bool, String> {
        if current["source"] != "downloaded" {
            return Ok(true);
        }
        let Some(version) = current["version"]
            .as_str()
            .and_then(|value| Version::parse(value).ok())
        else {
            return Ok(true);
        };
        if !accepts_version(channel, &version) {
            return Ok(true);
        }
        match self.version.cmp_precedence(&version) {
            Ordering::Greater => Ok(true),
            Ordering::Less => Ok(false),
            Ordering::Equal
                if current["source_sha"] == self.metadata.source_sha.as_str()
                    && current["sha256"]
                        .as_str()
                        .is_some_and(|sha| sha.eq_ignore_ascii_case(&self.metadata.sha256)) =>
            {
                Ok(false)
            }
            Ordering::Equal => Err("平台用例包版本与已启用版本相同，但源码或摘要不同".into()),
        }
    }
}

fn accepts_version(channel: UpdateChannel, version: &Version) -> bool {
    match channel {
        UpdateChannel::Stable => channel.accepts_version(version),
        UpdateChannel::Ci => {
            channel.accepts_version(version) && version.pre.as_str().split('.').count() == 3
        }
    }
}

fn is_hex(value: &str, len: usize) -> bool {
    value.len() == len && value.bytes().all(|byte| byte.is_ascii_hexdigit())
}

#[tauri::command]
pub(crate) fn case_pack_update_info(app: AppHandle) -> Value {
    json!({
        "enabled": platform_config(&app, PLUGIN).is_some(),
        "channel": selected_channel(&app, CHANNEL_FILE).as_str(),
    })
}

#[tauri::command]
pub(crate) fn set_case_pack_update_channel(app: AppHandle, channel: String) -> Result<(), String> {
    platform_config(&app, PLUGIN).ok_or("当前版本未配置平台用例更新")?;
    let channel = UpdateChannel::parse(&channel)?;
    let state = app.state::<PendingCasePack>();
    let mut pending = state.0.try_lock().map_err(|_| "正在更新用例包")?;
    save_channel(&app, CHANNEL_FILE, channel)?;
    *pending = None;
    Ok(())
}

#[tauri::command]
pub(crate) async fn prepare_case_pack_update(app: AppHandle) -> Result<Value, String> {
    let Some((endpoint, key)) = platform_config(&app, PLUGIN) else {
        return Ok(json!({"status": "disabled"}));
    };
    let state = app.state::<PendingCasePack>();
    let mut pending = state.0.try_lock().map_err(|_| "正在更新用例包")?;
    let channel = selected_channel(&app, CHANNEL_FILE);
    let mut catalog = Catalog::connect(endpoint).await?;
    let mut packages = catalog.list(PRODUCT, channel.as_str(), PLATFORM).await?;
    if packages.len() > 1 {
        return Err(format!("平台返回了多个 {} 用例包", channel.label()));
    }
    let Some(package) = packages.pop() else {
        *pending = None;
        return Ok(json!({"status": "unavailable"}));
    };
    let candidate = CasePackCandidate::from_package(package, channel)?;
    let status_app = app.clone();
    let current = tauri::async_runtime::spawn_blocking(move || {
        status_app.state::<ShellState>().case_pack_status()
    })
    .await
    .map_err(|e| format!("读取用例包状态失败：{e}"))??;
    if !candidate.is_update(&current, channel)? {
        *pending = None;
        return Ok(json!({"status": "current"}));
    }

    let result = json!({
        "status": "ready",
        "id": candidate.id,
        "version": candidate.metadata.version,
    });
    if pending.as_ref().is_some_and(|pack| pack.candidate == candidate) {
        return Ok(result);
    }
    *pending = None;
    let key = decode_public_key(key)?;
    let directory = app.state::<ShellState>().data_root().join(".case-downloads");
    tokio::fs::create_dir_all(&directory)
        .await
        .map_err(|e| format!("创建用例包下载目录失败：{e}"))?;
    let temporary = Builder::new()
        .prefix("mskdsp-testbench-cases-")
        .suffix(".zip")
        .tempfile_in(&directory)
        .map_err(|e| format!("创建临时用例包失败：{e}"))?;
    let (archive, signature) = download_verified_package(
        &mut catalog,
        &candidate.id,
        &candidate.metadata,
        &key,
        temporary,
    )
    .await?;
    verify_signed_identity(&signature, &candidate.metadata.version, |name| {
        name == candidate.metadata.file_name.as_str()
    })?;
    *pending = Some(PreparedPack {
        candidate,
        archive: Arc::new(archive),
    });
    Ok(result)
}

#[tauri::command]
pub(crate) async fn activate_case_pack_update(app: AppHandle, id: String) -> Result<Value, String> {
    let state = app.state::<PendingCasePack>();
    let mut pending = state.0.try_lock().map_err(|_| "正在更新用例包")?;
    let prepared = pending
        .as_ref()
        .filter(|pack| pack.candidate.id == id)
        .ok_or("待启用用例包已变化，请重新刷新用例")?;
    let archive = Arc::clone(&prepared.archive);
    let metadata = &prepared.candidate.metadata;
    let payload = json!({
        "path": archive.to_str().ok_or("无法读取临时用例包路径")?,
        "version": metadata.version,
        "source_sha": metadata.source_sha,
        "sha256": metadata.sha256.to_ascii_lowercase(),
    });
    let activation_app = app.clone();
    let result = tauri::async_runtime::spawn_blocking(move || {
        let activation = activation_app
            .state::<ShellState>()
            .activate_case_pack(payload);
        drop(archive);
        activation
    })
    .await
    .map_err(|e| format!("启用用例包失败：{e}"))?;
    if let Ok(None) = result {
        return Ok(json!({"status": "deferred"}));
    }
    *pending = None;
    Ok(json!({"status": "activated", "data": result?}))
}
