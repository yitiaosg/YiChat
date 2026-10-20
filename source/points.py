"""Compatibility presentation for services migrated to YIDT. Balances are never stored here."""
from __future__ import annotations
import yidt_core as y
from flask import has_request_context,request
DB_PATH=y.DB_PATH
connect=y.connect

def init(conn):y.init(conn)
def current_service():
    if has_request_context():
        if request.path.startswith('/ycards'):return 'ycards'
        if request.path.startswith('/games/parachute'):return 'parachute'
    return None

def ensure_account(conn,user):
    service=current_service();b=y.binding(conn,user,service) if service else None
    if b:balance=int(y.wallet(conn,b['address'])['balance'])//y.CENT
    elif service:balance=0
    else:balance=sum(int(r[0]) for r in conn.execute('SELECT w.balance FROM yidt_members m JOIN yidt_wallets w USING(address) WHERE m.user=?',(user,)))//y.CENT
    return dict(balance_cents=balance,streak=0,last_checkin=None)
def account_view(user):
    with connect() as c:
        row=ensure_account(c,user);d=c.execute('SELECT * FROM yidt_daily WHERE user=?',(user,)).fetchone()
        return row|dict(checked_today=bool(d and d['last_day']==y.utcday()),streak=d['streak'] if d else 0,history=[],next_bonus_in=5-(d['streak']%5 if d else 0))
def checkin(user):
    with connect() as c:
        c.execute('BEGIN IMMEDIATE');r=y.daily(c,user)
    return dict(ok=True,tx=r['hash'],reward=r['credit'],account=account_view(user))
def money(cents):return y.fmt(int(cents)*y.CENT)
def add_ledger(*args,**kwargs):raise RuntimeError('Legacy point mutation disabled; use YIDT ledger')
