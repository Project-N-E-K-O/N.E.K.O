import conftest as project_conftest
import pytest


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
