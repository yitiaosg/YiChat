import sys,time,sqlite3,shutil,json
from psycopg.conninfo import conninfo_to_dict
from pathlib import Path
import pytest
from cryptography.fernet import Fernet
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.config import Settings
from backend.db import migrate,connect
from backend.funds import Funds
from backend import policy,archive

@pytest.fixture
def postgres():
    dsns=json.loads((Path.home()/'.cache/yichat-test-dsns.json').read_text())
    for kind,dsn in dsns.items():
        info=conninfo_to_dict(dsn)
        if info.get('dbname')!='yichat_archive_test' or info.get('user')!='yc_test_'+kind or info.get('host') not in ('127.0.0.1','localhost','::1'):
            raise RuntimeError('Refusing destructive fixture outside dedicated local yc_test_* database')
    with archive.connect(dsns['owner']) as c:
        tables=c.execute("SELECT tablename FROM pg_tables WHERE schemaname='yichat'").fetchall()
        c.execute('TRUNCATE '+','.join('yichat.'+r['tablename'] for r in tables)+' CASCADE')
    return dsns

@pytest.fixture
def env(tmp_path,monkeypatch,postgres):
    site=tmp_path/'site';site.mkdir()
    shutil.copy(Path(__file__).resolve().parents[2]/'yidt_core.py',site/'yidt_core.py')
    s=Settings(root=tmp_path/'chat',site=site,origin='http://testserver',session_key=Fernet.generate_key().decode(),admin_token='fixture-admin')
    s.archive_reader=postgres['read'];s.archive_ingest=postgres['ingest'];s.archive_sealer=postgres['seal'];s.archive_cleanup=postgres['cleanup'];s.archive_path=tmp_path/'archives'
    f=Funds(s);y=f.y
    monkeypatch.setattr(y,'DB_PATH',site/'userdata.db');monkeypatch.setattr(y,'ACCOUNT_DB',site/'account.db');monkeypatch.setattr(y,'KEY_PATH',site/'key')
    users=['alice','bob','carol','dave','eve','shared']
    with sqlite3.connect(y.ACCOUNT_DB) as c:
        c.execute('CREATE TABLE users(username TEXT PRIMARY KEY,role TEXT,nickname TEXT)')
        c.executemany('INSERT INTO users VALUES(?,?,?)',[(u,'user',u) for u in users])
    with y.connect() as c:
        c.execute('CREATE TABLE points_accounts(user TEXT PRIMARY KEY,balance_cents INTEGER,streak INTEGER,last_checkin TEXT)')
        c.executemany('INSERT INTO points_accounts VALUES(?,100000,0,NULL)',[(u,) for u in users])
    y.migrate();migrate(s);f.migrate();policy.migrate_identities(s);s.funds_enabled=True;s.official=y.OFFICIAL
    with y.connect() as c:
        for user in users:
            address=c.execute('SELECT address FROM yidt_members WHERE user=?',(user,)).fetchone()[0]
            c.execute('INSERT INTO yidt_bindings VALUES(?,?,?,0,?)',(user,'yichat',address,y.stamp()))
            c.execute('INSERT INTO yidt_sessions VALUES(?,?,?,0)',('sid-'+user,user,time.time()+86400))
    sessions={u:{'username':u,'subject':policy.identity(s,u)['uid'],'sid':'sid-'+u,'mxid':'@'+u+':chat.yitiao.org'} for u in users}
    with connect(s.db) as c:
        for u in users:c.execute('INSERT INTO identities VALUES(?,?,?,?)',(sessions[u]['subject'],u,sessions[u]['mxid'],Fernet(s.session_key.encode()).encrypt(b'test-password').decode()))
    with connect(s.ledger) as c:
        for who in sessions.values():c.execute("INSERT INTO yichat_memberships VALUES(?,?,'join','fixture-accepted',NULL,NULL,NULL)",('!test:chat.yitiao.org',who['mxid']))
    return s,f,sessions
