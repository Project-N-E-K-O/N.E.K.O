"""Runtime compatibility API for live-status projections."""

from __future__ import annotations

from typing import Any

from . import live_status as live_status_rules
from .runtime_live_status_helpers import RuntimeLiveStatusHelperMixin


class RuntimeLiveStatusApiMixin(RuntimeLiveStatusHelperMixin):
    def live_status_summary(
        self, live_connection: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        connection = live_connection or self.live_connection_snapshot()
        return live_status_rules.live_status_summary(
            config=self.config,
            live_connection=connection,
            safety_status=self.safety_guard.status(),
            cooldown_remaining=round(
                float(self.safety_guard.output_cooldown_remaining()), 1
            ),
            output_channel=self.dispatcher.output_channel_status(),
        )

    def live_state_summary(
        self,
        live_status: dict[str, Any] | None = None,
        health_rows: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        status = live_status or self.live_status_summary()
        rows = health_rows if health_rows is not None else self.runtime_health_rows()
        engaged_threshold, idle_threshold = self._live_state_threshold_seconds()
        return live_status_rules.live_state_summary(
            config=self.config,
            live_status=status,
            health_rows=rows,
            recent_results=self.recent_results,
            warmup_observed=self._has_recent_hosting_response(),
            warmup_elapsed=self._solo_warmup_elapsed_seconds(),
            engaged_threshold=engaged_threshold,
            idle_threshold=idle_threshold,
            warmup_timeout_seconds=self._solo_warmup_timeout_seconds(),
            iso_age_fn=self._iso_age_sec,
        )

    def idle_hosting_status(
        self, live_state: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        state = live_state or self.live_state_summary()
        return live_status_rules.idle_hosting_status(
            live_state=state,
            now=float(self._idle_hosting_now()),
            last_attempt_at=float(self._idle_hosting_last_attempt_at or 0.0),
            min_interval_seconds=self._idle_hosting_min_interval_seconds(),
            consecutive_failures=int(self._idle_hosting_consecutive_failures),
            failure_limit=self._IDLE_HOSTING_FAILURE_LIMIT,
            recent_hosting_output_age=self._recent_hosting_output_age_sec(),
            host_output_cooldown_seconds=self._hosting_output_cooldown_seconds(),
        )

    def active_engagement_status(
        self,
        live_status: dict[str, Any] | None = None,
        live_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        status = live_status or self.live_status_summary()
        state = live_state or self.live_state_summary(status)
        idle_takeover_streak = self._recent_actual_route_streak_since_viewer_activity(
            "idle_hosting"
        )
        return live_status_rules.active_engagement_status(
            config=self.config,
            live_status=status,
            live_state=state,
            now=float(self._active_engagement_now()),
            last_attempt_at=float(self._active_engagement_last_attempt_at or 0.0),
            min_interval_seconds=self._active_engagement_min_interval_seconds(),
            recent_danmaku_output_age=self._recent_live_danmaku_output_age_sec(),
            recent_danmaku_wait_seconds=self._active_engagement_after_danmaku_interval_seconds(),
            recent_hosting_output_age=self._recent_hosting_output_age_sec(),
            host_output_cooldown_seconds=self._hosting_output_cooldown_seconds(),
            idle_hosting_wait_remaining=self._idle_hosting_wait_remaining_for_quiet_state(
                state
            ),
            idle_grace_seconds=self._active_engagement_idle_grace_seconds(),
            idle_takeover_streak=(
                idle_takeover_streak
                if idle_takeover_streak >= self._IDLE_HOSTING_STREAK_FOR_ACTIVE_TAKEOVER
                else 0
            ),
        )

    def live_director_status(
        self,
        live_status: dict[str, Any] | None = None,
        live_state: dict[str, Any] | None = None,
        idle_hosting_status: dict[str, Any] | None = None,
        active_engagement_status: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        status = live_status or self.live_status_summary()
        state = live_state or self.live_state_summary(status)
        idle_status = idle_hosting_status or self.idle_hosting_status(state)
        active_status = active_engagement_status or self.active_engagement_status(
            status, state
        )
        return live_status_rules.live_director_status(
            config=self.config,
            live_status=status,
            live_state=state,
            idle_hosting_status=idle_status,
            active_engagement_status=active_status,
        )

    def solo_test_readiness(
        self,
        live_status: dict[str, Any] | None = None,
        live_state: dict[str, Any] | None = None,
        live_director_status: dict[str, Any] | None = None,
        profile_count: int = 0,
    ) -> dict[str, Any]:
        status = live_status or self.live_status_summary()
        state = live_state or self.live_state_summary(status)
        director = live_director_status or self.live_director_status(status, state)
        return live_status_rules.solo_test_readiness(
            config=self.config,
            live_status=status,
            live_state=state,
            live_director_status=director,
            profile_count=profile_count,
            warmup_observed=self._has_recent_response_module("warmup_hosting"),
        )

    def speech_explanation(
        self,
        live_status: dict[str, Any] | None = None,
        live_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        status = live_status or self.live_status_summary()
        state = live_state or self.live_state_summary(status)
        return live_status_rules.speech_explanation(
            live_status=status,
            live_state=state,
            latest_result=self.recent_results[-1] if self.recent_results else None,
            iso_age_fn=self._iso_age_sec,
        )
