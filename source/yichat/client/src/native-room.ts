import * as sdk from 'matrix-js-sdk';
/** Locally restored rooms must forward the same public events as synced rooms. */
export function createNativeRoom(client:sdk.MatrixClient,roomId:string,mxid:string){
 const room=new sdk.Room(roomId,client,mxid,{pendingEventOrdering:sdk.PendingEventOrdering.Detached,lazyLoadMembers:true,timelineSupport:client.timelineSupport});
 client.reEmitter.reEmit(room,[sdk.RoomEvent.Name,sdk.RoomEvent.Redaction,sdk.RoomEvent.RedactionCancelled,sdk.RoomEvent.Receipt,sdk.RoomEvent.Tags,sdk.RoomEvent.LocalEchoUpdated,sdk.RoomEvent.AccountData,sdk.RoomEvent.MyMembership,sdk.RoomEvent.Timeline,sdk.RoomEvent.TimelineReset,sdk.RoomStateEvent.Events,sdk.RoomStateEvent.Members,sdk.RoomStateEvent.NewMember,sdk.RoomStateEvent.Update]);
 room.on(sdk.RoomStateEvent.NewMember,(_event,_state,member)=>{member.user=client.getUser(member.userId)||undefined;client.reEmitter.reEmit(member,[sdk.RoomMemberEvent.Name,sdk.RoomMemberEvent.Typing,sdk.RoomMemberEvent.PowerLevel,sdk.RoomMemberEvent.Membership])});
 return room;
}
