//! Native side of the existing `window.YiChatApp` message port (bridge v1).
//! The page sends `{id, method, args}` JSON; the reply is `{id, ok, value|error}`,
//! exactly as the Android MainActivity answers. Only this single command is
//! exposed to the webview (see capabilities/main.json).
use crate::auth::{self, Account, Attempt, Loopback};
use crate::config::{self, ORIGIN, PLATFORM};
use crate::proxy;
use crate::secure_store::SecureStore;
use crate::shell::{self, ShellCache};
use base64::{engine::general_purpose::STANDARD, Engine};
use http::{header, Request, Response, StatusCode};
use once_cell::sync::Lazy;
use regex::Regex;
use serde_json::{json, Map, Value};
use std::collections::HashMap;
use std::io::Write;
use std::path::PathBuf;
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
        let downloads_dir = cache.join("downloads");
        let _ = std::fs::remove_dir_all(&downloads_dir); // abandoned temporary downloads only
        std::fs::create_dir_all(&downloads_dir)?;
        let account = store.get("session").and_then(|s| serde_json::from_str(&s).ok());
        Ok(Self {
            shell: ShellCache::new(cache.join("web-shell"), http.clone()),
            app, store, http, downloads_dir,
            account: Mutex::new(account), login: Mutex::default(),
            active_room: Mutex::default(), downloads: Mutex::default(),
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
            // Phase 2 adds native notifications; until then the UI hides them.
            "notificationStatus" => Ok(json!({ "enabled": false, "permitted": false, "background_supported": false })),
            "notificationActiveRoom" => {
                let room = s("room");
                if !(room.is_empty() || (room.starts_with('!') && room.len() <= 255)) { return fail("会话无效"); }
                *self.active_room.lock().unwrap() = room; Ok(Value::Null)
            }
            "notificationNames" | "cancelNotification" => Ok(Value::Null),
            "pendingNotification" => Ok(json!({ "room": "" })),
            "saveStart" => self.save_start(&s("name"), args.get("bytes").and_then(Value::as_i64).unwrap_or(-1)),
            "saveChunk" => self.save_chunk(&s("transfer"), &s("data")),
            "saveCancel" => { if let Some(d) = self.downloads.lock().unwrap().remove(&s("transfer")) { let _ = std::fs::remove_file(d.file); } Ok(Value::Null) }
            "saveFinish" => self.save_finish(&s("transfer")).await,
            // Debug builds only: feature probe used by the local smoke test.
            #[cfg(debug_assertions)]
            "debugProbe" if std::env::var_os("YICHAT_DESKTOP_PROBE").is_some() => {
                eprintln!("YICHAT_PROBE {}", Value::Object(args.clone()));
                Ok(Value::Null)
            }
            _ => fail("桌面版暂未提供此功能"),
        }
    }

    async fn session(&self) -> Value {
        let online = tokio::time::timeout(Duration::from_millis(1500), tokio::net::TcpStream::connect("chat.yitiao.org:443"))
            .await.is_ok_and(|r| r.is_ok());
        if online { self.complete_pending_logout().await; }
        let account = self.account.lock().unwrap().clone();
        json!({
            "online": online, "version": env!("CARGO_PKG_VERSION"), "bridge": 1, "platform": PLATFORM,
            "capabilities": { "notifications": false, "backgroundNotifications": false },
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
        self.reload();
        Ok(())
    }

    fn lock(&self) {
        self.store.delete("session");
        *self.account.lock().unwrap() = None;
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

    pub fn notice(&self, message: &str) {
        let message = message.to_string();
        let parent = self.app.get_webview_window(config::MAIN_WINDOW);
        tauri::async_runtime::spawn(async move {
            let mut dialog = rfd::AsyncMessageDialog::new().set_title("YiChat").set_description(message).set_level(rfd::MessageLevel::Warning);
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

/// The only command callable from the page.
#[tauri::command]
pub async fn yichat_bridge(webview: tauri::Webview, desktop: tauri::State<'_, Arc<Desktop>>, message: String) -> Result<String, ()> {
    if webview.label() != config::MAIN_WINDOW || message.len() > 400_000 { return Err(()); }
    // Only the shell origin may use the bridge (never an external page).
    let origin_ok = webview.url().ok().is_some_and(|u| config::is_app_url(&u));
    if !origin_ok { return Err(()); }
    Ok(desktop.inner().handle(&message).await)
}
