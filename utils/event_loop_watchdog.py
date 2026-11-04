"""Default-on event-loop stall watchdog that logs *which* code holds the loop.

A heartbeat coroutine stamps ``time.monotonic()`` every few hundred ms; a
daemon thread checks the stamp. When the loop misses its heartbeat for longer
than the threshold, the thread samples only the loop thread's stack via
``sys._current_frames()`` and writes it to the service logger. Recovery is
logged with the approximate total blocked time.

Log records hold stack frames (file, line, function) and durations only:
no frame locals, arguments or source text are read, so no user text can leak
into the log. Paths are shortened relative to the project / interpreter roots.

Overhead while healthy: one tiny coroutine wakeup per heartbeat interval on
the loop, and one ``Event.wait`` timeout per check interval on the thread.
"""

import asyncio
import functools
import math
import os
import sys
import threading
import time
import weakref
from pathlib import Path


THRESHOLD_ENV = "NEKO_LOOP_STALL_THRESHOLD_MS"
DEFAULT_THRESHOLD_S = 1.0
MIN_THRESHOLD_S = 0.1
DEFAULT_REPORT_INTERVAL_S = 30.0
MAX_HEARTBEAT_INTERVAL_S = 0.2
MAX_CHECK_INTERVAL_S = 0.1
THREAD_NAME = "neko-loop-stall-watchdog"
HEARTBEAT_TASK_NAME = "neko-loop-stall-heartbeat"

# Innermost frames kept per dump, and innermost frames that identify a stall
# site for rate limiting (the same blocking call reached the same way).
_MAX_STACK_FRAMES = 40
_SIGNATURE_FRAMES = 5
_MAX_TRACKED_SIGNATURES = 64
_LOG_PREFIX = "[loop-stall]"
_DISABLED_VALUES = frozenset({"0", "off", "false", "no", "disable", "disabled"})

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# One watchdog per event loop: in merged mode several services share a loop
# and each asks for a watchdog at startup. Keyed weakly so a closed, dropped
# loop does not keep its entry alive.
_REGISTRY = weakref.WeakKeyDictionary()
_REGISTRY_LOCK = threading.Lock()


def resolve_stall_threshold(raw):
    """Parse the ``NEKO_LOOP_STALL_THRESHOLD_MS`` value.

    Returns the threshold in seconds, or ``None`` when the watchdog is
    disabled (``0``, ``off`` or any non-positive number). Empty or missing
    values give the default. Values below ``MIN_THRESHOLD_S`` are clamped up.
    Raises ``ValueError`` for anything unparsable.
    """
    if raw is None or not str(raw).strip():
        return DEFAULT_THRESHOLD_S
    text = str(raw).strip().lower()
    if text in _DISABLED_VALUES:
        return None
    millis = float(text)
    if not math.isfinite(millis):
        raise ValueError(f"invalid stall threshold: {raw!r}")
    if millis <= 0:
        return None
    return max(millis / 1000.0, MIN_THRESHOLD_S)


@functools.lru_cache(maxsize=1024)
def _short_path(filename):
    """Shorten a code filename so logs do not carry user names or home paths.

    Files under the project or interpreter roots become relative paths, files
    under the home directory become ``~/...``, and anything else keeps only
    its base name.
    """
    if not filename or filename.startswith("<"):
        return filename
    try:
        absolute = os.path.abspath(filename)
        normalized = os.path.normcase(absolute)
    except (TypeError, ValueError):
        return ".../" + os.path.basename(str(filename))
    roots = {str(_PROJECT_ROOT), sys.prefix, sys.base_prefix, sys.exec_prefix}
    for root in sorted((r for r in roots if r), key=len, reverse=True):
        root_norm = os.path.normcase(os.path.abspath(root))
        if normalized.startswith(root_norm + os.sep):
            return absolute[len(root_norm) + 1:].replace("\\", "/")
    home = os.path.expanduser("~")
    if home and home != "~":
        home_norm = os.path.normcase(os.path.abspath(home))
        if normalized.startswith(home_norm + os.sep):
            return "~/" + absolute[len(home_norm) + 1:].replace("\\", "/")
    return ".../" + os.path.basename(absolute)


def _format_frames(frames):
    return "\n".join(f"  {path}:{lineno} {func}" for path, lineno, func in frames)


class EventLoopStallWatchdog:
    """Detect event-loop stalls and log the loop thread's stack.

    ``start()`` must be called from the loop's own thread (the loop thread id
    is captured there). ``stop()`` may be called from any thread. A watchdog
    shared through ``start_loop_stall_watchdog_from_env`` counts its owners;
    each owner calls ``stop()`` once and the last call shuts it down.
    """

    def __init__(
        self,
        loop,
        *,
        logger,
        threshold_s=DEFAULT_THRESHOLD_S,
        report_interval_s=DEFAULT_REPORT_INTERVAL_S,
        label="main",
    ):
        self._loop = loop
        self._logger = logger
        self.threshold_s = max(float(threshold_s), MIN_THRESHOLD_S)
        self.heartbeat_interval_s = min(MAX_HEARTBEAT_INTERVAL_S, self.threshold_s / 4)
        self.check_interval_s = min(MAX_CHECK_INTERVAL_S, self.threshold_s / 4)
        self._report_interval_s = float(report_interval_s)
        self._labels = [label]
        self._owners = 0
        self._stop_event = threading.Event()
        # Set by any owner's stop(): on a shared loop the first service to
        # shut down means the process is shutting down, and its synchronous
        # cleanup should not be reported while the last owner still runs.
        self._reporting_paused = threading.Event()
        self._last_beat = time.monotonic()
        self._loop_thread_id = None
        self._task = None
        self._thread = None
        # signature -> [last_report_monotonic, suppressed_since_last_report]
        self._reports = {}

    @property
    def label(self):
        return "+".join(self._labels)

    @property
    def running(self):
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self):
        """Schedule the heartbeat and start the checker thread; returns at once."""
        if self._thread is not None:
            return
        self._owners = 1
        self._loop_thread_id = threading.get_ident()
        self._last_beat = time.monotonic()
        self._task = self._loop.create_task(self._heartbeat(), name=HEARTBEAT_TASK_NAME)
        self._thread = threading.Thread(target=self._watch, name=THREAD_NAME, daemon=True)
        self._thread.start()

    def _acquire(self, label):
        self._owners += 1
        self._reporting_paused.clear()
        if label not in self._labels:
            self._labels.append(label)

    def stop(self, timeout=1.0):
        """Release one owner; the last release stops the thread and heartbeat.

        Any release pauses reporting at once, so cleanup after the first
        service's shutdown begins is not logged as stalls. Idempotent once
        fully stopped.
        """
        self._reporting_paused.set()
        with _REGISTRY_LOCK:
            if self._owners > 1:
                self._owners -= 1
                return
            self._owners = 0
            if _REGISTRY.get(self._loop) is self:
                del _REGISTRY[self._loop]
        self._stop_event.set()
        task, self._task = self._task, None
        if task is not None and not task.done():
            try:
                if threading.get_ident() == self._loop_thread_id:
                    task.cancel()
                else:
                    self._loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass  # loop already closed; the task dies with it
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def _abandon_closed_loop(self):
        self._stop_event.set()
        self._task = None  # died with the loop
        with _REGISTRY_LOCK:
            self._owners = 0
            if _REGISTRY.get(self._loop) is self:
                del _REGISTRY[self._loop]

    async def _heartbeat(self):
        while not self._stop_event.is_set():
            self._last_beat = time.monotonic()
            await asyncio.sleep(self.heartbeat_interval_s)

    def _watch(self):
        stalled_beat = None  # heartbeat stamp the current stall is measured from
        stall_origin = 0.0
        stall_reported = False
        resumed_at = 0.0
        last_tick = time.monotonic()
        while not self._stop_event.wait(self.check_interval_s):
            now = time.monotonic()
            overslept = now - last_tick - self.check_interval_s
            last_tick = now
            beat = self._last_beat
            if self._reporting_paused.is_set():
                stalled_beat = None
                continue
            if stalled_beat is not None:
                if beat != stalled_beat:
                    self._finish_stall(beat - stall_origin, stall_reported)
                    stalled_beat = None
                continue
            if overslept > self.threshold_s:
                # This thread was frozen too (system sleep, debugger pause):
                # restart the measurement instead of blaming the loop.
                resumed_at = now
                continue
            if self._loop.is_closed():
                # Closed without stop(): nothing left to watch.
                self._abandon_closed_loop()
                return
            origin = max(beat, resumed_at)
            if now - origin < self.threshold_s or not self._loop.is_running():
                continue
            stalled_beat = beat
            stall_origin = origin
            try:
                stall_reported = self._report_stall(now - origin)
            except Exception:  # noqa: BLE001 - diagnostics must never kill the thread
                stall_reported = False

    def _capture_loop_stack(self):
        frame = sys._current_frames().get(self._loop_thread_id)
        if frame is None:
            return None, False
        frames = []
        try:
            while frame is not None and len(frames) < _MAX_STACK_FRAMES:
                code = frame.f_code
                frames.append((_short_path(code.co_filename), frame.f_lineno, code.co_name))
                frame = frame.f_back
            truncated = frame is not None
        finally:
            del frame
        frames.reverse()
        return frames, truncated

    def _report_stall(self, elapsed_s):
        frames, truncated = self._capture_loop_stack()
        if frames is None:
            return False
        signature = tuple((path, func) for path, _lineno, func in frames[-_SIGNATURE_FRAMES:])
        now = time.monotonic()
        entry = self._reports.get(signature)
        if entry is not None and now - entry[0] < self._report_interval_s:
            entry[1] += 1
            return False
        suppressed = entry[1] if entry is not None else 0
        self._reports[signature] = [now, 0]
        if len(self._reports) > _MAX_TRACKED_SIGNATURES:
            oldest = min(self._reports, key=lambda key: self._reports[key][0])
            self._reports.pop(oldest, None)
        suppressed_note = (
            f"; {suppressed} similar stall(s) suppressed since last report" if suppressed else ""
        )
        truncated_note = "  ... outer frames omitted\n" if truncated else ""
        self._logger.warning(
            "%s %s event loop blocked: no heartbeat for %d ms (threshold %d ms)%s. "
            "Loop thread stack, innermost last:\n%s%s",
            _LOG_PREFIX,
            self.label,
            int(elapsed_s * 1000),
            int(self.threshold_s * 1000),
            suppressed_note,
            truncated_note,
            _format_frames(frames),
        )
        return True

    def _finish_stall(self, gap_s, reported):
        if self._stop_event.is_set() or self._reporting_paused.is_set():
            return
        # The gap between heartbeats includes one regular sleep interval.
        blocked_ms = int(max(0.0, gap_s - self.heartbeat_interval_s) * 1000)
        if reported:
            self._logger.warning(
                "%s %s event loop recovered after ~%d ms blocked",
                _LOG_PREFIX,
                self.label,
                blocked_ms,
            )
        else:
            self._logger.debug(
                "%s %s event loop recovered after ~%d ms blocked (report suppressed)",
                _LOG_PREFIX,
                self.label,
                blocked_ms,
            )


def start_loop_stall_watchdog_from_env(loop, *, logger, label="main", environ=None):
    """Start a watchdog configured by ``NEKO_LOOP_STALL_THRESHOLD_MS``.

    Returns the watchdog, or ``None`` when disabled by the variable. If this
    loop already has a running watchdog, that one is returned with one more
    owner instead of starting a second; the caller still calls ``stop()``
    once. Must be called on the loop's thread.
    """
    raw = (os.environ if environ is None else environ).get(THRESHOLD_ENV)
    try:
        threshold_s = resolve_stall_threshold(raw)
    except ValueError:
        logger.warning(
            "%s ignoring invalid %s=%r; using default %d ms",
            _LOG_PREFIX,
            THRESHOLD_ENV,
            raw,
            int(DEFAULT_THRESHOLD_S * 1000),
        )
        threshold_s = DEFAULT_THRESHOLD_S
    if threshold_s is None:
        logger.info("%s watchdog disabled via %s=%s", _LOG_PREFIX, THRESHOLD_ENV, raw)
        return None
    with _REGISTRY_LOCK:
        existing = _REGISTRY.get(loop)
        if existing is not None and existing._owners > 0:
            existing._acquire(label)
            watchdog = None
        else:
            watchdog = EventLoopStallWatchdog(loop, logger=logger, threshold_s=threshold_s, label=label)
            watchdog.start()
            _REGISTRY[loop] = watchdog
    if watchdog is None:
        logger.info("%s %s shares the watchdog already running on this loop", _LOG_PREFIX, label)
        return existing
    logger.info(
        "%s %s watchdog started (threshold=%d ms, heartbeat=%d ms; set %s=0 to disable)",
        _LOG_PREFIX,
        label,
        int(watchdog.threshold_s * 1000),
        int(watchdog.heartbeat_interval_s * 1000),
        THRESHOLD_ENV,
    )
    return watchdog
