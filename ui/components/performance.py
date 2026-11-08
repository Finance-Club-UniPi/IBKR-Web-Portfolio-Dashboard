"""Portfolio performance chart: value or % return, with instrument comparison.

Controls (period, absolute/% toggle, compare box) are static layout elements so
their state survives the 60-second portfolio refresh.  `perf-holdings` is a
small store that changes with the holdings; a separate interval refreshes the
chart against the shared timed price cache.
"""

from __future__ import annotations

from datetime import date

import plotly.graph_objects as go
from dash import dcc, html

from backend.portfolio.history import (
    CUSTOM,
    DEFAULT_PERIOD,
    MAX_COMPARE,
    MAX_LOOKBACK_YEARS,
    PERIODS,
    custom_bounds,
)
from ui.helpers import section_label
from ui.styles import (
    CARD,
    COLOR_TEXT_MID,
    COLOR_TEXT_MUTED,
)

_NOTE_STYLE = {'fontSize': '13px', 'color': COLOR_TEXT_MUTED, 'margin': '8px 0 0', 'lineHeight': '1.5'}


def _default_start() -> date:
    """One year before today — the same window as the default 1Y button."""
    today = custom_bounds()[1]
    try:
        return today.replace(year=today.year - 1)
    except ValueError:                      # 29 Feb
        return today.replace(year=today.year - 1, day=28)


def performance_card():
    """Static layout for the performance section (used by layout.py)."""
    return html.Div([
        html.Div([
            section_label("Portfolio Performance"),
            html.Div([
                dcc.RadioItems(
                    id='perf-mode', className='seg', value='pct',
                    options=[{'label': 'Absolute', 'value': 'abs'},
                             {'label': '% return', 'value': 'pct'}]),
                dcc.RadioItems(
                    id='perf-period', className='seg', value=DEFAULT_PERIOD,
                    options=[{'label': p, 'value': p} for p in PERIODS] +
                            [{'label': CUSTOM, 'value': CUSTOM}]),
            ], style={'display': 'flex', 'gap': '12px', 'flexWrap': 'wrap'}),
        ], style={'display': 'flex', 'justifyContent': 'space-between',
                  'alignItems': 'center', 'flexWrap': 'wrap', 'gap': '8px'}),

        html.Div([
            html.Span("Compare with", style={'fontSize': '13px', 'color': COLOR_TEXT_MID,
                                             'fontWeight': '500'}),
            dcc.Dropdown(id='perf-compare', multi=True, value=[], options=[],
                         placeholder='Search any stock, ETF, index, crypto, commodity…',
                         maxHeight=320, optionHeight=40,
                         clearable=False,   # Esc / the clear-all ✕ would wipe every pick at once
                         style={'width': '520px', 'maxWidth': '100%', 'fontSize': '13px'}),
            html.Span(f"up to {MAX_COMPARE} · everything on Yahoo Finance",
                      style={'fontSize': '12px', 'color': COLOR_TEXT_MUTED}),
        ], style={'display': 'flex', 'alignItems': 'center', 'gap': '10px',
                  'flexWrap': 'wrap', 'margin': '10px 0 4px'}),

        html.Div([
            html.Span("From – Until", style={'fontSize': '13px', 'color': COLOR_TEXT_MID,
                                             'fontWeight': '500'}),
            dcc.DatePickerRange(
                id='perf-range', display_format='DD MMM YYYY', start_date_placeholder_text='From',
                end_date_placeholder_text='Until', minimum_nights=1, clearable=False,
                min_date_allowed=custom_bounds()[0], max_date_allowed=custom_bounds()[1],
                start_date=_default_start(), end_date=custom_bounds()[1],
            ),
            html.Span(f"up to {MAX_LOOKBACK_YEARS} years back · until today at the latest",
                      style={'fontSize': '12px', 'color': COLOR_TEXT_MUTED}),
        ], id='perf-range-wrap',
            style={'display': 'none', 'alignItems': 'center', 'gap': '10px',
                   'flexWrap': 'wrap', 'margin': '6px 0 4px'}),

        html.Div(id='perf-stats', style={'display': 'flex', 'gap': '18px', 'flexWrap': 'wrap',
                                         'minHeight': '22px', 'margin': '6px 0 0'}),
        dcc.Loading(
            dcc.Graph(id='perf-graph', figure=go.Figure(), config={'displayModeBar': False},
                      style={'height': '340px'}),
            type='circle', delay_show=400,
        ),
        html.P(id='perf-note', style=_NOTE_STYLE),
        dcc.Store(id='perf-holdings'),
    ], id='performance-section', style={**CARD, 'marginBottom': '14px'})
