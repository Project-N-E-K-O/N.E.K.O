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

"""Settings for the agent tool-call permission gate.

Single home for the vocabulary and the decision table, so the rule lives as data
instead of as ifs spread across the dispatch path. ``config`` is layer 0
(scripts/check_module_layering.py LAYERS), which is what lets main_logic (2),
main_routers (3) and the plugin SDK (4) all read one copy.

Two deliberate refusals:

- No default band lookup. An unmapped speaker is STRANGER because the brief says so,
  and that is written as a rule (``DEFAULT_BAND``) rather than as ``.get(key, ...)``
  sprinkled through callers: a silently-allowed stranger is the failure mode this
  table exists to prevent.
- No per-cell escape hatch. ``PERMISSION_MATRIX`` must be total over 5 bands x 4 risks.
  ``_validate_matrix()`` runs at import, so a missing or misspelled cell stops the
  process instead of surfacing at the first tool call, the same strictness the wire
  model already applies to ``speaker_tier`` (config/memory_settings.py:443).
"""

from enum import StrEnum

# ------------------------------------------------------------------ vocabulary


class TrustBand(StrEnum):
    """How far the speaker of this turn is trusted. Ordered, highest first.

    Kept separate from the memory trust pool (memory/speaker_trust.py): that one is a
    earned, continuously-changing score for memory arbitration. This one is a coarse
    permission band, and the gate must not quietly inherit a score change as a
    permission change -- see ``_BAND_ORDER`` for why ordering is local here.
    """

    OWNER = "owner"
    TRUSTED = "trusted"
    NORMAL = "normal"
    STRANGER = "stranger"
    BLOCKED = "blocked"


class ToolRisk(StrEnum):
    """What a tool does. Declared by whoever writes the tool, never inferred.

    read      looks something up; no state outside the process changes
    self      acts on the character's own state, not on the speaker's world
    write     changes something the speaker or the owner can observe or lose
    external  leaves the machine or talks to a third party on someone's behalf
    """

    READ = "read"
    SELF = "self"
    WRITE = "write"
    EXTERNAL = "external"


class PermissionAction(StrEnum):
    """The decision. ``CONFIRM`` suspends the call and waits for an OWNER."""

    ALLOW = "allow"
    DENY = "deny"
    CONFIRM = "confirm"


# Highest first. ``StrEnum`` gives no ordering, and comparing band *names* would sort
# BLOCKED before OWNER: the order has to be data, and it has to be total.
_BAND_ORDER: tuple[TrustBand, ...] = (
    TrustBand.OWNER,
    TrustBand.TRUSTED,
    TrustBand.NORMAL,
    TrustBand.STRANGER,
    TrustBand.BLOCKED,
)

DEFAULT_BAND: TrustBand = TrustBand.STRANGER
"""What an unmapped speaker is. The brief fixes this; do not soften it in code."""

OWNER_BAND: TrustBand = TrustBand.OWNER
"""The band every account in ``PERMISSION_OWNER_ACCOUNTS`` gets, whatever its source."""

# ------------------------------------------------------------------ source maps
#
# Keys are the canonical account grammar, ``platform:actor``
# (memory/speaker_trust.stable_speaker_id output, memory/identity.py:80). Group keys
# are ``platform:conversation``, the ``MemorySubject.group_chat`` shape
# (memory/scopes.py). Byte-exact matching is deliberate: the same function that
# normalizes ids for the trust pool normalizes these, so one spelling mistake cannot
# split one person into two bands.

PERMISSION_SOURCE_BANDS: dict[str, TrustBand] = {
    # The local UI is the owner's own machine. Anything not listed here and not listed
    # as an owner account is a stranger, including a platform that was once mapped and
    # then removed.
    "webui": TrustBand.OWNER,
}
"""Per-source default. A per-account entry in PERMISSION_OWNER/TRUSTED maps wins."""

PERMISSION_OWNER_ACCOUNTS: tuple[str, ...] = ()
"""Exact account ids treated as OWNER regardless of source.

Empty by default on purpose. Naming an owner is a deployment decision, and a shipped
default that guesses would hand OWNER privileges to whoever happens to match.
"""

PERMISSION_TRUSTED_ACCOUNTS: tuple[str, ...] = ()
"""Exact account ids mapped to TRUSTED."""

PERMISSION_NORMAL_ACCOUNTS: tuple[str, ...] = ()
"""Exact account ids mapped to NORMAL."""

PERMISSION_BLOCKED_ACCOUNTS: tuple[str, ...] = ()
"""Exact account ids mapped to BLOCKED. The one band that never gets an exception."""

PERMISSION_GROUP_BANDS: dict[str, TrustBand] = {}
"""Per-group band. A group with no entry falls back to DEFAULT_BAND, not to OWNER."""

# ------------------------------------------------------------------ the table
#
# Rows are bands, columns are risks. Every cell is explicit: a reader reviewing this
# feature should be able to see the whole policy on one screen, and a missing cell is
# a startup error rather than a runtime surprise.

PERMISSION_MATRIX: dict[TrustBand, dict[ToolRisk, PermissionAction]] = {
    TrustBand.OWNER: {
        ToolRisk.READ: PermissionAction.ALLOW,
        ToolRisk.SELF: PermissionAction.ALLOW,
        ToolRisk.WRITE: PermissionAction.ALLOW,
        ToolRisk.EXTERNAL: PermissionAction.ALLOW,
    },
    TrustBand.TRUSTED: {
        ToolRisk.READ: PermissionAction.ALLOW,
        ToolRisk.SELF: PermissionAction.ALLOW,
        ToolRisk.WRITE: PermissionAction.ALLOW,
        # external for anyone below OWNER is confirm-or-deny, never a silent allow.
        ToolRisk.EXTERNAL: PermissionAction.CONFIRM,
    },
    TrustBand.NORMAL: {
        ToolRisk.READ: PermissionAction.ALLOW,
        ToolRisk.SELF: PermissionAction.ALLOW,
        ToolRisk.WRITE: PermissionAction.CONFIRM,
        ToolRisk.EXTERNAL: PermissionAction.CONFIRM,
    },
    TrustBand.STRANGER: {
        # The only debatable cell in the table, so it is commented rather than quiet:
        # a read has no outside effect, and denying reads makes the character unable to
        # answer any ordinary question from someone she has never met. Flip this to DENY
        # if a read tool ever returns data the stranger should not see (recall_memory
        # does return the owner's own memories, and that is the argument for DENY).
        ToolRisk.READ: PermissionAction.ALLOW,
        ToolRisk.SELF: PermissionAction.DENY,
        ToolRisk.WRITE: PermissionAction.DENY,
        ToolRisk.EXTERNAL: PermissionAction.DENY,
    },
    TrustBand.BLOCKED: {
        # All deny, with no code path that can special-case it: the brief's
        # "BLOCKED always denies" is data here.
        ToolRisk.READ: PermissionAction.DENY,
        ToolRisk.SELF: PermissionAction.DENY,
        ToolRisk.WRITE: PermissionAction.DENY,
        ToolRisk.EXTERNAL: PermissionAction.DENY,
    },
}

# ------------------------------------------------------------------ confirm

PERMISSION_CONFIRM_TIMEOUT_SECONDS: float = 120.0
"""How long a CONFIRM may stay parked before it becomes a DENY.

Timeout is deny, never allow: an unanswered request must not turn into an action.
Two bounds on purpose. Too short and a owner away from the keyboard loses the
feature; too long and every parked call holds a task, a tool slot and the model's
attention for minutes. There is no fake clock that can drive a real loop timer
(tests/fake_clock.py notes the limitation), so tests shorten this constant rather
than patching ``time``.
"""

PERMISSION_APPROVER_ENABLED: bool = False
"""Whether a CONFIRM may actually ask someone.

False means CONFIRM degrades to DENY, and says so in the log. Today no host code knows
who the owner is or how to reach them: no owner roster exists, and the nearest
park-and-resume implementation (nekoclaw) is fenced off by the layering gate
(scripts/check_module_layering.py FORBIDDEN_EDGES). Fail-closed is the honest default;
flip this once an approver channel is wired.
"""

# ------------------------------------------------------------------ helpers


def band_order(band: TrustBand) -> int:
    """Index into the band order; lower is more trusted."""
    return _BAND_ORDER.index(band)


def lower_band(left: TrustBand, right: TrustBand) -> TrustBand:
    """The less trusted of two bands, used by the group rule.

    A group band and a speaker band are independent facts about one message. Taking the
    higher one would let a blocked person regain rights by standing in a friendly
    group, and taking the lower one is the brief's rule.
    """
    return left if band_order(left) >= band_order(right) else right


def band_for_account(account_id: object) -> TrustBand:
    """The speaker band for one canonical account id.

    Exact-account maps win over the per-source default, and anything unmapped is
    DEFAULT_BAND. No fuzzy matching, no nickname lookup, no co-occurrence: those are
    explicitly forbidden in the identity layer (memory/identity.py kill list), and a
    permission band guessed from a display name is worse than STRANGER.
    """
    text = str(account_id or "").strip()
    if not text:
        return DEFAULT_BAND
    if text in PERMISSION_OWNER_ACCOUNTS:
        return OWNER_BAND
    if text in PERMISSION_BLOCKED_ACCOUNTS:
        return TrustBand.BLOCKED
    if text in PERMISSION_TRUSTED_ACCOUNTS:
        return TrustBand.TRUSTED
    if text in PERMISSION_NORMAL_ACCOUNTS:
        return TrustBand.NORMAL
    source = text.split(":", 1)[0] if ":" in text else ""
    return PERMISSION_SOURCE_BANDS.get(source, DEFAULT_BAND)


def band_for_group(group_id: object) -> TrustBand:
    """The band of one conversation, ``platform:conversation``; unmapped is DEFAULT_BAND."""
    text = str(group_id or "").strip()
    if not text:
        return DEFAULT_BAND
    return PERMISSION_GROUP_BANDS.get(text, DEFAULT_BAND)


def decide(band: TrustBand, risk: ToolRisk) -> PermissionAction:
    """The one lookup the gate performs. No ifs, no ordering tricks.

    A missing cell raises rather than defaulting: a hole in the table is a bug in this
    file, and it must not be repaired at runtime by guessing.
    """
    return PERMISSION_MATRIX[band][risk]


def _validate_matrix() -> None:
    """Reject an incomplete or misspelled table at import, not at first call."""
    for band in _BAND_ORDER:
        row = PERMISSION_MATRIX.get(band)
        if row is None:
            raise ValueError(f"permission matrix has no row for band {band.value!r}")
        for risk in ToolRisk:
            action = row.get(risk)
            if action is None:
                raise ValueError(
                    f"permission matrix has no cell for band {band.value!r} "
                    f"risk {risk.value!r}"
                )
            if action not in set(PermissionAction):
                raise ValueError(
                    f"permission matrix cell {band.value}/{risk.value} is {action!r}, "
                    f"not an PermissionAction"
                )
    if set(PERMISSION_MATRIX) != set(_BAND_ORDER):
        extra = set(PERMISSION_MATRIX) - set(_BAND_ORDER)
        raise ValueError(f"permission matrix has rows for unknown bands: {extra}")
    for key, value in PERMISSION_SOURCE_BANDS.items():
        if not str(key).strip() or value not in set(TrustBand):
            raise ValueError(f"bad source band mapping {key!r} -> {value!r}")
    for name, accounts in (
        ("owner", PERMISSION_OWNER_ACCOUNTS),
        ("trusted", PERMISSION_TRUSTED_ACCOUNTS),
        ("normal", PERMISSION_NORMAL_ACCOUNTS),
        ("blocked", PERMISSION_BLOCKED_ACCOUNTS),
    ):
        for account in accounts:
            if not str(account).strip() or ":" not in str(account):
                raise ValueError(
                    f"{name} account {account!r} is not in canonical platform:actor form"
                )
    if PERMISSION_CONFIRM_TIMEOUT_SECONDS <= 0:
        raise ValueError("PERMISSION_CONFIRM_TIMEOUT_SECONDS must be positive")


_validate_matrix()
