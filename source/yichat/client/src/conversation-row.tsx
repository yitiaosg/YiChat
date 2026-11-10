import {messageCaption} from './message-display';
import {useEffect,useRef,useState} from 'preact/hooks';import {Archive,ArchiveRestore,Pin,PinOff,BellOff,Trash2,CheckCircle2,Circle,FileArchive} from 'lucide-preact';
import {ContextMenu} from './context-menu';import {isApp} from './app';
import {chat} from './chat';import {RoomAvatar} from './profiles';
const SWIPE_TRIGGER=64;
const HOLD_MS=480;
export function ConversationRow({room,active,open,selection,disabled=false,exportRoom}:{room:any;active:boolean;open:()=>void;selection?:boolean;disabled?:boolean;exportRoom?:()=>void}){
 const pref=chat.preferences.get(room.roomId)||{},swiped=useRef(false),[busy,setBusy]=useState(false),[menu,setMenu]=useState<{x:number;y:number}>(),hold=useRef<ReturnType<typeof setTimeout>>();
 useEffect(()=>()=>{clearTimeout(hold.current);clearTimeout(settle.current)},[]);
 const entry=useRef<HTMLDivElement>(null),drag=useRef<{pointer:number;x:number;y:number;locked:boolean;dx:number;armed:boolean;still?:boolean}>(),settle=useRef<ReturnType<typeof setTimeout>>();
 const empty=chat.info.get(room.roomId)?.has_messages===false;
 const count=chat.unreadCount(room),invited=room.getMyMembership()==='invite',last=chat.previewEvent(room),content=last?.getContent();
 const summary=invited?'邀请你加入会话':chat.info.get(room.roomId)?.peer_deleted?'对方账户已注销':chat.info.get(room.roomId)?.dissolved_at?'群聊已解散':room.getMyMembership()==='leave'?'已退出群聊':last?.isRedacted()?(last.getWireType()==='m.room.encrypted'?'加密消息 · 打开核实':'消息已撤回'):last?.isDecryptionFailure()?'加密消息 · 等待密钥':(content?messageCaption(content,chat.info.get(room.roomId)?.kind):'')||'还没有消息';
 async function change(value:any){setBusy(true);try{await chat.setPreference(room.roomId,value)}catch(e){chat.fail(e)}finally{setBusy(false)}}
 // The row follows the finger directly; the freed space names the action.
 function paint(dx:number){const node=entry.current;if(!node)return false;const row=node.querySelector<HTMLElement>('.room-row'),armed=Math.abs(dx)>=SWIPE_TRIGGER;if(row)row.style.transform=dx?`translateX(${dx}px)`:'';for(const hint of node.querySelectorAll<HTMLElement>('.swipe-hint')){const mine=hint.classList.contains('swipe-pin')?dx>0:dx<0;hint.style.width=(mine?Math.abs(dx):0)+'px';hint.classList.toggle('armed',mine&&armed)}return armed}
 function release(){const node=entry.current;drag.current=undefined;if(!node)return;node.classList.remove('dragging');paint(0);clearTimeout(settle.current);settle.current=setTimeout(()=>node.classList.remove('swiping'),240)}
 const pin=()=>change({pinned:!pref.pinned,...(!pref.pinned?{archived:false}:{})}),archive=()=>change({archived:empty||!pref.archived});
 const gestures=selection!==undefined||disabled?{}:{
  onPointerDown(e:PointerEvent){swiped.current=false;clearTimeout(hold.current);if(!e.isPrimary||e.button!==0)return;const x=e.clientX,y=e.clientY;
   // Holding still opens the menu where the finger (or mouse) is.
   hold.current=setTimeout(()=>{if(drag.current?.locked)return;swiped.current=true;release();try{navigator.vibrate?.(10)}catch{/* Optional feedback. */}setMenu({x,y})},HOLD_MS);
   if(e.pointerType==='mouse'){drag.current={pointer:e.pointerId,x,y,locked:false,dx:0,armed:false,still:true};return}
   drag.current={pointer:e.pointerId,x,y,locked:false,dx:0,armed:false}},
  onPointerMove(e:PointerEvent){const g=drag.current,node=entry.current;if(!g||!node||g.pointer!==e.pointerId)return;const x=e.clientX-g.x,y=e.clientY-g.y;
   if(!g.locked&&Math.hypot(x,y)>10)clearTimeout(hold.current);
   if(g.still)return;
   if(!g.locked){if(Math.abs(y)>10&&Math.abs(y)>=Math.abs(x)){drag.current=undefined;return}if(Math.abs(x)<8||Math.abs(x)<Math.abs(y)*1.2)return;g.locked=true;g.x+=Math.sign(x)*8;clearTimeout(hold.current);clearTimeout(settle.current);node.classList.add('swiping','dragging');try{(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)}catch{/* The pointer is already gone. */}}
   const raw=e.clientX-g.x,free=Math.max(SWIPE_TRIGGER*2,node.offsetWidth*.55),size=Math.abs(raw);g.dx=Math.sign(raw)*(size<=free?size:free+(size-free)*.3);
   const armed=paint(g.dx);if(armed&&!g.armed)try{navigator.vibrate?.(8)}catch{/* Optional feedback. */}g.armed=armed},
  onPointerUp(e:PointerEvent){clearTimeout(hold.current);const g=drag.current;if(!g||g.pointer!==e.pointerId)return;const dx=g.dx,act=g.locked&&g.armed;if(g.locked)swiped.current=true;release();if(act&&!busy&&!chat.preferenceSaving(room.roomId))void(dx>0?pin():archive())},
  onPointerCancel(){clearTimeout(hold.current);if(drag.current?.locked)swiped.current=true;release()},
  onPointerLeave(e:PointerEvent){if(e.pointerType==='mouse'){clearTimeout(hold.current);drag.current=undefined}},
  onContextMenu(e:MouseEvent){e.preventDefault();clearTimeout(hold.current);if(menu)return;const box=(e.currentTarget as HTMLElement).getBoundingClientRect(),pointed=e.clientX>0||e.clientY>0;swiped.current=e.button!==2&&pointed;release();setMenu(pointed?{x:e.clientX,y:e.clientY}:{x:box.left+56,y:box.top+box.height/2})}
 };
 const saving=busy||chat.preferenceSaving(room.roomId);
 return <div ref={entry} class={'conversation-entry '+(selection!==undefined?'selection-conversation':pref.pinned?'pinned':'')}>
 {selection===undefined&&<><span class="swipe-hint swipe-pin" aria-hidden="true"><span>{pref.pinned?<PinOff size={20}/>:<Pin size={20}/>}{pref.pinned?'取消置顶':'置顶'}</span></span><span class={'swipe-hint swipe-archive'+(empty?' swipe-delete':'')} aria-hidden="true"><span>{empty?<Trash2 size={20}/>:pref.archived?<ArchiveRestore size={20}/>:<Archive size={20}/>}{empty?'删除':pref.archived?'取消归档':'归档'}</span></span></>}
 <button type="button" class={'room-row '+(active?'active':'')+(menu?' menu-open':'')} aria-haspopup={selection===undefined?'menu':undefined} aria-pressed={selection} disabled={disabled} style={{touchAction:'pan-y'}} {...gestures} onClick={e=>{if(!swiped.current||e.detail===0)open()}}>
 <RoomAvatar room={room}/><span class="room-summary"><span><strong>{chat.roomName(room)}</strong><time>{last?new Date(last.getTs()).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false}):''}</time></span><span><small>{!!pref.pinned&&<Pin size={12}/>} {!!pref.muted&&<BellOff size={12}/>} {summary}</small>{selection===undefined&&(invited?<i class="invite-badge">邀请</i>:count>0&&<i class="unread-count">{count>99?'99+':count}</i>)}</span></span>{selection!==undefined&&(selection?<CheckCircle2 class="room-choice-mark chosen" size={23}/>:<Circle class="room-choice-mark" size={23}/>)}</button>
 {menu&&<ContextMenu x={menu.x} y={menu.y} label={chat.roomName(room)+' · 会话操作'} close={()=>setMenu(undefined)} items={[
  {label:pref.pinned?'取消置顶':'置顶',icon:pref.pinned?<PinOff size={17}/>:<Pin size={17}/>,disabled:saving,run:()=>void pin()},
  {label:empty?'删除空会话':pref.archived?'取消归档':'归档',icon:empty?<Trash2 size={17}/>:pref.archived?<ArchiveRestore size={17}/>:<Archive size={17}/>,danger:empty,disabled:saving,run:()=>void archive()},
  {label:'导出对话文件',icon:<FileArchive size={17}/>,disabled:!isApp||!exportRoom||empty,note:!isApp?'在 YiChat App 中可用':empty?'还没有消息':undefined,run:()=>exportRoom?.()}
 ]}/>}</div>;
}
