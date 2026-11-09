"""Portfolio performance chart: value or % return, with instrument comparison.

Controls (period, absolute/% toggle, compare box) are static layout elements so
their state survives the 60-second portfolio refresh.  `perf-holdings` is a
small store that only changes when the holdings do, so the (cached) history is
not rebuilt on every tick.
"""

from __future__ import annotations

from datetime import date

import plotly.graph_objects as go
from dash import Input, Output, State, html, no_update

from backend.network import fetch_parallel, run_parallel
from backend.portfolio.analysis import account_history
from backend.portfolio.history import (
    CUSTOM,
    DEFAULT_PERIOD,
    MAX_COMPARE,
    compare_frame,
    custom_bounds,
    get_compare_history,
    get_portfolio_history,
    parse_symbols,
    search_instruments,
)
from ui.helpers import ccy_symbol
from ui.styles import (
    COLOR_BAD,
    COLOR_BRAND,
    COLOR_GOOD,
    COLOR_TEXT_MID,
)

_COMPARE_COLORS = ['#f97316', '#a855f7', '#14b8a6', '#ec4899', '#eab308']
_GRID = 'rgba(128,128,128,0.18)'
_TICK = dict(size=12, color='#888')



def search_options(query, selected, previous):
    """Dropdown options for a search box: Yahoo matches plus the current selection.

    Each option carries the query in its `search` text so the dropdown's own
    client-side filter never hides a server-side result.
    """
    selected = list(selected or [])
    keep = [o for o in (previous or []) if o.get('value') in selected]
    known = {o['value'] for o in keep}
    for sym in selected:                      # a selection must always have an option
        if sym not in known:
            keep.append({'label': sym, 'value': sym})
    query = (query or '').strip()
    if not query or len(selected) >= MAX_COMPARE:
        return keep
    options = []
    for r in search_instruments(query):
        if r['symbol'] in selected:
            continue
        detail = ' · '.join(x for x in (r['name'], r['exchange'], r['type']) if x)
        options.append({'label': f"{r['symbol']}  —  {detail}" if detail else r['symbol'],
                        'value': r['symbol'], 'search': f"{query} {r['symbol']} {r['name']}"})
    typed = query.upper().replace(' ', '')
    if typed not in {o['value'] for o in options} | set(selected):
        options.append({'label': f"Use symbol “{typed}”", 'value': typed, 'search': query})
    return keep + options


def _holdings_payload(data):
    if not data or not data.get('positions'):
        return None
    positions = [
        {k: p.get(k) for k in ('ticker', 'quantity', 'currency', 'multiplier', 'conId', 'account',
                              'base_market_value', 'primary_exchange', 'valuation_as_of')}
        for p in data['positions']
    ]
    positions.sort(key=lambda p: p['ticker'])
    return {'base': (data.get('account') or {}).get('base_currency', 'EUR'), 'positions': positions,
            'account': data.get('account') or {}}


def _empty_figure(message: str) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(
        xaxis=dict(visible=False), yaxis=dict(visible=False),
        margin=dict(l=0, r=0, t=0, b=0),
        paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
        annotations=[dict(text=message, x=0.5, y=0.5, xref='paper', yref='paper',
                          showarrow=False, font=dict(size=14, color='#888'))],
    )
    return fig


def _stat(name, color, ratio_pct, change, sym):
    text = f"{ratio_pct:+.2f}%"
    if change is not None:
        text += f"  ({'+' if change >= 0 else '-'}{sym}{abs(change):,.0f})"
    return html.Span([
        html.Span('●', style={'color': color, 'marginRight': '6px'}),
        html.Span(name, style={'color': COLOR_TEXT_MID, 'fontSize': '13px', 'fontWeight': '600',
                               'marginRight': '8px'}),
        html.Span(text, style={'color': COLOR_GOOD if ratio_pct >= 0 else COLOR_BAD,
                               'fontSize': '14px', 'fontWeight': '600'}),
    ])


def _as_date(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def build_performance(holdings, period, mode, compare_text, start=None, end=None):
    """Return (figure, stats children, note) for the given controls."""
    if not holdings:
        return _empty_figure("Waiting for portfolio data…"), [], ""

    if 'account' in holdings and not (holdings['account'].get('net_liquidation') or 0) > 0:
        return _empty_figure('Positive account equity is required.'), [], 'Account equity unavailable'

    if period == CUSTOM:
        earliest, latest = custom_bounds()
        start, end = _as_date(start), _as_date(end) or latest
        if start is None:
            return _empty_figure("Pick a start date."), [], ""
        if start >= end:
            return _empty_figure("The start date must be before the end date."), [], ""
        start, end = max(start, earliest), min(end, latest)
        if (end - start).days < 5:
            return _empty_figure("Pick a range of at least a few days."), [], ""

    base = holdings['base']
    sym = ccy_symbol(base)
    symbols = parse_symbols(compare_text)

    window = (start, end) if period == CUSTOM else ()   # presets keep the plain (period) calls
    results = run_parallel({
        'portfolio': lambda: ((account_history(holdings, period, *window)[0], [])
                             if holdings.get('account', {}).get('net_liquidation') else
                             get_portfolio_history(holdings['positions'], base, period, *window)),
        'compare':   lambda: fetch_parallel(symbols,
                                            lambda s: get_compare_history(s, period, *window)),
    })
    value, excluded = results.get('portfolio') or (None, [])
    compare = results.get('compare') or {}
    if value is None:
        why = '; '.join(f"{t}: {r}" for t, r in excluded) or 'price data unavailable'
        return _empty_figure("Price history unavailable for this period."), [], f"Could not build history ({why})."

    found = {s: compare[s] for s in symbols if compare.get(s) is not None}
    missing = [s for s in symbols if s not in found]
    frame = compare_frame(value, found, mode)
    if frame.empty:
        return _empty_figure("No overlapping price history for this period."), [], ""

    fig = go.Figure()
    stats = []
    colors = {'Portfolio': COLOR_BRAND}
    colors.update({s: _COMPARE_COLORS[i % len(_COMPARE_COLORS)] for i, s in enumerate(found)})
    for name in frame.columns:
        series = frame[name]
        first, last = float(series.iloc[0]), float(series.iloc[-1])
        ratio_pct = last if mode == 'pct' else (last / first - 1) * 100
        label = name if name == 'Portfolio' or mode == 'pct' else f"{name} (same start capital)"
        fig.add_trace(go.Scatter(
            x=[d.strftime('%Y-%m-%d') for d in series.index], y=series.round(4).tolist(),
            mode='lines', name=label,
            line=dict(color=colors[name], width=2.5 if name == 'Portfolio' else 1.8),
            hovertemplate=('%{y:+.2f}%' if mode == 'pct' else f'{sym}%{{y:,.0f}}') + f'<extra>{label}</extra>',
        ))
        stats.append(_stat(name, colors[name], ratio_pct,
                           None if mode == 'pct' else last - first, sym))

    if mode == 'pct':
        fig.add_hline(y=0, line=dict(color='#888', width=1, dash='dot'))
    fig.update_layout(
        margin=dict(l=8, r=8, t=8, b=8), height=340, hovermode='x unified',
        showlegend=False, uirevision='perf',
        xaxis=dict(showgrid=False, tickfont=_TICK),
        yaxis=dict(showgrid=True, gridcolor=_GRID, tickfont=_TICK, zeroline=False,
                   **(dict(ticksuffix='%') if mode == 'pct' else dict(tickprefix=sym, tickformat=',.0f'))),
        plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
        hoverlabel=dict(font=dict(size=13)),
    )

    notes = [f"{frame.index[0]:%d %b %Y} → {frame.index[-1]:%d %b %Y}. Your current holdings are "
             f"valued at historical closes in {base} (daily FX); cash and past trades are not included. Prices are dividend-adjusted; "
             "the values are a total-return backcast, not historical account balances."]
    if holdings.get('account', {}).get('net_liquidation'):
        notes = [f"{frame.index[0]:%d %b %Y} → {frame.index[-1]:%d %b %Y}. Account-equity scenarios "
                 "at fixed current signed exposures, including cash FX. Dividend-adjusted prices; "
                 "not actual historical account balances. Financing, borrow fees and trading costs excluded."]
    if mode == 'abs' and found:
        notes.append("Comparison instruments show the same starting capital invested in them, "
                     "in their own trading currency.")
    elif found:
        notes.append("Comparison returns are in each instrument's own trading currency.")
    chained = value.attrs.get('chained') or []
    full_start = value.attrs.get('full_start')
    if chained and full_start is not None and frame.index[0] < full_start:
        late = ', '.join(f"{t} (from {d:%d %b %Y})" for t, d in chained)
        notes.append(f"Before {full_start:%d %b %Y} the history is chained from the holdings that "
                     f"already had prices — {late} had no price history yet — so earlier values "
                     "approximate today's portfolio.")
    if value.index[0] < frame.index[0]:
        notes.append(f"Chart starts {frame.index[0]:%d %b %Y}, where all series have data.")
    if excluded:
        notes.append("Not included: " + ', '.join(f"{t} ({r})" for t, r in excluded) + '.')
    if missing:
        notes.append("No data found for: " + ', '.join(missing) + '.')
    return fig, stats, ' '.join(notes)


def register(app):
    @app.callback(
        Output('perf-holdings', 'data'),
        Input('portfolio-data', 'data'),
        State('perf-holdings', 'data'),
    )
    def update_perf_holdings(data, current):
        payload = _holdings_payload(data)
        return no_update if payload == current else payload

    @app.callback(
        Output('perf-compare', 'options'),
        Input('perf-compare', 'search_value'),
        State('perf-compare', 'value'),
        State('perf-compare', 'options'),
    )
    def update_compare_options(query, selected, previous):
        return search_options(query, selected, previous)

    @app.callback(
        Output('perf-range-wrap', 'style'),
        Output('perf-range', 'min_date_allowed'),
        Output('perf-range', 'max_date_allowed'),
        Output('perf-range', 'end_date'),
        Input('perf-period', 'value'),
        State('perf-range', 'end_date'),
    )
    def toggle_custom_range(period, end_date):
        """Show the picker for Custom and re-anchor its bounds to today (the app may
        have been running for days); an end date past today is pulled back."""
        earliest, latest = custom_bounds()
        shown = {'display': 'flex', 'alignItems': 'center', 'gap': '10px',
                 'flexWrap': 'wrap', 'margin': '6px 0 4px'}
        hidden = {**shown, 'display': 'none'}
        end = _as_date(end_date)
        end = latest if end is None or end > latest else end
        return (shown if period == CUSTOM else hidden), earliest, latest, end

    @app.callback(
        Output('perf-graph', 'figure'),
        Output('perf-stats', 'children'),
        Output('perf-note', 'children'),
        Input('perf-holdings', 'data'),
        Input('perf-period', 'value'),
        Input('perf-mode', 'value'),
        Input('perf-compare', 'value'),
        Input('perf-range', 'start_date'),
        Input('perf-range', 'end_date'),
        Input('refresh-interval', 'n_intervals'),
    )
    def render_performance(holdings, period, mode, compare_text, start, end, _interval):
        try:
            return build_performance(holdings, period or DEFAULT_PERIOD, mode or 'pct',
                                     compare_text, start, end)
        except Exception as e:  # keep the rest of the dashboard alive
            return _empty_figure("Performance chart unavailable."), [], f"Error: {e}"
