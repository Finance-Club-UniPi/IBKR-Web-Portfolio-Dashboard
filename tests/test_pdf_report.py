"""PDF export helpers: sector/geo roll-up, period returns vs benchmark, and the export callback."""

import asyncio

import pandas as pd
import pytest

import pdf_report as pr


def series(values, end='2026-10-23'):
    idx = pd.bdate_range(end=end, periods=len(values))
    return pd.Series(values, index=idx, dtype='float64')


def test_sector_geo_splits_etf_and_ranks_by_weight():
    positions = [
        {'ticker': 'AAA', 'market_value': 60},
        {'ticker': 'ETF', 'market_value': 40},
    ]
    sector_geo = {
        'AAA': {'sector': 'Technology', 'country': 'United States'},
        'ETF': {'sector': 'Unknown', 'country': 'Ireland', 'is_etf': True,
                'sector_weights': {'Technology': 0.5, 'Energy': 0.5}},
    }
    sectors, countries = pr.sector_geo_breakdown(positions, sector_geo)
    assert [(s[0], round(s[1], 1)) for s in sectors] == [('Technology', 80.0), ('Energy', 20.0)]
    assert sectors[0][2] == ['AAA', 'ETF']
    assert [(c[0], round(c[1], 1)) for c in countries] == [('United States', 60.0), ('Ireland', 40.0)]


def test_sector_geo_unknown_ticker_is_not_dropped():
    sectors, countries = pr.sector_geo_breakdown([{'ticker': 'ZZZ', 'market_value': 10}], {})
    assert sectors == [('Unknown', 100.0, ['ZZZ'])]
    assert countries == [('Unknown', 100.0)]


def test_period_returns_vs_benchmark_and_missing_history():
    # ~220 business days (~10 months): covers 1W/1M/3M/YTD but not a full 1Y
    n = 220
    value = series([100 + i for i in range(n)])
    bench = series([200.0] * n)
    rows = {label: (p, b) for label, p, b in pr.period_returns(value, bench)}
    assert rows['1W'][0] == pytest.approx((value.iloc[-1] / value.asof(value.index[-1] - pd.Timedelta('7D')) - 1) * 100)
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
    drawing = pr.weekly_chart(frame, 400, 150, {'Portfolio': '#378ADD', pr.BENCHMARK_NAME: '#f97316'})
    assert drawing.width == 400


def test_export_pdf_end_to_end(demo_payload, demo_df, demo_summary, monkeypatch):
    pytest.importorskip('reportlab')
    asyncio.set_event_loop(asyncio.new_event_loop())   # eventkit (via ib_async) needs a loop at import
    from dashboard_core import export

    value = series([100 + i for i in range(30)])
    monkeypatch.setattr(export, 'fetch_performance',
                        lambda positions, base: (value, series([200.0] * 30), [('VWCE', 'no price history')]))
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
    monkeypatch.setattr(export, 'fetch_performance', lambda positions, base: None)
    assert captured['fn'](1, data, None)['filename'].endswith('.pdf')
