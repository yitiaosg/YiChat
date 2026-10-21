"""Small authenticated group profile images, separate from message retention."""
import asyncio,hashlib,io,re,secrets,time,warnings
from fastapi import Request,Response
from PIL import Image,ImageOps,UnidentifiedImageError
from .db import connect,transaction
from .security import authenticate,fail,reserve_rate,upload_slot
from . import membership,policy

SCHEMA='''CREATE TABLE IF NOT EXISTS group_avatar_images(id TEXT PRIMARY KEY,room TEXT NOT NULL,uploader TEXT NOT NULL,png BLOB NOT NULL,created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS group_avatar_images_room ON group_avatar_images(room,created DESC);
CREATE TABLE IF NOT EXISTS group_avatar_refs(room TEXT PRIMARY KEY,id TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS group_avatar_refs_id ON group_avatar_refs(id);'''
MAX_BYTES=2000000

def access(s,who,room,owner=False):
    info=policy.room(s,room)
    if info['kind']!='group':fail(403,'只有群聊可以设置群头像')
    with connect(s.ledger) as c:membership.require(c,room,who['mxid'])
    if owner and info['owner']!=who['mxid']:fail(403,'只有群主可以设置群头像')

def normalize(raw):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                if image.format not in ('PNG','JPEG','WEBP') or max(image.size)>4096:fail(400,'请选择长宽不超过4096像素的 PNG、JPEG 或 WebP 图片')
                image.load()
                image=ImageOps.fit(ImageOps.exif_transpose(image).convert('RGBA'),(256,256),method=Image.Resampling.LANCZOS)
                image.info.clear();out=io.BytesIO();image.save(out,format='PNG');return out.getvalue()
    except (UnidentifiedImageError,OSError,ValueError,Image.DecompressionBombError,Image.DecompressionBombWarning):fail(400,'图片无效，请重新选择 PNG、JPEG 或 WebP 图片')

def prune(c,room):
    # Retain the published image and two recent pending uploads, never an
    # unbounded image history. Native profile state is independent of archives.
    c.execute("DELETE FROM group_avatar_images WHERE room=? AND id NOT IN (SELECT id FROM group_avatar_refs WHERE room=?) AND id NOT IN (SELECT id FROM group_avatar_images WHERE room=? ORDER BY created DESC LIMIT 2) AND NOT EXISTS (SELECT 1 FROM group_operations o JOIN group_admissions a USING(id) WHERE o.room=group_avatar_images.room AND o.action='avatar' AND o.done=0 AND a.expires>strftime('%s','now') AND json_extract(o.payload,'$.image')=group_avatar_images.id)",(room,room,room))

def publish(c,room,image):
    if image:c.execute('INSERT INTO group_avatar_refs VALUES(?,?) ON CONFLICT(room) DO UPDATE SET id=excluded.id',(room,image))
    else:c.execute('DELETE FROM group_avatar_refs WHERE room=?',(room,))
    prune(c,room)

def content(s,who,room,image):
    if image is None:return {}
    if not isinstance(image,str) or not re.fullmatch('[a-f0-9]{64}',image):fail(400,'无效的群头像')
    with connect(s.db) as c:r=c.execute('SELECT length(png) AS bytes FROM group_avatar_images WHERE id=? AND room=? AND uploader=?',(image,room,who['subject'])).fetchone()
    if not r:fail(409,'群头像上传已失效，请重新选择图片')
    return {'url':'mxc://'+s.server_name+'/ycga_'+image,'info':{'w':256,'h':256,'mimetype':'image/png','size':r['bytes']}}

def read(s,who,room,image):
    access(s,who,room)
    with connect(s.db) as c:r=c.execute('SELECT png FROM group_avatar_images i JOIN group_avatar_refs r ON i.room=r.room AND i.id=r.id WHERE i.room=? AND i.id=?',(room,image)).fetchone()
    if not r:fail(404,'群头像已更换或尚未设置')
    return Response(bytes(r['png']),media_type='image/png')

def install(app,s,session):
    @app.post('/api/groups/{room}/avatar')
    async def upload(request:Request,room:str):
        who=session(request);access(s,who,room,True)
        try:size=int(request.headers.get('content-length','0'))
        except ValueError:fail(400,'无效文件长度')
        if not 0<size<=MAX_BYTES:fail(413,'群头像上传最多2MB，且需要声明实际大小')
        lease=secrets.token_hex(24);await upload_slot(s,who['subject'],lease,size)
        try:
            raw=bytearray();iterator=request.stream().__aiter__()
            while len(raw)<size:
                await asyncio.sleep(reserve_rate(s,who['subject'],'upload',min(65536,size-len(raw)),s.upload_rate))
                authenticate(s,request.headers.get('authorization','').removeprefix('Bearer ') or request.cookies.get('yc_session',''))
                access(s,who,room,True)
                try:chunk=await asyncio.wait_for(iterator.__anext__(),30)
                except StopAsyncIteration:break
                if not chunk:break
                if len(raw)+len(chunk)>size:fail(413,'群头像实际大小超过声明')
                if len(chunk)>65536:await asyncio.sleep(reserve_rate(s,who['subject'],'upload',len(chunk)-65536,s.upload_rate))
                raw.extend(chunk)
            if len(raw)!=size:fail(400,'群头像上传不完整')
            png=await asyncio.to_thread(normalize,bytes(raw));access(s,who,room,True)
            image=hashlib.sha256(room.encode()+who['subject'].encode()+png).hexdigest()
            with transaction(s.db) as c:
                if policy.room(s,room)['owner']!=who['mxid']:fail(403,'群主已变更，请刷新群资料')
                c.execute('INSERT INTO group_avatar_images VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET created=excluded.created',(image,room,who['subject'],png,time.time()))
                prune(c,room)
            return {'id':image,'bytes':len(png),'width':256,'height':256}
        finally:
            with transaction(s.db) as c:c.execute('DELETE FROM upload_leases WHERE id=?',(lease,))
    @app.get('/api/groups/{room}/avatar/{image}')
    async def download(request:Request,room:str,image:str):return read(s,session(request),room,image)
