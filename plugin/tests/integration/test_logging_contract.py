"""Exercise published logging entrypoints through the management transports.

Each scenario runs in its own interpreter: logging ownership is process-local,
and resetting globals before a scenario would hide the startup-order contract.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


_SCENARIO = """
import io
import logging
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugin.logging_config import get_logger, setup_plugin_logging
from utils.logging_layout import plugin_log_service_name

target, mode = sys.argv[1:]
service = 'PluginServer' if target == '_server' else plugin_log_service_name(target)
if mode == 'embedded':
    from plugin.server.http_app import build_plugin_server_app
    build_plugin_server_app()
else:
    setup_plugin_logging(service_name=service, own_root=True)

expected = ['sdk-contract-marker']
get_logger('contract').info(expected[0])
if target != '_server':
    from plugin import PluginFileLogger, enable_plugin_file_logging, plugin_file_logger
    from plugin.sdk.shared.logging import get_plugin_logger, get_sdk_logger

    # These published entrypoints must remain functional, not just importable.
    PluginFileLogger(target, Path.cwd()).setup().info('file-logger-contract-marker')
    enable_plugin_file_logging(target, Path.cwd()).info('helper-contract-marker')
    get_plugin_logger(target).info('plugin-helper-contract-marker')
    get_sdk_logger().info('sdk-helper-contract-marker')
    @plugin_file_logger()
    class LegacyPlugin:
        def __init__(self, ctx):
            pass
    legacy = LegacyPlugin(SimpleNamespace(plugin_id=target, config_path=None))
    legacy.file_logger.info('decorator-contract-marker')
    expected += ['file-logger-contract-marker', 'helper-contract-marker',
                 'plugin-helper-contract-marker', 'sdk-helper-contract-marker',
                 'decorator-contract-marker']
else:
    # Exercise production module loggers, including the embedded import mode.
    os.environ['NEKO_PLUGIN_HOSTED_BY_AGENT'] = str(mode == 'embedded').lower()
    from plugin.server import http_app, lifecycle
    http_app.logger.info('http-app-contract-marker')
    lifecycle.logger.info('lifecycle-contract-marker')
    expected += ['http-app-contract-marker', 'lifecycle-contract-marker']
if mode == 'standalone':
    logging.getLogger('third_party.contract').warning('stdlib-contract-marker')
    expected.append('stdlib-contract-marker')

from plugin.server.routes.logs import router

app = FastAPI()
app.include_router(router)
with TestClient(app) as client:
    response = client.get(f'/plugin/{target}/logs', params={'lines': 500})
    assert response.status_code == 200, response.text
    data = response.json()
    messages = [row['message'] for row in data['logs']]
    assert all(marker in messages for marker in expected), (expected, messages)
    assert data['log_file'].startswith('N.E.K.O_' + service + '_'), data

    # A client without subscription parameters retains the original protocol.
    with client.websocket_connect(f'/ws/logs/{target}') as websocket:
        packet = websocket.receive_json()
        assert packet['type'] == 'initial', packet
        assert all(marker in [row['message'] for row in packet['logs']]
                   for marker in expected), packet

    with client.websocket_connect(
        f'/ws/logs/{target}?lines=500&search=stdlib-contract-marker'
    ) as websocket:
        packet = websocket.receive_json()
        assert packet['type'] == 'initial', packet
        assert all('stdlib-contract-marker' in row['message']
                   for row in packet['logs']), packet

    response = client.get(f'/plugin/{target}/logs/export')
    assert response.status_code == 200, response.text
    assert response.headers['content-type'] == 'application/zip', response.headers
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        exported = '\\n'.join(archive.read(name).decode('utf-8')
                             for name in archive.namelist())
        assert all(marker in exported for marker in expected), exported
print('logging compatibility contract passed')
"""


@pytest.mark.plugin_integration
@pytest.mark.parametrize(
    ('target', 'mode'),
    [('_server', 'standalone'), ('_server', 'embedded'), ('compat_demo', 'standalone')],
)
def test_public_logging_to_management_transports(tmp_path, target, mode):
    root = Path(__file__).resolve().parents[3]
    env = dict(os.environ)
    env['NEKO_STORAGE_SELECTED_ROOT'] = str(tmp_path / 'runtime')
    env['NEKO_STORAGE_ANCHOR_ROOT'] = str(tmp_path / 'runtime')
    env['XDG_DATA_HOME'] = str(tmp_path / 'user-data')
    env['XDG_DOCUMENTS_DIR'] = str(tmp_path / 'documents')
    env['NEKO_LOG_LEVEL'] = 'INFO'
    env.pop('NEKO_PLUGIN_SERVICE_NAME', None)
    result = subprocess.run(
        [sys.executable, '-B', '-c', textwrap.dedent(_SCENARIO), target, mode],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
