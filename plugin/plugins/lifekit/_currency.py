"""汇率查询 — Frankfurter API (免费, 无需 key)。

https://www.frankfurter.app/docs/
数据源: 欧洲央行 (ECB)，每个工作日更新。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

_BASE = "https://api.frankfurter.app"
_TIMEOUT = 8.0

# 常见货币的中文名
CURRENCY_NAMES: Dict[str, str] = {
    "CNY": "人民币", "USD": "美元", "EUR": "欧元", "JPY": "日元",
    "GBP": "英镑", "KRW": "韩元", "HKD": "港币", "TWD": "新台币",
    "SGD": "新加坡元", "AUD": "澳元", "CAD": "加元", "CHF": "瑞士法郎",
    "THB": "泰铢", "MYR": "马来西亚林吉特", "INR": "印度卢比",
    "RUB": "俄罗斯卢布", "BRL": "巴西雷亚尔", "SEK": "瑞典克朗",
    "NOK": "挪威克朗", "DKK": "丹麦克朗", "NZD": "新西兰元",
    "ZAR": "南非兰特", "MXN": "墨西哥比索", "PHP": "菲律宾比索",
    "IDR": "印尼盾", "CZK": "捷克克朗", "PLN": "波兰兹罗提",
    "HUF": "匈牙利福林", "TRY": "土耳其里拉", "ILS": "以色列谢克尔",
    "BGN": "保加利亚列弗", "RON": "罗马尼亚列伊", "ISK": "冰岛克朗",
}


def currency_label(code: str) -> str:
    """返回 '美元(USD)' 格式的标签。"""
    name = CURRENCY_NAMES.get(code.upper(), "")
    return f"{name}({code.upper()})" if name else code.upper()


async def convert(
    amount: float,
    from_currency: str,
    to_currency: str,
) -> Optional[Dict[str, Any]]:
    """汇率换算。返回 {from, to, amount, result, rate, date} 或 None。"""
    fr = from_currency.upper().strip()
    to = to_currency.upper().strip()
    if not fr or not to or fr == to:
        return {"from": fr, "to": to, "amount": amount, "result": amount, "rate": 1.0, "date": ""}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.get(f"{_BASE}/latest", params={"from": fr, "to": to, "amount": amount})
            if r.status_code != 200:
                return None
            data = r.json()
        rates = data.get("rates", {})
        result = rates.get(to)
        if result is None:
            return None
        return {
            "from": fr,
            "to": to,
            "amount": amount,
            "result": round(float(result), 2),
            "rate": round(float(result) / amount, 6) if amount else 0,
            "date": data.get("date", ""),
        }
    except Exception:
        return None


async def list_currencies() -> List[Dict[str, str]]:
    """获取所有支持的货币列表。"""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
            r = await c.get(f"{_BASE}/currencies")
            if r.status_code != 200:
                return []
            data = r.json()
        return [{"code": k, "name": v} for k, v in sorted(data.items())]
    except Exception:
        return []
