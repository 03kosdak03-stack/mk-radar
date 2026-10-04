"""Final 1.0 adapter; engine file is byte-identical to the frozen research engine."""
import math
from .final_engine import forecast

MODEL_VERSION = 'MK-FINAL-1.0'
SCENARIOS = {'bear': (.25, 12), 'base': (.50, 16), 'bull': (1.0, 20)}


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def value_from_kap(fin, price=None):
    result = dict(ticker=fin.ticker, company=fin.company, period=fin.period, published_at=fin.published_at,
        period_end=fin.period_end, basis=fin.basis, source_urls=fin.source_urls,
        method=MODEL_VERSION, model_version=MODEL_VERSION, price=price, warnings=list(fin.warnings),
        status='VERI_EKSIK', reason=None, bands={}, fair_value=None, potential=None, forward_yield=None)
    result['alignment'] = getattr(fin, 'alignment', {})
    result['shares']=fin.financials.get('shares')
    result['shares_as_of']=getattr(fin,'shares_as_of',None)
    result['validation_grade']='RESEARCH_UNCALIBRATED'
    if fin.company_type in ('bank','financial'):
        from .sector_models import financial_bands
        result.update(method='MK-FINANCIAL-RI-0.1',model_version='MK-FINANCIAL-RI-0.1')
        try:
            result['bands']=financial_bands(fin)
        except ValueError as exc:
            result['reason']=str(exc)
            return result
        base=result['bands']['base'];result['forward_profit']=base['profit_proxy']
        result['price_range']=dict(low=min(b['price'] for b in result['bands'].values()),
                                  high=max(b['price'] for b in result['bands'].values()))
        if not (result['bands']['bear']['price']<=base['price']<=result['bands']['bull']['price']):
            result['warnings'].append('Artık kâr senaryo eksenleri fiyat sırasına göre dizili değil; düşük/yüksek aralık price_range içinde.')
        result['fair_value']=base['price'] if base['price']>0 else None
        result['warnings'].append('Artık kâr: özkaynak maliyeti ve kâr tutma oranları varsayımdır; şirket bazında bağımsız kalibre edilmedi.')
        result['warnings'].append('ROE = yıllık/TTM ana ortaklık kârı / son dönem özkaynağı. Beş yılda özkaynak maliyetine yaklaşma varsayımı.')
        if finite(price) and price>0:
            result.update(status='HESAPLANDI',forward_yield=base['profit_proxy']/(price*fin.financials['shares']),
                potential=(base['price']/price-1)*100 if base['price']>0 else None)
        else:
            result.update(status='FIYAT_EKSIK',reason='Güncel fiyat olmadan sıralama yapılamaz.')
        return result
    if fin.company_type != 'normal':
        result.update(status='AYRI_SEKTOR_MODELI', reason='Holding için tarihli SOTP segmentleri; GYO için doğrulanmış varlık değerleri/borçlar gerekli. Defter özkaynağı otomatik NAV sayılmadı.')
        return result
    if fin.input_error:
        result['reason'] = fin.input_error
        return result
    if fin.basis not in ('ANNUAL', 'TTM'):
        result['reason'] = 'Yıllık veya doğrulanmış TTM gelir ve karşılaştırma dönemi gerekli; ara dönem ikiyle çarpılmadı.'
        return result
    if fin.currency != 'TRY':
        result['reason'] = 'TRY ile karşılaştırılabilir parasal ölçek gerekli.'
        return result
    f, extra = fin.financials, fin.extra
    if any(v is not None and not finite(v) for v in list(f.values()) + list(extra.values())):
        result['reason'] = 'Finansal girdide sayısal olmayan veya sonlu olmayan değer var.'
        return result
    result['bands'] = {name: forecast(f, extra, growth, pe) for name, (growth, pe) in SCENARIOS.items()}
    if any(b.get('price') is None for b in result['bands'].values()):
        result['reason'] = next(b['reason'] for b in result['bands'].values() if b.get('price') is None)
        return result
    if any(not finite(b['price']) for b in result['bands'].values()):
        result['reason'] = 'Hesap taşması; sonlu fiyat üretilemedi.'
        return result
    base = result['bands']['base']
    result['forward_profit'] = base['profit_proxy']
    if base['price'] > 0:
        result['fair_value'] = base['price']  # Legacy API alias; UI calls this a scenario.
    else:
        result['warnings'].append('Pozitif kâr hedefi yok; negatif hesap toplam şirket değeri değildir.')
    cfo = f.get('cfo_current')
    if cfo is not None and cfo < 0:
        result['warnings'].append('İşletme nakit akışı negatif.')
    eq, assets = f.get('equity_total_current'), f.get('assets_current')
    if finite(eq) and finite(assets) and assets > 0 and eq / assets < .1:
        result['warnings'].append('Özkaynak/varlık oranı %10 altında.')
    if base['attribution_basis'] == 'EQUITY_SHARE_APPROXIMATION_NOT_PROFIT_ATTRIBUTION':
        result['warnings'].append('Ana ortaklık kâr payı, özkaynak payından yaklaşıklandı.')
    if finite(price) and price > 0:
        result['forward_yield'] = base['profit_proxy'] / (price * f['shares'])
        if base['price'] > 0:
            result['potential'] = (base['price'] / price - 1) * 100
        result['status'] = 'HESAPLANDI'
    else:
        result.update(status='FIYAT_EKSIK', reason='Güncel fiyat olmadan sıralama yapılamaz.')
    return result


def select_universe(rows):
    # No positive-NI/CFO/value-ratio gate. Deterministic alphabetical tie-breaking.
    ranked = sorted((r for r in rows if r['status'] == 'HESAPLANDI' and finite(r.get('forward_yield'))),
                    key=lambda r: (-r['forward_yield'], r['ticker']))
    top15 = ranked[:15]
    selected = [r for r in top15 if finite(r.get('turnover20')) and r['turnover20'] >= 5_000_000][:5]
    return dict(ranked=ranked, top15=top15, portfolio5=selected, cash_slots=5-len(selected))


def select_by_value(rows):
    """Comparable output ratio across different sector valuation methods.

    No technical entry signal: a model portfolio requires a positive base
    valuation discount and known liquidity. A nonpositive earnings proxy is
    not a negative company valuation and cannot supply this ratio.
    """
    ranked=sorted((r for r in rows if r['status']=='HESAPLANDI' and finite(r.get('potential'))
                   and finite(r.get('fair_value')) and r['fair_value']>0),
                  key=lambda r:(-r['potential'],r['ticker']))
    top15=ranked[:15]
    five=[r for r in top15 if r['potential']>0 and finite(r.get('turnover20'))
          and r['turnover20']>=5_000_000][:5]
    return dict(ranked=ranked,top15=top15,portfolio5=five,cash_slots=5-len(five),
                ranking_version='MK-RANK-2.0-BASE-POTENTIAL')
