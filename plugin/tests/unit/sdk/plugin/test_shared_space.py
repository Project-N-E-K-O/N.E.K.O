"""The plugin shared space: one plugin publishes files, others declare that they read them."""

from __future__ import annotations

from pathlib import Path

import pytest

from plugin.sdk.plugin.base import NekoPluginBase
from plugin.sdk.shared.core.base_runtime import (
    PLUGIN_SHARED_DIRNAME,
    resolve_plugin_shared_dir,
    resolve_shared_reads,
)
from plugin.sdk.shared.models.exceptions import ValidationError


class _Ctx:
    logger = None

    def __init__(self, plugin_id: str, config_path: Path, effective: dict[str, object] | None = None) -> None:
        self.plugin_id = plugin_id
        self.config_path = config_path
        self.metadata: dict[str, object] = {}
        self.bus = None
        self._effective_config = effective


class _Plugin(NekoPluginBase):
    pass


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(runtime_root))
    return runtime_root


def _manifest(tmp_path: Path, plugin_id: str, body: str = "") -> Path:
    path = tmp_path / plugin_id / "plugin.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'[plugin]\nid = "{plugin_id}"\n{body}', encoding="utf-8")
    return path


def _plugin(tmp_path: Path, plugin_id: str, body: str = "", effective: dict[str, object] | None = None) -> _Plugin:
    return _Plugin(ctx=_Ctx(plugin_id, _manifest(tmp_path, plugin_id, body), effective))


def test_a_plugin_publishes_below_its_own_shared_directory(root, tmp_path) -> None:
    weather = _plugin(tmp_path, "weather")

    assert weather.shared_path() == root / "plugin_shared" / "weather"
    assert weather.shared_path("cities", "list.json") == root / "plugin_shared" / "weather" / "cities" / "list.json"
    # Apart from the private state, which is removed with the plugin.
    assert root / "plugins" not in weather.shared_path().parents
    assert PLUGIN_SHARED_DIRNAME == "plugin_shared"
    # Asking for a path creates nothing, like data_path and cache_path.
    assert not (root / "plugin_shared").exists()


def test_another_plugin_reads_it_once_its_manifest_says_so(root, tmp_path) -> None:
    weather = _plugin(tmp_path, "weather")
    weather.shared_path().mkdir(parents=True)
    weather.shared_path("cities.json").write_text('["Shanghai"]', encoding="utf-8")
    travel = _plugin(tmp_path, "travel", '\n[plugin.shared]\nreads = ["weather", "calendar"]\n')

    path = travel.shared_path_of("weather", "cities.json")

    assert path == root / "plugin_shared" / "weather" / "cities.json"
    assert path.read_text(encoding="utf-8") == '["Shanghai"]'
    assert travel.shared_path_of("weather") == root / "plugin_shared" / "weather"
    # A declared plugin that published nothing yet: the path, and no directory.
    assert travel.shared_path_of("calendar", "events.json") == root / "plugin_shared" / "calendar" / "events.json"
    assert not (root / "plugin_shared" / "calendar").exists()


@pytest.mark.parametrize(
    "body",
    ["", "\n[plugin.shared]\n", '\n[plugin.shared]\nreads = ["calendar"]\n', "\n[plugin.shared]\nreads = 7\n"],
)
def test_without_the_declaration_it_is_refused(root, tmp_path, body) -> None:
    travel = _plugin(tmp_path, "travel", body)

    with pytest.raises(ValidationError) as caught:
        travel.shared_path_of("weather", "cities.json")

    assert caught.value.code == "shared_not_declared"
    assert "[plugin.shared] reads" in str(caught.value)


def test_a_plugin_does_not_declare_itself_to_use_its_own_directory(root, tmp_path) -> None:
    weather = _plugin(tmp_path, "weather")

    assert weather.shared_path("x") == root / "plugin_shared" / "weather" / "x"
    with pytest.raises(ValidationError):
        weather.shared_path_of("weather", "x")


@pytest.mark.parametrize("plugin_id", ["", "   ", None])
def test_an_empty_plugin_id_is_refused(root, tmp_path, plugin_id) -> None:
    travel = _plugin(tmp_path, "travel", '\n[plugin.shared]\nreads = ["weather"]\n')

    with pytest.raises(ValidationError):
        travel.shared_path_of(plugin_id)


@pytest.mark.parametrize("parts", [("..", "travel"), ("a", "..", "..", "b"), ("../x",), ("C:/Windows",), ("/etc",)])
def test_a_path_cannot_leave_the_shared_directory(root, tmp_path, parts) -> None:
    travel = _plugin(tmp_path, "travel", '\n[plugin.shared]\nreads = ["weather"]\n')

    with pytest.raises(ValidationError) as caught:
        travel.shared_path_of("weather", *parts)
    assert caught.value.code == "shared_bad_path"
    with pytest.raises(ValidationError):
        travel.shared_path(*parts)


def test_a_declared_id_cannot_name_a_directory_outside(root, tmp_path) -> None:
    travel = _plugin(tmp_path, "travel", '\n[plugin.shared]\nreads = ["../plugins/weather"]\n')

    path = travel.shared_path_of("../plugins/weather", "data")

    # The id is made a single safe directory name, as it is for private state.
    assert path.parent.parent == root / "plugin_shared"
    assert root / "plugins" not in path.parents


def test_the_declaration_is_read_from_the_effective_configuration_when_there_is_one(root, tmp_path) -> None:
    # The user's runtime configuration can add to what the manifest declares.
    effective = {"plugin": {"shared": {"reads": ["weather"]}}}
    travel = _plugin(tmp_path, "travel", "", effective)
    plain = _plugin(tmp_path, "plain", '\n[plugin.shared]\nreads = ["weather"]\n', {"plugin": {}})

    assert resolve_shared_reads(travel.ctx) == frozenset({"weather"})
    assert travel.shared_path_of("weather") == root / "plugin_shared" / "weather"
    # An effective configuration without the table says: nothing is declared.
    assert resolve_shared_reads(plain.ctx) == frozenset()


def test_a_manifest_that_cannot_be_read_declares_nothing(root, tmp_path) -> None:
    travel = _plugin(tmp_path, "travel")
    travel.ctx.config_path.write_text("not [ toml", encoding="utf-8")

    assert resolve_shared_reads(travel.ctx) == frozenset()
    # Nor does a context that knows no manifest.
    assert resolve_shared_reads(_Ctx("travel", None)) == frozenset()  # type: ignore[arg-type]
    assert resolve_shared_reads(object()) == frozenset()


def test_one_id_as_a_string_is_accepted(root, tmp_path) -> None:
    travel = _plugin(tmp_path, "travel", '\n[plugin.shared]\nreads = "weather"\n')

    assert resolve_shared_reads(travel.ctx) == frozenset({"weather"})


@pytest.mark.parametrize(
    "first,second",
    [
        ("weather", "weather_"),
        ("weather", "_weather"),
        ("weather", ".weather"),
        ("a b", "a_b"),
        ("a/b", "a_b"),
        ("weather_", "weather."),
        # What an earlier mapping gave one id is a valid id of another plugin.
        ("weather_", "weather-15a3e0c2"),
        # One directory on Windows and macOS, were they left as they are.
        ("Weather", "weather"),
        ("WEATHER", "Weather"),
        # Nor is an escaped name the plain name of anything.
        ("Weather", "~57eather"),
        ("nul", "~~nul"),
    ],
)
def test_two_plugin_ids_never_publish_into_one_directory(root, first, second) -> None:
    one, other = resolve_plugin_shared_dir(first), resolve_plugin_shared_dir(second)

    assert one.name.lower() != other.name.lower()
    assert one.parent == other.parent == root / "plugin_shared"


@pytest.mark.parametrize("plugin_id", ["weather", "memo_reminder", "mcp-adapter", "plugin2", "weather_", "_x", "-"])
def test_an_ordinary_plugin_id_is_its_own_directory_name(root, plugin_id) -> None:
    assert resolve_plugin_shared_dir(plugin_id) == root / "plugin_shared" / plugin_id


@pytest.mark.parametrize(
    "plugin_id,name",
    [
        ("Weather", "~57eather"),
        ("a b", "a~20b"),
        (".weather", "~2eweather"),
        ("a.b", "a~2eb"),
        ("天气", "~e5~a4~a9~e6~b0~94"),
        ("nul", "~~nul"),
        ("com1", "~~com1"),
        ("", "~"),
    ],
)
def test_other_characters_are_escaped(root, plugin_id, name) -> None:
    assert resolve_plugin_shared_dir(plugin_id).name == name


@pytest.mark.parametrize(
    "plugin_id", ["weather", "weather_", "Weather", "MCP-Adapter_2", "a b", "../x", "天气", "nul", "a~b", "~57eather"]
)
def test_the_directory_name_reads_backwards_to_the_id(root, plugin_id) -> None:
    import re

    name = resolve_plugin_shared_dir(plugin_id).name
    if name.startswith("~~"):
        name = name[2:]
    raw = re.sub(r"((?:~[0-9a-f]{2})+)", lambda match: bytes.fromhex(match.group(1).replace("~", "")).decode("utf-8"), name)

    assert raw == plugin_id
    # A single safe directory name, on any system.
    assert re.fullmatch(r"[a-z0-9_~-]+", resolve_plugin_shared_dir(plugin_id).name)


def test_private_state_keeps_its_directory_names(root, tmp_path) -> None:
    # Only the shared directory tells such ids apart; private paths are as they were.
    assert _plugin(tmp_path, "weather_").storage_dir == root / "plugins" / "weather"


def test_the_shared_directory_follows_the_data_root(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path / "elsewhere"))

    assert resolve_plugin_shared_dir("weather") == tmp_path / "elsewhere" / "plugin_shared" / "weather"
