"""Lightweight spawn entry point for committing a validated model pointer."""

from multiprocessing.connection import Connection
from pathlib import Path


def publish_resource_worker(connection: Connection, kind: str, nr_enabled: bool,
                            wake_path: str | None, pcm16: bytes) -> None:
    from .wake_word_bundle import WakeWordBundleError, publish_bundle_version

    try:
        publish_bundle_version(Path(wake_path), pcm16.decode("ascii"))
        connection.send({"ok": True, "result": {"installed": True}})
    except WakeWordBundleError as exc:
        connection.send({"ok": False, "reason": exc.code})
    except OSError:
        connection.send({"ok": False, "reason": "resource_storage_unavailable"})
    except Exception:
        connection.send({"ok": False, "reason": "WAKE_WORD_WORKER_FAILED"})
    finally:
        connection.close()
