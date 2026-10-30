// Private preview content only. Callers supply an account/device-scoped store.
export const PREVIEW_BYTES=24*1024*1024,PREVIEW_COUNT=160;
export type PreviewEntry={value:any;until:number;at:number;bytes:number};
export type PreviewStore={read:(key:string)=>Promise<PreviewEntry|undefined>;write:(key:string,row:PreviewEntry)=>Promise<void>;remove:(key:string)=>Promise<void>};
export function previewLifetime(value:any){
 if(['game','transaction','doudizhu_record'].includes(value?.type))return 30*86400000;
 if(value?.type==='card')return 60000;
 if(value?.type==='order')return 5000;
 return 20000; // Live room invitations, gifts and unavailable resources.
}
export class PreviewCache {
 private rows=new Map<string,PreviewEntry>();private bytes=0;private pending=new Map<string,Promise<any>>();private closed=false;
 constructor(private store?:PreviewStore,private now=Date.now){}
 private check(){if(this.closed)throw new DOMException('会话已锁定','AbortError')}
 private remember(key:string,row:PreviewEntry){
  this.bytes-=this.rows.get(key)?.bytes||0;this.rows.delete(key);
  if(row.bytes>PREVIEW_BYTES)return;
  while(this.rows.size&&(this.bytes+row.bytes>PREVIEW_BYTES||this.rows.size>=PREVIEW_COUNT)){const first=this.rows.keys().next().value!;this.bytes-=this.rows.get(first)!.bytes;this.rows.delete(first)}
  if(row.bytes<=PREVIEW_BYTES){this.rows.set(key,row);this.bytes+=row.bytes}
 }
 get(key:string,load:()=>Promise<any>,force=false):Promise<any>{
  this.check();const existing=this.pending.get(key);
  if(existing)return force?existing.catch(()=>{}).then(()=>this.get(key,load,true)):existing;
  const task=this.read(key,load,force).finally(()=>{if(this.pending.get(key)===task)this.pending.delete(key)});this.pending.set(key,task);return task;
 }
 private async read(key:string,load:()=>Promise<any>,force:boolean){
  if(!force){
   let row=this.rows.get(key);
   if(!row&&this.store)try{row=await this.store.read(key)}catch{/* A missing/corrupt cache must not block a live preview. */}
   this.check();if(row&&Number.isFinite(row.until)&&row.until>this.now()&&row.until<=this.now()+30*86400000&&Number.isFinite(row.bytes)&&row.bytes>=0&&row.bytes<=PREVIEW_BYTES){this.remember(key,row);return row.value}
  }
  try{
   const value=await load();this.check();const at=this.now(),row={value,at,until:at+previewLifetime(value),bytes:JSON.stringify(value??null).length*2};
   this.remember(key,row);if(this.store)try{await this.store.write(key,row)}catch{/* Cache quota/write failures do not fail the request. */}
   this.check();return value;
  }catch(e:any){
   if([401,403,404,410].includes(e.status)){this.bytes-=this.rows.get(key)?.bytes||0;this.rows.delete(key);if(this.store)try{await this.store.remove(key)}catch{}}
   throw e;
  }
 }
 close(){this.closed=true;this.rows.clear();this.bytes=0}
}
