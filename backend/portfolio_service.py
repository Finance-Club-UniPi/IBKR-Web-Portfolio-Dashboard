"""Portfolio pipeline: broker/demo snapshot → enrichment → calculations → UI payload.

The two-second enrichment budget keeps refreshes responsive. A missing or
unfinished FX result stays unavailable; it never enables an aggregate estimate.
"""

import concurrent.futures

from backend.portfolio.positions import get_summary, process_positions
from backend.providers.dividends import get_dividend_data_yf
from backend.providers.fx_history import compute_trade_fx
from backend.providers.ibkr import fetch_all_data, is_demo_mode
from backend.providers.market_data import get_betas
from backend.storage.trades import load_uploaded_trades


def fetch_portfolio():
    """None means disconnected; an empty dict means connected with no positions."""
    raw = fetch_all_data()
    if raw is None:
        return None
    if not raw.get('positions'):
        return {}
    return prepare_portfolio(raw, demo=is_demo_mode())


def prepare_portfolio(raw, *, demo=False):
    # IBKR tick-59 data first; yfinance fills any gaps.  Betas come from
    # yfinance too.  Both run under one shared 2 s budget so a slow Yahoo
    # Finance response doesn't block the portfolio-data store.  The
    # background threads keep running and populate the 4-hour cache, so the
    # next 60 s tick picks up whatever missed the deadline.
    div_data = raw.get('div_data', {})
    tickers  = [p['ticker'] for p in raw['positions']]
    missing  = [t for t in tickers if t not in div_data]
    base_ccy = raw.get('account', {}).get('base_currency', 'EUR')
    _ex = concurrent.futures.ThreadPoolExecutor(max_workers=3)
    _div_fut  = _ex.submit(get_dividend_data_yf, missing) if missing else None
    _beta_fut = _ex.submit(get_betas, tickers)
    # Since-purchase FX fallback: trade history + Yahoo FX closes (cached).
    _fx_fut   = None if demo else _ex.submit(
        compute_trade_fx, raw['positions'], base_ccy,
        raw.get('trades') or [], load_uploaded_trades(),
        daily_fx_rates=raw.get('daily_fx_rates', {}))
    _ex.shutdown(wait=False)
    concurrent.futures.wait([f for f in (_div_fut, _beta_fut, _fx_fut) if f], timeout=2.0)
    if _div_fut is not None and _div_fut.done() and not _div_fut.exception():
        div_data.update(_div_fut.result())
    betas = _beta_fut.result() if _beta_fut.done() and not _beta_fut.exception() else {}
    trade_fx = (_fx_fut.result() if _fx_fut is not None and _fx_fut.done()
                and not _fx_fut.exception() else raw.get('trade_fx', {}))

    df = process_positions(
        raw['positions'], raw.get('market_data', {}),
        base_currency=base_ccy,
        daily_fx_rates=raw.get('daily_fx_rates', {}),
        betas=betas,
        trade_fx=trade_fx,
    )
    if df.empty:
        return {}
    summary = get_summary(df)
    return {
        'positions': df.to_dict('records'),
        'summary':   summary,
        'account':   raw['account'],
        'div_data':  div_data,
        'trades':    raw.get('trades', []),
    }
