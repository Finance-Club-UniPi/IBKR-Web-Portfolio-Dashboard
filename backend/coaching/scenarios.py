"""
Portfolio Coach — rules-based scenario answers for beginner investors.

Each scenario is a pure function:
    fn(portfolio_data, market_intel, valuation_data) -> answer dict

No network calls, no LLMs, no external state. Everything is computed from
stores the dashboard already maintains. When any required piece of data is
missing the scenario returns a friendly "not ready yet" answer instead of
erroring. UI rendering lives in ui/components/scenarios.py.

The plain-English phrasing is deliberate. Thresholds are conservative and
explicit so a beginner can sanity-check the reasoning against the numbers
shown elsewhere on the dashboard.
"""

from __future__ import annotations

import math

from backend.portfolio.exposure import sector_geo_breakdown

# ── Presentation helpers ──────────────────────────────────────────────────────

def _answer(headline: str, body: list[str], tone: str = 'info') -> dict:
    return {'headline': headline, 'body': body, 'tone': tone}


def _not_ready(reason: str):
    return _answer("Not enough data yet",
                   [reason + " Try again in a few seconds once the dashboard has finished loading."],
                   tone='info')


def _fmt_money(v: float, port) -> str:
    account = port.get('account') or {}
    if account.get('portfolio_value_units') != 'base':
        return 'unavailable (unverified currency units)'
    currency = account.get('base_currency') or 'unknown currency'
    sign = '-' if v < 0 else ''
    return f"{sign}{abs(v):,.0f} {currency}"


def _fmt_pct(v: float) -> str:
    sign = '+' if v > 0 else ''
    return f"{sign}{v:.1f}%"


# ── Scenarios ─────────────────────────────────────────────────────────────────

def scenario_performance(port, _intel, _val):
    if not port or 'summary' not in port or not port.get('positions'):
        return _not_ready("Positions haven't loaded yet.")
    s = port['summary']
    pnl      = s.get('total_unrealized_pnl', 0)
    pnl_pct  = s.get('total_pnl_pct', 0)
    best     = s.get('best_performer', '—')
    worst    = s.get('worst_performer', '—')
    total    = s.get('total_value', 0)
    daily    = s.get('total_daily_pnl')

    # find per-ticker p&l% for the best/worst for richer text
    by_ticker = {p['ticker']: p for p in port['positions']}
    best_pct  = by_ticker.get(best,  {}).get('pnl_pct', 0)
    worst_pct = by_ticker.get(worst, {}).get('pnl_pct', 0)

    losers = [p for p in port['positions'] if p.get('pnl_pct', 0) < 0]

    tone = 'good' if pnl_pct >= 0 else 'warn'
    lines = [
        f"Portfolio value: {_fmt_money(total, port)} · Unrealised P&L: "
        f"{_fmt_money(pnl, port)} ({_fmt_pct(pnl_pct)} on cost).",
        f"Biggest winner: {best} ({_fmt_pct(best_pct)}). "
        + (f"Biggest drag: {worst} ({_fmt_pct(worst_pct)})."
           if losers else "No losers today — every position is in the green, which is a good reminder that this won't always be the case."),
    ]
    if daily is not None:
        lines.append(f"Today's move: {_fmt_money(daily, port)}.")

    headline = ("Good shape — up overall." if pnl_pct >= 0
                else "Down overall — worth reviewing which positions are dragging.")
    return _answer(headline, lines, tone=tone)


def scenario_biggest_risk(port, intel, val):
    if not port or 'summary' not in port:
        return _not_ready("Positions haven't loaded yet.")

    s       = port['summary']
    top     = s.get('largest_position', '—')
    top_pct = s.get('largest_position_pct')
    if top_pct is None or not math.isfinite(top_pct):
        return _not_ready('Verified base-currency holding weights are unavailable.')

    # Sector concentration (reuses the same exposure market-intel builds)
    top_sector_name, top_sector_pct = None, 0
    if intel and intel.get('sector_geo'):
        sectors, _ = sector_geo_breakdown(port['positions'], intel['sector_geo'])
        if sectors:
            top_sector_name, top_sector_pct, _ = sectors[0]

    # Valuation context — CAPE and Buffett, if available
    cape_val = (val or {}).get('cape', {}).get('value') if val else None
    buf_val  = (val or {}).get('buffett', {}).get('value') if val else None

    # Rank risks: concentration > sector > valuation
    if top_pct >= 50:
        body = [
            f"Concentration. {top} alone is {top_pct:.0f}% of your portfolio — "
            f"if it drops 10%, that's about {top_pct/10:.1f}% of everything.",
            "For a beginner diversified portfolio, many advisors cap any single position at 20–30%. "
            "Above 50% is a large single-name bet — fine if intentional, worth knowing either way.",
        ]
        if top_sector_name and top_sector_pct >= 40:
            body.append(
                f"Sector-wise you're also heavy in {top_sector_name} (~{top_sector_pct:.0f}% of holdings), "
                "which doubles the concentration."
            )
        return _answer("Biggest risk: concentration in one holding.", body, tone='warn')

    if top_sector_name and top_sector_pct >= 45:
        body = [
            f"Sector tilt. You're ~{top_sector_pct:.0f}% in {top_sector_name} across all holdings combined.",
            "A rotation out of that sector would hit you harder than the broader market. "
            "Roughly 30–35% in any one sector is a common beginner ceiling.",
        ]
        return _answer("Biggest risk: sector concentration.", body, tone='warn')

    if cape_val and cape_val >= 32:
        body = [
            f"Market-wide valuation. The Shiller CAPE is at {cape_val:.0f} — historically elevated "
            "(long-run average is ~17).",
            "This doesn't predict a crash, but it does mean future returns have historically been "
            "lower when entering at these levels. Your own positions look balanced; the macro backdrop is the wild card.",
        ]
        if buf_val:
            body.append(f"Buffett Indicator is also running hot at {buf_val:.0f}%.")
        return _answer("Biggest risk: elevated market valuations.", body, tone='warn')

    # Everything looks reasonable
    return _answer(
        "No single dominant risk stands out.",
        [
            f"Top holding ({top}) is {top_pct:.0f}% of the portfolio — within reasonable limits.",
            "Keep an eye on concentration as positions grow, and watch upcoming earnings for your larger holdings.",
        ],
        tone='good',
    )


def scenario_what_if(port, _intel, _val):
    if not port or not port.get('positions'):
        return _not_ready("Positions haven't loaded yet.")

    positions = port['positions']
    if any(p.get('allocation_pct') is None or not math.isfinite(p['allocation_pct'])
           or p['allocation_pct'] < 0 for p in positions):
        return _not_ready('Verified base-currency holding weights are unavailable.')
    top = max(positions, key=lambda p: p['allocation_pct'])
    total_value = sum(p['base_market_value'] for p in positions)
    share = top.get('allocation_pct', 0) / 100.0
    impact_pct = share * 20.0
    impact_eur = total_value * share * 0.20

    headline = f"If {top['ticker']} drops 20%, you'd lose about {_fmt_money(impact_eur, port)}."
    body = [
        f"That's roughly {impact_pct:.1f}% of your total portfolio value.",
        "The rest of your holdings wouldn't automatically offset that — they move somewhat independently.",
    ]

    # If the top holding is a diversified ETF, soften the tone
    is_etf = False
    if _intel and _intel.get('sector_geo'):
        is_etf = _intel['sector_geo'].get(top['ticker'], {}).get('is_etf', False)
    if is_etf:
        body.append(
            f"{top['ticker']} is a broad ETF, so a 20% drop would typically only happen during a market-wide "
            "sell-off — not an idiosyncratic single-company event."
        )
    else:
        body.append(
            f"Because {top['ticker']} is a single stock, a 20% drop on news (earnings miss, guidance cut) "
            "is entirely possible — that's the concentration trade-off."
        )

    tone = 'warn' if impact_pct >= 10 else 'info'
    return _answer(headline, body, tone=tone)


def scenario_sector_geo(port, intel, _val):
    if not port or not port.get('positions'):
        return _not_ready("Positions haven't loaded yet.")
    if not intel or not intel.get('sector_geo'):
        return _not_ready("Market-intelligence data (sector / geography) is still loading.")

    sectors, countries = sector_geo_breakdown(port['positions'], intel['sector_geo'])
    if not sectors:
        return _not_ready('Verified base-currency holding weights are unavailable.')
    top_sectors = [(name, weight) for name, weight, _ in sectors[:3]]
    top_countries = countries[:3]

    sec_str = ", ".join(f"{name} {pct:.0f}%" for name, pct in top_sectors) or "—"
    geo_str = ", ".join(f"{name} {pct:.0f}%" for name, pct in top_countries) or "—"

    top_sec_pct = top_sectors[0][1] if top_sectors else 0
    top_geo_pct = top_countries[0][1] if top_countries else 0

    body = [
        f"Sector mix (top 3): {sec_str}.",
        f"Geography (top 3): {geo_str}.",
    ]
    if top_sec_pct >= 45:
        body.append(f"Heads-up: {top_sectors[0][0]} is ~{top_sec_pct:.0f}% of your portfolio — concentrated. "
                    "A common beginner ceiling is 30–35% per sector.")
        tone = 'warn'
    elif top_geo_pct >= 85:
        body.append(f"You're ~{top_geo_pct:.0f}% in one region. Adding a position outside it (developed Europe, "
                    "emerging markets, or a global ETF) is the simplest diversification move.")
        tone = 'warn'
    else:
        body.append("Balance looks reasonable for a beginner portfolio. No urgent rebalancing signal from sector or geography alone.")
        tone = 'good'

    return _answer("Sector & geography breakdown", body, tone=tone)


def scenario_vs_market(port, _intel, val):
    if not port or 'summary' not in port:
        return _not_ready("Positions haven't loaded yet.")

    your_pct = port['summary'].get('total_pnl_pct', 0)

    # Best effort: use S&P 500 ytd from CAPE/Buffett? We don't have ytd here.
    # Show your return and compare qualitatively to long-run equity average (~8%/yr).
    body = [
        f"Your portfolio is {_fmt_pct(your_pct)} on cost (unrealised).",
        "The S&P 500's long-run average is about 8–10% per year. "
        "If you bought most holdings within the past 12 months, comparing to that yardstick is a rough but useful sanity check.",
    ]
    # Flag if a single position is doing most of the work
    positions = port['positions']
    if positions:
        total_pnl = sum(p.get('unrealized_pnl', 0) for p in positions) or 1
        biggest   = max(positions, key=lambda p: abs(p.get('unrealized_pnl', 0)))
        share     = biggest.get('unrealized_pnl', 0) / total_pnl * 100
        if abs(share) >= 60:
            body.append(
                f"Most of your gains come from one name: {biggest['ticker']} "
                f"accounts for ~{share:.0f}% of total unrealised P&L. "
                "Without it, your portfolio would be tracking the market closely — worth knowing."
            )

    tone = 'good' if your_pct >= 8 else 'info'
    return _answer(
        ("Beating the long-run average." if your_pct >= 10
         else "Tracking roughly in line with long-run averages." if your_pct >= 5
         else "Below long-run averages — but this depends heavily on how long you've held."),
        body, tone=tone,
    )


def scenario_news(port, intel, _val):
    """Latest headlines per holding. `intel['news']` is {ticker: [items] | None}."""
    if not port or not port.get('positions'):
        return _not_ready("Positions haven't loaded yet.")
    news = (intel or {}).get('news')
    if news is None:
        return _not_ready("Headlines haven't loaded yet (Yahoo Finance may be unreachable).")

    ranked = sorted(port['positions'], key=lambda p: p.get('allocation_pct') if p.get('allocation_pct') is not None and math.isfinite(p['allocation_pct']) else 0, reverse=True)
    return {'kind': 'news', 'positions': ranked, 'news': news}


# ── Registry ──────────────────────────────────────────────────────────────────
# Order matters: this is the order they appear in the dropdown.

SCENARIOS = [
    {'id': 'perf',          'label': 'Quick performance snapshot',       'fn': scenario_performance},
    {'id': 'biggest_risk',  'label': 'What is my biggest risk?',         'fn': scenario_biggest_risk},
    {'id': 'what_if',       'label': 'What if my biggest holding drops 20%?', 'fn': scenario_what_if},
    {'id': 'sector_geo',    'label': 'Am I balanced by sector & geography?',  'fn': scenario_sector_geo},
    {'id': 'vs_market',     'label': 'How am I doing vs the market?',    'fn': scenario_vs_market},
    {'id': 'news',          'label': "What's the latest news on my holdings?", 'fn': scenario_news},
]

SCENARIOS_BY_ID = {s['id']: s for s in SCENARIOS}


def evaluate_scenario(scenario_id: str, port, intel, val) -> dict:
    """Return answer data; failures are handled by the presentation boundary."""
    return SCENARIOS_BY_ID[scenario_id]['fn'](port, intel, val)
