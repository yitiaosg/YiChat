"""Real TCP transport: in-memory clients hide HTTP body segmentation/backpressure."""
import socket,threading,time,hashlib,secrets
import httpx,pytest,uvicorn
from test_gateway import gateway
from backend.db import connect

@pytest.mark.parametrize('size',[65535,262144,700000])
@pytest.mark.parametrize('protocol',['legacy','chunks'])
def test_real_stream_upload_does_not_stall_on_transport_chunks(gateway,size,protocol):
    _,app,s=gateway
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,lifespan='off',log_level='error',access_log=False))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    body=b'u'*size
    try:
        for _ in range(100):
            if server.started:break
            time.sleep(.02)
        def chunks(data):
            for offset in range(0,len(data),16384):
                yield data[offset:offset+16384]
                time.sleep(.002)
        started=time.monotonic()
        with httpx.Client(base_url=f'http://127.0.0.1:{port}',trust_env=False,timeout=7,headers={'Authorization':'Bearer session-token','Origin':s.origin}) as client:
            if protocol=='legacy':
                result=client.post('/api/files/!direct',content=chunks(body),headers={'Content-Length':str(size),'Content-Type':'application/octet-stream'})
            else:
                result=client.post('/api/uploads',json={'room':'!direct','bytes':size,'key':secrets.token_hex(16)})
                assert result.status_code==200,result.text
                uid=result.json()['id']
                for offset in range(0,size,131072):
                    data=body[offset:offset+131072]
                    result=client.put('/api/uploads/'+uid,content=chunks(data),headers={'Content-Length':str(len(data)),'Content-Type':'application/octet-stream','X-Upload-Offset':str(offset)})
                    assert result.status_code==200,result.text
                    assert result.json()['offset']==offset+len(data)
                result=client.post('/api/uploads/'+uid+'/complete',json={})
        elapsed=time.monotonic()-started
        assert result.status_code==200,result.text
        assert result.json()['bytes']==size
        with connect(s.db) as c:assert c.execute('SELECT sha256 FROM file_hashes WHERE id=?',(result.json()['id'],)).fetchone()[0]==hashlib.sha256(body).hexdigest()
        assert elapsed<5,elapsed
    finally:
        server.should_exit=True;thread.join(timeout=10)
