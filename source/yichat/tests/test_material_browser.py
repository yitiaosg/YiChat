"""Browser package merge and actual server rehash, isolated PG + local HTTP only."""
import json,os,subprocess,threading,time,urllib.request
from pathlib import Path
from datetime import datetime,timezone,timedelta
import uvicorn
from backend import archive,day_packets
from test_gateway import gateway
from test_material_protocol import accepted,who

def test_browser_local_merge_actual_verify_gray_and_empty_days(gateway,tmp_path):
 c,app,s=gateway;s.material_protocol=True
 from test_synapse_integration import free_port
 day=(datetime.now(timezone.utc).date()-timedelta(days=1)).isoformat();empty=(datetime.now(timezone.utc).date()-timedelta(days=2)).isoformat();low,_=archive.day_bounds(day)
 accepted(s,{'msgtype':'m.text','body':'真实服务器校验的原始文字'},eid='$browser',at=low+600001,seq=40)
 for d in (empty,day):assert day_packets.seal(s,'!direct',d,40,int(time.time()*1000))
 start=archive.day_bounds(empty)[0]
 archive.ingest_batch(s.archive_ingest,[('$browser-join','!direct',4,start-1,json.dumps({'event_id':'$browser-join','room_id':'!direct','sender':who(s)['mxid'],'state_key':who(s)['mxid'],'type':'m.room.member','content':{'membership':'join'}}))],40,int(time.time()*1000))
 packets=[day_packets.build(s,'!direct',d,True) for d in (empty,day)]
 port=free_port();config=uvicorn.Config(app,host='127.0.0.1',port=port,log_level='warning',lifespan='off');server=uvicorn.Server(config);thread=threading.Thread(target=server.run,daemon=True);thread.start()
 path=tmp_path/'fixture.json';path.write_text(json.dumps({'backend':'http://127.0.0.1:'+str(port),'authorization':'Bearer session-token','origin':s.origin,'packets':packets}))
 client=Path(__file__).parents[1]/'client';web_port=free_port();web_log=(tmp_path/'vite.log').open('w')
 web=subprocess.Popen(['node',str(client/'node_modules/vite/bin/vite.js'),'--config',str(client/'test/browser/protocol.vite.config.ts'),'--port',str(web_port)],cwd=client,stdout=web_log,stderr=web_log)
 web_base='http://127.0.0.1:'+str(web_port)
 try:
  for _ in range(200):
   try:
    if urllib.request.urlopen(web_base,timeout=1).status==200:break
   except OSError:pass
   if web.poll() is not None:raise RuntimeError((tmp_path/'vite.log').read_text())
   time.sleep(.05)
  for _ in range(100):
   if server.started:break
   time.sleep(.05)
  script=Path(__file__).parents[1]/'client/test/browser/packet-server.mjs'
  result=subprocess.run(['node',str(script),str(path)],capture_output=True,text=True,timeout=120,env=dict(os.environ,YICHAT_BROWSER_BASE=web_base))
  assert result.returncode==0,result.stdout+'\n'+result.stderr
 finally:
  server.should_exit=True;thread.join(10);web.terminate();web.wait(timeout=10);web_log.close()
