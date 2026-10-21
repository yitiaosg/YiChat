import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = '''
CREATE TABLE IF NOT EXISTS yc_migrations(version INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO yc_migrations VALUES(1);
CREATE TABLE IF NOT EXISTS identities(subject TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, mxid TEXT UNIQUE NOT NULL, password TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, subject TEXT NOT NULL, username TEXT NOT NULL, sid TEXT NOT NULL, mxid TEXT NOT NULL, device TEXT NOT NULL, upstream TEXT NOT NULL, expires REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0, cleaned INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS sessions_sid ON sessions(sid);
CREATE TABLE IF NOT EXISTS logins(state TEXT PRIMARY KEY, verifier TEXT NOT NULL, nonce TEXT NOT NULL, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS native_logins(state TEXT PRIMARY KEY REFERENCES logins(state) ON DELETE CASCADE,app_state TEXT NOT NULL,app_nonce TEXT NOT NULL,challenge TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS native_codes(code_hash TEXT PRIMARY KEY,app_state TEXT NOT NULL,app_nonce TEXT NOT NULL,challenge TEXT NOT NULL,subject TEXT NOT NULL,username TEXT NOT NULL,sid TEXT NOT NULL,expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS native_devices(proof_hash TEXT PRIMARY KEY,subject TEXT NOT NULL,mxid TEXT NOT NULL,device TEXT NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS native_transactions(subject TEXT NOT NULL,device TEXT NOT NULL,room TEXT NOT NULL,txn TEXT NOT NULL,metadata TEXT NOT NULL,event TEXT,PRIMARY KEY(subject,device,room,txn));
CREATE TABLE IF NOT EXISTS rooms(room TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('direct','group')), owner TEXT NOT NULL, peer TEXT, days INTEGER NOT NULL DEFAULT 7 CHECK(days BETWEEN 1 AND 30));
CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY, room TEXT NOT NULL REFERENCES rooms(room), uploader TEXT NOT NULL, bytes INTEGER NOT NULL, completed REAL NOT NULL, expires REAL NOT NULL, removed INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS files_expiry ON files(expires,removed);
CREATE TABLE IF NOT EXISTS upload_leases(id TEXT PRIMARY KEY, subject TEXT NOT NULL, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS upload_usage(subject TEXT NOT NULL, day INTEGER NOT NULL, bytes INTEGER NOT NULL, PRIMARY KEY(subject,day));
CREATE TABLE IF NOT EXISTS rate(subject TEXT NOT NULL, bucket TEXT NOT NULL, next REAL NOT NULL, PRIMARY KEY(subject,bucket));
CREATE TABLE IF NOT EXISTS request_hits(id INTEGER PRIMARY KEY,subject TEXT NOT NULL,at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS request_hits_subject ON request_hits(subject,at);
CREATE TABLE IF NOT EXISTS room_tickets(token TEXT PRIMARY KEY,owner TEXT NOT NULL,kind TEXT NOT NULL,peer TEXT,expires REAL NOT NULL,room TEXT);
CREATE TABLE IF NOT EXISTS room_requests(subject TEXT NOT NULL,request_key TEXT NOT NULL,payload TEXT NOT NULL,ticket TEXT NOT NULL REFERENCES room_tickets(token),created REAL NOT NULL,PRIMARY KEY(subject,request_key));
CREATE TABLE IF NOT EXISTS history_receipts(event_id TEXT PRIMARY KEY,room TEXT NOT NULL,sequence INTEGER UNIQUE NOT NULL,received_ms INTEGER NOT NULL,sender TEXT NOT NULL,type TEXT NOT NULL,origin_server_ts INTEGER NOT NULL,sha256 TEXT NOT NULL,redacts TEXT,business TEXT);
CREATE INDEX IF NOT EXISTS history_receipts_room_sequence ON history_receipts(room,sequence);
CREATE INDEX IF NOT EXISTS history_receipts_time ON history_receipts(received_ms,sequence);
CREATE INDEX IF NOT EXISTS history_receipts_redacts ON history_receipts(room,redacts);
CREATE TABLE IF NOT EXISTS history_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS direct_visibility(room TEXT PRIMARY KEY,visible INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS room_modes(room TEXT PRIMARY KEY REFERENCES rooms(room),encryption TEXT NOT NULL CHECK(encryption IN ('direct','plain','legacy_encrypted')),created_ms INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS file_hashes(id TEXT PRIMARY KEY REFERENCES files(id),sha256 TEXT NOT NULL CHECK(length(sha256)=64));
CREATE TABLE IF NOT EXISTS attachment_admissions(event TEXT NOT NULL,file TEXT NOT NULL REFERENCES files(id),worker TEXT NOT NULL,boot TEXT NOT NULL,PRIMARY KEY(event,file));
CREATE TABLE IF NOT EXISTS cleanup_audit(room TEXT,day TEXT,state TEXT NOT NULL,error TEXT,PRIMARY KEY(room,day));
'''

class ClosingConnection(sqlite3.Connection):
    def __exit__(self,exc_type,exc_value,traceback):
        try:return super().__exit__(exc_type,exc_value,traceback)
        finally:self.close()


def connect(path):
    c = sqlite3.connect(path, timeout=10, factory=ClosingConnection)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    c.execute('PRAGMA busy_timeout=10000')
    return c

@contextmanager
def transaction(path):
    c = connect(path)
    try:
        c.execute('BEGIN IMMEDIATE')
        yield c
        c.commit()
    except BaseException:
        c.rollback()
        raise
    finally: c.close()

def migrate(settings):
    settings.root.mkdir(parents=True, exist_ok=True, mode=0o700)
    settings.objects.mkdir(exist_ok=True, mode=0o700)
    with connect(settings.db) as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript(SCHEMA)
        # Existing rooms really are encrypted. Preserve history and crypto state.
        c.execute("INSERT OR IGNORE INTO room_modes SELECT room,CASE WHEN kind='direct' THEN 'direct' ELSE 'legacy_encrypted' END,0 FROM rooms")
        c.execute('UPDATE rooms SET days=30')
        c.execute('UPDATE files SET expires=max(expires,(CAST(completed/86400 AS INTEGER)+31)*86400)')
        from . import groups,group_avatars,record_audit
        c.executescript(groups.SCHEMA)
        c.executescript(group_avatars.SCHEMA)
        c.executescript(record_audit.SCHEMA)
        for row in c.execute("SELECT room FROM rooms WHERE kind='group'").fetchall():groups.register(c,row['room'],seeded=0)
        c.execute("INSERT OR IGNORE INTO direct_visibility SELECT room,EXISTS(SELECT 1 FROM history_receipts h WHERE h.room=rooms.room AND h.type='m.room.encrypted') FROM rooms WHERE kind='direct'")
        c.execute('INSERT OR IGNORE INTO yc_migrations VALUES(2)')
