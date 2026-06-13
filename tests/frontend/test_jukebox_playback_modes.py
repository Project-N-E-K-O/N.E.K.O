from pathlib import Path
import re

import pytest
from playwright.sync_api import Page


REPO_ROOT = Path(__file__).resolve().parents[2]
JUKEBOX_SCRIPT = (REPO_ROOT / "static" / "Jukebox.js").read_text(encoding="utf-8")
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


def test_jukebox_action_column_reserves_space_for_two_buttons():
    assert ".jukebox-table col.jukebox-col-action {\n        width: 104px;" in JUKEBOX_SCRIPT
    assert ".jukebox-table td.song-action" in JUKEBOX_SCRIPT
    assert "justify-content: center;" in JUKEBOX_SCRIPT


def test_jukebox_sequence_column_reserves_lock_space_and_centers_numbers():
    assert ".jukebox-table col.jukebox-col-sequence {\n        width: 66px;" in JUKEBOX_SCRIPT
    assert ".jukebox-sort-lock-btn {\n        width: 21px;" in JUKEBOX_SCRIPT
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


def test_jukebox_standalone_container_disables_open_close_transform_transition():
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
          Math.random = originalRandom;

          return {
            sequenceNext,
            sequenceEnd,
            noneNext,
            singleNext,
            removedSingleNext,
            randomNext
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
def test_jukebox_manual_previous_next_ignore_playback_mode(mock_page: Page):
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
          for (const mode of ['random', 'none', 'single']) {
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
        "randomNext": "song2",
        "randomPrevious": "song3",
        "noneNext": "song2",
        "nonePrevious": "song3",
        "singleNext": "song2",
        "singlePrevious": "song3",
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
