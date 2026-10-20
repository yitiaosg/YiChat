"""Actual browser/Web Crypto against the actual authenticated HTTP archive API."""
import os,json,time,threading,socket,subprocess
from pathlib import Path
from datetime import date
import httpx,uvicorn
from test_gateway import gateway
from backend.app import create_app
from backend import archive


def test_browser_download_and_local_hash_without_file_upload(gateway):
    c,app,s=gateway;day=date(2026,1,2);start,end=archive.day_bounds(day);now=int(time.time()*1000)
    rows=[('$join-old','!group',10,start-1,json.dumps({'type':'m.room.member','sender':'@alice:chat.yitiao.org','state_key':'@alice:chat.yitiao.org','content':{'membership':'join'}})),
        ('$old','!group',11,start+1,json.dumps({'type':'m.room.message','sender':'@alice:chat.yitiao.org','content':{'body':'isolated HTTP archive fixture'}}))]
    archive.ingest_batch(s.archive_ingest,rows,11,now,[(day,11,now)])
    archive.seal_day(s,'!group',day,'events-manifest',now)
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(create_app(s),host='127.0.0.1',port=port,access_log=False,log_level='error'))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    origin='http://127.0.0.1:'+str(port)
    try:
        for _ in range(100):
            if server.started:break
            time.sleep(.05)
        script=Path(__file__).parent/'browser'
        result=subprocess.run(['node','archive.mjs'],cwd=script,env=dict(os.environ,
            ARCHIVE_TEST_TOKEN='session-token',ARCHIVE_TEST_ORIGIN=origin,
            ARCHIVE_TEST_FILE=str(archive.artifact_path(s.archive_path,'!group',day,'events-manifest'))),capture_output=True,text=True,timeout=60)
        assert result.returncode==0,result.stderr
        assert json.loads(result.stdout)['uploadedFiles']==0
    finally:
        server.should_exit=True;thread.join(timeout=10)
