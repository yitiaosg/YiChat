import {render} from 'preact';
import {chat} from '../../src/chat';
import {requests} from '../../src/api';
import {ShareCard,GiftCard} from '../../src/previews';
import {RecordCard} from '../../src/sharing-ui';
import '../../src/style.css';
const w=window as any,room='!offline',id='a'.repeat(32),gift='b'.repeat(32),url='https://chat.yitiao.org/orders/'+id;
const order={id,kind:'direct',amount:'12.50',title:'午饭',state:'paid',actions:{},recipient_uid:'bob',recipient_mxid:'@bob:chat.yitiao.org',claims:[{user_id:'bob'}]};
const data={type:'order',url,data:order};
const card={id:'offline-card',cid:'CARD-OFFLINE',name:'离线赠卡',design:{name:'离线赠卡',width_mm:85.6,height_mm:54,corners:'round',material:'plastic',layers:[],thickness_mm:.8}};
const payload={version:2,owner:'@alice:chat.yitiao.org',identity:'both',messages:[{id:'$one',sender:'@alice:chat.yitiao.org',name:'爱丽丝',username:'alice',sent_at:Date.now(),content:{msgtype:'m.text',body:'离线聊天记录'},previews:[]}]};
const canonical=JSON.stringify(payload),hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(canonical)))).map(n=>n.toString(16).padStart(2,'0')).join('');
w.offlineStore={
 [room+'|'+url]:data,[room+'|order:'+id]:order,
 [room+'|gift:'+gift]:{id:gift,room,state:'transferred',card,sender:'alice',recipient:'bob',created:Date.now()/1000},
 ['|record:'+hash]:{hash,canonical}
};
w.offlineReads=[];w.apiLoads=[];
chat.session={uid:'alice',device_id:'device',mxid:'@alice:chat.yitiao.org'} as any;chat.offline=true;
chat.info.set(room,{room,kind:'direct',encryption:'plain'} as any);
requests.api=async(...args:any[])=>{w.apiLoads.push(args);throw TypeError('Offline network')};
render(<main style={{padding:'20px',maxWidth:'500px',margin:'auto'}}><p>离线备份预览</p><article class="message"><div class="message-main"><div class="bubble content-bubble"><ShareCard url={url} room={room}/></div></div></article><article class="message mine"><div class="message-main"><div class="bubble content-bubble"><ShareCard url={url} room={room} receiptSender="@bob:chat.yitiao.org"/></div></div></article><GiftCard id={gift} room={room}/><article class="message mine"><div class="message-main"><div class="bubble content-bubble"><RecordCard room={room} content={{body:'https://chat.yitiao.org/records/'+hash}}/></div></div></article></main>,document.getElementById('app')!);
