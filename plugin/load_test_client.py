import argparse
import asyncio
import multiprocessing as mp
import os
import statistics
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import httpx


def _now() -> float:
    """
    Get a high-resolution monotonic wall-clock timestamp.
    
    Returns:
        float: Monotonic time in seconds with high resolution suitable for measuring intervals.
    """
    return time.perf_counter()


@dataclass
class WorkerResult:
    process_index: int
    iterations: int
    errors: int
    elapsed_seconds: float
    lat_ms_p50: float
    lat_ms_p95: float
    lat_ms_p99: float


def _percentile(values: List[float], p: float) -> float:
    """
    Compute the p-th percentile of a list of numeric values.
    
    If `values` is empty, returns 0.0. If `p <= 0`, returns the minimum value; if `p >= 100`, returns the maximum value. For 0 < p < 100, returns the percentile using linear interpolation between adjacent sorted values.
    
    Parameters:
        values (List[float]): Sample values to compute the percentile from.
        p (float): Desired percentile in the range [0, 100].
    
    Returns:
        float: The p-th percentile value.
    """
    if not values:
        return 0.0
    if p <= 0:
        return min(values)
    if p >= 100:
        return max(values)
    values_sorted = sorted(values)
    k = (len(values_sorted) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(values_sorted) - 1)
    if f == c:
        return values_sorted[f]
    d0 = values_sorted[f] * (c - k)
    d1 = values_sorted[c] * (k - f)
    return d0 + d1


async def _worker_loop(
    *,
    url: str,
    duration_seconds: float,
    concurrency: int,
    plugin_id: str,
    entry_id: str,
    args_payload: Dict[str, Any],
    timeout: float,
) -> Tuple[int, int, float, List[float]]:
    """
    Run an asyncio worker loop that issues concurrent POST requests to a plugin trigger endpoint for a fixed duration.
    
    Parameters:
        url (str): Full endpoint URL to POST to.
        duration_seconds (float): Total time to run the loop in seconds.
        concurrency (int): Maximum number of concurrent in-flight requests.
        plugin_id (str): Plugin identifier included in each request JSON payload.
        entry_id (str): Entry identifier included in each request JSON payload.
        args_payload (Dict[str, Any]): Arguments included under the "args" key in each request JSON payload.
        timeout (float): Per-request timeout in seconds for the HTTP client.
    
    Returns:
        Tuple[int, int, float, List[float]]: A tuple containing:
            - iterations: number of successful responses recorded.
            - errors: number of requests that failed (non-200, missing/false "success", or exceptions).
            - elapsed_seconds: the requested duration_seconds (float).
            - latencies_ms: list of measured request latencies in milliseconds for successful requests.
    """
    deadline = _now() + float(duration_seconds)
    iterations = 0
    errors = 0
    latencies_ms: List[float] = []

    limits = httpx.Limits(max_keepalive_connections=max(20, concurrency * 2), max_connections=max(50, concurrency * 4))
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout), limits=limits) as client:
        sem = asyncio.Semaphore(max(1, int(concurrency)))

        async def _one() -> None:
            """
            Perform a single HTTP POST attempt to the plugin trigger endpoint and record its outcome.
            
            Sends a JSON POST to the configured URL using the enclosing AsyncClient; on a 200 response with a JSON body containing `"success": True` it increments `iterations` and appends the request latency (milliseconds) to `latencies_ms`. On non-200 responses, missing/false success flag, or any exception it increments `errors`. Does nothing if the per-process deadline has been reached.
            
            """
            nonlocal iterations, errors
            async with sem:
                if _now() >= deadline:
                    return
                t0 = _now()
                try:
                    resp = await client.post(
                        url,
                        json={
                            "plugin_id": plugin_id,
                            "entry_id": entry_id,
                            "args": args_payload,
                        },
                    )
                    if resp.status_code != 200:
                        errors += 1
                        return
                    data = resp.json()
                    if not isinstance(data, dict) or not data.get("success"):
                        errors += 1
                        return
                    iterations += 1
                    latencies_ms.append((_now() - t0) * 1000.0)
                except Exception:
                    errors += 1

        tasks: set[asyncio.Task] = set()
        while _now() < deadline:
            while len(tasks) < max(1, int(concurrency)) and _now() < deadline:
                t = asyncio.create_task(_one())
                tasks.add(t)
                t.add_done_callback(tasks.discard)
            if tasks:
                await asyncio.sleep(0)
            else:
                await asyncio.sleep(0.001)

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    elapsed = float(duration_seconds)
    return iterations, errors, elapsed, latencies_ms


def _run_process(
    process_index: int,
    result_queue: "mp.Queue[dict]",
    *,
    server: str,
    port: int,
    duration_seconds: float,
    concurrency: int,
    plugin_id: str,
    entry_id: str,
    timeout: float,
    max_count: Optional[int],
    source: Optional[str],
    inplace: Optional[bool],
) -> None:
    """
    Run a single worker process that performs the configured load test and enqueues its results.
    
    This function constructs the trigger URL and payload for the given plugin/entry, runs the asyncio worker loop for the specified duration and concurrency, computes p50/p95/p99 latencies, and puts a result dict into `result_queue`. If an exception occurs, a result dict containing an error message and zeroed metrics is enqueued instead.
    
    Parameters:
        process_index (int): Index of this worker process (used in the queued result).
        result_queue (mp.Queue[dict]): Multiprocessing queue where the result dictionary will be placed.
        server (str): Hostname or IP of the target server.
        port (int): Port of the target server.
        duration_seconds (float): Duration, in seconds, to run the worker loop.
        concurrency (int): Maximum concurrent requests the worker should issue.
        plugin_id (str): Plugin identifier sent in the trigger payload.
        entry_id (str): Entry identifier sent in the trigger payload; affects which optional args are included.
        timeout (float): Per-request timeout in seconds.
        max_count (Optional[int]): Optional numeric argument included for entries that accept `max_count`.
        source (Optional[str]): Optional `source` string included for entries that accept it.
        inplace (Optional[bool]): Optional `inplace` flag included for entries that accept it.
    """
    try:
        url = f"http://{server}:{int(port)}/plugin/trigger"

        args_payload: Dict[str, Any] = {}
        if entry_id == "op_bus_messages_get":
            if max_count is not None:
                args_payload["max_count"] = int(max_count)
        if entry_id == "op_buslist_reload":
            if max_count is not None:
                args_payload["max_count"] = int(max_count)
            if source:
                args_payload["source"] = str(source)
            if inplace is not None:
                args_payload["inplace"] = bool(inplace)

        iterations, errors, elapsed, lat_ms = asyncio.run(
            _worker_loop(
                url=url,
                duration_seconds=float(duration_seconds),
                concurrency=int(concurrency),
                plugin_id=str(plugin_id),
                entry_id=str(entry_id),
                args_payload=args_payload,
                timeout=float(timeout),
            )
        )

        p50 = _percentile(lat_ms, 50)
        p95 = _percentile(lat_ms, 95)
        p99 = _percentile(lat_ms, 99)
        result_queue.put(
            {
                "process_index": process_index,
                "iterations": int(iterations),
                "errors": int(errors),
                "elapsed_seconds": float(elapsed),
                "lat_ms_p50": float(p50),
                "lat_ms_p95": float(p95),
                "lat_ms_p99": float(p99),
            }
        )
    except Exception as e:
        result_queue.put(
            {
                "process_index": process_index,
                "iterations": 0,
                "errors": 1,
                "elapsed_seconds": float(duration_seconds),
                "lat_ms_p50": 0.0,
                "lat_ms_p95": 0.0,
                "lat_ms_p99": 0.0,
                "error": str(e),
            }
        )


def main() -> int:
    """
    Run the load-testing client: parse command-line options, spawn worker processes that execute the load test, collect per-process results, aggregate metrics (iterations, errors, QPS, and mean latency percentiles), and print a summary.
    
    Returns:
        int: Exit code `0` on success.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default=os.getenv("NEKO_PLUGIN_SERVER_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("NEKO_PLUGIN_SERVER_PORT", "48916")))

    parser.add_argument("--plugin", default="load_tester")
    parser.add_argument(
        "--entry",
        choices=["op_bus_messages_get", "op_buslist_reload"],
        default="op_bus_messages_get",
    )

    parser.add_argument("--processes", type=int, default=4)
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--duration", type=float, default=10.0)
    parser.add_argument("--timeout", type=float, default=1.0)

    parser.add_argument("--max-count", type=int, default=None)
    parser.add_argument("--source", type=str, default=None)
    parser.add_argument("--inplace", action="store_true")
    parser.add_argument("--no-inplace", action="store_true")

    args = parser.parse_args()

    processes = max(1, int(args.processes))
    concurrency = max(1, int(args.concurrency))

    inplace: Optional[bool]
    if bool(args.inplace) and bool(args.no_inplace):
        inplace = None
    elif bool(args.inplace):
        inplace = True
    elif bool(args.no_inplace):
        inplace = False
    else:
        inplace = None

    q: mp.Queue = mp.Queue()
    procs: List[mp.Process] = []
    for i in range(processes):
        p = mp.Process(
            target=_run_process,
            args=(i, q),
            kwargs={
                "server": args.server,
                "port": args.port,
                "duration_seconds": args.duration,
                "concurrency": concurrency,
                "plugin_id": args.plugin,
                "entry_id": args.entry,
                "timeout": args.timeout,
                "max_count": args.max_count,
                "source": args.source,
                "inplace": inplace,
            },
            daemon=True,
        )
        procs.append(p)
        p.start()

    results: List[WorkerResult] = []
    for _ in range(processes):
        item = q.get()
        results.append(
            WorkerResult(
                process_index=int(item.get("process_index", -1)),
                iterations=int(item.get("iterations", 0)),
                errors=int(item.get("errors", 0)),
                elapsed_seconds=float(item.get("elapsed_seconds", args.duration)),
                lat_ms_p50=float(item.get("lat_ms_p50", 0.0)),
                lat_ms_p95=float(item.get("lat_ms_p95", 0.0)),
                lat_ms_p99=float(item.get("lat_ms_p99", 0.0)),
            )
        )

    for p in procs:
        try:
            p.join(timeout=1.0)
        except Exception:
            pass

    total_iter = sum(r.iterations for r in results)
    total_err = sum(r.errors for r in results)
    elapsed = max(0.000001, float(args.duration))
    qps = float(total_iter) / elapsed

    p50s = [r.lat_ms_p50 for r in results if r.lat_ms_p50 > 0]
    p95s = [r.lat_ms_p95 for r in results if r.lat_ms_p95 > 0]
    p99s = [r.lat_ms_p99 for r in results if r.lat_ms_p99 > 0]

    agg_p50 = statistics.mean(p50s) if p50s else 0.0
    agg_p95 = statistics.mean(p95s) if p95s else 0.0
    agg_p99 = statistics.mean(p99s) if p99s else 0.0

    print(
        {
            "server": f"http://{args.server}:{args.port}",
            "plugin": args.plugin,
            "entry": args.entry,
            "processes": processes,
            "concurrency_per_process": concurrency,
            "duration_seconds": float(args.duration),
            "timeout_seconds": float(args.timeout),
            "iterations": total_iter,
            "errors": total_err,
            "qps": qps,
            "lat_ms_p50_mean": agg_p50,
            "lat_ms_p95_mean": agg_p95,
            "lat_ms_p99_mean": agg_p99,
        }
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())