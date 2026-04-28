"""System-wide signal collector for the user-activity tracker.

A single process-wide singleton ``SystemSignalCollector`` polls
Windows for:

  * Keyboard/mouse idle seconds (``GetLastInputInfo``) — the only
    reliable system-wide "user is here" signal that doesn't require
    raw input hooks.
  * CPU utilisation (``psutil.cpu_percent``) as both a 30s rolling
    average and the most recent point.
  * Active foreground window — title via ``pygetwindow`` and the
    owning process name via the Win32 thread-process API.

Per-character hooks (user messages, AI replies, voice RMS) live in
``UserActivityTracker`` instead — those are session-scoped and have
no business in a process singleton.

Why a singleton: the OS only has one foreground window and one input
queue, so polling separately per character would just duplicate work.
The collector starts a single asyncio background task on first ``.start()``
and is driven by ``CONFIG.activity.poll_interval_seconds`` (default 5s).
Cheaper than a real polling loop because each tick is ~1 syscall.

Non-Windows fallback: every getter degrades to a no-op; the collector
returns ``SystemSnapshot()`` defaults so the rest of the tracker keeps
working without OS-specific signals.
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import sys
import time
from collections import deque
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_IS_WINDOWS = platform.system() == 'Windows'


def _force_degraded_from_env() -> bool:
    """Honour ``NEKO_ACTIVITY_TRACKER_REMOTE`` / ``ACTIVITY_TRACKER_REMOTE``.

    Set to ``1`` / ``true`` / ``yes`` when the backend is on a different
    machine from the user — covers the Windows-remote edge case where
    the local OS APIs would happily report the server's foreground
    window (since pygetwindow technically works), but those signals
    are about the server, not the user.

    Default off — most users run backend on their own PC where local
    OS signals are correct.
    """
    for key in ('NEKO_ACTIVITY_TRACKER_REMOTE', 'ACTIVITY_TRACKER_REMOTE'):
        raw = os.getenv(key, '').strip().lower()
        if raw in ('1', 'true', 'yes', 'on'):
            return True
    return False


# ── Public snapshot dataclass ───────────────────────────────────────

@dataclass(frozen=True, slots=True)
class SystemSnapshot:
    """One sampled system state.

    Always returned by value. ``window_title`` is the raw active-window
    title — sanitisation/truncation belongs to the consumer (the
    tracker keeps full text for classification, only logs/prompts use
    truncated versions).
    """
    timestamp: float
    idle_seconds: float                # OS-wide keyboard/mouse idle
    cpu_avg_30s: float                 # rolling avg of last six 5s samples
    cpu_instant: float                 # latest poll
    window_title: str | None           # raw title (None if no foreground window)
    process_name: str | None           # bare exe name like 'Code.exe'
    gpu_utilization: float | None = None
    """Latest GPU utilisation percentage (0-100) — None when unavailable.

    Sourced from ``nvidia-smi`` when present. Non-NVIDIA users (AMD /
    Intel iGPU / no GPU) get ``None``; the state machine treats ``None``
    as "signal absent" and falls back to keyword matching only.
    """
    os_signals_available: bool = True
    """Whether this snapshot reflects signals from the user's actual OS.

    False in two situations:
      - Backend running on non-Windows where ``GetForegroundWindow`` /
        ``GetLastInputInfo`` aren't available (Linux/macOS server).
      - Backend running on a remote server while the user is on a
        different machine — the OS APIs would report the *server's*
        state, which is irrelevant.

    State-machine and prompt formatter use this to avoid pretending to
    know what the user is doing when the OS view doesn't reflect them.
    Conversation-derived signals (voice mode, msg timestamps, LLM
    enrichment) keep working in degraded mode.
    """


# ── Implementation ──────────────────────────────────────────────────

class SystemSignalCollector:
    """Process singleton polling Windows for activity signals.

    Lifecycle: ``await collector.start()`` once during app startup;
    ``await collector.stop()`` on shutdown. ``snapshot()`` is cheap
    and lock-free — readers grab the latest ``SystemSnapshot`` instance.
    """

    # Window of CPU samples to average. 6 × 5s = 30s — matches the
    # ``cpu_avg_30s`` field name. Tuning this changes the responsiveness
    # of the "high CPU = focused/gaming" signal.
    _CPU_WINDOW = 6

    # nvidia-smi cold-start cost is ~50-150ms on Windows. We avoid running
    # it every tick — once every other tick (10s) is plenty for catching
    # gaming sessions without heating up the polling loop.
    _GPU_POLL_EVERY_N_TICKS = 2

    def __init__(self, *, poll_interval: float = 5.0) -> None:
        self._poll_interval = poll_interval
        self._cpu_samples: deque[float] = deque(maxlen=self._CPU_WINDOW)
        # Initial snapshot — replaced on first ``_tick``. ``os_signals_available``
        # is computed below after the optional imports settle, so we
        # defer the right value until then via __post_init__-like update.
        self._latest = SystemSnapshot(
            timestamp=0.0,
            idle_seconds=0.0,
            cpu_avg_30s=0.0,
            cpu_instant=0.0,
            window_title=None,
            process_name=None,
            os_signals_available=False,  # will be corrected once probes complete
        )
        self._task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None

        # GPU polling state — sticky last-good value, refreshed on every
        # _GPU_POLL_EVERY_N_TICKS-th tick. ``_gpu_available`` becomes
        # False after one failed probe and stays that way for the
        # process lifetime (no point retrying when nvidia-smi is absent
        # — non-NVIDIA hosts will never grow it).
        self._gpu_tick_counter = 0
        self._gpu_last_value: float | None = None
        self._gpu_available: bool = True

        # Cached imports — None when unavailable. Always gate use behind
        # the platform check so non-Windows just no-ops.
        self._ctypes_user32 = None
        self._gw = None
        self._psutil = None

        if _IS_WINDOWS:
            try:
                import ctypes  # noqa: F401
                self._ctypes_user32 = ctypes.windll.user32
            except Exception as e:  # pragma: no cover — wildly broken Windows
                logger.warning('SystemSignalCollector: ctypes.user32 unavailable: %s', e)

            try:
                import pygetwindow as gw
                self._gw = gw
            except ImportError:
                logger.warning('SystemSignalCollector: pygetwindow not installed')

        try:
            import psutil
            self._psutil = psutil
        except ImportError:
            logger.warning('SystemSignalCollector: psutil not installed; CPU signal disabled')

        # Capability flag — true when we can read at least the
        # foreground-window signal (the most user-relevant one). On
        # non-Windows backends or when pygetwindow is missing, this
        # stays False and the state machine treats subsequent snapshots
        # as degraded. CPU-only / idle-only collectors aren't useful
        # enough on their own to claim "I see what the user does".
        #
        # The env override force-degrades even on Windows-with-pygetwindow,
        # for the case where the backend is a Windows server and the user
        # is on a different machine — local OS APIs would technically
        # work but report data about the server, not the user.
        env_force_degraded = _force_degraded_from_env()
        self._os_signals_available: bool = bool(
            _IS_WINDOWS and self._gw is not None and not env_force_degraded
        )
        if not self._os_signals_available:
            reason = (
                'NEKO_ACTIVITY_TRACKER_REMOTE env override' if env_force_degraded
                else f'platform={sys.platform}, pygetwindow={self._gw is not None}'
            )
            logger.info(
                'SystemSignalCollector: OS-side user signals UNAVAILABLE '
                '(%s) — degraded mode. Window / idle / process detection '
                'disabled; conversation-based signals (voice mode, msg '
                'timing) still work. For remote deployments push '
                'frontend-side signals via '
                'UserActivityTracker.push_external_system_signal().',
                reason,
            )

    @property
    def os_signals_available(self) -> bool:
        """True when this collector can produce real foreground-window data."""
        return self._os_signals_available

    # ── public ───────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the background polling task. Idempotent.

        Eagerly runs one ``_tick`` before spawning the loop so callers
        right after ``start()`` see real signals rather than the initial
        default snapshot. The first tick costs ~5-50ms (psutil + window
        query); GPU polling deliberately skips the first tick anyway,
        so no nvidia-smi subprocess is added to start latency.
        """
        if self._task is not None and not self._task.done():
            return
        self._stop_event = asyncio.Event()

        # Prime psutil's CPU counter — the first ``cpu_percent`` call
        # always returns 0.0 because there's no prior interval to diff.
        if self._psutil is not None:
            try:
                self._psutil.cpu_percent(interval=None)
            except Exception as e:
                # Priming is purely an optimisation — losing it just means
                # the first tick reports 0.0 CPU; the next tick recovers.
                # Don't block start() on it.
                logger.debug('psutil CPU prime failed: %s', e)

        # Run one tick eagerly so the first ``snapshot()`` after
        # ``start()`` returns real data, not the SystemSnapshot defaults.
        try:
            await self._tick()
        except Exception as e:
            logger.debug('SystemSignalCollector initial tick failed: %s', e)

        self._task = asyncio.create_task(
            self._run(), name='SystemSignalCollector.poll'
        )
        logger.info(
            'SystemSignalCollector started (interval=%.1fs, windows=%s, signals_available=%s)',
            self._poll_interval, _IS_WINDOWS, self._os_signals_available,
        )

    async def stop(self) -> None:
        """Cancel the background task and wait for it to wind down."""
        if self._task is None:
            return
        if self._stop_event is not None:
            self._stop_event.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            # Expected — we just cancelled it.
            pass
        except Exception as e:
            # The poll loop already swallows per-tick errors; reaching here
            # means something at task-level escaped (rare). Log and move on
            # rather than letting shutdown propagate it.
            logger.debug('SystemSignalCollector task exit error: %s', e)
        self._task = None

    def snapshot(self) -> SystemSnapshot:
        """Return the most recent sampled state.

        Lock-free: each successful poll atomically rebinds ``self._latest``
        to a frozen dataclass instance. Readers see a complete prior
        sample, never a torn one.
        """
        return self._latest

    # ── polling loop ─────────────────────────────────────────────

    async def _run(self) -> None:
        # CPU counter priming happens in ``start`` now, before the
        # eager first tick — so by the time we reach the loop the
        # counter has already been seeded.
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            try:
                await self._tick()
            except Exception as e:
                logger.warning('SystemSignalCollector tick failed: %s', e)
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._poll_interval,
                )
            except asyncio.TimeoutError:
                continue

    async def _tick(self) -> None:
        """Read all signals on the executor, then publish one snapshot."""
        loop = asyncio.get_event_loop()

        # Degraded mode: skip the syscalls entirely. On a Windows-remote
        # backend the APIs would happily return data about the *server*,
        # not the user — much worse than emitting an empty snapshot the
        # state machine knows to ignore.
        if not self._os_signals_available:
            self._latest = SystemSnapshot(
                timestamp=time.time(),
                idle_seconds=0.0,
                cpu_avg_30s=0.0,
                cpu_instant=0.0,
                window_title=None,
                process_name=None,
                gpu_utilization=None,
                os_signals_available=False,
            )
            return

        idle, cpu_now, title, proc = await loop.run_in_executor(
            None, self._sync_read_all,
        )

        if cpu_now is not None:
            self._cpu_samples.append(cpu_now)
        cpu_avg = (
            sum(self._cpu_samples) / len(self._cpu_samples)
            if self._cpu_samples else 0.0
        )

        # GPU polling — only every Nth tick, in a separate executor run
        # so a slow nvidia-smi (~150ms cold) doesn't compete for the
        # input/cpu/window slot. Carries last-good between samples.
        self._gpu_tick_counter += 1
        if self._gpu_available and self._gpu_tick_counter % self._GPU_POLL_EVERY_N_TICKS == 0:
            new_gpu = await loop.run_in_executor(None, self._read_gpu_utilization)
            if new_gpu is not None:
                self._gpu_last_value = new_gpu
            elif self._gpu_tick_counter == self._GPU_POLL_EVERY_N_TICKS:
                # First probe failed — assume nvidia-smi unavailable on
                # this host and stop trying. Subsequent ticks short-circuit.
                self._gpu_available = False
                logger.info('SystemSignalCollector: GPU signal unavailable (nvidia-smi probe failed); disabling further polls')

        self._latest = SystemSnapshot(
            timestamp=time.time(),
            idle_seconds=idle if idle is not None else 0.0,
            cpu_avg_30s=cpu_avg,
            cpu_instant=cpu_now if cpu_now is not None else 0.0,
            window_title=title,
            process_name=proc,
            gpu_utilization=self._gpu_last_value,
            os_signals_available=self._os_signals_available,
        )

    # ── sync reads (run in executor) ─────────────────────────────

    def _sync_read_all(self) -> tuple[float | None, float | None, str | None, str | None]:
        """Run all three syscalls on the executor thread.

        Bundling them avoids paying three executor-roundtrip costs per
        tick. None-safety lets each individual signal degrade without
        knocking out the others.
        """
        return (
            self._read_idle_seconds(),
            self._read_cpu_percent(),
            *self._read_active_window(),
        )

    def _read_idle_seconds(self) -> float | None:
        """Seconds since the last keyboard/mouse input, system-wide."""
        if self._ctypes_user32 is None or not _IS_WINDOWS:
            return None
        # Single ``from ctypes import ...`` to keep the linter happy and
        # avoid mixing ``import ctypes`` with ``from ctypes import wintypes``
        # at the same scope.
        from ctypes import Structure, byref, sizeof, wintypes

        class LASTINPUTINFO(Structure):
            _fields_ = [('cbSize', wintypes.UINT), ('dwTime', wintypes.DWORD)]

        info = LASTINPUTINFO()
        info.cbSize = sizeof(LASTINPUTINFO)
        try:
            if not self._ctypes_user32.GetLastInputInfo(byref(info)):
                return None
            tick_now = self._ctypes_user32.GetTickCount()
            # GetTickCount wraps every ~49.7 days; the wraparound arithmetic
            # is correct as long as we cast through the unsigned domain,
            # which (tick_now - dwTime) & 0xFFFFFFFF gives us.
            elapsed_ms = (tick_now - info.dwTime) & 0xFFFFFFFF
            return elapsed_ms / 1000.0
        except Exception:
            return None

    def _read_cpu_percent(self) -> float | None:
        """Most recent CPU percentage. Non-blocking (interval=None)."""
        if self._psutil is None:
            return None
        try:
            return float(self._psutil.cpu_percent(interval=None))
        except Exception:
            return None

    def _read_active_window(self) -> tuple[str | None, str | None]:
        """Foreground window title + owning process exe name.

        On non-Windows or when pygetwindow is missing, both halves return
        ``None``. When the window query succeeds but process resolution
        fails, returns ``(title, None)`` — title is still useful even
        without process attribution.
        """
        if not _IS_WINDOWS or self._gw is None:
            return (None, None)
        try:
            win = self._gw.getActiveWindow()
            if win is None:
                return (None, None)
            title = win.title or None
        except Exception:
            return (None, None)

        proc_name = self._read_active_process_name()
        return (title, proc_name)

    def _read_gpu_utilization(self) -> float | None:
        """Read GPU utilisation via ``nvidia-smi`` subprocess.

        Returns the first GPU's utilization as a percentage (0-100), or
        None if nvidia-smi isn't on PATH / fails / returns garbage. The
        first failure flips ``_gpu_available`` off in the caller, so
        non-NVIDIA hosts pay the cost exactly once at startup.

        Multi-GPU systems: we deliberately use only the first GPU
        reported. Detecting "any GPU is busy" via ``max()`` would let a
        secondary GPU (e.g. an iGPU running a video decoder) flag the
        system as gaming, which is exactly the false positive the GPU
        signal is meant to avoid. The primary GPU is virtually always
        the one rendering the active window.
        """
        import subprocess
        try:
            # CREATE_NO_WINDOW (0x08000000) avoids a console flash when
            # called from a windowed Python process. shell=False to keep
            # subprocess invocation explicit and avoid PATH-quoting bugs.
            kwargs: dict = {
                'capture_output': True,
                'text': True,
                'timeout': 3.0,
            }
            if _IS_WINDOWS:
                kwargs['creationflags'] = 0x08000000
            result = subprocess.run(
                [
                    'nvidia-smi',
                    '--query-gpu=utilization.gpu',
                    '--format=csv,noheader,nounits',
                ],
                **kwargs,
            )
            if result.returncode != 0:
                return None
            first_line = (result.stdout or '').strip().splitlines()[0:1]
            if not first_line:
                return None
            return float(first_line[0].strip())
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, OSError):
            return None
        except Exception:
            return None

    def _read_active_process_name(self) -> str | None:
        """Resolve the foreground window's owning process name.

        Uses Win32: ``GetForegroundWindow`` → ``GetWindowThreadProcessId``
        → ``psutil.Process(pid).name()``. Falls back through any failure
        without raising — process attribution is best-effort context,
        not load-bearing.
        """
        if not _IS_WINDOWS or self._ctypes_user32 is None or self._psutil is None:
            return None
        try:
            from ctypes import byref, wintypes
            hwnd = self._ctypes_user32.GetForegroundWindow()
            if not hwnd:
                return None
            pid = wintypes.DWORD()
            self._ctypes_user32.GetWindowThreadProcessId(hwnd, byref(pid))
            if pid.value == 0:
                return None
            return self._psutil.Process(pid.value).name()
        except Exception:
            return None


# ── Module-level singleton accessor ─────────────────────────────────

_singleton: SystemSignalCollector | None = None


def get_system_signal_collector() -> SystemSignalCollector:
    """Return the process-wide collector, creating it lazily.

    The first caller is responsible for calling ``await collector.start()``
    — instantiation alone does not begin polling. This split keeps tests
    that just want to read ``snapshot()`` defaults from spinning up a
    background task.
    """
    global _singleton
    if _singleton is None:
        _singleton = SystemSignalCollector()
    return _singleton
