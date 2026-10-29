"""FCM HTTP v1 wakeups and negotiated, bounded plaintext notification previews."""
import asyncio, contextlib, json, logging, re, secrets, time
from pathlib import Path
from urllib.parse import quote
import jwt
from cryptography.fernet import Fernet
from fastapi import Request
from .db import connect, transaction
from .security import digest, fail, valid_sid
from .conversations import notification_hints,unread_notification_events
from .notification_summary import event_preview,bounded

log=logging.getLogger('yichat.push')
SCHEMA='''CREATE TABLE IF NOT EXISTS push_devices(
 subject TEXT NOT NULL,device TEXT NOT NULL,session_hash TEXT NOT NULL,
 token_hash TEXT NOT NULL UNIQUE,token TEXT NOT NULL,enabled INTEGER NOT NULL,
 cursor INTEGER NOT NULL,lease TEXT,lease_until REAL NOT NULL DEFAULT 0,
 next_attempt REAL NOT NULL DEFAULT 0,failures INTEGER NOT NULL DEFAULT 0,preview_version INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(subject,device));'''

def migrate(c):
    # Old APKs reject extra fields: only opt-in devices receive previews.
    if 'preview_version' not in {r['name'] for r in c.execute('PRAGMA table_info(push_devices)')}:
        c.execute('ALTER TABLE push_devices ADD COLUMN preview_version INTEGER NOT NULL DEFAULT 0')

def payload(token,room):
    data={'conversation_id':room['room'],'conversation_type':'group' if room['kind']=='group' else 'private','message_id':room['event']}
    preview=bounded(room.get('preview'))
    tag=room.get('recipient_tag','')
    if preview and isinstance(tag,str) and re.fullmatch('[a-f0-9]{64}',tag):data.update(message_preview=preview,recipient_tag=tag)
    return {'message':{'token':token,'android':{'priority':'high','direct_boot_ok':True,'ttl':'86400s'},'data':data}}

class FCMSender:
    def __init__(self,settings,http):
        self.s=settings;self.http=http;self.access=None;self.expires=0
        self.cipher=Fernet(settings.session_key.encode())
        self.credentials=None
        if settings.fcm_project and settings.fcm_credentials:
            credentials=json.loads(Path(settings.fcm_credentials).read_text())
            if credentials.get('type')!='service_account' or credentials.get('project_id')!=settings.fcm_project:
                raise ValueError('FCM service account must belong to the configured project')
            if not re.fullmatch(r'[a-z][a-z0-9-]{4,62}',settings.fcm_project):raise ValueError('Invalid FCM project')
            self.credentials=credentials
    @property
    def configured(self):return self.credentials is not None
    async def bearer(self):
        if self.access and self.expires>time.time()+60:return self.access
        now=int(time.time());credentials=self.credentials
        assertion=jwt.encode({'iss':credentials['client_email'],'scope':'https://www.googleapis.com/auth/firebase.messaging',
            'aud':'https://oauth2.googleapis.com/token','iat':now,'exp':now+3600},credentials['private_key'],algorithm='RS256')
        response=await self.http.post('https://oauth2.googleapis.com/token',data={'grant_type':'urn:ietf:params:oauth:grant-type:jwt-bearer','assertion':assertion},timeout=15)
        if response.status_code!=200:raise RuntimeError('FCM authorization unavailable')
        result=response.json();self.access=result['access_token'];self.expires=time.time()+int(result['expires_in'])
        return self.access
    async def send(self,token,room):
        response=await self.http.post('https://fcm.googleapis.com/v1/projects/'+quote(self.s.fcm_project,safe='')+'/messages:send',
            headers={'Authorization':'Bearer '+await self.bearer()},json=payload(token,room),timeout=15)
        # FCM response strings may echo client data. Never include response bodies in logs.
        result=response.json() if response.content else {}
        errors=result.get('error',{}).get('details',[]) if isinstance(result,dict) else []
        if any(e.get('@type')=='type.googleapis.com/google.firebase.fcm.v1.FcmError' and e.get('errorCode')=='UNREGISTERED' for e in errors):return 'expired'
        if response.status_code==401:self.access=None
        if response.status_code not in (200,201):raise RuntimeError('FCM delivery unavailable')
        return 'sent'
    async def drain(self):
        if not self.configured:return
        # A DB lease prevents multiple gateway workers sending the same device
        # concurrently. A crash may repeat an event: clients replace by room ID.
        now=time.time();lease=secrets.token_hex(16)
        with transaction(self.s.db) as c:
            row=c.execute('SELECT * FROM push_devices WHERE enabled=1 AND lease_until<? AND next_attempt<=? ORDER BY next_attempt,subject,device LIMIT 1',(now,now)).fetchone()
            if not row:return
            c.execute('UPDATE push_devices SET lease=?,lease_until=? WHERE subject=? AND device=?',(lease,now+90,row['subject'],row['device']))
        try:
            with connect(self.s.db) as c:session=c.execute('SELECT * FROM sessions WHERE token_hash=? AND revoked=0 AND expires>?',(row['session_hash'],time.time())).fetchone()
            if not session or not valid_sid(self.s,session['sid'],session['username']):
                with transaction(self.s.db) as c:c.execute('DELETE FROM push_devices WHERE lease=?',(lease,))
                return
            result=notification_hints(self.s,dict(session),row['cursor'])
            token=self.cipher.decrypt(row['token'].encode()).decode()
            for room in result['rooms']:
                # Recheck revocation and user preferences close to the send.
                with connect(self.s.db) as c:
                    live=c.execute('SELECT 1 FROM sessions WHERE token_hash=? AND revoked=0 AND expires>?',(row['session_hash'],time.time())).fetchone()
                    preference=c.execute('SELECT archived,muted,read_sequence FROM conversation_preferences WHERE subject=? AND room=?',(row['subject'],room['room'])).fetchone()
                if not live or (preference and (preference['archived'] or preference['muted'] or preference['read_sequence']>=int(room['sequence']))):continue
                if row['preview_version']==1:
                    try:
                        preview=await asyncio.to_thread(event_preview,self.s,dict(session),room)
                    except Exception:
                        # Fixed category only: source bodies/errors can be private.
                        log.warning('FCM preview unavailable; sending route only')
                        preview=''
                    if preview:room=room|{'preview':preview,'recipient_tag':digest('|'.join(['https://'+self.s.server_name,session['subject'],session['mxid'],session['device']]))}
                    if room['event'] not in unread_notification_events(self.s,dict(session),room['room'],room['event']):continue
                with connect(self.s.db) as c:
                    active=c.execute('SELECT 1 FROM push_devices p JOIN sessions s ON s.token_hash=p.session_hash WHERE p.lease=? AND p.enabled=1 AND s.revoked=0 AND s.expires>?',(lease,time.time())).fetchone()
                if not active:continue
                if await self.send(token,room)=='expired':
                    with transaction(self.s.db) as c:c.execute('DELETE FROM push_devices WHERE lease=?',(lease,))
                    return
            with transaction(self.s.db) as c:c.execute('UPDATE push_devices SET cursor=?,lease=NULL,lease_until=0,failures=0,next_attempt=? WHERE lease=?',(int(result['cursor']),time.time()+(0 if result.get('has_more') else 2),lease))
        except asyncio.CancelledError:raise
        except Exception:
            # No exception text/traceback: neither token nor clear content in logs.
            log.warning('FCM batch pending; cursor retained')
            with transaction(self.s.db) as c:c.execute('UPDATE push_devices SET lease=NULL,lease_until=0,failures=failures+1,next_attempt=? WHERE lease=?',(time.time()+min(300,2**min(row['failures']+1,8)),lease))
    async def run(self):
        while True:
            try:await self.drain()
            except asyncio.CancelledError:raise
            except Exception:log.warning('FCM dispatch unavailable')
            await asyncio.sleep(.5)

def install(app,s,session):
    @app.post('/api/push/device')
    async def register(request:Request):
        who=session(request)
        if not app.state.push.configured:fail(503,'尚未配置系统推送；继续使用后台连接')
        with connect(s.db) as c:native=c.execute('SELECT 1 FROM native_devices WHERE subject=? AND device=?',(who['subject'],who['device'])).fetchone()
        if not native:fail(403,'仅限已授权 App 设备')
        data=await request.json()
        if not isinstance(data,dict) or not {'token','enabled'}<=set(data)<= {'token','enabled','preview_version'} or type(data['enabled'])!=bool or not isinstance(data['token'],str) or not re.fullmatch(r'[A-Za-z0-9:_.-]{20,4096}',data['token']):fail(400,'推送注册格式无效')
        version=data.get('preview_version',0)
        if type(version)!=int or version not in (0,1):fail(400,'推送预览协议版本无效')
        with transaction(s.db) as c:
            high=c.execute('SELECT coalesce(max(sequence),0) FROM history_receipts').fetchone()[0]
            c.execute('DELETE FROM push_devices WHERE token_hash=? AND (subject<>? OR device<>?)',(digest(data['token']),who['subject'],who['device']))
            c.execute('''INSERT INTO push_devices(subject,device,session_hash,token_hash,token,enabled,cursor,preview_version) VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(subject,device) DO UPDATE SET session_hash=excluded.session_hash,token_hash=excluded.token_hash,token=excluded.token,
                cursor=CASE WHEN push_devices.enabled=0 THEN excluded.cursor ELSE push_devices.cursor END,enabled=excluded.enabled,lease=NULL,lease_until=0,next_attempt=0,failures=0,preview_version=excluded.preview_version''',
                (who['subject'],who['device'],who['token_hash'],digest(data['token']),app.state.cipher.encrypt(data['token'].encode()).decode(),int(data['enabled']),high,version))
        return {'registered':True}
