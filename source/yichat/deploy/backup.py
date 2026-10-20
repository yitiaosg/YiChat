"""Root-only online snapshots and private key/config backup. No secret output.
These are individual online DB snapshots; a disaster restore across services needs
an operator-coordinated consistent point, not arbitrary mixing of generations.
"""
import os,subprocess,sqlite3,tarfile,json
from pathlib import Path
from datetime import datetime,timezone
import yaml


def main():
    if os.geteuid()!=0:raise SystemExit('Run with sudo')
    base=Path('/root/yitiao');destination=base/'backups'/('yichat-v2-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    destination.mkdir(mode=0o700)
    from setup import read_env
    env=read_env(Path('/etc/yichat/yichat.env'));gateway=Path(env.get('YICHAT_DATA','/var/lib/yichat/gateway'))
    config=yaml.safe_load(Path('/etc/yichat/synapse.yaml').read_text());source=Path(config['database']['args']['database'])
    for db in (base/'account.db',base/'userdata.db',gateway/'chat.db',source):
        with sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True) as src,sqlite3.connect(destination/db.name) as out:src.backup(out)
        (destination/db.name).chmod(0o600)
    with tarfile.open(destination/'private-config-and-keys.tar.gz','w:gz') as bundle:
        for path in (Path('/etc/yichat'),Path('/etc/caddy/Caddyfile'),base/'.yidt_key',base/'.secret_key',base/'.oidc_key.pem',Path(config['signing_key_path'])):
            if path.exists():bundle.add(path,arcname=str(path).lstrip('/'))
    (destination/'private-config-and-keys.tar.gz').chmod(0o600)
    with (destination/'archive.pgdump').open('wb') as out:
        subprocess.run(['sudo','-u','postgres','pg_dump','-Fc','yichat_archive'],stdout=out,check=True)
    (destination/'archive.pgdump').chmod(0o600)
    commit=subprocess.check_output(['git','-C',str(base),'rev-parse','HEAD'],text=True).strip()
    (destination/'manifest.json').write_text(json.dumps({'created_utc':datetime.now(timezone.utc).isoformat(),'source_commit':commit,'note':'Individual online snapshots; objects and archive entities intentionally excluded.'},indent=2))
    print('Private backup written:',destination)


if __name__=='__main__':main()
