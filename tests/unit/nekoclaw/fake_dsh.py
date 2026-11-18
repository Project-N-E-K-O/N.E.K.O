"""A stand-in for dsh's ACP server, for the tests of the dsh driver.

Run as ``python fake_dsh.py --profile acp --patch <file>``, the way the
driver starts dsh. It speaks ACP on its standard input and output as dsh
0.2.1rc2 was observed to (the notes of that are in the pull request that
added the driver), with the standard library only:

* it reads the patch it is given: the provider's address and the variable
  its key is in, the model names, and the tools the guard is to ask about;
* ``initialize``, ``session/new``, ``session/resume``, ``session/close``,
  ``session/set_config_option``, ``session/prompt``, ``session/cancel``;
* a session's id is its own invention; sessions are remembered in
  ``DSH_HOME`` so that another process can resume them;
* at ``session/new`` and ``session/resume`` it connects to every service it
  is handed (``initialize``, ``tools/list``) and fails the request when one
  does not answer, as dsh does;
* it says "the guard is loaded" on standard error when the patch inserts it.

What a prompt does is scripted by the prompt itself. Each line that starts
with ``!`` is one step, carried out in order::

    !say <text>                  a reply
    !thought <text>              a thought
    !tool <name> <json>          a call of one of dsh's own tools: announced,
                                 asked about unless the guard lets the tool
                                 pass, then carried out (the result says so)
    !host <tool> <json>          a call of a tool of the service named "host",
                                 asked about in the same way first
    !gateway <tool> <json>       the same for the service named "gateway"
    !model <text>                a model request with that user text; the
                                 answer becomes a reply
    !model-after-report <json>   a model request whose conversation ends in a
                                 report with these arguments and its result
    !ask-unknown                 a question about a call that was never announced
    !ask-finished <name>         a call of one of dsh's own tools that is carried
                                 out, and then a question about it
    !sleep <seconds>             wait; a cancel ends it
    !until <file>                wait until that file is there (the test
                                 writes it to let the turn go on); a cancel
                                 ends it, and so does a minute
    !fail <message>              the prompt fails with that message
    !crash                       the process exits at once

A prompt without steps is answered with ``echo: <the prompt>``. Everything
it was asked is appended to ``DSH_HOME/fake-record.jsonl`` for the test.

Switches, from the environment: ``FAKE_DSH_GUARD=off`` (the guard does not
report itself), ``FAKE_DSH_NO_RESUME=1`` (it cannot resume sessions),
``FAKE_DSH_CLOSE=crash`` (the process exits when asked to close a session),
``FAKE_DSH_CLOSE=refuse`` (it answers that with an error and keeps the
session), ``FAKE_DSH_CLOSE=slow`` (it takes half a second to answer),
``FAKE_DSH_NEW=crash`` (the process exits when asked for a new session),
``FAKE_DSH_RESUME=broken`` (resuming a session fails inside it),
``FAKE_DSH_EXIT_DELAY=<seconds>`` (it takes that long to exit once its input ends).
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

HOME = Path(os.environ["DSH_HOME"])
RECORD = HOME / "fake-record.jsonl"
KNOWN = HOME / "fake-sessions.json"

_write_lock = threading.Lock()
_record_lock = threading.Lock()
_pending: dict[int, tuple[threading.Event, dict[str, Any]]] = {}
_next_request = [1000]
_sessions: dict[str, dict[str, Any]] = {}


def record(kind: str, **fields: Any) -> None:
    with _record_lock:
        with RECORD.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"kind": kind, "pid": os.getpid(), **fields}, ensure_ascii=False) + "\n")


def send(message: dict[str, Any]) -> None:
    with _write_lock:
        sys.stdout.buffer.write(json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n")
        sys.stdout.buffer.flush()


def ask_client(method: str, params: dict[str, Any]) -> dict[str, Any]:
    """Send a request to the client and wait for its answer."""
    with _write_lock:
        _next_request[0] += 1
        request_id = _next_request[0]
    done = threading.Event()
    box: dict[str, Any] = {}
    _pending[request_id] = (done, box)
    send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
    done.wait()
    return box


def update(session_id: str, body: dict[str, Any]) -> None:
    send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": session_id, "update": body}})


def known_sessions() -> dict[str, Any]:
    try:
        return json.loads(KNOWN.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def remember(session_id: str, cwd: str) -> None:
    sessions = known_sessions()
    sessions[session_id] = {"cwd": cwd}
    KNOWN.write_text(json.dumps(sessions), encoding="utf-8")


def post(url: str, headers: dict[str, str], body: dict[str, Any], timeout: float = 30.0) -> tuple[int, bytes]:
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json", **headers}, method="POST"
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as answer:
            return answer.status, answer.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


class Service:
    """One MCP service a session was handed."""

    def __init__(self, declared: dict[str, Any]) -> None:
        self.name = declared["name"]
        self.url = declared["url"]
        self.headers = {item["name"]: item["value"] for item in declared.get("headers") or []}
        self.tools: list[str] = []
        self._id = 0

    def rpc(self, method: str, params: dict[str, Any] | None = None, timeout: float = 30.0) -> dict[str, Any]:
        self._id += 1
        body: dict[str, Any] = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            body["params"] = params
        status, raw = post(self.url, self.headers, body, timeout)
        if status != 200:
            raise RuntimeError(f"HTTP {status}")
        return json.loads(raw.decode("utf-8"))

    def connect(self) -> None:
        # dsh's client first probes for a newer protocol and goes on when told the method is unknown.
        probe = self.rpc("server/discover", {})
        if "error" not in probe:
            raise RuntimeError("the service answered a method it should not know")
        answer = self.rpc("initialize", {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "fake-dsh"}})
        if "tools" not in answer["result"]["capabilities"]:
            return
        self.tools = [tool["name"] for tool in self.rpc("tools/list")["result"]["tools"]]


def connect_services(declared: list[dict[str, Any]]) -> dict[str, Service]:
    services = {}
    for item in declared:
        service = Service(item)
        try:
            service.connect()
        except Exception as exc:  # noqa: BLE001 - reported as dsh reports it
            raise RpcError(-32603, "Internal error", f"mcp-client({service.name}): initial connection or tool synchronization failed: {exc}") from exc
        services[service.name] = service
    return services


class RpcError(Exception):
    def __init__(self, code: int, message: str, details: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


class Config:
    def __init__(self, patch_path: Path) -> None:
        rows = json.loads(patch_path.read_text(encoding="utf-8"))
        self.rows = {row["id"]: row for row in rows if "id" in row}
        providers = self.rows["llm-pi-ai"]["config"]["providers"]
        self.provider, settings = next(iter(providers.items()))
        self.base_url = settings["baseURL"]
        self.key = os.environ.get(settings["apiKeyEnv"], "")
        self.models = [model["id"] for model in settings["models"]]
        self.unasked: set[str] = set()
        self.unasked_prefixes: list[str] = []
        self.asked: set[str] = set()
        self.guard_inserted = False
        for row in rows:
            for inserted in row.get("insert") or []:
                if inserted.get("id") == "nekoclaw-guard":
                    self.guard_inserted = (patch_path.parent / inserted["name"]).is_file()
                    settings = inserted.get("config") or {}
                    self.unasked = set(settings.get("unasked") or [])
                    self.unasked_prefixes = list(settings.get("unaskedPrefixes") or [])
                    self.asked = set(settings.get("asked") or [])

    def asks(self, tool: str) -> bool:
        """Whether the guard makes dsh ask before a call of this tool."""
        if not self.guard_inserted:
            return False
        if tool in self.asked:
            return True
        return tool not in self.unasked and not any(tool.startswith(prefix) for prefix in self.unasked_prefixes)


def model_request(config: Config, session: dict[str, Any], messages: list[dict[str, Any]]) -> tuple[int, str]:
    """Make one streamed chat request as the session's model; returns the status and the text."""
    provider, model = json.loads(session["model"])
    del provider
    status, raw = post(
        f"{config.base_url}/chat/completions",
        {"Authorization": f"Bearer {config.key}"},
        {"model": model, "messages": messages, "stream": True, "stream_options": {"include_usage": True}, "max_completion_tokens": 8192},
        timeout=60.0,
    )
    text = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if not line.startswith("data:") or line.strip() == "data: [DONE]":
            continue
        try:
            chunk = json.loads(line[5:])
        except ValueError:
            continue
        for choice in chunk.get("choices") or []:
            content = (choice.get("delta") or {}).get("content")
            if content:
                text.append(content)
    return status, "".join(text) if status == 200 else raw.decode("utf-8", errors="replace")


def permitted(config: Config, session_id: str, call_id: str, name: str) -> str:
    """Ask the client about a call when the guard says so; returns allowed, rejected or cancelled."""
    if not config.asks(name):
        return "allowed"
    return asked(session_id, call_id)


def asked(session_id: str, call_id: str) -> str:
    """Ask the client about a call; returns allowed, rejected or cancelled."""
    answer = ask_client(
        "session/request_permission",
        {
            "sessionId": session_id,
            "toolCall": {"toolCallId": call_id},
            "options": [
                {"optionId": "allow-once", "name": "Allow once", "kind": "allow_once"},
                {"optionId": "reject-once", "name": "Reject", "kind": "reject_once"},
            ],
        },
    )
    decided = (answer.get("result") or {}).get("outcome") or {}
    if decided.get("optionId") == "allow-once":
        return "allowed"
    return "rejected" if decided.get("optionId") == "reject-once" else "cancelled"


def run_prompt(config: Config, session_id: str, session: dict[str, Any], text: str) -> dict[str, Any]:
    cancel: threading.Event = session["cancel"]
    cancel.clear()
    fails_stopped = False
    steps = [line[1:].strip() for line in text.splitlines() if line.startswith("!")]
    if not steps:
        update(session_id, {"sessionUpdate": "agent_message_chunk", "messageId": uuid.uuid4().hex, "content": {"type": "text", "text": f"echo: {text}"}})
    for number, step in enumerate(steps):
        if cancel.is_set() or session["closed"]:
            break
        verb, _, rest = step.partition(" ")
        rest = rest.strip()
        if verb == "say":
            update(session_id, {"sessionUpdate": "agent_message_chunk", "messageId": uuid.uuid4().hex, "content": {"type": "text", "text": rest}})
        elif verb == "thought":
            update(session_id, {"sessionUpdate": "agent_thought_chunk", "messageId": uuid.uuid4().hex, "content": {"type": "text", "text": rest}})
        elif verb == "tool":
            name, _, raw = rest.partition(" ")
            arguments = json.loads(raw or "{}")
            call_id = f"call_{session_id[:4]}_{number}"
            update(session_id, {"sessionUpdate": "tool_call", "toolCallId": call_id, "title": name, "kind": "other", "status": "in_progress", "rawInput": arguments})
            outcome = permitted(config, session_id, call_id, name)
            record("tool", session=session_id, name=name, outcome=outcome)
            if outcome == "allowed":
                result = {"status": "completed", "text": f"ran {name}"}
            elif outcome == "rejected":
                result = {"status": "failed", "text": f'Error: the user rejected tool "{name}"'}
            else:
                result = {"status": "failed", "text": f'Error: tool "{name}" was cancelled'}
            update(session_id, {"sessionUpdate": "tool_call_update", "toolCallId": call_id, "status": result["status"], "content": [{"type": "content", "content": {"type": "text", "text": result["text"]}}]})
        elif verb in ("host", "gateway"):
            tool, _, raw = rest.partition(" ")
            arguments = json.loads(raw or "{}")
            call_id = f"call_{session_id[:4]}_{number}"
            update(session_id, {"sessionUpdate": "tool_call", "toolCallId": call_id, "title": f"mcp__{verb}__{tool}", "kind": "other", "status": "in_progress", "rawInput": arguments})
            service = session["services"].get(verb)
            outcome = permitted(config, session_id, call_id, f"mcp__{verb}__{tool}")
            try:
                if outcome != "allowed":
                    raise RuntimeError(f'the user rejected tool "mcp__{verb}__{tool}"' if outcome == "rejected" else "cancelled")
                if service is None:
                    raise RuntimeError(f"no service named {verb}")
                answer = service.rpc("tools/call", {"name": tool, "arguments": arguments}, timeout=float(os.environ.get("FAKE_DSH_TOOL_SECONDS", "60")))
                result = answer.get("result") or {"isError": True, "content": [{"type": "text", "text": json.dumps(answer.get("error"))}]}
                failed = bool(result.get("isError"))
                # dsh joins the text blocks of a result with a line break.
                body = "\n".join(part.get("text", "") for part in result.get("content") or [] if part.get("type") == "text")
                if failed:
                    body = f"Error: {body}"
            except Exception as exc:  # noqa: BLE001 - what dsh shows of a failed call
                failed, body = True, f"Error: {exc}"
            record("service-call", session=session_id, service=verb, tool=tool, failed=failed, text=body, asked=config.asks(f"mcp__{verb}__{tool}"))
            update(session_id, {"sessionUpdate": "tool_call_update", "toolCallId": call_id, "status": "failed" if failed else "completed", "content": [{"type": "content", "content": {"type": "text", "text": body}}]})
        elif verb in ("model", "model-after-report"):
            if verb == "model":
                messages = [{"role": "user", "content": rest}]
            else:
                messages = [
                    {"role": "user", "content": "finish up"},
                    {"role": "assistant", "content": "", "tool_calls": [{"id": "call_r", "type": "function", "function": {"name": "mcp__host__report", "arguments": rest}}]},
                    {"role": "tool", "tool_call_id": "call_r", "content": "report delivered (ready)."},
                ]
            status, answer = model_request(config, session, messages)
            record("model", session=session_id, status=status, text=answer)
            if status != 200:
                raise RpcError(-32603, "Internal error", f"turn failed: HTTP {status}: {answer[:300]}")
            update(session_id, {"sessionUpdate": "agent_message_chunk", "messageId": uuid.uuid4().hex, "content": {"type": "text", "text": answer}})
            update(session_id, {"sessionUpdate": "usage_update", "used": 1234, "size": 128000})
        elif verb == "ask-unknown":
            record("unknown-ask", session=session_id, outcome=asked(session_id, "call_never_announced"))
        elif verb == "ask-finished":
            call_id = f"call_{session_id[:4]}_{number}"
            update(session_id, {"sessionUpdate": "tool_call", "toolCallId": call_id, "title": rest, "kind": "other", "status": "in_progress", "rawInput": {}})
            update(session_id, {"sessionUpdate": "tool_call_update", "toolCallId": call_id, "status": "completed", "content": []})
            record("finished-ask", session=session_id, outcome=asked(session_id, call_id))
        elif verb == "sleep":
            cancel.wait(float(rest or "1"))
        elif verb == "until":
            # The turn stays open until the test lets it go by writing this file
            # (or stops it); a test that never does is not waited for for ever.
            let_go, deadline = Path(rest), time.monotonic() + 60
            while not let_go.exists() and time.monotonic() < deadline and not cancel.wait(0.02):
                pass
        elif verb == "fail":
            raise RpcError(-32603, "Internal error", f"turn failed: {rest}")
        elif verb == "stopped-fails":
            # From here on a stop is answered with an error, and not by
            # saying that the turn was cancelled.
            fails_stopped = True
        elif verb == "crash":
            os._exit(3)
    if fails_stopped and cancel.is_set():
        raise RpcError(-32603, "Internal error", "turn failed: stopped")
    return {"stopReason": "cancelled" if cancel.is_set() or session["closed"] else "end_turn"}


def handle(config: Config, message: dict[str, Any]) -> None:
    method = message["method"]
    params = message.get("params") or {}
    request_id = message.get("id")
    try:
        result = dispatch(config, method, params)
    except RpcError as exc:
        error: dict[str, Any] = {"code": exc.code, "message": exc.message}
        if exc.details is not None:
            error["data"] = {"details": exc.details}
        send({"jsonrpc": "2.0", "id": request_id, "error": error})
        return
    except Exception as exc:  # noqa: BLE001 - a bug in the fake is shown to the test
        send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32603, "message": f"fake dsh: {type(exc).__name__}: {exc}"}})
        return
    send({"jsonrpc": "2.0", "id": request_id, "result": result})


def dispatch(config: Config, method: str, params: dict[str, Any]) -> dict[str, Any]:
    if method == "initialize":
        capabilities: dict[str, Any] = {"close": {}, "list": {}}
        if not os.environ.get("FAKE_DSH_NO_RESUME"):
            capabilities["resume"] = {}
        record("initialize", params=params, cwd=os.getcwd(), argv=sys.argv[1:])
        return {
            "protocolVersion": 1,
            "agentInfo": {"name": "fake-dsh", "version": "0"},
            "agentCapabilities": {"mcpCapabilities": {"http": True}, "sessionCapabilities": capabilities},
            "authMethods": [],
        }
    if method in ("session/new", "session/resume"):
        cwd = params["cwd"]
        if not os.path.isabs(cwd):
            raise RpcError(-32602, f"Invalid params: cwd must be an absolute path: {cwd}")
        if method == "session/resume":
            session_id = params["sessionId"]
            record("resume-attempt", session=session_id, cwd=cwd)
            if os.environ.get("FAKE_DSH_RESUME") == "broken":
                # As dsh answers when something in it fails: nothing of what it was.
                raise RpcError(-32603, "Internal error")
            known = known_sessions().get(session_id)
            if known is None:
                raise RpcError(-32602, f"Invalid params: session is not resumable: {session_id}")
            if session_id in _sessions:
                raise RpcError(-32602, f"Invalid params: session is already active: {session_id}")
            if os.path.normcase(known["cwd"]) != os.path.normcase(cwd):
                raise RpcError(-32602, f"Invalid params: session cwd does not match: {cwd}")
        else:
            if os.environ.get("FAKE_DSH_NEW") == "crash":
                os._exit(3)
            session_id = str(uuid.uuid4())
        services = connect_services(params.get("mcpServers") or [])
        _sessions[session_id] = {
            "cwd": cwd,
            "services": services,
            "model": json.dumps([config.provider, config.models[0]], separators=(",", ":")),
            "cancel": threading.Event(),
            "closed": False,
            "busy": False,
        }
        remember(session_id, cwd)
        record(method, session=session_id, cwd=cwd, services={name: service.tools for name, service in services.items()})
        return {"sessionId": session_id, "configOptions": []} if method == "session/new" else {"configOptions": []}
    session_id = params.get("sessionId")
    session = _sessions.get(session_id)
    if session is None:
        raise RpcError(-32602, f"Invalid params: unknown session: {session_id}")
    if method == "session/set_config_option":
        allowed = {json.dumps([config.provider, model], separators=(",", ":")) for model in config.models}
        if params.get("configId") != "model" or params.get("value") not in allowed:
            raise RpcError(-32602, f"Invalid params: unknown model option: {params.get('value')}")
        session["model"] = params["value"]
        record(method, session=session_id, value=params["value"])
        return {"configOptions": []}
    if method == "session/close":
        behaviour = os.environ.get("FAKE_DSH_CLOSE")
        if behaviour == "crash":
            os._exit(3)
        if behaviour == "refuse":
            record("close-refused", session=session_id)
            raise RpcError(-32603, "Internal error", "the session is busy")
        if behaviour == "slow":
            time.sleep(0.5)
        session["closed"] = True
        session["cancel"].set()
        _sessions.pop(session_id, None)
        record(method, session=session_id)
        return {}
    if method == "session/prompt":
        if session["busy"]:
            raise RpcError(-32602, "Invalid params: a prompt is already in flight for this session")
        session["busy"] = True
        try:
            text = "".join(block.get("text", "") for block in params.get("prompt") or [] if block.get("type") == "text")
            record(method, session=session_id, text=text)
            return run_prompt(config, session_id, session, text)
        finally:
            session["busy"] = False
    raise RpcError(-32601, f"Method not found: {method}")


def main() -> None:
    arguments = sys.argv[1:]
    config = Config(Path(arguments[arguments.index("--patch") + 1]))
    HOME.mkdir(parents=True, exist_ok=True)
    if config.guard_inserted and os.environ.get("FAKE_DSH_GUARD") != "off":
        sys.stderr.write("nekoclaw-guard ready\n")
        sys.stderr.flush()
    for raw in sys.stdin.buffer:
        if not raw.strip():
            continue
        message = json.loads(raw.decode("utf-8"))
        if "method" not in message:
            waiting = _pending.pop(message.get("id"), None)
            if waiting is not None:
                waiting[1].update(message)
                waiting[0].set()
            else:
                # The answer to a question that ended with its turn.
                record("late-answer", result=message.get("result"), error=message.get("error"))
            continue
        if "id" not in message:
            if message["method"] == "session/cancel":
                session = _sessions.get((message.get("params") or {}).get("sessionId"))
                if session is not None:
                    record("session/cancel", session=message["params"]["sessionId"])
                    session["cancel"].set()
                    # A question still open is over with the turn.
                    for request_id in list(_pending):
                        waiting = _pending.pop(request_id, None)
                        if waiting is not None:
                            waiting[1].update({"result": {"outcome": {"outcome": "cancelled"}}})
                            waiting[0].set()
            continue
        threading.Thread(target=handle, args=(config, message), daemon=True).start()
    # The client closed the connection: dsh closes its sessions and exits.
    time.sleep(float(os.environ.get("FAKE_DSH_EXIT_DELAY") or 0))
    record("exit")


if __name__ == "__main__":
    main()
