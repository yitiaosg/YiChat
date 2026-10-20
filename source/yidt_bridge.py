"""Shared first-party session and payment handoff, with no cross-domain cookies."""
import secrets,time,json
from urllib.parse import urlencode
from flask import request,session,jsonify,g,redirect
import yidt_core as y

RETURN={'ycards':'https://yitiao.org/ycards/','parachute':'https://yitiao.org/games/parachute/','daily':'https://yitiao.org/account/','yichat':'https://chat.yitiao.org/'}

def install(app):
    from werkzeug.middleware.proxy_fix import ProxyFix
    if not isinstance(app.wsgi_app,ProxyFix):app.wsgi_app=ProxyFix(app.wsgi_app,x_proto=1,x_host=1)
    @app.before_request
    def yidt_session():
        if not session.get('user'):return
        with y.connect() as c:
            sid=session.get('yidt_sid')
            if sid:
                if not y.sid_valid(c,sid,session['user']):
                    session.clear()
                    return redirect('/login/?'+urlencode({'next':request.path})) if request.method=='GET' else (jsonify(error='登录已失效，请重新登录'),401)
            else:
                sid=secrets.token_urlsafe(32);session['yidt_sid']=sid
                c.execute('INSERT INTO yidt_sessions VALUES(?,?,?,0)',(sid,session['user'],time.time()+30*86400))
        # Every mutating first-party request must be same-origin. Existing services also keep their CSRF tokens.
        if request.method=='POST' and request.path.startswith(('/games/parachute/','/account/yidt/')):
            if request.headers.get('Origin') and request.headers['Origin']!=request.host_url.rstrip('/'):
                return jsonify(error='请求来源不匹配'),403
            if not request.is_json and request.path.startswith('/games/parachute/api/'):
                return jsonify(error='需要 JSON 请求'),400

    @app.errorhandler(y.PaymentRequired)
    def payment_required(exc):
        user=session.get('user');sid=session.get('yidt_sid')
        if not user or not sid:return jsonify(error='请先登录'),401
        with y.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            b=y.binding(c,user,exc.service)
            if not b:return jsonify(error='绑定 YIDT 钱包以继续',authorize_url=y.ORIGIN+'/services/'+exc.service+'/',binding_required=True),402
            try:y.quote(c,b['address'],exc.amount)
            except y.Error as err:return jsonify(error=str(err)),409
            token=secrets.token_urlsafe(32)
            c.execute('INSERT INTO yidt_intents VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
              (token,user,sid,exc.service,y.payload_hash(request.path,request.get_json(silent=True) or {}),str(exc.amount),exc.reason,
               RETURN[exc.service],b['address'],'pending',time.time(),time.time()+900,None,b['created']))
        return jsonify(error=str(exc),authorize_url=y.ORIGIN+'/authorize/'+token,intent=token),402

    @app.errorhandler(y.Error)
    def yidt_error(exc):return jsonify(error=str(exc)),409

    @app.after_request
    def yidt_response(response):
        if response.is_json and response.status_code<300:
            data=response.get_json()
            if isinstance(data,dict):
                if getattr(g,'yidt_tx',None):
                    data['yidt_tx']=g.yidt_tx
                    if request.path.startswith('/ycards/'):
                        payload=request.get_json(silent=True) or {}
                        private=payload.get('kind')=='sealed'
                        if payload.get('auction_id'):
                            with y.connect() as c:
                                a=c.execute('SELECT kind FROM yc_auctions WHERE id=?',(payload['auction_id'],)).fetchone()
                                private=bool(a and a['kind']=='sealed')
                        data['yidt_private']=private
                if session.get('user') and request.path.startswith(('/ycards/','/games/parachute/')):
                    service='ycards' if request.path.startswith('/ycards/') else 'parachute'
                    with y.connect() as c:
                        b=y.binding(c,session['user'],service)
                        data['yidt_balance']=y.fmt(y.wallet(c,b['address'])['balance']) if b else None
                response.set_data(app.json.dumps(data))
        return response
