"""Read adapters reuse the authoritative YCards renderer payload and permissions.
No schema initialization, publication or ownership changes occur on these paths.
"""
from .db import connect
from .security import fail


def store(s):
    import ycards
    instance=ycards.Store.__new__(ycards.Store)
    instance.connect=lambda:connect(s.ledger)
    instance.cards=s.site/'cards'
    return instance


def public_card(s,iid,cid):
    service=store(s)
    # One database snapshot for public permission and all renderer inputs.
    with connect(s.ledger) as c:
        c.execute('BEGIN')
        row=c.execute('SELECT i.id FROM yc_instances i JOIN yc_public_cards p ON p.instance=i.id AND p.owner=i.owner WHERE i.id=? AND i.cid=? AND NOT EXISTS(SELECT 1 FROM yc_destroyed d WHERE d.instance=i.id)',(iid,cid)).fetchone()
        return service.preview(iid,connection=c) if row else None


def install(app,s,session):
    from fastapi import Request
    @app.get('/api/cards/mine')
    async def mine(request:Request,page:int=1):
        who=session(request)
        result=store(s).cards_list(who['username'],False,{'page':str(page),'compact':'1'})
        for item in result['items']:item['giftable']=not(item['listing_id'] or item['auction_id'])
        return result
    @app.get('/api/cards/{iid}/render')
    async def render(request:Request,iid:str):
        who=session(request)
        try:return store(s).preview(iid,who['username'])
        except Exception as exc:
            import ycards
            if isinstance(exc,ycards.CardError):fail(exc.status,exc.message)
            raise
