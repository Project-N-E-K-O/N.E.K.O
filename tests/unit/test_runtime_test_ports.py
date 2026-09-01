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


@pytest.fixture()
def all_slots_bindable(monkeypatch):
    """Take the host's port occupancy out of the picture.

    ``_xdist_band_port`` returns None when its slot is already in use so the
    caller can fall back to probing -- correct behaviour, but it makes any
    assertion about the slot depend on what else happens to be listening on this
    machine. These tests are about the arithmetic that gives two workers
    different ports, so the availability check is stubbed out and the fallback
    gets its own test below (Codex caught this on #3022).
    """
    monkeypatch.setattr(project_conftest, "_port_is_bindable", lambda port: True)


@pytest.mark.unit
def test_no_worker_candidate_ever_aliases_another_workers():
    """Exhaustive over the whole scheme, not a sample of it.

    Enumerates `_xdist_band_candidates` -- the real function -- rather than
    recomputing the formula here. A test that re-derives the arithmetic agrees
    with the implementation by construction, which is how the first version of
    this scheme shipped with gw64 attempt 0 sitting on gw0 attempt 1: the span
    allowed 64 workers and the check I wrote iterated range(64), so the two
    assumptions matched each other and nothing else.

    Every retry candidate counts, not just the first: the retries are exactly
    where an occupied slot sends a worker, so an alias there is an alias that
    only appears under load.
    """
    seen: dict[int, tuple[int, int]] = {}
    total = 0
    for index in range(project_conftest._XDIST_PORT_MAX_WORKERS):
        for offset in range(len(project_conftest._RUNTIME_TEST_PORT_SLOTS)):
            for port in project_conftest._xdist_band_candidates(index, offset):
                total += 1
                owner = seen.get(port)
                assert owner is None or owner[0] == index, (
                    f"port {port} is a candidate for both gw{owner[0]} and gw{index}"
                )
                seen[port] = (index, offset)
    assert total > 0
    assert max(seen) < 49152, (
        f"candidate {max(seen)} sits in the Windows ephemeral range, where bind(0) "
        "could hand it to an unrelated process"
    )


@pytest.mark.unit
def test_worker_above_the_cap_gets_no_slot_rather_than_an_aliased_one():
    """The boundary the previous version silently crossed."""
    cap = project_conftest._XDIST_PORT_MAX_WORKERS

    assert project_conftest._xdist_band_candidates(cap - 1, 0) != []
    assert project_conftest._xdist_band_candidates(cap, 0) == []
    assert project_conftest._xdist_band_candidates(cap + 1, 0) == []


@pytest.mark.unit
def test_every_worker_gets_more_than_one_candidate_to_retry_into(
    monkeypatch, isolated_runtime_test_ports
):
    """An occupied slot must have somewhere worker-unique left to go."""
    assert len(project_conftest._xdist_band_candidates(0, 0)) > 1

    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw5")
    monkeypatch.delenv("NEKO_MEMORY_SERVER_PORT", raising=False)
    first, second = project_conftest._xdist_band_candidates(5, 0)[:2]
    monkeypatch.setattr(
        project_conftest, "_port_is_bindable", lambda port: port != first
    )

    assert project_conftest._xdist_band_port("MEMORY_SERVER_PORT") == second


@pytest.mark.unit
def test_the_two_port_slots_do_not_collide_within_a_worker(
    monkeypatch, isolated_runtime_test_ports, all_slots_bindable
):
    """Dual of the above: distinct workers differ, and so do the two ports one worker owns."""
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw3")

    memory = project_conftest._xdist_band_port("MEMORY_SERVER_PORT")
    main = project_conftest._xdist_band_port("MAIN_SERVER_PORT")

    assert memory != main


@pytest.mark.unit
def test_worker_band_is_below_the_ephemeral_range(
    monkeypatch, isolated_runtime_test_ports, all_slots_bindable
):
    """A slot inside the ephemeral range could be handed to an unrelated process by bind(0)."""
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw31")

    for port_name in ("MEMORY_SERVER_PORT", "MAIN_SERVER_PORT"):
        port = project_conftest._xdist_band_port(port_name)
        assert port < 49152, f"{port_name} slot {port} sits in the Windows ephemeral range"


@pytest.mark.unit
def test_an_occupied_slot_falls_back_to_probing(monkeypatch, isolated_runtime_test_ports):
    """The fallback the tests above stub out: an occupied slot must not be handed on."""
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw3")
    monkeypatch.setattr(project_conftest, "_port_is_bindable", lambda port: False)

    assert project_conftest._xdist_band_port("MEMORY_SERVER_PORT") is None


@pytest.mark.unit
def test_non_xdist_run_has_no_band_and_falls_back_to_probing(
    monkeypatch, isolated_runtime_test_ports, all_slots_bindable
):
    """Outside xdist there is no worker index, so the allocator must keep probing."""
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)

    assert project_conftest._xdist_band_port("MEMORY_SERVER_PORT") is None
