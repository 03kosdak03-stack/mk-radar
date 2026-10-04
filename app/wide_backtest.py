"""Point-in-time selection, then separate outcome evaluation; no future-data exclusions.

Current surviving-code registry is a candidate list, NOT a historical universe.
Provider split normalization is conditional until every action is reconciled.
"""
import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta
from dataclasses import asdict
import gzip
import hashlib
import json
import math
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo
from .financials import FinancialProvider, FinancialSnapshot, parse_date
from .valuation import value_from_kap, select_universe, select_by_value

TZ=ZoneInfo('Europe/Istanbul')
ROOT=Path(__file__).resolve().parents[1]


def number(x):
    return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x)


def price_series(payload,ticker,decision_date=None):
    r=payload['chart']['result'][0]
    if r.get('meta',{}).get('currency')!='TRY' or r['meta'].get('symbol')!=ticker+'.IS':
        raise ValueError('Fiyat şirketi/para birimi eşleşmiyor.')
    q=r['indicators']['quote'][0];bars={}
    for i,t in enumerate(r['timestamp']):
        d=datetime.fromtimestamp(t,TZ).date().isoformat()
        vals={k:q.get(k,[])[i] if i<len(q.get(k,[])) else None for k in ('open','high','low','close','volume')}
        if not number(vals['close']) or vals['close']<=0:continue
        valid=all(number(vals[k]) and vals[k]>0 for k in ('open','high','low'))
        if valid:
            valid=vals['low']<=min(vals['open'],vals['close'])+1e-6 and vals['high']>=max(vals['open'],vals['close'])-1e-6
        if d in bars:
            if decision_date is None or d<=decision_date:raise ValueError('Karar öncesi tekrarlanan günlük fiyat.')
            valid=False
        bars[d]=dict(date=d,ohlc_valid=valid,**vals)
    actions=[]
    for a in r.get('events',{}).get('splits',{}).values():
        n,de=a.get('numerator'),a.get('denominator')
        if not number(n) or not number(de) or n<=0 or de<=0:raise ValueError('Sermaye işlemi katsayısı geçersiz.')
        actions.append(dict(date=datetime.fromtimestamp(a['date'],TZ).date().isoformat(),ratio=n/de))
    return sorted(bars.values(),key=lambda b:b['date']),sorted(actions,key=lambda a:a['date'])


def normalize_bars(bars,actions,decision_date):
    # Re-express split-adjusted provider data in the decision-date share unit.
    # Future events change representation only; they cannot change selection
    # eligibility or any forward earnings feature. Rights issues remain provisional.
    factor=math.prod(a['ratio'] for a in actions if a['date']>decision_date)
    if not number(factor) or factor<=0:raise ValueError('Fiyat birimi katsayısı geçersiz.')
    return [dict(b,**{k:b[k]*factor if number(b[k]) else None for k in ('open','high','low','close')},
                 volume=b['volume']/factor if number(b.get('volume')) else None) for b in bars],factor


def select_record(fin,bars,actions,decision,verified_actions):
    if parse_date(fin.published_at)>decision:raise ValueError('Gelecekte yayımlanan bilanço.')
    for u in fin.source_urls:
        if not u.startswith('https://www.kap.org.tr/tr/Bildirim/'):
            raise ValueError('KAP rapor kaynağı gerekli.')
    past=[b for b in bars if b['date']<=decision.date().isoformat()]
    if len(past)<20:raise ValueError('Karar öncesi 20 fiyat günü yok.')
    last=past[-1]
    if (decision.date()-datetime.fromisoformat(last['date']).date()).days>7:
        raise ValueError('Karar fiyatı yedi günden eski.')
    f=deepcopy(fin)
    for a in actions:
        if fin.shares_as_of<a['date']<=decision.date().isoformat():
            evidence=next((v for v in verified_actions if v['date']==a['date'] and abs(v['ratio']-a['ratio'])<1e-6),None)
            if not evidence or evidence.get('type')!='PURE_BONUS' or parse_date(evidence['published_at'])>decision:
                raise ValueError('Karar öncesi sermaye işlemi kaynağı/pay sayısı doğrulanmadı.')
            f.financials['shares']*=a['ratio'];f.shares=f.financials['shares']
    row=value_from_kap(f,last['close'])
    if row['status']!='HESAPLANDI':raise ValueError(row.get('reason') or row['status'])
    volumes=past[-20:]
    row['turnover20']=median(b['close']*b['volume'] for b in volumes) if all(number(b['volume']) and b['volume']>=0 for b in volumes) else None
    row.update(decision_price_date=last['date'],company_type=fin.company_type)
    if row.get('turnover20') is not None:
        row['liquidity_basis']='PROVIDER_SPLIT_ADJUSTED_CLOSE_X_VOLUME_NOT_EXCHANGE_RECONCILED'
    return row


def evaluate_outcome(row,bars,actions,entry_date,deadline,verified_actions,cost=.001):
    entry=next((b for b in bars if b['date']==entry_date),None)
    last=next((b for b in bars if b['date']==deadline),None)
    if entry is None or last is None:return dict(ticker=row['ticker'],status='OUTCOME_MISSING',reason='Alış/bitiş günü fiyatı yok.')
    future=[b for b in bars if entry_date<b['date']<=deadline]
    if any(not b.get('ohlc_valid',True) for b in [entry]+future):
        return dict(ticker=row['ticker'],status='OUTCOME_MISSING',reason='Gelecek OHLC doğrulanmadı; seçilmiş şirket listeden çıkarılmadı.')
    if len(future)<200:return dict(ticker=row['ticker'],status='OUTCOME_MISSING',reason='Bir yıllık günlük fiyat penceresi eksik.')
    unverified=[a for a in actions if entry_date<=a['date']<=deadline and not any(
        v['date']==a['date'] and abs(v['ratio']-a['ratio'])<1e-6 and v.get('type')=='PURE_BONUS' for v in verified_actions)]
    target=row['fair_value']
    immediate=target is not None and target<=entry['close']
    hit=next((b for b in future if target is not None and b['high']>=target),None) if not immediate else None
    sold=entry['close'] if immediate else max(target,hit['open']) if hit else last['close']
    confirmed=next((i for i,b in enumerate(future) if target is not None and b['close']>=target and i+1<len(future)),None)
    alternate=entry['close'] if immediate else future[confirmed+1]['open'] if confirmed is not None else last['close']
    peak=max(b['high'] for b in [entry]+future)/entry['close']
    multiple=sold/entry['close']*(1-cost)/(1+cost)
    return dict(ticker=row['ticker'],status='CONDITIONAL_UNVERIFIED_ACTION' if unverified else 'PROVIDER_SERIES_ONLY',
        entry_price=entry['close'],target=target,entry_date=entry_date,deadline=deadline,
        exit_price=sold,exit_date=entry_date if immediate else hit['date'] if hit else deadline,
        exit_reason='TARGET_ALREADY_MET_AT_ENTRY' if immediate else 'TARGET_TOUCH_OPTIMISTIC' if hit else 'DEADLINE',
        net_multiple=multiple,net_return_pct=(multiple-1)*100,
        execution_variant_multiple=alternate/entry['close']*.994/1.006,
        buy_hold_net_multiple=last['close']/entry['close']*(1-cost)/(1+cost),
        peak_multiple=peak,unverified_actions=unverified)


def evaluate_bundle(bundle,method='forward',weights=None,ranking='base_potential'):
    decision=datetime.fromisoformat(bundle['decision'])
    decisions=[];selection_errors=[];outcomes=[];record_by_ticker={}
    # Phase 1: no test-period price window or completion requirement is inspected.
    for record in bundle['records']:
        ticker=record['snapshot']['ticker']
        try:
            fin=FinancialSnapshot(**record['snapshot'])
            row=select_record(fin,record['bars'],record['actions'],decision,record.get('verified_actions',[]))
            if method in ('reported_pe','smooth_pe','cash_capped_pe'):
                f,x=fin.financials,fin.extra
                ni,previous,cfo=f.get('profit_parent_current'),f.get('profit_parent_prior'),f.get('cfo_current')
                if not all(number(v) for v in (ni,previous,cfo)):raise ValueError('Kontrol kâr/nakit girdileri eksik.')
                if method=='reported_pe':proxy=ni
                elif method=='smooth_pe':proxy=min(ni,(ni+previous)/2)
                else:
                    base=row['bands']['base']
                    core=(x['gross_current']+x['opex_current']+min(base['finance_net'],0))*.75*base['attribution']
                    proxy=min(ni,cfo*base['attribution'],core)
                # Control cohort is normal companies with no pre-decision
                # capital action unless explicitly reconciled in select_record.
                shares=fin.financials['shares']
                for a in record['actions']:
                    if fin.shares_as_of<a['date']<=decision.date().isoformat():shares*=a['ratio']
                row['bands']={k:dict(price=pe*proxy/shares,profit_proxy=proxy,reason='UNCALIBRATED_CONTROL')
                    for k,pe in [('bear',12),('base',16),('bull',20)]}
                row.update(fair_value=16*proxy/shares if proxy>0 else None,
                    potential=(16*proxy/shares/row['price']-1)*100 if proxy>0 else None,
                    forward_yield=proxy/(row['price']*shares),model_version=method)
            elif method not in ('forward','weighted_forward'):raise ValueError('Bilinmeyen yöntem.')
            if method=='weighted_forward':
                f,x=fin.financials,fin.extra
                features=dict(yield_=row['potential'],real_revenue_growth=f['revenue_current']/f['revenue_prior']-1,
                    gross_margin=x['gross_current']/f['revenue_current'],cash_to_assets=f['cfo_current']/f['assets_current'])
                features['yield']=features.pop('yield_')
                if row['fair_value'] is not None and not all(number(features.get(k)) for k in weights):raise ValueError('Ağırlıklı özellik eksik.')
                row['rank_features']=features
            decisions.append(row);record_by_ticker[ticker]=record
        except (ValueError,TypeError,KeyError) as exc:selection_errors.append(dict(ticker=ticker,reason=str(exc)))
    chosen=select_universe(decisions) if ranking=='legacy_yield' else select_by_value(decisions)
    if method=='weighted_forward':
        eligible=chosen['ranked'];n=len(eligible)
        for row in eligible:
            row['weighted_score']=sum(w*sum(other['rank_features'][k]<row['rank_features'][k] for other in eligible)/max(n-1,1)
                                     for k,w in weights.items())
        ranked=sorted(eligible,key=lambda r:(-r['weighted_score'],r['ticker']))
        top15=ranked[:15];five=[r for r in top15 if r['potential']>0 and number(r.get('turnover20')) and r['turnover20']>=5e6][:5]
        chosen=dict(ranked=ranked,top15=top15,portfolio5=five,cash_slots=5-len(five))
    # Phase 2: keep selected names even if an outcome/rights audit later fails.
    for row in decisions:
        record=record_by_ticker[row['ticker']]
        outcomes.append(evaluate_outcome(row,record['bars'],record['actions'],bundle['entry_date'],bundle['deadline'],record.get('verified_actions',[])))
    by={r['ticker']:r for r in outcomes};five=[by[r['ticker']] for r in chosen['portfolio5']]
    complete=all(r.get('net_multiple') is not None for r in five)
    market_outcomes=[]
    for record in bundle.get('market_records',[]):
        market_outcomes.append(evaluate_outcome(dict(ticker=record['ticker'],fair_value=None),record['bars'],record['actions'],
            bundle['entry_date'],bundle['deadline'],record.get('verified_actions',[])))
    ranked_winners=sorted((r for r in market_outcomes if r.get('peak_multiple') is not None),key=lambda r:(-r['peak_multiple'],r['ticker']))[:10]
    top={r['ticker'] for r in chosen['top15']};portfolio={r['ticker'] for r in chosen['portfolio5']}
    ranks={r['ticker']:i+1 for i,r in enumerate(chosen['ranked'])}
    decision_by={r['ticker']:r for r in decisions}
    def leader_reason(ticker):
        row=decision_by.get(ticker)
        if row and (not number(row.get('fair_value')) or row['fair_value']<=0):
            return 'Model pozitif baz değer üretmedi; negatif kâr tahmini şirketin negatif değerli olduğunu kanıtlamaz.'
        return next((e['reason'] for e in selection_errors+bundle['acquisition_errors'] if e['ticker']==ticker),None)
    eligible_outcomes=[r for r in outcomes if r.get('buy_hold_net_multiple') is not None]
    return dict(final=False,independent_validation=False,method=method,ranking=ranking,decision=bundle['decision'],
        entry_date=bundle['entry_date'],deadline=bundle['deadline'],candidate_count=bundle['candidate_count'],
        financial_counts=bundle['financial_counts'],input_records=len(bundle['records']),eligible=len(decisions),
        positive_valued_ranking_count=len(chosen['ranked']),
        eligible_models=dict(Counter(r['model_version'] for r in decisions)),
        top15=chosen['top15'],portfolio5=chosen['portfolio5'],cash_slots=chosen['cash_slots'],
        portfolio_conditional_net_return_pct=((sum(r['net_multiple'] for r in five)+chosen['cash_slots'])/5-1)*100 if complete else None,
        portfolio_execution_variant_pct=((sum(r['execution_variant_multiple'] for r in five)+chosen['cash_slots'])/5-1)*100 if complete else None,
        portfolio_outcomes=five,complete_portfolio_outcomes=complete,
        portfolio_provider_actions_reconciled=complete and all(not r.get('unverified_actions') for r in five),
        portfolio_corporate_action_audit_complete=False,
        equal_eligible_sample_buy_hold_pct=(sum(r['buy_hold_net_multiple'] for r in eligible_outcomes)/len(eligible_outcomes)-1)*100 if eligible_outcomes else None,
        observed_market_outcomes=len([r for r in market_outcomes if r.get('peak_multiple') is not None]),
        leaders=[dict(r,selection_rank=ranks.get(r['ticker']),in_top15=r['ticker'] in top,in_portfolio5=r['ticker'] in portfolio,
            decision_base_value=decision_by.get(r['ticker'],{}).get('fair_value'),
            decision_base_potential=decision_by.get(r['ticker'],{}).get('potential'),
            decision_turnover20=decision_by.get(r['ticker'],{}).get('turnover20'),
            selection_missing_reason=leader_reason(r['ticker'])) for r in ranked_winners],
        leaders_captured_top15=sum(r['ticker'] in top for r in ranked_winners),
        leaders_captured_five=sum(r['ticker'] in portfolio for r in ranked_winners),
        outcomes=outcomes,selection_errors=selection_errors,acquisition_errors=bundle['acquisition_errors'],
        benchmark=bundle.get('benchmark'),
        limitations=bundle['limitations'],engine_sha256=hashlib.sha256((ROOT/'app/final_engine.py').read_bytes()).hexdigest())


def acquire(args):
    from concurrent.futures import ThreadPoolExecutor
    decision=datetime.fromisoformat(args.decision)
    provider=FinancialProvider(zip_path=args.financial_zip or ROOT/'FinancialTable.zip',archive_dir=args.archives,universe_path=args.universe)
    # Current registry only supplies candidate names, never future sector inputs.
    provider.universe={t:dict(company=r.get('company','')) for t,r in provider.universe.items()}
    raw=Path(args.prices)
    window_start=(decision-timedelta(days=100)).date().isoformat()
    evidence=json.loads(Path(args.actions).read_text()) if args.actions else {}
    financial_rows=[];records=[];errors=[]
    def one(ticker):
        try:
            fin=provider.latest(ticker,decision)
            value=value_from_kap(fin)
        except (ValueError,KeyError,OSError) as exc:return dict(ticker=ticker,status='VERI_EKSIK',reason=str(exc)),None,dict(ticker=ticker,reason=str(exc))
        summary=dict(ticker=ticker,status=value['status'],reason=value.get('reason'),period=fin.period,basis=fin.basis,source_urls=fin.source_urls)
        if value['status']!='FIYAT_EKSIK':return summary,None,dict(ticker=ticker,reason=value.get('reason') or value['status'])
        file=raw/(ticker+'.json')
        if not file.exists():return summary,None,dict(ticker=ticker,reason='Fiyat dosyası yok.')
        try:
            body=file.read_bytes();bars,actions=price_series(json.loads(body),ticker,decision.date().isoformat())
            bars,factor=normalize_bars(bars,actions,decision.date().isoformat())
            snap={k:v for k,v in asdict(fin).items() if k not in ('comparison','line_items')}
            rec=dict(snapshot=snap,bars=[b for b in bars if window_start<=b['date']<=args.deadline],actions=actions,
                verified_actions=evidence.get(ticker,[]),provider_sha256=hashlib.sha256(body).hexdigest(),provider_price_unit_factor=factor)
            return summary,rec,None
        except (ValueError,KeyError,TypeError,OSError) as exc:return summary,None,dict(ticker=ticker,reason=str(exc))
    tickers=provider.tickers()
    market_records=[]
    for ticker in tickers:
        file=raw/(ticker+'.json')
        if not file.exists():continue
        try:
            bars,actions=price_series(json.loads(file.read_bytes()),ticker,decision.date().isoformat())
            bars,factor=normalize_bars(bars,actions,decision.date().isoformat())
            market_records.append(dict(ticker=ticker,bars=[b for b in bars if window_start<=b['date']<=args.deadline],
                actions=actions,verified_actions=evidence.get(ticker,[])))
        except (ValueError,KeyError,TypeError,OSError):pass
    with ThreadPoolExecutor(max_workers=4) as pool:
        for i,(summary,record,error) in enumerate(pool.map(one,tickers),1):
            financial_rows.append(summary)
            if record:records.append(record)
            if error:errors.append(error)
            if i%50==0:print('Finansal tarama',i,'/',len(tickers),'fiyat girdisi',len(records),flush=True)
    origin=('CURRENT_REGISTRY' if args.universe else 'FINANCIAL_ARCHIVE_CODES_NOT_VERIFIED_TRADABLE_UNIVERSE')
    bundle=dict(decision=args.decision,entry_date=args.entry_date,deadline=args.deadline,candidate_origin=origin,
        candidate_count=len(tickers),financial_counts=dict(Counter(r['status'] for r in financial_rows)),
        records=records,market_records=market_records,acquisition_errors=errors,financial_rows=financial_rows,
        limitations=[('Candidate names from current registry; historical constituent/IPO/delisting completeness not verified.' if args.universe else
            'Candidate names from supplied financial archive, including issuer codes and joint aliases; historical tradable-security universe not verified.'),
            'Latest accessible annual/interim reports as of decision; sources from current bulk exports may omit superseded originals.',
            'Latest accessible reports used. No future outcome completeness or future actions used to exclude selected names.',
            'Stock price files are a retrospective convenience subset; 500 priced valuations not achieved.',
            'Split-adjusted provider price units normalized mechanically; volume and all rights/other actions not independently exchange-reconciled.',
            'Unknown in-window corporate actions make outcomes conditional; selected names are not dropped or replaced.',
            'Retrospective study; model and price subset previously inspected, not an unseen holdout.',
            'Targets fixed for one year; this test does not implement quarterly target refresh, dividends or reinvestment.',
            'Normal operating and financial residual-income methods are not calibrated on a common risk-adjusted valuation scale.'])
    return bundle


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--archives');parser.add_argument('--universe');parser.add_argument('--prices');parser.add_argument('--actions')
    parser.add_argument('--financial-zip')
    parser.add_argument('--decision',default='2025-05-15T18:30:00')
    parser.add_argument('--entry-date',default='2025-05-16');parser.add_argument('--deadline',default='2026-05-15')
    parser.add_argument('--input',default=str(ROOT/'validation/wide_2025_inputs.json.gz'))
    parser.add_argument('--output',default=str(ROOT/'validation/wide_2025_result.json'))
    args=parser.parse_args();target=Path(args.input)
    if args.archives or args.financial_zip:
        bundle=acquire(args);target.parent.mkdir(parents=True,exist_ok=True)
        with gzip.open(target,'wt',encoding='utf-8') as f:json.dump(bundle,f,ensure_ascii=False,allow_nan=False)
    else:
        with gzip.open(target,'rt',encoding='utf-8') as f:bundle=json.load(f)
    result=evaluate_bundle(bundle)
    output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print('Kodlar',result['candidate_count'],'finansal',result['financial_counts'],'fiyatlı seçim',result['eligible'])
    print('İlk15',[r['ticker'] for r in result['top15']]);print('Beşli',[r['ticker'] for r in result['portfolio5']])
    print('Koşullu net',result['portfolio_conditional_net_return_pct'],'alternatif',result['portfolio_execution_variant_pct'])
    print('Örneklem ilk10 zirve yakalama',result['leaders_captured_top15'],result['leaders_captured_five'])

if __name__=='__main__':main()
