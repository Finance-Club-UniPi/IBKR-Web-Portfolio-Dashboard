"""One account-equity historical risk calculation, shared by UI and reports."""

import copy
import hashlib
import json
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from backend.network import run_parallel
from backend.portfolio.history import PERIODS, _series, fx_pair, get_compare_history, get_estr_history
from backend.portfolio.risk import compute_risk_metrics, rf_over, to_base
from backend.portfolio.valuation import position_key, recent
from backend.providers.market_data import get_position_history

_POOL = ThreadPoolExecutor(max_workers=2)
_LOCK = threading.Lock()
_JOBS = {}
_RESULTS = {}


def fingerprint(data, period):
    """Compatible book identity; changing quotes must not restart an in-flight job."""
    account = data.get('account', {})
    equity = account.get('net_liquidation')
    payload = {'period': period, 'account': {k: account.get(k) for k in ('account_id', 'base_currency', 'account_ready', 'cash_balances')},
               'equity_available': bool(equity and math.isfinite(equity) and equity > 0),
               'cash_fx_available': {ccy: bool(rate.get('now') and recent(rate.get('as_of')))
                                     for ccy, rate in account.get('valuation_rates', {}).items()
                                     if account.get('cash_balances', {}).get(ccy)}, 'positions': [
        {**{k: p.get(k) for k in ('account', 'conId', 'ticker', 'quantity', 'currency',
                                  'multiplier', 'primary_exchange')},
         'value_available': p.get('base_market_value') is not None}
        for p in sorted(data.get('positions', []), key=lambda p: position_key(p, account.get('account_id', '')))]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def exposure_returns(positions, account, prices, fx, calendar=None):
    """Fixed signed current exposures / current equity; foreign cash carries FX risk."""
    if account.get('account_ready') is False:
        raise ValueError('IBKR account update is not ready')
    equity = account.get('net_liquidation')
    if not equity or not math.isfinite(equity) or equity <= 0:
        raise ValueError('Positive account equity is required')
    base = account.get('base_currency', 'EUR')
    series, weights = {}, {}
    for p in positions:
        key = position_key(p, account.get('account_id', ''))
        value = p.get('base_market_value')
        if value is None or not math.isfinite(value):
            raise ValueError(f"Current valuation unavailable for {p['ticker']}")
        price = prices.get(key)
        if price is None or price.empty:
            raise ValueError(f"Price history unavailable for {p['ticker']} ({p.get('currency')})")
        currency = p.get('currency') or base
        if currency != base:
            price = to_base(price, fx.get(currency))
        if price is None:
            raise ValueError(f'FX history unavailable for {currency}/{base}')
        series[key], weights[key] = price, value / equity
    cash = account.get('cash_balances', {})
    current_fx = account.get('valuation_rates', {})
    for currency, balance in cash.items():
        if currency == base or not balance:
            continue
        rate = (current_fx.get(currency) or {}).get('now')
        if not rate or not math.isfinite(rate) or rate <= 0 or not recent(current_fx[currency].get('as_of')) or fx.get(currency) is None:
            raise ValueError(f'Cash FX history unavailable for {currency}/{base}')
        series['cash:' + currency], weights['cash:' + currency] = fx[currency], balance * rate / equity
    frame = pd.DataFrame(series).sort_index().dropna()
    if calendar is not None:
        frame = frame.loc[frame.index.intersection(calendar)]
    if len(frame) < 2:
        raise ValueError('No common price history')
    changes = frame.pct_change(fill_method=None).iloc[1:]
    returns = changes.mul(pd.Series(weights)).sum(axis=1)
    if not np.isfinite(returns).all() or (returns <= -1).any():
        raise ValueError('Historical scenario exhausts account equity; percentage risk is undefined')
    returns.attrs['start'] = frame.index[0]
    return returns


def load_scenarios(data, period):
    positions, account = data['positions'], data['account']
    base = account.get('base_currency', 'EUR')
    currencies = {p.get('currency') or base for p in positions} | set(account.get('cash_balances', {}))
    calls = {position_key(p, account.get('account_id', '')): (
        lambda p=p: _series(get_position_history(p, PERIODS[period]))) for p in positions}
    for ccy in currencies - {base}:
        calls['fx:' + ccy] = lambda ccy=ccy: get_compare_history(fx_pair(ccy, base), period)
    calls['bench'] = lambda: get_compare_history('^GSPC', period)
    if base != 'USD' and 'fx:USD' not in calls:
        calls['fx:USD'] = lambda: get_compare_history(fx_pair('USD', base), period)
    if base == 'EUR':
        calls['rf'] = get_estr_history
    elif base == 'USD':
        calls['rf'] = lambda: get_compare_history('^IRX', period)
    result = run_parallel(calls)
    fx = {k[3:]: v for k, v in result.items() if k.startswith('fx:')}
    benchmark = result.get('bench')
    if base != 'USD':
        benchmark = to_base(benchmark, result.get('fx:USD'))
    returns = exposure_returns(positions, account, result, fx,
                               calendar=benchmark.index if benchmark is not None else None)
    levels = pd.concat([pd.Series([1.0], index=[returns.attrs['start']]), (1 + returns).cumprod()])
    rf = rf_over(result.get('rf'), returns.index)
    return levels, benchmark, rf, returns


def account_history(data, period, start=None, end=None):
    levels, benchmark, _, _ = load_scenarios(data, '5Y' if period == 'Custom' else period)
    levels = levels / levels.iloc[-1] * data['account']['net_liquidation']
    if start is not None:
        levels = levels.loc[start:end]
    return levels, benchmark, []


def calculate_analysis(data, period):
    levels, benchmark, rf, returns = load_scenarios(data, period)
    metrics = compute_risk_metrics(levels, benchmark, rf or 0.0, budget=data['account']['net_liquidation'])
    if metrics['returns'] is None:
        raise ValueError('Need at least 30 common daily returns')
    return {'status': 'ready', 'metrics': metrics, 'period': period,
            'as_of': datetime.now(timezone.utc).isoformat(), 'history_end': str(returns.index[-1].date()),
            'valuation_as_of': min((p.get('valuation_as_of', '') for p in data['positions']), default=''),
            'reason': '' if metrics['beta'] is not None else 'Benchmark history unavailable or too short',
            'rf_available': rf is not None}


def request_analysis(data, period='1Y'):
    """Start once per input snapshot; poll without waiting on provider requests."""
    if not data or not data.get('positions'):
        return {'status': 'unavailable', 'reason': 'Waiting for portfolio data', 'period': period}
    key = fingerprint(data, period)
    with _LOCK:
        for old, job in list(_JOBS.items()):
            if old != key and (job.done() or job.cancel()):
                _JOBS.pop(old, None)
        cached = _RESULTS.get(key)
        if cached and cached.get('valuation_as_of') and not recent(cached['valuation_as_of']):
            cached = None
        age = ((datetime.now(timezone.utc) - datetime.fromisoformat(cached['as_of'])).total_seconds()
               if cached else float('inf'))
        if cached and (cached.get('retry_after', 0) > time.monotonic() or (cached['status'] == 'ready' and age < 4 * 3600)):
            return cached
        job = _JOBS.get(key)
        if job is None:
            job = _JOBS[key] = _POOL.submit(calculate_analysis, copy.deepcopy(data), period)
        if not job.done():
            if cached and cached['status'] == 'ready' and recent(cached['as_of']):
                return {**cached, 'reason': 'Using dated analysis while refreshing history'}
            return {'status': 'loading', 'reason': 'Historical analysis loading', 'period': period}
        try:
            result = job.result()
        except Exception as e:
            if cached and cached['status'] == 'ready' and recent(cached['as_of']):
                result = {**cached, 'reason': 'Provider unavailable; using last successful dated analysis',
                          'retry_after': time.monotonic() + 60}
            else:
                result = {'status': 'unavailable', 'reason': str(e), 'period': period,
                          'as_of': datetime.now(timezone.utc).isoformat(), 'retry_after': time.monotonic() + 60}
        result['input_key'] = key
        _RESULTS[key] = result
        _JOBS.pop(key, None)
        for old in list(_RESULTS)[:-8]:
            _RESULTS.pop(old, None)
        return result


def analysis_summary(result):
    metrics = result.get('metrics', {})
    return {k: result.get(k) for k in ('status', 'reason', 'period', 'as_of', 'history_end', 'valuation_as_of', 'input_key')} | {
        'beta': metrics.get('beta'), 'alpha': metrics.get('alpha'), 'n_obs': metrics.get('n_obs')}
