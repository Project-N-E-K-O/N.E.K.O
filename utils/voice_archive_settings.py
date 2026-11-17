"""Local opt-in for durable speech recordings; no character-specific defaults."""
import json
from pathlib import Path
import threading
import time

from utils.cloudsave_runtime import assert_cloudsave_writable
from utils.file_utils import atomic_write_json


# Snapshot POST transitions without reading disk on the realtime audio path.
# A tuple is (currently enabled, monotonic timestamp of the latest opt-in).
# Missing means the persisted startup setting has not changed in this process.
_recording_gates: dict[str, tuple[bool, int]] = {}
_save_lock = threading.Lock()


def settings_path(config):
    return Path(config.get_runtime_config_path('voice_archive.json'))


def recording_gate(config):
    """A lock-free, disk-free snapshot for the live speech producer."""
    return _recording_gates.get(str(settings_path(config)))


def load_settings(config):
    try:
        data = json.loads(settings_path(config).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {'enabled': False}
    return {'enabled': isinstance(data, dict) and data.get('enabled') is True}


def save_settings(config, enabled):
    path = settings_path(config)
    key = str(path)
    with _save_lock:
        assert_cloudsave_writable(config, operation='voice_archive_settings', target=key)
        previous = _recording_gates.get(key)
        # Publish the off state before writing, and establish the opt-in
        # boundary before a new true setting becomes visible on disk.
        opt_in_at = time.monotonic_ns() if enabled else (previous[1] if previous else 0)
        _recording_gates[key] = (False, opt_in_at)
        try:
            atomic_write_json(path, {'enabled': enabled})
        except BaseException:
            if previous is None:
                _recording_gates.pop(key, None)
            else:
                _recording_gates[key] = previous
            raise
        _recording_gates[key] = (enabled, opt_in_at)
    return {'enabled': enabled}
