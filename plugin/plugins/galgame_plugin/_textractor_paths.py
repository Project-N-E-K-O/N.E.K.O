from __future__ import annotations

import os
import shutil
from pathlib import Path


TEXTRACTOR_EXECUTABLE = "TextractorCLI.exe"


def _expand_candidate_path(raw_path: str) -> Path:
    return Path(os.path.expanduser(os.path.expandvars(raw_path)))


def _candidate_path_from_env(env_name: str, *parts: str) -> Path | None:
    base = str(os.getenv(env_name) or "").strip()
    if not base:
        return None
    return Path(base).joinpath(*parts)


def _iter_textractor_candidates(
    configured_path: str,
    *,
    install_target_dir_raw: str = "",
) -> list[Path]:
    candidates: list[Path] = []
    seen: set[str] = set()

    def _add(candidate: Path | None) -> None:
        if candidate is None:
            return
        key = os.path.normcase(str(candidate))
        if key in seen:
            return
        seen.add(key)
        candidates.append(candidate)

    configured = str(configured_path or "").strip()
    if configured:
        _add(_expand_candidate_path(configured))
    install_target_dir = str(install_target_dir_raw or "").strip()
    if install_target_dir:
        _add(_expand_candidate_path(f"{install_target_dir}/{TEXTRACTOR_EXECUTABLE}"))
    path_hit = shutil.which(TEXTRACTOR_EXECUTABLE)
    if path_hit:
        _add(Path(path_hit))
    _add(
        _candidate_path_from_env(
            "LOCALAPPDATA",
            "Programs",
            "Textractor",
            TEXTRACTOR_EXECUTABLE,
        )
    )
    _add(_candidate_path_from_env("ProgramFiles", "Textractor", TEXTRACTOR_EXECUTABLE))
    _add(
        _candidate_path_from_env(
            "ProgramFiles(x86)",
            "Textractor",
            TEXTRACTOR_EXECUTABLE,
        )
    )
    return candidates


def resolve_textractor_path(configured_path: str, *, install_target_dir_raw: str = "") -> str:
    for candidate in _iter_textractor_candidates(
        configured_path,
        install_target_dir_raw=install_target_dir_raw,
    ):
        if candidate.is_file():
            return str(candidate)
    return ""
