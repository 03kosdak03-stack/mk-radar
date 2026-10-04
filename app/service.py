from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo
from .config import settings
from .db import Store
from .financials import FinancialProvider
from .providers import PriceProvider
from .valuation import value_from_kap, select_by_value, MODEL_VERSION


class UpdateService:
    def __init__(self, financials=None, prices=None, store=None):
        self.financials = financials or self.new_financials()
        self.prices = prices or PriceProvider()
        self.store = store or Store()

    @staticmethod
    def new_financials():
        nominal = Path(settings.nominal_share_values)
        return FinancialProvider(settings.financial_zip, settings.normalized_financials, nominal if nominal.exists() else None,
                                 Path(settings.kap_data_dir)/'archives', Path(settings.kap_data_dir)/'universe.json')

    def value_ticker(self, ticker, as_of=None):
        as_of = as_of or datetime.now(ZoneInfo('Europe/Istanbul'))
        if as_of.tzinfo is None:
            as_of=as_of.replace(tzinfo=ZoneInfo('Europe/Istanbul'))
        as_of=as_of.astimezone(ZoneInfo('Europe/Istanbul'))
        local_time = as_of.replace(tzinfo=None)
        try:
            fin = self.financials.latest(ticker, local_time)
            row = value_from_kap(fin)
            share_classes = getattr(self.financials, 'universe', {}).get(ticker, {}).get('share_classes', [])
            if len(share_classes) > 1:
                row.update(status='PAY_SINIFI_KONTROLU', reason='Birden fazla pay sınıfı var; sınıf bazında pay sayısı ve haklar doğrulanmalı.')
        except (ValueError, KeyError, OSError) as exc:
            row=dict(ticker=ticker, status='VERI_EKSIK', reason=str(exc), bands={}, fair_value=None, potential=None, warnings=[], model_version=MODEL_VERSION,
                calculated_at=as_of.isoformat(timespec='seconds'))
            row['target_context']=self.store.target_context(row)
            self.store.save_snapshot(row)
            return row
        # Check financial eligibility before making a network request for hundreds of missing rows.
        if row['status'] == 'FIYAT_EKSIK':
            if fin.period_end and (as_of.date() - date.fromisoformat(fin.period_end)).days > 550:
                row.update(status='ESKI_FINANSAL', reason='Finansal dönem 550 günden eski; güncel sıralamaya alınmadı.')
            else:
                shares_day = getattr(fin, 'shares_as_of', None) or fin.period_end
                quote = self.prices.quote(ticker, as_of=as_of, shares_as_of=shares_day)
                row = value_from_kap(fin, quote.price)
                row.update(price_date=quote.price_date, turnover20=quote.turnover20, price_source=quote.source)
                if quote.error:
                    row.update(status='FIYAT_EKSIK', reason=quote.error)
                if quote.splits:
                    row.update(status='SERMAYE_KONTROLU', reason='Pay tarihi sonrası sermaye işlemi var; güncel pay sayısı doğrulanmalı.', corporate_actions=quote.splits)
                if not quote.corporate_actions_checked and not quote.error:
                    member=getattr(self.financials,'universe',{}).get(ticker,{})
                    fetched=getattr(self.financials,'universe_fetched_at',None)
                    cap=member.get('paid_capital')
                    valid=False
                    try:
                        stamp=datetime.fromisoformat(fetched)
                        valid=(stamp.tzinfo is not None and stamp<=as_of and
                            isinstance(cap,(int,float)) and cap>0 and fin.paid_capital is not None and
                            abs(cap-fin.paid_capital)<=fin.presentation_scale/2 and
                            (as_of-stamp).total_seconds()<=7*86400 and len(member.get('share_classes',[]))==1)
                    except (ValueError,TypeError):pass
                    if valid:
                        if cap!=fin.paid_capital:
                            from copy import deepcopy
                            adjusted=deepcopy(fin)
                            adjusted.financials['shares']=fin.financials['shares']*cap/fin.paid_capital
                            adjusted.shares=adjusted.financials['shares']
                            row=value_from_kap(adjusted,quote.price)
                            row.update(price_date=quote.price_date,turnover20=quote.turnover20,price_source=quote.source)
                        row['share_verification']=dict(method='CURRENT_KAP_CAPITAL_MATCHES_REPORT',
                            paid_capital=cap,source=member.get('capital_source'),checked_at=fetched,
                            report_paid_capital=fin.paid_capital,rounding_tolerance=fin.presentation_scale/2,
                            limitation='Nominal pay ve pay sınıfı varsayımları korunur; tam kurumsal işlem arşivi değildir.')
                        row['warnings'].append('Resmi kapanışta hacim yok. KAP güncel sermayesi raporla aynı; nominal pay varsayımı korunur.')
                    else:
                        row.update(status='SERMAYE_KONTROLU',reason='Resmi fiyat var; güncel KAP sermayesi/pay sınıfı raporla eşleştirilemedi. Sıralamaya alınmadı.')
        row['calculated_at'] = as_of.isoformat(timespec='seconds')
        row['target_context']=self.store.target_context(row)
        self.store.save_snapshot(row)
        return row

    def scan(self, as_of=None, workers=4):
        as_of = as_of or datetime.now(ZoneInfo('Europe/Istanbul'))
        tickers = self.financials.tickers()
        # Each request gets its own requests.Session; archive adapter reads are local.
        def calculate(ticker):
            if type(self.prices) is PriceProvider:
                service = UpdateService(self.financials, PriceProvider(), self.store)
                return service.value_ticker(ticker, as_of)
            return self.value_ticker(ticker, as_of)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            rows = list(pool.map(calculate, tickers))
        chosen = select_by_value(rows)
        run = dict(as_of=as_of.isoformat(timespec='seconds'), model_version=MODEL_VERSION,
                   ranking_version=chosen['ranking_version'],
                   total=len(tickers), counts=dict(Counter(r['status'] for r in rows)), rows=rows,
                   top15=chosen['top15'], portfolio5=chosen['portfolio5'], cash_slots=chosen['cash_slots'],
                   coverage_note='KAP pay türü/şirket kodu eşleşmesi kullanıldı.' if getattr(self.financials, 'universe', {}) else 'Liste yalnız yüklenen evreni kapsar; KAP şirket listesi henüz yüklenmedi.')
        from .final_review import coverage_summary
        run['readiness']=coverage_summary(rows)
        self.store.save_run(run)
        return run
