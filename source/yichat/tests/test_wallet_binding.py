import secrets

from backend.db import connect
from test_gateway import gateway


def test_wallet_refresh_and_recipient_binding_error_over_http(gateway):
    client,app,settings=gateway
    settings.funds_enabled=True
    wallet=client.get('/api/wallet').json()
    assert wallet['enabled'] and wallet['binding']
    with connect(settings.ledger) as db:
        # The legacy gateway fixture seeds archive evidence only. Transfers also
        # require the current membership admission barrier in the asset ledger.
        for name,event in [('alice','$join1'),('bob','$join2')]:
            db.execute("INSERT INTO yichat_memberships VALUES(?,?,'join',?,NULL,NULL,NULL)",('!direct','@'+name+':chat.yitiao.org',event))
        recipient=dict(db.execute("SELECT * FROM yidt_bindings WHERE user='bob' AND service='yichat'").fetchone())
        db.execute("DELETE FROM yidt_bindings WHERE user='bob' AND service='yichat'")
    payload={'room':'!direct','key':secrets.token_urlsafe(24),'amount':'0.01','count':1,'title':'test'}
    response=client.post('/api/orders',json=payload)
    assert response.status_code==409,response.text
    assert response.json()['code']=='YICHAT_RECIPIENT_WALLET_UNBOUND'
    assert response.json()['binding_party']=='recipient'
    assert 'bind_url' not in response.json()
    assert client.get('/api/wallet').json()['binding']==wallet['binding']
    with connect(settings.ledger) as db:
        db.execute('INSERT INTO yidt_bindings(user,service,address,locked,created) VALUES(:user,:service,:address,:locked,:created)',recipient)
    response=client.post('/api/orders',json=payload)
    assert response.status_code==200,response.text
    assert response.json()['source']==wallet['binding']
    assert response.json()['state']=='awaiting_deposit'


def test_wallet_endpoint_reads_binding_changes_without_new_session(gateway):
    client,app,settings=gateway
    with connect(settings.ledger) as db:
        own=dict(db.execute("SELECT * FROM yidt_bindings WHERE user='alice' AND service='yichat'").fetchone())
        db.execute("DELETE FROM yidt_bindings WHERE user='alice' AND service='yichat'")
    assert client.get('/api/wallet').json()['binding'] is None
    with connect(settings.ledger) as db:
        db.execute('INSERT INTO yidt_bindings(user,service,address,locked,created) VALUES(:user,:service,:address,:locked,:created)',own)
    assert client.get('/api/wallet').json()['binding']==own['address']
