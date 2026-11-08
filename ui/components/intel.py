"""Render sector/geography exposure and earnings from prepared store data."""

from __future__ import annotations

import logging
from datetime import date

import plotly.graph_objects as go
from dash import dcc, html

from backend.portfolio.exposure import sector_geo_breakdown
from ui.errors import NotReadyError
from ui.helpers import ccy_symbol, section_label
from ui.styles import (
    CARD,
    COLOR_BAD,
    COLOR_BORDER_LIGHT,
    COLOR_BRAND,
    COLOR_GOOD_SOFT,
    COLOR_TEXT,
    COLOR_TEXT_GHOST,
    COLOR_TEXT_MID,
    COLOR_TEXT_MUTED,
    COLOR_WARN,
    COLOR_WARN_SOFT,
    COLOR_WARN_YELLOW,
    TABLE_HEADER_CELL,
    TABLE_WRAPPER,
)

log = logging.getLogger(__name__)


def build_sector_geo(intel, port_data, status=''):
    if not intel:
        base = (status or '').split(':')[0]
        if base in ('disconnected', 'no_positions'):
            return []
        raise NotReadyError('Loading sector & geography data…')
    if not port_data or 'positions' not in port_data:
        return None

    sg = intel.get('sector_geo', {})
    positions = port_data['positions']

    sectors, countries = sector_geo_breakdown(positions, sg)
    if not sectors:
        return html.Div('Exposure unavailable: verified base-currency values for long holdings are required.', style=CARD)
    sector_val = {name: pct for name, pct, _ in sectors}
    sector_tickers = {name: tickers for name, _, tickers in sectors}
    country_val = dict(countries)
    total = 100.0

    # ── Sector donut ────────────────────────────────────────────────────────
    MIN_SECTOR_PCT = 5.0

    sec_full_sorted = sorted(sector_val.items(), key=lambda x: x[1], reverse=True)
    sec_labels = [s[0] for s in sec_full_sorted]
    sec_values = [s[1] for s in sec_full_sorted]

    colors = [COLOR_BRAND, COLOR_WARN_SOFT, '#a855f7', COLOR_GOOD_SOFT,
              COLOR_WARN_YELLOW, '#ec4899', '#14b8a6', '#6366f1',
              '#84cc16', '#ef4444', '#06b6d4']
    sec_colors = [colors[i % len(colors)] for i in range(len(sec_labels))]
    color_by_sec = dict(zip(sec_labels, sec_colors, strict=True))

    acct          = port_data.get('account') or {}
    base_ccy      = acct.get('base_currency', 'EUR')
    sym           = ccy_symbol(base_ccy)
    total_base = sum(p['base_market_value'] for p in positions)
    if total_base >= 1_000_000:
        center_val = f"{sym}{total_base/1_000_000:.2f}M"
    elif total_base >= 1_000:
        center_val = f"{sym}{total_base/1_000:.1f}K"
    else:
        center_val = f"{sym}{total_base:,.0f}"

    sec_values_base = [v / 100 * total_base for v in sec_values]
    donut = go.Figure(go.Pie(
        labels=sec_labels, values=sec_values, hole=0.62,
        textposition='none', sort=False,
        marker=dict(colors=sec_colors),
        customdata=sec_values_base,
        hovertemplate=f'<b>%{{label}}</b><br>%{{percent:.1%}}  ·  {sym}%{{customdata:,.0f}}<extra></extra>',
    ))
    donut.update_layout(
        margin=dict(t=0, b=0, l=0, r=0),
        showlegend=False,
        paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
        height=200,
        annotations=[dict(
            text=(f"<span style='font-size:18px;"
                  f"color:var(--clr-text-strong);font-weight:600'>"
                  f"{center_val}</span><br>"
                  f"<span style='font-size:11px;color:var(--clr-text-muted);"
                  f"letter-spacing:0.05em;text-transform:uppercase'>Portfolio</span>"),
            x=0.5, y=0.5, showarrow=False, align='center',
        )],
    )

    # ── Country chart ────────────────────────────────────────────────────────
    cty_sorted = sorted(country_val.items(), key=lambda x: x[1], reverse=True)[:8]
    cty_labels = [c[0] for c in cty_sorted]
    cty_values = [c[1] / total * 100 for c in cty_sorted]

    country_colors = [COLOR_BRAND, COLOR_GOOD_SOFT, COLOR_WARN_SOFT, '#a855f7',
                      COLOR_WARN_YELLOW, '#ec4899', '#14b8a6', '#6366f1']

    center_text = (f"{cty_values[0]:.0f}%<br>"
                   f"<span style='font-size:11px;color:#888'>{cty_labels[0]}</span>"
                   if len(cty_labels) == 1 else '')
    bar = go.Figure(go.Pie(
        labels=cty_labels, values=cty_values, hole=0.62,
        textposition='none',
        marker=dict(colors=country_colors[:len(cty_labels)]),
        hovertemplate='<b>%{label}</b><br>%{percent:.1%}<extra></extra>',
    ))
    bar.update_layout(
        margin=dict(t=0, b=0, l=0, r=0),
        showlegend=(len(cty_labels) > 1),
        legend=dict(orientation='h', yanchor='bottom', y=-0.15,
                    xanchor='center', x=0.5, font=dict(size=11)),
        paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
        height=200,
        annotations=[dict(text=center_text, x=0.5, y=0.5,
                          font=dict(size=20, color=COLOR_TEXT),
                          showarrow=False)] if center_text else [],
    )

    # ── Sector breakdown legend table ────────────────────────────────────────
    legend_sectors: list = []
    other_val = 0.0
    other_names: list = []
    for sec, val in sec_full_sorted:
        if (val / total * 100) >= MIN_SECTOR_PCT:
            legend_sectors.append((sec, val, color_by_sec[sec]))
        else:
            other_val += val
            other_names.append(sec)
    if other_val / total * 100 >= 1.0:
        other_tickers = sorted({
            t for s in other_names for t in sector_tickers.get(s, [])
        })
        sector_tickers['Other'] = other_tickers
        legend_sectors.append(('Other', other_val, '#b8b8b8'))

    legend_rows = []
    for sec, val, dot_color in legend_sectors:
        pct = val / total * 100
        tickers_str = ', '.join(sorted(sector_tickers.get(sec, [])))
        legend_rows.append(html.Tr([
            html.Td(html.Div(style={
                'width': '8px', 'height': '8px', 'borderRadius': '50%',
                'background': dot_color, 'display': 'inline-block',
            }), style={'padding': '6px 8px 6px 0', 'width': '17px'}),
            html.Td(sec, style={'padding': '6px 12px 6px 0',
                                'fontSize': '15px', 'fontWeight': '500'}),
            html.Td(f'{pct:.1f}%', style={'padding': '6px 12px', 'textAlign': 'right',
                                           'fontSize': '15px', 'color': COLOR_TEXT_MID}),
            html.Td(tickers_str, style={'padding': '6px 0', 'fontSize': '14px',
                                         'color': COLOR_TEXT_MUTED}),
        ], style={'borderTop': f'0.5px solid {COLOR_BORDER_LIGHT}'}))

    sector_legend = html.Table(
        [html.Tbody(legend_rows)],
        style={'borderCollapse': 'collapse', 'marginTop': '17px', 'width': '100%'},
    )

    _label_style = {
        'fontSize': '14px', 'color': COLOR_TEXT_MID,
        'textTransform': 'uppercase', 'letterSpacing': '0.05em',
        'fontWeight': '600', 'margin': '0 0 8px',
        'textAlign': 'center',
    }

    return html.Div([
        section_label("Sector & Geography Exposure"),

        # ── Two equal chart columns ───────────────────────────────────────────
        html.Div([
            html.Div([
                html.P("Sector", style=_label_style),
                dcc.Graph(figure=donut, config={'displayModeBar': False}),
            ], style={'flex': '1', 'minWidth': '240px'}),

            html.Div([
                html.P("Geography", style=_label_style),
                dcc.Graph(figure=bar, config={'displayModeBar': False}),
            ], style={'flex': '1', 'minWidth': '240px'}),
        ], style={'display': 'flex', 'gap': '24px', 'flexWrap': 'wrap',
                  'alignItems': 'flex-start', 'marginBottom': '8px'}),

        # ── Full-width breakdown table ────────────────────────────────────────
        sector_legend,

    ], style=CARD)


def build_earnings(intel, port_data, status=''):
    if not intel:
        base = (status or '').split(':')[0]
        if base in ('disconnected', 'no_positions'):
            return []
        raise NotReadyError('Loading earnings data…')

    earnings = intel.get('earnings', {})
    positions = (port_data or {}).get('positions', [])
    alloc_map = {p['ticker']: p['allocation_pct'] for p in positions}

    today = date.today()

    rows_data = []
    for sym, e in earnings.items():
        if not e.get('next_date'):
            continue
        try:
            earn_date = date.fromisoformat(e['next_date'])
        except Exception:
            continue
        days = (earn_date - today).days
        rows_data.append({
            'ticker':    sym,
            'next_date': e['next_date'],
            'days':      days,
            'avg_move':  e.get('avg_1d_move'),
            'moves':     e.get('last_1d_moves', []),
            'weight':    alloc_map.get(sym, 0),
        })

    if not rows_data:
        return html.Div(
            html.P("No upcoming earnings dates found — holdings may be ETFs or "
                   "data is unavailable.",
                   style={'fontSize': '15px', 'color': COLOR_TEXT_GHOST,
                          'textAlign': 'center', 'padding': '24px 0'}),
            style=CARD)

    rows_data.sort(key=lambda x: x['days'])

    td_l = lambda v, **kw: html.Td(v, style={'padding': '10px 12px',
                                               'textAlign': 'left', **kw})
    td_r = lambda v, **kw: html.Td(v, style={'padding': '10px 12px',
                                               'textAlign': 'center', **kw})

    table_rows = []
    for r in rows_data:
        days      = r['days']
        imminent  = days <= 14
        soon      = days <= 30

        if days < 0:
            days_str  = f"{abs(days)}d ago"
            days_color = COLOR_TEXT_GHOST
        elif days == 0:
            days_str  = 'Today'
            days_color = COLOR_BAD
        else:
            days_str  = f'in {days}d'
            days_color = COLOR_BAD if imminent else (COLOR_WARN if soon else COLOR_TEXT_MUTED)

        weight_str  = f"{r['weight']:.1f}%"
        row_class   = 'earnings-row-imminent' if imminent else ''

        table_rows.append(html.Tr([
            td_l(html.Span(r['ticker'], style={'fontWeight': '600'})),
            td_r(r['next_date'], color=COLOR_TEXT_MID),
            td_r(days_str,       color=days_color,
                 fontWeight='600' if imminent else '400'),
            td_r(weight_str,     color=COLOR_TEXT_MID),
        ], className=row_class,
           style={'borderTop': f'0.5px solid {COLOR_BORDER_LIGHT}'}))

    cols = ['Ticker', 'Earnings Date', 'When', 'Weight']
    header = html.Tr([
        html.Th(c, style={**TABLE_HEADER_CELL,
                          'textAlign': 'left' if i == 0 else 'center',
                          'width': '25%',
                          })
        for i, c in enumerate(cols)
    ])
    table = html.Table(
        [html.Thead(header), html.Tbody(table_rows)],
        style={**TABLE_WRAPPER, 'tableLayout': 'fixed'},
    )

    return html.Div([
        section_label("Earnings Calendar"),
        table,
    ], style=CARD)
