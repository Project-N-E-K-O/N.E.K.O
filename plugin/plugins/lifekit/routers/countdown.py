"""Countdown/anniversary router — pure computation with zero dependencies."""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from plugin.sdk.plugin import Err, Ok, SdkError, plugin_entry, quick_action, tr
from plugin.sdk.shared.core.router import PluginRouter

from .._chat import push_lifekit_content
from .._contracts import CountdownParams, DateDetailResult, DaysBetweenParams
from .._holiday import HolidayResolution, HolidayResolver, default_saved_country

_HOLIDAYS = HolidayResolver()


def _parse_numeric_date_info(
    text: str,
    *,
    reference_year: Optional[int] = None,
) -> tuple[Optional[date], bool]:
    """Parse YYYY-MM-DD or MM-DD and report whether a year was explicit."""
    t = text.strip().lower()

    # YYYY-MM-DD
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(t, fmt).date(), True
        except ValueError:
            continue

    # MM-DD (anchored to reference_year if provided, else next upcoming occurrence)
    for fmt in ("%m-%d", "%m/%d", "%m.%d"):
        try:
            parsed = datetime.strptime(t, fmt).date()
            if reference_year is not None:
                return date(reference_year, parsed.month, parsed.day), False
            today = date.today()
            target = date(today.year, parsed.month, parsed.day)
            if target < today:
                target = date(today.year + 1, parsed.month, parsed.day)
            return target, False
        except ValueError:
            continue

    return None, False


def _parse_numeric_date(
    text: str,
    *,
    reference_year: Optional[int] = None,
) -> Optional[date]:
    """Parse YYYY-MM-DD or MM-DD without applying regional assumptions."""
    parsed, _ = _parse_numeric_date_info(text, reference_year=reference_year)
    return parsed


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

        # Push the card
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
        start_stripped = start_date.strip()
        end_stripped = end_date.strip()

        start_has_year = True
        end_has_year = True

        if not start_stripped:
            d1: Optional[date] = today
        elif start_holiday.target is not None:
            d1 = start_holiday.target
        else:
            d1, start_has_year = _parse_numeric_date_info(start_date, reference_year=today.year)

        if not end_stripped:
            d2: Optional[date] = today
        elif end_holiday.target is not None:
            d2 = end_holiday.target
        else:
            d2, end_has_year = _parse_numeric_date_info(end_date, reference_year=today.year)

        if d1 is None:
            return Err(SdkError(i18n.t("date.invalid_start", value=start_date)))
        if d2 is None:
            return Err(SdkError(i18n.t("date.invalid_end", value=end_date)))

        if not start_has_year and end_has_year:
            d1 = date(d2.year, d1.month, d1.day)
            if d1 > d2 and end_stripped and start_holiday.target is None:
                d1 = date(d1.year - 1, d1.month, d1.day)
        elif not end_has_year and start_has_year:
            d2 = date(d1.year, d2.month, d2.day)
            if d2 < d1:
                d2 = date(d2.year + 1, d2.month, d2.day)
        elif not start_has_year and not end_has_year and d2 < d1:
            d2 = date(d2.year + 1, d2.month, d2.day)

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
