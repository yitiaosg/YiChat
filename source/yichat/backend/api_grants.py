"""One-time send grants for the developer API.

The Synapse module normally admits an event only while its sender has a live
main-site session. A message sent with an API key has no such session, so the
gateway registers a short-lived grant for exactly that event (room, sender,
type and a digest of the full content) just before sending it. The module
accepts the event if a matching, unexpired, unconsumed grant exists and the
API key that created it still exists. The gateway marks the grant consumed
once Synapse returns the event ID.

No FastAPI or network imports: this module is loaded inside Synapse.
"""
import hashlib
import json
import secrets
import time
from .db import connect, transaction

SCHEMA='''CREATE TABLE IF NOT EXISTS api_send_grants(id TEXT PRIMARY KEY,key_id TEXT NOT NULL,room TEXT NOT NULL,sender TEXT NOT NULL,type TEXT NOT NULL,digest TEXT NOT NULL,expires REAL NOT NULL,event TEXT);
CREATE INDEX IF NOT EXISTS api_send_grants_lookup ON api_send_grants(room,sender,type,digest);'''
TTL=120


def content_digest(content):
    return hashlib.sha256(json.dumps(content,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def create(s,key_id,room,sender,typ,content):
    gid=secrets.token_hex(16);now=time.time()
    with transaction(s.db) as c:
        c.execute('DELETE FROM api_send_grants WHERE expires<?',(now-86400,))
        c.execute('INSERT INTO api_send_grants VALUES(?,?,?,?,?,?,?,NULL)',(gid,key_id,room,sender,typ,content_digest(content),now+TTL))
    return gid


def granted(s,room,sender,typ,content):
    with connect(s.db) as c:
        return bool(c.execute('''SELECT 1 FROM api_send_grants g JOIN api_keys k ON k.id=g.key_id
            WHERE g.room=? AND g.sender=? AND g.type=? AND g.digest=? AND g.expires>? AND g.event IS NULL LIMIT 1''',
            (room,sender,typ,content_digest(content),time.time())).fetchone())


def consume(s,gid,event_id):
    with transaction(s.db) as c:c.execute('UPDATE api_send_grants SET event=? WHERE id=? AND event IS NULL',(event_id,gid))


def revoke_key(c,key_id):
    c.execute('DELETE FROM api_send_grants WHERE key_id=? AND event IS NULL',(key_id,))
