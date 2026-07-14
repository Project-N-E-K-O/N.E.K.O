#!/usr/bin/env python3
"""Collect reproducible runtime-memory checkpoints for N.E.K.O.

The probe deliberately keeps two measurement scopes separate:

* ``stack`` samples real child processes with psutil. It is suitable for the
  launcher, Electron, and their descendants.
* ``scenario`` runs one lazy feature transition in this process so tracemalloc
  can distinguish traced Python allocations from total process USS.

The JSON output never includes command lines, environment variables, API
payloads, or response text. Synthetic chat runs retain only message types and
status codes. ``stack`` subprocess logs can contain application data, so treat
them as sensitive diagnostics and remove them after extracting aggregate data.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import socket
import statistics
import subprocess
import time
import tracemalloc
import urllib.parse
import urllib.request
import uuid
from collections import Counter
from contextlib import suppress
from pathlib import Path
from typing import Any, Callable, Iterable

import psutil


MIB = 1024 * 1024
DEFAULT_SAMPLE_INTERVAL = 0.25


def _mib(value: int | float | None) -> float | None:
    if value is None:
        return None
    return round(float(value) / MIB, 3)


def _process_category(process: psutil.Process) -> str:
    try:
        name = process.name().lower()
    except (psutil.Error, OSError):
        return "unknown"

    try:
        command = [str(item).lower() for item in process.cmdline()]
    except (psutil.Error, OSError):
        command = []

    if "electron" in name or name == "n.e.k.o.exe":
        if any(item.startswith("--type=") for item in command):
            return "electron_chromium_child"
        return "electron_main"
    if "chrome" in name or "chromium" in name:
        return "chromium"
    if "python" in name:
        return "python"
    if name in {"uv.exe", "uv"}:
        return "uv_wrapper"
    if "node" in name:
        return "node"
    return "other"


def _process_row(process: psutil.Process) -> dict[str, Any] | None:
    try:
        basic = process.memory_info()
        try:
            full = process.memory_full_info()
            uss = getattr(full, "uss", None)
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            uss = None
        return {
            "pid": process.pid,
            "ppid": process.ppid(),
            "name": process.name(),
            "category": _process_category(process),
            "rss_mib": _mib(basic.rss),
            "uss_mib": _mib(uss),
        }
    except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
        return None


def _tree_processes(root_pids: Iterable[int]) -> list[psutil.Process]:
    found: dict[int, psutil.Process] = {}
    for pid in root_pids:
        try:
            root = psutil.Process(pid)
            found[root.pid] = root
            for child in root.children(recursive=True):
                found[child.pid] = child
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            continue
    return list(found.values())


def _sample(
    root_pids: Iterable[int],
    *,
    observed_processes: dict[tuple[int, float], psutil.Process] | None = None,
) -> dict[str, Any]:
    processes = _tree_processes(root_pids)
    if observed_processes is not None:
        for process in processes:
            try:
                observed_processes[(process.pid, process.create_time())] = process
            except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                continue
    rows = [row for process in processes if (row := _process_row(process))]
    categories: dict[str, dict[str, float | int | None]] = {}
    for row in rows:
        entry = categories.setdefault(
            row["category"],
            {"count": 0, "rss_mib": 0.0, "uss_mib": 0.0, "uss_processes": 0},
        )
        entry["count"] = int(entry["count"]) + 1
        entry["rss_mib"] = float(entry["rss_mib"]) + float(row["rss_mib"] or 0.0)
        if row["uss_mib"] is not None:
            entry["uss_mib"] = float(entry["uss_mib"]) + float(row["uss_mib"])
            entry["uss_processes"] = int(entry["uss_processes"]) + 1
    for entry in categories.values():
        if entry["uss_processes"] == 0:
            entry["uss_mib"] = None

    total_rss = sum(float(row["rss_mib"] or 0.0) for row in rows)
    total_uss_values = [float(row["uss_mib"]) for row in rows if row["uss_mib"] is not None]
    return {
        "elapsed_s": round(time.perf_counter(), 6),
        "categories": categories,
        "total": {
            "count": len(rows),
            "rss_mib": round(total_rss, 3),
            "uss_mib": round(sum(total_uss_values), 3) if total_uss_values else None,
            "uss_processes": len(total_uss_values),
        },
        "processes": rows,
    }


def _series_summary(samples: list[dict[str, Any]]) -> dict[str, Any]:
    category_names = sorted(
        {name for sample in samples for name in sample.get("categories", {})}
    )
    categories: dict[str, dict[str, float | int | None]] = {}
    for name in category_names:
        rss_values = [
            float(sample["categories"].get(name, {}).get("rss_mib", 0.0))
            for sample in samples
        ]
        uss_values: list[float] = []
        for sample in samples:
            category = sample["categories"].get(name)
            if category is None:
                uss_values.append(0.0)
                continue
            uss_mib = category.get("uss_mib")
            if uss_mib is not None:
                uss_values.append(float(uss_mib))
        count_values = [
            int(sample["categories"].get(name, {}).get("count", 0))
            for sample in samples
        ]
        categories[name] = {
            "median_count": int(statistics.median(count_values)),
            "max_count": max(count_values),
            "median_rss_mib": round(statistics.median(rss_values), 3),
            "peak_rss_mib": round(max(rss_values), 3),
            "median_uss_mib": round(statistics.median(uss_values), 3)
            if uss_values
            else None,
            "peak_uss_mib": round(max(uss_values), 3) if uss_values else None,
        }

    total_rss = [float(sample["total"]["rss_mib"] or 0.0) for sample in samples]
    total_uss = [
        float(sample["total"]["uss_mib"] or 0.0)
        for sample in samples
        if sample["total"]["uss_mib"] is not None
    ]
    return {
        "sample_count": len(samples),
        "categories": categories,
        "total": {
            "median_rss_mib": round(statistics.median(total_rss), 3) if total_rss else 0.0,
            "peak_rss_mib": round(max(total_rss), 3) if total_rss else 0.0,
            "median_uss_mib": round(statistics.median(total_uss), 3) if total_uss else None,
            "peak_uss_mib": round(max(total_uss), 3) if total_uss else None,
        },
        "last_processes": samples[-1]["processes"] if samples else [],
    }


def _sample_window(
    label: str,
    root_pids: Iterable[int],
    *,
    seconds: float,
    interval: float,
    traced_python_pid: int | None = None,
    observed_processes: dict[tuple[int, float], psutil.Process] | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + max(seconds, 0.0)
    samples: list[dict[str, Any]] = []
    while True:
        samples.append(_sample(root_pids, observed_processes=observed_processes))
        if time.monotonic() >= deadline:
            break
        time.sleep(interval)

    result = {"label": label, **_series_summary(samples)}
    if traced_python_pid is not None and tracemalloc.is_tracing():
        current, peak = tracemalloc.get_traced_memory()
        traced_uss = None
        for row in result["last_processes"]:
            if row["pid"] == traced_python_pid:
                traced_uss = row["uss_mib"]
                break
        current_mib = _mib(current)
        result["tracemalloc"] = {
            "pid": traced_python_pid,
            "current_mib": current_mib,
            "peak_mib": _mib(peak),
            "uss_minus_traced_current_mib": (
                round(float(traced_uss) - float(current_mib), 3)
                if traced_uss is not None and current_mib is not None
                else None
            ),
        }
    return result


def _metadata() -> dict[str, Any]:
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.SubprocessError):
        commit = ""
    return {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "git_commit": commit,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "logical_cpu_count": psutil.cpu_count(),
        "ram_gib": round(psutil.virtual_memory().total / (1024**3), 3),
        "sample_interval_s": DEFAULT_SAMPLE_INTERVAL,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(path.resolve())


async def _embedding_scenario(args: argparse.Namespace) -> dict[str, Any]:
    checkpoints = [
        _sample_window(
            "python_baseline",
            [os.getpid()],
            seconds=args.window,
            interval=args.interval,
            traced_python_pid=os.getpid(),
        )
    ]

    from config import (
        VECTORS_EMBEDDING_DIM,
        VECTORS_MIN_RAM_GB,
        VECTORS_MODEL_PROFILE_ID,
        VECTORS_QUANTIZATION,
    )
    from memory.embeddings import EmbeddingService

    checkpoints.append(
        _sample_window(
            "embedding_imported",
            [os.getpid()],
            seconds=args.window,
            interval=args.interval,
            traced_python_pid=os.getpid(),
        )
    )
    service = EmbeddingService(
        model_dir=str(Path(args.embedding_root).resolve()),
        enabled=True,
        embedding_dim_setting=VECTORS_EMBEDDING_DIM,
        quantization_setting=VECTORS_QUANTIZATION,
        min_ram_gb=VECTORS_MIN_RAM_GB,
        profile_id=VECTORS_MODEL_PROFILE_ID,
    )
    ready = await service.request_load()
    if not ready:
        raise RuntimeError(f"embedding service did not become READY: {service.disable_reason()}")
    checkpoints.append(
        _sample_window(
            "embedding_ready",
            [os.getpid()],
            seconds=args.window,
            interval=args.interval,
            traced_python_pid=os.getpid(),
        )
    )
    vector = await service.embed("N.E.K.O runtime memory baseline")
    if vector is None:
        raise RuntimeError(
            f"embedding inference failed after READY: {service.disable_reason()}"
        )
    checkpoints.append(
        _sample_window(
            "embedding_first_inference",
            [os.getpid()],
            seconds=args.window,
            interval=args.interval,
            traced_python_pid=os.getpid(),
        )
    )
    return {
        "scenario": "embedding",
        "embedding": {
            "ready": ready,
            "model_id": service.model_id(),
            "model_root": str(Path(args.embedding_root).resolve()),
            "vector_dimensions": len(vector),
        },
        "checkpoints": checkpoints,
    }


async def _ocr_scenario(args: argparse.Namespace) -> dict[str, Any]:
    checkpoints = [
        _sample_window(
            "python_baseline",
            [os.getpid()],
            seconds=args.window,
            interval=args.interval,
            traced_python_pid=os.getpid(),
        )
    ]
    from PIL import Image
    from plugin.plugins._shared.rapidocr.rapidocr_support import (
        DEFAULT_RAPIDOCR_ENGINE_TYPE,
        DEFAULT_RAPIDOCR_LANG_TYPE,
        DEFAULT_RAPIDOCR_MODEL_TYPE,
        DEFAULT_RAPIDOCR_OCR_VERSION,
    )
    from plugin.plugins.galgame_plugin.ocr_rapidocr_backend import RapidOcrBackend

    backend = RapidOcrBackend(
        install_target_dir_raw="",
        engine_type=DEFAULT_RAPIDOCR_ENGINE_TYPE,
        lang_type=DEFAULT_RAPIDOCR_LANG_TYPE,
        model_type=DEFAULT_RAPIDOCR_MODEL_TYPE,
        ocr_version=DEFAULT_RAPIDOCR_OCR_VERSION,
    )
    available = backend.is_available()
    checkpoints.append(
        _sample_window(
            "ocr_imported",
            [os.getpid()],
            seconds=args.window,
            interval=args.interval,
            traced_python_pid=os.getpid(),
        )
    )
    if not available:
        raise RuntimeError("RapidOCR backend is not available")
    text = backend.extract_text(Image.new("RGB", (640, 360), "white"))
    checkpoints.append(
        _sample_window(
            "ocr_ready_after_first_inference",
            [os.getpid()],
            seconds=args.window,
            interval=args.interval,
            traced_python_pid=os.getpid(),
        )
    )
    return {
        "scenario": "ocr",
        "ocr": {"available": available, "synthetic_text_length": len(text)},
        "checkpoints": checkpoints,
    }


async def _browser_scenario(args: argparse.Namespace) -> dict[str, Any]:
    checkpoints = [
        _sample_window(
            "python_baseline",
            [os.getpid()],
            seconds=args.window,
            interval=args.interval,
            traced_python_pid=os.getpid(),
        )
    ]
    from brain.browser_use_adapter import BrowserUseAdapter

    adapter = BrowserUseAdapter(headless=not args.headed)
    checkpoints.append(
        _sample_window(
            "browser_use_imported",
            [os.getpid()],
            seconds=args.window,
            interval=args.interval,
            traced_python_pid=os.getpid(),
        )
    )
    session = await adapter._get_browser_session()
    try:
        await session.start()
        adapter._session_ever_started = True
        checkpoints.append(
            _sample_window(
                "browser_use_playwright_started",
                [os.getpid()],
                seconds=args.window,
                interval=args.interval,
                traced_python_pid=os.getpid(),
            )
        )
    finally:
        await adapter.close()
    return {
        "scenario": "browser_use",
        "browser": {"headless": not args.headed},
        "checkpoints": checkpoints,
    }


def _ports_ready(ports: list[int]) -> bool:
    for port in ports:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                pass
        except OSError:
            return False
    return True


def _assert_ports_available(ports: list[int]) -> None:
    busy: list[int] = []
    for port in ports:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind(("127.0.0.1", port))
        except OSError:
            busy.append(port)
        finally:
            probe.close()
    if busy:
        raise RuntimeError(
            "benchmark ports must be free before startup; refusing launcher "
            f"fallback because it would invalidate readiness attribution: {busy}"
        )


def _terminate_process_trees(
    processes: Iterable[subprocess.Popen[Any]],
    observed_processes: Iterable[psutil.Process],
    timeout: float = 12.0,
) -> None:
    targets: dict[tuple[int, float], psutil.Process] = {}
    for target in observed_processes:
        try:
            targets[(target.pid, target.create_time())] = target
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            continue
    for process in processes:
        try:
            root = psutil.Process(process.pid)
            current = [root, *root.children(recursive=True)]
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            continue
        for target in current:
            try:
                targets[(target.pid, target.create_time())] = target
            except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                continue
    target_list = list(targets.values())
    # Teardown is best-effort because processes can exit between discovery and action.
    for target in reversed(target_list):
        with suppress(psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            target.terminate()
    _, alive = psutil.wait_procs(target_list, timeout=timeout)
    for target in alive:
        with suppress(psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            target.kill()


async def _run_synthetic_chat(
    *,
    port: int,
    character: str,
    prompt: str,
    timeout: float,
    after_turn: Callable[[], None] | None = None,
) -> dict[str, Any]:
    import websockets

    if not character:
        def _read_current_character() -> dict[str, Any]:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/characters/current_catgirl", timeout=5
            ) as response:
                return json.loads(response.read().decode("utf-8"))

        current = await asyncio.to_thread(_read_current_character)
        character = str(current.get("current_catgirl") or "").strip()
    if not character:
        raise RuntimeError("no character available for synthetic chat")

    request_id = f"memory-baseline-{uuid.uuid4().hex}"
    uri = f"ws://127.0.0.1:{port}/ws/{urllib.parse.quote(character, safe='')}"
    counts: Counter[str] = Counter()
    status_codes: Counter[str] = Counter()
    started_at = time.perf_counter()
    async with websockets.connect(uri, max_size=32 * MIB) as websocket:
        await websocket.send(
            json.dumps(
                {
                    "action": "start_session",
                    "input_type": "text",
                    "new_session": True,
                    "language": "en",
                }
            )
        )
        while True:
            raw = await asyncio.wait_for(websocket.recv(), timeout=timeout)
            message = json.loads(raw)
            message_type = str(message.get("type") or "unknown")
            counts[message_type] += 1
            if message_type == "status":
                try:
                    status = json.loads(message.get("message") or "{}")
                except (TypeError, json.JSONDecodeError):
                    status = {}
                code = str(status.get("code") or "unknown")
                status_codes[code] += 1
            if message_type == "session_failed":
                raise RuntimeError("synthetic chat session failed")
            if message_type == "session_started":
                break

        await websocket.send(
            json.dumps(
                {
                    "action": "stream_data",
                    "input_type": "text",
                    "data": prompt,
                    "request_id": request_id,
                    "source": "runtime_memory_baseline",
                }
            )
        )
        while True:
            raw = await asyncio.wait_for(websocket.recv(), timeout=timeout)
            message = json.loads(raw)
            message_type = str(message.get("type") or "unknown")
            counts[message_type] += 1
            if message_type == "status":
                try:
                    status = json.loads(message.get("message") or "{}")
                except (TypeError, json.JSONDecodeError):
                    status = {}
                status_codes[str(status.get("code") or "unknown")] += 1
            if (
                message_type == "system"
                and str(message.get("data") or "").startswith("turn end")
                and message.get("request_id") == request_id
            ):
                break

        turn_elapsed_s = round(time.perf_counter() - started_at, 3)
        try:
            if after_turn is not None:
                await asyncio.to_thread(after_turn)
        finally:
            await websocket.send(json.dumps({"action": "end_session"}))

    return {
        "elapsed_s": turn_elapsed_s,
        "message_type_counts": dict(sorted(counts.items())),
        "status_code_counts": dict(sorted(status_codes.items())),
        "request_id_prefix": "memory-baseline-",
        "prompt_recorded": False,
    }


def _decode_command(raw: str) -> list[str]:
    try:
        command = json.loads(raw)
    except json.JSONDecodeError:
        command = [item.strip() for item in raw.split("|")]
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(item, str) and item for item in command)
    ):
        raise ValueError("command must be a JSON array or a pipe-delimited string")
    return command


def _decode_ports(raw: str) -> list[int]:
    items = [item.strip() for item in raw.split(",")]
    if not items or any(not item for item in items):
        raise ValueError("--ports must be a comma-separated list of port numbers")
    try:
        ports = [int(item) for item in items]
    except ValueError as exc:
        raise ValueError(
            "--ports must be a comma-separated list of port numbers"
        ) from exc
    if any(port < 1 or port > 65535 for port in ports):
        raise ValueError("--ports values must be between 1 and 65535")
    if len(set(ports)) != len(ports):
        raise ValueError("--ports values must be unique")
    return ports


def _parse_env(items: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in items:
        key, separator, value = item.partition("=")
        if not separator or not key:
            raise ValueError(f"invalid environment override: {item!r}")
        result[key] = value
    return result


def _spawn(
    command: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    log_path: Path,
) -> tuple[subprocess.Popen[Any], Any]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("w", encoding="utf-8", errors="replace")
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0),
    )
    return process, log_file


def _stack(args: argparse.Namespace) -> dict[str, Any]:
    output = Path(args.output).resolve()
    env = os.environ.copy()
    env.update(_parse_env(args.env))
    backend_command = _decode_command(args.backend_command)
    electron_command = _decode_command(args.electron_command) if args.electron_command else None
    ports = _decode_ports(args.ports)
    _assert_ports_available(ports)
    processes: list[tuple[str, subprocess.Popen[Any], Any]] = []
    checkpoints: list[dict[str, Any]] = []
    startup_samples: list[dict[str, Any]] = []
    roots: list[int] = []
    observed_processes: dict[tuple[int, float], psutil.Process] = {}
    started_at = time.perf_counter()
    ports_ready_elapsed_s: float | None = None

    try:
        backend, backend_log = _spawn(
            backend_command,
            cwd=args.backend_cwd,
            env=env,
            log_path=output.with_suffix(".backend.log"),
        )
        processes.append(("backend", backend, backend_log))
        roots.append(backend.pid)
        deadline = time.monotonic() + args.timeout
        while not _ports_ready(ports):
            startup_samples.append(
                _sample(roots, observed_processes=observed_processes)
            )
            if backend.poll() is not None:
                raise RuntimeError(f"backend exited before ready with code {backend.returncode}")
            if time.monotonic() >= deadline:
                raise TimeoutError(f"ports did not become ready: {ports}")
            time.sleep(args.interval)
        ports_ready_elapsed_s = time.perf_counter() - started_at
        startup_samples.append(_sample(roots, observed_processes=observed_processes))
        time.sleep(args.settle)
        checkpoints.append(
            _sample_window(
                "cold_start_ready",
                roots,
                seconds=args.window,
                interval=args.interval,
                observed_processes=observed_processes,
            )
        )

        if electron_command is not None:
            electron, electron_log = _spawn(
                electron_command,
                cwd=args.electron_cwd,
                env=env,
                log_path=output.with_suffix(".electron.log"),
            )
            processes.append(("electron", electron, electron_log))
            roots.append(electron.pid)
            time.sleep(args.electron_settle)
            if electron.poll() is not None:
                raise RuntimeError(f"Electron exited early with code {electron.returncode}")
            checkpoints.append(
                _sample_window(
                    "electron_attached",
                    roots,
                    seconds=args.window,
                    interval=args.interval,
                    observed_processes=observed_processes,
                )
            )

        chat_result = None
        if args.synthetic_chat:
            def _record_live_first_chat() -> None:
                time.sleep(args.settle)
                checkpoints.append(
                    _sample_window(
                        "first_chat_complete_live",
                        roots,
                        seconds=args.window,
                        interval=args.interval,
                        observed_processes=observed_processes,
                    )
                )

            chat_result = asyncio.run(
                _run_synthetic_chat(
                    port=ports[0],
                    character=args.chat_character,
                    prompt=args.chat_prompt,
                    timeout=args.chat_timeout,
                    after_turn=_record_live_first_chat,
                )
            )

        return {
            "scenario": "stack",
            "ports_ready_elapsed_s": round(ports_ready_elapsed_s, 3),
            "measurement_elapsed_s": round(time.perf_counter() - started_at, 3),
            "ports": ports,
            "startup_window": _series_summary(startup_samples),
            "checkpoints": checkpoints,
            "synthetic_chat": chat_result,
            "logs": {
                "backend": str(output.with_suffix(".backend.log")),
                "electron": str(output.with_suffix(".electron.log")) if electron_command else None,
            },
        }
    finally:
        _terminate_process_trees(
            (process for _name, process, _log_file in processes),
            observed_processes.values(),
        )
        for _name, _process, log_file in processes:
            log_file.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="JSON output path")
    parser.add_argument("--interval", type=float, default=DEFAULT_SAMPLE_INTERVAL)
    parser.add_argument("--window", type=float, default=3.0)
    subparsers = parser.add_subparsers(dest="command", required=True)

    scenario = subparsers.add_parser("scenario", help="Run an in-process lazy feature scenario")
    scenario.add_argument("name", choices=("embedding", "ocr", "browser-use"))
    scenario.add_argument("--embedding-root", default="data/embedding_models")
    scenario.add_argument("--headed", action="store_true")

    stack = subparsers.add_parser("stack", help="Measure a launcher/Electron process tree")
    stack.add_argument(
        "--backend-command",
        required=True,
        help="JSON array or a pipe-delimited command",
    )
    stack.add_argument("--backend-cwd", default=str(Path.cwd()))
    stack.add_argument("--electron-command", help="JSON array or a pipe-delimited command")
    stack.add_argument("--electron-cwd", default=str(Path.cwd()))
    stack.add_argument("--ports", default="48911,48912,48915")
    stack.add_argument("--env", action="append", default=[], metavar="NAME=VALUE")
    stack.add_argument("--timeout", type=float, default=150.0)
    stack.add_argument("--settle", type=float, default=5.0)
    stack.add_argument("--electron-settle", type=float, default=12.0)
    stack.add_argument("--synthetic-chat", action="store_true")
    stack.add_argument("--chat-character", default="")
    stack.add_argument(
        "--chat-prompt",
        default="Reply with exactly OK. This is a runtime memory benchmark.",
    )
    stack.add_argument("--chat-timeout", type=float, default=90.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.interval <= 0 or args.window < 0:
        raise SystemExit("--interval must be positive and --window cannot be negative")

    if args.command == "scenario":
        tracemalloc.start(10)
        if args.name == "embedding":
            result = asyncio.run(_embedding_scenario(args))
        elif args.name == "ocr":
            result = asyncio.run(_ocr_scenario(args))
        else:
            result = asyncio.run(_browser_scenario(args))
    else:
        result = _stack(args)

    payload = {"metadata": _metadata(), **result}
    _write_json(Path(args.output), payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
