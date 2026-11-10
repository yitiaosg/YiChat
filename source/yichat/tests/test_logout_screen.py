"""Run the real logout handler in a fixture Flask app, never production sessions."""
import ast,secrets,sys,types,sqlite3
from pathlib import Path
from flask import Flask,session,request,redirect,url_for,render_template_string

def test_main_site_logout_screen_confirms_then_revokes_linked_session(tmp_path,monkeypatch):
 source=Path(__file__).parents[2]/'website.py';tree=ast.parse(source.read_text());node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='logout')
 app=Flask(__name__);app.secret_key='fixture-only'
 @app.get('/login/')
 def login():return 'login'
 path=tmp_path/'db.sqlite'
 def connect():return sqlite3.connect(path)
 with connect() as c:c.executescript("CREATE TABLE yidt_sessions(sid,user,revoked);INSERT INTO yidt_sessions VALUES('linked-sid','alice',0);CREATE TABLE oidc_tokens(sid);INSERT INTO oidc_tokens VALUES('linked-sid');CREATE TABLE oidc_codes(sid);INSERT INTO oidc_codes VALUES('linked-sid');")
 monkeypatch.setitem(sys.modules,'yidt_core',types.SimpleNamespace(connect=connect))
 ns=dict(app=app,session=session,request=request,redirect=redirect,url_for=url_for,render_template_string=render_template_string,secrets=secrets,_shell=lambda title,body:body,_conn=connect)
 exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),ns)
 client=app.test_client()
 with client.session_transaction() as s:s['user']='alice';s['yidt_sid']='linked-sid'
 page=client.get('/logout/');assert page.status_code==200 and '关联的 YiChat 会话也会自动退出'.encode() in page.data
 with connect() as c:assert c.execute('SELECT revoked FROM yidt_sessions').fetchone()[0]==0
 with client.session_transaction() as s:csrf=s['_logout_csrf']
 assert client.post('/logout/',data={'confirm':'yichat','csrf':'wrong'}).status_code==403
 assert client.post('/logout/',data={'confirm':'yichat','csrf':csrf}).status_code==302
 with connect() as c:assert c.execute('SELECT revoked FROM yidt_sessions').fetchone()[0]==1 and c.execute('SELECT count(*) FROM oidc_tokens').fetchone()[0]==0
