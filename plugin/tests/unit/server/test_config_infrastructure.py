from __future__ import annotations

import threading
from pathlib import Path

import pytest
from fastapi import HTTPException

from plugin.server.infrastructure import config_locking as locking
from plugin.server.infrastructure import config_merge as merge
from plugin.server.infrastructure import config_paths as paths
from plugin.server.infrastructure import config_protected as protected


@pytest.mark.plugin_unit
def test_deep_merge_handles_delete_replace_and_nested() -> None:
    base = {
        "keep": 1,
        "nested": {"a": 1, "b": 2},
        "remove_me": True,
        "repl": {"x": 1},
    }
    updates = {
        "nested": {"b": 3, 123: "ignored"},
        "remove_me": merge.DELETE_MARKER,
        "repl": {"__replace__": True, "k": "v"},
        1: "ignored",
    }
    out = merge.deep_merge(base, updates)
    assert out["keep"] == 1
    assert out["nested"] == {"a": 1, "b": 3}
    assert "remove_me" not in out
    assert out["repl"] == {"k": "v"}


@pytest.mark.plugin_unit
def test_deep_merge_sets_empty_mapping_and_plain_value() -> None:
    out = merge.deep_merge({"a": {"x": 1}, "b": 1}, {"a": {}, "b": 2})
    assert out["a"] == {}
    assert out["b"] == 2


@pytest.mark.plugin_unit
def test_validate_protected_fields_unchanged_and_get_field() -> None:
    current = {"plugin": {"id": "p1", "entry": "mod:Main"}}
    new_ok = {"plugin": {"id": "p1", "entry": "mod:Main"}}
    protected.validate_protected_fields_unchanged(current_config=current, new_config=new_ok)

    with pytest.raises(HTTPException):
        protected.validate_protected_fields_unchanged(
            current_config=current,
            new_config={"plugin": {"id": "p2", "entry": "mod:Main"}},
        )
    with pytest.raises(HTTPException):
        protected.validate_protected_fields_unchanged(
            current_config=current,
            new_config={"plugin": {"id": "p1", "entry": "other:Main"}},
        )
    assert protected._get_plugin_field({}, "id") is None


@pytest.mark.plugin_unit
def test_get_plugin_config_path_success_not_found_and_invalid(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    cfg = root / "demo" / "plugin.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("[plugin]\nid='demo'\n", encoding="utf-8")

    monkeypatch.setattr(paths, "PLUGIN_CONFIG_ROOT", root)

    assert paths.get_plugin_config_path("demo") == cfg

    with pytest.raises(HTTPException) as exc_not_found:
        paths.get_plugin_config_path("missing")
    assert exc_not_found.value.status_code == 404

    with pytest.raises(HTTPException) as exc_invalid:
        paths.get_plugin_config_path("bad/path")
    assert exc_invalid.value.status_code == 400


@pytest.mark.plugin_unit
def test_get_plugin_config_path_resolve_error_and_traversal(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    cfg = root / "demo" / "plugin.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("x=1\n", encoding="utf-8")
    monkeypatch.setattr(paths, "PLUGIN_CONFIG_ROOT", root)

    original_resolve = Path.resolve

    def _raise_resolve(self):  # type: ignore[no-untyped-def]
        raise OSError("resolve failed")

    monkeypatch.setattr(Path, "resolve", _raise_resolve)
    with pytest.raises(HTTPException) as exc_resolve:
        paths.get_plugin_config_path("demo")
    assert exc_resolve.value.status_code == 400

    def _fake_resolve(self):  # type: ignore[no-untyped-def]
        if str(self).endswith("plugin.toml"):
            return (tmp_path / "outside" / "plugin.toml")
        return original_resolve(self)

    monkeypatch.setattr(Path, "resolve", _fake_resolve)
    with pytest.raises(HTTPException) as exc_traversal:
        paths.get_plugin_config_path("demo")
    assert exc_traversal.value.status_code == 400


@pytest.mark.plugin_unit
def test_get_plugin_update_lock_and_file_lock_backends(monkeypatch: pytest.MonkeyPatch) -> None:
    lock1 = locking.get_plugin_update_lock("demo")
    lock2 = locking.get_plugin_update_lock("demo")
    assert isinstance(lock1, threading.Lock().__class__)
    assert lock1 is lock2

    class _File:
        def __init__(self) -> None:
            self.pos = 0

        def fileno(self) -> int:
            return 3

        def seek(self, offset: int, whence: int = 0) -> int:
            if whence == 2:
                self.pos = 10
            elif whence == 0:
                self.pos = offset
            return self.pos

        def tell(self) -> int:
            return self.pos

    file_obj = _File()

    # no backend
    monkeypatch.setattr(locking, "_msvcrt", None)
    monkeypatch.setattr(locking, "_fcntl", None)
    with locking.file_lock(file_obj):
        pass

    # msvcrt backend
    calls: list[tuple[int, int, int]] = []

    class _Msvcrt:
        LK_LOCK = 1
        LK_UNLCK = 2

        @staticmethod
        def locking(fd: int, mode: int, size: int) -> None:
            calls.append((fd, mode, size))

    monkeypatch.setattr(locking, "_msvcrt", _Msvcrt())
    monkeypatch.setattr(locking, "_fcntl", None)
    with locking.file_lock(file_obj):
        pass
    assert calls[0][1] == _Msvcrt.LK_LOCK
    assert calls[-1][1] == _Msvcrt.LK_UNLCK

    # fcntl backend
    f_calls: list[tuple[int, int]] = []

    class _Fcntl:
        LOCK_EX = 3
        LOCK_UN = 4

        @staticmethod
        def flock(fd: int, mode: int) -> None:
            f_calls.append((fd, mode))

    monkeypatch.setattr(locking, "_msvcrt", None)
    monkeypatch.setattr(locking, "_fcntl", _Fcntl())
    with locking.file_lock(file_obj):
        pass
    assert f_calls == [(3, _Fcntl.LOCK_EX), (3, _Fcntl.LOCK_UN)]


import io
import types
from pathlib import Path

import pytest
from fastapi import HTTPException

from plugin.server.infrastructure import config_queries as queries
from plugin.server.infrastructure import config_storage as storage
from plugin.server.infrastructure import config_toml as toml_mod
from plugin.server.infrastructure import config_updates as updates


@pytest.mark.plugin_unit
def test_toml_helpers_reader_writer_and_coerce(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(toml_mod, "_toml_reader", None)
    monkeypatch.setattr(toml_mod, "_toml_writer", None)
    with pytest.raises(HTTPException):
        toml_mod.require_toml_reader()
    with pytest.raises(HTTPException):
        toml_mod.require_toml_writer()

    assert toml_mod._coerce_string_key_mapping({"a": 1, 2: 3}, context="x") == {"a": 1}
    with pytest.raises(HTTPException):
        toml_mod._coerce_string_key_mapping([], context="bad")

    class _Reader:
        @staticmethod
        def load(_fp):  # noqa: ANN001
            return {"a": 1}

        @staticmethod
        def loads(_text):  # noqa: ANN001
            return {"b": 2}

    class _Writer:
        @staticmethod
        def dump(obj, fp):  # noqa: ANN001
            fp.write(b"k='v'\n")

    monkeypatch.setattr(toml_mod, "_toml_reader", _Reader())
    monkeypatch.setattr(toml_mod, "_toml_writer", _Writer())

    p = tmp_path / "x.toml"
    p.write_text("a=1\n", encoding="utf-8")
    assert toml_mod.load_toml_from_file(p) == {"a": 1}
    assert toml_mod.load_toml_from_stream(io.BytesIO(b"a=1"), context="stream") == {"a": 1}
    assert toml_mod.parse_toml_text("b=2", context="ctx") == {"b": 2}
    assert "k='v'" in toml_mod.render_toml_text({"x": 1})
    assert b"k='v'" in toml_mod.dump_toml_bytes({"x": 1})


@pytest.mark.plugin_unit
def test_toml_helpers_error_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class _ReaderBad:
        @staticmethod
        def load(_fp):  # noqa: ANN001
            raise ValueError("bad-load")

        @staticmethod
        def loads(_text):  # noqa: ANN001
            raise TypeError("bad-text")

    class _WriterBad:
        @staticmethod
        def dump(obj, fp):  # noqa: ANN001
            raise RuntimeError("bad-dump")

    monkeypatch.setattr(toml_mod, "_toml_reader", _ReaderBad())
    monkeypatch.setattr(toml_mod, "_toml_writer", _WriterBad())

    with pytest.raises(HTTPException):
        toml_mod.load_toml_from_file(tmp_path / "missing.toml")
    with pytest.raises(HTTPException):
        toml_mod.load_toml_from_stream(io.BytesIO(b""), context="ctx")
    with pytest.raises(HTTPException):
        toml_mod.parse_toml_text("x", context="ctx")
    with pytest.raises(HTTPException):
        toml_mod.render_toml_text({"x": 1})
    with pytest.raises(HTTPException):
        toml_mod.dump_toml_bytes({"x": 1})


@pytest.mark.plugin_unit
def test_storage_atomic_write_success_and_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "out.toml"
    storage.atomic_write_text(target=target, text="a=1\n", prefix=".cfg_")
    assert target.read_text(encoding="utf-8") == "a=1\n"

    monkeypatch.setattr(storage.tempfile, "mkstemp", lambda **_: (_ for _ in ()).throw(OSError("x")))
    with pytest.raises(HTTPException):
        storage.atomic_write_bytes(target=target, payload=b"x", prefix=".cfg_")


@pytest.mark.plugin_unit
def test_storage_atomic_write_cleanup_error_branch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "err.toml"
    temp = tmp_path / "tmp.toml"
    temp.write_bytes(b"x")
    fd = temp.open("rb").fileno()

    monkeypatch.setattr(storage.tempfile, "mkstemp", lambda **_: (fd, str(temp)))
    monkeypatch.setattr(storage.os, "fdopen", lambda _fd, _mode: open(temp, "wb"))
    monkeypatch.setattr(storage.os, "replace", lambda *_a, **_k: (_ for _ in ()).throw(OSError("replace")))
    monkeypatch.setattr(Path, "exists", lambda self: True)
    monkeypatch.setattr(Path, "unlink", lambda self: (_ for _ in ()).throw(OSError("unlink")))

    with pytest.raises(HTTPException):
        storage.atomic_write_bytes(target=target, payload=b"abc", prefix=".cfg_")


@pytest.mark.plugin_unit
def test_queries_validate_config_schema_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    original_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):  # type: ignore[no-untyped-def]
        if name == "plugin.server.config_schema":
            raise ImportError("no schema")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    assert queries._validate_config_schema({"x": 1}, "demo") is None

    monkeypatch.setattr(builtins, "__import__", original_import)

    module = types.ModuleType("plugin.server.config_schema")

    class _ConfigValidationError(Exception):
        def __init__(self, message: str, field: str, details):  # noqa: ANN001
            super().__init__(message)
            self.message = message
            self.field = field
            self.details = details

    def _raise_list(_data):  # noqa: ANN001
        raise _ConfigValidationError("m", "f", [{"a": 1, 2: 3}])

    module.ConfigValidationError = _ConfigValidationError
    module.validate_plugin_config = _raise_list
    monkeypatch.setitem(__import__("sys").modules, "plugin.server.config_schema", module)
    out = queries._validate_config_schema({"x": 1}, "demo")
    assert out == [{"a": 1, "2": 3}]

    def _raise_simple(_data):  # noqa: ANN001
        raise _ConfigValidationError("msg", "field", "x")

    module.validate_plugin_config = _raise_simple
    out2 = queries._validate_config_schema({"x": 1}, "demo")
    assert out2 == [{"msg": "msg", "field": "field"}]


@pytest.mark.plugin_unit
def test_queries_load_and_render_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = tmp_path / "plugin.toml"
    cfg.write_text("[plugin]\nid='demo'\n", encoding="utf-8")

    monkeypatch.setattr(queries, "get_plugin_config_path", lambda _pid: cfg)
    monkeypatch.setattr(queries, "load_toml_from_file", lambda _p: {"plugin": {"id": "demo"}, "runtime": {"x": 1}})
    monkeypatch.setattr(queries, "apply_user_config_profiles", lambda **kwargs: {"runtime": {"x": 2}})
    monkeypatch.setattr(queries, "_validate_config_schema", lambda *_a, **_k: [{"warn": True}])
    monkeypatch.setattr(queries, "_schema_validation_enabled", True)

    base = queries.load_plugin_base_config("demo")
    assert base["plugin_id"] == "demo"
    payload = queries.load_plugin_config("demo", validate=True)
    assert payload["config"] == {"runtime": {"x": 2}}

    toml_payload = queries.load_plugin_config_toml("demo")
    assert "toml" in toml_payload

    monkeypatch.setattr(queries, "parse_toml_text", lambda text, context: {"plugin": {"id": "demo"}})
    monkeypatch.setattr(queries, "load_plugin_config", lambda _pid: {"config": {"plugin": {"id": "demo"}}})
    monkeypatch.setattr(queries, "validate_protected_fields_unchanged", lambda **_: None)
    assert queries.parse_toml_to_config("demo", "x=1")["plugin_id"] == "demo"

    monkeypatch.setattr(queries, "render_toml_text", lambda config: "ok=1\n")
    assert queries.render_config_to_toml("demo", {"plugin": {"id": "demo"}})["toml"] == "ok=1\n"


@pytest.mark.plugin_unit
def test_queries_bad_payload_shape_and_input(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(HTTPException):
        queries.parse_toml_to_config("demo", None)  # type: ignore[arg-type]

    monkeypatch.setattr(queries, "parse_toml_text", lambda text, context: {})
    monkeypatch.setattr(queries, "load_plugin_config", lambda _pid: {"config": "bad"})
    with pytest.raises(HTTPException):
        queries.parse_toml_to_config("demo", "x=1")

    with pytest.raises(HTTPException):
        queries.render_config_to_toml("demo", [])  # type: ignore[arg-type]

    monkeypatch.setattr(queries, "load_plugin_config", lambda _pid: {"config": "bad"})
    with pytest.raises(HTTPException):
        queries.render_config_to_toml("demo", {"x": 1})


@pytest.mark.plugin_unit
def test_updates_helper_and_toml_update_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    with pytest.raises(HTTPException):
        updates._ensure_string_key_mapping([], field="updates")
    with pytest.raises(HTTPException):
        updates._ensure_string_key_mapping({1: "x"}, field="updates")
    assert updates._ensure_string_key_mapping({"a": 1}, field="updates") == {"a": 1}

    cfg = tmp_path / "plugin.toml"
    cfg.write_text("[plugin]\nid='demo'\n", encoding="utf-8")
    monkeypatch.setattr(updates, "get_plugin_update_lock", lambda _pid: __import__("threading").Lock())
    monkeypatch.setattr(updates, "get_plugin_config_path", lambda _pid: cfg)
    monkeypatch.setattr(updates, "parse_toml_text", lambda text, context: {"plugin": {"id": "demo"}})
    monkeypatch.setattr(updates, "load_toml_from_stream", lambda stream, context: {"plugin": {"id": "demo"}})
    monkeypatch.setattr(updates, "validate_protected_fields_unchanged", lambda **_: None)
    monkeypatch.setattr(updates, "atomic_write_text", lambda **_: None)
    monkeypatch.setattr(updates, "load_plugin_config", lambda _pid: {"config": {"plugin": {"id": "demo"}}})
    out = updates.update_plugin_config_toml("demo", "[plugin]\nid='demo'\n")
    assert out["success"] is True


@pytest.mark.plugin_unit
def test_updates_replace_plugin_config_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = tmp_path / "plugin.toml"
    cfg.write_text("[plugin]\nid='demo'\n", encoding="utf-8")

    monkeypatch.setattr(updates, "get_plugin_update_lock", lambda _pid: __import__("threading").Lock())
    monkeypatch.setattr(updates, "get_plugin_config_path", lambda _pid: cfg)
    monkeypatch.setattr(updates, "load_toml_from_stream", lambda stream, context: {"plugin": {"id": "demo", "entry": "m:Main"}})
    monkeypatch.setattr(updates, "validate_protected_fields_unchanged", lambda **_: None)
    monkeypatch.setattr(updates, "dump_toml_bytes", lambda payload: b"x=1\n")
    monkeypatch.setattr(updates, "atomic_write_bytes", lambda **_: None)
    monkeypatch.setattr(updates, "load_plugin_config", lambda _pid: {"config": {"runtime": {"enabled": True}}})

    out = updates.replace_plugin_config("demo", {"runtime": {"enabled": True}})
    assert out["success"] is True
    assert out["plugin_id"] == "demo"


import threading
from pathlib import Path

import pytest
from fastapi import HTTPException

from plugin.server.infrastructure import config_profiles as profiles
from plugin.server.infrastructure import config_profiles_write as profiles_write


@pytest.mark.plugin_unit
def test_profiles_helpers_and_active_resolution(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = tmp_path / "plugin.toml"
    cfg.write_text("[plugin]\nid='demo'\n", encoding="utf-8")

    assert profiles._extract_profiles_cfg_from_base_config({"plugin": {"config_profiles": {"active": "dev"}}}) == {
        "active": "dev"
    }
    assert profiles._extract_profiles_cfg_from_base_config({"plugin": {}}) is None

    monkeypatch.setenv("NEKO_PLUGIN_DEMO_PROFILE", "prod")
    assert profiles._resolve_active_profile_name("demo", {"active": "dev"}) == "prod"

    monkeypatch.delenv("NEKO_PLUGIN_DEMO_PROFILE", raising=False)
    assert profiles._resolve_active_profile_name("demo", {"active": "  "}) is None

    assert profiles._to_string_key_mapping({"a": 1, 2: 3}) == {"a": 1}


@pytest.mark.plugin_unit
def test_profiles_loaders_and_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    plugin_dir = tmp_path / "demo"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    cfg = plugin_dir / "plugin.toml"
    cfg.write_text("[plugin]\nid='demo'\n", encoding="utf-8")
    profiles_toml = plugin_dir / "profiles.toml"
    profiles_toml.write_text("[config_profiles]\nactive='dev'\n[config_profiles.files]\ndev='profiles/dev.toml'\n", encoding="utf-8")
    dev_path = plugin_dir / "profiles" / "dev.toml"
    dev_path.parent.mkdir(parents=True, exist_ok=True)
    dev_path.write_text("[runtime]\nlevel=2\n", encoding="utf-8")

    monkeypatch.setattr(profiles, "tomllib", __import__("tomllib"))
    cfg_obj = profiles.load_profiles_cfg_from_file("demo", cfg)
    assert isinstance(cfg_obj, dict)
    assert cfg_obj["active"] == "dev"

    state = profiles.get_profiles_state(plugin_id="demo", config_path=cfg)
    assert state["profiles_exists"] is True
    files = state["config_profiles"]["files"]  # type: ignore[index]
    assert files["dev"]["exists"] is True  # type: ignore[index]

    profile = profiles.get_profile_config(plugin_id="demo", profile_name="dev", config_path=cfg)
    assert profile["profile"]["exists"] is True  # type: ignore[index]
    assert profile["config"]["runtime"]["level"] == 2  # type: ignore[index]


@pytest.mark.plugin_unit
def test_profiles_get_profile_config_error_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = tmp_path / "plugin.toml"
    cfg.write_text("[plugin]\nid='demo'\n", encoding="utf-8")
    monkeypatch.setattr(profiles, "tomllib", __import__("tomllib"))

    with pytest.raises(HTTPException):
        profiles.get_profile_config(plugin_id="demo", profile_name="", config_path=cfg)
    with pytest.raises(HTTPException):
        profiles.get_profile_config(plugin_id="demo", profile_name="../x", config_path=cfg)

    p = tmp_path / "profiles" / "dev.toml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x=1\n", encoding="utf-8")
    monkeypatch.setattr(profiles, "_resolve_profiles_cfg", lambda **_: {"files": {"dev": "profiles/dev.toml"}})

    orig_load = profiles.tomllib.load
    monkeypatch.setattr(profiles.tomllib, "load", lambda fp: (_ for _ in ()).throw(ValueError("bad")))  # type: ignore[arg-type]
    with pytest.raises(HTTPException) as exc_parse:
        profiles.get_profile_config(plugin_id="demo", profile_name="dev", config_path=cfg)
    assert exc_parse.value.status_code == 400

    monkeypatch.setattr(profiles.tomllib, "load", lambda fp: (_ for _ in ()).throw(OSError("io")))  # type: ignore[arg-type]
    with pytest.raises(HTTPException) as exc_read:
        profiles.get_profile_config(plugin_id="demo", profile_name="dev", config_path=cfg)
    assert exc_read.value.status_code == 500

    monkeypatch.setattr(profiles.tomllib, "load", orig_load)  # type: ignore[arg-type]


@pytest.mark.plugin_unit
def test_profiles_write_helpers_and_set_active(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    lock1 = profiles_write._get_plugin_lock("demo")
    lock2 = profiles_write._get_plugin_lock("demo")
    assert isinstance(lock1, threading.Lock().__class__)
    assert lock1 is lock2

    monkeypatch.setattr(profiles_write, "tomllib", None)
    monkeypatch.setattr(profiles_write, "tomli_w", None)
    with pytest.raises(HTTPException):
        profiles_write._require_toml_read_write()

    monkeypatch.setattr(profiles_write, "tomllib", __import__("tomllib"))
    data = profiles_write._load_profiles_file_for_update(
        plugin_id="demo",
        profiles_path=tmp_path / "missing.toml",
        op="x",
    )
    assert data == {}

    cfg, files = profiles_write._normalize_profiles_config({"config_profiles": {"files": {"dev": "a.toml", 1: "x"}}})
    assert cfg["files"] == {"dev": "a.toml"}
    assert files == {"dev": "a.toml"}

    # set_active_profile branches
    plugin_dir = tmp_path / "p1"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text("[plugin]\nid='p1'\n", encoding="utf-8")
    monkeypatch.setattr(profiles_write, "tomli_w", object())
    monkeypatch.setattr(profiles_write, "get_plugin_config_path", lambda _pid: config_path)

    with pytest.raises(HTTPException):
        profiles_write.set_active_profile(plugin_id="p1", profile_name="")
    with pytest.raises(HTTPException):
        profiles_write.set_active_profile(plugin_id="p1", profile_name="dev")

    profiles_path = plugin_dir / "profiles.toml"
    profiles_path.write_text("[config_profiles]\n[config_profiles.files]\ndev='profiles/dev.toml'\n", encoding="utf-8")
    monkeypatch.setattr(
        profiles_write,
        "_load_profiles_file_for_update",
        lambda **_: {"config_profiles": {"files": {"dev": "profiles/dev.toml"}}},
    )
    monkeypatch.setattr(profiles_write, "_atomic_dump_toml", lambda **_: None)
    monkeypatch.setattr(
        profiles_write,
        "get_profiles_state",
        lambda **_: {"plugin_id": "p1", "config_profiles": {"active": "dev"}},
    )
    out = profiles_write.set_active_profile(plugin_id="p1", profile_name="dev")
    assert out["config_profiles"]["active"] == "dev"


@pytest.mark.plugin_unit
def test_profiles_write_upsert_and_delete_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    plugin_dir = tmp_path / "p2"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text("[plugin]\nid='p2'\n", encoding="utf-8")
    profiles_path = plugin_dir / "profiles.toml"
    profiles_path.write_text("", encoding="utf-8")

    monkeypatch.setattr(profiles_write, "tomllib", __import__("tomllib"))
    monkeypatch.setattr(profiles_write, "tomli_w", object())
    monkeypatch.setattr(profiles_write, "get_plugin_config_path", lambda _pid: config_path)

    writes: list[Path] = []
    monkeypatch.setattr(
        profiles_write,
        "_atomic_dump_toml",
        lambda **kwargs: writes.append(kwargs["target_path"]),
    )
    monkeypatch.setattr(
        profiles_write,
        "get_profile_config",
        lambda **kwargs: {"plugin_id": kwargs["plugin_id"], "profile": {"name": kwargs["profile_name"], "exists": True}},
    )
    monkeypatch.setattr(
        profiles_write,
        "_load_profiles_file_for_update",
        lambda **kwargs: {"config_profiles": {"files": {}}},
    )
    monkeypatch.setattr(
        profiles_write,
        "resolve_profile_path",
        lambda raw, base: (base / raw).resolve(),
    )

    out = profiles_write.upsert_profile_config(
        plugin_id="p2",
        profile_name="dev",
        config={"runtime": {"enabled": True}},
        make_active=True,
    )
    assert out["profile"]["name"] == "dev"
    assert any(p.name == "profiles.toml" for p in writes)

    # delete missing profiles.toml path
    profiles_path.unlink(missing_ok=True)
    out_del = profiles_write.delete_profile_config(plugin_id="p2", profile_name="dev")
    assert out_del["removed"] is False

    # delete existing and active cleanup
    profiles_path.write_text("x=1\n", encoding="utf-8")
    monkeypatch.setattr(
        profiles_write,
        "_load_profiles_file_for_update",
        lambda **kwargs: {"config_profiles": {"active": "dev", "files": {"dev": "profiles/dev.toml"}}},
    )
    out_del2 = profiles_write.delete_profile_config(plugin_id="p2", profile_name="dev")
    assert out_del2["removed"] is True


@pytest.mark.plugin_unit
def test_profiles_write_upsert_error_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    plugin_dir = tmp_path / "p3"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text("[plugin]\nid='p3'\n", encoding="utf-8")

    monkeypatch.setattr(profiles_write, "tomllib", __import__("tomllib"))
    monkeypatch.setattr(profiles_write, "tomli_w", object())
    monkeypatch.setattr(profiles_write, "get_plugin_config_path", lambda _pid: config_path)
    monkeypatch.setattr(
        profiles_write,
        "_load_profiles_file_for_update",
        lambda **kwargs: {"config_profiles": {"files": {}}},
    )

    with pytest.raises(HTTPException):
        profiles_write.upsert_profile_config(plugin_id="p3", profile_name="", config={}, make_active=None)
    with pytest.raises(HTTPException):
        profiles_write.upsert_profile_config(
            plugin_id="p3",
            profile_name="dev",
            config={"plugin": {"id": "x"}},
            make_active=None,
        )

    monkeypatch.setattr(profiles_write, "resolve_profile_path", lambda raw, base: None)
    with pytest.raises(HTTPException):
        profiles_write.upsert_profile_config(
            plugin_id="p3",
            profile_name="dev",
            config={"runtime": {"x": 1}},
            make_active=None,
        )

    monkeypatch.setattr(profiles_write, "resolve_profile_path", lambda raw, base: base / raw)
    monkeypatch.setattr(Path, "mkdir", lambda self, **kwargs: (_ for _ in ()).throw(OSError("mkdir")))
    with pytest.raises(HTTPException):
        profiles_write.upsert_profile_config(
            plugin_id="p3",
            profile_name="dev",
            config={"runtime": {"x": 1}},
            make_active=None,
        )


import builtins
import importlib
import io
import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import HTTPException

import plugin.server.infrastructure.config_locking as config_locking
import plugin.server.infrastructure.config_paths as config_paths
import plugin.server.infrastructure.config_profiles as config_profiles
import plugin.server.infrastructure.config_profiles_write as config_profiles_write
import plugin.server.infrastructure.config_storage as config_storage
import plugin.server.infrastructure.config_toml as config_toml
import plugin.server.infrastructure.config_updates as config_updates


def _reload_with_import(
    module: ModuleType,
    *,
    fake_import,
    platform: str | None = None,
) -> ModuleType:
    original_import = builtins.__import__
    original_platform = sys.platform
    try:
        builtins.__import__ = fake_import  # type: ignore[assignment]
        if platform is not None:
            sys.platform = platform
        return importlib.reload(module)
    finally:
        builtins.__import__ = original_import  # type: ignore[assignment]
        sys.platform = original_platform


@pytest.mark.plugin_unit
def test_import_fallback_branches_for_locking_toml_profiles_and_profiles_write() -> None:
    orig_import = builtins.__import__

    def _imp_toml_none(name, *a, **k):  # type: ignore[no-untyped-def]
        if name in {"tomllib", "tomli", "tomli_w"}:
            raise ImportError(name)
        return orig_import(name, *a, **k)

    reloaded_toml = _reload_with_import(config_toml, fake_import=_imp_toml_none)
    assert reloaded_toml._toml_reader is None
    assert reloaded_toml._toml_writer is None

    def _imp_tomli_fallback(name, *a, **k):  # type: ignore[no-untyped-def]
        if name == "tomllib":
            raise ImportError(name)
        if name == "tomli":
            return SimpleNamespace(load=lambda fp: {"x": 1}, loads=lambda text: {"x": 1})
        if name == "tomli_w":
            raise ImportError(name)
        return orig_import(name, *a, **k)

    reloaded_toml_fallback = _reload_with_import(config_toml, fake_import=_imp_tomli_fallback)
    assert reloaded_toml_fallback._toml_reader is not None

    reloaded_profiles = _reload_with_import(config_profiles, fake_import=_imp_toml_none)
    assert reloaded_profiles.tomllib is None

    reloaded_profiles_write = _reload_with_import(config_profiles_write, fake_import=_imp_toml_none)
    assert reloaded_profiles_write.tomllib is None
    assert reloaded_profiles_write.tomli_w is None

    def _imp_win_msvcrt_none(name, *a, **k):  # type: ignore[no-untyped-def]
        if name == "msvcrt":
            raise ImportError(name)
        return orig_import(name, *a, **k)

    reloaded_lock_win = _reload_with_import(config_locking, fake_import=_imp_win_msvcrt_none, platform="win32")
    assert reloaded_lock_win._msvcrt is None
    assert reloaded_lock_win._fcntl is None

    def _imp_linux_fcntl_none(name, *a, **k):  # type: ignore[no-untyped-def]
        if name == "fcntl":
            raise ImportError(name)
        return orig_import(name, *a, **k)

    reloaded_lock_linux = _reload_with_import(config_locking, fake_import=_imp_linux_fcntl_none, platform="linux")
    assert reloaded_lock_linux._msvcrt is None
    assert reloaded_lock_linux._fcntl is None

    # restore normal modules to avoid side effects for following tests
    importlib.reload(config_toml)
    importlib.reload(config_profiles)
    importlib.reload(config_profiles_write)
    importlib.reload(config_locking)


@pytest.mark.plugin_unit
def test_config_paths_else_branch_without_is_relative_to(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class _Resolved:
        def __init__(self, text: str) -> None:
            self._text = text

        def __str__(self) -> str:
            return self._text

    root = tmp_path / "plugins"
    cfg = root / "demo" / "plugin.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("x=1\n", encoding="utf-8")
    monkeypatch.setattr(config_paths, "PLUGIN_CONFIG_ROOT", root)

    original_resolve = Path.resolve

    def _fake_resolve(self):  # type: ignore[no-untyped-def]
        s = str(self)
        if s.endswith("demo/plugin.toml"):
            return _Resolved("/outside/demo/plugin.toml")
        if s == str(root):
            return _Resolved(str(root))
        return original_resolve(self)

    monkeypatch.setattr(Path, "resolve", _fake_resolve)
    with pytest.raises(HTTPException) as exc_info:
        config_paths.get_plugin_config_path("demo")
    assert exc_info.value.status_code == 400


@pytest.mark.plugin_unit
def test_config_storage_fsync_parent_dir_error_branches(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    p = tmp_path / "a.toml"
    p.write_text("x=1\n", encoding="utf-8")

    monkeypatch.setattr(config_storage.os, "open", lambda *a, **k: (_ for _ in ()).throw(AttributeError("no open")))
    config_storage._fsync_parent_dir(p)

    monkeypatch.setattr(config_storage.os, "open", lambda *a, **k: 3)
    monkeypatch.setattr(config_storage.os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("fsync")))
    monkeypatch.setattr(config_storage.os, "close", lambda fd: None)
    config_storage._fsync_parent_dir(p)


@pytest.mark.plugin_unit
def test_config_updates_fill_plugin_protected_fields_remaining_branches() -> None:
    # plugin section has non-string key -> only string keys copied
    incoming = {"plugin": {1: "x", "name": "ok"}}
    out = config_updates._fill_plugin_protected_fields(
        current_config={"plugin": "bad"},
        incoming_config=incoming,  # type: ignore[arg-type]
    )
    assert out["plugin"]["name"] == "ok"
    assert "id" not in out["plugin"]


@pytest.mark.plugin_unit
def test_config_toml_import_time_fallback_lines_are_exercised() -> None:
    # sanity use to keep reloaded module referenced in this test file
    assert hasattr(config_toml, "require_toml_reader")


@pytest.mark.plugin_unit
def test_config_profiles_missing_and_error_branches(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = tmp_path / "plugin.toml"
    cfg.write_text("[plugin]\nid='demo'\n", encoding="utf-8")
    profiles_file = tmp_path / "profiles.toml"
    profiles_file.write_text("x=1\n", encoding="utf-8")

    # load_profiles_cfg_from_file: tomllib missing
    monkeypatch.setattr(config_profiles, "tomllib", None)
    assert config_profiles.load_profiles_cfg_from_file("demo", cfg) is None

    # load_profiles_cfg_from_file: load error
    class _TomlBad:
        @staticmethod
        def load(_fp):  # noqa: ANN001
            raise ValueError("bad")

    monkeypatch.setattr(config_profiles, "tomllib", _TomlBad())
    assert config_profiles.load_profiles_cfg_from_file("demo", cfg) is None

    # load_profiles_cfg_from_file: root not mapping
    class _TomlNotMap:
        @staticmethod
        def load(_fp):  # noqa: ANN001
            return []

    monkeypatch.setattr(config_profiles, "tomllib", _TomlNotMap())
    assert config_profiles.load_profiles_cfg_from_file("demo", cfg) is None

    # load_profiles_cfg_from_file: config_profiles missing
    class _TomlNoCfg:
        @staticmethod
        def load(_fp):  # noqa: ANN001
            return {"x": 1}

    monkeypatch.setattr(config_profiles, "tomllib", _TomlNoCfg())
    assert config_profiles.load_profiles_cfg_from_file("demo", cfg) is None

    # _load_base_config_from_file: tomllib missing
    monkeypatch.setattr(config_profiles, "tomllib", None)
    assert config_profiles._load_base_config_from_file(cfg) is None

    # _load_base_config_from_file: load error
    monkeypatch.setattr(config_profiles, "tomllib", _TomlBad())
    assert config_profiles._load_base_config_from_file(cfg) is None

    # _load_base_config_from_file: not mapping
    monkeypatch.setattr(config_profiles, "tomllib", _TomlNotMap())
    assert config_profiles._load_base_config_from_file(cfg) is None

    # _resolve_profiles_cfg: no profile file + base not mapping => None
    monkeypatch.setattr(config_profiles, "load_profiles_cfg_from_file", lambda plugin_id, config_path: None)
    monkeypatch.setattr(config_profiles, "_load_base_config_from_file", lambda config_path: None)
    assert config_profiles._resolve_profiles_cfg(plugin_id="demo", config_path=cfg, base_config=None) is None
    assert config_profiles._extract_profiles_cfg_from_base_config({"plugin": "bad"}) is None

    # resolve_profile_path broad exception handler
    monkeypatch.setattr(config_profiles.os.path, "expandvars", lambda s: (_ for _ in ()).throw(TypeError("bad")))
    assert config_profiles.resolve_profile_path("x.toml", tmp_path) is None


@pytest.mark.plugin_unit
def test_config_profiles_apply_user_profile_remaining_branches(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = tmp_path / "plugin.toml"
    cfg.write_text("[plugin]\nid='demo'\n", encoding="utf-8")
    base = {"runtime": {"a": 1}}

    # base not mapping
    out_non_map = config_profiles.apply_user_config_profiles(  # type: ignore[arg-type]
        plugin_id="demo",
        base_config=[],  # type: ignore[arg-type]
        config_path=cfg,
    )
    assert out_non_map == []

    monkeypatch.setattr(config_profiles, "_resolve_profiles_cfg", lambda **kwargs: None)
    assert config_profiles.apply_user_config_profiles(plugin_id="demo", base_config=base, config_path=cfg) == base

    monkeypatch.setattr(config_profiles, "_resolve_profiles_cfg", lambda **kwargs: {"active": ""})
    assert config_profiles.apply_user_config_profiles(plugin_id="demo", base_config=base, config_path=cfg) == base

    monkeypatch.setattr(config_profiles, "_resolve_profiles_cfg", lambda **kwargs: {"active": "dev", "files": []})
    assert config_profiles.apply_user_config_profiles(plugin_id="demo", base_config=base, config_path=cfg) == base

    monkeypatch.setattr(config_profiles, "_resolve_profiles_cfg", lambda **kwargs: {"active": "2", "files": {"2": ""}})
    assert config_profiles.apply_user_config_profiles(plugin_id="demo", base_config=base, config_path=cfg) == base

    monkeypatch.setattr(config_profiles, "_resolve_profiles_cfg", lambda **kwargs: {"active": "dev", "files": {"dev": "profiles/dev.toml"}})
    monkeypatch.setattr(config_profiles, "resolve_profile_path", lambda p, b: None)
    assert config_profiles.apply_user_config_profiles(plugin_id="demo", base_config=base, config_path=cfg) == base

    monkeypatch.setattr(config_profiles, "resolve_profile_path", lambda p, b: b / "missing.toml")
    assert config_profiles.apply_user_config_profiles(plugin_id="demo", base_config=base, config_path=cfg) == base

    profile = tmp_path / "dev.toml"
    profile.write_text("x=1\n", encoding="utf-8")
    monkeypatch.setattr(config_profiles, "resolve_profile_path", lambda p, b: profile)
    monkeypatch.setattr(config_profiles, "tomllib", None)
    assert config_profiles.apply_user_config_profiles(plugin_id="demo", base_config=base, config_path=cfg) == base

    class _TomlBad:
        @staticmethod
        def load(_fp):  # noqa: ANN001
            raise ValueError("bad")

    monkeypatch.setattr(config_profiles, "tomllib", _TomlBad())
    assert config_profiles.apply_user_config_profiles(plugin_id="demo", base_config=base, config_path=cfg) == base

    class _TomlNotMap:
        @staticmethod
        def load(_fp):  # noqa: ANN001
            return []

    monkeypatch.setattr(config_profiles, "tomllib", _TomlNotMap())
    assert config_profiles.apply_user_config_profiles(plugin_id="demo", base_config=base, config_path=cfg) == base

    class _TomlOverlay:
        @staticmethod
        def load(_fp):  # noqa: ANN001
            return {"other": {"b": 2}}

    monkeypatch.setattr(config_profiles, "tomllib", _TomlOverlay())
    merged = config_profiles.apply_user_config_profiles(plugin_id="demo", base_config=base, config_path=cfg)
    assert merged["other"]["b"] == 2

    class _TomlOverlayScalar:
        @staticmethod
        def load(_fp):  # noqa: ANN001
            return {"priority": 7}

    monkeypatch.setattr(config_profiles, "tomllib", _TomlOverlayScalar())
    merged_scalar = config_profiles.apply_user_config_profiles(plugin_id="demo", base_config=base, config_path=cfg)
    assert merged_scalar["priority"] == 7


@pytest.mark.plugin_unit
def test_config_profiles_state_and_profile_config_remaining_branches(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = tmp_path / "plugin.toml"
    cfg.write_text("[plugin]\nid='demo'\n", encoding="utf-8")

    monkeypatch.setattr(config_profiles, "tomllib", None)
    with pytest.raises(HTTPException):
        config_profiles.get_profiles_state(plugin_id="demo", config_path=cfg)
    with pytest.raises(HTTPException):
        config_profiles.get_profile_config(plugin_id="demo", profile_name="dev", config_path=cfg)

    class _TomlReader:
        @staticmethod
        def load(fp):  # noqa: ANN001
            return {"k": "v"}

    monkeypatch.setattr(config_profiles, "tomllib", _TomlReader())
    monkeypatch.setattr(config_profiles, "_resolve_profiles_cfg", lambda **kwargs: {"active": "dev", "files": {"dev": 1, "x": "p.toml"}})
    monkeypatch.setattr(config_profiles, "resolve_profile_path", lambda p, b: b / str(p))
    state = config_profiles.get_profiles_state(plugin_id="demo", config_path=cfg)
    assert "x" in state["config_profiles"]["files"]  # type: ignore[index]

    with pytest.raises(HTTPException):
        config_profiles.get_profile_config(plugin_id="demo", profile_name="", config_path=cfg)
    with pytest.raises(HTTPException):
        config_profiles.get_profile_config(plugin_id="demo", profile_name="../bad", config_path=cfg)

    # resolve None path branch
    monkeypatch.setattr(config_profiles, "_resolve_profiles_cfg", lambda **kwargs: {"files": {"dev": "x.toml"}})
    monkeypatch.setattr(config_profiles, "resolve_profile_path", lambda p, b: None)
    out_none_path = config_profiles.get_profile_config(plugin_id="demo", profile_name="dev", config_path=cfg)
    assert out_none_path["profile"]["resolved_path"] is None  # type: ignore[index]

    # raw_path fallback branch -> profiles/{name}.toml
    monkeypatch.setattr(config_profiles, "_resolve_profiles_cfg", lambda **kwargs: None)
    monkeypatch.setattr(config_profiles, "resolve_profile_path", lambda p, b: b / p)
    out_default = config_profiles.get_profile_config(plugin_id="demo", profile_name="qa", config_path=cfg)
    assert out_default["profile"]["path"] == "profiles/qa.toml"  # type: ignore[index]


@pytest.mark.plugin_unit
def test_config_profiles_write_remaining_branches(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = tmp_path / "plugin.toml"
    cfg.write_text("[plugin]\nid='demo'\n", encoding="utf-8")
    profiles = tmp_path / "profiles.toml"
    profiles.write_text("x=1\n", encoding="utf-8")

    monkeypatch.setattr(config_profiles_write, "tomllib", None)
    with pytest.raises(HTTPException):
        config_profiles_write._load_profiles_file_for_update(plugin_id="demo", profiles_path=profiles, op="x")

    class _TomlBad:
        @staticmethod
        def load(fp):  # noqa: ANN001
            raise ValueError("bad")

    monkeypatch.setattr(config_profiles_write, "tomllib", _TomlBad())
    with pytest.raises(HTTPException):
        config_profiles_write._load_profiles_file_for_update(plugin_id="demo", profiles_path=profiles, op="x")

    class _TomlList:
        @staticmethod
        def load(fp):  # noqa: ANN001
            return []

    monkeypatch.setattr(config_profiles_write, "tomllib", _TomlList())
    assert config_profiles_write._load_profiles_file_for_update(plugin_id="demo", profiles_path=profiles, op="x") == {}
    cfg_obj, files_obj = config_profiles_write._normalize_profiles_config({})
    assert cfg_obj["files"] == {}
    assert files_obj == {}

    monkeypatch.setattr(config_profiles_write, "tomli_w", None)
    with pytest.raises(HTTPException):
        config_profiles_write._atomic_dump_toml(target_path=profiles, payload={}, prefix=".x")

    original_mkstemp = config_profiles_write.tempfile.mkstemp
    original_fsync = config_profiles_write.os.fsync
    original_open = config_profiles_write.os.open
    original_close = config_profiles_write.os.close
    original_replace = config_profiles_write.os.replace
    monkeypatch.setattr(config_profiles_write.tempfile, "mkstemp", lambda **kwargs: (_ for _ in ()).throw(OSError("mk")))
    monkeypatch.setattr(config_profiles_write, "tomli_w", SimpleNamespace(dump=lambda obj, fp: fp.write(b"x=1\n")))
    with pytest.raises(HTTPException):
        config_profiles_write._atomic_dump_toml(target_path=profiles, payload={}, prefix=".x")

    temp = tmp_path / "temp.toml"
    temp.write_text("", encoding="utf-8")
    fd = os.open(temp, os.O_WRONLY)
    monkeypatch.setattr(config_profiles_write.tempfile, "mkstemp", lambda **kwargs: (fd, str(temp)))
    monkeypatch.setattr(config_profiles_write.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("rep")))
    monkeypatch.setattr(config_profiles_write.Path, "exists", lambda self: True)
    monkeypatch.setattr(config_profiles_write.Path, "unlink", lambda self: (_ for _ in ()).throw(OSError("unlink")))
    with pytest.raises(HTTPException):
        config_profiles_write._atomic_dump_toml(target_path=profiles, payload={}, prefix=".x")

    # set_active missing profile_name + not found in files
    monkeypatch.setattr(config_profiles_write, "tomllib", __import__("tomllib"))
    monkeypatch.setattr(config_profiles_write, "tomli_w", object())
    monkeypatch.setattr(config_profiles_write, "get_plugin_config_path", lambda plugin_id: cfg)
    monkeypatch.setattr(config_profiles_write.os, "open", lambda *a, **k: (_ for _ in ()).throw(AttributeError("no")))
    config_profiles_write._fsync_parent_dir(profiles)
    monkeypatch.setattr(config_profiles_write.os, "open", lambda *a, **k: 3)
    monkeypatch.setattr(config_profiles_write.os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("fsync")))
    monkeypatch.setattr(config_profiles_write.os, "close", lambda fd: None)
    config_profiles_write._fsync_parent_dir(profiles)

    # success path should hit _fsync_parent_dir in _atomic_dump_toml
    class _TomliW:
        @staticmethod
        def dump(obj, fp):  # noqa: ANN001
            fp.write(b"x=1\n")

    monkeypatch.setattr(config_profiles_write, "tomli_w", _TomliW())
    monkeypatch.setattr(config_profiles_write.os, "replace", lambda src, dst: None)
    monkeypatch.setattr(config_profiles_write, "_fsync_parent_dir", lambda path: None)
    monkeypatch.setattr(config_profiles_write.tempfile, "mkstemp", original_mkstemp)
    monkeypatch.setattr(config_profiles_write.os, "fsync", original_fsync)
    monkeypatch.setattr(config_profiles_write.os, "open", original_open)
    monkeypatch.setattr(config_profiles_write.os, "close", original_close)
    monkeypatch.setattr(config_profiles_write.os, "replace", original_replace)
    config_profiles_write._atomic_dump_toml(target_path=profiles, payload={"x": 1}, prefix=".ok_")

    with pytest.raises(HTTPException):
        config_profiles_write.delete_profile_config(plugin_id="demo", profile_name="")

    with pytest.raises(HTTPException):
        config_profiles_write.set_active_profile(plugin_id="demo", profile_name="")

    monkeypatch.setattr(config_profiles_write, "_load_profiles_file_for_update", lambda **kwargs: {"config_profiles": {"files": {}}})
    with pytest.raises(HTTPException):
        config_profiles_write.set_active_profile(plugin_id="demo", profile_name="dev")
