#!/usr/bin/env python3
"""Reproduce and classify the Agent browser-control failure chain.

Default mode is read-only:
  - probe localhost main/agent ports and health endpoints
  - inspect browser-use Chrome/Chromium discovery
  - inspect Agent model config readiness with secrets redacted

Opt-in probes:
  - --probe-cua-image sends one real Agent model request with the same
    image_url payload shape used by ComputerUseAdapter.
  - --launch-browser-use starts BrowserUseAdapter and opens a real browser.
  - --watch-health polls health endpoints while a manual task is reproduced.

Examples:
  python scripts/repro_agent_control_chain.py
  python scripts/repro_agent_control_chain.py --watch-health 120
  python scripts/repro_agent_control_chain.py --probe-cua-image
  python scripts/repro_agent_control_chain.py --launch-browser-use
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import json
import os
import socket
import struct
import sys
import time
import traceback
import urllib.error
import urllib.request
import zlib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@dataclass
class ProbeResult:
    name: str
    status: str
    detail: str
    data: dict[str, Any] = field(default_factory=dict)


def _redact_secret(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= 8:
        return "<redacted>"
    return f"{text[:4]}...{text[-4:]}"


def _read_port_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = int(raw)
        if 1 <= value <= 65535:
            return value
    except ValueError:
        pass
    return default


def _short_error(exc: BaseException) -> str:
    return f"{exc.__class__.__name__}: {exc}"


def _http_json(url: str, timeout: float = 2.0) -> tuple[bool, int | None, Any, str]:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            text = raw.decode("utf-8", errors="replace")
            try:
                body: Any = json.loads(text)
            except json.JSONDecodeError:
                body = text[:1000]
            return True, resp.status, body, ""
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        text = raw.decode("utf-8", errors="replace")
        try:
            body = json.loads(text)
        except json.JSONDecodeError:
            body = text[:1000]
        return False, exc.code, body, _short_error(exc)
    except Exception as exc:
        return False, None, None, _short_error(exc)


def _port_open(host: str, port: int, timeout: float = 1.0) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, "tcp connect ok"
    except Exception as exc:
        return False, _short_error(exc)


def _classify_error(text: str) -> str:
    lower = text.lower()
    if "unknown variant" in lower and "image_url" in lower and "expected text" in lower:
        return "cua_image_url_schema_rejected"
    if "image_url" in lower and ("not supported" in lower or "unsupported" in lower):
        return "cua_image_url_not_supported"
    if "chrome browser not found" in lower or "chrome / chromium" in lower or "\u672a\u627e\u5230 chrome" in lower:
        return "browser_use_chrome_not_found"
    if "connection refused" in lower or "errno 111" in lower or "winerror 10061" in lower:
        return "localhost_connection_refused"
    return "unclassified"


def probe_health(timeout: float) -> list[ProbeResult]:
    main_port = _read_port_env("MAIN_SERVER_PORT", 48911)
    tool_port = _read_port_env("TOOL_SERVER_PORT", 48915)
    probes = [
        ("main", main_port, "/health"),
        ("agent", tool_port, "/health"),
        ("agent_flags", tool_port, "/agent/flags"),
    ]
    results: list[ProbeResult] = []
    for label, port, path in probes:
        tcp_ok, tcp_detail = _port_open("127.0.0.1", int(port), timeout=min(timeout, 1.0))
        url = f"http://127.0.0.1:{port}{path}"
        http_ok, status, body, error = _http_json(url, timeout=timeout)
        if http_ok:
            state = "PASS"
            detail = f"{url} -> HTTP {status}"
        elif tcp_ok:
            state = "WARN"
            detail = f"{url} failed but port is open: {error}"
        else:
            state = "FAIL"
            detail = f"{url} unreachable: {tcp_detail}"
        results.append(
            ProbeResult(
                name=f"health:{label}",
                status=state,
                detail=detail,
                data={
                    "url": url,
                    "port": int(port),
                    "tcp_open": tcp_ok,
                    "http_status": status,
                    "body": body,
                    "error": error,
                    "classification": _classify_error(f"{tcp_detail} {error}"),
                },
            )
        )
    return results


def probe_browser_paths() -> ProbeResult:
    data: dict[str, Any] = {
        "PLAYWRIGHT_BROWSERS_PATH": os.environ.get("PLAYWRIGHT_BROWSERS_PATH", ""),
    }
    try:
        from brain.browser_use_adapter import (
            _find_bundled_chromium,
            _find_chrome_path,
            _find_system_chrome_path,
        )

        bundled = _find_bundled_chromium()
        system = _find_system_chrome_path()
        effective = _find_chrome_path()
        data.update(
            {
                "bundled_chromium": bundled,
                "system_chrome": system,
                "effective_chrome": effective,
                "effective_exists": bool(effective and os.path.isfile(effective)),
            }
        )
        if effective and os.path.isfile(effective):
            return ProbeResult("browser_use:chrome_discovery", "PASS", f"Chrome found: {effective}", data)
        return ProbeResult(
            "browser_use:chrome_discovery",
            "FAIL",
            "No usable Chrome/Chromium executable found by BrowserUseAdapter preflight",
            data,
        )
    except Exception as exc:
        data["traceback"] = traceback.format_exc()
        return ProbeResult("browser_use:chrome_discovery", "FAIL", _short_error(exc), data)


def probe_agent_config() -> ProbeResult:
    try:
        cfg = _load_agent_config_readonly()
        reasons = []
        if not (cfg.get("model") or "").strip():
            reasons.append("Agent model is missing")
        if not (cfg.get("base_url") or "").strip():
            reasons.append("Agent base_url is missing")
        if not (cfg.get("api_key") or "").strip():
            reasons.append("Agent api_key is missing")
        ready = not reasons
        redacted = {
            "model": cfg.get("model", ""),
            "base_url": cfg.get("base_url", ""),
            "api_key": _redact_secret(cfg.get("api_key", "")),
            "assist_api": cfg.get("assist_api", ""),
            "source_file": cfg.get("source_file", ""),
        }
        status = "PASS" if ready else "FAIL"
        detail = "Agent API config is ready" if ready else "; ".join(reasons)
        return ProbeResult("agent:config", status, detail, {"config": redacted, "reasons": reasons})
    except Exception as exc:
        return ProbeResult(
            "agent:config",
            "FAIL",
            _short_error(exc),
            {"traceback": traceback.format_exc()},
        )


def _candidate_core_config_paths() -> list[Path]:
    paths: list[Path] = []
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        paths.append(Path(xdg) / "N.E.K.O" / "config" / "core_config.json")
    paths.append(Path.home() / ".local" / "share" / "N.E.K.O" / "config" / "core_config.json")
    paths.append(PROJECT_ROOT / "backup" / "config" / "core_config.json")
    paths.append(PROJECT_ROOT / "config" / "core_config.json")
    return paths


def _load_agent_config_readonly() -> dict[str, Any]:
    from config import DEFAULT_ASSIST_API_PROFILES, DEFAULT_CONFIG_DATA

    core_cfg = dict(DEFAULT_CONFIG_DATA.get("core_config.json", {}))
    source_file = ""
    for path in _candidate_core_config_paths():
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                core_cfg.update(loaded)
                source_file = str(path)
                break

    core_api = core_cfg.get("coreApi") or "qwen"
    assist_api = core_cfg.get("assistApi") or ("free" if core_api == "free" else "qwen")
    profiles = dict(DEFAULT_ASSIST_API_PROFILES)
    profile = dict(profiles.get(assist_api) or profiles.get("qwen") or {})
    if assist_api not in profiles:
        assist_api = "qwen"

    key_fields = {
        "free": None,
        "qwen": "assistApiKeyQwen",
        "qwen_intl": "assistApiKeyQwenIntl",
        "openai": "assistApiKeyOpenai",
        "glm": "assistApiKeyGlm",
        "step": "assistApiKeyStep",
        "silicon": "assistApiKeySilicon",
        "gemini": "assistApiKeyGemini",
        "kimi": "assistApiKeyKimi",
        "deepseek": "assistApiKeyDeepseek",
        "doubao": "assistApiKeyDoubao",
        "mimo": "assistApiKeyMimo",
        "grok": "assistApiKeyGrok",
        "claude": "assistApiKeyClaude",
        "openrouter": "assistApiKeyOpenrouter",
    }
    key_field = key_fields.get(assist_api)
    core_key = core_cfg.get("coreApiKey") or ""
    api_key = core_cfg.get("agentModelApiKey") or ""
    if not api_key and key_field:
        api_key = core_cfg.get(key_field) or ""
    if not api_key and assist_api == "free":
        api_key = profile.get("OPENROUTER_API_KEY", "")
    if not api_key and assist_api == core_api and core_key != "free-access":
        api_key = core_key

    return {
        "model": core_cfg.get("agentModelId") or profile.get("AGENT_MODEL", ""),
        "base_url": core_cfg.get("agentModelUrl") or profile.get("OPENROUTER_URL", ""),
        "api_key": api_key,
        "assist_api": assist_api,
        "source_file": source_file,
    }


def _make_png(width: int = 16, height: int = 16) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", binascii.crc32(kind + data) & 0xFFFFFFFF)
        )

    rows = b"".join(b"\x00" + (b"\xff\xff\xff\xff" * width) for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def probe_cua_image_payload(timeout: float) -> ProbeResult:
    probe_png = _make_png()
    try:
        from config import get_agent_extra_body
        from utils.llm_client import create_chat_llm

        cfg = _load_agent_config_readonly()
        reasons = []
        if not (cfg.get("model") or "").strip():
            reasons.append("Agent model is missing")
        if not (cfg.get("base_url") or "").strip():
            reasons.append("Agent base_url is missing")
        if not (cfg.get("api_key") or "").strip():
            reasons.append("Agent api_key is missing")
        ready = not reasons
        if not ready:
            return ProbeResult(
                "cua:image_url_payload",
                "SKIP",
                "Agent API config is not ready",
                {"reasons": reasons},
            )

        model = str(cfg.get("model") or "")
        client = create_chat_llm(
            model=model,
            base_url=cfg.get("base_url"),
            api_key=cfg.get("api_key"),
            max_retries=0,
            timeout=timeout,
            extra_body=None,
        )
        extra = get_agent_extra_body(model) or {}
        img = base64.b64encode(probe_png).decode("ascii")
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img}"}},
                    {"type": "text", "text": "Reply with OK only."},
                ],
            }
        ]
        resp = client.invoke_raw(
            messages,
            max_completion_tokens=8,
            extra_body=extra or None,
            timeout=timeout,
        )
        choice = resp.choices[0] if getattr(resp, "choices", None) else None
        msg = getattr(choice, "message", None)
        content = getattr(msg, "content", "") or ""
        usage = getattr(resp, "usage", None)
        return ProbeResult(
            "cua:image_url_payload",
            "PASS",
            "Agent endpoint accepted OpenAI-style image_url payload",
            {
                "model": model,
                "base_url": cfg.get("base_url"),
                "content_preview": content[:200],
                "usage": usage.model_dump() if usage else None,
            },
        )
    except Exception as exc:
        text = _short_error(exc)
        classification = _classify_error(text)
        status = "FAIL" if classification.startswith("cua_") else "WARN"
        return ProbeResult(
            "cua:image_url_payload",
            status,
            text,
            {"classification": classification, "traceback": traceback.format_exc()},
        )


async def _run_browser_task(task: str, timeout: float) -> ProbeResult:
    try:
        from brain.browser_use_adapter import BrowserUseAdapter

        adapter = BrowserUseAdapter()
        try:
            result = await adapter.run_instruction(task, timeout_s=int(timeout), session_id="repro-agent-control-chain")
        finally:
            await adapter.close()
        ok = bool(isinstance(result, dict) and result.get("success"))
        detail = "BrowserUse task completed" if ok else str((result or {}).get("error") or result)
        return ProbeResult(
            "browser_use:launch_task",
            "PASS" if ok else "FAIL",
            detail,
            {"result": result, "classification": _classify_error(detail)},
        )
    except Exception as exc:
        text = _short_error(exc)
        return ProbeResult(
            "browser_use:launch_task",
            "FAIL",
            text,
            {"classification": _classify_error(text), "traceback": traceback.format_exc()},
        )


def watch_health(seconds: float, interval: float, timeout: float) -> ProbeResult:
    deadline = time.monotonic() + seconds
    timeline: list[dict[str, Any]] = []
    last_state: dict[str, str] = {}
    transitions: list[dict[str, Any]] = []
    tick = 0
    while time.monotonic() < deadline:
        tick += 1
        snapshot = {"t": round(seconds - max(0.0, deadline - time.monotonic()), 2), "checks": []}
        for result in probe_health(timeout=timeout):
            state = result.status
            snapshot["checks"].append(
                {
                    "name": result.name,
                    "status": state,
                    "detail": result.detail,
                    "classification": result.data.get("classification"),
                }
            )
            previous = last_state.get(result.name)
            if previous is not None and previous != state:
                transitions.append(
                    {
                        "t": snapshot["t"],
                        "name": result.name,
                        "from": previous,
                        "to": state,
                        "detail": result.detail,
                    }
                )
            last_state[result.name] = state
        timeline.append(snapshot)
        if tick == 1 or transitions:
            print(_format_watch_snapshot(snapshot), flush=True)
            transitions.clear()
        time.sleep(interval)

    final_failures = [
        check
        for check in (timeline[-1]["checks"] if timeline else [])
        if check.get("status") == "FAIL"
    ]
    status = "FAIL" if final_failures else "PASS"
    detail = "Health watch finished"
    if final_failures:
        detail += f"; final failures: {len(final_failures)}"
    return ProbeResult("health:watch", status, detail, {"timeline": timeline[-20:]})


def _format_watch_snapshot(snapshot: dict[str, Any]) -> str:
    parts = [f"[watch +{snapshot['t']}s]"]
    for check in snapshot.get("checks", []):
        parts.append(f"{check['name']}={check['status']}")
    return " ".join(parts)


def print_report(results: list[ProbeResult]) -> None:
    print("N.E.K.O Agent control-chain repro probe")
    print(f"Project: {PROJECT_ROOT}")
    print("")
    for result in results:
        print(f"[{result.status}] {result.name}")
        print(f"  {result.detail}")
        if result.data:
            compact = _compact_data(result.data)
            if compact:
                print(f"  data: {compact}")
        print("")


def _compact_data(data: dict[str, Any]) -> str:
    hidden = {"traceback", "timeline", "body"}
    compact = {k: v for k, v in data.items() if k not in hidden}
    try:
        text = json.dumps(compact, ensure_ascii=False, default=str)
    except TypeError:
        text = str(compact)
    if len(text) > 900:
        text = text[:900] + "...<truncated>"
    return text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of text report.")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero when any probe reports FAIL.")
    parser.add_argument("--timeout", type=float, default=8.0, help="HTTP/model timeout in seconds.")
    parser.add_argument(
        "--probe-cua-image",
        action="store_true",
        help="Send one real Agent model request with a ComputerUse-style image_url payload.",
    )
    parser.add_argument(
        "--launch-browser-use",
        action="store_true",
        help="Run BrowserUseAdapter against a real browser. This may consume Agent quota.",
    )
    parser.add_argument(
        "--browser-task",
        default="Open https://example.com and report the page title.",
        help="Task used with --launch-browser-use.",
    )
    parser.add_argument("--browser-timeout", type=float, default=60.0, help="BrowserUse task timeout in seconds.")
    parser.add_argument(
        "--watch-health",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="Poll health endpoints while reproducing manually.",
    )
    parser.add_argument("--watch-interval", type=float, default=1.0, help="Health watch interval in seconds.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    results: list[ProbeResult] = []

    results.extend(probe_health(timeout=args.timeout))
    results.append(probe_browser_paths())
    results.append(probe_agent_config())

    if args.probe_cua_image:
        results.append(probe_cua_image_payload(timeout=args.timeout))
    else:
        results.append(
            ProbeResult(
                "cua:image_url_payload",
                "SKIP",
                "Use --probe-cua-image to send a real Agent model request.",
            )
        )

    if args.launch_browser_use:
        results.append(asyncio.run(_run_browser_task(args.browser_task, args.browser_timeout)))
    else:
        results.append(
            ProbeResult(
                "browser_use:launch_task",
                "SKIP",
                "Use --launch-browser-use to open a real browser and run BrowserUseAdapter.",
            )
        )

    if args.watch_health > 0:
        results.append(watch_health(args.watch_health, args.watch_interval, args.timeout))

    if args.json:
        print(json.dumps([asdict(result) for result in results], ensure_ascii=False, indent=2, default=str))
    else:
        print_report(results)

    if args.strict and any(result.status == "FAIL" for result in results):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
