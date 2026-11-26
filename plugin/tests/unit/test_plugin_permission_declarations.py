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
    narrowing_issues,
    parse_permission_declarations,
    resolve_text,
)
from plugin.neko_plugin_cli.commands import _permission_messages as permission_messages
from plugin.sdk.shared.permissions import PERMISSION_IDS
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


# An empty [permissions] table: the plugin opted in, so definite undeclared
# use is an error (without the table it is a transition warning).
def _scan(
    tmp_path: Path, source: str, manifest: str = "[permissions]\n", *, developer: bool = False, **files: str
) -> dict:
    """``developer`` adds context_loss, as neko-plugin check asks for."""
    (tmp_path / "main.py").write_text(source, encoding="utf-8")
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    if manifest:
        (tmp_path / "plugin.toml").write_text(manifest, encoding="utf-8")
    return analyze_capabilities(tmp_path, include_context_loss=developer)


def _all_spots(report: dict) -> list[dict]:
    """Counted blind spots, not_analyzed hints and (developer) context_loss."""
    return [*report["blind_spots"], *report["not_analyzed"], *report.get("context_loss", [])]


def _bases(report: dict) -> dict[str, str]:
    return {permission: item["basis"] for permission, item in report["permissions"].items()}


@pytest.mark.parametrize(
    "source,permission",
    [
        ("import socket\nsocket.create_connection(('a', 1))", "system.network:connect"),
        ("from urllib.request import urlopen\nurlopen('https://a')", "system.network:connect"),
        ("import httpx as h\nasync def f():\n    async with h.AsyncClient() as c:\n        pass", "system.network:connect"),
        ("import requests\nrequests.Session().get('x')", "system.network:connect"),
        ("from websockets.asyncio.client import connect\nasync def f():\n    await connect('ws://a')", "system.network:connect"),
        ("from websockets.sync.client import connect\nconnect('ws://a')", "system.network:connect"),
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


LISTEN = "system.network:listen"
CONNECT = "system.network:connect"


@pytest.mark.parametrize(
    "source",
    [
        "import socket\nsocket.create_server(('', 1))",
        "from socket import create_server\ncreate_server(('', 1))",
        "import socket\nsocket.socket().bind(('', 1))\nsocket.socket().listen()",
        "import socket\ns = socket.socket(socket.AF_INET)\ns.listen()",
        "import socket\ns = socket.socket()\nconn, addr = s.accept()",
        "from socket import socket as sock\nserver = sock()\nserver.bind(('', 1))\nserver.listen()",
        "import asyncio\nasync def f(cb):\n    await asyncio.start_server(cb, '', 1)",
        "import asyncio\nasync def f(p):\n    await asyncio.get_running_loop().create_server(p, '', 1)",
        "import asyncio\nloop = asyncio.get_event_loop()\nloop.create_server(object, '', 1)",
        "import asyncio\nasyncio.new_event_loop().create_server(object, '', 1)",
        "from http.server import HTTPServer\nHTTPServer(('127.0.0.1', 1), object)",
        "import http.server\nhttp.server.ThreadingHTTPServer(('', 1), object)",
        "import socketserver\nsocketserver.TCPServer(('', 1), object)",
        "import socketserver\nsocketserver.UDPServer(('', 1), object)",
        "import socketserver as ss\nss.ThreadingTCPServer(('', 1), object)",
        "from socketserver import ThreadingUDPServer\nThreadingUDPServer(('', 1), object)",
        "import socketserver\nsocketserver.ForkingTCPServer(('', 1), object)",
        "import socketserver\nsocketserver.ForkingUDPServer(('', 1), object)",
        "from wsgiref.simple_server import make_server\nmake_server('', 1, object)",
        "from wsgiref import simple_server\nsimple_server.WSGIServer(('', 1), object)",
        "from xmlrpc.server import SimpleXMLRPCServer\nSimpleXMLRPCServer(('', 1))",
        "import xmlrpc.server\nxmlrpc.server.DocXMLRPCServer(('', 1))",
        "import websockets\nasync def f(h):\n    await websockets.serve(h, '', 1)",
        "from websockets.server import serve\nserve(object, '', 1)",
        "from websockets.asyncio.server import serve\nasync def f(h):\n    await serve(h, '', 1)",
        "from websockets.sync.server import serve\nserve(object, '', 1)",
        "from websockets.asyncio.server import Server\nServer(object)",
        "from aiohttp import web\nweb.SockSite(object, object)",
        "from hypercorn.asyncio import serve\nasync def f(app, config):\n    await serve(app, config)",
        "import hypercorn.trio\nhypercorn.trio.serve(object, object)",
        "from aiohttp import web\nweb.run_app(web.Application())",
        "import aiohttp.web\naiohttp.web.TCPSite(object, '', 1)",
        "import uvicorn\nuvicorn.run(object)",
        "import uvicorn\nuvicorn.Server(object)",
        "from flask import Flask\napp = Flask(__name__)\napp.run(port=1)",
    ],
)
def test_servers_are_direct_evidence_of_listen(tmp_path, source):
    report = _scan(tmp_path, source)
    assert report["permissions"][LISTEN]["basis"] == "resolved_call"
    plain_socket = source.startswith(("import socket\n", "from socket import socket")) and "create_server" not in source
    if not plain_socket:
        # Starting a server is not connecting out. (A plain socket's own
        # construction stays connect evidence; see the next test.)
        connect = report["permissions"].get(CONNECT, {"basis": None})
        assert connect["basis"] not in capability_analysis.DIRECT_BASES


@pytest.mark.parametrize(
    "source",
    [
        # A client choosing its local address before connecting.
        "import socket\ns = socket.socket()\ns.bind(('127.0.0.1', 0))\ns.connect(('a', 1))\n",
        # A UDP server: bind without listen.
        "import socket\ns = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\ns.bind(('', 1))\ns.recvfrom(1024)\n",
    ],
)
def test_bind_alone_is_only_possible_listen_evidence(tmp_path, source):
    report = _scan(tmp_path, source)
    assert report["permissions"][LISTEN]["basis"] == "resolved_name"
    manifest = '[plugin]\nid = "x"\n[permissions.ask]\n"system.network:connect" = { reason = "x" }\n'
    issues = compare_declarations(_scan(tmp_path, source, manifest=manifest))
    assert [(issue.severity, issue.code) for issue in issues if issue.params["permission"] == LISTEN] == [
        ("warning", "used_undeclared_possible")
    ]


def test_a_socket_that_binds_reports_listen_there_and_connect_only_where_it_is_made(tmp_path):
    report = _scan(
        tmp_path,
        "import socket\n"
        "s = socket.socket()\n"
        "s.bind(('', 1))\n"
        "s.listen()\n"
        "c = socket.socket()\n"
        "c.connect(('a', 1))\n",
    )
    lines = lambda permission: [row["line"] for row in report["permissions"][permission]["findings"]]
    assert lines(LISTEN) == [3, 4]
    assert lines(CONNECT) == [2, 5, 6]


@pytest.mark.parametrize(
    "source",
    [
        # Plugin-defined names of the same spelling.
        "class HTTPServer:\n    pass\nHTTPServer()",
        "def create_server():\n    pass\ncreate_server()",
        # Clients and unrelated socket use.
        "import socket\nsocket.create_connection(('a', 1))",
        "import asyncio\nasync def f():\n    await asyncio.open_connection('a', 1)",
        "import socket\nsocket.socket().connect(('a', 1))",
        "import socket\nsocket.socket.bind.__name__",
        # An unresolved receiver: not followed (no suffix guess on .bind / .listen).
        "def f(s):\n    s.bind(('', 1))\n    s.listen()",
    ],
)
def test_listen_is_not_reported_for_lookalikes_and_clients(tmp_path, source):
    assert LISTEN not in _scan(tmp_path, source)["permissions"]


def test_server_subclass_and_import_are_weak_listen_evidence(tmp_path):
    named = _scan(tmp_path, "import socketserver\nclass S(socketserver.ThreadingMixIn, socketserver.TCPServer):\n    pass\n")
    assert named["permissions"][LISTEN]["basis"] == "resolved_name"
    for statement in (
        "import http.server",
        "import socketserver",
        "import wsgiref.simple_server",
        "import xmlrpc.server",
        # The imported name is the submodule.
        "from http import server",
        "from wsgiref import simple_server",
        "from xmlrpc import server",
    ):
        imported = _scan(tmp_path, f"{statement}\n")
        assert imported["permissions"][LISTEN]["basis"] == "import_only", statement
        assert CONNECT not in imported["permissions"], statement


def test_submodule_import_hints_apply_to_connect_too(tmp_path):
    for statement in ("from http import client", "from urllib import request"):
        assert _scan(tmp_path, f"{statement}\n")["permissions"][CONNECT]["basis"] == "import_only", statement


@pytest.mark.parametrize(
    "source",
    [
        "from aiohttp import web\nweb.run_app(web.Application())",
        "import aiohttp.web\naiohttp.web.run_app(object)",
        "import websockets\nasync def f(h):\n    await websockets.serve(h, '', 1)",
        "from websockets.asyncio.server import serve\nserve(object, '', 1)",
        "import socket\nsocket.create_server(('', 1))",
    ],
)
def test_a_server_only_import_gives_no_connect_hint(tmp_path, source):
    report = _scan(tmp_path, source)
    assert report["permissions"][LISTEN]["basis"] == "resolved_call"
    assert CONNECT not in report["permissions"]


@pytest.mark.parametrize(
    "source",
    [
        # Also imported for something else: the hint stays.
        "import aiohttp\nfrom aiohttp import web\nimport requests\nweb.run_app(object)",
        # Only weak listen evidence does not explain the import.
        "import socket\nstart = socket.create_server",
        # A listen call on a different module than the one imported.
        "import aiohttp\nimport uvicorn\nuvicorn.run(object)",
    ],
)
def test_connect_hint_stays_unless_every_hinted_module_is_explained(tmp_path, source):
    assert CONNECT in _scan(tmp_path, source)["permissions"]


def test_check_applies_the_same_rules_to_listen(tmp_path):
    server = "from http.server import HTTPServer\nHTTPServer(('127.0.0.1', 1), object)\n"

    def issues(manifest: str, source: str = server):
        return {
            issue.params["permission"]: (issue.severity, issue.code)
            for issue in compare_declarations(_scan(tmp_path, source, manifest=manifest))
        }

    assert issues('[plugin]\nid = "x"\n[permissions.ask]\n"system.env:read" = { reason = "x" }\n')[LISTEN] == (
        "error",
        "used_undeclared",
    )
    # Transition: no [permissions] table yet.
    assert issues('[plugin]\nid = "x"\n')[LISTEN] == ("warning", "used_undeclared_no_table")
    assert issues('[plugin]\nid = "x"\n[permissions.require]\n"system.network:listen" = { reason = "x" }\n') == {}
    assert issues(
        '[plugin]\nid = "x"\n[permissions.ask]\n"system.network:listen" = { reason = "x" }\n', "x = 1\n"
    ) == {LISTEN: ("warning", "declared_unused")}
    weak = issues(
        '[plugin]\nid = "x"\n[permissions]\n',
        "import socketserver\nclass S(socketserver.TCPServer):\n    pass\n",
    )
    assert weak[LISTEN] == ("warning", "used_undeclared_possible")


def test_listen_declaration_parses_and_hosts_do_not_apply(tmp_path):
    declarations, issues = _parse('[permissions.require]\n"system.network:listen" = { reason = "Hook port" }\n')
    assert declarations.level(LISTEN) == "require" and issues == []
    _, issues = _parse('[permissions.ask]\n"system.network:listen" = { reason = "x", hosts = ["a.com"] }\n')
    assert _codes(issues) == [("warning", "hosts_unused")]
    assert narrowing_issues([LISTEN], frozenset({LISTEN}), "@plugin_entry") == []


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
        ("open('a.txt', 'r+')", {"system.fs:read", "system.fs:write"}, "read_write"),
        ("open('a.txt', 'w+b')", {"system.fs:read", "system.fs:write"}, "read_write"),
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
        ("import importlib\nimportlib.import_module(name)", "dynamic_import"),
        ("__import__(name)", "dynamic_import"),
        ("import sys\nsys.modules['os']", "dynamic_import"),
        ("from os import *", "dynamic_import"),
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
        "getattr(obj, 'name')",
        "getattr(obj, 'not-an-identifier', None)",
        "import asyncio\nasyncio.to_thread(g)",
        "def eval(x):\n    return x\neval('1')",
    ],
)
def test_statically_known_forms_are_not_blind_spots(tmp_path, source):
    assert _scan(tmp_path, source)["blind_spots"] == []


def test_unscanned_parts_are_not_analyzed_hints_without_changing_status(tmp_path):
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "lib.py").write_text("import socket", encoding="utf-8")
    report = _scan(tmp_path, "x = 1", **{"ui/panel.tsx": "export default 1", "node_modules/a.js": "1"})
    assert report["status"] == "completed"
    assert report["permissions"] == {}
    # A hint only: not a counted blind spot.
    assert report["blind_spots"] == [] and report["blind_spot_files"] == []
    assert [(spot["type"], spot["reason"], spot["file"]) for spot in report["not_analyzed"]] == [
        ("not_analyzed", "skipped_dir", "node_modules"),
        ("not_analyzed", "non_python", "ui/panel.tsx"),
        ("not_analyzed", "vendor", "vendor"),
    ]


def test_analysis_warnings_become_not_analyzed_hints(tmp_path):
    report = _scan(tmp_path, "x = 1", **{"bad.py": "def broken("})
    assert report["status"] == "partial"
    assert {"type": "not_analyzed", "file": "bad.py", "line": None, "reason": "source_unreadable"} in report["not_analyzed"]
    assert report["blind_spots"] == []
    assert scan_note(report) == "not_analyzed"


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
    # 7: servers moved from system.network:connect to system.network:listen.
    assert capability_analysis.ANALYZER_VERSION >= 7


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
    assert issue.params["locations"] == "main.py:2, main.py:3, main.py:4, +2"
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
        ("partial", [], "not_analyzed"),
        ("partial", [{"type": "native_loader"}], "blind_spots"),
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
    plugin_dir = _plugin(tmp_path, "import os\nKEY = os.environ.get('KEY')\n", "[permissions]\n")
    errors = _issues(plugin_dir, "error")
    [error] = [message for message in errors if "system.env:read" in message]
    assert error.startswith("the code uses system.env:read (worker.py:2)")
    _assert_all_locales(error)


def test_cli_reports_an_undeclared_server_as_listen(tmp_path):
    source = "from http.server import HTTPServer\nserver = HTTPServer(('127.0.0.1', 0), object)\n"
    errors = _issues(_plugin(tmp_path, source, "[permissions]\n"), "error")
    [error] = [message for message in errors if "system.network:listen" in message]
    assert error.startswith("the code uses system.network:listen (worker.py:2)")
    _assert_all_locales(error)
    assert not [message for message in errors if "system.network:connect" in message]
    declared = _plugin(
        tmp_path / "declared",
        source,
        '[permissions.require]\n"system.network:listen" = { reason = "Receive hook calls" }\n',
    )
    assert not _issues(declared, "error")


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
    assert not any(message.startswith("@plugin_entry in worker.py:4: permissions= lists") for message in errors)
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


def _release_ready(plugin_dir: Path) -> Path:
    """Add what check -r requires besides permissions, so only they decide."""
    (plugin_dir / "README.md").write_text("# Demo\n", encoding="utf-8")
    (plugin_dir / "tests").mkdir(exist_ok=True)
    (plugin_dir / "tests" / "test_smoke.py").write_text("def test_ok():\n    pass\n", encoding="utf-8")
    (plugin_dir / ".vscode").mkdir(exist_ok=True)
    (plugin_dir / ".vscode" / "settings.json").write_text("{}\n", encoding="utf-8")
    (plugin_dir / ".vscode" / "tasks.json").write_text("{}\n", encoding="utf-8")
    (plugin_dir / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
    (plugin_dir / ".github" / "workflows" / "verify.yml").write_text("name: verify\n", encoding="utf-8")
    (plugin_dir / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    return plugin_dir


def _release_check(tmp_path: Path, plugin_dir: Path) -> int:
    return neko_plugin_cli.main(
        ["check", "-r", str(plugin_dir), "--skip-tests", "--target-dir", str(tmp_path / "target")]
    )


@pytest.mark.parametrize("declared,expected", [(False, 1), (True, 0)])
def test_release_check_blocks_on_undeclared_use(tmp_path, capsys, declared, expected):
    permissions = (
        '[permissions.require]\n"system.process:spawn" = { reason = "Run a tool" }\n'
        if declared
        else "[permissions]\n"
    )
    plugin_dir = _release_ready(_plugin(tmp_path, "import subprocess\nsubprocess.run(['ls'])\n", permissions))
    exit_code = _release_check(tmp_path, plugin_dir)
    captured = capsys.readouterr()
    assert exit_code == expected, captured.err
    assert ("[ERROR] the code uses system.process:spawn (worker.py:2)" in captured.err) is not declared
    assert "[ERROR]" not in captured.err or not declared


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
        "narrowing_unconfirmed", "used_undeclared", "used_undeclared_possible", "used_undeclared_no_table",
        "declared_unused", "narrowing_unsupported_decorator",
    }
    assert codes <= permission_messages.message_codes()
    source = Path(capability_analysis.__file__).read_text(encoding="utf-8") + Path(
        parse_permission_declarations.__code__.co_filename
    ).read_text(encoding="utf-8")
    emitted = set(re.findall(r'PermissionIssue\(\s*"(?:error|warning|info)",\s*"([a-z_]+)"', source))
    emitted |= set(re.findall(r'"(used_undeclared(?:_possible|_no_table)?|declared_unused)"', source))
    assert emitted <= permission_messages.message_codes()
    for kind in capability_analysis.BLIND_SPOT_TYPES:
        labels = {permission_messages.blind_spot_label(kind, locale) for locale in LOCALES}
        assert kind not in labels


def test_permission_ids_cover_the_legacy_capabilities():
    assert set(capability_analysis.LEGACY_CAPABILITY_PERMISSIONS.values()) <= set(PERMISSION_IDS)
    assert len(PERMISSION_IDS) == len(set(PERMISSION_IDS)) == 10


# --- consent comparison across names ----------------------------------------------


def test_normalize_permission_maps_legacy_names():
    from plugin.sdk.shared.permissions import LEGACY_PERMISSION_NAMES, normalize_permission

    for legacy, permission in LEGACY_PERMISSION_NAMES.items():
        assert normalize_permission(legacy) == permission
        assert normalize_permission(permission) == permission
    assert normalize_permission("camera.read") is None
    assert normalize_permission(None) is None


@pytest.mark.parametrize(
    "reported",
    [
        ["memory.read", "messages.push"],
        ["plugin_sdk.main_agent.memory:read", "plugin_sdk.main_agent.messages:push"],
        ["memory.read", "plugin_sdk.main_agent.messages:push"],
    ],
)
def test_legacy_approvals_cover_reports_in_either_naming(reported):
    from plugin.server.domain.consent_policy import derive_consent

    record = {"decision": "approved", "approved_capabilities": ["memory.read", "messages.push"]}
    view = derive_consent(record, {"status": "completed", "capabilities": reported})
    assert (view.state, view.unconfirmed, view.upgraded) == ("clear", [], False)


def test_a_really_new_permission_still_needs_confirmation():
    from plugin.server.domain.consent_policy import derive_consent

    record = {"decision": "approved", "approved_capabilities": ["memory.read"]}
    report = {"status": "completed", "capabilities": ["plugin_sdk.main_agent.memory:read", "conversations.read"]}
    view = derive_consent(record, report)
    assert (view.state, view.unconfirmed, view.upgraded) == ("confirm", ["conversations.read"], True)


def test_analyzer_upgrade_does_not_ask_approved_plugins_again(tmp_path):
    """The re-scan after the version bump reports the same legacy names."""
    from plugin.server.domain.consent_policy import derive_consent

    (tmp_path / "main.py").write_text(
        "import os\nclass P:\n    def go(self):\n        self.ctx.push_message(os.getenv('A'))\n"
        "        self.ctx.bus.memory.get('x')\n",
        encoding="utf-8",
    )
    report = analyze_capabilities(tmp_path)
    assert report["capabilities"] == ["memory.read", "messages.push"]
    record = {"decision": "approved", "approved_capabilities": ["memory.read", "messages.push"]}
    view = derive_consent(record, report)
    assert (view.state, view.upgraded) == ("clear", False)


# --- review follow-ups ----------------------------------------------------------------


def _errors(report: dict) -> list[str]:
    return [issue.params["permission"] for issue in compare_declarations(report) if issue.severity == "error"]


def test_rebinding_is_checked_against_the_name_as_written(tmp_path):
    report = _scan(tmp_path, "import os as o\nfor o in items:\n    o.system('x')\n")
    assert report["permissions"]["system.process:spawn"]["basis"] == "suffix_match"
    assert _errors(report) == []


@pytest.mark.parametrize(
    "source,direct",
    [
        ("import subprocess\nrunner = getattr(subprocess, 'run')\n", False),
        ("import subprocess\ngetattr(subprocess, 'run')(['ls'])\n", True),
    ],
)
def test_getattr_retrieval_is_not_a_call(tmp_path, source, direct):
    report = _scan(tmp_path, source)
    assert (report["permissions"]["system.process:spawn"]["basis"] == "resolved_call") is direct
    assert bool(_errors(report)) is direct


@pytest.mark.parametrize(
    "replacement",
    ["lambda *args: None", "pick() if flag else other", "handlers['run']"],
)
def test_a_name_reassigned_to_something_unresolvable_is_not_direct_evidence(tmp_path, replacement):
    report = _scan(tmp_path, f"import subprocess\nrunner = subprocess.run\nrunner = {replacement}\nrunner('x')\n")
    assert _errors(report) == []


def test_reassignment_weakening_keeps_legacy_capabilities(tmp_path):
    source = "from plugin.sdk import create_card\nmake = create_card\nmake = lambda: None\nmake()\n"
    before = "from plugin.sdk import create_card\nmake = create_card\nmake()\n"
    assert _scan(tmp_path, source)["capabilities"] == _scan(tmp_path, before)["capabilities"]


@pytest.mark.parametrize(
    "source",
    [
        "from os import environ\nKEY = environ['KEY']\n",
        "from os import environ as env\nKEY = env.get('KEY')\n",
        "from os import environ\ndef f():\n    return dict(environ)\n",
    ],
)
def test_imported_environ_reads_are_direct_evidence(tmp_path, source):
    report = _scan(tmp_path, source)
    assert _errors(report) == ["system.env:read"]
    assert report["capabilities"] == []


def test_a_truncated_tree_walk_is_a_blind_spot(tmp_path, monkeypatch):
    monkeypatch.setattr(capability_analysis, "_MAX_TREE_ENTRIES", 3)
    for index in range(5):
        (tmp_path / f"data_{index}.txt").write_text("x", encoding="utf-8")
    report = _scan(tmp_path, "x = 1\n")
    assert {"type": "not_analyzed", "file": None, "line": None, "reason": "budget_exceeded"} in report["not_analyzed"]
    assert report["blind_spots"] == []
    assert scan_note(report) == "not_analyzed"


def test_source_revision_changes_when_non_python_code_is_added(tmp_path):
    from plugin.server.application.plugins import capability_analysis_service as service

    (tmp_path / "plugin.toml").write_text('[plugin]\nid = "x"\n', encoding="utf-8")
    (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")
    before = service.source_revision(tmp_path, "1")
    (tmp_path / "ui").mkdir()
    (tmp_path / "ui" / "panel.tsx").write_text("export default 1", encoding="utf-8")
    after = service.source_revision(tmp_path, "1")
    assert before != after
    assert service.source_revision(tmp_path, "1") == after



# --- review round 2 ----------------------------------------------------------------------


def test_outer_rebinding_does_not_weaken_a_nested_scopes_own_import(tmp_path):
    report = _scan(tmp_path, "os = None\ndef f():\n    import os\n    os.system('echo x')\n")
    assert _errors(report) == ["system.process:spawn"]


def test_outer_rebinding_still_weakens_nested_scopes_that_inherit_the_name(tmp_path):
    report = _scan(tmp_path, "import os\nfor os in items:\n    pass\ndef f():\n    os.system('echo x')\n")
    assert _errors(report) == []


@pytest.mark.parametrize(
    "source",
    [
        "from os import environ\nfound = 'KEY' in environ\n",
        "from os import environ\nsnapshot = dict(environ)\n",
    ],
)
def test_more_imported_environ_reads(tmp_path, source):
    assert _errors(_scan(tmp_path, source)) == ["system.env:read"]


@pytest.mark.parametrize(
    "source",
    [
        "import os\nos.environ['X'] = '1'\n",
        "import os\ndel os.environ['X']\n",
        "import os\nos.environ.update(X='1')\n",
        "from os import environ\nenviron['X'] = '1'\n",
    ],
)
def test_writing_the_environment_is_not_a_definite_read(tmp_path, source):
    report = _scan(tmp_path, source)
    assert "system.env:read" in report["permissions"]
    assert _errors(report) == []


def test_reading_and_writing_on_one_line_is_still_a_read(tmp_path):
    report = _scan(tmp_path, "import os\nos.environ['X'] = os.environ['Y']\n")
    assert _errors(report) == ["system.env:read"]


def test_duplicate_is_reported_even_when_the_first_entry_is_invalid():
    _declarations, issues = _parse(
        '[permissions.require]\n"system.env:read" = { reason = "" }\n'
        '[permissions.ask]\n"system.env:read" = { reason = "b" }\n'
    )
    assert ("error", "duplicate_permission") in _codes(issues)


def test_vendor_sync_work_directories_are_not_reported(tmp_path):
    (tmp_path / ".vendor.staging-0a1b2c3d").mkdir()
    (tmp_path / ".vendor.staging-0a1b2c3d" / "x.js").write_text("1", encoding="utf-8")
    assert _scan(tmp_path, "x = 1\n")["blind_spots"] == []


def test_cli_accepts_a_declared_narrowing_on_a_component(tmp_path):
    plugin_dir = _plugin(
        tmp_path,
        "from plugin.sdk.plugin import plugin_entry\n"
        "class P:\n"
        "    @plugin_entry(id='a', permissions=['system.network:connect'])\n"
        "    async def a(self): ...\n",
        '[permissions.ask]\n"system.network:connect" = { reason = "x" }\n',
    )
    assert not [message for message in _issues(plugin_dir) if "permissions=" in message]


def test_cli_permissions_on_a_non_component_decorator_is_an_error_in_every_locale(tmp_path):
    plugin_dir = _plugin(
        tmp_path,
        "from plugin.sdk.plugin import lifecycle\n"
        "class P:\n"
        "    @lifecycle(id='startup', permissions=['system.network:connect'])\n"
        "    async def a(self): ...\n",
        '[permissions.ask]\n"system.network:connect" = { reason = "x" }\n',
    )
    [error] = [message for message in _issues(plugin_dir, "error") if "does not take permissions=" in message]
    assert error.startswith("@lifecycle in worker.py:4:")
    _assert_all_locales(error)



# --- transition for plugins without a [permissions] table --------------------------------


def test_definite_undeclared_use_is_an_error_once_the_table_exists():
    used = {"system.env:read": {"declared": None, "findings": [_finding("resolved_call")], "basis": "resolved_call"}}
    assert _codes(compare_declarations(_report(used, declarations="declared"))) == [("error", "used_undeclared")]


def test_definite_undeclared_use_is_a_warning_without_a_table():
    used = {"system.env:read": {"declared": None, "findings": [_finding("resolved_call")], "basis": "resolved_call"}}
    assert _codes(compare_declarations(_report(used, declarations="none"))) == [("warning", "used_undeclared_no_table")]


def test_release_check_passes_validation_without_a_table_and_says_it_will_become_an_error(tmp_path):
    plugin_dir = _plugin(tmp_path, "import subprocess\nsubprocess.run(['ls'])\n")
    messages = validate_plugin_dir(plugin_dir)
    assert not [message for level, message in messages if level == "error"]
    [warning] = [message for level, message in messages if level == "warning" and "no [permissions] table" in message]
    assert "will become an error" in warning
    _assert_all_locales(warning)


@pytest.mark.parametrize(
    "source",
    [
        "import os\nos.environ['PATH'] += ':extra'\n",
        "from os import environ\nenviron['PATH'] += ':extra'\n",
    ],
)
def test_augmented_assignment_to_the_environment_is_a_read(tmp_path, source):
    assert _errors(_scan(tmp_path, source)) == ["system.env:read"]


# --- review round 3 ----------------------------------------------------------------------


def test_optional_import_fallback_keeps_direct_evidence(tmp_path):
    source = (
        "try:\n    import requests\nexcept ImportError:\n    requests = None\n"
        "def fetch(u):\n    return requests.get(u)\n"
    )
    assert _errors(_scan(tmp_path, source)) == ["system.network:connect"]


def test_comprehension_targets_do_not_leak_into_the_enclosing_scope(tmp_path):
    source = "import subprocess\nITEMS = [subprocess for subprocess in range(3)]\ndef f():\n    subprocess.run(['ls'])\n"
    assert _errors(_scan(tmp_path, source)) == ["system.process:spawn"]


def test_aliases_of_rebound_names_are_uncertain(tmp_path):
    source = "import subprocess\nfor subprocess in handlers:\n    run = subprocess.run\n    run('x')\n"
    assert _errors(_scan(tmp_path, source)) == []
    plain = "import subprocess\nrun = subprocess.run\nrun('x')\n"
    assert _errors(_scan(tmp_path, plain)) == ["system.process:spawn"]


def test_a_plugin_defined_getattr_is_not_the_builtin(tmp_path):
    source = "import subprocess\ndef getattr(obj, name):\n    return lambda *a: None\ngetattr(subprocess, 'run')([])\n"
    assert _errors(_scan(tmp_path, source)) == []


def test_open_with_unpacked_arguments_has_an_unknown_mode(tmp_path):
    report = _scan(tmp_path, "open('out.txt', **{'mode': 'w'})\n")
    assert set(report["permissions"]) == {"system.fs:read", "system.fs:write"}
    assert report["permissions"]["system.fs:write"]["findings"][0]["mode"] == "unknown"


def test_scan_note_does_not_claim_no_extra_usage_next_to_undeclared_use(tmp_path):
    report = _scan(tmp_path, "import os\nos.getenv('KEY')\n")
    assert scan_note(report) == "completed"
    declared = _scan(tmp_path, "import os\nos.getenv('KEY')\n", manifest='[permissions.ask]\n"system.env:read" = { reason = "x" }\n')
    assert scan_note(declared) == "no_extra_usage"


@pytest.mark.parametrize("call", ["os.environ.pop('X', None)", "os.environ.setdefault('X', '1')"])
def test_methods_that_return_environment_values_are_reads(tmp_path, call):
    assert _errors(_scan(tmp_path, f"import os\nsend({call})\n")) == ["system.env:read"]


@pytest.mark.parametrize("read", ["os.environ.get('A')", "os.getenv('A')"])
def test_uses_of_a_stored_call_result_do_not_repeat_the_evidence(tmp_path, read):
    report = _scan(tmp_path, f"import os\ntok = {read}\nx = tok.strip()\ny = tok.lower()\n")
    assert [row["line"] for row in report["permissions"]["system.env:read"]["findings"]] == [2]


def test_directories_count_toward_the_tree_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(capability_analysis, "_MAX_TREE_ENTRIES", 3)
    for index in range(5):
        (tmp_path / f"dir_{index}").mkdir()
    assert capability_analysis.skipped_inventory(tmp_path)["truncated"] is True


def test_cli_only_checks_permissions_on_sdk_decorators(tmp_path):
    plugin_dir = _plugin(
        tmp_path,
        "def hook(**kwargs):\n    return lambda f: f\n"
        "class P:\n"
        "    @hook(permissions=['local-cache'])\n"
        "    def a(self): ...\n",
        "[permissions]\n",
    )
    assert not [message for message in _issues(plugin_dir) if "permissions=" in message]


def test_cli_narrowing_outside_the_declarations_is_an_error(tmp_path):
    plugin_dir = _plugin(
        tmp_path,
        "from plugin.sdk.plugin import plugin_entry\n"
        "class P:\n"
        "    @plugin_entry(id='a', permissions=['system.env:read'])\n"
        "    async def a(self): ...\n",
        '[permissions.ask]\n"system.network:connect" = { reason = "x" }\n',
    )
    errors = _issues(plugin_dir, "error")
    assert any(message.startswith("@plugin_entry in worker.py:4: permissions= lists") for message in errors)


@pytest.mark.parametrize(
    "source,permission",
    [
        ("import pathlib\np = pathlib.Path('x')\np.write_text('hi')\n", "system.fs:write"),
        ("import requests\ns = requests.Session()\ns.get('u')\n", "system.network:connect"),
    ],
)
def test_methods_on_stored_objects_still_count(tmp_path, source, permission):
    report = _scan(tmp_path, source)
    assert 3 in [row["line"] for row in report["permissions"][permission]["findings"]]


# --- review round 4 ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "import subprocess\ncallbacks = [lambda: subprocess.run('x') for subprocess in handlers]\n",
        "import os\ncallbacks = [lambda: os.environ for os in handlers]\n",
    ],
)
def test_lambdas_inside_a_comprehension_see_its_names(tmp_path, source):
    assert _errors(_scan(tmp_path, source)) == []


def test_the_first_comprehension_iterable_is_evaluated_outside(tmp_path):
    assert _errors(_scan(tmp_path, "import os\nkeys = [os for os in os.environ]\n")) == ["system.env:read"]


@pytest.mark.parametrize(
    "imports,decorator",
    [
        ("from plugin.sdk.plugin import plugin_entry as entry\n", "entry"),
        ("import plugin.sdk.plugin as sdk\n", "sdk.plugin_entry"),
        ("from plugin.sdk import plugin as sdk\n", "sdk.plugin_entry"),
    ],
)
def test_cli_follows_sdk_import_aliases_for_decorators(tmp_path, imports, decorator):
    plugin_dir = _plugin(
        tmp_path,
        imports + f"class P:\n    @{decorator}(id='a', permissions=['system.env:read'])\n    async def a(self): ...\n",
        "[permissions]\n",
    )
    errors = _issues(plugin_dir, "error")
    assert any(message.startswith("@plugin_entry in worker.py:4: permissions= lists") for message in errors)


def test_cli_ignores_plugin_owned_helpers_with_sdk_names(tmp_path):
    plugin_dir = _plugin(
        tmp_path,
        "from plugin.plugins.perm_demo.helpers import hook\n"
        "class P:\n    @hook(permissions=['local-cache'])\n    def a(self): ...\n",
        "[permissions]\n",
    )
    assert not [message for message in _issues(plugin_dir) if "permissions=" in message]


# --- review round 5 ----------------------------------------------------------------------


def test_a_bare_annotation_is_not_a_rebinding(tmp_path):
    assert _errors(_scan(tmp_path, "import requests\nrequests: object\nrequests.get('u')\n")) == [
        "system.network:connect"
    ]


def test_plugin_owned_modules_are_not_sdk_evidence(tmp_path):
    report = _scan(tmp_path, "from plugin.plugins.demo.helpers import create_card\ncreate_card()\n")
    assert report["permissions"]["plugin_sdk.main_agent.messages:push"]["basis"] == "suffix_match"
    assert _errors(report) == []


def test_an_invalid_declaration_is_not_also_reported_as_undeclared(tmp_path):
    report = _scan(
        tmp_path,
        "import os\nos.getenv('A')\n",
        manifest='[permissions.ask]\n"system.env:read" = { reason = "" }\n',
    )
    assert report["invalid_declarations"] == ["system.env:read"]
    assert [issue.code for issue in compare_declarations(report)] == []


def test_unreadable_directories_mark_the_inventory_incomplete(tmp_path, monkeypatch):
    import os as os_module

    real_walk = os_module.walk

    def walk(top, onerror=None, followlinks=False):
        if onerror is not None:
            onerror(PermissionError("denied"))
        yield from real_walk(top, followlinks=followlinks)

    monkeypatch.setattr(capability_analysis.os, "walk", walk)
    inventory = capability_analysis.skipped_inventory(tmp_path)
    assert (inventory["unreadable"], inventory["truncated"]) == (True, False)


def test_not_analyzed_survives_the_blind_spot_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(capability_analysis, "MAX_BLIND_SPOTS", 2)
    report = _scan(tmp_path, "\n".join(f"eval(x{i})" for i in range(5)) + "\n", **{"ui/panel.tsx": "1"})
    assert len(report["blind_spots"]) == 2
    assert any(spot["type"] == "not_analyzed" for spot in report["not_analyzed"])


def test_cli_only_counts_module_level_sdk_imports_for_decorators(tmp_path):
    plugin_dir = _plugin(
        tmp_path,
        "def hook(**kwargs):\n    return lambda f: f\n"
        "def setup():\n    from plugin.sdk.plugin import hook\n    return hook\n"
        "class P:\n    @hook(permissions=['local-cache'])\n    def a(self): ...\n",
        "[permissions]\n",
    )
    assert not [message for message in _issues(plugin_dir) if "permissions=" in message]


# --- review round 6: decorators resolve in their own scope ---------------------------------


@pytest.mark.parametrize(
    "source,line",
    [
        # SDK import in the class body, used by a method decorator there.
        ("class P:\n    from plugin.sdk.plugin import hook\n    @hook(permissions=['system.env:read'])\n    def a(self): ...\n", 4),
        # Function-local SDK import with an alias.
        (
            "def register():\n    from plugin.sdk.plugin import plugin_entry as entry\n"
            "    @entry(id='a', permissions=['system.env:read'])\n    def a(): ...\n    return a\n",
            4,
        ),
    ],
)
def test_cli_resolves_sdk_decorators_in_their_scope(tmp_path, source, line):
    plugin_dir = _plugin(tmp_path, source, "[permissions]\n")
    errors = _issues(plugin_dir, "error")
    assert any(f"worker.py:{line}:" in message and "permissions= lists" in message for message in errors)


def test_cli_local_helper_shadows_a_module_level_sdk_import(tmp_path):
    plugin_dir = _plugin(
        tmp_path,
        "from plugin.sdk.plugin import hook\n"
        "def build():\n    from .helpers import hook\n    @hook(permissions=['local-cache'])\n    def a(): ...\n    return a\n",
        "[permissions]\n",
    )
    assert not [message for message in _issues(plugin_dir) if "permissions=" in message]


def test_cli_class_body_imports_are_not_visible_in_methods(tmp_path):
    plugin_dir = _plugin(
        tmp_path,
        "class P:\n    from plugin.sdk.plugin import hook\n"
        "    def make(self):\n        @hook(permissions=['system.env:read'])\n        def a(): ...\n        return a\n",
        "[permissions]\n",
    )
    assert not [message for message in _issues(plugin_dir) if "permissions=" in message]


# --- review round 7 ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source,reported",
    [
        # Later SDK import wins over an earlier local definition.
        ("def hook(**k):\n    return lambda f: f\nfrom plugin.sdk.plugin import hook\n"
         "class P:\n    @hook(permissions=['system.env:read'])\n    def a(self): ...\n", True),
        # Later local definition wins over an earlier SDK import.
        ("from plugin.sdk.plugin import hook\ndef hook(**k):\n    return lambda f: f\n"
         "class P:\n    @hook(permissions=['system.env:read'])\n    def a(self): ...\n", False),
        # A parameter shadows an enclosing SDK import.
        ("def register():\n    from plugin.sdk.plugin import hook\n"
         "    def build(hook):\n        @hook(permissions=['x'])\n        def a(): ...\n        return a\n    return build\n", False),
        # Loop and with targets shadow too.
        ("from plugin.sdk.plugin import hook\nfor hook in makers:\n"
         "    @hook(permissions=['x'])\n    def a(): ...\n", False),
    ],
)
def test_cli_decorator_binding_order_and_shadowing(tmp_path, source, reported):
    plugin_dir = _plugin(tmp_path, source, "[permissions]\n")
    messages = [message for message in _issues(plugin_dir) if "permissions=" in message]
    assert bool(messages) is reported


@pytest.mark.parametrize(
    "source,blind",
    [
        ("import sys\nloaded = 'torch' in sys.modules\n", False),
        ("import sys\nmod = sys.modules['os']\n", True),
        ("import sys\nmod = sys.modules.get('os')\n", True),
    ],
)
def test_sys_modules_lookups_are_blind_spots_membership_tests_are_not(tmp_path, source, blind):
    spots = _scan(tmp_path, source)["blind_spots"]
    assert any(spot["type"] == "dynamic_import" for spot in spots) is blind


def test_dict_reflection_only_counts_sdk_origins(tmp_path):
    source = "from plugin.plugins.demo.helpers import state\nx = state.__dict__\n"
    assert _scan(tmp_path, source)["blind_spots"] == []


# --- review round 8 ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "import os, builtins\nbuiltins.getattr(os, 'system')('echo hi')\n",
        "import os\nfrom builtins import getattr as g\ng(os, 'system')('echo hi')\n",
    ],
)
def test_qualified_or_aliased_getattr_stays_a_blind_spot(tmp_path, source):
    spots = _scan(tmp_path, source)["blind_spots"]
    assert any(spot["type"] == "reflective_access" for spot in spots)


def test_nested_vendor_directories_are_not_analyzed(tmp_path):
    report = _scan(tmp_path, "x = 1\n", **{"helpers/vendor/network.py": "import socket\n"})
    assert {"type": "not_analyzed", "file": "helpers/vendor", "line": None, "reason": "vendor"} in report["not_analyzed"]


@pytest.mark.parametrize(
    "source,blind",
    [
        ("import sys\nmods = sys.modules.copy()\n", True),
        ("import sys\nfor m in sys.modules.values():\n    pass\n", True),
        ("from sys import modules\nmod = modules['os']\n", True),
        ("import sys\nmodules = sys.modules\nmod = modules['os']\n", True),
        ("import sys\nok = 'torch' not in sys.modules\n", False),
    ],
)
def test_sys_modules_access_beyond_membership_is_a_blind_spot(tmp_path, source, blind):
    spots = _scan(tmp_path, source)["blind_spots"]
    assert any(spot["type"] == "dynamic_import" for spot in spots) is blind


@pytest.mark.parametrize(
    "source,reported",
    [
        # Subscript / attribute assignment does not rebind the decorator name.
        ("from plugin.sdk.plugin import hook\nregistry = {}\nregistry[hook] = 1\nhook.note = 1\n"
         "class P:\n    @hook(permissions=['system.env:read'])\n    def a(self): ...\n", True),
        # A class body only sees what is bound before the class statement.
        ("def hook(**k):\n    return lambda f: f\n"
         "class P:\n    @hook(permissions=['x'])\n    def a(self): ...\n"
         "from plugin.sdk.plugin import hook\n", False),
        # Methods run later and see the module's final bindings.
        ("def hook(**k):\n    return lambda f: f\n"
         "class P:\n    def make(self):\n        @hook(permissions=['system.env:read'])\n        def a(): ...\n        return a\n"
         "from plugin.sdk.plugin import hook\n", True),
    ],
)
def test_cli_decorator_targets_and_class_snapshots(tmp_path, source, reported):
    plugin_dir = _plugin(tmp_path, source, "[permissions]\n")
    messages = [message for message in _issues(plugin_dir) if "permissions=" in message]
    assert bool(messages) is reported


# --- review round 9 ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "import os\nfrom builtins import getattr\ngetattr(os, 'system')('echo hi')\n",
        "import os, builtins\ngetattr = builtins.getattr\ngetattr(os, 'system')('echo hi')\n",
    ],
)
def test_rebound_getattr_name_stays_a_blind_spot(tmp_path, source):
    spots = _scan(tmp_path, source)["blind_spots"]
    assert any(spot["type"] == "reflective_access" for spot in spots)


def test_cli_plain_plugin_imports_keep_the_sdk_path(tmp_path):
    plugin_dir = _plugin(
        tmp_path,
        "import plugin.sdk.plugin\nimport plugin.plugins.perm_demo.helpers\n"
        "class P:\n    @plugin.sdk.plugin.hook(permissions=['system.env:read'])\n    def a(self): ...\n",
        "[permissions]\n",
    )
    assert any("permissions= lists" in message for message in _issues(plugin_dir, "error"))


def test_cli_nested_class_body_uses_the_definition_time_snapshot(tmp_path):
    plugin_dir = _plugin(
        tmp_path,
        "def hook(**k):\n    return lambda f: f\n"
        "class Outer:\n    class Inner:\n        @hook(permissions=['x'])\n        def a(self): ...\n"
        "from plugin.sdk.plugin import hook\n",
        "[permissions]\n",
    )
    assert not [message for message in _issues(plugin_dir) if "permissions=" in message]


# --- review round 10 -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "source,kind",
    [
        ("execute = eval\nexecute(code)\n", "code_exec"),
        ("load = __import__\nload(name)\n", "dynamic_import"),
        ("import os\ng = getattr\ng(os, 'system')('x')\n", "reflective_access"),
    ],
)
def test_aliases_of_builtins_keep_their_blind_spots(tmp_path, source, kind):
    spots = _scan(tmp_path, source)["blind_spots"]
    assert any(spot["type"] == kind for spot in spots)


def test_aliases_of_plugin_defined_names_are_not_builtins(tmp_path):
    source = "def eval(x):\n    return x\nexecute = eval\nexecute('1')\n"
    assert _scan(tmp_path, source)["blind_spots"] == []


# --- review round 11 -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "def f(open):\n    reader = open\n    reader('x')\n",
        "def f(eval):\n    e = eval\n    e('1')\n",
        "for eval in fs:\n    e = eval\n    e('1')\n",
    ],
)
def test_aliases_of_local_names_with_builtin_spelling_are_not_builtins(tmp_path, source):
    report = _scan(tmp_path, source)
    assert report["permissions"] == {}
    assert report["blind_spots"] == []


# --- review round 12 -----------------------------------------------------------------------


def test_cli_ignores_files_the_package_build_excludes(tmp_path):
    plugin_dir = _plugin(tmp_path, "x = 1\n", "[permissions]\n")
    (plugin_dir / "build").mkdir()
    (plugin_dir / "build" / "helper.py").write_text("import subprocess\nsubprocess.run(['ls'])\n", encoding="utf-8")
    assert not [message for message in _issues(plugin_dir, "error") if "system.process:spawn" in message]


def test_cli_still_checks_packaged_files(tmp_path):
    plugin_dir = _plugin(tmp_path, "import subprocess\nsubprocess.run(['ls'])\n", "[permissions]\n")
    assert [message for message in _issues(plugin_dir, "error") if "system.process:spawn" in message]


def test_alias_chains_of_shadowed_builtins_are_not_builtins(tmp_path):
    report = _scan(tmp_path, "def f(eval):\n    a = eval\n    b = a\n    b('1')\n")
    assert report["blind_spots"] == []


def test_taking_a_mutating_environment_method_is_not_a_read(tmp_path):
    report = _scan(tmp_path, "import os\nset_env = os.environ.update\nset_env(X='1')\n")
    assert _errors(report) == []


def test_an_entry_with_any_error_is_invalid():
    declarations, issues = _parse('[permissions.ask]\n"system.network:connect" = { reason = "x", hosts = [""] }\n')
    assert ("error", "hosts_type") in _codes(issues)
    assert declarations.ids() == frozenset()
    assert declarations.invalid == {"system.network:connect"}


def test_excluded_files_do_not_use_up_the_scan_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(capability_analysis, "MAX_FILES", 2)
    plugin_dir = _plugin(tmp_path, "import subprocess\nsubprocess.run(['ls'])\n", "[permissions]\n")
    (plugin_dir / "build").mkdir()
    for index in range(5):
        (plugin_dir / "build" / f"a{index}.py").write_text("x = 1\n", encoding="utf-8")
    errors = _issues(plugin_dir, "error")
    assert [message for message in errors if "system.process:spawn" in message]


def test_test_named_files_that_ship_are_not_analyzed_blind_spots(tmp_path):
    # The scan leaves test-named files out, but the package still ships them.
    report = _scan(
        tmp_path,
        "from .net_test import fetch\n",
        "[permissions.require]\n",
        **{
            "net_test.py": "import requests\ndef fetch():\n    return requests.get('https://example.com')\n",
            "tests/helper.py": "import subprocess\nsubprocess.run(['ls'])\n",
            "vendor/test_lib.py": "x = 1\n",
        },
    )
    assert report["status"] == "completed"
    assert report["permissions"] == {}
    assert [(spot["reason"], spot["file"]) for spot in report["not_analyzed"]] == [
        ("test_source", "net_test.py"),
        ("test_source", "tests/helper.py"),
        ("vendor", "vendor"),
    ]
    assert report["blind_spots"] == []
    assert scan_note(report) == "not_analyzed"
    assert report["capabilities"] == []


def test_cli_lists_shipped_test_files_but_not_excluded_ones(tmp_path):
    plugin_dir = _plugin(tmp_path, "x = 1\n", "[permissions.require]\n")
    (plugin_dir / "net_test.py").write_text("import requests\n", encoding="utf-8")
    for folder in ("build", "dist"):
        (plugin_dir / folder).mkdir()
        (plugin_dir / folder / "test_a.py").write_text("x = 1\n", encoding="utf-8")
        (plugin_dir / folder / "app.js").write_text("1\n", encoding="utf-8")
        (plugin_dir / folder / "vendor").mkdir()
    info = _issues(plugin_dir, "info")
    blind = [message for message in info if "(not scanned" in message.splitlines()[0]]
    assert [message.split(":", 1)[0] for message in blind] == ["net_test.py"]
    assert "test_source" in blind[0].splitlines()[0]
    assert not [message for message in info if "build/" in message or "dist/" in message]
    # A vendor/ directory that ships is still listed.
    (plugin_dir / "vendor").mkdir()
    blind = [message for message in _issues(plugin_dir, "info") if "(not scanned" in message.splitlines()[0]]
    assert [message.split(": ", 1)[0] for message in blind] == ["net_test.py, vendor"]
    assert not any(message.startswith("the code scan found no use beyond") for message in info)


def test_unused_declarations_are_only_info_when_part_of_the_plugin_was_not_read():
    declared = {"system.env:read": {"declared": "ask", "findings": [], "basis": "declared_only"}}
    [issue] = compare_declarations(_report(declared, status="partial"))
    assert (issue.severity, issue.code) == ("info", "declared_unused")
    unread = {"type": "not_analyzed", "reason": "test_source", "file": "a_test.py", "line": None}
    [issue] = compare_declarations({**_report(declared), "not_analyzed": [unread]})
    assert issue.severity == "info"
    [issue] = compare_declarations(_report(declared, blind_spots=[{"type": "code_exec"}]))
    assert issue.severity == "warning"


def test_summary_location_matches_the_reported_basis():
    findings = [_finding("suffix_match", 1), _finding("resolved_call", 7)]
    report = _report({"system.env:read": {"declared": None, "findings": findings, "basis": "resolved_call"}})
    [row] = summarize_permissions(report)["rows"]
    assert (row["basis"], row["line"]) == ("resolved_call", 7)


def test_cli_checks_permissions_by_decorator_kind(tmp_path):
    plugin_dir = _plugin(
        tmp_path,
        "from plugin.sdk.plugin import lifecycle, before_entry\n"
        "class P:\n"
        "    @lifecycle(id='startup', permissions=['system.env:read'])\n"
        "    async def a(self): ...\n"
        "    @before_entry(target='x', permissions=['system.env:read'])\n"
        "    async def b(self): ...\n",
        "[permissions.require]\n",
    )
    errors = _issues(plugin_dir, "error")
    # Not a component: the keyword itself is the error, no subset check.
    assert any(message.startswith("@lifecycle in worker.py:4: this decorator does not take permissions=") for message in errors)
    assert not any(message.startswith("@lifecycle in worker.py:4: permissions= lists") for message in errors)
    # An entry hook alias is a component: subset check only.
    assert any(message.startswith("@before_entry in worker.py:6: permissions= lists") for message in errors)
    assert not any("@before_entry" in message and "does not take" in message for message in errors)


@pytest.mark.parametrize(
    "source,permission",
    [
        ("import multiprocessing\nmultiprocessing.Pool(2).map(print, [1])", "system.process:spawn"),
        ("from multiprocessing.pool import Pool\nPool(2)", "system.process:spawn"),
        ("import multiprocessing as mp\nmp.get_context('spawn').Process(target=print).start()", "system.process:spawn"),
        ("import multiprocessing as mp\nctx = mp.get_context('spawn')\nctx.Pool(2)", "system.process:spawn"),
        ("import multiprocessing\nmultiprocessing.Manager()", "system.process:spawn"),
        ("import multiprocessing as mp\nmp.get_context('spawn').Manager()", "system.process:spawn"),
        ("import multiprocessing as mp\nctx = mp.get_context('spawn')\nctx.Manager()", "system.process:spawn"),
        ("from multiprocessing.managers import SyncManager\nSyncManager().start()", "system.process:spawn"),
        ("from concurrent.futures import ProcessPoolExecutor\nProcessPoolExecutor().submit(print)", "system.process:spawn"),
        ("import os\nos.fork()", "system.process:spawn"),
        ("import os\nos.forkpty()", "system.process:spawn"),
        ("from urllib.request import urlretrieve\nurlretrieve('https://a', 'f')", "system.network:connect"),
    ],
)
def test_process_pools_forks_and_downloads_are_direct_evidence(tmp_path, source, permission):
    report = _scan(tmp_path, source, '[permissions.require]\n"system.env:read" = { reason = "x" }\n')
    assert _bases(report)[permission] == "resolved_call"
    assert any(issue.code == "used_undeclared" and issue.params["permission"] == permission
               for issue in compare_declarations(report))
    assert scan_note(report) != "no_extra_usage"


@pytest.mark.parametrize(
    "call",
    [
        "loop.run_in_executor(None, g)",
        "loop.run_in_executor(executor=None, func=g)",
        "asyncio.get_running_loop().run_in_executor(None, g, 1)",
        "loop.run_in_executor(None, *rest)",
    ],
)
def test_default_executor_keeps_the_context(tmp_path, call):
    # The plugin runtime's default executor copies the submitting context.
    report = _scan(tmp_path, f"import asyncio\nasync def f(loop):\n    await {call}")
    assert report["status"] == "completed"
    assert report["blind_spots"] == []


@pytest.mark.parametrize(
    "source",
    [
        "async def f(loop, pool):\n    await loop.run_in_executor(pool, g)",
        "async def f(loop, pool):\n    await loop.run_in_executor(executor=pool, func=g)",
        "async def f(loop, args):\n    await loop.run_in_executor(*args)",
        "async def f(loop, opts):\n    await loop.run_in_executor(**opts)",
        "async def f(loop):\n    await loop.run_in_executor(False, g)",
        (
            "from concurrent.futures import ThreadPoolExecutor\n"
            "async def f(loop):\n    await loop.run_in_executor(ThreadPoolExecutor(), g)"
        ),
        (
            "from concurrent.futures import ThreadPoolExecutor\n"
            "with ThreadPoolExecutor() as pool:\n    pool.submit(g)"
        ),
        "import threading\nthreading.Thread(target=g).start()",
        # A loop the plugin makes itself, or a replaced default executor.
        "import asyncio\nloop = asyncio.new_event_loop()",
        "from asyncio import new_event_loop\nloop = new_event_loop()",
        "from asyncio.runners import Runner\nwith Runner() as runner:\n    runner.run(main())",
        "import asyncio\nwith asyncio.Runner() as runner:\n    runner.run(main())",
        "async def f(loop, pool):\n    loop.set_default_executor(pool)",
    ],
)
def test_other_executors_and_threads_still_lose_the_context(tmp_path, source):
    report = _scan(tmp_path, source, developer=True)
    assert report["status"] == "completed"
    assert report["context_loss"]
    assert {spot["type"] for spot in report["context_loss"]} == {"context_loss"}
    # Developer-only: never a counted blind spot.
    assert report["blind_spots"] == []


def test_process_pool_executor_is_still_a_context_loss_blind_spot(tmp_path):
    report = _scan(tmp_path, "from concurrent.futures import ProcessPoolExecutor\nProcessPoolExecutor()", developer=True)
    assert [spot["type"] for spot in report["context_loss"]] == ["context_loss"]


def test_importing_multiprocessing_alone_is_a_weak_hint(tmp_path):
    report = _scan(tmp_path, "import multiprocessing\nN = multiprocessing.cpu_count()")
    assert _bases(report) == {"system.process:spawn": "import_only"}


def test_choosing_a_multiprocessing_context_alone_is_not_direct_evidence(tmp_path):
    report = _scan(
        tmp_path,
        "import multiprocessing as mp\nctx = mp.get_context('spawn')\nlock = ctx.Lock()\n",
        '[permissions.require]\n"system.env:read" = { reason = "x" }\n',
    )
    assert _bases(report)["system.process:spawn"] == "import_only"
    assert not [issue for issue in compare_declarations(report) if issue.severity == "error"]


def test_urlretrieve_is_network_and_file_write_evidence(tmp_path):
    report = _scan(tmp_path, "from urllib.request import urlretrieve\nurlretrieve('https://a', 'f')")
    assert _bases(report) == {"system.network:connect": "resolved_call", "system.fs:write": "resolved_call"}


def test_urlretrieve_with_only_network_declared_warns_about_the_file_write(tmp_path):
    report = _scan(
        tmp_path,
        "from urllib.request import urlretrieve\nurlretrieve('https://a', 'f')",
        '[permissions.require]\n"system.network:connect" = { reason = "x" }\n',
    )
    found = {(issue.code, issue.params["permission"]) for issue in compare_declarations(report)}
    assert found == {("used_undeclared_possible", "system.fs:write")}


def test_reading_metadata_off_a_function_is_not_using_it(tmp_path):
    report = _scan(tmp_path, "import subprocess\nNAME = subprocess.run.__name__.lower()\n")
    # Naming the function stays a weak hint; it is not a call of it.
    assert _bases(report) == {"system.process:spawn": "resolved_name"}
    assert not [issue for issue in compare_declarations(report) if issue.severity == "error"]


def test_unused_declarations_are_reported_when_the_only_python_file_is_unscanned(tmp_path):
    (tmp_path / "net_test.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "plugin.toml").write_text('[permissions.ask]\n"system.env:read" = { reason = "x" }\n', encoding="utf-8")
    report = analyze_capabilities(tmp_path)
    assert report["status"] == "unavailable"
    [issue] = compare_declarations(report)
    assert (issue.severity, issue.code) == ("info", "declared_unused")
    no_files = _report({"system.env:read": {"declared": "ask", "findings": [], "basis": "declared_only"}}, status="unavailable")
    assert compare_declarations(no_files) == []


def test_excluded_subtrees_do_not_use_up_the_unscanned_list_bound(tmp_path, monkeypatch):
    monkeypatch.setattr(capability_analysis, "_MAX_TREE_ENTRIES", 10)
    plugin_dir = _plugin(tmp_path, "x = 1\n", "[permissions.require]\n")
    (plugin_dir / "build").mkdir()
    for index in range(20):
        (plugin_dir / "build" / f"a{index}.js").write_text("1\n", encoding="utf-8")
    (plugin_dir / "ui").mkdir()
    (plugin_dir / "ui" / "panel.js").write_text("1\n", encoding="utf-8")
    info = _issues(plugin_dir, "info")
    blind = [message for message in info if "(not scanned" in message.splitlines()[0]]
    assert [message.split(": ", 1)[0] for message in blind] == ["ui/panel.js"]


def test_cli_rejects_permissions_on_sdk_class_decorators(tmp_path):
    plugin_dir = _plugin(tmp_path, "x = 1\n", "[permissions.require]\n")
    (plugin_dir / "__init__.py").write_text(
        "from plugin.sdk.plugin import neko_plugin\n\n@neko_plugin(permissions=[])\nclass DemoPlugin: pass\n",
        encoding="utf-8",
    )
    errors = _issues(plugin_dir, "error")
    assert any(message.startswith("@neko_plugin in __init__.py:4: this decorator does not take permissions=") for message in errors)


def test_type_checking_imports_do_not_bind_decorators(tmp_path):
    plugin_dir = _plugin(
        tmp_path,
        "from typing import TYPE_CHECKING\n"
        "from .local import hook\n"
        "if TYPE_CHECKING:\n"
        "    from plugin.sdk.plugin import hook\n"
        "else:\n"
        "    pass\n"
        "class P:\n"
        "    @hook(permissions=['system.env:read'])\n"
        "    async def a(self): ...\n",
        "[permissions.require]\n",
    )
    assert not [message for message in _issues(plugin_dir, "error") if "permissions=" in message]
    (plugin_dir / "worker.py").write_text(
        (plugin_dir / "worker.py").read_text(encoding="utf-8").replace("if TYPE_CHECKING:", "if not TYPE_CHECKING:"),
        encoding="utf-8",
    )
    assert [message for message in _issues(plugin_dir, "error") if "permissions= lists" in message]


@pytest.mark.parametrize("excluded,shipped", [("ui", "ui/panel.js"), ("vendor", "vendor")])
def test_path_excludes_do_not_hide_the_subtree_they_do_not_exclude(tmp_path, excluded, shipped):
    # "exclude" matches the path itself; files below it still ship.
    plugin_dir = _plugin(tmp_path, "x = 1\n", "[permissions.require]\n")
    (plugin_dir / "pyproject.toml").write_text(
        f'[tool.neko.build]\nexclude = ["{excluded}"]\n', encoding="utf-8"
    )
    (plugin_dir / "ui").mkdir()
    (plugin_dir / "ui" / "panel.js").write_text("1\n", encoding="utf-8")
    (plugin_dir / "vendor").mkdir()
    (plugin_dir / "vendor" / "a.py").write_text("x = 1\n", encoding="utf-8")
    info = _issues(plugin_dir, "info")
    blind = " ".join(message.splitlines()[0] for message in info if "(not scanned" in message.splitlines()[0])
    assert shipped in blind
    assert not any(message.startswith("the code scan found no use beyond") for message in info)


# --- blind spots by kind: counted, per-file summary, hints, developer-only ---------------


def test_context_loss_is_left_out_of_the_default_report(tmp_path):
    source = "import threading\nthreading.Thread(target=print).start()\n"
    report = _scan(tmp_path, source)
    assert "context_loss" not in report
    assert report["blind_spots"] == [] and report["blind_spot_files"] == []
    assert scan_note(report) == "no_extra_usage"
    assert summarize_permissions(report)["blind_spot_types"] == []
    developer = _scan(tmp_path, source, developer=True)
    assert [(spot["type"], spot["line"]) for spot in developer["context_loss"]] == [("context_loss", 2)]


def test_cli_still_shows_context_loss_to_the_developer(tmp_path):
    plugin_dir = _plugin(tmp_path, "import threading\nthreading.Thread(target=print).start()\n", "[permissions]\n")
    info = _issues(plugin_dir, "info")
    assert [message for message in info if message.startswith("worker.py:2: ") and "asyncio.to_thread" in message]
    # Not a counted blind spot, so no blind-spot sentence.
    assert not [message for message in info if message.startswith("the code scan has known blind spots")]


def test_not_analyzed_parts_are_never_counted(tmp_path):
    report = _scan(tmp_path, "x = 1\n", **{"ui/panel.vue": "<template/>", "tests/test_a.py": "x = 1\n"})
    assert report["blind_spots"] == [] and report["blind_spot_files"] == []
    assert {spot["reason"] for spot in report["not_analyzed"]} == {"non_python", "test_source"}
    summary = summarize_permissions(report)
    assert summary["blind_spot_types"] == []
    assert not [row for row in summary["rows"] if row["source"] == "unconfirmed"]
    # Still no claim that the scan saw everything.
    assert scan_note(report) == "not_analyzed"


@pytest.mark.parametrize(
    "source,basis",
    [
        ("import importlib\nsp = importlib.import_module('subprocess')\nsp.run(['ls'])\n", "resolved_call"),
        ("from importlib import import_module as im\nim('subprocess').run(['ls'])\n", "resolved_call"),
        ("import importlib\nimportlib.import_module(name='subprocess').run(['ls'])\n", "resolved_call"),
        ("__import__('subprocess').run(['ls'])\n", "resolved_call"),
        ("__import__('subprocess.x').run(['ls'])\n", "resolved_call"),
        ("import importlib\ngetattr(importlib.import_module('subprocess'), 'run')(['ls'])\n", "resolved_call"),
        (
            "import importlib\nclass P:\n    def __init__(self):\n        self.sp = importlib.import_module('subprocess')\n"
            "    def go(self):\n        self.sp.run(['ls'])\n",
            "resolved_call",
        ),
        (
            "import importlib\nclass P:\n    def __init__(self):\n        self.load = importlib.import_module\n"
            "    def go(self):\n        self.load('subprocess').run(['ls'])\n",
            "resolved_call",
        ),
        ("import importlib\nsp = importlib.import_module('subprocess')\n", "import_only"),
        ("import importlib\nimportlib.import_module('subprocess')\n", "import_only"),
    ],
)
def test_literal_import_module_is_followed_like_an_import(tmp_path, source, basis):
    report = _scan(tmp_path, source)
    assert report["blind_spots"] == []
    assert _bases(report) == {"system.process:spawn": basis}


@pytest.mark.parametrize(
    "source",
    [
        "import importlib\nimportlib.import_module(name)\n",
        "import importlib\nimportlib.import_module('sub' + 'process')\n",
        "__import__('subprocess', fromlist=['run'])\n",
        "import builtins\nbuiltins.__import__('subprocess')\n",
        "import importlib\nimportlib.import_module('not a module')\n",
        # The import function's name is rebound: the module loaded is unknown.
        "import importlib\nfor importlib in loaders:\n    importlib.import_module('subprocess')\n",
        "from importlib import import_module\nwith opener() as import_module:\n    import_module('subprocess')\n",
        "__import__('subprocess.')\n",
        "__import__('.sub')\n",
    ],
)
def test_non_literal_dynamic_imports_stay_blind_spots(tmp_path, source):
    report = _scan(tmp_path, source)
    assert [spot["type"] for spot in report["blind_spots"]] == ["dynamic_import"]
    assert "system.process:spawn" not in report["permissions"]


def test_a_plugin_defined_import_module_is_not_followed(tmp_path):
    source = "def import_module(x):\n    return x\nsp = import_module('subprocess')\nsp.run(['ls'])\n"
    assert "system.process:spawn" not in _scan(tmp_path, source)["permissions"]


def test_counted_blind_spots_are_summed_up_per_file_without_lines(tmp_path, monkeypatch):
    monkeypatch.setattr(capability_analysis, "MAX_BLIND_SPOTS", 2)
    report = _scan(
        tmp_path,
        "eval(a)\neval(b)\nimport ctypes\nctypes.CDLL(p)\nimport threading\nthreading.Thread().start()\n",
        **{"b.py": "exec(c)\n", "ui/x.ts": "1"},
    )
    # The rows (with lines) are capped; the per-file counts are not.
    assert len(report["blind_spots"]) == 2
    assert report["blind_spot_files"] == [
        {"file": "b.py", "count": 1, "types": {"code_exec": 1}, "high_risk": False},
        {"file": "main.py", "count": 3, "types": {"code_exec": 2, "native_loader": 1}, "high_risk": True},
    ]


def test_native_loader_is_marked_high_risk(tmp_path):
    report = _scan(tmp_path, "import ctypes\nctypes.CDLL(p)\neval(x)\n")
    assert {(spot["type"], spot["high_risk"]) for spot in report["blind_spots"]} == {
        ("native_loader", True),
        ("code_exec", False),
    }
    assert capability_analysis.HIGH_RISK_BLIND_SPOT_TYPES == frozenset({"native_loader"})


def test_environment_and_package_directories_are_listed_as_not_analyzed(tmp_path):
    report = _scan(
        tmp_path, "x = 1\n",
        **{".venv/lib/a.py": "x = 1\n", "venv/b.py": "x = 1\n", "web/node_modules/c.js": "1", ".git/d": "x"},
    )
    assert [(spot["reason"], spot["file"]) for spot in report["not_analyzed"]] == [
        ("skipped_dir", ".venv"),
        ("skipped_dir", "venv"),
        ("skipped_dir", "web/node_modules"),
    ]


def test_a_cut_short_not_analyzed_list_says_how_many_were_left_out(tmp_path, monkeypatch):
    monkeypatch.setattr(capability_analysis, "_MAX_NON_PYTHON_REPORTED", 2)
    report = _scan(tmp_path, "x = 1\n", **{f"p{index}/vendor/a.py": "x = 1\n" for index in range(5)})
    vendor = [spot for spot in report["not_analyzed"] if spot["reason"] == "vendor"]
    assert [spot["file"] for spot in vendor] == ["p0/vendor", "p1/vendor", None]
    assert vendor[-1]["omitted"] == 3
    plugin_dir = _plugin(tmp_path / "cli", "x = 1\n", "[permissions]\n")
    for index in range(5):
        (plugin_dir / f"p{index}" / "vendor").mkdir(parents=True)
    [message] = [m for m in _issues(plugin_dir, "info") if "(not scanned" in m.splitlines()[0]]
    assert message.startswith("p0/vendor, p1/vendor, +3: ")


def test_unscanned_rows_without_a_file_do_not_hide_each_other(tmp_path, monkeypatch):
    monkeypatch.setattr(capability_analysis, "_MAX_TREE_ENTRIES", 3)
    for index in range(5):
        (tmp_path / f"data_{index}.txt").write_text("x", encoding="utf-8")
    sources = capability_analysis.read_capability_sources(tmp_path)
    sources.skipped.append({"reason": "unreadable", "file": None})
    report = analyze_capabilities(tmp_path, sources=sources)
    assert {spot["reason"] for spot in report["not_analyzed"] if spot["file"] is None} >= {"budget_exceeded", "unreadable"}


def test_legacy_report_fields_keep_their_shape(tmp_path):
    report = _scan(tmp_path, "from plugin.sdk.plugin import NekoPluginBase\neval(x)\n")
    for key in ("status", "capabilities", "evidence", "warnings", "files_analyzed", "permissions", "blind_spots"):
        assert key in report
    [spot] = report["blind_spots"]
    assert {"type", "file", "line"} <= set(spot)
    assert (spot["type"], spot["file"], spot["line"]) == ("code_exec", "main.py", 2)


@pytest.mark.parametrize(
    "source,permission,basis",
    [
        # A relative name is resolved against a literal package, as importlib does.
        ("import importlib\nimportlib.import_module('.request', 'urllib').urlopen(u)\n",
         "system.network:connect", "resolved_call"),
        ("import importlib\nr = importlib.import_module('.request', package='urllib')\nr.urlopen(u)\n",
         "system.network:connect", "resolved_call"),
        ("import importlib\nimportlib.import_module('..request', 'urllib.parse').urlopen(u)\n",
         "system.network:connect", "resolved_call"),
        # __import__ loads the full name (the hint) but returns the top level.
        ("__import__('http.server')\n", "system.network:listen", "import_only"),
        ("__import__('os.path').system('x')\n", "system.process:spawn", "resolved_call"),
    ],
)
def test_literal_imports_resolve_what_python_loads(tmp_path, source, permission, basis):
    report = _scan(tmp_path, source)
    assert report["blind_spots"] == []
    assert _bases(report).get(permission) == basis


@pytest.mark.parametrize(
    "source",
    [
        "import importlib\nimportlib.import_module('.request', pkg).urlopen(u)\n",
        "import importlib\nimportlib.import_module('.request').urlopen(u)\n",
        "import importlib\nimportlib.import_module('..request', 'urllib').urlopen(u)\n",
        "import importlib\nimportlib.import_module('.request', package=pkg)\n",
    ],
)
def test_relative_literal_imports_without_a_known_package_stay_blind_spots(tmp_path, source):
    assert "dynamic_import" in {spot["type"] for spot in _scan(tmp_path, source)["blind_spots"]}


def test_long_call_chains_are_charged_to_the_analysis_budget(tmp_path):
    source = "x = obj" + ".f()" * 300 + "\n"
    assert _scan(tmp_path, source)["status"] == "completed"
    # Walking a chain for a literal import counts toward the shared budget
    # and honours the stop event.
    import ast
    from threading import Event

    stop = Event()
    stop.set()
    chain = ast.parse(source).body[0].value
    with pytest.raises(capability_analysis._AnalysisBudgetExceeded):
        capability_analysis._resolve_chain_root(chain, {}, capability_analysis._AnalysisWalker(stop))


# --- constant getattr through names and call chains (#3951) ---------------------


def _without_lines(report: dict) -> dict:
    return {
        permission: {
            **item,
            "findings": [{key: value for key, value in row.items() if key != "line"} for row in item["findings"]],
        }
        for permission, item in report["permissions"].items()
    }


@pytest.mark.parametrize(
    "reflective,plain,before,after",
    [
        # (getattr spelling, attribute spelling, bases on main before this
        # change, bases now; a missing permission was not reported at all).
        (
            "import pathlib\nP = getattr(pathlib, 'Path')\nP('/etc/x').write_text('a')\n",
            "import pathlib\nP = pathlib.Path\nP('/etc/x').write_text('a')\n",
            {},
            {"system.fs:write": "resolved_call"},
        ),
        (
            "import pathlib\ngetattr(pathlib, 'Path')('/etc/x').write_text('a')\n",
            "import pathlib\npathlib.Path('/etc/x').write_text('a')\n",
            {},
            {"system.fs:write": "resolved_call"},
        ),
        (
            "import multiprocessing\nctx = getattr(multiprocessing, 'get_context')('spawn')\nctx.Process(target=print).start()\n",
            "import multiprocessing\nctx = multiprocessing.get_context('spawn')\nctx.Process(target=print).start()\n",
            {"system.process:spawn": "import_only"},
            {"system.process:spawn": "resolved_call"},
        ),
        (
            "import subprocess\nrun = getattr(subprocess, 'run')\nrun(['ls'])\n",
            "import subprocess\nrun = subprocess.run\nrun(['ls'])\n",
            {"system.process:spawn": "resolved_name"},
            {"system.process:spawn": "resolved_call"},
        ),
        # Nested getattr, and a default value.
        (
            "import multiprocessing\ngetattr(getattr(multiprocessing, 'get_context')('spawn'), 'Process')(target=print)\n",
            "import multiprocessing\nmultiprocessing.get_context('spawn').Process(target=print)\n",
            {"system.process:spawn": "import_only"},
            {"system.process:spawn": "resolved_call"},
        ),
        (
            "import subprocess\nrun = getattr(subprocess, 'run', None)\nrun(['ls'])\n",
            "import subprocess\nrun = subprocess.run\nrun(['ls'])\n",
            {"system.process:spawn": "resolved_name"},
            {"system.process:spawn": "resolved_call"},
        ),
        # A rebound receiver stays uncertain, as with the attribute.
        (
            "import subprocess\nfor subprocess in items:\n    run = getattr(subprocess, 'run')\n    run([])\n",
            "import subprocess\nfor subprocess in items:\n    run = subprocess.run\n    run([])\n",
            {"system.process:spawn": "suffix_match"},
            {"system.process:spawn": "suffix_match"},
        ),
    ],
)
def test_constant_getattr_results_are_followed_like_the_attribute(tmp_path, reflective, plain, before, after):
    (tmp_path / "getattr").mkdir()
    (tmp_path / "plain").mkdir()
    report = _scan(tmp_path / "getattr", reflective)
    expected = _scan(tmp_path / "plain", plain)
    assert _bases(report) == after
    del before  # documents the main result; see the PR for the comparison
    assert _without_lines(report) == _without_lines(expected)
    assert report["blind_spots"] == expected["blind_spots"] == []
    assert [(issue.severity, issue.code) for issue in compare_declarations(report)] == [
        (issue.severity, issue.code) for issue in compare_declarations(expected)
    ]


@pytest.mark.parametrize(
    "source,capabilities,bases",
    [
        # Values as on main before getattr was followed: getattr on a plugin
        # or SDK object is not followed, so the legacy capability scan (read
        # by the old consent flow) sees exactly what it saw before.
        (
            ("class P:\n    def go(self):\n        getattr(self.ctx, 'bus').memory.get('x')\n"
             "        push = getattr(self.ctx, 'push_message')\n        push('hi')\n"),
            ["messages.push"],
            {"plugin_sdk.main_agent.messages:push": "suffix_match"},
        ),
        (
            "from plugin.sdk import bus\nm = getattr(bus, 'memory')\nm.get('x')\n",
            [],
            {},
        ),
        # External origins add permissions, never legacy capabilities.
        ("import subprocess\nrun = getattr(subprocess, 'run')\nrun(['ls'])\n", [], {"system.process:spawn": "resolved_call"}),
    ],
)
def test_getattr_following_leaves_legacy_capabilities_unchanged(tmp_path, source, capabilities, bases):
    report = _scan(tmp_path, source)
    assert report["capabilities"] == capabilities
    assert _bases(report) == bases


def test_aliased_getattr_with_a_constant_name_is_still_a_blind_spot(tmp_path):
    report = _scan(tmp_path, "import subprocess\nga = getattr\nrun = ga(subprocess, 'run')\nrun(['ls'])\n")
    assert "reflective_access" in {spot["type"] for spot in report["blind_spots"]}


# --- AF_UNIX sockets and datagram endpoints (#3951) -----------------------------


@pytest.mark.parametrize(
    "source",
    [
        "import socket\ns = socket.socket(socket.AF_UNIX)\ns.bind('/tmp/p.sock')\ns.listen()\nconn, _ = s.accept()\n",
        "import socket\ns = socket.socket(family=socket.AF_UNIX, type=socket.SOCK_STREAM)\ns.listen()\n",
        "import socket\ns = socket.socket(socket.AddressFamily.AF_UNIX)\ns.listen()\n",
        "from socket import socket, AF_UNIX\ns = socket(AF_UNIX)\ns.listen()\n",
        "from socket import socket as sock, AF_UNIX\ns = sock(AF_UNIX)\ns.listen()\n",
    ],
)
def test_unix_socket_servers_are_only_possible_evidence(tmp_path, source):
    report = _scan(tmp_path, source)
    # On main: listen and connect were resolved_call (an error in check -r).
    assert _bases(report) == {CONNECT: "resolved_name", LISTEN: "resolved_name"}
    assert [(issue.severity, issue.code) for issue in compare_declarations(report)] == [
        ("warning", "used_undeclared_possible"),
        ("warning", "used_undeclared_possible"),
    ]


@pytest.mark.parametrize(
    "source",
    [
        "import socket\nsocket.socket(socket.AF_UNIX).connect('/p')\n",
        "from socket import socket, AF_UNIX\nc = socket(family=AF_UNIX)\nc.connect('/p')\n",
    ],
)
def test_unix_socket_clients_are_only_possible_connect_evidence(tmp_path, source):
    assert _bases(_scan(tmp_path, source)) == {CONNECT: "resolved_name"}


@pytest.mark.parametrize(
    "source",
    [
        "import socket\ns = socket.socket(socket.AF_INET)\ns.listen()\n",
        # The family is not statically known.
        "import socket\ndef f(family):\n    s = socket.socket(family)\n    s.listen()\n",
        "import socket\ndef f(args):\n    s = socket.socket(*args)\n    s.listen()\n",
        # A plugin-defined name spelled AF_UNIX is not the socket constant.
        "import socket\nAF_UNIX = 2\ns = socket.socket(AF_UNIX)\ns.listen()\n",
        # The name may hold another socket.
        "import socket\ns = socket.socket(socket.AF_UNIX)\ns = socket.socket()\ns.listen()\n",
    ],
)
def test_sockets_not_known_to_be_unix_stay_direct_evidence(tmp_path, source):
    report = _scan(tmp_path, source)
    assert report["permissions"][LISTEN]["basis"] == "resolved_call"
    assert report["permissions"][CONNECT]["basis"] == "resolved_call"


@pytest.mark.parametrize(
    "call,basis",
    [
        ("loop.create_datagram_endpoint(object, local_addr=('', 1))", "resolved_call"),
        ("loop.create_datagram_endpoint(object, ('', 1))", "resolved_call"),
        ("loop.create_datagram_endpoint(object, **options)", "resolved_name"),
        ("loop.create_datagram_endpoint(object, local_addr='/p', family=socket.AF_UNIX)", "resolved_name"),
        ("loop.create_datagram_endpoint(object, remote_addr=('a', 1))", None),
        ("loop.create_datagram_endpoint(object, local_addr=None, remote_addr=('a', 1))", None),
        ("loop.create_datagram_endpoint(object, sock=options)", None),
    ],
)
def test_datagram_endpoints_listen_only_with_a_local_address(tmp_path, call, basis):
    source = f"import asyncio, socket\nasync def f(options):\n    loop = asyncio.get_running_loop()\n    await {call}\n"
    report = _scan(tmp_path, source)
    # On main none of these reported system.network:listen.
    assert report["permissions"].get(LISTEN, {}).get("basis") == basis
    assert report["capabilities"] == []


@pytest.mark.parametrize(
    "source",
    [
        # Receivers from an external origin whose result reaches an
        # SDK-looking method: main read these as "getattr()()..." (not
        # external), so the legacy capability scan matched them.
        "import extlib\nctx = getattr(extlib, 'get_ctx')()\nctx.push_message(source='a')\n",
        "import extlib\ngetattr(extlib, 'get_ctx')().push_message('a')\n",
        "import extlib\nP = getattr(extlib, 'Plugin')\nP().push_message('x')\n",
        "import extlib\ngetattr(getattr(extlib, 'get_ctx')(), 'push_message')('x')\n",
        "import importlib\nim = getattr(importlib, 'import_module')\nim('x').push_message()\n",
        # An alias chain that receives the getattr path last.
        ("import extlib\nfrom extlib import get_ctx as a, get_ctx as b, get_ctx as c\nc = b\nb = a\n"
         "a = getattr(extlib, 'get_ctx')\nc().push_message()\n"),
    ],
)
def test_followed_getattr_keeps_the_legacy_reading(tmp_path, source):
    report = _scan(tmp_path, source)
    assert report["capabilities"] == ["messages.push"]
    assert [row["method"] for row in report["evidence"]] == ["getattr()().push_message"]
    assert report["permissions"]["plugin_sdk.main_agent.messages:push"]["basis"] == "suffix_match"


@pytest.mark.parametrize(
    "source,bases",
    [
        # x = getattr(x, "a") is not followed, like x = x.a (it would grow
        # on every round); the result stays as on main.
        ("import os\nos = getattr(os, 'path')\nos.join('a')\n", {}),
        ("import subprocess\nsubprocess = getattr(subprocess, 'x')\nsubprocess.run([])\n", {"system.process:spawn": "import_only"}),
        # socket is reassigned here, so socket.AF_UNIX is not trusted either.
        ("import socket\nsocket = socket.socket(socket.AF_UNIX)\nsocket.listen()\n", {CONNECT: "resolved_call"}),
        # An indirect cycle keeps the reading it had on main.
        ("import pathlib as a\nb = getattr(a, 'Path')\na = getattr(b, 'cwd')\n", {}),
    ],
)
def test_self_referencing_assignments_are_not_followed(tmp_path, source, bases):
    report = _scan(tmp_path, source)
    assert report["status"] == "completed"
    assert _bases(report) == bases


def test_import_module_through_getattr_stays_a_blind_spot(tmp_path):
    report = _scan(tmp_path, "import importlib\nim = getattr(importlib, 'import_module')\nim('subprocess').run([])\n")
    assert "dynamic_import" in {spot["type"] for spot in report["blind_spots"]}


@pytest.mark.parametrize(
    "source",
    [
        "import socket\ngetattr(socket, 'socket')(socket.AF_UNIX).listen()\n",
        "import socket\ns = getattr(socket, 'socket')(socket.AF_UNIX)\ns.listen()\n",
        "import socket\nmake = getattr(socket, 'socket')\ns = make(socket.AF_UNIX)\ns.listen()\n",
    ],
)
def test_unix_sockets_made_through_getattr_are_local_too(tmp_path, source):
    # On main: connect resolved_call, listen not reported.
    assert _bases(_scan(tmp_path, source)) == {CONNECT: "resolved_name", LISTEN: "resolved_name"}


@pytest.mark.parametrize(
    "source",
    [
        # t may hold the INET socket when it listens.
        "import socket\ns = socket.socket(socket.AF_UNIX)\nt = socket.socket()\nt.listen()\nt = s\n",
        # Assigned by another scope.
        ("import socket\ns = socket.socket(socket.AF_UNIX)\ndef f():\n    global s\n    s = socket.socket()\n"
         "def g():\n    s.listen()\n"),
        ("import socket\ndef f():\n    s = socket.socket(socket.AF_UNIX)\n    def g():\n        nonlocal s\n"
         "        s = socket.socket()\n    g()\n    s.listen()\n"),
        # The family name is rebound.
        "import socket\nfrom socket import AF_UNIX\nAF_UNIX = 2\nsocket.socket(AF_UNIX).listen()\n",
        "import socket\nfrom socket import AF_UNIX\nfor AF_UNIX in families:\n    socket.socket(AF_UNIX).listen()\n",
        "import socket\nfrom socket import AF_UNIX\nAF_UNIX = 2\ns = socket.socket(AF_UNIX)\ns.bind(('', 80))\ns.listen()\n",
        "import socket\nfrom socket import AF_UNIX\nwith make() as AF_UNIX:\n    s = socket.socket(AF_UNIX)\n    s.listen()\n",
        "import socket\nfamily = socket.AF_UNIX\nfamily = 2\ns = socket.socket(family)\ns.listen()\n",
        "import socket\nfrom socket import AF_UNIX\n[socket.socket(AF_UNIX).listen() for AF_UNIX in families]\n",
        "import socket\nfamily = socket.AF_UNIX\nfrom other import family\nsocket.socket(family).listen()\n",
        ("import socket\nfrom socket import AF_UNIX\ndef f():\n    global AF_UNIX\n    AF_UNIX = 2\n"
         "socket.socket(AF_UNIX).listen()\n"),
        # A class attribute set to another socket in another method.
        ("import socket\nclass S:\n    def a(self):\n        self.sock = socket.socket(socket.AF_UNIX)\n"
         "    def b(self):\n        self.sock = socket.socket()\n    def c(self):\n        self.sock.listen()\n"),
    ],
)
def test_sockets_that_may_not_be_unix_keep_their_listen_evidence(tmp_path, source):
    report = _scan(tmp_path, source)
    assert report["permissions"][LISTEN]["basis"] in ("resolved_call", "suffix_match")
    if "AF_UNIX = 2" in source:
        # The construction and what is done through it agree.
        assert report["permissions"][CONNECT]["basis"] == "resolved_call"


def test_the_local_socket_marker_never_reaches_the_report(tmp_path):
    report = _scan(
        tmp_path,
        ("import socket\nclass P:\n    def go(self):\n        s = socket.socket(socket.AF_UNIX)\n"
         "        getattr(s, 'bind')('/p')\n        s.listen()\n        s.push_message()\n"
         "        socket.socket(socket.AF_UNIX).__dict__\n"),
    )
    rows = [row for item in report["permissions"].values() for row in item["findings"]]
    methods = [row["method"] for row in rows + report["evidence"] + report["blind_spots"]]
    assert methods and not [method for method in methods if "[" in method or "AF_UNIX" in method]
    assert all(type(method) is str for method in methods)
    assert report["capabilities"] == []


@pytest.mark.parametrize(
    "source",
    [
        "import socket\nx = socket" + ".socket(socket.AF_UNIX)" * 60 + "\n",
        "import socket\nx = " + "getattr(" * 60 + "socket" + ", 'socket')(socket.AF_UNIX)" * 60 + "\n",
        "x = " + "getattr(" * 60 + "obj" + ", 'f')(socket.AF_UNIX)" * 60 + "\n",
        "import socket\nx = socket" + ".socket(socket.AF_UNIX).a()" * 60 + "\n",
    ],
)
def test_nested_getattr_and_socket_chains_stay_within_budget(tmp_path, monkeypatch, source):
    # Each part of a chain is resolved once: well under a small budget.
    monkeypatch.setattr(capability_analysis, "MAX_NODES", 200_000)
    assert _scan(tmp_path, source)["status"] == "completed"


def test_untrusted_family_names_keep_their_legacy_binding(tmp_path):
    # As on main: item is an import, so item.push_message() is external.
    report = _scan(tmp_path, "from socket import AF_UNIX as item\nfor item in clients:\n    item.push_message()\n")
    assert report["capabilities"] == []
    assert "plugin_sdk.main_agent.messages:push" not in report["permissions"]


@pytest.mark.parametrize(
    "source",
    [
        # A parameter replaced in one branch only.
        (
            "import socket\ndef f(sock):\n    if sock is None:\n        sock = socket.socket(socket.AF_UNIX)\n"
            "    sock.connect(('a', 80))\n    sock.listen()\n"
        ),
        # self.x set to a network socket in another method, or through setattr.
        (
            "import socket\nclass C:\n    def __init__(self):\n        self.sock = socket.socket()\n"
            "    def use(self):\n        self.sock.listen()\n        self.sock = socket.socket(socket.AF_UNIX)\n"
        ),
        (
            "import socket\nclass C:\n    def __init__(self):\n        setattr(self, 'sock', socket.socket())\n"
            "    def use(self):\n        self.sock = socket.socket(socket.AF_UNIX)\n        self.sock.listen()\n"
        ),
        (
            "import socket\nclass C:\n    def __init__(self, name):\n        setattr(self, name, socket.socket())\n"
            "    def use(self):\n        self.sock = socket.socket(socket.AF_UNIX)\n        self.sock.listen()\n"
        ),
        # A match capture reuses the name.
        "import socket\ns = socket.socket(socket.AF_UNIX)\nmatch x:\n    case s:\n        s.listen()\n",
        "import socket\ns = socket.socket(socket.AF_UNIX)\nmatch x:\n    case [*s]:\n        s.listen()\n",
        "import socket\ns = socket.socket(socket.AF_UNIX)\nmatch x:\n    case {**s}:\n        s.listen()\n",
        # fileno= wraps whatever socket the descriptor is.
        "import socket\ndef f(fd):\n    s = socket.socket(socket.AF_UNIX, fileno=fd)\n    s.listen()\n",
        "import socket\ndef f(fd):\n    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM, 0, fd)\n    s.listen()\n",
        "import socket\ndef f(options):\n    s = socket.socket(socket.AF_UNIX, **options)\n    s.listen()\n",
    ],
)
def test_names_that_may_hold_another_socket_keep_direct_evidence(tmp_path, source):
    # On main all of these are resolved_call.
    report = _scan(tmp_path, source)
    assert report["permissions"][LISTEN]["basis"] == "resolved_call"
    assert not [issue for issue in compare_declarations(report) if issue.params["permission"] == LISTEN and issue.severity != "error"]



_SELF_SOCKET = (
    "import socket\nclass C:\n    def __init__(self):\n        self.sock = socket.socket(socket.AF_UNIX)\n"
    "    def go(self, many):\n        {}\n        self.sock.listen()\n"
)


@pytest.mark.parametrize(
    "write",
    [
        "self.sock, self.o = socket.socket(), 1",
        "with socket.socket() as self.sock: pass",
        "for self.sock in many: pass",
        "self.sock += 1",
        "def inner(): self.sock = socket.socket()",
        "f = lambda: setattr(self, 'sock', socket.socket())",
        "me = self; me.sock = socket.socket()",
        "self.__dict__['sock'] = socket.socket()",
    ],
)
def test_self_attributes_written_in_other_ways_are_not_local(tmp_path, write):
    assert _scan(tmp_path, _SELF_SOCKET.format(write))["permissions"][LISTEN]["basis"] == "resolved_call"


def test_self_attributes_are_never_local(tmp_path):
    # Only a plain local name keeps the marker; the construction itself
    # stays possible evidence.
    report = _scan(tmp_path, _SELF_SOCKET.format("pass"))
    assert _bases(report) == {CONNECT: "resolved_name", LISTEN: "resolved_call"}

def test_followed_getattr_on_an_environment_value_adds_no_evidence(tmp_path):
    (tmp_path / "plain").mkdir()
    (tmp_path / "getattr").mkdir()
    plain = _scan(tmp_path / "plain", "import os\ntok = os.environ.get('X')\ntok.strip().lower()\n")
    reflective = _scan(tmp_path / "getattr", "import os\ntok = os.environ.get('X')\ngetattr(tok, 'strip')().lower()\n")

    def methods(report):
        return {row["method"] for row in report["permissions"]["system.env:read"]["findings"]}

    # The getattr call itself reads as tok.strip (as on main); the chain
    # after it adds nothing, like the attribute spelling.
    assert methods(reflective) - methods(plain) == {"os.environ.get().strip"}


@pytest.mark.parametrize(
    "source",
    [
        # The module name of the family is rebound.
        ("import socket as families\nfrom socket import socket as make\nfor families in sources:\n"
         "    s = make(families.AF_UNIX)\n    s.listen()\n"),
        "from socket import socket as make\nimport socket\nfor socket in families:\n    make(socket.AF_UNIX).listen()\n",
        # A method parameter spelled like the module constant.
        ("import socket\nfrom socket import AF_UNIX\nclass S:\n    def __init__(self, AF_UNIX):\n"
         "        self.s = socket.socket(AF_UNIX)\n    def go(self):\n        self.s.listen()\n"),
    ],
)
def test_rebound_family_roots_and_parameters_are_not_local(tmp_path, source):
    assert _scan(tmp_path, source)["permissions"][LISTEN]["basis"] == "resolved_call"


def test_unix_socket_from_a_literal_import_is_local(tmp_path):
    source = "import importlib, socket\ns = importlib.import_module('socket').socket(socket.AF_UNIX)\ns.listen()\n"
    assert _bases(_scan(tmp_path, source)) == {CONNECT: "resolved_name", LISTEN: "resolved_name"}


@pytest.mark.parametrize(
    "call,basis",
    [
        ("loop.create_datagram_endpoint(object, *addresses)", "resolved_name"),
        ("loop.create_datagram_endpoint(*addresses, remote_addr=('a', 1))", "resolved_name"),
        ("loop.create_datagram_endpoint(object, *addresses, local_addr=('', 1))", "resolved_call"),
        ("getattr(loop, 'create_datagram_endpoint')(object, local_addr=('', 1))", "resolved_call"),
        ("getattr(loop, 'create_datagram_endpoint')(object, local_addr='/p', family=socket.AF_UNIX)", "resolved_name"),
    ],
)
def test_datagram_endpoints_with_unpacked_or_reflected_calls(tmp_path, call, basis):
    source = f"import asyncio, socket\nasync def f(addresses):\n    loop = asyncio.get_running_loop()\n    await {call}\n"
    assert _scan(tmp_path, source)["permissions"][LISTEN]["basis"] == basis



@pytest.mark.parametrize(
    "cycle",
    [
        "b = a.Path\na = b.cwd\n",
        "b = a.Path\nc = b.x\na = c.cwd\n",
        "def f():\n    b = a.Path\n    a = b.cwd\n",
    ],
)
def test_indirect_attribute_alias_cycles_do_not_stop_the_scan(tmp_path, cycle):
    # On main these grew every round until the length limit (partial).
    report = _scan(tmp_path, "import pathlib as a\nimport subprocess\n" + cycle + "subprocess.run(['ls'])\n")
    assert report["status"] == "completed"
    assert report["permissions"]["system.process:spawn"]["basis"] == "resolved_call"


def test_alias_cycles_that_settle_resolve_as_before(tmp_path):
    report = _scan(tmp_path, "class P:\n    def go(self):\n        a = b\n        b = a\n        a.push_message()\n")
    assert report["capabilities"] == ["messages.push"]
    assert [row["method"] for row in report["evidence"]] == ["b.push_message"]



@pytest.mark.parametrize(
    "source,basis",
    [
        # An alias of a family name that is assigned twice.
        (
            "import socket\nfamily = socket.AF_INET\nfamily = socket.AF_UNIX\nalias = family\n"
            "s = socket.socket(alias)\ns.listen()\n",
            "resolved_call",
        ),
        ("import socket\nfamily = socket.AF_UNIX\nalias = family\ns = socket.socket(alias)\ns.listen()\n", "resolved_call"),
        # A method local shadowing the module constant.
        (
            "import socket\nfrom socket import AF_UNIX\nclass C:\n    def __init__(self):\n"
            "        AF_UNIX = socket.AF_INET\n        self.s = socket.socket(AF_UNIX)\n"
            "    def go(self):\n        self.s.listen()\n",
            "resolved_call",
        ),
        # A function's own import is judged on its own.
        (
            "import socket\nfor socket in xs:\n    pass\ndef f():\n    import socket\n"
            "    s = socket.socket(socket.AF_UNIX)\n    s.listen()\n",
            "resolved_name",
        ),
    ],
)
def test_family_trust_follows_aliases_locals_and_own_imports(tmp_path, source, basis):
    assert _scan(tmp_path, source)["permissions"][LISTEN]["basis"] == basis


def test_long_alias_chains_stay_within_budget(tmp_path, monkeypatch):
    # Cycle detection walks each name once.
    monkeypatch.setattr(capability_analysis, "MAX_NODES", 400_000)
    source = "import os\nx0 = os\n" + "".join(f"x{i} = x{i - 1}\n" for i in range(1, 4000))
    assert _scan(tmp_path, source)["status"] == "completed"



@pytest.mark.parametrize(
    "source,basis",
    [
        # A local or parameter of another method does not shadow __init__'s.
        (
            "import socket\nclass C:\n    def __init__(self):\n        self.s = socket.socket(socket.AF_UNIX)\n"
            "    def other(self):\n        AF_UNIX = 1\n    def go(self):\n        self.s.listen()\n",
            "resolved_call",
        ),
        (
            "import socket\nfrom socket import AF_UNIX\nclass C:\n    def __init__(self):\n"
            "        self.s = socket.socket(AF_UNIX)\n    def other(self, AF_UNIX):\n        pass\n"
            "    def go(self):\n        self.s.listen()\n",
            "resolved_call",
        ),
        # A class attribute default is another socket self.sock may hold.
        (
            "import socket\nclass C:\n    sock = socket.socket()\n    def maybe(self, x):\n        if x:\n"
            "            self.sock = socket.socket(socket.AF_UNIX)\n    def go(self):\n        self.sock.listen()\n",
            "resolved_call",
        ),
    ],
)
def test_self_attribute_sockets_keep_direct_evidence(tmp_path, source, basis):
    assert _scan(tmp_path, source)["permissions"][LISTEN]["basis"] == basis


@pytest.mark.parametrize(
    "source",
    [
        # Aliases of the socket or of the family are not followed.
        "import socket\nt = socket.socket(socket.AF_UNIX)\ns = t\ns.listen()\n",
        "import socket\nt = socket.socket(socket.AF_UNIX)\ns = t\ngetattr(s, 'listen')()\n",
        "import socket\nfamily = socket.AF_UNIX\ns = socket.socket(family)\ns.listen()\n",
        # Nor is a socket made in another scope, or stored in an attribute.
        "import socket\ns = socket.socket(socket.AF_UNIX)\ndef f():\n    s.listen()\n",
        (
            "import socket\nclass S:\n    def __init__(self):\n        self.sock = socket.socket(socket.AF_UNIX)\n"
            "    def serve(self):\n        self.sock.bind('/p')\n        self.sock.listen()\n"
        ),
        # Constructor alias assigned twice, name bound by two imports, a
        # nested alias of a family, self.s = parameter, a method import
        # shadowing AF_UNIX, object.__setattr__, a module attribute, a
        # family bound by two imports.
        "import socket, evil\nmake = evil.factory\nif c:\n    make = socket.socket\nmake(socket.AF_UNIX).listen()\n",
        "from evil import socket\nif 0:\n    import socket\ns = socket.socket(socket.AF_UNIX)\ns.listen()\n",
        (
            "import socket\nfrom socket import AF_INET, AF_UNIX\nfamily = AF_INET\nif c:\n    family = AF_UNIX\n"
            "def f():\n    fam = family\n    socket.socket(fam).listen()\n"
        ),
        (
            "import socket\nsock = socket.socket(socket.AF_UNIX)\nclass C:\n    def __init__(self, sock):\n"
            "        self.s = sock\n    def go(self):\n        self.s.listen()\n"
        ),
        (
            "import socket\nfrom socket import AF_UNIX\nclass C:\n    def __init__(self):\n"
            "        from mylib import AF_UNIX\n        self.s = socket.socket(AF_UNIX)\n"
            "    def go(self):\n        self.s.listen()\n"
        ),
        (
            "import socket\nclass C:\n    def __init__(self):\n        self.sock = socket.socket(socket.AF_UNIX)\n"
            "    def other(self):\n        object.__setattr__(self, 'sock', socket.socket())\n"
            "    def go(self):\n        self.sock.listen()\n"
        ),
        (
            "import socket, ns\nns.sock = socket.socket(socket.AF_UNIX)\ndef f():\n    ns.sock = socket.socket()\n"
            "def g():\n    ns.sock.listen()\n"
        ),
        (
            "import socket\nfrom socket import AF_INET as family\nif use_unix:\n    from socket import AF_UNIX as family\n"
            "s = socket.socket(family)\ns.listen()\n"
        ),
        (
            "import socket\nclass C:\n    sock = None\n    def __init__(self):\n        self.sock = socket.socket(socket.AF_UNIX)\n"
            "    def go(self):\n        self.sock.listen()\n"
        ),
        # Rebound by a def / class, or possibly by a star import.
        "import socket\ns = socket.socket(socket.AF_UNIX)\ndef s():\n    pass\ns.listen()\n",
        "import socket\ns = socket.socket(socket.AF_UNIX)\nclass s:\n    pass\ns.listen()\n",
        "import socket\ns = socket.socket(socket.AF_UNIX)\nfrom evil import *\ns.listen()\n",
    ],
)
def test_only_the_narrow_local_socket_shapes_are_trusted(tmp_path, source):
    # On main all of these are resolved_call.
    assert _scan(tmp_path, source)["permissions"][LISTEN]["basis"] == "resolved_call"


@pytest.mark.parametrize(
    "source",
    [
        "import socket\ns = socket.socket(socket.AF_UNIX)\ns.bind('/p')\ns.listen()\n",
        "import socket\ndef f():\n    s = socket.socket(socket.AF_UNIX)\n    s.listen()\n",
        "from socket import socket, AF_UNIX\ns = socket(AF_UNIX)\ns.listen()\n",
    ],
)
def test_the_narrow_local_socket_shapes_are_possible_evidence(tmp_path, source):
    assert _bases(_scan(tmp_path, source)) == {CONNECT: "resolved_name", LISTEN: "resolved_name"}


@pytest.mark.parametrize("long_tail", [False, True])
def test_getattr_cycles_through_an_earlier_assignment_are_detected(tmp_path, long_tail):
    # b is reassigned after the cycle, which runs through its earlier
    # assignment. As on main: completed, nothing lost.
    source = "import pathlib as a\nimport x\nimport subprocess\nb = getattr(a, 'Path')\na = getattr(b, 'cwd')\nb = x\n"
    source += "".join(f"v{i} = {i}\n" for i in range(300)) if long_tail else ""
    source += "a.push_message()\nsubprocess.run([])\n"
    report = _scan(tmp_path, source)
    assert report["status"] == "completed"
    assert report["capabilities"] == ["messages.push"]
    assert report["permissions"]["system.process:spawn"]["basis"] == "resolved_call"


@pytest.mark.parametrize(
    "source",
    [
        # A star import may rebind an imported family name too.
        "import socket\nfrom socket import AF_UNIX\nfrom evil import *\n\nsocket.socket(AF_UNIX).listen()\n",
        # A method taken from the construction is not the socket.
        "import socket\nconnect = socket.socket(socket.AF_UNIX).connect\nconnect('/p')\nlisten = socket.socket(socket.AF_UNIX).listen\nlisten()\n",
    ],
)
def test_star_imports_and_bound_methods_are_not_local(tmp_path, source):
    report = _scan(tmp_path, source)
    rows = report["permissions"].get(LISTEN, report["permissions"][CONNECT])["findings"]
    # The calls through the stored name stay direct (as on main).
    assert [row["basis"] for row in rows if row["line"] in (3, 5, 6)] and all(
        row["basis"] == "resolved_call" for row in rows if row["line"] in (3, 5, 6)
    )


def test_a_long_getattr_attribute_name_does_not_stop_the_scan(tmp_path):
    name = "a" * 4100
    source = f"import os, subprocess\ndef f(obj):\n    value = getattr(obj, '{name}')\nv = getattr(os, '{name}')\nv()\nsubprocess.run([])\n"
    report = _scan(tmp_path, source)
    assert report["status"] == "completed"
    assert report["permissions"]["system.process:spawn"]["basis"] == "resolved_call"


@pytest.mark.parametrize(
    "init,spawn",
    [
        # getattr on a method parameter: not resolved through the module of
        # the same name (as on main).
        ("def __init__(self, runner):\n        self.run = getattr(runner, 'run')", "import_only"),
        # On the module itself it is followed, like self.run = runner.run.
        ("def __init__(self):\n        self.run = getattr(runner, 'run')", "resolved_call"),
    ],
)
def test_getattr_on_a_method_parameter_is_not_the_module(tmp_path, init, spawn):
    source = f"import subprocess as runner\nclass C:\n    {init}\n    def go(self):\n        self.run()\n"
    report = _scan(tmp_path, source)
    assert report["permissions"]["system.process:spawn"]["basis"] == spawn
    assert report["capabilities"] == []


@pytest.mark.parametrize(
    "use,basis",
    [
        # The getattr receiver is bound twice: like net.socket(AF_UNIX).
        ("getattr(net, 'socket')(AF_UNIX).listen()", "resolved_call"),
        ("net.socket(AF_UNIX).listen()", "resolved_call"),
    ],
)
def test_reflected_constructor_on_an_untrusted_root_is_not_local(tmp_path, use, basis):
    source = "import thirdparty as net\nif use_stdlib:\n    import socket as net\nfrom socket import AF_UNIX\n" + use + "\n"
    report = _scan(tmp_path, source)
    assert {row["basis"] for row in report["permissions"][LISTEN]["findings"]} == {basis}
    assert report["permissions"][CONNECT]["basis"] == basis


def test_reflected_constructor_on_a_trusted_import_is_local(tmp_path):
    source = "import socket\ngetattr(socket, 'socket')(socket.AF_UNIX).listen()\n"
    assert _bases(_scan(tmp_path, source)) == {CONNECT: "resolved_name", LISTEN: "resolved_name"}


@pytest.mark.parametrize(
    "call,basis",
    [
        # A client with a fixed source address: binds, but need not listen.
        ("loop.create_datagram_endpoint(object, local_addr=('0.0.0.0', 0), remote_addr=('8.8.8.8', 53))", "resolved_name"),
        ("loop.create_datagram_endpoint(object, ('0.0.0.0', 0), ('8.8.8.8', 53))", "resolved_name"),
        ("loop.create_datagram_endpoint(object, local_addr=('', 1), remote_addr=None)", "resolved_call"),
    ],
)
def test_datagram_endpoints_with_a_remote_address_are_possible_listen(tmp_path, call, basis):
    source = f"import asyncio\nasync def f():\n    loop = asyncio.get_running_loop()\n    await {call}\n"
    assert _scan(tmp_path, source)["permissions"][LISTEN]["basis"] == basis


def test_resolved_references_keep_the_legacy_spelling():
    import ast
    from threading import Event

    tree = ast.parse("import extlib\ngetattr(extlib, 'get_ctx')().push_message('a')\n")
    walker = capability_analysis._AnalysisWalker(Event())
    methods = {method for _node, (method, external) in capability_analysis._resolved_references(tree, walker) if not external}
    assert "getattr()().push_message" in methods


_UNIX_SERVER = "import socket\ns = socket.socket(socket.AF_UNIX)\ns.bind(('', 8080))\ns.listen()\n"


@pytest.mark.parametrize(
    "write",
    [
        "socket.AF_UNIX = 2\n",
        "socket.AF_UNIX += 0\n",
        "setattr(socket, 'AF_UNIX', 2)\n",
        "del socket.AF_UNIX\n",
        "socket.AddressFamily = object\n",
        "socket.AF_UNIX: int = socket.AF_INET\n",
        "socket.AF_UNIX, other = socket.AF_INET, 0\n",
        "for socket.AF_UNIX in (socket.AF_INET,):\n    pass\n",
        "delattr(socket, 'AF_UNIX')\n",
    ],
)
def test_a_rewritten_family_constant_is_not_trusted(tmp_path, write):
    # One import only, so the family write alone decides.
    report = _scan(tmp_path, _UNIX_SERVER.replace("import socket\n", "import socket\n" + write, 1))
    # As on main: definite evidence.
    assert _bases(report) == {CONNECT: "resolved_call", LISTEN: "resolved_call"}


def test_a_family_constant_rewritten_in_another_file_is_not_trusted(tmp_path):
    report = _scan(tmp_path, _UNIX_SERVER, **{"setup.py": "import socket\nsocket.AF_UNIX = 2\n"})
    assert _bases(report) == {CONNECT: "resolved_call", LISTEN: "resolved_call"}


def test_comparing_with_the_family_constant_is_not_a_write(tmp_path):
    report = _scan(tmp_path, _UNIX_SERVER + "if s.family == socket.AF_UNIX:\n    pass\n")
    assert _bases(report) == {CONNECT: "resolved_name", LISTEN: "resolved_name"}


def test_the_family_write_check_is_linear():
    # Many unterminated "setattr(" in one file: a single parse and walk,
    # no backtracking text scan. Called directly, so a slow CI machine does
    # not make it flaky; the old regex took tens of seconds here.
    from threading import Event

    source = _UNIX_SERVER + "# " + "setattr(" * 100000 + "\n"
    walker = capability_analysis._AnalysisWalker(Event())
    assert capability_analysis._families_written([("main.py", source)], walker) is False


@pytest.mark.parametrize(
    "call,basis",
    [
        ("loop.create_datagram_endpoint(object, local_addr=('', 1), **options)", "resolved_name"),
        # *rest in the remote_addr slot may carry one.
        ("loop.create_datagram_endpoint(object, ('', 1), *rest)", "resolved_name"),
        # With local_addr= given, a non-empty *rest would fill local_addr
        # again (TypeError), so it cannot carry remote_addr.
        ("loop.create_datagram_endpoint(object, *rest, local_addr=('', 1))", "resolved_call"),
        ("loop.create_datagram_endpoint(object, local_addr=('', 1), remote_addr=None, **options)", "resolved_call"),
    ],
)
def test_datagram_endpoints_whose_remote_address_may_be_unpacked(tmp_path, call, basis):
    source = f"import asyncio\nasync def f(options, rest):\n    loop = asyncio.get_running_loop()\n    await {call}\n"
    assert _scan(tmp_path, source)["permissions"][LISTEN]["basis"] == basis


@pytest.mark.parametrize(
    "write",
    [
        "vars(socket)['AF_UNIX'] = 2\n",
        "socket.__dict__['AF_UNIX'] = 2\n",
        "(socket.AF_UNIX) = 2\n",
    ],
)
def test_a_family_constant_rewritten_through_the_module_dict_is_not_trusted(tmp_path, write):
    report = _scan(tmp_path, _UNIX_SERVER.replace("import socket\n", "import socket\n" + write, 1))
    assert _bases(report) == {CONNECT: "resolved_call", LISTEN: "resolved_call"}


def test_getattr_on_a_loop_target_is_not_the_module_in_self_attributes(tmp_path):
    source = (
        "import subprocess as runner\nclass C:\n    def __init__(self, runners):\n        for runner in runners:\n"
        "            self.run = getattr(runner, 'run')\n    def go(self):\n        self.run()\n"
    )
    report = _scan(tmp_path, source)
    # The use in go() is not resolved through the shadowed module (as on main).
    assert [row["line"] for row in report["permissions"]["system.process:spawn"]["findings"]] == [5]



@pytest.mark.parametrize(
    "main_prefix,other",
    [
        ("import builtins\nbuiltins.setattr(socket, 'AF_UNIX', 2)\n", None),
        ("exec('socket.AF_UNIX = 2')\n", None),
        ("socket.__dict__.update(AF_UNIX=2)\n", None),
        ("vars(socket).update({'AF_UNIX': 2})\n", None),
        ("socket.__setattr__('AF_UNIX', 2)\n", None),
        ("object.__setattr__(socket, 'AF_UNIX', 2)\n", None),
        # In another file, spelled so that the raw text does not match.
        ("", "import socket\nsetattr(socket, 'AF_' 'UNIX', 2)\n"),
        ("", "import socket\nsetattr(socket, 'AF\\x5fUNIX', 2)\n"),
        ("", "import socket\nsocket.\uff21F_UNIX = 2\n"),
    ],
)
def test_a_family_name_used_as_data_disables_the_downgrade(tmp_path, main_prefix, other):
    source = _UNIX_SERVER.replace("import socket\n", "import socket\n" + main_prefix, 1)
    files = {"net_setup.py": other} if other else {}
    report = _scan(tmp_path, source, **files)
    assert _bases(report) == {CONNECT: "resolved_call", LISTEN: "resolved_call"}


@pytest.mark.parametrize(
    "main_prefix,other",
    [
        ("setattr(socket, 'AF_' + 'UNIX', 2)\n", None),
        ("import sys\nsetattr(socket, sys.argv[1], 2)\n", None),
        ("setattr(socket, b'AF_UNIX'.decode(), 2)\n", None),
        ("exec(b'socket.AF_' b'UNIX = 2')\n", None),
        ("import builtins\ngetattr(builtins, 'set' + 'attr')(socket, 'AF_' + 'UNIX', 2)\n", None),
        ("import builtins\nw = getattr(builtins, 'setattr')\n", None),
        ("w = setattr\n", None),
        ("for key in socket.__dict__:\n    pass\n", None),
        ("globals()['AF_' + 'UNIX'] = 2\n", None),
        ("import sys\nsys.modules['sock' + 'et'] = object()\n", None),
        # In another file.
        ("", "import socket\nx = 'F_UNIX'\nsetattr(socket, 'A' + x, 2)\n"),
    ],
)
def test_a_family_name_built_at_runtime_disables_the_downgrade(tmp_path, main_prefix, other):
    source = _UNIX_SERVER.replace("import socket\n", "import socket\n" + main_prefix, 1)
    files = {"net_setup.py": other} if other else {}
    report = _scan(tmp_path, source, **files)
    assert _bases(report) == {CONNECT: "resolved_call", LISTEN: "resolved_call"}


@pytest.mark.parametrize(
    "prefix",
    [
        "class C:\n    def __init__(self):\n        setattr(self, 'name', 1)\n        object.__setattr__(self, 'x', 2)\n",
        "class C:\n    def __init__(self):\n        self.__setattr__('name', 1)\n        getattr(self, 'name')\n",
    ],
)
def test_a_constant_attribute_name_keeps_the_downgrade(tmp_path, prefix):
    report = _scan(tmp_path, _UNIX_SERVER + prefix)
    assert _bases(report) == {CONNECT: "resolved_name", LISTEN: "resolved_name"}


_REINIT_CLIENT = "import socket\ndef go():\n    s = socket.socket(socket.AF_UNIX)\n    {}\n    s.connect(('evil.com', 80))\n"


_NAME, _CALL = "resolved_name", "resolved_call"


@pytest.mark.parametrize(
    "use,construction,connect",
    [
        ("s.setblocking(False)", _NAME, _NAME),
        # Only this name loses the mark: the construction line stays local.
        ("reinit(s)", _NAME, _CALL),
        ("t = s", _NAME, _CALL),
        ("return s", _NAME, _CALL),
        ("s._sock = None", _NAME, _CALL),
        # .__init__ also turns the downgrade off for the whole plugin.
        ("s.__init__(socket.AF_INET)", _CALL, _CALL),
        ("getattr(s, '__init__')(2)", _CALL, _CALL),
        ("socket.socket.__init__(s, socket.AF_INET)", _CALL, _CALL),
        ("def inner():\n        s.__init__(socket.AF_INET)\n    inner()", _CALL, _CALL),
    ],
)
def test_a_socket_used_other_than_as_a_receiver_is_not_trusted(tmp_path, use, construction, connect):
    rows = _scan(tmp_path, _REINIT_CLIENT.format(use))["permissions"][CONNECT]["findings"]
    by_line = {row["line"]: row["basis"] for row in rows}
    # The construction (line 3) and the connect (last line), apart.
    assert (by_line[3], by_line[max(by_line)]) == (construction, connect)


def test_a_socket_reinitialised_by_a_module_helper_is_not_trusted(tmp_path):
    source = (
        "import socket\ndef reinit(x):\n    x.__init__(socket.AF_INET)\ndef go():\n"
        "    s = socket.socket(socket.AF_UNIX)\n    reinit(s)\n    s.connect(('evil.com', 80))\n"
    )
    assert _bases(_scan(tmp_path, source)) == {CONNECT: "resolved_call"}


@pytest.mark.parametrize(
    "other",
    [
        "import socket\nimport main\nmain.s.__init__(socket.AF_INET)\n",
        "import main\ngetattr(main.s, '__init__')(2)\n",
    ],
)
def test_a_module_socket_reinitialised_from_another_file_is_not_trusted(tmp_path, other):
    report = _scan(tmp_path, _UNIX_SERVER, **{"net_setup.py": other})
    assert _bases(report) == {CONNECT: "resolved_call", LISTEN: "resolved_call"}


def test_super_init_keeps_the_downgrade(tmp_path):
    source = _UNIX_SERVER + "class C(object):\n    def __init__(self):\n        super().__init__()\n"
    assert _bases(_scan(tmp_path, source)) == {CONNECT: "resolved_name", LISTEN: "resolved_name"}


@pytest.mark.parametrize("name", ["socket.py", "lib/socket.py", "socket/__init__.py"])
def test_a_plugin_module_named_socket_disables_the_downgrade(tmp_path, name):
    report = _scan(tmp_path, _UNIX_SERVER, **{name: "AF_UNIX = 2\n"})
    assert _bases(report) == {CONNECT: "resolved_call", LISTEN: "resolved_call"}


@pytest.mark.parametrize(
    "prefix",
    [
        "class C:\n    def set(self, name):\n        self.__setattr__(name, 'value')\n",
        "class C:\n    def drop(self, name):\n        object.__delattr__(self, name)\n",
    ],
)
def test_a_dunder_setattr_takes_the_name_by_position(tmp_path, prefix):
    report = _scan(tmp_path, _UNIX_SERVER + prefix)
    assert _bases(report) == {CONNECT: "resolved_call", LISTEN: "resolved_call"}


def test_super_with_arguments_is_not_exempt(tmp_path):
    other = "import socket\nimport main\nsuper(socket.socket, main.s).__init__(socket.AF_INET)\n"
    report = _scan(tmp_path, _UNIX_SERVER, **{"net_setup.py": other})
    assert _bases(report) == {CONNECT: "resolved_call", LISTEN: "resolved_call"}


@pytest.mark.parametrize(
    "other",
    [
        "import socket\nimport main\nmain.s = socket.socket()\n",
        "import socket\nimport main\nsetattr(main, 's', socket.socket())\n",
    ],
)
def test_a_module_socket_replaced_from_another_file_is_not_trusted(tmp_path, other):
    report = _scan(tmp_path, _UNIX_SERVER, **{"net_setup.py": other})
    assert _bases(report) == {CONNECT: "resolved_call", LISTEN: "resolved_call"}


def test_an_attribute_write_elsewhere_keeps_a_function_local_socket(tmp_path):
    # Another module cannot rebind a function's local name.
    report = _scan(tmp_path, _REINIT_CLIENT.format("pass"), **{"net_setup.py": "import main\nmain.s = None\n"})
    assert _bases(report) == {CONNECT: "resolved_name"}


@pytest.mark.parametrize(
    "patch",
    [
        "make = socket.socket\nsocket.socket = lambda family: make(socket.AF_INET)\n",
        "socket.SocketType = object\n",
        "setattr(socket, 'socket', object)\n",
        "socket.socket.listen = lambda self: None\n",
        "from socket import socket as S\nS.listen = lambda self: None\n",
        "S = socket.socket\nT = S\nT.bind = lambda self, address: None\n",
        "setattr(socket.socket, 'bind', None)\n",
    ],
)
def test_a_patched_socket_module_disables_the_downgrade(tmp_path, patch):
    report = _scan(tmp_path, _UNIX_SERVER.replace("import socket\n", "import socket\n" + patch, 1))
    assert _bases(report) == {CONNECT: "resolved_call", LISTEN: "resolved_call"}


def test_a_socket_module_patched_in_another_file_disables_the_downgrade(tmp_path):
    other = "import socket\nmake = socket.socket\nsocket.socket = lambda family: make(socket.AF_INET)\n"
    report = _scan(tmp_path, _UNIX_SERVER, **{"net_setup.py": other})
    assert _bases(report) == {CONNECT: "resolved_call", LISTEN: "resolved_call"}


def test_writing_other_attributes_keeps_the_downgrade(tmp_path):
    report = _scan(tmp_path, _UNIX_SERVER + "class C:\n    def go(self):\n        self.sock = None\n")
    assert _bases(report) == {CONNECT: "resolved_name", LISTEN: "resolved_name"}
