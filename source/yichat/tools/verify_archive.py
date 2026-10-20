#!/usr/bin/env python3
"""Streaming local SHA-256 verification. Only authenticated summary GET is online.
The file, plaintext and keys never leave this process. Token is read from stdin
or an environment variable, never a command argument or URL.
"""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request,urlopen,HTTPRedirectHandler,build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ValueError('Server redirect refused')


def verify(path,summary):
    h=hashlib.sha256();size=0
    with Path(path).open('rb') as f:
        while chunk:=f.read(1048576):h.update(chunk);size+=len(chunk)
    return h.hexdigest()==summary['sha256'] and size==summary['bytes']


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('file',type=Path);p.add_argument('--room',required=True);p.add_argument('--day',required=True)
    p.add_argument('--spec',choices=('events-manifest','with-attachments'),default='events-manifest')
    args=p.parse_args();token=os.getenv('YICHAT_ACCESS_TOKEN') or getpass.getpass('YiChat access token: ')
    url='https://chat.yitiao.org/api/archives/'+quote(args.room,safe='')+'/'+quote(args.day,safe='')+'/summary?spec='+args.spec
    request=Request(url,headers={'Authorization':'Bearer '+token,'Accept':'application/json'})
    with build_opener(NoRedirect).open(request,timeout=30) as r:
        if r.status!=200:raise ValueError('Authenticated summary unavailable')
        summary=json.load(r)
    if summary['room']!=args.room or str(summary['day'])!=args.day or summary['spec']!=args.spec or summary['version']!=1:raise ValueError('Summary scope mismatch')
    ok=verify(args.file,summary);print('VERIFIED' if ok else 'MISMATCH');return 0 if ok else 1


if __name__=='__main__':raise SystemExit(main())
