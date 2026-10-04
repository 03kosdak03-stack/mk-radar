"""Cross-sectional fundamental ranking. No timing signal or value-entry gate.

Forward prices are comparable scenario multiples, not calibrated fair values.
Expenses are extracted individually. Missing expense rows remain missing.
"""
import math

def normalized_income(gross, expenses):
    if gross is None or any(v is None for v in expenses):return None
    return gross+sum(expenses)  # Expense rows have signed negative values.

def forecast(f, extra, growth_scale, pe, expense_growth_scale=.5, margin_recovery_share=0.):
    rev,rp=f.get('revenue_current'),f.get('revenue_prior')
    gp=extra.get('gross_current');opex=extra.get('opex_current')
    if rev is None or rp is None or rev<=0 or rp<=0 or gp is None:
        return dict(price=None,reason='MISSING_REVENUE_OR_GROSS_PROFIT')
    if opex is None:return dict(price=None,reason='MISSING_CORE_EXPENSE_AGGREGATE')
    if opex>0:return dict(price=None,reason='UNEXPECTED_EXPENSE_SIGN')
    growth=max(-.20,min(1.0,(rev/rp-1)*growth_scale))
    expense_growth=max(-.1,growth*expense_growth_scale)
    current_margin=gp/rev;target_margin=current_margin
    reference=extra.get('pre_disruption_gross_margin')
    if margin_recovery_share and reference is not None and extra.get('recovery_branch_eligible'):
        target_margin=current_margin+max(reference-current_margin,0)*margin_recovery_share
    next_revenue=rev*(1+growth);next_gross=next_revenue*target_margin
    next_operating=next_gross+opex*(1+expense_growth)
    ni,total=f.get('profit_parent_current'),f.get('profit_total_current')
    # Attribution ratio is a proxy. Never divide zero or impute unobserved NCI.
    if total is not None and ni is not None and total!=0 and 0<=ni/total<=1:
        attribution=ni/total;attribution_basis='REPORTED_PROFIT_SHARE'
    elif f.get('equity_total_current') is not None and f.get('equity_parent_current')==f['equity_total_current']:
        attribution=1.;attribution_basis='EQUAL_PARENT_AND_TOTAL_EQUITY'
    elif f.get('equity_total_current') is not None and f['equity_total_current']>0 and f.get('equity_parent_current') is not None and 0<=f['equity_parent_current']/f['equity_total_current']<=1:
        attribution=f['equity_parent_current']/f['equity_total_current'];attribution_basis='EQUITY_SHARE_APPROXIMATION_NOT_PROFIT_ATTRIBUTION'
    else:return dict(price=None,reason='MISSING_OR_UNSTABLE_NCI_ATTRIBUTION')
    fi,fe=extra.get('finance_income_current'),extra.get('finance_expense_current')
    if fi is not None and fe is not None:financing=fi+fe
    elif extra.get('finance_net_current') is not None:financing=extra['finance_net_current']
    else:return dict(price=None,reason='MISSING_FINANCING_COMPONENT')
    # Finance gains cannot create an operating earnings premium. Retain losses.
    normalized_financing=min(financing,0)
    profit=(next_operating+normalized_financing)*.75*attribution
    shares=f.get('shares')
    if shares is None or shares<=0:return dict(price=None,reason='MISSING_SHARES')
    return dict(price=pe*profit/shares,profit_proxy=profit,revenue_growth_assumption=growth,
                next_revenue=next_revenue,gross_margin_assumption=target_margin,
                margin_recovery_share=margin_recovery_share,
                next_core_operating_profit=next_operating,finance_net=financing,
                financing_proxy=normalized_financing,attribution=attribution,
                attribution_basis=attribution_basis,pe_assumption=pe,
                reason='FORWARD_EARNINGS_SCENARIO_NOT_VERIFIED_FAIR_VALUE')


def rank(rows, weights):
    """Missing feature excludes score only, never turns the feature into zero."""
    usable=[r for r in rows if all(r['features'].get(k) is not None and math.isfinite(r['features'][k]) for k in weights)]
    n=len(usable);out=[]
    for r in usable:
        score=sum(w*sum(s['features'][k]<r['features'][k] for s in usable)/max(n-1,1) for k,w in weights.items())
        out.append(dict(r,score=score))
    return sorted(out,key=lambda r:(-r['score'],r['code']))
