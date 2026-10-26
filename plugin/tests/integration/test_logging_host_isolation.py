"""Plugin startup owns its namespace without changing host logging policy."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = pytest.mark.plugin_integration

_SCENARIO = r'''
import io
import logging
from pathlib import Path
import sys

from utils.logger_config import setup_logging

scenario = sys.argv[1]
if scenario == 'launcher':
    from launcher_core import runtime
    runtime._configure_stdio_utf8 = lambda: None
    runtime._maybe_reexec_into_project_venv = lambda _: None
    runtime._configure_multiprocessing_executable = lambda _: None
    runtime._initialize_launcher_context = lambda: None
    runtime._patch_http_user_agents = lambda: None
    runtime._bootstrap_launcher_runtime(str(Path.cwd()))
    record = logging.LogRecord('probe', logging.INFO, 'probe.py', 1,
                               'progress {} at 50%', ('ready',), None)
    assert record.getMessage() == 'progress ready at 50%'
elif scenario == 'standalone':
    from plugin import user_plugin_server
    logging.getLogger('library.standalone-contract').info('standalone-library-marker')
    from plugin.server.http_app import build_plugin_server_app
    before = list(logging.getLogger().handlers)
    build_plugin_server_app()
    assert logging.getLogger().handlers == before, 'app factory downgraded explicit standalone ownership'
    assert 'standalone-library-marker' in user_plugin_server._log_config.log_file.read_text()
else:
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    formatter = logging.LogRecord.getMessage
    for service in ('Memory', 'Agent', 'Main'):
        setup_logging(service_name=service, silent=True)
    assert root.handlers == handlers
    assert root.level == level
    assert logging.LogRecord.getMessage is formatter, 'ordinary host initialization installed a global hook'

    from plugin.server.http_app import build_plugin_server_app
    from plugin.logging_config import get_logger, resolve_plugin_log_directory
    output = io.StringIO()
    host_handler = logging.StreamHandler(output)
    root.addHandler(host_handler)
    root.setLevel(logging.INFO)
    host_handlers, host_level = list(root.handlers), root.level
    shared = logging.getLogger('N.E.K.O')
    shared_handlers = list(shared.handlers)
    build_plugin_server_app()
    assert root.handlers == host_handlers
    assert root.level == host_level
    assert shared.handlers == shared_handlers
    get_logger('isolation-contract').info('plugin-namespace-marker')
    logging.getLogger('library.host-contract').info('host-library-marker')
    assert 'host-library-marker' in output.getvalue()
    assert 'plugin-namespace-marker' not in output.getvalue()
    files = resolve_plugin_log_directory().glob('N.E.K.O_PluginServer_*.log')
    assert 'plugin-namespace-marker' in '\n'.join(path.read_text() for path in files)
print('host isolation contract passed')
'''


@pytest.mark.parametrize('scenario', ['embedded', 'standalone', 'launcher'])
def test_logging_stays_within_plugin_scope(tmp_path, scenario):
    root = Path(__file__).resolve().parents[3]
    env = dict(os.environ)
    env.update(
        NEKO_STORAGE_SELECTED_ROOT=str(tmp_path / 'runtime'),
        NEKO_STORAGE_ANCHOR_ROOT=str(tmp_path / 'runtime'),
        XDG_DATA_HOME=str(tmp_path / 'user-data'),
        XDG_DOCUMENTS_DIR=str(tmp_path / 'documents'),
        NEKO_LOG_LEVEL='INFO',
    )
    env.pop('NEKO_PLUGIN_SERVICE_NAME', None)
    result = subprocess.run(
        [sys.executable, '-B', '-c', _SCENARIO, scenario], cwd=root, env=env,
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
