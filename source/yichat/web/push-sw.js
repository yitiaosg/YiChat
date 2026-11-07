// YiChat Web Push worker. Registered by the Web version only, scope /push/:
// it has no fetch handler and never caches or intercepts page requests.
'use strict';
const ROOM=/^![A-Za-z0-9._=:-]{1,255}$/;
self.addEventListener('install',()=>self.skipWaiting());
self.addEventListener('activate',event=>event.waitUntil(self.clients.claim()));
self.addEventListener('push',event=>{
  let data={};
  try{data=event.data?event.data.json():{}}catch{data={}}
  const room=typeof data.room==='string'&&ROOM.test(data.room)?data.room:'';
  const title=typeof data.title==='string'&&data.title?data.title.slice(0,100):'YiChat';
  const body=typeof data.body==='string'&&data.body?data.body.slice(0,300):'你有新的消息';
  event.waitUntil(self.registration.showNotification(title,{
    body,tag:room||'yichat',renotify:true,icon:'/brand/yichat.png',badge:'/brand/yichat.png',data:{room}
  }));
});
self.addEventListener('notificationclick',event=>{
  event.notification.close();
  const room=event.notification.data&&ROOM.test(event.notification.data.room||'')?event.notification.data.room:'';
  event.waitUntil((async()=>{
    const windows=await self.clients.matchAll({type:'window',includeUncontrolled:true});
    for(const client of windows){
      if(new URL(client.url).origin!==self.location.origin)continue;
      await client.focus();
      if(room)client.postMessage({type:'yc:open-room',room});
      return;
    }
    await self.clients.openWindow('/'+(room?'?room='+encodeURIComponent(room):''));
  })());
});
