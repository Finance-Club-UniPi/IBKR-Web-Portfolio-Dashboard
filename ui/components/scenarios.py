"""Render ordinary scenario answers as Dash cards; no financial calculations."""

import math
from datetime import datetime, timezone

from dash import html

from backend.coaching.scenarios import SCENARIOS_BY_ID, evaluate_scenario

_TONE = {
    'good':  ('#16a34a', '#f0fdf4', '#bbf7d0'),   # fg, bg, border
    'warn':  ('#b45309', '#fffbeb', '#fde68a'),
    'info':  ('#374151', '#f9fafb', '#e5e7eb'),
}


def _answer(headline: str, body: list[str], tone: str = 'info'):
    fg, bg, bd = _TONE.get(tone, _TONE['info'])
    return html.Div([
        html.P(headline, style={
            'margin': '0 0 10px', 'fontWeight': '600', 'fontSize': '16px', 'color': fg,
        }),
        *[html.P(p, style={
            'margin': '0 0 8px', 'fontSize': '15px', 'color': '#374151', 'lineHeight': '1.55',
        }) for p in body],
    ], style={
        'background': bg, 'border': f'0.5px solid {bd}', 'borderRadius': '10px',
        'padding': '16px 18px',
    })


def _weight_text(value):
    return f'{value:.1f}%' if value is not None and math.isfinite(value) else 'weight unavailable'


def _ago(iso: str, now: datetime | None = None) -> str:
    """'15m ago', '3h ago', '2d ago' for an ISO timestamp ('' if unparseable)."""
    try:
        then = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return ''
    seconds = ((now or datetime.now(timezone.utc)) - then).total_seconds()
    if seconds < 3600:
        return f"{max(int(seconds // 60), 1)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"


def _render_news(answer):
    ranked, news = answer['positions'], answer['news']
    sections, empty = [], []
    for p in ranked:
        items = news.get(p['ticker']) or []
        if not items:
            empty.append(p['ticker'])
            continue
        sections.append(html.Div([
            html.P([html.Span(p['ticker'], style={'fontWeight': '700'}),
                    html.Span(f"  {_weight_text(p.get('allocation_pct'))} of portfolio",
                              style={'color': '#6b7280', 'fontWeight': '400', 'fontSize': '13px'})],
                   style={'margin': '0 0 6px', 'fontSize': '15px', 'color': '#111827'}),
            *[html.Div([
                html.A(i['title'], href=i['link'], target='_blank', rel='noopener noreferrer',
                       style={'color': '#1d4ed8', 'textDecoration': 'none', 'fontSize': '14px',
                              'lineHeight': '1.4'}),
                html.Div(f"{i['source']} · {_ago(i['published'])}",
                         style={'fontSize': '12px', 'color': '#6b7280', 'marginTop': '1px'}),
            ], style={'marginBottom': '8px'}) for i in items[:3]],
        ], style={'marginBottom': '10px'}))

    if not sections:
        return _answer("No recent headlines found",
                       ["Yahoo Finance has nothing from the last two weeks for your holdings. "
                        "Broad ETFs in particular often have no stories of their own."])
    fg, bg, bd = _TONE['info']
    footer = []
    if empty:
        footer.append(html.P(f"No recent headlines for: {', '.join(empty)}.",
                             style={'margin': '4px 0 0', 'fontSize': '13px', 'color': '#6b7280'}))
    footer.append(html.P("Headlines come from Yahoo Finance and can lag. A story appearing here "
                         "doesn't mean it moves the price, and none of this is investment advice.",
                         style={'margin': '8px 0 0', 'fontSize': '12px', 'color': '#6b7280'}))
    return html.Div([
        html.P(f"Latest headlines for your {len(sections)} holding"
               f"{'s' if len(sections) != 1 else ''} with news",
               style={'margin': '0 0 12px', 'fontWeight': '600', 'fontSize': '16px', 'color': fg}),
        *sections, *footer,
    ], style={'background': bg, 'border': f'0.5px solid {bd}', 'borderRadius': '10px',
              'padding': '16px 18px'})


def render_scenario(scenario_id, port, intel, val):
    if scenario_id not in SCENARIOS_BY_ID:
        return html.P('Unknown question.', style={'color': '#b45309'})
    try:
        answer = evaluate_scenario(scenario_id, port, intel, val)
        if answer.get('kind') == 'news':
            return _render_news(answer)
        return _answer(answer['headline'], answer['body'], answer['tone'])
    except Exception as error:
        return html.Div([
            html.P("Couldn't produce an answer for that question.",
                   style={'color': '#b45309', 'fontWeight': '600', 'margin': '0 0 6px'}),
            html.P(f'({type(error).__name__}: {error})',
                   style={'color': '#888', 'fontSize': '13px', 'margin': '0'}),
        ], style={'padding': '16px', 'background': '#fffbeb',
                  'border': '0.5px solid #fde68a', 'borderRadius': '10px'})
