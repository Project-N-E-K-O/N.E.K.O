"""Simulate many live danmaku events through the running neko_roast plugin.

The script uses the same local plugin chain as a real live event:
/runs -> submit_live_event -> live provider normalization -> pipeline.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import random
import sys
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any

import requests

from plugin.plugins.neko_roast.tools.pressure_guard import (
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


PLUGIN_ID = "neko_roast"
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
_THREAD_LOCAL = threading.local()

DANMAKU_SAMPLES = (
    "\u732b\u732b\u4eca\u5929\u72b6\u6001\u4e0d\u9519",
    "\u62631",
    "1",
    "2",
    "666",
    "111111",
    "\u8fd9\u4e2a\u6e38\u620f\u6211\u4e5f\u73a9\u8fc7",
    "\u732b\u732b\u8bb2\u4e2a\u7b11\u8bdd",
    "\u4e0b\u4e00\u4e2a\u8bdd\u9898\u662f\u4ec0\u4e48",
    "\u6211\u521a\u8fdb\u6765",
    "\u6210\u8bed\u63a5\u9f99\uff1a\u753b\u9f99\u70b9\u775b",
    "\u559d\u6c34\u559d\u6c34",
    "\u8fd9\u53e5\u63a5\u5f97\u597d",
    "\u54c8\u54c8\u54c8",
    "\u4eca\u5929\u4eba\u597d\u591a",
    "\u8fd9\u4e2a\u4e8c\u9009\u4e00\u6709\u70b9\u96be",
    "\u522b\u590d\u8bfb\u5566",
    "\u732b\u732b\u770b\u5230\u6211\u4e86\u5417",
    "\u6211\u9009A",
    "\u6211\u9009B",
)


def request_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 90.0,
) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    kwargs: dict[str, Any] = {"headers": headers, "timeout": timeout}
    if payload is not None:
        headers["Content-Type"] = "application/json"
        kwargs["data"] = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    session = _thread_session()
    try:
        response = session.request(method, url, **kwargs)
        if response.status_code >= 400:
            raise RuntimeError(f"{method} {url} -> HTTP {response.status_code}: {response.text}")
        return response.json() if response.text else {}
    except requests.RequestException as exc:
        raise RuntimeError(f"{method} {url} -> {type(exc).__name__}: {exc}") from exc


def _thread_session() -> requests.Session:
    session = getattr(_THREAD_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        session.trust_env = False
        _THREAD_LOCAL.session = session
    return session


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

    def create_entry_run(
        self,
        entry_id: str,
        args: dict[str, Any],
        *,
        timeout: float = 45.0,
    ) -> tuple[dict[str, Any], str]:
        payload = {"plugin_id": PLUGIN_ID, "entry_id": entry_id, "args": args}
        created = request_json("POST", f"{self.base_url}/runs", payload, timeout=timeout)
        run_id = str(created.get("run_id") or "")
        return created, run_id

    def collect_entry_run(
        self,
        created: dict[str, Any],
        run_id: str,
        *,
        timeout: float = 45.0,
    ) -> dict[str, Any]:
        if not run_id:
            return {"created": created, "record": {}, "data": {}}
        deadline = time.monotonic() + timeout
        record: dict[str, Any] = {}
        while time.monotonic() < deadline:
            record = request_json("GET", f"{self.base_url}/runs/{run_id}", timeout=timeout)
            if str(record.get("status") or "") in {"succeeded", "failed", "cancelled"}:
                break
            time.sleep(0.05)
        data = export_data(self.base_url, run_id)
        return {"created": created, "record": record, "data": data}

    def run_entry(self, entry_id: str, args: dict[str, Any], *, timeout: float = 45.0) -> dict[str, Any]:
        created, run_id = self.create_entry_run(entry_id, args, timeout=timeout)
        return self.collect_entry_run(created, run_id, timeout=timeout)


def export_data(base_url: str, run_id: str) -> dict[str, Any]:
    export = request_json("GET", f"{base_url}/runs/{run_id}/export", timeout=45.0)
    items = export.get("items") if isinstance(export.get("items"), list) else []
    for item in items:
        if not isinstance(item, dict):
            continue
        payload = item.get("json")
        if not isinstance(payload, dict):
            continue
        data = payload.get("data")
        if isinstance(data, dict):
            return data
    return {}


def state_from_context(context: dict[str, Any]) -> dict[str, Any]:
    state = context.get("state")
    return state if isinstance(state, dict) else {}


def summarize_context(context: dict[str, Any]) -> dict[str, Any]:
    state = state_from_context(context)
    config = state.get("config") if isinstance(state.get("config"), dict) else {}
    live_status = state.get("live_status") if isinstance(state.get("live_status"), dict) else {}
    live_state = state.get("live_state") if isinstance(state.get("live_state"), dict) else {}
    return {
        "config": {
            "live_enabled": config.get("live_enabled"),
            "live_mode": config.get("live_mode"),
            "dry_run": config.get("dry_run"),
            "rate_limit_seconds": config.get("rate_limit_seconds"),
            "stream_theme": config.get("stream_theme"),
        },
        "live_status": {
            "summary": live_status.get("summary"),
            "reason": live_status.get("reason"),
            "can_output": live_status.get("can_output"),
        },
        "live_state": {
            "reason": live_state.get("reason"),
            "last_viewer_activity_age_sec": live_state.get("last_viewer_activity_age_sec"),
            "last_output_age_sec": live_state.get("last_output_age_sec"),
        },
    }


def build_test_config(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "live_enabled": True,
        "live_mode": "solo_stream",
        "dry_run": not args.real_output,
        "developer_tools_enabled": True,
        "rate_limit_seconds": args.rate_limit,
        "queue_limit": args.queue_limit,
        "activity_level": "active",
        "stream_theme": args.theme,
        "stream_goal": "\u9ad8\u4eba\u6d41\u91cf\u538b\u6d4b\uff1a\u4f18\u5148\u7a33\u5b9a\u3001\u77ed\u53e5\u3001\u4e0d\u590d\u8bfb\uff0c\u4e0d\u9700\u8981\u56de\u6bcf\u4e2a\u4eba\u3002",
        "stream_columns": "\u77ed\u7b54\u3001\u4e8c\u9009\u4e00\u3001\u8f7b\u5410\u69fd\u3001\u70b9\u540d\u5c11\u91cf\u89c2\u4f17",
        "stream_avoid_topics": "\u4e0d\u8981\u63d0\u5c55\u89c8\uff1b\u4e0d\u8981\u8ffd\u6bcf\u6761\u5f39\u5e55\uff1b\u4e0d\u8981\u8f93\u51fa\u62ec\u53f7\u52a8\u4f5c\u3002",
    }


def make_event(index: int, rng: random.Random, *, user_count: int) -> dict[str, Any]:
    user_no = rng.randrange(1, user_count + 1)
    text = rng.choice(DANMAKU_SAMPLES)
    return {
        "uid": f"mass_u{user_no:05d}",
        "nickname": f"\u538b\u6d4b\u89c2\u4f17{user_no:05d}",
        "avatar_url": "",
        "danmaku_text": text,
        "event_type": "danmaku",
        "sequence": index,
    }


def compact_run_result(index: int, args: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    event = data.get("event") if isinstance(data.get("event"), dict) else {}
    record = response.get("record") if isinstance(response.get("record"), dict) else {}
    steps = data.get("steps") if isinstance(data.get("steps"), list) else []
    return {
        "type": "event_result",
        "time": now_iso(),
        "index": index,
        "run_status": record.get("status"),
        "accepted": data.get("accepted"),
        "status": data.get("status"),
        "reason": data.get("reason"),
        "uid": args.get("uid"),
        "nickname": args.get("nickname"),
        "text": args.get("danmaku_text"),
        "source": event.get("source"),
        "output": data.get("output") or "",
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


def submit_one(base_url: str, index: int, args: dict[str, Any]) -> dict[str, Any]:
    client = HostedClient(base_url)
    last_error = ""
    created: dict[str, Any] = {}
    run_id = ""
    attempt = 0
    for attempt in range(1, 4):
        try:
            created, run_id = client.create_entry_run("submit_live_event", args)
            break
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(0.1 * attempt)
    else:
        return {
            "type": "event_error",
            "time": now_iso(),
            "index": index,
            "uid": args.get("uid"),
            "nickname": args.get("nickname"),
            "text": args.get("danmaku_text"),
            "error": last_error,
        }

    try:
        response = client.collect_entry_run(created, run_id)
        result = compact_run_result(index, args, response)
        if attempt > 1:
            result["attempts"] = attempt
        return result
    except Exception as exc:  # noqa: BLE001
        last_error = f"{type(exc).__name__}: {exc}"
    return {
        "type": "event_error",
        "time": now_iso(),
        "index": index,
        "uid": args.get("uid"),
        "nickname": args.get("nickname"),
        "text": args.get("danmaku_text"),
        "run_id": run_id,
        "accepted": bool(run_id),
        "attempts": attempt,
        "error": last_error,
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
    restore_config = {key: initial_config.get(key) for key in RESTORE_KEYS if key in initial_config}
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
    write_jsonl(log_path, {"type": "initial_context", "time": now_iso(), "summary": summarize_context(initial_context)})
    print(f"[mass] log={log_path}")
    print(f"[mass] initial={json.dumps(summarize_context(initial_context), ensure_ascii=False)}")

    test_config = build_test_config(args)
    room = args.room or str(initial_config.get("live_room_ref") or initial_config.get("live_room_id") or "")
    if args.connect and not room:
        raise PressureError("--connect requires --room or an existing configured room")

    connected_by_script = False
    try:
        require_action_success("update_config", client.action("update_config", test_config))
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
                write_jsonl(log_path, {"type": "connect_live_room", "time": now_iso(), "room": room, "response": connect_response})
                context = wait_for_connection(client, room=room, timeout=args.connect_timeout)
                write_jsonl(log_path, {"type": "connect_ready", "time": now_iso(), "summary": summarize_context(context)})

        rng = random.Random(args.seed)
        events = [make_event(index, rng, user_count=args.users) for index in range(1, args.events + 1)]
        started = time.monotonic()
        status_counts: dict[str, int] = {}
        reason_counts: dict[str, int] = {}
        error_count = 0
        output_count = 0
        accepted_count = 0
        pushed_outputs: list[str] = []

        def collect_result(result: dict[str, Any]) -> None:
            nonlocal accepted_count, error_count, output_count
            write_jsonl(log_path, result)
            if result.get("type") == "event_error":
                error_count += 1
                return
            status = str(result.get("status") or "unknown")
            reason = str(result.get("reason") or "")
            status_counts[status] = status_counts.get(status, 0) + 1
            if reason:
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
            if result.get("accepted"):
                accepted_count += 1
            output = str(result.get("output") or "")
            if output:
                output_count += 1
                if len(pushed_outputs) < 20:
                    pushed_outputs.append(output)

        def print_progress(completed: int) -> None:
            elapsed = max(0.001, time.monotonic() - started)
            print(
                "[mass] "
                f"{completed}/{args.events} "
                f"{completed / elapsed:.1f}/s "
                f"statuses={json.dumps(status_counts, ensure_ascii=False)} "
                f"errors={error_count}"
            )

        completed = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            if args.burst_size > 0:
                wave_no = 0
                for start in range(0, len(events), args.burst_size):
                    wave_no += 1
                    wave = events[start : start + args.burst_size]
                    write_jsonl(
                        log_path,
                        {
                            "type": "burst_start",
                            "time": now_iso(),
                            "wave": wave_no,
                            "start_index": start + 1,
                            "size": len(wave),
                        },
                    )
                    futures = {
                        executor.submit(
                            submit_one,
                            args.base_url.rstrip("/"),
                            start + offset,
                            event,
                        ): start + offset
                        for offset, event in enumerate(wave, start=1)
                    }
                    for future in concurrent.futures.as_completed(futures):
                        collect_result(future.result())
                        completed += 1
                        if completed % max(1, args.progress_every) == 0 or completed == args.events:
                            print_progress(completed)
                    if completed < args.events:
                        gap = rng.uniform(args.burst_gap_min, args.burst_gap_max)
                        write_jsonl(
                            log_path,
                            {
                                "type": "burst_gap",
                                "time": now_iso(),
                                "wave": wave_no,
                                "completed": completed,
                                "gap_sec": round(gap, 3),
                            },
                        )
                        time.sleep(max(0.0, gap))
            else:
                futures = {
                    executor.submit(submit_one, args.base_url.rstrip("/"), index, event): index
                    for index, event in enumerate(events, start=1)
                }
                for future in concurrent.futures.as_completed(futures):
                    collect_result(future.result())
                    completed += 1
                    if completed % max(1, args.progress_every) == 0 or completed == args.events:
                        print_progress(completed)

        final_context = client.context()
        summary = {
            "type": "summary",
            "time": now_iso(),
            "events": args.events,
            "users": args.users,
            "concurrency": args.concurrency,
            "elapsed_sec": round(time.monotonic() - started, 2),
            "accepted_count": accepted_count,
            "output_count": output_count,
            "error_count": error_count,
            "status_counts": status_counts,
            "reason_counts": dict(sorted(reason_counts.items(), key=lambda item: item[1], reverse=True)[:20]),
            "sample_outputs": pushed_outputs,
            "final_context": summarize_context(final_context),
        }
        write_jsonl(log_path, summary)
        print(f"[mass] summary={json.dumps(summary, ensure_ascii=False)}")
    finally:
        cleanup_error: PressureError | None = None
        primary_exception_active = sys.exc_info()[0] is not None
        if args.restore and connected_by_script:
            try:
                if disconnect_owned_connection(client, owned_room=room):
                    print("[mass] disconnected listener owned by this pressure process")
            except PressureError as exc:
                write_jsonl(log_path, {"type": "disconnect_error", "time": now_iso(), "error": f"{type(exc).__name__}: {exc}"})
                cleanup_error = exc
        if args.restore:
            try:
                restored, skipped = compare_and_restore_config(
                    client,
                    initial_config=restore_config,
                    applied_config=test_config,
                )
                print(f"[mass] restored={json.dumps(restored, ensure_ascii=False)} skipped={skipped}")
            except PressureError as exc:
                cleanup_error = cleanup_error or exc
        finalize_cleanup_failure(
            cleanup_error,
            primary_exception_active=primary_exception_active,
            record_event=lambda event: write_jsonl(log_path, event),
            timestamp=now_iso,
            warning_prefix="[mass]",
        )

    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Exit codes: 0 success, 2 CLI usage, 3 preflight refused, 4 connection timeout, 5 run failure, 6 restore failure.",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:48916")
    parser.add_argument("--events", type=int, default=2000)
    parser.add_argument("--users", type=int, default=2000)
    parser.add_argument("--concurrency", type=int, default=12)
    parser.add_argument("--seed", type=int, default=20260709)
    parser.add_argument("--rate-limit", type=float, default=1.0)
    parser.add_argument("--queue-limit", type=int, default=5)
    parser.add_argument("--room", default="")
    parser.add_argument("--connect", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument(
        "--allow-accountless",
        action="store_true",
        help="Explicitly allow Bilibili's limited accountless fallback for this connection.",
    )
    parser.add_argument("--connect-timeout", type=float, default=15.0)
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
    parser.add_argument("--progress-every", type=int, default=100)
    parser.add_argument(
        "--burst-size",
        type=int,
        default=0,
        help="Submit events in waves of this size; 0 submits all events at once.",
    )
    parser.add_argument("--burst-gap-min", type=float, default=0.4)
    parser.add_argument("--burst-gap-max", type=float, default=1.2)
    parser.add_argument(
        "--theme",
        default="\u9ad8\u4eba\u6d41\u91cf\u95f2\u804a\u538b\u6d4b\uff1a\u89c2\u4f17\u5f88\u591a\u3001\u5f39\u5e55\u5f88\u5feb\uff0c\u732b\u732b\u53ea\u6311\u6709\u8da3\u7684\u77ed\u63a5\u3002",
    )
    parser.add_argument(
        "--log",
        default=str(Path("_local_artifacts") / "neko_roast" / "pressure" / "live_random_danmaku_pressure.jsonl"),
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
        print(f"[mass] ERROR: {exc}", file=sys.stderr)
        return exc.exit_code
    except Exception as exc:  # noqa: BLE001
        print(f"[mass] ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_RUN


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
