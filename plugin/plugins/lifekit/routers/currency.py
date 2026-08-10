"""汇率换算 router — Frankfurter API (ECB 数据源)。"""

from __future__ import annotations

from plugin.sdk.plugin import Err, Ok, SdkError, plugin_entry, quick_action, tr
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
        name=tr("entries.currencyConvert.name", default="Convert currency"),
        description=tr("entries.currencyConvert.description", default="Convert major currencies using European Central Bank reference rates."),
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

        plugin = self.main_plugin
        plugin._resolve_locale()
        i18n = plugin._i18n

        if not from_currency.strip() or not to_currency.strip():
            return Err(SdkError(i18n.t("currency.no_currencies")))

        result = await currency_api.convert(
            amount=float(amount),
            from_currency=from_currency,
            to_currency=to_currency,
        )

        if result is None:
            return Err(SdkError(i18n.t(
                "currency.convert_failed",
                **{"from": from_currency.upper(), "to": to_currency.upper()},
            )))

        fr_label = currency_api.currency_label(result["from"])
        to_label = currency_api.currency_label(result["to"])

        summary = f"{result['amount']} {fr_label} = {result['result']} {to_label}"
        if result.get("date"):
            summary += i18n.t(
                "runtime.currency_rate_suffix",
                rate=result["rate"],
                date=result["date"],
            )

        # 推送卡片
        blocks = [
            {"type": "text", "text": f"💱 {result['amount']} {fr_label} → {result['result']} {to_label}"},
        ]
        if result.get("rate") and result["rate"] != 1.0:
            blocks.append({"type": "text", "text": i18n.t(
                "runtime.currency_rate_card",
                from_currency=result["from"],
                rate=result["rate"],
                to_currency=result["to"],
                date=result.get("date", ""),
            )})

        push_lifekit_content(self.main_plugin, blocks)

        return Ok({
            "summary": summary,
            "conversion": result,
            "next_actions": ["trip_advice", "get_weather"],
        })
