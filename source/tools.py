import os
import sqlite3
import pandas as pd
import sys
import re
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_DOWN
import numpy as np
from app import api
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from pathlib import Path
import secrets
import debug


def get_full_path(filename):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    fullpath = os.path.join(script_dir, filename)
    return fullpath

def db_get(target_str, db_path, col_idx=3):
    if not hasattr(db_get, "_cache"):
        db_get._cache = {}
    
    if db_path not in db_get._cache:
        conn = sqlite3.connect(db_path, check_same_thread=False)
        db_get._cache[db_path] = conn
    else:
        conn = db_get._cache[db_path]
    
    cursor = conn.cursor()
    
    column_name = f"col{col_idx}" if isinstance(col_idx, int) else col_idx
    
    try:
        query = f'SELECT "{column_name}" FROM settings WHERE col2 = ? LIMIT 1'
        cursor.execute(query, (target_str,))
        res = cursor.fetchone()
        return res[0] if res else None
    except:
        return None

def _get_validated_select_clause(conn, table_name, columns):
    """
    内部辅助函数：校验列名并返回 SQL SELECT 子句
    """
    if not columns:
        return "*"
    
    # 获取数据库中表的所有列名
    cursor = conn.cursor()
    try:
        cursor.execute(f"PRAGMA table_info({table_name})")
        db_cols = [info[1] for info in cursor.fetchall()]
    except Exception as e:
        raise ValueError(f"无法读取表结构: {e}")

    # 校验用户传入的列
    selected_cols = list(columns)
    for col in selected_cols:
        if col not in db_cols:
            raise ValueError(f"错误：列名 '{col}' 在数据库表 '{table_name}' 中不存在。")
    
    # 确保 'time' 列始终被包含（作为索引列）
    if 'time' in db_cols and 'time' not in selected_cols:
        selected_cols.insert(0, 'time')
    
    return ", ".join(selected_cols)

def db_to_pandas(file_name, instid, time=None, limit=None, *columns):
    if not os.path.exists(file_name):
        return pd.DataFrame()   
    
    conn = sqlite3.connect(file_name)
    table_name = instid.replace("-", "_")
    
    try:
        # 获取 SELECT 子句 (例如 "time, col1, col2" 或 "*")
        select_clause = _get_validated_select_clause(conn, table_name, columns)
        
        if time is not None and limit is not None:
            query = f"""
            SELECT * FROM (
                SELECT {select_clause} FROM {table_name} 
                WHERE time < ? 
                ORDER BY time DESC 
                LIMIT ?
            ) ORDER BY time ASC
            """
            df = pd.read_sql(query, conn, params=(time, int(limit)))
        else:
            df = pd.read_sql(f"SELECT {select_clause} FROM {table_name} ORDER BY time ASC", conn)
            
    except Exception as e:
        # 直接抛出异常或打印后重新抛出，以满足“直接报错”的需求
        raise e
    finally:
        conn.close()
    
    if df.empty:
        return df

    # 1. 索引处理
    if 'time' in df.columns:
        df.set_index('time', inplace=True)
    elif "username" in df.columns:
        df.set_index('username', inplace=True)
    
    # 2. 自动转换数值
    for col in df.columns:
        try:
            converted = pd.to_numeric(df[col], errors='coerce')
            if not converted.isna().all():
                df[col] = converted
        except:
            continue
            
    return df

def lastdb_to_pandas(file_name, instid, limit=2000, *columns):
    if not os.path.exists(file_name):
        return pd.DataFrame()

    conn = sqlite3.connect(file_name)
    table_name = instid.replace("-", "_")

    try:
        select_clause = _get_validated_select_clause(conn, table_name, columns)
        
        df = pd.read_sql(
            f"SELECT {select_clause} FROM {table_name} ORDER BY time DESC LIMIT {limit}",
            conn
        )
    except Exception as e:
        raise e
    finally:
        conn.close()

    if df.empty:
        return df

    # 翻转回正序
    df = df.iloc[::-1].reset_index(drop=True)

    if 'time' in df.columns:
        df.set_index('time', inplace=True)

    for col in df.columns:
        try:
            converted = pd.to_numeric(df[col], errors='coerce')
            if not converted.isna().all():
                df[col] = converted
        except:
            continue

    return df

def ts_to_time(ms_timestamp):
    dt = datetime.fromtimestamp(float(ms_timestamp) / 1000.0, tz=timezone.utc)
    return dt.isoformat(timespec='milliseconds').replace("+00:00", "Z")
from datetime import datetime, timezone


def time_to_ts(isots):
    normalized_str = isots.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized_str)
    return int(dt.timestamp() * 1000)

def mstous(text):
    return text.replace("-", "_")

def get_multiplier(bar_str):
    try:
        num = int(bar_str[:-1])
        unit = bar_str[-1]
        return num * {'m': 1, 'h': 60, 'd': 1440}.get(unit, 1)
    except:
        return 1
def timeinj(table, cline):
    if cline is None:
        print(f"[Error] Variable 'cline' not found.")
        return

    try:
        if 'time' in cline.columns:
            df_to_inj = cline[['time']].copy()
        else:
            df_to_inj = pd.DataFrame(index=cline.index)
            df_to_inj['time'] = cline.index

        target_dbs = ["cdata.db", "cbal.db"]
        
        for db_name in target_dbs:
            db_path = get_full_path(db_name)
            
            with sqlite3.connect(db_path) as conn:
                df_to_inj.to_sql(table, conn, if_exists='replace', index=False)
                conn.execute(f"CREATE INDEX IF NOT EXISTS \"idx_time_{table}\" ON \"{table}\"(time)")
                conn.commit()
                
    except Exception as e:
        print(f"[Error] Time injection failed: {e}")

RAW_CONF = {
    ("BTC-USDT-SWAP", "3m"):  (50, 3.1, 1),
    ("BTC-USDT-SWAP", "5m"):  (50, 2.5, 1),
    ("BTC-USDT-SWAP", "15m"): (150, 2.5, 1),
    ("BTC-USDT-SWAP", "30m"): (300, 2.5, 1),
    ("BTC-USDT-SWAP", "1h"):  (180, 1.0, 1),

    ("ETH-USDT-SWAP", "3m"):  (100, 3.0, 2),
    ("ETH-USDT-SWAP", "5m"):  (100, 2.1, 2),
    ("ETH-USDT-SWAP", "15m"): (300, 2.0, 2),
}

def _get_conf_value(instid, cycle):
    return RAW_CONF.get((instid, cycle))

def bbper(instid, cycle):
    res = _get_conf_value(instid, cycle)
    return int(res[0]) if res else 50

def bbdev(instid, cycle):
    res = _get_conf_value(instid, cycle)
    return float(res[1]) if res else 3.1

def pricefor(instid, cycle):
    res = _get_conf_value(instid, cycle)
    return int(res[2]) if res else 1

def str2bool(s):
    return {"true": True, "false": False}[s.lower()]


def truncate(number, decimals):
    if number is None or np.isnan(number):
        return np.nan
    decimals = int(decimals) 
    d = Decimal(str(number))
    if decimals > 0:
        quantize_str = "0." + "0" * (decimals - 1) + "1"
    else:
        quantize_str = "1"
    return float(d.quantize(Decimal(quantize_str), rounding=ROUND_DOWN))


def get_dl_count(inst_id, cycle, db_path):
    # 1. 解析周期参数
    match = re.match(r"^(\d+)([mhd])$", cycle)
    if not match:
        raise ValueError("无效的周期格式。请输入形如 '5m', '1h', '2d' 的字符串。")

    value, unit = match.groups()
    value = int(value)

    if unit == "m":
        expected_diff = timedelta(minutes=value)
    elif unit == "h":
        expected_diff = timedelta(hours=value)
    elif unit == "d":
        expected_diff = timedelta(days=value)
    else:
        raise ValueError("无效的周期单位。")

    # 2. 读取后 10000 行时间
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        if not re.match(r"^[a-zA-Z0-9_]+$", inst_id):
            raise ValueError("表名包含非法字符。")

        query = f'SELECT time FROM "{inst_id}" ORDER BY time DESC LIMIT 10000'
        cursor.execute(query)
        rows = cursor.fetchall()
    except sqlite3.Error as e:
        print(f"数据库读取错误: {e}")
        return 0
    finally:
        conn.close()

    if not rows:
        return 0

    time_strings = [row[0] for row in reversed(rows)]

    if len(time_strings) < 2:
        return 0

    def parse_time(t_str):
        return datetime.fromisoformat(t_str.replace("Z", "+00:00"))

    # 3. 找到原逻辑下“即将返回”的那个时间点
    target_time_str = time_strings[-1]
    for i in range(len(time_strings) - 1):
        t1 = parse_time(time_strings[i])
        t2 = parse_time(time_strings[i + 1])

        if t2 - t1 != expected_diff:
            target_time_str = time_strings[i]
            break

    # 4. 和当前时间比较，计算还差多少根
    target_time = parse_time(target_time_str)
    now_time = datetime.now(timezone.utc)

    diff = now_time - target_time
    if diff.total_seconds() <= 0:
        return 0

    cycles = diff // expected_diff
    need = int(cycles) - 1

    return max(0, need)


def dlkline(instId, bar, count, db_path):
    def _api_call_with_retry(func, args, timeout=1, retries=5):
        """
        API 超时重试：
        - 单次等待 1 秒
        - 连续重试 5 次
        - 仍无响应则抛错
        """
        last_err = None
    
        for _ in range(retries):
            executor = ThreadPoolExecutor(max_workers=1)
            future = executor.submit(func, *args)
            try:
                result = future.result(timeout=timeout)
                executor.shutdown(wait=False, cancel_futures=True)
                return result
            except FuturesTimeoutError as e:
                last_err = e
                executor.shutdown(wait=False, cancel_futures=True)
                continue
            except Exception:
                executor.shutdown(wait=False, cancel_futures=True)
                raise
    
        raise TimeoutError(f"API 请求连续 {retries} 次超过 {timeout} 秒无响应") from last_err
    full_path = get_full_path(db_path)
    table_name = instId.replace("-", "_")
    all_raw_data = []

    os.makedirs(os.path.dirname(full_path), exist_ok=True)

    # 直接覆盖旧数据库文件
    if os.path.exists(full_path):
        os.remove(full_path)

    # 1. 获取最新数据
    first_batch = _api_call_with_retry(api.new_kl, (instId, bar, 300), timeout=1, retries=5)
    if not first_batch:
        print(f"[Error] API returned no data for {bar} on {instId}")
        return

    all_raw_data.extend(first_batch)
    last_ts = first_batch[-1][0]

    # 2. 循环获取历史数据
    while len(all_raw_data) < count:
        msg = f"Downloading {bar} to {db_path}: {len(all_raw_data)}/{count}"
        sys.stdout.write(f"\r{msg}")
        sys.stdout.flush()

        history_batch = _api_call_with_retry(api.his_kl, (instId, bar, last_ts, 300), timeout=1, retries=5)
        if not history_batch:
            break

        all_raw_data.extend(history_batch)
        last_ts = history_batch[-1][0]

    sys.stdout.write(f"\r{' ' * 80}\r")
    sys.stdout.flush()

    # 3. 清洗数据
    reversed_data = all_raw_data[::-1]
    clean_data = [
        (str(int(r[0])), ts_to_time(int(r[0])), r[1], r[2], r[3], r[4], r[5], r[6])
        for r in reversed_data if r[-1] != "0"
    ]

    if not clean_data:
        print(f"[Error] No valid kline data for {instId} {bar}")
        return

    # 4. 直接写入新库
    conn = sqlite3.connect(full_path)
    try:
        cursor = conn.cursor()
        cursor.execute("PRAGMA synchronous = NORMAL")
        cursor.execute("PRAGMA journal_mode = DELETE")

        cursor.execute(f"""
            CREATE TABLE "{table_name}" (
                ts TEXT,
                time TEXT PRIMARY KEY,
                open TEXT,
                high TEXT,
                low TEXT,
                close TEXT,
                vol TEXT,
                volCcy TEXT
            )
        """)

        cursor.executemany(
            f'INSERT INTO "{table_name}" VALUES (?,?,?,?,?,?,?,?)',
            clean_data
        )

        conn.commit()
    finally:
        conn.close()

    print(f"Done: {db_path} ({len(clean_data)} rows)")


ACCOUNT_DB = Path(__file__).resolve().parent / "account.db"


def adduser(username, password, role="guest", secretkey=None):
    """
    命令行加/改用户。写 account.db:users，密码按 website.py 的方式哈希存储。
    role: 'guest' 或 'user'。user 等级登录时会被要求补绑身份验证器。
    """
    from werkzeug.security import generate_password_hash

    conn = sqlite3.connect(ACCOUNT_DB)
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

        pw_hash = generate_password_hash(password) if password else None
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        exists = conn.execute(
            "SELECT 1 FROM users WHERE username=?", (username,)
        ).fetchone()

        if exists:
            conn.execute(
                "UPDATE users SET password_hash=?, role=?, totp_secret=? WHERE username=?",
                (pw_hash, role, secretkey, username),
            )
        else:
            conn.execute(
                """
                INSERT INTO users (username, password_hash, role, totp_secret,
                                   totp_on_login, nickname, biography, created_at)
                VALUES (?, ?, ?, ?, 1, '', '', ?)
                """,
                (username, pw_hash, role, secretkey, now),
            )

        conn.commit()
        print(f"用户 '{username}' 已添加/更新（role={role}）。")
    finally:
        conn.close()


def rmuser(username):
    if not os.path.exists(ACCOUNT_DB):
        print("数据库不存在！")
        return

    conn = sqlite3.connect(ACCOUNT_DB)
    try:
        cur = conn.execute("DELETE FROM users WHERE username=?", (username,))
        conn.commit()
        if cur.rowcount:
            print(f"用户 '{username}' 已删除。")
        else:
            print(f"用户 '{username}' 不存在。")
    except sqlite3.OperationalError:
        print("users 表不存在！")

    finally:
        conn.close()






KEY_FILE = Path("/root/yitiao/.secret_key")

def get_secret_key():
    if KEY_FILE.exists():
        return KEY_FILE.read_text(encoding="utf-8").strip()

    key = secrets.token_hex(32)
    KEY_FILE.write_text(key, encoding="utf-8")
    os.chmod(KEY_FILE, 0o600)
    return key