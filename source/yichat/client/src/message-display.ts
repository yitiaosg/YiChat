import {shareUrls} from './api';
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
 return messageBody(content,urls)||(urls.length?'分享内容':String(content.body||''));
}
