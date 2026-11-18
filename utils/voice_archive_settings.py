"""Local opt-in for durable speech recordings; no character-specific defaults."""
import json
from pathlib import Path
import threading

from utils.cloudsave_runtime import assert_cloudsave_writable
from utils.file_utils import atomic_write_json


# Snapshot POST transitions without reading disk on the realtime audio path.
# The immutable tuple is (currently enabled, authorization generation).
# Missing means that the persisted startup setting has not changed here yet.
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


def write_if_recording_authorized(config, generation: int, write):
    """Keep the final consent check and SQLite commit in one sync boundary.

    Called only in the archive worker thread. Settings POSTs use the same
    lock, so no OFF/ON transition can race between checking and committing.
    The realtime producer never acquires this lock or touches the filesystem.
    """
    key = str(settings_path(config))
    with _save_lock:
        gate = _recording_gates.get(key)
        if gate is not None and (not gate[0] or gate[1] != generation):
            return False
        if not load_settings(config)['enabled']:
            return False
        write()
        return True


def save_settings(config, enabled):
    path = settings_path(config)
    key = str(path)
    with _save_lock:
        assert_cloudsave_writable(config, operation='voice_archive_settings', target=key)
        previous = _recording_gates.get(key)
        if previous is None:
            # This read is on the settings endpoint's worker thread, not on
            # live speech. A repeated True POST must not discard frames from
            # an already-enabled persisted recording setting.
            old_enabled = load_settings(config)['enabled']
            previous = (old_enabled, 0)
        changed = previous[0] != enabled
        next_generation = previous[1] + int(changed)
        if changed:
            # Block in-flight writers and producers while a transition is
            # persisted; a repeat save never changes authorization.
            _recording_gates[key] = (False, next_generation)
        try:
            atomic_write_json(path, {'enabled': enabled})
        except BaseException:
            _recording_gates[key] = previous
            raise
        _recording_gates[key] = (enabled, next_generation)
    return {'enabled': enabled}
