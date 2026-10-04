"""Daily public price adapter. Quote dates and corporate actions are explicit."""
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo
import math
import statistics
import re
import requests
import json
from pathlib import Path
import tempfile
import os

ISTANBUL = ZoneInfo('Europe/Istanbul')


@dataclass
class Quote:
    ticker: str
    price: float | None = None
    price_date: str | None = None
    turnover20: float | None = None
    splits: list = field(default_factory=list)
    source: str = 'Yahoo Finance günlük fiyat'
    error: str | None = None
    corporate_actions_checked: bool = True


def number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


class PriceProvider:
    def __init__(self, session=None, cache_dir=None, primary_fallback=True):
        self.session = session or requests.Session()
        self.session.headers.update({'User-Agent': 'Mozilla/5.0'})
        self.cache_dir=Path(cache_dir) if cache_dir else (Path(__file__).resolve().parents[1]/'data/prices' if session is None else None)
        self.primary_fallback=primary_fallback

    def cached(self,ticker,as_of,shares_as_of):
        if self.cache_dir is None:return None
        path=self.cache_dir/(ticker+'.json')
        try:
            record=json.loads(path.read_text(encoding='utf-8'))
            received=datetime.fromisoformat(record['received_at'])
            if received.tzinfo is None or received>as_of:return None
            if (as_of-received).total_seconds()>7*86400:return None
            quote=self.parse(ticker,record['result'],as_of,shares_as_of)
            if quote.error:return None
            quote.source='Yahoo günlük fiyat (tarihli yerel önbellek)'
            return quote
        except (OSError,ValueError,KeyError,TypeError,IndexError):return None

    def save_cache(self,ticker,result,as_of):
        if self.cache_dir is None:return
        self.cache_dir.mkdir(parents=True,exist_ok=True)
        fd,temp=tempfile.mkstemp(dir=self.cache_dir,suffix='.tmp')
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as f:
                json.dump(dict(received_at=as_of.isoformat(),result=result),f,ensure_ascii=False,allow_nan=False)
            os.replace(temp,self.cache_dir/(ticker+'.json'))
        finally:
            if os.path.exists(temp):os.unlink(temp)

    def fallback(self,ticker,as_of,message):
        if self.primary_fallback:
            try:
                from .primary_prices import primary_close
                return primary_close(self.session,ticker,as_of)
            except (requests.RequestException,ValueError,KeyError,TypeError,OverflowError,OSError):pass
        return Quote(ticker,error=message)

    def quote(self, ticker, as_of=None, shares_as_of=None):
        ticker = ticker.upper().removesuffix('.IS')
        if not re.fullmatch('[A-Z0-9]{2,12}', ticker):
            raise ValueError('Geçersiz hisse kodu')
        as_of = as_of or datetime.now(ISTANBUL)
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=ISTANBUL)
        as_of = as_of.astimezone(ISTANBUL)
        cached=self.cached(ticker,as_of,shares_as_of)
        if cached is not None:return cached
        start = as_of.date() - timedelta(days=100)
        if shares_as_of:
            start = min(start, date.fromisoformat(shares_as_of))
        params = dict(interval='1d', period1=int(datetime.combine(start, datetime.min.time(), ISTANBUL).timestamp()),
                      period2=int(as_of.timestamp()), events='splits')
        try:
            response = self.session.get(f'https://query1.finance.yahoo.com/v8/finance/chart/{ticker}.IS', params=params, timeout=(5, 20))
            response.raise_for_status()
            chart = response.json()['chart']
            if not chart.get('result'):
                return self.fallback(ticker,as_of,'Fiyat sağlayıcı sonucu yok.')
            quote=self.parse(ticker, chart['result'][0], as_of, shares_as_of)
            if quote.error:
                return self.fallback(ticker,as_of,quote.error)
            if not quote.error:
                try:self.save_cache(ticker,chart['result'][0],as_of)
                except OSError:pass
            return quote
        except requests.HTTPError as exc:
            code = exc.response.status_code if exc.response is not None else None
            if code == 429:
                return self.fallback(ticker,as_of,'Yahoo istek sınırına ulaşıldı (HTTP 429); güncel fiyat alınamadı.')
            return self.fallback(ticker,as_of,f'Fiyat sağlayıcı HTTP hatası: {code or "bilinmiyor"}.')
        except (requests.RequestException, ValueError, KeyError, TypeError, IndexError):
            return self.fallback(ticker,as_of,'Fiyat sağlayıcı yanıtı alınamadı veya biçimi geçersiz.')

    @staticmethod
    def parse(ticker, result, as_of, shares_as_of=None):
        if result.get('meta', {}).get('currency') != 'TRY':
            return Quote(ticker, error='Fiyat para birimi TRY değil.')
        bars = result.get('indicators', {}).get('quote', [{}])[0]
        rows = []
        for i, stamp in enumerate(result.get('timestamp', [])):
            trading_day = datetime.fromtimestamp(stamp, ISTANBUL).date()
            # Do not call a live intraday bar a completed daily close.
            if trading_day > as_of.date() or (trading_day == as_of.date() and (as_of.hour, as_of.minute) < (18, 30)):
                continue
            close = bars.get('close', [])[i] if i < len(bars.get('close', [])) else None
            volume = bars.get('volume', [])[i] if i < len(bars.get('volume', [])) else None
            if number(close) and close > 0:
                rows.append((trading_day, close, volume))
        if not rows:
            return Quote(ticker, error='Tamamlanmış günlük kapanış yok.')
        rows.sort()
        day, close, _ = rows[-1]
        if (as_of.date() - day).days > 7:
            return Quote(ticker, price_date=day.isoformat(), error='Fiyat yedi takvim gününden eski.')
        turnover = None
        window = rows[-20:]
        if len(window) == 20 and all(number(v) and v >= 0 for _, _, v in window):
            turnover = statistics.median(c * v for _, c, v in window)
        splits = []
        for action in result.get('events', {}).get('splits', {}).values():
            action_day = datetime.fromtimestamp(action['date'], ISTANBUL).date()
            if action_day <= as_of.date() and (not shares_as_of or action_day > date.fromisoformat(shares_as_of)):
                splits.append(dict(date=action_day.isoformat(), numerator=action.get('numerator'), denominator=action.get('denominator')))
        return Quote(ticker, float(close), day.isoformat(), turnover, splits)

    def last_close(self, ticker):
        return self.quote(ticker).price


price_provider = PriceProvider()
