// Keep a gesture on the DOM node: selecting a message rerenders its handlers mid-touch.
type Gesture={x:number;y:number;dx:number;cancelled:boolean;held:boolean;timer?:ReturnType<typeof setTimeout>;suppressUntil:number};
const gestures=new WeakMap<HTMLElement,Gesture>();
export function selectMessageText(node:HTMLElement){
 const selection=window.getSelection();selection?.removeAllRanges();
 const body=node.querySelector<HTMLElement>('.message-copy-text');if(!body)return '';
 const range=document.createRange();range.selectNodeContents(body);selection?.addRange(range);return body.textContent||'';
}
export function swipeReply(reply:()=>void,longPress?:(node:HTMLElement)=>void,selectTap?:(e:MouseEvent)=>void){
 function clear(node:HTMLElement,g?:Gesture){clearTimeout(g?.timer);node.style.removeProperty('--reply-drag');node.classList.remove('swiping-reply')}
 return {
 onTouchStart(e:TouchEvent){const node=e.currentTarget as HTMLElement;clear(node,gestures.get(node));if(e.touches.length!==1||(e.target as HTMLElement).closest('input,textarea,audio,video,.message-actions')){gestures.delete(node);return}
 const g:Gesture={x:e.touches[0].clientX,y:e.touches[0].clientY,dx:0,cancelled:false,held:false,suppressUntil:0};gestures.set(node,g);
 g.timer=setTimeout(()=>{if(!node.isConnected){gestures.delete(node);return}g.held=true;g.cancelled=true;g.suppressUntil=Date.now()+1000;longPress?.(node)},500)},
 onTouchMove(e:TouchEvent){const node=e.currentTarget as HTMLElement,g=gestures.get(node);if(!g)return;if(e.touches.length!==1){g.cancelled=true;clear(node,g);return}
 g.dx=e.touches[0].clientX-g.x;const dy=e.touches[0].clientY-g.y;
 if(Math.abs(g.dx)>10||Math.abs(dy)>10)clearTimeout(g.timer);
 if(g.dx>18||Math.abs(dy)>20&&Math.abs(dy)>Math.abs(g.dx)){g.cancelled=true;clear(node,g);return}
 if(!g.cancelled&&g.dx< -18&&Math.abs(g.dx)>Math.abs(dy)*1.25){if(e.cancelable)e.preventDefault();node.classList.add('swiping-reply');node.style.setProperty('--reply-drag',Math.max(-80,g.dx)+'px')}
 },
 onTouchEnd(e:TouchEvent){const node=e.currentTarget as HTMLElement,g=gestures.get(node);if(!g)return;
 if(g.held){g.suppressUntil=Date.now()+800;requestAnimationFrame(()=>selectMessageText(node))}
 else if(!g.cancelled&&g.dx<= -60){g.suppressUntil=Date.now()+800;reply()}
 clear(node,g)},
 onTouchCancel(e:TouchEvent){const node=e.currentTarget as HTMLElement,g=gestures.get(node);if(g)g.cancelled=true;clear(node,g)},
 onContextMenu(e:MouseEvent){e.preventDefault();const node=e.currentTarget as HTMLElement,g=gestures.get(node);if(g){clear(node,g);g.held=true;g.cancelled=true;g.suppressUntil=Date.now()+1000}longPress?.(node)},
 onClickCapture(e:MouseEvent){const g=gestures.get(e.currentTarget as HTMLElement);if(g&&Date.now()<g.suppressUntil){e.preventDefault();e.stopPropagation();g.suppressUntil=0;return}selectTap?.(e)}
 };
}
