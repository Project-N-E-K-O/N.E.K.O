"""Lightweight spawn entry point for the atomic wake-word preference write."""

from multiprocessing.connection import Connection
from pathlib import Path


def save_preference_worker(connection: Connection, kind: str, nr_enabled: bool,
                           wake_path: str | None, pcm16: bytes) -> None:
    # Keep this target independent of the resource manager: unpickling it must
    # not import enrollment, numpy or native audio/model dependencies.
    try:
        from config.voice_wake_word import save_wake_word_preference

        result = save_wake_word_preference(pcm16 == b"\1", Path(wake_path))
        connection.send({"ok": True, "result": result})
    except ValueError as exc:
        connection.send({"ok": False, "reason": str(exc)})
    except OSError:
        connection.send({"ok": False, "reason": "resource_storage_unavailable"})
    except Exception:
        connection.send({"ok": False, "reason": "WAKE_WORD_WORKER_FAILED"})
    finally:
        connection.close()
