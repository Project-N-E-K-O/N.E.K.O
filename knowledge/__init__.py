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

"""Public knowledge: user-imported reference packs, kept apart from memory.

The package runs inside the Memory Server process and is reached by Main only
over HTTP (``/internal/knowledge/*``). It owns ``<root>/knowledge/`` and never
reads or writes user or character memory. Embedding inference is borrowed
through an injected embedder so this package does not import ``memory``.

Layout under the knowledge root:

* ``registry.json``  - user data: installed packs, per-pack policy, disabled
  entries and the global switch.
* ``packs/``         - user data: the raw schema-v1 pack files.
* ``knowledge.db``   - derived index (entries, FTS, chunks, vectors). It is
  rebuilt from ``registry.json`` + ``packs/`` whenever it is missing, damaged
  or written by an unknown schema.
"""
