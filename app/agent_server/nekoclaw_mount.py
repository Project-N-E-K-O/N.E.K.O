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

"""Ties the NekoClaw host (``nekoclaw/host``) to this process.

``mount_nekoclaw_host`` runs during startup and must stay free: it tells the
host how to read this process's switches and what to call for the things only
this process can do, and subscribes it to plugin lifecycle events. It does not
build the host, open its ledger, start a runtime or connect to anything; that
happens on the first dispatch.

What this process gives the host:

* the switches (master switch, plugin switch);
* the built-in capabilities, keyboard-and-mouse and browser control
  (``nekoclaw_builtins``);
* ``send_report``: every report the host has stored in its outbox goes to the
  main server over the event bridge. How a commission ended keeps the shape of
  a ``task_result`` event. A question for the user, a correction and a late
  receipt are sentences for her; they travel as a ``proactive_message`` from
  the system, which the main server already knows how to wake her with or to
  keep for her next turn;
* the count of a task agent's model requests against the free daily quota of
  the agent model.

What it takes from the host: every commission that is filed or changes its
status becomes a ``task_update`` event, the one the task HUD has always been
fed with. ``hud`` keeps this process's view of them, so the status snapshot
and ``GET /tasks`` answer without touching the ledger.
"""

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from . import _shared
from ._shared import _now_iso, logger
from .results import _emit_main_event, _send_main_event

_unsubscribe: Optional[Callable[[], Any]] = None

# How long a finished task stays in this process's view (the HUD shows it
# for a few seconds; a page that reloads meanwhile still finds it).
HUD_RETENTION_SECONDS = 300.0

# Ahead of plugin pushes of ordinary priority: a correction is only worth
# anything while what it corrects is still fresh.
_NOTICE_PRIORITY = 10

# Ledger status -> the status the task HUD is given.
_HUD_STATUS = {
    "queued": "queued",
    "working": "running",
    "waiting_user": "waiting_user",
    "done": "completed",
    "blocked": "blocked",
    "failed": "failed",
    "cancelled": "cancelled",
}
_HUD_ENDED = frozenset({"completed", "blocked", "failed", "cancelled"})
HUD_ACTIVE = frozenset({"queued", "running", "waiting_user"})


_CAPABILITY_SWITCHES = ("computer_use_enabled", "browser_use_enabled", "user_plugin_enabled")


def nekoclaw_on() -> bool:
    """Whether NekoClaw is on: the master switch and at least one capability switch.

    The main server reads a session the same way (``_is_agent_enabled``).
    With the master switch alone a session carries no task tool, so nothing
    is dispatched in her name either, and no turn of hers is checked.
    """
    flags = _shared.Modules.agent_flags or {}
    return bool(_shared.Modules.analyzer_enabled) and any(bool(flags.get(key)) for key in _CAPABILITY_SWITCHES)


def _plugins_enabled() -> bool:
    return bool((_shared.Modules.agent_flags or {}).get("user_plugin_enabled", False))


def _iso(timestamp: Optional[float]) -> Optional[str]:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(float(timestamp), timezone.utc).isoformat().replace("+00:00", "Z")


def hud_task(commission: Any) -> Optional[Dict[str, Any]]:
    """Return a commission as the task HUD knows tasks; None for one it does not show.

    A task agent started by another task agent is its parent's business and is
    not shown, as a query does not list it.
    """
    if commission.parent_id is not None:
        return None
    status = _HUD_STATUS.get(commission.status, "queued")
    params: Dict[str, Any] = {"description": commission.title, "instruction": commission.dispatch_text}
    if commission.route == "plugin":
        task_type = "user_plugin"
        params["plugin_id"] = commission.plugin_id
        params["entry_id"] = commission.entry_id
    elif commission.route == "task_agent":
        task_type = "task_agent"
    else:
        # Filed, not yet decided who gets it done.
        task_type = "task"
    task: Dict[str, Any] = {
        "id": commission.id,
        "status": status,
        "type": task_type,
        "start_time": _iso(commission.started_at or commission.created_at),
        "params": params,
        "lanlan_name": commission.lanlan_name,
    }
    if status in _HUD_ENDED:
        task["end_time"] = _iso(commission.ended_at or commission.updated_at)
    if status in ("failed", "blocked", "cancelled"):
        task["error"] = commission.status_reason or None
    return task


class HudTasks:
    """This process's view of the host's commissions, for the task HUD."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._tasks: Dict[str, Dict[str, Any]] = {}
        self._ended_at: Dict[str, float] = {}

    def _prune(self) -> None:
        cutoff = self._clock() - HUD_RETENTION_SECONDS
        for task_id in [task_id for task_id, ended in self._ended_at.items() if ended < cutoff]:
            self._ended_at.pop(task_id, None)
            self._tasks.pop(task_id, None)

    def update(self, commission: Any) -> Optional[Dict[str, Any]]:
        """Take in a commission's current state; returns its task, or None if it is not shown."""
        task = hud_task(commission)
        if task is None:
            return None
        previous = self._tasks.get(task["id"])
        if previous is not None:
            # The plugin's display name was looked up once already.
            name = (previous.get("params") or {}).get("plugin_name")
            if name and task["type"] == "user_plugin":
                task["params"]["plugin_name"] = name
        self._tasks[task["id"]] = task
        if task["status"] in _HUD_ENDED:
            self._ended_at.setdefault(task["id"], self._clock())
        else:
            self._ended_at.pop(task["id"], None)
        self._prune()
        return task

    def progress(self, commission_id: str, update: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Note the progress of a running task; returns the task, or None if it is not running."""
        task = self._tasks.get(commission_id)
        if task is None or task.get("status") != "running":
            return None
        for key in ("progress", "stage", "message", "step", "step_total"):
            if update.get(key) is not None:
                task[key] = update[key]
        return task

    def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        self._prune()
        task = self._tasks.get(task_id)
        return dict(task) if task is not None else None

    def all(self) -> List[Dict[str, Any]]:
        """Return every task still in view: under way, and ended a short while ago."""
        self._prune()
        return [dict(task) for task in self._tasks.values()]

    def active(self) -> List[Dict[str, Any]]:
        return [task for task in self.all() if task.get("status") in HUD_ACTIVE]

    def clear(self) -> None:
        self._tasks.clear()
        self._ended_at.clear()


hud = HudTasks()

# task_update events go out one at a time, in the order the changes happened.
_updates: Optional["asyncio.Queue[Dict[str, Any]]"] = None
_updates_worker: Optional["asyncio.Task[None]"] = None


async def _send_updates() -> None:
    from .plugin_host import _get_plugin_friendly_name

    assert _updates is not None
    while True:
        task = await _updates.get()
        try:
            params = task.get("params") or {}
            plugin_id = params.get("plugin_id")
            if task.get("type") == "user_plugin" and plugin_id and not params.get("plugin_name"):
                # The HUD shows the plugin by its display name. Cached; one
                # local request when the cache is cold.
                name = await _get_plugin_friendly_name(str(plugin_id))
                if name:
                    params["plugin_name"] = name
                    kept = hud._tasks.get(task["id"])
                    if kept is not None:
                        kept.setdefault("params", {})["plugin_name"] = name
            await _emit_main_event("task_update", task.get("lanlan_name"), task=task)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug("[Agent] task_update not sent: task_id=%s error=%s", task.get("id"), e)
        finally:
            _updates.task_done()


def _queue_update(task: Dict[str, Any]) -> None:
    global _updates, _updates_worker
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _updates is None:
        _updates = asyncio.Queue()
    if _updates_worker is None or _updates_worker.done():
        _updates_worker = loop.create_task(_send_updates())
        _shared.Modules._persistent_tasks.add(_updates_worker)
        _updates_worker.add_done_callback(_shared.Modules._persistent_tasks.discard)
    _updates.put_nowait(dict(task, params=dict(task.get("params") or {})))


def _on_commission_change(commission: Any, previous: Optional[str]) -> None:
    """A commission was filed or changed its status: the HUD is told."""
    del previous
    task = hud.update(commission)
    if task is not None:
        _queue_update(task)


def _on_progress(commission: Any, update: Dict[str, Any]) -> None:
    """A direct plugin call reported progress: the HUD's bar moves."""
    task = hud.progress(commission.id, update)
    if task is not None:
        _queue_update(task)


async def send_report(item: Any) -> bool:
    """Deliver one outbox row to the main server. True once the bridge has taken it."""
    payload = dict(item.payload or {})
    if item.kind == "task_result":
        return await _send_main_event({"event_type": "task_result", **payload, "timestamp": _now_iso()})
    text = str(payload.get("text") or "").strip()
    if not text:
        # Nothing to say; a row like this is not worth retrying.
        return True
    task_id = str(payload.get("task_id") or "")
    reason = str(payload.get("reason") or payload.get("receipt") or "")
    event = {
        "event_type": "proactive_message",
        "lanlan_name": payload.get("lanlan_name"),
        "text": text,
        "summary": text,
        "detail": text,
        "task_id": task_id,
        "channel": "nekoclaw",
        "source_kind": "system",
        "source_name": "",
        # A correction is something that went wrong; the rest is information.
        "status": "blocked" if item.kind == "correction" and reason != "filed" else "completed",
        "success": True,
        "delivery_mode": payload.get("delivery_mode") or "proactive",
        # For her only: neither a chat bubble nor a HUD toast shows it.
        "visibility": [],
        "priority": _NOTICE_PRIORITY,
        "coalesce_key": f"nekoclaw:{item.kind}:{task_id or item.id}",
        "metadata": {"nekoclaw_kind": item.kind, "nekoclaw_reason": reason},
        "timestamp": _now_iso(),
    }
    return await _send_main_event(event)


async def _count_task_agent_request(commission_id: str) -> bool:
    """Count one model request of a task agent against the free daily quota of the agent model.

    Task agents run on the agent model, as keyboard-and-mouse and browser
    control do, and are counted the same way. A paid configuration is not
    metered and always has a request left.
    """
    del commission_id
    from utils.config_manager import get_config_manager

    allowed, _info = await get_config_manager().aconsume_agent_daily_quota(source="nekoclaw.task_agent", units=1)
    return bool(allowed)


def mount_nekoclaw_host(change_token: Optional[Callable[[], Any]] = None) -> None:
    """Register this process with the NekoClaw host. Cheap; safe to call twice."""
    global _unsubscribe
    try:
        from nekoclaw.host import mount

        from .nekoclaw_builtins import provider

        mount.configure(
            enabled=nekoclaw_on,
            plugins_enabled=_plugins_enabled,
            change_token=change_token,
            builtins=provider,
            send_report=send_report,
            on_progress=_on_progress,
            on_commission=_on_commission_change,
            task_agent_meter=_count_task_agent_request,
        )
        if _unsubscribe is None:
            # A plugin that starts, stops, reloads, is installed or removed
            # changes the capability list. The callback only sets a flag, so
            # it is fine that it runs on the plugin server's thread.
            from plugin.core.state import state as plugin_state

            _unsubscribe = plugin_state.bus_change_hub.subscribe("lifecycle", mount.notify_plugins_changed)
    except Exception as e:
        # Failing to register must not affect startup; dispatches then find nothing to do.
        logger.warning("[Agent] NekoClaw host registration failed: %s", e)


# The host whose commissions ``hud`` has been filled from.
_filled_from: Any = None
# When building the host for ``restore_hud`` last failed (monotonic seconds).
_restore_failed_at: Optional[float] = None
# How long ``restore_hud`` leaves the host unbuilt after that failed. The
# task window asks every few seconds; each try would build the host again.
RESTORE_RETRY_SECONDS = 30.0
_restore_clock = time.monotonic


def peek_host() -> Any:
    """Return the host if anything has built it, else None. Builds nothing."""
    from nekoclaw.host import mount

    return mount.peek_host()


async def ready_host() -> Any:
    """Return the host, building it on first use.

    The first time, what a previous process left under way (deferred plugin
    results, mostly) is taken into the HUD's view, reports that process did
    not get to send start going out, and deferred results get their time
    limits back.
    """
    global _filled_from
    from nekoclaw.host import mount
    from nekoclaw.host.ledger import ACTIVE_STATUSES

    host = mount.get_host()
    if _filled_from is not host:
        try:
            for commission in await host.ledger.select(statuses=ACTIVE_STATUSES, oldest_first=True):
                if hud.get(commission.id) is None:
                    hud.update(commission)
            host.start()
            # The timer of a deferred result went with the process that set
            # it. Without this a page that only reads the tasks would show
            # one as running for good: what is overdue fails now, the rest
            # is timed again.
            await host.brain.expire_deferred()
        except Exception as e:
            # Not marked as done: the next call tries again.
            logger.warning("[Agent] reading the commissions under way failed: %s", e)
        else:
            _filled_from = host
    return host


async def restore_hud() -> None:
    """Take what an earlier process left under way into the HUD's view, if there is anything to read.

    The HUD is filled when the host is first used. A page that asks for the
    tasks before that (right after a restart, with a deferred plugin result
    still on record) would be shown none, and could not stop one. Returns at
    once when the host is up, when NekoClaw is off, or when there is no
    ledger file: nothing is built for an empty record.
    """
    host = peek_host()
    if host is not None and _filled_from is host:
        return
    if not nekoclaw_on():
        return
    global _restore_failed_at
    if _restore_failed_at is not None and _restore_clock() - _restore_failed_at < RESTORE_RETRY_SECONDS:
        return
    if host is None and not await ledger_exists():
        return
    try:
        await ready_host()
    except Exception:
        # The pages that call this only read what is in memory; a host that
        # cannot be built must not turn them into errors.
        _restore_failed_at = _restore_clock()
        logger.warning("[Agent] building the host to restore the HUD failed", exc_info=True)
    else:
        # ``ready_host`` keeps a failure to read the ledger to itself and
        # leaves the HUD unfilled; that waits as well.
        _restore_failed_at = None if _filled_from is peek_host() else _restore_clock()


async def ledger_exists() -> bool:
    """Whether a ledger file is there to read, without building the host."""
    from nekoclaw.host import mount

    try:
        return await asyncio.to_thread(lambda: mount.ledger_path().exists())
    except Exception as exc:
        logger.warning("[Agent] looking for the ledger file failed (exc_type=%s)", type(exc).__name__)
        return False


async def stop_everything(reason: str = "Cancelled by user") -> int:
    """Stop every commission that is under way. Returns how many were stopped."""
    from nekoclaw.host.ledger import ACTIVE_STATUSES

    host = peek_host()
    if host is None:
        # Nothing has used the host in this process, yet an earlier process
        # may have left a deferred result under way. That is stopped too,
        # whether or not NekoClaw is on by now.
        if not await ledger_exists():
            return 0
        try:
            host = await ready_host()
        except Exception as e:
            logger.warning("[Agent] opening the ledger to stop what is under way failed: %s", e)
            return 0
    # A dispatch still in its first round has no commission yet and would be
    # missed below; it is told not to file one.
    host.brain.drop_pending_dispatches()
    to_stop = [
        commission
        for commission in await host.ledger.select(statuses=ACTIVE_STATUSES)
        # A child is stopped along with its parent. A blocked one has ended
        # as far as work goes; it only waits to be taken up again.
        if commission.parent_id is None and commission.status != "blocked"
    ]
    # All at once: a plugin that is slow to take its cancel must not keep the
    # other commissions at work until it has answered.
    outcomes = await asyncio.gather(
        *(host.abort(commission.id, reason=reason) for commission in to_stop), return_exceptions=True
    )
    stopped = 0
    for commission, outcome in zip(to_stop, outcomes):
        if isinstance(outcome, BaseException):
            logger.warning("[Agent] stopping commission %s failed: %s", commission.id, outcome)
        else:
            stopped += 1
    return stopped


async def shutdown_nekoclaw_host() -> None:
    """Stop the NekoClaw host if anything ever started it.

    Waits ``HOST_SHUTDOWN_BUDGET_SECONDS`` at most. The host's steps go on
    after that until the process exits; see the setting for why that is safe.
    """
    global _unsubscribe, _filled_from, _restore_failed_at
    from config.nekoclaw_settings import HOST_SHUTDOWN_BUDGET_SECONDS

    unsubscribe, _unsubscribe = _unsubscribe, None
    if unsubscribe is not None:
        try:
            unsubscribe()
        except Exception as e:
            logger.debug("[Agent] NekoClaw lifecycle unsubscribe failed: %s", e)
    try:
        from nekoclaw.host import mount

        # A task of its own, kept referenced until it is done and not among
        # the tasks this process cancels while it stops: the steps finish
        # even when this stops waiting for them.
        steps = asyncio.ensure_future(mount.shutdown_host())
        _lingering.add(steps)
        steps.add_done_callback(_lingering.discard)
        await asyncio.wait_for(asyncio.shield(steps), HOST_SHUTDOWN_BUDGET_SECONDS)
    except asyncio.TimeoutError:
        logger.warning(
            "[Agent] NekoClaw host still shutting down after %.1fs; going on", HOST_SHUTDOWN_BUDGET_SECONDS
        )
    except Exception as e:
        logger.warning("[Agent] NekoClaw host shutdown error: %s", e)
    _filled_from = None
    _restore_failed_at = None
    hud.clear()


_lingering: "set[asyncio.Future[Any]]" = set()
