// Pointer events cover touch, pen and a held mouse button. Keep state on the DOM
// node because entering selection mode rerenders the handlers during the press.
type Gesture={pointer:number;x:number;y:number;dx:number;dy:number;cancelled:boolean;held:boolean;timer?:ReturnType<typeof setTimeout>;suppressUntil:number;ended:boolean};
const gestures=new WeakMap<HTMLElement,Gesture>();
const controls='input,textarea,audio,video,.message-actions,.message-selection-control';
export function selectMessageText(node:HTMLElement){
 const selection=window.getSelection();selection?.removeAllRanges();
 document.querySelectorAll('.message-text-selected').forEach(el=>el.classList.remove('message-text-selected'));
 const body=node.querySelector<HTMLElement>('.message-copy-text');if(!body)return '';
 node.classList.add('message-text-selected');
 const range=document.createRange();range.selectNodeContents(body);selection?.addRange(range);return body.textContent||'';
}
export function swipeReply(reply:()=>void,longPress?:(node:HTMLElement)=>void,selectTap?:(e:MouseEvent)=>void){
 function clear(node:HTMLElement,g?:Gesture){clearTimeout(g?.timer);node.style.removeProperty('--reply-drag');node.classList.remove('swiping-reply')}
 function hold(node:HTMLElement,g?:Gesture){if(g?.held)return;if(g){clear(node,g);g.held=true;g.cancelled=true;g.suppressUntil=Date.now()+1000}longPress?.(node)}
 return {
 onPointerDown(e:PointerEvent){if(!e.isPrimary||e.button!==0)return;const node=e.currentTarget as HTMLElement;clear(node,gestures.get(node));if((e.target as HTMLElement).closest(controls)){gestures.delete(node);return}
 node.classList.remove('message-text-selected');
 const g:Gesture={pointer:e.pointerId,x:e.clientX,y:e.clientY,dx:0,dy:0,cancelled:false,held:false,suppressUntil:0,ended:false};gestures.set(node,g);
 g.timer=setTimeout(()=>{if(!node.isConnected){gestures.delete(node);return}hold(node,g)},500)},
 onPointerMove(e:PointerEvent){const node=e.currentTarget as HTMLElement,g=gestures.get(node);if(!g||g.ended||g.pointer!==e.pointerId)return;
 g.dx=e.clientX-g.x;g.dy=e.clientY-g.y;
 if(Math.abs(g.dx)>10||Math.abs(g.dy)>10)clearTimeout(g.timer);
 if(g.dx>18||Math.abs(g.dy)>20&&Math.abs(g.dy)>Math.abs(g.dx)){g.cancelled=true;clear(node,g);return}
 if(!g.cancelled&&g.dx< -18&&Math.abs(g.dx)>Math.abs(g.dy)*1.25){if(e.cancelable)e.preventDefault();node.classList.add('swiping-reply');node.style.setProperty('--reply-drag',Math.max(-80,g.dx)+'px')}
 },
 onPointerUp(e:PointerEvent){const node=e.currentTarget as HTMLElement,g=gestures.get(node);if(!g||g.pointer!==e.pointerId)return;
 if(g.held){g.suppressUntil=Date.now()+800;requestAnimationFrame(()=>selectMessageText(node))}
 else if(!g.cancelled&&g.dx<= -60&&Math.abs(g.dx)>Math.abs(g.dy)*1.25){g.suppressUntil=Date.now()+800;reply()}
 g.ended=true;clear(node,g)},
 onPointerCancel(e:PointerEvent){const node=e.currentTarget as HTMLElement,g=gestures.get(node);if(g){g.cancelled=true;g.ended=true}clear(node,g)},
 onPointerLeave(e:PointerEvent){if(e.pointerType!=='mouse')return;const node=e.currentTarget as HTMLElement,g=gestures.get(node);if(g&&!g.held){g.cancelled=true;g.ended=true;clear(node,g)}},
 onContextMenu(e:MouseEvent){if((e.target as HTMLElement).closest(controls))return;e.preventDefault();const node=e.currentTarget as HTMLElement;hold(node,gestures.get(node))},
 onClickCapture(e:MouseEvent){if((e.target as HTMLElement).closest('.message-selection-control'))return;const g=gestures.get(e.currentTarget as HTMLElement);if(g&&Date.now()<g.suppressUntil){e.preventDefault();e.stopPropagation();g.suppressUntil=0;return}selectTap?.(e)}
 };
}
