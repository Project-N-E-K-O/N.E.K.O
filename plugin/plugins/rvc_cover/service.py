"""Search → download → RVC convert → push to music player."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlparse
from uuid import uuid4

import httpx

from .paths import default_python_path, default_rvc_root, resolve_rvc_root
from .rvc_runner import (
    RvcInferConfig,
    list_weight_models,
    run_infer,
    validate_rvc_install,
)

PushMusicFn = Callable[..., None]
NotifyFn = Callable[[str], None]

_OUTPUT_SUBDIR = "outputs"
_WORK_SUBDIR = "work"


@dataclass
class RvcCoverSettings:
    rvc_root: Path = field(default_factory=default_rvc_root)
    python_path: Path = field(default_factory=lambda: default_python_path())
    model_name: str = "Ai糯糯雫.pth"
    index_path: str = ""
    f0_up_key: int = 0
    f0_method: str = "rmvpe"
    device: str = "cuda:0"
    index_rate: float = 0.0
    filter_radius: int = 3
    resample_sr: int = 0
    rms_mix_rate: float = 1.0
    protect: float = 0.33
    infer_timeout_seconds: int = 600
    use_uvr: bool = False
    # Gradio/API (infer-web) managed with plugin start/stop in plugin manager.
    auto_start_web: bool = True
    web_port: int = 7897
    web_server_name: str = "127.0.0.1"
    web_startup_timeout_seconds: int = 90
    web_shutdown_timeout_seconds: int = 8


@dataclass
class CoverJob:
    job_id: str
    query: str
    song: str = ""
    artist: str = ""
    model_name: str = ""
    target_lanlan: str = ""
    cancel_event: threading.Event = field(default_factory=threading.Event)
    status: str = "queued"
    error: str = ""
    title: str = ""
    output_url: str = ""


def settings_to_mapping(settings: RvcCoverSettings) -> dict[str, Any]:
    """Serialize settings for UI / config.update (paths relative to repo when possible)."""
    from .paths import repo_root

    root = repo_root().resolve()

    def _rel(path: Path) -> str:
        try:
            return str(path.resolve().relative_to(root)).replace("\\", "/")
        except Exception:
            return str(path)

    return {
        "rvc_root": _rel(settings.rvc_root),
        "python_path": _rel(settings.python_path),
        "model_name": settings.model_name,
        "index_path": settings.index_path,
        "f0_up_key": int(settings.f0_up_key),
        "f0_method": settings.f0_method,
        "device": settings.device,
        "index_rate": float(settings.index_rate),
        "filter_radius": int(settings.filter_radius),
        "resample_sr": int(settings.resample_sr),
        "rms_mix_rate": float(settings.rms_mix_rate),
        "protect": float(settings.protect),
        "infer_timeout_seconds": int(settings.infer_timeout_seconds),
        "use_uvr": bool(settings.use_uvr),
        "auto_start_web": bool(settings.auto_start_web),
        "web_port": int(settings.web_port),
        "web_server_name": str(settings.web_server_name),
        "web_startup_timeout_seconds": int(settings.web_startup_timeout_seconds),
        "web_shutdown_timeout_seconds": int(settings.web_shutdown_timeout_seconds),
    }


def settings_from_mapping(raw: dict[str, Any] | None) -> RvcCoverSettings:
    data = raw if isinstance(raw, dict) else {}
    default_root = default_rvc_root()
    default_py = default_python_path(default_root)

    def _float(key: str, default: float) -> float:
        try:
            return float(data.get(key, default))
        except (TypeError, ValueError):
            return default

    def _int(key: str, default: int) -> int:
        try:
            return int(data.get(key, default))
        except (TypeError, ValueError):
            return default

    rvc_root = resolve_rvc_root(data.get("rvc_root") or default_root)
    py_raw = str(data.get("python_path") or "").strip()
    if py_raw:
        py_path = Path(py_raw)
        if not py_path.is_absolute():
            from .paths import repo_root

            py_path = (repo_root() / py_path).resolve()
        else:
            py_path = py_path.resolve()
    else:
        py_path = default_python_path(rvc_root)

    return RvcCoverSettings(
        rvc_root=rvc_root,
        python_path=py_path,
        model_name=str(data.get("model_name") or "Ai糯糯雫.pth").strip() or "Ai糯糯雫.pth",
        index_path=str(data.get("index_path") or "").strip(),
        f0_up_key=_int("f0_up_key", 0),
        f0_method=str(data.get("f0_method") or "rmvpe").strip() or "rmvpe",
        device=str(data.get("device") or "cuda:0").strip() or "cuda:0",
        index_rate=_float("index_rate", 0.0),
        filter_radius=_int("filter_radius", 3),
        resample_sr=_int("resample_sr", 0),
        rms_mix_rate=_float("rms_mix_rate", 1.0),
        protect=_float("protect", 0.33),
        infer_timeout_seconds=max(30, _int("infer_timeout_seconds", 600)),
        use_uvr=bool(data.get("use_uvr", False)),
        auto_start_web=bool(data.get("auto_start_web", True)),
        web_port=max(1, min(65535, _int("web_port", 7897))),
        web_server_name=str(data.get("web_server_name") or "127.0.0.1").strip() or "127.0.0.1",
        web_startup_timeout_seconds=max(10, _int("web_startup_timeout_seconds", 90)),
        web_shutdown_timeout_seconds=max(1, _int("web_shutdown_timeout_seconds", 8)),
    )


def _main_server_origin() -> str:
    try:
        from config import MAIN_SERVER_PORT

        port = int(MAIN_SERVER_PORT)
        if 1 <= port <= 65535:
            return f"http://127.0.0.1:{port}"
    except Exception:
        pass
    env_port = str(os.getenv("MAIN_SERVER_PORT", "") or "").strip()
    if env_port.isdigit():
        return f"http://127.0.0.1:{int(env_port)}"
    return "http://127.0.0.1:48911"


def _plugin_server_origin() -> str:
    for key in (
        "NEKO_PLUGIN_SERVER_ORIGIN",
        "NEKO_USER_PLUGIN_SERVER_ORIGIN",
        "NEKO_SERVER_ORIGIN",
    ):
        val = str(os.getenv(key, "") or "").strip().rstrip("/")
        if val.startswith("http://") or val.startswith("https://"):
            return val
    try:
        env_port = int(os.getenv("NEKO_USER_PLUGIN_SERVER_PORT", "").strip())
        if 1 <= env_port <= 65535:
            return f"http://127.0.0.1:{env_port}"
    except Exception:
        pass
    try:
        from config import USER_PLUGIN_SERVER_PORT

        port = int(USER_PLUGIN_SERVER_PORT)
        if 1 <= port <= 65535:
            return f"http://127.0.0.1:{port}"
    except Exception:
        pass
    return "http://127.0.0.1:48916"


def _guess_extension(url: str, content_type: str) -> str:
    path = urlparse(url).path.lower()
    for ext in (".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".m4s"):
        if path.endswith(ext):
            return ".m4a" if ext == ".m4s" else ext
    ctype = (content_type or "").split(";")[0].strip().lower()
    mapping = {
        "audio/mpeg": ".mp3",
        "audio/mp3": ".mp3",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/flac": ".flac",
        "audio/ogg": ".ogg",
        "audio/mp4": ".m4a",
        "audio/aac": ".aac",
        "application/octet-stream": ".bin",
    }
    return mapping.get(ctype, ".bin")


class RvcCoverService:
    def __init__(
        self,
        *,
        logger: logging.Logger,
        work_dir: Path,
        static_ui_dir: Path,
        plugin_id: str,
        push_music: PushMusicFn,
        notify: NotifyFn | None = None,
    ) -> None:
        self.logger = logger
        self.work_dir = work_dir
        self.static_ui_dir = static_ui_dir
        self.plugin_id = plugin_id
        self._push_music = push_music
        self._notify = notify or (lambda _msg: None)
        self.settings = RvcCoverSettings()
        self._lock = threading.Lock()
        self._active_job: CoverJob | None = None
        self._worker_thread: threading.Thread | None = None

    def apply_settings(self, raw: dict[str, Any] | None) -> list[str]:
        self.settings = settings_from_mapping(raw)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        (self.static_ui_dir / _OUTPUT_SUBDIR).mkdir(parents=True, exist_ok=True)
        return validate_rvc_install(self._infer_config())

    def _infer_config(self, model_name: str = "") -> RvcInferConfig:
        settings = self.settings
        return RvcInferConfig(
            rvc_root=settings.rvc_root,
            python_path=settings.python_path,
            model_name=(model_name or settings.model_name).strip() or settings.model_name,
            index_path=settings.index_path,
            f0_up_key=settings.f0_up_key,
            f0_method=settings.f0_method,
            device=settings.device,
            index_rate=settings.index_rate,
            filter_radius=settings.filter_radius,
            resample_sr=settings.resample_sr,
            rms_mix_rate=settings.rms_mix_rate,
            protect=settings.protect,
            timeout_seconds=settings.infer_timeout_seconds,
        )

    def list_models(self) -> list[str]:
        return list_weight_models(self.settings.rvc_root)

    def _resolve_model_name(self, model_name: str = "") -> str:
        raw = str(model_name or "").strip()
        if not raw:
            return self.settings.model_name
        if raw.lower().endswith(".pth"):
            return raw
        needle = raw.casefold()
        for name in self.list_models():
            stem = Path(name).stem.casefold()
            if needle in stem or stem in needle:
                return name
        return self.settings.model_name

    def enqueue(
        self,
        *,
        query: str,
        song: str = "",
        artist: str = "",
        model_name: str = "",
        target_lanlan: str = "",
    ) -> dict[str, Any]:
        q = str(query or song or "").strip()
        if not q and not str(song or "").strip():
            return {"ok": False, "error": "missing_query", "message": "请提供要唱的歌名或关键词。"}

        resolved_model = self._resolve_model_name(model_name)
        problems = validate_rvc_install(self._infer_config(resolved_model))
        if problems:
            return {
                "ok": False,
                "error": "rvc_not_ready",
                "message": "；".join(problems),
                "problems": problems,
            }

        job = CoverJob(
            job_id=uuid4().hex[:12],
            query=q or str(song).strip(),
            song=str(song or "").strip(),
            artist=str(artist or "").strip(),
            model_name=resolved_model,
            target_lanlan=str(target_lanlan or "").strip(),
        )

        with self._lock:
            previous = self._active_job
            if previous is not None and previous.status in {"queued", "running"}:
                previous.cancel_event.set()
            self._active_job = job
            thread = threading.Thread(
                target=self._run_job,
                args=(job,),
                name=f"rvc-cover-{job.job_id}",
                daemon=True,
            )
            self._worker_thread = thread
            thread.start()

        self._notify(f"开始翻唱「{job.query}」，请稍等…")
        return {
            "ok": True,
            "status": "started",
            "job_id": job.job_id,
            "query": job.query,
            "model_name": job.model_name,
            "message": f"已开始联网搜歌并用 {job.model_name} 翻唱「{job.query}」。",
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            job = self._active_job
            if job is None:
                return {"ok": True, "status": "idle"}
            return {
                "ok": True,
                "status": job.status,
                "job_id": job.job_id,
                "query": job.query,
                "model_name": job.model_name,
                "title": job.title,
                "output_url": job.output_url,
                "error": job.error,
            }

    def list_training_projects(self) -> list[str]:
        root = Path(self.settings.rvc_root)
        logs = root / "logs"
        if not logs.is_dir():
            return []
        names: list[str] = []
        try:
            for path in sorted(logs.iterdir()):
                if not path.is_dir():
                    continue
                name = path.name
                if name.startswith((".", "__", "plugin_")):
                    continue
                if name.lower().startswith("smoke"):
                    continue
                names.append(name)
        except OSError:
            return []
        return names

    def dashboard_state(self) -> dict[str, Any]:
        problems = validate_rvc_install(self._infer_config())
        models = self.list_models()
        settings = settings_to_mapping(self.settings)
        job = self.status()
        projects = self.list_training_projects()
        ready = not problems and bool(models)
        return {
            "ok": True,
            "ready": ready,
            "problems": problems,
            "models": models,
            "training_projects": projects,
            "settings": settings,
            "job": job,
            "hints": {
                "setup": "scripts/setup_rvc_vendor.ps1",
                "training": "scripts/start_rvc_training.bat",
                "sync_weights": "scripts/sync_rvc_weights.ps1",
                "trigger_phrases": [
                    "给我唱一首晴天",
                    "给我翻唱一首小星星",
                    "翻唱告白气球",
                    "用糯糯的声音唱七里香",
                ],
            },
        }

    def _run_job(self, job: CoverJob) -> None:
        job.status = "running"
        try:
            if job.cancel_event.is_set():
                job.status = "cancelled"
                return
            track = asyncio.run(self._search_track(job))
            if job.cancel_event.is_set():
                job.status = "cancelled"
                return
            if not track:
                raise RuntimeError(f"未找到可下载的歌曲：{job.query}")

            title = str(track.get("name") or job.song or job.query).strip() or job.query
            artist = str(track.get("artist") or job.artist or "Unknown").strip()
            source_url = str(track.get("url") or "").strip()
            if not source_url:
                raise RuntimeError("搜到的歌曲没有可下载地址")

            job.title = title
            work_dir = self.work_dir / _WORK_SUBDIR / job.job_id
            work_dir.mkdir(parents=True, exist_ok=True)
            source_path = self._download_track(source_url, work_dir / "source")
            if job.cancel_event.is_set():
                job.status = "cancelled"
                return

            output_wav = work_dir / "cover.wav"
            if self.settings.use_uvr:
                self.logger.warning(
                    "use_uvr=true but UVR weights are not wired in v1; converting whole track"
                )
            run_infer(
                self._infer_config(job.model_name),
                input_path=source_path,
                output_path=output_wav,
            )
            if job.cancel_event.is_set():
                job.status = "cancelled"
                return

            public_name = f"{job.job_id}.wav"
            public_path = self.static_ui_dir / _OUTPUT_SUBDIR / public_name
            public_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(output_wav, public_path)

            relative = f"/plugin/{self.plugin_id}/ui/{_OUTPUT_SUBDIR}/{public_name}"
            absolute = f"{_plugin_server_origin()}{relative}"
            job.output_url = absolute
            model_label = Path(job.model_name).stem
            self._push_music(
                url=absolute,
                title=title,
                artist=f"RVC · {model_label}",
                target_lanlan=job.target_lanlan or None,
            )
            job.status = "done"
            self._notify(f"翻唱完成：{title}")
            self.logger.info("rvc cover done job=%s title=%s url=%s", job.job_id, title, absolute)
        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)
            self.logger.exception("rvc cover failed job=%s", job.job_id)
            self._notify(f"翻唱失败：{exc}")

    async def _search_track(self, job: CoverJob) -> dict[str, Any] | None:
        keyword = " ".join(
            part for part in (job.song or job.query, job.artist) if part
        ).strip() or job.query
        from utils.music_crawlers import fetch_music_content

        result = await fetch_music_content(
            keyword,
            limit=5,
            requested_song=job.song or "",
            requested_artist=job.artist or "",
            bypass_recommendation_dedupe=True,
        )
        if not isinstance(result, dict) or not result.get("success"):
            return None
        tracks = result.get("data") if isinstance(result.get("data"), list) else []
        for track in tracks:
            if isinstance(track, dict) and str(track.get("url") or "").strip():
                return track
        return None

    def _download_track(self, url: str, dest_prefix: Path) -> Path:
        proxy_base = f"{_main_server_origin()}/api/music/proxy"
        fetch_url = f"{proxy_base}?url={quote(url, safe='')}"
        dest_prefix.parent.mkdir(parents=True, exist_ok=True)

        with httpx.Client(timeout=120.0, follow_redirects=True) as client:
            with client.stream("GET", fetch_url) as response:
                if response.status_code >= 400:
                    # Fall back to direct download for already-local/plugin URLs.
                    with client.stream("GET", url) as direct:
                        direct.raise_for_status()
                        ext = _guess_extension(url, direct.headers.get("content-type", ""))
                        path = dest_prefix.with_suffix(ext)
                        with path.open("wb") as handle:
                            for chunk in direct.iter_bytes():
                                handle.write(chunk)
                        return path
                response.raise_for_status()
                ext = _guess_extension(url, response.headers.get("content-type", ""))
                path = dest_prefix.with_suffix(ext)
                with path.open("wb") as handle:
                    for chunk in response.iter_bytes():
                        handle.write(chunk)
        if not path.is_file() or path.stat().st_size <= 0:
            raise RuntimeError("downloaded audio is empty")
        return path
