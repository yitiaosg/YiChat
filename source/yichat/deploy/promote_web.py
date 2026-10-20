"""Promote a reviewed static build; no database, key or service configuration writes."""
from pathlib import Path
from datetime import datetime,timezone
import argparse,json,os,shutil,subprocess


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--dist',required=True,type=Path)
    parser.add_argument('--repo',type=Path,default=Path('/root/yitiao'))
    parser.add_argument('--release-root',type=Path,default=Path('/var/lib/yichat/web'))
    args=parser.parse_args()
    if os.geteuid()!=0:parser.error('Run promotion with sudo; build as an unprivileged user first')
    source=args.dist.resolve();entry=args.repo/'yichat/client/dist';current=args.release_root/'current'
    if not (source/'index.html').is_file() or not list((source/'assets').glob('*.wasm')):
        parser.error('Expected a complete YiChat Vite build including Rust WASM')
    for path in (entry,current):
        if path.exists() and not path.is_symlink():parser.error('Refuse to replace a real directory: '+str(path))
    version=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    releases=args.release_root/'releases';releases.mkdir(parents=True,exist_ok=True)
    target=releases/version;shutil.copytree(source,target)
    if current.exists():
        for asset in (current/'assets').iterdir():
            if asset.is_file() and not (target/'assets'/asset.name).exists():shutil.copy2(asset,target/'assets'/asset.name)
    commit=subprocess.check_output(['git','-C',str(args.repo),'rev-parse','HEAD'],text=True).strip()
    (target/'release.json').write_text(json.dumps({'source_commit':commit,'created_utc':version},indent=2))
    next_link=args.release_root/'current.next';next_link.unlink(missing_ok=True);next_link.symlink_to(target);os.replace(next_link,current)
    entry.parent.mkdir(exist_ok=True)
    next_entry=entry.with_name('dist.next');next_entry.unlink(missing_ok=True);next_entry.symlink_to(current);os.replace(next_entry,entry)
    print('Static release:',target)
    print('Source commit:',commit)


if __name__=='__main__':main()
