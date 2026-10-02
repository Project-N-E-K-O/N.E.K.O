"""Fallback port picking must skip every N.E.K.O default port, retired ones included.

48917/48918 (the old AGENT_MQ / MAIN_AGENT_EVENT ports) no longer have a
listener in this build, but an older build still running on the same machine
may hold them, so a fallback must never land there. The range was once shrunk
by accident together with the port table and no test noticed.
"""

from __future__ import annotations


def test_fallback_skips_known_and_retired_default_ports(monkeypatch):
    from launcher_core import runtime as launcher

    monkeypatch.setattr(launcher, "_is_port_bindable", lambda port: True)

    assert launcher._pick_fallback_port(48910, set()) == 48919
    assert launcher._pick_fallback_port(48916, set()) == 48919
