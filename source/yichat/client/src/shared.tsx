import {useContext,useEffect,useRef,useState} from 'preact/hooks';
import {createContext} from 'preact';
import type {ComponentChildren} from 'preact';
import {createPortal} from 'preact/compat';
import {X,AlertCircle,ArrowLeft} from 'lucide-preact';
export function useVisible(){const ref=useRef<HTMLDivElement>(null),[visible,setVisible]=useState(false);useEffect(()=>{const observer=new IntersectionObserver(([entry])=>setVisible(entry.isIntersecting),{rootMargin:'60px'});if(ref.current)observer.observe(ref.current);return()=>observer.disconnect()},[]);return {ref,visible}}
export function SettingSwitch({label,description,checked,disabled=false,change}:{label:string;description?:string;checked:boolean;disabled?:boolean;change:(value:boolean)=>void}){
 return <label class={'setting-switch'+(disabled?' disabled':'')}><span class="setting-switch-label"><strong>{label}</strong>{description&&<small>{description}</small>}</span><input type="checkbox" role="switch" aria-label={label} checked={checked} disabled={disabled} onChange={e=>change(e.currentTarget.checked)}/><span class="switch-track" aria-hidden="true"/></label>
}
export function ErrorText({children}:{children:ComponentChildren}){return <p class="error" role="alert"><AlertCircle size={16}/>{children}</p>}
/** Full-screen pages: a Modal rendered inside a PageStack becomes one page of it. */
type PageInfo={depth:number;top:boolean;leaving:boolean};
const PageContext=createContext<PageInfo|null>(null);
export const PAGE_MS=260;
export function PageStack({stack,render}:{stack:string[];render:(id:string)=>ComponentChildren}){
 const [shown,setShown]=useState<{id:string;leaving:boolean}[]>(()=>stack.map(id=>({id,leaving:false})));
 const signature=stack.join('\n');
 useEffect(()=>{
  setShown(prev=>[...stack.map(id=>({id,leaving:false})),...prev.filter(p=>!stack.includes(p.id)).map(p=>({id:p.id,leaving:true}))]);
  const timer=setTimeout(()=>setShown(prev=>prev.filter(p=>!p.leaving)),PAGE_MS);
  return()=>clearTimeout(timer);
 },[signature]);
 if(!shown.length)return null;
 const live=shown.filter(p=>!p.leaving).length;
 // The top page comes first in the document; depth decides the stacking order.
 return createPortal(<div class="page-host">{shown.map((p,i)=><PageContext.Provider key={p.id} value={{depth:i,top:!p.leaving&&i===live-1,leaving:p.leaving}}>{render(p.id)}</PageContext.Provider>).reverse()}</div>,document.body);
}
function PageSheet({title,close,children,page}:{title:string;close:()=>void;children:ComponentChildren;page:PageInfo}){
 const ref=useRef<HTMLDivElement>(null),latest=useRef(close);latest.current=close;
 useEffect(()=>{if(!page.top)return;ref.current?.focus({preventScroll:true});const key=(e:KeyboardEvent)=>{if(e.key==='Escape'&&!document.querySelector('.modal-shade')){e.stopImmediatePropagation();latest.current()}};document.addEventListener('keydown',key);return()=>document.removeEventListener('keydown',key)},[page.top]);
 const hidden=!page.top;
 return <div class={'modal page-sheet'+(page.leaving?' page-leaving':'')+(hidden&&!page.leaving?' page-covered':'')} style={{zIndex:12+page.depth}} ref={ref} tabIndex={-1} role="dialog" aria-modal="true" aria-label={title} aria-hidden={hidden} {...({inert:hidden||undefined} as any)} data-no-zoom><header><button type="button" class="icon" aria-label="关闭" title="返回" onClick={close}><ArrowLeft/></button><h2>{title}</h2></header><div class="page-body"><PageContext.Provider value={null}>{children}</PageContext.Provider></div></div>;
}
export function Modal({title,close,children}:{title:string;close:()=>void;children:ComponentChildren}){
  const page=useContext(PageContext);
  return page?<PageSheet title={title} close={close} page={page}>{children}</PageSheet>:<Dialog title={title} close={close}>{children}</Dialog>;
}
function Dialog({title,close,children}:{title:string;close:()=>void;children:ComponentChildren}){
  const ref=useRef<HTMLDivElement>(null);useEffect(()=>{const previous=document.activeElement as HTMLElement;ref.current?.querySelector<HTMLElement>('input,button,a')?.focus();const key=(e:KeyboardEvent)=>{if(e.key==='Escape')close();if(e.key==='Tab'){const items=ref.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),textarea,select,a[href]');if(!items?.length)return;const first=items[0],last=items[items.length-1];if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus()}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus()}}};document.addEventListener('keydown',key);return()=>{document.removeEventListener('keydown',key);previous?.focus()}},[]);
  return createPortal(<div class="modal-shade" data-no-zoom onClick={e=>{if(e.target===e.currentTarget)close()}}><div class="modal" ref={ref} role="dialog" aria-modal="true" aria-label={title}><header><h2>{title}</h2><button type="button" class="icon" aria-label="关闭" onClick={close}><X/></button></header>{children}</div></div>,document.body)
}
const rendererLoads=new Map<string,Promise<void>>();
export function rendererScript(name:string){
 let pending=rendererLoads.get(name);if(!pending){pending=new Promise<void>((resolve,reject)=>{const s=document.createElement('script');s.src='/render/'+name+'.js';s.dataset.render=name;s.onload=()=>resolve();s.onerror=()=>{s.remove();rendererLoads.delete(name);reject(Error('渲染器加载失败'))};document.head.append(s)});rendererLoads.set(name,pending)}return pending;
}
export function TextLinks({body}:{body:string}){const parts=body.split(/(https?:\/\/[^\s<>]+)/g);return <>{parts.map((part,i)=>/^https?:\/\//.test(part)?<a key={i} href={part} target="_blank" rel="noopener noreferrer">{part}</a>:part)}</>}
