"""Account identity and social policy, shared by the site and Matrix service.

Only account_ids.uid identifies a person. Usernames are current display/lookup keys.
Every mutation takes the same SQLite writer lock, including block/follow races.
"""
import contextlib
import sqlite3
import time

PRIVACY_MESSAGE = '由于对方隐私设置，你不能发送消息'
PRIVACY_CODE = 'YICHAT_MESSAGE_FORBIDDEN'


class PolicyError(ValueError):
    def __init__(self, message, status=403, code=PRIVACY_CODE):
        self.status, self.code = status, code
        super().__init__(message)


@contextlib.contextmanager
def database(path, write=False):
    c = sqlite3.connect(path, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    try:
        c.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
        yield c
        c.commit()
    except BaseException:
        c.rollback()
        raise
    finally:
        c.close()


def migrate(path):
    with database(path, True) as c:
        statements = [
            '''CREATE TABLE IF NOT EXISTS account_ids(
                uid TEXT PRIMARY KEY CHECK(length(uid)=32), username TEXT UNIQUE NOT NULL)''',
            '''INSERT OR IGNORE INTO account_ids SELECT lower(hex(randomblob(16))),username FROM users''',
            '''CREATE TRIGGER IF NOT EXISTS account_id_new AFTER INSERT ON users BEGIN
                INSERT INTO account_ids VALUES(lower(hex(randomblob(16))),NEW.username); END''',
            '''CREATE TRIGGER IF NOT EXISTS account_id_rename AFTER UPDATE OF username ON users BEGIN
                UPDATE account_ids SET username=NEW.username WHERE username=OLD.username; END''',
            '''CREATE TRIGGER IF NOT EXISTS account_id_immutable BEFORE UPDATE OF uid ON account_ids
                BEGIN SELECT RAISE(ABORT,'immutable account ID'); END''',
            '''CREATE TRIGGER IF NOT EXISTS account_id_preserve BEFORE DELETE ON account_ids
                BEGIN SELECT RAISE(ABORT,'account ID is retained'); END''',
            '''CREATE TABLE IF NOT EXISTS social_follows(
                source TEXT REFERENCES account_ids(uid),target TEXT REFERENCES account_ids(uid),
                created REAL NOT NULL,PRIMARY KEY(source,target),CHECK(source<>target))''',
            'CREATE INDEX IF NOT EXISTS social_follow_target ON social_follows(target,source)',
            '''CREATE TABLE IF NOT EXISTS social_blocks(
                source TEXT REFERENCES account_ids(uid),target TEXT REFERENCES account_ids(uid),
                created REAL NOT NULL,PRIMARY KEY(source,target),CHECK(source<>target))''',
            '''CREATE TABLE IF NOT EXISTS social_privacy(
                uid TEXT PRIMARY KEY REFERENCES account_ids(uid),following INTEGER NOT NULL DEFAULT 0,
                followers INTEGER NOT NULL DEFAULT 0,friends INTEGER NOT NULL DEFAULT 0,
                messages TEXT NOT NULL DEFAULT 'everyone' CHECK(messages IN ('everyone','followers','friends')),
                CHECK(following IN (0,1) AND followers IN (0,1) AND friends IN (0,1)))''',
            '''CREATE TRIGGER IF NOT EXISTS social_no_blocked_follow BEFORE INSERT ON social_follows
                WHEN EXISTS(SELECT 1 FROM social_blocks WHERE
                  (source=NEW.source AND target=NEW.target) OR (source=NEW.target AND target=NEW.source))
                BEGIN SELECT RAISE(ABORT,'blocked relationship'); END''',
            '''CREATE TRIGGER IF NOT EXISTS social_block_clears AFTER INSERT ON social_blocks BEGIN
                DELETE FROM social_follows WHERE (source=NEW.source AND target=NEW.target)
                OR (source=NEW.target AND target=NEW.source); END''',
        ]
        for sql in statements:
            c.execute(sql)


def identity(c, *, username=None, uid=None):
    column, value = ('a.username', username) if username is not None else ('a.uid', uid)
    row = c.execute('SELECT a.*,u.* FROM account_ids a JOIN users u ON a.username=u.username WHERE '+column+'=?', (value,)).fetchone()
    if not row or dict(row).get('purged_at'):
        raise PolicyError('用户不存在', 404, 'YICHAT_USER_NOT_FOUND')
    return dict(row)


def get_identity(path, **kwargs):
    with database(path) as c:
        return identity(c, **kwargs)


def blocked(c, a, b):
    return bool(c.execute('SELECT 1 FROM social_blocks WHERE (source=? AND target=?) OR (source=? AND target=?)', (a,b,b,a)).fetchone())


def follows(c, a, b):
    return bool(c.execute('SELECT 1 FROM social_follows WHERE source=? AND target=?', (a,b)).fetchone())


def privacy(c, uid):
    row = c.execute('SELECT following,followers,friends,messages FROM social_privacy WHERE uid=?', (uid,)).fetchone()
    return dict(row) if row else dict(following=0,followers=0,friends=0,messages='everyone')


def can_message(c, sender, recipient):
    if sender == recipient or blocked(c, sender, recipient):
        return False
    mode = privacy(c, recipient)['messages']
    return mode == 'everyone' or (follows(c,sender,recipient) and (mode == 'followers' or follows(c,recipient,sender)))


def require_message(path, sender, recipient):
    # Missing database/schema/identity errors propagate: policy failure never allows.
    with database(path) as c:
        identity(c,uid=sender); identity(c,uid=recipient)
        if not can_message(c,sender,recipient):
            raise PolicyError(PRIVACY_MESSAGE)


def mutate(path, actor, target, action):
    if action not in ('follow','unfollow','block','unblock'):
        raise PolicyError('无效操作',400,'YICHAT_INVALID_ACTION')
    with database(path, True) as c:
        identity(c,uid=actor); identity(c,uid=target)
        if actor == target:
            raise PolicyError('不能对自己执行此操作',400,'YICHAT_SELF_RELATION')
        if action == 'follow':
            if blocked(c,actor,target):
                raise PolicyError('拉黑期间不能关注',403,'YICHAT_BLOCKED')
            c.execute('INSERT OR IGNORE INTO social_follows VALUES(?,?,?)',(actor,target,time.time()))
        elif action == 'unfollow':
            c.execute('DELETE FROM social_follows WHERE source=? AND target=?',(actor,target))
        elif action == 'block':
            c.execute('INSERT OR IGNORE INTO social_blocks VALUES(?,?,?)',(actor,target,time.time()))
        else:
            c.execute('DELETE FROM social_blocks WHERE source=? AND target=?',(actor,target))
        return view(c,target,actor)


def view(c, target, viewer=None):
    person = identity(c,uid=target)
    out = {k:person.get(k) or '' for k in ('uid','username','nickname','biography')}
    out['counts'] = {
        'following':c.execute('SELECT count(*) FROM social_follows WHERE source=?',(target,)).fetchone()[0],
        'followers':c.execute('SELECT count(*) FROM social_follows WHERE target=?',(target,)).fetchone()[0],
        'friends':c.execute('SELECT count(*) FROM social_follows a JOIN social_follows b ON a.source=b.target AND a.target=b.source WHERE a.source=?',(target,)).fetchone()[0],
    }
    own = viewer == target
    p = privacy(c,target)
    out['lists_visible'] = {k:bool(own or p[k]) for k in ('following','followers','friends')}
    following = bool(viewer and follows(c,viewer,target))
    mutual = bool(following and follows(c,target,viewer))
    out.update(is_self=own, following=following, mutual=mutual,
        blocked=bool(viewer and c.execute('SELECT 1 FROM social_blocks WHERE source=? AND target=?',(viewer,target)).fetchone()),
        can_message=bool(viewer and can_message(c,viewer,target)), chat_available=True)
    return out


def list_people(path, target, kind, viewer, cursor='', limit=30):
    if kind not in ('following','followers','friends','blocked'):
        raise PolicyError('名单不存在',404,'YICHAT_LIST_NOT_FOUND')
    if not isinstance(limit,int) or not 1<=limit<=100 or (cursor and (len(cursor)!=32 or any(x not in '0123456789abcdef' for x in cursor))):
        raise PolicyError('无效分页参数',400,'YICHAT_INVALID_CURSOR')
    with database(path) as c:
        identity(c,uid=target)
        if viewer != target and (kind == 'blocked' or not privacy(c,target)[kind]):
            raise PolicyError('对方未公开此名单',403,'YICHAT_LIST_PRIVATE')
        if kind == 'blocked':
            query='SELECT target uid FROM social_blocks WHERE source=?'
        elif kind == 'following':
            query='SELECT target uid FROM social_follows WHERE source=?'
        elif kind == 'followers':
            query='SELECT source uid FROM social_follows WHERE target=?'
        else:
            query='SELECT a.target uid FROM social_follows a JOIN social_follows b ON a.target=b.source AND a.source=b.target WHERE a.source=?'
        rows=c.execute('SELECT a.uid,a.username,u.nickname FROM ('+query+') r JOIN account_ids a USING(uid) JOIN users u ON a.username=u.username WHERE a.uid>? ORDER BY a.uid LIMIT ?', (target,cursor,limit+1)).fetchall()
        return dict(items=[dict(r) for r in rows[:limit]], next_cursor=rows[limit-1]['uid'] if len(rows)>limit else None)


def compact(n):
    if n>=999950 and n<1000000:return '1m'
    for unit, size in (('m',1000000),('k',1000)):
        if n>=size:
            return ('%.1f'%(n/size)).rstrip('0').rstrip('.')+unit
    return str(n)


def install(app, deps):
    """Flask routes reuse the site's live session, avatar, shell and QR library."""
    import secrets,os
    from flask import request, session, jsonify, redirect, render_template_string, Response
    path=deps['db']; migrate(path)
    @app.before_request
    def shared_request_limit():
        if not request.path.startswith(('/api/social/','/account/privacy/','/account/qr/')):return
        rate_db=os.getenv('YICHAT_RATE_DB')
        if not rate_db:return
        from types import SimpleNamespace
        from yichat.backend.security import request_limit
        from fastapi import HTTPException
        user=deps['current_user']()
        key='user:'+get_identity(path,username=user['username'])['uid'] if user else 'anonymous:'+request.remote_addr
        try:request_limit(SimpleNamespace(db=rate_db),key)
        except HTTPException as e:return jsonify(e.detail),e.status_code,e.headers or {}
    def actor():
        user=deps['current_user']()
        if not user: raise PolicyError('请先登录',401,'YICHAT_UNAUTHENTICATED')
        return get_identity(path,username=user['username'])['uid']
    def csrf():
        value=request.headers.get('X-CSRF-Token','') or request.form.get('csrf','')
        if not value or not secrets.compare_digest(value,session.get('social_csrf','')):
            raise PolicyError('请求验证失败，请刷新页面',403,'YICHAT_CSRF')
        if request.headers.get('Origin') and request.headers['Origin']!=request.host_url.rstrip('/'):
            raise PolicyError('请求来源不匹配',403,'YICHAT_CSRF')
    @app.errorhandler(PolicyError)
    def error(e): return jsonify(error=str(e),code=e.code,errcode='M_FORBIDDEN'), e.status
    def settings_page(page_title,template,**values):
        page=deps['shell'](page_title,'{% include settings_template %}').replace('</head>','<link rel="stylesheet" href="/static/account-settings.css">\n</head>')
        return render_template_string(page,settings_template=template,**values)
    @app.context_processor
    def context():
        session.setdefault('social_csrf',secrets.token_urlsafe(32))
        return {'social_csrf':session['social_csrf'],'compact_count':compact}
    @app.get('/api/social/<uid>')
    def get_view(uid):
        viewer=actor() if deps['current_user']() else None
        with database(path) as c:return jsonify(view(c,uid,viewer))
    @app.post('/api/social/<uid>/<action>')
    def relation(uid,action):
        who=actor(); csrf(); return jsonify(mutate(path,who,uid,action))
    @app.get('/api/social/<uid>/lists/<kind>')
    def people(uid,kind):
        viewer=actor() if deps['current_user']() else None
        return jsonify(list_people(path,uid,kind,viewer,request.args.get('cursor','')))
    @app.route('/account/privacy/',methods=['GET','POST'])
    def settings():
        who=actor()
        if request.method=='POST':
            csrf(); data=request.form
            mode=data.get('messages')
            if mode not in ('everyone','followers','friends'):raise PolicyError('无效隐私选项',400,'YICHAT_INVALID_PRIVACY')
            with database(path,True) as c:
                c.execute('INSERT INTO social_privacy VALUES(?,?,?,?,?) ON CONFLICT(uid) DO UPDATE SET following=excluded.following,followers=excluded.followers,friends=excluded.friends,messages=excluded.messages',
                    (who,*[int(data.get(k)=='on') for k in ('following','followers','friends')],mode))
            return redirect('/account/privacy/?saved=1')
        with database(path) as c:p=privacy(c,who)
        return settings_page('隐私设置','account_privacy.html',p=p,uid=who)
    @app.get('/account/relations/<uid>/<kind>/')
    def relation_page(uid,kind):
        viewer=actor() if deps['current_user']() else None
        list_people(path,uid,kind,viewer,limit=1)
        names={'following':'关注','followers':'粉丝','friends':'朋友','blocked':'黑名单'}
        return settings_page(names[kind],'account_relations.html',title=names[kind],uid=uid,kind=kind)
    @app.post('/api/social/block-username')
    def block_username():
        who=actor();csrf();data=request.get_json(silent=True) or {}
        target=get_identity(path,username=str(data.get('username','')))['uid']
        return jsonify(mutate(path,who,target,'block'))
    @app.get('/account/qr/')
    def qr_page():
        who=actor();name=get_identity(path,uid=who)['username']
        return settings_page('我的二维码','account_qr.html',username=name,link='https://yitiao.org/@'+name+'/')
    @app.get('/account/qr/image')
    def qr_image():
        import io,qrcode
        name=get_identity(path,uid=actor())['username'];buf=io.BytesIO()
        qr=qrcode.QRCode(box_size=8,border=4);qr.add_data('https://yitiao.org/@'+name+'/');qr.make(fit=True)
        qr.make_image(fill_color='white',back_color='transparent').save(buf,format='PNG')
        return Response(buf.getvalue(),mimetype='image/png',headers={'Cache-Control':'no-store'})
