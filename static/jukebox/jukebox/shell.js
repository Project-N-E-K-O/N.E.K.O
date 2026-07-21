Object.assign(window.Jukebox, {
  init: function() {
    console.log('[Jukebox]', window.t('Jukebox.initialized', '初始化点歌台...'));
    Jukebox.loadPlaybackPreferences();

    window.Jukebox_playSong = Jukebox.playSong;
    window.Jukebox_close = Jukebox.close;
    window.Jukebox_hide = Jukebox.hide;
    window.Jukebox_updateVolume = Jukebox.updateVolume;
    window.Jukebox_logVolumeChange = Jukebox.logVolumeChange;
    window.Jukebox_togglePause = Jukebox.togglePause;

    // 独立窗口模式不需要绑定旧 DOM；懒加载模式只跳过按钮绑定，仍保留聊天最小化清理监听。
    if (!window.__NEKO_JUKEBOX_STANDALONE__) {
      if (!window.__NEKO_JUKEBOX_LAZY_LOADER__) {
        Jukebox.setupButton();
      }
      Jukebox.setupCloseListener();
    }
  },

  setupButton: function(retries = 0) {
    const MAX_RETRIES = 20;
    const jukeboxButton = document.getElementById('jukeboxButton');
    if (!jukeboxButton) {
      // React Chat 模式下按钮由 React 组件渲染，通过 onJukeboxClick 回调直接调用
      // window.Jukebox.toggle()，无需绑定 DOM 按钮
      const reactChatRoot = document.getElementById('react-chat-window-root');
      if (reactChatRoot) {
        console.log('[Jukebox]', 'React Chat 模式，跳过 DOM 按钮绑定');
        return;
      }
      if (retries >= MAX_RETRIES) {
        console.error('[Jukebox]', window.t('Jukebox.btnNotFoundGiveUp', '点歌台按钮在重试后仍未找到，放弃绑定'));
        return;
      }
      console.warn('[Jukebox]', window.t('Jukebox.btnNotFound', '点歌台按钮不存在，等待加载...'));
      setTimeout(() => Jukebox.setupButton(retries + 1), 500);
      return;
    }

    jukeboxButton.addEventListener('click', Jukebox.toggle);
    console.log('[Jukebox]', window.t('Jukebox.btnBound', '点歌台按钮已绑定'));
  },

  setupCloseListener: function(retries = 0) {
    const MAX_RETRIES = 20;
    if (Jukebox.State.observer && Jukebox.State.closeListenerButton) return;

    const toggleChatBtn = document.getElementById('toggle-chat-btn');
    if (toggleChatBtn) {
      if (!Jukebox.State.closeListenerHandler) {
        Jukebox.State.closeListenerHandler = () => {
          // 仅在聊天框即将最小化时销毁（展开时不需要）
          const chatContainer = document.getElementById('chat-container');
          const isCurrentlyMinimized = chatContainer &&
            (chatContainer.classList.contains('minimized') || chatContainer.classList.contains('mobile-collapsed'));
          if (isCurrentlyMinimized) {
            // 当前已最小化 → 即将展开，不销毁
            return;
          }
          console.log('[Jukebox]', window.t('Jukebox.minimizeDetected', '检测到对话框最小化，销毁点歌台'));
          Jukebox.destroy();
        };
      }
      if (Jukebox.State.closeListenerButton !== toggleChatBtn) {
        if (Jukebox.State.closeListenerButton && Jukebox.State.closeListenerHandler) {
          Jukebox.State.closeListenerButton.removeEventListener('click', Jukebox.State.closeListenerHandler);
        }
        toggleChatBtn.addEventListener('click', Jukebox.State.closeListenerHandler);
        Jukebox.State.closeListenerButton = toggleChatBtn;
      }
      console.log('[Jukebox]', window.t('Jukebox.minimizeListenerSet', '最小化按钮监听器已设置'));
    } else {
      if (retries >= MAX_RETRIES) {
        console.error('[Jukebox]', window.t('Jukebox.minimizeBtnNotFoundGiveUp', '最小化按钮在重试后仍未找到，放弃监听'));
        return;
      }
      console.warn('[Jukebox]', window.t('Jukebox.minimizeBtnNotFound', '最小化按钮不存在，等待加载...'));
      setTimeout(() => Jukebox.setupCloseListener(retries + 1), 500);
      return;
    }

    if (!Jukebox.State.observer) {
      const observer = new MutationObserver((mutations) => {
        mutations.forEach((mutation) => {
          if (mutation.type === 'childList') {
            const removedNodes = Array.from(mutation.removedNodes);
            const jukeboxRemoved = removedNodes.some(node =>
              node === Jukebox.State.container
            );

            if (jukeboxRemoved) {
              console.log('[Jukebox]', window.t('Jukebox.removedDetected', '检测到点歌台被移除'));
              Jukebox.State.isOpen = false;
            }
          }
        });
      });

      observer.observe(document.body, { childList: true, subtree: true });
      Jukebox.State.observer = observer;
    }

    console.log('[Jukebox]', window.t('Jukebox.closeListenerSet', '关闭监听器已设置'));
  },

  cleanupCloseListener: function() {
    if (Jukebox.State.closeListenerButton && Jukebox.State.closeListenerHandler) {
      Jukebox.State.closeListenerButton.removeEventListener('click', Jukebox.State.closeListenerHandler);
    }
    Jukebox.State.closeListenerButton = null;
    Jukebox.State.closeListenerHandler = null;
    if (Jukebox.State.observer) {
      Jukebox.State.observer.disconnect();
      Jukebox.State.observer = null;
    }
  },

  toggle: function() {
    // Electron Pet 窗口：委托给主进程打开独立 Jukebox 窗口
    if (!window.__NEKO_JUKEBOX_STANDALONE__ && typeof window.__nekoJukeboxToggle === 'function') {
      window.__nekoJukeboxToggle();
      return;
    }
    if (Jukebox.State.isHidden) {
      Jukebox.show();
    } else if (Jukebox.State.isOpen) {
      Jukebox.hide();
    } else {
      Jukebox.open();
    }
  },

  open: function() {
    const Jukebox = window.Jukebox || this;
    if (Jukebox.State.isOpen) return;

    Jukebox.buildUI();
    if (!window.__NEKO_JUKEBOX_STANDALONE__) {
      Jukebox.setupCloseListener();
    }

    requestAnimationFrame(() => {
      setTimeout(() => {
        if (!Jukebox.State.isOpen || !Jukebox.State.container) {
          console.log('[Jukebox] 点歌台已关闭，取消初始化');
          return;
        }
        console.log('[Jukebox] 准备加载歌曲，检查容器...');
        const tbody = document.getElementById('jukebox-song-list');
        console.log('[Jukebox] 歌曲列表容器:', tbody);
        Jukebox.loadSongs();
        Jukebox.initPlayer();
        Jukebox.initVolumeSlider();
        Jukebox.updateCalibrationVisibility();
      }, 100);
    });

    Jukebox.State.isOpen = true;

    // 监听管理器独立窗口的刷新通知（BroadcastChannel 跨窗口通信）
    try {
      if (!Jukebox._broadcastChannel) {
        Jukebox._broadcastChannel = new BroadcastChannel('neko-jukebox');
        Jukebox._broadcastChannel.onmessage = function(e) {
          if (e.data && e.data.type === 'reload' && Jukebox.State.isOpen) {
            console.log('[Jukebox] 收到管理器刷新通知，重新加载歌曲');
            Jukebox.loadSongs();
          }
        };
      }
    } catch (e) {}

    Jukebox.startConfigPolling();

    const jukeboxButton = document.getElementById('jukeboxButton');
    if (jukeboxButton) {
      jukeboxButton.classList.add('active');
    }

    console.log('[Jukebox] 点歌台已打开');
  },

  hide: function() {
    if (!Jukebox.State.container) return;

    const container = Jukebox.State.container.querySelector('.jukebox-container');
    if (container) {
      container.classList.remove('open');
      container.classList.add('hidden');
    }
    Jukebox.State.isHidden = true;

    const jukeboxButton = document.getElementById('jukeboxButton');
    if (jukeboxButton) {
      jukeboxButton.classList.remove('active');
    }

    // 同时关闭管理器UI
    Jukebox.SongActionManager.hide();

    console.log('[Jukebox] 点歌台已隐藏');
  },

  show: function() {
    if (!Jukebox.State.container) return;

    const container = Jukebox.State.container.querySelector('.jukebox-container');
    if (container) {
      container.classList.remove('hidden');
      container.classList.add('open');
    }
    Jukebox.State.isHidden = false;

    const jukeboxButton = document.getElementById('jukeboxButton');
    if (jukeboxButton) {
      jukeboxButton.classList.add('active');
    }

    console.log('[Jukebox] 点歌台已显示');
  },

  destroyPlayer: function() {
    const player = Jukebox.State.player;
    if (player) {
      try {
        if (typeof player.pause === 'function') player.pause();
      } catch (_) {}
      try {
        if (typeof player.seek === 'function') player.seek(0);
      } catch (_) {}
      try {
        if (player.list && typeof player.list.clear === 'function') player.list.clear();
      } catch (_) {}
      try {
        if (typeof player.destroy === 'function') player.destroy();
      } catch (error) {
        console.warn('[Jukebox] APlayer 销毁失败:', error);
      }
    }

    Jukebox.State.player = null;
    Jukebox.State.boundPlayer = null;
    Jukebox.State.audioElement = null;
  },

  prepareForUnload: function() {
    Jukebox.stopProgressUpdate();
    Jukebox.destroyPlayer();
    Jukebox.hideTooltip();

    if (Jukebox.State.marqueeRaf) {
      cancelAnimationFrame(Jukebox.State.marqueeRaf);
      Jukebox.State.marqueeRaf = null;
    }
    if (Jukebox.State.marqueeItems) {
      Jukebox.State.marqueeItems.clear();
    }

    Jukebox.stopConfigPolling();

    try {
      if (Jukebox._broadcastChannel) {
        Jukebox._broadcastChannel.onmessage = null;
        Jukebox._broadcastChannel.close();
        Jukebox._broadcastChannel = null;
      }
    } catch (_) {}

  },

  notifyFullClose: function(reason) {
    try {
      window.dispatchEvent(new CustomEvent('neko:jukebox-full-close', {
        detail: { reason: reason || 'close' }
      }));
    } catch (_) {}
  },

  close: function() {
    Jukebox.stopPlayback();
    Jukebox.prepareForUnload();

    // 销毁管理器面板（移除 DOM 节点 + 清理拖拽监听）
    Jukebox.SongActionManager.destroy();

    // 清理点歌台拖拽事件监听
    if (Jukebox.State._dragCleanup) {
      Jukebox.State._dragCleanup();
      Jukebox.State._dragCleanup = null;
    }

    // 清理缩放事件监听
    if (Jukebox.State._resizeCleanup) {
      Jukebox.State._resizeCleanup();
      Jukebox.State._resizeCleanup = null;
    }

    // 断开独立窗口拖拽层守护 observer
    if (Jukebox.State._dragGuard) {
      try { Jukebox.State._dragGuard.disconnect(); } catch (_) {}
      Jukebox.State._dragGuard = null;
    }

    if (Jukebox.State.container) {
      if (Jukebox.State.hasCustomWindowSize) {
        Jukebox.saveWindowSize(Jukebox.State.container.querySelector('.jukebox-container'));
      }
      Jukebox.State.container.remove();
      Jukebox.State.container = null;
    }

    if (Jukebox.State.styleElement) {
      Jukebox.State.styleElement.remove();
      Jukebox.State.styleElement = null;
    }

    Jukebox.State.isOpen = false;
    Jukebox.State.isHidden = false;
    Jukebox.State.hasCustomWindowSize = false;
    Jukebox.stopConfigPolling();
    Jukebox.State.configRevision = null;

    // 清理 BroadcastChannel
    try {
      if (Jukebox._broadcastChannel) {
        Jukebox._broadcastChannel.onmessage = null;
        Jukebox._broadcastChannel.close();
        Jukebox._broadcastChannel = null;
      }
    } catch (e) {}

    // 清空歌曲列表和元素映射，确保下次打开时重新渲染
    Jukebox.State.songs = [];
    Jukebox.State.songElements = {};

    const jukeboxButton = document.getElementById('jukeboxButton');
    if (jukeboxButton) {
      jukeboxButton.classList.remove('active');
    }

    console.log('[Jukebox] 点歌台已关闭');
    Jukebox.notifyFullClose('close');
  },

  destroy: function() {
    Jukebox.stopPlayback();
    Jukebox.prepareForUnload();

    Jukebox.SongActionManager.destroy();

    // 清理拖拽事件监听
    if (Jukebox.State._dragCleanup) {
      Jukebox.State._dragCleanup();
      Jukebox.State._dragCleanup = null;
    }

    // 清理缩放事件监听
    if (Jukebox.State._resizeCleanup) {
      Jukebox.State._resizeCleanup();
      Jukebox.State._resizeCleanup = null;
    }

    // 断开独立窗口拖拽层守护 observer
    if (Jukebox.State._dragGuard) {
      try { Jukebox.State._dragGuard.disconnect(); } catch (_) {}
      Jukebox.State._dragGuard = null;
    }

    if (Jukebox.State.container) {
      if (Jukebox.State.hasCustomWindowSize) {
        Jukebox.saveWindowSize(Jukebox.State.container.querySelector('.jukebox-container'));
      }
      Jukebox.State.container.remove();
      Jukebox.State.container = null;
    }

    if (Jukebox.State.styleElement) {
      Jukebox.State.styleElement.remove();
      Jukebox.State.styleElement = null;
    }

    if (Jukebox.State.observer) {
      Jukebox.State.observer.disconnect();
      Jukebox.State.observer = null;
    }

    Jukebox.State.isOpen = false;
    Jukebox.State.isHidden = false;
    Jukebox.State.hasCustomWindowSize = false;
    Jukebox.State.songs = [];
    Jukebox.State.songElements = {}; // 清空元素映射

    console.log('[Jukebox] 点歌台已销毁');
    Jukebox.notifyFullClose('destroy');
  },

  buildUI: function() {
    Jukebox.loadPlaybackPreferences();

    const wrapper = document.createElement('div');
    wrapper.className = 'jukebox-wrapper';

    const sidePanel = Jukebox.SongActionManager.create();

    const jukeboxContainer = document.createElement('div');
    jukeboxContainer.className = 'jukebox-container';
    Jukebox.applyStoredWindowSize(jukeboxContainer);
    jukeboxContainer.innerHTML = `
      <div class="jukebox-drag-overlay"></div>
      <div class="jukebox-header">
        <div class="jukebox-header-left">
          <h3>${window.t('Jukebox.title', '点歌台')}</h3>
          <span id="jukebox-status-text" class="jukebox-status-text">${window.t('Jukebox.ready', '准备就绪')}</span>
        </div>
        <div class="jukebox-header-drag-fill" aria-hidden="true"></div>
        <div class="jukebox-header-buttons">
          <button class="jukebox-settings" onclick="Jukebox.SongActionManager.toggle()" data-tooltip="${Jukebox.escapeAttr(window.t('Jukebox.manager', '点歌台管理与导入'))}" aria-label="${Jukebox.escapeAttr(window.t('Jukebox.manager', '点歌台管理与导入'))}">
            <span class="jukebox-settings-icon" aria-hidden="true">⚙</span>
            <span class="jukebox-settings-label">${window.t('Jukebox.settingsShort', '管理/导入')}</span>
          </button>
          <button class="jukebox-pin" type="button" data-neko-window-control="pin" data-i18n-title="common.pinWindow" data-i18n-aria="common.pinWindow" data-tooltip="${Jukebox.escapeAttr(window.t('common.pinWindow', '置顶窗口'))}" title="${Jukebox.escapeAttr(window.t('common.pinWindow', '置顶窗口'))}" aria-label="${Jukebox.escapeAttr(window.t('common.pinWindow', '置顶窗口'))}" aria-pressed="false" hidden>
            <span class="neko-window-pin-icon" aria-hidden="true"></span>
          </button>
          <button class="jukebox-minimize" onclick="Jukebox_hide()" data-tooltip="${Jukebox.escapeAttr(window.t('Jukebox.minimize', '最小化'))}" aria-label="${Jukebox.escapeAttr(window.t('Jukebox.minimize', '最小化'))}">−</button>
          <button class="jukebox-close" onclick="Jukebox_close()" data-tooltip="${Jukebox.escapeAttr(window.t('Jukebox.close', '关闭'))}" aria-label="${Jukebox.escapeAttr(window.t('Jukebox.close', '关闭'))}">×</button>
        </div>
      </div>
      <div class="jukebox-resize-handle" data-dir="n"></div>
      <div class="jukebox-resize-handle" data-dir="s"></div>
      <div class="jukebox-resize-handle" data-dir="w"></div>
      <div class="jukebox-resize-handle" data-dir="e"></div>
      <div class="jukebox-resize-handle" data-dir="nw"></div>
      <div class="jukebox-resize-handle" data-dir="ne"></div>
      <div class="jukebox-resize-handle" data-dir="sw"></div>
      <div class="jukebox-resize-handle" data-dir="se"></div>
      <div id="jukebox-calibration-section" class="jukebox-calibration-section" style="display: none;">
        <button id="jukebox-calibration-toggle" class="jukebox-calibration-toggle" onclick="Jukebox.toggleCalibrationPanel()">
          ${window.t('Jukebox.calibrateAnimation', '校准动画')}
        </button>
        <div id="jukebox-calibration-panel" class="jukebox-calibration-panel" style="display: none;">
          <div class="jukebox-calibration-header">
            <span class="jukebox-calibration-title">${window.t('Jukebox.animationCalibration', '动画校准')} <span id="jukebox-calibration-fps" class="jukebox-calibration-fps">(30 FPS)</span></span>
            <button class="jukebox-calibration-close" onclick="Jukebox.toggleCalibrationPanel()">${window.t('Jukebox.closeCalibration', '关闭校准控制台')}</button>
          </div>
          <div class="jukebox-calibration-controls">
            <button class="jukebox-calibration-btn" onclick="Jukebox.adjustOffset(-30)" title="${window.t('Jukebox.advance1s', '动画提前1秒')}"><<</button>
            <button class="jukebox-calibration-btn" onclick="Jukebox.adjustOffset(-10)" title="${window.t('Jukebox.advance10f', '动画提前10帧')}"><</button>
            <button class="jukebox-calibration-btn" onclick="Jukebox.adjustOffset(-1)" title="${window.t('Jukebox.advance1f', '动画提前1帧')}"><</button>
            <span id="jukebox-calibration-value" class="jukebox-calibration-value">0${window.t('Jukebox.frames', '帧')}</span>
            <button class="jukebox-calibration-btn" onclick="Jukebox.adjustOffset(1)" title="${window.t('Jukebox.delay1f', '动画推迟1帧')}">></button>
            <button class="jukebox-calibration-btn" onclick="Jukebox.adjustOffset(10)" title="${window.t('Jukebox.delay10f', '动画推迟10帧')}">></button>
            <button class="jukebox-calibration-btn" onclick="Jukebox.adjustOffset(30)" title="${window.t('Jukebox.delay1s', '动画推迟1秒')}">>></button>
            <button class="jukebox-calibration-reset" onclick="Jukebox.resetOffset()" title="${window.t('Jukebox.reset', '重置')}">${window.t('Jukebox.reset', '重置')}</button>
          </div>
        </div>
      </div>
      <div class="jukebox-notice">
        <div class="jukebox-notice-item">${window.t('Jukebox.noticeDance', '💃 伴舞服务目前仅在载入 MMD 形象时可用，后续会增加更多互动')}</div>
        <div class="jukebox-notice-item">${window.t('Jukebox.noticeMusic', '⚠️ 当前歌曲仅供测试，后续版本将清除版权音乐，请自行导入')}</div>
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
                  <span>${window.t('Jukebox.sequence', '序号')}</span>
          <button type="button" class="jukebox-sort-lock-btn" onclick="Jukebox.toggleSongSortLock(event)" data-tooltip="${Jukebox.escapeAttr(window.t('Jukebox.songSortLockTooltipLocked', '歌曲排序已锁定：防止误拖，点击解锁后可拖动歌曲调整顺序'))}" aria-label="${Jukebox.escapeAttr(window.t('Jukebox.unlockSongSort', '解锁歌曲排序'))}" aria-pressed="false">
                    ${Jukebox.getSongSortLockIcon()}
                  </button>
                </div>
              </th>
              <th>${window.t('Jukebox.song', '歌曲')}</th>
              <th>${window.t('Jukebox.artist', '艺术家')}</th>
              <th>${window.t('Jukebox.action', '操作')}</th>
            </tr>
          </thead>
          <tbody id="jukebox-song-list">
            <tr>
              <td colspan="4" class="loading">${window.t('Jukebox.loading', '加载中...')}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="jukebox-controls-row">
        <div class="jukebox-progress">
          <span id="jukebox-time-current">0:00</span>
          <input type="range" id="jukebox-progress-slider" min="0" max="100" step="0.1" value="0">
          <span id="jukebox-time-total">0:00</span>
        </div>
        <div class="jukebox-playback-controls">
          <div id="jukebox-mode-controls" class="jukebox-mode-controls" aria-label="${Jukebox.escapeAttr(window.t('Jukebox.switchPlaybackMode', '切换播放模式'))}"></div>
          <div class="jukebox-control-divider" aria-hidden="true"></div>
          <div class="jukebox-transport-controls">
            <button type="button" class="play-btn jukebox-transport-btn" id="jukebox-control-prev" onclick="Jukebox.playAdjacentSong(-1)" aria-label="${Jukebox.escapeAttr(window.t('Jukebox.previousSong', '上一首'))}">
              <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="M6 6h2v12H6V6zm3 6 9 6V6l-9 6z"/></svg>
            </button>
            <button type="button" class="play-btn jukebox-transport-btn jukebox-play-pause-btn" id="jukebox-control-play-pause" onclick="Jukebox.toggleGlobalPlayPause()" data-tooltip="${Jukebox.escapeAttr(window.t('Jukebox.play', '播放'))}" aria-label="${Jukebox.escapeAttr(window.t('Jukebox.play', '播放'))}">
              <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="M8 5v14l11-7z"/></svg>
            </button>
            <button type="button" class="play-btn jukebox-transport-btn" id="jukebox-control-next" onclick="Jukebox.playAdjacentSong(1)" aria-label="${Jukebox.escapeAttr(window.t('Jukebox.nextSong', '下一首'))}">
              <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="M16 6h2v12h-2V6zM6 18l9-6-9-6v12z"/></svg>
            </button>
          </div>
          <div class="jukebox-volume-wrapper">
            <button class="jukebox-speaker-btn" id="jukebox-speaker-btn" aria-label="${Jukebox.escapeAttr(window.t('Jukebox.mute', '静音'))}">
              <svg class="speaker-icon" viewBox="0 0 24 24" width="20" height="20">
                <path fill="currentColor" d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z"/>
              </svg>
              <svg class="speaker-muted-icon" viewBox="0 0 24 24" width="20" height="20" style="display:none;">
                <path fill="currentColor" d="M16.5 12c0-1.77-1.02-3.29-2.5-4.03v2.21l2.45 2.45c.03-.2.05-.41.05-.63zm2.5 0c0 .94-.2 1.82-.54 2.64l1.51 1.51C20.63 14.91 21 13.5 21 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89.86 5 3.54 5 6.71zM4.27 3L3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06c1.38-.31 2.63-.95 3.69-1.81L19.73 21 21 19.73l-9-9L4.27 3zM12 4L9.91 6.09 12 8.18V4z"/>
              </svg>
            </button>
            <div class="jukebox-volume-popup">
              <div class="jukebox-volume-slider-container">
                <div class="jukebox-volume-track"></div>
                <input type="range" id="jukebox-volume-slider" min="0" max="1" step="0.01" value="1" oninput="Jukebox_updateVolume(this.value)" onchange="Jukebox_logVolumeChange(this.value)">
              </div>
              <span id="jukebox-volume-value" class="jukebox-volume-value-editable">100%</span>
            </div>
          </div>
        </div>
      </div>
    `;

    wrapper.appendChild(jukeboxContainer);
    document.body.appendChild(wrapper);
    document.body.appendChild(sidePanel);
    Jukebox.State.container = wrapper;

    // 独立窗口模式下由下方的专属拖拽层（.jukebox-drag-overlay）处理原生窗口拖拽，
    // JS 拖拽会因 preventDefault 与原生拖拽冲突，且 inset:0!important 阻止 wrapper 移动
    if (!window.__NEKO_JUKEBOX_STANDALONE__) {
      Jukebox.bindWindowDrag(wrapper, jukeboxContainer);
      Jukebox.bindPanelDrag(sidePanel);
      Jukebox.bindPanelResize(sidePanel);
      Jukebox.bindResize(jukeboxContainer);
    }

    Jukebox.injectStyles();
    Jukebox.renderPlaybackControls();
    Jukebox.bindTextTooltips(jukeboxContainer.querySelector('.jukebox-header-buttons'));
    const sortLockButton = jukeboxContainer.querySelector('.jukebox-sort-lock-btn');
    if (sortLockButton) {
      Jukebox.setupTooltip(sortLockButton, () => Jukebox.getSongSortLockTooltip());
    }
    Jukebox.updateSongSortLockControls(jukeboxContainer);

    // Standalone 点歌台的桌面端拖拽/缩放统一交给 static/jukebox/jukebox-standalone.js，
    // 避免在 open() 首帧先写入旧的 app-region 热区，导致标题按钮命中缓存异常。

  },

  // 窗口拖拽功能
  bindWindowDrag: function(wrapper, container) {
    if (Jukebox.State._dragCleanup) {
      Jukebox.State._dragCleanup();
      Jukebox.State._dragCleanup = null;
    }

    const dragRoot = container.querySelector('.jukebox-header');
    if (!dragRoot) return;

    const onMouseDown = (e) => {
      // 只允许标题栏拖动；标题栏按钮仍保持可点击。
      if (e.target.closest('button, input, a, select, textarea, .jukebox-header-buttons')) return;

      e.preventDefault();
      const clientX = e.touches ? e.touches[0].clientX : e.clientX;
      const clientY = e.touches ? e.touches[0].clientY : e.clientY;

      const rect = wrapper.getBoundingClientRect();

      // 将 bottom/right 定位转换为 left/top
      if (!wrapper.style.left) {
        wrapper.style.left = rect.left + 'px';
        wrapper.style.top = rect.top + 'px';
        wrapper.style.bottom = 'auto';
        wrapper.style.right = 'auto';
      }

      Jukebox.State.isDragging = true;
      Jukebox.State.dragOffset = {
        x: clientX - rect.left,
        y: clientY - rect.top
      };

      document.body.classList.add('jukebox-dragging');
    };

    const onMouseMove = (e) => {
      if (!Jukebox.State.isDragging) return;
      e.preventDefault();

      const clientX = e.touches ? e.touches[0].clientX : e.clientX;
      const clientY = e.touches ? e.touches[0].clientY : e.clientY;

      let newLeft = clientX - Jukebox.State.dragOffset.x;
      let newTop = clientY - Jukebox.State.dragOffset.y;

      // 边界限制
      const wrapperRect = wrapper.getBoundingClientRect();
      const maxLeft = window.innerWidth - wrapperRect.width;
      const maxTop = window.innerHeight - wrapperRect.height;

      newLeft = Math.max(0, Math.min(newLeft, maxLeft));
      newTop = Math.max(0, Math.min(newTop, maxTop));

      wrapper.style.left = newLeft + 'px';
      wrapper.style.top = newTop + 'px';
    };

    const onMouseUp = () => {
      if (!Jukebox.State.isDragging) return;
      Jukebox.State.isDragging = false;
      document.body.classList.remove('jukebox-dragging');
    };

    dragRoot.addEventListener('mousedown', onMouseDown);
    dragRoot.addEventListener('touchstart', onMouseDown, { passive: false });
    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('touchmove', onMouseMove, { passive: false });
    document.addEventListener('mouseup', onMouseUp);
    document.addEventListener('touchend', onMouseUp);
    document.addEventListener('touchcancel', onMouseUp);
    window.addEventListener('blur', onMouseUp);

    // 保存引用，方便 destroy 时清理
    Jukebox.State._dragCleanup = () => {
      dragRoot.removeEventListener('mousedown', onMouseDown);
      dragRoot.removeEventListener('touchstart', onMouseDown);
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('touchmove', onMouseMove);
      document.removeEventListener('mouseup', onMouseUp);
      document.removeEventListener('touchend', onMouseUp);
      document.removeEventListener('touchcancel', onMouseUp);
      window.removeEventListener('blur', onMouseUp);
      Jukebox.State.isDragging = false;
      document.body.classList.remove('jukebox-dragging');
    };
  },

  // 网页浮层 resize：只改变容器宽高，不缩放内部文字和控件。
  bindResize: function(container) {
    const handles = container.querySelectorAll('.jukebox-resize-handle');
    if (!handles.length) return;

    const MIN_WIDTH = 360;
    const MIN_HEIGHT = 300;

    const onPointerDown = (e) => {
      e.preventDefault();
      e.stopPropagation();

      const dir = e.target.dataset.dir;
      if (!dir) return;

      const startX = e.touches ? e.touches[0].clientX : e.clientX;
      const startY = e.touches ? e.touches[0].clientY : e.clientY;
      const startRect = container.getBoundingClientRect();
      const startLeft = startRect.left;
      const startTop = startRect.top;
      const computed = window.getComputedStyle(container);
      const widthExtras = startRect.width - parseFloat(computed.width);
      const heightExtras = startRect.height - parseFloat(computed.height);
      const initialContentWidth = Math.max(0, startRect.width - widthExtras);
      const initialContentHeight = Math.max(0, startRect.height - heightExtras);
      const maxWidth = Math.max(MIN_WIDTH, window.innerWidth - 16);
      const maxHeight = Math.max(MIN_HEIGHT, window.innerHeight - 16);
      let didResize = false;

      document.body.classList.add('jukebox-resizing');

      const onPointerMove = (ev) => {
        const clientX = ev.touches ? ev.touches[0].clientX : ev.clientX;
        const clientY = ev.touches ? ev.touches[0].clientY : ev.clientY;
        const dx = clientX - startX;
        const dy = clientY - startY;

        let newLeft = startLeft;
        let newTop = startTop;
        let newOuterW = startRect.width;
        let newOuterH = startRect.height;

        if (dir.includes('e')) {
          newOuterW = clamp(startRect.width + dx, MIN_WIDTH, maxWidth);
        }
        if (dir.includes('w')) {
          newOuterW = clamp(startRect.width - dx, MIN_WIDTH, maxWidth);
          newLeft = startLeft + (startRect.width - newOuterW);
        }
        if (dir.includes('s')) {
          newOuterH = clamp(startRect.height + dy, MIN_HEIGHT, maxHeight);
        }
        if (dir.includes('n')) {
          newOuterH = clamp(startRect.height - dy, MIN_HEIGHT, maxHeight);
          newTop = startTop + (startRect.height - newOuterH);
        }

        const nextWidth = Math.max(0, newOuterW - widthExtras);
        const nextHeight = Math.max(0, newOuterH - heightExtras);
        container.style.width = nextWidth + 'px';
        container.style.height = nextHeight + 'px';
        if (!didResize) {
          didResize =
            Math.abs(nextWidth - initialContentWidth) > 0.5 ||
            Math.abs(nextHeight - initialContentHeight) > 0.5;
        }

        const wrapper = container.parentElement;
        if (wrapper && (dir.includes('w') || dir.includes('n'))) {
          wrapper.style.left = newLeft + 'px';
          wrapper.style.top = newTop + 'px';
          wrapper.style.bottom = 'auto';
          wrapper.style.right = 'auto';
        }
      };

      const cleanup = () => {
        if (didResize) {
          Jukebox.State.hasCustomWindowSize = true;
          Jukebox.saveWindowSize(container);
        }
        document.body.classList.remove('jukebox-resizing');
        document.removeEventListener('mousemove', onPointerMove);
        document.removeEventListener('touchmove', onPointerMove);
        document.removeEventListener('mouseup', cleanup);
        document.removeEventListener('touchend', cleanup);
        document.removeEventListener('touchcancel', cleanup);
        window.removeEventListener('blur', cleanup);
        Jukebox.State._resizeCleanup = null;
      };

      // 保存清理函数以便 destroy 时调用
      Jukebox.State._resizeCleanup = cleanup;

      document.addEventListener('mousemove', onPointerMove);
      document.addEventListener('touchmove', onPointerMove, { passive: false });
      document.addEventListener('mouseup', cleanup);
      document.addEventListener('touchend', cleanup);
      document.addEventListener('touchcancel', cleanup);
      // 窗口失焦时清理，防止监听泄漏
      window.addEventListener('blur', cleanup);
    };

    handles.forEach(function(handle) {
      handle.addEventListener('mousedown', onPointerDown);
      handle.addEventListener('touchstart', onPointerDown, { passive: false });
    });

    function clamp(value, min, max) {
      return Math.max(min, Math.min(max, value));
    }
  },

  // 管理器面板拖拽功能
  bindPanelDrag: function(panel) {
    if (Jukebox.SongActionManager._panelDragCleanup) {
      Jukebox.SongActionManager._panelDragCleanup();
      Jukebox.SongActionManager._panelDragCleanup = null;
    }

    let isDragging = false;
    let dragOffset = { x: 0, y: 0 };
    const dragRoot = panel.querySelector('.sam-header');
    if (!dragRoot) return;

    const onMouseDown = (e) => {
      // 忽略所有交互元素
      if (e.target.closest('button, input, a, select, textarea, .sam-close-btn, .sam-tab, .sam-checkbox, .sam-resize-handle')) return;

      e.preventDefault();
      const clientX = e.touches ? e.touches[0].clientX : e.clientX;
      const clientY = e.touches ? e.touches[0].clientY : e.clientY;

      const rect = panel.getBoundingClientRect();

      if (!panel.style.left) {
        panel.style.left = rect.left + 'px';
        panel.style.top = rect.top + 'px';
      }

      isDragging = true;
      dragOffset = {
        x: clientX - rect.left,
        y: clientY - rect.top
      };

      document.body.classList.add('sam-panel-dragging');
    };

    const onMouseMove = (e) => {
      if (!isDragging) return;
      e.preventDefault();

      const clientX = e.touches ? e.touches[0].clientX : e.clientX;
      const clientY = e.touches ? e.touches[0].clientY : e.clientY;

      let newLeft = clientX - dragOffset.x;
      let newTop = clientY - dragOffset.y;

      const panelRect = panel.getBoundingClientRect();
      const maxLeft = window.innerWidth - panelRect.width;
      const maxTop = window.innerHeight - panelRect.height;

      newLeft = Math.max(0, Math.min(newLeft, maxLeft));
      newTop = Math.max(0, Math.min(newTop, maxTop));

      panel.style.left = newLeft + 'px';
      panel.style.top = newTop + 'px';
    };

    const onMouseUp = () => {
      if (!isDragging) return;
      isDragging = false;
      document.body.classList.remove('sam-panel-dragging');
    };

    dragRoot.addEventListener('mousedown', onMouseDown);
    dragRoot.addEventListener('touchstart', onMouseDown, { passive: false });
    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('touchmove', onMouseMove, { passive: false });
    document.addEventListener('mouseup', onMouseUp);
    document.addEventListener('touchend', onMouseUp);
    document.addEventListener('touchcancel', onMouseUp);
    window.addEventListener('blur', onMouseUp);

    Jukebox.SongActionManager._panelDragCleanup = () => {
      dragRoot.removeEventListener('mousedown', onMouseDown);
      dragRoot.removeEventListener('touchstart', onMouseDown);
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('touchmove', onMouseMove);
      document.removeEventListener('mouseup', onMouseUp);
      document.removeEventListener('touchend', onMouseUp);
      document.removeEventListener('touchcancel', onMouseUp);
      window.removeEventListener('blur', onMouseUp);
      isDragging = false;
      document.body.classList.remove('sam-panel-dragging');
    };
  },

  // Web 管理器 resize：模拟桌面窗口边缘 resize，不用于 Electron standalone 管理器。
  bindPanelResize: function(panel) {
    if (window.__NEKO_JUKEBOX_MANAGER_STANDALONE__) return;
    const handles = panel.querySelectorAll('.sam-resize-handle');
    if (!handles.length) return;

    const MIN_WIDTH = 420;
    const MIN_HEIGHT = 360;
    const VIEWPORT_MARGIN = 12;

    const onPointerDown = (e) => {
      e.preventDefault();
      e.stopPropagation();

      const dir = e.currentTarget.dataset.dir;
      if (!dir) return;

      const startX = e.touches ? e.touches[0].clientX : e.clientX;
      const startY = e.touches ? e.touches[0].clientY : e.clientY;
      const rect = panel.getBoundingClientRect();
      const startLeft = rect.left;
      const startTop = rect.top;
      const maxWidth = Math.max(MIN_WIDTH, window.innerWidth - VIEWPORT_MARGIN);
      const maxHeight = Math.max(MIN_HEIGHT, window.innerHeight - VIEWPORT_MARGIN);

      if (!panel.style.left) panel.style.left = startLeft + 'px';
      if (!panel.style.top) panel.style.top = startTop + 'px';
      panel.style.right = 'auto';
      panel.style.bottom = 'auto';
      panel.style.maxWidth = `calc(100vw - ${VIEWPORT_MARGIN * 2}px)`;
      panel.style.maxHeight = `calc(100vh - ${VIEWPORT_MARGIN * 2}px)`;

      document.body.classList.add('sam-panel-resizing');

      const onPointerMove = (ev) => {
        ev.preventDefault();
        const clientX = ev.touches ? ev.touches[0].clientX : ev.clientX;
        const clientY = ev.touches ? ev.touches[0].clientY : ev.clientY;
        const dx = clientX - startX;
        const dy = clientY - startY;

        let nextWidth = rect.width;
        let nextHeight = rect.height;
        let nextLeft = startLeft;
        let nextTop = startTop;

        if (dir.includes('e')) {
          nextWidth = clamp(rect.width + dx, MIN_WIDTH, maxWidth);
        }
        if (dir.includes('w')) {
          nextWidth = clamp(rect.width - dx, MIN_WIDTH, maxWidth);
          nextLeft = startLeft + (rect.width - nextWidth);
        }
        if (dir.includes('s')) {
          nextHeight = clamp(rect.height + dy, MIN_HEIGHT, maxHeight);
        }
        if (dir.includes('n')) {
          nextHeight = clamp(rect.height - dy, MIN_HEIGHT, maxHeight);
          nextTop = startTop + (rect.height - nextHeight);
        }

        nextLeft = clamp(nextLeft, 0, Math.max(0, window.innerWidth - nextWidth));
        nextTop = clamp(nextTop, 0, Math.max(0, window.innerHeight - nextHeight));

        panel.style.width = nextWidth + 'px';
        panel.style.height = nextHeight + 'px';
        panel.style.left = nextLeft + 'px';
        panel.style.top = nextTop + 'px';
      };

      const cleanup = () => {
        document.body.classList.remove('sam-panel-resizing');
        document.removeEventListener('mousemove', onPointerMove);
        document.removeEventListener('touchmove', onPointerMove);
        document.removeEventListener('mouseup', cleanup);
        document.removeEventListener('touchend', cleanup);
        document.removeEventListener('touchcancel', cleanup);
        window.removeEventListener('blur', cleanup);
      };

      document.addEventListener('mousemove', onPointerMove);
      document.addEventListener('touchmove', onPointerMove, { passive: false });
      document.addEventListener('mouseup', cleanup);
      document.addEventListener('touchend', cleanup);
      document.addEventListener('touchcancel', cleanup);
      window.addEventListener('blur', cleanup);
    };

    handles.forEach((handle) => {
      handle.addEventListener('mousedown', onPointerDown);
      handle.addEventListener('touchstart', onPointerDown, { passive: false });
    });

    Jukebox.SongActionManager._panelResizeCleanup = () => {
      handles.forEach((handle) => {
        handle.removeEventListener('mousedown', onPointerDown);
        handle.removeEventListener('touchstart', onPointerDown);
      });
      document.body.classList.remove('sam-panel-resizing');
    };

    function clamp(value, min, max) {
      return Math.max(min, Math.min(max, value));
    }
  },

  injectStyles: function() {
    if (Jukebox.State.styleElement) {
      Jukebox.State.styleElement.remove();
    }

    const style = document.createElement('style');
    style.id = 'jukebox-styles';
    Jukebox.State.styleElement = style;

    style.textContent = `
      /* z-index 层级: 悬浮按钮 99999 < 对话框 100000 < 点歌台/管理器 100010 < 导出预览 100020 < tooltip 100030 */
      .jukebox-wrapper {
        position: fixed;
        bottom: 20px;
        right: 20px;
        display: flex;
        align-items: flex-end;
        gap: 10px;
        z-index: 100010;
        pointer-events: none;
      }

      .jukebox-wrapper > * {
        pointer-events: auto;
      }

      html.neko-jukebox-standalone-host,
      html.neko-jukebox-standalone-host body,
      html[data-theme="dark"].neko-jukebox-standalone-host,
      html[data-theme="dark"].neko-jukebox-standalone-host body.neko-jukebox-standalone-page {
        background: ${Jukebox.Config.container.background} !important;
      }

      body.neko-jukebox-standalone-page .jukebox-wrapper {
        position: fixed !important;
        inset: 0 !important;
        bottom: 0 !important;
        right: 0 !important;
      }

      ${Jukebox.SongActionManager.getStyles()}

      .jukebox-container {
        width: ${Jukebox.Config.width};
        height: calc(100vh - 40px);
        max-height: calc(100vh - 40px);
        background: ${Jukebox.Config.container.background};
        border-radius: 12px;
        box-shadow: ${Jukebox.Config.container.boxShadow};
        color: ${Jukebox.Config.container.color};
        border: 1px solid rgba(120, 203, 232, 0.45);
        backdrop-filter: blur(18px) saturate(1.12);
        -webkit-backdrop-filter: blur(18px) saturate(1.12);
        padding: 0;
        display: flex;
        flex-direction: column;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        transition: transform 0.3s ease, opacity 0.3s ease;
        overflow: hidden;
        opacity: 0;
        transform: translateY(20px);
        pointer-events: auto;
        scrollbar-width: none;
        -ms-overflow-style: none;
      }

      .jukebox-container::-webkit-scrollbar {
        display: none;
      }

      .jukebox-container.open {
        opacity: 1;
        transform: translateY(0);
      }

      .jukebox-container.hidden {
        opacity: 0;
        pointer-events: none;
        transform: translateY(20px);
      }

      body.neko-jukebox-standalone-page .jukebox-container,
      body.neko-jukebox-standalone-page .jukebox-container.open,
      body.neko-jukebox-standalone-page .jukebox-container.hidden {
        width: 100% !important;
        height: 100% !important;
        transition: none !important;
        transform: none !important;
      }

      .jukebox-header {
        display: flex;
        flex: 0 0 auto;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 12px;
        padding: 20px 20px 10px;
        border-bottom: ${Jukebox.Config.header.borderBottom};
        cursor: grab;
        user-select: none;
        -webkit-user-select: none;
        position: relative;
      }

      .jukebox-header-left,
      .jukebox-header-drag-fill {
        cursor: grab;
      }

      .jukebox-header-left:active,
      .jukebox-header-drag-fill:active,
      body.jukebox-dragging .jukebox-header,
      body.jukebox-dragging .jukebox-header-left,
      body.jukebox-dragging .jukebox-header-drag-fill {
        cursor: grabbing;
      }

      .jukebox-header-drag-fill {
        align-self: stretch;
        flex: 1 1 24px;
        min-width: 16px;
      }

      /* 专属拖拽层：与按钮是兄弟关系而非父子关系，规避 Chromium
         -webkit-app-region 命中测试缓存 bug。保持标题文字和按钮都在
         overlay 之上，避免独立窗在快速拖动时重新落到不稳定热区。 */
      .jukebox-drag-overlay {
        position: absolute;
        inset: 0;
        z-index: 0;
        border-radius: inherit;
        pointer-events: none;
      }

      .jukebox-header-left,
      .jukebox-header-drag-fill,
      .jukebox-header-buttons,
      .jukebox-content,
      .jukebox-controls-row,
      .jukebox-calibration-section,
      .jukebox-notice,
      .sam-panel {
        position: relative;
        z-index: 1;
      }

      body.jukebox-dragging {
        user-select: none;
        -webkit-user-select: none;
        cursor: grabbing !important;
      }

      body.jukebox-dragging .jukebox-wrapper {
        transition: none !important;
      }

      body.jukebox-dragging .jukebox-container {
        transition: none !important;
        opacity: 0.9;
      }

      body.jukebox-resizing {
        user-select: none;
        -webkit-user-select: none;
      }

      body.jukebox-resizing .jukebox-wrapper {
        transition: none !important;
      }

      body.jukebox-resizing .jukebox-container {
        transition: none !important;
      }

      .jukebox-resize-handle {
        position: absolute;
        z-index: 2;
      }

      /* 四条边 - 6px 宽的透明热区 */
      .jukebox-resize-handle[data-dir="n"]  { top: -3px; left: 8px; right: 8px; height: 6px; cursor: ns-resize; }
      .jukebox-resize-handle[data-dir="s"]  { bottom: -3px; left: 8px; right: 8px; height: 6px; cursor: ns-resize; }
      .jukebox-resize-handle[data-dir="w"]  { left: -3px; top: 8px; bottom: 8px; width: 6px; cursor: ew-resize; }
      .jukebox-resize-handle[data-dir="e"]  { right: -3px; top: 8px; bottom: 8px; width: 6px; cursor: ew-resize; }

      /* 四个角 - 14x14 热区 */
      .jukebox-resize-handle[data-dir="nw"] { top: -3px; left: -3px; width: 14px; height: 14px; cursor: nwse-resize; }
      .jukebox-resize-handle[data-dir="ne"] { top: -3px; right: -3px; width: 14px; height: 14px; cursor: nesw-resize; }
      .jukebox-resize-handle[data-dir="sw"] { bottom: -3px; left: -3px; width: 14px; height: 14px; cursor: nesw-resize; }
      .jukebox-resize-handle[data-dir="se"] { bottom: -3px; right: -3px; width: 14px; height: 14px; cursor: nwse-resize; }

      .jukebox-resize-handle:hover {
        background: rgba(255,255,255,0.15);
      }

      .jukebox-header-left {
        display: flex;
        align-items: center;
        gap: 12px;
        flex: 1 1 auto;
        min-width: 0;
        overflow: hidden;
      }

      .jukebox-header-buttons {
        display: flex;
        gap: 10px;
        align-items: center;
        flex: 0 0 auto;
      }

      .jukebox-header h3 {
        margin: 0;
        font-size: 20px;
        font-weight: 600;
        color: ${Jukebox.Config.container.color};
        flex: 0 0 auto;
        white-space: nowrap;
      }

      .jukebox-status-text {
        font-size: 13px;
        color: ${Jukebox.Config.status.color};
        background: ${Jukebox.Config.status.bg};
        padding: 3px 10px;
        border-radius: 12px;
        box-sizing: border-box;
        display: block;
        flex: 1 1 auto;
        min-width: 0;
        max-width: 100%;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }

      .jukebox-calibration-section {
        margin: 0 20px 12px;
      }

      .jukebox-calibration-toggle {
        background: ${Jukebox.Config.calibration.toggleBg};
        border: none;
        color: ${Jukebox.Config.container.color};
        padding: 8px 16px;
        border-radius: 6px;
        cursor: pointer;
        font-size: 13px;
        font-weight: 500;
        transition: all 0.3s ease;
      }

      .jukebox-calibration-toggle:hover {
        transform: translateY(-1px);
        box-shadow: ${Jukebox.Config.calibration.toggleShadow};
      }

      .jukebox-calibration-panel {
        background: ${Jukebox.Config.calibration.panelBg};
        border-radius: 8px;
        padding: 12px;
        margin-top: 8px;
      }

      .jukebox-calibration-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 10px;
      }

      .jukebox-calibration-title {
        font-size: 14px;
        font-weight: 600;
        color: ${Jukebox.Config.calibration.titleColor};
      }

      .jukebox-calibration-fps {
        font-size: 12px;
        font-weight: 400;
        color: ${Jukebox.Config.calibration.fpsColor};
        margin-left: 8px;
      }

      .jukebox-calibration-close {
        background: ${Jukebox.Config.calibration.closeBg};
        border: none;
        color: ${Jukebox.Config.calibration.closeColor};
        padding: 4px 10px;
        border-radius: 4px;
        cursor: pointer;
        font-size: 12px;
        transition: all 0.2s;
      }

      .jukebox-calibration-close:hover {
        background: ${Jukebox.Config.calibration.closeHoverBg};
        color: ${Jukebox.Config.container.color};
      }

      .jukebox-calibration-controls {
        display: flex;
        align-items: center;
        gap: 8px;
        flex-wrap: wrap;
      }

      .jukebox-calibration-btn {
        background: ${Jukebox.Config.calibration.btnBg};
        border: 1px solid ${Jukebox.Config.calibration.btnBorder};
        color: ${Jukebox.Config.container.color};
        padding: 6px 10px;
        border-radius: 4px;
        cursor: pointer;
        font-size: 13px;
        font-weight: 500;
        transition: all 0.2s;
        min-width: 32px;
      }

      .jukebox-calibration-btn:hover {
        background: ${Jukebox.Config.calibration.btnHoverBg};
        border-color: ${Jukebox.Config.calibration.btnHoverBorder};
      }

      .jukebox-calibration-value {
        font-size: 14px;
        font-weight: 600;
        color: ${Jukebox.Config.calibration.valueColor};
        min-width: 60px;
        text-align: center;
        padding: 0 8px;
      }

      .jukebox-calibration-reset {
        background: ${Jukebox.Config.calibration.resetBg};
        border: 1px solid ${Jukebox.Config.calibration.resetBorder};
        color: ${Jukebox.Config.calibration.resetColor};
        padding: 6px 12px;
        border-radius: 4px;
        cursor: pointer;
        font-size: 12px;
        transition: all 0.2s;
        margin-left: 12px;
      }

      .jukebox-calibration-reset:hover {
        background: ${Jukebox.Config.calibration.resetHoverBg};
        border-color: ${Jukebox.Config.calibration.resetHoverBorder};
      }

      .jukebox-notice {
        background: ${Jukebox.Config.notice.background};
        border: ${Jukebox.Config.notice.border};
        border-radius: 8px;
        padding: 8px 12px;
        margin: 0 20px 12px;
        font-size: 12.5px;
        line-height: 1.6;
        box-sizing: border-box;
        max-width: calc(100% - 40px);
        overflow-wrap: anywhere;
      }

      .jukebox-notice-item {
        padding: 2px 0;
        min-width: 0;
        overflow-wrap: anywhere;
      }

      .jukebox-settings {
        background: rgba(255,255,255,0.58);
        border: 1px solid rgba(99,199,232,0.22);
        color: rgba(45, 78, 104, 0.86);
        font-size: 13px;
        font-weight: 700;
        cursor: pointer;
        padding: 0 12px;
        min-width: 70px;
        height: 34px;
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 5px;
        border-radius: 999px;
        line-height: 1;
        white-space: nowrap;
        transition: background 0.2s ease, color 0.2s ease, transform 0.2s ease, box-shadow 0.2s ease;
      }

      .jukebox-settings:hover {
        background: ${Jukebox.Config.header.btnHoverBg};
        color: rgba(28, 48, 68, 0.94);
        transform: translateY(-1px);
        box-shadow: 0 6px 14px rgba(99, 199, 232, 0.18);
      }

      .jukebox-settings-icon {
        font-size: 15px;
        line-height: 1;
      }

      .jukebox-settings-label {
        line-height: 1;
      }

      .jukebox-pin,
      .jukebox-minimize {
        background: rgba(255,255,255,0.46);
        border: 1px solid rgba(99,199,232,0.16);
        color: rgba(45, 78, 104, 0.8);
        cursor: pointer;
        padding: 0;
        width: 34px;
        height: 34px;
        display: flex;
        align-items: center;
        justify-content: center;
        border-radius: 50%;
        line-height: 1;
        transition: background 0.2s ease, color 0.2s ease, transform 0.2s ease;
      }

      .jukebox-minimize {
        font-size: 24px;
      }

      .jukebox-pin:hover,
      .jukebox-minimize:hover {
        background: ${Jukebox.Config.header.btnHoverBg};
        color: rgba(28, 48, 68, 0.94);
        transform: translateY(-1px);
      }

      .jukebox-pin.is-pinned {
        color: #fff;
        background: ${Jukebox.Config.header.btnHoverBg};
        border-color: rgba(255,255,255,0.34);
      }

      .jukebox-close {
        background: rgba(255,255,255,0.46);
        border: 1px solid rgba(217,75,97,0.16);
        color: rgba(120, 52, 68, 0.76);
        font-size: 25px;
        cursor: pointer;
        padding: 0;
        width: 34px;
        height: 34px;
        display: flex;
        align-items: center;
        justify-content: center;
        border-radius: 50%;
        line-height: 1;
        overflow: visible;
        transition: background 0.2s ease, color 0.2s ease, transform 0.2s ease;
      }

      .jukebox-close:hover {
        background: rgba(217,75,97,0.12);
        color: #b94356;
        transform: translateY(-1px);
      }

      .jukebox-content {
        flex: 1 1 auto;
        overflow-y: auto;
        min-height: 0;
        margin: 0 20px;
        scrollbar-width: none;
        -ms-overflow-style: none;
      }

      .jukebox-content::-webkit-scrollbar {
        display: none;
      }

      .jukebox-table {
        width: 100%;
        border-collapse: separate;
        border-spacing: 0;
        background: ${Jukebox.Config.table.bodyBg};
        border: 1px solid rgba(120, 203, 232, 0.18);
        border-radius: 10px;
        overflow: hidden;
        table-layout: fixed;
        box-shadow: 0 8px 24px rgba(78, 153, 190, 0.12);
      }

      .jukebox-table col.jukebox-col-sequence {
        width: 66px;
      }

      .jukebox-table col.jukebox-col-song {
        width: auto;
      }

      .jukebox-table col.jukebox-col-artist {
        width: 118px;
      }

      .jukebox-table col.jukebox-col-action {
        width: 104px;
      }

      .jukebox-table thead {
        background: ${Jukebox.Config.table.headerBg};
      }

      .jukebox-table th {
        padding: 12px;
        text-align: left;
        font-weight: 600;
        font-size: 14px;
        color: ${Jukebox.Config.table.headerColor};
      }

      .jukebox-table th:not(:last-child) {
        background-image: linear-gradient(
          to bottom,
          transparent 18%,
          rgba(116, 190, 224, 0.22) 18%,
          rgba(116, 190, 224, 0.22) 82%,
          transparent 82%
        );
        background-repeat: no-repeat;
        background-position: right center;
        background-size: 1px 100%;
      }

      .jukebox-table th.jukebox-sequence-th {
        padding-left: 9px;
        padding-right: 7px;
      }

      .jukebox-sequence-header {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 4px;
        white-space: nowrap;
        min-width: 0;
      }

      .jukebox-sort-lock-btn {
        width: 22px;
        height: 22px;
        flex: 0 0 22px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        padding: 0;
        border: 1px solid rgba(99, 199, 232, 0.18);
        border-radius: 999px;
        color: rgba(45, 78, 104, 0.76);
        background: linear-gradient(160deg, rgba(255,255,255,0.86), rgba(232,247,255,0.72));
        box-shadow: 0 3px 8px rgba(78, 153, 190, 0.12);
        cursor: pointer;
        line-height: 1;
        transition: background 0.18s ease, color 0.18s ease, transform 0.18s ease, box-shadow 0.18s ease;
      }

      .jukebox-sort-lock-btn:hover,
      .jukebox-sort-lock-btn.unlocked {
        color: rgba(28, 48, 68, 0.94);
        background: linear-gradient(135deg, rgba(99,199,232,0.24), rgba(255,159,189,0.2));
        box-shadow: 0 5px 12px rgba(99, 199, 232, 0.18);
        transform: translateY(-1px);
      }

      .jukebox-sort-lock-btn:active {
        transform: translateY(0);
      }

      .jukebox-sort-lock-btn svg {
        width: 14px;
        height: 14px;
      }

      .jukebox-table td {
        padding: 12px;
        border-bottom: ${Jukebox.Config.table.rowBorder};
        font-size: 14px;
        vertical-align: middle;
      }

      .jukebox-table td:not(:last-child) {
        background-image: linear-gradient(
          to bottom,
          transparent 20%,
          rgba(116, 190, 224, 0.16) 20%,
          rgba(116, 190, 224, 0.16) 80%,
          transparent 80%
        );
        background-repeat: no-repeat;
        background-position: right center;
        background-size: 1px 100%;
      }

      .jukebox-table th:nth-child(1),
      .jukebox-table td.song-index {
        padding-left: 8px;
        padding-right: 8px;
        text-align: center;
      }

      .song-index-number {
        display: inline-flex;
        min-width: 1.6em;
        justify-content: center;
      }

      .jukebox-table td.song-name,
      .jukebox-table td.song-artist {
        white-space: nowrap;
        overflow: hidden;
        text-overflow: clip;
        scrollbar-width: none;
        -ms-overflow-style: none;
      }

      .jukebox-table td.song-name {
        color: rgba(28, 48, 68, 0.94);
        font-weight: 650;
      }

      .jukebox-table td.song-artist {
        color: rgba(38, 118, 148, 0.82);
        font-weight: 500;
      }

      .jukebox-table td.song-name::-webkit-scrollbar,
      .jukebox-table td.song-artist::-webkit-scrollbar {
        display: none;
      }

      .jukebox-table tbody tr:hover {
        background: ${Jukebox.Config.table.rowHoverBg};
      }

      .jukebox-table tbody tr.jukebox-row-sort-unlocked {
        cursor: grab;
      }

      .jukebox-table tbody tr.jukebox-row-dragging {
        opacity: 0.55;
      }

      .jukebox-table tbody tr.jukebox-row-drop-before td {
        box-shadow: inset 0 2px 0 rgba(0, 60, 100, 0.75);
      }

      .jukebox-table tbody tr.jukebox-row-drop-after td {
        box-shadow: inset 0 -2px 0 rgba(0, 60, 100, 0.75);
      }

      .jukebox-table tbody tr:last-child td {
        border-bottom: none;
      }

      .loading {
        text-align: center;
        padding: 20px;
        color: ${Jukebox.Config.table.loadingColor};
      }

      .jukebox-table td.song-action {
        display: flex;
        gap: 6px;
        align-items: center;
        justify-content: center;
        white-space: nowrap;
        padding-left: 8px;
        padding-right: 8px;
      }

      .play-btn {
        background: ${Jukebox.Config.button.playBg};
        border: none;
        color: ${Jukebox.Config.button.color};
        padding: 6px 8px;
        border-radius: 4px;
        cursor: pointer;
        font-size: 13px;
        transition: all 0.3s;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        position: relative;
      }

      .play-btn:hover {
        background: ${Jukebox.Config.button.playHoverBg};
        transform: scale(1.05);
      }

      .play-btn.playing {
        background: ${Jukebox.Config.button.playingBg};
      }

      .play-btn.playing:hover {
        background: ${Jukebox.Config.button.playingHoverBg};
      }

      .play-btn.pause-btn {
        background: ${Jukebox.Config.button.pauseBg};
        margin-right: 6px;
      }

      .play-btn.pause-btn:hover {
        background: ${Jukebox.Config.button.pauseHoverBg};
      }

      .play-btn.resume-btn {
        background: ${Jukebox.Config.button.resumeBg};
        margin-right: 6px;
      }

      .play-btn.resume-btn:hover {
        background: ${Jukebox.Config.button.resumeHoverBg};
      }

      .play-btn.jukebox-mode-btn {
        background: linear-gradient(160deg, rgba(255,255,255,0.86), rgba(232,247,255,0.72));
        border: 1px solid rgba(99,199,232,0.24);
        color: rgba(38,118,148,0.88);
        padding: 6px 7px;
        box-shadow: 0 3px 8px rgba(78,153,190,0.12);
      }

      .play-btn.jukebox-mode-btn:hover {
        background: linear-gradient(135deg, rgba(99,199,232,0.24), rgba(255,159,189,0.2));
        color: rgba(28,48,68,0.94);
      }

      .play-btn.jukebox-mode-btn.active {
        background: linear-gradient(135deg, rgba(99,199,232,0.92), rgba(255,159,189,0.82));
        border-color: rgba(99,199,232,0.42);
        color: white;
        box-shadow: 0 6px 14px rgba(99,199,232,0.22);
      }

      .play-btn.jukebox-mode-btn.active:hover {
        background: linear-gradient(135deg, rgba(83,188,222,0.98), rgba(255,143,180,0.88));
      }

      .jukebox-controls-row {
        flex: 0 0 auto;
        margin: 15px 20px 20px;
        padding: 10px;
        background: ${Jukebox.Config.progress.containerBg};
        border-radius: 6px;
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 10px;
      }

      .jukebox-progress {
        display: flex;
        align-items: center;
        gap: 8px;
        width: 100%;
        min-width: 0;
        font-size: 13px;
        color: ${Jukebox.Config.progress.textColor};
      }

      .jukebox-playback-controls {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 8px;
        width: 100%;
        min-width: 0;
        flex-wrap: wrap;
      }

      .jukebox-mode-controls,
      .jukebox-transport-controls {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        gap: 5px;
      }

      .jukebox-control-divider {
        width: 1px;
        height: 22px;
        background: rgba(99,199,232,0.24);
        margin: 0 2px;
      }

      .jukebox-transport-btn,
      .jukebox-play-pause-btn {
        min-width: 32px;
        min-height: 30px;
        box-shadow: 0 4px 10px rgba(53,169,201,0.18);
      }

      .jukebox-play-pause-btn {
        min-width: 36px;
      }

      #jukebox-progress-slider {
        flex: 1;
        min-width: 0;
        -webkit-appearance: none;
        appearance: none;
        height: 6px;
        background: ${Jukebox.Config.progress.trackBg};
        border-radius: 3px;
        outline: none;
        cursor: default;
        pointer-events: none;
      }

      #jukebox-progress-slider.seekable {
        cursor: pointer;
        pointer-events: auto;
      }

      #jukebox-progress-slider::-webkit-slider-thumb {
        -webkit-appearance: none;
        appearance: none;
        width: 14px;
        height: 14px;
        background: ${Jukebox.Config.progress.sliderBg};
        border-radius: 50%;
        transition: background 0.3s;
      }

      #jukebox-progress-slider.seekable::-webkit-slider-thumb {
        background: ${Jukebox.Config.progress.sliderSeekableBg};
        cursor: pointer;
      }

      #jukebox-progress-slider::-moz-range-thumb {
        width: 14px;
        height: 14px;
        background: ${Jukebox.Config.progress.sliderBg};
        border-radius: 50%;
        border: none;
      }

      #jukebox-progress-slider.seekable::-moz-range-thumb {
        background: ${Jukebox.Config.progress.sliderSeekableBg};
        cursor: pointer;
      }

      #jukebox-time-current, #jukebox-time-total {
        min-width: 35px;
        text-align: center;
        font-variant-numeric: tabular-nums;
      }

      .jukebox-volume-wrapper {
        position: relative;
        display: flex;
        align-items: center;
      }

      .jukebox-speaker-btn {
        background: linear-gradient(160deg, rgba(255,255,255,0.86), rgba(232,247,255,0.72));
        border: 1px solid rgba(99,199,232,0.24);
        color: ${Jukebox.Config.volume.iconColor};
        cursor: pointer;
        padding: 5px;
        border-radius: 999px;
        transition: background 0.3s, color 0.3s, transform 0.2s ease, box-shadow 0.2s ease;
        display: flex;
        align-items: center;
        justify-content: center;
        box-shadow: 0 3px 8px rgba(78,153,190,0.12);
      }

      .jukebox-speaker-btn:hover {
        background: ${Jukebox.Config.volume.iconHoverBg};
        color: ${Jukebox.Config.volume.iconHoverColor};
        transform: translateY(-1px);
        box-shadow: 0 5px 12px rgba(99,199,232,0.18);
      }

      .jukebox-speaker-btn svg {
        display: block;
        fill: currentColor;
      }

      .jukebox-volume-popup {
        position: absolute;
        bottom: 100%;
        left: 50%;
        transform: translateX(-50%) translateY(10px);
        margin-bottom: 10px;
        background: ${Jukebox.Config.volume.popupBg};
        border-radius: 8px;
        padding: 12px 8px;
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 8px;
        opacity: 0;
        visibility: hidden;
        transition: opacity 0.2s ease, transform 0.2s ease, visibility 0.2s;
        z-index: 100;
        box-shadow: ${Jukebox.Config.volume.popupShadow};
      }

      .jukebox-volume-wrapper:hover .jukebox-volume-popup {
        opacity: 1;
        visibility: visible;
        transform: translateX(-50%) translateY(0);
      }

      #jukebox-volume-slider {
        -webkit-appearance: none;
        appearance: none;
        width: 80px;
        height: 14px;
        background: transparent;
        outline: none;
        cursor: pointer;
        margin: 0;
        transform: rotate(270deg);
        transform-origin: center center;
        position: absolute;
        top: 33px;
        left: -33px;
        z-index: 2;
      }

      .jukebox-volume-slider-container {
        position: relative;
        width: 14px;
        height: 80px;
      }

      .jukebox-volume-track {
        position: absolute;
        width: 4px;
        height: 100%;
        background: ${Jukebox.Config.volume.trackColor};
        border-radius: 2px;
        top: 0;
        left: 5px;
        z-index: 1;
        pointer-events: none;
      }

      #jukebox-volume-slider::-webkit-slider-runnable-track {
        width: 80px;
        height: 4px;
        background: transparent;
      }

      #jukebox-volume-slider::-webkit-slider-thumb {
        -webkit-appearance: none;
        appearance: none;
        width: 14px;
        height: 14px;
        background: ${Jukebox.Config.volume.sliderColor};
        border-radius: 50%;
        cursor: pointer;
        transition: background 0.3s;
        margin-top: -5px;
      }

      #jukebox-volume-slider::-webkit-slider-thumb:hover {
        background: ${Jukebox.Config.volume.sliderHoverColor};
      }

      #jukebox-volume-slider::-moz-range-track {
        width: 80px;
        height: 4px;
        background: transparent;
      }

      #jukebox-volume-slider::-moz-range-thumb {
        width: 14px;
        height: 14px;
        background: ${Jukebox.Config.volume.sliderColor};
        border-radius: 50%;
        cursor: pointer;
        border: none;
        transition: background 0.3s;
      }

      #jukebox-volume-slider::-moz-range-thumb:hover {
        background: ${Jukebox.Config.volume.sliderHoverColor};
      }

      #jukebox-volume-value {
        font-size: 12px;
        color: ${Jukebox.Config.volume.textColor};
        min-width: 35px;
        text-align: center;
      }

      .jukebox-volume-value-editable {
        cursor: pointer;
        padding: 2px 4px;
        border-radius: 4px;
        transition: background 0.2s;
      }

      .jukebox-volume-value-editable:hover {
        background: ${Jukebox.Config.volume.textHoverBg};
      }

      .jukebox-volume-input {
        font-size: 12px;
        color: ${Jukebox.Config.volume.textColor};
        background: ${Jukebox.Config.volume.inputBg};
        border: 1px solid ${Jukebox.Config.volume.inputBorder};
        border-radius: 4px;
        padding: 2px 4px;
        width: 40px;
        text-align: center;
        outline: none;
      }

      .jukebox-volume-input:focus {
        border-color: ${Jukebox.Config.volume.inputFocusBorder};
        background: ${Jukebox.Config.volume.inputFocusBg};
      }

      #jukeboxButton.active {
        background: ${Jukebox.Config.buttonActive.background} !important;
      }

      .jukebox-tooltip {
        position: fixed;
        background: linear-gradient(160deg, rgba(255, 255, 255, 0.96), rgba(232, 247, 255, 0.92));
        color: #24566a;
        padding: 8px 12px;
        border: 1px solid rgba(99, 199, 232, 0.28);
        border-radius: 8px;
        box-shadow: 0 10px 26px rgba(76, 157, 190, 0.18), 0 2px 8px rgba(255, 159, 189, 0.12);
        backdrop-filter: blur(10px);
        -webkit-backdrop-filter: blur(10px);
        font-size: 12px;
        line-height: 1.45;
        pointer-events: none;
        z-index: 100030;
        box-sizing: border-box;
        max-width: min(280px, calc(100vw - 16px));
        overflow-wrap: anywhere;
        text-align: center;
        white-space: pre-line;
        opacity: 0;
        transform: translateY(2px);
        transition: opacity 0.15s ease, transform 0.15s ease;
      }

      .jukebox-tooltip.visible {
        opacity: 1;
        transform: translateY(0);
      }

      [data-theme="dark"] {
        --neko-jukebox-bg: #101722;
      }

      html[data-theme="dark"].neko-jukebox-standalone-host,
      html[data-theme="dark"].neko-jukebox-standalone-host body.neko-jukebox-standalone-page,
      html[data-theme="dark"] body.neko-jukebox-standalone-page {
        background: var(--neko-jukebox-bg) !important;
      }

      [data-theme="dark"] .jukebox-container {
        background: linear-gradient(160deg, rgba(18, 25, 36, 0.96), rgba(26, 38, 52, 0.94));
        color: #e6edf3;
        border-color: rgba(124, 218, 244, 0.24);
        box-shadow: 0 20px 54px rgba(2, 8, 23, 0.54), 0 4px 18px rgba(0, 0, 0, 0.28);
      }

      [data-theme="dark"] .jukebox-header {
        border-bottom-color: rgba(124, 218, 244, 0.16);
      }

      [data-theme="dark"] .jukebox-header h3 {
        color: #f8fafc;
      }

      [data-theme="dark"] .jukebox-status-text {
        color: #9bdcf5;
        background: rgba(14, 165, 233, 0.12);
      }

      [data-theme="dark"] .jukebox-notice {
        color: #cbd5e1;
        background: rgba(15, 23, 42, 0.54);
        border-color: rgba(124, 218, 244, 0.18);
      }

      [data-theme="dark"] .jukebox-settings,
      [data-theme="dark"] .jukebox-minimize,
      [data-theme="dark"] .jukebox-sort-lock-btn,
      [data-theme="dark"] .jukebox-speaker-btn,
      [data-theme="dark"] .play-btn.jukebox-mode-btn {
        color: #b7e8f8;
        background: linear-gradient(160deg, rgba(30, 41, 59, 0.9), rgba(15, 23, 42, 0.82));
        border-color: rgba(124, 218, 244, 0.22);
        box-shadow: 0 4px 12px rgba(2, 8, 23, 0.26);
      }

      [data-theme="dark"] .jukebox-settings:hover,
      [data-theme="dark"] .jukebox-pin:hover,
      [data-theme="dark"] .jukebox-pin.is-pinned,
      [data-theme="dark"] .jukebox-minimize:hover,
      [data-theme="dark"] .jukebox-sort-lock-btn:hover,
      [data-theme="dark"] .jukebox-sort-lock-btn.unlocked,
      [data-theme="dark"] .jukebox-speaker-btn:hover,
      [data-theme="dark"] .play-btn.jukebox-mode-btn:hover {
        color: #f8fafc;
        background: linear-gradient(135deg, rgba(14, 165, 233, 0.28), rgba(244, 114, 182, 0.18));
        border-color: rgba(124, 218, 244, 0.36);
        box-shadow: 0 6px 16px rgba(14, 165, 233, 0.16);
      }

      [data-theme="dark"] .jukebox-close {
        color: #fca5a5;
        background: rgba(30, 41, 59, 0.82);
        border-color: rgba(248, 113, 113, 0.2);
      }

      [data-theme="dark"] .jukebox-close:hover {
        color: #fff1f2;
        background: rgba(220, 38, 38, 0.22);
        border-color: rgba(248, 113, 113, 0.34);
      }

      [data-theme="dark"] .play-btn.jukebox-mode-btn.active {
        color: #ffffff;
        background: linear-gradient(135deg, rgba(14, 165, 233, 0.88), rgba(244, 114, 182, 0.62));
        border-color: rgba(125, 211, 252, 0.48);
        box-shadow: 0 8px 20px rgba(14, 165, 233, 0.24);
      }

      [data-theme="dark"] .play-btn.jukebox-mode-btn.active:hover {
        background: linear-gradient(135deg, rgba(56, 189, 248, 0.92), rgba(251, 113, 133, 0.68));
      }

      [data-theme="dark"] .speaker-icon,
      [data-theme="dark"] .speaker-muted-icon {
        filter: none;
      }

      [data-theme="dark"] .jukebox-table {
        background: rgba(15, 23, 42, 0.48);
        border-color: rgba(124, 218, 244, 0.18);
        box-shadow: 0 10px 28px rgba(2, 8, 23, 0.28);
      }

      [data-theme="dark"] .jukebox-table thead {
        background: rgba(30, 41, 59, 0.66);
      }

      [data-theme="dark"] .jukebox-table th {
        color: #c8e7f5;
      }

      [data-theme="dark"] .jukebox-table th:not(:last-child) {
        background-image: linear-gradient(
          to bottom,
          transparent 18%,
          rgba(124, 218, 244, 0.16) 18%,
          rgba(124, 218, 244, 0.16) 82%,
          transparent 82%
        );
      }

      [data-theme="dark"] .jukebox-table td {
        border-bottom-color: rgba(124, 218, 244, 0.12);
      }

      [data-theme="dark"] .jukebox-table td:not(:last-child) {
        background-image: linear-gradient(
          to bottom,
          transparent 20%,
          rgba(124, 218, 244, 0.1) 20%,
          rgba(124, 218, 244, 0.1) 80%,
          transparent 80%
        );
      }

      [data-theme="dark"] .jukebox-table td.song-name {
        color: #f1f5f9;
      }

      [data-theme="dark"] .jukebox-table td.song-artist,
      [data-theme="dark"] .jukebox-container .loading {
        color: #9bdcf5;
      }

      [data-theme="dark"] .jukebox-table tbody tr:hover {
        background: rgba(30, 41, 59, 0.78);
      }

      [data-theme="dark"] .jukebox-table tbody tr.jukebox-row-dragging {
        background: rgba(14, 165, 233, 0.16);
      }

      [data-theme="dark"] .jukebox-table tbody tr.jukebox-row-drop-before td {
        box-shadow: inset 0 2px 0 rgba(125, 211, 252, 0.9);
      }

      [data-theme="dark"] .jukebox-table tbody tr.jukebox-row-drop-after td {
        box-shadow: inset 0 -2px 0 rgba(125, 211, 252, 0.9);
      }

      [data-theme="dark"] .jukebox-controls-row {
        color: #cbd5e1;
        background: rgba(15, 23, 42, 0.58);
        border: 1px solid rgba(124, 218, 244, 0.14);
      }

      [data-theme="dark"] .jukebox-control-divider {
        background: rgba(124, 218, 244, 0.18);
      }

      [data-theme="dark"] .jukebox-progress {
        color: #b8c7d9;
      }

      [data-theme="dark"] #jukebox-progress-slider {
        background: rgba(148, 163, 184, 0.22);
      }

      [data-theme="dark"] #jukebox-progress-slider::-webkit-slider-thumb,
      [data-theme="dark"] #jukebox-volume-slider::-webkit-slider-thumb {
        background: #38bdf8;
      }

      [data-theme="dark"] #jukebox-progress-slider.seekable::-webkit-slider-thumb,
      [data-theme="dark"] #jukebox-volume-slider::-webkit-slider-thumb:hover {
        background: #7dd3fc;
      }

      [data-theme="dark"] #jukebox-progress-slider::-moz-range-thumb,
      [data-theme="dark"] #jukebox-volume-slider::-moz-range-thumb {
        background: #38bdf8;
      }

      [data-theme="dark"] #jukebox-progress-slider.seekable::-moz-range-thumb,
      [data-theme="dark"] #jukebox-volume-slider::-moz-range-thumb:hover {
        background: #7dd3fc;
      }

      [data-theme="dark"] .jukebox-volume-popup {
        background: rgba(15, 23, 42, 0.96);
        border: 1px solid rgba(124, 218, 244, 0.18);
        box-shadow: 0 14px 34px rgba(2, 8, 23, 0.46);
      }

      [data-theme="dark"] .jukebox-volume-track {
        background: rgba(148, 163, 184, 0.28);
      }

      [data-theme="dark"] #jukebox-volume-value,
      [data-theme="dark"] .jukebox-volume-input {
        color: #dcebf5;
      }

      [data-theme="dark"] .jukebox-volume-value-editable:hover {
        background: rgba(14, 165, 233, 0.16);
      }

      [data-theme="dark"] .jukebox-volume-input {
        background: rgba(30, 41, 59, 0.9);
        border-color: rgba(124, 218, 244, 0.28);
      }

      [data-theme="dark"] .jukebox-volume-input:focus {
        background: rgba(15, 23, 42, 0.96);
        border-color: #38bdf8;
      }

      [data-theme="dark"] .jukebox-calibration-toggle {
        color: #f8fafc;
        background: linear-gradient(135deg, #0ea5e9, #38bdf8);
      }

      [data-theme="dark"] .jukebox-calibration-panel {
        background: rgba(15, 23, 42, 0.66);
        border: 1px solid rgba(124, 218, 244, 0.14);
      }

      [data-theme="dark"] .jukebox-calibration-title,
      [data-theme="dark"] .jukebox-calibration-value {
        color: #f8fafc;
      }

      [data-theme="dark"] .jukebox-calibration-fps {
        color: #94a3b8;
      }

      [data-theme="dark"] .jukebox-calibration-close,
      [data-theme="dark"] .jukebox-calibration-btn {
        color: #dbeafe;
        background: rgba(30, 41, 59, 0.9);
        border-color: rgba(124, 218, 244, 0.2);
      }

      [data-theme="dark"] .jukebox-calibration-close:hover,
      [data-theme="dark"] .jukebox-calibration-btn:hover {
        color: #f8fafc;
        background: rgba(14, 165, 233, 0.2);
        border-color: rgba(124, 218, 244, 0.36);
      }

      [data-theme="dark"] .jukebox-tooltip,
      [data-theme="dark"] .sam-danger-tooltip {
        color: #e6edf3;
        background: linear-gradient(160deg, rgba(15, 23, 42, 0.96), rgba(30, 41, 59, 0.92));
        border-color: rgba(124, 218, 244, 0.2);
        box-shadow: 0 12px 30px rgba(2, 8, 23, 0.42);
      }

      /* Keep this SongActionManager dark palette in sync with templates/jukebox_manager.html. */
      [data-theme="dark"] .jukebox-sam-panel {
        color: #e6edf3;
        background: linear-gradient(160deg, rgba(18, 25, 36, 0.97), rgba(26, 38, 52, 0.94));
        border-color: rgba(124, 218, 244, 0.24);
        box-shadow: 0 20px 54px rgba(2, 8, 23, 0.54), 0 4px 18px rgba(0, 0, 0, 0.28);
      }

      [data-theme="dark"] .sam-header {
        border-bottom-color: rgba(124, 218, 244, 0.16);
      }

      [data-theme="dark"] .sam-title,
      [data-theme="dark"] .sam-import-header h4,
      [data-theme="dark"] .sam-item-name,
      [data-theme="dark"] .sam-binding-item-name,
      [data-theme="dark"] .sam-danger-modal h3 {
        color: #f8fafc;
      }

      [data-theme="dark"] .sam-close-btn,
      [data-theme="dark"] .sam-tab,
      [data-theme="dark"] .sam-btn,
      [data-theme="dark"] .sam-add-binding-btn,
      [data-theme="dark"] .sam-visibility-btn {
        color: #b7e8f8;
        background: linear-gradient(160deg, rgba(30, 41, 59, 0.9), rgba(15, 23, 42, 0.82));
        border-color: rgba(124, 218, 244, 0.22);
      }

      [data-theme="dark"] .sam-close-btn:hover,
      [data-theme="dark"] .sam-tab:hover,
      [data-theme="dark"] .sam-btn:hover,
      [data-theme="dark"] .sam-add-binding-btn:hover,
      [data-theme="dark"] .sam-visibility-btn:hover {
        color: #f8fafc;
        background: linear-gradient(135deg, rgba(14, 165, 233, 0.28), rgba(244, 114, 182, 0.18));
        border-color: rgba(124, 218, 244, 0.36);
      }

      [data-theme="dark"] .sam-tab.active {
        color: #ffffff;
        background: linear-gradient(135deg, rgba(14, 165, 233, 0.88), rgba(244, 114, 182, 0.62));
        box-shadow: 0 8px 20px rgba(14, 165, 233, 0.22);
      }

      [data-theme="dark"] .sam-file-drop-zone,
      [data-theme="dark"] .sam-bindings-list {
        border-color: rgba(124, 218, 244, 0.22);
      }

      [data-theme="dark"] .sam-file-drop-zone:hover,
      [data-theme="dark"] .sam-file-drop-zone.drag-over,
      [data-theme="dark"] .sam-bindings-list.drag-over {
        background: rgba(14, 165, 233, 0.12);
        border-color: rgba(125, 211, 252, 0.48);
      }

      [data-theme="dark"] .sam-drop-hint,
      [data-theme="dark"] .sam-empty,
      [data-theme="dark"] .sam-import-hint,
      [data-theme="dark"] .sam-unified-hint,
      [data-theme="dark"] .sam-danger-modal-detail {
        color: #94a3b8;
        background: rgba(15, 23, 42, 0.58);
        border-color: rgba(124, 218, 244, 0.18);
      }

      [data-theme="dark"] .sam-item,
      [data-theme="dark"] .sam-binding-item {
        background: rgba(15, 23, 42, 0.58);
        border-color: rgba(124, 218, 244, 0.16);
        box-shadow: 0 8px 20px rgba(2, 8, 23, 0.24);
      }

      [data-theme="dark"] .sam-item:hover,
      [data-theme="dark"] .sam-binding-item:hover,
      [data-theme="dark"] .sam-add-hint:hover,
      [data-theme="dark"] .sam-import-item:hover,
      [data-theme="dark"] .sam-item-name:hover,
      [data-theme="dark"] .sam-item-artist:hover {
        background: rgba(30, 41, 59, 0.78);
      }

      [data-theme="dark"] .sam-item-name:focus,
      [data-theme="dark"] .sam-item-artist:focus {
        background: rgba(14, 165, 233, 0.16);
      }

      [data-theme="dark"] .sam-item-artist,
      [data-theme="dark"] .sam-import-item-name,
      [data-theme="dark"] .sam-checkbox,
      [data-theme="dark"] .sam-bindings-section h4,
      [data-theme="dark"] .sam-item-format,
      [data-theme="dark"] .sam-binding-item-index {
        color: #b8c7d9;
      }

      [data-theme="dark"] .sam-item-format,
      [data-theme="dark"] .sam-binding-item-index,
      [data-theme="dark"] .sam-binding-tag,
      [data-theme="dark"] .sam-binding-count {
        background: rgba(30, 41, 59, 0.76);
        border-color: rgba(124, 218, 244, 0.18);
      }

      [data-theme="dark"] .sam-binding-tag {
        color: #b7e8f8;
      }

      [data-theme="dark"] .sam-binding-count {
        color: #f8fafc;
      }

      [data-theme="dark"] .sam-binding-item-tags {
        border-top-color: rgba(124, 218, 244, 0.14);
      }

      [data-theme="dark"] .sam-add-binding-input,
      [data-theme="dark"] .jukebox-sam-panel input:not([type="checkbox"]):not([type="file"]),
      [data-theme="dark"] .jukebox-sam-panel textarea,
      [data-theme="dark"] .jukebox-sam-panel select {
        color: #e6edf3;
        background: rgba(15, 23, 42, 0.78);
        border-color: rgba(124, 218, 244, 0.24);
      }

      [data-theme="dark"] .sam-add-binding-input:focus,
      [data-theme="dark"] .jukebox-sam-panel input:not([type="checkbox"]):not([type="file"]):focus,
      [data-theme="dark"] .jukebox-sam-panel textarea:focus,
      [data-theme="dark"] .jukebox-sam-panel select:focus {
        border-color: #38bdf8;
        box-shadow: 0 0 0 3px rgba(14, 165, 233, 0.18);
      }

      [data-theme="dark"] .jukebox-sam-panel input[type="checkbox"] {
        accent-color: #38bdf8;
      }

      [data-theme="dark"] .sam-import-container {
        background: rgba(15, 23, 42, 0.52);
        border-color: rgba(124, 218, 244, 0.18);
      }

      [data-theme="dark"] .sam-import-header,
      [data-theme="dark"] .sam-list-header,
      [data-theme="dark"] .sam-footer,
      [data-theme="dark"] .sam-import-footer {
        background: rgba(15, 23, 42, 0.62);
        border-color: rgba(124, 218, 244, 0.14);
      }

      [data-theme="dark"] .sam-selection-info,
      [data-theme="dark"] .sam-footer,
      [data-theme="dark"] .sam-danger-modal p,
      [data-theme="dark"] .sam-danger-modal-detail {
        color: #b8c7d9;
      }

      [data-theme="dark"] .sam-item-selected {
        background: rgba(14, 165, 233, 0.18) !important;
        border-left-color: #38bdf8;
      }

      [data-theme="dark"] .sam-visibility-btn.hidden {
        color: #f9a8d4;
        background: rgba(244, 114, 182, 0.14);
        border-color: rgba(244, 114, 182, 0.28);
      }

      [data-theme="dark"] .sam-delete-btn,
      [data-theme="dark"] .sam-btn-danger,
      [data-theme="dark"] .sam-danger-modal-confirm {
        color: #fca5a5;
        background: rgba(220, 38, 38, 0.14);
        border-color: rgba(248, 113, 113, 0.28);
      }

      [data-theme="dark"] .sam-delete-btn:hover,
      [data-theme="dark"] .sam-btn-danger:hover,
      [data-theme="dark"] .sam-danger-modal-confirm:hover {
        color: #fff1f2;
        background: rgba(220, 38, 38, 0.24);
      }

      [data-theme="dark"] .sam-danger-modal-backdrop {
        background: rgba(2, 8, 23, 0.68);
      }

      [data-theme="dark"] .sam-danger-modal {
        color: #e6edf3;
        background: linear-gradient(160deg, rgba(15, 23, 42, 0.98), rgba(30, 41, 59, 0.94));
        border-color: rgba(124, 218, 244, 0.22);
        box-shadow: 0 20px 54px rgba(2, 8, 23, 0.58);
      }

      [data-theme="dark"] .sam-danger-modal-cancel {
        color: #dbeafe;
        background: rgba(30, 41, 59, 0.92);
        border-color: rgba(124, 218, 244, 0.2);
      }
    `;

    document.head.appendChild(style);

    setTimeout(() => {
      if (Jukebox.State.container) {
        const container = Jukebox.State.container.querySelector('.jukebox-container');
        if (container) {
          container.classList.add('open');
        }
      }
    }, 10);
  },

});
