"""Conversation operations on browser-owned thread data."""

import uuid
from datetime import datetime, timezone


def new_thread(history: list | None = None) -> dict:
    h = list(history or [])
    return {
        'id':      uuid.uuid4().hex[:12],
        'title':   thread_title(h),
        # Store unambiguous UTC timestamps in browser conversation data.
        'created': datetime.now(timezone.utc).isoformat(),
        'history': h,
    }


def thread_title(history: list) -> str:
    if not history:
        return 'New chat'
    first_q = (history[0].get('q') or '').strip().replace('\n', ' ')
    if not first_q:
        return 'New chat'
    return first_q[:40] + ('…' if len(first_q) > 40 else '')


def find_thread(threads: list, thread_id: str | None) -> dict | None:
    if not thread_id:
        return None
    for t in threads or []:
        if t.get('id') == thread_id:
            return t
    return None


def active_history(threads: list, active_id: str | None) -> list:
    t = find_thread(threads, active_id)
    return list(t.get('history') or []) if t else []


def commit_history(threads: list, active_id: str | None,
                    history: list) -> tuple[list, str]:
    """Write `history` into the active thread, creating one if needed.
    Returns (threads, active_id).  Auto-titles from the first user message."""
    threads = list(threads or [])
    t = find_thread(threads, active_id)
    if t is None:
        t = new_thread(history)
        threads.insert(0, t)
        active_id = t['id']
    else:
        t['history'] = list(history)
        # Only retitle if the title is still the default placeholder.
        if t.get('title', 'New chat') in ('New chat', ''):
            t['title'] = thread_title(history)
    return threads, active_id
