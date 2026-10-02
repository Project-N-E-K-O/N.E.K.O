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

"""Catgirl visit pure-logic layer (docs/design/visit-infrastructure.md PR-06)."""

from .forget import ForgetPlan, RevocationLog, plan_forget_person, run_revocation
from .identity import (
    JtiWindow,
    PubkeySet,
    TicketClaims,
    TicketRejected,
    verify_identity_ticket,
)
from .limits import Blocklist, PeerRateLimiter
from .liveness import VisitLiveness
from .outbox import InboxSequencer, VisitOutbox
from .room import (
    IncomingLineDone,
    IncomingLineStart,
    LineRef,
    ReplyPlan,
    RoomEffects,
    VisitRoom,
    WrapUpDecision,
    WrapUpState,
)
from .sanitize import (
    assert_no_peer_ngram,
    clamp_peer_line,
    defang_markdown_media,
    neutralize_display_name,
    redact_outbound,
    sanitize_relay_text,
)
from .spool import VisitSpool, is_digestable
from .subjects import (
    PeerRoster,
    derive_pair_id,
    derive_peer_char_id,
    derive_person_id,
    derive_vid,
    resolve_visit_recall_subjects,
)

__all__ = [
    "Blocklist",
    "ForgetPlan",
    "IncomingLineDone",
    "IncomingLineStart",
    "InboxSequencer",
    "JtiWindow",
    "LineRef",
    "PeerRateLimiter",
    "PeerRoster",
    "PubkeySet",
    "ReplyPlan",
    "RevocationLog",
    "RoomEffects",
    "TicketClaims",
    "TicketRejected",
    "VisitLiveness",
    "VisitOutbox",
    "VisitRoom",
    "VisitSpool",
    "WrapUpDecision",
    "WrapUpState",
    "assert_no_peer_ngram",
    "clamp_peer_line",
    "defang_markdown_media",
    "derive_pair_id",
    "derive_peer_char_id",
    "derive_person_id",
    "derive_vid",
    "is_digestable",
    "neutralize_display_name",
    "plan_forget_person",
    "redact_outbound",
    "resolve_visit_recall_subjects",
    "run_revocation",
    "sanitize_relay_text",
    "verify_identity_ticket",
]
