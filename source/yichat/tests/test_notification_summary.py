import hashlib,json,time
from pathlib import Path
import pytest
from backend import notification_summary as n,archive,push
from backend.db import connect

@pytest.mark.parametrize('row',json.loads((Path(__file__).parent/'fixtures/notification-summary.json').read_text()),ids=lambda row:row['name'])
def test_summary_matches_shared_frontend_cases(row):
    assert n.summary(row['content'],'private')==row['private']
    assert n.summary(row['content'],'group')==row['group']


def test_preview_fits_utf8_budget_and_removes_only_unsafe_controls():
    text=n.bounded('字😀'*1000)
    assert len(text.encode())<=1200 and text.endswith('…')
    assert n.bounded('你好\x00\n明天见')=='你好\n明天见'
    value=push.payload('token',{'room':'!room','kind':'direct','event':'$event','preview':'😀'*2000,'recipient_tag':'a'*64})
    assert len(json.dumps(value['message']['data'],ensure_ascii=False).encode())<4096
    assert value['message']['data']['message_preview'].endswith('…')


def seed(s,sequence,eid,body):
    event={'type':'m.room.message','event_id':eid,'room_id':'!test:chat.yitiao.org','sender':'@bob:chat.yitiao.org','content':{'msgtype':'m.text','body':body}}
    now=int(time.time()*1000)
    archive.ingest_batch(s.archive_ingest,[(eid,event['room_id'],sequence,now,json.dumps(event))],sequence,now)
    raw=archive.canonical(event)
    with connect(s.db) as c:
        c.execute('INSERT INTO history_receipts VALUES(?,?,?,?,?,?,?,?,?,?)',(eid,event['room_id'],sequence,now,event['sender'],event['type'],now,hashlib.sha256(raw).hexdigest(),None,None))
    return {'room':event['room_id'],'kind':'direct','event':eid}


def test_preview_requires_unread_authorized_hash_verified_source(env):
    s,f,u=env;hint=seed(s,1,'$preview','真实正文')
    assert n.event_preview(s,u['alice'],hint)=='真实正文'
    with connect(s.db) as c:c.execute("UPDATE history_receipts SET sha256=?",('0'*64,))
    with pytest.raises(ValueError,match='integrity'):n.event_preview(s,u['alice'],hint)
    with connect(s.db) as c:c.execute('INSERT INTO conversation_preferences(subject,room,muted) VALUES(?,?,1)',(u['alice']['subject'],hint['room']))
    assert n.event_preview(s,u['alice'],hint)==''
