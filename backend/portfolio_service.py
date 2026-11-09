"""Portfolio pipeline: broker/demo snapshot → enrichment → calculations → UI payload.

Enrichment runs in background workers. A missing or unfinished FX result stays
unavailable; it never enables an aggregate estimate.
"""

import concurrent.futures
import hashlib
import json
import threading

from backend.portfolio.positions import get_summary, process_positions
from backend.portfolio.valuation import recent, refresh_valuations, value_positions
from backend.providers.dividends import get_dividend_data_yf
from backend.providers.flex import read_history, save_history, schedule_sync
from backend.providers.fx_history import complete_daily_rates, compute_trade_fx
from backend.providers.ibkr import fetch_all_data, is_demo_mode
from backend.providers.market_data import get_betas, set_position_symbols
from backend.storage.trades import load_uploaded_trades

_ENRICH_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=3)
_ENRICH_JOBS = {}
_ENRICH_LOCK = threading.Lock()


def enrichment(kind, inputs, fn, *args, **kwargs):
    key = (kind, hashlib.sha256(json.dumps(inputs, sort_keys=True, default=str).encode()).hexdigest())
    with _ENRICH_LOCK:
        job = _ENRICH_JOBS.get(key)
        if job is None:
            if any(k[0] == kind and not old.done() for k, old in _ENRICH_JOBS.items()):
                return None
            job = _ENRICH_JOBS[key] = _ENRICH_POOL.submit(fn, *args, **kwargs)
        if not job.done() or job.exception():
            if job.done():
                _ENRICH_JOBS.pop(key, None)
            return None
        for old in list(_ENRICH_JOBS)[:-12]:
            if _ENRICH_JOBS[old].done():
                _ENRICH_JOBS.pop(old, None)
        return job.result()


_VALUATIONS = concurrent.futures.ThreadPoolExecutor(max_workers=1)
_VAL_JOB = None


def fetch_portfolio():
    """None means disconnected; an empty dict means connected with no positions."""
    raw = fetch_all_data()
    if raw is None:
        return None
    if not raw.get('positions'):
        return {}
    return prepare_portfolio(raw, demo=is_demo_mode())


def prepare_portfolio(raw, *, demo=False):
    set_position_symbols(raw['positions'])
    # Optional providers finish off the request path; subsequent polls collect results.
    div_data = raw.get('div_data', {})
    tickers  = [p['ticker'] for p in raw['positions']]
    missing  = [t for t in tickers if t not in div_data]
    base_ccy = raw.get('account', {}).get('base_currency', 'EUR')
    global _VAL_JOB
    raw['account']['valuation_rates'] = raw.get('valuation_rates', {})
    if not demo:
        value_positions(raw['positions'], raw['account'], raw.get('valuation_rates', {}))
        if any(p.get('base_market_value') is None for p in raw['positions']) and (_VAL_JOB is None or _VAL_JOB.done()):
            _VAL_JOB = _VALUATIONS.submit(refresh_valuations, raw['positions'], raw['account'], raw.get('valuation_rates', {}))
        for p in raw['positions']:
            if p.get('valuation_rate') and not recent(raw['account']['valuation_rates'].get(p.get('currency'), {}).get('as_of')):
                raw['account']['valuation_rates'][p['currency']] = {'now': p['valuation_rate'], 'as_of': p['valuation_as_of'], 'source': p['valuation_source']}
    history_status = ''
    uploaded = []
    try:
        uploaded = load_uploaded_trades()
    except ValueError as e:
        history_status = str(e)
    lot_state = None
    account_id = raw['account'].get('account_id')
    if not demo and account_id:
        try:
            if raw.get('trades'):
                save_history(account_id, raw['trades'])
            lot_state = read_history()['accounts'].get(account_id, {})
            history_status = history_status or schedule_sync(account_id)
        except (ValueError, OSError) as e:
            history_status = str(e)
    daily_rates = raw.get('daily_fx_rates', {})
    if not demo:
        daily_rates = enrichment('daily_fx', raw['account'].get('valuation_rates', {}), complete_daily_rates,
                                 base_ccy, raw['account'].get('valuation_rates', {}), daily_rates) or daily_rates
    if missing:
        div_data.update(enrichment('dividends', missing, get_dividend_data_yf, missing) or {})
    betas = enrichment('betas', tickers, get_betas, tickers) or {}
    trade_fx = raw.get('trade_fx', {})
    if not demo:
        inputs = {'positions': [{k: p.get(k) for k in ('position_id', 'quantity', 'valuation_price', 'valuation_rate')}
                                for p in raw['positions']], 'lots': lot_state, 'trades': raw.get('trades'),
                  'executions_from': raw.get('executions_from')}
        trade_fx = enrichment('lot_fx', inputs, compute_trade_fx, raw['positions'], base_ccy,
                              raw.get('trades') or [], uploaded,
                              daily_fx_rates=raw.get('valuation_rates', raw.get('daily_fx_rates', {})),
                              lot_state=lot_state, executions_from=raw.get('executions_from')) or {}

    df = process_positions(
        raw['positions'], raw.get('market_data', {}),
        base_currency=base_ccy,
        daily_fx_rates=daily_rates,
        betas=betas,
        trade_fx=trade_fx, equity=raw['account'].get('net_liquidation'),
    )
    if df.empty:
        return {}
    summary = get_summary(df)
    dated = sorted({rate['as_of'][:10] for ccy, rate in daily_rates.items() if ccy != base_ccy and rate.get('as_of')})
    if dated:
        from datetime import datetime, timezone
        summary['fx_label'] = 'FX today' if dated[0] == datetime.now(timezone.utc).date().isoformat() else f'FX change ({dated[0]})'
    return {
        'positions': df.to_dict('records'),
        'summary':   summary,
        'history_status': history_status,
        'account':   raw['account'],
        'div_data':  div_data,
        'trades':    [t for t in lot_state.get('trades', []) if not t.get('cancelled')] if lot_state else raw.get('trades', []),
    }
