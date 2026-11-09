import secrets

from backend.db import connect
from test_gateway import gateway


def test_internal_transfer_does_not_require_recipient_yidt_binding(gateway,env):
    client,app,settings=gateway
    settings.funds_enabled=True
    from test_chat_wallet import deposit
    deposit(settings,app.state.funds,env[2])
    wallet=client.get('/api/wallet').json()
    with connect(settings.ledger) as db:db.execute("DELETE FROM yidt_bindings WHERE user='bob' AND service='yichat'")
    payload={'room':'!direct','key':secrets.token_urlsafe(24),'amount':'0.01','count':1,'title':'test'}
    response=client.post('/api/orders',json=payload)
    assert response.status_code==200,response.text
    assert response.json()['internal'] and response.json()['state']=='awaiting_receipt'
    assert client.get('/api/wallet').json()['binding']==wallet['binding']


def test_wallet_endpoint_reads_binding_changes_without_new_session(gateway):
    client,app,settings=gateway
    with connect(settings.ledger) as db:
        own=dict(db.execute("SELECT * FROM yidt_bindings WHERE user='alice' AND service='yichat'").fetchone())
        db.execute("DELETE FROM yidt_bindings WHERE user='alice' AND service='yichat'")
    assert client.get('/api/wallet').json()['binding'] is None
    with connect(settings.ledger) as db:
        db.execute('INSERT INTO yidt_bindings(user,service,address,locked,created) VALUES(:user,:service,:address,:locked,:created)',own)
    assert client.get('/api/wallet').json()['binding']==own['address']
