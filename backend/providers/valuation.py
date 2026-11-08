"""
Macro market valuation indicators.

Three metrics that give a high-level read on whether the overall market is
cheap or expensive — independent of your specific portfolio:

  Buffett Indicator  — US Total Market Cap / US GDP × 100
  S&P 500 P/E        — Trailing & forward price-to-earnings (yfinance)
  Shiller CAPE       — Cyclically Adjusted P/E (10-year real earnings avg)
                       Source: Robert Shiller / Yale (ie_data.xls)

All functions are cached 4 hours so repeated Dash renders never hit the
network. Each returns None on failure so the UI can degrade gracefully.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

import pandas as pd

from backend.storage.cache import cached_fetch

log = logging.getLogger(__name__)

_TTL = 3600 * 4   # 4 hours; backed by cache_util (diskcache, persists across restarts)


# ── Zone classifiers ───────────────────────────────────────────────────────────

def buffett_zone(v: float) -> tuple[str, str]:
    """(label, hex-colour) for a given Buffett Indicator value (%).

    Thresholds anchored to the modern structural baseline (~125-130%),
    not the pre-tech-era 100%.  The US economy shifted from capital-heavy
    industry to high-margin software, so market cap naturally runs higher
    relative to GDP today.
    """
    if v < 75:   return ('Well Below Historic Norms',     '#16a34a')
    if v < 110:  return ('Fairly Valued',                 '#22c55e')
    if v < 150:  return ('Elevated — Within Modern Range', '#84cc16')
    if v < 190:  return ('Running Hot',                   '#f97316')
    return              ('Historically Stretched',        '#dc2626')


def pe_zone(v: float) -> tuple[str, str]:
    """(label, hex-colour) for a trailing P/E value."""
    if v < 15:   return ('Cheap',                 '#16a34a')
    if v < 20:   return ('Fairly Valued',          '#22c55e')
    if v < 25:   return ('Expensive',              '#eab308')
    if v < 30:   return ('Very Expensive',         '#f97316')
    return              ('Extremely Expensive',    '#dc2626')


def cape_zone(v: float) -> tuple[str, str]:
    """(label, hex-colour) for a Shiller CAPE value."""
    if v < 15:   return ('Undervalued',            '#16a34a')
    if v < 20:   return ('Fairly Valued',          '#22c55e')
    if v < 25:   return ('Overvalued',             '#eab308')
    if v < 30:   return ('Highly Overvalued',      '#f97316')
    return              ('Extremely Overvalued',   '#dc2626')


def treasury_zone(v: float) -> tuple[str, str]:
    """
    (label, hex-colour) for the 10-year Treasury yield (%).

    Low yield  → bonds pay little → stocks face less competition (green).
    High yield → bonds pay well   → stocks must justify higher valuations (red).
    """
    if v < 2:    return ('Very Low — Stocks Favoured',   '#16a34a')
    if v < 3:    return ('Low — Stocks Competitive',     '#22c55e')
    if v < 4:    return ('Moderate',                     '#eab308')
    if v < 5:    return ('Elevated — Bonds Competitive', '#f97316')
    return              ('High — Bonds Attractive',      '#dc2626')


# ── Buffett Indicator ──────────────────────────────────────────────────────────

def get_buffett_indicator() -> dict | None:
    """Annual US listed-domestic market cap / GDP, both in current USD.

    World Bank CM.MKT.LCAP.CD and NY.GDP.MKTP.CD are matched by year.
    This is a dated annual observation, not a live Wilshire-index estimate.
    """
    def fetch():
        observations = []
        for indicator in ('CM.MKT.LCAP.CD', 'NY.GDP.MKTP.CD'):
            url = (f'https://api.worldbank.org/v2/country/US/indicator/{indicator}'
                   '?format=json&per_page=20&mrv=20')
            request = urllib.request.Request(url, headers={'User-Agent': 'IBKRDashboard/1.0'})
            with urllib.request.urlopen(request, timeout=8) as response:
                records = json.loads(response.read())[1] or []
            observations.append({row['date']: float(row['value']) for row in records
                                 if row.get('value') is not None and float(row['value']) > 0})
        market_caps, gdps = observations
        years = market_caps.keys() & gdps.keys()
        if not years:
            raise ValueError('No common year for market capitalization and GDP')
        year = max(years)
        return {'value': round(market_caps[year] / gdps[year] * 100, 1),
                'market_cap_t': round(market_caps[year] / 1e12, 1),
                'gdp_t': round(gdps[year] / 1e12, 1),
                'gdp_quarter': year, 'gdp_source': 'World Bank, annual; listed domestic companies'}
    try:
        return cached_fetch('buffett_annual_usd_v1', _TTL, fetch)
    except Exception as error:
        log.warning('Buffett indicator unavailable: %s', error)
        return None


# ── S&P 500 P/E ratio ──────────────────────────────────────────────────────────

def get_sp500_pe() -> dict | None:
    """
    Trailing P/E for the S&P 500.

    Primary:  multpl.com monthly table (most reliable, no API key needed).
    Fallback: yfinance SPY ETF (^GSPC does not expose PE via yfinance).

    Returns
    -------
    {trailing_pe, forward_pe, price}  or None on failure.
    """
    def fetch():
        import re
        # ── Primary: multpl.com ───────────────────────────────────────────────
        try:
            url = 'https://www.multpl.com/s-p-500-pe-ratio/table/by-month'
            req = urllib.request.Request(
                url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
            with urllib.request.urlopen(req, timeout=6) as resp:
                html = resp.read().decode('utf-8', errors='replace')
            rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL)
            values = []
            for row in rows:
                cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
                if len(cells) >= 2:
                    val_text = re.sub(r'<[^>]+>', '', cells[1]).strip()
                    try:
                        values.append(float(val_text))
                    except ValueError:
                        pass
            if values:
                trailing = round(values[0], 1)
                # Also grab ^GSPC price from yfinance for display
                price = None
                try:
                    import yfinance as yf
                    hist = yf.Ticker('^GSPC').history(period='2d')
                    if not hist.empty:
                        price = round(float(hist['Close'].dropna().iloc[-1]), 2)
                except Exception:
                    pass
                return {'trailing_pe': trailing, 'forward_pe': None, 'price': price}
        except Exception as e:
            log.debug('multpl P/E fetch failed, trying yfinance: %s', e)

        # ── Fallback: yfinance SPY (ETF exposes PE, ^GSPC does not) ──────────
        import yfinance as yf
        info     = yf.Ticker('SPY').info
        trailing = info.get('trailingPE')
        forward  = info.get('forwardPE')
        price    = None
        try:
            hist  = yf.Ticker('^GSPC').history(period='2d')
            price = round(float(hist['Close'].dropna().iloc[-1]), 2)
        except Exception:
            pass
        if trailing is None and forward is None:
            raise RuntimeError('No P/E data from multpl or yfinance')
        return {
            'trailing_pe': round(float(trailing), 1) if trailing else None,
            'forward_pe':  round(float(forward),  1) if forward  else None,
            'price':       price,
        }

    try:
        return cached_fetch('sp500_pe', _TTL, fetch)
    except Exception as e:
        log.warning('S&P 500 P/E fetch failed: %s', e)
        return None


# ── Shiller CAPE ───────────────────────────────────────────────────────────────

def get_shiller_cape() -> dict | None:
    """
    Cyclically Adjusted P/E (Shiller CAPE / P/E10).

    Source: multpl.com monthly table — more reliably accessible than Yale's
    ie_data.xls which is often slow or unreachable.

    Returns
    -------
    {value, hist_mean, hist_median, last_date, dates:[str], values:[float]}
    or None on failure.
    """
    def fetch():
        import re
        url = 'https://www.multpl.com/shiller-pe/table/by-month'
        req = urllib.request.Request(
            url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        with urllib.request.urlopen(req, timeout=6) as resp:
            html = resp.read().decode('utf-8', errors='replace')

        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL)
        dates, values = [], []
        for row in rows:
            cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
            if len(cells) < 2:
                continue
            date_text = re.sub(r'<[^>]+>', '', cells[0]).strip()
            val_text  = re.sub(r'[<][^>]+[>]|\xa0|&#x2002;', '', cells[1]).strip()
            try:
                # Parse "Apr 1, 2026" → "2026-04"
                dt = pd.to_datetime(date_text, errors='coerce')
                if pd.isna(dt):
                    continue
                dates.append(dt.strftime('%Y-%m'))
                values.append(float(val_text))
            except (ValueError, TypeError):
                continue

        if not values:
            raise RuntimeError('Could not parse CAPE table from multpl.com')

        # multpl returns newest-first — reverse to oldest-first for stats/chart
        dates  = dates[::-1]
        values = values[::-1]

        series  = pd.Series(values, dtype=float)
        current = values[-1]
        last_date = dates[-1]

        # Last ~50 years for chart (≈ 600 monthly rows)
        chart_dates  = dates[-600:]
        chart_values = values[-600:]

        return {
            'value':       round(current,           1),
            'hist_mean':   round(float(series.mean()),   1),
            'hist_median': round(float(series.median()), 1),
            'last_date':   last_date,
            'dates':       chart_dates,
            'values':      [round(v, 1) for v in chart_values],
        }

    try:
        return cached_fetch('shiller_cape', _TTL, fetch)
    except Exception as e:
        log.warning('Shiller CAPE fetch failed: %s', e)
        return None


# ── 10-Year Treasury Yield ─────────────────────────────────────────────────────

def get_treasury_yield() -> dict | None:
    """
    Current US 10-year Treasury yield (^TNX via yfinance).

    Returns
    -------
    {value}  e.g. {'value': 4.35}  or None on failure.
    """
    def fetch():
        import yfinance as yf
        hist = yf.Ticker('^TNX').history(period='5d')
        if hist.empty:
            raise RuntimeError('Empty ^TNX history')
        value = round(float(hist['Close'].dropna().iloc[-1]), 2)
        return {'value': value}

    try:
        return cached_fetch('treasury_yield', _TTL, fetch)
    except Exception as e:
        log.warning('Treasury yield fetch failed: %s', e)
        return None
