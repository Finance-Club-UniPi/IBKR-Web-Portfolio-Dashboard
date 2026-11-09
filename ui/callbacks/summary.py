"""Top-of-page rendering: summary cards, holdings table, allocation donut,
and the dividends panel.

All four callbacks read from `portfolio-data` and render plain children. No
network, no IBKR — pure functions of the store payload.
"""

from __future__ import annotations

from dash import Input, Output, State

from backend.schemas import PortfolioData
from ui.components.summary import (
    build_allocation_chart,
    build_dividends,
    build_holdings_table,
    build_summary_cards,
)
from ui.errors import safe_render


def register(app):
    @app.callback(
        Output('summary-cards', 'children'),
        Input('portfolio-data', 'data'),
        State('connection-status', 'data'),
        Input('portfolio-analysis', 'data'),
    )
    @safe_render('Summary')
    def update_summary(data: PortfolioData | None, status, analysis=None):
        return build_summary_cards(data, status, analysis)

    @app.callback(
        Output('holdings-table', 'children'),
        Output('positions-count', 'children'),
        Output('stale-price-badge', 'children'),
        Input('portfolio-data', 'data'),
        State('connection-status', 'data'),
    )
    def update_holdings(data, status):
        return build_holdings_table(data, status)

    @app.callback(
        Output('donut-container', 'children'),
        Input('portfolio-data', 'data'),
    )
    def update_donut(data):
        return build_allocation_chart(data)

    @app.callback(
        Output('holdings-donut-row', 'style'),
        Input('connection-status', 'data'),
        Input('portfolio-data', 'data'),
    )
    def toggle_holdings_row(status, _data):
        base = (status or '').split(':')[0]
        visible = {'display': 'flex', 'gap': '14px'}
        if base == 'disconnected':
            return {**visible, 'display': 'none'}
        return visible


    @app.callback(
        Output('dividend-section', 'children'),
        Input('portfolio-data', 'data'),
        Input('refresh-interval', 'n_intervals'),
        State('connection-status', 'data'),
    )
    @safe_render('Dividends')
    def update_dividends(data: PortfolioData | None, _interval, status):
        return build_dividends(data, _interval, status)
