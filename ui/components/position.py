"""Position detail components: sparkline, fundamentals grid, ETF and stock briefs."""

from __future__ import annotations

import logging

import plotly.graph_objects as go
from dash import dcc, html

from backend.portfolio.fundamentals import score_fundamentals
from backend.providers.market_data import get_price_history
from ui.styles import (
    COLOR_BAD,
    COLOR_BORDER_HEAVY,
    COLOR_BORDER_LIGHT,
    COLOR_BRAND,
    COLOR_GOOD,
    COLOR_SURFACE,
    COLOR_SURFACE_WHITE,
    COLOR_TEXT,
    COLOR_TEXT_MID,
    COLOR_TEXT_STRONG,
    COLOR_WARN,
)

log = logging.getLogger(__name__)

_PERIOD_TO_YF   = {'1M': '1mo', '3M': '3mo', '1Y': '1y', '3Y': '3y', '5Y': '5y'}


def _stat(label, value, accent=None):
    return html.Div([
        html.P(label, style={
            'fontSize': '13px', 'color': COLOR_TEXT_MID, 'margin': '0 0 4px',
            'textTransform': 'uppercase', 'letterSpacing': '0.05em',
            'fontWeight': '600',
        }),
        html.P(value, style={
            'fontSize': '17px', 'fontWeight': '600', 'margin': '0',
            'color': accent or COLOR_TEXT_STRONG,
        }),
    ], style={'minWidth': '90px'})


def _period_btn(label, active):
    return html.Button(label, id={'type': 'period-btn', 'index': label},
                       n_clicks=0, style={
        'background': COLOR_BRAND if active else 'transparent',
        'color':      COLOR_SURFACE_WHITE    if active else COLOR_TEXT_MID,
        'border':     '1px solid ' + (COLOR_BRAND if active else COLOR_BORDER_HEAVY),
        'borderRadius': '6px', 'padding': '4px 12px',
        'fontSize': '12px', 'cursor': 'pointer',
        'fontFamily': 'inherit', 'fontWeight': '500',
    })


def _build_price_sparkline(ticker, period, avg_cost, trades=None, position=None):
    yf_period = _PERIOD_TO_YF.get(period, '1mo')
    try:
        if position:
            from backend.providers.market_data import get_position_history
            hist = {ticker: get_position_history(position, yf_period)}
        else:
            hist = get_price_history([ticker], yf_period)
    except Exception as e:
        log.debug('sparkline fetch failed for %s: %s', ticker, e)
        hist = {}
    data_h = hist.get(ticker) or {}
    prices = data_h.get('prices') or []
    dates  = data_h.get('dates')  or []
    if len(prices) < 2:
        return html.P('Chart data unavailable for this period.',
                      style={'color': COLOR_TEXT_MID, 'fontSize': '14px', 'fontWeight': '500',
                             'textAlign': 'center', 'margin': '24px 0 8px'})

    from ui.helpers import ccy_symbol
    currency_symbol = ccy_symbol((position or {}).get('currency', 'USD'))
    first, last = prices[0], prices[-1]
    up = last >= first
    line_color = COLOR_GOOD if up else COLOR_BAD
    fill_color = 'rgba(22,163,74,0.08)' if up else 'rgba(220,38,38,0.08)'

    fig = go.Figure(go.Scatter(
        x=dates, y=prices, mode='lines',
        line=dict(color=line_color, width=2),
        fill='tozeroy', fillcolor=fill_color,
        hovertemplate='%{x}<br>' + currency_symbol + '%{y:,.2f}<extra></extra>',
    ))
    # Average cost reference line (if we know it and it's within chart range)
    if avg_cost and avg_cost == avg_cost and avg_cost > 0:
        lo, hi = min(prices), max(prices)
        if lo * 0.6 <= avg_cost <= hi * 1.4:
            fig.add_hline(y=avg_cost, line=dict(color='#888', width=1, dash='dot'),
                          annotation_text=f'Avg {currency_symbol}{avg_cost:,.2f}',
                          annotation_position='top left',
                          annotation=dict(font=dict(size=12, color='#888')))

    # Trade markers (BUY ▲ green / SELL ▼ red) on trades whose date falls
    # within the plotted window.  `dates` are 'YYYY-MM-DD' strings.
    if trades and dates:
        date_set = set(dates)
        buys_x,  buys_y,  buys_hover  = [], [], []
        sells_x, sells_y, sells_hover = [], [], []
        for t in trades:
            tm = (t.get('time') or '')[:10]
            if not tm or tm not in date_set:
                continue
            try:
                y = prices[dates.index(tm)]
            except ValueError:
                continue
            side   = (t.get('side') or '').upper()
            shares = t.get('shares') or 0
            price_ = t.get('price') or 0
            hover  = f"{side} {shares:g} @ {currency_symbol}{price_:,.2f}<br>{tm}"
            if side == 'BUY':
                buys_x.append(tm);  buys_y.append(y);  buys_hover.append(hover)
            elif side == 'SELL':
                sells_x.append(tm); sells_y.append(y); sells_hover.append(hover)
        if buys_x:
            fig.add_trace(go.Scatter(
                x=buys_x, y=buys_y, mode='markers', name='BUY',
                marker=dict(symbol='triangle-up', color=COLOR_GOOD,
                            size=24, line=dict(color='rgba(0,0,0,0)', width=1)),
                hovertext=buys_hover, hoverinfo='text',
            ))
        if sells_x:
            fig.add_trace(go.Scatter(
                x=sells_x, y=sells_y, mode='markers', name='SELL',
                marker=dict(symbol='triangle-down', color=COLOR_BAD,
                            size=24, line=dict(color='rgba(0,0,0,0)', width=1)),
                hovertext=sells_hover, hoverinfo='text',
            ))
    pct_change = (last - first) / first * 100 if first else 0
    # Zoom y-axis to the actual price range with a small padding, and also
    # include avg_cost if it's on-chart so the dotted reference line stays
    # visible.  Without this, fill='tozeroy' anchors the axis at 0 and small
    # real ranges (e.g. $15→$17) render as a nearly-flat line.
    y_lo, y_hi = min(prices), max(prices)
    if avg_cost and avg_cost > 0 and y_lo * 0.6 <= avg_cost <= y_hi * 1.4:
        y_lo = min(y_lo, avg_cost)
        y_hi = max(y_hi, avg_cost)
    pad = (y_hi - y_lo) * 0.15 or y_hi * 0.02 or 1.0
    fig.update_layout(
        margin=dict(l=8, r=8, t=8, b=8), height=200,
        xaxis=dict(showgrid=False, showticklabels=False, title=None),
        yaxis=dict(showgrid=True, gridcolor='#f2f2f2', title=None,
                   tickfont=dict(size=12, color='#888'),
                   range=[y_lo - pad, y_hi + pad]),
        plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
        showlegend=False,
        hoverlabel=dict(bordercolor=COLOR_BRAND,
                        font=dict(size=14, family='Inter, system-ui, sans-serif')),
    )
    summary_line = html.Div([
        html.Span(f"{period} ", style={'color': COLOR_TEXT_MID, 'fontSize': '13px',
                                        'fontWeight': '500',
                                        'letterSpacing': '0.05em'}),
        html.Span(f"{'+' if pct_change >= 0 else ''}{pct_change:.2f}%",
                  style={'color': line_color, 'fontSize': '13px',
                         'fontWeight': '600', 'marginLeft': '4px'}),
    ], style={'textAlign': 'right', 'marginTop': '2px'})
    return html.Div([
        dcc.Graph(figure=fig, config={'displayModeBar': False}),
        summary_line,
    ])


def _build_brief_section(f: dict, scored: dict, pos: dict) -> html.Div:
    """Investment brief view: signals, narrative, risk/reward, condensed metrics."""
    sig = scored.get('signals', {})

    _CHIP_BASE = {
        'display': 'inline-block', 'padding': '3px 10px',
        'borderRadius': '12px', 'fontSize': '11px', 'fontWeight': '600',
        'marginRight': '6px', 'marginBottom': '6px', 'letterSpacing': '0.04em',
    }
    _CHIP_LABELS = [
        ('valuation',     'Valuation'),
        ('growth',        'Growth'),
        ('profitability', 'Profitability'),
        ('fin_health',    'Financial Health'),
        ('sentiment',     'Sentiment'),
        ('momentum',      'Momentum'),
    ]
    chips = []
    for key, label in _CHIP_LABELS:
        s = sig.get(key, {})
        c = s.get('color', '#9ca3af')
        chips.append(html.Span(
            f"{label}: {s.get('label', '—')}",
            style={**_CHIP_BASE, 'background': c + '18', 'color': c,
                   'border': f'1px solid {c}40'},
        ))

    overall_block = html.Div([
        html.Div(scored.get('overall', ''), style={
            'fontSize': '14px', 'fontWeight': '600', 'color': COLOR_TEXT_STRONG,
            'marginBottom': '3px',
        }),
        html.Div('→ ' + scored.get('overall_sub', ''), style={
            'fontSize': '12px', 'color': COLOR_TEXT_MID, 'fontStyle': 'italic',
        }),
    ], style={'marginBottom': '10px'})

    chips_block = html.Div(chips, style={'marginBottom': '10px'})

    sentences = scored.get('explanation', [])
    explanation_block = html.Div([
        html.P(s, style={'fontSize': '13px', 'color': COLOR_TEXT,
                         'margin': '0 0 4px', 'lineHeight': '1.5'})
        for s in sentences
    ], style={'marginBottom': '10px'}) if sentences else None

    rr_block = html.Div([
        html.Div([
            html.Div("↑ Upside", style={
                'fontSize': '11px', 'fontWeight': '700', 'color': COLOR_GOOD,
                'textTransform': 'uppercase', 'letterSpacing': '0.06em', 'marginBottom': '4px',
            }),
            html.Div(scored.get('upside', ''), style={
                'fontSize': '12px', 'color': COLOR_TEXT, 'lineHeight': '1.4',
            }),
        ], style={'flex': '1', 'paddingRight': '10px'}),
        html.Div(style={'width': '1px', 'background': COLOR_BORDER_LIGHT, 'alignSelf': 'stretch'}),
        html.Div([
            html.Div("↓ Downside", style={
                'fontSize': '11px', 'fontWeight': '700', 'color': COLOR_BAD,
                'textTransform': 'uppercase', 'letterSpacing': '0.06em', 'marginBottom': '4px',
            }),
            html.Div(scored.get('downside', ''), style={
                'fontSize': '12px', 'color': COLOR_TEXT, 'lineHeight': '1.4',
            }),
        ], style={'flex': '1', 'paddingLeft': '10px'}),
    ], style={
        'display': 'flex', 'marginBottom': '10px',
        'padding': '8px 0',
        'borderTop': f'1px solid {COLOR_BORDER_LIGHT}',
        'borderBottom': f'1px solid {COLOR_BORDER_LIGHT}',
    })

    watch = scored.get('watch', [])
    watch_block = html.Div([
        html.Div("What to watch", style={
            'fontSize': '11px', 'fontWeight': '700', 'color': COLOR_TEXT_MID,
            'textTransform': 'uppercase', 'letterSpacing': '0.06em', 'marginBottom': '4px',
        }),
        html.Ul([
            html.Li(w, style={'fontSize': '12px', 'color': COLOR_TEXT, 'marginBottom': '2px'})
            for w in watch
        ], style={'margin': '0', 'paddingLeft': '16px'}),
    ], style={'marginBottom': '10px'}) if watch else None

    # Condensed metric rows with inline interpretation
    def _cm(label, val_str, interp, color):
        parts = [
            html.Span(label + ": ", style={'fontSize': '11px', 'color': COLOR_TEXT_MID, 'fontWeight': '600'}),
            html.Span(val_str or '—', style={'fontSize': '12px', 'fontWeight': '700',
                                              'color': color or COLOR_TEXT_STRONG}),
        ]
        if interp:
            parts.append(html.Span(f' · {interp}', style={'fontSize': '11px', 'color': COLOR_TEXT_MID}))
        return html.Div(parts, style={'marginBottom': '3px'})

    def _grade_pe(v):
        if v is None: return '—', None, '#9ca3af'
        if v > 40:  return f"{v:.1f}×", "very expensive", '#dc2626'
        if v > 25:  return f"{v:.1f}×", "expensive",      '#ea580c'
        if v > 15:  return f"{v:.1f}×", "fair",            '#d97706'
        return          f"{v:.1f}×", "cheap",           '#16a34a'

    def _grade_margin(v):
        if v is None: return '—', None, '#9ca3af'
        if v > 20:  return f"{v:.1f}%", "high margin",    '#16a34a'
        if v > 10:  return f"{v:.1f}%", "decent margin",  '#d97706'
        if v >= 0:  return f"{v:.1f}%", "thin margin",    '#ea580c'
        return          f"{v:.1f}%", "losing money",   '#dc2626'

    def _grade_fcf(v):
        if v is None: return '—', None, '#9ca3af'
        if v > 5:   return f"{v:.1f}%", "cash machine",   '#16a34a'
        if v > 0:   return f"{v:.1f}%", "positive",       '#d97706'
        return          f"{v:.1f}%", "burning cash",   '#dc2626'

    def _grade_debt(v):
        if v is None: return '—', None, '#9ca3af'
        if v < 1:   return f"{v:.1f}×", "very low",       '#16a34a'
        if v < 3:   return f"{v:.1f}×", "manageable",     '#d97706'
        if v < 5:   return f"{v:.1f}×", "elevated",       '#ea580c'
        return          f"{v:.1f}×", "high risk",      '#dc2626'

    pe_s, pe_i, pe_c   = _grade_pe(f.get('fwd_pe'))
    mg_s, mg_i, mg_c   = _grade_margin(f.get('profit_margin'))
    fcf_s, fcf_i, fcf_c = _grade_fcf(f.get('fcf_yield'))
    dbt_s, dbt_i, dbt_c = _grade_debt(f.get('debt_ebitda'))

    peg = f.get('peg')
    rev = f.get('rev_growth_pct')
    roe = f.get('roe')
    ncp = f.get('net_cash_pct')
    grw_c = sig.get('growth', {}).get('color', '#9ca3af')

    metrics_block = html.Div([
        html.Div("Core Metrics", style={
            'fontSize': '11px', 'fontWeight': '700', 'color': COLOR_TEXT_MID,
            'textTransform': 'uppercase', 'letterSpacing': '0.06em', 'marginBottom': '6px',
        }),
        html.Div([
            html.Div([
                _cm("Fwd P/E",      pe_s,  pe_i,  pe_c),
                _cm("PEG",         f"{peg:.2f}" if peg else None, "growth-adjusted", '#9ca3af'),
                _cm("Rev Growth",  f"{rev:+.1f}%/yr" if rev is not None else None,
                    ("very fast" if rev and rev > 20 else
                     "solid"    if rev and rev > 10 else
                     "slow"     if rev is not None and rev >= 0 else
                     "declining") if rev is not None else None,
                    grw_c),
            ], style={'flex': '1', 'minWidth': '130px'}),
            html.Div([
                _cm("Net Margin",  mg_s,  mg_i,  mg_c),
                _cm("ROE",
                    f"{roe:.1f}%" if roe is not None else None,
                    ("strong"   if roe is not None and roe > 20 else
                     "moderate" if roe is not None and roe > 10 else
                     "weak")    if roe is not None else None,
                    '#16a34a' if roe is not None and roe > 20 else
                    '#d97706' if roe is not None and roe > 10 else
                    '#ea580c' if roe is not None else '#9ca3af'),
                _cm("FCF Yield",   fcf_s, fcf_i, fcf_c),
            ], style={'flex': '1', 'minWidth': '130px'}),
            html.Div([
                _cm("Debt/EBITDA", dbt_s, dbt_i, dbt_c),
                _cm("Net Cash",
                    f"+{ncp:.1f}% of mkt cap" if ncp is not None and ncp > 0 else
                    f"{ncp:.1f}% (net debt)"  if ncp is not None else None,
                    None,
                    '#16a34a' if ncp is not None and ncp > 0 else
                    '#dc2626' if ncp is not None and ncp < -10 else
                    '#d97706' if ncp is not None else '#9ca3af'),
            ], style={'flex': '1', 'minWidth': '130px'}),
        ], style={'display': 'flex', 'gap': '12px', 'flexWrap': 'wrap'}),
    ], style={'borderTop': f'1px solid {COLOR_BORDER_LIGHT}',
              'paddingTop': '8px', 'marginBottom': '10px'})

    gain_pct = pos.get('cost_diff_pct')
    alloc    = pos.get('allocation_pct')
    pos_parts = []
    if gain_pct is not None:
        gain_color = COLOR_GOOD if gain_pct >= 0 else COLOR_BAD
        pos_parts += [
            html.Span("Your gain: ", style={'color': COLOR_TEXT_MID}),
            html.Span(f"{gain_pct:+.1f}%", style={'color': gain_color, 'fontWeight': '700'}),
        ]
    if alloc:
        pos_parts += [
            html.Span("  ·  Portfolio weight: ", style={'color': COLOR_TEXT_MID}),
            html.Span(f"{alloc:.1f}%", style={'fontWeight': '600', 'color': COLOR_TEXT_STRONG}),
        ]
        if alloc > 10:
            pos_parts.append(html.Span(
                " — large position, increases portfolio risk",
                style={'color': COLOR_WARN, 'fontSize': '11px'},
            ))
        elif alloc > 5:
            pos_parts.append(html.Span(
                " — meaningful position",
                style={'color': COLOR_TEXT_MID, 'fontSize': '11px'},
            ))
    pos_block = html.Div(pos_parts, style={
        'fontSize': '12px', 'padding': '6px 0',
        'borderTop': f'1px solid {COLOR_BORDER_LIGHT}', 'marginBottom': '8px',
    }) if pos_parts else None

    takeaway = scored.get('takeaway', '')
    takeaway_block = html.Div([
        html.Span("💡 ", style={'fontSize': '14px'}),
        html.Span(takeaway, style={'fontSize': '12px', 'fontStyle': 'italic',
                                   'color': COLOR_TEXT, 'lineHeight': '1.5'}),
    ], style={
        'borderLeft': f'3px solid {COLOR_BRAND}', 'paddingLeft': '8px', 'marginTop': '4px',
    }) if takeaway else None

    return html.Div([c for c in [
        overall_block, chips_block, explanation_block,
        rr_block, watch_block, metrics_block, pos_block, takeaway_block,
    ] if c is not None])


def _build_etf_section(etf: dict, pos: dict | None = None) -> html.Div | None:
    """
    ETF-specific panel shown instead of the stock fundamentals section.
    Shows fund identity, sector breakdown bars, fund stats, and position context.
    Returns None if etf is empty.
    """
    if not etf:
        return None

    pos = pos or {}
    long_name      = etf.get('long_name', '')
    category       = etf.get('category', 'ETF / Fund')
    expense        = etf.get('expense_ratio')
    assets         = etf.get('total_assets')
    sector_weights = etf.get('sector_weights') or {}
    geo            = etf.get('geo')

    _HDR = {
        'fontSize': '13px', 'color': COLOR_TEXT_MID, 'fontWeight': '600',
        'textTransform': 'uppercase', 'letterSpacing': '0.05em',
    }
    _DIVIDER_STYLE = {
        'borderTop': f'1px solid {COLOR_BORDER_LIGHT}',
        'marginTop': '14px', 'paddingTop': '14px',
    }

    # ── Fund identity ─────────────────────────────────────────────────────────
    meta = []
    if geo:
        meta.append(geo)
    meta.append(category)
    if expense is not None:
        meta.append(f"Expense ratio: {expense:.2f}%")
    if assets is not None:
        if assets >= 1e9:
            meta.append(f"AUM: ${assets / 1e9:.1f}B")
        elif assets >= 1e6:
            meta.append(f"AUM: ${assets / 1e6:.0f}M")

    identity = html.Div([
        html.Div(long_name, style={
            'fontWeight': '600', 'fontSize': '14px', 'color': COLOR_TEXT_STRONG,
            'marginBottom': '3px',
        }),
        html.Div("  ·  ".join(meta), style={
            'fontSize': '12px', 'color': COLOR_TEXT_MID,
        }),
    ], style={'marginBottom': '16px'})

    # ── Sector breakdown bars ─────────────────────────────────────────────────
    sector_block = None
    if sector_weights:
        sorted_sw  = sorted(sector_weights.items(), key=lambda x: x[1], reverse=True)[:8]
        max_weight = sorted_sw[0][1] if sorted_sw else 1

        rows = []
        for sector_name, weight in sorted_sw:
            pct       = weight * 100
            bar_frac  = weight / max_weight  # relative width 0–1
            rows.append(html.Div([
                html.Div(sector_name, style={
                    'width': '148px', 'minWidth': '148px',
                    'fontSize': '12px', 'color': COLOR_TEXT_MID, 'fontWeight': '500',
                }),
                html.Div(
                    html.Div(style={
                        'width': f'{bar_frac * 100:.1f}%', 'height': '100%',
                        'background': COLOR_BRAND, 'borderRadius': '2px',
                        'transition': 'width 0.3s ease',
                    }),
                    style={
                        'flex': '1', 'height': '7px',
                        'background': COLOR_BORDER_LIGHT,
                        'borderRadius': '2px', 'margin': '0 10px',
                        'alignSelf': 'center',
                    },
                ),
                html.Div(f"{pct:.1f}%", style={
                    'width': '38px', 'fontSize': '12px',
                    'color': COLOR_TEXT_STRONG, 'fontWeight': '600',
                    'textAlign': 'right',
                }),
            ], style={'display': 'flex', 'alignItems': 'center', 'marginBottom': '6px'}))

        sector_block = html.Div([
            html.Div("Sector Breakdown", style={**_HDR, 'marginBottom': '10px'}),
            *rows,
        ])
    else:
        sector_block = html.Div(
            "Sector breakdown unavailable for this ETF.",
            style={'fontSize': '12px', 'color': COLOR_TEXT_MID, 'fontStyle': 'italic'},
        )

    # ── ETF note ──────────────────────────────────────────────────────────────
    note = html.Div(
        "ETFs hold a basket of securities — stock-level metrics (P/E, revenue growth) "
        "don't apply to the fund itself.",
        style={
            **_DIVIDER_STYLE,
            'fontSize': '12px', 'color': COLOR_TEXT_MID, 'fontStyle': 'italic',
        },
    )

    # ── Position context ──────────────────────────────────────────────────────
    pos_block = None
    gain  = pos.get('cost_diff_pct')
    alloc = pos.get('allocation_pct')
    if gain is not None or alloc is not None:
        gain_color = COLOR_GOOD if (gain or 0) >= 0 else COLOR_BAD
        pieces = []
        if gain is not None:
            pieces.append(html.Span(f"{gain:+.1f}% gain",
                                    style={'color': gain_color, 'fontWeight': '600'}))
        if alloc is not None:
            if pieces:
                pieces.append(html.Span("  ·  ", style={'color': COLOR_TEXT_MID}))
            pieces.append(html.Span(f"{alloc:.1f}% of portfolio",
                                    style={'color': COLOR_TEXT_MID}))
        pos_block = html.Div(
            [html.Span("Your position: ", style={'color': COLOR_TEXT_MID}), *pieces],
            style={**_DIVIDER_STYLE, 'fontSize': '13px'},
        )

    return html.Div([
        html.Div("Fund Overview", style={**_HDR, 'marginBottom': '14px'}),
        identity,
        sector_block,
        note,
        *([pos_block] if pos_block else []),
    ], style={
        'marginTop': '18px',
        'padding': '14px 16px 10px',
        'background': COLOR_SURFACE,
        'borderRadius': '10px',
        'border': f'1px solid {COLOR_BORDER_LIGHT}',
    })


def _build_fundamentals_section(f: dict, pos: dict | None = None,
                                view_mode: str = 'grid') -> html.Div | None:
    """
    Render the Valuation / Growth / Sentiment 3-column panel + Technicals row,
    with a toggle button to switch to the Investment Brief view.
    view_mode='grid' or 'brief' controls which view is shown on render.
    Returns None when f is empty (e.g. ETF with no data).
    """
    if not f:
        return None

    # ── helpers ───────────────────────────────────────────────────────────────
    _COL_HDR = {
        'fontSize': '11px', 'fontWeight': '700', 'color': COLOR_TEXT_MID,
        'textTransform': 'uppercase', 'letterSpacing': '0.08em',
        'borderBottom': f'1px solid {COLOR_BORDER_LIGHT}',
        'paddingBottom': '5px', 'marginBottom': '10px',
    }

    def _metric(label, value, subtitle, color=None, tooltip=None):
        label_content = html.Div([
            label,
            html.Span([
                "ⓘ",
                html.Span(tooltip, className='metric-tooltip'),
            ], className='metric-info-icon') if tooltip else None,
        ], style={
            'fontSize': '11px', 'color': COLOR_TEXT_MID,
            'textTransform': 'uppercase', 'letterSpacing': '0.06em',
            'fontWeight': '600', 'marginBottom': '1px',
            'display': 'flex', 'alignItems': 'center', 'gap': '4px',
        })
        return html.Div([
            label_content,
            html.Div(value or '—', style={
                'fontSize': '16px', 'fontWeight': '700',
                'color': color or COLOR_TEXT_STRONG,
                'lineHeight': '1.2',
            }),
            html.Div(subtitle, style={
                'fontSize': '11px', 'color': COLOR_TEXT_MID, 'marginTop': '1px',
            }),
        ], style={'marginBottom': '10px'})

    _COL_STYLE = {'flex': '1', 'minWidth': '120px'}
    _DIVIDER   = {'width': '1px', 'background': COLOR_BORDER_LIGHT,
                  'margin': '0 14px', 'alignSelf': 'stretch'}

    # ── Valuation column ──────────────────────────────────────────────────────
    pe   = f.get('fwd_pe')
    peg  = f.get('peg')
    ev   = f.get('ev_ebitda')

    pe_color  = (COLOR_GOOD if pe  and pe  < 20 else
                 COLOR_WARN if pe  and pe  < 35 else
                 COLOR_BAD  if pe  else None)
    peg_color = (COLOR_GOOD if peg and peg < 1  else
                 COLOR_WARN if peg and peg < 2  else
                 COLOR_BAD  if peg else None)
    ev_color  = (COLOR_GOOD if ev  and ev  < 15 else
                 COLOR_WARN if ev  and ev  < 25 else
                 COLOR_BAD  if ev  else None)

    val_col = html.Div([
        html.Div("Valuation", style=_COL_HDR),
        _metric("Fwd P/E",    f"{pe:.1f}×"   if pe   else None, "price vs expected earnings", pe_color,
                tooltip="Forward Price-to-Earnings: price divided by expected next-year earnings. <20× is generally good; >35× is expensive relative to history."),
        _metric("PEG ratio",  f"{peg:.2f}"   if peg  else None, "value adjusted for growth",  peg_color,
                tooltip="P/E divided by the earnings growth rate. <1 = potentially undervalued for its growth; >2 = expensive. Combines value and growth in one number."),
        _metric("EV/EBITDA",  f"{ev:.1f}×"   if ev   else None, "company vs operating profit", ev_color,
                tooltip="Enterprise Value divided by operating profit (before interest, taxes, depreciation & amortisation). <15× is generally good value; >25× is expensive."),
    ], style=_COL_STYLE)

    # ── Growth column ─────────────────────────────────────────────────────────
    rev    = f.get('rev_growth_pct')
    margin = f.get('profit_margin')
    beat   = f.get('eps_beat')

    rev_color    = (COLOR_GOOD if rev    is not None and rev    > 10 else
                    COLOR_WARN if rev    is not None and rev    >= 0 else
                    COLOR_BAD  if rev    is not None else None)
    margin_color = (COLOR_GOOD if margin is not None and margin > 20 else
                    COLOR_WARN if margin is not None and margin >= 5  else
                    COLOR_BAD  if margin is not None else None)

    growth_col = html.Div([
        html.Div("Growth", style=_COL_HDR),
        _metric("Revenue",    f"{rev:+.1f}%/yr" if rev    is not None else None, "annual revenue change", rev_color,
                tooltip="Year-over-year change in total sales. >10%/yr indicates strong expansion. Negative means the company is shrinking."),
        _metric("Net Margin", f"{margin:.1f}%"   if margin is not None else None, "profit per $1 of revenue", margin_color,
                tooltip="Percentage of revenue kept as profit after all costs. >20% is strong; <5% is thin. Higher margins mean a more efficient or competitively protected business."),
        _metric("EPS Beat",   beat,                                                "earnings surprises (last 4 qtrs)",
                tooltip="How often the company beat analyst earnings-per-share forecasts over the last 4 quarters. Consistent beats signal disciplined management and conservative guidance."),
    ], style=_COL_STYLE)

    # ── Sentiment column ──────────────────────────────────────────────────────
    buy, hold_n, sell = f.get('analyst_buy'), f.get('analyst_hold'), f.get('analyst_sell')
    target  = f.get('price_target')
    upside  = f.get('target_upside')
    short   = f.get('short_pct')
    insider = f.get('insider_net')

    # Analyst counts as coloured chips
    if all(v is not None for v in [buy, hold_n, sell]):
        analyst_val = html.Span([
            html.Span(f"{buy}", style={'color': COLOR_GOOD, 'fontWeight': '700'}),
            html.Span(" Buy  ", style={'color': COLOR_TEXT_MID}),
            html.Span(f"{hold_n}", style={'color': COLOR_TEXT_STRONG, 'fontWeight': '700'}),
            html.Span(" Hold  ", style={'color': COLOR_TEXT_MID}),
            html.Span(f"{sell}", style={'color': COLOR_BAD, 'fontWeight': '700'}),
            html.Span(" Sell", style={'color': COLOR_TEXT_MID}),
        ], style={'fontSize': '14px', 'fontWeight': '600'})
    else:
        analyst_val = None

    # Price target with upside
    if target:
        upside_color = COLOR_GOOD if upside and upside > 0 else COLOR_BAD if upside and upside < 0 else COLOR_TEXT_MID
        target_val = html.Span([
            html.Span(f"${target:,.2f}", style={'color': COLOR_TEXT_STRONG, 'fontWeight': '700', 'fontSize': '16px'}),
            html.Span(f"  ({upside:+.1f}%)" if upside is not None else '',
                      style={'color': upside_color, 'fontSize': '13px', 'fontWeight': '600'}),
        ])
    else:
        target_val = None

    short_color   = (COLOR_GOOD if short is not None and short < 2  else
                     COLOR_WARN if short is not None and short < 5  else
                     COLOR_BAD  if short is not None else None)
    insider_color = (COLOR_GOOD if insider == 'Net+'
                     else COLOR_BAD  if insider == 'Net−'
                     else COLOR_TEXT_MID)

    # Short + Insider on one row
    if short is not None or insider:
        si_val = html.Span([
            *([html.Span(f"{short:.1f}%", style={'color': short_color, 'fontWeight': '700'}),
               html.Span(" short  ", style={'color': COLOR_TEXT_MID})] if short is not None else []),
            *([html.Span(insider, style={'color': insider_color, 'fontWeight': '700'}),
               html.Span(" insider", style={'color': COLOR_TEXT_MID})] if insider else []),
        ], style={'fontSize': '14px'})
    else:
        si_val = None

    sentiment_col = html.Div([
        html.Div("Sentiment", style=_COL_HDR),
        _metric("Analysts",     analyst_val, "Wall Street recommendations",
                tooltip="Aggregated buy/hold/sell ratings from professional Wall Street analysts. A high buy count relative to sells signals broadly positive sentiment."),
        _metric("Price Target", target_val,  "analyst consensus forecast",
                tooltip="Average price target across all analyst forecasts, plus the implied upside or downside vs the current price. Useful as a rough consensus view — not a guarantee."),
        _metric("Short / Insider", si_val,   "bearish bets · exec activity",
                tooltip="Short interest = % of shares being bet against (high short % = more bearish positioning). Insider activity = net buying or selling by executives based on SEC filings."),
    ], style=_COL_STYLE)

    three_cols = html.Div([
        val_col,
        html.Div(style=_DIVIDER),
        growth_col,
        html.Div(style=_DIVIDER),
        sentiment_col,
    ], style={'display': 'flex', 'padding': '14px 0 8px'})

    # ── Technicals row ────────────────────────────────────────────────────────
    rsi       = f.get('rsi_14')
    above_50d = f.get('above_50d_ma')
    vol       = f.get('volume_vs_avg')

    def _tech_info(tooltip_text):
        return html.Span([
            "ⓘ",
            html.Span(tooltip_text, className='metric-tooltip'),
        ], className='metric-info-icon', style={'fontSize': '11px', 'marginLeft': '3px'})

    tech_chips = []
    if rsi is not None:
        rsi_color = (COLOR_BAD  if rsi > 70 else
                     COLOR_GOOD if rsi < 30 else COLOR_TEXT_STRONG)
        rsi_tag   = 'overbought' if rsi > 70 else 'oversold' if rsi < 30 else 'neutral'
        tech_chips.append(html.Span([
            html.Span("RSI ", style={'color': COLOR_TEXT_MID, 'fontSize': '12px'}),
            html.Span(f"{rsi:.0f}", style={'color': rsi_color, 'fontWeight': '700'}),
            html.Span(f" ({rsi_tag})", style={'color': COLOR_TEXT_MID, 'fontSize': '12px'}),
            _tech_info("Relative Strength Index (14-day). Measures price momentum. >70 = overbought (may pull back soon); <30 = oversold (may bounce). 30–70 = neutral trend."),
        ], style={'marginRight': '14px', 'position': 'relative'}))

    if above_50d is not None:
        ma_color = COLOR_GOOD if above_50d else COLOR_BAD
        ma_text  = "Above 50d MA" if above_50d else "Below 50d MA"
        tech_chips.append(html.Span([
            html.Span(ma_text, style={
                'color': ma_color, 'fontWeight': '600', 'fontSize': '13px',
            }),
            _tech_info("Whether the stock is trading above or below its 50-day moving average — a widely watched trend signal. Above = bullish momentum; below = bearish."),
        ], style={'marginRight': '14px', 'position': 'relative'}))

    if vol:
        vol_color = COLOR_WARN if vol == 'High' else COLOR_TEXT_MID
        tech_chips.append(html.Span([
            html.Span("Volume ", style={'color': COLOR_TEXT_MID, 'fontSize': '12px'}),
            html.Span(vol, style={'color': vol_color, 'fontWeight': '600', 'fontSize': '13px'}),
            _tech_info("Today's trading volume vs the 10-day average. High volume on a price move adds conviction — it means more participants agree. Low volume moves are less reliable."),
        ], style={'position': 'relative'}))

    tech_row = html.Div([
        html.Div("Technicals", style={**_COL_HDR, 'borderBottom': 'none',
                                      'paddingBottom': 0, 'marginBottom': '6px',
                                      'display': 'inline-block', 'marginRight': '10px'}),
        *tech_chips,
    ], style={
        'borderTop': f'1px solid {COLOR_BORDER_LIGHT}',
        'paddingTop': '10px', 'marginTop': '4px',
        'display': 'flex', 'alignItems': 'center', 'flexWrap': 'wrap', 'gap': '4px',
    }) if tech_chips else None

    # Don't render the section at all if every value is None (e.g. bond ETFs)
    has_data = any([pe, peg, ev, rev, margin, beat, buy, target, short, insider,
                    rsi, above_50d, vol])
    if not has_data:
        return None

    scored = score_fundamentals(f)
    pos    = pos or {}

    showing_brief  = view_mode == 'brief'
    toggle_label   = '← Metric Grid' if showing_brief else '📊 Investment Brief'
    grid_display   = {'display': 'none'} if showing_brief else {'display': 'block'}
    brief_display  = {'display': 'block', 'marginTop': '10px'} if showing_brief else {'display': 'none', 'marginTop': '10px'}

    toggle_btn = html.Button(
        toggle_label,
        id='fund-toggle-btn',
        n_clicks=0,
        style={
            'background': 'transparent', 'color': COLOR_BRAND,
            'border': f'1px solid {COLOR_BRAND}', 'borderRadius': '6px',
            'padding': '3px 10px', 'fontSize': '12px', 'cursor': 'pointer',
            'fontWeight': '600', 'fontFamily': 'inherit',
        },
    )

    header = html.Div([
        html.Div("Fundamentals & Signals", style={
            'fontSize': '13px', 'color': COLOR_TEXT_MID, 'fontWeight': '600',
            'textTransform': 'uppercase', 'letterSpacing': '0.05em',
        }),
        toggle_btn,
    ], style={'display': 'flex', 'justifyContent': 'space-between', 'alignItems': 'center'})

    grid_view = html.Div(
        [three_cols, tech_row],
        id='fund-grid-view',
        style=grid_display,
    )
    brief_view = html.Div(
        _build_brief_section(f, scored, pos),
        id='fund-brief-view',
        style=brief_display,
    )

    return html.Div([
        header,
        grid_view,
        brief_view,
    ], style={
        'marginTop': '18px',
        'padding': '14px 16px 10px',
        'background': COLOR_SURFACE,
        'borderRadius': '10px',
        'border': f'1px solid {COLOR_BORDER_LIGHT}',
    })
