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
                    this.sinkId = 'default';
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
                    this.sinkId = 'default';
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
