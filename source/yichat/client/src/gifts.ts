type GiftOperation={
 type:string;
 card:{name:string};
 payload:{recipient:string;instance:string};
 result?:{id:string;instance:string;state:string};
};

// The message body holds only the stable owner-independent URL. Card designs
// are fetched at view time and are never embedded in a chat event.
export function giftMessage(operation:GiftOperation){
 const result=operation.result;
 if(operation.type!=='gift'||result?.state!=='transferred'||!/^[a-f0-9]{32}$/.test(result.id)||result.instance!==operation.payload.instance)throw Error('赠卡尚未完成，不能发送赠卡消息');
 return {msgtype:'m.text',body:'https://chat.yitiao.org/gifts/'+result.id};
}

// Older clients may carry only the accepted business reference. Both forms
// still require the authenticated gift lookup before showing a completed gift.
export function giftId(content:Record<string,any>):string|undefined{
 const share=content['org.yitiao.share'],business=content['org.yitiao.business'];
 const url=String(share?.url||content.body||'').trim().match(/^https:\/\/chat\.yitiao\.org\/gifts\/([a-f0-9]{32})$/);
 const id=url?.[1]||(business?.kind==='gift'?business.id:share?.type==='gift'?share.id:undefined);
 return typeof id==='string'&&/^[a-f0-9]{32}$/.test(id)?id:undefined;
}
