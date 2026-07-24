Object.assign(window.Jukebox, {
  startConfigPolling: function() {
    Jukebox.stopConfigPolling();
    Jukebox.State.configPollTimer = setInterval(() => {
      Jukebox.checkConfigUpdates();
    }, 10000);
  },

  stopConfigPolling: function() {
    if (Jukebox.State.configPollTimer) {
      clearInterval(Jukebox.State.configPollTimer);
      Jukebox.State.configPollTimer = null;
    }
    Jukebox.State.configPollInFlight = false;
  },

  checkConfigUpdates: async function() {
    const Jukebox = window.Jukebox || this;
    if (!Jukebox.State.isOpen || Jukebox.State.configPollInFlight) return;

    Jukebox.State.configPollInFlight = true;
    try {
      const response = await fetch('/api/jukebox/config/summary', { cache: 'no-store' });
      if (!response.ok) return;

      const summary = await response.json();
      const nextRevision = summary && summary.configRevision;
      if (!nextRevision) return;

      const currentRevision = Jukebox.State.configRevision;
      if (currentRevision && currentRevision !== nextRevision) {
        console.log('[Jukebox] 检测到歌单配置更新，重新加载歌曲');
        await Jukebox.loadSongs();
        if (Jukebox.SongActionManager && typeof Jukebox.SongActionManager.load === 'function') {
          await Jukebox.SongActionManager.load();
        }
      } else if (!currentRevision) {
        Jukebox.State.configRevision = nextRevision;
      }
    } catch (error) {
      console.warn('[Jukebox] 检查歌单更新失败:', error);
    } finally {
      Jukebox.State.configPollInFlight = false;
    }
  },

  loadSongs: async function() {
    const Jukebox = window.Jukebox || this;
    try {
      // 从后端API加载配置
      const response = await fetch('/api/jukebox/config');
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      const data = await response.json();

      // 保存完整的配置数据
      Jukebox.State.config = data;
      Jukebox.State.configRevision = data.configRevision || Jukebox.State.configRevision || null;

      // 将后端的歌曲对象转换为数组格式
      const songs = data.songs || {};
      const actions = data.actions || {};
      const bindings = data.bindings || {};

      Jukebox.State.songs = Jukebox.applySavedSongOrder(Object.entries(songs).map(([id, song]) => {
        // 获取该歌曲绑定的动画
        const songBindings = bindings[id] || {};
        const boundActions = Object.keys(songBindings)
          .filter(actionId => actions[actionId] && actions[actionId].visible !== false)
          .map(actionId => ({
            id: actionId,
            ...actions[actionId]
          })); // 过滤掉不存在或已隐藏的动画

        return {
          id: id,
          name: song.name || '未知',
          artist: song.artist || '未知',
          audio: song.audio || '',
          vmd: song.vmd || '',
          duration: song.duration || 0,
          visible: song.visible !== false, // 默认可见
          defaultAction: song.defaultAction || '',
          isBuiltin: song.isBuiltin || false, // 传递自带资源标记
          boundActions: boundActions // 绑定的动画列表
        };
      }).filter(song => song.visible)); // 只显示可见的歌曲

      console.log('[Jukebox]', window.t('Jukebox.songsLoaded', '歌曲列表已加载'), Jukebox.State.songs.length, '首歌曲');

      Jukebox.syncRandomQueueWithSongs();
      Jukebox.renderList();

    } catch (error) {
      console.error('[Jukebox]', window.t('Jukebox.loadFailed', '加载歌曲列表失败'), error);
      Jukebox.showError(window.t('Jukebox.loadFailed', '加载歌曲列表失败') + ': ' + error.message);
    }
  },

  resolveJukeboxFileUrl: function(filePath) {
    const rawPath = String(filePath || '').trim();
    if (!rawPath) return '';
    if (/^(?:https?:|data:|blob:)/i.test(rawPath)) return rawPath;
    if (/^\/?static\/jukebox\//.test(rawPath)) {
      return '/api/jukebox/file/' + rawPath.replace(/^\/?static\/jukebox\//, '');
    }
    if (rawPath.startsWith('/api/') || rawPath.startsWith('/static/') || rawPath.startsWith('/user_')) {
      return rawPath;
    }
    return '/api/jukebox/file' + '/' + rawPath.replace(/^\/+/, '');
  },

  renderList: function() {
    const tbody = document.getElementById('jukebox-song-list');
    if (!tbody) {
      console.error('[Jukebox]', window.t('Jukebox.listContainerNotFound', '歌曲列表容器不存在'));
      return;
    }

    if (Jukebox.State.songs.length === 0) {
      tbody.innerHTML = '<tr><td colspan="4" class="loading">' + window.t('Jukebox.noSongs', '暂无歌曲') + '</td></tr>';
      Jukebox.State.songElements = {};
      return;
    }

    // 增量更新：只更新变化的歌曲，不重新创建正在播放的歌曲行
    const currentIds = new Set(Jukebox.State.songs.map(s => s.id));
    const existingIds = new Set(Object.keys(Jukebox.State.songElements));

    // 删除已经不存在的歌曲行
    for (const id of existingIds) {
      if (!currentIds.has(id)) {
        const row = Jukebox.State.songElements[id];
        if (row && row.parentNode) {
          row.remove();
        }
        delete Jukebox.State.songElements[id];
      }
    }

    // 删除"加载中..."提示行（如果有的话）
    const loadingRow = tbody.querySelector('tr .loading');
    if (loadingRow) {
      const loadingTr = loadingRow.closest('tr');
      if (loadingTr) {
        loadingTr.remove();
      }
    }

    // 创建、更新并按当前排序重新排列歌曲行
    Jukebox.State.songs.forEach((song, index) => {
      const existingRow = Jukebox.State.songElements[song.id];
      let row;

      if (existingRow) {
        // 更新现有行（只更新非播放状态的内容）
        Jukebox.updateSongRow(existingRow, song, index);
        row = existingRow;
      } else {
        // 创建新行
        row = Jukebox.createSongRow(song, index);
        Jukebox.State.songElements[song.id] = row;
      }
      tbody.appendChild(row);
    });

    console.log('[Jukebox]', window.t('Jukebox.songsRendered', '歌曲列表已渲染'));
    Jukebox.updatePlaybackModeButtons();
    Jukebox.updateSongSortLockControls();
    Jukebox.bindTextTooltips(tbody);
    Jukebox.scheduleMarqueeTextUpdate(tbody);
  },

  // 创建歌曲行
  createSongRow: function(song, index) {
    const tr = document.createElement('tr');
    tr.dataset.songId = song.id;
    tr.draggable = Jukebox.isSongSortUnlocked();
    tr.innerHTML = `
      <td class="song-index"><span class="song-index-number">${index + 1}</span></td>
      <td class="song-name" data-neko-marquee data-tooltip="${Jukebox.escapeAttr(song.name)}">${Jukebox.escapeHtml(song.name)}</td>
      <td class="song-artist" data-neko-marquee data-tooltip="${Jukebox.escapeAttr(song.artist)}">${Jukebox.escapeHtml(song.artist)}</td>
      <td class="song-action">
        <button class="play-btn" data-song-id="${Jukebox.escapeAttr(song.id)}" data-tooltip="${Jukebox.escapeAttr(window.t('Jukebox.play', '播放'))}">
          <svg viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M8 5v14l11-7z"/></svg>
        </button>
      </td>
    `;

    const btn = tr.querySelector('.play-btn');
    Jukebox.setupTooltip(btn, btn.dataset.tooltip);
    btn.addEventListener('click', () => {
      Jukebox_playSong(song.id);
    });
    Jukebox.bindSongRowDragEvents(tr);

    return tr;
  },

  // 更新歌曲行（只更新基本信息，不触碰播放按钮）
  updateSongRow: function(row, song, index) {
    // 更新序号
    const indexCell = row.querySelector('.song-index');
    if (indexCell) {
      const indexNumber = indexCell.querySelector('.song-index-number');
      if (indexNumber) {
        indexNumber.textContent = index + 1;
      } else {
        indexCell.textContent = index + 1;
      }
    }

    // 更新歌名
    const nameCell = row.querySelector('.song-name');
    if (nameCell) {
      nameCell.textContent = song.name;
      nameCell.dataset.tooltip = song.name;
      nameCell.removeAttribute('title');
    }

    // 更新歌手
    const artistCell = row.querySelector('.song-artist');
    if (artistCell) {
      artistCell.textContent = song.artist;
      artistCell.dataset.tooltip = song.artist;
      artistCell.removeAttribute('title');
    }

    Jukebox.scheduleMarqueeTextUpdate(row);

    // 注意：不更新播放按钮，以保持播放状态
  },

  bindSongRowDragEvents: function(row) {
    row.addEventListener('dragstart', (event) => {
      if (!Jukebox.isSongSortUnlocked()) {
        event.preventDefault();
        return;
      }
      if (event.target && event.target.closest('button, input, a, select, textarea')) {
        event.preventDefault();
        return;
      }
      Jukebox.State.draggedSongId = row.dataset.songId;
      row.classList.add('jukebox-row-dragging');
      if (event.dataTransfer) {
        event.dataTransfer.effectAllowed = 'move';
        event.dataTransfer.setData('text/plain', row.dataset.songId);
      }
    });

    row.addEventListener('dragover', (event) => {
      if (!Jukebox.isSongSortUnlocked()) return;
      if (!Jukebox.State.draggedSongId || Jukebox.State.draggedSongId === row.dataset.songId) return;
      event.preventDefault();
      const rect = row.getBoundingClientRect();
      const placeAfter = event.clientY > rect.top + rect.height / 2;
      row.classList.toggle('jukebox-row-drop-before', !placeAfter);
      row.classList.toggle('jukebox-row-drop-after', placeAfter);
      if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
    });

    row.addEventListener('dragleave', () => {
      row.classList.remove('jukebox-row-drop-before', 'jukebox-row-drop-after');
    });

    row.addEventListener('drop', (event) => {
      if (!Jukebox.isSongSortUnlocked()) return;
      if (!Jukebox.State.draggedSongId) return;
      event.preventDefault();
      const rect = row.getBoundingClientRect();
      const placeAfter = event.clientY > rect.top + rect.height / 2;
      const moved = Jukebox.moveSongInPlaylist(Jukebox.State.draggedSongId, row.dataset.songId, placeAfter);
      if (!moved) Jukebox.clearSongDragState();
    });

    row.addEventListener('dragend', () => {
      Jukebox.clearSongDragState();
    });
  },

  getNextSongToPlay: function(endedSong) {
    const songs = Jukebox.State.songs || [];
    if (!endedSong || songs.length === 0) return null;

    if (Jukebox.State.playbackMode === 'none') {
      Jukebox.expireRandomQueueIfPendingSongEnded(endedSong.id);
      return null;
    }

    if (Jukebox.State.playbackMode === 'single') {
      Jukebox.expireRandomQueueIfPendingSongEnded(endedSong.id);
      return songs.find(song => song.id === endedSong.id) || null;
    }

    if (Jukebox.State.playbackMode === 'random') {
      Jukebox.ensureRandomQueueAnchor(endedSong.id);
      return Jukebox.getRandomAdjacentSong(1, endedSong.id);
    }

    const currentIndex = songs.findIndex(song => song.id === endedSong.id);
    Jukebox.expireRandomQueueIfPendingSongEnded(endedSong.id);
    if (currentIndex >= 0 && currentIndex < songs.length - 1) {
      return songs[currentIndex + 1];
    }
    return null;
  },

  handleAudioEnded: function(player) {
    const endedSong = Jukebox.State.currentSong;
    console.log('[Jukebox]', window.t('Jukebox.audioEnded', '音频播放结束'), {
      isPlaying: Jukebox.State.isPlaying,
      currentSong: endedSong,
      playerLoop: player && player.options ? player.options.loop : undefined,
      playbackMode: Jukebox.State.playbackMode
    });

    const nextSong = Jukebox.getNextSongToPlay(endedSong);
    const nextAction = nextSong ? Jukebox.getActionForModel(nextSong) : null;
    Jukebox.stopVMD(!!nextAction);
    Jukebox.State.isPlaying = false;
    Jukebox.State.isPaused = false;

    Jukebox.State.currentSong = null;
    Jukebox.updateStoppedStatus();

    if (nextSong) {
      setTimeout(() => {
        if (!Jukebox.State.isOpen && !window.__NEKO_JUKEBOX_STANDALONE__) return;
        Jukebox.playSong(nextSong.id, { fromQueue: Jukebox.State.playbackMode === 'random' });
      }, 0);
    }
  },

  playSong: async function(songId, options = {}) {
    const song = Jukebox.State.songs.find(s => s.id === songId);
    if (!song) {
      console.error('[Jukebox]', window.t('Jukebox.notFound', '找不到歌曲'), songId);
      return;
    }

    if (Jukebox.State.currentSong && Jukebox.State.currentSong.id === songId) {
      if (Jukebox.State.isPaused) {
        console.log('[Jukebox] 恢复暂停的歌曲:', song.name);
        Jukebox.togglePause();
        return;
      }
      if (Jukebox.State.isPlaying) {
        if (options.fromQueue === true) {
          return;
        }
        console.log('[Jukebox] 停止当前播放的歌曲:', song.name);
        Jukebox.stopPlayback();
        return;
      }
    }

    if (Jukebox.State.playbackMode === 'random') {
      if (options.fromQueue === true) {
        Jukebox.ensureRandomQueueAnchor(songId);
      } else {
        Jukebox.resetRandomQueue(songId);
      }
    } else if (Jukebox.State.randomQueueExitSongId && Jukebox.State.randomQueueExitSongId !== songId) {
      Jukebox.clearRandomQueue();
    }

    console.log('[Jukebox] 播放歌曲:', song.name);

    const preserveRandomQueue = Jukebox.State.playbackMode === 'random'
      || (
        Jukebox.State.randomQueueExitSongId
        && Jukebox.State.randomQueueExitSongId === songId
      );
    Jukebox.stopPlayback({ preserveRandomQueue });

    const requestId = ++Jukebox.State.playRequestId;

    try {
      await Jukebox.playAudio(song);

      if (requestId !== Jukebox.State.playRequestId) {
        console.log('[Jukebox] 播放请求已被新请求取代，取消状态更新');
        return;
      }

      // 根据模型类型播放对应格式的动画
      const action = Jukebox.getActionForModel(song);
      if (action) {
        const actionUrl = Jukebox.resolveJukeboxFileUrl(action.file || '');
        console.log('[Jukebox] 播放动画:', action.name, '格式:', action.format || 'vmd', '路径:', actionUrl);

        const modelType = Jukebox.getModelType();
        if (modelType === 'mmd' || modelType === 'live3d') {
          await Jukebox.playVMD(actionUrl);
        } else if (modelType === 'vrm') {
          await Jukebox.playVRMA(actionUrl);
        } else if (modelType === 'fbx') {
          await Jukebox.playFBX(actionUrl);
        }
      }

      if (requestId !== Jukebox.State.playRequestId) {
        console.log('[Jukebox] 播放请求已被新请求取代，取消状态更新');
        return;
      }

      Jukebox.State.currentSong = song;
      Jukebox.State.isPlaying = true;

      Jukebox.updatePlayingStatus(song);
      Jukebox.updateCalibrationDisplay();
    } catch (error) {
      if (requestId !== Jukebox.State.playRequestId) {
        return;
      }
      console.error('[Jukebox]', window.t('Jukebox.playFailed', '播放失败'), error);
      Jukebox.showError(window.t('Jukebox.playFailed', '播放失败') + ': ' + error.message);
    }
  },

  playAudio: async function(song) {
    const player = Jukebox.getPlayer();
    if (!player) {
      console.error('[Jukebox]', window.t('Jukebox.playError', '音乐播放器未初始化'));
      throw new Error(window.t('Jukebox.playError', '音乐播放器未初始化'));
    }

    player.list.clear();

    console.log('[Jukebox]', window.t('Jukebox.useAPlayer', '使用APlayer播放音频文件'));

    const audioUrl = Jukebox.resolveJukeboxFileUrl(song.audio);

    player.list.add([{
      name: song.name,
      artist: song.artist,
      url: audioUrl,
      cover: ''
    }]);

    player.options.loop = 'none';

    if (Jukebox.State.boundPlayer !== player) {
      player.on('ended', () => {
        Jukebox.handleAudioEnded(player);
      });
      Jukebox.State.boundPlayer = player;
    }

    player.play();

    console.log('[Jukebox]', window.t('Jukebox.startPlay', '开始播放音频'), song.audio);
  },

  playVMD: async function(vmdPath) {
    // 独立窗口模式：通过 IPC 桥接到 Pet 窗口执行
    if (window.__NEKO_JUKEBOX_STANDALONE__ && window.nekoJukeboxBridge) {
      window.nekoJukeboxBridge.playVMD(vmdPath);
      Jukebox.State.isVMDPlaying = true;
      console.log('[Jukebox]', window.t('Jukebox.vmdPlayed', 'VMD 动画已播放'), '(IPC)', vmdPath);
      return;
    }

    if (!window.mmdManager || !window.mmdManager.animationModule) {
      console.warn('[Jukebox]', window.t('Jukebox.vmdNotInit', 'MMD Manager 未初始化，跳过动画'));
      return;
    }

    try {
      // 保存当前待机动画 URL（用于停止后恢复）
      // 只在未保存过待机动画 URL 时保存，避免被舞蹈 VMD 覆盖
      if (!Jukebox.State.savedIdleAnimationUrl && window.mmdManager.currentAnimationUrl) {
        Jukebox.State.savedIdleAnimationUrl = window.mmdManager.currentAnimationUrl;
      }

      Jukebox.stopVMD(true); // skipIdleRestore = true

      await window.mmdManager.loadAnimation(vmdPath);
      window.mmdManager.playAnimation('dance');

      Jukebox.State.isVMDPlaying = true;

      console.log('[Jukebox]', window.t('Jukebox.vmdPlayed', 'VMD 动画已播放'), vmdPath);
    } catch (error) {
      console.error('[Jukebox]', window.t('Jukebox.vmdPlayFailed', 'VMD 播放失败'), error);
    }
  },

  // 播放 VRMA 动画（VRM 模型）
  playVRMA: async function(vrmaPath) {
    // 独立窗口模式：复用 VMD 桥接通道发送到 Pet（Pet 侧根据模型类型分发）
    if (window.__NEKO_JUKEBOX_STANDALONE__ && window.nekoJukeboxBridge) {
      window.nekoJukeboxBridge.playVMD(vrmaPath);
      Jukebox.State.isVMDPlaying = true;
      console.log('[Jukebox] VRMA 动画已发送 (IPC):', vrmaPath);
      return;
    }
    if (!window.vrmManager) {
      console.warn('[Jukebox] VRM Manager 未初始化，跳过动画');
      return;
    }

    try {
      console.log('[Jukebox] 播放 VRMA 动画:', vrmaPath);

      Jukebox.stopVMD(true); // 停止之前的舞蹈动画

      // 使用 VRMManager 播放 VRMA（manager 内部会确保 animation 模块已初始化）
      await window.vrmManager.playVRMAAnimation(vrmaPath, {
        loop: false,
        fadeInDuration: 0.5,
        fadeOutDuration: 0.5
      });
      Jukebox.State.isVMDPlaying = true;
      console.log('[Jukebox] VRMA 动画已播放:', vrmaPath);
    } catch (error) {
      console.error('[Jukebox] VRMA 播放失败:', error);
    }
  },

  // 播放 FBX 动画（FBX 模型）
  playFBX: async function(fbxPath) {
    if (!window.fbxManager) {
      console.warn('[Jukebox] FBX Manager 未初始化，跳过动画');
      return;
    }

    try {
      console.log('[Jukebox] 播放 FBX 动画:', fbxPath);
      // TODO: 实现 FBX 模型的动画播放
      // 这里需要根据 FBXManager 的实际 API 来实现
      // await window.fbxManager.loadAnimation(fbxPath);
      // window.fbxManager.playAnimation();
      console.warn('[Jukebox] FBX 动画播放尚未实现');
    } catch (error) {
      console.error('[Jukebox] FBX 播放失败:', error);
    }
  },

  updateVolume: function(value) {
    const volume = parseFloat(value);
    const player = Jukebox.getPlayer();

    if (player) {
      player.volume(volume);
    }

    if (volume > 0 && Jukebox.State.isMuted) {
      Jukebox.State.isMuted = false;
      Jukebox.State.savedVolume = volume;
    }

    Jukebox.updateVolumeDisplay(volume);
  },

  logVolumeChange: function(value) {
    const volume = parseFloat(value);
    console.log('[Jukebox]', window.t('Jukebox.volumeSet', '音量已设置为'), volume, '(' + Math.round(volume * 100) + '%)');
  },

  initVolumeSlider: function() {
    const player = Jukebox.getPlayer();
    const volumeSlider = document.getElementById('jukebox-volume-slider');

    if (player && volumeSlider) {
      volumeSlider.value = player.audio.volume;
      const volumeValue = document.getElementById('jukebox-volume-value');
      if (volumeValue) {
        volumeValue.textContent = Math.round(player.audio.volume * 100) + '%';
      }
      console.log('[Jukebox] 音量滑条已初始化，当前音量:', player.audio.volume);
    }

    const speakerBtn = document.getElementById('jukebox-speaker-btn');
    if (speakerBtn) {
      speakerBtn.addEventListener('click', Jukebox.toggleMute);
    }

    const volumeValueEl = document.getElementById('jukebox-volume-value');
    if (volumeValueEl) {
      volumeValueEl.addEventListener('click', Jukebox.startVolumeEdit);
    }

    Jukebox.bindVolumeWheel();
  },

  bindVolumeWheel: function() {
    const volumeWrapper = document.querySelector('.jukebox-volume-wrapper');
    if (!volumeWrapper || volumeWrapper.dataset.wheelBound === 'true') return;

    volumeWrapper.dataset.wheelBound = 'true';
    volumeWrapper.addEventListener('wheel', Jukebox.handleVolumeWheel, { passive: false });
  },

  handleVolumeWheel: function(e) {
    if (!e) return;

    e.preventDefault();
    e.stopPropagation();

    if (e.deltaY === 0) return;

    const volumeSlider = document.getElementById('jukebox-volume-slider');
    const player = Jukebox.getPlayer();
    const sliderVolume = volumeSlider ? parseFloat(volumeSlider.value) : NaN;
    const playerVolume = player && player.audio ? parseFloat(player.audio.volume) : NaN;
    const fallbackVolume = Jukebox.State.isMuted ? 0 : (Jukebox.State.savedVolume || 1);
    const currentVolume = Number.isFinite(sliderVolume)
      ? sliderVolume
      : (Number.isFinite(playerVolume) ? playerVolume : fallbackVolume);
    const wheelStep = 0.05;
    const nextVolume = Math.max(0, Math.min(1, Math.round((currentVolume + (e.deltaY < 0 ? wheelStep : -wheelStep)) * 100) / 100));

    if (volumeSlider) {
      volumeSlider.value = nextVolume;
    }

    Jukebox.updateVolume(nextVolume);
  },

  startVolumeEdit: function() {
    const volumeValueEl = document.getElementById('jukebox-volume-value');
    if (!volumeValueEl || volumeValueEl.dataset.editing === 'true') return;

    const currentVolume = Math.round((Jukebox.State.isMuted ? Jukebox.State.savedVolume : (Jukebox.getPlayer()?.audio?.volume || 1)) * 100);

    volumeValueEl.dataset.editing = 'true';
    volumeValueEl.innerHTML = `<input type="text" class="jukebox-volume-input" value="${currentVolume}" maxlength="3">`;

    const input = volumeValueEl.querySelector('.jukebox-volume-input');
    if (input) {
      input.focus();
      input.select();

      input.addEventListener('keydown', Jukebox.handleVolumeInputKeydown);
      input.addEventListener('blur', Jukebox.confirmVolumeEdit);
      input.addEventListener('input', Jukebox.filterVolumeInput);
    }
  },

  filterVolumeInput: function(e) {
    const input = e.target;
    input.value = input.value.replace(/[^0-9]/g, '');
  },

  handleVolumeInputKeydown: function(e) {
    if (e.key === 'Enter') {
      e.preventDefault();
      e.target.blur();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      Jukebox.cancelVolumeEdit();
    }
  },

  confirmVolumeEdit: function(e) {
    const volumeValueEl = document.getElementById('jukebox-volume-value');
    if (!volumeValueEl || volumeValueEl.dataset.editing !== 'true') return;

    const input = e.target;
    const inputValue = input.value.trim();

    if (inputValue === '') {
      Jukebox.cancelVolumeEdit();
      return;
    }

    let newVolume = parseInt(inputValue, 10);
    if (isNaN(newVolume)) {
      Jukebox.cancelVolumeEdit();
      return;
    }

    newVolume = Math.max(0, Math.min(100, newVolume));
    const normalizedVolume = newVolume / 100;

    const player = Jukebox.getPlayer();
    if (player) {
      player.volume(normalizedVolume);
    }

    const volumeSlider = document.getElementById('jukebox-volume-slider');
    if (volumeSlider) {
      volumeSlider.value = normalizedVolume;
    }

    if (normalizedVolume > 0 && Jukebox.State.isMuted) {
      Jukebox.State.isMuted = false;
      Jukebox.State.savedVolume = normalizedVolume;
    }

    volumeValueEl.dataset.editing = 'false';
    volumeValueEl.textContent = newVolume + '%';
    Jukebox.updateSpeakerIcon(normalizedVolume === 0);
  },

  cancelVolumeEdit: function() {
    const volumeValueEl = document.getElementById('jukebox-volume-value');
    if (!volumeValueEl) return;

    const currentVolume = Math.round((Jukebox.State.isMuted ? Jukebox.State.savedVolume : (Jukebox.getPlayer()?.audio?.volume || 1)) * 100);
    volumeValueEl.dataset.editing = 'false';
    volumeValueEl.textContent = currentVolume + '%';
  },

  toggleMute: function() {
    const player = Jukebox.getPlayer();
    const volumeSlider = document.getElementById('jukebox-volume-slider');

    if (Jukebox.State.isMuted) {
      Jukebox.State.isMuted = false;
      if (player && player.audio) {
        player.audio.volume = Jukebox.State.savedVolume;
      }
      if (volumeSlider) {
        volumeSlider.value = Jukebox.State.savedVolume;
      }
      Jukebox.updateVolumeDisplay(Jukebox.State.savedVolume);
      Jukebox.updateSpeakerIcon(false);
    } else {
      Jukebox.State.savedVolume = player && player.audio ? player.audio.volume : 1;
      Jukebox.State.isMuted = true;
      if (player && player.audio) {
        player.audio.volume = 0;
      }
      if (volumeSlider) {
        volumeSlider.value = 0;
      }
      Jukebox.updateVolumeDisplay(0);
      Jukebox.updateSpeakerIcon(true);
    }
  },

  updateSpeakerIcon: function(isMuted) {
    const speakerIcon = document.querySelector('.speaker-icon');
    const mutedIcon = document.querySelector('.speaker-muted-icon');
    if (speakerIcon && mutedIcon) {
      speakerIcon.style.display = isMuted ? 'none' : 'block';
      mutedIcon.style.display = isMuted ? 'block' : 'none';
    }
  },

  updateVolumeDisplay: function(volume) {
    const volumeValue = document.getElementById('jukebox-volume-value');
    if (volumeValue && volumeValue.dataset.editing !== 'true') {
      volumeValue.textContent = Math.round(volume * 100) + '%';
    }
    Jukebox.updateSpeakerIcon(volume === 0);
  },

  stopPlayback: function(options = {}) {
    const preserveRandomQueue = options.preserveRandomQueue === true;
    Jukebox.stopAudio();
    Jukebox.stopVMD();

    Jukebox.State.currentSong = null;
    Jukebox.State.isPlaying = false;
    Jukebox.State.isPaused = false;
    Jukebox.State.isVMDPlaying = false;
    if (!preserveRandomQueue) {
      Jukebox.clearRandomQueue();
    }

    Jukebox.updateStoppedStatus();
  },

  stopAudio: function() {
    if (Jukebox.State.audioElement) {
      Jukebox.State.audioElement.pause();
      Jukebox.State.audioElement.currentTime = 0;
      Jukebox.State.audioElement = null;
    }

    const player = Jukebox.getPlayer();
    if (player && Jukebox.State.isPlaying) {
      player.pause();
      player.seek(0);
    }
  },

  stopVMD: function(skipIdleRestore) {
    // 独立窗口模式：通过 IPC 桥接到 Pet 窗口执行
    if (window.__NEKO_JUKEBOX_STANDALONE__ && window.nekoJukeboxBridge) {
      if (Jukebox.State.isVMDPlaying) {
        window.nekoJukeboxBridge.stopVMD(skipIdleRestore);
        Jukebox.State.isVMDPlaying = false;
        Jukebox.State.isPaused = false;
      }
      return;
    }

    // 没有在播放舞蹈动画时，不要停止当前动画（可能是 idle 待机）
    if (!Jukebox.State.isVMDPlaying) return;

    // 根据模型类型停止对应的动画模块
    var modelType = Jukebox.getModelType();
    if (modelType === 'vrm') {
      if (window.vrmManager) window.vrmManager.stopVRMAAnimation();
    } else {
      if (window.mmdManager?.animationModule) {
        // 直接停止动画模块，不通过 stopAnimation()
        // 避免在 idle 加载完成前改变 cursor follow 状态
        window.mmdManager.animationModule.stop();
      }
    }

    Jukebox.State.isVMDPlaying = false;
    Jukebox.State.isPaused = false;

    if (!skipIdleRestore) {
      Jukebox.restoreIdleAnimation();
    }
  },

  _resetToNoneMode: function() {
    if (window.__NEKO_JUKEBOX_STANDALONE__) return;
    const mesh = window.mmdManager.currentModel?.mesh;
    if (mesh?.skeleton) {
      mesh.skeleton.pose();
    }
    if (window.mmdManager.cursorFollow) {
      window.mmdManager.cursorFollow.setAnimationMode('none');
    }
  },

  restoreIdleAnimation: async function() {
    const Jukebox = window.Jukebox || this;
    // 独立窗口模式：Pet 侧在 stopVMD 时自动恢复，此处无需操作
    if (window.__NEKO_JUKEBOX_STANDALONE__) return;

    // VRM 模式：恢复 VRM 待机动画
    var modelType = Jukebox.getModelType();
    if (modelType === 'vrm' && window.vrmManager) {
      try {
        var vrmIdleList = window.lanlan_config?.vrmIdleAnimations;
        var vrmIdleUrl = (Array.isArray(vrmIdleList) && vrmIdleList.length > 0) ? vrmIdleList[0] : null;
        if (!vrmIdleUrl) {
          vrmIdleUrl = window.lanlan_config?.vrmIdleAnimation || '/static/vrm/animation/wait03.vrma';
        }
        await window.vrmManager.playVRMAAnimation(vrmIdleUrl, {
          loop: true,
          isIdle: true
        });
        console.log('[Jukebox] VRM 待机动画已恢复');
      } catch (error) {
        console.warn('[Jukebox] VRM 待机动画恢复失败:', error);
      }
      return;
    }

    if (!window.mmdManager) return;

    const restoreRequestId = Jukebox.State.playRequestId;

    let idleUrl = Jukebox.State.savedIdleAnimationUrl;

    // 如果保存的是点歌台舞蹈 VMD（不是真正的待机动画），则忽略
    if (idleUrl && idleUrl.includes('/jukebox/song_')) {
      idleUrl = null;
    }

    // 如果没有保存的待机动画 URL，从角色配置获取
    if (!idleUrl) {
      try {
        const catgirlName = window.lanlan_config?.catgirl_name;
        if (catgirlName) {
          const charRes = await fetch('/api/characters');
          if (charRes.ok) {
            const charData = await charRes.json();
            idleUrl = charData?.['猫娘']?.[catgirlName]?.mmd_idle_animation;
          }
        }
      } catch (_) { /* ignore */ }
    }

    if (restoreRequestId !== Jukebox.State.playRequestId) return;

    if (!idleUrl) {
      Jukebox._resetToNoneMode();
      return;
    }

    try {
      await window.mmdManager.loadAnimation(idleUrl);
      if (restoreRequestId !== Jukebox.State.playRequestId) return;
      window.mmdManager.playAnimation('idle');
      console.log('[Jukebox]', window.t('Jukebox.idleRestored', '已恢复待机动画'));
    } catch (error) {
      console.warn('[Jukebox]', window.t('Jukebox.idleRestoreFailed', '恢复待机动画失败'), error);
      if (restoreRequestId !== Jukebox.State.playRequestId) return;
      Jukebox._resetToNoneMode();
    }
  },

  togglePause: function() {
    // Pet 窗口通过 IPC 调用时 currentSong 为 null，用 isVMDPlaying 兜底
    if (!Jukebox.State.currentSong && !Jukebox.State.isVMDPlaying) return;

    const player = Jukebox.getPlayer();
    var isStandalone = window.__NEKO_JUKEBOX_STANDALONE__ && window.nekoJukeboxBridge;
    var modelType = Jukebox.getModelType();

    if (Jukebox.State.isPaused) {
      // 恢复播放
      if (player) player.play();
      if (isStandalone) {
        window.nekoJukeboxBridge.resumeVMD();
      } else if (modelType === 'vrm') {
        var vrmAnim = window.vrmManager?.animationModule || window.vrmManager?.animation;
        if (vrmAnim?.currentAction) vrmAnim.currentAction.paused = false;
      } else if (window.mmdManager?.animationModule) {
        // 直接恢复动画模块（不通过 playAnimation 避免重置动画进度）
        window.mmdManager.animationModule.play();
        if (window.mmdManager.cursorFollow) {
          window.mmdManager.cursorFollow.setAnimationMode('dance');
        }
      }
      Jukebox.State.isPaused = false;
      Jukebox.State.isPlaying = true;
      if (Jukebox.State.currentSong) Jukebox.updatePlayingStatus(Jukebox.State.currentSong);
      console.log('[Jukebox]', window.t('Jukebox.resumed', '已恢复播放'));
    } else if (Jukebox.State.isPlaying || Jukebox.State.isVMDPlaying) {
      // 暂停
      if (player) player.pause();
      if (isStandalone) {
        window.nekoJukeboxBridge.pauseVMD();
      } else if (modelType === 'vrm') {
        var vrmAnim = window.vrmManager?.animationModule || window.vrmManager?.animation;
        if (vrmAnim?.currentAction) vrmAnim.currentAction.paused = true;
      } else if (window.mmdManager?.animationModule) {
        window.mmdManager.animationModule.pause();
        // 暂停时提升跟踪权重，让视线追踪更明显
        if (window.mmdManager.cursorFollow) {
          window.mmdManager.cursorFollow.setAnimationMode('idle');
        }
      }
      Jukebox.State.isPaused = true;
      Jukebox.State.isPlaying = false;
      if (Jukebox.State.currentSong) Jukebox.updatePausedStatus(Jukebox.State.currentSong);
      console.log('[Jukebox]', window.t('Jukebox.paused', '已暂停'));
    }
  },

  // ═══════════════════ 进度条 ═══════════════════

  startProgressUpdate: function() {
    Jukebox.stopProgressUpdate();

    const slider = document.getElementById('jukebox-progress-slider');
    if (slider) {
      // 始终允许拖动进度条
      slider.classList.add('seekable');
      // 绑定 seek 事件
      if (!slider._jukeboxBound) {
        slider.addEventListener('input', Jukebox._onProgressInput);
        slider.addEventListener('change', Jukebox._onProgressChange);
        slider._jukeboxBound = true;
      }
    }

    Jukebox.State.progressTimer = setInterval(() => {
      if (!Jukebox.State.isSeeking) {
        Jukebox._updateProgressDisplay();
      }
    }, 250);
  },

  stopProgressUpdate: function() {
    if (Jukebox.State.progressTimer) {
      clearInterval(Jukebox.State.progressTimer);
      Jukebox.State.progressTimer = null;
    }
  },

  _updateProgressDisplay: function() {
    const player = Jukebox.getPlayer();
    if (!player || !player.audio) return;

    const currentTime = player.audio.currentTime || 0;
    const duration = player.audio.duration || 0;

    const slider = document.getElementById('jukebox-progress-slider');
    const timeCurrent = document.getElementById('jukebox-time-current');
    const timeTotal = document.getElementById('jukebox-time-total');

    if (slider && duration > 0) {
      slider.value = (currentTime / duration) * 100;
    }
    if (timeCurrent) timeCurrent.textContent = Jukebox.formatDuration(Math.floor(currentTime));
    if (timeTotal) timeTotal.textContent = Jukebox.formatDuration(Math.floor(duration));
  },

  _onProgressInput: function() {
    Jukebox.State.isSeeking = true;
    // 拖动时只更新显示，不实际跳转
    Jukebox._updateProgressDisplayFromSlider();
  },

  getAnimationTimeForMusicTime: function(musicTime, offset) {
    const song = Jukebox.State.currentSong;
    const action = song ? Jukebox.getActionForModel(song) : null;
    const fps = Jukebox.getAnimationFps(action);
    const frameOffset = Number.isFinite(Number(offset)) ? Number(offset) : Jukebox.getCurrentOffset();
    const animFrame = (Number(musicTime) || 0) * fps + frameOffset;
    return Math.max(0, animFrame / fps);
  },

  _seekMmdAnimationToTime: function(animTime, requireClip) {
    const anim = window.mmdManager?.animationModule;
    if (!anim || !anim.mixer || (requireClip && !anim.currentClip)) return false;

    anim.mixer.setTime(animTime);
    const mesh = window.mmdManager.currentModel?.mesh;
    if (typeof anim._restoreBones === 'function') anim._restoreBones(mesh);
    if (anim.mixer.update) anim.mixer.update(0);
    if (typeof anim._saveBones === 'function') anim._saveBones(mesh);
    if (mesh) mesh.updateMatrixWorld(true);
    if (anim.ikSolver) anim.ikSolver.update();
    if (anim.grantSolver) anim.grantSolver.update();
    return true;
  },

  _seekVrmAnimationToTime: function(animTime) {
    const manager = window.vrmManager;
    const seekOptions = { paused: Jukebox.State.isPaused === true };
    if (manager && typeof manager.seekVRMAAnimation === 'function') {
      return manager.seekVRMAAnimation(animTime, seekOptions);
    }
    const anim = manager?.animationModule || manager?.animation;
    if (anim && typeof anim.seekTo === 'function') {
      return anim.seekTo(animTime, seekOptions);
    }
    console.warn('[Jukebox] VRM动画同步入口不可用，跳过 seek:', animTime);
    return false;
  },

  syncCurrentAnimationToTime: function(animTime, options = {}) {
    if (window.__NEKO_JUKEBOX_STANDALONE__) return false;

    const modelType = Jukebox.getModelType();
    if (modelType === 'mmd' || modelType === 'live3d') {
      return Jukebox._seekMmdAnimationToTime(animTime, options.requireClipForMmd === true);
    }
    if (modelType === 'vrm') {
      return Jukebox._seekVrmAnimationToTime(animTime);
    }
    if (modelType === 'fbx') {
      console.log('[Jukebox] FBX动画同步:', animTime);
    }
    return false;
  },

  _onProgressChange: function() {
    const slider = document.getElementById('jukebox-progress-slider');
    if (!slider) {
      Jukebox.State.isSeeking = false;
      return;
    }

    const player = Jukebox.getPlayer();
    if (!player || !player.audio) {
      Jukebox.State.isSeeking = false;
      return;
    }

    const duration = player.audio.duration || 0;
    const seekTime = (parseFloat(slider.value) / 100) * duration;

    // 同步音频
    player.seek(seekTime);

    // 同步动画（考虑 offset）—— 独立窗口无法直接操作动画模块
    Jukebox.syncCurrentAnimationToTime(
      Jukebox.getAnimationTimeForMusicTime(seekTime),
      { requireClipForMmd: true }
    );

    Jukebox.State.isSeeking = false;
    Jukebox._updateProgressDisplay();
  },

  // 根据滑块值更新显示（不实际跳转）
  _updateProgressDisplayFromSlider: function() {
    const slider = document.getElementById('jukebox-progress-slider');
    const timeCurrent = document.getElementById('jukebox-time-current');
    if (!slider || !timeCurrent) return;

    const player = Jukebox.getPlayer();
    if (!player || !player.audio) return;

    const duration = player.audio.duration || 0;
    const previewTime = (parseFloat(slider.value) / 100) * duration;
    timeCurrent.textContent = Jukebox.formatDuration(Math.floor(previewTime));
  },

  _setProgressSeekable: function(seekable) {
    const slider = document.getElementById('jukebox-progress-slider');
    if (slider) {
      if (seekable) {
        slider.classList.add('seekable');
      } else {
        slider.classList.remove('seekable');
      }
    }
  },

  getPlayer: function() {
    if (window.music_ui && window.music_ui.getMusicPlayerInstance) {
      const sharedPlayer = window.music_ui.getMusicPlayerInstance();
      if (sharedPlayer) {
        return sharedPlayer;
      }
    }

    return Jukebox.State.player;
  },

  initPlayer: function() {
    const Jukebox = window.Jukebox || this;
    if (window.music_ui && window.music_ui.getMusicPlayerInstance) {
      const existingPlayer = window.music_ui.getMusicPlayerInstance();
      if (existingPlayer) {
        console.log('[Jukebox] 使用现有的音乐播放器');
        return;
      }
      console.log('[Jukebox] music_ui 存在但播放器未初始化，创建新播放器');
    }

    if (!Jukebox.State.container) {
      console.warn('[Jukebox] 容器不存在，取消播放器初始化');
      return;
    }

    console.log('[Jukebox] 创建新的音乐播放器');

    if (typeof APlayer === 'undefined') {
      console.warn('[Jukebox] APlayer 未加载，等待加载...');
      setTimeout(() => Jukebox.initPlayer(), 500);
      return;
    }

    const playerContainer = document.createElement('div');
    playerContainer.id = 'jukebox-player';
    playerContainer.style.display = 'none';
    Jukebox.State.container.appendChild(playerContainer);

    Jukebox.State.player = new APlayer({
      container: playerContainer,
      autoplay: false,
      theme: Jukebox.Config.container.background,
      preload: 'auto',
      listFolded: true,
      volume: 1,
      audio: []
    });

    console.log('[Jukebox] APlayer已创建，音量:', Jukebox.State.player.audio.volume);
  },

  // 获取当前模型类型（拆分 live3d 子类型，返回 'mmd' / 'vrm' / 'live2d'）
  getModelType: function() {
    var mt = window.lanlan_config?.model_type || 'live2d';
    if (mt === 'live3d') {
      var sub = (window.lanlan_config?.live3d_sub_type || '').toLowerCase();
      if (sub === 'vrm') return 'vrm';
      return 'mmd'; // live3d 默认走 MMD
    }
    return mt;
  },

  // 检查当前模型是否支持动画
  isAnimationSupported: function() {
    const modelType = Jukebox.getModelType();
    return ['mmd', 'live3d', 'vrm', 'fbx'].includes(modelType);
  },

  // 显示/隐藏校准区域
  updateCalibrationVisibility: function() {
    const section = document.getElementById('jukebox-calibration-section');
    if (section) {
      section.style.display = Jukebox.isAnimationSupported() ? 'block' : 'none';
    }
  },

  // 切换校准面板显示
  toggleCalibrationPanel: function() {
    const panel = document.getElementById('jukebox-calibration-panel');
    if (panel) {
      const isVisible = panel.style.display !== 'none';
      panel.style.display = isVisible ? 'none' : 'block';
    }
  },

  // 获取当前歌曲和动画的offset
  getCurrentOffset: function() {
    const song = Jukebox.State.currentSong;
    if (!song) return 0;

    const action = Jukebox.getActionForModel(song);
    if (!action) return 0;

    // 当前会话编辑过的值优先；普通播放路径未打开管理器时回退到 loadSongs 已加载的配置。
    const managerBinding = Jukebox.SongActionManager.data.bindings?.[song.id]?.[action.id];
    const configBinding = Jukebox.State.config?.bindings?.[song.id]?.[action.id];
    const offset = managerBinding?.offset ?? configBinding?.offset ?? 0;
    return Number.isFinite(Number(offset)) ? Number(offset) : 0;
  },

  // 更新校准显示值
  updateCalibrationDisplay: function() {
    const valueEl = document.getElementById('jukebox-calibration-value');
    const fpsEl = document.getElementById('jukebox-calibration-fps');

    if (valueEl) {
      const offset = Jukebox.getCurrentOffset();
      valueEl.textContent = offset + window.t('Jukebox.frames', '帧');
    }

    if (fpsEl) {
      const song = Jukebox.State.currentSong;
      const action = song ? Jukebox.getActionForModel(song) : null;
      const fps = Jukebox.getAnimationFps(action);
      fpsEl.textContent = '(' + fps + ' FPS)';
    }
  },

  // 调整offset
  adjustOffset: async function(delta) {
    const song = Jukebox.State.currentSong;
    if (!song) {
      Jukebox.showError(window.t('Jukebox.noSongPlaying', '没有正在播放的歌曲'));
      return;
    }

    const action = Jukebox.getActionForModel(song);
    if (!action) {
      Jukebox.showError(window.t('Jukebox.noActionBound', '当前歌曲没有绑定动画'));
      return;
    }

    const currentOffset = Jukebox.getCurrentOffset();
    const newOffset = currentOffset + delta;

    try {
      // 保存到后端
      await Jukebox.SongActionManager.api.updateOffset(song.id, action.id, newOffset);

      // 更新本地状态 (保存到 SongActionManager.data)
      if (!Jukebox.SongActionManager.data.bindings[song.id]) {
        Jukebox.SongActionManager.data.bindings[song.id] = {};
      }
      Jukebox.SongActionManager.data.bindings[song.id][action.id] = { offset: newOffset };

      // 更新显示
      Jukebox.updateCalibrationDisplay();

      // 如果正在播放，实时调整动画
      if (Jukebox.State.isPlaying && !Jukebox.State.isPaused) {
        Jukebox.syncAnimationToOffset(newOffset);
      }

      console.log('[Jukebox] Offset已调整:', currentOffset, '->', newOffset);
    } catch (error) {
      console.error('[Jukebox] 调整offset失败:', error);
      Jukebox.showError(window.t('Jukebox.adjustOffsetFailed', '调整偏移失败'));
    }
  },

  // 重置offset
  resetOffset: async function() {
    await Jukebox.adjustOffset(-Jukebox.getCurrentOffset());
  },

  // 获取动画的FPS
  getAnimationFps: function(action) {
    if (!action) return 30;

    // MMD/VMD 固定30fps
    const format = (action.format || 'vmd').toLowerCase();
    if (format === 'vmd') return 30;

    // 其他格式从配置读取，默认30
    return action.fps || 30;
  },

  // 根据offset同步动画
  syncAnimationToOffset: function(offset) {
    // 独立窗口模式：校准需要直接访问动画模块，无法通过 IPC 操作
    if (window.__NEKO_JUKEBOX_STANDALONE__) return;

    const player = Jukebox.getPlayer();
    if (!player || !player.audio) return;

    const musicTime = player.audio.currentTime;
    const animTime = Jukebox.getAnimationTimeForMusicTime(musicTime, offset);
    Jukebox.syncCurrentAnimationToTime(animTime);
  },

  // 根据模型类型获取对应格式的动画
  // 没有默认动画本身也是合理的状态，可以通过点击已设置的默认动画来取消它
  getActionForModel: function(song) {
    const modelType = Jukebox.getModelType();

    // 模型类型到动画格式的映射
    const formatMap = {
      'mmd': 'vmd',
      'live3d': 'vmd',
      'vrm': 'vrma',
      'fbx': 'fbx'
    };

    const targetFormat = formatMap[modelType];
    if (!targetFormat) {
      console.log('[Jukebox] 当前模型类型不支持动画:', modelType);
      return null;
    }

    // 获取绑定的动画中对应格式的动画
    const boundActions = song.boundActions || [];
    const availableActions = boundActions.filter(a => a.missing !== true);
    const formatActions = availableActions.filter(a =>
      (a.format || 'vmd').toLowerCase() === targetFormat
    );

    if (formatActions.length === 0) {
      console.log('[Jukebox] 歌曲没有绑定', targetFormat.toUpperCase(), '格式的动画');
      return null;
    }

    // 如果用户设置了默认动画，优先使用它
    if (song.defaultAction) {
      const defaultAction = formatActions.find(a => a.id === song.defaultAction);
      if (defaultAction) {
        return defaultAction;
      }
      // defaultAction 是其他格式（如 VMD），当前格式（如 VRMA）有可用动画则 fallback
      if (formatActions.length > 0) {
        console.log('[Jukebox] 默认动画格式不匹配，使用该格式的第一个动画:', formatActions[0].name);
        return formatActions[0];
      }
      // 该格式无可用动画
      console.log('[Jukebox] 默认动画格式不匹配且无可用动画');
      return null;
    }

    // 没有设置默认动画，不播放动画
    return null;
  },

  updatePlayingStatus: function(song) {
    const statusText = document.getElementById('jukebox-status-text');
    if (statusText) {
      statusText.textContent = window.t('Jukebox.playing', { name: song.name, artist: song.artist }) || `正在播放: ${song.name} - ${song.artist}`;
    }

    Jukebox._resetAllButtons();
    Jukebox.startProgressUpdate();
    Jukebox.updateGlobalTransportControls();

    const currentRow = document.querySelector(`tr[data-song-id="${CSS.escape(song.id)}"]`);
    if (currentRow) {
      const td = currentRow.querySelector('td:last-child');
      if (td) {
        td.innerHTML = '';

        const pauseBtn = document.createElement('button');
        pauseBtn.className = 'play-btn pause-btn';
        pauseBtn.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/></svg>';
        Jukebox.setupTooltip(pauseBtn, window.t('Jukebox.pause', '暂停'));
        pauseBtn.addEventListener('click', () => Jukebox.togglePause());

        const stopBtn = document.createElement('button');
        stopBtn.className = 'play-btn playing';
        stopBtn.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M6 6h12v12H6z"/></svg>';
        Jukebox.setupTooltip(stopBtn, window.t('Jukebox.stop', '停止'));
        stopBtn.addEventListener('click', () => Jukebox.stopPlayback());

        td.appendChild(pauseBtn);
        td.appendChild(stopBtn);
      }
    }
  },

  updatePausedStatus: function(song) {
    const statusText = document.getElementById('jukebox-status-text');
    if (statusText) {
      statusText.textContent = window.t('Jukebox.pausedStatus', { name: song.name }) || `已暂停: ${song.name}`;
    }

    Jukebox._resetAllButtons();
    Jukebox.updateGlobalTransportControls();

    const currentRow = document.querySelector(`tr[data-song-id="${CSS.escape(song.id)}"]`);
    if (currentRow) {
      const td = currentRow.querySelector('td:last-child');
      if (td) {
        td.innerHTML = '';

        const resumeBtn = document.createElement('button');
        resumeBtn.className = 'play-btn resume-btn';
        resumeBtn.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M8 5v14l11-7z"/></svg>';
        Jukebox.setupTooltip(resumeBtn, window.t('Jukebox.resume', '继续'));
        resumeBtn.addEventListener('click', () => Jukebox.togglePause());

        const stopBtn = document.createElement('button');
        stopBtn.className = 'play-btn playing';
        stopBtn.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M6 6h12v12H6z"/></svg>';
        Jukebox.setupTooltip(stopBtn, window.t('Jukebox.stop', '停止'));
        stopBtn.addEventListener('click', () => Jukebox.stopPlayback());

        td.appendChild(resumeBtn);
        td.appendChild(stopBtn);
      }
    }
  },

  _resetAllButtons: function() {
    document.querySelectorAll('#jukebox-song-list td:last-child').forEach(td => {
      const songId = td.parentElement?.dataset?.songId;
      if (!songId) return;
      td.innerHTML = '';
      const btn = document.createElement('button');
      btn.className = 'play-btn';
      btn.dataset.songId = songId;
      btn.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M8 5v14l11-7z"/></svg>';
      Jukebox.setupTooltip(btn, window.t('Jukebox.play', '播放'));
      btn.addEventListener('click', () => Jukebox_playSong(songId));
      td.appendChild(btn);
    });
  },

  updateStoppedStatus: function() {
    const statusText = document.getElementById('jukebox-status-text');
    if (statusText) {
      statusText.textContent = window.t('Jukebox.ready', '准备就绪');
    }

    Jukebox.stopProgressUpdate();
    Jukebox._resetAllButtons();
    Jukebox.updateGlobalTransportControls();
  },

  showError: function(message) {
    const statusText = document.getElementById('jukebox-status-text');
    if (statusText) {
      statusText.textContent = (window.t('Jukebox.error', { message }) || '错误: ' + message);
      statusText.style.color = '#ff6b6b';
    }
  },

  formatDuration: function(seconds) {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}:${secs.toString().padStart(2, '0')}`;
  },

  escapeHtml: function(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  },

  escapeAttr: function(text) {
    return Jukebox.escapeHtml(text).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  },

  escapeJsAttr: function(text) {
    const jsText = String(text)
      .replace(/\\/g, '\\\\')
      .replace(/'/g, "\\'")
      .replace(/\r/g, '\\r')
      .replace(/\n/g, '\\n')
      .replace(/\u2028/g, '\\u2028')
      .replace(/\u2029/g, '\\u2029');
    return Jukebox.escapeAttr(jsText);
  },

  /**
   * 语言切换后刷新 Jukebox UI 文本
   * 独立窗口模式直接重载页面；嵌入模式逐一更新 DOM 元素
   */
  refreshLocale: function() {
    // 独立窗口（N.E.K.O.-PC）：重载最干净
    if (window.__NEKO_JUKEBOX_STANDALONE__) {
      location.reload();
      return;
    }

    // 嵌入模式：逐一刷新已渲染的静态文本
    const c = Jukebox.State.container;
    if (!c) return;

    // --- Header ---
    var h3 = c.querySelector('.jukebox-header h3');
    if (h3) h3.textContent = window.t('Jukebox.title', '点歌台');
    var settingsBtn = c.querySelector('.jukebox-settings');
    if (settingsBtn) {
      settingsBtn.dataset.tooltip = window.t('Jukebox.manager', '点歌台管理与导入');
      settingsBtn.removeAttribute('title');
      settingsBtn.setAttribute('aria-label', settingsBtn.dataset.tooltip);
      Jukebox.refreshTooltip(settingsBtn);
      var settingsLabel = settingsBtn.querySelector('.jukebox-settings-label');
      if (settingsLabel) settingsLabel.textContent = window.t('Jukebox.settingsShort', '管理/导入');
    }
    var minBtn = c.querySelector('.jukebox-minimize');
    if (minBtn) {
      minBtn.dataset.tooltip = window.t('Jukebox.minimize', '最小化');
      minBtn.removeAttribute('title');
      minBtn.setAttribute('aria-label', minBtn.dataset.tooltip);
      Jukebox.refreshTooltip(minBtn);
    }
    var closeBtn = c.querySelector('.jukebox-close');
    if (closeBtn) {
      closeBtn.dataset.tooltip = window.t('Jukebox.close', '关闭');
      closeBtn.removeAttribute('title');
      closeBtn.setAttribute('aria-label', closeBtn.dataset.tooltip);
      Jukebox.refreshTooltip(closeBtn);
    }

    // --- Calibration ---
    var calToggle = c.querySelector('#jukebox-calibration-toggle');
    if (calToggle) calToggle.textContent = window.t('Jukebox.calibrateAnimation', '校准动画');
    var calClose = c.querySelector('.jukebox-calibration-close');
    if (calClose) calClose.textContent = window.t('Jukebox.closeCalibration', '关闭校准控制台');
    var calReset = c.querySelector('.jukebox-calibration-reset');
    if (calReset) { calReset.textContent = window.t('Jukebox.reset', '重置'); calReset.title = window.t('Jukebox.reset', '重置'); }
    var calTitle = c.querySelector('.jukebox-calibration-title');
    if (calTitle) {
      var fpsSpan = calTitle.querySelector('#jukebox-calibration-fps');
      var fpsHtml = fpsSpan ? fpsSpan.outerHTML : '';
      calTitle.innerHTML = window.t('Jukebox.animationCalibration', '动画校准') + ' ' + fpsHtml;
    }

    // --- Notice ---
    var notices = c.querySelectorAll('.jukebox-notice-item');
    if (notices[0]) notices[0].textContent = window.t('Jukebox.noticeDance', '💃 伴舞服务目前仅在载入 MMD 形象时可用，后续会增加更多互动');
    if (notices[1]) notices[1].textContent = window.t('Jukebox.noticeMusic', '⚠️ 当前歌曲仅供测试，后续版本将清除版权音乐，请自行导入');

    // --- Table headers ---
    var ths = c.querySelectorAll('.jukebox-table thead th');
    if (ths.length >= 4) {
      var sequenceLabel = ths[0].querySelector('span');
      if (sequenceLabel) {
        sequenceLabel.textContent = window.t('Jukebox.sequence', '序号');
      } else {
        ths[0].textContent = window.t('Jukebox.sequence', '序号');
      }
      ths[1].textContent = window.t('Jukebox.song', '歌曲');
      ths[2].textContent = window.t('Jukebox.artist', '艺术家');
      ths[3].textContent = window.t('Jukebox.action', '操作');
    }

    // --- Mute button ---
    var speakerBtn = c.querySelector('#jukebox-speaker-btn');
    if (speakerBtn) {
      speakerBtn.removeAttribute('title');
      speakerBtn.setAttribute('aria-label', window.t('Jukebox.mute', '静音'));
    }
    var prevBtn = c.querySelector('#jukebox-control-prev');
    if (prevBtn) {
      prevBtn.removeAttribute('title');
      prevBtn.setAttribute('aria-label', window.t('Jukebox.previousSong', '上一首'));
    }
    var nextBtn = c.querySelector('#jukebox-control-next');
    if (nextBtn) {
      nextBtn.removeAttribute('title');
      nextBtn.setAttribute('aria-label', window.t('Jukebox.nextSong', '下一首'));
    }
    Jukebox.renderPlaybackControls();
    Jukebox.updateSongSortLockControls(c);

    // --- Re-render song list (preserves playback state) ---
    if (Jukebox.State.songs && Jukebox.State.songs.length) {
      Jukebox.renderList();
    }

    // --- Re-render SongActionManager (if visible) ---
    try {
      if (Jukebox.SongActionManager && Jukebox.SongActionManager.element) {
        // Rebuild panel to refresh tab titles and static text
        var panel = Jukebox.SongActionManager.element;
        var titleEl = panel.querySelector('.sam-title');
        if (titleEl) titleEl.textContent = window.t('Jukebox.managerTitle', '点歌台管理');
        var tabs = panel.querySelectorAll('.sam-tab');
        var tabKeys = ['Jukebox.songs', 'Jukebox.actions', 'Jukebox.bindings'];
        var tabDefaults = ['歌曲库', '舞蹈动作', '歌曲绑定'];
        tabs.forEach(function(tab, i) {
          if (tabKeys[i]) tab.textContent = window.t(tabKeys[i], tabDefaults[i]);
        });
        var samCloseBtn = panel.querySelector('.sam-close-btn');
        if (samCloseBtn) {
          samCloseBtn.dataset.tooltip = window.t('Jukebox.close', '关闭');
          samCloseBtn.removeAttribute('title');
          samCloseBtn.setAttribute('aria-label', samCloseBtn.dataset.tooltip);
          Jukebox.refreshTooltip(samCloseBtn);
        }
        // Re-render active tab content
        Jukebox.SongActionManager.render();
      }
    } catch (e) { console.warn('[Jukebox] refreshLocale SongActionManager error:', e); }

    console.log('[Jukebox] UI 文本已刷新');
  }
});
