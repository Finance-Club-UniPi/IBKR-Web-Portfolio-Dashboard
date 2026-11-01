"""Historical portfolio value and instrument comparison series.

The portfolio history is a *backcast*: today's quantities held constant over
the window, valued at historical closes and converted to the account base
currency with daily FX.  It is not a record of what the account was actually
worth (no trade history, no cash, no dividends in cash), and says so in the UI.

`build_value_series` and `compare_frame` are pure; the network lives in
`get_portfolio_history` / `get_compare_history`, which reuse the shared 4-hour
price cache.
"""

from __future__ import annotations

import csv
import io
import logging
import math
import urllib.request
from datetime import date

import pandas as pd

from cache_util import cached_fetch
from market_intel import get_price_history
from net_util import fetch_with_retry

log = logging.getLogger(__name__)

# Dashboard label → yfinance period string.
PERIODS = {'1M': '1mo', '3M': '3mo', '6M': '6mo', 'YTD': 'ytd',
           '1Y': '1y', '3Y': '3y', '5Y': '5y'}
DEFAULT_PERIOD = '1Y'
CUSTOM = 'Custom'        # user-chosen from/until; served from the cached 5Y download
MAX_LOOKBACK_YEARS = 5
MAX_COMPARE = 5

_TTL = 3600 * 4
_SEARCH_TTL = 3600
# ECB Data Portal: €STR volume-weighted trimmed mean, daily, % p.a.
_ESTR_URL = ('https://data-api.ecb.europa.eu/service/data/EST/B.EU000A2X2A25.WT'
             '?format=csvdata&startPeriod={start}')


def custom_bounds(today: date | None = None) -> tuple[date, date]:
    """(earliest selectable start, latest selectable end) for the custom range."""
    today = today or date.today()
    try:
        earliest = today.replace(year=today.year - MAX_LOOKBACK_YEARS)
    except ValueError:                      # 29 Feb
        earliest = today.replace(year=today.year - MAX_LOOKBACK_YEARS, day=28)
    return earliest, today


def clip_window(entry: dict | None, start, end, pad_days: int = 0) -> dict | None:
    """Keep only the {'dates','prices'} rows within [start - pad_days, end]."""
    if not entry or start is None or end is None:
        return entry
    lo = (pd.Timestamp(start) - pd.Timedelta(f'{pad_days}D')).strftime('%Y-%m-%d')
    hi = pd.Timestamp(end).strftime('%Y-%m-%d')
    keep = [i for i, d in enumerate(entry['dates']) if lo <= d <= hi]
    return {'dates': [entry['dates'][i] for i in keep], 'prices': [entry['prices'][i] for i in keep]}


def parse_symbols(value: str | list | tuple | None) -> list[str]:
    """Unique upper-case symbols from a picker list or a comma/space separated string."""
    if isinstance(value, (list, tuple)):
        raw_items = [str(v) for v in value]
    else:
        raw_items = (value or '').replace(';', ',').replace('\n', ',').replace(' ', ',').split(',')
    seen: list[str] = []
    for raw in raw_items:
        sym = raw.strip().upper()
        if sym and sym not in seen:
            seen.append(sym)
    return seen[:MAX_COMPARE]


def fx_pair(currency: str, base: str) -> str:
    """Yahoo symbol for base-currency units per one unit of `currency`."""
    return f'{currency}{base}=X'


def _series(entry: dict | None) -> pd.Series | None:
    if not entry or not entry.get('prices'):
        return None
    s = pd.Series(entry['prices'], index=pd.to_datetime(entry['dates']), dtype='float64')
    s = s[~s.index.duplicated(keep='last')].sort_index().dropna()
    return s if len(s) >= 2 else None


def build_value_series(positions: list[dict], base_ccy: str, prices: dict,
                       fx: dict) -> tuple[pd.Series | None, list[tuple[str, str]]]:
    """Base-currency value of the current holdings through time.

    `prices` / `fx` map symbol → {'dates', 'prices'} as returned by
    `get_price_history`.  Returns (series | None, [(ticker, reason excluded)]).
    Only dates on which every included position has a price are kept, so the
    series never mixes a partial set of holdings.
    """
    base = (base_ccy or 'EUR').upper()
    parts: dict[str, pd.Series] = {}
    excluded: list[tuple[str, str]] = []

    for p in positions:
        ticker = p.get('ticker')
        try:
            qty = float(p.get('quantity'))
            mult = float(p.get('multiplier'))
        except (TypeError, ValueError):
            excluded.append((ticker, 'not a plain stock/ETF position'))
            continue
        if not (math.isfinite(qty) and math.isfinite(mult) and mult > 0) or qty == 0:
            excluded.append((ticker, 'not a plain stock/ETF position'))
            continue
        px = _series(prices.get(ticker))
        if px is None:
            excluded.append((ticker, 'no price history'))
            continue
        ccy = (p.get('currency') or base).upper()
        value = px * qty * mult
        if ccy != base:
            rate = _series(fx.get(fx_pair(ccy, base)))
            if rate is None:
                excluded.append((ticker, f'no {ccy}/{base} history'))
                continue
            value = value * rate.reindex(value.index.union(rate.index)).ffill().reindex(value.index)
        parts[ticker] = value

    if not parts:
        return None, excluded
    frame = pd.DataFrame(parts).sort_index().ffill()
    if len(frame) < 2:
        return None, excluded + [('portfolio', 'holdings share too little price history')]

    # Where every holding has a price the value is the plain sum.  Before a
    # recent listing the series is chained backwards from the holdings that
    # did trade, so a young holding no longer truncates every longer period:
    # value(d) = value(last) / prod(daily growth of the then-available set).
    actual = frame.sum(axis=1, min_count=frame.shape[1])
    prev = frame.shift(1)
    both = frame.notna() & prev.notna()
    num, den = frame.where(both).sum(axis=1), prev.where(both).sum(axis=1)
    growth = (num / den).where(den > 0, 1.0)
    later = (1 / growth.iloc[::-1]).cumprod().shift(1).fillna(1.0).iloc[::-1]
    chained = later * frame.iloc[-1].sum()
    value = actual.where(actual.notna(), chained)
    if value.isna().any() or len(value) < 2:
        return None, excluded + [('portfolio', 'holdings share too little price history')]

    full_start = actual.first_valid_index()
    value.attrs['chained'] = sorted(
        ((t, frame[t].first_valid_index()) for t in frame.columns
         if frame[t].first_valid_index() > frame.index[0] + pd.Timedelta('7D')),
        key=lambda item: item[1])
    value.attrs['full_start'] = full_start
    return value, excluded


def compare_frame(portfolio: pd.Series, others: dict[str, pd.Series], mode: str) -> pd.DataFrame:
    """Align everything to a common start and express it in `mode`.

    mode 'pct': cumulative return in % (0 at the common start).
    mode 'abs': portfolio value in base currency; each instrument is rebased to
    the portfolio's starting value (the same capital invested in it).
    Columns are 'Portfolio' then each symbol that has data in the window.
    """
    series = {'Portfolio': portfolio}
    for name, s in others.items():
        aligned = s.reindex(portfolio.index.union(s.index)).ffill().reindex(portfolio.index)
        series[name] = aligned
    frame = pd.DataFrame(series).dropna(how='any')   # common window only
    if len(frame) < 2:
        return pd.DataFrame()
    rebased = frame / frame.iloc[0]
    if mode == 'abs':
        return rebased * frame['Portfolio'].iloc[0]
    return (rebased - 1) * 100


# ── Network ───────────────────────────────────────────────────────────────────

def get_portfolio_history(positions: list[dict], base_ccy: str, period: str,
                          start=None, end=None):
    """Fetch prices + FX (cached) and build the value series.

    For ``period == CUSTOM`` the 5Y download (shared cache with the 5Y button) is
    clipped to ``[start, end]``; FX gets a week of look-back so the first day is
    always convertible.
    """
    custom = period == CUSTOM
    yf_period = '5y' if custom else PERIODS.get(period, PERIODS[DEFAULT_PERIOD])
    base = (base_ccy or 'EUR').upper()
    tickers = sorted({p['ticker'] for p in positions if p.get('ticker')})
    pairs = sorted({fx_pair(p['currency'].upper(), base) for p in positions
                    if p.get('currency') and p['currency'].upper() != base})
    history = get_price_history(tickers + pairs, yf_period) if tickers else {}
    prices = {t: history.get(t) for t in tickers}
    fx = {pair: history.get(pair) for pair in pairs}
    if custom:
        prices = {t: clip_window(e, start, end) for t, e in prices.items()}
        fx = {k: clip_window(e, start, end, pad_days=7) for k, e in fx.items()}
    return build_value_series(positions, base, prices, fx)


def get_compare_history(symbol: str, period: str, start=None, end=None) -> pd.Series | None:
    """Adjusted daily closes for one user-supplied Yahoo Finance symbol.

    Returns None when Yahoo has nothing for it.  Failures raise inside the
    cached function so they are not cached for four hours.
    """
    import yfinance as yf

    custom = period == CUSTOM
    yf_period = '5y' if custom else PERIODS.get(period, PERIODS[DEFAULT_PERIOD])

    def fetch():
        hist = yf.Ticker(symbol).history(period=yf_period, interval='1d', auto_adjust=True)
        close = hist['Close'].dropna() if hist is not None and not hist.empty else None
        if close is None or len(close) < 2:
            raise LookupError(symbol)
        idx = close.index.tz_localize(None) if close.index.tz else close.index
        return {'dates': [d.strftime('%Y-%m-%d') for d in idx],
                'prices': [round(float(v), 6) for v in close]}

    try:
        entry = cached_fetch(('compare', symbol, yf_period), _TTL, fetch)
        return _series(clip_window(entry, start, end) if custom else entry)
    except Exception as e:
        log.debug('compare history %s unavailable: %s', symbol, e)
        return None


def get_estr_history() -> pd.Series | None:
    """Daily €STR (euro short-term rate, % p.a.) from the ECB, covering the max lookback."""
    def fetch():
        start = custom_bounds()[0].isoformat()
        req = urllib.request.Request(_ESTR_URL.format(start=start), headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            rows = [r for r in csv.DictReader(io.StringIO(resp.read().decode())) if r.get('OBS_VALUE')]
        if len(rows) < 2:
            raise LookupError('estr')
        return {'dates': [r['TIME_PERIOD'] for r in rows], 'prices': [float(r['OBS_VALUE']) for r in rows]}

    try:
        return _series(cached_fetch(('estr',), _TTL, lambda: fetch_with_retry(fetch, retries=2)))
    except Exception as e:
        log.debug('ESTR history unavailable: %s', e)
        return None


def search_instruments(query: str, limit: int = 8) -> list[dict]:
    """Yahoo Finance symbol search: stocks, ETFs, indices, crypto, futures, FX.

    Returns [{'symbol', 'name', 'exchange', 'type'}], best match first, or []
    when the query is empty or Yahoo is unreachable (failures are not cached).
    """
    import yfinance as yf

    query = (query or '').strip()
    if not query:
        return []

    def fetch():
        quotes = yf.Search(query, max_results=limit, news_count=0, lists_count=0,
                           include_cb=False, enable_fuzzy_query=False, timeout=10).quotes
        return [{'symbol': q['symbol'],
                 'name': q.get('longname') or q.get('shortname') or '',
                 'exchange': q.get('exchDisp') or q.get('exchange') or '',
                 'type': (q.get('quoteType') or '').title()}
                for q in quotes if q.get('symbol')]

    try:
        return cached_fetch(('search', query.lower(), limit), _SEARCH_TTL, fetch)
    except Exception as e:
        log.debug('instrument search %r failed: %s', query, e)
        return []
