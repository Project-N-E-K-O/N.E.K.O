import json
import shutil
import subprocess
import textwrap
from pathlib import Path

from main_routers import pages_router


PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_AUTO_GOODBYE_PATH = PROJECT_ROOT / "static" / "app" / "app-auto-goodbye.js"
APP_INTERPAGE_PATH = PROJECT_ROOT / "static" / "app" / "app-interpage.js"
INDEX_TEMPLATE_PATH = PROJECT_ROOT / "templates" / "index.html"
CHAT_TEMPLATE_PATH = PROJECT_ROOT / "templates" / "chat.html"


def _run_node_harness(script: str) -> subprocess.CompletedProcess[str]:
    node_path = shutil.which("node")
    if not node_path:
        raise AssertionError("node is required to run app-auto-goodbye harness tests")

    return subprocess.run(
        [node_path, "-e", script],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_app_auto_goodbye_phase1_harness():
    script = textwrap.dedent(
        f"""
        const fs = require('node:fs');
        const vm = require('node:vm');

        const source = fs.readFileSync({json.dumps(str(APP_AUTO_GOODBYE_PATH))}, 'utf8');

        class EventTargetLike {{
          constructor() {{
            this.listeners = new Map();
          }}
          addEventListener(type, handler) {{
            if (!this.listeners.has(type)) this.listeners.set(type, []);
            this.listeners.get(type).push(handler);
          }}
          dispatchEvent(event) {{
            event.target = this;
            const handlers = this.listeners.get(event.type) || [];
            for (const handler of handlers.slice()) {{
              handler.call(this, event);
            }}
            return true;
          }}
        }}

        class CustomEventLike {{
          constructor(type, init = {{}}) {{
            this.type = type;
            this.detail = init.detail;
          }}
        }}

        function createHarness(pathname, options = {{}}) {{
          let now = 0;
          let nextIntervalId = 1;
          const intervals = new Map();
          const goodbyeEvents = [];
          const win = new EventTargetLike();
          const doc = new EventTargetLike();
          let focused = options.focused !== false;
          let visibilityState = options.visibilityState || 'visible';
          let barrierResolved = options.barrierResolved === true;
          let resolveBarrier = null;
          const barrierPromise = barrierResolved
            ? Promise.resolve()
            : new Promise((resolve) => {{
                resolveBarrier = () => {{
                  barrierResolved = true;
                  resolve();
                }};
              }});

          function createNode(config = {{}}) {{
            const classes = new Set(config.classes || []);
            const node = {{
              hidden: config.hidden === true,
              dataset: Object.assign({{}}, config.dataset || {{}}),
              style: Object.assign({{
                display: 'block',
                visibility: 'visible',
                opacity: '1',
              }}, config.style || {{}}),
              classList: {{
                contains(name) {{
                  return classes.has(name);
                }},
                add(name) {{
                  classes.add(name);
                }},
                remove(name) {{
                  classes.delete(name);
                }},
                toggle(name, force) {{
                  if (force === true) {{
                    classes.add(name);
                    return true;
                  }}
                  if (force === false) {{
                    classes.delete(name);
                    return false;
                  }}
                  if (classes.has(name)) {{
                    classes.delete(name);
                    return false;
                  }}
                  classes.add(name);
                  return true;
                }},
              }},
              getBoundingClientRect() {{
                if (
                  node.hidden
                  || node.style.display === 'none'
                  || node.style.visibility === 'hidden'
                  || node.style.opacity === '0'
                ) {{
                  return {{ width: 0, height: 0 }};
                }}
                return {{
                  width: Number.isFinite(config.width) ? config.width : 32,
                  height: Number.isFinite(config.height) ? config.height : 32,
                }};
              }},
            }};
            return node;
          }}

          const selectorNodes = new Map();
          const elementsById = new Map();
          const resetSessionButton = createNode();
          resetSessionButton.id = 'resetSessionButton';
          const screenButton = createNode();
          screenButton.id = 'screenButton';
          elementsById.set('resetSessionButton', resetSessionButton);
          elementsById.set('screenButton', screenButton);

          doc.body = createNode({{ classes: options.bodyClasses || [] }});
          doc.readyState = 'complete';
          doc.visibilityState = visibilityState;
          doc.hasFocus = () => focused;
          doc.getElementById = (id) => elementsById.get(id) || null;
          doc.querySelectorAll = (selector) => selectorNodes.get(selector) || [];

          win.document = doc;
          win.location = {{ pathname }};
          win.appConst = {{}};
          const sentMessages = [];
          win.appState = {{ socket: {{
            readyState: 0,
            send(payload) {{
              try {{
                sentMessages.push(JSON.parse(payload));
              }} catch (_) {{
                sentMessages.push(payload);
              }}
            }},
          }} }};
          win.live2dManager = {{ _goodbyeClicked: false }};
          win.vrmManager = {{ _goodbyeClicked: false }};
          win.mmdManager = {{ _goodbyeClicked: false }};
          win._agentTaskMap = new Map();
          win._openedWindows = {{}};
          win.NekoHomeTutorialFeatureController = null;
          win.isMicStarting = false;
          win.waitForStorageLocationStartupBarrier = () => barrierPromise;
          win.setInterval = (fn) => {{
            const id = nextIntervalId++;
            intervals.set(id, fn);
            return id;
          }};
          win.clearInterval = (id) => intervals.delete(id);
          win.dispatchEvent = EventTargetLike.prototype.dispatchEvent.bind(win);
          win.addEventListener = EventTargetLike.prototype.addEventListener.bind(win);
          win.removeEventListener = () => {{}};
          doc.addEventListener = EventTargetLike.prototype.addEventListener.bind(doc);
          doc.dispatchEvent = EventTargetLike.prototype.dispatchEvent.bind(doc);
          win.getComputedStyle = (node) => {{
            const style = node && node.style ? node.style : {{}};
            return {{
              display: typeof style.display === 'string' ? style.display : 'block',
              visibility: typeof style.visibility === 'string' ? style.visibility : 'visible',
              opacity: typeof style.opacity === 'string' ? style.opacity : '1',
            }};
          }};

          // Simulate the existing goodbye / return base chain.
          win.addEventListener('live2d-goodbye-click', (event) => {{
            win.live2dManager._goodbyeClicked = true;
            win.vrmManager._goodbyeClicked = true;
            win.mmdManager._goodbyeClicked = true;
            goodbyeEvents.push(event.detail || {{}});
          }});
          win.addEventListener('live2d-return-click', () => {{
            win.live2dManager._goodbyeClicked = false;
            win.vrmManager._goodbyeClicked = false;
            win.mmdManager._goodbyeClicked = false;
          }});
          win.addEventListener('vrm-return-click', () => {{
            win.live2dManager._goodbyeClicked = false;
            win.vrmManager._goodbyeClicked = false;
            win.mmdManager._goodbyeClicked = false;
          }});
          win.addEventListener('mmd-return-click', () => {{
            win.live2dManager._goodbyeClicked = false;
            win.vrmManager._goodbyeClicked = false;
            win.mmdManager._goodbyeClicked = false;
          }});

          const context = {{
            window: win,
            document: doc,
            console,
            CustomEvent: CustomEventLike,
            WebSocket: {{ OPEN: 1 }},
            Date: {{ now: () => now }},
            Math,
            Promise,
            Map,
            Set,
          }};

          vm.createContext(context);
          vm.runInContext(source, context);

          return {{
            win,
            doc,
            goodbyeEvents,
            sentMessages,
            advance(ms) {{
              now += ms;
            }},
            setSocketOpen(open) {{
              win.appState.socket.readyState = open ? 1 : 0;
            }},
            tickAll() {{
              for (const fn of Array.from(intervals.values())) fn();
            }},
            resolveBarrier() {{
              if (resolveBarrier) resolveBarrier();
            }},
            setTutorialLocked(locked) {{
              win.isNekoHomeTutorialInteractionLocked = () => locked;
            }},
            setYuiTakingOver(active) {{
              if (active) doc.body.classList.add('yui-taking-over');
              else doc.body.classList.remove('yui-taking-over');
            }},
            setFocused(active) {{
              focused = !!active;
              if (focused) {{
                win.dispatchEvent(new CustomEventLike('focus'));
              }} else {{
                win.dispatchEvent(new CustomEventLike('blur'));
              }}
            }},
            setVisibility(nextState) {{
              visibilityState = nextState === 'hidden' ? 'hidden' : 'visible';
              doc.visibilityState = visibilityState;
              doc.dispatchEvent(new CustomEventLike('visibilitychange'));
            }},
            setVisibleSelector(selector, active) {{
              if (!active) {{
                selectorNodes.delete(selector);
                return;
              }}
              selectorNodes.set(selector, [createNode()]);
            }},
            setAppState(patch) {{
              Object.assign(win.appState, patch || {{}});
            }},
            setMicStarting(active) {{
              win.isMicStarting = !!active;
            }},
            openAuxWindow(name) {{
              win._openedWindows[name] = {{ closed: false }};
            }},
            closeAuxWindow(name) {{
              if (!win._openedWindows[name]) {{
                win._openedWindows[name] = {{ closed: true }};
                return;
              }}
              win._openedWindows[name].closed = true;
            }},
            setScreenShareActive(active) {{
              if (active) {{
                screenButton.classList.add('active');
                win.appState.videoSenderInterval = 1;
              }} else {{
                screenButton.classList.remove('active');
                win.appState.videoSenderInterval = null;
              }}
            }},
            setBodyClass(name, active) {{
              if (active) doc.body.classList.add(name);
              else doc.body.classList.remove(name);
            }},
            flush() {{
              return Promise.resolve().then(() => Promise.resolve()).then(() => Promise.resolve());
            }}
          }};
        }}

        function assert(condition, message) {{
          if (!condition) {{
            throw new Error(message);
          }}
        }}

        (async () => {{
          const AUTO_GOODBYE_MS = 10 * 60 * 1000;
          const CAT2_MS = 15 * 60 * 1000;
          const CAT3_MS = 18 * 60 * 1000;
          const CAT2_DELTA_MS = CAT2_MS - AUTO_GOODBYE_MS;
          const CAT3_DELTA_MS = CAT3_MS - CAT2_MS;

          // Only the homepage should start, and only after the storage barrier resolves.
          const delayed = createHarness('/', {{ barrierResolved: false }});
          await delayed.flush();
          assert(delayed.win.nekoAutoGoodbye.getState().started === false, 'controller should wait for barrier');
          delayed.resolveBarrier();
          await delayed.flush();
          assert(delayed.win.nekoAutoGoodbye.getState().started === true, 'controller should start after barrier');

          // /chat should never start the controller.
          const chat = createHarness('/chat', {{ barrierResolved: true }});
          await chat.flush();
          assert(chat.win.nekoAutoGoodbye.getState().started === false, '/chat should not start controller');

          const namedCharacter = createHarness('/Miao', {{ barrierResolved: true }});
          await namedCharacter.flush();
          assert(namedCharacter.win.nekoAutoGoodbye.getState().started === true, 'named character route should start controller');

          const nestedPath = createHarness('/Miao/profile', {{ barrierResolved: true }});
          await nestedPath.flush();
          assert(nestedPath.win.nekoAutoGoodbye.getState().started === false, 'nested paths should not start controller');

          const assetLikePath = createHarness('/Miao.json', {{ barrierResolved: true }});
          await assetLikePath.flush();
          assert(assetLikePath.win.nekoAutoGoodbye.getState().started === false, 'asset-like paths should not start controller');

          // Priming should wait for websocket OPEN, then reset the timer baseline.
          const home = createHarness('/', {{ barrierResolved: true }});
          await home.flush();
          assert(home.win.nekoAutoGoodbye.getState().started === true, 'home controller should start');
          home.advance(6000);
          home.tickAll();
          assert(home.goodbyeEvents.length === 0, 'should not auto-goodbye before websocket open');
          home.setSocketOpen(true);
          home.tickAll();
          const primed = home.win.nekoAutoGoodbye.getState();
          assert(primed.infrastructurePrimed === true, 'controller should prime after websocket open');
          assert(primed.lastInteractionAt === 6000, 'priming should reset timer baseline');

          home.setSocketOpen(false);
          home.advance(AUTO_GOODBYE_MS);
          home.tickAll();
          assert(home.goodbyeEvents.length === 0, 'closed websocket should block auto-goodbye after priming');
          assert(home.win.nekoAutoGoodbye.getState().infrastructurePrimed === false, 'closed websocket should clear infrastructure priming');
          home.setSocketOpen(true);
          home.tickAll();
          assert(home.win.nekoAutoGoodbye.getState().infrastructurePrimed === true, 'reopened websocket should re-prime infrastructure');
          assert(home.win.nekoAutoGoodbye.getState().lastInteractionAt === 6000 + AUTO_GOODBYE_MS, 're-prime should reset timer baseline after reconnect');

          // Normal auto-goodbye path.
          home.advance(AUTO_GOODBYE_MS);
          home.tickAll();
          assert(home.goodbyeEvents.length === 1, 'should trigger exactly one auto-goodbye event');
          assert(home.goodbyeEvents[0].autoGoodbye === true, 'auto-goodbye detail should be preserved');
          assert(home.win.nekoAutoGoodbye.getState().visualTier === 'cat1', 'auto-goodbye should move to cat1');

          // Desktop can keep conversation/system blockers alive after the model is hidden;
          // those blockers must not freeze the goodbye cat in CAT1.
          home.setAppState({{ isRecording: true }});
          home.advance(CAT2_DELTA_MS);
          home.tickAll();
          assert(home.win.nekoAutoGoodbye.getState().visualTier === 'cat2', 'goodbye idle should progress to cat2 even while suppressed');
          home.advance(CAT3_DELTA_MS);
          home.tickAll();
          assert(home.win.nekoAutoGoodbye.getState().visualTier === 'cat3', 'goodbye idle should progress to cat3 even while suppressed');
          home.setAppState({{ isRecording: false }});

          // Generic pointer/touch suppression must not refresh the idle baseline.
          home.advance(10000);
          home.tickAll();
          assert(home.win.nekoAutoGoodbye.getState().visualTier === 'cat3', 'goodbye idle should progress to cat3');
          const cat3Baseline = home.win.nekoAutoGoodbye.getState().lastInteractionAt;
          home.doc.dispatchEvent(new CustomEventLike('pointerdown'));
          home.tickAll();
          assert(home.win.nekoAutoGoodbye.getState().visualTier === 'cat3', 'pointerdown during goodbye should keep the current tier');
          assert(home.win.nekoAutoGoodbye.getState().lastInteractionAt === cat3Baseline, 'pointerdown during goodbye should not refresh idle baseline');
          home.setBodyClass('neko-model-dragging', true);
          home.tickAll();
          assert(home.win.nekoAutoGoodbye.getState().lastInteractionAt === cat3Baseline, 'drag suppression during goodbye should not refresh idle baseline');
          home.setBodyClass('neko-model-dragging', false);
          home.tickAll();
          assert(home.win.nekoAutoGoodbye.getState().visualTier === 'cat3', 'generic drag suppression release should keep the current tier');
          assert(home.win.nekoAutoGoodbye.getState().lastInteractionAt === cat3Baseline, 'generic drag suppression release should not refresh idle baseline');

          // Return-ball drag releases step the visual tier back without refreshing idle baseline.
          const dragEnd = () => home.win.dispatchEvent(new CustomEventLike('neko:return-ball-manual-move', {{
            detail: {{ reason: 'return-ball-drag-end', movedDistancePx: 24 }}
          }}));
          dragEnd();
          home.tickAll();
          assert(home.win.nekoAutoGoodbye.getState().visualTier === 'cat3', 'first CAT3 drag should keep CAT3');
          home.win.dispatchEvent(new CustomEventLike('neko:return-ball-manual-move', {{
            detail: {{ reason: 'return-ball-drag-end', movedDistancePx: 0, dragCancelled: true }}
          }}));
          home.tickAll();
          assert(home.win.nekoAutoGoodbye.getState().visualTier === 'cat3', 'cancelled CAT3 drag should not count toward drag demotion');
          dragEnd();
          home.tickAll();
          assert(home.win.nekoAutoGoodbye.getState().visualTier === 'cat2', 'second CAT3 drag should step back to CAT2');
          assert(home.win.nekoAutoGoodbye.getState().lastInteractionAt === cat3Baseline, 'CAT3 drag demotion should not refresh idle baseline');

          dragEnd();
          home.tickAll();
          assert(home.win.nekoAutoGoodbye.getState().visualTier === 'cat1', 'one CAT2 drag should step back to CAT1');
          assert(home.win.nekoAutoGoodbye.getState().lastInteractionAt === cat3Baseline, 'CAT2 drag demotion should not refresh idle baseline');

          home.advance(CAT2_DELTA_MS - 1);
          home.tickAll();
          assert(home.win.nekoAutoGoodbye.getState().visualTier === 'cat1', 'demoted CAT1 should not jump back to CAT2 before the full CAT1 phase length');
          home.advance(1);
          home.tickAll();
          assert(home.win.nekoAutoGoodbye.getState().visualTier === 'cat2', 'demoted CAT1 should progress to CAT2 after the full CAT1 phase length');
          home.advance(CAT3_DELTA_MS);
          home.tickAll();
          assert(home.win.nekoAutoGoodbye.getState().visualTier === 'cat3', 'demoted CAT2 should still progress to CAT3 after the normal CAT2 interval');

          // Return should clear auto state and tier.
          home.win.dispatchEvent(new CustomEventLike('live2d-return-click'));
          const returned = home.win.nekoAutoGoodbye.getState();
          assert(returned.visualTier === 'none', 'return should clear visual tier');
          assert(returned.autoGoodbyeTriggered === false, 'return should clear auto flag');

          // Running / queued tasks block; terminal tasks do not.
          const blocked = createHarness('/', {{ barrierResolved: true }});
          await blocked.flush();
          blocked.setSocketOpen(true);
          blocked.tickAll();
          blocked.win._agentTaskMap.set('task-1', {{ id: 'task-1', status: 'running' }});
          blocked.advance(AUTO_GOODBYE_MS);
          blocked.tickAll();
          assert(blocked.goodbyeEvents.length === 0, 'running task should block auto-goodbye');
          blocked.win._agentTaskMap.set('task-2', {{ id: 'task-2', status: 'queued' }});
          blocked.tickAll();
          assert(blocked.goodbyeEvents.length === 0, 'queued task should also block auto-goodbye');
          blocked.win._agentTaskMap.clear();
          blocked.win._agentTaskMap.set('task-3', {{ id: 'task-3', status: 'completed' }});
          blocked.tickAll();
          assert(blocked.goodbyeEvents.length === 0, 'clearing task blockers should not immediately auto-goodbye');
          blocked.advance(AUTO_GOODBYE_MS);
          blocked.tickAll();
          assert(blocked.goodbyeEvents.length === 1, 'terminal task should allow auto-goodbye after a fresh idle window');

          // Tutorial / Yui guards must suppress auto-goodbye while active.
          const guarded = createHarness('/', {{ barrierResolved: true }});
          await guarded.flush();
          guarded.setSocketOpen(true);
          guarded.tickAll();
          guarded.setTutorialLocked(true);
          guarded.advance(AUTO_GOODBYE_MS);
          guarded.tickAll();
          assert(guarded.goodbyeEvents.length === 0, 'tutorial lock should suppress auto-goodbye');
          guarded.setTutorialLocked(false);
          guarded.setYuiTakingOver(true);
          guarded.tickAll();
          assert(guarded.goodbyeEvents.length === 0, 'yui takeover should suppress auto-goodbye');
          guarded.setYuiTakingOver(false);
          guarded.tickAll();
          assert(guarded.goodbyeEvents.length === 0, 'guards clearing should not immediately trigger auto-goodbye');
          guarded.advance(AUTO_GOODBYE_MS);
          guarded.tickAll();
          assert(guarded.goodbyeEvents.length === 1, 'auto-goodbye should resume only after a fresh idle window');

          // Voice and user-content events should count as interaction and postpone timeout.
          const interaction = createHarness('/', {{ barrierResolved: true }});
          await interaction.flush();
          interaction.setSocketOpen(true);
          interaction.tickAll();
          interaction.advance(4000);
          interaction.win.dispatchEvent(new CustomEventLike('neko:voice-session-started'));
          interaction.advance(6000);
          interaction.tickAll();
          assert(interaction.goodbyeEvents.length === 0, 'voice start should hold conversation grace beyond the raw idle timeout');
          interaction.win.dispatchEvent(new CustomEventLike('neko:user-content-sent'));
          interaction.advance(6000);
          interaction.tickAll();
          assert(interaction.goodbyeEvents.length === 0, 'user content should keep the conversation guard active while waiting for a reply');
          interaction.advance(9000);
          interaction.tickAll();
          assert(interaction.goodbyeEvents.length === 0, 'conversation grace clearing should not immediately auto-goodbye');
          interaction.advance(AUTO_GOODBYE_MS);
          interaction.tickAll();
          assert(interaction.goodbyeEvents.length === 1, 'conversation grace should restart the idle countdown after it clears');

          // Conversation / system / auxiliary blockers should all be detected.
          const blockers = createHarness('/', {{ barrierResolved: true }});
          await blockers.flush();
          blockers.setSocketOpen(true);
          blockers.tickAll();
          blockers.setAppState({{ isRecording: true }});
          assert(blockers.win.nekoAutoGoodbye.hasActiveConversationState() === true, 'recording should activate conversation guard');
          assert(blockers.win.nekoAutoGoodbye.getIdleBlockReasons().includes('active-conversation'), 'recording should be an active-conversation blocker');
          blockers.setAppState({{ isRecording: false, voiceChatActive: true }});
          assert(blockers.win.nekoAutoGoodbye.hasActiveConversationState() === false, 'voice chat active alone should not activate the conversation guard');
          assert(!blockers.win.nekoAutoGoodbye.getIdleBlockReasons().includes('active-conversation'), 'voice chat active alone should not be an active-conversation blocker');
          blockers.setAppState({{ voiceChatActive: false, isTextSessionActive: true }});
          assert(blockers.win.nekoAutoGoodbye.hasActiveConversationState() === false, 'text session alone should not activate the conversation guard');
          assert(!blockers.win.nekoAutoGoodbye.getIdleBlockReasons().includes('active-conversation'), 'text session alone should not be an active-conversation blocker');
          blockers.setAppState({{ isTextSessionActive: false, voiceStartPending: true }});
          assert(blockers.win.nekoAutoGoodbye.hasActiveConversationState() === true, 'voice session startup should activate the conversation guard');
          assert(blockers.win.nekoAutoGoodbye.getIdleBlockReasons().includes('active-conversation'), 'voice session startup should be an active-conversation blocker');
          blockers.setAppState({{ voiceStartPending: false }});
          blockers.setAppState({{ isRecording: false, assistantTurnId: 'turn-1', assistantTurnCompletedId: null }});
          assert(blockers.win.nekoAutoGoodbye.hasActiveConversationState() === true, 'assistant turn should activate conversation guard');
          assert(blockers.win.nekoAutoGoodbye.getIdleBlockReasons().includes('active-conversation'), 'assistant turn should be an active-conversation blocker');
          blockers.setAppState({{ assistantTurnId: null, assistantTurnCompletedId: null }});
          blockers.setMicStarting(true);
          assert(blockers.win.nekoAutoGoodbye.hasActiveConversationState() === false, 'mic starting alone should not activate the conversation guard');
          assert(!blockers.win.nekoAutoGoodbye.getIdleBlockReasons().includes('active-conversation'), 'mic starting alone should not be an active-conversation blocker');
          blockers.setMicStarting(false);
          blockers.setScreenShareActive(true);
          assert(blockers.win.nekoAutoGoodbye.hasActiveSystemExecutionState() === true, 'manual screen share should activate system execution guard');
          assert(blockers.win.nekoAutoGoodbye.getIdleBlockReasons().includes('active-system'), 'manual screen share should be an active-system blocker');
          blockers.setScreenShareActive(false);
          blockers.openAuxWindow('plugin-dashboard');
          assert(!blockers.win.nekoAutoGoodbye.getIdleBlockReasons().includes('open-window'), 'idle should not be blocked by a static child window alone');
          blockers.setFocused(false);
          assert(!blockers.win.nekoAutoGoodbye.getIdleBlockReasons().includes('open-window'), 'a blurred child window alone should not block idle');
          blockers.setFocused(true);
          blockers.closeAuxWindow('plugin-dashboard');
          blockers.setBodyClass('neko-model-dragging', true);
          assert(blockers.win.nekoAutoGoodbye.getIdleBlockReasons().includes('dragging'), 'active dragging should block idle');
          blockers.setBodyClass('neko-model-dragging', false);

          // Cross-window activity should refresh the idle timer without blocking forever.
          const crossWindow = createHarness('/', {{ barrierResolved: true }});
          await crossWindow.flush();
          crossWindow.setSocketOpen(true);
          crossWindow.tickAll();
          crossWindow.advance(4000);
          crossWindow.win.dispatchEvent(new CustomEventLike('neko:cross-window-user-activity', {{
            detail: {{ source: 'chat-pointerdown', kind: 'interaction' }}
          }}));
          crossWindow.advance(2000);
          crossWindow.tickAll();
          assert(crossWindow.goodbyeEvents.length === 0, 'cross-window interaction should refresh the idle timer');
          crossWindow.advance(AUTO_GOODBYE_MS - 2000);
          crossWindow.tickAll();
          assert(crossWindow.goodbyeEvents.length === 1, 'cross-window interaction should not block idle forever');

          const runtimePaused = createHarness('/', {{ barrierResolved: true }});
          await runtimePaused.flush();
          runtimePaused.setSocketOpen(true);
          runtimePaused.tickAll();
          runtimePaused.advance(4000);
          runtimePaused.setAppState({{ isRecording: true }});
          runtimePaused.tickAll();
          runtimePaused.advance(12000);
          runtimePaused.tickAll();
          assert(runtimePaused.goodbyeEvents.length === 0, 'recording should suppress auto-goodbye');
          runtimePaused.setAppState({{ isRecording: false }});
          runtimePaused.tickAll();
          assert(runtimePaused.goodbyeEvents.length === 0, 'recording ending should not immediately auto-goodbye');
          runtimePaused.advance(AUTO_GOODBYE_MS);
          runtimePaused.tickAll();
          assert(runtimePaused.goodbyeEvents.length === 1, 'recording suppression should restart the idle countdown');

          // Manual / existing goodbye events must still land on CAT1 without marking auto.
          const manual = createHarness('/', {{ barrierResolved: true }});
          await manual.flush();
          manual.setSocketOpen(true);
          manual.tickAll();
          manual.advance(15000);
          manual.win.dispatchEvent(new CustomEventLike('live2d-goodbye-click'));
          const manualState = manual.win.nekoAutoGoodbye.getState();
          assert(manualState.visualTier === 'cat1', 'manual goodbye should still resolve to cat1');
          assert(manualState.autoGoodbyeTriggered === false, 'manual goodbye should not set auto flag');
          assert(manualState.lastReason === 'manual-goodbye', 'manual goodbye should preserve manual reason');

          // Fresh-page priming reconciles stale backend goodbye_silent: a model/pet window that is
          // not in goodbye sends goodbye_state:false once on first prime, clearing leftover silence
          // from a previous "请她离开" so greeting / proactive are not永久 suppressed after a reload.
          const reconcile = createHarness('/', {{ barrierResolved: true }});
          await reconcile.flush();
          assert(reconcile.sentMessages.length === 0, 'reconcile should not fire before websocket open');
          reconcile.setSocketOpen(true);
          reconcile.tickAll();
          const reconcileSends = reconcile.sentMessages.filter((m) => m && m.action === 'goodbye_state');
          assert(reconcileSends.length === 1, 'fresh prime should send exactly one goodbye_state reconcile');
          assert(reconcileSends[0].active === false, 'reconcile should clear stale goodbye silence');
          assert(reconcileSends[0].reason === 'fresh-connect-reconcile', 'reconcile should be tagged');
          // A subsequent same-session reconnect already has the in-memory state → no duplicate send.
          reconcile.setSocketOpen(false);
          reconcile.tickAll();
          reconcile.setSocketOpen(true);
          reconcile.tickAll();
          const reconcileSendsAfter = reconcile.sentMessages.filter((m) => m && m.action === 'goodbye_state');
          assert(reconcileSendsAfter.length === 1, 'reconnect with cached state should not re-send reconcile');

          // A window already in goodbye must NOT have its silence cleared by the prime reconcile.
          const stillGoodbye = createHarness('/', {{ barrierResolved: true }});
          await stillGoodbye.flush();
          stillGoodbye.win.dispatchEvent(new CustomEventLike('live2d-goodbye-click'));
          stillGoodbye.setSocketOpen(true);
          stillGoodbye.tickAll();
          const clearedWhileGoodbye = stillGoodbye.sentMessages.filter(
            (m) => m && m.action === 'goodbye_state' && m.active === false
          );
          assert(clearedWhileGoodbye.length === 0, 'prime must not clear silence while still in goodbye');

          console.log('app-auto-goodbye phase1 harness passed');
        }})().catch((error) => {{
          console.error(error && error.stack ? error.stack : error);
          process.exit(1);
        }});
        """
    )

    result = _run_node_harness(script)
    assert result.returncode == 0, (
        "node harness failed\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "app-auto-goodbye phase1 harness passed" in result.stdout


def test_app_auto_goodbye_only_injected_on_homepage():
    index_source = INDEX_TEMPLATE_PATH.read_text(encoding="utf-8")
    chat_source = CHAT_TEMPLATE_PATH.read_text(encoding="utf-8")

    assert '/static/app/app-auto-goodbye.js?v={{ static_asset_version }}' in index_source
    assert '/static/app/app-auto-goodbye.js?v={{ static_asset_version }}' not in chat_source


def test_pages_router_static_asset_version_tracks_app_auto_goodbye():
    assert APP_AUTO_GOODBYE_PATH in pages_router._YUI_GUIDE_ASSET_VERSION_PATHS


def test_app_interpage_relays_chat_idle_activity_to_homepage():
    source = APP_INTERPAGE_PATH.read_text(encoding="utf-8")

    assert "action: 'idle_activity'" in source
    assert "window.dispatchEvent(new CustomEvent('neko:cross-window-user-activity'" in source
    assert "function bindStandaloneChatIdleActivityRelay()" in source


def test_app_interpage_relays_idle_return_ball_state_to_chat_window():
    source = APP_INTERPAGE_PATH.read_text(encoding="utf-8")

    assert "case 'idle_return_ball_state':" in source
    assert "function dispatchIdleReturnBallState(detail)" in source
    assert "new CustomEvent('neko:idle-return-ball-state'" in source


def test_goodbye_composer_hidden_syncs_to_chat_window():
    interpage_source = APP_INTERPAGE_PATH.read_text(encoding="utf-8")
    app_ui_source = (PROJECT_ROOT / "static" / "app" / "app-ui.js").read_text(encoding="utf-8")
    standalone_block = interpage_source.split("function isStandaloneChatPage()", 1)[1].split(
        "function dispatchCrossWindowIdleActivity",
        1,
    )[0]
    goodbye_handler_block = interpage_source.split(
        "function handleGoodbyeChatComposerHiddenMessage(data, via)",
        1,
    )[1].split("function postGoodbyeChatComposerHiddenState", 1)[0]
    goodbye_read_block = interpage_source.split(
        "function readGoodbyeChatComposerHidden()",
        1,
    )[1].split("function applyGoodbyeChatComposerHidden", 1)[0]
    goodbye_filter_block = interpage_source.split(
        "function isGoodbyeChatComposerHiddenMessageForCurrentLanlan(data)",
        1,
    )[1].split("function handleGoodbyeChatComposerHiddenMessage", 1)[0]
    goodbye_request_block = interpage_source.split(
        "function requestGoodbyeChatComposerHiddenState(reason)",
        1,
    )[1].split("function isGoodbyeChatComposerHiddenMessageForCurrentLanlan", 1)[0]
    goodbye_post_block = interpage_source.split(
        "function postGoodbyeChatComposerHiddenState(hidden, reason)",
        1,
    )[1].split("function pruneVoiceConfigSwitchOps", 1)[0]
    goodbye_initial_request_block = interpage_source.split(
        "var postGoodbyeComposerRequest = function ()",
        1,
    )[1].split("postAvatarRequest();", 1)[0]

    assert "function applyGoodbyeChatComposerHidden(hidden, reason)" in interpage_source
    assert "function getGoodbyeChatComposerHiddenElectronBridge()" in interpage_source
    assert "function postGoodbyeChatComposerHiddenElectron(payload)" in interpage_source
    assert "function handleGoodbyeChatComposerHiddenMessage(data, via)" in interpage_source
    assert "function postGoodbyeChatComposerHiddenState(hidden, reason)" in interpage_source
    assert "window.nekoElectronGoodbyeChatComposerHidden" in interpage_source
    assert "neko:electron-goodbye-chat-composer-hidden" in interpage_source
    assert "action: 'goodbye_chat_composer_hidden'" in interpage_source
    assert "case 'goodbye_chat_composer_hidden':" in interpage_source
    assert "case 'request_goodbye_chat_composer_hidden':" in interpage_source
    assert "pathname === '/chat_full'" in standalone_block
    assert "pathname === '/chat_full/'" in standalone_block
    assert (
        "typeof window.isNekoGoodbyeModeActive === 'function'\n"
        "                && window.isNekoGoodbyeModeActive()"
        in goodbye_read_block
    )
    assert "window.__nekoGoodbyeChatComposerHidden.hidden === true" in goodbye_read_block
    assert "window.__nekoGoodbyeSilentState && window.__nekoGoodbyeSilentState.active === true" in goodbye_read_block
    assert "if (!data || !data.lanlan_name) return false;" in goodbye_filter_block
    assert "return !!currentName && data.lanlan_name === currentName;" in goodbye_filter_block
    assert "var lanlanName = getCurrentLanlanName();" in goodbye_post_block
    assert "if (!lanlanName) return;" in goodbye_post_block
    assert "lanlan_name: lanlanName" in goodbye_post_block
    assert "window.appState && typeof window.appState.lanlan_name === 'string'" in interpage_source
    assert "var lanlanName = getCurrentLanlanName();" in goodbye_request_block
    assert "if (!lanlanName) return false;" in goodbye_request_block
    assert "reason: reason || 'request-goodbye-chat-composer-hidden'" in goodbye_request_block
    assert "lanlan_name: lanlanName" in goodbye_request_block
    assert "requestGoodbyeChatComposerHiddenState('standalone-chat-state-request')" in goodbye_initial_request_block
    assert "GOODBYE_COMPOSER_REQUEST_RETRY_DELAYS_MS[goodbyeComposerRequestRetryIndex++]" in interpage_source
    assert "scheduleGoodbyeComposerRequest(0);" in interpage_source
    assert "if (isStandaloneChatPage()) return true;" in goodbye_handler_block
    assert (
        "postGoodbyeChatComposerHiddenState(undefined, 'request-goodbye-chat-composer-hidden');"
        in goodbye_handler_block
    )
    assert "nekoBroadcastChannel || getGoodbyeChatComposerHiddenElectronBridge()" in interpage_source
    assert "postGoodbyeChatComposerHiddenPayload({" in interpage_source
    assert "postGoodbyeComposerRequest();" in interpage_source
    # config 注入后只通过 postStandaloneChatStateRequests 统一补发，不再单独注册 once 监听，避免重复请求
    assert (
        "window.addEventListener('neko:config-injected', postGoodbyeComposerRequest"
        not in interpage_source
    )
    assert (
        "window.addEventListener('neko:config-injected', postAvatarRequest"
        not in interpage_source
    )
    assert "window.addEventListener('neko:config-injected', postStandaloneChatStateRequests);" in interpage_source
    assert "window.addEventListener('neko:request-goodbye-chat-composer-hidden-state'" in interpage_source
    assert "window.addEventListener('focus', function ()" in interpage_source
    assert "document.addEventListener('visibilitychange', function ()" in interpage_source
    assert (
        "mod.postGoodbyeChatComposerHiddenElectron = postGoodbyeChatComposerHiddenElectron;"
        in interpage_source
    )
    assert (
        "mod.handleGoodbyeChatComposerHiddenMessage = handleGoodbyeChatComposerHiddenMessage;"
        in interpage_source
    )
    assert "mod.postGoodbyeChatComposerHiddenState = postGoodbyeChatComposerHiddenState;" in interpage_source
    assert "mod.requestGoodbyeChatComposerHiddenState = requestGoodbyeChatComposerHiddenState;" in interpage_source
    assert "window.postGoodbyeChatComposerHiddenState = postGoodbyeChatComposerHiddenState;" in interpage_source
    assert "window.requestGoodbyeChatComposerHiddenState = requestGoodbyeChatComposerHiddenState;" in interpage_source
    assert "postGoodbyeChatComposerHiddenState(true, 'live2d-goodbye-click')" in app_ui_source
    assert "postGoodbyeChatComposerHiddenState(false, 'return-click')" in app_ui_source


def test_app_interpage_initializes_goodbye_bridge_exports_with_tutorial_bridge_fallback():
    script = textwrap.dedent(
        """
        const fs = require('node:fs');
        const vm = require('node:vm');
        const source = fs.readFileSync(__APP_INTERPAGE_PATH__, 'utf8');
        const listeners = {};
        const storage = {
          getItem() { return null; },
          setItem() {},
          removeItem() {}
        };
        const quietConsole = {
          log() {},
          warn() {},
          error() {}
        };
        const windowStub = {
          appState: { lanlan_name: 'Yui' },
          appConst: {},
          lanlan_config: { lanlan_name: 'Yui' },
          location: { origin: 'http://localhost', pathname: '/chat' },
          addEventListener(type, handler) { (listeners[type] ||= []).push(handler); },
          removeEventListener() {},
          dispatchEvent() {},
          setTimeout,
          clearTimeout,
          setInterval,
          clearInterval,
          localStorage: storage,
          console: quietConsole
        };
        windowStub.window = windowStub;

        const documentStub = {
          hidden: false,
          body: { classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } } },
          head: { appendChild() {} },
          documentElement: { appendChild() {} },
          createElement() {
            return {
              hidden: false,
              style: {},
              classList: { add() {}, remove() {} },
              setAttribute() {},
              appendChild() {}
            };
          },
          getElementById() { return null; },
          querySelector() { return null; },
          querySelectorAll() { return []; },
          addEventListener(type, handler) { (listeners[`document:${type}`] ||= []).push(handler); },
          removeEventListener() {}
        };

        class CustomEvent {
          constructor(type, init) {
            this.type = type;
            this.detail = init && init.detail;
          }
        }
        class BroadcastChannel {
          constructor(name) { this.name = name; }
          postMessage() {}
          close() {}
        }

        const context = {
          window: windowStub,
          document: documentStub,
          localStorage: storage,
          console: quietConsole,
          setTimeout,
          clearTimeout,
          setInterval,
          clearInterval,
          CustomEvent,
          BroadcastChannel,
          URL,
          location: windowStub.location
        };

        try {
          vm.runInNewContext(source, context, { filename: 'static/app/app-interpage.js' });
        } catch (error) {
          console.error(error && (error.stack || error.message) || error);
          process.exit(1);
        }

        if (!windowStub.appInterpage || typeof windowStub.postGoodbyeChatComposerHiddenState !== 'function') {
          process.exit(2);
        }
        windowStub.postGoodbyeChatComposerHiddenState(true, 'harness-goodbye');
        if (!windowStub.__nekoGoodbyeChatComposerHidden || windowStub.__nekoGoodbyeChatComposerHidden.hidden !== true) {
          process.exit(3);
        }
        """
    ).replace("__APP_INTERPAGE_PATH__", json.dumps(str(APP_INTERPAGE_PATH)))

    result = _run_node_harness(script)
    assert result.returncode == 0, result.stderr + result.stdout


def test_app_interpage_relays_idle_chat_minimized_state_to_pet_window():
    source = APP_INTERPAGE_PATH.read_text(encoding="utf-8")

    assert "case 'idle_chat_minimized_state':" in source
    assert "function dispatchIdleChatMinimizedState(detail)" in source
    assert "new CustomEvent('neko:idle-chat-minimized-state'" in source
    assert "postInterpageMessage(Object.assign({" in source
    assert "function isHighVolumeBroadcastChannelAction(action)" in source
    assert "action === 'idle_chat_minimized_state'" in source
    assert "if (!isHighVolumeBroadcastChannelAction(message.action))" in source


def test_app_interpage_relays_idle_chat_pair_move_bounds_to_chat_window():
    source = APP_INTERPAGE_PATH.read_text(encoding="utf-8")

    assert "case 'idle_chat_pair_move_bounds':" in source
    assert "function dispatchIdleChatPairMoveBounds(detail)" in source
    assert "new CustomEvent('neko:idle-chat-pair-move-bounds'" in source


def test_app_auto_goodbye_visual_tiers_progress_without_retriggering_goodbye():
    script = textwrap.dedent(
        f"""
        const fs = require('node:fs');
        const vm = require('node:vm');

        const source = fs.readFileSync({json.dumps(str(APP_AUTO_GOODBYE_PATH))}, 'utf8');

        class EventTargetLike {{
          constructor() {{
            this.listeners = new Map();
          }}
          addEventListener(type, handler) {{
            if (!this.listeners.has(type)) this.listeners.set(type, []);
            this.listeners.get(type).push(handler);
          }}
          dispatchEvent(event) {{
            event.target = this;
            const handlers = this.listeners.get(event.type) || [];
            for (const handler of handlers.slice()) {{
              handler.call(this, event);
            }}
            return true;
          }}
        }}

        class CustomEventLike {{
          constructor(type, init = {{}}) {{
            this.type = type;
            this.detail = init.detail;
          }}
        }}

        let now = 0;
        let nextIntervalId = 1;
        const intervals = new Map();
        const goodbyeEvents = [];
        const win = new EventTargetLike();
        const doc = new EventTargetLike();

        function createNode() {{
          return {{
            hidden: false,
            style: {{ display: 'block', visibility: 'visible', opacity: '1' }},
            classList: {{
              contains() {{ return false; }},
              add() {{}},
              remove() {{}},
            }},
            getBoundingClientRect() {{
              return {{ width: 32, height: 32 }};
            }},
          }};
        }}

        const resetSessionButton = createNode();
        resetSessionButton.id = 'resetSessionButton';
        const screenButton = createNode();
        screenButton.id = 'screenButton';

        doc.body = createNode();
        doc.readyState = 'complete';
        doc.getElementById = (id) => {{
          if (id === 'resetSessionButton') return resetSessionButton;
          if (id === 'screenButton') return screenButton;
          return null;
        }};
        doc.querySelectorAll = () => [];
        doc.addEventListener = EventTargetLike.prototype.addEventListener.bind(doc);
        doc.dispatchEvent = EventTargetLike.prototype.dispatchEvent.bind(doc);

        win.document = doc;
        win.location = {{ pathname: '/' }};
        win.appConst = {{}};
        win.appState = {{ socket: {{ readyState: 1 }} }};
        win.live2dManager = {{ _goodbyeClicked: false }};
        win.vrmManager = {{ _goodbyeClicked: false }};
        win.mmdManager = {{ _goodbyeClicked: false }};
        win._agentTaskMap = new Map();
        win.waitForStorageLocationStartupBarrier = () => Promise.resolve();
        win.setInterval = (fn) => {{
          const id = nextIntervalId++;
          intervals.set(id, fn);
          return id;
        }};
        win.clearInterval = (id) => intervals.delete(id);
        win.dispatchEvent = EventTargetLike.prototype.dispatchEvent.bind(win);
        win.addEventListener = EventTargetLike.prototype.addEventListener.bind(win);
        win.removeEventListener = () => {{}};
        win.getComputedStyle = (node) => node.style;

        win.addEventListener('live2d-goodbye-click', (event) => {{
          win.live2dManager._goodbyeClicked = true;
          win.vrmManager._goodbyeClicked = true;
          win.mmdManager._goodbyeClicked = true;
          goodbyeEvents.push(event.detail || {{}});
        }});

        const context = {{
          window: win,
          document: doc,
          console,
          CustomEvent: CustomEventLike,
          WebSocket: {{ OPEN: 1 }},
          Date: {{ now: () => now }},
          Math,
          Promise,
          Map,
          Set,
        }};

        vm.createContext(context);
        vm.runInContext(source, context);

        function tickAll() {{
          for (const fn of Array.from(intervals.values())) fn();
        }}

        function flush() {{
          return Promise.resolve().then(() => Promise.resolve()).then(() => Promise.resolve());
        }}

        function assert(condition, message) {{
          if (!condition) throw new Error(message);
        }}

        (async () => {{
          const AUTO_GOODBYE_MS = 10 * 60 * 1000;
          const CAT2_MS = 15 * 60 * 1000;
          const CAT3_MS = 18 * 60 * 1000;

          await flush();
          assert(win.nekoAutoGoodbye.getState().started === true, 'controller should start after barrier resolves');

          tickAll();
          assert(win.nekoAutoGoodbye.getState().visualTier === 'none', 'initial tier should be none');

          now += AUTO_GOODBYE_MS;
          tickAll();
          assert(goodbyeEvents.length === 1, 'cat1 entry should dispatch goodbye once');
          assert(win.nekoAutoGoodbye.getState().visualTier === 'cat1', '10min should enter cat1');

          now += CAT2_MS - AUTO_GOODBYE_MS;
          tickAll();
          assert(goodbyeEvents.length === 1, 'cat2 transition should not dispatch goodbye again');
          assert(win.nekoAutoGoodbye.getState().visualTier === 'cat2', '15min should transition to cat2');

          now += CAT3_MS - CAT2_MS;
          tickAll();
          assert(goodbyeEvents.length === 1, 'cat3 transition should not dispatch goodbye again');
          assert(win.nekoAutoGoodbye.getState().visualTier === 'cat3', '18min should transition to cat3');

          console.log('app-auto-goodbye phase3 visual tiers passed');
        }})().catch((error) => {{
          console.error(error && error.stack ? error.stack : error);
          process.exit(1);
        }});
        """
    )

    result = _run_node_harness(script)
    assert result.returncode == 0, (
        "node harness failed\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "app-auto-goodbye phase3 visual tiers passed" in result.stdout
