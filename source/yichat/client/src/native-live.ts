import {appCall} from './app';
/** Persist the visible wire event before waiting for archive receipts or media. */
export class NativeLiveMessages {
 private pending=new Map<string,Promise<unknown>>();private saved=new Set<string>();
 remember(room:string,event:any){
  const raw=event?.event||event,id=raw?.event_id;
  if(!id?.startsWith('$')||raw.room_id!==room||!['m.room.message','m.room.encrypted','m.room.redaction'].includes(raw.type))return Promise.resolve();
  const key=room+'|'+id;if(this.saved.has(key))return Promise.resolve();const prior=this.pending.get(key);if(prior)return prior;
  const task=appCall('messagePersist',{room,raw:JSON.stringify({...raw,unsigned:undefined})}).then(()=>{this.saved.add(key);if(this.saved.size>10000)this.saved.delete(this.saved.values().next().value!)}).finally(()=>this.pending.delete(key));this.pending.set(key,task);return task;
 }
 async room(room:string){await appCall('messageSyncRoom',{room})}
}
