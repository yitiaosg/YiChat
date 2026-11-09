/** Display bytes from an explicit local package; trust remains a whole-day decision. */
export function localFileContent(content:any,files:any[]){const id=String(content.url||'').split('/').pop(),file=files.find(f=>f.id===id);return file?.data?{...content,'org.yitiao.local_file':file}:content}
export function localMaterialDocument(doc:any,files:any[]){
 if(doc.kind==='media')return {...doc,content:localFileContent(doc.content,files)};
 if(doc.kind==='record')return {...doc,record:{...doc.record,payload:{...doc.record.payload,messages:doc.record.payload.messages.map((m:any)=>({...m,content:localFileContent(m.content,files)}))}}};
 return doc;
}
