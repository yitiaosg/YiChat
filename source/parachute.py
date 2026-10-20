"""Parachute: server-timed flight, atomic points settlement."""
from __future__ import annotations

import json
import re
import secrets
import sqlite3
import time
from datetime import datetime
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, redirect, render_template, request, session, url_for

import points
import yidt_core as y
import tools

BASE_DIR = Path(__file__).resolve().parent
ACCOUNT_DB = BASE_DIR / "account.db"
PREFIX = "/games/parachute"
CLIMB_ACCELERATION = 5.0
FEE_CENTS = 50
HISTORY_LIMIT = 100
PIN_LIMIT = 99
PERSONAL_RANK_LIMIT = 50
GLOBAL_USER_RANK_LIMIT = 10
SHANGHAI = ZoneInfo("Asia/Shanghai")
app = Flask(__name__)
app.secret_key = tools.get_secret_key()


def _now():
    return time.time()


def avatar_url(user):
    p = BASE_DIR / "static" / "avatar" / f"{user}.jpg"
    return url_for("static", filename=f"avatar/{user}.jpg") + f"?v={int(p.stat().st_mtime)}" if p.exists() else None


def init(conn):
    points.init(conn)
    conn.execute("""CREATE TABLE IF NOT EXISTS parachute_rounds (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT NOT NULL,
        stake_cents INTEGER NOT NULL, crash_height REAL NOT NULL,
        started_at REAL NOT NULL, ended_at REAL, exit_height REAL,
        payout_cents INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL CHECK(status IN ('active','jumped','crashed'))
    )""")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_parachute_active ON parachute_rounds(user) WHERE status='active'")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_parachute_user ON parachute_rounds(user,id DESC)")
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(parachute_rounds)")}
    if "fee_cents" not in columns:
        conn.execute("ALTER TABLE parachute_rounds ADD COLUMN fee_cents INTEGER NOT NULL DEFAULT 0")
    if "debit_cents" not in columns:
        conn.execute("ALTER TABLE parachute_rounds ADD COLUMN debit_cents INTEGER")
    if "pinned" not in columns:
        conn.execute("ALTER TABLE parachute_rounds ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")
    if "share_token" not in columns:
        conn.execute("ALTER TABLE parachute_rounds ADD COLUMN share_token TEXT")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_parachute_share ON parachute_rounds(share_token)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_parachute_user_recent ON parachute_rounds(user, id DESC)")

    for field in ('stake_units','debit_units','payout_units','paid_tx','redeem_tx','wallet_address'):
        if field not in columns:conn.execute('ALTER TABLE parachute_rounds ADD COLUMN '+field+' TEXT')
    conn.execute('CREATE TABLE IF NOT EXISTS yidt_parachute_pending(user TEXT PRIMARY KEY,stake_units TEXT,debit_units TEXT,address TEXT,tx TEXT,created REAL)')


def score_cents(row):
    return row["payout_cents"] - (row["debit_cents"] if row["debit_cents"] is not None else row["stake_cents"])


def page_header(user):
    with y.connect() as c:
        b=y.binding(c,user,'parachute') if user else None
        balance=y.fmt(y.wallet(c,b['address'])['balance']) if b else '绑定 YIDT 钱包'
    return dict(username=user,avatar_url=avatar_url(user) if user else None,balance=balance)


def _retention(conn, user):
    rows = conn.execute("""SELECT id, pinned, stake_cents, debit_cents, payout_cents
        FROM parachute_rounds WHERE user=? AND status!='active' ORDER BY id DESC""", (user,)).fetchall()
    top = sorted(rows, key=lambda row: (score_cents(row), row["id"]), reverse=True)[:PERSONAL_RANK_LIMIT]
    top_ids = {row["id"] for row in top}
    pinned_ids = {row["id"] for row in rows if row["pinned"]}
    recent_ids = set(pinned_ids)
    for row in rows:
        if len(recent_ids) >= HISTORY_LIMIT:
            break
        recent_ids.add(row["id"])
    return rows, top_ids, recent_ids


def prune_user(conn, user):
    rows, top_ids, recent_ids = _retention(conn, user)
    keep = top_ids | recent_ids
    stale = [(row["id"], user) for row in rows if row["id"] not in keep]
    if stale:
        conn.executemany("DELETE FROM parachute_rounds WHERE id=? AND user=? AND status!='active'", stale)


def _nickname_map(users):
    if not users or not ACCOUNT_DB.exists():
        return {}
    names = list(dict.fromkeys(users))
    marks = ",".join("?" for _ in names)
    with sqlite3.connect(ACCOUNT_DB) as conn:
        rows = conn.execute(f"SELECT username,nickname FROM users WHERE username IN ({marks})", names).fetchall()
    return {user: nickname or user for user, nickname in rows}


def _record_dict(row, nicknames=None):
    user = row["user"]
    record = {
        "id": row["id"], "user": user,
        "paid_tx": row["paid_tx"], "redeem_tx": row["redeem_tx"],
        "nickname": (nicknames or {}).get(user, user),
        "avatar_url": avatar_url(user),
        "status": row["status"], "height": row["exit_height"],
        "crash_height": row["crash_height"],
        "stake_cents": row["stake_cents"], "fee_cents": row["fee_cents"],
        "payout_cents": row["payout_cents"], "score_cents": score_cents(row),
        "pinned": bool(row["pinned"]),
        "share_url": f"{PREFIX}/share/{row['share_token']}/" if row["share_token"] else None,
        "played_at": datetime.fromtimestamp(row["started_at"], SHANGHAI).strftime("%Y-%m-%d %H:%M"),
    }
    return record


def latest(conn, user):
    return conn.execute("SELECT * FROM parachute_rounds WHERE user=? ORDER BY id DESC LIMIT 1", (user,)).fetchone()


def current_height(row, now):
    elapsed = max(0.0, now - row["started_at"])
    return 0.5 * CLIMB_ACCELERATION * elapsed * elapsed


def finish(conn, row, status, height, payout, now):
    conn.execute("UPDATE parachute_rounds SET status=?,ended_at=?,exit_height=?,payout_cents=? WHERE id=? AND status='active'",
                 (status, now, height, payout, row["id"]))
    if payout:
        tx=y.service_credit(conn,row['user'],'parachute',payout*y.CENT,'跳伞返还',str(row['id']),address=row['wallet_address'])
        conn.execute('UPDATE parachute_rounds SET payout_units=?,redeem_tx=? WHERE id=?',(str(payout*y.CENT-y.GAS),tx['hash'] if tx else None,row['id']))
    prune_user(conn, row["user"])


def settle_if_crashed(conn, row, now):
    if row and row["status"] == "active" and current_height(row, now) >= row["crash_height"]:
        finish(conn, row, "crashed", row["crash_height"], 0, now)
        return latest(conn, row["user"])
    return row


def view(conn, user, now):
    row = settle_if_crashed(conn, latest(conn, user), now)
    account = points.ensure_account(conn, user)
    result = {"balance_cents": account["balance_cents"], "status": "idle"}
    pending=conn.execute('SELECT * FROM yidt_parachute_pending WHERE user=?',(user,)).fetchone()
    if pending:
        return result|dict(status='paid',stake=y.fmt(pending['stake_units']),paid_tx=pending['tx'])
    if not row:
        return result
    result.update(paid_tx=row['paid_tx'],redeem_tx=row['redeem_tx'],stake=y.fmt(row['stake_units'] or row['stake_cents']*y.CENT),payout=y.fmt(row['payout_units'] or row['payout_cents']*y.CENT))
    result.update({"status": row["status"], "round_id": row["id"],
                   "stake_cents": row["stake_cents"],
                   "fee_cents": row["fee_cents"],
                   "debit_cents": row["debit_cents"] or row["stake_cents"]})
    if row["status"] == "active":
        result["height"] = current_height(row, now)
    else:
        result.update({"height": row["exit_height"],
                       "crash_height": row["crash_height"],
                       "flight_elapsed_seconds": max(0.0, now - row["started_at"]),
                       "payout_cents": row["payout_cents"],
                       "profit_cents": row["payout_cents"] - row["stake_cents"],
                       "wallet_delta_cents": row["payout_cents"] - (row["debit_cents"] or row["stake_cents"])})
    return result


def error(message, status=400):
    return jsonify({"ok": False, "error": message}), status


def demo(action="state", stake_cents=1000):
    """Keep anonymous flights server-side, separate from wallets and ranked rounds."""
    if action == "start" and stake_cents != 1000:
        return error("试玩每局固定使用 10.00 积分")
    token = session.get("parachute_demo")
    if not token and action == "state":
        return jsonify(ok=True, state={"status": "idle", "demo": True})
    if not token:
        token = secrets.token_urlsafe(24)
        session["parachute_demo"] = token
    now = _now()
    with points.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""CREATE TABLE IF NOT EXISTS parachute_demo_rounds (
            token TEXT PRIMARY KEY, started_at REAL NOT NULL, payload TEXT NOT NULL
        )""")
        conn.execute("DELETE FROM parachute_demo_rounds WHERE started_at < ?", (now - 86400,))
        saved = conn.execute("SELECT payload FROM parachute_demo_rounds WHERE token=?", (token,)).fetchone()
        row = json.loads(saved["payload"]) if saved else None
        if row and row["status"] == "active" and current_height(row, now) >= row["crash_height"]:
            row.update(status="crashed", exit_height=row["crash_height"], payout_cents=0)
        if action == "start":
            if row and row["status"] == "active":
                return error("已有正在飞行的游戏，请先跳伞", 409)
            u = (secrets.randbelow(1 << 53) + 0.5) / (1 << 53)
            row = {"id": secrets.token_urlsafe(12), "stake_cents": 1000,
                   "started_at": now, "crash_height": 100.0 * u / (1.0 - u), "status": "active"}
        elif action == "jump":
            if not row or row["status"] != "active":
                if row and row["status"] == "crashed":
                    pass  # Return the crash that occurred before this jump request.
                else:
                    return error("当前没有正在飞行的游戏", 409)
            else:
                height = current_height(row, now)
                reward = int((Decimal(1000) * Decimal(str(height)) / Decimal(100)).to_integral_value(rounding=ROUND_DOWN))
                row.update(status="jumped", exit_height=height, payout_cents=1000 + reward)
        result = {"status": "idle", "demo": True}
        if row:
            conn.execute("INSERT OR REPLACE INTO parachute_demo_rounds VALUES (?,?,?)",
                         (token, row["started_at"], json.dumps(row)))
            result.update(status=row["status"], round_id=row["id"], stake_cents=1000,
                          fee_cents=0, debit_cents=1000)
            if row["status"] == "active":
                result["height"] = current_height(row, now)
            else:
                result.update(height=row["exit_height"], crash_height=row["crash_height"],
                              flight_elapsed_seconds=max(0.0, now-row["started_at"]),
                              payout_cents=row["payout_cents"], profit_cents=row["payout_cents"]-1000,
                              wallet_delta_cents=0)
        return jsonify(ok=True, state=result)


@app.before_request
def guard():
    if request.path.startswith(PREFIX) and not session.get("user"):
        if request.path in (PREFIX + "/", PREFIX + "/api/state", PREFIX + "/api/start", PREFIX + "/api/jump"):
            return
        if request.path.startswith(PREFIX + "/share/") or request.path.startswith(PREFIX + "/ranking/"):
            return
        if request.path == PREFIX + "/api/ranking":
            return
        if request.path.startswith(PREFIX + "/api/"):
            return error("请先登录", 401)
        return redirect("/login/?next=" + request.path)


@app.get(PREFIX + "/")
def page():
    user = session.get("user")
    return render_template("parachute.html", username=user, avatar_url=avatar_url(user) if user else None)


@app.get(PREFIX + "/api/state")
def state():
    if not session.get("user"):
        return demo()
    with points.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        init(conn)
        return jsonify({"ok": True, "state": view(conn, session["user"], _now())})


@app.post(PREFIX + '/api/pay')
def pay():
    if not request.is_json:return error('请求格式错误')
    if not session.get('user'):return error('请先登录',401)
    user=session['user'];raw=(request.get_json(silent=True) or {}).get('stake','')
    amount=y.units(raw)
    if amount<10*y.SCALE:return error('每局至少投入 10 YIDT')
    with points.connect() as conn:
        conn.execute('BEGIN IMMEDIATE');init(conn)
        if conn.execute('SELECT 1 FROM yidt_parachute_pending WHERE user=?',(user,)).fetchone():return jsonify(ok=True,state=view(conn,user,_now()))
        old=settle_if_crashed(conn,latest(conn,user),_now())
        if old and old['status']=='active':return error('已有正在飞行的游戏',409)
        b=y.binding(conn,user,'parachute')
        if not b:raise y.PaymentRequired('parachute',amount+FEE_CENTS*y.CENT,'跳伞投入及手续费')
        available=int(y.wallet(conn,b['address'])['balance'])
        if available<amount:raise y.Error('钱包余额不足')
        debit=min(amount+FEE_CENTS*y.CENT,available)
        ref='parachute:'+secrets.token_hex(16)
        tx=y.service_debit(conn,user,'parachute',debit,'跳伞投入及手续费',ref)
        actual=y.units(tx['credit'])-FEE_CENTS*y.CENT
        if actual<=0:raise y.Error('投入不足以支付费用')
        conn.execute('INSERT INTO yidt_parachute_pending VALUES(?,?,?,?,?,?)',(user,str(actual),str(y.units(tx['debit'])),b['address'],tx['hash'],_now()))
        return jsonify(ok=True,state=view(conn,user,_now()))


@app.post(PREFIX + '/api/start')
def start():
    if not request.is_json:return error('请求格式错误')
    if not session.get('user'):return demo('start',1000)
    user=session['user'];now=_now()
    with points.connect() as conn:
        conn.execute('BEGIN IMMEDIATE');init(conn)
        pending=conn.execute('SELECT * FROM yidt_parachute_pending WHERE user=?',(user,)).fetchone()
        if not pending:return error('请先付款，再手动开始游戏',409)
        old=settle_if_crashed(conn,latest(conn,user),now)
        if old and old['status']=='active':return error('已有正在飞行的游戏',409)
        u=(secrets.randbelow(1<<53)+0.5)/(1<<53)
        conn.execute("""INSERT INTO parachute_rounds(user,stake_cents,fee_cents,debit_cents,crash_height,started_at,status,stake_units,debit_units,paid_tx,wallet_address)
            VALUES(?,?,?,?,?,?,'active',?,?,?,?)""",(user,int(pending['stake_units'])//y.CENT,FEE_CENTS,int(pending['debit_units'])//y.CENT,100*u/(1-u),now,pending['stake_units'],pending['debit_units'],pending['tx'],pending['address']))
        conn.execute('DELETE FROM yidt_parachute_pending WHERE user=?',(user,))
        return jsonify(ok=True,state=view(conn,user,now))


@app.post(PREFIX + "/api/jump")
def jump():
    if not request.is_json:
        return error("请求格式错误")
    if not session.get("user"):
        return demo("jump")
    user = session["user"]
    now = _now()
    with points.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        init(conn)
        row = latest(conn, user)
        if not row or row["status"] != "active":
            return error("当前没有正在飞行的游戏", 409)
        height = current_height(row, now)
        if height >= row["crash_height"]:
            finish(conn, row, "crashed", row["crash_height"], 0, now)
        else:
            from decimal import localcontext
            with localcontext() as ctx:
                ctx.prec=80
                stake=Decimal(row['stake_units'] or row['stake_cents']*y.CENT)
                payout=int((stake*(1+Decimal(str(height))/100)/y.CENT).to_integral_value(rounding=ROUND_DOWN))
            finish(conn, row, "jumped", height, payout, now)
        return jsonify({"ok": True, "state": view(conn, user, now)})


@app.get(PREFIX + "/history/")
def history_page():
    user = session["user"]
    return render_template("parachute_history.html", **page_header(user))


@app.get(PREFIX + "/api/history")
def history_data():
    user = session["user"]
    with points.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        init(conn)
        prune_user(conn, user)
        rows, _, history_ids = _retention(conn, user)
        records = [row["id"] for row in rows if row["id"] in history_ids]
        if not records:
            return jsonify(ok=True, items=[])
        marks = ",".join("?" for _ in records)
        full_rows = conn.execute(f"SELECT * FROM parachute_rounds WHERE id IN ({marks}) ORDER BY id DESC", records).fetchall()
    nicknames = _nickname_map([user])
    return jsonify(ok=True, items=[_record_dict(row, nicknames) for row in full_rows])


@app.post(PREFIX + "/api/records/<int:record_id>/pin")
def pin_record(record_id):
    if not request.is_json:
        return error("请求格式错误")
    pinned = (request.get_json(silent=True) or {}).get("pinned")
    if not isinstance(pinned, bool):
        return error("挂起状态无效")
    user = session["user"]
    with points.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        init(conn)
        row = conn.execute("SELECT * FROM parachute_rounds WHERE id=? AND user=? AND status!='active'", (record_id, user)).fetchone()
        if row is None:
            return error("记录不存在", 404)
        if pinned and not row["pinned"]:
            count = conn.execute("SELECT COUNT(*) FROM parachute_rounds WHERE user=? AND pinned=1", (user,)).fetchone()[0]
            if count >= PIN_LIMIT:
                return error("最多挂起 99 条，以便保留最新一局", 409)
        conn.execute("UPDATE parachute_rounds SET pinned=? WHERE id=? AND user=?", (int(pinned), record_id, user))
        prune_user(conn, user)
        kept = conn.execute("SELECT 1 FROM parachute_rounds WHERE id=? AND user=?", (record_id, user)).fetchone()
    return jsonify(ok=True, pinned=pinned, deleted=kept is None)


@app.post(PREFIX + "/api/records/<int:record_id>/share")
def share_record(record_id):
    if not request.is_json:
        return error("请求格式错误")
    user = session["user"]
    with points.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        init(conn)
        row = conn.execute("SELECT * FROM parachute_rounds WHERE id=? AND user=? AND status!='active'", (record_id, user)).fetchone()
        if row is None:
            return error("记录不存在", 404)
        if not row["pinned"]:
            count = conn.execute("SELECT COUNT(*) FROM parachute_rounds WHERE user=? AND pinned=1", (user,)).fetchone()[0]
            if count >= PIN_LIMIT:
                return error("最多挂起 99 条，以便保留最新一局", 409)
        token = row["share_token"] or secrets.token_urlsafe(24)
        conn.execute("UPDATE parachute_rounds SET pinned=1,share_token=? WHERE id=? AND user=?", (token, record_id, user))
        prune_user(conn, user)
    return jsonify(ok=True, share_url=f"{PREFIX}/share/{token}/",
                   options_url=f"{PREFIX}/share/{token}/options/", pinned=True)


def shared_row(token):
    with points.connect() as conn:
        init(conn)
        return conn.execute("SELECT * FROM parachute_rounds WHERE share_token=? AND status!='active'", (token,)).fetchone()


@app.get(PREFIX + "/share/<token>/")
def shared_record(token):
    row = shared_row(token)
    if row is None:
        return render_template("parachute_share.html", record=None, **page_header(session.get("user"))), 404
    record = _record_dict(row, _nickname_map([row["user"]]))
    return render_template("parachute_share.html", record=record, **page_header(session.get("user")))


@app.get(PREFIX + "/share/<token>/options/")
def share_options(token):
    row = shared_row(token)
    if row is None:
        return render_template("parachute_share_options.html", record=None, **page_header(session.get("user"))), 404
    record = _record_dict(row, _nickname_map([row["user"]]))
    return render_template("parachute_share_options.html", record=record,
                           share_url=f"{PREFIX}/share/{token}/", **page_header(session.get("user")))


@app.get(PREFIX + "/ranking/")
def ranking_page():
    user = session.get("user")
    return render_template("parachute_ranking.html", **page_header(user))


@app.get(PREFIX + "/api/ranking")
def ranking_data():
    scope = request.args.get("scope", "global")
    if scope not in ("personal", "global"):
        return error("排行榜类型无效")
    user = session.get("user")
    if scope == "personal" and not user:
        return error("请先登录", 401)
    offset = max(0, request.args.get("offset", 0, type=int) or 0)
    limit = min(100, max(1, request.args.get("limit", 50, type=int) or 50))
    score_sql = "(payout_cents - COALESCE(debit_cents, stake_cents))"
    with points.connect() as conn:
        init(conn)
        if scope == "personal":
            rows = conn.execute(f"""SELECT * FROM parachute_rounds
                WHERE user=? AND status!='active'
                ORDER BY {score_sql} DESC, id DESC LIMIT ? OFFSET ?""",
                (user, min(limit, max(0, PERSONAL_RANK_LIMIT - offset)), offset)).fetchall()
        else:
            rows = conn.execute(f"""WITH eligible AS (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY user ORDER BY {score_sql} DESC, id DESC
                ) AS user_position
                FROM parachute_rounds WHERE status!='active'
            ) SELECT * FROM eligible WHERE user_position<=?
              ORDER BY {score_sql} DESC, id DESC LIMIT ? OFFSET ?""",
              (GLOBAL_USER_RANK_LIMIT, limit, offset)).fetchall()
    names = _nickname_map([row["user"] for row in rows])
    items = [{**_record_dict(row, names), "rank": offset + index + 1} for index, row in enumerate(rows)]
    has_more = len(rows) == limit and (scope == "global" or offset + limit < PERSONAL_RANK_LIMIT)
    return jsonify(ok=True, items=items, has_more=has_more)


import yidt_bridge
yidt_bridge.install(app)

if __name__ == "__main__":
    with points.connect() as conn:
        init(conn)
        users = [row[0] for row in conn.execute("SELECT DISTINCT user FROM parachute_rounds")]
        for user in users:
            prune_user(conn, user)
    app.run(host="127.0.0.1", port=5008, debug=False, use_reloader=False)
