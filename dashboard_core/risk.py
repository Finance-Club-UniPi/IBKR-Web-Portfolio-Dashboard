"""Risk metrics page: summary-style cards plus Value-at-Risk and drawdown charts.

Computed from the same constant-holdings backcast as the performance chart.
The page is hidden unless the nav store says 'risk'; renders skip otherwise so
no history is fetched until the tab is opened.
"""

from __future__ import annotations

import plotly.graph_objects as go
from dash import Input, Output, State, dcc, html, no_update

from dashboard_core.helpers import ccy_symbol, section_label
from dashboard_core.performance import _empty_figure, _holdings_payload
from net_util import run_parallel
from portfolio_history import fx_pair, get_compare_history, get_estr_history, get_portfolio_history
from risk_metrics import compute_risk_metrics
from styles import (
    CARD,
    COLOR_BAD,
    COLOR_BAD_PLOT,
    COLOR_BORDER,
    COLOR_BRAND,
    COLOR_SURFACE_SOFT,
    COLOR_TEXT_FAINT,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_STRONG,
)

RISK_PERIODS = ['1Y', '3Y', '5Y']
_GRID = 'rgba(128,128,128,0.18)'
_TICK = dict(size=12, color='#888')
_NOTE_STYLE = {'fontSize': '13px', 'color': COLOR_TEXT_MUTED, 'margin': '8px 0 0', 'lineHeight': '1.5'}


def risk_page():
    """Static layout for the risk page (used by layout.py)."""
    return html.Div([
        html.Div([
            section_label("Risk metrics"),
            dcc.RadioItems(id='risk-period', className='seg', value='1Y',
                           options=[{'label': p, 'value': p} for p in RISK_PERIODS]),
        ], style={'display': 'flex', 'justifyContent': 'space-between',
                  'alignItems': 'center', 'flexWrap': 'wrap', 'gap': '8px', 'marginBottom': '14px'}),
        dcc.Loading(html.Div(id='risk-cards', style={
            'display': 'grid', 'gridTemplateColumns': 'repeat(4, 1fr)',
            'gap': '14px', 'marginBottom': '14px', 'minHeight': '120px'}),
            type='circle', delay_show=400),
        html.Div([
            html.Div([section_label("Value at Risk"),
                      dcc.Graph(id='risk-var-graph', figure=go.Figure(),
                                config={'displayModeBar': False}, style={'height': '340px'})],
                     style={**CARD, 'flex': '1 1 420px', 'minWidth': '0'}),
            html.Div([section_label("Maximum drawdown"),
                      dcc.Graph(id='risk-dd-graph', figure=go.Figure(),
                                config={'displayModeBar': False}, style={'height': '340px'})],
                     style={**CARD, 'flex': '1 1 420px', 'minWidth': '0'}),
        ], style={'display': 'flex', 'gap': '14px', 'flexWrap': 'wrap'}),
        html.P(id='risk-note', style=_NOTE_STYLE),
        dcc.Store(id='risk-holdings'),
    ], id='risk-page', style={'display': 'none'})


def _card(label, value, sub=None, bad=False):
    return html.Div([
        html.P(label, style={
            'fontSize': '14px', 'color': COLOR_TEXT_FAINT, 'margin': '0 0 10px',
            'textTransform': 'uppercase', 'letterSpacing': '0.05em', 'fontWeight': '500'}),
        html.P(value, style={'fontSize': '26px', 'fontWeight': '600', 'margin': '0',
                             'color': COLOR_TEXT_STRONG, 'letterSpacing': '-0.5px'}),
        html.Div(html.Span(sub or '', style={'fontSize': '14px', 'color': COLOR_TEXT_FAINT}),
                 style={'marginTop': '4px', 'minHeight': '20px'}),
    ], style={'background': COLOR_SURFACE_SOFT, 'borderRadius': '12px', 'padding': '18px',
              'borderLeft': f'3px solid {COLOR_BAD if bad else COLOR_BORDER}'})


def _pct(v):
    return '—' if v is None else f"{v * 100:.2f}%"


def _num(v, nd=2):
    return '—' if v is None else f"{v:.{nd}f}"


def _amt(v, sym):
    return None if v is None else f"{sym}{v:,.0f}"


def build_cards(m, sym):
    param_amts = [a for a in (_amt(m['var_param_95_amt'], sym), _amt(m['var_param_99_amt'], sym)) if a]
    return [
        _card("VaR 95% (1-day, hist.)", _pct(m['var_hist_95']), _amt(m['var_hist_95_amt'], sym), True),
        _card("VaR 99% (1-day, hist.)", _pct(m['var_hist_99']), _amt(m['var_hist_99_amt'], sym), True),
        _card("CVaR 95% (exp. shortfall)", _pct(m['cvar_95']), _amt(m['cvar_95_amt'], sym), True),
        _card("Parametric VaR 95% / 99%", f"{_pct(m['var_param_95'])} / {_pct(m['var_param_99'])}",
              ' / '.join(param_amts), True),
        _card("Volatility (ann.)", _pct(m['vol'])),
        _card("Return (ann.)", _pct(m['mean_ann']), "mean daily × 252"),
        _card("Sharpe (ann.)", _num(m['sharpe'])),
        _card("Sortino (ann.)", _num(m['sortino'])),
        _card("Max drawdown", _pct(m['max_drawdown']),
              f"{m['dd_peak']:%d %b %Y} → {m['dd_trough']:%d %b %Y}", True),
        _card("Beta vs S&P 500", _num(m['beta'])),
        _card("Jensen's alpha (ann.)", _pct(m['alpha'])),
        _card("Skew / Kurtosis", f"{_num(m['skew'])} / {_num(m['kurt'])}", "excess kurtosis"),
    ]


def _style_fig(fig, x=None, y=None):
    fig.update_layout(
        margin=dict(l=8, r=8, t=8, b=8), height=340, showlegend=False, barmode='stack', bargap=0.05,
        plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
        xaxis=dict(showgrid=False, tickfont=_TICK, **(x or {})),
        yaxis=dict(showgrid=True, gridcolor=_GRID, tickfont=_TICK, zeroline=False, **(y or {})),
        hoverlabel=dict(font=dict(size=13)),
    )
    return fig


def var_figure(m):
    r = m['returns'] * 100
    cut = -m['var_hist_95'] * 100
    size = float((r.max() - r.min()) / 40) or 0.1
    bins = dict(start=float(r.min()), end=float(r.max()) + size, size=size)
    fig = go.Figure()
    fig.add_trace(go.Histogram(x=r[r > cut], xbins=bins, marker_color=COLOR_BRAND,
                               hovertemplate='%{x:.2f}%<br>%{y} days<extra></extra>'))
    fig.add_trace(go.Histogram(x=r[r <= cut], xbins=bins, marker_color=COLOR_BAD_PLOT,
                               hovertemplate='%{x:.2f}%<br>%{y} days<extra>beyond VaR 95%</extra>'))
    for x, label, dash in ((-m['var_hist_95'], 'VaR 95%', 'dash'), (-m['var_hist_99'], 'VaR 99%', 'dot'),
                           (-m['cvar_95'], 'CVaR 95%', 'solid')):
        fig.add_vline(x=x * 100, line=dict(color=COLOR_BAD_PLOT, width=1.5, dash=dash),
                      annotation=dict(text=label, font=dict(size=11, color='#888'), textangle=-90,
                                      xanchor='right'), annotation_position='top left')
    return _style_fig(fig, x=dict(ticksuffix='%'), y=dict(title=dict(text='days', font=_TICK)))


def dd_figure(m):
    dd = m['drawdown'] * 100
    fig = go.Figure(go.Scatter(
        x=[d.strftime('%Y-%m-%d') for d in dd.index], y=dd.round(4).tolist(), mode='lines',
        fill='tozeroy', line=dict(color=COLOR_BAD_PLOT, width=1.5), fillcolor='rgba(220,38,38,0.25)',
        hovertemplate='%{y:.2f}%<extra>drawdown</extra>'))
    fig.add_trace(go.Scatter(
        x=[m['dd_trough'].strftime('%Y-%m-%d')], y=[m['max_drawdown'] * 100],
        mode='markers+text', marker=dict(color=COLOR_BAD_PLOT, size=9),
        text=[f"{m['max_drawdown'] * 100:.1f}%"], textposition='bottom center',
        textfont=dict(size=12, color='#888'), hoverinfo='skip'))
    return _style_fig(fig, y=dict(ticksuffix='%'))


def to_base(series, rate):
    """USD series converted with a base-per-USD rate series (rate forward-filled onto its dates)."""
    if series is None or rate is None:
        return None
    rate = rate[~rate.index.duplicated()]
    return series * rate.reindex(series.index.union(rate.index)).ffill().reindex(series.index)


def rf_over(rates, index):
    """Mean annual risk-free rate (fraction) over the dates of `index`; None if no overlap."""
    if rates is None or not len(index):
        return None
    window = rates.loc[index[0]:index[-1]]
    return float(window.mean()) / 100 if len(window) else None


def build_risk(holdings, period):
    """Return (cards, var figure, drawdown figure, note) for the holdings and lookback."""
    if not holdings:
        wait = _empty_figure("Waiting for portfolio data…")
        return [], wait, wait, ""
    base = holdings['base']
    calls = {
        'portfolio': lambda: get_portfolio_history(holdings['positions'], base, period),
        'bench':     lambda: get_compare_history('^GSPC', period),
        'rf':        get_estr_history if base == 'EUR' else (lambda: get_compare_history('^IRX', period)),
    }
    rf_name = "€STR, ECB euro short-term rate" if base == 'EUR' else "13-week US T-bill, ^IRX"
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
            f"current holdings valued at historical closes in {base}; cash, past trades and dividends are "
            f"not included. VaR and CVaR are 1-day, in {base} on the latest value. {rf_text}. "
            f"Beta and alpha are against the S&P 500 (^GSPC) in {bench_ccy or base}"
            + ("." if m['beta'] is not None else ", which is unavailable."))
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
    )
    def render_risk(holdings, period, page):
        if page != 'risk':
            return no_update, no_update, no_update, no_update
        try:
            return build_risk(holdings, period or '1Y')
        except Exception as e:  # keep the rest of the dashboard alive
            msg = _empty_figure("Risk metrics unavailable.")
            return [], msg, msg, f"Error: {e}"
