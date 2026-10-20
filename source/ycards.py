"""YCards. Uses website's identity and points' existing SQLite connection.

All economic writes use BEGIN IMMEDIATE on userdata.db. Immutable file payloads
are committed in SQLite first, then exported with no-overwrite atomic links.
The durable DB payload is an outbox that repairs missing files after a crash.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
import zlib
import xml.etree.ElementTree as ET
from collections import Counter
from contextlib import contextmanager, nullcontext
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from urllib.parse import urlencode, quote as url_quote

from flask import Blueprint, jsonify, redirect, render_template, request, session
from PIL import Image, UnidentifiedImageError
import points
import ycards_promotions as promotions

VERSION = 2
EFFECTS = {'image': ['image'], 'foil': ['color', 'mono'],
           'glow': ['pulse', 'star', 'ring', 'bar'], 'filter': ['negative', 'lenticular'],
           'material': ['gold', 'silver', 'vinyl']}
MAX_POINTS = 10**12  # Exact integer arithmetic in SQLite and JavaScript.
MAX_BODY = 32 * 1024 * 1024
CARDS = Path(__file__).resolve().parent / 'cards'
RNG = secrets.SystemRandom()
GUEST_MESSAGE = '当前为guest用户，升级user等级以继续'


class CardError(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def integer(v, label, lo=0, hi=MAX_POINTS):
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise CardError(f'{label}须为 {lo}～{hi} 的整数')
    return v


def identifier(v, label, pattern=r'[A-Za-z0-9]{1,12}'):
    if not isinstance(v, str) or not re.fullmatch(pattern, v):
        raise CardError(f'{label}格式无效')
    return v


def number(v, label, lo, hi):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi:
        raise CardError(f'{label}超出范围 {lo}～{hi}')
    return v


def choice(v, values, label):
    if not isinstance(v, str) or v not in values:
        raise CardError(f'{label}无效')
    return v


def asset(value, mask=False):
    """Embedded assets only. Re-encode PNG to remove metadata and validate pixels.
    SVG has a strict geometry-only allowlist: no CSS, links, images or animation.
    Client renders masks to binary PNG; server repeats PNG alpha normalization.
    """
    if value is None or value == '':
        return None
    if not isinstance(value, str) or len(value) > 6 * 1024 * 1024:
        raise CardError('图片资源过大（每张不超过 4 MB）')
    match = re.fullmatch(r'data:image/(png|svg\+xml);base64,([A-Za-z0-9+/=\r\n]+)', value)
    if not match or (not mask and match[1] != 'png'):
        raise CardError('图片仅支持 PNG；遮罩支持 PNG / SVG')
    try:
        raw = base64.b64decode(match[2], validate=True)
        if len(raw) > 4 * 1024 * 1024:
            raise CardError('图片资源过大')
        if match[1] == 'png':
            im = Image.open(io.BytesIO(raw))
            if im.format != 'PNG' or max(im.size) > 2048 or min(im.size) < 1 or getattr(im, 'n_frames', 1) != 1:
                raise CardError('请使用不超过 2048×2048 的静态 PNG')
            im.load()
            im = im.convert('RGBA')
            if mask:
                alpha = im.getchannel('A').point(lambda a: 255 if a else 0)
                im = Image.new('RGBA', im.size, 'white')
                im.putalpha(alpha)
            out = io.BytesIO()
            im.save(out, format='PNG')
            raw = out.getvalue()
        else:
            if b'<!' in raw or len(raw) > 512 * 1024:
                raise CardError('SVG 不允许声明、实体或过大内容')
            root = ET.fromstring(raw)
            tags = {'svg', 'g', 'path', 'rect', 'circle', 'ellipse', 'line', 'polyline', 'polygon'}
            attrs = {'width', 'height', 'viewBox', 'x', 'y', 'x1', 'x2', 'y1', 'y2', 'cx', 'cy', 'r', 'rx', 'ry',
                     'd', 'points', 'transform', 'fill', 'stroke', 'stroke-width', 'fill-rule', 'stroke-linecap',
                     'stroke-linejoin', 'opacity', 'fill-opacity', 'stroke-opacity', 'preserveAspectRatio'}
            if root.tag.split('}')[-1] != 'svg' or len(list(root.iter())) > 2000:
                raise CardError('SVG 结构无效')
            for node in root.iter():
                if node.tag.split('}')[-1] not in tags:
                    raise CardError('SVG 仅支持基础矢量形状，禁止脚本和外部资源')
                for key, val in list(node.attrib.items()):
                    if key not in attrs or any(s in val.lower() for s in ('url', 'href', 'http', 'data:', 'javascript', 'expression')):
                        raise CardError('SVG 含不允许的属性或外部资源')
                    if len(val) > 100000:
                        raise CardError('SVG 路径过长')
                node.text = None
                node.tail = None
            raw = ET.tostring(root, encoding='utf-8')
    except (ValueError, ET.ParseError, UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise CardError('图片或 SVG 无效') from exc
    return 'data:image/' + match[1] + ';base64,' + base64.b64encode(raw).decode()


def validate_design(data):
    if not isinstance(data, dict):
        raise CardError('设计格式无效')
    name = data.get('name', '')
    if not isinstance(name, str) or not 1 <= len(name) <= 20:
        raise CardError('名称须为 1～20 个字符')
    material = choice(data.get('material'), ('plastic', 'glass'), '材质')
    orientation = choice(data.get('orientation'), ('portrait', 'landscape'), '尺寸')
    width, height = (53.98, 85.60) if orientation == 'portrait' else (85.60, 53.98)
    result = dict(format_version=VERSION, name=name, material=material, orientation=orientation,
                  width_mm=width, height_mm=height, thickness_mm=.76, corner_radius_mm=3.18,
                  corners=choice(data.get('corners'), ('round', 'square'), '边角'),
                  color=choice(data.get('color'), ('color', 'mono'), '颜色'), layers=[])
    layers = data.get('layers', [])
    if not isinstance(layers, list) or len(layers) > 320:
        raise CardError('每类最多 64 层')
    assets = {}
    def checked_asset(value, mask=False):
        if value is None or value=='':return None
        if not isinstance(value,str):return asset(value,mask)
        key=(value,mask)
        if key not in assets:assets[key]=asset(value,mask)
        return assets[key]
    counts, ids = Counter(), set()
    for i, layer in enumerate(layers):
        if not isinstance(layer, dict):
            raise CardError('图层格式无效')
        kind = choice(layer.get('type'), EFFECTS, '图层类型')
        counts[kind] += 1
        if counts[kind] > 64:
            raise CardError('每类最多 64 层，正反面共用上限')
        lid = layer.get('id')
        if not isinstance(lid, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', lid) or lid in ids:
            raise CardError('图层 ID 无效或重复')
        ids.add(lid)
        side = choice(layer.get('side', 'front'), ('front', 'back') if material == 'glass' else ('front',), '正反面')
        position = choice(layer.get('position', 'surface'), ('front', 'surface', 'behind') if kind == 'image' else ('front', 'surface'), '空间位置')
        distance = number(layer.get('distance_mm', 0), '距离', 0, 100)
        if position == 'surface':
            distance = 0
        scope = choice(layer.get('scope', 'full'), ('full', 'custom'), '效果范围')
        mask = checked_asset(layer.get('mask'), True) if kind != 'image' else None
        if kind != 'image' and scope == 'full' and mask:
            raise CardError('自定义遮罩必须使用自定义范围')
        if kind != 'image' and scope == 'custom' and not mask:
            raise CardError('自定义范围需要遮罩')
        # A custom region and a repeated pattern are independent binary masks.
        scope = 'custom' if mask else 'full'
        effect = layer.get('effect')
        legacy_repeat = kind == 'foil' and effect in ('text', 'pattern')
        if legacy_repeat:
            effect = 'color'
        if kind == 'glow' and effect == 'circle':
            effect = 'ring'
        item = dict(id=lid, type=kind, effect=choice(effect, EFFECTS[kind], '效果'),
                    side=side, order=i, position=position, distance_mm=distance,
                    canvas_width_mm=round(width + 2*distance, 4), canvas_height_mm=round(height + 2*distance, 4),
                    x=number(layer.get('x', 0), '横向位置', -300, 300),
                    y=number(layer.get('y', 0), '纵向位置', -300, 300),
                    width=number(layer.get('width', width), '内容宽度', .1, 400),
                    height=number(layer.get('height', height), '内容高度', .1, 400),
                    rotation=number(layer.get('rotation', 0), '旋转角', -360, 360),
                    scope=scope, mask=mask)
        if kind == 'image':
            item['image'] = checked_asset(layer.get('image'))
            if not item['image']:
                raise CardError('图片图层需要 PNG 图片')
        if kind != 'image':
            repeat = layer.get('repeat_mask')
            if legacy_repeat and repeat is None:
                repeat = dict(kind='pattern' if layer.get('pattern') else 'text',
                              text=layer.get('text', 'YCARDS'), pattern=layer.get('pattern'),
                              density=layer.get('density', 6), angle=layer.get('angle', -25),
                              size_mm=height/layer.get('density', 6)*.55 if isinstance(layer.get('density', 6), int) and layer.get('density', 6)>0 else 6)
            item['repeat_mask'] = validate_repeat(repeat, checked_asset)
        if kind == 'glow':
            tint = layer.get('tint', '#bdeaff')
            if not isinstance(tint, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', tint):
                raise CardError('光晕颜色须为六位十六进制颜色')
            item.update(glow_width=number(layer.get('glow_width', 4), '光晕宽度', .1, 100),
                        feather=number(layer.get('feather', .65), '羽化度', 0, 1),
                        opacity=number(layer.get('opacity', 1), '光晕透明度', 0, 1), tint=tint)
        if kind == 'filter' and effect == 'lenticular':
            item.update(pitch_mm=number(layer.get('pitch_mm', .7), '光栅间距', .2, 4),
                        strength=number(layer.get('strength', .65), '光栅强度', 0, 1))
        result['layers'].append(item)
    cost = template_cost(result)
    result['cost'] = str(cost) if cost > 2**53-1 else cost
    return result


def validate_repeat(repeat, checked_asset=asset):
    if repeat is None:
        return None
    if not isinstance(repeat, dict):
        raise CardError('多行遮罩格式无效')
    kind = choice(repeat.get('kind', 'text'), ('text', 'pattern'), '多行遮罩类型')
    text = repeat.get('text', 'YCARDS')
    if not isinstance(text, str) or not re.fullmatch(r'[\x20-\x7E]{1,100}', text):
        raise CardError('遮罩文字须为 1～100 个英文字符')
    pattern = checked_asset(repeat.get('pattern'), True)
    if kind == 'pattern' and not pattern:
        raise CardError('图案多行遮罩需要 PNG／SVG')
    return dict(kind=kind, text=text, pattern=pattern,
                font=choice(repeat.get('font', 'sans'), ('sans','serif','mono'), '字体'),
                size_mm=number(repeat.get('size_mm', 6), '图案／字体大小', .5, 100),
                pattern_width_mm=number(repeat.get('pattern_width_mm', 12), '图案宽度', .5, 200),
                spacing_mm=number(repeat.get('spacing_mm', 3), '图案横向间距', 0, 100),
                density=integer(repeat.get('density', 6), '排列行密度', 1, 64),
                angle=number(repeat.get('angle', -25), '行倾斜角', -180, 180),
                x=number(repeat.get('x', 0), '遮罩横向偏移', -300, 300),
                y=number(repeat.get('y', 0), '遮罩纵向偏移', -300, 300))


def template_cost(design):
    # Exact rational arithmetic: all additions first, only one final ceiling.
    a, numerator, denominator = 100, 1, 1
    if design['material'] == 'glass':
        numerator *= 2
    if design['color'] == 'mono':
        numerator *= 3
        denominator *= 4
    for layer in design['layers']:
        kind, pos = layer['type'], layer['position']
        masks = int(bool(layer.get('mask'))) + int(bool(layer.get('repeat_mask')))
        if kind == 'image':
            a += {'front': 200, 'surface': 0, 'behind': 150}[pos]
        elif kind == 'foil':
            a += 300 + masks*200 + (100 if pos == 'front' else 0)
        elif kind == 'glow':
            a += 200 + masks*200 + (50 if pos == 'front' else 0)
        else:
            if layer['effect'] == 'vinyl':
                numerator *= 2
            else:
                numerator *= 3
                denominator *= 2
            numerator *= 5**masks
            denominator *= 4**masks
    return (a*numerator + denominator-1)//denominator


def production_quote(cost, maturity, mode, count, improvement):
    choice(mode, ('personal', 'factory'), '生产方式')
    integer(count, '版数', 1, 100)
    integer(improvement, '改善投入', 0, 100000)
    if improvement % 1000 or (mode == 'personal' and (count != 1 or improvement)):
        raise CardError('个人生产一次一张；工厂改善投入须为 1000 的整数倍')
    sheets = []
    for _ in range(count):
        price = (cost * (100-maturity) * (4 if mode == 'factory' else 1) + 99)//100
        sheets.append(dict(maturity=maturity, cost=price, seconds=price/100))
        maturity = min(64, maturity + (3 if mode == 'factory' else 1))
    total = sum(s['cost'] for s in sheets) + improvement
    if total > MAX_POINTS:
        raise CardError('批次费用过高')
    return dict(sheets=sheets, total=total, seconds=sum(s['seconds'] for s in sheets), improvement=improvement)


def sample_defects(design, mode, improvement=0, rng=None):
    rng = rng or RNG
    scale = 2 ** (-improvement//1000)
    defects = {'layers': [], 'cuts': [], 'destroyed': False}
    if design['material'] == 'glass':
        defects['destroyed'] = rng.random() < ((.25 if mode == 'personal' else .5*scale))
    if mode == 'personal':
        return defects
    for layer in design['layers']:
        missing = rng.random() < .02*scale
        skew = rng.random() < .01*scale
        if missing or skew:
            d = dict(id=layer['id'], missing=missing)
            if skew:
                d['offset'] = [round(rng.uniform(-design['width_mm']/2, design['width_mm']/2), 6),
                               round(rng.uniform(-design['height_mm']/2, design['height_mm']/2), 6)]
            defects['layers'].append(d)
    cuts = [('x', n) for n in range(1, 4)] + [('y', n) for n in range(1, 4)]
    if design['corners'] == 'round':
        cuts += [(c, 0) for c in ('tl', 'tr', 'bl', 'br')]
    for axis, line in cuts:
        missing, skew = rng.random() < .02*scale, rng.random() < .01*scale
        if missing or skew:
            # Corner die moves along its diagonal normal, limited in both axes.
            limit = (design['width_mm'] if axis == 'x' else design['height_mm'] if axis == 'y'
                     else min(design['width_mm'], design['height_mm'])) / 4
            d = dict(axis=axis, line=line, missing=missing)
            if skew:
                d['offset'] = round(rng.uniform(-limit, limit), 6)
            defects['cuts'].append(d)
    return defects


def empty_defects(d):
    return not d.get('destroyed') and not d.get('layers') and not d.get('cuts')


def product_geometry(design, defects, mode):
    """Full-line cuts split the 4x4 sheet into rectangular connected components.
    Straight cuts remove the round offcut sectors at every grid node they cross.
    Four additional dies touch ONLY the four outside corners of the whole sheet.
    Missing full-line cuts join cells; retained edge scraps remain in the outline.
    """
    w, h = design['width_mm'], design['height_mm']
    if mode == 'personal':
        return [dict(cells=[1], bounds=[0, 0, w, h], origin=[0, 0],
                     corners=[3.18 if design['corners'] == 'round' else 0]*4, offcuts=[], corner_radius_mm=3.18)]
    cuts = {(d['axis'], d['line']): d for d in defects['cuts']}
    xs, ys = [(0, 0.0)], [(0, 0.0)]
    for axis, size, out in [('x', w, xs), ('y', h, ys)]:
        for n in range(1, 4):
            d = cuts.get((axis, n), {})
            if not d.get('missing'):
                out.append((n, n*size + d.get('offset', 0)))
        out.append((4, 4*size))
    products = []
    for yi in range(len(ys)-1):
        for xi in range(len(xs)-1):
            left, right = xs[xi], xs[xi+1]
            top, bottom = ys[yi], ys[yi+1]
            cells = [row*4+col+1 for row in range(top[0], bottom[0]) for col in range(left[0], right[0])]
            radii, offcuts = [], []
            effective_x = {n for n, _ in xs if n not in (0, 4)}
            effective_y = {n for n, _ in ys if n not in (0, 4)}
            def sector(q, gx, gy):
                outside = (gx, gy) in ((0, 0), (4, 0), (0, 4), (4, 4))
                loss = cuts.get((q, 0), {}) if outside else {}
                removed = (not loss.get('missing')) if outside else gx in effective_x or gy in effective_y
                radius = max(0, 3.18 + loss.get('offset', 0)) if removed else 0
                return dict(node=[gx, gy], quadrant=q, removed=bool(removed), radius=radius)
            for q, gx, gy in [('tl', left[0], top[0]), ('tr', right[0], top[0]),
                              ('bl', left[0], bottom[0]), ('br', right[0], bottom[0])]:
                radii.append(sector(q, gx, gy)['radius'] if design['corners'] == 'round' else 0)
            if design['corners'] == 'round':
                for row in range(top[0], bottom[0]):
                    for col in range(left[0], right[0]):
                        for q, gx, gy in [('tl', col, row), ('tr', col+1, row),
                                          ('bl', col, row+1), ('br', col+1, row+1)]:
                            offcuts.append(sector(q, gx, gy))
            products.append(dict(cells=cells, bounds=[left[1], top[1], right[1], bottom[1]],
                                 origin=[left[0]*w, top[0]*h], corners=radii, offcuts=offcuts,
                                 factory=True, corner_radius_mm=3.18, cut_model=2))
    return products


SCHEMA = '''
CREATE TABLE IF NOT EXISTS yc_templates(tid TEXT PRIMARY KEY COLLATE BINARY, owner TEXT NOT NULL, name TEXT NOT NULL, design TEXT NOT NULL, cost TEXT NOT NULL, created REAL NOT NULL, deleted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS yc_drafts(id TEXT PRIMARY KEY, owner TEXT NOT NULL, design TEXT NOT NULL, updated REAL NOT NULL, revision INTEGER NOT NULL DEFAULT 1, exported_revision INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS yc_stats(user TEXT NOT NULL, tid TEXT NOT NULL REFERENCES yc_templates(tid), maturity INTEGER NOT NULL DEFAULT 0 CHECK(maturity BETWEEN 0 AND 64), quantity INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(user,tid));
CREATE TABLE IF NOT EXISTS yc_sequences(user TEXT PRIMARY KEY, number INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS yc_jobs(id TEXT PRIMARY KEY, user TEXT NOT NULL, tid TEXT NOT NULL REFERENCES yc_templates(tid), mode TEXT NOT NULL, count INTEGER NOT NULL, improvement INTEGER NOT NULL, total INTEGER NOT NULL, started REAL NOT NULL, ends REAL NOT NULL, state TEXT NOT NULL, request_key TEXT NOT NULL, UNIQUE(user,request_key));
CREATE UNIQUE INDEX IF NOT EXISTS yc_active_user ON yc_jobs(user) WHERE state='active';
CREATE TABLE IF NOT EXISTS yc_defects(tid TEXT NOT NULL REFERENCES yc_templates(tid), number INTEGER NOT NULL, hash TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(tid,number), UNIQUE(tid,hash));
CREATE TABLE IF NOT EXISTS yc_sheets(id INTEGER PRIMARY KEY, job TEXT NOT NULL REFERENCES yc_jobs(id), ordinal INTEGER NOT NULL, production_number INTEGER NOT NULL, maturity INTEGER NOT NULL, cost INTEGER NOT NULL, ends REAL NOT NULL, defects TEXT NOT NULL, settled INTEGER NOT NULL DEFAULT 0, result TEXT, UNIQUE(job,ordinal));
CREATE TABLE IF NOT EXISTS yc_instances(id TEXT PRIMARY KEY, tid TEXT NOT NULL REFERENCES yc_templates(tid), defect_number INTEGER NOT NULL, producer TEXT NOT NULL, production_number INTEGER NOT NULL, cell INTEGER NOT NULL, cid TEXT NOT NULL, geometry TEXT NOT NULL, owner TEXT NOT NULL, acquired REAL NOT NULL, created REAL NOT NULL, UNIQUE(producer,production_number,cell));
CREATE INDEX IF NOT EXISTS yc_instances_owner ON yc_instances(owner,acquired);
CREATE TABLE IF NOT EXISTS yc_listings(id TEXT PRIMARY KEY, instance TEXT NOT NULL REFERENCES yc_instances(id), seller TEXT NOT NULL, price_cents INTEGER NOT NULL CHECK(price_cents>0), state TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS yc_listing_active ON yc_listings(instance) WHERE state='active';
CREATE TABLE IF NOT EXISTS yc_listing_requests(user TEXT NOT NULL, request_key TEXT NOT NULL, listing TEXT NOT NULL REFERENCES yc_listings(id), instance TEXT NOT NULL, price_cents INTEGER NOT NULL, PRIMARY KEY(user,request_key));
CREATE TABLE IF NOT EXISTS yc_trades(id TEXT PRIMARY KEY, listing TEXT NOT NULL UNIQUE REFERENCES yc_listings(id), instance TEXT NOT NULL REFERENCES yc_instances(id), buyer TEXT NOT NULL, seller TEXT NOT NULL, price_cents INTEGER NOT NULL, fee_cents INTEGER NOT NULL, created REAL NOT NULL);
'''


from ycards_auctions import AuctionMixin, AUCTION_SCHEMA


from ycards_collection import CollectionMixin, SCHEMA as COLLECTION_SCHEMA


from ycards_library import LibraryMixin, SCHEMA as LIBRARY_SCHEMA


class Store(LibraryMixin, CollectionMixin, AuctionMixin):
    def __init__(self, connect=points.connect, cards=CARDS):
        self._connect, self.cards = connect, Path(cards)
        self.cards.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            points.init(conn)
            conn.executescript(SCHEMA + AUCTION_SCHEMA + promotions.SCHEMA + COLLECTION_SCHEMA + LIBRARY_SCHEMA)
            if 'exported_revision' not in {r['name'] for r in conn.execute('PRAGMA table_info(yc_drafts)')}:
                conn.execute('ALTER TABLE yc_drafts ADD COLUMN exported_revision INTEGER NOT NULL DEFAULT 0')
            self.library_migrate(conn)

    @contextmanager
    def connect(self):
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self):
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            yield conn

    def publish(self, tid=None, draft_id=None):
        """Recover outbox exports. Never replace immutable files, even after crash."""
        with self.connect() as conn:
            rows = [(r['tid']+'.card', 'yc_templates', r['tid'], None) for r in conn.execute('SELECT tid FROM yc_templates'+(' WHERE tid=?' if tid else ' WHERE 0' if draft_id else ''), (tid,) if tid else ())]
            if not tid and not draft_id:
                rows += [(f"{r['tid']}_{r['number']}.card", 'yc_defects', r['tid'], r['number']) for r in conn.execute('SELECT tid,number FROM yc_defects')]
            for name, table, export_tid, number in rows:
                path = self.cards/name
                if path.exists():
                    continue
                payload = (conn.execute('SELECT design FROM yc_templates WHERE tid=?', (export_tid,)).fetchone()[0]
                           if table == 'yc_templates' else conn.execute('SELECT payload FROM yc_defects WHERE tid=? AND number=?', (export_tid, number)).fetchone()[0])
                tmp = self.cards/('.'+uuid.uuid4().hex+'.tmp')
                try:
                    with tmp.open('x', encoding='utf-8') as f:
                        f.write(payload)
                        f.flush()
                        os.fsync(f.fileno())
                    try:
                        os.link(tmp, path)
                    except FileExistsError:
                        pass
                finally:
                    tmp.unlink(missing_ok=True)
        # Draft exports are serialized with draft updates/deletions.
        with self.transaction() as conn:
            for r in conn.execute('SELECT id,revision,exported_revision FROM yc_drafts'+(' WHERE id=?' if draft_id else ' WHERE 0' if tid else ''), (draft_id,) if draft_id else ()):
                path = self.cards/f"draft_{r['id']}.card"
                if path.exists() and r['exported_revision'] == r['revision']:
                    continue
                payload = conn.execute('SELECT design FROM yc_drafts WHERE id=?', (r['id'],)).fetchone()[0]
                tmp = self.cards/('.'+uuid.uuid4().hex+'.tmp')
                try:
                    with tmp.open('x', encoding='utf-8') as f:
                        f.write(payload)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(tmp, path)
                    conn.execute('UPDATE yc_drafts SET exported_revision=? WHERE id=?', (r['revision'], r['id']))
                finally:
                    tmp.unlink(missing_ok=True)
        fd = os.open(self.cards, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    @staticmethod
    def owned_template(conn, user, tid):
        identifier(tid, 'TID')
        r = conn.execute('SELECT * FROM yc_templates WHERE tid=? AND owner=?', (tid, user)).fetchone()
        if not r:
            raise CardError('模板不存在或不属于当前用户', 404)
        return r

    @staticmethod
    def balance_change(conn, user, cents, reason, ref):
        import yidt_core as y
        amount = abs(int(cents))*y.CENT
        if cents < 0:
            result=y.service_debit(conn,user,'ycards',amount,reason,str(ref))
            if reason=='YCards 拍卖冻结':
                b=y.binding(conn,user,'ycards')
                old=conn.execute('SELECT units FROM yidt_escrow WHERE service=? AND reference=? AND user=?',('ycards',str(ref),user)).fetchone()
                y.escrow_set(conn,'ycards',str(ref),user,b['address'],(int(old[0]) if old else 0)+y.units(result['credit']))
            return result
        if '解冻' in reason:
            old=conn.execute('SELECT * FROM yidt_escrow WHERE service=? AND reference=? AND user=?',('ycards',str(ref),user)).fetchone()
            if old:
                amount=min(amount,int(old['units']))
                y.escrow_set(conn,'ycards',str(ref),user,old['address'],int(old['units'])-amount)
        return y.service_credit(conn,user,'ycards',amount,reason,str(ref))

    def save(self, user, data, formal, compact=False):
        key=data.get('request_key');digest=None
        if key is not None:
            identifier(key,'保存请求编号',r'[a-zA-Z0-9_-]{16,100}')
            digest=hashlib.sha256(canonical([formal,{k:v for k,v in data.items() if k!='request_key'}]).encode()).hexdigest()
            with self.connect() as db:
                old=db.execute('SELECT hash,result FROM yc_save_requests WHERE user=? AND request_key=?',(user,key)).fetchone()
                if old:
                    if old['hash']!=digest:raise CardError('同一保存请求不能用于不同设计',409)
                    return json.loads(old['result'])
        design = validate_design(data.get('design'))
        design['creator'] = user
        did = data.get('draft_id')
        if did is not None:
            identifier(did, '草稿 ID', r'[a-f0-9]{32}')
            integer(data.get('revision'), '草稿版本', 1)
        with self.transaction() as conn:
            if key:
                old=conn.execute('SELECT hash,result FROM yc_save_requests WHERE user=? AND request_key=?',(user,key)).fetchone()
                if old:
                    if old['hash']!=digest:raise CardError('同一保存请求不能用于不同设计',409)
                    return json.loads(old['result'])
            if did:
                draft = conn.execute('SELECT * FROM yc_drafts WHERE id=? AND owner=?', (did, user)).fetchone()
                if not draft:
                    raise CardError('草稿不存在', 404)
                if data.get('revision') != draft['revision']:
                    raise CardError('草稿已在其他窗口更新，请重新打开', 409)
            if formal:
                tid = data.get('tid')
                if not isinstance(tid, str) or not re.fullmatch(r'[A-Za-z0-9]{1,12}', tid):
                    raise CardError('TID 仅允许 1～12 位大小写英文字母或数字')
                if data.get('confirmed') is not True:
                    raise CardError('请确认保存后不可再次编辑')
                design['tid'] = tid
                try:
                    conn.execute('INSERT INTO yc_templates(tid,owner,name,design,cost,created,material) VALUES(?,?,?,?,?,?,?)',
                                 (tid, user, design['name'], canonical(design), str(design['cost']), time.time(), design['material']))
                except sqlite3.IntegrityError as exc:
                    raise CardError('TID 已存在（区分大小写）', 409) from exc
                if did:
                    conn.execute('DELETE FROM yc_drafts WHERE id=?', (did,))
                    (self.cards/f'draft_{did}.card').unlink(missing_ok=True)
                result = dict(tid=tid, design=design)
            else:
                did = did or uuid.uuid4().hex
                revision = draft['revision']+1 if data.get('draft_id') else 1
                design['draft_id'] = did
                conn.execute('INSERT INTO yc_drafts(id,owner,design,updated,revision,name,material,cost) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET design=excluded.design,updated=excluded.updated,revision=excluded.revision,name=excluded.name,material=excluded.material,cost=excluded.cost',
                             (did, user, canonical(design), time.time(), revision, design['name'], design['material'], str(design['cost'])))
                result = dict(draft_id=did, revision=revision, design=design)
            summary={k:v for k,v in result.items() if k!='design'}
            if key:conn.execute('INSERT INTO yc_save_requests VALUES(?,?,?,?,?)',(user,key,digest,canonical(summary),time.time()))
        self.publish(tid=design['tid'] if formal else None,draft_id=None if formal else did)
        return summary if compact else result

    def templates(self, user, summary=False, production=False):
        with self.connect() as conn:
            result=[]
            columns='t.tid,t.name,t.cost,t.material,t.deleted,t.created'+('' if summary else ',t.design')
            query='SELECT '+columns+',COALESCE(s.maturity,0) maturity,COALESCE(s.quantity,0) quantity FROM yc_templates t LEFT JOIN yc_stats s ON s.tid=t.tid AND s.user=? WHERE t.owner=?'+(' AND t.deleted=0' if production else '')+' ORDER BY t.created DESC'
            for r in conn.execute(query,(user,user)):
                item=dict(tid=r['tid'],name=r['name'],material=r['material'],maturity=r['maturity'],quantity=r['quantity'],cost=r['cost'],archived=bool(r['deleted']),delete_reason='模板仅支持归档和恢复')
                if not summary:
                    item['design']=json.loads(r['design']);item['design']['name']=r['name']
                result.append(item)
            if not production:
                columns='id,revision,name,material,cost,archived'+('' if summary else ',design')
                for r in conn.execute('SELECT '+columns+' FROM yc_drafts WHERE owner=? ORDER BY updated DESC',(user,)):
                    item=dict(draft_id=r['id'],revision=r['revision'],name=r['name'],material=r['material'],maturity=0,quantity=0,cost=r['cost'],archived=bool(r['archived']))
                    if not summary:item['design']=json.loads(r['design'])
                    result.append(item)
            return result

    def design(self, user, tid=None, draft_id=None):
        with self.connect() as conn:
            if draft_id:
                identifier(draft_id, '草稿 ID', r'[a-f0-9]{32}')
                row = conn.execute('SELECT design,revision FROM yc_drafts WHERE id=? AND owner=?', (draft_id, user)).fetchone()
                if not row:
                    raise CardError('草稿不存在', 404)
                return dict(design=json.loads(row['design']), revision=row['revision'], draft_id=draft_id)
            row = self.owned_template(conn, user, tid)
            design=json.loads(row['design']);design['name']=row['name']
            return dict(design=design, tid=tid)

    @staticmethod
    def delete_reason(conn, tid):
        reasons = []
        for table, label in [('yc_instances', '已有卡片实例'), ('yc_jobs', '已有生产任务'), ('yc_defects', '已有损耗模板')]:
            if conn.execute(f'SELECT 1 FROM {table} WHERE tid=? LIMIT 1', (tid,)).fetchone():
                reasons.append(label)
        return '、'.join(reasons)

    def delete(self, user, data):
        raise CardError('模板和草稿不支持完全删除，请使用归档；归档后可以恢复',409)

    def quote(self, user, data, conn=None):
        if conn is None:
            with self.connect() as db:
                return self.quote(user, data, db)
        t = self.owned_template(conn, user, data.get('tid'))
        if t['deleted']:raise CardError('模板已归档，请先恢复后再生产',409)
        row = conn.execute('SELECT maturity FROM yc_stats WHERE user=? AND tid=?', (user, t['tid'])).fetchone()
        maturity = row['maturity'] if row else 0
        q = production_quote(int(t['cost']), maturity, data.get('mode'), data.get('count', 1), data.get('improvement', 0))
        promotions.apply(self, conn, user, data, q)
        q['maturity'] = maturity
        q['token'] = hashlib.sha256(canonical([t['tid'], data.get('mode'), q]).encode()).hexdigest()
        return q

    def start(self, user, data):
        key = data.get('request_key')
        if not isinstance(key, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{16,100}', key):
            raise CardError('请求编号无效')
        self.settle()
        with self.transaction() as conn:
            old = conn.execute('SELECT id FROM yc_jobs WHERE user=? AND request_key=?', (user, key)).fetchone()
            if old:
                return {'id': old['id']}
            if conn.execute("SELECT 1 FROM yc_jobs WHERE user=? AND state='active'", (user,)).fetchone():
                raise CardError('请等待当前批次完成', 409)
            q = self.quote(user, data, conn)
            if data.get('quote_token') != q['token']:
                raise CardError('报价已变化，请重新获取报价', 409)
            template = self.owned_template(conn, user, data['tid'])
            design = json.loads(template['design'])
            jid, now = uuid.uuid4().hex, time.time()
            self.balance_change(conn, user, -q['total']*100, 'YCards 生产', jid)
            conn.execute('INSERT INTO yc_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                         (jid, user, data['tid'], data['mode'], len(q['sheets']), q['improvement'], q['total'], now, now+q['seconds'], 'active', key))
            promotions.record(conn, jid, user, q, now)
            conn.execute('INSERT OR IGNORE INTO yc_sequences VALUES(?,0)', (user,))
            num = conn.execute('SELECT number FROM yc_sequences WHERE user=?', (user,)).fetchone()['number']
            end = now
            for i, sheet in enumerate(q['sheets']):
                end += sheet['seconds']
                defect = sample_defects(design, data['mode'], q['improvement'])
                conn.execute('INSERT INTO yc_sheets(job,ordinal,production_number,maturity,cost,ends,defects) VALUES(?,?,?,?,?,?,?)',
                             (jid, i+1, num+i+1, sheet['maturity'], sheet['cost'], end, canonical(defect)))
            conn.execute('UPDATE yc_sequences SET number=? WHERE user=?', (num+len(q['sheets']), user))
        return {'id': jid}

    def defect_number(self, conn, tid, defects):
        if empty_defects(defects):
            return 0
        # Includes *all* parameters; excludes producer, timestamps and job identity.
        text = canonical(defects)
        digest = hashlib.sha256(text.encode()).hexdigest()
        row = conn.execute('SELECT number FROM yc_defects WHERE tid=? AND hash=?', (tid, digest)).fetchone()
        if row:
            return row['number']
        n = conn.execute('SELECT COALESCE(MAX(number),0)+1 n FROM yc_defects WHERE tid=?', (tid,)).fetchone()['n']
        payload = canonical(dict(format_version=VERSION, tid=tid, template=tid+'.card', defect_number=n, defects=defects))
        conn.execute('INSERT INTO yc_defects VALUES(?,?,?,?)', (tid, n, digest, payload))
        return n

    def settle(self, now=None):
        now = time.time() if now is None else now
        with self.transaction() as conn:
            rows = conn.execute('SELECT s.*,j.user,j.tid,j.mode,t.design,t.name FROM yc_sheets s JOIN yc_jobs j ON j.id=s.job JOIN yc_templates t ON t.tid=j.tid WHERE s.settled=0 AND s.ends<=? ORDER BY s.ends,s.ordinal', (now,)).fetchall()
            for r in rows:
                defects, design = json.loads(r['defects']), json.loads(r['design'])
                n = self.defect_number(conn, r['tid'], defects)
                produced = []
                if not defects['destroyed']:
                    for geo in product_geometry(design, defects, r['mode']):
                        cell = min(geo['cells'])
                        iid = uuid.uuid4().hex
                        cid = f"{r['tid']}_{n}_{cell}_{r['production_number']}"
                        conn.execute('INSERT INTO yc_instances VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                                     (iid, r['tid'], n, r['user'], r['production_number'], cell, cid, canonical(geo), r['user'], r['ends'], r['ends']))
                        conn.execute('INSERT INTO yc_card_names VALUES(?,?)',(iid,r['name']))
                        produced.append(dict(id=iid, cid=cid, cells=geo['cells']))
                gain = 3 if r['mode'] == 'factory' else 1
                qty = 16 if r['mode'] == 'factory' else 1
                conn.execute('INSERT INTO yc_stats VALUES(?,?,?,?) ON CONFLICT(user,tid) DO UPDATE SET maturity=MIN(64,maturity+excluded.maturity),quantity=quantity+excluded.quantity', (r['user'], r['tid'], gain, qty))
                result = dict(destroyed=defects['destroyed'], defect_number=n, products=produced, theoretical=qty)
                conn.execute('UPDATE yc_sheets SET settled=1,result=? WHERE id=?', (canonical(result), r['id']))
            conn.execute("UPDATE yc_jobs SET state='done' WHERE state='active' AND NOT EXISTS(SELECT 1 FROM yc_sheets s WHERE s.job=yc_jobs.id AND s.settled=0)")
        self.settle_auctions(now)
        if rows:
            self.publish()

    def jobs(self, user):
        self.settle()
        with self.connect() as conn:
            result = []
            for row in conn.execute('SELECT * FROM yc_jobs WHERE user=? ORDER BY started DESC LIMIT 50', (user,)):
                j = dict(row)
                j.pop('request_key')
                discount = conn.execute('SELECT code,original_total,discount,paid FROM yc_coupon_uses WHERE job=?', (j['id'],)).fetchone()
                j['coupon'] = dict(discount) if discount else None
                j['sheets'] = []
                for sheet in conn.execute('SELECT ordinal,production_number,maturity,cost,ends,settled,result FROM yc_sheets WHERE job=? ORDER BY ordinal', (j['id'],)):
                    s = dict(sheet)
                    s['result'] = json.loads(s['result']) if s['result'] else None
                    if s['result']:
                        s['result']['product_count'] = len(s['result'].pop('products'))
                    j['sheets'].append(s)
                result.append(j)
            return result

    def preview(self, iid, user=None, public=False, auction_id=None, compact=False, connection=None):
        with (nullcontext(connection) if connection is not None else self.connect()) as conn:
            design_column = '' if compact else 't.design,'
            r = conn.execute('SELECT i.*,'+design_column+'COALESCE(n.name,t.name) name,l.id listing_id,l.price_cents,l.seller FROM yc_instances i JOIN yc_templates t ON t.tid=i.tid LEFT JOIN yc_card_names n ON n.instance=i.id LEFT JOIN yc_listings l ON l.instance=i.id AND l.state=\'active\' WHERE i.id=?', (iid,)).fetchone()
            auction = conn.execute("SELECT id FROM yc_auctions WHERE instance=? AND state='active'", (iid,)).fetchone()
            historical = auction_id and conn.execute('SELECT 1 FROM yc_auctions WHERE id=? AND instance=?', (auction_id, iid)).fetchone()
            showcased = conn.execute('SELECT 1 FROM yc_showcase s JOIN yc_instances i ON i.id=s.instance WHERE s.instance=? AND s.user=i.owner', (iid,)).fetchone()
            is_public = conn.execute('SELECT 1 FROM yc_public_cards p JOIN yc_instances i ON i.id=p.instance AND i.owner=p.owner WHERE p.instance=?', (iid,)).fetchone()
            destroyed = conn.execute('SELECT 1 FROM yc_destroyed WHERE instance=?', (iid,)).fetchone()
            if not r or (destroyed and not historical) or (r['owner'] != user and not r['listing_id'] and not auction and not historical and not showcased and not is_public):
                raise CardError('卡片不存在或不可查看', 404)
            d = conn.execute('SELECT payload FROM yc_defects WHERE tid=? AND number=?', (r['tid'], r['defect_number'])).fetchone()
            design = None if compact else json.loads(r['design'])
            if design is not None:
                design.pop('cost', None)
            result = dict(id=r['id'], tid=r['tid'], cid=r['cid'], name=r['name'],
                        geometry=json.loads(r['geometry']), defects=json.loads(d['payload'])['defects'] if d else {'layers': [], 'cuts': [], 'destroyed': False},
                        auction_id=auction['id'] if auction else None, listing_id=r['listing_id'], price_cents=r['price_cents'], owned=r['owner'] == user,
                        owner=r['owner'] if r['owner']==user or r['listing_id'] or auction or is_public or showcased else None, seller=r['seller'], public=bool(is_public), acquired=r['acquired'], render_auction_id=auction_id, showcased=bool(showcased), destroyed=bool(destroyed))
            if design is not None:
                result['design'] = design
            return result

    def render_template(self, tid, iid, user, auction_id=None):
        identifier(tid, 'TID')
        identifier(iid, '实例 ID', r'[a-f0-9]{32}')
        card = self.preview(iid, user, auction_id=auction_id, compact=True)
        if card['tid'] != tid:
            raise CardError('模板与实例不匹配', 404)
        with self.connect() as conn:
            design = json.loads(conn.execute('SELECT design FROM yc_templates WHERE tid=?', (tid,)).fetchone()[0])
        design.pop('cost', None)
        return dict(tid=tid, design=design)

    def cards_list(self, user, market, args):
        with self.connect() as conn:
            query = '''SELECT i.id,i.tid,i.cid,i.defect_number,i.acquired,i.geometry,i.owner,COALESCE(n.name,t.name) name,l.id listing_id,l.price_cents,a.id auction_id,
                       EXISTS(SELECT 1 FROM yc_public_cards p WHERE p.instance=i.id AND p.owner=i.owner) public,
                       EXISTS(SELECT 1 FROM yc_showcase s WHERE s.instance=i.id AND s.user=i.owner) showcased,
                       COALESCE(d.payload,'{}') defect_payload FROM yc_instances i JOIN yc_templates t ON t.tid=i.tid LEFT JOIN yc_card_names n ON n.instance=i.id
                       LEFT JOIN yc_listings l ON l.instance=i.id AND l.state='active'
                       LEFT JOIN yc_defects d ON d.tid=i.tid AND d.number=i.defect_number
                       LEFT JOIN yc_auctions a ON a.instance=i.id AND a.state='active' WHERE '''
            params = []
            query += "l.state='active'" if market else 'i.owner=?'
            query += ' AND NOT EXISTS(SELECT 1 FROM yc_destroyed x WHERE x.instance=i.id)'
            if not market:
                params.append(user)
            if args.get('tid'):
                query += ' AND i.tid=?'
                params.append(args['tid'])
            if args.get('name'):
                query += ' AND instr(COALESCE(n.name,t.name),?)>0'
                params.append(args['name'][:100])
            for key, op in [('min', '>='), ('max', '<=')]:
                if market and args.get(key):
                    query += f' AND l.price_cents{op}?'
                    params.append(price_cents(args[key], allow_zero=True))
            order = {'template': 'i.tid,i.acquired DESC', 'oldest': 'i.acquired', 'newest': 'i.acquired DESC'}
            query += ' ORDER BY ' + ('l.updated DESC' if market else order.get(args.get('sort'), 'i.acquired DESC'))
            wanted = set(filter(None, str(args.get('defects', '')).split(',')))
            result = []
            for row in conn.execute(query, params):
                item = dict(row)
                payload = json.loads(item.pop('defect_payload'))
                kinds = defect_kinds(payload.get('defects', {}))
                if wanted and not wanted.issubset(set(kinds or ['none'])):
                    continue
                item['defect_kinds'] = kinds
                item['owned'] = item['owner'] == user
                if market:item['seller'] = item['owner']
                if args.get('compact') == '1':
                    item['geometry'] = json.loads(item['geometry'])
                    item['defects'] = payload.get('defects', {'layers': [], 'cuts': [], 'destroyed': False})
                else:
                    item.pop('geometry')
                result.append(item)
            page = max(1, min(100000, int(args.get('page', '1')) if str(args.get('page', '1')).isdigit() else 1))
            page = min(page, max(1, (len(result)+23)//24))
            return dict(items=result[(page-1)*24:page*24], total=len(result), page=page)

    def list_card(self, user, data):
        price = price_cents(data.get('price'))
        iid = identifier(data.get('instance_id'), '实例 ID', r'[a-f0-9]{32}')
        request_key = data.get('request_key')
        if request_key is not None:
            identifier(request_key, '请求编号', r'[a-zA-Z0-9_-]{16,100}')
        with self.transaction() as conn:
            if request_key:
                old = conn.execute('SELECT * FROM yc_listing_requests WHERE user=? AND request_key=?', (user, request_key)).fetchone()
                if old:
                    if old['instance'] != iid or old['price_cents'] != price:
                        raise CardError('同一请求编号不能用于不同挂售', 409)
                    return {'id': old['listing']}
            self.available_card(conn, user, iid)
            if conn.execute("SELECT 1 FROM yc_auctions WHERE instance=? AND state='active'", (iid,)).fetchone():
                raise CardError('该卡片正在拍卖，不能挂售', 409)
            lid, now = uuid.uuid4().hex, time.time()
            try:
                conn.execute('INSERT INTO yc_listings VALUES(?,?,?,?,?,?,?)', (lid, iid, user, price, 'active', now, now))
            except sqlite3.IntegrityError as exc:
                raise CardError('该卡片已挂售', 409) from exc
            self.balance_change(conn, user, -1000, 'YCards 挂售发布手续费', lid)
            if request_key:
                conn.execute('INSERT INTO yc_listing_requests VALUES(?,?,?,?,?)', (user,request_key,lid,iid,price))
        return {'id': lid}

    def change_listing(self, user, data):
        identifier(data.get('listing_id'), '挂售 ID', r'[a-f0-9]{32}')
        with self.transaction() as conn:
            row = conn.execute("SELECT l.* FROM yc_listings l JOIN yc_instances i ON i.id=l.instance WHERE l.id=? AND l.seller=? AND i.owner=? AND l.state='active'", (data.get('listing_id'), user, user)).fetchone()
            if not row:
                raise CardError('挂售已失效或不属于当前用户', 409)
            if data.get('action') == 'withdraw':
                conn.execute("UPDATE yc_listings SET state='withdrawn',updated=? WHERE id=?", (time.time(), row['id']))
            elif data.get('action') == 'reprice':
                conn.execute('UPDATE yc_listings SET price_cents=?,updated=? WHERE id=?', (price_cents(data.get('price')), time.time(), row['id']))
            else:
                raise CardError('操作无效')

    def buy(self, user, data):
        lid = identifier(data.get('listing_id'), '挂售 ID', r'[a-f0-9]{32}')
        integer(data.get('expected_price_cents'), '报价', 1, MAX_POINTS*100)
        with self.transaction() as conn:
            old = conn.execute('SELECT id,buyer FROM yc_trades WHERE listing=?', (lid,)).fetchone()
            if old and old['buyer'] == user:
                return {'id': old['id']}
            r = conn.execute('SELECT l.*,i.owner FROM yc_listings l JOIN yc_instances i ON i.id=l.instance WHERE l.id=?', (lid,)).fetchone()
            if not r or r['state'] != 'active' or r['owner'] != r['seller']:
                raise CardError('卡片已成交或下架', 409)
            if user == r['seller']:
                raise CardError('不能购买自己的卡片')
            if data.get('expected_price_cents') != r['price_cents']:
                raise CardError('售价已变化，请重新查看', 409)
            # P is measured in points: ceil(P * .005) whole points.
            fee = ((r['price_cents'] + 19999)//20000)*100
            trade, now = uuid.uuid4().hex, time.time()
            import yidt_core as y
            route=conn.execute('SELECT address FROM yidt_routes WHERE service=? AND reference=? AND user=?',('ycards',lid,r['seller'])).fetchone()
            target=route[0] if route else y.bound_address(conn,r['seller'],'ycards')
            conn.execute('INSERT OR IGNORE INTO yidt_routes VALUES(?,?,?,?)',('ycards',trade,r['seller'],target))
            self.balance_change(conn, user, -(r['price_cents']+fee), 'YCards 买入及交易手续费', trade)
            self.balance_change(conn, r['seller'], r['price_cents'], 'YCards 卖出', trade)
            conn.execute('UPDATE yc_instances SET owner=?,acquired=? WHERE id=?', (user, now, r['instance']))
            conn.execute("UPDATE yc_listings SET state='sold',updated=? WHERE id=?", (now, lid))
            conn.execute('INSERT INTO yc_trades VALUES(?,?,?,?,?,?,?,?)',
                         (trade, lid, r['instance'], user, r['seller'], r['price_cents'], fee, now))
        return {'id': trade}

    def history(self, user):
        with self.connect() as conn:
            listings = [dict(r) for r in conn.execute('SELECT l.*,i.cid,COALESCE(n.name,t.name) name FROM yc_listings l JOIN yc_instances i ON i.id=l.instance JOIN yc_templates t ON t.tid=i.tid LEFT JOIN yc_card_names n ON n.instance=i.id WHERE l.seller=? ORDER BY l.updated DESC', (user,))]
            trades = [dict(r) for r in conn.execute('SELECT x.*,i.cid,COALESCE(n.name,t.name) name FROM yc_trades x JOIN yc_instances i ON i.id=x.instance JOIN yc_templates t ON t.tid=i.tid LEFT JOIN yc_card_names n ON n.instance=i.id WHERE x.buyer=? OR x.seller=? ORDER BY x.created DESC', (user, user))]
            import yidt_core as y
            for r in listings+trades:
                receipts=list(conn.execute('SELECT hash,source FROM yidt_transactions WHERE reference=? ORDER BY id',(r['id'],)))
                r['yidt_transactions']=[t['hash'] for t in receipts]
                r['yidt_receipts']=[dict(hash=t['hash'],label='收款详情' if t['source']==y.OFFICIAL else '付款详情') for t in receipts]
            return dict(active=[r for r in listings if r['state'] == 'active'], withdrawn=[r for r in listings if r['state'] == 'withdrawn'],
                        bought=[r for r in trades if r['buyer'] == user], sold=[r for r in trades if r['seller'] == user])


def price_cents(value, allow_zero=False):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)) or not re.fullmatch(r'\d{1,12}(?:\.\d{1,2})?', str(value)):
        raise CardError('价格须为最多两位小数的正数')
    amount = int(Decimal(str(value))*100)
    if amount < (0 if allow_zero else 1) or amount > MAX_POINTS*100:
        raise CardError('价格超出范围')
    return amount


def defect_kinds(d):
    result = set()
    for layer in d.get('layers', []):
        if layer.get('missing'):
            result.add('missing_print')
        if 'offset' in layer:
            result.add('skew_print')
    for cut in d.get('cuts', []):
        if cut.get('missing'):
            result.add('missing_cut')
        if 'offset' in cut:
            result.add('skew_cut')
    return sorted(result)


def init_app(app, current_user, avatar_url, store=None, worker=True, profile=None):
    store = store or Store()
    app.extensions['ycards'] = store
    bp = Blueprint('ycards', __name__, url_prefix='/ycards')

    def identity():
        u = current_user()
        if u and (u.get('deleted_at') or u.get('purged_at')):
            return None
        return u

    def user_info(username):
        if not username:return None
        row=profile(username) if profile else {}
        return dict(username=username,nickname=(row or {}).get('nickname') or username,
                    avatar_url=avatar_url(username),url='/@'+url_quote(username,safe=''))

    def enrich_card(card):
        if card.get('seller'):card['seller_profile']=user_info(card['seller'])
        return card

    def enrich_auction(auction):
        auction['seller_profile']=user_info(auction.get('seller'))
        if auction.get('winner'):auction['buyer_profile']=user_info(auction['winner'])
        if auction.get('card'):enrich_card(auction['card'])
        return auction

    def is_admin(username):
        u = identity()
        return bool(u and u['username'] == username and u.get('role') == 'admin')
    store.is_admin = is_admin

    def denied(next_path='/ycards/'):
        u = identity()
        target = '/account/?' + urlencode({'err': GUEST_MESSAGE}) if u else '/login/?' + urlencode({'next': next_path})
        if '/api/' in request.path:
            return jsonify(error=GUEST_MESSAGE if u else '请先登录', redirect=target), 403 if u else 401
        return redirect(target)

    @bp.before_request
    def guard():
        public = request.method == 'GET' and (request.path in ('/ycards/', '/ycards/api/market', '/ycards/api/auctions') or request.path.startswith(('/ycards/api/preview/', '/ycards/api/auctions/', '/ycards/api/render-template/', '/ycards/card/', '/ycards/api/public/')))
        u = identity()
        if not public and (not u or u.get('role', 'guest') == 'guest'):
            return denied(request.full_path.rstrip('?'))
        if request.method == 'POST':
            request.max_content_length = MAX_BODY
            if not session.get('yc_csrf') or not secrets.compare_digest(request.headers.get('X-YCards-CSRF', '').encode(), session['yc_csrf'].encode()):
                raise CardError('页面已过期，请刷新后重试', 403)
            if request.content_length and request.content_length > MAX_BODY:
                raise CardError('设计资源总大小超过 32 MB', 413)
            if not request.is_json:
                raise CardError('需要 JSON 请求')

    @bp.errorhandler(CardError)
    def error(exc):
        return jsonify(error=exc.message), exc.status

    def body():
        if request.headers.get('Content-Encoding')=='gzip':
            try:
                decoder=zlib.decompressobj(31)
                raw=decoder.decompress(request.get_data(),MAX_BODY+1)
                if len(raw)>MAX_BODY or decoder.unconsumed_tail:raise CardError('设计资源总大小超过 32 MB',413)
                if not decoder.eof or decoder.unused_data:raise CardError('压缩请求格式无效')
                data=json.loads(raw)
            except (zlib.error,ValueError,UnicodeDecodeError) as exc:
                raise CardError('压缩请求格式无效') from exc
        else:
            data = request.get_json()
        if not isinstance(data, dict):
            raise CardError('请求格式无效')
        return data

    @bp.route('/')
    def page():
        u = identity()
        tab = request.args.get('tab', 'market')
        if tab not in ('market','auction') and (not u or u.get('role', 'guest') == 'guest'):
            return denied(request.full_path.rstrip('?'))
        session.setdefault('yc_csrf', secrets.token_urlsafe(32))
        balance = points.account_view(u['username'])['balance_cents'] if u else None
        return render_template('ycards.html', user=u, avatar_url=avatar_url(u['username']) if u else None,
                               boot=dict(user=u['username'] if u else None, allowed=bool(u and u.get('role', 'guest') != 'guest'),
                                         csrf=session['yc_csrf'], balance_cents=balance, is_admin=bool(u and u.get('role') == 'admin'), guest_message=GUEST_MESSAGE))

    @bp.route('/api/market')
    def market():
        result=store.cards_list(None, True, request.args)
        for card in result['items']:enrich_card(card)
        return jsonify(result)

    @bp.route('/api/preview/<iid>')
    def preview(iid):
        u = identity()
        return jsonify(enrich_card(store.preview(iid, u['username'] if u and u.get('role', 'guest') != 'guest' else None, compact=request.args.get('compact') == '1')))

    @bp.route('/api/render-template/<tid>')
    def render_template_data(tid):
        u = identity()
        response = jsonify(store.render_template(tid, request.args.get('instance_id'),
                            u['username'] if u and u.get('role', 'guest') != 'guest' else None,
                            request.args.get('auction_id')))
        response.headers['Cache-Control'] = 'private, no-store'
        return response

    @bp.route('/api/state')
    def state():
        u = identity()['username']
        jobs = store.jobs(u)
        return jsonify(balance_cents=points.account_view(u)['balance_cents'], auction_held_cents=store.auction_reserved(u), jobs=jobs, now=time.time())

    @bp.route('/api/templates', methods=['GET', 'POST'])
    def templates():
        u = identity()['username']
        if request.method == 'GET':
            store.settle()
            return jsonify(items=store.templates(u, summary=request.args.get('summary') == '1',production=request.args.get('production')=='1'))
        started=time.perf_counter();result=store.save(u, body(), True,compact=request.args.get('compact')=='1')
        response=jsonify(result);response.headers['Server-Timing']=f'ycards-save;dur={(time.perf_counter()-started)*1000:.1f}'
        return response

    @bp.route('/api/design')
    def design_data():
        return jsonify(store.design(identity()['username'], request.args.get('tid'), request.args.get('draft_id')))

    @bp.route('/api/drafts', methods=['POST'])
    def drafts():
        return jsonify(store.save(identity()['username'], body(), False,compact=request.args.get('compact')=='1'))

    @bp.route('/api/rename', methods=['POST'])
    def rename():
        return jsonify(store.rename(identity()['username'],body()))

    @bp.route('/api/archive', methods=['POST'])
    def archive():
        return jsonify(store.archive(identity()['username'],body()))

    @bp.route('/api/save-status/<key>')
    def save_status(key):
        return jsonify(store.save_status(identity()['username'],key))

    @bp.route('/api/delete', methods=['POST'])
    def delete():
        store.delete(identity()['username'], body())
        return jsonify(ok=True)

    @bp.route('/api/quote', methods=['POST'])
    def quote():
        store.settle()
        return jsonify(store.quote(identity()['username'], body()))

    @bp.route('/api/produce', methods=['POST'])
    def produce():
        return jsonify(store.start(identity()['username'], body()))

    @bp.route('/api/mine')
    def mine():
        store.settle()
        return jsonify(store.cards_list(identity()['username'], False, request.args))

    @bp.route('/api/destroy', methods=['POST'])
    def destroy_cards():
        return jsonify(store.destroy_cards(identity()['username'], body()))

    @bp.route('/api/list-batch', methods=['POST'])
    def batch_list():
        return jsonify(store.batch_list(identity()['username'], body()))

    @bp.route('/api/public', methods=['POST'])
    def publish_cards():
        return jsonify(store.set_public(identity()['username'], body()))

    @bp.route('/api/share', methods=['POST'])
    def share_card():
        return jsonify(store.share_card(identity()['username'], body()))

    @bp.route('/card/<iid>/<cid>/')
    def public_card_page(iid,cid):
        try:
            card=store.public_card(iid,cid)
        except CardError as exc:
            response=app.make_response((render_template('ycards_public.html',card=None,error=exc.message),exc.status))
        else:
            response=app.make_response(render_template('ycards_public.html',card=card))
        response.headers['Cache-Control']='private, no-store'
        response.headers['X-Robots-Tag']='noindex'
        return response

    @bp.route('/api/public/<iid>/<cid>')
    def public_card_data(iid,cid):
        response=jsonify(store.public_card(iid,cid))
        response.headers['Cache-Control']='private, no-store'
        return response

    @bp.route('/api/showcase', methods=['GET', 'POST'])
    def showcase():
        u = identity()['username']
        if request.method == 'POST':
            return jsonify(store.set_showcase(u, body()))
        result = store.showcase(u)
        result['balance_cents'] = points.account_view(u)['balance_cents']
        return jsonify(result)

    @bp.route('/api/showcase/purchase', methods=['POST'])
    def buy_showcase_slot():
        return jsonify(store.buy_showcase_slot(identity()['username'], body()))

    @bp.route('/api/list', methods=['POST'])
    def list_card():
        return jsonify(store.list_card(identity()['username'], body()))

    @bp.route('/api/listing', methods=['POST'])
    def change_listing():
        store.change_listing(identity()['username'], body())
        return jsonify(ok=True)

    @bp.route('/api/buy', methods=['POST'])
    def buy():
        return jsonify(store.buy(identity()['username'], body()))

    @bp.route('/api/auctions', methods=['GET', 'POST'])
    def auctions():
        u = identity()
        if request.method == 'POST':
            return jsonify(store.create_auction(u['username'], body()))
        result=store.auctions_list(u['username'] if u else None, request.args)
        for auction in result['items']:enrich_auction(auction)
        return jsonify(result)

    @bp.route('/api/auctions/<aid>')
    def auction_detail(aid):
        u = identity()
        return jsonify(enrich_auction(store.auction_detail(aid, u['username'] if u else None, compact=request.args.get('compact') == '1', include_card=request.args.get('card') != '0')))

    @bp.route('/api/bid', methods=['POST'])
    def bid():
        return jsonify(store.bid(identity()['username'], body()))

    @bp.route('/api/auction-history')
    def auction_history():
        result=store.auctions_list(identity()['username'], request.args, history=True)
        for auction in result['items']:enrich_auction(auction)
        return jsonify(result)

    @bp.route('/api/history')
    def history():
        result=store.history(identity()['username'])
        for card in result['sold']:card['buyer_profile']=user_info(card['buyer'])
        for card in result['bought']:card['seller_profile']=user_info(card['seller'])
        return jsonify(result)

    app.register_blueprint(bp)
    if worker:
        def run():
            exported = 0
            while True:
                try:
                    store.settle()
                    if time.time()-exported > 60:
                        store.publish()
                        exported = time.time()
                except Exception:
                    app.logger.exception('YCards worker will retry durable jobs/outbox')
                time.sleep(1)
        threading.Thread(target=run, name='ycards-settlement', daemon=True).start()
    return store
