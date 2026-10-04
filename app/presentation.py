"""Plain-text Telegram messages, independent of Telegram/network for testing."""
from datetime import datetime, timezone


def fmt(value, decimals=2):
    if value is None:
        return '—'
    return f'{value:,.{decimals}f}'.replace(',', '_').replace('.', ',').replace('_', '.')


def detail(row):
    lines = [f"{row['ticker']} • {row.get('model_version','MK Araştırma')}", f"Durum: {row['status']}"]
    if row.get('company'):
        lines.append(row['company'])
    if row.get('period'):
        lines.append(f"Finansal dönem: {row['period']} ({row.get('basis', '—')})")
    if row.get('published_at'):
        lines.append(f"Rapor yayını: {row['published_at']}")
    if row.get('price') is not None:
        lines.append(f"Kapanış: {fmt(row['price'])} TL • {row.get('price_date', '—')}")
    bands = row.get('bands', {})
    if bands and all(v.get('price') is not None for v in bands.values()):
        labels=([('bear','Senaryo 1'),('base','Baz'),('bull','Senaryo 3')]
                if row.get('model_version')=='MK-FINANCIAL-RI-0.1' else
                [('bear','Aşağı'),('base','Baz'),('bull','Yukarı')])
        for key, label in labels:
            v = bands[key]['price']
            lines.append(f'{label} senaryo: {fmt(v)} TL' if v > 0 else f'{label} senaryo: pozitif kâr hedefi yok')
        if row.get('price_range'):
            lines.append(f"Senaryo aralığı: {fmt(row['price_range']['low'])}–{fmt(row['price_range']['high'])} TL")
        if row.get('potential') is not None:
            lines.append(f"Baz potansiyel: %{fmt(row['potential'], 1)}")
        lines.append('Değerler varsayımlı senaryodur; kesin eder değildir.')
        lines.append('Model kuralı: baz hedefte satış, en geç bir yıl sonunda çıkış.')
    if row.get('reason'):
        lines.append('Gerekçe: ' + row['reason'])
    context=row.get('target_context',{})
    previous=context.get('previous_filing_target')
    if previous:
        lines.append(f"Önceki bilanço baz hedefi: {fmt(previous.get('fair_value'))} TL • {previous['period_end']}")
        if context.get('target_change_pct') is not None:
            lines.append(f"Hedef değişimi: %{fmt(context['target_change_pct'])}")
        else:
            lines.append('Pay birimi eşleşmedi/doğrulanmadı; iki hedefin yüzde değişimi karşılaştırılmadı.')
    last=context.get('last_valid_target')
    if not context.get('current_target_available',True) and last:
        lines.append(f"Son geçerli eski hedef: {fmt(last['fair_value'])} TL • {last['period_end']}")
        lines.append('Yeni hedef doğrulanamadı. Eski hedef güncel hedef veya satış talimatı değildir.')
    for warning in row.get('warnings', []):
        lines.append('• ' + warning)
    for url in row.get('source_urls', [])[:3]:
        lines.append('Kaynak: ' + url)
    return '\n'.join(lines)


def top_text(run):
    if not run:
        return 'Henüz Final 1.0 taraması yok. /yenile ile yüklenen verileri tara.'
    lines = ['MK Araştırma • İlk 15', 'Tarama: ' + run['as_of'], f"Yüklenen evren: {run['total']} | Hesaplanan: {run['counts'].get('HESAPLANDI', 0)}"]
    if run['counts'].get('HESAPLANDI',0)<500:
        lines.append('Kapsam eksik: bu ilk15 tüm piyasadan doğrulanmış yatırım finali değildir.')
    if not run['top15']:
        lines.append('Sıralanabilen şirket yok. /durum ile veri eksiklerini incele.')
    for i, row in enumerate(run['top15'], 1):
        if row['fair_value'] is None:
            value = 'Pozitif hedef yok'
        else:
            value = f"Baz {fmt(row['fair_value'])} TL | %{fmt(row['potential'], 1)}"
        lines.append(f"{i}. {row['ticker']} | Fiyat {fmt(row['price'])} | {value}")
    ranking='Baz senaryo/fiyat potansiyeli' if run.get('ranking_version')=='MK-RANK-2.0-BASE-POTENTIAL' else 'Eski ileri kâr/piyasa değeri'
    lines.extend([f'Sıra: {ranking}. /portfoy — ilk15 içinden likit beşli.', run['coverage_note']])
    return '\n'.join(lines)


def portfolio_text(run):
    if not run:
        return 'Henüz tarama yok. /yenile'
    lines = ['MK Araştırma • Beşli', 'Tarama: ' + run['as_of']]
    if run['counts'].get('HESAPLANDI',0)<500:
        lines.append('500 fiyatlı değerleme tamamlanmadı; bu beşli eksik evrenin model çıktısıdır.')
    for row in run['portfolio5']:
        lines.append(f"{row['ticker']} | Model payı %20 | Baz: {fmt(row['fair_value'])} TL")
    if run['cash_slots']:
        lines.append(f"Boş yuva: {run['cash_slots']} (%{run['cash_slots'] * 20} nakit).")
    lines.extend(['Kural: ilk15 içindeki yaklaşık 20 günlük medyan hacmi ≥5 milyon TL olan ilk5.', 'Eksik hacim likit kabul edilmez. Baz hedefte, en geç bir yıl sonunda çıkış.'])
    if run.get('ranking_version')=='MK-RANK-2.0-BASE-POTENTIAL':
        lines.append('Baz senaryosu karar fiyatının üstünde olmayan şirket beşliye alınmaz; eksik yuvalar nakit kalır.')
    return '\n'.join(lines)


def status_text(run):
    if not run:
        return 'Henüz tarama yok. /yenile'
    lines = ['Son tarama: ' + run['as_of'], 'Evren: ' + str(run['total'])]
    scenarios = sum(bool(r.get('bands')) and all(b.get('price') is not None for b in r['bands'].values()) for r in run['rows'])
    lines.append(f'Finansal senaryo hesabı: {scenarios} | Fiyatıyla sıralanan: {run["counts"].get("HESAPLANDI",0)}')
    if scenarios < 500:
        lines.append('500 şirket değerleme hedefi henüz karşılanmadı; sistem yatırım finali değildir.')
    lines.extend(f'{key}: {value}' for key, value in sorted(run['counts'].items()))
    lines.append('Eksiklerden örnekler:')
    for row in [r for r in run['rows'] if r['status'] != 'HESAPLANDI'][:8]:
        lines.append(row['ticker'] + ': ' + (row.get('reason') or row['status']))
    return '\n'.join(lines)


def list_text(tickers, run=None, page=1):
    size=25;pages=max(1,(len(tickers)+size-1)//size)
    if page<1 or page>pages:
        return f'Sayfa 1–{pages} arasında olmalı. /liste 1'
    states={r['ticker']:r['status'] for r in run['rows']} if run else {}
    lines=[f'KAP pay listesi: {len(tickers)} kod • Sayfa {page}/{pages}']
    lines += [f'{t} — {states.get(t,"HENÜZ TARANMADI")}' for t in tickers[(page-1)*size:page*size]]
    lines += ['Detay: /hisse KOD',f'Sonraki sayfa: /liste {page+1}' if page<pages else 'Liste sonu.',
              'Kod listesi değerleme başarısı değildir; eksikler de gösterilir.']
    return '\n'.join(lines)


def final_text(run, review):
    from .final_review import coverage_summary
    lines=['MK • Yatırım doğrulaması: TAMAMLANMADI']
    if run:
        c=coverage_summary(run['rows'])
        lines.extend([f"Son tarama: {run['as_of']}",f"Kod: {c['total_unique']} | Finansal senaryo: {c['financial_scenarios']}",
            f"Pozitif baz: {c['positive_base_values']} | Fiyatlı pozitif değer: {c['priced_positive_values']}/500",
            f"Hacmi bilinen likit değer: {c['known_liquid_values']}"])
    else:
        lines.append('Güncel fiyatlı tarama yok; /kapguncelle ile başlat.')
    if review:
        lines.append('Geliştirme dönemi kontrolleri:')
        for r in review['period_checks']:
            lines.append(f"{r['year']}: {r['priced_values']} fiyatlı hesap; ilk15 ile zirve ilk10 yakalama {r['leaders_in_top15']}/10.")
        w=review['winner_concentration']
        lines.append(f"2025 dinamik koşullu getiri %{fmt(review['dynamic_2025_conditional_return_pct'])}; {w['largest_contributor']} yuvası nakit kalsaydı %{fmt(w['original_portfolio_with_this_slot_cash_pct'])}.")
        lines.extend('• '+x for x in review['blockers'])
    else:
        lines.append('Doğrulama raporu bulunamadı; yazılım testleri yatırım başarısı kanıtı değildir.')
    return '\n'.join(lines)
