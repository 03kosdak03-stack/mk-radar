"""Read-only Mini App, same SQLite database as Telegram. No token in browser."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl,urlparse
import hashlib,hmac,json,time
from datetime import datetime
from zoneinfo import ZoneInfo
from .config import settings,ROOT
from .db import Store


def validate_init_data(raw,token,now=None,allowed_ids=None):
    if not raw or not token or len(raw)>32768:raise ValueError('Telegram oturumu gerekli.')
    pairs=parse_qsl(raw,keep_blank_values=True,strict_parsing=True)
    if len(dict(pairs))!=len(pairs):raise ValueError('Tekrarlanan oturum alanı.')
    fields=dict(pairs);signature=fields.pop('hash',None)
    secret=hmac.new(b'WebAppData',token.encode(),hashlib.sha256).digest()
    expected=hmac.new(secret,'\n'.join(k+'='+fields[k] for k in sorted(fields)).encode(),hashlib.sha256).hexdigest()
    if not signature or not hmac.compare_digest(signature,expected):raise ValueError('Telegram oturumu doğrulanamadı.')
    stamp=int(fields['auth_date']);clock=int(time.time() if now is None else now)
    if stamp>clock+30 or clock-stamp>86400:raise ValueError('Telegram oturumu eski; uygulamayı yeniden aç.')
    user=json.loads(fields['user'])
    if not isinstance(user.get('id'),int) or isinstance(user['id'],bool):raise ValueError('Kullanıcı kimliği geçersiz.')
    if allowed_ids and user['id'] not in allowed_ids:raise ValueError('Bu kullanıcıya erişim verilmedi.')
    return user


def snapshot_payload(store,universe_path=None):
    run=store.latest_run()
    path=Path(universe_path or Path(settings.kap_data_dir)/'universe.json')
    try:universe=json.loads(path.read_text(encoding='utf-8')).get('companies',{})
    except (OSError,ValueError):universe={}
    if not run:
        return dict(as_of=None,stale=True,total=len(universe),rows=[dict(ticker=t,company=v.get('company',''),status='HENUZ_TARANMADI',bands={}) for t,v in sorted(universe.items())],top15=[],portfolio5=[],investment_final=False)
    stamp=datetime.fromisoformat(run['as_of'])
    if stamp.tzinfo is None:stamp=stamp.replace(tzinfo=ZoneInfo('Europe/Istanbul'))
    stale=(datetime.now(ZoneInfo('Europe/Istanbul'))-stamp).total_seconds()>86400
    rows={r['ticker']:r for r in run['rows']}
    for ticker,member in universe.items():
        rows.setdefault(ticker,dict(ticker=ticker,company=member.get('company',''),status='HENUZ_TARANMADI',bands={}))
    return dict(as_of=run['as_of'],stale=stale,total=len(rows),rows=list(rows.values()),
        top15=[r['ticker'] for r in run['top15']],portfolio5=[r['ticker'] for r in run['portfolio5']],
        investment_final=False,ranking_version=run.get('ranking_version'),counts=run['counts'])


def make_server(host='127.0.0.1',port=8080,local=False,store=None):
    if local and host not in ('127.0.0.1','localhost','::1'):raise ValueError('Yerel önizleme yalnız yerel ağ arayüzünde açılabilir.')
    db=store or Store()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def send(self,status,payload,mime='application/json; charset=utf-8'):
            content=payload if isinstance(payload,bytes) else json.dumps(payload,ensure_ascii=False,allow_nan=False).encode()
            self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(content)))
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
            self.end_headers();self.wfile.write(content)
        def do_GET(self):
            path=urlparse(self.path).path
            if path in ('/','/app.js','/style.css'):
                name={'/':'index.html','/app.js':'app.js','/style.css':'style.css'}[path]
                mime={'/':'text/html; charset=utf-8','/app.js':'text/javascript; charset=utf-8','/style.css':'text/css; charset=utf-8'}[path]
                return self.send(200,(ROOT/'web'/name).read_bytes(),mime)
            if path.startswith('/api/'):
                try:
                    if not local:validate_init_data(self.headers.get('X-Telegram-Init-Data',''),settings.telegram_bot_token,allowed_ids=settings.admin_ids)
                except (ValueError,KeyError,TypeError):return self.send(401,{'error':'Telegram üzerinden aç veya oturumu yenile.'})
                if path=='/api/snapshot':return self.send(200,snapshot_payload(db))
                if path.startswith('/api/history/'):
                    import re
                    ticker=path.removeprefix('/api/history/')
                    if not re.fullmatch('[A-Z0-9]{2,12}',ticker):return self.send(400,{'error':'Geçersiz kod.'})
                    return self.send(200,db.target_history(ticker,limit=10))
            self.send(404,{'error':'Bulunamadı.'})
    return ThreadingHTTPServer((host,port),Handler)


def main():
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--host',default='127.0.0.1');p.add_argument('--port',type=int,default=8080);p.add_argument('--local',action='store_true');a=p.parse_args()
    server=make_server(a.host,a.port,a.local)
    print('MK Mini App:',f'http://{a.host}:{a.port}', '| Yerel önizleme' if a.local else '| Telegram oturum doğrulaması açık')
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()

if __name__=='__main__':main()
