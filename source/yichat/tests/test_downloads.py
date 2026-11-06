"""Download links by browser platform; GitHub is mocked."""
import httpx,pytest
from backend import downloads
from test_gateway import gateway

REPO='yitiaosg/YiChat'
BASE='https://github.com/'+REPO+'/releases/download/'
RELEASES=[
    {'tag_name':'desktop-v0.2.0','draft':True,'prerelease':False,'assets':[{'name':'YiChat_0.2.0_x64-setup.exe','browser_download_url':BASE+'desktop-v0.2.0/YiChat_0.2.0_x64-setup.exe'}]},
    {'tag_name':'desktop-v0.1.0','draft':False,'prerelease':False,'assets':[
        {'name':'YiChat_0.1.0_x64-setup.exe.sig','browser_download_url':BASE+'desktop-v0.1.0/YiChat_0.1.0_x64-setup.exe.sig'},
        {'name':'YiChat_0.1.0_x64-setup.exe','browser_download_url':BASE+'desktop-v0.1.0/YiChat_0.1.0_x64-setup.exe'},
        {'name':'YiChat_0.1.0_amd64.deb','browser_download_url':BASE+'desktop-v0.1.0/YiChat_0.1.0_amd64.deb'},
        {'name':'YiChat_0.1.0_amd64.AppImage','browser_download_url':BASE+'desktop-v0.1.0/YiChat_0.1.0_amd64.AppImage'}]},
    {'tag_name':'android-20261030-v0.3.7','draft':False,'prerelease':False,'assets':[
        {'name':'YiChat-0.3.7-15.apk','browser_download_url':BASE+'android-20261030-v0.3.7/YiChat-0.3.7-15.apk'}]},
    {'tag_name':'android-20261029-v0.3.6','draft':False,'prerelease':False,'assets':[
        {'name':'YiChat-0.3.6-14.apk','browser_download_url':BASE+'android-20261029-v0.3.6/YiChat-0.3.6-14.apk'}]},
    {'tag_name':'desktop-updater','draft':False,'prerelease':False,'assets':[{'name':'latest.json','browser_download_url':BASE+'desktop-updater/latest.json'}]},
    {'tag_name':'android-evil','draft':False,'prerelease':False,'assets':[{'name':'x.apk','browser_download_url':'https://evil.example/x.apk'}]},
]

UA={
    'android':'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/130 Mobile Safari/537.36',
    'windows':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36',
    'linux':'Mozilla/5.0 (X11; Linux x86_64; rv:132.0) Gecko/20100101 Firefox/132.0',
    'macos':'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 Version/18.0 Safari/605.1.15',
    'ios':'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1',
}


@pytest.mark.parametrize('name',list(UA))
def test_platform_detection(name):
    assert downloads.platform(UA[name])==name
    assert downloads.platform('')=='other'


def test_pick_latest_published_assets_only():
    found=downloads.pick(RELEASES,REPO)
    assert found['android']['file']=='YiChat-0.3.7-15.apk'
    assert found['windows']['file']=='YiChat_0.1.0_x64-setup.exe'
    assert found['linux']['file']=='YiChat_0.1.0_amd64.AppImage'
    assert all(v['url'].startswith(BASE) for v in found.values())


def mock_github(app,status=200,data=RELEASES):
    calls=[]
    def handler(request):
        calls.append(request);return httpx.Response(status,json=data)
    app.state.http=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return calls


@pytest.mark.parametrize('name,file',[('android','YiChat-0.3.7-15.apk'),('windows','YiChat_0.1.0_x64-setup.exe'),('linux','YiChat_0.1.0_amd64.AppImage')])
def test_recommended_link_follows_user_agent(gateway,name,file):
    c,app,s=gateway;calls=mock_github(app)
    r=c.get('/api/downloads',headers={'User-Agent':UA[name]}).json()
    assert r['platform']==name and r['recommended']['url']==BASE+r['recommended']['version']+'/'+file
    assert {l['platform'] for l in r['links']}=={'android','windows','linux'}
    c.get('/api/downloads',headers={'User-Agent':UA[name]})
    assert len(calls)==1 and str(calls[0].url).startswith('https://api.github.com/repos/'+REPO+'/releases')


@pytest.mark.parametrize('name',['macos','ios'])
def test_unsupported_platforms_get_release_page(gateway,name):
    c,app,s=gateway;mock_github(app)
    r=c.get('/api/downloads',headers={'User-Agent':UA[name]}).json()
    assert r['recommended']['url']=='https://github.com/'+REPO+'/releases'


def test_github_failure_falls_back_to_release_pages(gateway):
    c,app,s=gateway;mock_github(app,status=503)
    r=c.get('/api/downloads',headers={'User-Agent':UA['android']}).json()
    assert r['links']==[] and r['recommended']['url']=='https://github.com/'+REPO+'/releases/latest'
    r=c.get('/api/downloads',headers={'User-Agent':UA['windows']}).json()
    assert r['recommended']['url']=='https://github.com/'+REPO+'/releases'


def test_desktop_unreleased_falls_back_to_release_page(gateway):
    c,app,s=gateway;mock_github(app,data=[r for r in RELEASES if not r['tag_name'].startswith('desktop-v')])
    r=c.get('/api/downloads',headers={'User-Agent':UA['windows']}).json()
    assert r['recommended']['url']=='https://github.com/'+REPO+'/releases' and [l['platform'] for l in r['links']]==['android']
