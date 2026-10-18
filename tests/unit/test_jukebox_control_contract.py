import asyncio
import json
import shutil
import textwrap
import types
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from tests.node_harness import run_node_script


ROOT = Path(__file__).resolve().parents[2]
APP_WEBSOCKET_PATH = ROOT / "static" / "app" / "app-websocket.js"


class _FakePushSocket:
    def __init__(self):
        self.events = []

    def send_json(self, event, flags=None):
        self.events.append(json.loads(json.dumps(event)))


@dataclass
class _WebSocket:
    payloads: list = field(default_factory=list)

    async def send_json(self, payload):
        self.payloads.append(payload)


@dataclass
class _Manager:
    websocket: _WebSocket


@pytest.mark.asyncio
async def test_jukebox_event_bus_delivers_canonical_command_to_one_session(monkeypatch):
    """Drive the real event-bus handler instead of reading its source.

    A changed payload shape or a delivery to the wrong websocket fails here;
    reformatting the handler does not.
    """
    from app import main_server
    from app.main_server import character_runtime

    target = _Manager(_WebSocket())
    bystander = _Manager(_WebSocket())
    monkeypatch.setattr(
        character_runtime,
        "_get_session_manager",
        lambda name: target if name == "cat" else None,
    )
    monkeypatch.setattr(
        character_runtime,
        "_iter_session_managers",
        lambda: iter([("cat", target), ("dog", bystander)]),
    )

    await main_server._handle_agent_event(
        {
            "event_type": "jukebox_control",
            "lanlan_name": "cat",
            "action": "  PLAY  ",
            "query": "桃园",
            "value": 50,
            "mode": "random",
            "source": "jukebox_controller",
            # Legacy aliases the wire contract deliberately drops.
            "song": "legacy-song",
            "volume": 11,
            "delta": 7,
        }
    )

    assert target.websocket.payloads == [
        {
            "type": "jukebox_control",
            "command": {
                "action": "play",
                "query": "桃园",
                "value": 50,
                "mode": "random",
            },
            "source": "jukebox_controller",
        }
    ]
    # Jukebox control mutates one local playback runtime: it must never fan out.
    assert bystander.websocket.payloads == []


@pytest.mark.asyncio
async def test_jukebox_event_bus_drops_control_without_target_session(monkeypatch):
    from app import main_server
    from app.main_server import character_runtime

    bystander = _Manager(_WebSocket())
    monkeypatch.setattr(character_runtime, "_get_session_manager", lambda _name: None)
    monkeypatch.setattr(
        character_runtime,
        "_iter_session_managers",
        lambda: iter([("dog", bystander)]),
    )

    await main_server._handle_agent_event(
        {
            "event_type": "jukebox_control",
            "lanlan_name": "",
            "action": "next",
        }
    )

    assert bystander.websocket.payloads == []


def _websocket_jukebox_handler_source() -> str:
    """Cut the real handler (and the queue it serializes on) out of the bundle."""
    source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")
    queue_decl = "let _jukeboxControlQueue = Promise.resolve();"
    assert queue_decl in source, "jukebox control queue declaration moved"
    seq_decl = "let _jukeboxSupersedeGeneration = 0;"
    assert seq_decl in source, "jukebox supersede generation declaration moved"
    queue_decl = queue_decl + "\n" + seq_decl
    # Extract routing decisions together with handlers: both determine who executes this instruction.
    handler_start = "    function isSecondaryJukeboxControlSurface() {"
    handler_end = "    function readNewUserIcebreakerStore() {"
    assert handler_start in source and handler_end in source
    handler = handler_start + source.split(handler_start, 1)[1].split(handler_end, 1)[0]
    return queue_decl + "\n" + handler


def _run_node(script: str):
    node_path = shutil.which("node")
    if not node_path:
        pytest.skip("node is not installed; skipping jukebox control harness test")
    return run_node_script(
        node_path,
        script,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def test_jukebox_websocket_handler_forwards_canonical_command_and_serializes():
    """Run the real handler under node.

    Pins what the old substring assertions could not: the exact command object
    handed to ``executeControl``, and that a second command waits for the first
    to settle instead of racing it.
    """
    harness = (
        textwrap.dedent(
            """
            const emit = console.log;
            const window = { Jukebox: null, location: { pathname: '/' } };
            globalThis.window = window;
            """
        )
        + _websocket_jukebox_handler_source()
        + textwrap.dedent(
            """
            (async () => {
              const seen = [];
              const order = [];
              let releaseFirst;
              const firstGate = new Promise(resolve => { releaseFirst = resolve; });
              let call = 0;
              window.Jukebox = {
                executeControl: (command) => {
                  seen.push(command);
                  call += 1;
                  if (call === 1) {
                    order.push('first-start');
                    return firstGate.then(() => { order.push('first-end'); });
                  }
                  order.push('second-start');
                  return Promise.reject(new Error('boom'));
                }
              };

              handleJukeboxControlResponse({
                type: 'jukebox_control',
                command: { action: 'play', query: 'peach', value: 50, mode: 'random',
                           song: 'legacy', name: 'legacy', volume: 9, delta: 3 },
                source: 'jukebox_controller'
              });
              handleJukeboxControlResponse({
                type: 'jukebox_control',
                command: { action: 'next' }
              });

              await new Promise(resolve => setTimeout(resolve, 20));
              const startedEarly = order.includes('second-start');
              releaseFirst();
              await new Promise(resolve => setTimeout(resolve, 20));

              // A rejected command must not wedge the queue for the next one.
              window.Jukebox.executeControl = (command) => {
                seen.push(command);
                order.push('third-start');
                return Promise.resolve();
              };
              handleJukeboxControlResponse({ command: { action: 'stop' } });
              await new Promise(resolve => setTimeout(resolve, 20));

              emit(JSON.stringify({ seen, order, startedEarly }));
            })();
            """
        )
    )
    result = _run_node(harness)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])

    assert payload["seen"][0] == {
        "action": "play",
        "query": "peach",
        "value": 50,
        "mode": "random",
        "headless": True,
    }
    # An absent value/mode stays absent on the wire (JS undefined), never a
    # legacy alias: the handler forwards only the canonical four plus headless.
    assert payload["seen"][1] == {"action": "next", "query": "", "headless": True}
    for command in payload["seen"]:
        assert set(command) <= {"action", "query", "value", "mode", "headless"}
    # Serialized, not concurrent: the second command starts only after the
    # first settles, and a rejection does not wedge the queue.
    assert payload["startedEarly"] is False
    assert payload["order"] == [
        "first-start",
        "first-end",
        "second-start",
        "third-start",
    ]


def test_jukebox_plugin_normalizes_and_rejects_actions():
    """Exercise the plugin's own validation and the part it actually pushes."""
    from plugin.plugins.jukebox_controller import JukeboxControllerPlugin
    from plugin.sdk.plugin import Err

    plugin = JukeboxControllerPlugin.__new__(JukeboxControllerPlugin)
    pushed = []
    plugin.ctx = types.SimpleNamespace(
        push_message=lambda **kwargs: pushed.append(kwargs),
        _current_lanlan="cat",
    )

    result = asyncio.run(
        plugin.control_jukebox(
            action="  PLAY  ", query="  桃源  ", mode=" RANDOM ",
            _ctx={"lanlan_name": "cat"},
        )
    )
    assert result.value["action"] == "play"
    assert result.value["query"] == "桃源"
    assert result.value["mode"] == "random"
    assert pushed[0]["parts"] == [
        {
            "type": "ui_action",
            "action": "jukebox_control",
            "jukebox_action": "play",
            "query": "桃源",
            "value": None,
            "mode": "random",
        }
    ]
    assert pushed[0]["visibility"] == ["chat"]
    assert pushed[0]["ai_behavior"] == "blind"
    # The command is scoped to the character it was invoked for.
    assert pushed[0]["target_lanlan"] == "cat"

    for unsupported in ("skip", "shuffle", "", "pause "):
        rejected = asyncio.run(
            plugin.control_jukebox(action=unsupported, _ctx={"lanlan_name": "cat"})
        )
        assert isinstance(rejected, Err), unsupported
    # A rejected action must not reach the frontend at all.
    assert len(pushed) == 1


def test_jukebox_proactive_bridge_uses_canonical_control_keys(monkeypatch):
    from plugin.server.messaging import proactive_bridge

    if proactive_bridge.zmq is None:
        monkeypatch.setattr(proactive_bridge, "zmq", types.SimpleNamespace(NOBLOCK=1))

    push = _FakePushSocket()
    proactive_bridge.ProactiveBridge()._dispatch(
        {
            "plugin_id": "jukebox_controller",
            "time": "now",
            "metadata": {"query": "metadata-query", "song": "legacy-metadata-song"},
            "visibility": ["chat"],
            "ai_behavior": "blind",
            "parts": [
                {
                    "type": "ui_action",
                    "action": "jukebox_control",
                    "jukebox_action": "play",
                    "control": "stop",
                    "command": "next",
                    "query": "桃园",
                    "value": 50,
                    "mode": "random",
                    "song": "legacy-song",
                }
            ],
        },
        push,
    )

    assert push.events == [
        {
            "event_type": "jukebox_control",
            "lanlan_name": None,
            "action": "play",
            "query": "桃园",
            "value": 50,
            "mode": "random",
            "source": "jukebox_controller",
            "timestamp": "now",
        }
    ]

    metadata_only_push = _FakePushSocket()
    proactive_bridge.ProactiveBridge()._dispatch(
        {
            "plugin_id": "jukebox_controller",
            "time": "now",
            "metadata": {"query": "metadata-query"},
            "visibility": ["chat"],
            "ai_behavior": "blind",
            "parts": [
                {
                    "type": "ui_action",
                    "action": "jukebox_control",
                    "jukebox_action": "play",
                }
            ],
        },
        metadata_only_push,
    )

    assert metadata_only_push.events[0]["query"] is None
    assert metadata_only_push.events[0]["value"] is None
    assert metadata_only_push.events[0]["mode"] is None

    legacy_push = _FakePushSocket()
    proactive_bridge.ProactiveBridge()._dispatch(
        {
            "plugin_id": "jukebox_controller",
            "time": "now",
            "metadata": {"query": "metadata-query", "song": "legacy-metadata-song"},
            "visibility": ["chat"],
            "ai_behavior": "blind",
            "parts": [
                {
                    "type": "ui_action",
                    "action": "jukebox_control",
                    "control": "play",
                    "command": "next",
                    "song": "legacy-song",
                }
            ],
        },
        legacy_push,
    )

    assert legacy_push.events == []


def test_jukebox_plugin_rejects_incomplete_action_arguments():
    """Codex P2: a volume/mode command missing its argument must not report success.

    The browser rejects it as invalid_volume / invalid_playback_mode, but that
    verdict is asynchronous and never reaches the caller, so the model would
    tell the user a change was made that never happened.
    """
    from plugin.plugins.jukebox_controller import JukeboxControllerPlugin
    from plugin.sdk.plugin import Err

    plugin = JukeboxControllerPlugin.__new__(JukeboxControllerPlugin)
    pushed = []
    plugin.ctx = types.SimpleNamespace(
        push_message=lambda **kwargs: pushed.append(kwargs),
        _current_lanlan="cat",
    )

    rejected = [
        ("set_volume", {}),
        ("set_volume", {"value": ""}),
        ("set_volume", {"value": "loud"}),
        ("set_volume", {"value": 140}),
        ("set_volume", {"value": -1}),
        # bool subclasses int: float(True)==1.0 fools purely numeric validation.
        ("set_volume", {"value": True}),
        ("set_volume", {"value": False}),
        ("adjust_volume", {"value": True}),
        ("adjust_volume", {}),
        ("adjust_volume", {"value": None}),
        ("adjust_volume", {"value": -140}),
        ("set_mode", {}),
        ("set_mode", {"mode": "shuffle"}),
    ]
    for action, kwargs in rejected:
        result = asyncio.run(
            plugin.control_jukebox(action=action, _ctx={"lanlan_name": "cat"}, **kwargs)
        )
        assert isinstance(result, Err), (action, kwargs)
    # Rejected calls must never reach the frontend.
    assert pushed == []

    accepted = [
        ("set_volume", {"value": 0}),
        ("set_volume", {"value": 35}),
        ("set_volume", {"value": "42"}),
        ("adjust_volume", {"value": -20}),
        ("adjust_volume", {"value": 0.5}),
        ("set_mode", {"mode": " RANDOM "}),
        # Parameterless actions are unaffected by this validation.
        ("next", {}),
        ("stop", {}),
    ]
    for action, kwargs in accepted:
        result = asyncio.run(
            plugin.control_jukebox(action=action, _ctx={"lanlan_name": "cat"}, **kwargs)
        )
        assert not isinstance(result, Err), (action, kwargs)
    assert len(pushed) == len(accepted)


def test_jukebox_plugin_scopes_command_to_the_invoking_context():
    """Codex P1: ctx._current_lanlan is shared across concurrent triggers.

    Each invocation carries its own ``_ctx``; that must win over the value
    another trigger may have left on the shared plugin context.
    """
    from plugin.plugins.jukebox_controller import JukeboxControllerPlugin

    plugin = JukeboxControllerPlugin.__new__(JukeboxControllerPlugin)
    pushed = []
    # Another concurrent invocation changes shared context to a different character.
    plugin.ctx = types.SimpleNamespace(
        push_message=lambda **kwargs: pushed.append(kwargs),
        _current_lanlan="dog",
    )

    asyncio.run(
        plugin.control_jukebox(action="next", _ctx={"lanlan_name": "cat"})
    )
    assert pushed[-1]["target_lanlan"] == "cat"

    # Explicit target_lanlan has highest priority.
    asyncio.run(
        plugin.control_jukebox(
            action="next", target_lanlan="  fox  ", _ctx={"lanlan_name": "cat"}
        )
    )
    assert pushed[-1]["target_lanlan"] == "fox"

    # Missing ownership in this call must never fall back to shared context, which can only contain another invocation's
    # character name (its sole writer is some call's _ctx["lanlan_name"]); using it sends this instruction
    # to someone else's session. Prefer explicit failure.
    from plugin.sdk.plugin import Err as _Err

    before = len(pushed)
    orphan = asyncio.run(plugin.control_jukebox(action="next"))
    assert isinstance(orphan, _Err)
    assert "invocation-local target" in str(orphan.error)
    # Failed instructions must never be pushed.
    assert len(pushed) == before


def test_jukebox_control_routes_to_exactly_one_executor():
    """#4: in multi-window Electron the same message reaches several windows.

    RAW_MESSAGE forwarding hands one WebSocket message to the pet window and
    the chat window, while the standalone jukebox window owns the player the
    user can actually see. Exactly one of them may act on it.
    """
    harness = (
        textwrap.dedent(
            """
            const emit = console.log;
            const window = { Jukebox: null, location: { pathname: '/' } };
            globalThis.window = window;
            """
        )
        + _websocket_jukebox_handler_source()
        + textwrap.dedent(
            """
            (async () => {
              const log = [];
              const settle = () => new Promise(resolve => setTimeout(resolve, 5));

              const setup = (opts) => {
                window.__NEKO_MULTI_WINDOW__ = opts.multiWindow === true;
                window.location.pathname = opts.pathname;
                window.Jukebox = {
                  executeControl: (command) => {
                    log.push({ scenario: opts.scenario, via: 'local', command });
                    return Promise.resolve({ ok: true });
                  }
                };
                window.__nekoJukeboxLoader = {
                  hasControlOwner: () => opts.ownerAlive === true,
                  forwardControl: (command) => {
                    log.push({ scenario: opts.scenario, via: 'forward', command });
                    return opts.forwardFails
                      ? Promise.resolve({ ok: false, message: 'jukebox_owner_timeout' })
                      : Promise.resolve({ ok: true });
                  }
                };
              };

              const fire = async (opts) => {
                setup(opts);
                handleJukeboxControlResponse({
                  type: 'jukebox_control',
                  command: { action: 'next' }
                });
                await settle();
              };

              // 独立点唱机窗口开着：必须转发，不能本地执行
              await fire({ scenario: 'owner', ownerAlive: true, pathname: '/' });
              // 没有拥有者 + 多窗口下的 chat 窗口：让位，什么都不做
              await fire({ scenario: 'chat', ownerAlive: false, multiWindow: true, pathname: '/chat' });
              await fire({ scenario: 'chat_full', ownerAlive: false, multiWindow: true, pathname: '/chat_full' });
              // 没有拥有者 + 主窗口：本地执行
              await fire({ scenario: 'pet', ownerAlive: false, multiWindow: true, pathname: '/' });
              // 网页端单窗口（没有多窗口标志）即便路径是 /chat 也要本地执行
              await fire({ scenario: 'web_chat', ownerAlive: false, multiWindow: false, pathname: '/chat' });
              // 转发失败不能回落本地：那会在隐藏窗口里再起一条音轨
              await fire({ scenario: 'forward_fails', ownerAlive: true, forwardFails: true, pathname: '/' });

              emit(JSON.stringify(log));
            })();
            """
        )
    )
    result = _run_node(harness)
    assert result.returncode == 0, result.stderr
    log = json.loads(result.stdout.strip().splitlines()[-1])
    routed = [(entry["scenario"], entry["via"]) for entry in log]

    assert routed == [
        ("owner", "forward"),
        ("pet", "local"),
        ("web_chat", "local"),
        ("forward_fails", "forward"),
    ], routed
    # Neither yielding chat scenario should have any records.
    assert not [entry for entry in log if entry["scenario"].startswith("chat")]
    # Forward the normalized command, not the original response.
    assert log[0]["command"] == {"action": "next", "query": "", "headless": True}


def test_jukebox_control_defers_from_secondary_window_even_with_an_owner():
    """CodeRabbit Major: deferral must not hang off whether an owner exists.

    Every character window that receives the forwarded message can see the same
    owner, so gating the deferral on "no owner" made both of them forward and
    the owner run the command twice.
    """
    harness = (
        textwrap.dedent(
            """
            const emit = console.log;
            const window = { Jukebox: null, location: { pathname: '/chat' } };
            globalThis.window = window;
            """
        )
        + _websocket_jukebox_handler_source()
        + textwrap.dedent(
            """
            (async () => {
              const log = [];
              window.__NEKO_MULTI_WINDOW__ = true;
              window.Jukebox = {
                executeControl: (c) => { log.push(['local', c.action]); return Promise.resolve({ ok: true }); }
              };
              window.__nekoJukeboxLoader = {
                hasControlOwner: () => true,
                forwardControl: (c) => { log.push(['forward', c.action]); return Promise.resolve({ ok: true }); }
              };

              handleJukeboxControlResponse({ command: { action: 'adjust_volume', value: 10 } });
              handleJukeboxControlResponse({ command: { action: 'stop' } });
              await new Promise(resolve => setTimeout(resolve, 20));
              emit(JSON.stringify(log));
            })();
            """
        )
    )
    result = _run_node(harness)
    assert result.returncode == 0, result.stderr
    # Yield even with an owner: send nothing locally.
    assert json.loads(result.stdout.strip().splitlines()[-1]) == []


def test_jukebox_control_resolves_ownership_when_the_command_runs():
    """Codex P2: ownership snapshotted outside the queue goes stale.

    The standalone window can open or close while an earlier command occupies
    the serialized queue.
    """
    harness = (
        textwrap.dedent(
            """
            const emit = console.log;
            const window = { Jukebox: null, location: { pathname: '/' } };
            globalThis.window = window;
            """
        )
        + _websocket_jukebox_handler_source()
        + textwrap.dedent(
            """
            (async () => {
              const log = [];
              let ownerAlive = false;
              let releaseFirst;
              const firstGate = new Promise(resolve => { releaseFirst = resolve; });

              window.Jukebox = {
                executeControl: (c) => {
                  log.push(['local', c.action]);
                  return c.action === 'play' ? firstGate : Promise.resolve({ ok: true });
                }
              };
              window.__nekoJukeboxLoader = {
                hasControlOwner: () => ownerAlive,
                forwardControl: (c) => { log.push(['forward', c.action]); return Promise.resolve({ ok: true }); }
              };

              // 第一条在没有拥有者时进来 -> 本地执行，并卡住队列
              handleJukeboxControlResponse({ command: { action: 'play', query: 'x' } });
              await new Promise(resolve => setTimeout(resolve, 10));
              // 第二条排队期间独立点唱机窗口打开了
              handleJukeboxControlResponse({ command: { action: 'next' } });
              ownerAlive = true;
              releaseFirst();
              await new Promise(resolve => setTimeout(resolve, 20));

              emit(JSON.stringify(log));
            })();
            """
        )
    )
    result = _run_node(harness)
    assert result.returncode == 0, result.stderr
    log = json.loads(result.stdout.strip().splitlines()[-1])
    # No owner while queued, but one exists when execution starts -> forward, never start separately in the hidden window.
    assert log == [["local", "play"], ["forward", "next"]], log


def test_jukebox_stop_preempts_a_queued_playback():
    """Codex P2: a cancel action queued behind what it cancels is useless.

    A play stuck on a slow animation load kept the stop -- and every command
    behind it -- waiting, while the audio was already audible.
    """
    harness = (
        textwrap.dedent(
            """
            const emit = console.log;
            const window = { Jukebox: null, location: { pathname: '/' } };
            globalThis.window = window;
            """
        )
        + _websocket_jukebox_handler_source()
        + textwrap.dedent(
            """
            (async () => {
              const order = [];
              let releasePlay;
              let cancelled = 0;
              const playGate = new Promise(resolve => { releasePlay = resolve; });
              window.__nekoJukeboxLoader = { hasControlOwner: () => false };
              window.Jukebox = {
                cancelActivePlayback: () => { cancelled += 1; order.push('cancel'); },
                executeControl: (c) => {
                  order.push(c.action);
                  if (c.action === 'play') return playGate;
                  return Promise.resolve({ ok: true });
                }
              };

              // 先让 play 真正跑起来并卡在慢动画加载上，再来 stop —— 这才是评审
              // 描述的场景（同一拍到达的话，作废甚至发生在 play 起步之前）。
              handleJukeboxControlResponse({ command: { action: 'play', query: 'x' } });
              await new Promise(resolve => setTimeout(resolve, 10));
              const beforeStop = order.slice();
              handleJukeboxControlResponse({ command: { action: 'stop' } });
              handleJukeboxControlResponse({ command: { action: 'next' } });
              await new Promise(resolve => setTimeout(resolve, 20));
              const beforeRelease = order.slice();
              releasePlay();
              await new Promise(resolve => setTimeout(resolve, 20));
              emit(JSON.stringify({ beforeStop, beforeRelease, order, cancelled }));
            })();
            """
        )
    )
    result = _run_node(harness)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    # play is itself a replacement command, so invalidate locally on arrival (nothing playing yet).
    assert payload["beforeStop"] == ["cancel", "play"]
    # Invalidation is immediate: audio stops while play still waits for animation loading, without awaiting settlement.
    # stop and the subsequent next each add one, totaling three.
    assert payload["cancelled"] == 3
    assert payload["beforeRelease"] == ["cancel", "play", "cancel", "cancel"]
    # Preserve order: stop still follows the play it cancels, followed by next.
    assert payload["order"] == [
        "cancel", "play", "cancel", "cancel", "stop", "next",
    ]


def test_jukebox_stop_cancels_on_the_owner_when_one_is_present():
    """CodeRabbit Major: a local cancel cannot reach playback running elsewhere.

    With a standalone owner the in-flight play lives in that window, so the
    cancel has to be routed there too or the queued stop just waits out the
    forward timeout.
    """
    harness = (
        textwrap.dedent(
            """
            const emit = console.log;
            const window = { Jukebox: null, location: { pathname: '/' } };
            globalThis.window = window;
            """
        )
        + _websocket_jukebox_handler_source()
        + textwrap.dedent(
            """
            (async () => {
              const log = [];
              let ownerAlive = true;
              window.Jukebox = {
                cancelActivePlayback: () => log.push('local-cancel'),
                executeControl: (c) => { log.push('local:' + c.action); return Promise.resolve({ ok: true }); }
              };
              window.__nekoJukeboxLoader = {
                hasControlOwner: () => ownerAlive,
                cancelOnOwner: () => { log.push('owner-cancel'); return true; },
                forwardControl: (c) => { log.push('forward:' + c.action); return Promise.resolve({ ok: true }); }
              };

              handleJukeboxControlResponse({ command: { action: 'stop' } });
              await new Promise(resolve => setTimeout(resolve, 20));
              const withOwner = log.slice();

              log.length = 0;
              ownerAlive = false;
              handleJukeboxControlResponse({ command: { action: 'stop' } });
              await new Promise(resolve => setTimeout(resolve, 20));

              emit(JSON.stringify({ withOwner, withoutOwner: log }));
            })();
            """
        )
    )
    result = _run_node(harness)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    # With an owner, cancel on both owner and this window; ownership may change while commands queue,
    # and this window may hold earlier unclaimed playback. Forward stop itself in order.
    assert payload["withOwner"] == ["owner-cancel", "local-cancel", "forward:stop"]
    # Without an owner: cancel and execute locally.
    assert payload["withoutOwner"] == ["local-cancel", "local:stop"]


def test_jukebox_plugin_reports_a_rejected_submission():
    """Codex P2: a synchronously rejected push never reaches the frontend.

    Returning Ok for it makes the model tell the user the command was sent.
    An absent receipt still counts as success: older SDKs return None after a
    successful submission, so treating that as failure would report failures
    for commands that did go out.
    """
    from plugin.plugins.jukebox_controller import JukeboxControllerPlugin
    from plugin.sdk.plugin import Err

    def run(receipt):
        plugin = JukeboxControllerPlugin.__new__(JukeboxControllerPlugin)
        pushed = []

        def push(**kwargs):
            pushed.append(kwargs)
            return receipt

        plugin.ctx = types.SimpleNamespace(push_message=push, _current_lanlan=None)
        result = asyncio.run(
            plugin.control_jukebox(action="next", _ctx={"lanlan_name": "cat"})
        )
        return result, pushed

    for rejected in (
        {"ok": False, "submitted": False, "reason": "backpressure"},
        {"ok": False, "submitted": False, "reason": "transport_unavailable"},
        {"ok": False, "submitted": False, "reason": "payload_too_large"},
        {"submitted": False},
    ):
        result, pushed = run(rejected)
        assert isinstance(result, Err), rejected
        assert "not submitted" in str(result.error)
        assert len(pushed) == 1

    reason = str(run({"ok": False, "submitted": False, "reason": "backpressure"})[0].error)
    assert "backpressure" in reason

    # Successful receipts and missing receipts both count as sent.
    for accepted in ({"submitted": True}, None, object()):
        result, pushed = run(accepted)
        assert not isinstance(result, Err), accepted
        assert result.value["action"] == "next"
        assert len(pushed) == 1


def test_jukebox_handoff_cancels_the_local_playback_too():
    """Codex P2: ownership may change while a command is queued.

    A play that started locally before the standalone window opened is still
    audible in this window; cancelling only the newly announced owner leaves it
    unowned, and every later command is forwarded away from it.
    """
    harness = (
        textwrap.dedent(
            """
            const emit = console.log;
            const window = { Jukebox: null, location: { pathname: '/' } };
            globalThis.window = window;
            """
        )
        + _websocket_jukebox_handler_source()
        + textwrap.dedent(
            """
            (async () => {
              const log = [];
              let ownerAlive = false;
              let localAudible = false;
              let cancelled = false;

              window.Jukebox = {
                // 真实的 cancelActivePlayback 推进取消世代，在途的那条 play 随后
                // 在自己的闸门处作废——所以这里用 cancelled 建模，而不是去
                // resolve play 的 gate（那样反而会让 play 在取消之后才置真）。
                cancelActivePlayback: () => { log.push('local-cancel'); cancelled = true; },
                executeControl: (c) => {
                  log.push('local:' + c.action);
                  if (c.action !== 'play') return Promise.resolve({ ok: true });
                  // 这条 play 要 40ms 才走完，stop 必须落在它还在途的时候；
                  // 没被取消的话声音就留下了。
                  return new Promise(resolve => setTimeout(() => {
                    if (!cancelled) localAudible = true;
                    resolve({ ok: !cancelled });
                  }, 40));
                }
              };
              window.__nekoJukeboxLoader = {
                hasControlOwner: () => ownerAlive,
                cancelOnOwner: () => { log.push('owner-cancel'); return true; },
                forwardControl: (c) => { log.push('forward:' + c.action); return Promise.resolve({ ok: true }); }
              };

              // 本窗口先起播（此刻还没有独立点唱机窗口）。
              handleJukeboxControlResponse({ command: { action: 'play', query: 'x' } });
              await new Promise(resolve => setTimeout(resolve, 10));

              // 用户打开独立点唱机窗口，归属易主。
              ownerAlive = true;
              handleJukeboxControlResponse({ command: { action: 'stop' } });
              // 等到那条 play 也走完，确认它没有在 stop 之后把声音留下。
              await new Promise(resolve => setTimeout(resolve, 80));

              emit(JSON.stringify({ log, localAudible }));
            })();
            """
        )
    )
    result = _run_node(harness)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    # Replacement commands always invalidate locally first, including the opening play (a noop
    # with nothing playing). The key stop instant still cancels both potential players while forwarding
    # the command according to current ownership.
    assert payload["log"] == [
        "local-cancel", "local:play", "owner-cancel", "local-cancel", "forward:stop",
    ]
    # Key: this window's playback must not survive stop.
    assert payload["localAudible"] is False


def test_jukebox_control_waits_for_the_parts_instead_of_dropping():
    """Codex P2: bootstrap.js replaces the lazy facade with an empty object.

    executeControl only exists in the fifth of six serially loaded parts, so a
    command arriving mid-load hit the "no control entry point" branch and was
    discarded outright rather than waiting for the load already in flight.
    """
    harness = (
        textwrap.dedent(
            """
            const emit = console.log;
            const window = { Jukebox: null, location: { pathname: '/' } };
            globalThis.window = window;
            """
        )
        + _websocket_jukebox_handler_source()
        + textwrap.dedent(
            """
            (async () => {
              const executed = [];
              // 分片加载中：bootstrap.js 已经把门面换成空对象。
              window.Jukebox = {};
              let resolveLoad;
              const loadGate = new Promise(resolve => { resolveLoad = resolve; });
              window.__nekoJukeboxLoader = {
                hasControlOwner: () => false,
                load: () => loadGate
              };

              handleJukeboxControlResponse({ command: { action: 'play', query: 'x' } });
              await new Promise(resolve => setTimeout(resolve, 20));
              const executedWhileLoading = executed.length;

              // 第五个分片落地，executeControl 出现了。
              const loaded = {
                executeControl: (c) => { executed.push(c.action); return Promise.resolve({ ok: true }); }
              };
              window.Jukebox = loaded;
              resolveLoad(loaded);
              await new Promise(resolve => setTimeout(resolve, 20));

              emit(JSON.stringify({ executedWhileLoading, executed }));
            })();
            """
        )
    )
    result = _run_node(harness)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["executedWhileLoading"] == 0
    # Execute after the chunk loads rather than dropping the command.
    assert payload["executed"] == ["play"]


def test_jukebox_control_rechecks_ownership_after_the_parts_load():
    """CodeRabbit: the ownership snapshot is stale by the time the parts land.

    Waiting for the parts instead of dropping the command opened a window of
    hundreds of milliseconds to seconds.  The standalone jukebox window can be
    opened inside it, and executing locally afterwards starts a second, hidden
    audio track that nothing can reach.
    """
    harness = (
        textwrap.dedent(
            """
            const emit = console.log;
            const window = { Jukebox: null, location: { pathname: '/' } };
            globalThis.window = window;
            """
        )
        + _websocket_jukebox_handler_source()
        + textwrap.dedent(
            """
            (async () => {
              const log = [];
              let ownerAlive = false;
              // 分片加载中：bootstrap.js 已经把门面换成空对象。
              window.Jukebox = {};
              let resolveLoad;
              const loadGate = new Promise(resolve => { resolveLoad = resolve; });
              window.__nekoJukeboxLoader = {
                hasControlOwner: () => ownerAlive,
                forwardControl: (c) => { log.push('forward:' + c.action); return Promise.resolve({ ok: true }); },
                load: () => loadGate
              };

              handleJukeboxControlResponse({ command: { action: 'play', query: 'x' } });
              await new Promise(resolve => setTimeout(resolve, 20));

              // 加载还没完，用户打开了独立点唱机窗口。
              ownerAlive = true;

              const loaded = {
                executeControl: (c) => { log.push('local:' + c.action); return Promise.resolve({ ok: true }); }
              };
              window.Jukebox = loaded;
              resolveLoad(loaded);
              await new Promise(resolve => setTimeout(resolve, 20));

              emit(JSON.stringify({ log }));
            })();
            """
        )
    )
    result = _run_node(harness)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    # Forward to the newly appeared owner, never start a hidden audio track in this window.
    assert payload["log"] == ["forward:play"]


def test_chat_surface_can_receive_jukebox_control():
    """Codex P2: /chat and /chat_full drop every AI-issued jukebox command.

    ``dispatchJukeboxControl`` needs either ``window.Jukebox`` or the loader to
    do anything at all, and both come from ``jukebox-loader.js`` — which
    ``templates/chat.html`` did not load.  The deferral to the primary window
    only fires under ``__NEKO_MULTI_WINDOW__``, so on the web ``/chat`` route
    the chat page is the sole recipient and the command died there.
    """
    chat_html = (ROOT / "templates" / "chat.html").read_text(encoding="utf-8")
    index_html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    loader_tag = "/static/jukebox/jukebox-loader.js"
    assert loader_tag in index_html, "index.html 不再加载点歌台门面，这条判据要重写"
    assert loader_tag in chat_html, (
        "chat.html 必须加载 jukebox-loader.js，否则 /chat 收到的点播指令无处可去"
    )
    # Match index.html's order too: the facade depends on music_ui.js styles and the APlayer global.
    assert chat_html.index("/static/jukebox/music_ui.js") < chat_html.index(loader_tag)
    assert "/static/libs/APlayer.min.js" in chat_html

    # After loading the facade, web /chat (single-window) must really execute rather than yield.
    harness = (
        textwrap.dedent(
            """
            const emit = console.log;
            const window = { Jukebox: null, location: { pathname: '/chat' } };
            globalThis.window = window;
            """
        )
        + _websocket_jukebox_handler_source()
        + textwrap.dedent(
            """
            (async () => {
              const log = [];
              window.Jukebox = {
                executeControl: (c) => { log.push('local:' + c.action); return Promise.resolve({ ok: true }); }
              };
              window.__nekoJukeboxLoader = { hasControlOwner: () => false };
              handleJukeboxControlResponse({ command: { action: 'play', query: 'x' } });
              await new Promise(resolve => setTimeout(resolve, 20));
              emit(JSON.stringify({ log }));
            })();
            """
        )
    )
    result = _run_node(harness)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["log"] == ["local:play"]


def test_jukebox_stop_also_cancels_a_playback_command_still_in_the_queue():
    """Codex P2: the in-place cancel only reaches the command already running.

    play B queued behind a slow play A had captured no cancellation generation
    yet.  A stop arriving while B waited cancelled A, and B then read the
    already-advanced generation as current, started playing, and left the
    user's final stop queued behind it — new audio after the last stop.
    """
    harness = (
        textwrap.dedent(
            """
            const emit = console.log;
            const window = { Jukebox: null, location: { pathname: '/' } };
            globalThis.window = window;
            """
        )
        + _websocket_jukebox_handler_source()
        + textwrap.dedent(
            """
            (async () => {
              const log = [];
              let cancelGeneration = 0;
              window.Jukebox = {
                cancelActivePlayback: () => { cancelGeneration += 1; },
                executeControl: (c) => {
                  log.push(c.action + ':' + (c.query || ''));
                  if (c.action !== 'play') return Promise.resolve({ ok: true });
                  // A 很慢：B 和 stop 都在它还在途的时候到达。
                  const mine = cancelGeneration;
                  return new Promise(resolve => setTimeout(() => {
                    // 在途的那条自己会在闸门处作废；这里只建模「跑完了」。
                    resolve({ ok: mine === cancelGeneration });
                  }, 40));
                }
              };
              window.__nekoJukeboxLoader = { hasControlOwner: () => false };

              handleJukeboxControlResponse({ command: { action: 'play', query: 'A' } });
              await new Promise(resolve => setTimeout(resolve, 5));
              handleJukeboxControlResponse({ command: { action: 'play', query: 'B' } });
              handleJukeboxControlResponse({ command: { action: 'stop' } });
              await new Promise(resolve => setTimeout(resolve, 120));

              emit(JSON.stringify({ log }));
            })();
            """
        )
    )
    result = _run_node(harness)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    # stop displaced B while queued; it must not sound after the user's final stop.
    assert payload["log"] == ["play:A", "stop:"], payload["log"]


def test_jukebox_volume_survives_a_stop_that_arrives_behind_it():
    """The queued-command cancellation must not swallow unrelated commands.

    set_volume and set_mode have nothing to do with cancelling playback;
    dropping the whole queue on stop would eat a volume change the user sent
    first.  Only playback commands take an arrival number, so only they can be
    superseded.
    """
    harness = (
        textwrap.dedent(
            """
            const emit = console.log;
            const window = { Jukebox: null, location: { pathname: '/' } };
            globalThis.window = window;
            """
        )
        + _websocket_jukebox_handler_source()
        + textwrap.dedent(
            """
            (async () => {
              const log = [];
              window.Jukebox = {
                cancelActivePlayback: () => {},
                executeControl: (c) => {
                  log.push(c.action);
                  if (c.action !== 'play') return Promise.resolve({ ok: true });
                  return new Promise(resolve => setTimeout(() => resolve({ ok: true }), 40));
                }
              };
              window.__nekoJukeboxLoader = { hasControlOwner: () => false };

              handleJukeboxControlResponse({ command: { action: 'play', query: 'A' } });
              await new Promise(resolve => setTimeout(resolve, 5));
              handleJukeboxControlResponse({ command: { action: 'set_volume', value: 40 } });
              handleJukeboxControlResponse({ command: { action: 'stop' } });
              await new Promise(resolve => setTimeout(resolve, 120));

              emit(JSON.stringify({ log }));
            })();
            """
        )
    )
    result = _run_node(harness)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["log"] == ["play", "set_volume", "stop"], payload["log"]


def test_jukebox_play_supersedes_a_playback_command_still_in_the_queue():
    """Codex P2: only stop invalidated a queued playback command.

    With a slow play A running, play B waiting and play C arriving, C cancelled
    A in place but left B's generation matching, so the obsolete B started
    before C and delayed the replacement all over again.
    """
    harness = (
        textwrap.dedent(
            """
            const emit = console.log;
            const window = { Jukebox: null, location: { pathname: '/' } };
            globalThis.window = window;
            """
        )
        + _websocket_jukebox_handler_source()
        + textwrap.dedent(
            """
            (async () => {
              const log = [];
              window.Jukebox = {
                cancelActivePlayback: () => {},
                executeControl: (c) => {
                  log.push(c.action + ':' + (c.query || ''));
                  if (c.action !== 'play') return Promise.resolve({ ok: true });
                  return new Promise(resolve => setTimeout(() => resolve({ ok: true }), 40));
                }
              };
              window.__nekoJukeboxLoader = { hasControlOwner: () => false };

              handleJukeboxControlResponse({ command: { action: 'play', query: 'A' } });
              await new Promise(resolve => setTimeout(resolve, 5));
              handleJukeboxControlResponse({ command: { action: 'play', query: 'B' } });
              handleJukeboxControlResponse({ command: { action: 'play', query: 'C' } });
              await new Promise(resolve => setTimeout(resolve, 160));

              emit(JSON.stringify({ log }));
            })();
            """
        )
    )
    result = _run_node(harness)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    # C displaced B while queued; B must not sound ahead of C.
    assert payload["log"] == ["play:A", "play:C"], payload["log"]


def test_jukebox_next_does_not_swallow_the_play_queued_in_front_of_it():
    """The asymmetry is deliberate: next is relative, play and stop are absolute.

    next means "the song after the current one", so the play queued in front of
    it has to take effect first -- otherwise next advances from a stale
    position.  Only stop and play invalidate what is already queued.
    """
    harness = (
        textwrap.dedent(
            """
            const emit = console.log;
            const window = { Jukebox: null, location: { pathname: '/' } };
            globalThis.window = window;
            """
        )
        + _websocket_jukebox_handler_source()
        + textwrap.dedent(
            """
            (async () => {
              const log = [];
              window.Jukebox = {
                cancelActivePlayback: () => {},
                executeControl: (c) => {
                  log.push(c.action + ':' + (c.query || ''));
                  if (c.action !== 'play') return Promise.resolve({ ok: true });
                  return new Promise(resolve => setTimeout(() => resolve({ ok: true }), 40));
                }
              };
              window.__nekoJukeboxLoader = { hasControlOwner: () => false };

              handleJukeboxControlResponse({ command: { action: 'play', query: 'A' } });
              await new Promise(resolve => setTimeout(resolve, 5));
              handleJukeboxControlResponse({ command: { action: 'play', query: 'B' } });
              handleJukeboxControlResponse({ command: { action: 'next' } });
              await new Promise(resolve => setTimeout(resolve, 200));

              emit(JSON.stringify({ log }));
            })();
            """
        )
    )
    result = _run_node(harness)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["log"] == ["play:A", "play:B", "next:"], payload["log"]


def test_jukebox_supersession_is_rechecked_after_the_parts_load():
    """Codex P2: the in-place cancel cannot reach a command inside the load.

    bootstrap.js swaps the lazy facade for an empty object while the parts
    load, so a later stop's cancelActivePlayback() is a silent no-op -- the
    method does not exist yet.  The command had already passed runCommand's
    only generation check, so it started the obsolete playback once the parts
    landed, with the stop still queued behind it.
    """
    harness = (
        textwrap.dedent(
            """
            const emit = console.log;
            const window = { Jukebox: null, location: { pathname: '/' } };
            globalThis.window = window;
            """
        )
        + _websocket_jukebox_handler_source()
        + textwrap.dedent(
            """
            (async () => {
              const log = [];
              // 分片加载中：bootstrap.js 已经把门面换成空对象，注意它没有
              // cancelActivePlayback —— 这正是就地取消够不着的原因。
              window.Jukebox = {};
              let resolveLoad;
              const loadGate = new Promise(resolve => { resolveLoad = resolve; });
              window.__nekoJukeboxLoader = {
                hasControlOwner: () => false,
                load: () => loadGate
              };

              handleJukeboxControlResponse({ command: { action: 'play', query: 'x' } });
              await new Promise(resolve => setTimeout(resolve, 20));
              // 分片还没落地，用户就叫停了。
              handleJukeboxControlResponse({ command: { action: 'stop' } });

              const loaded = {
                executeControl: (c) => { log.push(c.action); return Promise.resolve({ ok: true }); }
              };
              window.Jukebox = loaded;
              resolveLoad(loaded);
              await new Promise(resolve => setTimeout(resolve, 20));

              emit(JSON.stringify({ log }));
            })();
            """
        )
    )
    result = _run_node(harness)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    # That play must not start sounding after the chunk loads; stop still executes normally.
    assert payload["log"] == ["stop"], payload["log"]


def test_jukebox_relative_navigation_cancel_does_not_silence():
    """The sender decides whether the cancellation silences the audio.

    next/previous may resolve to no target at all -- the head of the random
    history, an empty playlist -- and then the command is a no-op that has
    already stopped the music.  Absolute commands still silence: stop wants
    exactly that, and play replaces the audio anyway.
    """
    harness = (
        textwrap.dedent(
            """
            const emit = console.log;
            const window = { Jukebox: null, location: { pathname: '/' } };
            globalThis.window = window;
            """
        )
        + _websocket_jukebox_handler_source()
        + textwrap.dedent(
            """
            (async () => {
              const cancels = [];
              window.Jukebox = {
                cancelActivePlayback: (options) => {
                  cancels.push(options && options.silenceAudio);
                },
                executeControl: () => Promise.resolve({ ok: true })
              };
              window.__nekoJukeboxLoader = { hasControlOwner: () => false };

              for (const action of ['next', 'previous', 'stop', 'play']) {
                handleJukeboxControlResponse({ command: { action, query: 'x' } });
                await new Promise(resolve => setTimeout(resolve, 5));
              }
              emit(JSON.stringify({ cancels }));
            })();
            """
        )
    )
    result = _run_node(harness)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    # next / previous do not mute; stop / play mute.
    assert payload["cancels"] == [False, False, True, True], payload["cancels"]
