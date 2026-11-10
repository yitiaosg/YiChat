/** Events rebuilt from this device's own store re-enter the SDK timeline as
 * "live" events. They were delivered before and must never raise a notification. */
export class ReplayedEvents {
 private ids=new Set<string>();
 constructor(private limit=20000){}
 mark(events:Iterable<{getId():string|undefined}>){
  for(const event of events){const id=event.getId();if(!id)continue;this.ids.delete(id);this.ids.add(id)}
  while(this.ids.size>this.limit)this.ids.delete(this.ids.values().next().value!);
 }
 has(id:string|undefined){return !!id&&this.ids.has(id)}
}
type Candidate={getId():string|undefined;getSender():string|undefined;getWireType():string};
type ReadState={hasUserReadEvent?(user:string,event:string):boolean};
/** A notification is only for a newly delivered message from someone else that is still unread. */
export function shouldNotify(event:Candidate,room:ReadState,me:string,replayed:ReplayedEvents){
 const id=event.getId();
 if(!id?.startsWith('$')||event.getSender()===me||!['m.room.message','m.room.encrypted'].includes(event.getWireType()))return false;
 if(replayed.has(id))return false;
 try{if(room.hasUserReadEvent?.(me,id))return false}catch{/* Unknown read state: treat as unread. */}
 return true;
}
