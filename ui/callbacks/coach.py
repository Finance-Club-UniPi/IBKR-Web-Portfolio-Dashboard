"""Coach callbacks: user actions, thread state, and AI requests.

Cards and chat bubbles live in ui/components/; scenario calculations and
conversation operations live in backend/coaching/."""

from __future__ import annotations

import logging
import time

import requests
from dash import ALL, Input, Output, State, ctx, html, no_update

import backend.providers.ai as ai_provider
from backend.coaching.conversations import active_history, commit_history, find_thread
from backend.providers.news import news_for_portfolio as _news_for
from ui.components.chat import _chat_bubbles
from ui.components.coach_panel import render_coach as build_coach_panel
from ui.styles import (
    COLOR_BORDER_MID,
    COLOR_SURFACE_WHITE,
    COLOR_TEXT_SLATE,
    COLOR_TEXT_STRONG,
)

log = logging.getLogger(__name__)


def register(app):
    # ── Thread helpers ────────────────────────────────────────────────────────────
    # A "thread" is one named conversation:
    #   {id: str, title: str, created: iso, history: [{q, a, error?, followups?}]}
    # Threads are persisted in browser localStorage via the coach-threads store.

    @app.callback(
        Output('coach-open', 'data'),
        Input('coach-toggle-btn', 'n_clicks'),
        State('coach-open', 'data'),
        prevent_initial_call=True,
    )
    def toggle_coach(n, is_open):
        if not n:
            return no_update
        return not bool(is_open)


    @app.callback(
        Output('coach-open', 'data', allow_duplicate=True),
        Input('coach-close-btn', 'n_clicks'),
        prevent_initial_call=True,
    )
    def close_coach(n):
        if not n:
            return no_update
        return False


    @app.callback(
        Output('coach-active-id', 'data'),
        Input({'type': 'coach-preset-btn', 'index': ALL}, 'n_clicks'),
        prevent_initial_call=True,
    )
    def pick_scenario(clicks):
        trig = ctx.triggered_id
        if not isinstance(trig, dict):
            return no_update
        # Guard against pattern-matching phantom triggers on mount.
        if not any(clicks or []):
            return no_update
        return trig.get('index') or no_update


    @app.callback(
        Output('coach-mode', 'data'),
        Input('coach-mode-preset-btn', 'n_clicks'),
        Input('coach-mode-ai-btn', 'n_clicks'),
        prevent_initial_call=True,
    )
    def switch_mode(p, a):
        trig = ctx.triggered_id
        if trig == 'coach-mode-ai-btn' and a:
            return 'ai'
        if trig == 'coach-mode-preset-btn' and p:
            return 'preset'
        return no_update


    @app.callback(
        Output('coach-clear-key-confirm', 'displayed'),
        Input('coach-clear-key-btn', 'n_clicks'),
        prevent_initial_call=True,
    )
    def show_clear_key_confirm(n):
        return bool(n)

    @app.callback(
        Output('coach-api-key', 'data'),
        Output('coach-key-status', 'data'),
        Input('coach-save-key-btn', 'n_clicks'),
        Input('coach-clear-key-confirm', 'submit_n_clicks'),
        State('coach-key-input', 'value'),
        prevent_initial_call=True,
    )
    def save_or_clear_key(save, clear_confirmed, value):
        trig = ctx.triggered_id
        if trig == 'coach-clear-key-confirm' and clear_confirmed:
            return '', None
        if trig == 'coach-save-key-btn' and save:
            key = (value or '').strip()
            ok, err = ai_provider.validate_key(key)
            if ok:
                return key, None
            return '', {'error': err}
        return no_update, no_update


    # ── Chat: submit → pending-q, then run_llm consumes pending-q ────────────────

    @app.callback(
        Output('coach-pending-q', 'data'),
        Output('coach-input', 'value'),
        Output('coach-input', 'disabled'),
        Output('coach-send-btn', 'disabled'),
        Output('coach-send-btn', 'children'),
        Input('coach-send-btn', 'n_clicks'),
        Input('coach-input', 'n_submit'),
        Input({'type': 'coach-starter',  'index': ALL}, 'n_clicks'),
        Input({'type': 'coach-followup', 'index': ALL}, 'n_clicks'),
        State('coach-input', 'value'),
        State({'type': 'coach-starter',  'index': ALL}, 'children'),
        State({'type': 'coach-followup', 'index': ALL}, 'children'),
        State('coach-pending-q', 'data'),
        prevent_initial_call=True,
    )
    def submit_question(send_n, submit_n, starter_clicks, fup_clicks,
                        text, starter_labels, fup_labels, pending):
        noop = (no_update,) * 5

        # If a request is already in flight, ignore new submissions.
        if pending:
            return noop

        trig = ctx.triggered_id
        q = None
        if trig == 'coach-send-btn' or trig == 'coach-input':
            q = (text or '').strip()
        elif isinstance(trig, dict):
            t = trig.get('type')
            i = trig.get('index')
            # Guard against the phantom trigger that pattern-matching Inputs
            # sometimes fire on mount (n_clicks is None/0 in that case).
            clicks = starter_clicks if t == 'coach-starter' else fup_clicks
            labels = starter_labels if t == 'coach-starter' else fup_labels
            if i is None or i >= len(clicks) or not clicks[i]:
                return noop
            q = (labels[i] if i < len(labels) else '').strip()

        if not q:
            return noop

        # Starter cards pre-fill the input so the user can review and hit Send.
        # Follow-up chips send immediately.
        if isinstance(trig, dict):
            t = trig.get('type')
            if t == 'coach-starter':
                return no_update, q, False, False, "Send ↑"
            if t == 'coach-followup':
                return q, '', True, True, "Sending…"

        # Set pending-q → triggers run_llm via its Input. Disable input/button
        # while the request is in flight; run_llm re-enables them on completion.
        return q, '', True, True, "Sending…"


    @app.callback(
        Output('coach-threads', 'data', allow_duplicate=True),
        Output('coach-active-thread-id', 'data', allow_duplicate=True),
        Output('coach-pending-q', 'data', allow_duplicate=True),
        Output('coach-input', 'disabled', allow_duplicate=True),
        Output('coach-send-btn', 'disabled', allow_duplicate=True),
        Output('coach-send-btn', 'children', allow_duplicate=True),
        Input('coach-pending-q', 'data'),
        State('coach-api-key', 'data'),
        State('coach-threads', 'data'),
        State('coach-active-thread-id', 'data'),
        State('portfolio-data', 'data'),
        State('market-intel-data', 'data'),
        State('valuation-data', 'data'),
        State('uploaded-trades', 'data'),
        prevent_initial_call=True,
    )
    def run_llm(question, key, threads, active_id, port, intel, val, trades):
        # Triggered when submit_question writes a question into pending-q.
        # Also fires when we clear pending-q at the end — we bail on that.
        log.info("coach.run_llm fired: question=%r key_present=%s",
                 (question or '')[:60], bool(key))
        if not question:
            return no_update, no_update, no_update, no_update, no_update, no_update

        try:
            history = active_history(threads, active_id)
            for turn in history:
                turn.pop('followups', None)

            def _commit_and_return(h):
                new_threads, new_active = commit_history(threads, active_id, h)
                return new_threads, new_active, None, False, False, "Send ↑"

            if not key:
                history.append({'q': question,
                                'a': "No API key saved. Switch to the AI tab and paste a key "
                                     "(Anthropic / OpenAI / xAI) — it stays in your browser only.",
                                'error': True})
                return _commit_and_return(history)

            try:
                provider = ai_provider.detect_provider(key)
                log.info("coach: calling provider=%s", provider)
                context_json = ai_provider.build_portfolio_context(port, intel, val, trades)
                t0 = time.time()
                news_ctx = ai_provider.build_news_context(_news_for(port), port)
                answer, followups = ai_provider.ask(key, context_json, question,
                                                    history=history, news_context=news_ctx)
                log.info("coach: provider reply in %.1fs, %d chars",
                         time.time() - t0, len(answer or ''))
                if not (answer or '').strip():
                    history.append({'q': question,
                                    'a': "The provider returned an empty response. "
                                         "Try again or switch to a different model.",
                                    'error': True})
                else:
                    history.append({'q': question, 'a': answer, 'followups': followups})
            except requests.HTTPError as e:
                status = e.response.status_code if e.response is not None else None
                body = ''
                try:
                    body = e.response.text[:300]
                except Exception:
                    pass
                log.warning("coach: HTTPError %s: %s", status, body)
                history.append({
                    'q': question,
                    'a': ai_provider.explain_http_error(status, body),
                    'error': True,
                })
            except Exception as e:
                log.exception("coach: provider call failed")
                history.append({'q': question,
                                'a': f"Couldn't reach the provider: {type(e).__name__}: {e}",
                                'error': True})
            return _commit_and_return(history)
        except Exception as e:
            log.exception("coach.run_llm crashed")
            # Surface the failure as an error bubble instead of silently
            # dropping the question, so the user always sees what went wrong
            # even when the API key validated fine.
            try:
                history = active_history(threads, active_id)
                history.append({
                    'q': question,
                    'a': f"Something went wrong handling this question: "
                         f"{type(e).__name__}: {e}",
                    'error': True,
                })
                new_threads, new_active = commit_history(threads, active_id, history)
                return new_threads, new_active, None, False, False, "Send ↑"
            except Exception:
                # Last-resort: at least unstick the UI from "Thinking…"
                return no_update, no_update, None, False, False, "Send ↑"


    # ── Derived: chat-history = active thread's history ──────────────────────────

    @app.callback(
        Output('coach-chat-history', 'data'),
        Input('coach-threads', 'data'),
        Input('coach-active-thread-id', 'data'),
    )
    def _derive_chat_history(threads, active_id):
        return active_history(threads, active_id)


    # ── Render the thread-tabs row. Kept separate from render_coach so sending a
    #    message doesn't rebuild the whole panel.

    @app.callback(
        Output('coach-tabs-row', 'children'),
        Input('coach-threads', 'data'),
        Input('coach-active-thread-id', 'data'),
    )
    def render_thread_tabs(threads, active_thread_id):
        threads = threads or []
        tabs: list = []
        for t in threads:
            is_active = (t.get('id') == active_thread_id)
            tab_label = t.get('title') or 'New chat'
            tabs.append(html.Div([
                html.Button(
                    tab_label,
                    id={'type': 'coach-thread-tab', 'index': t['id']},
                    n_clicks=0,
                    title=tab_label,
                    style={
                        'background': COLOR_TEXT_STRONG if is_active else COLOR_SURFACE_WHITE,
                        'color':      COLOR_SURFACE_WHITE if is_active else COLOR_TEXT_SLATE,
                        'border':     '0.5px solid ' + (COLOR_TEXT_STRONG if is_active else COLOR_BORDER_MID),
                        'borderRadius': '999px 0 0 999px',
                        'padding': '4px 10px', 'fontSize': '12px',
                        'cursor': 'pointer', 'maxWidth': '180px',
                        'overflow': 'hidden', 'textOverflow': 'ellipsis',
                        'whiteSpace': 'nowrap', 'fontWeight': '500',
                    }),
                html.Button(
                    '×',
                    id={'type': 'coach-thread-del', 'index': t['id']},
                    n_clicks=0, title='Delete chat',
                    style={
                        'background': COLOR_TEXT_STRONG if is_active else COLOR_SURFACE_WHITE,
                        'color':      COLOR_SURFACE_WHITE if is_active else '#9ca3af',
                        'border':     '0.5px solid ' + (COLOR_TEXT_STRONG if is_active else COLOR_BORDER_MID),
                        'borderLeft': 'none',
                        'borderRadius': '0 999px 999px 0',
                        'padding': '4px 8px', 'fontSize': '12px',
                        'cursor': 'pointer', 'fontWeight': '600',
                    }),
            ], style={'display': 'flex', 'alignItems': 'center', 'marginRight': '6px'}))
        return tabs


    # ── Thread management: clear / new / switch / delete ─────────────────────────

    @app.callback(
        Output('coach-threads', 'data', allow_duplicate=True),
        Output('coach-active-thread-id', 'data', allow_duplicate=True),
        Input('coach-clear-chat-btn', 'n_clicks'),
        State('coach-threads', 'data'),
        State('coach-active-thread-id', 'data'),
        prevent_initial_call=True,
    )
    def clear_chat(n, threads, active_id):
        """Clear the current thread's history (keep the thread; fresh canvas)."""
        if not n:
            return no_update, no_update
        threads = list(threads or [])
        t = find_thread(threads, active_id)
        if t is None:
            return threads, active_id
        t['history'] = []
        t['title']   = 'New chat'
        return threads, active_id


    @app.callback(
        Output('coach-threads', 'data', allow_duplicate=True),
        Output('coach-active-thread-id', 'data', allow_duplicate=True),
        Input('coach-new-thread-btn', 'n_clicks'),
        State('coach-threads', 'data'),
        State('coach-active-thread-id', 'data'),
        prevent_initial_call=True,
    )
    def new_thread(n, threads, active_id):
        if not n:
            return no_update, no_update
        threads = list(threads or [])
        # If the active thread already has no messages, stay there — don't create a ghost.
        active = find_thread(threads, active_id)
        if active is not None and not (active.get('history') or []):
            return no_update, no_update
        # If the first thread is empty (and there's no active thread), reuse it.
        if threads and not (threads[0].get('history') or []):
            return threads, threads[0]['id']
        t = new_thread([])
        threads.insert(0, t)
        return threads, t['id']


    @app.callback(
        Output('coach-active-thread-id', 'data', allow_duplicate=True),
        Input({'type': 'coach-thread-tab', 'index': ALL}, 'n_clicks'),
        prevent_initial_call=True,
    )
    def switch_thread(clicks):
        trig = ctx.triggered_id
        if not isinstance(trig, dict):
            return no_update
        if not any(clicks or []):
            return no_update
        return trig.get('index') or no_update


    @app.callback(
        Output('coach-threads', 'data', allow_duplicate=True),
        Output('coach-active-thread-id', 'data', allow_duplicate=True),
        Input({'type': 'coach-thread-del', 'index': ALL}, 'n_clicks'),
        State('coach-threads', 'data'),
        State('coach-active-thread-id', 'data'),
        prevent_initial_call=True,
    )
    def delete_thread(clicks, threads, active_id):
        trig = ctx.triggered_id
        if not isinstance(trig, dict) or not any(clicks or []):
            return no_update, no_update
        tid = trig.get('index')
        threads = [t for t in (threads or []) if t.get('id') != tid]
        if active_id == tid:
            active_id = threads[0]['id'] if threads else None
        return threads, active_id


    # ── Regenerate last answer: pop last turn and resubmit its question ──────────

    @app.callback(
        Output('coach-threads', 'data', allow_duplicate=True),
        Output('coach-active-thread-id', 'data', allow_duplicate=True),
        Output('coach-pending-q', 'data', allow_duplicate=True),
        Output('coach-input', 'disabled', allow_duplicate=True),
        Output('coach-send-btn', 'disabled', allow_duplicate=True),
        Output('coach-send-btn', 'children', allow_duplicate=True),
        Input('coach-regenerate-btn', 'n_clicks'),
        State('coach-threads', 'data'),
        State('coach-active-thread-id', 'data'),
        State('coach-pending-q', 'data'),
        prevent_initial_call=True,
    )
    def regenerate_last(n, threads, active_id, pending):
        if not n or pending:
            return (no_update,) * 6
        history = active_history(threads, active_id)
        if not history:
            return (no_update,) * 6
        last_q = (history[-1].get('q') or '').strip()
        if not last_q:
            return (no_update,) * 6
        history = history[:-1]
        new_threads, new_active = commit_history(threads, active_id, history)
        return new_threads, new_active, last_q, True, True, "Sending…"


    # ── Edit last question: pop last turn and pre-fill the input ─────────────────

    @app.callback(
        Output('coach-threads', 'data', allow_duplicate=True),
        Output('coach-active-thread-id', 'data', allow_duplicate=True),
        Output('coach-input', 'value', allow_duplicate=True),
        Input('coach-edit-btn', 'n_clicks'),
        State('coach-threads', 'data'),
        State('coach-active-thread-id', 'data'),
        prevent_initial_call=True,
    )
    def edit_last(n, threads, active_id):
        if not n:
            return no_update, no_update, no_update
        history = active_history(threads, active_id)
        if not history:
            return no_update, no_update, no_update
        last_q = (history[-1].get('q') or '')
        history = history[:-1]
        new_threads, new_active = commit_history(threads, active_id, history)
        return new_threads, new_active, last_q


    # ── Chat output: separate callback so typing/scrolling doesn't rebuild panel ─

    @app.callback(
        Output('coach-chat-output', 'children'),
        Input('coach-chat-history', 'data'),
        Input('coach-pending-q', 'data'),
    )
    def render_chat(history, pending):
        return _chat_bubbles(history or [], pending)


    # ── Clientside: copy an answer to clipboard ─────────────────────────────────

    app.clientside_callback(
        """
        function(clicks, history) {
            const ctx = window.dash_clientside.callback_context;
            if (!ctx.triggered || !ctx.triggered.length) return window.dash_clientside.no_update;
            const trig = ctx.triggered[0];
            if (!trig.value) return window.dash_clientside.no_update;
            try {
                const id = JSON.parse(trig.prop_id.split('.')[0]);
                const turn = (history || [])[id.index] || {};
                const txt = turn.a || '';
                if (navigator.clipboard && txt) { navigator.clipboard.writeText(txt); }
                const btn = document.getElementById(trig.prop_id.split('.')[0]);
                if (btn) {
                    btn.textContent = 'Copied!';
                    btn.style.color = '#16a34a';
                    setTimeout(function() {
                        btn.textContent = 'Copy';
                        btn.style.color = '';
                    }, 1500);
                }
            } catch (e) {}
            return (Date.now());
        }
        """,
        Output('coach-copy-signal', 'data'),
        Input({'type': 'coach-copy', 'index': ALL}, 'n_clicks'),
        State('coach-chat-history', 'data'),
        prevent_initial_call=True,
    )


    # ── Clientside: auto-scroll the chat area to the bottom on new content ──────

    app.clientside_callback(
        """
        function(_children) {
            setTimeout(function() {
                var el = document.getElementById('coach-chat-output');
                if (el) { el.scrollTop = el.scrollHeight; }
            }, 30);
            return Date.now();
        }
        """,
        Output('coach-scroll-signal', 'data'),
        Input('coach-chat-output', 'children'),
        prevent_initial_call=True,
    )


    # ── Clientside: smooth-scroll to a panel when it opens ──────────────────────
    # Target-Y math: let the BROWSER do it. We call scrollIntoView({block:'start'})
    # synchronously, read the resulting pageYOffset (which the browser has placed
    # exactly where it should land, respecting scroll-margin-top from custom.css),
    # then immediately revert to the original scroll position. Both writes happen
    # in the same JS task so the browser only paints the final state — the jump
    # is invisible. This eliminates any manual math (offsetTop chains, transform
    # interference, flex/grid edge cases) and produces always-correct target Y.
    #
    # Speed: browser-native scrollIntoView({behavior:'smooth'}) is fixed and fast.
    # We replace it with a requestAnimationFrame tween using easeInOutCubic over
    # ~800 ms for a more deliberate feel. Reduced-motion users get an instant
    # jump via the matchMedia check.

    _SMOOTH_SCROLL_JS_TMPL = """
    function(%(trigger)s) {
        %(guard)s
        setTimeout(function() {
            var el = document.getElementById('%(element_id)s');
            if (!el) { return; }

            // Capture the browser's own idea of the correct scroll target by
            // jumping there synchronously, reading pageYOffset, then reverting.
            // Both scroll writes happen in one JS task — nothing is painted
            // between them, so the jump-and-revert is invisible to the user.
            var startY = window.pageYOffset;
            el.scrollIntoView({ block: 'start' });
            var targetY = window.pageYOffset;
            window.scrollTo(0, startY);

            var dy = targetY - startY;
            if (Math.abs(dy) < 2) { return; }

            // Respect OS "reduce motion" preference — skip the animation.
            var reduce = window.matchMedia
                && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
            if (reduce) { window.scrollTo(0, targetY); return; }

            var duration = 800;
            var start = performance.now();
            function step(now) {
                var t = (now - start) / duration;
                if (t > 1) { t = 1; }
                // easeInOutCubic
                var ease = t < 0.5
                    ? 4 * t * t * t
                    : 1 - Math.pow(-2 * t + 2, 3) / 2;
                window.scrollTo(0, startY + dy * ease);
                if (t < 1) { requestAnimationFrame(step); }
            }
            requestAnimationFrame(step);
        }, %(delay)d);
        return Date.now();
    }
    """

    # Position-detail: trigger only when the user selects a ticker (opens the
    # panel).  Using selected-ticker instead of position-detail.children means
    # period changes, trade uploads, and 60-s data refreshes no longer yank
    # the page back down.  Guard on null so closing the panel is a no-op.
    app.clientside_callback(
        _SMOOTH_SCROLL_JS_TMPL % {
            'trigger':    'ticker',
            'guard':      'if (!ticker) { return window.dash_clientside.no_update; }',
            'element_id': 'position-detail',
            'delay':      280,   # wait for slideInDown (250 ms) + a safety margin
        },
        Output('position-detail-scroll-signal', 'data'),
        Input('selected-ticker', 'data'),
        prevent_initial_call=True,
    )

    # Coach panel: trigger on coach-open flipping to True. NOT on coach-panel.children
    # (that would yank the page up on every chat keystroke during a re-render).
    app.clientside_callback(
        _SMOOTH_SCROLL_JS_TMPL % {
            'trigger':    'isOpen',
            'guard':      'if (!isOpen) { return window.dash_clientside.no_update; }',
            'element_id': 'coach-panel',
            'delay':      80,    # coach render is server-side only; no slide animation
        },
        Output('coach-panel-scroll-signal', 'data'),
        Input('coach-open', 'data'),
        prevent_initial_call=True,
    )


    # ── Prefill: cleared on the next tick after it's consumed by render_coach ───
    # coach-input reads prefill as its initial value. We clear the store right
    # after so the next panel render doesn't re-insert the same text.

    @app.callback(
        Output('coach-prefill', 'data', allow_duplicate=True),
        Input('coach-panel', 'children'),
        State('coach-prefill', 'data'),
        prevent_initial_call=True,
    )
    def clear_prefill_after_render(_children, prefill):
        return '' if prefill else no_update


    # ── Ask coach about a specific ticker ────────────────────────────────────────
    # Button lives inside the position-detail panel. Clicking it opens the coach,
    # switches to AI mode, and pre-fills the input with a specific, actionable
    # question about the ticker so the user can send immediately or lightly edit.


    @app.callback(
        Output('coach-open',    'data', allow_duplicate=True),
        Output('coach-mode',    'data', allow_duplicate=True),
        Output('coach-prefill', 'data', allow_duplicate=True),
        Input({'type': 'position-ask-coach', 'index': ALL}, 'n_clicks'),
        State('selected-ticker', 'data'),
        prevent_initial_call=True,
    )
    def ask_coach_about_position(clicks, ticker):
        if not any(clicks or []) or not ticker:
            return no_update, no_update, no_update
        return True, 'ai', ''


    @app.callback(
        Output('coach-panel', 'children'),
        Input('coach-open', 'data'),
        Input('coach-mode', 'data'),
        Input('coach-active-id', 'data'),
        Input('coach-api-key', 'data'),
        Input('coach-key-status', 'data'),
        State('coach-threads', 'data'),
        State('coach-active-thread-id', 'data'),
        State('coach-chat-history', 'data'),
        State('coach-prefill', 'data'),
        State('portfolio-data', 'data'),
        State('market-intel-data', 'data'),
        State('valuation-data', 'data'),
    )
    def render_coach(is_open, mode, active_id, key, key_status, threads, active_thread_id,
                     chat_history, prefill, port, intel, val):
        return build_coach_panel(is_open, mode, active_id, key, key_status, threads,
                                 active_thread_id, chat_history, prefill, port, intel, val)
