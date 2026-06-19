# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Telemetry end-to-end smoke test.

How to run:
    uv run python scripts/telemetry_smoke.py

What it does:
  1) Starts telemetry_server (uvicorn in the background) + a temp SQLite DB
  2) Feeds some metrics in-process via utils/instrument + utils/event_logger
  3) Calls TokenTracker._report_to_server directly to deliver to the server (gzip included)
  4) Also builds a "simulated frontend WS forward" client payload and POSTs it
  5) Queries SQLite + the dashboard HTML, asserting the key data all arrived
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
    """Build the HMAC envelope and POST it to the server."""
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
            # health probe 在 server 启动期会反复抛连接拒绝，这是预期 —— 继续
            # poll 直到 deadline。
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
                # 终止 server 进程或读它的 output 本身失败：我们正要 return 1
                # 退出 smoke，再 raise 没意义。
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
        # 用 surface dim 而不是 lanlan_name —— 后者是用户自定义 character
        # 名（PII + 高基数），生产代码已经不传，smoke 也不该示范坏 pattern。
        histogram("ws_session_sec", 850.5, surface="pet_widget")

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
        # [4b] Regression: Codex P1 — instrument-only 上报 batch_id 必须不同
        # --------------------------------------------------------------
        print("\n[4b] Regression: instrument-only batch_id uniqueness...")
        # 重置单例确保是干净的 instrument 累积
        Instrument._instance = None
        TokenTracker._instance = None
        # 设备 D：只灌前端 counter，不动 LLM token（daily_stats / recent_records
        # 都将为空）。如果 batch_id 不含 instruments，两次 snapshot 算出同一
        # hash → 第二次会被 seen_batches dedupe，server 端只看到第一次的数据。
        tracker_d = TokenTracker.get_instance()
        for batch_n in range(2):
            counter("window_only_counter", 1, surface="index_wide", batch_n=batch_n)
            # 强行触发上报：把节流计时重置
            tracker_d._last_report_time = 0
            tracker_d.save()
            time.sleep(0.5)

        # 直查 SQLite：window_only_counter 必须是 2（两次窗口都被入库）
        conn2 = sqlite3.connect(db_path)
        rows = conn2.execute(
            "SELECT metric_key, value FROM instrument_counters "
            "WHERE metric_key LIKE 'window_only_counter%' "
            "ORDER BY metric_key"
        ).fetchall()
        conn2.close()
        keys = [r[0] for r in rows]
        vals = [r[1] for r in rows]
        print(f"  metric_keys seen: {keys}")
        print(f"  values:           {vals}")
        # batch_n=0 和 batch_n=1 各 1 次 → 两个独立 key
        assert len(rows) == 2, (
            f"P1 regression: expected 2 distinct metric_keys (one per window), "
            f"got {len(rows)} — second instrument-only batch likely dedupe-dropped"
        )
        assert all(v == 1.0 for v in vals), (
            f"P1 regression: each counter should be 1.0 (one increment per window), "
            f"got {vals}"
        )
        print("  ✓ both instrument-only windows landed (batch_id includes instruments)")

        # --------------------------------------------------------------
        # [4c] Regression: Codex P2 — _atexit_save 顺序，session_end 必须上报
        # --------------------------------------------------------------
        print("\n[4c] Regression: _atexit_save emits session_end before save()...")
        # 新设备 E + record_app_start → 模拟 atexit
        Instrument._instance = None
        TokenTracker._instance = None
        # device_id 在 TokenTracker 是 OS-derived，多次实例共享；只能靠 process
        # 维度区分 session_end 的 emitter。换一个 process 名让 instrument key 唯一。
        tracker_e = TokenTracker.get_instance()
        tracker_e.record_app_start(process="smoke_atexit_test")
        # 让 session_duration > 0 以触发 histogram
        tracker_e._session_start_ts = time.time() - 5.0
        # _atexit_save 内部调 save() → _report_to_server → instrument snapshot →
        # 远程上报。session_end emit 在 save 之前，所以应该被带上。
        tracker_e._atexit_save()
        time.sleep(0.5)

        conn3 = sqlite3.connect(db_path)
        session_end_rows = conn3.execute(
            "SELECT metric_key, value FROM instrument_counters "
            "WHERE metric_key LIKE 'session_end%process=smoke_atexit_test%'"
        ).fetchall()
        session_dur_rows = conn3.execute(
            "SELECT metric_key, count, sum FROM instrument_histograms "
            "WHERE metric_key LIKE 'session_duration_sec%process=smoke_atexit_test%'"
        ).fetchall()
        conn3.close()
        print(f"  session_end counter rows: {session_end_rows}")
        print(f"  session_duration_sec rows: {session_dur_rows}")
        assert len(session_end_rows) >= 1, (
            "P2 regression: session_end counter missing — _atexit_save must "
            "emit before save() so the snapshot picks it up"
        )
        assert len(session_dur_rows) >= 1, (
            "P2 regression: session_duration_sec histogram missing"
        )
        # 把 URL 重新启用，下面的 [5/5] dashboard 测试还要用
        tt_mod._TELEMETRY_SERVER_URL = SERVER_URL
        print("  ✓ session_end counter + duration histogram both reached server")

        # --------------------------------------------------------------
        # [4d] Regression: Codex P1 round-2 — daily 重传幂等
        #
        # 网络 timeout（server commit 了 client 没收到 200）场景：client 用
        # 同一个 batch_seq 重传，server seen_batches 必须 dedupe，daily 不能
        # 被双倍计数。手动构造两份 batch_id 完全相同的 payload 验证 server
        # 行为；同时单独检查 client 在失败时 _pending_batch_seq 不被清空。
        # --------------------------------------------------------------
        print("\n[4d] Regression: daily retry idempotency (batch_seq stability)...")
        Instrument._instance = None
        TokenTracker._instance = None
        tracker_f = TokenTracker.get_instance()
        # Step A: 用 bad URL 让首次上报失败，验证 _pending_batch_seq 保留
        good_url = tt_mod._TELEMETRY_SERVER_URL
        tt_mod._TELEMETRY_SERVER_URL = "http://127.0.0.1:1"
        tracker_f.record(model="gpt-4o-mini", prompt_tokens=111,
                         completion_tokens=22, total_tokens=133,
                         cached_tokens=0, call_type="conversation")
        tracker_f._last_report_time = 0
        tracker_f.save()
        time.sleep(0.3)
        seq_after_fail = tracker_f._pending_batch_seq
        assert seq_after_fail is not None, (
            "P1 round-2 regression: _pending_batch_seq must persist after failure"
        )
        print(f"  ✓ failure preserved batch_seq = {seq_after_fail[:12]}...")
        # Step B: 恢复 URL，重传应成功并清 batch_seq
        tt_mod._TELEMETRY_SERVER_URL = good_url
        tracker_f._last_report_time = 0
        tracker_f.save()
        time.sleep(0.3)
        assert tracker_f._pending_batch_seq is None, (
            "P1 round-2 regression: _pending_batch_seq must clear after success"
        )
        print("  ✓ success cleared batch_seq")

        # Step C: 模拟"server commit 但 client 没收到 200，client 重发"。
        # 手动 POST 两次 batch_id 完全相同的 payload，验证 server dedupe 工作。
        dev_f = "f" * 64
        seq_test = "deadbeef12345678"
        bc = {"device_id": dev_f, "app_version": "retry-test", "batch_seq": seq_test}
        bid_test = hashlib.sha256(
            json.dumps(bc, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()[:32]
        today_iso = time.strftime("%Y-%m-%d")
        daily_f = {
            today_iso: {
                "total_prompt_tokens": 7777, "total_completion_tokens": 100,
                "total_tokens": 7877, "cached_tokens": 0,
                "call_count": 1, "error_count": 0,
                "by_model": {}, "by_call_type": {},
            }
        }
        payload_f = {
            "device_id": dev_f, "app_version": "retry-test",
            "branch": "main", "locale": "zh-CN", "timezone": "UTC",
            "distribution": "source", "steam_user_id": "",
            "daily_stats": daily_f, "recent_records": [],
        }
        s1, _ = _sign_and_submit(payload_f, gzip_it=False, batch_id=bid_test)
        s2, r2 = _sign_and_submit(payload_f, gzip_it=False, batch_id=bid_test)
        print(f"  first POST: HTTP {s1} | retry (same batch_id): HTTP {s2} {r2[:80]!r}")
        assert s1 == 200 and s2 == 200
        assert b"duplicate" in r2, (
            "P1 round-2 regression: server must dedupe same batch_id"
        )
        conn_f = sqlite3.connect(db_path)
        row_f = conn_f.execute(
            "SELECT total_tokens FROM daily_aggregates "
            "WHERE device_id=? AND model='_total' AND call_type='_total'",
            (dev_f,),
        ).fetchone()
        conn_f.close()
        assert row_f is not None
        assert row_f[0] == 7877, (
            f"P1 round-2 regression: daily double-counted on retry — "
            f"got {row_f[0]}, expected 7877"
        )
        print(f"  ✓ daily NOT double-counted on retry: total_tokens={row_f[0]}")

        # --------------------------------------------------------------
        # [4e] Regression: Codex P2 round-2 — short-session atexit bypass throttle
        # --------------------------------------------------------------
        print("\n[4e] Regression: short-session atexit bypasses throttle...")
        Instrument._instance = None
        TokenTracker._instance = None
        tracker_g = TokenTracker.get_instance()
        tracker_g.record_app_start(process="smoke_short_session")
        tracker_g._session_start_ts = time.time() - 2.0  # 2s 短 session
        # 先一次成功上报，让 _last_report_time = now（throttle 窗口刚开始）
        counter("short_session_anchor", 1)
        tracker_g._last_report_time = 0
        tracker_g.save()
        time.sleep(0.3)
        assert tracker_g._last_report_time > 0, "anchor save should set _last_report_time"
        # 此刻距下次允许还差 60s。如果 _atexit_save 不 bypass throttle，
        # session_end emit 之后的 save() 会被 throttle gate 挡掉。
        tracker_g._atexit_save()
        time.sleep(0.3)
        conn_g = sqlite3.connect(db_path)
        rows_g = conn_g.execute(
            "SELECT metric_key, value FROM instrument_counters "
            "WHERE metric_key LIKE 'session_end%smoke_short_session%'"
        ).fetchall()
        conn_g.close()
        assert len(rows_g) >= 1, (
            "P2 round-2 regression: session_end missing from short-session atexit "
            "— throttle bypass not working"
        )
        print(f"  ✓ short-session session_end reached server: {rows_g}")
        # 恢复 URL 给后续 [5/5] 用
        tt_mod._TELEMETRY_SERVER_URL = SERVER_URL

        # --------------------------------------------------------------
        # [4f] Regression: Codex P1 round-3 — instrument-only 失败清 stale seq
        #
        # 场景：instrument-only payload server commit 了但 client timeout。
        # instruments 已 clear-on-read 没东西放回。如果失败时不清 batch_seq，
        # 下一个新窗口（不同数据）会复用 stale seq → server seen_batches
        # 命中旧 commit → 返回 "duplicate, skipped" → 新数据被静默丢弃。
        # --------------------------------------------------------------
        print("\n[4f] Regression: instrument-only failure clears stale batch_seq...")
        Instrument._instance = None
        TokenTracker._instance = None
        tracker_h = TokenTracker.get_instance()
        # Step A: instrument-only 失败
        counter("h_only_counter_A", 1, surface="index_wide")
        good_url = tt_mod._TELEMETRY_SERVER_URL
        tt_mod._TELEMETRY_SERVER_URL = "http://127.0.0.1:1"
        tracker_h._last_report_time = 0
        tracker_h.save()
        time.sleep(0.3)
        # 关键断言：instrument-only 失败必须清 batch_seq（had_unsent_payload=False）
        assert tracker_h._pending_batch_seq is None, (
            "P1 round-3 regression: instrument-only failure left stale "
            f"_pending_batch_seq = {tracker_h._pending_batch_seq!r}"
        )
        print("  ✓ instrument-only failure cleared batch_seq")

        # Step B: 恢复 URL，发新 counter（B），应该用全新 seq 成功上报
        tt_mod._TELEMETRY_SERVER_URL = good_url
        counter("h_only_counter_B", 1, surface="index_wide")
        tracker_h._last_report_time = 0
        tracker_h.save()
        time.sleep(0.5)
        # 直查 server: h_only_counter_B 必须在库（说明不被 stale seq 误伤）
        conn_h = sqlite3.connect(db_path)
        rows_h = conn_h.execute(
            "SELECT metric_key, value FROM instrument_counters "
            "WHERE metric_key LIKE 'h_only_counter_B%'"
        ).fetchall()
        conn_h.close()
        assert len(rows_h) >= 1, (
            "P1 round-3 regression: new counter after instrument-only failure "
            "was dropped — stale batch_seq still in effect"
        )
        print(f"  ✓ new window after failure landed: {rows_h}")

        # --------------------------------------------------------------
        # [4g] Regression: Codex P2 round-4 — periodic loop wakes for instrument-only
        #
        # 纯前端互动用户 _dirty 永远是 False。如果 _periodic_save_loop 只在
        # _dirty=True 时调 save()，instrument 数据只能等 atexit 才发，不是
        # 设计的 60s 节奏。loop 必须同时检查 instrument.has_data。
        # --------------------------------------------------------------
        print("\n[4g] Regression: periodic loop wakes for instrument-only data...")
        import asyncio as _asyncio

        async def _periodic_wake_test():
            Instrument._instance = None
            TokenTracker._instance = None
            tt_mod._TELEMETRY_SERVER_URL = SERVER_URL
            tracker_i = TokenTracker.get_instance()
            # 加速 loop：500ms 一个 tick 让 smoke 不用等 60s
            tracker_i._save_interval = 0.5
            tracker_i._last_report_time = 0
            tracker_i.start_periodic_save()

            # 只灌 counter（不触发 _dirty） —— 模拟纯前端互动
            counter("periodic_wake_test", 1, scenario="instrument_only")

            # 等够 2 个 tick 让 loop 至少跑一次
            await _asyncio.sleep(1.3)

            # 停 task 防泄漏（smoke 进程下来还要做后续步骤）
            task = tracker_i._save_task
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except _asyncio.CancelledError:
                    # 期望路径 —— cancel 主动制造的 CancelledError，吞掉是 idiomatic
                    pass

        _asyncio.run(_periodic_wake_test())

        conn_i = sqlite3.connect(db_path)
        rows_i = conn_i.execute(
            "SELECT metric_key, value FROM instrument_counters "
            "WHERE metric_key LIKE 'periodic_wake_test%'"
        ).fetchall()
        conn_i.close()
        assert len(rows_i) >= 1, (
            "P2 round-4 regression: periodic loop did not wake for "
            "instrument-only data; counter never reached server. "
            "loop must check instrument.has_data() not just self._dirty"
        )
        print(f"  ✓ periodic loop picked up instrument-only: {rows_i}")

        # --------------------------------------------------------------
        # [4h] Regression: Codex P2 round-5 — retry 不能拖走新 instrument
        #
        # 场景：daily-bearing payload server commit 但 client timeout
        # → _pending_batch_seq=A 保留供 retry。期间新 counter 累积 (Y2)。
        # 如果 retry 把 Y2 一起塞进 payload，batch_id=A 已经在 server
        # seen_batches，整个 batch 被 dedupe，Y2 跟着丢（已 clear-on-read）。
        # 修复：retry 不 snapshot instrument，Y2 留在 buffer 等下窗口。
        # --------------------------------------------------------------
        print("\n[4h] Regression: retry doesn't drag fresh instruments...")
        Instrument._instance = None
        TokenTracker._instance = None
        tracker_j = TokenTracker.get_instance()

        # Step A: daily-bearing 失败 → batch_seq 保留
        tt_mod._TELEMETRY_SERVER_URL = "http://127.0.0.1:1"
        tracker_j.record(model="gpt-4o-mini", prompt_tokens=222,
                         completion_tokens=33, total_tokens=255,
                         cached_tokens=0, call_type="conversation")
        tracker_j._last_report_time = 0
        tracker_j.save()
        time.sleep(0.3)
        retained_seq = tracker_j._pending_batch_seq
        assert retained_seq is not None, "daily failure must preserve batch_seq"
        print(f"  daily failed, batch_seq retained = {retained_seq[:12]}...")

        # Step B: 灌新 counter 到 buffer
        counter("fresh_after_retry", 1, surface="index_wide")
        inst_j = Instrument.get_instance()
        assert inst_j.has_data(), "fresh counter should be in buffer"

        # Step C: 恢复 URL，触发 retry（buffer 里有 fresh counter）
        tt_mod._TELEMETRY_SERVER_URL = SERVER_URL
        tracker_j._last_report_time = 0
        tracker_j.save()
        time.sleep(0.5)

        # 关键断言：retry 跑完后 fresh counter **必须仍在 buffer**
        # 如果 retry 把它 snapshot 拿走，server dedupe 返 duplicate，fresh
        # counter 永远不会到 server。
        assert inst_j.has_data(), (
            "P2 round-5 regression: fresh counter consumed by retry snapshot. "
            "Retry must skip instrument snapshot to avoid dedupe-induced loss."
        )
        # 而且 batch_seq 应该被清了（retry 成功）
        assert tracker_j._pending_batch_seq is None, (
            "retry should succeed (server hasn't actually seen this batch_id "
            "in our test setup) and clear batch_seq"
        )
        print("  ✓ retry kept fresh counter in buffer")

        # Step D: 下一窗口（新 batch_seq）发 fresh counter
        tracker_j._last_report_time = 0
        tracker_j.save()
        time.sleep(0.5)
        conn_j = sqlite3.connect(db_path)
        rows_j = conn_j.execute(
            "SELECT metric_key, value FROM instrument_counters "
            "WHERE metric_key LIKE 'fresh_after_retry%'"
        ).fetchall()
        conn_j.close()
        assert len(rows_j) >= 1, (
            "P2 round-5 regression: fresh counter never reached server even "
            "in the window after retry"
        )
        print(f"  ✓ fresh counter landed in next window: {rows_j}")

        # --------------------------------------------------------------
        # [4i] 新增业务埋点端到端：D1 信号 + settings_state + proactive_fired
        #
        # 验证 note_first_user_message / note_core_loop_completed /
        # record_settings_state 产出的 counter/histogram 走完整通道到 server。
        # --------------------------------------------------------------
        print("\n[4i] New telemetry: D1 signals + settings_state...")
        Instrument._instance = None
        TokenTracker._instance = None
        tt_mod._TELEMETRY_SERVER_URL = SERVER_URL
        tracker_k = TokenTracker.get_instance()
        tracker_k._session_start_ts = time.time() - 8.0
        # D1 漏斗
        tracker_k.note_first_user_message("text")
        tracker_k.note_core_loop_completed()
        # settings_state 走**真正的** record_settings_state（读 preferences +
        # 分桶 + privacy 取反逻辑），而不是手写 counter——否则分桶/取反/读盘
        # 逻辑坏了 smoke 也发现不了（CodeRabbit 指出）。
        tt_mod.record_settings_state()
        # proactive + D1 错误（直接走 instrument，模拟各 hook 点）
        counter("proactive_fired", 1, channel="vision")
        counter("llm_error", 1, error_class="TimeoutError")
        counter("api_key_invalid", 1)
        counter("tts_error", 1, code="API_KEY_REJECTED")
        histogram("llm_ttft_ms", 850.0)
        tracker_k._last_report_time = 0
        tracker_k.save()
        time.sleep(0.5)

        conn_k = sqlite3.connect(db_path)
        ic = {r[0]: r[1] for r in conn_k.execute(
            "SELECT metric_key, value FROM instrument_counters WHERE metric_key IN "
            "('first_message_sent|input_type=text','core_loop_completed',"
            "'proactive_fired|channel=vision','llm_error|error_class=TimeoutError',"
            "'api_key_invalid','tts_error|code=API_KEY_REJECTED') "
            "OR metric_key LIKE 'settings_state|%'"
        ).fetchall()}
        ih = {r[0]: r[1] for r in conn_k.execute(
            "SELECT metric_key, count FROM instrument_histograms "
            "WHERE metric_key LIKE 'llm_ttft_ms%' OR metric_key LIKE 'time_to_first_message_sec%'"
        ).fetchall()}
        conn_k.close()
        print(f"  counters landed: {sorted(ic.keys())}")
        print(f"  histograms landed: {sorted(ih.keys())}")
        for need in ("first_message_sent|input_type=text", "core_loop_completed",
                     "proactive_fired|channel=vision", "llm_error|error_class=TimeoutError",
                     "api_key_invalid", "tts_error|code=API_KEY_REJECTED"):
            assert need in ic, f"new telemetry counter missing: {need}"
        assert any(k.startswith("settings_state|") for k in ic), "settings_state missing"
        assert any(k.startswith("llm_ttft_ms") for k in ih), "llm_ttft_ms missing"
        assert any(k.startswith("time_to_first_message_sec") for k in ih), "time_to_first_message_sec missing"
        print("  ✓ all new D1 + settings + proactive telemetry reached server")

        # --------------------------------------------------------------
        # [4j] Regression: Codex P2 — 伪造坏 stat_date 不能落成垃圾分区
        #
        # HMAC 密钥在开源客户端可读，伪造 payload 能塞 "9999-99-99" 这类长度
        # 和 dash 位置都过、但非法的日期。server 必须 date.fromisoformat 校验，
        # 坏值回退到 window_end/今天，否则字典序 retention 永远 prune 不掉。
        # --------------------------------------------------------------
        print("\n[4j] Regression: malformed stat_date rejected...")
        bad_payload = {
            "device_id": "j" * 64, "app_version": "2.0.0", "branch": "main",
            "locale": "en-US", "timezone": "UTC", "distribution": "source",
            "steam_user_id": "", "daily_stats": {}, "recent_records": [],
            "instruments": {
                "window_start": time.time() - 60, "window_end": time.time(),
                "stat_date": "9999-99-99",  # 伪造的非法日期
                "bounds": [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000],
                "counters": {"forged_stat_date_counter": 1},
                "histograms": {},
            },
        }
        s_bad, _ = _sign_and_submit(bad_payload, gzip_it=False, batch_id="smoke-baddate-1")
        assert s_bad == 200, f"bad-date payload should still be accepted (HTTP {s_bad})"
        time.sleep(0.3)
        conn_bad = sqlite3.connect(db_path)
        bad_rows = conn_bad.execute(
            "SELECT stat_date FROM instrument_counters "
            "WHERE metric_key = 'forged_stat_date_counter'"
        ).fetchall()
        conn_bad.close()
        landed_dates = [r[0] for r in bad_rows]
        print(f"  forged counter landed under stat_date(s): {landed_dates}")
        assert landed_dates, "forged counter should still be stored (under fallback date)"
        assert "9999-99-99" not in landed_dates, (
            "regression: malformed stat_date '9999-99-99' was persisted "
            "as a real partition key — date.fromisoformat validation not working"
        )
        print("  ✓ malformed stat_date rejected, fell back to a valid date")

        # 第二个 case：parse-pass 但越界的未来日期（9999-12-31）。fromisoformat
        # 接受它，但 ±2 天范围约束必须拦下，否则字典序 retention 永远 prune
        # 不掉（CodeRabbit）。
        oor_payload = {
            "device_id": "k" * 64, "app_version": "2.0.0", "branch": "main",
            "locale": "en-US", "timezone": "UTC", "distribution": "source",
            "steam_user_id": "", "daily_stats": {}, "recent_records": [],
            "instruments": {
                "window_start": time.time() - 60, "window_end": time.time(),
                "stat_date": "9999-12-31",  # 可解析但越界的未来日期
                "bounds": [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000],
                "counters": {"oor_stat_date_counter": 1},
                "histograms": {},
            },
        }
        s_oor, _ = _sign_and_submit(oor_payload, gzip_it=False, batch_id="smoke-oordate-1")
        assert s_oor == 200, f"out-of-range-date payload should still be accepted (HTTP {s_oor})"
        time.sleep(0.3)
        conn_oor = sqlite3.connect(db_path)
        oor_rows = conn_oor.execute(
            "SELECT stat_date FROM instrument_counters "
            "WHERE metric_key = 'oor_stat_date_counter'"
        ).fetchall()
        conn_oor.close()
        oor_dates = [r[0] for r in oor_rows]
        print(f"  out-of-range counter landed under stat_date(s): {oor_dates}")
        assert oor_dates, "oor counter should still be stored (under fallback date)"
        assert "9999-12-31" not in oor_dates, (
            "regression: out-of-range stat_date '9999-12-31' was persisted as a "
            "real partition key — range check (±2 days) not working"
        )
        print("  ✓ out-of-range stat_date rejected, fell back to a valid date")

        # 第三个 case：client_date 无效（走 window_end fallback）但 window_end
        # 是远期时间戳。fallback 路径也必须过同一 recency 校验，否则偏斜时钟/
        # 伪造 window_end 能造远期分区逃过 retention（Codex P2）。
        we_payload = {
            "device_id": "p" * 64, "app_version": "2.0.0", "branch": "main",
            "locale": "en-US", "timezone": "UTC", "distribution": "source",
            "steam_user_id": "", "daily_stats": {}, "recent_records": [],
            "instruments": {
                "window_start": time.time(),
                "window_end": time.time() + 5 * 365 * 86400,  # ~5 年后
                "stat_date": "not-a-valid-date",  # len!=10 → 跳过 client_date，走 window_end
                "bounds": [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000],
                "counters": {"we_fallback_counter": 1},
                "histograms": {},
            },
        }
        s_we, _ = _sign_and_submit(we_payload, gzip_it=False, batch_id="smoke-wedate-1")
        assert s_we == 200, f"window_end-fallback payload should be accepted (HTTP {s_we})"
        time.sleep(0.3)
        conn_we = sqlite3.connect(db_path)
        we_dates = [r[0] for r in conn_we.execute(
            "SELECT stat_date FROM instrument_counters WHERE metric_key='we_fallback_counter'"
        ).fetchall()]
        conn_we.close()
        _today = time.strftime("%Y-%m-%d")
        print(f"  window_end-fallback counter landed under stat_date(s): {we_dates}")
        assert we_dates and all(d == _today for d in we_dates), (
            "Codex P2 regression: far-future window_end bypassed recency guard — "
            f"landed under {we_dates} instead of today {_today}"
        )
        print("  ✓ far-future window_end fallback rejected, fell back to today")

        # NaN counter 不能炸整批：含 NaN 的 payload 仍 200，NaN 样本被跳过，
        # 同批的合法 counter 照常入库（Codex P2）。
        nan_payload = {
            "device_id": "m" * 64, "app_version": "2.0.0", "branch": "main",
            "locale": "en-US", "timezone": "UTC", "distribution": "source",
            "steam_user_id": "", "daily_stats": {}, "recent_records": [],
            "instruments": {
                "window_start": time.time() - 60, "window_end": time.time(),
                "stat_date": time.strftime("%Y-%m-%d"),
                "bounds": [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000],
                # NaN 通过 JSON 'NaN' token 传入（Python json 默认接受）
                "counters": {"nan_counter": float("nan"), "good_counter_beside_nan": 7},
                "histograms": {},
            },
        }
        # _sign_and_submit 内部 json.dumps（默认 allow_nan=True）会把 NaN 序列化
        # 成 'NaN' token，server json.loads 默认接受 → 进 storage 被 isfinite 拦下。
        s_nan, _ = _sign_and_submit(nan_payload, gzip_it=False, batch_id="smoke-nan-1")
        assert s_nan == 200, f"NaN payload should be accepted, bad sample skipped (HTTP {s_nan})"
        time.sleep(0.3)
        conn_nan = sqlite3.connect(db_path)
        good_beside = conn_nan.execute(
            "SELECT value FROM instrument_counters WHERE metric_key = 'good_counter_beside_nan'"
        ).fetchone()
        nan_landed = conn_nan.execute(
            "SELECT COUNT(*) FROM instrument_counters WHERE metric_key = 'nan_counter'"
        ).fetchone()[0]
        conn_nan.close()
        assert good_beside is not None and good_beside[0] == 7, (
            "Codex P2 regression: NaN sample rolled back the whole batch — "
            "valid counter beside it was lost"
        )
        assert nan_landed == 0, "NaN counter should have been skipped, not stored"
        print("  ✓ NaN counter skipped, valid counter in same batch survived")

        # 形状不自洽的 histogram（count=100 但 sum(buckets)=2）必须被跳过，
        # 否则 dashboard 的 avg(用 count) 与 p50/p95(用 buckets) 打架（CodeRabbit）。
        shape_payload = {
            "device_id": "n" * 64, "app_version": "2.0.0", "branch": "main",
            "locale": "en-US", "timezone": "UTC", "distribution": "source",
            "steam_user_id": "", "daily_stats": {}, "recent_records": [],
            "instruments": {
                "window_start": time.time() - 60, "window_end": time.time(),
                "stat_date": time.strftime("%Y-%m-%d"),
                "bounds": [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000],
                "counters": {},
                # 注：fractional count/bucket（如 0.5）在 Pydantic HistogramStat
                # (count:int, buckets:List[int]) 层就被拒成 400，到不了 storage；
                # storage 的整数归一化是 defense-in-depth。所以这里只用整数值的
                # 形状不自洽 case 测 storage 跳过逻辑。
                "histograms": {
                    "bad_shape_hist": {  # count=100 但 buckets 只加起来 =2
                        "count": 100, "sum": 50.0,
                        "buckets": [0, 0, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
                    },
                    "good_shape_hist": {  # 自洽：sum(buckets)=3=count
                        "count": 3, "sum": 18.0,
                        "buckets": [0, 0, 1, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
                    },
                },
            },
        }
        s_shape, _ = _sign_and_submit(shape_payload, gzip_it=False, batch_id="smoke-shape-1")
        assert s_shape == 200, f"shape payload should be accepted, bad hist skipped (HTTP {s_shape})"
        time.sleep(0.3)
        conn_shape = sqlite3.connect(db_path)
        bad_hist = conn_shape.execute(
            "SELECT COUNT(*) FROM instrument_histograms WHERE metric_key='bad_shape_hist'"
        ).fetchone()[0]
        good_hist = conn_shape.execute(
            "SELECT count FROM instrument_histograms WHERE metric_key='good_shape_hist'"
        ).fetchone()
        conn_shape.close()
        assert bad_hist == 0, (
            "CodeRabbit regression: inconsistent histogram (sum(buckets)!=count) "
            "was stored — shape validation not working"
        )
        assert good_hist is not None and good_hist[0] == 3, (
            "self-consistent histogram in same batch should still be stored"
        )
        print("  ✓ inconsistent histogram skipped, consistent one in same batch stored")

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
