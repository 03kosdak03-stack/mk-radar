"""MK Final reply builder for an existing Telegram router. Pure function: does not poll, send, or touch any bot token.

Usage from a router:  from adapters.telegram_final import reply;  bot.send(chat, reply('/final'))
Commands: /final  /varlik  /faaliyet  /hisse TICKER  /takip  /backtest  /kural
"""
from pathlib import Path
import json, sys


def _env(name, default=''):
    """Önce ortam değişkeni, yoksa botun .env dosyası (pydantic .env'i ortama aktarmaz)."""
    import os
    from pathlib import Path
    if os.environ.get(name): return os.environ[name]
    f = Path(__file__).resolve().parents[1] / '.env'
    try:
        for line in f.read_text(encoding='utf-8').splitlines():
            k, _, v = line.partition('=')
            if k.strip() == name: return v.strip().strip('"').strip("'")
    except OSError:
        pass
    return default

# MK_VAL_AI_V2 klasörünün yolu (.env içinde MKFINAL_DIR=C:\\Users\\...\\MK_VAL_AI_V2).
ROOT = Path(_env('MKFINAL_DIR') or Path(__file__).resolve().parents[1])
LIVE = ROOT / 'data/live'
NOTE = 'Araştırma amaçlı model senaryosu; garanti getiri değildir. Yatırım tavsiyesi değildir.'


def _load(name):
    p = LIVE / name
    if not p.exists():
        p = ROOT / 'reports' / name
    if not p.exists():
        p = ROOT / 'mk-final' / name
    if not p.exists():
        p = ROOT / 'exports/web' / name
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else None


def _n(v, d=2):
    if v is None:
        return '—'
    s = f'{v:,.{d}f}'
    return s.replace(',', 'X').replace('.', ',').replace('X', '.')


def _p(v):
    return '—' if v is None else f"{'+' if v > 0 else ''}%{100 * v:.0f}"


def _line(i, x):
    w = ' ⚠' if x.get('warnings') else ''
    return f"{i}. {x['ticker']} {_n(x['price'])} → model değeri {_n(x['value_ps'])} (fark {_p(x['potential'])}){w}"


def _snapshot():
    return _load('final_snapshot.json')


def reply(command, text=''):
    cmd = command.split('@')[0].lower()
    d = _snapshot()
    if cmd in ('/kural', '/yardim', '/help'):
        return ('MK Final kuralları\n'
                '• 3 VARLIK şirketi: GYO, holding veya gayrimenkulü aktifinin %30+ olanlar. Piyasa değeri / (özkaynak − sermaye avansı) en düşük 3.\n'
                '• 2 FAALİYET şirketi: 16 × ileri yıl kârı (MK Final 1.0; kâr tahmini yoksa özkaynak + kârlılık), farkı en yüksek 2.\n'
                "• Günlük işlem ≥ 20.000 $, kırmızı bayrak yok. 5 hisse eşit ağırlık; önerilen: parayı 4'e bölüp her çeyrek bir dilim.\n"
                '• Model değerine fark = model değeri / fiyat − 1; beklenen getiri değildir, model değeri düşebilir. Hedef fiyat yok; satış zamanı sizin kararınız.\n• Gizli değer (Reysaş tipi) sıralamada değil; /hisse ile ek senaryo olarak görünür. Payı doğrulanamayan hisse seçilmez; aynı gruptan en fazla 1 hisse.\n'
                'Komutlar: /mkfinal /secim /varlik /faaliyet /deger KOD /takip /mkbacktest\n' + NOTE)
    if cmd == '/backtest':
        b = _load('final_backtest.json')
        if not b:
            return 'Bu sürümde backtest dosyası yüklenmedi.'
        rows = '\n'.join(f"15 Nis {y['year']}: {_p(y['avg'])}  ({' '.join(p['ticker'] for p in y['picks'])})" for y in b['years'])
        return f"MK Final geriye dönük test (yüklenen rapor; bilgi sızıntısı ve gerçekleşebilirlik burada doğrulanmadı)\n{rows}\nToplam: {b['total_multiple']:.0f} kat\n{NOTE}"
    if d is None:
        return 'MK Final: henüz tarama yok. Bilgisayarda 05_MK_FINAL_WINDOWS.bat çalıştırın.'
    head = f"MK Final • {d['asof']}"
    if cmd == '/final':
        a = [x for x in d['picks'] if x['group'] == 'VARLIK']
        o = [x for x in d['picks'] if x['group'] != 'VARLIK']
        return (f"{head}\nVARLIK (Pabrai)\n" + '\n'.join(_line(i, x) for i, x in enumerate(a, 1)) +
                "\nFAALİYET (MK)\n" + '\n'.join(_line(i, x) for i, x in enumerate(o, 1)) +
                f"\n5 hisse eşit ağırlık. ⚠ = kontrol uyarısı (/deger KOD).\n{NOTE}")
    if cmd in ('/varlik', '/faaliyet'):
        g = 'VARLIK' if cmd == '/varlik' else 'FAALİYET'
        rows = [x for x in d['asset' if g == 'VARLIK' else 'operating'] if x.get('eligible')][:15]
        return f"{head} • {g} ilk 15\n" + '\n'.join(_line(i, x) for i, x in enumerate(rows, 1)) + f"\n{NOTE}"
    if cmd == '/hisse':
        t = text.strip().upper()
        if not t:
            return 'Kullanım: /deger RYSAS'
        x = next((r for r in d['asset'] + d['operating'] if r['ticker'] == t), None)
        if x is None:
            return f'{t}: bu taramada değerlenemedi (veri eksik, zarar/negatif kâr ya da likidite dışı).'
        pick = any(p['ticker'] == t for p in d['picks'])
        extra = ''
        if x['group'] == 'VARLIK':
            extra = f"\nPD / düzeltilmiş özkaynak: {_n(x.get('score'))}"
            if x.get('hidden_from'):
                extra += f"\nEk senaryo (doğrulanmamış, sıralamada yok): gizli değer {', '.join(x['hidden_from'])} {_n((x.get('hidden_value') or 0) / 1e6, 0)} mn TL → hisse başı {_n((x.get('detail') or {}).get('hidden_scenario_ps'))} TL"
        warns = ''.join(f"\n⚠ {w}" for w in x.get('warnings', []))
        q = x.get('quality') or {}
        if q.get('level'):
            mark = {'KIRMIZI': '⛔ KIRMIZI — sistem seçmez', 'SARI': '⚠ SARI — kontrol edin', 'TEMİZ': '✓ TEMİZ'}.get(q['level'], q['level'])
            warns += f"\nKalite kontrolü: {mark}" + ''.join(f"\n  • {l}: {m}" for l, m in q.get('flags', []))
        return (f"{t} • {x['company']}\nGrup: {x['group']} • {x['method']}\n"
                f"Fiyat: {_n(x['price'])} TL ({x['price_date']})\nModel değeri: {_n(x['value_ps'])} TL (fark {_p(x['potential'])}; beklenen getiri değildir)\n"
                f"{extra}\nBilanço: {x['period_end']} (yayın {x['published_at']})\n"
                f"Bu haftanın listesinde: {'EVET' if pick else 'hayır'}{warns}\n" + '\n'.join(x.get('source_urls', [])) + f"\n{NOTE}")
    if cmd == '/takip':
        t = _load('final_tracking.json')
        if not t:
            return 'Sanal takip ilk haftalık taramadan sonra başlar.'
        out = []
        for w in t[-4:][::-1]:
            out.append(f"{w['asof']} listesi: ortalama {_p(w['avg'])}\n" + '  '.join(f"{r['ticker']} {_p(r['ret'])}" + (' ✓' if r.get('above_value', False) else '') for r in w['rows']))
        return 'MK Final sanal takip (✓ = fiyat model değerinin üstünde)\n' + '\n'.join(out) + f"\n{NOTE}"
    return 'MK Final komutları: /mkfinal /secim /varlik /faaliyet /deger KOD /takip /mkbacktest /kural'


if __name__ == '__main__':
    print(reply(sys.argv[1] if len(sys.argv) > 1 else '/final', ' '.join(sys.argv[2:])))
