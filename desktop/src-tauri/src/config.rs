//! Fixed constants. The desktop shell talks to exactly one gateway origin.

/// The only upstream the reverse proxy, shell refresh and login may contact.
pub const ORIGIN: &str = "https://chat.yitiao.org";
/// Matrix user IDs issued by this gateway end with this server name.
pub const MXID_SUFFIX: &str = ":chat.yitiao.org";
/// Path prefixes the page may reach through the reverse proxy.
pub const PROXY_PREFIXES: [&str; 3] = ["/api/", "/_matrix/", "/auth/"];
/// `<meta name="yichat-app-bridge" content="N">` versions this shell implements.
/// An online index.html declaring anything else is ignored in favour of the
/// bundled copy, so a server-side bridge change can never break the desktop app.
pub const BRIDGE_VERSIONS: [u32; 1] = [1];
/// Custom URI scheme serving the shell and proxy (yichat://localhost on Linux,
/// https://yichat.localhost on Windows).
pub const SCHEME: &str = "yichat";
/// Keyring service name and window label.
pub const SERVICE: &str = "org.yitiao.yichat.desktop";
pub const MAIN_WINDOW: &str = "main";

#[cfg(target_os = "windows")]
pub const PLATFORM: &str = "windows";
#[cfg(not(target_os = "windows"))]
pub const PLATFORM: &str = "linux";

/// Same policy as the Android WebShellCache, plus Tauri's IPC endpoints.
pub const CSP: &str = "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; worker-src 'self' blob:; connect-src 'self' ipc: http://ipc.localhost; frame-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'";

/// The page origin as seen by the webview.
pub fn app_origin() -> String {
    if cfg!(target_os = "windows") { format!("https://{SCHEME}.localhost") } else { format!("{SCHEME}://localhost") }
}

pub fn user_agent() -> String {
    format!("YiChat-Desktop/{} ({PLATFORM})", env!("CARGO_PKG_VERSION"))
}

/// True for URLs on the shell origin (custom schemes have opaque `Url::origin`).
pub fn is_app_url(url: &url::Url) -> bool {
    if cfg!(target_os = "windows") {
        url.scheme() == "https" && url.host_str() == Some(&format!("{SCHEME}.localhost")) && url.port().is_none()
    } else {
        url.scheme() == SCHEME && url.host_str() == Some("localhost") && url.port().is_none()
    }
}
