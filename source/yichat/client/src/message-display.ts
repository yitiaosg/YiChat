import {recordHash} from './record-model';
import {shareUrls} from './api';
export function standaloneContent(content:Record<string,any>,previews:string[]=[],gift=false){
 return !!recordHash(content)||gift||previews.length>0||['m.image','m.audio','m.video','m.file'].includes(content.msgtype);
}
// Only remove a URL when that exact resource has a preview in this message.
// Keep captions, other websites, unrecognized routes and unrendered extra URLs.
export function messageBody(content:Record<string,any>,previews:string[]=[]):string {
 const body=typeof content.body==='string'?content.body:'';
 const displayed=new Set(previews);
 return body.replace(/https:\/\/[^\s<>"\u3000]+/g,token=>{
  const url=token.replace(/[，。！？、；：）】》」』.,!?;:)]+$/u,'');
  return displayed.has(url)?token.slice(url.length):token;
 }).trim();
}
export function messageCaption(content:Record<string,any>):string {
 const urls=content['org.yitiao.no_preview']?[]:shareUrls(content.body||'');
 if(recordHash(content))return '[聊天记录]';
 const body=messageBody(content,urls);if(body&&!body.startsWith('https://'))return body;
 const share=content['org.yitiao.share'];
 const media:Record<string,string>={'m.image':'[图片]','m.audio':'[语音]','m.video':'[视频]','m.file':'[文件]'};
 if(media[content.msgtype])return media[content.msgtype]+(content.body?' '+content.body:'');
 if(share?.type==='receipt')return '[已收款]';
 if(content['org.yitiao.business']?.kind==='order'||urls.some(u=>u.includes('/orders/')))return '[转账 / 红包]';
 if(share?.type==='gift'||urls.some(u=>u.includes('/gifts/')))return '[赠卡]';
 if(urls.some(u=>u.includes('/records/')))return '[游戏记录]';
 if(urls.some(u=>u.includes('/ycards/card/')))return '[卡片]';
 if(urls.some(u=>u.includes('/explorer/tx/')))return '[YIDT 交易]';
 if(urls.some(u=>u.includes('/doudizhu/room/')))return '[斗地主房间]';
 return body||(urls.length?'[站内分享]':String(content.body||''));
}
