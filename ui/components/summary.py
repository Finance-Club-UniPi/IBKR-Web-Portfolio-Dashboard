"""Top-of-page rendering: summary cards, holdings table, allocation donut,
and the dividends panel.

All four callbacks read from `portfolio-data` and render plain children. No
network, no IBKR — pure functions of the store payload.
"""

from __future__ import annotations

import pandas as pd
import plotly.express as px
from dash import dash_table, dcc, html

from backend.portfolio.positions import daily_pnl as select_daily_pnl
from backend.schemas import PortfolioData
from ui.errors import NotReadyError
from ui.helpers import EURUSD_FALLBACK, ccy_symbol, make_table, section_label
from ui.styles import (
    CARD,
    COLOR_BAD,
    COLOR_BORDER,
    COLOR_BORDER_LIGHT,
    COLOR_BORDER_STRONG,
    COLOR_BRAND,
    COLOR_GOOD,
    COLOR_GOOD_SOFT,
    COLOR_SURFACE_SOFT,
    COLOR_SURFACE_WHITE,
    COLOR_TEXT_DIM,
    COLOR_TEXT_FAINT,
    COLOR_TEXT_GHOST,
    COLOR_TEXT_MID,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_STRONG,
    COLOR_WARN,
    COLOR_WARN_BG,
    COLOR_WARN_SOFT,
    COLOR_WARN_YELLOW,
)


def build_summary_cards(data: PortfolioData | None, status):
    if not data or 'summary' not in data:
        if status == 'disconnected':
            return []
        def _skel_card():
            return html.Div([
                html.Div(className='skeleton-block', style={'height': '11px', 'width': '55%', 'marginBottom': '14px'}),
                html.Div(className='skeleton-block', style={'height': '28px', 'width': '78%', 'marginBottom': '10px'}),
                html.Div(className='skeleton-block', style={'height': '11px', 'width': '40%'}),
            ], style={'background': COLOR_SURFACE_SOFT, 'borderRadius': '12px',
                      'padding': '18px', 'borderLeft': f'3px solid {COLOR_BORDER_STRONG}'})
        return [_skel_card() for _ in range(4)]

    s = data['summary']
    a = data.get('account', {})
    rate      = a.get('eurusd_rate', EURUSD_FALLBACK)
    base_ccy  = a.get('base_currency', 'EUR')
    sym       = ccy_symbol(base_ccy)
    portfolio_values_base = a.get('portfolio_value_units') == 'base'

    # Secondary currency is USD when base≠USD, otherwise EUR.
    sec_ccy   = 'USD' if base_ccy != 'USD' else 'EUR'
    sec_sym   = ccy_symbol(sec_ccy)

    total_val  = s['total_value']
    unreal_pnl = s['total_unrealized_pnl']
    daily_pnl = select_daily_pnl(a, s)
    cash_base  = a.get('cash_base', 0)
    pnl_pct    = s.get('total_pnl_pct')

    def _to_sec(base_val):
        """Convert a base-currency value to the secondary display currency."""
        if base_ccy == 'USD':
            return base_val / rate if rate else base_val   # USD → EUR
        if base_ccy == 'EUR':
            return base_val * rate                         # EUR → USD
        return None

    def card(label, base_val, pnl_pct=None, is_pnl=False, note=None, verified_base=True,
             value_text=None, title=None):
        """`value_text` replaces the formatted amount (e.g. a beta or '—')."""
        positive = base_val is not None and base_val >= 0
        accent   = (COLOR_GOOD if positive else COLOR_BAD) if is_pnl and base_val is not None \
                   else COLOR_TEXT_STRONG
        prefix = sym if verified_base else ''
        if value_text is not None:
            val_str = value_text
        else:
            val_str = f"{prefix}{base_val:+,.2f}" if is_pnl else f"{prefix}{base_val:,.2f}"
        secondary = _to_sec(base_val) if verified_base and value_text is None else None
        sec_str = f"{'-' if secondary < 0 else ''}{sec_sym}{abs(secondary):,.2f}" if secondary is not None else ''
        return html.Div([
            html.P(label, style={
                'fontSize': '14px', 'color': COLOR_TEXT_FAINT, 'margin': '0 0 10px',
                'textTransform': 'uppercase', 'letterSpacing': '0.05em', 'fontWeight': '500',
            }),
            html.P(val_str, style={
                'fontSize': '26px', 'fontWeight': '600', 'margin': '0',
                'color': accent if is_pnl else COLOR_TEXT_STRONG, 'letterSpacing': '-0.5px',
            }),
            html.Div([
                html.Span(sec_str, style={'fontSize': '14px', 'color': COLOR_TEXT_FAINT}) if sec_str else None,
                html.Span(f" · {pnl_pct:+.2f}%",
                          style={'fontSize': '14px', 'color': accent}) if pnl_pct is not None else None,
                html.Span(f" · {note}",
                          style={'fontSize': '14px', 'color': COLOR_TEXT_MUTED}) if note is not None else None,
            ], style={'marginTop': '4px'}),
        ], title=title, style={
            'background': COLOR_SURFACE_SOFT, 'borderRadius': '12px', 'padding': '18px',
            'borderLeft': f'3px solid {accent if is_pnl else COLOR_BORDER}',
        })

    # Demo values are base; live PortfolioItem units are unverified.
    total_base = total_val
    # Net liquidation (positions + cash + accruals) is IBKR's own account total
    # in base currency.  The positions sum above is in instrument-currency
    # units (e.g. USD) and excludes cash, so it never matches the broker app.
    net_liq   = a.get('net_liquidation')
    cash_pct   = round(cash_base / net_liq * 100, 1) if net_liq and net_liq > 0 else (
        round(cash_base / total_base * 100, 1) if total_base and portfolio_values_base else None)

    # Today's P&L as a share of yesterday's net liquidation value (IBKR's own
    # base for the daily %, and always in account base currency).
    prev_nlv  = net_liq - daily_pnl if net_liq and daily_pnl is not None else None
    daily_pct = round(daily_pnl / prev_nlv * 100, 2) if prev_nlv and prev_nlv > 0 else None

    gross_base   = a.get('gross_position_value') or 0
    realized_pnl = s.get('total_realized_pnl') or 0
    fx_total     = s.get('total_fx_today')
    fx_n         = s.get('fx_covered', 0)
    fx_all       = s.get('total_fx_total')
    fx_all_n     = s.get('fx_total_covered', 0)
    n_positions  = len(data.get('positions', []))
    beta         = s.get('portfolio_beta')
    beta_cov     = s.get('beta_coverage_pct', 0)

    return [
        card("Net Liquidation", net_liq,
             note=f"{sym}{gross_base:,.0f} positions + cash",
             title="IBKR's account total in base currency: positions at market value + cash + "
                   "accrued interest/dividends. Matches the figure in TWS / the IBKR app.")
        if net_liq and net_liq > 0 else
        card("Total Value" if portfolio_values_base else "Positions value (TWS units)",
             total_base, verified_base=portfolio_values_base),
        card("Unrealized P&L" if portfolio_values_base else "Unrealized P&L (TWS units)",
             unreal_pnl, pnl_pct=pnl_pct, is_pnl=True, verified_base=portfolio_values_base),
        card("Today's P&L",    daily_pnl, pnl_pct=daily_pct, is_pnl=True,
             value_text='—' if daily_pnl is None else None,
             title="Today's change as a percentage of yesterday's net liquidation value "
                   "(current net liquidation minus today's P&L)."),
        card("Cash",           cash_base, note=f"{cash_pct:.1f}% of portfolio" if cash_pct is not None else None),
        card("Realized P&L" if portfolio_values_base else "Realized P&L (TWS units)",
             realized_pnl, is_pnl=True, verified_base=portfolio_values_base,
             note="open positions",
             title="Profit already locked in on the positions you currently hold, as reported by "
                   "IBKR. Positions that were fully closed no longer appear in the account "
                   "portfolio and are not included."),
        card("FX total", fx_all, is_pnl=fx_all is not None,
             value_text='—' if fx_all is None else None,
             note=f"{fx_all_n} of {n_positions} positions",
             title="Sum of the per-holding 'FX total' column: estimated effect of currency moves "
                   "since you bought, in account base currency, rebuilt from complete trade history "
                   "using daily purchase-date FX closes. The price/FX cross-term "
                   "is assigned to FX."),
        card("FX today", fx_total, is_pnl=fx_total is not None,
             value_text='—' if fx_total is None else None,
             note=f"{fx_n} of {n_positions} positions",
             title="Sum of the per-holding 'FX today' column: estimated effect of today's currency "
                   "move on current exposure, in account base currency. Not since-purchase."),
        card("Portfolio beta", beta, value_text='—' if beta is None else f"{beta:.2f}",
             note=f"{beta_cov:.0f}% of value covered" if beta is not None else None,
             title="Value-weighted beta vs the S&P 500 (Yahoo Finance) over holdings with a beta, "
                   "excluding cash. Above 1 amplifies market moves; below 1 dampens them."),
    ]


def build_holdings_table(data, status):
    if not data or 'positions' not in data:
        if status == 'disconnected':
            return None, '', None
        skel_row = html.Div([
            html.Div(className='skeleton-block', style={'height': '13px', 'width': w, 'borderRadius': '4px'})
            for w in ('12%', '18%', '15%', '14%', '14%', '13%')
        ], style={'display': 'flex', 'gap': '12px', 'alignItems': 'center',
                  'padding': '10px 0', 'borderBottom': f'0.5px solid {COLOR_BORDER_STRONG}'})
        return html.Div([skel_row for _ in range(5)],
                        style={'padding': '4px 0'}), '', None
    df       = pd.DataFrame(data['positions'])
    acct     = data.get('account', {})
    base_ccy = acct.get('base_currency', 'EUR')
    values_base = acct.get('portfolio_value_units') == 'base'

    count = f"{len(df)} positions"
    any_stale = df.get('price_stale', pd.Series(False)).any()
    stale_badge = html.Span("● Market closed · last-close prices",
                            className='badge-warn',
                            style={
                                'fontSize': '13px', 'color': COLOR_WARN,
                                'background': COLOR_WARN_BG, 'border': '0.5px solid #fde68a',
                                'padding': '3px 9px', 'borderRadius': '20px',
                                'marginTop': '-10px',
                            }) if any_stale else None

    # Pre-format display columns
    def _price_display(row):
        quote = row.get('fx_native_price')
        if row.get('price_unit_verified') and pd.notna(quote):
            return f"{ccy_symbol(row.get('currency') or '')}{quote:,.2f}"
        return f"~{row['current_price']:,.2f}"

    df['price_display'] = df.apply(_price_display, axis=1)
    df['weight_display']    = df['allocation_pct'].apply(lambda v: f"{v:.1f}%" if pd.notna(v) else '—')
    df['pnl_pct_display']   = df['pnl_pct'].apply(lambda v: f"{v:+.2f}%")
    for col in ('beta', 'fx_total'):
        if col not in df.columns:
            df[col] = None

    table_frame = df[[
        'ticker', 'quantity', 'avg_cost', 'price_display',
        'market_value', 'pnl_pct', 'pnl_pct_display',
        'unrealized_pnl', 'fx_total', 'fx_today', 'beta', 'weight_display',
    ]].astype(object)
    table_data = table_frame.where(pd.notna(table_frame), None).to_dict('records')

    tooltip_data = [{
        'fx_total': {
            'value': row.get('fx_total_reason') or (
                'Rebuilt from your trade history: Σ lot qty × price × (FX now − FX at purchase), '
                'purchase rates are Yahoo daily closes (approximate)'
                if row.get('fx_total_source') == 'trade history' else
                'Base-currency value − (price ÷ avg cost) × base-currency cost'),
            'type': 'text',
        },
        'fx_today': {
            'value': row.get('fx_today_reason') or
                     'Current position exposure × (current FX rate − previous FX daily close)',
            'type': 'text',
        }
    } for _, row in df.iterrows()]

    table = dash_table.DataTable(
        id='holdings-datatable',
        columns=[
            {'name': 'Ticker',   'id': 'ticker',           'type': 'text'},
            {'name': 'Qty',      'id': 'quantity',          'type': 'numeric'},
            {'name': 'Avg Cost (native)' if values_base else 'Avg Cost (TWS units)',
             'id': 'avg_cost', 'type': 'numeric',
             'format': {'specifier': ',.2f'}},
            {'name': 'Price',    'id': 'price_display',     'type': 'text'},
            {'name': f'Value ({base_ccy})' if values_base else 'Value (TWS units)',
             'id': 'market_value', 'type': 'numeric',
             'format': {'specifier': ',.0f'}},
            {'name': 'P&L %',   'id': 'pnl_pct_display',    'type': 'text'},
            {'name': f'P&L ({base_ccy})' if values_base else 'P&L (TWS units)',
             'id': 'unrealized_pnl', 'type': 'numeric',
             'format': {'specifier': '+,.2f'}},
            {'name': f'FX total ({base_ccy})', 'id': 'fx_total', 'type': 'numeric',
             'format': {'specifier': '+,.2f', 'nully': '—'}},
            {'name': f'FX today ({base_ccy})', 'id': 'fx_today', 'type': 'numeric',
             'format': {'specifier': '+,.2f', 'nully': '—'}},
            {'name': 'Beta',    'id': 'beta', 'type': 'numeric',
             'format': {'specifier': '.2f', 'nully': '—'}},
            {'name': 'Weight',  'id': 'weight_display',     'type': 'text'},
        ],
        data=table_data,
        tooltip_header={'fx_total': (
            'Estimated effect of currency moves since purchase on this position, in account base '
            'currency. Rebuilt from complete trade history using FIFO lots and Yahoo daily FX closes '
            'on each purchase date. The price/FX cross-term is assigned to FX. This is approximate; '
            '— means the lot history or required prices/rates are unavailable.'
        ), 'fx_today': (
            'Estimated effect of today’s FX move on the current position, in account base currency. '
            'Current native price × quantity × multiplier × (FX now − previous FX daily close). '
            'The price/FX cross-term is assigned to FX. — means a current price, verified price units, '
            'or a fresh current/previous FX rate is unavailable. This is not since-purchase attribution.'
        ), 'beta': (
            'Sensitivity to the S&P 500 (Yahoo Finance, 5-year monthly; 3-year for some ETFs). '
            '1.0 moves with the market, above 1 amplifies it, below 1 dampens it. '
            '— means no beta is available for this ticker.'
        ), 'price_display': (
            'Fresh contract quote in the instrument currency when available; '
            '~ indicates the cached TWS portfolio price, whose units or freshness are unverified.'
        )},
        tooltip_data=tooltip_data,
        tooltip_duration=None,
        sort_action='native',
        sort_by=[{'column_id': 'market_value', 'direction': 'desc'}],
        page_size=50,
        style_as_list_view=True,
        style_table={'overflowX': 'auto'},
        style_header={
            'fontSize': '14px', 'color': COLOR_TEXT_FAINT, 'fontWeight': '500',
            'textTransform': 'uppercase', 'letterSpacing': '0.04em',
            'backgroundColor': COLOR_SURFACE_WHITE, 'border': 'none',
            'borderBottom': f'0.5px solid {COLOR_BORDER_LIGHT}',
            'paddingBottom': '14px',
        },
        style_cell={
            'fontFamily': '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
            'fontSize': '16px', 'padding': '12px 12px',
            'backgroundColor': COLOR_SURFACE_WHITE, 'color': COLOR_TEXT_STRONG,
            'border': 'none', 'borderBottom': f'0.5px solid {COLOR_BORDER_LIGHT}',
        },
        style_cell_conditional=(
            [{'if': {'column_id': 'ticker'}, 'fontWeight': '600', 'textAlign': 'left'}] +
            [{'if': {'column_id': c}, 'textAlign': 'right'}
             for c in ['quantity', 'avg_cost', 'price_display', 'market_value',
                       'pnl_pct_display', 'unrealized_pnl', 'fx_total', 'fx_today', 'beta',
                       'weight_display']]
        ),
        style_data_conditional=[
            {'if': {'filter_query': '{pnl_pct} >= 0', 'column_id': 'pnl_pct_display'},
             'color': COLOR_GOOD},
            {'if': {'filter_query': '{pnl_pct} < 0', 'column_id': 'pnl_pct_display'},
             'color': COLOR_BAD},
            {'if': {'filter_query': '{unrealized_pnl} >= 0', 'column_id': 'unrealized_pnl'},
             'color': COLOR_GOOD},
            {'if': {'filter_query': '{unrealized_pnl} < 0', 'column_id': 'unrealized_pnl'},
             'color': COLOR_BAD},
            {'if': {'filter_query': '{fx_total} >= 0', 'column_id': 'fx_total'},
             'color': COLOR_GOOD},
            {'if': {'filter_query': '{fx_total} < 0', 'column_id': 'fx_total'},
             'color': COLOR_BAD},
            {'if': {'filter_query': '{fx_today} >= 0', 'column_id': 'fx_today'},
             'color': COLOR_GOOD},
            {'if': {'filter_query': '{fx_today} < 0', 'column_id': 'fx_today'},
             'color': COLOR_BAD},
{'if': {'filter_query': '{price_display} contains "~"', 'column_id': 'price_display'},
             'color': COLOR_WARN},
            {'if': {'state': 'active'}, 'backgroundColor': '#f0f7ff', 'border': 'none'},
        ],
    )

    return table, count, stale_badge


def build_allocation_chart(data):
    if not data or 'positions' not in data:
        return html.Div([
            html.Div(className='skeleton-block', style={
                'width': '160px', 'height': '160px', 'borderRadius': '50%',
                'margin': '24px auto 20px',
            }),
            html.Div(className='skeleton-block', style={'height': '11px', 'width': '70%', 'margin': '0 auto 8px'}),
            html.Div(className='skeleton-block', style={'height': '11px', 'width': '55%', 'margin': '0 auto 8px'}),
            html.Div(className='skeleton-block', style={'height': '11px', 'width': '60%', 'margin': '0 auto'}),
        ])
    df = pd.DataFrame(data['positions'])
    if df['allocation_pct'].isna().any() or (df['allocation_pct'] < 0).any():
        return html.P('Allocation unavailable: verified base-currency values for long holdings are required.')
    colors = [COLOR_BRAND, COLOR_WARN_SOFT, '#a855f7', COLOR_GOOD_SOFT, COLOR_WARN_YELLOW, '#ec4899', '#14b8a6']
    fig = px.pie(df, values='allocation_pct', names='ticker', hole=0.68,
                 color_discrete_sequence=colors)
    fig.update_traces(
        textposition='none',
        textinfo='none',
        hovertemplate='<b>%{label}</b><br>%{percent}<extra></extra>',
    )
    fig.update_layout(
        margin=dict(t=0, b=0, l=0, r=0),
        showlegend=True,
        legend=dict(orientation='v', x=0.5, y=0.5, xanchor='center', yanchor='middle',
                    font=dict(size=12, color=COLOR_TEXT_MID), itemclick=False, itemdoubleclick=False),
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
    )
    return dcc.Graph(figure=fig, config={'displayModeBar': False}, style={'height': '260px'})


def build_dividends(data: PortfolioData | None, _interval, status):
    if not data or 'positions' not in data:
        base = (status or '').split(':')[0]
        if base in ('disconnected', 'no_positions'):
            return []
        raise NotReadyError('Loading dividend data…')

    positions = data['positions']
    div_data  = data.get('div_data', {})
    acct      = data.get('account', {})
    rate      = acct.get('eurusd_rate', EURUSD_FALLBACK)
    base_ccy  = acct.get('base_currency', 'EUR')
    sym       = ccy_symbol(base_ccy)

    # Build per-position dividend enrichment
    div_positions = []
    for p in positions:
        sym  = p['ticker']
        d    = div_data.get(sym, {})
        n12  = d.get('next_12m')
        p12  = d.get('past_12m')
        price = p['current_price']
        qty   = p['quantity']
        if not (n12 or p12):
            continue
        annual_dps    = n12 or p12
        yield_pct     = round(annual_dps / price * 100, 2) if price else None
        annual_income = round(annual_dps * qty, 2)
        div_positions.append({
            'ticker':       sym,
            'yield_pct':    yield_pct,
            'annual_dps':   annual_dps,
            'annual_income':annual_income,
            'next_date':    d.get('next_date'),
            'next_amount':  d.get('next_amount'),
            'quantity':     qty,
        })

    annual_income = sum(p['annual_income'] for p in div_positions)

    if not div_positions:
        return html.Div(
            html.P("No dividend data — positions may not pay dividends or market data is unavailable.",
                   style={'fontSize': '15px', 'color': COLOR_TEXT_GHOST, 'textAlign': 'center', 'padding': '24px 0'}),
            style=CARD)

    # ── Summary cards ────────────────────────────────────────────────────
    def div_card(label, value, sub=None, color=COLOR_TEXT_STRONG):
        return html.Div([
            html.P(label, style={'fontSize': '12px', 'color': COLOR_TEXT_FAINT, 'margin': '0 0 6px',
                                 'textTransform': 'uppercase', 'letterSpacing': '0.05em',
                                 'fontWeight': '500'}),
            html.P(value, style={'fontSize': '20px', 'fontWeight': '600', 'margin': '0',
                                 'color': color, 'letterSpacing': '-0.5px'}),
            html.P(sub, style={'fontSize': '13px', 'color': COLOR_TEXT_MUTED, 'margin': '3px 0 0'}) if sub else None,
        ], style={'background': COLOR_SURFACE_SOFT, 'borderRadius': '12px', 'padding': '10px 14px',
                  'borderLeft': f'3px solid {COLOR_BORDER}'})

    portfolio_yield = round(annual_income / data['summary']['total_value'] * 100, 2) \
        if annual_income and data.get('summary', {}).get('total_value') else None

    summary_row = html.Div([
        div_card("Projected Annual Income", f"${annual_income:,.2f}",
                 sub=f"€{annual_income / rate:,.2f}"),
        div_card("Portfolio Yield",
                 f"{portfolio_yield:.2f}%" if portfolio_yield else "—",
                 sub="Based on next 12M dividends"),
    ], style={'display': 'grid', 'gridTemplateColumns': 'repeat(2, 1fr)',
              'gap': '12px', 'marginBottom': '20px'})

    # ── Per-position yield table ─────────────────────────────────────────
    if div_positions:
        td_r = lambda v, **kw: html.Td(v, style={'textAlign': 'right', 'padding': '10px 12px', **kw})
        td_l = lambda v, **kw: html.Td(v, style={'textAlign': 'left',  'padding': '10px 12px', **kw})

        rows = []
        for p in sorted(div_positions, key=lambda x: x['annual_income'], reverse=True):
            nxt_date = p['next_date'] or '—'
            nxt_amt  = f"${p['next_amount']:,.4f}" if p['next_amount'] else '—'
            nxt_pay  = f"${p['next_amount'] * p['quantity']:,.2f}" \
                       if p['next_amount'] else '—'
            yield_color = COLOR_GOOD if (p['yield_pct'] or 0) >= 2 else COLOR_TEXT_STRONG
            rows.append(html.Tr([
                td_l(html.Span(p['ticker'], style={'fontWeight': '600', 'color': COLOR_TEXT_STRONG})),
                td_r(f"{p['yield_pct']:.2f}%" if p['yield_pct'] else '—', color=yield_color),
                td_r(f"${p['annual_dps']:,.4f}"),
                td_r(f"${p['annual_income']:,.2f}"),
                td_r(nxt_date, color=COLOR_TEXT_DIM),
                td_r(nxt_amt,  color=COLOR_TEXT_DIM),
                td_r(nxt_pay),
            ], style={'borderTop': f'0.5px solid {COLOR_BORDER_LIGHT}'}))

        yield_table = html.Div([
            section_label("Yield per Position"),
            make_table(
                ['Ticker', 'Yield', 'Ann. DPS', 'Ann. Income', 'Next Ex-Date', 'Next Div/Share', 'Next Payout'],
                rows),
        ], style={'marginBottom': '20px'})
    else:
        yield_table = None

    return html.Div([
        section_label("Dividends"),
        summary_row,
        yield_table,
    ], style=CARD)
