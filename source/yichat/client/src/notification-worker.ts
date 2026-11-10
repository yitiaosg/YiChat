import {Chat} from './chat';
import {appCall} from './app';
import {notificationContent,NotificationRequest,StaleNotification} from './notification-content';

const client=new Chat(true);
window.addEventListener('yc:notification-close',()=>client.lock('loading'),{once:true});
let request:NotificationRequest|undefined;
try {
 request=await appCall<NotificationRequest>('notificationRequest');
 await client.boot();
 const messages=await notificationContent(client,request);
 await client.client.store.save(true);
 client.lock('loading');
 await appCall('notificationResult',{id:request.id,ok:true,messages});
}catch(e){
 if(request)await appCall('notificationResult',{id:request.id,ok:false,...(e instanceof StaleNotification?{stale:true}:{})}).catch(()=>{});
}finally{client.lock('loading')}
