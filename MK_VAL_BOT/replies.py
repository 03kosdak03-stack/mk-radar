"""MK Final reply builder for an existing Telegram router. Pure function: does not poll, send, or touch any bot token.

Usage from a router: from adapters.telegram_final import reply; bot.send(chat, reply('/final'))
Commands: /final(/secim) /varlik /faaliyet /hisse(/deger) TICKER /takip /backtest /kural
"""
from pathlib import Path
import json, sys

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / 'data/live'
NOTE = 'Araştırma amaçlı model senaryosu; garanti getiri değildir. Yatırım tavsiyesi değildir.'


def _load(name):
    p = LIVE / name
    if not p.exists():
        p = ROOT / 'reports' / name
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else None


def _n(v, d=2):
    if v is None:
        return '—'
    s = f'{v:,.{d}f}'
    return s.replace(',', 'X').replace('.', ',').replace('X', '.')


def _p(v):
    return '—' if v is None else f"{'+' if v > 0 else '−' if v < 0 else ''}%{abs(100 * v):.0f}"


def _line(i, x):
    w = ' ⚠' if x.get('warnings') else ''
    return f"{i}. {x['ticker']} {_n(x['price'])} → model değeri {_n(x['value_ps'])} (fark {_p(x['potential'])}){w}"


def _owners(c):
    own = c.get('owners') or []
    other = lambda r: r[0].upper().startswith(('DİĞER', 'DIGER'))
    part = lambda r: ('Diğer' if other(r) else r[0]) + (f" ({r[3]})" if r[3] else '') + f" %{100 * r[1]:.0f}" + (f" (oy %{100 * r[2]:.0f})" if abs(r[2] - r[1]) > 0.005 else '')
    return ('\nAna ortaklar: ' + ', '.join(part(r) for r in own)) if own else ''


def _company(x):
    """Şirketin durumu: kısa güçlü/zayıf etiketleri (mini uygulamadaki bölümün özeti)."""
    F = (x.get('ctx') or {}).get('fin') or {}
    if not F:
        return ''
    mc = F.get('mcap') or x.get('mcap')
    a = x.get('group') == 'VARLIK'
    W, S = [], []
    ch = lambda v, p: v / p - 1 if v is not None and p and p > 0 else None
    rg = ch(F.get('rev'), F.get('rev_prior'))
    if rg is not None and rg <= -0.10:
        W.append(f"satışlar düşüyor ({_p(rg)})")
    elif rg is not None and rg >= 0.10:
        S.append(f"satışlar artıyor ({_p(rg)})")
    if not a and F.get('gross') is not None and F['gross'] < 0:
        W.append('ana iş brüt zararda')
    elif not a and F.get('op') is not None and F['op'] < 0:
        W.append('esas faaliyetler zararda')
    np_, npp, m = F.get('np'), F.get('np_prior'), F.get('months')
    ey = np_ * 12 / m / mc if np_ is not None and m and mc else None
    if np_ is not None and np_ <= 0:
        W.append('net zarar')
    elif ey is not None and ey < 0.03:
        W.append('kâr çok az')
    elif ey is not None and ey >= 0.10:
        S.append('kârına göre ucuz')
    pg = ch(np_, npp) if np_ and np_ > 0 else None
    if pg is not None and pg <= -0.30:
        W.append(f"kâr düşüyor ({_p(pg)})")
    elif pg is not None and pg >= 0.30:
        S.append(f"kâr artıyor ({_p(pg)})")
    debt, cash = F.get('debt'), F.get('cash')
    if debt is not None and cash is not None and mc:
        nd = debt - cash
        if nd < 0 and -nd >= 0.1 * mc:
            S.append('borçtan fazla nakit')
        elif nd >= mc:
            W.append('borç yüksek')
    if not a and F.get('mach') and F.get('equity') and F['mach'] >= 0.35 * F['equity']:
        W.append('özkaynağın çoğu makine')
    if F.get('rel_recv') and mc and F['rel_recv'] >= 0.15 * mc:
        W.append('grup şirketinden yüksek alacak')
    ex = (F.get('ip') or 0) + (F.get('eqinv') or 0)
    if not a and mc and ex >= 0.3 * mc:
        S.append('üretim dışı gayrimenkul ve iştirakler')
    L = F.get('lv') or {}
    if L.get('mid', 0) > 0 and L.get('low', -1) >= (mc or 0):
        S.append('tasfiye değeri piyasa değerinin üstünde')
    out = ''
    if S:
        out += '\nGüçlü: ' + ', '.join(S)
    if W:
        out += '\nZayıf: ' + ', '.join(W)
    return out


def _why(d, x):
    """Kısa yatırım gerekçesi + ana ortaklar."""
    c = x.get('ctx') or {}
    if not c:
        return ''
    if not (x.get('potential') or 0) > 0 or (x.get('quality') or {}).get('level') == 'KIRMIZI':
        return _company(x) + _owners(c)
    out, r, v = [], c.get('ret1y'), c.get('value_chg1y')
    if r is not None:
        out.append(f"Fiyat son 1 yıl {_p(r)}; 52 hafta zirvesinin {_p(c.get('off_high'))} altında")
    if r is not None and v is not None:
        if r < 0 and v > r + 0.25:
            out.append(f"Ucuzluk fiyat düşüşünden geliyor: şirket değeri {_p(v)}, fiyat {_p(r)}")
        elif v > r + 0.25:
            out.append(f"Ucuzluk değer artışından geliyor: değer {_p(v)}, fiyat {_p(r)}")
    h = [p for _, p in c.get('pot_hist') or [] if p is not None]
    if h:
        out.append('Son 1 yılın tüm ölçümlerinde ucuzdu' if all(p > 0.2 for p in h) else
                   'Ucuzluk yeni: 1 yıl önce değerinin üstündeydi' if h[0] <= 0 else
                   f"Son 1 yılın {len(h)} ölçümünün {sum(p > 0.2 for p in h)}'inde ucuzdu")
    if r is not None and r <= -0.5:
        out.append('⚠ Bu kadar sert düşüşün nedenini (sulanma, dava, ortak satışı, finansman) KAP\'ta bulmadan almayın')
    return (('\nGerekçe:' + ''.join(f"\n• {s_}" for s_ in out)) if out else '') + _company(x) + _owners(c)


def _snapshot():
    return _load('final_snapshot.json')


def reply(command, text=''):
    cmd = command.split('@')[0].lower()
    d = _snapshot()
    if cmd in ('/kural', '/yardim', '/help'):
        return ('MK Final kuralları\n'
                '• 3 VARLIK şirketi: GYO, holding veya gayrimenkulü aktifinin %30+ olanlar. Piyasa değeri / (özkaynak − sermaye avansı + gizli değer) en düşük 3.\n'
                '• 2 FAALİYET şirketi: 16 × ileri yıl kârı (MK Final 1.0; kâr tahmini yoksa özkaynak + kârlılık), farkı en yüksek 2.\n'
                "• Günlük işlem ≥ 20.000 $, kırmızı bayrak yok. 5 hisse eşit ağırlık; önerilen: parayı 4'e bölüp her çeyrek bir dilim.\n"
                "• Model değerine fark = model değeri / fiyat − 1; beklenen getiri değildir, model değeri düşebilir. Hedef fiyat yok; satış zamanı sizin kararınız.\n• Gizli değer (Reysaş tipi) sıralamada: borsadaki bağlı GYO'nun ekspertiz farkı özkaynağa eklenir (otomatik, doğrulanmamış). Payı doğrulanamayan hisse seçilmez; aynı gruptan en fazla 1 hisse.\n"
                'Komutlar: /uygulama /secim /varlik /faaliyet /deger KOD /takip /backtest\n' + NOTE)
    if cmd == '/backtest':
        b = _load('final_backtest.json')
        if not b:
            return 'Backtest dosyası yok. Bilgisayarda: python run.py final-backtest'
        rows = '\n'.join(f"15 Nis {y['year']}: {_p(y['avg'])}  ({' '.join(p['ticker'] for p in y['picks'])})" for y in b['years'])
        return f"MK Final geriye dönük test (o günkü bilgilerle, ertesi gün al, 12 ay tut; doğrulanmış başarı değildir)\n{rows}\nToplam: {b['total_multiple']:.0f} kat\n{NOTE}"
    if d is None:
        return 'MK Final: henüz tarama yok. Bilgisayarda 05_MK_FINAL_WINDOWS.bat çalıştırın.'
    head = f"MK Final • {d['asof']}"
    if cmd in ('/final', '/secim'):
        a = [x for x in d['picks'] if x['group'] == 'VARLIK']
        o = [x for x in d['picks'] if x['group'] != 'VARLIK']
        return (f"{head}\nVARLIK (Pabrai)\n" + '\n'.join(_line(i, x) for i, x in enumerate(a, 1)) +
                "\nFAALİYET (MK)\n" + '\n'.join(_line(i, x) for i, x in enumerate(o, 1)) +
                f"\n5 hisse eşit ağırlık. ⚠ = kontrol uyarısı (/deger KOD).\n{NOTE}")
    if cmd in ('/varlik', '/faaliyet'):
        g = 'VARLIK' if cmd == '/varlik' else 'FAALİYET'
        rows = [x for x in d['asset' if g == 'VARLIK' else 'operating'] if x.get('eligible')][:15]
        return f"{head} • {g} ilk 15\n" + '\n'.join(_line(i, x) for i, x in enumerate(rows, 1)) + f"\n{NOTE}"
    if cmd in ('/hisse', '/deger'):
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
            if x.get('hidden_from') and (x.get('detail') or {}).get('hidden_in_rank'):
                extra += f"\nGizli değer eklendi (Reysaş tipi): {', '.join(x['hidden_from'])} {_n((x.get('hidden_value') or 0) / 1e6, 0)} mn TL"
            elif x.get('hidden_from'):
                extra += f"\nEk senaryo (doğrulanmamış, sıralamada yok): gizli değer {', '.join(x['hidden_from'])} {_n((x.get('hidden_value') or 0) / 1e6, 0)} mn TL → hisse başı {_n((x.get('detail') or {}).get('hidden_scenario_ps'))} TL"
        warns = ''.join(f"\n⚠ {w}" for w in x.get('warnings', []))
        q = x.get('quality') or {}
        if q.get('level'):
            mark = {'KIRMIZI': '⛔ KIRMIZI — sistem seçmez', 'SARI': '⚠ SARI — kontrol edin', 'TEMİZ': '✓ TEMİZ'}.get(q['level'], q['level'])
            warns += f"\nKalite kontrolü: {mark}" + ''.join(f"\n  • {l}: {m}" for l, m in q.get('flags', []))
        return (f"{t} • {x['company']}\nGrup: {x['group']} • {x['method']}\n"
                f"Fiyat: {_n(x['price'])} TL ({x['price_date']})\nModel değeri: {_n(x['value_ps'])} TL (fark {_p(x['potential'])}; beklenen getiri değildir)\n"
                f"{extra}\nBilanço: {x['period_end']} (yayın {x['published_at']})\n"
                f"Bu haftanın listesinde: {'EVET' if pick else 'hayır'}{warns}{_why(d, x)}\n" + '\n'.join(x.get('source_urls', [])) + f"\n{NOTE}")
    if cmd == '/takip':
        t = _load('final_tracking.json')
        if not t:
            return 'Sanal takip ilk haftalık taramadan sonra başlar.'
        out = []
        for w in t[-4:][::-1]:
            ctrl = ''.join(f" · {k} {_p(v)}" for k, v in (w.get('controls') or {}).items())
            out.append(f"{w['asof']} listesi: ortalama {_p(w['avg'])}{ctrl}\n" + '  '.join(f"{r['ticker']} {_p(r['ret'])}" + (' ✓' if r.get('above_value') else '') for r in w['rows']))
        return 'MK Final sanal takip (✓ = fiyat model değerinin üstünde)\n' + '\n'.join(out) + f"\n{NOTE}"
    return 'MK Final komutları: /uygulama /secim /varlik /faaliyet /deger KOD /takip /backtest /kural'


if __name__ == '__main__':
    print(reply(sys.argv[1] if len(sys.argv) > 1 else '/final', ' '.join(sys.argv[2:])))
