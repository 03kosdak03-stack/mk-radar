"""Reproducible financial-only coverage audit; no quote requests or fake prices."""
import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo
from .financials import FinancialProvider
from .valuation import value_from_kap


def financial_audit(provider, as_of):
    rows = []
    for ticker in provider.tickers():
        try:
            snapshot = provider.latest(ticker, as_of)
            row = value_from_kap(snapshot)
            row['company_type'] = snapshot.company_type
        except (ValueError, KeyError, OSError) as exc:
            row = dict(ticker=ticker, status='VERI_EKSIK', reason=str(exc), bands={})
        rows.append(row)
    scenarios = sum(bool(r.get('bands')) and all(b.get('price') is not None
                    for b in r['bands'].values()) for r in rows)
    positive = sum(r.get('fair_value') is not None and r['fair_value'] > 0 for r in rows)
    model_counts=Counter(r.get('model_version','UNKNOWN') for r in rows
                        if r.get('status')=='FIYAT_EKSIK' and r.get('bands'))
    return dict(as_of=as_of.isoformat(), total=len(rows), scenario_count=scenarios,
        positive_base_count=positive, price_verified_count=0,
        eligible_model_counts=dict(model_counts),
        counts=dict(Counter(r['status'] for r in rows)), rows=rows,
        final=False, final_blockers=['En az 500 şirket için desteklenen değerleme henüz doğrulanmadı.'
            if scenarios < 500 else 'Tam sektör/kapsam doğrulaması gerekli.',
            'Bu denetim fiyat ve likidite doğrulaması içermez.',
            'Bağımsız yeni dönem ve tarihsel tüm evren testi tamamlanmadı.'])


def main():
    from .service import UpdateService
    parser = argparse.ArgumentParser()
    parser.add_argument('--as-of', default=datetime.now(ZoneInfo('Europe/Istanbul')).isoformat(timespec='seconds'))
    parser.add_argument('--output', default='reports/financial_coverage.json')
    args = parser.parse_args()
    decision=datetime.fromisoformat(args.as_of)
    if decision.tzinfo is not None:
        decision=decision.astimezone(ZoneInfo('Europe/Istanbul')).replace(tzinfo=None)
    result = financial_audit(UpdateService.new_financials(),decision)
    target = Path(args.output); target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='rows'},ensure_ascii=False,indent=2))
    print('Rapor:',target.resolve())


if __name__=='__main__':
    main()
