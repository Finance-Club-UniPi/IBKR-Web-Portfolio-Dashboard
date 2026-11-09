"""Shared background analysis for both beta cards and the Risk page."""

from __future__ import annotations

from dash import Input, Output, State, ctx, no_update

from backend.portfolio.analysis import analysis_summary, request_analysis
from ui.callbacks.performance import _empty_figure
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


def build_risk(data, period):
    result = request_analysis(data, period)
    summary = analysis_summary(result)
    m = result.get('metrics')
    if m is None:
        figure = _empty_figure(result['reason'])
        return [], figure, figure, result['reason'], summary
    note = (f"{m['start']:%d %b %Y} → {m['end']:%d %b %Y}, {m['n_obs']} daily returns. "
            f"Current signed exposures / account equity, including cash, in {data['account']['base_currency']}. "
            "Historical scenarios at fixed current weights; not actual account performance. "
            "Borrow fees, financing and trading costs are excluded. "
            f"Valuation as of {result['valuation_as_of']}. "
            + ('' if result['rf_available'] else 'Risk-free data unavailable; 0% used. '))
    return (build_cards(m, ccy_symbol(data['account']['base_currency'])),
            var_figure(m), dd_figure(m), note, summary)


def register(app):
    @app.callback(
        Output('risk-holdings', 'data'),
        Input('portfolio-data', 'data'),
        State('risk-holdings', 'data'),
    )
    def update_risk_holdings(data, current):
        payload = data
        return no_update if payload == current else payload

    @app.callback(
        Output('risk-cards', 'children'),
        Output('risk-var-graph', 'figure'),
        Output('risk-dd-graph', 'figure'),
        Output('risk-note', 'children'),
        Output('portfolio-analysis', 'data'),
        Input('risk-holdings', 'data'),
        Input('risk-period', 'value'),
        Input('page-nav', 'data'),
        Input('refresh-interval', 'n_intervals'),
        Input('fast-connect-poll', 'n_intervals'),
    )
    def render_risk(holdings, period, page, _interval, _poll):
        try:
            return build_risk(holdings, period or '1Y')
        except Exception as e:  # keep the rest of the dashboard alive
            msg = _empty_figure("Risk metrics unavailable.")
            return [], msg, msg, str(e), {'status': 'unavailable', 'reason': str(e), 'beta': None}

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
