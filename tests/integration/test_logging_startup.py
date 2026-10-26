"""Production launcher/host initialization order, isolated from live services.

Launcher bootstrap and Agent imports run in a fresh interpreter with temporary
user data and a substituted server loop. The merged case checks real ownership
before application imports, then checks subsequent namespace initialization;
it does not launch three network services.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = pytest.mark.integration_serial

_SCENARIO = r'''
import builtins
import logging
from pathlib import Path
import sys
import threading

from launcher_core import runtime

repo, scenario = sys.argv[1:]
runtime._configure_stdio_utf8 = lambda: None
runtime._maybe_reexec_into_project_venv = lambda _: None
runtime._configure_multiprocessing_executable = lambda _: None
runtime._initialize_launcher_context = lambda: None
runtime._patch_http_user_agents = lambda: None

def bootstrap():
    runtime._bootstrap_launcher_runtime(repo)

if scenario == 'formatter':
    bootstrap()
    from utils.logger_config import setup_logging
    setup_logging(service_name='Probe', silent=True)
    for template, args, expected in [
        ('progress {} at 50%', ('ready',), 'progress ready at 50%'),
        ('named {state}', ({'state': 'ready'},), 'named ready'),
    ]:
        record = logging.LogRecord('probe', logging.INFO, 'probe.py', 1, template, args, None)
        assert record.getMessage() == expected
    print('startup contract passed')
    raise SystemExit(0)

if scenario == 'merged':
    bootstrap()
    class StopBeforeApplications(Exception): pass
    real_import = builtins.__import__
    def stop_at_first_application(name, globals=None, locals=None, fromlist=(), level=0):
        if name == 'app' and 'memory_server' in fromlist:
            logging.getLogger('library.before-merged-imports').info('merged-process-marker')
            raise StopBeforeApplications()
        return real_import(name, globals, locals, fromlist, level)
    builtins.__import__ = stop_at_first_application
    try:
        runtime.run_merged_servers()
    except StopBeforeApplications:
        pass
    finally:
        builtins.__import__ = real_import
    from utils.logger_config import setup_logging
    # The namespace bootstraps executed by the three application imports must
    # preserve the owner already selected by the merged process entrypoint.
    for name in ('Memory', 'Agent', 'Main'):
        setup_logging(service_name=name, silent=True)
else:
    if scenario == 'agent':
        bootstrap()
        import uvicorn
        class ServerLoop:
            def __init__(self, config): self.config = config
            def run(self):
                logging.getLogger('library.agent-server-loop').info('agent-loop-marker')
        uvicorn.Server = ServerLoop
        runtime._apply_child_process_signal_policy = lambda: None
        runtime._disable_uvicorn_signal_handlers = lambda _: None
        runtime.register_child_graceful_stop_hook = lambda _: None
        runtime.run_agent_server(threading.Event())
    else:
        # Import the actual direct-run Agent facade. Its existing setup_logging
        # call is intentionally not replaced with a more favorable test setup.
        from app import agent_server
        bootstrap()

from plugin.logging_config import get_logger, setup_plugin_logging
from utils.logging_layout import resolve_log_directory

root_before = list(logging.getLogger().handlers)
assert any(getattr(h, '_neko_owned', False) for h in root_before), 'host never claimed root'
setup_plugin_logging('PluginServer', own_root=False)
assert logging.getLogger().handlers == root_before, 'embedding replaced host ownership'
get_logger('startup-contract').info('plugin-namespace-marker')
logging.getLogger('library.startup-contract').info('ordinary-library-marker')
logging.getLogger('library.startup-contract').info('progress {} at 50%', 'ready')
for handler in logging.getLogger().handlers: handler.flush()

directory = resolve_log_directory()
agent_text = '\n'.join(p.read_text() for p in directory.glob('N.E.K.O_Agent_*.log'))
plugin_text = '\n'.join(p.read_text() for p in directory.glob('N.E.K.O_PluginServer_*.log'))
assert 'ordinary-library-marker' in agent_text
assert 'progress ready at 50%' in agent_text
assert 'ordinary-library-marker' not in plugin_text
assert 'plugin-namespace-marker' in plugin_text
assert 'plugin-namespace-marker' not in agent_text
if scenario == 'merged': assert 'merged-process-marker' in agent_text
if scenario == 'agent': assert 'agent-loop-marker' in agent_text
print('startup contract passed')
'''


@pytest.mark.parametrize('scenario', ['formatter', 'agent', 'direct-agent', 'merged'])
def test_production_logging_startup(tmp_path, scenario):
    repo = Path(__file__).resolve().parents[2]
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
        [sys.executable, '-B', '-c', _SCENARIO, str(repo), scenario],
        cwd=repo, env=env, text=True, capture_output=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
