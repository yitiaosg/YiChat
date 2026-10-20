"""Deterministic synthetic format vectors, not real messages or decryptable E2EE."""
from pathlib import Path
import tempfile,json,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.archive import canonical,write_zip,sha256,file_digest,PERMISSION_SPEC


def main():
    out=Path(__file__).resolve().parents[1]/'docs/samples';out.mkdir(exist_ok=True)
    room='!synthetic-private-example:chat.yitiao.org';day='2026-01-02'
    ciphertext=b'YICHAT SYNTHETIC ATTACHMENT CIPHERTEXT FORMAT VECTOR\n'
    fid='0'*48
    files=[dict(id=fid,bytes=len(ciphertext),sha256=sha256(ciphertext),received_ms=1767312000001,uploader='@synthetic:chat.yitiao.org',event_ids=['$synthetic'])]
    events=canonical(dict(event={'event_id':'$synthetic','room_id':room,'type':'m.room.encrypted','sender':'@synthetic:chat.yitiao.org','content':{'algorithm':'m.megolm.v1.aes-sha2','ciphertext':'SYNTHETIC_FORMAT_VECTOR_NOT_A_REAL_MESSAGE','session_id':'synthetic','sender_key':'synthetic','device_id':'synthetic','org.yitiao.attachments':[fid]}},event_id='$synthetic',room_id=room,sender='@synthetic:chat.yitiao.org',sequence=1,received_ms=1767312000000))
    summaries={}
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp)/fid).write_bytes(ciphertext)
        for spec in ('events-manifest','with-attachments'):
            entries={'archive.json':canonical(dict(day=day,format='yichat-archive',permission_spec=PERMISSION_SPEC,room_id=room,spec=spec,version=1)),
                'events.ndjson':events,'attachments.json':canonical(dict(attachments=files,day=day,room_id=room,version=1))}
            path=out/('synthetic-v1-'+spec+'.zip');write_zip(path,entries,files,Path(tmp),spec=='with-attachments')
            digest,size=file_digest(path);summaries[spec]=dict(sha256=digest,bytes=size,room=room,day=day,version=1,spec=spec)
        (out/'expected.json').write_bytes(canonical(summaries))
    print('Synthetic vectors generated deterministically; no user data or keys included.')


if __name__=='__main__':main()
