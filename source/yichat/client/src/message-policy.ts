export const MESSAGE_EPOCH='plain-20261029-v1';
export function chatStoreScope(device:string,epoch:string=MESSAGE_EPOCH){return epoch+':'+device}
export async function clearLegacyChatStores(){
 if(typeof indexedDB.databases!=='function')return;
 for(const db of await indexedDB.databases()){
  if(!db.name||!['yc-sync:','yc-rust:','yc-vault:'].some(prefix=>db.name!.startsWith(prefix))||db.name.includes(MESSAGE_EPOCH)||db.name.startsWith('yc-vault:account:'))continue;
  await new Promise<void>((resolve,reject)=>{const request=indexedDB.deleteDatabase(db.name!);request.onsuccess=()=>resolve();request.onerror=()=>reject(request.error);request.onblocked=()=>reject(Error('请关闭其他 YiChat 页面后重新打开，以清理旧聊天缓存'))});
 }
}
