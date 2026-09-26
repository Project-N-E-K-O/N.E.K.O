from pathlib import Path
import pytest

from tests.support.websocket_source_harness import (
    _run_settings_node_harness,
)

APP_WEBSOCKET_PATH = Path(__file__).resolve().parents[3] / "static" / "app" / "app-websocket.js"

pytestmark = pytest.mark.integration_serial


def test_voice_lifecycle_status_is_validated_and_exposed_to_ui():
    source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")

    assert "statusCode === 'ASR_LIFECYCLE_STATE'" in source
    assert "voiceInputLifecycleState" in source
    assert "voice-input-lifecycle-changed" in source
    assert "data-voice-input-state" in source


def test_lifecycle_blocked_clears_independent_asr_and_shows_failure_toast():
    # runtime.py _handle_independent_asr_error always broadcasts lifecycle
    # BLOCKED before the fatal status code, and most fatal codes
    # (ASR_ENDPOINTING_FAILED, ASR_BLOCKED_ENDPOINTING,
    # ASR_AUDIO_ORDERING_FAILED, ASR_PROVIDER_FINAL_TIMEOUT, provider codes)
    # do NOT carry the ASR_INDEPENDENT_ prefix. The failure teardown must
    # therefore hang off the BLOCKED lifecycle notification, not off a
    # fatal-code enumeration.
    source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")

    lifecycle_block = source.split("if (statusCode === 'ASR_LIFECYCLE_STATE')", 1)[1].split(
        "if (statusCode === 'VOICE_INPUT_LEASE_RESYNC_REQUIRED')",
        1,
    )[0]

    # BLOCKED teardown lives inside the validated-state branch.
    assert "if (lifecycleState === 'blocked')" in lifecycle_block
    assert lifecycle_block.index("allowedLifecycleStates.indexOf(lifecycleState)") < lifecycle_block.index(
        "if (lifecycleState === 'blocked')"
    )

    blocked_branch = lifecycle_block.split("if (lifecycleState === 'blocked')", 1)[1]
    # Performed by the shared teardown helper the branch calls.
    assert "tearDownBlockedVoiceRoute();" in blocked_branch
    teardown_fn = source.split("function tearDownBlockedVoiceRoute() {", 1)[1].split(
        "\n    }", 1
    )[0]
    assert "removeExternalAsrPreview();" in teardown_fn
    assert "S.independentAsrActive = false;" in teardown_fn
    assert teardown_fn.index("removeExternalAsrPreview();") < teardown_fn.index(
        "S.independentAsrActive = false;"
    )
    # The teardown runs before the toast, so the failure message is what stays
    # on screen.
    assert blocked_branch.index("tearDownBlockedVoiceRoute();") < blocked_branch.index(
        "microphone.independentAsrFallback"
    )

    # Cross-reference comment so backend changes to the failure path get
    # traced back here.
    assert "_handle_independent_asr_error" in lifecycle_block

    # Start-path failures never emit BLOCKED; the per-code toasts in the
    # ASR_INDEPENDENT_ prefix branch must survive.
    prefix_block = source.split(
        "if (statusCode && statusCode.indexOf('ASR_INDEPENDENT_') === 0)",
        1,
    )[1].split("if (statusCode === 'TTS_CONNECTION_FAILED')", 1)[0]
    assert "microphone.independentAsrProviderUnavailable" in prefix_block
    assert "microphone.independentAsrFallback" in prefix_block


def test_core_capability_refresh_failures_fail_open_and_coalesce_requests_harness():
    source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")
    start = source.index("function publishCoreApiCapability(provider, capability)")
    end = source.index("// Prime the capability once", start)
    refresh_source = source[start:end]
    harness = (
        """
        const S = {
          coreApiProvider: 'free',
          coreApiSupportsIndependentAsr: false,
        };
        let _coreApiCapabilityRefreshPromise = null;
        let _coreApiCapabilityRequestGeneration = 0;
        const events = [];
        class CustomEvent {
          constructor(type, options) {
            this.type = type;
            this.detail = options && options.detail;
          }
        }
        const window = {
          dispatchEvent(event) { events.push(event); },
          fetch: null,
        };
        """
        + refresh_source
        + """
        function response(data) {
          return { ok: true, json: async () => data };
        }
        function assert(condition, message) {
          if (!condition) throw new Error(message);
        }
        async function main() {
          window.fetch = async () => response({ success: false, coreApi: 'qwen' });
          await refreshCoreApiCapability({ force: true });
          assert(S.coreApiSupportsIndependentAsr === null, 'success:false must fail open');
          assert(S.coreApiProvider === '', 'failed response provider must become unknown');

          S.coreApiProvider = 'free';
          S.coreApiSupportsIndependentAsr = false;
          window.fetch = async () => response({ success: true, coreApi: 'qwen' });
          await refreshCoreApiCapability({ force: true });
          assert(S.coreApiSupportsIndependentAsr === null, 'legacy response must fail open');
          assert(S.coreApiProvider === 'qwen', 'usable provider context should be retained');

          const validCapability = {
            success: true,
            coreApi: 'free',
            effectiveCoreApi: 'qwen',
            supportsIndependentAsr: true,
          };
          let fetchCalls = 0;
          let resolveShared;
          window.fetch = () => {
            fetchCalls += 1;
            return new Promise((resolve) => { resolveShared = resolve; });
          };
          const firstRequest = refreshCoreApiCapability({ force: true });
          const joinedForceRequest = refreshCoreApiCapability({ force: true });
          const joinedDefaultRequest = refreshCoreApiCapability();
          assert(firstRequest === joinedForceRequest, 'force callers must share the in-flight request');
          assert(firstRequest === joinedDefaultRequest, 'all callers must share the in-flight request');
          assert(fetchCalls === 1, 'coalesced callers must issue one fetch');
          resolveShared(response(validCapability));
          await firstRequest;
          assert(S.coreApiSupportsIndependentAsr === true, 'shared success must publish capability');
          assert(S.coreApiProvider === 'qwen', 'effective provider must win');

          let resolveNext;
          window.fetch = () => {
            fetchCalls += 1;
            return new Promise((resolve) => { resolveNext = resolve; });
          };
          const nextRequest = refreshCoreApiCapability({ force: true });
          assert(nextRequest !== firstRequest, 'force must bypass completed cache data');
          assert(fetchCalls === 2, 'force after settlement must issue a fresh fetch');
          resolveNext(response(validCapability));
          await nextRequest;
          assert(
            events.length === 3
              && events.every((event) => event.type === 'neko:core-api-capability-changed'),
            'capability changes should notify the shared UI exactly once per change'
          );
          console.log('ok');
        }
        main().catch((error) => {
          console.error(error);
          process.exitCode = 1;
        });
        """
    )
    result = _run_settings_node_harness(harness)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
