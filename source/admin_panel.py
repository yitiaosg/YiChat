"""Admin routes for the existing Flask app; registered with explicit dependencies."""
from __future__ import annotations
import hashlib
import hmac
import json
import os
import re
import secrets
import signal
import sqlite3
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from flask import Blueprint, jsonify, redirect, render_template, request, session
import pyotp

JOB_TIMEOUT = 120
UNIT_RE = re.compile(r'^[A-Za-z0-9_.:@\\-]+\.(service|timer)$')
LABELS = {'username':'用户名','user':'用户','password_hash':'密码设置','role':'角色','totp_secret':'身份验证器',
 'totp_on_login':'登录两步验证开关','google_sub':'Google 绑定','google_email':'Google 邮箱','nickname':'昵称',
 'biography':'简介','created_at':'创建时间','last_login_at':'最后登录时间','email':'邮箱','recovery_codes':'备用验证码',
 'deleted_at':'删除时间','purged_at':'永久停用时间','balance_cents':'余额（分）','change_cents':'变动（分）',
 'reason':'原因','reference':'关联记录','streak':'连续签到天数','last_checkin':'最后签到日期','id':'记录编号',
 'recording_time':'成绩时间','time':'扫雷耗时（毫秒）','stake_cents':'投注（分）','payout_cents':'获得点数（分）',
 'fee_cents':'费用（分）','debit_cents':'扣款（分）','crash_height':'坠落高度','exit_height':'离开高度',
 'started_at':'开始时间','ended_at':'结束时间','status':'状态','pinned':'置顶','auth_time':'授权时间',
 'expires_at':'到期时间','client_id':'客户端','kind':'类型','scope':'授权范围','target':'目标','purpose':'用途',
 'sent_at':'发送时间','tries':'尝试次数','consumed':'已使用','retired_at':'解绑时间'}
TABLES = {'users':'账户资料','msEasyRanking':'扫雷 · 简单','msMediumRanking':'扫雷 · 中等',
 'msHardRanking':'扫雷 · 困难','points_accounts':'账户点数','points_ledger':'点数变动记录',
 'parachute_rounds':'跳伞记录','parachute_demo_rounds':'跳伞试玩','oidc_tokens':'登录授权凭证',
 'oidc_codes':'待兑换授权','email_codes':'邮箱验证','trusted_devices':'受信设备','retired_identities':'解绑身份'}

TABLES.update({'yc_card_names':'YCards · 卡片名称','yc_save_requests':'YCards · 模板保存请求','yc_public_cards':'YCards · 公开卡片','yc_batch_list_requests':'YCards · 批量挂售请求','yc_destroyed':'YCards · 损毁记录','yc_showcase':'YCards · 主页展示','yc_showcase_purchases':'YCards · 展示框购买','yc_listing_requests':'YCards · 挂售请求记录','yc_templates':'YCards · 正式模板','yc_drafts':'YCards · 草稿','yc_stats':'YCards · 成熟度与产量',
 'yc_sequences':'YCards · 生产编号','yc_jobs':'YCards · 生产批次','yc_sheets':'YCards · 逐版生产',
 'yc_defects':'YCards · 损耗模板','yc_instances':'YCards · 卡片仓库','yc_listings':'YCards · 挂售',
 'yc_trades':'YCards · 交易','yc_auctions':'YCards · 拍卖','yc_bids':'YCards · 最高报价与冻结',
 'yc_bid_events':'YCards · 报价历史','yc_coupons':'YCards · 优惠码','yc_coupon_uses':'YCards · 优惠使用记录'})
LABELS.update({'tid':'模板 TID','cid':'展示 CID','owner':'当前所有者','producer':'生产者','seller':'卖家','buyer':'买家',
 'winner':'获胜者','instance':'卡片实例 ID','auction':'拍卖 ID','listing':'挂售 ID','job':'生产任务',
 'name':'名称','design':'设计 JSON','cost':'原价 point','total':'实付 point','original_total':'优惠前 point',
 'discount':'减免 point','paid':'实付 point','maturity':'成熟度','quantity':'理论产量（张）','number':'序号',
 'production_number':'生产编号','cell':'版内编号','geometry':'成品轮廓','defect_number':'损耗编号',
 'defects':'损耗 JSON','payload':'文件 JSON','mode':'生产方式','count':'版／张数','improvement':'改善投入 point',
 'ordinal':'版／张序号','settled':'已结算','result':'产出结果','started':'开始时间','ends':'截止时间',
 'created':'创建时间','updated':'更新时间','finished':'完成时间','acquired':'获得时间','state':'状态',
 'price_cents':'成交／挂售价（分）','amount_cents':'报价（分）','held_cents':'冻结（分）','start_cents':'起拍价（分）',
 'increment_cents':'最小加价（分）','pricing':'成交规则','clock':'计时规则','duration_seconds':'持续秒数',
 'rolling_seconds':'滚动间隔秒数','extension_seconds':'延时秒数','priority':'同价先后编号','revision':'版本',
 'exported_revision':'文件版本','deleted':'已删除','code':'优惠码','value':'优惠值','active':'已启用',
 'expires':'有效期截止','max_uses':'总次数上限（0 不限）','per_user':'每人上限（0 不限）','created_by':'创建者','slot':'展示位置','request_key':'请求编号'})

def quote(name):
    return '"' + name.replace('"','""') + '"'

def unit_files():
    result = subprocess.run(['systemctl','list-unit-files','--type=service,timer','--no-pager','--output=json'],
                            capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise ValueError(result.stderr.strip() or '无法扫描系统服务')
    return [r for r in json.loads(result.stdout) if UNIT_RE.fullmatch(r['unit_file'])]

def list_services():
    units = unit_files()
    runtime = subprocess.run(['systemctl','list-units','--all','--type=service,timer','--no-pager','--output=json'],
                             capture_output=True,text=True,timeout=20)
    if runtime.returncode:
        raise ValueError(runtime.stderr.strip() or '无法读取运行状态')
    live = {r['unit']:r for r in json.loads(runtime.stdout)}
    # Also include instantiated and transient units visible to systemd.
    known = {r['unit_file'] for r in units}
    units += [{'unit_file':name,'state':'运行实例','preset':None} for name in live if name not in known and UNIT_RE.fullmatch(name)]
    for row in units:
        info = live.get(row['unit_file'],{})
        row.update(active=info.get('active','inactive'),sub=info.get('sub','未加载'),
                   load=info.get('load','not-loaded'),description=info.get('description',''))
    return sorted(units,key=lambda r:r['unit_file'])

def init_admin_panel(app, deps):
    base = Path(deps['base'])
    db_path = base / 'admin_panel.db'
    logs = base / 'admin-panel-logs'
    logs.mkdir(mode=0o700,exist_ok=True)
    @contextmanager
    def connect():
        conn = sqlite3.connect(db_path,timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()
    with connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS authenticators (
          username TEXT PRIMARY KEY, secret TEXT, pending_secret TEXT, pending_until REAL,
          last_step INTEGER NOT NULL DEFAULT -1, failures INTEGER NOT NULL DEFAULT 0,
          locked_until REAL NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS grants (
          token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, expires_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS shortcuts (
          id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, command TEXT NOT NULL,
          created_by TEXT NOT NULL, created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, username TEXT NOT NULL, description TEXT NOT NULL,
          state TEXT NOT NULL, exit_code INTEGER, created_at REAL NOT NULL, finished_at REAL);
        CREATE TABLE IF NOT EXISTS audit (
          id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL, action TEXT NOT NULL,
          target TEXT NOT NULL, created_at REAL NOT NULL);
        ''')
        c.execute("UPDATE jobs SET state='interrupted',finished_at=? WHERE state='running'",(time.time(),))
    os.chmod(db_path,0o600)
    def revoke_grants(username):
        with connect() as c:
            c.execute('DELETE FROM grants WHERE username=?',(username,))
    app.extensions['admin_panel_revoke'] = revoke_grants
    bp=Blueprint('admin_panel',__name__,url_prefix='/admin-panel',template_folder=str(base/'templates'))

    def audit(action,target):
        with connect() as c:
            c.execute('INSERT INTO audit(username,action,target,created_at) VALUES (?,?,?,?)',
                      (session['user'],action,target,time.time()))

    def csrf():
        return session.setdefault('_admin_csrf',secrets.token_urlsafe(32))

    @bp.before_request
    def guard():
        # Role is checked before reading authenticator state or showing any admin UI.
        if session.get('role') != 'admin':
            return redirect('https://google.com')
        user = deps['get_user'](session.get('user',''))
        if not user or user.get('role') != 'admin' or user.get('deleted_at') or user.get('purged_at'):
            return redirect('https://google.com')
        if request.method != 'GET':
            provided = request.headers.get('X-CSRF-Token') or request.form.get('csrf','')
            if not hmac.compare_digest(provided,csrf()):
                return jsonify(error='页面已过期，请刷新后重试'),403
        if request.endpoint != 'admin_panel.entry':
            token=session.get('_admin_grant','')
            with connect() as c:
                grant=c.execute('SELECT * FROM grants WHERE token_hash=? AND username=? AND expires_at>?',
                                (hashlib.sha256(token.encode()).hexdigest(),session['user'],time.time())).fetchone()
            if not grant:
                if request.path.startswith('/admin-panel/api/'):
                    return jsonify(error='管理员验证已过期，请重新输入验证码',reauth=True),401
                return redirect('/admin-panel/')

    @bp.after_request
    def no_cache(response):
        response.headers['Cache-Control']='no-store'
        response.headers['X-Frame-Options']='DENY'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='same-origin'
        return response

    def totp_check(code,bind=False):
        now=time.time()
        with connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT * FROM authenticators WHERE username=?',(session['user'],)).fetchone()
            if not row:
                return False,'验证器未绑定，请重新进入面板'
            if row['locked_until']>now:
                return False,'验证码尝试过多，请在 5 分钟后重试'
            secret=row['secret']
            if bind and not secret and (row['pending_until'] or 0)>now:
                secret=row['pending_secret']
            step=None
            if secret and re.fullmatch(r'[0-9]{6}',code or ''):
                totp=pyotp.TOTP(secret)
                current=int(now//30)
                for candidate in (current,current-1,current+1):
                    if hmac.compare_digest(totp.at(candidate*30),code):
                        step=candidate;break
            if step is None:
                failures=row['failures']+1
                c.execute('UPDATE authenticators SET failures=?,locked_until=? WHERE username=?',
                          (0 if failures>=5 else failures,now+300 if failures>=5 else 0,session['user']))
                return False,'验证码不正确或绑定已过期'
            if step<=row['last_step']:
                return False,'该验证码已使用，请等待下一组验证码'
            c.execute('UPDATE authenticators SET secret=?,pending_secret=NULL,pending_until=NULL,last_step=?,failures=0,locked_until=0 WHERE username=?',
                      (secret,step,session['user']))
        return True,''

    @bp.route('/',methods=['GET','POST'])
    def entry():
        error=''
        if request.method=='POST':
            ok,error=totp_check(request.form.get('code','').strip(),bind=True)
            if ok:
                token=secrets.token_urlsafe(32)
                with connect() as c:
                    c.execute('DELETE FROM grants WHERE expires_at<?',(time.time(),))
                    c.execute('INSERT INTO grants VALUES (?,?,?)',
                              (hashlib.sha256(token.encode()).hexdigest(),session['user'],time.time()+1800))
                session['_admin_grant']=token
                audit('进入管理员面板','')
                return redirect('/admin-panel/dashboard/')
        else:
            # Opening the entry route always requires a fresh authenticator code.
            token=session.pop('_admin_grant','')
            if token:
                with connect() as c:
                    c.execute('DELETE FROM grants WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),))
        with connect() as c:
            c.execute('INSERT OR IGNORE INTO authenticators(username) VALUES (?)',(session['user'],))
            row=c.execute('SELECT * FROM authenticators WHERE username=?',(session['user'],)).fetchone()
            secret=None
            if not row['secret']:
                secret=row['pending_secret'] if (row['pending_until'] or 0)>time.time() else pyotp.random_base32()
                c.execute('UPDATE authenticators SET pending_secret=?,pending_until=? WHERE username=?',
                          (secret,time.time()+600,session['user']))
        uri=pyotp.TOTP(secret).provisioning_uri(name=session['user'],issuer_name='Yitiao Admin') if secret else ''
        return render_template('admin_panel.html',gate=True,csrf=csrf(),error=error,
                               qr=deps['qr'](uri) if uri else '',secret=secret,username=session['user'])

    @bp.get('/dashboard/')
    def dashboard():
        return render_template('admin_panel.html',gate=False,csrf=csrf(),username=session['user'])

    @bp.post('/api/lock')
    def lock():
        token=session.pop('_admin_grant','')
        with connect() as c:
            c.execute('DELETE FROM grants WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),))
        return jsonify(ok=True)

    @bp.errorhandler(ValueError)
    def invalid(error):
        return jsonify(error=str(error)),400

    @bp.errorhandler(sqlite3.Error)
    def sql_error(error):
        app.logger.exception('Admin SQLite operation failed')
        return jsonify(error='数据库读取失败，请检查文件和表结构'),400

    @bp.errorhandler(subprocess.TimeoutExpired)
    def system_timeout(error):
        return jsonify(error='读取系统状态超时，请稍后重试'),504

    @bp.get('/api/services')
    def services():
        return jsonify(units=list_services())

    @bp.get('/api/service-detail')
    def service_detail():
        unit=request.args.get('unit','')
        if unit not in {r['unit_file'] for r in list_services()}:
            raise ValueError('服务不存在')
        info=subprocess.run(['systemctl','show',unit,'--no-pager'],capture_output=True,text=True,timeout=20)
        definition=subprocess.run(['systemctl','cat',unit,'--no-pager'],capture_output=True,text=True,timeout=20)
        return jsonify(properties=info.stdout,definition=definition.stdout or definition.stderr)

    def start_job(description,argv=None,command=None):
        job=secrets.token_hex(16)
        owner=session['user']
        with connect() as c:
            c.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?)',(job,owner,description,'running',None,time.time(),None))
        audit('运行任务',description)
        def worker():
            path=logs/(job+'.log')
            proc=None
            state='finished';code=-1
            try:
                fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(fd,'wb') as out:
                    proc=subprocess.Popen(argv if argv else ['/bin/bash','-c',command],cwd=base,
                                          stdout=subprocess.PIPE,stderr=subprocess.STDOUT,start_new_session=True,
                                          stdin=subprocess.DEVNULL)
                    timed_out=threading.Event()
                    def kill():
                        # Kill the group even when a shell exited but background children hold stdout.
                        try:os.killpg(proc.pid,signal.SIGKILL)
                        except ProcessLookupError:pass
                    def expire():
                        timed_out.set();kill()
                    timer=threading.Timer(JOB_TIMEOUT,expire);timer.daemon=True;timer.start()
                    try:
                        size=0
                        while True:
                            chunk=os.read(proc.stdout.fileno(),4096)
                            if not chunk:break
                            size+=len(chunk)
                            if size>1024*1024:
                                out.write('\n输出已达到 1 MiB，任务终止。\n'.encode());out.flush();state='output_limit';kill();break
                            out.write(chunk);out.flush()
                        code=proc.wait(timeout=5)
                        if timed_out.is_set() and state=='finished':state='timeout'
                    finally:
                        timer.cancel();proc.stdout.close()
            except Exception as error:
                state='failed'
                if proc and proc.poll() is None:
                    try:os.killpg(proc.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                with path.open('ab') as out:out.write(('\n'+str(error)).encode())
            finally:
                with connect() as c:
                    c.execute('UPDATE jobs SET state=?,exit_code=?,finished_at=? WHERE id=?',(state,code,time.time(),job))
        threading.Thread(target=worker,daemon=True).start()
        return job

    @bp.post('/api/service-action')
    def service_action():
        data=request.get_json() or {};unit=data.get('unit','');action=data.get('action','')
        if action not in ('restart','reload','stop') or unit not in {r['unit_file'] for r in list_services()}:
            raise ValueError('无效的服务或操作')
        return jsonify(job=start_job(action+' '+unit,argv=['systemctl',action,unit]))

    @bp.get('/api/shortcuts')
    def shortcuts():
        with connect() as c:
            return jsonify(shortcuts=[dict(r) for r in c.execute('SELECT * FROM shortcuts ORDER BY id DESC')])

    @bp.post('/api/shortcuts')
    def add_shortcut():
        data=request.get_json() or {}
        name=str(data.get('name','')).strip();command=str(data.get('command',''))
        if not name or len(name)>100 or not command.strip() or len(command)>32000:
            raise ValueError('请输入名称（最多 100 字）和命令（最多 32000 字）')
        ok,error=totp_check(str(data.get('code','')).strip())
        if not ok:return jsonify(error=error),403
        with connect() as c:
            c.execute('INSERT INTO shortcuts(name,command,created_by,created_at) VALUES (?,?,?,?)',
                      (name,command,session['user'],time.time()))
        audit('添加快捷指令',name)
        return jsonify(ok=True)

    @bp.post('/api/shortcuts/<int:shortcut>/run')
    def run_shortcut(shortcut):
        with connect() as c:
            row=c.execute('SELECT * FROM shortcuts WHERE id=?',(shortcut,)).fetchone()
        if not row:raise ValueError('快捷指令不存在')
        return jsonify(job=start_job(row['name'],command=row['command']))

    @bp.get('/api/jobs')
    def jobs():
        with connect() as c:
            return jsonify(jobs=[dict(r) for r in c.execute('SELECT * FROM jobs ORDER BY created_at DESC LIMIT 30')])

    @bp.get('/api/jobs/<job>')
    def job_result(job):
        if not re.fullmatch('[0-9a-f]{32}',job):raise ValueError('任务编号无效')
        with connect() as c:
            row=c.execute('SELECT * FROM jobs WHERE id=?',(job,)).fetchone()
        if not row:raise ValueError('任务不存在')
        path=logs/(job+'.log')
        return jsonify(job=dict(row),output=path.read_bytes()[:1024*1024].decode('utf-8',errors='replace') if path.exists() else '')

    def db_files():
        # Discover data files, never accept a caller-supplied filesystem path.
        return {p.name:p for p in sorted(base.glob('*.db')) if p.is_file() and not p.is_symlink() and p.name!='admin_panel.db'}

    @contextmanager
    def readonly(name):
        paths=db_files()
        if name not in paths:raise ValueError('数据库不存在')
        c=sqlite3.connect(paths[name].as_uri()+'?mode=ro',uri=True,timeout=3)
        c.row_factory=sqlite3.Row
        c.execute('PRAGMA query_only=ON')
        deadline=time.monotonic()+3
        c.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
        try:
            yield c
        finally:
            c.close()

    @bp.get('/api/databases')
    def databases():
        result=[]
        for name in db_files():
            try:
                with readonly(name) as c:
                    tables=[{'name':r[0],'label':TABLES.get(r[0],r[0])} for r in c.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
                result.append({'name':name,'tables':tables})
            except sqlite3.Error:
                result.append({'name':name,'tables':[],'error':'无法读取'})
        return jsonify(databases=result)

    def display_value(column,value,table=None):
        lower=column.lower()
        if table and table.startswith('yc_'):
            if column=='code':return value
            states={'active':'进行中','done':'已完成','sold':'已成交','unsold':'流拍','cancelled':'已取消',
                    'withdrawn':'已下架','personal':'个人','factory':'工厂','sealed':'密封竞价','public':'公开拍卖',
                    'first':'第一价格','second':'第二价格','fixed':'固定时间','rolling':'滚动截止','rate':'支付比例','admin_test':'管理员测试'}
            if column in ('state','mode','kind','pricing','clock'):return states.get(value,value)
            if column in ('active','settled','deleted'):return '是' if value else '否'
            if column in ('created','updated','started','ends','finished','acquired','expires') and value is not None:
                return time.strftime('%Y-%m-%d %H:%M:%S',time.localtime(value))+'（'+str(value)+'）'
            if column in ('design','payload','geometry','defects','result') and isinstance(value,str):
                try:
                    def compact(v):
                        if isinstance(v,dict):return {k:compact(x) for k,x in v.items()}
                        if isinstance(v,list):return [compact(x) for x in v]
                        if isinstance(v,str) and v.startswith('data:image/'):return '[内嵌图片资源]'
                        return v
                    value=json.dumps(compact(json.loads(value)),ensure_ascii=False,indent=2)
                except (ValueError,TypeError):pass
        if column=='password_hash':return '已设置' if value else '未设置'
        if column=='totp_secret':return '已绑定' if value else '未绑定'
        if column=='google_sub':return '已绑定' if value else '未绑定'
        if column=='recovery_codes':
            try:
                codes=json.loads(value or '[]');return f"剩余 {sum(not x.get('used') for x in codes)} / {len(codes)}"
            except (ValueError,TypeError,AttributeError):return '已设置' if value else '未设置'
        if any(part in lower for part in ('password','secret','token','code','nonce','challenge')):
            return '已设置（已隐藏）' if value else '—'
        if column=='role':return {'guest':'访客 guest','user':'用户 user','admin':'管理员 admin'}.get(value,value)
        if column=='status':return {'active':'进行中','jumped':'已跳出','crashed':'已坠落'}.get(value,value)
        if column in ('totp_on_login','pinned','consumed'):return '是' if value else '否'
        if isinstance(value,bytes):return f'二进制数据（{len(value)} 字节）'
        if value is None:return '尚无记录' if column=='last_login_at' else '—'
        text=str(value)
        if column.endswith('_cents'):
            try:return f'{value}（{int(value)/100:.2f} 点）'
            except (ValueError,TypeError):pass
        return text[:8000]+('…（已截断）' if len(text)>8000 else '')

    @bp.get('/api/table')
    def table():
        name=request.args.get('db','account.db');table=request.args.get('table','users')
        try:page=max(1,int(request.args.get('page','1')))
        except ValueError:raise ValueError('页码无效')
        search=request.args.get('search','').strip()[:200];user=request.args.get('user','').strip()[:200]
        with readonly(name) as c:
            if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():
                raise ValueError('表不存在')
            columns=[dict(r) for r in c.execute('PRAGMA table_info('+quote(table)+')')]
            names=[r['name'] for r in columns];where=[];params=[]
            identities=[x for x in ('username','user','owner','producer','seller','buyer','winner','created_by') if x in names]
            if user:
                if identities:
                    where.append('('+' OR '.join(quote(x)+'=?' for x in identities)+')');params.extend([user]*len(identities))
                elif table=='yc_sheets' and name=='userdata.db':
                    where.append('job IN (SELECT id FROM yc_jobs WHERE user=?)');params.append(user)
                elif table=='yc_defects' and name=='userdata.db':
                    where.append('tid IN (SELECT tid FROM yc_templates WHERE owner=?)');params.append(user)
                else:return jsonify(columns=columns,rows=[],has_next=False,page=page,message='此表没有用户关联字段')
            if search:
                searchable=[x for x in names if (table.startswith('yc_') and x=='code') or not any(part in x.lower() for part in ('password','secret','token','code','nonce','challenge'))]
                if searchable:
                    where.append('('+' OR '.join('instr(CAST('+quote(x)+' AS TEXT),?)>0' for x in searchable)+')')
                    params.extend([search]*len(searchable))
            query='SELECT * FROM '+quote(table)+(' WHERE '+' AND '.join(where) if where else '')
            order=next((x for x in ('id','created_at','recording_time','started_at') if x in names),None)
            if order:query+=' ORDER BY '+quote(order)+' DESC'
            else:
                primary=[x['name'] for x in columns if x['pk']]
                if primary:query+=' ORDER BY '+','.join(quote(x) for x in primary)
                else:query+=' ORDER BY '+','.join(quote(x) for x in names)
            rows=c.execute(query+' LIMIT 51 OFFSET ?',params+[(page-1)*50]).fetchall()
            result=[]
            for r in rows[:50]:
                raw=dict(r)
                shown={k:display_value(k,v,table) for k,v in raw.items()}
                if table=='yc_coupons':
                    shown['value'] = f"支付 {raw['value']/100:g}%" if raw['kind']=='rate' else f"减免 {raw['value']} point"
                if name=='account.db' and table=='users':
                    shown['_account_state']='永久停用' if raw.get('purged_at') else '待删除（安全期）' if raw.get('deleted_at') else '正常'
                    shown['_twofa_methods']=' / '.join(x for x,yes in [('身份验证器',raw.get('totp_secret')),('邮箱',raw.get('email')),('备用验证码',raw.get('recovery_codes'))] if yes) or '未设置'
                result.append(shown)
            if name=='account.db' and table=='users':
                columns += [{'name':'_account_state','type':'TEXT'},{'name':'_twofa_methods','type':'TEXT'}]
            for column in columns:column['label']={'_account_state':'用户状态','_twofa_methods':'两步验证方式'}.get(column['name'],LABELS.get(column['name'],column['name']))
        return jsonify(columns=columns,rows=result,page=page,has_next=len(rows)>50)

    def ycards_store():
        store=app.extensions.get('ycards')
        if not store:raise ValueError('YCards 尚未初始化')
        return store

    @bp.get('/api/ycards')
    def ycards_overview():
        import ycards_promotions
        store=ycards_store()
        with store.connect() as c:
            counts={name:c.execute('SELECT COUNT(*) FROM '+quote(name)).fetchone()[0] for name in TABLES if name.startswith('yc_')}
        return jsonify(counts=counts,coupons=ycards_promotions.listing(store))

    @bp.post('/api/ycards/coupons')
    def ycards_coupon_create():
        import ycards_promotions
        from ycards import CardError
        try:result=ycards_promotions.create(ycards_store(),session['user'],request.get_json())
        except CardError as error:return jsonify(error=error.message),error.status
        audit('YCards 添加优惠码',result['code'])
        return jsonify(**result,message='优惠码 '+result['code']+' 已创建')

    @bp.post('/api/ycards/coupons/toggle')
    def ycards_coupon_toggle():
        import ycards_promotions
        from ycards import CardError
        data=request.get_json()
        try:result=ycards_promotions.toggle(ycards_store(),data)
        except CardError as error:return jsonify(error=error.message),error.status
        audit('YCards 优惠码'+('启用' if data['active'] else '停用'),data['code'])
        return jsonify(result)

    @bp.post('/api/users')
    def create_user():
        data=request.get_json() or {};username=str(data.get('username','')).strip()
        password=str(data.get('password',''));role=data.get('role')
        email=str(data.get('email','')).strip();nickname=str(data.get('nickname','')).strip()
        error=deps['validate_username'](username) or deps['validate_password'](password,password)
        if role not in ('guest','user'):error='只能创建 guest 或 user'
        if email:error=error or deps['validate_email'](email)
        if error:raise ValueError(error)
        deps['create_user'](username,password,role=role,email=email or None,nickname=nickname)
        audit('创建用户',username)
        return jsonify(ok=True)

    @bp.post('/api/users/delete')
    def delete_user():
        data=request.get_json() or {};username=str(data.get('username','')).strip()
        user=deps['get_user'](username)
        if not user:raise ValueError('用户不存在')
        if user.get('role')=='admin':raise ValueError('不能在此删除管理员账户')
        if user.get('purged_at'):raise ValueError('该用户已永久停用')
        if data.get('confirm')!=username:raise ValueError('请输入要删除的完整用户名以确认')
        ok,error=totp_check(str(data.get('code','')).strip())
        if not ok:return jsonify(error=error),403
        deps['revoke'](username)
        deps['mark_delete'](username)
        deps['purge'](username)
        deps['update_user'](username,role='guest')
        audit('永久停用用户',username)
        return jsonify(ok=True,message='账户已永久停用，用户名占位及点数、游戏历史保留')

    app.register_blueprint(bp)
    return bp
