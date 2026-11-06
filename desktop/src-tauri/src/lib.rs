mod auth;
mod bridge;
mod config;
mod notify;
mod proxy;
mod secure_store;
mod settings;
mod shell;
mod tray;

use bridge::Desktop;
use std::sync::Arc;
use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

pub fn run() {
    // Probe the OS secure store before any webview exists: without it the
    // webview runs ephemerally and nothing is persisted.
    let store = Arc::new(secure_store::SecureStore::open());

    let args: Vec<String> = std::env::args().collect();
    let autostarted = args.iter().any(|a| a == tray::AUTOSTART_ARG);
    #[cfg(windows)]
    notify::register();

    tauri::Builder::default()
        // Must be first: a second launch (or a toast activation) is forwarded here.
        .plugin(tauri_plugin_single_instance::init(|app, argv, _cwd| tray::activate(app, &argv, false)))
        .plugin(tauri_plugin_autostart::init(tauri_plugin_autostart::MacosLauncher::LaunchAgent, Some(vec![tray::AUTOSTART_ARG])))
        .register_asynchronous_uri_scheme_protocol(config::SCHEME, |ctx, request, responder| {
            let app = ctx.app_handle().clone();
            tauri::async_runtime::spawn(async move {
                let Some(desktop) = app.try_state::<Arc<Desktop>>().map(|s| s.inner().clone()) else {
                    responder.respond(proxy::error(http::StatusCode::SERVICE_UNAVAILABLE, "正在启动"));
                    return;
                };
                responder.respond(desktop.serve(request).await);
            });
        })
        .invoke_handler(tauri::generate_handler![bridge::yichat_bridge])
        .setup(move |app| {
            let desktop = Arc::new(Desktop::new(app.handle().clone(), store.clone())?);
            app.manage(desktop.clone());
            let has_tray = tray::create(app.handle());
            desktop.set_tray(has_tray);
            // Autostart opens straight into the tray when there is one.
            create_main_window(app.handle(), store.persistent(), !(autostarted && has_tray))?;
            tray::activate(app.handle(), &args, true);
            tray::spawn_reconnect_monitor(desktop.clone());
            let refresh = desktop.clone();
            tauri::async_runtime::spawn(async move { refresh.refresh_shell().await });
            Ok(())
        })
        .on_window_event(tray::on_window_event)
        .run(tauri::generate_context!())
        .expect("error while running YiChat");
}

fn create_main_window(app: &tauri::AppHandle, persistent: bool, visible: bool) -> tauri::Result<()> {
    let start: url::Url = format!("{}/", config::app_origin()).parse().expect("static url");
    #[allow(unused_mut)]
    let mut script = include_str!("bridge.js").replace("__YICHAT_ORIGIN__", &config::app_origin());
    #[cfg(debug_assertions)]
    if std::env::var_os("YICHAT_DESKTOP_PROBE").is_some() { script.push_str(include_str!("probe.js")); }
    let handle = app.clone();
    let builder = WebviewWindowBuilder::new(app, config::MAIN_WINDOW, WebviewUrl::CustomProtocol(start))
        .title("YiChat")
        .visible(visible)
        .inner_size(1100.0, 760.0)
        .min_inner_size(380.0, 560.0)
        .use_https_scheme(true) // Windows: https://yichat.localhost (secure context)
        .incognito(!persistent)
        .initialization_script(script)
        // Let the page handle file drops for attachments.
        .disable_drag_drop_handler()
        .on_navigation(move |url| navigate(&handle, url))
        .on_new_window(|url, _| {
            external(&url);
            tauri::webview::NewWindowResponse::Deny
        })
        .on_download(|_, _| false);
    // Keep the sync connection and timers running while hidden in the tray
    // (WebView2 otherwise throttles background pages). wry's defaults kept.
    #[cfg(windows)]
    let builder = builder.additional_browser_args(
        "--disable-features=msWebOOUI,msPdfOOUI,msSmartScreenProtection --disable-background-timer-throttling --disable-renderer-backgrounding --disable-backgrounding-occluded-windows");
    builder.build()?;
    if !visible { tray::set_page_hidden(app, true); }
    Ok(())
}

/// Only shell pages stay in the webview. The web login link starts native
/// login; any other http(s) link opens in the system browser.
fn navigate(app: &tauri::AppHandle, url: &url::Url) -> bool {
    if config::is_app_url(url) {
        if url.path() == "/auth/login" {
            if let Some(desktop) = app.try_state::<Arc<Desktop>>() {
                let message = r#"{"id":"nav-login","method":"login","args":{}}"#;
                let desktop = desktop.inner().clone();
                tauri::async_runtime::spawn(async move { desktop.handle(message).await; });
            }
            return false;
        }
        return url.path() == "/" || url.path() == "/index.html";
    }
    external(url);
    false
}

fn external(url: &url::Url) {
    if matches!(url.scheme(), "https" | "http") && url.host_str().is_some_and(|h| !h.is_empty()) && url.username().is_empty() {
        let _ = open::that_detached(url.as_str());
    }
}
