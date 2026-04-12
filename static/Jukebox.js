window.Jukebox = {



  Config: {
    width: '500px',
    container: {
      background: '#87CEEB', // 容器背景色
      boxShadow: '0 8px 32px rgba(0, 0, 0, 0.3)', // 容器阴影
      color: 'rgba(255, 255, 255, 1)' // 容器文字颜色
    },
    header: {
      borderBottom: '1px solid rgba(255, 255, 255, 0.2)', // 标题栏下边框
      btnHoverBg: 'rgba(255, 255, 255, 0.2)' // 标题栏按钮悬停背景
    },
    notice: {
      background: 'rgba(255, 255, 255, 0.18)', // 提示区域背景
      border: '1px solid rgba(255, 255, 255, 0.35)' // 提示区域边框
    },
    table: {
      headerBg: 'rgba(0, 0, 0, 0.2)', // 表头背景
      headerColor: 'rgba(255, 255, 255, 0.9)', // 表头文字颜色
      bodyBg: 'rgba(255, 255, 255, 0.1)', // 表格背景
      rowHoverBg: 'rgba(255, 255, 255, 0.15)', // 表格行悬停背景
      rowBorder: '1px solid rgba(255, 255, 255, 0.1)', // 表格行边框
      loadingColor: 'rgba(255, 255, 255, 0.7)' // 加载中文字颜色
    },
    button: {
      playBg: '#4CAF50', // 播放按钮背景
      playHoverBg: '#45a049', // 播放按钮悬停背景
      playingBg: '#f44336', // 播放中按钮背景
      playingHoverBg: '#da190b', // 播放中按钮悬停背景
      pauseBg: '#FF9800', // 暂停按钮背景
      pauseHoverBg: '#F57C00', // 暂停按钮悬停背景
      resumeBg: '#63c7c7ff', // 恢复按钮背景
      resumeHoverBg: '#7fe0e0ff', // 恢复按钮悬停背景
      color: 'white' // 按钮文字颜色
    },
    progress: {
      containerBg: 'rgba(0, 0, 0, 0.2)', // 进度条容器背景
      trackBg: 'rgba(255, 255, 255, 0.3)', // 进度条轨道背景
      sliderBg: 'rgba(255, 255, 255, 0.6)', // 进度条滑块背景
      sliderSeekableBg: '#4CAF50', // 进度条滑块可拖动时背景
      textColor: 'rgba(255, 255, 255, 0.9)' // 进度条文字颜色
    },
    volume: {
      iconColor: 'rgba(255, 255, 255, 1)', // 喇叭图标颜色
      iconHoverColor: 'rgba(0, 40, 80, 1)', // 喇叭悬停颜色
      iconHoverBg: 'rgba(0, 60, 100, 0.15)', // 喇叭悬停背景
      popupBg: 'rgba(255, 255, 255, 0.95)', // 弹出窗口背景
      popupShadow: '0 4px 12px rgba(0, 0, 0, 0.15)', // 弹出窗口阴影
      trackColor: 'rgba(0, 100, 150, 0.3)', // 音量轨道颜色
      textColor: 'rgba(0, 60, 100, 0.85)', // 文字颜色
      textHoverBg: 'rgba(0, 100, 150, 0.15)', // 文字悬停背景
      inputBg: 'rgba(0, 100, 150, 0.1)', // 输入框背景
      inputBorder: 'rgba(0, 100, 150, 0.3)', // 输入框边框
      inputFocusBorder: '#4CAF50', // 输入框聚焦边框
      inputFocusBg: 'rgba(0, 100, 150, 0.15)', // 输入框聚焦背景
      sliderColor: '#4CAF50', // 滑块颜色
      sliderHoverColor: '#45a049' // 滑块悬停颜色
    },
    buttonActive: {
      background: 'rgba(30, 60, 114, 0.3)' // 点歌台按钮激活状态背景
    },
    // 校准面板颜色
    calibration: {
      toggleBg: 'linear-gradient(135deg, #6695ea 0%, #6695ea 100%)', // 校准按钮背景
      toggleShadow: '0 4px 12px rgba(102, 126, 234, 0.4)', // 校准按钮悬停阴影
      panelBg: 'rgba(0, 0, 0, 0.2)', // 校准面板背景
      titleColor: 'rgba(255, 255, 255, 0.9)', // 标题颜色
      fpsColor: 'rgba(255, 255, 255, 0.6)', // FPS显示颜色
      closeBg: 'rgba(255, 255, 255, 0.1)', // 关闭按钮背景
      closeHoverBg: 'rgba(255, 255, 255, 0.2)', // 关闭按钮悬停背景
      closeColor: 'rgba(255, 255, 255, 0.8)', // 关闭按钮颜色
      btnBg: 'rgba(255, 255, 255, 0.1)', // 校准按钮背景
      btnBorder: 'rgba(255, 255, 255, 0.2)', // 校准按钮边框
      btnHoverBg: 'rgba(255, 255, 255, 0.2)', // 校准按钮悬停背景
      btnHoverBorder: 'rgba(255, 255, 255, 0.4)', // 校准按钮悬停边框
      valueColor: '#ffffffff', // 数值颜色
      resetBg: 'rgba(244, 67, 54, 0.2)', // 重置按钮背景
      resetBorder: 'rgba(244, 67, 54, 0.4)', // 重置按钮边框
      resetColor: '#f44336', // 重置按钮颜色
      resetHoverBg: 'rgba(244, 67, 54, 0.3)', // 重置按钮悬停背景
      resetHoverBorder: 'rgba(244, 67, 54, 0.6)' // 重置按钮悬停边框
    },
    // 状态文字
    status: {
      color: 'rgba(255, 255, 255, 0.8)', // 状态文字颜色
      bg: 'rgba(0, 0, 0, 0.15)' // 状态文字背景
    }
  },
  
  State: {
    songs: [],
    currentSong: null,
    isPlaying: false,
    isVMDPlaying: false,
    player: null,
    audioElement: null,
    mp3EndedListenerAdded: false,
    boundPlayer: null,
    playRequestId: 0,
    isPaused: false,
    savedIdleAnimationUrl: null,
    savedVolume: 1,
    isMuted: false,
    progressTimer: null,
    isSeeking: false,
    isOpen: false,
    isHidden: false,
    container: null,
    styleElement: null,
    observer: null,
    songElements: {},
    tooltipElement: null,
    tooltipTimeout: null,
    // 窗口拖拽状态
    isDragging: false,
    dragOffset: { x: 0, y: 0 }
  },

  showTooltip: function(element, text) {
    Jukebox.hideTooltip();
    
    Jukebox.State.tooltipTimeout = setTimeout(() => {
      if (!Jukebox.State.tooltipElement) {
        const tooltip = document.createElement('div');
        tooltip.className = 'jukebox-tooltip';
        tooltip.textContent = text;
        document.body.appendChild(tooltip);
        Jukebox.State.tooltipElement = tooltip;
      }
      
      const tooltip = Jukebox.State.tooltipElement;
      tooltip.textContent = text;
      
      const rect = element.getBoundingClientRect();
      tooltip.style.left = rect.left + rect.width / 2 - tooltip.offsetWidth / 2 + 'px';
      tooltip.style.top = rect.bottom + 6 + 'px';
      
      requestAnimationFrame(() => {
        tooltip.classList.add('visible');
      });
    }, 400);
  },

  hideTooltip: function() {
    if (Jukebox.State.tooltipTimeout) {
      clearTimeout(Jukebox.State.tooltipTimeout);
      Jukebox.State.tooltipTimeout = null;
    }
    
    if (Jukebox.State.tooltipElement) {
      Jukebox.State.tooltipElement.remove();
      Jukebox.State.tooltipElement = null;
    }
  },

  setupTooltip: function(element, text) {
    element.addEventListener('mouseenter', () => Jukebox.showTooltip(element, text));
    element.addEventListener('mouseleave', () => Jukebox.hideTooltip());
  },

  SongActionManager: {
    element: null,

    // 管理器颜色配置
    Config: {
      // 面板
      panel: {
        background: 'rgba(0, 0, 0, 0.85)',
        color: 'white'
      },
      // 标签页
      tabs: {
        borderBottom: 'rgba(255,255,255,0.2)',
        tabColor: 'rgba(255,255,255,0.7)',
        tabHoverBg: 'rgba(255,255,255,0.1)',
        tabActiveBg: 'rgba(255,255,255,0.2)'
      },
      // 列表项
      item: {
        background: 'rgba(255,255,255,0.1)',
        hoverBg: 'rgba(255,255,255,0.15)',
        draggingOpacity: '0.5'
      },
      // 格式颜色
      formatColors: {
        vmd: { primary: '#2196F3', bg: 'rgba(33,150,243,0.4)', bgHover: 'rgba(33,150,243,0.6)', bgDefault: 'rgba(33,150,243,0.85)', border: 'rgba(33,150,243,0.6)', borderDefault: 'rgba(100,200,255,0.9)', smallBg: 'rgba(33,150,243,0.3)', smallBgDefault: 'rgba(33,150,243,0.7)', smallBorder: 'rgba(33,150,243,0.5)' },
        bvh: { primary: '#FF9800', bg: 'rgba(255,152,0,0.4)', bgHover: 'rgba(255,152,0,0.6)', bgDefault: 'rgba(255,152,0,0.85)', border: 'rgba(255,152,0,0.6)', borderDefault: 'rgba(255,200,100,0.9)', smallBg: 'rgba(255,152,0,0.3)', smallBgDefault: 'rgba(255,152,0,0.7)', smallBorder: 'rgba(255,152,0,0.5)' },
        vrma: { primary: '#4CAF50', bg: 'rgba(76,175,80,0.4)', bgHover: 'rgba(76,175,80,0.6)', bgDefault: 'rgba(76,175,80,0.85)', border: 'rgba(76,175,80,0.6)', borderDefault: 'rgba(120,220,120,0.9)', smallBg: 'rgba(76,175,80,0.3)', smallBgDefault: 'rgba(76,175,80,0.7)', smallBorder: 'rgba(76,175,80,0.5)' },
        fbx: { primary: '#9C27B0', bg: 'rgba(156,39,176,0.4)', bgHover: 'rgba(156,39,176,0.6)', bgDefault: 'rgba(156,39,176,0.85)', border: 'rgba(156,39,176,0.6)', borderDefault: 'rgba(200,100,220,0.9)', smallBg: 'rgba(156,39,176,0.3)', smallBgDefault: 'rgba(156,39,176,0.7)', smallBorder: 'rgba(156,39,176,0.5)' },
        default: { primary: '#757575', bg: 'rgba(158,158,158,0.4)', bgHover: 'rgba(158,158,158,0.6)', bgDefault: 'rgba(158,158,158,0.85)', border: 'rgba(158,158,158,0.6)', borderDefault: 'rgba(200,200,200,0.9)', smallBg: 'rgba(158,158,158,0.3)', smallBgDefault: 'rgba(158,158,158,0.7)', smallBorder: 'rgba(158,158,158,0.5)' }
      },
      // 功能色
      functional: {
        success: '#4CAF50',
        successBg: 'rgba(76,175,80,0.1)',
        successHoverBg: 'rgba(76,175,80,0.3)',
        danger: '#ff4444',
        dangerHover: '#ff6666',
        missing: '#ff6b6b',
        missingBg: 'rgba(255,107,107,0.2)',
        confirmBg: 'rgba(76,175,80,0.7)',
        confirmHoverBg: 'rgba(76,175,80,0.9)',
        cancelBg: 'rgba(244,67,54,0.7)',
        cancelHoverBg: 'rgba(244,67,54,0.9)',
        dropdownBg: 'rgba(40,40,40,0.95)',
        tagBg: 'rgba(76,175,80,0.3)',
        countBg: 'rgba(120,120,120,0.8)'
      },
      // 边框和分割线
      borders: {
        dashed: 'rgba(255,255,255,0.2)',
        solid: 'rgba(255,255,255,0.3)',
        divider: 'rgba(255,255,255,0.1)',
        itemFormatBg: 'rgba(255,255,255,0.1)'
      },
      // 文字颜色
      text: {
        primary: 'white',
        secondary: 'rgba(255,255,255,0.8)',
        muted: 'rgba(255,255,255,0.6)',
        placeholder: 'rgba(255,255,255,0.5)',
        empty: 'rgba(255,255,255,0.5)'
      },
      // 输入框
      input: {
        hoverBg: 'rgba(255,255,255,0.1)',
        focusBg: 'rgba(255,255,255,0.2)'
      },
      // 按钮
      buttons: {
        visibility: {
          color: 'rgba(255,255,255,0.7)',
          hoverBg: 'rgba(255,255,255,0.1)',
          hoverColor: 'rgba(255,255,255,0.9)',
          hiddenColor: 'rgba(255,255,255,0.4)'
        },
        delete: {
          color: '#ff6666',
          hoverBg: 'rgba(255,102,102,0.2)'
        },
        primary: {
          bg: '#4CAF50',
          hoverBg: '#45a049'
        }
      },
      // 选中状态
      selected: {
        bg: 'rgba(76,175,80,0.2)',
        border: '3px solid #4CAF50'
      },
      // 拖放区域
      dropzone: {
        overBg: 'rgba(100, 150, 255, 0.2)',
        overBorder: '2px dashed rgba(100, 150, 255, 0.5)'
      },
      // 底部区域
      footer: {
        bg: 'rgba(0,0,0,0.3)',
        borderTop: '1px solid rgba(255,255,255,0.1)',
        importBg: 'rgba(0,0,0,0.4)',
        buttonBg: 'rgba(255,255,255,0.2)',
        buttonHoverBg: 'rgba(255,255,255,0.3)',
        hintColor: 'rgba(255,255,255,0.7)',
        shortcutColor: 'rgba(255,255,255,0.5)'
      }
    },

    // 获取格式颜色配置
    getFormatColorConfig: function(format) {
      return this.Config.formatColors[format?.toLowerCase()] || this.Config.formatColors.default;
    },

    // 获取格式颜色（主色）
    getFormatColor: function(format) {
      return this.getFormatColorConfig(format).primary;
    },

    api: {
      baseUrl: '/api/jukebox',
      
      async getConfig() {
        const response = await fetch(`${this.baseUrl}/config`);
        return response.json();
      },
      
      async addSong(file, name) {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('name', name);
        const response = await fetch(`${this.baseUrl}/songs`, {
          method: 'POST',
          body: formData
        });
        return response.json();
      },
      
      async addAction(file, name) {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('name', name);
        const response = await fetch(`${this.baseUrl}/actions`, {
          method: 'POST',
          body: formData
        });
        return response.json();
      },
      
      async bind(songId, actionId, offset = 0) {
        const formData = new FormData();
        formData.append('songId', songId);
        formData.append('actionId', actionId);
        formData.append('offset', offset);
        const response = await fetch(`${this.baseUrl}/bind`, {
          method: 'POST',
          body: formData
        });
        return response.json();
      },

      async unbind(songId, actionId) {
        const formData = new FormData();
        formData.append('songId', songId);
        formData.append('actionId', actionId);
        const response = await fetch(`${this.baseUrl}/bind`, {
          method: 'DELETE',
          body: formData
        });
        return response.json();
      },

      async uploadSongs(files, metadata) {
        const formData = new FormData();
        files.forEach(f => formData.append('files', f));
        formData.append('metadata', JSON.stringify(metadata));
        const response = await fetch(`${this.baseUrl}/songs`, {
          method: 'POST',
          body: formData
        });
        return response.json();
      },

      async uploadActions(files, metadata) {
        const formData = new FormData();
        files.forEach(f => formData.append('files', f));
        formData.append('metadata', JSON.stringify(metadata));
        const response = await fetch(`${this.baseUrl}/actions`, {
          method: 'POST',
          body: formData
        });
        return response.json();
      },
      
      async updateOffset(songId, actionId, offset) {
        return this.bind(songId, actionId, offset);
      },
      
      async deleteSong(songId) {
        const response = await fetch(`${this.baseUrl}/songs/${songId}`, {
          method: 'DELETE'
        });
        return response.json();
      },
      
      async deleteAction(actionId) {
        const response = await fetch(`${this.baseUrl}/actions/${actionId}`, {
          method: 'DELETE'
        });
        return response.json();
      },
      
      async updateSongVisibility(songId, visible) {
        const formData = new FormData();
        formData.append('visible', visible);
        const response = await fetch(`${this.baseUrl}/songs/${songId}/visibility`, {
          method: 'PUT',
          body: formData
        });
        return response.json();
      },

      async updateSongMetadata(songId, name, artist) {
        const formData = new FormData();
        if (name !== undefined) formData.append('name', name);
        if (artist !== undefined) formData.append('artist', artist);
        const response = await fetch(`${this.baseUrl}/songs/${songId}/metadata`, {
          method: 'PUT',
          body: formData
        });
        return response.json();
      },

      async updateActionMetadata(actionId, name) {
        const formData = new FormData();
        formData.append('name', name);
        const response = await fetch(`${this.baseUrl}/actions/${actionId}/metadata`, {
          method: 'PUT',
          body: formData
        });
        return response.json();
      },

      async setSongDefaultAction(songId, actionId) {
        const formData = new FormData();
        formData.append('action_id', actionId);
        const response = await fetch(`${this.baseUrl}/songs/${songId}/default-action`, {
          method: 'PUT',
          body: formData
        });
        if (!response.ok) {
          const errorData = await response.json().catch(() => ({}));
          throw new Error(errorData.detail || `HTTP ${response.status}`);
        }
        return response.json();
      },

      async export() {
        const response = await fetch(`${this.baseUrl}/export`);
        return response.blob();
      },
      
      async import(file) {
        const formData = new FormData();
        formData.append('file', file);
        const response = await fetch(`${this.baseUrl}/import`, {
          method: 'POST',
          body: formData
        });
        return response.json();
      }
    },
    
    data: {
      songs: {},
      actions: {},
      bindings: {}
    },
    
    async load() {
      try {
        const config = await this.api.getConfig();
        this.data.songs = config.songs || {};
        this.data.actions = config.actions || {};
        this.data.bindings = config.bindings || {};
        this.render();
      } catch (error) {
        console.error('[SongActionManager] 加载配置失败:', error);
      }
    },
    
    isVisible: false,

    toggle: function() {
      if (window.__NEKO_JUKEBOX_STANDALONE__) {
        // 独立模式：打开/关闭管理器独立窗口
        if (this._managerWindow && !this._managerWindow.closed) {
          this._managerWindow.close();
          this._managerWindow = null;
        } else {
          this._managerWindow = window.open(
            '/jukebox/manager', 'neko-jukebox-manager',
            'width=480,height=600'
          );
        }
        return;
      }
      // Web 模式：切换 DOM 面板
      if (this.isVisible) {
        this.hide();
      } else {
        this.show();
      }
    },

    show: function() {
      if (this.element) {
        this.element.style.display = 'flex';
        this.isVisible = true;
        if (!this.element.style.left) {
          this.positionNextToJukebox();
        }
        this.load();
      }
    },

    hide: function() {
      if (this.element) {
        this.element.style.display = 'none';
        this.isVisible = false;
      }
    },

    positionNextToJukebox: function() {
      const wrapper = Jukebox.State.container;
      if (!wrapper || !this.element) return;
      const wrapperRect = wrapper.getBoundingClientRect();
      const panelWidth = 450;
      const gap = 10;
      const viewportWidth = document.documentElement.clientWidth;
      const maxLeft = Math.max(0, viewportWidth - panelWidth);
      let left = wrapperRect.left - panelWidth - gap;
      let top = wrapperRect.bottom - this.element.offsetHeight;
      // 如果左侧空间不够，放到右侧
      if (left < 0) {
        left = wrapperRect.right + gap;
      }
      // 限制在视口内
      left = Math.min(Math.max(0, left), maxLeft);
      top = Math.max(0, top);
      this.element.style.left = left + 'px';
      this.element.style.top = top + 'px';
    },

    create: function() {
      const panel = document.createElement('div');
      panel.className = 'jukebox-sam-panel';
      panel.style.display = 'none'; // 默认隐藏
      panel.innerHTML = `
        <div class="sam-header">
          <span class="sam-title">${window.t('Jukebox.managerTitle', '管理器')}</span>
          <div class="sam-tabs">
            <button class="sam-tab active" data-tab="songs">${window.t('Jukebox.songs', '歌曲')}</button>
            <button class="sam-tab" data-tab="actions">${window.t('Jukebox.actions', '动作')}</button>
            <button class="sam-tab" data-tab="bindings">${window.t('Jukebox.bindings', '绑定')}</button>
          </div>
          <button class="sam-close-btn" onclick="Jukebox.SongActionManager.hide()" title="${window.t('Jukebox.close', '关闭')}">×</button>
        </div>
        <div class="sam-content">
          <div class="sam-panel songs-panel active"></div>
          <div class="sam-panel actions-panel"></div>
          <div class="sam-panel bindings-panel"></div>
        </div>
        <div class="sam-footer">
          <div class="sam-footer-buttons">
            <button class="sam-btn sam-btn-export-all" onclick="Jukebox.SongActionManager.exportAll(false)">${window.t('Jukebox.exportAllIgnoreHidden', '全部导出(忽略隐藏)')}</button>
            <button class="sam-btn sam-btn-export-all" onclick="Jukebox.SongActionManager.exportAll(true)">${window.t('Jukebox.exportAllIncludeHidden', '全部导出(含隐藏)')}</button>
            <button class="sam-btn sam-btn-export-selected" onclick="Jukebox.SongActionManager.exportSelected()" style="display:none">${window.t('Jukebox.exportSelected', '导出选中')}</button>
          </div>
          <span class="sam-selection-info" id="sam-selection-info"></span>
          <div class="sam-unified-hint" id="sam-unified-hint">
            <span class="sam-hint-normal">${window.t('Jukebox.unifiedDropHint', '支持拖拽歌曲、动作、导入包到此处')} · <span class="sam-click-add" onclick="Jukebox.SongActionManager.showUnifiedFilePicker()">+ ${window.t('Jukebox.clickToAdd', '点击添加')}</span></span>
            <span class="sam-hint-status" style="display:none"></span>
          </div>
        </div>
      `;

      // 绑定统一拖拽导入事件
      this.bindUnifiedDropEvents(panel);
      
      this.element = panel;
      this.bindEvents(panel);
      this.load();
      return panel;
    },
    
    bindEvents(panel) {
      const tabs = panel.querySelectorAll('.sam-tab');
      tabs.forEach(tab => {
        tab.addEventListener('click', () => {
          tabs.forEach(t => t.classList.remove('active'));
          tab.classList.add('active');
          
          const tabName = tab.dataset.tab;
          panel.querySelectorAll('.sam-panel').forEach(p => p.classList.remove('active'));
          panel.querySelector(`.${tabName}-panel`).classList.add('active');
          
          this.renderTab(tabName);
        });
      });
    },
    
    render() {
      if (!this.element) return;
      const activeTab = this.element.querySelector('.sam-tab.active')?.dataset.tab || 'songs';
      this.renderTab(activeTab);
    },
    
    renderTab(tabName) {
      const panel = this.element?.querySelector(`.${tabName}-panel`);
      if (!panel) return;
      
      switch (tabName) {
        case 'songs':
          this.renderSongs(panel);
          break;
        case 'actions':
          this.renderActions(panel);
          break;
        case 'bindings':
          this.renderBindings(panel);
          break;
      }
      this.updateSelectionInfo();
    },
    
    renderSongs(panel) {
      const showHidden = this.showHiddenSongs !== false;
      const songs = Object.entries(this.data.songs).filter(([id, song]) => showHidden || song.visible !== false);

      panel.innerHTML = `
        <div class="sam-list-header">
          <label class="sam-checkbox">
            <input type="checkbox" id="select-all-songs" onchange="Jukebox.SongActionManager.toggleSelectAllSongs(this.checked)">
            <span>${window.t('Jukebox.selectAll', '全选')}</span>
          </label>
          <label class="sam-checkbox sam-checkbox-right">
            <input type="checkbox" ${showHidden ? 'checked' : ''} onchange="Jukebox.SongActionManager.toggleShowHidden(this.checked)">
            <span>${window.t('Jukebox.showHiddenSongs', '显示隐藏的歌曲')}</span>
          </label>
        </div>
        <div class="sam-list">
            ${songs.length === 0 ? `<div class="sam-empty">${window.t('Jukebox.noSongs', '暂无歌曲')}</div>` :
              songs.map(([id, song]) => `
                <div class="sam-item ${song.visible === false ? 'sam-item-hidden' : ''} ${this.selectedSongs?.has(id) ? 'sam-item-selected' : ''}" data-id="${id}" draggable="true">
                  <div class="sam-item-header">
                    <label class="sam-checkbox sam-item-checkbox">
                      <input type="checkbox" class="sam-song-select" data-id="${id}" ${this.selectedSongs?.has(id) ? 'checked' : ''} onchange="Jukebox.SongActionManager.toggleSongSelect('${id}', this.checked)">
                    </label>
                    <span class="sam-item-name" contenteditable="true"
                          onblur="Jukebox.SongActionManager.updateSongName('${id}', this.innerText)"
                          onkeydown="if(event.key==='Enter'){this.blur();event.preventDefault();}">${song.name}</span>
                    <div class="sam-item-actions">
                      <button class="sam-visibility-btn ${song.visible === false ? 'hidden' : ''}"
                              onclick="Jukebox.SongActionManager.toggleSongVisibility('${id}')"
                              title="${song.visible === false ? window.t('Jukebox.show', '显示') : window.t('Jukebox.hide', '隐藏')}">
                        ${song.visible === false
                          ? '<svg viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M12 4.5C7 4.5 2.73 7.61 1 12c1.73 4.39 6 7.5 11 7.5s9.27-3.11 11-7.5c-1.73-4.39-6-7.5-11-7.5zM12 17c-2.76 0-5-2.24-5-5s2.24-5 5-5 5 2.24 5 5-2.24 5-5 5zm0-8c-1.66 0-3 1.34-3 3s1.34 3 3 3 3-1.34 3-3-1.34-3-3-3z"/><line x1="3" y1="3" x2="21" y2="21" stroke="currentColor" stroke-width="2"/></svg>'
                          : '<svg viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M12 4.5C7 4.5 2.73 7.61 1 12c1.73 4.39 6 7.5 11 7.5s9.27-3.11 11-7.5c-1.73-4.39-6-7.5-11-7.5zM12 17c-2.76 0-5-2.24-5-5s2.24-5 5-5 5 2.24 5 5-2.24 5-5 5zm0-8c-1.66 0-3 1.34-3 3s1.34 3 3 3 3-1.34 3-3-1.34-3-3-3z"/></svg>'}
                      </button>
                      <button class="sam-delete-btn" onclick="Jukebox.SongActionManager.confirmDeleteSong('${id}')" title="${window.t('Jukebox.delete', '删除')}">🗑</button>
                    </div>
                  </div>
                  <div class="sam-item-artist" contenteditable="true"
                       onblur="Jukebox.SongActionManager.updateSongArtist('${id}', this.innerText)"
                       onkeydown="if(event.key==='Enter'){this.blur();event.preventDefault();}">${song.artist || window.t('Jukebox.unknown', '未知')}</span>
                  </div>
                  <div class="sam-item-bindings">
                    ${this.getSongBindings(id).map(actionId => {
                      const action = this.data.actions[actionId];
                      if (!action) return '';
                      const isDefault = song.defaultAction === actionId;
                      const format = action.format || 'vmd';
                      const titleText = isDefault
                        ? `${window.t('Jukebox.defaultAction', '默认动画')} - ${window.t('Jukebox.clickSetDefault', '点击设为默认')}\n${window.t('Jukebox.format', '格式')}: ${format.toUpperCase()}`
                        : `${window.t('Jukebox.clickSetDefault', '点击设为默认')}\n${window.t('Jukebox.format', '格式')}: ${format.toUpperCase()}`;
                      return `<span class="sam-binding-tag sam-action-tag sam-action-tag-${format.toLowerCase()} ${isDefault ? 'sam-action-tag-default' : ''}"
                                   onclick="Jukebox.SongActionManager.setDefaultAction('${id}', '${actionId}')"
                                   title="${titleText}">
                        ${isDefault ? '★ ' : ''}${action.name}
                      </span>`;
                    }).join('')}
                  </div>
                </div>
              `).join('')}
          </div>
      `;
      this.bindDragEvents(panel);
      this.bindFileDropEvents(panel, 'audio');
    },

    renderActions(panel) {
      const actions = Object.entries(this.data.actions);
      panel.innerHTML = `
        <div class="sam-list-header">
          <label class="sam-checkbox">
            <input type="checkbox" id="select-all-actions" onchange="Jukebox.SongActionManager.toggleSelectAllActions(this.checked)">
            <span>${window.t('Jukebox.selectAll', '全选')}</span>
          </label>
          <span></span>
        </div>
        <div class="sam-list">
            ${actions.length === 0 ? `<div class="sam-empty">${window.t('Jukebox.noActions', '暂无动画')}</div>` :
              actions.map(([id, action]) => {
                const format = action.format || 'vmd';
                const formatColor = this.getFormatColor(format);
                return `
                <div class="sam-item ${this.selectedActions?.has(id) ? 'sam-item-selected' : ''}" data-id="${id}" draggable="true">
                  <div class="sam-item-header">
                    <label class="sam-checkbox sam-item-checkbox">
                      <input type="checkbox" class="sam-action-select" data-id="${id}" ${this.selectedActions?.has(id) ? 'checked' : ''} onchange="Jukebox.SongActionManager.toggleActionSelect('${id}', this.checked)">
                    </label>
                    <span class="sam-format-dot" style="background-color: ${formatColor};"></span>
                    <span class="sam-item-name" contenteditable="true"
                          onblur="Jukebox.SongActionManager.updateActionName('${id}', this.innerText)"
                          onkeydown="if(event.key==='Enter'){this.blur();event.preventDefault();}">${action.name}</span>
                    <div class="sam-item-actions">
                      ${action.missing ? `<span class="sam-missing-badge">${window.t('Jukebox.missing', '缺失')}</span>` : ''}
                      <button class="sam-delete-btn" onclick="Jukebox.SongActionManager.confirmDeleteAction('${id}')" title="${window.t('Jukebox.delete', '删除')}">🗑</button>
                    </div>
                  </div>
                  <div class="sam-item-bindings">
                    ${this.getActionBindings(id).map(songId => {
                      const song = this.data.songs[songId];
                      return song ? `<span class="sam-binding-tag">${song.name}</span>` : '';
                    }).join('')}
                  </div>
                </div>
              `}).join('')}
          </div>
      `;
      this.bindDragEvents(panel);
      this.bindFileDropEvents(panel, 'action');
    },

    renderBindings(panel) {
      this.initSelection();

      panel.innerHTML = `
        <div class="sam-bindings-container">
          <div class="sam-bindings-section">
            <div class="sam-bindings-header">
              <h4>${window.t('Jukebox.songList', '歌曲列表 (拖拽到右侧)')}</h4>
              <label class="sam-checkbox">
                <input type="checkbox" id="select-all-binding-songs" onchange="Jukebox.SongActionManager.toggleSelectAllBindingSongs(this.checked)">
                <span>${window.t('Jukebox.selectAll', '全选')}</span>
              </label>
            </div>
            <div class="sam-bindings-list songs-for-drop">
              ${Object.entries(this.data.songs).length === 0 ? `<div class="sam-empty">${window.t('Jukebox.noSongs', '暂无歌曲')}</div>` :
                Object.entries(this.data.songs).map(([id, song], index) => {
                  const boundActions = this.getSongBindings(id);
                  const isFullySelected = this.isSongFullySelectedInBindings(id);
                  const isPartiallySelected = this.selectedSongs.has(id) && !isFullySelected;
                  const songIndex = index + 1;
                  return `
                <div class="sam-binding-item ${isFullySelected ? 'sam-binding-item-selected' : ''} ${isPartiallySelected ? 'sam-binding-item-partial' : ''}" data-song-id="${id}" draggable="true" data-index="${songIndex}">
                  <div class="sam-binding-item-main">
                    <label class="sam-checkbox sam-item-checkbox">
                      <input type="checkbox" ${isFullySelected ? 'checked' : ''} onchange="Jukebox.SongActionManager.toggleBindingSongSelect('${id}', this.checked)">
                    </label>
                    <span class="sam-binding-item-index">${songIndex}</span>
                    <span class="sam-binding-item-name">${song.name}</span>
                  </div>
                  <div class="sam-binding-item-tags">
                    ${boundActions.map(actionId => {
                      const action = this.data.actions[actionId];
                      const isActionSelected = this.selectedActions.has(actionId);
                      const isDefault = song.defaultAction === actionId;
                      const format = action?.format || 'vmd';
                      const formatColor = this.getFormatColor(format);
                      const offset = this.data.bindings[id]?.[actionId]?.offset || 0;
                      const titleText = isDefault
                        ? `${window.t('Jukebox.defaultAction', '默认动画')} - ${window.t('Jukebox.clickSetDefault', '点击设为默认')}\n${window.t('Jukebox.offset', '偏移')}: ${offset}${window.t('Jukebox.frame', '帧')}\n${window.t('Jukebox.format', '格式')}: ${format.toUpperCase()}`
                        : `${window.t('Jukebox.clickSetDefault', '点击设为默认')}\n${window.t('Jukebox.offset', '偏移')}: ${offset}${window.t('Jukebox.frame', '帧')}\n${window.t('Jukebox.format', '格式')}: ${format.toUpperCase()}`;
                      return action ? `
                        <span class="sam-binding-tag-small sam-action-tag-small sam-action-tag-small-${format.toLowerCase()} ${isActionSelected ? 'sam-tag-selected' : ''} ${isDefault ? 'sam-action-tag-small-default' : ''}"
                              onclick="Jukebox.SongActionManager.setDefaultAction('${id}', '${actionId}')"
                              title="${titleText}">
                          <span class="sam-format-dot" style="background-color: ${formatColor};"></span>
                          ${isDefault ? '★ ' : ''}${action.name}
                          <button class="sam-unbind-btn" onclick="event.stopPropagation(); Jukebox.SongActionManager.unbindSongFromAction('${id}', '${actionId}');" title="${window.t('Jukebox.unbind', '解除绑定')}">×</button>
                        </span>` : '';
                    }).join('')}
                    <button class="sam-add-binding-btn" onclick="Jukebox.SongActionManager.showAddBindingInput(this, '${id}', 'song')" title="${window.t('Jukebox.addActionBinding', '手动添加动画绑定')}">+</button>
                  </div>
                </div>
              `}).join('')}
            </div>
          </div>
          <div class="sam-bindings-section">
            <div class="sam-bindings-header">
              <h4>${window.t('Jukebox.actionList', '动画列表 (拖拽到左侧)')}</h4>
              <label class="sam-checkbox">
                <input type="checkbox" id="select-all-binding-actions" onchange="Jukebox.SongActionManager.toggleSelectAllBindingActions(this.checked)">
                <span>${window.t('Jukebox.selectAll', '全选')}</span>
              </label>
            </div>
            <div class="sam-bindings-list actions-for-drop">
              ${Object.entries(this.data.actions).length === 0 ? `<div class="sam-empty">${window.t('Jukebox.noActions', '暂无动画')}</div>` :
                Object.entries(this.data.actions).map(([id, action], index) => {
                  const boundSongs = this.getActionBindings(id);
                  const isFullySelected = this.isActionFullySelectedInBindings(id);
                  const isPartiallySelected = this.selectedActions.has(id) && !isFullySelected;
                  const format = action.format || 'vmd';
                  const formatColor = this.getFormatColor(format);
                  const actionIndex = index + 1;
                  return `
                <div class="sam-binding-item ${isFullySelected ? 'sam-binding-item-selected' : ''} ${isPartiallySelected ? 'sam-binding-item-partial' : ''}" data-action-id="${id}" draggable="true" data-index="${actionIndex}">
                  <div class="sam-binding-item-main">
                    <label class="sam-checkbox sam-item-checkbox">
                      <input type="checkbox" ${isFullySelected ? 'checked' : ''} onchange="Jukebox.SongActionManager.toggleBindingActionSelect('${id}', this.checked)">
                    </label>
                    <span class="sam-binding-item-index">${actionIndex}</span>
                    <span class="sam-format-dot" style="background-color: ${formatColor};"></span>
                    <span class="sam-binding-item-name">${action.name}</span>
                  </div>
                  <div class="sam-binding-item-tags">
                    ${boundSongs.map(songId => {
                      const song = this.data.songs[songId];
                      const isSongSelected = this.selectedSongs.has(songId);
                      const offset = this.data.bindings[songId]?.[id]?.offset || 0;
                      const titleText = `${window.t('Jukebox.offset', '偏移')}: ${offset}${window.t('Jukebox.frame', '帧')}`;
                      return song ? `
                        <span class="sam-binding-tag-small ${isSongSelected ? 'sam-tag-selected' : ''}" title="${titleText}">
                          ${song.name}
                          <button class="sam-unbind-btn" onclick="Jukebox.SongActionManager.unbindSongFromAction('${songId}', '${id}'); event.stopPropagation();" title="${window.t('Jukebox.unbind', '解除绑定')}">×</button>
                        </span>` : '';
                    }).join('')}
                    <button class="sam-add-binding-btn" onclick="Jukebox.SongActionManager.showAddBindingInput(this, '${id}', 'action')" title="${window.t('Jukebox.addSongBinding', '手动添加歌曲绑定')}">+</button>
                  </div>
                </div>
              `}).join('')}
            </div>
          </div>
        </div>
      `;
      this.bindBindingDragEvents(panel);
    },
    
    toggleShowHidden(checked) {
      this.showHiddenSongs = checked;
      const songsPanel = document.querySelector('.songs-panel');
      if (songsPanel) {
        this.renderSongs(songsPanel);
      }
    },
    
    async toggleSongVisibility(songId) {
      const song = this.data.songs[songId];
      if (!song) return;
      
      const newVisible = song.visible === false ? true : false;
      try {
        await this.api.updateSongVisibility(songId, newVisible);
        song.visible = newVisible;
        
        const songsPanel = document.querySelector('.songs-panel');
        if (songsPanel) {
          this.renderSongs(songsPanel);
        }
        
        // 通知主UI刷新歌曲列表
        if (window.Jukebox && window.Jukebox.loadSongs) {
          window.Jukebox.loadSongs();
        }
      } catch (err) {
        console.error('切换歌曲可见性失败:', err);
        alert(window.t('Jukebox.operationFailed', '操作失败'));
      }
    },

    async updateSongName(songId, name) {
      name = name.trim();
      if (!name) return;

      const song = this.data.songs[songId];
      if (!song || song.name === name) return;

      try {
        await this.api.updateSongMetadata(songId, name, undefined);
        song.name = name;
        console.log('更新歌曲名称成功:', songId, name);
      } catch (err) {
        console.error('更新歌曲名称失败:', err);
        alert(window.t('Jukebox.saveFailed', '保存失败'));
        // 恢复原值
        const songsPanel = document.querySelector('.songs-panel');
        if (songsPanel) {
          this.renderSongs(songsPanel);
        }
      }
    },

    async updateSongArtist(songId, artist) {
      artist = artist.trim();
      
      const song = this.data.songs[songId];
      if (!song || song.artist === artist) return;
      
      try {
        await this.api.updateSongMetadata(songId, undefined, artist);
        song.artist = artist;
        console.log('更新歌曲歌手成功:', songId, artist);
      } catch (err) {
        console.error('更新歌曲歌手失败:', err);
        alert(window.t('Jukebox.saveFailed', '保存失败'));
        // 恢复原值
        const songsPanel = document.querySelector('.songs-panel');
        if (songsPanel) {
          this.renderSongs(songsPanel);
        }
      }
    },

    async updateActionName(actionId, name) {
      name = name.trim();
      if (!name) return;

      const action = this.data.actions[actionId];
      if (!action || action.name === name) return;

      try {
        await this.api.updateActionMetadata(actionId, name);
        action.name = name;
        console.log('更新动画名称成功:', actionId, name);
      } catch (err) {
        console.error('更新动画名称失败:', err);
        alert(window.t('Jukebox.saveFailed', '保存失败'));
        // 恢复原值
        const actionsPanel = document.querySelector('.actions-panel');
        if (actionsPanel) {
          this.renderActions(actionsPanel);
        }
      }
    },
    
    async setDefaultAction(songId, actionId) {
      const song = this.data.songs[songId];
      if (!song) {
        console.error('歌曲不存在:', songId);
        return;
      }
      
      // 如果点击的是当前默认动画，则取消默认
      const newDefaultAction = song.defaultAction === actionId ? '' : actionId;
      console.log('设置默认动画:', { songId, actionId, newDefaultAction, currentDefault: song.defaultAction });
      
      try {
        const result = await this.api.setSongDefaultAction(songId, newDefaultAction);
        console.log('API返回结果:', result);
        
        if (result && result.success === true) {
          song.defaultAction = newDefaultAction;

          // 刷新歌曲面板
          const songsPanel = document.querySelector('.songs-panel');
          if (songsPanel) {
            this.renderSongs(songsPanel);
          }

          // 刷新绑定面板（如果打开的话）
          const bindingsPanel = document.querySelector('.bindings-panel');
          if (bindingsPanel && bindingsPanel.innerHTML.trim()) {
            this.renderBindings(bindingsPanel);
          }

          // 通知主UI重新加载配置
          if (window.Jukebox && window.Jukebox.loadSongs) {
            console.log('[SongActionManager] 通知主UI重新加载配置');
            await window.Jukebox.loadSongs();
          }

          console.log('设置默认动画成功:', songId, newDefaultAction || '无');
        } else {
          console.error('API返回失败:', result);
          throw new Error((result && (result.error || result.detail)) || window.t('Jukebox.setDefaultFailed', '设置失败'));
        }
      } catch (err) {
        console.error('设置默认动画失败:', err);
        alert(window.t('Jukebox.setDefaultFailed', '设置失败') + ': ' + (err.message || window.t('Jukebox.unknownError', '未知错误')));
      }
    },
    
    confirmDeleteSong(songId) {
      const song = this.data.songs[songId];
      if (!song) return;

      const message = window.t('Jukebox.confirmDeleteSong', '确定要删除歌曲 "{{name}}" 吗？\n此操作不可恢复！', { name: song.name });
      if (confirm(message)) {
        this.deleteSong(songId);
      }
    },

    confirmDeleteAction(actionId) {
      const action = this.data.actions[actionId];
      if (!action) return;

      const message = window.t('Jukebox.confirmDeleteAction', '确定要删除动画 "{{name}}" 吗？\n此操作不可恢复！', { name: action.name });
      if (confirm(message)) {
        this.deleteAction(actionId);
      }
    },
    
    async deleteSong(songId) {
      try {
        await this.api.deleteSong(songId);
        // 从选择集合中移除
        if (this.selectedSongs) this.selectedSongs.delete(songId);
        delete this.data.songs[songId];
        delete this.data.bindings[songId];

        // 刷新所有面板
        this.refreshAllPanels();
      } catch (err) {
        console.error('删除歌曲失败:', err);
        alert(window.t('Jukebox.deleteFailed', '删除失败'));
      }
    },

    async deleteAction(actionId) {
      try {
        await this.api.deleteAction(actionId);
        // 从选择集合中移除
        if (this.selectedActions) this.selectedActions.delete(actionId);
        delete this.data.actions[actionId];

        // 从所有绑定中移除
        for (const songId in this.data.bindings) {
          delete this.data.bindings[songId][actionId];
          if (Object.keys(this.data.bindings[songId]).length === 0) {
            delete this.data.bindings[songId];
          }
        }

        // 刷新所有面板
        this.refreshAllPanels();
      } catch (err) {
        console.error('删除动画失败:', err);
        alert(window.t('Jukebox.deleteFailed', '删除失败'));
      }
    },

    // 刷新所有面板
    refreshAllPanels() {
      const songsPanel = document.querySelector('.songs-panel .sam-panel-content');
      if (songsPanel) this.renderSongs(songsPanel);

      const actionsPanel = document.querySelector('.actions-panel .sam-panel-content');
      if (actionsPanel) this.renderActions(actionsPanel);

      const bindingsPanel = document.querySelector('.bindings-panel .sam-panel-content');
      if (bindingsPanel) this.renderBindings(bindingsPanel);
    },
    
    // 初始化选择集合
    initSelection() {
      if (!this.selectedSongs) this.selectedSongs = new Set();
      if (!this.selectedActions) this.selectedActions = new Set();
    },

    // 检查歌曲在绑定Tab是否应该显示勾选（合集逻辑：歌曲被勾选且其所有绑定的动画都被勾选）
    isSongFullySelectedInBindings(songId) {
      this.initSelection();
      if (!this.selectedSongs.has(songId)) return false;
      
      const boundActions = this.getSongBindings(songId);
      if (boundActions.length === 0) return true; // 没有绑定动画时，只检查歌曲本身
      
      return boundActions.every(actionId => this.selectedActions.has(actionId));
    },

    // 检查动画在绑定Tab是否应该显示勾选（合集逻辑：动画被勾选且其所有绑定的歌曲都被勾选）
    isActionFullySelectedInBindings(actionId) {
      this.initSelection();
      if (!this.selectedActions.has(actionId)) return false;
      
      const boundSongs = this.getActionBindings(actionId);
      if (boundSongs.length === 0) return true; // 没有绑定歌曲时，只检查动画本身
      
      return boundSongs.every(songId => this.selectedSongs.has(songId));
    },

    // 歌曲Tab：只勾选歌曲本身，不联动
    toggleSongSelect(songId, checked) {
      this.initSelection();
      
      if (checked) {
        this.selectedSongs.add(songId);
      } else {
        this.selectedSongs.delete(songId);
      }
      
      this.refreshAllPanels();
    },

    // 动画Tab：只勾选动画本身，不联动
    toggleActionSelect(actionId, checked) {
      this.initSelection();
      
      if (checked) {
        this.selectedActions.add(actionId);
      } else {
        this.selectedActions.delete(actionId);
      }
      
      this.refreshAllPanels();
    },

    // 绑定Tab勾选歌曲：联动勾选/取消该歌曲绑定的所有动画
    toggleBindingSongSelect(songId, checked) {
      this.initSelection();
      
      if (checked) {
        this.selectedSongs.add(songId);
        // 联动勾选该歌曲绑定的所有动画
        const boundActions = this.getSongBindings(songId);
        boundActions.forEach(actionId => this.selectedActions.add(actionId));
      } else {
        this.selectedSongs.delete(songId);
        // 联动取消勾选该歌曲绑定的所有动画
        const boundActions = this.getSongBindings(songId);
        boundActions.forEach(actionId => this.selectedActions.delete(actionId));
      }
      
      this.refreshAllPanels();
    },

    // 绑定Tab勾选动画：联动勾选/取消该动画绑定的所有歌曲
    toggleBindingActionSelect(actionId, checked) {
      this.initSelection();
      
      if (checked) {
        this.selectedActions.add(actionId);
        // 联动勾选该动画绑定的所有歌曲
        const boundSongs = this.getActionBindings(actionId);
        boundSongs.forEach(songId => this.selectedSongs.add(songId));
      } else {
        this.selectedActions.delete(actionId);
        // 联动取消勾选该动画绑定的所有歌曲
        const boundSongs = this.getActionBindings(actionId);
        boundSongs.forEach(songId => this.selectedSongs.delete(songId));
      }
      
      this.refreshAllPanels();
    },

    // 歌曲Tab全选：只勾选歌曲本身
    toggleSelectAllSongs(checked) {
      this.initSelection();
      
      const showHidden = this.showHiddenSongs !== false;
      const songs = Object.entries(this.data.songs).filter(([id, song]) => showHidden || song.visible !== false);
      
      songs.forEach(([id]) => {
        if (checked) {
          this.selectedSongs.add(id);
        } else {
          this.selectedSongs.delete(id);
        }
      });
      
      this.refreshAllPanels();
    },
    
    // 动画Tab全选：只勾选动画本身
    toggleSelectAllActions(checked) {
      this.initSelection();
      
      Object.keys(this.data.actions).forEach(id => {
        if (checked) {
          this.selectedActions.add(id);
        } else {
          this.selectedActions.delete(id);
        }
      });
      
      this.refreshAllPanels();
    },

    // 绑定Tab全选歌曲（使用合集逻辑：只勾选满足条件的歌曲）
    toggleSelectAllBindingSongs(checked) {
      this.initSelection();
      
      Object.keys(this.data.songs).forEach(songId => {
        if (checked) {
          this.selectedSongs.add(songId);
        } else {
          this.selectedSongs.delete(songId);
        }
      });
      
      this.refreshAllPanels();
    },

    // 绑定Tab全选动画（使用合集逻辑：只勾选满足条件的动画）
    toggleSelectAllBindingActions(checked) {
      this.initSelection();
      
      Object.keys(this.data.actions).forEach(actionId => {
        if (checked) {
          this.selectedActions.add(actionId);
        } else {
          this.selectedActions.delete(actionId);
        }
      });
      
      this.refreshAllPanels();
    },

    // 刷新所有面板
    refreshAllPanels() {
      const songsPanel = document.querySelector('.songs-panel');
      if (songsPanel) this.renderSongs(songsPanel);
      
      const actionsPanel = document.querySelector('.actions-panel');
      if (actionsPanel) this.renderActions(actionsPanel);
      
      const bindingsPanel = document.querySelector('.bindings-panel');
      if (bindingsPanel) this.renderBindings(bindingsPanel);
      
      this.updateSelectionInfo();
    },
    
    updateSelectionInfo() {
      const infoEl = document.getElementById('sam-selection-info');
      if (!infoEl) return;

      const songCount = this.selectedSongs?.size || 0;
      const actionCount = this.selectedActions?.size || 0;

      let text = '';
      if (songCount > 0 || actionCount > 0) {
        const template = window.t('Jukebox.selectedInfo', '已选择 {{songCount}} 首歌曲，{{actionCount}} 个动画');
        text = template.replace('{{songCount}}', songCount).replace('{{actionCount}}', actionCount);
      }

      infoEl.textContent = text;
      
      // 切换导出按钮显示
      const hasSelection = songCount > 0 || actionCount > 0;
      const exportAllBtns = document.querySelectorAll('.sam-btn-export-all');
      const exportSelectedBtn = document.querySelector('.sam-btn-export-selected');
      
      exportAllBtns.forEach(btn => {
        btn.style.display = hasSelection ? 'none' : '';
      });
      if (exportSelectedBtn) {
        exportSelectedBtn.style.display = hasSelection ? '' : 'none';
      }
    },
    
    async exportAll(includeHidden) {
      try {
        const songIds = Object.keys(this.data.songs);
        const actionIds = Object.keys(this.data.actions);

        const formData = new FormData();
        formData.append('songIds', JSON.stringify(songIds));
        formData.append('actionIds', JSON.stringify(actionIds));
        formData.append('includeHidden', includeHidden);

        const response = await fetch(`${this.api.baseUrl}/export`, {
          method: 'POST',
          body: formData
        });

        if (!response.ok) {
          throw new Error(`导出失败: ${response.status}`);
        }

        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `jukebox_export_${new Date().toISOString().slice(0, 10)}.zip`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);

        console.log('[SongActionManager] 导出成功');
      } catch (error) {
        console.error('[SongActionManager] 导出失败:', error);
        alert(window.t('Jukebox.exportFailed', '导出失败') + ': ' + error.message);
      }
    },

    async exportSelected() {
      try {
        const songIds = Array.from(this.selectedSongs);
        const actionIds = Array.from(this.selectedActions);

        if (songIds.length === 0 && actionIds.length === 0) {
          alert(window.t('Jukebox.selectExportFirst', '请先选择要导出的歌曲或动画'));
          return;
        }

        const formData = new FormData();
        formData.append('songIds', JSON.stringify(songIds));
        formData.append('actionIds', JSON.stringify(actionIds));
        formData.append('includeHidden', 'true');

        const response = await fetch(`${this.api.baseUrl}/export`, {
          method: 'POST',
          body: formData
        });

        if (!response.ok) {
          throw new Error(`导出失败: ${response.status}`);
        }

        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `jukebox_selected_${new Date().toISOString().slice(0, 10)}.zip`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);

        console.log('[SongActionManager] 导出选中项成功');
      } catch (error) {
        console.error('[SongActionManager] 导出失败:', error);
        alert(window.t('Jukebox.exportFailed', '导出失败') + ': ' + error.message);
      }
    },

    getSongBindings(songId) {
      return Object.keys(this.data.bindings[songId] || {});
    },
    
    getActionBindings(actionId) {
      const songs = [];
      for (const [songId, actions] of Object.entries(this.data.bindings)) {
        if (actions[actionId]) {
          songs.push(songId);
        }
      }
      return songs;
    },

    // 显示手动添加绑定输入框（在+号位置）
    showAddBindingInput: function(btn, sourceId, sourceType) {
      const isSong = sourceType === 'song';
      const container = btn.parentElement;

      // 创建输入框
      const inputWrapper = document.createElement('span');
      inputWrapper.className = 'sam-add-binding-input-wrapper';
      inputWrapper.innerHTML = `
        <input type="text" class="sam-add-binding-input" placeholder="${window.t('Jukebox.inputIndexOrName', '输入序号或名称')}">
        <button class="sam-add-binding-confirm" title="${window.t('Jukebox.confirm', '确认')}">✓</button>
        <button class="sam-add-binding-cancel" title="${window.t('Jukebox.cancel', '取消')}">✕</button>
      `;

      // 替换按钮为输入框
      btn.style.display = 'none';
      container.appendChild(inputWrapper);

      // 获取可用项目（排除已绑定的）
      const availableItems = isSong ? this.data.actions : this.data.songs;
      const currentBindings = isSong
        ? (this.data.bindings[sourceId] || {})
        : this.getActionBindings(sourceId);
      const boundIds = new Set(isSong ? Object.keys(currentBindings) : currentBindings);

      // 获取所有项目的原始序号映射
      const allItemsWithIndex = Object.entries(availableItems)
        .map(([id, item], index) => ({ id, item, originalIndex: index + 1 }));

      // 过滤：只排除已绑定的项目（被隐藏的歌曲也可以绑定）
      const filteredItems = allItemsWithIndex
        .filter(({ id }) => !boundIds.has(id));

      // 创建自定义下拉列表（使用原始序号）
      const dropdown = document.createElement('div');
      dropdown.className = 'sam-add-binding-dropdown';
      dropdown.innerHTML = filteredItems.map(({ id, item, originalIndex }) =>
        `<div class="sam-add-binding-option" data-id="${id}">
          <span class="sam-add-binding-option-index">${originalIndex}</span>
          <span class="sam-add-binding-option-name">${item.name}</span>
        </div>`
      ).join('');

      // 将下拉列表添加到输入框下方
      inputWrapper.style.position = 'relative';
      inputWrapper.appendChild(dropdown);

      const input = inputWrapper.querySelector('.sam-add-binding-input');
      const confirmBtn = inputWrapper.querySelector('.sam-add-binding-confirm');
      const cancelBtn = inputWrapper.querySelector('.sam-add-binding-cancel');

      input.focus();

      // 通过序号、ID或名称查找项目
      const findItemByIndexOrName = (query, filteredItems, allItemsWithIndex) => {
        query = query.trim();
        if (!query) return null;

        // 先尝试匹配原始序号
        const index = parseInt(query, 10);
        if (!isNaN(index) && index > 0) {
          // 在所有项目中查找对应原始序号的项
          const itemByOriginalIndex = allItemsWithIndex.find(item => item.originalIndex === index);
          if (itemByOriginalIndex && !boundIds.has(itemByOriginalIndex.id)) {
            return itemByOriginalIndex.id;
          }
        }

        // 再尝试匹配名称（不区分大小写）
        const lowerQuery = query.toLowerCase();
        for (const { id, item } of filteredItems) {
          if (item.name.toLowerCase() === lowerQuery) return id;
        }

        // 最后尝试部分匹配名称
        for (const { id, item } of filteredItems) {
          if (item.name.toLowerCase().includes(lowerQuery)) return id;
        }

        return null;
      };

      // 确认绑定
      const doBind = () => {
        const query = input.value.trim();
        if (!query) {
          this.hideAddBindingInput(btn, inputWrapper);
          return;
        }

        const targetId = findItemByIndexOrName(query, filteredItems, allItemsWithIndex);

        if (!targetId) {
          input.style.borderColor = '#f44336';
          input.placeholder = isSong ? window.t('Jukebox.actionNotExist', '动画不存在') : window.t('Jukebox.songNotExist', '歌曲不存在');
          setTimeout(() => {
            input.style.borderColor = '';
            input.placeholder = window.t('Jukebox.inputIndexOrName', '输入序号或名称');
          }, 1500);
          return;
        }

        if (isSong) {
          this.bindSongToAction(sourceId, targetId);
        } else {
          this.bindSongToAction(targetId, sourceId);
        }
        this.hideAddBindingInput(btn, inputWrapper);
      };

      // 下拉列表选项点击事件
      dropdown.querySelectorAll('.sam-add-binding-option').forEach(option => {
        option.onclick = () => {
          const targetId = option.dataset.id;
          if (isSong) {
            this.bindSongToAction(sourceId, targetId);
          } else {
            this.bindSongToAction(targetId, sourceId);
          }
          this.hideAddBindingInput(btn, inputWrapper);
        };
      });

      // 输入时过滤下拉列表
      input.oninput = () => {
        const query = input.value.trim().toLowerCase();
        dropdown.querySelectorAll('.sam-add-binding-option').forEach(option => {
          const index = option.querySelector('.sam-add-binding-option-index').textContent;
          const name = option.querySelector('.sam-add-binding-option-name').textContent.toLowerCase();
          if (index === query || name.includes(query)) {
            option.style.display = 'flex';
          } else {
            option.style.display = 'none';
          }
        });
      };

      // 点击输入框显示下拉列表
      input.onclick = () => {
        dropdown.style.display = 'block';
      };

      confirmBtn.onclick = doBind;
      cancelBtn.onclick = () => this.hideAddBindingInput(btn, inputWrapper);

      input.onkeydown = (e) => {
        if (e.key === 'Enter') doBind();
        if (e.key === 'Escape') this.hideAddBindingInput(btn, inputWrapper);
      };

      // 点击外部关闭下拉列表
      document.addEventListener('click', function closeDropdown(e) {
        if (!inputWrapper.contains(e.target)) {
          dropdown.style.display = 'none';
          document.removeEventListener('click', closeDropdown);
        }
      });
    },

    hideAddBindingInput: function(btn, wrapper) {
      wrapper.remove();
      btn.style.display = 'flex';
    },
    
    bindDragEvents(panel) {
      const items = panel.querySelectorAll('.sam-item[draggable]');
      items.forEach(item => {
        item.addEventListener('dragstart', (e) => {
          e.dataTransfer.setData('text/plain', item.dataset.id);
          e.dataTransfer.setData('type', item.closest('.songs-panel') ? 'song' : 'action');
          item.classList.add('dragging');
        });
        
        item.addEventListener('dragend', () => {
          item.classList.remove('dragging');
        });
      });
    },
    
    bindBindingDragEvents(panel) {
      // 用于跟踪当前拖拽的类型和ID
      this._draggingType = null;
      this._draggingId = null;

      // 绑定可拖拽项的 dragstart 事件
      const songItems = panel.querySelectorAll('.sam-binding-item[data-song-id]');
      songItems.forEach(item => {
        item.addEventListener('dragstart', (e) => {
          this._draggingType = 'song';
          this._draggingId = item.dataset.songId;
          e.dataTransfer.setData('text/plain', item.dataset.songId);
          e.dataTransfer.setData('type', 'song');
          item.classList.add('dragging');
        });
        item.addEventListener('dragend', () => {
          this._draggingType = null;
          this._draggingId = null;
          item.classList.remove('dragging');
          // 清除所有高亮
          panel.querySelectorAll('.sam-binding-item').forEach(el => {
            el.classList.remove('drag-over', 'drag-over-duplicate');
          });
        });
      });

      const actionItems = panel.querySelectorAll('.sam-binding-item[data-action-id]');
      actionItems.forEach(item => {
        item.addEventListener('dragstart', (e) => {
          this._draggingType = 'action';
          this._draggingId = item.dataset.actionId;
          e.dataTransfer.setData('text/plain', item.dataset.actionId);
          e.dataTransfer.setData('type', 'action');
          item.classList.add('dragging');
        });
        item.addEventListener('dragend', () => {
          this._draggingType = null;
          this._draggingId = null;
          item.classList.remove('dragging');
          // 清除所有高亮
          panel.querySelectorAll('.sam-binding-item').forEach(el => {
            el.classList.remove('drag-over', 'drag-over-duplicate');
          });
        });
      });

      // 绑定放置区域 - 歌曲列表接收动画，动画列表接收歌曲
      const songsList = panel.querySelector('.songs-for-drop');
      const actionsList = panel.querySelector('.actions-for-drop');

      // 为歌曲项添加放置事件
      if (songsList) {
        const songItems = songsList.querySelectorAll('.sam-binding-item[data-song-id]');
        songItems.forEach(item => {
          item.addEventListener('dragover', (e) => {
            e.preventDefault();
            e.stopPropagation();
            
            // 只有拖拽的是动画时才处理
            if (this._draggingType !== 'action') return;
            
            const actionId = this._draggingId;
            const songId = item.dataset.songId;
            
            // 检查是否已绑定
            const isBound = this.data.bindings[songId]?.[actionId] !== undefined;
            
            // 清除之前的高亮
            item.classList.remove('drag-over', 'drag-over-duplicate');
            
            // 已绑定显示蓝色，未绑定显示绿色
            if (isBound) {
              item.classList.add('drag-over-duplicate');
            } else {
              item.classList.add('drag-over');
            }
          });
          
          item.addEventListener('dragleave', (e) => {
            e.stopPropagation();
            item.classList.remove('drag-over', 'drag-over-duplicate');
          });
          
          item.addEventListener('drop', async (e) => {
            e.preventDefault();
            e.stopPropagation();
            item.classList.remove('drag-over', 'drag-over-duplicate');

            if (this._draggingType !== 'action') return;

            const actionId = this._draggingId;
            const songId = item.dataset.songId;
            await this.bindSongToAction(songId, actionId);
          });
        });
      }

      // 为动画项添加放置事件
      if (actionsList) {
        const actionItems = actionsList.querySelectorAll('.sam-binding-item[data-action-id]');
        actionItems.forEach(item => {
          item.addEventListener('dragover', (e) => {
            e.preventDefault();
            e.stopPropagation();
            
            // 只有拖拽的是歌曲时才处理
            if (this._draggingType !== 'song') return;
            
            const songId = this._draggingId;
            const actionId = item.dataset.actionId;
            
            // 检查是否已绑定
            const isBound = this.data.bindings[songId]?.[actionId] !== undefined;
            
            // 清除之前的高亮
            item.classList.remove('drag-over', 'drag-over-duplicate');
            
            // 已绑定显示蓝色，未绑定显示绿色
            if (isBound) {
              item.classList.add('drag-over-duplicate');
            } else {
              item.classList.add('drag-over');
            }
          });
          
          item.addEventListener('dragleave', (e) => {
            e.stopPropagation();
            item.classList.remove('drag-over', 'drag-over-duplicate');
          });
          
          item.addEventListener('drop', async (e) => {
            e.preventDefault();
            e.stopPropagation();
            item.classList.remove('drag-over', 'drag-over-duplicate');

            if (this._draggingType !== 'song') return;

            const songId = this._draggingId;
            const actionId = item.dataset.actionId;
            await this.bindSongToAction(songId, actionId);
          });
        });
      }
    },
    
    bindFileDropEvents(panel, fileType) {
      const dropZone = panel.querySelector('.sam-file-drop-zone');
      if (!dropZone) return;

      ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
        dropZone.addEventListener(eventName, (e) => {
          e.preventDefault();
          e.stopPropagation();
        });
      });

      ['dragenter', 'dragover'].forEach(eventName => {
        dropZone.addEventListener(eventName, () => {
          dropZone.classList.add('drag-over');
        });
      });

      ['dragleave', 'drop'].forEach(eventName => {
        dropZone.addEventListener(eventName, () => {
          dropZone.classList.remove('drag-over');
        });
      });

      dropZone.addEventListener('drop', async (e) => {
        const files = Array.from(e.dataTransfer.files);

        if (fileType === 'audio') {
          const audioFiles = files.filter(f => f.name.toLowerCase().endsWith('.mp3'));
          if (audioFiles.length === 0) {
            console.log('[SongActionManager] 没有检测到 MP3 文件');
            return;
          }
          await this.uploadSongs(audioFiles);
        } else if (fileType === 'action') {
          const vmdFiles = files.filter(f => f.name.toLowerCase().endsWith('.vmd'));
          if (vmdFiles.length === 0) {
            console.log('[SongActionManager] 没有检测到 VMD 文件');
            return;
          }
          await this.uploadActions(vmdFiles);
        }
      });
    },

    async uploadSongs(files) {
      try {
        const metadata = files.map(f => ({
          name: f.name.replace(/\.mp3$/i, ''),
          artist: '未知'
        }));
        const result = await this.api.uploadSongs(files, metadata);
        console.log('[SongActionManager] 上传歌曲成功:', result);
        await this.load();
      } catch (error) {
        console.error('[SongActionManager] 上传歌曲失败:', error);
      }
    },

    async uploadActions(files) {
      try {
        const metadata = files.map(f => ({
          name: f.name.replace(/\.vmd$/i, '')
        }));
        const result = await this.api.uploadActions(files, metadata);
        console.log('[SongActionManager] 上传动画成功:', result);
        await this.load();
      } catch (error) {
        console.error('[SongActionManager] 上传动画失败:', error);
      }
    },

    async bindSongToAction(songId, actionId) {
      try {
        const result = await this.api.bind(songId, actionId, 0);
        this.data.bindings[songId] = this.data.bindings[songId] || {};
        this.data.bindings[songId][actionId] = { offset: 0 };
        
        // 如果后端返回了默认动画信息，更新本地数据
        if (result && result.defaultAction !== undefined) {
          this.data.songs[songId].defaultAction = result.defaultAction;
        } else {
          // 否则根据后端逻辑自动设置：如果没有默认动画，设为第一个绑定的动画
          const song = this.data.songs[songId];
          if (!song.defaultAction) {
            song.defaultAction = actionId;
          }
        }
        
        this.render();
        
        // 通知主UI重新加载配置，更新boundActions
        if (window.Jukebox && window.Jukebox.loadSongs) {
          console.log('[SongActionManager] 绑定后通知主UI重新加载配置');
          await window.Jukebox.loadSongs();
        }
      } catch (error) {
        console.error('[SongActionManager] 绑定失败:', error);
      }
    },
    
    async unbindSongFromAction(songId, actionId) {
      try {
        const result = await this.api.unbind(songId, actionId);
        if (this.data.bindings[songId]) {
          delete this.data.bindings[songId][actionId];
        }
        
        // 更新默认动画（如果后端返回了）
        if (result && result.defaultAction !== undefined) {
          this.data.songs[songId].defaultAction = result.defaultAction;
        } else {
          // 如果解绑的是当前默认动画，清除它
          const song = this.data.songs[songId];
          if (song.defaultAction === actionId) {
            song.defaultAction = '';
          }
        }
        
        this.render();
        
        // 通知主UI重新加载配置，更新boundActions
        if (window.Jukebox && window.Jukebox.loadSongs) {
          console.log('[SongActionManager] 解绑后通知主UI重新加载配置');
          await window.Jukebox.loadSongs();
        }
      } catch (error) {
        console.error('[SongActionManager] 解绑失败:', error);
      }
    },
    
    async exportConfig() {
      try {
        const blob = await this.api.export();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'jukebox-config.zip';
        a.click();
        URL.revokeObjectURL(url);
      } catch (error) {
        console.error('[SongActionManager] 导出失败:', error);
      }
    },
    
    // 显示底部状态提示
    showStatusHint(messages, duration = 5000) {
      const hintEl = document.getElementById('sam-unified-hint');
      if (!hintEl) {
        console.log('[SongActionManager] 提示元素未找到');
        return;
      }
      
      const normalEl = hintEl.querySelector('.sam-hint-normal');
      const statusEl = hintEl.querySelector('.sam-hint-status');
      
      if (normalEl && statusEl) {
        const text = messages.join(' · ');
        console.log('[SongActionManager] 显示提示:', text);
        statusEl.textContent = text;
        normalEl.style.display = 'none';
        statusEl.style.display = 'inline';
        
        // 清除之前的定时器
        if (this._statusHintTimer) {
          clearTimeout(this._statusHintTimer);
        }
        
        // 设置恢复定时器
        this._statusHintTimer = setTimeout(() => {
          normalEl.style.display = 'inline';
          statusEl.style.display = 'none';
        }, duration);
      } else {
        console.log('[SongActionManager] 提示子元素未找到', { normalEl, statusEl });
      }
    },

    async importConfig(file) {
      try {
        const result = await this.api.import(file);
        await this.load();

        // 显示导入结果
        const stats = result.stats || {};
        const messages = [window.t('Jukebox.importSuccess', '导入成功！')];
        if (stats.songsAdded) messages.push(`新增 ${stats.songsAdded} 首歌曲`);
        if (stats.songsMerged) messages.push(`合并 ${stats.songsMerged} 首歌曲`);
        if (stats.actionsAdded) messages.push(`新增 ${stats.actionsAdded} 个动画`);
        if (stats.actionsMerged) messages.push(`合并 ${stats.actionsMerged} 个动画`);
        if (stats.bindingsAdded) messages.push(`新增 ${stats.bindingsAdded} 个绑定`);
        
        if (messages.length === 1) {
          messages.push(window.t('Jukebox.noChanges', '无变化'));
        }

        this.showStatusHint(messages, 5000);
        console.log('[SongActionManager] 导入成功:', result);

        // 同步更新主UI
        if (window.Jukebox && typeof window.Jukebox.loadSongs === 'function') {
          await window.Jukebox.loadSongs();
        }
      } catch (error) {
        console.error('[SongActionManager] 导入失败:', error);
        this.showStatusHint([window.t('Jukebox.importFailed', '导入失败') + ': ' + error.message], 5000);
      }
    },
    
    // 显示统一文件选择器
    showUnifiedFilePicker() {
      const input = document.createElement('input');
      input.type = 'file';
      input.multiple = true;
      input.accept = '.mp3,.vmd,.zip,audio/*,video/*';
      input.onchange = async (e) => {
        if (e.target.files && e.target.files.length > 0) {
          await this.processFiles(Array.from(e.target.files));
        }
      };
      input.click();
    },

    // 处理文件（自动判断类型）
    processFiles: async function(files) {
      const songs = [];
      const actions = [];
      const zips = [];

      for (const file of files) {
        const ext = file.name.split('.').pop().toLowerCase();
        if (ext === 'mp3' || file.type.startsWith('audio/')) {
          songs.push(file);
        } else if (ext === 'vmd') {
          actions.push(file);
        } else if (ext === 'zip') {
          zips.push(file);
        }
      }

      // 处理歌曲
      if (songs.length > 0) {
        await this.importSongs(songs);
      }

      // 处理动作
      if (actions.length > 0) {
        await this.importActions(actions);
      }

      // 处理ZIP
      for (const zip of zips) {
        await this.importConfig(zip);
      }
    },

    // 导入歌曲文件
    importSongs: async function(files) {
      try {
        for (const file of files) {
          const formData = new FormData();
          formData.append('file', file);
          formData.append('name', file.name.replace(/\.[^/.]+$/, ''));

          const response = await fetch('/api/jukebox/upload', {
            method: 'POST',
            body: formData
          });

          if (!response.ok) {
            throw new Error(`上传失败: ${response.status}`);
          }

          const result = await response.json();
          if (result.success) {
            // 添加到数据
            this.data.songs[result.song_id] = {
              id: result.song_id,
              name: result.name,
              artist: result.artist || '',
              visible: true
            };
          }
        }
        this.render();
        // 通知主UI刷新
        if (window.Jukebox && window.Jukebox.loadSongs) {
          window.Jukebox.loadSongs();
        }
        console.log(`[SongActionManager] 成功导入 ${files.length} 首歌曲`);
      } catch (error) {
        console.error('[SongActionManager] 导入歌曲失败:', error);
        alert(window.t('Jukebox.importFailed', '导入失败') + ': ' + error.message);
      }
    },

    // 导入动作文件
    importActions: async function(files) {
      try {
        for (const file of files) {
          const formData = new FormData();
          formData.append('file', file);
          formData.append('name', file.name.replace(/\.[^/.]+$/, ''));
          formData.append('format', 'vmd');

          const response = await fetch('/api/jukebox/upload_action', {
            method: 'POST',
            body: formData
          });

          if (!response.ok) {
            throw new Error(`上传失败: ${response.status}`);
          }

          const result = await response.json();
          if (result.success) {
            // 添加到数据
            this.data.actions[result.action_id] = {
              id: result.action_id,
              name: result.name,
              format: result.format || 'vmd'
            };
          }
        }
        this.render();
        console.log(`[SongActionManager] 成功导入 ${files.length} 个动作`);
      } catch (error) {
        console.error('[SongActionManager] 导入动作失败:', error);
        alert(window.t('Jukebox.importFailed', '导入失败') + ': ' + error.message);
      }
    },

    // 绑定统一拖拽事件（整个窗口支持文件拖入导入）
    bindUnifiedDropEvents: function(panel) {
      // 用于跟踪拖拽状态，避免内部拖拽触发文件导入高亮
      this._isDraggingFiles = false;
      this._dragCounter = 0;

      // 只在从外部拖入文件时显示高亮
      panel.addEventListener('dragenter', (e) => {
        // 检查是否包含文件
        if (e.dataTransfer.types && e.dataTransfer.types.includes('Files')) {
          this._dragCounter++;
          this._isDraggingFiles = true;
          panel.classList.add('sam-file-drag-over');
        }
      });

      panel.addEventListener('dragleave', (e) => {
        this._dragCounter--;
        if (this._dragCounter <= 0) {
          this._dragCounter = 0;
          this._isDraggingFiles = false;
          panel.classList.remove('sam-file-drag-over');
        }
      });

      panel.addEventListener('dragover', (e) => {
        // 只允许文件拖入
        if (this._isDraggingFiles) {
          e.preventDefault();
          e.stopPropagation();
        }
      });

      panel.addEventListener('drop', async (e) => {
        this._dragCounter = 0;
        this._isDraggingFiles = false;
        panel.classList.remove('sam-file-drag-over');

        // 检查是否是文件拖入
        if (!e.dataTransfer.files || e.dataTransfer.files.length === 0) {
          return; // 不是文件，可能是内部拖拽，不处理
        }

        e.preventDefault();
        e.stopPropagation();

        const files = [];

        // 处理拖拽的文件
        if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
          for (const file of e.dataTransfer.files) {
            files.push(file);
          }
        }

        // 处理拖拽的文件夹
        const items = e.dataTransfer.items;
        if (items && items.length > 0) {
          for (const item of items) {
            const entry = item.webkitGetAsEntry();
            if (entry && entry.isDirectory) {
              await this.importFolder([item]);
              return;
            }
          }
        }

        if (files.length > 0) {
          await this.processFiles(files);
        }
      });
    },

    destroy: function() {
      if (this._panelDragCleanup) {
        this._panelDragCleanup();
        this._panelDragCleanup = null;
      }
      if (this.element) {
        this.element.remove();
        this.element = null;
      }
      this.isVisible = false;
      this.data = { songs: {}, actions: {}, bindings: {} };
    },

    // 绑定导入拖拽事件
    bindImportDropEvents: function(panel) {
      const footer = panel.querySelector('.sam-footer');
      if (!footer) return;

      footer.addEventListener('dragover', (e) => {
        e.preventDefault();
        e.stopPropagation();
        footer.classList.add('drag-over');
      });

      footer.addEventListener('dragleave', (e) => {
        e.preventDefault();
        e.stopPropagation();
        footer.classList.remove('drag-over');
      });

      footer.addEventListener('drop', async (e) => {
        e.preventDefault();
        e.stopPropagation();
        footer.classList.remove('drag-over');

        const items = e.dataTransfer.items;
        if (!items || items.length === 0) return;

        // 检查是否是 ZIP 文件
        const files = e.dataTransfer.files;
        if (files.length === 1 && files[0].name.endsWith('.zip')) {
          await this.importConfig(files[0]);
          return;
        }

        // 处理文件夹导入
        await this.importFolder(items);
      });
    },

    // 导入文件夹
    importFolder: async function(items) {
      try {
        const fileEntries = [];

        // 递归获取文件夹中的所有文件
        const getFiles = async (item, path = '') => {
          if (item.isFile) {
            return new Promise((resolve) => {
              item.file((file) => {
                fileEntries.push({
                  file: file,
                  path: path + file.name
                });
                resolve();
              });
            });
          } else if (item.isDirectory) {
            const reader = item.createReader();
            const entries = await new Promise((resolve) => {
              reader.readEntries((entries) => resolve(entries));
            });
            for (const entry of entries) {
              await getFiles(entry, path + item.name + '/');
            }
          }
        };

        for (const item of items) {
          const entry = item.webkitGetAsEntry();
          if (entry) {
            await getFiles(entry);
          }
        }

        if (fileEntries.length === 0) {
          alert(window.t('Jukebox.noImportFilesFound', '未找到可导入的文件'));
          return;
        }

        // 查找 config.json
        const configEntry = fileEntries.find(f => f.path.endsWith('config.json'));
        if (!configEntry) {
          alert(window.t('Jukebox.missingConfigJson', '文件夹中缺少 config.json 文件'));
          return;
        }

        // 创建 ZIP 文件
        const zipBlob = await this.createZipFromFiles(fileEntries);
        const zipFile = new File([zipBlob], 'import.zip', { type: 'application/zip' });

        await this.importConfig(zipFile);
      } catch (error) {
        console.error('[SongActionManager] 文件夹导入失败:', error);
        alert(window.t('Jukebox.folderImportFailed', '文件夹导入失败') + ': ' + error.message);
      }
    },

    // 从文件列表创建 ZIP
    createZipFromFiles: async function(fileEntries) {
      // 使用 JSZip 或类似库，这里简化处理，直接打包文件
      // 实际项目中应该使用 JSZip 库
      const formData = new FormData();

      for (const entry of fileEntries) {
        formData.append('files', entry.file, entry.path);
      }

      // 发送到后端打包
      const response = await fetch(`${this.api.baseUrl}/pack-folder`, {
        method: 'POST',
        body: formData
      });

      if (!response.ok) {
        throw new Error('打包文件夹失败');
      }

      return await response.blob();
    },
    
    getStyles: function() {
      const C = this.Config;
      const FC = C.formatColors;
      return `
        .jukebox-sam-panel {
          position: fixed;
          z-index: 100010;
          background: ${C.panel.background};
          color: ${C.panel.color};
          padding: 15px;
          border-radius: 12px;
          width: 450px;
          max-height: 500px;
          overflow: hidden;
          display: flex;
          flex-direction: column;
          box-sizing: border-box;
          border: 2px solid transparent;
          transition: border-color 0.3s, box-shadow 0.3s;
          pointer-events: auto;
        }

        .jukebox-sam-panel.sam-file-drag-over {
          border-color: ${C.functional.success};
          box-shadow: 0 0 0 4px ${C.functional.successBg};
        }

        .sam-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 10px;
          padding-bottom: 10px;
          border-bottom: 1px solid ${C.tabs.borderBottom};
          gap: 10px;
          cursor: grab;
          user-select: none;
          -webkit-user-select: none;
        }

        .sam-header:active {
          cursor: grabbing;
        }

        body.sam-panel-dragging {
          user-select: none;
          -webkit-user-select: none;
          cursor: grabbing !important;
        }

        body.sam-panel-dragging .jukebox-sam-panel {
          transition: none !important;
          opacity: 0.9;
        }

        .sam-title {
          font-size: 16px;
          font-weight: 600;
          flex-shrink: 0;
        }

        .sam-close-btn {
          background: none;
          border: none;
          color: ${C.panel.color};
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
          flex-shrink: 0;
        }

        .sam-close-btn:hover {
          background: ${C.item.hoverBg};
        }

        .sam-tabs {
          display: flex;
          gap: 5px;
          flex: 1;
          justify-content: center;
        }

        .sam-tab {
          background: none;
          border: none;
          color: ${C.tabs.tabColor};
          padding: 5px 10px;
          cursor: pointer;
          border-radius: 4px;
          transition: all 0.3s;
        }

        .sam-tab:hover {
          color: ${C.text.primary};
          background: ${C.tabs.tabHoverBg};
        }

        .sam-tab.active {
          color: ${C.text.primary};
          background: ${C.tabs.tabActiveBg};
        }
        
        .sam-content {
          flex: 1;
          overflow-y: auto;
          min-height: 0;
        }

        .sam-panel {
          display: none;
          height: 100%;
          overflow-y: auto;
        }

        .sam-panel.active {
          display: block;
        }
        
        .sam-list {
          display: flex;
          flex-direction: column;
          gap: 8px;
          overflow-y: auto;
          flex: 1;
          min-height: 0;
        }
        
        .sam-file-drop-zone {
          border: 2px dashed ${C.borders.dashed};
          border-radius: 8px;
          padding: 8px;
          min-height: 80px;
          max-height: 120px;
          overflow-y: auto;
          transition: all 0.3s;
          display: flex;
          flex-direction: column;
          cursor: pointer;
          margin-bottom: 8px;
        }

        .sam-file-drop-zone:hover {
          border-color: ${C.functional.success};
          background: ${C.functional.successBg.replace('0.1', '0.05')};
        }

        .sam-file-drop-zone.drag-over {
          border-color: ${C.functional.success};
          background: ${C.functional.successBg};
        }

        .sam-drop-hint {
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          padding: 10px 8px;
          color: ${C.text.placeholder};
          text-align: center;
          flex-shrink: 0;
        }

        .sam-drop-icon {
          font-size: 24px;
          margin-bottom: 6px;
        }

        .sam-add-hint {
          cursor: pointer;
          transition: all 0.3s;
        }

        .sam-add-hint:hover {
          background: ${C.item.hoverBg};
          border-radius: 6px;
        }

        .sam-add-hint-text {
          font-size: 14px;
          font-weight: 600;
          color: ${C.functional.success};
          margin-top: 4px;
        }

        .sam-item {
          background: ${C.item.background};
          padding: 10px;
          border-radius: 6px;
          cursor: grab;
          transition: all 0.3s;
        }

        .sam-item:hover {
          background: ${C.item.hoverBg};
        }

        .sam-item.dragging {
          opacity: ${C.item.draggingOpacity};
          transform: scale(1.02);
        }

        .sam-item-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 5px;
        }

        .sam-item-format {
          font-size: 11px;
          color: ${C.text.muted};
          background: ${C.borders.itemFormatBg};
          padding: 2px 6px;
          border-radius: 3px;
        }

        .sam-missing-badge {
          font-size: 10px;
          color: ${C.functional.missing};
          background: ${C.functional.missingBg};
          padding: 2px 6px;
          border-radius: 3px;
        }

        .sam-item-bindings {
          display: flex;
          flex-wrap: wrap;
          gap: 4px;
        }

        .sam-binding-tag {
          font-size: 10px;
          color: ${C.text.secondary};
          background: ${C.functional.tagBg};
          padding: 2px 6px;
          border-radius: 10px;
        }
        
        /* 动画标签样式 - 不同格式不同颜色 */
        .sam-action-tag {
          cursor: pointer;
          transition: all 0.2s;
          user-select: none;
        }
        
        .sam-action-tag:hover {
          transform: scale(1.05);
        }
        
        /* VMD格式 - 蓝色 */
        .sam-action-tag-vmd {
          background: ${FC.vmd.bg} !important;
          border: 1px solid ${FC.vmd.border};
        }

        .sam-action-tag-vmd:hover {
          background: ${FC.vmd.bgHover} !important;
        }

        /* VRMA格式 - 绿色 */
        .sam-action-tag-vrma {
          background: ${FC.vrma.bg} !important;
          border: 1px solid ${FC.vrma.border};
        }

        .sam-action-tag-vrma:hover {
          background: ${FC.vrma.bgHover} !important;
        }

        /* BVH格式 - 橙色 */
        .sam-action-tag-bvh {
          background: ${FC.bvh.bg} !important;
          border: 1px solid ${FC.bvh.border};
        }

        .sam-action-tag-bvh:hover {
          background: ${FC.bvh.bgHover} !important;
        }

        /* FBX格式 - 紫色 */
        .sam-action-tag-fbx {
          background: ${FC.fbx.bg} !important;
          border: 1px solid ${FC.fbx.border};
        }

        .sam-action-tag-fbx:hover {
          background: ${FC.fbx.bgHover} !important;
        }

        /* 其他格式 - 灰色 */
        .sam-action-tag-other {
          background: ${FC.default.bg} !important;
          border: 1px solid ${FC.default.border};
        }

        .sam-action-tag-other:hover {
          background: ${FC.default.bgHover} !important;
        }

        /* 默认动画 - 高亮效果（对应颜色的更亮版本，无金色边框） */
        .sam-action-tag-default {
          font-weight: bold;
        }

        .sam-action-tag-default.sam-action-tag-vmd {
          background: ${FC.vmd.bgDefault} !important;
          border-color: ${FC.vmd.borderDefault};
        }

        .sam-action-tag-default.sam-action-tag-vrma {
          background: ${FC.vrma.bgDefault} !important;
          border-color: ${FC.vrma.borderDefault};
        }

        .sam-action-tag-default.sam-action-tag-bvh {
          background: ${FC.bvh.bgDefault} !important;
          border-color: ${FC.bvh.borderDefault};
        }

        .sam-action-tag-default.sam-action-tag-fbx {
          background: ${FC.fbx.bgDefault} !important;
          border-color: ${FC.fbx.borderDefault};
        }
        
        .sam-empty {
          text-align: center;
          color: ${C.text.empty};
          padding: 20px;
        }

        .sam-add-btn {
          width: 100%;
          margin-top: 10px;
          padding: 10px;
          background: ${C.functional.successHoverBg};
          border: 1px dashed ${C.functional.success};
          color: ${C.text.primary};
          border-radius: 6px;
          cursor: pointer;
          transition: all 0.3s;
        }

        .sam-add-btn:hover {
          background: ${C.functional.successHoverBg.replace('0.3', '0.5')};
        }

        .sam-bindings-container {
          display: flex;
          gap: 15px;
        }

        .sam-bindings-section {
          flex: 1;
        }

        .sam-bindings-section h4 {
          margin: 0 0 10px 0;
          font-size: 13px;
          color: ${C.text.secondary};
        }

        .sam-bindings-list {
          display: flex;
          flex-direction: column;
          gap: 8px;
          max-height: 200px;
          overflow-y: auto;
          padding: 10px;
          border: 2px dashed transparent;
          border-radius: 8px;
          transition: all 0.3s;
        }

        .sam-bindings-list.drag-over {
          border-color: ${C.functional.success};
          background: ${C.functional.successBg};
        }

        .sam-binding-item {
          background: ${C.item.background};
          padding: 8px;
          border-radius: 6px;
          position: relative;
          cursor: grab;
          transition: all 0.3s;
          min-height: 40px;
          display: flex;
          flex-direction: column;
          gap: 4px;
        }

        .sam-binding-item:hover {
          background: ${C.item.hoverBg};
        }

        .sam-binding-item.dragging {
          opacity: ${C.item.draggingOpacity};
          cursor: grabbing;
        }

        .sam-binding-item.drag-over {
          border: 2px solid ${C.functional.success};
          background: ${C.functional.successBg.replace('0.1', '0.2')};
          transform: scale(1.02);
        }

        .sam-binding-item.drag-over-duplicate {
          border: 2px solid ${C.buttons.primary.bg};
          background: ${C.buttons.primary.bg.replace(')', ', 0.2)')};
          transform: scale(1.02);
        }

        .sam-binding-item-main {
          display: flex;
          align-items: center;
          gap: 8px;
          width: 100%;
        }

        .sam-binding-item-index {
          font-size: 11px;
          color: ${C.text.muted};
          background: ${C.borders.itemFormatBg};
          padding: 2px 6px;
          border-radius: 4px;
          min-width: 20px;
          text-align: center;
          flex-shrink: 0;
        }

        .sam-binding-item-name {
          font-weight: 500;
          flex: 1;
        }

        .sam-binding-count {
          font-size: 11px;
          color: ${C.text.primary};
          background: ${C.functional.countBg};
          padding: 2px 6px;
          border-radius: 10px;
          min-width: 18px;
          text-align: center;
        }

        .sam-binding-item-tags {
          display: flex;
          flex-wrap: wrap;
          gap: 4px;
          margin-top: 4px;
          padding-top: 4px;
          border-top: 1px solid ${C.borders.divider};
        }

        .sam-add-binding-btn {
          width: 20px;
          height: 20px;
          border-radius: 50%;
          border: 1px dashed ${C.borders.dashed};
          background: transparent;
          color: ${C.text.muted};
          font-size: 14px;
          line-height: 1;
          cursor: pointer;
          display: flex;
          align-items: center;
          justify-content: center;
          transition: all 0.2s;
        }

        .sam-add-binding-btn:hover {
          border-color: ${C.borders.solid};
          color: ${C.text.primary};
          background: ${C.tabs.tabHoverBg};
        }

        .sam-add-binding-input-wrapper {
          display: inline-flex;
          align-items: center;
          gap: 2px;
        }

        .sam-add-binding-input {
          width: 80px;
          height: 20px;
          padding: 0 4px;
          font-size: 11px;
          border: 1px solid ${C.borders.solid};
          border-radius: 3px;
          background: rgba(0,0,0,0.5);
          color: ${C.text.primary};
          outline: none;
        }

        .sam-add-binding-input:focus {
          border-color: ${C.borders.solid};
        }

        .sam-add-binding-confirm,
        .sam-add-binding-cancel {
          width: 18px;
          height: 18px;
          border: none;
          border-radius: 3px;
          font-size: 10px;
          cursor: pointer;
          display: flex;
          align-items: center;
          justify-content: center;
          padding: 0;
        }

        .sam-add-binding-confirm {
          background: ${C.functional.confirmBg};
          color: ${C.text.primary};
        }

        .sam-add-binding-confirm:hover {
          background: ${C.functional.confirmHoverBg};
        }

        .sam-add-binding-cancel {
          background: ${C.functional.cancelBg};
          color: ${C.text.primary};
        }

        .sam-add-binding-cancel:hover {
          background: ${C.functional.cancelHoverBg};
        }

        .sam-add-binding-dropdown {
          position: absolute;
          top: 100%;
          left: 0;
          min-width: 200px;
          max-height: 200px;
          overflow-y: auto;
          background: ${C.functional.dropdownBg};
          border: 1px solid ${C.borders.solid};
          border-radius: 4px;
          z-index: 1000;
          display: none;
          margin-top: 2px;
        }

        .sam-add-binding-option {
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 6px 10px;
          cursor: pointer;
          transition: background 0.2s;
          border-bottom: 1px solid ${C.borders.divider};
        }

        .sam-add-binding-option:last-child {
          border-bottom: none;
        }

        .sam-add-binding-option:hover {
          background: ${C.tabs.tabHoverBg};
        }

        .sam-add-binding-option-index {
          font-size: 11px;
          color: ${C.text.primary};
          background: ${C.functional.countBg};
          padding: 2px 6px;
          border-radius: 10px;
          min-width: 18px;
          text-align: center;
          white-space: nowrap;
        }

        .sam-add-binding-option-name {
          font-size: 12px;
          color: ${C.text.primary};
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }

        .sam-binding-tag-small {
          font-size: 10px;
          color: ${C.text.secondary};
          background: ${C.functional.tagBg};
          padding: 2px 6px;
          border-radius: 10px;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
          max-width: 140px;
          display: inline-flex;
          align-items: center;
          gap: 4px;
          position: relative;
        }

        .sam-unbind-btn {
          width: 14px;
          height: 14px;
          border-radius: 50%;
          background: ${C.functional.danger};
          border: none;
          color: ${C.text.primary};
          font-size: 10px;
          font-weight: bold;
          cursor: pointer;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          padding: 0;
          line-height: 1;
          transition: all 0.2s;
          flex-shrink: 0;
          opacity: 0;
          visibility: hidden;
        }

        .sam-binding-tag-small:hover .sam-unbind-btn {
          opacity: 1;
          visibility: visible;
        }

        .sam-unbind-btn:hover {
          background: ${C.functional.dangerHover};
          transform: scale(1.1);
        }

        /* 绑定页面动画标签格式颜色 */
        .sam-action-tag-small {
          display: inline-flex;
          align-items: center;
          gap: 4px;
          cursor: pointer;
          transition: all 0.2s;
        }

        .sam-action-tag-small:hover {
          transform: scale(1.05);
        }

        .sam-format-dot {
          width: 8px;
          height: 8px;
          border-radius: 50%;
          display: inline-block;
          flex-shrink: 0;
        }

        /* VMD格式 */
        .sam-action-tag-small-vmd {
          background: ${FC.vmd.smallBg} !important;
          border: 1px solid ${FC.vmd.smallBorder};
        }

        /* VRMA格式 */
        .sam-action-tag-small-vrma {
          background: ${FC.vrma.smallBg} !important;
          border: 1px solid ${FC.vrma.smallBorder};
        }

        /* BVH格式 */
        .sam-action-tag-small-bvh {
          background: ${FC.bvh.smallBg} !important;
          border: 1px solid ${FC.bvh.smallBorder};
        }

        /* FBX格式 */
        .sam-action-tag-small-fbx {
          background: ${FC.fbx.smallBg} !important;
          border: 1px solid ${FC.fbx.smallBorder};
        }

        /* 其他格式 */
        .sam-action-tag-small-other {
          background: ${FC.default.smallBg} !important;
          border: 1px solid ${FC.default.smallBorder};
        }

        /* 默认动画高亮 - 使用对应颜色的更亮版本，无金色边框 */
        .sam-action-tag-small-default {
          font-weight: bold;
        }

        .sam-action-tag-small-default.sam-action-tag-small-vmd {
          background: ${FC.vmd.smallBgDefault} !important;
          border-color: ${FC.vmd.borderDefault};
        }

        .sam-action-tag-small-default.sam-action-tag-small-vrma {
          background: ${FC.vrma.smallBgDefault} !important;
          border-color: ${FC.vrma.borderDefault};
        }

        .sam-action-tag-small-default.sam-action-tag-small-bvh {
          background: ${FC.bvh.smallBgDefault} !important;
          border-color: ${FC.bvh.borderDefault};
        }

        .sam-action-tag-small-default.sam-action-tag-small-fbx {
          background: ${FC.fbx.smallBgDefault} !important;
          border-color: ${FC.fbx.borderDefault};
        }

        .sam-drop-zone {
          position: absolute;
          top: 0;
          left: 0;
          right: 0;
          bottom: 0;
          border: 2px dashed transparent;
          border-radius: 6px;
          transition: all 0.3s;
          pointer-events: auto;
        }

        .sam-drop-zone.drag-over {
          border-color: ${C.functional.success};
          background: ${C.functional.successBg.replace('0.1', '0.2')};
          z-index: 10;
        }
        
        .sam-import-container {
          display: flex;
          flex-direction: column;
          height: 100%;
        }
        
        .sam-import-sections {
          display: flex;
          gap: 20px;
          flex: 1;
          overflow: hidden;
        }
        
        .sam-import-section {
          flex: 1;
          display: flex;
          flex-direction: column;
          background: rgba(0,0,0,0.2);
          border-radius: 8px;
          overflow: hidden;
        }
        
        .sam-import-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          padding: 12px 16px;
          background: rgba(255,255,255,0.05);
          border-bottom: 1px solid rgba(255,255,255,0.1);
        }
        
        .sam-import-header h4 {
          margin: 0;
          font-size: 14px;
          color: rgba(255,255,255,0.9);
        }
        
        .sam-import-list {
          flex: 1;
          overflow-y: auto;
          padding: 8px;
        }
        
        .sam-import-item {
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 8px 12px;
          border-radius: 6px;
          cursor: pointer;
          transition: all 0.2s;
        }
        
        .sam-import-item:hover {
          background: ${C.input.hoverBg};
        }

        .sam-import-item-name {
          flex: 1;
          font-size: 13px;
          color: ${C.text.secondary};
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }

        .sam-import-checkbox {
          width: 16px;
          height: 16px;
          cursor: pointer;
        }

        .sam-list-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          padding: 8px 12px;
          background: ${C.borders.itemFormatBg};
          border-bottom: ${C.borders.divider};
          margin-bottom: 8px;
        }

        .sam-item-hidden {
          opacity: 0.5;
        }

        .sam-item-hidden .sam-item-name,
        .sam-item-hidden .sam-item-artist {
          color: ${C.buttons.visibility.hiddenColor} !important;
        }
        
        .sam-item-header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 8px;
        }
        
        .sam-item-name {
          flex: 1;
          font-weight: 500;
          cursor: text;
          padding: 2px 4px;
          border-radius: 3px;
          transition: all 0.2s;
        }
        
        .sam-item-name:hover {
          background: ${C.input.hoverBg};
        }

        .sam-item-name:focus {
          background: ${C.input.focusBg};
          outline: none;
        }

        .sam-item-artist {
          font-size: 12px;
          color: ${C.text.muted};
          cursor: text;
          padding: 2px 4px;
          border-radius: 3px;
          transition: all 0.2s;
          margin-top: 4px;
        }

        .sam-item-artist:hover {
          background: ${C.input.hoverBg};
        }

        .sam-item-artist:focus {
          background: ${C.input.focusBg};
          outline: none;
        }
        
        .sam-item-actions {
          display: flex;
          align-items: center;
          gap: 4px;
        }
        
        .sam-visibility-btn {
          width: 24px;
          height: 24px;
          border: none;
          background: transparent;
          cursor: pointer;
          font-size: 14px;
          border-radius: 4px;
          transition: all 0.2s;
          display: flex;
          align-items: center;
          justify-content: center;
          color: ${C.buttons.visibility.color};
        }

        .sam-visibility-btn:hover {
          background: ${C.buttons.visibility.hoverBg};
          color: ${C.buttons.visibility.hoverColor};
        }

        .sam-visibility-btn.hidden {
          color: ${C.buttons.visibility.hiddenColor};
        }

        .sam-delete-btn {
          width: 24px;
          height: 24px;
          border: none;
          background: transparent;
          cursor: pointer;
          font-size: 14px;
          border-radius: 4px;
          transition: all 0.2s;
          color: ${C.buttons.delete.color};
        }

        .sam-delete-btn:hover {
          background: ${C.buttons.delete.hoverBg};
        }

        .sam-checkbox {
          display: flex;
          align-items: center;
          gap: 6px;
          cursor: pointer;
          font-size: 12px;
          color: ${C.text.muted};
        }

        .sam-checkbox input {
          width: 14px;
          height: 14px;
          cursor: pointer;
        }

        .sam-checkbox-right {
          margin-left: auto;
        }

        .sam-item-checkbox {
          margin-right: 4px;
        }
        
        .sam-item-selected {
          background: ${C.selected.bg} !important;
          border-left: ${C.selected.border};
        }

        .sam-footer {
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 8px;
          padding: 12px 16px;
          background: ${C.footer.importBg};
          border-top: ${C.footer.borderTop};
        }

        .sam-footer-buttons {
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 12px;
        }

        .sam-selection-info {
          font-size: 12px;
          color: ${C.footer.hintColor};
          min-height: 16px;
        }

        .sam-import-hint {
          font-size: 11px;
          color: ${C.footer.shortcutColor};
          text-align: center;
        }

        .sam-footer.drag-over {
          background: ${C.dropzone.overBg};
          border-top: ${C.dropzone.overBorder};
        }

        .sam-unified-hint {
          font-size: 11px;
          color: ${C.footer.shortcutColor};
          text-align: center;
          padding: 4px 0;
          min-height: 20px;
        }

        .sam-hint-normal {
          display: inline;
        }

        .sam-hint-status {
          display: none;
          color: ${C.functional.success};
          font-weight: 500;
        }

        .sam-click-add {
          color: ${C.functional.success};
          cursor: pointer;
          transition: color 0.3s;
        }

        .sam-click-add:hover {
          color: ${C.functional.success};
          text-decoration: underline;
        }

        .sam-import-footer {
          display: flex;
          justify-content: center;
          gap: 16px;
          padding: 16px;
          background: ${C.footer.bg};
          border-top: ${C.footer.borderTop};
        }

        .sam-btn {
          padding: 8px 16px;
          background: ${C.footer.buttonBg};
          border: none;
          color: ${C.text.primary};
          border-radius: 4px;
          cursor: pointer;
          transition: all 0.3s;
        }

        .sam-btn:hover {
          background: ${C.footer.buttonHoverBg};
        }

        .sam-btn-primary {
          background: ${C.buttons.primary.bg};
        }

        .sam-btn-primary:hover {
          background: ${C.buttons.primary.hoverBg};
        }
      `;
    }
  },
  
  init: function() {
    console.log('[Jukebox]', window.t('Jukebox.initialized', '初始化点歌台...'));

    window.Jukebox_playSong = Jukebox.playSong;
    window.Jukebox_close = Jukebox.close;
    window.Jukebox_hide = Jukebox.hide;
    window.Jukebox_updateVolume = Jukebox.updateVolume;
    window.Jukebox_logVolumeChange = Jukebox.logVolumeChange;
    window.Jukebox_togglePause = Jukebox.togglePause;

    // 独立窗口模式：不需要绑定按钮和监听聊天框关闭
    if (!window.__NEKO_JUKEBOX_STANDALONE__) {
      Jukebox.setupButton();
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
    if (Jukebox.State.observer) return;

    const toggleChatBtn = document.getElementById('toggle-chat-btn');
    if (toggleChatBtn) {
      toggleChatBtn.addEventListener('click', () => {
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
      });
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
    if (Jukebox.State.isOpen) return;
    
    Jukebox.buildUI();
    
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
  
  close: function() {
    Jukebox.stopPlayback();
    
    // 销毁管理器面板（移除 DOM 节点 + 清理拖拽监听）
    Jukebox.SongActionManager.destroy();
    
    // 清理点歌台拖拽事件监听
    if (Jukebox.State._dragCleanup) {
      Jukebox.State._dragCleanup();
      Jukebox.State._dragCleanup = null;
    }
    
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
  },
  
  destroy: function() {
    Jukebox.stopPlayback();
    
    Jukebox.SongActionManager.destroy();
    
    // 清理拖拽事件监听
    if (Jukebox.State._dragCleanup) {
      Jukebox.State._dragCleanup();
      Jukebox.State._dragCleanup = null;
    }
    
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
    Jukebox.State.songElements = {}; // 清空元素映射

    console.log('[Jukebox] 点歌台已销毁');
  },
  
  buildUI: function() {
    const wrapper = document.createElement('div');
    wrapper.className = 'jukebox-wrapper';
    
    const sidePanel = Jukebox.SongActionManager.create();
    
    const jukeboxContainer = document.createElement('div');
    jukeboxContainer.className = 'jukebox-container';
    jukeboxContainer.innerHTML = `
      <div class="jukebox-header">
        <div class="jukebox-header-left">
          <h3>${window.t('Jukebox.title', '点歌台')}</h3>
          <span id="jukebox-status-text" class="jukebox-status-text">${window.t('Jukebox.ready', '准备就绪')}</span>
        </div>
        <div class="jukebox-header-buttons">
          <button class="jukebox-settings" onclick="Jukebox.SongActionManager.toggle()" title="${window.t('Jukebox.manager', '管理器')}">⚙</button>
          <button class="jukebox-minimize" onclick="Jukebox_hide()" title="${window.t('Jukebox.minimize', '最小化')}">−</button>
          <button class="jukebox-close" onclick="Jukebox_close()" title="${window.t('Jukebox.close', '关闭')}">×</button>
        </div>
      </div>
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
          <thead>
            <tr>
              <th>${window.t('Jukebox.sequence', '序号')}</th>
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
        <div class="jukebox-volume-wrapper">
          <button class="jukebox-speaker-btn" id="jukebox-speaker-btn" title="${window.t('Jukebox.mute', '静音')}">
            <svg class="speaker-icon" viewBox="0 0 24 24" width="20" height="20">
              <path fill="${Jukebox.Config.volume.iconColor}" d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z"/>
            </svg>
            <svg class="speaker-muted-icon" viewBox="0 0 24 24" width="20" height="20" style="display:none;">
              <path fill="${Jukebox.Config.volume.iconColor}" d="M16.5 12c0-1.77-1.02-3.29-2.5-4.03v2.21l2.45 2.45c.03-.2.05-.41.05-.63zm2.5 0c0 .94-.2 1.82-.54 2.64l1.51 1.51C20.63 14.91 21 13.5 21 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89.86 5 3.54 5 6.71zM4.27 3L3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06c1.38-.31 2.63-.95 3.69-1.81L19.73 21 21 19.73l-9-9L4.27 3zM12 4L9.91 6.09 12 8.18V4z"/>
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
    `;
    
    wrapper.appendChild(jukeboxContainer);
    document.body.appendChild(wrapper);
    document.body.appendChild(sidePanel);
    Jukebox.State.container = wrapper;
    
    // 独立窗口模式由 preload 注入 -webkit-app-region: drag 处理拖拽，
    // JS 拖拽会因 preventDefault 与原生拖拽冲突，且 inset:0!important 阻止 wrapper 移动
    if (!window.__NEKO_JUKEBOX_STANDALONE__) {
      Jukebox.bindWindowDrag(wrapper, jukeboxContainer);
      Jukebox.bindPanelDrag(sidePanel);
    }
    
    Jukebox.injectStyles();
  },
  
  // 窗口拖拽功能
  bindWindowDrag: function(wrapper, container) {
    const header = container.querySelector('.jukebox-header');
    if (!header) return;

    const onMouseDown = (e) => {
      // 忽略按钮点击
      if (e.target.closest('.jukebox-header-buttons')) return;
      
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

    header.addEventListener('mousedown', onMouseDown);
    header.addEventListener('touchstart', onMouseDown, { passive: false });
    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('touchmove', onMouseMove, { passive: false });
    document.addEventListener('mouseup', onMouseUp);
    document.addEventListener('touchend', onMouseUp);
    
    // 保存引用，方便 destroy 时清理
    Jukebox.State._dragCleanup = () => {
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('touchmove', onMouseMove);
      document.removeEventListener('mouseup', onMouseUp);
      document.removeEventListener('touchend', onMouseUp);
      Jukebox.State.isDragging = false;
      document.body.classList.remove('jukebox-dragging');
    };
  },
  
  // 管理器面板拖拽功能
  bindPanelDrag: function(panel) {
    const header = panel.querySelector('.sam-header');
    if (!header) return;

    let isDragging = false;
    let dragOffset = { x: 0, y: 0 };

    const onMouseDown = (e) => {
      // 忽略按钮和 tab 点击
      if (e.target.closest('.sam-close-btn') || e.target.closest('.sam-tab')) return;
      
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

    header.addEventListener('mousedown', onMouseDown);
    header.addEventListener('touchstart', onMouseDown, { passive: false });
    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('touchmove', onMouseMove, { passive: false });
    document.addEventListener('mouseup', onMouseUp);
    document.addEventListener('touchend', onMouseUp);
    
    Jukebox.SongActionManager._panelDragCleanup = () => {
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('touchmove', onMouseMove);
      document.removeEventListener('mouseup', onMouseUp);
      document.removeEventListener('touchend', onMouseUp);
      isDragging = false;
      document.body.classList.remove('sam-panel-dragging');
    };
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

      ${Jukebox.SongActionManager.getStyles()}

      .jukebox-container {
        width: ${Jukebox.Config.width};
        max-height: 500px;
        background: ${Jukebox.Config.container.background};
        border-radius: 12px;
        box-shadow: ${Jukebox.Config.container.boxShadow};
        color: ${Jukebox.Config.container.color};
        padding: 20px;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
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
        border-bottom: ${Jukebox.Config.header.borderBottom};
        cursor: grab;
        user-select: none;
        -webkit-user-select: none;
      }
      
      .jukebox-header:active {
        cursor: grabbing;
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
      
      .jukebox-header-left {
        display: flex;
        align-items: center;
        gap: 12px;
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

      .jukebox-status-text {
        font-size: 13px;
        color: ${Jukebox.Config.status.color};
        background: ${Jukebox.Config.status.bg};
        padding: 3px 10px;
        border-radius: 12px;
      }

      .jukebox-calibration-section {
        margin-bottom: 12px;
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
        margin-bottom: 12px;
        font-size: 12.5px;
        line-height: 1.6;
      }

      .jukebox-notice-item {
        padding: 2px 0;
      }

      .jukebox-settings {
        background: none;
        border: none;
        color: ${Jukebox.Config.container.color};
        font-size: 20px;
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

      .jukebox-settings:hover {
        background: ${Jukebox.Config.header.btnHoverBg};
      }

      .jukebox-minimize {
        background: none;
        border: none;
        color: ${Jukebox.Config.container.color};
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
        background: ${Jukebox.Config.header.btnHoverBg};
      }

      .jukebox-close {
        background: none;
        border: none;
        color: ${Jukebox.Config.container.color};
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
        background: ${Jukebox.Config.header.btnHoverBg};
      }
      
      .jukebox-content {
        flex: 1;
        overflow-y: auto;
        min-height: 0;
      }
      
      .jukebox-table {
        width: 100%;
        border-collapse: collapse;
        background: ${Jukebox.Config.table.bodyBg};
        border-radius: 8px;
        overflow: hidden;
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
      
      .jukebox-table td {
        padding: 12px;
        border-bottom: ${Jukebox.Config.table.rowBorder};
        font-size: 14px;
      }
      
      .jukebox-table tbody tr:hover {
        background: ${Jukebox.Config.table.rowHoverBg};
      }
      
      .jukebox-table tbody tr:last-child td {
        border-bottom: none;
      }
      
      .loading {
        text-align: center;
        padding: 20px;
        color: ${Jukebox.Config.table.loadingColor};
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
      
      .jukebox-controls-row {
        margin-top: 15px;
        padding: 10px;
        background: ${Jukebox.Config.progress.containerBg};
        border-radius: 6px;
        display: flex;
        align-items: center;
        gap: 15px;
      }

      .jukebox-progress {
        display: flex;
        align-items: center;
        gap: 8px;
        flex: 3;
        min-width: 0;
        font-size: 13px;
        color: ${Jukebox.Config.progress.textColor};
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
        background: none;
        border: none;
        color: ${Jukebox.Config.volume.iconColor};
        cursor: pointer;
        padding: 5px;
        border-radius: 4px;
        transition: background 0.3s, color 0.3s;
        display: flex;
        align-items: center;
        justify-content: center;
      }

      .jukebox-speaker-btn:hover {
        background: ${Jukebox.Config.volume.iconHoverBg};
        color: ${Jukebox.Config.volume.iconHoverColor};
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
        background: rgba(0, 0, 0, 0.85);
        color: white;
        padding: 6px 10px;
        border-radius: 4px;
        font-size: 12px;
        pointer-events: none;
        z-index: 100030;
        white-space: nowrap;
        opacity: 0;
        transition: opacity 0.15s ease;
      }

      .jukebox-tooltip.visible {
        opacity: 1;
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
  
  loadSongs: async function() {
    try {
      // 从后端API加载配置
      const response = await fetch('/api/jukebox/config');
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      
      const data = await response.json();
      
      // 保存完整的配置数据
      Jukebox.State.config = data;
      
      // 将后端的歌曲对象转换为数组格式
      const songs = data.songs || {};
      const actions = data.actions || {};
      const bindings = data.bindings || {};
      
      Jukebox.State.songs = Object.entries(songs).map(([id, song]) => {
        // 获取该歌曲绑定的动画
        const songBindings = bindings[id] || {};
        const boundActions = Object.keys(songBindings).map(actionId => ({
          id: actionId,
          ...actions[actionId]
        })).filter(a => a.id); // 过滤掉不存在的动画
        
        // 处理音频路径：自带资源使用 /static/jukebox/ 前缀
        let audioPath = song.audio || '';
        if (song.isBuiltin && audioPath && !audioPath.startsWith('/static/')) {
          // 将 songs/xxx.mp3 转换为 /static/jukebox/xxx.mp3
          audioPath = '/static/jukebox/' + audioPath.replace(/^songs\//, '');
        }
        
        return {
          id: id,
          name: song.name || '未知',
          artist: song.artist || '未知',
          audio: audioPath,
          vmd: song.vmd || '',
          duration: song.duration || 0,
          visible: song.visible !== false, // 默认可见
          defaultAction: song.defaultAction || '',
          isBuiltin: song.isBuiltin || false, // 传递自带资源标记
          boundActions: boundActions // 绑定的动画列表
        };
      }).filter(song => song.visible); // 只显示可见的歌曲
      
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

    // 创建或更新歌曲行
    Jukebox.State.songs.forEach((song, index) => {
      const existingRow = Jukebox.State.songElements[song.id];

      if (existingRow) {
        // 更新现有行（只更新非播放状态的内容）
        Jukebox.updateSongRow(existingRow, song, index);
      } else {
        // 创建新行
        const newRow = Jukebox.createSongRow(song, index);
        tbody.appendChild(newRow);
        Jukebox.State.songElements[song.id] = newRow;
      }
    });

    console.log('[Jukebox]', window.t('Jukebox.songsRendered', '歌曲列表已渲染'));
  },

  // 创建歌曲行
  createSongRow: function(song, index) {
    const tr = document.createElement('tr');
    tr.dataset.songId = song.id;
    tr.innerHTML = `
      <td class="song-index">${index + 1}</td>
      <td class="song-name">${Jukebox.escapeHtml(song.name)}</td>
      <td class="song-artist">${Jukebox.escapeHtml(song.artist)}</td>
      <td class="song-action">
        <button class="play-btn" data-song-id="${Jukebox.escapeHtml(song.id)}" data-tooltip="${window.t('Jukebox.play', '播放')}">
          <svg viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M8 5v14l11-7z"/></svg>
        </button>
      </td>
    `;

    const btn = tr.querySelector('.play-btn');
    Jukebox.setupTooltip(btn, btn.dataset.tooltip);
    btn.addEventListener('click', () => {
      Jukebox_playSong(song.id);
    });

    return tr;
  },

  // 更新歌曲行（只更新基本信息，不触碰播放按钮）
  updateSongRow: function(row, song, index) {
    // 更新序号
    const indexCell = row.querySelector('.song-index');
    if (indexCell) indexCell.textContent = index + 1;

    // 更新歌名
    const nameCell = row.querySelector('.song-name');
    if (nameCell) nameCell.textContent = Jukebox.escapeHtml(song.name);

    // 更新歌手
    const artistCell = row.querySelector('.song-artist');
    if (artistCell) artistCell.textContent = Jukebox.escapeHtml(song.artist);

    // 注意：不更新播放按钮，以保持播放状态
  },
  
  playSong: async function(songId) {
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
        console.log('[Jukebox] 停止当前播放的歌曲:', song.name);
        Jukebox.stopPlayback();
        return;
      }
    }
    
    console.log('[Jukebox] 播放歌曲:', song.name);
    
    Jukebox.stopPlayback();
    
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
        // 处理动画路径：自带资源使用 /static/jukebox/ 前缀
        let actionFilePath = action.file || '';
        if (action.isBuiltin && actionFilePath && !actionFilePath.startsWith('/static/')) {
          // 将 actions/xxx.vmd 转换为 /static/jukebox/xxx.vmd
          actionFilePath = '/static/jukebox/' + actionFilePath.replace(/^actions\//, '');
        }
        const actionUrl = `/api/jukebox/file/${actionFilePath}`;
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
    
    if (!song.audio.endsWith('.mp3')) {
      console.error('[Jukebox]', window.t('Jukebox.nonMp3Error', '试图播放非mp3格式文件'));
      console.error('[Jukebox]', window.t('Jukebox.nonMp3Info', '非mp3音频信息'), JSON.stringify(song, null, 2));
      throw new Error(window.t('Jukebox.nonMp3Error', '试图播放非mp3格式文件'));
    }
    
    console.log('[Jukebox]', window.t('Jukebox.useAPlayer', '使用APlayer播放mp3文件'));
    
    // 将相对路径转换为API路径
    const audioUrl = `/api/jukebox/file/${song.audio}`;
    
    player.list.add([{
      name: song.name,
      artist: song.artist,
      url: audioUrl,
      cover: ''
    }]);
    
    player.options.loop = 'none';
    
    if (Jukebox.State.boundPlayer !== player) {
      player.on('ended', () => {
        console.log('[Jukebox]', window.t('Jukebox.mp3Ended', 'mp3播放结束'), {
          isPlaying: Jukebox.State.isPlaying,
          currentSong: Jukebox.State.currentSong,
          playerLoop: player.options.loop
        });
        Jukebox.stopVMD();
        Jukebox.State.isPlaying = false;
        Jukebox.State.isPaused = false;
        Jukebox.State.currentSong = null;
        Jukebox.updateStoppedStatus();
      });
      Jukebox.State.boundPlayer = player;
    }
    
    player.play();
    
    console.log('[Jukebox]', window.t('Jukebox.startPlay', '开始播放mp3音频'), song.audio);
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
      console.log('[Jukebox] VRMA 动画已发送 (IPC):', vrmaPath);
      return;
    }
    if (!window.vrmManager) {
      console.warn('[Jukebox] VRM Manager 未初始化，跳过动画');
      return;
    }

    try {
      console.log('[Jukebox] 播放 VRMA 动画:', vrmaPath);

      // 使用 VRMManager 的动画模块播放 VRMA
      if (window.vrmManager.animationModule) {
        await window.vrmManager.animationModule.playVRMAAnimation(vrmaPath, {
          loop: false,
          fadeInDuration: 0.5,
          fadeOutDuration: 0.5
        });
        console.log('[Jukebox] VRMA 动画已播放:', vrmaPath);
      } else {
        console.warn('[Jukebox] VRM AnimationModule 未初始化');
      }
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
  
  stopPlayback: function() {
    Jukebox.stopAudio();
    Jukebox.stopVMD();

    Jukebox.State.currentSong = null;
    Jukebox.State.isPlaying = false;
    Jukebox.State.isPaused = false;
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

    if (!window.mmdManager?.animationModule) return;

    // 没有在播放舞蹈 VMD 时，不要停止当前动画（可能是 idle 待机）
    if (!Jukebox.State.isVMDPlaying) return;

    // 直接停止动画模块，不通过 stopAnimation()
    // 避免在 idle 加载完成前改变 cursor follow 状态
    window.mmdManager.animationModule.stop();
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
    // 独立窗口模式：Pet 侧在 stopVMD 时自动恢复，此处无需操作
    if (window.__NEKO_JUKEBOX_STANDALONE__) return;
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
          const charRes = await fetch('/api/characters/');
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
    if (!Jukebox.State.currentSong) return;

    const player = Jukebox.getPlayer();
    var isStandalone = window.__NEKO_JUKEBOX_STANDALONE__ && window.nekoJukeboxBridge;

    if (Jukebox.State.isPaused) {
      // 恢复播放
      if (player) player.play();
      if (isStandalone) {
        window.nekoJukeboxBridge.resumeVMD();
      } else if (window.mmdManager?.animationModule) {
        // 直接恢复动画模块（不通过 playAnimation 避免重置动画进度）
        window.mmdManager.animationModule.play();
        if (window.mmdManager.cursorFollow) {
          window.mmdManager.cursorFollow.setAnimationMode('dance');
        }
      }
      Jukebox.State.isPaused = false;
      Jukebox.State.isPlaying = true;
      Jukebox.updatePlayingStatus(Jukebox.State.currentSong);
      console.log('[Jukebox]', window.t('Jukebox.resumed', '已恢复播放'));
    } else if (Jukebox.State.isPlaying) {
      // 暂停
      if (player) player.pause();
      if (isStandalone) {
        window.nekoJukeboxBridge.pauseVMD();
      } else if (window.mmdManager?.animationModule) {
        window.mmdManager.animationModule.pause();
        // 暂停时提升跟踪权重，让视线追踪更明显
        if (window.mmdManager.cursorFollow) {
          window.mmdManager.cursorFollow.setAnimationMode('idle');
        }
      }
      Jukebox.State.isPaused = true;
      Jukebox.State.isPlaying = false;
      Jukebox.updatePausedStatus(Jukebox.State.currentSong);
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

    // 同步 VMD 动画（考虑 offset）—— 独立窗口无法直接操作动画模块
    if (!window.__NEKO_JUKEBOX_STANDALONE__) {
      const song = Jukebox.State.currentSong;
      const action = song ? Jukebox.getActionForModel(song) : null;
      const fps = Jukebox.getAnimationFps(action);
      const offset = Jukebox.getCurrentOffset();
      const animFrame = seekTime * fps + offset;
      const animTime = Math.max(0, animFrame / fps);

      const anim = window.mmdManager?.animationModule;
      if (anim && anim.mixer && anim.currentClip) {
        anim.mixer.setTime(animTime);
        // 手动执行一帧更新让姿态同步
        anim._restoreBones(window.mmdManager.currentModel?.mesh);
        anim.mixer.update(0);
        anim._saveBones(window.mmdManager.currentModel?.mesh);
        const mesh = window.mmdManager.currentModel?.mesh;
        if (mesh) mesh.updateMatrixWorld(true);
        if (anim.ikSolver) anim.ikSolver.update();
        if (anim.grantSolver) anim.grantSolver.update();
      }
    }

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
      theme: Jukebox.Config.container.background,
      preload: 'auto',
      listFolded: true,
      volume: 1,
      audio: []
    });
    
    console.log('[Jukebox] APlayer已创建，音量:', Jukebox.State.player.audio.volume);
  },
  
  // 获取当前模型类型
  getModelType: function() {
    return window.lanlan_config?.model_type || 'live2d';
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

    // 从绑定关系中获取offset (从 SongActionManager.data 中获取)
    const binding = Jukebox.SongActionManager.data.bindings?.[song.id]?.[action.id];
    return binding?.offset || 0;
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

    const song = Jukebox.State.currentSong;
    const action = Jukebox.getActionForModel(song);
    const fps = Jukebox.getAnimationFps(action);

    const player = Jukebox.getPlayer();
    if (!player || !player.audio) return;

    const musicTime = player.audio.currentTime;
    const animFrame = musicTime * fps + offset;
    const animTime = Math.max(0, animFrame / fps);

    // 根据模型类型同步动画
    const modelType = Jukebox.getModelType();
    if (modelType === 'mmd' || modelType === 'live3d') {
      const anim = window.mmdManager?.animationModule;
      if (anim && anim.mixer) {
        anim.mixer.setTime(animTime);
        // 手动更新一帧确保同步
        if (anim.mixer.update) {
          anim.mixer.update(0);
        }
      }
    } else if (modelType === 'vrm') {
      // VRM动画同步（如果有相关API）
      console.log('[Jukebox] VRM动画同步:', animTime, 'FPS:', fps);
    } else if (modelType === 'fbx') {
      // FBX动画同步
      console.log('[Jukebox] FBX动画同步:', animTime, 'FPS:', fps);
    }
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
    const formatActions = boundActions.filter(a =>
      (a.format || 'vmd').toLowerCase() === targetFormat
    );

    if (formatActions.length === 0) {
      console.log('[Jukebox] 歌曲没有绑定', targetFormat.toUpperCase(), '格式的动画');
      return null;
    }

    // 如果用户设置了默认动画，优先使用它
    // 但如果默认动画已被删除（不在boundActions中），则不播放动画
    if (song.defaultAction) {
      const defaultAction = formatActions.find(a => a.id === song.defaultAction);
      if (defaultAction) {
        return defaultAction;
      }
      // 默认动画已删除，不播放动画
      console.log('[Jukebox] 默认动画已被删除，不播放动画');
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
    if (settingsBtn) settingsBtn.title = window.t('Jukebox.manager', '管理器');
    var minBtn = c.querySelector('.jukebox-minimize');
    if (minBtn) minBtn.title = window.t('Jukebox.minimize', '最小化');
    var closeBtn = c.querySelector('.jukebox-close');
    if (closeBtn) closeBtn.title = window.t('Jukebox.close', '关闭');

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
      ths[0].textContent = window.t('Jukebox.sequence', '序号');
      ths[1].textContent = window.t('Jukebox.song', '歌曲');
      ths[2].textContent = window.t('Jukebox.artist', '艺术家');
      ths[3].textContent = window.t('Jukebox.action', '操作');
    }

    // --- Mute button ---
    var speakerBtn = c.querySelector('#jukebox-speaker-btn');
    if (speakerBtn) speakerBtn.title = window.t('Jukebox.mute', '静音');

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
        if (titleEl) titleEl.textContent = window.t('Jukebox.managerTitle', '管理器');
        var tabs = panel.querySelectorAll('.sam-tab');
        var tabKeys = ['Jukebox.songs', 'Jukebox.actions', 'Jukebox.bindings'];
        var tabDefaults = ['歌曲', '动作', '绑定'];
        tabs.forEach(function(tab, i) {
          if (tabKeys[i]) tab.textContent = window.t(tabKeys[i], tabDefaults[i]);
        });
        var samCloseBtn = panel.querySelector('.sam-close-btn');
        if (samCloseBtn) samCloseBtn.title = window.t('Jukebox.close', '关闭');
        // Re-render active tab content
        Jukebox.SongActionManager.render();
      }
    } catch (e) { console.warn('[Jukebox] refreshLocale SongActionManager error:', e); }

    console.log('[Jukebox] UI 文本已刷新');
  }
};

// ===== 跨窗口语言切换自动刷新 =====
// i18n-i18next.js 会通过 storage 事件检测其他窗口的语言变更并调用 changeLanguage，
// changeLanguage 触发 languageChanged → localechange 自定义事件。
// 此处监听 localechange，在 Jukebox 已打开时自动刷新 UI 文本。
window.addEventListener('localechange', function() {
  if (Jukebox.State.isOpen || Jukebox.State.isHidden) {
    Jukebox.refreshLocale();
  }
});
