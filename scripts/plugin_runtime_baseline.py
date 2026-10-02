#!/usr/bin/env python3
"""Measure what running N plugins costs: startup time, memory, threads, idle CPU.

Each plugin count runs in a fresh interpreter that starts N synthetic plugins
through the real ``PluginHost`` (the code path the plugin server uses for
autostart), one after another like production autostart, and records:

* per-plugin start latency and total startup wall time;
* the process tree: process count, RSS/USS and thread count per process;
* idle CPU time and context switches over a fixed window (exposes busy polling);
* entry call round-trip latency through ``PluginHost.trigger``;
* shutdown time and processes left alive afterwards.

Storage, logs and temp files are redirected into a throwaway directory before
any project module is imported, so no user data is read or written. Nothing
touches the network. The JSON output holds aggregate numbers only.

Usage (from the repository root)::

    uv run python -m scripts.plugin_runtime_baseline --counts 0,1,5,10
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import psutil

REPO_ROOT = Path(__file__).resolve().parents[1]
MIB = 1024 * 1024

# Plugin children are spawned (Windows) and re-execute the parent's __main__
# module. In production that module is launcher.py, so every plugin child pays
# for launcher's top-level imports. Mirror that when asked to.
if os.environ.get("NEKO_PLUGIN_BASELINE_LAUNCHER_MAIN") == "1":
    import launcher  # noqa: F401

PLUGIN_TOML = """\
[plugin]
id = "{plugin_id}"
name = "Baseline {plugin_id}"
description = "Synthetic plugin for runtime baseline measurements."
version = "0.1.0"
type = "plugin"
entry = "plugins.{plugin_id}:BaselinePlugin"

[plugin.sdk]
supported = ">=0.1.0,<0.3.0"

[plugin_runtime]
enabled = true
auto_start = false
"""

PLUGIN_SOURCE = """\
from plugin.sdk.plugin import NekoPluginBase, Ok, lifecycle, neko_plugin, plugin_entry

PUSH_ON_STARTUP = {push_on_startup!r}
READ_CONFIG_ON_STARTUP = {read_config_on_startup!r}


@neko_plugin
class BaselinePlugin(NekoPluginBase):
    @lifecycle(id="startup")
    async def startup(self, **_):
        if READ_CONFIG_ON_STARTUP:
            await self.config.dump(timeout=10.0)
        if PUSH_ON_STARTUP:
            self.push_message(
                source="{plugin_id}",
                visibility=[],
                ai_behavior="read",
                parts=[{{"type": "text", "text": "baseline"}}],
                priority=0,
                metadata={{}},
            )

    @plugin_entry(id="ping", name="Ping", description="Round-trip probe.")
    async def ping(self, **_):
        return Ok({{"pong": True}})
"""


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return ordered[index]


def _process_sample(proc: psutil.Process) -> dict[str, Any] | None:
    try:
        with proc.oneshot():
            rss = proc.memory_info().rss
            try:
                uss = proc.memory_full_info().uss
            except (psutil.AccessDenied, psutil.NoSuchProcess, AttributeError):
                uss = None
            cpu = proc.cpu_times()
            ctx = proc.num_ctx_switches()
            return {
                "pid": proc.pid,
                "rss": rss,
                "uss": uss,
                "threads": proc.num_threads(),
                "cpu_seconds": cpu.user + cpu.system,
                "ctx_switches": ctx.voluntary + ctx.involuntary,
            }
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def _tree_sample(plugin_pids: set[int]) -> dict[str, Any]:
    me = psutil.Process()
    host = _process_sample(me)
    plugins: list[dict[str, Any]] = []
    others: list[dict[str, Any]] = []
    for child in me.children(recursive=True):
        row = _process_sample(child)
        if row is None:
            continue
        (plugins if child.pid in plugin_pids else others).append(row)
    return {"host": host, "plugins": plugins, "others": others}


def _idle_delta(before: dict[str, Any], after: dict[str, Any], seconds: float) -> dict[str, Any]:
    def by_pid(sample: dict[str, Any], key: str) -> dict[int, dict[str, Any]]:
        return {row["pid"]: row for row in sample[key]}

    def rates(start: dict[str, Any] | None, end: dict[str, Any] | None) -> dict[str, float] | None:
        if start is None or end is None:
            return None
        return {
            "cpu_percent": 100.0 * (end["cpu_seconds"] - start["cpu_seconds"]) / seconds,
            "ctx_switches_per_s": (end["ctx_switches"] - start["ctx_switches"]) / seconds,
        }

    plugin_start = by_pid(before, "plugins")
    plugin_rates = [
        rate
        for pid, row in by_pid(after, "plugins").items()
        if (rate := rates(plugin_start.get(pid), row)) is not None
    ]
    other_start = by_pid(before, "others")
    other_rates = [
        rate
        for pid, row in by_pid(after, "others").items()
        if (rate := rates(other_start.get(pid), row)) is not None
    ]
    return {
        "seconds": seconds,
        "host": rates(before["host"], after["host"]),
        "plugins": plugin_rates,
        "others": other_rates,
    }


def _write_plugin(root: Path, plugin_id: str, *, push_on_startup: bool, read_config_on_startup: bool) -> Path:
    plugin_dir = root / plugin_id
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text(PLUGIN_TOML.format(plugin_id=plugin_id), encoding="utf-8")
    (plugin_dir / "__init__.py").write_text(
        PLUGIN_SOURCE.format(
            plugin_id=plugin_id,
            push_on_startup=push_on_startup,
            read_config_on_startup=read_config_on_startup,
        ),
        encoding="utf-8",
    )
    return plugin_dir / "plugin.toml"


async def _run_one(args: argparse.Namespace) -> dict[str, Any]:
    """Runs inside a fresh interpreter whose environment is already isolated."""
    from plugin.core.host import PluginHost

    count = args.run_one
    plugins_root = Path(os.environ["NEKO_PLUGIN_BASELINE_PLUGINS_ROOT"])
    message_queue: asyncio.Queue = asyncio.Queue()
    result: dict[str, Any] = {"count": count, "after_import": _tree_sample(set())}

    hosts: list[Any] = []
    start_seconds: list[float] = []
    startup_began = time.perf_counter()
    try:
        for index in range(count):
            plugin_id = f"baseline_{index:03d}"
            config_path = _write_plugin(
                plugins_root,
                plugin_id,
                push_on_startup=args.push_on_startup,
                read_config_on_startup=args.read_config_on_startup,
            )
            began = time.perf_counter()
            host = PluginHost(plugin_id=plugin_id, entry_point=f"plugins.{plugin_id}:BaselinePlugin", config_path=config_path)
            hosts.append(host)
            await host.start(
                message_target_queue=message_queue,
                startup_timeout=args.startup_timeout,
                startup_failure="fail",
            )
            start_seconds.append(time.perf_counter() - began)
        result["startup"] = {
            "total_seconds": time.perf_counter() - startup_began,
            "per_plugin_seconds": start_seconds,
        }

        plugin_pids = {host.process.pid for host in hosts if host.process.pid is not None}
        await asyncio.sleep(args.settle)
        result["ready"] = _tree_sample(plugin_pids)
        result["host_asyncio_tasks"] = len(asyncio.all_tasks())

        before = _tree_sample(plugin_pids)
        await asyncio.sleep(args.idle)
        after = _tree_sample(plugin_pids)
        result["idle"] = _idle_delta(before, after, args.idle)

        latencies: list[float] = []
        for _ in range(args.calls):
            for host in hosts:
                began = time.perf_counter()
                reply = await host.trigger("ping", {}, timeout=args.startup_timeout)
                latencies.append(time.perf_counter() - began)
                if not (isinstance(reply, dict) and reply.get("pong") is True):
                    raise RuntimeError("baseline_ping_unexpected_reply")
        result["call_seconds"] = latencies
    finally:
        shutdown_began = time.perf_counter()
        for host in hosts:
            try:
                await host.shutdown(timeout=args.startup_timeout)
            except Exception as exc:  # keep measuring the rest
                result.setdefault("shutdown_errors", []).append(type(exc).__name__)
        result["shutdown_seconds"] = time.perf_counter() - shutdown_began
        await asyncio.sleep(0.5)
        result["leftover_processes"] = len(psutil.Process().children(recursive=True))
    return result


def _isolated_env(sandbox: Path, *, launcher_main: bool) -> dict[str, str]:
    env = dict(os.environ)
    storage = sandbox / "storage"
    temp = sandbox / "temp"
    home = sandbox / "home"
    xdg = {
        "XDG_CONFIG_HOME": home / ".config",
        "XDG_DATA_HOME": home / ".local" / "share",
        "XDG_STATE_HOME": home / ".local" / "state",
        "XDG_CACHE_HOME": home / ".cache",
    }
    for path in (
        storage,
        sandbox / "anchor",
        sandbox / "localappdata",
        sandbox / "appdata",
        temp,
        sandbox / "plugins",
        *xdg.values(),
    ):
        path.mkdir(parents=True, exist_ok=True)
    env.update(
        {
            "NEKO_STORAGE_SELECTED_ROOT": str(storage),
            "NEKO_STORAGE_ANCHOR_ROOT": str(sandbox / "anchor"),
            "LOCALAPPDATA": str(sandbox / "localappdata"),
            "APPDATA": str(sandbox / "appdata"),
            "TEMP": str(temp),
            "TMP": str(temp),
            "TMPDIR": str(temp),
            "HOME": str(home),
            **{name: str(path) for name, path in xdg.items()},
            "NEKO_PLUGIN_BASELINE_PLUGINS_ROOT": str(sandbox / "plugins"),
            "PYTHONUTF8": "1",
        }
    )
    env.pop("NEKO_PLUGIN_BASELINE_LAUNCHER_MAIN", None)
    if launcher_main:
        env["NEKO_PLUGIN_BASELINE_LAUNCHER_MAIN"] = "1"
    return env


def summarize(run: dict[str, Any]) -> dict[str, Any]:
    """Collapse one run into the numbers worth comparing between revisions."""
    ready = run.get("ready") or run["after_import"]
    plugins = ready["plugins"]
    others = ready["others"]
    host = ready["host"] or {}
    tree_rows = [host, *plugins, *others]
    idle = run.get("idle") or {}
    plugin_idle = idle.get("plugins") or []
    calls_ms = [value * 1000 for value in run.get("call_seconds", [])]
    per_plugin = run.get("startup", {}).get("per_plugin_seconds", [])

    def mean(values: list[float]) -> float | None:
        return statistics.fmean(values) if values else None

    return {
        "count": run["count"],
        "startup_total_s": run.get("startup", {}).get("total_seconds"),
        "startup_per_plugin_p50_s": statistics.median(per_plugin) if per_plugin else None,
        "startup_per_plugin_max_s": max(per_plugin) if per_plugin else None,
        "processes": len(tree_rows),
        "tree_rss_mib": sum(row.get("rss") or 0 for row in tree_rows) / MIB,
        "tree_uss_mib": (
            sum(row["uss"] for row in tree_rows)
            / MIB
            if all(row.get("uss") is not None for row in tree_rows)
            else None
        ),
        "plugin_rss_mib_avg": mean([row["rss"] / MIB for row in plugins]),
        "plugin_uss_mib_avg": mean([row["uss"] / MIB for row in plugins if row.get("uss") is not None]),
        "plugin_threads_avg": mean([row["threads"] for row in plugins]),
        "host_threads": host.get("threads"),
        "host_threads_added": (host.get("threads") or 0) - (run["after_import"]["host"] or {}).get("threads", 0),
        "other_processes": len(others),
        "idle_host_cpu_pct": (idle.get("host") or {}).get("cpu_percent"),
        "idle_host_ctx_per_s": (idle.get("host") or {}).get("ctx_switches_per_s"),
        "idle_plugin_cpu_pct_avg": mean([row["cpu_percent"] for row in plugin_idle]),
        "idle_plugin_ctx_per_s_avg": mean([row["ctx_switches_per_s"] for row in plugin_idle]),
        "call_p50_ms": _percentile(calls_ms, 50),
        "call_p95_ms": _percentile(calls_ms, 95),
        "shutdown_s": run.get("shutdown_seconds"),
        "leftover_processes": run.get("leftover_processes"),
        "shutdown_errors": len(run.get("shutdown_errors", [])),
    }


def _format_table(rows: list[dict[str, Any]]) -> str:
    columns = [
        ("N", "count", "{:d}"),
        ("startup s", "startup_total_s", "{:.2f}"),
        ("per-plugin p50 s", "startup_per_plugin_p50_s", "{:.2f}"),
        ("procs", "processes", "{:d}"),
        ("tree RSS MiB", "tree_rss_mib", "{:.0f}"),
        ("tree USS MiB", "tree_uss_mib", "{:.0f}"),
        ("plugin RSS MiB", "plugin_rss_mib_avg", "{:.1f}"),
        ("plugin threads", "plugin_threads_avg", "{:.1f}"),
        ("host threads +", "host_threads_added", "{:d}"),
        ("idle host CPU %", "idle_host_cpu_pct", "{:.2f}"),
        ("idle plugin CPU %", "idle_plugin_cpu_pct_avg", "{:.2f}"),
        ("idle plugin ctx/s", "idle_plugin_ctx_per_s_avg", "{:.0f}"),
        ("call p50 ms", "call_p50_ms", "{:.1f}"),
        ("shutdown s", "shutdown_s", "{:.2f}"),
        ("leftover", "leftover_processes", "{:d}"),
        ("shutdown errors", "shutdown_errors", "{:d}"),
    ]

    def cell(row: dict[str, Any], key: str, fmt: str) -> str:
        value = row.get(key)
        if value is None:
            return "-"
        return fmt.format(int(value) if fmt.endswith("d}") else value)

    lines = [
        "| " + " | ".join(title for title, _, _ in columns) + " |",
        "|" + "|".join("---" for _ in columns) + "|",
    ]
    for row in rows:
        lines.append("| " + " | ".join(cell(row, key, fmt) for _, key, fmt in columns) + " |")
    return "\n".join(lines)


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill the --run-one interpreter and every plugin process it spawned."""
    try:
        children = psutil.Process(proc.pid).children(recursive=True)
    except psutil.NoSuchProcess:
        children = []
    for child in children:
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass
    proc.kill()
    psutil.wait_procs(children, timeout=10)


def _run_isolated(command: list[str], env: dict[str, str], timeout: float) -> tuple[int, str]:
    """Run one count in its own process group; on timeout or Ctrl+C reap the whole tree."""
    group = (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        if sys.platform == "win32"
        else {"start_new_session": True}
    )
    proc = subprocess.Popen(
        command,
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        **group,
    )
    try:
        _, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        _, stderr = proc.communicate()
        return -1, (stderr or "") + f"\nrun timed out after {timeout}s"
    except BaseException:
        _kill_tree(proc)
        raise
    return proc.returncode, stderr or ""


def _git_revision() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _orchestrate(args: argparse.Namespace) -> int:
    counts = [int(value) for value in args.counts.split(",") if value.strip()]
    if any(count < 0 for count in counts):
        raise SystemExit("--counts must be non-negative integers")

    runs: list[dict[str, Any]] = []
    for repeat in range(args.repeat):
        for count in counts:
            sandbox = Path(tempfile.mkdtemp(prefix="neko-plugin-baseline-"))
            try:
                result_path = sandbox / "result.json"
                command = [
                    sys.executable,
                    "-m",
                    "scripts.plugin_runtime_baseline",
                    "--run-one",
                    str(count),
                    "--result",
                    str(result_path),
                    "--startup-timeout",
                    str(args.startup_timeout),
                    "--settle",
                    str(args.settle),
                    "--idle",
                    str(args.idle),
                    "--calls",
                    str(args.calls),
                ]
                if not args.push_on_startup:
                    command.append("--no-push-on-startup")
                if args.read_config_on_startup:
                    command.append("--read-config-on-startup")
                began = time.perf_counter()
                returncode, stderr = _run_isolated(
                    command,
                    _isolated_env(sandbox, launcher_main=args.launcher_main),
                    args.run_timeout,
                )
                if returncode != 0 or not result_path.is_file():
                    tail = stderr.strip().splitlines()[-5:]
                    print(f"[baseline] N={count} failed (exit {returncode})", file=sys.stderr)
                    for line in tail:
                        print(f"[baseline]   {line}", file=sys.stderr)
                    return 1
                run = json.loads(result_path.read_text(encoding="utf-8"))
                run["repeat"] = repeat
                run["interpreter_wall_seconds"] = time.perf_counter() - began
                runs.append(run)
                print(f"[baseline] N={count} repeat={repeat} done", file=sys.stderr)
            finally:
                if args.keep_sandbox:
                    print(f"[baseline] sandbox kept: {sandbox}", file=sys.stderr)
                else:
                    shutil.rmtree(sandbox, ignore_errors=True)

    summaries = [summarize(run) for run in runs]
    payload = {
        "metadata": {
            "git_revision": _git_revision(),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
            "launcher_main": args.launcher_main,
            "push_on_startup": args.push_on_startup,
            "read_config_on_startup": args.read_config_on_startup,
            "idle_seconds": args.idle,
            "calls_per_plugin": args.calls,
            "repeat": args.repeat,
        },
        "summary": summaries,
        "runs": runs,
    }
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[baseline] wrote {output}", file=sys.stderr)
    print(_format_table(summaries))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--counts", default="0,1,5,10", help="comma-separated plugin counts (default: 0,1,5,10)")
    parser.add_argument("--repeat", type=int, default=1, help="repeat every count this many times")
    parser.add_argument("--idle", type=float, default=10.0, help="idle measurement window in seconds")
    parser.add_argument("--settle", type=float, default=3.0, help="seconds to wait after startup before sampling")
    parser.add_argument("--calls", type=int, default=20, help="ping calls per plugin")
    parser.add_argument("--startup-timeout", type=float, default=30.0)
    parser.add_argument("--run-timeout", type=float, default=600.0, help="per-count interpreter timeout")
    parser.add_argument("--output", help="write the full JSON result here")
    parser.add_argument("--keep-sandbox", action="store_true", help="keep the temporary storage root for inspection")
    parser.add_argument(
        "--no-launcher-main",
        dest="launcher_main",
        action="store_false",
        help="do not mirror production's launcher.py re-import in plugin children",
    )
    parser.add_argument(
        "--no-push-on-startup",
        dest="push_on_startup",
        action="store_false",
        help="synthetic plugins skip their startup push_message",
    )
    parser.add_argument(
        "--read-config-on-startup",
        action="store_true",
        help="synthetic plugins read their config once in startup, like most real plugins",
    )
    parser.add_argument("--run-one", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--result", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.repeat < 1 or args.idle <= 0 or args.calls < 1:
        raise SystemExit("--repeat and --calls must be >= 1 and --idle must be positive")
    if args.run_one is None:
        return _orchestrate(args)
    if "NEKO_PLUGIN_BASELINE_PLUGINS_ROOT" not in os.environ or not args.result:
        raise SystemExit("--run-one is internal; run without it")
    result = asyncio.run(_run_one(args))
    Path(args.result).write_text(json.dumps(result), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
