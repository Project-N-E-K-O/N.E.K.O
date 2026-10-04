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

"""Catgirl visit (cross-machine visiting) constants.

Single source of truth for every number of the visit infrastructure
(``docs/design/visit-infrastructure.md`` §4.8). Grouped by axis; each
assignment is followed by an English docstring naming its consumer. Env
overrides go through ``config.network._read_bool_env / _read_str_env``.

Cross-constant invariants that the design relies on are checked once at
import time (``_check_invariants`` at the bottom) so a later edit to one
number cannot silently break another axis.
"""

from __future__ import annotations

from .network import _read_bool_env, _read_str_env

# ── 发布总闸与开发环回 ─────────────────────────────────────────────────

VISIT_ENABLED = _read_bool_env("VISIT_ENABLED", False)
"""Release gate, read from ``NEKO_VISIT_ENABLED`` (default off).

When off only the entry points that start or join a visit return 404 and the
settings page hides the visit group; data management endpoints and background
tasks (upload retry, crash recovery, forget replay) keep running. Python code
must reference this constant, never the env var name."""

NEKO_VISIT_ALLOW_NONLOCAL = _read_bool_env("VISIT_ALLOW_NONLOCAL", False)
"""Escape hatch for non-loopback / reverse-proxy deployments (default off).

Turning it on disables both the loopback check and the "proxy headers mean
reject" rule for ``/api/visit/*``, the transport WS and ``visit_bind``.
Operators must authenticate at the proxy and overwrite (not append) XFF."""

NEKO_VISIT_DEV_KEYFILE = _read_str_env("VISIT_DEV_KEYFILE", "")
"""Path to a local Ed25519 private key used only by the dev loopback.

When non-empty its public key is added under ``VISIT_DEV_KID``; the ticket
verification code path is identical to production (no skip-verify branch)."""

NEKO_VISIT_DEV_LIVEKIT_URL = _read_str_env("VISIT_DEV_LIVEKIT_URL", "")
"""Local LiveKit server URL for the dev loopback (empty = unused)."""

NEKO_VISIT_DEV_LIVEKIT_API_KEY = _read_str_env("VISIT_DEV_LIVEKIT_API_KEY", "")
"""Local LiveKit API key for the dev loopback (empty = unused)."""

NEKO_VISIT_DEV_LIVEKIT_SECRET = _read_str_env("VISIT_DEV_LIVEKIT_SECRET", "")
"""Local LiveKit API secret for the dev loopback (empty = unused)."""

# ── 用户设置默认值（ALLOWED_CONVERSATION_SETTINGS 三键）──────────────────

VISIT_ENABLED_DEFAULT = False
"""Default of the ``visitEnabled`` conversation setting (off)."""

VISIT_MEMORY_DEFAULT = True
"""Default of the hidden ``visitMemoryEnabled`` conversation setting (on).

Read once per visit when it activates and frozen into
``state.json.memory_enabled``; a mid-visit change applies from the next one."""

VISIT_VOICE_DEFAULT = True
"""Default of the ``visitVoiceEnabled`` conversation setting (on)."""

# ── 生命周期（OD-11 v2）────────────────────────────────────────────────

VISIT_HEARTBEAT_S = 5
"""Interval of the ``hb`` data-channel message."""

VISIT_PEER_LOST_S = 30
"""The only peer-death clock: no message from the peer for this long → ``peer_lost``.

Host side starts it after the peer ``hello`` verifies; guest side counts from
its own room join (it also waits only this long for the host ``hello``)."""

VISIT_SELF_RECONNECT_S = 25
"""Upper bound of the own SDK reconnect window.

The actual deadline is ``min(disconnect + 25 s, last successful send + 30 s -
VISIT_RECONNECT_MARGIN_S)``."""

VISIT_RECONNECT_MARGIN_S = 3
"""Margin subtracted from "last successful send + VISIT_PEER_LOST_S"."""

VISIT_LOCAL_PAGE_GRACE_S = 20
"""Grace after the transport WS drops (page reload) before ``local_page_lost``."""

VISIT_SHUTDOWN_BUDGET_S = 3
"""Budget for ``stop_all`` at the front of ``on_shutdown``."""

VISIT_INVITE_CODE_TTL_S = 600
"""Lifetime of an invite code (10 min)."""

VISIT_INVITE_WAIT_S = 600
"""Host only: wait limit before the peer ``hello`` verifies → ``invite_expired``.

Equal to the invite code lifetime."""

VISIT_JOIN_ALLOWANCE_S = 60
"""When the host observes the peer entering the room, its wait deadline is
extended to ``max(deadline, now + 60 s)`` to leave room for hello verification."""

VISIT_ACCEPT_TIMEOUT_S = 60
"""Host reception decision window, counted from receiving and acking the peer ``hello``."""

VISIT_ACTIVATION_ALLOWANCE_S = 15
"""After the host accepts: deadline for local init and sending ``ready``."""

VISIT_READY_DELIVERY_MARGIN_S = 10
"""Delivery margin for ``ready``; guest waits 60 + 15 + 10 = 85 s after its hello is acked."""

VISIT_MAX_DURATION_S = 1800
"""Hard cap of one visit (30 min)."""

VISIT_TIME_UP_WRAP_UP_S = 60
"""Wrap-up starts at ``max_duration - 60 s`` with ``reason='time_up'``."""

VISIT_ENDING_SOON_S = 120
"""Badge hint shown this long before the hard cap."""

VISIT_IDLE_TIMEOUT_S = 300
"""No line from either side for this long → ``idle_timeout`` (hidden time excluded)."""

VISIT_SWEEP_INTERVAL_S = 2
"""Tick interval of ``visit_sweep_loop``."""

VISIT_LEAVE_DRAIN_S = 2
"""On a normal finalize, wait at most this long for the outbox to drain before ``leave``."""

VISIT_LEAVE_GAP_GRACE_S = 5
"""After ``leave``: how long the receiver waits for gaps ``<= last_seq`` to be
filled; the sender keeps retransmitting for the same window."""

VISIT_PEER_REJOIN_GRACE_S = 35
"""An explicit vendor-level leave of the peer is tentative for this long.

Must be at least ``VISIT_LOCAL_PAGE_GRACE_S`` plus a 15 s SDK reload budget (design
invariant ``REJOIN >= LOCAL_PAGE + 15``). The reload itself is bounded by the
absolute deadline of ``VISIT_PAGE_REJOIN_SAFETY_S``; the capability gate's
``VISIT_CAPS_SDK_TIMEOUT_S`` is a separate timer, also capped by what is left
of that deadline."""

VISIT_PAGE_REJOIN_SAFETY_S = 5
"""A reloaded page must be back in the vendor room this long before the peer's rejoin grace ends.

Absolute reload deadline = ``min(left + VISIT_PEER_REJOIN_GRACE_S - this,
last successful send + VISIT_PEER_LOST_S - VISIT_RECONNECT_MARGIN_S)``."""

VISIT_CAPS_PREFLIGHT_TIMEOUT_S = 15
"""Wait limit for capability gates 1-2 (before credentials)."""

VISIT_CAPS_SDK_TIMEOUT_S = 20
"""Wait limit for capability gate 3 (vendor SDK load) → ``unsupported``."""

VISIT_INBOX_HANDOFF_MAX_S = 20
"""Fallback cap for handing ``VisitInbox`` back after finalize.

Counted only after both the ceremony line and the debrief summary are queued
to TTS; extended while either is still reporting playback."""

VISIT_INBOX_HANDOFF_ABS_MAX_S = 120
"""Absolute ceiling of the ``VisitInbox`` hand-back deadline."""

# ── 数据通道、可靠层与限速（OD-30）──────────────────────────────────────

VISIT_WIRE_PROTO = 1
"""Wire protocol major version (``hello.caps.proto``); mismatch → ``proto_mismatch``."""

VISIT_PIECE_MAX_BYTES = 1000
"""Max encoded bytes of one envelope piece (bytes, not 1024)."""

VISIT_PIECES_MAX = 8
"""Max pieces of one payload; counted on the fully encoded form."""

VISIT_DELTA_TEXT_MAX_BYTES = 800
"""Max raw UTF-8 bytes of ``line_delta.txt``."""

VISIT_LINE_DELTA_PAYLOAD_MAX_BYTES = 900
"""Max fully encoded bytes of one ``line_delta`` (payload JSON escaped again as
envelope ``p``); guarantees a delta always fits one piece."""

VISIT_TEXT_MAX_BYTES = 4096
"""First cap of ``text.txt`` (``clamp_text_utf8``)."""

VISIT_LINE_MAX_TOKENS = 400
"""Token cap applied by ``sanitize_relay_text`` to relayed text."""

VISIT_HUMAN_LINE_MAX_TOKENS = 600
"""Token cap of a human line (not streamed, sanitized as a whole)."""

VISIT_LINE_DELTA_MAX_I = 255
"""Upper bound of ``line_delta.i`` and ``i_done``; checked before indexing."""

VISIT_REASSEMBLY_TIMEOUT_S = 6
"""A payload whose pieces are not all in this long after the first is dropped."""

VISIT_REASSEMBLY_MAX_ENTRIES = 32
"""Max in-flight reassembly entries; the oldest is evicted beyond it."""

VISIT_UPLOAD_MAX_LINES = 7500
"""Max lines of one uploaded transcript (Servers side contract)."""

VISIT_INBOUND_TEXT_BURST = 90
"""Receiver token bucket capacity for peer ``text`` (refill 2/s) = 20 + 2 × rejoin grace."""

VISIT_INBOUND_TEXT_REFILL_PER_S = 2
"""Refill rate of the receiver ``text`` bucket (= 20 per 10 s)."""

VISIT_INBOUND_TEXT_MAX = 3750
"""Hard cap of peer ``text`` accepted per visit = ``VISIT_UPLOAD_MAX_LINES / 2``."""

VISIT_OWN_TEXT_PER_10S = 20
"""Local hard limit on outgoing ``text`` (cat and human lines combined) per 10 s."""

VISIT_VENDOR_WINDOW_BYTES = 6144
"""iframe 1 s sliding window of sent piece bytes (below TRTC 8 KB/s)."""

VISIT_VENDOR_WINDOW_MSGS = 20
"""iframe 1 s sliding window of sent pieces (below TRTC 30/s)."""

VISIT_UPLOAD_PENDING_CAP_BYTES = 200 * 1024 * 1024
"""Admission cap of unreclaimable files (pending uploads + unsettled spools);
reaching it refuses new visits with 409 ``VISIT_UPLOAD_BACKLOG``."""

VISIT_DATA_BUCKET_BPS = 5120
"""Outgoing byte bucket rate (5 KB/s ≈ 40 kbps)."""

VISIT_DATA_BUCKET_BURST_BYTES = 8192
"""Byte bucket capacity; must hold one max encoded ``text`` (8 pieces × 1 KB)."""

VISIT_MSG_BUCKET_PER_S = 20
"""Outgoing message-count bucket rate."""

VISIT_MSG_BUCKET_BURST = 10
"""Outgoing message-count bucket capacity."""

VISIT_DELTA_MIN_INTERVAL_MS = 250
"""Two pieces of one line closer than this are merged into one ``line_delta``."""

VISIT_DELTA_BACKLOG_MERGE_S = 3
"""Queue backlog beyond this merges adjacent deltas of a line up to 900 encoded bytes."""

VISIT_DELTA_BACKLOG_DROP_S = 10
"""Queue backlog beyond this drops the remaining deltas of a line (``text`` covers it)."""

VISIT_OUTBOX_RETRY_S = (1, 2, 4, 8, 8)
"""Retransmit schedule of reliable messages; after the last step keep retrying every 8 s."""

VISIT_DELIVERY_TIMEOUT_S = 30
"""A reliable item unacked for this long (connected, peer-present time only) → ``delivery_failed``."""

VISIT_OUTBOX_PENDING_MAX_BYTES = 20480
"""In-flight encoded bytes cap of unacked reliable messages
= ``VISIT_DATA_BUCKET_BPS × (VISIT_LEAVE_GAP_GRACE_S - 1)``."""

VISIT_ACK_COALESCE_MS = 500
"""Cumulative ack is sent at most this long after a reliable message lands."""

VISIT_DEDUP_LRU = 512
"""LRU size of the ``ln`` / ``seq`` idempotency sets."""

VISIT_REORDER_BUFFER_MAX = 128
"""Max reliable messages buffered behind a ``seq`` gap; beyond → ``peer_protocol_violation``."""

VISIT_LP_MAX = 2 ** 53 - 1
"""Upper bound of a Lamport value (``lp`` / ``lp_seen``)."""

VISIT_LP_MAX_JUMP = 10000
"""Max forward jump of one ``lp`` over the highest seen value."""

VISIT_LP_REGRESS_MAX = 1000
"""An ``lp`` regression larger than this on a new line counts as an anomaly."""

VISIT_ANOMALY_FINALIZE_COUNT = 20
"""Consecutive anomalies (unknown ``t`` excluded) that finalize with ``peer_protocol_violation``."""

VISIT_PEER_TEXT_PER_10S = 20
"""Per-sender receive limit of text-channel messages per 10 s."""

VISIT_PEER_CTL_PER_S = 4
"""Per-sender receive limit of control messages per second."""

VISIT_PEER_LOSSY_PER_S = 2
"""Per-sender receive limit of lossy messages per second."""

VISIT_PEER_RECV_BPS = 10240
"""Per-sender receive byte limit at the ``recv`` entry (10 KB/s)."""

VISIT_PEER_RECV_MSGS_PER_S = 40
"""Per-sender receive message limit at the ``recv`` entry."""

VISIT_STREAM_DELTAS = True
"""Emergency switch: False falls subtitles back to whole-line mode (TTS still streams)."""

VISIT_CLAUSE_SOFT_MAX_CHARS = 24
"""Comma-level split only once the clause holds this many CJK characters."""

VISIT_CLAUSE_SOFT_MAX_LATIN_WORDS = 12
"""Comma-level split threshold for Latin text, in words."""

VISIT_CLAUSE_MIN_CHARS = 2
"""Fragments shorter than this merge into the next clause."""

VISIT_LINE_STALL_S = 20
"""No new piece and no ``text`` for this long → the receiver truncates locally."""

VISIT_TYPING_CLEAR_S = 8
"""Typing indicator clears itself when no first piece arrives within this long."""

# ── 身份与凭证（OD-01 v2）──────────────────────────────────────────────

VISIT_TICKET_VERSION = 1
"""Required ``v`` claim of the identity ticket."""

VISIT_TICKET_ISS = "neko-servers"
"""Required ``iss`` claim of the identity ticket."""

VISIT_TICKET_AUD = "neko-visit"
"""Required ``aud`` claim of the identity ticket."""

VISIT_DEV_KID = "dev"
"""kid under which the dev loopback public key (``NEKO_VISIT_DEV_KEYFILE``) is added."""

VISIT_SERVERS_PUBKEYS: dict[str, dict] = {}
"""Built-in Servers Ed25519 public keys: ``{kid: {pub, not_before, not_after}}``.

``pub`` is base64url of the 32-byte raw key; the validity window bounds the
ticket ``iat``. Rotated by release; ``GET /api/visit/pubkeys`` is the second
source and its ``revoked`` list overrides this table. Production kids are
filled when Servers issues them; until then only the dev loopback key exists."""

VISIT_PUBKEYS_CACHE_S = 86400
"""Cache upper bound of the fetched pubkey set; stale + refresh failure fails closed."""

VISIT_TICKET_CLOCK_TOLERANCE_S = 300
"""Clock tolerance on ``iat`` / ``exp`` (same as telemetry)."""

VISIT_CREDENTIAL_TTL_S = 2400
"""Guest identity ticket lifetime (40 min); vendor grants use ``VISIT_VENDOR_GRANT_TTL_S``."""

VISIT_HOST_CREDENTIAL_TTL_S = 3000
"""Host identity ticket lifetime (50 min) = invite wait + max duration + 600 s margin."""

VISIT_VENDOR_GRANT_TTL_S = 600
"""Vendor room grant lifetime (TRTC UserSig / LiveKit JWT); renewed before reconnects."""

VISIT_VENDOR_REFRESH_MARGIN_S = 120
"""Renew the vendor grant (``credentials{refresh:true}``) once less than this remains."""

VISIT_BANNED_CACHE_S = 60
"""A Servers ``403 banned`` is remembered this long; new rooms / joins answer 403 locally."""

VISIT_SHORT_ID_LEN = 6
"""UI shows only ``visit_uid[:6].upper()``."""

VISIT_LIVEKIT_HOSTS: frozenset[str] = frozenset()
"""Exact LiveKit host names the iframe accepts (Cloud + self-hosted domains).

Empty until deployment domains are fixed, which makes LiveKit fail closed."""

VISIT_FREE_MINUTES_PER_DAY = 120
"""Placeholder of the free daily minutes (enforced by Servers; UI copy only)."""

VISIT_MAX_CONCURRENT_ROOMS_PER_ACCOUNT = 2
"""Servers-side concurrent room limit per account (documentation only)."""

# ── 视觉通道（OD-06 v2）────────────────────────────────────────────────

VISIT_TIERS: dict[str, dict] = {
    "sd600": {
        "enabled": True,
        "crop_upper": (320, 448),
        "crop_full": (256, 560),
        "pack_upper": (320, 896),
        "pack_full": (256, 1120),
        "fps": 30,
        "video_kbps": 560,
        "data_kbps_max": 40,
        "trtc_tier": "sd",
    },
    "hd1200": {
        "enabled": False,
        "crop_upper": (480, 672),
        "crop_full": None,
        "pack_upper": (480, 1344),
        "pack_full": None,
        "fps": 30,
        "video_kbps": 1150,
        "data_kbps_max": 50,
        "trtc_tier": "hd",
    },
    "fhd2400": {
        "enabled": False,
        "crop_upper": (720, 1008),
        "crop_full": None,
        "pack_upper": (720, 2016),
        "pack_full": None,
        "fps": 30,
        "video_kbps": 2300,
        "data_kbps_max": 100,
        "trtc_tier": "fhd",
    },
}
"""Video tiers; only ``sd600`` is published, the other two are paid placeholders
(their full-body framing is not designed yet, hence ``None``)."""

VISIT_VIDEO_TIER_DEFAULT = "sd600"
"""The only published tier."""

VISIT_VIDEO_KBPS = 560
"""Video bitrate cap."""

VISIT_DATA_KBPS_MAX = 40
"""Data channel budget (= 5 KB/s)."""

VISIT_CAPTURE_FPS = 30
"""Target of the fractional frame accumulator; 30 is a hard floor."""

VISIT_CROP_DEFAULT = "upper"
"""Default framing (upper body); ``'full'`` is 256×560."""

VISIT_CROP_REFRESH_MS = (300, 1000)
"""Refresh interval range of the crop box."""

VISIT_CROP_HYSTERESIS = {"center_pct": 4, "size_pct": 8, "transition_ms": 300}
"""Crop hysteresis: center 4 %, size 8 %, 300 ms transition."""

VISIT_CONGESTION_LADDER = {
    "upper": ((320, 448, 560), (256, 352, 400), (192, 272, 300)),
    "full": ((256, 560, 560), (208, 448, 400), (160, 352, 300)),
}
"""Congestion ladder per framing: ``(w, h, kbps)``; only resolution drops, never fps."""

VISIT_CONGESTION_TRIGGER_S = 10
"""``rx_fps < 24`` or uplink loss > 15 % for this long → step down."""

VISIT_CONGESTION_RECOVER_S = 30
"""Clean for this long → step up."""

VISIT_CONGESTION_MIN_KBPS = 300
"""Lowest ladder bitrate (lower bound of the TRTC standard-definition band)."""

VISIT_PACK_AREA_MAX = 307200
"""TRTC standard-definition area bound; every packed canvas must stay below it."""

VISIT_LIVEKIT_PUBLISH = {
    "videoCodec": "vp9",
    "simulcast": False,
    "scalabilityMode": "L1T1",
    "maxBitrate": 560000,
    "maxFramerate": 30,
    "degradationPreference": "maintain-framerate",
}
"""LiveKit publish options; ``scalabilityMode`` must be explicit (SDK defaults vp9 to L3T3_KEY)."""

VISIT_VP9_SOFTENC_MIN_FPS = 27
"""vp9 encode fps below this for ``VISIT_VP9_CPU_FALLBACK[1]`` s → vp8 next visit."""

VISIT_VP9_CPU_FALLBACK = (VISIT_VP9_SOFTENC_MIN_FPS, 10)
"""(min fps, seconds) of the vp9 software-encoder fallback."""

VISIT_FRAME_STARVATION_S = 1.0
"""No successful ``onFrame`` for this long → ``hidden{on:true}``."""

# ── 对话（OD-08 v2 / OD-15 v3 / OD-21 v3）──────────────────────────────

VISIT_MAX_CAT_TURNS_WITHOUT_HUMAN = 6
"""Six cat lines in a row without any human line → wrap up."""

VISIT_OWN_LINES_PER_VISIT = 40
"""This side's cat said 40 lines → wrap up."""

VISIT_OWN_LINES_PER_MINUTE = 6
"""Per-minute cap of own cat lines; only delays, never wraps up."""

VISIT_REPLY_GAP_S = (1.0, 2.5)
"""Uniform random reply gap added after the peer's ``tail_ms``."""

VISIT_WRAP_UP_STEP_S = 15
"""From ``begin`` until the peer's goodbye starts (first ``wu`` delta or ``wrap_up{speaking}``)."""

VISIT_WRAP_UP_MAX_S = 45
"""Hard cap from ``begin``: finalize unconditionally."""

VISIT_WRAP_UP_PROPOSE_TIMEOUT_S = 5
"""Guest proposes and gets no ``begin`` in time → it starts its goodbye itself."""

VISIT_SPEAKING_ABORT_AFTER_S = 10
"""A non-goodbye line still speaking this long after ``begin`` is aborted."""

VISIT_GOODBYE_LLM_TIMEOUT_S = 8
"""Goodbye generation timeout; the fixed fallback line is used beyond it."""

VISIT_GOODBYE_MAX_CHARS = 40
"""Goodbye line hard cap (prompt asks for it; the delta entry also enforces it)."""

VISIT_MAX_LINES = 80
"""Protocol-violation guard on cat lines of both sides (= 2 × 40); human lines excluded."""

VISIT_RESPONSE_MAX_TOKENS = 160
"""``max_response_length`` of the isolated visit session."""

VISIT_HISTORY_MAX_MESSAGES = 40
"""History trim of the isolated visit session."""

VISIT_CONTEXT_MAX_TOKENS = 2000
"""Total budget of the visit memory block (last-visit summary first, then scoped context)."""

VISIT_LLM_TIMEOUT_S = 20
"""Timeout of one visit LLM call."""

VISIT_CEREMONY_TIMEOUT_S = 8
"""Timeout of the arrival / homecoming ceremony line."""

VISIT_LLM_ERROR_FINALIZE_COUNT = 5
"""Consecutive LLM errors that finalize with ``llm_error``."""

VISIT_PEER_LABEL_MAX_TOKENS = 16
"""Token cap of a peer display label."""

VISIT_SHARE_HUMAN_LABEL = False
"""Whether the human speaker label is shared with the peer (off)."""

VISIT_RECALL_TOOL_ENABLED = False
"""Whether the isolated session gets a recall tool (off)."""

VISIT_TTS_START_TIMEOUT_S = 4
"""No first ``visit_speech_progress`` this long after the first push → estimate fallback."""

VISIT_SPEECH_PROGRESS_STALL_S = 3
"""Playback started but no progress for this long → abort the line, finish by estimate."""

VISIT_CJK_MS_PER_CHAR = 180
"""``estimate_speech_ms``: per CJK character."""

VISIT_LATIN_MS_PER_WORD = 250
"""``estimate_speech_ms``: per Latin word."""

VISIT_PUNCT_END_MS = 250
"""``estimate_speech_ms``: per sentence-ending mark."""

VISIT_PUNCT_COMMA_MS = 120
"""``estimate_speech_ms``: per comma / enumeration mark."""

VISIT_CLAUSE_MIN_MS = 400
"""Lower clamp of ``estimate_speech_ms``."""

VISIT_CLAUSE_MAX_MS = 12000
"""Upper clamp of ``estimate_speech_ms``; also the receiver bound of ``text.tail_ms``."""

# ── 记忆 / spool / debrief（OD-16 v4 / OD-17 v2）────────────────────────

VISIT_SPOOL_DIRNAME = "visit_spool"
"""Spool directory under ``config_dir`` (``.jsonl`` + ``.state.json`` + ``.outbox.jsonl``).

Not synced by Steam cloud save, which only covers ``MANAGED_MEMORY_FILENAMES``."""

VISIT_SPOOL_FSYNC_S = 30
"""Spool fsync cadence (plus once at finalize)."""

VISIT_SPOOL_RETENTION_DAYS = 7
"""Retention of ``state.json`` and unanswered spools."""

VISIT_SPOOL_DIR_CAP_BYTES = 20 * 1024 * 1024
"""Reclaim threshold (not a hard cap): beyond it only settled visits and
uploaded files are deleted."""

VISIT_SPOOL_LINE_MAX_BYTES = 32 * 1024
"""Max encoded JSONL bytes of one spool line; larger lines raise instead of truncating."""

VISIT_MEMORY_PLATFORM = "neko_visit"
"""Platform component of every visit memory subject (``neko_visit:...``).

The scoped persona headers select their visit tables by
``(subject_kind, platform)`` with this value."""

VISIT_PEERS_FILENAME = "visit_peers.json"
"""Peer roster under ``config_dir`` (partitioned by own community account)."""

VISIT_BLOCKLIST_FILENAME = "visit_blocklist.json"
"""Machine-wide blocklist under ``config_dir`` (not partitioned by account)."""

VISIT_REVOCATIONS_DIRNAME = "visit_revocations"
"""Directory of local "forget this person" revocation logs under ``config_dir``."""

VISIT_FORGET_EPOCHS_FILENAME = "visit_forget_epochs.json"
"""Per-subject forget generations under ``config_dir`` (only increase, never deleted)."""

VISIT_REPORTS_DIRNAME = "visit_reports"
"""Directory of queued reports under ``config_dir``; never touched by cleanups."""

VISIT_PERSONA_DIRNAME = "visit_persona"
"""Directory of the reviewed public visit personas (``<character_uid>.json``)."""

VISIT_DIGEST_MAX_LINES = 400
"""Lines fed to one visit digest: all cat lines first, then the newest human lines."""

VISIT_DEBRIEF_DEFAULT = "ask_later"
"""Debrief choice on timeout or crash; never writes private memory by default."""

VISIT_DEBRIEF_TIMEOUT_S = 600
"""Debrief chips expire (but stay clickable) after this long."""

VISIT_DEBRIEF_MAX_TOKENS = 200
"""Output cap of the homecoming summary."""

VISIT_DIARY_MAX_TOKENS = 300
"""Output cap of the diary paragraph."""

VISIT_DIARY_FACTS_MAX = 3
"""Max visit facts extracted together with the diary."""

VISIT_DIARY_FACT_MAX_CHARS = 60
"""Max characters of one visit fact."""

VISIT_DEBRIEF_COMMIT_BACKOFF_S = (30, 120, 600, 3600)
"""Back-off (s) after the n-th transient diary-commit failure; the last item repeats, no attempt cap."""

VISIT_LAST_SUMMARY_HANDOFF_S = 8
"""Wait limit for the previous visit's last-summary commit before opening."""

VISIT_LAST_SUMMARY_MAX_TOKENS = 300
"""Token cap of the "last visit" summary (cut after generation and again at assembly)."""

VISIT_DEBRIEF_INPUT_MAX_TOKENS = 6000
"""Token budget of the transcript block fed to debrief / diary generation."""

VISIT_LAST_SUMMARY_INPUT_MAX_TOKENS = 6000
"""Token budget of the transcript block fed to last-summary generation."""

VISIT_TRANSCRIPT_MEMORY_TTL_S = 600
"""In-memory transcript residence when visit memory is off."""

VISIT_DETAILS_MAX_PAGES = 30
"""Cloud transcript page cap (15000 aligned lines ÷ 500 per page)."""

VISIT_UPLOAD_CHUNK_BYTES = 512 * 1024
"""Transcript upload request bodies beyond this are split into parts."""

VISIT_PEER_NGRAM_N = 8
"""n of ``assert_no_peer_ngram`` (also the persona private-paragraph check)."""

VISIT_PERSONA_MAX_TOKENS = 800
"""Token cap of the public visit persona."""


def _check_invariants() -> None:
    """Raise if the cross-axis relations the design depends on are broken."""
    problems: list[str] = []

    def need(ok: bool, what: str) -> None:
        if not ok:
            problems.append(what)

    need(sum(VISIT_OUTBOX_RETRY_S) < VISIT_PEER_LOST_S,
         "outbox retry schedule must finish before peer death")
    need(VISIT_PEER_LOST_S - VISIT_SELF_RECONNECT_S >= VISIT_HEARTBEAT_S,
         "self reconnect must end a heartbeat before peer death")
    need(VISIT_SELF_RECONNECT_S - VISIT_LOCAL_PAGE_GRACE_S >= VISIT_HEARTBEAT_S,
         "page grace must end a heartbeat before self reconnect")
    need(VISIT_PEER_REJOIN_GRACE_S >= VISIT_LOCAL_PAGE_GRACE_S + 15,
         "rejoin grace must cover page grace plus SDK reload budget")
    need(VISIT_PAGE_REJOIN_SAFETY_S > 0,
         "the page reload must end before the peer's rejoin grace (positive safety margin)")
    need(VISIT_PEER_REJOIN_GRACE_S - VISIT_PAGE_REJOIN_SAFETY_S > VISIT_LOCAL_PAGE_GRACE_S,
         "absolute page reload deadline must outlast the transport WS grace")
    need(VISIT_INVITE_WAIT_S == VISIT_INVITE_CODE_TTL_S,
         "host wait must equal the invite code lifetime")
    guest_ready_wait = (VISIT_ACCEPT_TIMEOUT_S + VISIT_ACTIVATION_ALLOWANCE_S
                        + VISIT_READY_DELIVERY_MARGIN_S)
    need(guest_ready_wait > VISIT_ACCEPT_TIMEOUT_S + VISIT_ACTIVATION_ALLOWANCE_S
         + sum(VISIT_OUTBOX_RETRY_S[:3]),
         "guest ready wait must outlast the latest host send plus three retries")
    need(VISIT_OUTBOX_PENDING_MAX_BYTES
         == VISIT_DATA_BUCKET_BPS * (VISIT_LEAVE_GAP_GRACE_S - 1),
         "outbox in-flight cap must equal the post-leave refill window")
    need(VISIT_DATA_BUCKET_BURST_BYTES >= VISIT_PIECES_MAX * 1024,
         "byte bucket must hold one max encoded text")
    need(VISIT_INBOUND_TEXT_BURST == 20 + 2 * VISIT_PEER_REJOIN_GRACE_S,
         "inbound text burst must cover one rejoin grace")
    need(VISIT_INBOUND_TEXT_MAX == VISIT_UPLOAD_MAX_LINES // 2,
         "inbound text cap must be half the upload line cap")
    need(VISIT_REORDER_BUFFER_MAX >= VISIT_INBOUND_TEXT_BURST,
         "reorder buffer must hold one inbound burst")
    need(VISIT_HOST_CREDENTIAL_TTL_S
         == VISIT_INVITE_WAIT_S + VISIT_MAX_DURATION_S + 600,
         "host ticket must cover invite wait, max duration and margin")
    need(VISIT_CREDENTIAL_TTL_S > VISIT_MAX_DURATION_S,
         "guest ticket must outlive one visit")
    need(0 < VISIT_VENDOR_REFRESH_MARGIN_S < VISIT_VENDOR_GRANT_TTL_S,
         "vendor grant renewal margin must fall inside the grant lifetime")
    need(VISIT_MAX_DURATION_S - VISIT_TIME_UP_WRAP_UP_S + VISIT_WRAP_UP_MAX_S
         < VISIT_MAX_DURATION_S,
         "time-up wrap-up must finish before the hard cap")
    need(VISIT_MAX_LINES == 2 * VISIT_OWN_LINES_PER_VISIT,
         "max lines guard must be twice the per-side cap")
    need(VISIT_LINE_DELTA_PAYLOAD_MAX_BYTES < VISIT_PIECE_MAX_BYTES,
         "a line_delta must always fit one piece")
    need(VISIT_DELTA_TEXT_MAX_BYTES < VISIT_LINE_DELTA_PAYLOAD_MAX_BYTES,
         "delta text cap must leave room for the payload fields")
    if problems:
        raise ValueError("visit_settings invariants broken: " + "; ".join(problems))


_check_invariants()
