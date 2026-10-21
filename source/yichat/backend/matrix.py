from urllib.parse import quote
from fastapi import HTTPException
from .security import fail

class Matrix:
    def __init__(self, settings, http, cipher): self.s,self.http,self.cipher=settings,http,cipher
    async def call(self, method, path, token=None, data=None, params=None):
        r=await self.http.request(method,self.s.synapse+path,headers={'Authorization':'Bearer '+(token or self.s.admin_token)},json=data,params=params,timeout=40)
        if r.status_code>=400:
            raise HTTPException(r.status_code, r.json(),headers={'Retry-After':r.headers['Retry-After']} if 'Retry-After' in r.headers else None)
        return r.json() if r.content else {}
    def token(self, session): return self.cipher.decrypt(session['upstream'].encode()).decode()
    async def membership(self, session, room):
        return await self.member(session,room,session['mxid'])
    async def member(self, session, room, mxid):
        try:
            m=await self.call('GET',f'/_matrix/client/v3/rooms/{quote(room,safe="")}/state/m.room.member/{quote(mxid,safe="")}',self.token(session))
        except HTTPException: fail(403,'你不是此会话的有效成员')
        if m.get('membership')!='join': fail(403,'你不是此会话的有效成员')
    async def provision(self, mxid, password, username):
        await self.call('PUT','/_synapse/admin/v2/users/'+quote(mxid,safe=''),data={'displayname':username, 'password':password, 'logout_devices':False, 'admin':False})
    async def login(self, mxid, password, device_id=None, device_name="YiChat 浏览器"):
        data={'type':'m.login.password','identifier':{'type':'m.id.user','user':mxid},'password':password,'initial_device_display_name':device_name}
        if device_id:data['device_id']=device_id
        return await self.call('POST','/_matrix/client/v3/login',data=data)
    async def fence(self):
        result=await self.call('GET','/_synapse/client/yichat/fence')
        if type(result.get('sequence')) is not int:fail(503,'历史权限服务尚未就绪')
        return result['sequence']
