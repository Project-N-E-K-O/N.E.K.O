from pathlib import Path
import re

import pytest
from playwright.sync_api import Page


REPO_ROOT = Path(__file__).resolve().parents[2]
JUKEBOX_SCRIPT = (REPO_ROOT / "static" / "jukebox" / "Jukebox.js").read_text(encoding="utf-8")
JUKEBOX_LOADER_SCRIPT = (REPO_ROOT / "static" / "jukebox" / "jukebox-loader.js").read_text(encoding="utf-8")
JUKEBOX_TEMPLATE = (REPO_ROOT / "templates" / "jukebox.html").read_text(encoding="utf-8")

HARNESS_HTML = """
<!DOCTYPE html>
<html>
<body>
  <div class="jukebox-container open">
    <div class="jukebox-header">
      <div class="jukebox-header-left"></div>
      <div class="jukebox-header-drag-fill"></div>
      <div class="jukebox-header-buttons"></div>
    </div>
    <div class="jukebox-content">
      <table class="jukebox-table">
        <colgroup>
          <col class="jukebox-col-sequence">
          <col class="jukebox-col-song">
          <col class="jukebox-col-artist">
          <col class="jukebox-col-action">
        </colgroup>
        <thead>
          <tr>
            <th class="jukebox-sequence-th">
              <div class="jukebox-sequence-header">
                <span>序号</span>
                <button type="button" class="jukebox-sort-lock-btn" onclick="Jukebox.toggleSongSortLock(event)" aria-label="解锁歌曲排序" aria-pressed="false"></button>
              </div>
            </th>
            <th>歌曲</th>
            <th>艺术家</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody id="jukebox-song-list"></tbody>
      </table>
    </div>
    <div class="jukebox-controls-row">
      <div class="jukebox-progress">
        <span id="jukebox-time-current">0:00</span>
        <input type="range" id="jukebox-progress-slider" min="0" max="100" step="0.1" value="0">
        <span id="jukebox-time-total">0:00</span>
      </div>
      <div class="jukebox-playback-controls">
        <div id="jukebox-mode-controls" class="jukebox-mode-controls"></div>
        <button id="jukebox-control-prev" type="button" onclick="Jukebox.playAdjacentSong(-1)"></button>
        <button id="jukebox-control-play-pause" type="button" onclick="Jukebox.toggleGlobalPlayPause()"></button>
        <button id="jukebox-control-next" type="button" onclick="Jukebox.playAdjacentSong(1)"></button>
        <div class="jukebox-volume-wrapper">
          <button id="jukebox-speaker-btn" class="jukebox-speaker-btn" type="button">
            <span class="speaker-icon"></span>
            <span class="speaker-muted-icon" style="display: none;"></span>
          </button>
          <div class="jukebox-volume-popup">
            <div class="jukebox-volume-slider-container">
              <div class="jukebox-volume-track"></div>
              <input type="range" id="jukebox-volume-slider" min="0" max="1" step="0.01" value="1">
            </div>
            <div id="jukebox-volume-value">100%</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</body>
</html>
"""


def setup_jukebox_page(mock_page: Page) -> None:
    mock_page.set_content(HARNESS_HTML)
    mock_page.evaluate(
        """
        () => {
          const store = {};
          Object.defineProperty(window, 'localStorage', {
            configurable: true,
            value: {
              getItem(key) {
                return Object.prototype.hasOwnProperty.call(store, key) ? store[key] : null;
              },
              setItem(key, value) {
                store[key] = String(value);
              },
              removeItem(key) {
                delete store[key];
              },
              clear() {
                Object.keys(store).forEach((key) => delete store[key]);
              }
            }
          });
          window.__jukeboxLocalStore = store;
          window.t = (key, fallback) => typeof fallback === 'string' ? fallback : key;
        }
        """
    )
    mock_page.add_script_tag(content=JUKEBOX_SCRIPT)
    mock_page.evaluate("() => window.Jukebox.injectStyles()")
    mock_page.evaluate(
        """
        () => {
          window.Jukebox.State.songs = [
            { id: 'song1', name: 'Song 1', artist: 'A' },
            { id: 'song2', name: 'Song 2', artist: 'B' },
            { id: 'song3', name: 'Song 3', artist: 'C' }
          ];
          window.Jukebox.State.songElements = {};
          window.Jukebox.State.playbackMode = 'sequence';
          window.Jukebox.renderList();
          window.Jukebox.renderPlaybackControls();
        }
        """
    )


def setup_headless_jukebox_page(mock_page: Page) -> None:
    mock_page.set_content("<!DOCTYPE html><html><body></body></html>")
    mock_page.evaluate(
        """
        () => {
          const store = {};
          Object.defineProperty(window, 'localStorage', {
            configurable: true,
            value: {
              getItem(key) {
                return Object.prototype.hasOwnProperty.call(store, key) ? store[key] : null;
              },
              setItem(key, value) {
                store[key] = String(value);
              },
              removeItem(key) {
                delete store[key];
              },
              clear() {
                Object.keys(store).forEach((key) => delete store[key]);
              }
            }
          });
          window.__jukeboxLocalStore = store;
          window.t = (key, fallback) => typeof fallback === 'string' ? fallback : key;
          window.fetch = async (url, options = {}) => {
            if (options.method === 'HEAD') {
              const available = !String(url).includes('missing');
              return { ok: available, status: available ? 200 : 404 };
            }
            if (url === '/api/jukebox/config') {
              return {
                ok: true,
                json: async () => ({
                  configRevision: 'rev-headless',
                  songs: {
                    song1: { name: 'Song 1', artist: 'A', audio: 'songs/song1.mp3', visible: true },
                    song2: { name: 'Song 2', artist: 'B', audio: 'songs/song2.mp3', visible: true },
                    song3: { name: 'Song 3', artist: 'C', audio: 'songs/song3.mp3', visible: true },
                    song4: { name: '桃源恋歌', artist: 'GARNiDELiA', audio: 'songs/tougen-renka.mp3', visible: true }
                  },
                  actions: {},
                  bindings: {}
                })
              };
            }
            throw new Error('Unexpected fetch: ' + url);
          };
          window.APlayer = class {
            constructor(options) {
              this.options = options;
              this.audio = { volume: options.volume || 1, duration: 0, currentTime: 0, paused: true };
              this.events = {};
              this.list = {
                items: [],
                clear: () => { this.list.items = []; },
                add: (items) => { this.list.items = items; }
              };
              window.__lastAPlayer = this;
            }
            on(name, handler) { this.events[name] = handler; }
            play() { this.audio.paused = false; this.played = true; }
            pause() { this.audio.paused = true; }
            seek(value) { this.audio.currentTime = value; }
            destroy() { this.destroyed = true; }
          };
        }
        """
    )
    mock_page.add_script_tag(content=JUKEBOX_SCRIPT)


@pytest.mark.frontend
def test_jukebox_execute_control_play_headless_loads_without_ui(mock_page: Page):
    setup_headless_jukebox_page(mock_page)

    result = mock_page.evaluate(
        """
        async () => {
          const result = await window.Jukebox.executeControl({ action: 'play', query: 'Song' });
          return {
            result,
            hasUi: !!document.querySelector('.jukebox-wrapper'),
            hasRuntimeHost: !!document.getElementById('neko-jukebox-runtime-host'),
            currentSong: window.Jukebox.State.currentSong && window.Jukebox.State.currentSong.id,
            isRuntimeReady: window.Jukebox.State.isRuntimeReady,
            playerItems: window.__lastAPlayer.list.items.map((item) => item.name),
            playerUrls: window.__lastAPlayer.list.items.map((item) => item.url)
          };
        }
        """
    )

    assert result == {
        "result": {
            "ok": True,
            "action": "play",
            "song": {"id": "song1", "name": "Song 1", "artist": "A"},
            "actionStatus": "no_action",
        },
        "hasUi": False,
        "hasRuntimeHost": True,
        "currentSong": "song1",
        "isRuntimeReady": True,
        "playerItems": ["Song 1"],
        "playerUrls": ["/api/jukebox/file/songs/song1.mp3"],
    }


@pytest.mark.frontend
def test_jukebox_execute_control_same_song_replays_instead_of_stopping(mock_page: Page):
    setup_headless_jukebox_page(mock_page)

    result = mock_page.evaluate(
        """
        async () => {
          const first = await window.Jukebox.executeControl({ action: 'play', query: 'Song 1' });
          const second = await window.Jukebox.executeControl({ action: 'play', query: 'Song 1' });
          return {
            first,
            second,
            currentSong: window.Jukebox.State.currentSong && window.Jukebox.State.currentSong.id,
            isPlaying: window.Jukebox.State.isPlaying,
            isPaused: window.Jukebox.State.isPaused,
            playerItems: window.__lastAPlayer.list.items.map((item) => item.name)
          };
        }
        """
    )

    assert result == {
        "first": {
            "ok": True,
            "action": "play",
            "song": {"id": "song1", "name": "Song 1", "artist": "A"},
            "actionStatus": "no_action",
        },
        "second": {
            "ok": True,
            "action": "play",
            "song": {"id": "song1", "name": "Song 1", "artist": "A"},
            "actionStatus": "no_action",
        },
        "currentSong": "song1",
        "isPlaying": True,
        "isPaused": False,
        "playerItems": ["Song 1"],
    }


@pytest.mark.frontend
def test_jukebox_execute_control_discards_stale_preflight_play(mock_page: Page):
    setup_headless_jukebox_page(mock_page)

    result = mock_page.evaluate(
        """
        async () => {
          const originalFetch = window.fetch;
          let releaseSong1Head;
          window.fetch = async (url, options = {}) => {
            if (options.method === 'HEAD' && String(url).includes('song1.mp3')) {
              return await new Promise((resolve) => {
                releaseSong1Head = () => resolve({ ok: true, status: 200 });
              });
            }
            return originalFetch(url, options);
          };

          const firstPromise = window.Jukebox.executeControl({ action: 'play', query: 'Song 1' });
          while (typeof releaseSong1Head !== 'function') {
            await new Promise((resolve) => setTimeout(resolve, 0));
          }
          const second = await window.Jukebox.executeControl({ action: 'play', query: 'Song 2' });
          releaseSong1Head();
          const first = await firstPromise;

          return {
            first,
            second,
            currentSong: window.Jukebox.State.currentSong && window.Jukebox.State.currentSong.id,
            playerItems: window.__lastAPlayer.list.items.map((item) => item.name)
          };
        }
        """
    )

    assert result == {
        "first": {
            "ok": False,
            "action": "play",
            "message": "play_superseded",
            "song": {"id": "song1", "name": "Song 1", "artist": "A"},
        },
        "second": {
            "ok": True,
            "action": "play",
            "song": {"id": "song2", "name": "Song 2", "artist": "B"},
            "actionStatus": "no_action",
        },
        "currentSong": "song2",
        "playerItems": ["Song 2"],
    }


@pytest.mark.frontend
def test_jukebox_execute_control_stop_discards_pending_play(mock_page: Page):
    setup_headless_jukebox_page(mock_page)

    result = mock_page.evaluate(
        """
        async () => {
          const originalFetch = window.fetch;
          let releaseSong1Head;
          window.fetch = async (url, options = {}) => {
            if (options.method === 'HEAD' && String(url).includes('song1.mp3')) {
              return await new Promise((resolve) => {
                releaseSong1Head = () => resolve({ ok: true, status: 200 });
              });
            }
            return originalFetch(url, options);
          };

          const playPromise = window.Jukebox.executeControl({ action: 'play', query: 'Song 1' });
          while (typeof releaseSong1Head !== 'function') {
            await new Promise((resolve) => setTimeout(resolve, 0));
          }
          const stop = await window.Jukebox.executeControl({ action: 'stop' });
          releaseSong1Head();
          const play = await playPromise;

          return {
            play,
            stop,
            currentSong: window.Jukebox.State.currentSong && window.Jukebox.State.currentSong.id,
            isPlaying: window.Jukebox.State.isPlaying,
            playerItems: window.__lastAPlayer.list.items.map((item) => item.name)
          };
        }
        """
    )

    assert result == {
        "play": {
            "ok": False,
            "action": "play",
            "message": "play_superseded",
            "song": {"id": "song1", "name": "Song 1", "artist": "A"},
        },
        "stop": {"ok": True, "action": "stop"},
        "currentSong": None,
        "isPlaying": False,
        "playerItems": [],
    }


@pytest.mark.frontend
def test_jukebox_play_song_skips_stale_action_start(mock_page: Page):
    setup_headless_jukebox_page(mock_page)

    result = mock_page.evaluate(
        """
        async () => {
          const J = window.Jukebox;
          await J.ensureRuntime({ headless: true });

          let releaseAction;
          const animationCalls = [];
          J.getModelType = () => 'vrm';
          J.playVRMA = async (url) => { animationCalls.push(url); };
          J.getActionAvailability = async (song) => {
            if (song.id === 'song1') {
              return await new Promise((resolve) => {
                releaseAction = () => resolve({
                  ok: true,
                  status: 'action_ready',
                  action: { id: 'action1', name: 'Dance 1', file: 'actions/song1.vrma' },
                  url: '/api/jukebox/file/actions/song1.vrma'
                });
              });
            }
            return { ok: true, status: 'no_action', action: null, url: '' };
          };

          const firstPromise = J.playSong('song1');
          while (typeof releaseAction !== 'function') {
            await new Promise((resolve) => setTimeout(resolve, 0));
          }
          const second = await J.playSong('song2');
          releaseAction();
          const first = await firstPromise;

          return {
            first: first && first.id,
            second: second && second.id,
            currentSong: J.State.currentSong && J.State.currentSong.id,
            animationCalls
          };
        }
        """
    )

    assert result == {
        "first": None,
        "second": "song2",
        "currentSong": "song2",
        "animationCalls": [],
    }


@pytest.mark.frontend
def test_jukebox_play_song_skips_stale_vrma_internal_start(mock_page: Page):
    setup_headless_jukebox_page(mock_page)

    result = mock_page.evaluate(
        """
        async () => {
          const J = window.Jukebox;
          await J.ensureRuntime({ headless: true });

          let releaseAnimation;
          const animationStarts = [];
          J.getModelType = () => 'vrm';
          J.getActionAvailability = async (song) => {
            if (song.id === 'song1') {
              return {
                ok: true,
                status: 'action_ready',
                action: { id: 'action1', name: 'Dance 1', file: 'actions/song1.vrma' },
                url: '/api/jukebox/file/actions/song1.vrma'
              };
            }
            return { ok: true, status: 'no_action', action: null, url: '' };
          };
          window.vrmManager = {
            playVRMAAnimation: async (url, options = {}) => {
              return await new Promise((resolve) => {
                releaseAnimation = () => {
                  const shouldStart = typeof options.shouldStart === 'function' ? options.shouldStart() : true;
                  if (shouldStart) animationStarts.push(url);
                  resolve(shouldStart);
                };
              });
            }
          };

          const firstPromise = J.playSong('song1');
          while (typeof releaseAnimation !== 'function') {
            await new Promise((resolve) => setTimeout(resolve, 0));
          }
          const second = await J.playSong('song2');
          releaseAnimation();
          const first = await firstPromise;

          return {
            first: first && first.id,
            second: second && second.id,
            currentSong: J.State.currentSong && J.State.currentSong.id,
            animationStarts,
            isVMDPlaying: J.State.isVMDPlaying
          };
        }
        """
    )

    assert result == {
        "first": None,
        "second": "song2",
        "currentSong": "song2",
        "animationStarts": [],
        "isVMDPlaying": False,
    }


@pytest.mark.frontend
def test_jukebox_execute_control_play_uses_fuzzy_matching(mock_page: Page):
    setup_headless_jukebox_page(mock_page)

    result = mock_page.evaluate(
        """
        async () => {
          const result = await window.Jukebox.executeControl({ action: 'play', query: '桃园' });
          return {
            result,
            currentSong: window.Jukebox.State.currentSong && window.Jukebox.State.currentSong.id,
            playerItems: window.__lastAPlayer.list.items.map((item) => item.name)
          };
        }
        """
    )

    assert result == {
        "result": {
            "ok": True,
            "action": "play",
            "song": {"id": "song4", "name": "桃源恋歌", "artist": "GARNiDELiA"},
            "actionStatus": "no_action",
        },
        "currentSong": "song4",
        "playerItems": ["桃源恋歌"],
    }


@pytest.mark.frontend
def test_jukebox_execute_control_uses_canonical_control_keys(mock_page: Page):
    setup_headless_jukebox_page(mock_page)

    result = mock_page.evaluate(
        """
        async () => {
          const skipResult = await window.Jukebox.executeControl({ action: 'skip' });
          const cutResult = await window.Jukebox.executeControl({ action: 'cut' });
          const commandOnlyResult = await window.Jukebox.executeControl({ command: 'stop' });
          const legacyNameResult = await window.Jukebox.executeControl({ action: 'play', name: 'Song 2' });
          return {
            skipResult,
            cutResult,
            commandOnlyResult,
            legacyNameResult,
            currentSong: window.Jukebox.State.currentSong && window.Jukebox.State.currentSong.id
          };
        }
        """
    )

    assert result == {
        "skipResult": {
            "ok": False,
            "action": "skip",
            "message": "unsupported_jukebox_action",
        },
        "cutResult": {
            "ok": False,
            "action": "cut",
            "message": "unsupported_jukebox_action",
        },
        "commandOnlyResult": {
            "ok": False,
            "action": "",
            "message": "unsupported_jukebox_action",
        },
        "legacyNameResult": {
            "ok": True,
            "action": "play",
            "song": {"id": "song1", "name": "Song 1", "artist": "A"},
            "actionStatus": "no_action",
        },
        "currentSong": "song1",
    }


@pytest.mark.frontend
def test_jukebox_execute_control_sets_and_adjusts_volume_headless(mock_page: Page):
    setup_headless_jukebox_page(mock_page)

    result = mock_page.evaluate(
        """
        async () => {
          const setResult = await window.Jukebox.executeControl({ action: 'set_volume', value: 35 });
          const afterSet = window.__lastAPlayer.audio.volume;
          const adjustResult = await window.Jukebox.executeControl({ action: 'adjust_volume', value: 10 });
          const afterAdjust = window.__lastAPlayer.audio.volume;
          const invalidSet = await window.Jukebox.executeControl({ action: 'set_volume', value: 130 });
          const invalidAdjust = await window.Jukebox.executeControl({ action: 'adjust_volume', value: 'louder' });
          return {
            setResult,
            afterSet,
            adjustResult,
            afterAdjust,
            invalidSet,
            invalidAdjust,
            hasUi: !!document.querySelector('.jukebox-wrapper'),
            hasRuntimeHost: !!document.getElementById('neko-jukebox-runtime-host')
          };
        }
        """
    )

    assert result == {
        "setResult": {"ok": True, "action": "set_volume", "volume": 0.35},
        "afterSet": 0.35,
        "adjustResult": {"ok": True, "action": "adjust_volume", "volume": 0.45, "value": 0.1},
        "afterAdjust": 0.45,
        "invalidSet": {"ok": False, "action": "set_volume", "message": "invalid_volume"},
        "invalidAdjust": {"ok": False, "action": "adjust_volume", "message": "invalid_volume_delta"},
        "hasUi": False,
        "hasRuntimeHost": True,
    }


@pytest.mark.frontend
def test_jukebox_execute_control_sets_playback_mode_without_ui(mock_page: Page):
    setup_headless_jukebox_page(mock_page)

    result = mock_page.evaluate(
        """
        async () => {
          const randomResult = await window.Jukebox.executeControl({ action: 'set_mode', mode: 'random' });
          const invalidResult = await window.Jukebox.executeControl({ action: 'set_mode', mode: 'shuffle' });
          return {
            randomResult,
            invalidResult,
            playbackMode: window.Jukebox.State.playbackMode,
            storedMode: window.localStorage.getItem('neko.jukebox.playbackMode'),
            hasUi: !!document.querySelector('.jukebox-wrapper'),
            hasRuntimeHost: !!document.getElementById('neko-jukebox-runtime-host')
          };
        }
        """
    )

    assert result == {
        "randomResult": {"ok": True, "action": "set_mode", "mode": "random"},
        "invalidResult": {"ok": False, "action": "set_mode", "message": "invalid_playback_mode"},
        "playbackMode": "random",
        "storedMode": '"random"',
        "hasUi": False,
        "hasRuntimeHost": False,
    }


@pytest.mark.frontend
def test_jukebox_builtin_paths_keep_resource_directories(mock_page: Page):
    setup_headless_jukebox_page(mock_page)

    result = mock_page.evaluate(
        """
        async () => {
          window.fetch = async (url, options = {}) => {
            if (options.method === 'HEAD') {
              return { ok: !String(url).includes('missing'), status: String(url).includes('missing') ? 404 : 200 };
            }
            if (url === '/api/jukebox/config') {
              return {
                ok: true,
                json: async () => ({
                  configRevision: 'rev-builtin-paths',
                  songs: {
                    song_001: {
                      name: '桃源恋歌',
                      artist: 'GARNiDELiA',
                      audio: 'songs/song_001.mp3',
                      visible: true,
                      isBuiltin: true,
                      defaultAction: 'action_001'
                    }
                  },
                  actions: {
                    action_001: {
                      name: '桃源恋歌',
                      file: 'actions/song_001.vrma',
                      format: 'vrma',
                      visible: true,
                      isBuiltin: true
                    }
                  },
                  bindings: {
                    song_001: { action_001: { offset: 0 } }
                  }
                })
              };
            }
            throw new Error('Unexpected fetch: ' + url);
          };
          window.lanlan_config = { model_type: 'live3d', live3d_sub_type: 'vrm' };
          const vrmaCalls = [];
          window.vrmManager = {
            playVRMAAnimation: async (url) => vrmaCalls.push(url)
          };

          await window.Jukebox.executeControl({ action: 'play', query: '桃园' });

          return {
            audio: window.Jukebox.State.songs[0].audio,
            audioUrl: window.__lastAPlayer.list.items[0].url,
            vrmaCalls
          };
        }
        """
    )

    assert result == {
        "audio": "songs/song_001.mp3",
        "audioUrl": "/api/jukebox/file/songs/song_001.mp3",
        "vrmaCalls": ["/api/jukebox/file/actions/song_001.vrma"],
    }


@pytest.mark.frontend
def test_jukebox_execute_control_does_not_play_when_audio_missing(mock_page: Page):
    setup_headless_jukebox_page(mock_page)

    result = mock_page.evaluate(
        """
        async () => {
          window.fetch = async (url, options = {}) => {
            if (options.method === 'HEAD') {
              return { ok: !String(url).includes('missing'), status: String(url).includes('missing') ? 404 : 200 };
            }
            if (url === '/api/jukebox/config') {
              return {
                ok: true,
                json: async () => ({
                  configRevision: 'rev-missing-audio',
                  songs: {
                    missingSong: {
                      name: 'Missing Song',
                      artist: 'A',
                      audio: 'songs/missing.mp3',
                      visible: true
                    }
                  },
                  actions: {},
                  bindings: {}
                })
              };
            }
            throw new Error('Unexpected fetch: ' + url);
          };

          const result = await window.Jukebox.executeControl({ action: 'play', query: 'Missing' });
          return {
            result,
            playerItems: window.__lastAPlayer.list.items,
            played: window.__lastAPlayer.played === true,
            currentSong: window.Jukebox.State.currentSong
          };
        }
        """
    )

    assert result == {
        "result": {
            "ok": False,
            "action": "play",
            "message": "audio_not_found",
            "song": {"id": "missingSong", "name": "Missing Song", "artist": "A"},
        },
        "playerItems": [],
        "played": False,
        "currentSong": None,
    }


@pytest.mark.frontend
def test_jukebox_execute_control_skips_missing_action_but_plays_audio(mock_page: Page):
    setup_headless_jukebox_page(mock_page)

    result = mock_page.evaluate(
        """
        async () => {
          window.fetch = async (url, options = {}) => {
            if (options.method === 'HEAD') {
              return { ok: !String(url).includes('missing-action'), status: String(url).includes('missing-action') ? 404 : 200 };
            }
            if (url === '/api/jukebox/config') {
              return {
                ok: true,
                json: async () => ({
                  configRevision: 'rev-missing-action',
                  songs: {
                    songWithMissingAction: {
                      name: 'Song With Missing Action',
                      artist: 'A',
                      audio: 'songs/song1.mp3',
                      visible: true,
                      defaultAction: 'missingAction'
                    }
                  },
                  actions: {
                    missingAction: {
                      name: 'Missing Action',
                      file: 'actions/missing-action.vrma',
                      format: 'vrma',
                      visible: true
                    }
                  },
                  bindings: {
                    songWithMissingAction: { missingAction: { offset: 0 } }
                  }
                })
              };
            }
            throw new Error('Unexpected fetch: ' + url);
          };
          window.lanlan_config = { model_type: 'live3d', live3d_sub_type: 'vrm' };
          const vrmaCalls = [];
          window.vrmManager = {
            playVRMAAnimation: async (url) => vrmaCalls.push(url)
          };

          const result = await window.Jukebox.executeControl({ action: 'play', query: 'Missing Action' });
          return {
            result,
            playerItems: window.__lastAPlayer.list.items.map((item) => item.name),
            played: window.__lastAPlayer.played === true,
            currentSong: window.Jukebox.State.currentSong && window.Jukebox.State.currentSong.id,
            vrmaCalls
          };
        }
        """
    )

    assert result == {
        "result": {
            "ok": True,
            "action": "play",
            "song": {"id": "songWithMissingAction", "name": "Song With Missing Action", "artist": "A"},
            "actionStatus": "action_not_found",
        },
        "playerItems": ["Song With Missing Action"],
        "played": True,
        "currentSong": "songWithMissingAction",
        "vrmaCalls": [],
    }


@pytest.mark.frontend
def test_jukebox_close_preserves_headless_runtime(mock_page: Page):
    setup_headless_jukebox_page(mock_page)

    result = mock_page.evaluate(
        """
        async () => {
          const J = window.Jukebox;
          await J.executeControl({ action: 'play', query: 'Song 1', headless: true });
          let fullCloseEvents = 0;
          window.addEventListener('neko:jukebox-full-close', () => { fullCloseEvents += 1; });

          const wrapper = document.createElement('div');
          wrapper.className = 'jukebox-wrapper';
          wrapper.innerHTML = '<div class="jukebox-container"></div>';
          document.body.appendChild(wrapper);
          const style = document.createElement('style');
          document.head.appendChild(style);

          J.State.container = wrapper;
          J.State.styleElement = style;
          J.State.isOpen = true;
          J.State.isHidden = false;
          J._broadcastChannel = {
            onmessage: () => {},
            closed: false,
            close() { this.closed = true; }
          };
          const channel = J._broadcastChannel;

          J.close();

          return {
            fullCloseEvents,
            hasUi: !!document.querySelector('.jukebox-wrapper'),
            hasStyle: document.head.contains(style),
            hasRuntimeHost: !!document.getElementById('neko-jukebox-runtime-host'),
            isRuntimeReady: J.State.isRuntimeReady,
            playerHost: J.State.playerHost,
            playerDestroyed: window.__lastAPlayer.destroyed === true,
            currentSong: J.State.currentSong && J.State.currentSong.id,
            songCount: J.State.songs.length,
            channelClosed: channel.closed === true
          };
        }
        """
    )

    assert result == {
        "fullCloseEvents": 0,
        "hasUi": False,
        "hasStyle": False,
        "hasRuntimeHost": True,
        "isRuntimeReady": True,
        "playerHost": "runtime",
        "playerDestroyed": False,
        "currentSong": "song1",
        "songCount": 4,
        "channelClosed": True,
    }


@pytest.mark.frontend
def test_jukebox_execute_control_next_and_stop_headless(mock_page: Page):
    setup_headless_jukebox_page(mock_page)

    result = mock_page.evaluate(
        """
        async () => {
          await window.Jukebox.executeControl({ action: 'play', query: 'Song 1' });
          const nextResult = await window.Jukebox.executeControl({ action: 'next' });
          const previousResult = await window.Jukebox.executeControl({ action: 'previous' });
          window.Jukebox.State.playbackMode = 'random';
          window.Jukebox.State.randomQueue = ['song1', 'song2'];
          window.Jukebox.State.randomQueueIndex = 1;
          const stopResult = await window.Jukebox.executeControl({ action: 'stop' });
          return {
            nextResult,
            previousResult,
            stopResult,
            currentSong: window.Jukebox.State.currentSong,
            isPlaying: window.Jukebox.State.isPlaying,
            randomQueue: window.Jukebox.State.randomQueue,
            randomQueueIndex: window.Jukebox.State.randomQueueIndex,
            hasRuntimeHost: !!document.getElementById('neko-jukebox-runtime-host')
          };
        }
        """
    )

    assert result["nextResult"] == {
        "ok": True,
        "action": "next",
        "song": {"id": "song2", "name": "Song 2", "artist": "B"},
        "actionStatus": "no_action",
    }
    assert result["previousResult"] == {
        "ok": True,
        "action": "previous",
        "song": {"id": "song1", "name": "Song 1", "artist": "A"},
        "actionStatus": "no_action",
    }
    assert result["stopResult"] == {"ok": True, "action": "stop"}
    assert result["currentSong"] is None
    assert result["isPlaying"] is False
    assert result["randomQueue"] == []
    assert result["randomQueueIndex"] == -1
    assert result["hasRuntimeHost"] is True


@pytest.mark.frontend
def test_jukebox_loader_native_mode_keeps_animation_facade(mock_page: Page):
    mock_page.set_content(
        """
        <script>
          window.nativeToggled = false;
          window.__nekoJukeboxToggle = function() {
            window.nativeToggled = true;
          };
          window.t = (key, fallback) => typeof fallback === 'string' ? fallback : key;
        </script>
        """
    )
    mock_page.add_script_tag(content=JUKEBOX_LOADER_SCRIPT)

    result = mock_page.evaluate(
        """
        async () => {
          const calls = [];
          window.lanlan_config = { model_type: 'live3d', live3d_sub_type: 'mmd' };
          window.mmdManager = {
            currentAnimationUrl: '/idle.vmd',
            currentModel: { mesh: { skeleton: { pose: () => calls.push('pose') } } },
            animationModule: {
              stop: () => calls.push('stop'),
              pause: () => calls.push('pause'),
              play: () => calls.push('module-play')
            },
            cursorFollow: {
              setAnimationMode: (mode) => calls.push('cursor:' + mode)
            },
            loadAnimation: async (path) => calls.push('load:' + path),
            playAnimation: (mode) => calls.push('play:' + mode)
          };

          await window.Jukebox.playVMD('/dance.vmd');
          window.Jukebox.togglePause();
          window.Jukebox.togglePause();
          window.Jukebox.stopVMD(true);
          window.Jukebox.toggle();

          return {
            hasFacade: window.Jukebox.__nativeBridgeFacade === true,
            hasExecuteControl: typeof window.Jukebox.executeControl === 'function',
            hasInit: typeof window.Jukebox.init === 'function',
            nativeToggled: window.nativeToggled,
            webLoaderToggle: !!window.__nekoJukeboxToggle.__nekoJukeboxWebLoader,
            loaderReady: !!window.__nekoJukeboxLoader,
            state: {
              isPlaying: window.Jukebox.State.isPlaying,
              isVMDPlaying: window.Jukebox.State.isVMDPlaying,
              isPaused: window.Jukebox.State.isPaused
            },
            calls
          };
        }
        """
    )

    assert result == {
        "hasFacade": True,
        "hasExecuteControl": True,
        "hasInit": True,
        "nativeToggled": True,
        "webLoaderToggle": False,
        "loaderReady": True,
        "state": {
            "isPlaying": False,
            "isVMDPlaying": False,
            "isPaused": False,
        },
        "calls": [
            "load:/dance.vmd",
            "play:dance",
            "pause",
            "cursor:idle",
            "module-play",
            "cursor:dance",
            "stop",
        ],
    }


@pytest.mark.frontend
def test_jukebox_loader_restores_native_facade_after_full_unload(mock_page: Page):
    mock_page.set_content(
        """
        <script>
          window.nativeToggleCount = 0;
          window.__nekoJukeboxToggle = function() {
            window.nativeToggleCount += 1;
          };
          window.t = (key, fallback) => typeof fallback === 'string' ? fallback : key;
        </script>
        """
    )
    mock_page.add_script_tag(content=JUKEBOX_LOADER_SCRIPT)

    result = mock_page.evaluate(
        """
        () => {
          const originalSetTimeout = window.setTimeout;
          window.setTimeout = (handler, delay) => {
            if (delay === 3000) {
              handler();
              return 1;
            }
            return originalSetTimeout(handler, delay);
          };

          window.__nekoJukeboxLoader.unload();
          window.Jukebox.toggle();

          return {
            hasFacade: window.Jukebox.__nativeBridgeFacade === true,
            hasExecuteControl: typeof window.Jukebox.executeControl === 'function',
            nativeToggleCount: window.nativeToggleCount,
            webLoaderToggle: !!window.__nekoJukeboxToggle.__nekoJukeboxWebLoader
          };
        }
        """
    )

    assert result == {
        "hasFacade": True,
        "hasExecuteControl": True,
        "nativeToggleCount": 1,
        "webLoaderToggle": False,
    }


@pytest.mark.frontend
def test_jukebox_loader_exposes_control_on_jukebox_key_only(mock_page: Page):
    mock_page.set_content(
        """
        <script>
          window.t = (key, fallback) => typeof fallback === 'string' ? fallback : key;
        </script>
        """
    )
    mock_page.add_script_tag(content=JUKEBOX_LOADER_SCRIPT)

    result = mock_page.evaluate(
        """
        () => ({
          hasJukeboxFacade: !!window.Jukebox && window.Jukebox.__nekoLazyFacade === true,
          hasExecuteControl: typeof window.Jukebox.executeControl === 'function',
          hasEnsureRuntime: typeof window.Jukebox.ensureRuntime === 'function',
          hasInit: typeof window.Jukebox.init === 'function',
          initReturns: window.Jukebox.init(),
          loaderHasControl: Object.prototype.hasOwnProperty.call(window.__nekoJukeboxLoader, 'control')
        })
        """
    )

    assert result == {
        "hasJukeboxFacade": True,
        "hasExecuteControl": True,
        "hasEnsureRuntime": True,
        "hasInit": True,
        "initReturns": None,
        "loaderHasControl": False,
    }


@pytest.mark.frontend
def test_jukebox_loader_reloads_stale_control_api_with_versioned_url(mock_page: Page):
    requested_urls = []

    def fulfill_loader(route):
        route.fulfill(
            status=200,
            content_type="application/javascript",
            body=JUKEBOX_LOADER_SCRIPT,
        )

    def fulfill_jukebox(route):
        requested_urls.append(route.request.url)
        route.fulfill(
            status=200,
            content_type="application/javascript",
            body="""
              window.Jukebox = {
                controlApiVersion: 3,
                supportedControlActions: ['play', 'next', 'previous', 'stop', 'set_volume', 'adjust_volume', 'set_mode'],
                init() { window.__jukeboxInitCalled = true; },
                executeControl: async (command) => ({
                  ok: true,
                  action: command.action,
                  controlApiVersion: window.Jukebox.controlApiVersion
                })
              };
            """,
        )

    mock_page.route("**/static/jukebox/jukebox-loader.js*", fulfill_loader)
    mock_page.route("**/static/jukebox/Jukebox.js*", fulfill_jukebox)
    mock_page.set_content(
        """
        <!DOCTYPE html>
        <html>
        <head><base href="http://127.0.0.1:48911/"></head>
        <body>
          <script>
            window.t = (key, fallback) => typeof fallback === 'string' ? fallback : key;
            window.Jukebox = {
              controlApiVersion: 1,
              executeControl: async (command) => ({
                ok: false,
                action: command.action,
                message: 'stale-control-api'
              })
            };
          </script>
        </body>
        </html>
        """
    )
    mock_page.add_script_tag(url="http://127.0.0.1:48911/static/jukebox/jukebox-loader.js?v=test-assets")

    result = mock_page.evaluate(
        """
        async () => {
          const result = await window.Jukebox.executeControl({ action: 'adjust_volume', value: 20 });
          return {
            result,
            initCalled: window.__jukeboxInitCalled === true,
            controlApiVersion: window.Jukebox.controlApiVersion,
            supported: window.Jukebox.supportedControlActions
          };
        }
        """
    )

    assert result == {
        "result": {"ok": True, "action": "adjust_volume", "controlApiVersion": 3},
        "initCalled": True,
        "controlApiVersion": 3,
        "supported": ["play", "next", "previous", "stop", "set_volume", "adjust_volume", "set_mode"],
    }
    assert len(requested_urls) == 1
    assert "v=test-assets" in requested_urls[0]
    assert "jukebox_control_api=3" in requested_urls[0]


def test_jukebox_control_api_declares_versioned_supported_actions():
    assert "controlApiVersion: 3" in JUKEBOX_SCRIPT
    assert "supportedControlActions: ['play', 'next', 'previous', 'stop', 'set_volume', 'adjust_volume', 'set_mode']" in JUKEBOX_SCRIPT
    assert "REQUIRED_CONTROL_API_VERSION = 3" in JUKEBOX_LOADER_SCRIPT
    assert "jukebox_control_api" in JUKEBOX_LOADER_SCRIPT


def test_jukebox_action_column_reserves_space_for_two_buttons():
    assert ".jukebox-table col.jukebox-col-action {\n        width: 104px;" in JUKEBOX_SCRIPT
    assert ".jukebox-table td.song-action" in JUKEBOX_SCRIPT
    assert "justify-content: center;" in JUKEBOX_SCRIPT


def test_jukebox_sequence_column_reserves_lock_space_and_centers_numbers():
    assert ".jukebox-table col.jukebox-col-sequence {\n        width: 66px;" in JUKEBOX_SCRIPT
    assert ".jukebox-sort-lock-btn {\n        width: 22px;" in JUKEBOX_SCRIPT
    assert ".jukebox-sort-lock-btn svg {\n        width: 14px;" in JUKEBOX_SCRIPT
    assert ".jukebox-table td.song-index" in JUKEBOX_SCRIPT
    assert "text-align: center;" in JUKEBOX_SCRIPT
    assert ".song-index-number" in JUKEBOX_SCRIPT
    assert "justify-content: center;" in JUKEBOX_SCRIPT


def test_jukebox_header_owns_top_drag_region_instead_of_container_padding():
    container_match = re.search(r"\.jukebox-container\s*\{(?P<body>[\s\S]*?)\n\s*\}", JUKEBOX_SCRIPT)
    assert container_match is not None
    assert re.search(r"padding:\s*0;", container_match.group("body"))

    header_match = re.search(r"\.jukebox-header\s*\{(?P<body>[\s\S]*?)\n\s*\}", JUKEBOX_SCRIPT)
    assert header_match is not None
    assert re.search(r"padding:\s*20px 20px 10px;", header_match.group("body"))
    assert re.search(r"cursor:\s*grab;", header_match.group("body"))

    assert re.search(r"\.jukebox-content\s*\{[\s\S]*?margin:\s*0 20px;", JUKEBOX_SCRIPT)
    assert re.search(r"\.jukebox-controls-row\s*\{[\s\S]*?margin:\s*15px 20px 20px;", JUKEBOX_SCRIPT)


def test_jukebox_list_area_flexes_between_header_and_bottom_player():
    container_match = re.search(r"\.jukebox-container\s*\{(?P<body>[\s\S]*?)\n\s*\}", JUKEBOX_SCRIPT)
    assert container_match is not None
    container_body = container_match.group("body")
    assert re.search(r"display:\s*flex;", container_body)
    assert re.search(r"flex-direction:\s*column;", container_body)
    assert re.search(r"height:\s*calc\(100vh - 40px\);", container_body)
    assert re.search(r"max-height:\s*calc\(100vh - 40px\);", container_body)
    assert re.search(r"overflow:\s*hidden;", container_body)

    content_match = re.search(r"\.jukebox-content\s*\{(?P<body>[\s\S]*?)\n\s*\}", JUKEBOX_SCRIPT)
    assert content_match is not None
    content_body = content_match.group("body")
    assert re.search(r"flex:\s*1 1 auto;", content_body)
    assert re.search(r"overflow-y:\s*auto;", content_body)
    assert re.search(r"min-height:\s*0;", content_body)
    assert not re.search(r"max-height:\s*270px;", content_body)

    controls_match = re.search(r"\.jukebox-controls-row\s*\{(?P<body>[\s\S]*?)\n\s*\}", JUKEBOX_SCRIPT)
    assert controls_match is not None
    assert re.search(r"flex:\s*0 0 auto;", controls_match.group("body"))


def test_jukebox_injected_standalone_styles_disable_open_close_transform_transition():
    assert "html.neko-jukebox-standalone-host" in JUKEBOX_SCRIPT
    assert "html[data-theme=\"dark\"].neko-jukebox-standalone-host" in JUKEBOX_SCRIPT
    assert "body.neko-jukebox-standalone-page .jukebox-container.open" in JUKEBOX_SCRIPT
    assert "body.neko-jukebox-standalone-page .jukebox-container.hidden" in JUKEBOX_SCRIPT
    assert "body.neko-jukebox-standalone-page .jukebox-container.open" in JUKEBOX_TEMPLATE
    assert "body.neko-jukebox-standalone-page .jukebox-container.hidden" in JUKEBOX_TEMPLATE
    assert "transition: none !important;" in JUKEBOX_TEMPLATE
    assert "transform: none !important;" in JUKEBOX_TEMPLATE


@pytest.mark.frontend
def test_jukebox_web_window_size_is_saved_and_restored(mock_page: Page):
    setup_jukebox_page(mock_page)

    result = mock_page.evaluate(
        """
        () => {
          const J = window.Jukebox;
          const container = document.querySelector('.jukebox-container');
          container.style.width = '432px';
          container.style.height = '376px';
          J.saveWindowSize(container);

          container.style.width = '';
          container.style.height = '';
          J.applyStoredWindowSize(container);

          return {
            stored: JSON.parse(window.__jukeboxLocalStore['neko.jukebox.windowSize']),
            width: container.style.width,
            height: container.style.height
          };
        }
        """
    )

    assert result["stored"] == {"width": 432, "height": 376}
    assert result["width"] == "432px"
    assert result["height"] == "376px"


@pytest.mark.frontend
def test_jukebox_web_resize_click_without_delta_does_not_save_size(mock_page: Page):
    setup_jukebox_page(mock_page)

    result = mock_page.evaluate(
        """
        () => {
          const J = window.Jukebox;
          const container = document.querySelector('.jukebox-container');
          const handle = document.createElement('div');
          handle.className = 'jukebox-resize-handle';
          handle.dataset.dir = 'se';
          container.appendChild(handle);

          J.State.hasCustomWindowSize = false;
          J.bindResize(container);

          handle.dispatchEvent(new MouseEvent('mousedown', {
            bubbles: true,
            cancelable: true,
            clientX: 100,
            clientY: 100
          }));
          document.dispatchEvent(new MouseEvent('mouseup', {
            bubbles: true,
            cancelable: true,
            clientX: 100,
            clientY: 100
          }));

          return {
            hasCustomWindowSize: J.State.hasCustomWindowSize,
            stored: window.__jukeboxLocalStore['neko.jukebox.windowSize'] || null,
            resizingClass: document.body.classList.contains('jukebox-resizing')
          };
        }
        """
    )

    assert result == {
        "hasCustomWindowSize": False,
        "stored": None,
        "resizingClass": False,
    }


@pytest.mark.frontend
def test_jukebox_content_height_expands_while_bottom_player_stays_inside(mock_page: Page):
    setup_jukebox_page(mock_page)

    result = mock_page.evaluate(
        """
        () => {
          const J = window.Jukebox;
          J.State.songs = Array.from({ length: 30 }, (_, index) => ({
            id: `song-${index}`,
            name: `Song ${index}`,
            artist: 'Artist'
          }));
          J.State.songElements = {};
          J.renderList();

          const container = document.querySelector('.jukebox-container');
          const content = document.querySelector('.jukebox-content');
          const controls = document.querySelector('.jukebox-controls-row');

          const measure = (height) => {
            container.style.height = `${height}px`;
            const containerRect = container.getBoundingClientRect();
            const contentRect = content.getBoundingClientRect();
            const controlsRect = controls.getBoundingClientRect();
            return {
              contentHeight: contentRect.height,
              controlsBottomGap: containerRect.bottom - controlsRect.bottom,
              contentClientHeight: content.clientHeight,
              contentScrollHeight: content.scrollHeight
            };
          };

          return {
            compact: measure(360),
            roomy: measure(560),
            containerOverflow: getComputedStyle(container).overflow,
            contentOverflowY: getComputedStyle(content).overflowY,
            controlsFlex: getComputedStyle(controls).flex
          };
        }
        """
    )

    assert result["containerOverflow"] == "hidden"
    assert result["contentOverflowY"] == "auto"
    assert result["controlsFlex"] == "0 0 auto"
    assert result["compact"]["contentScrollHeight"] > result["compact"]["contentClientHeight"]
    assert result["roomy"]["contentHeight"] - result["compact"]["contentHeight"] > 150
    assert abs(result["compact"]["controlsBottomGap"] - 20) <= 1
    assert abs(result["roomy"]["controlsBottomGap"] - 20) <= 1


@pytest.mark.frontend
def test_jukebox_volume_wheel_adjusts_volume_without_scrolling_container(mock_page: Page):
    setup_jukebox_page(mock_page)

    result = mock_page.evaluate(
        """
        () => {
          const J = window.Jukebox;
          const calls = [];
          J.State.player = {
            audio: { volume: 0.5 },
            volume(value) {
              this.audio.volume = value;
              calls.push(value);
            }
          };
          J.State.isMuted = false;
          J.State.savedVolume = 0.5;
          J.initVolumeSlider();

          const container = document.querySelector('.jukebox-container');
          const slider = document.getElementById('jukebox-volume-slider');
          const value = document.getElementById('jukebox-volume-value');
          let containerWheelCount = 0;
          container.addEventListener('wheel', () => {
            containerWheelCount += 1;
          });

          const upEvent = new WheelEvent('wheel', { deltaY: -120, bubbles: true, cancelable: true });
          const upDispatchResult = slider.dispatchEvent(upEvent);
          const afterUp = { slider: slider.value, value: value.textContent, volume: J.State.player.audio.volume };

          const downEvent = new WheelEvent('wheel', { deltaY: 120, bubbles: true, cancelable: true });
          const downDispatchResult = slider.dispatchEvent(downEvent);

          return {
            calls,
            afterUp,
            finalSlider: slider.value,
            finalValue: value.textContent,
            finalVolume: J.State.player.audio.volume,
            upDefaultPrevented: upEvent.defaultPrevented,
            downDefaultPrevented: downEvent.defaultPrevented,
            upDispatchResult,
            downDispatchResult,
            containerWheelCount
          };
        }
        """
    )

    assert result["calls"] == [0.55, 0.5]
    assert result["afterUp"] == {"slider": "0.55", "value": "55%", "volume": 0.55}
    assert result["finalSlider"] == "0.5"
    assert result["finalValue"] == "50%"
    assert result["finalVolume"] == 0.5
    assert result["upDefaultPrevented"] is True
    assert result["downDefaultPrevented"] is True
    assert result["upDispatchResult"] is False
    assert result["downDispatchResult"] is False
    assert result["containerWheelCount"] == 0


@pytest.mark.frontend
def test_jukebox_config_poll_fetches_full_config_only_after_revision_change(mock_page: Page):
    setup_jukebox_page(mock_page)

    result = mock_page.evaluate(
        """
        async () => {
          const J = window.Jukebox;
          const urls = [];
          const managerLoads = [];
          J.State.isOpen = true;
          J.State.configRevision = 'rev-a';
          J.loadSongs = async () => {
            urls.push('/api/jukebox/config');
            J.State.configRevision = 'rev-b';
          };
          J.SongActionManager.load = async () => {
            managerLoads.push('manager');
          };
          window.fetch = async (url) => {
            urls.push(String(url));
            return {
              ok: true,
              json: async () => ({ configRevision: 'rev-a', songCount: 2, visibleSongCount: 2 })
            };
          };

          await J.checkConfigUpdates();
          window.fetch = async (url) => {
            urls.push(String(url));
            return {
              ok: true,
              json: async () => ({ configRevision: 'rev-b', songCount: 3, visibleSongCount: 3 })
            };
          };
          await J.checkConfigUpdates();

          return { urls, revision: J.State.configRevision, managerLoads };
        }
        """
    )

    assert result == {
        "urls": [
            "/api/jukebox/config/summary",
            "/api/jukebox/config/summary",
            "/api/jukebox/config",
        ],
        "revision": "rev-b",
        "managerLoads": ["manager"],
    }


@pytest.mark.frontend
def test_jukebox_playback_mode_button_cycles_and_persists(mock_page: Page):
    setup_jukebox_page(mock_page)

    mode_button = mock_page.locator("#jukebox-mode-controls .jukebox-mode-btn")
    assert mode_button.count() == 1
    assert mode_button.get_attribute("data-mode") == "sequence"

    mode_button.click()
    assert mode_button.get_attribute("data-mode") == "single"
    assert mock_page.evaluate("window.__jukeboxLocalStore['neko.jukebox.playbackMode']") == '"single"'

    mode_button.click()
    assert mode_button.get_attribute("data-mode") == "random"
    assert mock_page.evaluate("window.__jukeboxLocalStore['neko.jukebox.playbackMode']") == '"random"'

    mode_button.click()
    assert mode_button.get_attribute("data-mode") == "none"
    assert mock_page.evaluate("window.__jukeboxLocalStore['neko.jukebox.playbackMode']") == '"none"'

    mode_button.click()
    assert mode_button.get_attribute("data-mode") == "sequence"
    assert mock_page.evaluate("window.__jukeboxLocalStore['neko.jukebox.playbackMode']") == '"sequence"'


@pytest.mark.frontend
def test_jukebox_playback_mode_tooltip_uses_current_mode(mock_page: Page):
    setup_jukebox_page(mock_page)

    mode_button = mock_page.locator("#jukebox-mode-controls .jukebox-mode-btn")
    assert mode_button.get_attribute("title") is None

    mode_button.hover()
    tooltip = mock_page.locator(".jukebox-tooltip")
    mock_page.wait_for_function(
        "() => {"
        " const el = document.querySelector('.jukebox-tooltip');"
        " return !!el && el.textContent.includes('顺序播放');"
        "}"
    )
    assert "顺序播放" in tooltip.inner_text()

    mode_button.click()
    assert mode_button.get_attribute("data-mode") == "single"
    assert mode_button.get_attribute("title") is None
    assert "单曲循环" in tooltip.inner_text()


@pytest.mark.frontend
def test_jukebox_next_song_respects_sequence_single_and_random(mock_page: Page):
    setup_jukebox_page(mock_page)

    result = mock_page.evaluate(
        """
        () => {
          const J = window.Jukebox;
          const ended = J.State.songs[0];
          const last = J.State.songs[2];

          J.State.playbackMode = 'sequence';
          const sequenceNext = J.getNextSongToPlay(ended)?.id;
          const sequenceEnd = J.getNextSongToPlay(last);

          J.State.playbackMode = 'none';
          const noneNext = J.getNextSongToPlay(ended);

          J.State.playbackMode = 'single';
          const singleNext = J.getNextSongToPlay(ended)?.id;
          const removedSingleNext = J.getNextSongToPlay({ id: 'removed-song' });

          const originalRandom = Math.random;
          Math.random = () => 0;
          J.State.playbackMode = 'random';
          const randomNext = J.getNextSongToPlay(ended)?.id;
          const randomQueue = [...J.State.randomQueue];
          const randomQueueIndex = J.State.randomQueueIndex;
          Math.random = originalRandom;

          return {
            sequenceNext,
            sequenceEnd,
            noneNext,
            singleNext,
            removedSingleNext,
            randomNext,
            randomQueue,
            randomQueueIndex
          };
        }
        """
    )

    assert result == {
        "sequenceNext": "song2",
        "sequenceEnd": None,
        "noneNext": None,
        "singleNext": "song1",
        "removedSingleNext": None,
        "randomNext": "song2",
        "randomQueue": ["song1", "song2"],
        "randomQueueIndex": 1,
    }


@pytest.mark.frontend
def test_jukebox_auto_next_skips_idle_restore_only_when_next_song_has_animation(mock_page: Page):
    setup_jukebox_page(mock_page)

    result = mock_page.evaluate(
        """
        async () => {
          const J = window.Jukebox;
          const stopArgs = [];
          const played = [];
          J.stopVMD = (skipIdleRestore) => {
            stopArgs.push(skipIdleRestore);
          };
          J.updateStoppedStatus = () => {};
          J.playSong = async (songId) => {
            played.push(songId);
          };
          J.getModelType = () => 'mmd';
          J.State.isOpen = true;
          J.State.playbackMode = 'sequence';
          J.State.songs[1].boundActions = [{ id: 'action-song2', name: 'Action', format: 'vmd' }];
          J.State.songs[1].defaultAction = 'action-song2';
          J.State.currentSong = J.State.songs[0];

          J.handleAudioEnded({ options: { loop: 'none' } });
          await new Promise((resolve) => setTimeout(resolve, 0));

          J.State.songs[1].boundActions = [];
          J.State.songs[1].defaultAction = '';
          J.State.currentSong = J.State.songs[0];
          J.handleAudioEnded({ options: { loop: 'none' } });
          await new Promise((resolve) => setTimeout(resolve, 0));

          J.State.songs[1].boundActions = [{ id: 'missing-song2', name: 'Missing', format: 'vmd', missing: true }];
          J.State.songs[1].defaultAction = 'missing-song2';
          J.State.currentSong = J.State.songs[0];
          const missingAction = J.getActionForModel(J.State.songs[1]);
          J.handleAudioEnded({ options: { loop: 'none' } });
          await new Promise((resolve) => setTimeout(resolve, 0));

          J.State.currentSong = J.State.songs[2];
          J.handleAudioEnded({ options: { loop: 'none' } });
          await new Promise((resolve) => setTimeout(resolve, 0));

          return { missingAction, stopArgs, played };
        }
        """
    )

    assert result == {
        "missingAction": None,
        "stopArgs": [True, False, False, False],
        "played": ["song2", "song2", "song2"],
    }


@pytest.mark.frontend
def test_jukebox_single_loop_removed_current_song_restores_idle(mock_page: Page):
    setup_jukebox_page(mock_page)

    result = mock_page.evaluate(
        """
        async () => {
          const J = window.Jukebox;
          const stopArgs = [];
          const played = [];
          J.stopVMD = (skipIdleRestore) => {
            stopArgs.push(skipIdleRestore);
          };
          J.updateStoppedStatus = () => {};
          J.playSong = async (songId) => {
            played.push(songId);
          };
          J.getActionForModel = () => ({ id: 'stale-action' });
          J.State.isOpen = true;
          J.State.playbackMode = 'single';
          J.State.songs = J.State.songs.filter(song => song.id !== 'song1');
          const removedSong = { id: 'song1', name: 'Removed Song' };
          J.State.currentSong = removedSong;
          const nextSong = J.getNextSongToPlay(removedSong);

          J.handleAudioEnded({ options: { loop: 'none' } });
          await new Promise((resolve) => setTimeout(resolve, 0));

          return { nextSong, stopArgs, played };
        }
        """
    )

    assert result == {
        "nextSong": None,
        "stopArgs": [False],
        "played": [],
    }


@pytest.mark.frontend
def test_jukebox_global_transport_controls_follow_sorted_playlist(mock_page: Page):
    setup_jukebox_page(mock_page)

    result = mock_page.evaluate(
        """
        () => {
          const J = window.Jukebox;
          const played = [];
          J.playSong = async (songId) => {
            played.push(songId);
            J.State.currentSong = J.State.songs.find((song) => song.id === songId) || null;
            J.State.isPlaying = true;
            J.State.isPaused = false;
            J.updateGlobalTransportControls();
          };

          J.State.songs = [J.State.songs[2], J.State.songs[0], J.State.songs[1]];
          J.renderList();
          J.State.currentSong = J.State.songs[1];
          J.State.isPlaying = true;
          J.State.isPaused = false;
          J.updateGlobalTransportControls();
          const pauseLabel = document.getElementById('jukebox-control-play-pause').getAttribute('aria-label');

          J.playAdjacentSong(-1);
          J.playAdjacentSong(1);

          J.State.isPlaying = false;
          J.State.isPaused = true;
          J.updateGlobalTransportControls();
          const resumeLabel = document.getElementById('jukebox-control-play-pause').getAttribute('aria-label');

          return { played, pauseLabel, resumeLabel };
        }
        """
    )

    assert result == {
        "played": ["song3", "song1"],
        "pauseLabel": "暂停",
        "resumeLabel": "继续",
    }


@pytest.mark.frontend
def test_jukebox_non_random_manual_previous_next_follow_sorted_playlist(mock_page: Page):
    setup_jukebox_page(mock_page)

    result = mock_page.evaluate(
        """
        () => {
          const J = window.Jukebox;
          const result = {};
          J.playSong = (songId) => {
            result.lastPlayed = songId;
            J.State.currentSong = J.State.songs.find((song) => song.id === songId) || null;
          };

          J.State.songs = [J.State.songs[2], J.State.songs[0], J.State.songs[1]];
          const song1 = J.State.songs[1];
          for (const mode of ['none', 'single', 'sequence']) {
            J.State.playbackMode = mode;
            J.State.currentSong = song1;
            J.playAdjacentSong(1);
            result[`${mode}Next`] = result.lastPlayed;

            J.State.currentSong = song1;
            J.playAdjacentSong(-1);
            result[`${mode}Previous`] = result.lastPlayed;
          }
          delete result.lastPlayed;
          return result;
        }
        """
    )

    assert result == {
        "noneNext": "song2",
        "nonePrevious": "song3",
        "singleNext": "song2",
        "singlePrevious": "song3",
        "sequenceNext": "song2",
        "sequencePrevious": "song3",
    }


@pytest.mark.frontend
def test_jukebox_random_mode_starts_queue_from_current_song(mock_page: Page):
    setup_jukebox_page(mock_page)

    result = mock_page.evaluate(
        """
        () => {
          const J = window.Jukebox;
          J.State.currentSong = J.State.songs[1];
          J.setPlaybackMode('random');
          const entered = {
            queue: [...J.State.randomQueue],
            index: J.State.randomQueueIndex,
            pendingExit: J.State.randomQueueExitSongId
          };

          J.setPlaybackMode('sequence');
          const exited = {
            queue: [...J.State.randomQueue],
            index: J.State.randomQueueIndex,
            pendingExit: J.State.randomQueueExitSongId
          };

          J.State.currentSong = J.State.songs[2];
          J.setPlaybackMode('random');
          const reentered = {
            queue: [...J.State.randomQueue],
            index: J.State.randomQueueIndex,
            pendingExit: J.State.randomQueueExitSongId
          };

          return { entered, exited, reentered };
        }
        """
    )

    assert result == {
        "entered": {"queue": ["song2"], "index": 0, "pendingExit": None},
        "exited": {"queue": [], "index": -1, "pendingExit": None},
        "reentered": {"queue": ["song3"], "index": 0, "pendingExit": None},
    }


@pytest.mark.frontend
def test_jukebox_random_exit_is_delayed_until_current_song_ends(mock_page: Page):
    setup_jukebox_page(mock_page)

    result = mock_page.evaluate(
        """
        () => {
          const J = window.Jukebox;
          J.State.playbackMode = 'random';
          J.State.currentSong = J.State.songs[1];
          J.State.isPlaying = true;
          J.State.randomQueue = ['song1', 'song2'];
          J.State.randomQueueIndex = 1;

          J.setPlaybackMode('sequence');
          const afterExit = {
            mode: J.State.playbackMode,
            queue: [...J.State.randomQueue],
            index: J.State.randomQueueIndex,
            pendingExit: J.State.randomQueueExitSongId
          };

          J.setPlaybackMode('random');
          const afterReturn = {
            mode: J.State.playbackMode,
            queue: [...J.State.randomQueue],
            index: J.State.randomQueueIndex,
            pendingExit: J.State.randomQueueExitSongId
          };

          J.setPlaybackMode('sequence');
          const nextSong = J.getNextSongToPlay(J.State.songs[1])?.id;
          const afterEndedOutsideRandom = {
            nextSong,
            queue: [...J.State.randomQueue],
            index: J.State.randomQueueIndex,
            pendingExit: J.State.randomQueueExitSongId
          };

          return { afterExit, afterReturn, afterEndedOutsideRandom };
        }
        """
    )

    assert result == {
        "afterExit": {
            "mode": "sequence",
            "queue": ["song1", "song2"],
            "index": 1,
            "pendingExit": "song2",
        },
        "afterReturn": {
            "mode": "random",
            "queue": ["song1", "song2"],
            "index": 1,
            "pendingExit": None,
        },
        "afterEndedOutsideRandom": {
            "nextSong": "song3",
            "queue": [],
            "index": -1,
            "pendingExit": None,
        },
    }


@pytest.mark.frontend
def test_jukebox_random_exit_uses_queued_anchor_without_current_song(mock_page: Page):
    setup_jukebox_page(mock_page)

    result = mock_page.evaluate(
        """
        () => {
          const J = window.Jukebox;
          J.State.playbackMode = 'random';
          J.State.currentSong = null;
          J.State.isPlaying = false;
          J.State.isPaused = false;
          J.State.randomQueue = ['song1', 'song2'];
          J.State.randomQueueIndex = 1;

          J.setPlaybackMode('sequence');
          const afterExit = {
            mode: J.State.playbackMode,
            queue: [...J.State.randomQueue],
            index: J.State.randomQueueIndex,
            pendingExit: J.State.randomQueueExitSongId
          };

          J.setPlaybackMode('random');
          const afterReturn = {
            mode: J.State.playbackMode,
            queue: [...J.State.randomQueue],
            index: J.State.randomQueueIndex,
            pendingExit: J.State.randomQueueExitSongId
          };

          J.State.currentSong = null;
          J.State.randomQueue = ['missing-song'];
          J.State.randomQueueIndex = 0;
          J.setPlaybackMode('sequence');

          return {
            afterExit,
            afterReturn,
            invalidQueuedAnchor: {
              queue: [...J.State.randomQueue],
              index: J.State.randomQueueIndex,
              pendingExit: J.State.randomQueueExitSongId
            }
          };
        }
        """
    )

    assert result == {
        "afterExit": {
            "mode": "sequence",
            "queue": ["song1", "song2"],
            "index": 1,
            "pendingExit": "song2",
        },
        "afterReturn": {
            "mode": "random",
            "queue": ["song1", "song2"],
            "index": 1,
            "pendingExit": None,
        },
        "invalidQueuedAnchor": {
            "queue": [],
            "index": -1,
            "pendingExit": None,
        },
    }


@pytest.mark.frontend
def test_jukebox_random_exit_prunes_removed_songs_while_preserving_anchor(mock_page: Page):
    setup_jukebox_page(mock_page)

    result = mock_page.evaluate(
        """
        () => {
          const J = window.Jukebox;
          J.State.playbackMode = 'sequence';
          J.State.currentSong = J.State.songs[1];
          J.State.randomQueue = ['song1', 'song2', 'song3'];
          J.State.randomQueueIndex = 1;
          J.State.randomQueueExitSongId = 'song2';

          J.State.songs = [
            { id: 'song2', name: 'Song 2', artist: 'B' },
            { id: 'song4', name: 'Song 4', artist: 'D' }
          ];
          J.syncRandomQueueWithSongs();

          return {
            queue: [...J.State.randomQueue],
            index: J.State.randomQueueIndex,
            pendingExit: J.State.randomQueueExitSongId
          };
        }
        """
    )

    assert result == {
        "queue": ["song2"],
        "index": 0,
        "pendingExit": "song2",
    }


@pytest.mark.frontend
def test_jukebox_random_exit_sync_preserves_queued_pending_anchor(mock_page: Page):
    setup_jukebox_page(mock_page)

    result = mock_page.evaluate(
        """
        () => {
          const J = window.Jukebox;
          J.State.playbackMode = 'sequence';
          J.State.currentSong = null;
          J.State.randomQueue = ['song1', 'song2'];
          J.State.randomQueueIndex = 1;
          J.State.randomQueueExitSongId = 'song2';

          J.syncRandomQueueWithSongs();

          const retainedQueuedPending = {
            queue: [...J.State.randomQueue],
            index: J.State.randomQueueIndex,
            pendingExit: J.State.randomQueueExitSongId
          };

          J.State.currentSong = null;
          J.State.randomQueue = ['song1', 'song2'];
          J.State.randomQueueIndex = 1;
          J.State.randomQueueExitSongId = 'song1';

          J.syncRandomQueueWithSongs();

          return {
            retainedQueuedPending,
            clearedMismatchedPending: {
              queue: [...J.State.randomQueue],
              index: J.State.randomQueueIndex,
              pendingExit: J.State.randomQueueExitSongId
            }
          };
        }
        """
    )

    assert result == {
        "retainedQueuedPending": {
            "queue": ["song1", "song2"],
            "index": 1,
            "pendingExit": "song2",
        },
        "clearedMismatchedPending": {
            "queue": [],
            "index": -1,
            "pendingExit": None,
        },
    }


@pytest.mark.frontend
def test_jukebox_random_exit_pending_song_start_preserves_queue(mock_page: Page):
    setup_jukebox_page(mock_page)

    result = mock_page.evaluate(
        """
        async () => {
          const J = window.Jukebox;
          J.stopAudio = () => {};
          J.stopVMD = () => {};
          J.updateStoppedStatus = () => {};
          J.updatePlayingStatus = () => {};
          J.updateCalibrationDisplay = () => {};
          J.playAudio = async () => {};
          J.getActionForModel = () => null;

          J.State.playbackMode = 'sequence';
          J.State.currentSong = null;
          J.State.randomQueue = ['song1', 'song2'];
          J.State.randomQueueIndex = 1;
          J.State.randomQueueExitSongId = 'song2';

          await J.playSong('song2');

          return {
            currentSong: J.State.currentSong && J.State.currentSong.id,
            isPlaying: J.State.isPlaying,
            queue: [...J.State.randomQueue],
            index: J.State.randomQueueIndex,
            pendingExit: J.State.randomQueueExitSongId
          };
        }
        """
    )

    assert result == {
        "currentSong": "song2",
        "isPlaying": True,
        "queue": ["song1", "song2"],
        "index": 1,
        "pendingExit": "song2",
    }


@pytest.mark.frontend
def test_jukebox_random_explicit_stop_clears_queue(mock_page: Page):
    setup_jukebox_page(mock_page)

    result = mock_page.evaluate(
        """
        () => {
          const J = window.Jukebox;
          J.stopAudio = () => {};
          J.stopVMD = () => {};
          J.updateStoppedStatus = () => {};

          J.State.playbackMode = 'random';
          J.State.currentSong = J.State.songs[1];
          J.State.isPlaying = true;
          J.State.randomQueue = ['song1', 'song2'];
          J.State.randomQueueIndex = 1;
          J.State.randomQueueExitSongId = null;

          J.stopPlayback();

          return {
            currentSong: J.State.currentSong && J.State.currentSong.id,
            isPlaying: J.State.isPlaying,
            queue: [...J.State.randomQueue],
            index: J.State.randomQueueIndex,
            pendingExit: J.State.randomQueueExitSongId
          };
        }
        """
    )

    assert result == {
        "currentSong": None,
        "isPlaying": False,
        "queue": [],
        "index": -1,
        "pendingExit": None,
    }


@pytest.mark.frontend
def test_jukebox_random_song_start_preserves_reset_queue(mock_page: Page):
    setup_jukebox_page(mock_page)

    result = mock_page.evaluate(
        """
        async () => {
          const J = window.Jukebox;
          J.stopAudio = () => {};
          J.stopVMD = () => {};
          J.updateStoppedStatus = () => {};
          J.updatePlayingStatus = () => {};
          J.updateCalibrationDisplay = () => {};
          J.playAudio = async () => {};
          J.getActionForModel = () => null;

          J.State.playbackMode = 'random';
          J.State.currentSong = J.State.songs[0];
          J.State.isPlaying = true;
          J.State.randomQueue = ['song1', 'song3'];
          J.State.randomQueueIndex = 1;

          await J.playSong('song2');

          return {
            currentSong: J.State.currentSong && J.State.currentSong.id,
            isPlaying: J.State.isPlaying,
            queue: [...J.State.randomQueue],
            index: J.State.randomQueueIndex,
            pendingExit: J.State.randomQueueExitSongId
          };
        }
        """
    )

    assert result == {
        "currentSong": "song2",
        "isPlaying": True,
        "queue": ["song2"],
        "index": 0,
        "pendingExit": None,
    }


@pytest.mark.frontend
def test_jukebox_random_sync_preserves_current_duplicate_queue_entry(mock_page: Page):
    setup_jukebox_page(mock_page)

    result = mock_page.evaluate(
        """
        () => {
          const J = window.Jukebox;
          J.State.playbackMode = 'random';
          J.State.currentSong = J.State.songs[0];
          J.State.randomQueue = ['song1', 'song2', 'song1', 'song3'];
          J.State.randomQueueIndex = 0;

          J.syncRandomQueueWithSongs();

          const afterFirstDuplicate = {
            queue: [...J.State.randomQueue],
            index: J.State.randomQueueIndex
          };

          J.State.randomQueueIndex = 2;
          J.syncRandomQueueWithSongs();

          return {
            afterFirstDuplicate,
            afterSecondDuplicate: {
              queue: [...J.State.randomQueue],
              index: J.State.randomQueueIndex
            }
          };
        }
        """
    )

    assert result == {
        "afterFirstDuplicate": {
            "queue": ["song1", "song2", "song1", "song3"],
            "index": 0,
        },
        "afterSecondDuplicate": {
            "queue": ["song1", "song2", "song1", "song3"],
            "index": 2,
        },
    }


@pytest.mark.frontend
def test_jukebox_random_sync_preserves_queued_anchor_without_current_song(mock_page: Page):
    setup_jukebox_page(mock_page)

    result = mock_page.evaluate(
        """
        () => {
          const J = window.Jukebox;
          J.State.playbackMode = 'random';
          J.State.currentSong = null;
          J.State.randomQueue = ['song1', 'song2'];
          J.State.randomQueueIndex = 1;

          J.syncRandomQueueWithSongs();

          const retainedQueuedAnchor = {
            queue: [...J.State.randomQueue],
            index: J.State.randomQueueIndex
          };

          J.State.currentSong = null;
          J.State.randomQueue = ['missing-song'];
          J.State.randomQueueIndex = 0;

          J.syncRandomQueueWithSongs();

          return {
            retainedQueuedAnchor,
            clearedMissingAnchor: {
              queue: [...J.State.randomQueue],
              index: J.State.randomQueueIndex
            }
          };
        }
        """
    )

    assert result == {
        "retainedQueuedAnchor": {
            "queue": ["song1", "song2"],
            "index": 1,
        },
        "clearedMissingAnchor": {
            "queue": [],
            "index": -1,
        },
    }


@pytest.mark.frontend
def test_jukebox_random_next_appends_only_at_queue_end(mock_page: Page):
    setup_jukebox_page(mock_page)

    result = mock_page.evaluate(
        """
        () => {
          const J = window.Jukebox;
          const played = [];
          let randomCalls = 0;
          const originalRandom = Math.random;
          Math.random = () => {
            randomCalls += 1;
            return 0;
          };
          J.playSong = (songId, options = {}) => {
            played.push({ songId, fromQueue: options.fromQueue === true });
            J.State.currentSong = J.State.songs.find((song) => song.id === songId) || null;
          };

          J.State.playbackMode = 'random';
          J.State.currentSong = J.State.songs[0];
          J.State.randomQueue = ['song1'];
          J.State.randomQueueIndex = 0;
          J.playAdjacentSong(1);
          const appended = {
            played: [...played],
            queue: [...J.State.randomQueue],
            index: J.State.randomQueueIndex,
            randomCalls
          };

          J.State.currentSong = J.State.songs[1];
          J.State.randomQueue = ['song1', 'song2', 'song3'];
          J.State.randomQueueIndex = 1;
          J.playAdjacentSong(1);
          Math.random = originalRandom;

          return {
            appended,
            finalPlayed: played,
            finalQueue: J.State.randomQueue,
            finalIndex: J.State.randomQueueIndex,
            finalRandomCalls: randomCalls
          };
        }
        """
    )

    assert result == {
        "appended": {
            "played": [{"songId": "song2", "fromQueue": True}],
            "queue": ["song1", "song2"],
            "index": 1,
            "randomCalls": 1,
        },
        "finalPlayed": [
            {"songId": "song2", "fromQueue": True},
            {"songId": "song3", "fromQueue": True},
        ],
        "finalQueue": ["song1", "song2", "song3"],
        "finalIndex": 2,
        "finalRandomCalls": 1,
    }


@pytest.mark.frontend
def test_jukebox_random_rapid_next_uses_advanced_queue_anchor(mock_page: Page):
    setup_jukebox_page(mock_page)

    result = mock_page.evaluate(
        """
        () => {
          const J = window.Jukebox;
          const played = [];
          let randomCalls = 0;
          const randomValues = [0, 0];
          const originalRandom = Math.random;
          Math.random = () => randomValues[randomCalls++] || 0;
          J.playSong = (songId, options = {}) => {
            played.push({ songId, fromQueue: options.fromQueue === true });
          };

          J.State.playbackMode = 'random';
          J.State.currentSong = J.State.songs[0];
          J.State.randomQueue = ['song1'];
          J.State.randomQueueIndex = 0;

          J.playAdjacentSong(1);
          J.playAdjacentSong(1);
          Math.random = originalRandom;

          return {
            currentSong: J.State.currentSong && J.State.currentSong.id,
            played,
            queue: [...J.State.randomQueue],
            index: J.State.randomQueueIndex,
            randomCalls
          };
        }
        """
    )

    assert result == {
        "currentSong": "song1",
        "played": [
            {"songId": "song2", "fromQueue": True},
            {"songId": "song3", "fromQueue": True},
        ],
        "queue": ["song1", "song2", "song3"],
        "index": 2,
        "randomCalls": 2,
    }


@pytest.mark.frontend
def test_jukebox_random_rapid_previous_does_not_stop_stale_current_song(mock_page: Page):
    setup_jukebox_page(mock_page)

    result = mock_page.evaluate(
        """
        () => {
          const J = window.Jukebox;
          let stopped = false;
          J.stopPlayback = () => {
            stopped = true;
            J.State.isPlaying = false;
          };

          J.State.playbackMode = 'random';
          J.State.currentSong = J.State.songs[0];
          J.State.isPlaying = true;
          J.State.isPaused = false;
          J.State.randomQueue = ['song1', 'song2'];
          J.State.randomQueueIndex = 1;

          J.playAdjacentSong(-1);

          return {
            stopped,
            currentSong: J.State.currentSong && J.State.currentSong.id,
            isPlaying: J.State.isPlaying,
            queue: [...J.State.randomQueue],
            index: J.State.randomQueueIndex
          };
        }
        """
    )

    assert result == {
        "stopped": False,
        "currentSong": "song1",
        "isPlaying": True,
        "queue": ["song1", "song2"],
        "index": 0,
    }


@pytest.mark.frontend
def test_jukebox_random_previous_uses_accumulated_queue(mock_page: Page):
    setup_jukebox_page(mock_page)

    result = mock_page.evaluate(
        """
        () => {
          const J = window.Jukebox;
          const played = [];
          J.playSong = (songId, options = {}) => {
            played.push({ songId, fromQueue: options.fromQueue === true });
            J.State.currentSong = J.State.songs.find((song) => song.id === songId) || null;
          };

          J.State.playbackMode = 'random';
          J.State.currentSong = J.State.songs[1];
          J.State.randomQueue = ['song1', 'song2', 'song3'];
          J.State.randomQueueIndex = 1;
          J.playAdjacentSong(-1);
          J.playAdjacentSong(-1);

          return {
            played,
            queue: J.State.randomQueue,
            index: J.State.randomQueueIndex
          };
        }
        """
    )

    assert result == {
        "played": [{"songId": "song1", "fromQueue": True}],
        "queue": ["song1", "song2", "song3"],
        "index": 0,
    }


@pytest.mark.frontend
def test_jukebox_random_audio_end_advances_queue_and_skips_idle_restore(mock_page: Page):
    setup_jukebox_page(mock_page)

    result = mock_page.evaluate(
        """
        async () => {
          const J = window.Jukebox;
          const stopArgs = [];
          const played = [];
          const originalRandom = Math.random;
          Math.random = () => 0;
          J.stopVMD = (skipIdleRestore) => {
            stopArgs.push(skipIdleRestore);
          };
          J.updateStoppedStatus = () => {};
          J.playSong = async (songId, options = {}) => {
            played.push({ songId, fromQueue: options.fromQueue === true });
            J.State.currentSong = J.State.songs.find((song) => song.id === songId) || null;
          };
          J.getModelType = () => 'mmd';
          J.State.isOpen = true;
          J.State.playbackMode = 'random';
          J.State.songs[1].boundActions = [{ id: 'action-song2', name: 'Action', format: 'vmd' }];
          J.State.songs[1].defaultAction = 'action-song2';
          J.State.currentSong = J.State.songs[0];
          J.State.randomQueue = ['song1'];
          J.State.randomQueueIndex = 0;

          J.handleAudioEnded({ options: { loop: 'none' } });
          await new Promise((resolve) => setTimeout(resolve, 0));
          Math.random = originalRandom;

          return {
            stopArgs,
            played,
            queue: J.State.randomQueue,
            index: J.State.randomQueueIndex
          };
        }
        """
    )

    assert result == {
        "stopArgs": [True],
        "played": [{"songId": "song2", "fromQueue": True}],
        "queue": ["song1", "song2"],
        "index": 1,
    }


@pytest.mark.frontend
def test_jukebox_audio_end_queued_next_respects_request_generation(mock_page: Page):
    setup_jukebox_page(mock_page)

    result = mock_page.evaluate(
        """
        async () => {
          const J = window.Jukebox;
          const played = [];
          J.stopVMD = () => {};
          J.updateStoppedStatus = () => {};
          J.playSong = async (songId, options = {}) => {
            played.push({ songId, requestId: options.requestId });
            J.State.currentSong = J.State.songs.find((song) => song.id === songId) || null;
          };

          J.State.isOpen = true;
          J.State.playbackMode = 'sequence';
          J.State.playRequestId = 7;
          J.State.currentSong = J.State.songs[0];

          J.handleAudioEnded({ options: { loop: 'none' } });
          J.State.playRequestId += 1;
          await new Promise((resolve) => setTimeout(resolve, 0));

          return {
            played,
            currentSong: J.State.currentSong && J.State.currentSong.id,
            playRequestId: J.State.playRequestId
          };
        }
        """
    )

    assert result == {
        "played": [],
        "currentSong": None,
        "playRequestId": 8,
    }


@pytest.mark.frontend
def test_jukebox_random_user_selected_song_resets_queue_anchor(mock_page: Page):
    setup_jukebox_page(mock_page)

    result = mock_page.evaluate(
        """
        async () => {
          const J = window.Jukebox;
          J.stopPlayback = () => {};
          J.playAudio = async () => {};
          J.getActionForModel = () => null;
          J.updatePlayingStatus = () => {};
          J.updateCalibrationDisplay = () => {};
          J.State.playbackMode = 'random';
          J.State.currentSong = J.State.songs[0];
          J.State.randomQueue = ['song1', 'song3'];
          J.State.randomQueueIndex = 1;

          await J.playSong('song2');

          return {
            currentSong: J.State.currentSong && J.State.currentSong.id,
            queue: J.State.randomQueue,
            index: J.State.randomQueueIndex
          };
        }
        """
    )

    assert result == {
        "currentSong": "song2",
        "queue": ["song2"],
        "index": 0,
    }


@pytest.mark.frontend
def test_jukebox_manual_previous_uses_last_song_without_current_song(mock_page: Page):
    setup_jukebox_page(mock_page)

    result = mock_page.evaluate(
        """
        () => {
          const J = window.Jukebox;
          J.State.currentSong = null;
          const noCurrentPrevious = J.getManualAdjacentSong(-1)?.id;
          const noCurrentNext = J.getManualAdjacentSong(1)?.id;

          J.State.currentSong = { id: 'missing-song' };
          const missingCurrentPrevious = J.getManualAdjacentSong(-1)?.id;
          const missingCurrentNext = J.getManualAdjacentSong(1)?.id;

          return {
            noCurrentPrevious,
            noCurrentNext,
            missingCurrentPrevious,
            missingCurrentNext
          };
        }
        """
    )

    assert result == {
        "noCurrentPrevious": "song3",
        "noCurrentNext": "song1",
        "missingCurrentPrevious": "song3",
        "missingCurrentNext": "song1",
    }


@pytest.mark.frontend
def test_jukebox_drag_sort_requires_unlock_button(mock_page: Page):
    setup_jukebox_page(mock_page)

    lock_button = mock_page.locator(".jukebox-sort-lock-btn")
    first_row = mock_page.locator('#jukebox-song-list tr[data-song-id="song1"]')

    assert lock_button.get_attribute("aria-pressed") == "false"
    assert first_row.evaluate("(row) => row.draggable") is False

    lock_button.click()
    assert lock_button.get_attribute("aria-pressed") == "true"
    assert first_row.evaluate("(row) => row.draggable") is True

    lock_button.click()
    assert lock_button.get_attribute("aria-pressed") == "false"
    assert first_row.evaluate("(row) => row.draggable") is False


@pytest.mark.frontend
def test_jukebox_drag_sort_order_is_rendered_and_persisted(mock_page: Page):
    setup_jukebox_page(mock_page)

    result = mock_page.evaluate(
        """
        () => {
          const J = window.Jukebox;
          const moved = J.moveSongInPlaylist('song3', 'song1', false);
          const renderedOrder = Array.from(document.querySelectorAll('#jukebox-song-list tr'))
            .map((row) => row.dataset.songId);
          const saved = JSON.parse(window.__jukeboxLocalStore['neko.jukebox.songOrder']);
          const reapplied = J.applySavedSongOrder([
            { id: 'song1' },
            { id: 'song2' },
            { id: 'song3' },
            { id: 'song4' }
          ]).map((song) => song.id);
          return { moved, renderedOrder, saved, reapplied };
        }
        """
    )

    assert result == {
        "moved": True,
        "renderedOrder": ["song3", "song1", "song2"],
        "saved": ["song3", "song1", "song2"],
        "reapplied": ["song3", "song1", "song2", "song4"],
    }
