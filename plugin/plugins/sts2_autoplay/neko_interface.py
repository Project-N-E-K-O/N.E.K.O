from __future__ import annotations

from typing import Any


class STS2NekoInterface:
    def __init__(self, service: Any) -> None:
        self._service = service

    def t(self, key: str, *, default: str = "", **params: Any) -> str:
        return self._service.t(key, default=default, **params)

    async def get_status(self) -> dict[str, Any]:
        return await self._service.get_status()

    async def get_snapshot(self) -> dict[str, Any]:
        return await self.get_readout()

    async def get_summary(self) -> dict[str, Any]:
        readout = await self.get_readout()
        return {
            "status": readout.get("status", "ok"),
            "message": readout.get("summary") or readout.get("message") or self.t("neko.summary.empty", default="当前暂无可用局势摘要。"),
            "summary": readout.get("summary") or readout.get("message") or self.t("neko.summary.empty", default="当前暂无可用局势摘要。"),
            "situation_summary": dict(readout.get("situation_summary") if isinstance(readout.get("situation_summary"), dict) else {}),
        }

    async def get_catgirl_sync(self) -> dict[str, Any]:
        readout = await self.get_readout()
        catgirl_sync = readout.get("catgirl_sync") if isinstance(readout.get("catgirl_sync"), dict) else {}
        message = self.t("neko.sync.available", default="已生成猫娘同步包。") if catgirl_sync else self.t("neko.sync.empty", default="当前暂无猫娘同步包。")
        return {
            "status": readout.get("status", "ok"),
            "message": message,
            "summary": message,
            "catgirl_sync": catgirl_sync,
        }

    async def get_readout(self) -> dict[str, Any]:
        snapshot_result = await self._service.get_snapshot()
        snapshot = snapshot_result.get("snapshot") if isinstance(snapshot_result.get("snapshot"), dict) else {}
        situation_summary = snapshot.get("situation_summary") if isinstance(snapshot.get("situation_summary"), dict) else {}
        catgirl_sync = snapshot.get("catgirl_sync") if isinstance(snapshot.get("catgirl_sync"), dict) else {}
        summary_text = str(situation_summary.get("text") or "")
        message = summary_text or self.t("neko.summary.empty", default="当前暂无可用局势摘要。")
        return {
            "status": "ok",
            "message": message,
            "summary": message,
            "snapshot": snapshot,
            "situation_summary": situation_summary,
            "catgirl_sync": catgirl_sync,
        }

    async def set_standby(self, standby: bool) -> dict[str, Any]:
        mode_info = self._service.set_standby(standby)
        await self._service.refresh_state(trigger_sync=True)
        mode_label = "standby" if self._service._state.standby else "active"
        message = self.t("neko.standby.changed", default="已切换到 {mode_label} 模式。", mode_label=mode_label)
        return {
            "status": "ok",
            "message": message,
            "summary": message,
            "standby": self._service._state.standby,
            "mode": mode_info,
        }

    async def set_companion_mode(self, enabled: bool) -> dict[str, Any]:
        result = self._service.set_companion_mode(enabled)
        if enabled:
            await self._service.refresh_state(trigger_sync=True)
        return result

    async def extract_and_upsert_preference(self, instruction: str, *, source: str = "user") -> dict[str, Any]:
        snapshot = await self._current_snapshot()
        context = self._build_preference_context(snapshot)
        extracted = self._service._preference_extractor.extract(instruction, context=context)
        if extracted is None:
            message = self.t("neko.preference.extract_none", default="当前指令未识别为可结构化保存的偏好。")
            return {"status": "idle", "message": message, "summary": message}
        record = self._service._preference_store.upsert(
            extracted["domain"],
            extracted["key"],
            extracted["value"],
            source=source,
        )
        message = self.t(
            "neko.preference.extracted",
            default="已提取并更新偏好：{pref_domain}/{pref_key}。",
            pref_domain=extracted["domain"],
            pref_key=extracted["key"],
        )
        return {
            "status": "ok",
            "message": message,
            "summary": message,
            "record": record,
            "extracted": extracted,
        }

    async def get_strategy_context(self) -> dict[str, Any]:
        snapshot = await self._current_snapshot()
        strategy_context = snapshot.get("strategy_context") if isinstance(snapshot.get("strategy_context"), dict) else {}
        strategy_name = strategy_context.get("strategy_name") or "unknown"
        message = self.t("neko.strategy_context", default="当前策略上下文：{strategy_name}。", strategy_name=strategy_name)
        return {
            "status": "ok",
            "message": message,
            "summary": message,
            "strategy_context": strategy_context,
        }

    async def get_planned_operation(self) -> dict[str, Any]:
        snapshot = await self._current_snapshot()
        planned_operation = snapshot.get("planned_operation") if isinstance(snapshot.get("planned_operation"), dict) else None
        if planned_operation is None:
            message = self.t("neko.planned_operation.empty", default="当前暂无规划动作。")
            return {"status": "idle", "message": message, "summary": message}
        current = self.t("neko.planned_operation.current", default="当前规划动作：{action_type}。", action_type=planned_operation.get("action_type", "unknown"))
        return {
            "status": "ok",
            "message": current,
            "summary": current,
            "planned_operation": planned_operation,
        }

    async def execute_planned_operation(self) -> dict[str, Any]:
        return await self._service.execute_planned_operation()

    async def start_autoplay(self) -> dict[str, Any]:
        return self._service.start_autoplay()

    async def pause_autoplay(self, reason: str = "user") -> dict[str, Any]:
        return self._service.pause_autoplay(reason=reason)

    async def resume_autoplay(self) -> dict[str, Any]:
        return self._service.resume_autoplay()

    async def stop_autoplay(self, reason: str = "manual") -> dict[str, Any]:
        return self._service.stop_autoplay(reason=reason)

    async def _current_snapshot(self) -> dict[str, Any]:
        status = await self._service.get_snapshot()
        snapshot = status.get("snapshot") if isinstance(status.get("snapshot"), dict) else {}
        return snapshot

    def _build_preference_context(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        summary_context = snapshot.get("summary_context") if isinstance(snapshot.get("summary_context"), dict) else {}
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        return {
            "screen": snapshot.get("screen"),
            "floor": snapshot.get("floor"),
            "payload": payload,
            "event_id": payload.get("event_id"),
        }


__all__ = ["STS2NekoInterface"]
