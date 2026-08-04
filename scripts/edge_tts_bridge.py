# -*- coding: utf-8 -*-
"""
Minimal OpenAI-compatible TTS bridge using Microsoft Edge TTS.

Exposes:
  GET  /health
  POST /v1/audio/speech   JSON: {input, voice, model, response_format}

Returns raw PCM s16le mono 24 kHz (what N.E.K.O openai TTS worker expects).
Requires: pip install edge-tts
Optional but recommended: ffmpeg on PATH (for mp3->pcm).
"""
from __future__ import annotations

import argparse
import asyncio
import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_VOICE = "ja-JP-NanamiNeural"
SAMPLE_RATE = 24000


def _have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def _mp3_to_pcm(mp3: bytes) -> bytes:
    if not mp3:
        return b""
    if not _have_ffmpeg():
        raise RuntimeError(
            "ffmpeg not found on PATH. Install ffmpeg, or use GPT-SoVITS instead."
        )
    proc = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            "pipe:0",
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "pipe:1",
        ],
        input=mp3,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", errors="ignore")
        raise RuntimeError(f"ffmpeg failed: {err[:300]}")
    return proc.stdout


async def _edge_mp3(text: str, voice: str) -> bytes:
    try:
        import edge_tts
    except ImportError as e:
        raise RuntimeError("edge-tts not installed. Run: pip install edge-tts") from e

    communicate = edge_tts.Communicate(text, voice)
    chunks: list[bytes] = []
    async for item in communicate.stream():
        if item.get("type") == "audio":
            chunks.append(item.get("data") or b"")
    return b"".join(chunks)


def synthesize(text: str, voice: str) -> bytes:
    text = (text or "").strip()
    if not text:
        return b""
    voice = (voice or DEFAULT_VOICE).strip() or DEFAULT_VOICE
    mp3 = asyncio.run(_edge_mp3(text, voice))
    return _mp3_to_pcm(mp3)


class Handler(BaseHTTPRequestHandler):
    server_version = "NekoEdgeTTS/1.0"

    def log_message(self, fmt: str, *args) -> None:
        sys.stdout.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/health", "/v1/health"):
            body = json.dumps(
                {
                    "ok": True,
                    "ffmpeg": _have_ffmpeg(),
                    "default_voice": DEFAULT_VOICE,
                    "pcm_sample_rate": SAMPLE_RATE,
                }
            ).encode("utf-8")
            self._send(200, body, "application/json")
            return
        self._send(404, b'{"error":"not found"}', "application/json")

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path not in ("/v1/audio/speech", "/audio/speech"):
            self._send(404, b'{"error":"not found"}', "application/json")
            return
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            self._send(400, b'{"error":"invalid json"}', "application/json")
            return
        text = str(data.get("input") or data.get("text") or "")
        voice = str(data.get("voice") or DEFAULT_VOICE)
        try:
            pcm = synthesize(text, voice)
        except Exception as e:
            msg = json.dumps({"error": str(e)}, ensure_ascii=False).encode("utf-8")
            self._send(500, msg, "application/json")
            return
        self._send(200, pcm, "application/octet-stream")


def _pid_path() -> Path:
    # scripts/ -> repo root -> logs/edge_tts_bridge.pid
    return Path(__file__).resolve().parents[1] / "logs" / "edge_tts_bridge.pid"


def _write_pid_file() -> Path | None:
    path = _pid_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(os.getpid()), encoding="utf-8")
        return path
    except OSError:
        return None


def _clear_pid_file(path: Path | None) -> None:
    if path is None:
        return
    try:
        if path.is_file() and path.read_text(encoding="utf-8").strip() == str(os.getpid()):
            path.unlink()
    except OSError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=19000)
    args = parser.parse_args()

    if not _have_ffmpeg():
        print("[WARN] ffmpeg not on PATH — /v1/audio/speech will fail until installed.")
        print("       winget install Gyan.FFmpeg")
    try:
        import edge_tts  # noqa: F401
    except ImportError:
        print("[ERROR] pip install edge-tts")
        return 1

    # Idempotent start: if another healthy bridge already owns the port, exit 0
    # instead of binding a second broken listener (Windows can leave multiple
    # LISTENING sockets that RST health checks).
    try:
        import urllib.request

        with urllib.request.urlopen(
            f"http://{args.host}:{args.port}/health", timeout=1.0
        ) as resp:
            if resp.status == 200:
                print(f"Edge TTS bridge already healthy on http://{args.host}:{args.port}")
                return 0
    except Exception:
        pass

    pid_file = _write_pid_file()
    if pid_file is not None:
        atexit.register(_clear_pid_file, pid_file)

    try:
        httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as e:
        print(f"[ERROR] cannot bind {args.host}:{args.port}: {e}")
        return 1
    print(f"Edge TTS bridge on http://{args.host}:{args.port}")
    print("  GET  /health")
    print("  POST /v1/audio/speech")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        _clear_pid_file(pid_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
