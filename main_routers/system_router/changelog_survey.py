# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Changelog and in-app survey endpoints.

Split out of the former monolithic ``main_routers/system_router.py``.
"""

from ._shared import _read_json_object, _validate_local_mutation_request, logger, router
import os
import asyncio
import json
import re
from fastapi import Request
from ..shared_state import get_config_manager


# --- 版本更新日志 ---

@router.get("/changelog")
async def get_changelog(since: str = "", lang: str = ""):
    """Return all changelog entries since the given version.

    The frontend passes the lastNotifiedVersion stored in localStorage; the backend
    returns all changelog entries > since (ascending by version) plus the current
    version number.
    The lang parameter is the frontend locale (e.g. zh-CN / en / ja / ko / ru / zh-TW).
    A concrete locale (including Chinese variants like zh-TW) prefers its own subdir
    first; non-Chinese locales then fall back to en; everything finally lands on the
    Simplified Chinese base file. Mirrors the survey loader's fallback chain.
    """
    from config import APP_VERSION
    import glob as _glob

    def _parse_ver(s: str) -> tuple[int, ...]:
        """Convert '0.7.3' into a comparable int tuple; returns (0,) on parse failure."""
        try:
            return tuple(int(x) for x in s.strip().split("."))
        except (ValueError, AttributeError):
            return (0,)

    # __file__ is one level deeper than the former main_routers/system_router.py,
    # so climb three dirs (not two) to reach the repo root.
    changelog_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config", "changelog")
    entries: list[dict] = []
    since_ver = _parse_ver(since) if since else (0,)

    # lang 来自 query string，下面会拼进 os.path.join(changelog_dir, lang, ...)，
    # 先白名单化挡路径穿越（与 survey 下发口共用 _safe_locale）。
    lang = _safe_locale(lang)
    # 确定 fallback 链，与 survey 下发口（_load_survey_for_version）保持一致：
    # 具体 locale（含 zh-TW 等中文变体）先试自己的子目录 -> 非中文再回退 en ->
    # 最后都落到简体中文原文（zh_content）。zh-TW 也 startswith("zh")，但简体
    # base 并无 zh-CN/ 子目录，所以简体请求自然落回原文，不受影响。
    is_chinese = lang.startswith("zh") if lang else True
    fallback_langs: list[str] = []
    if lang:
        fallback_langs.append(lang)
    if not is_chinese and "en" not in fallback_langs:
        fallback_langs.append("en")

    def _read_localized(stem: str, zh_content: str) -> str:
        """Look up the localized version along the fallback chain; returns the original Chinese when not found."""
        for loc in fallback_langs:
            loc_file = os.path.join(changelog_dir, loc, f"{stem}.md")
            try:
                with open(loc_file, "r", encoding="utf-8") as f:
                    return f.read()
            except Exception:
                continue
        return zh_content

    if os.path.isdir(changelog_dir):
        for md_file in sorted(_glob.glob(os.path.join(changelog_dir, "*.md")),
                              key=lambda p: _parse_ver(os.path.splitext(os.path.basename(p))[0])):
            stem = os.path.splitext(os.path.basename(md_file))[0]
            file_ver = _parse_ver(stem)
            if file_ver == (0,):
                continue
            if file_ver > since_ver:
                try:
                    with open(md_file, "r", encoding="utf-8") as f:
                        zh_content = f.read()
                except Exception:
                    zh_content = ""
                content = _read_localized(stem, zh_content)
                entries.append({"version": stem, "content": content})

    return {"current_version": APP_VERSION, "entries": entries}


_LOCALE_RE = re.compile(r'^[A-Za-z]{2,8}(-[A-Za-z0-9]{2,8})*$')


def _safe_locale(lang: object) -> str:
    """Whitelist a client-supplied locale (zh-CN / en / ja / ...) before it touches a filesystem path.

    ``lang`` arrives from the request query string and is joined into changelog /
    survey file paths; an unfiltered ``../`` or an absolute prefix would let a
    crafted value escape the content dir (path traversal). Anything not matching the
    locale shape returns '' (→ caller falls back to the Chinese base / en).
    """
    return lang if (isinstance(lang, str) and _LOCALE_RE.match(lang)) else ""


def _load_survey_for_version(version: str, lang: str) -> dict | None:
    """Load config/surveys/<version>.json with a per-locale fallback chain.

    Returns the parsed (localized) survey dict, or None when no survey exists for
    the version. Fallback: a concrete locale tries its own subdir first (incl.
    Chinese variants like zh-TW); Chinese variants then fall back to the Simplified
    base file, non-Chinese fall back to en, and everything finally lands on the
    base. This loader is independent of ``_load_changelog`` — changing it does not
    touch changelog's language fallback. The whole file is swapped per locale
    (question ids must stay identical across locales — answers are reported by id).
    """
    # Same three-level climb as _load_changelog above (package is one dir deeper
    # than the former monolithic module).
    surveys_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config", "surveys")
    base_file = os.path.join(surveys_dir, f"{version}.json")
    if not os.path.isfile(base_file):
        return None

    # 任何具体 locale 先试自己的子目录（含 zh-TW 等中文变体，于是繁体不再被并入
    # 简体 base）；中文变体回退到简体 base，非中文回退 en，最后都落 base。
    candidates: list[str] = []
    if lang:
        candidates.append(os.path.join(surveys_dir, lang, f"{version}.json"))
    is_chinese = lang.startswith("zh") if lang else True
    if not is_chinese:
        en_path = os.path.join(surveys_dir, "en", f"{version}.json")
        if en_path not in candidates:
            candidates.append(en_path)
    candidates.append(base_file)

    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if isinstance(data, dict):
            # 强制归一到文件版本（= APP_VERSION），不用 setdefault：本地化文件若误写了
            # 别的 survey_version，会让前端去重键和上报版本错位、统计分裂。
            data["survey_version"] = version
            return data
    return None


def _sanitize_survey_answers(answers: object) -> dict:
    """Whitelist + cap the answer dict before forwarding (abuse / oversized-payload guard).

    Mirrors the remote server's data-minimization contract: at most 50 questions,
    keys <= 64 chars, string answers <= 2000 chars, list answers <= 50 items of
    <= 200 chars each. Anything else is dropped.
    """
    out: dict = {}
    if not isinstance(answers, dict):
        return out
    for i, (k, v) in enumerate(answers.items()):
        if i >= 50:
            break
        if not isinstance(k, str) or not k:
            continue
        key = k[:64]
        if isinstance(v, bool):
            out[key] = v
        elif isinstance(v, str):
            out[key] = v[:2000]
        elif isinstance(v, (int, float)):
            out[key] = v
        elif isinstance(v, list):
            out[key] = [str(x)[:200] for x in v[:50] if isinstance(x, (str, int, float, bool))]
    return out


def _resolve_survey_for_request(version: str, lang: str) -> dict | None:
    """Steam gate + localized survey load (sync; runs in a worker thread).

    Survey is Steam-only: a non-Steam install gets None (-> has_survey:false). The
    judgment is distribution=='steam' (live Steam64 / workshop subscription /
    workshop_config.json disk fallback; see survey_client.is_steam_user). On any
    error in the steam check we fail closed (None) — better to skip the popup than
    to show it to a possibly-non-Steam user.
    """
    try:
        from utils.survey_client import is_steam_user
        if not is_steam_user():
            return None
    except Exception:
        return None
    return _load_survey_for_version(version, lang)


@router.get("/survey")
async def get_survey(lang: str = ""):
    """Return the survey for the current app version, or {has_survey: false}.

    Two gates before content is served:
    - DNT: opted-out users (NEKO_DO_NOT_TRACK / DO_NOT_TRACK) get nothing — the same
      switch governs passive stats and surveys.
    - Steam-only: non-Steam installs get nothing (judged by the cached Steam64 +
      distribution==steam fallback).
    """
    from config import APP_VERSION

    try:
        from utils.survey_client import is_reporting_enabled
        if not is_reporting_enabled():
            return {"has_survey": False, "survey_version": APP_VERSION}
    except Exception:
        return {"has_survey": False, "survey_version": APP_VERSION}

    survey = await asyncio.to_thread(_resolve_survey_for_request, APP_VERSION, _safe_locale(lang))
    if not survey:
        return {"has_survey": False, "survey_version": APP_VERSION}
    return {
        "has_survey": True,
        "survey_version": survey.get("survey_version", APP_VERSION),
        "survey": survey,
    }


@router.post("/survey/submit")
async def submit_survey(request: Request):
    """Receive the user's survey answers (or a skip) and forward them, HMAC-signed, to the remote survey server.

    Best-effort: a failed upload still returns ok=True so the frontend records the
    survey as done and never re-prompts; uploaded reflects whether the remote 200'd.
    """
    payload = await _read_json_object(request)
    validation_error = _validate_local_mutation_request(request, payload=payload)
    if validation_error is not None:
        return validation_error

    from config import APP_VERSION

    action = payload.get("action")
    if action not in ("submit", "skip"):
        action = "submit"
    # survey_version 用服务端 APP_VERSION 权威值，不信客户端传入——否则恶意请求可写
    # 任意版本污染远端版本维度。问卷本就只对当前版本下发，没有跨版本提交的合法场景。
    survey_version = APP_VERSION
    answers = _sanitize_survey_answers(payload.get("answers"))

    uploaded = False
    try:
        from utils.survey_client import report_survey
        config_dir = None
        try:
            config_dir = get_config_manager().config_dir
        except Exception:
            config_dir = None
        uploaded = await asyncio.to_thread(
            report_survey, survey_version, action, answers, config_dir=config_dir
        )
    except Exception as e:
        logger.warning("survey submit forward failed: %s", e)

    return {"ok": True, "uploaded": bool(uploaded)}
