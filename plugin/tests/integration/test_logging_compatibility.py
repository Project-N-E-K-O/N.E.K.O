"""Process-local logging contracts for plugins and their containing applications."""
from __future__ import annotations

import os
import multiprocessing
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

pytestmark = pytest.mark.plugin_integration
ROOT = Path(__file__).resolve().parents[3]


def run_scenario(tmp_path: Path, source: str) -> None:
    from plugin.tests.logging_scenarios import isolated_logging_environment

    script = tmp_path / "scenario.py"
    script.write_text(textwrap.dedent(source), encoding="utf-8")
    env = isolated_logging_environment(tmp_path, level="DEBUG")
    result = subprocess.run([sys.executable, "-B", str(script)], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_sdk_percent_records_keep_filtering_and_handler_error_protection(tmp_path):
    run_scenario(tmp_path, '''
        import logging
        from plugin.logging_config import get_logger, setup_plugin_logging
        setup_plugin_logging('Plugin_demo', own_root=True)
        logger = get_logger('contract')
        class Broken:
            def __str__(self):
                raise RuntimeError('cannot render')
        # Standard handlers handle formatting failures, rather than plugin code.
        logging.raiseExceptions = False
        logger.info('value=%s', Broken())
        records = []
        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record)
        raw = logging.getLogger('N.E.K.O.Plugin_demo.contract')
        raw.handlers = [Capture()]
        raw.propagate = False
        value = object()
        logger.info('value=%s', value)
        assert records[0].msg == 'value=%s'
        assert records[0].args == (value,)
        logger.info('value=%(x)s {literal}', {'x': 'ok'})
        assert records[1].msg == 'value=%(x)s {literal}'
        assert records[1].args == {'x': 'ok'}
        from types import MappingProxyType
        mapping = MappingProxyType({'x': 'ok'})
        logger.info('value=%(x)s {literal}', mapping)
        assert records[2].msg == 'value=%(x)s {literal}'
        assert records[2].args is mapping
    ''')


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires fork")
def test_plugin_child_keeps_host_named_loggers_and_parent_files_separate(tmp_path):
    run_scenario(tmp_path, '''
        import logging, os
        from pathlib import Path
        from utils.logger_config import setup_logging, get_module_logger
        from plugin.logging_config import setup_plugin_logging, get_logger
        main, config = setup_logging(service_name='Main', silent=True)
        setup_logging(service_name='Agent', silent=True)
        inherited = get_module_logger('contract', 'Main')
        pid = os.fork()
        if pid == 0:
            try:
                setup_plugin_logging('Plugin_demo', own_root=True)
                inherited.info('child-main-marker')
                get_module_logger('shared').info('child-shared-marker')
                get_logger('contract').info('child-sdk-marker')
                logging.getLogger('library.contract').info('child-library-marker')
                setup_plugin_logging('Plugin_demo', own_root=True, log_level=logging.WARNING)
                inherited.info('filtered-after-reconfiguration')
                inherited.warning('child-reconfigured-marker')
                os._exit(0)
            except BaseException:
                os._exit(1)
        _, status = os.waitpid(pid, 0)
        assert os.waitstatus_to_exitcode(status) == 0
        main.info('parent-after-marker')
        parent = config.log_file.read_text()
        assert 'parent-after-marker' in parent
        assert 'child-' not in parent
        child = '\\n'.join(p.read_text() for p in (config.log_dir / 'plugin').glob('*.log'))
        for marker in ['child-main-marker', 'child-shared-marker', 'child-sdk-marker', 'child-library-marker']:
            assert child.count(marker) == 1, child
        assert child.count('child-reconfigured-marker') == 1
        assert 'filtered-after-reconfiguration' not in child
    ''')


@pytest.mark.parametrize("service", ["Main", "Memory", "Agent", "Monitor"])
def test_host_setup_retains_upstream_replacement_policy(tmp_path, service):
    run_scenario(tmp_path, '''
        import logging
        from utils.logger_config import setup_logging
        root = logging.getLogger()
        root_before = (list(root.handlers), root.level)
        formatter = logging.LogRecord.getMessage
        namespace = logging.getLogger('N.E.K.O.SERVICE')
        foreign = logging.NullHandler()
        namespace.addHandler(foreign)
        _, first = setup_logging(service_name='SERVICE', silent=True)
        original_handlers = list(namespace.handlers)
        assert foreign not in namespace.handlers and foreign._closed
        files = [h for h in namespace.handlers if isinstance(h, logging.FileHandler)]
        assert all(h.stream is not None and not h.delay for h in files)
        shared = logging.getLogger('N.E.K.O')
        shared_before = list(shared.handlers)
        _, second = setup_logging(service_name='SERVICE', silent=True)
        assert all(h._closed for h in original_handlers)
        assert shared.handlers == shared_before
        assert (root.handlers, root.level) == root_before
        assert logging.LogRecord.getMessage is formatter
        nested = setup_logging(service_name='Other', silent=True, log_subdir=' /one/two/ ')[1]
        assert nested.log_dir == second.log_dir / 'one/two'
    '''.replace('SERVICE', service))


@pytest.mark.parametrize("obstruction", [
    pytest.param("symlink", marks=pytest.mark.skipif(os.name == "nt", reason="requires POSIX symlink")),
    "file",
])
def test_logging_directory_failure_degrades_and_explicit_retry_recovers(tmp_path, obstruction):
    run_scenario(tmp_path, '''
        import os
        from pathlib import Path
        from plugin.logging_config import setup_plugin_logging, get_logger
        base = Path(os.environ['NEKO_STORAGE_SELECTED_ROOT']) / 'logs'
        base.mkdir(parents=True)
        CREATE_OBSTRUCTION
        _, degraded = setup_plugin_logging('Plugin_demo', own_root=True)
        assert degraded is None
        get_logger('contract').info('business-continues')
        (base / 'plugin').unlink()
        _, config = setup_plugin_logging('Plugin_demo', own_root=True)
        get_logger('contract').info('recovered-marker')
        assert config.log_file.read_text().count('recovered-marker') == 1
    '''.replace("CREATE_OBSTRUCTION", (
        "(base / 'plugin').symlink_to(base / 'missing', target_is_directory=True)"
        if obstruction == "symlink" else "(base / 'plugin').write_text('blocked')")))


@pytest.mark.parametrize("method", multiprocessing.get_all_start_methods())
def test_spawned_sdk_worker_initializes_without_a_host_runner(tmp_path, method):
    run_scenario(tmp_path, '''
        import multiprocessing as mp, os
        from pathlib import Path
        from plugin.logging_config import get_logger, setup_plugin_logging
        def idle_worker():
            pass
        def worker():
            import logging
            setup_plugin_logging('Plugin_demo', own_root=True)
            logging.getLogger('worker.library').info('before-sdk-marker')
            get_logger('worker').info('spawn-worker-marker')
            logging.getLogger('worker.library').info('spawn-library-marker')
        if __name__ == '__main__':
            context = mp.get_context('WORKER_START_METHOD')
            if 'WORKER_START_METHOD' == 'forkserver':
                warm = context.Process(target=idle_worker)
                warm.start()
                warm.join(15)
                assert warm.exitcode == 0
            setup_plugin_logging('Plugin_demo', own_root=True)
            child = context.Process(target=worker)
            child.start()
            child.join(15)
            assert not child.is_alive()
            assert child.exitcode == 0
            text = '\\n'.join(p.read_text() for p in Path(os.environ['NEKO_STORAGE_SELECTED_ROOT']).rglob('*.log'))
            assert text.count('spawn-worker-marker') == 1, text
            assert text.count('spawn-library-marker') == 1, text
            assert text.count('before-sdk-marker') == 1, text
    '''.replace('WORKER_START_METHOD', method))


def test_explicit_sdk_setup_preserves_legacy_level_behavior(tmp_path):
    run_scenario(tmp_path, '''
        import logging
        from plugin.logging_config import get_logger, setup_plugin_logging
        from plugin.sdk.shared.logging import setup_sdk_logging, LogLevel
        _, config = setup_plugin_logging('Plugin_demo', own_root=True)
        assert logging.getLogger('N.E.K.O.Plugin_demo').level == logging.INFO
        setup_sdk_logging('contract', level=LogLevel.WARNING)
        get_logger('contract').info('legacy-info-marker')
        assert 'legacy-info-marker' in config.log_file.read_text()
    ''')


def test_legacy_sdk_setup_initializes_files_without_a_host(tmp_path):
    run_scenario(tmp_path, '''
        import os
        from pathlib import Path
        from plugin.sdk.shared.logging import setup_sdk_logging, get_sdk_logger
        setup_sdk_logging()
        get_sdk_logger().info('standalone-sdk-marker')
        text = '\\n'.join(p.read_text() for p in Path(os.environ['NEKO_STORAGE_SELECTED_ROOT']).rglob('*.log'))
        assert text.count('standalone-sdk-marker') == 1
    ''')


def test_embedded_collection_preserves_existing_host_file_output(tmp_path):
    run_scenario(tmp_path, '''
        import logging
        from pathlib import Path
        from utils.logger_config import setup_logging
        _, main = setup_logging(service_name='Main', silent=True)
        root = logging.getLogger()
        foreign = logging.FileHandler(main.log_dir / 'host-owned.log')
        root.addHandler(foreign)
        root.setLevel(logging.INFO)
        before = (list(root.handlers), root.level)
        shared = logging.getLogger('N.E.K.O')
        shared_before = list(shared.handlers)
        from plugin.server.http_app import build_plugin_server_app
        build_plugin_server_app()
        assert (root.handlers, root.level) == before
        assert shared.handlers == shared_before
        logging.getLogger('library').error('host-file-marker')
        assert 'host-file-marker' in Path(foreign.baseFilename).read_text()
    ''')


def test_sdk_respects_an_already_configured_namespace(tmp_path):
    run_scenario(tmp_path, '''
        import io, logging, os
        os.environ['NEKO_PLUGIN_SERVICE_NAME'] = 'Plugin_demo'
        output = io.StringIO()
        raw = logging.getLogger('N.E.K.O.Plugin_demo')
        raw.setLevel(logging.WARNING)
        raw.propagate = False
        foreign = logging.StreamHandler(output)
        raw.addHandler(foreign)
        from plugin.logging_config import get_logger
        get_logger('consumer').info('filtered-info')
        get_logger('consumer').warning('visible-warning')
        assert raw.level == logging.WARNING
        assert raw.handlers == [foreign]
        assert output.getvalue() == 'visible-warning\\n'
    ''')


def test_sdk_lazy_initialization_keeps_legacy_third_party_collection(tmp_path):
    run_scenario(tmp_path, '''
        import logging, os
        from pathlib import Path
        from plugin.logging_config import get_logger
        root = logging.getLogger()
        before = (list(root.handlers), root.level)
        get_logger('sdk').info('sdk-only-marker')
        logging.getLogger('library').info('unmanaged-library-marker')
        assert all(h in root.handlers for h in before[0])
        text = '\\n'.join(p.read_text() for p in Path(os.environ['NEKO_STORAGE_SELECTED_ROOT']).rglob('*.log'))
        assert 'sdk-only-marker' in text
        assert text.count('unmanaged-library-marker') == 1
    ''')


def test_plugin_reconfiguration_preserves_external_partial_subscription(tmp_path):
    run_scenario(tmp_path, '''
        import logging
        from plugin.logging_config import setup_plugin_logging
        _, config = setup_plugin_logging('Plugin_demo', own_root=True)
        main = logging.getLogger('N.E.K.O.Plugin_demo')
        error = next(h for h in main.handlers if isinstance(h, logging.FileHandler) and h.level == logging.ERROR)
        class Reject(logging.Filter):
            def filter(self, record):
                return 'restricted-marker' not in record.getMessage()
        reject = Reject()
        error.addFilter(reject)
        borrowed = logging.getLogger('external.error-only')
        borrowed.setLevel(logging.INFO)
        borrowed.propagate = False
        borrowed.addHandler(error)
        previous_handlers = tuple(main.handlers)
        _, replacement = setup_plugin_logging('Plugin_demo', own_root=True, log_level=logging.WARNING)
        assert replacement is not config
        assert not set(previous_handlers).intersection(main.handlers)
        assert borrowed.handlers == [error]
        assert error.filters == [reject]
        assert not error._closed
        main.warning('replacement-namespace-marker')
        borrowed.info('external-info-marker')
        borrowed.error('restricted-marker')
        borrowed.error('external-error-marker')
        regular = config.log_file.read_text()
        errors = (config.log_dir / 'N.E.K.O_Plugin_demo_error.log').read_text()
        assert 'external-info-marker' not in regular + errors
        assert 'restricted-marker' not in regular + errors
        assert errors.count('external-error-marker') == 1
        assert 'external-error-marker' not in regular
        assert regular.count('replacement-namespace-marker') == 1
    ''')


def test_reconfiguration_does_not_attach_unfinished_handlers(tmp_path):
    run_scenario(tmp_path, '''
        import logging, threading
        from utils import logger_config as backend
        from plugin.logging_config import setup_plugin_logging
        main, config = setup_plugin_logging("Plugin_demo", own_root=True)
        constructing = threading.Event()
        release = threading.Event()
        original = backend.RotatingFileHandler
        def slow_handler(filename, *args, **kwargs):
            handler = original(filename, *args, **kwargs)
            if str(filename).endswith('_error.log'):
                constructing.set()
                assert release.wait(5)
            return handler
        backend.RotatingFileHandler = slow_handler
        worker = threading.Thread(target=lambda: setup_plugin_logging('Plugin_demo', own_root=True, log_level=logging.WARNING))
        worker.start()
        try:
            assert constructing.wait(5)
            main.info('during-reconfiguration-marker')
        finally:
            release.set()
            worker.join(5)
        assert not worker.is_alive()
        assert config.log_file.read_text().count('during-reconfiguration-marker') == 1
    ''')


@pytest.mark.parametrize(("service", "target"), [("PluginServer", "_server"), ("Plugin_demo", "demo")])
def test_log_queries_follow_the_active_writer_after_public_reconfiguration(tmp_path, service, target):
    run_scenario(tmp_path, '''
        import os
        from pathlib import Path
        from plugin.logging_config import get_logger, setup_plugin_logging
        from utils.logger_config import setup_logging
        from plugin.server.application.logs import LogService
        _, old = setup_plugin_logging('SERVICE_NAME', own_root=False)
        new_root = old.log_dir.parent / 'new-root'
        os.environ['NEKO_STORAGE_SELECTED_ROOT'] = str(new_root)
        _, actual = setup_logging(service_name='SERVICE_NAME', silent=True)
        get_logger('writer').info('active-writer-marker')
        reader = LogService()
        assert reader.get_plugin_log_directory('TARGET_ID') == actual.log_dir
        assert [row['message'] for row in reader.get_plugin_logs(plugin_id='TARGET_ID').logs] == ['active-writer-marker']
        if 'SERVICE_NAME'.startswith('Plugin_'):
            from plugin.core.plugin_logger import PluginFileLogger
            assert PluginFileLogger('TARGET_ID', Path.cwd()).get_log_file_path() == actual.log_file
        _, retried = setup_plugin_logging('SERVICE_NAME', own_root=False)
        get_logger('writer').info('after-retry-marker')
        assert retried.log_file != old.log_file
        assert reader.get_plugin_log_directory('TARGET_ID') == retried.log_dir
        assert 'after-retry-marker' in retried.log_file.read_text()
    '''.replace('SERVICE_NAME', service).replace('TARGET_ID', target))


def test_concurrent_plugin_initializers_keep_the_final_configuration_coherent(tmp_path):
    run_scenario(tmp_path, '''
        import logging, threading
        from plugin.logging_config import setup_plugin_logging
        from utils.logger_config import get_module_logger
        gate = threading.Barrier(3)
        failures = []
        def configure(level):
            try:
                gate.wait(5)
                setup_plugin_logging('Plugin_demo', own_root=True, log_level=level)
            except BaseException as error:
                failures.append(error)
        threads = [threading.Thread(target=configure, args=(level,))
                   for level in (logging.WARNING, logging.INFO)]
        for thread in threads: thread.start()
        gate.wait(5)
        for thread in threads:
            thread.join(10)
            assert not thread.is_alive()
        assert not failures, failures
        namespace = logging.getLogger('N.E.K.O.Plugin_demo')
        shared = logging.getLogger('N.E.K.O')
        assert namespace.handlers == shared.handlers == logging.getLogger().handlers
        assert namespace.level == shared.level == logging.getLogger().level
        namespace.warning('final-service-marker')
        get_module_logger('contract').warning('final-shared-marker')
        from plugin.logging_config import resolve_plugin_log_directory
        text = '\\n'.join(p.read_text() for p in resolve_plugin_log_directory(log_subdir='plugin').glob('*.log'))
        assert text.count('final-service-marker') == 1
        assert text.count('final-shared-marker') == 1
    ''')


def test_global_format_hook_keeps_host_percent_and_existing_wrappers(tmp_path):
    run_scenario(tmp_path, '''
        import logging
        from utils.logger_config import install_message_formatting
        original = logging.LogRecord.getMessage
        def host_message(record):
            return 'host:' + original(record)
        logging.LogRecord.getMessage = host_message
        install_message_formatting()
        record = logging.LogRecord('host', 20, 'probe.py', 1, 'value=%s {literal}', ('ok',), None)
        assert record.getMessage() == 'host:value=ok {literal}'
        brace = logging.LogRecord('host', 20, 'probe.py', 1, 'value={}', ('ok',), None)
        assert brace.getMessage() == 'value=ok'
    ''')


def test_official_metadata_worker_captures_import_logs(tmp_path):
    run_scenario(tmp_path, '''
        import json, os, subprocess
        from pathlib import Path
        from plugin.server.application.plugins.metadata_scanner import _metadata_worker_command
        fixture = Path(__file__).parent
        (fixture / 'audit_plugin.py').write_text(
            "import logging\\nfrom plugin.logging_config import get_logger\\n"
            "get_logger('metadata').info('metadata-sdk-marker')\\n"
            "logging.getLogger('metadata.library').info('metadata-library-marker')\\n"
            "class Plugin: pass\\n")
        config = fixture / 'plugin.toml'
        config.write_text('[plugin]\\nid="audit"\\n')
        env = dict(os.environ)
        env['PYTHONPATH'] = str(fixture) + os.pathsep + os.getcwd()
        request = dict(plugin_id='audit', module_path='audit_plugin', class_name='Plugin',
                       config_path=str(config), conf={}, pdata={})
        child = subprocess.run(_metadata_worker_command(), input=json.dumps(request) + '\\n',
                               env=env, capture_output=True, text=True, timeout=15)
        assert child.returncode == 0, child.stdout + child.stderr
        packet = next(line for line in child.stdout.splitlines() if line.startswith('NEKO_PLUGIN_METADATA_RESULT:'))
        assert json.loads(packet.split(':', 1)[1])['ok'] is True
        text = '\\n'.join(p.read_text() for p in Path(os.environ['NEKO_STORAGE_SELECTED_ROOT']).rglob('*.log'))
        assert text.count('metadata-sdk-marker') == 1, text
        assert text.count('metadata-library-marker') == 1, text
    ''')


def test_embedded_agent_keeps_httpx_failure_logs(tmp_path):
    run_scenario(tmp_path, '''
        import httpx, logging
        from utils.logger_config import setup_logging
        _, agent = setup_logging(service_name='Agent', silent=True)
        import uvicorn
        uvicorn.Config('unused:app', log_level='error')
        from plugin.server.http_app import build_plugin_server_app
        build_plugin_server_app()
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503))) as client:
            client.get('https://example.invalid/logging-contract')
        from plugin.logging_config import resolve_plugin_log_directory
        text = '\\n'.join(p.read_text() for p in resolve_plugin_log_directory().glob('*.log'))
        assert '503' in text and 'logging-contract' in text
    ''')


def test_standalone_keeps_preconfigured_uvicorn_errors(tmp_path):
    run_scenario(tmp_path, '''
        import logging, logging.config, uvicorn
        logging.config.dictConfig(uvicorn.config.LOGGING_CONFIG)
        from plugin import user_plugin_server
        logging.getLogger('uvicorn.error').error('uvicorn-file-marker')
        assert 'uvicorn-file-marker' in user_plugin_server._log_config.log_file.read_text()
    ''')


def test_public_plugin_file_path_uses_the_attached_day(tmp_path):
    run_scenario(tmp_path, '''
        import os, logging
        from datetime import date, timedelta
        from utils.logger_config import RobustLoggerConfig
        from plugin.core.plugin_logger import PluginFileLogger
        config = RobustLoggerConfig(service_name='Plugin_demo', log_subdir='plugin')
        config.log_file = config.log_dir / f'N.E.K.O_Plugin_demo_{date.today()-timedelta(days=1):%Y%m%d}.log'
        logger = config.setup_logger()
        os.environ['NEKO_PLUGIN_SERVICE_NAME'] = 'Plugin_demo'
        logger.info('yesterday-writer-marker')
        assert PluginFileLogger('demo', config.log_dir).get_log_file_path() == config.log_file
    ''')


@pytest.mark.parametrize("method", multiprocessing.get_all_start_methods())
def test_legacy_sdk_workers_keep_third_party_collection(tmp_path, method):
    run_scenario(tmp_path, '''
        import multiprocessing as mp, os, logging
        from pathlib import Path
        from plugin.logging_config import get_logger, setup_plugin_logging
        def idle():
            pass
        def worker():
            os.environ['NEKO_PLUGIN_SERVICE_NAME'] = 'Plugin_demo'
            get_logger('legacy-worker').info('legacy-worker-sdk-marker')
            logging.getLogger('worker.library').info('legacy-worker-library-marker')
        if __name__ == '__main__':
            context = mp.get_context('METHOD')
            if 'METHOD' == 'forkserver':
                warm = context.Process(target=idle)
                warm.start()
                warm.join(10)
                assert warm.exitcode == 0
            setup_plugin_logging('Plugin_demo', own_root=True)
            child = context.Process(target=worker)
            child.start()
            child.join(10)
            if child.is_alive():
                child.terminate()
                child.join(5)
                raise AssertionError('worker did not exit')
            assert child.exitcode == 0
            text = '\\n'.join(p.read_text() for p in Path(os.environ['NEKO_STORAGE_SELECTED_ROOT']).rglob('*.log'))
            assert text.count('legacy-worker-sdk-marker') == 1
            assert text.count('legacy-worker-library-marker') == 1
    '''.replace('METHOD', method))


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires fork")
def test_child_logging_does_not_inherit_a_locked_runtime(tmp_path):
    run_scenario(tmp_path, '''
        import multiprocessing as mp, threading
        from plugin import logging_config as logging_runtime
        ready = threading.Event()
        release = threading.Event()
        def hold_parent_lock():
            with logging_runtime._process_lock():
                ready.set()
                release.wait(15)
        def worker():
            logging_runtime.setup_plugin_logging('Plugin_demo', own_root=True)
            logging_runtime.get_logger('child').info('fork-while-locked-marker')
        if __name__ == '__main__':
            logging_runtime.setup_plugin_logging('PluginServer', own_root=True)
            thread = threading.Thread(target=hold_parent_lock)
            thread.start()
            child = mp.get_context('fork').Process(target=worker)
            try:
                assert ready.wait(5)
                child.start()
                child.join(10)
                assert not child.is_alive(), 'child inherited parent runtime lock'
                assert child.exitcode == 0
            finally:
                if child.is_alive():
                    child.terminate()
                    child.join(5)
                release.set()
                thread.join(5)
    ''')


@pytest.mark.parametrize("host_first", [True, False])
def test_compatibility_bridge_yields_to_host_file_output(tmp_path, host_first):
    run_scenario(tmp_path, '''
        import logging, os
        from pathlib import Path
        from plugin.server.http_app import build_plugin_server_app
        root = logging.getLogger()
        host_file = Path(os.environ['NEKO_STORAGE_SELECTED_ROOT']) / 'host-owned.log'
        host_file.parent.mkdir(parents=True, exist_ok=True)
        if not HOST_FIRST:
            build_plugin_server_app()
        foreign = logging.FileHandler(host_file)
        root.addHandler(foreign)
        root.setLevel(logging.INFO)
        # A late host must take precedence even before any further app call.
        logging.getLogger('library').error('host-before-factory-marker')
        build_plugin_server_app()
        logging.getLogger('library').error('host-after-factory-marker')
        assert host_file.read_text().count('host-before-factory-marker') == 1
        assert host_file.read_text().count('host-after-factory-marker') == 1
        text = '\\n'.join(p.read_text() for p in host_file.parent.rglob('N.E.K.O_*.log'))
        assert 'host-before-factory-marker' not in text
        assert 'host-after-factory-marker' not in text
    '''.replace('HOST_FIRST', repr(host_first)))


def test_compatibility_bridge_follows_public_writer_reconfiguration(tmp_path):
    run_scenario(tmp_path, '''
        import logging, os
        from pathlib import Path
        from plugin.server.http_app import build_plugin_server_app
        from utils.logger_config import setup_logging
        build_plugin_server_app()
        old_root = Path(os.environ['NEKO_STORAGE_SELECTED_ROOT'])
        os.environ['NEKO_STORAGE_SELECTED_ROOT'] = str(old_root / 'new')
        _, actual = setup_logging(service_name='PluginServer', silent=True)
        logging.getLogger('library').error('new-writer-library-marker')
        assert actual.log_file.read_text().count('new-writer-library-marker') == 1
        assert 'new-writer-library-marker' not in '\\n'.join(p.read_text() for p in (old_root / 'logs').glob('*.log'))
    ''')



def test_compatibility_bridge_does_not_deadlock_standard_reconfiguration(tmp_path):
    run_scenario(tmp_path, '''
        import logging, logging.config, threading, os, faulthandler
        from plugin.logging_config import get_logger
        get_logger('setup').info('initialize-sdk-bridge')
        bridge = logging.getLogger()._neko_sdk_bridge
        entered = threading.Event()
        configuring = threading.Event()
        original_emit = bridge.emit
        failures = []
        def gated_emit(record):
            entered.set()
            assert configuring.wait(5)
            original_emit(record)
        bridge.emit = gated_emit
        def write():
            try:
                logging.getLogger('library').error('concurrent-config-marker')
            except BaseException as error:
                failures.append(error)
        def configure():
            try:
                with logging._lock:
                    configuring.set()
                    logging.config.dictConfig({'version': 1, 'disable_existing_loggers': False})
            except BaseException as error:
                failures.append(error)
        writer = threading.Thread(target=write, daemon=True)
        writer.start()
        assert entered.wait(5)
        configurer = threading.Thread(target=configure, daemon=True)
        configurer.start()
        writer.join(5)
        configurer.join(5)
        if writer.is_alive() or configurer.is_alive():
            faulthandler.dump_traceback()
            os._exit(3)
        assert not failures, failures
    ''')
