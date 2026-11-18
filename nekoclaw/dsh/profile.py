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

"""What a dsh process is started with: its configuration, environment and directory.

dsh is configured by patches laid over the plugin list of its ``acp``
profile. The one written here (``PATCH_FILE``, next to it the guard) makes a
dsh process fit to run task agents for this host:

* **Nothing is sent anywhere but to the model.** The upload of the session
  log and of the plugin list with every model request, and the feedback
  telemetry, are switched off.
* **The model is reached through the host.** dsh knows one provider, the
  driver's own endpoint on the loopback address (``forward.py``), and a
  fixed set of model names, one per session it can hold. The real endpoint
  and its key never reach dsh.
* **Permissions are the host's.** dsh's own scheme (a sandbox that refuses,
  and a question only when the model asks for more) cannot express the
  host's settings, and its Windows sandbox changes the access rights of the
  working directory. The sandbox is off, and a plugin of a few lines, the
  guard, makes dsh ask before every call of a tool, except those listed
  here as needing no permission. The question reaches the driver as a
  permission request and is decided by the host: for dsh's file and
  command tools by the same code as in the built-in runtime, for the
  gateway's tools by the gateway (before the call, because dsh ends a call
  of such a tool after a minute and a person may take longer to answer),
  and for a tool the driver does not know (one a dsh plugin brought) under
  the host's category for everything else.
* **What cannot work here is not offered**: dsh's subagents (their events and
  questions do not reach a client), plan mode and workflows (they need dsh's
  own interface), changing the working directory (permissions are judged
  against a fixed one), its web search (it needs a key of its vendor) and the
  tools for resources of connected services (the gateway has none).

The guard rests on an internal event of dsh, so one version of dsh is used
(``DSH_VERSION``) and a process whose guard did not report itself is not
used at all (``GUARD_READY``).
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROFILE = "acp"
PROVIDER = "nekoclaw"
PATCH_FILE = "nekoclaw.patch.yml"
GUARD_FILE = "nekoclaw-guard.mjs"
# What the guard writes to standard error once dsh has loaded it.
GUARD_READY = "nekoclaw-guard ready"
# The variable dsh reads the "key" of its one provider from: the token of the
# driver's endpoint, of no use anywhere else.
KEY_VARIABLE = "NEKOCLAW_DSH_ENDPOINT_TOKEN"
# The service the gateway is handed to dsh as; the host's own tools are
# ``prompts_nekoclaw_dsh.HOST_SERVICE``.
GATEWAY_SERVICE = "gateway"

# dsh's tools the built-in runtime's gate has a category for
# (``nekoclaw.runtime.permissions.classify``): files and commands.
GATED_TOOLS: tuple[str, ...] = ("read", "read_image", "glob", "grep", "write", "edit", "pwsh", "bash")

# dsh's own tools that touch neither files, the shell nor the network and
# are not asked about, as the built-in runtime does not ask about its
# counterparts. (dsh's ``web_fetch`` is not among them: it is switched off,
# see ``build_patch``.)
UNASKED_TOOLS: tuple[str, ...] = (
    "create_goal",
    "get_goal",
    "update_goal",
    "job_kill",
    "job_list",
    "job_output",
    "skill",
    "todo_write",
)

# The host's own tools that may have to wait for the user: a web search
# when the user set plugin entries to be asked about. dsh gives a call less
# than a minute, so the host is asked before the call, as for the gateway's
# tools, though the rest of the host's tools pass unasked.
ASKED_HOST_TOOLS: tuple[str, ...] = ("web_search",)

# What the guard goes by is a tool's name, nothing else: one of the names
# above, or one that starts with the prefix dsh gives the tools of the host's
# own service, passes unasked; one that starts with the gateway's prefix is
# ruled on as a call of the gateway, which lets it through when it knows no
# such tool. A plugin the user adds to dsh can register a tool under any
# name, these included. That widens nothing, since such a plugin runs inside
# the dsh process with everything that process may do and no sandbox; but
# whoever offers a way to add plugins has to know that asking before a tool
# call is no boundary against one.

# Rows of dsh's plugin list that are switched off; see the module docstring.
# A row a version of dsh does not have is skipped by dsh.
DISABLED_ROWS: tuple[str, ...] = (
    "session-telemetry-otel",
    "plan-mode",
    "tool-subagent",
    "tool-subagent-fork",
    "tool-subagent-control",
    "tool-subagent-list-agents",
    "tool-workflow",
    "workflow-ptc",
    "agent-team",
    "tool-agent-team",
    "mcp-resources",
    "tool-working-directory",
)

GUARD_SOURCE = """\
// Written by N.E.K.O (nekoclaw/dsh/profile.py). Do not edit: it is rewritten at every start.
//
// Makes dsh ask before it carries out a tool call. The question reaches the
// N.E.K.O host as a permission request, and the host decides by its own
// settings. Only the tools named in the configuration pass unasked; without
// a configuration every tool is asked about.
export const name = 'nekoclaw-guard'

export function apply(ctx, config) {
  const unasked = new Set(Array.isArray(config?.unasked) ? config.unasked : [])
  const prefixes = Array.isArray(config?.unaskedPrefixes) ? config.unaskedPrefixes : []
  const asked = new Set(Array.isArray(config?.asked) ? config.asked : [])
  ctx.on('tools/pre-execute', async (exec, next) => {
    if (!asked.has(exec.name) && (unasked.has(exec.name) || prefixes.some(prefix => exec.name.startsWith(prefix)))) {
      return next()
    }
    return { kind: 'ask', reason: 'the N.E.K.O host decides' }
  })
  process.stderr.write('%s\\n')
}
""" % GUARD_READY


class DshUnavailable(RuntimeError):
    """dsh cannot be used on this computer, or for this session; the text says why."""


# In a session directory: which dsh session belongs to it. The host looks
# whether it is there (``nekoclaw.host.paths.DSH_SESSION_NAME``); a test keeps
# the two names the same.
SESSION_FILE = "dsh-session.json"


@dataclass(frozen=True, slots=True)
class DshLaunch:
    """What is needed to start a dsh process: its executable and its home directory."""

    executable: Path
    home: Path
    # Arguments in front of dsh's own; see ``launch_arguments``.
    leading: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Shape:
    """What of a model's configuration a dsh process is started with and cannot change later.

    Everything else of a session's model (its endpoint, its key, its name)
    is applied per request by the driver's endpoint.
    """

    context_window: int
    max_output_tokens: int
    supports_images: bool
    # The part of the window a conversation may fill before dsh sums it up.
    compact_ratio: float = 0.85


def slot_model(index: int) -> str:
    """Return the model name dsh uses for the session in slot ``index``."""
    return f"slot-{index}"


def model_option(index: int) -> str:
    """Return the value of dsh's model option that selects slot ``index``."""
    # Exactly as dsh writes it: a JSON array without spaces.
    return json.dumps([PROVIDER, slot_model(index)], separators=(",", ":"))


def build_patch(*, shape: Shape, endpoint: str, slots: int, home: Path) -> list[dict[str, Any]]:
    """Return the patch a dsh process is started with.

    ``endpoint`` is the driver's model endpoint as dsh is to call it (an
    address on the loopback interface ending in ``/v1``). A patch replaces
    the whole configuration of the row it names.
    """
    from config.nekoclaw_settings import DSH_MODEL_RETRIES, DSH_SPILL_KEPT_DAYS
    from config.prompts.prompts_nekoclaw import TASK_AGENT_IDENTITY
    from config.prompts.prompts_nekoclaw_dsh import DSH_HOST_TOOLS_SECTION, HOST_TOOL_PREFIX

    # The built-in runtime's margin (design section 8.9): dsh's fixed 65536
    # leaves nothing, or less than nothing, of a small window.
    from nekoclaw.runtime.context import headroom_of

    headroom = headroom_of(shape.context_window)
    models = [
        {
            "id": slot_model(index),
            "name": "task agent model",
            "contextWindow": shape.context_window,
            "maxTokens": shape.max_output_tokens,
            "input": ["text", "image"] if shape.supports_images else ["text"],
        }
        for index in range(slots)
    ]
    rows: list[dict[str, Any]] = [
        {
            "id": "llm-pi-ai",
            "config": {
                "providers": {
                    PROVIDER: {
                        "api": "openai-completions",
                        "baseURL": endpoint,
                        "apiKeyEnv": KEY_VARIABLE,
                        # The endpoint speaks plain chat completions; what a
                        # provider wants beyond that is added there.
                        "compat": {
                            "supportsStore": False,
                            "supportsDeveloperRole": False,
                            "supportsUsageInStreaming": True,
                        },
                        "retryPolicy": {"mode": "normal", "maxRetries": DSH_MODEL_RETRIES},
                        "models": models,
                    }
                }
            },
        },
        {"id": "acp", "config": {"provider": PROVIDER, "model": slot_model(0)}},
        {"id": "agent-default-model", "config": {"provider": PROVIDER, "model": slot_model(0)}},
        {"id": "session-log-deepseek", "config": {"enabled": False}},
        {"id": "plugin-package-inventory-deepseek", "config": {"enabled": False}},
        {
            "id": "system-prompt",
            "config": {
                "includeHarnessIdentity": False,
                "personaPrefix": TASK_AGENT_IDENTITY,
                # One for every session of the process: what only some are
                # offered (the subagent tools) is told in a session's first
                # message instead; see ``driver._first_message``.
                "personaSuffix": DSH_HOST_TOOLS_SECTION,
            },
        },
        # Neither of dsh's web tools: its search needs its vendor's key, and
        # its fetch reaches every address. The host's ``web_fetch`` and
        # ``web_search`` are offered through the host's service instead; that
        # ``web_fetch`` reaches public addresses only, as in the built-in runtime.
        {"id": "tool-web", "config": {"search": False, "fetch": False}},
        {"id": "sandbox-policy", "config": {"mode": "danger-full-access"}},
        {"id": "approval", "config": {"policy": "ask"}},
        {
            "id": "permission",
            "config": {
                "presets": {PROVIDER: {"sandbox": "danger-full-access", "approval": "ask"}},
                "defaultPreset": PROVIDER,
            },
        },
        {
            "id": "compaction-basic",
            "config": {
                # As in the built-in runtime: summed up from this part of the
                # window on, and no later than where the output and the
                # margin begin.
                "thresholdRatio": shape.compact_ratio,
                "headroomTokens": headroom,
                "maxTokens": min(shape.max_output_tokens, max(1, headroom)),
            },
        },
        {"id": "spill-local", "config": {"root": str(spill_dir(home)), "cleanupPeriodDays": DSH_SPILL_KEPT_DAYS}},
        *({"id": row, "disabled": True} for row in DISABLED_ROWS),
        {
            "insert": [
                {
                    "id": "nekoclaw-guard",
                    "name": f"./{GUARD_FILE}",
                    # The host's own tools ask the host themselves when they
                    # need to; everything else not listed is asked about.
                    "config": {
                        "unasked": list(UNASKED_TOOLS),
                        "unaskedPrefixes": [HOST_TOOL_PREFIX],
                        "asked": [f"{HOST_TOOL_PREFIX}{name}" for name in ASKED_HOST_TOOLS],
                    },
                }
            ]
        },
    ]
    return rows


def spill_dir(home: Path) -> Path:
    """Return where dsh keeps the full text of results too long for the context."""
    return home / "spill"


def session_spill_dir(home: Path, dsh_session: str) -> Path:
    """Return where dsh keeps such results of one of its sessions.

    As dsh names it (``spill-local``): below the root, a directory named
    after the first twelve hex digits of the SHA-256 of the session's id.
    """
    digest = hashlib.sha256(dsh_session.encode("utf-8")).hexdigest()[:12]
    return spill_dir(home) / f"session-{digest}"


def config_dir(home: Path) -> Path:
    """Return the directory the patch and the guard are written to."""
    return home / "nekoclaw"


def write_profile(home: Path, patch: list[dict[str, Any]], *, tag: str = "") -> Path:
    """Write the patch and the guard below ``home`` and return the patch's path. Blocking.

    The patch is JSON, which dsh reads as the YAML it expects. Both files
    are written anew at every start, so what a user or a plugin changed in
    them does not last. ``tag`` tells the patches of processes apart that
    run at the same time with different shapes.
    """
    directory = config_dir(home)
    directory.mkdir(parents=True, exist_ok=True)
    (home / "agents").mkdir(parents=True, exist_ok=True)
    (directory / GUARD_FILE).write_text(GUARD_SOURCE, encoding="utf-8")
    name = PATCH_FILE if not tag else PATCH_FILE.replace(".patch.", f"-{tag}.patch.")
    path = directory / name
    path.write_text(json.dumps(patch, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def launch_arguments(executable: Path, patch: Path, leading: tuple[str, ...] = ()) -> list[str]:
    """Return the command line of a dsh process serving ACP.

    ``leading`` goes in front of dsh's own arguments: nothing for the
    program as it is released, the script for one run by an interpreter.
    """
    return [str(executable), *leading, "--profile", PROFILE, "--patch", str(patch)]


def child_environment(base: Mapping[str, str], *, home: Path, token: str) -> dict[str, str]:
    """Return the environment of a dsh process.

    The host's environment, as the built-in runtime's commands get it,
    without what would configure dsh or its vendor's services behind the
    host's back, and with dsh's directories set to ours.
    """
    environment = {
        name: value for name, value in base.items() if not name.upper().startswith(("DSH_", "DEEPSEEK_"))
    }
    environment["DSH_HOME"] = str(home)
    # The user's own ``~/.agents`` (rules and skills for other tools) is not
    # read: a task agent gets its rules from the host.
    environment["DSH_AGENTS_HOME"] = str(home / "agents")
    environment["DSH_TELEMETRY_DISABLED"] = "1"
    # What dsh downloads for its plugins, and what its executable unpacks of
    # itself, stay below the directory an uninstall removes.
    environment["DSH_RESOURCE_CACHE"] = str(home.parent / "resources")
    environment["PKG_NATIVE_CACHE_PATH"] = str(home.parent / "cache")
    environment[KEY_VARIABLE] = token
    return environment


def start_directory(home: Path) -> Path:
    """Return the directory a dsh process is started in, creating it. Blocking.

    dsh reads a ``.env`` from the directory it is started in, so it is one of
    ours. On Windows it has to be on drive C: the executable looks for the
    files packed into it below ``C:\\snapshot`` whatever drive it runs on,
    and a session cannot be created when it was started elsewhere. The
    working directory of a session is another matter and can be anywhere.
    """
    if sys.platform != "win32":
        directory = home / "start"
        directory.mkdir(parents=True, exist_ok=True)
        return directory
    candidates = [home / "start", Path(tempfile.gettempdir()) / "nekoclaw-dsh"]
    system_root = os.environ.get("SystemRoot")
    if system_root:
        candidates.append(Path(system_root) / "Temp" / "nekoclaw-dsh")
    for candidate in candidates:
        if Path(os.path.abspath(candidate)).drive.upper() != "C:":
            continue
        try:
            candidate.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        return candidate
    raise DshUnavailable("dsh has to be started from a directory on drive C:, and none could be made")
