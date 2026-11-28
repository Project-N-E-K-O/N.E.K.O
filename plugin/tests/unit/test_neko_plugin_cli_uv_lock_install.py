from __future__ import annotations

from pathlib import Path

from packaging.tags import Tag
import pytest

from plugin.neko_plugin_cli.core import uv_lock_install as lock_install

pytestmark = pytest.mark.plugin_unit


def _plugin(tmp_path: Path, *, lock: str | None = "version = 1\n", vendor: str | None = None) -> Path:
    plugin_dir = tmp_path / "demo"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    if lock is not None:
        (plugin_dir / "uv.lock").write_text(lock, encoding="utf-8")
    if vendor == "shipped":
        (plugin_dir / "vendor").mkdir()
        (plugin_dir / "vendor" / "dep.py").write_text("", encoding="utf-8")
    elif vendor == "locked":
        (plugin_dir / "vendor").mkdir()
        lock_install.write_lock_marker(plugin_dir / "vendor", lock_install.lock_sha256(plugin_dir))
    return plugin_dir


# ---------------------------------------------------------------------------
# Commands


def test_export_command_reads_the_lock_as_is_without_plugin_config(tmp_path):
    command = lock_install.uv_export_command("uv", tmp_path)

    assert command[:2] == ["uv", "export"]
    for switch in (
        "--no-config", "--frozen", "--no-default-groups", "--no-emit-project",
        "--no-emit-workspace", "--no-python-downloads",
    ):
        assert switch in command
    assert command[command.index("--format") + 1] == "requirements.txt"
    assert command[command.index("--directory") + 1] == str(tmp_path)
    # Hashes are what --require-hashes checks; local sources must stay in the
    # list so that check rejects them instead of skipping them.
    assert "--no-hashes" not in command
    assert "--no-emit-local" not in command
    assert "--no-dev" not in command  # --no-default-groups covers every default group


def test_install_command_carries_every_safety_switch(tmp_path):
    requirements, target = tmp_path / "req.txt", tmp_path / ".vendor.staging-0123abcd"
    command = lock_install.uv_install_command("uv", "/py/python", requirements, target)

    assert command[:3] == ["uv", "pip", "install"]
    for switch in ("--no-config", "--no-python-downloads", "--require-hashes", "--no-deps"):
        assert switch in command
    assert command[command.index("--only-binary") + 1] == ":all:"
    assert command[command.index("--python") + 1] == "/py/python"
    assert command[command.index("--target") + 1] == str(target)
    assert command[command.index("-r") + 1] == str(requirements)
    assert command[command.index("--link-mode") + 1] == "copy"
    # The platform comes from the interpreter; an explicit one would override uv's detection.
    assert "--python-platform" not in command
    assert "--dry-run" not in command


def test_dry_run_install_command_differs_only_by_dry_run(tmp_path):
    args = ("uv", "/py/python", tmp_path / "req.txt", tmp_path / "t")
    real = lock_install.uv_install_command(*args)
    dry = lock_install.uv_install_command(*args, dry_run=True)

    assert [part for part in dry if part != "--dry-run"] == real
    assert dry.count("--dry-run") == 1


def test_install_env_sets_one_index_and_drops_inherited_ones():
    base = {
        "PATH": "/bin", "UV_INDEX_URL": "https://a.invalid", "uv_extra_index_url": "https://b.invalid",
        "UV_DEFAULT_INDEX": "https://c.invalid", "PIP_INDEX_URL": "https://d.invalid", "UV_CACHE_DIR": "/c",
    }

    env = lock_install.lock_install_env(base, "https://pypi.org/simple")

    assert env["UV_DEFAULT_INDEX"] == "https://pypi.org/simple"
    assert env["PATH"] == "/bin" and env["UV_CACHE_DIR"] == "/c"
    for name in ("UV_INDEX_URL", "uv_extra_index_url", "PIP_INDEX_URL"):
        assert name not in env
    assert base["UV_DEFAULT_INDEX"] == "https://c.invalid"  # not mutated


# ---------------------------------------------------------------------------
# Confirmation list


def test_parse_install_plan_reads_uv_package_lines():
    output = (
        "Using CPython 3.11.18 interpreter at: /py\nResolved 3 packages in 5ms\nWould install 3 packages\n"
        " + anyio==4.16.0\n + Typing_Extensions==4.16.0\n + orjson==3.13.0\n + anyio==4.16.0\n"
    )

    assert lock_install.parse_install_plan(output) == [
        ("anyio", "4.16.0"), ("typing-extensions", "4.16.0"), ("orjson", "3.13.0"),
    ]


_LOCK = {
    "package": [
        {
            "name": "orjson", "version": "3.13.0",
            "wheels": [
                {"url": "https://files/orjson-3.13.0-cp311-cp311-macosx_10_15_universal2.whl", "size": 223146},
                {"url": "https://files/orjson-3.13.0-cp311-cp311-manylinux_2_17_x86_64.whl", "size": 130518},
                {"url": "https://files/orjson-3.13.0-cp311-cp311-win_amd64.whl", "size": 121287},
            ],
        },
        {
            "name": "idna", "version": "3.20",
            "wheels": [{"url": "https://files/idna-3.20-py3-none-any.whl", "size": 69000}],
        },
        {"name": "nosize", "version": "1.0", "wheels": [{"url": "https://files/nosize-1.0-py3-none-any.whl"}]},
    ],
}


def test_sizes_come_from_the_wheel_this_host_gets():
    tags = [Tag("cp311", "cp311", "manylinux_2_17_x86_64"), Tag("py3", "none", "any")]
    packages = lock_install.locked_wheel_sizes(
        _LOCK, [("orjson", "3.13.0"), ("idna", "3.20"), ("nosize", "1.0"), ("absent", "2.0")], tags=tags,
    )

    assert [(p.name, p.version, p.size) for p in packages] == [
        ("orjson", "3.13.0", 130518),  # not the universal2 wheel uv tree reports
        ("idna", "3.20", 69000),
        ("nosize", "1.0", None),
        ("absent", "2.0", None),
    ]


def test_sizes_follow_the_host_platform():
    windows = [Tag("cp311", "cp311", "win_amd64"), Tag("py3", "none", "any")]
    (orjson,) = lock_install.locked_wheel_sizes(_LOCK, [("orjson", "3.13.0")], tags=windows)
    assert orjson.size == 121287


# ---------------------------------------------------------------------------
# Marker, trigger and carry-over decisions


def test_marker_round_trip_and_garbage(tmp_path):
    lock_install.write_lock_marker(tmp_path, "a" * 64)
    assert lock_install.read_lock_marker(tmp_path) == "a" * 64

    (tmp_path / lock_install.LOCK_MARKER_NAME).write_text('{"lock_sha256": "nope"}', encoding="utf-8")
    assert lock_install.read_lock_marker(tmp_path) is None
    (tmp_path / lock_install.LOCK_MARKER_NAME).write_text("not json", encoding="utf-8")
    assert lock_install.read_lock_marker(tmp_path) is None


@pytest.mark.parametrize(
    ("lock", "vendor", "applies"),
    [
        ("version = 1\n", None, True),       # lock, nothing vendored yet
        ("version = 1\n", "locked", True),   # vendor/ the host installed from the lock
        ("version = 1\n", "shipped", False),  # vendor/ shipped by the package: unchanged behavior
        (None, None, False),                 # no lock: never triggers
    ],
)
def test_lock_install_applies(tmp_path, lock, vendor, applies):
    assert lock_install.lock_install_applies(_plugin(tmp_path, lock=lock, vendor=vendor)) is applies


def test_vendor_carries_over_only_when_installed_from_the_same_lock(tmp_path):
    old = _plugin(tmp_path / "old", vendor="locked")
    same = _plugin(tmp_path / "same")
    changed = _plugin(tmp_path / "changed", lock="version = 1\n# changed\n")

    assert lock_install.vendor_matches_lock(old, same) is True
    assert lock_install.vendor_matches_lock(old, changed) is False


def test_hand_copied_vendor_never_counts_as_an_unchanged_lock(tmp_path):
    # Same uv.lock on both sides, but the old vendor/ has no marker.
    old = _plugin(tmp_path / "old", vendor="shipped")
    new = _plugin(tmp_path / "new")

    assert lock_install.vendor_matches_lock(old, new) is False


def test_new_package_with_its_own_vendor_is_left_alone(tmp_path):
    old = _plugin(tmp_path / "old", vendor="locked")
    new = _plugin(tmp_path / "new", vendor="shipped")

    assert lock_install.vendor_matches_lock(old, new) is False


def test_new_package_without_lock_does_not_inherit_vendor(tmp_path):
    old = _plugin(tmp_path / "old", vendor="locked")
    new = _plugin(tmp_path / "new", lock=None)

    assert lock_install.vendor_matches_lock(old, new) is False


def test_vendor_installed_for_another_python_is_not_carried_over(tmp_path):
    # The host's Python or platform changed: compiled wheels in the old vendor/ may not load.
    old = _plugin(tmp_path / "old")
    (old / "vendor").mkdir()
    lock_install.write_lock_marker(old / "vendor", lock_install.lock_sha256(old), python="cp310-win-amd64")
    new = _plugin(tmp_path / "new")

    assert lock_install.read_lock_marker(old / "vendor") is not None
    assert lock_install.vendor_matches_lock(old, new) is (lock_install.runtime_python_tag() == "cp310-win-amd64")


def test_empty_vendor_counts_as_absent_and_requirements_txt_opts_out(tmp_path):
    plugin = _plugin(tmp_path / "a")
    (plugin / "vendor").mkdir()
    assert lock_install.lock_install_applies(plugin) is True
    (plugin / "requirements.txt").write_text("x\n", encoding="utf-8")
    assert lock_install.lock_install_applies(plugin) is False


def test_unchanged_lock_carries_over_into_an_empty_shipped_vendor_dir(tmp_path):
    old = _plugin(tmp_path / "old", vendor="locked")
    new = _plugin(tmp_path / "new")
    (new / "vendor").mkdir()
    assert lock_install.vendor_matches_lock(old, new) is True


def test_lock_vendor_stale_compares_the_recorded_python(tmp_path):
    plugin = _plugin(tmp_path, vendor="locked")
    assert lock_install.lock_vendor_stale(plugin) is False
    lock_install.write_lock_marker(plugin / "vendor", "a" * 64, python="cp27-plan9")
    assert lock_install.lock_vendor_stale(plugin) is True
    assert lock_install.lock_vendor_stale(_plugin(tmp_path / "shipped", vendor="shipped")) is False


def test_unpinned_requirements_flags_everything_not_from_the_index():
    exported = (
        "anyio==4.16.0 \\\n    --hash=sha256:aa\n"
        "colorama==0.4.6 ; sys_platform == 'win32' \\\n    --hash=sha256:bb\n"
        "uvicorn[standard]==0.30.0 \\\n    --hash=sha256:cc\n"
        "evil @ https://10.0.0.1/x.whl \\\n    --hash=sha256:dd\n"
        "libx @ file:///tmp/libx\n"
        "-e ./local\n"
        "./other\n"
    )
    assert lock_install.unpinned_requirements(exported) == [
        "evil @ https://10.0.0.1/x.whl", "libx @ file:///tmp/libx", "-e ./local", "./other",
    ]


def test_unpinned_requirements_does_not_trust_hash_lines_or_url_versions():
    assert lock_install.unpinned_requirements(
        "a==1.0+cpu \\\n    --hash=sha256:aa \\\n    --hash=sha256:bb\nb==1!2.0\n"
        "--hash=sha256:aa --index-url http://e\nc==http://evil/x\n"
    ) == ["--hash=sha256:aa --index-url http://e", "c==http://evil/x"]
