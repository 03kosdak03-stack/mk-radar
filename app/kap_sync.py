"""Public KAP bulk export sync. URLs verified against KAP's own frontend.

No API key, credentials or Telegram token are sent to KAP. Downloaded XLS files
stay in ZIP containers; they are never extracted or executed.
"""
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import hashlib
import json
import os
import re
import tempfile
import zipfile
import requests
from lxml import html
from .financials import canonical
from .config import settings

BASE = 'https://www.kap.org.tr/tr'
COMPANIES_URL = BASE + '/api/company/items/IGS/A'
INSTRUMENTS_URL = BASE + '/tumKalemler/kpy41_acc3_son_durum_borsa_piyasalar'
INDICES_URL = BASE + '/tumKalemler/kpy41_acc3_endeksler'


def atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, allow_nan=False)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def equity_company_codes(raw):
    """Select equity rows, including a company cell spanning several rows."""
    doc = html.fromstring(raw)
    result = set()
    company_code, remaining = None, 0
    for row in doc.xpath('//tr'):
        cells = row.findall('td')
        if not cells:
            continue
        links = cells[0].xpath('.//a[contains(@href, "/sirket-bilgileri/ozet/")]')
        if links:
            match = re.search(r'/ozet/(\d+)', links[0].get('href', ''))
            company_code = match[1] if match else None
            remaining = max(1, int(cells[0].get('rowspan', '1')))
            instrument_cell = 1
        elif remaining > 0 and not row.get('id', '').startswith('S:'):
            instrument_cell = 0
        else:
            continue
        if len(cells) > instrument_cell:
            kind = canonical(' '.join(cells[instrument_cell].itertext()))
            if company_code and (re.search(r'\bPAY\b', kind) or 'HISSE' in kind or 'HISSE' in kind.replace('ı', 'I')):
                result.add(company_code)
        remaining -= 1
    if len(result) < 400:
        raise ValueError('KAP pay türü listesi eksik/biçimi değişmiş; mevcut evren korunuyor.')
    return result


def index_company_codes(raw):
    result = set()
    for row in html.fromstring(raw).xpath('//tr'):
        if 'BIST' not in canonical(' '.join(row.itertext())):
            continue
        for a in row.xpath('.//a[contains(@href, "/sirket-bilgileri/ozet/")]'):
            match = re.search(r'/ozet/(\d+)', a.get('href', ''))
            if match:
                result.add(match[1])
    if len(result) < 400:
        raise ValueError('KAP endeks listesi eksik; mevcut evren korunuyor.')
    return result


def build_universe(members, instruments, indices=None):
    equity_ids = equity_company_codes(instruments)
    if indices is not None:
        equity_ids |= index_company_codes(indices)
    companies = {}
    for member in members:
        if str(member.get('companyCode')) not in equity_ids or str(member.get('payIslemDurumu')) != '1':
            continue
        codes = re.findall(r'\b[A-Z][A-Z0-9]{3,9}\b', member.get('stockCode') or '')
        for ticker in codes:
            if ticker in companies:
                raise ValueError('KAP kodu birden fazla şirketle eşleşti: ' + ticker)
            companies[ticker] = dict(company=member['kapMemberTitle'], company_code=str(member['companyCode']),
                financial_type=member.get('financialType'), share_classes=codes,
                paid_capital=member.get('paidCapital'), member_oid=member.get('kapMemberOid'),
                capital_source=COMPANIES_URL,
                source=INSTRUMENTS_URL, indices_source=INDICES_URL if indices is not None else None)
    if len(companies) < 400:
        raise ValueError('KAP şirket kodu eşleşmesi eksik; mevcut evren korunuyor.')
    return companies


def periods_needed(as_of):
    # Two annuals support January and companies whose latest interim is last year.
    year = as_of.year
    periods = {(year-2, 4), (year-1, 4)}
    for q in range(1, 4):
        periods.add((year-1, q))
        if q*3 < as_of.month or (q*3 == as_of.month and as_of.day >= 28):
            periods.add((year, q))
    return sorted(periods)


class KapSync:
    def __init__(self, root=None, session=None):
        self.root = Path(root or settings.kap_data_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.archives = self.root / 'archives'
        self.archives.mkdir(exist_ok=True)
        self.session = session or requests.Session()
        self.session.headers.update({'Accept-Language': 'tr', 'User-Agent': 'MK-Radar/1.1'})

    def sync(self, as_of=None, force=False, progress=None):
        as_of = as_of or datetime.now(ZoneInfo('Europe/Istanbul'))
        manifest_path = self.root / 'sync_status.json'
        previous = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {}
        result = dict(started_at=as_of.isoformat(), completed_at=None, files=previous.get('files', {}), errors=[], downloaded=0, cached=0)
        try:
            members = self.session.get(COMPANIES_URL, timeout=(15, 90)); members.raise_for_status()
            instruments = self.session.get(INSTRUMENTS_URL, timeout=(15, 90)); instruments.raise_for_status()
            indices = self.session.get(INDICES_URL, timeout=(15, 90)); indices.raise_for_status()
            companies = build_universe(members.json(), instruments.content, indices.content)
            atomic_json(self.root / 'universe.json', dict(fetched_at=as_of.isoformat(), sources=[COMPANIES_URL, INSTRUMENTS_URL, INDICES_URL], companies=companies))
            result['universe_count'] = len(companies)
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            result['errors'].append('Şirket listesi: ' + type(exc).__name__ + '. Önceki doğrulanmış liste korunuyor.')
        for year, quarter in periods_needed(as_of):
            key = f'{year}_{quarter}'
            path = self.archives / (key+'.zip')
            info = result['files'].get(key, {})
            active=year==as_of.year or (year==as_of.year-1 and quarter==4)
            ttl = timedelta(hours=max(1,settings.kap_sync_interval_hours)) if active else timedelta(days=7)
            if not force and path.exists() and info.get('fetched_at') and as_of - datetime.fromisoformat(info['fetched_at']) < ttl:
                result['cached'] += 1
                continue
            if progress:
                progress(f'{year}/{quarter} KAP raporları indiriliyor')
            url = BASE + f'/api/financialTable/download/{year}/{quarter}'
            temporary = None
            try:
                check = self.session.get(BASE + f'/api/financialTable/checkFileExist/{year}/{quarter}', timeout=(15, 90))
                check.raise_for_status()
                available = check.json()
                if not isinstance(available, list):
                    raise ValueError('KAP arşiv kontrol yanıtı biçimi değişmiş.')
                if not available:
                    result['errors'].append(f'{key}: KAP arşivi henüz yok; önceki raporlar korundu.')
                    continue
                conditional={}
                if path.exists() and not force:
                    if info.get('etag'):conditional['If-None-Match']=info['etag']
                    if info.get('last_modified'):conditional['If-Modified-Since']=info['last_modified']
                with self.session.get(url, timeout=(15, 180), stream=True,headers=conditional) as response:
                    if response.status_code==304:
                        if not path.exists():raise ValueError('304 yanıtı var ancak yerel arşiv yok.')
                        info=dict(info,fetched_at=as_of.isoformat())
                        result['files'][key]=info;result['cached']+=1
                        continue
                    if response.status_code == 404:
                        result['errors'].append(f'{key}: KAP arşivi henüz yok; önceki raporlar korundu.')
                        continue
                    response.raise_for_status()
                    fd, temporary = tempfile.mkstemp(dir=self.archives, suffix='.tmp')
                    size = 0
                    digest = hashlib.sha256()
                    with os.fdopen(fd, 'wb') as output:
                        for block in response.iter_content(1024*1024):
                            size += len(block)
                            if size > 250*1024*1024:
                                raise ValueError('KAP arşivi boyut sınırını aşıyor.')
                            digest.update(block); output.write(block)
                with zipfile.ZipFile(temporary) as archive:
                    files = [x for x in archive.infolist() if not x.is_dir()]
                    valid = [x for x in files if re.fullmatch(r'[A-Z0-9]+(?:-[A-Z0-9]+)*_\d+_'+str(year)+'_'+str(quarter)+r'\.xls', Path(x.filename).name, re.I)]
                    if not valid or len(valid) != len(files) or sum(x.file_size for x in files) > 2*1024**3 or archive.testzip():
                        raise ValueError('KAP arşivi/rapor adları doğrulanamadı.')
                os.replace(temporary, path)
                result['files'][key] = dict(fetched_at=as_of.isoformat(), source=url, sha256=digest.hexdigest(), reports=len(valid), size_bytes=size,
                    etag=response.headers.get('ETag'),last_modified=response.headers.get('Last-Modified'))
                result['downloaded'] += 1
            except (requests.RequestException, ValueError, OSError, zipfile.BadZipFile) as exc:
                result['errors'].append(f'{key}: indirme/doğrulama tamamlanamadı ({type(exc).__name__}); önceki dosya korundu.')
            finally:
                if temporary and os.path.exists(temporary):
                    os.unlink(temporary)
        result['completed_at'] = datetime.now(ZoneInfo('Europe/Istanbul')).isoformat()
        atomic_json(manifest_path, result)
        return result


def main():
    result = KapSync().sync(progress=print)
    print('Pay evreni:', result.get('universe_count', 'önceki liste'))
    print('İndirilen arşiv:', result['downloaded'], 'Önbellek:', result['cached'])
    for error in result['errors']:
        print(error)


if __name__ == '__main__':
    main()
