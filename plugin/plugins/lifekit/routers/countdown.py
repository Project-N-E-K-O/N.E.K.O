"""倒计时/纪念日 router — 纯计算，零依赖。"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional

from plugin.sdk.plugin import plugin_entry, quick_action, Ok, Err, SdkError
from plugin.sdk.shared.core.router import PluginRouter

from .._chat import push_lifekit_content

# 常见节日/节气（公历固定日期）
_KNOWN_DATES: Dict[str, tuple[int, int]] = {
    "元旦": (1, 1), "new year": (1, 1),
    "情人节": (2, 14), "valentine": (2, 14),
    "妇女节": (3, 8), "women's day": (3, 8),
    "愚人节": (4, 1), "april fools": (4, 1),
    "劳动节": (5, 1), "labor day": (5, 1),
    "儿童节": (6, 1), "children's day": (6, 1),
    "国庆节": (10, 1), "national day": (10, 1),
    "万圣节": (10, 31), "halloween": (10, 31),
    "平安夜": (12, 24), "christmas eve": (12, 24),
    "圣诞节": (12, 25), "christmas": (12, 25),
    "跨年": (12, 31), "new year's eve": (12, 31),
}


def _parse_date(text: str) -> Optional[date]:
    """尝试解析日期字符串。支持 YYYY-MM-DD、MM-DD、自然语言节日名。"""
    t = text.strip().lower()

    # 已知节日
    if t in _KNOWN_DATES:
        m, d = _KNOWN_DATES[t]
        today = date.today()
        target = date(today.year, m, d)
        if target < today:
            target = date(today.year + 1, m, d)
        return target

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


class CountdownRouter(PluginRouter):
    """countdown + days_between entries。"""

    def __init__(self):
        super().__init__(name="countdown")

    @plugin_entry(
        id="countdown",
        name="倒计时",
        description=(
            "计算距离某个日期还有多少天。"
            "支持具体日期(2025-10-01)、月日(10-01)、节日名(圣诞节/国庆节/元旦)。"
            "适合回答「距离国庆还有几天」「离圣诞节还有多久」。"
        ),
        llm_result_fields=["summary", "detail"],
        input_schema={
            "type": "object",
            "properties": {
                "target_date": {
                    "type": "string",
                    "description": "目标日期：YYYY-MM-DD、MM-DD、或节日名（元旦/圣诞节/国庆节等）",
                },
                "label": {
                    "type": "string",
                    "description": "事件名称（如：生日、旅行、考试），留空自动识别",
                    "default": "",
                },
            },
            "required": ["target_date"],
        },
    )
    @quick_action(icon="⏳", priority=4)
    async def countdown(self, target_date: str = "", label: str = "", **_):
        if not target_date.strip():
            return Err(SdkError("请指定目标日期"))

        parsed = _parse_date(target_date)
        if parsed is None:
            return Err(SdkError(f"无法识别日期「{target_date}」，请用 YYYY-MM-DD 格式或节日名"))

        today = date.today()
        delta = (parsed - today).days
        event = label.strip() or target_date.strip()

        if delta > 0:
            summary = f"距离 {event} 还有 {delta} 天 ({parsed.isoformat()})"
            emoji = "⏳"
        elif delta == 0:
            summary = f"🎉 今天就是 {event}！"
            emoji = "🎉"
        else:
            summary = f"{event} 已经过去 {abs(delta)} 天 ({parsed.isoformat()})"
            emoji = "📅"

        weeks = abs(delta) // 7
        detail = {
            "target": parsed.isoformat(),
            "days": delta,
            "weeks": weeks,
            "weekday": ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][parsed.weekday()],
        }

        # 推送卡片
        blocks = [{"type": "text", "text": f"{emoji} {summary}"}]
        if abs(delta) > 7:
            blocks.append({"type": "text", "text": f"约 {weeks} 周 | {detail['weekday']}"})

        push_lifekit_content(self.main_plugin, blocks)

        return Ok({"summary": summary, "detail": detail})

    @plugin_entry(
        id="days_between",
        name="日期间隔",
        description=(
            "计算两个日期之间相隔多少天。"
            "适合回答「我们认识多少天了」「从某天到某天有多久」。"
        ),
        llm_result_fields=["summary", "detail"],
        input_schema={
            "type": "object",
            "properties": {
                "start_date": {
                    "type": "string",
                    "description": "起始日期 (YYYY-MM-DD)，留空表示今天",
                    "default": "",
                },
                "end_date": {
                    "type": "string",
                    "description": "结束日期 (YYYY-MM-DD)，留空表示今天",
                    "default": "",
                },
            },
        },
    )
    async def days_between(self, start_date: str = "", end_date: str = "", **_):
        today = date.today()
        d1 = _parse_date(start_date) if start_date.strip() else today
        d2 = _parse_date(end_date) if end_date.strip() else today

        if d1 is None:
            return Err(SdkError(f"无法识别起始日期「{start_date}」"))
        if d2 is None:
            return Err(SdkError(f"无法识别结束日期「{end_date}」"))

        delta = abs((d2 - d1).days)
        years = delta // 365
        months = (delta % 365) // 30
        weeks = delta // 7

        summary = f"{d1.isoformat()} → {d2.isoformat()}：共 {delta} 天"
        detail = {"start": d1.isoformat(), "end": d2.isoformat(), "days": delta, "weeks": weeks, "years": years, "months_approx": months}

        parts = []
        if years > 0:
            parts.append(f"{years} 年")
        if months > 0:
            parts.append(f"{months} 个月")
        parts.append(f"{delta} 天")

        push_lifekit_content(self.main_plugin, [
            {"type": "text", "text": f"📅 {d1} → {d2}"},
            {"type": "text", "text": " | ".join(parts)},
        ])

        return Ok({"summary": summary, "detail": detail})
