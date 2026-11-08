"""Coach panel layout, controls, and rendering of preset/AI modes."""

from __future__ import annotations

import logging

from dash import dcc, html

import backend.providers.ai as ai_provider
from backend.coaching.scenarios import SCENARIOS
from backend.providers.news import news_for_portfolio as _news_for
from ui.components.chat import _COACH_BTN_PRIMARY, _COACH_INPUT, _chat_bubbles
from ui.components.scenarios import render_scenario
from ui.styles import (
    CARD,
    COLOR_SURFACE,
    COLOR_SURFACE_WHITE,
    COLOR_TEXT_DIM,
    COLOR_TEXT_FAINT,
    COLOR_TEXT_MID,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_SLATE,
    COLOR_TEXT_STRONG,
)

log = logging.getLogger(__name__)


def _mode_btn_style(active: bool) -> dict:
    # Constant fontWeight (600) across active/inactive prevents width jitter
    # between states. minWidth ensures "Preset" and "AI" occupy identical
    # space so the pill never reshuffles when the user toggles modes.
    base = {
        'padding':        '6px 16px',
        'fontSize':       '12px',
        'fontWeight':     '600',
        'minWidth':       '62px',
        'textAlign':      'center',
        'lineHeight':     '1.4',
        'border':         'none',
        'outline':        'none',
        'borderRadius':   '5px',
        'letterSpacing': '0.02em',
        'fontFamily':     'inherit',
        'cursor':         'pointer',
        'boxSizing':      'border-box',
        'transition':     'background 120ms ease, color 120ms ease',
    }
    if active:
        return {**base, 'color': COLOR_SURFACE_WHITE, 'background': COLOR_TEXT_STRONG}
    return {**base, 'color': COLOR_TEXT_DIM, 'background': 'transparent'}


def render_coach(is_open, mode, active_id, key, key_status, threads, active_thread_id,
                 chat_history, prefill, port, intel, val):
    if not is_open:
        return None

    mode = mode or 'preset'

    # ── Header: title left, mode toggle + close button right ──────────────────
    toggle = html.Div([
        html.Button("Preset", id='coach-mode-preset-btn',
                    style=_mode_btn_style(mode == 'preset')),
        html.Button("AI", id='coach-mode-ai-btn',
                    style=_mode_btn_style(mode == 'ai')),
    ], style={'display': 'flex', 'gap': '2px', 'padding': '3px',
              'background': COLOR_SURFACE, 'borderRadius': '7px'})

    header = html.Div([
        html.Div([
            html.Span("✨", style={'fontSize': '18px', 'marginRight': '8px'}),
            html.Span("Portfolio coach",
                      style={'fontSize': '16px', 'fontWeight': '600', 'color': COLOR_TEXT_STRONG}),
        ], style={'display': 'flex', 'alignItems': 'center'}),
        html.Div([
            toggle,
            html.Button("✕", id='coach-close-btn', title="Close", style={
                'width': '32px', 'height': '28px', 'background': COLOR_SURFACE_WHITE,
                'border': '0.5px solid #ddd', 'borderRadius': '6px',
                'cursor': 'pointer', 'fontSize': '14px', 'color': COLOR_TEXT_DIM,
                'padding': '0', 'lineHeight': '1', 'marginLeft': '10px',
            }),
        ], style={'display': 'flex', 'alignItems': 'center'}),
    ], style={'display': 'flex', 'justifyContent': 'space-between',
              'alignItems': 'center', 'marginBottom': '14px',
              'paddingBottom': '14px', 'borderBottom': '0.5px solid #ebebeb'})

    provider    = ai_provider.detect_provider(key or '')
    key_present = bool(provider)

    children: list = [header]

    # Hidden ids registered once per branch below so callbacks (regenerate,
    # edit, new-thread) always have a target even when the relevant widget
    # isn't visible. suppress_callback_exceptions lets Dash tolerate missing
    # ids, but pattern-matching callbacks are happier with them present.
    _hidden_always = html.Div([
        html.Button(id='coach-regenerate-btn', n_clicks=0, style={'display': 'none'}),
        html.Button(id='coach-edit-btn',       n_clicks=0, style={'display': 'none'}),
        html.Button(id='coach-new-thread-btn', n_clicks=0, style={'display': 'none'}),
        html.Div(id='coach-tabs-row', style={'display': 'none'}),
    ], style={'display': 'none'})

    if mode == 'preset':
        # ── Preset: grid of clickable question chips + answer box ─────────────
        def _preset_chip_style(selected: bool) -> dict:
            base = {
                'padding': '10px 14px', 'fontSize': '13px', 'cursor': 'pointer',
                'borderRadius': '10px', 'textAlign': 'left', 'lineHeight': '1.35',
                'transition': 'all 120ms ease', 'fontWeight': '500',
            }
            if selected:
                return {**base, 'background': COLOR_TEXT_STRONG, 'color': COLOR_SURFACE_WHITE,
                        'border': '0.5px solid #111'}
            return {**base, 'background': COLOR_SURFACE_WHITE, 'color': COLOR_TEXT_SLATE,
                    'border': '0.5px solid #e5e7eb'}

        children.append(html.Div([
            html.Button(
                s['label'],
                id={'type': 'coach-preset-btn', 'index': s['id']},
                n_clicks=0,
                className='coach-chip',
                style=_preset_chip_style(s['id'] == active_id),
            )
            for s in SCENARIOS
        ], style={'display': 'grid',
                  'gridTemplateColumns': 'repeat(auto-fill, minmax(220px, 1fr))',
                  'gap': '8px'}))

        if active_id:
            scenario_intel = ({**(intel or {}), 'news': _news_for(port)}
                              if active_id == 'news' else intel)
            children.append(html.Div(
                render_scenario(active_id, port, scenario_intel, val),
                style={'marginTop': '14px'},
            ))
        # Hidden AI-only ids (keep registered for callbacks)
        children.append(html.Div([
            dcc.Input(id='coach-key-input', type='password', style={'display': 'none'}),
            html.Button(id='coach-save-key-btn',  style={'display': 'none'}),
            html.Button(id='coach-clear-key-btn', style={'display': 'none'}),
            dcc.ConfirmDialog(id='coach-clear-key-confirm', message=''),
            dcc.Input(id='coach-input', type='text', style={'display': 'none'}),
            html.Button(id='coach-send-btn',        style={'display': 'none'}),
            html.Button(id='coach-clear-chat-btn',  style={'display': 'none'}),
            html.Div(id='coach-chat-output', style={'display': 'none'}),
        ], style={'display': 'none'}))
        children.append(_hidden_always)

    elif not key_present:
        # ── AI mode without key: key-entry form ───────────────────────────────
        key_err = (key_status or {}).get('error')
        children.append(html.Div([
            html.Div([
                dcc.Input(
                    id='coach-key-input', type='password', value='',
                    placeholder='Paste API key: sk-ant-… / xai-… / sk-…',
                    n_submit=0,
                    style={**_COACH_INPUT, 'marginBottom': '0', 'flex': '1',
                           'borderColor': '#ef4444' if key_err else None},
                ),
                html.Button("Save", id='coach-save-key-btn',
                            style={**_COACH_BTN_PRIMARY, 'marginLeft': '8px'}),
            ], style={'display': 'flex', 'alignItems': 'stretch'}),
            html.Div(key_err, style={
                'fontSize': '12px', 'color': '#ef4444',
                'margin': '6px 0 0', 'lineHeight': '1.5',
            }) if key_err else html.Div(
                "Stored in your browser only — never uploaded.",
                style={'color': COLOR_TEXT_FAINT, 'fontSize': '12px', 'margin': '6px 0 0'},
            ),
        ]))
        children.append(html.Div([
            html.Button(id='coach-clear-key-btn',   style={'display': 'none'}),
            dcc.ConfirmDialog(id='coach-clear-key-confirm', message=''),
            dcc.Input(id='coach-input', type='text', style={'display': 'none'}),
            html.Button(id='coach-send-btn',        style={'display': 'none'}),
            html.Button(id='coach-clear-chat-btn',  style={'display': 'none'}),
            html.Div(id='coach-chat-output',        style={'display': 'none'}),
        ], style={'display': 'none'}))
        children.append(_hidden_always)

    else:
        # ── AI mode with key: full chat UI ────────────────────────────────────
        # Thread tabs row — its children are rendered by a separate callback
        # (render_thread_tabs) so sending a message doesn't rebuild the whole
        # panel (which would unmount the chat output mid-request).
        # When there are no threads yet, collapse the whole row (including its
        # border) so the panel doesn't show an empty gap above the status bar.
        has_threads = bool(threads)
        tabs_row = html.Div([
            html.Div(id='coach-tabs-row', style={
                'display': 'flex', 'overflowX': 'auto', 'flex': '1',
                'alignItems': 'center', 'gap': '0',
                'scrollbarWidth': 'thin',
            }),
            html.Button("＋ New", id='coach-new-thread-btn', n_clicks=0, style={
                'background': COLOR_SURFACE_WHITE, 'color': COLOR_TEXT_STRONG,
                'border': '0.5px solid #e5e7eb', 'borderRadius': '999px',
                'padding': '4px 12px', 'fontSize': '12px',
                'cursor': 'pointer', 'marginLeft': '8px', 'fontWeight': '500',
                'whiteSpace': 'nowrap',
            }),
        ], style={
            'display': 'flex' if has_threads else 'none',
            'alignItems': 'center',
            'marginBottom': '10px', 'paddingBottom': '10px',
            'borderBottom': '0.5px solid #f0f0f0',
        })

        # Chat scroll area
        chat_area = html.Div(
            _chat_bubbles(chat_history or [], None),
            id='coach-chat-output',
            className='coach-chat-output',
            style={'maxHeight': '420px', 'overflowY': 'auto',
                   'padding': '4px 0 12px', 'marginBottom': '4px'},
        )

        _CLEAR_BTN = {
            'background': 'none', 'border': 'none', 'padding': '0',
            'fontSize': '13px', 'color': COLOR_TEXT_MID,
            'cursor': 'pointer', 'transition': 'color 120ms ease', 'fontWeight': '500',
        }
        input_row = html.Div([
            dcc.Input(
                id='coach-input', type='text', value=prefill or '', n_submit=0,
                placeholder='Message your coach…',
                autoComplete='off', debounce=False,
                style={'flex': '1', 'padding': '5px 10px', 'fontSize': '14px',
                       'lineHeight': '1.5', 'boxSizing': 'border-box',
                       'border': '1px solid #e2e8f0', 'borderRadius': '10px',
                       'background': COLOR_SURFACE_WHITE, 'color': COLOR_TEXT_STRONG,
                       'transition': 'border-color 120ms ease'},
            ),
            html.Button("Send ↑", id='coach-send-btn', n_clicks=0,
                        className='coach-send-btn',
                        style={**_COACH_BTN_PRIMARY,
                               'marginLeft': '8px', 'padding': '5px 18px',
                               'fontSize': '13px', 'fontWeight': '600',
                               'borderRadius': '10px', 'alignSelf': 'stretch'}),
        ], className='coach-input-row',
           style={'display': 'flex', 'alignItems': 'stretch'})

        # Footer: connected status + clear actions
        footer = html.Div([
            html.Span([
                html.Span("●", style={'fontSize': '9px', 'marginRight': '5px',
                                     'color': '#22c55e', 'lineHeight': '1'}),
                html.Span(f"{ai_provider.provider_label(provider)} connected",
                          style={'color': COLOR_TEXT_MUTED}),
            ], style={'display': 'inline-flex', 'alignItems': 'center'}),
            html.Div([
                html.Button("Clear chat", id='coach-clear-chat-btn', n_clicks=0,
                            className='coach-clear-link', style=_CLEAR_BTN),
                html.Span("·", style={'color': COLOR_TEXT_FAINT, 'margin': '0 7px'}),
                html.Button("Clear key", id='coach-clear-key-btn', n_clicks=0,
                            className='coach-clear-link', style=_CLEAR_BTN),
                dcc.ConfirmDialog(id='coach-clear-key-confirm',
                                  message='Are you sure you want to clear the API key?'),
            ], style={'display': 'flex', 'alignItems': 'center'}),
        ], style={'display': 'flex', 'justifyContent': 'space-between',
                  'alignItems': 'center', 'marginBottom': '12px', 'fontSize': '13px'})

        children.append(tabs_row)
        children.append(footer)
        children.append(chat_area)
        children.append(input_row)

        # Hidden key-input ids (keep registered)
        children.append(html.Div([
            dcc.Input(id='coach-key-input', type='password',
                      value=key or '', style={'display': 'none'}),
            html.Button(id='coach-save-key-btn', style={'display': 'none'}),
        ], style={'display': 'none'}))

    return html.Div(
        children,
        id='coach-panel-card',
        className='coach-panel-card',
        style={**CARD, 'marginTop': '14px'},
    )
