"""Historical development-sample replay. This is NOT independent validation."""
import argparse
from datetime import datetime
import gzip
import hashlib
import json
from pathlib import Path
from statistics import median
from .final_engine import forecast

ROOT = Path(__file__).resolve().parents[1]


def replay_row(row, method='forward'):
    decision = row['decision']
    if datetime.strptime(row['published'],'%d.%m.%Y %H:%M:%S') > datetime.fromisoformat(decision):
        raise ValueError('Karar tarihinden sonraki finansal rapor.')
    if not row['source'].startswith('https://www.kap.org.tr/tr/Bildirim/'):
        raise ValueError('Tarihsel KAP kaynağı gerekli.')
    past = [b for b in row['bars'] if b['date'] <= decision[:10]]
    future = [b for b in row['bars'] if row['entry_date'] < b['date'] <= row['deadline']]
    entry_bar = next(b for b in row['bars'] if b['date']==row['entry_date'])
    end_bar = next(b for b in row['bars'] if b['date']==row['deadline'])
    if len(past)<20 or len(future)<200:
        raise ValueError('Fiyat penceresi eksik.')
    factor = row['historical_price_unit_factor']
    price, entry = past[-1]['close']*factor, entry_bar['close']*factor
    f,x = row['financials'],row['extra']
    bands = {k:forecast(f,x,g,p) for k,g,p in [('bear',.25,12),('base',.5,16),('bull',1.,20)]}
    if any(b['price'] is None for b in bands.values()):
        raise ValueError('Tarihsel model girdisi eksik.')
    if method != 'forward':
        ni,np,cfo=f.get('profit_parent_current'),f.get('profit_parent_prior'),f.get('cfo_current')
        if any(v is None for v in (ni,np,cfo)):
            raise ValueError('Ortak kontrol örneklemi için net kâr ve nakit girdileri eksik.')
        if method=='reported_pe':proxy=ni
        elif method=='smooth_pe':proxy=min(ni,(ni+np)/2)
        elif method=='cash_capped_pe':
            core=(x['gross_current']+x['opex_current']+min(bands['base']['finance_net'],0))*.75*bands['base']['attribution']
            proxy=min(ni,cfo*bands['base']['attribution'],core)
        else:raise ValueError('Bilinmeyen karşılaştırma yöntemi.')
        bands={k:dict(price=pe*proxy/f['shares'],profit_proxy=proxy,
                reason='RESEARCH_CONTROL_NOT_VERIFIED_FAIR_VALUE',method=method)
                for k,pe in [('bear',12),('base',16),('bull',20)]}
    if not all(b['volume'] is not None for b in past[-20:]):
        raise ValueError('20 günlük hacim verisi eksik.')
    target = bands['base']['price']
    # Target-touch fill retains the historical convention and is optimistic.
    hit = next((b for b in future if target>0 and b['high']*factor>=target),None)
    exit_price = max(target,hit['open']*factor) if hit else end_bar['close']*factor
    # Stress scenario requires a closing confirmation then a next-day open;
    # no future high is used to determine that execution price.
    confirmed = next((i for i,b in enumerate(future)
        if target>0 and b['close']*factor>=target and i+1<len(future)),None)
    stress_price = future[confirmed+1]['open']*factor if confirmed is not None else end_bar['close']*factor
    return dict(ticker=row['ticker'], published=row['published'], source=row['source'],
        decision_price=price, entry_price=entry, bands=bands,
        forward_yield=bands['base']['profit_proxy']/(price*f['shares']),
        turnover20=median(b['close']*b['volume'] for b in past[-20:]),
        buy_hold_net_multiple=end_bar['close']*factor/entry*.999/1.001,
        net_multiple=exit_price/entry*.999/1.001,
        stress_net_multiple=stress_price/entry*.994/1.006,
        exit_date=hit['date'] if hit else row['deadline'],
        exit_reason='TARGET_TOUCH_OPTIMISTIC' if hit else 'DEADLINE',
        peak_multiple=max(b['high'] for b in future)*factor/entry)


def run(bundle, method='forward'):
    periods=[]
    for year, inputs in bundle['cohorts'].items():
        rows=[]; errors=[]
        for row in inputs:
            try: rows.append(replay_row(row,method))
            except (ValueError,KeyError,StopIteration) as exc:
                errors.append(dict(ticker=row['ticker'],reason=str(exc)))
        ranked=sorted(rows,key=lambda r:(-r['forward_yield'],r['ticker']))
        top15=ranked[:15]
        five=[r for r in top15 if r['turnover20']>=5_000_000][:5]
        periods.append(dict(year=year,eligible=len(rows),top15=[r['ticker'] for r in top15],
            portfolio5=[r['ticker'] for r in five],
            portfolio_net_multiple=(sum(r['net_multiple'] for r in five)+5-len(five))/5,
            equal_sample_buy_hold_net_multiple=sum(r['buy_hold_net_multiple'] for r in rows)/len(rows) if rows else None,
            stress_net_multiple=(sum(r['stress_net_multiple'] for r in five)+5-len(five))/5,
            rows=rows,errors=errors))
    result = dict(independent_validation=False, final=False,method=method,
        engine_sha256=hashlib.sha256((ROOT/'app/final_engine.py').read_bytes()).hexdigest(),
        periods=periods, limitations=bundle['limitations'])
    if len(periods)==1:
        b=bundle.get('benchmarks',{}).get(periods[0]['year'])
        if b:
            if b['start']<=0 or b['end']<=0:raise ValueError('Geçersiz endeks fiyatı.')
            result['benchmark']=dict(b,price_multiple=b['end']/b['start'],
                same_cost_net_multiple=b['end']/b['start']*.999/1.001)
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--input',default=str(ROOT/'validation/historical_inputs.json.gz'))
    parser.add_argument('--output',default='reports/backtest_replay.json')
    args=parser.parse_args()
    with gzip.open(args.input,'rt',encoding='utf-8') as handle: bundle=json.load(handle)
    result=run(bundle)
    target=Path(args.output);target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    for period in result['periods']:
        print(period['year'],period['eligible'],period['portfolio5'],
              round((period['portfolio_net_multiple']-1)*100,2),
              'alternatif gerçekleşme:',round((period['stress_net_multiple']-1)*100,2))
    print('Bağımsız test değil. Rapor:',target.resolve())


if __name__=='__main__':
    main()
