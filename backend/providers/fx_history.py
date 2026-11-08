"""Fetch daily purchase-date FX rates and evaluate complete open trade lots.

Pure calculations live in backend/portfolio/fx.py; incomplete history or
unavailable rates produces an unavailable result, never an aggregate fallback."""

from __future__ import annotations

import bisect
import logging
from datetime import date, timedelta

from backend.portfolio.fx import _trade_date, merge_trades, total_fx_from_trades, valid_positive
from backend.storage.cache import cached_fetch

log = logging.getLogger(__name__)

_TTL = 4 * 3600


def _yahoo_rates(currency: str, base: str, start: date) -> dict:
    """{date: base per 1 `currency`} daily closes since `start`; raises when unavailable."""
    import yfinance as yf

    for pair, inverted in ((f'{currency}{base}=X', False), (f'{base}{currency}=X', True)):
        hist = yf.Ticker(pair).history(start=start.isoformat(), auto_adjust=False)
        if hist is None or hist.empty:
            continue
        rates = {}
        for ts, close in hist['Close'].items():
            if valid_positive(close):
                rates[ts.date()] = 1 / float(close) if inverted else float(close)
        if rates:
            return rates
    raise RuntimeError(f'No FX history for {currency}/{base}')


def fx_history(currency: str, base: str, start: date) -> dict:
    # Anchor `start` to the month so changing trade sets reuse the cached series;
    # the extra week of look-back covers purchases on a weekend / holiday at month start.
    anchor = date(start.year, start.month, 1) - timedelta(days=7)
    key = ('fx_hist', currency, base, anchor.isoformat())
    return cached_fetch(key, _TTL, lambda: _yahoo_rates(currency, base, anchor))


def _rate_lookup(series: dict):
    days = sorted(series)

    def rate_on(day: date):
        """Latest close on or before `day` (weekends / holidays roll back)."""
        i = bisect.bisect_right(days, day)
        return series[days[i - 1]] if i else None
    return rate_on, (series[days[-1]] if days else None)


def compute_trade_fx(positions, base_currency: str, *trade_sources, daily_fx_rates=None) -> dict:
    """{ticker: (value | None, reason)} for non-base positions, from trade history."""
    base = str(base_currency or '').upper()
    trades = merge_trades(*trade_sources)
    daily_fx_rates = daily_fx_rates or {}
    out: dict = {}
    series_by_ccy: dict = {}
    for p in positions:
        currency = str(p.get('currency') or '').upper()
        if not currency or currency == base:
            continue
        ticker = p.get('ticker')
        days = [d for t in trades if t.get('ticker') == ticker and (d := _trade_date(t))]
        if not days:
            out[ticker] = (None, 'No trade history for this ticker — upload a transaction CSV')
            continue
        if currency not in series_by_ccy or min(days) < series_by_ccy[currency][0]:
            try:
                series_by_ccy[currency] = (min(days), fx_history(currency, base, min(days)))
            except Exception as e:
                log.debug('FX history %s/%s unavailable: %s', currency, base, e)
                out[ticker] = (None, 'Historical FX rates are unavailable (Yahoo Finance)')
                continue
        rate_on, latest = _rate_lookup(series_by_ccy[currency][1])
        live = (daily_fx_rates.get(currency) or {}).get('now')
        out[ticker] = total_fx_from_trades(p, base, trades, rate_on, live if valid_positive(live) else latest)
    return out
