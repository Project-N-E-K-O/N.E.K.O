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

"""Verify that plugins can read the conversation feed.

Queries the message-plane ``conversations`` store directly — the same records a
plugin reads via ``await ctx.bus.conversations.get(since_ts=...)`` — so nothing
has to be installed to check what a plugin gets and whether the timestamps hold::

    uv run python scripts/plugin_conversation_probe.py             # current content
    uv run python scripts/plugin_conversation_probe.py --follow    # live tail
    uv run python scripts/plugin_conversation_probe.py --self-test # offline self-check

Each line: local time | turn_type | role | channel | content.

Endpoint resolution: ``--rpc`` > ``NEKO_MESSAGE_PLANE_ZMQ_RPC_ENDPOINT`` >
``tcp://127.0.0.1:38865``; when that is dead it scans 38860-38890 and says so.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import zmq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 供 --self-test 导入 plugin.*

_DEFAULT_RPC = "tcp://127.0.0.1:38865"
_DEFAULT_STORE = "conversations"


def _iso(ts: float | None) -> str:
    if not ts:
        return "-"
    try:
        return datetime.fromtimestamp(float(ts)).astimezone().strftime("%H:%M:%S.%f")[:-3]
    except Exception:
        return "-"


def _query(sock: zmq.Socket, op: str, args: dict, req_id: str, timeout_ms: int = 3000) -> dict:
    sock.send_json({"v": 1, "op": op, "req_id": req_id, "args": args})
    if not sock.poll(timeout_ms, zmq.POLLIN):
        raise TimeoutError(f"{op} 超时（{timeout_ms}ms）——宿主/插件服务器没在跑？")
    return sock.recv_json()


def _pick_endpoint(explicit: str | None) -> str:
    if explicit:
        return explicit
    env = (os.getenv("NEKO_MESSAGE_PLANE_ZMQ_RPC_ENDPOINT") or "").strip()
    if env:
        return env
    return _DEFAULT_RPC


def _connect(endpoint: str) -> zmq.Socket:
    ctx = zmq.Context.instance()
    sock = ctx.socket(zmq.DEALER)
    sock.linger = 0
    sock.connect(endpoint)
    return sock


def _scan_endpoints(ctx: zmq.Context) -> str | None:
    """Look for a live plugin server in the usual port range."""
    for port in range(38860, 38891):
        endpoint = f"tcp://127.0.0.1:{port}"
        sock = ctx.socket(zmq.DEALER)
        sock.linger = 0
        try:
            sock.connect(endpoint)
            sock.send_json({"v": 1, "op": "ping", "req_id": "scan", "args": {}})
            if sock.poll(150, zmq.POLLIN):
                sock.recv()
                return endpoint
        except Exception:
            pass
        finally:
            sock.close(linger=0)
    return None


def _record_line(item: dict) -> str:
    payload = item.get("payload") if isinstance(item, dict) else None
    if not isinstance(payload, dict):
        payload = item if isinstance(item, dict) else {}
    meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    ts = meta.get("ts") or payload.get("timestamp")
    return " | ".join([
        _iso(ts),
        str(meta.get("turn_type") or payload.get("type") or "-"),
        str(meta.get("role") or "-"),
        str(meta.get("channel") or ("voice" if meta.get("is_voice") else "text")),
        str(payload.get("content") or "").replace("\n", " ")[:90],
    ])


def _effective_ts(item: dict) -> float:
    """Display time of a record: producer ts first, then forward time, then seq."""
    payload = item.get("payload") if isinstance(item, dict) else None
    if not isinstance(payload, dict):
        payload = item if isinstance(item, dict) else {}
    meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    for candidate in (meta.get("ts"), payload.get("timestamp"),
                      (item or {}).get("ts"), (item or {}).get("seq")):
        try:
            if candidate is not None:
                return float(candidate)
        except (TypeError, ValueError):
            continue
    return 0.0


def _dedupe_key(item: dict) -> str:
    payload = item.get("payload") if isinstance(item, dict) else None
    if not isinstance(payload, dict):
        payload = item if isinstance(item, dict) else {}
    meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    return f"{payload.get('id')}|{meta.get('ts')}|{payload.get('content')}"


def _fetch_page(sock: zmq.Socket, *, store: str, limit: int,
                since_ts: float | None, until_ts: float | None,
                req_id: str) -> list[dict]:
    """One bus.query page, oldest-first by the timestamp it is displayed with."""
    payload: dict = {"store": store, "topic": "all", "limit": int(limit)}
    if since_ts is not None:
        payload["since_ts"] = float(since_ts)
    if until_ts is not None:
        payload["until_ts"] = float(until_ts)
    resp = _query(sock, "bus.query", payload, req_id)
    if not resp.get("ok"):
        raise RuntimeError(f"bus.query rejected: {resp.get('error')}")
    items = list((resp.get("result") or {}).get("items") or [])
    items.sort(key=_effective_ts)
    return items


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rpc", default=None, help=f"插件服务器 RPC 端点（默认 {_DEFAULT_RPC}）")
    ap.add_argument("--store", default=_DEFAULT_STORE, help="要查的 store（默认 conversations）")
    ap.add_argument("--limit", type=int, default=50, help="每次拉取的条数上限")
    ap.add_argument("--since-ts", type=float, default=None, help="只看这个时间戳之后的")
    ap.add_argument("--follow", action="store_true", help="持续跟读新对话")
    ap.add_argument("--interval", type=float, default=1.0, help="跟读轮询间隔（秒）")
    ap.add_argument("--seconds", type=float, default=0.0, help="跟读时长；0=不限")
    ap.add_argument("--show-all", action="store_true", help="跟读时也打印已有的旧记录")
    ap.add_argument("--self-test", action="store_true",
                    help="本机起一个内存总线并塞两条对话，自检脚本链路（不需要宿主）")
    args = ap.parse_args()

    if args.self_test:
        return _self_test()

    ctx = zmq.Context.instance()
    endpoint = _pick_endpoint(args.rpc)
    sock = _connect(endpoint)
    try:
        resp = _query(sock, "ping", {}, "ping-1", timeout_ms=1500)
        if not resp.get("ok"):
            print(f"[probe] {endpoint} 有响应但不是插件服务器: {resp}")
            return 2
    except Exception as exc:
        print(f"[probe] {endpoint} 连不上（{exc}）")
        found = _scan_endpoints(ctx)
        if not found:
            print("[probe] 38860-38890 里也没有活的插件服务器 —— 先启动宿主。")
            return 2
        print(f"[probe] 换用扫描到的端点: {found}")
        endpoint = found
        sock = _connect(endpoint)

    print(f"[probe] 端点 {endpoint}；store={args.store}")
    print("[probe] 每行：时间 | turn_type | role | channel | content")

    seen_prev: set[str] = set()
    deadline = time.time() + args.seconds if args.seconds else None
    since_ts = args.since_ts
    rounds = 0
    while True:
        try:
            items = _fetch_page(
                sock, store=args.store, limit=int(args.limit),
                since_ts=since_ts, until_ts=None, req_id=f"q-{rounds}",
            )
        except Exception as exc:
            print(f"[probe] 查询失败: {exc}")
            return 2
        # A full page means the server handed back only the newest slice: walk
        # backwards with until_ts so a burst bigger than --limit loses nothing.
        if items and len(items) >= int(args.limit):
            older: list[dict] = []
            boundary = _effective_ts(items[0])
            for hop in range(20):
                try:
                    batch = _fetch_page(
                        sock, store=args.store, limit=int(args.limit),
                        since_ts=since_ts, until_ts=boundary - 1e-6,
                        req_id=f"q-{rounds}-b{hop}",
                    )
                except Exception as exc:
                    print(f"[probe] 回补失败: {exc}")
                    break
                if not batch:
                    break
                older = batch + older
                if len(batch) < int(args.limit):
                    break
                boundary = _effective_ts(batch[0])
            items = older + items
            if len(older) >= 20 * int(args.limit):
                print("[probe] 注意：回补达到上限，可能仍有记录未取到，请调大 --limit")

        printed = 0
        round_keys: set[str] = set()
        for item in items:
            key = _dedupe_key(item)
            round_keys.add(key)
            # Records sharing the boundary timestamp come back again next round;
            # only the previous page's keys need remembering.
            if key in seen_prev:
                continue
            if rounds == 0 and not args.follow and not args.show_all:
                pass  # 首轮（非跟读）就是要看现状，照常打印
            elif rounds == 0 and args.follow and not args.show_all:
                continue  # 跟读模式默认只看之后的
            print("[probe] " + _record_line(item))
            printed += 1

        if rounds == 0 and printed == 0:
            print("[probe] （store 里还没有记录：等一次对话/一次主动搭话再看，或还没重启宿主）")
        if not args.follow:
            break
        # Advance the server-side cursor so older records can't be re-fetched and
        # a burst larger than --limit is detected instead of silently skipped.
        if items:
            newest = max(_effective_ts(item) for item in items)
            since_ts = newest if since_ts is None else max(float(since_ts), newest)
        seen_prev = round_keys
        rounds += 1
        if deadline and time.time() >= deadline:
            break
        time.sleep(max(0.2, args.interval))

    print(
        "\n[probe] 插件侧等价写法:\n"
        "    records = await self.bus.conversations.get(since_ts=last_ts, max_count=200)\n"
        "    for rec in records:\n"
        "        role = (rec.metadata or {}).get('role')      # master | cat\n"
        "        ts = (rec.metadata or {}).get('ts') or rec.timestamp\n"
    )
    return 0


def _self_test() -> int:
    """Start an in-memory bus, seed two turns, then read them back."""
    import threading

    from plugin.message_plane.rpc_server import MessagePlaneRpcServer
    from plugin.message_plane.stores import build_default_store_registry

    endpoint = "tcp://127.0.0.1:38899"
    registry = build_default_store_registry(maxlen=64, frames_maxlen=4)
    store = registry.get(_DEFAULT_STORE)
    assert store is not None
    now = time.time()
    # Seeded out of order on purpose: display order must follow metadata.ts.
    store.publish("all", {
        "kind": "conversation", "type": "conversation_turn",
        "source": "main_logic.core", "timestamp": now - 1.0,
        "content": "在吗", "metadata": {"role": "master", "ts": now - 1.0},
    })
    store.publish("all", {
        "kind": "conversation", "type": "conversation_turn",
        "source": "main_logic.core", "timestamp": now - 3.0,
        "content": "喵，我在的。",
        "metadata": {"role": "cat", "ts": now - 3.0, "turn_type": "proactive_reply"},
    })

    server = MessagePlaneRpcServer(endpoint=endpoint, stores=registry)
    thread = threading.Thread(target=server.serve_forever, name="probe-self-test", daemon=True)
    thread.start()
    print(f"[self-test] 内存总线起在 {endpoint}，塞了 1 条主人 + 1 条猫娘对话")

    sock = _connect(endpoint)
    try:
        for _ in range(20):
            try:
                if _query(sock, "ping", {}, "st-ping", timeout_ms=300).get("ok"):
                    break
            except Exception:
                time.sleep(0.1)
        resp = _query(sock, "bus.query", {"store": _DEFAULT_STORE, "topic": "all", "limit": 10},
                      "st-query")
        items = list((resp.get("result") or {}).get("items") or [])
        items.sort(key=_effective_ts)
        print(f"[self-test] 读回 {len(items)} 条：")
        for item in items:
            print("[self-test] " + _record_line(item))
        lines = [_record_line(item) for item in items]
        ok = len(lines) == 2 and "cat" in lines[0] and "master" in lines[1]
        print("[self-test]", "OK —— 插件读到的就是这两条" if ok else f"FAIL: {lines}")
        return 0 if ok else 1
    finally:
        sock.close(linger=0)
        server.stop()
        server.close()


if __name__ == "__main__":
    sys.exit(main())
