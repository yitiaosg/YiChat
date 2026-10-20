"""Bound actual request bytes, including requests without Content-Length."""
import asyncio
from fastapi import HTTPException

class BodyLimitMiddleware:
    def __init__(self, app, settings): self.app,self.settings=app,settings
    async def __call__(self, scope, receive, send):
        if scope['type']!='http':return await self.app(scope,receive,send)
        upload=scope['method']=='POST' and (scope['path'].startswith('/api/files/') or scope['path'] in ('/_matrix/media/v3/upload','/_matrix/client/v1/media/upload'))
        limit=self.settings.max_file if upload else 262144
        total=0
        async def bounded():
            nonlocal total
            try:event=await asyncio.wait_for(receive(),30)
            except asyncio.TimeoutError:raise HTTPException(408,'上传连接空闲超时')
            if event['type']=='http.request':
                total+=len(event.get('body',b''))
                if total>limit:raise HTTPException(413,'请求体过大')
            return event
        await self.app(scope,bounded,send)
