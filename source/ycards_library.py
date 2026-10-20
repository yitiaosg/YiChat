"""Mutable names/archive state live outside immutable card designs."""
import json
import time
import ycards as yc

SCHEMA='''
CREATE TABLE IF NOT EXISTS yc_card_names(instance TEXT PRIMARY KEY REFERENCES yc_instances(id), name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS yc_save_requests(user TEXT NOT NULL, request_key TEXT NOT NULL, hash TEXT NOT NULL, result TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(user,request_key));
'''

class LibraryMixin:
    @staticmethod
    def library_migrate(conn):
        # Columns keep list summaries independent of multi-megabyte design blobs.
        conn.execute('BEGIN IMMEDIATE')
        for table,columns in [('yc_templates',{'material':'TEXT'}),('yc_drafts',{'name':'TEXT','material':'TEXT','cost':'TEXT','archived':'INTEGER NOT NULL DEFAULT 0'})]:
            existing={r['name'] for r in conn.execute('PRAGMA table_info('+table+')')}
            for name,definition in columns.items():
                if name not in existing:conn.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
        for r in conn.execute('SELECT tid,design FROM yc_templates WHERE material IS NULL').fetchall():
            conn.execute('UPDATE yc_templates SET material=? WHERE tid=?',(json.loads(r['design'])['material'],r['tid']))
        for r in conn.execute('SELECT id,design FROM yc_drafts WHERE material IS NULL').fetchall():
            d=json.loads(r['design']);conn.execute('UPDATE yc_drafts SET name=?,material=?,cost=? WHERE id=?',(d['name'],d['material'],str(d['cost']),r['id']))
        conn.execute('INSERT OR IGNORE INTO yc_card_names SELECT i.id,t.name FROM yc_instances i JOIN yc_templates t ON t.tid=i.tid')

    @staticmethod
    def display_name(value):
        if not isinstance(value,str) or not 1<=len(value)<=20:
            raise yc.CardError('名称须为 1～20 个字符')
        return value

    def rename(self,user,data):
        name=self.display_name(data.get('name'))
        targets=[k for k in ('tid','draft_id','instance_id') if data.get(k) is not None]
        if len(targets)!=1:raise yc.CardError('请选择一个模板、草稿或卡片')
        target=targets[0];value=data[target];revision=None
        with self.transaction() as conn:
            if target=='tid':
                self.owned_template(conn,user,value)
                conn.execute('UPDATE yc_templates SET name=? WHERE tid=?',(name,value))
            elif target=='instance_id':
                yc.identifier(value,'实例 ID',r'[a-f0-9]{32}');self.available_card(conn,user,value)
                conn.execute('INSERT INTO yc_card_names VALUES(?,?) ON CONFLICT(instance) DO UPDATE SET name=excluded.name',(value,name))
            else:
                yc.identifier(value,'草稿 ID',r'[a-f0-9]{32}')
                row=conn.execute('SELECT design,revision FROM yc_drafts WHERE id=? AND owner=?',(value,user)).fetchone()
                if not row:raise yc.CardError('草稿不存在或不属于当前用户',404)
                design=json.loads(row['design']);design['name']=name;revision=row['revision']+1
                conn.execute('UPDATE yc_drafts SET name=?,design=?,revision=?,updated=? WHERE id=?',(name,yc.canonical(design),revision,time.time(),value))
        if target=='draft_id':self.publish(draft_id=value)
        return dict(ok=True,name=name,revision=revision)

    def archive(self,user,data):
        archived=data.get('archived')
        if not isinstance(archived,bool):raise yc.CardError('归档状态无效')
        if bool(data.get('tid'))==bool(data.get('draft_id')):raise yc.CardError('请选择一个模板或草稿')
        with self.transaction() as conn:
            if data.get('tid'):
                tid=data['tid'];self.owned_template(conn,user,tid)
                conn.execute('UPDATE yc_templates SET deleted=? WHERE tid=?',(int(archived),tid))
            else:
                did=yc.identifier(data['draft_id'],'草稿 ID',r'[a-f0-9]{32}')
                row=conn.execute('UPDATE yc_drafts SET archived=? WHERE id=? AND owner=?',(int(archived),did,user))
                if not row.rowcount:raise yc.CardError('草稿不存在或不属于当前用户',404)
        return dict(ok=True,archived=archived)

    def save_status(self,user,key):
        yc.identifier(key,'保存请求编号',r'[a-zA-Z0-9_-]{16,100}')
        with self.connect() as conn:
            row=conn.execute('SELECT result FROM yc_save_requests WHERE user=? AND request_key=?',(user,key)).fetchone()
            return dict(saved=bool(row),result=json.loads(row['result']) if row else None)
