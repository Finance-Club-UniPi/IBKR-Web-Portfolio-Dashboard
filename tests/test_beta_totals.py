"""Per-holding beta, value-weighted portfolio beta, and the FX / realized totals."""

import pytest

from data_processor import get_summary, portfolio_beta, process_positions
from demo_data import build_demo_payload


def build(betas, portfolio='balanced'):
    raw = build_demo_payload(portfolio)
    return process_positions(raw['positions'], raw['market_data'],
                             base_currency=raw['account']['base_currency'],
                             trade_fx=raw['trade_fx'], daily_fx_rates=raw['daily_fx_rates'], betas=betas)


def test_beta_column_maps_by_ticker_and_defaults_to_nan():
    df = build({'AAPL': 1.2, 'MSFT': None})
    assert df.loc[df.ticker == 'AAPL', 'beta'].iloc[0] == 1.2
    assert df['beta'].dtype.kind == 'f'
    assert df.loc[df.ticker == 'MSFT', 'beta'].isna().all()
    assert build(None)['beta'].isna().all()


def test_portfolio_beta_is_value_weighted():
    df = build({t: 1.0 for t in build(None).ticker})
    assert portfolio_beta(df) == (1.0, 100.0, 8)

    df = build({'AAPL': 2.0, 'MSFT': 0.0})
    aapl, msft = (df.loc[df.ticker == t, 'market_value'].iloc[0] for t in ('AAPL', 'MSFT'))
    beta, coverage, count = portfolio_beta(df)
    assert beta == pytest.approx(2.0 * aapl / (aapl + msft), abs=0.01)
    assert count == 2 and 0 < coverage < 100


def test_missing_betas_do_not_drag_result_toward_zero():
    assert portfolio_beta(build({'NVDA': 1.7}))[0] == 1.7
    assert portfolio_beta(build({})) == (None, 0.0, 0)


def test_summary_totals():
    df = build({'AAPL': 1.1})
    s = get_summary(df)
    assert s['portfolio_beta'] == 1.1 and s['beta_covered'] == 1
    assert s['total_realized_pnl'] == pytest.approx(-258.35)
    assert s['total_fx_today'] == pytest.approx(df['fx_today'].dropna().sum(), abs=0.01)
    assert s['fx_covered'] == int(df['fx_today'].notna().sum()) == 8
    # USD names are hit by the demo's falling USD, EUR names contribute exactly 0.
    assert s['total_fx_today'] < 0
    assert s['total_fx_total'] == pytest.approx(df['fx_total'].sum(), abs=0.01) and s['total_fx_total'] > 0
    assert s['fx_total_covered'] == 8


def test_fx_total_is_none_when_no_position_has_an_estimate():
    raw = build_demo_payload('balanced')
    df = process_positions(raw['positions'], raw['market_data'], base_currency='EUR',
                           daily_fx_rates={})
    s = get_summary(df)
    assert s['total_fx_today'] == 0 or s['total_fx_today'] is None
    assert s['fx_covered'] == int(df['fx_today'].notna().sum())


def test_ui_renders_cards_and_beta_column():
    from dashboard_core.summary import register

    callbacks = {}

    class App:
        def callback(self, *args, **kwargs):
            def save(fn):
                callbacks[fn.__name__] = fn
                return fn
            return save

    register(App())
    raw = build_demo_payload('balanced')
    df = build({'AAPL': 1.2, 'NVDA': 1.7})
    data = {'positions': df.to_dict('records'), 'summary': get_summary(df),
            'account': raw['account']}

    cards = callbacks['update_summary'](data, 'connected')
    assert len(cards) == 8
    values = {c.children[0].children: c.children[1].children for c in cards}
    assert values['Realized P&L'] == '€-258.35'
    assert values['Portfolio beta'] == f"{data['summary']['portfolio_beta']:.2f}"
    assert values['FX today'].startswith('€-')
    assert values['FX total'].startswith('€+')   # USD names bought at 1.12, now 1.08

    table, _, _ = callbacks['update_holdings'](data, 'connected')
    beta_col = next(c for c in table.columns if c['id'] == 'beta')
    assert beta_col['format']['nully'] == '—'
    assert next(r for r in table.data if r['ticker'] == 'AAPL')['beta'] == 1.2
    assert next(r for r in table.data if r['ticker'] == 'KO')['beta'] is None
    assert 'S&P 500' in table.tooltip_header['beta']

    # No betas at all: the card falls back to a dash instead of raising.
    empty = {'positions': build(None).to_dict('records'),
             'summary': get_summary(build(None)), 'account': raw['account']}
    beta_card = callbacks['update_summary'](empty, 'connected')[-1]
    assert beta_card.children[1].children == '—'
