"""Recent headlines for the portfolio's holdings.

Source: Yahoo Finance's per-ticker headline RSS feed (the same stories Yahoo
shows on a quote page, with the original article links).  Cached for 15
minutes per ticker through `cache_util`.  Failures raise inside the cached
function so a network blip is never cached; callers see `None` for a ticker
whose fetch failed and `[]` for one with simply no recent headlines.

Headlines are third-party text.  They are rendered as plain text links in the
UI and passed to the LLM inside a clearly delimited block (see ai_provider).
"""

from __future__ import annotations

import html
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import requests

from backend.network import fetch_parallel
from backend.providers.market_data import _resolve_yf_sym
from backend.storage.cache import cached_fetch

log = logging.getLogger(__name__)

_FEED_URL = 'https://feeds.finance.yahoo.com/rss/2.0/headline'
_TTL = 15 * 60
_MAX_AGE = timedelta(days=14)
_TAG_RE = re.compile(r'<[^>]+>')

# Friendly names for the publishers that show up most; anything else falls
# back to its domain.
_SOURCES = {
    'finance.yahoo.com': 'Yahoo Finance', 'uk.finance.yahoo.com': 'Yahoo Finance UK',
    'fool.com': 'Motley Fool', 'investors.com': "Investor's Business Daily",
    'reuters.com': 'Reuters', 'bloomberg.com': 'Bloomberg', 'cnbc.com': 'CNBC',
    'marketwatch.com': 'MarketWatch', 'barrons.com': "Barron's", 'wsj.com': 'WSJ',
    'ft.com': 'Financial Times', 'seekingalpha.com': 'Seeking Alpha',
    'benzinga.com': 'Benzinga', 'stocktwits.com': 'Stocktwits', 'zacks.com': 'Zacks',
    'thestreet.com': 'TheStreet', 'investopedia.com': 'Investopedia',
}


def _clean(text: str | None, limit: int) -> str:
    text = html.unescape(_TAG_RE.sub(' ', text or ''))
    text = ' '.join(text.split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def _text(node, tag: str) -> str:
    """All text under `tag`, including text after any nested markup elements."""
    child = node.find(tag)
    return ''.join(child.itertext()) if child is not None else ''


def _source_name(url: str) -> str:
    host = (urlparse(url).hostname or '').lower()
    if host in _SOURCES:
        return _SOURCES[host]
    bare = host[4:] if host.startswith('www.') else host
    return _SOURCES.get(bare, bare)


def parse_feed(xml_bytes: bytes, now: datetime | None = None, per_ticker: int = 6) -> list[dict]:
    """Parse one RSS document into newest-first [{title, link, source, published, summary}]."""
    now = now or datetime.now(timezone.utc)
    items, seen = [], set()
    for node in ET.fromstring(xml_bytes).iter('item'):
        title = _clean(_text(node, 'title'), 200)
        link = (node.findtext('link') or '').strip()
        if not title or title.lower() in seen:
            continue
        if urlparse(link).scheme not in ('http', 'https'):   # never emit javascript:/data: links
            continue
        try:
            published = parsedate_to_datetime(node.findtext('pubDate') or '')
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            published = published.astimezone(timezone.utc)
        except (TypeError, ValueError):
            continue
        if now - published > _MAX_AGE or published - now > timedelta(hours=1):
            continue
        seen.add(title.lower())
        items.append({'title': title, 'link': link, 'source': _source_name(link),
                      'published': published.isoformat(),
                      'summary': _clean(_text(node, 'description'), 220)})
    items.sort(key=lambda i: i['published'], reverse=True)
    return items[:per_ticker]


def _fetch_one(symbol: str) -> list[dict]:
    r = requests.get(_FEED_URL, params={'s': symbol, 'region': 'US', 'lang': 'en-US'},
                     headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
    r.raise_for_status()
    return parse_feed(r.content)


def get_news(tickers: list[str]) -> dict[str, list[dict] | None]:
    """{ticker: headlines} — [] when none, None when the fetch failed."""
    def one(ticker):
        symbol = _resolve_yf_sym(ticker)
        return cached_fetch(('news', symbol), _TTL, lambda: _fetch_one(symbol))

    return fetch_parallel(list(dict.fromkeys(tickers)), one)


def news_for_portfolio(port):
    """Cached headlines for the current holdings, or None if they can't be loaded."""
    tickers = [p['ticker'] for p in (port or {}).get('positions', []) if p.get('ticker')]
    if not tickers:
        return None
    try:
        news = get_news(tickers)
    except Exception:
        log.exception("coach: headline fetch failed")
        return None
    return None if all(v is None for v in news.values()) else news
