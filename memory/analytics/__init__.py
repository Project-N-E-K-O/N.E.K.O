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

"""Shared read-only memory analytics and user-facing imprint builders."""

from .deep_conflicts import arun_deep_conflict_check
from .export import abuild_memory_export_zip, build_memory_export_zip, redact_obj, redact_text
from .imprint import (
    abuild_memory_overview,
    build_memory_overview,
    load_imprint_note_cache,
)
from .imprint_note import agenerate_imprint_note
from .lineage import (
    abuild_full_lineage_graph,
    abuild_memory_lineage_story,
    build_full_lineage_graph,
    build_memory_lineage_story,
)
from .snapshots import (
    acreate_snapshot,
    alist_snapshots,
    arestore_snapshot,
    create_snapshot_sync,
    list_snapshots,
)
from .star_map import (
    abuild_star_map,
    afind_related_memories,
    alabel_star_map_clusters,
    build_star_map,
    find_related_memories,
)

__all__ = [
    "abuild_full_lineage_graph",
    "abuild_memory_export_zip",
    "abuild_memory_lineage_story",
    "abuild_memory_overview",
    "abuild_star_map",
    "acreate_snapshot",
    "afind_related_memories",
    "agenerate_imprint_note",
    "alabel_star_map_clusters",
    "alist_snapshots",
    "arestore_snapshot",
    "arun_deep_conflict_check",
    "build_full_lineage_graph",
    "build_memory_export_zip",
    "build_memory_lineage_story",
    "build_memory_overview",
    "build_star_map",
    "create_snapshot_sync",
    "find_related_memories",
    "list_snapshots",
    "load_imprint_note_cache",
    "redact_obj",
    "redact_text",
]
