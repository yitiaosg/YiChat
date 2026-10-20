import asyncio
import re
from datetime import date,datetime,timezone
from pathlib import Path
from fastapi import Request
from fastapi.responses import FileResponse,JSONResponse,StreamingResponse
from . import archive
from .security import fail


def install(app,s,session):
    def validate(day,spec):
        try:date.fromisoformat(day)
        except ValueError:fail(400,'日期须为 YYYY-MM-DD UTC')
        if spec not in archive.SPECS:fail(400,'无效归档规格')
    def permitted(who,room,day,spec):
        validate(day,spec)
        try:archive.authorize(s.archive_reader,room,who['mxid'],day)
        except PermissionError as e:fail(403,{'errcode':'M_FORBIDDEN','code':'YICHAT_PARTIAL_HISTORY','error':str(e)})
    @app.get('/archives/')
    async def archive_page():return FileResponse(Path(__file__).parent.parent/'web'/'archives.html')
    @app.get('/api/archives/{room}/{day}/summary')
    async def summary(request:Request,room:str,day:str,spec:str='events-manifest'):
        who=session(request);permitted(who,room,day,spec)
        row=archive.summary(s.archive_reader,room,day,spec)
        if not row:
            fail(409,{'errcode':'M_UNKNOWN','code':'YICHAT_ARCHIVE_NOT_SEALED','error':'此日期尚未完整封存'})
        if row['has_gaps']:fail(503,{'errcode':'M_UNKNOWN','code':'YICHAT_ARCHIVE_QUARANTINED','error':'此日期存证出现缺口，完整日期下载和清理已暂停'})
        return row
    @app.get('/api/archives/{room}/{day}/download')
    async def download(request:Request,room:str,day:str,spec:str='events-manifest'):
        who=session(request);permitted(who,room,day,spec)
        row=archive.summary(s.archive_reader,room,day,spec)
        if not row:fail(409,'归档尚未完整封存')
        if row['has_gaps']:fail(503,'此日期存证出现缺口，完整日期下载已暂停')
        target=archive.artifact_path(s.archive_path,room,day,spec)
        if row['cleaned_at']:fail(410,'归档实体已清理；可信摘要仍可用于校验本地文件')
        if not target.is_file():fail(503,'归档实体缺失，下载和清理已暂停；可信摘要仍保留')
        if await asyncio.to_thread(archive.file_digest,target)!=(row['sha256'],row['bytes']):
            fail(503,'归档完整性检查失败，下载和清理已暂停')
        handle=target.open('rb')
        async def stream():
            with handle as f:
                while chunk:=f.read(65536):
                    session(request)
                    yield chunk
                    await asyncio.sleep(0)
        return StreamingResponse(stream(),media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="yichat-{day}-v1-{spec}.zip"','X-Archive-SHA256':row['sha256'],'Content-Length':str(row['bytes'])})
    @app.get('/api/archives/{room}/{day}/snapshot')
    async def snapshot(request:Request,room:str,day:str):
        # A provisional snapshot is deliberately a separate NDJSON range/spec and
        # never receives or replaces the sealed whole-day digest.
        who=session(request);validate(day,'events-manifest')
        if day!=datetime.now(timezone.utc).date().isoformat():fail(400,'快照仅用于 UTC 当天')
        now=int(datetime.now(timezone.utc).timestamp()*1000)
        fence=await app.state.matrix.fence()
        with archive.connect(s.archive_reader) as c:
            c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            checkpoint=c.execute("SELECT sequence,healthy FROM checkpoints WHERE source='synapse'").fetchone()
            if not checkpoint or not checkpoint['healthy'] or checkpoint['sequence']<fence:fail(503,'历史证据尚未追平，请重试')
            start,_=archive.day_bounds(day)
            rows,files,entries,mh=archive.contents(c,room,day,'events-manifest')
            through=max([now]+[r['received_ms']+1 for r in rows])
            if not archive.allowed(c,room,who['mxid'],start,through):fail(403,'没有完整快照范围的历史权限')
        body=entries['events.ndjson']
        return JSONResponse({'state':'provisional','spec':'snapshot-events-only-v1','room_id':room,'day':day,
            'first_sequence':rows[0]['sequence'] if rows else None,'last_sequence':rows[-1]['sequence'] if rows else None,
            'range_start_ms':start,'range_end_ms':through,'sha256':archive.sha256(body),'events_ndjson':body.decode(),'complete_day':False})
