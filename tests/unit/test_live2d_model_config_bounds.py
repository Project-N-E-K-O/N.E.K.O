"""Live2D config read/write endpoints stay inside the model folder.

* Config files (``.model3.json``, ``.cdi3.json``, ``parameters.json``) whose real
  path leaves the model folder are refused with a 4xx and are neither read nor written.
* File references posted to ``/model_config`` must be model-relative.
* The motion / expression listing walk stops at a depth / entry limit.

Everything runs on synthetic files under ``tmp_path``.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from main_routers import live2d_router


def _link(target: Path, link: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError) as e:  # e.g. Windows without symlink privilege
        pytest.skip(f"symlinks unavailable: {e}")


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


_OUTSIDE_CONFIG = {
    "Version": 3,
    "Marker": "outside",
    "FileReferences": {"Motions": {}, "Expressions": []},
    "EmotionMapping": {"motions": {"happy": ["outside.motion3.json"]}, "expressions": {}},
}
_INSIDE_CONFIG = {
    "Version": 3,
    "Marker": "inside",
    "FileReferences": {"Motions": {}, "Expressions": []},
}


class _FakeRequest:
    def __init__(self, payload) -> None:
        self._payload = payload

    async def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def _clear_model_config_write_locks():
    live2d_router._MODEL_CONFIG_WRITE_LOCKS.clear()
    yield
    live2d_router._MODEL_CONFIG_WRITE_LOCKS.clear()


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    root = tmp_path / "outside"
    _write(root / "o.model3.json", _OUTSIDE_CONFIG)
    _write(root / "o.cdi3.json", {"Parameters": [{"Id": "OutsideParam"}]})
    _write(root / "parameters.json", {"OutsideParam": 1})
    return root


@pytest.fixture
def model_dir(monkeypatch, tmp_path: Path) -> Path:
    path = tmp_path / "user_live2d" / "m"
    path.mkdir(parents=True)
    monkeypatch.setattr(
        live2d_router, "find_model_directory", lambda _name: (str(path), "/user_live2d")
    )
    return path


def _status(result) -> int:
    return getattr(result, "status_code", 200)


def _body(result) -> dict:
    if hasattr(result, "body"):
        return json.loads(result.body)
    return result


def _snapshot(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


# --- config files whose real path leaves the model folder -------------------------


def test_get_model_config_refuses_config_link_leaving_model_dir(model_dir, outside):
    _link(outside / "o.model3.json", model_dir / "m.model3.json")
    result = live2d_router.get_model_config("m")
    assert _status(result) == 403
    assert "outside" not in result.body.decode("utf-8")


def test_get_emotion_mapping_refuses_config_link_leaving_model_dir(model_dir, outside):
    _link(outside / "o.model3.json", model_dir / "m.model3.json")
    result = live2d_router.get_emotion_mapping("m")
    assert _status(result) == 403
    assert "outside" not in result.body.decode("utf-8")


def test_get_model_parameters_refuses_cdi3_link_leaving_model_dir(model_dir, outside):
    _link(outside / "o.cdi3.json", model_dir / "m.cdi3.json")
    result = live2d_router.get_model_parameters("m")
    assert _status(result) == 403
    assert "OutsideParam" not in result.body.decode("utf-8")


def test_load_model_parameters_refuses_link_leaving_model_dir(model_dir, outside):
    _link(outside / "parameters.json", model_dir / "parameters.json")
    result = live2d_router.load_model_parameters("m")
    assert _status(result) == 403
    assert "OutsideParam" not in result.body.decode("utf-8")


@pytest.mark.parametrize(
    "handler, payload",
    [
        ("update_model_config", {"FileReferences": {"Motions": {"Idle": [{"File": "a.motion3.json"}]}}}),
        ("update_emotion_mapping", {"motions": {"happy": ["a.motion3.json"]}, "expressions": {}}),
    ],
)
def test_config_writes_refuse_config_link_leaving_model_dir(model_dir, outside, handler, payload):
    link = model_dir / "m.model3.json"
    _link(outside / "o.model3.json", link)
    before = _snapshot(outside)

    result = asyncio.run(getattr(live2d_router, handler)("m", _FakeRequest(payload)))

    assert _status(result) == 403
    assert _snapshot(outside) == before
    # Not replaced either: the link is left as it was.
    assert link.is_symlink()
    assert sorted(p.name for p in model_dir.iterdir()) == ["m.model3.json"]


def test_save_model_parameters_refuses_link_leaving_model_dir(model_dir, outside):
    link = model_dir / "parameters.json"
    _link(outside / "parameters.json", link)
    before = _snapshot(outside)

    result = asyncio.run(
        live2d_router.save_model_parameters("m", _FakeRequest({"parameters": {"P": 2}}))
    )

    assert _status(result) == 403
    assert _snapshot(outside) == before
    assert link.is_symlink()


def test_inside_config_is_used_when_an_outside_link_sits_next_to_it(model_dir, outside):
    _write(model_dir / "m.model3.json", _INSIDE_CONFIG)
    _link(outside / "o.model3.json", model_dir / "z.model3.json")
    result = live2d_router.get_model_config("m")
    assert _status(result) == 200
    assert result["config"]["Marker"] == "inside"


# --- normal reads and writes keep working ------------------------------------------


def test_regular_config_reads_and_writes_still_work(model_dir):
    _write(model_dir / "m.model3.json", _INSIDE_CONFIG)
    _write(model_dir / "m.cdi3.json", {"Parameters": [{"Id": "ParamA"}]})

    assert live2d_router.get_model_config("m")["config"]["Marker"] == "inside"
    assert live2d_router.get_emotion_mapping("m")["success"] is True
    assert live2d_router.get_model_parameters("m")["parameters"][0]["id"] == "ParamA"

    motions = {"Idle": [{"File": "motions/idle.motion3.json"}]}
    result = asyncio.run(
        live2d_router.update_model_config("m", _FakeRequest({"FileReferences": {"Motions": motions}}))
    )
    assert result["success"] is True
    saved = json.loads((model_dir / "m.model3.json").read_text(encoding="utf-8"))
    assert saved["FileReferences"]["Motions"] == motions

    result = asyncio.run(
        live2d_router.update_emotion_mapping(
            "m", _FakeRequest({"motions": {"happy": ["motions\\idle.motion3.json"]}, "expressions": {}})
        )
    )
    assert result["success"] is True
    saved = json.loads((model_dir / "m.model3.json").read_text(encoding="utf-8"))
    assert saved["FileReferences"]["Motions"] == {"happy": [{"File": "motions/idle.motion3.json"}]}

    result = asyncio.run(
        live2d_router.save_model_parameters("m", _FakeRequest({"parameters": {"ParamA": 0.5}}))
    )
    assert result["success"] is True
    assert live2d_router.load_model_parameters("m")["parameters"] == {"ParamA": 0.5}


def test_inside_config_link_and_linked_model_folder_still_work(monkeypatch, tmp_path):
    real_dir = tmp_path / "elsewhere" / "m"
    _write(real_dir / "configs" / "real.model3.json", _INSIDE_CONFIG)
    _link(real_dir / "configs" / "real.model3.json", real_dir / "m.model3.json")
    linked = tmp_path / "user_live2d" / "m"
    linked.parent.mkdir(parents=True)
    _link(real_dir, linked)
    monkeypatch.setattr(
        live2d_router, "find_model_directory", lambda _name: (str(linked), "/user_live2d")
    )
    assert live2d_router.get_model_config("m")["config"]["Marker"] == "inside"


@pytest.mark.parametrize("name", ["..", "../m", "/etc", "a\\..\\b"])
def test_model_names_with_path_syntax_are_refused(name):
    # Real find_model_directory: path syntax is refused before any directory is touched.
    assert _status(live2d_router.get_model_config(name)) == 404
    assert _status(asyncio.run(live2d_router.update_model_config(name, _FakeRequest({})))) == 404


# --- file references posted to /model_config ---------------------------------------


_UNSAFE_REFS = ["../x.motion3.json", "motions/../../x.motion3.json", "/abs/x.motion3.json",
                "C:/x.motion3.json", "..\\x.motion3.json", "C:x.motion3.json",
                "http://example.invalid/x.motion3.json", "file:///x.motion3.json",
                "//example.invalid/x.motion3.json", "%2e%2e/%2e%2e/other/x.motion3.json",
                "motions%2f..%2f..%2fx.motion3.json", "%2Fabs/x.motion3.json",
                ".\t./x.motion3.json", "..\n/x.motion3.json", " ../x.motion3.json", "../x.motion3.json ",
                ".%09./x.motion3.json", "x\x00.motion3.json",
                "motions/x.motion3.json#y", "motions/x.motion3.json?y=1", "motions/x%23y.motion3.json",
                "motions/x%3Fy.motion3.json"]


def _by_id_model(monkeypatch, tmp_path: Path) -> Path:
    item_dir = tmp_path / "workshop" / "123"
    _write(item_dir / "m.model3.json", _INSIDE_CONFIG)
    monkeypatch.setattr(
        live2d_router, "find_workshop_item_by_id", lambda _id: (str(item_dir), "/workshop")
    )
    return item_dir


@pytest.mark.parametrize("ref", _UNSAFE_REFS)
@pytest.mark.parametrize("where", ["Motions", "Expressions", "Sound"])
def test_model_config_post_refuses_references_leaving_model_dir(monkeypatch, tmp_path, model_dir, ref, where):
    _write(model_dir / "m.model3.json", _INSIDE_CONFIG)
    item_dir = _by_id_model(monkeypatch, tmp_path)
    if where == "Motions":
        refs = {"Motions": {"Idle": [{"File": "ok.motion3.json"}, {"File": ref}]}}
    elif where == "Expressions":
        refs = {"Expressions": [{"Name": "e", "File": ref}]}
    else:
        refs = {"Motions": {"Idle": [{"File": "ok.motion3.json", "Sound": ref}]}}

    for call, config in (
        (lambda: live2d_router.update_model_config("m", _FakeRequest({"FileReferences": refs})),
         model_dir / "m.model3.json"),
        (lambda: live2d_router.update_model_config_by_id("123", _FakeRequest({"FileReferences": refs})),
         item_dir / "m.model3.json"),
    ):
        before = config.read_bytes()
        result = asyncio.run(call())
        assert _status(result) == 400
        assert config.read_bytes() == before


def test_model_config_by_id_post_accepts_model_relative_references(monkeypatch, tmp_path):
    item_dir = _by_id_model(monkeypatch, tmp_path)
    expressions = [{"Name": "smile", "File": "expressions/smile.exp3.json"}]
    result = asyncio.run(
        live2d_router.update_model_config_by_id(
            "123", _FakeRequest({"FileReferences": {"Expressions": expressions}})
        )
    )
    assert result["success"] is True
    saved = json.loads((item_dir / "m.model3.json").read_text(encoding="utf-8"))
    assert saved["FileReferences"]["Expressions"] == expressions


@pytest.mark.parametrize("ref", _UNSAFE_REFS)
@pytest.mark.parametrize("section", ["motions", "expressions", "常驻"])
def test_emotion_mapping_post_refuses_references_leaving_model_dir(model_dir, ref, section):
    _write(model_dir / "m.model3.json", _INSIDE_CONFIG)
    before = (model_dir / "m.model3.json").read_bytes()
    if section == "常驻":
        # Motions in this group are not written to FileReferences, but the request is kept as EmotionMapping.
        payload = {"motions": {"常驻": [ref]}, "expressions": {}}
    else:
        payload = {"motions": {}, "expressions": {}}
        payload[section] = {"happy": ["ok.motion3.json", ref]}
    result = asyncio.run(live2d_router.update_emotion_mapping("m", _FakeRequest(payload)))
    assert _status(result) == 400
    assert (model_dir / "m.model3.json").read_bytes() == before


_BAD_EMOTION_SECTIONS = [
    {"happy": "%2e%2e%2foutside.motion3.json"},
    {"happy": ".."},
    {"happy": "/"},
    {"happy": {"%2e%2e%2foutside.motion3.json": 1}},
    {"happy": [1]},
    {"happy": [{"File": "../x.motion3.json"}]},
    ["../x.motion3.json"],
    "../x.motion3.json",
]


@pytest.mark.parametrize("value", _BAD_EMOTION_SECTIONS)
@pytest.mark.parametrize("section", ["motions", "expressions"])
def test_emotion_mapping_post_refuses_unexpected_shapes(model_dir, section, value):
    # The raw request is stored as EmotionMapping, so a section that is not
    # {group: [str, ...]} is refused as a whole instead of being stored unchecked.
    _write(model_dir / "m.model3.json", _INSIDE_CONFIG)
    before = (model_dir / "m.model3.json").read_bytes()
    payload = {"motions": {}, "expressions": {}}
    payload[section] = value
    result = asyncio.run(live2d_router.update_emotion_mapping("m", _FakeRequest(payload)))
    assert _status(result) == 400
    assert (model_dir / "m.model3.json").read_bytes() == before


def test_emotion_mapping_post_refuses_non_object_request(model_dir):
    _write(model_dir / "m.model3.json", _INSIDE_CONFIG)
    before = (model_dir / "m.model3.json").read_bytes()
    result = asyncio.run(live2d_router.update_emotion_mapping("m", _FakeRequest(["../x.motion3.json"])))
    assert _status(result) == 400
    assert (model_dir / "m.model3.json").read_bytes() == before


def test_emotion_mapping_post_accepts_missing_sections(model_dir):
    _write(model_dir / "m.model3.json", _INSIDE_CONFIG)
    payload = {"expressions": {"happy": ["expressions/smile.exp3.json"]}}
    result = asyncio.run(live2d_router.update_emotion_mapping("m", _FakeRequest(payload)))
    assert result["success"] is True
    saved = json.loads((model_dir / "m.model3.json").read_text(encoding="utf-8"))
    assert saved["EmotionMapping"] == payload


_BAD_SHAPES = [
    "FileReferences",
    ["FileReferences"],
    {"FileReferences": "x"},
    {"FileReferences": {"Motions": None}},
    {"FileReferences": {"Expressions": None}},
    {"FileReferences": {"Motions": {"Idle": [{}]}}},
    {"FileReferences": {"Expressions": [{}]}},
    {"FileReferences": {"Expressions": [{"Name": "e", "File": None}]}},
    {"FileReferences": {"Motions": ["../x.motion3.json"]}},
    {"FileReferences": {"Motions": {"Idle": {"File": "../x.motion3.json"}}}},
    {"FileReferences": {"Motions": {"Idle": ["../x.motion3.json"]}}},
    {"FileReferences": {"Expressions": {"e": {"File": "../../x.exp3.json"}}}},
    {"FileReferences": {"Expressions": [{"Name": "e", "File": ["../x.exp3.json"]}]}},
    {"FileReferences": {"Motions": {"Idle": [{"File": "ok.motion3.json", "Sound": {"p": "../x.wav"}}]}}},
]


@pytest.mark.parametrize("payload", _BAD_SHAPES)
def test_model_config_post_refuses_unexpected_shapes(monkeypatch, tmp_path, model_dir, payload):
    _write(model_dir / "m.model3.json", _INSIDE_CONFIG)
    item_dir = _by_id_model(monkeypatch, tmp_path)
    for call, config in (
        (lambda: live2d_router.update_model_config("m", _FakeRequest(payload)), model_dir / "m.model3.json"),
        (lambda: live2d_router.update_model_config_by_id("123", _FakeRequest(payload)), item_dir / "m.model3.json"),
    ):
        before = config.read_bytes()
        result = asyncio.run(call())
        assert _status(result) == 400
        assert config.read_bytes() == before


def test_model_config_post_keeps_extra_entry_fields_and_percent_names(model_dir):
    _write(model_dir / "m.model3.json", _INSIDE_CONFIG)
    motions = {"Idle": [{"File": "motions/a%20b.motion3.json", "FadeInTime": 0.5},
                        {"File": "motions/c.motion3.json", "Sound": "sounds/c.wav"}]}
    result = asyncio.run(
        live2d_router.update_model_config("m", _FakeRequest({"FileReferences": {"Motions": motions}}))
    )
    assert result["success"] is True
    saved = json.loads((model_dir / "m.model3.json").read_text(encoding="utf-8"))
    assert saved["FileReferences"]["Motions"] == motions


# --- references resolving through links --------------------------------------------


def _ref_link_layout(base: Path, outside: Path) -> None:
    """``out`` links outside the model folder, ``inner`` links to a folder inside it."""
    _write(base / "motions" / "idle.motion3.json", {"Version": 3})
    _link(outside, base / "out")
    _link(base / "motions", base / "inner")


_LINKED_CASES = {
    "Motions": lambda ref: {"Motions": {"Idle": [{"File": ref}]}},
    "Expressions": lambda ref: {"Expressions": [{"Name": "e", "File": ref}]},
    "Sound": lambda ref: {"Motions": {"Idle": [{"File": "motions/idle.motion3.json", "Sound": ref}]}},
}


@pytest.mark.parametrize("where", list(_LINKED_CASES))
def test_model_config_post_refuses_references_through_outside_links(monkeypatch, tmp_path, model_dir, outside, where):
    _write(model_dir / "m.model3.json", _INSIDE_CONFIG)
    _ref_link_layout(model_dir, outside)
    item_dir = _by_id_model(monkeypatch, tmp_path)
    _ref_link_layout(item_dir, outside)
    refs = _LINKED_CASES[where]("out/o.model3.json")

    for call, config in (
        (lambda: live2d_router.update_model_config("m", _FakeRequest({"FileReferences": refs})),
         model_dir / "m.model3.json"),
        (lambda: live2d_router.update_model_config_by_id("123", _FakeRequest({"FileReferences": refs})),
         item_dir / "m.model3.json"),
    ):
        before = config.read_bytes()
        result = asyncio.run(call())
        assert _status(result) == 400
        assert config.read_bytes() == before


@pytest.mark.parametrize("where", list(_LINKED_CASES))
def test_model_config_post_accepts_references_through_inside_links(monkeypatch, tmp_path, model_dir, outside, where):
    _write(model_dir / "m.model3.json", _INSIDE_CONFIG)
    _ref_link_layout(model_dir, outside)
    item_dir = _by_id_model(monkeypatch, tmp_path)
    _ref_link_layout(item_dir, outside)
    refs = _LINKED_CASES[where]("inner/idle.motion3.json")

    for call, config in (
        (lambda: live2d_router.update_model_config("m", _FakeRequest({"FileReferences": refs})),
         model_dir / "m.model3.json"),
        (lambda: live2d_router.update_model_config_by_id("123", _FakeRequest({"FileReferences": refs})),
         item_dir / "m.model3.json"),
    ):
        result = asyncio.run(call())
        assert result["success"] is True
        saved = json.loads(config.read_text(encoding="utf-8"))["FileReferences"]
        for key, value in refs.items():
            assert saved[key] == value


def test_model_config_by_id_nested_config_checks_references_against_item_folder(monkeypatch, tmp_path, outside):
    # The config sits in a subfolder: references are relative to it, the boundary is the item folder.
    item_dir = tmp_path / "workshop" / "456"
    _write(item_dir / "m" / "m.model3.json", _INSIDE_CONFIG)
    _ref_link_layout(item_dir / "m", outside)
    monkeypatch.setattr(
        live2d_router, "find_workshop_item_by_id", lambda _id: (str(item_dir), "/workshop")
    )
    config = item_dir / "m" / "m.model3.json"
    ok = {"Motions": {"Idle": [{"File": "inner/idle.motion3.json"}]}}
    assert asyncio.run(
        live2d_router.update_model_config_by_id("456", _FakeRequest({"FileReferences": ok}))
    )["success"] is True
    before = config.read_bytes()
    bad = {"Motions": {"Idle": [{"File": "out/o.model3.json"}]}}
    result = asyncio.run(live2d_router.update_model_config_by_id("456", _FakeRequest({"FileReferences": bad})))
    assert _status(result) == 400
    assert config.read_bytes() == before


@pytest.mark.parametrize("section", ["motions", "expressions"])
def test_emotion_mapping_post_checks_references_through_links(model_dir, outside, section):
    _write(model_dir / "m.model3.json", _INSIDE_CONFIG)
    _ref_link_layout(model_dir, outside)
    config = model_dir / "m.model3.json"
    before = config.read_bytes()

    bad = {"motions": {}, "expressions": {}}
    bad[section] = {"happy": ["out/o.model3.json"]}
    result = asyncio.run(live2d_router.update_emotion_mapping("m", _FakeRequest(bad)))
    assert _status(result) == 400
    assert config.read_bytes() == before

    ok = {"motions": {}, "expressions": {}}
    ok[section] = {"happy": ["inner/idle.motion3.json"]}
    result = asyncio.run(live2d_router.update_emotion_mapping("m", _FakeRequest(ok)))
    assert result["success"] is True
    saved = json.loads(config.read_text(encoding="utf-8"))
    assert saved["EmotionMapping"] == ok


# --- listing walk limits -----------------------------------------------------------


def _stacked_aliases(model_dir: Path, levels: int) -> None:
    """Each level holds the next one plus an alias link to it: 2**levels paths to walk."""
    _write(model_dir / "m.model3.json", _INSIDE_CONFIG)
    current = model_dir
    for i in range(levels):
        nxt = current / f"d{i}"
        _write(nxt / f"l{i}.motion3.json", {"Version": 3})
        _link(nxt, current / f"a{i}")
        current = nxt


def _list_by_name(model_dir: Path) -> dict:
    return live2d_router.get_model_files("m")


def _list_by_id(monkeypatch, model_dir: Path) -> dict:
    monkeypatch.setattr(
        live2d_router, "find_workshop_item_by_id", lambda _id: (str(model_dir), "/workshop")
    )
    return live2d_router.get_model_files_by_id("123")


@pytest.mark.parametrize("route", ["by_name", "by_id"])
def test_stacked_aliases_stop_at_entry_limit(monkeypatch, model_dir, route):
    # 2**12 paths would be walked without the entry cap; a small cap keeps the test fast.
    monkeypatch.setattr(live2d_router, "_MODEL_SCAN_MAX_ENTRIES", 500)
    _stacked_aliases(model_dir, 12)
    result = _list_by_name(model_dir) if route == "by_name" else _list_by_id(monkeypatch, model_dir)
    assert result["success"] is False
    assert result["error"] == live2d_router._MODEL_SCAN_LIMIT_ERROR


@pytest.mark.parametrize("route", ["by_name", "by_id"])
def test_deep_nesting_stops_at_depth_limit(monkeypatch, model_dir, route):
    monkeypatch.setattr(live2d_router, "_MODEL_SCAN_MAX_DEPTH", 3)
    _write(model_dir / "m.model3.json", _INSIDE_CONFIG)
    _write(model_dir / "a" / "b" / "c" / "d" / "deep.motion3.json", {"Version": 3})
    result = _list_by_name(model_dir) if route == "by_name" else _list_by_id(monkeypatch, model_dir)
    assert result["success"] is False
    assert result["error"] == live2d_router._MODEL_SCAN_LIMIT_ERROR


@pytest.mark.parametrize("route", ["by_name", "by_id"])
def test_listing_within_limits_is_unchanged(monkeypatch, model_dir, route):
    monkeypatch.setattr(live2d_router, "_MODEL_SCAN_MAX_DEPTH", 3)
    _stacked_aliases(model_dir, 2)
    _write(model_dir / "a" / "b" / "c" / "deep.motion3.json", {"Version": 3})
    result = _list_by_name(model_dir) if route == "by_name" else _list_by_id(monkeypatch, model_dir)
    assert result["success"] is True, result
    assert sorted(result["motion_files"]) == [
        "a/b/c/deep.motion3.json",
        "a0/a1/l1.motion3.json",
        "a0/d1/l1.motion3.json",
        "a0/l0.motion3.json",
        "d0/a1/l1.motion3.json",
        "d0/d1/l1.motion3.json",
        "d0/l0.motion3.json",
    ]


def test_listing_walk_stops_reading_at_entry_limit(monkeypatch, model_dir):
    """Names are streamed: a folder with endless entries is read only up to the limit."""
    monkeypatch.setattr(live2d_router, "_MODEL_SCAN_MAX_ENTRIES", 50)
    yielded = 0

    class _Entry:
        def __init__(self, name: str) -> None:
            self.name = name

    class _EndlessScan:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def __iter__(self):
            nonlocal yielded
            while True:
                yielded += 1
                yield _Entry(f"f{yielded}.txt")

    real_scandir = os.scandir
    target = str(model_dir)
    # Only the walked folder is replaced; any other scandir call reaches the real function.
    monkeypatch.setattr(
        live2d_router.os, "scandir",
        lambda path=".": _EndlessScan() if os.fspath(path) == target else real_scandir(path),
    )
    with pytest.raises(live2d_router._ModelScanLimitExceeded):
        live2d_router._search_model_files(os.path.realpath(model_dir), str(model_dir), ".motion3.json")
    assert yielded == 51


# --- GET -> edit -> POST round trip of emotion_mapping -----------------------------


def _round_trip(model_dir: Path) -> tuple[dict, object]:
    got = live2d_router.get_emotion_mapping("m")
    assert got["success"] is True, got
    mapping = got["config"]
    # What the model manager does when adding a resident expression.
    mapping.setdefault("expressions", {}).setdefault("常驻", []).append("expressions/smile.exp3.json")
    return mapping, asyncio.run(live2d_router.update_emotion_mapping("m", _FakeRequest(mapping)))


def test_derived_mapping_leaves_out_refused_paths_and_round_trips(model_dir):
    config = {
        "Version": 3,
        "FileReferences": {
            "Motions": {"Idle": [{"File": "../shared/idle.motion3.json"}, {"File": "motions/ok.motion3.json"}]},
            "Expressions": [{"Name": "happy_a", "File": "a:b.exp3.json"}, {"Name": "happy_b", "File": "b.exp3.json"}],
        },
    }
    _write(model_dir / "m.model3.json", config)
    got = live2d_router.get_emotion_mapping("m")["config"]
    assert got == {"motions": {"Idle": ["motions/ok.motion3.json"]}, "expressions": {"happy": ["b.exp3.json"]}}

    mapping, result = _round_trip(model_dir)
    assert result["success"] is True
    saved = json.loads((model_dir / "m.model3.json").read_text(encoding="utf-8"))
    assert saved["EmotionMapping"] == mapping


def test_legacy_stored_mapping_is_cleaned_on_read_and_round_trips(model_dir):
    config = dict(_INSIDE_CONFIG)
    config["EmotionMapping"] = {
        "motions": {"happy": "motions/ok.motion3.json", "sad": ["../x.motion3.json", 3, "motions/s.motion3.json"]},
        "expressions": {"angry": {"x": 1}, "happy": ["C:/x.exp3.json", "e.exp3.json"]},
        "extra": "kept",
    }
    _write(model_dir / "m.model3.json", config)
    got = live2d_router.get_emotion_mapping("m")["config"]
    assert got == {
        "motions": {"happy": ["motions/ok.motion3.json"], "sad": ["motions/s.motion3.json"]},
        "expressions": {"angry": [], "happy": ["e.exp3.json"]},
        "extra": "kept",
    }
    _mapping, result = _round_trip(model_dir)
    assert result["success"] is True


@pytest.mark.parametrize("section", ["motions", "expressions"])
def test_emotion_mapping_post_refuses_explicit_null_section(model_dir, section):
    config = dict(_INSIDE_CONFIG)
    config["FileReferences"] = {"Motions": {"Idle": [{"File": "motions/idle.motion3.json"}]},
                                "Expressions": [{"Name": "e", "File": "e.exp3.json"}]}
    _write(model_dir / "m.model3.json", config)
    before = (model_dir / "m.model3.json").read_bytes()
    payload = {"motions": {}, "expressions": {}}
    payload[section] = None
    result = asyncio.run(live2d_router.update_emotion_mapping("m", _FakeRequest(payload)))
    assert _status(result) == 400
    assert (model_dir / "m.model3.json").read_bytes() == before


def test_model_config_post_treats_null_sound_as_absent(monkeypatch, tmp_path, model_dir):
    _write(model_dir / "m.model3.json", _INSIDE_CONFIG)
    item_dir = _by_id_model(monkeypatch, tmp_path)
    motions = {"Idle": [{"File": "motions/idle.motion3.json", "Sound": None}]}
    for call, config in (
        (lambda: live2d_router.update_model_config("m", _FakeRequest({"FileReferences": {"Motions": motions}})),
         model_dir / "m.model3.json"),
        (lambda: live2d_router.update_model_config_by_id("123", _FakeRequest({"FileReferences": {"Motions": motions}})),
         item_dir / "m.model3.json"),
    ):
        assert asyncio.run(call())["success"] is True
        assert json.loads(config.read_text(encoding="utf-8"))["FileReferences"]["Motions"] == motions


def test_rejected_reference_does_not_log_the_submitted_path(monkeypatch, model_dir, outside, caplog):
    # Let the router's records reach caplog even after setup_logging turned propagation off.
    logger = live2d_router.logger
    while logger is not None:
        monkeypatch.setattr(logger, "propagate", True)
        logger = logger.parent
    _write(model_dir / "m.model3.json", _INSIDE_CONFIG)
    _ref_link_layout(model_dir, outside)
    payload = {"motions": {"happy": ["out/secret-name.motion3.json"]}, "expressions": {}}
    with caplog.at_level("INFO", logger=live2d_router.logger.name):
        result = asyncio.run(live2d_router.update_emotion_mapping("m", _FakeRequest(payload)))
    assert _status(result) == 400
    assert any("情绪映射" in r.getMessage() for r in caplog.records)  # the capture itself works
    assert not any("secret-name" in r.getMessage() for r in caplog.records)


def test_derived_mapping_skips_non_string_expression_files(model_dir):
    config = {"Version": 3, "FileReferences": {"Motions": {}, "Expressions": [
        {"Name": "happy_a", "File": 3}, {"Name": "happy_b", "File": "b.exp3.json"}]}}
    _write(model_dir / "m.model3.json", config)
    assert live2d_router.get_emotion_mapping("m")["config"]["expressions"] == {"happy": ["b.exp3.json"]}


_ODD_DISK_CONFIGS = [
    ([], {"motions": {}, "expressions": {}}),
    ({"FileReferences": "x"}, {"motions": {}, "expressions": {}}),
    ({"FileReferences": {"Motions": [1], "Expressions": {"a": 1}}}, {"motions": {}, "expressions": {}}),
    ({"FileReferences": {"Motions": {"Idle": 5}}}, {"motions": {"Idle": []}, "expressions": {}}),
    ({"FileReferences": {"Expressions": [{"Name": 3, "File": "e.exp3.json"}]}},
     {"motions": {}, "expressions": {"neutral": ["e.exp3.json"]}}),
]


@pytest.mark.parametrize("config, expected", _ODD_DISK_CONFIGS)
def test_get_emotion_mapping_tolerates_unexpected_shapes_on_disk(model_dir, config, expected):
    _write(model_dir / "m.model3.json", config)
    result = live2d_router.get_emotion_mapping("m")
    assert _status(result) == 200
    assert result["config"] == expected
