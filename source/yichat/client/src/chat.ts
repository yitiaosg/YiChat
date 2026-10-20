import * as sdk from 'matrix-js-sdk';
import { decodeRecoveryKey } from 'matrix-js-sdk/lib/crypto-api/recovery-key';
import {sdkLog} from './log';
import { requests, path, codepoints, shareUrls } from './api';
import { Vault } from './vault';
import {History} from './history';
export type RoomInfo={room:string;kind:'direct'|'group';owner:string;peer:string|null;encryption:'direct'|'plain'|'legacy_encrypted'};
export type Outgoing={txn:string;room:string;content:any;attachments:string[];state:'sending'|'failed';error?:string;time:number};
export type Profile={mxid:string;username:string;nickname:string;biography?:string;avatar_url?:string;profile_url?:string};
// This gateway does not expose calls. Disable the SDK's TURN poll explicitly.
class MessagingClient extends sdk.MatrixClient {protected canSupportVoip=false;}
export class Chat {
  client!:sdk.MatrixClient; history=new History(this);private historyStarted=false; session:any;vault=new Vault();businessVault=new Vault();phase='loading';error='';sync='正在连接';
  rooms: sdk.Room[]=[];info=new Map<string,RoomInfo>();profiles=new Map<string,Profile>();outbox:Outgoing[]=[];business:any[]=[];
  verification:any; sas:any;verificationStatus='';backupRemaining:number|null=null;private recovery?:Uint8Array;private keys=new Map<string,Uint8Array>();
  private listeners=new Set<()=>void>();private release?:()=>void;private closed=false;private cryptoInit?:Promise<void>;private refreshTimer?:ReturnType<typeof setTimeout>;private channel?:BroadcastChannel;
  readonly outer=new Map<string,string[]>();private outerBusiness=new Map<string,any>();private profileTimes=new Map<string,number>();private inflight=new Set<string>();private profileFlight?:Promise<void>;
  private encryptionReady=new Map<string,Promise<void>>();
  subscribe=(fn:()=>void)=>{this.listeners.add(fn);return()=>this.listeners.delete(fn)};
  changed=()=>{for(const fn of this.listeners)fn()};
  async boot(){
    requests.onUnauthorized=()=>this.lock('login');
    try{
      if(!navigator.locks||!window.indexedDB||!window.isSecureContext)throw Error('此浏览器需要 HTTPS、IndexedDB 和 Web Locks 才能安全保存加密密钥。请使用新版浏览器。');
      const active=await new Promise<boolean>((resolve)=>{
        navigator.locks.request('yc:active-client',{ifAvailable:true},async lock=>{
          if(!lock){resolve(false);return}
          await new Promise<void>(release=>{this.release=release;resolve(true)});
        }).catch(e=>{this.error=String(e);resolve(false)});
      });
      if(!active){this.phase='other-tab';this.changed();return}
      this.channel=new BroadcastChannel('yc:lifecycle');this.channel.onmessage=e=>{if(e.data==='logout')this.lock('login');if(e.data==='release')this.lock('other-tab')};
      this.session=await requests.api('/api/session');
      if(this.closed)return;requests.scope=this.session.uid||this.session.mxid;requests.token=this.session.token;
      const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode([this.session.uid,this.session.mxid,this.session.device_id].join('|')));
      if(this.closed)return;
      const scope=Array.from(new Uint8Array(bytes),b=>b.toString(16).padStart(2,'0')).join('');
      await this.vault.open(scope);if(this.closed){this.vault.close();return}this.outbox=(await this.vault.outbox()).map((x:Outgoing)=>({...x,state:'failed',error:'上次发送结果未知；重试将沿用原事务 ID'}));if(this.closed)return;await this.businessVault.open('account:'+this.session.uid+':business');if(this.closed){this.businessVault.close();return}this.business=await this.businessVault.business();
      if(this.closed)return;
      const marker=await this.vault.get('rust-initialized');const existing=await requests.api('/_matrix/client/v3/keys/query',{device_keys:{[this.session.mxid]:[this.session.device_id]}});if(this.closed)return;
      const databases=typeof indexedDB.databases==='function'?await indexedDB.databases():null;
      if(existing.device_keys?.[this.session.mxid]?.[this.session.device_id]&&(!marker||(databases&&!databases.some(db=>db.name?.startsWith('yc-rust:'+scope)))))throw Error('此设备的本地加密存储缺失。请重新授权登录以创建新设备，再恢复历史密钥；原设备和备份会保留。');
      if(this.closed)return;
      this.client=new MessagingClient({store:new sdk.MemoryStore(),logger:sdkLog,baseUrl:location.origin,accessToken:this.session.token,userId:this.session.mxid,deviceId:this.session.device_id,
        fetchFn:async(input,init={})=>{
          const url=String(input),txn=decodeURIComponent(url.split('?')[0].split('/').at(-1)||'');const ids=this.outer.get(txn),business=this.outerBusiness.get(txn);
          if((ids?.length||business)&&/\/send\//.test(url)&&typeof init.body==='string')init={...init,body:JSON.stringify({...JSON.parse(init.body),...(ids?.length?{'org.yitiao.attachments':ids}:{}),...(business?{'org.yitiao.business':business}:{})})};
          return requests.fetch(input,init);
        },
        cryptoCallbacks:{
          getSecretStorageKey:async({keys})=>{for(const id of Object.keys(keys)){const k=this.keys.get(id)||this.recovery;if(k)return [id,k]}throw Error('请在安全设置中输入恢复密钥以解锁备份')},
          cacheSecretStorageKey:(id,_info,key)=>{this.keys.set(id,key)}
        },verificationMethods:['m.sas.v1']});
      this.cryptoInit=this.client.initRustCrypto({useIndexedDB:true,cryptoDatabasePrefix:'yc-rust:'+scope,storageKey:this.vault.storageKey});await this.cryptoInit;this.cryptoInit=undefined;if(this.closed){this.client.stopClient();return}await this.vault.set('rust-initialized',true);if(this.closed){this.client.stopClient();return}
      this.client.on(sdk.ClientEvent.Sync,(state,_prev,data)=>{
        this.sync=state==='PREPARED'||state==='SYNCING'?'已连接':state==='ERROR'?'连接中断，正在重试':'正在同步';
        if(state==='PREPARED'){this.phase='ready';void this.refresh().then(()=>{if(!this.historyStarted&&!this.closed){this.historyStarted=true;return this.history.start()}}).catch(e=>this.fail(e))}if(data?.error&&(data.error as any).httpStatus===401)this.lock('login');this.updateRooms();
      });
      const update=()=>{this.updateRooms()};
      this.client.on(sdk.RoomEvent.Timeline,update);this.client.on(sdk.RoomEvent.Name,update);this.client.on(sdk.RoomEvent.Receipt,update);this.client.on(sdk.RoomEvent.Redaction,update);
      this.client.on(sdk.MatrixEventEvent.Decrypted,update);this.client.on(sdk.RoomEvent.MyMembership,()=>{this.scheduleRefresh();update()});this.client.on(sdk.ClientEvent.Room,()=>{this.scheduleRefresh();update()});
      this.client.on('crypto.keyBackupSessionsRemaining' as any,(count:number)=>{this.backupRemaining=count;this.changed()});
      this.client.on('crypto.verificationRequestReceived' as any,(request:any)=>this.watchVerification(request));
      await this.client.startClient({initialSyncLimit:40,lazyLoadMembers:true,pendingEventOrdering:sdk.PendingEventOrdering.Detached});
      window.addEventListener('pagehide',()=>this.lock('loading'),{once:true});
    }catch(e:any){if(!this.closed){this.phase='error';this.error=e.message;this.changed()}}
  }
  fail(e:any){if(e?.name!=='AbortError'){this.error=e.message||String(e);this.changed()}}
  updateRooms(){if(!this.client||this.closed)return;this.rooms=this.client.getRooms().filter(r=>['join','invite'].includes(r.getMyMembership())).sort((a,b)=>b.getLastActiveTimestamp()-a.getLastActiveTimestamp());this.changed()}
  scheduleRefresh(){clearTimeout(this.refreshTimer);this.refreshTimer=setTimeout(()=>void this.refresh().catch(e=>this.fail(e)),800)}
  async refresh(){const rows:RoomInfo[]=await requests.single('rooms',()=>requests.api('/api/rooms'));if(this.closed)return;this.info=new Map(rows.map(r=>[r.room,r]));this.updateRooms();await this.loadProfiles([this.session.mxid,...rows.flatMap(r=>[r.owner,...(r.peer?[r.peer]:[])]),...this.rooms.slice(0,60).flatMap(r=>r.getMembers().map(m=>m.userId))])}
  async loadProfiles(mxids:string[],usernames:string[]=[]):Promise<void>{
    const missing=[...new Set(mxids)].filter(id=>!this.profiles.has(id)||(this.profileTimes.get(id)||0)<Date.now()-300000).slice(0,100);if(!missing.length)return;
    if(this.profileFlight){await this.profileFlight;return this.loadProfiles(mxids,usernames)}
    this.profileFlight=(async()=>{const rows:Profile[]=await requests.api('/api/profiles',{mxids:missing.slice(0,100-usernames.length),usernames});if(this.closed)return;for(const id of missing)this.profiles.set(id,{mxid:id,username:id,nickname:id});for(const id of missing)this.profileTimes.set(id,Date.now());for(const row of rows)this.profiles.set(row.mxid,row);this.changed()})();
    try{await this.profileFlight}finally{this.profileFlight=undefined}
  }
  name(mxid?:string){return mxid?(this.profiles.get(mxid)?.nickname||this.client?.getUser(mxid)?.displayName||mxid):'未知用户'}
  roomName(room:sdk.Room){const info=this.info.get(room.roomId);if(info?.kind==='direct')return this.name(info.owner===this.session.mxid?info.peer!:info.owner);return room.name||'会话'}
  timeline(room:sdk.Room){return this.history.timeline(room)}
  findEvent(room:sdk.Room,id:string){return room.findEventById(id)}
  canRedact(event:sdk.MatrixEvent){const receipt=this.history.receipts.get(event.getId()!);return !receipt?.business&&!event.getContent()['org.yitiao.business']&&!event.getWireContent()['org.yitiao.business']&&!['gift','order'].includes(event.getContent()['org.yitiao.share']?.type)&&Date.now()-(receipt?.received_ms||event.getTs())<=300000}
  async persist(){if(this.closed)return;await this.vault.saveOutbox(this.outbox);this.changed()}
  async saveBusiness(){if(this.closed)return;await this.businessVault.saveBusiness(this.business);this.changed()}
  async recordCancelledOrder(order:any){
    if(order.state!=='cancelled'||order.sender_uid!==this.session.uid)return;
    for(const op of this.business)if(op.type==='order'&&op.result?.id===order.id){op.result=order;op.state='cancelled'}
    await this.saveBusiness();
  }
  async announceOrder(order:any){
    if(order.state==='cancelled'||order.sender_uid!==this.session.uid||!order.deposit)throw Error('请先明确确认入金；已取消订单不能发送');
    let op=this.business.find(b=>b.type==='order'&&b.result?.id===order.id);
    if(op?.state==='announced')return;
    if(!op){op={type:'order',room:order.room,key:crypto.randomUUID(),txn:crypto.randomUUID(),result:order,state:'funded'};this.business.push(op)}
    op.result=order;await this.saveBusiness();
    const url='https://chat.yitiao.org/orders/'+order.id;
    await this.send(order.room,{msgtype:'m.text',body:url,'org.yitiao.share':{type:'order',url},'org.yitiao.business':{kind:'order',id:order.id}},[],op.txn);
    op.state='announced';await this.saveBusiness();
  }
  async announceReceipt(order:any){
    if(order.kind!=='direct'||order.recipient_uid!==this.session.uid||!order.claims?.some((p:any)=>p.user_id===this.session.uid))return;
    let op=this.business.find(b=>b.type==='receipt'&&b.result?.id===order.id);
    if(op?.state==='announced')return;
    if(!op){op={type:'receipt',room:order.room,txn:crypto.randomUUID(),result:{id:order.id},state:'pending'};this.business.push(op)}
    await this.saveBusiness();
    const url='https://chat.yitiao.org/orders/'+order.id;
    await this.send(order.room,{msgtype:'m.text',body:'已收款 · '+url,'org.yitiao.share':{type:'receipt',url,id:order.id},'org.yitiao.business':{kind:'order',id:order.id}},[],op.txn);
    op.state='announced';await this.saveBusiness();
  }
  async send(room:string,content:any,attachments:string[]=[],txn:string=crypto.randomUUID()){
    if(codepoints(content.body||'')>2000||codepoints(content['m.new_content']?.body||'')>2000)throw Error('文字最多 2000 个 Unicode 码点');
    if(!this.info.has(room))throw Error('会话类型尚未核实，请稍后重试');
    const info=this.info.get(room)!,r=this.client.getRoom(room);
    const encrypted=!!r?.hasEncryptionStateEvent();
    if(info.encryption!=='plain'&&!encrypted)throw Error('端到端加密尚未就绪，已阻止明文发送');
    if(info.encryption==='plain'&&encrypted)throw Error('服务端会话策略与加密状态不一致，请重新同步');
    if(!content['org.yitiao.business'])for(const url of shareUrls(content.body||'')){const match=url.match(/^https:\/\/chat\.yitiao\.org\/orders\/([A-Za-z0-9_-]{32})$/);if(match)try{const order=await requests.api('/api/orders/'+path(match[1]));if(order.room===room){content['org.yitiao.business']={kind:'order',id:order.id};break}}catch{}}
    let row=this.outbox.find(x=>x.txn===txn);if(!row){row={txn,room,content,attachments,state:'sending',time:Date.now()};this.outbox.push(row)}
    row.state='sending';row.error=undefined;await this.persist();return this.dispatch(row);
  }
  async dispatch(row:Outgoing){
    if(this.inflight.has(row.txn))return;this.inflight.add(row.txn);this.outer.set(row.txn,row.attachments);if(row.content['org.yitiao.business'])this.outerBusiness.set(row.txn,row.content['org.yitiao.business']);
    try{
      if(this.info.get(row.room)?.encryption!=='plain')await this.prepareEncryption(row.room);
      const old=this.client.getRoom(row.room)?.getPendingEvents().find(e=>e.getTxnId()===row.txn);
      if(old)await this.client.resendEvent(old,this.client.getRoom(row.room)!);else await this.client.sendEvent(row.room,sdk.EventType.RoomMessage,row.content,row.txn);
      this.outbox=this.outbox.filter(x=>x.txn!==row.txn);await this.persist();
    }catch(e:any){row.state='failed';row.error=e.data?.error||e.message||'发送失败';await this.persist()}
    finally{this.inflight.delete(row.txn);this.outer.delete(row.txn);this.outerBusiness.delete(row.txn);this.changed()}
  }
  private async prepareEncryption(id:string){
    let ready=this.encryptionReady.get(id);
    if(!ready){ready=(async()=>{
      const room=this.client.getRoom(id);if(!room)throw Error('加密会话尚未同步');
      // MemoryStore has no roster after a page return. Read current accepted
      // membership events (without the earlier sync token) before key sharing.
      const roster=await this.client.members(id);
      room.currentState.setStateEvents(roster.chunk.map(event=>new sdk.MatrixEvent({...event,room_id:id})));
      const targets=await room.getEncryptionTargetMembers(),info=this.info.get(id)!;
      if(info.kind==='direct'&&!targets.some(m=>m.userId===(info.owner===this.session.mxid?info.peer:info.owner)))throw Error('加密接收成员尚未就绪，请同步会话或等待对方接受邀请后重试');
      await this.client.getCrypto()!.getUserDeviceInfo(targets.map(m=>m.userId),true);
      // Resolve the full native roster/device list before the first send.
      // Keep native device identities and existing Megolm history intact.
    })();this.encryptionReady.set(id,ready);ready.catch(()=>this.encryptionReady.delete(id))}
    await ready;
  }
  async retry(row:Outgoing){return this.send(row.room,row.content,row.attachments,row.txn)}
  async discard(row:Outgoing){const event=this.client.getRoom(row.room)?.getPendingEvents().find(e=>e.getTxnId()===row.txn);if(event?.status===sdk.EventStatus.NOT_SENT)this.client.cancelPendingEvent(event);this.outbox=this.outbox.filter(x=>x!==row);await this.persist()}
  async create(kind:'direct'|'group',users:Profile[],name:string){
    await this.refresh();
    for(const room of this.rooms){const info=this.info.get(room.roomId);if(info?.kind!==kind)continue;
      if(kind==='direct'&&users.length===1&&[info.owner,info.peer].includes(users[0].mxid))return room.roomId;
      if(kind==='group'&&room.name===name){const ids=room.getMembers().filter(m=>['join','invite'].includes(m.membership||'')).map(m=>m.userId).sort();if(JSON.stringify(ids)===JSON.stringify([this.session.mxid,...users.map(x=>x.mxid)].sort()))return room.roomId}
    }
    if(sessionStorage.getItem('yc:create:'+this.session.uid))return this.resumeCreation();
    const payload={kind,users:users.map(u=>u.username),name,key:crypto.randomUUID()};
    sessionStorage.setItem('yc:create:'+this.session.uid,JSON.stringify(payload));
    try{const result=await requests.api('/api/rooms',payload);sessionStorage.removeItem('yc:create:'+this.session.uid);this.scheduleRefresh();return result.room_id}
    catch(e:any){if(e.status&&e.status<500&&e.data?.code!=='YICHAT_ROOM_CREATE_PENDING'&&e.status!==429)sessionStorage.removeItem('yc:create:'+this.session.uid);throw e}
  }
  get pendingCreation(){return sessionStorage.getItem('yc:create:'+this.session.uid)}
  async resumeCreation():Promise<string>{
    const stored=this.pendingCreation;if(!stored)throw Error('没有待确认的创建请求');const payload=JSON.parse(stored);
    let result:any;
    try{result=await requests.api('/api/room-creations/'+path(payload.key))}
    catch(e:any){if(e.status!==404)throw e;result=await requests.api('/api/rooms',payload)}
    if(result.room_id){sessionStorage.removeItem('yc:create:'+this.session.uid);this.scheduleRefresh();return result.room_id}
    if(result.state==='not_created'){sessionStorage.removeItem('yc:create:'+this.session.uid);throw Error('原请求已确认未创建会话。现在可以重新选择并创建。')}
    throw Error('原请求仍在处理中，已保留创建请求键；请稍后核实，不会重复创建。');
  }
  async join(room:string){await this.client.joinRoom(room);await this.refresh()}
  async backupStatus(){return {secret:await this.client.getCrypto()!.isSecretStorageReady(),backup:await this.client.getCrypto()!.getActiveSessionBackupVersion(),cross:await this.client.getCrypto()!.getCrossSigningStatus()}}
  async generateBackup(){
    const api=this.client.getCrypto()!;
    if(await this.client.secretStorage.getDefaultKeyId())throw Error('已有密钥备份，请输入原恢复密钥；不会重置旧备份');
    return api.createRecoveryKeyFromPassphrase();
  }
  async setupBackup(generated:any){
    const api=this.client.getCrypto()!;this.recovery=generated.privateKey.slice();
    await api.bootstrapCrossSigning({authUploadDeviceSigningKeys:async makeRequest=>{await makeRequest({})}});
    await api.bootstrapSecretStorage({createSecretStorageKey:async()=>({...generated,privateKey:generated.privateKey.slice()}),setupNewKeyBackup:true});
    await api.checkKeyBackupAndEnable();this.changed();
  }
  async restoreBackup(encoded:string){
    this.recovery=decodeRecoveryKey(encoded);const api=this.client.getCrypto()!;
    await api.bootstrapCrossSigning({authUploadDeviceSigningKeys:async request=>{await request({})}});
    await api.loadSessionBackupPrivateKeyFromSecretStorage();await api.checkKeyBackupAndEnable();const result=await api.restoreKeyBackup();await this.history.retryDecrypt();
    this.changed();return result;
  }
  watchVerification(request:any){this.verification=request;this.verificationStatus='请在两台设备上核对验证数字';this.sas=undefined;
    request.on('change',()=>{if(request.verifier)this.watchVerifier(request.verifier);if(request.phase===6)this.verificationStatus='设备验证成功';if(request.phase===5)this.verificationStatus='验证已取消';this.changed()});if(request.verifier)this.watchVerifier(request.verifier);this.changed();
  }
  private watched=new WeakSet<object>();
  watchVerifier(verifier:any){if(this.watched.has(verifier))return;this.watched.add(verifier);verifier.on('show_sas',(sas:any)=>{this.sas=sas;this.changed()});void verifier.verify().then(()=>{this.verificationStatus='设备验证成功';this.sas=undefined;this.changed()}).catch((e:any)=>{this.verificationStatus=e.message;this.sas=undefined;this.changed()})}
  async beginVerification(user:string,device:string){this.watchVerification(await this.client.getCrypto()!.requestDeviceVerification(user,device))}
  async acceptVerification(){await this.verification.accept();this.changed()}
  async startVerification(){this.watchVerifier(await this.verification.startVerification('m.sas.v1'))}
  async logout(){this.client?.stopClient();try{await requests.api('/api/logout',{})}catch(e:any){this.error='退出请求未确认，请到主站账户退出以撤销全部关联会话：'+e.message}finally{if(!this.closed)this.channel?.postMessage('logout');this.lock('login')}}
  takeOver(){const c=new BroadcastChannel('yc:lifecycle');c.postMessage('release');c.close();setTimeout(()=>location.reload(),700)}
  lock(phase:string){if(this.closed)return;this.closed=true;this.phase=phase;this.client?.stopClient();this.history.stop();requests.close();this.recovery?.fill(0);for(const key of this.keys.values())key.fill(0);this.keys.clear();this.encryptionReady.clear();this.outbox=[];this.business=[];this.rooms=[];this.info.clear();this.profiles.clear();this.profileTimes.clear();this.session=undefined;this.channel?.close();clearTimeout(this.refreshTimer);const finalize=()=>{this.client?.stopClient();this.vault.close();this.businessVault.close();this.release?.()};if(this.cryptoInit)void this.cryptoInit.then(finalize,finalize);else finalize();this.changed();window.dispatchEvent(new Event('yc:locked'))}
}
export const chat=new Chat();
export {sdk};
