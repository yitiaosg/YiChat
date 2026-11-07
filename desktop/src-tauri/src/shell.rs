//! Hybrid shell cache, mirroring the Android WebShellCache:
//! public framework resources only (index.html, /assets, /brand, /render).
//! Authenticated API responses and media never enter this cache.
//!
//! Serving is cache-first so the window opens instantly offline. A background
//! refresh downloads the online index.html and every asset it references; the
//! new index is adopted (for the next launch) only if its bridge version is
//! supported and all assets were stored. Anything else keeps the bundled copy.
use crate::config::{BRIDGE_VERSIONS, ORIGIN};
use once_cell::sync::Lazy;
use regex::Regex;
use sha2::{Digest, Sha256};
use std::collections::{HashSet, VecDeque};
use std::path::{Path, PathBuf};
use std::time::Duration;

const MAX_FILE: usize = 32 * 1024 * 1024;
const MAX_TOTAL: usize = 96 * 1024 * 1024;
const MAX_FILES: usize = 400;

static PUBLIC_ASSET: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"^/(assets|render|brand)/[A-Za-z0-9_./-]+\.(js|css|wasm|svg|png|jpg|webp|woff2?)$").unwrap()
});
static ASSET_REF: Lazy<Regex> = Lazy::new(|| {
    // Quoted or url()-wrapped reference: "/assets/x", "assets/x" or (inside /assets/) "./x".
    Regex::new(r#"["'(](/assets/|assets/|\./)([A-Za-z0-9_-][A-Za-z0-9_.-]*\.(?:js|css|wasm|svg|png|jpg|webp|woff2?))["')]"#).unwrap()
});
static BRIDGE_META: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r#"<meta\s+name="yichat-app-bridge"\s+content="(\d{1,4})"\s*/?>"#).unwrap()
});

pub fn public_asset(path: &str) -> bool {
    !path.contains("..") && !path.contains('%') && !path.contains("//") && PUBLIC_ASSET.is_match(path)
}

/// Bridge version declared by an index.html, if any.
pub fn bridge_version(html: &str) -> Option<u32> {
    BRIDGE_META.captures(html).and_then(|c| c[1].parse().ok())
}
pub fn compatible(html: &str) -> bool {
    bridge_version(html).is_some_and(|v| BRIDGE_VERSIONS.contains(&v))
}

/// Hashed asset paths referenced from HTML/JS/CSS text, as absolute paths.
/// Relative "./x" references only resolve for files that live in /assets/.
pub fn asset_refs(text: &str, in_assets: bool) -> Vec<String> {
    ASSET_REF.captures_iter(text)
        .filter(|c| in_assets || &c[1] != "./")
        .map(|c| format!("/assets/{}", &c[2]))
        .filter(|p| public_asset(p)).collect()
}

pub fn mime(path: &str) -> &'static str {
    match path.rsplit('.').next().unwrap_or("") {
        "html" | "" => "text/html; charset=utf-8",
        "js" => "text/javascript; charset=utf-8",
        "css" => "text/css; charset=utf-8",
        "wasm" => "application/wasm",
        "svg" => "image/svg+xml",
        "png" => "image/png",
        "jpg" => "image/jpeg",
        "webp" => "image/webp",
        "woff" => "font/woff",
        "woff2" => "font/woff2",
        _ => "application/octet-stream",
    }
}

pub struct ShellCache {
    dir: PathBuf,
    http: reqwest::Client,
    base: String,
}

impl ShellCache {
    pub fn new(dir: PathBuf, http: reqwest::Client) -> Self {
        let _ = std::fs::create_dir_all(&dir);
        Self { dir, http, base: ORIGIN.to_string() }
    }
    fn file(&self, path: &str) -> PathBuf {
        self.dir.join(hex(&Sha256::digest(path.as_bytes())))
    }
    fn index_file(&self) -> PathBuf {
        // Tied to the desktop version: an app update starts from its bundled shell.
        self.dir.join(format!("index-{}.html", env!("CARGO_PKG_VERSION")))
    }

    /// Cached index.html if it is still compatible; otherwise None (use bundled).
    pub fn cached_index(&self) -> Option<Vec<u8>> {
        let bytes = std::fs::read(self.index_file()).ok()?;
        compatible(std::str::from_utf8(&bytes).ok()?).then_some(bytes)
    }
    pub fn cached(&self, path: &str) -> Option<Vec<u8>> {
        std::fs::read(self.file(path)).ok()
    }

    /// Fetch one public resource from the gateway and store it.
    pub async fn fetch(&self, path: &str) -> Option<Vec<u8>> {
        if !public_asset(path) { return None; }
        let bytes = self.download(path).await?;
        write_atomic(&self.file(path), &bytes).ok()?;
        Some(bytes)
    }

    async fn download(&self, path: &str) -> Option<Vec<u8>> {
        let r = self.http.get(format!("{}{path}", self.base)).timeout(Duration::from_secs(20)).send().await.ok()?;
        if !r.status().is_success() || r.content_length().is_some_and(|n| n as usize > MAX_FILE) { return None; }
        let bytes = r.bytes().await.ok()?;
        (bytes.len() <= MAX_FILE).then(|| bytes.to_vec())
    }

    /// Download the online shell. `known` reports assets already available
    /// (cached or bundled) so they are not downloaded again.
    pub async fn refresh(&self, known: impl Fn(&str) -> bool) -> Result<bool, &'static str> {
        let index = self.http.get(format!("{}/", self.base)).timeout(Duration::from_secs(10)).send().await.map_err(|_| "network")?;
        if !index.status().is_success() { return Err("status"); }
        let index = index.bytes().await.map_err(|_| "network")?;
        if index.len() > 2 * 1024 * 1024 { return Err("size"); }
        let html = std::str::from_utf8(&index).map_err(|_| "encoding")?;
        if !compatible(html) { return Err("bridge-version"); }
        if self.cached_index().as_deref() == Some(&index[..]) { return Ok(false); }

        let mut queue: VecDeque<String> = asset_refs(html, false).into();
        let mut seen: HashSet<String> = queue.iter().cloned().collect();
        let (mut files, mut total) = (0usize, 0usize);
        while let Some(path) = queue.pop_front() {
            files += 1;
            if files > MAX_FILES { return Err("too-many-files"); }
            let text = if let Some(bytes) = self.cached(&path) {
                bytes
            } else if known(&path) {
                continue; // Bundled with this build: identical content by hash name.
            } else {
                let bytes = self.fetch(&path).await.ok_or("asset")?;
                total += bytes.len();
                if total > MAX_TOTAL { return Err("too-large"); }
                bytes
            };
            if path.ends_with(".js") || path.ends_with(".css") {
                for r in asset_refs(&String::from_utf8_lossy(&text), true) {
                    if seen.insert(r.clone()) { queue.push_back(r); }
                }
            }
        }
        write_atomic(&self.index_file(), &index).map_err(|_| "write")?;
        Ok(true)
    }
}

fn write_atomic(target: &Path, bytes: &[u8]) -> std::io::Result<()> {
    let tmp = target.with_extension(format!("tmp{}", std::process::id()));
    std::fs::write(&tmp, bytes)?;
    std::fs::rename(&tmp, target)
}

pub fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    /// Minimal HTTP/1.1 server returning fixed bodies (404 otherwise).
    async fn serve(files: HashMap<&'static str, String>) -> String {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let base = format!("http://{}", listener.local_addr().unwrap());
        tokio::spawn(async move {
            loop {
                let (mut s, _) = listener.accept().await.unwrap();
                let mut buf = vec![0; 4096];
                let n = s.read(&mut buf).await.unwrap();
                let line = String::from_utf8_lossy(&buf[..n]).lines().next().unwrap_or("").to_string();
                let path = line.split(' ').nth(1).unwrap_or("/").to_string();
                let r = match files.get(path.as_str()) {
                    Some(b) => format!("HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{b}", b.len()),
                    None => "HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".into(),
                };
                let _ = s.write_all(r.as_bytes()).await;
            }
        });
        base
    }

    fn cache(base: String) -> (ShellCache, PathBuf) {
        let dir = std::env::temp_dir().join(format!("yc-shell-{}", rand::random::<u64>()));
        let mut c = ShellCache::new(dir.clone(), reqwest::Client::new());
        c.base = base;
        (c, dir)
    }

    #[tokio::test]
    async fn refresh_adopts_compatible_index_with_all_assets() {
        let index = r#"<meta name="yichat-app-bridge" content="1"><script src="/assets/main-a1.js"></script>"#.to_string();
        let base = serve(HashMap::from([
            ("/", index.clone()),
            ("/assets/main-a1.js", r#"import("./chat-b2.js")"#.into()),
            ("/assets/chat-b2.js", r#"fetch("/assets/w-c3.wasm")"#.into()),
            ("/assets/w-c3.wasm", "wasm".into()),
        ])).await;
        let (c, dir) = cache(base);
        assert!(c.cached_index().is_none());
        assert_eq!(c.refresh(|_| false).await, Ok(true));
        assert_eq!(c.cached_index().unwrap(), index.as_bytes());
        assert_eq!(c.cached("/assets/w-c3.wasm").unwrap(), b"wasm");
        assert_eq!(c.refresh(|_| false).await, Ok(false)); // unchanged
        let _ = std::fs::remove_dir_all(dir);
    }

    #[tokio::test]
    async fn incompatible_or_incomplete_index_keeps_bundled() {
        let base = serve(HashMap::from([("/", r#"<meta name="yichat-app-bridge" content="2">"#.to_string())])).await;
        let (c, dir) = cache(base);
        assert_eq!(c.refresh(|_| false).await, Err("bridge-version"));
        assert!(c.cached_index().is_none());
        let _ = std::fs::remove_dir_all(dir);

        let base = serve(HashMap::from([("/", r#"<meta name="yichat-app-bridge" content="1"><script src="/assets/gone-x.js"></script>"#.to_string())])).await;
        let (c, dir) = cache(base);
        assert_eq!(c.refresh(|_| false).await, Err("asset"));
        assert!(c.cached_index().is_none());
        // Bundled assets need not be downloaded again.
        assert_eq!(c.refresh(|p| p == "/assets/gone-x.js").await, Ok(true));
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn public_asset_rules() {
        assert!(public_asset("/assets/main-DeslhfnA.js"));
        assert!(public_asset("/brand/yichat.png"));
        assert!(public_asset("/render/parachute-jump.svg"));
        assert!(!public_asset("/api/session"));
        assert!(!public_asset("/assets/../api/x.js"));
        assert!(!public_asset("/assets/%2e%2e/x.js"));
        assert!(!public_asset("/assets//x.js"));
        assert!(!public_asset("/assets/x.html"));
    }

    #[test]
    fn bridge_versions() {
        assert!(compatible(r#"<head><meta name="yichat-app-bridge" content="1"><title>"#));
        assert!(!compatible(r#"<meta name="yichat-app-bridge" content="2">"#));
        assert!(!compatible("<html>no marker</html>"));
    }

    #[test]
    fn finds_vite_references() {
        let html = r#"<script type="module" crossorigin src="/assets/main-DeslhfnA.js"></script><link rel="stylesheet" href="/assets/main-DX9mpqyH.css">"#;
        assert_eq!(asset_refs(html, false), vec!["/assets/main-DeslhfnA.js", "/assets/main-DX9mpqyH.css"]);
        let js = r#"import("./chat-BfA_jlmW.js");const m=["assets/y-1.js"];fetch("/assets/matrix_sdk_crypto_wasm_bg-dMeGppz-.wasm");x="./../api.js""#;
        assert_eq!(asset_refs(js, true), vec!["/assets/chat-BfA_jlmW.js", "/assets/y-1.js", "/assets/matrix_sdk_crypto_wasm_bg-dMeGppz-.wasm"]);
        assert!(asset_refs(r#"src="./x-1.js""#, false).is_empty());
    }
}
