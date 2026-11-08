"""Chat bubbles, follow-up chips, and starter prompts. No callback registration."""

from __future__ import annotations

import logging

from dash import dcc, html

from ui.styles import (
    COLOR_SURFACE,
    COLOR_SURFACE_WHITE,
    COLOR_TEXT_DIM,
    COLOR_TEXT_MID,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_SLATE,
    COLOR_TEXT_STRONG,
    COLOR_WARN,
    COLOR_WARN_BG,
)

log = logging.getLogger(__name__)


_COACH_BTN = {
    'fontSize': '13px', 'color': COLOR_TEXT_MID, 'background': COLOR_SURFACE,
    'border': '0.5px solid #ddd', 'borderRadius': '8px',
    'padding': '6px 14px', 'cursor': 'pointer',
    'transition': 'background 120ms ease, color 120ms ease',
}

_COACH_BTN_PRIMARY = {
    **_COACH_BTN,
    'background': COLOR_TEXT_STRONG, 'color': COLOR_SURFACE_WHITE, 'border': '0.5px solid #111',
}

_COACH_INPUT = {
    'width': '100%', 'padding': '8px 10px', 'fontSize': '14px',
    'border': '0.5px solid #ddd', 'borderRadius': '8px', 'marginBottom': '8px',
}


_USER_BUBBLE = {
    'maxWidth': '80%', 'padding': '10px 14px', 'borderRadius': '16px 16px 4px 16px',
    'background': COLOR_TEXT_STRONG, 'color': COLOR_SURFACE_WHITE, 'fontSize': '14px', 'lineHeight': '1.5',
    'whiteSpace': 'pre-wrap', 'wordBreak': 'break-word',
}
_ASSIST_BUBBLE = {
    'maxWidth': '88%', 'padding': '12px 16px', 'borderRadius': '16px 16px 16px 4px',
    'background': '#f4f4f5', 'color': COLOR_TEXT_STRONG, 'fontSize': '14px', 'lineHeight': '1.55',
    'wordBreak': 'break-word',
}
_ASSIST_ERR = {**_ASSIST_BUBBLE, 'background': COLOR_WARN_BG,
               'border': '0.5px solid #fde68a', 'color': COLOR_WARN}

_CHIP_STYLE = {
    'fontSize': '12px', 'padding': '6px 12px', 'cursor': 'pointer',
    'background': COLOR_SURFACE_WHITE, 'border': '0.5px solid #e5e7eb', 'borderRadius': '999px',
    'color': COLOR_TEXT_SLATE, 'transition': 'all 120ms ease', 'textAlign': 'left',
    'lineHeight': '1.3',
}

_ICON_BTN = {
    'background': 'transparent', 'border': 'none', 'cursor': 'pointer',
    'color': COLOR_TEXT_MUTED, 'fontSize': '12px', 'padding': '2px 6px',
    'borderRadius': '6px', 'marginLeft': '6px',
}

_STARTER_PROMPTS = [
    "Portfolio health check — strengths, risks, and one thing to fix.",
    "Am I over-concentrated? Where is my real exposure?",
    "Where would you put €500 more given what I already hold?",
    "What would a fund manager cut from this portfolio first, and why?",
]


def _user_row(text: str, is_last: bool = False):
    edit_btn = None
    if is_last:
        edit_btn = html.Button(
            "✎ Edit", id='coach-edit-btn', n_clicks=0, title="Edit this question",
            className='coach-icon-btn',
            style={**_ICON_BTN, 'marginTop': '4px'})
    return html.Div([
        html.Div(text, style=_USER_BUBBLE),
        edit_btn,
    ], style={'display': 'flex', 'flexDirection': 'column',
              'alignItems': 'flex-end', 'marginBottom': '10px'})


def _assistant_row(text: str, idx: int, err: bool = False, is_last: bool = False,
                   followups: list[str] | None = None):
    body = (
        html.Div(text, className='coach-error-bubble', style={**_ASSIST_ERR, 'whiteSpace': 'pre-wrap'})
        if err else
        html.Div(dcc.Markdown(text, style={'margin': '0'}), className='coach-assistant-bubble', style=_ASSIST_BUBBLE)
    )
    actions = None
    if not err:
        action_btns = [
            html.Button("Copy", id={'type': 'coach-copy', 'index': idx},
                        n_clicks=0, title="Copy answer",
                        className='coach-icon-btn', style=_ICON_BTN),
        ]
        if is_last:
            action_btns.append(html.Button(
                "↻ Regenerate", id='coach-regenerate-btn', n_clicks=0,
                title="Retry this question", className='coach-icon-btn',
                style=_ICON_BTN))
        actions = html.Div(action_btns, style={
            'display': 'flex', 'marginTop': '4px',
            'marginLeft': '6px', 'opacity': '0.7'})

    chips = None
    if is_last and followups:
        chips = html.Div([
            html.Div([
                html.Button(f, id={'type': 'coach-followup', 'index': i},
                            n_clicks=0, className='coach-chip', style=_CHIP_STYLE)
                for i, f in enumerate(followups)
            ], style={'display': 'flex', 'flexWrap': 'wrap', 'gap': '6px',
                      'marginLeft': '2px', 'marginTop': '10px'}),
        ])

    return html.Div([
        html.Div([body, actions] if actions else [body],
                 style={'display': 'flex', 'flexDirection': 'column',
                        'alignItems': 'flex-start'}),
        chips,
    ], style={'display': 'flex', 'flexDirection': 'column',
              'alignItems': 'flex-start', 'marginBottom': '14px'})


def _thinking_bubble():
    return html.Div(
        html.Div([
            html.Span(".", style={'animation': 'coachPulse 1.2s infinite', 'animationDelay': '0s'}),
            html.Span(".", style={'animation': 'coachPulse 1.2s infinite', 'animationDelay': '0.2s', 'marginLeft': '2px'}),
            html.Span(".", style={'animation': 'coachPulse 1.2s infinite', 'animationDelay': '0.4s', 'marginLeft': '2px'}),
            html.Span("Thinking…", style={'marginLeft': '8px', 'color': COLOR_TEXT_MUTED,
                                           'fontSize': '13px'}),
        ], style={**_ASSIST_BUBBLE, 'display': 'flex', 'alignItems': 'center',
                  'color': COLOR_TEXT_DIM}),
        style={'display': 'flex', 'justifyContent': 'flex-start', 'marginBottom': '10px'},
    )


def _starter_panel():
    cards = [
        html.Button(
            prompt,
            id={'type': 'coach-starter', 'index': i}, n_clicks=0,
            className='coach-starter-card',
            style={
                'flex': '1', 'minWidth': '0', 'textAlign': 'left',
                'padding': '12px 14px', 'fontSize': '13px', 'color': COLOR_TEXT_STRONG,
                'lineHeight': '1.4', 'whiteSpace': 'normal',
                'background': COLOR_SURFACE_WHITE,
                'border': '0.5px solid #e5e7eb', 'borderRadius': '10px',
                'cursor': 'pointer', 'transition': 'all 140ms ease',
            },
        )
        for i, prompt in enumerate(_STARTER_PROMPTS)
    ]
    return html.Div(cards, style={
        'display': 'flex', 'flexWrap': 'nowrap', 'gap': '8px', 'padding': '8px 0 4px',
    })


def _chat_bubbles(history: list[dict], pending: str | None = None):
    """Render the chat log — ChatGPT-style split bubbles."""
    if not history and not pending:
        return _starter_panel()

    rows: list = []
    last_idx = len(history) - 1
    for i, turn in enumerate(history):
        q = turn.get('q', '')
        a = turn.get('a', '')
        err = bool(turn.get('error'))
        fups = turn.get('followups') or []
        # "Edit" shows on the last user turn only when no request is in flight
        # and the last turn has a completed answer.
        rows.append(_user_row(q, is_last=(i == last_idx and not pending)))
        rows.append(_assistant_row(a, idx=i, err=err,
                                   is_last=(i == last_idx and not pending),
                                   followups=fups))
    if pending:
        rows.append(_user_row(pending))
        rows.append(_thinking_bubble())

    return html.Div(rows)
