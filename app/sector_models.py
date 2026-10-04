"""Equity residual-income and SOTP/NAV math. Parameters are explicit scenarios.

Theory: Damodaran, Investment Valuation ch21 (financial services).
Uncalibrated defaults are research assumptions, never a certified fair value.
"""
import math


def finite(v):
    return isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)


def residual_income(book, roe, ke, retention, terminal_growth, years=5):
    if not all(finite(v) for v in (book,roe,ke,retention,terminal_growth)):
        raise ValueError('Artık kâr modelinde sonlu girdiler gerekli.')
    if book<=0 or not 0<=retention<=1 or ke<=terminal_growth or ke<=0 or not isinstance(years,int) or not 1<=years<=30:
        raise ValueError('Özkaynak/iskonto/büyüme/ufuk koşulları geçersiz.')
    if terminal_growth<0 or terminal_growth>=ke:
        raise ValueError('Sonsuz büyüme özkaynak maliyetinin altında olmalı.')
    # ROE fades to ke: no permanent excess-profit premium by assumption.
    pv=0.;b=book;path=[]
    for year in range(1,years+1):
        r=roe+(ke-roe)*((year-1)/years)
        income=b*r;excess=income-ke*b
        pv+=excess/(1+ke)**year
        dividend=max(income,0)*(1-retention)
        next_book=b+income-dividend
        if not all(finite(v) for v in (income,excess,pv,dividend,next_book)):
            raise ValueError('Artık kâr hesabında taşma.')
        if next_book<=0:
            raise ValueError('Tahmini özkaynak negatife düşüyor; yeniden sermayelendirme senaryosu gerekli.')
        path.append(dict(year=year,opening_book=b,roe=r,income=income,dividend=dividend,
                         residual_income=excess,closing_book=next_book))
        b=next_book
    # Terminal ROE equals ke, so terminal residual income is zero. A nonzero
    # terminal premium would require a separately supported sustainable ROE.
    return dict(equity_value=book+pv,book=book,pv_residual_income=pv,path=path,
        terminal_roe=ke,terminal_residual_income=0.,terminal_growth=terminal_growth,
        terminal_growth_note='No terminal excess return; growth cannot create a premium without excess ROE.')


def financial_bands(fin):
    if fin.input_error or fin.basis not in ('ANNUAL','TTM') or fin.currency!='TRY':
        raise ValueError(fin.input_error or 'Finans sektörü için doğrulanmış yıllık/TTM TRY girdileri gerekli.')
    f=fin.financials
    book,profit,shares=[f.get(k) for k in ('equity_parent_current','profit_parent_current','shares')]
    if not all(finite(v) for v in (book,profit,shares)) or book<=0 or shares<=0:
        raise ValueError('Ana ortaklık özkaynağı, kârı ve pozitif pay sayısı gerekli.')
    # End-book ROE is an explicit proxy, not average-period ROE or regulatory ROE.
    roe=profit/book
    factors=[e.get('factor') for e in fin.alignment.values() if isinstance(e,dict) and finite(e.get('factor'))]
    real_unit=any(abs(v-1)>0.005 for v in factors)
    assumptions={'bear':(.25 if real_unit else .50,.25),
                 'base':(.18 if real_unit else .40,.50),
                 'bull':(.12 if real_unit else .30,.75)}
    bands={}
    for name,(ke,retention) in assumptions.items():
        valuation=residual_income(book,roe,ke,retention,0.)
        bands[name]=dict(price=valuation['equity_value']/shares,equity_value=valuation['equity_value'],
            profit_proxy=profit,observed_end_book_roe=roe,ke_assumption=ke,retention_assumption=retention,
            money_basis='REPORTED_REMEASURED_UNIT' if real_unit else 'REPORTED_UNIT_NO_MATERIAL_REMEASUREMENT',
            detail=valuation,reason='RESIDUAL_INCOME_SCENARIO_UNCALIBRATED')
    # Parameter axes can cross for below-cost ROE. Labels denote assumptions,
    # not promised price ordering; the adapter supplies an ordered price_range.
    return bands


def asset_sotp(segments, holding_cash, holding_debt, other_liabilities, taxes, shares):
    if not segments or not all(finite(v) for v in (holding_cash,holding_debt,other_liabilities,taxes,shares)):
        raise ValueError('SOTP için ayrı şirket varlık/borç/nakit/vergi ve pay girdileri gerekli.')
    if shares<=0 or min(holding_cash,holding_debt,other_liabilities,taxes)<0:
        raise ValueError('SOTP işaret/pay koşulları geçersiz.')
    total=0.
    for segment in segments:
        value,ownership=segment.get('equity_value'),segment.get('ownership')
        if not finite(value) or not finite(ownership) or value<0 or not 0<=ownership<=1 or not segment.get('source'):
            raise ValueError('Her segmentte kaynaklı özkaynak değeri ve sahiplik payı gerekli.')
        total+=value*ownership
    equity=total+holding_cash-holding_debt-other_liabilities-taxes
    return dict(equity_value=equity,price=equity/shares,
        reason='SEGMENT_EQUITY_VALUES_PLUS_PARENT_CASH_MINUS_PARENT_LIABILITIES')


def appraised_nav(fair_assets, liabilities, nci, shares):
    if not fair_assets or not all(finite(v) for v in (liabilities,nci,shares)) or liabilities<0 or shares<=0:
        raise ValueError('NAV için kaynaklı varlıklar, borçlar, NCI ve pay sayısı gerekli.')
    for asset in fair_assets:
        if not asset.get('source') or not asset.get('fair_value_verified') or not finite(asset.get('value')) or asset['value']<0:
            raise ValueError('Defter değeri doğrulanmış piyasa değerinin yerine geçirilemez.')
    equity=sum(a['value'] for a in fair_assets)-liabilities-nci
    return dict(equity_value=equity,price=equity/shares,reason='VERIFIED_ASSET_NAV')
