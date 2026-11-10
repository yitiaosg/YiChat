/** SDK cache order is not an authority for the latest accepted room message. */
type RoomEvent={getRoomId:()=>string|undefined;getId:()=>string|undefined;getTs:()=>number;getType:()=>string;getWireType:()=>string;getUnsigned:()=>Record<string,any>};
type Receipt={received_ms:number;sequence:number};
export function receivedAt(event:RoomEvent,receipts?:ReadonlyMap<string,Receipt>){return Number(receipts?.get(event.getId()!)?.received_ms||event.getUnsigned()['org.yitiao.received_ms']||event.getTs())||0}
function compare(a:RoomEvent,b:RoomEvent,receipts?:ReadonlyMap<string,Receipt>){
 const left=Number(receipts?.get(a.getId()!)?.sequence||a.getUnsigned()['org.yitiao.sequence']),right=Number(receipts?.get(b.getId()!)?.sequence||b.getUnsigned()['org.yitiao.sequence']);
 return Number.isFinite(left)&&Number.isFinite(right)&&left!==right?left-right:receivedAt(a,receipts)-receivedAt(b,receipts);
}
export function orderedRoomEvents<T extends RoomEvent>(room:string,events:readonly T[],receipts?:ReadonlyMap<string,Receipt>):T[]{
 return events.filter(e=>e.getRoomId()===room).map((event,index)=>({event,index})).sort((a,b)=>compare(a.event,b.event,receipts)||a.index-b.index).map(row=>row.event);
}
export function latestRoomMessage<T extends RoomEvent>(room:string,events:readonly T[],receipts?:ReadonlyMap<string,Receipt>):T|undefined {
 let latest:T|undefined;
 for(const event of events)if(event.getRoomId()===room&&(event.getType()==='m.room.message'||event.getWireType()==='m.room.encrypted')&&(!latest||compare(event,latest,receipts)>=0))latest=event;
 return latest;
}
