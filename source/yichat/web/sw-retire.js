// Migration only: retire the old app shell without deleting IndexedDB keys or archives.
self.addEventListener('install',()=>self.skipWaiting());
self.addEventListener('activate',event=>event.waitUntil((async()=>{
  for(const key of await caches.keys())if(key.startsWith('yichat-shell-'))await caches.delete(key);
  await self.clients.claim();
  await self.registration.unregister();
  const clients=await self.clients.matchAll({type:'window',includeUncontrolled:true});
  for(const client of clients)await client.navigate(client.url);
})()));
