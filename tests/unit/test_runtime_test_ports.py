import importlib.util
import sys
from pathlib import Path

import pytest


def _load_root_conftest():
    """Resolve `tests/conftest.py` (the bare name `conftest` would shadow this
    file with `tests/unit/conftest.py`). Reuse the module pytest already loaded
    when possible to avoid re-running its module-level side effects."""
    root_conftest_path = Path(__file__).resolve().parents[1] / "conftest.py"
    target = root_conftest_path.resolve()
    for module in sys.modules.values():
        module_file = getattr(module, "__file__", None)
        if module_file and Path(module_file).resolve() == target:
            return module
    spec = importlib.util.spec_from_file_location(
        "_tests_root_conftest", root_conftest_path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


project_conftest = _load_root_conftest()


@pytest.fixture(scope="session", autouse=True)
def mock_memory_server():
    """Override the repo-level autouse fixture for isolated helper tests."""
    yield


@pytest.fixture()
def isolated_runtime_test_ports(monkeypatch):
    original_ports = dict(project_conftest._RUNTIME_TEST_PORTS)
    monkeypatch.delenv("NEKO_MEMORY_SERVER_PORT", raising=False)
    monkeypatch.delenv("NEKO_MAIN_SERVER_PORT", raising=False)
    project_conftest._RUNTIME_TEST_PORTS.clear()

    try:
        yield
    finally:
        project_conftest._RUNTIME_TEST_PORTS.clear()
        project_conftest._RUNTIME_TEST_PORTS.update(original_ports)


@pytest.mark.unit
def test_initialize_runtime_test_ports_replaces_duplicate_second_port(monkeypatch, isolated_runtime_test_ports):
    resolved_ports = iter((43101, 43101))
    fallback_ports = iter((43102,))
    assigned_ports = []

    monkeypatch.setattr(
        project_conftest,
        "_resolve_runtime_test_port",
        lambda port_name: next(resolved_ports),
    )
    monkeypatch.setattr(
        project_conftest,
        "_find_free_local_port",
        lambda: next(fallback_ports),
    )
    monkeypatch.setattr(
        project_conftest,
        "_set_runtime_test_port",
        lambda port_name, port_value: assigned_ports.append((port_name, port_value)),
    )

    project_conftest._initialize_runtime_test_ports()

    assert project_conftest._RUNTIME_TEST_PORTS == {
        "MEMORY_SERVER_PORT": 43101,
        "MAIN_SERVER_PORT": 43102,
    }
    assert assigned_ports == [
        ("MEMORY_SERVER_PORT", 43101),
        ("MAIN_SERVER_PORT", 43102),
    ]


@pytest.mark.unit
def test_initialize_runtime_test_ports_raises_when_unique_port_cannot_be_found(
    monkeypatch,
    isolated_runtime_test_ports,
):
    resolved_ports = iter((43201, 43201))

    monkeypatch.setattr(
        project_conftest,
        "_resolve_runtime_test_port",
        lambda port_name: next(resolved_ports),
    )
    monkeypatch.setattr(project_conftest, "_find_free_local_port", lambda: 43201)
    monkeypatch.setattr(project_conftest, "_set_runtime_test_port", lambda port_name, port_value: None)
    monkeypatch.setattr(project_conftest, "_RUNTIME_TEST_PORT_RETRY_LIMIT", 2)

    with pytest.raises(RuntimeError, match="Unable to allocate unique runtime test port"):
        project_conftest._initialize_runtime_test_ports()

    assert project_conftest._RUNTIME_TEST_PORTS == {
        "MEMORY_SERVER_PORT": 43201,
    }


# ── xdist worker 端口隔离（对偶：worker 忽略继承值 / 单进程仍尊重显式 pin） ──
#
# unit-tests.yml 用 `-n auto` 跑这套用例，而 xdist controller 在收集阶段就会
# import tests/conftest.py，分配结果落进 controller 自己的 os.environ，execnet
# 再把整份环境复制给每个 worker——于是所有 worker 解析出同一个端口，而本目录里
# 十二个用到 mock_memory_server 的文件都会在这个端口上起 uvicorn。Windows 下
# SO_REUSEADDR 让第二次 bind 成功而不是失败，所以冲突完全不报错：readiness 探针
# 被先起来的那个服务应答，套件照绿，只是某个 worker 在跟别人的服务说话。
#
# 两条一起才说明规则是什么，缺一条都能被"永远忽略 env"或"永远尊重 env"蒙混过去。


@pytest.mark.unit
def test_xdist_worker_ignores_the_port_it_inherited(monkeypatch, isolated_runtime_test_ports):
    monkeypatch.setenv("NEKO_MEMORY_SERVER_PORT", "13479")
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw3")

    resolved = project_conftest._resolve_runtime_test_port("MEMORY_SERVER_PORT")

    assert resolved != 13479, (
        "xdist worker 复用了 controller 的端口，所有 worker 会 bind 同一个地址"
    )
    assert 1 <= resolved <= 65535


@pytest.mark.unit
def test_single_process_run_still_honours_an_explicit_pin(monkeypatch, isolated_runtime_test_ports):
    monkeypatch.setenv("NEKO_MEMORY_SERVER_PORT", "13479")
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)

    assert project_conftest._resolve_runtime_test_port("MEMORY_SERVER_PORT") == 13479


@pytest.mark.unit
def test_two_xdist_workers_resolve_different_ports(monkeypatch, isolated_runtime_test_ports):
    monkeypatch.setenv("NEKO_MEMORY_SERVER_PORT", "13479")

    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
    first = project_conftest._resolve_runtime_test_port("MEMORY_SERVER_PORT")
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw1")
    second = project_conftest._resolve_runtime_test_port("MEMORY_SERVER_PORT")

    assert first != second
