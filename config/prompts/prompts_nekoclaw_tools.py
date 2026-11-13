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
#
# ---------------------------------------------------------------------------
# DSH_TOOL_SCHEMAS below is taken from DeepSeek Harness (dsh),
# https://github.com/deepseek-ai/deepseek-harness, commit
# fcc0ad462172522aaf5c93a7b2e01f6b9f0b0159, which is distributed under the
# MIT License:
#
#   MIT License
#
#   Copyright (c) 2026 DeepSeek
#
#   Permission is hereby granted, free of charge, to any person obtaining a copy
#   of this software and associated documentation files (the "Software"), to deal
#   in the Software without restriction, including without limitation the rights
#   to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
#   copies of the Software, and to permit persons to whom the Software is
#   furnished to do so, subject to the following conditions:
#
#   The above copyright notice and this permission notice shall be included in all
#   copies or substantial portions of the Software.
#
#   THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
#   IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
#   FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
#   AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
#   LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
#   OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
#   SOFTWARE.
# ---------------------------------------------------------------------------

"""Tool definitions shown to the model by the built-in agent runtime.

``DSH_TOOL_SCHEMAS`` holds tool names, descriptions and parameter schemas from
dsh, whose wording was tuned against task benchmarks. Using them unchanged lets
the built-in runtime and the optional dsh runtime present the same tools. They
are model-facing English text and are deliberately not translated.

Source: ``snapshots/sdk/team-default-enabled/tool-schemas.expected.json`` at
the commit named above, for every tool except ``pwsh``. That snapshot was
captured on a POSIX host and has no ``pwsh`` entry, so ``pwsh`` comes from
``packages/shell/tool-pwsh/src/index.ts`` at the same commit.

Differences from the source, all of them removals or replacements required
because the tool's behavior here differs (a description must match what the
implementation does):

* ``bash`` / ``pwsh`` / ``write`` / ``edit``: the ``sandbox_permissions`` and
  ``justification`` parameters and the sentence about the file sandbox are
  removed. The built-in runtime has no operating-system sandbox.
* ``bash`` / ``pwsh``: the sentence about managed ``DSH_*`` environment
  variables is removed. The built-in runtime exports none.
* ``grep``: "a ripgrep regular expression" and "(ripgrep syntax)" are replaced.
  The implementation matches with Python's ``re`` line by line; it does not
  bundle ripgrep.

Only tools the built-in runtime implements are listed. ``NEKOCLAW_TOOL_SCHEMAS``
holds this project's own two tools.

A snapshot test (``tests/unit/nekoclaw/test_prompt_snapshot.py``) locks the
assembled result; any edit here has to update that snapshot explicitly.
"""

from __future__ import annotations

from typing import Any

DSH_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    'ask_user_question': {
        'name': 'ask_user_question',
        'description': (
            'Ask the user a concise question when you need confirmation, a choice, or missing '
            'information before proceeding. An answer item with empty selected and no custom '
            'means the user skipped that question: continue without it, do not ask it again, '
            'and do not treat the skip as permission.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'questions': {
                    'type': 'array',
                    'description': 'Questions to ask the user before continuing.',
                    'items': {
                        'type': 'object',
                        'additionalProperties': True,
                        'properties': {
                            'id': {
                                'type': 'string',
                                'description': 'Stable id for this question; echoed in the answer.',
                            },
                            'question': {
                                'type': 'string',
                                'description': 'The specific question to ask the user.',
                            },
                            'header': {
                                'type': 'string',
                                'description': (
                                    'Optional short heading for the question, such as '
                                    '"Confirm" or "Choose Mode".'
                                ),
                            },
                            'options': {
                                'type': 'array',
                                'description': (
                                    'Optional choices to show the user. If you recommend one, '
                                    'put it first and append "(Recommended)" to that label.'
                                ),
                                'items': {
                                    'type': 'object',
                                    'additionalProperties': True,
                                    'properties': {
                                        'label': {
                                            'type': 'string',
                                            'description': 'Short user-facing option label.',
                                        },
                                        'description': {
                                            'type': 'string',
                                            'description': 'One sentence explaining the tradeoff or impact.',
                                        },
                                    },
                                    'required': ['label'],
                                },
                            },
                            'multi_select': {
                                'type': 'boolean',
                                'description': (
                                    'Whether the user may select more than one option. '
                                    'Defaults to false.'
                                ),
                            },
                        },
                        'required': ['id', 'question'],
                    },
                },
            },
            'required': ['questions'],
        },
    },
    'bash': {
        'name': 'bash',
        'description': (
            'Execute a bash command (`bash -c`) and return its stdout/stderr. Each call runs '
            'in a fresh shell; pass `workdir` instead of using `cd`. Long output is truncated '
            'to its tail; the full output is saved to a file whose path is reported when '
            'available. Provide `description` before `command` in the arguments. Before any '
            'delete or move, verify that the resolved absolute target path is the intended '
            'one; never run it against a computed path you have not checked. An unset '
            'variable expands to an empty string, so guard variables in such paths with '
            '`${VAR:?}`.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'description': {
                    'type': 'string',
                    'description': (
                        'Clear, concise description of what this command does in active '
                        'voice, 5-10 words (shown in the UI). Examples: "ls" → "List files in '
                        'current directory"; "git status" → "Show working tree status"; "npm '
                        'install" → "Install package dependencies".'
                    ),
                },
                'command': {
                    'type': 'string',
                    'description': 'The bash command to execute.',
                },
                'timeoutMs': {
                    'type': 'number',
                    'description': (
                        'Timeout in milliseconds. The executor applies its configured default '
                        'and cap; on expiry the command moves to the background as a job '
                        'instead of being killed.'
                    ),
                },
                'workdir': {
                    'type': 'string',
                    'description': (
                        'Working directory for this command. Defaults to the session '
                        'workspace; a relative path is resolved against it.'
                    ),
                },
                'run_in_background': {
                    'type': 'boolean',
                    'description': (
                        'Run in the background and return a job id immediately (collect with '
                        'job_output, stop with job_kill). No timeout applies.'
                    ),
                },
            },
            'required': ['description', 'command'],
        },
    },
    'create_goal': {
        'name': 'create_goal',
        'description': (
            'Create a persisted goal that keeps this session working across automatic '
            'continuation rounds. Use it when the direct human request is a long-running '
            'objective, even if the user did not say "goal"; not for single-turn work.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'objective': {
                    'type': 'string',
                    'description': 'The concrete completion objective inferred from the direct human request.',
                },
                'max_goal_rounds': {
                    'type': 'number',
                    'description': 'Optional positive safe-integer limit on automatic continuation rounds.',
                },
            },
            'required': ['objective'],
        },
    },
    'edit': {
        'name': 'edit',
        'description': 'Edit an existing UTF-8 text file by replacing literal text.',
        'parameters': {
            'type': 'object',
            'properties': {
                'file_path': {
                    'type': 'string',
                    'description': (
                        'Path to edit, resolved by the filesystem backend. Provide '
                        '`file_path` before `old_string` and `new_string` in the arguments.'
                    ),
                },
                'old_string': {
                    'type': 'string',
                    'description': 'Literal text to replace.',
                },
                'new_string': {
                    'type': 'string',
                    'description': 'Literal replacement text. Use an empty string to delete the match.',
                },
                'replace_all': {
                    'type': 'boolean',
                    'description': (
                        'Replace all matches. Defaults to false; when false, old_string must '
                        'appear exactly once.'
                    ),
                },
            },
            'required': ['file_path', 'old_string', 'new_string'],
        },
    },
    'get_goal': {
        'name': 'get_goal',
        'description': (
            'Read the current session goal, including the id and revision that update_goal '
            'requires.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
            },
        },
    },
    'glob': {
        'name': 'glob',
        'description': (
            'Find files, not directories, whose paths match a glob pattern, including hidden '
            'and ignored files. Returns up to 100 paths in modification-time order; a larger '
            'result keeps the first paths and reports where the complete list was saved.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'pattern': {
                    'type': 'string',
                    'description': (
                        'Glob pattern to match file paths against (e.g. "**/*.ts", '
                        '"src/**/*.test.js"). A pattern with no "/" matches the basename at '
                        'any depth, so "*" and "*.ts" both search the whole tree; include a '
                        'separator to anchor the depth.'
                    ),
                },
                'path': {
                    'type': 'string',
                    'description': (
                        'Directory to search in. Defaults to the session workspace; a '
                        'relative path resolves against it.'
                    ),
                },
            },
            'required': ['pattern'],
        },
    },
    'grep': {
        'name': 'grep',
        'description': (
            'Search file contents with a regular expression. Returns matching lines with line '
            'numbers, grouped by file. Returns up to 250 matches; a larger result reports '
            'where the complete match list was saved.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'pattern': {
                    'type': 'string',
                    'description': (
                        'Regular expression to search for (Python `re` syntax, matched line '
                        'by line).'
                    ),
                },
                'path': {
                    'type': 'string',
                    'description': (
                        'File or directory to search. Defaults to the session workspace; a '
                        'relative path resolves against it.'
                    ),
                },
                'include': {
                    'type': 'string',
                    'description': (
                        'One glob filter for which files to search (e.g. "*.ts", '
                        '"*.{js,jsx}"). Not a list; negation is not supported.'
                    ),
                },
            },
            'required': ['pattern'],
        },
    },
    'interrupt_agent': {
        'name': 'interrupt_agent',
        'description': (
            'Ask a subagent to stop its current work. This call returns without waiting for '
            "it to stop. You can continue a local subagent's conversation later with "
            'send_message. External executions stop permanently and cannot receive '
            'follow-ups. Subagents it started will keep running.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'agent': {
                    'type': 'string',
                    'description': (
                        'The name of an agent created under you: your direct child or a '
                        'deeper descendant.'
                    ),
                },
            },
            'required': ['agent'],
        },
    },
    'job_kill': {
        'name': 'job_kill',
        'description': 'Request cancellation of a running background job.',
        'parameters': {
            'type': 'object',
            'properties': {
                'job_id': {
                    'type': 'string',
                    'description': 'Job id returned by the tool that started the background work.',
                },
                'reason': {
                    'type': 'string',
                    'description': 'Optional short reason, recorded in the log and forwarded to the job.',
                },
            },
            'required': ['job_id'],
        },
    },
    'job_list': {
        'name': 'job_list',
        'description': 'List your background jobs (running and finished) with their ids, kinds, and statuses.',
        'parameters': {
            'type': 'object',
            'properties': {
            },
        },
    },
    'job_output': {
        'name': 'job_output',
        'description': (
            'Read a background job: output since the previous read for stream jobs, or the '
            'result of a finished final-output job.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'job_id': {
                    'type': 'string',
                    'description': 'Job id returned by the tool that started the background work.',
                },
                'wait': {
                    'type': 'boolean',
                    'description': (
                        'Block until the job finishes or the timeout expires; a timed-out '
                        'wait leaves the job running. Defaults to false.'
                    ),
                },
                'timeout_ms': {
                    'type': 'number',
                    'description': (
                        'Max wait in milliseconds with wait: true. Defaults to and is capped '
                        'by configuration.'
                    ),
                },
            },
            'required': ['job_id'],
        },
    },
    'list_agents': {
        'name': 'list_agents',
        'description': (
            'List your entire subagent tree: the root agent and all reachable continuable '
            'descendants, including yourself. Rows carry names, descriptions, and status. A '
            "descendant's name is its path from the root, such as reviewer/checker. You can "
            'message any other agent in the subagent tree. running means it is working; '
            'inactive means no turn is executing; it does not indicate task completion, '
            "success, or failure. corrupt, unsupported, or unavailable means this row's own "
            'descendants could not be read; the row itself is still a member you can message. '
            'While you remain active, you will be notified when a direct child finishes; '
            'there is no need to keep checking its status. Use send_message to continue the '
            'conversation.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
            },
        },
    },
    'pwsh': {
        'name': 'pwsh',
        'description': (
            'Execute a PowerShell command (`pwsh -Command`) and return its stdout/stderr. '
            'Each call runs in a fresh pwsh process; pass `workdir` instead of using `cd`. '
            'Paths use native Windows form (`C:\\...`); read environment variables with '
            '`$env:NAME`. Long output is truncated to its tail; the full output is saved to a '
            'file whose path is reported when available. On Windows a force-killed command '
            'settles as `[exit code: 1]` without a signal marker — treat it as an '
            'interruption, not a command failure. Provide `description` before `command` in '
            'the arguments. Before any delete or move, verify that the resolved absolute '
            'target path is the intended one; never run it against a computed path you have '
            'not checked. Do not assign to automatic variables such as `$HOME`; variable '
            'names are case-insensitive, so `$home` is the same read-only variable.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'description': {
                    'type': 'string',
                    'description': (
                        'Clear, concise description of what this command does in active '
                        'voice, 5-10 words (shown in the UI). Examples: "ls" → "List files in '
                        'current directory"; "git status" → "Show working tree status"; '
                        '"Get-Process" → "List running processes".'
                    ),
                },
                'command': {
                    'type': 'string',
                    'description': 'The PowerShell command to execute.',
                },
                'timeoutMs': {
                    'type': 'number',
                    'description': (
                        'Timeout in milliseconds. The executor applies its configured default '
                        'and cap; on expiry the command moves to the background as a job '
                        'instead of being killed.'
                    ),
                },
                'workdir': {
                    'type': 'string',
                    'description': (
                        'Working directory for this command. Defaults to the session '
                        'workspace; a relative path is resolved against it.'
                    ),
                },
                'run_in_background': {
                    'type': 'boolean',
                    'description': (
                        'Run in the background and return a job id immediately (collect with '
                        'job_output, stop with job_kill). No timeout applies.'
                    ),
                },
            },
            'required': ['description', 'command'],
        },
    },
    'read': {
        'name': 'read',
        'description': 'Read a UTF-8 text file and return line-numbered content.',
        'parameters': {
            'type': 'object',
            'properties': {
                'file_path': {
                    'type': 'string',
                    'description': 'Path to read, resolved by the filesystem backend.',
                },
                'offset': {
                    'type': 'number',
                    'description': '1-based first line to return. Defaults to 1.',
                },
                'limit': {
                    'type': 'number',
                    'description': 'Maximum number of lines to return. Defaults to 2000.',
                },
            },
            'required': ['file_path'],
        },
    },
    'read_image': {
        'name': 'read_image',
        'description': (
            'Read a PNG/JPEG/WebP/GIF file and return the image itself. Large images are '
            'downscaled automatically; do not install image libraries or create thumbnails to '
            'inspect an image.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'file_path': {
                    'type': 'string',
                    'description': 'Path to the image file, resolved by the filesystem backend.',
                },
            },
            'required': ['file_path'],
        },
    },
    'send_message': {
        'name': 'send_message',
        'description': (
            'Send a message to an agent. A working agent receives it at its next step; an '
            'idle agent starts a new turn with it. Returns delivery confirmation.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'agent': {
                    'type': 'string',
                    'description': 'The name of another agent in your subagent tree, as shown by list_agents.',
                },
                'message': {
                    'type': 'string',
                    'description': 'The message to deliver to the agent.',
                },
            },
            'required': ['agent', 'message'],
        },
    },
    'subagent': {
        'name': 'subagent',
        'description': (
            'Delegate a self-contained task to a subagent (a separate agent that works in its '
            'own context) to offload focused, independent work — research, a scoped '
            "implementation, an analysis — so it does not consume this conversation's "
            'context. The subagent returns its result, not its intermediate steps. This tool '
            'starts an independently managed subagent and immediately returns its name. If '
            'you are still online when it finishes, the runtime automatically delivers its '
            'completion notice. The notice includes the final answer unless the child sent '
            'you a message just before finishing. Use `send_message` to steer it while '
            'running or continue its conversation after it finishes.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'cwd': {
                    'type': 'string',
                    'description': (
                        'Initial child working directory. Relative paths use your current '
                        'directory; omitted inherits it. Later directory changes in either '
                        'agent are independent.'
                    ),
                },
                'description': {
                    'type': 'string',
                    'description': (
                        'A short (3-5 word) description of the delegated task, for display. '
                        "Use the language of the user's current request."
                    ),
                },
                'name': {
                    'type': 'string',
                    'description': (
                        'Optional short name for the subagent, unique among the subagents you '
                        'create: lower-case letters, digits, and hyphens, starting with a '
                        'letter, at most 32 characters; lead is reserved for the root agent. '
                        'Omitted names default to agent-1, agent-2, and so on.'
                    ),
                },
                'prompt': {
                    'type': 'string',
                    'description': (
                        'The complete, self-contained task for the subagent. It does not '
                        "share this conversation's context, so include everything it needs."
                    ),
                },
            },
            'required': ['description', 'prompt'],
        },
    },
    'todo_write': {
        'name': 'todo_write',
        'description': (
            'Record and update a task list to plan multi-step work and show progress; skip it '
            'for trivial single-step tasks. Add one todo per concrete step before you start. '
            'While work remains, keep the todos being worked on `in_progress`, several only '
            'when work runs in parallel. Mark each todo `completed` as soon as it is done.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'todos': {
                    'type': 'array',
                    'description': 'The COMPLETE task list, replacing any previous list.',
                    'items': {
                        'type': 'object',
                        'additionalProperties': False,
                        'properties': {
                            'content': {
                                'type': 'string',
                                'description': 'What the task is — a short imperative line.',
                            },
                            'status': {
                                'type': 'string',
                                'description': 'pending (not started) | in_progress (now) | completed (done).',
                                'enum': ['pending', 'in_progress', 'completed'],
                            },
                        },
                        'required': ['content', 'status'],
                    },
                },
            },
            'required': ['todos'],
        },
    },
    'update_goal': {
        'name': 'update_goal',
        'description': 'Update the current goal.',
        'parameters': {
            'type': 'object',
            'properties': {
                'goal_id': {
                    'type': 'string',
                    'description': 'Exact id returned by get_goal.',
                },
                'revision': {
                    'type': 'number',
                    'description': 'Exact positive revision returned by get_goal.',
                },
                'action': {
                    'type': 'string',
                    'description': (
                        'edit, pause, and resume require a direct top-level human request. '
                        'complete and blocked are also allowed during an automatic '
                        'continuation of this goal; blocked is rejected before the configured '
                        'minimum round count.'
                    ),
                    'enum': ['edit', 'pause', 'resume', 'complete', 'blocked'],
                },
                'objective': {
                    'type': 'string',
                    'description': 'Replacement objective; valid only with action edit.',
                },
                'max_goal_rounds': {
                    'type': 'number',
                    'description': 'Replacement cap; valid only with action edit.',
                },
                'blocked_reason': {
                    'type': 'string',
                    'description': (
                        'Required only with action blocked: the concrete condition that '
                        'persisted across rounds and blocks progress.'
                    ),
                },
            },
            'required': ['goal_id', 'revision', 'action'],
        },
    },
    'wait_agent': {
        'name': 'wait_agent',
        'description': (
            'Wait for input at your next step, including agent messages, completion '
            'notifications, and user steering. Returns immediately if new input is pending. '
            '`timedOut: false` means new input is pending. Pending input is delivered to the '
            'model after code execution finishes.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'timeout_ms': {
                    'type': 'integer',
                    'description': (
                        'Wait duration in milliseconds, from 10000 through 3600000. Defaults '
                        'to 30000.'
                    ),
                },
            },
        },
    },
    'web_fetch': {
        'name': 'web_fetch',
        'description': 'Fetch the content of a specific HTTP(S) URL and return it decoded to text.',
        'parameters': {
            'type': 'object',
            'properties': {
                'url': {
                    'type': 'string',
                    'description': 'The HTTP(S) URL to fetch.',
                },
            },
            'required': ['url'],
        },
    },
    'web_search': {
        'name': 'web_search',
        'description': (
            'Search the web for current information. Returns an optional summary answer and a '
            'list of source URLs.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'queries': {
                    'type': 'array',
                    'description': '1–10 search queries; their results are merged.',
                    'items': {
                        'type': 'string',
                    },
                },
            },
            'required': ['queries'],
        },
    },
    'write': {
        'name': 'write',
        'description': 'Create or fully replace a UTF-8 text file.',
        'parameters': {
            'type': 'object',
            'properties': {
                'file_path': {
                    'type': 'string',
                    'description': (
                        'Path to write, resolved by the filesystem backend. Provide '
                        '`file_path` before `content` in the arguments.'
                    ),
                },
                'content': {
                    'type': 'string',
                    'description': 'Full UTF-8 text content to write.',
                },
            },
            'required': ['file_path', 'content'],
        },
    },
}

# This project's own tools. Their wording is ours, not dsh's.
NEKOCLAW_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    'note_lesson': {
        'name': 'note_lesson',
        'description': (
            'Submit one lesson worth keeping for future commissions: a correction or approval '
            'from the user, a reusable finding, a reflection on what went wrong, a pointer to a '
            'resource, or project progress. Record only what would change how a later commission '
            'is carried out; do not record what the files themselves already state. The host '
            'stores the lesson; this call writes no file itself.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'title': {
                    'type': 'string',
                    'description': 'Short title of a few words.',
                },
                'content': {
                    'type': 'string',
                    'description': (
                        'The lesson, self-contained: what applies, why, and when to apply it.'
                    ),
                },
                'category': {
                    'type': 'string',
                    'description': (
                        'feedback (a correction or approval from the user) | lesson (a reusable '
                        'finding) | reflection (what went wrong and what to do instead) | '
                        'reference (where something can be found) | progress (state of the '
                        'project).'
                    ),
                    'enum': ['feedback', 'lesson', 'reflection', 'reference', 'progress'],
                },
                'scope': {
                    'type': 'string',
                    'description': (
                        'project (the project of this commission; the default) | global (every '
                        'project).'
                    ),
                    'enum': ['project', 'global'],
                },
            },
            'required': ['title', 'content', 'category'],
        },
    },
    'report': {
        'name': 'report',
        'description': (
            'Report on this commission to whoever commissioned it. Use `progress` for a notable '
            'milestone while work continues. Use `ready` when the commission is complete, '
            '`blocked` when it cannot proceed without something you cannot obtain yourself, '
            '`failed` when it cannot be completed, and `waiting` when you need an answer before '
            'continuing. Those four end your current turn, so call report on its own, after all '
            'other work of the turn is done. State only what tool results in this session '
            'establish.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'status': {
                    'type': 'string',
                    'description': (
                        'progress (work continues) | waiting (an answer is needed) | ready '
                        '(complete) | blocked (cannot proceed) | failed (cannot be completed).'
                    ),
                    'enum': ['progress', 'waiting', 'ready', 'blocked', 'failed'],
                },
                'summary': {
                    'type': 'string',
                    'description': (
                        'One or two sentences for the user stating the outcome or the current '
                        "state. Use the language of the user's request."
                    ),
                },
                'details': {
                    'type': 'string',
                    'description': (
                        'Optional supporting details: what was done, how it was verified, and the '
                        'paths of the results. With blocked or waiting, exactly what is needed.'
                    ),
                },
                'delivery': {
                    'type': 'string',
                    'description': (
                        'Optional suggestion for how this report reaches the user: proactive '
                        '(tell them now) | passive (mention it at their next turn) | silent '
                        '(only record it). The host makes the final choice.'
                    ),
                    'enum': ['proactive', 'passive', 'silent'],
                },
            },
            'required': ['status', 'summary'],
        },
    },
}
