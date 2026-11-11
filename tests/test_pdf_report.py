"""PDF export helpers: sector/geo roll-up, period returns vs benchmark, and the export callback."""

import asyncio

import pandas as pd
import pytest

import backend.portfolio.report_data as pr
from backend.portfolio.exposure import sector_geo_breakdown
from ui.reports.charts import weekly_chart


def series(values, end='2026-10-23'):
    idx = pd.bdate_range(end=end, periods=len(values))
    return pd.Series(values, index=idx, dtype='float64')


def test_sector_geo_splits_etf_and_ranks_by_weight():
    positions = [
        {'ticker': 'AAA', 'allocation_pct': 60, 'base_market_value': 60},
        {'ticker': 'ETF', 'allocation_pct': 40, 'base_market_value': 40},
    ]
    sector_geo = {
        'AAA': {'sector': 'Technology', 'country': 'United States'},
        'ETF': {'sector': 'Unknown', 'country': 'Ireland', 'is_etf': True,
                'sector_weights': {'Technology': 0.5, 'Energy': 0.5}},
    }
    sectors, countries = sector_geo_breakdown(positions, sector_geo)
    assert [(s[0], round(s[1], 1)) for s in sectors] == [('Technology', 80.0), ('Energy', 20.0)]
    assert sectors[0][2] == ['AAA', 'ETF']
    assert [(c[0], round(c[1], 1)) for c in countries] == [('United States', 60.0), ('Ireland', 40.0)]


def test_sector_geo_unknown_ticker_is_not_dropped():
    sectors, countries = sector_geo_breakdown([{'ticker': 'ZZZ', 'allocation_pct': 100, 'base_market_value': 10}], {})
    assert sectors == [('Unknown', 100.0, ['ZZZ'])]
    assert countries == [('Unknown', 100.0)]


def test_period_returns_vs_benchmark_and_missing_history():
    # ~220 business days (~10 months): covers 1W/1M/3M/YTD but not a full 1Y
    n = 220
    value = series([100 + i for i in range(n)])
    bench = series([200.0] * n)
    rows = {label: (p, b) for label, p, b in pr.period_returns(value, bench)}
    assert rows['1W'][0] == pytest.approx((value.iloc[-1] / value.asof(value.index[-1] - pd.Timedelta(days=7)) - 1) * 100)
    assert all(rows[k][1] == pytest.approx(0.0) for k in ('1W', '1M', '3M', 'YTD'))
    assert rows['1Y'] == (None, None)          # history starts well after a year ago


def test_period_returns_without_benchmark():
    value = series([100, 101, 102, 103, 104, 105, 106, 107, 108])
    rows = pr.period_returns(value, None)
    assert all(b is None for _, _, b in rows)
    assert rows[0][1] is not None


def test_weekly_frame_rebases_to_zero_and_aligns_benchmark():
    value = series([100, 102, 101, 103, 104, 105, 106, 108])
    bench = series([50, 50, 51, 52, 51, 52, 53, 54])
    frame = pr.weekly_frame(value, bench)
    assert list(frame.columns) == ['Portfolio', pr.BENCHMARK_NAME]
    assert frame.iloc[0].tolist() == [0.0, 0.0]
    assert (frame.index[-1] - frame.index[0]).days <= pr.CHART_DAYS + 3   # base close sits just before the window
    assert len(frame) >= 2


def test_weekly_frame_portfolio_only_when_no_benchmark():
    frame = pr.weekly_frame(series([100, 101, 102, 103, 104, 105]), None)
    assert list(frame.columns) == ['Portfolio']


def test_weekly_chart_builds_a_drawing():
    pytest.importorskip('reportlab')
    frame = pr.weekly_frame(series([100, 102, 101, 103, 104, 105, 106, 108]),
                            series([50, 50, 51, 52, 51, 52, 53, 54]))
    drawing = weekly_chart(frame, 400, 150, {'Portfolio': '#378ADD', pr.BENCHMARK_NAME: '#f97316'})
    assert drawing.width == 400


def test_export_pdf_end_to_end(demo_payload, demo_df, demo_summary, monkeypatch):
    pytest.importorskip('reportlab')
    asyncio.set_event_loop(asyncio.new_event_loop())   # eventkit (via ib_async) needs a loop at import
    import ui.callbacks.export as export
    from ui.reports import pdf as pdf_renderer
    value = series([100 + i for i in range(30)])
    monkeypatch.setattr(pdf_renderer, 'fetch_performance',
                        lambda positions, account: (value, series([200.0] * 30), [('VWCE', 'no price history')]))
    captured = {}

    class App:
        def callback(self, *_, **__):
            def deco(fn):
                captured['fn'] = fn
                return fn
            return deco

    export.register(App())
    account = dict(demo_payload['account'], portfolio_value_units='base')
    data = {'positions': demo_df.to_dict('records'), 'summary': demo_summary,
            'account': account, 'div_data': demo_payload['div_data']}
    intel = {'sector_geo': {'NVDA': {'sector': 'Technology', 'country': 'United States'}}}

    out = captured['fn'](1, data, intel)
    assert out['filename'].endswith('.pdf')
    import base64
    content = out['content']
    raw = content if isinstance(content, bytes) else base64.b64decode(content)
    assert raw.startswith(b'%PDF')

    # Still renders when history and market intel are both missing.
    monkeypatch.setattr(pdf_renderer, 'fetch_performance', lambda positions, account: None)
    assert captured['fn'](1, data, None)['filename'].endswith('.pdf')


def _pdf_tables(data, monkeypatch, perf=lambda positions, account: None):
    """Build the PDF with fetch_performance stubbed; return the table rows it drew."""
    pytest.importorskip('reportlab')
    from reportlab.platypus import Table

    from ui.reports import pdf as pdf_renderer
    rows = []
    original = Table.__init__

    def record(self, cells, *args, **kwargs):
        rows.extend(cells)
        original(self, cells, *args, **kwargs)

    monkeypatch.setattr(Table, '__init__', record)
    monkeypatch.setattr(pdf_renderer, 'fetch_performance', perf)
    pdf_renderer.build_pdf(data)
    return rows


def _pdf_data(demo_df, demo_summary, **summary):
    return {'positions': demo_df.to_dict('records'), 'summary': dict(demo_summary, **summary),
            'account': {'base_currency': 'EUR', 'portfolio_value_units': 'base', 'eurusd_rate': 2.0}}


def test_pdf_total_return_unavailable_is_not_rebuilt_from_pnl(demo_df, demo_summary, monkeypatch):
    # EUR 100 -> 50 plus USD 100 -> 200 at 0.50 EUR/USD: the old fallback printed +50% here.
    data = _pdf_data(demo_df, demo_summary, total_pnl_pct=None, total_value=150.0, total_unrealized_pnl=50.0)
    rows = _pdf_tables(data, monkeypatch)
    assert rows[rows.index(['Total Return', 'Today', 'Best Performer', 'Worst Performer']) + 1][0] == '—'


def test_pdf_total_return_uses_backend_value_unchanged(demo_df, demo_summary, monkeypatch):
    data = _pdf_data(demo_df, demo_summary, total_pnl_pct=12.5)
    rows = _pdf_tables(data, monkeypatch)
    assert rows[rows.index(['Total Return', 'Today', 'Best Performer', 'Worst Performer']) + 1][0] == '+12.50%'


def _history(values):
    return {'prices': values, 'dates': [d.strftime('%Y-%m-%d') for d in pd.bdate_range(end='2026-10-23', periods=len(values))]}


def test_pdf_performance_is_the_account_level_return_with_cash_and_a_short(demo_df, demo_summary, monkeypatch):
    import backend.portfolio.analysis as analysis
    from ui.reports import pdf as pdf_renderer
    n = 300
    # Long 10,000 +10% and short 5,000 +10% on the last day, 100,000 account: 0.1*10% - 0.05*10% = +0.50%.
    long_, short = _history([100.0] * (n - 1) + [110.0]), _history([100.0] * (n - 1) + [110.0])
    positions = [dict(demo_df.iloc[0].to_dict(), ticker='LONG', conId=1, currency='EUR', quantity=100, base_market_value=10000.0),
                 dict(demo_df.iloc[0].to_dict(), ticker='SHRT', conId=2, currency='EUR', quantity=-50, base_market_value=-5000.0)]
    account = {'base_currency': 'EUR', 'portfolio_value_units': 'base', 'net_liquidation': 100000.0}
    flat = pd.Series([1.0] * n, index=pd.bdate_range(end='2026-10-23', periods=n))
    monkeypatch.setattr(analysis, 'get_position_history', lambda p, period: long_ if p['ticker'] == 'LONG' else short)
    monkeypatch.setattr(analysis, 'get_compare_history', lambda symbol, period: flat)
    monkeypatch.setattr(analysis, 'get_estr_history', lambda: flat * 3)
    data = dict(_pdf_data(demo_df, demo_summary), positions=positions, account=account)

    rows = _pdf_tables(data, monkeypatch, pdf_renderer.fetch_performance)
    portfolio = next(r for r in rows if r[0] == 'Portfolio')
    assert portfolio[1:] == ['+0.50%'] * 5


@pytest.mark.parametrize('equity', [None, 0.0, -5.0])
def test_pdf_performance_needs_positive_account_equity(demo_df, demo_summary, monkeypatch, equity):
    import backend.portfolio.analysis as analysis
    from ui.reports import pdf as pdf_renderer
    positions = demo_df.to_dict('records')
    # History is fully available, so only the equity check can make the result unavailable.
    n = 300
    flat = pd.Series([1.0] * n, index=pd.bdate_range(end='2026-10-23', periods=n))
    monkeypatch.setattr(analysis, 'get_position_history', lambda p, period: _history([100.0] * n))
    monkeypatch.setattr(analysis, 'get_compare_history', lambda symbol, period: flat)
    monkeypatch.setattr(analysis, 'get_estr_history', lambda: flat * 3)
    account = {'base_currency': 'EUR', 'portfolio_value_units': 'base', 'net_liquidation': equity}
    assert pr.fetch_performance(positions, account) is None
    data = dict(_pdf_data(demo_df, demo_summary), account=account)
    rows = _pdf_tables(data, monkeypatch, pdf_renderer.fetch_performance)
    assert not any(r[0] == 'Return' for r in rows)
