# -*- coding: utf-8 -*-
"""
Telemetry end-to-end smoke test.

跑法：
    uv run python scripts/telemetry_smoke.py

会做：
  1) 起 telemetry_server（uvicorn 后台）+ 临时 SQLite DB
  2) 用 utils/instrument + utils/event_logger 在本进程灌一些埋点
  3) 直接调 TokenTracker._report_to_server 投递到 server（含 gzip）
  4) 也直接构造一份"模拟前端 WS 转发"的客户端 payload，POST 上去
  5) 查 SQLite + dashboard HTML，断言关键数据齐了
"""
from __future__ import annotations

import gzip
import hashlib
import hmac
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

HMAC_SECRET = "neko-v1-a3f8b2c1d4e5f6789012345678abcdef"
PORT = 18099  # 临时端口，避开生产 8099
SERVER_URL = f"http://127.0.0.1:{PORT}"


def _http(method, path, body=None, headers=None, timeout=5.0):
    h = dict(headers or {})
    data = None
    if body is not None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body).encode("utf-8")
            h.setdefault("Content-Type", "application/json")
        else:
            data = body
    req = urllib.request.Request(f"{SERVER_URL}{path}", data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _sign_and_submit(payload: dict, gzip_it: bool = True, batch_id: str | None = None):
    """构造 HMAC 信封并 POST 到 server。"""
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    ts = time.time()
    body_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    sig = hmac.new(
        HMAC_SECRET.encode(), f"{ts}|{body_hash}".encode(), hashlib.sha256
    ).hexdigest()
    submission = {"timestamp": ts, "signature": sig, "payload": payload, "batch_id": batch_id}
    body = json.dumps(submission, ensure_ascii=False).encode("utf-8")

    headers = {"Content-Type": "application/json"}
    if gzip_it:
        body = gzip.compress(body, compresslevel=6, mtime=0)
        headers["Content-Encoding"] = "gzip"

    status, resp = _http("POST", "/api/v1/telemetry", body=body, headers=headers)
    return status, resp


def _wait_for_health(timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            status, _ = _http("GET", "/health", timeout=1.0)
            if status == 200:
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


def main():
    print("=" * 70)
    print("TELEMETRY SMOKE TEST")
    print("=" * 70)

    tmpdir = tempfile.mkdtemp(prefix="neko_telemetry_smoke_")
    db_path = os.path.join(tmpdir, "telemetry.db")
    print(f"Temp dir: {tmpdir}")
    print(f"DB: {db_path}")

    env = dict(os.environ)
    env["TELEMETRY_HMAC_SECRET"] = HMAC_SECRET
    env["TELEMETRY_DB_PATH"] = db_path
    env["TELEMETRY_ADMIN_TOKEN"] = "smoke-test-admin"

    server_cwd = str(PROJECT_ROOT / "local_server" / "telemetry_server")
    cmd = [sys.executable, "server.py", "--host", "127.0.0.1", "--port", str(PORT)]
    print(f"\n[1/5] Starting server: {' '.join(cmd)}")
    proc = subprocess.Popen(
        cmd,
        cwd=server_cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        if not _wait_for_health():
            print("FAIL: server did not become healthy in 10s")
            # 顺手 dump output
            try:
                proc.terminate()
                out, _ = proc.communicate(timeout=2)
                print("--- server output ---")
                print(out[-4000:])
            except Exception:
                pass
            return 1
        print("✓ server healthy")

        # --------------------------------------------------------------
        # [2/5] 通过 utils/instrument 在 smoke 进程灌埋点，触发上报
        # --------------------------------------------------------------
        print("\n[2/5] Generating instrument data via SDK...")

        # 让 TokenTracker 上报到本地 server
        import utils.token_tracker as tt_mod
        tt_mod._TELEMETRY_SERVER_URL = SERVER_URL
        tt_mod._TELEMETRY_HMAC_SECRET = HMAC_SECRET

        from utils.token_tracker import TokenTracker
        from utils.instrument import counter, histogram, event, Instrument
        from utils.event_logger import EventLogger

        # 重置单例确保隔离
        TokenTracker._instance = None
        Instrument._instance = None
        EventLogger._instance = None

        # 模拟一些埋点
        for _ in range(5):
            counter("user_message_sent", 1, surface="pet_widget")
        for _ in range(3):
            counter("user_message_sent", 1, surface="chat_window")
        counter("feature_invoked", 1, feature="galgame")
        counter("session_start", 1, process="main_server")

        histogram("ttft_ms", 234)
        histogram("ttft_ms", 412)
        histogram("ttft_ms", 156)
        histogram("ws_session_sec", 850.5, lanlan_name="hibiki")

        event("session_start", process="main_server")
        event("crash", error_class="ValueError", traceback_hash="deadbeef")
        event("onboarding_step", status="persona_chosen")

        tracker = TokenTracker.get_instance()
        # 也加一点 LLM token 数据
        tracker.record(model="gpt-4o-mini", prompt_tokens=100, completion_tokens=50,
                       total_tokens=150, cached_tokens=20, call_type="conversation")
        tracker.record(model="gpt-4o-mini", prompt_tokens=200, completion_tokens=80,
                       total_tokens=280, cached_tokens=50, call_type="conversation")

        # 强制上报：清掉 _last_report_time 节流
        tracker._last_report_time = 0
        tracker.save()  # 触发 _report_to_server
        print("✓ SDK report attempted")

        # --------------------------------------------------------------
        # [3/5] 模拟"前端 WS 转发上来的 telemetry"—— 直接构造另一份 payload
        # --------------------------------------------------------------
        print("\n[3/5] Simulating frontend-originated counter via additional payload...")

        # 这里其实跟 SDK 内部走的是同一路径，只是构造手工，验证 server 在
        # 老客户端 (无 instruments) / 新客户端 (有 instruments) 两种 payload
        # 上都能工作。
        old_payload = {
            "device_id": "b" * 64,
            "app_version": "1.0.0",
            "branch": "main",
            "locale": "ja-JP",
            "timezone": "Asia/Tokyo",
            "distribution": "release",
            "steam_user_id": "",
            "daily_stats": {
                time.strftime("%Y-%m-%d"): {
                    "total_prompt_tokens": 500, "total_completion_tokens": 100,
                    "total_tokens": 600, "cached_tokens": 50,
                    "call_count": 3, "error_count": 0,
                    "by_model": {"gpt-4o": {"prompt_tokens": 500, "completion_tokens": 100,
                                            "total_tokens": 600, "cached_tokens": 50, "call_count": 3}},
                    "by_call_type": {"conversation": {"prompt_tokens": 500, "completion_tokens": 100,
                                                       "total_tokens": 600, "cached_tokens": 50, "call_count": 3}},
                }
            },
            "recent_records": [],
        }
        status, resp = _sign_and_submit(old_payload, gzip_it=False, batch_id="smoke-old-1")
        print(f"  old client (raw JSON, no instruments): HTTP {status} {resp[:100]!r}")
        assert status == 200, f"old payload submit failed: {status}"

        new_payload = {
            "device_id": "c" * 64,
            "app_version": "2.0.0",
            "branch": "privacy_default_off_v1",
            "locale": "en-US",
            "timezone": "America/New_York",
            "distribution": "steam",
            "steam_user_id": "76561198000000001",
            "daily_stats": {},
            "recent_records": [],
            "instruments": {
                "window_start": time.time() - 60,
                "window_end": time.time(),
                "bounds": [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000],
                "counters": {
                    "user_message_sent|surface=index_wide": 42,
                    "live2d_touched": 17,
                },
                "histograms": {
                    "client_render_ms": {
                        "count": 10, "sum": 1234.5,
                        "buckets": [0, 0, 0, 2, 5, 3, 0, 0, 0, 0, 0, 0, 0, 0]
                    },
                },
            },
        }
        status, resp = _sign_and_submit(new_payload, gzip_it=True, batch_id="smoke-new-1")
        print(f"  new client (gzip + instruments): HTTP {status} {resp[:100]!r}")
        assert status == 200, f"new payload submit failed: {status}"

        # 同一 batch_id 重发 —— 应该 dedupe
        status, resp = _sign_and_submit(new_payload, gzip_it=True, batch_id="smoke-new-1")
        print(f"  same batch_id replay: HTTP {status} {resp[:120]!r}")
        assert status == 200

        # --------------------------------------------------------------
        # [4/5] 直查 SQLite 看数据落了
        # --------------------------------------------------------------
        print("\n[4/5] Inspecting SQLite directly...")
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        def _table_count(name):
            return conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]

        events_n = _table_count("events")
        daily_n = _table_count("daily_aggregates")
        devices_n = _table_count("devices")
        batches_n = _table_count("seen_batches")
        counters_n = _table_count("instrument_counters")
        hist_n = _table_count("instrument_histograms")
        print(f"  events={events_n} daily_aggregates={daily_n} devices={devices_n}")
        print(f"  seen_batches={batches_n} instrument_counters={counters_n} instrument_histograms={hist_n}")

        assert events_n >= 2, "expected >=2 events stored (1 SDK + 2 manual; dedupe collapses 1)"
        assert devices_n >= 2, "expected >=2 devices"
        assert counters_n >= 2, f"expected counters table populated, got {counters_n}"
        assert hist_n >= 1, f"expected histogram table populated, got {hist_n}"

        # 打印 sample 数据
        print("\n  --- sample counters ---")
        for r in conn.execute(
            "SELECT stat_date, metric_key, value FROM instrument_counters "
            "ORDER BY value DESC LIMIT 8"
        ).fetchall():
            print(f"    {r['stat_date']} {r['metric_key']:50s} value={r['value']}")

        print("\n  --- sample histograms ---")
        for r in conn.execute(
            "SELECT stat_date, metric_key, count, sum, buckets FROM instrument_histograms LIMIT 5"
        ).fetchall():
            print(f"    {r['stat_date']} {r['metric_key']:30s} count={r['count']} sum={r['sum']}")
            print(f"      buckets={r['buckets']}")

        # 验证去重 —— same batch_id 没让 counter 双倍
        new_dev_counter = conn.execute(
            "SELECT value FROM instrument_counters "
            "WHERE device_id = ? AND metric_key = 'live2d_touched'",
            ("c" * 64,),
        ).fetchone()
        assert new_dev_counter is not None
        assert new_dev_counter["value"] == 17.0, \
            f"dedupe broken: expected 17, got {new_dev_counter['value']}"
        print(f"  ✓ idempotency: live2d_touched = {new_dev_counter['value']} (not doubled)")

        conn.close()

        # --------------------------------------------------------------
        # [5/5] dashboard 能返回 + 含 instrument 表
        # --------------------------------------------------------------
        print("\n[5/5] Fetching dashboard HTML...")
        status, body = _http(
            "GET", "/api/v1/admin/dashboard?days=30&token=smoke-test-admin"
        )
        assert status == 200, f"dashboard HTTP {status}"
        text = body.decode("utf-8", errors="replace")
        # 关键标记
        for marker in (
            "N.E.K.O Telemetry Dashboard",
            "DAU (Today)",
            "Top Counters",
            "Histograms",
            "live2d_touched",  # 我们存的 counter key 应在 HTML 里
            "client_render_ms",  # histogram key
        ):
            assert marker in text, f"dashboard missing marker: {marker!r}"
        print(f"  ✓ dashboard returned {len(text)} bytes with all expected markers")

        # 测试 health / global stats
        status, body = _http("GET", "/api/v1/admin/stats?days=30&token=smoke-test-admin")
        assert status == 200
        stats = json.loads(body)
        print(f"  global stats: total_devices={stats['total_devices']} "
              f"total_events={stats['total_events']}")
        assert stats["total_devices"] >= 2

        print("\n" + "=" * 70)
        print("✓ ALL SMOKE TESTS PASSED")
        print("=" * 70)
        return 0

    finally:
        print("\nShutting down server...")
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        # 临时目录留在磁盘上方便事后查（不删）
        print(f"(Leftover temp dir for inspection: {tmpdir})")


if __name__ == "__main__":
    sys.exit(main())
