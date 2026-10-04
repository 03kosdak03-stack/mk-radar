"""Dated primary-source CPI bridges. Unknown dates never reuse a stale factor."""
from .financials import parse_date

BRIDGES = {
    ('2025-12-31', '2026-06-30'): 1.1776,
    ('2025-06-30', '2026-06-30'): 1.3211,
}
SOURCE = 'https://veriportali.tuik.gov.tr/tr/press/58289'
PUBLISHED = '2026-07-03'
AVAILABLE_AFTER = '2026-07-04T00:00:00'  # Exact intraday release time not asserted.
HISTORICAL_BRIDGES = {
    ('2024-12-31','2025-03-31'): (1.1006,'https://veriportali.tuik.gov.tr/tr/press/54178','2025-04-03','2025-04-04T00:00:00'),
    ('2024-03-31','2025-03-31'): (1.3810,'https://veriportali.tuik.gov.tr/tr/press/54178','2025-04-03','2025-04-04T00:00:00'),
    ('2024-12-31','2025-06-30'): (1.1667,'https://veriportali.tuik.gov.tr/Bulten/Index?p=Tuketici-Fiyat-Endeksi-Haziran-2025-54181','2025-07-03','2025-07-04T00:00:00'),
    ('2024-06-30','2025-06-30'): (1.3505,'https://veriportali.tuik.gov.tr/Bulten/Index?p=Tuketici-Fiyat-Endeksi-Haziran-2025-54181','2025-07-03','2025-07-04T00:00:00'),
    ('2024-12-31','2025-09-30'): (1.2543,'https://veriportali.tuik.gov.tr/tr/press/54184','2025-10-03','2025-10-04T00:00:00'),
    ('2024-09-30','2025-09-30'): (1.3329,'https://veriportali.tuik.gov.tr/tr/press/54184','2025-10-03','2025-10-04T00:00:00'),
    ('2025-12-31','2026-03-31'): (1.1004,'https://veriportali.tuik.gov.tr/tr/search?q=fiyat+endeksi','2026-04-03','2026-04-04T00:00:00'),
    ('2025-03-31','2026-03-31'): (1.3087,'https://veriportali.tuik.gov.tr/tr/search?q=fiyat+endeksi','2026-04-03','2026-04-04T00:00:00'),
}


def official_bridge(old_end, new_end, as_of):
    record=HISTORICAL_BRIDGES.get((old_end,new_end))
    if record:
        factor,source,published,available=record
    else:
        factor=BRIDGES.get((old_end,new_end))
        source,published,available=SOURCE,PUBLISHED,AVAILABLE_AFTER
    if factor is None:
        raise ValueError('Bu tarihler için kaynaklı TÜFE köprüsü yok.')
    if parse_date(available) > as_of:
        raise ValueError('TÜFE kaynağı karar tarihinde henüz yayımlanmamış.')
    return factor, dict(source=source, published_at=published,
                        available_after=available,
                        from_period=old_end, to_period=new_end)
