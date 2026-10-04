"""Console and Telegram use exactly the same Final1 service."""
import argparse
import json
from pathlib import Path
from .service import UpdateService


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='data/final_scan.json')
    args = parser.parse_args()
    run = UpdateService().scan()
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(run, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print('MK Araştırma / KAP 1.4 |', run['as_of'], '| Evren:', run['total'], '| Durum:', run['counts'])
    for i, row in enumerate(run['top15'], 1):
        base = row['fair_value']
        print(i, row['ticker'], 'Fiyat:', row['price'], 'Baz:', base, 'Potansiyel:', row['potential'])
    print('Beşli:', ', '.join(r['ticker'] for r in run['portfolio5']), '| Nakit yuva:', run['cash_slots'])
    print('Dosya:', target)


if __name__ == '__main__':
    main()
