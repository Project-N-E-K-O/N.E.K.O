window.Jukebox = {
  Config: {
    songsJsonPath: '/static/jukebox/songs.json',
    themeColor: '#87CEEB',
    secondaryColor: '#d9edf5ff',
    width: '500px'
  },
  
  State: {
    songs: [],
    currentSong: null,
    isPlaying: false,
    isVMDPlaying: false,
    player: null,
    audioElement: null,
    mp3EndedListenerAdded: false,
    isOpen: false,
    isHidden: false,
    container: null,
    styleElement: null,
    observer: null
  },
  
  init: function() {
    console.log('[Jukebox]', window.t('Jukebox.initialized', '初始化点歌台...'));
    
    window.Jukebox_playSong = Jukebox.playSong;
    window.Jukebox_close = Jukebox.close;
    window.Jukebox_hide = Jukebox.hide;
    window.Jukebox_updateVolume = Jukebox.updateVolume;
    window.Jukebox_logVolumeChange = Jukebox.logVolumeChange;
    
    Jukebox.setupButton();
    Jukebox.setupCloseListener();
  },
  
  setupButton: function() {
    const jukeboxButton = document.getElementById('jukeboxButton');
    if (!jukeboxButton) {
      console.warn('[Jukebox]', window.t('Jukebox.btnNotFound', '点歌台按钮不存在，等待加载...'));
      setTimeout(Jukebox.setupButton, 500);
      return;
    }
    
    jukeboxButton.addEventListener('click', Jukebox.toggle);
    console.log('[Jukebox]', window.t('Jukebox.btnBound', '点歌台按钮已绑定'));
  },
  
  setupCloseListener: function() {
    if (Jukebox.State.observer) return;

    const toggleChatBtn = document.getElementById('toggle-chat-btn');
    if (toggleChatBtn) {
      toggleChatBtn.addEventListener('click', () => {
        console.log('[Jukebox]', window.t('Jukebox.minimizeDetected', '检测到对话框最小化，销毁点歌台'));
        Jukebox.destroy();
      });
      console.log('[Jukebox]', window.t('Jukebox.minimizeListenerSet', '最小化按钮监听器已设置'));
    } else {
      console.warn('[Jukebox]', window.t('Jukebox.minimizeBtnNotFound', '最小化按钮不存在，等待加载...'));
      setTimeout(Jukebox.setupCloseListener, 500);
      return;
    }
    
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
    
    console.log('[Jukebox]', window.t('Jukebox.closeListenerSet', '关闭监听器已设置'));
  },
  
  toggle: function() {
    if (Jukebox.State.isHidden) {
      Jukebox.show();
    } else if (Jukebox.State.isOpen) {
      Jukebox.hide();
    } else {
      Jukebox.open();
    }
  },
  
  open: function() {
    if (Jukebox.State.isOpen) return;
    
    Jukebox.buildUI();
    
    requestAnimationFrame(() => {
      setTimeout(() => {
        console.log('[Jukebox] 准备加载歌曲，检查容器...');
        const tbody = document.getElementById('jukebox-song-list');
        console.log('[Jukebox] 歌曲列表容器:', tbody);
        Jukebox.loadSongs();
        Jukebox.initPlayer();
        Jukebox.initVolumeSlider();
      }, 100);
    });
    
    Jukebox.State.isOpen = true;
    
    const jukeboxButton = document.getElementById('jukeboxButton');
    if (jukeboxButton) {
      jukeboxButton.classList.add('active');
    }
    
    console.log('[Jukebox] 点歌台已打开');
  },
  
  hide: function() {
    if (!Jukebox.State.container) return;
    
    Jukebox.State.container.classList.remove('open');
    Jukebox.State.container.classList.add('hidden');
    Jukebox.State.isHidden = true;
    
    const jukeboxButton = document.getElementById('jukeboxButton');
    if (jukeboxButton) {
      jukeboxButton.classList.remove('active');
    }
    
    console.log('[Jukebox] 点歌台已隐藏');
  },
  
  show: function() {
    if (!Jukebox.State.container) return;
    
    Jukebox.State.container.classList.remove('hidden');
    Jukebox.State.container.classList.add('open');
    Jukebox.State.isHidden = false;
    
    const jukeboxButton = document.getElementById('jukeboxButton');
    if (jukeboxButton) {
      jukeboxButton.classList.add('active');
    }
    
    console.log('[Jukebox] 点歌台已显示');
  },
  
  close: function() {
    Jukebox.stopPlayback();
    
    if (Jukebox.State.container) {
      Jukebox.State.container.remove();
      Jukebox.State.container = null;
    }
    
    if (Jukebox.State.styleElement) {
      Jukebox.State.styleElement.remove();
      Jukebox.State.styleElement = null;
    }
    
    Jukebox.State.isOpen = false;
    Jukebox.State.isHidden = false;
    
    const jukeboxButton = document.getElementById('jukeboxButton');
    if (jukeboxButton) {
      jukeboxButton.classList.remove('active');
    }
    
    console.log('[Jukebox] 点歌台已关闭');
  },
  
  destroy: function() {
    Jukebox.stopPlayback();
    
    if (Jukebox.State.container) {
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
    Jukebox.State.songs = [];
    
    console.log('[Jukebox] 点歌台已销毁');
  },
  
  buildUI: function() {
    const jukeboxContainer = document.createElement('div');
    jukeboxContainer.className = 'jukebox-container';
    jukeboxContainer.innerHTML = `
      <div class="jukebox-header">
        <h3>${window.t('Jukebox.title', '点歌台')}</h3>
        <div class="jukebox-header-buttons">
          <button class="jukebox-minimize" onclick="Jukebox_hide()" title="${window.t('Jukebox.minimize', '最小化')}">−</button>
          <button class="jukebox-close" onclick="Jukebox_close()" title="${window.t('Jukebox.close', '关闭')}">×</button>
        </div>
      </div>
      <div class="jukebox-content">
        <table class="jukebox-table">
          <thead>
            <tr>
              <th>${window.t('Jukebox.sequence', '序号')}</th>
              <th>${window.t('Jukebox.song', '歌曲')}</th>
              <th>${window.t('Jukebox.artist', '艺术家')}</th>
              <th>${window.t('Jukebox.duration', '时长')}</th>
              <th>${window.t('Jukebox.action', '操作')}</th>
            </tr>
          </thead>
          <tbody id="jukebox-song-list">
            <tr>
              <td colspan="5" class="loading">${window.t('Jukebox.loading', '加载中...')}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="jukebox-volume-control">
        <label for="jukebox-volume-slider">${window.t('Jukebox.volume', '音量')}</label>
        <input type="range" id="jukebox-volume-slider" min="0" max="1" step="0.01" value="1" oninput="Jukebox_updateVolume(this.value)" onchange="Jukebox_logVolumeChange(this.value)">
        <span id="jukebox-volume-value">100%</span>
      </div>
      <div class="jukebox-status">
        <span id="jukebox-status-text">${window.t('Jukebox.ready', '准备就绪')}</span>
      </div>
    `;
    
    document.body.appendChild(jukeboxContainer);
    Jukebox.State.container = jukeboxContainer;
    
    Jukebox.injectStyles();
  },
  
  injectStyles: function() {
    if (Jukebox.State.styleElement) {
      Jukebox.State.styleElement.remove();
    }
    
    const style = document.createElement('style');
    style.id = 'jukebox-styles';
    Jukebox.State.styleElement = style;
    
    style.textContent = `
      .jukebox-container {
        position: fixed;
        bottom: 20px;
        right: 20px;
        width: ${Jukebox.Config.width};
        max-height: 500px;
        background: #87CEEB;
        border-radius: 12px;
        box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
        color: white;
        padding: 20px;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        z-index: 9999;
        transition: transform 0.3s ease, opacity 0.3s ease;
        overflow-y: auto;
        opacity: 0;
        transform: translateY(20px);
        pointer-events: auto;
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
      
      .jukebox-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 20px;
        padding-bottom: 10px;
        border-bottom: 1px solid rgba(255, 255, 255, 0.2);
      }
      
      .jukebox-header-buttons {
        display: flex;
        gap: 10px;
        align-items: center;
      }
      
      .jukebox-header h3 {
        margin: 0;
        font-size: 20px;
        font-weight: 600;
      }
      
      .jukebox-minimize {
        background: none;
        border: none;
        color: white;
        font-size: 24px;
        cursor: pointer;
        padding: 0;
        width: 30px;
        height: 30px;
        display: flex;
        align-items: center;
        justify-content: center;
        border-radius: 4px;
        transition: background 0.3s;
      }
      
      .jukebox-minimize:hover {
        background: rgba(255, 255, 255, 0.2);
      }
      
      .jukebox-close {
        background: none;
        border: none;
        color: white;
        font-size: 24px;
        cursor: pointer;
        padding: 0;
        width: 30px;
        height: 30px;
        display: flex;
        align-items: center;
        justify-content: center;
        border-radius: 4px;
        transition: background 0.3s;
      }
      
      .jukebox-close:hover {
        background: rgba(255, 255, 255, 0.2);
      }
      
      .jukebox-content {
        flex: 1;
        overflow-y: auto;
        min-height: 0;
      }
      
      .jukebox-table {
        width: 100%;
        border-collapse: collapse;
        background: rgba(255, 255, 255, 0.1);
        border-radius: 8px;
        overflow: hidden;
      }
      
      .jukebox-table thead {
        background: rgba(0, 0, 0, 0.2);
      }
      
      .jukebox-table th {
        padding: 12px;
        text-align: left;
        font-weight: 600;
        font-size: 14px;
        color: rgba(255, 255, 255, 0.9);
      }
      
      .jukebox-table td {
        padding: 12px;
        border-bottom: 1px solid rgba(255, 255, 255, 0.1);
        font-size: 14px;
      }
      
      .jukebox-table tbody tr:hover {
        background: rgba(255, 255, 255, 0.15);
      }
      
      .jukebox-table tbody tr:last-child td {
        border-bottom: none;
      }
      
      .loading {
        text-align: center;
        padding: 20px;
        color: rgba(255, 255, 255, 0.7);
      }
      
      .play-btn {
        background: #4CAF50;
        border: none;
        color: white;
        padding: 6px 14px;
        border-radius: 4px;
        cursor: pointer;
        font-size: 13px;
        transition: all 0.3s;
      }
      
      .play-btn:hover {
        background: #45a049;
        transform: scale(1.05);
      }
      
      .play-btn.playing {
        background: #f44336;
      }
      
      .play-btn.playing:hover {
        background: #da190b;
      }
      
      .jukebox-status {
        margin-top: 15px;
        padding: 10px;
        background: rgba(0, 0, 0, 0.2);
        border-radius: 6px;
        font-size: 14px;
        color: rgba(255, 255, 255, 0.8);
      }
      
      .jukebox-volume-control {
        margin-top: 15px;
        padding: 10px;
        background: rgba(0, 0, 0, 0.2);
        border-radius: 6px;
        display: flex;
        align-items: center;
        gap: 10px;
        font-size: 14px;
        color: rgba(255, 255, 255, 0.9);
      }
      
      .jukebox-volume-control label {
        min-width: 40px;
      }
      
      #jukebox-volume-slider {
        flex: 1;
        -webkit-appearance: none;
        appearance: none;
        height: 6px;
        background: rgba(255, 255, 255, 0.3);
        border-radius: 3px;
        outline: none;
        cursor: pointer;
      }
      
      #jukebox-volume-slider::-webkit-slider-thumb {
        -webkit-appearance: none;
        appearance: none;
        width: 16px;
        height: 16px;
        background: #4CAF50;
        border-radius: 50%;
        cursor: pointer;
        transition: background 0.3s;
      }
      
      #jukebox-volume-slider::-webkit-slider-thumb:hover {
        background: #45a049;
      }
      
      #jukebox-volume-slider::-moz-range-thumb {
        width: 16px;
        height: 16px;
        background: #4CAF50;
        border-radius: 50%;
        cursor: pointer;
        border: none;
        transition: background 0.3s;
      }
      
      #jukebox-volume-slider::-moz-range-thumb:hover {
        background: #45a049;
      }
      
      #jukebox-volume-value {
        min-width: 45px;
        text-align: right;
        font-size: 13px;
      }
      
      #jukeboxButton.active {
        background: rgba(30, 60, 114, 0.3) !important;
      }
    `;
    
    document.head.appendChild(style);
    
    setTimeout(() => {
      if (Jukebox.State.container) {
        Jukebox.State.container.classList.add('open');
      }
    }, 10);
  },
  
  loadSongs: async function() {
    try {
      const response = await fetch(Jukebox.Config.songsJsonPath);
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      
      const data = await response.json();
      Jukebox.State.songs = data.songs || [];
      
      console.log('[Jukebox]', window.t('Jukebox.songsLoaded', '歌曲列表已加载'), Jukebox.State.songs.length, '首歌曲');
      
      Jukebox.renderList();
      
    } catch (error) {
      console.error('[Jukebox]', window.t('Jukebox.loadFailed', '加载歌曲列表失败'), error);
      Jukebox.showError(window.t('Jukebox.loadFailed', '加载歌曲列表失败') + ': ' + error.message);
    }
  },
  
  renderList: function() {
    const tbody = document.getElementById('jukebox-song-list');
    if (!tbody) {
      console.error('[Jukebox]', window.t('Jukebox.listContainerNotFound', '歌曲列表容器不存在'));
      return;
    }
    
    if (Jukebox.State.songs.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" class="loading">' + window.t('Jukebox.noSongs', '暂无歌曲') + '</td></tr>';
      return;
    }
    
    tbody.innerHTML = Jukebox.State.songs.map((song, index) => `
      <tr data-song-id="${song.id}">
        <td>${index + 1}</td>
        <td>${Jukebox.escapeHtml(song.name)}</td>
        <td>${Jukebox.escapeHtml(song.artist)}</td>
        <td>${Jukebox.formatDuration(song.duration)}</td>
        <td>
          <button class="play-btn" onclick="Jukebox_playSong('${song.id}')">
            ${window.t('Jukebox.play', '播放')}
          </button>
        </td>
      </tr>
    `).join('');
    
    console.log('[Jukebox]', window.t('Jukebox.songsRendered', '歌曲列表已渲染'));
  },
  
  playSong: async function(songId) {
    const song = Jukebox.State.songs.find(s => s.id === songId);
    if (!song) {
      console.error('[Jukebox]', window.t('Jukebox.notFound', '找不到歌曲'), songId);
      return;
    }
    
    if (Jukebox.State.currentSong && Jukebox.State.currentSong.id === songId && Jukebox.State.isPlaying) {
      console.log('[Jukebox] 停止当前播放的歌曲:', song.name);
      Jukebox.stopPlayback();
      return;
    }
    
    console.log('[Jukebox] 播放歌曲:', song.name);
    
    Jukebox.stopPlayback();
    
    try {
      await Jukebox.playAudio(song);
      
      if (Jukebox.isMMDModel() && song.vmd) {
        await Jukebox.playVMD(song.vmd);
      }
      
      Jukebox.State.currentSong = song;
      Jukebox.State.isPlaying = true;
      
      Jukebox.updatePlayingStatus(song);
    } catch (error) {
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
    
    if (!song.audio.endsWith('.mp3')) {
      console.error('[Jukebox]', window.t('Jukebox.nonMp3Error', '试图播放非mp3格式文件'));
      console.error('[Jukebox]', window.t('Jukebox.nonMp3Info', '非mp3音频信息'), JSON.stringify(song, null, 2));
      throw new Error(window.t('Jukebox.nonMp3Error', '试图播放非mp3格式文件'));
    }
    
    console.log('[Jukebox]', window.t('Jukebox.useAPlayer', '使用APlayer播放mp3文件'));
    
    player.list.add([{
      name: song.name,
      artist: song.artist,
      url: song.audio,
      cover: ''
    }]);
    
    player.options.loop = 'none';
    
    if (!Jukebox.State.mp3EndedListenerAdded) {
      player.on('ended', () => {
        console.log('[Jukebox]', window.t('Jukebox.mp3Ended', 'mp3播放结束'), {
          isPlaying: Jukebox.State.isPlaying,
          currentSong: Jukebox.State.currentSong,
          playerLoop: player.options.loop
        });
        Jukebox.stopVMD();
        Jukebox.State.isPlaying = false;
        Jukebox.State.currentSong = null;
        Jukebox.updateStoppedStatus();
      });
      Jukebox.State.mp3EndedListenerAdded = true;
    }
    
    player.play();
    
    console.log('[Jukebox]', window.t('Jukebox.startPlay', '开始播放mp3音频'), song.audio);
  },
  
  playVMD: async function(vmdPath) {
    if (!window.mmdManager || !window.mmdManager.animationModule) {
      console.warn('[Jukebox]', window.t('Jukebox.vmdNotInit', 'MMD Manager 未初始化，跳过动画'));
      return;
    }
    
    try {
      Jukebox.stopVMD();
      
      await window.mmdManager.animationModule.loadAnimation(vmdPath);
      await window.mmdManager.animationModule.play();
      
      Jukebox.State.isVMDPlaying = true;
      
      console.log('[Jukebox]', window.t('Jukebox.vmdPlayed', 'VMD 动画已播放'), vmdPath);
    } catch (error) {
      console.error('[Jukebox]', window.t('Jukebox.vmdPlayFailed', 'VMD 播放失败'), error);
    }
  },
  
  updateVolume: function(value) {
    const volume = parseFloat(value);
    const player = Jukebox.getPlayer();
    
    if (player) {
      player.volume(volume);
    }
    
    const volumeValue = document.getElementById('jukebox-volume-value');
    if (volumeValue) {
      volumeValue.textContent = Math.round(volume * 100) + '%';
    }
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
  },
  
  stopPlayback: function() {
    Jukebox.stopAudio();
    Jukebox.stopVMD();
    
    Jukebox.State.currentSong = null;
    Jukebox.State.isPlaying = false;
    Jukebox.State.isVMDPlaying = false;
    
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
  
  stopVMD: function() {
    if (window.mmdManager?.animationModule) {
      window.mmdManager.animationModule.stop();
      Jukebox.State.isVMDPlaying = false;
    }
  },
  
  getPlayer: function() {
    if (window.music_ui && window.music_ui.getMusicPlayerInstance) {
      return window.music_ui.getMusicPlayerInstance();
    }
    
    return Jukebox.State.player;
  },
  
  initPlayer: function() {
    if (window.music_ui && window.music_ui.getMusicPlayerInstance) {
      console.log('[Jukebox] 使用现有的音乐播放器');
      return;
    }
    
    console.log('[Jukebox] 创建新的音乐播放器');
    
    if (typeof APlayer === 'undefined') {
      console.warn('[Jukebox] APlayer 未加载，等待加载...');
      setTimeout(Jukebox.initPlayer, 500);
      return;
    }
    
    const playerContainer = document.createElement('div');
    playerContainer.id = 'jukebox-player';
    playerContainer.style.display = 'none';
    Jukebox.State.container.appendChild(playerContainer);
    
    Jukebox.State.player = new APlayer({
      container: playerContainer,
      autoplay: false,
      theme: Jukebox.Config.themeColor,
      preload: 'auto',
      listFolded: true,
      volume: 1,
      audio: []
    });
    
    console.log('[Jukebox] APlayer已创建，音量:', Jukebox.State.player.audio.volume);
  },
  
  isMMDModel: function() {
    const modelType = window.lanlan_config?.model_type || 'live2d';
    return modelType === 'mmd' || modelType === 'live3d';
  },
  
  updatePlayingStatus: function(song) {
    const statusText = document.getElementById('jukebox-status-text');
    if (statusText) {
      statusText.textContent = window.t('Jukebox.playing', { name: song.name, artist: song.artist }) || `正在播放: ${song.name} - ${song.artist}`;
    }
    
    document.querySelectorAll('.play-btn').forEach(btn => {
      btn.textContent = window.t('Jukebox.play', '播放');
      btn.classList.remove('playing');
    });
    
    const currentRow = document.querySelector(`tr[data-song-id="${song.id}"]`);
    if (currentRow) {
      const btn = currentRow.querySelector('.play-btn');
      if (btn) {
        btn.textContent = window.t('Jukebox.stop', '停止');
        btn.classList.add('playing');
      }
    }
  },
  
  updateStoppedStatus: function() {
    const statusText = document.getElementById('jukebox-status-text');
    if (statusText) {
      statusText.textContent = window.t('Jukebox.ready', '准备就绪');
    }
    
    document.querySelectorAll('.play-btn').forEach(btn => {
      btn.textContent = window.t('Jukebox.play', '播放');
      btn.classList.remove('playing');
    });
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
  }
};
