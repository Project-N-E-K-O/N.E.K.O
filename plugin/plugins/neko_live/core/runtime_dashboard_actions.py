"""Dashboard action projection for hosted-ui compatibility."""

from __future__ import annotations


def dashboard_actions() -> list[dict[str, str]]:
    action_ids = [
        "update_config",
        "pick_folder",
        "set_live_room",
        "lookup_live_room",
        "connect_live_room",
        "disconnect_live_room",
        "pause_roast",
        "resume_roast",
        "clear_queue",
        "trigger_idle_hosting",
        "trigger_warmup_hosting",
        "trigger_active_engagement",
        "submit_viewer_event",
        "clear_sandbox_data",
        "bili_login",
        "bili_login_check",
        "bili_login_status",
        "bili_logout",
        "douyin_cookie_import",
        "douyin_cookie_status",
        "douyin_cookie_validate",
        "douyin_cookie_delete",
    ]
    return [{"id": action_id, "entry_id": action_id} for action_id in action_ids]
