"""Shared pytest configuration for manual integration tests."""
import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--run-manual",
        action="store_true",
        default=False,
        help="run manual integration tests (real API calls, screen/browser control)",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "manual: requires human supervision and real API/screen/browser")


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-manual", default=False):
        skip = pytest.mark.skip(reason="needs --run-manual to run")
        for item in items:
            if "manual" in item.keywords:
                item.add_marker(skip)
