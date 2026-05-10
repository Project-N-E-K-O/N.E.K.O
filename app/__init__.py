# -*- coding: utf-8 -*-
"""Top-level server entry-point package.

Houses the four FastAPI server modules — main_server, memory_server,
agent_server, monitor. launcher.py at the repo root remains the single
Nuitka/PyInstaller entry; everything inside this package is imported by
launcher (in-process or via spawned subprocess targets) rather than
executed as standalone scripts.
"""
