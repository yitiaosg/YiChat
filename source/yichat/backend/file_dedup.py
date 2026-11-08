"""Coalesce verified identical bytes without exposing cross-account hash probes.
Logical file IDs and their independently authorized lifetimes remain unchanged.
Call while holding the gateway SQLite write transaction, after hash insertion.
"""
import os,secrets

def coalesce(c,s,fid,sha256):
    target=s.objects/fid
    for row in c.execute('SELECT f.id FROM files f JOIN file_hashes h USING(id) WHERE h.sha256=? AND f.id<>? AND f.removed=0',(sha256,fid)):
        source=s.objects/row['id']
        if not source.is_file():continue
        if os.path.samefile(source,target):return
        link=s.objects/(fid+'.'+secrets.token_hex(6)+'.link')
        try:
            os.link(source,link);os.replace(link,target)
            directory=os.open(s.objects,os.O_RDONLY)
            try:os.fsync(directory)
            finally:os.close(directory)
        finally:link.unlink(missing_ok=True)
        return
