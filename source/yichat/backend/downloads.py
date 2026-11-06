"""Download links by browser platform, pointing to GitHub Releases.

Release assets are resolved from the public GitHub API and cached; any
failure falls back to the GitHub release pages. Only URLs under this
repository's release downloads are ever returned.
"""
import logging
import re
import time
from fastapi import Request

log=logging.getLogger('yichat.downloads')
CACHE_SECONDS=3600
RETRY_SECONDS=300


def platform(user_agent):
    ua=user_agent or ''
    if re.search(r'Android',ua,re.I):return 'android'
    if re.search(r'iPhone|iPad|iPod',ua):return 'ios'
    if re.search(r'Windows',ua):return 'windows'
    if re.search(r'Macintosh|Mac OS X',ua):return 'macos'
    if re.search(r'Linux|X11|CrOS',ua):return 'linux'
    return 'other'


def pick(releases,repo):
    prefix='https://github.com/'+repo+'/releases/download/'
    found={}
    def asset(release,test):
        for a in release.get('assets') or []:
            url=a.get('browser_download_url');name=a.get('name')
            if isinstance(url,str) and isinstance(name,str) and url.startswith(prefix) and test(name):return url,name
        return None
    for r in releases if isinstance(releases,list) else []:
        if not isinstance(r,dict) or r.get('draft') or r.get('prerelease'):continue
        tag=r.get('tag_name') or ''
        page='https://github.com/'+repo+'/releases/tag/'+tag
        if tag.startswith('android-') and 'android' not in found:
            hit=asset(r,lambda n:n.endswith('.apk'))
            if hit:found['android']={'url':hit[0],'file':hit[1],'version':tag,'page':page}
        if tag.startswith('desktop-v'):
            if 'windows' not in found:
                hit=asset(r,lambda n:n.endswith('-setup.exe')) or asset(r,lambda n:n.endswith('.msi'))
                if hit:found['windows']={'url':hit[0],'file':hit[1],'version':tag,'page':page}
            if 'linux' not in found:
                hit=asset(r,lambda n:n.endswith('.AppImage')) or asset(r,lambda n:n.endswith('.deb'))
                if hit:found['linux']={'url':hit[0],'file':hit[1],'version':tag,'page':page}
    return found


LABELS={'android':'下载 Android 版','windows':'下载 Windows 版','linux':'下载 Linux 版'}


def install(app,s):
    cache={'until':0,'found':{}}

    async def resolve():
        if cache['until']>time.time():return cache['found']
        try:
            r=await app.state.http.get('https://api.github.com/repos/'+s.github_repo+'/releases',params={'per_page':30},
                                       headers={'Accept':'application/vnd.github+json','User-Agent':'YiChat-Gateway'},timeout=10)
            if r.status_code!=200:raise RuntimeError('GitHub releases unavailable')
            cache.update(found=pick(r.json(),s.github_repo),until=time.time()+CACHE_SECONDS)
        except Exception:
            log.warning('GitHub release lookup unavailable; using release pages')
            cache['until']=time.time()+RETRY_SECONDS
        return cache['found']

    @app.get('/api/downloads')
    async def downloads(request:Request):
        found=await resolve()
        releases='https://github.com/'+s.github_repo+'/releases'
        links=[{'platform':p,'label':LABELS[p],**found[p]} for p in ('android','windows','linux') if p in found]
        current=platform(request.headers.get('user-agent'))
        hit=next((l for l in links if l['platform']==current),None)
        if hit:recommended=hit
        elif current=='android':recommended={'platform':'android','label':LABELS['android'],'url':releases+'/latest','page':releases+'/latest'}
        else:
            # iOS/macOS have no build; desktop builds may not be released yet.
            recommended={'platform':current,'label':'在 GitHub 查看 YiChat 下载','url':releases,'page':releases}
        return {'platform':current,'recommended':recommended,'links':links,'releases':releases}
