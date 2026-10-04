"""Official İş Yatırım daily closing prices. No volume/actions are invented."""
from datetime import datetime,timedelta
import math

URL='https://www.isyatirim.com.tr/_Layouts/15/IsYatirim.Website/Common/ChartData.aspx/IndexHistoricalAll'


def primary_close(session,ticker,as_of):
    from .providers import Quote,ISTANBUL
    start=as_of-timedelta(days=100)
    response=session.get(URL,params=dict(period=1440,**{'from':start.strftime('%Y%m%d000000'),
        'to':as_of.strftime('%Y%m%d%H%M%S'),'endeks':ticker}),timeout=(5,20))
    response.raise_for_status()
    payload=response.json();points=payload.get('data')
    if not isinstance(points,list) or not points:
        raise ValueError('Resmi fiyat kaynağında günlük seri yok.')
    rows={}
    for point in points:
        if not isinstance(point,list) or len(point)!=2:
            raise ValueError('Resmi günlük fiyat serisi biçimi değişmiş.')
        stamp,price=point
        if isinstance(stamp,bool) or not isinstance(stamp,(int,float)) or not math.isfinite(stamp):
            raise ValueError('Resmi fiyat tarihi geçersiz.')
        if isinstance(price,bool) or not isinstance(price,(int,float)) or not math.isfinite(price) or price<=0:
            raise ValueError('Resmi kapanış geçersiz.')
        day=datetime.fromtimestamp(stamp/1000,ISTANBUL).date()
        if day>as_of.date() or (day==as_of.date() and (as_of.hour,as_of.minute)<(18,30)):
            continue
        if day in rows and rows[day]!=price:raise ValueError('Aynı gün için çelişen kapanışlar.')
        rows[day]=price
    if not rows:raise ValueError('Tamamlanmış resmi günlük kapanış yok.')
    day=max(rows)
    if (as_of.date()-day).days>7:raise ValueError('Resmi kapanış yedi günden eski.')
    return Quote(ticker,float(rows[day]),day.isoformat(),source='İş Yatırım resmi günlük kapanış',
                 corporate_actions_checked=False)
