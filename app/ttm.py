"""TTM assembly only from amounts already verified in the same monetary unit.

A bare combination of separately inflation-restated reports is not accepted.
"""
from copy import deepcopy
from datetime import date
from .financials import parse_date
from dataclasses import asdict
import math
from statistics import median


def overlap_factor(new, old, kind):
    """Infer a common-unit factor ONLY when overlapping reported amounts agree.

    This is an arithmetic reconciliation, not independent CPI/audit validation.
    Nominal capital and its inflation adjustment are intentionally excluded:
    capital remains nominal while its adjustment absorbs the remeasurement.
    All other shared nonempty rows must reconcile; no outlier trimming.
    """
    nc, oc = new.comparison, old.comparison
    dates_n, dates_o = nc.get(kind+'_dates', []), oc.get(kind+'_dates', [])
    if len(dates_n) < 2 or not dates_o or dates_n[1] != dates_o[0]:
        raise ValueError('Örtüşen ' + kind + ' karşılaştırma tarihleri eşleşmiyor.')
    left, right = nc[kind+'_prior'], oc[kind+'_current']
    excluded = ('ÖDENMIŞ SERMAYE', 'SERMAYE DÜZELTME FARKLARI', 'PAY BAŞINA')
    pairs = [(k, left[k], right[k]) for k in sorted(left.keys() & right.keys())
             if not any(x in k for x in excluded) and left[k] is not None and right[k] is not None]
    ratios = [a/b for _, a, b in pairs if a and b and a*b > 0]
    if len(ratios) < 8:
        raise ValueError('Parasal eşleştirme için en az sekiz dolu karşılaştırma kalemi gerekli.')
    if new.company_type in ('bank','financial'):
        groups = ([['TOPLAM VARLIKLAR','VARLIKLAR TOPLAMI'],['TOPLAM ÖZKAYNAKLAR','ÖZKAYNAKLAR','ÖZSERMAYE TOPLAMI']]
            if kind=='balance' else [['DÖNEM KARI (ZARARI)','DÖNEM NET KARI VEYA ZARARI']])
        anchors = [next((label.split('|')[0] for label,a,b in pairs if label.split('|')[0] in group and a and b),'MISSING') for group in groups]
    else:
        anchors = ['TOPLAM VARLIKLAR', 'TOPLAM ÖZKAYNAKLAR'] if kind == 'balance' else ['HASILAT', 'BRÜT KAR (ZARAR)']
    if any(not any(label.split('|')[0] == k and a and b for label, a, b in pairs) for k in anchors):
        raise ValueError('Parasal eşleştirme ana kontrol kalemleri eksik.')
    factor = median(ratios)
    if not math.isfinite(factor) or factor <= 0:
        raise ValueError('Geçersiz parasal katsayı.')
    failures = []
    for label, a, b in pairs:
        # Amounts can be rounded to one presentation unit (including thousand TL).
        tolerance = max(2000.0, 2*new.presentation_scale, 2*old.presentation_scale*factor,
                        abs(a)*0.000005, abs(b*factor)*0.000005)
        if abs(a-b*factor) > tolerance:
            failures.append(label)
    if failures:
        raise ValueError('Parasal karşılaştırma uyuşmuyor: ' + ', '.join(failures[:3]))
    return factor, dict(factor=factor, checked_rows=len(pairs), anchors=anchors,
                       method='REPORTED_OVERLAP_RECONCILIATION', independent_cpi_verified=False)


def core_cpi_factor(new, old, kind, as_of):
    """Allow unrelated reclassifications only with CPI and model-input checks.

    This validates the monetary bridge and the inputs used by this model;
    it does not certify every note, operating scope or financial-statement row.
    """
    from .cpi import official_bridge
    factor, source = official_bridge(old.period_end, new.period_end, as_of)
    nc, oc = new.comparison, old.comparison
    if nc.get(kind+'_dates', [None, None])[1:] != oc.get(kind+'_dates', [])[:1]:
        raise ValueError('TÜFE köprüsünde karşılaştırma tarihleri eşleşmiyor.')
    left, right = nc[kind+'_prior'], oc[kind+'_current']
    pairs = [(k, left[k], right[k]) for k in left.keys() & right.keys()
             if left[k] is not None and right[k] is not None]
    def agrees(a, b):
        return (math.isfinite(a) and math.isfinite(b) and
                abs(a-b*factor) <= max(2000.,2*new.presentation_scale,2*old.presentation_scale*factor,
                                       abs(a)*.00005, abs(b*factor)*.00005))
    matching = [k for k,a,b in pairs if abs(a) >= 1_000_000 and agrees(a,b)]
    if len(matching) < 8:
        raise ValueError('TÜFE katsayısını doğrulayan en az sekiz maddi kalem gerekli.')
    labels = (('TOPLAM VARLIKLAR', 'TOPLAM ÖZKAYNAKLAR') if kind == 'balance' else
        ('HASILAT', 'BRÜT KAR (ZARAR)', 'GENEL YÖNETIM GIDERLERI', 'PAZARLAMA GIDERLERI',
         'PAZARLAMA, SATIŞ VE DAĞITIM GIDERLERI', 'ARAŞTIRMA VE GELIŞTIRME GIDERLERI',
         'ESAS FAALIYETLERDEN DIĞER GELIRLER', 'ESAS FAALIYETLERDEN DIĞER GIDERLER',
         'ESAS FAALIYET KARI (ZARARI)', 'FINANSMAN GELIRLERI', 'FINANSMAN GIDERLERI',
         'FINANSMAN GELIRI (GIDERI) ÖNCESI FAALIYET KARI (ZARARI)',
         'SÜRDÜRÜLEN FAALIYETLER VERGI ÖNCESI KARI (ZARARI)',
         'DÖNEM KARI (ZARARI)', 'ANA ORTAKLIK PAYLARI'))
    for title in labels:
        # First occurrence is the net-income row, as in the parser; later
        # comprehensive-income attribution is not the model's net profit.
        key = next((k for k in left if k.split('|')[0] == title), None)
        if key is not None and (key not in right or
            (left[key] is None) != (right[key] is None) or
            (left[key] is not None and not agrees(left[key],right[key]))):
            raise ValueError('TÜFE ile çekirdek rapor kalemi uyuşmuyor: '+title)
    if kind == 'balance':
        controls = [('financials', 'assets'), ('financials', 'equity_total')]
    else:
        controls = [('financials', k) for k in ('revenue','profit_parent','profit_total')]
        controls += [('extra', k) for k in ('gross','opex','finance_net')]
    for group, key in controls:
        a = getattr(new, group).get(key+'_prior')
        b = getattr(old, group).get(key+'_current')
        if a is None and b is None and key in ('profit_parent','profit_total'):
            continue  # Missing attribution remains missing for the engine to handle.
        if a is None or b is None or not agrees(a,b):
            raise ValueError('TÜFE ile çekirdek model girdisi uyuşmuyor: '+key)
    mismatches = sorted(k for k,a,b in pairs if not agrees(a,b))
    return factor, dict(factor=factor, checked_rows=len(pairs), matching_material_rows=len(matching),
        method='CPI_AND_CORE_INPUT_RECONCILIATION', independent_cpi_verified=True,
        cpi=source, reclassified_or_unmatched_rows=mismatches,
        scope='MODEL_INPUTS_ONLY_NOT_FULL_STATEMENT_AUDIT')


def reconciled_factor(new, old, kind, as_of):
    try:
        return overlap_factor(new, old, kind)
    except ValueError as original:
        try:
            if new.company_type in ('bank','financial'):
                return financial_core_factor(new,old,kind,as_of)
            return core_cpi_factor(new, old, kind, as_of)
        except (ValueError, KeyError, IndexError, TypeError) as fallback:
            raise ValueError(str(original)+'; TÜFE kontrolü: '+str(fallback)) from original


def financial_core_factor(new,old,kind,as_of):
    """Reported nominal bridge or dated CPI, with all RI input controls."""
    from .cpi import official_bridge
    n,o=new.comparison,old.comparison
    if n.get(kind+'_dates',[None,None])[1:]!=o.get(kind+'_dates',[])[:1]:
        raise ValueError('Finans sektörü karşılaştırma tarihleri eşleşmiyor.')
    if new.statement_type!=old.statement_type:
        raise ValueError('Konsolide/solo finansal kapsamlar birleştirilemez.')
    if kind=='balance':controls=('assets','equity_total','equity_parent')
    else:controls=('profit_parent','profit_total')
    factors=[(1.,None)]
    try:factors.append(official_bridge(old.period_end,new.period_end,as_of))
    except ValueError:pass
    left,right=n[kind+'_prior'],o[kind+'_current']
    pairs=[(k,left[k],right[k]) for k in left.keys()&right.keys() if left[k] is not None and right[k] is not None]
    for factor,cpi in factors:
        def match(a,b):
            return math.isfinite(a) and math.isfinite(b) and abs(a-b*factor)<=max(2000.,
                2*new.presentation_scale,2*old.presentation_scale*factor,abs(a)*.00005)
        matched=[k for k,a,b in pairs if abs(a)>=max(1e6,100*new.presentation_scale) and match(a,b)]
        if len(matched)<8:continue
        if any(new.financials.get(k+'_prior') is None or old.financials.get(k+'_current') is None or
               not match(new.financials[k+'_prior'],old.financials[k+'_current']) for k in controls):continue
        critical=(('VARLIKLAR TOPLAMI','TOPLAM VARLIKLAR','ÖZKAYNAKLAR','TOPLAM ÖZKAYNAKLAR','ÖZSERMAYE TOPLAMI')
            if kind=='balance' else ('DÖNEM NET KARI VEYA ZARARI','DÖNEM KARI (ZARARI)','ANA ORTAKLIK PAYLARI'))
        if any(not match(a,b) for k,a,b in pairs if k.split('|')[0] in critical):continue
        return factor,dict(factor=factor,method='FINANCIAL_CORE_RECONCILIATION',
            checked_rows=len(pairs),matching_material_rows=len(matched),independent_cpi_verified=cpi is not None,
            cpi=cpi,reclassified_or_unmatched_rows=sorted(k for k,a,b in pairs if not match(a,b)),
            scope='RI_INPUTS_ONLY_NOT_FULL_STATEMENT_OR_REGULATORY_AUDIT')
    raise ValueError('Finans sektörü özkaynak/kâr/para birimi kontrolleri uyuşmuyor.')


def assemble_snapshots(annual, latest, prior, as_of):
    """Bridge the latest YTD to the frozen engine without six-month doubling."""
    from .financials import FinancialSnapshot
    if any(snap.company_type!=latest.company_type for snap in (annual,prior)):
        raise ValueError('TTM şirket türleri/kapsamları eşleşmiyor.')
    annual_factor, annual_evidence = reconciled_factor(latest, annual, 'balance', as_of)
    prior_factor, prior_evidence = reconciled_factor(latest, prior, 'income', as_of)
    target = ('TRY_REPORTED_COMPARABLE_UNIT_' if latest.company_type in ('bank','financial') else
              'TRY_REPORTED_PURCHASING_POWER_') + latest.period_end
    records = []
    flow_f = ('revenue', 'profit_parent', 'profit_total', 'cfo')
    flow_e = ('gross', 'opex', 'finance_income', 'finance_expense', 'finance_net')
    for snap, factor in [(annual, annual_factor), (latest, 1.0), (prior, prior_factor)]:
        if snap.input_error or snap.company_type not in ('normal','bank','financial') or not snap.source_urls:
            raise ValueError('TTM kaynak raporu/şirket türü/para birimi doğrulanamadı.')
        r = asdict(snap)
        r.update(money_basis=target, comparable_money_basis_verified=True,
                 sources=[dict(url=u, published_at=snap.published_at) for u in snap.source_urls])
        for group, prefixes in [('financials', flow_f), ('extra', flow_e)]:
            for prefix in prefixes:
                for suffix in ('current', 'prior'):
                    key = prefix+'_'+suffix
                    value = r[group].get(key)
                    if value is not None:
                        r[group][key] = value*factor
        records.append(r)
    r = build_ttm(*records, target, as_of)
    r['warnings'] = [w for w in latest.warnings if not w.startswith('Ara dönem yıllıklaştırılmadı')]
    r['warnings'].append('TTM: raporlar ortak parasal ölçüme getirildi; dayanaklar alignment kaydında. Bu tam dipnot denetimi değildir.')
    if any(e['independent_cpi_verified'] for e in (annual_evidence, prior_evidence)):
        r['warnings'].append('TÜFE köprüsü: resmi tarihli katsayı ve çekirdek model girdileri doğrulandı. Diğer yeniden sınıflanan kalemler tam dipnot/kapsam denetiminden geçmedi; alignment kaydında listelendi.')
    r['warnings'].append('Ara dönem ×2 yapılmadı; yıllık + güncel ara dönem − karşılaştırmalı ara dönem kullanıldı.')
    r['alignment'] = dict(annual=annual_evidence, prior_ytd=prior_evidence, money_basis=target)
    r['basis'] = 'TTM'
    r['file_name'] = 'RECONCILED_TTM'
    return FinancialSnapshot(**{k:v for k,v in r.items() if k in FinancialSnapshot.__dataclass_fields__})


def build_ttm(annual, latest_ytd, prior_ytd, monetary_basis, as_of):
    """Records: FY(t-1), YTD(t), YTD(t-1), each with current/prior columns.

    Caller must first restate all flows to the same money basis and attest it in
    each record. This function never guesses CPI factors or missing zeros.
    """
    records = [annual, latest_ytd, prior_ytd]
    if not monetary_basis or any(r.get('money_basis') != monetary_basis or not r.get('comparable_money_basis_verified') for r in records):
        raise ValueError('TTM akımları ortak parasal ölçüme doğrulanmadan birleştirilemez.')
    if len({r['ticker'] for r in records}) != 1 or any(r['currency'] != 'TRY' for r in records):
        raise ValueError('Şirket veya para birimi eşleşmiyor.')
    a, c, p = [date.fromisoformat(r['period_end']) for r in records]
    if annual['basis'] != 'ANNUAL' or any(r['basis'] != 'YTD' for r in [latest_ytd, prior_ytd]) or (a.month, a.day) != (12, 31) or a.year != c.year-1 or p.year != c.year-1 or (p.month, p.day) != (c.month, c.day):
        raise ValueError('Yıllık/ara dönem tarihleri eşleşmiyor.')
    sources = []
    for r in records:
        for source in r['sources']:
            if parse_date(source['published_at']) > as_of:
                raise ValueError('Karar tarihinden sonra yayımlanan rapor kullanılamaz.')
            if source not in sources:
                sources.append(source)
    out = deepcopy(latest_ytd)
    out.update(basis='TTM', published_at=max((s['published_at'] for s in sources), key=parse_date),
               source_urls=list(dict.fromkeys(s['url'] for s in sources)), sources=sources)
    # Balance figures and current shares are never annualized.
    for group, prefixes in [('financials', ('revenue', 'profit_parent', 'profit_total', 'cfo')),
                            ('extra', ('gross', 'opex', 'finance_income', 'finance_expense', 'finance_net'))]:
        for prefix in prefixes:
            def combine(values):
                return None if any(v is None for v in values) else values[0]+values[1]-values[2]
            ar, cr, pr = [r[group] for r in records]
            out[group][prefix+'_current'] = combine([ar.get(prefix+'_current'), cr.get(prefix+'_current'), cr.get(prefix+'_prior')])
            out[group][prefix+'_prior'] = combine([ar.get(prefix+'_prior'), pr.get(prefix+'_current'), pr.get(prefix+'_prior')])
    out.setdefault('warnings', []).append('TTM: üç rapor, ortak parasal ölçüm doğrulaması ile birleştirildi.')
    return out
