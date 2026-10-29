import {shareUrls} from './api';
import {giftId} from './gifts';
export function notificationText(content:Record<string,any>,kind:'private'|'group'='private'):string {
 const group=kind==='group',say=(action:string)=>(group?'':'对方')+action;
 const share=content['org.yitiao.share'],business=content['org.yitiao.business'];
 const urls=shareUrls(typeof content.body==='string'?content.body:'');
 const order=business?.kind==='order'||urls.some(url=>url.startsWith('https://chat.yitiao.org/orders/'));
 if(share?.type==='receipt'&&order)return say('已确认收款');
 if(giftId(content))return say(group?'赠送了一张卡片':'向你赠送了一张卡片');
 if(order)return say(group?'发送了一个红包':'向你转了一笔账');
 const media:Record<string,string>={'m.image':'发送了一张图片','m.audio':'发送了一条语音','m.video':'发送了一段视频','m.file':'发送了一个文件','m.location':'分享了一个位置'};
 if(media[content.msgtype])return say(media[content.msgtype]);
 if(urls.length){
  const url=urls[0];
  if(url.includes('/ycards/card/'))return say('分享了一张卡片');
  if(url.includes('/explorer/tx/'))return say('分享了一笔 YIDT 交易');
  if(url.includes('/games/doudizhu/room/'))return say(group?'分享了斗地主房间':'邀请你加入斗地主房间');
  if(url.includes('/games/'))return say('分享了一条游戏记录');
 }
 return typeof content.body==='string'?content.body.slice(0,2000):say('发送了一条消息');
}
