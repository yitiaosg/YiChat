"""Production promotions share the existing SQLite economic transaction."""
import re
import secrets
import time
from decimal import Decimal, InvalidOperation

SCHEMA = '''
CREATE TABLE IF NOT EXISTS yc_coupons(
 code TEXT PRIMARY KEY COLLATE BINARY, kind TEXT NOT NULL CHECK(kind IN ('rate','fixed')),
 value INTEGER NOT NULL CHECK(value>=0), active INTEGER NOT NULL DEFAULT 1,
 expires REAL, max_uses INTEGER NOT NULL DEFAULT 0, per_user INTEGER NOT NULL DEFAULT 0,
 created_by TEXT NOT NULL, created REAL NOT NULL, revision INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS yc_coupon_uses(
 job TEXT PRIMARY KEY REFERENCES yc_jobs(id), code TEXT NOT NULL, user TEXT NOT NULL,
 original_total INTEGER NOT NULL, discount INTEGER NOT NULL, paid INTEGER NOT NULL,
 created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS yc_coupon_use_code ON yc_coupon_uses(code,user);
'''


def code_value(value):
    from ycards import CardError
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,40}', value):
        raise CardError('优惠码须为 1～40 个英文字母、数字、下划线或连字符')
    return value


def apply(store, conn, user, data, quote):
    from ycards import CardError
    code = data.get('coupon', '')
    if code is None or code == '':
        return
    code = code_value(code)
    original = quote['total']
    if code == 'test':
        if not getattr(store, 'is_admin', lambda username: False)(user):
            raise CardError('test 优惠码仅限 admin 使用', 403)
        paid, revision, kind, value = 0, 1, 'admin_test', 0
    else:
        row = conn.execute('SELECT * FROM yc_coupons WHERE code=?', (code,)).fetchone()
        if not row or not row['active'] or (row['expires'] is not None and row['expires'] <= time.time()):
            raise CardError('优惠码不存在、已停用或已过期', 409)
        total, own = conn.execute('SELECT COUNT(*),COALESCE(SUM(user=?),0) FROM yc_coupon_uses WHERE code=?', (user,code)).fetchone()
        if row['max_uses'] and total >= row['max_uses']:
            raise CardError('优惠码使用次数已用完', 409)
        if row['per_user'] and own >= row['per_user']:
            raise CardError('已达到该优惠码的每人使用上限', 409)
        kind, value, revision = row['kind'], row['value'], row['revision']
        paid = (original*value+9999)//10000 if kind == 'rate' else max(0,original-value)
    quote.update(original_total=original, discount=original-paid, total=paid,
                 coupon=dict(code=code,kind=kind,value=value,revision=revision))


def record(conn, job, user, quote, now):
    if quote.get('coupon'):
        conn.execute('INSERT INTO yc_coupon_uses VALUES(?,?,?,?,?,?,?)',
                     (job,quote['coupon']['code'],user,quote['original_total'],quote['discount'],quote['total'],now))


def create(store, user, data):
    from ycards import CardError, integer, number, MAX_POINTS
    if not isinstance(data, dict):
        raise CardError('优惠码参数无效')
    code = code_value(data.get('code') or ('YC-'+secrets.token_hex(4).upper()))
    if code.lower() == 'test':
        raise CardError('test 为管理员测试保留码')
    kind = data.get('kind')
    if kind == 'rate':
        try:
            text = str(data.get('value', ''))
            if not re.fullmatch(r'\d{1,3}(?:\.\d{1,2})?', text):
                raise InvalidOperation
            percent = Decimal(text)
            if not 0 <= percent < 100:
                raise InvalidOperation
            value = int(percent*100)
        except InvalidOperation as exc:
            raise CardError('支付比例须为 0～99.99%，例如 80 表示八折') from exc
    elif kind == 'fixed':
        value = integer(data.get('value'), '减免 point', 1, MAX_POINTS)
    else:
        raise CardError('请选择支付比例或固定减免')
    maximum = integer(data.get('max_uses',0), '总使用次数', 0, 1000000000)
    per_user = integer(data.get('per_user',0), '每人次数', 0, 1000000000)
    expires = data.get('expires')
    if expires is not None:
        number(expires, '有效期', time.time()+1, time.time()+86400*365*100)
    with store.transaction() as conn:
        if conn.execute('SELECT 1 FROM yc_coupons WHERE code=?',(code,)).fetchone():
            raise CardError('优惠码已存在，不能覆盖', 409)
        conn.execute('INSERT INTO yc_coupons(code,kind,value,expires,max_uses,per_user,created_by,created) VALUES(?,?,?,?,?,?,?,?)',
                     (code,kind,value,expires,maximum,per_user,user,time.time()))
    return {'code':code}


def listing(store):
    with store.connect() as conn:
        return [dict(row) for row in conn.execute('''SELECT c.*,COUNT(u.job) used,COALESCE(SUM(u.discount),0) saved
            FROM yc_coupons c LEFT JOIN yc_coupon_uses u ON u.code=c.code GROUP BY c.code ORDER BY c.created DESC LIMIT 500''')]


def toggle(store, data):
    from ycards import CardError
    if not isinstance(data, dict) or not isinstance(data.get('active'), bool):
        raise CardError('启用状态无效')
    code=code_value(data.get('code'))
    with store.transaction() as conn:
        if not conn.execute('UPDATE yc_coupons SET active=?,revision=revision+1 WHERE code=?',(int(data['active']),code)).rowcount:
            raise CardError('优惠码不存在',404)
    return {'ok':True}
