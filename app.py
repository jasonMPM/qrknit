"""
to.ALWISP - URL Shortener & QR Code Generator
Single-admin auth: set ADMIN_PASSWORD env var. No accounts, no JWTs.
"""

import os
import re
import sqlite3
import hashlib
import hmac
import time
import io
import struct
import zlib
from datetime import datetime, timedelta, timezone
from functools import wraps
from flask import Flask, request, jsonify, redirect, Response, session

app = Flask(__name__, static_folder='static', template_folder='templates')
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY')
if not app.config['SECRET_KEY']:
    raise RuntimeError("SECRET_KEY environment variable must be set")

ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', '')
if not ADMIN_PASSWORD:
    raise RuntimeError("ADMIN_PASSWORD environment variable must be set")

DB_PATH  = os.environ.get('DB_PATH',  '/app/data/sniplink.db')
BASE_URL = os.environ.get('BASE_URL', 'http://localhost:5000').rstrip('/')

COOKIE_SECURE = os.environ.get('COOKIE_SECURE', 'false').lower() == 'true'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE']   = COOKIE_SECURE
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)


# ─────────────────────────────────────────────
# Database
# ─────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS links (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                code       TEXT UNIQUE NOT NULL,
                long_url   TEXT NOT NULL,
                title      TEXT,
                created_at TEXT NOT NULL,
                expires_at TEXT,
                clicks     INTEGER DEFAULT 0,
                is_active  INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS clicks (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                link_id    INTEGER NOT NULL,
                clicked_at TEXT NOT NULL,
                referrer   TEXT,
                user_agent TEXT,
                FOREIGN KEY (link_id) REFERENCES links(id)
            );
            CREATE TABLE IF NOT EXISTS tags (
                id   INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL
            );
            CREATE TABLE IF NOT EXISTS link_tags (
                link_id INTEGER NOT NULL,
                tag_id  INTEGER NOT NULL,
                PRIMARY KEY (link_id, tag_id),
                FOREIGN KEY (link_id) REFERENCES links(id),
                FOREIGN KEY (tag_id)  REFERENCES tags(id)
            );
            CREATE INDEX IF NOT EXISTS idx_links_code  ON links(code);
            CREATE INDEX IF NOT EXISTS idx_clicks_link ON clicks(link_id);
            CREATE INDEX IF NOT EXISTS idx_clicks_at   ON clicks(clicked_at);
        """)

init_db()


# ─────────────────────────────────────────────
# Auth decorator
# ─────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('authenticated'):
            return jsonify({'error': 'Authentication required'}), 401
        return f(*args, **kwargs)
    return decorated


# ─────────────────────────────────────────────
# QR Generator
# ─────────────────────────────────────────────

def generate_qr_png(data: str, size: int = 300, fg=(0,0,0), bg=(255,255,255)) -> bytes:
    try:
        import qrcode as qrc
        qr = qrc.QRCode(border=2)
        qr.add_data(data)
        qr.make(fit=True)
        img = qr.make_image(fill_color=fg, back_color=bg)
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        return buf.getvalue()
    except ImportError:
        pass
    module_count = 21
    cell = max(4, size // module_count)
    img_size = cell * module_count
    pixels = []
    for row in range(img_size):
        row_pixels = b''
        for col in range(img_size):
            r, c = row // cell, col // cell
            in_finder = (
                (r < 7 and c < 7) or (r < 7 and c >= module_count - 7) or
                (r >= module_count - 7 and c < 7)
            )
            is_dark = False
            if in_finder:
                lr, lc = r % 7, c % 7
                is_dark = (lr == 0 or lr == 6 or lc == 0 or lc == 6 or
                           (2 <= lr <= 4 and 2 <= lc <= 4))
            elif (row // cell + col // cell) % 2 == 0:
                is_dark = (r == 6 or c == 6) and (r % 2 == 0 or c % 2 == 0)
            row_pixels += bytes(fg if is_dark else bg)
        pixels.append(row_pixels)

    def png_chunk(t, d):
        c = t + d
        return struct.pack('>I', len(d)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)

    raw = b''.join(b'\x00' + row for row in pixels)
    return (b'\x89PNG\r\n\x1a\n'
            + png_chunk(b'IHDR', struct.pack('>IIBBBBB', img_size, img_size, 8, 2, 0, 0, 0))
            + png_chunk(b'IDAT', zlib.compress(raw))
            + png_chunk(b'IEND', b''))


# ─────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────

def generate_code(url: str, length: int = 6) -> str:
    return hashlib.sha256(f"{url}{time.time()}".encode()).hexdigest()[:length]

def validate_url(url: str) -> bool:
    return url.startswith(('http://', 'https://'))

def hex_to_rgb(h):
    h = h.lstrip('#')
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def parse_device(ua):
    if not ua: return 'Unknown'
    u = ua.lower()
    if any(x in u for x in ('mobile','android','iphone')): return 'Mobile'
    if any(x in u for x in ('tablet','ipad')):             return 'Tablet'
    return 'Desktop'

def parse_browser(ua):
    if not ua: return 'Unknown'
    u = ua.lower()
    if 'edg/' in u:     return 'Edge'
    if 'opr/' in u:     return 'Opera'
    if 'chrome/' in u:  return 'Chrome'
    if 'firefox/' in u: return 'Firefox'
    if 'safari/' in u:  return 'Safari'
    if 'curl' in u:     return 'curl'
    if 'python' in u:   return 'Python'
    return 'Other'

def parse_referrer(ref):
    if not ref: return 'Direct'
    r = ref.lower()
    if 'google' in r:                                      return 'Google'
    if 'bing' in r:                                        return 'Bing'
    if 'facebook' in r or 'fb.com' in r:                  return 'Facebook'
    if 'twitter' in r or 't.co' in r or 'x.com' in r:    return 'Twitter/X'
    if 'linkedin' in r:                                    return 'LinkedIn'
    if 'reddit' in r:                                      return 'Reddit'
    if 'youtube' in r:                                     return 'YouTube'
    if 'instagram' in r:                                   return 'Instagram'
    return 'Other'

def get_link_tags(conn, link_id):
    rows = conn.execute(
        'SELECT t.id, t.name FROM tags t JOIN link_tags lt ON t.id=lt.tag_id WHERE lt.link_id=?',
        (link_id,)
    ).fetchall()
    return [{'id': r['id'], 'name': r['name']} for r in rows]

def set_link_tags(conn, link_id, tag_names):
    conn.execute('DELETE FROM link_tags WHERE link_id=?', (link_id,))
    for name in tag_names:
        name = name.strip().lower()
        if not name: continue
        conn.execute('INSERT OR IGNORE INTO tags (name) VALUES (?)', (name,))
        tid = conn.execute('SELECT id FROM tags WHERE name=?', (name,)).fetchone()['id']
        conn.execute('INSERT OR IGNORE INTO link_tags (link_id,tag_id) VALUES (?,?)', (link_id, tid))

def format_link(row, conn):
    return {
        'id':        row['id'],
        'code':      row['code'],
        'long_url':  row['long_url'],
        'title':     row['title'],
        'created_at': row['created_at'],
        'expires_at': row['expires_at'],
        'clicks':    row['clicks'],
        'is_active': row['is_active'],
        'short_url': f"{BASE_URL}/{row['code']}",
        'qr_url':    f"{BASE_URL}/api/qr/{row['code']}",
        'tags':      get_link_tags(conn, row['id']),
    }


# ─────────────────────────────────────────────
# Auth Routes
# ─────────────────────────────────────────────
# Health check (public — used by Docker healthcheck)
# ─────────────────────────────────────────────

@app.route('/api/health')
def health():
    return jsonify({'status': 'ok'})



def login():
    data     = request.get_json(silent=True) or {}
    password = data.get('password') or ''
    if not hmac.compare_digest(password, ADMIN_PASSWORD):
        return jsonify({'error': 'Invalid password'}), 401
    session.permanent = True
    session['authenticated'] = True
    return jsonify({'authenticated': True})


@app.route('/api/auth/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True})


@app.route('/api/auth/me', methods=['GET'])
def me():
    if session.get('authenticated'):
        return jsonify({'authenticated': True})
    return jsonify({'authenticated': False}), 401


# ─────────────────────────────────────────────
# Links
# ─────────────────────────────────────────────

@app.route('/api/shorten', methods=['POST'])
@login_required
def shorten():
    data        = request.get_json(silent=True) or {}
    long_url    = (data.get('url') or '').strip()
    custom_code = (data.get('custom_code') or '').strip()
    title       = (data.get('title') or '').strip()
    expires_at  = data.get('expires_at')
    tags        = data.get('tags', [])

    if not long_url:
        return jsonify({'error': 'URL is required'}), 400
    if not validate_url(long_url):
        return jsonify({'error': 'URL must start with http:// or https://'}), 400
    if custom_code and not re.match(r'^[a-zA-Z0-9]{3,20}$', custom_code):
        return jsonify({'error': 'Custom code must be 3–20 alphanumeric characters'}), 400

    code = custom_code or generate_code(long_url)

    with get_db() as conn:
        existing = conn.execute('SELECT code FROM links WHERE code=?', (code,)).fetchone()
        if existing:
            if custom_code:
                return jsonify({'error': 'Custom code already taken'}), 409
            code = generate_code(long_url + str(time.time()))

        conn.execute(
            'INSERT INTO links (code,long_url,title,created_at,expires_at) VALUES (?,?,?,?,?)',
            (code, long_url, title or None,
             datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
             expires_at or None)
        )
        link_id = conn.execute('SELECT id FROM links WHERE code=?', (code,)).fetchone()['id']
        if tags:
            set_link_tags(conn, link_id, tags)

    return jsonify({
        'code':      code,
        'short_url': f"{BASE_URL}/{code}",
        'long_url':  long_url,
        'title':     title,
        'tags':      tags,
        'qr_url':    f"{BASE_URL}/api/qr/{code}",
    }), 201


@app.route('/api/links', methods=['GET'])
@login_required
def list_links():
    page       = int(request.args.get('page', 1))
    per_page   = min(int(request.args.get('per_page', 20)), 100)
    offset     = (page - 1) * per_page
    search     = (request.args.get('q') or '').strip()
    tag_filter = (request.args.get('tag') or '').strip().lower()

    where_clauses = ['l.is_active=1']
    params = []

    if search:
        where_clauses.append('(l.code LIKE ? OR l.long_url LIKE ? OR l.title LIKE ?)')
        s = f'%{search}%'
        params += [s, s, s]
    if tag_filter:
        where_clauses.append(
            'l.id IN (SELECT lt.link_id FROM link_tags lt '
            'JOIN tags t ON lt.tag_id=t.id WHERE t.name=?)'
        )
        params.append(tag_filter)

    where_sql = ' AND '.join(where_clauses)
    with get_db() as conn:
        total = conn.execute(f'SELECT COUNT(*) FROM links l WHERE {where_sql}', params).fetchone()[0]
        rows  = conn.execute(
            f'SELECT * FROM links l WHERE {where_sql} ORDER BY l.created_at DESC LIMIT ? OFFSET ?',
            params + [per_page, offset]
        ).fetchall()
        links = [format_link(r, conn) for r in rows]

    return jsonify({'links': links, 'total': total, 'page': page, 'per_page': per_page})


@app.route('/api/links/<code>', methods=['GET'])
@login_required
def link_detail(code):
    with get_db() as conn:
        link = conn.execute('SELECT * FROM links WHERE code=?', (code,)).fetchone()
        if not link:
            return jsonify({'error': 'Not found'}), 404
        return jsonify(format_link(link, conn))


@app.route('/api/links/<code>', methods=['PATCH'])
@login_required
def edit_link(code):
    with get_db() as conn:
        link = conn.execute('SELECT * FROM links WHERE code=? AND is_active=1', (code,)).fetchone()
        if not link:
            return jsonify({'error': 'Not found'}), 404

        data = request.get_json(silent=True) or {}
        updates = {}
        if 'url' in data:
            url = data['url'].strip()
            if not validate_url(url): return jsonify({'error': 'Invalid URL'}), 400
            updates['long_url'] = url
        if 'title'      in data: updates['title']      = data['title'].strip() or None
        if 'expires_at' in data: updates['expires_at'] = data['expires_at'] or None

        if updates:
            set_clause = ', '.join(f'{k}=?' for k in updates)
            conn.execute(f'UPDATE links SET {set_clause} WHERE code=?',
                         list(updates.values()) + [code])
        if 'tags' in data:
            set_link_tags(conn, link['id'], data['tags'])

        updated = conn.execute('SELECT * FROM links WHERE code=?', (code,)).fetchone()
        return jsonify(format_link(updated, conn))


@app.route('/api/links/<code>', methods=['DELETE'])
@login_required
def delete_link(code):
    with get_db() as conn:
        link = conn.execute('SELECT * FROM links WHERE code=?', (code,)).fetchone()
        if not link:
            return jsonify({'error': 'Not found'}), 404
        conn.execute('UPDATE links SET is_active=0 WHERE code=?', (code,))
    return jsonify({'success': True})


# ─────────────────────────────────────────────
# Analytics
# ─────────────────────────────────────────────

@app.route('/api/links/<code>/analytics')
@login_required
def link_analytics(code):
    days = int(request.args.get('days', 30))
    with get_db() as conn:
        link = conn.execute('SELECT * FROM links WHERE code=?', (code,)).fetchone()
        if not link:
            return jsonify({'error': 'Not found'}), 404

        link_id = link['id']
        since   = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)).isoformat()

        daily_rows = conn.execute("""
            SELECT substr(clicked_at,1,10) as day, COUNT(*) as count
            FROM clicks WHERE link_id=? AND clicked_at>=?
            GROUP BY day ORDER BY day
        """, (link_id, since)).fetchall()
        daily_map = {r['day']: r['count'] for r in daily_rows}
        daily = [
            {'date': (datetime.now(timezone.utc).replace(tzinfo=None)-timedelta(days=days-1-i)).strftime('%Y-%m-%d'), 'clicks': 0}
            for i in range(days)
        ]
        for d in daily: d['clicks'] = daily_map.get(d['date'], 0)

        ref_rows = conn.execute(
            'SELECT referrer, COUNT(*) as count FROM clicks WHERE link_id=? AND clicked_at>=? GROUP BY referrer',
            (link_id, since)
        ).fetchall()
        referrers = {}
        for r in ref_rows:
            b = parse_referrer(r['referrer']); referrers[b] = referrers.get(b,0) + r['count']
        referrers = [{'source':k,'count':v} for k,v in sorted(referrers.items(), key=lambda x:-x[1])]

        ua_rows = conn.execute(
            'SELECT user_agent, COUNT(*) as count FROM clicks WHERE link_id=? AND clicked_at>=? GROUP BY user_agent',
            (link_id, since)
        ).fetchall()
        devices = {}; browsers = {}
        for r in ua_rows:
            ua = r['user_agent'] or ''
            devices[parse_device(ua)]   = devices.get(parse_device(ua),0)   + r['count']
            browsers[parse_browser(ua)] = browsers.get(parse_browser(ua),0) + r['count']
        devices  = [{'device':k,'count':v}  for k,v in sorted(devices.items(),  key=lambda x:-x[1])]
        browsers = [{'browser':k,'count':v} for k,v in sorted(browsers.items(), key=lambda x:-x[1])]

    return jsonify({
        'code': code, 'days': days,
        'total_clicks':  link['clicks'],
        'period_clicks': sum(d['clicks'] for d in daily),
        'daily': daily, 'referrers': referrers, 'devices': devices, 'browsers': browsers,
    })


# ─────────────────────────────────────────────
# Tags & Stats
# ─────────────────────────────────────────────

@app.route('/api/tags')
@login_required
def list_tags():
    with get_db() as conn:
        rows = conn.execute("""
            SELECT t.id, t.name, COUNT(lt.link_id) as link_count
            FROM tags t LEFT JOIN link_tags lt ON t.id=lt.tag_id
            GROUP BY t.id ORDER BY t.name
        """).fetchall()
    return jsonify({'tags': [dict(r) for r in rows]})


@app.route('/api/stats')
@login_required
def stats():
    with get_db() as conn:
        total_links  = conn.execute('SELECT COUNT(*) FROM links WHERE is_active=1').fetchone()[0]
        total_clicks = conn.execute('SELECT COALESCE(SUM(clicks),0) FROM links WHERE is_active=1').fetchone()[0]
        since_7d     = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)).isoformat()
        clicks_7d    = conn.execute(
            'SELECT COUNT(*) FROM clicks c JOIN links l ON c.link_id=l.id '
            'WHERE c.clicked_at>=? AND l.is_active=1', (since_7d,)
        ).fetchone()[0]
        top_links = conn.execute(
            'SELECT code, long_url, title, clicks FROM links WHERE is_active=1 '
            'ORDER BY clicks DESC LIMIT 5'
        ).fetchall()

    return jsonify({
        'total_links':  total_links,
        'total_clicks': total_clicks,
        'clicks_7d':    clicks_7d,
        'top_links':    [dict(r) for r in top_links],
    })


# ─────────────────────────────────────────────
# QR Routes
# ─────────────────────────────────────────────

@app.route('/api/qr/<code>')
def qr_code(code):
    fg_hex = request.args.get('fg', '000000')
    bg_hex = request.args.get('bg', 'ffffff')
    size   = min(int(request.args.get('size', 300)), 1000)
    with get_db() as conn:
        link = conn.execute('SELECT 1 FROM links WHERE code=? AND is_active=1', (code,)).fetchone()
    if not link:
        return jsonify({'error': 'Not found'}), 404
    png = generate_qr_png(f"{BASE_URL}/{code}", size=size,
                          fg=hex_to_rgb(fg_hex), bg=hex_to_rgb(bg_hex))
    return Response(png, mimetype='image/png', headers={'Cache-Control': 'public, max-age=3600'})


@app.route('/api/qr/custom')
def qr_custom():
    url = request.args.get('url', '').strip()
    if not url or not validate_url(url):
        return jsonify({'error': 'Valid URL required'}), 400
    fg_hex = request.args.get('fg', '000000')
    bg_hex = request.args.get('bg', 'ffffff')
    size   = min(int(request.args.get('size', 300)), 1000)
    png = generate_qr_png(url, size=size, fg=hex_to_rgb(fg_hex), bg=hex_to_rgb(bg_hex))
    return Response(png, mimetype='image/png')


# ─────────────────────────────────────────────
# Redirect
# ─────────────────────────────────────────────

@app.route('/<code>')
def redirect_link(code):
    if code in ('static', 'api', 'favicon.ico'):
        return 'Not found', 404
    with get_db() as conn:
        link = conn.execute('SELECT * FROM links WHERE code=? AND is_active=1', (code,)).fetchone()
        if not link:
            return redirect('/?error=not_found')
        if link['expires_at'] and link['expires_at'] < datetime.now(timezone.utc).replace(tzinfo=None).isoformat():
            return redirect('/?error=expired')
        conn.execute(
            'INSERT INTO clicks (link_id,clicked_at,referrer,user_agent) VALUES (?,?,?,?)',
            (link['id'], datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
             request.referrer, request.headers.get('User-Agent','')[:500])
        )
        conn.execute('UPDATE links SET clicks=clicks+1 WHERE id=?', (link['id'],))
        return redirect(link['long_url'], code=301)


# ─────────────────────────────────────────────
# Frontend — catch-all
# ─────────────────────────────────────────────

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def frontend(path):
    if path.startswith('api/') or path.startswith('static/'):
        return 'Not found', 404
    with open(os.path.join(os.path.dirname(__file__), 'index.html')) as f:
        return f.read()


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port,
            debug=os.environ.get('DEBUG', 'false').lower() == 'true')
