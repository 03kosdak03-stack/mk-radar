"""Standalone MK VAL: Telegram polling, bounded ZIP import, persistent snapshots."""
import io
import json
import math
import os
import re
import sqlite3
import threading
import time
import zipfile
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.request import Request, urlopen
from urllib.parse import urlsplit
import replies

BASE = Path(__file__).resolve().parent
DATA = Path(os.environ.get('DATA_DIR', BASE / 'storage'))
NAMES = ('final_snapshot.json', 'final_tracking.json', 'final_backtest.json')
LIMIT = 15 * 1024 * 1024
TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
ADMIN = int(os.environ.get('ADMIN_ID', '0'))
PUBLIC = os.environ.get('PUBLIC_URL', os.environ.get('RENDER_EXTERNAL_URL', '')).rstrip('/')

def db():
    c = sqlite3.connect(DATA / 'bot.sqlite3', timeout=30)
    c.execute('PRAGMA journal_mode=WAL')
    return c

def init():
    DATA.mkdir(parents=True, exist_ok=True)
    with db() as c:
        c.execute('CREATE TABLE IF NOT EXISTS versions(id INTEGER PRIMARY KEY, asof TEXT, payload TEXT)')
        c.execute('CREATE TABLE IF NOT EXISTS state(k TEXT PRIMARY KEY, v TEXT)')
        if not c.execute('SELECT 1 FROM versions').fetchone():
            payload = {n: json.loads((BASE / 'seed' / n).read_text()) for n in NAMES}
            validate(payload)
            c.execute('INSERT INTO versions(asof,payload) VALUES (?,?)', (payload[NAMES[0]]['asof'], json.dumps(payload)))

def current():
    with db() as c:
        return json.loads(c.execute('SELECT payload FROM versions ORDER BY id DESC LIMIT 1').fetchone()[0])

def finite(x):
    return type(x) in (int, float) and math.isfinite(x)

def validate(p):
    d = p.get(NAMES[0])
    if not isinstance(d, dict):
        raise ValueError('final_snapshot.json bulunamadı.')
    stamp = date.fromisoformat(d['asof'])
    if stamp > date.today():
        raise ValueError('Tarama tarihi gelecekte olamaz.')
    for group in ('picks', 'asset', 'operating'):
        rows = d[group]
        if not isinstance(rows, list) or len(rows) > 2000:
            raise ValueError('Hisse listesi biçimi geçersiz.')
        seen = set()
        for r in rows:
            if not isinstance(r, dict) or not re.fullmatch(r'[A-Z0-9]{2,12}', r.get('ticker', '')):
                raise ValueError('Hisse kodu geçersiz.')
            if r['ticker'] in seen:
                raise ValueError('Tekrarlanan hisse kodu.')
            seen.add(r['ticker'])
            for k in ('price', 'value_ps', 'potential'):
                if not finite(r.get(k)):
                    raise ValueError('Fiyat/değer alanı eksik veya geçersiz.')
            if r['price'] <= 0:
                raise ValueError('Fiyat pozitif olmalı.')
            for k in ('company', 'method', 'price_date', 'period_end', 'published_at', 'group'):
                if not isinstance(r.get(k), str):
                    raise ValueError('Hisse ayrıntıları eksik.')
            if r['group'] not in ('VARLIK', 'FAALİYET'):
                raise ValueError('Hisse grubu geçersiz.')
            if any(not isinstance(u, str) or not u.startswith('https://') for u in r.get('source_urls', [])):
                raise ValueError('Kaynak bağlantısı geçersiz.')
    if not d['asset'] and not d['operating']:
        raise ValueError('Boş tarama yüklenemez.')
    if len(d['picks']) > 5:
        raise ValueError('Seçim listesi en fazla beş hisse olabilir.')
    lookup = {r['ticker']: r for r in d['asset'] + d['operating']}
    if any(lookup.get(r['ticker']) != r for r in d['picks']):
        raise ValueError('Seçimler ana listeyle uyuşmuyor.')
    # Exercise every renderer before publishing, including optional report files.
    original = replies._load
    try:
        replies._load = p.get
        for cmd in ('/final', '/varlik', '/faaliyet', '/backtest', '/takip'):
            replies.reply(cmd)
        for r in lookup.values():
            replies.reply('/hisse', r['ticker'])
    finally:
        replies._load = original

def import_zip(blob):
    if len(blob) > LIMIT:
        raise ValueError('ZIP en fazla 15 MB olabilir.')
    p = {}
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        infos = z.infolist()
        if len(infos) > 10000 or sum(i.file_size for i in infos) > 100 * 1024 * 1024:
            raise ValueError('ZIP açılmış boyutu çok büyük.')
        for i in infos:
            path = PurePosixPath(i.filename.replace('\\', '/'))
            if path.is_absolute() or '..' in path.parts:
                raise ValueError('ZIP dosya yolu geçersiz.')
            if path.name not in NAMES:
                continue  # Never extract or execute code from uploaded ZIPs.
            if i.file_size > LIMIT:
                raise ValueError('Veri dosyası çok büyük.')
            raw = z.read(i)
            value = json.loads(raw, parse_constant=lambda v: (_ for _ in ()).throw(ValueError('Geçersiz sayı')))
            if path.name in p and p[path.name] != value:
                raise ValueError('ZIP içinde çelişen veri kopyaları var.')
            p[path.name] = value
    validate(p)
    stamp = p[NAMES[0]]['asof']
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        last = c.execute('SELECT asof FROM versions ORDER BY id DESC LIMIT 1').fetchone()
        if last and stamp < last[0]:
            raise ValueError('Bu ZIP mevcut taramadan eski; yüklenmedi.')
        c.execute('INSERT INTO versions(asof,payload) VALUES (?,?)', (stamp, json.dumps(p)))
        c.execute('DELETE FROM versions WHERE id NOT IN (SELECT id FROM versions ORDER BY id DESC LIMIT 12)')
    return stamp

def api(method, **params):
    req = Request('https://api.telegram.org/bot' + TOKEN + '/' + method,
                  data=json.dumps(params).encode(), headers={'Content-Type': 'application/json'})
    with urlopen(req, timeout=50) as r:
        data = json.load(r)
    if not data.get('ok'):
        raise RuntimeError('Telegram işlemi başarısız.')
    return data['result']

def send(chat, text, **extra):
    for i in range(0, len(text), 3500):
        api('sendMessage', chat_id=chat, text=text[i:i+3500], **extra)

def allowed(m):
    return m.get('chat', {}).get('type') == 'private' and m.get('from', {}).get('id') == ADMIN and ADMIN > 0

def handle(m):
    chat = m.get('chat', {}).get('id')
    if not allowed(m):
        if chat and m.get('chat', {}).get('type') == 'private':
            send(chat, 'Bu bot yalnızca sahibine açıktır. Telegram kimliğin: ' + str(m.get('from', {}).get('id')))
        return
    if 'document' in m:
        doc = m['document']
        if not doc.get('file_name', '').lower().endswith('.zip') or doc.get('file_size', LIMIT+1) > LIMIT:
            send(chat, '15 MB altında bir haftalık veri ZIP dosyası gönder.'); return
        info = api('getFile', file_id=doc['file_id'])
        with urlopen('https://api.telegram.org/file/bot' + TOKEN + '/' + info['file_path'], timeout=40) as r:
            blob = r.read(LIMIT+1)
        try:
            stamp = import_zip(blob)
        except (ValueError, KeyError, TypeError, zipfile.BadZipFile, RuntimeError):
            send(chat, 'ZIP kabul edilmedi: eksik/geçersiz veri, eski tarih veya çelişen kopya. Mevcut veri korundu.'); return
        send(chat, 'Güncelleme tamamlandı. Tarama tarihi: ' + stamp + '\nEkranı kapatıp yeniden açabilirsin.'); return
    parts = m.get('text', '/start').split(maxsplit=1)
    cmd = parts[0].split('@')[0].lower() if parts else '/start'
    arg = parts[1] if len(parts)>1 else ''
    if cmd in ('/start', '/mkfinal', '/radar'):
        extra = {}
        if PUBLIC.startswith('https://'):
            extra['reply_markup'] = {'inline_keyboard': [[{'text': '📊 MK VAL Uygulamasını Aç', 'web_app': {'url': PUBLIC}}]]}
        send(chat, 'MK VAL\n/secim • /deger KOD • /varlik • /faaliyet\n/takip • /durum • /guncelle • /kural\nHaftalık güncelleme: ZIP dosyasını bu sohbete gönder.', **extra)
    elif cmd == '/guncelle':
        send(chat, 'Yeni final_snapshot.json içeren ZIP dosyasını gönder. İsteğe bağlı final_tracking.json ve final_backtest.json eklenebilir. Bu işlem veri yükler; yeni değerleme hesaplamaz.')
    elif cmd == '/durum':
        d = current()[NAMES[0]]
        age = (date.today()-date.fromisoformat(d['asof'])).days
        send(chat, f"Veri tarihi: {d['asof']} ({age} gün önce)\nSürüm: {d.get('version','—')}\nDeğerlenen: {len(d['asset'])+len(d['operating'])}\nSeçim: {len(d['picks'])}\nCanlı fiyat akışı yok. Haftalık ZIP verisi gösteriliyor.")
    else:
        mapping = {'/secim':'/final', '/deger':'/hisse', '/mkbacktest':'/backtest'}
        p = current()
        replies._load = p.get
        send(chat, replies.reply(mapping.get(cmd, cmd), arg))

class Web(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/health':
            body, mime = b'OK', 'text/plain'
        elif path in ('/', '/index.html', '/hisseler.html'):
            body, mime = (BASE/'web/index.html').read_bytes(), 'text/html; charset=utf-8'
        elif path in ('/api/final', '/final_snapshot.json'):
            body, mime = json.dumps(current()[NAMES[0]]).encode(), 'application/json'
        else:
            self.send_error(404); return
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

def run():
    if not TOKEN or ADMIN <= 0:
        raise SystemExit('TELEGRAM_BOT_TOKEN ve sayısal ADMIN_ID gerekli.')
    init()
    if PUBLIC and (urlsplit(PUBLIC).scheme != 'https' or not urlsplit(PUBLIC).netloc):
        raise SystemExit('PUBLIC_URL HTTPS adresi olmalı.')
    api('getMe')
    api('deleteWebhook', drop_pending_updates=False)
    if PUBLIC:
        api('setChatMenuButton', chat_id=ADMIN, menu_button={'type':'web_app','text':'MK VAL','web_app':{'url':PUBLIC}})
    api('setMyCommands', commands=[{'command':a,'description':b} for a,b in [('start','Uygulamayı aç'),('secim','Haftanın hisseleri'),('deger','Hisse ayrıntısı'),('durum','Veri tarihi'),('guncelle','Haftalık ZIP yükleme')]])
    server = ThreadingHTTPServer(('0.0.0.0', int(os.environ.get('PORT','8080'))), Web)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with db() as c:
        row=c.execute("SELECT v FROM state WHERE k='offset'").fetchone()
    offset=int(row[0]) if row else 0
    print('MK VAL başlatıldı.', flush=True)
    while True:
        try:
            for u in api('getUpdates', offset=offset, timeout=30, allowed_updates=['message']):
                try:
                    handle(u.get('message', {}))
                except Exception as e:
                    print('Mesaj hatası:', type(e).__name__, flush=True)
                    if allowed(u.get('message', {})):
                        try: send(u['message']['chat']['id'], 'İşlem tamamlanamadı; yeniden dene. /durum')
                        except Exception: pass
                offset=u['update_id']+1
                with db() as c:
                    c.execute("INSERT OR REPLACE INTO state VALUES ('offset',?)", (str(offset),))
        except Exception as e:
            # Never log exception messages containing a Telegram token URL.
            print('Bağlantı hatası:', type(e).__name__, flush=True)
            time.sleep(5)

if __name__ == '__main__':
    run()
