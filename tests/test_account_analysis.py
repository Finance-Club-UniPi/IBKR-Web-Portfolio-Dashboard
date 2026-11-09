"""Known-answer account-equity, signed-exposure and dated-valuation checks."""

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from backend.portfolio.analysis import exposure_returns
from backend.portfolio.positions import get_summary, process_positions
from backend.portfolio.risk import compute_risk_metrics
from backend.portfolio.valuation import value_positions


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch):
    import backend.providers.market_data as market
    import backend.storage.cache as cache
    monkeypatch.setattr(cache, '_DISK', None)
    monkeypatch.setattr(cache, '_MEM', {})
    monkeypatch.setattr(market, '_POSITION_SYMBOLS', {})
    monkeypatch.setattr(market, '_YF_SYM_CACHE', {})


def prices():
    r = np.random.default_rng(3).normal(.001, .01, 100)
    return pd.Series(100 * np.cumprod(1 + r), index=pd.bdate_range('2024-01-01', periods=100))


@pytest.mark.parametrize('exposure,equity,beta', [(100, 100, 1), (-100, 100, -1), (200, 100, 2), (100, 200, .5)])
def test_signed_exposure_beta(exposure, equity, beta):
    series = prices()
    positions = [{'ticker': 'X', 'conId': 1, 'currency': 'USD', 'base_market_value': exposure}]
    returns = exposure_returns(positions, {'base_currency': 'USD', 'net_liquidation': equity}, {':1': series}, {})
    assert np.cov(returns, series.pct_change().dropna(), ddof=1)[0, 1] / series.pct_change().var() == pytest.approx(beta)


def test_market_neutral_and_foreign_cash():
    series = prices()
    positions = [{'ticker': 'X', 'conId': 1, 'base_market_value': 100},
                 {'ticker': 'Y', 'conId': 2, 'base_market_value': -100}]
    account = {'base_currency': 'USD', 'net_liquidation': 100}
    returns = exposure_returns(positions, account, {':1': series, ':2': series}, {})
    assert (returns == 0).all()
    account.update(cash_balances={'EUR': 200}, valuation_rates={'EUR': {'now': .5, 'as_of': datetime.now(timezone.utc).isoformat()}})
    returns = exposure_returns(positions, account, {':1': series, ':2': series}, {'EUR': series})
    assert returns.tolist() == pytest.approx(series.pct_change().dropna().tolist())


def test_current_valuation_needs_no_previous_fx_and_cache_matches_quantity():
    stamp = datetime.now(timezone.utc).isoformat()
    p = {'account': 'TEST', 'ticker': 'X', 'conId': 1, 'currency': 'USD', 'quantity': -2,
         'multiplier': 1, 'fx_native_price': 100, 'price_unit_verified': True, 'valuation_as_of': stamp}
    account = {'account_id': 'TEST', 'base_currency': 'EUR'}
    value_positions([p], account, {'USD': {'now': .8, 'as_of': stamp}})
    assert p['base_market_value'] == -160
    missing = {**p, 'fx_native_price': None}
    value_positions([missing], account, {})
    assert missing['base_market_value'] == -160 and missing['valuation_source'] == 'Last successful valuation'
    changed = {**missing, 'quantity': -3}
    value_positions([changed], account, {})
    assert changed['base_market_value'] is None


def test_expired_price_and_not_ready_cannot_create_new_valuation():
    stamp = (datetime.now(timezone.utc) - timedelta(days=6)).isoformat()
    p = {'ticker': 'X', 'conId': 1, 'currency': 'EUR', 'quantity': 2, 'multiplier': 1,
         'fx_native_price': 100, 'price_unit_verified': True, 'valuation_as_of': stamp}
    value_positions([p], {'base_currency': 'EUR'}, {})
    assert p['base_market_value'] is None


def test_short_profit_sign_and_gross_allocation():
    positions = [dict(ticker=t, conId=i, quantity=q, current_price=90, avg_cost=100,
                      base_market_value=q * 90, market_value=q * 90, unrealized_pnl=0, realized_pnl=0)
                 for t, i, q in [('LONG', 1, 1), ('SHORT', 2, -1)]]
    frame = process_positions(positions, equity=100)
    assert frame.set_index('ticker').loc['SHORT', 'pnl_pct'] == 10
    assert frame.set_index('ticker').loc['LONG', 'pnl_pct'] == -10
    assert frame.allocation_pct.tolist() == [50, 50]
    assert sorted(frame.signed_exposure_pct) == [-90, 90]
    assert get_summary(frame)['portfolio_beta'] is None  # Comes from the common history analysis only.


def test_drawdown_and_var_accept_negative_beta_returns():
    series = prices()
    returns = -series.pct_change().dropna()
    values = pd.Series(np.r_[100, 100 * (1 + returns).cumprod()], index=series.index)
    result = compute_risk_metrics(values, series, 0, budget=100)
    assert result['beta'] == pytest.approx(-1)
    assert result['var_hist_95_amt'] == pytest.approx(100 * result['var_hist_95'])


def test_benchmark_calendar_uses_the_same_multi_day_return_intervals():
    series = prices()
    trimmed = series.drop(series.index[1])
    account = {'base_currency': 'USD', 'net_liquidation': 100}
    positions = [{'ticker': 'X', 'conId': 1, 'base_market_value': 200}]
    returns = exposure_returns(positions, account, {':1': series}, {}, calendar=trimmed.index)
    assert returns.tolist() == pytest.approx((2 * trimmed.pct_change().dropna()).tolist())
    assert returns.attrs['start'] == trimmed.index[0]


def test_listing_history_checks_chart_currency_and_normalizes_pence(monkeypatch):
    import yfinance as yf

    import backend.providers.market_data as market
    seen = []

    class Instrument:
        def __init__(self, symbol):
            seen.append(symbol)

        def history(self, **kwargs):
            return pd.DataFrame({'Close': [100, 110]}, index=pd.bdate_range('2024-01-01', periods=2))

        def get_history_metadata(self):
            return {'currency': 'GBp'}

    monkeypatch.setattr(yf, 'Ticker', Instrument)
    result = market.get_position_history({'ticker': 'TEST', 'currency': 'GBP', 'primary_exchange': 'LSE'}, '1y')
    assert result['prices'] == [1, 1.1] and seen == ['TEST.L']
    assert market.get_position_history({'ticker': 'TEST', 'currency': 'EUR', 'primary_exchange': 'LSE'}, '1y') is None


def test_london_etf_history_uses_the_broker_listing_without_plain_symbol_probes(monkeypatch):
    import yfinance as yf

    from backend.providers.market_data import get_position_history
    seen = []

    class Instrument:
        def __init__(self, symbol):
            seen.append(symbol)

        def history(self, **kwargs):
            assert kwargs['period'] == '1y'
            return pd.DataFrame({'Close': [100, 110]}, index=pd.bdate_range('2026-01-01', periods=2))

        def get_history_metadata(self):
            return {'currency': 'USD'}

    monkeypatch.setattr(yf, 'Ticker', Instrument)
    result = get_position_history({'ticker': 'VUAA', 'currency': 'USD', 'primary_exchange': 'LSEETF'}, '1y')
    assert result['symbol'] == 'VUAA.L' and result['currency'] == 'USD'
    assert seen == ['VUAA.L']


def test_metadata_betas_and_dividends_share_the_broker_listing(monkeypatch):
    import yfinance as yf

    from backend.providers import market_data as market
    from backend.providers.dividends import _fetch_one_dividend
    seen = []

    class Instrument:
        def __init__(self, symbol):
            seen.append(symbol)

        @property
        def info(self):
            return {'quoteType': 'ETF', 'beta3Year': 1.1, 'dividendRate': 2}

    monkeypatch.setattr(yf, 'Ticker', Instrument)
    market.set_position_symbols([{'ticker': 'VUAA', 'primary_exchange': 'LSEETF'}])
    assert market._resolve_yf_sym('VUAA') == 'VUAA.L'
    assert market.get_betas(['VUAA']) == {'VUAA': 1.1}
    assert _fetch_one_dividend('VUAA')['next_12m'] == 2
    assert seen == ['VUAA.L']


def test_conflicting_listings_do_not_create_a_ticker_only_hint():
    from backend.providers import market_data as market
    market.set_position_symbols([{'ticker': 'SAME', 'primary_exchange': exchange}
                                 for exchange in ('LSEETF', 'IBIS')])
    assert 'SAME' not in market._POSITION_SYMBOLS


def test_failed_metadata_is_retried_instead_of_cached(monkeypatch):
    import yfinance as yf

    from backend.providers import market_data as market
    replies = iter([{}, {'quoteType': 'ETF'}])
    monkeypatch.setattr(yf, 'Ticker', lambda symbol: type('Instrument', (), {'info': next(replies)})())
    assert market._get_info('TEST.L') == {}
    assert market._get_info('TEST.L') == {'quoteType': 'ETF'}


def test_failed_symbol_resolution_can_recover_later(monkeypatch):
    from backend.providers import market_data as market
    monkeypatch.setattr(market, '_get_info', lambda symbol: {})
    assert market._resolve_yf_sym('UNKNOWN') == 'UNKNOWN'
    assert 'UNKNOWN' not in market._YF_SYM_CACHE
    monkeypatch.setattr(market, '_get_info', lambda symbol: {'quoteType': 'ETF'} if symbol == 'UNKNOWN.L' else {})
    assert market._resolve_yf_sym('UNKNOWN') == 'UNKNOWN.L'


def test_two_same_tickers_keep_distinct_currencies_and_values():
    stamp = datetime.now(timezone.utc).isoformat()
    positions = [dict(ticker='SAME', conId=i, currency=ccy, quantity=1, multiplier=1,
                      fx_native_price=100, price_unit_verified=True, valuation_as_of=stamp)
                 for i, ccy in [(1, 'EUR'), (2, 'USD')]]
    value_positions(positions, {'account_id': 'TEST', 'base_currency': 'EUR'}, {'USD': {'now': .5, 'as_of': stamp}})
    assert [p['base_market_value'] for p in positions] == [100, 50]
    assert positions[0]['position_id'] != positions[1]['position_id']


def test_yesterday_fx_fallback_does_not_change_current_rate(monkeypatch):
    import backend.portfolio.history as history
    from backend.providers.fx_history import complete_daily_rates
    stamp = datetime.now(timezone.utc)
    series = pd.Series([.7, .9], index=pd.to_datetime([(stamp - timedelta(days=1)).date(), stamp.date()]))
    monkeypatch.setattr(history, 'get_compare_history', lambda *args: series)
    rates = complete_daily_rates('EUR', {'USD': {'now': .8, 'as_of': stamp.isoformat()}}, {})
    assert rates['USD']['now'] == .8 and rates['USD']['previous'] == .7


def test_cash_balance_change_invalidates_analysis_cache():
    from backend.portfolio.analysis import fingerprint
    data = {'positions': [], 'account': {'cash_balances': {'USD': 100},
                                        'valuation_rates': {'USD': {'now': .8}}}}
    first = fingerprint(data, '1Y')
    data['account']['cash_balances']['USD'] = 200
    assert first != fingerprint(data, '1Y')


def test_quote_changes_do_not_restart_background_analysis(monkeypatch):
    import copy
    from concurrent.futures import Future
    from types import SimpleNamespace

    import backend.portfolio.analysis as analysis
    submitted = []

    def submit(*args):
        job = Future()
        submitted.append(job)
        return job

    monkeypatch.setattr(analysis, '_POOL', SimpleNamespace(submit=submit))
    monkeypatch.setattr(analysis, '_JOBS', {})
    monkeypatch.setattr(analysis, '_RESULTS', {})
    data = {'positions': [{'ticker': 'X', 'conId': 1, 'quantity': 2, 'base_market_value': 100},
                          {'ticker': 'Y', 'conId': 2, 'quantity': 1, 'base_market_value': 30}],
            'account': {'base_currency': 'USD', 'net_liquidation': 200}}
    assert analysis.request_analysis(data)['status'] == 'loading'
    changed = copy.deepcopy(data)
    changed['positions'][0]['base_market_value'] = 110
    changed['account']['net_liquidation'] = 210
    changed['positions'].reverse()
    assert analysis.request_analysis(changed)['status'] == 'loading'
    assert len(submitted) == 1
    stamp = datetime.now(timezone.utc).isoformat()
    submitted[0].set_result({'status': 'ready', 'as_of': stamp, 'valuation_as_of': stamp, 'metrics': {'beta': .5}})
    assert analysis.request_analysis(changed)['metrics']['beta'] == .5
    changed['positions'][0]['quantity'] = 3
    assert analysis.request_analysis(changed)['status'] == 'loading'
    assert len(submitted) == 2


def test_valuation_refresh_replaces_an_expired_fx_rate(monkeypatch):
    import backend.portfolio.history as history
    from backend.portfolio.valuation import refresh_valuations
    stamp = datetime.now(timezone.utc).isoformat()
    old = (datetime.now(timezone.utc) - timedelta(days=6)).isoformat()
    p = {'account': 'TEST', 'conId': 1, 'ticker': 'X', 'quantity': 2, 'currency': 'USD',
         'multiplier': 1, 'price_unit_verified': True, 'fx_native_price': 100, 'valuation_as_of': stamp}
    monkeypatch.setattr(history, 'get_compare_history', lambda *args: pd.Series(
        [.8], index=[pd.Timestamp.now(tz='UTC').normalize().tz_localize(None)]))
    refresh_valuations([p], {'account_id': 'TEST', 'base_currency': 'EUR'}, {'USD': {'now': .1, 'as_of': old}})
    value_positions([p], {'account_id': 'TEST', 'base_currency': 'EUR'}, {})
    assert p['base_market_value'] == 160
