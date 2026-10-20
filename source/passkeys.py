"""WebAuthn storage/verification. Separate stores and RP IDs isolate the two sites."""
import base64,hashlib,json,secrets,sqlite3,time
from contextlib import contextmanager
from webauthn import generate_registration_options,generate_authentication_options,verify_registration_response,verify_authentication_response,options_to_json
from webauthn.helpers.structs import AuthenticatorSelectionCriteria,ResidentKeyRequirement,UserVerificationRequirement,PublicKeyCredentialDescriptor
from webauthn.helpers.cose import COSEAlgorithmIdentifier

class Error(ValueError):pass

def encode(value):return base64.urlsafe_b64encode(value).decode().rstrip('=')
def decode(value):return base64.urlsafe_b64decode(value+'='*((-len(value))%4))
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False)
def credential_data(data):
    if not isinstance(data,dict) or not isinstance(data.get('token'),str):raise Error('通行密钥响应格式无效')
    credential=data.get('credential')
    if not isinstance(credential,dict) or not isinstance(credential.get('id'),str) or not isinstance(credential.get('response'),dict):raise Error('通行密钥响应格式无效')
    try:
        client=json.loads(decode(credential['response']['clientDataJSON']))
        if client.get('crossOrigin') or client.get('topOrigin'):raise ValueError()
    except Exception as exc:raise Error('通行密钥请求来源无效') from exc
    return credential

def form_context(path,fields):
    # Include every business field and submit button, excluding authentication data.
    return hashlib.sha256(canonical([path,{k:fields.getlist(k) for k in sorted(fields) if k not in ('csrf','pin','passkey_response')}]).encode()).hexdigest()

class Store:
    def __init__(self,connect,rp_id,name,origins):self.connect,self.rp_id,self.name,self.origins=connect,rp_id,name,origins
    def init(self,c):
        c.execute('''CREATE TABLE IF NOT EXISTS passkey_users(user TEXT PRIMARY KEY,handle BLOB UNIQUE NOT NULL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS passkey_credentials(id TEXT PRIMARY KEY,user TEXT NOT NULL,public_key BLOB NOT NULL,sign_count INTEGER NOT NULL,name TEXT NOT NULL,created REAL NOT NULL,last_used REAL,backed_up INTEGER NOT NULL DEFAULT 0)''')
        c.execute('CREATE INDEX IF NOT EXISTS passkey_credentials_user ON passkey_credentials(user)')
        c.execute('''CREATE TABLE IF NOT EXISTS passkey_challenges(id TEXT PRIMARY KEY,challenge BLOB NOT NULL,user TEXT,session_id TEXT NOT NULL,purpose TEXT NOT NULL,context TEXT NOT NULL,expires REAL NOT NULL)''')
    @contextmanager
    def db(self):
        c=self.connect()
        try:
            self.init(c)
            yield c
            c.commit()
        except Exception:c.rollback();raise
        finally:c.close()
    def items(self,user):
        with self.db() as c:return [dict(r) for r in c.execute('SELECT id,name,created,last_used FROM passkey_credentials WHERE user=? ORDER BY created',(user,))]
    def has(self,user):return bool(user and self.items(user))
    def _new(self,c,user,sid,purpose,context):
        c.execute('DELETE FROM passkey_challenges WHERE expires<?',(time.time(),))
        # Bound pending work per browser without invalidating separate active tabs.
        if c.execute('SELECT COUNT(*) FROM passkey_challenges WHERE session_id=?',(sid,)).fetchone()[0]>=16:
            c.execute('DELETE FROM passkey_challenges WHERE id=(SELECT id FROM passkey_challenges WHERE session_id=? ORDER BY expires LIMIT 1)',(sid,))
        token=secrets.token_urlsafe(32);challenge=secrets.token_bytes(32)
        c.execute('INSERT INTO passkey_challenges VALUES(?,?,?,?,?,?,?)',(token,challenge,user,sid,purpose,canonical(context),time.time()+300))
        return token,challenge
    def _take(self,c,token,user,sid,purpose,context):
        row=c.execute('SELECT * FROM passkey_challenges WHERE id=?',(token,)).fetchone()
        if not row or row['expires']<=time.time() or row['user']!=user or row['session_id']!=sid or row['purpose']!=purpose or row['context']!=canonical(context):raise Error('通行密钥请求已失效，请重试')
        c.execute('DELETE FROM passkey_challenges WHERE id=?',(token,))
        return row
    def registration_options(self,user,sid):
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            rows=list(c.execute('SELECT id FROM passkey_credentials WHERE user=?',(user,)))
            if len(rows)>=8:raise Error('每个账户最多设置 8 个通行密钥')
            c.execute('INSERT OR IGNORE INTO passkey_users VALUES(?,?)',(user,secrets.token_bytes(32)))
            handle=c.execute('SELECT handle FROM passkey_users WHERE user=?',(user,)).fetchone()[0]
            token,challenge=self._new(c,user,sid,'register',{})
            options=generate_registration_options(rp_id=self.rp_id,rp_name=self.name,user_name=user,user_id=handle,challenge=challenge,
                authenticator_selection=AuthenticatorSelectionCriteria(resident_key=ResidentKeyRequirement.REQUIRED,require_resident_key=True,user_verification=UserVerificationRequirement.REQUIRED),
                exclude_credentials=[PublicKeyCredentialDescriptor(id=decode(r['id'])) for r in rows],
                supported_pub_key_algs=[COSEAlgorithmIdentifier.ECDSA_SHA_256,COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256],timeout=120000)
            return dict(token=token,publicKey=json.loads(options_to_json(options)))
    def register(self,c,user,sid,data):
        credential=credential_data(data)
        row=self._take(c,data.get('token'),user,sid,'register',{})
        if c.execute('SELECT COUNT(*) FROM passkey_credentials WHERE user=?',(user,)).fetchone()[0]>=8:raise Error('每个账户最多设置 8 个通行密钥')
        name=str(data.get('name') or '通行密钥').strip()
        if not 1<=len(name)<=40:raise Error('名称须为 1～40 个字符')
        try:
            result=verify_registration_response(credential=credential,expected_challenge=row['challenge'],expected_rp_id=self.rp_id,expected_origin=self.origins,require_user_verification=True,supported_pub_key_algs=[COSEAlgorithmIdentifier.ECDSA_SHA_256,COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256])
        except Exception as exc:raise Error('通行密钥创建验证失败，请重试') from exc
        try:c.execute('INSERT INTO passkey_credentials VALUES(?,?,?,?,?,?,NULL,?)',(encode(result.credential_id),user,result.credential_public_key,result.sign_count,name,time.time(),int(result.credential_backed_up)))
        except sqlite3.IntegrityError as exc:raise Error('这个通行密钥已经绑定') from exc
    def authentication_options(self,user,sid,purpose,context):
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            rows=list(c.execute('SELECT id FROM passkey_credentials WHERE user=?',(user,))) if user else []
            if user and not rows:raise Error('当前账户没有可用通行密钥')
            token,challenge=self._new(c,user,sid,purpose,context)
            options=generate_authentication_options(rp_id=self.rp_id,challenge=challenge,user_verification=UserVerificationRequirement.REQUIRED,
                allow_credentials=[PublicKeyCredentialDescriptor(id=decode(r['id'])) for r in rows],timeout=120000)
            return dict(token=token,publicKey=json.loads(options_to_json(options)))
    def authenticate(self,c,user,sid,purpose,context,data):
        credential=credential_data(data)
        row=self._take(c,data.get('token'),user,sid,purpose,context)
        key=c.execute('SELECT * FROM passkey_credentials WHERE id=?',(credential.get('id'),)).fetchone()
        if not key or (user and key['user']!=user):raise Error('通行密钥不属于当前账户')
        handle=(credential.get('response') or {}).get('userHandle')
        stored=c.execute('SELECT handle FROM passkey_users WHERE user=?',(key['user'],)).fetchone()
        try:
            if (not user and not handle) or (handle and (not stored or decode(handle)!=stored[0])):raise ValueError('userHandle')
            result=verify_authentication_response(credential=credential,expected_challenge=row['challenge'],expected_rp_id=self.rp_id,expected_origin=self.origins,
                credential_public_key=key['public_key'],credential_current_sign_count=key['sign_count'],require_user_verification=True)
        except Exception as exc:raise Error('通行密钥验证失败，请重试或使用其他方式') from exc
        c.execute('UPDATE passkey_credentials SET sign_count=?,last_used=?,backed_up=? WHERE id=?',(result.new_sign_count,time.time(),int(result.credential_backed_up),key['id']))
        return key['user']
    def manage(self,c,user,data):
        if data.get('action')=='delete':
            changed=c.execute('DELETE FROM passkey_credentials WHERE user=? AND id=?',(user,data.get('id'))).rowcount
        elif data.get('action')=='rename':
            name=str(data.get('name') or '').strip()
            if not 1<=len(name)<=40:raise Error('名称须为 1～40 个字符')
            changed=c.execute('UPDATE passkey_credentials SET name=? WHERE user=? AND id=?',(name,user,data.get('id'))).rowcount
        else:raise Error('操作无效')
        if not changed:raise Error('通行密钥不存在或已移除')
        c.execute('DELETE FROM passkey_challenges WHERE user=?',(user,))
