"""
OpenID Connect Provider（Authorization Code + PKCE），给 LibreChat 之类的 RP 用。

设计要点
  · 只做 code flow：LibreChat 用的 openid-client v6 走 discovery + code。
  · 签名密钥 RS256，PEM 落盘（.oidc_key.pem），重启后 JWKS 不变，
    否则已经发出去的 id_token / access_token 全部失效。
  · code / token 存 account.db，单进程 Flask 重启也不掉线。
  · 用户身份由 website.py 的 session 提供，这里只负责协议部分。

website.py 里通过 init_oidc(app, deps) 挂载，deps 见 OIDCDeps。
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode, urlsplit

from flask import Response, jsonify, redirect, request, session
from joserfc import jwt
from joserfc.jwk import KeySet, RSAKey

# ── 生命周期（秒）──────────────────────────────────────────────────────────────
CODE_TTL = 60          # 授权码：一次性，用完即删
ACCESS_TTL = 3600      # access_token / id_token
REFRESH_TTL = 30 * 86400
CLEAN_EVERY = 300      # 过期记录清理节流


@dataclass
class OIDCDeps:
    """website.py 注入的钩子，避免 oidc_provider 反向 import 造成循环依赖。"""
    issuer: str
    db_conn: Callable[[], sqlite3.Connection]
    get_user: Callable[[str], dict | None]
    current_username: Callable[[], str | None]
    claims_for: Callable[[dict], dict]
    login_redirect: Callable[[str], Response]
    deny_page: Callable[[str, str], tuple[str, int]]
    key_path: Path


_d: OIDCDeps | None = None
_keyset: KeySet | None = None
_signing_key: RSAKey | None = None
_last_clean = 0.0


# ── 客户端注册表（环境变量，多客户端用逗号分隔的 redirect_uris）─────────────────
def _load_clients() -> dict[str, dict]:
    """
    OIDC_CLIENTS 可给 JSON；否则用单客户端的简单变量：
      oidc_client_id / oidc_client_secret / oidc_redirect_uris
    """
    raw = os.environ.get("OIDC_CLIENTS", "").strip()
    if raw:
        try:
            data = json.loads(raw)
            return {
                str(c["client_id"]): {
                    "client_secret": str(c.get("client_secret") or ""),
                    "redirect_uris": [str(u).strip() for u in c.get("redirect_uris", []) if str(u).strip()],
                    "name": str(c.get("name") or c["client_id"]),
                }
                for c in data
            }
        except Exception:
            pass

    cid = os.environ.get("oidc_client_id", "librechat")
    uris = [u.strip() for u in os.environ.get(
        "oidc_redirect_uris",
        "https://chat.yitiao.org/oauth/openid/callback",
    ).split(",") if u.strip()]
    return {cid: {
        "client_secret": os.environ.get("oidc_client_secret", ""),
        "redirect_uris": uris,
        "name": os.environ.get("oidc_client_name", cid),
    }}


CLIENTS = _load_clients()
CLIENTS['yidt'] = {'client_secret':'', 'redirect_uris':['https://yidt.yitiao.org/oauth/callback'], 'name':'YIDT'}
CLIENTS['yichat'] = {'client_secret':'', 'redirect_uris':['https://chat.yitiao.org/auth/callback'], 'name':'YiChat'}


# ── 存储 ─────────────────────────────────────────────────────────────────────
def _init_db() -> None:
    conn = _d.db_conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS oidc_codes (
                code           TEXT PRIMARY KEY,
                client_id      TEXT NOT NULL,
                username       TEXT NOT NULL,
                redirect_uri   TEXT NOT NULL,
                scope          TEXT NOT NULL,
                nonce          TEXT,
                code_challenge TEXT,
                challenge_method TEXT,
                auth_time      INTEGER NOT NULL,
                expires_at     INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS oidc_tokens (
                token_hash  TEXT PRIMARY KEY,
                kind        TEXT NOT NULL,          -- 'access' | 'refresh'
                client_id   TEXT NOT NULL,
                username    TEXT NOT NULL,
                scope       TEXT NOT NULL,
                auth_time   INTEGER NOT NULL,
                expires_at  INTEGER NOT NULL
            )
            """
        )
        for table in ('oidc_codes','oidc_tokens'):
            if 'sid' not in {r[1] for r in conn.execute('PRAGMA table_info('+table+')')}:
                conn.execute('ALTER TABLE '+table+' ADD COLUMN sid TEXT')
        conn.commit()
    finally:
        conn.close()


def _sweep(force: bool = False) -> None:
    global _last_clean
    now = time.time()
    if not force and now - _last_clean < CLEAN_EVERY:
        return
    _last_clean = now
    conn = _d.db_conn()
    try:
        conn.execute("DELETE FROM oidc_codes WHERE expires_at < ?", (int(now),))
        conn.execute("DELETE FROM oidc_tokens WHERE expires_at < ?", (int(now),))
        conn.commit()
    finally:
        conn.close()


def _hash(token: str) -> str:
    """库里只存哈希，DB 泄露不等于 token 泄露。"""
    return hashlib.sha256(token.encode()).hexdigest()


# ── 签名密钥 ─────────────────────────────────────────────────────────────────
def _load_key() -> None:
    global _keyset, _signing_key
    path = _d.key_path
    if path.exists():
        key = RSAKey.import_key(path.read_bytes())
    else:
        key = RSAKey.generate_key(2048, private=True)
        path.write_bytes(key.as_pem(private=True))
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    # kid 用公钥指纹：内容决定 kid，换了密钥 RP 能自然发现
    key = RSAKey.import_key(
        key.as_pem(private=True),
        parameters={"use": "sig", "alg": "RS256", "kid": key.thumbprint()},
    )
    _signing_key = key
    _keyset = KeySet([key])


def _sign(payload: dict) -> str:
    return jwt.encode({"alg": "RS256", "kid": _signing_key.kid}, payload, _signing_key)


def _err(error: str, desc: str, status: int = 400):
    resp = jsonify({"error": error, "error_description": desc})
    resp.status_code = status
    if status == 401:
        resp.headers["WWW-Authenticate"] = 'Bearer error="invalid_token"'
    return resp


def _redirect_err(redirect_uri: str, error: str, desc: str, state: str | None):
    """错误按 spec 回传给 RP，让 LibreChat 能显示原因而不是白屏。"""
    params = {"error": error, "error_description": desc}
    if state:
        params["state"] = state
    sep = "&" if urlsplit(redirect_uri).query else "?"
    return redirect(redirect_uri + sep + urlencode(params))


def _scopes(raw: str) -> list[str]:
    return [s for s in (raw or "").replace("+", " ").split(" ") if s]


# ── 路由 ─────────────────────────────────────────────────────────────────────
def init_oidc(app, deps: OIDCDeps) -> None:
    global _d
    _d = deps
    _init_db()
    _load_key()

    iss = deps.issuer.rstrip("/")

    @app.route("/.well-known/openid-configuration")
    def oidc_discovery():
        return jsonify({
            "issuer": iss,
            "authorization_endpoint": f"{iss}/oidc/authorize",
            "token_endpoint": f"{iss}/oidc/token",
            "userinfo_endpoint": f"{iss}/oidc/userinfo",
            "jwks_uri": f"{iss}/oidc/jwks",
            "end_session_endpoint": f"{iss}/oidc/logout",
            "scopes_supported": ["openid", "profile", "email"],
            "response_types_supported": ["code"],
            "response_modes_supported": ["query"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "token_endpoint_auth_methods_supported": [
                "client_secret_basic", "client_secret_post", "none",
            ],
            "code_challenge_methods_supported": ["S256", "plain"],
            "claims_supported": [
                "sub", "iss", "aud", "exp", "iat", "auth_time", "nonce",
                "name", "preferred_username", "picture", "email",
                "email_verified", "role", "roles",
            ],
        })

    @app.route("/oidc/jwks")
    def oidc_jwks():
        return jsonify(_keyset.as_dict(private=False))

    @app.route("/oidc/authorize")
    def oidc_authorize():
        _sweep()
        client_id = request.args.get("client_id", "")
        redirect_uri = request.args.get("redirect_uri", "")
        response_type = request.args.get("response_type", "")
        state = request.args.get("state")
        scope = request.args.get("scope", "openid")

        client = CLIENTS.get(client_id)
        if not client:
            return _d.deny_page("应用未注册",
                                f"client_id「{client_id or '(空)'}」不在授权列表中。")
        if redirect_uri not in client["redirect_uris"]:
            # redirect_uri 没通过白名单时绝不能重定向回去，否则成了开放重定向
            return _d.deny_page("回调地址不匹配",
                                "该 redirect_uri 未在服务端登记，请检查应用配置。")

        if response_type != "code":
            return _redirect_err(redirect_uri, "unsupported_response_type",
                                 "仅支持 response_type=code", state)
        if "openid" not in _scopes(scope):
            return _redirect_err(redirect_uri, "invalid_scope",
                                 "scope 必须包含 openid", state)

        challenge = request.args.get("code_challenge")
        method = request.args.get("code_challenge_method", "plain" if challenge else "")
        if challenge and method not in ("S256", "plain"):
            return _redirect_err(redirect_uri, "invalid_request",
                                 "不支持的 code_challenge_method", state)
        if client_id == 'yichat' and (not challenge or method != 'S256'):
            return _redirect_err(redirect_uri, "invalid_request", "YiChat requires S256 PKCE", state)
        # 公开客户端（没配 secret）必须用 PKCE，否则 code 可被冒用
        if not client["client_secret"] and not challenge:
            return _redirect_err(redirect_uri, "invalid_request",
                                 "公开客户端必须使用 PKCE", state)

        username = _d.current_username()
        prompt = _scopes(request.args.get("prompt", ""))

        if not username:
            if "none" in prompt:
                return _redirect_err(redirect_uri, "login_required",
                                     "用户未登录", state)
            # 未登录：带着完整 authorize URL 去 /login/，登录完自动回来
            return _d.login_redirect(request.full_path)

        user = _d.get_user(username)
        if not user:
            return _d.login_redirect(request.full_path)

        # guest 一律拒绝：这里读库而不是读 session，避免降权后仍能拿 token
        if str(user.get("role") or "guest") == "guest" and client_id not in ("yidt", "yichat"):
            return _d.deny_page(
                "权限不足",
                f"账号「{username}」当前为访客（guest），无权登录 "
                f"{client['name']}。请联系管理员升级账号权限。",
            )

        code = secrets.token_urlsafe(32)
        now = int(time.time())
        conn = _d.db_conn()
        try:
            conn.execute(
                "INSERT INTO oidc_codes (code, client_id, username, redirect_uri, scope,"
                " nonce, code_challenge, challenge_method, auth_time, expires_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (code, client_id, username, redirect_uri, scope,
                 request.args.get("nonce"), challenge, method or None,
                 now, now + CODE_TTL),
            )
            conn.execute('UPDATE oidc_codes SET sid=? WHERE code=?',(session.get('yidt_sid'),code))
            conn.commit()
        finally:
            conn.close()

        params = {"code": code}
        if state:
            params["state"] = state
        sep = "&" if urlsplit(redirect_uri).query else "?"
        return redirect(redirect_uri + sep + urlencode(params))

    @app.route("/oidc/token", methods=["POST"])
    def oidc_token():
        _sweep()
        client_id, client_secret = _client_auth()
        client = CLIENTS.get(client_id or "")
        if not client:
            return _err("invalid_client", "client_id 无效", 401)
        if client["client_secret"] and not secrets.compare_digest(
                client_secret or "", client["client_secret"]):
            return _err("invalid_client", "client_secret 不匹配", 401)

        grant = request.form.get("grant_type", "")
        if grant == "authorization_code":
            return _grant_code(client_id, client)
        if grant == "refresh_token":
            return _grant_refresh(client_id)
        return _err("unsupported_grant_type", f"不支持 grant_type={grant or '(空)'}")

    @app.route("/oidc/userinfo", methods=["GET", "POST"])
    def oidc_userinfo():
        auth = request.headers.get("Authorization", "")
        token = auth[7:].strip() if auth[:7].lower() == "bearer " else (
            request.values.get("access_token", "")
        )
        if not token:
            resp = _err("invalid_request", "缺少 access_token", 401)
            resp.headers["WWW-Authenticate"] = "Bearer"
            return resp

        row = _token_row(token, "access")
        if not row:
            return _err("invalid_token", "access_token 无效或已过期", 401)

        user = _d.get_user(row["username"])
        if not user:
            return _err("invalid_token", "用户不存在", 401)
        if str(user.get("role") or "guest") == "guest" and row["client_id"] not in ("yidt", "yichat"):
            # 发 token 之后被降权，userinfo 也要立刻拒绝
            return _err("insufficient_scope", "账号权限不足（guest）", 403)

        claims = _d.claims_for(user)
        if row['client_id'] == 'yichat': claims['sub'] = claims['user_id']
        return jsonify(_filter_claims(claims, _scopes(row["scope"])))

    @app.route("/oidc/logout")
    def oidc_logout():
        """RP-Initiated Logout：只做重定向，本站 session 由 /logout/ 负责。"""
        target = request.args.get("post_logout_redirect_uri", "")
        allowed = {u for c in CLIENTS.values() for u in c["redirect_uris"]}
        origins = {f"{urlsplit(u).scheme}://{urlsplit(u).netloc}" for u in allowed}
        if target:
            o = urlsplit(target)
            if f"{o.scheme}://{o.netloc}" not in origins:
                return _d.deny_page("退出地址不允许",
                                    "post_logout_redirect_uri 未登记。")
            return redirect(target)
        return redirect(f"{iss}/")


def _client_auth() -> tuple[str | None, str | None]:
    """支持 client_secret_basic 与 client_secret_post。"""
    if request.authorization and request.authorization.type == "basic":
        return request.authorization.username, request.authorization.password
    return request.form.get("client_id"), request.form.get("client_secret")


def _verify_pkce(row: sqlite3.Row, verifier: str) -> bool:
    challenge = row["code_challenge"]
    if not challenge:
        return True
    if not verifier:
        return False
    if row["challenge_method"] == "S256":
        import base64
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        calc = base64.urlsafe_b64encode(digest).decode().rstrip("=")
        return secrets.compare_digest(calc, challenge)
    return secrets.compare_digest(verifier, challenge)


def _issue_tokens(client_id: str, username: str, scope: str,
                  auth_time: int, nonce: str | None, sid=None) -> dict:
    now = int(time.time())
    user = _d.get_user(username) or {}
    claims = _d.claims_for(user)
    if client_id == 'yichat':
        claims['sub'] = claims['user_id']
    scopes = _scopes(scope)

    access = secrets.token_urlsafe(32)
    refresh = secrets.token_urlsafe(32)
    conn = _d.db_conn()
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO oidc_tokens (token_hash, kind, client_id,"
            " username, scope, auth_time, expires_at) VALUES (?,?,?,?,?,?,?)",
            [
                (_hash(access), "access", client_id, username, scope, auth_time, now + ACCESS_TTL),
                (_hash(refresh), "refresh", client_id, username, scope, auth_time, now + REFRESH_TTL),
            ],
        )
        conn.execute('UPDATE oidc_tokens SET sid=? WHERE token_hash IN (?,?)',(sid,_hash(access),_hash(refresh)))
        conn.commit()
    finally:
        conn.close()

    payload = {
        "iss": _d.issuer.rstrip("/"),
        "sub": claims["sub"],
        "aud": client_id,
        "iat": now,
        "exp": now + ACCESS_TTL,
        "auth_time": auth_time,
        "azp": client_id,
    }
    if sid:
        payload["sid"] = sid
    if nonce:
        payload["nonce"] = nonce
    payload.update(_filter_claims(claims, scopes))

    return {
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": ACCESS_TTL,
        "refresh_token": refresh,
        "scope": scope,
        "id_token": _sign(payload),
    }


def _filter_claims(claims: dict, scopes: list[str]) -> dict:
    out = {"sub": claims["sub"]}
    if "profile" in scopes:
        for k in ("name", "preferred_username", "nickname", "picture",
                  "updated_at", "role", "roles", "user_id"):
            if claims.get(k) is not None:
                out[k] = claims[k]
    if "email" in scopes:
        for k in ("email", "email_verified"):
            if claims.get(k) is not None:
                out[k] = claims[k]
    return out


def _token_row(token: str, kind: str) -> sqlite3.Row | None:
    conn = _d.db_conn()
    try:
        row = conn.execute(
            "SELECT * FROM oidc_tokens WHERE token_hash=? AND kind=? AND expires_at>?",
            (_hash(token), kind, int(time.time())),
        ).fetchone()
    finally:
        conn.close()
    if row and row['client_id'] in ('yidt','yichat'):
        import yidt_core as y
        with y.connect() as c:
            if not y.sid_valid(c,row['sid'],row['username']):return None
    return row


def _grant_code(client_id: str, client: dict):
    code = request.form.get("code", "")
    redirect_uri = request.form.get("redirect_uri", "")
    conn = _d.db_conn()
    try:
        row = conn.execute("SELECT * FROM oidc_codes WHERE code=?", (code,)).fetchone()
        if row:
            # 授权码一次性：先删再验，重放拿不到第二次
            conn.execute("DELETE FROM oidc_codes WHERE code=?", (code,))
            conn.commit()
    finally:
        conn.close()

    if not row:
        return _err("invalid_grant", "授权码无效或已使用")
    if row["expires_at"] < int(time.time()):
        return _err("invalid_grant", "授权码已过期")
    if row["client_id"] != client_id:
        return _err("invalid_grant", "授权码不属于该客户端")
    if redirect_uri and redirect_uri != row["redirect_uri"]:
        return _err("invalid_grant", "redirect_uri 与授权时不一致")
    if not _verify_pkce(row, request.form.get("code_verifier", "")):
        return _err("invalid_grant", "code_verifier 校验失败")

    if client_id in ('yidt','yichat'):
        import yidt_core as y
        with y.connect() as c:
            if not y.sid_valid(c,row['sid'],row['username']):return _err('invalid_grant','主站会话已退出')
    user = _d.get_user(row["username"])
    if not user:
        return _err("invalid_grant", "用户不存在")
    if str(user.get("role") or "guest") == "guest" and client_id not in ("yidt", "yichat"):
        return _err("access_denied", "账号权限不足（guest）", 403)

    return jsonify(_issue_tokens(client_id, row["username"], row["scope"],
                                 row["auth_time"], row["nonce"], row["sid"]))


def _grant_refresh(client_id: str):
    token = request.form.get("refresh_token", "")
    row = _token_row(token, "refresh")
    if not row:
        return _err("invalid_grant", "refresh_token 无效或已过期")
    if row["client_id"] != client_id:
        return _err("invalid_grant", "refresh_token 不属于该客户端")

    if client_id in ('yidt','yichat'):
        import yidt_core as y
        with y.connect() as c:
            if not y.sid_valid(c,row['sid'],row['username']):return _err('invalid_grant','主站会话已退出')
    user = _d.get_user(row["username"])
    if not user:
        return _err("invalid_grant", "用户不存在")
    if str(user.get("role") or "guest") == "guest" and client_id not in ("yidt", "yichat"):
        return _err("access_denied", "账号权限不足（guest）", 403)

    requested = request.form.get("scope")
    scope = row["scope"]
    if requested:
        # 只能缩小范围，不能借刷新提权
        allowed = set(_scopes(scope))
        want = _scopes(requested)
        if not set(want) <= allowed:
            return _err("invalid_scope", "refresh 不能扩大 scope")
        scope = " ".join(want)

    conn = _d.db_conn()
    try:  # 轮换：旧 refresh_token 立即失效
        conn.execute("DELETE FROM oidc_tokens WHERE token_hash=?", (_hash(token),))
        conn.commit()
    finally:
        conn.close()

    return jsonify(_issue_tokens(client_id, row["username"], scope,
                                 row["auth_time"], None, row["sid"]))


def revoke_user_tokens(username: str) -> None:
    """website.py 在改密码/降权/登出时可调用，撤掉该用户所有 OIDC 凭证。"""
    if _d is None:
        return
    conn = _d.db_conn()
    try:
        conn.execute("DELETE FROM oidc_tokens WHERE username=?", (username,))
        conn.execute("DELETE FROM oidc_codes WHERE username=?", (username,))
        conn.commit()
    finally:
        conn.close()
