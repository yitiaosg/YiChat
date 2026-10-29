import asyncio, base64, contextlib, hashlib, json, logging, os, re, secrets, time
from pathlib import Path
from urllib.parse import urlencode, quote
from contextlib import asynccontextmanager
import httpx, jwt
from cryptography.fernet import Fernet
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse, FileResponse, Response, StreamingResponse
from .config import Settings
from .db import connect, transaction, migrate
from .security import authenticate, valid_sid, check_origin, digest, fail, reserve_rate, upload_slot
from .matrix import Matrix
from .funds import Funds
from . import previews
from .limits import BodyLimitMiddleware
from . import policy, archive, direct, group_avatars,lifecycle,conversations
from .security import request_limit

log=logging.getLogger('yichat')

def create_app(settings=None):
    s=settings or Settings()
    @asynccontextmanager
    async def lifespan(app):
        migrate(s)
        policy.migrate_identities(s)
        if not s.session_key or not s.admin_token: raise RuntimeError('YICHAT_SESSION_KEY and YICHAT_SYNAPSE_ADMIN_TOKEN are required')
        app.state.cipher=Fernet(s.session_key.encode())
        app.state.http=httpx.AsyncClient(trust_env=False,follow_redirects=False)
        app.state.matrix=Matrix(s,app.state.http,app.state.cipher)
        app.state.funds=Funds(s)
        app.state.funds.migrate()
        from .assets import migrate as migrate_assets
        migrate_assets(s)
        from .push import FCMSender
        app.state.push=FCMSender(s,app.state.http)
        push_job=asyncio.create_task(app.state.push.run())
        job=asyncio.create_task(maintenance(app))
        yield
        push_job.cancel()
        with contextlib.suppress(asyncio.CancelledError): await push_job
        job.cancel()
        with contextlib.suppress(asyncio.CancelledError): await job
        await app.state.http.aclose()
    app=FastAPI(lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    app.state.settings=s
    app.add_middleware(BodyLimitMiddleware,settings=s)

    def session(request):
        token=request.headers.get('authorization','').removeprefix('Bearer ') or request.cookies.get('yc_session','')
        return authenticate(s,token)
    def room_info(room):return policy.room(s,room)
    def require_send(who,info):policy.require_send(s,info,who['mxid'])
    async def room_access(request,room):
        who=session(request); info=room_info(room)
        await app.state.matrix.membership(who,room)
        if info['kind']=='direct' and who['mxid'] not in (info['owner'],info['peer']):fail(403,'私聊身份不匹配')
        return who,info

    async def file_access(who,row):
        # Capture every membership transition allocated before this request,
        # then require the durable archive cursor to cover that fence. A lagging
        # replica must not mistake an old join for access across a missed leave.
        fence=await app.state.matrix.fence()
        with archive.connect(s.archive_reader) as c:
            c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            checkpoint=c.execute("SELECT sequence,healthy FROM checkpoints WHERE source='synapse'").fetchone()
            if not checkpoint or not checkpoint['healthy'] or checkpoint['sequence']<fence:
                raise HTTPException(503,{'errcode':'M_UNKNOWN','code':'YICHAT_HISTORY_PENDING','error':'历史权限证据正在同步，请稍后重试'},headers={'Retry-After':'1'})
            refs=c.execute('SELECT received_ms FROM attachment_references WHERE file=%s ORDER BY received_ms DESC',(row['id'],)).fetchall()
            if who['subject']==row['uploader']:
                return max([row['expires']]+[(r['received_ms']//86400000+31)*86400 for r in refs])
            for ref in refs:
                at=ref['received_ms']
                if archive.allowed(c,row['room'],who['mxid'],at,at+1):return (at//86400000+31)*86400
            fail(403,'没有此附件已接受消息的历史访问权限')

    def error_response(exc):
        body=exc.detail if isinstance(exc.detail,dict) else {'error':str(exc.detail),'errcode':'M_FORBIDDEN' if exc.status_code==403 else 'M_UNKNOWN_TOKEN' if exc.status_code==401 else 'M_UNKNOWN','code':'YICHAT_REQUEST_REJECTED'}
        return JSONResponse(body,status_code=exc.status_code,headers=exc.headers)
    @app.exception_handler(HTTPException)
    async def http_error(request,exc):return error_response(exc)

    @app.middleware('http')
    async def protection(request,call_next):
        token=''
        try:
            # The native exchange is a public PKCE endpoint, never cookie-authenticated.
            native_exchange=request.url.path=='/auth/native/exchange' and request.method=='POST'
            if native_exchange:
                if request.headers.get('origin') or request.headers.get('sec-fetch-mode') or request.cookies.get('yc_session'):fail(403,'原生交换不接受浏览器会话')
                if request.headers.get('content-type','').split(';')[0]!='application/json':fail(415,'需要 JSON 请求')
            else:check_origin(request,s)
            token=request.headers.get('authorization','').removeprefix('Bearer ') or request.cookies.get('yc_session','')
            if request.url.path.startswith(('/api/','/_matrix/','/auth/')) or request.url.path=='/health':
                host=request.client.host if request.client else 'unknown'
                if host in ('127.0.0.1','::1'):
                    host=request.headers.get('x-forwarded-for',host).split(',')[-1].strip()
                if token and not request.url.path.startswith('/auth/'):
                    who=None
                    try:who=authenticate(s,token)
                    except HTTPException as exc:
                        if exc.status_code==401:request_limit(s,'anonymous:'+host,limit=10)
                        if request.url.path!='/_matrix/client/versions' or exc.status_code!=401:raise
                    if who:
                        # The official FFI has no HTTP transport callback. Queue
                        # every request from native devices here, including Rust
                        # key queries, sync, media and business requests. This is
                        # admission pacing, not a claim of client-side transport
                        # interception. The global ten-per-second guard remains.
                        with connect(s.db) as c:
                            native_device=c.execute('SELECT 1 FROM native_devices WHERE subject=? AND device=? LIMIT 1',(who['subject'],who['device'])).fetchone()
                        if native_device:
                            await asyncio.sleep(reserve_rate(s,who['subject'],'native_requests',1,6))
                            authenticate(s,token)
                        request_limit(s,'user:'+who['subject'])
                else:
                    request_limit(s,'anonymous:'+host,limit=10)
            try:length=int(request.headers.get('content-length','0'))
            except ValueError:fail(400,'无效请求长度')
            is_upload=request.method=='POST' and (request.url.path.startswith('/api/files/') or request.url.path in ('/_matrix/media/v3/upload','/_matrix/client/v1/media/upload') or bool(re.fullmatch(r'/api/groups/[^/]+/avatar',request.url.path)))
            if not is_upload and length>262144: fail(413,'请求体过大')
            response=await call_next(request)
        except HTTPException as e:response=error_response(e)
        except Exception:
            log.error('YiChat dependency unavailable; request denied',exc_info=False)
            response=JSONResponse({'errcode':'M_UNKNOWN','code':'YICHAT_DEPENDENCY_UNAVAILABLE','error':'服务暂不可用，请稍后重试'},status_code=503)
        if response.status_code==429 and token and not request.url.path.startswith('/auth/'):
            # Both local admission errors and upstream 429s share a cooldown.
            try:
                who=authenticate(s,token)
                seconds=min(60,max(1,int(response.headers.get('Retry-After','1'))))
                with transaction(s.db) as c:
                    c.execute("INSERT INTO rate VALUES(?,?,?) ON CONFLICT(subject,bucket) DO UPDATE SET next=max(next,excluded.next)",(who['subject'],'native_requests',time.time()+seconds))
            except Exception:
                log.warning('Shared cooldown persistence unavailable',exc_info=False)
        response.headers.update({'Cache-Control':('public, max-age=31536000, immutable' if request.url.path.startswith('/assets/') else 'public, max-age=3600' if (request.url.path.startswith('/render/') or request.url.path=='/.well-known/assetlinks.json') else 'no-store'),'X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer','X-Frame-Options':'DENY','Content-Security-Policy':"default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; worker-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
        return response

    @app.get('/health')
    async def health():
        with archive.connect(s.archive_reader) as c:
            checkpoint=c.execute("SELECT healthy,observed_ms FROM checkpoints WHERE source='synapse'").fetchone()
            errors=c.execute("SELECT EXISTS(SELECT 1 FROM archive_gaps) OR EXISTS(SELECT 1 FROM archive_jobs WHERE status='failed') failed").fetchone()['failed']
        ready=bool(checkpoint and checkpoint['healthy'] and not errors and checkpoint['observed_ms']>=int(time.time()*1000)-120000)
        return JSONResponse({'service':'YiChat','status':'ok' if ready else 'degraded','message_epoch':s.message_epoch,'e2ee':False,'funds_enabled':app.state.funds.enabled()},status_code=200 if ready else 503)

    @app.get('/auth/login')
    async def login():
        state=secrets.token_urlsafe(32);verifier=secrets.token_urlsafe(48);nonce=secrets.token_urlsafe(32)
        with transaction(s.db) as c:
            c.execute('DELETE FROM logins WHERE expires<?',(time.time(),))
            c.execute('INSERT INTO logins VALUES(?,?,?,?)',(digest(state),verifier,nonce,time.time()+600))
        params=dict(client_id='yichat',response_type='code',scope='openid profile',redirect_uri=s.origin+'/auth/callback',state=state,nonce=nonce,code_challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('='),code_challenge_method='S256')
        r=RedirectResponse(s.issuer+'/oidc/authorize?'+urlencode(params));r.set_cookie('yc_login',state,secure=s.origin.startswith('https:'),httponly=True,samesite='lax',max_age=600)
        return r

    @app.get('/auth/callback')
    async def callback(request:Request):
        state=request.query_params.get('state','')
        if not state or not secrets.compare_digest(state,request.cookies.get('yc_login','')):fail(400,'登录状态校验失败')
        with transaction(s.db) as c:
            login=c.execute('SELECT * FROM logins WHERE state=? AND expires>?',(digest(state),time.time())).fetchone()
            native=c.execute('SELECT * FROM native_logins WHERE state=?',(digest(state),)).fetchone()
            c.execute('DELETE FROM logins WHERE state=?',(digest(state),))
        if not login:fail(400,'登录请求已过期或被使用')
        h=app.state.http
        try:
            result=await h.post(s.idp_internal+'/oidc/token',data=dict(client_id='yichat',grant_type='authorization_code',code=request.query_params.get('code',''),redirect_uri=s.origin+'/auth/callback',code_verifier=login['verifier']))
            result.raise_for_status()
            keys=await h.get(s.idp_internal+'/oidc/jwks');keys.raise_for_status()
            encoded=result.json()['id_token'];header=jwt.get_unverified_header(encoded)
            key=next(k for k in keys.json()['keys'] if k['kid']==header['kid'])
            claims=jwt.decode(encoded,jwt.PyJWK(key).key,algorithms=['RS256'],audience='yichat',issuer=s.issuer,options={'require':['exp','iat','sub','sid','nonce']})
            if claims['nonce']!=login['nonce']:raise ValueError()
            person=policy.identity(s,claims['preferred_username'])
            if claims['sub']!=person['uid']:raise ValueError()
            if not valid_sid(s,claims['sid'],person['username']):raise ValueError()
        except Exception:fail(401,'主站 OIDC 验证失败，请重新登录')
        if native:
            from .native_auth import finish_authorization
            return finish_authorization(s,native,claims,person)
        subject=claims['sub'];username=person['username'];mxid=policy.mxid(s,username)
        cipher=app.state.cipher
        with transaction(s.db) as c:
            c.execute('INSERT OR IGNORE INTO identities VALUES(?,?,?,?)',(subject,username,mxid,cipher.encrypt(secrets.token_urlsafe(48).encode()).decode()))
            identity=c.execute('SELECT * FROM identities WHERE subject=?',(subject,)).fetchone()
        password=cipher.decrypt(identity['password'].encode()).decode()
        m=app.state.matrix
        await m.provision(mxid,password,username)
        auth=await m.login(mxid,password)
        token=secrets.token_urlsafe(48)
        with transaction(s.db) as c:c.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,0,0)',(digest(token),subject,username,claims['sid'],mxid,auth['device_id'],cipher.encrypt(auth['access_token'].encode()).decode(),time.time()+30*86400))
        r=RedirectResponse('/');r.delete_cookie('yc_login');r.set_cookie('yc_session',token,secure=s.origin.startswith('https:'),httponly=True,samesite='strict',max_age=30*86400)
        return r

    from .native_auth import install as install_native_auth
    install_native_auth(app,s)

    @app.get('/api/session')
    async def me(request:Request):
        who=session(request)
        return {'uid':who['subject'],'user':who['username'],'mxid':who['mxid'],'device':who['device'],'token':request.headers.get('authorization','').removeprefix('Bearer ') or request.cookies.get('yc_session'),'expires':who['expires'],'user_id':who['mxid'],'device_id':who['device'],'home_server':s.server_name,'message_epoch':s.message_epoch,'e2ee':False,'funds_enabled':app.state.funds.enabled(),'upload_rate':s.upload_rate,'max_file':s.max_file,'packet_ttl':s.packet_ttl,'retention':{'days':30,'timezone':'GMT+0','basis':'utc_day_end','cleanup_requires_sealed_archive':True}}

    @app.post('/api/logout')
    async def logout(request:Request):
        who=session(request)
        with transaction(s.db) as c:c.execute('UPDATE sessions SET revoked=1 WHERE sid=?',(who['sid'],))
        # Gateway denial is immediate, even while Synapse is temporarily unavailable.
        await revoke_sessions(app)
        r=JSONResponse({'ok':True});r.delete_cookie('yc_session');return r

    @app.get('/api/users')
    async def users(request:Request,q:str=''):
        session(request)
        if not 1<=len(q)<=50:return []
        with connect(s.site/'account.db') as c:rows=c.execute('SELECT * FROM users WHERE username LIKE ? ESCAPE ? LIMIT 20',(q.replace('!','!!').replace('%','!%').replace('_','!_')+'%','!')).fetchall()
        return [{'username':r['username'],'nickname':r['nickname'],'mxid':policy.mxid(s,r['username'])} for r in rows if not dict(r).get('purged_at')]

    def public_profile(username):
        with connect(s.site/'account.db') as c:
            row=c.execute('SELECT * FROM users WHERE username=?',(username,)).fetchone()
        if not row:fail(404,'用户不存在')
        if dict(row).get('purged_at'):
            with connect(s.db) as c:old=c.execute('SELECT mxid FROM identities WHERE username=?',(username,)).fetchone()
            return {'username':username,'nickname':username,'mxid':old['mxid'] if old else '', 'biography':'','avatar_url':None,'profile_url':None,'deleted':True}
        row=dict(row)
        avatar=(s.site/'static/avatar'/f'{username}.jpg').resolve()
        has_avatar=avatar.is_relative_to((s.site/'static/avatar').resolve()) and avatar.is_file()
        return {'username':username,'mxid':policy.mxid(s,username),
                'nickname':row.get('nickname') or username,'biography':row.get('biography') or '',
                'profile_url':s.issuer+'/@'+quote(username,safe='')+'/',
                'avatar_url':'/api/avatars/'+quote(username,safe='')+'?v='+str(avatar.stat().st_mtime_ns) if has_avatar else None}

    @app.post('/api/profiles')
    async def profiles(request:Request):
        session(request);data=await request.json();ids=data.get('mxids',[]);names=data.get('usernames',[])
        if not isinstance(ids,list) or not isinstance(names,list) or len(ids)+len(names)>100 or any(not isinstance(x,str) for x in ids+names):fail(400,'最多查询 100 位用户')
        result=[]
        with connect(s.db) as c:
            for mxid in dict.fromkeys(ids):
                row=c.execute('SELECT subject,username FROM identities WHERE mxid=?',(mxid,)).fetchone()
                if row:
                    try:result.append(public_profile(row['username']))
                    except HTTPException as e:
                        if e.status_code!=404:raise
        for name in dict.fromkeys(names):
            try:
                profile=public_profile(name)
                if not any(r['mxid']==profile['mxid'] for r in result):result.append(profile)
            except HTTPException as e:
                if e.status_code!=404:raise
        return result

    @app.get('/api/people/{username}')
    async def person_view(request:Request,username:str):
        who=session(request);person=policy.identity(s,username)
        with policy.social.database(s.accounts) as c:result=policy.social.view(c,person['uid'],who['subject'])
        return dict(result,**public_profile(username))
    @app.post('/api/people/{username}/{action}')
    async def person_action(request:Request,username:str,action:str):
        who=session(request);person=policy.identity(s,username)
        try:return policy.social.mutate(s.accounts,who['subject'],person['uid'],action)
        except policy.social.PolicyError as e:fail(e.status,{'code':e.code,'error':str(e)})

    @app.get('/api/avatars/{username}')
    async def avatar(request:Request,username:str):
        session(request);profile=public_profile(username)
        if not profile['avatar_url']:fail(404,'未设置头像')
        return FileResponse((s.site/'static/avatar'/f'{username}.jpg').resolve(),media_type='image/jpeg')

    @app.get('/api/rooms')
    async def room_list(request:Request):
        who=session(request)
        joined=await app.state.matrix.call('GET','/_matrix/client/v3/joined_rooms',app.state.matrix.token(who))
        result=[]
        with connect(s.ledger) as ledger:past=ledger.execute("SELECT room,membership FROM yichat_memberships WHERE mxid=? AND event_id IS NOT NULL",(who['mxid'],)).fetchall()
        memberships={r['room']:r['membership'] for r in past}
        for rid in dict.fromkeys([*joined.get('joined_rooms',[]),*[r['room'] for r in past if r['membership']=='leave']]):
            try:
                if policy.retired(s,rid):continue
                info=room_info(rid)
                with connect(s.db) as db:
                    hidden=conversations.deleted(db,who['subject'],rid)
                    has_messages=bool(db.execute("SELECT 1 FROM history_receipts WHERE room=? AND type IN ('m.room.message','m.room.encrypted','m.room.redaction') LIMIT 1",(rid,)).fetchone())
                if not hidden and direct.visible(s,info,who['mxid']):result.append(dict(info,has_messages=has_messages,membership=memberships.get(rid,'join'),peer_deleted=info['kind']=='direct' and lifecycle.purged(s,info['peer'] if info['owner']==who['mxid'] else info['owner']),dissolved_at=lifecycle.status(s,rid)))
            except HTTPException as exc:
                if exc.status_code not in (403,404):raise
        return result

    @app.post('/api/rooms')
    async def create_room(request:Request):
        who=session(request);data=await request.json();kind=data.get('kind')
        if kind not in ('direct','group'):fail(400,'会话类型不正确')
        names=data.get('users',[])
        if not isinstance(names,list) or not 1<=len(names)<=50 or any(not isinstance(n,str) for n in names):fail(400,'请选择 1～50 位用户')
        names=list(dict.fromkeys(n for n in names if n!=who['username']))
        if kind=='direct' and len(names)!=1:fail(400,'私聊必须有且只有一位接收方')
        key=data.get('key')
        payload=json.dumps([kind,sorted(names),str(data.get('name') or (names[0] if kind=='direct' else '新群聊'))[:100]],ensure_ascii=False,separators=(',',':'))
        if key is not None and (not isinstance(key,str) or not re.fullmatch('[A-Za-z0-9_-]{16,100}',key)):fail(400,'无效创建请求键')
        if key:
            with connect(s.db) as db:old=db.execute('SELECT r.payload,t.room FROM room_requests r JOIN room_tickets t ON t.token=r.ticket WHERE r.subject=? AND r.request_key=?',(who['subject'],key)).fetchone()
            if old:
                if old['payload']!=payload:fail(409,'创建请求键已用于不同会话')
                if old['room']:
                    with connect(s.db) as db:retired=db.execute('SELECT 1 FROM retired_rooms WHERE room=?',(old['room'],)).fetchone()
                    if retired:fail(410,'此创建请求对应的测试会话已删除，请重新选择用户')
                    return await direct.open_existing(app,s,who,old['room']) if kind=='direct' else {'room_id':old['room']}
        if kind=='direct':
            target=policy.mxid(s,names[0]);first,second=direct.pair(who['mxid'],target)
            with connect(s.db) as db:existing=db.execute('SELECT room FROM direct_pairs WHERE first=? AND second=?',(first,second)).fetchone()
            if existing and existing['room']:return await direct.open_existing(app,s,who,existing['room'])
        invite=[];provisions=[]
        for name in names:
            with connect(s.site/'account.db') as c:exists=c.execute('SELECT 1 FROM users WHERE username=?',(name,)).fetchone()
            if not exists:fail(404,'站内用户不存在')
            person=policy.identity(s,name);mxid=policy.mxid(s,name)
            with transaction(s.db) as c:
                c.execute('INSERT OR IGNORE INTO identities VALUES(?,?,?,?)',(person['uid'],name,mxid,app.state.cipher.encrypt(secrets.token_urlsafe(48).encode()).decode()))
                identity=c.execute('SELECT * FROM identities WHERE subject=?',(person['uid'],)).fetchone()
            provisions.append((mxid,app.state.cipher.decrypt(identity['password'].encode()).decode(),name))
            invite.append(mxid)
        ticket,reused=direct.reserve_creation(s,who,kind,invite,key,payload)
        if reused:return await direct.open_existing(app,s,who,reused) if kind=='direct' else {'room_id':reused}
        try:
            for mxid,password,name in provisions:await app.state.matrix.provision(mxid,password,name)
        except Exception:
            # No room create has started. Retire this unused reservation so a
            # known provisioning failure cannot strand the pair for two minutes.
            with transaction(s.db) as db:db.execute('UPDATE room_tickets SET expires=? WHERE token=? AND room IS NULL',(time.time()-1,ticket))
            raise
        initial=[{'type':'m.room.history_visibility','state_key':'','content':{'history_visibility':'joined'}}]
        body={'preset':'private_chat','name':str(data.get('name') or (names[0] if kind=='direct' else '新群聊'))[:100],'is_direct':kind=='direct','invite':invite if kind=='group' else [],'room_version':'11','creation_content':{'m.federate':False,'org.yitiao.ticket':ticket},'initial_state':initial}
        result=await app.state.matrix.call('POST','/_matrix/client/v3/createRoom',app.state.matrix.token(who),body)
        policy.register_room(s,ticket,result['room_id'])
        return result

    @app.get('/api/room-creations/{key}')
    async def room_creation(request:Request,key:str):
        who=session(request)
        with connect(s.db) as db:
            row=db.execute('SELECT t.room,t.expires FROM room_requests r JOIN room_tickets t ON t.token=r.ticket WHERE r.subject=? AND r.request_key=?',(who['subject'],key)).fetchone()
        if not row:fail(404,'创建请求不存在')
        with connect(s.db) as db:retired=bool(row['room'] and db.execute('SELECT 1 FROM retired_rooms WHERE room=?',(row['room'],)).fetchone())
        return {'state':'deleted' if retired else 'created' if row['room'] else 'not_created' if row['expires']<time.time() else 'pending','room_id':None if retired else row['room'],'retry_after_ms':max(0,int((row['expires']-time.time())*1000))}

    @app.post('/api/rooms/{room}/invite')
    async def invite_user(request:Request,room:str):
        who,info=await room_access(request,room)
        if info['kind']!='group':fail(403,'只能在群聊邀请成员')
        with connect(s.ledger) as ledger:lifecycle.require_active(ledger,room)
        data=await request.json();name=str(data.get('username',''))
        with connect(s.site/'account.db') as c:exists=c.execute('SELECT 1 FROM users WHERE username=?',(name,)).fetchone()
        if not exists:fail(404,'站内用户不存在')
        # Synapse alone checks current invite permission, including room v12's
        # implicit creator power. A users-only precheck rejects valid creators.
        person=policy.identity(s,name);mxid=policy.mxid(s,name)
        with transaction(s.db) as c:
            c.execute('INSERT OR IGNORE INTO identities VALUES(?,?,?,?)',(person['uid'],name,mxid,app.state.cipher.encrypt(secrets.token_urlsafe(48).encode()).decode()))
            identity=c.execute('SELECT * FROM identities WHERE subject=?',(person['uid'],)).fetchone()
        await app.state.matrix.provision(mxid,app.state.cipher.decrypt(identity['password'].encode()).decode(),name)
        return await app.state.matrix.call('POST','/_matrix/client/v3/rooms/'+quote(room,safe='')+'/invite',app.state.matrix.token(who),{'user_id':mxid})

    @app.get('/api/rooms/{room}')
    async def room_get(request:Request,room:str):
        _,info=await room_access(request,room);return info

    @app.api_route('/api/native/rooms/{room}/transactions/{txn}',methods=['GET','POST'])
    async def native_transaction(request:Request,room:str,txn:str):
        who,info=await room_access(request,room)
        if not re.fullmatch(r'[A-Za-z0-9_.~-]{1,128}',txn):fail(400,'无效消息事务')
        with connect(s.db) as c:
            if not c.execute('SELECT 1 FROM native_devices WHERE subject=? AND device=?',(who['subject'],who['device'])).fetchone():fail(403,'需要原生设备会话')
        if request.method=='POST':
            require_send(who,info);data=await request.json()
            if not isinstance(data,dict) or set(data)-{'attachments','business'}:fail(400,'只能登记附件或业务引用')
            ids=data.get('attachments',[])
            if not isinstance(ids,list) or len(ids)>100 or any(not isinstance(i,str) or not re.fullmatch(r'[a-f0-9]{48}',i) for i in ids):fail(400,'附件ID无效')
            if data.get('business') is not None:
                from .history import business_reference
                business_reference(s,room,who['mxid'],{'org.yitiao.business':data['business']})
            with connect(s.db) as c:
                for fid in ids:
                    if not c.execute('SELECT 1 FROM files WHERE id=? AND room=? AND uploader=? AND removed=0 AND expires>?',(fid,room,who['subject'],time.time())).fetchone():fail(403,'附件不属于本人的此会话上传')
            metadata=json.dumps(data,sort_keys=True,separators=(',',':'))
            with transaction(s.db) as c:
                old=c.execute('SELECT metadata FROM native_transactions WHERE subject=? AND device=? AND room=? AND txn=?',(who['subject'],who['device'],room,txn)).fetchone()
                if old and old['metadata']!=metadata:fail(409,'消息事务已绑定其他元数据')
                c.execute('INSERT OR IGNORE INTO native_transactions VALUES(?,?,?,?,?,NULL)',(who['subject'],who['device'],room,txn,metadata))
        with connect(s.db) as c:
            record=c.execute('SELECT event FROM native_transactions WHERE subject=? AND device=? AND room=? AND txn=?',(who['subject'],who['device'],room,txn)).fetchone()
        if not record:fail(404,'未登记此消息事务')
        return {'txn':txn,'event_id':record['event'],'state':'accepted' if record['event'] else 'registered'}

    from .uploads import install as install_uploads
    install_uploads(app,s,session,room_access,require_send)

    @app.post('/api/files/{room}')
    async def upload(request:Request,room:str):
        who,info=await room_access(request,room);require_send(who,info)
        try:size=int(request.headers.get('content-length','0'))
        except ValueError:fail(400,'无效文件长度')
        if size<0 or size>s.max_file:fail(413,'文件大小超限')
        fid=secrets.token_hex(24);await upload_slot(s,who['subject'],fid,size or s.max_file)
        archive.reserve_upload(s.archive_ingest,fid)
        target=s.objects/(fid+'.part');total=0;file_hash=hashlib.sha256()
        try:
            # Starlette/ASGI transport is bounded by Uvicorn high-water mark. Reserve
            # each read quantum globally BEFORE requesting the next transport chunk.
            iterator=request.stream().__aiter__()
            with target.open('xb') as f:
                while True:
                    delay=reserve_rate(s,who['subject'],'upload',min(65536,size-total) if size else 65536,s.upload_rate)
                    await asyncio.sleep(delay)
                    authenticate(s,request.headers.get('authorization','').removeprefix('Bearer ') or request.cookies.get('yc_session',''))
                    try: chunk=await asyncio.wait_for(iterator.__anext__(),30)
                    except StopAsyncIteration:break
                    if not chunk:break
                    if len(chunk)>65536:
                        await asyncio.sleep(reserve_rate(s,who['subject'],'upload',len(chunk)-65536,s.upload_rate))
                    total+=len(chunk)
                    if total>s.max_file or (size and total>size):fail(413,'上传数据超过上限或声明长度')
                    require_send(who,info)
                    f.write(chunk);file_hash.update(chunk)
                    with transaction(s.db) as c:c.execute('UPDATE upload_leases SET expires=? WHERE id=?',(time.time()+60,fid))
                if size and total!=size:fail(400,'上传不完整')
                f.flush();os.fsync(f.fileno())
            await app.state.matrix.membership(who,room)
            require_send(who,info)
            completed=time.time();expires=(int(completed//86400)+1+30)*86400
            target.rename(s.objects/fid)
            directory=os.open(s.objects,os.O_RDONLY);os.fsync(directory);os.close(directory)
            with transaction(s.db) as c:
                c.execute('INSERT INTO files VALUES(?,?,?,?,?,?,0)',(fid,room,who['subject'],total,completed,expires))
                c.execute('INSERT INTO file_hashes VALUES(?,?)',(fid,file_hash.hexdigest()))
            # PG receipt derives from bytes actually streamed by the authenticated server.
            # A crash before this commit leaves a recoverable file receipt for maintenance.
            archive.record_attachment(s.archive_ingest,fid,room,who['mxid'],int(completed*1000),total,file_hash.hexdigest())
            return {'id':fid,'url':'mxc://'+s.server_name+'/'+fid,'expires':expires,'bytes':total}
        finally:
            target.unlink(missing_ok=True)
            if not (s.objects/fid).exists():archive.release_upload(s.archive_ingest,fid)
            with transaction(s.db) as c:c.execute('DELETE FROM upload_leases WHERE id=?',(fid,))

    @app.get('/api/files/{fid}/meta')
    async def filemeta(request:Request,fid:str):
        with connect(s.db) as c:row=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
        if not row:fail(404,'附件不存在')
        who=session(request)
        expiry=await file_access(who,row)
        with connect(s.db) as c:receipt=c.execute('SELECT sha256 FROM file_hashes WHERE id=?',(fid,)).fetchone()
        return dict(row)|{'expires':expiry,'expired':expiry<=time.time(),'sha256':receipt['sha256'] if receipt else None}

    @app.get('/api/files/{fid}/content')
    async def download(request:Request,fid:str):
        with connect(s.db) as c:row=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
        if not row:fail(404,'附件不存在')
        who=session(request)
        expiry=await file_access(who,row)
        if expiry<=time.time() or row['removed']:fail(410,'服务器附件已到期；可从已保存的本地归档打开')
        if not (s.objects/fid).is_file():fail(503,'附件实体缺失，下载和清理已暂停')
        handle=(s.objects/fid).open('rb')
        async def stream():
            with handle as f:
                while True:
                    session(request)
                    if expiry<=time.time():return
                    chunk=f.read(65536)
                    if not chunk:return
                    yield chunk
                    await asyncio.sleep(0)
        return StreamingResponse(stream(),media_type='application/octet-stream',headers={'Content-Disposition':'attachment','Content-Length':str(row['bytes'])})

    @app.get('/api/wallet')
    async def wallet(request:Request):
        who=session(request)
        with connect(s.ledger) as c:b=app.state.funds.y.binding(c,who['username'],'yichat')
        with connect(s.db) as c:native=bool(c.execute('SELECT 1 FROM native_devices WHERE subject=? AND device=? LIMIT 1',(who['subject'],who['device'])).fetchone())
        return {'enabled':app.state.funds.enabled(),'binding':b['address'] if b else None,'bind_url':'https://yidt.yitiao.org/services/yichat/'+('?client=android' if native else ''),'fees':{'incoming':app.state.funds.y.fmt(app.state.funds.y.GAS),'outgoing_per_share':app.state.funds.y.fmt(app.state.funds.y.GAS)}}

    @app.post('/api/orders')
    async def order_create(request:Request):
        data=await request.json();who,info=await room_access(request,str(data.get('room','')));require_send(who,info)
        recipient=None
        if info['kind']=='direct':
            target=info['peer'] if who['mxid']==info['owner'] else info['owner']
            with connect(s.db) as c:r=c.execute('SELECT subject FROM identities WHERE mxid=?',(target,)).fetchone()
            if not r:fail(409,'接收用户尚不可用')
            recipient=policy.username(s,r['subject'])
        with connect(s.db) as c:
            native=bool(c.execute('SELECT 1 FROM native_devices WHERE subject=? AND device=? LIMIT 1',(who['subject'],who['device'])).fetchone())
        return app.state.funds.create(who,info,data,recipient,native_return=native)

    async def order_access(request,oid):
        with connect(s.ledger) as c:o=c.execute('SELECT * FROM yichat_orders WHERE id=?',(oid,)).fetchone()
        if not o:fail(404,'订单不存在')
        who=session(request)
        if policy.retired(s,o['room']):
            # Chat history deletion never removes access to one's wallet receipt.
            # Retired orders are read-only and authenticated by immutable UID.
            with connect(s.ledger) as ledger:
                participants=ledger.execute('SELECT sender_uid,recipient_uid FROM yichat_order_participants WHERE order_id=?',(oid,)).fetchone()
                claimed=ledger.execute('SELECT 1 FROM yichat_payout_identities WHERE order_id=? AND uid=?',(oid,who['subject'])).fetchone()
            if not participants or (who['subject'] not in (participants['sender_uid'],participants['recipient_uid']) and not claimed):fail(403,'没有此历史资金订单的权限')
            return who,o
        conversations.accessible(s,who,o['room'])
        with connect(s.ledger) as ledger:member=ledger.execute("SELECT 1 FROM yichat_memberships WHERE room=? AND mxid=? AND membership='join' AND pending_event IS NULL",(o['room'],who['mxid'])).fetchone()
        if not member:
            fence=await app.state.matrix.fence()
            with archive.connect(s.archive_reader) as pg:
                checkpoint=pg.execute("SELECT sequence,healthy FROM checkpoints WHERE source='synapse'").fetchone()
                if not checkpoint or not checkpoint['healthy'] or checkpoint['sequence']<fence:fail(503,'历史权限存证正在同步，请稍后重试')
                at=int(o['created']*1000)
                if not archive.allowed(pg,o['room'],who['mxid'],at,at+1):fail(403,'没有此订单创建时的历史访问权限')
        return who,o

    @app.get('/api/orders/{oid}')
    async def order_get(request:Request,oid:str):
        who,o=await order_access(request,oid)
        with connect(s.ledger) as c:return app.state.funds.public(c,o,who['username'])

    @app.post('/api/orders/{oid}/cancel')
    async def order_cancel(request:Request,oid:str):
        who,_=await order_access(request,oid)
        return app.state.funds.cancel(who,oid)

    @app.post('/api/orders/{oid}/fund')
    async def order_fund(request:Request,oid:str):
        who,o=await order_access(request,oid);require_send(who,room_info(o['room']))
        return app.state.funds.fund(who,oid)

    @app.post('/api/orders/{oid}/claim')
    async def order_claim(request:Request,oid:str):
        who,o=await order_access(request,oid);data=await request.json()
        if data.get('room')!=o['room']:fail(403,'请在红包所属群聊中领取')
        return app.state.funds.claim(who,oid,data['room'])

    @app.post('/api/orders/{oid}/receive')
    async def order_receive(request:Request,oid:str):
        who,o=await order_access(request,oid);data=await request.json()
        if data.get('room')!=o['room']:fail(403,'请在转账所属私聊中确认收款')
        return app.state.funds.receive(who,oid,data['room'])

    @app.post('/api/preview')
    async def preview(request:Request):
        who=session(request);data=await request.json();url=data.get('url','')
        if not isinstance(url,str) or len(url)>2048:fail(400,'仅提交必要的预览 URL')
        if data.get('room'):
            policy.room(s,data['room'])
            conversations.accessible(s,who,data['room'])
        parsed=previews.parse(url)
        if parsed and parsed[0]=='order':
            _,o=await order_access(request,parsed[1][0])
            if data.get('room')!=o['room']:fail(403,'资金卡片不属于当前会话')
            with connect(s.ledger) as c:return {'type':'order','url':url,'data':app.state.funds.public(c,o,who['username'])}
        return await asyncio.to_thread(previews.metadata,s,url)

    @app.api_route('/_matrix/{path:path}',methods=['GET','POST','PUT','DELETE'])
    async def proxy(request:Request,path:str):
        if '%' in path or '\\' in path or '..' in path:fail(400,'无效路径')
        if path=='client/versions' and request.method=='GET':
            result=await app.state.http.get(s.synapse+'/_matrix/client/versions')
            return Response(result.content,status_code=result.status_code,media_type='application/json')
        who=session(request);m=app.state.matrix
        if request.method=='POST' and path in ('media/v3/upload','client/v1/media/upload'):
            room=request.query_params.get('room_id') or request.headers.get('x-yichat-room')
            if not room:fail(403,'原生上传需要 room_id 或 X-YiChat-Room，以校验所属会话和隐私权限')
            result=await upload(request,room)
            return dict(result,content_uri=result['url'])
        avatar_match=re.fullmatch(r'(?:media/v3|client/v1/media)/download/chat\.yitiao\.org/ycga_([a-f0-9]{64})(?:/[^/]+)?',path)
        if request.method=='GET' and avatar_match:
            who=session(request)
            with connect(s.db) as c:r=c.execute('SELECT room FROM group_avatar_refs WHERE id=?',(avatar_match[1],)).fetchone()
            if not r:fail(404,'群头像已更换')
            return group_avatars.read(s,who,r['room'],avatar_match[1])
        media_match=re.fullmatch(r'(?:media/v3|client/v1/media)/download/chat\.yitiao\.org/([a-f0-9]{48})(?:/[^/]+)?',path)
        if request.method=='GET' and media_match:return await download(request,media_match[1])
        if request.method=='GET' and path in ('media/v3/config','client/v1/media/config'):
            return {'m.upload.size':s.max_file}
        if path.startswith('client/v3/sendToDevice/'):
            typ=path.split('/')[3]
            if typ in ('m.room_key','m.forwarded_room_key','m.secret.send'):fail(403,'密钥材料必须使用 Matrix 加密的设备间传输')
        # Deny by default: no native media, registration, login, federation or admin.
        allowed=r'client/(?:v3|r0|unstable)/(?:sync|versions|capabilities|account/whoami|keys/(?:upload|query|claim|changes|signatures/upload|device_signing/upload)|room_keys(?:/.*)?|sendToDevice/.*|devices(?:/[^/]+)?|user/[^/]+/(?:account_data/.*|filter(?:/[^/]+)?)|profile/[^/]+(?:/displayname)?|rooms/[^/]+/.*|join/[^/]+|pushrules/.*)'
        if not re.fullmatch(allowed,path) and path!='client/versions':fail(403,'此 Matrix 入口未开放')
        profile_match=re.fullmatch(r'client/(?:v3|r0)/profile/([^/]+)(?:/(displayname|avatar_url))?',path)
        if profile_match and request.method=='GET' and lifecycle.purged(s,profile_match[1]):
            with connect(s.db) as db:retired=db.execute('SELECT username FROM identities WHERE mxid=?',(profile_match[1],)).fetchone()
            profile={'displayname':retired['username'],'avatar_url':''}
            return {profile_match[2]:profile[profile_match[2]]} if profile_match[2] else profile
        body=await request.body()
        if len(body)>262144:fail(413,'请求体过大')
        to_device=re.fullmatch(r'client/(?:v3|r0|unstable)/sendToDevice/([^/]+)/[^/]+',path)
        if to_device and request.method=='PUT':policy.validate_to_device(s,who['mxid'],to_device[1],json.loads(body))
        room_match=re.match(r'client/(?:v3|r0)/rooms/([^/]+)/(.*)',path)
        if room_match:
            room,action=room_match.groups();info=room_info(room)
            if request.method!='GET':
                if info['kind']=='group' and action not in ('receipt/m.read','read_markers'):
                    with connect(s.ledger) as ledger:lifecycle.require_active(ledger,room)
                if info['kind']=='direct' and action=='leave':fail(403,'私聊不支持离开会话，请使用归档')
                data=json.loads(body) if body else {}
                if action.startswith('send/'):
                    parts=action.split('/')
                    if len(parts)!=3:fail(400,'无效事件路径')
                    with connect(s.db) as c:
                        native_meta=c.execute('SELECT metadata FROM native_transactions WHERE subject=? AND device=? AND room=? AND txn=?',(who['subject'],who['device'],room,parts[2])).fetchone()
                    if native_meta:
                        metadata=json.loads(native_meta['metadata'])
                        if metadata.get('attachments'):data[archive.ATTACHMENT_KEY]=metadata['attachments']
                        if metadata.get('business'):data['org.yitiao.business']=metadata['business']
                        body=json.dumps(data,ensure_ascii=False,separators=(',',':')).encode()
                    declared=request.headers.get('x-yichat-attachments')
                    if declared is not None:
                        try:data[archive.ATTACHMENT_KEY]=json.loads(declared)
                        except (ValueError,TypeError):fail(400,'无效附件声明')
                        body=json.dumps(data,ensure_ascii=False,separators=(',',':')).encode()
                    policy.validate_event(s,info,parts[1],data,who['mxid'])
                elif action.startswith('state/'):
                    parts=action.split('/')
                    if parts[1] not in ('m.room.name','m.room.topic','m.room.power_levels','m.room.avatar'):fail(403,'不可修改会话加密或可见性策略')
                    if info['kind']=='direct':fail(403,'私聊策略不可修改')
                elif action=='invite':
                    if info['kind']!='group':fail(403,'私聊参与者固定')
                    policy.subject(s,data.get('user_id',''))
                elif action.startswith('redact/'):
                    require_send(who,info)
                elif info['kind']=='direct' and action.split('/')[0] in ('kick','ban','unban'):
                    fail(403,'私聊参与者固定')
        params=dict(request.query_params);params.pop('access_token',None)
        if path.endswith('/sync'):
            params['timeout']=str(min(30000,max(0,int(params.get('timeout','30000'))))) # Recheck sid between short sync requests.
        r=await app.state.http.request(request.method,s.synapse+'/_matrix/'+quote(path,safe='/!:@'),content=body,params=params,headers={'Authorization':'Bearer '+m.token(who),'Content-Type':'application/json'},timeout=40)
        # Backend supplies Matrix account authentication for cross-signing public-key
        # upload. E2EE private keys are never submitted to this service.
        if path.endswith('/keys/device_signing/upload') and r.status_code==401:
            challenge=r.json()
            with connect(s.db) as c:i=c.execute('SELECT password FROM identities WHERE subject=?',(who['subject'],)).fetchone()
            data=json.loads(body);data['auth']={'type':'m.login.password','identifier':{'type':'m.id.user','user':who['mxid']},'password':app.state.cipher.decrypt(i['password'].encode()).decode(),'session':challenge.get('session')}
            r=await app.state.http.post(s.synapse+'/_matrix/'+path,json=data,headers={'Authorization':'Bearer '+m.token(who)})
        session(request) # Discard results if main-site logout occurred during upstream wait.
        sent=re.fullmatch(r'client/(?:v3|r0)/rooms/([^/]+)/send/[^/]+/([^/]+)',path)
        if sent and request.method=='PUT' and r.is_success:
            eid=r.json().get('event_id')
            if eid:
                with transaction(s.db) as c:c.execute('UPDATE native_transactions SET event=? WHERE subject=? AND device=? AND room=? AND txn=? AND (event IS NULL OR event=?)',(eid,who['subject'],who['device'],sent[1],sent[2],eid))
        if path.endswith('/sync') and r.is_success:
            session(request) # Recheck a main-site revocation during the long poll.
            return JSONResponse(await app.state.filter_direct_sync(who,r.json()))
        return Response(r.content,status_code=r.status_code,media_type='application/json',headers={'Retry-After':r.headers['Retry-After']} if 'Retry-After' in r.headers else None)

    from .groups import install as install_groups
    install_groups(app,s,session,room_access)
    direct.install(app,s,session)
    conversations.install(app,s,session)
    from . import push
    push.install(app,s,session)
    group_avatars.install(app,s,session)
    from .record_audit import install as install_record_audit
    install_record_audit(app,s,session)

    from .archive_routes import install as install_archives
    install_archives(app,s,session)
    from .assets import install as install_assets
    install_assets(app,s,session,room_access,require_send)

    from .history import install as install_history
    install_history(app,s,session)

    webroot=Path(os.getenv('YICHAT_WEB_ROOT',str(Path(__file__).parent.parent/'client'/'dist')))
    from .card_reads import install as install_card_reads
    install_card_reads(app,s,session)

    @app.get('/')
    async def landing():
        target=webroot/'index.html'
        return FileResponse(target if target.is_file() else Path(__file__).parent.parent/'web'/'index.html')
    @app.get('/assets/{name}')
    @app.get('/render/{name}')
    @app.get('/brand/{name}')
    async def web_asset(request:Request,name:str):
        folder='brand' if request.url.path.startswith('/brand/') else 'render' if request.url.path.startswith('/render/') else 'assets'
        target=(webroot/folder/name).resolve()
        if not target.is_relative_to((webroot/folder).resolve()) or not target.is_file():fail(404,'资源不存在')
        return FileResponse(target)

    @app.get('/.well-known/matrix/client')
    async def discovery():return {'m.homeserver':{'base_url':s.origin}}
    @app.get('/sw.js')
    async def retire_worker():
        return FileResponse(Path(__file__).parent.parent/'web'/'sw-retire.js',media_type='application/javascript',headers={'Service-Worker-Allowed':'/'})
    @app.get('/gifts/{gid}')
    async def gift_link(gid:str):
        if not re.fullmatch('[a-f0-9]{32}',gid):fail(404,'赠卡链接不存在')
        return RedirectResponse('/?gift='+gid)

    @app.get('/orders/{oid}')
    async def order_page(oid:str):
        if not re.fullmatch('[A-Za-z0-9_-]{32}',oid):fail(404,'订单不存在')
        # Existing YIDT intents retain this return URL. Resolve the room through
        # the authenticated API in the client; visiting it never funds an order.
        return RedirectResponse('/?order='+oid,status_code=303)
    @app.get('/tools/{name}')
    async def local_tool(name:str):
        if name not in ('verify_archive.py',):fail(404,'工具不存在')
        return FileResponse(Path(__file__).parent.parent/'tools'/name,media_type='text/plain')
    @app.get('/static/{name}')
    async def static(name:str):
        if name not in ('archive.js','order.js'):fail(404,'资源不存在')
        return FileResponse(Path(__file__).parent.parent/'web'/name)

    return app

async def revoke_sessions(app):
    s=app.state.settings
    with connect(s.db) as c:rows=c.execute('SELECT * FROM sessions WHERE cleaned=0').fetchall()
    for row in rows:
        if row['revoked'] or row['expires']<=time.time() or not valid_sid(s,row['sid'],row['username']):
            with transaction(s.db) as c:c.execute('UPDATE sessions SET revoked=1 WHERE token_hash=?',(row['token_hash'],))
            # The Synapse module revokes only this token. Matrix /logout deletes
            # the device and its keys, which is inappropriate for main-site SSO exit.

async def maintenance(app):
    s=app.state.settings;last_audit=0
    while True:
        try:
            await revoke_sessions(app)
            from .uploads import reconcile as reconcile_uploads
            await asyncio.to_thread(reconcile_uploads,s)
            # Evidence-first retention runs in the isolated archive worker. The API
            # process cannot update sealed digests and never deletes an expired file.
            if s.archive_ingest:
                with connect(s.db) as c:
                    receipts=c.execute('SELECT f.*,h.sha256,i.mxid FROM files f JOIN file_hashes h USING(id) JOIN identities i ON i.subject=f.uploader WHERE f.removed=0').fetchall()
                for row in receipts:
                    await asyncio.to_thread(archive.record_attachment,s.archive_ingest,row['id'],row['room'],row['mxid'],int(row['completed']*1000),row['bytes'],row['sha256'])
            for file in s.objects.glob('*.part'):
                if time.time()-file.stat().st_mtime>3600:
                    with connect(s.db) as c:
                        live=c.execute("SELECT 1 FROM upload_leases WHERE id=? AND expires>? UNION ALL SELECT 1 FROM file_uploads WHERE id=? AND state IN ('uploading','finalizing')",(file.stem,time.time(),file.stem)).fetchone()
                    if not live:file.unlink(missing_ok=True)
            if app.state.funds.enabled() and time.time()-last_audit>300:
                await asyncio.to_thread(app.state.funds.reconcile);last_audit=time.time()
            await asyncio.to_thread(app.state.funds.tick)
        except asyncio.CancelledError:raise
        except Exception:log.exception('YiChat 后台任务失败')
        await asyncio.sleep(1)

app=create_app()
