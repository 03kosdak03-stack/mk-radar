"""KAP HTML/XLS table adapter. No text-neighbour matching or half-year doubling."""
from dataclasses import dataclass, field
from datetime import datetime, date
from functools import lru_cache
from pathlib import Path
import json
import math
import re
import zipfile
from lxml import html

ROOT = Path(__file__).resolve().parents[1]
ZIP_PATH = ROOT / 'FinancialTable.zip'


def to_number(value):
    if value is None:
        return None
    s = str(value).strip().replace('\u00a0', '').replace(' ', '')
    if s in ('', '-', '—'):
        return None
    negative = s.startswith('(') and s.endswith(')')
    if negative:
        s = s[1:-1]
    if not re.fullmatch(r'-?\d+(?:\.\d{3})*(?:,\d+)?', s):
        return None
    v = float(s.replace('.', '').replace(',', '.'))
    return -v if negative else v


def canonical(s):
    return ' '.join(s.upper().replace('İ', 'I').replace('ı', 'I').split())


def parse_date(s):
    for fmt in ('%d.%m.%Y %H:%M:%S', '%Y.%m.%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d', '%d.%m.%Y'):
        try:
            return datetime.strptime(s, fmt)
        except (ValueError, TypeError):
            pass
    raise ValueError('Tarih biçimi geçersiz')


@dataclass
class FinancialSnapshot:
    ticker: str
    file_name: str
    period: str | None = None
    published_at: str | None = None
    period_end: str | None = None
    basis: str = 'YTD'
    currency: str | None = None
    company_type: str = 'normal'
    company: str = ''
    revenue: float | None = None
    ebitda: float | None = None
    net_income_parent: float | None = None
    cash: float | None = None
    debt: float | None = None
    net_debt: float | None = None
    equity_parent: float | None = None
    equity_total: float | None = None
    paid_capital: float | None = None
    shares: float | None = None
    shares_as_of: str | None = None
    operating_profit: float | None = None
    depreciation: float | None = None
    financials: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    source_urls: list = field(default_factory=list)
    input_error: str | None = None
    comparison: dict = field(default_factory=dict)
    alignment: dict = field(default_factory=dict)
    statement_type: str = 'UNKNOWN'
    line_items: dict = field(default_factory=dict)
    presentation_scale: float = 1.0


def classify(title):
    t = canonical(title)
    if 'BANKASI' in t or 'BANK A.' in t:
        return 'bank'
    if 'HOLDING' in t or 'YATIRIM HOLD' in t:
        return 'holding'
    if 'GAYRIMENKUL YATIRIM ORTAKLI' in t:
        return 'gyo'
    if re.search(r'\bMENKUL\b', t) or any(x in t for x in ('SIGORTA', 'EMEKLILIK', 'YATIRIM ORTAKLI', 'VARLIK YÖNETIM', 'FINANSAL KIRALAMA', 'FAKTORING', 'FAKTÖRING', 'FINANSMAN A.')):
        return 'financial'
    return 'normal'


def parse_report(ticker, file_name, raw, nominal_share_value=1.0):
    soup = html.fromstring(raw)
    node_text = lambda node: ' '.join(' '.join(node.itertext()).split())
    text = node_text(soup)
    headings = soup.xpath('//h1')
    company = node_text(headings[0]) if headings else ticker
    publication = re.search(r'Gönderim Tarihi\s*:\s*(\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}:\d{2})', text)
    currency = None
    scale = None
    statement_types = set()
    for row in soup.xpath('//table[contains(concat(" ", @class, " "), " financial-header-table ")]//tr'):
        cells = row.findall('td')
        if len(cells)>=2 and canonical(node_text(cells[0]))=='FINANSAL TABLO NITELIĞI':
            statement_types.add(canonical(node_text(cells[1])))
        if len(cells) >= 2 and canonical(node_text(cells[0])) == 'SUNUM PARA BIRIMI':
            unit = canonical(node_text(cells[1]))
            if unit in ('TL', 'TRY'):
                currency, scale = 'TRY', 1
            elif unit in ('BIN TL', '1.000 TL', '1000 TL'):
                currency, scale = 'TRY', 1000
            elif unit in ('MILYON TL', '1.000.000 TL'):
                currency, scale = 'TRY', 1000000
    tables = []
    table_errors = {}
    for table in soup.xpath('//table[contains(concat(" ", @class, " "), " financial-table ")]'):
        header_nodes = table.xpath('.//td[contains(concat(" ", @class, " "), " context-header ")]')
        headers = [node_text(c) for c in header_nodes]
        dated = [c for c in header_nodes if re.search(r'\d{2}\.\d{2}\.\d{4}',node_text(c))]
        leaves = [canonical(node_text(c)) for c in header_nodes if c not in dated]
        dimensional = [int(c.get('colspan','1')) for c in dated]
        total_columns = ([2,5] if dimensional==[3,3] and leaves==['TP','YP','TOPLAM']*2 else None)
        errors = []
        if any(n>1 for n in dimensional) and total_columns is None:
            errors.append('Boyutlu tablo sütunları doğrulanamadı.')
        if total_columns is not None:
            headers = [node_text(c) for c in dated]
        values = {}
        unique_rows = {}
        for row in table.iter('tr'):
            cells = row.findall('td')
            label = next((c for c in cells if 'taxonomy-field-title' in c.get('class', '').split()), None)
            amount_cells = [c for c in cells if 'taxonomy-context-value' in c.get('class', '').split()]
            if label is None or not amount_cells:
                continue
            key = canonical(node_text(label))
            amounts = [to_number(node_text(c)) for c in amount_cells]
            amounts = [x * scale if x is not None and scale is not None else None for x in amounts]
            if total_columns is not None:
                if len(amounts)!=6:
                    errors.append('TP/YP/Toplam sütun sayısı uyuşmuyor.')
                    amounts=[None,None]
                else:
                    for j in (0,3):
                        a,b,c=amounts[j:j+3]
                        if all(v is not None for v in (a,b,c)) and abs(a+b-c)>max(2*(scale or 1),abs(c)*1e-6):
                            errors.append('TP + YP toplamı uyuşmuyor: '+key)
                    amounts=[amounts[i] for i in total_columns]
            role = next((x for x in row.get('class', '').split() if re.fullmatch(r'[a-z_]+_role_\d+-row-\d+', x)), '')
            if role:
                unique_rows[key + '|' + role] = amounts
            # The first profit-share row is net profit; later comprehensive profit is distinct.
            if key not in values or all(x is None for x in values[key]):
                values[key] = amounts
        tables.append((headers, values, unique_rows))
        table_errors[id(tables[-1])] = errors
    balance = next((t for t in tables if 'ÖDENMIŞ SERMAYE' in t[1]), ([], {}, {}))
    income = next((t for t in tables if t[0] and len(re.findall(r'\d{2}\.\d{2}\.\d{4}',t[0][0]))==2
                   and any(k in t[1] for k in ('HASILAT','FAIZ GELIRLERI','DÖNEM NET KARI VEYA ZARARI'))), ([], {}, {}))
    cashflow = next((t for t in tables if any('NAKIT AKIŞLARI' in k and 'IŞLETME' in k for k in t[1])), ([], {}, {}))

    def amount(table, labels, column=0):
        for label in labels:
            a = table[1].get(canonical(label))
            if a is not None and len(a) > column and a[column] is not None:
                return a[column]
        return None

    def inc(labels, column=0):
        return amount(income, labels, column)

    end = None
    basis = 'UNKNOWN'
    if income[0]:
        dates = re.findall(r'\d{2}\.\d{2}\.\d{4}', income[0][0])
        if len(dates) == 2:
            end = parse_date(dates[1]).date().isoformat()
            basis = 'ANNUAL' if dates[0] == '01.01.' + dates[1][-4:] and dates[1].startswith('31.12.') else 'YTD'
            # Check the comparative income period rather than the balance comparator.
            prior_dates = re.findall(r'\d{2}\.\d{2}\.\d{4}', income[0][1]) if len(income[0]) > 1 else []
            if len(prior_dates) != 2 or prior_dates[0][:6] != dates[0][:6] or prior_dates[1][:6] != dates[1][:6] or int(prior_dates[1][-4:]) != int(dates[1][-4:]) - 1:
                basis = 'INCOMPARABLE_PERIODS'
    if end is None and balance[0]:
        dates = re.findall(r'\d{2}\.\d{2}\.\d{4}', balance[0][0])
        if dates:
            end = parse_date(dates[-1]).date().isoformat()
    f = {}
    extra = {}
    statement_type = next(iter(statement_types)) if len(statement_types)==1 else 'UNKNOWN'
    for col, suffix in ((0, 'current'), (1, 'prior')):
        f['revenue_' + suffix] = inc(['Hasılat'], col)
        f['profit_parent_' + suffix] = inc(['Ana Ortaklık Payları'], col)
        f['profit_total_' + suffix] = inc(['DÖNEM KARI (ZARARI)', 'DÖNEM NET KARI VEYA ZARARI'], col)
        f['equity_parent_' + suffix] = amount(balance, ['Ana Ortaklığa Ait Özkaynaklar'], col)
        f['equity_total_' + suffix] = amount(balance, ['TOPLAM ÖZKAYNAKLAR','ÖZKAYNAKLAR','ÖZSERMAYE TOPLAMI'], col)
        f['assets_' + suffix] = amount(balance, ['TOPLAM VARLIKLAR','VARLIKLAR TOPLAMI'], col)
        nci=amount(balance,['Kontrol Gücü Olmayan Paylar'],col)
        f['nci_'+suffix]=nci
        if f['equity_parent_'+suffix] is None and nci is not None and f['equity_total_'+suffix] is not None:
            f['equity_parent_'+suffix]=f['equity_total_'+suffix]-nci
        if statement_type=='KONSOLIDE OLMAYAN':
            if f['equity_parent_'+suffix] is None:f['equity_parent_'+suffix]=f['equity_total_'+suffix]
            if f['profit_parent_'+suffix] is None:f['profit_parent_'+suffix]=f['profit_total_'+suffix]
        for key, labels in {
            'gross': ['BRÜT KAR (ZARAR)'],
            'admin': ['Genel Yönetim Giderleri'],
            'marketing': ['Pazarlama Giderleri', 'Pazarlama, Satış ve Dağıtım Giderleri'],
            'rd': ['Araştırma ve Geliştirme Giderleri'],
            'other_income': ['Esas Faaliyetlerden Diğer Gelirler'],
            'other_expense': ['Esas Faaliyetlerden Diğer Giderler'],
            'finance_income': ['Finansman Gelirleri'],
            'finance_expense': ['Finansman Giderleri'],
            'before_finance': ['FİNANSMAN GELİRİ (GİDERİ) ÖNCESİ FAALİYET KARI (ZARARI)'],
            'pretax': ['SÜRDÜRÜLEN FAALİYETLER VERGİ ÖNCESİ KARI (ZARARI)'],
        }.items():
            extra[key + '_' + suffix] = inc(labels, col)
        expenses = [extra[k + '_' + suffix] for k in ('admin', 'marketing', 'rd')]
        ebit = inc(['ESAS FAALİYET KARI (ZARARI)'], col)
        if all(x is not None for x in expenses):
            extra['opex_' + suffix] = sum(expenses)
        elif all(x is not None for x in (ebit, extra['other_income_' + suffix], extra['other_expense_' + suffix], extra['gross_' + suffix])):
            core = ebit - extra['other_income_' + suffix] - extra['other_expense_' + suffix]
            extra['opex_' + suffix] = core - extra['gross_' + suffix]
        else:
            extra['opex_' + suffix] = None
        fi, fe = extra['finance_income_' + suffix], extra['finance_expense_' + suffix]
        if fi is not None and fe is not None:
            extra['finance_net_' + suffix] = fi + fe
        elif extra['pretax_' + suffix] is not None and extra['before_finance_' + suffix] is not None:
            extra['finance_net_' + suffix] = extra['pretax_' + suffix] - extra['before_finance_' + suffix]
        else:
            extra['finance_net_' + suffix] = None
    paid = amount(balance, ['Ödenmiş Sermaye'])
    shares = paid / nominal_share_value if paid is not None and nominal_share_value > 0 else None
    f['shares'] = shares
    f['cfo_current'] = amount(cashflow, ['İŞLETME FAALİYETLERİNDEN NAKİT AKIŞLARI', 'İşletme Faaliyetlerinden Nakit Akışları'])
    f['cfo_prior'] = amount(cashflow, ['İŞLETME FAALİYETLERİNDEN NAKİT AKIŞLARI', 'İşletme Faaliyetlerinden Nakit Akışları'], 1)
    source_id = re.search(r'_(\d+)_', Path(file_name).name)
    warnings = [f'Pay sayısı: ödenmiş sermaye / {nominal_share_value:g} TL nominal pay varsayımı.']
    if basis != 'ANNUAL':
        warnings.append('Ara dönem yıllıklaştırılmadı; doğrulanmış son 12 aylık veri gerekli.')
    if end and end >= '2023-12-31':
        warnings.append('TMS29: nominal büyüme ve sabit çarpan varsayımları; bağımsız kalibrasyon yok.')
    comparison = {}
    for kind, table in [('balance', balance), ('income', income)]:
        comparison[kind + '_dates'] = [re.findall(r'\d{2}\.\d{2}\.\d{4}', h) for h in table[0][:2]]
        for col, suffix in [(0, 'current'), (1, 'prior')]:
            comparison[kind + '_' + suffix] = {k: v[col] for k, v in table[2].items() if len(v) > col}
    return FinancialSnapshot(ticker=ticker, file_name=file_name, period=end, period_end=end,
        published_at=publication[1] if publication else None, basis=basis, currency=currency,
        company=company, company_type=classify(company), revenue=f['revenue_current'],
        net_income_parent=f['profit_parent_current'], equity_parent=f['equity_parent_current'],
        equity_total=f['equity_total_current'], paid_capital=paid, shares=shares, shares_as_of=end,
        financials=f, extra=extra, warnings=warnings,
        source_urls=[f'https://www.kap.org.tr/tr/Bildirim/{source_id[1]}'] if source_id else [],
        input_error=('Sunum para birimi/ölçeği desteklenmiyor.' if scale is None else
            '; '.join(dict.fromkeys(table_errors.get(id(balance),[])+table_errors.get(id(income),[]))) or None),
        comparison=comparison, statement_type=statement_type, presentation_scale=scale or 1.0,
        line_items={suffix:{k:v[col] for k,v in balance[1].items() if len(v)>col}
                    for col,suffix in [(0,'current'),(1,'prior')]})


class FinancialProvider:
    def __init__(self, zip_path=ZIP_PATH, normalized_path=None, nominal_path=None, archive_dir=None, universe_path=None):
        self.zip_path = Path(zip_path)
        self.normalized_path = Path(normalized_path or ROOT / 'data/normalized_financials.json')
        self.nominal = json.loads(Path(nominal_path).read_text(encoding='utf-8')) if nominal_path else {}
        self.universe = {}
        self.universe_fetched_at = None
        if universe_path and Path(universe_path).exists():
            registry=json.loads(Path(universe_path).read_text(encoding='utf-8'))
            self.universe=registry.get('companies',{})
            self.universe_fetched_at=registry.get('fetched_at')
        self.index = {}
        paths = [self.zip_path] if self.zip_path.exists() else []
        if archive_dir:
            paths += sorted(Path(archive_dir).glob('*.zip'))
        self.archive_paths = {}
        for archive_path in paths:
            with zipfile.ZipFile(archive_path) as archive:
                for name in archive.namelist():
                    m = re.fullmatch(r'([A-Z0-9]+(?:-[A-Z0-9]+)*)_(\d+)_(\d{4})_([1-4])\.xls', Path(name).name.upper(), re.I)
                    if m:
                        for ticker in m[1].split('-'):
                            self.index.setdefault(ticker, []).append((int(m[2]), name))
                            self.archive_paths[(ticker, name)] = archive_path
        self.normalized = {}
        if self.normalized_path.exists():
            payload = json.loads(self.normalized_path.read_text(encoding='utf-8'))
            for record in payload['records']:
                self.normalized.setdefault(record['ticker'].upper(), []).append(record)

    def tickers(self):
        return sorted(self.universe) if self.universe else sorted(set(self.index) | set(self.normalized))

    @lru_cache(maxsize=64)
    def _parse(self, ticker, name):
        with zipfile.ZipFile(self.archive_paths[(ticker, name)]) as archive:
            snap = parse_report(ticker, name, archive.read(name), float(self.nominal.get(ticker, 1.0)))
        kind = self.universe.get(ticker, {}).get('financial_type')
        mapping = {'SIR': 'normal', 'BNK': 'bank', 'KTL': 'bank', 'HLD': 'holding', 'GYO': 'gyo', 'SIG': 'financial', 'FFF': 'financial', 'YO': 'financial', 'GSYO': 'financial'}
        if kind in mapping and kind != 'SIR':
            snap.company_type = mapping[kind]
        return snap

    def _latest_raw(self, ticker, as_of, year=None, quarter=None):
        entries = []
        for disclosure_id, name in self.index.get(ticker, []):
            match = re.search(r'_(\d{4})_([1-4])\.xls$', name, re.I)
            y, q = int(match[1]), int(match[2])
            if year is not None and (y, q) != (year, quarter):
                continue
            entries.append((y, q, disclosure_id, name))
        for _, _, _, name in sorted(set(entries), reverse=True):
            snap = self._parse(ticker, name)
            if snap.published_at and snap.period_end and parse_date(snap.published_at) <= as_of and date.fromisoformat(snap.period_end) <= as_of.date():
                return snap
        return None

    def latest(self, ticker, as_of=None):
        ticker = ticker.upper().removesuffix('.IS')
        as_of = as_of or datetime.now()
        candidates = []
        errors = []
        for record in self.normalized.get(ticker, []):
            try:
                if parse_date(record['published_at']) > as_of:
                    continue
                end = date.fromisoformat(record['period_end'])
                if end > as_of.date():
                    continue
                f = record['financials']
                x = FinancialSnapshot(ticker=ticker, file_name='normalized_financials.json',
                    period=record['period_end'], period_end=record['period_end'],
                    published_at=record['published_at'], basis=record['basis'], currency=record['currency'],
                    company_type=record['company_type'], financials=f, extra=record['extra'],
                    company=record.get('company', ticker), shares=f.get('shares'), shares_as_of=record.get('shares_as_of', record['period_end']),
                    warnings=record.get('warnings', []), source_urls=record['source_urls'])
                if not record.get('comparable_money_basis_verified') or not record['source_urls']:
                    x.input_error = 'Ortak parasal ölçüm/kaynak doğrulaması gerekli.'
                if date.fromisoformat(x.shares_as_of) > as_of.date():
                    x.input_error = 'Pay sayısı tarihi karar tarihinden sonra.'
                sources = record.get('sources', [])
                if not sources or any(parse_date(s['published_at']) > as_of for s in sources):
                    x.input_error = 'Kaynak raporların yayımlanma tarihleri doğrulanmalı.'
                elif set(record['source_urls']) != {s['url'] for s in sources}:
                    x.input_error = 'Kaynak URL ve yayın tarihi kayıtları eşleşmiyor.'
                if record['basis'] == 'TTM' and len(sources) < 3:
                    x.input_error = 'TTM için en az üç tarihli rapor kaynağı gerekli.'
                candidates.append(x)
            except (KeyError, ValueError, TypeError):
                errors.append('Normalize kayıt biçimi geçersiz.')
        # Read only the newest eligible report and the two TTM companions.
        # Opening every older XLS on every scan made the full universe too slow.
        raw = self._latest_raw(ticker, as_of)
        if raw:
            candidates.append(raw)
        if not candidates:
            raise ValueError(errors[0] if errors else 'Karar tarihinde erişilebilir finansal rapor yok.')
        # Prefer an audited normalized TTM over raw YTD for the same latest end date.
        latest = max(candidates, key=lambda x: (x.period_end or '', x.basis in ('TTM', 'ANNUAL'), parse_date(x.published_at)))
        if latest.basis == 'YTD' and latest.company_type in ('normal','bank','financial'):
            end = date.fromisoformat(latest.period_end)
            annual = self._latest_raw(ticker, as_of, end.year-1, 4)
            prior = self._latest_raw(ticker, as_of, end.year-1, (end.month-1)//3+1)
            latest = __import__('copy').deepcopy(latest)
            if not annual or not prior:
                missing = ([f'{end.year-1} yıllık'] if not annual else []) + ([f'{end.year-1} aynı ara dönem'] if not prior else [])
                latest.input_error = 'TTM kaynak raporları eksik: ' + ', '.join(missing) + '. /kapguncelle ile geçmiş raporları yükle.'
            else:
                try:
                    from .ttm import assemble_snapshots
                    latest = assemble_snapshots(annual, latest, prior, as_of)
                except (ValueError, KeyError, TypeError) as exc:
                    latest.input_error = 'TTM parasal eşleştirme tamamlanamadı: ' + str(exc)
        return latest
