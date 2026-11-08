"""Investment signal labels derived from already-fetched fundamentals."""

def score_fundamentals(f: dict) -> dict:
    """
    Derive 6 signal labels + narrative text from a fundamentals dict.
    Pure function — no network calls. Returns {} if f is empty.
    """
    if not f:
        return {}

    _RED    = '#dc2626'
    _ORANGE = '#ea580c'
    _YELLOW = '#d97706'
    _GREEN  = '#16a34a'
    _GRAY   = '#9ca3af'
    _TEAL   = '#0891b2'

    pe          = f.get('fwd_pe')
    peg         = f.get('peg')
    rev         = f.get('rev_growth_pct')
    margin      = f.get('profit_margin')
    roe         = f.get('roe')
    fcf_yield   = f.get('fcf_yield')
    debt_ebitda = f.get('debt_ebitda')
    net_cash_pct = f.get('net_cash_pct')
    buy         = f.get('analyst_buy')
    hold_n      = f.get('analyst_hold')
    sell        = f.get('analyst_sell')
    short_pct   = f.get('short_pct')
    insider_net = f.get('insider_net')
    rsi         = f.get('rsi_14')
    above_50d   = f.get('above_50d_ma')

    # ── Valuation ─────────────────────────────────────────────────────────────
    val_label, val_color = 'Unknown', _GRAY
    if pe is not None or peg is not None:
        if (pe and pe > 40) or (peg and peg > 3):
            val_label, val_color = 'Very High', _RED
        elif (pe and pe > 25) or (peg and peg > 2):
            val_label, val_color = 'High', _ORANGE
        elif (pe is None or pe < 15) and (peg is None or peg < 1):
            val_label, val_color = 'Low', _GREEN
        else:
            val_label, val_color = 'Fair', _YELLOW

    # ── Growth ────────────────────────────────────────────────────────────────
    grw_label, grw_color = 'Unknown', _GRAY
    if rev is not None:
        if rev > 20:
            grw_label, grw_color = 'Very Strong', _GREEN
        elif rev > 10:
            grw_label, grw_color = 'Strong', _GREEN
        elif rev > 5:
            grw_label, grw_color = 'Moderate', _YELLOW
        elif rev >= 0:
            grw_label, grw_color = 'Weak', _ORANGE
        else:
            grw_label, grw_color = 'Declining', _RED

    # ── Profitability ─────────────────────────────────────────────────────────
    prf_label, prf_color = 'Unknown', _GRAY
    if margin is not None:
        if margin > 20:
            prf_label, prf_color = 'Strong', _GREEN
        elif margin > 10:
            prf_label, prf_color = 'Moderate', _YELLOW
        elif margin > 0:
            prf_label, prf_color = 'Weak', _ORANGE
        else:
            prf_label, prf_color = 'Losing', _RED
        if roe is not None and roe > 20 and prf_label in ('Moderate', 'Weak'):
            prf_label, prf_color = 'Strong', _GREEN

    # ── Financial Health ──────────────────────────────────────────────────────
    fin_label, fin_color = 'Unknown', _GRAY
    has_fin = any(v is not None for v in [fcf_yield, debt_ebitda, net_cash_pct])
    if has_fin:
        net_cash_pos = net_cash_pct is not None and net_cash_pct > 0
        low_debt     = debt_ebitda is not None and debt_ebitda < 2
        pos_fcf      = fcf_yield   is not None and fcf_yield > 3
        high_debt    = debt_ebitda is not None and debt_ebitda > 5
        neg_fcf      = fcf_yield   is not None and fcf_yield < 0
        if net_cash_pos:                    # net cash on balance sheet trumps everything
            fin_label, fin_color = 'Strong', _GREEN
        elif pos_fcf and low_debt:          # generates cash AND carries little debt
            fin_label, fin_color = 'Strong', _GREEN
        elif neg_fcf or high_debt:          # burning cash OR heavily leveraged
            fin_label, fin_color = 'At Risk', _RED
        else:
            fin_label, fin_color = 'Moderate', _YELLOW

    # ── Sentiment — composite: analyst bias + insider activity + short interest ─
    # Analysts alone are lagging. Insider buying is the strongest signal; high
    # short interest is a warning even when Wall Street is bullish.
    snt_label, snt_color = 'Unknown', _GRAY
    if all(v is not None for v in [buy, hold_n, sell]):
        total = (buy or 0) + (hold_n or 0) + (sell or 0)
        if total > 0:
            buy_pct = (buy or 0) / total * 100
            score   = 2 if buy_pct > 60 else (1 if buy_pct > 40 else -1)
            if insider_net == 'Net+':   score += 1   # insiders buying: strong positive
            elif insider_net == 'Net−': score -= 1  # insiders selling: negative
            if short_pct is not None and short_pct > 10:
                score -= 1               # >10% short float = known bearish thesis
            if score >= 3:
                snt_label, snt_color = 'Very Bullish', _GREEN
            elif score >= 2:
                snt_label, snt_color = 'Bullish', _GREEN
            elif score >= 1:
                snt_label, snt_color = 'Neutral', _YELLOW
            elif score == 0:
                snt_label, snt_color = 'Cautious', _ORANGE
            else:
                snt_label, snt_color = 'Bearish', _RED
    elif insider_net == 'Net+':
        snt_label, snt_color = 'Insider Buying', _GREEN
    elif insider_net == 'Net−':
        snt_label, snt_color = 'Insider Selling', _RED

    # ── Momentum ──────────────────────────────────────────────────────────────
    mom_label, mom_color = 'Unknown', _GRAY
    if rsi is not None or above_50d is not None:
        overbought  = rsi is not None and rsi > 70
        oversold    = rsi is not None and rsi < 30
        neutral_rsi = rsi is not None and 40 <= rsi <= 70
        above       = above_50d is True
        below       = above_50d is False
        if overbought:
            mom_label, mom_color = 'Extended', _ORANGE      # run hard — watch for reversal
        elif oversold:
            mom_label, mom_color = 'Oversold', _TEAL        # potential bounce zone
        elif above and neutral_rsi:
            mom_label, mom_color = 'Uptrend', _GREEN        # trend intact, not stretched
        elif below and (rsi is None or rsi < 45):
            mom_label, mom_color = 'Losing Momentum', _RED  # trend broken, weak RSI
        else:
            mom_label, mom_color = 'Mixed', _YELLOW         # conflicting signals

    # ── Overall text ──────────────────────────────────────────────────────────
    _OVERALL_MAP = {
        ('Very High', 'Very Strong'): ("High-conviction growth stock — priced for perfection",
                                       "Strong company, but any earnings miss will reprice it sharply"),
        ('Very High', 'Strong'):      ("Expensive premium for solid — not exceptional — growth",
                                       "Poor risk/reward unless the growth rate accelerates"),
        ('Very High', 'Moderate'):    ("Overpriced for the growth on offer — limited upside",
                                       "High risk of disappointment at current valuation"),
        ('Very High', 'Weak'):        ("Very expensive, growth falling short — unfavorable setup",
                                       "Price implies a turnaround the numbers don't yet support"),
        ('Very High', 'Declining'):   ("Danger zone — premium price, shrinking revenue",
                                       "Unless a major catalyst exists, risk outweighs reward"),
        ('High', 'Very Strong'):      ("Growth justifies a premium — but execution must continue",
                                       "Attractive if momentum holds; vulnerable if growth slows"),
        ('High', 'Strong'):           ("Paying up for quality — reasonable if growth persists",
                                       "Balanced risk/reward, but limited downside protection"),
        ('High', 'Moderate'):         ("Priced for more growth than it is currently delivering",
                                       "Needs to accelerate to justify the valuation"),
        ('High', 'Weak'):             ("Expensive with weak growth — unfavorable setup",
                                       "Market may be mispricing the risk here"),
        ('High', 'Declining'):        ("Expensive while the business contracts — high risk",
                                       "Elevated downside if decline continues"),
        ('Fair', 'Very Strong'):      ("Strong growth at a fair price — favorable setup",
                                       "Potentially undervalued; reward outweighs risk here"),
        ('Fair', 'Strong'):           ("Solid risk/reward — growing without overpaying",
                                       "A balanced position; no urgent reason to add or exit"),
        ('Fair', 'Moderate'):         ("Steady business, fair price — no strong edge either way",
                                       "Hold if you own it; no compelling entry or exit signal"),
        ('Fair', 'Weak'):             ("Fair price, but growth needs to recover to matter",
                                       "Monitor for improvement before adding exposure"),
        ('Fair', 'Declining'):        ("Reasonable price, but the business is heading the wrong way",
                                       "Watch for stabilization before drawing conclusions"),
        ('Low', 'Very Strong'):       ("Overlooked opportunity — fast growth at a low price",
                                       "Strong risk/reward if the growth rate is sustainable"),
        ('Low', 'Strong'):            ("Attractively priced with solid fundamentals",
                                       "Favorable entry — reward outweighs risk if growth holds"),
        ('Low', 'Moderate'):          ("Cheap, but verify the low price is not deserved",
                                       "Value play or value trap — the business must stabilize"),
        ('Low', 'Weak'):              ("Cheap for a reason — do not mistake price for value",
                                       "Investigate why it is this cheap before acting"),
        ('Low', 'Declining'):         ("Deep discount, but the business is shrinking",
                                       "Only for distressed-value specialists — high risk"),
    }
    key = (val_label, grw_label)
    if key in _OVERALL_MAP:
        overall, overall_sub = _OVERALL_MAP[key]
    elif val_label != 'Unknown' and grw_label == 'Unknown':
        overall = f"{val_label} valuation"
        overall_sub = "Growth data unavailable"
    elif grw_label != 'Unknown' and val_label == 'Unknown':
        overall = f"{grw_label} growth"
        overall_sub = "Valuation data unavailable"
    else:
        overall = "Insufficient data for full assessment"
        overall_sub = "Some metrics could not be retrieved"

    # ── Explanation sentences ─────────────────────────────────────────────────
    sentences = []
    if rev is not None:
        if rev > 20:
            sentences.append(f"This company is growing very fast (revenue {rev:+.1f}%/yr).")
        elif rev > 10:
            sentences.append(f"Revenue is growing solidly at {rev:+.1f}%/yr.")
        elif rev >= 0:
            sentences.append(f"Revenue growth is modest at {rev:+.1f}%/yr.")
        else:
            sentences.append(f"Revenue is shrinking ({rev:+.1f}%/yr).")
    if val_label not in ('Unknown',) and grw_label not in ('Unknown',):
        if val_label == 'Very High' and grw_label in ('Very Strong', 'Strong'):
            sentences.append("The stock is expensive — the market expects continued outperformance, and any earnings miss will be punished hard.")
        elif val_label == 'Very High':
            sentences.append("The stock is very expensive despite limited growth — the price implies a turnaround not yet visible in the numbers.")
        elif val_label == 'High' and grw_label in ('Very Strong', 'Strong'):
            sentences.append("The premium is supported by strong growth — but any slowdown would quickly erode the case for holding at this price.")
        elif val_label == 'High':
            sentences.append("The stock carries a premium the current growth rate does not fully support.")
        elif val_label == 'Low' and grw_label in ('Very Strong', 'Strong'):
            sentences.append("The stock is cheap relative to its growth — if that growth is real, this looks mispriced to the upside.")
        elif val_label == 'Low' and grw_label in ('Declining', 'Weak'):
            sentences.append("The low price likely reflects real business weakness — cheap is not the same as good value.")
        elif val_label == 'Fair':
            sentences.append("The stock is reasonably priced — no valuation alarm in either direction.")
    if fin_label == 'Strong':
        sentences.append("Financially strong — generates real cash with manageable debt.")
    elif fin_label == 'Moderate':
        sentences.append("Financial health is moderate — manageable debt but watch cash flow.")
    elif fin_label == 'At Risk':
        sentences.append("Financial health is a concern — elevated debt or negative cash flow.")

    # ── Watch items — driven by the specific metrics that triggered signals ────
    watch = []
    if val_label in ('Very High', 'High'):
        watch.append("Earnings vs expectations — a miss at this valuation will reprice the stock sharply")
    if rev is not None and 0 < rev < 8:
        watch.append("Revenue growth — it is thin and needs to hold or accelerate")
    elif rev is not None and rev < 0:
        watch.append("Whether the revenue decline is stabilizing or still deepening")
    if margin is not None and margin < 10:
        watch.append("Margin trend — profitability is below healthy levels and needs to improve")
    if fin_label == 'At Risk':
        watch.append("Liquidity runway and debt refinancing terms")
    if short_pct is not None and short_pct > 10:
        watch.append(f"High short interest ({short_pct:.1f}%) — a strong bearish thesis is already in the market")
    if mom_label == 'Extended':
        watch.append("Signs of momentum fading after an extended run")
    if not watch:
        watch = ["Quarterly earnings results", "Analyst estimate revisions"]
    watch = watch[:3]

    # ── Upside / Downside ─────────────────────────────────────────────────────
    if grw_label in ('Very Strong', 'Strong'):
        upside = "Growth continues above expectations → stock rises further"
    elif fin_label == 'Strong' and val_label == 'Low':
        upside = "Market re-rates the stock as value is recognized"
    elif prf_label in ('Weak', 'Losing'):
        upside = "Margins improve as the business scales"
    else:
        upside = "Steady performance sustains the current valuation"

    if val_label in ('Very High', 'High') and grw_label in ('Very Strong', 'Strong'):
        downside = "Growth slows or disappoints → stock drops sharply"
    elif fin_label == 'At Risk':
        downside = "Debt burden or cash burn forces dilutive financing"
    elif grw_label in ('Declining', 'Weak'):
        downside = "Continued revenue weakness leads to multiple compression"
    else:
        downside = "Macro headwinds or sector rotation compress the multiple"

    # ── One-line takeaway ─────────────────────────────────────────────────────
    _QUALITY_MAP = {
        ('Strong',   'Strong'):   "High-quality",
        ('Strong',   'Moderate'): "Solid",
        ('Strong',   'Weak'):     "Profitable but financially stretched",
        ('Moderate', 'Strong'):   "Decent quality",
        ('Moderate', 'Moderate'): "Steady",
        ('Weak',     'Strong'):   "Unprofitable but well-funded",
        ('Losing',   'At Risk'):  "High-risk",
    }
    _GROWTH_DESC = {
        'Very Strong': "very strong growth",
        'Strong':      "solid growth",
        'Moderate':    "moderate growth",
        'Weak':        "weak growth",
        'Declining':   "declining revenue",
        'Unknown':     "unconfirmed growth",
    }
    _RISK_MAP = {
        ('Very High', 'Very Strong'): "the price already assumes continued outperformance",
        ('Very High', 'Strong'):      "the premium leaves little room for error",
        ('High',      'Very Strong'): "any slowdown could cause sharp re-pricing",
        ('High',      'Moderate'):    "the valuation assumes improvement not yet delivered",
        ('Low',       'Declining'):   "the cheap price may signal deeper problems",
        ('Low',       'Weak'):        "cheap for a reason — verify the thesis before buying",
    }
    quality     = _QUALITY_MAP.get((prf_label, fin_label), "")
    growth_desc = _GROWTH_DESC.get(grw_label, "")
    risk        = _RISK_MAP.get((val_label, grw_label), "monitor for changes in the core drivers")
    if quality and growth_desc:
        takeaway = f"{quality} company with {growth_desc}, but {risk}."
    elif growth_desc:
        takeaway = f"Company with {growth_desc} — {risk}."
    else:
        takeaway = "Insufficient data for a definitive assessment."

    return {
        'signals': {
            'valuation':     {'label': val_label,  'color': val_color},
            'growth':        {'label': grw_label,  'color': grw_color},
            'profitability': {'label': prf_label,  'color': prf_color},
            'fin_health':    {'label': fin_label,  'color': fin_color},
            'sentiment':     {'label': snt_label,  'color': snt_color},
            'momentum':      {'label': mom_label,  'color': mom_color},
        },
        'overall':     overall,
        'overall_sub': overall_sub,
        'explanation': sentences,
        'watch':       watch,
        'upside':      upside,
        'downside':    downside,
        'takeaway':    takeaway,
    }
