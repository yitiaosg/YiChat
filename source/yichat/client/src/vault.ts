// Only the active, authenticated device opens its vault. Encrypted local outboxes are preserved
// on logout and are never shared with a new device.
export class Vault {
  private writes:Promise<void>=Promise.resolve();db!:IDBDatabase; key!:CryptoKey;
  async open(scope:string){
    this.db=await new Promise((resolve,reject)=>{const r=indexedDB.open('yc-vault:'+scope,2);r.onupgradeneeded=()=>{if(!r.result.objectStoreNames.contains('data'))r.result.createObjectStore('data');if(!r.result.objectStoreNames.contains('packets')){const store=r.result.createObjectStore('packets',{keyPath:'id'});store.createIndex('at','at')}};r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error)});
    this.key=await this.get('wrap')||await crypto.subtle.generateKey({name:'AES-GCM',length:256},false,['encrypt','decrypt']);
    await this.set('wrap',this.key);
  }
  async clearChatState(){
    const keys:IDBValidKey[]=await new Promise((resolve,reject)=>{const r=this.db.transaction('data').objectStore('data').getAllKeys();r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error)});
    await new Promise<void>((resolve,reject)=>{const tx=this.db.transaction(['data','packets'],'readwrite');
      for(const key of keys)if(typeof key==='string'&&(key==='app-ui'||key.startsWith('history')||key.startsWith('recall:')))tx.objectStore('data').delete(key);
      tx.objectStore('packets').clear();tx.oncomplete=()=>resolve();tx.onerror=()=>reject(tx.error);
    });
  }
  get(key:string):Promise<any>{return new Promise((resolve,reject)=>{const r=this.db.transaction('data').objectStore('data').get(key);r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error)})}
  set(key:string,value:any){return new Promise<void>((resolve,reject)=>{const tx=this.db.transaction('data','readwrite');tx.objectStore('data').put(value,key);tx.oncomplete=()=>resolve();tx.onerror=()=>reject(tx.error)})}
  private sealed(key:string,value:unknown){const plain=new TextEncoder().encode(JSON.stringify(value));const task=this.writes.then(async()=>{const iv=crypto.getRandomValues(new Uint8Array(12));const data=await crypto.subtle.encrypt({name:'AES-GCM',iv},this.key,plain);await this.set(key,{iv,data})});this.writes=task.catch(()=>{});return task}
  async saveOutbox(value:unknown){await this.sealed('outbox',value)}
  async outbox(){const row=await this.get('outbox');return row?JSON.parse(new TextDecoder().decode(await crypto.subtle.decrypt({name:'AES-GCM',iv:row.iv},this.key,row.data))):[]}
  async saveBusiness(value:unknown){await this.sealed('business',value)}
  async business(){const row=await this.get('business');return row?JSON.parse(new TextDecoder().decode(await crypto.subtle.decrypt({name:'AES-GCM',iv:row.iv},this.key,row.data))):[]}
  async saveState(key:string,value:unknown){await this.sealed(key,value)}
  async state(key:string){const row=await this.get(key);return row?JSON.parse(new TextDecoder().decode(await crypto.subtle.decrypt({name:'AES-GCM',iv:row.iv},this.key,row.data))):undefined}
  async packet(id:string){const row:any=await new Promise((resolve,reject)=>{const r=this.db.transaction('packets').objectStore('packets').get(id);r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error)});return row?JSON.parse(new TextDecoder().decode(await crypto.subtle.decrypt({name:'AES-GCM',iv:row.iv},this.key,row.data))):undefined}
  async putPacket(id:string,value:any){const iv=crypto.getRandomValues(new Uint8Array(12)),data=await crypto.subtle.encrypt({name:'AES-GCM',iv},this.key,new TextEncoder().encode(JSON.stringify(value)));await new Promise<void>((resolve,reject)=>{const tx=this.db.transaction('packets','readwrite');tx.objectStore('packets').put({id,room:value.receipt.room,iv,data,at:value.receipt.received_ms,bytes:data.byteLength});tx.oncomplete=()=>resolve();tx.onerror=()=>reject(tx.error)})}
  async storedPackets(){return new Promise<any[]>((resolve,reject)=>{const r=this.db.transaction('packets').objectStore('packets').index('at').getAll();r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error)})}
  async prunePackets(){const rows=await this.storedPackets();let bytes=rows.reduce((n,r)=>n+r.bytes,0),count=rows.length;const remove:string[]=[];for(const row of rows){if(bytes<=100*1024*1024&&count<=10000)break;remove.push(row.id);bytes-=row.bytes;count--}if(remove.length)await new Promise<void>((resolve,reject)=>{const tx=this.db.transaction('packets','readwrite');for(const id of remove)tx.objectStore('packets').delete(id);tx.oncomplete=()=>resolve();tx.onerror=()=>reject(tx.error)})}
  close(){this.db?.close();this.key=undefined!}
}
