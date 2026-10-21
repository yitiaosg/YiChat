from __future__ import annotations
import base64
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyotp
import qrcode
import requests
from PIL import Image, ImageOps
from werkzeug.security import check_password_hash, generate_password_hash

import mailer
import oidc_provider
import tools
import points
from authlib.integrations.flask_client import OAuth
from flask import (
    Flask, redirect, render_template_string, request,
    send_from_directory, session, url_for,
)
from werkzeug.middleware.proxy_fix import ProxyFix


BASE_DIR = Path(__file__).resolve().parent

TURNSTILE_SITEKEY = os.environ.get("site_key", "0x4AAAAAADnpqpn-0F3VLb91")
TURNSTILE_SECRET = os.environ.get('secret_key', "")
TURNSTILE_EMAIL_ACTION = "email_code"

# ── Google OAuth2 / OIDC（authlib）──────────────────────────────────────────
GOOGLE_CLIENT_ID = os.environ.get("google_client_id", "")
GOOGLE_CLIENT_SECRET = os.environ.get("google_client_secret", "")
GOOGLE_METADATA_URL = "https://accounts.google.com/.well-known/openid-configuration"

ACCOUNT_DB = Path(os.environ.get("YIDT_ACCOUNT_DB", BASE_DIR / "account.db"))

# ── 二次验证相关常量 ─────────────────────────────────────────────────────────
EMAIL_CODE_LENGTH = 6
EMAIL_CODE_TTL = 600            # 验证码有效期（秒）
EMAIL_CODE_COOLDOWN = 60        # 同账号发信间隔（秒），前后端共同限速
EMAIL_CODE_MAX_TRIES = 6        # 单个验证码允许的错误次数
RECOVERY_CODE_COUNT = 10        # 一次性验证码数量
RECOVERY_CODE_LENGTH = 8        # 一次性验证码位数
TRUSTED_DEVICE_COOKIE = "ytdev"
TRUSTED_DEVICE_DAYS = 90
VERIFY_TICKET_TTL = 300         # 敏感操作验证通过后的有效期（秒）

# ── 注销账号 / 身份冷却 ──────────────────────────────────────────────────────
DELETE_GRACE_DAYS = 7           # 注销后的安全期，期间登录即恢复
IDENTITY_COOLDOWN_DAYS = 21     # 旧邮箱 / Google 账号的冷却期（三周）
DELETE_CONFIRM_WORD = "DELETE"  # 注销最后一步要手抄的词

app = Flask(__name__)
app.secret_key = tools.get_secret_key()
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
# 不设置 SERVER_NAME，避免 session cookie domain 问题
# app.config["SERVER_NAME"] = "yitiao.org"
app.config["PREFERRED_URL_SCHEME"] = "https"
# SESSION_COOKIE_DOMAIN 留空，让浏览器自动匹配当前域名
app.config["SESSION_COOKIE_SECURE"] = True
app.config["SESSION_COOKIE_HTTPONLY"] = True
# OAuth 回调将 SameSite 改为 None，确保跨站回调时cookie不丢失
app.config["SESSION_COOKIE_SAMESITE"] = None

# 信任 Caddy 反向代理的 X-Forwarded-* 头
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

oauth = OAuth(app)

google = oauth.register(
    name="google",
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    server_metadata_url=GOOGLE_METADATA_URL,
    client_kwargs={
        "scope": "openid email profile",
        "code_challenge_method": "S256",  # PKCE
    },
)


def google_ready() -> bool:
    """没配置 client id / secret 时前端隐藏 Google 入口。"""
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


# ── 账号存储：account.db（取代旧的 user.db 与 userdata.db:profile）───────────
def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(ACCOUNT_DB))
    conn.row_factory = sqlite3.Row
    return conn


def init_account_db() -> None:
    conn = _conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                username      TEXT PRIMARY KEY,
                password_hash TEXT,
                role          TEXT NOT NULL DEFAULT 'guest',
                totp_secret   TEXT,
                totp_on_login INTEGER NOT NULL DEFAULT 1,
                google_sub    TEXT UNIQUE,
                google_email  TEXT,
                nickname      TEXT NOT NULL DEFAULT '',
                biography     TEXT NOT NULL DEFAULT '',
                created_at    TEXT NOT NULL
            )
            """
        )

        # ── 老库补列：邮箱登录 / 一次性验证码 ──
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
        if "last_login_at" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN last_login_at TEXT")
        if "email" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
        if "recovery_codes" not in cols:
            # JSON: [{"code": "ABCD1234", "used": 0}, ...]，需要能查看所以存明文
            conn.execute("ALTER TABLE users ADD COLUMN recovery_codes TEXT")
        # 注销：安全期内 deleted_at 有值但 purged_at 为空，登录即恢复；
        # 过了安全期由 purge 落地，purged_at 有值 = 用户名永久占位、禁止登录
        if "deleted_at" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN deleted_at INTEGER")
        if "purged_at" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN purged_at INTEGER")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email "
            "ON users(email) WHERE email IS NOT NULL"
        )

        # 弃用的身份：换绑 / 注销时旧的邮箱、Google sub 落到这里冷却三周。
        # kind='email' 时 value 存规范化邮箱，kind='google' 时存 google_sub。
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS retired_identities (
                kind       TEXT NOT NULL,
                value      TEXT NOT NULL,
                username   TEXT NOT NULL,
                retired_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                PRIMARY KEY (kind, value)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_retired_expires "
            "ON retired_identities(expires_at)"
        )

        # 邮箱验证码：同一账号同时只保留一条，sent_at 用于跨操作限速
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS email_codes (
                username   TEXT PRIMARY KEY,
                code_hash  TEXT NOT NULL,
                target     TEXT NOT NULL,
                purpose    TEXT NOT NULL,
                sent_at    INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                tries      INTEGER NOT NULL DEFAULT 0,
                consumed   INTEGER NOT NULL DEFAULT 0
            )
            """
        )

        # 受信设备：登录过二次验证的设备在有效期内不再重复验证
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trusted_devices (
                token_hash TEXT PRIMARY KEY,
                username   TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_trusted_user ON trusted_devices(username)"
        )
        conn.commit()
    finally:
        conn.close()


init_account_db()


def purge_expired_accounts() -> None:
    """检查并清理过了安全期（7天）仍未恢复的账号。"""
    now = int(time.time())
    grace_deadline = now - DELETE_GRACE_DAYS * 86400

    conn = _conn()
    try:
        # 找出所有过期的待删除账号（deleted_at 有值、purged_at 为空、超过 7 天）
        expired = conn.execute(
            "SELECT username FROM users WHERE deleted_at IS NOT NULL "
            "AND purged_at IS NULL AND deleted_at < ?",
            (grace_deadline,)
        ).fetchall()

        for row in expired:
            username = row["username"]
            purge_account(username)
    finally:
        conn.close()

    # 顺手清掉过期的冷却记录
    conn = _conn()
    try:
        conn.execute("DELETE FROM retired_identities WHERE expires_at <= ?", (now,))
        conn.commit()
    finally:
        conn.close()


_last_purge_at = 0.0
_PURGE_INTERVAL = 3600  # 最多一小时扫一次，避免每个请求都查库


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")


def _norm_email(email: str | None) -> str:
    """邮箱统一小写去空白，作为唯一索引的存储形式。"""
    return (email or "").strip().lower()


def _validate_email(email: str) -> str:
    email = _norm_email(email)
    if not email:
        return "邮箱不能为空"
    if len(email) > 254 or not _EMAIL_RE.match(email):
        return "邮箱格式不正确"
    return ""


def get_user(username: str) -> dict | None:
    if not username:
        return None
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def get_user_by_google_sub(sub: str) -> dict | None:
    if not sub:
        return None
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM users WHERE google_sub=?", (sub,)).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def get_user_by_email(email: str) -> dict | None:
    email = _norm_email(email)
    if not email:
        return None
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def get_user_by_login(identifier: str) -> dict | None:
    """登录框既接受用户名也接受邮箱。"""
    identifier = (identifier or "").strip()
    if not identifier:
        return None
    if "@" in identifier:
        return get_user_by_email(identifier)
    return get_user(identifier)


def _user_exists(username: str) -> bool:
    return get_user(username) is not None


def create_user(
    username: str,
    password: str | None = None,
    role: str = "guest",
    totp_secret: str | None = None,
    google_sub: str | None = None,
    google_email: str | None = None,
    nickname: str = "",
    email: str | None = None,
    *, password_hash: str | None = None,
) -> None:
    # 检查用户名是否已被占用（包括已删除的账号）
    existing = get_user(username)
    if existing:
        raise ValueError(f"用户名 {username} 已被占用")

    conn = _conn()
    try:
        conn.execute(
            """
            INSERT INTO users (username, password_hash, role, totp_secret,
                               totp_on_login, google_sub, google_email,
                               nickname, biography, created_at, email)
            VALUES (?, ?, ?, ?, 0, ?, ?, ?, '', ?, ?)
            """,
            (
                username,
                password_hash or (generate_password_hash(password) if password else None),
                role,
                totp_secret,
                google_sub or None,
                google_email or None,
                nickname or "",
                _now_iso(),
                _norm_email(email) or None,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _update_user(username: str, **fields) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    conn = _conn()
    try:
        conn.execute(
            f"UPDATE users SET {sets} WHERE username=?",
            (*fields.values(), username),
        )
        conn.commit()
    finally:
        conn.close()


def set_password(username: str, password: str) -> None:
    _update_user(username, password_hash=generate_password_hash(password))


def set_totp(username: str, secret: str | None) -> None:
    _update_user(username, totp_secret=secret)


def set_totp_on_login(username: str, enabled: bool) -> None:
    _update_user(username, totp_on_login=1 if enabled else 0)


def bind_google(username: str, sub: str, email: str | None) -> None:
    user = get_user(username)
    old_sub = user.get("google_sub") if user else None
    if old_sub and old_sub != sub:
        retire_identity("google", old_sub, username)
    if old_sub == sub:
        # 本人重新绑回自己的 Google 账号，解除冷却
        release_identity("google", sub)
    _update_user(username, google_sub=sub, google_email=email or None)


def unbind_google(username: str) -> None:
    user = get_user(username)
    old_sub = user.get("google_sub") if user else None
    if old_sub:
        retire_identity("google", old_sub, username)
    _update_user(username, google_sub=None, google_email=None)


def set_email(username: str, email: str | None) -> None:
    user = get_user(username)
    old_email = user.get("email") if user else None
    new_email = _norm_email(email) or None
    if old_email and old_email != new_email:
        retire_identity("email", old_email, username)
    if new_email and old_email == new_email:
        # 本人重新绑回自己的邮箱，解除冷却
        release_identity("email", new_email)
    _update_user(username, email=new_email)


def set_role(username: str, role: str) -> None:
    _update_user(username, role=role)


def clear_password(username: str) -> None:
    _update_user(username, password_hash=None)


def check_user_password(user: dict, password: str) -> bool:
    pw_hash = user.get("password_hash")
    if not pw_hash or not password:
        return False
    return check_password_hash(pw_hash, password)


# ── 一次性验证码（恢复码）────────────────────────────────────────────────────
# 需求要求「使用过的变灰，仅支持查看/获取/重置」，所以必须能原样展示，存明文。
_RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # 去掉易混淆的 I O 0 1


def _gen_recovery_code() -> str:
    return "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(RECOVERY_CODE_LENGTH))


def get_recovery_codes(user: dict) -> list[dict]:
    try:
        codes = json.loads(user.get("recovery_codes") or "[]")
    except Exception:
        return []
    return [c for c in codes if isinstance(c, dict) and c.get("code")]


def reset_recovery_codes(username: str) -> list[dict]:
    """一次刷新全部 10 个，旧的直接作废。"""
    codes = [{"code": _gen_recovery_code(), "used": 0} for _ in range(RECOVERY_CODE_COUNT)]
    _update_user(username, recovery_codes=json.dumps(codes))
    return codes


def has_unused_recovery(user: dict) -> bool:
    return any(not c.get("used") for c in get_recovery_codes(user))


def consume_recovery_code(username: str, code: str) -> bool:
    """核对并标记为已用。大小写不敏感，允许中间有空格或短横线。"""
    entered = re.sub(r"[\s-]", "", (code or "")).upper()
    if len(entered) != RECOVERY_CODE_LENGTH:
        return False
    user = get_user(username)
    if not user:
        return False
    codes = get_recovery_codes(user)
    for item in codes:
        if item.get("used"):
            continue
        if hmac.compare_digest(str(item["code"]).upper(), entered):
            item["used"] = 1
            _update_user(username, recovery_codes=json.dumps(codes))
            return True
    return False


# ── 弃用身份冷却（旧邮箱 / 旧 Google 账号）───────────────────────────────────
def retire_identity(kind: str, value: str, username: str) -> None:
    """把换掉 / 注销掉的邮箱或 Google 账号放进冷却池，三周内不可再被使用。"""
    value = _norm_email(value) if kind == "email" else (value or "").strip()
    if not value:
        return
    now = int(time.time())
    conn = _conn()
    try:
        conn.execute(
            """
            INSERT INTO retired_identities (kind, value, username, retired_at, expires_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(kind, value) DO UPDATE SET
                username=excluded.username, retired_at=excluded.retired_at,
                expires_at=excluded.expires_at
            """,
            (kind, value, username, now, now + IDENTITY_COOLDOWN_DAYS * 86400),
        )
        conn.commit()
    finally:
        conn.close()


def identity_cooling(kind: str, value: str, ignore_username: str = "") -> bool:
    """
    这个邮箱 / Google 账号是否还在冷却期内。
    ignore_username：本人拿回自己刚换掉的身份时不拦。
    """
    value = _norm_email(value) if kind == "email" else (value or "").strip()
    if not value:
        return False
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT username, expires_at FROM retired_identities WHERE kind=? AND value=?",
            (kind, value),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return False
    if int(row["expires_at"]) <= int(time.time()):
        return False
    return str(row["username"]) != ignore_username


def release_identity(kind: str, value: str) -> None:
    """本人重新绑回自己的旧身份时，把冷却记录撤掉。"""
    value = _norm_email(value) if kind == "email" else (value or "").strip()
    if not value:
        return
    conn = _conn()
    try:
        conn.execute(
            "DELETE FROM retired_identities WHERE kind=? AND value=?", (kind, value)
        )
        conn.commit()
    finally:
        conn.close()


def mark_for_deletion(username: str) -> None:
    """标记账号为已删除，进入 7 天安全期。同时把邮箱和 Google 送进冷却池。"""
    user = get_user(username)
    if not user:
        return
    email = user.get("email")
    gsub = user.get("google_sub")
    if email:
        retire_identity("email", email, username)
    if gsub:
        retire_identity("google", gsub, username)
    
    conn = _conn()
    try:
        conn.execute(
            "UPDATE users SET deleted_at=? WHERE username=?",
            (int(time.time()), username),
        )
        conn.commit()
    finally:
        conn.close()


def restore_account(username: str) -> None:
    """用户在安全期内登录，恢复账号。清掉 deleted_at，并把本人的身份从冷却池撤回。"""
    user = get_user(username)
    if not user:
        return
    email = user.get("email")
    gsub = user.get("google_sub")
    if email:
        release_identity("email", email)
    if gsub:
        release_identity("google", gsub)
    
    conn = _conn()
    try:
        conn.execute(
            "UPDATE users SET deleted_at=NULL WHERE username=?", (username,)
        )
        conn.commit()
    finally:
        conn.close()


def is_deleted(user: dict | None) -> bool:
    """账号是否处于已删除状态（安全期内 deleted_at 有值，或已清除 purged_at 有值）。"""
    if not user:
        return False
    return user.get("deleted_at") or user.get("purged_at")


def purge_account(username: str) -> None:
    """安全期过后的硬删除：删头像文件，清个人数据，用户名永久占位。"""
    user = get_user(username)
    if not user:
        return
    
    # 删头像文件：之后 _user_avatar_url 返回 None，各处自动回落默认头像
    avatar_path = _avatar_path(username)
    try:
        avatar_path.unlink(missing_ok=True)
    except Exception:
        pass

    conn = _conn()
    try:
        # 清个人数据，保留用户名占位和游戏数据
        conn.execute(
            """
            UPDATE users SET
                password_hash=NULL,
                email=NULL,
                google_sub=NULL,
                google_email=NULL,
                totp_secret=NULL,
                totp_on_login=0,
                recovery_codes=NULL,
                nickname='',
                biography='',
                purged_at=?
            WHERE username=?
            """,
            (int(time.time()), username),
        )
        # 清二次验证相关
        main_passkeys.init(conn)
        conn.execute("DELETE FROM passkey_credentials WHERE user=?",(username,))
        conn.execute("DELETE FROM passkey_users WHERE user=?",(username,))
        conn.execute("DELETE FROM passkey_challenges WHERE user=?",(username,))
        conn.execute("DELETE FROM email_codes WHERE username=?", (username,))
        conn.execute("DELETE FROM trusted_devices WHERE username=?", (username,))
        conn.commit()
    finally:
        conn.close()


@app.before_request
def _sweep_expired_accounts():
    """
    每小时最多一次，清理过了安全期的账号和过期的冷却记录。
    挂在请求钩子上而不是启动时跑一次，服务器长期不重启也能落地。
    """
    global _last_purge_at
    now = time.time()
    if now - _last_purge_at < _PURGE_INTERVAL:
        return
    _last_purge_at = now
    try:
        purge_expired_accounts()
    except Exception:
        pass


# ── 邮箱验证码 ───────────────────────────────────────────────────────────────
def _code_hash(username: str, code: str) -> str:
    """加盐哈希后入库，数据库泄露也拿不到明文验证码。"""
    return hmac.new(
        app.secret_key if isinstance(app.secret_key, bytes) else str(app.secret_key).encode(),
        f"{username}:{code}".encode(),
        hashlib.sha256,
    ).hexdigest()


def email_code_cooldown_left(username: str) -> int:
    """距离下次可发信还剩几秒。跨操作共享，满足「无论什么操作 1 分钟一次」。"""
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT sent_at FROM email_codes WHERE username=?", (username,)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return 0
    left = EMAIL_CODE_COOLDOWN - (int(time.time()) - int(row["sent_at"]))
    return max(0, left)


def issue_email_code(username: str, target: str, purpose: str) -> tuple[bool, str, int]:
    """
    生成并发送邮箱验证码。返回 (是否成功, 错误信息, 冷却剩余秒数)。
    同账号只保留一条有效验证码，重新获取会顶掉上一条。
    """
    left = email_code_cooldown_left(username)
    if left > 0:
        return False, f"发送过于频繁，请 {left} 秒后重试", left

    code = f"{secrets.randbelow(10 ** EMAIL_CODE_LENGTH):0{EMAIL_CODE_LENGTH}d}"
    now = int(time.time())

    ok, err = mailer.send_code_email(
        target, code, _purpose_text(purpose), EMAIL_CODE_TTL // 60
    )
    if not ok:
        return False, err, 0

    conn = _conn()
    try:
        conn.execute(
            """
            INSERT INTO email_codes (username, code_hash, target, purpose,
                                     sent_at, expires_at, tries, consumed)
            VALUES (?, ?, ?, ?, ?, ?, 0, 0)
            ON CONFLICT(username) DO UPDATE SET
                code_hash=excluded.code_hash, target=excluded.target,
                purpose=excluded.purpose, sent_at=excluded.sent_at,
                expires_at=excluded.expires_at, tries=0, consumed=0
            """,
            (username, _code_hash(username, code), _norm_email(target), purpose,
             now, now + EMAIL_CODE_TTL),
        )
        conn.commit()
    finally:
        conn.close()
    return True, "", EMAIL_CODE_COOLDOWN


def verify_email_code(username: str, purpose: str, code: str) -> tuple[bool, str]:
    """核对邮箱验证码；通过后立即标记 consumed，一码一用。"""
    code = re.sub(r"\s", "", code or "")
    if not code:
        return False, "请输入邮箱验证码"

    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM email_codes WHERE username=?", (username,)
        ).fetchone()
        if not row:
            return False, "请先获取邮箱验证码"
        if int(row["consumed"]):
            return False, "验证码已使用，请重新获取"
        if int(row["expires_at"]) < int(time.time()):
            return False, "验证码已过期，请重新获取"
        if str(row["purpose"]) != purpose:
            return False, "验证码与当前操作不匹配，请重新获取"
        if int(row["tries"]) >= EMAIL_CODE_MAX_TRIES:
            return False, "错误次数过多，请重新获取验证码"

        if not hmac.compare_digest(str(row["code_hash"]), _code_hash(username, code)):
            conn.execute(
                "UPDATE email_codes SET tries=tries+1 WHERE username=?", (username,)
            )
            conn.commit()
            return False, "验证码错误，请重试"

        conn.execute("UPDATE email_codes SET consumed=1 WHERE username=?", (username,))
        conn.commit()
    finally:
        conn.close()
    return True, ""


def email_code_target(username: str) -> str:
    """当前待验证的收件地址，用于界面上打码回显。"""
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT target, consumed, expires_at FROM email_codes WHERE username=?",
            (username,),
        ).fetchone()
    finally:
        conn.close()
    if not row or int(row["consumed"]) or int(row["expires_at"]) < int(time.time()):
        return ""
    return str(row["target"])


def clear_email_code(username: str) -> None:
    conn = _conn()
    try:
        conn.execute("DELETE FROM email_codes WHERE username=?", (username,))
        conn.commit()
    finally:
        conn.close()


def _mask_email(email: str) -> str:
    """a***@example.com，避免在页面上暴露完整地址。"""
    email = _norm_email(email)
    if "@" not in email:
        return email
    name, domain = email.split("@", 1)
    if len(name) <= 1:
        head = name
    elif len(name) == 2:
        head = name[0] + "*"
    else:
        head = name[0] + "*" * min(4, len(name) - 2) + name[-1]
    return f"{head}@{domain}"


# ── 受信设备 ─────────────────────────────────────────────────────────────────
def _device_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def device_trusted(username: str, token: str | None) -> bool:
    if not username or not token:
        return False
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT expires_at FROM trusted_devices WHERE token_hash=? AND username=?",
            (_device_hash(token), username),
        ).fetchone()
    finally:
        conn.close()
    return bool(row) and int(row["expires_at"]) > int(time.time())


def remember_device(username: str) -> str:
    """登记一台受信设备，返回要写进 cookie 的 token。"""
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    conn = _conn()
    try:
        conn.execute("DELETE FROM trusted_devices WHERE expires_at<?", (now,))
        conn.execute(
            "INSERT OR REPLACE INTO trusted_devices "
            "(token_hash, username, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (_device_hash(token), username, now,
             now + TRUSTED_DEVICE_DAYS * 86400),
        )
        conn.commit()
    finally:
        conn.close()
    return token


def forget_devices(username: str) -> None:
    """改密码 / 动二次验证设置后清掉所有受信设备。"""
    conn = _conn()
    try:
        conn.execute("DELETE FROM trusted_devices WHERE username=?", (username,))
        conn.commit()
    finally:
        conn.close()


def _get_profile_row(username: str) -> dict[str, str]:
    user = get_user(username)
    if not user:
        return {"user": username, "nickname": "", "biography": ""}
    return {
        "user": username,
        "nickname": user.get("nickname") or "",
        "biography": user.get("biography") or "",
    }


def _upsert_profile_row(username: str, nickname: str, biography: str) -> None:
    _update_user(username, nickname=nickname, biography=biography)


# ── TOTP / 头像 / session ────────────────────────────────────────────────────
def _totp_uri(username: str, secret: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name="Yitiao")


def _verify_totp(secret: str | None, code: str) -> bool:
    if not secret or not code:
        return False
    return pyotp.TOTP(secret).verify(code.replace(" ", ""), valid_window=1)


def _make_qr_b64(uri: str) -> str:
    buf = io.BytesIO()
    qrcode.make(uri).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _avatar_path(username: str) -> Path:
    return BASE_DIR / "static" / "avatar" / f"{username}.jpg"


def _user_avatar_url(username: str | None) -> str | None:
    if not username:
        return None
    path = _avatar_path(username)
    if path.exists():
        # 用 mtime 当版本号，防止改完头像浏览器还在用缓存
        return url_for("static", filename=f"avatar/{username}.jpg") + f"?v={int(path.stat().st_mtime)}"
    return None


def _save_avatar_from_upload(username: str, file_bytes: bytes) -> None:
    img = Image.open(io.BytesIO(file_bytes))
    img.load()  # 强制解码，损坏/伪造的文件会在这里直接报错，不是靠后缀名判断
    img = ImageOps.exif_transpose(img)  # 修正手机拍照的方向

    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        rgba = img.convert("RGBA")
        bg.paste(rgba, mask=rgba.split()[-1])
        img = bg
    else:
        img = img.convert("RGB")

    w, h = img.size
    side = min(w, h)
    left, top = (w - side) // 2, (h - side) // 2
    img = img.crop((left, top, left + side, top + side))

    if side > 512:
        img = img.resize((512, 512), Image.LANCZOS)

    avatar_dir = BASE_DIR / "static" / "avatar"
    avatar_dir.mkdir(parents=True, exist_ok=True)
    img.save(avatar_dir / f"{username}.jpg", "JPEG", quality=90)


_TMP_SESSION_KEYS = (
    "_lpending", "_lnewsecret", "_lremember",
    "_cpending_user", "_cpending_pw", "_cpending_pw_hash", "_cnewsecret", "_cpending_email", "_cstage",
    "_g_mode", "_g_next",
    "_g_sub", "_g_email", "_g_name", "_g_user", "_g_pw", "_g_pw_hash", "_g_secret",
    "_v",            # 进行中的二次验证
    "_pending_email",  # 待验证的新邮箱
    "_pending_pw", "_pending_pw_hash",     # 待落库的新密码
    "_tf_want",        # 两步验证开关的目标状态
)


def _clear_google_state() -> None:
    """
    清掉 authlib 的 _state_google_<state> 残留。authorize_access_token() 成功后
    自己会删，这里管的是中途放弃授权、或上一轮失败留下的僵尸 state。
    """
    for key in [k for k in list(session.keys()) if str(k).startswith("_state_google_")]:
        session.pop(key, None)


@app.before_request
def discard_legacy_pending_passwords():
    user = get_user(session.get("user", ""))
    if session.get("user") and (not user or is_deleted(user)):
        session.clear()
    # Old signed cookies may carry plaintext passwords; restart those flows.
    if any(key in session for key in ("_cpending_pw", "_g_pw", "_pending_pw")):
        _clear_tmp_session()


def _clear_tmp_session() -> None:
    for key in _TMP_SESSION_KEYS:
        session.pop(key, None)
    _clear_google_state()


def _finish_login(username: str, remember: bool | None = None) -> None:
    """所有验证通过后统一完成 session 写入。"""
    if remember is None:
        remember = bool(session.get("_lremember", False))
    user = get_user(username) or {}
    _update_user(username, last_login_at=_now_iso())
    import yidt_core as y
    if session.get('yidt_sid'):
        with y.connect() as c:c.execute('UPDATE yidt_sessions SET revoked=1 WHERE sid=?',(session['yidt_sid'],))
    # 安全期内登录触发的恢复提示要活过这次 session.clear()
    restored = bool(session.get("_account_restored", False))
    session.clear()
    session["user"] = username
    session["role"] = str(user.get("role") or "guest")
    session.permanent = bool(remember)
    if restored:
        session["_account_restored"] = True


def _current_user() -> dict | None:
    return get_user(session.get("user", ""))


# ── 二次验证方式模型 ─────────────────────────────────────────────────────────
# 「二次验证开关」（totp_on_login）只管登录时要不要验；已绑定的验证方式与开关
# 是两件事。敏感操作只要账号存在任一验证方式就强制验证，不看开关。
_PURPOSE_TEXT = {
    "login": "登录 Yitiao 账号",
    "yidt_pin": "设置或重置 YIDT 支付密码",
    "manage_passkeys": "添加或管理通行密钥（须使用邮箱或验证器）",
    "bind_email": "绑定邮箱",
    "change_email": "更换邮箱",
    "remove_email": "移除邮箱",
    "bind_google": "绑定 Google 账号",
    "rebind_google": "更换绑定的 Google 账号",
    "unbind_google": "解除 Google 账号绑定",
    "set_password": "设置账号密码",
    "change_password": "修改账号密码",
    "twofa_on": "开启两步验证",
    "twofa_off": "关闭两步验证",
    "set_totp": "更改身份验证器",
    "remove_totp": "移除身份验证器",
    "recovery": "获取一次性验证码",
    "reset_recovery": "重置一次性验证码",
}


def _purpose_text(purpose: str) -> str:
    return _PURPOSE_TEXT.get(purpose, "进行账号安全操作")


def twofa_methods(user: dict, kind: str = "login") -> list[str]:
    """
    账号当前可用的二次验证方式，顺序即界面默认优先级。
    一次性验证码是「进不去账号时的救命手段」，只在登录场景可用；
    账号内的敏感操作不接受它，否则捡到一张码就能改密码/解绑。
    """
    methods = ["passkey"] if main_passkeys.has(user.get("username")) else []
    if user.get("email"):
        methods.append("email")       # 设置邮箱后自动启用
    if user.get("totp_secret"):
        methods.append("totp")
    if kind == "login" and has_unused_recovery(user):
        methods.append("recovery")
    return methods


_METHOD_LABEL = {"passkey":"通行密钥", "email": "邮箱验证码", "totp": "身份验证器", "recovery": "一次性验证码"}


def has_twofa(user: dict) -> bool:
    return bool(twofa_methods(user))


def _needs_twofa_for_sensitive(user: dict) -> bool:
    """敏感操作：只要有邮箱、验证器或通行密钥就强制验证（不含一次性验证码）。"""
    return bool(twofa_methods(user, "sensitive"))


# 兼容旧调用点
def _needs_totp_for_sensitive(user: dict) -> bool:
    return _needs_twofa_for_sensitive(user)


def twofa_on_login(user: dict) -> bool:
    """开关打开且真有验证方式时才算生效。"""
    return bool(int(user.get("totp_on_login") or 0)) and has_twofa(user)


# ── 敏感操作的验证票据 ───────────────────────────────────────────────────────
def _ticket_key(purpose: str) -> str:
    return f"_vok_{purpose}"


def _grant_ticket(purpose: str) -> None:
    session[_ticket_key(purpose)] = int(time.time())


def _has_ticket(purpose: str) -> bool:
    """看一眼票据是否有效，不消费。表单校验失败时不至于要求重新验证。"""
    ts = session.get(_ticket_key(purpose))
    return bool(ts) and (int(time.time()) - int(ts)) <= VERIFY_TICKET_TTL


def consume_ticket(purpose: str) -> None:
    """操作真正落库后再作废票据，一次验证只能办一件事。"""
    session.pop(_ticket_key(purpose), None)


def _clear_tickets() -> None:
    for key in [k for k in list(session.keys()) if str(k).startswith("_vok_")]:
        session.pop(key, None)


def require_twofa(user: dict, purpose: str, next_url: str, exclude_passkeys=False):
    """
    敏感操作入口：没有任何验证方式 → 直接放行；否则要求先过一次验证。
    返回 None 表示已通过，否则返回一个 redirect 让调用方 return 出去。
    """
    methods=twofa_methods(user,'sensitive')
    if exclude_passkeys:methods=[m for m in methods if m!='passkey']
    if not methods:
        if exclude_passkeys:return redirect(url_for('account',err='请先绑定邮箱验证或身份验证器以继续'))
        return None
    if _has_ticket(purpose):
        return None
    session["_v"] = {
        "kind": "sensitive",
        "user": user["username"],
        "purpose": purpose,
        "next": _safe_next(next_url, "/account/"),
        "method": methods[0],
        "no_passkey": exclude_passkeys,
        "id": secrets.token_urlsafe(16),
    }
    return redirect(url_for("verify"))


def _safe_next(raw: str | None, default: str = "/") -> str:
    """只允许站内相对路径，避免 open redirect。"""
    if not raw or not isinstance(raw,str) or not raw.startswith("/") or raw.startswith("//") or "\\" in raw or any(ord(ch)<32 for ch in raw):
        return default
    return raw


# ── Google OIDC：由 authlib 负责 state / nonce / PKCE / JWKS 验签 ─────────────
def _google_authorize_redirect(mode: str, next_url: str):
    """mode: 'login' 走登录/注册，'bind' 走 /account/ 绑定。"""
    # OAuth 流程需要 session 在重定向后保留，必须设为 permanent
    session.permanent = True
    session["_g_mode"] = mode
    session["_g_next"] = _safe_next(next_url)
    # 上一次没走完的授权残留会干扰这次，先清掉
    _clear_google_state()
    return google.authorize_redirect(
        url_for("google_callback", _external=True),
        prompt="select_account",
    )


def _google_fetch_claims() -> dict:
    """
    在回调里换 token。authlib 已经校验过 state / PKCE / nonce，
    并用 Google 的 JWKS 验过 id_token 签名与 aud / iss / exp。
    """
    token = google.authorize_access_token()
    claims = dict(token.get("userinfo") or {})
    if not claims.get("sub"):
        raise ValueError("Google 未返回有效的 id_token")
    return claims


def _suggest_username(claims: dict) -> str:
    """从 Google 资料猜一个合法用户名（>5 位、字母数字、不以数字开头）。"""
    raw = (claims.get("email") or "").split("@")[0] or (claims.get("name") or "")
    cleaned = "".join(ch for ch in raw if ch.isalnum())
    if not cleaned or cleaned[0].isdigit():
        cleaned = "user" + cleaned
    cleaned = cleaned[:20]
    if len(cleaned) <= 5:
        cleaned = (cleaned + "yitiao")[:12]
    base = cleaned
    n = 1
    while _user_exists(cleaned):
        suffix = str(n)
        cleaned = base[: 20 - len(suffix)] + suffix
        n += 1
    return cleaned


def _validate_username(username: str) -> str:
    if not username:
        return "用户名不能为空"
    if len(username) <= 5:
        return "用户名长度必须大于 5 个字符"
    if not username.isalnum():
        return "用户名只能包含字母和数字"
    if username[0].isdigit():
        return "用户名不能以数字开头"
    if _user_exists(username):
        return "用户名已存在"
    return ""


def _validate_password(password: str, confirm: str) -> str:
    if not password:
        return "密码不能为空"
    if len(password) < 8:
        return "密码长度至少 8 位"
    if not any(ch.isalpha() for ch in password) or not any(ch.isdigit() for ch in password):
        return "密码需要同时包含字母和数字"
    if password != confirm:
        return "两次输入的密码不一致"
    return ""


def _verify_turnstile(token: str, expected_action: str = "") -> tuple[bool, str]:
    if not TURNSTILE_SECRET:
        return False, "Turnstile secret key 未配置"
    if not token:
        return False, "请先完成人机验证"

    try:
        resp = requests.post(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data={
                "secret": TURNSTILE_SECRET,
                "response": token,
                "remoteip": request.remote_addr,
            },
            timeout=8,
        )
        data = resp.json()
    except Exception:
        return False, "人机验证请求失败"

    if not data.get("success"):
        return False, "人机验证失败"
    if expected_action and data.get("action") != expected_action:
        return False, "人机验证失败，请刷新页面重试"
    if expected_action and data.get("hostname") not in ("yitiao.org", "www.yitiao.org"):
        return False, "人机验证来源不匹配，请刷新页面重试"
    return True, ""


@app.route("/logo.svg")
def logo_svg():
    """Backward-compatible route that serves static/logo.svg."""
    logo_path = BASE_DIR / "static" / "logo.svg"
    if logo_path.exists():
        return send_from_directory(BASE_DIR / "static", "logo.svg")
    return ("logo.svg not found", 404)


# ── 共享样式与页面骨架 ───────────────────────────────────────────────────────
_CSS = """
    :root {
      --bg0: #02101f; --bg1: #04142d;
      --text: #eaf1ff;
      --muted: rgba(234,241,255,0.66);
      --muted2: rgba(234,241,255,0.42);
      --accent: #2d71ff; --accent2: #5aa7ff;
      --card: rgba(2,10,25,0.58);
      --border: rgba(255,255,255,0.10);
      --ib: rgba(255,255,255,0.07);
      --iborder: rgba(255,255,255,0.16);
      --ifocus: rgba(45,113,255,0.50);
      --err: #ff6b6b;
      --ok: #38d26a;
      --disabled: rgba(45,113,255,.26);
    }
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    html, body {
      min-height: 100vh; width: 100%;
      font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: radial-gradient(ellipse 110% 80% at 50% -8%, #10315f, var(--bg1) 58%, var(--bg0));
      color: var(--text);
    }
    a { color: inherit; text-decoration: none; }
    .page {
      min-height: 100vh;
      display: flex; align-items: center; justify-content: center;
      padding: 28px 20px;
    }
    .card {
      width: min(460px, 100%);
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 26px;
      padding: 44px 38px 38px;
      backdrop-filter: blur(22px);
      box-shadow: 0 36px 90px rgba(0,0,0,0.46), inset 0 1px 0 rgba(255,255,255,0.06);
      animation: rise .42s cubic-bezier(.2,.8,.2,1) both;
    }
    @keyframes rise {
      from { opacity:0; transform:translateY(20px) scale(.98); }
      to   { opacity:1; transform:none; }
    }
    .logo {
      display: block; width: 76px; height: 76px;
      margin: 0 auto 24px;
      filter: drop-shadow(0 10px 22px rgba(0,0,0,.32));
    }
    h1 {
      text-align: center; font-size: 30px; font-weight: 800;
      letter-spacing: .02em; margin-bottom: 26px;
    }
    .step-desc {
      text-align: center; font-size: 15px;
      color: var(--muted); line-height: 1.7; margin-bottom: 22px;
    }
    .field { margin-bottom: 18px; }
    label {
      display: block; font-size: 12.5px; font-weight: 600;
      letter-spacing: .06em; text-transform: uppercase;
      color: var(--muted); margin-bottom: 8px;
    }
    input[type="text"], input[type="password"], input[type="tel"], textarea {
      display: block; width: 100%; height: 50px;
      padding: 0 16px;
      background: var(--ib); border: 1px solid var(--iborder);
      border-radius: 14px; color: var(--text);
      font: 15px/1 inherit; outline: none;
      transition: border-color .18s, box-shadow .18s;
    }
    input::placeholder, textarea::placeholder { color: rgba(234,241,255,.28); }
    input:focus, textarea:focus { border-color: var(--accent); box-shadow: 0 0 0 3.5px var(--ifocus); }
    input.invalid {
      border-color: rgba(255,107,107,.55) !important;
      box-shadow: 0 0 0 3.5px rgba(255,107,107,.16) !important;
    }
    .remember-row {
      display: flex; align-items: center; gap: 10px;
      margin-bottom: 22px; cursor: pointer;
    }
    .remember-row input[type="checkbox"] {
      width: 18px; height: 18px;
      accent-color: var(--accent); flex-shrink: 0; cursor: pointer;
    }
    .remember-row span { font-size: 14px; color: var(--muted); user-select: none; }
    .btn-primary {
      display: flex; align-items: center; justify-content: center;
      width: 100%; height: 52px;
      border: 0; border-radius: 16px;
      background: linear-gradient(135deg, var(--accent2), var(--accent));
      color: #fff; font: 700 16px/1 inherit; cursor: pointer;
      letter-spacing: .03em;
      box-shadow: 0 10px 30px rgba(45,113,255,.34);
      transition: transform .15s, box-shadow .15s, opacity .15s, background .15s;
    }
    .btn-primary:hover:enabled, a.btn-primary:hover { transform: translateY(-1.5px); box-shadow: 0 14px 36px rgba(45,113,255,.44); }
    .btn-primary:active { transform: none; }
    .btn-primary:disabled {
      cursor: not-allowed;
      background: linear-gradient(135deg, var(--disabled), rgba(45,113,255,.16));
      box-shadow: none; opacity: .68; transform: none;
    }
    .btn-row { display: flex; gap: 10px; }
    .btn-secondary {
      flex: 1; height: 52px;
      display: flex; align-items: center; justify-content: center; gap: 6px;
      border: 1px solid rgba(255,255,255,.18); border-radius: 16px;
      background: rgba(255,255,255,.06); color: var(--muted);
      font: 14px/1 inherit; cursor: pointer;
      transition: background .15s, color .15s;
    }
    .btn-secondary:hover { background: rgba(255,255,255,.12); color: var(--text); }
    .btn-primary-half {
      flex: 1; height: 52px; border: 0; border-radius: 16px;
      background: linear-gradient(135deg, var(--accent2), var(--accent));
      color: #fff; font: 700 15px/1 inherit; cursor: pointer;
      box-shadow: 0 10px 30px rgba(45,113,255,.34);
      transition: transform .15s, box-shadow .15s;
    }
    .btn-primary-half:hover { transform: translateY(-1.5px); box-shadow: 0 14px 36px rgba(45,113,255,.44); }
    .btn-back {
      display: flex; align-items: center; justify-content: center;
      width: 100%; height: 46px; margin-top: 12px;
      border: 1px solid rgba(255,255,255,.14); border-radius: 14px;
      background: rgba(255,255,255,.06); color: var(--muted);
      font: 14px/1 inherit; cursor: pointer;
      transition: background .15s, color .15s;
    }
    .btn-back:hover { background: rgba(255,255,255,.11); color: var(--text); }
    .link-mini {
      background: none; border: 0;
      color: rgba(234,241,255,.46); font-family: inherit; font-size: 13px; font-weight: 400; line-height: 1; text-decoration: none;
      cursor: pointer; padding: 6px 0;
      transition: color .15s;
    }
    #passkey-status:empty { display: none; }
    .link-mini:hover { color: var(--accent2); }
    .footer-links {
      display: flex; justify-content: center; gap: 18px; margin-top: 14px;
    }
    .error-msg {
      background: rgba(255,107,107,.12);
      border: 1px solid rgba(255,107,107,.28);
      border-radius: 12px; padding: 13px 16px; margin-bottom: 20px;
      font-size: 14px; color: var(--err); text-align: center; line-height: 1.5;
    }
    .notice-msg {
      background: rgba(56,210,106,.10);
      border: 1px solid rgba(56,210,106,.28);
      border-radius: 12px; padding: 13px 16px; margin-bottom: 20px;
      font-size: 14px; color: var(--ok); text-align: center; line-height: 1.5;
    }
    /* Google 按钮 */
    .btn-google {
      display: flex; align-items: center; justify-content: center; gap: 10px;
      width: 100%; height: 50px;
      border: 1px solid rgba(255,255,255,.20); border-radius: 16px;
      background: #fff; color: #1f1f1f;
      font: 600 15px/1 inherit; cursor: pointer;
      transition: transform .15s, box-shadow .15s, background .15s;
    }
    .btn-google:hover { transform: translateY(-1.5px); box-shadow: 0 12px 30px rgba(0,0,0,.32); background: #f6f8fc; }
    .btn-google svg { width: 19px; height: 19px; flex-shrink: 0; }
    .btn-google.disabled { opacity: .45; cursor: not-allowed; transform: none; box-shadow: none; }
    .or-split {
      display: flex; align-items: center; gap: 12px;
      margin: 20px 0; color: var(--muted2); font-size: 12.5px;
      letter-spacing: .08em; text-transform: uppercase;
    }
    .or-split::before, .or-split::after {
      content: ""; flex: 1; height: 1px; background: rgba(255,255,255,.10);
    }
    /* TOTP 二维码 */
    .qr-block { text-align: center; margin-bottom: 22px; }
    .qr-block img {
      width: 188px; height: 188px; display: block;
      margin: 0 auto 14px; padding: 8px;
      background: #fff; border-radius: 16px;
      border: 2px solid rgba(255,255,255,.14);
    }
    .secret-pill {
      display: inline-block;
      background: rgba(255,255,255,.08);
      border: 1px solid rgba(255,255,255,.14);
      border-radius: 10px; padding: 9px 16px;
      font: 13px/1.5 "JetBrains Mono","Fira Code",ui-monospace,monospace;
      letter-spacing: .08em; word-break: break-all;
      color: var(--accent2); cursor: pointer;
      transition: background .15s; max-width: 100%;
    }
    .secret-pill:hover { background: rgba(255,255,255,.13); }
    .copy-hint { font-size: 11px; color: rgba(234,241,255,.36); margin-top: 7px; }
    .pw-rules { margin: -4px 0 14px; display: grid; gap: 8px; }
    .pw-rule { font-size: 13px; color: rgba(234,241,255,.42); line-height: 1.4; }
    .pw-rule.ok { color: var(--ok); }
    .pw-rule.bad { color: var(--err); }
    .inline-error { display: none; margin-top: 8px; font-size: 13px; line-height: 1.45; color: var(--err); }
    .inline-error.show { display: block; }
    .turnstile-wrap { margin: 8px 0 16px; display: flex; justify-content: center; }
    .divider { height: 1px; background: rgba(255,255,255,.08); margin: 22px 0; }
    @media (max-width: 480px) { .card { padding: 36px 22px 28px; } }
"""

_GOOGLE_SVG = """
<svg viewBox="0 0 48 48" aria-hidden="true">
  <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/>
  <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/>
  <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/>
  <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/>
</svg>
"""


def _shell(title: str, body: str) -> str:
    """统一的深蓝玻璃拟态页面骨架，各页只写 body。"""
    return (
        """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>"""
        + title
        + """ – Yitiao</title>
  <link rel="icon" href="{{ url_for('static', filename='logo.ico') }}" type="image/x-icon">
  <style>"""
        + _CSS
        + """</style>
</head>
<body>
"""
        + body
        + """
</body>
</html>
"""
    )


_COPY_JS = """
<script>
  function copySecret(el, key) {
    navigator.clipboard.writeText(key).then(function () {
      var orig = el.textContent;
      el.textContent = '已复制 ✓';
      setTimeout(function () { el.textContent = orig; }, 1600);
    }).catch(function () { el.textContent = '请手动复制'; });
  }
</script>
"""

# 绑定/验证 验证器的公共片段（step 内复用）
_TOTP_BIND_BLOCK = (
    """
    <div class="qr-block">
      <img src="data:image/png;base64,{{ qr_b64 }}" alt="TOTP 二维码">
      <div class="secret-pill" onclick="copySecret(this,'{{ new_secret }}')"
           title="点击复制密钥">{{ new_secret }}</div>
      <div class="copy-hint">点击密钥可复制 &nbsp;·&nbsp; 请妥善保管，勿泄露</div>
    </div>
"""
)


# ── 登录 ─────────────────────────────────────────────────────────────────────
_LOGIN_BODY = """
<div class="page">
  <div class="card">
    <a href="/" style="display:contents;">
      <img class="logo" src="{{ url_for('static', filename='logo.svg') }}" alt="Yitiao">
    </a>

    {% if step == 1 %}
    <h1>欢迎</h1>
    <div class="notice-msg"><a href="/points/">YIDT 钱包</a> · 登录后管理钱包与每日活动</div>
    {% if sso_app %}<div class="notice-msg">登录后将授权访问 {{ sso_app }}</div>{% endif %}
    {% if error %}<div class="error-msg">{{ error }}</div>{% endif %}

    {% if google_ready %}
    <a class="btn-google" href="{{ url_for('google_login', next=next_url) }}">
      """ + _GOOGLE_SVG + """
      使用 Google 账号登录
    </a>
    {% else %}
    <div class="btn-google disabled" title="服务器未配置 Google OAuth">
      """ + _GOOGLE_SVG + """
      Google 登录未配置
    </div>
    {% endif %}

    <div class="or-split">或</div>

    <form method="post">
      <input type="hidden" name="_action" value="login">
      <input type="hidden" name="_next"   value="{{ next_url }}">
      <div class="field">
        <label for="un">用户名或邮箱</label>
        <input id="un" type="text" name="username" placeholder="请输入用户名或邮箱"
               autocomplete="username webauthn" autofocus required>
      </div>
      <div class="field">
        <label for="pw">密码</label>
        <input id="pw" type="password" name="password" placeholder="请输入密码"
               autocomplete="current-password" required>
      </div>
      <label class="remember-row" for="rem">
        <input id="rem" type="checkbox" name="remember" value="1">
        <span>自动登录（30 天）</span>
      </label>
      <button class="btn-primary" type="submit">登录</button>
      <div class="footer-links">
        <button class="link-mini" type="button" onclick="openForgot()">忘记密码</button>
        <button class="link-mini" type="button" data-passkey-login data-csrf="{{session._pk_csrf}}" data-next="{{next_url}}">通行密钥</button>
        <a class="link-mini" href="/createaccount/">注册</a>
      </div>
    </form>
    <p id="passkey-status" role="status" class="copy-hint"></p><script src="/static/passkeys.js" defer></script>

    {% elif step == 2 %}
    <h1>绑定验证器</h1>
    <div class="step-desc">
      你的账号还没有任何二次验证方式<br>建议绑定身份验证器，也可以跳过
    </div>
    {% if error %}<div class="error-msg">{{ error }}</div>{% endif %}
""" + _TOTP_BIND_BLOCK + """
    <form method="post">
      <input type="hidden" name="_action" value="setup_totp">
      <input type="hidden" name="_next"   value="{{ next_url }}">
      <div class="field">
        <label for="tc">输入 App 中显示的 6 位验证码</label>
        <input id="tc" type="tel" name="totp_code" placeholder="000000" maxlength="6"
               pattern="[0-9]{6}" autocomplete="one-time-code" autofocus required>
      </div>
      <div class="btn-row">
        <a href="/login/back/" class="btn-secondary">返回</a>
        <button class="btn-secondary" type="submit" name="_skip" value="1" formnovalidate style="flex:.9;">跳过</button>
        <button class="btn-primary-half" type="submit">完成绑定</button>
      </div>
    </form>
    {% endif %}
  </div>
</div>

<div class="modal-bg" id="forgotModal" onclick="if(event.target===this)closeForgot()"
     style="position:fixed;inset:0;background:rgba(1,8,22,.74);backdrop-filter:blur(10px);
            z-index:200;display:flex;align-items:center;justify-content:center;padding:24px;
            visibility:hidden;opacity:0;transition:opacity .22s,visibility .22s;">
  <div class="card" style="width:min(380px,100%);text-align:center;">
    <h1 style="font-size:20px;margin-bottom:12px;">忘记密码</h1>
    <div class="step-desc">暂时不支持密码自助修改，<br>请联系管理员重置您的密码。</div>
    <button class="btn-back" style="margin-top:0;" onclick="closeForgot()">关闭</button>
  </div>
</div>

<script>
  function openForgot() {
    var m = document.getElementById('forgotModal');
    m.style.visibility = 'visible'; m.style.opacity = '1';
  }
  function closeForgot() {
    var m = document.getElementById('forgotModal');
    m.style.visibility = 'hidden'; m.style.opacity = '0';
  }
</script>
""" + _COPY_JS

_LOGIN_HTML = _shell("登录", _LOGIN_BODY)


def _sso_app_name(next_url: str) -> str:
    """next 指向 /oidc/authorize 时，取出发起方应用名用于登录页提示。"""
    if not next_url.startswith("/oidc/authorize"):
        return ""
    from urllib.parse import parse_qs, urlsplit
    cid = (parse_qs(urlsplit(next_url).query).get("client_id") or [""])[0]
    client = oidc_provider.CLIENTS.get(cid)
    return str(client["name"]) if client else ""


def _render_login(step=1, error="", next_url="/", qr_b64="", new_secret="", force_totp=False):
    _passkey_session()
    return render_template_string(
        _LOGIN_HTML,
        step=step,
        error=error,
        next_url=next_url,
        qr_b64=qr_b64,
        new_secret=new_secret,
        force_totp=force_totp,
        google_ready=google_ready(),
        sso_app=_sso_app_name(next_url),
    )


def _start_totp_bind(username: str) -> tuple[str, str]:
    """生成新密钥并写入 session，返回 (secret, qr_b64)。"""
    secret = pyotp.random_base32()
    session["_lnewsecret"] = secret
    return secret, _make_qr_b64(_totp_uri(username, secret))


def _login_next_step(user: dict, next_url: str, via_redirect: bool = False):
    """
    密码/Google 校验通过后决定下一步：
      · 开了两步验证开关、有验证方式、且设备不受信 → 走 /verify/
      · 完全没有任何二次验证方式 → 提示绑定验证器（可跳过）
      · 其余 → 直接登录

    via_redirect=True 时不直接渲染表单，而是 302 出去。Google 回调必须走这条
    路：回调 URL 上挂着 authlib 的 state，就地渲染表单会让表单 POST 回
    /login/google/callback/，而那个 state 已经被 authorize_access_token()
    消费掉了，于是报 mismatching_state。
    """
    username = user["username"]
    session["_lpending"] = username

    needs_code = (
        twofa_on_login(user)
        and not device_trusted(username, request.cookies.get(TRUSTED_DEVICE_COOKIE))
    )

    if needs_code:
        session["_v"] = {
            "kind": "login",
            "user": username,
            "purpose": "login",
            "next": next_url,
            "method": (twofa_methods(user) or ["email"])[0],
        }
        return redirect(url_for("verify"))

    # 一个二次验证方式都没有：建议绑定验证器，但所有注册方式都允许跳过
    if not has_twofa(user):
        secret, qr = _start_totp_bind(username)
        if via_redirect:
            return redirect(url_for("login", next=next_url))
        return _render_login(step=2, next_url=next_url, qr_b64=qr,
                             new_secret=secret, force_totp=False)

    _finish_login(username)
    _clear_tmp_session()
    return redirect(next_url)


@app.route("/login/", methods=["GET", "POST"])
def login():
    next_url = _safe_next(request.args.get("next"))
    # Google 回调失败时会 302 过来并带上 err
    err_msg = request.args.get("err", "")

    if session.get("user"):
        return redirect(next_url)

    if request.method == "POST":
        action = request.form.get("_action", "")
        next_url = _safe_next(request.form.get("_next"), next_url)

        if action == "login":
            identifier = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            session["_lremember"] = request.form.get("remember") == "1"

            user = get_user_by_login(identifier)
            # 已清理的账号：用户名永久占位，直接拒登（此时密码已被清空）
            if user and user.get("purged_at"):
                return _render_login(step=1, error="账号已删除", next_url=next_url)

            if not user or not check_user_password(user, password):
                return _render_login(step=1, error="账号或密码不正确", next_url=next_url)

            # 检查账号是否在安全期内（deleted_at 有值但未 purge）
            if user.get("deleted_at") and not user.get("purged_at"):
                restore_account(user["username"])
                session["_account_restored"] = True

            return _login_next_step(user, next_url)

        pending = session.get("_lpending", "")
        user = get_user(pending)
        if not user:
            _clear_tmp_session()
            return _render_login(step=1, error="会话已过期，请重新登录", next_url=next_url)

        if action == "setup_totp":
            secret = session.get("_lnewsecret", "")
            if not secret:
                _clear_tmp_session()
                return _render_login(step=1, error="会话已过期，请重新登录", next_url=next_url)

            # 所有注册方式都允许跳过绑定验证器
            if request.form.get("_skip") == "1":
                _finish_login(pending)
                _clear_tmp_session()
                return redirect(next_url)

            if _verify_totp(secret, request.form.get("totp_code", "")):
                set_totp(pending, secret)
                set_totp_on_login(pending, True)
                _finish_login(pending)
                _clear_tmp_session()
                return redirect(next_url)

            return _render_login(step=2, error="验证码错误，请重试", next_url=next_url,
                                 qr_b64=_make_qr_b64(_totp_uri(pending, secret)),
                                 new_secret=secret, force_totp=False)

    # GET：恢复中间步骤
    pending = session.get("_lpending", "")
    pending_user = get_user(pending) if pending else None
    if pending_user:
        # Google 回调是 302 过来的，next 从 session 里带过来，别丢掉
        next_url = _safe_next(session.get("_g_next"), next_url)
        secret = session.get("_lnewsecret", "")
        if secret:
            return _render_login(step=2, next_url=next_url,
                                 qr_b64=_make_qr_b64(_totp_uri(pending, secret)),
                                 new_secret=secret, force_totp=False)
        _clear_tmp_session()

    return _render_login(step=1, next_url=next_url, error=err_msg)


@app.route("/logout/", methods=["POST"])
def logout():
    username = session.get("user")
    if username:
        import yidt_core as y
        sid=session.get('yidt_sid')
        with y.connect() as c:
            c.execute('UPDATE yidt_sessions SET revoked=1 WHERE sid=? AND user=?',(sid,username))
        with _conn() as c:
            c.execute('DELETE FROM oidc_tokens WHERE sid=?',(sid,))
            c.execute('DELETE FROM oidc_codes WHERE sid=?',(sid,))
    session.clear()
    return redirect(url_for("login"))


def revoke_linked_sessions(username: str) -> None:
    """Revoke account authorization and administrator sessions."""
    oidc_provider.revoke_user_tokens(username)
    import yidt_core as y
    with y.connect() as c:c.execute('UPDATE yidt_sessions SET revoked=1 WHERE user=?',(username,))
    revoke_admin = app.extensions.get("admin_panel_revoke")
    if revoke_admin:
        revoke_admin(username)


@app.route("/login/back/")
def login_back():
    """取消登录中间流程，清除临时 session 后回到登录首屏。"""
    _clear_tmp_session()
    return redirect("/login/")


# ── 统一二次验证页（登录 + 敏感操作共用）─────────────────────────────────────
_VERIFY_CSS = """
    .m-list { display: grid; gap: 10px; margin-bottom: 4px; }
    .m-item {
      display: flex; align-items: center; gap: 14px; width: 100%;
      padding: 15px 16px; text-align: left;
      background: rgba(255,255,255,.05);
      border: 1px solid rgba(255,255,255,.09);
      border-radius: 16px; color: var(--text);
      font: 15px/1.3 inherit; cursor: pointer;
      transition: background .16s, border-color .16s;
    }
    .m-item:hover { background: rgba(255,255,255,.10); border-color: rgba(255,255,255,.18); }
    .m-item.cur { border-color: rgba(90,167,255,.46); background: rgba(45,113,255,.12); }
    .m-ico {
      width: 36px; height: 36px; border-radius: 11px; flex-shrink: 0;
      display: flex; align-items: center; justify-content: center;
      background: rgba(45,113,255,.16); color: var(--accent2);
    }
    .m-ico svg { width: 18px; height: 18px; }
    .m-body { flex: 1; min-width: 0; }
    .m-title { font-size: 15px; font-weight: 600; }
    .m-sub { margin-top: 3px; font-size: 12.5px; color: var(--muted2); }
    .code-row { display: flex; gap: 10px; align-items: stretch; }
    .code-row input { flex: 1; }
    .btn-send {
      flex-shrink: 0; min-width: 116px; height: 50px; padding: 0 14px;
      border: 1px solid rgba(90,167,255,.38); border-radius: 14px;
      background: rgba(45,113,255,.16); color: var(--accent2);
      font: 600 13.5px/1 inherit; cursor: pointer;
      transition: background .15s, opacity .15s;
    }
    .btn-send:hover:enabled { background: rgba(45,113,255,.26); }
    .btn-send:disabled { opacity: .5; cursor: not-allowed; }
"""

_IC_MAIL = """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2.5" y="4.5" width="19" height="15" rx="2.5"/><path d="M3 7l9 6 9-6"/></svg>"""
_IC_PHONE = """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="2.5" width="12" height="19" rx="2.5"/><line x1="10.5" y1="18.5" x2="13.5" y2="18.5"/></svg>"""
_IC_LIST = """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="9" y1="7" x2="20" y2="7"/><line x1="9" y1="12" x2="20" y2="12"/><line x1="9" y1="17" x2="20" y2="17"/><circle cx="4.5" cy="7" r="1.2" fill="currentColor"/><circle cx="4.5" cy="12" r="1.2" fill="currentColor"/><circle cx="4.5" cy="17" r="1.2" fill="currentColor"/></svg>"""

_METHOD_ICONS = {"passkey":_IC_PHONE, "email": _IC_MAIL, "totp": _IC_PHONE, "recovery": _IC_LIST}

_VERIFY_BODY = """
<div class="page">
  <div class="card">
    <a href="/" style="display:contents;">
      <img class="logo" src="{{ url_for('static', filename='logo.svg') }}" alt="Yitiao">
    </a>

    {% if picking %}
    <h1>选择验证方式</h1>
    <div class="step-desc">当前操作：{{ purpose_text }}</div>
    {% if error %}<div class="error-msg">{{ error }}</div>{% endif %}
    <div class="m-list">
      {% for m in methods %}
      <form action="{{ verify_url }}" method="post" style="display:contents;">
        <input type="hidden" name="csrf" value="{{ session.get('_yidt_csrf','') }}"><input type="hidden" name="_action" value="pick">
        <input type="hidden" name="method" value="{{ m }}">
        <button class="m-item {{ 'cur' if m == method else '' }}" type="submit">
          <span class="m-ico">{{ icons[m] | safe }}</span>
          <span class="m-body">
            <span class="m-title">{{ labels[m] }}</span>
            <span class="m-sub">{{ subs[m] }}</span>
          </span>
        </button>
      </form>
      {% endfor %}
    </div>
    <a class="btn-back" href="{{ verify_url }}">返回验证</a>

    {% else %}
    <h1>身份验证</h1>
    <div class="step-desc">
      为了确认是你本人操作，请完成一次验证<br>
      当前操作：<b>{{ purpose_text }}</b>
    </div>
    {% if error %}<div class="error-msg">{{ error }}</div>{% endif %}
    {% if notice %}<div class="notice-msg">{{ notice }}</div>{% endif %}

    {% if method == 'passkey' %}<link rel="stylesheet" href="/static/passkeys.css"><button class="passkey-button" type="button" data-passkey-verify data-csrf="{{session._pk_csrf}}" data-flow="{{request.args.get('flow','')}}">使用通行密钥验证</button><p id="passkey-status" role="status" class="copy-hint"></p><script src="/static/passkeys.js" defer></script>{% else %}
    {% if method == 'email' %}
    <form action="{{ verify_url }}" method="post" id="vsendform">
      <input type="hidden" name="csrf" value="{{ session.get('_yidt_csrf','') }}"><input type="hidden" name="_action" value="send">
      <div class="turnstile-wrap">
        <div class="cf-turnstile" data-sitekey="{{ turnstile_sitekey }}"
             data-action="{{ turnstile_email_action }}" data-theme="dark"
             data-size="compact"></div>
      </div>
      <div class="copy-hint" style="text-align:center;margin-bottom:12px;">发送邮件前请完成人机验证</div>
    </form>
    {% endif %}
    <form action="{{ verify_url }}" method="post" id="vform"><input type="hidden" name="csrf" value="{{ session.get('_yidt_csrf','') }}"><input type="hidden" name="_action" value="check">
      {% if method == 'email' %}
      <div class="field">
        <label for="vc">邮箱验证码</label>
        <div class="code-row">
          <input id="vc" type="tel" name="code" placeholder="000000" maxlength="6"
                 pattern="[0-9]{6}" autocomplete="one-time-code" autofocus required>
          <button class="btn-send" id="sendBtn" type="submit" form="vsendform"
                  formnovalidate {% if cooldown > 0 %}disabled{% endif %}>
            {% if cooldown > 0 %}{{ cooldown }} 秒后重试{% else %}获取验证码{% endif %}
          </button>
        </div>
        <div class="copy-hint" style="margin-top:8px;">
          验证码将发送到 {{ email_masked }}
        </div>
      </div>
      {% elif method == 'totp' %}
      <div class="field">
        <label for="vc">身份验证器验证码</label>
        <input id="vc" type="tel" name="code" placeholder="000000" maxlength="6"
               pattern="[0-9]{6}" autocomplete="one-time-code" autofocus required>
      </div>
      {% else %}
      <div class="field">
        <label for="vc">一次性验证码</label>
        <input id="vc" type="text" name="code" placeholder="8 位一次性验证码" maxlength="11"
               autocomplete="off" autocapitalize="characters" spellcheck="false" autofocus required>
        <div class="copy-hint" style="margin-top:8px;">
          使用后该验证码立即失效，剩余 {{ recovery_left }} 个
        </div>
      </div>
      {% endif %}
      <button class="btn-primary" type="submit" name="_action" value="check">验证</button>
    </form>

    {% endif %}
    <div class="footer-links">
      {% if other_count > 0 %}
      <a class="link-mini" href="{{ verify_pick_url }}">其他选项</a>
      {% endif %}
      <a class="link-mini" href="{{ verify_cancel_url }}">取消</a>
    </div>
    {% endif %}
  </div>
</div>

{% if not picking and method == 'email' %}
<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>
{% endif %}
<script>
  var sendBtn = document.getElementById('sendBtn');
  if (sendBtn && sendBtn.disabled) {
    // 前端倒计时只是提示，真正的限速在后端 email_codes.sent_at 上
    var left = {{ cooldown }};
    var timer = setInterval(function () {
      left -= 1;
      if (left <= 0) {
        clearInterval(timer);
        sendBtn.disabled = false;
        sendBtn.textContent = '获取验证码';
      } else {
        sendBtn.textContent = left + ' 秒后重试';
      }
    }, 1000);
  }
</script>
"""

_VERIFY_HTML = _shell("身份验证", _VERIFY_BODY).replace("</style>", _VERIFY_CSS + "</style>")


def _verify_state() -> dict | None:
    if request.args.get('flow'):
        flow=_yidt_flow(request.args['flow'], states=('pending',))
        if not flow:return None
        return dict(kind='sensitive',user=flow['user'],purpose='yidt_pin',method=flow['method'],flow=flow['token'])
    st = session.get("_v")
    return st if isinstance(st, dict) and st.get("user") else None


def _render_verify(st: dict, user: dict, error="", notice="", picking=False):
    methods = twofa_methods(user, str(st.get("kind") or "sensitive"))
    if st.get("no_passkey"):methods=[m for m in methods if m!="passkey"]
    method = st.get("method") or (methods[0] if methods else "email")
    email = str(user.get("email") or "")
    recovery_left = sum(1 for c in get_recovery_codes(user) if not c.get("used"))
    # 绑定/更换邮箱时验证码发往新地址，其余情况发往账号邮箱
    target = str(st.get("target") or email)

    _passkey_session()
    subs = {
        "passkey":"使用设备解锁、手机或 USB 安全密钥",
        "email": f"发送到 {_mask_email(target)}" if target else "未绑定邮箱",
        "totp": "输入验证器 App 中的 6 位验证码",
        "recovery": f"剩余 {recovery_left} 个可用",
    }
    return render_template_string(
        _VERIFY_HTML,
        verify_url=url_for('verify', **({'flow':st['flow']} if st.get('flow') else {})),
        verify_pick_url=url_for('verify', pick=1, **({'flow':st['flow']} if st.get('flow') else {})),
        verify_cancel_url=url_for('verify_cancel', **({'flow':st['flow']} if st.get('flow') else {})),
        picking=picking,
        method=method,
        methods=methods,
        labels=_METHOD_LABEL,
        icons=_METHOD_ICONS,
        subs=subs,
        error=error,
        notice=notice,
        purpose_text=_purpose_text(str(st.get("purpose") or "")),
        email_masked=_mask_email(target) or "（未绑定邮箱）",
        cooldown=email_code_cooldown_left(user["username"]),
        turnstile_sitekey=TURNSTILE_SITEKEY,
        turnstile_email_action=TURNSTILE_EMAIL_ACTION,
        recovery_left=recovery_left,
        other_count=max(0, len(methods) - 1),
    )


@app.route("/verify/cancel/")
def verify_cancel():
    if request.args.get('flow'):
        import yidt_core as y
        flow=_yidt_flow(request.args['flow'], states=('pending',))
        if flow:
            with y.connect() as c:c.execute("UPDATE yidt_account_flows SET state='used' WHERE token=?",(flow['token'],))
        return redirect(url_for('account',err='已取消支付密码验证'))
    st = _verify_state() or {}
    kind = str(st.get("kind") or "sensitive")
    session.pop("_v", None)
    if kind == "login":
        _clear_tmp_session()
        return redirect("/login/")
    return redirect(url_for("account", err="已取消验证"))


@app.route("/verify/", methods=["GET", "POST"])
def verify():
    st = _verify_state()
    if not st:
        if request.args.get('flow'):
            return redirect(url_for('yidt_pin',flow=request.args['flow']))
        return redirect("/account/" if session.get("user") else "/login/")

    username = str(st["user"])
    user = get_user(username)
    if not user:
        session.pop("_v", None)
        _clear_tmp_session()
        return redirect("/login/")

    kind = str(st.get("kind") or "sensitive")
    purpose = str(st.get("purpose") or "")
    methods = twofa_methods(user, kind)
    if st.get("no_passkey"):methods=[m for m in methods if m!="passkey"]
    if st.get("no_passkey") and not methods:
        session.pop("_v",None)
        return redirect(url_for("account",err="请先绑定邮箱验证或身份验证器以继续"))
    if st.get("target") and "email" not in methods:
        methods = ["email"] + methods          # 绑定新邮箱时邮箱验证一定可用

    if not methods and st.get('flow'):
        return redirect(url_for('yidt_reauth',flow=st['flow']))

    if not methods:
        # 验证方式在中途被移除：敏感操作直接放行，登录直接完成
        session.pop("_v", None)
        if kind == "login":
            return _complete_login_from_verify(st)
        _grant_ticket(purpose)
        return redirect(_safe_next(st.get("next"), "/account/"))

    if st.get("method") not in methods:
        st["method"] = methods[0]
        if not st.get('flow'):session["_v"] = st

    if request.method == "POST":
        if st.get('flow') and (not session.get('_yidt_csrf') or not secrets.compare_digest(request.form.get('csrf',''),session['_yidt_csrf'])):
            return '请求无效，请刷新后重试',403
        action = (request.form.getlist("_action") or [""])[-1] or request.form.get("action", "")

        if action == "pick":
            picked = request.form.get("method", "")
            if picked in methods:
                st["method"] = picked
                if st.get('flow'):
                    import yidt_core as y
                    with y.connect() as c:c.execute("UPDATE yidt_account_flows SET method=? WHERE token=? AND state='pending'",(picked,st['flow']))
                    return redirect(url_for('verify',flow=st['flow']))
                session["_v"] = st
                return redirect(url_for("verify"))
            # 没选方法 → 进入选择页
            return redirect(url_for("verify", pick="1", **({'flow':st['flow']} if st.get('flow') else {})))

        if action == "send":
            target = str(st.get("target") or user.get("email") or "")
            if not target:
                return _render_verify(st, user, error="当前账号没有可用邮箱")
            left = email_code_cooldown_left(username)
            if left:
                return _render_verify(st, user, error=f"发送过于频繁，请 {left} 秒后重试")
            human_ok, human_err = _verify_turnstile(
                request.form.get("cf-turnstile-response", ""), TURNSTILE_EMAIL_ACTION
            )
            if not human_ok:
                return _render_verify(st, user, error=human_err)
            ok, err, _ = issue_email_code(username, target, purpose)
            if not ok:
                return _render_verify(st, user, error=err)
            return _render_verify(st, user, notice="验证码已发送，请查收邮件")

        if action == "check":
            code = request.form.get("code", "")
            method = str(st.get("method"))

            if method == "email":
                ok, err = verify_email_code(username, purpose, code)
            elif method == "totp":
                ok = _verify_totp(user.get("totp_secret"), code)
                err = "" if ok else "验证码错误，请重试"
            elif method == "recovery" and kind == "login":
                ok = consume_recovery_code(username, code)
                err = "" if ok else "一次性验证码无效或已被使用"
            else:
                # 一次性验证码只能用于登录，敏感操作不接受
                ok, err = False, "该验证方式不可用于此操作"

            if not ok:
                return _render_verify(st, user, error=err)

            if st.get('flow'):
                _yidt_verified(st['flow'])
                return redirect(url_for('yidt_pin',flow=st['flow']))
            session.pop("_v", None)
            if kind == "login":
                return _complete_login_from_verify(st)
            _grant_ticket(purpose)
            return redirect(_safe_next(st.get("next"), "/account/"))

    return _render_verify(st, user, picking=request.args.get("pick") == "1")


def _complete_login_from_verify(st: dict):
    """登录场景验证通过：写 session，并把这台设备记为受信。"""
    username = str(st["user"])
    next_url = _safe_next(st.get("next"), "/")
    remember = bool(session.get("_lremember", False))
    _finish_login(username, remember=remember)
    _clear_tmp_session()
    resp = redirect(next_url)
    token = remember_device(username)
    resp.set_cookie(
        TRUSTED_DEVICE_COOKIE, token,
        max_age=TRUSTED_DEVICE_DAYS * 86400,
        secure=True, httponly=True, samesite="Lax",
    )
    return resp


# ── 注册（用户名 → guest）─────────────────────────────────────────────────────
_CREATEACCOUNT_BODY = """
<div class="page">
  <div class="card">
    <a href="/" style="display:contents;">
      <img class="logo" src="{{ url_for('static', filename='logo.svg') }}" alt="Yitiao">
    </a>

    {% if step == 1 %}
    <h1>注册</h1>
    {% if error %}<div class="error-msg">{{ error }}</div>{% endif %}

    {% if google_ready %}
    <a class="btn-google" href="{{ url_for('google_login', next='/') }}">
      """ + _GOOGLE_SVG + """
      使用 Google 账号注册
    </a>
    <div class="step-desc" style="margin:12px 0 0;font-size:13px;">
      Google 注册的账号为 <b>user</b> 等级，需绑定身份验证器
    </div>
    {% else %}
    <div class="btn-google disabled" title="服务器未配置 Google OAuth">
      """ + _GOOGLE_SVG + """
      Google 注册未配置
    </div>
    {% endif %}

    <div class="or-split">或用用户名注册</div>

    <form method="post">
      <input type="hidden" name="_action" value="register">

      <div class="field">
        <label for="un">用户名</label>
        <input id="un" type="text" name="username" placeholder="请输入用户名"
               autocomplete="username" value="{{ form_username }}" required>
      </div>

      <div class="field">
        <label for="em">邮箱（可选）</label>
        <input id="em" type="text" name="email" placeholder="填写邮箱可用邮箱登录，账号等级为 user"
               autocomplete="email" value="{{ form_email }}">
        <div class="copy-hint" style="margin-top:8px;">
          不填邮箱将注册为 guest 账号；填写后需完成邮箱验证
        </div>
      </div>

      <div class="field">
        <label for="pw">密码</label>
        <input id="pw" type="password" name="password" placeholder="请输入密码"
               autocomplete="new-password" required>
      </div>

      <div class="pw-rules">
        <div class="pw-rule" id="ruleLen">密码至少 8 位</div>
        <div class="pw-rule" id="ruleMix">密码需要同时包含字母和数字</div>
      </div>

      <div class="field">
        <label for="cp">确认密码</label>
        <input id="cp" type="password" name="confirm_password" placeholder="再次输入密码"
               autocomplete="new-password" required>
        <div class="inline-error" id="cpError"></div>
      </div>

      <label class="remember-row" for="usev">
        <input id="usev" type="checkbox" name="use_validator" value="1">
        <span>同时绑定身份验证器（可选）</span>
      </label>

      <div class="turnstile-wrap">
        <div class="cf-turnstile" data-sitekey="{{ turnstile_sitekey }}"
             data-callback="turnstile_ok" data-error-callback="turnstile_fail"
             data-expired-callback="turnstile_fail"></div>
      </div>

      <input type="hidden" id="turnstileToken" name="turnstile_token" value="">
      <button id="regBtn" class="btn-primary" type="submit" disabled>注册</button>
      <div class="footer-links">
        <a class="link-mini" href="/login/">已有账号？登录</a>
      </div>
    </form>

    {% elif step == 2 %}
    <h1>绑定验证器</h1>
    <div class="step-desc">注册完成前请先绑定身份验证器<br>也可以直接跳过</div>
    {% if error %}<div class="error-msg">{{ error }}</div>{% endif %}
""" + _TOTP_BIND_BLOCK + """
    <form method="post">
      <input type="hidden" name="_action" value="setup_totp">
      <div class="field">
        <label for="tc">输入 App 中显示的 6 位验证码</label>
        <input id="tc" type="tel" name="totp_code" placeholder="000000" maxlength="6"
               pattern="[0-9]{6}" autocomplete="one-time-code" autofocus required>
      </div>
      <div class="btn-row">
        <a href="/createaccount/back/" class="btn-secondary">返回</a>
        <button class="btn-secondary" type="submit" name="_skip" value="1" formnovalidate style="flex:.9;">跳过</button>
        <button class="btn-primary-half" type="submit">完成绑定</button>
      </div>
    </form>

    {% elif step == 3 %}
    <h1>验证邮箱</h1>
    <div class="step-desc">
      验证码已发送到 <b>{{ pending_email }}</b><br>
      完成验证后即注册为 user 等级账号
    </div>
    {% if error %}<div class="error-msg">{{ error }}</div>{% endif %}
    {% if notice %}<div class="notice-msg">{{ notice }}</div>{% endif %}
    <form method="post" id="regResendForm">
      <input type="hidden" name="_action" value="resend_email">
      <div class="turnstile-wrap">
        <div class="cf-turnstile" data-sitekey="{{ turnstile_sitekey }}"
             data-action="{{ turnstile_email_action }}" data-theme="dark"
             data-size="compact"></div>
      </div>
    </form>
    <form method="post">
      <div class="field">
        <label for="ec">邮箱验证码</label>
        <div class="code-row" style="display:flex;gap:10px;align-items:stretch;">
          <input id="ec" type="tel" name="email_code" placeholder="000000" maxlength="6"
                 pattern="[0-9]{6}" autocomplete="one-time-code" autofocus required
                 style="flex:1;">
          <button class="btn-send" id="sendBtn" type="submit" form="regResendForm"
                  formnovalidate {% if cooldown > 0 %}disabled{% endif %}
                  style="flex-shrink:0;min-width:116px;height:50px;padding:0 14px;
                         border:1px solid rgba(90,167,255,.38);border-radius:14px;
                         background:rgba(45,113,255,.16);color:var(--accent2);
                         font:600 13.5px/1 inherit;cursor:pointer;">
            {% if cooldown > 0 %}{{ cooldown }} 秒后重试{% else %}重新发送{% endif %}
          </button>
        </div>
      </div>
      <div class="btn-row">
        <a href="/createaccount/back/" class="btn-secondary">返回</a>
        <button class="btn-primary-half" type="submit" name="_action" value="verify_email">完成注册</button>
      </div>
    </form>
    {% endif %}
  </div>
</div>

<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>
<script>
  var pw = document.getElementById('pw');
  if (pw) {
    var cp = document.getElementById('cp');
    var ruleLen = document.getElementById('ruleLen');
    var ruleMix = document.getElementById('ruleMix');
    var cpError = document.getElementById('cpError');
    var regBtn = document.getElementById('regBtn');
    var tsToken = document.getElementById('turnstileToken');

    function validate() {
      var v = pw.value || '';
      var lenOk = v.length >= 8;
      var mixOk = /[A-Za-z]/.test(v) && /[0-9]/.test(v);
      ruleLen.classList.toggle('ok', lenOk);  ruleLen.classList.toggle('bad', !lenOk);
      ruleMix.classList.toggle('ok', mixOk);  ruleMix.classList.toggle('bad', !mixOk);
      var confirmOk = cp.value.length === 0 ? true : (pw.value === cp.value);
      cpError.classList.toggle('show', !confirmOk);
      cpError.textContent = confirmOk ? '' : '两次输入的密码不一致';
      pw.classList.toggle('invalid', !lenOk || !mixOk);
      cp.classList.toggle('invalid', !confirmOk);
      return lenOk && mixOk && confirmOk && cp.value.length > 0;
    }
    function sync() { regBtn.disabled = !(validate() && !!tsToken.value); }
    window.turnstile_ok = function (t) { tsToken.value = t || ''; sync(); };
    window.turnstile_fail = function () { tsToken.value = ''; sync(); };
    pw.addEventListener('input', sync);
    cp.addEventListener('input', sync);
    sync();
  }

  var sendBtn = document.getElementById('sendBtn');
  if (sendBtn && sendBtn.disabled) {
    var left = {{ cooldown }};
    var timer = setInterval(function () {
      left -= 1;
      if (left <= 0) {
        clearInterval(timer);
        sendBtn.disabled = false;
        sendBtn.textContent = '重新发送';
      } else {
        sendBtn.textContent = left + ' 秒后重试';
      }
    }, 1000);
  }
</script>
""" + _COPY_JS

_CREATEACCOUNT_HTML = _shell("注册", _CREATEACCOUNT_BODY)


def _render_create(step=1, error="", qr_b64="", new_secret="", notice="",
                   form_username="", form_email=""):
    session["_cstage"] = step        # GET 刷新时据此回到同一步
    pending_email = session.get("_cpending_email", "")
    # 注册时账号还不存在，限速键用 "reg:<邮箱>"
    cooldown = email_code_cooldown_left(f"reg:{pending_email}") if pending_email else 0
    return render_template_string(
        _CREATEACCOUNT_HTML,
        step=step,
        error=error,
        notice=notice,
        qr_b64=qr_b64,
        new_secret=new_secret,
        turnstile_sitekey=TURNSTILE_SITEKEY,
        turnstile_email_action=TURNSTILE_EMAIL_ACTION,
        google_ready=google_ready(),
        pending_email=pending_email,
        cooldown=cooldown,
        form_username=form_username or session.get("_cpending_user", ""),
        form_email=form_email or pending_email,
    )


@app.route("/createaccount/back/")
def createaccount_back():
    _clear_tmp_session()
    return redirect("/createaccount/")


@app.route("/createaccount/", methods=["GET", "POST"])
def createaccount():
    if session.get("user"):
        return redirect("/account/")

    def finish(username: str, password_hash: str, email: str = "",
               totp_secret: str | None = None):
        """注册收尾：有邮箱 → user，没有 → guest。"""
        create_user(
            username, password_hash=password_hash,
            role="user" if email else "guest",
            totp_secret=totp_secret,
            email=email or None,
        )
        if email:
            clear_email_code(f"reg:{_norm_email(email)}")
        _finish_login(username)
        _clear_tmp_session()
        return redirect("/")

    def send_reg_code(email: str):
        return issue_email_code(f"reg:{_norm_email(email)}", email, "bind_email")

    if request.method == "POST":
        action = (request.form.getlist("_action") or [""])[-1]

        if action == "register":
            username = request.form.get("username", "").strip()
            email = _norm_email(request.form.get("email", ""))
            password = request.form.get("password", "")
            confirm = request.form.get("confirm_password", "")
            use_validator = request.form.get("use_validator") == "1"

            ok, msg = _verify_turnstile(request.form.get("turnstile_token", ""))
            error = "" if ok else msg
            error = error or _validate_username(username) or _validate_password(password, confirm)
            if not error and email:
                error = _validate_email(email)
                if not error and get_user_by_email(email):
                    error = "该邮箱已被其他账号使用"
            if error:
                return _render_create(step=1, error=error,
                                      form_username=username, form_email=email)

            session["_cpending_user"] = username
            session["_cpending_pw_hash"] = generate_password_hash(password)
            session["_cpending_email"] = email

            if use_validator:
                secret = pyotp.random_base32()
                session["_cnewsecret"] = secret
                return _render_create(step=2, qr_b64=_make_qr_b64(_totp_uri(username, secret)),
                                      new_secret=secret)

            if email:
                sent, err, _ = send_reg_code(email)
                if not sent:
                    session.pop("_cpending_email", None)
                    return _render_create(step=1, error=err,
                                          form_username=username, form_email=email)
                return _render_create(step=3, notice="验证码已发送，请查收邮件")

            return finish(username, session["_cpending_pw_hash"])

        if action == "setup_totp":
            username = session.get("_cpending_user", "")
            password = session.get("_cpending_pw_hash", "")
            email = session.get("_cpending_email", "")
            secret = session.get("_cnewsecret", "")
            if not (username and password and secret):
                _clear_tmp_session()
                return _render_create(step=1, error="会话已过期，请重新注册")

            skipped = request.form.get("_skip") == "1"
            if not skipped and not _verify_totp(secret, request.form.get("totp_code", "")):
                return _render_create(step=2, error="验证码错误，请重试",
                                      qr_b64=_make_qr_b64(_totp_uri(username, secret)),
                                      new_secret=secret)
            if skipped:
                secret = None
            session["_cnewsecret"] = secret or ""

            if email:
                sent, err, _ = send_reg_code(email)
                if not sent:
                    return _render_create(step=2, error=err,
                                          qr_b64=_make_qr_b64(_totp_uri(username, session["_cnewsecret"]))
                                          if secret else "",
                                          new_secret=secret or "")
                return _render_create(step=3, notice="验证码已发送，请查收邮件")

            return finish(username, password, totp_secret=secret)

        if action in ("verify_email", "resend_email"):
            username = session.get("_cpending_user", "")
            password = session.get("_cpending_pw_hash", "")
            email = session.get("_cpending_email", "")
            secret = session.get("_cnewsecret", "") or None
            if not (username and password and email):
                _clear_tmp_session()
                return _render_create(step=1, error="会话已过期，请重新注册")

            if action == "resend_email":
                left = email_code_cooldown_left(f"reg:{email}")
                if left:
                    return _render_create(step=3, error=f"发送过于频繁，请 {left} 秒后重试")
                human_ok, human_err = _verify_turnstile(
                    request.form.get("cf-turnstile-response", ""), TURNSTILE_EMAIL_ACTION
                )
                if not human_ok:
                    return _render_create(step=3, error=human_err)
                sent, err, _ = send_reg_code(email)
                return _render_create(step=3, error="" if sent else err,
                                      notice="验证码已重新发送" if sent else "")

            ok, err = verify_email_code(f"reg:{email}", "bind_email",
                                        request.form.get("email_code", ""))
            if not ok:
                return _render_create(step=3, error=err)

            # 验证期间可能被别人抢注
            error = _validate_username(username)
            if not error and get_user_by_email(email):
                error = "该邮箱已被其他账号使用"
            # 验证所有权后检查冷却期
            if not error and identity_cooling("email", email):
                error = "该邮箱已被其他账号占用"
            if error:
                _clear_tmp_session()
                return _render_create(step=1, error=error)

            return finish(username, password, email=email, totp_secret=secret)

    # GET：恢复中间步骤
    username = session.get("_cpending_user", "")
    if username and session.get("_cstage") == 3:
        return _render_create(step=3)
    secret = session.get("_cnewsecret", "")
    if username and secret:
        return _render_create(step=2, qr_b64=_make_qr_b64(_totp_uri(username, secret)),
                              new_secret=secret)
    return _render_create(step=1)


# ── Google OIDC 路由 ─────────────────────────────────────────────────────────
_ONBOARD_BODY = """
<div class="page">
  <div class="card">
    <a href="/" style="display:contents;">
      <img class="logo" src="{{ url_for('static', filename='logo.svg') }}" alt="Yitiao">
    </a>

    {% if step == 1 %}
    <h1>设置用户名</h1>
    <div class="step-desc">
      已通过 Google 验证{% if g_email %}：<b>{{ g_email }}</b>{% endif %}<br>
      请为你的 Yitiao 账号设置一个用户名
    </div>
    {% if error %}<div class="error-msg">{{ error }}</div>{% endif %}
    <form method="post">
      <input type="hidden" name="_action" value="username">
      <div class="field">
        <label for="un">用户名</label>
        <input id="un" type="text" name="username" value="{{ suggested }}"
               placeholder="大于 5 位，字母数字，不能以数字开头"
               autocomplete="username" autofocus required>
      </div>
      <button class="btn-primary" type="submit">下一步</button>
      <a class="btn-back" href="{{ url_for('google_onboarding_cancel') }}">取消</a>
    </form>

    {% elif step == 2 %}
    <h1>设置密码</h1>
    <div class="step-desc">
      密码是可选的。设置后你可以用<br>用户名 + 密码登录，也可以继续用 Google 登录
    </div>
    {% if error %}<div class="error-msg">{{ error }}</div>{% endif %}
    <form method="post">
      <input type="hidden" name="_action" value="password">
      <div class="field">
        <label for="pw">密码</label>
        <input id="pw" type="password" name="password" placeholder="请输入密码"
               autocomplete="new-password">
      </div>
      <div class="pw-rules">
        <div class="pw-rule" id="ruleLen">密码至少 8 位</div>
        <div class="pw-rule" id="ruleMix">密码需要同时包含字母和数字</div>
      </div>
      <div class="field">
        <label for="cp">确认密码</label>
        <input id="cp" type="password" name="confirm_password" placeholder="再次输入密码"
               autocomplete="new-password">
        <div class="inline-error" id="cpError"></div>
      </div>
      <div class="btn-row">
        <button class="btn-secondary" type="submit" name="_skip" value="1">跳过</button>
        <button class="btn-primary-half" type="submit">下一步</button>
      </div>
    </form>

    {% elif step == 3 %}
    <h1>绑定验证器</h1>
    <div class="step-desc">
      建议绑定身份验证器保护账号<br>请用 Google Authenticator 等 App 扫描二维码
    </div>
    {% if error %}<div class="error-msg">{{ error }}</div>{% endif %}
""" + _TOTP_BIND_BLOCK + """
    <form method="post">
      <input type="hidden" name="_action" value="totp">
      <div class="field">
        <label for="tc">输入 App 中显示的 6 位验证码</label>
        <input id="tc" type="tel" name="totp_code" placeholder="000000" maxlength="6"
               pattern="[0-9]{6}" autocomplete="one-time-code" autofocus required>
      </div>
      <div class="btn-row">
        <button class="btn-secondary" type="submit" name="_skip" value="1" formnovalidate>跳过</button>
        <button class="btn-primary-half" type="submit">完成注册</button>
      </div>
      <a class="btn-back" href="{{ url_for('google_onboarding_cancel') }}">取消注册</a>
    </form>
    {% endif %}
  </div>
</div>

<script>
  var pw = document.getElementById('pw');
  if (pw) {
    var cp = document.getElementById('cp');
    var ruleLen = document.getElementById('ruleLen');
    var ruleMix = document.getElementById('ruleMix');
    var cpError = document.getElementById('cpError');
    function validate() {
      var v = pw.value || '';
      if (!v && !cp.value) {
        ruleLen.classList.remove('ok', 'bad');
        ruleMix.classList.remove('ok', 'bad');
        cpError.classList.remove('show');
        pw.classList.remove('invalid'); cp.classList.remove('invalid');
        return;
      }
      var lenOk = v.length >= 8;
      var mixOk = /[A-Za-z]/.test(v) && /[0-9]/.test(v);
      ruleLen.classList.toggle('ok', lenOk);  ruleLen.classList.toggle('bad', !lenOk);
      ruleMix.classList.toggle('ok', mixOk);  ruleMix.classList.toggle('bad', !mixOk);
      var confirmOk = cp.value.length === 0 ? true : (pw.value === cp.value);
      cpError.classList.toggle('show', !confirmOk);
      cpError.textContent = confirmOk ? '' : '两次输入的密码不一致';
      pw.classList.toggle('invalid', !lenOk || !mixOk);
      cp.classList.toggle('invalid', !confirmOk);
    }
    pw.addEventListener('input', validate);
    cp.addEventListener('input', validate);
  }
</script>
""" + _COPY_JS

_ONBOARD_HTML = _shell("完成注册", _ONBOARD_BODY)


def _render_onboard(step=1, error="", qr_b64="", new_secret=""):
    return render_template_string(
        _ONBOARD_HTML,
        step=step,
        error=error,
        qr_b64=qr_b64,
        new_secret=new_secret,
        suggested=session.get("_g_user", ""),
        g_email=session.get("_g_email", ""),
    )


@app.route("/login/google/")
def google_login():
    if not google_ready():
        return _render_login(step=1, error="服务器未配置 Google OAuth", next_url="/")
    return _google_authorize_redirect("login", request.args.get("next", "/"))


@app.route("/account/bindgoogle/")
def bind_google_start():
    """绑定 / 换绑 Google：先过两步验证，再跳去 Google 授权。"""
    user = _current_user()
    if not user:
        return redirect(url_for("login", next="/account/"))
    if not google_ready():
        return redirect(url_for("account", err="服务器未配置 Google OAuth"))

    purpose = "rebind_google" if user.get("google_sub") else "bind_google"
    guard = require_twofa(user, purpose, "/account/bindgoogle/")
    if guard is not None:
        return guard
    return _google_authorize_redirect("bind", "/account/")


@app.route("/login/google/callback/")
def google_callback():
    mode = session.get("_g_mode", "login")
    next_url = _safe_next(session.get("_g_next"), "/")

    if request.args.get("error"):
        _clear_tmp_session()
        if mode == 'yidt':return redirect(next_url+'&error=已取消Google验证')
        if mode == "bind":
            return redirect(url_for("account", err="已取消 Google 授权"))
        return redirect(url_for("login", next=next_url, err="已取消 Google 授权"))

    try:
        # authlib 内部校验 state / PKCE / nonce，失败会抛异常
        claims = _google_fetch_claims()
    except Exception as exc:
        _clear_tmp_session()
        if mode == 'yidt':return redirect(next_url+'&error=Google验证失败，请重试')
        if mode == "bind":
            return redirect(url_for("account", err=f"Google 登录失败：{exc}"))
        return redirect(url_for("login", next=next_url, err=f"Google 登录失败：{exc}"))

    # state 已被 authorize_access_token() 消费，清掉残留避免下次撞车
    _clear_google_state()

    sub = claims["sub"]
    email = claims.get("email") or ""
    if mode == 'yidt':
        from urllib.parse import urlparse,parse_qs
        token=parse_qs(urlparse(next_url).query).get('flow',[''])[0]
        flow=_yidt_flow(token,states=('pending',));me=_current_user()
        if not flow or not me or me.get('google_sub') != sub:
            return redirect(next_url+'&error=Google账号不匹配或验证已过期')
        if _needs_twofa_for_sensitive(me):return redirect(url_for('yidt_reauth',flow=token))
        _yidt_verified(token)
        return redirect(url_for('yidt_pin',flow=token))

    # ── 绑定模式：把 Google 挂到当前登录账号上 ──
    # 两步验证已经在 bind_google_start 里做完了，这里只落库。
    if mode == "bind":
        me = _current_user()
        if not me:
            _clear_tmp_session()
            return redirect(url_for("login", next="/account/"))

        rebind = bool(me.get("google_sub"))
        purpose = "rebind_google" if rebind else "bind_google"
        if _needs_twofa_for_sensitive(me) and not _has_ticket(purpose):
            _clear_tmp_session()
            return redirect(url_for("account", err="验证已过期，请重新操作"))

        owner = get_user_by_google_sub(sub)
        if owner and owner["username"] != me["username"]:
            _clear_tmp_session()
            return redirect(url_for("account", err="该 Google 账号已绑定到其他 Yitiao 账号"))

        # 验证所有权后检查冷却期
        if identity_cooling("google", sub, ignore_username=me["username"]):
            _clear_tmp_session()
            return redirect(url_for("account", err="该 Google 账号已被其他账号占用"))

        bind_google(me["username"], sub, email)
        consume_ticket(purpose)
        forget_devices(me["username"])
        msg = "Google 账号已更换" if rebind else "Google 账号已绑定"
        # guest 升级为 user，admin 保持 admin
        if str(me.get("role")) == "guest":
            set_role(me["username"], "user")
            session["role"] = "user"
            msg += "，账号等级已升级为 user"
        _clear_tmp_session()
        return redirect(url_for("account", ok=msg))

    # ── 登录模式：已绑定则登录，否则进入 onboarding ──
    user = get_user_by_google_sub(sub)
    if user:
        # 检查账号是否已被永久删除
        if user.get("purged_at"):
            return redirect(url_for("login", err="账号已删除"))

        # 检查账号是否在安全期内（deleted_at 有值但未 purge）
        if user.get("deleted_at") and not user.get("purged_at"):
            restore_account(user["username"])
            session["_account_restored"] = True
        session["_lremember"] = True
        return _login_next_step(user, next_url, via_redirect=True)

    # 注册流程：OAuth 完成后检查冷却期
    if identity_cooling("google", sub):
        _clear_tmp_session()
        return redirect(url_for("login", next=next_url, err="该 Google 账号已被其他账号占用"))

    session["_g_sub"] = sub
    session["_g_email"] = email
    session["_g_name"] = claims.get("name") or ""
    session["_g_user"] = _suggest_username(claims)
    session.pop("_g_pw_hash", None)
    session.pop("_g_secret", None)
    return redirect(url_for("google_onboarding"))


@app.route("/login/google/onboarding/cancel/")
def google_onboarding_cancel():
    _clear_tmp_session()
    return redirect("/login/")


@app.route("/login/google/onboarding/", methods=["GET", "POST"])
def google_onboarding():
    """Google 注册收尾：用户名（必填）→ 密码（可跳过）→ 验证器（必绑）。"""
    sub = session.get("_g_sub", "")
    if not sub:
        return redirect("/login/")
    if get_user_by_google_sub(sub):
        _clear_tmp_session()
        return redirect("/login/")

    if request.method == "POST":
        action = request.form.get("_action", "")

        if action == "username":
            username = request.form.get("username", "").strip()
            error = _validate_username(username)
            if error:
                session["_g_user"] = username
                return _render_onboard(step=1, error=error)
            session["_g_user"] = username
            return _render_onboard(step=2)

        if action == "password":
            if request.form.get("_skip") == "1":
                session.pop("_g_pw_hash", None)
            else:
                password = request.form.get("password", "")
                confirm = request.form.get("confirm_password", "")
                if not password:
                    return _render_onboard(step=2, error="请输入密码，或点击跳过")
                error = _validate_password(password, confirm)
                if error:
                    return _render_onboard(step=2, error=error)
                session["_g_pw_hash"] = generate_password_hash(password)

            secret = pyotp.random_base32()
            session["_g_secret"] = secret
            return _render_onboard(step=3,
                                   qr_b64=_make_qr_b64(_totp_uri(session["_g_user"], secret)),
                                   new_secret=secret)

        if action == "totp":
            username = session.get("_g_user", "")
            secret = session.get("_g_secret", "")
            if not (username and secret):
                return _render_onboard(step=1, error="会话已过期，请重新设置")

            # Google 注册也允许跳过绑定验证器
            if request.form.get("_skip") == "1":
                secret = None
            elif not _verify_totp(secret, request.form.get("totp_code", "")):
                return _render_onboard(step=3, error="验证码错误，请重试",
                                       qr_b64=_make_qr_b64(_totp_uri(username, secret)),
                                       new_secret=secret)

            error = _validate_username(username)
            if error:
                return _render_onboard(step=1, error=error)

            create_user(
                username,
                password_hash=session.get("_g_pw_hash"),
                role="user",
                totp_secret=secret,
                google_sub=sub,
                google_email=session.get("_g_email", ""),
                nickname=session.get("_g_name", "") or "",
            )
            _finish_login(username, remember=True)
            _clear_tmp_session()
            return redirect("/")

    # GET：按 session 里已有的数据决定停在哪一步
    if session.get("_g_secret") and session.get("_g_user"):
        return _render_onboard(step=3,
                               qr_b64=_make_qr_b64(_totp_uri(session["_g_user"], session["_g_secret"])),
                               new_secret=session["_g_secret"])
    return _render_onboard(step=1)


# ── 账户中心 ──────────────────────────────────────────────────────────────────
_ACCOUNT_CSS = """
    .acct-card { width: min(560px, 100%); padding: 38px 34px 30px; }
    .acct-head {
      display: flex; align-items: center; gap: 18px; margin-bottom: 26px;
    }
    .acct-avatar, .acct-avatar-img {
      width: 78px; height: 78px; border-radius: 50%; flex-shrink: 0;
      border: 2px solid rgba(255,255,255,.14);
      box-shadow: 0 10px 26px rgba(0,0,0,.34);
    }
    .acct-avatar {
      background: linear-gradient(135deg, var(--accent2), var(--accent));
      display: flex; align-items: center; justify-content: center;
      font-size: 32px; font-weight: 800; color: #fff;
    }
    .acct-avatar-img { object-fit: cover; }
    .acct-id { min-width: 0; flex: 1; text-align: left; }
    .acct-name {
      font-size: 22px; font-weight: 800; line-height: 1.25;
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }
    .acct-handle { margin-top: 4px; font-size: 13px; color: var(--muted2); }
    .badge {
      display: inline-flex; align-items: center; gap: 5px;
      margin-top: 9px; padding: 4px 11px; border-radius: 999px;
      font-size: 11.5px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase;
    }
    .badge-user {
      background: rgba(45,113,255,.18); border: 1px solid rgba(90,167,255,.42); color: var(--accent2);
    }
    .badge-guest {
      background: rgba(255,255,255,.07); border: 1px solid rgba(255,255,255,.16); color: var(--muted);
    }
    .sec-title {
      font-size: 11.5px; font-weight: 700; letter-spacing: .10em;
      text-transform: uppercase; color: var(--muted2);
      margin: 24px 0 12px;
    }
    .row {
      display: flex; align-items: center; gap: 14px;
      padding: 15px 16px; margin-bottom: 10px;
      background: rgba(255,255,255,.05);
      border: 1px solid rgba(255,255,255,.09);
      border-radius: 16px;
      transition: background .16s, border-color .16s;
    }
    a.row:hover, .row.clickable:hover {
      background: rgba(255,255,255,.09); border-color: rgba(255,255,255,.16);
    }
    .row-ico {
      width: 38px; height: 38px; border-radius: 11px; flex-shrink: 0;
      display: flex; align-items: center; justify-content: center;
      background: rgba(45,113,255,.16); color: var(--accent2);
    }
    .row-ico.plain { background: rgba(255,255,255,.07); color: var(--muted); }
    .row-ico svg { width: 18px; height: 18px; }
    .row-body { flex: 1; min-width: 0; text-align: left; }
    .row-title { font-size: 15px; font-weight: 600; }
    .row-sub {
      margin-top: 3px; font-size: 12.5px; color: var(--muted2);
      line-height: 1.45; overflow: hidden; text-overflow: ellipsis;
    }
    .row-sub.ok { color: var(--ok); }
    .row-sub.warn { color: #ffc357; }
    .chev { color: var(--muted2); flex-shrink: 0; }
    .chev svg { width: 16px; height: 16px; }
    .pill-state {
      flex-shrink: 0; padding: 5px 12px; border-radius: 999px;
      font-size: 12px; font-weight: 700;
    }
    .pill-on  { background: rgba(56,210,106,.14); border: 1px solid rgba(56,210,106,.34); color: var(--ok); }
    .pill-off { background: rgba(255,255,255,.07); border: 1px solid rgba(255,255,255,.16); color: var(--muted2); }
    .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    .grid-2 .row { margin-bottom: 0; }
    /* 两步验证小窗口：一个开关 + 三种验证方式 */
    .tf-panel {
      background: rgba(255,255,255,.04);
      border: 1px solid rgba(255,255,255,.09);
      border-radius: 18px; padding: 6px; margin-bottom: 10px;
    }
    .tf-head {
      display: flex; align-items: center; gap: 14px;
      padding: 14px 14px 15px;
      border-bottom: 1px solid rgba(255,255,255,.07);
    }
    .tf-head-body { flex: 1; min-width: 0; text-align: left; }
    .tf-switch {
      flex-shrink: 0; width: 50px; height: 28px; padding: 0;
      border: 1px solid rgba(255,255,255,.16); border-radius: 999px;
      background: rgba(255,255,255,.08); cursor: pointer;
      position: relative; transition: background .18s, border-color .18s;
    }
    .tf-switch.on {
      background: linear-gradient(135deg, var(--accent2), var(--accent));
      border-color: rgba(90,167,255,.5);
    }
    .tf-switch:disabled { opacity: .4; cursor: not-allowed; }
    .tf-knob {
      position: absolute; top: 2px; left: 2px;
      width: 22px; height: 22px; border-radius: 50%;
      background: #fff; box-shadow: 0 2px 6px rgba(0,0,0,.34);
      transition: transform .18s;
    }
    .tf-switch.on .tf-knob { transform: translateX(22px); }
    .tf-row {
      display: flex; align-items: center; gap: 14px;
      padding: 13px 14px; border-radius: 14px;
      transition: background .16s;
    }
    .tf-row:hover { background: rgba(255,255,255,.07); }
    .tf-row .m-ico {
      width: 34px; height: 34px; border-radius: 10px; flex-shrink: 0;
      display: flex; align-items: center; justify-content: center;
      background: rgba(45,113,255,.16); color: var(--accent2);
    }
    .tf-row .m-ico svg { width: 17px; height: 17px; }
    .tf-row .row-body { display: block; }
    .tf-row .row-title, .tf-row .row-sub { display: block; }
    /* 一次性验证码清单 */
    .rc-grid {
      display: grid; grid-template-columns: 1fr 1fr; gap: 10px;
      margin-bottom: 22px;
    }
    .rc-item {
      padding: 12px 10px; text-align: center; border-radius: 12px;
      background: rgba(255,255,255,.06);
      border: 1px solid rgba(255,255,255,.12);
      font: 15px/1 "JetBrains Mono","Fira Code",ui-monospace,monospace;
      letter-spacing: .12em; color: var(--accent2);
    }
    .rc-item.used {
      color: rgba(234,241,255,.26); text-decoration: line-through;
      background: rgba(255,255,255,.03); border-color: rgba(255,255,255,.07);
    }
    .btn-logout {
      display: flex; align-items: center; justify-content: center; gap: 8px;
      width: 100%; height: 46px; margin-top: 18px;
      border: 1px solid rgba(255,255,255,.14); border-radius: 14px;
      background: rgba(255,255,255,.06);
      color: rgba(234,241,255,.70); font: 14px/1 inherit; cursor: pointer;
      transition: background .15s, color .15s, border-color .15s;
    }
    .btn-logout:hover {
      background: rgba(255,107,107,.10); border-color: rgba(255,107,107,.30); color: #ff8a8a;
    }
    @media (max-width: 520px) {
      .grid-2 { grid-template-columns: 1fr; }
      .acct-card { padding: 30px 20px 24px; }
    }
"""

_IC_GOOGLE_MONO = """<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12.24 10.29v3.5h5.79c-.23 1.5-1.74 4.4-5.79 4.4-3.49 0-6.34-2.89-6.34-6.44s2.85-6.44 6.34-6.44c1.99 0 3.32.85 4.08 1.58l2.78-2.68C17.34 2.4 15.03 1.4 12.24 1.4 6.86 1.4 2.5 5.76 2.5 11.14s4.36 9.74 9.74 9.74c5.62 0 9.34-3.95 9.34-9.51 0-.64-.07-1.13-.16-1.62h-9.18z"/></svg>"""
_IC_SHIELD = """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>"""
_IC_KEY = """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 2l-2 2m-7.6 7.6a5 5 0 1 1-7.07 7.07 5 5 0 0 1 7.07-7.07zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3"/></svg>"""
_IC_IMAGE = """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg>"""
_IC_EDIT = """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4v16h16v-7"/><path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>"""
_IC_CHEV = """<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>"""

_ACCOUNT_BODY = """
<div class="page">
  <div class="card acct-card {{ 'acct-settings' if account_section != 'home' else '' }}">
    {% if account_section == 'home' %}
    <a href="/" style="display:contents;">
      <img class="logo" src="{{ url_for('static', filename='logo.svg') }}" alt="Yitiao"
           style="width:56px;height:56px;margin-bottom:20px;">
    </a>

    {% if ok_msg %}<div class="notice-msg">{{ ok_msg }}</div>{% endif %}
    {% if err_msg %}<div class="error-msg">{{ err_msg }}</div>{% endif %}

    {% include 'yidt_daily.html' %}
    <div class="acct-head">
      <a href="{{ url_for('change_avatar') }}" title="更换头像" style="display:contents;">
        {% if avatar_url %}
        <img class="acct-avatar-img" src="{{ avatar_url }}" alt="头像">
        {% else %}
        <div class="acct-avatar">{{ username[0] | upper }}</div>
        {% endif %}
      </a>
      <div class="acct-id">
        {% if nickname %}<div class="acct-name">{{ nickname }}</div>{% endif %}
        <div class="acct-handle">@{{ username }}</div>
        <span class="badge {{ 'badge-user' if role == 'user' else 'badge-guest' }}">{{ role }}</span>
      </div>
    </div>

    {% include 'account_social_summary.html' %}
    <a class="row" href="/points/">
      <div class="row-ico plain">✦</div>
      <div class="row-body"><div class="row-title">我的 YIDT · {{ points_balance }}</div><div class="row-sub">钱包、授权与公开账本</div></div>
      <div class="chev">›</div>
    </a>

    <a class="row" href="/account/profile/"><div class="row-body"><div class="row-title">个人资料设置</div></div><div class="chev">›</div></a>
    <a class="row" href="/account/security/"><div class="row-body"><div class="row-title">账户与安全</div></div><div class="chev">›</div></a>
    <a class="row" href="/account/privacy/"><div class="row-body"><div class="row-title">隐私设置</div></div><div class="chev">›</div></a>
    <a class="row" href="/account/qr/"><div class="row-body"><div class="row-title">我的二维码</div></div><div class="chev">›</div></a>
    {% else %}
    <nav class="settings-top"><a href="/account/" class="settings-back">‹ 返回账户</a></nav>
    <h1>{{ '个人资料设置' if account_section == 'profile' else '账户与安全' }}</h1>
    {% if ok_msg %}<div class="notice-msg" role="status">{{ ok_msg }}</div>{% endif %}
    {% if err_msg %}<div class="error-msg" role="alert">{{ err_msg }}</div>{% endif %}
    {% endif %}
    {% if account_section == 'profile' %}
    <div class="grid-2">
      <a class="row" href="{{ url_for('change_avatar') }}">
        <div class="row-ico">""" + _IC_IMAGE + """</div>
        <div class="row-body"><div class="row-title">更换头像</div></div>
      </a>
      <a class="row" href="{{ url_for('change_profile') }}">
        <div class="row-ico">""" + _IC_EDIT + """</div>
        <div class="row-body"><div class="row-title">昵称与自我介绍</div></div>
      </a>
    </div>

    {% endif %}
    {% if account_section == 'security' %}<div class="sec-title">账户与安全 · 登录方式</div>

    <a class="row" href="{{ url_for('manage_email') }}">
      <div class="row-ico">""" + _IC_MAIL + """</div>
      <div class="row-body">
        <div class="row-title">邮箱</div>
        <div class="row-sub {{ 'ok' if email else '' }}">
          {% if email %}{{ email }}{% else %}未设置，添加后可用邮箱登录并启用邮箱验证{% endif %}
        </div>
      </div>
      <div class="chev">""" + _IC_CHEV + """</div>
    </a>

    {% if google_ready %}
    <a class="row" href="{{ url_for('manage_google') }}">
      <div class="row-ico">""" + _IC_GOOGLE_MONO + """</div>
      <div class="row-body">
        <div class="row-title">Google 账号</div>
        <div class="row-sub {{ 'ok' if google_email else '' }}">
          {% if google_email %}{{ google_email }}{% else %}未绑定，绑定后可用 Google 快速登录{% endif %}
        </div>
      </div>
      <div class="chev">""" + _IC_CHEV + """</div>
    </a>
    {% else %}
    <div class="row">
      <div class="row-ico plain">""" + _IC_GOOGLE_MONO + """</div>
      <div class="row-body">
        <div class="row-title">Google 账号</div>
        <div class="row-sub">服务器未配置 Google OAuth</div>
      </div>
      <div class="pill-state pill-off">不可用</div>
    </div>
    {% endif %}

    <a class="row" href="{{ url_for('change_password') }}">
      <div class="row-ico">""" + _IC_KEY + """</div>
      <div class="row-body">
        <div class="row-title">{{ '修改密码' if has_password else '设置密码' }}</div>
        <div class="row-sub">
          {% if has_password %}用户名 / 邮箱 + 密码登录{% else %}未设置密码，设置后可用密码登录{% endif %}
        </div>
      </div>
      <div class="chev">""" + _IC_CHEV + """</div>
    </a>

    <a class="row" href="/account/passkeys/"><div class="row-ico plain">⌘</div><div class="row-body"><div class="row-title">通行密钥</div><div class="row-sub">添加 / 管理 · 最多 8 个 · 可用于登录和敏感操作验证</div></div><div class="chev">›</div></a>
    <div class="sec-title">两步验证</div>
    <div class="tf-panel">
      <div class="tf-head">
        <div class="tf-head-body">
          <div class="row-title">登录时要求两步验证</div>
          <div class="row-sub">
            {% if not has_twofa %}
            还没有可用的验证方式，请先设置下方任一方式
            {% elif twofa_on %}
            新设备登录时需要完成一次验证
            {% else %}
            登录不验证，但敏感操作仍会强制验证
            {% endif %}
          </div>
        </div>
        <form method="post" action="{{ url_for('twofa_toggle') }}">
          <input type="hidden" name="want" value="{{ 0 if twofa_on else 1 }}">
          <button class="tf-switch {{ 'on' if twofa_on else '' }}" type="submit"
                  role="switch" aria-label="登录时要求两步验证" aria-checked="{{ 'true' if twofa_on else 'false' }}"
                  {% if not has_twofa %}disabled title="没有可用的验证方式"{% endif %}>
            <span class="tf-knob"></span>
          </button>
        </form>
      </div>

      <a class="tf-row" href="{{ url_for('manage_email') }}">
        <span class="m-ico">""" + _IC_MAIL + """</span>
        <span class="row-body">
          <span class="row-title">邮箱验证</span>
          <span class="row-sub">{{ '已启用 · ' ~ email_masked if email else '设置邮箱后自动启用' }}</span>
        </span>
        <span class="pill-state {{ 'pill-on' if email else 'pill-off' }}">{{ '已启用' if email else '未启用' }}</span>
      </a>

      <a class="tf-row" href="{{ url_for('manage_totp') }}">
        <span class="m-ico">""" + _IC_PHONE + """</span>
        <span class="row-body">
          <span class="row-title">验证器验证</span>
          <span class="row-sub">{{ '已绑定身份验证器' if has_totp else '未绑定，点击设置' }}</span>
        </span>
        <span class="pill-state {{ 'pill-on' if has_totp else 'pill-off' }}">{{ '已启用' if has_totp else '未启用' }}</span>
      </a>

      <a class="tf-row" href="{{ url_for('recovery_codes') }}">
        <span class="m-ico">""" + _IC_LIST + """</span>
        <span class="row-body">
          <span class="row-title">一次性验证码</span>
          <span class="row-sub">
            {% if recovery_total %}剩余 {{ recovery_left }} / {{ recovery_total }} 个可用{% else %}未获取，点击获取 10 个{% endif %}
            &nbsp;·&nbsp; 仅登录时可用
          </span>
        </span>
        <span class="pill-state {{ 'pill-on' if recovery_left else 'pill-off' }}">{{ '已启用' if recovery_left else '未启用' }}</span>
      </a>
    </div>

    {% include 'account_devices.html' %}
    <a class="btn-delete" href="{{ url_for('delete_account') }}" style="display:block;text-align:center;margin:18px 0 12px;padding:13px;background:rgba(255,69,58,0.12);border:1px solid rgba(255,69,58,0.28);border-radius:10px;color:#ff6b6b;font-size:14.5px;font-weight:500;text-decoration:none;">删除账户</a>

    <form method="post" action="{{ url_for('logout') }}">
      <button class="btn-logout" type="submit">
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/>
          <polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/>
        </svg>
        退出登录
      </button>
    </form>
    {% endif %}
  </div>
</div>
"""

_ACCOUNT_HTML = _shell("账户", _ACCOUNT_BODY).replace("</style>", _ACCOUNT_CSS + "</style>").replace('</head>', '<link rel="stylesheet" href="/static/account-settings.css">\n</head>')


@app.get("/status/<path:filename>")
def game_image(filename):
    return send_from_directory(BASE_DIR / "status", filename)


@app.get("/static/parachute.svg")
@app.get("/static/parachute-plane.svg")
@app.get("/static/parachute-jump.svg")
def legacy_game_image():
    return redirect("/status/" + request.path.rsplit("/", 1)[-1], code=308)


@app.route("/account/profile/")
@app.route("/account/security/")
@app.route("/account/")
def account():
    user = _current_user()
    if not user:
        return redirect(url_for("login", next="/account/"))

    username = user["username"]
    codes = get_recovery_codes(user)
    email = str(user.get("email") or "")

    # 显示账户恢复提示
    ok_msg = request.args.get("ok", "")
    if session.pop("_account_restored", False):
        ok_msg = "账户已恢复"

    return render_template_string(
        _ACCOUNT_HTML,
        account_section='security' if request.path.endswith('/security/') else 'profile' if request.path.endswith('/profile/') else 'home',
        social=account_social_view(username, username),
        points_balance=yidt_account_data(username)['total'],
        yidt_daily=yidt_account_data(username),
        username=username,
        nickname=user.get("nickname") or "",
        role=str(user.get("role") or "guest"),
        avatar_url=_user_avatar_url(username),
        email=email,
        email_masked=_mask_email(email),
        google_email=user.get("google_email") or "",
        google_ready=google_ready(),
        has_totp=bool(user.get("totp_secret")),
        has_twofa=has_twofa(user),
        twofa_on=twofa_on_login(user),
        recovery_total=len(codes),
        recovery_left=sum(1 for c in codes if not c.get("used")),
        has_password=bool(user.get("password_hash")),
        ok_msg=ok_msg,
        err_msg=request.args.get("err", ""),
    )


# ── 两步验证：开关 / 验证器 / 一次性验证码 ───────────────────────────────────
_TWOFA_BODY = """
<div class="page">
  <div class="card">
    <a href="/" style="display:contents;">
      <img class="logo" src="{{ url_for('static', filename='logo.svg') }}" alt="Yitiao">
    </a>
    <h1>{{ title }}</h1>

    {% if mode == 'bind' %}
    <div class="step-desc">
      {% if has_totp %}
      更改验证器后，旧的验证器立即失效<br>请用 Google Authenticator 等 App 扫描新二维码
      {% else %}
      绑定身份验证器后即可作为两步验证方式<br>请用 Google Authenticator 等 App 扫描二维码
      {% endif %}
    </div>
    {% if error %}<div class="error-msg">{{ error }}</div>{% endif %}
""" + _TOTP_BIND_BLOCK + """
    <form method="post">
      <input type="hidden" name="_action" value="bind">
      <div class="field">
        <label for="tc">输入 App 中显示的 6 位验证码</label>
        <input id="tc" type="tel" name="totp_code" placeholder="000000" maxlength="6"
               pattern="[0-9]{6}" autocomplete="one-time-code" autofocus required>
      </div>
      <button class="btn-primary" type="submit">{{ '确认更改' if has_totp else '完成绑定' }}</button>
      <a class="btn-back" href="{{ back_url }}">返回</a>
    </form>

    {% elif mode == 'manage' %}
    <div class="step-desc">身份验证器已绑定<br>可以更改为新的验证器，或移除后改用其他方式</div>
    {% if error %}<div class="error-msg">{{ error }}</div>{% endif %}
    <a class="btn-primary" href="{{ url_for('manage_totp', act='change') }}"
       style="text-decoration:none;">更改验证器</a>
    <form method="post" style="margin-top:10px;">
      <input type="hidden" name="_action" value="remove">
      <button class="btn-back" type="submit" style="margin-top:0;">移除验证器</button>
    </form>
    <a class="btn-back" href="{{ back_url }}">返回</a>
    {% endif %}
  </div>
</div>
""" + _COPY_JS

_TWOFA_HTML = _shell("两步验证", _TWOFA_BODY)


def _render_twofa(mode, error="", qr_b64="", new_secret="", has_totp=False,
                  title="两步验证", back_url="/account/"):
    return render_template_string(
        _TWOFA_HTML,
        mode=mode,
        error=error,
        qr_b64=qr_b64,
        new_secret=new_secret,
        has_totp=has_totp,
        title=title,
        back_url=back_url,
    )


def _twofa_bind_page(username: str, error: str = "", has_totp: bool = False,
                     back_url: str = "/account/"):
    secret = session.get("_lnewsecret") or pyotp.random_base32()
    session["_lnewsecret"] = secret
    return _render_twofa("bind", error=error, qr_b64=_make_qr_b64(_totp_uri(username, secret)),
                         new_secret=secret, has_totp=has_totp,
                         title="更改验证器" if has_totp else "绑定验证器", back_url=back_url)


@app.route("/account/2fa/toggle/", methods=["POST"])
def twofa_toggle():
    """两步验证总开关。只控制登录时验不验，与已绑定的方式相互独立。"""
    user = _current_user()
    if not user:
        return redirect(url_for("login", next="/account/"))

    want_on = request.form.get("want") == "1"
    if want_on and not has_twofa(user):
        return redirect(url_for("account", err="请先设置至少一种两步验证方式"))

    purpose = "twofa_on" if want_on else "twofa_off"
    guard = require_twofa(user, purpose, "/account/2fa/toggle/apply/")
    if guard is not None:
        session["_tf_want"] = 1 if want_on else 0
        return guard

    return _apply_twofa_toggle(user, want_on, purpose)


def _apply_twofa_toggle(user: dict, want_on: bool, purpose: str):
    set_totp_on_login(user["username"], want_on)
    consume_ticket(purpose)
    forget_devices(user["username"])
    return redirect(url_for(
        "account", ok="两步验证已开启" if want_on else "两步验证已关闭"))


@app.route("/account/2fa/toggle/apply/")
def twofa_toggle_apply():
    """/verify/ 通过后回到这里落库（GET，因为 verify 是 302 过来的）。"""
    user = _current_user()
    if not user:
        return redirect(url_for("login", next="/account/"))
    want_on = session.pop("_tf_want", None) == 1
    purpose = "twofa_on" if want_on else "twofa_off"
    if not _has_ticket(purpose):
        return redirect(url_for("account", err="验证已过期，请重试"))
    if want_on and not has_twofa(user):
        return redirect(url_for("account", err="请先设置至少一种两步验证方式"))
    return _apply_twofa_toggle(user, want_on, purpose)


@app.route("/account/2fa/totp/", methods=["GET", "POST"])
def manage_totp():
    """设置 / 更改 / 移除身份验证器。开启验证器本身不需要先过两步验证。"""
    user = _current_user()
    if not user:
        return redirect(url_for("login", next="/account/2fa/totp/"))

    username = user["username"]
    has_totp = bool(user.get("totp_secret"))

    if request.method == "POST":
        action = request.form.get("_action", "")

        if action == "bind":
            # 更改验证器属于敏感操作，需要先验证；首次绑定不需要
            if has_totp:
                guard = require_twofa(user, "set_totp", "/account/2fa/totp/?act=change")
                if guard is not None:
                    return guard
            secret = session.get("_lnewsecret", "")
            if not secret:
                return _twofa_bind_page(username, error="会话已过期，请重新操作",
                                        has_totp=has_totp)
            if not _verify_totp(secret, request.form.get("totp_code", "")):
                return _render_twofa("bind", error="验证码错误，请重试",
                                     qr_b64=_make_qr_b64(_totp_uri(username, secret)),
                                     new_secret=secret, has_totp=has_totp,
                                     title="更改验证器" if has_totp else "绑定验证器")
            set_totp(username, secret)
            if not has_totp and not twofa_on_login(user):
                set_totp_on_login(username, True)   # 首次拥有验证方式，默认开启
            session.pop("_lnewsecret", None)
            if has_totp:
                consume_ticket("set_totp")
            forget_devices(username)
            return redirect(url_for(
                "account", ok="验证器已更改" if has_totp else "身份验证器已绑定，两步验证已开启"))

        if action == "remove":
            if not has_totp:
                return redirect(url_for("account", err="尚未绑定身份验证器"))
            guard = require_twofa(user, "remove_totp", "/account/2fa/totp/remove/")
            if guard is not None:
                return guard
            return _apply_remove_totp(user)

    if not has_totp or request.args.get("act") == "change":
        session.pop("_lnewsecret", None)
        return _twofa_bind_page(username, has_totp=has_totp)
    return _render_twofa("manage", has_totp=True, title="验证器验证")


def _apply_remove_totp(user: dict):
    username = user["username"]
    set_totp(username, None)
    consume_ticket("remove_totp")
    # 移除后可能一个方式都不剩，开关自动落到关闭
    if not has_twofa(get_user(username) or {}):
        set_totp_on_login(username, False)
    forget_devices(username)
    return redirect(url_for("account", ok="身份验证器已移除"))


@app.route("/account/2fa/totp/remove/")
def manage_totp_remove():
    user = _current_user()
    if not user:
        return redirect(url_for("login", next="/account/"))
    if not user.get("totp_secret"):
        return redirect(url_for("account", err="尚未绑定身份验证器"))
    if not _has_ticket("remove_totp"):
        return redirect(url_for("account", err="验证已过期，请重试"))
    return _apply_remove_totp(user)


@app.route("/account/2fa/recovery/", methods=["GET", "POST"])
def recovery_codes():
    """一次性验证码：只支持查看 / 获取 / 重置，一次重置刷新全部 10 个。"""
    user = _current_user()
    if not user:
        return redirect(url_for("login", next="/account/2fa/recovery/"))

    username = user["username"]
    codes = get_recovery_codes(user)
    first_time = not codes
    purpose = "recovery" if first_time else "reset_recovery"

    # GET 和 POST 都需要验证
    if not _has_ticket(purpose):
        guard = require_twofa(user, purpose, "/account/2fa/recovery/apply/")
        if guard is not None:
            return guard

    if request.method == "POST":
        return _apply_reset_recovery(user, purpose)

    return _render_recovery(user, codes)


def _apply_reset_recovery(user: dict, purpose: str):
    username = user["username"]
    codes = reset_recovery_codes(username)
    consume_ticket(purpose)
    fresh = get_user(username) or {}
    if not int(fresh.get("totp_on_login") or 0):
        set_totp_on_login(username, True)     # 有了可用方式，默认开启开关
        fresh = get_user(username) or {}
    return _render_recovery(fresh, codes, notice="已生成 10 个新的一次性验证码，旧的已全部作废")


@app.route("/account/2fa/recovery/apply/")
def recovery_codes_apply():
    user = _current_user()
    if not user:
        return redirect(url_for("login", next="/account/"))
    purpose = "reset_recovery" if get_recovery_codes(user) else "recovery"
    if not _has_ticket(purpose):
        return redirect(url_for("account", err="验证已过期，请重试"))
    return _apply_reset_recovery(user, purpose)


_RECOVERY_BODY = """
<div class="page">
  <div class="card acct-card">
    <a href="/" style="display:contents;">
      <img class="logo" src="{{ url_for('static', filename='logo.svg') }}" alt="Yitiao"
           style="width:56px;height:56px;margin-bottom:20px;">
    </a>
    <h1>一次性验证码</h1>
    <div class="step-desc">
      每个验证码只能使用一次，共 {{ total_count }} 个<br>
      <b>只能在登录时使用</b>，账号内的敏感操作不接受<br>
      请抄写或截图保存，用完可以重置
    </div>
    {% if notice %}<div class="notice-msg">{{ notice }}</div>{% endif %}
    {% if error %}<div class="error-msg">{{ error }}</div>{% endif %}

    {% if codes %}
    <div class="rc-grid">
      {% for c in codes %}
      <div class="rc-item {{ 'used' if c.used else '' }}">{{ c.code }}</div>
      {% endfor %}
    </div>
    <div class="copy-hint" style="text-align:center;margin:-14px 0 20px;">
      划掉的是已使用的验证码 &nbsp;·&nbsp; 剩余 {{ left_count }} 个可用
    </div>
    {% else %}
    <div class="step-desc" style="margin-bottom:22px;">
      当前还没有一次性验证码，点击下方按钮获取 {{ total_wanted }} 个
    </div>
    {% endif %}

    <form method="post" {% if codes %}onsubmit="return confirm('重置会作废现有全部验证码，确定继续？');"{% endif %}>
      <button class="btn-primary" type="submit">{{ '重置全部验证码' if codes else '获取验证码' }}</button>
    </form>
    <a class="btn-back" href="{{ url_for('account') }}">返回</a>
  </div>
</div>
"""

_RECOVERY_HTML = _shell("一次性验证码", _RECOVERY_BODY).replace("</style>", _ACCOUNT_CSS + "</style>")


def _render_recovery(user: dict, codes: list[dict], notice="", error=""):
    return render_template_string(
        _RECOVERY_HTML,
        codes=codes,
        total_count=len(codes),
        left_count=sum(1 for c in codes if not c.get("used")),
        total_wanted=RECOVERY_CODE_COUNT,
        notice=notice,
        error=error,
    )


# ── 邮箱：添加 / 更换 / 移除 ─────────────────────────────────────────────────
def _other_login_methods(user: dict, ignore: str = "") -> int:
    """密码之外的登录方式数量：邮箱、Google。ignore 用于「移除后还剩几个」。"""
    count = 0
    if user.get("email") and ignore != "email":
        count += 1
    if user.get("google_sub") and ignore != "google":
        count += 1
    return count


_EMAIL_BODY = """
<div class="page">
  <div class="card">
    <a href="/" style="display:contents;">
      <img class="logo" src="{{ url_for('static', filename='logo.svg') }}" alt="Yitiao">
    </a>
    <h1>{{ '更换邮箱' if email else '添加邮箱' }}</h1>

    {% if step == 'form' %}
    <div class="step-desc">
      {% if email %}
      当前邮箱：<b>{{ email }}</b><br>更换后需要验证新邮箱
      {% else %}
      添加邮箱后可用邮箱登录，并自动启用邮箱验证{% if role == 'guest' %}<br>账号等级将升级为 user{% endif %}
      {% endif %}
    </div>
    {% if error %}<div class="error-msg">{{ error }}</div>{% endif %}
    <form method="post">
      <input type="hidden" name="_action" value="start">
      <div class="field">
        <label for="em">{{ '新邮箱' if email else '邮箱' }}</label>
        <input id="em" type="text" name="email" placeholder="name@example.com"
               autocomplete="email" value="{{ form_email }}" autofocus required>
      </div>
      <div class="turnstile-wrap">
        <div class="cf-turnstile" data-sitekey="{{ turnstile_sitekey }}"
             data-action="{{ turnstile_email_action }}" data-theme="dark"
             data-size="compact"></div>
      </div>
      <button class="btn-primary" type="submit">发送验证码</button>
    </form>
    {% if email %}
    <form method="post"
          onsubmit="return confirm('移除邮箱后将无法用邮箱登录和接收验证码，确定继续？');">
      <input type="hidden" name="_action" value="remove">
      <button class="btn-back" type="submit">移除邮箱</button>
    </form>
    {% endif %}
    <a class="btn-back" href="{{ url_for('account') }}">返回</a>

    {% else %}
    <div class="step-desc">
      验证码已发送到 <b>{{ target }}</b><br>请输入邮件中的 6 位验证码完成绑定
    </div>
    {% if error %}<div class="error-msg">{{ error }}</div>{% endif %}
    {% if notice %}<div class="notice-msg">{{ notice }}</div>{% endif %}
    <form method="post" id="emailResendForm">
      <input type="hidden" name="_action" value="resend">
      <div class="turnstile-wrap">
        <div class="cf-turnstile" data-sitekey="{{ turnstile_sitekey }}"
             data-action="{{ turnstile_email_action }}" data-theme="dark"
             data-size="compact"></div>
      </div>
    </form>
    <form method="post">
      <div class="field">
        <label for="ec">邮箱验证码</label>
        <div style="display:flex;gap:10px;align-items:stretch;">
          <input id="ec" type="tel" name="email_code" placeholder="000000" maxlength="6"
                 pattern="[0-9]{6}" autocomplete="one-time-code" autofocus required style="flex:1;">
          <button id="sendBtn" type="submit" form="emailResendForm" formnovalidate
                  {% if cooldown > 0 %}disabled{% endif %}
                  style="flex-shrink:0;min-width:116px;height:50px;padding:0 14px;
                         border:1px solid rgba(90,167,255,.38);border-radius:14px;
                         background:rgba(45,113,255,.16);color:var(--accent2);
                         font:600 13.5px/1 inherit;cursor:pointer;">
            {% if cooldown > 0 %}{{ cooldown }} 秒后重试{% else %}重新发送{% endif %}
          </button>
        </div>
      </div>
      <button class="btn-primary" type="submit" name="_action" value="confirm">确认绑定</button>
      <a class="btn-back" href="{{ url_for('manage_email', reset=1) }}">返回</a>
    </form>
    {% endif %}
  </div>
</div>

<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>
<script>
  var sendBtn = document.getElementById('sendBtn');
  if (sendBtn && sendBtn.disabled) {
    var left = {{ cooldown }};
    var timer = setInterval(function () {
      left -= 1;
      if (left <= 0) {
        clearInterval(timer); sendBtn.disabled = false;
        sendBtn.textContent = '重新发送';
      } else { sendBtn.textContent = left + ' 秒后重试'; }
    }, 1000);
  }
</script>
"""

_EMAIL_HTML = _shell("邮箱", _EMAIL_BODY)


@app.route("/account/email/", methods=["GET", "POST"])
def manage_email():
    user = _current_user()
    if not user:
        return redirect(url_for("login", next="/account/email/"))

    username = user["username"]
    email = str(user.get("email") or "")
    role = str(user.get("role") or "guest")

    def render(step="form", error="", notice="", form_email=""):
        return render_template_string(
            _EMAIL_HTML,
            step=step, error=error, notice=notice,
            email=email, role=role,
            form_email=form_email,
            turnstile_sitekey=TURNSTILE_SITEKEY,
            turnstile_email_action=TURNSTILE_EMAIL_ACTION,
            target=session.get("_pending_email", ""),
            cooldown=email_code_cooldown_left(username),
        )

    if request.args.get("reset") == "1":
        session.pop("_pending_email", None)
        session.pop("_pending_email_turnstile", None)
        return redirect(url_for("manage_email"))

    if request.method == "POST":
        action = (request.form.getlist("_action") or [""])[-1]

        # ── 移除邮箱 ──
        if action == "remove":
            if not email:
                return redirect(url_for("account", err="当前没有绑定邮箱"))
            if role in ("user", "admin") and _other_login_methods(user, ignore="email") == 0:
                return render(error="user 等级账号在密码之外必须保留邮箱或 Google 中的一个")
            guard = require_twofa(user, "remove_email", "/account/email/remove/")
            if guard is not None:
                return guard
            return _apply_remove_email(user)

        # ── 添加 / 更换邮箱：先过两步验证，再验证新邮箱归属 ──
        if action == "start":
            new_email = _norm_email(request.form.get("email", ""))
            err = _validate_email(new_email)
            if not err and new_email == email:
                err = "新邮箱与当前邮箱相同"
            if not err:
                owner = get_user_by_email(new_email)
                if owner and owner["username"] != username:
                    err = "该邮箱已被其他账号使用"
            if err:
                return render(error=err, form_email=request.form.get("email", ""))

            left = email_code_cooldown_left(username)
            if left:
                return render(error=f"发送过于频繁，请 {left} 秒后重试", form_email=new_email)
            human_ok, human_err = _verify_turnstile(
                request.form.get("cf-turnstile-response", ""), TURNSTILE_EMAIL_ACTION
            )
            if not human_ok:
                return render(error=human_err, form_email=new_email)

            purpose = "change_email" if email else "bind_email"
            guard = require_twofa(user, purpose, "/account/email/")
            if guard is not None:
                session["_pending_email"] = new_email
                session["_pending_email_turnstile"] = new_email
                return guard

            session["_pending_email"] = new_email
            session.pop("_pending_email_turnstile", None)
            ok, err, _ = issue_email_code(username, new_email, purpose)
            if not ok:
                session.pop("_pending_email", None)
                return render(error=err, form_email=new_email)
            return render(step="code", notice="验证码已发送，请查收邮件")

        if action == "resend":
            pending = session.get("_pending_email", "")
            if not pending:
                return render(error="会话已过期，请重新填写邮箱")
            left = email_code_cooldown_left(username)
            if left:
                return render(step="code", error=f"发送过于频繁，请 {left} 秒后重试")
            human_ok, human_err = _verify_turnstile(
                request.form.get("cf-turnstile-response", ""), TURNSTILE_EMAIL_ACTION
            )
            if not human_ok:
                return render(step="code", error=human_err)
            purpose = "change_email" if email else "bind_email"
            ok, err, _ = issue_email_code(username, pending, purpose)
            return render(step="code", error="" if ok else err,
                          notice="验证码已重新发送" if ok else "")

        if action == "confirm":
            pending = session.get("_pending_email", "")
            if not pending:
                return render(error="会话已过期，请重新填写邮箱")
            purpose = "change_email" if email else "bind_email"
            ok, err = verify_email_code(username, purpose,
                                        request.form.get("email_code", ""))
            if not ok:
                return render(step="code", error=err)

            owner = get_user_by_email(pending)
            if owner and owner["username"] != username:
                session.pop("_pending_email", None)
                return render(error="该邮箱已被其他账号使用")

            # 验证所有权后检查冷却期
            if identity_cooling("email", pending, ignore_username=username):
                session.pop("_pending_email", None)
                return render(error="该邮箱已被其他账号占用")

            set_email(username, pending)
            session.pop("_pending_email", None)
            session.pop("_pending_email_turnstile", None)
            consume_ticket(purpose)
            msg = "邮箱已更换" if email else "邮箱已添加，邮箱验证已启用"
            # guest 绑定邮箱后升级为 user
            if role == "guest":
                set_role(username, "user")
                session["role"] = "user"
                msg += "，账号等级已升级为 user"
            if not twofa_on_login(get_user(username) or {}):
                set_totp_on_login(username, True)
            forget_devices(username)
            return redirect(url_for("account", ok=msg))

    # GET：验证回来后如果票据在手且有待验证邮箱，直接发码进入第二步
    pending = session.get("_pending_email", "")
    if pending:
        purpose = "change_email" if email else "bind_email"
        if _has_ticket(purpose) or not _needs_twofa_for_sensitive(user):
            if email_code_target(username) == pending:
                session.pop("_pending_email_turnstile", None)
                return render(step="code")
            if session.pop("_pending_email_turnstile", None) != pending:
                session.pop("_pending_email", None)
                return render(error="请先完成人机验证", form_email=pending)
            ok, err, _ = issue_email_code(username, pending, purpose)
            if ok:
                return render(step="code", notice="验证码已发送，请查收邮件")
            session.pop("_pending_email", None)
            return render(error=err, form_email=pending)
    return render()


def _apply_remove_email(user: dict):
    username = user["username"]
    set_email(username, None)
    clear_email_code(username)
    consume_ticket("remove_email")
    if not has_twofa(get_user(username) or {}):
        set_totp_on_login(username, False)
    forget_devices(username)
    return redirect(url_for("account", ok="邮箱已移除"))


@app.route("/account/email/remove/")
def manage_email_remove():
    user = _current_user()
    if not user:
        return redirect(url_for("login", next="/account/"))
    if not user.get("email"):
        return redirect(url_for("account", err="当前没有绑定邮箱"))
    if not _has_ticket("remove_email"):
        return redirect(url_for("account", err="验证已过期，请重试"))
    return _apply_remove_email(user)


# ── Google：绑定 / 换绑 / 解绑 ───────────────────────────────────────────────
_GOOGLE_MANAGE_BODY = """
<div class="page">
  <div class="card">
    <a href="/" style="display:contents;">
      <img class="logo" src="{{ url_for('static', filename='logo.svg') }}" alt="Yitiao">
    </a>
    <h1>Google 账号</h1>
    <div class="step-desc">
      {% if google_email %}
      当前绑定：<b>{{ google_email }}</b>
      {% else %}
      绑定 Google 账号后可用 Google 快速登录{% if role == 'guest' %}<br>账号等级将升级为 user{% endif %}
      {% endif %}
    </div>
    {% if error %}<div class="error-msg">{{ error }}</div>{% endif %}

    <a class="btn-google" href="{{ url_for('bind_google_start') }}">
      """ + _GOOGLE_SVG + """
      {{ '更换绑定的 Google 账号' if google_email else '绑定 Google 账号' }}
    </a>

    {% if google_email %}
    <form method="post" style="margin-top:12px;"
          onsubmit="return confirm('解除绑定后将无法用 Google 登录，确定继续？');">
      <input type="hidden" name="_action" value="unbind">
      <button class="btn-back" type="submit" style="margin-top:0;">解除绑定</button>
    </form>
    {% endif %}
    <a class="btn-back" href="{{ url_for('account') }}">返回</a>
  </div>
</div>
"""

_GOOGLE_MANAGE_HTML = _shell("Google 账号", _GOOGLE_MANAGE_BODY)


@app.route("/account/google/", methods=["GET", "POST"])
def manage_google():
    user = _current_user()
    if not user:
        return redirect(url_for("login", next="/account/google/"))

    google_email = str(user.get("google_email") or "")
    role = str(user.get("role") or "guest")

    def render(error=""):
        return render_template_string(
            _GOOGLE_MANAGE_HTML,
            google_email=google_email, role=role, error=error,
        )

    if request.method == "POST" and request.form.get("_action") == "unbind":
        if not user.get("google_sub"):
            return redirect(url_for("account", err="当前没有绑定 Google 账号"))
        if role in ("user", "admin") and _other_login_methods(user, ignore="google") == 0:
            return render(error="user 等级账号在密码之外必须保留邮箱或 Google 中的一个")
        guard = require_twofa(user, "unbind_google", "/account/google/unbind/")
        if guard is not None:
            return guard
        return _apply_unbind_google(user)

    if not google_ready():
        return render(error="服务器未配置 Google OAuth")
    return render()


def _apply_unbind_google(user: dict):
    unbind_google(user["username"])
    consume_ticket("unbind_google")
    forget_devices(user["username"])
    return redirect(url_for("account", ok="Google 账号已解除绑定"))


@app.route("/account/google/unbind/")
def manage_google_unbind():
    user = _current_user()
    if not user:
        return redirect(url_for("login", next="/account/"))
    if not user.get("google_sub"):
        return redirect(url_for("account", err="当前没有绑定 Google 账号"))
    if not _has_ticket("unbind_google"):
        return redirect(url_for("account", err="验证已过期，请重试"))
    return _apply_unbind_google(user)


@app.route("/account/bindgoogle/cancel/")
def bind_google_cancel():
    _clear_tmp_session()
    return redirect(url_for("account", err="已取消绑定 Google 账号"))


# ── 修改密码 ─────────────────────────────────────────────────────────────────
_CHANGEPW_BODY = """
<div class="page">
  <div class="card">
    <a href="/" style="display:contents;">
      <img class="logo" src="{{ url_for('static', filename='logo.svg') }}" alt="Yitiao">
    </a>
    <h1>{{ '修改密码' if has_password else '设置密码' }}</h1>
    {% if not has_password %}
    <div class="step-desc">你的账号目前没有密码<br>设置后可以用用户名 + 密码登录</div>
    {% endif %}
    {% if error %}<div class="error-msg">{{ error }}</div>{% endif %}
    <form method="post">
      <div class="field">
        <label for="np">新密码</label>
        <input id="np" type="password" name="new_password" placeholder="至少 8 位，含字母和数字"
               autocomplete="new-password" autofocus required>
      </div>
      <div class="field">
        <label for="cp">确认新密码</label>
        <input id="cp" type="password" name="confirm_password" placeholder="再次输入新密码"
               autocomplete="new-password" required>
      </div>
      <button class="btn-primary" type="submit">确认</button>
      <a class="btn-back" href="{{ url_for('account') }}">返回</a>
    </form>
  </div>
</div>
"""

_CHANGEPW_HTML = _shell("修改密码", _CHANGEPW_BODY)


@app.route("/account/changepassword/", methods=["GET", "POST"])
def change_password():
    user = _current_user()
    if not user:
        return redirect(url_for("login", next="/account/changepassword/"))

    username = user["username"]
    role = str(user.get("role") or "guest")
    has_password = bool(user.get("password_hash"))

    def render(error=""):
        return render_template_string(
            _CHANGEPW_HTML,
            error=error,
            role=role,
            has_password=has_password,
        )

    if request.method == "POST":
        new_pw = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")

        if not new_pw:
            return render("密码不能为空")
        if len(new_pw) < 8:
            return render("密码长度至少 8 位")
        if not any(c.isalpha() for c in new_pw) or not any(c.isdigit() for c in new_pw):
            return render("密码需要同时包含字母和数字")
        if new_pw != confirm:
            return render("两次输入的密码不一致")

        purpose = "change_password" if has_password else "set_password"
        guard = require_twofa(user, purpose, "/account/changepassword/")
        if guard is not None:
            session["_pending_pw_hash"] = generate_password_hash(new_pw)
            return guard

        return _apply_password(username, generate_password_hash(new_pw), purpose)

    # 验证回来后带着票据直接落库
    pending = session.get("_pending_pw_hash", "")
    purpose = "change_password" if has_password else "set_password"
    if pending and _has_ticket(purpose):
        return _apply_password(username, pending, purpose)
    session.pop("_pending_pw_hash", None)
    return render()


def _apply_password(username: str, new_pw: str, purpose: str):
    _update_user(username, password_hash=new_pw)
    consume_ticket(purpose)
    session.pop("_pending_pw_hash", None)
    forget_devices(username)
    revoke_linked_sessions(username)
    session.clear()
    return redirect(url_for("login"))


# ── 注销账号 ─────────────────────────────────────────────────────────────────


@app.route("/account/delete/", methods=["GET", "POST"])
def delete_account():
    user = _current_user()
    if not user:
        return redirect(url_for("login", next="/account/delete/"))

    username = user["username"]

    if request.method == "POST":
        action = request.form.get("_action", "")

        # 第一步：确认删除意图
        if action == "confirm":
            guard = require_twofa(user, "delete_account", url_for("delete_account_verify"))
            if guard is not None:
                session["_delete_confirmed"] = True
                return guard
            return redirect(url_for("delete_account_verify"))

        # 第三步：手动输入 DELETE
        if action == "final":
            if not _has_ticket("delete_account"):
                return redirect(url_for("account", err="验证已过期，请重试"))

            typed = request.form.get("confirm_word", "").strip()
            if typed != DELETE_CONFIRM_WORD:
                return render_template_string(
                    _DELETE_FINAL_HTML,
                    error=f"请准确输入 {DELETE_CONFIRM_WORD}",
                )

            # 执行删除
            mark_for_deletion(username)
            consume_ticket("delete_account")
            session.clear()
            return redirect(url_for("login", msg="账号已进入 7 天安全期，期间登录即可恢复"))

    # GET：显示确认页面
    return render_template_string(_DELETE_CONFIRM_HTML, username=username)


@app.route("/account/delete/verify/")
def delete_account_verify():
    user = _current_user()
    if not user:
        return redirect(url_for("login"))

    if not session.get("_delete_confirmed"):
        return redirect(url_for("account"))

    if not _has_ticket("delete_account"):
        return redirect(url_for("account", err="验证已过期，请重试"))

    session.pop("_delete_confirmed", None)
    return render_template_string(_DELETE_FINAL_HTML)


_DELETE_CONFIRM_HTML = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>删除账户 – Yitiao</title>
  <link rel="icon" href="{{ url_for('static', filename='logo.ico') }}" type="image/x-icon">
  <style>
    :root {
      --bg0: #02101f; --bg1: #04142d;
      --text: #eaf1ff; --muted: rgba(234,241,255,0.66); --faint: rgba(234,241,255,0.46);
      --accent: #2d71ff; --accent2: #5aa7ff;
      --card: rgba(2,10,25,0.58); --border: rgba(255,255,255,0.10);
      --err: #ff6b6b; --warn: #f97316;
    }
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    html, body {
      min-height: 100vh; width: 100%;
      font-family: Inter, system-ui, -apple-system, sans-serif;
      background: radial-gradient(ellipse 110% 80% at 50% -8%, #10315f, var(--bg1) 58%, var(--bg0));
      color: var(--text);
    }
    .page { min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 28px 20px; }
    .card {
      width: min(480px, 100%);
      background: var(--card); border: 1px solid var(--border);
      border-radius: 26px; padding: 44px 38px 38px;
      backdrop-filter: blur(22px);
      box-shadow: 0 36px 90px rgba(0,0,0,0.46), inset 0 1px 0 rgba(255,255,255,0.06);
    }
    .logo { display: block; width: 64px; height: 64px; margin: 0 auto 20px; }
    h1 { font-size: 24px; font-weight: 700; text-align: center; margin-bottom: 14px; }
    .warn-box {
      background: rgba(249,115,22,0.12); border: 1px solid rgba(249,115,22,0.32);
      border-radius: 14px; padding: 18px 20px; margin-bottom: 24px;
      font-size: 14px; line-height: 1.7; color: var(--muted);
    }
    .warn-box strong { color: var(--warn); }
    .info-row { font-size: 14px; color: var(--faint); margin-bottom: 28px; text-align: center; }
    .btn-group { display: flex; gap: 12px; }
    .btn {
      flex: 1; padding: 14px 20px; border-radius: 12px; font-size: 15px; font-weight: 600;
      border: none; cursor: pointer; transition: all .18s;
    }
    .btn-cancel {
      background: rgba(255,255,255,0.08); color: var(--text);
    }
    .btn-cancel:hover { background: rgba(255,255,255,0.14); }
    .btn-danger {
      background: var(--err); color: #fff;
    }
    .btn-danger:hover { background: #ff5252; transform: translateY(-1px); }
    a { color: var(--accent2); text-decoration: none; }
  </style>
</head>
<body>
<div class="page">
  <div class="card">
    <a href="/"><img class="logo" src="{{ url_for('static', filename='logo.svg') }}" alt="Yitiao"></a>
    <h1>确认删除账户</h1>
    <div class="warn-box">
      <strong>删除账户后：</strong><br>
      • 账号进入 <strong>7 天安全期</strong>，期间任意方式登录即可恢复<br>
      • 7 天后未登录则<strong>永久删除</strong>个人数据和头像<br>
      • 用户名 <strong>{{ username }}</strong> 将被永久保留，无法重新注册<br>
      • 游戏排行榜数据保留，但显示默认头像
    </div>
    <div class="info-row">下一步将进行两步验证</div>
    <form method="post">
      <input type="hidden" name="_action" value="confirm">
      <div class="btn-group">
        <a href="/account/" class="btn btn-cancel">取消</a>
        <button type="submit" class="btn btn-danger">继续删除</button>
      </div>
    </form>
  </div>
</div>
</body>
</html>
"""

_DELETE_FINAL_HTML = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>最后确认 – Yitiao</title>
  <link rel="icon" href="{{ url_for('static', filename='logo.ico') }}" type="image/x-icon">
  <style>
    :root {
      --bg0: #02101f; --bg1: #04142d;
      --text: #eaf1ff; --muted: rgba(234,241,255,0.66);
      --accent: #2d71ff; --accent2: #5aa7ff;
      --card: rgba(2,10,25,0.58); --border: rgba(255,255,255,0.10);
      --err: #ff6b6b;
    }
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    html, body {
      min-height: 100vh; width: 100%;
      font-family: Inter, system-ui, -apple-system, sans-serif;
      background: radial-gradient(ellipse 110% 80% at 50% -8%, #10315f, var(--bg1) 58%, var(--bg0));
      color: var(--text);
    }
    .page { min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 28px 20px; }
    .card {
      width: min(440px, 100%);
      background: var(--card); border: 1px solid var(--border);
      border-radius: 26px; padding: 44px 38px 38px;
      backdrop-filter: blur(22px);
      box-shadow: 0 36px 90px rgba(0,0,0,0.46);
      text-align: center;
    }
    .logo { display: block; width: 64px; height: 64px; margin: 0 auto 20px; }
    h1 { font-size: 24px; font-weight: 700; margin-bottom: 14px; }
    .desc { font-size: 14px; color: var(--muted); line-height: 1.7; margin-bottom: 24px; }
    .error-msg {
      background: rgba(255,107,107,.12); border: 1px solid rgba(255,107,107,.28);
      border-radius: 12px; padding: 13px 16px; margin-bottom: 20px;
      font-size: 14px; color: var(--err);
    }
    .field { margin-bottom: 20px; }
    label { display: block; font-size: 13px; font-weight: 600; margin-bottom: 8px; text-align: left; }
    input[type=text] {
      width: 100%; padding: 14px 16px; border-radius: 12px;
      background: rgba(255,255,255,0.06); border: 1px solid var(--border);
      color: var(--text); font-size: 15px; font-family: monospace; letter-spacing: 2px;
      transition: all .18s;
    }
    input[type=text]:focus {
      outline: none; border-color: var(--err);
      background: rgba(255,255,255,0.08);
    }
    .btn-group { display: flex; gap: 12px; margin-top: 24px; }
    .btn {
      flex: 1; padding: 14px 20px; border-radius: 12px; font-size: 15px; font-weight: 600;
      border: none; cursor: pointer; transition: all .18s;
    }
    .btn-cancel {
      background: rgba(255,255,255,0.08); color: var(--text);
    }
    .btn-cancel:hover { background: rgba(255,255,255,0.14); }
    .btn-danger {
      background: var(--err); color: #fff;
    }
    .btn-danger:hover { background: #ff5252; transform: translateY(-1px); }
    a { color: var(--accent2); text-decoration: none; }
  </style>
</head>
<body>
<div class="page">
  <div class="card">
    <a href="/"><img class="logo" src="{{ url_for('static', filename='logo.svg') }}" alt="Yitiao"></a>
    <h1>最后确认</h1>
    <div class="desc">
      请在下方输入框中输入 <strong style="color:var(--err);">DELETE</strong> 以确认删除
    </div>
    {% if error %}<div class="error-msg">{{ error }}</div>{% endif %}
    <form method="post" action="/account/delete/">
      <input type="hidden" name="_action" value="final">
      <div class="field">
        <label for="cw">确认删除</label>
        <input type="text" id="cw" name="confirm_word" required autofocus autocomplete="off">
      </div>
      <div class="btn-group">
        <a href="/account/" class="btn btn-cancel">取消</a>
        <button type="submit" class="btn btn-danger">确认删除</button>
      </div>
    </form>
  </div>
</div>
</body>
</html>
"""


_CHANGEAVATAR_HTML = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>更换头像 – Yitiao</title>
  <link rel="icon" href="{{ url_for('static', filename='logo.ico') }}" type="image/x-icon">
  <style>
    :root {
      --bg0: #02101f; --bg1: #04142d;
      --text: #eaf1ff; --muted: rgba(234,241,255,0.66);
      --accent: #2d71ff; --accent2: #5aa7ff;
      --card: rgba(2,10,25,0.58); --border: rgba(255,255,255,0.10);
      --err: #ff6b6b;
    }
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    html, body {
      min-height: 100vh; width: 100%;
      font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: radial-gradient(ellipse 110% 80% at 50% -8%, #10315f, var(--bg1) 58%, var(--bg0));
      color: var(--text);
    }
    .page { min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 28px 20px; }
    .card {
      width: min(420px, 100%);
      background: var(--card); border: 1px solid var(--border);
      border-radius: 26px; padding: 44px 38px 38px;
      backdrop-filter: blur(22px);
      box-shadow: 0 36px 90px rgba(0,0,0,0.46), inset 0 1px 0 rgba(255,255,255,0.06);
      animation: rise .42s cubic-bezier(.2,.8,.2,1) both;
      text-align: center;
    }
    @keyframes rise { from { opacity:0; transform:translateY(20px) scale(.98); } to { opacity:1; transform:none; } }
    .logo { display: block; width: 76px; height: 76px; margin: 0 auto 24px; filter: drop-shadow(0 10px 22px rgba(0,0,0,.32)); }
    h1 { font-size: 26px; font-weight: 800; letter-spacing: .02em; margin-bottom: 24px; }
    .error-msg {
      background: rgba(255,107,107,.12); border: 1px solid rgba(255,107,107,.28);
      border-radius: 12px; padding: 13px 16px; margin-bottom: 20px;
      font-size: 14px; color: var(--err); text-align: center; line-height: 1.5;
    }
    .avatar-preview-wrap { display: flex; justify-content: center; margin-bottom: 28px; }
    .avatar-preview-img, .avatar-preview-fallback {
      width: 160px; height: 160px; border-radius: 50%;
      box-shadow: 0 12px 30px rgba(0,0,0,.36);
      border: 3px solid rgba(255,255,255,.14);
    }
    .avatar-preview-img { object-fit: cover; }
    .avatar-preview-fallback {
      background: linear-gradient(135deg, var(--accent2), var(--accent));
      display: flex; align-items: center; justify-content: center;
      font-size: 60px; font-weight: 800; color: #fff;
    }
    .btn-primary {
      display: block; width: 100%; height: 52px; border: 0; border-radius: 16px;
      background: linear-gradient(135deg, var(--accent2), var(--accent));
      color: #fff; font: 700 16px/1 inherit; cursor: pointer;
      letter-spacing: .03em; margin-bottom: 18px;
      box-shadow: 0 10px 30px rgba(45,113,255,.34);
      transition: transform .15s, box-shadow .15s;
    }
    .btn-primary:hover { transform: translateY(-1.5px); box-shadow: 0 14px 36px rgba(45,113,255,.44); }
    .btn-row { display: flex; gap: 10px; }
    .btn-secondary {
      flex: 1; height: 52px; display: flex; align-items: center; justify-content: center;
      border: 1px solid rgba(255,255,255,.18); border-radius: 16px;
      background: rgba(255,255,255,.06); color: var(--muted);
      font: 14px/1 inherit; text-decoration: none; cursor: pointer;
      transition: background .15s, color .15s;
    }
    .btn-secondary:hover { background: rgba(255,255,255,.12); color: var(--text); }
    .btn-primary-half {
      flex: 1; height: 52px; border: 0; border-radius: 16px;
      background: linear-gradient(135deg, var(--accent2), var(--accent));
      color: #fff; font: 700 15px/1 inherit; cursor: pointer;
      box-shadow: 0 10px 30px rgba(45,113,255,.34);
      transition: transform .15s, box-shadow .15s, opacity .15s;
    }
    .btn-primary-half:hover:enabled { transform: translateY(-1.5px); box-shadow: 0 14px 36px rgba(45,113,255,.44); }
    .btn-primary-half:disabled { opacity: .5; cursor: not-allowed; box-shadow: none; transform: none; }
    @media (max-width: 480px) { .card { padding: 36px 22px 28px; } }
  </style>
</head>
<body>
<div class="page">
  <div class="card">
    <a href="/" style="display:contents;">
      <img class="logo" src="{{ url_for('static', filename='logo.svg') }}" alt="Yitiao">
    </a>
    <h1>更换头像</h1>
    {% if error %}<div class="error-msg">{{ error }}</div>{% endif %}

    <div class="avatar-preview-wrap">
      <img id="avatarPreview" class="avatar-preview-img"
           {% if avatar_url %}src="{{ avatar_url }}"{% endif %}
           style="display:{{ 'block' if avatar_url else 'none' }};" alt="头像预览">
      <div id="avatarFallback" class="avatar-preview-fallback"
           style="display:{{ 'none' if avatar_url else 'flex' }};">{{ username[0] | upper }}</div>
    </div>

    <form method="post" enctype="multipart/form-data">
      <input type="file" id="fileInput" name="avatar_file" accept="image/*"
             style="display:none" onchange="handleFileChange(event)">
      <button class="btn-primary" type="button"
              onclick="document.getElementById('fileInput').click()">上传图片</button>
      <div class="btn-row">
        <a href="/account/" class="btn-secondary">返回</a>
        <button class="btn-primary-half" type="submit" id="confirmBtn" disabled>确认</button>
      </div>
    </form>
  </div>
</div>

<script>
  function handleFileChange(e) {
    const file = e.target.files[0];
    if (!file) return;
    if (file.size > 5 * 1024 * 1024) {
      alert('图片大小不能超过 5MB');
      e.target.value = '';
      return;
    }
    const url = URL.createObjectURL(file);
    const img = document.getElementById('avatarPreview');
    const fallback = document.getElementById('avatarFallback');
    img.src = url;
    img.style.display = 'block';
    fallback.style.display = 'none';
    document.getElementById('confirmBtn').disabled = false;
  }
</script>
</body>
</html>
"""


@app.route("/account/changeavatar/", methods=["GET", "POST"])
def change_avatar():
    username = session.get("user")
    if not username:
        return redirect("/login/")

    error = ""
    if request.method == "POST":
        file = request.files.get("avatar_file")
        if not file or not file.filename:
            error = "请先选择图片"
        else:
            data = file.read()
            if len(data) > 5 * 1024 * 1024:
                error = "图片大小不能超过 5MB"
            else:
                try:
                    _save_avatar_from_upload(username, data)
                except Exception:
                    error = "无法识别的图片文件，换一张试试"
                else:
                    return redirect("/account/")

    return render_template_string(
        _CHANGEAVATAR_HTML,
        username=username,
        error=error,
        avatar_url=_user_avatar_url(username),
    )


_CHANGEPROFILE_HTML = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>编辑主页 – Yitiao</title>
  <link rel="icon" href="{{ url_for('static', filename='logo.ico') }}" type="image/x-icon">
  <style>
    :root {
      --bg0: #02101f;
      --bg1: #04142d;
      --text: #eaf1ff;
      --muted: rgba(234,241,255,0.66);
      --accent: #2d71ff;
      --accent2: #5aa7ff;
      --card: rgba(2,10,25,0.58);
      --border: rgba(255,255,255,0.10);
      --ib: rgba(255,255,255,0.07);
      --iborder: rgba(255,255,255,0.16);
      --ifocus: rgba(45,113,255,0.50);
      --err: #ff6b6b;
    }
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    html, body {
      min-height: 100vh; width: 100%;
      font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: radial-gradient(ellipse 110% 80% at 50% -8%, #10315f, var(--bg1) 58%, var(--bg0));
      color: var(--text);
    }
    .page {
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 28px 20px;
      position: relative;
    }
    .logo-link {
      position: absolute;
      left: 18px;
      top: 18px;
      display: inline-flex;
      z-index: 5;
    }
    .logo {
      width: 56px;
      height: 56px;
      filter: drop-shadow(0 10px 22px rgba(0,0,0,.32));
    }
    .card {
      width: min(520px, 100%);
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 26px;
      padding: 42px 36px 36px;
      backdrop-filter: blur(22px);
      box-shadow: 0 36px 90px rgba(0,0,0,0.46), inset 0 1px 0 rgba(255,255,255,0.06);
      animation: rise .42s cubic-bezier(.2,.8,.2,1) both;
    }
    @keyframes rise {
      from { opacity:0; transform:translateY(20px) scale(.98); }
      to   { opacity:1; transform:none; }
    }
    h1 {
      text-align: center;
      font-size: 26px;
      font-weight: 800;
      letter-spacing: .02em;
      margin-bottom: 22px;
    }
    .avatar-wrap {
      display: flex;
      justify-content: center;
      margin-bottom: 22px;
    }
    .avatar-img, .avatar-fallback {
      width: 132px;
      height: 132px;
      border-radius: 50%;
      border: 3px solid rgba(255,255,255,.14);
      box-shadow: 0 12px 30px rgba(0,0,0,.36);
    }
    .avatar-img {
      object-fit: cover;
    }
    .avatar-fallback {
      background: linear-gradient(135deg, var(--accent2), var(--accent));
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 52px;
      font-weight: 800;
      color: #fff;
    }
    .field { margin-bottom: 18px; }
    label {
      display: block;
      font-size: 12.5px;
      font-weight: 600;
      letter-spacing: .06em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 8px;
    }
    input[type="text"], textarea {
      display: block;
      width: 100%;
      padding: 14px 16px;
      background: var(--ib);
      border: 1px solid var(--iborder);
      border-radius: 14px;
      color: var(--text);
      font: 15px/1.5 inherit;
      outline: none;
      transition: border-color .18s, box-shadow .18s;
      resize: vertical;
    }
    input[type="text"] {
      height: 50px;
    }
    textarea {
      min-height: 180px;
    }
    input::placeholder, textarea::placeholder { color: rgba(234,241,255,.28); }
    input:focus, textarea:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 3.5px var(--ifocus);
    }
    .hint {
      margin-top: 8px;
      font-size: 12px;
      color: rgba(234,241,255,.40);
      text-align: right;
    }
    .btn-primary {
      display: block;
      width: 100%;
      height: 52px;
      border: 0;
      border-radius: 16px;
      background: linear-gradient(135deg, var(--accent2), var(--accent));
      color: #fff;
      font: 700 16px/1 inherit;
      cursor: pointer;
      letter-spacing: .03em;
      box-shadow: 0 10px 30px rgba(45,113,255,.34);
      transition: transform .15s, box-shadow .15s;
      margin-top: 4px;
    }
    .btn-primary:hover {
      transform: translateY(-1.5px);
      box-shadow: 0 14px 36px rgba(45,113,255,.44);
    }
    .btn-back {
      display: block;
      width: 100%;
      height: 46px;
      margin-top: 12px;
      border: 1px solid rgba(255,255,255,.14);
      border-radius: 14px;
      background: rgba(255,255,255,.06);
      color: var(--muted);
      font: 14px/1 inherit;
      cursor: pointer;
      text-decoration: none;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: background .15s, color .15s;
    }
    .btn-back:hover {
      background: rgba(255,255,255,.11);
      color: var(--text);
    }
    .error-msg {
      background: rgba(255,107,107,.12);
      border: 1px solid rgba(255,107,107,.28);
      border-radius: 12px;
      padding: 13px 16px;
      margin-bottom: 18px;
      font-size: 14px;
      color: var(--err);
      text-align: center;
      line-height: 1.5;
    }
    @media (max-width: 480px) {
      .card { padding: 36px 22px 28px; }
      .logo-link { left: 14px; top: 14px; }
      .logo { width: 48px; height: 48px; }
    }
      @media (min-width: 1000px) { .card { width: min(1120px, 94vw); max-width: 1120px; } }
  </style>
</head>
<body>
<div class="page">
  <a class="logo-link" href="/">
    <img class="logo" src="{{ url_for('static', filename='logo.svg') }}" alt="Yitiao">
  </a>

  <div class="card">
    <h1>编辑主页</h1>

    <div class="avatar-wrap">
      {% if avatar_url %}
      <img class="avatar-img" src="{{ avatar_url }}" alt="头像">
      {% else %}
      <div class="avatar-fallback">{{ username[0] | upper }}</div>
      {% endif %}
    </div>

    {% if error %}<div class="error-msg" id="errBox">{{ error }}</div>{% endif %}

    <div class="field">
      <label for="nick">昵称</label>
      <input id="nick" type="text" maxlength="50" placeholder="最多 50 个字符" value="{{ nickname }}">
      <div class="hint"><span id="nickCount">0</span>/50</div>
    </div>

    <div class="field">
      <label for="bio">自我介绍</label>
      <textarea id="bio" maxlength="1000" placeholder="最多 1000 个字符">{{ biography }}</textarea>
      <div class="hint"><span id="bioCount">0</span>/1000</div>
    </div>

    {% include "ycards_showcase.html" %}
    <button class="btn-primary" type="button" id="saveBtn">确认</button>
    <a class="btn-back" href="{{ url_for('account') }}">返回</a>
  </div>
</div>

<script>
  const nick = document.getElementById('nick');
  const bio = document.getElementById('bio');
  const saveBtn = document.getElementById('saveBtn');
  const nickCount = document.getElementById('nickCount');
  const bioCount = document.getElementById('bioCount');
  const errBox = document.getElementById('errBox');

  function updateCounts() {
    nickCount.textContent = (nick.value || '').length;
    bioCount.textContent = (bio.value || '').length;
  }

  async function saveProfile() {
    const nickname = (nick.value || '').trim();
    const biography = (bio.value || '').trim();

    if (nickname.length > 50) {
      alert('昵称不能超过 50 个字符');
      return;
    }
    if (biography.length > 1000) {
      alert('介绍不能超过 1000 个字符');
      return;
    }

    saveBtn.disabled = true;
    saveBtn.textContent = '保存中...';

    try {
      const resp = await fetch(location.href, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ nickname, biography })
      });
      const data = await resp.json();

      if (!resp.ok || !data.ok) {
        alert(data.error || '保存失败');
        return;
      }
      location.href = '/account/';
    } catch (e) {
      alert('保存失败');
    } finally {
      saveBtn.disabled = false;
      saveBtn.textContent = '确认';
    }
  }

  nick.addEventListener('input', updateCounts);
  bio.addEventListener('input', updateCounts);
  saveBtn.addEventListener('click', saveProfile);
  updateCounts();
</script>
</body>
</html>
"""


def _ycards_showcase_boot(username, edit=False):
    viewer = _current_user()
    allowed = bool(viewer and viewer.get('role', 'guest') != 'guest' and not viewer.get('deleted_at') and not viewer.get('purged_at'))
    state = app.extensions['ycards'].showcase(username)
    if edit and allowed:
        session.setdefault('yc_csrf', secrets.token_urlsafe(32))
        state['balance_cents'] = points.account_view(username)['balance_cents']
    return dict(viewer=viewer['username'] if viewer else None, edit=bool(edit and allowed),
                csrf=session.get('yc_csrf') if edit and allowed else None, state=state)


@app.route("/account/changeprofile/", methods=["GET", "POST"])
def change_profile():
    username = session.get("user")
    if not username:
        return redirect("/login/")

    profile = _get_profile_row(username)

    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        nickname = str(data.get("nickname", "")).strip()
        biography = str(data.get("biography", "")).strip()

        if len(nickname) > 50:
            return {"ok": False, "error": "昵称不能超过 50 个字符"}, 400
        if len(biography) > 1000:
            return {"ok": False, "error": "介绍不能超过 1000 个字符"}, 400

        _upsert_profile_row(username, nickname, biography)
        return {"ok": True}

    return render_template_string(
        _CHANGEPROFILE_HTML,
        username=username,
        nickname=profile.get("nickname", ""),
        biography=profile.get("biography", ""),
        avatar_url=_user_avatar_url(username),
        error="",
        showcase_boot=_ycards_showcase_boot(username, edit=True),
    )



_PROFILE_HTML = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>@{{ username }} – Yitiao</title>
  <link rel="icon" href="{{ url_for('static', filename='logo.ico') }}" type="image/x-icon">
  <style>
    :root {
      --bg0: #02101f;
      --bg1: #04142d;
      --text: #eaf1ff;
      --muted: rgba(234,241,255,0.66);
      --muted2: rgba(234,241,255,0.42);
      --accent: #2d71ff;
      --accent2: #5aa7ff;
      --card: rgba(2,10,25,0.58);
      --border: rgba(255,255,255,0.10);
    }
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    html, body {
      min-height: 100vh;
      width: 100%;
      font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: radial-gradient(ellipse 110% 80% at 50% -8%, #10315f, var(--bg1) 58%, var(--bg0));
      color: var(--text);
    }
    .page {
      min-height: 100vh;
      position: relative;
      overflow: hidden;
    }
    .logo-link {
      position: fixed;
      left: 18px;
      top: 18px;
      z-index: 10;
      display: inline-flex;
    }
    .logo {
      width: 56px;
      height: 56px;
      filter: drop-shadow(0 10px 22px rgba(0,0,0,.32));
    }
    .hero {
      position: relative;
      min-height: 52vh;
      display: flex;
      align-items: flex-end;
      justify-content: center;
      padding: 90px 20px 34px;
      overflow: hidden;
    }
    .hero-bg {
      position: absolute;
      inset: -30px;
      background:
        linear-gradient(180deg, rgba(1,8,22,.24), rgba(1,8,22,.72)),
        {{ bg_css }};
      background-size: cover;
      background-position: center;
      filter: blur(24px) saturate(1.05);
      transform: scale(1.12);
      z-index: -2;
    }
    .hero-overlay {
      position: absolute;
      inset: 0;
      background: linear-gradient(180deg, rgba(1,8,22,.16), rgba(1,8,22,.68));
      z-index: -1;
    }
    .card {
      width: min(1120px, 100%);
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 28px;
      padding: 34px 28px 30px;
      backdrop-filter: blur(20px);
      box-shadow: 0 36px 90px rgba(0,0,0,.42);
      text-align: center;
      margin-top: 70px;
    }
    .avatar {
      width: 126px;
      height: 126px;
      border-radius: 50%;
      object-fit: cover;
      border: 3px solid rgba(255,255,255,.16);
      box-shadow: 0 12px 30px rgba(0,0,0,.36);
      margin-top: -92px;
      background: rgba(255,255,255,.06);
    }
    .avatar-fallback {
      width: 126px;
      height: 126px;
      border-radius: 50%;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      margin-top: -92px;
      border: 3px solid rgba(255,255,255,.16);
      box-shadow: 0 12px 30px rgba(0,0,0,.36);
      background: linear-gradient(135deg, var(--accent2), var(--accent));
      font-size: 54px;
      font-weight: 800;
      color: #fff;
    }
    h1 {
      margin-top: 18px;
      font-size: 30px;
      font-weight: 800;
      letter-spacing: .01em;
    }
    .handle {
      margin-top: 6px;
      font-size: 14px;
      color: var(--muted2);
    }
    .bio {
      margin: 26px auto 0;
      width: min(620px, 100%);
      font-size: 16px;
      line-height: 1.85;
      color: var(--text);
      white-space: pre-wrap;
      text-align: left;
    }
    .bio-empty {
      color: var(--muted2);
    }
    @media (max-width: 480px) {
      .logo-link { left: 14px; top: 14px; }
      .logo { width: 48px; height: 48px; }
      .card { padding: 30px 18px 24px; }
      h1 { font-size: 26px; }
      .bio { font-size: 15px; }
    }
  </style>
</head>
<body>
<div class="page">
  <a class="logo-link" href="/">
    <img class="logo" src="{{ url_for('static', filename='logo.svg') }}" alt="Yitiao">
  </a>

  <section class="hero">
    <div class="hero-bg"></div>
    <div class="hero-overlay"></div>

    <div class="card">
      <div style="display:flex;align-items:center;gap:20px;text-align:left">
      {% if avatar_url %}<img class="avatar" style="margin:0;width:90px;height:90px" src="{{ avatar_url }}" alt="头像">
      {% else %}<div class="avatar-fallback" style="margin:0;width:90px;height:90px;font-size:36px">{{ username[0] | upper }}</div>{% endif %}
      <div>{% if social.nickname %}<h1 style="margin:0">{{ social.nickname }}</h1>{% endif %}<div class="handle" style="font-size:20px">@{{ username }}</div></div></div>
      {% include 'account_social_summary.html' %}
      <div class="bio {% if not biography %}bio-empty{% endif %}">
        {{ biography if biography else "这个人还没有写自我介绍。" }}
      </div>
      {% include "ycards_showcase.html" %}
    </div>
  </section>
</div>
</body>
</html>
"""


@app.route("/@<username>")
@app.route("/@<username>/")
def public_profile(username):
    row = get_user(username)
    if not row:
        return ("Not Found", 404)

    # 已清理的账号：用户名还占位，但主页当作不存在
    if row.get("purged_at"):
        return ("用户不存在", 404)

    profile = _get_profile_row(username)
    display_name = (profile.get("nickname") or username).strip()

    return render_template_string(
        _PROFILE_HTML,
        social=account_social_view(username, session.get("user")),
        username=username,
        showcase_boot=_ycards_showcase_boot(username),
        display_name=display_name,
        biography=profile.get("biography", ""),
        avatar_url=_user_avatar_url(username),
        bg_css=f"url('{_user_avatar_url(username)}')" if _user_avatar_url(username) else "linear-gradient(180deg, #0b2f63, #04142d)",
    )

@app.route("/")
def index():
    avatar_url = _user_avatar_url(session.get("user"))
    return render_template_string(  
        """  
        <!doctype html>  
        <html lang="zh-CN">  
        <head>  
          <meta charset="utf-8">  
          <meta name="viewport" content="width=device-width, initial-scale=1">  
          <title>Yitiao</title>  
          <link rel="icon" href="{{ url_for('static', filename='logo.ico') }}" type="image/x-icon">  
          <link rel="shortcut icon" href="{{ url_for('static', filename='logo.ico') }}" type="image/x-icon">  
          <meta name="description" content="探索 Yitiao 的游戏、量化分析、闪卡、加密聊天与社区">  
          <link rel="stylesheet" href="{{ url_for('static', filename='home.css') }}">
          <link rel="preload" as="image" href="{{ url_for('static', filename='home-games.webp') }}" media="(orientation: landscape)">
          <link rel="preload" as="image" href="{{ url_for('static', filename='home-games.webp') }}" media="(orientation: portrait)">  
        </head>  
        <body>  
          <div class="app-shell">  
            <div class="intro-overlay" id="introOverlay"></div>  
  
            <header class="topbar">  
              <div class="topbar-left">  
                <button class="icon-btn menu-btn" id="menuBtn" aria-label="打开导航" aria-expanded="false" aria-controls="menuPanel" title="导航">  
                  <svg width="20" height="20" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">  
                    <path d="M3 5H17M3 10H17M3 15H17" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>  
                  </svg>  
                </button>  
              </div>  
              <div class="topbar-right">
                {% if session.get('user') %}
                <a class="user-chip" href="/account/">
                  {% if avatar_url %}
                  <img class="avatar-sm avatar-sm-img" src="{{ avatar_url }}" alt="">
                  {% else %}
                  <span class="avatar-sm">{{ (session.get('user') or '?')[0] | upper }}</span>
                  {% endif %}
                  <span class="uname">{{ session.get('user') }}</span>
                </a>
                {% else %}
                <a class="login-btn" href="/login/">Log in</a>
                {% endif %}
              </div>
            </header>  
  
            <div class="header-logo-wrap" id="logoWrap" aria-hidden="true">  
              <img class="logo-img" src="{{ url_for('static', filename='logo.svg') }}" alt="Yitiao">  
            </div>  
  
            <aside class="menu-panel" id="menuPanel" aria-label="站点导航" aria-hidden="true">
              <div class="menu-title">Explore Yitiao</div>
              <button class="menu-item" type="button" data-target="0"><span><strong>Games</strong><small>轻松玩一会儿</small></span><span class="arrow">→</span></button>
              <button class="menu-item" type="button" data-target="1"><span><strong>YitiaoAiGo</strong><small>AI 驱动的量化交易平台</small></span><span class="arrow">→</span></button>
              <button class="menu-item" type="button" data-target="2"><span><strong>YCards</strong><small>创作、生产与收藏闪卡</small></span><span class="arrow">→</span></button>
              <button class="menu-item" type="button" data-target="3"><span><strong>YiChat</strong><small>端到端加密聊天 · 立即开始</small></span><span class="arrow">→</span></button>
              <button class="menu-item" type="button" data-target="4"><span><strong>YiHub</strong><small>分享观点，发现讨论</small></span><span class="arrow">→</span></button>
            </aside>

            <nav class="section-indicator" aria-label="首页分区">
              <button class="dot active" type="button" aria-label="Games" aria-current="true"></button>
              <button class="dot" type="button" aria-label="YitiaoAiGo"></button>
              <button class="dot" type="button" aria-label="YCards"></button>
              <button class="dot" type="button" aria-label="YiChat"></button>
              <button class="dot" type="button" aria-label="YiHub"></button>
            </nav>

            <main class="content" id="homeContent">
              <section class="panel active" style="--bg-wide:url('{{ url_for('static', filename='home-games.webp') }}');--bg-mobile:url('{{ url_for('static', filename='home-games.webp') }}')">
                <div class="panel-inner">
                  <p class="eyebrow">PLAY · EXPLORE</p>
                  <h1 class="big-title">Games</h1>
                  <p class="subtitle">几款轻量小游戏，随时来一局，给日常添一点乐趣。</p>
                  <a class="panel-link" href="/games/">开始游戏 <span class="arrow" aria-hidden="true">↗</span></a>
                </div>
              </section>
              <section class="panel" style="--bg-wide:url('{{ url_for('static', filename='app1w.jpg') }}');--bg-mobile:url('{{ url_for('static', filename='home-aigo-mobile.webp') }}')">
                <div class="panel-inner">
                  <p class="eyebrow">DATA · INTELLIGENCE</p>
                  <h2 class="big-title">YitiaoAiGo</h2>
                  <p class="subtitle">用 AI 与数据洞察市场，让量化分析和交易决策更清晰。</p>
                  <a class="panel-link" href="{{ '/frontend/' if session.get('user') else '/login/?next=/frontend/' }}">探索 YitiaoAiGo <span class="arrow" aria-hidden="true">↗</span></a>
                </div>
              </section>
              <section class="panel" style="--bg-wide:url('/static/home-ycards.svg');--bg-mobile:url('/static/home-ycards.svg')">
                <div class="panel-inner">
                  <p class="eyebrow">CREATE · COLLECT</p>
                  <h2 class="big-title">YCards</h2>
                  <p class="subtitle">让创意映出独特光影。设计闪卡、投入生产，用 point 收藏心动之作。</p>
                  <a class="panel-link" href="/ycards/">探索 YCards <span class="arrow" aria-hidden="true">↗</span></a>
                </div>
              </section>
              <section class="panel" style="--bg-wide:url('{{ url_for('static', filename='home-yichat.svg') }}');--bg-mobile:url('{{ url_for('static', filename='home-yichat.svg') }}')">
                <div class="panel-inner">
                  <p class="eyebrow">PRIVATE · CONNECT</p>
                  <h2 class="big-title">YiChat</h2>
                  <p class="subtitle">私聊端到端加密，与朋友和群组自在交流。支持网页版与 Android App。</p>
                  <a class="panel-link" href="https://chat.yitiao.org/">打开 YiChat <span class="arrow" aria-hidden="true">↗</span></a>
                </div>
              </section>
              <section class="panel panel--hub" style="--bg-wide:url('{{ url_for('static', filename='home-yihub-wide.webp') }}');--bg-mobile:url('{{ url_for('static', filename='home-yihub-mobile.webp') }}')">
                <div class="panel-inner">
                  <p class="eyebrow">SHARE · CONNECT</p>
                  <h2 class="big-title">YiHub</h2>
                  <p class="subtitle">一个分享帖子与观点的社区，在这里发现新鲜想法，加入感兴趣的讨论。</p>
                  <a class="panel-link coming-soon" href="#">即将上线 <span class="arrow" aria-hidden="true">↗</span></a>
                </div>
                <div class="site-footer">Copyright © 2026 Yitiao. All rights reserved.</div>
              </section>
            </main>
          </div>

          <script src="{{ url_for('static', filename='home.js') }}" defer></script>  
        </body>  
        </html>  
        """,
        avatar_url=avatar_url,
    )  
  
  
# ── 作为 OpenID Provider 对外提供单点登录（LibreChat 等）─────────────────────
_OIDC_DENY_BODY = """
<div class="page">
  <div class="card" style="text-align:center;">
    <a href="/" style="display:contents;">
      <img class="logo" src="{{ url_for('static', filename='logo.svg') }}" alt="Yitiao">
    </a>
    <h1>{{ title }}</h1>
    <div class="error-msg">{{ detail }}</div>
    <div class="step-desc" style="margin-top:14px;">
      如需切换账号，请先退出当前登录后重试。
    </div>
    <div class="btn-row" style="margin-top:18px;">
      <a class="btn-secondary" href="/">返回首页</a>
      <form method="post" action="/logout/" style="flex:1;display:flex;">
        <button class="btn-primary-half" type="submit" style="flex:1;">退出登录</button>
      </form>
    </div>
  </div>
</div>
"""

_OIDC_DENY_HTML = _shell("授权失败", _OIDC_DENY_BODY)


def _oidc_deny_page(title: str, detail: str) -> tuple[str, int]:
    return render_template_string(_OIDC_DENY_HTML, title=title, detail=detail), 403


def _oidc_login_redirect(next_url: str):
    """未登录时把完整的 authorize URL 塞进 ?next=，登录完原路返回。"""
    return redirect(url_for("login", next=_safe_next(next_url, "/")))


def _oidc_claims_for(user: dict) -> dict:
    """把 account.db 的用户映射成 OIDC 标准 claims。"""
    username = str(user.get("username") or "")
    role = str(user.get("role") or "guest")
    nickname = str(user.get("nickname") or "").strip()
    email = str(user.get("google_email") or "").strip()

    picture = None
    avatar = _user_avatar_url(username)
    if avatar:
        picture = f"https://yitiao.org{avatar}"

    return {
        # Existing RPs keep their legacy sub; YiChat uses the immutable user_id in the provider.
        "sub": username,
        "user_id": account_social.get_identity(ACCOUNT_DB, username=username)["uid"],
        "preferred_username": username,
        "nickname": nickname or username,
        "name": nickname or username,
        "picture": picture,
        # LibreChat 的 user schema 里 email 是 required 且要过 /\S+@\S+\.\S+/，
        # 空值会在建号时抛 ValidationError，所以没绑 Google 的account用站内地址占位。
        "email": email or f"{username}@yitiao.org",
        # 占位地址在我自己的域名下、身份也由本站密码+TOTP 验过，报 true；
        # 注意这类地址不一定真能收信，别拿它发邮件。
        "email_verified": True,
        "role": role,
        "roles": [role],
    }


oidc_provider.init_oidc(
    app,
    oidc_provider.OIDCDeps(
        issuer="https://yitiao.org",
        db_conn=_conn,
        get_user=get_user,
        current_username=lambda: session.get("user") or None,
        claims_for=_oidc_claims_for,
        login_redirect=_oidc_login_redirect,
        deny_page=_oidc_deny_page,
        key_path=BASE_DIR / ".oidc_key.pem",
    ),
)


# Administrator panel keeps its authenticator and jobs separate from account login.
import admin_panel
admin_panel.init_admin_panel(app, {
    "base": BASE_DIR,
    "get_user": get_user,
    "qr": _make_qr_b64,
    "validate_username": _validate_username,
    "validate_password": _validate_password,
    "validate_email": _validate_email,
    "create_user": create_user,
    "revoke": revoke_linked_sessions,
    "mark_delete": mark_for_deletion,
    "purge": purge_account,
    "update_user": _update_user,
})


# YCards shares the existing account identity and userdata.db points ledger.
import ycards
ycards.init_app(app, _current_user, _user_avatar_url, profile=_get_profile_row)


def yidt_account_data(user):
    import yidt_core as y
    session.setdefault('_yidt_csrf',secrets.token_urlsafe(32))
    with y.connect() as c:
        b=y.binding(c,user,'daily');r=c.execute('SELECT * FROM yidt_daily WHERE user=?',(user,)).fetchone()
        checked=bool(r and r['last_day']==y.utcday())
        yesterday=(datetime.now(timezone.utc).date()-timedelta(days=1)).isoformat()
        streak=r['streak'] if r and r['last_day'] in (y.utcday(),yesterday) else 0
        tx=c.execute('SELECT credit FROM yidt_transactions WHERE hash=?',(r['tx'],)).fetchone() if checked and r['tx'] else None
        amount=int(tx['credit']) if tx else (300 if (streak+1)%5==0 else 100)*y.SCALE-y.GAS
        return dict(total=y.dashboard(c,user)['total'],bound=bool(b),checked=checked,streak=streak,amount=y.fmt(amount),tx=r['tx'] if checked else None)

@app.post('/account/yidt/daily/')
def yidt_claim():
    from flask import jsonify
    import yidt_core as y
    if not session.get('user'):return jsonify(error='请先登录'),401
    data=request.get_json(silent=True) or {}
    if not session.get('_yidt_csrf') or not secrets.compare_digest(str(data.get('csrf','')),session['_yidt_csrf']):return jsonify(error='页面已过期，请刷新'),403
    with y.connect() as c:
        c.execute('BEGIN IMMEDIATE');tx=y.daily(c,session['user'])
    return jsonify(ok=True,tx=tx['hash'],credit=tx['credit'])

@app.after_request
def yidt_account_headers(response):
    if request.path.startswith('/account/yidt/') or (request.path.startswith('/verify/') and request.args.get('flow')):
        response.headers['Cache-Control']='no-store'
        response.headers['Referrer-Policy']='same-origin'
        response.headers['X-Frame-Options']='DENY'
    return response


# Payment PIN is an account setting. Flow state lives in the database so another
# verification tab or an old cookie response cannot replace its destination.
def _yidt_flow(token,states=('pending','verified')):
    import yidt_core as y
    if not session.get('user') or not session.get('yidt_sid'):return None
    with y.connect() as c:
        row=c.execute('SELECT * FROM yidt_account_flows WHERE token=?',(token,)).fetchone()
        if row and row['user']==session['user'] and row['sid']==session['yidt_sid'] and row['expires']>time.time() and row['state'] in states:
            return dict(row)
    return None


def _yidt_verified(token):
    import yidt_core as y
    with y.connect() as c:
        c.execute("UPDATE yidt_account_flows SET state='verified',expires=? WHERE token=? AND user=? AND sid=? AND state='pending' AND expires>?",
                  (time.time()+300,token,session['user'],session['yidt_sid'],time.time()))


_YIDT_PIN_BODY = """
<div class="page"><div class="card">
<a href="https://yidt.yitiao.org/pin/" class="btn-back">← YIDT</a><h1>验证账户身份</h1>
{% if failure %}<div class="error-msg">{{failure}}</div>{% endif %}
{% if expired %}<p>验证已过期或已使用，请重新验证。</p><a class="btn-primary" href="{{restart_url}}">重新验证账户</a>
{% else %}
<p class="step-desc">请先验证账户身份，再设置或重置支付密码。</p>
<form method="post"><input type="hidden" name="csrf" value="{{csrf}}">
{% if password %}<div class="field"><label for="account_password">Yitiao 账户密码</label><input id="account_password" type="password" name="password" autocomplete="current-password"></div><button class="btn-primary">验证密码</button>{% endif %}
{% if google %}<button class="btn-google" name="method" value="google">使用 Google 验证</button>{% endif %}</form>
{% endif %}</div></div>
"""
_YIDT_PIN_HTML = _shell('验证账户身份',_YIDT_PIN_BODY)


def _yidt_pin_page(**kwargs):
    response=app.make_response(render_template_string(_YIDT_PIN_HTML,csrf=session.get('_yidt_csrf',''),**kwargs))
    response.headers['Cache-Control']='no-store'
    response.headers['Referrer-Policy']='same-origin'
    response.headers['X-Frame-Options']='DENY'
    return response


@app.route('/account/yidt/verify/', methods=['GET','POST'])
def yidt_reauth():
    import yidt_core as y
    me=_current_user()
    if not me:return redirect(url_for('login',next=request.full_path))
    session.setdefault('_yidt_csrf',secrets.token_urlsafe(32))
    token=request.args.get('flow','')
    if not token:
        token=secrets.token_urlsafe(32)
        methods=twofa_methods(me,'sensitive')
        target=y.ORIGIN+'/pin/complete'
        with y.connect() as c:
            c.execute('DELETE FROM yidt_account_flows WHERE expires<?',(time.time()-86400,))
            c.execute('INSERT INTO yidt_account_flows VALUES(?,?,?,?,?,?,?)',
                      (token,me['username'],session['yidt_sid'],time.time()+600,methods[0] if methods else None,target,'pending'))
        return redirect(url_for('yidt_reauth',flow=token))
    flow=_yidt_flow(token)
    if not flow:return _yidt_pin_page(expired=True,restart_url=url_for('yidt_reauth',**({'return':'yidt'} if request.args.get('return')=='yidt' else {})))
    if flow['state']=='verified':return redirect(url_for('yidt_pin',flow=token))
    if _needs_twofa_for_sensitive(me):return redirect(url_for('verify',flow=token))
    failure=request.args.get('error','')
    if request.method=='POST':
        if not secrets.compare_digest(request.form.get('csrf',''),session['_yidt_csrf']):return '请求无效，请刷新后重试',403
        with y.connect() as c:
            r=c.execute('SELECT value FROM yidt_meta WHERE key=?',('reauth:'+me['username'],)).fetchone()
            failures=json.loads(r[0]) if r else [0,0]
            if failures[1]>time.time():return '尝试次数过多，请 15 分钟后重试',429
        if request.form.get('method')=='google' and me.get('google_sub'):
            return _google_authorize_redirect('yidt',url_for('yidt_reauth',flow=token))
        if me.get('password_hash') and check_password_hash(me['password_hash'],request.form.get('password','')):
            with y.connect() as c:c.execute('DELETE FROM yidt_meta WHERE key=?',('reauth:'+me['username'],))
            _yidt_verified(token)
            return redirect(url_for('yidt_pin',flow=token))
        failure='账户密码不正确'
        with y.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            r=c.execute('SELECT value FROM yidt_meta WHERE key=?',('reauth:'+me['username'],)).fetchone()
            failures=json.loads(r[0]) if r else [0,0];failures[0]+=1
            if failures[0]>=5:failures[1]=time.time()+900
            c.execute('INSERT INTO yidt_meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',('reauth:'+me['username'],json.dumps(failures)))
    return _yidt_pin_page(failure=failure,password=bool(me.get('password_hash')),google=bool(me.get('google_sub')))


@app.get('/account/yidt/pin/')
def yidt_pin():
    # Compatibility callback: settings are displayed and submitted only on YIDT.
    import yidt_core as y
    from urllib.parse import urlencode
    token=request.args.get('flow','')
    return redirect(y.ORIGIN+'/pin/'+('?' + urlencode({'flow':token}) if token else ''))


# Main-site passkeys: this store never reads YIDT's independent credentials.
import passkeys as pk
main_passkeys=pk.Store(_conn,'yitiao.org','Yitiao',['https://yitiao.org','https://www.yitiao.org'])
with main_passkeys.db():pass


def _passkey_session():
    session.setdefault('_pk_sid',secrets.token_urlsafe(32))
    session.setdefault('_pk_csrf',secrets.token_urlsafe(32))
    return 'account:'+session.get('yidt_sid',session['_pk_sid'])


def _passkey_json():
    from flask import abort
    if request.content_length and request.content_length>65536:abort(413)
    if request.headers.get('Origin') not in main_passkeys.origins:abort(403)
    if not request.is_json:abort(415)
    if not session.get('_pk_csrf') or not secrets.compare_digest(request.headers.get('X-Passkey-CSRF',''),session['_pk_csrf']):abort(403)
    data=request.get_json()
    if not isinstance(data,dict):abort(400)
    return data


@app.errorhandler(pk.Error)
def passkey_error(exc):
    from flask import jsonify
    return jsonify(error=str(exc)),400


@app.after_request
def passkey_headers(response):
    if request.path.startswith(('/auth/passkeys/','/account/passkeys/')) or request.path in ('/login/','/verify/'):
        response.headers['Cache-Control']='no-store'
        response.headers['Referrer-Policy']='same-origin'
        response.headers['X-Frame-Options']='DENY'
    return response


@app.get('/account/passkeys/')
def manage_passkeys():
    user=_current_user()
    if not user:return redirect(url_for('login',next=request.path))
    if not user.get('email') and not user.get('totp_secret'):
        return redirect(url_for('account',err='请先绑定邮箱验证或身份验证器，以继续设置通行密钥。不能使用其他通行密钥完成此验证。'))
    guard=require_twofa(user,'manage_passkeys',request.path,exclude_passkeys=True)
    if guard is not None:return guard
    _passkey_session()
    return render_template_string(_shell('通行密钥','<div class="page"><div class="card">{% include "passkeys_manage.html" %}</div></div>'),
        passkeys=main_passkeys.items(user['username']),pk_api='/account/passkeys',pk_csrf=session['_pk_csrf'],pk_wallet=False,pk_ready=True)


def _passkey_management_auth():
    user=_current_user()
    if not user or not (user.get('email') or user.get('totp_secret')) or not _has_ticket('manage_passkeys'):
        raise pk.Error('请先通过邮箱或身份验证器验证身份，再管理通行密钥')
    return user['username']


@app.post('/account/passkeys/register/options')
def main_passkey_registration_options():
    _passkey_json();user=_passkey_management_auth()
    return main_passkeys.registration_options(user,_passkey_session())


@app.post('/account/passkeys/register/finish')
def main_passkey_registration_finish():
    data=_passkey_json();user=_passkey_management_auth()
    with main_passkeys.db() as c:
        c.execute('BEGIN IMMEDIATE')
        try:main_passkeys.register(c,user,_passkey_session(),data)
        except pk.Error:c.commit();raise
    consume_ticket('manage_passkeys')
    return {'ok':True,'next':'/account/?ok=通行密钥已添加'}


@app.post('/account/passkeys/manage')
def main_passkey_management():
    data=_passkey_json();user=_passkey_management_auth()
    with main_passkeys.db() as c:
        c.execute('BEGIN IMMEDIATE');main_passkeys.manage(c,user,data)
    consume_ticket('manage_passkeys')
    return {'ok':True,'next':'/account/?ok=通行密钥已更新'}


@app.post('/auth/passkeys/login/options')
def passkey_login_options():
    data=_passkey_json()
    if session.get('user'):raise pk.Error('当前账户已登录')
    context={'next':_safe_next(data.get('next'),'/'),'remember':bool(data.get('remember'))}
    result=main_passkeys.authentication_options(None,_passkey_session(),'login',context)
    result['context']=context
    return result


@app.post('/auth/passkeys/login/finish')
def passkey_login_finish():
    data=_passkey_json()
    if session.get('user'):raise pk.Error('当前账户已登录')
    context=data.get('context') or {}
    with main_passkeys.db() as c:
        c.execute('BEGIN IMMEDIATE')
        try:user=main_passkeys.authenticate(c,None,_passkey_session(),'login',context,data)
        except pk.Error:c.commit();raise
    account=get_user(user)
    if not account or account.get('purged_at'):raise pk.Error('账号已删除，无法登录')
    if account.get('deleted_at'):
        restore_account(user);session['_account_restored']=True
    # UV was required by the authenticator; no additional login second-factor loop.
    _finish_login(user,remember=bool(context.get('remember')));_clear_tmp_session()
    return {'ok':True,'next':_safe_next(context.get('next'),'/')}


def _passkey_verification_state():
    st=_verify_state()
    if not st or st.get('no_passkey') or st.get('purpose')=='manage_passkeys' or st.get('method')!='passkey':raise pk.Error('当前操作不能使用通行密钥验证')
    user=get_user(st['user'])
    if not user or is_deleted(user) or not main_passkeys.has(st['user']):raise pk.Error('当前账户没有可用通行密钥')
    return st


@app.post('/auth/passkeys/verify/options')
def passkey_verify_options():
    _passkey_json();st=_passkey_verification_state()
    return main_passkeys.authentication_options(st['user'],_passkey_session(),'verify',st)


@app.post('/auth/passkeys/verify/finish')
def passkey_verify_finish():
    data=_passkey_json();st=_passkey_verification_state()
    with main_passkeys.db() as c:
        c.execute('BEGIN IMMEDIATE')
        try:main_passkeys.authenticate(c,st['user'],_passkey_session(),'verify',st,data)
        except pk.Error:c.commit();raise
    if st.get('flow'):
        _yidt_verified(st['flow']);return {'ok':True,'next':url_for('yidt_pin',flow=st['flow'])}
    session.pop('_v',None)
    if st.get('kind')=='login':
        response=_complete_login_from_verify(st)
        target=response.headers.pop('Location','/')
        response.status_code=200;response.set_data(app.json.dumps({'ok':True,'next':target}));response.content_type='application/json'
        return response
    _grant_ticket(st['purpose'])
    return {'ok':True,'next':_safe_next(st.get('next'),'/account/')}

import yidt_bridge
yidt_bridge.install(app)
import account_devices
import yidt_core as _devices_yidt
account_devices.install(app,_devices_yidt.connect)


import account_social
account_social.install(app, {'db':ACCOUNT_DB, 'current_user':_current_user, 'shell':_shell})
def account_social_view(username, viewer=None):
    with account_social.database(ACCOUNT_DB) as c:
        target=account_social.identity(c,username=username)['uid']
        who=account_social.identity(c,username=viewer)['uid'] if viewer else None
        return account_social.view(c,target,who)

if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "5001")),
        debug=False,
        use_reloader=False,
    )
