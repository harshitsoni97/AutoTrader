"""Dry-run slippage model.

Paper trading that assumes a perfect fill at the plan price systematically
inflates returns. This applies a realistic adverse fill:

    effective_bps = half_spread_bps + impact_bps_per_lakh × (notional / 1e5)
    buy_fill  = price × (1 + effective_bps/1e4)     # you pay up
    sell_fill = price × (1 - effective_bps/1e4)     # you give up

Both the half-spread (a fixed cost crossing the book) and a size-dependent
market-impact term are configurable in trading_policy.yaml. Impact grows with
order notional, so larger orders in the same name cost more — the effect the
reviewer flagged for mid-cap Indian equities.
"""

from __future__ import annotations


def slipped_fill(
    price: float,
    qty: int,
    side: str,
    half_spread_bps: float,
    impact_bps_per_lakh: float,
) -> tuple[float, float]:
    """Return (fill_price, slippage_per_share) for an adverse dry-run fill.

    side: "BUY" fills above price, "SELL" fills below.
    """
    if not price or price <= 0 or not qty:
        return price, 0.0
    notional = price * qty
    effective_bps = half_spread_bps + impact_bps_per_lakh * (notional / 1e5)
    frac = effective_bps / 1e4
    if side.upper() == "BUY":
        fill = price * (1 + frac)
    else:
        fill = price * (1 - frac)
    fill = round(fill, 2)
    return fill, round(abs(fill - price), 4)
