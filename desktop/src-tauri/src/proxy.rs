//! Same-origin reverse proxy: the page talks to its own origin, the shell
//! forwards only /api/, /_matrix/ and /auth/ to the fixed gateway origin.
//! No other host or prefix is reachable. Cookies are neither forwarded nor
//! stored (the desktop app authenticates with a Bearer token). Request and
//! response bodies are never logged.
use crate::config::{ORIGIN, PROXY_PREFIXES};
use http::{header, HeaderName, HeaderValue, Method, Request, Response, StatusCode};
use std::time::Duration;

const MAX_REQUEST: usize = 64 * 1024 * 1024;
const MAX_RESPONSE: usize = 128 * 1024 * 1024;

const REQUEST_HEADERS: [&str; 9] = [
    "authorization", "content-type", "accept", "accept-language", "range",
    "if-none-match", "if-modified-since", "if-range", "cache-control",
];
const DROPPED_RESPONSE_HEADERS: [&str; 9] = [
    "set-cookie", "set-cookie2", "connection", "keep-alive", "transfer-encoding",
    "content-length", "content-encoding", "strict-transport-security", "alt-svc",
];

/// Only fixed prefixes, no traversal or encoded separators.
pub fn allowed(path: &str) -> bool {
    let lower = path.to_ascii_lowercase();
    PROXY_PREFIXES.iter().any(|p| path.starts_with(p))
        && !path.contains("..") && !path.contains("//") && !path.contains('\\')
        && !lower.contains("%2e") && !lower.contains("%2f") && !lower.contains("%5c")
        // Native login/exchange is performed by the shell itself, never by the page.
        && !path.starts_with("/auth/native/")
}

fn forward_header(name: &str) -> bool {
    REQUEST_HEADERS.contains(&name) || name.starts_with("x-upload-")
}

pub fn error(status: StatusCode, message: &str) -> Response<Vec<u8>> {
    Response::builder().status(status)
        .header(header::CONTENT_TYPE, "application/json; charset=utf-8")
        .header(header::CACHE_CONTROL, "no-store")
        .body(serde_json::json!({ "error": message }).to_string().into_bytes()).unwrap()
}

pub async fn forward(http: &reqwest::Client, request: Request<Vec<u8>>) -> Response<Vec<u8>> {
    let uri = request.uri();
    let path = uri.path();
    if !allowed(path) { return error(StatusCode::NOT_FOUND, "不支持的请求路径"); }
    let method = request.method().clone();
    if ![Method::GET, Method::HEAD, Method::POST, Method::PUT, Method::DELETE].contains(&method) {
        return error(StatusCode::METHOD_NOT_ALLOWED, "不支持的请求方法");
    }
    if request.body().len() > MAX_REQUEST { return error(StatusCode::PAYLOAD_TOO_LARGE, "请求过大"); }
    let target = match uri.query() { Some(q) => format!("{ORIGIN}{path}?{q}"), None => format!("{ORIGIN}{path}") };

    let mut out = http.request(method, &target)
        // Matrix /sync long-polls up to ~30 s; leave headroom for slow links.
        .timeout(Duration::from_secs(if path.starts_with("/_matrix/") { 90 } else { 120 }));
    for (name, value) in request.headers() {
        if forward_header(name.as_str()) { out = out.header(name, value); }
    }
    let (_, body) = request.into_parts();
    if !body.is_empty() { out = out.body(body); }

    let upstream = match out.send().await {
        Ok(r) => r,
        Err(e) if e.is_timeout() => return error(StatusCode::GATEWAY_TIMEOUT, "网络超时，正在重试"),
        Err(_) => return error(StatusCode::BAD_GATEWAY, "网络不可用，正在重试"),
    };
    let status = upstream.status();
    if upstream.content_length().is_some_and(|n| n as usize > MAX_RESPONSE) {
        return error(StatusCode::BAD_GATEWAY, "响应过大");
    }
    let mut response = Response::builder().status(status);
    for (name, value) in upstream.headers() {
        if DROPPED_RESPONSE_HEADERS.contains(&name.as_str()) { continue; }
        if *name == header::LOCATION {
            // Keep same-origin redirects same-origin for the page.
            if let Some(v) = value.to_str().ok().and_then(|v| v.strip_prefix(ORIGIN)) {
                if let Ok(v) = HeaderValue::from_str(if v.is_empty() { "/" } else { v }) {
                    response = response.header(header::LOCATION, v);
                }
                continue;
            }
        }
        response = response.header(name.clone(), value.clone());
    }
    let bytes = match upstream.bytes().await {
        Ok(b) if b.len() <= MAX_RESPONSE => b.to_vec(),
        Ok(_) => return error(StatusCode::BAD_GATEWAY, "响应过大"),
        Err(_) => return error(StatusCode::BAD_GATEWAY, "网络中断，正在重试"),
    };
    response.header(HeaderName::from_static("x-content-type-options"), "nosniff")
        .body(bytes).unwrap_or_else(|_| error(StatusCode::BAD_GATEWAY, "响应无效"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn prefixes_and_traversal() {
        for ok in ["/api/session", "/_matrix/client/v3/sync?x=1", "/auth/login", "/api/uploads/abc/chunks"] {
            assert!(allowed(ok.split('?').next().unwrap()), "{ok}");
        }
        for bad in ["/", "/index.html", "/assets/x.js", "/apix", "/api", "/api/../admin", "/api/%2e%2e/x",
                    "/api/a%2Fb", "/api//x", "/api\\x", "/_synapse/admin", "/auth/native/exchange",
                    "/auth/native/login", "/admin/api/x", "/.well-known/x"] {
            assert!(!allowed(bad), "{bad}");
        }
    }

    #[test]
    fn header_whitelist() {
        assert!(forward_header("authorization"));
        assert!(forward_header("x-upload-offset"));
        assert!(!forward_header("cookie"));
        assert!(!forward_header("origin"));
        assert!(!forward_header("referer"));
        assert!(!forward_header("host"));
        assert!(!forward_header("x-forwarded-for"));
    }
}
