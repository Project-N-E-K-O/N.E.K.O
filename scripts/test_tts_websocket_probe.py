#!/usr/bin/env python3
"""
Probe lanlan.app free TTS WebSocket and log raw handshake failures.

Default URL is hardcoded to wss://lanlan.app/tts (no GeoIP / lanlan.tech switching).

Use when debugging HTTP 503 (or other non-101) during the WebSocket upgrade: the
``websockets`` library raises InvalidStatus with the full HTTP response attached.

Examples:
  python scripts/test_tts_websocket_probe.py --token free-access --no-proxy
  python scripts/test_tts_websocket_probe.py --from-config --no-proxy
  python scripts/test_tts_websocket_probe.py --from-config --no-proxy --protocol-roundtrip --text "测试"

Env (optional): TTS_PROBE_TOKEN, AUDIO_API_KEY. Override URL only with --url if you must.
"""

from __future__ import annotations

import argparse
import asyncio
import base64 as _b64
import json
import logging
import os
import ssl
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import websockets
from websockets.exceptions import InvalidStatus

REPO_ROOT = Path(__file__).resolve().parent.parent

# Fixed probe target (do not route via ConfigManager GeoIP).
DEFAULT_TTS_WSS_URL = "wss://lanlan.app/tts"


def _setup_logging(log_path: Path | None) -> logging.Logger:
    log = logging.getLogger("tts_probe")
    log.setLevel(logging.DEBUG)
    log.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(logging.DEBUG)
    sh.setFormatter(fmt)
    log.addHandler(sh)
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        log.addHandler(fh)
    return log


def _headers_as_lines(headers: Any) -> list[str]:
    lines: list[str] = []
    try:
        for k, v in headers.raw_items():
            lines.append(f"{k.decode() if isinstance(k, bytes) else k}: {v.decode() if isinstance(v, bytes) else v}")
    except Exception:
        try:
            for k, v in headers.items():
                lines.append(f"{k}: {v}")
        except Exception as e:
            lines.append(f"(could not format headers: {e})")
    return lines


def _log_http_rejection(log: logging.Logger, exc: InvalidStatus) -> None:
    r = exc.response
    log.error("WebSocket upgrade rejected (InvalidStatus)")
    log.error("  status_code=%s reason=%r", r.status_code, getattr(r, "reason_phrase", ""))
    for line in _headers_as_lines(r.headers):
        log.error("  hdr %s", line)
    body = r.body or b""
    if body:
        try:
            text = body.decode("utf-8", errors="replace")
        except Exception:
            text = repr(body)
        if len(text) > 8192:
            text = text[:8192] + "\n... (truncated)"
        log.error("  body (%d bytes):\n%s", len(body), text)
    else:
        log.error("  body: (empty)")


def _load_token_from_config(log: logging.Logger) -> str:
    sys.path.insert(0, str(REPO_ROOT))
    from utils.config_manager import get_config_manager  # noqa: WPS433

    cfg = get_config_manager().get_core_config()
    token = (cfg.get("AUDIO_API_KEY") or "").strip()
    if not token:
        token = (cfg.get("CORE_API_KEY") or "").strip()
    log.info("from-config: token_set=%s", bool(token))
    return token


async def _read_until(
    ws: websockets.ClientConnection,
    log: logging.Logger,
    *,
    want_types: set[str],
    timeout: float,
    label: str,
) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.monotonic()))
        except TimeoutError:
            continue
        log.debug("[%s] recv %s", label, raw if len(raw) < 2000 else raw[:2000] + "...")
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("[%s] non-JSON frame: %r", label, raw[:500])
            continue
        t = msg.get("type")
        log.info("[%s] event type=%s", label, t)
        if t in want_types:
            return msg
        if t == "tts.response.error":
            log.error("[%s] server error event: %s", label, json.dumps(msg, ensure_ascii=False))
            return msg
    log.error("[%s] timed out waiting for one of %s", label, want_types)
    return None


async def run_probe(args: argparse.Namespace, log: logging.Logger) -> int:
    url = (args.url or "").strip() or DEFAULT_TTS_WSS_URL
    token = args.token or os.environ.get("TTS_PROBE_TOKEN", os.environ.get("AUDIO_API_KEY", "")).strip()

    if args.from_config and not token:
        token = _load_token_from_config(log)
    elif args.from_config and token:
        log.info("--from-config: token already set via CLI/env, skipping config load")

    if not url:
        log.error("No URL")
        return 2
    if not token:
        log.warning("No Bearer token: pass --token or set TTS_PROBE_TOKEN / AUDIO_API_KEY (some servers still handshake)")

    ssl_ctx: ssl.SSLContext | bool | None = True
    if args.insecure:
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        log.warning("TLS verification disabled (--insecure)")

    proxy: str | bool | None = True
    if args.no_proxy:
        proxy = None
        log.info("connect: proxy disabled (websockets default proxy=True can break or alter paths)")

    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    log.info("python=%s websockets=%s", sys.version.split()[0], websockets.__version__)
    log.info("connecting %s", url)
    log.debug("extra headers keys=%s", list(headers.keys()))

    try:
        ws = await websockets.connect(
            url,
            additional_headers=headers or None,
            ssl=ssl_ctx,
            proxy=proxy,
            open_timeout=args.open_timeout,
            logger=log,
        )
    except InvalidStatus as e:
        _log_http_rejection(log, e)
        return 1
    except Exception as e:
        log.exception("connect failed: %r", e)
        return 1

    log.info("handshake OK (101), remote=%s local=%s", ws.remote_address, ws.local_address)

    try:
        msg = await _read_until(
            ws,
            log,
            want_types={"tts.connection.done", "tts.response.error"},
            timeout=args.wait_connection,
            label="post-handshake",
        )
        if not msg or msg.get("type") != "tts.connection.done":
            return 1
        session_id = (msg.get("data") or {}).get("session_id")
        if not session_id:
            log.error("tts.connection.done missing session_id: %s", msg)
            return 1
        log.info("session_id=%s", session_id)

        if args.protocol_roundtrip:
            if "lanlan.app" in url:
                create_data: dict[str, Any] = {
                    "session_id": session_id,
                    "voice_id": "Leda",
                    "response_format": "wav",
                    "sample_rate": 24000,
                    "language_code": args.language_code,
                }
            else:
                # Only when --url overrides away from lanlan.app
                create_data = {
                    "session_id": session_id,
                    "voice_id": args.voice_id,
                    "response_format": "wav",
                    "sample_rate": 24000,
                }
            if args.voice_label_language:
                # Test field placement for lanlan.tech voice_label.language.
                # Position controlled by --voice-label-position: "data" (nested under data)
                # vs "event" (sibling of data at top level).
                vl = {"language": args.voice_label_language}
                if args.voice_label_position == "event":
                    payload = {"type": "tts.create", "data": create_data, "voice_label": vl}
                else:
                    create_data["voice_label"] = vl
                    payload = {"type": "tts.create", "data": create_data}
            else:
                payload = {"type": "tts.create", "data": create_data}
            out = json.dumps(payload)
            log.info("send tts.create")
            log.debug("send payload=%s", out)
            await ws.send(out)
            msg2 = await _read_until(
                ws,
                log,
                want_types={"tts.response.created", "tts.response.error"},
                timeout=args.wait_session,
                label="after-create",
            )
            if not msg2 or msg2.get("type") != "tts.response.created":
                return 1

            if args.text.strip():
                te = json.dumps(
                    {
                        "type": "tts.text.delta",
                        "data": {"session_id": session_id, "text": args.text},
                    }
                )
                log.info("send tts.text.delta (%d chars)", len(args.text))
                await ws.send(te)
                done = json.dumps({"type": "tts.text.done", "data": {"session_id": session_id}})
                await ws.send(done)
                audio_chunks: list[bytes] = []
                final_audio: bytes | None = None
                audio_done = False
                for _ in range(200):
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=3.0)
                    except TimeoutError:
                        break
                    try:
                        evt = json.loads(raw)
                    except json.JSONDecodeError:
                        log.info("post-text recv (non-JSON): %s", raw[:500])
                        continue
                    et = evt.get("type")
                    if et == "tts.response.audio.delta":
                        b64 = (evt.get("data") or {}).get("audio", "")
                        if b64:
                            try:
                                audio_chunks.append(_b64.b64decode(b64))
                            except Exception as e:
                                log.warning("audio b64 decode failed: %s", e)
                        log.info("post-text recv audio delta (%d bytes b64)", len(b64))
                    elif et == "tts.response.audio.done":
                        b64 = (evt.get("data") or {}).get("audio", "")
                        if b64:
                            try:
                                final_audio = _b64.b64decode(b64)
                            except Exception as e:
                                log.warning("final audio b64 decode failed: %s", e)
                        log.info("post-text recv audio.done (final audio %d bytes b64)", len(b64))
                        audio_done = True
                        break
                    elif et == "tts.response.done":
                        log.info("post-text recv response.done")
                        audio_done = True
                        break
                    elif et == "tts.response.error":
                        log.error("post-text server error: %s", raw)
                        break
                    else:
                        log.info("post-text recv: %s", raw if len(raw) < 2000 else raw[:2000] + "...")
                log.info("audio chunks=%d, final=%s, done=%s", len(audio_chunks), bool(final_audio), audio_done)
                if args.save_audio:
                    out_path = Path(args.save_audio)
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    # Prefer the assembled WAV from tts.response.audio.done;
                    # fall back to first delta chunk.
                    if final_audio:
                        out_path.write_bytes(final_audio)
                        log.info("saved final audio (%d bytes) to %s", len(final_audio), out_path.resolve())
                    elif audio_chunks:
                        merged = b"".join(audio_chunks)
                        out_path.write_bytes(merged)
                        log.info(
                            "saved concatenated delta chunks (%d chunks, %d bytes) to %s "
                            "(note: each chunk is a standalone WAV, file contains multiple headers)",
                            len(audio_chunks),
                            len(merged),
                            out_path.resolve(),
                        )

    finally:
        await ws.close()
        log.info("closed")

    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="TTS WebSocket handshake / protocol probe with verbose logging")
    p.add_argument(
        "--url",
        default="",
        help=f"Override WebSocket URL (default: {DEFAULT_TTS_WSS_URL})",
    )
    p.add_argument("--token", default="", help="Bearer token (omit to try env / --from-config)")
    p.add_argument(
        "--from-config",
        action="store_true",
        help="Load AUDIO_API_KEY (or CORE_API_KEY) from ConfigManager; URL stays lanlan.app",
    )
    p.add_argument("--no-proxy", action="store_true", help="Disable HTTP proxy for WebSocket (recommended for debugging)")
    p.add_argument("--insecure", action="store_true", help="Disable TLS certificate verification")
    p.add_argument("--open-timeout", type=float, default=15.0)
    p.add_argument("--wait-connection", type=float, default=8.0, help="Wait for tts.connection.done")
    p.add_argument("--wait-session", type=float, default=5.0, help="Wait for tts.response.created")
    p.add_argument("--protocol-roundtrip", action="store_true", help="After handshake, send tts.create (+ optional text)")
    p.add_argument("--voice-id", default="qingchunshaonv", help="voice_id only if --url is not lanlan.app")
    p.add_argument("--language-code", default="cmn-CN", help="language_code for lanlan.app (matches app default map)")
    p.add_argument("--text", default="", help="If set with --protocol-roundtrip, send this as tts.text.delta")
    p.add_argument(
        "--voice-label-language",
        default="",
        help="If set, include voice_label.language in tts.create (e.g. '日语', '粤语', '四川话'). For lanlan.tech.",
    )
    p.add_argument(
        "--voice-label-position",
        default="data",
        choices=["data", "event"],
        help="Where to place voice_label: nested under 'data' (default) or sibling at event level",
    )
    p.add_argument("--save-audio", default="", help="If set, write concatenated audio to this path (raw concatenated wav chunks)")
    p.add_argument(
        "--log-file",
        default="",
        help="Append detailed log to this file (default: logs/tts_ws_probe_<utc>.log under repo root)",
    )
    args = p.parse_args()

    default_name = f"tts_ws_probe_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.log"
    log_path = Path(args.log_file) if args.log_file else (REPO_ROOT / "logs" / default_name)
    log = _setup_logging(log_path)
    log.info("log file: %s", log_path.resolve())

    try:
        rc = asyncio.run(run_probe(args, log))
    except KeyboardInterrupt:
        log.warning("interrupted")
        rc = 130
    raise SystemExit(rc)


if __name__ == "__main__":
    main()
