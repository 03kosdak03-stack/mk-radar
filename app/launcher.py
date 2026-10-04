"""Run the bot and authenticated panel together; stop both on exit."""
import argparse,subprocess,sys,threading
from .config import ROOT
from .miniapp import make_server

def main():
    p=argparse.ArgumentParser();p.add_argument('--host',default='127.0.0.1');p.add_argument('--port',type=int,default=8080);a=p.parse_args()
    server=make_server(a.host,a.port)
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    bot=None
    try:
        bot=subprocess.Popen([sys.executable,'-m','app.bot'],cwd=ROOT)
        print('Bot ve Telegram oturum doğrulamalı kart paneli çalışıyor. Durdurmak için Ctrl+C.',flush=True)
        bot.wait()
    except KeyboardInterrupt:pass
    finally:
        if bot and bot.poll() is None:
            bot.terminate()
            try:bot.wait(timeout=10)
            except subprocess.TimeoutExpired:bot.kill();bot.wait()
        server.shutdown();server.server_close();worker.join(timeout=2)

if __name__=='__main__':main()
