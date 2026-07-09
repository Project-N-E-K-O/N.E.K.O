"""Runtime compatibility helpers for live-status timing and age calculations."""

from __future__ import annotations

from typing import Any

from . import live_status as live_status_rules


class RuntimeLiveStatusHelperMixin:
    def _active_engagement_min_interval_seconds(self) -> float:
        return live_status_rules.active_engagement_min_interval_seconds(self.config)

    def _active_engagement_after_danmaku_interval_seconds(self) -> float:
        return live_status_rules.active_engagement_after_danmaku_interval_seconds(
            self.config
        )

    def _active_engagement_idle_grace_seconds(self) -> float:
        return live_status_rules.active_engagement_idle_grace_seconds(
            self.config,
            float(self._ACTIVE_ENGAGEMENT_IDLE_GRACE_SECONDS),
        )

    def _idle_hosting_wait_remaining_for_quiet_state(
        self, live_state: dict[str, Any]
    ) -> float | None:
        return live_status_rules.idle_hosting_wait_remaining_for_quiet_state(
            live_state,
            idle_threshold_fallback=self._live_state_threshold_seconds()[1],
        )

    def _idle_hosting_min_interval_seconds(self) -> float:
        return live_status_rules.idle_hosting_min_interval_seconds(self.config)

    def _solo_warmup_elapsed_seconds(self) -> float | None:
        if self._live_listener_started_at <= 0:
            return None
        return max(
            0.0, float(self._live_state_now()) - float(self._live_listener_started_at)
        )

    def _solo_warmup_timeout_seconds(self) -> float:
        return live_status_rules.solo_warmup_timeout_seconds(
            self.config,
            float(self._SOLO_WARMUP_TIMEOUT_SECONDS),
        )

    def _live_state_threshold_seconds(self) -> tuple[float, float]:
        return live_status_rules.live_state_threshold_seconds(
            self.config,
            float(self._LIVE_STATE_ENGAGED_SECONDS),
            float(self._LIVE_STATE_IDLE_SECONDS),
        )

    def _recent_live_danmaku_output_age_sec(self) -> float | None:
        return live_status_rules.recent_live_danmaku_output_age_sec(
            self.recent_results, self._iso_age_sec
        )

    def _recent_hosting_output_age_sec(self) -> float | None:
        return live_status_rules.recent_hosting_output_age_sec(
            self.recent_results, self._iso_age_sec
        )

    def _hosting_output_cooldown_seconds(self) -> float:
        return float(self._HOSTING_OUTPUT_COOLDOWN_SECONDS)

    def _last_viewer_activity_age_sec(self, rows: list[dict[str, Any]]) -> float | None:
        return live_status_rules.last_viewer_activity_age_sec(
            rows, self.recent_results, self._iso_age_sec
        )

    def _last_output_age_sec(self, rows: list[dict[str, Any]]) -> float | None:
        return live_status_rules.last_output_age_sec(
            rows, self.recent_results, self._iso_age_sec
        )

    def _recent_live_danmaku_event_age_sec(self) -> float | None:
        return live_status_rules.recent_live_danmaku_event_age_sec(
            self.recent_results, self._iso_age_sec
        )

    @staticmethod
    def _age_sec(timestamp: Any) -> float | None:
        return live_status_rules.age_sec(timestamp)

    @staticmethod
    def _iso_age_sec(value: Any) -> float | None:
        return live_status_rules.iso_age_sec(value)
