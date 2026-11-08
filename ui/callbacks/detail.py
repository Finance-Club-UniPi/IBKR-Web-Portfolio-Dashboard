"""Position selection, detail loading, and trade-upload callbacks."""

from __future__ import annotations

import logging

import pandas as pd
from dash import ALL, Input, Output, State, ctx, dcc, html, no_update

from backend.providers.market_data import get_etf_brief_data, get_stock_fundamentals
from backend.schemas import PortfolioData
from backend.storage.trades import parse_activity_csv, save_uploaded_trades
from ui.components.position import (
    _build_etf_section,
    _build_fundamentals_section,
    _build_price_sparkline,
    _period_btn,
    _stat,
)
from ui.errors import safe_render
from ui.styles import (
    CARD,
    COLOR_BAD,
    COLOR_BRAND,
    COLOR_GOOD,
    COLOR_SURFACE,
    COLOR_SURFACE_WHITE,
    COLOR_TEXT,
    COLOR_TEXT_MID,
    COLOR_TEXT_SLATE,
    COLOR_TEXT_STRONG,
    COLOR_WARN,
    LINK_PILL,
)

log = logging.getLogger(__name__)

_LINK_STYLE = LINK_PILL
_PERIOD_CHOICES = ['1M', '3M', '1Y', '3Y', '5Y']


def register(app):
    app.clientside_callback(
        """
        function(n, briefStyle) {
            if (!n || n === 0) return window.dash_clientside.no_update;
            var showingBrief = (briefStyle || {}).display !== 'none';
            if (showingBrief) {
                return [{'display': 'block'}, {'display': 'none', 'marginTop': '10px'}, '📊 Investment Brief', 'grid'];
            } else {
                return [{'display': 'none'}, {'display': 'block', 'marginTop': '10px'}, '← Metric Grid', 'brief'];
            }
        }
        """,
        Output('fund-grid-view',  'style'),
        Output('fund-brief-view', 'style'),
        Output('fund-toggle-btn', 'children'),
        Output('fund-view-mode',  'data'),
        Input('fund-toggle-btn',  'n_clicks'),
        State('fund-brief-view',  'style'),
        prevent_initial_call=True,
    )

    @app.callback(
        Output('selected-ticker', 'data'),
        Output('holdings-datatable', 'active_cell'),
        Output('holdings-datatable', 'selected_cells'),
        Output('fund-view-mode', 'data', allow_duplicate=True),
        Input('holdings-datatable', 'active_cell'),
        Input('kb-escape-btn', 'n_clicks'),   # triggered when user presses Escape
        Input({'type': 'position-close', 'index': ALL}, 'n_clicks'),
        State('holdings-datatable', 'derived_viewport_data'),
        State('selected-ticker', 'data'),
        prevent_initial_call=True,
    )
    def select_ticker(active_cell, _esc, close_clicks, table_data, current):
        trig = ctx.triggered_id
        # Escape key or ✕ close button: always close the detail panel and
        # clear the DataTable's active-cell highlight so the row doesn't stay
        # tinted.
        if trig == 'kb-escape-btn':
            return None, None, [], 'grid'
        if isinstance(trig, dict) and trig.get('type') == 'position-close':
            if not any(close_clicks or []):
                return no_update, no_update, no_update, no_update
            return None, None, [], 'grid'
        if not active_cell or not table_data:
            return no_update, no_update, no_update, no_update
        ticker = table_data[active_cell['row']]['ticker']
        if current == ticker:
            return None, None, [], 'grid'
        return ticker, no_update, no_update, 'grid'

    @app.callback(
        Output('selected-period', 'data'),
        Input({'type': 'period-btn', 'index': ALL}, 'n_clicks'),
        prevent_initial_call=True,
    )
    def update_selected_period(_):
        tid = ctx.triggered_id
        if isinstance(tid, dict) and tid.get('type') == 'period-btn':
            return tid.get('index') or no_update
        return no_update

    @app.callback(
        Output('position-detail', 'children'),
        Input('selected-ticker', 'data'),
        Input('selected-period', 'data'),
        Input('uploaded-trades', 'data'),
        State('portfolio-data', 'data'),
        State('fund-view-mode', 'data'),
    )
    @safe_render('Position Detail')
    def show_position_detail(ticker: str | None, period: str, uploaded, data: PortfolioData | None,
                             fund_view: str | None):
        if not ticker or not data or 'positions' not in data:
            return None
        df = pd.DataFrame(data['positions'])
        matches = df[df['ticker'] == ticker]
        if matches.empty:
            return None
        r = matches.iloc[0]

        price        = r['current_price']
        daily_chg    = r.get('daily_change')
        daily_chg_pct = r.get('daily_change_pct')

        # Daily change header
        if daily_chg is not None and daily_chg == daily_chg:
            chg_color = COLOR_GOOD if daily_chg >= 0 else COLOR_BAD
            chg_str   = f"{'▲' if daily_chg >= 0 else '▼'} ${abs(daily_chg):,.2f}  ({daily_chg_pct:+.2f}%)"
        else:
            chg_color, chg_str = COLOR_TEXT_MID, '—'

        qty       = r.get('quantity') or 0
        mkt_val   = r.get('market_value')
        unreal    = r.get('unrealized_pnl')
        avg_cost  = r.get('avg_cost')

        if avg_cost and avg_cost == avg_cost and avg_cost > 0:
            cost_diff_pct = (price - avg_cost) / avg_cost * 100
            cost_color = COLOR_GOOD if cost_diff_pct >= 0 else COLOR_BAD
            cost_str   = f"${avg_cost:,.2f}  ({cost_diff_pct:+.2f}%)"
        else:
            cost_color, cost_str = COLOR_TEXT_MID, '—'

        qty_str    = f"{qty:,.0f}" if qty else '—'
        mkt_str    = f"${mkt_val:,.2f}" if mkt_val is not None else '—'
        if unreal is not None:
            unreal_color = COLOR_GOOD if unreal >= 0 else COLOR_BAD
            unreal_str   = f"${unreal:+,.2f}"
        else:
            unreal_color, unreal_str = COLOR_TEXT_MID, '—'

        stats = html.Div([
            _stat("Quantity", qty_str),
            _stat("Avg Cost", cost_str, cost_color),
            _stat("Market Value", mkt_str),
            _stat("Unrealized P&L", unreal_str, unreal_color),
            _stat("Daily Change", chg_str, chg_color),
        ], style={'display': 'flex', 'gap': '32px', 'flexWrap': 'wrap', 'marginTop': '17px'})

        # Fundamentals panel — ETFs get a sector/fund brief; stocks get the
        # signal grid + investment brief. Both are 4h cached via market_intel.
        pos_ctx = {
            'cost_diff_pct':  cost_diff_pct if avg_cost and avg_cost > 0 else None,
            'allocation_pct': r.get('allocation_pct'),
        }
        etf_data = get_etf_brief_data(ticker)
        if etf_data:
            fundamentals_section = _build_etf_section(etf_data, pos_ctx)
        else:
            fundamentals_section = _build_fundamentals_section(
                get_stock_fundamentals(ticker), pos_ctx, fund_view or 'grid'
            )

        # Collect trades for this ticker from live fills + uploaded CSV history.
        live_trades = (data.get('trades') or []) if isinstance(data, dict) else []
        all_trades  = list(uploaded or []) + list(live_trades)
        ticker_trades = [t for t in all_trades if (t.get('ticker') or '') == ticker]

        # Price history chart with period toggle + CSV upload button
        sel_period = period if period in _PERIOD_CHOICES else '1M'
        upload_btn = dcc.Upload(
            id={'type': 'position-trade-upload', 'index': 0},
            accept='.csv',
            multiple=False,
            children=html.Span("Upload trades CSV", style={
                'background': 'transparent', 'color': COLOR_BRAND,
                'border': '1px solid #88A0BA', 'borderRadius': '6px',
                'padding': '5px 12px', 'fontSize': '13px',
                'cursor': 'pointer', 'fontWeight': '500',
                'display': 'inline-block',
            }),
        )
        delete_btn = html.Button(
            "Delete trade history",
            id={'type': 'position-delete-trades', 'index': 0},
            n_clicks=0,
            style={
                'background': 'transparent', 'color': COLOR_BAD,
                'border': f'1px solid {COLOR_BAD}', 'borderRadius': '6px',
                'padding': '5px 12px', 'fontSize': '13px',
                'cursor': 'pointer', 'fontWeight': '500',
                'fontFamily': 'inherit',
            },
        )
        upload_help = html.Details([
            html.Summary("How to export from IBKR ▸", style={
                'cursor': 'pointer', 'color': COLOR_BRAND, 'fontSize': '13px',
                'fontWeight': '600', 'marginTop': '6px',
            }),
            html.Ol([
                html.Li("Log in to the IBKR Client Portal."),
                html.Li("Go to Performance & Reports → Transaction History."),
                html.Li("Pick a date range and click the CSV / download icon."),
                html.Li("Drop the downloaded .csv file using the button below."),
            ], style={'fontSize': '13px', 'color': COLOR_TEXT, 'lineHeight': '1.7',
                      'fontWeight': '500',
                      'paddingLeft': '20px', 'margin': '6px 0 8px'}),
            html.Div([upload_btn, delete_btn],
                     style={'display': 'flex', 'gap': '8px', 'alignItems': 'center',
                            'marginBottom': '4px'}),
        ])
        trade_count_note = (
            f"{len(ticker_trades)} trade{'s' if len(ticker_trades)!=1 else ''} plotted"
            if ticker_trades else "No trades on file for this ticker yet."
        )
        chart_section = html.Div([
            html.Div([
                html.P("Price History", style={
                    'fontSize': '14px', 'color': COLOR_TEXT_MID, 'margin': '0',
                    'textTransform': 'uppercase', 'letterSpacing': '0.05em',
                    'fontWeight': '600',
                }),
                html.Div([_period_btn(p, p == sel_period) for p in _PERIOD_CHOICES],
                         style={'display': 'flex', 'gap': '4px'}),
            ], style={'display': 'flex', 'justifyContent': 'space-between',
                      'alignItems': 'center', 'marginBottom': '6px'}),
            _build_price_sparkline(ticker, sel_period, avg_cost, ticker_trades),
            html.Div([
                html.Span(trade_count_note, style={'fontSize': '13px', 'color': COLOR_TEXT_MID, 'fontWeight': '500'}),
                html.Div(id={'type': 'position-upload-status', 'index': 0},
                         style={'fontSize': '12px'}),
            ], style={'display': 'flex', 'justifyContent': 'space-between',
                      'alignItems': 'center', 'marginTop': '2px'}),
            upload_help,
        ], style={'marginTop': '20px'})

        return html.Div([
            # Header row: ticker + price + daily change + quick links
            html.Div([
                html.Div([
                    html.Span(ticker, style={'fontWeight': '700', 'fontSize': '17px', 'color': COLOR_TEXT_STRONG}),
                    html.Span(f"${price:,.2f}",
                              style={'fontSize': '17px', 'color': COLOR_TEXT_STRONG, 'marginLeft': '12px'}),
                    html.Span(chg_str,
                              style={'fontSize': '15px', 'color': chg_color, 'marginLeft': '12px'}),
                ], style={'display': 'flex', 'alignItems': 'center'}),
                html.Div([
                    html.Button(f"✨ Ask coach about {ticker}",
                                id={'type': 'position-ask-coach', 'index': 0},
                                n_clicks=0, style={
                        'fontSize': '13px', 'color': COLOR_TEXT_SLATE,
                        'background': COLOR_SURFACE, 'border': '1px solid #e5e7eb',
                        'borderRadius': '8px', 'padding': '5px 12px',
                        'cursor': 'pointer', 'fontWeight': '500',
                        'fontFamily': 'inherit', 'marginRight': '4px',
                    }),
                    html.A("Yahoo", href=f"https://finance.yahoo.com/quote/{ticker}",
                           target='_blank', style=_LINK_STYLE),
                    html.A("TradingView", href=f"https://www.tradingview.com/symbols/{ticker}/",
                           target='_blank', style=_LINK_STYLE),
                    html.Button("✕ Close", id={'type': 'position-close', 'index': 0}, n_clicks=0, style={
                        'fontSize': '13px', 'color': COLOR_SURFACE_WHITE,
                        'background': COLOR_BAD, 'border': '1px solid #dc2626',
                        'borderRadius': '8px', 'padding': '5px 12px',
                        'cursor': 'pointer', 'marginLeft': '4px', 'fontWeight': '500',
                        'fontFamily': 'inherit',
                    }),
                ], style={'display': 'flex', 'alignItems': 'center', 'gap': '6px'}),
            ], style={'display': 'flex', 'justifyContent': 'space-between', 'alignItems': 'center'}),
            stats,
            fundamentals_section,
            chart_section,
        ], style={
            **CARD,
            'marginTop': '14px',
            'background': COLOR_SURFACE_WHITE,
            'borderLeft': '3px solid #378ADD',
            'animation': 'slideInDown 0.25s ease-out',
        })

    def _status(msg, color):
        return html.Span(msg, style={'color': color, 'fontWeight': '500'})

    def _broadcast(msg, color, ref_list):
        return [_status(msg, color) for _ in (ref_list or [])]

    # ── Per-position CSV trade upload ─────────────────────────────────────────
    # The upload button lives inside the opened position detail card. The user
    # exports a Transaction History CSV from the IBKR Client Portal and drops
    # it here. Parsed trades are persisted to data/uploaded_trades.json (the
    # same store used by the global trade history timeline) and plotted as
    # BUY/SELL arrows on the per-position price chart.
    @app.callback(
        Output('uploaded-trades', 'data'),
        Output({'type': 'position-upload-status', 'index': ALL}, 'children'),
        Input({'type': 'position-trade-upload', 'index': ALL}, 'contents'),
        State({'type': 'position-trade-upload', 'index': ALL}, 'filename'),
        prevent_initial_call=True,
    )
    def handle_position_trade_upload(contents_list, filenames_list):
        import base64

        contents = next((c for c in (contents_list or []) if c), None)
        filename = next((f for f in (filenames_list or []) if f), None)
        if not contents:
            return no_update, [no_update for _ in (contents_list or [])]

        if not (filename or '').lower().endswith('.csv'):
            return no_update, _broadcast("Need a .csv file", COLOR_BAD, contents_list)
        try:
            _, b64 = contents.split(',', 1)
            decoded = base64.b64decode(b64)
        except Exception:
            return no_update, _broadcast("Could not decode file", COLOR_BAD, contents_list)

        parsed = parse_activity_csv(decoded)
        if parsed is None:
            return no_update, _broadcast(
                "Doesn't look like an IBKR Activity Statement — "
                "export from Client Portal → Reports → Activity",
                COLOR_BAD, contents_list)
        if not parsed:
            return no_update, _broadcast("No trades found in CSV", COLOR_WARN, contents_list)

        merged = save_uploaded_trades(parsed)
        return merged, _broadcast(f"{len(parsed)} parsed · {len(merged)} total stored", COLOR_GOOD, contents_list)

    @app.callback(
        Output('uploaded-trades', 'data', allow_duplicate=True),
        Output({'type': 'position-upload-status', 'index': ALL}, 'children', allow_duplicate=True),
        Input({'type': 'position-delete-trades', 'index': ALL}, 'n_clicks'),
        prevent_initial_call=True,
    )
    def handle_delete_trades(n_clicks_list):
        from backend.storage.trades import clear_uploaded_trades

        if not any(n_clicks_list or []):
            return no_update, [no_update for _ in (n_clicks_list or [])]
        cleared = clear_uploaded_trades()
        return cleared, _broadcast("Trade history deleted", COLOR_WARN, n_clicks_list)
