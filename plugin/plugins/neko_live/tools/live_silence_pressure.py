"""Run a local NEKO Live silence pressure test through the hosted UI API.

This is intentionally an integration script: it talks to the running plugin
server, updates only plugin config, calls real hosted-ui actions, records every
result, and restores the original config at the end.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from plugin.plugins.neko_live.tools.pressure_guard import (
    EXIT_RUN,
    PressureError,
    compare_and_restore_config,
    disconnect_owned_connection,
    finalize_cleanup_failure,
    now_iso,
    prepare_log_path,
    require_action_success,
    require_real_output_confirmation,
    require_safe_preflight,
    wait_for_connection,
    write_jsonl,
)


PLUGIN_ID = "neko_live"
SURFACE_ID = "main"
KIND = "panel"
LOCALE = "zh-CN"
RESTORE_KEYS = (
    "live_platform",
    "live_room_ref",
    "live_room_id",
    "live_enabled",
    "live_mode",
    "dry_run",
    "developer_tools_enabled",
    "rate_limit_seconds",
    "queue_limit",
    "activity_level",
    "stream_theme",
    "stream_goal",
    "stream_columns",
    "stream_avoid_topics",
)


def request_json(method: str, url: str, payload: dict[str, Any] | None = None, *, timeout: float = 90) -> dict[str, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} -> HTTP {exc.code}: {body}") from exc


class HostedClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def context(self) -> dict[str, Any]:
        query = urllib.parse.urlencode({"kind": KIND, "id": SURFACE_ID, "locale": LOCALE})
        return request_json("GET", f"{self.base_url}/plugin/{PLUGIN_ID}/hosted-ui/context?{query}")

    def action(self, action_id: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {
            "args": args or {},
            "kind": KIND,
            "surface_id": SURFACE_ID,
            "locale": LOCALE,
        }
        return request_json("POST", f"{self.base_url}/plugin/{PLUGIN_ID}/hosted-ui/action/{action_id}", payload)

    def run_entry(self, entry_id: str, args: dict[str, Any]) -> dict[str, Any]:
        payload = {"plugin_id": PLUGIN_ID, "entry_id": entry_id, "args": args}
        created = request_json("POST", f"{self.base_url}/runs", payload)
        run_id = str(created.get("run_id") or "")
        if not run_id:
            return {"created": created, "record": {}, "export": {}}
        deadline = time.monotonic() + 30.0
        record: dict[str, Any] = {}
        while time.monotonic() < deadline:
            record = request_json("GET", f"{self.base_url}/runs/{run_id}")
            if str(record.get("status") or "") in {"succeeded", "failed", "cancelled"}:
                break
            time.sleep(0.2)
        export = request_json("GET", f"{self.base_url}/runs/{run_id}/export")
        return {"created": created, "record": record, "export": export}


def state_from_context(context: dict[str, Any]) -> dict[str, Any]:
    state = context.get("state")
    return state if isinstance(state, dict) else {}


def result_payload(action_response: dict[str, Any]) -> dict[str, Any]:
    result = action_response.get("result")
    if not isinstance(result, dict):
        return {}
    data = result.get("data")
    return data if isinstance(data, dict) else result


def compact_result(action_id: str, action_response: dict[str, Any]) -> dict[str, Any]:
    result = result_payload(action_response)
    event = result.get("event") if isinstance(result.get("event"), dict) else {}
    steps = result.get("steps") if isinstance(result.get("steps"), list) else []
    return {
        "action": action_id,
        "accepted": result.get("accepted"),
        "status": result.get("status"),
        "route": event.get("source"),
        "reason": result.get("reason"),
        "output": result.get("output") or "",
        "latency_ms": result.get("response_latency_ms"),
        "trace_id": event.get("trace_id"),
        "steps": [
            {
                "id": step.get("id"),
                "status": step.get("status"),
                "message": step.get("message"),
            }
            for step in steps
            if isinstance(step, dict)
        ],
    }


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text)).strip()


def risk_flags(text: str, previous_outputs: list[str]) -> list[str]:
    normalized = normalize_text(text)
    flags: list[str] = []
    if not normalized:
        return flags
    if len(text) > 90:
        flags.append("long_output")
    if re.search(r"[（(][^）)]{1,40}[）)]", text):
        flags.append("parenthetical_action")
    operator_patterns = (
        "你直播间",
        "本喵等了你",
        "你哪去了",
        "快跟进来的观众",
        "主播",
        "操作员",
        "YUI",
    )
    if any(pattern in text for pattern in operator_patterns):
        flags.append("operator_or_host_reference")
    for old in previous_outputs[-8:]:
        old_norm = normalize_text(old)
        if normalized and old_norm and normalized == old_norm:
            flags.append("exact_repeat")
            break
        if normalized and old_norm and (normalized in old_norm or old_norm in normalized):
            if min(len(normalized), len(old_norm)) >= 12:
                flags.append("near_repeat")
                break
    return flags


def summarize_context(context: dict[str, Any]) -> dict[str, Any]:
    state = state_from_context(context)
    config = state.get("config") if isinstance(state.get("config"), dict) else {}
    live_status = state.get("live_status") if isinstance(state.get("live_status"), dict) else {}
    live_state = state.get("live_state") if isinstance(state.get("live_state"), dict) else {}
    director = state.get("live_director_status") if isinstance(state.get("live_director_status"), dict) else {}
    recent = state.get("recent_results") if isinstance(state.get("recent_results"), list) else []
    return {
        "config": {
            "live_enabled": config.get("live_enabled"),
            "live_mode": config.get("live_mode"),
            "dry_run": config.get("dry_run"),
            "activity_level": config.get("activity_level"),
            "rate_limit_seconds": config.get("rate_limit_seconds"),
            "stream_theme": config.get("stream_theme"),
        },
        "live_status": {
            "summary": live_status.get("summary"),
            "reason": live_status.get("reason"),
            "can_output": live_status.get("can_output"),
        },
        "live_state": {
            "summary": live_state.get("summary"),
            "reason": live_state.get("reason"),
            "last_viewer_activity_age_sec": live_state.get("last_viewer_activity_age_sec"),
            "last_output_age_sec": live_state.get("last_output_age_sec"),
        },
        "director": {
            "action": director.get("next_auto_action"),
            "reason": director.get("reason"),
        },
        "recent_count": len(recent),
    }


def parse_fake_signal_points(value: str) -> set[int]:
    points: set[int] = set()
    for part in str(value or "").split(","):
        text = part.strip()
        if not text:
            continue
        try:
            point = int(text)
        except ValueError:
            continue
        if point > 0:
            points.add(point)
    return points


def fake_signal_payload(index: int) -> dict[str, Any]:
    samples = [
        ("fake_u01", "测试观众A", "1"),
        ("fake_u02", "测试观众B", "666"),
        ("fake_u03", "测试观众C", "扣1"),
        ("fake_u04", "测试观众D", "画龙点睛"),
    ]
    uid, nickname, text = samples[(index - 1) % len(samples)]
    return {
        "uid": uid,
        "nickname": nickname,
        "avatar_url": "",
        "danmaku_text": text,
        "event_type": "danmaku",
    }


def run(args: argparse.Namespace) -> int:
    require_real_output_confirmation(
        real_output=args.real_output,
        confirmed=args.confirm_real_output,
    )
    client = HostedClient(args.base_url)
    log_path = Path(args.log).resolve()
    prepare_log_path(log_path, append=args.append, overwrite=args.overwrite)

    initial_context = client.context()
    require_safe_preflight(initial_context)
    initial_state = state_from_context(initial_context)
    initial_config = initial_state.get("config") if isinstance(initial_state.get("config"), dict) else {}
    initial_connection = (
        initial_state.get("live_connection")
        if isinstance(initial_state.get("live_connection"), dict)
        else {}
    )
    initially_connected = bool(initial_connection.get("connected"))
    initial_room = str(
        initial_connection.get("room_ref")
        or initial_connection.get("room_id")
        or initial_config.get("live_room_ref")
        or initial_config.get("live_room_id")
        or ""
    )
    restore_config = {key: initial_config.get(key) for key in RESTORE_KEYS if key in initial_config}

    write_jsonl(log_path, {"type": "initial_context", "time": now_iso(), "summary": summarize_context(initial_context)})
    print(f"[pressure] log={log_path}")
    print(f"[pressure] initial={json.dumps(summarize_context(initial_context), ensure_ascii=False)}")

    test_config = {
        "live_enabled": True,
        "live_mode": "solo_stream",
        "dry_run": not args.real_output,
        "developer_tools_enabled": True,
        "rate_limit_seconds": args.rate_limit,
        "queue_limit": 5,
        "activity_level": "active",
        "stream_theme": args.theme,
        "stream_goal": "在没有观众接话时保持轻短、自然、不复读；每句话都给下一句留下明确接法。",
        "stream_columns": "小二选一、轻吐槽、成语接龙、日常小观察",
        "stream_avoid_topics": "不要提展览；不要提未开播前私聊；独播时不要提操作员或主播本人；不要输出括号动作。",
    }
    room = args.room or str(initial_config.get("live_room_ref") or initial_config.get("live_room_id") or "")
    if args.connect and not room:
        raise PressureError("--connect requires --room or an existing configured room")

    outputs: list[str] = []
    risk_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    action_counts: dict[str, int] = {}

    try:
        config_response = require_action_success("update_config", client.action("update_config", test_config))
        write_jsonl(log_path, {"type": "configure", "time": now_iso(), "response": config_response})
        connected_by_script = False
        if args.connect:
            if initially_connected and room and room != initial_room:
                raise PressureError("refusing to replace a listener not owned by this pressure process")
            if not initially_connected:
                connect_response = require_action_success(
                    "connect_live_room",
                    client.action(
                        "connect_live_room",
                        {
                            "room_id": room,
                            "allow_accountless": bool(args.allow_accountless),
                        },
                    ),
                )
                connected_by_script = True
                write_jsonl(
                    log_path,
                    {
                        "type": "connect_live_room",
                        "time": now_iso(),
                        "room": room,
                        "response": connect_response,
                    },
                )
                wait_context = wait_for_connection(
                    client,
                    room=room,
                    timeout=args.connect_timeout,
                )
                write_jsonl(
                    log_path,
                    {
                        "type": "connect_ready",
                        "time": now_iso(),
                        "summary": summarize_context(wait_context),
                    },
                )

        actions = ["trigger_warmup_hosting"]
        pattern = ["trigger_active_engagement", "trigger_idle_hosting"]
        actions.extend(pattern[index % len(pattern)] for index in range(max(0, args.cycles - 1)))

        fake_points = parse_fake_signal_points(args.fake_signal_at)

        for index, action_id in enumerate(actions, start=1):
            if index in fake_points:
                signal_args = fake_signal_payload(index)
                signal_response = client.run_entry("submit_live_event", signal_args)
                write_jsonl(
                    log_path,
                    {
                        "type": "fake_signal",
                        "time": now_iso(),
                        "index": index,
                        "args": signal_args,
                        "response": signal_response,
                    },
                )
                status = str(signal_response.get("record", {}).get("status") or "unknown")
                print(f"[fake@{index:02d}] {signal_args['nickname']} :: {signal_args['danmaku_text']} run={status}")
            started = time.monotonic()
            try:
                response = client.action(action_id)
                compact = compact_result(action_id, response)
                output = str(compact.get("output") or "")
                flags = risk_flags(output, outputs)
                if output:
                    outputs.append(output)
                for flag in flags:
                    risk_counts[flag] = risk_counts.get(flag, 0) + 1
                status = str(compact.get("status") or "unknown")
                status_counts[status] = status_counts.get(status, 0) + 1
                action_counts[action_id] = action_counts.get(action_id, 0) + 1
                record = {
                    "type": "action_result",
                    "time": now_iso(),
                    "index": index,
                    "elapsed_ms": round((time.monotonic() - started) * 1000),
                    "result": compact,
                    "risk_flags": flags,
                }
                write_jsonl(log_path, record)
                visible = output.replace("\n", " ")[:120] if output else f"<{compact.get('reason')}>"
                print(f"[{index:02d}/{len(actions):02d}] {action_id} {status} flags={flags} :: {visible}")
            except Exception as exc:  # noqa: BLE001
                write_jsonl(
                    log_path,
                    {
                        "type": "action_error",
                        "time": now_iso(),
                        "index": index,
                        "action": action_id,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
                print(f"[{index:02d}/{len(actions):02d}] {action_id} ERROR {type(exc).__name__}: {exc}")
            if index < len(actions):
                time.sleep(args.interval)

        final_context = client.context()
        summary = {
            "type": "summary",
            "time": now_iso(),
            "cycles": len(actions),
            "status_counts": status_counts,
            "action_counts": action_counts,
            "risk_counts": risk_counts,
            "outputs": len(outputs),
            "unique_outputs": len({normalize_text(output) for output in outputs}),
            "final_context": summarize_context(final_context),
        }
        write_jsonl(log_path, summary)
        print(f"[pressure] summary={json.dumps(summary, ensure_ascii=False)}")
    finally:
        cleanup_error: PressureError | None = None
        primary_exception_active = sys.exc_info()[0] is not None
        if args.restore and "connected_by_script" in locals() and connected_by_script:
            try:
                if disconnect_owned_connection(client, owned_room=room):
                    write_jsonl(log_path, {"type": "disconnect_live_room", "time": now_iso(), "room": room})
                    print("[pressure] disconnected listener owned by this pressure process")
            except PressureError as exc:
                write_jsonl(
                    log_path,
                    {
                        "type": "disconnect_error",
                        "time": now_iso(),
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
                cleanup_error = exc
        if args.restore:
            try:
                restored, skipped = compare_and_restore_config(
                    client,
                    initial_config=restore_config,
                    applied_config=test_config,
                )
                write_jsonl(log_path, {"type": "restore", "time": now_iso(), "restore_config": restored, "skipped": skipped})
                print(f"[pressure] restored={json.dumps(restored, ensure_ascii=False)} skipped={skipped}")
            except PressureError as exc:
                cleanup_error = cleanup_error or exc
        finalize_cleanup_failure(
            cleanup_error,
            primary_exception_active=primary_exception_active,
            record_event=lambda event: write_jsonl(log_path, event),
            timestamp=now_iso,
            warning_prefix="[pressure]",
        )

    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Exit codes: 0 success, 2 CLI usage, 3 preflight refused, 4 connection timeout, 5 run failure, 6 restore failure.",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:48916")
    parser.add_argument("--cycles", type=int, default=12)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--rate-limit", type=float, default=1.0)
    parser.add_argument("--room", default="")
    parser.add_argument("--connect", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument(
        "--allow-accountless",
        action="store_true",
        help="Explicitly allow Bilibili's limited accountless fallback for this connection.",
    )
    parser.add_argument("--connect-timeout", type=float, default=15.0)
    parser.add_argument(
        "--fake-signal-at",
        default="",
        help="Comma-separated action indexes before which fake live danmaku is injected.",
    )
    parser.add_argument(
        "--real-output",
        action="store_true",
        help="Push real output through NEKO instead of dry-run summaries.",
    )
    parser.add_argument(
        "--confirm-real-output",
        action="store_true",
        help="Second explicit confirmation required together with --real-output.",
    )
    parser.add_argument(
        "--theme",
        default="轻松闲聊：观众暂时不说话时，用短句自然抛出二选一、小接龙或生活观察。",
    )
    parser.add_argument(
        "--log",
        default=str(Path("_local_artifacts") / "neko_live" / "pressure" / "live_silence_pressure.jsonl"),
    )
    log_mode = parser.add_mutually_exclusive_group()
    log_mode.add_argument("--append", action="store_true", help="Append to an existing pressure log.")
    log_mode.add_argument("--overwrite", action="store_true", help="Explicitly truncate an existing pressure log.")
    parser.add_argument("--no-restore", dest="restore", action="store_false")
    parser.set_defaults(restore=True)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    try:
        return run(parse_args(argv))
    except PressureError as exc:
        print(f"[pressure] ERROR: {exc}", file=sys.stderr)
        return exc.exit_code
    except Exception as exc:  # noqa: BLE001
        print(f"[pressure] ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_RUN


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
