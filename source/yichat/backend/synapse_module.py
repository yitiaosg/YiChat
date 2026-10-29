"""Synapse 1.163 module. No core edits or internal-table writes.

The polling recovery path uses a pinned, read-only source adapter because
on_new_event exceptions are logged and swallowed by Synapse. A callback alone
cannot be a durable archive. The global allocated/persisted stream fences include
unfinished transactions across event writers, rather than MAX(events.id).
"""
import json
import logging
import secrets
import time
from datetime import datetime, timezone, timedelta
from fastapi import HTTPException
from synapse.logging.context import defer_to_thread
from twisted.internet import reactor
from synapse.api.errors import SynapseError
from synapse.util.frozenutils import unfreeze
from synapse.http.server import DirectServeJsonResource
from .config import Settings
from .db import connect,transaction
from . import policy, archive, history, groups

log=logging.getLogger('yichat.module')


class PolicySynapseError(SynapseError):
    def __init__(self,status,message,errcode,business_code):
        super().__init__(status,message,errcode=errcode)
        self.business_code=business_code
    def error_dict(self,config=None):
        # Synapse cs_error's parameter is itself named "code"; passing this
        # through additional_fields collides with its positional argument.
        result=super().error_dict(config);result['code']=self.business_code
        return result


class IngestionFenceResource(DirectServeJsonResource):
    def __init__(self,module):
        super().__init__(clock=module.api._hs.get_clock())
        self.module=module
    async def _async_render_GET(self,request):
        import hmac
        auth=request.getHeader('Authorization') or ''
        if not self.module.s.admin_token or not hmac.compare_digest(auth,'Bearer '+self.module.s.admin_token):
            raise SynapseError(403,'Internal fence requires service authentication')
        if not self.module.ready:raise SynapseError(503,'Source recovery is not ready')
        sequence=await self.module.api._store.get_events_stream_id_generator().get_max_allocated_token()
        return 200,{'sequence':sequence}


class YiChatModule:
    @staticmethod
    def parse_config(config):return config

    def __init__(self,config,module_api):
        self.api=module_api
        self.s=Settings(**config)
        self.busy=False
        self.ready=False
        self.boot=secrets.token_hex(16)
        self.instance=module_api._hs.get_instance_name()
        self.initialized_rooms=set()
        module_api.register_web_resource('/_synapse/client/yichat/fence',IngestionFenceResource(self))
        with transaction(self.s.ledger) as c:
            c.execute('INSERT INTO yichat_membership_workers VALUES(?,?) ON CONFLICT(instance) DO UPDATE SET boot=excluded.boot',(self.instance,self.boot))
        module_api.register_third_party_rules_callbacks(
            on_create_room=self.on_create_room,check_event_allowed=self.check_event_allowed,
            on_new_event=self.on_new_event,check_threepid_can_be_invited=self.deny_thirdparty,
            check_visibility_can_be_modified=self.check_visibility)
        module_api.looping_background_call(self.sweep,1000,desc='yichat_archive_ingest',run_on_all_instances=True)

    async def deny_thirdparty(self,*args):return False
    async def check_visibility(self,room,state,visibility):return visibility=='private'

    async def on_create_room(self,requester,config,is_admin):
        try:
            if not self.ready:raise HTTPException(503,'会话服务正在恢复，请稍后重试')
            token=config.get('creation_content',{}).get('org.yitiao.ticket','')
            t=policy.validate_ticket(self.s,token,requester.user.to_string())
            expected=[] if t['kind']=='direct' else config.get('invite',[])
            if sorted(config.get('invite',[]))!=sorted(expected):raise HTTPException(403,'私聊参与者与服务器记录不符')
            config['visibility']='private';config['creation_content']['m.federate']=False
            initial=config.setdefault('initial_state',[])
            if not any(e['type']=='m.room.guest_access' for e in initial):
                initial.append({'type':'m.room.guest_access','state_key':'','content':{'guest_access':'forbidden'}})
            if any(e['type']=='m.room.encryption' for e in initial):raise HTTPException(403,'聊天已停用端到端加密')
        except HTTPException as e:raise self.convert(e)

    @staticmethod
    def convert(exc):
        body=exc.detail if isinstance(exc.detail,dict) else {'error':exc.detail}
        return PolicySynapseError(exc.status_code,body.get('error','请求被拒绝'),body.get('errcode','M_FORBIDDEN'),body.get('code','YICHAT_POLICY_REJECTED'))

    async def check_event_allowed(self,event,state):
        try:
            if not self.ready:raise HTTPException(503,'会话服务正在恢复，请稍后重试')
            if policy.retired(self.s,event.room_id):
                # Operator-marked retired rooms only admit self-leave events for
                # the official Synapse shutdown API. No content or asset action.
                if event.type=='m.room.member' and event.sender==event.state_key and event.content.get('membership')=='leave':return True,None
                raise HTTPException(410,'此旧会话已清空，请打开新会话')
            # A stable, live main-site grant is required even for a stolen raw token.
            control=groups.admitted(self.s,event.room_id,event.sender,event.type,unfreeze(event.content),event.state_key if event.is_state() else '')
            with connect(self.s.db) as c:
                sessions=c.execute('SELECT username,sid FROM sessions WHERE mxid=? AND revoked=0 AND expires>strftime(\'%s\',\'now\')',(event.sender,)).fetchall()
            from .security import valid_sid
            from . import lifecycle
            retirement=event.type=='m.room.member' and event.sender==event.state_key and event.content.get('membership')=='leave' and lifecycle.purged(self.s,event.sender)
            if not control and not retirement and lifecycle.purged(self.s,event.sender):raise HTTPException(401,'账户已注销')
            if not control and not retirement and not any(valid_sid(self.s,r['sid'],r['username']) for r in sessions):
                raise HTTPException(401,'主站会话已退出')
            if event.type=='m.room.create':
                token=event.content.get('org.yitiao.ticket','')
                ticket=policy.validate_ticket(self.s,token,event.sender)
                if event.content.get('m.federate') is not False:raise HTTPException(403,'外部联邦已关闭')
                policy.register_room(self.s,token,event.room_id)
            else:
                info=policy.room(self.s,event.room_id)
                if event.type=='m.room.member':
                    policy.subject(self.s,event.state_key)
                elif event.is_state():
                    permitted={'m.room.encryption','m.room.history_visibility','m.room.join_rules','m.room.power_levels','m.room.name','m.room.topic','m.room.avatar','m.room.canonical_alias','m.room.guest_access'}
                    if event.type not in permitted:raise HTTPException(403,'此状态事件未开放')
                    if event.type=='m.room.join_rules' and event.content.get('join_rule')!='invite':raise HTTPException(403,'只允许邀请加入')
                    if info['kind']=='direct' and (event.type,event.state_key) in state and event.type in ('m.room.encryption','m.room.power_levels','m.room.join_rules','m.room.history_visibility','m.room.guest_access'):
                        raise HTTPException(403,'私聊加密和成员策略固定')
                if info['kind']=='group' and event.is_state() and event.type in ('m.room.power_levels','m.room.name','m.room.topic','m.room.avatar') and not control:
                    with connect(self.s.db) as c:rank=groups.role(c,event.room_id,event.sender)
                    if event.type=='m.room.avatar' or (event.type=='m.room.power_levels' and ('m.room.power_levels','') in state) or rank not in ('owner','admin'):raise HTTPException(403,'请通过群管理接口修改权限或群资料')
                policy.validate_event(self.s,info,event.type,unfreeze(event.content),event.sender,event.state_key if event.is_state() else None,control)
                if event.type in ('m.room.message','m.room.encrypted'):history.business_reference(self.s,event.room_id,event.sender,unfreeze(event.content))
                if event.type=='m.room.redaction':await self.validate_redaction(event)
            # Archive outages stop new accepted timeline events; poll also repairs
            # the crash gap after persistence and before a notification callback.
            await defer_to_thread(reactor,self.check_archive)
            policy.admit_attachments(self.s,event.event_id,event.room_id,event.sender,event.type,unfreeze(event.content),self.instance,self.boot)
            if event.type=='m.room.member':
                previous=state.get(('m.room.member',event.state_key))
                # Synapse builds profile updates before deduplicating unchanged
                # state. Such candidates may never persist or call on_new_event.
                # Only membership changes need the financial admission barrier;
                # profile-only candidates must not overwrite another transition.
                if previous is None or previous.content.get('membership')!=event.content.get('membership'):
                    with transaction(self.s.ledger) as c:
                        c.execute("INSERT INTO yichat_memberships VALUES(?,?,'unknown',NULL,?,?,?) ON CONFLICT(room,mxid) DO UPDATE SET pending_event=excluded.pending_event,pending_worker=excluded.pending_worker,pending_boot=excluded.pending_boot",
                            (event.room_id,event.state_key,event.event_id,self.instance,self.boot))
            return True,None
        except HTTPException as e:raise self.convert(e)

    async def validate_redaction(self,event):
        target=event.content.get('redacts') or event.get('redacts')
        if not target:raise HTTPException(400,'缺少撤回目标')
        original=await self.api._store.get_event(target,allow_none=True)
        if not original or original.room_id!=event.room_id or original.is_state():raise HTTPException(403,'不能撤回此事件')
        info=policy.room(self.s,event.room_id)
        control=groups.admitted(self.s,event.room_id,event.sender,event.type,unfreeze(event.content))
        if info['kind']=='direct' and original.sender!=event.sender:raise HTTPException(403,'只能撤回自己的私聊消息')
        if info['kind']=='group' and original.sender!=event.sender and not control:
            with connect(self.s.db) as c:groups.permission(c,event.room_id,event.sender,'redact',original.sender)
        content=unfreeze(original.content)
        if content.get(history.BUSINESS) or history.business_reference(self.s,event.room_id,event.sender,content):raise HTTPException(403,{'code':'YICHAT_BUSINESS_IMMUTABLE','error':'转账、红包和赠卡消息不允许撤回'})
        def accepted(txn):
            txn.execute('SELECT received_ts FROM events WHERE event_id=?',(target,))
            row=txn.fetchone();return row[0] if row else None
        at=await self.api.run_db_interaction('yichat_redaction_accept_time',accepted)
        if at is None or not 0<=int(time.time()*1000)-at<=300000:
            raise HTTPException(403,{'code':'YICHAT_REDACTION_WINDOW','error':'消息只能在发送后5分钟内撤回'})

    def check_archive(self):
        import time
        try:
            with archive.connect(self.s.archive_ingest) as c:checkpoint=c.execute("SELECT healthy,observed_ms FROM checkpoints WHERE source='synapse'").fetchone()
            if checkpoint and (not checkpoint['healthy'] or checkpoint['observed_ms']>int(time.time()*1000)):
                raise RuntimeError('Archive unhealthy or source clock moved backward')
        except Exception:
            raise HTTPException(503,{'errcode':'M_UNKNOWN','code':'YICHAT_ARCHIVE_UNAVAILABLE','error':'存证服务暂不可用，发送已暂停'})

    async def on_new_event(self,event,state):
        groups.apply(self.s,unfreeze(event.content),event.event_id)
        if event.type in ('m.room.message','m.room.encrypted'):
            with transaction(self.s.db) as c:c.execute('UPDATE direct_visibility SET visible=1 WHERE room=?',(event.room_id,))
        if event.content.get(archive.ATTACHMENT_KEY):
            def received(txn):
                txn.execute('SELECT received_ts FROM events WHERE event_id=?',(event.event_id,))
                return txn.fetchone()[0]
            accepted_ms=await self.api.run_db_interaction('yichat_attachment_accept_time',received)
            policy.finish_attachments(self.s,event.event_id,accepted_ms)
        if event.type=='m.room.member':
            accepted=state.get(('m.room.member',event.state_key),event)
            from . import lifecycle
            info=policy.room(self.s,event.room_id)
            with transaction(self.s.ledger) as c:
                c.execute('UPDATE yichat_memberships SET membership=?,event_id=?,pending_event=CASE WHEN pending_event=? THEN NULL ELSE pending_event END WHERE room=? AND mxid=?',
                    (accepted.content.get('membership','unknown'),accepted.event_id,event.event_id,event.room_id,event.state_key))
                if info['kind']=='group':lifecycle.transition(c,event.room_id)
            if info['kind']=='group':
                if accepted.content.get('membership')=='leave':
                    with transaction(self.s.db) as db:db.execute('UPDATE group_members SET admin=0,muted_until=0 WHERE room=? AND mxid=?',(event.room_id,event.state_key))
        # Never rely on this hook alone. Sweep resumes from a durable PG cursor.
        self.api.run_as_background_process('yichat_ingest_notice',self.sweep)

    async def recover_memberships(self):
        with connect(self.s.db) as c:pending=c.execute('SELECT DISTINCT event FROM attachment_admissions WHERE worker=? AND boot<>?',(self.instance,self.boot)).fetchall()
        for entry in pending:
            def received(txn):
                txn.execute('SELECT received_ts FROM events WHERE event_id=?',(entry['event'],))
                row=txn.fetchone();return row[0] if row else None
            at=await self.api.run_db_interaction('yichat_recover_attachment_admission',received)
            if at is not None:policy.finish_attachments(self.s,entry['event'],at)
            else:
                with transaction(self.s.db) as c:c.execute('DELETE FROM attachment_admissions WHERE event=? AND worker=? AND boot<>?',(entry['event'],self.instance,self.boot))
        with connect(self.s.db) as c:rooms=c.execute('SELECT room FROM rooms').fetchall()
        for row in rooms:
            room=row['room']
            if policy.retired(self.s,room):continue
            if room in self.initialized_rooms:continue
            state=await self.api.get_room_state(room,[('m.room.member',None)])
            with transaction(self.s.ledger) as c:
                for (_,mxid),event in state.items():
                    c.execute('INSERT OR IGNORE INTO yichat_memberships VALUES(?,?,?,?,NULL,NULL,NULL)',(room,mxid,event.content.get('membership','unknown'),event.event_id))
                    # Only this restarted writer's old in-flight admission is
                    # abandoned. Other live writers' transitions remain barriers.
                    c.execute('UPDATE yichat_memberships SET membership=?,event_id=?,pending_event=NULL WHERE room=? AND mxid=? AND pending_worker=? AND pending_boot<>?',
                        (event.content.get('membership','unknown'),event.event_id,room,mxid,self.instance,self.boot))
            if policy.room(self.s,room)['kind']=='group':
                from .lifecycle import transition
                with transaction(self.s.ledger) as ledger:
                    with archive.connect(self.s.archive_reader) as pg:ever=pg.execute("SELECT count(DISTINCT mxid) n FROM memberships WHERE room=%s AND membership='join'",(room,)).fetchone()['n']
                    if ever>=2:ledger.execute('INSERT INTO yichat_group_lifecycle(room,had_multiple) VALUES(?,1) ON CONFLICT(room) DO UPDATE SET had_multiple=1',(room,))
                    transition(ledger,room)
                with transaction(self.s.db) as db:
                    for (_,mxid),event in state.items():
                        if event.content.get('membership')!='join':db.execute('UPDATE group_members SET admin=0,muted_until=0 WHERE room=? AND mxid=?',(room,mxid))
            self.initialized_rooms.add(room)

    async def revoke_sessions(self):
        from .security import valid_sid
        from cryptography.fernet import Fernet
        import time
        with connect(self.s.db) as c:rows=c.execute('SELECT * FROM sessions WHERE cleaned=0').fetchall()
        cipher=Fernet(self.s.session_key.encode())
        for row in rows:
            if row['revoked'] or row['expires']<=time.time() or not valid_sid(self.s,row['sid'],row['username']):
                with transaction(self.s.db) as c:c.execute('UPDATE sessions SET revoked=1 WHERE token_hash=?',(row['token_hash'],))
                # Version-pinned Synapse auth handler: unlike the standard logout
                # endpoint, this invalidates access only and preserves device keys.
                await self.api._auth_handler.delete_access_token(cipher.decrypt(row['upstream'].encode()).decode())
                with transaction(self.s.db) as c:c.execute('UPDATE sessions SET cleaned=1 WHERE token_hash=?',(row['token_hash'],))

    async def sweep(self):
        if self.busy:return
        self.busy=True
        try:
            await self.revoke_sessions()
            await self.recover_memberships()
            # This read-only, version-pinned store accessor handles PostgreSQL
            # multiwriters and SQLite. Only the background instance advances PG.
            generator=self.api._store.get_events_stream_id_generator()
            at=int(datetime.now(timezone.utc).timestamp()*1000)
            allocated=await generator.get_max_allocated_token()
            safe=generator.get_current_token()
            with archive.connect(self.s.archive_ingest) as c:
                first=c.execute('SELECT min(day) d FROM (SELECT day FROM events UNION SELECT day FROM attachments) a').fetchone()['d']
            cursor=await defer_to_thread(reactor,archive.reconcile_source_position,self.s.archive_ingest,allocated)
            def fetch(txn):
                txn.execute('SELECT e.event_id,e.room_id,e.stream_ordering,e.received_ts,j.json FROM events e JOIN event_json j ON j.event_id=e.event_id WHERE e.stream_ordering>? AND e.stream_ordering<=? AND e.outlier=0 AND e.rejection_reason IS NULL ORDER BY e.stream_ordering LIMIT 1000',(cursor,safe))
                return list(txn.fetchall())
            rows=await self.api.run_db_interaction('yichat_read_accepted_events_v1163',fetch)
            through=rows[-1][2] if len(rows)==1000 else safe
            yesterday=datetime.fromtimestamp(at/1000,timezone.utc).date()-timedelta(days=1)
            first=first or (archive.utcday(rows[0][3]) if rows else yesterday)
            fences=[];day=first
            while day<=yesterday:
                fences.append((day,allocated,at));day+=timedelta(days=1)
            rows=[row for row in rows if not policy.retired(self.s,row[1])]
            for eid,rid,_,_,raw in rows:
                accepted=json.loads(raw);groups.apply(self.s,accepted.get('content',{}),eid)
                if accepted.get('type') in ('m.room.message','m.room.encrypted'):
                    with transaction(self.s.db) as c:c.execute('UPDATE direct_visibility SET visible=1 WHERE room=?',(rid,))
            await defer_to_thread(reactor,history.record_rows,self.s,rows)
            await defer_to_thread(reactor,archive.ingest_batch,self.s.archive_ingest,rows,through,at,fences)
            await defer_to_thread(reactor,history.backfill,self.s)
            self.ready=True
            if time.time()-getattr(self,'retirement_at',0)>15:
                self.retirement_at=time.time()
                try:
                    from .lifecycle import retire_accounts
                    await retire_accounts(self)
                except Exception:log.exception('Account chat retirement pending; will retry')
        except Exception:
            self.ready=False
            log.exception('YiChat durable ingestion failed; sealing and cleanup paused')
            try:await defer_to_thread(reactor,archive.record_failure,self.s.archive_ingest)
            except Exception:log.error('Cannot persist ingestion failure')
        finally:self.busy=False
