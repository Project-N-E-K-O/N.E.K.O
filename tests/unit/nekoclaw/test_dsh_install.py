"""Getting dsh onto the disk and off it again. Nothing is downloaded: the release file is made up here."""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest

from nekoclaw.dsh import install as install_module
from nekoclaw.dsh.install import INSTALLED_FILE, DshInstallation, executable_name, platform_tag
from nekoclaw.dsh.profile import DshUnavailable

TAG = "win-x64" if sys.platform == "win32" else "linux-x64"
PROGRAM = b"#!/bin/sh\necho not really dsh\n" * 400


def _release(extra: dict[str, bytes] | None = None, *, program: bool = True) -> bytes:
    """A release file laid out as dsh's is: the program below one directory, other things beside it."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        prefix = "deepseek_harness_runtime/runtime/"
        if program:
            bundle.writestr(prefix + executable_name(TAG), PROGRAM)
        bundle.writestr(prefix + executable_name(TAG).replace(TAG, f"{TAG}-rg"), b"ripgrep")
        bundle.writestr(prefix + f"{TAG}/downloads.json", b"{}")
        bundle.writestr("deepseek_harness_runtime/__init__.py", b"# not unpacked")
        bundle.writestr("deepseek_harness_runtime_bin-0.dist-info/RECORD", b"")
        for name, data in (extra or {}).items():
            bundle.writestr(name, data)
    return buffer.getvalue()


@pytest.fixture
def release(monkeypatch: pytest.MonkeyPatch):
    """Point the settings at a made-up release for this platform; returns how to change its bytes."""
    from config import nekoclaw_settings

    monkeypatch.setattr(install_module, "platform_tag", lambda: TAG)
    monkeypatch.setattr(nekoclaw_settings, "DSH_DOWNLOAD_HOSTS", ("https://first.test/packages", "https://second.test/packages"))

    def use(data: bytes) -> bytes:
        wheel = {"file": "dsh-test.whl", "path": "aa/bb/cc", "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
        monkeypatch.setattr(nekoclaw_settings, "DSH_WHEELS", {TAG: wheel})
        return data

    return use


def _serving(answers: dict[str, Any], seen: list[str]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        answer = answers[request.url.host]
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, int):
            return httpx.Response(answer)
        if isinstance(answer, httpx.AsyncByteStream):
            return httpx.Response(200, stream=answer)
        return httpx.Response(200, content=answer)

    return httpx.MockTransport(handler)


async def test_the_program_is_downloaded_checked_and_unpacked(tmp_path: Path, release) -> None:
    data = release(_release())
    seen: list[str] = []
    progress: list[tuple[int, int]] = []
    dsh = DshInstallation(tmp_path / "dsh", transport=_serving({"first.test": data}, seen))
    assert not dsh.installed() and dsh.status()["installed"] is False
    with pytest.raises(DshUnavailable):
        dsh.launch()

    status = await dsh.install(lambda done, total: progress.append((done, total)))

    assert seen == ["https://first.test/packages/aa/bb/cc/dsh-test.whl"]
    assert status["installed"] is True and status["in_use"] is True and status["downloading"] is False
    assert progress[-1] == (len(data), len(data))
    directory = dsh.runtime_dir()
    # Only the program's directory is unpacked.
    assert sorted(path.relative_to(directory).as_posix() for path in directory.rglob("*") if path.is_file()) == sorted(
        [executable_name(TAG), executable_name(TAG).replace(TAG, f"{TAG}-rg"), f"{TAG}/downloads.json", INSTALLED_FILE]
    )
    assert (directory / executable_name(TAG)).read_bytes() == PROGRAM
    noted = json.loads((directory / INSTALLED_FILE).read_text(encoding="utf-8"))
    assert noted["sha256"] == hashlib.sha256(data).hexdigest() and noted["platform"] == TAG
    # The download is gone, and launching names the program and dsh's home.
    assert list((tmp_path / "dsh" / "download").iterdir()) == []
    launch = dsh.launch()
    assert launch.executable == directory / executable_name(TAG) and launch.home == dsh.home() and launch.home.is_dir()
    if sys.platform != "win32":
        assert os.access(launch.executable, os.X_OK)


async def test_installing_again_downloads_nothing(tmp_path: Path, release) -> None:
    data = release(_release())
    seen: list[str] = []
    dsh = DshInstallation(tmp_path / "dsh", transport=_serving({"first.test": data}, seen))
    await dsh.install()
    await dsh.install()
    assert len(seen) == 1


async def test_a_host_that_fails_is_followed_by_the_next(tmp_path: Path, release) -> None:
    data = release(_release())
    seen: list[str] = []
    dsh = DshInstallation(tmp_path / "dsh", transport=_serving({"first.test": httpx.ConnectTimeout("slow"), "second.test": data}, seen))
    await dsh.install()
    assert [httpx.URL(url).host for url in seen] == ["first.test", "second.test"] and dsh.installed()


@pytest.mark.parametrize("tampered", [b"altered", b"x"])
async def test_a_file_that_is_not_the_one_recorded_is_never_unpacked(tmp_path: Path, release, tampered: bytes) -> None:
    data = release(_release())
    # Same length with other bytes, or another length: neither is the file that was verified.
    wrong = data[: -len(tampered)] + tampered if tampered == b"altered" else data + tampered
    seen: list[str] = []
    dsh = DshInstallation(tmp_path / "dsh", transport=_serving({"first.test": wrong, "second.test": 404}, seen))

    with pytest.raises(DshUnavailable) as failure:
        await dsh.install()

    assert "not the one expected" in str(failure.value) and "HTTP 404" in str(failure.value)
    assert not dsh.installed() and not dsh.runtime_dir().exists()
    assert list((tmp_path / "dsh" / "download").iterdir()) == []
    assert dsh.status()["last_error"] == str(failure.value)
    with pytest.raises(DshUnavailable):
        dsh.launch()


def test_what_dsh_holds_of_the_sessions_a_directory_had_before_is_removed_with_the_rest(tmp_path: Path) -> None:
    from nekoclaw.dsh.driver import SESSION_FILE

    dsh = DshInstallation(tmp_path / "dsh")
    earlier = "11111111-2222-4333-8444-555555555555"
    session_dir, log, cached = _session(tmp_path, dsh)
    old_log = dsh.home() / "sessions" / "--D-work--" / earlier / "session.v4.jsonl.zstd"
    old_log.parent.mkdir(parents=True)
    old_log.write_bytes(b"what was said before")
    note = json.loads((session_dir / SESSION_FILE).read_text(encoding="utf-8"))
    # dsh could not resume the earlier one, and another was started in its place.
    (session_dir / SESSION_FILE).write_text(json.dumps({**note, "previous": [earlier, "../../escape", 7]}), encoding="utf-8")

    assert dsh.forget_session(session_dir) is True

    assert not log.exists() and not cached.exists() and not old_log.parent.exists()
    assert (session_dir / "work" / "kept.txt").is_file()


async def test_a_host_that_sends_more_than_the_file_is_cut_off_and_its_file_refused(
    tmp_path: Path, release, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nekoclaw.dsh import install

    data = release(_release())
    # The file arrives whole in one piece, and the host goes on sending.
    monkeypatch.setattr(install, "_CHUNK_BYTES", len(data))
    sent: list[int] = []

    class Endless(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield data
            for index in range(200):
                sent.append(index)
                yield b"x" * len(data)

    dsh = DshInstallation(tmp_path / "dsh", transport=_serving({"first.test": Endless(), "second.test": 404}, []))

    with pytest.raises(DshUnavailable) as failure:
        await dsh.install()

    # What was kept is the file that was verified, byte for byte; what came after makes it another file.
    assert "not the one expected" in str(failure.value)
    # It was not read on to its end.
    assert len(sent) < 5
    assert not dsh.installed() and list((tmp_path / "dsh" / "download").iterdir()) == []


async def test_a_file_that_names_a_path_outside_its_directory_is_refused(tmp_path: Path, release) -> None:
    data = release(_release({"deepseek_harness_runtime/runtime/../../../escaped.txt": b"x"}))
    dsh = DshInstallation(tmp_path / "dsh", transport=_serving({"first.test": data}, []))
    with pytest.raises(DshUnavailable):
        await dsh.install()
    assert not dsh.installed() and not (tmp_path / "escaped.txt").exists() and not (tmp_path / "dsh" / "escaped.txt").exists()


async def test_a_file_without_the_program_for_this_platform_is_refused(tmp_path: Path, release) -> None:
    data = release(_release(program=False))
    dsh = DshInstallation(tmp_path / "dsh", transport=_serving({"first.test": data}, []))
    with pytest.raises(DshUnavailable):
        await dsh.install()
    assert not dsh.installed()


async def test_a_platform_dsh_is_not_released_for(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(install_module, "platform_tag", lambda: None)
    dsh = DshInstallation(tmp_path / "dsh")
    status = dsh.status()
    assert status["supported"] is False and status["installed"] is False and status["download_bytes"] is None
    with pytest.raises(DshUnavailable):
        await dsh.install()
    assert not dsh.wanted()


def test_the_platform_names_are_dshs(monkeypatch: pytest.MonkeyPatch) -> None:
    import platform

    for system, machine, expected in [
        ("win32", "AMD64", "win-x64"),
        ("darwin", "arm64", "macos-arm64"),
        ("darwin", "x86_64", "macos-x64"),
        ("linux", "x86_64", "linux-x64"),
        ("linux", "aarch64", "linux-arm64"),
        # No build for Windows on ARM, nor for anything else.
        ("win32", "ARM64", None),
        ("freebsd", "amd64", None),
    ]:
        monkeypatch.setattr(sys, "platform", system)
        monkeypatch.setattr(platform, "machine", lambda value=machine: value)
        monkeypatch.setattr(platform, "mac_ver", lambda: ("15.1", ("", "", ""), "arm64"))
        monkeypatch.setattr(platform, "libc_ver", lambda *args, **kwargs: ("glibc", "2.39"))
        assert platform_tag() == expected
    assert executable_name("win-x64").endswith(".exe") and not executable_name("linux-x64").endswith(".exe")


@pytest.mark.parametrize(
    ("system", "mac", "libc", "expected"),
    [
        # The release files are for macOS 14 and a C library of 2.28 at the least: an older
        # system would install dsh and then not start it.
        ("darwin", "13.6.1", ("", ""), None),
        ("darwin", "14.0", ("", ""), "macos-arm64"),
        ("darwin", "26.1", ("", ""), "macos-arm64"),
        ("linux", "", ("glibc", "2.27"), None),
        ("linux", "", ("glibc", "2.28"), "linux-arm64"),
        ("linux", "", ("glibc", "2.9"), None),
        ("linux", "", ("glibc", "3.0"), "linux-arm64"),
        # A system that does not tell is taken to be new enough.
        ("darwin", "", ("", ""), "macos-arm64"),
        ("linux", "", ("", ""), "linux-arm64"),
        ("linux", "", ("musl", "1.2"), "linux-arm64"),
        ("linux", "", ("glibc", "unknown"), "linux-arm64"),
    ],
)
def test_a_system_older_than_the_release_runs_on_counts_as_not_released_for(
    monkeypatch: pytest.MonkeyPatch, system: str, mac: str, libc: tuple[str, str], expected: str | None
) -> None:
    import platform

    monkeypatch.setattr(sys, "platform", system)
    monkeypatch.setattr(platform, "machine", lambda: "arm64")
    monkeypatch.setattr(platform, "mac_ver", lambda: (mac, ("", "", ""), "arm64"))
    monkeypatch.setattr(platform, "libc_ver", lambda *args, **kwargs: libc)
    assert platform_tag() == expected


async def test_an_install_cancelled_while_it_unpacks_waits_for_the_unpacking(
    tmp_path: Path, release, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    import threading

    data = release(_release())
    started, go_on = threading.Event(), threading.Event()
    finished: list[bool] = []
    unpack = DshInstallation._unpack

    def slow(self: DshInstallation, *args: Any) -> None:
        started.set()
        go_on.wait(10)
        unpack(self, *args)
        finished.append(True)

    monkeypatch.setattr(DshInstallation, "_unpack", slow)
    dsh = DshInstallation(tmp_path / "dsh", transport=_serving({"first.test": data}, []))
    installing = asyncio.ensure_future(dsh.install())
    await _until(started)

    installing.cancel()
    await asyncio.sleep(0.1)
    # Not handed back before the thread that unpacks is done: an uninstall after it
    # does not run beside it, and the release file is gone only once it was read.
    assert not installing.done()
    go_on.set()
    with pytest.raises(asyncio.CancelledError):
        await installing

    assert finished == [True]
    assert list((tmp_path / "dsh" / "download").iterdir()) == []
    await dsh.uninstall()
    assert not dsh.installed()


async def test_an_install_cancelled_before_it_downloads_is_not_left_downloading(
    tmp_path: Path, release, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    import threading

    data = release(_release())
    looked, go_on = threading.Event(), threading.Event()
    tag = install_module.platform_tag
    asked: list[bool] = []

    def platform_first() -> Any:
        # Asked once by ``installed()``, then by the install itself.
        asked.append(True)
        if len(asked) == 2:
            looked.set()
        return tag()

    def root() -> Path:
        # Where it goes is looked up after the platform: held there.
        if looked.is_set():
            go_on.wait(10)
        return tmp_path / "dsh"

    monkeypatch.setattr(install_module, "platform_tag", platform_first)
    dsh = DshInstallation(root, transport=_serving({"first.test": data}, []))
    installing = asyncio.ensure_future(dsh.install())
    await _until(looked)
    await asyncio.sleep(0.05)

    installing.cancel()
    go_on.set()
    with pytest.raises(asyncio.CancelledError):
        await installing

    assert dsh.status()["downloading"] is False


async def test_an_uninstall_cancelled_while_it_removes_waits_for_the_removing(
    tmp_path: Path, release, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    import threading

    data = release(_release())
    dsh = DshInstallation(tmp_path / "dsh", transport=_serving({"first.test": data}, []))
    await dsh.install()
    started, go_on = threading.Event(), threading.Event()
    finished: list[bool] = []
    remove = install_module._remove

    def slow(path: Path, *, strict: bool = False) -> None:
        if path.name == "runtime":
            started.set()
            go_on.wait(10)
        remove(path, strict=strict)
        if path.name == "runtime":
            finished.append(True)

    monkeypatch.setattr(install_module, "_remove", slow)
    removing = asyncio.ensure_future(dsh.uninstall())
    await _until(started)

    removing.cancel()
    await asyncio.sleep(0.1)
    # The lock (and the host's, around it) is not let go of while the program is
    # being removed: no commission starts it from a half removed directory.
    assert not removing.done()
    go_on.set()
    with pytest.raises(asyncio.CancelledError):
        await removing

    assert finished == [True] and not dsh.installed()


async def _until(event: Any) -> None:
    """Wait, a bounded while, for a thread's event; fail when it does not come."""
    import asyncio

    for _attempt in range(1000):
        if event.is_set():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the thread never got there")


async def test_a_download_that_breaks_off_leaves_no_half_a_file(tmp_path: Path, release) -> None:
    data = release(_release())

    class Full(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield data[:10]
            raise OSError(28, "No space left on device")

    dsh = DshInstallation(tmp_path / "dsh", transport=_serving({"first.test": Full()}, []))

    with pytest.raises(DshUnavailable, match="No space left on device"):
        await dsh.install()

    assert list((tmp_path / "dsh" / "download").iterdir()) == [] and not dsh.installed()
    assert "No space left" in dsh.status()["last_error"] and dsh.status()["downloading"] is False


async def test_an_install_that_is_cancelled_leaves_no_half_a_file(tmp_path: Path, release) -> None:
    import asyncio

    data = release(_release())
    reached = asyncio.Event()

    class Stalled(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield data[:10]
            reached.set()
            await asyncio.sleep(60)
            yield data[10:]

    dsh = DshInstallation(tmp_path / "dsh", transport=_serving({"first.test": Stalled()}, []))
    installing = asyncio.ensure_future(dsh.install())
    await asyncio.wait_for(reached.wait(), 10)
    for _attempt in range(200):
        if list((tmp_path / "dsh" / "download").iterdir()):
            break
        await asyncio.sleep(0.01)

    installing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await installing

    assert list((tmp_path / "dsh" / "download").iterdir()) == [] and not dsh.installed()
    assert dsh.status()["downloading"] is False


async def test_removing_keeps_dshs_home_unless_told_otherwise(tmp_path: Path, release) -> None:
    data = release(_release())
    dsh = DshInstallation(tmp_path / "dsh", transport=_serving({"first.test": data}, []))
    await dsh.install()
    log = dsh.home() / "sessions" / "x" / "session.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text("what was said", encoding="utf-8")
    (tmp_path / "dsh" / "cache").mkdir()
    dsh.set_preference("builtin")

    status = await dsh.uninstall()

    assert status["installed"] is False and not (tmp_path / "dsh" / "runtime").exists() and not (tmp_path / "dsh" / "cache").exists()
    # The logs of its sessions stay: a commission resumes from them after a new install.
    assert log.read_text(encoding="utf-8") == "what was said" and dsh.preference() == "builtin"
    await dsh.uninstall(purge=True)
    assert not dsh.home().exists()
    # Removing what is not there is fine.
    await dsh.uninstall()


async def test_which_runtime_the_user_wants(tmp_path: Path, release) -> None:
    data = release(_release())
    dsh = DshInstallation(lambda: tmp_path / "dsh", transport=_serving({"first.test": data}, []))
    # Not installed: the built-in runtime, whatever the setting says.
    assert dsh.preference() == "auto" and not dsh.wanted()
    await dsh.install()
    assert dsh.wanted()

    dsh.set_preference("builtin")
    assert dsh.preference() == "builtin" and not dsh.wanted() and dsh.status()["in_use"] is False
    assert dsh.installed()
    dsh.set_preference("auto")
    assert dsh.wanted()
    with pytest.raises(ValueError):
        dsh.set_preference("dsh-nightly")
    # A file that cannot be read is the default, not an error.
    dsh.settings_file().write_text("{not json", encoding="utf-8")
    assert dsh.preference() == "auto"


async def test_another_version_on_the_disk_is_not_used(tmp_path: Path, release, monkeypatch: pytest.MonkeyPatch) -> None:
    from config import nekoclaw_settings

    data = release(_release())
    dsh = DshInstallation(tmp_path / "dsh", transport=_serving({"first.test": data}, []))
    await dsh.install()
    monkeypatch.setattr(nekoclaw_settings, "DSH_VERSION", "9.9.9")
    assert not dsh.installed() and dsh.status()["version"] == "9.9.9"


# ---------------------------------------------------------------------------
# What dsh keeps
# ---------------------------------------------------------------------------


def _session(tmp_path: Path, dsh: DshInstallation, session: str = "6fb9e6f3-9e7f-44e0-a874-583885aa9787") -> tuple[Path, Path, Path]:
    """A session directory of the host's, and what dsh keeps of the session that belongs to it."""
    from nekoclaw.dsh.driver import SESSION_FILE

    session_dir = tmp_path / "sessions" / "c1"
    session_dir.mkdir(parents=True)
    (session_dir / SESSION_FILE).write_text(json.dumps({"session": session, "workdir": "w"}), encoding="utf-8")
    (session_dir / "work").mkdir()
    (session_dir / "work" / "kept.txt").write_text("the user's file", encoding="utf-8")
    log = dsh.home() / "sessions" / "--D-work--" / session / "session.v4.jsonl.zstd"
    log.parent.mkdir(parents=True)
    log.write_bytes(b"what was said")
    cached = dsh.home() / "storages" / "session_projcache" / "sessions" / f"{session}.json"
    cached.parent.mkdir(parents=True)
    cached.write_text("{}", encoding="utf-8")
    return session_dir, log, cached


def test_a_session_whose_log_cannot_be_removed_keeps_its_note_for_the_next_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    from nekoclaw.dsh.driver import SESSION_FILE

    dsh = DshInstallation(tmp_path / "dsh")
    session_dir, log, cached = _session(tmp_path, dsh)
    removing = shutil.rmtree

    def held_open(path: Any, *args: Any, **kwargs: Any) -> None:
        if Path(path) == log.parent:
            raise PermissionError(13, "held open by another program", str(path))
        removing(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", held_open)

    with pytest.raises(OSError):
        dsh.forget_session(session_dir)

    # What could be removed is; the note stays, so the next attempt knows what is left.
    assert log.exists() and not cached.exists() and (session_dir / SESSION_FILE).exists()
    monkeypatch.setattr(shutil, "rmtree", removing)
    assert dsh.forget_session(session_dir) is True
    assert not log.parent.exists() and not (session_dir / SESSION_FILE).exists()


def test_a_file_among_dshs_directories_of_sessions_is_none_of_them(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nekoclaw.dsh.driver import SESSION_FILE

    dsh = DshInstallation(tmp_path / "dsh")
    session_dir, log, cached = _session(tmp_path, dsh)
    stray = dsh.home() / "sessions" / ".DS_Store"
    stray.write_bytes(b"\0")
    remove = install_module._remove

    def as_on_posix(path: Path, *, strict: bool = False) -> None:
        # What removing a path below a file raises on macOS and Linux (on Windows it is "not found").
        if path.parent.is_file():
            raise NotADirectoryError(20, "Not a directory", str(path))
        remove(path, strict=strict)

    monkeypatch.setattr(install_module, "_remove", as_on_posix)

    assert dsh.forget_session(session_dir) is True
    assert not log.parent.exists() and not cached.exists() and not (session_dir / SESSION_FILE).exists()
    assert stray.exists()


def test_a_session_whose_logs_cannot_be_looked_for_keeps_its_note_for_the_next_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nekoclaw.dsh.driver import SESSION_FILE

    dsh = DshInstallation(tmp_path / "dsh")
    session_dir, log, cached = _session(tmp_path, dsh)
    listing = Path.iterdir
    projects = dsh.home() / "sessions"

    def refused(self: Path) -> Any:
        if self == projects:
            raise PermissionError(13, "Permission denied", str(self))
        return listing(self)

    monkeypatch.setattr(Path, "iterdir", refused)

    with pytest.raises(PermissionError):
        dsh.forget_session(session_dir)

    # The log may well be there: the note stays, so that it is not left behind for good.
    assert log.exists() and (session_dir / SESSION_FILE).exists()
    monkeypatch.setattr(Path, "iterdir", listing)
    assert dsh.forget_session(session_dir) is True
    assert not log.parent.exists() and not (session_dir / SESSION_FILE).exists()


def test_what_dsh_kept_of_a_session_is_removed_with_it(tmp_path: Path) -> None:
    from nekoclaw.dsh.driver import SESSION_FILE

    dsh = DshInstallation(tmp_path / "dsh")
    session_dir, log, cached = _session(tmp_path, dsh)
    other = dsh.home() / "sessions" / "--D-work--" / "another-session-0001" / "session.v4.jsonl.zstd"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"someone else's")

    assert dsh.forget_session(session_dir) is True

    assert not log.parent.exists() and not cached.exists() and not (session_dir / SESSION_FILE).exists()
    # Nothing else: another session's log, and what the task agent worked on.
    assert other.read_bytes() == b"someone else's"
    assert (session_dir / "work" / "kept.txt").read_text(encoding="utf-8") == "the user's file"
    # A session directory of a commission that never ran in dsh: nothing to do.
    assert dsh.forget_session(session_dir) is False
    assert dsh.forget_session(tmp_path / "no-such-directory") is False


@pytest.mark.parametrize("session", ["../../../escape", "..", "a/b", "", "x", 7, None])
def test_a_note_that_does_not_name_a_session_removes_nothing_of_dshs(tmp_path: Path, session: Any) -> None:
    from nekoclaw.dsh.driver import SESSION_FILE

    dsh = DshInstallation(tmp_path / "dsh")
    session_dir, log, cached = _session(tmp_path, dsh)
    (session_dir / SESSION_FILE).write_text(json.dumps({"session": session}), encoding="utf-8")
    outside = tmp_path / "escape"
    outside.mkdir()
    (outside / "file.txt").write_text("not dsh's", encoding="utf-8")

    assert dsh.forget_session(session_dir) is True

    # The note goes; what it does not properly name stays.
    assert not (session_dir / SESSION_FILE).exists()
    assert log.is_file() and cached.is_file() and (outside / "file.txt").is_file()


def test_the_plugins_the_user_added_are_listed_from_the_profile(tmp_path: Path) -> None:
    dsh = DshInstallation(tmp_path / "dsh")
    assert dsh.plugins() == []
    profile = dsh.home() / "profiles" / "acp"
    profile.mkdir(parents=True)
    (profile / "package.json").write_text(
        json.dumps(
            {
                "name": "dsh-profile-acp",
                "dependencies": {"dsh-plugin-notes": "^1.2.0", "left-pad": "1.3.0"},
                "dsh": {"profile": {"bundles": ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-acp-app", "dsh-plugin-notes", "linked-bundle"]}},
            }
        ),
        encoding="utf-8",
    )

    assert dsh.plugins() == [
        {"name": "dsh-plugin-notes", "version": "^1.2.0", "enabled": True},
        # Installed but not a bundle of the profile: not switched on.
        {"name": "left-pad", "version": "1.3.0", "enabled": False},
        {"name": "linked-bundle", "version": "", "enabled": True},
    ]
    (profile / "package.json").write_text("{not json", encoding="utf-8")
    assert dsh.plugins() == []
