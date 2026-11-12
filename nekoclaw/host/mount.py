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

"""One host per process, created on first use.

The agent process calls ``configure`` while it starts. That only remembers how
the host will read the process's switches; it builds nothing. ``get_host``
builds the host the first time something needs it (a dispatch, a query, the
commission board), and ``shutdown_host`` stops it if it was ever built.

This module imports nothing from the rest of the package until ``get_host``
runs, so importing it at startup costs nothing: no SQLite, no HTTP client, no
runtime.
"""

from __future__ import annotations

import threading
from typing import Any

_lock = threading.Lock()
_options: dict[str, Any] = {}
_host: Any = None
# The host being shut down, until that has finished.
_closing: Any = None
# Set when plugins changed before the host existed; nothing to do then, the
# first read fetches the list anyway.
_configured = False


def configure(**options: Any) -> None:
    """Remember the options ``NekoClawHost`` is built with later.

    Accepts the keyword arguments of ``nekoclaw.host.service.NekoClawHost``,
    typically ``enabled``, ``plugins_enabled``, ``task_agents_enabled``,
    ``change_token`` and ``send_report``. Calling it again replaces them; a
    host that already exists is not rebuilt.
    """
    global _configured
    with _lock:
        _options.clear()
        _options.update(options)
        _configured = True


def is_configured() -> bool:
    return _configured


def peek_host() -> Any:
    """Return the host if it has been built, else None. Builds nothing."""
    return _host


def get_host() -> Any:
    """Return the process's host, building it on the first call.

    While a host is being shut down, that host is returned: it takes no new
    work, and a second host must not open the same files meanwhile. A new one
    is built once the shutdown has finished.
    """
    global _host, _closing
    if _host is not None:
        return _host
    with _lock:
        if _host is None:
            if _closing is not None:
                if not _closing.closed:
                    return _closing
                _closing = None
            from nekoclaw.host.service import NekoClawHost

            _host = NekoClawHost(**_options)
        return _host


def notify_plugins_changed(*_args: Any, **_kwargs: Any) -> None:
    """Tell the host that a plugin started, stopped, reloaded, was installed or removed.

    Safe to call from any thread and before the host exists. Accepts and
    ignores arguments, so it can be subscribed to an event source directly.
    """
    host = _host
    if host is not None:
        host.invalidate_capabilities()


async def shutdown_host() -> None:
    """Shut the host down if it was built.

    Once that has finished, the next ``get_host`` builds a new one. Calling
    this again while a shutdown is under way waits for that shutdown.
    """
    global _host, _closing
    with _lock:
        host, _host = _host, None
        if host is not None:
            _closing = host
        else:
            host = _closing
    if host is None:
        return
    await host.shutdown()
    with _lock:
        if _closing is host:
            _closing = None


def reset_for_tests() -> None:
    """Forget the host and its options without shutting anything down."""
    global _host, _closing, _configured
    with _lock:
        _host = None
        _closing = None
        _options.clear()
        _configured = False
