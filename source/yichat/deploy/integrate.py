"""Apply small, idempotent first-party registrations; never restart existing apps."""
from pathlib import Path
import shutil, time, sys
root=Path(sys.argv[1] if len(sys.argv)>1 else '/root/yitiao')
changes={}
p=root/'oidc_provider.py';s=p.read_text()
if "CLIENTS['yichat']" not in s:
    anchor="CLIENTS['yidt'] = {'client_secret':'', 'redirect_uris':['https://yidt.yitiao.org/oauth/callback'], 'name':'YIDT'}"
    if anchor not in s:raise SystemExit('OIDC provider changed; review integration manually')
    s=s.replace(anchor,anchor+"\nCLIENTS['yichat'] = {'client_secret':'', 'redirect_uris':['https://chat.yitiao.org/auth/callback'], 'name':'YiChat'}")
    s=s.replace("row['client_id']=='yidt'","row['client_id'] in ('yidt','yichat')").replace("client_id=='yidt'","client_id in ('yidt','yichat')")
    s=s.replace('client_id != "yidt"','client_id not in ("yidt", "yichat")').replace('row["client_id"] != "yidt"','row["client_id"] not in ("yidt", "yichat")')
    # YiChat requires S256 even though the legacy provider allows plain for other clients.
    anchor='        # 公开客户端（没配 secret）必须用 PKCE，否则 code 可被冒用'
    s=s.replace(anchor,'''        if client_id == 'yichat' and (not challenge or method != 'S256'):
            return _redirect_err(redirect_uri, "invalid_request", "YiChat requires S256 PKCE", state)
'''+anchor)
    changes[p]=s
p=root/'yidt_core.py';s=p.read_text()
if "'yichat':'YiChat'" not in s:
    anchor="'wallet':'钱包转账'"
    if anchor not in s:raise SystemExit('Wallet services changed; review integration manually')
    changes[p]=s.replace(anchor,anchor+", 'yichat':'YiChat'")
p=root/'yidt_bridge.py';s=p.read_text()
if "'yichat':'https://chat.yitiao.org/'" not in s:
    anchor="'daily':'https://yitiao.org/account/'"
    if anchor not in s:raise SystemExit('Wallet return routes changed; review integration manually')
    changes[p]=s.replace(anchor,anchor+",'yichat':'https://chat.yitiao.org/'")
if changes:
    backup=root/'backups'/('yichat-integration-'+time.strftime('%Y%m%d-%H%M%S'));backup.mkdir(parents=True,mode=0o700)
    for p,s in changes.items():
        compile(s,str(p),'exec');shutil.copy2(p,backup/p.name);p.write_text(s)
    print('Updated registrations:',', '.join(p.name for p in changes))
    print('Existing processes were NOT restarted. Main website and YIDT must reload to activate registrations.')
else: print('Integration already present')
