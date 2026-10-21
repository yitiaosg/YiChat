"""Original YIDT binding handler in an isolated Flask app and fixture ledger."""
import sys
from pathlib import Path
import pytest
from backend.db import connect

@pytest.fixture
def wallet(env):
    s,f,u=env
    # Match existing fixture dependency discovery without installing into production.
    optional=Path('/opt/vps-python/lib/python3.11/site-packages')
    if optional.exists() and str(optional) not in sys.path:sys.path.append(str(optional))
    import yidt
    from flask import Flask
    from werkzeug.security import generate_password_hash
    app=Flask('isolated_native_wallet');app.secret_key='isolated-native-wallet-secret'
    app.before_request(yidt.guard)
    app.add_url_rule('/services/<service>/',view_func=yidt.services,methods=['GET','POST'])
    with connect(s.ledger) as c:
        address=c.execute("SELECT address FROM yidt_bindings WHERE user='alice' AND service='yichat'").fetchone()[0]
        c.execute('INSERT INTO yidt_pins(user,hash) VALUES(?,?)',('alice',generate_password_hash('123456')))
    client=app.test_client()
    with client.session_transaction(base_url='https://yidt.yitiao.org') as session:
        session.update(user='alice',sid='sid-alice',csrf='isolated-csrf')
    return client,s,address

@pytest.mark.parametrize('service,query,target',[
    ('yichat','?client=android','https://chat.yitiao.org/app/return'),
    ('yichat','','https://chat.yitiao.org/'),
    ('yichat','?client=https://evil.example/','https://chat.yitiao.org/'),
])
def test_wallet_binding_has_fixed_app_or_original_web_return(wallet,service,query,target):
    c,s,address=wallet
    response=c.post('/services/'+service+'/'+query,data={'csrf':'isolated-csrf','address':address},base_url='https://yidt.yitiao.org')
    assert response.status_code==302 and response.location==target
    with connect(s.ledger) as db:assert db.execute("SELECT count(*) FROM yidt_transactions WHERE kind='yichat_deposit'").fetchone()[0]==0


def test_pin_continuation_keeps_native_return_without_bypassing_verification(wallet):
    c,s,address=wallet
    with connect(s.ledger) as db:db.execute("DELETE FROM yidt_pins WHERE user='alice'")
    response=c.post('/services/yichat/?client=android',data={'csrf':'isolated-csrf','address':address},base_url='https://yidt.yitiao.org')
    assert response.status_code==302 and response.location=='/pin/'
    with c.session_transaction(base_url='https://yidt.yitiao.org') as session:assert session['pin_return']=='/services/yichat/?client=android'
    response=c.post('/services/yichat/?client=android',data={'csrf':'wrong','address':address},base_url='https://yidt.yitiao.org')
    assert response.status_code==403
