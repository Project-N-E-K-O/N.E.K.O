"""汇率换算 router — Frankfurter API (ECB 数据源)。"""

from __future__ import annotations

from typing import Any, Dict

from plugin.sdk.plugin import plugin_entry, quick_action, Ok, Err, SdkError
from plugin.sdk.shared.core.router import PluginRouter

from .. import _currency as currency_api
from .._chat import push_lifekit_content
from .._contracts import CurrencyConvertParams, CurrencyConvertResult


class CurrencyRouter(PluginRouter):
    """currency_convert entry：汇率换算。"""

    def __init__(self):
        super().__init__(name="currency")

    @plugin_entry(
        id="currency_convert",
        name="汇率换算",
        description=(
            "实时汇率换算，支持全球主要货币。数据来源：欧洲央行。"
            "适合回答「100美元多少人民币」「日元兑欧元汇率」。"
            "出国旅行时可配合 trip_advice 使用。"
        ),
        params=CurrencyConvertParams,
        llm_result_model=CurrencyConvertResult,
    )
    @quick_action(icon="💱", priority=5)
    async def currency_convert(
        self, params: CurrencyConvertParams | None = None, amount: float = 1,
        from_currency: str = "", to_currency: str = "", **_,
    ):
        if params is not None:
            amount = params.amount
            from_currency = params.from_currency
            to_currency = params.to_currency

        if not from_currency.strip() or not to_currency.strip():
            return Err(SdkError("请指定源货币和目标货币（如 USD → CNY）"))

        result = await currency_api.convert(
            amount=float(amount),
            from_currency=from_currency,
            to_currency=to_currency,
        )

        if result is None:
            return Err(SdkError(f"汇率查询失败：{from_currency.upper()} → {to_currency.upper()}，请检查货币代码"))

        fr_label = currency_api.currency_label(result["from"])
        to_label = currency_api.currency_label(result["to"])

        summary = f"{result['amount']} {fr_label} = {result['result']} {to_label}"
        if result.get("date"):
            summary += f" (汇率 {result['rate']}，{result['date']})"

        # 推送卡片
        blocks = [
            {"type": "text", "text": f"💱 {result['amount']} {fr_label} → {result['result']} {to_label}"},
        ]
        if result.get("rate") and result["rate"] != 1.0:
            blocks.append({"type": "text", "text": f"汇率: 1 {result['from']} = {result['rate']} {result['to']}  ({result.get('date', '')})"})

        push_lifekit_content(self.main_plugin, blocks)

        return Ok({
            "summary": summary,
            "conversion": result,
            "next_actions": ["trip_advice — 出行规划", "get_weather — 目的地天气"],
        })
