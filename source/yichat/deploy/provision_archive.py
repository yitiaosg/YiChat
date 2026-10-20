#!/usr/bin/env python3
"""Provision the independent archive PostgreSQL database and least-privilege roles.
Secrets are generated once into a mode-0600 manifest, never printed. Existing
databases/roles are preserved; a missing manifest with existing roles is an error.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import psycopg
from psycopg.conninfo import make_conninfo


def admin(sql):
    subprocess.run(['sudo','-u','postgres','psql','-v','ON_ERROR_STOP=1','-q'],input=sql,text=True,check=True,stdout=subprocess.DEVNULL)


def provision(test=False):
    prefix='yc_test_' if test else 'yc_'
    db='yichat_archive_test' if test else 'yichat_archive'
    path=Path.home()/'.cache/yichat-test-dsns.json' if test else Path('/etc/yichat/archive-dsns.json')
    if path.exists():
        dsns=json.loads(path.read_text())
        sql=Path(__file__).with_name('archive.sql').read_text()
        if test:sql=sql.replace('yc_ingest','yc_test_ingest').replace('yc_seal','yc_test_seal').replace('yc_read','yc_test_read').replace('yc_cleanup','yc_test_cleanup')
        with psycopg.connect(dsns['owner']) as c:c.execute(sql)
        return dsns
    passwords={kind:secrets.token_urlsafe(36) for kind in ('owner','ingest','seal','read','cleanup')}
    # All identifiers are fixed constants; password alphabet is SQL-safe URL base64.
    for role,password in passwords.items():
        admin(f"CREATE ROLE {prefix+role} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD '{password}';")
    admin(f'CREATE DATABASE {db} OWNER {prefix}owner;')
    dsns={role:make_conninfo(host='127.0.0.1',dbname=db,user=prefix+role,password=password) for role,password in passwords.items()}
    with psycopg.connect(dsns['owner']) as c:
        sql=Path(__file__).with_name('archive.sql').read_text()
        if test:sql=sql.replace('yc_ingest','yc_test_ingest').replace('yc_seal','yc_test_seal').replace('yc_read','yc_test_read').replace('yc_cleanup','yc_test_cleanup')
        c.execute(sql)
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:json.dump(dsns,f)
    return dsns


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--test',action='store_true');args=p.parse_args()
    provision(args.test);print('Archive PostgreSQL roles/database ready; credentials saved privately.')
