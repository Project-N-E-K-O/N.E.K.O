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

"""
Survey reporting client (Python side).

Mirrors the telemetry upload path in ``utils.token_tracker``: the browser never
talks to the remote survey server directly. The frontend POSTs answers to the
local backend (``/api/survey/submit``); this module signs the payload with HMAC
and forwards it to the remote survey collection server.

Anonymity / identity (device_id) and the Do-Not-Track opt-out are **shared with
telemetry** — we reuse token_tracker's helpers so a user who disabled telemetry
(NEKO_DO_NOT_TRACK / DO_NOT_TRACK) is silently excluded here too. The HMAC secret
and server URL are this channel's own (distinct from telemetry).
"""
from __future__ import annotations

import gzip
import hashlib
import hmac
import json
import logging
import time
import urllib.request

logger = logging.getLogger(__name__)

# ★ Set before release: survey collection-server URL. Empty means no reporting.
# Uses a different port from telemetry (survey_server defaults to 8100).
_SURVEY_SERVER_URL = "http://118.31.122.91:8100"

if _SURVEY_SERVER_URL and not _SURVEY_SERVER_URL.startswith(("http://", "https://")):
    logger.warning("Survey client: invalid server URL scheme, disabling remote reporting")
    _SURVEY_SERVER_URL = ""

# ★ Set before release: HMAC signing key, matching DEFAULT_HMAC_SECRET in survey_server/security.py.
# Intentionally use a **different** key from telemetry: the reporting channels do not vouch for each other.
_SURVEY_HMAC_SECRET = "neko-survey-v1-7d2e9c4b8a1f60533e7a2b9c8d4f1e06"  # noqa: S105

_SURVEY_TIMEOUT = 10  # Seconds
_SURVEY_GZIP_THRESHOLD = 1024  # gzip only at >= 1KB (not worthwhile for small payloads)


def _compute_signature(payload_json: str, timestamp: float) -> str:
    """HMAC-SHA256(secret, f"{timestamp}|{sha256(payload_json)}") — same algorithm as telemetry, different secret."""
    body_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    message = f"{timestamp}|{body_hash}"
    return hmac.new(
        _SURVEY_HMAC_SECRET.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def is_reporting_enabled() -> bool:
    """Whether survey reporting is enabled: False when no server URL is configured or Do-Not-Track is on.

    Shares telemetry's single DNT switch — a user who disabled passive stats has
    both the survey popup and its reporting skipped (the DNT gate governs both the
    ``GET /api/survey`` serving and this upload).
    """
    if not _SURVEY_SERVER_URL:
        return False
    try:
        from utils.token_tracker import _DO_NOT_TRACK
        if _DO_NOT_TRACK:
            return False
    except Exception:
        # Conservatively avoid reporting if the switch cannot be read.
        return False
    return True


def is_steam_user() -> bool:
    """Whether this install counts as a Steam user (survey is Steam-only).

    Lenient rule: distribution=='steam' — which covers a live Steam64, workshop
    subscriptions, or the workshop_config.json disk fallback that proves this
    machine ran the Steam edition. A Steam-launched session always has the client
    running, so the live signal is reliable; the disk fallback catches the rest. A
    source/dev build (distribution=='source') sees no Steam signal -> non-Steam,
    skipping the survey exactly like production (no dev backdoor).
    """
    try:
        from utils.token_tracker import _get_telemetry_metadata
        dist, _live = _get_telemetry_metadata()
    except Exception:
        return False
    return dist == "steam"


def report_survey(
    survey_version: str,
    action: str,
    answers: dict | None,
    *,
    config_dir=None,
) -> bool:
    """Sign and upload one survey response (best-effort, blocking).

    Call from a worker thread (e.g. ``asyncio.to_thread``) so it never blocks the
    event loop. Returns True only on a 200 from the remote server; any failure
    (DNT off-switch, network, non-200) returns False without raising — the caller
    still records the survey as "done" locally so it won't re-pop.

    ``action`` is 'submit' or 'skip'. ``answers`` maps question_id -> value
    (string or list of strings); ignored / forced empty when action == 'skip'.
    """
    if not is_reporting_enabled():
        return False

    if action not in ("submit", "skip"):
        action = "submit"
    if action == "skip":
        answers = {}
    answers = answers or {}

    try:
        from utils.token_tracker import (
            _get_anonymous_device_id,
            _get_legacy_device_id,
            _get_app_version_from_changelog,
            _get_telemetry_locale,
            _get_telemetry_branch,
            _get_telemetry_metadata,
        )

        device_id = _get_anonymous_device_id()
        # Fetch distribution + live Steam64 together from the same source. Surveys are Steam-only (enforced by delivery),
        # and including the id aligns device↔account dimensions with telemetry; when Steam is not running
        # and no id is available, use an empty string, matching telemetry's 'steam + empty id' semantics.
        distribution, steam_user_id = _get_telemetry_metadata()
        branch = "unknown"
        if config_dir is not None:
            try:
                branch = _get_telemetry_branch(config_dir)
            except Exception:
                branch = "unknown"

        payload = {
            "device_id": device_id,
            "device_id_legacy": _get_legacy_device_id(),
            "app_version": _get_app_version_from_changelog(),
            "survey_version": str(survey_version or "unknown"),
            "locale": _get_telemetry_locale(),
            "branch": branch,
            "distribution": distribution,
            # Steam64 (cache first). Surveys are Steam-only; include the id to align with telemetry's
            # device↔account dimensions; non-Steam requests cannot reach here (delivery blocks them).
            "steam_user_id": steam_user_id or "",
            "action": action,
            "answers": answers,
        }
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)

        ts = time.time()
        sig = _compute_signature(payload_json, ts)

        # batch_id combines device + survey_version + action. Retries of the same failed action
        # retain it (server seen_batches deduplication); submit and skip differ (one funnel record for each).
        batch_core = {"device_id": device_id, "survey_version": payload["survey_version"], "action": action}
        batch_id = hashlib.sha256(
            json.dumps(batch_core, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()[:32]

        submission = {
            "timestamp": ts,
            "signature": sig,
            "payload": payload,
            "batch_id": batch_id,
        }
        body = json.dumps(submission, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if len(body) >= _SURVEY_GZIP_THRESHOLD:
            body = gzip.compress(body, compresslevel=6, mtime=0)
            headers["Content-Encoding"] = "gzip"

        req = urllib.request.Request(
            f"{_SURVEY_SERVER_URL}/api/v1/survey",
            data=body,
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=_SURVEY_TIMEOUT) as resp:
            if resp.status == 200:
                logger.debug("Survey client: reported successfully (action=%s)", action)
                return True
        return False
    except Exception as e:
        logger.debug("Survey client: report failed (non-critical): %s", e)
        return False
