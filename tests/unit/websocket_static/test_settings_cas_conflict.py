import json
import textwrap
from pathlib import Path
import pytest

from tests.support.websocket_source_harness import (
    _run_settings_node_harness,
)

APP_SETTINGS_PATH = Path(__file__).resolve().parents[3] / "static" / "app" / "app-settings.js"

pytestmark = pytest.mark.integration_serial


def test_settings_cas_conflict_rebuilds_body_from_winning_asr_decision_harness():
    harness = textwrap.dedent(
        """
        const fs = require('node:fs');
        const vm = require('node:vm');
        const source = fs.readFileSync(__APP_SETTINGS_PATH__, 'utf8');

        function assert(cond, msg) {
          if (!cond) throw new Error('ASSERT: ' + msg);
        }
        function response(ok, status, etag, data) {
          return {
            ok,
            status,
            headers: {
              get(name) { return name.toLowerCase() === 'etag' ? etag : null; },
            },
            json: async () => data,
          };
        }
        const tick = () => new Promise((resolve) => setImmediate(resolve));

        function makeContext(
          bootFails,
          bootData,
          initialProjectSettings,
          failSharedStorageWrites
        ) {
          let storageListener = null;
          const runtime = {
            stoppedSpeech: 0,
            stoppedScreening: 0,
            stoppedTracks: 0,
            scheduled: 0,
          };
          const store = new Map([
            ['project_neko_settings', JSON.stringify(initialProjectSettings || {
              independentAsrEnabled: false,
              proactiveVisionEnabled: true,
              slopFilterEnabled: false,
              mergeMessagesEnabled: false,
              mouseTrackingEnabled: false,
            })],
            ['neko_noise_reduction', '0'],
          ]);
          const postCalls = [];
          const sandbox = {
            console: { log() {}, warn() {}, error() {} },
            setInterval() { return 0; },
            clearInterval() {},
            setTimeout(fn, ms) {
              const timer = setTimeout(fn, ms);
              if (timer && typeof timer.unref === 'function') timer.unref();
              return timer;
            },
            clearTimeout,
            localStorage: {
              getItem(key) { return store.has(key) ? store.get(key) : null; },
              setItem(key, value) {
                if (failSharedStorageWrites && key === 'project_neko_settings') {
                  throw new Error('shared localStorage unavailable');
                }
                store.set(key, String(value));
              },
              removeItem(key) { store.delete(key); },
            },
            document: { getElementById() { return null; } },
            fetch(url, opts) {
              if (opts && opts.method === 'POST') {
                return new Promise((resolve) => {
                  postCalls.push({ url, opts, resolve });
                });
              }
              if (bootFails) return Promise.resolve({ ok: false, status: 500 });
              return Promise.resolve(response(
                true,
                200,
                '"conversation-settings-0"',
                bootData || {
                  success: true,
                  settings: { independentAsrEnabled: false },
                  telemetryBranch: null,
                  decisions: {},
                }
              ));
            },
          };
          sandbox.window = {
            appState: {
              independentAsrEnabled: false,
              proactiveVisionEnabled: true,
              slopFilterEnabled: false,
              mergeMessagesEnabled: false,
              focusModeEnabled: false,
              settingsHydrated: false,
              screenCaptureStream: {
                getTracks() {
                  return [{ stop() { runtime.stoppedTracks += 1; } }];
                },
              },
            },
            appConst: {},
            appUtils: { mapRenderQualityToFollowPerf() { return 'medium'; } },
            proactiveVisionEnabled: true,
            slopFilterEnabled: false,
            focusModeEnabled: false,
            stopProactiveVisionDuringSpeech() { runtime.stoppedSpeech += 1; },
            stopScreening() { runtime.stoppedScreening += 1; },
            scheduleProactiveChat() { runtime.scheduled += 1; },
            addEventListener(type, listener) {
              if (type === 'storage') storageListener = listener;
            },
            removeEventListener() {},
          };
          vm.createContext(sandbox);
          vm.runInContext(source, sandbox);
          return {
            S: sandbox.window.appState,
            win: sandbox.window,
            mod: sandbox.window.appSettings,
            postCalls,
            store,
            runtime,
            fireStorage(newValue) {
              storageListener({ key: 'project_neko_settings', newValue });
            },
          };
        }

        async function runBootMetadataScenario() {
          const tuple = {
            // Valid at an authority whose clock is just over one second ahead,
            // but beyond this browser's independently measured +1 year bound.
            writeId: Date.now() + (365 * 24 * 60 * 60 * 1000) + 1000,
            writerId: 'server-ahead',
            value: false,
          };
          const tupleOnly = makeContext(false, {
            success: true,
            settings: { independentAsrEnabled: false },
            revision: 1,
            telemetryBranch: null,
            decisions: { independentAsrEnabled: tuple },
          });
          await tick();
          await tick();
          const persistedTuple = JSON.parse(
            tupleOnly.store.get('project_neko_settings')
          )._sharedWriteMeta.asrDecision;
          const tupleEnvelope = JSON.parse(
            tupleOnly.store.get('project_neko_settings')
          )._sharedWriteMeta.writeId;
          assert(
            JSON.stringify(persistedTuple) === JSON.stringify(tuple),
            'a newer same-value server tuple must persist for offline write-id flooring'
          );
          assert(
            tupleEnvelope < tuple.writeId,
            'a server ASR floor must not inflate the localStorage envelope id'
          );

          const resetPriorDecision = {
            writeId: Date.now() + 1000,
            writerId: 'server-before-reset',
            value: true,
          };
          assert(
            resetPriorDecision.value !== false,
            'the pre-reset ASR decision must differ from the disabled reset default'
          );
          const reset = makeContext(false, {
            success: true,
            settings: {},
            revision: 10,
            reset: true,
            telemetryBranch: null,
            decisions: { independentAsrEnabled: resetPriorDecision },
          });
          await tick();
          await tick();
          const resetVisionDefault = reset.mod._isUserRegionChina();
          assert(
            reset.S.slopFilterEnabled === true
              && reset.S.proactiveVisionEnabled === resetVisionDefault
              && reset.S.independentAsrEnabled === false
              && reset.S.voiceInputResourceOptimizationEnabled === true,
            'an empty authoritative restore must reset stale local values to defaults: '
              + JSON.stringify({
                slop: reset.S.slopFilterEnabled,
                vision: reset.S.proactiveVisionEnabled,
                visionDefault: resetVisionDefault,
                asr: reset.S.independentAsrEnabled,
                optimization: reset.S.voiceInputResourceOptimizationEnabled,
              })
          );
          assert(reset.postCalls.length === 1, 'the reset defaults must be written back once');
          const resetWritebackDecision = JSON.parse(
            reset.postCalls[0].opts.headers[
              'X-Conversation-Settings-ASR-Decision'
            ]
          );
          assert(
            resetWritebackDecision.value === false
              && resetWritebackDecision.writeId
                > resetPriorDecision.writeId,
            'a reset writeback must rebase the stale tuple onto the reset default'
          );
          assert(
            reset.postCalls[0].opts.headers['X-Conversation-Settings-Full-Snapshot'] === '1',
            'a reset writeback must declare that it can clear the tombstone'
          );
          const resetBody = JSON.parse(reset.postCalls[0].opts.body);
          assert(
            resetBody.slopFilterEnabled === true
              && resetBody.proactiveVisionEnabled === resetVisionDefault
              && resetBody.independentAsrEnabled === false
              && resetBody.voiceInputResourceOptimizationEnabled === true,
            'the reset writeback must not repopulate the server with stale localStorage'
          );
          reset.postCalls[0].resolve(response(
            true,
            200,
            '"conversation-settings-11"',
            {
              success: true,
              settings: resetBody,
              revision: 11,
              reset: false,
              decisions: {
                independentAsrEnabled: resetWritebackDecision,
              },
            }
          ));
          await tick();
          const resetPersisted = JSON.parse(
            reset.store.get('project_neko_settings')
          );
          assert(
            resetPersisted._sharedWriteMeta.asrDecision.value === false
              && resetPersisted._sharedWriteMeta.serverRevision === 11,
            'a full-write success must adopt and rebroadcast the generated server ASR tuple'
          );

          const resetRace = makeContext(false, {
            success: true,
            settings: {},
            revision: 10,
            reset: true,
            telemetryBranch: null,
            decisions: { independentAsrEnabled: resetPriorDecision },
          });
          await tick();
          await tick();
          assert(
            resetRace.postCalls.length === 1,
            'the reset-race writeback must start'
          );
          const resetRaceBaselineDecision = JSON.parse(
            resetRace.postCalls[0].opts.headers[
              'X-Conversation-Settings-ASR-Decision'
            ]
          );
          resetRace.S.independentAsrEnabled = true;
          resetRace.mod.saveSettings({
            skipServerSync: true,
            explicitSharedKeys: ['independentAsrEnabled'],
          });
          const resetToggleSync = resetRace.mod.syncSettingsToServer({
            userInitiated: true,
          });
          await tick();
          assert(
            resetRace.postCalls.length === 1,
            'the user toggle must queue behind the reset writeback'
          );
          const resetToggleDecision = JSON.parse(
            resetRace.store.get('project_neko_settings')
          )._sharedWriteMeta.asrDecision;
          assert(
            resetToggleDecision.value === true
              && resetToggleDecision.writeId
                > resetRaceBaselineDecision.writeId,
            'a toggle during reset must mint above the rebased reset decision'
          );
          resetRace.postCalls[0].resolve(response(
            true,
            200,
            '"conversation-settings-11"',
            {
              success: true,
              settings: {
                independentAsrEnabled: false,
                slopFilterEnabled: true,
              },
              revision: 11,
              reset: false,
              decisions: {
                independentAsrEnabled: resetRaceBaselineDecision,
              },
            }
          ));
          await tick();
          await tick();
          assert(
            resetRace.S.independentAsrEnabled === true
              && resetRace.postCalls.length === 2,
            'the older reset response must not overwrite the queued toggle'
          );
          const resetToggleBody = JSON.parse(resetRace.postCalls[1].opts.body);
          const resetToggleHeader = JSON.parse(
            resetRace.postCalls[1].opts.headers[
              'X-Conversation-Settings-ASR-Decision'
            ]
          );
          assert(
            resetToggleBody.independentAsrEnabled === true
              && JSON.stringify(resetToggleHeader)
                === JSON.stringify(resetToggleDecision),
            'the queued sync must persist the newer toggle tuple'
          );
          resetRace.postCalls[1].resolve(response(
            true,
            200,
            '"conversation-settings-12"',
            {
              success: true,
              settings: resetToggleBody,
              revision: 12,
              reset: false,
              decisions: {
                independentAsrEnabled: resetToggleDecision,
              },
            }
          ));
          await resetToggleSync;

          const noiseMerge = makeContext(false, {
            success: true,
            settings: {
              independentAsrEnabled: false,
              noiseReductionEnabled: true,
            },
            revision: 1,
            telemetryBranch: null,
            decisions: {},
          });
          await tick();
          await tick();
          assert(
            noiseMerge.store.get('neko_noise_reduction') === '1',
            'a boot merge must synchronize the legacy noise cache'
          );

          const serverBroadcaster = makeContext(false, {
            success: true,
            settings: {
              independentAsrEnabled: false,
              proactiveVisionEnabled: false,
            },
            revision: 2,
            telemetryBranch: null,
            decisions: {},
          });
          await tick();
          await tick();
          const serverSnapshot = JSON.parse(
            serverBroadcaster.store.get('project_neko_settings')
          );
          assert(
            serverSnapshot._sharedWriteMeta.serverRevision === 2
              && serverSnapshot._sharedWriteMeta.serverAuthoritativeKeys
                .includes('proactiveVisionEnabled'),
            'a server winner must carry its real revision and authoritative fields'
          );
          serverBroadcaster.fireStorage(JSON.stringify({
            proactiveVisionEnabled: true,
            _sharedWriteMeta: {
              writeId: 1,
              writerId: 'window-old',
              changedKeys: ['proactiveVisionEnabled'],
              hydrated: true,
              asrAuthoritative: true,
              knownKeyWrites: {
                proactiveVisionEnabled: {
                  writeId: 1,
                  writerId: 'window-old',
                },
              },
            },
          }));
          assert(
            serverBroadcaster.S.proactiveVisionEnabled === true,
            'an unconfirmed explicit edit must outrank a server snapshot '
              + 'that did not observe it'
          );
          assert(
            !serverSnapshot._sharedWriteMeta.knownKeyWrites
              .proactiveVisionEnabled
              && serverSnapshot._sharedWriteMeta.serverKeyRevisions
                .proactiveVisionEnabled === 2,
            'the server floor must be serialized without forging a source token'
          );
          const reloadedServerWinner = makeContext(
            true,
            null,
            serverSnapshot
          );
          await tick();
          await tick();
          reloadedServerWinner.fireStorage(JSON.stringify({
            proactiveVisionEnabled: true,
            _sharedWriteMeta: {
              writeId: 1,
              writerId: 'window-old-after-reload',
              changedKeys: ['proactiveVisionEnabled'],
              hydrated: true,
              asrAuthoritative: true,
              knownKeyWrites: {
                proactiveVisionEnabled: {
                  writeId: 1,
                  writerId: 'window-old-after-reload',
                },
              },
            },
          }));
          assert(
            reloadedServerWinner.S.proactiveVisionEnabled === true,
            'reload must not let the persisted server floor suppress '
              + 'an unconfirmed explicit edit'
          );

          const receiver = makeContext(true);
          await tick();
          await tick();
          receiver.fireStorage(JSON.stringify({
            proactiveVisionEnabled: true,
            _sharedWriteMeta: {
              writeId: 1,
              writerId: 'window-old',
              changedKeys: ['proactiveVisionEnabled'],
              hydrated: true,
              asrAuthoritative: true,
              knownKeyWrites: {
                proactiveVisionEnabled: {
                  writeId: 1,
                  writerId: 'window-old',
                  confirmedRevision: 1,
                },
              },
            },
          }));
          receiver.fireStorage(JSON.stringify(serverSnapshot));
          assert(
            receiver.S.proactiveVisionEnabled === false,
            'a newer authoritative server winner must outrank an older local token'
          );
          assert(
            receiver.runtime.stoppedSpeech === 1
              && receiver.runtime.stoppedScreening === 1
              && receiver.runtime.stoppedTracks === 1,
            'the authoritative privacy disable must stop active vision runtime'
          );
          receiver.fireStorage(JSON.stringify({
            proactiveVisionEnabled: true,
            _sharedWriteMeta: {
              writeId: 1,
              writerId: 'window-old',
              changedKeys: ['proactiveVisionEnabled'],
              hydrated: true,
              asrAuthoritative: true,
              knownKeyWrites: {
                proactiveVisionEnabled: {
                  writeId: 1,
                  writerId: 'window-old',
                  confirmedRevision: 1,
                },
              },
            },
          }));
          assert(
            receiver.S.proactiveVisionEnabled === false,
            'an accepted server winner must retain a floor against delayed old events'
          );
          const newerVisionWriteId =
            serverSnapshot._sharedWriteMeta.writeId + 1;
          receiver.fireStorage(JSON.stringify({
            proactiveVisionEnabled: true,
            _sharedWriteMeta: {
              writeId: newerVisionWriteId,
              writerId: 'window-new',
              changedKeys: ['proactiveVisionEnabled'],
              hydrated: true,
              asrAuthoritative: true,
              knownKeyWrites: {
                proactiveVisionEnabled: {
                  writeId: newerVisionWriteId,
                  writerId: 'window-new',
                },
              },
            },
          }));
          assert(
            receiver.S.proactiveVisionEnabled === true,
            'a genuinely newer explicit event must still supersede the server floor'
          );

          const subtitleReceiver = makeContext(true);
          await tick();
          await tick();
          subtitleReceiver.fireStorage(JSON.stringify({
            subtitleEnabled: false,
            userLanguage: null,
            _sharedWriteMeta: {
              writeId: 700,
              writerId: 'window-subtitle-editor',
              changedKeys: ['subtitleEnabled', 'userLanguage'],
              hydrated: true,
              asrAuthoritative: false,
              knownKeyWrites: {
                subtitleEnabled: {
                  writeId: 700,
                  writerId: 'window-subtitle-editor',
                },
                userLanguage: {
                  writeId: 700,
                  writerId: 'window-subtitle-editor',
                },
              },
            },
          }));
          subtitleReceiver.fireStorage(JSON.stringify({
            subtitleEnabled: true,
            userLanguage: 'ja',
            _sharedWriteMeta: {
              writeId: 800,
              writerId: 'window-stale-server-reader',
              changedKeys: [],
              hydrated: true,
              asrAuthoritative: false,
              serverRevision: 1,
              serverAuthoritativeKeys: [
                'subtitleEnabled',
                'userLanguage',
              ],
              knownKeyWrites: {},
            },
          }));
          assert(
            subtitleReceiver.S.subtitleEnabled === false
              && subtitleReceiver.S.userLanguage === null,
            'same-value subtitle intent must enter per-key ordering and survive '
              + 'a delayed stale server merge'
          );

          const noSharedStorage = makeContext(
            false,
            {
              success: true,
              settings: {},
              revision: 0,
              telemetryBranch: null,
              decisions: {},
            },
            null,
            true
          );
          await tick();
          await tick();
          noSharedStorage.S.noiseReductionEnabled = false;
          noSharedStorage.mod.saveSettings();
          await tick();
          await tick();
          assert(
            noSharedStorage.postCalls.length === 1
              && JSON.parse(
                noSharedStorage.postCalls[0].opts.body
              ).noiseReductionEnabled === false,
            'a failed shared localStorage write must not suppress the noise CAS POST'
          );
          noSharedStorage.postCalls[0].resolve(response(
            true,
            200,
            '"conversation-settings-1"',
            {
              success: true,
              settings: { noiseReductionEnabled: false },
              revision: 1,
              decisions: {},
            }
          ));

          const staleReceiver = makeContext(false, {
            success: true,
            settings: {
              independentAsrEnabled: false,
              focusModeEnabled: false,
            },
            revision: 2,
            telemetryBranch: null,
            decisions: {},
          });
          await tick();
          await tick();
          staleReceiver.fireStorage(JSON.stringify({
            focusModeEnabled: true,
            _sharedWriteMeta: {
              writeId: 50,
              writerId: 'window-editor',
              changedKeys: ['focusModeEnabled'],
              hydrated: true,
              asrAuthoritative: true,
              knownKeyWrites: {
                focusModeEnabled: {
                  writeId: 50,
                  writerId: 'window-editor',
                },
              },
            },
          }));
          const staleServerSnapshot = {
            focusModeEnabled: false,
            _sharedWriteMeta: {
              writeId: 500,
              writerId: 'window-stale-server-reader',
              changedKeys: [],
              hydrated: true,
              asrAuthoritative: true,
              serverRevision: 2,
              serverAuthoritativeKeys: ['focusModeEnabled'],
              knownKeyWrites: {},
            },
          };
          staleReceiver.fireStorage(JSON.stringify(staleServerSnapshot));
          assert(
            staleReceiver.S.focusModeEnabled === true,
            'a same-revision server snapshot must not launder its envelope '
              + 'over an unconfirmed explicit edit'
          );

          const equalRevision = makeContext(false, {
            success: true,
            settings: {
              independentAsrEnabled: false,
              focusModeEnabled: false,
            },
            revision: 2,
            telemetryBranch: null,
            decisions: {},
          });
          await tick();
          await tick();
          equalRevision.fireStorage(JSON.stringify({
            focusModeEnabled: true,
            _sharedWriteMeta: {
              writeId: 600,
              writerId: 'window-editor',
              changedKeys: ['focusModeEnabled'],
              hydrated: true,
              asrAuthoritative: true,
              knownKeyWrites: {
                focusModeEnabled: {
                  writeId: 600,
                  writerId: 'window-editor',
                },
              },
            },
          }));
          equalRevision.fireStorage(JSON.stringify({
            focusModeEnabled: true,
            _sharedWriteMeta: {
              writeId: 601,
              writerId: 'window-server-reader',
              changedKeys: [],
              hydrated: true,
              asrAuthoritative: true,
              serverRevision: 2,
              serverAuthoritativeKeys: ['focusModeEnabled'],
              knownKeyWrites: {
                focusModeEnabled: {
                  writeId: 600,
                  writerId: 'window-editor',
                  confirmedRevision: 2,
                },
              },
            },
          }));
          const equalSync = equalRevision.mod.syncSettingsToServer();
          await tick();
          assert(equalRevision.postCalls.length === 1, 'the confirmed view must POST');
          equalRevision.postCalls[0].resolve(response(
            false,
            412,
            '"conversation-settings-3"',
            {
              success: false,
              settings: {
                independentAsrEnabled: false,
                focusModeEnabled: false,
              },
              revision: 3,
              decisions: {},
            }
          ));
          await tick();
          await tick();
          assert(equalRevision.postCalls.length === 2, 'the newer conflict must retry');
          const equalRetryBody = JSON.parse(equalRevision.postCalls[1].opts.body);
          assert(
            equalRetryBody.focusModeEnabled === false,
            'an equal-revision confirmation must stop the older local value '
              + 'being preserved over revision 3'
          );
          equalRevision.postCalls[1].resolve(response(
            true,
            200,
            '"conversation-settings-4"',
            {
              success: true,
              settings: equalRetryBody,
              revision: 4,
              decisions: {},
            }
          ));
          await equalSync;
        }

        async function runScenario(serverDecisionIsNewer) {
          const ctx = makeContext();
          await tick();
          await tick();
          assert(ctx.S.settingsHydrated === true, 'boot GET must hydrate settings');

          ctx.S.independentAsrEnabled = true;
          ctx.mod.saveSettings({ skipServerSync: true });
          const syncPromise = ctx.mod.syncSettingsToServer({ userInitiated: true });
          await tick();
          assert(ctx.postCalls.length === 1, 'first CAS POST must be issued');
          const first = ctx.postCalls[0];
          const firstBody = JSON.parse(first.opts.body);
          const localDecision = JSON.parse(
            first.opts.headers['X-Conversation-Settings-ASR-Decision']
          );
          assert(
            first.opts.headers['If-Match'] === '"conversation-settings-0"',
            'boot ETag must guard the first POST'
          );
          assert(localDecision.value === true, 'first request carries local ASR intent');

          const serverDecision = {
            writeId: serverDecisionIsNewer
              ? localDecision.writeId + 1
              : Math.max(0, localDecision.writeId - 1),
            writerId: serverDecisionIsNewer ? 'window-z' : 'window-a',
            value: false,
          };
          first.resolve(response(
            false,
            412,
            '"conversation-settings-1"',
            {
              success: false,
              settings: { independentAsrEnabled: false, slopFilterEnabled: true },
              revision: 1,
              decisions: { independentAsrEnabled: serverDecision },
            }
          ));
          await tick();
          await tick();
          assert(ctx.S.slopFilterEnabled === true, 'conflict merge must adopt the server field');
          assert(
            ctx.win.slopFilterEnabled === true,
            'conflict merge must synchronize the window mirror'
          );
          assert(ctx.postCalls.length === 2, 'a CAS conflict must retry once');
          const retry = ctx.postCalls[1];
          const retryBody = JSON.parse(retry.opts.body);
          assert(
            retry.opts.headers['If-Match'] === '"conversation-settings-1"',
            'retry must use the conflict response ETag'
          );
          assert(
            retryBody.independentAsrEnabled === !serverDecisionIsNewer,
            'retry body must use the winning decision value'
          );
          assert(
            retryBody.slopFilterEnabled === true,
            'retry must not roll the conflict-merged value back from a stale window mirror'
          );
          const retryDecision =
            JSON.parse(retry.opts.headers['X-Conversation-Settings-ASR-Decision']);
          assert(
            retryDecision.writeId === (
              serverDecisionIsNewer ? serverDecision.writeId : localDecision.writeId
            ),
            'retry must carry the winning decision token'
          );
          retry.resolve(response(
            true,
            200,
            '"conversation-settings-2"',
            {
              success: true,
              settings: { independentAsrEnabled: retryBody.independentAsrEnabled },
              revision: 2,
              decisions: { independentAsrEnabled: retryDecision },
            }
          ));
          await syncPromise;
          if (serverDecisionIsNewer) {
            ctx.S.independentAsrEnabled = true;
            ctx.mod.saveSettings({ skipServerSync: true });
            const nextSharedSnapshot = JSON.parse(
              ctx.store.get('project_neko_settings')
            );
            const nextDecision = nextSharedSnapshot._sharedWriteMeta.asrDecision;
            assert(
              nextDecision.writeId > serverDecision.writeId,
              'the next explicit local toggle must supersede an adopted server decision'
            );
            assert(nextDecision.value === true, 'the superseding tuple carries the new choice');
          }
        }

        async function runAcknowledgedDirtyScenario() {
          const ctx = makeContext();
          await tick();
          await tick();

          ctx.win.slopFilterEnabled = true;
          ctx.mod.saveSettings();
          assert(ctx.S.slopFilterEnabled === true, 'the first edit updates shared state immediately');
          await tick();
          assert(ctx.postCalls.length === 1, 'the first user edit must POST');
          const acknowledged = JSON.parse(ctx.postCalls[0].opts.body);
          ctx.postCalls[0].resolve(response(
            true,
            200,
            '"conversation-settings-1"',
            {
              success: true,
              settings: acknowledged,
              revision: 1,
              decisions: {},
            }
          ));
          await tick();
          await tick();

          const acknowledgedLocal = JSON.parse(
            ctx.store.get('project_neko_settings')
          );
          const acknowledgedSlopToken =
            acknowledgedLocal._sharedWriteMeta.knownKeyWrites.slopFilterEnabled;
          ctx.fireStorage(JSON.stringify({
            slopFilterEnabled: false,
            proactiveMusicEnabled: false,
            _sharedWriteMeta: {
              writeId: acknowledgedLocal._sharedWriteMeta.writeId + 20,
              writerId: 'window-unrelated-editor',
              changedKeys: ['proactiveMusicEnabled'],
              hydrated: true,
              asrAuthoritative: true,
              knownKeyWrites: {
                slopFilterEnabled: {
                  writeId: Math.max(0, acknowledgedSlopToken.writeId - 1),
                  writerId: 'window-stale',
                },
                proactiveMusicEnabled: {
                  writeId: acknowledgedLocal._sharedWriteMeta.writeId + 20,
                  writerId: 'window-unrelated-editor',
                },
              },
            },
          }));
          assert(
            ctx.S.slopFilterEnabled === true
              && ctx.S.proactiveMusicEnabled === false,
            'an unrelated explicit snapshot must filter a stale incidental field'
          );
          ctx.fireStorage(JSON.stringify({
            slopFilterEnabled: false,
            _sharedWriteMeta: {
              writeId: Math.max(
                0,
                acknowledgedLocal._sharedWriteMeta.writeId - 1
              ),
              writerId: 'window-delayed',
              changedKeys: ['slopFilterEnabled'],
              hydrated: true,
              asrAuthoritative: true,
            },
          }));
          assert(
            ctx.S.slopFilterEnabled === true,
            'an older explicit event must not roll back an acknowledged edit'
          );
          ctx.fireStorage(JSON.stringify({
            slopFilterEnabled: false,
            _sharedWriteMeta: {
              writeId: acknowledgedLocal._sharedWriteMeta.writeId,
              writerId: 'zzzzzzzzzzzzzzzz',
              changedKeys: [],
              hydrated: true,
              asrAuthoritative: true,
            },
          }));
          assert(
            ctx.S.slopFilterEnabled === true,
            'a delayed same-id server merge must not roll back an acknowledged local edit'
          );

          // A newer explicit value may only have been received from another
          // window, so it advances the applied floor without minting a local
          // write id. A delayed merge must respect that floor too.
          const externalWriteId =
            acknowledgedLocal._sharedWriteMeta.writeId + 10;
          ctx.fireStorage(JSON.stringify({
            mergeMessagesEnabled: true,
            _sharedWriteMeta: {
              writeId: externalWriteId,
              writerId: 'window-c',
              changedKeys: ['mergeMessagesEnabled'],
              hydrated: true,
              asrAuthoritative: true,
            },
          }));
          ctx.fireStorage(JSON.stringify({
            mergeMessagesEnabled: false,
            _sharedWriteMeta: {
              writeId: externalWriteId,
              writerId: 'window-d',
              changedKeys: [],
              hydrated: true,
              asrAuthoritative: true,
            },
          }));
          assert(
            ctx.S.mergeMessagesEnabled === true,
            'a delayed merge must not roll back a newer externally applied value'
          );

          // This explicit edit arrives after the boot ETag but before the next
          // CAS request starts. It is already present in that request snapshot,
          // so mutationVersionAtSend alone cannot detect it later; the ETag's
          // cross-window watermark must preserve it across the 412.
          ctx.fireStorage(JSON.stringify({
            avatarReactionBubbleEnabled: true,
            _sharedWriteMeta: {
              writeId: externalWriteId + 1,
              writerId: 'window-c',
              changedKeys: ['avatarReactionBubbleEnabled'],
              hydrated: true,
              asrAuthoritative: true,
            },
          }));
          assert(
            ctx.S.avatarReactionBubbleEnabled === true,
            'the pre-request cross-window edit must be accepted locally'
          );

          // A different local edit races a newer server revision. The earlier
          // slopFilterEnabled=true was already acknowledged and must no longer
          // be protected as pending during the 412 merge.
          ctx.win.focusModeEnabled = true;
          ctx.mod.saveSettings();
          await tick();
          assert(ctx.postCalls.length === 2, 'the unrelated edit must POST');

          // A sibling's unrelated explicit edit still carries a full snapshot.
          // Its incidental copy of this window's pending key must not replace
          // the pending value merely because changedKeys is nonempty.
          ctx.fireStorage(JSON.stringify({
            focusModeEnabled: false,
            mergeMessagesEnabled: true,
            _sharedWriteMeta: {
              writeId: 98,
              writerId: 'window-b',
              changedKeys: ['mergeMessagesEnabled'],
              hydrated: true,
              asrAuthoritative: true,
            },
          }));
          assert(
            ctx.S.focusModeEnabled === true && ctx.S.mergeMessagesEnabled === true,
            'a nonempty explicit broadcast preserves unrelated pending keys'
          );

          // A server-merge broadcast may have been built before this pending
          // edit. It must neither overwrite the local value nor leave its
          // stale full snapshot in shared localStorage.
          ctx.fireStorage(JSON.stringify({
            focusModeEnabled: false,
            _sharedWriteMeta: {
              writeId: 99,
              writerId: 'server-window',
              changedKeys: [],
              hydrated: true,
              asrAuthoritative: true,
            },
          }));
          assert(
            ctx.S.focusModeEnabled === true,
            'a server-merge broadcast must preserve a pending local edit'
          );
          const reasserted = JSON.parse(ctx.store.get('project_neko_settings'));
          assert(
            reasserted.focusModeEnabled === true
              && reasserted._sharedWriteMeta.changedKeys.length === 0,
            'the pending value must be restored without advertising new user intent'
          );

          // Cross-window ABA edits do not enter this window's pending set and
          // leave the final value equal to the request snapshot. The mutation
          // itself must still protect the latest choice from the 412 snapshot.
              ctx.fireStorage(JSON.stringify({
                mergeMessagesEnabled: true,
                _sharedWriteMeta: {
                  writeId: externalWriteId + 2,
              writerId: 'window-b',
              changedKeys: ['mergeMessagesEnabled'],
              hydrated: true,
            },
          }));
              ctx.fireStorage(JSON.stringify({
                mergeMessagesEnabled: false,
                _sharedWriteMeta: {
                  writeId: externalWriteId + 3,
              writerId: 'window-b',
              changedKeys: ['mergeMessagesEnabled'],
              hydrated: true,
            },
          }));
          ctx.postCalls[1].resolve(response(
            false,
            412,
            '"conversation-settings-2"',
            {
              success: false,
              settings: {
                independentAsrEnabled: false,
                proactiveVisionEnabled: false,
                slopFilterEnabled: false,
                focusModeEnabled: false,
                mergeMessagesEnabled: true,
                avatarReactionBubbleEnabled: false,
              },
              revision: 2,
              decisions: {},
            }
          ));
          await tick();
          await tick();
          assert(ctx.postCalls.length === 3, 'the conflict must retry');
          const retryBody = JSON.parse(ctx.postCalls[2].opts.body);
          assert(
            retryBody.slopFilterEnabled === false,
            'the retry must adopt the newer server value for an acknowledged old edit'
          );
          assert(
            retryBody.focusModeEnabled === true,
            'the still-pending local edit must survive the conflict merge'
          );
          assert(
            retryBody.mergeMessagesEnabled === false,
            'a non-pending ABA edit made after send must survive the conflict merge'
          );
          assert(
            retryBody.avatarReactionBubbleEnabled === true,
            'an explicit cross-window edit after the ETag but before send must survive'
          );
          assert(
            retryBody.proactiveVisionEnabled === false,
            'the retry must retain the server privacy winner'
          );
          assert(
            ctx.runtime.stoppedSpeech === 1
              && ctx.runtime.stoppedScreening === 1
              && ctx.runtime.stoppedTracks === 1,
            'the privacy winner must stop every active vision runtime path'
          );
          const reconciledLocal = JSON.parse(ctx.store.get('project_neko_settings'));
          assert(
            reconciledLocal.slopFilterEnabled === false
              && reconciledLocal.focusModeEnabled === true
              && reconciledLocal.proactiveVisionEnabled === false
              && reconciledLocal.mergeMessagesEnabled === false
              && reconciledLocal.avatarReactionBubbleEnabled === true,
            'the conflict winners and pending local edit must persist to shared localStorage'
          );
          assert(
            reconciledLocal.mouseTrackingEnabled === false,
            'server reconciliation must preserve local-only settings'
          );
          assert(
            reconciledLocal._sharedWriteMeta.changedKeys.length === 0,
            'server winners must not be advertised as new user intent'
          );
          ctx.postCalls[2].resolve(response(
            true,
            200,
            '"conversation-settings-3"',
            {
              success: true,
              settings: retryBody,
              revision: 3,
              decisions: {},
            }
          ));
          await tick();
        }

        async function runSuccessfulPartialSnapshotScenario() {
          const ctx = makeContext(true);
          await tick();
          await tick();
          assert(ctx.S.settingsHydrated === false, 'failed boot GET stays unhydrated');

          // A cross-window edit arrives before this partial request starts.
          // It is not this window's pending key and therefore is absent from
          // the dirty-only payload, but the success snapshot must not erase it.
          ctx.fireStorage(JSON.stringify({
            avatarReactionBubbleEnabled: true,
            _sharedWriteMeta: {
              writeId: 101,
              writerId: 'window-b',
              changedKeys: ['avatarReactionBubbleEnabled'],
              hydrated: true,
              asrAuthoritative: true,
            },
          }));

          ctx.win.focusModeEnabled = true;
          ctx.mod.saveSettings();
          await tick();
          assert(ctx.postCalls.length === 1, 'the first pending edit must POST');
          const firstBody = JSON.parse(ctx.postCalls[0].opts.body);
          assert(
            Object.keys(firstBody).length === 1 && firstBody.focusModeEnabled === true,
            'an unmerged view must send only its pending key, got: '
              + JSON.stringify(firstBody)
          );

          ctx.fireStorage(JSON.stringify({
            focusModeEnabled: false,
            _sharedWriteMeta: {
              writeId: 1,
              writerId: 'window-old',
              changedKeys: ['focusModeEnabled'],
              hydrated: true,
              asrAuthoritative: true,
              knownKeyWrites: {
                focusModeEnabled: {
                  writeId: 1,
                  writerId: 'window-old',
                },
              },
            },
          }));
          assert(
            ctx.S.focusModeEnabled === true,
            'an older explicit token must not replace a newer pending edit'
          );

          // A later edit happens while the partial write is in flight. The
          // successful response snapshot is authoritative for untouched keys,
          // but must not overwrite this still-pending local value.
          ctx.win.slopFilterEnabled = true;
          ctx.mod.saveSettings();
          assert(ctx.S.slopFilterEnabled === true, 'the in-flight edit updates shared state');
          await tick();
          assert(ctx.postCalls.length === 1, 'the later edit queues behind the first POST');

          // The other window explicitly chooses the value this stale local
          // view already holds. The metadata still represents a newer user
          // mutation and must protect the key from the delayed response.
          ctx.fireStorage(JSON.stringify({
            mergeMessagesEnabled: false,
            _sharedWriteMeta: {
              writeId: 102,
              writerId: 'window-b',
              changedKeys: ['mergeMessagesEnabled'],
              hydrated: true,
              asrAuthoritative: true,
            },
          }));

          ctx.postCalls[0].resolve(response(
            true,
            200,
            '"conversation-settings-1"',
            {
              success: true,
              settings: {
                independentAsrEnabled: true,
                proactiveVisionEnabled: false,
                slopFilterEnabled: false,
                focusModeEnabled: true,
                mergeMessagesEnabled: true,
                noiseReductionEnabled: true,
                avatarReactionBubbleEnabled: false,
              },
              revision: 1,
              decisions: {},
            }
          ));
          await tick();
          await tick();

          assert(ctx.S.settingsHydrated === true, 'the complete success snapshot hydrates the view');
          assert(
            ctx.S.independentAsrEnabled === true,
            'an untouched field hydrates from the successful partial-write response'
          );
          assert(
            ctx.S.slopFilterEnabled === true,
            'an edit made while the request was in flight remains pending'
          );
          assert(
            ctx.S.mergeMessagesEnabled === false,
            'a same-value explicit cross-window edit survives the delayed response'
          );
          assert(
            ctx.S.avatarReactionBubbleEnabled === true,
            'a pre-request cross-window edit absent from the partial body survives'
          );
          assert(
            ctx.store.get('neko_noise_reduction') === '1',
            'accepted shared noise reduction mirrors into the legacy cache'
          );
          assert(
            ctx.runtime.stoppedSpeech === 1
              && ctx.runtime.stoppedScreening === 1
              && ctx.runtime.stoppedTracks === 1,
            'hydrating the privacy winner stops active vision runtime'
          );
          assert(ctx.postCalls.length === 2, 'the queued edit runs after hydration');
          const secondBody = JSON.parse(ctx.postCalls[1].opts.body);
          assert(
            secondBody.independentAsrEnabled === true
              && secondBody.proactiveVisionEnabled === false
              && secondBody.slopFilterEnabled === true
              && secondBody.mergeMessagesEnabled === false
              && secondBody.noiseReductionEnabled === true
              && secondBody.avatarReactionBubbleEnabled === true,
            'the queued retry uses the reconciled full snapshot plus the pending edit'
          );
          const reconciledLocal = JSON.parse(ctx.store.get('project_neko_settings'));
          assert(
            reconciledLocal.independentAsrEnabled === true
              && reconciledLocal.proactiveVisionEnabled === false
              && reconciledLocal.slopFilterEnabled === true,
            'the reconciled success snapshot persists for offline restart'
          );
          assert(
            reconciledLocal.mouseTrackingEnabled === false,
            'success reconciliation preserves local-only settings'
          );
          assert(
            reconciledLocal._sharedWriteMeta.knownKeyWrites.focusModeEnabled
              .confirmedRevision === 1,
            'the acknowledged explicit token must carry its confirmed revision'
          );
          ctx.postCalls[1].resolve(response(
            true,
            200,
            '"conversation-settings-2"',
            {
              success: true,
              settings: secondBody,
              revision: 2,
              decisions: {},
            }
          ));
          await tick();
        }

        async function runPartialResetSnapshotScenario() {
          const ctx = makeContext(true);
          await tick();
          await tick();
          assert(ctx.S.slopFilterEnabled === false, 'the harness starts with stale local slop');

          ctx.win.focusModeEnabled = true;
          ctx.mod.saveSettings();
          await tick();
          await tick();
          assert(ctx.postCalls.length === 1, 'the pre-hydration edit must POST');
          assert(
            !('X-Conversation-Settings-Full-Snapshot' in ctx.postCalls[0].opts.headers),
            'a dirty-only pre-hydration write must not clear the reset tombstone'
          );

          ctx.postCalls[0].resolve(response(
            true,
            200,
            '"conversation-settings-1"',
            {
              success: true,
              settings: { focusModeEnabled: true },
              revision: 1,
              reset: true,
              decisions: {},
            }
          ));
          await tick();
          await tick();

          assert(
            ctx.S.focusModeEnabled === true
              && ctx.S.slopFilterEnabled === true
              && ctx.S.independentAsrEnabled === false,
            'a partial reset response must materialize defaults without losing the edit'
          );
          const restoredLocal = JSON.parse(ctx.store.get('project_neko_settings'));
          assert(
            restoredLocal.focusModeEnabled === true
              && restoredLocal.slopFilterEnabled === true
              && restoredLocal.independentAsrEnabled === false,
            'the partial reset response must replace stale localStorage values'
          );
        }

        async function runPartialConfirmedWatermarkScenario() {
          const ctx = makeContext(true);
          await tick();
          await tick();

          ctx.fireStorage(JSON.stringify({
            mergeMessagesEnabled: true,
            _sharedWriteMeta: {
              writeId: 110,
              writerId: 'window-b',
              changedKeys: ['mergeMessagesEnabled'],
              hydrated: true,
              asrAuthoritative: true,
            },
          }));
          ctx.win.focusModeEnabled = true;
          ctx.mod.saveSettings();
          await tick();
          await tick();

          assert(ctx.postCalls.length === 1, 'the dirty-only edit must POST');
          const firstBody = JSON.parse(ctx.postCalls[0].opts.body);
          assert(firstBody.focusModeEnabled === true, 'the pending key is sent');
          assert(
            !Object.prototype.hasOwnProperty.call(
              firstBody,
              'mergeMessagesEnabled'
            ),
            'the sibling cross-window key stays outside the partial request'
          );

          ctx.postCalls[0].resolve(response(
            true,
            200,
            '"conversation-settings-1"',
            {
              success: true,
              settings: {
                independentAsrEnabled: false,
                focusModeEnabled: true,
                mergeMessagesEnabled: true,
                slopFilterEnabled: false,
              },
              revision: 1,
              decisions: {},
            }
          ));
          await tick();
          await tick();

          ctx.win.slopFilterEnabled = true;
          ctx.mod.saveSettings();
          await tick();
          await tick();

          assert(ctx.postCalls.length === 2, 'the next edit sends a full snapshot');
          ctx.postCalls[1].resolve(response(
            false,
            412,
            '"conversation-settings-2"',
            {
              success: false,
              settings: {
                independentAsrEnabled: false,
                focusModeEnabled: true,
                mergeMessagesEnabled: false,
                slopFilterEnabled: false,
              },
              revision: 2,
              decisions: {},
            }
          ));
          await tick();
          await tick();

          assert(ctx.postCalls.length === 3, 'the CAS mismatch retries once');
          const retryBody = JSON.parse(ctx.postCalls[2].opts.body);
          assert(
            retryBody.mergeMessagesEnabled === false,
            'a sibling edit confirmed by the prior response no longer masks server state'
          );
          assert(
            retryBody.slopFilterEnabled === true,
            'the still-pending local edit survives the CAS merge'
          );

          ctx.postCalls[2].resolve(response(
            true,
            200,
            '"conversation-settings-3"',
            {
              success: true,
              settings: retryBody,
              revision: 3,
              decisions: {},
            }
          ));
          await tick();
        }

        async function main() {
          await runBootMetadataScenario();
          await runScenario(false);
          await runScenario(true);
          await runAcknowledgedDirtyScenario();
          await runSuccessfulPartialSnapshotScenario();
          await runPartialResetSnapshotScenario();
          await runPartialConfirmedWatermarkScenario();
          console.log('CAS_HARNESS_OK');
          process.exitCode = 0;
        }
        main().catch((err) => {
          console.error(err && err.stack ? err.stack : String(err));
          process.exitCode = 1;
        });
        """
    ).replace("__APP_SETTINGS_PATH__", json.dumps(str(APP_SETTINGS_PATH)))

    result = _run_settings_node_harness(harness)
    assert result.returncode == 0, (
        "settings CAS harness failed\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "CAS_HARNESS_OK" in result.stdout
