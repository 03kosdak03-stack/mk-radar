import asyncio
import re
import json
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from telegram import Update,InlineKeyboardButton,InlineKeyboardMarkup,WebAppInfo,MenuButtonWebApp
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.error import InvalidToken, NetworkError, Conflict,TelegramError
from .config import settings
from .db import Store
from .service import UpdateService
from .presentation import detail, top_text, portfolio_text, status_text, list_text, final_text
from .kap_sync import KapSync,atomic_json


async def reply(update, text):
    message = update.effective_message
    if message:
        # Plain text, bounded chunks; no Markdown escaping from company names.
        for start in range(0, len(text), 3500):
            await message.reply_text(text[start:start + 3500])


def store(context):
    return context.application.bot_data['store']


def current_run(context):
    run = store(context).latest_run()
    if run:
        stamp = datetime.fromisoformat(run['as_of'])
        now = datetime.now(ZoneInfo('Europe/Istanbul'))
        if now - stamp > timedelta(hours=24):
            return None, 'Son tarama 24 saatten eski. /yenile ile fiyatları ve yüklenen raporları yeniden hesapla.'
    return run, None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply(update, 'MK Araştırma • KAP bağlantısı 1.6\n500 değerleme hedefi henüz doğrulanmadı.\n/radar • /liste 1 • /hisse AKSEN • /top • /portfoy • /final • /durum • /kapguncelle • /yenile • /yardim')


async def kapguncelle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if settings.admin_ids and (not update.effective_user or update.effective_user.id not in settings.admin_ids):
        await reply(update, 'Güncelleme yetkisi yöneticiye ait.')
        return
    lock = context.application.bot_data['scan_lock']
    if lock.locked():
        await reply(update, 'KAP güncellemesi/tarama devam ediyor. /durum ile son kayıtlı sonucu görebilirsin.')
        return
    async with lock:
        await reply(update, 'KAP şirket listesi ve geçmiş raporlar yükleniyor. İlk indirme birkaç dakika sürebilir; ardından tarama yapılacak.')
        result = await asyncio.to_thread(KapSync().sync,force=True)
        run = await asyncio.to_thread(UpdateService(store=store(context)).scan)
        note = f"KAP: {result['downloaded']} arşiv indirildi, {result['cached']} arşiv önbellekten."
        if result['errors']:
            note += '\n' + '\n'.join(result['errors'][:4])
        await reply(update, note + '\n\n' + status_text(run))


async def automatic_updates(application):
    while True:
        try:
            async with application.bot_data['scan_lock']:
                await update_cycle(application)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Exception messages can contain URLs/tokens. Store only the type.
            application.bot_data['background_error'] = type(exc).__name__
            atomic_json(Path(settings.kap_data_dir)/'automatic_status.json',dict(
                state='ERROR',checked_at=datetime.now(ZoneInfo('Europe/Istanbul')).isoformat(),error_type=type(exc).__name__))
        await asyncio.sleep(max(1, settings.kap_sync_interval_hours)*3600)


async def update_cycle(application,sync=None,service=None):
    started=datetime.now(ZoneInfo('Europe/Istanbul'))
    status_path=Path(settings.kap_data_dir)/'automatic_status.json'
    await asyncio.to_thread(atomic_json,status_path,dict(state='RUNNING',started_at=started.isoformat()))
    result=await asyncio.to_thread((sync or KapSync()).sync)
    run=await asyncio.to_thread((service or UpdateService(store=application.bot_data['store'])).scan)
    completed=datetime.now(ZoneInfo('Europe/Istanbul'))
    await asyncio.to_thread(atomic_json,status_path,dict(state='PARTIAL' if result['errors'] else 'OK',
        started_at=started.isoformat(),completed_at=completed.isoformat(),scan_as_of=run['as_of'],
        next_check_after=(completed+timedelta(hours=max(1,settings.kap_sync_interval_hours))).isoformat(),
        errors=result['errors'],total=run['total']))
    application.bot_data.pop('background_error',None)
    return result,run


async def post_init(application):
    if settings.mini_app_url:
        from urllib.parse import urlparse
        parsed=urlparse(settings.mini_app_url)
        if parsed.scheme!='https' or not parsed.netloc:
            application.bot_data['mini_app_error']='MINI_APP_URL_HTTPS_GEREKLI'
        else:
            try:
                await application.bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text='MK Radar',web_app=WebAppInfo(settings.mini_app_url)))
            except TelegramError:
                application.bot_data['mini_app_error']='MENÜ_BAĞLANTISI_KURULAMADI'
    if settings.kap_auto_sync:
        application.bot_data['kap_task'] = asyncio.create_task(automatic_updates(application))


async def post_stop(application):
    task = application.bot_data.get('kap_task')
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def hisse(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ticker = context.args[0].upper().removesuffix('.IS') if len(context.args) == 1 else ''
    if not re.fullmatch('[A-Z0-9]{2,12}', ticker):
        await reply(update, 'Kullanım: /hisse AKSEN')
        return
    service = UpdateService(store=store(context))
    row = await asyncio.to_thread(service.value_ticker, ticker)
    await reply(update, detail(row))


async def radar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from urllib.parse import urlparse
    parsed=urlparse(settings.mini_app_url)
    if parsed.scheme!='https' or not parsed.netloc:
        await reply(update,'Kart ekranı kodu hazır. Telegram’dan açmak için panel sunucusunun erişilebilir HTTPS adresini .env içindeki MINI_APP_URL alanına eklemek gerekiyor. Yerel önizleme: ONIZLEME_PANEL.cmd. Botun komutları /top ve /hisse KOD ile çalışır.')
        return
    if not update.effective_chat or update.effective_chat.type!='private':
        await reply(update,'MK Radar’ı botla özel sohbetinden /radar komutuyla aç.')
        return
    await update.effective_message.reply_text('MK Radar · bilanço hedefleri',reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton('📊 MK RADAR’I AÇ',web_app=WebAppInfo(settings.mini_app_url))]]))


async def top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    run, warning = current_run(context)
    await reply(update, warning or top_text(run))


async def portfoy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    run, warning = current_run(context)
    await reply(update, warning or portfolio_text(run))


async def durum(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = status_text(store(context).latest_run())
    if context.application.bot_data['scan_lock'].locked():
        text += '\nKAP güncellemesi/tarama şu anda devam ediyor.'
    text += '\nOtomatik KAP: ' + ('açık (bot çalışırken)' if settings.kap_auto_sync else 'kapalı')
    text+='\nPanel HTTPS adresi: '+('ayarlı' if settings.mini_app_url else 'henüz ayarlanmadı')
    if context.application.bot_data.get('mini_app_error'):
        text+='\nPanel menüsü: '+context.application.bot_data['mini_app_error']
    status_path = Path(settings.kap_data_dir)/'sync_status.json'
    if status_path.exists():
        try:
            status = json.loads(status_path.read_text(encoding='utf-8'))
            text += '\nSon KAP kontrolü: ' + (status.get('completed_at') or status.get('started_at', '—'))
            if status.get('errors'):
                text += '\nKAP eksikleri: ' + '; '.join(status['errors'][:3])
        except (OSError, ValueError):
            text += '\nKAP güncelleme durumu okunamadı.'
    if context.application.bot_data.get('background_error'):
        text += '\nOtomatik güncelleme hatası: ' + context.application.bot_data['background_error']
    auto_path=Path(settings.kap_data_dir)/'automatic_status.json'
    try:
        auto=json.loads(auto_path.read_text(encoding='utf-8'))
        text+='\nOtomatik döngü: '+auto['state']
        if auto.get('next_check_after'):text+='\nSonraki kontrol: '+auto['next_check_after']
    except (OSError,ValueError,KeyError):pass
    await reply(update, text)


async def gecmis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ticker = context.args[0].upper() if len(context.args) == 1 else ''
    if not re.fullmatch('[A-Z0-9]{2,12}', ticker):
        await reply(update, 'Kullanım: /gecmis AKSEN')
        return
    history = store(context).history(ticker)
    await reply(update, '\n\n'.join(detail(r) for r in history) if history else 'Bu hisse için kayıt yok.')


async def yenile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if settings.admin_ids and (not update.effective_user or update.effective_user.id not in settings.admin_ids):
        await reply(update, 'Tarama başlatma yetkisi yöneticiye ait.')
        return
    lock = context.application.bot_data['scan_lock']
    if lock.locked():
        await reply(update, 'Tarama devam ediyor; tamamlanınca sonuç kaydedilecek.')
        return
    last = store(context).latest_run()
    if last and datetime.now(ZoneInfo('Europe/Istanbul')) - datetime.fromisoformat(last['as_of']) < timedelta(minutes=1):
        await reply(update, 'Son tarama bir dakikadan yeni. /top ve /durum ile sonucu görebilirsin.')
        return
    async with lock:
        await reply(update, 'Yüklenen finansal raporlar ve fiyatlar araştırma motoruyla taranıyor. Eksik kapsam ayrıca gösterilecek; sonuç ilk15 ve beşli olarak kaydedilecek.')
        service = UpdateService(store=store(context))
        run = await asyncio.to_thread(service.scan)
        await reply(update, status_text(run) + '\n\n/top — ilk15\n/portfoy — beşli')


async def liste(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args)>1 or (context.args and not re.fullmatch(r'[0-9]{1,3}',context.args[0])):
        await reply(update,'Kullanım: /liste 1')
        return
    page=int(context.args[0]) if context.args else 1
    provider=await asyncio.to_thread(UpdateService.new_financials)
    if not provider.universe:
        await reply(update,'Doğrulanmış KAP pay listesi henüz yüklenmedi. /kapguncelle veya otomatik güncellemenin tamamlanmasını bekle. /durum')
        return
    await reply(update,list_text(provider.tickers(),store(context).latest_run(),page))


async def yardim(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply(update, '/radar — kart ekranını aç\n/hisse KOD — değer bantları ve rapor kaynağı\n/top — ilk15\n/portfoy — ilk15 içinden likit beşli\n/final — yatırım doğrulaması ve eksik kapsam\n/durum — kapsama ve güncelleme durumu\n/liste 1 — eksikler dahil tüm kodlar\n/gecmis KOD — önceki hesaplar\n/kapguncelle — KAP listesini/geçmiş raporları indir ve tara\n/yenile — önbellekteki raporlarla fiyatları yeniden tara\n\nOtomatik KAP kontrolü varsayılan 6 saatte bir, bot çalışırken yapılır. TTM için raporların örtüşen karşılaştırma kalemleri parasal olarak eşleştirilir. Eşleşmeyen raporlar sıralamaya alınmaz. Banka/finans artık kâr senaryosu kullanır; holding/GYO kaynaklı varlık değerleri bekler.')


async def final(update: Update, context: ContextTypes.DEFAULT_TYPE):
    path=Path(__file__).resolve().parents[1]/'validation/final_review.json'
    try:
        review=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):review=None
    run,warning=current_run(context)
    text=final_text(run,review)
    if warning:text=warning+'\n\n'+text
    await reply(update,text)


async def on_error(update, context):
    if update and update.effective_message:
        await reply(update, 'İşlem tamamlanamadı. Veri dosyalarını ve bağlantıyı kontrol edip yeniden dene.')


def build_application(token=None, snapshot_store=None):
    token = token or settings.telegram_bot_token
    if not token:
        raise ValueError('.env içinde TELEGRAM_BOT_TOKEN gerekli.')
    application = Application.builder().token(token).concurrent_updates(8).post_init(post_init).post_stop(post_stop).build()
    application.bot_data['store'] = snapshot_store or Store()
    application.bot_data['scan_lock'] = asyncio.Lock()
    for name, callback in [('start', start), ('hisse', hisse), ('top', top), ('portfoy', portfoy), ('durum', durum), ('gecmis', gecmis), ('yenile', yenile), ('kapguncelle', kapguncelle), ('yardim', yardim), ('liste', liste), ('final', final), ('radar',radar)]:
        application.add_handler(CommandHandler(name, callback))
    application.add_error_handler(on_error)
    return application


def main():
    asyncio.set_event_loop(asyncio.new_event_loop())
    try:
        build_application().run_polling()
    except InvalidToken:
        print('Telegram tokenı kabul edilmedi. .env içindeki TELEGRAM_BOT_TOKEN değerini BotFather tokenıyla yenile.')
    except Conflict:
        print('Aynı bot başka bir yerde çalışıyor. Diğer bot sürecini durdurup yeniden başlat.')
    except NetworkError:
        print('Telegram bağlantısı kurulamadı. İnternet bağlantısını kontrol edip yeniden başlat.')


if __name__ == '__main__':
    main()
