//! Signed self-update (tauri-plugin-updater, minisign signatures).
//!
//! The public key is compiled in from the build environment
//! (`YICHAT_UPDATER_PUBKEY`, a GitHub Actions variable); builds without one
//! never auto-update. The manifest lives on the fixed GitHub release
//! `desktop-updater` so it never competes with Android's "latest" release.
//! `requireSignedVersion` rejects downgrades to older signed artifacts.
use crate::bridge::Desktop;
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tauri::{AppHandle, Manager};
use tauri_plugin_updater::UpdaterExt;

pub const RELEASES_URL: &str = "https://github.com/yitiaosg/YiChat/releases";
const PUBKEY: Option<&str> = option_env!("YICHAT_UPDATER_PUBKEY");

pub fn enabled() -> bool { PUBKEY.is_some_and(|k| !k.trim().is_empty()) }

/// Versions the user already declined this session (no repeated prompts).
static DECLINED: Mutex<Option<String>> = Mutex::new(None);

pub fn spawn(app: AppHandle) {
    if !enabled() { return; }
    tauri::async_runtime::spawn(async move {
        tokio::time::sleep(Duration::from_secs(60)).await;
        loop {
            check(&app, false).await;
            tokio::time::sleep(Duration::from_secs(6 * 3600)).await;
        }
    });
}

/// `manual`: from the tray menu, so also report "up to date" and errors.
pub async fn check(app: &AppHandle, manual: bool) {
    let desktop = app.state::<Arc<Desktop>>().inner().clone();
    if !enabled() {
        if manual { let _ = open::that_detached(RELEASES_URL); }
        return;
    }
    let updater = match app.updater_builder().pubkey(PUBKEY.unwrap_or_default().trim()).build() {
        Ok(u) => u,
        Err(_) => { if manual { desktop.notice("无法检查更新"); } return; }
    };
    let update = match updater.check().await {
        Ok(Some(u)) => u,
        Ok(None) => { if manual { desktop.inform("YiChat 已是最新版本。"); } return; }
        Err(_) => { if manual { desktop.notice("检查更新失败，请检查网络后重试。"); } return; }
    };
    if !manual && DECLINED.lock().unwrap().as_deref() == Some(update.version.as_str()) { return; }
    let accepted = rfd::AsyncMessageDialog::new()
        .set_title("YiChat 更新")
        .set_description(format!("发现新版本 {}（当前 {}）。\n安装包已通过发布签名校验，安装后会自动重启。是否现在更新？",
            update.version, update.current_version))
        .set_buttons(rfd::MessageButtons::YesNo)
        .show().await == rfd::MessageDialogResult::Yes;
    if !accepted { *DECLINED.lock().unwrap() = Some(update.version.clone()); return; }
    // Signature (and signed version) are verified before anything is installed.
    match update.download_and_install(|_, _| {}, || {}).await {
        Ok(()) => app.restart(),
        Err(_) => desktop.notice("更新未完成：下载失败或签名校验未通过，当前版本保持不变。"),
    }
}
