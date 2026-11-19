"""Price and fill math.

pump.fun bonding curves are constant-product over *virtual* reserves, so the
same x*y=k formula gives both price and the slippage a $50 order causes.
"""
from __future__ import annotations


def price_sol(v_sol: float, v_tokens: float) -> float:
    return v_sol / v_tokens if v_tokens > 0 else 0.0


def buy_tokens_out(v_sol: float, v_tokens: float, sol_in: float) -> float:
    """Tokens received for sol_in (already net of fees)."""
    if sol_in <= 0 or v_sol <= 0 or v_tokens <= 0:
        return 0.0
    return v_tokens * sol_in / (v_sol + sol_in)


def sell_sol_out(v_sol: float, v_tokens: float, tokens_in: float) -> float:
    """Gross SOL received for selling tokens_in (before fees)."""
    if tokens_in <= 0 or v_sol <= 0 or v_tokens <= 0:
        return 0.0
    return v_sol * tokens_in / (v_tokens + tokens_in)


def apply_fee(amount: float, bps: float) -> float:
    return amount * (1.0 - bps / 10_000.0)
