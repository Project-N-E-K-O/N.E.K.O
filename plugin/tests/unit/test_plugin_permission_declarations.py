"""plugin.toml permission declarations, narrowing, the permission scan and check -r."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from plugin.neko_plugin_cli import cli as neko_plugin_cli
from plugin.core import capability_analysis
from plugin.core.capability_analysis import (
    analyze_capabilities,
    compare_declarations,
    scan_note,
    summarize_permissions,
)
from plugin.core.permission_declarations import (
    PERMISSION_IDS,
    narrowing_issues,
    parse_permission_declarations,
    resolve_text,
)
from plugin.neko_plugin_cli.commands import _permission_messages as permission_messages
from plugin.neko_plugin_cli.commands.validate_cmd import validate_plugin_dir

LOCALES = ("en", "zh-CN", "zh-TW", "ja", "ko", "ru", "es", "pt")


def _parse(text: str):
    return parse_permission_declarations(tomllib.loads(text))


def _codes(issues) -> list[tuple[str, str]]:
    return [(issue.severity, issue.code) for issue in issues]


# --- declarations -----------------------------------------------------------


def test_valid_declarations_are_parsed():
    declarations, issues = _parse(
        """
[permissions.require]
"system.network:connect" = { reason = "Fetch the forecast", hosts = ["api.example.com"] }

[permissions.ask]
"plugin_sdk.main_agent.memory:read" = { reason = { zh = "按常住城市给出天气", en = "Use your home city" }, background = true, background_reason = { en = "Morning summary" } }
"plugin_sdk.models:invoke" = { reason = "Turn the forecast into one sentence" }
"""
    )
    assert issues == []
    assert declarations.status == "declared"
    network = declarations.items["system.network:connect"]
    assert (network.level, network.reason, network.hosts, network.background) == (
        "require",
        "Fetch the forecast",
        ("api.example.com",),
        False,
    )
    memory = declarations.items["plugin_sdk.main_agent.memory:read"]
    assert memory.level == "ask"
    assert memory.reason == {"zh": "按常住城市给出天气", "en": "Use your home city"}
    assert memory.background is True
    assert memory.background_reason == {"en": "Morning summary"}
    assert declarations.ids() == {
        "system.network:connect",
        "plugin_sdk.main_agent.memory:read",
        "plugin_sdk.models:invoke",
    }


def test_missing_permissions_table_is_not_an_error():
    declarations, issues = parse_permission_declarations({"plugin": {"id": "x"}})
    assert declarations.status == "none"
    assert declarations.items == {}
    assert issues == []


@pytest.mark.parametrize(
    "text,expected",
    [
        ('permissions = "all"', [("error", "table_type")]),
        ('permissions = { require = ["system.env:read"] }', [("error", "table_type")]),
        ('[permissions.requires]\n"system.env:read" = { reason = "x" }', [("error", "unknown_section")]),
        ('[permissions.ask]\n"system.camera:read" = { reason = "x" }', [("error", "unknown_permission")]),
        ('[permissions.ask]\n"system.network" = { reason = "x" }', [("error", "unknown_permission")]),
        ('[permissions.ask]\n"system.env:read" = "x"', [("error", "entry_type")]),
        ('[permissions.ask]\n"system.env:read" = {}', [("error", "reason_missing")]),
        ('[permissions.ask]\n"system.env:read" = { reason = "  " }', [("error", "text_empty")]),
        ('[permissions.ask]\n"system.env:read" = { reason = 3 }', [("error", "text_type")]),
        ('[permissions.ask]\n"system.env:read" = { reason = { en = "" } }', [("warning", "text_translation_empty"), ("error", "text_empty")]),
        ('[permissions.ask]\n"system.env:read" = { reason = { en = 1, ja = "x" } }', [("error", "text_translation_type")]),
        ('[permissions.ask]\n"system.env:read" = { reason = "x", background = "yes" }', [("error", "background_type")]),
        ('[permissions.ask]\n"system.env:read" = { reason = "x", background = true }', [("error", "background_reason_missing")]),
        ('[permissions.ask]\n"system.env:read" = { reason = "x", background = true, background_reason = "" }', [("error", "text_empty")]),
        ('[permissions.ask]\n"system.env:read" = { reason = "x", background_reason = "y" }', [("warning", "background_reason_unused")]),
        ('[permissions.require]\n"system.env:read" = { reason = "x", background = true }', [("warning", "background_on_require")]),
        ('[permissions.ask]\n"system.network:connect" = { reason = "x", hosts = "a.com" }', [("error", "hosts_type")]),
        ('[permissions.ask]\n"system.network:connect" = { reason = "x", hosts = [""] }', [("error", "hosts_type")]),
        ('[permissions.ask]\n"system.env:read" = { reason = "x", hosts = ["a.com"] }', [("warning", "hosts_unused")]),
        ('[permissions.ask]\n"system.env:read" = { reason = "x", why = "y" }', [("warning", "unknown_field")]),
    ],
)
def test_invalid_declarations(text, expected):
    _declarations, issues = _parse(text)
    assert _codes(issues) == expected


def test_permission_declared_in_both_sections_is_an_error():
    declarations, issues = _parse(
        '[permissions.require]\n"system.env:read" = { reason = "a" }\n'
        '[permissions.ask]\n"system.env:read" = { reason = "b" }\n'
    )
    assert _codes(issues) == [("error", "duplicate_permission")]
    assert declarations.level("system.env:read") == "require"


def test_invalid_entries_are_left_out_of_the_declarations():
    declarations, _issues = _parse(
        '[permissions.ask]\n"system.env:read" = { reason = "" }\n"system.fs:read" = { reason = "ok" }\n'
    )
    assert declarations.ids() == {"system.fs:read"}


def test_reason_may_cover_only_some_languages():
    declarations, issues = _parse('[permissions.ask]\n"system.env:read" = { reason = { ja = "設定を読む" } }\n')
    assert issues == []
    assert declarations.items["system.env:read"].reason == {"ja": "設定を読む"}


# --- reason language fallback -------------------------------------------------


def test_resolve_text_prefers_the_requested_locale_then_its_base_language():
    reason = {"zh": "中文", "en": "English", "ja": "日本語"}
    assert resolve_text(reason, "ja") == "日本語"
    assert resolve_text(reason, "zh-TW") == "中文"
    assert resolve_text(reason, "zh_CN") == "中文"
    assert resolve_text({"zh-CN": "简体", "zh-TW": "繁體"}, "zh-TW") == "繁體"
    assert resolve_text({"zh-CN": "简体", "zh-TW": "繁體"}, "zh") == "简体"


def test_resolve_text_falls_back_to_a_stable_language():
    assert resolve_text({"ja": "日本語", "en": "English"}, "ru") == "English"
    without_english = {"ko": "한국어", "es": "Español", "ja": "日本語"}
    picks = {resolve_text(without_english, locale) for locale in ("ru", "pt", None, "")}
    assert picks == {"Español"}
    assert resolve_text({"ja": " ", "ko": "한국어"}, "ja") == "한국어"
    assert resolve_text("Only one", "ja") == "Only one"
    assert resolve_text({}, "en") is None


# --- narrowing ---------------------------------------------------------------


def test_narrowing_must_be_a_subset_of_the_declarations():
    declared = frozenset({"system.network:connect", "plugin_sdk.main_agent.memory:read"})
    assert narrowing_issues(["system.network:connect"], declared, "@llm_tool") == []
    assert narrowing_issues([], declared, "@llm_tool") == []
    assert _codes(narrowing_issues(["system.env:read"], declared, "@llm_tool")) == [
        ("error", "narrowing_not_declared")
    ]
    assert _codes(narrowing_issues(["memory.read"], declared, "@llm_tool")) == [("error", "unknown_permission")]
    assert _codes(narrowing_issues("system.network:connect", declared, "@llm_tool")) == [("error", "narrowing_type")]
    assert _codes(narrowing_issues([1], declared, "@llm_tool")) == [("error", "narrowing_type")]


# --- analyzer -------------------------------------------------------------------


def _scan(tmp_path: Path, source: str, manifest: str = "", **files: str) -> dict:
    (tmp_path / "main.py").write_text(source, encoding="utf-8")
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    if manifest:
        (tmp_path / "plugin.toml").write_text(manifest, encoding="utf-8")
    return analyze_capabilities(tmp_path)


def _bases(report: dict) -> dict[str, str]:
    return {permission: item["basis"] for permission, item in report["permissions"].items()}


@pytest.mark.parametrize(
    "source,permission",
    [
        ("import socket\nsocket.create_connection(('a', 1))", "system.network:connect"),
        ("from urllib.request import urlopen\nurlopen('https://a')", "system.network:connect"),
        ("import httpx as h\nasync def f():\n    async with h.AsyncClient() as c:\n        pass", "system.network:connect"),
        ("import requests\nrequests.Session().get('x')", "system.network:connect"),
        ("import subprocess\nsubprocess.run(['ls'])", "system.process:spawn"),
        ("import os\nos.system('ls')", "system.process:spawn"),
        ("import asyncio\nasyncio.create_subprocess_exec('ls')", "system.process:spawn"),
        ("import os\nos.environ.get('KEY')", "system.env:read"),
        ("import os\nx = os.environ['KEY']", "system.env:read"),
        ("from os import getenv\ngetenv('KEY')", "system.env:read"),
        ("from plugin.sdk.plugin import MemoryClient\nMemoryClient(ctx).search(query='q')", "plugin_sdk.main_agent.memory:read"),
        ("from plugin.sdk import create_card\ncreate_card()", "plugin_sdk.main_agent.messages:push"),
    ],
)
def test_resolved_origins_are_direct_evidence(tmp_path, source, permission):
    report = _scan(tmp_path, source)
    assert report["permissions"][permission]["basis"] in capability_analysis.DIRECT_BASES


def test_sdk_suffix_matches_are_weak_evidence_and_legacy_capabilities_are_unchanged(tmp_path):
    report = _scan(tmp_path, "class P:\n    def go(self):\n        self.ctx.push_message('hi')\n        self.ctx.bus.memory.get('x')\n")
    assert report["capabilities"] == ["memory.read", "messages.push"]
    assert _bases(report) == {
        "plugin_sdk.main_agent.memory:read": "suffix_match",
        "plugin_sdk.main_agent.messages:push": "suffix_match",
    }
    finding = report["permissions"]["plugin_sdk.main_agent.messages:push"]["findings"][0]
    assert finding == {
        "file": "main.py",
        "line": 3,
        "function": "P.go",
        "method": "self.ctx.push_message",
        "basis": "suffix_match",
    }


def test_import_only_is_the_weakest_basis_and_yields_to_calls(tmp_path):
    report = _scan(tmp_path, "import subprocess\nimport socket\nsocket.socket()\n")
    assert _bases(report) == {
        "system.process:spawn": "import_only",
        "system.network:connect": "resolved_call",
    }


@pytest.mark.parametrize(
    "source",
    [
        "import socket\ndef f(s):\n    return isinstance(s, socket.socket)",
        "import httpx\ndef f(client: httpx.AsyncClient):\n    return client",
        "import subprocess\nrunner = subprocess.run",
    ],
)
def test_naming_a_callable_without_calling_it_is_weak_evidence(tmp_path, source):
    report = _scan(tmp_path, source)
    assert report["permissions"]
    for item in report["permissions"].values():
        assert item["basis"] not in capability_analysis.DIRECT_BASES
    assert not [issue for issue in compare_declarations(report) if issue.severity == "error"]


@pytest.mark.parametrize(
    "rebinding",
    [
        "for os in items:\n    os.system('x')",
        "with make() as os:\n    os.system('x')",
        "os, other = make()\nos.system('x')",
        "try:\n    pass\nexcept Exception as os:\n    os.system('x')",
        "if (os := make()):\n    os.system('x')",
        "[os.system('x') for os in items]",
    ],
)
def test_names_rebound_by_other_targets_are_not_direct_evidence(tmp_path, rebinding):
    report = _scan(tmp_path, "import os\n" + rebinding + "\n")
    assert report["permissions"]["system.process:spawn"]["basis"] == "suffix_match"
    assert not [issue for issue in compare_declarations(report) if issue.severity == "error"]


def test_rebinding_does_not_change_legacy_capabilities(tmp_path):
    source = "from .helpers import client\nfor client in clients:\n    client.push_message()\n"
    assert _scan(tmp_path, source)["capabilities"] == []


def test_model_client_access_is_reported_as_models_invoke(tmp_path):
    report = _scan(tmp_path, "class P:\n    async def go(self):\n        client = self.ctx.models.get_client()\n")
    assert report["permissions"]["plugin_sdk.models:invoke"]["basis"] == "suffix_match"
    assert report["capabilities"] == []


def test_evidence_cap_keeps_the_strongest_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(capability_analysis, "MAX_PERMISSION_EVIDENCE", 3)
    weak = "\n".join("self.ctx.push_message('x')" for _ in range(5))
    report = _scan(tmp_path, weak + "\nfrom plugin.sdk import create_card\ncreate_card()\n")
    item = report["permissions"]["plugin_sdk.main_agent.messages:push"]
    assert len(item["findings"]) == 3
    assert item["basis"] == "resolved_call"


def test_shadowed_or_plugin_owned_names_are_not_reported(tmp_path):
    report = _scan(
        tmp_path,
        "def open(path):\n    return path\nopen('x')\n"
        "from .helpers import subprocess\nsubprocess.run()\n"
        "def f(os):\n    os.environ.get('x')\n",
    )
    assert report["permissions"] == {}


@pytest.mark.parametrize(
    "call,expected,mode",
    [
        ("open('a.txt')", {"system.fs:read"}, "read"),
        ("open('a.txt', 'rb')", {"system.fs:read"}, "read"),
        ("open('a.txt', mode='w')", {"system.fs:write"}, "write"),
        ("open('a.txt', 'r+')", {"system.fs:write"}, "write"),
        ("open('a.txt', mode)", {"system.fs:read", "system.fs:write"}, "unknown"),
        ("import pathlib\npathlib.Path('a').write_text('x')", {"system.fs:write"}, None),
        ("import shutil\nshutil.rmtree('a')", {"system.fs:write"}, None),
    ],
)
def test_file_access_and_open_modes(tmp_path, call, expected, mode):
    report = _scan(tmp_path, call)
    assert set(report["permissions"]) == expected
    if mode:
        for item in report["permissions"].values():
            assert item["findings"][0]["mode"] == mode


@pytest.mark.parametrize(
    "source,kind",
    [
        ("import importlib\nimportlib.import_module(name)", "dynamic_import"),
        ("__import__(name)", "dynamic_import"),
        ("getattr(obj, name)", "reflective_access"),
        ("import operator\noperator.attrgetter('a')", "reflective_access"),
        ("class P:\n    def f(self):\n        return self.ctx.__dict__", "reflective_access"),
        ("eval(text)", "code_exec"),
        ("exec(text)", "code_exec"),
        ("import runpy\nrunpy.run_path('x.py')", "code_exec"),
        ("import ctypes\nctypes.CDLL('libc.so.6')", "native_loader"),
        ("from ctypes import cdll\ncdll.LoadLibrary('x')", "native_loader"),
        ("import threading\nthreading.Thread(target=f).start()", "context_loss"),
        ("from concurrent.futures import ThreadPoolExecutor\nThreadPoolExecutor()", "context_loss"),
        ("async def f(loop, pool):\n    await loop.run_in_executor(pool, g)", "context_loss"),
    ],
)
def test_blind_spots_are_reported(tmp_path, source, kind):
    report = _scan(tmp_path, source)
    assert report["status"] == "completed"
    assert [(spot["type"], spot["file"]) for spot in report["blind_spots"]] == [(kind, "main.py")]
    assert report["blind_spots"][0]["line"] >= 1


@pytest.mark.parametrize(
    "source",
    [
        "import importlib\nimportlib.import_module('json')",
        "getattr(obj, 'name')",
        "getattr(obj, 'not-an-identifier', None)",
        "async def f(loop):\n    await loop.run_in_executor(None, g)",
        "import asyncio\nasyncio.to_thread(g)",
        "def eval(x):\n    return x\neval('1')",
    ],
)
def test_statically_known_forms_are_not_blind_spots(tmp_path, source):
    assert _scan(tmp_path, source)["blind_spots"] == []


def test_unscanned_parts_are_blind_spots_without_changing_status(tmp_path):
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "lib.py").write_text("import socket", encoding="utf-8")
    report = _scan(tmp_path, "x = 1", **{"ui/panel.tsx": "export default 1", "node_modules/a.js": "1"})
    assert report["status"] == "completed"
    assert report["permissions"] == {}
    assert [(spot["type"], spot["reason"], spot["file"]) for spot in report["blind_spots"]] == [
        ("not_analyzed", "non_python", "ui/panel.tsx"),
        ("not_analyzed", "vendor", "vendor"),
    ]


def test_analysis_warnings_become_not_analyzed_blind_spots(tmp_path):
    report = _scan(tmp_path, "x = 1", **{"bad.py": "def broken("})
    assert report["status"] == "partial"
    assert {"type": "not_analyzed", "file": "bad.py", "line": None, "reason": "source_unreadable"} in report["blind_spots"]
    assert scan_note(report) == "blind_spots"


def test_report_carries_declarations_from_plugin_toml(tmp_path):
    report = _scan(
        tmp_path,
        "import os\nos.getenv('A')\n",
        manifest='[plugin]\nid = "x"\n[permissions.ask]\n"system.env:read" = { reason = "x" }\n'
        '"system.network:connect" = { reason = "y" }\n',
    )
    assert report["declarations"] == "declared"
    assert report["permissions"]["system.env:read"]["declared"] == "ask"
    assert report["permissions"]["system.network:connect"] == {
        "declared": "ask",
        "findings": [],
        "basis": "declared_only",
    }


def test_analyzer_version_was_bumped_for_the_new_report_fields():
    assert capability_analysis.ANALYZER_VERSION >= 5


def test_function_attribution_follows_classes_and_nested_functions(tmp_path):
    report = _scan(
        tmp_path,
        "import os\n"
        "class Plugin:\n"
        "    def outer(self):\n"
        "        def inner():\n"
        "            return os.getenv('A')\n"
        "        cb = lambda: os.getenv('B')\n"
        "os.getenv('C')\n",
    )
    functions = [row["function"] for row in report["permissions"]["system.env:read"]["findings"]]
    assert functions == ["Plugin.outer.inner", "Plugin.outer", ""]


# --- comparison and summary ---------------------------------------------------


def _report(permissions: dict, *, status="completed", blind_spots=(), declarations="declared") -> dict:
    return {
        "status": status,
        "declarations": declarations,
        "permissions": permissions,
        "blind_spots": list(blind_spots),
    }


def _finding(basis: str, line: int = 1) -> dict:
    return {"file": "main.py", "line": line, "function": "", "method": "m", "basis": basis}


def test_check_severities_follow_the_evidence():
    report = _report(
        {
            "system.network:connect": {"declared": None, "findings": [_finding("resolved_call")], "basis": "resolved_call"},
            "system.env:read": {"declared": None, "findings": [_finding("resolved_reference")], "basis": "resolved_reference"},
            "plugin_sdk.main_agent.messages:push": {"declared": None, "findings": [_finding("suffix_match")], "basis": "suffix_match"},
            "system.process:spawn": {"declared": None, "findings": [_finding("import_only")], "basis": "import_only"},
            "system.fs:write": {"declared": None, "findings": [_finding("resolved_call")], "basis": "resolved_call"},
            "plugin_sdk.models:invoke": {"declared": "ask", "findings": [], "basis": "declared_only"},
            "plugin_sdk.main_agent.memory:read": {"declared": "require", "findings": [_finding("resolved_call")], "basis": "resolved_call"},
        }
    )
    assert {issue.params["permission"]: (issue.severity, issue.code) for issue in compare_declarations(report)} == {
        "system.network:connect": ("error", "used_undeclared"),
        "system.env:read": ("error", "used_undeclared"),
        "plugin_sdk.main_agent.messages:push": ("warning", "used_undeclared_possible"),
        "system.process:spawn": ("warning", "used_undeclared_possible"),
        "system.fs:write": ("warning", "used_undeclared_possible"),
        "plugin_sdk.models:invoke": ("warning", "declared_unused"),
    }


def test_error_locations_list_direct_evidence_and_count_the_rest():
    findings = [_finding("suffix_match", 1)] + [_finding("resolved_call", line) for line in range(2, 7)]
    report = _report({"system.env:read": {"declared": None, "findings": findings, "basis": "resolved_call"}})
    [issue] = compare_declarations(report)
    assert issue.params["locations"] == "main.py:2, main.py:3, main.py:4"
    assert issue.params["more"] == "2"


def test_invalid_table_and_unavailable_scan_do_not_pile_up_findings():
    used = {"system.env:read": {"declared": None, "findings": [_finding("resolved_call")], "basis": "resolved_call"}}
    assert compare_declarations(_report(used, declarations="invalid")) == []
    declared = {"system.env:read": {"declared": "ask", "findings": [], "basis": "declared_only"}}
    assert compare_declarations(_report(declared, status="unavailable")) == []


def test_summary_labels_each_row_with_its_source():
    report = _report(
        {
            "system.env:read": {"declared": None, "findings": [_finding("resolved_call", 4)], "basis": "resolved_call"},
            "system.network:connect": {"declared": "require", "findings": [], "basis": "declared_only"},
        },
        blind_spots=[{"type": "code_exec"}, {"type": "dynamic_import"}, {"type": "code_exec"}],
    )
    summary = summarize_permissions(report)
    assert summary["rows"] == [
        {"permission": "system.network:connect", "source": "declared", "level": "require"},
        {"permission": "system.env:read", "source": "detected", "basis": "resolved_call", "file": "main.py", "line": 4},
        {"type": "dynamic_import", "source": "unconfirmed"},
        {"type": "code_exec", "source": "unconfirmed"},
    ]
    assert summary["note"] == "blind_spots"


@pytest.mark.parametrize(
    "status,blind_spots,note",
    [
        ("completed", [], "no_extra_usage"),
        ("completed", [{"type": "code_exec"}], "blind_spots"),
        ("partial", [], "blind_spots"),
        ("unavailable", [], "unavailable"),
        ("failed", [], "unavailable"),
    ],
)
def test_scan_note_never_claims_more_than_the_scan_saw(status, blind_spots, note):
    assert scan_note(_report({}, status=status, blind_spots=blind_spots)) == note


# --- CLI ---------------------------------------------------------------------------


def _plugin(tmp_path: Path, source: str, permissions: str = "", plugin_id: str = "perm_demo") -> Path:
    plugin_dir = tmp_path / plugin_id
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.toml").write_text(
        "[plugin]\n"
        f'id = "{plugin_id}"\n'
        'name = "Permission Demo"\n'
        'version = "0.0.1"\n'
        f'entry = "plugin.plugins.{plugin_id}:DemoPlugin"\n\n'
        "[plugin.sdk]\n"
        'recommended = ">=0.1.0,<0.2.0"\n'
        'supported = ">=0.1.0,<0.3.0"\n\n' + permissions,
        encoding="utf-8",
    )
    (plugin_dir / "__init__.py").write_text(
        "from plugin.sdk.plugin import neko_plugin\n\n@neko_plugin\nclass DemoPlugin: pass\n",
        encoding="utf-8",
    )
    (plugin_dir / "worker.py").write_text(source, encoding="utf-8")
    return plugin_dir


def _issues(plugin_dir: Path, severity: str | None = None) -> list[str]:
    return [message for level, message in validate_plugin_dir(plugin_dir) if severity is None or level == severity]


def _assert_all_locales(message: str) -> None:
    lines = message.splitlines()
    assert [re.match(r"\s+([a-zA-Z-]+): ", line).group(1) for line in lines[1:]] == list(LOCALES[1:])


def test_cli_reports_undeclared_use_as_an_error_in_every_locale(tmp_path):
    plugin_dir = _plugin(tmp_path, "import os\nKEY = os.environ.get('KEY')\n")
    errors = _issues(plugin_dir, "error")
    [error] = [message for message in errors if "system.env:read" in message]
    assert error.startswith("the code uses system.env:read (worker.py:2)")
    _assert_all_locales(error)


def test_cli_accepts_a_matching_declaration(tmp_path):
    plugin_dir = _plugin(
        tmp_path,
        "import os\nKEY = os.environ.get('KEY')\n",
        '[permissions.require]\n"system.env:read" = { reason = { en = "Read the API key", zh-CN = "读取 API 密钥" } }\n',
    )
    messages = validate_plugin_dir(plugin_dir)
    assert not [message for level, message in messages if level == "error"]
    info = [message for level, message in messages if level == "info"]
    assert any(message.startswith("permission summary: [author declared] system.env:read (require)") for message in info)
    assert any(message.startswith("the code scan found no use beyond the declarations") for message in info)


def test_cli_warns_for_weak_evidence_and_unused_declarations(tmp_path):
    plugin_dir = _plugin(
        tmp_path,
        "class P:\n    def go(self):\n        self.ctx.push_message('hi')\n",
        '[permissions.ask]\n"plugin_sdk.models:invoke" = { reason = "x" }\n',
    )
    warnings = _issues(plugin_dir, "warning")
    assert any(message.startswith("the code may use plugin_sdk.main_agent.messages:push") for message in warnings)
    assert any(message.startswith('"plugin_sdk.models:invoke" is declared but the scan found no use') for message in warnings)
    assert not _issues(plugin_dir, "error")


def test_cli_checks_component_narrowing(tmp_path):
    plugin_dir = _plugin(
        tmp_path,
        "from plugin.sdk.plugin import plugin_entry, llm_tool\n"
        "class P:\n"
        "    @plugin_entry(id='a', permissions=['system.network:connect'])\n"
        "    async def a(self): ...\n"
        "    @llm_tool(name='b', permissions=['system.env:read'])\n"
        "    async def b(self): ...\n"
        "    @llm_tool(name='c', permissions=NARROWED)\n"
        "    async def c(self): ...\n",
        '[permissions.ask]\n"system.network:connect" = { reason = "x" }\n',
    )
    messages = validate_plugin_dir(plugin_dir)
    errors = [message for level, message in messages if level == "error"]
    assert any(
        message.startswith('@llm_tool in worker.py:6: permissions= lists "system.env:read"') for message in errors
    )
    assert not any("worker.py:4" in message for message in errors)
    info = [message for level, message in messages if level == "info"]
    assert any(message.startswith("@llm_tool in worker.py:8: permissions= is not a literal list") for message in info)


def test_cli_ui_entry_permissions_are_a_separate_namespace(tmp_path):
    plugin_dir = _plugin(tmp_path, "x = 1\n")
    manifest = plugin_dir / "plugin.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + '\n[permissions.ask]\n"system.env:read" = { reason = "x" }\n',
        encoding="utf-8",
    )
    assert not [message for message in _issues(plugin_dir, "error") if "permission" in message.lower()]


def test_cli_reports_blind_spots_as_info(tmp_path):
    plugin_dir = _plugin(tmp_path, "import threading\nthreading.Thread(target=print)\neval(code)\n")
    info = _issues(plugin_dir, "info")
    assert any(message.startswith("worker.py:2: lost call context") for message in info)
    assert any(message.startswith("worker.py:3: the scan cannot confirm what this code does (code run at runtime") for message in info)
    blind = [message for message in info if message.startswith("the code scan has known blind spots")]
    assert len(blind) == 1
    _assert_all_locales(blind[0])


def test_cli_reports_declaration_errors_in_every_locale(tmp_path):
    plugin_dir = _plugin(tmp_path, "x = 1\n", '[permissions.ask]\n"system.env:read" = { reason = "" }\n')
    [error] = [message for message in _issues(plugin_dir, "error") if "reason" in message]
    _assert_all_locales(error)


def test_release_check_blocks_on_undeclared_use(tmp_path, capsys):
    plugin_dir = _plugin(tmp_path, "import subprocess\nsubprocess.run(['ls'])\n")
    exit_code = neko_plugin_cli.main(
        ["check", "-r", str(plugin_dir), "--skip-tests", "--target-dir", str(tmp_path / "target")]
    )
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "[ERROR] the code uses system.process:spawn (worker.py:2)" in captured.err


def test_release_check_does_not_block_on_warnings_or_blind_spots(tmp_path, capsys):
    plugin_dir = _plugin(
        tmp_path,
        "class P:\n    def go(self):\n        self.ctx.push_message('hi')\n        eval(code)\n",
        '[permissions.ask]\n"system.env:read" = { reason = "x" }\n',
    )
    messages = validate_plugin_dir(plugin_dir)
    assert not [message for level, message in messages if level == "error"]
    assert {level for level, _message in messages} >= {"warning", "info"}


# --- messages --------------------------------------------------------------------


def test_every_issue_code_has_a_message_in_all_locales():
    codes = {
        "table_type", "unknown_section", "unknown_permission", "duplicate_permission", "entry_type",
        "unknown_field", "reason_missing", "text_empty", "text_type", "text_translation_type",
        "text_translation_empty", "background_type", "background_on_require", "background_reason_missing",
        "background_reason_unused", "hosts_type", "hosts_unused", "narrowing_type", "narrowing_not_declared",
        "narrowing_unconfirmed", "used_undeclared", "used_undeclared_possible", "declared_unused",
    }
    assert codes <= permission_messages.message_codes()
    source = Path(capability_analysis.__file__).read_text(encoding="utf-8") + Path(
        parse_permission_declarations.__code__.co_filename
    ).read_text(encoding="utf-8")
    emitted = set(re.findall(r'PermissionIssue\(\s*"(?:error|warning|info)",\s*"([a-z_]+)"', source))
    emitted |= set(re.findall(r'"(used_undeclared(?:_possible)?|declared_unused)"', source))
    assert emitted <= permission_messages.message_codes()
    for kind in capability_analysis.BLIND_SPOT_TYPES:
        labels = {permission_messages.blind_spot_label(kind, locale) for locale in LOCALES}
        assert kind not in labels


def test_permission_ids_cover_the_legacy_capabilities():
    assert set(capability_analysis.LEGACY_CAPABILITY_PERMISSIONS.values()) <= set(PERMISSION_IDS)
    assert len(PERMISSION_IDS) == len(set(PERMISSION_IDS)) == 9
