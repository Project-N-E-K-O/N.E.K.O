from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from main_logic import music_playback, music_requests
from plugin.plugins.rvc_cover import rvc_runner
from plugin.plugins.rvc_cover.service import RvcCoverService, settings_from_mapping


@pytest.mark.parametrize(
    ("text", "query"),
    (
        ("给我唱一首晴天", "晴天"),
        ("给我翻唱一首小星星", "小星星"),
        ("唱一首小星星", "小星星"),
        ("翻唱海阔天空", "海阔天空"),
        ("帮我唱周杰伦的晴天", "周杰伦的晴天"),
        ("sing me a song Yellow", "Yellow"),
        ("cover Shape of You", "Shape of You"),
    ),
)
def test_parse_explicit_sing_cover_request(text, query) -> None:
    request = music_requests.parse_explicit_sing_cover_request(text)
    assert request is not None
    assert request.query == query
    # Must not also be treated as plain playback.
    assert music_requests.parse_explicit_user_music_request(text) is None


def test_voice_model_hint_sing_cover() -> None:
    request = music_requests.parse_explicit_sing_cover_request("用糯糯的声音唱晴天")
    assert request is not None
    assert request.query == "晴天"
    assert "糯糯" in request.model_hint


@pytest.mark.parametrize(
    "text",
    (
        "播放晴天",
        "点首歌",
        "来一首晴天",
        "不要唱了",
        "你喜欢唱歌吗",
    ),
)
def test_plain_playback_is_not_sing_cover(text) -> None:
    assert music_requests.parse_explicit_sing_cover_request(text) is None


def test_build_infer_command_includes_model_and_paths(tmp_path: Path) -> None:
    cfg = rvc_runner.RvcInferConfig(
        rvc_root=tmp_path,
        python_path=tmp_path / "python.exe",
        model_name="Ai糯糯雫.pth",
        f0_method="harvest",
        device="cuda:0",
        index_rate=0.0,
    )
    input_path = tmp_path / "in.wav"
    output_path = tmp_path / "out.wav"
    cmd = rvc_runner.build_infer_command(
        cfg,
        input_path=input_path,
        output_path=output_path,
        f0_method="harvest",
    )
    assert str(cfg.python_path) in cmd
    assert "--model_name" in cmd
    assert "Ai糯糯雫.pth" in cmd
    assert "--input_path" in cmd
    assert str(input_path) in cmd
    assert "--opt_path" in cmd
    assert str(output_path) in cmd
    assert "--f0method" in cmd
    assert "harvest" in cmd


def test_resolve_f0_method_falls_back_without_rmvpe(tmp_path: Path) -> None:
    assert rvc_runner.resolve_f0_method(tmp_path, "rmvpe") == "harvest"
    rmvpe_dir = tmp_path / "assets" / "rmvpe"
    rmvpe_dir.mkdir(parents=True)
    (rmvpe_dir / "rmvpe.pt").write_bytes(b"x")
    assert rvc_runner.resolve_f0_method(tmp_path, "rmvpe") == "rmvpe"


def test_settings_from_mapping_defaults() -> None:
    from plugin.plugins.rvc_cover.paths import default_python_path, default_rvc_root

    settings = settings_from_mapping({})
    assert settings.model_name == "Ai糯糯雫.pth"
    assert settings.use_uvr is False
    assert settings.rvc_root == default_rvc_root()
    assert settings.python_path == default_python_path()
    # Must not point at the user's original install by default.
    assert "D:\\RVC" not in str(settings.rvc_root)
    assert str(settings.rvc_root).replace("\\", "/").endswith("vendor/rvc")


def test_resolve_rvc_root_relative_to_repo() -> None:
    from plugin.plugins.rvc_cover.paths import resolve_rvc_root

    root = resolve_rvc_root("vendor/rvc")
    assert root.is_absolute()
    assert root.name == "rvc"
    assert root.parent.name == "vendor"


@pytest.mark.asyncio
async def test_service_enqueue_pushes_after_mock_pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pushed = []

    def push_music(**kwargs):
        pushed.append(kwargs)

    service = RvcCoverService(
        logger=MagicMock(),
        work_dir=tmp_path / "work",
        static_ui_dir=tmp_path / "static_ui",
        plugin_id="rvc_cover",
        push_music=push_music,
        notify=lambda _m: None,
    )
    # Pretend RVC install is valid.
    monkeypatch.setattr(
        "plugin.plugins.rvc_cover.service.validate_rvc_install",
        lambda _cfg: [],
    )

    async def fake_search(job):
        return {
            "name": "晴天",
            "artist": "周杰伦",
            "url": "https://music.163.com/song/media/outer/url?id=1.mp3",
        }

    def fake_download(url, dest_prefix):
        path = dest_prefix.with_suffix(".mp3")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake-mp3")
        return path

    def fake_infer(cfg, *, input_path, output_path, env_extra=None):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"RIFF....WAVE")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(service, "_search_track", fake_search)
    monkeypatch.setattr(service, "_download_track", fake_download)
    monkeypatch.setattr("plugin.plugins.rvc_cover.service.run_infer", fake_infer)

    result = service.enqueue(query="晴天", song="晴天")
    assert result["ok"] is True
    # Wait for background worker.
    for _ in range(50):
        status = service.status()
        if status.get("status") in {"done", "failed"}:
            break
        await asyncio.sleep(0.05)

    status = service.status()
    assert status["status"] == "done", status
    assert pushed
    assert "晴天" in pushed[0]["title"]
    assert pushed[0]["url"].endswith(".wav")


@pytest.mark.asyncio
async def test_utterance_triggers_rvc_cover(monkeypatch: pytest.MonkeyPatch) -> None:
    called = []

    async def fake_execute(manager, request):
        called.append((manager.lanlan_name, request.query))
        return {"ok": True}

    monkeypatch.setattr(music_playback, "_execute_sing_cover_request", fake_execute)

    scheduled = []

    def fire_task(coro):
        task = asyncio.create_task(coro)
        scheduled.append(task)
        return task

    manager = SimpleNamespace(lanlan_name="YUI", _fire_task=fire_task)
    monkeypatch.setattr(
        music_playback,
        "_session_manager_getter",
        lambda _: manager,
    )

    music_playback._on_user_utterance(
        "YUI",
        {"lanlan": "YUI", "content": "给我唱一首晴天"},
    )
    await asyncio.gather(*scheduled)
    assert called == [("YUI", "晴天")]


@pytest.mark.asyncio
async def test_trigger_rvc_cover_plugin_uses_runs_protocol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx as httpx_mod

    posts: list[dict] = []
    gets: list[str] = []

    class _Resp:
        def __init__(self, status_code: int, payload: dict):
            self.status_code = status_code
            self._payload = payload
            self.text = ""

        def json(self):
            return self._payload

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None):
            posts.append({"url": url, "json": json})
            assert url.endswith("/runs")
            return _Resp(200, {"run_id": "run-1", "status": "queued"})

        async def get(self, url):
            gets.append(url)
            if url.endswith("/runs/run-1"):
                return _Resp(200, {"run_id": "run-1", "status": "succeeded"})
            if url.endswith("/runs/run-1/export"):
                return _Resp(
                    200,
                    {
                        "items": [
                            {
                                "type": "json",
                                "json": {
                                    "data": {
                                        "success": True,
                                        "value": {
                                            "ok": True,
                                            "status": "started",
                                            "message": "ok",
                                        },
                                    }
                                },
                            }
                        ]
                    },
                )
            return _Resp(404, {})

    monkeypatch.setattr(httpx_mod, "AsyncClient", _Client)
    monkeypatch.setattr(
        music_playback,
        "_plugin_server_origin",
        lambda: "http://127.0.0.1:48916",
    )

    result = await music_playback.trigger_rvc_cover_plugin(
        music_requests.SingCoverRequest(query="小星星", song_name="小星星"),
        target_lanlan="YUI",
    )
    assert result["ok"] is True
    assert posts and posts[0]["json"]["entry_id"] == "sing_cover"
    assert any(url.endswith("/runs/run-1") for url in gets)
    assert any(url.endswith("/export") for url in gets)


@pytest.mark.asyncio
async def test_rvc_config_store_persists_settings(tmp_path: Path) -> None:
    from plugin.plugins.rvc_cover.config_store import RvcCoverConfigStore

    store = RvcCoverConfigStore(tmp_path)
    saved = await store.save(
        {
            "model_name": "Ai糯糯雫.pth",
            "f0_up_key": 2,
            "device": "cuda:0",
        }
    )
    assert saved["f0_up_key"] == 2
    loaded = await store.load()
    assert loaded["model_name"] == "Ai糯糯雫.pth"
    assert loaded["f0_up_key"] == 2
    merged = store.merge_with_base({"model_name": "old.pth", "device": "cpu"}, loaded)
    assert merged["model_name"] == "Ai糯糯雫.pth"
    assert merged["device"] == "cuda:0"
