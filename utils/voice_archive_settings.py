"""Local opt-in for durable speech recordings; no character-specific defaults."""
import json
from pathlib import Path

from utils.cloudsave_runtime import assert_cloudsave_writable
from utils.file_utils import atomic_write_json


def settings_path(config):
    return Path(config.get_runtime_config_path('voice_archive.json'))


def load_settings(config):
    try:
        data = json.loads(settings_path(config).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {'enabled': False}
    return {'enabled': isinstance(data, dict) and data.get('enabled') is True}


def save_settings(config, enabled):
    path = settings_path(config)
    assert_cloudsave_writable(config, operation='voice_archive_settings', target=str(path))
    atomic_write_json(path, {'enabled': enabled})
    return {'enabled': enabled}
