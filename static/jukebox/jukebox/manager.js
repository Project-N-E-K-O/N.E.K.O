Object.assign(window.Jukebox, {
  SongActionManager: {
    element: null,

    // 管理面板颜色配置
    Config: {
      // 面板
      panel: {
        background: 'linear-gradient(160deg, rgba(255,255,255,.92), rgba(232,247,255,.86))',
        color: 'rgba(28, 48, 68, 0.94)',
        border: '1px solid rgba(120, 203, 232, 0.45)',
        shadow: '0 18px 48px rgba(78, 153, 190, 0.28), 0 4px 18px rgba(255, 159, 189, 0.16)'
      },
      // 标签页
      tabs: {
        borderBottom: 'rgba(116, 190, 224, 0.28)',
        tabColor: 'rgba(54, 92, 118, 0.76)',
        tabHoverBg: 'rgba(99, 199, 232, 0.14)',
        tabActiveBg: 'linear-gradient(135deg, rgba(99,199,232,.92), rgba(255,159,189,.82))',
        tabActiveShadow: '0 6px 16px rgba(99, 199, 232, 0.25)'
      },
      // 列表项
      item: {
        background: 'rgba(255,255,255,0.68)',
        hoverBg: 'rgba(255,255,255,0.9)',
        border: '1px solid rgba(120, 203, 232, 0.22)',
        hoverBorder: 'rgba(255,159,189,0.36)',
        shadow: '0 6px 18px rgba(78, 153, 190, 0.12)',
        draggingOpacity: '0.5'
      },
      // 格式颜色
      formatColors: {
        vmd: { primary: '#2196F3', bg: 'rgba(33,150,243,0.4)', bgHover: 'rgba(33,150,243,0.6)', bgDefault: 'rgba(33,150,243,0.85)', border: 'rgba(33,150,243,0.6)', borderDefault: 'rgba(100,200,255,0.9)', smallBg: 'rgba(33,150,243,0.3)', smallBgDefault: 'rgba(33,150,243,0.7)', smallBorder: 'rgba(33,150,243,0.5)' },
        bvh: { primary: '#FF9800', bg: 'rgba(255,152,0,0.4)', bgHover: 'rgba(255,152,0,0.6)', bgDefault: 'rgba(255,152,0,0.85)', border: 'rgba(255,152,0,0.6)', borderDefault: 'rgba(255,200,100,0.9)', smallBg: 'rgba(255,152,0,0.3)', smallBgDefault: 'rgba(255,152,0,0.7)', smallBorder: 'rgba(255,152,0,0.5)' },
        vrma: { primary: '#4CAF50', bg: 'rgba(76,175,80,0.4)', bgHover: 'rgba(76,175,80,0.6)', bgDefault: 'rgba(76,175,80,0.85)', border: 'rgba(76,175,80,0.6)', borderDefault: 'rgba(120,220,120,0.9)', smallBg: 'rgba(76,175,80,0.3)', smallBgDefault: 'rgba(76,175,80,0.7)', smallBorder: 'rgba(76,175,80,0.5)' },
        fbx: { primary: '#9C27B0', bg: 'rgba(156,39,176,0.4)', bgHover: 'rgba(156,39,176,0.6)', bgDefault: 'rgba(156,39,176,0.85)', border: 'rgba(156,39,176,0.6)', borderDefault: 'rgba(200,100,220,0.9)', smallBg: 'rgba(156,39,176,0.3)', smallBgDefault: 'rgba(156,39,176,0.7)', smallBorder: 'rgba(156,39,176,0.5)' },
        default: { primary: '#35a9c9', bg: 'rgba(99,199,232,0.24)', bgHover: 'rgba(99,199,232,0.34)', bgDefault: 'rgba(255,159,189,0.36)', border: 'rgba(99,199,232,0.42)', borderDefault: 'rgba(255,159,189,0.58)', smallBg: 'rgba(99,199,232,0.18)', smallBgDefault: 'rgba(255,159,189,0.26)', smallBorder: 'rgba(99,199,232,0.34)' }
      },
      // 功能色
      functional: {
        success: '#35a9c9',
        successSubtleBg: 'rgba(99,199,232,0.06)',
        successBg: 'rgba(99,199,232,0.14)',
        successEmphasisBg: 'rgba(99,199,232,0.22)',
        successHoverBg: 'rgba(99,199,232,0.28)',
        successStrongHoverBg: 'rgba(99,199,232,0.46)',
        danger: '#d94b61',
        dangerHover: '#ec6a7c',
        missing: '#d94b61',
        missingBg: 'rgba(217,75,97,0.12)',
        confirmBg: 'rgba(99,199,232,0.72)',
        confirmHoverBg: 'rgba(99,199,232,0.9)',
        cancelBg: 'rgba(217,75,97,0.18)',
        cancelHoverBg: 'rgba(217,75,97,0.28)',
        dropdownBg: 'rgba(248,252,255,0.98)',
        tagBg: 'rgba(255,159,189,0.22)',
        countBg: 'rgba(99,199,232,0.78)'
      },
      // 边框和分割线
      borders: {
        dashed: 'rgba(99,199,232,0.32)',
        solid: 'rgba(99,199,232,0.38)',
        divider: 'rgba(116,190,224,0.18)',
        itemFormatBg: 'rgba(99,199,232,0.12)'
      },
      // 文字颜色
      text: {
        primary: 'rgba(28,48,68,0.94)',
        secondary: 'rgba(38,118,148,0.86)',
        muted: 'rgba(54,112,140,0.78)',
        placeholder: 'rgba(54,112,140,0.72)',
        empty: 'rgba(38,118,148,0.78)'
      },
      // 输入框
      input: {
        hoverBg: 'rgba(99,199,232,0.12)',
        focusBg: 'rgba(255,255,255,0.82)'
      },
      // 按钮
      buttons: {
        visibility: {
          color: 'rgba(38,118,148,0.9)',
          hoverBg: 'rgba(99,199,232,0.2)',
          hoverColor: 'rgba(28,48,68,0.92)',
          hiddenColor: 'rgba(255,159,189,0.82)'
        },
        delete: {
          color: '#d94b61',
          hoverBg: 'rgba(217,75,97,0.12)'
        },
        primary: {
          bg: '#35a9c9',
          hoverBg: '#63c7e8',
          softBg: 'rgba(53,169,201,0.2)'
        }
      },
      // 选中状态
      selected: {
        bg: 'rgba(99,199,232,0.2)',
        border: '3px solid #63c7e8'
      },
      // 允许的文件扩展名（与后端 ALLOWED_AUDIO/ACTION_EXTENSIONS 保持一致）
      allowedAudioExts: ['mp3', 'wav', 'ogg', 'flac'],
      allowedActionExts: ['vmd', 'bvh', 'fbx', 'vrma'],
      // 拖放区域
      dropzone: {
        overBg: 'rgba(99,199,232,0.2)',
        overBorder: '2px dashed rgba(99,199,232,0.5)'
      },
      // 底部区域
      footer: {
        bg: 'rgba(255,255,255,0.58)',
        borderTop: '1px solid rgba(116,190,224,0.2)',
        importBg: 'rgba(255,255,255,0.52)',
        buttonBg: 'rgba(255,255,255,0.78)',
        buttonHoverBg: 'rgba(99,199,232,0.2)',
        hintColor: 'rgba(38,118,148,0.82)',
        shortcutColor: 'rgba(38,118,148,0.76)'
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

      async batchDeleteSongs(songIds) {
        const response = await fetch(`${this.baseUrl}/songs/batch-delete`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json'
          },
          body: JSON.stringify({ songIds })
        });
        if (!response.ok) {
          const errorData = await response.json().catch(() => ({}));
          throw new Error(errorData.detail?.message || errorData.detail || errorData.error || `HTTP ${response.status}`);
        }
        return response.json();
      },

      async batchDeleteActions(actionIds) {
        const response = await fetch(`${this.baseUrl}/actions/batch-delete`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json'
          },
          body: JSON.stringify({ actionIds })
        });
        if (!response.ok) {
          const errorData = await response.json().catch(() => ({}));
          throw new Error(errorData.detail?.message || errorData.detail || errorData.error || `HTTP ${response.status}`);
        }
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

      async updateActionVisibility(actionId, visible) {
        const formData = new FormData();
        formData.append('visible', visible);
        const response = await fetch(`${this.baseUrl}/actions/${actionId}/visibility`, {
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
            'width=480,height=600,resizable=yes'
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
        ${window.__NEKO_JUKEBOX_MANAGER_STANDALONE__ ? '' : `
          <div class="sam-resize-handle" data-dir="n"></div>
          <div class="sam-resize-handle" data-dir="s"></div>
          <div class="sam-resize-handle" data-dir="w"></div>
          <div class="sam-resize-handle" data-dir="e"></div>
          <div class="sam-resize-handle" data-dir="nw"></div>
          <div class="sam-resize-handle" data-dir="ne"></div>
          <div class="sam-resize-handle" data-dir="sw"></div>
          <div class="sam-resize-handle" data-dir="se"></div>
        `}
        <div class="sam-header">
          <span class="sam-title">${window.t('Jukebox.managerTitle', '点歌台管理')}</span>
          <span class="sam-drag-fill sam-drag-fill-left" aria-hidden="true"></span>
          <div class="sam-tabs">
            <button class="sam-tab active" data-tab="songs">${window.t('Jukebox.songs', '歌曲库')}</button>
            <button class="sam-tab" data-tab="actions">${window.t('Jukebox.actions', '舞蹈动作')}</button>
            <button class="sam-tab" data-tab="bindings">${window.t('Jukebox.bindings', '歌曲绑定')}</button>
          </div>
          <span class="sam-drag-fill sam-drag-fill-right" aria-hidden="true"></span>
          <div class="sam-window-controls">
            <button class="sam-pin-btn" type="button"
                    data-neko-window-control="pin"
                    data-i18n-title="common.pinWindow"
                    data-i18n-aria="common.pinWindow"
                    data-tooltip="${Jukebox.escapeAttr(window.t('common.pinWindow', '置顶窗口'))}"
                    title="${Jukebox.escapeAttr(window.t('common.pinWindow', '置顶窗口'))}"
                    aria-label="${Jukebox.escapeAttr(window.t('common.pinWindow', '置顶窗口'))}"
                    aria-pressed="false" hidden><span class="neko-window-pin-icon" aria-hidden="true"></span></button>
            <button class="sam-close-btn"
                    onclick="Jukebox.SongActionManager.hide()"
                    data-tooltip="${Jukebox.escapeAttr(window.t('Jukebox.close', '关闭'))}"
                    aria-label="${Jukebox.escapeAttr(window.t('Jukebox.close', '关闭'))}">×</button>
          </div>
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
            <span class="sam-danger-action-wrap" style="display:none">
              <button class="sam-btn sam-btn-danger sam-btn-song-danger"
                      onclick="Jukebox.SongActionManager.confirmManagerBatchDelete()"
                      onmouseenter="Jukebox.SongActionManager.showManagerDeleteTooltip(this)"
                      onmouseleave="Jukebox.SongActionManager.hideManagerDeleteTooltip()"></button>
              <span class="sam-danger-tooltip" role="tooltip"></span>
            </span>
          </div>
          <span class="sam-selection-info" id="sam-selection-info"></span>
          <div class="sam-unified-hint" id="sam-unified-hint">
            <span class="sam-hint-normal">${window.t('Jukebox.unifiedDropHint', '拖入歌曲 / 舞蹈动作 / 导入包，或点击添加')} · <span class="sam-click-add" onclick="Jukebox.SongActionManager.showUnifiedFilePicker()">+ ${window.t('Jukebox.clickToAdd', '点击添加')}</span></span>
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
      this.bindButtonTooltips(panel);
    },

    bindButtonTooltips(panel) {
      if (!panel) return;
      panel.querySelectorAll('.sam-close-btn').forEach((button) => {
        Jukebox.setupTooltipOnce(button, () => button.dataset.tooltip || window.t('Jukebox.close', '关闭'));
      });
      panel.querySelectorAll('.sam-pin-btn').forEach((button) => {
        Jukebox.setupTooltipOnce(button, () => button.dataset.tooltip || window.t('common.pinWindow', '置顶窗口'));
      });
      panel.querySelectorAll('.sam-visibility-btn').forEach((button) => {
        Jukebox.setupTooltipOnce(button, () => button.dataset.tooltip || button.getAttribute('aria-label') || '');
      });
      panel.querySelectorAll('.sam-delete-btn').forEach((button) => {
        Jukebox.setupTooltipOnce(button, () => button.dataset.tooltip || window.t('Jukebox.delete', '删除'));
      });
      panel.querySelectorAll('.sam-unbind-btn').forEach((button) => {
        Jukebox.setupTooltipOnce(button, () => button.dataset.tooltip || window.t('Jukebox.unbind', '解除绑定'));
      });
      panel.querySelectorAll('.sam-add-binding-btn').forEach((button) => {
        Jukebox.setupTooltipOnce(button, () => button.dataset.tooltip || '');
      });
      panel.querySelectorAll('.sam-add-binding-confirm').forEach((button) => {
        Jukebox.setupTooltipOnce(button, () => button.dataset.tooltip || window.t('Jukebox.confirm', '确认'));
      });
      panel.querySelectorAll('.sam-add-binding-cancel').forEach((button) => {
        Jukebox.setupTooltipOnce(button, () => button.dataset.tooltip || window.t('Jukebox.cancel', '取消'));
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
          this.rerenderPanel(panel, () => this.renderSongs(panel));
          break;
        case 'actions':
          this.rerenderPanel(panel, () => this.renderActions(panel));
          break;
        case 'bindings':
          this.rerenderPanel(panel, () => this.renderBindings(panel));
          break;
      }
      this.updateSelectionInfo();
    },

    renderSongs(panel) {
      const showHidden = this.showHiddenSongs !== false;
      const songs = this.getVisibleSongEntries();
      const allSongsSelected = this.areAllSongsSelected();
      const hasAnySongsSelected = this.hasAnySongsSelected();

      panel.innerHTML = `
        <div class="sam-list-header">
          <label class="sam-checkbox">
            <input type="checkbox" id="select-all-songs" ${allSongsSelected ? 'checked' : ''} onchange="Jukebox.SongActionManager.toggleSelectAllSongs(this.checked)">
            <span>${window.t('Jukebox.selectAll', '全选')}</span>
          </label>
          <label class="sam-checkbox sam-checkbox-right">
            <input type="checkbox" ${showHidden ? 'checked' : ''} onchange="Jukebox.SongActionManager.toggleShowHidden(this.checked)">
            <span>${window.t('Jukebox.showHiddenSongs', '显示隐藏的歌曲')}</span>
          </label>
        </div>
        <div class="sam-list">
            ${songs.length === 0 ? `<div class="sam-empty">${window.t('Jukebox.noSongs', '暂无歌曲')}</div>` :
              songs.map(([id, song]) => {
                const idAttr = Jukebox.escapeAttr(id);
                const idJs = Jukebox.escapeJsAttr(id);
                return `
                <div class="sam-item ${song.visible === false ? 'sam-item-hidden' : ''} ${this.selectedSongs?.has(id) ? 'sam-item-selected' : ''}" data-id="${idAttr}" draggable="true">
                  <div class="sam-item-header">
                    <label class="sam-checkbox sam-item-checkbox">
                      <input type="checkbox" class="sam-song-select" data-id="${idAttr}" ${this.selectedSongs?.has(id) ? 'checked' : ''} onchange="Jukebox.SongActionManager.toggleSongSelect('${idJs}', this.checked)">
                    </label>
                    <span class="sam-item-name" contenteditable="true" data-neko-marquee data-tooltip="${Jukebox.escapeAttr(song.name)}"
                          onblur="Jukebox.SongActionManager.updateSongName('${idJs}', this.innerText)"
                          onkeydown="if(event.key==='Enter'){this.blur();event.preventDefault();}">${Jukebox.escapeHtml(song.name)}</span>
                    <div class="sam-item-actions">
                      <button class="sam-visibility-btn ${song.visible === false ? 'hidden' : ''}"
                              onclick="Jukebox.SongActionManager.toggleSongVisibility('${idJs}')"
                              data-tooltip="${Jukebox.escapeAttr(song.visible === false ? window.t('Jukebox.show', '显示') : window.t('Jukebox.hide', '隐藏'))}"
                              aria-label="${Jukebox.escapeAttr(song.visible === false ? window.t('Jukebox.show', '显示') : window.t('Jukebox.hide', '隐藏'))}">
                        ${song.visible === false
                          ? '<svg viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M12 4.5C7 4.5 2.73 7.61 1 12c1.73 4.39 6 7.5 11 7.5s9.27-3.11 11-7.5c-1.73-4.39-6-7.5-11-7.5zM12 17c-2.76 0-5-2.24-5-5s2.24-5 5-5 5 2.24 5 5-2.24 5-5 5zm0-8c-1.66 0-3 1.34-3 3s1.34 3 3 3 3-1.34 3-3-1.34-3-3-3z"/><line x1="3" y1="3" x2="21" y2="21" stroke="currentColor" stroke-width="2"/></svg>'
                          : '<svg viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M12 4.5C7 4.5 2.73 7.61 1 12c1.73 4.39 6 7.5 11 7.5s9.27-3.11 11-7.5c-1.73-4.39-6-7.5-11-7.5zM12 17c-2.76 0-5-2.24-5-5s2.24-5 5-5 5 2.24 5 5-2.24 5-5 5zm0-8c-1.66 0-3 1.34-3 3s1.34 3 3 3 3-1.34 3-3-1.34-3-3-3z"/></svg>'}
                      </button>
                      <button class="sam-delete-btn" onclick="Jukebox.SongActionManager.confirmDeleteSong('${idJs}')" data-tooltip="${Jukebox.escapeAttr(window.t('Jukebox.delete', '删除'))}" aria-label="${Jukebox.escapeAttr(window.t('Jukebox.delete', '删除'))}">🗑</button>
                    </div>
                  </div>
                  <div class="sam-item-artist" contenteditable="true" data-neko-marquee data-tooltip="${Jukebox.escapeAttr(song.artist || window.t('Jukebox.unknown', '未知'))}"
                       onblur="Jukebox.SongActionManager.updateSongArtist('${idJs}', this.innerText)"
                       onkeydown="if(event.key==='Enter'){this.blur();event.preventDefault();}">${Jukebox.escapeHtml(song.artist || window.t('Jukebox.unknown', '未知'))}
                  </div>
                  <div class="sam-item-bindings">
                    ${this.getSongBindings(id).filter(actionId => this.shouldShowAction(this.data.actions[actionId])).map(actionId => {
                      const action = this.data.actions[actionId];
                      if (!action) return '';
                      const isDefault = song.defaultAction === actionId;
                      const format = action.format || 'vmd';
                      const titleText = isDefault
                        ? `${window.t('Jukebox.defaultAction', '默认动画')} - ${window.t('Jukebox.clickSetDefault', '点击设为默认')}\n${window.t('Jukebox.format', '格式')}: ${format.toUpperCase()}`
                        : `${window.t('Jukebox.clickSetDefault', '点击设为默认')}\n${window.t('Jukebox.format', '格式')}: ${format.toUpperCase()}`;
                      const actionIdJs = Jukebox.escapeJsAttr(actionId);
                      return `<span class="sam-binding-tag sam-action-tag sam-action-tag-${format.toLowerCase()} ${isDefault ? 'sam-action-tag-default' : ''}"
                                   data-neko-marquee
                                   data-tooltip="${Jukebox.escapeAttr(titleText)}"
                                   onclick="Jukebox.SongActionManager.setDefaultAction('${idJs}', '${actionIdJs}')"
                                   >
                        ${isDefault ? '★ ' : ''}${Jukebox.escapeHtml(action.name)}
                      </span>`;
                    }).join('')}
                  </div>
                </div>
              `}).join('')}
          </div>
      `;
      this.syncCheckboxState(panel.querySelector('#select-all-songs'), allSongsSelected, hasAnySongsSelected && !allSongsSelected);
      this.bindDragEvents(panel);
      this.bindFileDropEvents(panel, 'audio');
      this.bindButtonTooltips(panel);
      Jukebox.bindTextTooltips(panel);
      Jukebox.scheduleMarqueeTextUpdate(panel);
    },

    renderActions(panel) {
      const showHidden = this.showHiddenActions !== false;
      const actions = this.getVisibleActionEntries();
      const allActionsSelected = this.areAllActionsSelected();
      const hasAnyActionsSelected = this.hasAnyActionsSelected();
      panel.innerHTML = `
        <div class="sam-list-header">
          <label class="sam-checkbox">
            <input type="checkbox" id="select-all-actions" ${allActionsSelected ? 'checked' : ''} onchange="Jukebox.SongActionManager.toggleSelectAllActions(this.checked)">
            <span>${window.t('Jukebox.selectAll', '全选')}</span>
          </label>
          <label class="sam-checkbox sam-checkbox-right">
            <input type="checkbox" ${showHidden ? 'checked' : ''} onchange="Jukebox.SongActionManager.toggleShowHiddenActions(this.checked)">
            <span>${window.t('Jukebox.showHiddenActions', '显示隐藏的动画')}</span>
          </label>
        </div>
        <div class="sam-list">
            ${actions.length === 0 ? `<div class="sam-empty">${window.t('Jukebox.noActions', '暂无动画')}</div>` :
              actions.map(([id, action]) => {
                const idAttr = Jukebox.escapeAttr(id);
                const idJs = Jukebox.escapeJsAttr(id);
                const format = action.format || 'vmd';
                const formatColor = this.getFormatColor(format);
                return `
                <div class="sam-item ${action.visible === false ? 'sam-item-hidden' : ''} ${this.selectedActions?.has(id) ? 'sam-item-selected' : ''}" data-id="${idAttr}" draggable="true">
                  <div class="sam-item-header">
                    <label class="sam-checkbox sam-item-checkbox">
                      <input type="checkbox" class="sam-action-select" data-id="${idAttr}" ${this.selectedActions?.has(id) ? 'checked' : ''} onchange="Jukebox.SongActionManager.toggleActionSelect('${idJs}', this.checked)">
                    </label>
                    <span class="sam-format-dot" style="background-color: ${formatColor};"></span>
                    <span class="sam-item-name" contenteditable="true" data-neko-marquee data-tooltip="${Jukebox.escapeAttr(action.name)}"
                          onblur="Jukebox.SongActionManager.updateActionName('${idJs}', this.innerText)"
                          onkeydown="if(event.key==='Enter'){this.blur();event.preventDefault();}">${Jukebox.escapeHtml(action.name)}</span>
                    <div class="sam-item-actions">
                      ${action.missing ? `<span class="sam-missing-badge">${window.t('Jukebox.missing', '缺失')}</span>` : ''}
                      <button class="sam-visibility-btn ${action.visible === false ? 'hidden' : ''}"
                              onclick="Jukebox.SongActionManager.toggleActionVisibility('${idJs}')"
                              data-tooltip="${Jukebox.escapeAttr(action.visible === false ? window.t('Jukebox.show', '显示') : window.t('Jukebox.hide', '隐藏'))}"
                              aria-label="${Jukebox.escapeAttr(action.visible === false ? window.t('Jukebox.show', '显示') : window.t('Jukebox.hide', '隐藏'))}">
                        ${action.visible === false
                          ? '<svg viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M12 4.5C7 4.5 2.73 7.61 1 12c1.73 4.39 6 7.5 11 7.5s9.27-3.11 11-7.5c-1.73-4.39-6-7.5-11-7.5zM12 17c-2.76 0-5-2.24-5-5s2.24-5 5-5 5 2.24 5 5-2.24 5-5 5zm0-8c-1.66 0-3 1.34-3 3s1.34 3 3 3 3-1.34 3-3-1.34-3-3-3z"/><line x1="3" y1="3" x2="21" y2="21" stroke="currentColor" stroke-width="2"/></svg>'
                          : '<svg viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M12 4.5C7 4.5 2.73 7.61 1 12c1.73 4.39 6 7.5 11 7.5s9.27-3.11 11-7.5c-1.73-4.39-6-7.5-11-7.5zM12 17c-2.76 0-5-2.24-5-5s2.24-5 5-5 5 2.24 5 5-2.24 5-5 5zm0-8c-1.66 0-3 1.34-3 3s1.34 3 3 3 3-1.34 3-3-1.34-3-3-3z"/></svg>'}
                      </button>
                      <button class="sam-delete-btn" onclick="Jukebox.SongActionManager.confirmDeleteAction('${idJs}')" data-tooltip="${Jukebox.escapeAttr(window.t('Jukebox.delete', '删除'))}" aria-label="${Jukebox.escapeAttr(window.t('Jukebox.delete', '删除'))}">🗑</button>
                    </div>
                  </div>
                  <div class="sam-item-bindings">
                    ${this.getActionBindings(id).filter(songId => this.shouldShowSong(this.data.songs[songId])).map(songId => {
                      const song = this.data.songs[songId];
                      return song ? `<span class="sam-binding-tag" data-neko-marquee data-tooltip="${Jukebox.escapeAttr(song.name)}">${Jukebox.escapeHtml(song.name)}</span>` : '';
                    }).join('')}
                  </div>
                </div>
              `}).join('')}
          </div>
      `;
      this.syncCheckboxState(panel.querySelector('#select-all-actions'), allActionsSelected, hasAnyActionsSelected && !allActionsSelected);
      this.bindDragEvents(panel);
      this.bindFileDropEvents(panel, 'action');
      this.bindButtonTooltips(panel);
      Jukebox.bindTextTooltips(panel);
      Jukebox.scheduleMarqueeTextUpdate(panel);
    },

    renderBindings(panel) {
      this.initSelection();
      this.initBindingSelection();
      const allBindingSongsSelected = this.areAllBindingSongsSelected();
      const hasAnyBindingSongsSelected = this.hasAnyBindingSongsSelected();
      const allBindingActionsSelected = this.areAllBindingActionsSelected();
      const hasAnyBindingActionsSelected = this.hasAnyBindingActionsSelected();
      const visibleSongs = this.getVisibleSongEntries();
      const visibleActions = this.getVisibleActionEntries();

      panel.innerHTML = `
        <div class="sam-bindings-container">
          <div class="sam-bindings-section">
            <div class="sam-bindings-header">
              <h4>${window.t('Jukebox.songList', '歌曲列表 (拖拽到右侧)')}</h4>
              <label class="sam-checkbox">
                <input type="checkbox" id="select-all-binding-songs" ${allBindingSongsSelected ? 'checked' : ''} onchange="Jukebox.SongActionManager.toggleSelectAllBindingSongs(this.checked)">
                <span>${window.t('Jukebox.selectAll', '全选')}</span>
              </label>
            </div>
            <div class="sam-bindings-list songs-for-drop">
              ${visibleSongs.length === 0 ? `<div class="sam-empty">${window.t('Jukebox.noSongs', '暂无歌曲')}</div>` :
                visibleSongs.map(([id, song], index) => {
                  const boundActions = this.getSongBindings(id).filter(actionId => this.shouldShowAction(this.data.actions[actionId]));
                  const isSelected = this.bindingSelectedSongs.has(id);
                  const songIndex = index + 1;
                  const idAttr = Jukebox.escapeAttr(id);
                  const idJs = Jukebox.escapeJsAttr(id);
                  return `
                <div class="sam-binding-item ${isSelected ? 'sam-binding-item-selected' : ''}" data-song-id="${idAttr}" draggable="true" data-index="${songIndex}">
                  <div class="sam-binding-item-main">
                    <label class="sam-checkbox sam-item-checkbox">
                      <input type="checkbox" ${isSelected ? 'checked' : ''} onchange="Jukebox.SongActionManager.toggleBindingSongSelect('${idJs}', this.checked)">
                    </label>
                    <span class="sam-binding-item-index">${songIndex}</span>
                    <span class="sam-binding-item-name" data-neko-marquee data-tooltip="${Jukebox.escapeAttr(song.name)}">${Jukebox.escapeHtml(song.name)}</span>
                  </div>
                  <div class="sam-binding-item-tags">
                    ${boundActions.map(actionId => {
                      const action = this.data.actions[actionId];
                      const isActionSelected = this.bindingSelectedActions.has(actionId);
                      const isDefault = song.defaultAction === actionId;
                      const format = action?.format || 'vmd';
                      const formatColor = this.getFormatColor(format);
                      const offset = this.data.bindings[id]?.[actionId]?.offset || 0;
                      const titleText = isDefault
                        ? `${window.t('Jukebox.defaultAction', '默认动画')} - ${window.t('Jukebox.clickSetDefault', '点击设为默认')}\n${window.t('Jukebox.offset', '偏移')}: ${offset}${window.t('Jukebox.frame', '帧')}\n${window.t('Jukebox.format', '格式')}: ${format.toUpperCase()}`
                        : `${window.t('Jukebox.clickSetDefault', '点击设为默认')}\n${window.t('Jukebox.offset', '偏移')}: ${offset}${window.t('Jukebox.frame', '帧')}\n${window.t('Jukebox.format', '格式')}: ${format.toUpperCase()}`;
                      const actionIdJs = Jukebox.escapeJsAttr(actionId);
                      return action ? `
                        <span class="sam-binding-tag-small sam-action-tag-small sam-action-tag-small-${format.toLowerCase()} ${isActionSelected ? 'sam-tag-selected' : ''} ${isDefault ? 'sam-action-tag-small-default' : ''}"
                              onclick="Jukebox.SongActionManager.setDefaultAction('${idJs}', '${actionIdJs}')"
                              data-tooltip="${Jukebox.escapeAttr(titleText)}">
                          <span class="sam-format-dot" style="background-color: ${formatColor};"></span>
                          <span class="sam-binding-tag-label" data-neko-marquee>${isDefault ? '★ ' : ''}${Jukebox.escapeHtml(action.name)}</span>
                          <button class="sam-unbind-btn" onclick="event.stopPropagation(); Jukebox.SongActionManager.unbindSongFromAction('${idJs}', '${actionIdJs}');" data-tooltip="${Jukebox.escapeAttr(window.t('Jukebox.unbind', '解除绑定'))}" aria-label="${Jukebox.escapeAttr(window.t('Jukebox.unbind', '解除绑定'))}">×</button>
                        </span>` : '';
                    }).join('')}
                    <button class="sam-add-binding-btn" onclick="Jukebox.SongActionManager.showAddBindingInput(this, '${idJs}', 'song')" data-tooltip="${Jukebox.escapeAttr(window.t('Jukebox.addActionBinding', '手动添加动画绑定'))}" aria-label="${Jukebox.escapeAttr(window.t('Jukebox.addActionBinding', '手动添加动画绑定'))}">+</button>
                  </div>
                </div>
              `}).join('')}
            </div>
          </div>
          <div class="sam-bindings-section">
            <div class="sam-bindings-header">
              <h4>${window.t('Jukebox.actionList', '动画列表 (拖拽到左侧)')}</h4>
              <label class="sam-checkbox">
                <input type="checkbox" id="select-all-binding-actions" ${allBindingActionsSelected ? 'checked' : ''} onchange="Jukebox.SongActionManager.toggleSelectAllBindingActions(this.checked)">
                <span>${window.t('Jukebox.selectAll', '全选')}</span>
              </label>
            </div>
            <div class="sam-bindings-list actions-for-drop">
              ${visibleActions.length === 0 ? `<div class="sam-empty">${window.t('Jukebox.noActions', '暂无动画')}</div>` :
                visibleActions.map(([id, action], index) => {
                  const boundSongs = this.getActionBindings(id);
                  const isSelected = this.bindingSelectedActions.has(id);
                  const format = action.format || 'vmd';
                  const formatColor = this.getFormatColor(format);
                  const actionIndex = index + 1;
                  const idAttr = Jukebox.escapeAttr(id);
                  const idJs = Jukebox.escapeJsAttr(id);
                  return `
                <div class="sam-binding-item ${isSelected ? 'sam-binding-item-selected' : ''}" data-action-id="${idAttr}" draggable="true" data-index="${actionIndex}">
                  <div class="sam-binding-item-main">
                    <label class="sam-checkbox sam-item-checkbox">
                      <input type="checkbox" ${isSelected ? 'checked' : ''} onchange="Jukebox.SongActionManager.toggleBindingActionSelect('${idJs}', this.checked)">
                    </label>
                    <span class="sam-binding-item-index">${actionIndex}</span>
                    <span class="sam-format-dot" style="background-color: ${formatColor};"></span>
                    <span class="sam-binding-item-name" data-neko-marquee data-tooltip="${Jukebox.escapeAttr(action.name)}">${Jukebox.escapeHtml(action.name)}</span>
                  </div>
                  <div class="sam-binding-item-tags">
                    ${boundSongs.filter(songId => this.shouldShowSong(this.data.songs[songId])).map(songId => {
                      const song = this.data.songs[songId];
                      const isSongSelected = this.bindingSelectedSongs.has(songId);
                      const offset = this.data.bindings[songId]?.[id]?.offset || 0;
                      const titleText = `${window.t('Jukebox.offset', '偏移')}: ${offset}${window.t('Jukebox.frame', '帧')}`;
                      const songIdJs = Jukebox.escapeJsAttr(songId);
                      return song ? `
                        <span class="sam-binding-tag-small ${isSongSelected ? 'sam-tag-selected' : ''}" data-tooltip="${Jukebox.escapeAttr(titleText)}">
                          <span class="sam-binding-tag-label" data-neko-marquee>${Jukebox.escapeHtml(song.name)}</span>
                          <button class="sam-unbind-btn" onclick="Jukebox.SongActionManager.unbindSongFromAction('${songIdJs}', '${idJs}'); event.stopPropagation();" data-tooltip="${Jukebox.escapeAttr(window.t('Jukebox.unbind', '解除绑定'))}" aria-label="${Jukebox.escapeAttr(window.t('Jukebox.unbind', '解除绑定'))}">×</button>
                        </span>` : '';
                    }).join('')}
                    <button class="sam-add-binding-btn" onclick="Jukebox.SongActionManager.showAddBindingInput(this, '${idJs}', 'action')" data-tooltip="${Jukebox.escapeAttr(window.t('Jukebox.addSongBinding', '手动添加歌曲绑定'))}" aria-label="${Jukebox.escapeAttr(window.t('Jukebox.addSongBinding', '手动添加歌曲绑定'))}">+</button>
                  </div>
                </div>
              `}).join('')}
            </div>
          </div>
        </div>
      `;
      this.syncCheckboxState(panel.querySelector('#select-all-binding-songs'), allBindingSongsSelected, hasAnyBindingSongsSelected && !allBindingSongsSelected);
      this.syncCheckboxState(panel.querySelector('#select-all-binding-actions'), allBindingActionsSelected, hasAnyBindingActionsSelected && !allBindingActionsSelected);
      this.bindBindingDragEvents(panel);
      this.bindButtonTooltips(panel);
      Jukebox.bindTextTooltips(panel);
      Jukebox.scheduleMarqueeTextUpdate(panel);
    },

    toggleShowHidden(checked) {
      this.showHiddenSongs = checked;
      if (!checked) {
        this.pruneHiddenSongSelection();
      }
      const songsPanel = document.querySelector('.songs-panel');
      if (songsPanel) {
        this.rerenderPanel(songsPanel, () => this.renderSongs(songsPanel));
      }
      this.updateSelectionInfo();
    },

    toggleShowHiddenActions(checked) {
      this.showHiddenActions = checked;
      if (!checked) {
        this.pruneHiddenActionSelection();
      }
      const actionsPanel = document.querySelector('.actions-panel');
      if (actionsPanel) {
        this.rerenderPanel(actionsPanel, () => this.renderActions(actionsPanel));
      }
      const bindingsPanel = document.querySelector('.bindings-panel');
      if (bindingsPanel && bindingsPanel.innerHTML.trim()) {
        this.rerenderPanel(bindingsPanel, () => this.renderBindings(bindingsPanel));
      }
      this.updateSelectionInfo();
    },

    async toggleSongVisibility(songId) {
      const song = this.data.songs[songId];
      if (!song) return;

      const newVisible = song.visible === false ? true : false;
      try {
        await this.api.updateSongVisibility(songId, newVisible);
        song.visible = newVisible;
        if (!newVisible) {
          if (this.selectedSongs) this.selectedSongs.delete(songId);
          if (this.bindingSourceSongs) this.bindingSourceSongs.delete(songId);
          if (this.bindingSelectedSongs) this.bindingSelectedSongs.delete(songId);
        }

        const songsPanel = document.querySelector('.songs-panel');
        if (songsPanel) {
          this.rerenderPanel(songsPanel, () => this.renderSongs(songsPanel));
        }
        this.updateSelectionInfo();

        // 通知主UI刷新歌曲列表
        if (window.Jukebox && window.Jukebox.loadSongs) {
          window.Jukebox.loadSongs();
        }
      } catch (err) {
        console.error('切换歌曲可见性失败:', err);
        alert(window.t('Jukebox.operationFailed', '操作失败'));
      }
    },

    async toggleActionVisibility(actionId) {
      const action = this.data.actions[actionId];
      if (!action) return;

      const newVisible = action.visible === false ? true : false;
      try {
        await this.api.updateActionVisibility(actionId, newVisible);
        action.visible = newVisible;
        if (!newVisible) {
          if (this.selectedActions) this.selectedActions.delete(actionId);
          if (this.bindingSourceActions) this.bindingSourceActions.delete(actionId);
          if (this.bindingSelectedActions) this.bindingSelectedActions.delete(actionId);
        }

        const actionsPanel = document.querySelector('.actions-panel');
        if (actionsPanel) {
          this.rerenderPanel(actionsPanel, () => this.renderActions(actionsPanel));
        }
        const bindingsPanel = document.querySelector('.bindings-panel');
        if (bindingsPanel && bindingsPanel.innerHTML.trim()) {
          this.rerenderPanel(bindingsPanel, () => this.renderBindings(bindingsPanel));
        }
        this.updateSelectionInfo();

        if (window.Jukebox && window.Jukebox.loadSongs) {
          await window.Jukebox.loadSongs();
        }
      } catch (err) {
        console.error('切换动画可见性失败:', err);
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
          this.rerenderPanel(songsPanel, () => this.renderSongs(songsPanel));
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
          this.rerenderPanel(songsPanel, () => this.renderSongs(songsPanel));
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
          this.rerenderPanel(actionsPanel, () => this.renderActions(actionsPanel));
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
            this.rerenderPanel(songsPanel, () => this.renderSongs(songsPanel));
          }

          // 刷新绑定面板（如果打开的话）
          const bindingsPanel = document.querySelector('.bindings-panel');
          if (bindingsPanel && bindingsPanel.innerHTML.trim()) {
            this.rerenderPanel(bindingsPanel, () => this.renderBindings(bindingsPanel));
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

      const template = window.t('Jukebox.confirmDeleteSong', '确定要删除歌曲 "{{name}}" 吗？\n此操作不可恢复！');
      const message = template.replace('{{name}}', song.name);
      if (confirm(message)) {
        this.deleteSong(songId);
      }
    },

    confirmDeleteAction(actionId) {
      const action = this.data.actions[actionId];
      if (!action) return;

      const template = window.t('Jukebox.confirmDeleteAction', '确定要删除动画 "{{name}}" 吗？\n此操作不可恢复！');
      const message = template.replace('{{name}}', action.name);
      if (confirm(message)) {
        this.deleteAction(actionId);
      }
    },

    async deleteSong(songId) {
      try {
        const result = await this.api.deleteSong(songId);
        // 从选择集合中移除
        if (this.selectedSongs) this.selectedSongs.delete(songId);
        if (this.bindingSourceSongs) this.bindingSourceSongs.delete(songId);
        if (this.bindingSelectedSongs) this.bindingSelectedSongs.delete(songId);
        if (result?.hidden && this.data.songs[songId]) {
          this.data.songs[songId].visible = false;
        } else {
          delete this.data.songs[songId];
          delete this.data.bindings[songId];
        }

        // 刷新所有面板
        this.refreshAllPanels();
        // 通知点歌台播放器窗口同步刷新
        if (window.Jukebox && window.Jukebox.loadSongs) {
          window.Jukebox.loadSongs();
        }
      } catch (err) {
        console.error('删除歌曲失败:', err);
        alert(window.t('Jukebox.deleteFailed', '删除失败'));
      }
    },

    getSongDeleteMode() {
      const activeTab = this.element?.querySelector('.sam-tab.active')?.dataset.tab || 'songs';
      if (activeTab !== 'songs') return null;

      const visibleSongIds = this.getVisibleSongEntries().map(([id]) => id);
      const visibleSongIdSet = new Set(visibleSongIds);
      const selectedIds = Array.from(this.selectedSongs || []).filter(id => visibleSongIdSet.has(id));
      if (selectedIds.length > 0) {
        return { resource: 'songs', mode: 'selected', ids: selectedIds, songIds: selectedIds };
      }

      if (visibleSongIds.length > 0) {
        return { resource: 'songs', mode: 'clear-visible', ids: visibleSongIds, songIds: visibleSongIds };
      }

      return null;
    },

    getActionDeleteMode() {
      const activeTab = this.element?.querySelector('.sam-tab.active')?.dataset.tab || 'songs';
      if (activeTab !== 'actions') return null;

      const visibleActionIds = this.getVisibleActionEntries().map(([id]) => id);
      const visibleActionIdSet = new Set(visibleActionIds);
      const selectedIds = Array.from(this.selectedActions || []).filter(id => visibleActionIdSet.has(id));
      if (selectedIds.length > 0) {
        return { resource: 'actions', mode: 'selected', ids: selectedIds, actionIds: selectedIds };
      }

      if (visibleActionIds.length > 0) {
        return { resource: 'actions', mode: 'clear-visible', ids: visibleActionIds, actionIds: visibleActionIds };
      }

      return null;
    },

    getManagerDeleteMode() {
      const activeTab = this.element?.querySelector('.sam-tab.active')?.dataset.tab || 'songs';
      if (activeTab === 'songs') return this.getSongDeleteMode();
      if (activeTab === 'actions') return this.getActionDeleteMode();
      return null;
    },

    getSongDeleteSummary(songIds) {
      const songs = songIds.map(id => [id, this.data.songs[id]]).filter(([, song]) => !!song);
      return {
        total: songs.length,
        userCount: songs.filter(([, song]) => !song.isBuiltin).length,
        builtinCount: songs.filter(([, song]) => !!song.isBuiltin).length,
        hiddenCount: songs.filter(([, song]) => song.visible === false).length
      };
    },

    getActionDeleteSummary(actionIds) {
      const actions = actionIds.map(id => [id, this.data.actions[id]]).filter(([, action]) => !!action);
      return {
        total: actions.length,
        userCount: actions.filter(([, action]) => !action.isBuiltin).length,
        builtinCount: actions.filter(([, action]) => !!action.isBuiltin).length
      };
    },

    formatSongDeleteTooltip(mode, songIds) {
      const summary = this.getSongDeleteSummary(songIds);
      const scope = mode === 'selected'
        ? window.t('Jukebox.deleteSelectedScope', '选中的歌曲')
        : window.t('Jukebox.clearVisibleScope', '当前显示的歌曲');
      const hiddenHint = mode === 'clear-visible'
        ? (this.showHiddenSongs !== false
          ? window.t('Jukebox.clearVisibleIncludesHidden', '已开启显示隐藏歌曲，隐藏歌曲也会被处理。')
          : window.t('Jukebox.clearVisibleExcludesHidden', '未开启显示隐藏歌曲，隐藏歌曲不会被处理。'))
        : '';
      const template = window.t('Jukebox.songBatchDeleteTooltip', {
        defaultValue: '范围：{{scope}}\n共 {{total}} 首；自定义歌曲会删除，内置歌曲会隐藏。\n自定义：{{userCount}}，内置：{{builtinCount}}\n{{hiddenHint}}',
        scope,
        total: summary.total,
        userCount: summary.userCount,
        builtinCount: summary.builtinCount,
        hiddenHint
      });
      return template
        .replace('{{scope}}', scope)
        .replace('{{total}}', summary.total)
        .replace('{{userCount}}', summary.userCount)
        .replace('{{builtinCount}}', summary.builtinCount)
        .replace('{{hiddenHint}}', hiddenHint)
        .trim();
    },

    formatActionDeleteTooltip(mode, actionIds) {
      const summary = this.getActionDeleteSummary(actionIds);
      const scope = mode === 'selected'
        ? window.t('Jukebox.deleteSelectedActionScope', '选中的动画')
        : window.t('Jukebox.clearVisibleActionScope', '当前显示的动画');
      const hiddenHint = mode === 'clear-visible'
        ? (this.showHiddenActions !== false
          ? window.t('Jukebox.clearVisibleActionsIncludesHidden', '已开启显示隐藏动画，隐藏动画也会被处理。')
          : window.t('Jukebox.clearVisibleActionsExcludesHidden', '未开启显示隐藏动画，隐藏动画不会被处理。'))
        : '';
      const template = window.t('Jukebox.actionBatchDeleteTooltip', {
        defaultValue: '范围：{{scope}}\n共 {{total}} 个；自定义动画会删除，内置动画会隐藏。\n自定义：{{userCount}}，内置：{{builtinCount}}\n{{hiddenHint}}',
        scope,
        total: summary.total,
        userCount: summary.userCount,
        builtinCount: summary.builtinCount,
        hiddenHint
      });
      return template
        .replace('{{scope}}', scope)
        .replace('{{total}}', summary.total)
        .replace('{{userCount}}', summary.userCount)
        .replace('{{builtinCount}}', summary.builtinCount)
        .replace('{{hiddenHint}}', hiddenHint)
        .trim();
    },

    showManagerDeleteTooltip(button) {
      const state = this.getManagerDeleteMode();
      const wrapper = button?.closest('.sam-danger-action-wrap');
      const tooltip = wrapper?.querySelector('.sam-danger-tooltip');
      if (!state || !tooltip) return;
      tooltip.textContent = state.resource === 'actions'
        ? this.formatActionDeleteTooltip(state.mode, state.ids)
        : this.formatSongDeleteTooltip(state.mode, state.ids);
      tooltip.style.display = 'block';
    },

    hideManagerDeleteTooltip() {
      const tooltip = this.element?.querySelector('.sam-danger-tooltip');
      if (tooltip) tooltip.style.display = 'none';
    },

    showSongDeleteTooltip(button) {
      this.showManagerDeleteTooltip(button);
    },

    hideSongDeleteTooltip() {
      this.hideManagerDeleteTooltip();
    },

    confirmManagerBatchDelete() {
      const state = this.getManagerDeleteMode();
      if (!state || state.ids.length === 0) return;
      this.showResourceDeleteConfirmDialog(state.resource, state.mode, state.ids, 1);
    },

    confirmSongBatchDelete() {
      const state = this.getSongDeleteMode();
      if (!state || state.ids.length === 0) return;
      this.showResourceDeleteConfirmDialog('songs', state.mode, state.ids, 1);
    },

    confirmActionBatchDelete() {
      const state = this.getActionDeleteMode();
      if (!state || state.ids.length === 0) return;
      this.showResourceDeleteConfirmDialog('actions', state.mode, state.ids, 1);
    },

    closeSongDeleteDialog() {
      const dialog = document.querySelector('.sam-danger-modal-backdrop');
      if (dialog) dialog.remove();
    },

    getDangerDialogHost() {
      if (this.element && document.body.contains(this.element)) {
        return this.element;
      }
      return document.body;
    },

    showSongDeleteConfirmDialog(mode, songIds, step) {
      this.showResourceDeleteConfirmDialog('songs', mode, songIds, step);
    },

    showResourceDeleteConfirmDialog(resource, mode, ids, step) {
      this.closeSongDeleteDialog();
      const isActions = resource === 'actions';
      const summary = isActions ? this.getActionDeleteSummary(ids) : this.getSongDeleteSummary(ids);
      const isClear = mode === 'clear-visible';
      const isFinalClear = isClear && step === 2;
      const backdrop = document.createElement('div');
      backdrop.className = 'sam-danger-modal-backdrop';

      const dialog = document.createElement('div');
      dialog.className = `sam-danger-modal ${isFinalClear ? 'sam-danger-modal-final' : ''}`;
      dialog.setAttribute('role', 'dialog');
      dialog.setAttribute('aria-modal', 'true');

      const title = document.createElement('h3');
      if (isActions) {
        title.textContent = isClear
          ? window.t('Jukebox.clearVisibleActionsTitle', '删除当前显示')
          : window.t('Jukebox.deleteSelectedActionsTitle', '删除选中动画');
      } else {
        title.textContent = isClear
          ? window.t('Jukebox.clearVisibleSongsTitle', '删除当前显示')
          : window.t('Jukebox.deleteSelectedSongsTitle', '删除选中歌曲');
      }

      const body = document.createElement('p');
      if (isFinalClear) {
        body.textContent = isActions
          ? window.t('Jukebox.clearVisibleActionsSecondConfirm', '真..真的要删除当前显示吗..')
          : window.t('Jukebox.clearVisibleSongsSecondConfirm', '真..真的要删除当前显示吗..');
      } else if (isClear) {
        body.textContent = isActions
          ? window.t('Jukebox.clearVisibleActionsConfirm', {
            defaultValue: '将处理当前显示的 {{total}} 个动画。自定义动画会被删除，内置动画会被隐藏。此操作不可恢复。',
            total: summary.total
          }).replace('{{total}}', summary.total)
          : window.t('Jukebox.clearVisibleSongsConfirm', {
            defaultValue: '将处理当前显示的 {{total}} 首歌曲。自定义歌曲会被删除，内置歌曲会被隐藏。此操作不可恢复。',
            total: summary.total
          }).replace('{{total}}', summary.total);
      } else {
        body.textContent = isActions
          ? window.t('Jukebox.deleteSelectedActionsConfirm', {
            defaultValue: '将删除选中的 {{total}} 个动画。自定义动画会被删除，内置动画会被隐藏。此操作不可恢复。',
            total: summary.total
          }).replace('{{total}}', summary.total)
          : window.t('Jukebox.deleteSelectedSongsConfirm', {
            defaultValue: '将删除选中的 {{total}} 首歌曲。自定义歌曲会被删除，内置歌曲会被隐藏。此操作不可恢复。',
            total: summary.total
          }).replace('{{total}}', summary.total);
      }

      const detail = document.createElement('p');
      detail.className = 'sam-danger-modal-detail';
      detail.textContent = window.t(isActions ? 'Jukebox.actionBatchDeleteSummary' : 'Jukebox.songBatchDeleteSummary', {
        defaultValue: isActions
          ? '共 {{total}} 个；自定义 {{userCount}} 个，内置 {{builtinCount}} 个。'
          : '共 {{total}} 首；自定义 {{userCount}} 首，内置 {{builtinCount}} 首。',
        total: summary.total,
        userCount: summary.userCount,
        builtinCount: summary.builtinCount
      })
        .replace('{{total}}', summary.total)
        .replace('{{userCount}}', summary.userCount)
        .replace('{{builtinCount}}', summary.builtinCount);

      const actions = document.createElement('div');
      actions.className = `sam-danger-modal-actions ${isFinalClear ? 'sam-danger-modal-actions-reversed' : ''}`;

      const cancelBtn = document.createElement('button');
      cancelBtn.className = 'sam-danger-modal-cancel';
      cancelBtn.textContent = window.t('Jukebox.cancel', '取消');
      cancelBtn.onclick = () => this.closeSongDeleteDialog();

      const confirmZone = document.createElement('span');
      confirmZone.className = `sam-danger-confirm-zone ${isFinalClear ? 'sam-danger-confirm-zone-final' : ''}`;

      const confirmBtn = document.createElement('button');
      confirmBtn.className = 'sam-danger-modal-confirm';
      confirmBtn.textContent = isClear
        ? (isFinalClear
          ? window.t(isActions ? 'Jukebox.clearVisibleActionsNow' : 'Jukebox.clearVisibleSongsNow', '删除当前显示')
          : window.t('Jukebox.continue', '继续'))
        : window.t(isActions ? 'Jukebox.deleteSelectedActionsNow' : 'Jukebox.deleteSelectedSongsNow', '删除选中');

      if (isFinalClear) {
        confirmBtn.dataset.escapeReady = 'false';
      }

      if (isFinalClear) {
        confirmZone.onmouseenter = (event) => {
          if (confirmBtn.dataset.escaped === 'true') return;
          this.runFinalClearButtonEscape(confirmBtn, event, body);
        };
      }

      confirmBtn.onclick = (event) => {
        if (isFinalClear && confirmBtn.dataset.escapeReady !== 'true') {
          event.preventDefault();
          event.stopPropagation();
          if (confirmBtn.dataset.escaped !== 'true') {
            this.runFinalClearButtonEscape(confirmBtn, event, body);
          }
          return;
        }
        if (isClear && step === 1) {
          this.showResourceDeleteConfirmDialog(resource, mode, ids, 2);
          return;
        }
        if (isActions) {
          this.executeActionBatchDelete(mode, ids);
        } else {
          this.executeSongBatchDelete(mode, ids);
        }
      };

      confirmZone.appendChild(confirmBtn);
      actions.appendChild(cancelBtn);
      actions.appendChild(confirmZone);
      dialog.appendChild(title);
      dialog.appendChild(body);
      dialog.appendChild(detail);
      dialog.appendChild(actions);
      backdrop.appendChild(dialog);
      this.getDangerDialogHost().appendChild(backdrop);
      confirmBtn.focus();
    },

    runFinalClearButtonEscape(confirmBtn, event, promptEl) {
      confirmBtn.dataset.escaped = 'true';
      confirmBtn.classList.add('sam-danger-confirm-escaped', 'sam-danger-confirm-escaping');

      const buttonRect = confirmBtn.getBoundingClientRect();
      const promptRect = promptEl.getBoundingClientRect();
      const centerX = buttonRect.left + buttonRect.width / 2;
      const centerY = buttonRect.top + buttonRect.height / 2;
      const mouseX = Number.isFinite(event?.clientX) ? event.clientX : centerX + 1;
      const mouseY = Number.isFinite(event?.clientY) ? event.clientY : centerY;

      const normalize = (x, y, fallbackX, fallbackY) => {
        const length = Math.hypot(x, y);
        if (length < 0.001) return { x: fallbackX, y: fallbackY };
        return { x: x / length, y: y / length };
      };

      const escape = normalize(centerX - mouseX, centerY - mouseY, -1, 0);
      // Bias the curved path toward the early ".." area in the final warning text.
      const promptTargetX = promptRect.left + promptRect.width * 0.28;
      const promptTargetY = promptRect.top + promptRect.height * 0.48;
      const towardPrompt = normalize(promptTargetX - centerX, promptTargetY - centerY, 0, -1);
      const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

      const controlX = escape.x * 116;
      const controlY = escape.y * 54;
      const endX = clamp(escape.x * 96 + towardPrompt.x * 54, -150, 150);
      const endY = clamp(escape.y * 36 + towardPrompt.y * 70, -92, 42);
      confirmBtn.dataset.escapeInitialX = String(Math.round(controlX));
      confirmBtn.dataset.escapeInitialY = String(Math.round(controlY));
      confirmBtn.dataset.escapeX = String(Math.round(endX));
      confirmBtn.dataset.escapeY = String(Math.round(endY));

      const durationMs = 620;
      const startedAt = performance.now();
      const easeInOutSine = (t) => 0.5 - Math.cos(Math.PI * t) / 2;

      const tick = (now) => {
        const progress = Math.min(1, (now - startedAt) / durationMs);
        const eased = easeInOutSine(progress);
        const inv = 1 - eased;
        const x = inv * inv * 0 + 2 * inv * eased * controlX + eased * eased * endX;
        const y = inv * inv * 0 + 2 * inv * eased * controlY + eased * eased * endY;
        confirmBtn.style.transform = `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px)`;
        if (progress < 1) {
          requestAnimationFrame(tick);
        } else {
          confirmBtn.style.transform = `translate(${endX.toFixed(1)}px, ${endY.toFixed(1)}px)`;
          confirmBtn.classList.remove('sam-danger-confirm-escaping');
          confirmBtn.dataset.escapeReady = 'true';
        }
      };

      requestAnimationFrame(tick);
    },

    async executeSongBatchDelete(mode, songIds) {
      try {
        const result = await this.api.batchDeleteSongs(songIds);
        this.applySongBatchDeleteResult(result);
        this.closeSongDeleteDialog();
        this.showSongBatchDeleteResult(result);
        this.refreshAllPanels();
        if (window.Jukebox && window.Jukebox.loadSongs) {
          await window.Jukebox.loadSongs();
        }
      } catch (err) {
        console.error('批量删除歌曲失败:', err);
        this.closeSongDeleteDialog();
        alert(window.t('Jukebox.deleteFailed', '删除失败') + ': ' + (err.message || window.t('Jukebox.unknownError', '未知错误')));
      }
    },

    applySongBatchDeleteResult(result) {
      const deletedIds = (result.deleted || []).map(item => item.songId);
      const hiddenIds = (result.hidden || []).map(item => item.songId);
      const processedIds = new Set([...deletedIds, ...hiddenIds]);

      deletedIds.forEach(songId => {
        delete this.data.songs[songId];
        delete this.data.bindings[songId];
      });
      hiddenIds.forEach(songId => {
        if (this.data.songs[songId]) this.data.songs[songId].visible = false;
      });

      processedIds.forEach(songId => {
        if (this.selectedSongs) this.selectedSongs.delete(songId);
        if (this.bindingSourceSongs) this.bindingSourceSongs.delete(songId);
        if (this.bindingSelectedSongs) this.bindingSelectedSongs.delete(songId);
      });
    },

    showSongBatchDeleteResult(result) {
      const failed = result.failed || [];
      const failedNames = failed.slice(0, 5).map(item => item.name || item.songId).join(', ');
      let message = window.t('Jukebox.songBatchDeleteResult', {
        defaultValue: '已删除 {{deletedCount}} 首自定义歌曲，隐藏 {{hiddenCount}} 首内置歌曲。',
        deletedCount: result.deletedCount || 0,
        hiddenCount: result.hiddenCount || 0
      })
        .replace('{{deletedCount}}', result.deletedCount || 0)
        .replace('{{hiddenCount}}', result.hiddenCount || 0);

      if ((result.failedCount || 0) > 0) {
        message += '\n' + window.t('Jukebox.songBatchDeleteFailedSummary', {
          defaultValue: '{{failedCount}} 首处理失败：{{names}}',
          failedCount: result.failedCount,
          names: failedNames
        })
          .replace('{{failedCount}}', result.failedCount)
          .replace('{{names}}', failedNames);
      }

      const backdrop = document.createElement('div');
      backdrop.className = 'sam-danger-modal-backdrop sam-danger-result-backdrop';
      const dialog = document.createElement('div');
      dialog.className = 'sam-danger-modal sam-danger-result-modal';
      const title = document.createElement('h3');
      title.textContent = (result.failedCount || 0) > 0
        ? window.t('Jukebox.songBatchDeletePartialTitle', '处理完成，部分失败')
        : window.t('Jukebox.songBatchDeleteSuccessTitle', '处理完成');
      const body = document.createElement('p');
      body.textContent = message;
      const actions = document.createElement('div');
      actions.className = 'sam-danger-modal-actions';
      const okBtn = document.createElement('button');
      okBtn.className = 'sam-danger-modal-confirm';
      okBtn.textContent = window.t('Jukebox.confirm', '确认');
      okBtn.onclick = () => backdrop.remove();
      actions.appendChild(okBtn);
      dialog.appendChild(title);
      dialog.appendChild(body);
      dialog.appendChild(actions);
      backdrop.appendChild(dialog);
      this.getDangerDialogHost().appendChild(backdrop);
      okBtn.focus();
    },

    async executeActionBatchDelete(mode, actionIds) {
      try {
        const result = await this.api.batchDeleteActions(actionIds);
        this.applyActionBatchDeleteResult(result);
        this.closeSongDeleteDialog();
        this.showActionBatchDeleteResult(result);
        this.refreshAllPanels();
        if (window.Jukebox && window.Jukebox.loadSongs) {
          await window.Jukebox.loadSongs();
        }
      } catch (err) {
        console.error('批量删除动画失败:', err);
        this.closeSongDeleteDialog();
        alert(window.t('Jukebox.deleteFailed', '删除失败') + ': ' + (err.message || window.t('Jukebox.unknownError', '未知错误')));
      }
    },

    applyActionBatchDeleteResult(result) {
      const deletedIds = (result.deleted || []).map(item => item.actionId);
      const hiddenIds = (result.hidden || []).map(item => item.actionId);
      const processedIds = new Set([...deletedIds, ...hiddenIds]);

      deletedIds.forEach(actionId => {
        delete this.data.actions[actionId];
      });

      hiddenIds.forEach(actionId => {
        if (this.data.actions[actionId]) this.data.actions[actionId].visible = false;
      });

      processedIds.forEach(actionId => {
        if (this.selectedActions) this.selectedActions.delete(actionId);
        if (this.bindingSourceActions) this.bindingSourceActions.delete(actionId);
        if (this.bindingSelectedActions) this.bindingSelectedActions.delete(actionId);
      });

      deletedIds.forEach(actionId => {
        for (const songId in this.data.bindings) {
          delete this.data.bindings[songId][actionId];
          if (Object.keys(this.data.bindings[songId]).length === 0) {
            delete this.data.bindings[songId];
          }
        }

        for (const songId in this.data.songs) {
          if (this.data.songs[songId]?.defaultAction === actionId) {
            this.data.songs[songId].defaultAction = '';
          }
        }
      });
    },

    showActionBatchDeleteResult(result) {
      const failed = result.failed || [];
      const failedNames = failed.slice(0, 5).map(item => item.name || item.actionId).join(', ');
      let message = window.t('Jukebox.actionBatchDeleteResult', {
        defaultValue: '已删除 {{deletedCount}} 个自定义动画，隐藏 {{hiddenCount}} 个内置动画。',
        deletedCount: result.deletedCount || 0,
        hiddenCount: result.hiddenCount || 0
      })
        .replace('{{deletedCount}}', result.deletedCount || 0)
        .replace('{{hiddenCount}}', result.hiddenCount || 0);

      if ((result.failedCount || 0) > 0) {
        message += '\n' + window.t('Jukebox.actionBatchDeleteFailedSummary', {
          defaultValue: '{{failedCount}} 个处理失败：{{names}}',
          failedCount: result.failedCount,
          names: failedNames
        })
          .replace('{{failedCount}}', result.failedCount)
          .replace('{{names}}', failedNames);
      }

      const backdrop = document.createElement('div');
      backdrop.className = 'sam-danger-modal-backdrop sam-danger-result-backdrop';
      const dialog = document.createElement('div');
      dialog.className = 'sam-danger-modal sam-danger-result-modal';
      const title = document.createElement('h3');
      title.textContent = (result.failedCount || 0) > 0
        ? window.t('Jukebox.actionBatchDeletePartialTitle', '处理完成，部分失败')
        : window.t('Jukebox.actionBatchDeleteSuccessTitle', '处理完成');
      const body = document.createElement('p');
      body.textContent = message;
      const actions = document.createElement('div');
      actions.className = 'sam-danger-modal-actions';
      const okBtn = document.createElement('button');
      okBtn.className = 'sam-danger-modal-confirm';
      okBtn.textContent = window.t('Jukebox.confirm', '确认');
      okBtn.onclick = () => backdrop.remove();
      actions.appendChild(okBtn);
      dialog.appendChild(title);
      dialog.appendChild(body);
      dialog.appendChild(actions);
      backdrop.appendChild(dialog);
      this.getDangerDialogHost().appendChild(backdrop);
      okBtn.focus();
    },

    async deleteAction(actionId) {
      try {
        const result = await this.api.deleteAction(actionId);
        // 从选择集合中移除
        if (this.selectedActions) this.selectedActions.delete(actionId);
        if (this.bindingSourceActions) this.bindingSourceActions.delete(actionId);
        if (this.bindingSelectedActions) this.bindingSelectedActions.delete(actionId);
        if (result?.hidden && this.data.actions[actionId]) {
          this.data.actions[actionId].visible = false;
        } else {
          delete this.data.actions[actionId];

          // 从所有绑定中移除
          for (const songId in this.data.bindings) {
            delete this.data.bindings[songId][actionId];
            if (Object.keys(this.data.bindings[songId]).length === 0) {
              delete this.data.bindings[songId];
            }
          }
        }

        // 刷新所有面板
        this.refreshAllPanels();
        // 通知点歌台播放器窗口同步刷新
        if (window.Jukebox && window.Jukebox.loadSongs) {
          window.Jukebox.loadSongs();
        }
      } catch (err) {
        console.error('删除动画失败:', err);
        alert(window.t('Jukebox.deleteFailed', '删除失败'));
      }
    },

    // 刷新所有面板
    capturePanelScrollState(panel) {
      if (!panel) return null;

      return {
        panelScrollTop: panel.scrollTop,
        nestedScrolls: Array.from(panel.querySelectorAll('.sam-list, .sam-bindings-list')).map((el, index) => ({
          index,
          scrollTop: el.scrollTop
        }))
      };
    },

    restorePanelScrollState(panel, state) {
      if (!panel || !state) return;

      panel.scrollTop = state.panelScrollTop || 0;
      const nestedLists = panel.querySelectorAll('.sam-list, .sam-bindings-list');
      state.nestedScrolls?.forEach(({ index, scrollTop }) => {
        if (nestedLists[index]) {
          nestedLists[index].scrollTop = scrollTop;
        }
      });
    },

    rerenderPanel(panel, renderFn) {
      if (!panel || typeof renderFn !== 'function') return;

      const scrollState = this.capturePanelScrollState(panel);
      renderFn();
      this.restorePanelScrollState(panel, scrollState);
    },


    // 初始化选择集合
    initSelection() {
      if (!this.selectedSongs) this.selectedSongs = new Set();
      if (!this.selectedActions) this.selectedActions = new Set();
    },

    ensureBindingSelectionState() {
      if (!this.bindingSelectedSongs) this.bindingSelectedSongs = new Set();
      if (!this.bindingSelectedActions) this.bindingSelectedActions = new Set();
      if (!this.bindingSourceSongs) this.bindingSourceSongs = new Set(this.bindingSelectedSongs);
      if (!this.bindingSourceActions) this.bindingSourceActions = new Set(this.bindingSelectedActions);
    },

    initBindingSelection() {
      this.ensureBindingSelectionState();
      this.syncBindingSelection();
    },

    getVisibleSongEntries() {
      const showHidden = this.showHiddenSongs !== false;
      return Object.entries(this.data.songs).filter(([id, song]) => showHidden || song.visible !== false);
    },

    shouldShowSong(song) {
      const showHidden = this.showHiddenSongs !== false;
      return !!song && (showHidden || song.visible !== false);
    },

    shouldShowAction(action) {
      const showHidden = this.showHiddenActions !== false;
      return !!action && (showHidden || action.visible !== false);
    },

    getVisibleActionEntries() {
      return Object.entries(this.data.actions).filter(([, action]) => this.shouldShowAction(action));
    },

    pruneHiddenSongSelection() {
      const visibleSongIds = new Set(this.getVisibleSongEntries().map(([id]) => id));
      this.selectedSongs?.forEach(id => {
        if (!visibleSongIds.has(id)) this.selectedSongs.delete(id);
      });
      this.bindingSelectedSongs?.forEach(id => {
        if (!visibleSongIds.has(id)) this.bindingSelectedSongs.delete(id);
      });
      this.bindingSourceSongs?.forEach(id => {
        if (!visibleSongIds.has(id)) this.bindingSourceSongs.delete(id);
      });
      this.syncBindingSelection();
    },

    pruneHiddenActionSelection() {
      const visibleActionIds = new Set(this.getVisibleActionEntries().map(([id]) => id));
      this.selectedActions?.forEach(id => {
        if (!visibleActionIds.has(id)) this.selectedActions.delete(id);
      });
      this.bindingSelectedActions?.forEach(id => {
        if (!visibleActionIds.has(id)) this.bindingSelectedActions.delete(id);
      });
      this.bindingSourceActions?.forEach(id => {
        if (!visibleActionIds.has(id)) this.bindingSourceActions.delete(id);
      });
      this.syncBindingSelection();
    },

    areAllSongsSelected() {
      this.initSelection();
      const songs = this.getVisibleSongEntries();
      return songs.length > 0 && songs.every(([id]) => this.selectedSongs.has(id));
    },

    hasAnySongsSelected() {
      this.initSelection();
      return this.getVisibleSongEntries().some(([id]) => this.selectedSongs.has(id));
    },

    areAllActionsSelected() {
      this.initSelection();
      const actionIds = this.getVisibleActionEntries().map(([id]) => id);
      return actionIds.length > 0 && actionIds.every(id => this.selectedActions.has(id));
    },

    hasAnyActionsSelected() {
      this.initSelection();
      return this.getVisibleActionEntries().some(([id]) => this.selectedActions.has(id));
    },

    areAllBindingSongsSelected() {
      this.initBindingSelection();
      const songIds = this.getVisibleSongEntries().map(([id]) => id);
      return songIds.length > 0 && songIds.every(songId => this.bindingSelectedSongs.has(songId));
    },

    hasAnyBindingSongsSelected() {
      this.initBindingSelection();
      return this.getVisibleSongEntries().some(([songId]) => this.bindingSelectedSongs.has(songId));
    },

    areAllBindingActionsSelected() {
      this.initBindingSelection();
      const actionIds = this.getVisibleActionEntries().map(([id]) => id);
      return actionIds.length > 0 && actionIds.every(actionId => this.bindingSelectedActions.has(actionId));
    },

    hasAnyBindingActionsSelected() {
      this.initBindingSelection();
      return this.getVisibleActionEntries().some(([actionId]) => this.bindingSelectedActions.has(actionId));
    },

    syncCheckboxState(checkbox, checked, indeterminate) {
      if (!checkbox) return;
      checkbox.checked = !!checked;
      checkbox.indeterminate = !!indeterminate;
    },

    syncBindingSelection() {
      this.ensureBindingSelectionState();

      const songIds = new Set();
      const actionIds = new Set();

      this.bindingSourceSongs.forEach(songId => {
        if (!this.shouldShowSong(this.data.songs[songId])) return;
        songIds.add(songId);
        this.getSongBindings(songId).forEach(actionId => {
          if (this.shouldShowAction(this.data.actions[actionId])) {
            actionIds.add(actionId);
          }
        });
      });

      this.bindingSourceActions.forEach(actionId => {
        if (!this.shouldShowAction(this.data.actions[actionId])) return;
        actionIds.add(actionId);
        this.getActionBindings(actionId).forEach(songId => {
          if (this.shouldShowSong(this.data.songs[songId])) {
            songIds.add(songId);
          }
        });
      });

      this.bindingSelectedSongs = songIds;
      this.bindingSelectedActions = actionIds;

      return {
        songIds: Array.from(songIds),
        actionIds: Array.from(actionIds)
      };
    },

    getBindingBundleSelection() {
      this.initBindingSelection();
      return this.syncBindingSelection();
    },

    // 检查歌曲在绑定Tab是否应该显示勾选（合集逻辑：歌曲被勾选且其所有绑定的动画都被勾选）
    isSongFullySelectedInBindings(songId) {
      this.initBindingSelection();
      return this.bindingSelectedSongs.has(songId);
    },

    // 检查动画在绑定Tab是否应该显示勾选（合集逻辑：动画被勾选且其所有绑定的歌曲都被勾选）
    isActionFullySelectedInBindings(actionId) {
      this.initBindingSelection();
      return this.bindingSelectedActions.has(actionId);
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
      this.ensureBindingSelectionState();

      if (checked) {
        this.bindingSourceSongs.add(songId);
      } else {
        this.bindingSourceSongs.delete(songId);
      }

      this.syncBindingSelection();
      this.refreshAllPanels();
    },

    // 绑定Tab勾选动画：联动勾选/取消该动画绑定的所有歌曲
    toggleBindingActionSelect(actionId, checked) {
      this.ensureBindingSelectionState();

      if (checked) {
        this.bindingSourceActions.add(actionId);
      } else {
        this.bindingSourceActions.delete(actionId);
      }

      this.syncBindingSelection();
      this.refreshAllPanels();
    },

    // 歌曲Tab全选：只勾选歌曲本身
    toggleSelectAllSongs(checked) {
      this.initSelection();
      const songs = this.getVisibleSongEntries();

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

      this.getVisibleActionEntries().forEach(([id]) => {
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
      this.ensureBindingSelectionState();

      this.getVisibleSongEntries().forEach(([songId]) => {
        if (checked) {
          this.bindingSourceSongs.add(songId);
        } else {
          this.bindingSourceSongs.delete(songId);
        }
      });

      this.syncBindingSelection();
      this.refreshAllPanels();
    },

    // 绑定Tab全选动画（使用合集逻辑：只勾选满足条件的动画）
    toggleSelectAllBindingActions(checked) {
      this.ensureBindingSelectionState();

      this.getVisibleActionEntries().forEach(([actionId]) => {
        if (checked) {
          this.bindingSourceActions.add(actionId);
        } else {
          this.bindingSourceActions.delete(actionId);
        }
      });

      this.syncBindingSelection();
      this.refreshAllPanels();
    },

    // 刷新所有面板
    refreshAllPanels() {
      const songsPanel = document.querySelector('.songs-panel');
      if (songsPanel) this.rerenderPanel(songsPanel, () => this.renderSongs(songsPanel));

      const actionsPanel = document.querySelector('.actions-panel');
      if (actionsPanel) this.rerenderPanel(actionsPanel, () => this.renderActions(actionsPanel));

      const bindingsPanel = document.querySelector('.bindings-panel');
      if (bindingsPanel) this.rerenderPanel(bindingsPanel, () => this.renderBindings(bindingsPanel));

      this.updateSelectionInfo();
    },

    updateSelectionInfo() {
      const infoEl = document.getElementById('sam-selection-info');
      if (!infoEl) return;

      const songCount = this.selectedSongs?.size || 0;
      const actionCount = this.selectedActions?.size || 0;
      const bindingSongCount = this.bindingSelectedSongs?.size || 0;
      const bindingActionCount = this.bindingSelectedActions?.size || 0;
      const activeTab = this.element?.querySelector('.sam-tab.active')?.dataset.tab || 'songs';
      const bindingBundle = (bindingSongCount > 0 || bindingActionCount > 0)
        ? this.getBindingBundleSelection()
        : { songIds: [], actionIds: [] };

      let text = '';
      if (activeTab === 'bindings' && (bindingSongCount > 0 || bindingActionCount > 0)) {
        text = window.t('Jukebox.bindingsSelected', {
          defaultValue: '绑定页已选 {{bindingSongCount}} 首歌曲、{{bindingActionCount}} 个动画；绑定集合共 {{bundleSongCount}} 首歌曲、{{bundleActionCount}} 个动画',
          bindingSongCount,
          bindingActionCount,
          bundleSongCount: bindingBundle.songIds.length,
          bundleActionCount: bindingBundle.actionIds.length,
        });
      } else if (songCount > 0 || actionCount > 0) {
        text = window.t('Jukebox.selectedInfo', {
          defaultValue: '已选择 {{songCount}} 首歌曲，{{actionCount}} 个动画',
          songCount,
          actionCount,
        });
      }

      infoEl.textContent = text;

      // 切换导出按钮显示
      const hasSelection = songCount > 0 || actionCount > 0;
      const exportAllBtns = document.querySelectorAll('.sam-btn-export-all');
      const exportSelectedBtn = document.querySelector('.sam-btn-export-selected');
      const dangerWrap = document.querySelector('.sam-danger-action-wrap');
      const dangerBtn = document.querySelector('.sam-btn-song-danger');
      const hasBindingSelection = bindingSongCount > 0 || bindingActionCount > 0;
      const hasActiveSelection = activeTab === 'bindings' ? hasBindingSelection : hasSelection;

      exportAllBtns.forEach(btn => {
        btn.style.display = hasActiveSelection ? 'none' : '';
      });
      if (exportSelectedBtn) {
        exportSelectedBtn.style.display = hasActiveSelection ? '' : 'none';
      }

      if (dangerWrap && dangerBtn) {
        const deleteState = this.getManagerDeleteMode();
        if ((activeTab === 'songs' || activeTab === 'actions') && deleteState) {
          const isActions = deleteState.resource === 'actions';
          dangerWrap.style.display = '';
          dangerBtn.dataset.mode = deleteState.mode;
          dangerBtn.dataset.resource = deleteState.resource;
          dangerBtn.textContent = deleteState.mode === 'selected'
            ? window.t(isActions ? 'Jukebox.deleteSelectedActions' : 'Jukebox.deleteSelectedSongs', {
              defaultValue: '删除选中({{count}})',
              count: deleteState.ids.length,
            }).replace('{{count}}', deleteState.ids.length)
            : window.t(isActions ? 'Jukebox.clearVisibleActions' : 'Jukebox.clearVisibleSongs', {
              defaultValue: '删除当前显示({{count}})',
              count: deleteState.ids.length,
            }).replace('{{count}}', deleteState.ids.length);
          dangerBtn.title = deleteState.mode === 'selected'
            ? window.t(isActions ? 'Jukebox.deleteSelectedActionsTitle' : 'Jukebox.deleteSelectedSongsTitle', isActions ? '删除选中动画' : '删除选中歌曲')
            : window.t(isActions ? 'Jukebox.clearVisibleActionsTitle' : 'Jukebox.clearVisibleSongsTitle', '删除当前显示');
        } else {
          dangerWrap.style.display = 'none';
          this.hideManagerDeleteTooltip();
        }
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
      const activeTab = this.element?.querySelector('.sam-tab.active')?.dataset.tab || 'songs';
      if (activeTab === 'bindings') {
        const bundle = this.getBindingBundleSelection();
        if (bundle.songIds.length === 0 && bundle.actionIds.length === 0) {
          alert(window.t('Jukebox.selectExportFirst', '请先选择要导出的歌曲或动画'));
          return;
        }

        await this.exportByIds(bundle.songIds, bundle.actionIds, 'jukebox_binding_selected');
        return;
      }

      const songIds = Array.from(this.selectedSongs);
      const actionIds = Array.from(this.selectedActions);

      if (songIds.length === 0 && actionIds.length === 0) {
        alert(window.t('Jukebox.selectExportFirst', '请先选择要导出的歌曲或动画'));
        return;
      }

      await this.exportByIds(songIds, actionIds, 'jukebox_selected');
    },

    async exportByIds(songIds, actionIds, filenamePrefix) {
      try {
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
        a.download = `${filenamePrefix || 'jukebox_selected'}_${new Date().toISOString().slice(0, 10)}.zip`;
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
        if (actions && Object.prototype.hasOwnProperty.call(actions, actionId)) {
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
        <input type="text" class="sam-add-binding-input" placeholder="${Jukebox.escapeAttr(window.t('Jukebox.inputIndexOrName', '输入序号或名称'))}">
        <button class="sam-add-binding-confirm" data-tooltip="${Jukebox.escapeAttr(window.t('Jukebox.confirm', '确认'))}" aria-label="${Jukebox.escapeAttr(window.t('Jukebox.confirm', '确认'))}">✓</button>
        <button class="sam-add-binding-cancel" data-tooltip="${Jukebox.escapeAttr(window.t('Jukebox.cancel', '取消'))}" aria-label="${Jukebox.escapeAttr(window.t('Jukebox.cancel', '取消'))}">✕</button>
      `;

      // 替换按钮为输入框
      btn.style.display = 'none';
      container.appendChild(inputWrapper);

      // 获取可用项目（排除当前视图隐藏和已绑定的）
      const availableEntries = isSong ? this.getVisibleActionEntries() : this.getVisibleSongEntries();
      const currentBindings = isSong
        ? (this.data.bindings[sourceId] || {})
        : this.getActionBindings(sourceId);
      const boundIds = new Set(isSong ? Object.keys(currentBindings) : currentBindings);

      // 获取当前视图项目的序号映射
      const allItemsWithIndex = availableEntries
        .map(([id, item], index) => ({ id, item, originalIndex: index + 1 }));

      // 过滤：只排除已绑定的项目（被隐藏的歌曲也可以绑定）
      const filteredItems = allItemsWithIndex
        .filter(({ id }) => !boundIds.has(id));

      // 创建自定义下拉列表（使用原始序号）
      const dropdown = document.createElement('div');
      dropdown.className = 'sam-add-binding-dropdown';
      dropdown.innerHTML = filteredItems.map(({ id, item, originalIndex }) =>
        `<div class="sam-add-binding-option" data-id="${Jukebox.escapeAttr(id)}">
          <span class="sam-add-binding-option-index">${originalIndex}</span>
          <span class="sam-add-binding-option-name">${Jukebox.escapeHtml(item.name)}</span>
        </div>`
      ).join('');

      // 将下拉列表添加到输入框下方
      inputWrapper.style.position = 'relative';
      inputWrapper.appendChild(dropdown);

      const input = inputWrapper.querySelector('.sam-add-binding-input');
      const confirmBtn = inputWrapper.querySelector('.sam-add-binding-confirm');
      const cancelBtn = inputWrapper.querySelector('.sam-add-binding-cancel');
      Jukebox.setupTooltipOnce(confirmBtn, () => confirmBtn.dataset.tooltip || window.t('Jukebox.confirm', '确认'));
      Jukebox.setupTooltipOnce(cancelBtn, () => cancelBtn.dataset.tooltip || window.t('Jukebox.cancel', '取消'));

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
          const audioFiles = files.filter(f => {
            const ext = f.name.split('.').pop().toLowerCase();
            return this.Config.allowedAudioExts.includes(ext);
          });
          if (audioFiles.length === 0) {
            console.log('[SongActionManager] 没有检测到音频文件');
            return;
          }
          await this.uploadSongs(audioFiles);
        } else if (fileType === 'action') {
          const actionFiles = files.filter(f => {
            const ext = f.name.split('.').pop().toLowerCase();
            return this.Config.allowedActionExts.includes(ext);
          });
          if (actionFiles.length === 0) {
            console.log('[SongActionManager] 没有检测到动画文件');
            return;
          }
          await this.uploadActions(actionFiles);
        }
      });
    },

    async uploadSongs(files) {
      try {
        const metadata = files.map(() => ({}));
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
          name: f.name.replace(/\.[^/.]+$/, '')
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
      input.accept = '.mp3,.wav,.ogg,.flac,.vmd,.bvh,.fbx,.vrma,.zip,audio/*';
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
        if (this.Config.allowedAudioExts.includes(ext) || file.type.startsWith('audio/')) {
          songs.push(file);
        } else if (this.Config.allowedActionExts.includes(ext)) {
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

    // 导入歌曲文件（委托给 uploadSongs，使用正确的批量上传 API）
    importSongs: async function(files) {
      try {
        await this.uploadSongs(files);
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

    // 导入动作文件（委托给 uploadActions，使用正确的批量上传 API）
    importActions: async function(files) {
      try {
        await this.uploadActions(files);
        // 通知主UI刷新
        if (window.Jukebox && window.Jukebox.loadSongs) {
          window.Jukebox.loadSongs();
        }
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
      if (this._panelResizeCleanup) {
        this._panelResizeCleanup();
        this._panelResizeCleanup = null;
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
          padding: 0;
          border-radius: 12px;
          width: 450px;
          height: min(500px, calc(100vh - 24px));
          min-width: 420px;
          min-height: 360px;
          max-width: calc(100vw - 24px);
          max-height: calc(100vh - 24px);
          overflow: hidden;
          display: flex;
          flex-direction: column;
          box-sizing: border-box;
          border: ${C.panel.border};
          box-shadow: ${C.panel.shadow};
          backdrop-filter: blur(18px) saturate(1.12);
          -webkit-backdrop-filter: blur(18px) saturate(1.12);
          transition: border-color 0.3s, box-shadow 0.3s;
          pointer-events: auto;
          cursor: default;
          user-select: none;
          -webkit-user-select: none;
        }

        .sam-resize-handle {
          position: absolute;
          z-index: 130;
          -webkit-app-region: no-drag;
        }

        .sam-resize-handle[data-dir="n"]  { top: -3px; left: 12px; right: 12px; height: 6px; cursor: ns-resize; }
        .sam-resize-handle[data-dir="s"]  { bottom: -3px; left: 12px; right: 12px; height: 6px; cursor: ns-resize; }
        .sam-resize-handle[data-dir="w"]  { left: -3px; top: 12px; bottom: 12px; width: 6px; cursor: ew-resize; }
        .sam-resize-handle[data-dir="e"]  { right: -3px; top: 12px; bottom: 12px; width: 6px; cursor: ew-resize; }
        .sam-resize-handle[data-dir="nw"] { top: -3px; left: -3px; width: 18px; height: 18px; cursor: nwse-resize; }
        .sam-resize-handle[data-dir="ne"] { top: -3px; right: -3px; width: 18px; height: 18px; cursor: nesw-resize; }
        .sam-resize-handle[data-dir="sw"] { bottom: -3px; left: -3px; width: 18px; height: 18px; cursor: nesw-resize; }
        .sam-resize-handle[data-dir="se"] { bottom: -3px; right: -3px; width: 18px; height: 18px; cursor: nwse-resize; }

        .jukebox-sam-panel button,
        .jukebox-sam-panel a {
          cursor: pointer;
        }

        .jukebox-sam-panel input,
        .jukebox-sam-panel textarea {
          cursor: text;
        }

        .jukebox-sam-panel select {
          cursor: default;
        }

        .jukebox-sam-panel.sam-file-drag-over {
          border-color: ${C.functional.success};
          box-shadow: 0 0 0 4px ${C.functional.successBg};
        }

        .sam-header {
          display: grid;
          grid-template-columns: auto minmax(0, 1fr) auto;
          align-items: center;
          margin-bottom: 10px;
          padding: 12px 12px 10px 16px;
          border-bottom: 1px solid ${C.tabs.borderBottom};
          gap: 8px;
          cursor: grab;
          touch-action: none;
          min-height: 58px;
          box-sizing: border-box;
          overflow: visible;
        }

        .sam-title,
        .sam-drag-fill {
          cursor: grab;
        }

        .sam-header:active,
        .sam-title:active,
        .sam-drag-fill:active,
        body.sam-panel-dragging .sam-header,
        body.sam-panel-dragging .sam-title,
        body.sam-panel-dragging .sam-drag-fill {
          cursor: grabbing;
        }

        .sam-drag-fill {
          display: none;
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
          color: ${C.text.primary};
          min-width: 0;
          white-space: nowrap;
        }

        .sam-window-controls {
          display: flex;
          align-items: center;
          gap: 6px;
          flex-shrink: 0;
          -webkit-app-region: no-drag;
        }

        .sam-pin-btn,
        .sam-close-btn {
          background: rgba(255,255,255,0.46);
          border: 1px solid rgba(99,199,232,0.16);
          color: rgba(38,118,148,0.86);
          font-weight: 500;
          cursor: pointer;
          padding: 0;
          width: 36px;
          height: 36px;
          min-width: 36px;
          display: flex;
          align-items: center;
          justify-content: center;
          border-radius: 50%;
          line-height: 1;
          overflow: visible;
          transition: background 0.2s ease, color 0.2s ease, transform 0.2s ease;
          flex-shrink: 0;
          box-sizing: border-box;
        }

        .sam-close-btn {
          font-size: 28px;
        }

        .sam-pin-btn:hover,
        .sam-close-btn:hover {
          color: ${C.text.primary};
          background: ${C.tabs.tabHoverBg};
          transform: scale(1.04);
        }

        .sam-pin-btn.is-pinned {
          color: #fff;
          background: ${C.tabs.tabActiveBg};
          border-color: rgba(255,255,255,0.36);
        }

        .sam-tabs {
          display: flex;
          gap: 5px;
          justify-content: center;
          min-width: 0;
          overflow: hidden;
        }

        .sam-tab {
          background: rgba(255,255,255,0.42);
          border: 1px solid rgba(99,199,232,0.14);
          color: ${C.tabs.tabColor};
          padding: 6px 10px;
          min-width: 0;
          cursor: pointer;
          border-radius: 999px;
          font-weight: 600;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
          transition: background 0.2s ease, color 0.2s ease, transform 0.2s ease, box-shadow 0.2s ease;
        }

        .sam-tab:hover {
          color: ${C.text.primary};
          background: ${C.tabs.tabHoverBg};
          transform: translateY(-1px);
        }

        .sam-tab.active {
          color: white;
          background: ${C.tabs.tabActiveBg};
          box-shadow: ${C.tabs.tabActiveShadow};
        }

        .sam-content {
          flex: 1;
          overflow: hidden;
          min-height: 0;
          padding: 0 15px;
        }

        .sam-panel {
          display: none;
          height: 100%;
          min-height: 0;
          overflow: hidden;
        }

        .sam-panel.active {
          display: flex;
          flex-direction: column;
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
          background: ${C.functional.successSubtleBg};
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
          background: rgba(232,247,255,0.58);
          border: 1px dashed rgba(99,199,232,0.24);
          border-radius: 8px;
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
          border: ${C.item.border};
          border-radius: 10px;
          box-shadow: ${C.item.shadow};
          cursor: default;
          transition: background 0.2s ease, border-color 0.2s ease, transform 0.2s ease, box-shadow 0.2s ease;
        }

        .sam-item:hover {
          background: ${C.item.hoverBg};
          border-color: ${C.item.hoverBorder};
          transform: translateY(-1px);
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
          color: ${C.text.secondary};
          background: rgba(232,247,255,0.82);
          border: 1px solid rgba(99,199,232,0.18);
          padding: 2px 6px;
          border-radius: 999px;
        }

        .sam-missing-badge {
          font-size: 10px;
          color: ${C.functional.missing};
          background: ${C.functional.missingBg};
          padding: 2px 6px;
          border-radius: 999px;
        }

        .sam-item-bindings {
          display: flex;
          flex-wrap: wrap;
          gap: 4px;
          min-width: 0;
        }

        .sam-binding-tag {
          font-size: 10px;
          color: rgba(38, 118, 148, 0.9);
          background: ${C.functional.tagBg};
          border: 1px solid rgba(255,159,189,0.18);
          padding: 2px 6px;
          border-radius: 10px;
          display: inline-block;
          max-width: 180px;
          white-space: nowrap;
          overflow: hidden;
        }

        .jukebox-sam-panel [data-neko-marquee] {
          white-space: nowrap;
          overflow: hidden;
          text-overflow: clip;
          scrollbar-width: none;
          -ms-overflow-style: none;
        }

        .jukebox-sam-panel [data-neko-marquee]::-webkit-scrollbar {
          display: none;
        }

        .jukebox-sam-panel [data-neko-marquee].neko-marquee-active {
          cursor: default;
        }

        .jukebox-sam-panel [contenteditable][data-neko-marquee]:focus {
          overflow-x: auto;
          scrollbar-width: thin;
          -ms-overflow-style: auto;
        }

        .jukebox-sam-panel [contenteditable][data-neko-marquee]:focus::-webkit-scrollbar {
          display: block;
          height: 6px;
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
          margin: 6px;
          border-radius: 10px;
          background: rgba(232,247,255,0.52);
          border: 1px dashed rgba(99,199,232,0.24);
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
          background: ${C.functional.successStrongHoverBg};
        }

        .sam-bindings-container {
          display: flex;
          gap: 15px;
          width: 100%;
          flex: 1;
          min-width: 0;
          min-height: 0;
          overflow: hidden;
        }

        .sam-bindings-section {
          display: flex;
          flex-direction: column;
          flex: 1 1 0;
          min-width: 0;
          min-height: 0;
          overflow: hidden;
        }

        .sam-bindings-header {
          flex: 0 0 auto;
        }

        .sam-bindings-section h4 {
          margin: 0 0 10px 0;
          font-size: 13px;
          color: ${C.text.secondary};
          font-weight: 700;
        }

        .sam-bindings-list {
          display: flex;
          flex-direction: column;
          gap: 8px;
          flex: 1;
          min-height: 0;
          overflow-y: auto;
          overflow-x: hidden;
          padding: 10px;
          border: 2px dashed transparent;
          border-radius: 8px;
          transition: all 0.3s;
          min-width: 0;
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
          min-height: 66px;
          display: flex;
          flex-direction: column;
          gap: 6px;
          width: 100%;
          min-width: 0;
          box-sizing: border-box;
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
          background: ${C.functional.successEmphasisBg};
          transform: scale(1.02);
        }

        .sam-binding-item.drag-over-duplicate {
          border: 2px solid ${C.buttons.primary.bg};
          background: ${C.buttons.primary.softBg};
          transform: scale(1.02);
        }

        .sam-binding-item-main {
          display: flex;
          align-items: center;
          gap: 8px;
          width: 100%;
          min-width: 0;
          flex: 0 0 auto;
          min-height: 22px;
        }

        .sam-binding-item-index {
          font-size: 11px;
          color: ${C.text.secondary};
          background: rgba(232,247,255,0.82);
          border: 1px solid rgba(99,199,232,0.18);
          padding: 2px 6px;
          border-radius: 4px;
          min-width: 20px;
          text-align: center;
          flex-shrink: 0;
        }

        .sam-binding-item-main input[type="checkbox"] {
          flex-shrink: 0;
        }

        .sam-binding-item-name {
          font-weight: 650;
          color: rgba(28,48,68,0.94);
          flex: 1;
          min-width: 0;
          max-width: 100%;
        }

        .sam-binding-count {
          font-size: 11px;
          color: ${C.text.primary};
          background: ${C.functional.countBg};
          padding: 2px 6px;
          border-radius: 10px;
          min-width: 18px;
          text-align: center;
          flex-shrink: 0;
        }

        .sam-binding-item-tags {
          display: flex;
          flex-wrap: nowrap;
          gap: 4px;
          margin-top: 4px;
          padding-top: 4px;
          border-top: 1px solid ${C.borders.divider};
          min-width: 0;
          width: 100%;
          max-width: 100%;
          min-height: 24px;
          align-items: center;
          overflow: hidden;
          box-sizing: border-box;
          flex: 0 0 auto;
          position: relative;
          z-index: 1;
        }

        .sam-add-binding-btn {
          width: 22px;
          height: 22px;
          border-radius: 999px;
          border: 1px dashed rgba(99, 199, 232, 0.46);
          background: linear-gradient(160deg, rgba(255,255,255,0.88), rgba(232,247,255,0.72));
          color: #35a9c9;
          font-size: 14px;
          font-weight: 700;
          line-height: 1;
          cursor: pointer;
          display: flex;
          align-items: center;
          justify-content: center;
          transition: background 0.2s ease, border-color 0.2s ease, color 0.2s ease, transform 0.2s ease, box-shadow 0.2s ease;
          flex: 0 0 22px;
          box-sizing: border-box;
          box-shadow: 0 3px 8px rgba(78, 153, 190, 0.12);
        }

        .sam-add-binding-btn:hover {
          border-color: rgba(255, 159, 189, 0.5);
          color: rgba(28, 48, 68, 0.94);
          background: linear-gradient(135deg, rgba(99,199,232,0.24), rgba(255,159,189,0.2));
          transform: translateY(-1px);
          box-shadow: 0 5px 12px rgba(99, 199, 232, 0.18);
        }

        .sam-add-binding-input-wrapper {
          display: inline-flex;
          align-items: center;
          gap: 4px;
          position: relative;
        }

        .sam-add-binding-input {
          width: 92px;
          height: 24px;
          padding: 0 8px;
          font-size: 12px;
          border: 1px solid rgba(99, 199, 232, 0.32);
          border-radius: 999px;
          background: rgba(255, 255, 255, 0.82);
          color: ${C.text.primary};
          outline: none;
          box-sizing: border-box;
        }

        .sam-add-binding-input:focus {
          border-color: rgba(99, 199, 232, 0.72);
          box-shadow: 0 0 0 3px rgba(99, 199, 232, 0.14);
        }

        .sam-add-binding-confirm,
        .sam-add-binding-cancel {
          width: 22px;
          height: 22px;
          border: 1px solid transparent;
          border-radius: 999px;
          font-size: 10px;
          font-weight: 700;
          cursor: pointer;
          display: flex;
          align-items: center;
          justify-content: center;
          padding: 0;
          transition: background 0.2s ease, transform 0.2s ease, box-shadow 0.2s ease;
        }

        .sam-add-binding-confirm {
          background: ${C.functional.confirmBg};
          color: white;
        }

        .sam-add-binding-confirm:hover {
          background: ${C.functional.confirmHoverBg};
          transform: translateY(-1px);
          box-shadow: 0 5px 12px rgba(99, 199, 232, 0.2);
        }

        .sam-add-binding-cancel {
          background: ${C.functional.cancelBg};
          color: #b94356;
          border-color: rgba(217, 75, 97, 0.18);
        }

        .sam-add-binding-cancel:hover {
          background: ${C.functional.cancelHoverBg};
          transform: translateY(-1px);
        }

        .sam-add-binding-dropdown {
          position: absolute;
          top: 100%;
          left: 0;
          min-width: 200px;
          max-height: 200px;
          overflow-y: auto;
          background: ${C.functional.dropdownBg};
          border: 1px solid rgba(99, 199, 232, 0.26);
          border-radius: 10px;
          z-index: 1000;
          display: none;
          margin-top: 6px;
          box-shadow: 0 12px 26px rgba(78, 153, 190, 0.18);
          backdrop-filter: blur(12px);
          -webkit-backdrop-filter: blur(12px);
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
          max-width: 140px;
          display: inline-flex;
          align-items: center;
          gap: 4px;
          position: relative;
          overflow: hidden;
          min-width: 0;
          flex: 1 1 0;
          box-sizing: border-box;
          min-height: 20px;
          max-height: 20px;
        }

        .sam-binding-tag-label {
          display: inline-block;
          min-width: 0;
          overflow: hidden;
          white-space: nowrap;
          flex: 1 1 auto;
        }

        .sam-unbind-btn {
          width: 16px;
          height: 16px;
          border-radius: 50%;
          background: rgba(255, 239, 244, 0.92);
          border: 1px solid rgba(217, 75, 97, 0.32);
          color: #b94356;
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
          background: rgba(217, 75, 97, 0.18);
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
          background: ${C.functional.successEmphasisBg};
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
          background: rgba(255,255,255,0.62);
          border: 1px solid rgba(120, 203, 232, 0.22);
          border-radius: 8px;
          overflow: hidden;
        }

        .sam-import-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          padding: 12px 16px;
          background: rgba(255,255,255,0.58);
          border-bottom: 1px solid rgba(116, 190, 224, 0.18);
        }

        .sam-import-header h4 {
          margin: 0;
          font-size: 14px;
          color: ${C.text.primary};
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
          min-width: 0;
        }

        .sam-item-name {
          display: block;
          flex: 1;
          min-width: 0;
          max-width: 100%;
          overflow: hidden;
          white-space: nowrap;
          font-weight: 650;
          color: rgba(28,48,68,0.94);
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
          color: rgba(38,118,148,0.86);
          font-weight: 500;
          cursor: text;
          padding: 2px 4px;
          border-radius: 3px;
          transition: all 0.2s;
          margin-top: 4px;
          overflow: hidden;
          white-space: nowrap;
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
          flex-shrink: 0;
        }

        .sam-visibility-btn {
          width: 24px;
          height: 24px;
          border: 1px solid rgba(99, 199, 232, 0.28);
          background: linear-gradient(160deg, rgba(255,255,255,0.88), rgba(232,247,255,0.72));
          cursor: pointer;
          font-size: 14px;
          border-radius: 999px;
          transition: background 0.2s ease, color 0.2s ease, transform 0.2s ease;
          display: flex;
          align-items: center;
          justify-content: center;
          color: ${C.buttons.visibility.color};
        }

        .sam-visibility-btn:hover {
          background: ${C.buttons.visibility.hoverBg};
          color: ${C.buttons.visibility.hoverColor};
          transform: translateY(-1px);
        }

        .sam-visibility-btn.hidden {
          color: ${C.buttons.visibility.hiddenColor};
          border-color: rgba(255,159,189,0.28);
          background: rgba(255,159,189,0.14);
        }

        .sam-delete-btn {
          width: 24px;
          height: 24px;
          border: 1px solid rgba(217,75,97,0.24);
          background: linear-gradient(160deg, rgba(255,255,255,0.88), rgba(255,239,244,0.72));
          cursor: pointer;
          font-size: 14px;
          border-radius: 999px;
          transition: background 0.2s ease, transform 0.2s ease, box-shadow 0.2s ease;
          color: ${C.buttons.delete.color};
        }

        .sam-delete-btn:hover {
          background: rgba(217,75,97,0.18);
          transform: translateY(-1px);
          box-shadow: 0 5px 12px rgba(217,75,97,0.12);
        }

        .sam-checkbox {
          display: flex;
          align-items: center;
          gap: 6px;
          cursor: pointer;
          font-size: 12px;
          color: ${C.text.secondary};
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
          flex: 0 0 auto;
          padding: 12px 16px;
          background: ${C.footer.importBg};
          border-top: ${C.footer.borderTop};
        }

        .sam-footer-buttons {
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 12px;
          flex-wrap: wrap;
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
          background: rgba(232,247,255,0.5);
          border: 1px solid rgba(99,199,232,0.18);
          border-radius: 999px;
          padding: 4px 10px;
        }

        .sam-footer.drag-over {
          background: ${C.dropzone.overBg};
          border-top: ${C.dropzone.overBorder};
        }

        .sam-unified-hint {
          font-size: 11px;
          color: ${C.footer.shortcutColor};
          text-align: center;
          padding: 5px 10px;
          min-height: 20px;
          background: rgba(232,247,255,0.5);
          border: 1px solid rgba(99,199,232,0.18);
          border-radius: 999px;
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
          border: 1px solid rgba(99, 199, 232, 0.18);
          color: ${C.text.primary};
          border-radius: 999px;
          cursor: pointer;
          transition: all 0.3s;
          box-shadow: 0 4px 12px rgba(78, 153, 190, 0.1);
        }

        .sam-btn:hover {
          background: ${C.footer.buttonHoverBg};
          transform: translateY(-1px);
        }

        .sam-danger-action-wrap {
          position: relative;
          display: inline-flex;
        }

        .sam-btn-danger {
          background: rgba(217, 75, 97, 0.1);
          color: #b94356;
          border: 1px solid rgba(217, 75, 97, 0.26);
        }

        .sam-btn-danger:hover {
          background: rgba(217, 75, 97, 0.18);
        }

        .sam-danger-tooltip {
          position: absolute;
          left: 50%;
          bottom: calc(100% + 8px);
          transform: translateX(-50%);
          z-index: 30;
          display: none;
          width: max-content;
          max-width: 320px;
          padding: 8px 10px;
          border-radius: 8px;
          background: linear-gradient(160deg, rgba(255,255,255,0.96), rgba(232,247,255,0.92));
          border: 1px solid rgba(217,75,97,0.18);
          box-shadow: 0 10px 26px rgba(78, 153, 190, 0.18), 0 2px 8px rgba(217,75,97,0.1);
          color: ${C.text.primary};
          font-size: 12px;
          line-height: 1.45;
          text-align: left;
          white-space: pre-line;
          pointer-events: none;
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

        .sam-danger-modal-backdrop {
          position: fixed;
          inset: 0;
          z-index: 100040;
          display: flex;
          align-items: center;
          justify-content: center;
          background: rgba(54, 92, 118, 0.28);
          backdrop-filter: blur(6px);
          -webkit-backdrop-filter: blur(6px);
          -webkit-app-region: no-drag;
          pointer-events: auto;
        }

        .jukebox-sam-panel > .sam-danger-modal-backdrop {
          position: absolute;
          z-index: 120;
        }

        .sam-danger-modal {
          width: min(420px, calc(100vw - 32px));
          padding: 18px;
          border-radius: 12px;
          background: linear-gradient(160deg, rgba(255,255,255,0.96), rgba(232,247,255,0.92));
          border: 1px solid rgba(120,203,232,0.32);
          box-shadow: 0 18px 54px rgba(78,153,190,0.24), 0 4px 18px rgba(217,75,97,0.12);
          color: ${C.text.primary};
          pointer-events: auto;
        }

        .sam-danger-modal h3 {
          margin: 0 0 10px;
          font-size: 16px;
          font-weight: 600;
        }

        .sam-danger-modal p {
          margin: 0 0 10px;
          font-size: 13px;
          line-height: 1.55;
          white-space: pre-line;
        }

        .sam-danger-modal-detail {
          color: ${C.text.secondary};
          background: rgba(232,247,255,0.52);
          border: 1px solid rgba(99,199,232,0.18);
          border-radius: 8px;
          padding: 8px 10px;
        }

        .sam-danger-modal-actions {
          display: flex;
          justify-content: flex-end;
          align-items: center;
          gap: 12px;
          margin-top: 16px;
        }

        .sam-danger-modal-actions-reversed {
          flex-direction: row-reverse;
          justify-content: flex-start;
        }

        .sam-danger-modal-actions button {
          min-width: 88px;
          padding: 8px 14px;
          border-radius: 999px;
          border: 1px solid rgba(99,199,232,0.18);
          color: ${C.text.primary};
          cursor: pointer;
        }

        .sam-danger-modal-cancel {
          background: rgba(255,255,255,0.72);
        }

        .sam-danger-modal-confirm {
          background: rgba(217,75,97,0.16);
          border-color: rgba(217,75,97,0.26);
          color: #b94356;
        }

        .sam-danger-confirm-zone-final {
          display: inline-flex;
          padding: 12px;
          margin: -12px;
        }

        .sam-danger-confirm-escaped {
          will-change: transform;
        }

        .sam-danger-confirm-escaping {
          cursor: default;
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

});
