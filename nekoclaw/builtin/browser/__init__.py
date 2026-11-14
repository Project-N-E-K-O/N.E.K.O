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

"""The browser tools: a browser of this computer driven over its debugging protocol.

There is no browser automation library underneath. The browser is one the
user has installed (Chrome, Edge, Brave, Chromium ...), started with a profile
directory of its own or, when the user chose that, the browser they are
already using; it is spoken to in the Chrome DevTools Protocol over a
WebSocket.

Modules:

* ``cdp``       - the protocol connection: commands, answers, events.
* ``launcher``  - finding a browser, starting it, connecting to one that
  runs, closing it and killing its process tree.
* ``settings``  - which browser and which of the two modes the user chose.
* ``snapshot``  - a page as text: its accessibility tree, each element with
  a uid.
* ``keys``      - key names and what the protocol wants for them.
* ``session``   - the pages of the browser and the actions on a page.
* ``tools``     - the tools a task agent calls, one call at a time.

The split into tools, the snapshot format and the way uids are given follow
Chrome DevTools MCP (Apache-2.0); what an "interesting" element of the
accessibility tree is follows Puppeteer (Apache-2.0), on which that server is
built. Both are named in the files that carry their logic.
"""
