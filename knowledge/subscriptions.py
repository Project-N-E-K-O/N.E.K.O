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

"""Where a pack installed from the plugin market came from.

A subscription is metadata the plugin server attaches when it hands a
downloaded market artifact to ``/internal/knowledge/subscriptions/apply``. The
pack itself is an ordinary schema-v1 pack (raw text only; v1 ships no
prebuilt vectors). The subscription records which market package it is, so an
update can replace it and an unsubscribe can prove it removes the right pack.

Identity rules (enforced by the service, see ``identity_conflict``):

* A pack is either local or subscribed, for good: a local import never
  overwrites a subscribed pack and a subscription never overwrites a local one.
* A subscribed pack keeps its provider, package id, remote id and material
  type; only the version, channel and artifact may change.
* One market package installs as at most one pack.

An unsubscribe names the releases (version + artifact digest) it removes, so
repeating it, or one arriving late, never removes a later subscription of the
same package (see ``KnowledgeService.remove_subscription``).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from .models import MATERIAL_TYPES, pack_id_is_valid


SUBSCRIPTION_PROTOCOL_VERSION = 1
MARKET_PROVIDER = "plugin-market"
CHANNELS = ("stable", "beta")
_FIELDS = frozenset(
    {
        "provider",
        "provider_package_id",
        "remote_id",
        "pack_id",
        "version",
        "channel",
        "artifact_sha256",
        "material_type",
    }
)
_PACKAGE_ID_RE = re.compile(r"^[1-9][0-9]{0,18}$")
_REMOTE_ID_RE = re.compile(r"^knowledge/[a-z0-9][a-z0-9._-]{1,99}$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,99}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
# More than an unsubscribe can ever name: the installed release plus every
# import of the package that can be under way at once.
MAX_RELEASES = 16

# (version, artifact_sha256) of a subscription.
Release = tuple[str, str]


class SubscriptionError(ValueError):
    """Subscription metadata was rejected; ``reason`` is a stable code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class KnowledgeSubscription:
    provider: str
    provider_package_id: str
    remote_id: str
    pack_id: str
    version: str
    channel: str
    # SHA-256 of the artifact bytes as the market published them; the file
    # under packs/ is the normalized form and has its own digest.
    artifact_sha256: str
    material_type: str

    @property
    def identity(self) -> tuple[str, str, str]:
        """What a later update must keep: provider, package id and remote id."""
        return (self.provider, self.provider_package_id, self.remote_id)

    @property
    def release(self) -> Release:
        return (self.version, self.artifact_sha256)

    def to_json(self) -> dict[str, str]:
        return asdict(self)


def _field(payload: Mapping[str, Any], name: str, pattern: re.Pattern[str]) -> str:
    value = payload.get(name)
    # fullmatch: ``$`` alone would accept a trailing newline.
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise SubscriptionError("invalid_subscription")
    return value


def parse_subscription(payload: object, *, strict: bool = True) -> KnowledgeSubscription:
    """Validate subscription metadata; every known field is required.

    ``strict`` (an apply request): no other field either. Reading the
    registry is lenient about extra fields, so a registry written by a later
    version that records more about a subscription still opens here.
    """
    if not isinstance(payload, Mapping) or not _FIELDS <= set(payload):
        raise SubscriptionError("invalid_subscription")
    if strict and set(payload) != _FIELDS:
        raise SubscriptionError("invalid_subscription")
    if payload.get("provider") != MARKET_PROVIDER:
        raise SubscriptionError("untrusted_provider")
    pack_id = payload.get("pack_id")
    if not pack_id_is_valid(pack_id):
        raise SubscriptionError("invalid_subscription")
    if payload.get("channel") not in CHANNELS or payload.get("material_type") not in MATERIAL_TYPES:
        raise SubscriptionError("invalid_subscription")
    return KnowledgeSubscription(
        provider=MARKET_PROVIDER,
        provider_package_id=_field(payload, "provider_package_id", _PACKAGE_ID_RE),
        remote_id=_field(payload, "remote_id", _REMOTE_ID_RE),
        pack_id=str(pack_id),
        version=_field(payload, "version", _VERSION_RE),
        channel=str(payload["channel"]),
        artifact_sha256=_field(payload, "artifact_sha256", _SHA256_RE),
        material_type=str(payload["material_type"]),
    )


def parse_subscription_json(raw: str) -> KnowledgeSubscription:
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise SubscriptionError("invalid_subscription") from exc
    return parse_subscription(payload)


def parse_expected_identity(payload: object) -> tuple[str, str, str]:
    """The identity an unsubscribe expects to remove: provider, package id, remote id."""
    if not isinstance(payload, Mapping) or set(payload) != {"provider", "provider_package_id", "remote_id"}:
        raise SubscriptionError("invalid_subscription")
    if payload.get("provider") != MARKET_PROVIDER:
        raise SubscriptionError("untrusted_provider")
    return (
        MARKET_PROVIDER,
        _field(payload, "provider_package_id", _PACKAGE_ID_RE),
        _field(payload, "remote_id", _REMOTE_ID_RE),
    )


def parse_releases(payload: object) -> frozenset[Release]:
    """The releases an unsubscribe removes: 1..MAX_RELEASES of {version, artifact_sha256}."""
    if not isinstance(payload, list) or not 1 <= len(payload) <= MAX_RELEASES:
        raise SubscriptionError("invalid_subscription")
    releases: set[Release] = set()
    for item in payload:
        if not isinstance(item, Mapping) or set(item) != {"version", "artifact_sha256"}:
            raise SubscriptionError("invalid_subscription")
        releases.add((_field(item, "version", _VERSION_RE), _field(item, "artifact_sha256", _SHA256_RE)))
    return frozenset(releases)


def identity_conflict(
    pack_id: str,
    subscription: KnowledgeSubscription | None,
    installed: Mapping[str, KnowledgeSubscription | None],
) -> bool:
    """Whether installing ``pack_id`` with ``subscription`` breaks the identity rules.

    ``installed`` maps every installed pack id to its subscription (``None``
    for a local pack).
    """
    if pack_id in installed:
        current = installed[pack_id]
        if (current is None) != (subscription is None):
            return True
        if current is not None and subscription is not None and (
            current.identity != subscription.identity
            # Changing what the pack is used for is not an update.
            or current.material_type != subscription.material_type
        ):
            return True
    if subscription is None:
        return False
    return any(
        other is not None
        and other_id != pack_id
        and other.provider == subscription.provider
        and other.provider_package_id == subscription.provider_package_id
        for other_id, other in installed.items()
    )
