"""Refresh the valuation store and connect it to its UI renderer."""

from __future__ import annotations

from dash import Input, Output, State

from backend.network import run_parallel
from backend.providers.valuation import (
    get_buffett_indicator,
    get_shiller_cape,
    get_sp500_pe,
    get_treasury_yield,
)
from ui.components.valuation import build_market_valuation
from ui.errors import safe_render


def register(app):
    @app.callback(
        Output('valuation-data', 'data'),
        Input('refresh-interval', 'n_intervals'),
        Input('startup-interval', 'n_intervals'),
        Input('kb-refresh-btn', 'n_clicks'),
    )
    def populate_valuation_data(*_):
        """
        Fetch all four valuation metrics in parallel and store them.
        Each getter has its own 4-hour cache, so real network calls are rare.
        On cold start the HTTP requests run concurrently — total latency is
        bounded by the slowest one, not all four. Failures are isolated per
        metric; one failing doesn't block the others.
        """
        return run_parallel({
            'buffett':  get_buffett_indicator,
            'sp500_pe': get_sp500_pe,
            'cape':     get_shiller_cape,
            'treasury': get_treasury_yield,
        })

    @app.callback(
        Output('market-valuation-section', 'children'),
        Input('valuation-data', 'data'),
        State('connection-status', 'data'),
    )
    @safe_render('Market Valuation')
    def render_market_valuation(data, status):
        base = (status or '').split(':')[0]
        if base in ('disconnected', 'no_positions'):
            return []
        return build_market_valuation(data)
