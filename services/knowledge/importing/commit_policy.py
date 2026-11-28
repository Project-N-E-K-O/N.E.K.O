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


"""Pure policy for the registry record of a verified knowledge import.

The service supplies the frozen job, checked pack, previous record and time.
This module owns no store, locks, budgets or files; callers must first validate
source consent, capacity and subscription identity under the existing lock.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..models import KnowledgePack
from ..registry import PackRecord

if TYPE_CHECKING:
    from ..jobs import ImportJob


def build_import_record(
    pack: KnowledgePack, previous: PackRecord | None, job: ImportJob,
    *, sha: str, chunks: int, now: str,
) -> PackRecord:
    """Apply disclosure defaults and retain choices still belonging to this pack."""
    keys = {entry.key for entry in pack.entries}
    # Ordinary local file replacement introduces new disclosure scope:
    # both retrieval and automatic context start off again. Confirmed
    # source-version updates retain their switches.
    new_disclosure = job.kind == "pack_import" and job.initial_enabled is False
    # A different local body replaces the built-in provenance. Same-body
    # repairs and source updates keep their existing record's origin.
    imported_replacement = (
        previous is not None and previous.origin == "builtin"
        and job.kind == "pack_import" and job.origin == "imported"
        and sha != previous.pack_sha256
    )
    return PackRecord(
        pack_id=pack.pack_id,
        pack_sha256=sha,
        source=pack.source,
        declared_material_type=pack.material_type,
        entries=len(pack.entries),
        chunks=chunks,
        material_type_override=previous.material_type_override if previous else None,
        auto_context=False if new_disclosure else previous.auto_context if previous else False,
        local_embedding=previous.local_embedding if previous else True,
        disabled_titles=tuple(
            sorted(set(previous.disabled_titles) & keys) if previous else ()
        ),
        installed_at=previous.installed_at if previous else now,
        updated_at=now,
        subscription=job.subscription,
        enabled=(False if new_disclosure
                 else previous.enabled if previous else (job.initial_enabled if job.initial_enabled is not None else True)),
        origin=("imported" if imported_replacement else previous.origin if previous
                else (job.origin or ("market" if job.subscription else "builtin"))),
        archive_sha256=job.archive_sha256,
    )
