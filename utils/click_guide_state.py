"""Independent click-guide progress. Never changes the seven-day tutorial state."""

import json
from pathlib import Path
from threading import RLock
from time import time

from utils.file_utils import atomic_write_json

_LOCK = RLock()


def _path(config_manager):
    return Path(config_manager.get_config_path("click_guide_state.json"))


def get_click_guide_state(*, config_manager):
    with _LOCK:
        try:
            with _path(config_manager).open("r", encoding="utf-8") as handle:
                state = json.load(handle)
        except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
            state = None
        if isinstance(state, dict) and type(state.get("version")) is int and state["version"] > 1:
            # An older app must not downgrade a store written by a newer app.
            return {"version": 1, "revision": 0, "choice": "seven-day",
                    "status": "unseen", "pending": False, "readOnly": True}
        if (isinstance(state, dict) and type(state.get("version")) is int
                and state["version"] == 1
                and type(state.get("revision")) is int and state["revision"] >= 0
                and "choice" in state and state["choice"] in (None, "click", "seven-day")
                and state.get("status") in ("unseen", "completed", "skipped")
                and type(state.get("pending")) is bool):
            if state["choice"] == "click" and type(state.get("selectedAt")) is not int:
                state.update(selectedAt=int(time() * 1000), revision=state["revision"] + 1)
                atomic_write_json(_path(config_manager), state, ensure_ascii=False, indent=2)
            return state
        state = {"version": 1, "revision": 0, "choice": "seven-day",
                 "status": "unseen", "pending": False}
        atomic_write_json(_path(config_manager), state, ensure_ascii=False, indent=2)
        return state


def update_click_guide_state(payload, *, config_manager):
    with _LOCK:
        state = get_click_guide_state(config_manager=config_manager)
        if state.get("readOnly"):
            raise ValueError("Unsupported click-guide state version")
        revision = payload.get("expectedRevision")
        if type(revision) is not int or revision != state["revision"]:
            return {"ok": False, "state": state}
        action = payload.get("action")
        if action == "choose" and payload.get("choice") in ("click", "seven-day"):
            state.update(choice=payload["choice"], pending=payload["choice"] == "click",
                         status="unseen" if payload["choice"] == "click" else state["status"],
                         selectedAt=int(time() * 1000))
        elif action == "reset":
            state.update(choice="click", status="unseen", pending=True, selectedAt=int(time() * 1000))
        elif action == "finish" and payload.get("status") in ("completed", "skipped"):
            state.update(status=payload["status"], pending=False)
        else:
            raise ValueError("Invalid click-guide action")
        state["revision"] += 1
        atomic_write_json(_path(config_manager), state, ensure_ascii=False, indent=2)
        return {"ok": True, "state": state}
