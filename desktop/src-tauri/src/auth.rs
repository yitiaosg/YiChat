//! Native login: system browser + PKCE S256 + RFC 8252 loopback redirect.
//! The authorization code arrives on http://127.0.0.1:<port>/app/callback,
//! is exchanged once, and only the resulting gateway session is kept (in the
//! OS secure store). Codes, verifiers and tokens are never logged.
use crate::config::{MXID_SUFFIX, ORIGIN, PLATFORM};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use once_cell::sync::Lazy;
use rand::RngCore;
use regex::Regex;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::time::Duration;
use subtle::ConstantTimeEq;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};

static OPAQUE: Lazy<Regex> = Lazy::new(|| Regex::new(r"^[A-Za-z0-9_-]{43,128}$").unwrap());
static UID: Lazy<Regex> = Lazy::new(|| Regex::new(r"^[a-f0-9]{32}$").unwrap());

#[derive(Clone, Serialize, Deserialize)]
pub struct Account {
    pub uid: String,
    pub user: String,
    pub mxid: String,
    pub device: String,
    pub token: String,
    #[serde(default)]
    pub new_device: bool,
}

impl Account {
    /// Per-account storage id, same construction as Android `Account.storage`.
    pub fn storage(&self) -> String {
        crate::shell::hex(&Sha256::digest(format!("{ORIGIN}|{}|{}|{}", self.uid, self.mxid, self.device).as_bytes()))
    }
}

pub struct Attempt {
    pub state: String,
    pub nonce: String,
    pub verifier: String,
}

fn random() -> String {
    let mut bytes = [0u8; 48];
    rand::rngs::OsRng.fill_bytes(&mut bytes);
    URL_SAFE_NO_PAD.encode(bytes)
}

impl Attempt {
    pub fn new() -> Self { Self { state: random(), nonce: random(), verifier: random() } }
    pub fn challenge(&self) -> String { URL_SAFE_NO_PAD.encode(Sha256::digest(self.verifier.as_bytes())) }
    pub fn authorize_url(&self, redirect: &str) -> String {
        let mut url = url::Url::parse(&format!("{ORIGIN}/auth/native/login")).unwrap();
        url.query_pairs_mut()
            .append_pair("state", &self.state).append_pair("nonce", &self.nonce)
            .append_pair("code_challenge", &self.challenge()).append_pair("code_challenge_method", "S256")
            .append_pair("redirect_uri", redirect).append_pair("platform", PLATFORM);
        url.into()
    }
}

/// A user-facing message (safe to show; contains no secrets).
#[derive(Debug)]
pub struct LoginError(pub String);
fn err(m: impl Into<String>) -> LoginError { LoginError(m.into()) }

pub struct Loopback { listener: TcpListener, pub redirect: String }

impl Loopback {
    pub async fn bind() -> Result<Self, LoginError> {
        let listener = TcpListener::bind(("127.0.0.1", 0)).await.map_err(|_| err("无法监听本机登录回调端口"))?;
        let port = listener.local_addr().map_err(|_| err("无法监听本机登录回调端口"))?.port();
        Ok(Self { listener, redirect: format!("http://127.0.0.1:{port}/app/callback") })
    }

    /// Wait (up to 10 minutes) for the browser to deliver code+state.
    /// Unrelated or forged requests are answered and ignored.
    pub async fn wait(self, state: &str) -> Result<String, LoginError> {
        let deadline = tokio::time::Instant::now() + Duration::from_secs(600);
        loop {
            let accepted = tokio::time::timeout_at(deadline, self.listener.accept()).await
                .map_err(|_| err("登录已超时，请重新开始"))?;
            let Ok((stream, peer)) = accepted else { continue };
            if !peer.ip().is_loopback() { continue; }
            if let Some(code) = handle(stream, state).await { return Ok(code); }
        }
    }
}

/// Parse "GET /app/callback?code=..&state=.. HTTP/1.1"; exactly one code and state.
pub fn parse_callback(request_line: &str, state: &str) -> Result<String, &'static str> {
    let mut parts = request_line.split(' ');
    let (Some("GET"), Some(target), Some(version)) = (parts.next(), parts.next(), parts.next()) else { return Err("bad-request") };
    if !version.starts_with("HTTP/1.") || parts.next().is_some() { return Err("bad-request"); }
    let (path, query) = target.split_once('?').unwrap_or((target, ""));
    if path != "/app/callback" { return Err("not-found"); }
    let (mut code, mut got_state, mut count) = (None, None, 0);
    for (k, v) in url::form_urlencoded::parse(query.as_bytes()) {
        count += 1;
        match k.as_ref() {
            "code" if code.is_none() => code = Some(v.into_owned()),
            "state" if got_state.is_none() => got_state = Some(v.into_owned()),
            _ => return Err("bad-request"),
        }
    }
    let (Some(code), Some(got_state)) = (code, got_state) else { return Err("bad-request") };
    if count != 2 || !OPAQUE.is_match(&code) || !OPAQUE.is_match(&got_state) { return Err("bad-request"); }
    if !bool::from(got_state.as_bytes().ct_eq(state.as_bytes())) { return Err("state"); }
    Ok(code)
}

async fn handle(mut stream: TcpStream, state: &str) -> Option<String> {
    let mut buffer = Vec::with_capacity(1024);
    let read = tokio::time::timeout(Duration::from_secs(5), async {
        let mut chunk = [0u8; 1024];
        while !buffer.windows(4).any(|w| w == b"\r\n\r\n") && buffer.len() < 8192 {
            let n = stream.read(&mut chunk).await.ok()?;
            if n == 0 { break; }
            buffer.extend_from_slice(&chunk[..n]);
        }
        Some(())
    }).await;
    if !matches!(read, Ok(Some(()))) { return None; }
    let line = String::from_utf8_lossy(&buffer);
    let line = line.lines().next().unwrap_or("");
    let (status, body, result) = match parse_callback(line, state) {
        Ok(code) => ("200 OK", "登录已完成，请返回 YiChat。此页面可以关闭。", Some(code)),
        Err("not-found") => ("404 Not Found", "未找到。", None),
        Err(_) => ("400 Bad Request", "登录回跳无效，请返回 YiChat 重新发起登录。", None),
    };
    // Static text only; nothing from the request is reflected.
    let html = format!("<!doctype html><html lang=\"zh\"><meta charset=\"utf-8\"><title>YiChat</title><body style=\"font:17px system-ui,sans-serif;max-width:30rem;margin:12vh auto;padding:24px\"><h1>YiChat</h1><p>{body}</p></body></html>");
    let response = format!("HTTP/1.1 {status}\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {}\r\nCache-Control: no-store\r\nReferrer-Policy: no-referrer\r\nX-Content-Type-Options: nosniff\r\nConnection: close\r\n\r\n{html}", html.len());
    let _ = stream.write_all(response.as_bytes()).await;
    let _ = stream.shutdown().await;
    result
}

#[derive(Deserialize)]
struct Exchange { uid: String, user: String, mxid: String, device_id: String, token: String, device_proof: String, new_device: bool, homeserver: String }

/// One attempt; a consumed code cannot be retried.
pub async fn exchange(http: &reqwest::Client, attempt: &Attempt, code: &str, redirect: &str, proof: Option<String>)
    -> Result<(Account, String), LoginError> {
    let mut body = serde_json::json!({
        "code": code, "state": attempt.state, "nonce": attempt.nonce,
        "code_verifier": attempt.verifier, "redirect_uri": redirect,
    });
    if let Some(proof) = proof { body["device_proof"] = proof.into(); }
    let r = http.post(format!("{ORIGIN}/auth/native/exchange")).json(&body)
        .timeout(Duration::from_secs(60)).send().await.map_err(|_| err("网络不可用，请检查网络后重新登录"))?;
    let status = r.status();
    let data: serde_json::Value = r.json().await.map_err(|_| err("服务器响应无效"))?;
    if !status.is_success() {
        // Gateway errors are fixed user-facing strings.
        let message = data.get("error").and_then(|e| e.as_str()).unwrap_or("登录未完成，请重新开始");
        return Err(err(message.chars().take(200).collect::<String>()));
    }
    let e: Exchange = serde_json::from_value(data).map_err(|_| err("服务器响应无效"))?;
    if e.homeserver != ORIGIN || !UID.is_match(&e.uid) || !e.mxid.ends_with(MXID_SUFFIX)
        || e.device_id.is_empty() || e.token.is_empty() || !OPAQUE.is_match(&e.device_proof) {
        return Err(err("服务器响应无效"));
    }
    Ok((Account { uid: e.uid, user: e.user, mxid: e.mxid, device: e.device_id, token: e.token, new_device: e.new_device }, e.device_proof))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn authorize_url_uses_loopback_and_platform() {
        let a = Attempt::new();
        let url = url::Url::parse(&a.authorize_url("http://127.0.0.1:5000/app/callback")).unwrap();
        let q: std::collections::HashMap<_, _> = url.query_pairs().into_owned().collect();
        assert_eq!(url.as_str().split('?').next().unwrap(), "https://chat.yitiao.org/auth/native/login");
        assert_eq!(q.len(), 6);
        assert_eq!(q["redirect_uri"], "http://127.0.0.1:5000/app/callback");
        assert_eq!(q["code_challenge_method"], "S256");
        assert_eq!(q["code_challenge"].len(), 43);
        assert_eq!(q["platform"], PLATFORM);
        assert!(OPAQUE.is_match(&q["state"]) && OPAQUE.is_match(&q["nonce"]) && OPAQUE.is_match(&a.verifier));
    }

    #[test]
    fn callback_parsing() {
        let state = "s".repeat(64);
        let code = "c".repeat(43);
        let ok = format!("GET /app/callback?code={code}&state={state} HTTP/1.1");
        assert_eq!(parse_callback(&ok, &state).unwrap(), code);
        assert_eq!(parse_callback(&format!("GET /app/callback?state={state}&code={code} HTTP/1.1"), &state).unwrap(), code);
        assert_eq!(parse_callback("GET /favicon.ico HTTP/1.1", &state), Err("not-found"));
        assert_eq!(parse_callback(&format!("GET /app/callback?code={code}&state={} HTTP/1.1", "x".repeat(64)), &state), Err("state"));
        for bad in [
            format!("POST /app/callback?code={code}&state={state} HTTP/1.1"),
            format!("GET /app/callback?code={code}&state={state}&x=1 HTTP/1.1"),
            format!("GET /app/callback?code={code}&code={code}&state={state} HTTP/1.1"),
            format!("GET /app/callback?code=short&state={state} HTTP/1.1"),
            format!("GET /app/callback?state={state} HTTP/1.1"),
        ] { assert_eq!(parse_callback(&bad, &state), Err("bad-request"), "{bad}"); }
    }

    #[tokio::test]
    async fn loopback_receives_code_and_ignores_forgery() {
        let state = "s".repeat(64);
        let code = "c".repeat(43);
        let lb = Loopback::bind().await.unwrap();
        let addr = lb.redirect.trim_start_matches("http://").trim_end_matches("/app/callback").to_string();
        let st = state.clone();
        let waiter = tokio::spawn(async move { lb.wait(&st).await.map_err(|e| e.0) });
        let send = |line: String| { let addr = addr.clone(); async move {
            let mut s = TcpStream::connect(addr).await.unwrap();
            s.write_all(format!("{line}\r\nHost: 127.0.0.1\r\n\r\n").as_bytes()).await.unwrap();
            let mut out = String::new(); s.read_to_string(&mut out).await.unwrap(); out
        }};
        let forged = send(format!("GET /app/callback?code={code}&state={} HTTP/1.1", "x".repeat(64))).await;
        assert!(forged.starts_with("HTTP/1.1 400") && !forged.contains(&code));
        let good = send(format!("GET /app/callback?code={code}&state={state} HTTP/1.1")).await;
        assert!(good.starts_with("HTTP/1.1 200") && !good.contains(&code) && !good.contains(&state));
        assert_eq!(waiter.await.unwrap().unwrap(), code);
    }
}
