"""战雷全量数据抓包采集器。

直接拉取游戏 8111 的**所有**原始接口（不止 HUD），按时间戳落盘到专门文件夹，
供后续离线分析与关键词校准（如 HUD 自机通知 _SELF_NOTICES 的措辞核对）。

采集内容（单线程定时轮询，各接口按自己的间隔）：
    /state             载具仪表          0.5s   -> state.jsonl
    /indicators        座舱原始数据      0.5s   -> indicators.jsonl
    /hudmsg            击杀/事件(增量)   1.0s   -> hudmsg.jsonl（逐条，无损）
    /gamechat          聊天(增量)        1.0s   -> gamechat.jsonl（逐条，无损）
    /map_obj.json      地图单位          1.0s   -> map_obj.jsonl
    /map_info.json     坐标换算参数      2.0s   -> map_info.jsonl
    /mission.json      任务状态          2.0s   -> mission.jsonl
    /map.img           小地图底图        5.0s   -> maps/map_<gen>.<ext>（变化才存）
    (可选) 8112 加工层  完整快照     可调(默认1.0s) -> processed_8112.jsonl

每个 .jsonl 文件一行一条记录，含 `ts`（采集时间戳，秒）。原始 JSON 直接内嵌，
解析失败时存原始文本到 `raw_text`。

用法：
    python wt_capture.py                          # 抓 10 分钟，存到 captures/<时间戳>/
    python wt_capture.py --duration 300           # 抓 5 分钟
    python wt_capture.py --no-server              # 不抓 8112 加工层
    python wt_capture.py --out-dir D:/caps         # 自定义根目录
    python wt_capture.py --label overspeed         # 给本次会话打场景标签（写入 meta.json）
    python wt_capture.py --server-interval 0.2     # 加工层快照提到 5Hz（抓快瞬变：超速/失速）
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from typing import Any

DEFAULT_WT_HOST = "127.0.0.1"
DEFAULT_WT_PORT = 8111
DEFAULT_SERVER_PORT = 8112
DEFAULT_DURATION = 600.0  # 秒（10 分钟）
FETCH_TIMEOUT = 1.0       # 单次请求超时（秒）

_IMAGE_MAGIC: dict[bytes, str] = {
    b"\xff\xd8\xff": "jpg",
    b"\x89PNG\r\n\x1a\n": "png",
}


def _detect_image_ext(data: bytes) -> str | None:
    for magic, ext in _IMAGE_MAGIC.items():
        if data.startswith(magic):
            return ext
    return None


def _fetch_text(url: str, timeout: float = FETCH_TIMEOUT) -> tuple[bool, int | None, bytes | None]:
    """请求一个接口，返回 (connected, http_status, body_bytes)。

    connected=False -> 端口连不上（游戏没开/拒绝/超时）。
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return True, getattr(resp, "status", 200), resp.read()
    except urllib.error.HTTPError as exc:
        return True, exc.code, None
    except (urllib.error.URLError, socket.timeout, ConnectionError, OSError):
        return False, None, None
    except Exception:
        return False, None, None


def _parse_json(body: bytes | None) -> tuple[Any, str | None]:
    """尝试把字节解析成 JSON，返回 (data, raw_text_if_failed)。"""
    if not body:
        return None, None
    try:
        return json.loads(body.decode("utf-8", errors="replace")), None
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, body.decode("utf-8", errors="replace")


class JsonlWriter:
    """按需打开的 JSONL 追加写入器（行缓冲，进程中途崩溃也不丢已写入行）。"""

    def __init__(self, path: str) -> None:
        self.path = path
        self._fh = open(path, "a", encoding="utf-8", buffering=1)
        self.count = 0

    def write(self, record: dict[str, Any]) -> None:
        self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.count += 1

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass


class Capturer:
    """单线程定时抓包，把各接口数据落盘到一个会话文件夹。"""

    def __init__(
        self,
        wt_base: str,
        server_base: str | None,
        out_dir: str,
        label: str = "",
    ) -> None:
        self.wt_base = wt_base.rstrip("/")
        self.server_base = server_base.rstrip("/") if server_base else None
        self.label = label
        self.session_dir = out_dir
        self.maps_dir = os.path.join(out_dir, "maps")
        os.makedirs(self.maps_dir, exist_ok=True)
        self._server_interval = 1.0  # 由 run() 覆盖

        # 各 JSONL 写入器
        self._w: dict[str, JsonlWriter] = {
            name: JsonlWriter(os.path.join(out_dir, f"{name}.jsonl"))
            for name in (
                "state", "indicators", "hudmsg", "gamechat",
                "map_obj", "map_info", "mission", "processed_8112",
            )
        }

        # 增量游标
        self._last_evt = 0
        self._last_dmg = 0
        self._last_chat = 0
        self._last_map_gen: int | None = None
        self._map_saved = 0

        # 统计
        self._connected_ticks = 0
        self._total_ticks = 0
        self._started_at = time.time()

    # -- 各接口采集 --------------------------------------------------------

    def _snap_json(self, writer_key: str, path: str) -> bool:
        """拉取一个 JSON 接口并整条落盘。返回是否 connected。"""
        connected, status, body = _fetch_text(f"{self.wt_base}{path}")
        if not connected:
            return False
        data, raw_text = _parse_json(body)
        rec: dict[str, Any] = {"ts": round(time.time(), 3), "status": status}
        if raw_text is not None:
            rec["raw_text"] = raw_text
        else:
            rec["data"] = data
        self._w[writer_key].write(rec)
        return True

    def _snap_hudmsg(self) -> None:
        url = f"{self.wt_base}/hudmsg?lastEvt={self._last_evt}&lastDmg={self._last_dmg}"
        connected, status, body = _fetch_text(url)
        if not connected:
            return
        data, raw_text = _parse_json(body)
        ts = round(time.time(), 3)
        if not isinstance(data, dict):
            if raw_text:
                self._w["hudmsg"].write({"ts": ts, "stream": "_raw", "raw_text": raw_text})
            return
        for ev in data.get("events", []) or []:
            if not isinstance(ev, dict):
                continue
            eid = int(ev.get("id", 0) or 0)
            self._last_evt = max(self._last_evt, eid)
            self._w["hudmsg"].write({"ts": ts, "stream": "event", **ev})
        for dm in data.get("damage", []) or []:
            if not isinstance(dm, dict):
                continue
            did = int(dm.get("id", 0) or 0)
            self._last_dmg = max(self._last_dmg, did)
            self._w["hudmsg"].write({"ts": ts, "stream": "damage", **dm})

    def _snap_gamechat(self) -> None:
        url = f"{self.wt_base}/gamechat?lastId={self._last_chat}"
        connected, status, body = _fetch_text(url)
        if not connected:
            return
        data, raw_text = _parse_json(body)
        ts = round(time.time(), 3)
        if not isinstance(data, list):
            if raw_text:
                self._w["gamechat"].write({"ts": ts, "raw_text": raw_text})
            return
        for msg in data:
            if isinstance(msg, dict):
                self._last_chat = max(self._last_chat, int(msg.get("id", 0) or 0))
                self._w["gamechat"].write({"ts": ts, **msg})

    def _snap_mapimg(self) -> None:
        # 先看 map_info 的 generation，变化才存（避免重复写同一张）
        connected, _, body = _fetch_text(f"{self.wt_base}/map_info.json")
        if not connected:
            return
        info, _ = _parse_json(body)
        if not isinstance(info, dict) or not info.get("valid", False):
            return
        gen = info.get("map_generation")
        gen = int(gen) if isinstance(gen, (int, float)) else None
        if gen is not None and gen == self._last_map_gen:
            return
        connected, _, img = _fetch_text(f"{self.wt_base}/map.img", timeout=2.0)
        if not connected or not img:
            return
        ext = _detect_image_ext(img)
        if ext is None:
            return
        name = f"map_{gen}.{ext}" if gen is not None else f"map_{int(time.time())}.{ext}"
        try:
            with open(os.path.join(self.maps_dir, name), "wb") as fh:
                fh.write(img)
            self._last_map_gen = gen
            self._map_saved += 1
        except OSError:
            pass

    def _snap_server(self) -> None:
        if not self.server_base:
            return
        connected, status, body = _fetch_text(f"{self.server_base}/api/telemetry", timeout=2.0)
        if not connected:
            return
        data, raw_text = _parse_json(body)
        rec: dict[str, Any] = {"ts": round(time.time(), 3), "status": status}
        if raw_text is not None:
            rec["raw_text"] = raw_text
        else:
            rec["data"] = data
        self._w["processed_8112"].write(rec)

    # -- 主循环 ------------------------------------------------------------

    def run(self, duration: float, server_interval: float = 1.0) -> None:
        """按到期时间调度各采集组（支持任意 server_interval，含亚秒级高频）。"""
        self._server_interval = server_interval
        deadline = self._started_at + duration
        # 各采集组的间隔（秒）
        intervals = {
            "fast": 0.5,                  # state + indicators
            "hud": 1.0,                   # hudmsg + gamechat + map_obj
            "slow": 2.0,                  # map_info + mission
            "mapimg": 5.0,                # 小地图底图
        }
        if self.server_base:
            intervals["server"] = max(0.05, server_interval)
        # 睡眠粒度：取最小间隔的一半，钳到 [0.02, 0.25]，保证高频组能按时触发
        gran = max(0.02, min(0.25, min(intervals.values()) / 2))
        due = {k: self._started_at for k in intervals}
        last_progress = self._started_at
        try:
            while True:
                now = time.time()
                if now >= deadline:
                    break

                if now >= due["fast"]:
                    self._total_ticks += 1
                    c1 = self._snap_json("state", "/state")
                    c2 = self._snap_json("indicators", "/indicators")
                    if c1 or c2:
                        self._connected_ticks += 1
                    due["fast"] = now + intervals["fast"]

                if now >= due["hud"]:
                    self._snap_hudmsg()
                    self._snap_gamechat()
                    self._snap_json("map_obj", "/map_obj.json")
                    due["hud"] = now + intervals["hud"]

                if "server" in due and now >= due["server"]:
                    self._snap_server()
                    due["server"] = now + intervals["server"]

                if now >= due["slow"]:
                    self._snap_json("map_info", "/map_info.json")
                    self._snap_json("mission", "/mission.json")
                    due["slow"] = now + intervals["slow"]

                if now >= due["mapimg"]:
                    self._snap_mapimg()
                    due["mapimg"] = now + intervals["mapimg"]

                if now - last_progress >= 30.0:
                    self._print_progress(deadline - now)
                    last_progress = now

                time.sleep(gran)
        except KeyboardInterrupt:
            print("\n[中断] 收到 Ctrl+C，正在收尾…")

    def _print_progress(self, remaining: float) -> None:
        online = "在线" if self._connected_ticks > 0 else "离线(等待游戏)"
        print(
            f"  [{time.strftime('%H:%M:%S')}] 剩余 {remaining:5.0f}s | {online} | "
            f"hud={self._w['hudmsg'].count} chat={self._w['gamechat'].count} "
            f"state={self._w['state'].count} ind={self._w['indicators'].count} "
            f"mapobj={self._w['map_obj'].count} proc={self._w['processed_8112'].count} "
            f"maps={self._map_saved}"
        )

    def finalize(self) -> dict[str, Any]:
        summary = {
            "label": self.label,
            "started_at": self._started_at,
            "ended_at": time.time(),
            "duration_sec": round(time.time() - self._started_at, 1),
            "wt_base": self.wt_base,
            "server_base": self.server_base,
            "server_interval_sec": self._server_interval if self.server_base else None,
            "total_ticks": self._total_ticks,
            "connected_ticks": self._connected_ticks,
            "maps_saved": self._map_saved,
            "counts": {k: w.count for k, w in self._w.items()},
        }
        with open(os.path.join(self.session_dir, "meta.json"), "w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=2)
        for w in self._w.values():
            w.close()
        return summary


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="战雷全量数据抓包采集器")
    parser.add_argument("--host", default=DEFAULT_WT_HOST, help="游戏 8111 地址")
    parser.add_argument("--port", type=int, default=DEFAULT_WT_PORT, help="游戏遥测端口（默认 8111）")
    parser.add_argument("--duration", type=float, default=DEFAULT_DURATION, help="采集时长（秒，默认 600=10 分钟）")
    parser.add_argument("--out-dir", default="captures", help="采集数据根目录（默认 captures）")
    parser.add_argument("--server-port", type=int, default=DEFAULT_SERVER_PORT, help="加工层服务端口（默认 8112）")
    parser.add_argument("--no-server", action="store_true", help="不抓 8112 加工层快照")
    parser.add_argument("--server-interval", type=float, default=1.0, help="加工层 /api/telemetry 快照间隔（秒，默认 1.0；抓超速/失速等快瞬变可设 0.2）")
    parser.add_argument("--label", default="", help="场景标签，写入 meta.json 并加进会话文件夹名（如 overspeed/stall）")
    args = parser.parse_args()

    wt_base = f"http://{args.host}:{args.port}"
    server_base = None if args.no_server else f"http://{args.host}:{args.server_port}"

    # 会话文件夹名带上场景标签，便于事后挑选
    label = args.label.strip()
    _safe = "".join(c for c in label if c.isalnum() or c in "-_") if label else ""
    session = time.strftime("capture_%Y%m%d_%H%M%S")
    if _safe:
        session += f"_{_safe}"
    out_dir = os.path.join(args.out_dir, session)
    os.makedirs(out_dir, exist_ok=True)

    print(f"战雷数据抓包开始：{wt_base}")
    if label:
        print(f"  场景标签：{label}")
    if server_base:
        print(f"  同时尝试抓加工层：{server_base}（间隔 {args.server_interval:.2f}s，连不上会自动跳过）")
    print(f"  时长：{args.duration:.0f}s（{args.duration/60:.1f} 分钟）")
    print(f"  输出：{os.path.abspath(out_dir)}")
    print("  Ctrl+C 可提前结束并收尾\n")

    cap = Capturer(wt_base, server_base, out_dir, label=label)
    cap.run(args.duration, server_interval=args.server_interval)
    summary = cap.finalize()

    print("\n采集结束，汇总：")
    print(f"  时长：{summary['duration_sec']}s  在线节拍：{summary['connected_ticks']}/{summary['total_ticks']}")
    print(f"  各文件条数：{summary['counts']}")
    print(f"  小地图：{summary['maps_saved']} 张")
    print(f"  数据已存到：{os.path.abspath(out_dir)}")
    if summary["connected_ticks"] == 0:
        print("\n  ⚠️ 全程未连上 8111：请确认游戏已启动并进入了战局/测试飞行。")


if __name__ == "__main__":
    main()
