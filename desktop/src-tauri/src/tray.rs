//! System tray, close-to-tray, single-instance activation and reconnect nudges.
use crate::bridge::Desktop;
use crate::config::MAIN_WINDOW;
use crate::notify;
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime};
use tauri::menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager, WindowEvent};
use tauri_plugin_autostart::ManagerExt;

pub const AUTOSTART_ARG: &str = "--autostart";

/// Builds the tray. Returns false when the desktop has no tray support
/// (e.g. Linux without AppIndicator): closing the window then quits.
pub fn create(app: &AppHandle) -> bool {
    match build(app) { Ok(()) => true, Err(_) => { log::info!("tray unavailable"); false } }
}

fn build(app: &AppHandle) -> tauri::Result<()> {
    let desktop = app.state::<Arc<Desktop>>().inner().clone();
    let settings = desktop.settings.get();
    let open = MenuItem::with_id(app, "open", "打开 YiChat", true, None::<&str>)?;
    let preview = CheckMenuItem::with_id(app, "preview", "通知显示消息预览", true, settings.preview, None::<&str>)?;
    let autostart_on = app.autolaunch().is_enabled().unwrap_or(false);
    let autostart = CheckMenuItem::with_id(app, "autostart", "开机时启动", true, autostart_on, None::<&str>)?;
    let update = MenuItem::with_id(app, "update", format!("检查更新（当前 {}）", env!("CARGO_PKG_VERSION")), true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "退出 YiChat", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open, &PredefinedMenuItem::separator(app)?, &preview, &autostart,
        &PredefinedMenuItem::separator(app)?, &update, &quit])?;
    let (preview_item, autostart_item) = (preview.clone(), autostart.clone());
    TrayIconBuilder::with_id("main")
        .icon(app.default_window_icon().cloned().ok_or(tauri::Error::InvalidIcon(std::io::Error::other("icon")))?)
        .tooltip("YiChat")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(move |app, event| match event.id().as_ref() {
            "open" => show_main(app),
            "preview" => {
                let d = app.state::<Arc<Desktop>>();
                d.settings.update(|v| v.preview = !v.preview);
                let _ = preview_item.set_checked(d.settings.get().preview);
            }
            "autostart" => {
                let launcher = app.autolaunch();
                let enabled = launcher.is_enabled().unwrap_or(false);
                let _ = if enabled { launcher.disable() } else { launcher.enable() };
                let _ = autostart_item.set_checked(launcher.is_enabled().unwrap_or(false));
            }
            "update" => { let app = app.clone(); tauri::async_runtime::spawn(async move { crate::updater::check(&app, true).await }); }
            "quit" => app.exit(0),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = event {
                show_main(tray.app_handle());
            }
        })
        .build(app)?;
    Ok(())
}

pub fn show_main(app: &AppHandle) {
    if let Some(w) = app.get_webview_window(MAIN_WINDOW) {
        let _ = w.unminimize();
        let _ = w.show();
        let _ = w.set_focus();
        set_page_hidden(app, false);
    }
}

/// The shared UI only notifies for the open conversation while
/// `document.hidden`; a window hidden to the tray must count as hidden on
/// every platform. Handled by the desktop shim in bridge.js.
pub fn set_page_hidden(app: &AppHandle, hidden: bool) {
    if let Some(w) = app.get_webview_window(MAIN_WINDOW) {
        let _ = w.eval(format!("window.dispatchEvent(new CustomEvent('yc-desktop:visibility',{{detail:{hidden}}}))"));
    }
}

/// Close hides to the tray (when there is one) instead of quitting.
pub fn on_window_event(window: &tauri::Window, event: &WindowEvent) {
    let app = window.app_handle();
    let Some(desktop) = app.try_state::<Arc<Desktop>>() else { return };
    if let WindowEvent::CloseRequested { api, .. } = event {
        if !desktop.tray_available() { return; } // No tray: closing quits as usual.
        api.prevent_close();
        let _ = window.hide();
        set_page_hidden(app, true);
        if !desktop.settings.get().tray_hint_shown {
            desktop.settings.update(|v| v.tray_hint_shown = true);
            notify::show("", "YiChat 仍在后台运行", "可从系统托盘重新打开，或在托盘菜单中退出。", || {});
        }
    }
}

/// Arguments from a second launch (or the first one): open a conversation
/// from a toast activation URL, otherwise just bring the window forward.
pub fn activate(app: &AppHandle, args: &[String], startup: bool) {
    let desktop = app.state::<Arc<Desktop>>();
    if let Some(order) = args.iter().find_map(|a| notify::return_from_activation(a)) {
        desktop.open_return(order.as_deref());
        return;
    }
    let room = args.iter().find_map(|a| notify::room_from_activation(a));
    match room {
        Some(room) => desktop.open_room(&room),
        None if !startup => show_main(app),
        None => {}
    }
}

/// The SDK retries on its own with backoff; after the network returns or the
/// machine wakes from sleep, ask it to retry immediately instead.
pub fn spawn_reconnect_monitor(desktop: Arc<Desktop>) {
    tauri::async_runtime::spawn(async move {
        const TICK: Duration = Duration::from_secs(15);
        let mut online = true;
        let (mut wall, mut mono) = (SystemTime::now(), Instant::now());
        loop {
            tokio::time::sleep(TICK).await;
            let now_online = crate::bridge::online().await;
            // Monotonic time stops during suspend on Linux; wall time does not.
            let slept = SystemTime::now().duration_since(wall).unwrap_or_default()
                .saturating_sub(mono.elapsed()) > Duration::from_secs(30);
            if now_online && (!online || slept) { desktop.nudge_sync(); }
            online = now_online;
            (wall, mono) = (SystemTime::now(), Instant::now());
        }
    });
}

/// AppImage has no installer: register `yichat-open:` for this user so
/// payment returns and links reach the running AppImage. The .deb ships the
/// same MimeType in its desktop file. Best effort; never fatal.
#[cfg(target_os = "linux")]
pub fn register_appimage_scheme() {
    let Some(appimage) = std::env::var_os("APPIMAGE") else { return };
    let Some(home) = std::env::var_os("HOME") else { return };
    let dir = std::path::Path::new(&home).join(".local/share/applications");
    let exec = appimage.to_string_lossy().replace('\\', "\\\\").replace('"', "\\\"");
    let entry = format!("[Desktop Entry]\nType=Application\nName=YiChat\nIcon=yichat-desktop\nExec=\"{exec}\" %u\nTerminal=false\nNoDisplay=true\nMimeType=x-scheme-handler/{};\n", crate::config::OPEN_SCHEME);
    let file = dir.join("yichat-desktop-appimage.desktop");
    if std::fs::read_to_string(&file).ok().as_deref() == Some(entry.as_str()) { return; }
    if std::fs::create_dir_all(&dir).is_err() || std::fs::write(&file, entry).is_err() { return; }
    let _ = std::process::Command::new("xdg-mime").args(["default", "yichat-desktop-appimage.desktop", &format!("x-scheme-handler/{}", crate::config::OPEN_SCHEME)]).status();
    let _ = std::process::Command::new("update-desktop-database").arg(&dir).status();
}
#[cfg(not(target_os = "linux"))]
pub fn register_appimage_scheme() {}
