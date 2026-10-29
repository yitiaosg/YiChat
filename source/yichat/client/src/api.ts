export class ApiError extends Error {
  constructor(public status:number, public data:Record<string,any>){super(data.error||`请求失败 (${status})`)}
}
export const sleep=(ms:number,signal?:AbortSignal)=>new Promise<void>((resolve,reject)=>{
  if(signal?.aborted){reject(new DOMException('已取消','AbortError'));return}
  const end=()=>{clearTimeout(timer);reject(new DOMException('已取消','AbortError'))};
  const timer=setTimeout(()=>{signal?.removeEventListener('abort',end);resolve()},ms);signal?.addEventListener('abort',end,{once:true});
});
export function retryDelay(response:Response,data:any,now=Date.now()){
  const header=response.headers.get('Retry-After');
  const seconds=header&&/^\d+(\.\d+)?$/.test(header)?parseFloat(header)*1000:header?Math.max(0,Date.parse(header)-now):0;
  return Math.max(1000,seconds||0,Number(data?.retry_after_ms)||0);
}
export class Requests {
  token='';scope='anonymous';stopped=false;onUnauthorized=()=>{};
  private next=0;private tail:Promise<any>=Promise.resolve();private pending=new Map<string,Promise<any>>();
  private controllers=new Set<AbortController>();
  // One budget for SDK, profiles, business, media and previews. Six starts/s
  // leaves headroom for the main site and other devices; 429 shares a cooldown.
  async slot(signal?:AbortSignal){
    const run=async()=>{
      const reserve=async()=>{const key='yc:rate:'+this.scope;let next=this.next;try{next=Math.max(next,parseInt(localStorage.getItem(key)||'0')||0)}catch{}
        await sleep(Math.max(0,next-Date.now()),signal);if(this.stopped)throw new DOMException('会话已锁定','AbortError');
        this.next=Date.now()+175;try{localStorage.setItem(key,String(this.next))}catch{}
      };
      if(navigator.locks)await navigator.locks.request('yc:rate:'+this.scope,reserve);else await reserve();
    };
    const task=this.tail.then(run,run);this.tail=task.catch(()=>{});await task;
  }
  readonly fetch:typeof fetch=async(input,init={})=>{
    const target=new URL(typeof input==='string'?input:input instanceof URL?input.href:input.url,location.origin);
    if(target.origin!==location.origin)throw Error('只允许同源网关请求');
    const controller=new AbortController();this.controllers.add(controller);
    const abort=()=>controller.abort();init.signal?.addEventListener('abort',abort,{once:true});if(init.signal?.aborted)abort();
    try{
      for(let attempt=0;attempt<5;attempt++){
        await this.slot(controller.signal);
        const headers=new Headers(init.headers);if(this.token)headers.set('Authorization','Bearer '+this.token);
        const response=await fetch(input,{...init,headers,signal:controller.signal,credentials:'same-origin',cache:'no-store'});
        if(response.status===401){this.onUnauthorized();return response}
        if(response.status!==429)return response;
        const data=await response.clone().json().catch(()=>({}));const delay=retryDelay(response,data)+Math.random()*250;
        this.next=Math.max(this.next,Date.now()+delay);try{localStorage.setItem('yc:rate:'+this.scope,String(this.next))}catch{}
        if(attempt===4)return response;
      }
      throw Error('请求失败');
    }finally{this.controllers.delete(controller);init.signal?.removeEventListener('abort',abort)}
  };
  async api<T=any>(path:string,body?:unknown,method?:string,signal?:AbortSignal):Promise<T>{
    const response=await this.fetch(path,{method:method||(body===undefined?'GET':'POST'),headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),signal});
    const data=await response.json().catch(()=>({}));if(!response.ok)throw new ApiError(response.status,data);return data;
  }
  single<T>(key:string,fn:()=>Promise<T>):Promise<T>{
    const existing=this.pending.get(key);if(existing)return existing;
    const task=fn().finally(()=>this.pending.delete(key));this.pending.set(key,task);return task;
  }
  close(){this.stopped=true;this.token='';for(const c of this.controllers)c.abort();this.controllers.clear();this.pending.clear()}
}
export const requests=new Requests();
export const path=(value:string)=>encodeURIComponent(value);
export const codepoints=(value:string)=>Array.from(value).length;
export const UNIT=10n**16n;
export function units(value:string){if(!/^\d+(\.\d{1,16})?$/.test(value)||value.length>100)throw Error('金额最多支持 16 位小数');const [a,b='']=value.split('.');return BigInt(a)*UNIT+BigInt(b.padEnd(16,'0'))}
export function decimal(value:string|bigint){const n=BigInt(value),sign=n<0n?'-':'',v=n<0n?-n:n;return sign+(v/UNIT).toString()+((v%UNIT)?'.'+(v%UNIT).toString().padStart(16,'0').replace(/0+$/,''):'')}
export function shareUrls(body:string){
  const links=(body.match(/https:\/\/[^\s<>"\u3000]+/g)||[]).map(url=>url.replace(/[，。！？、；：）】》」』.,!?;:)]+$/u,''));
  return [...new Set(links.filter(url=>/^(https:\/\/yidt\.yitiao\.org\/explorer\/tx\/[a-f0-9]{64}|https:\/\/yitiao\.org\/ycards\/card\/[a-f0-9]{32}\/[A-Za-z0-9_-]{1,100}\/|https:\/\/yitiao\.org\/games\/doudizhu\/share\/[A-Za-z0-9_-]{32}\/|https:\/\/yitiao\.org\/games\/doudizhu\/room\/\d{5}\/[a-f0-9]{32}\/|https:\/\/yitiao\.org\/games\/parachute\/share\/[A-Za-z0-9_-]{32}\/|https:\/\/chat\.yitiao\.org\/(?:orders|gifts)\/[A-Za-z0-9_-]{32})$/.test(url)))].slice(0,4);
}
