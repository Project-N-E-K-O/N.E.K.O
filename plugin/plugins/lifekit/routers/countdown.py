"""倒计时/纪念日 router — 纯计算，零依赖。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from plugin.sdk.plugin import Err, Ok, SdkError, plugin_entry, quick_action, tr
from plugin.sdk.shared.core.router import PluginRouter

from .._chat import push_lifekit_content
from .._contracts import CountdownParams, DateDetailResult, DaysBetweenParams
from .._holiday import HolidayResolution, HolidayResolver, default_saved_country

_HOLIDAYS = HolidayResolver()


def _parse_numeric_date(text: str) -> Optional[date]:
    """Parse YYYY-MM-DD or MM-DD without applying regional assumptions."""
    t = text.strip().lower()

    # YYYY-MM-DD
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            continue

    # MM-DD (当年或下一年)
    for fmt in ("%m-%d", "%m/%d", "%m.%d"):
        try:
            parsed = datetime.strptime(t, fmt).date()
            today = date.today()
            target = date(today.year, parsed.month, parsed.day)
            if target < today:
                target = date(today.year + 1, parsed.month, parsed.day)
            return target
        except ValueError:
            continue

    return None


def _holiday_note(i18n, event: str, resolution: HolidayResolution) -> str:
    if not resolution.assumed_country:
        return event
    if not resolution.alternatives:
        return f"{event} ({resolution.assumed_country})"
    alternatives = ", ".join(
        f"{item.country} {item.target.isoformat()}"
        for item in resolution.alternatives
    )
    return i18n.t(
        "date.holiday_alternatives",
        event=event,
        country=resolution.assumed_country,
        alternatives=alternatives,
    )


class CountdownRouter(PluginRouter):
    """countdown + days_between entries。"""

    def __init__(self):
        super().__init__(name="countdown")

    @plugin_entry(
        id="countdown",
        name=tr("entries.countdown.name", default="Countdown"),
        description=tr("entries.countdown.description", default="Count days to a date, month-day, or supported holiday name."),
        params=CountdownParams,
        llm_result_model=DateDetailResult,
    )
    @quick_action(icon="⏳", priority=4)
    async def countdown(
        self,
        params: CountdownParams | None = None,
        target_date: str = "",
        label: str = "",
        country_hint: str = "",
        **_,
    ):
        if params is not None:
            target_date = params.target_date
            label = params.label
            country_hint = params.country_hint

        plugin = self.main_plugin
        plugin._resolve_locale()
        i18n = plugin._i18n

        if not target_date.strip():
            return Err(SdkError(i18n.t("date.target_required")))

        holiday = _HOLIDAYS.resolve(
            target_date,
            country_hint=country_hint,
        )
        if holiday.alternatives and not country_hint:
            default_country = await default_saved_country(plugin)
            if default_country:
                holiday = _HOLIDAYS.resolve(
                    target_date,
                    country_hint=default_country,
                )
        parsed = holiday.target or _parse_numeric_date(target_date)
        if parsed is None:
            return Err(SdkError(i18n.t("date.invalid", value=target_date)))

        today = date.today()
        delta = (parsed - today).days
        event = label.strip() or target_date.strip()
        event = _holiday_note(i18n, event, holiday)

        if delta > 0:
            summary = i18n.t("date.future", event=event, days=delta, date=parsed.isoformat())
            emoji = "⏳"
        elif delta == 0:
            summary = i18n.t("date.today", event=event)
            emoji = "🎉"
        else:
            summary = i18n.t("date.past", event=event, days=abs(delta), date=parsed.isoformat())
            emoji = "📅"

        weeks = abs(delta) // 7
        weekdays = i18n.value("date.weekdays")
        if not isinstance(weekdays, list) or len(weekdays) != 7:
            weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        detail = {
            "target": parsed.isoformat(),
            "days": delta,
            "weeks": weeks,
            "weekday": weekdays[parsed.weekday()],
            "assumed_country": holiday.assumed_country,
            "holiday_alternatives": [
                item.as_dict() for item in holiday.alternatives
            ],
        }

        # 推送卡片
        blocks = [{"type": "text", "text": f"{emoji} {summary}"}]
        if abs(delta) > 7:
            blocks.append({"type": "text", "text": i18n.t("date.weeks", weeks=weeks, weekday=detail["weekday"])})

        push_lifekit_content(self.main_plugin, blocks)

        return Ok({"summary": summary, "detail": detail})

    @plugin_entry(
        id="days_between",
        name=tr("entries.daysBetween.name", default="Days between dates"),
        description=tr("entries.daysBetween.description", default="Calculate the number of days between two dates."),
        params=DaysBetweenParams,
        llm_result_model=DateDetailResult,
    )
    async def days_between(
        self,
        params: DaysBetweenParams | None = None,
        start_date: str = "",
        end_date: str = "",
        country_hint: str = "",
        **_,
    ):
        if params is not None:
            start_date = params.start_date
            end_date = params.end_date
            country_hint = params.country_hint

        plugin = self.main_plugin
        plugin._resolve_locale()
        i18n = plugin._i18n

        today = date.today()
        start_holiday = _HOLIDAYS.resolve(
            start_date,
            country_hint=country_hint,
        )
        end_holiday = _HOLIDAYS.resolve(
            end_date,
            country_hint=country_hint,
        )
        if (
            not country_hint
            and (start_holiday.alternatives or end_holiday.alternatives)
        ):
            default_country = await default_saved_country(plugin)
            if default_country:
                start_holiday = _HOLIDAYS.resolve(
                    start_date,
                    country_hint=default_country,
                )
                end_holiday = _HOLIDAYS.resolve(
                    end_date,
                    country_hint=default_country,
                )
        d1 = (
            start_holiday.target or _parse_numeric_date(start_date)
            if start_date.strip()
            else today
        )
        d2 = (
            end_holiday.target or _parse_numeric_date(end_date)
            if end_date.strip()
            else today
        )

        if d1 is None:
            return Err(SdkError(i18n.t("date.invalid_start", value=start_date)))
        if d2 is None:
            return Err(SdkError(i18n.t("date.invalid_end", value=end_date)))

        delta = abs((d2 - d1).days)
        years = delta // 365
        months = (delta % 365) // 30
        weeks = delta // 7

        summary = i18n.t("date.between", start=d1.isoformat(), end=d2.isoformat(), days=delta)
        holiday_notes = [
            _holiday_note(i18n, text, resolution)
            for text, resolution in (
                (start_date, start_holiday),
                (end_date, end_holiday),
            )
            if resolution.assumed_country
        ]
        if holiday_notes:
            summary = f"{summary} | {'; '.join(holiday_notes)}"
        holiday_alternatives = [
            item.as_dict()
            for resolution in (start_holiday, end_holiday)
            for item in resolution.alternatives
        ]
        detail = {
            "start": d1.isoformat(),
            "end": d2.isoformat(),
            "days": delta,
            "weeks": weeks,
            "years": years,
            "months_approx": months,
            "assumed_country": (
                start_holiday.assumed_country or end_holiday.assumed_country
            ),
            "holiday_alternatives": holiday_alternatives,
        }

        parts = []
        if years > 0:
            parts.append(i18n.t("date.years", value=years))
        if months > 0:
            parts.append(i18n.t("date.months", value=months))
        parts.append(i18n.t("date.days", value=delta))

        push_lifekit_content(self.main_plugin, [
            {"type": "text", "text": f"📅 {d1} → {d2}"},
            {"type": "text", "text": " | ".join(parts)},
        ])

        return Ok({"summary": summary, "detail": detail})
