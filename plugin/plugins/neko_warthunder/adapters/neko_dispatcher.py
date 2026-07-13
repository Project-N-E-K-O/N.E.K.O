"""唯一 NEKO 输出边界（D-B4）。

所有开口只走这里：把 BattleEvent 拼成"事实行 + 要求行"prompt（带 {MASTER_NAME} 占位符，
宿主按会话展开），普通事件经 push_message(visibility=[], ai_behavior="respond") 交给猫娘 LLM 润色并触发语音；
显式开启插件直出时，事件可用短句 push_message(visibility=["chat"], ai_behavior="blind") 降低延迟，但这只进聊天气泡。
dry_run 时短路、绝不真投。常驻场景上下文走 push_context(ai_behavior="read")。
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from typing import Any

from ..core.contracts import BattleEvent
from .runtime_timeline import RuntimeTimeline
from .text_safety import sanitize_event_payload

BATTLE_EVENT_COALESCE_KEY = "neko_warthunder:battle_event"
BATTLE_REPLY_CONTRACT = "short_tts_line"
BATTLE_REPLY_MAX_CHARS = 28
BATTLE_RESPONSE_MODULE_HINT = "war_thunder_battle_event"
HOST_CALLBACK_CONTRACT_VERSION = "neko.callback.v1"
HOST_CALLBACK_KIND = "realtime_cue"
HOST_REPLY_STYLE = "short_line"
HOST_QUIET_WINDOW_POLICY = "suppress_non_urgent_during_user_input"
V2_LIVE_EVIDENCE_GATED_EVENTS = frozenset({"enemy_on_six", "tailing_risk", "ground_target_nearby"})
FREE_TEXT_DRY_RUN_ONLY_EVENTS = frozenset({"free_text_activity"})
BACKPRESSURE_BYPASS_EVENTS = frozenset({"you_died", "you_killed"})
URGENT_REPLACE_EVENTS = frozenset({"you_died", "stall_risk", "high_aoa", "over_g", "low_alt_danger", "overspeed"})
FLEX_STYLE_EVENTS = frozenset(
    {
        "spawn",
        "you_killed",
        "you_died",
        "overheat",
        "low_fuel",
        "ground_laser_warning",
        "ground_crew_loss",
        "ground_gunner_disabled",
        "ground_driver_disabled",
        "ground_ammo_empty",
        "ground_ammo_low",
        "player_radio_command",
        "enemy_nearby",
        "ground_target_nearby",
        "battle_end",
    }
)
PLUGIN_OWNED_DIRECT_EVENTS = frozenset(
    {
        "stall_risk",
        "high_aoa",
        "over_g",
        "low_alt_danger",
        "overspeed",
        "overheat",
        "low_fuel",
        "ground_laser_warning",
        "ground_crew_loss",
        "ground_gunner_disabled",
        "ground_driver_disabled",
        "ground_ammo_empty",
        "ground_ammo_low",
        "ground_target_nearby",
        "enemy_nearby",
        "air_threat_nearby",
        "enemy_on_six",
        "tailing_risk",
        "you_killed",
        "you_died",
        "battle_end",
    }
)
REPEAT_COLLAPSE_EVENT_IDS = frozenset(
    {
        "stall_risk",
        "high_aoa",
        "over_g",
        "low_alt_danger",
        "overspeed",
        "overheat",
        "low_fuel",
        "ground_laser_warning",
        "ground_crew_loss",
        "ground_gunner_disabled",
        "ground_driver_disabled",
        "ground_ammo_empty",
        "ground_ammo_low",
        "ground_target_nearby",
        "enemy_nearby",
        "air_threat_nearby",
        "enemy_on_six",
        "tailing_risk",
    }
)
REPEAT_COLLAPSE_SECONDS = 30.0
EVENT_MAX_AGE_OVERRIDES_SECONDS: dict[str, float] = {
    # These cues are useful only while the condition is still tactically fresh.
    "spawn": 3.0,
    "enemy_nearby": 3.0,
    "air_threat_nearby": 3.0,
    "enemy_on_six": 3.0,
    "tailing_risk": 3.0,
    "ground_target_nearby": 4.0,
    "low_alt_danger": 4.0,
    "overspeed": 4.0,
    "high_aoa": 4.0,
    "over_g": 4.0,
    "stall_risk": 5.0,
    "overheat": 6.0,
    "ground_laser_warning": 4.0,
    "ground_crew_loss": 6.0,
    "ground_gunner_disabled": 6.0,
    "ground_driver_disabled": 6.0,
    "ground_ammo_empty": 8.0,
    "ground_ammo_low": 8.0,
    "player_radio_command": 10.0,
    # Kill/death/battle-end can tolerate a little more host latency, but still
    # should not be replayed as old news.
    "you_killed": 30.0,
    "you_died": 8.0,
    "battle_end": 8.0,
}
COPILOT_ROLE_BOUNDARY = (
    "边界：只提醒陪伴；不接管、不编锁定/开火/战果/损伤。"
)

# 每个事件的"要求行"意图（不写最终台词，台词归角色 LLM）。
_INTENT: dict[str, str] = {
    "stall_risk": "濒临失速，提醒 {MASTER_NAME} 加速/松杆改出",
    "high_aoa": "攻角过大，提醒 {MASTER_NAME} 松杆改出，别继续硬拉",
    "over_g": "过载过大，提醒 {MASTER_NAME} 松杆/回正，别继续硬拉",
    "low_alt_danger": "离地太近还在下沉，提醒 {MASTER_NAME} 立刻拉起",
    "overspeed": "速度过头，提醒 {MASTER_NAME} 收油门改出，别硬拉",
    "overheat": "发动机温度高，提醒 {MASTER_NAME} 收油门散热",
    "low_fuel": "油不多了，提醒 {MASTER_NAME} 留油返航",
    "ground_laser_warning": "陆战激光告警，提醒 {MASTER_NAME} 可能被测距或锁定，短促说一句找掩体或动一下",
    "ground_crew_loss": "陆战乘员损失，提醒 {MASTER_NAME} 车组受损，短促说一句收住、找掩体或别贪",
    "ground_gunner_disabled": "陆战炮手失能，提醒 {MASTER_NAME} 暂时别硬拼输出，短促说一句先缩回去",
    "ground_driver_disabled": "陆战驾驶员失能，提醒 {MASTER_NAME} 机动受限，短促说一句先找掩体",
    "ground_ammo_empty": "陆战一级弹药打空，提醒 {MASTER_NAME} 装填会变慢，短促说一句别硬拼",
    "ground_ammo_low": "陆战一级弹药偏少，提醒 {MASTER_NAME} 后续装填会慢，短促说一句规划节奏",
    "ground_target_nearby": "报任务目标点接近，提醒 {MASTER_NAME} 看方位",
    "enemy_nearby": "报附近接触，提醒 {MASTER_NAME} 保持观察",
    "air_threat_nearby": "报空中威胁方位，提醒 {MASTER_NAME} 抬头确认",
    "enemy_on_six": "报后方威胁，提醒 {MASTER_NAME} 别让对面贴住",
    "tailing_risk": "报后方持续贴近，提醒 {MASTER_NAME} 立刻改出",
    "free_text_activity": "提醒 {MASTER_NAME} 检测到战场文字来源，只做安全泛化提示，不复读原文",
    "player_radio_command": "听到 {MASTER_NAME} 发出的固定无线电口令；只按标准化口令短回应，不引用聊天原文",
    "you_killed": "确认刚才战果；按载具域一句短话，可不夸，不套固定话",
    "you_died": "按事实安抚 {MASTER_NAME}，准备重整",
    "spawn": (
        "跟 {MASTER_NAME} 短促开局招呼；"
        "按空/陆/海载具域寒暄打气，可活泼即兴，"
        "别报敌情/方位/锁定/击杀/威胁"
    ),
    "battle_end": "这局结束，给 {MASTER_NAME} 收尾一句，不展开战报",
}

_RECOVERY_INTENT = "刚才的危险解除了，跟 {MASTER_NAME} 说句'好险、稳住了'之类的"


def _output_backpressure_seconds(plugin: Any) -> float:
    cfg = getattr(plugin, "cfg", None)
    try:
        return max(0.0, float(getattr(cfg, "output_backpressure_seconds", 20.0)))
    except (TypeError, ValueError):
        return 20.0


def _output_event_max_age_seconds(plugin: Any, event: BattleEvent | None = None) -> float:
    cfg = getattr(plugin, "cfg", None)
    try:
        configured = max(0.0, float(getattr(cfg, "output_event_max_age_seconds", 8.0)))
    except (TypeError, ValueError):
        configured = 8.0
    if event is None or configured <= 0:
        return configured
    override = EVENT_MAX_AGE_OVERRIDES_SECONDS.get(event.event_id)
    if override is None:
        return configured
    if event.event_id == "you_killed":
        return max(configured, override)
    return min(configured, override)


def _user_chat_quiet_window_seconds(plugin: Any) -> float:
    cfg = getattr(plugin, "cfg", None)
    try:
        return max(0.0, float(getattr(cfg, "user_chat_quiet_window_seconds", 20.0)))
    except (TypeError, ValueError):
        return 20.0


def _battle_output_quiet_window_seconds(plugin: Any) -> float:
    cfg = getattr(plugin, "cfg", None)
    try:
        return max(0.0, float(getattr(cfg, "battle_output_quiet_window_seconds", 20.0)))
    except (TypeError, ValueError):
        return 20.0


def _dialogue_intrusion_mode(plugin: Any) -> str:
    cfg = getattr(plugin, "cfg", None)
    mode = str(getattr(cfg, "dialogue_intrusion_mode", "critical_only") or "").strip()
    aliases = {
        "avoid_interrupt": "no_interrupt",
        "protect_chat": "critical_only",
        "balanced": "critical_only",
        "immediate": "allow_interrupt",
    }
    mode = aliases.get(mode, mode)
    return mode if mode in {"no_interrupt", "critical_only", "allow_interrupt"} else "critical_only"


def _v2_live_verified_real_output_enabled(plugin: Any) -> bool:
    cfg = getattr(plugin, "cfg", None)
    return bool(getattr(cfg, "v2_live_verified_real_output_enabled", False))


def _plugin_reply_hint_enabled(plugin: Any) -> bool:
    cfg = getattr(plugin, "cfg", None)
    return bool(getattr(cfg, "plugin_reply_hint_enabled", True))


def _plugin_owned_blind_output_enabled(plugin: Any) -> bool:
    cfg = getattr(plugin, "cfg", None)
    return bool(getattr(cfg, "plugin_owned_blind_output_enabled", False))


def _plugin_owned_battle_output_enabled(plugin: Any) -> bool:
    cfg = getattr(plugin, "cfg", None)
    return bool(getattr(cfg, "plugin_owned_battle_output_enabled", False))


def _plugin_owned_urgent_output_enabled(plugin: Any) -> bool:
    cfg = getattr(plugin, "cfg", None)
    return bool(getattr(cfg, "plugin_owned_urgent_output_enabled", True))


def _should_use_plugin_owned_output(plugin: Any, event: BattleEvent, recommended_reply: str) -> bool:
    if not recommended_reply:
        return False
    if _plugin_owned_blind_output_enabled(plugin):
        return True
    if _plugin_owned_battle_output_enabled(plugin) and event.event_id in PLUGIN_OWNED_DIRECT_EVENTS:
        return True
    if not _plugin_owned_urgent_output_enabled(plugin):
        return False
    return event.event_id == "you_died" or (event.event_id in URGENT_REPLACE_EVENTS and event.level == "critical")


def _quiet_window_bypass(plugin: Any, event: BattleEvent) -> bool:
    mode = _dialogue_intrusion_mode(plugin)
    if mode == "no_interrupt":
        return False
    if mode == "allow_interrupt":
        return True
    if event.event_id == "you_died":
        return True
    return event.level == "critical" and event.event_id in URGENT_REPLACE_EVENTS


def _quiet_window_suppression(plugin: Any, event: BattleEvent, now: float) -> tuple[str, float] | None:
    if _quiet_window_bypass(plugin, event):
        return None

    user_window = _user_chat_quiet_window_seconds(plugin)
    try:
        last_user_chat_at = float(getattr(plugin, "_last_user_chat_at", 0.0) or 0.0)
    except (TypeError, ValueError):
        last_user_chat_at = 0.0
    if user_window > 0 and last_user_chat_at > 0:
        remaining = user_window - (now - last_user_chat_at)
        if remaining > 0:
            return "user_chat_quiet_window", round(remaining, 3)

    battle_window = _battle_output_quiet_window_seconds(plugin)
    try:
        last_battle_respond_at = float(getattr(plugin, "_last_battle_respond_at", 0.0) or 0.0)
    except (TypeError, ValueError):
        last_battle_respond_at = 0.0
    if battle_window > 0 and last_battle_respond_at > 0:
        remaining = battle_window - (now - last_battle_respond_at)
        if remaining > 0:
            return "battle_output_quiet_window", round(remaining, 3)

    return None


def _clean_target_lanlan(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()[:80]


def _resolve_target_lanlan(plugin: Any, event: BattleEvent | None = None) -> str:
    payload = event.payload if event and isinstance(event.payload, dict) else {}
    for candidate in (
        payload.get("target_lanlan"),
        payload.get("lanlan_name"),
    ):
        target = _clean_target_lanlan(candidate)
        if target:
            return target

    ctx_obj = payload.get("_ctx")
    if isinstance(ctx_obj, dict):
        target = _clean_target_lanlan(ctx_obj.get("lanlan_name"))
        if target:
            return target

    cfg = getattr(plugin, "cfg", None)
    for candidate in (
        getattr(cfg, "target_lanlan", ""),
        getattr(cfg, "lanlan_name", ""),
    ):
        target = _clean_target_lanlan(candidate)
        if target:
            return target

    plugin_ctx = getattr(plugin, "ctx", None)
    target = _clean_target_lanlan(getattr(plugin_ctx, "_current_lanlan", None))
    if target:
        return target

    for env_name in ("NEKO_WARTHUNDER_TARGET_LANLAN", "NEKO_TARGET_LANLAN", "NEKO_LANLAN_NAME", "NEKO_HER_NAME"):
        target = _clean_target_lanlan(os.getenv(env_name, ""))
        if target:
            return target

    try:
        from utils.config_manager import get_config_manager

        character_data = get_config_manager().get_character_data()
        if isinstance(character_data, tuple) and len(character_data) >= 2:
            target = _clean_target_lanlan(character_data[1])
            if target:
                return target
    except Exception:
        # Character lookup is optional; fall through to the empty target.
        pass

    return ""


def _event_freshness_metadata(event: BattleEvent, now: float, plugin: Any) -> dict[str, float]:
    out: dict[str, float] = {}
    max_age = _output_event_max_age_seconds(plugin, event)
    if event.ts > 0:
        out["event_ts"] = round(float(event.ts), 3)
        if now >= event.ts:
            out["event_age_seconds"] = round(float(now - event.ts), 3)
        if max_age > 0:
            out["event_max_age_seconds"] = round(float(max_age), 3)
            out["event_expires_at"] = round(float(event.ts + max_age), 3)
    elif max_age > 0:
        out["event_max_age_seconds"] = round(float(max_age), 3)
    return out


def _reply_style_contract(event: BattleEvent) -> str:
    if event.event_id == "you_killed":
        if event.payload.get("trade_death"):
            return (
                "Style: exactly one natural Chinese line; lost vehicle but note the trade gently; no slogan, no analysis."
            )
        try:
            kill_count = int(event.payload.get("kill_count") or 1)
        except (TypeError, ValueError):
            kill_count = 1
        if kill_count > 1:
            return "Style: exactly one natural Chinese line; react to the streak once, warm and playful; no fixed praise."
        return "Style: exactly one natural Chinese line; casual live reaction to the kill; warm or playful, no slogan."
    if event.event_id == "you_died":
        return "Style: one short Chinese line; calm reset; no analysis."
    if event.event_id == "spawn":
        return "Style: one short lively Chinese line; ready-up greeting only; no battle facts."
    if event.event_id in URGENT_REPLACE_EVENTS or event.level == "critical":
        return "Style: one short Chinese line; urgent command; no filler."
    if event.event_id in {"air_threat_nearby", "enemy_nearby", "enemy_on_six", "tailing_risk"}:
        return "Style: one short Chinese line; situational cue; no takeover."
    if event.event_id == "ground_target_nearby":
        return "Style: one short Chinese line; target/nav cue only; no takeover."
    if event.event_id in {
        "ground_laser_warning",
        "ground_crew_loss",
        "ground_gunner_disabled",
        "ground_driver_disabled",
        "ground_ammo_empty",
        "ground_ammo_low",
    }:
        return "Style: one short Chinese line; ground crew cue; no takeover."
    if event.event_id == "overheat":
        return "Style: one short Chinese line; situational cue; no repeated wording."
    return "Style: one short Chinese line; concise copilot cue."


def _plugin_dialogue_policy(event: BattleEvent) -> dict[str, Any]:
    return {
        "owner": "plugin",
        "mode": BATTLE_REPLY_CONTRACT,
        "max_chars": BATTLE_REPLY_MAX_CHARS,
        "single_line": True,
        "no_followup": True,
        "prompt_owned": True,
        "style": HOST_REPLY_STYLE,
        "style_hint": _reply_style_contract(event),
    }


def _short_line(text: str) -> str:
    line = str(text or "").strip().replace("\r", " ").replace("\n", " ")
    for sep in ("。", "！", "？", "!", "?"):
        idx = line.find(sep)
        if idx >= 0:
            line = line[: idx + 1]
            break
    return line[:BATTLE_REPLY_MAX_CHARS].strip()


def _spawn_domain(event: BattleEvent) -> str:
    payload = event.payload if isinstance(event.payload, dict) else {}
    return str(payload.get("domain") or "").lower()


def _spawn_domain_hint(event: BattleEvent) -> str:
    domain = _spawn_domain(event)
    if domain == "air":
        return "当前模式：空战/飞行。角色：后座或僚机。可用语境：上机、升空、跟上、护住你"
    if domain == "heli":
        return (
            "当前模式：直升机/旋翼机。角色：机组搭档。"
            "可用语境：起飞、贴地、悬停、看高度、跟上；不要串到其他载具域"
        )
    if domain == "ground":
        return (
            "当前模式：陆战/地面载具。角色：车组搭档。"
            "可用语境：上车、出击、车组、装填、掩体、看路；不要串到其他载具域"
        )
    if domain == "naval":
        return (
            "当前模式：海战/舰艇。角色：舰桥观察员。"
            "可用语境：上舰、出航、舰桥、航向、海面；不要串到其他载具域"
        )
    return "当前模式：未知载具域。只做泛化出场招呼和打气，不猜载具类型"


def _event_domain(event: BattleEvent) -> str:
    payload = event.payload if isinstance(event.payload, dict) else {}
    return str(payload.get("domain") or "").lower()


def _domain_prompt_contract(event: BattleEvent) -> str:
    if event.event_id == "spawn":
        return ""
    domain = _event_domain(event)
    if domain == "air":
        return "当前模式：空战/飞行；角色：后座或僚机；只用本域语境；"
    if domain == "heli":
        return "当前模式：直升机/旋翼机；角色：机组搭档；只用本域语境；"
    if domain == "ground":
        return "当前模式：陆战/地面载具；角色：车组搭档；只用本域语境；"
    if domain == "naval":
        return "当前模式：海战/舰艇；角色：舰桥观察员；只用本域语境；"
    return ""


def _metadata_domain_prompt_contract(event: BattleEvent) -> str:
    if event.event_id == "spawn":
        return _spawn_domain_hint(event)
    return _domain_prompt_contract(event)


def _kill_domain_intent(domain: str) -> str:
    if domain == "air":
        return "空战后座语气，可轻夸、吐槽或提醒留速"
    if domain == "heli":
        return "直升机机组语气，可轻夸、吐槽或提醒高度/脱离；不猜固定翼动作"
    if domain == "ground":
        return "陆战车组语气，可轻夸、吐槽或提醒别贪；只用地面载具战果语境"
    if domain == "naval":
        return "海战舰桥语气，可轻夸、提气或提醒航向；只用舰艇战果语境"
    return "泛化确认战果，不猜载具类型"


def _kill_style_hint(domain: str, kill_count: int, *, trade_death: bool) -> str:
    base = "风格：一句短话；像临场反应，不像颁奖词；不复读上一句。"
    if trade_death:
        return f"{base} 只轻轻肯定换掉了，不复盘。"
    if kill_count > 1:
        return f"{base} 连杀只合并说一次，可以带点惊喜或坏笑。"
    if domain == "ground":
        return f"{base} 陆战别固定说稳住/推进，可轻夸、调侃或提醒别贪。"
    if domain == "naval":
        return f"{base} 海战别固定说压住/航向，可轻夸、提气或收住。"
    if domain == "heli":
        return f"{base} 直升机别固定说漂亮/节奏，可轻夸、调侃或提醒高度/脱离。"
    if domain == "air":
        return f"{base} 空战别固定说漂亮/节奏，可轻夸、调侃或提醒留速。"
    return f"{base} 可不夸。"


def _recommended_reply_line(event: BattleEvent) -> str:
    p, _ = sanitize_event_payload(event.event_id, event.payload)
    if event.event_id == "spawn":
        return ""
    if event.event_id == "you_killed":
        return ""
    if event.event_id == "player_radio_command":
        command = str(p.get("command") or "")
        point = _radio_point(p)
        replies = {
            "cover_me": "收到，我看着你。",
            "need_help": "收到，先别硬撑。",
            "attack_point": f"收到，看{point}点。" if point else "收到，看目标点。",
            "defend_point": f"收到，守{point}点。" if point else "收到，守住节奏。",
            "return_to_base": "好，先活着回去。",
            "repairing": "收到，先躲稳。",
            "follow_me": "嗯，我跟着你。",
            "thanks": "哼，知道就好。",
            "affirmative": "收到，跟你走。",
            "negative": "收到，先不冒险。",
            "well_done": "哼，那当然。",
        }
        return replies.get(command, "收到。")
    if event.event_id == "stall_risk":
        return "加速，快失速了！"
    if event.event_id == "high_aoa":
        return "松杆，迎角过大！"
    if event.event_id == "over_g":
        return "松杆，过载太大！"
    if event.event_id == "low_alt_danger":
        return "拉起来，要撞地了！"
    if event.event_id == "overspeed":
        return "收油，速度太快！"
    if event.event_id == "ground_laser_warning":
        return "被照了，找掩体！"
    if event.event_id == "ground_crew_loss":
        return "车组受损，先收一下！"
    if event.event_id == "ground_gunner_disabled":
        return "炮手没了，先别硬拼！"
    if event.event_id == "ground_driver_disabled":
        return "驾驶没了，找掩体！"
    if event.event_id == "ground_ammo_empty":
        return "一级弹药空了，别硬拼！"
    if event.event_id == "ground_ammo_low":
        return "待发弹不多了，控节奏！"
    if event.event_id == "enemy_on_six":
        return "六点钟，甩开它！"
    if event.event_id == "tailing_risk":
        return "后方咬住了，机动！"
    if event.event_id == "air_threat_nearby":
        clock = p.get("clock")
        if isinstance(clock, int) and 1 <= clock <= 12:
            return _short_line(f"{clock}点钟有敌机。")
        return "附近有敌机，抬头。"
    return ""


def _copilot_role_boundary(event: BattleEvent) -> str:
    if event.event_id in {
        "enemy_nearby",
        "air_threat_nearby",
        "enemy_on_six",
        "tailing_risk",
        "ground_target_nearby",
        "ground_laser_warning",
        "ground_crew_loss",
        "ground_gunner_disabled",
        "ground_driver_disabled",
        "ground_ammo_empty",
        "ground_ammo_low",
    }:
        return (
            COPILOT_ROLE_BOUNDARY
            + " 只报观测到的方位/距离/目标类型，缺项别补；禁：交给我/我来/已锁定/开火。"
        )
    return COPILOT_ROLE_BOUNDARY


def _event_intent(event: BattleEvent) -> str:
    if event.edge == "recovery":
        return _RECOVERY_INTENT
    if event.event_id == "spawn":
        return (
            f"跟 {{MASTER_NAME}} 短促开局招呼；"
            f"{_spawn_domain_hint(event)}；"
            "可活泼即兴、轻微玩笑或打气；别报敌情/方位/锁定/击杀/威胁"
        )
    if event.event_id == "you_killed":
        p, _ = sanitize_event_payload(event.event_id, event.payload)
        domain = str(p.get("domain") or "").lower()
        try:
            kill_count = int(p.get("kill_count") or 1)
        except (TypeError, ValueError):
            kill_count = 1
        if p.get("trade_death"):
            return (
                f"换掉一个；{_kill_domain_intent(domain)}；"
                "对 {MASTER_NAME} 克制反应，可安慰或轻夸，不复盘，不套固定话"
            )
        if kill_count > 1:
            return (
                f"连续战果 {kill_count} 个；{_kill_domain_intent(domain)}；"
                "对 {MASTER_NAME} 只回应一句，可开心、坏笑或轻轻得意，不逐条念，不套固定话"
            )
        return (
            f"刚才战果；{_kill_domain_intent(domain)}；"
            "对 {MASTER_NAME} 顺口接一句，可确认、轻夸、调侃或收住，不套固定话；别总说稳住/继续推进"
        )
    return _INTENT.get(event.event_id, "")


def _prompt_reply_contract(event: BattleEvent) -> str:
    if event.event_id in FLEX_STYLE_EVENTS:
        return "短话为主，可带一点情绪/玩笑/陪伴感；不反问、不续聊。"
    return "一句短话；不反问、不续聊。"


def _output_shape_contract(event: BattleEvent) -> str:
    if event.event_id in URGENT_REPLACE_EVENTS or event.level == "critical":
        tone = "紧急事件优先动作词"
    elif event.event_id in {"you_killed", "spawn", "battle_end"}:
        tone = "轻松事件可以有一点情绪"
    elif event.event_id == "player_radio_command":
        tone = "玩家主动口令可以像顺手应一声"
    else:
        tone = "提醒事件自然像同伴开口"
    return f"输出：一句中文台词，28字内；{tone}；不复述规则/字段，不加前缀或引号。"


def _domain_vocab_contract(event: BattleEvent) -> str:
    domain = _spawn_domain(event) if event.event_id == "spawn" else _event_domain(event)
    if domain == "air":
        return "语境：只用空战飞行词，不串其他载具域。"
    if domain == "heli":
        return "语境：只用直升机机组词，不串其他载具域。"
    if domain == "ground":
        return "语境：只用陆战车组词，不串其他载具域。"
    if domain == "naval":
        return "语境：只用海战舰艇词，不串其他载具域。"
    return "语境：未知载具域只泛化打气，不猜载具动作。"


def _prompt_style_hint(event: BattleEvent) -> str:
    if event.event_id == "you_killed":
        domain = _event_domain(event)
        try:
            kill_count = int(event.payload.get("kill_count") or 1)
        except (TypeError, ValueError):
            kill_count = 1
        return _kill_style_hint(domain, kill_count, trade_death=bool(event.payload.get("trade_death")))
    if event.event_id == "you_died":
        return "风格：短话为主；可安抚、吐槽或打气，不复盘。"
    if event.event_id == "spawn":
        return "风格：短话为主；活泼一点，可有小情绪，只打出场招呼。"
    if event.event_id == "player_radio_command":
        return "风格：短话为主；像听到队内无线电后的回应，不复述无线电原文。"
    if event.event_id in URGENT_REPLACE_EVENTS or event.level == "critical":
        return "风格：一句短话；急促指令，不闲聊。"
    if event.event_id in {"air_threat_nearby", "enemy_nearby", "enemy_on_six", "tailing_risk"}:
        if event.event_id == "enemy_nearby":
            return "风格：短话为主；像提醒伙伴留神，不接管武器。"
        return "风格：一句短话；只报态势，不接管武器。"
    if event.event_id == "ground_target_nearby":
        return "风格：短话为主；像导航或观察提醒，不接管操作。"
    if event.event_id in {
        "ground_laser_warning",
        "ground_crew_loss",
        "ground_gunner_disabled",
        "ground_driver_disabled",
        "ground_ammo_empty",
        "ground_ammo_low",
    }:
        return "风格：一句短话；像车组提醒，不接管操作，不展开分析。"
    if event.event_id == "overheat":
        return "风格：短话为主；提醒处置，可带一点紧张感。"
    if event.event_id == "low_fuel":
        return "风格：短话为主；提醒规划，可带一点陪伴感。"
    if event.event_id == "battle_end":
        return "风格：短话为主；轻松收尾，可小小吐槽，不展开战报。"
    return "风格：一句短话；干净利落。"


def _host_interrupt_pending(event: BattleEvent) -> bool:
    return event.event_id in URGENT_REPLACE_EVENTS


def _host_callback_contract(
    event: BattleEvent,
    *,
    freshness: dict[str, float],
    target_lanlan: str,
) -> dict[str, Any]:
    delivery = {
        "coalesce_key": BATTLE_EVENT_COALESCE_KEY,
        "replace_pending": True,
        "interrupt_pending": _host_interrupt_pending(event),
        "priority": event.priority,
    }
    if freshness.get("event_expires_at") is not None:
        delivery["expires_at"] = freshness["event_expires_at"]
    if freshness.get("event_max_age_seconds") is not None:
        delivery["max_age_seconds"] = freshness["event_max_age_seconds"]

    contract: dict[str, Any] = {
        "version": HOST_CALLBACK_CONTRACT_VERSION,
        "kind": HOST_CALLBACK_KIND,
        "delivery": delivery,
        "freshness": {
            key: freshness[key]
            for key in ("event_ts", "event_age_seconds", "event_max_age_seconds", "event_expires_at")
            if freshness.get(key) is not None
        },
    }
    if target_lanlan:
        contract["target"] = {"lanlan": target_lanlan}
    return contract


def _fact_line(event: BattleEvent) -> str:
    p, _ = sanitize_event_payload(event.event_id, event.payload)
    bits: list[str] = []
    kill_fact = _kill_fact(event.event_id, p)
    death_fact = _death_fact(event.event_id, p)
    proximity_fact = _proximity_fact(event.event_id, p)
    objective_fact = _objective_fact(event.event_id, p)
    ground_fact = _ground_vehicle_fact(event.event_id, p)
    free_text_fact = _free_text_fact(event.event_id, p)
    radio_fact = _radio_command_fact(event.event_id, p)
    has_radio_altitude = p.get("radio_altitude_m") is not None
    order = [
        ("ias_kmh", "IAS {:.0f}km/h"),
        ("aoa_deg", "迎角 {:.0f}°"),
        ("altitude_m", "高度 {:.0f}m"),
        ("climb_ms", "垂速 {:+.0f}m/s"),
        ("mach", "M {:.2f}"),
        ("fuel_fraction", "余油 {:.0%}"),
        ("temp_c", "温度 {:.0f}℃"),
        ("kill_count", "连杀 {}"),
        ("result", "战果 {}"),
    ]
    if kill_fact:
        bits.append(kill_fact)
    if event.event_id == "you_killed" and p.get("trade_death"):
        bits.append("同归于尽/换掉一个")
    if death_fact:
        bits.append(death_fact)
    if proximity_fact:
        bits.append(proximity_fact)
    if objective_fact:
        bits.append(objective_fact)
    if ground_fact:
        bits.append(ground_fact)
    if free_text_fact:
        bits.append(free_text_fact)
    if radio_fact:
        bits.append(radio_fact)
    if has_radio_altitude:
        try:
            bits.append("AGL {:.0f}m".format(p["radio_altitude_m"]))
        except (ValueError, TypeError):
            # Ignore malformed optional telemetry and keep the remaining facts.
            pass
    for key, fmt in order:
        if key == "altitude_m" and has_radio_altitude:
            continue
        if key in p and p[key] is not None:
            try:
                bits.append(fmt.format(p[key]))
            except (ValueError, TypeError):
                # Ignore malformed optional telemetry and keep the remaining facts.
                pass
    return "、".join(bits)


def _kill_fact(event_id: str, payload: dict[str, Any]) -> str:
    if event_id != "you_killed":
        return ""
    domain = str(payload.get("domain") or "").lower()
    if domain in {"air", "heli"}:
        return "击落敌方空中目标"
    if domain == "ground":
        return "击毁敌方地面目标"
    if domain == "naval":
        return "击毁敌方舰艇"
    return "击毁敌方目标"


def _death_fact(event_id: str, payload: dict[str, Any]) -> str:
    if event_id != "you_died":
        return ""
    cause = str(payload.get("cause") or "").lower()
    domain = str(payload.get("domain") or "").lower()
    if cause == "crashed":
        return "己方载具坠毁"
    if cause in {"destroyed", "wrecked"}:
        if domain == "naval":
            return "己方舰艇被摧毁"
        return "己方载具被摧毁"
    if cause == "shot_down":
        if domain in {"air", "heli"}:
            return "己方空中载具被击落"
        return "己方载具被击毁"
    return "己方载具损失"


def _proximity_fact(event_id: str, payload: dict[str, Any]) -> str:
    if event_id not in {"enemy_nearby", "air_threat_nearby", "enemy_on_six", "tailing_risk"}:
        return ""
    if event_id == "tailing_risk":
        base = "后方威胁持续接近"
    elif event_id == "enemy_on_six":
        base = "后方威胁接近"
    elif event_id == "air_threat_nearby":
        base = "空中威胁接近"
    else:
        base = "敌方目标接近"

    detail: list[str] = []
    clock = payload.get("clock")
    if isinstance(clock, int) and 1 <= clock <= 12:
        detail.append(f"{clock}点钟")
    elif payload.get("compass"):
        detail.append(f"{payload['compass']}方向")

    distance = payload.get("distance_m")
    try:
        if distance is not None:
            detail.append("距离{:.0f}m".format(float(distance)))
    except (TypeError, ValueError):
        # Invalid optional distance does not invalidate the proximity event.
        pass

    return base if not detail else f"{base}（{'，'.join(detail)}）"


def _objective_fact(event_id: str, payload: dict[str, Any]) -> str:
    if event_id != "ground_target_nearby":
        return ""

    detail: list[str] = []
    grid = payload.get("grid")
    if isinstance(grid, str) and grid:
        detail.append(f"{grid}网格")

    distance = payload.get("distance_m")
    try:
        if distance is not None:
            detail.append("距离{:.0f}m".format(float(distance)))
    except (TypeError, ValueError):
        # Invalid optional distance does not invalidate the objective event.
        pass

    return "任务目标点接近" if not detail else f"任务目标点接近（{'，'.join(detail)}）"


def _ground_vehicle_fact(event_id: str, payload: dict[str, Any]) -> str:
    if event_id == "ground_laser_warning":
        return "陆战激光告警"
    if event_id == "ground_crew_loss":
        return "陆战车组受损"
    if event_id == "ground_gunner_disabled":
        return "陆战炮手失能"
    if event_id == "ground_driver_disabled":
        return "陆战驾驶员失能"
    if event_id == "ground_ammo_empty":
        return "一级弹药打空"
    if event_id == "ground_ammo_low":
        return "一级弹药偏少"
    return ""


def _free_text_fact(event_id: str, payload: dict[str, Any]) -> str:
    if event_id != "free_text_activity":
        return ""
    source_labels = {
        "awards": "奖励/战绩通知",
        "combat_feed": "战斗记录",
        "hud_notices": "HUD通知",
        "hud_events": "HUD事件",
        "hudmsg": "战场提示",
    }
    source = str(payload.get("source") or "")
    label = source_labels.get(source, "战场文字来源")
    detail: list[str] = []
    try:
        count = int(payload.get("count") or 0)
    except (TypeError, ValueError):
        count = 0
    if count > 0:
        detail.append(f"{count}条")
    code = payload.get("latest_code")
    if isinstance(code, str) and code:
        detail.append(code)
    return label if not detail else f"{label}（{'，'.join(detail)}）"


def _radio_point(payload: dict[str, Any]) -> str:
    point = str(payload.get("point") or "").strip().upper()
    return point if point in {"A", "B", "C", "D"} else ""


def _radio_command_label(payload: dict[str, Any]) -> str:
    command = str(payload.get("command") or "")
    point = _radio_point(payload)
    if command == "attack_point":
        return f"进攻{point}点" if point else "进攻目标点"
    if command == "defend_point":
        return f"防守{point}点" if point else "防守目标点"
    labels = {
        "cover_me": "掩护我",
        "need_help": "需要支援",
        "return_to_base": "返回基地",
        "repairing": "正在维修",
        "follow_me": "跟着我",
        "thanks": "感谢",
        "affirmative": "肯定",
        "negative": "否定",
        "well_done": "干得好",
    }
    return labels.get(command, "无线电口令")


def _radio_command_fact(event_id: str, payload: dict[str, Any]) -> str:
    if event_id != "player_radio_command":
        return ""
    return f"玩家无线电：{_radio_command_label(payload)}"


class NekoDispatcher:
    def __init__(
        self,
        plugin: Any,
        *,
        timeline: RuntimeTimeline | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.plugin = plugin
        self.timeline = timeline
        self.logger = getattr(plugin, "logger", None)
        self._clock = clock or time.time
        self._last_push_at: float | None = None
        self._last_push_priority = -1
        self._last_event_push: dict[str, tuple[float, str, str]] = {}

    def build_prompt(self, event: BattleEvent) -> str:
        intent = _event_intent(event)
        fact = _fact_line(event)
        recommended_reply = _recommended_reply_line(event)
        domain_contract = _domain_prompt_contract(event)
        lines = []
        if fact:
            lines.append(f"[当前] {fact}")
        lines.append(f"[要求] {domain_contract}{intent}。{_prompt_reply_contract(event)}")
        if _plugin_reply_hint_enabled(self.plugin) and recommended_reply:
            lines[-1] = f"{lines[-1]} 建议台词：{recommended_reply}"
        lines.append(f"{_copilot_role_boundary(event)} {_domain_vocab_contract(event)}")
        lines.append(f"{_output_shape_contract(event)} {_prompt_style_hint(event)}")
        return "\n".join(lines)

    def push_event(self, event: BattleEvent, *, dry_run: bool) -> str:
        """把一个 BattleEvent 投给猫娘。dry_run 时只返回摘要、不真投。"""
        if dry_run:
            if self.timeline:
                self.timeline.record_stage(
                    stage="dispatcher_dry_run",
                    outcome="dry_run",
                    reason="dry_run_enabled",
                    event_id=event.event_id,
                    edge=event.edge,
                    level=event.level,
                    priority=event.priority,
                    dry_run=True,
                    kind="event",
                    ai_behavior="respond",
                    pushed=False,
                    safe_summary=f"{event.event_id}/{event.edge}/{event.level}",
            )
            return f"dry_run(event={event.event_id}/{event.edge}/{event.level}, prio={event.priority}, preempt={event.preempt_eligible})"
        if event.event_id in FREE_TEXT_DRY_RUN_ONLY_EVENTS:
            if self.timeline:
                self.timeline.record_stage(
                    stage="dispatcher_suppressed",
                    outcome="dropped",
                    reason="free_text_dry_run_only",
                    event_id=event.event_id,
                    edge=event.edge,
                    level=event.level,
                    priority=event.priority,
                    dry_run=False,
                    kind="event",
                    ai_behavior="respond",
                    pushed=False,
                    safe_summary=f"{event.event_id}/{event.edge}/{event.level}",
                )
            return f"suppressed(event={event.event_id}/{event.edge}, reason=free_text_dry_run_only)"
        if self._is_v2_live_evidence_gated(event):
            if self.timeline:
                self.timeline.record_stage(
                    stage="dispatcher_suppressed",
                    outcome="dropped",
                    reason="v2_live_evidence_pending",
                    event_id=event.event_id,
                    edge=event.edge,
                    level=event.level,
                    priority=event.priority,
                    dry_run=False,
                    kind="event",
                    ai_behavior="respond",
                    pushed=False,
                    safe_summary=f"{event.event_id}/{event.edge}/{event.level}",
                )
            return f"suppressed(event={event.event_id}/{event.edge}, reason=v2_live_evidence_pending)"
        now = self._clock()
        freshness = _event_freshness_metadata(event, now, self.plugin)
        if self._is_expired(event, now):
            if self.timeline:
                self.timeline.record_stage(
                    stage="dispatcher_suppressed",
                    outcome="dropped",
                    reason="event_expired",
                    event_id=event.event_id,
                    edge=event.edge,
                    level=event.level,
                    priority=event.priority,
                    dry_run=False,
                    kind="event",
                    ai_behavior="respond",
                    pushed=False,
                    safe_summary=f"{event.event_id}/{event.edge}/{event.level}",
                    **freshness,
                )
            return f"suppressed(event={event.event_id}/{event.edge}, reason=event_expired)"
        recommended_reply = _recommended_reply_line(event)
        if self._is_repeated_event_collapsed(event, recommended_reply, now):
            if self.timeline:
                self.timeline.record_stage(
                    stage="dispatcher_suppressed",
                    outcome="dropped",
                    reason="repeated_event_collapsed",
                    event_id=event.event_id,
                    edge=event.edge,
                    level=event.level,
                    priority=event.priority,
                    dry_run=False,
                    kind="event",
                    ai_behavior="blind" if _should_use_plugin_owned_output(self.plugin, event, recommended_reply) else "respond",
                    pushed=False,
                    safe_summary=f"{event.event_id}/{event.edge}/{event.level}",
                    plugin_recommended_reply=recommended_reply,
                    **freshness,
                )
            return f"suppressed(event={event.event_id}/{event.edge}, reason=repeated_event_collapsed)"
        quiet_suppression = _quiet_window_suppression(self.plugin, event, now)
        if quiet_suppression is not None:
            reason, remaining = quiet_suppression
            if self.timeline:
                self.timeline.record_stage(
                    stage="dispatcher_suppressed",
                    outcome="dropped",
                    reason=reason,
                    event_id=event.event_id,
                    edge=event.edge,
                    level=event.level,
                    priority=event.priority,
                    dry_run=False,
                    kind="event",
                    ai_behavior="respond",
                    pushed=False,
                    safe_summary=f"{event.event_id}/{event.edge}/{event.level}",
                    quiet_window_remaining_seconds=remaining,
                    plugin_recommended_reply=recommended_reply,
                    **freshness,
                )
            return f"suppressed(event={event.event_id}/{event.edge}, reason={reason})"
        if self._is_backpressured(event, now):
            if self.timeline:
                self.timeline.record_stage(
                    stage="dispatcher_suppressed",
                    outcome="dropped",
                    reason="output_backpressure",
                    event_id=event.event_id,
                    edge=event.edge,
                    level=event.level,
                    priority=event.priority,
                    dry_run=False,
                    kind="event",
                    ai_behavior="respond",
                    pushed=False,
                    safe_summary=f"{event.event_id}/{event.edge}/{event.level}",
                    **freshness,
                )
            return f"suppressed(event={event.event_id}/{event.edge}, reason=output_backpressure)"
        plugin_owned_output = _should_use_plugin_owned_output(self.plugin, event, recommended_reply)
        text = _short_line(recommended_reply) if plugin_owned_output and recommended_reply else self.build_prompt(event)
        target_lanlan = _resolve_target_lanlan(self.plugin, event)
        host_contract = _host_callback_contract(event, freshness=freshness, target_lanlan=target_lanlan)
        dialogue_policy = _plugin_dialogue_policy(event)
        visibility = ["chat"] if plugin_owned_output else []
        ai_behavior = "blind" if plugin_owned_output else "respond"
        metadata = {
            "plugin": "neko_warthunder",
            "event_id": event.event_id,
            "edge": event.edge,
            "level": event.level,
            "domain": _event_domain(event),
            "domain_prompt_contract": _metadata_domain_prompt_contract(event),
            "coalesce_key": BATTLE_EVENT_COALESCE_KEY,
            "replace_pending": True,
            "interrupt_battle_event": _host_interrupt_pending(event),
            "interrupt_pending": _host_interrupt_pending(event),
            "battle_reply_contract": BATTLE_REPLY_CONTRACT,
            "live_reply_contract": BATTLE_REPLY_CONTRACT,
            "reply_contract": BATTLE_REPLY_CONTRACT,
            "max_reply_chars": BATTLE_REPLY_MAX_CHARS,
            "reply_max_chars": BATTLE_REPLY_MAX_CHARS,
            "response_module_hint": BATTLE_RESPONSE_MODULE_HINT,
            "plugin_recommended_reply": recommended_reply,
            "plugin_owned_output": plugin_owned_output,
            "reply_style_contract": _reply_style_contract(event),
            "dialogue_policy_owner": "plugin",
            "plugin_dialogue_policy": dialogue_policy,
            "plugin_quiet_window_policy": HOST_QUIET_WINDOW_POLICY,
            "host_callback_contract_version": HOST_CALLBACK_CONTRACT_VERSION,
            "host_callback_contract": host_contract,
            **freshness,
        }
        if target_lanlan:
            metadata["target_lanlan"] = target_lanlan
        try:
            self.plugin.push_message(
                source="neko_warthunder",
                visibility=visibility,
                ai_behavior=ai_behavior,
                parts=[{"type": "text", "text": text}],
                priority=event.priority,
                coalesce_key=BATTLE_EVENT_COALESCE_KEY,
                metadata=metadata,
                target_lanlan=target_lanlan or None,
            )
        except Exception as exc:
            if self.timeline:
                self.timeline.record_stage(
                    stage="dispatcher_failed",
                    outcome="failed",
                    reason=type(exc).__name__,
                    event_id=event.event_id,
                    edge=event.edge,
                    level=event.level,
                    priority=event.priority,
                    dry_run=False,
                    kind="event",
                    ai_behavior=ai_behavior,
                    pushed=False,
                )
            raise
        self._last_push_at = now
        self._last_push_priority = event.priority
        self._last_event_push[event.event_id] = (
            now,
            event.level,
            self._repeat_signature(event, recommended_reply),
        )
        if ai_behavior == "respond" and self.plugin is not None:
            try:
                setattr(self.plugin, "_last_battle_respond_at", now)
            except Exception:
                # Host objects may reject optional bookkeeping attributes.
                pass
        if self.timeline:
            self.timeline.record_stage(
                stage="dispatcher_pushed",
                outcome="pushed",
                reason="push_message_accepted",
                event_id=event.event_id,
                edge=event.edge,
                level=event.level,
                priority=event.priority,
                dry_run=False,
                kind="event",
                ai_behavior=ai_behavior,
                pushed=True,
                safe_summary=f"{event.event_id}/{event.edge}/{event.level}",
                target_lanlan=target_lanlan,
                visibility=visibility,
                coalesce_key=BATTLE_EVENT_COALESCE_KEY,
                battle_reply_contract=BATTLE_REPLY_CONTRACT,
                live_reply_contract=BATTLE_REPLY_CONTRACT,
                max_reply_chars=BATTLE_REPLY_MAX_CHARS,
                response_module_hint=BATTLE_RESPONSE_MODULE_HINT,
                plugin_recommended_reply=recommended_reply,
                plugin_owned_output=plugin_owned_output,
                replace_pending=True,
                interrupt_battle_event=_host_interrupt_pending(event),
                interrupt_pending=_host_interrupt_pending(event),
                reply_style_contract=_reply_style_contract(event),
                reply_contract=BATTLE_REPLY_CONTRACT,
                reply_max_chars=BATTLE_REPLY_MAX_CHARS,
                dialogue_policy_owner="plugin",
                plugin_dialogue_policy=dialogue_policy,
                plugin_quiet_window_policy=HOST_QUIET_WINDOW_POLICY,
                host_callback_contract_version=HOST_CALLBACK_CONTRACT_VERSION,
                **freshness,
            )
        return f"pushed(event={event.event_id}/{event.edge})"

    def _is_backpressured(self, event: BattleEvent, now: float) -> bool:
        if event.event_id in BACKPRESSURE_BYPASS_EVENTS or event.level == "critical":
            return False
        guard = _output_backpressure_seconds(self.plugin)
        if guard <= 0 or self._last_push_at is None:
            return False
        if now - self._last_push_at >= guard:
            return False
        return event.priority <= self._last_push_priority

    def _is_expired(self, event: BattleEvent, now: float) -> bool:
        max_age = _output_event_max_age_seconds(self.plugin, event)
        if max_age <= 0 or event.ts <= 0:
            return False
        return now >= event.ts and now - event.ts > max_age

    def _is_repeated_event_collapsed(self, event: BattleEvent, recommended_reply: str, now: float) -> bool:
        if event.event_id not in REPEAT_COLLAPSE_EVENT_IDS:
            return False
        last_at, last_level, last_signature = self._last_event_push.get(event.event_id, (-1e9, "", ""))
        if now - last_at >= REPEAT_COLLAPSE_SECONDS:
            return False
        if event.level == "critical" and last_level != "critical":
            return False
        return last_signature == self._repeat_signature(event, recommended_reply)

    @staticmethod
    def _repeat_signature(event: BattleEvent, recommended_reply: str) -> str:
        if recommended_reply:
            return recommended_reply
        keys = ("target_type", "distance_m", "clock", "grid", "temp_c", "temp_source", "domain", "source")
        facts = {key: event.payload.get(key) for key in keys if event.payload.get(key) is not None}
        return json.dumps(facts, ensure_ascii=True, sort_keys=True, separators=(",", ":"))

    def _is_v2_live_evidence_gated(self, event: BattleEvent) -> bool:
        if event.event_id not in V2_LIVE_EVIDENCE_GATED_EVENTS:
            return False
        return not _v2_live_verified_real_output_enabled(self.plugin)

    def push_context(self, text: str) -> bool:
        """注入/恢复常驻场景上下文（ai_behavior='read'，不触发回复）。"""
        target_lanlan = _resolve_target_lanlan(self.plugin)
        metadata = {"plugin": "neko_warthunder", "kind": "context"}
        if target_lanlan:
            metadata["target_lanlan"] = target_lanlan
        try:
            self.plugin.push_message(
                source="neko_warthunder",
                visibility=[],
                ai_behavior="read",
                parts=[{"type": "text", "text": text}],
                priority=0,
                metadata=metadata,
                target_lanlan=target_lanlan or None,
            )
            if self.timeline:
                self.timeline.record_stage(
                    stage="context_pushed",
                    outcome="pushed",
                    reason="push_message_accepted",
                    kind="context",
                    ai_behavior="read",
                    pushed=True,
                    dry_run=False,
                    safe_summary="context/read",
                    target_lanlan=target_lanlan,
                )
            return True
        except Exception as exc:  # noqa: BLE001 — 上下文注入失败不致命
            if self.timeline:
                self.timeline.record_stage(
                    stage="context_failed",
                    outcome="failed",
                    reason=type(exc).__name__,
                    kind="context",
                    ai_behavior="read",
                    pushed=False,
                    dry_run=False,
                )
            if self.logger:
                self.logger.warning(f"push_context failed: {type(exc).__name__}")
            return False
