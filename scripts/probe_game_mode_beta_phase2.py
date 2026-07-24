#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Probe helper for Game Mode Beta Phase 2 manual acceptance.

The script is intentionally read-only by default. It queries the running
desktop backend and prints a compact report that can be pasted into the
Phase 2 acceptance record.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


FetchResult = dict[str, Any]
Fetcher = Callable[[str, str, float], FetchResult]
DEFAULT_CANDIDATE_PORTS = (48911, 48912, 48920, 14514, 3000, 8080, 5173)


@dataclass(frozen=True)
class ProbeOptions:
    base_url: str = "auto"
    timeout: float = 3.0
    expect_default_off: bool = False
    check_debug_health: bool = False
    monitor_seconds: float = 0.0
    monitor_interval: float = 5.0
    debug_health_log_path: str | None = None


def _join_url(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + "/" + path.lstrip("/")


def candidate_base_urls(base_url: str) -> tuple[str, ...]:
    if base_url and base_url.strip().lower() != "auto":
        return (base_url.strip(),)

    candidates: list[str] = []
    for env_name in ("NEKO_GAME_MODE_PROBE_URL", "NEKO_BACKEND_URL", "NEKO_ORIGINAL_URL"):
        value = os.environ.get(env_name, "").strip()
        if value:
            candidates.append(value)
    candidates.extend(f"http://127.0.0.1:{port}" for port in DEFAULT_CANDIDATE_PORTS)

    seen: set[str] = set()
    unique: list[str] = []
    for candidate in candidates:
        normalized = candidate.rstrip("/")
        if normalized and normalized not in seen:
            seen.add(normalized)
            unique.append(normalized)
    return tuple(unique)


def fetch_json(base_url: str, path: str, timeout: float) -> FetchResult:
    url = _join_url(base_url, path)
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            data = json.loads(raw) if raw else None
            return {"ok": True, "status": response.status, "data": data, "url": url}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            data: Any = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            data = raw
        return {"ok": False, "status": exc.code, "data": data, "url": url}
    except Exception as exc:
        return {"ok": False, "status": None, "error": str(exc), "url": url}


def normalize_game_mode_state(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    state = payload.get("state") if payload.get("success") is True else payload
    return state if isinstance(state, dict) else None


def summarize_state(state: dict[str, Any] | None) -> str:
    if not state:
        return "state unavailable"
    return (
        f"enabled={state.get('enabled')}, "
        f"pressure_state={state.get('pressure_state')}, "
        f"resource_session_phase={state.get('resource_session_phase')}, "
        f"resource_session_id={state.get('resource_session_id')}, "
        f"registered_window_count={state.get('registered_window_count')}"
    )


def _check_default_off(state: dict[str, Any] | None) -> list[str]:
    failures: list[str] = []
    if state is None:
        return ["game mode state endpoint did not return a state object"]
    if state.get("enabled") is not False:
        failures.append("expected enabled=false after restart")
    if state.get("pressure_state") != "normal":
        failures.append("expected pressure_state=normal after restart")
    if state.get("resource_session_phase") != "idle":
        failures.append("expected resource_session_phase=idle after restart")
    if state.get("resource_session_id") is not None:
        failures.append("expected resource_session_id=null after restart")
    return failures


def _debug_health_game_mode(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    game_mode = payload.get("game_mode_beta")
    return game_mode if isinstance(game_mode, dict) else None


def inspect_debug_health_log(path_text: str) -> dict[str, Any]:
    path = Path(path_text).expanduser()
    rotated = path.parent / (path.name + ".1")
    result: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "size_bytes": None,
        "line_count": 0,
        "latest_has_game_mode_beta": False,
        "rotated_path": str(rotated),
        "rotated_exists": rotated.exists(),
        "rotated_size_bytes": None,
    }
    if rotated.exists():
        try:
            result["rotated_size_bytes"] = rotated.stat().st_size
        except OSError as exc:
            result["rotated_error"] = str(exc)
    if not path.exists():
        return result

    try:
        result["size_bytes"] = path.stat().st_size
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        result["line_count"] = len(lines)
    except OSError as exc:
        result["error"] = str(exc)
        return result

    for raw_line in reversed(lines[-50:]):
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and isinstance(payload.get("game_mode_beta"), dict):
            result["latest_has_game_mode_beta"] = True
            break
    return result


def resolve_backend(
    options: ProbeOptions,
    fetcher: Fetcher,
) -> tuple[str, FetchResult, list[str]]:
    failures: list[str] = []
    candidates = candidate_base_urls(options.base_url)
    last_response: FetchResult | None = None
    for candidate in candidates:
        response = fetcher(candidate, "/api/game-mode-beta/state", options.timeout)
        last_response = response
        state = normalize_game_mode_state(response.get("data")) if response.get("ok") else None
        if response.get("ok") and state is not None:
            return candidate, response, failures
        status = response.get("status") or response.get("error") or "no state"
        failures.append(f"{candidate}: {status}")

    fallback = candidates[0] if candidates else options.base_url
    if last_response is None:
        last_response = {"ok": False, "status": None, "error": "no backend candidates", "url": fallback}
    return fallback, last_response, failures


def run_probe(
    options: ProbeOptions,
    *,
    fetcher: Fetcher = fetch_json,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.time,
) -> dict[str, Any]:
    started_at = now()
    failures: list[str] = []
    observations: dict[str, Any] = {"started_at": started_at}

    selected_base_url, state_response, discovery_failures = resolve_backend(options, fetcher)
    observations["selected_base_url"] = selected_base_url
    observations["discovery_failures"] = discovery_failures
    observations["game_mode_state_response"] = state_response
    state = normalize_game_mode_state(state_response.get("data")) if state_response.get("ok") else None
    observations["game_mode_state"] = state
    if not state_response.get("ok"):
        failures.append(f"game mode state request failed: {state_response.get('status') or state_response.get('error')}")
    elif state is None:
        failures.append("game mode state endpoint did not return a state object")
    if options.expect_default_off and state is not None:
        failures.extend(_check_default_off(state))

    if options.check_debug_health or options.monitor_seconds > 0:
        health_response = fetcher(selected_base_url, "/api/debug/health", options.timeout)
        observations["debug_health_response"] = health_response
        game_mode = _debug_health_game_mode(health_response.get("data")) if health_response.get("ok") else None
        observations["debug_health_game_mode"] = game_mode
        if not health_response.get("ok"):
            failures.append(f"debug health request failed: {health_response.get('status') or health_response.get('error')}")
        elif game_mode is None:
            failures.append("debug health response missing game_mode_beta")

    monitor_samples: list[dict[str, Any]] = []
    deadline = started_at + max(0.0, options.monitor_seconds)
    while options.monitor_seconds > 0 and now() < deadline:
        response = fetcher(selected_base_url, "/api/debug/health", options.timeout)
        data = response.get("data") if response.get("ok") else None
        game_mode = _debug_health_game_mode(data)
        monitor_samples.append({
            "ok": response.get("ok") is True,
            "status": response.get("status"),
            "game_mode_beta_present": game_mode is not None,
            "enabled": game_mode.get("enabled") if game_mode else None,
            "pressure_state": game_mode.get("pressure_state") if game_mode else None,
            "sample_count": len(game_mode.get("last_samples") or []) if game_mode else None,
        })
        if game_mode is None:
            failures.append("monitor sample missing game_mode_beta")
        if options.monitor_interval > 0 and now() < deadline:
            sleep(min(options.monitor_interval, max(0.0, deadline - now())))
    observations["monitor_samples"] = monitor_samples

    log_info = None
    if options.debug_health_log_path:
        log_info = inspect_debug_health_log(options.debug_health_log_path)
        observations["debug_health_log"] = log_info
        if not log_info.get("exists"):
            failures.append(f"debug health log missing: {log_info.get('path')}")
        elif int(log_info.get("line_count") or 0) <= 0:
            failures.append(f"debug health log has no lines: {log_info.get('path')}")
        elif not log_info.get("latest_has_game_mode_beta"):
            failures.append("debug health log latest JSON lines missing game_mode_beta")

    return {
        "ok": not failures,
        "failures": failures,
        "observations": observations,
        "summary": {
            "selected_base_url": selected_base_url,
            "game_mode_state": summarize_state(state),
            "monitor_samples": len(monitor_samples),
            "debug_health_game_mode_present": observations.get("debug_health_game_mode") is not None,
            "debug_health_log": log_info,
        },
    }


def format_text_report(report: dict[str, Any]) -> str:
    lines = [
        "Game Mode Beta Phase 2 probe",
        f"result: {'PASS' if report.get('ok') else 'FAIL'}",
        f"base_url: {report.get('summary', {}).get('selected_base_url')}",
        f"state: {report.get('summary', {}).get('game_mode_state')}",
        f"debug_health_game_mode_present: {report.get('summary', {}).get('debug_health_game_mode_present')}",
        f"monitor_samples: {report.get('summary', {}).get('monitor_samples')}",
    ]
    log_info = report.get("summary", {}).get("debug_health_log")
    if isinstance(log_info, dict):
        lines.append(
            "debug_health_log: "
            f"path={log_info.get('path')}, "
            f"exists={log_info.get('exists')}, "
            f"lines={log_info.get('line_count')}, "
            f"size_bytes={log_info.get('size_bytes')}, "
            f"latest_has_game_mode_beta={log_info.get('latest_has_game_mode_beta')}, "
            f"rotated_exists={log_info.get('rotated_exists')}, "
            f"rotated_size_bytes={log_info.get('rotated_size_bytes')}"
        )
    failures = report.get("failures") or []
    if failures:
        lines.append("failures:")
        lines.extend(f"- {failure}" for failure in failures)
    return "\n".join(lines)


def parse_args(argv: list[str]) -> ProbeOptions:
    parser = argparse.ArgumentParser(description="Probe Game Mode Beta Phase 2 runtime acceptance state.")
    parser.add_argument("--base-url", default="auto", help="Running N.E.K.O backend base URL, or 'auto' to scan common local ports.")
    parser.add_argument("--timeout", type=float, default=3.0, help="HTTP timeout in seconds.")
    parser.add_argument("--expect-default-off", action="store_true", help="Fail unless game mode is off with a clean state.")
    parser.add_argument("--check-debug-health", action="store_true", help="Check /api/debug/health contains game_mode_beta.")
    parser.add_argument("--monitor-seconds", type=float, default=0.0, help="Poll debug health for this many seconds.")
    parser.add_argument("--monitor-interval", type=float, default=5.0, help="Seconds between monitor samples.")
    parser.add_argument("--debug-health-log-path", default=None, help="Optional debug_health.jsonl path to inspect for game_mode_beta and rotation summary.")
    args = parser.parse_args(argv)
    return ProbeOptions(
        base_url=args.base_url,
        timeout=args.timeout,
        expect_default_off=args.expect_default_off,
        check_debug_health=args.check_debug_health,
        monitor_seconds=args.monitor_seconds,
        monitor_interval=args.monitor_interval,
        debug_health_log_path=args.debug_health_log_path,
    )


def main(argv: list[str] | None = None) -> int:
    options = parse_args(list(argv or sys.argv[1:]))
    report = run_probe(options)
    print(format_text_report(report))
    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
