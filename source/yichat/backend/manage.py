"""Run with sudo: python -m backend.manage migrate|reconcile|status|archive-once."""
import sys,json,os,shlex,asyncio
from pathlib import Path


def load_env(path):
    if not path.exists():return
    for line in path.read_text().splitlines():
        if line and not line.startswith('#') and '=' in line:
            key,value=line.split('=',1);parts=shlex.split(value)
            os.environ.setdefault(key,parts[0] if len(parts)==1 else value)


def main():
    load_env(Path('/etc/yichat/yichat.env'))
    action=sys.argv[1] if len(sys.argv)>1 else 'status'
    if action=='archive-once':load_env(Path('/etc/yichat/archive-worker.env'))
    from .config import Settings
    from .db import migrate,connect
    from .funds import Funds
    from . import policy,archive
    from .assets import migrate as migrate_assets
    s=Settings();f=Funds(s)
    if action=='migrate':
        migrate(s);policy.migrate_identities(s);f.migrate();migrate_assets(s)
        print('YiChat schema v2 ready; immutable account IDs and original Matrix identities preserved.')
    elif action=='reconcile':print(json.dumps(f.reconcile(),ensure_ascii=False,indent=2))
    elif action=='archive-once':
        from .archive_worker import run_once
        asyncio.run(run_once(s));print('Archive worker pass finished; inspect job states and journal for failures.')
    elif action=='status':
        with connect(s.db) as c:version=c.execute('SELECT MAX(version) FROM yc_migrations').fetchone()[0]
        with archive.connect(s.archive_reader) as c:
            checkpoint=c.execute("SELECT sequence,observed_ms,healthy,error FROM checkpoints WHERE source='synapse'").fetchone()
            sealed=c.execute('SELECT count(*) n FROM archives').fetchone()['n']
            gaps=c.execute('SELECT count(*) n FROM archive_gaps').fetchone()['n']
        print(json.dumps({'schema':version,'funds_enabled':f.enabled(),'official_configured':bool(s.official),'origin':s.origin,'request_limit_per_rolling_second':10,'max_file_bytes':s.max_file,'upload_bytes_per_second':s.upload_rate,'archive_ingestion':checkpoint,'sealed_specifications':sealed,'archive_gaps':gaps},indent=2))
    else:raise SystemExit('Unknown command')


if __name__=='__main__':main()
