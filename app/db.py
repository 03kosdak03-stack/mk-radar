"""Append-only Final1 snapshots; old legacy snapshot table is left intact."""
from datetime import datetime
from pathlib import Path
import hashlib
import json
import sqlite3
import math
from contextlib import closing
from .config import settings


class Store:
    def __init__(self, path=None):
        self.path = Path(path or settings.database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS final1_runs (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS final1_snapshots (id INTEGER PRIMARY KEY, ticker TEXT NOT NULL, created_at TEXT NOT NULL, fingerprint TEXT NOT NULL UNIQUE, payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS final1_filing_snapshots (id INTEGER PRIMARY KEY, ticker TEXT NOT NULL, period_end TEXT NOT NULL, fingerprint TEXT NOT NULL UNIQUE, payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS target_versions (id INTEGER PRIMARY KEY, ticker TEXT NOT NULL, period_end TEXT NOT NULL, fingerprint TEXT NOT NULL UNIQUE, payload TEXT NOT NULL)')

    def connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def save_snapshot(self, row):
        payload = json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False)
        key = hashlib.sha256(payload.encode()).hexdigest()
        with closing(self.connect()) as db, db:
            db.execute('INSERT OR IGNORE INTO final1_snapshots(ticker,created_at,fingerprint,payload) VALUES(?,?,?,?)',
                       (row['ticker'], datetime.now().isoformat(), key, payload))
            if self.valid_target(row):
                identity = {k: row.get(k) for k in ('ticker', 'model_version', 'period_end', 'basis')}
                identity['source_urls'] = sorted(row['source_urls'])
                filing_key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
                # Keep the first successfully calculated target for each filing set.
                db.execute('INSERT OR IGNORE INTO final1_filing_snapshots(ticker,period_end,fingerprint,payload) VALUES(?,?,?,?)',
                           (row['ticker'], row['period_end'], filing_key, payload))
                version_key=self.target_key(row)
                db.execute('INSERT OR IGNORE INTO target_versions(ticker,period_end,fingerprint,payload) VALUES(?,?,?,?)',
                           (row['ticker'],row['period_end'],version_key,payload))

    @staticmethod
    def valid_target(row):
        bands=row.get('bands',{})
        valid=(row.get('status') in ('HESAPLANDI','FIYAT_EKSIK') and bool(row.get('source_urls'))
            and bool(row.get('period_end')) and set(bands)=={'bear','base','bull'} and
            all(isinstance(b.get('price'),(int,float)) and not isinstance(b['price'],bool) and math.isfinite(b['price']) for b in bands.values()))
        if not valid:return False
        base=bands['base']['price'];fair=row.get('fair_value')
        return fair is None if base<=0 else (isinstance(fair,(int,float)) and not isinstance(fair,bool)
            and math.isfinite(fair) and math.isclose(fair,base,rel_tol=1e-10,abs_tol=1e-10))

    @staticmethod
    def target_key(row):
        identity={k:row.get(k) for k in ('ticker','model_version','period_end','basis','bands')}
        identity['source_urls']=sorted(row.get('source_urls',[]))
        return hashlib.sha256(json.dumps(identity,sort_keys=True,allow_nan=False).encode()).hexdigest()

    def target_history(self,ticker,limit=50):
        from .financials import parse_date
        with closing(self.connect()) as db:
            versions=db.execute('SELECT payload FROM target_versions WHERE ticker=? ORDER BY period_end DESC,id DESC LIMIT ?', (ticker,limit)).fetchall()
            legacy=db.execute('SELECT payload FROM final1_filing_snapshots WHERE ticker=? ORDER BY period_end DESC,id DESC LIMIT ?', (ticker,limit)).fetchall()
        result=[];seen=set()
        for record in versions+legacy:
            row=json.loads(record[0])
            if not self.valid_target(row):continue
            key=self.target_key(row)
            if key not in seen:result.append(row);seen.add(key)
        def order(row):
            try:pub=parse_date(row.get('published_at'))
            except ValueError:pub=datetime.min
            return row.get('period_end',''),pub
        return sorted(result,key=order,reverse=True)[:limit]

    def target_context(self,row):
        from .financials import parse_date
        history=self.target_history(row['ticker'])
        cutoff=row.get('calculated_at')
        if cutoff:
            decision=datetime.fromisoformat(cutoff).replace(tzinfo=None)
            history=[r for r in history if r.get('published_at') and parse_date(r['published_at'])<=decision]
        history=[r for r in history if r.get('model_version')==row.get('model_version') and
                 (not row.get('period_end') or r['period_end']<=row['period_end'])]
        def compact(r):
            return {k:r.get(k) for k in ('period_end','published_at','fair_value','model_version','basis','source_urls','shares','shares_as_of')}
        previous=next((r for r in history if r['period_end']<row.get('period_end','')),None)
        last=next((r for r in history if isinstance(r.get('fair_value'),(int,float)) and r['fair_value']>0),None)
        current=self.valid_target(row) and isinstance(row.get('fair_value'),(int,float)) and row['fair_value']>0
        comparable=bool(current and previous and previous.get('fair_value') and row.get('shares') and previous.get('shares')==row['shares'])
        return dict(current_target_available=current,previous_filing_target=compact(previous) if previous else None,
            last_valid_target=compact(last) if last else None,previous_target_comparable=comparable,
            target_change_pct=(row['fair_value']/previous['fair_value']-1)*100 if comparable and previous.get('fair_value') else None,
            retained_target_is_current=False if not current else None)

    def save_run(self, run):
        with closing(self.connect()) as db, db:
            db.execute('INSERT INTO final1_runs(created_at,payload) VALUES(?,?)', (run['as_of'], json.dumps(run, ensure_ascii=False, allow_nan=False)))

    def latest_run(self):
        with closing(self.connect()) as db, db:
            row = db.execute('SELECT payload FROM final1_runs ORDER BY id DESC LIMIT 1').fetchone()
        return json.loads(row[0]) if row else None

    def history(self, ticker, limit=5):
        with closing(self.connect()) as db, db:
            rows = db.execute('SELECT payload FROM final1_filing_snapshots WHERE ticker=? ORDER BY period_end DESC,id DESC LIMIT ?', (ticker, limit)).fetchall()
            if not rows:
                rows = db.execute('SELECT payload FROM final1_snapshots WHERE ticker=? ORDER BY id DESC LIMIT ?', (ticker, limit)).fetchall()
        return [json.loads(r[0]) for r in rows]


def init_db():
    return Store()
