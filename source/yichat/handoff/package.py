#!/usr/bin/env python3
"""Export a reviewed, secret-free Android source handoff from tracked files only."""
import argparse
import ast
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile

ROOT=Path(__file__).resolve().parents[2]
ROOT_FILES={'.gitignore','website.py','oidc_provider.py','account_social.py','yidt.py','yidt_core.py','yidt_bridge.py','passkeys.py','points.py','mailer.py','tools.py','debug.py','parachute.py','admin_panel.py','yichat.service','yidt.service'}
PREFIXES=('yichat/','static/','templates/','docs/','tests/')
# tools.py imports this namespace; trading strategies and daemon entrypoints are irrelevant.
EXTRA={'app/api.py'}
SECRET_NAMES={'TURNSTILE_SECRET','BREVO_API_KEY'}
FORBIDDEN_PARTS={'node_modules','.git','.venv','dist','artifacts','backups','__pycache__','telegram-web-a','frontend','releases','avatar'}


def digest(data):return hashlib.sha256(data).hexdigest()


def selected(name):
    path=PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or FORBIDDEN_PARTS.intersection(path.parts):return False
    if path.name.startswith('.env') and not path.name.endswith('.example'):return False
    if re.search(r'\.(?:db|sqlite3?|pem|key|p12|pfx|jks|keystore|pyc)(?:-|$)',name,re.I):return False
    return name in ROOT_FILES or name in EXTRA or name.startswith(PREFIXES) or (name.startswith('ycards') and name.endswith('.py'))


def sanitized(name,data):
    edits=[]
    if name in ('website.py','mailer.py'):
        text=data.decode();lines=text.splitlines(keepends=True)
        for node in ast.walk(ast.parse(text)):
            if not isinstance(node,ast.Assign):continue
            names=[target.id for target in node.targets if isinstance(target,ast.Name)]
            for field in SECRET_NAMES.intersection(names):
                if not isinstance(node.value,ast.Call) or len(node.value.args)!=2:raise RuntimeError('Review changed credential expression: '+name)
                env_name=ast.literal_eval(node.value.args[0])
                if not isinstance(env_name,str):raise RuntimeError('Unexpected environment variable')
                lines[node.lineno-1:node.end_lineno]=[field+' = os.environ.get('+repr(env_name)+', "")\n']
                edits.append({'file':name,'field':field,'change':'Removed embedded/placeholder credential fallback; environment required'})
        data=''.join(lines).encode()
    if re.search(rb'-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----',data):raise RuntimeError('Private key material found in '+name)
    if re.search(rb'(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{30,}|sk-(?:proj-)?[A-Za-z0-9_-]{40,})',data):raise RuntimeError('Credential-like value found; review '+name)
    return data,edits


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--reference',type=Path,required=True,help='Original attached API document')
    args=p.parse_args()
    status=subprocess.check_output(['git','-c','safe.directory=*','status','--porcelain'],cwd=ROOT,text=True)
    if status:raise SystemExit('Commit/review working tree before packaging; refusing unreviewed files')
    commit=subprocess.check_output(['git','-c','safe.directory=*','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    tracked=subprocess.check_output(['git','-c','safe.directory=*','ls-files','-z'],cwd=ROOT).decode().split('\0')
    files={};sources=[];redactions=[];excluded=[]
    for name in sorted(filter(None,tracked)):
        if not selected(name):excluded.append(name);continue
        path=ROOT/name
        if path.is_symlink() or not path.is_file():raise RuntimeError('Only regular tracked files allowed: '+name)
        original=subprocess.check_output(['git','-c','safe.directory=*','show',commit+':'+name],cwd=ROOT)
        if original!=path.read_bytes():raise RuntimeError('File changed during packaging: '+name)
        data,changes=sanitized(name,original);redactions.extend(changes)
        target='source/'+name;files[target]=data
        sources.append({'path':target,'sha256':digest(data),'original_sha256':digest(original),'bytes':len(data),'sanitized':bool(changes)})
    for name in ('README.md','ANDROID-PROMPT.md','ARCHITECTURE.md','bootstrap-tests.sh','requirements-main.in','prepare_signing.py'):
        files['handoff/'+name]=files['source/yichat/handoff/'+name]
    reference=args.reference.read_bytes()
    if b'PRIVATE KEY-----' in reference:raise RuntimeError('Unexpected reference key material')
    files['reference/ORIGINAL-CHAT-KERNEL-API.md']=reference
    files['README.md']=files['handoff/README.md']
    files['ANDROID-PROMPT.md']=files['handoff/ANDROID-PROMPT.md']
    files['ARCHITECTURE.md']=files['handoff/ARCHITECTURE.md']
    manifest={'format':'yichat-android-handoff-v1','date':'2026-10-20','source_commit':commit,
        'source_files':sources,'excluded_tracked_files':excluded,'redactions':redactions,
        'reference_sha256':digest(reference),'contains_android_implementation':False,
        'excluded_runtime':'All untracked/runtime files, live databases, keys, env, DSNs, credentials, uploads, personal archives, old Telegram sources, dependencies, build outputs, screenshots, Git history'}
    files['SOURCE-MANIFEST.json']=(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode()
    files['SHA256SUMS']=''.join(digest(data)+'  '+name+'\n' for name,data in sorted(files.items())).encode()
    output=args.output.expanduser().resolve();output.parent.mkdir(parents=True,exist_ok=True)
    if output.exists():raise SystemExit('Output already exists; keep reviewed archive or choose a new filename')
    prefix='yichat-android-handoff-20261020'
    with tarfile.open(output,'w:gz') as tar:
        for name,data in sorted(files.items()):
            entry=tarfile.TarInfo(prefix+'/'+name);entry.size=len(data);entry.mode=0o755 if name.endswith('.sh') else 0o644
            entry.mtime=1792454400;entry.uid=entry.gid=0;entry.uname=entry.gname=''
            tar.addfile(entry,io.BytesIO(data))
    checksum=output.with_name(output.name+'.sha256');checksum.write_text(digest(output.read_bytes())+'  '+output.name+'\n')
    print(json.dumps({'output':str(output),'source_commit':commit,'source_files':len(sources),'total_files':len(files),'bytes':output.stat().st_size,'credential_fallbacks_removed':len(redactions)},ensure_ascii=False))


if __name__=='__main__':main()
