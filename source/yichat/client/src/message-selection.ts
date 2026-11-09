// Receipt metadata is incremental and may be absent after reopening a cached room.
// This is only an initial UI check; /eligible and /selection remain authoritative.
export function selectionAllowed(event:any,receipt?:{received_ms:number},now=Date.now()){
 const content=event.getContent(),received=receipt?.received_ms??event.getTs();
 return !event.isRedacted()&&!event.isDecryptionFailure()&&!content['org.yitiao.record']&&!/https:\/\/chat\.yitiao\.org\/records\//.test(content.body||'')&&Number.isFinite(received)&&received>0&&now-received>300000;
}
