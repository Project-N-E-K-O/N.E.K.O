from pathlib import Path

import pytest
from playwright.sync_api import Page


ROOT = Path(__file__).resolve().parents[2]
APP_STATE = ROOT / "static" / "app" / "app-state.js"
APP_AUDIO_PLAYBACK = ROOT / "static" / "app" / "app-audio-playback.js"


@pytest.mark.frontend
def test_preferred_speaker_falls_back_without_forgetting_and_auto_restores(
    page: Page, running_server: str
) -> None:
    page.goto(f"{running_server}/health")
    page.set_content("<main>speaker device harness</main>")
    page.add_script_tag(path=str(APP_STATE))
    page.add_script_tag(
        content="""
        (() => {
            class FakeAudioContext {
                constructor() {
                    this.state = 'running';
                    this.sinkId = '';
                    this.setSinkIdCalls = [];
                }
                async setSinkId(deviceId) {
                    this.setSinkIdCalls.push(deviceId);
                    if (window.__blockedSpeakerIds.has(deviceId)) {
                        throw new DOMException('device unavailable', 'NotFoundError');
                    }
                    this.sinkId = deviceId;
                }
            }
            window.__blockedSpeakerIds = new Set();
            window.AudioContext = FakeAudioContext;
            window.webkitAudioContext = FakeAudioContext;
        })();
        """
    )
    page.add_script_tag(path=str(APP_AUDIO_PLAYBACK))

    result = page.evaluate(
        """async () => {
            const outputsWithPreferred = [
                { kind: 'audiooutput', deviceId: 'default' },
                { kind: 'audiooutput', deviceId: 'communications' },
                { kind: 'audiooutput', deviceId: 'preferred-speaker' },
            ];
            const outputsWithoutPreferred = [
                { kind: 'audiooutput', deviceId: 'default' },
                { kind: 'audiooutput', deviceId: 'communications' },
            ];

            await window.selectSpeakerDevice('preferred-speaker');
            const context = await window.ensureAudioPlayerContext();
            const afterSelection = {
                selected: window.appState.selectedSpeakerId,
                effective: window.appState.effectiveSpeakerId,
                stored: localStorage.getItem('neko_selected_speaker'),
                calls: context.setSinkIdCalls.slice(),
            };

            await window.reconcileSelectedSpeakerDevices(outputsWithoutPreferred);
            const afterMissing = {
                selected: window.appState.selectedSpeakerId,
                effective: window.appState.effectiveSpeakerId,
                available: window.appState.selectedSpeakerAvailable,
                stored: localStorage.getItem('neko_selected_speaker'),
                calls: context.setSinkIdCalls.slice(),
            };

            await window.reconcileSelectedSpeakerDevices(outputsWithoutPreferred);
            const callsAfterRepeatedMissing = context.setSinkIdCalls.slice();

            await window.reconcileSelectedSpeakerDevices(outputsWithPreferred);
            const afterRestore = {
                selected: window.appState.selectedSpeakerId,
                effective: window.appState.effectiveSpeakerId,
                available: window.appState.selectedSpeakerAvailable,
                stored: localStorage.getItem('neko_selected_speaker'),
                calls: context.setSinkIdCalls.slice(),
            };

            await window.reconcileSelectedSpeakerDevices(outputsWithPreferred);
            const callsAfterRepeatedPresent = context.setSinkIdCalls.slice();

            await window.selectSpeakerDevice('default');
            const afterManualDefault = {
                selected: window.appState.selectedSpeakerId,
                effective: window.appState.effectiveSpeakerId,
                stored: localStorage.getItem('neko_selected_speaker'),
                calls: context.setSinkIdCalls.slice(),
            };

            return {
                afterSelection,
                afterMissing,
                callsAfterRepeatedMissing,
                afterRestore,
                callsAfterRepeatedPresent,
                afterManualDefault,
            };
        }"""
    )

    assert result["afterSelection"] == {
        "selected": "preferred-speaker",
        "effective": "preferred-speaker",
        "stored": "preferred-speaker",
        "calls": ["preferred-speaker"],
    }
    assert result["afterMissing"] == {
        "selected": "preferred-speaker",
        "effective": "default",
        "available": False,
        "stored": "preferred-speaker",
        "calls": ["preferred-speaker", "default"],
    }
    assert result["callsAfterRepeatedMissing"] == ["preferred-speaker", "default"]
    assert result["afterRestore"] == {
        "selected": "preferred-speaker",
        "effective": "preferred-speaker",
        "available": True,
        "stored": "preferred-speaker",
        "calls": ["preferred-speaker", "default", "preferred-speaker"],
    }
    assert result["callsAfterRepeatedPresent"] == [
        "preferred-speaker",
        "default",
        "preferred-speaker",
    ]
    assert result["afterManualDefault"] == {
        "selected": "default",
        "effective": "default",
        "stored": None,
        "calls": [
            "preferred-speaker",
            "default",
            "preferred-speaker",
            "default",
        ],
    }


@pytest.mark.frontend
def test_context_sink_failure_keeps_preference_for_later_restoration(
    page: Page, running_server: str
) -> None:
    page.goto(f"{running_server}/health")
    page.set_content("<main>speaker failure harness</main>")
    page.add_script_tag(path=str(APP_STATE))
    page.add_script_tag(
        content="""
        (() => {
            class FakeAudioContext {
                constructor() {
                    this.state = 'running';
                    this.sinkId = '';
                    this.setSinkIdCalls = [];
                }
                async setSinkId(deviceId) {
                    this.setSinkIdCalls.push(deviceId);
                    if (window.__blockedSpeakerIds.has(deviceId)) {
                        throw new DOMException('device unavailable', 'NotFoundError');
                    }
                    this.sinkId = deviceId;
                }
            }
            window.__blockedSpeakerIds = new Set();
            window.AudioContext = FakeAudioContext;
            window.webkitAudioContext = FakeAudioContext;
        })();
        """
    )
    page.add_script_tag(path=str(APP_AUDIO_PLAYBACK))

    result = page.evaluate(
        """async () => {
            await window.selectSpeakerDevice('sleeping-headset');
            window.__blockedSpeakerIds.add('sleeping-headset');
            const context = await window.ensureAudioPlayerContext();
            const afterFailure = {
                selected: window.appState.selectedSpeakerId,
                effective: window.appState.effectiveSpeakerId,
                available: window.appState.selectedSpeakerAvailable,
                stored: localStorage.getItem('neko_selected_speaker'),
                calls: context.setSinkIdCalls.slice(),
            };

            window.__blockedSpeakerIds.delete('sleeping-headset');
            await window.reconcileSelectedSpeakerDevices([
                { kind: 'audiooutput', deviceId: 'default' },
                { kind: 'audiooutput', deviceId: 'sleeping-headset' },
            ]);
            const afterRestore = {
                selected: window.appState.selectedSpeakerId,
                effective: window.appState.effectiveSpeakerId,
                available: window.appState.selectedSpeakerAvailable,
                stored: localStorage.getItem('neko_selected_speaker'),
                calls: context.setSinkIdCalls.slice(),
            };
            return { afterFailure, afterRestore };
        }"""
    )

    assert result["afterFailure"] == {
        "selected": "sleeping-headset",
        "effective": "default",
        "available": False,
        "stored": "sleeping-headset",
        "calls": ["sleeping-headset"],
    }
    assert result["afterRestore"] == {
        "selected": "sleeping-headset",
        "effective": "sleeping-headset",
        "available": True,
        "stored": "sleeping-headset",
        "calls": ["sleeping-headset", "sleeping-headset"],
    }


def _install_controllable_speaker_harness(
    page: Page, running_server: str
) -> None:
    page.goto(f"{running_server}/health")
    page.set_content("<main>controlled speaker device harness</main>")
    page.add_script_tag(path=str(APP_STATE))
    page.add_script_tag(
        content="""
        (() => {
            class ControlledAudioContext {
                constructor() {
                    this.state = 'running';
                    this.sinkId = '';
                    this.setSinkIdCalls = [];
                }
                async setSinkId(deviceId) {
                    this.setSinkIdCalls.push(deviceId);
                    if (window.__delayedSpeakerIds.has(deviceId)) {
                        await new Promise((resolve, reject) => {
                            window.__pendingSpeakerRoutes.push({
                                deviceId,
                                resolve: () => {
                                    this.sinkId = deviceId;
                                    resolve();
                                },
                                reject,
                            });
                        });
                        return;
                    }
                    if (window.__blockedSpeakerIds.has(deviceId)) {
                        throw new DOMException('device unavailable', 'NotFoundError');
                    }
                    this.sinkId = deviceId;
                }
            }
            window.__delayedSpeakerIds = new Set();
            window.__blockedSpeakerIds = new Set();
            window.__pendingSpeakerRoutes = [];
            window.__settleSpeakerRoute = (deviceId, succeed) => {
                const index = window.__pendingSpeakerRoutes.findIndex(
                    (route) => route.deviceId === deviceId
                );
                if (index < 0) throw new Error('pending speaker route not found');
                const route = window.__pendingSpeakerRoutes.splice(index, 1)[0];
                window.__delayedSpeakerIds.delete(deviceId);
                if (succeed) route.resolve();
                else route.reject(new DOMException('device unavailable', 'NotFoundError'));
            };
            window.__waitForPendingSpeakerRoute = async () => {
                for (let attempt = 0; attempt < 1000; attempt += 1) {
                    if (window.__pendingSpeakerRoutes.length) return;
                    await new Promise((resolve) => setTimeout(resolve, 0));
                }
                throw new Error('expected speaker route was not observed');
            };
            window.AudioContext = ControlledAudioContext;
            window.webkitAudioContext = ControlledAudioContext;
        })();
        """
    )
    page.add_script_tag(path=str(APP_AUDIO_PLAYBACK))


@pytest.mark.frontend
def test_implicit_default_sink_does_not_require_set_sink_id(
    page: Page, running_server: str
) -> None:
    _install_controllable_speaker_harness(page, running_server)

    result = page.evaluate(
        """async () => {
            const context = await window.ensureAudioPlayerContext();
            return {
                sinkId: context.sinkId,
                effective: window.appState.effectiveSpeakerId,
                calls: context.setSinkIdCalls.slice(),
            };
        }"""
    )

    assert result == {"sinkId": "", "effective": "default", "calls": []}


@pytest.mark.frontend
def test_existing_context_waits_for_an_in_flight_speaker_transition(
    page: Page, running_server: str
) -> None:
    _install_controllable_speaker_harness(page, running_server)

    result = page.evaluate(
        """async () => {
            const context = await window.ensureAudioPlayerContext();
            window.__delayedSpeakerIds.add('new-speaker');
            const selection = window.selectSpeakerDevice('new-speaker');
            await window.__waitForPendingSpeakerRoute();
            let ensureResolved = false;
            const ensured = window.ensureAudioPlayerContext().then((current) => {
                ensureResolved = true;
                return current.sinkId;
            });
            await new Promise((resolve) => setTimeout(resolve, 0));
            const resolvedBeforeSwitch = ensureResolved;
            window.__settleSpeakerRoute('new-speaker', true);
            const [applied, ensuredSink] = await Promise.all([selection, ensured]);
            return {
                resolvedBeforeSwitch,
                applied,
                ensuredSink,
                sinkId: context.sinkId,
                calls: context.setSinkIdCalls.slice(),
            };
        }"""
    )

    assert result == {
        "resolvedBeforeSwitch": False,
        "applied": True,
        "ensuredSink": "new-speaker",
        "sinkId": "new-speaker",
        "calls": ["new-speaker"],
    }


@pytest.mark.frontend
def test_failed_older_selection_cannot_roll_back_a_newer_selection(
    page: Page, running_server: str
) -> None:
    _install_controllable_speaker_harness(page, running_server)

    result = page.evaluate(
        """async () => {
            const context = await window.ensureAudioPlayerContext();
            window.__delayedSpeakerIds.add('first-speaker');
            const first = window.selectSpeakerDevice('first-speaker').then(
                () => 'resolved',
                () => 'rejected'
            );
            await window.__waitForPendingSpeakerRoute();
            const second = window.selectSpeakerDevice('second-speaker');
            window.__settleSpeakerRoute('first-speaker', false);
            const outcomes = await Promise.all([first, second]);
            return {
                outcomes,
                selected: window.appState.selectedSpeakerId,
                effective: window.appState.effectiveSpeakerId,
                stored: localStorage.getItem('neko_selected_speaker'),
                sinkId: context.sinkId,
                calls: context.setSinkIdCalls.slice(),
            };
        }"""
    )

    assert result == {
        "outcomes": ["rejected", True],
        "selected": "second-speaker",
        "effective": "second-speaker",
        "stored": "second-speaker",
        "sinkId": "second-speaker",
        "calls": ["first-speaker", "second-speaker"],
    }


@pytest.mark.frontend
def test_overlapping_disconnect_and_reconnect_restore_the_preferred_sink(
    page: Page, running_server: str
) -> None:
    _install_controllable_speaker_harness(page, running_server)

    result = page.evaluate(
        """async () => {
            const withPreferred = [
                { kind: 'audiooutput', deviceId: 'default' },
                { kind: 'audiooutput', deviceId: 'preferred-speaker' },
            ];
            const withoutPreferred = [
                { kind: 'audiooutput', deviceId: 'default' },
            ];
            await window.selectSpeakerDevice('preferred-speaker');
            const context = await window.ensureAudioPlayerContext();
            window.__delayedSpeakerIds.add('default');
            const disconnect = window.reconcileSelectedSpeakerDevices(
                withoutPreferred
            );
            await window.__waitForPendingSpeakerRoute();
            const reconnect = window.reconcileSelectedSpeakerDevices(withPreferred);
            window.__settleSpeakerRoute('default', true);
            const outcomes = await Promise.all([disconnect, reconnect]);
            return {
                outcomes,
                selected: window.appState.selectedSpeakerId,
                effective: window.appState.effectiveSpeakerId,
                available: window.appState.selectedSpeakerAvailable,
                stored: localStorage.getItem('neko_selected_speaker'),
                sinkId: context.sinkId,
                calls: context.setSinkIdCalls.slice(),
            };
        }"""
    )

    assert result == {
        "outcomes": [False, True],
        "selected": "preferred-speaker",
        "effective": "preferred-speaker",
        "available": True,
        "stored": "preferred-speaker",
        "sinkId": "preferred-speaker",
        "calls": ["preferred-speaker", "default", "preferred-speaker"],
    }


@pytest.mark.frontend
def test_storage_failure_keeps_the_session_selection(
    page: Page, running_server: str
) -> None:
    _install_controllable_speaker_harness(page, running_server)

    result = page.evaluate(
        """async () => {
            const context = await window.ensureAudioPlayerContext();
            const descriptor = Object.getOwnPropertyDescriptor(
                Storage.prototype,
                'setItem'
            );
            Object.defineProperty(Storage.prototype, 'setItem', {
                configurable: true,
                value() {
                    throw new DOMException('storage disabled', 'SecurityError');
                },
            });
            let applied;
            try {
                applied = await window.selectSpeakerDevice('session-speaker');
            } finally {
                Object.defineProperty(Storage.prototype, 'setItem', descriptor);
            }
            return {
                applied,
                selected: window.appState.selectedSpeakerId,
                effective: window.appState.effectiveSpeakerId,
                stored: localStorage.getItem('neko_selected_speaker'),
                sinkId: context.sinkId,
                calls: context.setSinkIdCalls.slice(),
            };
        }"""
    )

    assert result == {
        "applied": True,
        "selected": "session-speaker",
        "effective": "session-speaker",
        "stored": None,
        "sinkId": "session-speaker",
        "calls": ["session-speaker"],
    }


@pytest.mark.frontend
def test_failed_default_fallback_keeps_the_observed_effective_sink(
    page: Page, running_server: str
) -> None:
    _install_controllable_speaker_harness(page, running_server)

    result = page.evaluate(
        """async () => {
            await window.selectSpeakerDevice('preferred-speaker');
            const context = await window.ensureAudioPlayerContext();
            window.__blockedSpeakerIds.add('default');
            const reconciled = await window.reconcileSelectedSpeakerDevices([
                { kind: 'audiooutput', deviceId: 'default' },
            ]);
            return {
                reconciled,
                selected: window.appState.selectedSpeakerId,
                effective: window.appState.effectiveSpeakerId,
                available: window.appState.selectedSpeakerAvailable,
                stored: localStorage.getItem('neko_selected_speaker'),
                sinkId: context.sinkId,
                calls: context.setSinkIdCalls.slice(),
            };
        }"""
    )

    assert result == {
        "reconciled": False,
        "selected": "preferred-speaker",
        "effective": "preferred-speaker",
        "available": False,
        "stored": "preferred-speaker",
        "sinkId": "preferred-speaker",
        "calls": ["preferred-speaker", "default"],
    }


@pytest.mark.frontend
def test_stale_storage_event_cannot_override_a_pending_local_selection(
    page: Page, running_server: str
) -> None:
    _install_controllable_speaker_harness(page, running_server)

    result = page.evaluate(
        """async () => {
            const context = await window.ensureAudioPlayerContext();
            window.__delayedSpeakerIds.add('local-speaker');
            const localSelection = window.selectSpeakerDevice('local-speaker');
            await window.__waitForPendingSpeakerRoute();
            localStorage.setItem('neko_selected_speaker', 'older-speaker');
            window.dispatchEvent(new StorageEvent('storage', {
                key: 'neko_selected_speaker',
                newValue: 'older-speaker',
            }));
            window.__settleSpeakerRoute('local-speaker', true);
            await localSelection;
            await new Promise((resolve) => setTimeout(resolve, 0));
            return {
                selected: window.appState.selectedSpeakerId,
                effective: window.appState.effectiveSpeakerId,
                stored: localStorage.getItem('neko_selected_speaker'),
                sinkId: context.sinkId,
                calls: context.setSinkIdCalls.slice(),
            };
        }"""
    )

    assert result == {
        "selected": "local-speaker",
        "effective": "local-speaker",
        "stored": "local-speaker",
        "sinkId": "local-speaker",
        "calls": ["local-speaker"],
    }


@pytest.mark.frontend
def test_stale_audio_epoch_stops_before_stateful_ogg_decode_after_sink_setup(
    page: Page, running_server: str
) -> None:
    _install_controllable_speaker_harness(page, running_server)

    result = page.evaluate(
        """async () => {
            const state = window.appState;
            state.selectedSpeakerId = 'epoch-speaker';
            state.selectedSpeakerAvailable = true;
            window.__delayedSpeakerIds.add('epoch-speaker');
            window.__oggDecodeCalls = 0;
            window.decodeOggOpusChunk = async () => {
                window.__oggDecodeCalls += 1;
                return null;
            };
            const expectedEpoch = state.incomingAudioEpoch;
            const pending = window.handleAudioBlob(
                new Blob([new Uint8Array([0x4f, 0x67, 0x67, 0x53])]),
                expectedEpoch
            );
            await window.__waitForPendingSpeakerRoute();
            state.incomingAudioEpoch += 1;
            window.__settleSpeakerRoute('epoch-speaker', true);
            await pending;
            return {
                decodeCalls: window.__oggDecodeCalls,
                sinkCalls: state.audioPlayerContext.setSinkIdCalls.slice(),
            };
        }"""
    )

    assert result == {"decodeCalls": 0, "sinkCalls": ["epoch-speaker"]}
