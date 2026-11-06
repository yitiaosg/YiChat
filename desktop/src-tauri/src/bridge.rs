//! Native side of the existing `window.YiChatApp` message port (bridge v1).
//! The page sends `{id, method, args}` JSON; the reply is `{id, ok, value|error}`,
//! exactly as the Android MainActivity answers. Only this single command is
//! exposed to the webview (see capabilities/main.json).
use crate::auth::{self, Account, Attempt, Loopback};
use crate::backup::{self, ExportConfig};
use crate::history::{self, HistoryDb};
use crate::records;
use crate::config::{self, ORIGIN, PLATFORM};
use crate::notify;
use crate::proxy;
use crate::secure_store::SecureStore;
use crate::settings::Settings;
use crate::shell::{self, ShellCache};
use base64::{engine::general_purpose::STANDARD, Engine};
use http::{header, Request, Response, StatusCode};
use once_cell::sync::Lazy;
use regex::Regex;
use serde_json::{json, Map, Value};
use std::collections::HashMap;
use std::io::Write;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tauri::{AppHandle, Manager};

static ID: Lazy<Regex> = Lazy::new(|| Regex::new(r"^[A-Za-z0-9_-]{1,64}$").unwrap());
const MAX_DOWNLOAD: u64 = 100_000_000;

/// Error text shown in the web UI. Never carries secrets or internal detail.
pub struct Failure(pub String);
impl<E: std::fmt::Display> From<E> for Failure {
    fn from(_: E) -> Self { Failure("操作未完成，请检查登录、网络和存储空间".into()) }
}
fn fail<T>(message: &str) -> Result<T, Failure> { Err(Failure(message.into())) }

struct Download { file: PathBuf, name: String, expected: u64, written: u64 }

pub struct Desktop {
    pub app: AppHandle,
    pub store: Arc<SecureStore>,
    pub http: reqwest::Client,
    pub shell: ShellCache,
    downloads_dir: PathBuf,
    account: Mutex<Option<Account>>,
    login: Mutex<Option<tauri::async_runtime::JoinHandle<()>>>,
    active_room: Mutex<String>,
    downloads: Mutex<HashMap<String, Download>>,
    pub settings: Settings,
    tray: AtomicBool,
    /// Conversation to open once the page is ready (notification click).
    pending_room: Mutex<String>,
    /// Content requests sent to the page: id -> (room, waiter).
    fetches: Mutex<HashMap<String, tokio::sync::oneshot::Sender<Option<Vec<notify::Message>>>>>,
    /// Latest notification generation per room; older results are dropped.
    generations: Mutex<HashMap<String, u64>>,
    serial: AtomicU64,
    /// Encrypted packet database of the logged-in account (None when locked).
    history: Mutex<Option<Arc<HistoryDb>>>,
    data_dir: PathBuf,
    /// Serializes catch-up / snapshot / export runs.
    backup_run: tokio::sync::Mutex<()>,
    backup_error: Mutex<Option<String>>,
}

/// Quick reachability probe for the gateway (no request is sent).
pub async fn online() -> bool {
    tokio::time::timeout(Duration::from_millis(1500), tokio::net::TcpStream::connect("chat.yitiao.org:443"))
        .await.is_ok_and(|r| r.is_ok())
}

impl Desktop {
    pub fn new(app: AppHandle, store: Arc<SecureStore>) -> Result<Self, Box<dyn std::error::Error>> {
        let http = reqwest::Client::builder()
            .user_agent(config::user_agent())
            .redirect(reqwest::redirect::Policy::none())
            .connect_timeout(Duration::from_secs(10))
            .https_only(true)
            .build()?;
        let cache = app.path().app_cache_dir()?;
        let data_dir = app.path().app_data_dir()?;
        let downloads_dir = cache.join("downloads");
        let _ = std::fs::remove_dir_all(&downloads_dir); // abandoned temporary downloads only
        std::fs::create_dir_all(&downloads_dir)?;
        let account = store.get("session").and_then(|s| serde_json::from_str(&s).ok());
        let settings = Settings::load(app.path().app_config_dir()?.join("settings.json"));
        Ok(Self {
            shell: ShellCache::new(cache.join("web-shell"), http.clone()),
            app, store, http, downloads_dir,
            account: Mutex::new(account), login: Mutex::default(),
            active_room: Mutex::default(), downloads: Mutex::default(),
            settings, tray: AtomicBool::new(false), pending_room: Mutex::default(),
            fetches: Mutex::default(), generations: Mutex::default(), serial: AtomicU64::new(0),
            history: Mutex::default(), data_dir, backup_run: tokio::sync::Mutex::new(()), backup_error: Mutex::default(),
        })
    }

    fn bundled(&self, path: &str) -> Option<Vec<u8>> {
        let path = if path == "/" { "index.html" } else { path.trim_start_matches('/') };
        self.app.asset_resolver().get(path.to_string()).map(|a| a.bytes().to_vec())
    }

    // ---------------------------------------------------------------- protocol

    /// Serves `yichat://localhost/*` (Linux) or `https://yichat.localhost/*` (Windows).
    pub async fn serve(&self, request: Request<Vec<u8>>) -> Response<Vec<u8>> {
        let expected_host = if cfg!(target_os = "windows") { "yichat.localhost" } else { "localhost" };
        if request.uri().host() != Some(expected_host) { return proxy::error(StatusCode::FORBIDDEN, "来源无效"); }
        let path = request.uri().path().to_string();
        if config::PROXY_PREFIXES.iter().any(|p| path.starts_with(p)) {
            return proxy::forward(&self.http, request).await;
        }
        if request.method() != http::Method::GET { return proxy::error(StatusCode::METHOD_NOT_ALLOWED, "不支持的请求方法"); }
        if path == "/" || path == "/index.html" {
            let html = self.shell.cached_index().or_else(|| self.bundled("/"));
            return match html {
                Some(body) => Response::builder()
                    .header(header::CONTENT_TYPE, "text/html; charset=utf-8")
                    .header(header::CACHE_CONTROL, "no-store")
                    .header("Content-Security-Policy", config::CSP)
                    .header("X-Content-Type-Options", "nosniff")
                    .header("Referrer-Policy", "no-referrer")
                    .body(body).unwrap(),
                None => proxy::error(StatusCode::SERVICE_UNAVAILABLE, "界面资源缺失"),
            };
        }
        if !shell::public_asset(&path) { return proxy::error(StatusCode::NOT_FOUND, "未找到"); }
        // Hashed /assets are immutable: cache, then bundled. Others prefer bundled.
        let mut body = if path.starts_with("/assets/") {
            self.shell.cached(&path).or_else(|| self.bundled(&path))
        } else {
            self.bundled(&path).or_else(|| self.shell.cached(&path))
        };
        if body.is_none() { body = self.shell.fetch(&path).await; }
        match body {
            Some(body) => Response::builder()
                .header(header::CONTENT_TYPE, shell::mime(&path))
                .header(header::CACHE_CONTROL, if path.starts_with("/assets/") { "public, max-age=31536000, immutable" } else { "no-cache" })
                .header("X-Content-Type-Options", "nosniff")
                .body(body).unwrap(),
            None => proxy::error(StatusCode::NOT_FOUND, "未找到"),
        }
    }

    pub async fn refresh_shell(&self) {
        let result = self.shell.refresh(|p| self.bundled(p).is_some()).await;
        if let Err(category) = result { log::info!("shell refresh skipped: {category}"); }
    }

    // ------------------------------------------------------------------ bridge

    pub async fn handle(self: &Arc<Self>, raw: &str) -> String {
        let Ok(Value::Object(data)) = serde_json::from_str::<Value>(raw) else { return String::new() };
        let id = data.get("id").and_then(Value::as_str).unwrap_or("");
        if !ID.is_match(id) { return String::new(); }
        let method = data.get("method").and_then(Value::as_str).unwrap_or("");
        let empty = Map::new();
        let args = data.get("args").and_then(Value::as_object).unwrap_or(&empty);
        let reply = match self.dispatch(method, args).await {
            Ok(value) => json!({ "id": id, "ok": true, "value": value }),
            Err(Failure(error)) => json!({ "id": id, "ok": false, "error": error }),
        };
        reply.to_string()
    }

    async fn dispatch(self: &Arc<Self>, method: &str, args: &Map<String, Value>) -> Result<Value, Failure> {
        let s = |k: &str| args.get(k).and_then(Value::as_str).unwrap_or("").to_string();
        match method {
            "session" => Ok(self.session().await),
            "desktopStatus" => Ok(json!({
                "bridge": 1, "platform": PLATFORM, "persistent": self.store.persistent(),
                "reason": self.store.reason(),
            })),
            "login" => { self.start_login(!args.get("newDevice").and_then(Value::as_bool).unwrap_or(false)); Ok(Value::Null) }
            "lock" => { self.lock(); Ok(Value::Null) }
            "logout" => Ok(json!({ "pending": self.logout().await })),
            "notificationStatus" => Ok(json!({
                "enabled": self.settings.get().notifications, "permitted": notify::available(), "background_supported": true,
            })),
            "configureNotifications" => {
                let enabled = args.get("enabled").and_then(Value::as_bool).unwrap_or(false);
                self.settings.update(|v| v.notifications = enabled);
                if !enabled { notify::cancel(None); }
                Ok(Value::Null)
            }
            "backgroundSettings" => {
                self.notice_info(if self.tray_available() {
                    "关闭窗口后，YiChat 会在系统托盘中保持连接并接收新消息。可在托盘菜单中设置“开机时启动”和“通知显示消息预览”，或选择“退出 YiChat”完全退出。"
                } else {
                    "当前桌面环境没有可用的系统托盘，关闭窗口会退出 YiChat，退出后不再接收新消息。"
                });
                Ok(Value::Null)
            }
            "notificationSettings" => { open_notification_settings(); Ok(Value::Null) }
            "notifyRoom" => { self.notify_room(args, true); Ok(Value::Null) }
            "notificationResult" => {
                let id = s("id");
                let messages = if args.get("ok").and_then(Value::as_bool) == Some(true) {
                    args.get("messages").cloned().and_then(|m| serde_json::from_value::<Vec<notify::Message>>(m).ok())
                } else { None };
                if let Some(waiter) = self.fetches.lock().unwrap().remove(&id) { let _ = waiter.send(messages); }
                Ok(Value::Null)
            }
            "cancelNotification" => {
                let room = s("room");
                if notify::valid_room(&room) { self.generations.lock().unwrap().remove(&room); notify::cancel(Some(&room)); }
                Ok(Value::Null)
            }
            "notificationActiveRoom" => {
                let room = s("room");
                if !(room.is_empty() || (room.starts_with('!') && room.len() <= 255)) { return fail("会话无效"); }
                *self.active_room.lock().unwrap() = room; Ok(Value::Null)
            }
            "notificationNames" => Ok(Value::Null),
            "pendingNotification" => Ok(json!({ "room": std::mem::take(&mut *self.pending_room.lock().unwrap()) })),
            "saveStart" => self.save_start(&s("name"), args.get("bytes").and_then(Value::as_i64).unwrap_or(-1)),
            "saveChunk" => self.save_chunk(&s("transfer"), &s("data")),
            "saveCancel" => { if let Some(d) = self.downloads.lock().unwrap().remove(&s("transfer")) { let _ = std::fs::remove_file(d.file); } Ok(Value::Null) }
            "saveFinish" => self.save_finish(&s("transfer")).await,
            // Debug builds only: feature probe used by the local smoke test.
            // Debug builds only: drive the notification pipeline without a session.
            #[cfg(debug_assertions)]
            "debugNotify" if std::env::var_os("YICHAT_DESKTOP_PROBE").is_some() => { self.notify_room(args, false); Ok(Value::Null) }
            #[cfg(debug_assertions)]
            "debugClose" if std::env::var_os("YICHAT_DESKTOP_PROBE").is_some() => {
                let w = self.app.get_webview_window(config::MAIN_WINDOW).ok_or(Failure("no window".into()))?;
                w.close()?;
                tokio::time::sleep(Duration::from_millis(800)).await;
                Ok(json!({ "tray": self.tray_available(), "visibleAfterClose": w.is_visible().unwrap_or(true) }))
            }
            #[cfg(debug_assertions)]
            "debugProbe" if std::env::var_os("YICHAT_DESKTOP_PROBE").is_some() => {
                eprintln!("YICHAT_PROBE {}", Value::Object(args.clone()));
                Ok(Value::Null)
            }
            "recordRooms" => self.with_history(records::rooms).await,
            "recordDays" => { let (r, a, b) = (s("room"), s("start"), s("end")); self.with_history(move |db| records::days(db, &r, &a, &b)).await }
            "recordPage" => { let (r, d, a) = (s("room"), s("day"), s("after")); self.with_history(move |db| records::page(db, &r, &d, &a)).await }
            "backupStatus" => Ok(self.backup_status()),
            "backupNow" => { self.run_backup(true, false).await; Ok(self.backup_status()) }
            "exportNow" => { self.run_export().await?; Ok(self.backup_status()) }
            "stopExport" => {
                let db = self.current_history().ok_or(Failure("请先登录".into()))?;
                self.store.delete(&format!("export.{}", db.storage));
                Ok(self.backup_status())
            }
            "configureExport" => self.configure_export(args.get("days").and_then(Value::as_i64).unwrap_or(0), &s("password")).await,
            _ => fail("桌面版暂未提供此功能"),
        }
    }

    async fn session(&self) -> Value {
        let online = online().await;
        if online { self.complete_pending_logout().await; }
        let account = self.account.lock().unwrap().clone();
        json!({
            "online": online, "version": env!("CARGO_PKG_VERSION"), "bridge": 1, "platform": PLATFORM,
            "capabilities": { "notifications": true, "backgroundNotifications": true },
            "pendingLogout": self.store.get("pending-logout").is_some(),
            "persistent": self.store.persistent(),
            "session": account.map(|a| json!({
                "uid": a.uid, "user": a.user, "mxid": a.mxid, "device": a.device, "device_id": a.device, "token": a.token,
            })),
        })
    }

    fn start_login(self: &Arc<Self>, resume: bool) {
        let me = self.clone();
        let task = tauri::async_runtime::spawn(async move {
            if let Err(e) = me.login(resume).await { me.notice(&e.0); }
        });
        // A new request replaces any attempt still waiting for the browser.
        if let Some(previous) = self.login.lock().unwrap().replace(task) { previous.abort(); }
    }

    async fn login(&self, resume: bool) -> Result<(), auth::LoginError> {
        self.complete_pending_logout().await;
        let attempt = Attempt::new();
        let loopback = Loopback::bind().await?;
        let redirect = loopback.redirect.clone();
        open::that_detached(attempt.authorize_url(&redirect)).map_err(|_| auth::LoginError("无法打开系统浏览器".into()))?;
        let code = loopback.wait(&attempt.state).await?;
        let proof = if resume { self.store.get("device-resume") } else { None };
        let (account, proof) = auth::exchange(&self.http, &attempt, &code, &redirect, proof).await?;
        self.store.put("session", &serde_json::to_string(&account).unwrap()).map_err(auth::LoginError)?;
        self.store.put("device-resume", &proof).map_err(auth::LoginError)?;
        *self.account.lock().unwrap() = Some(account);
        self.open_history();
        self.reload();
        Ok(())
    }

    fn lock(&self) {
        self.store.delete("session");
        *self.account.lock().unwrap() = None;
        // Close the packet database; it stays encrypted on disk.
        *self.history.lock().unwrap() = None;
        // Nothing from the previous session may surface afterwards.
        self.fetches.lock().unwrap().clear();
        self.generations.lock().unwrap().clear();
        self.pending_room.lock().unwrap().clear();
        notify::cancel(None);
    }

    /// Lock locally first, then revoke on the server; retry later if offline.
    async fn logout(&self) -> bool {
        if let Some(token) = self.account.lock().unwrap().as_ref().map(|a| a.token.clone()) {
            let _ = self.store.put("pending-logout", &token);
        }
        self.lock();
        self.complete_pending_logout().await;
        self.store.get("pending-logout").is_some()
    }

    async fn complete_pending_logout(&self) {
        let Some(token) = self.store.get("pending-logout") else { return };
        let r = self.http.post(format!("{ORIGIN}/api/logout")).bearer_auth(token).json(&json!({}))
            .timeout(Duration::from_secs(15)).send().await;
        if let Ok(r) = r { if r.status().is_success() || r.status() == StatusCode::UNAUTHORIZED { self.store.delete("pending-logout"); } }
    }

    fn reload(&self) {
        if let Some(w) = self.app.get_webview_window(config::MAIN_WINDOW) {
            if let Ok(url) = format!("{}/", config::app_origin()).parse() { let _ = w.navigate(url); }
            let _ = w.show(); let _ = w.set_focus();
        }
    }

    // ------------------------------------------------------------ local backup

    fn current_history(&self) -> Option<Arc<HistoryDb>> { self.history.lock().unwrap().clone() }

    /// Open (or create) the logged-in account's SQLCipher database. The random
    /// key lives in the OS secure store; without one the database is in memory.
    pub fn open_history(&self) {
        let Some(account) = self.account.lock().unwrap().clone() else { return };
        let storage = account.storage();
        if self.current_history().is_some_and(|h| h.storage == storage) { return; }
        let result = (|| -> Result<HistoryDb, history::HistoryError> {
            if !self.store.persistent() {
                return HistoryDb::open(&storage, None, zeroize::Zeroizing::new(vec![0; 32]));
            }
            let name = format!("db-key.{storage}");
            let path = self.data_dir.join("accounts").join(&storage).join("history.db");
            let key = match self.store.get(&name).and_then(|k| hex::decode(k).ok()).filter(|k| k.len() == 32) {
                Some(k) => zeroize::Zeroizing::new(k),
                None => {
                    // No key: any existing file is unreadable; keep it aside, never delete silently.
                    if path.exists() { let _ = std::fs::rename(&path, path.with_extension(format!("unreadable-{}", history::now_ms()))); }
                    let mut k = zeroize::Zeroizing::new(vec![0u8; 32]);
                    rand::RngCore::fill_bytes(&mut rand::rngs::OsRng, &mut k);
                    self.store.put(&name, &hex::encode(&*k)).map_err(history::HistoryError)?;
                    k
                }
            };
            HistoryDb::open(&storage, Some(&path), key)
        })();
        match result {
            Ok(db) => { *self.history.lock().unwrap() = Some(Arc::new(db)); }
            Err(_) => { *self.backup_error.lock().unwrap() = Some("本地加密数据库无法打开".into()); }
        }
    }

    async fn with_history<F>(&self, f: F) -> Result<Value, Failure>
    where F: FnOnce(&HistoryDb) -> Result<Value, history::HistoryError> + Send + 'static {
        let db = self.current_history().ok_or(Failure("请先登录".into()))?;
        let storage = db.storage.clone();
        let value = tauri::async_runtime::spawn_blocking(move || f(&db)).await
            .map_err(|_| Failure("操作未完成".into()))?
            .map_err(|e| Failure(if !e.0.is_ascii() { e.0 } else { "本地记录读取失败".into() }))?;
        // The account must not have changed while reading.
        if self.current_history().map(|h| h.storage.clone()) != Some(storage) { return fail("会话已锁定"); }
        Ok(value)
    }

    fn export_config(&self, storage: &str) -> Option<ExportConfig> {
        self.store.get(&format!("export.{storage}")).and_then(|v| serde_json::from_str(&v).ok())
    }

    fn backup_status(&self) -> Value {
        let db = self.current_history();
        let export = db.as_ref().and_then(|d| self.export_config(&d.storage));
        let mut v = backup::status_value(db.as_deref(), export.as_ref());
        if let Some(e) = self.backup_error.lock().unwrap().clone() {
            v["status"] = Value::from(format!("{}；{e}，已保存的数据保留", v["status"].as_str().unwrap_or("")));
        }
        v
    }

    /// Catch up packets, request missing ones, refresh the encrypted snapshot.
    pub async fn run_backup(&self, force: bool, relogin: bool) {
        let _guard = self.backup_run.lock().await;
        let (Some(db), Some(token)) = (self.current_history(), self.account.lock().unwrap().as_ref().map(|a| a.token.clone())) else { return };
        if !online().await { return; }
        let gateway = history::Gateway { http: &self.http, token: &token };
        let result = async {
            gateway.catch_up(&db, relogin).await?;
            gateway.recover(&db).await
        }.await;
        *self.backup_error.lock().unwrap() = match result {
            Ok(()) => None,
            Err(history::CallError::Unauthorized) => Some("登录已失效，请重新登录".into()),
            Err(history::CallError::Failed(_)) => Some("本次同步未完成，将自动重试".into()),
        };
        let last: i64 = db.meta("snapshot-last").and_then(|v| v.parse().ok()).unwrap_or(0);
        if db.persistent && (force || history::now_ms() - last >= backup::SNAPSHOT_INTERVAL_MS) {
            let target = self.data_dir.join("accounts").join(&db.storage).join("history.snapshot.db");
            let snap = db.clone();
            let _ = tauri::async_runtime::spawn_blocking(move || backup::snapshot(&snap, &target)).await;
        }
    }

    async fn configure_export(&self, days: i64, password: &str) -> Result<Value, Failure> {
        if !(1..=3650).contains(&days) || !(12..=1024).contains(&password.chars().count()) {
            return fail("请输入1至3650个整天和至少12字符的独立口令");
        }
        if !self.store.persistent() { return fail("未检测到系统安全存储，无法保存导出口令和目录"); }
        let db = self.current_history().ok_or(Failure("请先登录".into()))?;
        let mut dialog = rfd::AsyncFileDialog::new().set_title("选择 YiChat 备份导出目录");
        if let Some(w) = self.app.get_webview_window(config::MAIN_WINDOW) { dialog = dialog.set_parent(&w); }
        let Some(dir) = dialog.pick_folder().await else { return Ok(json!({ "cancelled": true })) };
        if self.current_history().map(|h| h.storage.clone()) != Some(db.storage.clone()) { return fail("账号已锁定或改变，请重新配置导出"); }
        // As on Android, the first export runs after the chosen interval.
        let config = ExportConfig { dir: dir.path().display().to_string(), password: password.to_string(), days: days as u32, last: history::now_ms() };
        self.store.put(&format!("export.{}", db.storage), &serde_json::to_string(&config)?).map_err(Failure)?;
        Ok(self.backup_status())
    }

    async fn run_export(&self) -> Result<(), Failure> {
        let _guard = self.backup_run.lock().await;
        let db = self.current_history().ok_or(Failure("请先登录".into()))?;
        let account = self.account.lock().unwrap().clone().ok_or(Failure("请先登录".into()))?;
        let mut config = self.export_config(&db.storage).ok_or(Failure("请先选择备份目录".into()))?;
        let (dir, password) = (PathBuf::from(&config.dir), zeroize::Zeroizing::new(config.password.clone().into_bytes()));
        let snap = db.clone();
        tauri::async_runtime::spawn_blocking(move || backup::export(&snap, &account.uid, &account.mxid, &dir, &password)).await
            .map_err(|_| Failure("导出未完成".into()))?
            .map_err(|_| Failure("外部导出未完成，请检查目录权限和存储空间；内部数据保留".into()))?;
        config.last = history::now_ms();
        self.store.put(&format!("export.{}", db.storage), &serde_json::to_string(&config)?).map_err(Failure)?;
        Ok(())
    }

    /// Periodic work while logged in: packet catch-up every minute, snapshot
    /// every 6 h, scheduled export when due.
    pub fn spawn_backup_loop(self: &Arc<Self>) {
        let me = self.clone();
        tauri::async_runtime::spawn(async move {
            let mut first = true;
            loop {
                if me.current_history().is_some() {
                    me.run_backup(false, first).await;
                    first = false;
                    if let Some(db) = me.current_history() {
                        if let Some(c) = me.export_config(&db.storage) {
                            if history::now_ms() - c.last >= i64::from(c.days) * 86_400_000 { let _ = me.run_export().await; }
                        }
                    }
                }
                tokio::time::sleep(Duration::from_secs(60)).await;
            }
        });
    }

    /// `yichat-open://return[?order=…]` after a YIDT binding or payment.
    pub fn open_return(&self, order: Option<&str>) {
        let path = match order { Some(o) => format!("/?order={o}"), None => "/".to_string() };
        if let Some(w) = self.app.get_webview_window(config::MAIN_WINDOW) {
            if let Ok(url) = format!("{}{path}", config::app_origin()).parse() { let _ = w.navigate(url); }
        }
        crate::tray::show_main(&self.app);
    }

    pub fn tray_available(&self) -> bool { self.tray.load(Ordering::Relaxed) }
    pub fn set_tray(&self, available: bool) { self.tray.store(available, Ordering::Relaxed) }

    fn eval(&self, script: &str) {
        if let Some(w) = self.app.get_webview_window(config::MAIN_WINDOW) { let _ = w.eval(script); }
    }

    /// Ask the SDK to retry its sync loop right away (network back / resume).
    pub fn nudge_sync(&self) { self.eval("window.dispatchEvent(new Event('yc:notification-sync'))"); }

    /// Bring the window forward and open a conversation (notification click).
    pub fn open_room(&self, room: &str) {
        if !notify::valid_room(room) { return; }
        *self.pending_room.lock().unwrap() = room.to_string();
        crate::tray::show_main(&self.app);
        self.eval(&format!("window.dispatchEvent(new CustomEvent('yc:open-room',{{detail:{}}}))", Value::from(room)));
    }

    /// From the shared UI's live timeline (it already skipped muted/archived/
    /// read-only rooms and the visible conversation).
    fn notify_room(self: &Arc<Self>, args: &Map<String, Value>, require_session: bool) {
        let s = |k: &str| args.get(k).and_then(Value::as_str).unwrap_or("").to_string();
        let (room, event, kind) = (s("room"), s("event"), s("kind"));
        let settings = self.settings.get();
        if !settings.notifications || (require_session && self.account.lock().unwrap().is_none()) { return; }
        if !notify::valid_room(&room) || !notify::valid_event(&event) || !matches!(kind.as_str(), "private" | "group" | "") { return; }
        let kind = if kind == "group" { "group" } else { "private" }.to_string();
        // Native foreground check, as on Android: never for the conversation in front of the user.
        if let Some(w) = self.app.get_webview_window(config::MAIN_WINDOW) {
            let front = w.is_visible().unwrap_or(false) && w.is_focused().unwrap_or(false) && !w.is_minimized().unwrap_or(false);
            if front && *self.active_room.lock().unwrap() == room { return; }
        }
        let generation = self.serial.fetch_add(1, Ordering::Relaxed) + 1;
        self.generations.lock().unwrap().insert(room.clone(), generation);
        let me = self.clone();
        let click = { let me = me.clone(); let room = room.clone(); move || { let app = me.app.clone(); let handle = app.clone(); let _ = app.run_on_main_thread(move || {
            if let Some(d) = handle.try_state::<Arc<Desktop>>() { d.open_room(&room); }
        }); } };
        if !settings.preview {
            notify::show(&room, notify::GENERIC_TITLE, notify::GENERIC_BODY, click);
            return;
        }
        let label = notify::clean(&s("label"), 80);
        let id = format!("n{generation}");
        let (tx, rx) = tokio::sync::oneshot::channel();
        self.fetches.lock().unwrap().insert(id.clone(), tx);
        // The page verifies membership/unread/redaction and returns plain message text.
        let request = json!({ "id": id, "room": room, "kind": kind, "event": event });
        self.eval(&format!("window.dispatchEvent(new CustomEvent('yc:notification-fetch',{{detail:{request}}}))"));
        tauri::async_runtime::spawn(async move {
            let messages = tokio::time::timeout(Duration::from_secs(12), rx).await.ok().and_then(Result::ok).flatten();
            me.fetches.lock().unwrap().remove(&id);
            if me.generations.lock().unwrap().get(&room) != Some(&generation) { return; } // superseded, read or locked
            if !me.settings.get().notifications { return; }
            let title = if label.is_empty() { notify::GENERIC_TITLE.to_string() } else { label };
            match messages.as_deref().and_then(|m| notify::preview_body(&kind, m)) {
                Some(body) if me.settings.get().preview => notify::show(&room, &title, &body, click),
                Some(_) => notify::show(&room, notify::GENERIC_TITLE, notify::GENERIC_BODY, click),
                // Unread already cleared or content unavailable: Android keeps a placeholder.
                None => notify::show(&room, &title, notify::GENERIC_BODY, click),
            }
        });
    }

    fn notice_info(&self, message: &str) { self.dialog(message, rfd::MessageLevel::Info) }

    pub fn notice(&self, message: &str) { self.dialog(message, rfd::MessageLevel::Warning) }

    fn dialog(&self, message: &str, level: rfd::MessageLevel) {
        let message = message.to_string();
        let parent = self.app.get_webview_window(config::MAIN_WINDOW);
        tauri::async_runtime::spawn(async move {
            let mut dialog = rfd::AsyncMessageDialog::new().set_title("YiChat").set_description(message).set_level(level);
            if let Some(w) = parent.as_ref() { dialog = dialog.set_parent(w); }
            dialog.show().await;
        });
    }

    // --------------------------------------------------------------- downloads

    fn save_start(&self, name: &str, bytes: i64) -> Result<Value, Failure> {
        let mut downloads = self.downloads.lock().unwrap();
        if !downloads.is_empty() { return fail("请先完成当前下载"); }
        if !(0..=MAX_DOWNLOAD as i64).contains(&bytes) { return fail("实际下载文件上限100MB"); }
        let name: String = name.chars().map(|c| if c == '/' || c == '\\' || c.is_control() || ":*?\"<>|".contains(c) { '_' } else { c })
            .take(120).collect();
        let name = if name.trim().is_empty() { "YiChat-download".to_string() } else { name };
        let key = shell::hex(&rand::random::<[u8; 16]>());
        let file = self.downloads_dir.join(&key);
        std::fs::File::create(&file)?;
        downloads.insert(key.clone(), Download { file, name, expected: bytes as u64, written: 0 });
        Ok(json!({ "transfer": key }))
    }

    fn save_chunk(&self, transfer: &str, data: &str) -> Result<Value, Failure> {
        if data.len() > 65536 { return fail("数据块过大"); }
        let bytes = STANDARD.decode(data)?;
        let mut downloads = self.downloads.lock().unwrap();
        let Some(d) = downloads.get_mut(transfer) else { return fail("下载已取消") };
        if d.written + bytes.len() as u64 > d.expected { return fail("文件大小与声明不一致"); }
        std::fs::OpenOptions::new().append(true).open(&d.file)?.write_all(&bytes)?;
        d.written += bytes.len() as u64;
        Ok(Value::Null)
    }

    async fn save_finish(&self, transfer: &str) -> Result<Value, Failure> {
        let Some(d) = self.downloads.lock().unwrap().remove(transfer) else { return fail("下载已取消") };
        let result = async {
            if d.written != d.expected { return fail("文件未完整传输"); }
            let mut dialog = rfd::AsyncFileDialog::new().set_file_name(&d.name);
            if let Some(w) = self.app.get_webview_window(config::MAIN_WINDOW) { dialog = dialog.set_parent(&w); }
            let Some(target) = dialog.save_file().await else { return Ok(json!({ "cancelled": true })) };
            tokio::fs::copy(&d.file, target.path()).await?;
            Ok(json!({ "saved": true }))
        }.await;
        let _ = std::fs::remove_file(&d.file);
        result
    }
}

fn open_notification_settings() {
    #[cfg(windows)]
    { let _ = open::that_detached("ms-settings:notifications"); }
}

/// The only command callable from the page.
#[tauri::command]
pub async fn yichat_bridge(webview: tauri::Webview, desktop: tauri::State<'_, Arc<Desktop>>, message: String) -> Result<String, ()> {
    if webview.label() != config::MAIN_WINDOW || message.len() > 400_000 { return Err(()); }
    // Only the shell origin may use the bridge (never an external page).
    let origin_ok = webview.url().ok().is_some_and(|u| config::is_app_url(&u));
    if !origin_ok { return Err(()); }
    Ok(desktop.inner().handle(&message).await)
}
