"""Isolated main-site browser fixture; never install this as a production route."""
from pathlib import Path
import sys,os
source=Path(__file__).resolve().parents[3]
fixture=source/'tests/yidt_browser_fixture.py'
context={'__file__':str(fixture),'__name__':'isolated_main_fixture'}
exec(compile(fixture.read_text().split('for app,port in')[0],str(fixture),'exec'),context)
website=context['website'];root=context['d']
sys.path.insert(0,str(source/'yichat'))
from backend.db import migrate
from backend.config import Settings
s=Settings(root=root/'gateway',site=root)
migrate(s);os.environ['YICHAT_RATE_DB']=str(s.db)
from werkzeug.serving import make_server
print('Isolated account fixture ready on 5091',flush=True)
make_server('127.0.0.1',5091,website.app,threaded=True).serve_forever()
