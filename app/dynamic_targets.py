"""Quarterly target refresh on a fixed selected portfolio, without reselection.

An update is usable only from the next complete trading session. Sold names
stay sold, invalid/missing quarterly valuations retain the last valid target.
"""
from dataclasses import asdict
from datetime import datetime,timedelta
import gzip,json
from pathlib import Path
from .financials import FinancialProvider,parse_date
from .valuation import value_from_kap
from .wide_backtest import ROOT,number


def replay_dynamic(row,record,events,entry_date,deadline,execution='target_touch',cost=.001):
    if execution not in ('target_touch','closing_confirmation') or not 0<=cost<1:
        raise ValueError('Gerçekleşme kuralı/maliyet geçersiz.')
    for event in events:
        if event['valid']:
            if not number(event.get('target_decision_share_unit')) or event['target_decision_share_unit']<=0:
                raise ValueError('Geçerli hedef için pozitif sonlu fiyat gerekli.')
            snap=event.get('snapshot')
            if snap and parse_date(snap['published_at'])>datetime.fromisoformat(event['published_at']):
                raise ValueError('Hedef güncellemesinde gelecekteki rapor kullanılmış.')
    bars=record['bars'];entry=next(b for b in bars if b['date']==entry_date)
    if any(not b.get('ohlc_valid',True) for b in bars if entry_date<=b['date']<=deadline):
        raise ValueError('Gelecek OHLC eksik/tutarsız; pozisyon geriye dönük değiştirilemez.')
    target=row['fair_value'];updates=[];exit_bar=None;exit_price=None;reason=None
    applied=set()
    if target is not None and target<=entry['close']:
        return dict(ticker=row['ticker'],entry_price=entry['close'],exit_date=entry_date,exit_price=entry['close'],
            net_return_pct=((1-cost)/(1+cost)-1)*100,exit_reason='TARGET_ALREADY_MET_AT_ENTRY',
            updates_before_exit=[],unapplied_or_after_exit_events=events,final_target=target,
            validation='CONDITIONAL_PROVIDER_PRICES_NOT_FULL_CORPORATE_ACTION_AUDIT')
    pending_exit=False
    for bar in [b for b in bars if entry_date<b['date']<=deadline]:
        if pending_exit:
            exit_bar,exit_price,reason=bar,bar['open'],'NEXT_OPEN_AFTER_CLOSING_CONFIRMATION';break
        eligible=[(i,e) for i,e in enumerate(events) if i not in applied and e['published_at'][:10]<bar['date']]
        for i,e in eligible:
            applied.add(i)
            if e['valid']:
                target=e['target_decision_share_unit']
            updates.append(dict(e,applied_on=bar['date'],retained_target=target))
        if execution=='closing_confirmation':
            if target is not None and bar['close']>=target:pending_exit=True
            continue
        if target is not None and target<=bar['open']:
            exit_bar,exit_price,reason=bar,bar['open'],'OPEN_AT_OR_ABOVE_CURRENT_TARGET';break
        if target is not None and bar['high']>=target:
            exit_bar,exit_price,reason=bar,target,'INTRADAY_TARGET_TOUCH_OPTIMISTIC';break
    if exit_bar is None:
        exit_bar=next(b for b in bars if b['date']==deadline);exit_price=exit_bar['close'];reason='DEADLINE'
    return dict(ticker=row['ticker'],entry_price=entry['close'],exit_date=exit_bar['date'],exit_price=exit_price,
        net_return_pct=(exit_price/entry['close']*(1-cost)/(1+cost)-1)*100,exit_reason=reason,
        updates_before_exit=updates,unapplied_or_after_exit_events=[e for i,e in enumerate(events) if i not in applied],
        final_target=target,validation='CONDITIONAL_PROVIDER_PRICES_NOT_FULL_CORPORATE_ACTION_AUDIT')


def acquire_events(provider,row,record,decision,deadline):
    ticker=row['ticker'];raw_events=[]
    for _,name in set(provider.index.get(ticker,[])):
        snap=provider._parse(ticker,name)
        if snap.published_at:
            pub=parse_date(snap.published_at)
            if decision<pub<=deadline:raw_events.append((pub,name))
    events=[]
    for pub,name in sorted(set(raw_events)):
        snap=provider.latest(ticker,pub+timedelta(seconds=1))
        valued=value_from_kap(snap)
        # Each new target is in that balance's share unit. Re-express in the
        # original decision unit only for recognized provider split events.
        ratio=1.
        for a in record['actions']:
            if decision.date().isoformat()<a['date']<=snap.shares_as_of:ratio*=a['ratio']
        valid=valued['status']=='FIYAT_EKSIK' and number(valued.get('fair_value'))
        events.append(dict(published_at=pub.isoformat(),period=snap.period,valid=valid,
            target_decision_share_unit=valued['fair_value']*ratio if valid else None,
            reason=valued.get('reason'),source_urls=snap.source_urls,snapshot=asdict(snap)))
    return events


def main():
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--archives');args=p.parse_args()
    with gzip.open(ROOT/'validation/wide_2025_inputs.json.gz','rt',encoding='utf-8') as f:bundle=json.load(f)
    result=json.loads((ROOT/'validation/wide_2025_result.json').read_text(encoding='utf-8'))
    inputs=ROOT/'validation/dynamic_2025_inputs.json.gz'
    if args.archives:
        provider=FinancialProvider(archive_dir=args.archives)
        events={row['ticker']:acquire_events(provider,row,next(r for r in bundle['records'] if r['snapshot']['ticker']==row['ticker']),
            datetime.fromisoformat(bundle['decision']),datetime.fromisoformat(bundle['deadline'])+timedelta(hours=23,minutes=59)) for row in result['portfolio5']}
        with gzip.open(inputs,'wt',encoding='utf-8') as f:json.dump(events,f,ensure_ascii=False,allow_nan=False)
    else:
        with gzip.open(inputs,'rt',encoding='utf-8') as f:events=json.load(f)
    rows=[];alternate_rows=[]
    for row in result['portfolio5']:
        record=next(r for r in bundle['records'] if r['snapshot']['ticker']==row['ticker'])
        rows.append(replay_dynamic(row,record,events[row['ticker']],bundle['entry_date'],bundle['deadline']))
        alternate_rows.append(replay_dynamic(row,record,events[row['ticker']],bundle['entry_date'],bundle['deadline'],
                                             execution='closing_confirmation',cost=.006))
    report=dict(final=False,independent_validation=False,initial_selection=[r['ticker'] for r in result['portfolio5']],rows=rows,
        conditional_portfolio_net_return_pct=sum(r['net_return_pct'] for r in rows)/5,
        execution_variant_portfolio_pct=sum(r['net_return_pct'] for r in alternate_rows)/5,
        execution_variant_rows=alternate_rows,
        limitations=['Same preselected five, no replacement with retrospectively successful stocks.',
            'Next complete trading session after publication; daily high cannot be used before publication.',
            'Position closes at the active target; later financials do not reopen or raise a target after sale.',
            'Invalid/missing valuation retains old target; number of failed updates is reported.',
            'Nonpositive base earnings forecast cannot certify a negative company value; last positive target retained.',
            'Provider action archive, all dilutive placements and per-share unit consistency are not independently audited.',
            'This is development data and target-touch filling is optimistic; dividends and reinvestment excluded.'])
    (ROOT/'validation/dynamic_2025_result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print('Dinamik koşullu portföy',report['conditional_portfolio_net_return_pct'])
    for r in rows:print(r['ticker'],r['exit_date'],r['net_return_pct'],[(u['period'],u['valid'],u['retained_target']) for u in r['updates_before_exit']])

if __name__=='__main__':main()
