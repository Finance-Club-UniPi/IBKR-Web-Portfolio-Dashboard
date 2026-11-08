"""Refresh each browser tab's intelligence store and render its sections."""

from __future__ import annotations

import logging
import time
from datetime import datetime

from dash import Input, Output, ctx, html, no_update

from backend.network import run_parallel
from backend.providers.market_data import get_earnings_data, get_sector_geo
from ui.components.intel import build_earnings, build_sector_geo
from ui.errors import safe_render
from ui.styles import (
    COLOR_SURFACE_WHITE,
    COLOR_TEXT_STRONG,
)

log = logging.getLogger(__name__)


def register(app):
    # ── Toast notifications ───────────────────────────────────────────────────
    # One callback covers all toast triggers.  Using a single Output avoids the
    # "multiple callbacks with the same output" constraint in Dash.
    #
    # The child div receives a unique `key` (current timestamp) on every call
    # so React unmounts and remounts it — which restarts the CSS animation
    # even when the message text is identical to the previous one.
    @app.callback(
        Output('toast', 'children'),
        Input('portfolio-data', 'data'),
        prevent_initial_call=True,
    )
    def update_toast(*_):
        triggered = ctx.triggered_id
        ts = datetime.now().strftime('%H:%M:%S')
        messages = {
            'portfolio-data': f"Portfolio refreshed · {ts}",
        }
        msg = messages.get(triggered)
        if not msg:
            return no_update
        return html.Div(
            msg,
            key=str(time.time()),
            className='toast-msg',
            style={
                'background': COLOR_TEXT_STRONG, 'color': COLOR_SURFACE_WHITE,
                'padding': '10px 18px', 'borderRadius': '12px',
                'fontSize': '15px', 'fontWeight': '500',
                'letterSpacing': '0.01em',
                'boxShadow': '0 4px 20px rgba(0,0,0,0.18)',
                'whiteSpace': 'nowrap',
            },
        )

    # ── Populate market-intel store ───────────────────────────────────────────
    # Fires whenever portfolio-data updates. Calls yfinance-backed functions in
    # parallel (sector/geo + earnings concurrently) so cold-start latency is
    # bounded by the slower of the two rather than their sum.
    @app.callback(
        Output('market-intel-data', 'data'),
        Input('portfolio-data', 'data'),
    )
    def populate_market_intel(data):
        if not data or 'positions' not in data:
            return None

        tickers    = [p['ticker'] for p in data['positions']]

        parallel = run_parallel({
            'sector_geo': lambda: get_sector_geo(tickers),
            'earnings':   lambda: get_earnings_data(tickers),
        })
        result = {
            'tickers':    tickers,
            'sector_geo': parallel.get('sector_geo') or {},
            'earnings':   parallel.get('earnings')   or {},
        }

        return result

    # ── 1. Sector & Geography ────────────────────────────────────────────────
    @app.callback(
        Output('sector-geo-section', 'children'),
        Input('market-intel-data', 'data'),
        Input('connection-status', 'data'),
        Input('portfolio-data', 'data'),
    )
    @safe_render('Sector & Geography')
    def render_sector_geo(intel, status, port_data):
        return build_sector_geo(intel, port_data, status)

    # ── 3. Earnings calendar ─────────────────────────────────────────────────
    @app.callback(
        Output('earnings-section', 'children'),
        Input('market-intel-data', 'data'),
        Input('connection-status', 'data'),
        Input('portfolio-data', 'data'),
    )
    @safe_render('Earnings')
    def render_earnings(intel, status, port_data):
        return build_earnings(intel, port_data, status)
