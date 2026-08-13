"""汇率查询 — Frankfurter API (免费, 无需 key)。

https://frankfurter.dev/v1/
数据源: 欧洲央行 (ECB)，每个工作日更新。
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
    """汇率换算。返回 {from, to, amount, result, rate, date} 或 None。"""
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
