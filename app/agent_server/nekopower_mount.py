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

"""Ties the NekoPower host (``nekopower/host``) to this process.

``mount_nekopower_host`` runs during startup and must stay free: it tells the
host how to read this process's switches and what to call for the things only
this process can do, and subscribes it to plugin lifecycle events. It does not
build the host, open its ledger, start a runtime or connect to anything; that
happens on the first dispatch.

What this process gives the host:

* the switches (master switch, plugin switch);
* the built-in tools, keyboard-and-mouse and browser control
  (``nekopower_builtins``), and a listener told when a commission begins and
  stops using them;
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
and ``GET /tasks`` answer without touching the ledger. A task that waits for
the user carries what it asks (``question``), for the HUD to show with the
ways to answer; what a task agent's own task agents ask is carried by the
task shown above them (``sub_questions``), as they are not shown. A commission that stops waiting for the user, shown on the
HUD or not, is told as a ``nekopower_question`` event with no question and
the id of the one it asked (``question_id``): the card of that question is
closed wherever it is shown.
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


def nekopower_on() -> bool:
    """Whether NekoPower is on: the master switch and at least one capability switch.

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
    # What it asks while it waits for the user, with the answers on offer.
    # Said as None otherwise: the page merges what it is sent into what it
    # has, and a question left out would stay on the card once answered.
    task["question"] = _page_question(commission) if status == "waiting_user" else None
    return task


def _page_question(commission: Any) -> Optional[Dict[str, Any]]:
    """Return what a commission asks as a card shows it, in the commission's language."""
    from nekopower.host.reports import page_question

    return page_question(commission.question(), commission.lang or "en")


class HudTasks:
    """This process's view of the host's commissions, for the task HUD.

    A task agent's own task agents are not shown as tasks. What one of them
    asks while it waits for the user is shown with the task it was started
    for, the one the HUD shows (``sub_questions``, the one that asked first
    first): it is the user who is asked, and the HUD has nothing else to say
    that somebody there waits for her.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._tasks: Dict[str, Dict[str, Any]] = {}
        self._ended_at: Dict[str, float] = {}
        # The task agent each one that a task agent started was started by.
        self._parents: Dict[str, str] = {}
        # By the task shown: what the task agents under it ask, by their ids.
        self._asked_below: Dict[str, Dict[str, Dict[str, Any]]] = {}

    def _prune(self) -> None:
        cutoff = self._clock() - HUD_RETENTION_SECONDS
        gone = {task_id for task_id, ended in self._ended_at.items() if ended < cutoff}
        for task_id in gone:
            self._ended_at.pop(task_id, None)
            self._tasks.pop(task_id, None)
            self._asked_below.pop(task_id, None)
        if gone:
            for child_id in [child_id for child_id in self._parents if self._top(child_id) in gone]:
                self._parents.pop(child_id, None)

    def _top(self, commission_id: str) -> str:
        """Return the commission no task agent started that one descends from, as far as it is known here."""
        seen = {commission_id}
        while commission_id in self._parents:
            commission_id = self._parents[commission_id]
            if commission_id in seen:
                break
            seen.add(commission_id)
        return commission_id

    def _below(self, commission: Any) -> Optional[Dict[str, Any]]:
        """Take in the state of a task agent's own task agent; returns the task shown above it if what it shows changed."""
        self._parents[commission.id] = commission.parent_id
        top = self._top(commission.id)
        asked = self._asked_below.setdefault(top, {})
        question = _page_question(commission) if commission.status == "waiting_user" else None
        if question is not None:
            entry = {"id": commission.id, "name": commission.name or "", "title": commission.title or "", "question": question}
            if asked.get(commission.id) == entry:
                return None
            asked[commission.id] = entry
        elif asked.pop(commission.id, None) is None:
            return None
        if not asked:
            self._asked_below.pop(top, None)
        task = self._tasks.get(top)
        if task is None:
            return None
        task["sub_questions"] = list(asked.values())
        return task

    def update(self, commission: Any) -> Optional[Dict[str, Any]]:
        """Take in a commission's current state; returns the task the HUD is to be told of, or None.

        That is its own task; for a task agent's own task agent, the task
        shown above it, when what that one shows of it changed.
        """
        if commission.parent_id is not None:
            return self._below(commission)
        task = hud_task(commission)
        if task is None:
            return None
        # Always said, if only as none: the page merges what it is sent into what it has.
        task["sub_questions"] = list(self._asked_below.get(task["id"], {}).values())
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
        self._parents.clear()
        self._asked_below.clear()


hud = HudTasks()

# task_update events go out one at a time, in the order the changes happened;
# that a commission stopped waiting for the user goes out among them.
_QUESTION_CLOSED = "question_closed"
# The id of what each commission that waits for the user asks. The ledger
# lets go of the question as the commission stops waiting; which one that
# was is said then, so that a card of the next one, shown already, stays.
_asked: Dict[str, str] = {}
_updates: Optional["asyncio.Queue[Dict[str, Any]]"] = None
_updates_worker: Optional["asyncio.Task[None]"] = None


async def _send_updates() -> None:
    from .plugin_host import _get_plugin_friendly_name

    assert _updates is not None
    while True:
        task = await _updates.get()
        try:
            if task.get("type") == _QUESTION_CLOSED:
                await _emit_main_event(
                    "nekopower_question",
                    task.get("lanlan_name"),
                    task_id=task.get("id"),
                    question_id=task.get("question_id") or "",
                    question=None,
                )
                continue
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
    """A commission was filed or changed its status: the HUD is told.

    One that waited for the user and does not any more (answered here or
    elsewhere, stopped, over) has the card of its question closed: also one
    the HUD does not show, a task agent's own task agent, whose question the
    user is shown all the same.
    """
    task = hud.update(commission)
    if task is not None:
        _queue_update(task)
    if commission.status == "waiting_user":
        _asked[commission.id] = str((commission.question() or {}).get("id") or "")
    elif previous == "waiting_user":
        _queue_update(
            {
                "type": _QUESTION_CLOSED,
                "id": commission.id,
                "question_id": _asked.pop(commission.id, ""),
                "lanlan_name": commission.lanlan_name,
            }
        )


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
        "channel": "nekopower",
        "source_kind": "system",
        "source_name": "",
        # A correction is something that went wrong; the rest is information.
        "status": "blocked" if item.kind == "correction" and reason != "filed" else "completed",
        "success": True,
        "delivery_mode": payload.get("delivery_mode") or "proactive",
        # For her only: neither a chat bubble nor a HUD toast shows it.
        # Unless the row says it is for the HUD: what NekoPower tells the
        # user of its own settings is shown to the user as it is written,
        # and goes into no model's context (its delivery mode is silent).
        "visibility": ["hud"] if "hud" in (payload.get("visibility") or ()) else [],
        "priority": _NOTICE_PRIORITY,
        "coalesce_key": f"nekopower:{item.kind}:{task_id or item.id}",
        "metadata": {"nekopower_kind": item.kind, "nekopower_reason": reason},
        "timestamp": _now_iso(),
    }
    question = payload.get("card") or payload.get("question")
    if item.kind == "question" and task_id and isinstance(question, dict):
        # The parts of what is asked, for a card with the ways to answer; the
        # sentence above is hers to pass on all the same.
        event["question"] = question
        event["title"] = str(payload.get("title") or "")
        # Who does the work: a plugin called directly takes no sentence added to an approval.
        event["route"] = payload.get("route")
    return await _send_main_event(event)


async def _count_task_agent_request(commission_id: str) -> bool:
    """Count one model request of a task agent against the free daily quota.

    The host asks only for a session whose requests go with the built-in
    free key (``nekopower/host/role_models.py``), as the plugin gateway
    counts its calls: a session sent to a model with a key of the user's
    own is never counted, whatever the task agent model is.
    """
    del commission_id
    from utils.config_manager import get_config_manager

    allowed, _info = await get_config_manager().aconsume_free_daily_quota(source="nekopower.task_agent", units=1)
    return bool(allowed)


def mount_nekopower_host(change_token: Optional[Callable[[], Any]] = None) -> None:
    """Register this process with the NekoPower host. Cheap; safe to call twice."""
    global _unsubscribe
    try:
        from nekopower.host import mount
        from nekopower.host.role_models import PluginModelSlots

        from .nekopower_builtins import on_builtin_activity, provider

        mount.configure(
            enabled=nekopower_on,
            plugins_enabled=_plugins_enabled,
            change_token=change_token,
            builtins=provider,
            on_builtin_activity=on_builtin_activity,
            send_report=send_report,
            on_progress=_on_progress,
            on_commission=_on_commission_change,
            task_agent_meter=_count_task_agent_request,
            # The slots of the model panel each preset's model is chosen from.
            model_slots=PluginModelSlots(),
        )
        if _unsubscribe is None:
            # A plugin that starts, stops, reloads, is installed or removed
            # changes the capability list. The callback only sets a flag, so
            # it is fine that it runs on the plugin server's thread.
            from plugin.core.state import state as plugin_state

            _unsubscribe = plugin_state.bus_change_hub.subscribe("lifecycle", mount.notify_plugins_changed)
    except Exception as e:
        # Failing to register must not affect startup; dispatches then find nothing to do.
        logger.warning("[Agent] NekoPower host registration failed: %s", e)


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
    from nekopower.host import mount

    return mount.peek_host()


# Stops of everything that found no host and are on their way to one (they
# look for the record first, which takes a moment), and the hosts whose
# hand-overs are held for them. Whoever builds the host meanwhile (a page
# that asks for the tasks, say) starts it with nothing handed over: started
# as usual, it would hand a session what an earlier process left for it, and
# that would be a turn the stop comes too late for.
_stops_on_their_way = 0
_held_for_stops: list[Any] = []


def _hold_for_stops(host: Any) -> None:
    """Hold a host's hand-overs for the stops that are on their way to it; once per host, nothing awaited."""
    if _stops_on_their_way and not any(held is host for held in _held_for_stops):
        host.wakes.hold()
        _held_for_stops.append(host)


def _stop_arrived() -> None:
    """One of those stops is through: with the last of them, what was held for them goes on."""
    global _stops_on_their_way
    _stops_on_their_way -= 1
    if _stops_on_their_way <= 0:
        _stops_on_their_way = 0
        held, _held_for_stops[:] = list(_held_for_stops), []
        for host in held:
            host.wakes.release()


async def ready_host() -> Any:
    """Return the host, building it on first use.

    The first time, what a previous process left under way (deferred plugin
    results, mostly) is taken into the HUD's view, reports that process did
    not get to send start going out, and deferred results get their time
    limits back.
    """
    global _filled_from
    from nekopower.host import mount
    from nekopower.host.ledger import ACTIVE_STATUSES

    host = mount.get_host()
    # Before anything is awaited, and before it is started below.
    _hold_for_stops(host)
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
    once when the host is up, when NekoPower is off, or when there is no
    ledger file: nothing is built for an empty record.
    """
    host = peek_host()
    if host is not None and _filled_from is host:
        return
    if not nekopower_on():
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
    from nekopower.host import mount

    try:
        return await asyncio.to_thread(lambda: mount.ledger_path().exists())
    except Exception as exc:
        logger.warning("[Agent] looking for the ledger file failed (exc_type=%s)", type(exc).__name__)
        return False


async def stop_everything(reason: str = "Cancelled by user") -> int:
    """Stop every commission that is under way. Returns how many were stopped."""
    global _stops_on_their_way

    host = peek_host()
    held = None
    # Said before anything is awaited: see ``_stops_on_their_way``.
    on_its_way = host is None
    if on_its_way:
        _stops_on_their_way += 1
    # Whatever ends this, a cancel while the host is made ready included:
    # what was held is let go again, once. Left held, nothing would be
    # handed to a session for as long as the process lasts.
    try:
        if host is None:
            # Nothing has used the host in this process, yet an earlier process
            # may have left a deferred result under way. That is stopped too,
            # whether or not NekoPower is on by now.
            if not await ledger_exists():
                return 0
            try:
                # Held before it is made ready: making it ready starts what an
                # earlier process left to be handed to sessions, and a note
                # handed over then would start a turn this stop comes too late
                # for.
                from nekopower.host import mount

                built = mount.get_host()
                built.wakes.hold()
                held = built
                host = await ready_host()
            except Exception as e:
                logger.warning("[Agent] opening the ledger to stop what is under way failed: %s", e)
                return 0
        return await _stop_everything(host, reason)
    finally:
        if held is not None:
            held.wakes.release()
        if on_its_way:
            _stop_arrived()


async def _stop_everything(host: Any, reason: str) -> int:
    from nekopower.host.ledger import ACTIVE_STATUSES

    # A dispatch still in its first round has no commission yet and would be
    # missed below; it is told not to file one.
    host.brain.drop_pending_dispatches()
    # Likewise a note one task agent session wrote to another that was not
    # handed over yet: handed over after the stop, it would start a turn.
    # Dropped now, and none is handed over while the commissions are being
    # stopped; what one of them wrote meanwhile is dropped at the end. (A
    # time a task agent set for itself goes with its commission below.)
    async with host.stopping_everything():
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


async def shutdown_nekopower_host() -> None:
    """Stop the NekoPower host if anything ever started it.

    Waits ``HOST_SHUTDOWN_BUDGET_SECONDS`` at most. The host's steps go on
    after that until the process exits; see the setting for why that is safe.
    """
    global _unsubscribe, _filled_from, _restore_failed_at
    from config.nekopower_settings import HOST_SHUTDOWN_BUDGET_SECONDS

    unsubscribe, _unsubscribe = _unsubscribe, None
    if unsubscribe is not None:
        try:
            unsubscribe()
        except Exception as e:
            logger.debug("[Agent] NekoPower lifecycle unsubscribe failed: %s", e)
    try:
        from nekopower.host import mount

        # A task of its own, kept referenced until it is done and not among
        # the tasks this process cancels while it stops: the steps finish
        # even when this stops waiting for them.
        steps = asyncio.ensure_future(mount.shutdown_host())
        _lingering.add(steps)
        steps.add_done_callback(_lingering.discard)
        await asyncio.wait_for(asyncio.shield(steps), HOST_SHUTDOWN_BUDGET_SECONDS)
    except asyncio.TimeoutError:
        logger.warning(
            "[Agent] NekoPower host still shutting down after %.1fs; going on", HOST_SHUTDOWN_BUDGET_SECONDS
        )
    except Exception as e:
        logger.warning("[Agent] NekoPower host shutdown error: %s", e)
    _filled_from = None
    _restore_failed_at = None
    hud.clear()
    _asked.clear()


_lingering: "set[asyncio.Future[Any]]" = set()
