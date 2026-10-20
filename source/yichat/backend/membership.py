"""Membership admission barrier shares the asset ledger's transaction lock.

Synapse marks a transition pending BEFORE it can accept the membership event.
Claims/gifts fail closed while a transition is pending. Thus leaving and claiming
have one serialization point with random allocation, wallet uniqueness and escrow.
"""
from .security import fail


SCHEMA='''
CREATE TABLE IF NOT EXISTS yichat_memberships(
 room TEXT NOT NULL,mxid TEXT NOT NULL,membership TEXT NOT NULL,event_id TEXT,
 pending_event TEXT,pending_worker TEXT,pending_boot TEXT,PRIMARY KEY(room,mxid));
CREATE TABLE IF NOT EXISTS yichat_membership_workers(instance TEXT PRIMARY KEY,boot TEXT NOT NULL);
'''


def require(c,room,mxid):
    row=c.execute('SELECT * FROM yichat_memberships WHERE room=? AND mxid=?',(room,mxid)).fetchone()
    if not row or row['membership']!='join' or row['pending_event']:
        fail(403,{'errcode':'M_FORBIDDEN','code':'YICHAT_NOT_CURRENT_MEMBER','error':'你不是此会话的已确认当前成员，或成员变更尚未完成'})
