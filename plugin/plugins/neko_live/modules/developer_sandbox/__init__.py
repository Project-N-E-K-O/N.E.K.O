"""Developer sandbox input helpers."""

from __future__ import annotations

import re
from typing import Any

from ...core.contracts import ViewerEvent
from .._base import BaseModule

_UID_RE = re.compile(r"(?:space\.bilibili\.com/)?(\d+)")
_DEMO_TARGETS = {"__demo__", "demo", "neko_live_demo", "内置案例", "测试案例"}
_DEMO_UID = "9000000000000001"
_DEMO_NICKNAME = "粉桃猫猫观察员"
_DEMO_AVATAR_URL = "neko-live://fixtures/demo-avatar"
_DEMO_DANMAKU = "初见，猫猫可以锐评一下我的头像吗"


class DeveloperSandboxModule(BaseModule):
    id = "developer_sandbox"
    title = "开发者沙盒"

    def parse_target(
        self,
        *,
        target: str = "",
        uid: str = "",
        nickname: str = "",
        avatar_url: str = "",
        danmaku_text: str = "",
        target_lanlan: str = "",
        use_presets: bool = True,
        _ctx: Any = None,
        **extra: Any,
    ) -> ViewerEvent:
        target = str(target or "").strip()
        raw_uid = str(uid or "").strip()
        raw_nickname = str(nickname or "").strip()
        raw_avatar_url = str(avatar_url or "").strip()
        raw_danmaku_text = str(danmaku_text or "").strip()
        explicit_target_lanlan = str(target_lanlan or "").strip()
        raw: dict[str, Any] = {"target": target}
        if explicit_target_lanlan:
            raw["target_lanlan"] = explicit_target_lanlan
        if isinstance(_ctx, dict):
            raw["_ctx"] = _ctx
        if extra:
            raw["extra_keys"] = sorted(str(key) for key in extra)
        use_demo = target in _DEMO_TARGETS or (use_presets and not target and not raw_uid)
        if use_demo:
            return ViewerEvent(
                uid=raw_uid or _DEMO_UID,
                nickname=raw_nickname or _DEMO_NICKNAME,
                avatar_url=raw_avatar_url or _DEMO_AVATAR_URL,
                danmaku_text=raw_danmaku_text or _DEMO_DANMAKU,
                target_lanlan=explicit_target_lanlan,
                source="developer_sandbox",
                live_mode=self.ctx.config.live_mode if self.ctx else "co_stream",
                raw={**raw, "fixture": "demo_avatar"},
            )

        parsed_uid = raw_uid
        if not parsed_uid and target:
            match = _UID_RE.search(target)
            parsed_uid = match.group(1) if match else target
        return ViewerEvent(
            uid=parsed_uid,
            nickname=raw_nickname,
            avatar_url=raw_avatar_url,
            danmaku_text=raw_danmaku_text or (_DEMO_DANMAKU if use_presets else ""),
            target_lanlan=explicit_target_lanlan,
            source="developer_sandbox",
            live_mode=self.ctx.config.live_mode if self.ctx else "co_stream",
            raw=raw,
        )

    def status(self) -> dict[str, Any]:
        return {"enabled": bool(self.ctx and self.ctx.config.developer_tools_enabled)}
