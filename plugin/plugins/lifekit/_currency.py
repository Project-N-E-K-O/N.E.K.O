"""Exchange-rate lookup — Frankfurter API (free, no key required).

https://frankfurter.dev/v1/
Data source: European Central Bank (ECB), updated each business day.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import httpx

_BASE = "https://api.frankfurter.dev/v1"
_TIMEOUT = 8.0

def currency_label(code: str) -> str:
    """Return the locale-neutral ISO currency code."""
    return code.upper()


async def convert(
    amount: float,
    from_currency: str,
    to_currency: str,
) -> Optional[Dict[str, Any]]:
    """Convert currency. Return {from, to, amount, result, rate, date} or None."""
    fr = from_currency.upper().strip()
    to = to_currency.upper().strip()
    if not fr or not to:
        return None
    if fr == to:
        return {"from": fr, "to": to, "amount": amount, "result": amount, "rate": 1.0, "date": ""}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.get(f"{_BASE}/latest", params={"base": fr, "symbols": to})
            if r.status_code != 200:
                return None
            data = r.json()
        rates = data.get("rates", {})
        rate = rates.get(to)
        if rate is None:
            return None
        result = round(float(amount) * float(rate), 2)
        return {
            "from": fr,
            "to": to,
            "amount": amount,
            "result": result,
            "rate": round(float(rate), 6),
            "date": data.get("date", ""),
        }
    except Exception:
        return None
