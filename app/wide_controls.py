"""Predefined method controls on exactly the same latest-report input subset.
Development comparison, not a best-year-switch or independent validation.
"""
from copy import deepcopy
import gzip
import json
from pathlib import Path
from .wide_backtest import ROOT,evaluate_bundle,number,select_record
from .financials import FinancialSnapshot
from datetime import datetime

WEIGHTS={'yield':.50,'real_revenue_growth':.20,'gross_margin':.15,'cash_to_assets':.15}


def compare(bundle):
    shared=[]
    for record in bundle['records']:
        snap=record['snapshot'];f,x=snap['financials'],snap['extra']
        if snap['company_type']!='normal':continue
        if not all(number(f.get(k)) for k in ('profit_parent_current','profit_parent_prior','cfo_current','assets_current','revenue_current','revenue_prior')):continue
        if f['assets_current']<=0 or f['revenue_current']<=0 or f['revenue_prior']<=0 or not number(x.get('gross_current')):continue
        shared.append(record)
    reports=[]
    for method in ['forward','reported_pe','smooth_pe','cash_capped_pe','weighted_forward']:
        b=deepcopy(bundle);b['records']=shared
        # evaluate_bundle receives the predeclared method and builds all ranks
        # before inspecting any realized outcome or winner list.
        reports.append(evaluate_bundle(b,method=method,weights=WEIGHTS))
    return dict(final=False,independent_validation=False,shared_input_records=len(shared),
        weights=WEIGHTS,period=bundle['decision'],reports=reports,
        limitations=['Weights fixed for this development comparison, not searched for the best realized result.',
                     'Only common normal-company records with all control features; banks excluded from this comparison.',
                     'Ranking weights change the order; weighted_forward retains exactly the frozen operating price targets.',
                     'All prices and action/liquidity limitations of the wide test apply.'])


def main():
    with gzip.open(ROOT/'validation/wide_2025_inputs.json.gz','rt',encoding='utf-8') as f:b=json.load(f)
    r=compare(b)
    (ROOT/'validation/wide_2025_controls.json').write_text(json.dumps(r,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    for p in r['reports']:
        print(p['method'],p['eligible'],[x['ticker'] for x in p['portfolio5']],p['portfolio_conditional_net_return_pct'],p['leaders_captured_top15'])

if __name__=='__main__':main()
