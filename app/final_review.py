"""Evidence review; a successful software test is not investment validation."""
from collections import Counter
from pathlib import Path
import argparse, gzip, hashlib, json
from .wide_backtest import ROOT, number
from .dynamic_targets import replay_dynamic


def coverage_summary(rows):
    unique={r['ticker']:r for r in rows}
    scenarios=[r for r in unique.values() if r.get('bands') and all(
        number(b.get('price')) for b in r['bands'].values())]
    positive=[r for r in scenarios if number(r.get('fair_value')) and r['fair_value']>0]
    priced=[r for r in positive if r.get('status')=='HESAPLANDI' and number(r.get('price')) and r['price']>0]
    liquid=[r for r in priced if number(r.get('turnover20')) and r['turnover20']>=5e6]
    return dict(total_unique=len(unique),financial_scenarios=len(scenarios),positive_base_values=len(positive),
        priced_positive_values=len(priced),known_liquid_values=len(liquid),
        missing_to_500_priced_values=max(0,500-len(priced)),investment_final=False,
        reason='500 fiyatlı değerleme ve bağımsız tüm-evren doğrulaması henüz kanıtlanmadı.')


def equity_path(bundle,rows):
    """Equal initial weights; sold proceeds stay cash and are never reinvested."""
    records={r['snapshot']['ticker']:r for r in bundle['records']}
    lookups={r['ticker']:{b['date']:b['close'] for b in records[r['ticker']]['bars']} for r in rows}
    dates=sorted(set.intersection(*(set(v) for v in lookups.values()))) if rows else []
    dates=[d for d in dates if bundle['entry_date']<=d<=bundle['deadline']]
    peak=1.;worst=0.;path=[]
    for day in dates:
        value=(5-len(rows)+sum(1+r['net_return_pct']/100 if day>=r['exit_date']
            else lookups[r['ticker']][day]/r['entry_price']/1.001 for r in rows))/5
        peak=max(peak,value);worst=min(worst,value/peak-1)
        path.append(dict(date=day,equity_multiple=value))
    return dict(close_to_close_max_drawdown_pct=100*worst,path=path,
        basis='Provider closing prices; no dividends, no interest on cash, no reinvestment; intraday drawdown not measured.')


def review():
    coverage=json.loads((ROOT/'validation/financial_coverage.json').read_text(encoding='utf-8'))
    wide=json.loads((ROOT/'validation/wide_2025_result.json').read_text(encoding='utf-8'))
    old=json.loads((ROOT/'validation/wide_2020_result.json').read_text(encoding='utf-8'))
    dynamic=json.loads((ROOT/'validation/dynamic_2025_result.json').read_text(encoding='utf-8'))
    with gzip.open(ROOT/'validation/wide_2025_inputs.json.gz','rt',encoding='utf-8') as f:bundle=json.load(f)
    with gzip.open(ROOT/'validation/dynamic_2025_inputs.json.gz','rt',encoding='utf-8') as f:events=json.load(f)
    outcomes=dynamic['rows'];returns=[r['net_return_pct'] for r in outcomes]
    best=max(outcomes,key=lambda r:r['net_return_pct'])
    # Fixed original five, with one initial slot left as cash. No successful
    # replacement is chosen after looking at outcomes.
    without_best=(sum(returns)-best['net_return_pct'])/5
    sensitivities={}
    for band in ('bear','base','bull'):
        rows=[]
        for original in wide['portfolio5']:
            row=dict(original);row['fair_value']=row['bands'][band]['price']
            if not number(row['fair_value']) or row['fair_value']<=0:
                rows.append(dict(ticker=row['ticker'],net_return_pct=0.,policy='NONPOSITIVE_INITIAL_TARGET_CASH'));continue
            revised=[]
            for event in events[row['ticker']]:
                e=dict(event)
                if e['valid']:
                    from .financials import FinancialSnapshot
                    from .valuation import value_from_kap
                    value=value_from_kap(FinancialSnapshot(**e['snapshot']))
                    base=value['fair_value'];target=value.get('bands',{}).get(band,{}).get('price')
                    ratio=e['target_decision_share_unit']/base if number(base) and base>0 else None
                    e['valid']=number(target) and target>0 and number(ratio)
                    e['target_decision_share_unit']=target*ratio if e['valid'] else None
                revised.append(e)
            record=next(r for r in bundle['records'] if r['snapshot']['ticker']==row['ticker'])
            rows.append(replay_dynamic(row,record,revised,bundle['entry_date'],bundle['deadline']))
        sensitivities[band]=dict(conditional_return_pct=sum(r['net_return_pct'] for r in rows)/5,rows=rows)
    groups={}
    for row in coverage['rows']:
        status=row['status'];reason=row.get('reason','')
        if status=='AYRI_SEKTOR_MODELI':need='Tarihli segment sahipliği ve segment özkaynak değerleri (holding) veya ekspertizli varlık değeri/borç/NCI (GYO).'
        elif reason.startswith('TTM parasal'):need='Aynı kapsam ve parasal ölçümde yıllık + güncel ara dönem + önceki aynı ara dönem; çekirdek tutar farklarının dipnotta açıklaması.'
        elif reason.startswith('TTM kaynak'):need='Eksik yıllık veya önceki aynı ara dönem KAP raporu.'
        elif status=='FIYAT_EKSIK':need='Tarihli fiyat, 20 günlük işlem hacmi, pay/sermaye işlemi doğrulaması; şirket bazında model varsayımlarının kalibrasyonu.'
        else:need='Gerekçede belirtilen eksik çekirdek finansal girdinin tarihli KAP kaynağıyla doğrulanması.'
        groups.setdefault(status,[]).append(dict(ticker=row['ticker'],required=need,reason=reason,sources=row.get('source_urls',[])))
    return dict(investment_final=False,software_checkpoint='KAP-1.5-REVIEW',coverage=coverage_summary(coverage['rows']),
        coverage_as_of=coverage['as_of'],gap_groups=groups,
        period_checks=[dict(year=y,priced_values=r['eligible'],observed_prices=r['observed_market_outcomes'],
            leaders_in_top15=r['leaders_captured_top15'],leaders_in_five=r['leaders_captured_five'],
            static_conditional_return_pct=r['portfolio_conditional_net_return_pct'],independent_validation=False) for y,r in [('2020',old),('2025',wide)]],
        dynamic_2025_conditional_return_pct=dynamic['conditional_portfolio_net_return_pct'],
        dynamic_equity=equity_path(bundle,outcomes),
        winner_concentration=dict(largest_contributor=best['ticker'],contribution_percentage_points=best['net_return_pct']/5,
            original_portfolio_with_this_slot_cash_pct=without_best,policy='Same five initial slots; no hindsight replacement.'),
        scenario_sensitivity=sensitivities,historical_leader_diagnostics={'2020':old['leaders'],'2025':wide['leaders']},
        blockers=['500 şirket için kaynaklı, fiyatlı ve karşılaştırılabilir değerleme tamamlanmadı.',
            'En çok yükselen ilk10 içinden ilk15 ile 3–4 hisse yakalama hedefi bu örneklemlerde karşılanmadı.',
            '2025 dinamik sonuç tek şirkete yoğunlaşıyor; yeniden kullanılan geliştirme verisidir.',
            'Bağımsız tam evren, sektör kalibrasyonu ve eksiksiz sermaye işlemi doğrulaması yok.'],
        acquisition_failure='2023 yıllık KAP arşivini yeni dönem için indirme denemesi bu oturumda bağlantı zaman aşımına uğradı; veri uydurulmadı.',
        engine_sha256=hashlib.sha256((ROOT/'app/final_engine.py').read_bytes()).hexdigest())


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',default=str(ROOT/'validation/final_review.json'));args=p.parse_args()
    result=review();target=Path(args.output);target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('gap_groups','scenario_sensitivity','dynamic_equity','historical_leader_diagnostics')},ensure_ascii=False,indent=2))
    print('Senaryo duyarlılığı:',{k:v['conditional_return_pct'] for k,v in result['scenario_sensitivity'].items()})
    print('Kapanış bazlı maksimum düşüş:',result['dynamic_equity']['close_to_close_max_drawdown_pct'])

if __name__=='__main__':main()
