"""Predeclared simple controls on shared inputs; no per-year winning switch."""
import gzip
import json
from pathlib import Path
from .replay import ROOT, run


def main():
    cohorts={}
    for name in ['historical_inputs.json.gz','new_period_10_inputs.json.gz']:
        with gzip.open(ROOT/'validation'/name,'rt',encoding='utf-8') as f:
            cohorts.update(json.load(f)['cohorts'])
    # Same completeness requirements for every method, including baseline.
    shared={year:[r for r in rows if all(r['financials'].get(k) is not None
        for k in ('profit_parent_current','profit_parent_prior','cfo_current'))]
        for year,rows in cohorts.items()}
    results=[]
    for method in ('forward','reported_pe','smooth_pe','cash_capped_pe'):
        replay=run(dict(cohorts=shared,limitations=[]),method)
        for p in replay['periods']:
            results.append(dict(year=p['year'],method=method,eligible=p['eligible'],
                portfolio=p['portfolio5'],net_return_pct=(p['portfolio_net_multiple']-1)*100,
                execution_variant_return_pct=(p['stress_net_multiple']-1)*100))
    report=dict(final=False,independent_validation=False,shared_input_universe=True,
        methods=dict(forward='Frozen forward operating earnings ×16',
            reported_pe='Reported parent net income ×16',
            smooth_pe='min(current parent income, two-period average parent income) ×16',
            cash_capped_pe='min(parent income, attributed CFO, after-tax core earnings proxy) ×16'),
        limitations=['Same 10-large-company convenience sample for 2025; not 500-market validation.',
            'No yearly best-method switch. No new method declared final based on this comparison.',
            'Cash flow cap is an earnings control, not FCFF or a full DCF.',
            'All methods use uncalibrated PE scenarios, target-touch filling and development-sample selection limits.'],
        results=results)
    target=ROOT/'validation/method_comparison.json'
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    for r in results:print(r['year'],r['method'],r['eligible'],round(r['net_return_pct'],2),r['portfolio'])


if __name__=='__main__':main()
