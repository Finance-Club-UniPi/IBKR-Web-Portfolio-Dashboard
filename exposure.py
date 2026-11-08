"""One sector/country roll-up for charts, reports, and coaching.

Weights must already be verified in one account currency. Missing or short
exposures are unavailable rather than converted with a display FX fallback.
"""

import math


def sector_geo_breakdown(positions: list[dict], sector_geo: dict):
    sectors, tickers, countries = {}, {}, {}
    if not positions:
        return [], []
    for position in positions:
        weight = position.get('allocation_pct')
        if weight is None or not math.isfinite(weight) or weight < 0:
            return [], []
        ticker = position['ticker']
        info = sector_geo.get(ticker) or {}
        country = info.get('country') or 'Unknown'
        countries[country] = countries.get(country, 0) + weight
        weights = info.get('sector_weights') or {}
        weights = {name: value for name, value in weights.items()
                   if isinstance(value, (int, float)) and math.isfinite(value) and value > 0}
        total = sum(weights.values())
        shares = ({name: weight * value / total for name, value in weights.items()}
                  if total else {info.get('sector') or 'Unknown': weight})
        for name, share in shares.items():
            sectors[name] = sectors.get(name, 0) + share
            tickers.setdefault(name, set()).add(ticker)
    return ([(name, value, sorted(tickers[name])) for name, value in
             sorted(sectors.items(), key=lambda item: item[1], reverse=True)],
            sorted(countries.items(), key=lambda item: item[1], reverse=True))
