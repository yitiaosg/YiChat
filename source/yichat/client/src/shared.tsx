import {useEffect,useRef,useState} from 'preact/hooks';
import type {ComponentChildren} from 'preact';
import {createPortal} from 'preact/compat';
import {X,AlertCircle} from 'lucide-preact';
export function useVisible(){const ref=useRef<HTMLDivElement>(null),[visible,setVisible]=useState(false);useEffect(()=>{const observer=new IntersectionObserver(([entry])=>setVisible(entry.isIntersecting),{rootMargin:'60px'});if(ref.current)observer.observe(ref.current);return()=>observer.disconnect()},[]);return {ref,visible}}
export function ErrorText({children}:{children:ComponentChildren}){return <p class="error" role="alert"><AlertCircle size={16}/>{children}</p>}
export function Modal({title,close,children}:{title:string;close:()=>void;children:ComponentChildren}){
  const ref=useRef<HTMLDivElement>(null);useEffect(()=>{const previous=document.activeElement as HTMLElement;ref.current?.querySelector<HTMLElement>('input,button,a')?.focus();const key=(e:KeyboardEvent)=>{if(e.key==='Escape')close();if(e.key==='Tab'){const items=ref.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),textarea,select,a[href]');if(!items?.length)return;const first=items[0],last=items[items.length-1];if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus()}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus()}}};document.addEventListener('keydown',key);return()=>{document.removeEventListener('keydown',key);previous?.focus()}},[]);
  return createPortal(<div class="modal-shade" onClick={e=>{if(e.target===e.currentTarget)close()}}><div class="modal" ref={ref} role="dialog" aria-modal="true" aria-label={title}><header><h2>{title}</h2><button type="button" class="icon" aria-label="关闭" onClick={close}><X/></button></header>{children}</div></div>,document.body)
}
const rendererLoads=new Map<string,Promise<void>>();
export function rendererScript(name:string){
 let pending=rendererLoads.get(name);if(!pending){pending=new Promise<void>((resolve,reject)=>{const s=document.createElement('script');s.src='/render/'+name+'.js';s.dataset.render=name;s.onload=()=>resolve();s.onerror=()=>{s.remove();rendererLoads.delete(name);reject(Error('渲染器加载失败'))};document.head.append(s)});rendererLoads.set(name,pending)}return pending;
}
export function TextLinks({body}:{body:string}){const parts=body.split(/(https?:\/\/[^\s<>]+)/g);return <>{parts.map((part,i)=>/^https?:\/\//.test(part)?<a key={i} href={part} target="_blank" rel="noopener noreferrer">{part}</a>:part)}</>}
