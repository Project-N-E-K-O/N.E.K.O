"""Market launch expiry and verified downloads, with no user data or network."""

import hashlib
import json
import threading
from types import SimpleNamespace

import pytest

from services.model_workshop import market
from services.model_workshop.paths import WorkshopError
from tests.fake_clock import patch_module_clock


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "characters.json"
    data = {"当前猫娘": "A", "猫娘": {
        "A": {"_reserved": {"character_id": "a" * 32}},
        "B": {"_reserved": {"character_id": "b" * 32}},
    }}
    path.write_text(json.dumps(data))
    return SimpleNamespace(app_docs_dir=tmp_path,
                           get_runtime_config_path=lambda _: path,
                           load_characters=lambda **_: json.loads(path.read_text()))


def test_switching_away_and_back_permanently_expires_application(config):
    row = market.Launches().create(config)
    path = config.get_runtime_config_path("characters.json")
    original = path.read_bytes()
    data = json.loads(original); data["当前猫娘"] = "B"
    path.write_text(json.dumps(data)); path.write_bytes(original)
    with pytest.raises(WorkshopError, match="target_changed"):
        row.check(config)


def test_new_root_or_same_name_new_identity_cannot_inherit_launch(config, tmp_path):
    row = market.Launches().create(config)
    data = config.load_characters()
    data["猫娘"]["A"]["_reserved"]["character_id"] = "c" * 32
    with pytest.raises(WorkshopError, match="target_changed"):
        row.check(config, data)
    config.app_docs_dir = tmp_path / "moved"
    with pytest.raises(WorkshopError, match="target_changed"):
        row.check(config)


def test_missing_role_file_fails_closed_without_a_filesystem_error(config):
    row = market.Launches().create(config)
    config.get_runtime_config_path('characters.json').unlink()
    with pytest.raises(WorkshopError, match='target_changed'):
        row.check(config)


def test_selection_is_idempotent_and_launch_cannot_be_retargeted(config):
    launches = market.Launches(); row = launches.create(config)
    assert launches.select(config, row.request_id, 1, "a" * 64, 100) == (row, True)
    assert launches.select(config, row.request_id, 1, "a" * 64, 100) == (row, False)
    with pytest.raises(WorkshopError, match="selection_changed"):
        launches.select(config, row.request_id, 2, "a" * 64, 100)


@pytest.mark.parametrize("model_id,digest,size", [(True, "a" * 64, 1), (0, "a" * 64, 1),
    (1, "../escape", 1), (1, "a" * 64, True), (1, "a" * 64, 0),
    (1, "a" * 64, market.BundleLimits().archive_bytes + 1)])
def test_bad_selection_rejected_before_job(config, model_id, digest, size):
    launches = market.Launches(); row = launches.create(config)
    with pytest.raises(WorkshopError, match="invalid_selection"):
        launches.select(config, row.request_id, model_id, digest, size)
    assert row.phase == "browsing" and row.selection is None


def test_launch_and_active_limits_are_bounded(config, monkeypatch):
    launches = market.Launches()
    rows = [launches.create(config) for _ in range(market.MAX_LAUNCHES)]
    with pytest.raises(WorkshopError, match="busy"):
        launches.create(config)
    for row in rows[:2]: launches.select(config, row.request_id, 1, "a" * 64, 1)
    with pytest.raises(WorkshopError, match="busy"):
        launches.select(config, rows[2].request_id, 1, "a" * 64, 1)
    patch_module_clock(monkeypatch, market, monotonic=lambda: rows[0].expires + 1)
    with pytest.raises(WorkshopError, match="expired"):
        rows[0].check(config)


@pytest.mark.parametrize("chunks,size,digest", [([b"abc"], 4, hashlib.sha256(b"abc").hexdigest()),
    ([b"abc"], 3, "a" * 64), ([b"abcd"], 3, hashlib.sha256(b"abc").hexdigest())])
def test_bad_network_body_never_returns_eof(chunks, size, digest):
    stream = market.PackageStream(chunks, digest, size, threading.Event())
    with pytest.raises(WorkshopError, match="package_changed"):
        while stream.read(1024 * 1024): pass


def test_stream_cancel_deadline_and_success(monkeypatch):
    cancelled = threading.Event()
    stream = market.PackageStream([b"abc"], hashlib.sha256(b"abc").hexdigest(), 3, cancelled)
    assert stream.read(1024) == b"abc"
    assert stream.read(1024) == b"" and stream.read(1024) == b""
    cancelled.set()
    with pytest.raises(WorkshopError, match="cancelled"): stream.read(1024)
    cancelled.clear()
    patch_module_clock(monkeypatch, market, monotonic=lambda: stream.deadline + 1)
    with pytest.raises(WorkshopError, match="timeout"): stream.read(1024)
