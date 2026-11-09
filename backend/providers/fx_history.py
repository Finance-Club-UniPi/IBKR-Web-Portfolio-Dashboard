"""Fetch daily purchase-date FX rates and evaluate complete open trade lots.

Pure calculations live in backend/portfolio/fx.py; incomplete history or
unavailable rates produces an unavailable result, never an aggregate fallback."""

from __future__ import annotations

import bisect
import logging
from datetime import date, timedelta

from backend.portfolio.fx import _trade_date, merge_trades, total_fx_from_trades, valid_positive
from backend.portfolio.valuation import position_key
from backend.providers.flex import reconciled_lots
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
        return series[days[i - 1]] if i and (day - days[i - 1]).days <= 5 else None
    return rate_on, (series[days[-1]] if days else None)


def compute_trade_fx(positions, base_currency: str, *trade_sources, daily_fx_rates=None, lot_state=None, executions_from=None) -> dict:
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
        key = position_key(p) if p.get('conId') else ticker
        if lot_state is not None:
            lots, reason = reconciled_lots(p, lot_state, executions_from)
            if lots is None:
                out[key] = (None, reason)
                continue
            try:
                opened = [date.fromisoformat(lot['opened_at'][:10]) for lot in lots]
                rates = fx_history(currency, base, min(opened))
                lookup, _ = _rate_lookup(rates)
                now = p.get('valuation_rate') or (daily_fx_rates.get(currency) or {}).get('now')
                price = p.get('valuation_price') or p.get('fx_native_price')
                if not valid_positive(now) or not valid_positive(price):
                    raise ValueError('Dated current valuation is unavailable')
                value = 0.0
                for lot, day in zip(lots, opened, strict=True):
                    opening_rate = lot.get('fx_rate') or lookup(day)
                    if not valid_positive(opening_rate):
                        raise ValueError(f'Opening FX rate unavailable for {day}')
                    value += lot['quantity'] * price * (now - opening_rate)
                out[key] = (round(value, 2), f"Reported open lots; valuation {p.get('valuation_as_of', '')}; purchase-day FX estimate")
            except Exception as e:
                out[key] = (None, str(e))
            continue
        if not p.get('history_reconciled'):
            out[key] = (None, 'Complete open-lot coverage is unverified; configure Flex history')
            continue
        days = [d for t in trades if t.get('ticker') == ticker and (d := _trade_date(t))]
        if not days:
            out[key] = (None, 'No trade history for this ticker — upload a transaction CSV')
            continue
        if currency not in series_by_ccy or min(days) < series_by_ccy[currency][0]:
            try:
                series_by_ccy[currency] = (min(days), fx_history(currency, base, min(days)))
            except Exception as e:
                log.debug('FX history %s/%s unavailable: %s', currency, base, e)
                out[key] = (None, 'Historical FX rates are unavailable (Yahoo Finance)')
                continue
        rate_on, _ = _rate_lookup(series_by_ccy[currency][1])
        live = (daily_fx_rates.get(currency) or {}).get('now')
        out[key] = total_fx_from_trades(p, base, trades, rate_on, live if valid_positive(live) else None)
    return out


def complete_daily_rates(base, current, daily):
    """Fill optional previous-day FX independently of current holding valuation."""
    from datetime import datetime

    from backend.portfolio.history import get_compare_history
    from backend.portfolio.valuation import recent

    result = {ccy: dict(rate) for ccy, rate in daily.items()}
    for currency, rate in current.items():
        if currency == base or not recent(rate.get('as_of')):
            continue
        entry = result.setdefault(currency, dict(rate))
        if valid_positive(entry.get('previous')):
            continue
        as_of = datetime.fromisoformat(entry['as_of']).date()
        series = get_compare_history(f'{currency}{base}=X', '1M')
        if series is None:
            continue
        previous = series[[stamp.date() < as_of for stamp in series.index]].dropna()
        if not previous.empty and (as_of - previous.index[-1].date()).days <= 5:
            entry['previous'] = float(previous.iloc[-1])
            entry['previous_as_of'] = previous.index[-1].date().isoformat()
            entry['source'] = 'Current valuation FX / Yahoo previous daily FX'
    return result
