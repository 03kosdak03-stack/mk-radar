import getpass,json,os,subprocess,sys
from pathlib import Path
p=Path('storage/local_config.json')
if p.exists():
    config=json.loads(p.read_text())
else:
    config={'TELEGRAM_BOT_TOKEN':getpass.getpass('BotFather token (gizli): '), 'ADMIN_ID':input('Telegram sayisal kullanici kimligin: ').strip(), 'PUBLIC_URL':input('HTTPS uygulama adresi (yoksa bos): ').strip()}
    if not config['ADMIN_ID'].isdigit(): raise SystemExit('Kimlik sayisal olmali.')
    p.parent.mkdir(exist_ok=True); p.write_text(json.dumps(config)); p.chmod(0o600)
subprocess.run([sys.executable,'app.py'],env={**os.environ,**config},check=True)
