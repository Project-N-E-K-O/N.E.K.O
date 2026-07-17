from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _context_with_latency(latency_ms: int) -> dict:
    return {
        "state": {
            "config": {"dry_run": False},
            "live_connection": {"state": "connected", "connected": True},
            "safety": {"status": "running"},
            "speech_explanation": {
                "summary": "recently_spoke",
                "reason": "recent_output",
                "last_result_status": "pushed",
                "last_result_latency_ms": latency_ms,
            },
            "recent_results": [
                {
                    "status": "pushed",
                    "reason": "",
                    "response_latency_ms": latency_ms,
                    "pipeline_latency_ms": latency_ms,
                    "dispatcher_latency_ms": 120,
                }
            ],
        }
    }


def _context_with_latest_route_and_signal(*, latest_age_seconds: int = 12) -> dict:
    return {
        "state": {
            "config": {"dry_run": False, "live_mode": "solo_stream", "activity_level": "standard"},
            "live_connection": {"state": "connected", "connected": True},
            "live_status": {"summary": "ready_to_stream", "reason": "ready"},
            "live_state": {
                "state": "engaged",
                "reason": "recent_activity",
                "idle_hosting_candidate": False,
                "last_viewer_activity_age_sec": 42.0,
                "last_output_age_sec": 8.0,
                "engaged_threshold_seconds": 60.0,
                "idle_threshold_seconds": 120.0,
            },
            "idle_hosting_status": {
                "eligible": False,
                "reason": "not_candidate",
            },
            "active_engagement_status": {
                "eligible": False,
                "reason": "recent_danmaku_output",
                "min_interval_seconds": 120.0,
                "minimum_interval_remaining": 0.0,
                "recent_danmaku_cooldown_remaining": 24.0,
                "idle_hosting_wait_remaining": 4.5,
            },
            "live_director_status": {
                "next_auto_action": "idle_hosting",
                "eligible": False,
                "reason": "approaching_idle_hosting",
                "cooldown_remaining": 4.5,
            },
            "safety": {"status": "running"},
            "speech_explanation": {
                "summary": "recently_spoke",
                "reason": "recent_output",
                "last_result_status": "pushed",
            },
            "recent_results": [
                {
                    "status": "pushed",
                    "reason": "dispatcher_pushed",
                    "response_module": "danmaku_response",
                    "event_signal": "gift_signal",
                    "response_latency_ms": 3200,
                    "pipeline_latency_ms": 3200,
                    "dispatcher_latency_ms": 180,
                    "created_at": (datetime.now(timezone.utc) - timedelta(seconds=latest_age_seconds)).isoformat(),
                    "event": {
                        "source": "live_danmaku",
                        "danmaku_text": "猫猫今天怎么这么安静",
                        "topic_source": "bili_trending",
                        "topic_shape": "either_or",
                        "topic_title": "猫猫今天怎么这么安静",
                        "topic_key": "bili:BV_TEST",
                        "topic_family": "choice_vote",
                        "topic_hook": "Make the topic into one concrete A/B choice.",
                        "topic_pattern": "Turn the title into two concrete sides.",
                        "topic_intent": "quick_vote",
                        "topic_fun_axis": "choice",
                        "topic_pack": "micro_poll",
                        "topic_reply_affordance": "viewer can answer with one side",
                        "topic_recent_skip_reason": "single_viewer_flood",
                        "host_beat_key": "idle:soft_observation:quiet-room",
                        "host_beat_shape": "soft_observation",
                        "host_beat_family": "room_mood",
                        "host_beat_fun_axis": "mood",
                        "host_beat_idle_stage": "settle",
                        "host_beat_title": "\u5b89\u9759\u7684\u76f4\u64ad\u95f4\u6c14\u6c1b",
                        "host_beat_hint": "Say one soft concrete observation.",
                        "host_beat_reply_affordance": "viewer can answer with one mood word",
                    },
                }
            ],
            "recent_profiles": [{"uid": "1"}, {"uid": "2"}],
            "solo_test_readiness": {
                "summary": "ready_for_live_test",
                "profile_count": 2,
                "items": [
                    {"id": "preflight", "status": "ready", "reason": "ready"},
                    {"id": "test_isolation", "status": "warning", "reason": "viewer_profiles_present"},
                ],
            },
        }
    }


def _context_from_other_checkout() -> dict:
    context = _context_with_latest_route_and_signal()
    context["plugin"] = {
        "config_path": r"D:\Users\zheng\Documents\Code\other\N.E.K.O\plugin\plugins\neko_live\plugin.toml"
    }
    return context


def _solo_idle_context() -> dict:
    return {
        "state": {
            "config": {"dry_run": False, "live_mode": "solo_stream"},
            "live_connection": {"state": "connected", "connected": True},
            "live_status": {"summary": "ready_to_stream", "reason": "ready"},
            "live_state": {
                "state": "idle",
                "reason": "no_recent_activity",
                "idle_hosting_candidate": True,
            },
            "idle_hosting_status": {
                "eligible": True,
                "reason": "ready",
            },
            "safety": {"status": "running"},
            "speech_explanation": {
                "summary": "waiting_for_activity",
                "reason": "idle_hosting_candidate",
            },
            "recent_results": [],
        }
    }


def _solo_quiet_context(*, dry_run: bool = False) -> dict:
    return {
        "state": {
            "config": {"dry_run": dry_run, "live_mode": "solo_stream"},
            "live_connection": {"state": "connected", "connected": True},
            "live_status": {"summary": "ready_to_stream", "reason": "ready"},
            "live_state": {
                "state": "quiet",
                "reason": "quiet_activity_gap",
                "idle_hosting_candidate": False,
            },
            "idle_hosting_status": {
                "eligible": False,
                "reason": "not_candidate",
            },
            "safety": {"status": "running"},
            "speech_explanation": {
                "summary": "waiting_for_activity",
                "reason": "waiting_for_danmaku",
            },
            "recent_results": [],
        }
    }
