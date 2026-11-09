"""Portfolio history backcast, comparison alignment, and the performance callback."""

import pandas as pd
import pytest

import backend.portfolio.history as ph
from backend.portfolio.history import build_value_series, compare_frame, parse_symbols
from backend.portfolio.positions import get_summary, process_positions
from backend.providers.demo import build_demo_payload

DATES = ['2026-01-05', '2026-01-06', '2026-01-07', '2026-01-08']


def entry(prices, dates=DATES):
    return {'dates': list(dates), 'prices': list(prices)}


def pos(ticker, qty, currency='EUR', multiplier=1.0):
    return dict(ticker=ticker, quantity=qty, currency=currency, multiplier=multiplier)


def test_value_series_sums_positions_and_converts_fx():
    prices = {'AAA': entry([10, 11, 12, 13]), 'BBB': entry([100, 100, 110, 110])}
    fx = {'USDEUR=X': entry([0.5, 0.5, 0.4, 0.4])}
    series, excluded = build_value_series(
        [pos('AAA', 10), pos('BBB', 2, 'USD')], 'EUR', prices, fx)
    assert excluded == []
    # AAA: 10 * price (EUR); BBB: 2 * price * USDEUR
    assert series.round(2).tolist() == [100 + 100, 110 + 100, 120 + 88, 130 + 88]


def test_missing_data_is_excluded_with_a_reason_not_zeroed():
    prices = {'AAA': entry([10, 11, 12, 13]), 'BBB': None, 'CCC': entry([5, 5, 5, 5])}
    series, excluded = build_value_series(
        [pos('AAA', 1), pos('BBB', 1), pos('CCC', 1, 'GBP'), pos('FUT', 1, multiplier=None)],
        'EUR', prices, {})
    assert series.tolist() == [10, 11, 12, 13]
    reasons = dict(excluded)
    assert 'no price history' in reasons['BBB']
    assert 'GBP/EUR' in reasons['CCC']
    assert 'plain stock' in reasons['FUT']


def test_short_history_holding_chains_instead_of_truncating_the_series():
    prices = {'AAA': entry([10, 11, 12, 13]), 'BBB': entry([100, 110], DATES[2:])}
    series, _ = build_value_series([pos('AAA', 1), pos('BBB', 1)], 'EUR', prices, {})
    assert list(series.index.strftime('%Y-%m-%d')) == DATES
    # Real sums where both exist; earlier days scaled back by AAA's own growth.
    assert series.round(4).tolist()[2:] == [112, 123]
    expected_first = 112 / (12 / 11) / (11 / 10)
    assert series.iloc[0] == pytest.approx(expected_first)
    assert series.iloc[0] / series.iloc[1] == pytest.approx(10 / 11)
    assert series.attrs['chained'] == []   # < 7 days apart is treated as calendar noise
    # Nothing priceable at all → no series
    assert build_value_series([pos('ZZZ', 1)], 'EUR', {}, {})[0] is None


def test_chained_history_is_reported_when_a_holding_listed_much_later():
    long_dates = [d.strftime('%Y-%m-%d') for d in pd.date_range('2026-01-01', periods=30)]
    prices = {'AAA': entry(range(10, 40), long_dates),
              'BBB': entry([5, 6, 7], long_dates[-3:])}
    series, _ = build_value_series([pos('AAA', 1), pos('BBB', 1)], 'EUR', prices, {})
    assert len(series) == 30
    assert [t for t, _ in series.attrs['chained']] == ['BBB']
    assert series.attrs['full_start'] == pd.Timestamp(long_dates[-3])


def test_compare_frame_pct_and_abs_share_a_common_start():
    idx = pd.to_datetime(DATES)
    portfolio = pd.Series([200.0, 210, 220, 230], index=idx)
    bench = pd.Series([50.0, 55, 50, 60], index=idx)
    pct = compare_frame(portfolio, {'SPY': bench}, 'pct')
    assert pct['Portfolio'].round(2).tolist() == [0, 5, 10, 15]
    assert pct['SPY'].round(2).tolist() == [0, 10, 0, 20]
    absolute = compare_frame(portfolio, {'SPY': bench}, 'abs')
    assert absolute['Portfolio'].round(2).tolist() == [200, 210, 220, 230]
    assert absolute['SPY'].round(2).tolist() == [200, 220, 200, 240]   # same 200 invested


def test_compare_frame_handles_different_calendars_and_late_starts():
    idx = pd.to_datetime(DATES)
    portfolio = pd.Series([100.0, 110, 120, 130], index=idx)
    crypto = pd.Series([1.0, 2.0], index=pd.to_datetime(['2026-01-06', '2026-01-08']))  # sparse
    frame = compare_frame(portfolio, {'X': crypto}, 'pct')
    assert list(frame.index.strftime('%Y-%m-%d')) == DATES[1:]       # starts where X exists
    assert frame['Portfolio'].round(2).tolist() == [0, 9.09, 18.18]
    assert frame['X'].tolist() == [0, 0, 100]                         # forward-filled on 01-07


def test_parse_symbols():
    assert parse_symbols(' spy, QQQ  btc-usd;spy ') == ['SPY', 'QQQ', 'BTC-USD']
    assert parse_symbols('') == [] and parse_symbols(None) == []
    assert len(parse_symbols('a b c d e f g')) == ph.MAX_COMPARE


def test_demo_holdings_build_the_performance_figure(monkeypatch):
    import ui.callbacks.performance as perf
    monkeypatch.setattr(perf, 'account_history', lambda holdings, *args: (*ph.get_portfolio_history(holdings['positions'], holdings['base'], '1Y')[:1], None, []))
    raw = build_demo_payload('balanced')
    df = process_positions(raw['positions'], raw['market_data'], base_currency='EUR',
                           daily_fx_rates=raw['daily_fx_rates'])
    data = {'positions': df.to_dict('records'), 'account': raw['account']}
    holdings = perf._holdings_payload(data)
    assert holdings['base'] == 'EUR' and len(holdings['positions']) == 8

    idx = [d.strftime('%Y-%m-%d') for d in pd.bdate_range('2026-01-01', periods=30)]
    fake = {p['ticker']: entry([100 + i for i in range(30)], idx) for p in holdings['positions']}
    fake['USDEUR=X'] = entry([0.9] * 30, idx)
    monkeypatch.setattr(ph, 'get_price_history', lambda syms, period: fake)
    bench = pd.Series([50 + i for i in range(30)], index=pd.to_datetime(idx))
    monkeypatch.setattr(perf, 'get_compare_history',
                        lambda s, period: bench if s == 'SPY' else None)

    fig, stats, note = perf.build_performance(holdings, '1Y', 'pct', 'spy, nope')
    assert [t.name for t in fig.data] == ['Portfolio', 'SPY']
    assert fig.data[0].y[0] == 0 and fig.data[0].y[-1] > 0
    assert len(stats) == 2 and 'No data found for: NOPE' in note

    fig, _, note = perf.build_performance(holdings, '1Y', 'abs', 'SPY')
    assert fig.data[1].name == 'SPY (same start capital)'
    assert fig.data[0].y[0] == pytest.approx(fig.data[1].y[0])
    assert 'same starting capital' in note

    fig, stats, note = perf.build_performance(None, '1Y', 'pct', '')
    assert len(fig.data) == 0 and stats == []


def test_todays_pnl_card_shows_percentage_of_yesterdays_nlv():
    from ui.callbacks.summary import register

    callbacks = {}

    class App:
        def callback(self, *args, **kwargs):
            def save(fn):
                callbacks[fn.__name__] = fn
                return fn
            return save

    register(App())
    raw = build_demo_payload('balanced')
    df = process_positions(raw['positions'], raw['market_data'], base_currency='EUR',
                           daily_fx_rates=raw['daily_fx_rates'])
    data = {'positions': df.to_dict('records'), 'summary': get_summary(df), 'account': raw['account']}
    card = next(c for c in callbacks['update_summary'](data, 'connected')
                if c.children[0].children == "Today's P&L")
    sub = ''.join(str(s.children) for s in card.children[2].children if s is not None)
    acct = raw['account']
    expected = acct['daily_pnl'] / (acct['net_liquidation'] - acct['daily_pnl']) * 100
    assert f"{expected:+.2f}%" in sub

    # No daily P&L available → no percentage rather than a bogus one.
    data['account'] = {**acct, 'daily_pnl': None}
    data['summary'] = {**data['summary'], 'total_daily_pnl': None}
    card = next(c for c in callbacks['update_summary'](data, 'connected')
                if c.children[0].children == "Today's P&L")
    assert '%' not in ''.join(str(s.children) for s in card.children[2].children if s is not None)


def test_parse_symbols_accepts_picker_lists():
    assert parse_symbols(['spy', 'BTC-USD', 'SPY']) == ['SPY', 'BTC-USD']
    assert parse_symbols([]) == []


def test_search_options_merge_results_selection_and_typed_symbol(monkeypatch):
    import ui.callbacks.performance as perf
    hits = [{'symbol': 'AAPL', 'name': 'Apple Inc.', 'exchange': 'NASDAQ', 'type': 'Equity'},
            {'symbol': 'AAPL.TO', 'name': 'APPLE CDR', 'exchange': 'Toronto', 'type': 'Equity'}]
    monkeypatch.setattr(perf, 'search_instruments', lambda q: hits)

    options = perf.search_options('apple', [], [])
    assert [o['value'] for o in options] == ['AAPL', 'AAPL.TO', 'APPLE']   # typed symbol offered last
    assert 'Apple Inc.' in options[0]['label'] and 'NASDAQ' in options[0]['label']
    # Every option stays searchable by what was typed, whatever Yahoo matched on.
    assert all('apple' in o['search'].lower() for o in options)

    # Existing selection keeps its (previously rich) label and is not offered twice.
    prev = [{'label': 'SPY — SPDR S&P 500', 'value': 'SPY'}]
    options = perf.search_options('apple', ['SPY'], prev)
    assert options[0] == prev[0] and [o['value'] for o in options].count('SPY') == 1
    # A selection with no remembered option still gets one, even with an empty search.
    assert perf.search_options('', ['QQQ'], []) == [{'label': 'QQQ', 'value': 'QQQ'}]
    # An exact ticker typed is not duplicated as a "use symbol" entry.
    monkeypatch.setattr(perf, 'search_instruments', lambda q: hits[:1])
    assert [o['value'] for o in perf.search_options('aapl', [], [])] == ['AAPL']
    # At the limit nothing more can be added.
    full = ['A', 'B', 'C', 'D', 'E']
    assert [o['value'] for o in perf.search_options('apple', full, [])] == full


def test_search_instruments_handles_blank_and_failures(monkeypatch):
    import yfinance as yf

    assert ph.search_instruments('   ') == []

    class Boom:
        def __init__(self, *a, **k):
            raise RuntimeError('offline')

    monkeypatch.setattr(yf, 'Search', Boom)
    assert ph.search_instruments('definitely-not-cached-query-xyz') == []


def test_custom_bounds_and_clip_window():
    from datetime import date

    from backend.portfolio.history import clip_window, custom_bounds

    assert custom_bounds(date(2026, 10, 19)) == (date(2021, 10, 19), date(2026, 10, 19))
    assert custom_bounds(date(2024, 2, 29))[0] == date(2019, 2, 28)
    clipped = clip_window(entry([1, 2, 3, 4]), '2026-01-06', '2026-01-07')
    assert clipped == {'dates': DATES[1:3], 'prices': [2, 3]}
    assert clip_window(entry([1, 2, 3, 4]), '2026-01-07', '2026-01-08', pad_days=1)['dates'] == DATES[1:]
    assert clip_window(None, '2026-01-06', '2026-01-07') is None


def test_custom_range_builds_only_the_chosen_window(monkeypatch):
    from ui.callbacks.performance import build_performance

    dates = [d.strftime('%Y-%m-%d') for d in pd.bdate_range('2026-01-01', periods=60)]
    history = {'AAA': entry(range(10, 70), dates)}
    monkeypatch.setattr(ph, 'get_price_history', lambda tickers, period: history)
    holdings = {'base': 'EUR', 'positions': [pos('AAA', 1)]}
    fig, _, note = build_performance(holdings, 'Custom', 'abs', [], '2026-01-12', '2026-02-13')
    xs = list(fig.data[0].x)
    assert xs[0] == '2026-01-12' and xs[-1] == '2026-02-13'
    # invalid / missing inputs give a readable message, not an error
    for start, end, text in ((None, '2026-02-13', 'Pick a start'), ('2026-02-13', '2026-01-12', 'before')):
        fig, stats, _ = build_performance(holdings, 'Custom', 'abs', [], start, end)
        assert text in fig.layout.annotations[0].text and not fig.data
