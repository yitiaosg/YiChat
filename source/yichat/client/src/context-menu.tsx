import {useEffect,useLayoutEffect,useRef} from 'preact/hooks';
import type {ComponentChildren} from 'preact';
import {createPortal} from 'preact/compat';
export type MenuItem={label:string;icon?:ComponentChildren;run:()=>void;danger?:boolean;disabled?:boolean;note?:string};
/** A menu that opens at the pointer, like a desktop context menu, and stays inside the window. */
export function ContextMenu({x,y,label,items,close}:{x:number;y:number;label:string;items:MenuItem[];close:()=>void}){
 const ref=useRef<HTMLDivElement>(null);
 useLayoutEffect(()=>{const node=ref.current;if(!node)return;const w=node.offsetWidth,h=node.offsetHeight,left=Math.max(8,Math.min(x,innerWidth-w-8)),top=Math.max(8,y+h+8>innerHeight?y-h:y);node.style.left=left+'px';node.style.top=Math.min(top,Math.max(8,innerHeight-h-8))+'px';node.style.transformOrigin=`${Math.max(0,Math.min(w,x-left))}px ${y>top?h:0}px`;node.classList.add('open');node.querySelector<HTMLElement>('button:not(:disabled)')?.focus({preventScroll:true})},[x,y]);
 useEffect(()=>{const previous=document.activeElement as HTMLElement|null;const key=(e:KeyboardEvent)=>{
   if(e.key==='Escape'||e.key==='Tab'){e.preventDefault();e.stopImmediatePropagation();close();return}
   const buttons=[...(ref.current?.querySelectorAll<HTMLElement>('button:not(:disabled)')||[])],at=buttons.indexOf(document.activeElement as HTMLElement);
   if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();buttons[(at+(e.key==='ArrowDown'?1:buttons.length-1))%buttons.length]?.focus()}
   if(e.key==='Home'||e.key==='End'){e.preventDefault();buttons.at(e.key==='Home'?0:-1)?.focus()}
  };const away=()=>close();document.addEventListener('keydown',key,true);window.addEventListener('resize',away);window.addEventListener('blur',away);return()=>{document.removeEventListener('keydown',key,true);window.removeEventListener('resize',away);window.removeEventListener('blur',away);previous?.focus?.({preventScroll:true})}},[]);
 return createPortal(<div class="context-menu-shade" data-no-zoom onPointerDown={e=>{if(e.target===e.currentTarget){e.preventDefault();close()}}} onContextMenu={e=>{e.preventDefault();if(e.target===e.currentTarget)close()}} onWheel={close}>
  <div class="context-menu" ref={ref} role="menu" aria-label={label}>{items.map(item=><button type="button" role="menuitem" class={item.danger?'danger':''} disabled={item.disabled} onClick={()=>{close();item.run()}}>{item.icon}<span>{item.label}{item.note&&<small>{item.note}</small>}</span></button>)}</div>
 </div>,document.body);
}
