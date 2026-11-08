// Touch gestures leave scrolling and interactive media controls to the browser.
export function swipeReply(reply:()=>void,longPress?:()=>void){
 let timer:ReturnType<typeof setTimeout>|undefined;let start:{x:number;y:number}|undefined,dx=0,cancelled=false;
 function reset(node:HTMLElement){clearTimeout(timer);node.style.removeProperty('--reply-drag');node.classList.remove('swiping-reply');start=undefined;dx=0}
 return {
 onTouchStart(e:TouchEvent){if(e.touches.length!==1||(e.target as HTMLElement).closest('button,a,input,textarea,audio,video'))return;start={x:e.touches[0].clientX,y:e.touches[0].clientY};cancelled=false;dx=0;timer=setTimeout(()=>{cancelled=true;longPress?.()},550)},
 onTouchMove(e:TouchEvent){if(!start||e.touches.length!==1)return;dx=e.touches[0].clientX-start.x;const dy=e.touches[0].clientY-start.y;if(Math.abs(dx)>10||Math.abs(dy)>10)clearTimeout(timer);if(Math.abs(dy)>20&&Math.abs(dy)>Math.abs(dx)){cancelled=true;return}if(!cancelled&&Math.abs(dx)>18){const node=e.currentTarget as HTMLElement;node.classList.add('swiping-reply');node.style.setProperty('--reply-drag',Math.max(-80,Math.min(80,dx))+'px')}},
 onTouchEnd(e:TouchEvent){if(start&&!cancelled&&Math.abs(dx)>=60)reply();reset(e.currentTarget as HTMLElement)},
 onTouchCancel(e:TouchEvent){reset(e.currentTarget as HTMLElement)}
 };
}
