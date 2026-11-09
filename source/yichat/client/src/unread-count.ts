export function unreadCount(room:any,pref?:{read_sequence?:number;latest_event?:string;unread_count?:number}){
 const count=room.getUnreadNotificationCount();
 if(!pref?.read_sequence||pref.unread_count===undefined)return count;
 const latest=room.getLiveTimeline().getEvents().filter((e:any)=>['m.room.message','m.room.encrypted'].includes(e.getWireType())).at(-1);
 return latest?.getId()===pref.latest_event?Math.min(count,pref.unread_count):count;
}
