"""Risk page loading and callbacks; history is fetched only while open."""

from __future__ import annotations

from dash import Input, Output, State, ctx, no_update

from backend.network import run_parallel
from backend.portfolio.history import fx_pair, get_compare_history, get_estr_history, get_portfolio_history
from backend.portfolio.risk import compute_risk_metrics, rf_over, to_base
from ui.callbacks.performance import _empty_figure, _holdings_payload
from ui.components.risk import build_cards, dd_figure, var_figure
from ui.helpers import ccy_symbol
from ui.styles import (
    COLOR_BORDER,
    COLOR_SURFACE,
    COLOR_TEXT_MUTED,
    COLOR_WARN_BG,
    COLOR_WARN_BORDER,
    COLOR_WARN_DEEP,
)


def build_risk(holdings, period):
    """Return (cards, var figure, drawdown figure, note) for the holdings and lookback."""
    if not holdings:
        wait = _empty_figure("Waiting for portfolio data…")
        return [], wait, wait, ""
    base = holdings['base']
    calls = {
        'portfolio': lambda: get_portfolio_history(holdings['positions'], base, period),
        'bench':     lambda: get_compare_history('^GSPC', period),
    }
    rf_name = None
    if base == 'EUR':
        calls['rf'] = get_estr_history
        rf_name = "€STR, ECB euro short-term rate"
    elif base == 'USD':
        calls['rf'] = lambda: get_compare_history('^IRX', period)
        rf_name = "13-week US T-bill, ^IRX"
    if base != 'USD':
        calls['fx'] = lambda: get_compare_history(fx_pair('USD', base), period)
    results = run_parallel(calls)
    bench = results.get('bench')
    bench_ccy = 'USD'
    if base != 'USD':
        bench = to_base(bench, results.get('fx'))
        bench_ccy = base if bench is not None else None
    value, excluded = results.get('portfolio') or (None, [])
    if value is None:
        msg = _empty_figure("Price history unavailable for this period.")
        return [], msg, msg, "Could not build price history for these holdings."
    rf = rf_over(results.get('rf'), value.index)
    m = compute_risk_metrics(value, bench, rf or 0.0)
    if m['returns'] is None:
        msg = _empty_figure(f"Not enough history ({m['n_obs']} daily returns, need 30).")
        return [], msg, msg, ""

    rf_text = (f"Risk-free {rf * 100:.2f}% (period mean, {rf_name})" if rf is not None
               else "Risk-free data unavailable, 0% used")
    note = (f"{m['start']:%d %b %Y} → {m['end']:%d %b %Y}, {m['n_obs']} daily returns. Backcast of your "
            f"current holdings valued at dividend-adjusted historical closes in {base}; cash and past trades are "
            f"not included. This is a total-return backcast, not historical account balances. VaR and CVaR are 1-day, in {base} on the latest value. {rf_text}. "
            f"Beta and alpha are against the S&P 500 (^GSPC) in {bench_ccy or base}"
            + ("." if m['beta'] is not None else ", which is unavailable."))
    if value.attrs.get('chained'):
        note += ' Earlier history is chained from holdings with available prices and approximates today’s portfolio.'
    if excluded:
        note += " Not included: " + ', '.join(f"{t} ({r})" for t, r in excluded) + '.'
    return build_cards(m, ccy_symbol(base)), var_figure(m), dd_figure(m), note


def register(app):
    @app.callback(
        Output('risk-holdings', 'data'),
        Input('portfolio-data', 'data'),
        State('risk-holdings', 'data'),
    )
    def update_risk_holdings(data, current):
        payload = _holdings_payload(data)
        return no_update if payload == current else payload

    @app.callback(
        Output('risk-cards', 'children'),
        Output('risk-var-graph', 'figure'),
        Output('risk-dd-graph', 'figure'),
        Output('risk-note', 'children'),
        Input('risk-holdings', 'data'),
        Input('risk-period', 'value'),
        Input('page-nav', 'data'),
        Input('refresh-interval', 'n_intervals'),
    )
    def render_risk(holdings, period, page, _interval):
        if page != 'risk':
            return no_update, no_update, no_update, no_update
        try:
            return build_risk(holdings, period or '1Y')
        except Exception as e:  # keep the rest of the dashboard alive
            msg = _empty_figure("Risk metrics unavailable.")
            return [], msg, msg, f"Error: {e}"

    @app.callback(
        Output('page-nav', 'data'),
        Output('nav-portfolio', 'style'),
        Output('nav-risk', 'style'),
        Input('nav-portfolio', 'n_clicks'),
        Input('nav-risk', 'n_clicks'),
    )
    def switch_page(_p, _r):
        page = 'risk' if ctx.triggered_id == 'nav-risk' else 'portfolio'

        def pill(active):
            return {
                'fontSize':     '12px',
                'fontWeight':   '600' if active else '500',
                'color':        COLOR_WARN_DEEP if active else COLOR_TEXT_MUTED,
                'background':   COLOR_WARN_BG if active else COLOR_SURFACE,
                'border':       f"0.5px solid {COLOR_WARN_BORDER if active else COLOR_BORDER}",
                'borderRadius': '999px',
                'padding':      '4px 12px',
                'cursor':       'pointer',
                'lineHeight':   '1.4',
            }
        return page, pill(page == 'portfolio'), pill(page == 'risk')
