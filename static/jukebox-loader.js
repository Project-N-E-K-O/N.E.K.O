(function() {
  'use strict';

  function ensureNativeJukeboxFacade() {
    if (window.Jukebox) return;

    var facade = {
      __nativeBridgeFacade: true,
      State: {
        currentSong: null,
        isPlaying: false,
        isVMDPlaying: false,
        isPaused: false,
        savedIdleAnimationUrl: null,
        playRequestId: 0
      },

      toggle: function() {
        if (typeof window.__nekoJukeboxToggle === 'function') {
          window.__nekoJukeboxToggle();
        }
      },

      init: function() {},

      getPlayer: function() {
        return null;
      },

      getModelType: function() {
        var config = window.lanlan_config || {};
        var modelType = config.model_type || 'live2d';
        if (modelType === 'live3d') {
          var subType = String(config.live3d_sub_type || '').toLowerCase();
          return subType === 'vrm' ? 'vrm' : 'mmd';
        }
        return modelType;
      },

      playVMD: async function(vmdPath) {
        if (!vmdPath) return;
        if (!window.mmdManager || !window.mmdManager.animationModule) {
          console.warn('[Jukebox]', translate('Jukebox.vmdNotInit', 'MMD Manager 未初始化，跳过动画'));
          return;
        }

        var state = facade.State;
        state.playRequestId += 1;
        var playRequestId = state.playRequestId;

        try {
          if (!state.savedIdleAnimationUrl && window.mmdManager.currentAnimationUrl) {
            state.savedIdleAnimationUrl = window.mmdManager.currentAnimationUrl;
          }
          facade.stopVMD(true);
          if (typeof window.mmdManager.loadAnimation === 'function') {
            await window.mmdManager.loadAnimation(vmdPath);
          }
          if (playRequestId !== state.playRequestId) return;
          if (typeof window.mmdManager.playAnimation === 'function') {
            window.mmdManager.playAnimation('dance');
          } else if (window.mmdManager.animationModule && typeof window.mmdManager.animationModule.play === 'function') {
            window.mmdManager.animationModule.play();
          }
          state.isVMDPlaying = true;
          state.isPaused = false;
          state.isPlaying = true;
        } catch (error) {
          console.error('[Jukebox]', translate('Jukebox.vmdPlayFailed', 'VMD 播放失败'), error);
        }
      },

      playVRMA: async function(vrmaPath) {
        if (!vrmaPath) return;
        if (!window.vrmManager || typeof window.vrmManager.playVRMAAnimation !== 'function') {
          console.warn('[Jukebox] VRM Manager 未初始化，跳过动画');
          return;
        }

        var state = facade.State;
        state.playRequestId += 1;
        var playRequestId = state.playRequestId;

        try {
          facade.stopVMD(true);
          await window.vrmManager.playVRMAAnimation(vrmaPath, {
            loop: false,
            fadeInDuration: 0.5,
            fadeOutDuration: 0.5
          });
          if (playRequestId !== state.playRequestId) return;
          state.isVMDPlaying = true;
          state.isPaused = false;
          state.isPlaying = true;
        } catch (error) {
          console.error('[Jukebox] VRMA 播放失败:', error);
        }
      },

      stopVMD: function(skipIdleRestore) {
        var state = facade.State;
        if (!state.isVMDPlaying) return;

        var modelType = facade.getModelType();
        if (modelType === 'vrm') {
          if (window.vrmManager && typeof window.vrmManager.stopVRMAAnimation === 'function') {
            window.vrmManager.stopVRMAAnimation();
          }
        } else if (window.mmdManager && window.mmdManager.animationModule &&
            typeof window.mmdManager.animationModule.stop === 'function') {
          window.mmdManager.animationModule.stop();
        }

        state.isVMDPlaying = false;
        state.isPaused = false;
        state.isPlaying = false;

        if (!skipIdleRestore) {
          facade.restoreIdleAnimation();
        }
      },

      restoreIdleAnimation: async function() {
        var state = facade.State;
        state.playRequestId += 1;
        var restoreRequestId = state.playRequestId;
        var modelType = facade.getModelType();

        if (modelType === 'vrm' && window.vrmManager && typeof window.vrmManager.playVRMAAnimation === 'function') {
          try {
            var vrmIdleList = window.lanlan_config && window.lanlan_config.vrmIdleAnimations;
            var vrmIdleUrl = Array.isArray(vrmIdleList) && vrmIdleList.length > 0 ? vrmIdleList[0] : null;
            if (!vrmIdleUrl) {
              vrmIdleUrl = window.lanlan_config && window.lanlan_config.vrmIdleAnimation;
            }
            await window.vrmManager.playVRMAAnimation(vrmIdleUrl || '/static/vrm/animation/wait03.vrma', {
              loop: true,
              isIdle: true
            });
          } catch (error) {
            console.warn('[Jukebox] VRM 待机动画恢复失败:', error);
          }
          return;
        }

        if (!window.mmdManager) return;

        var idleUrl = state.savedIdleAnimationUrl;
        if (idleUrl && idleUrl.indexOf('/jukebox/song_') >= 0) {
          idleUrl = null;
        }
        if (!idleUrl) {
          facade._resetToNoneMode();
          return;
        }

        try {
          if (typeof window.mmdManager.loadAnimation === 'function') {
            await window.mmdManager.loadAnimation(idleUrl);
          }
          if (restoreRequestId !== state.playRequestId) return;
          if (typeof window.mmdManager.playAnimation === 'function') {
            window.mmdManager.playAnimation('idle');
          }
        } catch (error) {
          console.warn('[Jukebox]', translate('Jukebox.idleRestoreFailed', '恢复待机动画失败'), error);
          if (restoreRequestId !== state.playRequestId) return;
          facade._resetToNoneMode();
        }
      },

      _resetToNoneMode: function() {
        if (!window.mmdManager) return;
        var mesh = window.mmdManager.currentModel && window.mmdManager.currentModel.mesh;
        if (mesh && mesh.skeleton && typeof mesh.skeleton.pose === 'function') {
          mesh.skeleton.pose();
        }
        if (window.mmdManager.cursorFollow && typeof window.mmdManager.cursorFollow.setAnimationMode === 'function') {
          window.mmdManager.cursorFollow.setAnimationMode('none');
        }
      },

      togglePause: function() {
        var state = facade.State;
        if (!state.currentSong && !state.isVMDPlaying) return;

        var modelType = facade.getModelType();
        if (state.isPaused) {
          if (modelType === 'vrm') {
            var resumeVrmAnim = window.vrmManager &&
              (window.vrmManager.animationModule || window.vrmManager.animation);
            if (resumeVrmAnim && resumeVrmAnim.currentAction) {
              resumeVrmAnim.currentAction.paused = false;
            }
          } else if (window.mmdManager && window.mmdManager.animationModule) {
            if (typeof window.mmdManager.animationModule.play === 'function') {
              window.mmdManager.animationModule.play();
            }
            if (window.mmdManager.cursorFollow && typeof window.mmdManager.cursorFollow.setAnimationMode === 'function') {
              window.mmdManager.cursorFollow.setAnimationMode('dance');
            }
          }
          state.isPaused = false;
          state.isPlaying = true;
        } else if (state.isPlaying || state.isVMDPlaying) {
          if (modelType === 'vrm') {
            var pauseVrmAnim = window.vrmManager &&
              (window.vrmManager.animationModule || window.vrmManager.animation);
            if (pauseVrmAnim && pauseVrmAnim.currentAction) {
              pauseVrmAnim.currentAction.paused = true;
            }
          } else if (window.mmdManager && window.mmdManager.animationModule) {
            if (typeof window.mmdManager.animationModule.pause === 'function') {
              window.mmdManager.animationModule.pause();
            }
            if (window.mmdManager.cursorFollow && typeof window.mmdManager.cursorFollow.setAnimationMode === 'function') {
              window.mmdManager.cursorFollow.setAnimationMode('idle');
            }
          }
          state.isPaused = true;
          state.isPlaying = false;
        }
      }
    };

    window.Jukebox = facade;
    window.Jukebox_togglePause = facade.togglePause;
  }

  var SCRIPT_ID = 'neko-jukebox-script';
  var TOAST_ID = 'neko-jukebox-loader-toast';
  var STYLE_ID = 'neko-jukebox-loader-style';
  var REQUIRED_CONTROL_API_VERSION = 3;
  var currentScript = document.currentScript;
  var assetQuery = getAssetQuery(currentScript && currentScript.src);
  var loadPromise = null;
  var toggleInFlight = false;
  var toastTimer = null;
  var unloadTimer = null;
  var toastShownAt = 0;
  var MIN_INITIALIZING_TOAST_MS = 650;

  window.__NEKO_JUKEBOX_LAZY_LOADER__ = true;

  function hasRequiredControlApi(jukebox) {
    if (!jukebox || typeof jukebox.executeControl !== 'function') return false;
    var version = Number(jukebox.controlApiVersion || jukebox.__controlApiVersion || 0);
    if (version >= REQUIRED_CONTROL_API_VERSION) return true;
    return Array.isArray(jukebox.supportedControlActions)
      && jukebox.supportedControlActions.indexOf('adjust_volume') >= 0
      && jukebox.supportedControlActions.indexOf('set_mode') >= 0
      && jukebox.supportedControlActions.indexOf('previous') >= 0;
  }

  function isLoadedJukebox(jukebox) {
    return !!(
      jukebox
      && !jukebox.__nekoLazyFacade
      && !jukebox.__nativeBridgeFacade
      && hasRequiredControlApi(jukebox)
    );
  }

  function getAssetQuery(src) {
    if (!src) return '';
    try {
      var url = new URL(src, window.location.href);
      return url.search || '';
    } catch (_) {
      var queryIndex = src.indexOf('?');
      return queryIndex >= 0 ? src.slice(queryIndex) : '';
    }
  }

  function buildJukeboxScriptSrc() {
    try {
      var url = new URL('/static/Jukebox.js', window.location.href);
      if (assetQuery) {
        var inherited = new URLSearchParams(assetQuery.replace(/^\?/, ''));
        inherited.forEach(function(value, key) {
          url.searchParams.set(key, value);
        });
      }
      url.searchParams.set('jukebox_control_api', String(REQUIRED_CONTROL_API_VERSION));
      return url.pathname + url.search;
    } catch (_) {
      var query = assetQuery || '';
      var separator = query ? '&' : '?';
      return '/static/Jukebox.js' + query + separator + 'jukebox_control_api=' + encodeURIComponent(String(REQUIRED_CONTROL_API_VERSION));
    }
  }

  function translate(key, fallback) {
    try {
      if (typeof window.t === 'function') {
        return window.t(key, fallback) || fallback;
      }
    } catch (_) {}
    return fallback;
  }

  function ensureToastStyle() {
    if (document.getElementById(STYLE_ID)) return;

    var style = document.createElement('style');
    style.id = STYLE_ID;
    style.textContent = [
      '.neko-jukebox-loader-toast {',
      '  position: fixed;',
      '  right: 22px;',
      '  bottom: 92px;',
      '  z-index: 10050;',
      '  display: inline-flex;',
      '  align-items: center;',
      '  gap: 10px;',
      '  max-width: min(320px, calc(100vw - 32px));',
      '  padding: 10px 14px;',
      '  border-radius: 8px;',
      '  color: rgba(28, 48, 68, 0.94);',
      '  background: rgba(255, 255, 255, 0.94);',
      '  border: 1px solid rgba(116, 190, 224, 0.28);',
      '  box-shadow: 0 12px 34px rgba(78, 153, 190, 0.24);',
      '  font-size: 14px;',
      '  line-height: 1.35;',
      '  opacity: 0;',
      '  transform: translateY(8px);',
      '  pointer-events: none;',
      '  transition: opacity 160ms ease, transform 160ms ease;',
      '}',
      '.neko-jukebox-loader-toast.visible {',
      '  opacity: 1;',
      '  transform: translateY(0);',
      '}',
      '.neko-jukebox-loader-toast.error {',
      '  color: #8f2230;',
      '  border-color: rgba(217, 75, 97, 0.32);',
      '  box-shadow: 0 12px 34px rgba(217, 75, 97, 0.18);',
      '}',
      '.neko-jukebox-loader-spinner {',
      '  width: 14px;',
      '  height: 14px;',
      '  flex: 0 0 auto;',
      '  border-radius: 50%;',
      '  border: 2px solid rgba(53, 169, 201, 0.24);',
      '  border-top-color: #35a9c9;',
      '  animation: neko-jukebox-loader-spin 800ms linear infinite;',
      '}',
      '.neko-jukebox-loader-toast.error .neko-jukebox-loader-spinner {',
      '  display: none;',
      '}',
      '@keyframes neko-jukebox-loader-spin {',
      '  to { transform: rotate(360deg); }',
      '}',
      'html[data-theme="dark"] .neko-jukebox-loader-toast {',
      '  color: rgba(230, 237, 243, 0.94);',
      '  background: rgba(15, 23, 42, 0.94);',
      '  border-color: rgba(124, 218, 244, 0.2);',
      '  box-shadow: 0 12px 34px rgba(2, 8, 23, 0.42);',
      '}',
      '@media (max-width: 640px) {',
      '  .neko-jukebox-loader-toast {',
      '    right: 16px;',
      '    bottom: 74px;',
      '  }',
      '}'
    ].join('\n');
    document.head.appendChild(style);
  }

  function getToast() {
    ensureToastStyle();
    var toast = document.getElementById(TOAST_ID);
    if (toast) return toast;

    toast = document.createElement('div');
    toast.id = TOAST_ID;
    toast.className = 'neko-jukebox-loader-toast';
    toast.setAttribute('role', 'status');
    toast.setAttribute('aria-live', 'polite');
    toast.innerHTML = '<span class="neko-jukebox-loader-spinner" aria-hidden="true"></span><span class="neko-jukebox-loader-message"></span>';
    document.body.appendChild(toast);
    return toast;
  }

  function showToast(message, isError) {
    if (toastTimer) {
      clearTimeout(toastTimer);
      toastTimer = null;
    }
    toastShownAt = Date.now();

    var toast = getToast();
    var messageEl = toast.querySelector('.neko-jukebox-loader-message');
    if (messageEl) messageEl.textContent = message;
    toast.classList.toggle('error', !!isError);
    requestAnimationFrame(function() {
      toast.classList.add('visible');
    });
  }

  function hideToast(delay) {
    var toast = document.getElementById(TOAST_ID);
    if (!toast) return;

    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(function() {
      toast.classList.remove('visible');
      toastTimer = setTimeout(function() {
        if (toast.parentNode && !toast.classList.contains('visible')) {
          toast.remove();
        }
        toastTimer = null;
      }, 180);
    }, delay || 0);
  }

  function getInitializingToastDelay(fallbackDelay) {
    var elapsed = Date.now() - toastShownAt;
    var remaining = MIN_INITIALIZING_TOAST_MS - elapsed;
    return Math.max(fallbackDelay || 0, remaining > 0 ? remaining : 0);
  }

  function showInitializing() {
    showToast(translate('Jukebox.initializing', '正在初始化点歌台...'), false);
  }

  function showInitializeFailed(error) {
    console.error('[JukeboxLoader] 初始化失败:', error);
    showToast(translate('Jukebox.initializeFailed', '点歌台初始化失败'), true);
    hideToast(2800);
  }

  function clearPendingUnload() {
    if (unloadTimer) {
      clearTimeout(unloadTimer);
      unloadTimer = null;
    }
  }

  function ensureLazyJukeboxFacade() {
    if (isLoadedJukebox(window.Jukebox)) return window.Jukebox;
    if (window.Jukebox && window.Jukebox.__nekoLazyFacade) return window.Jukebox;

    var facade = window.Jukebox || {
      State: {
        isOpen: false,
        isHidden: false,
        currentSong: null,
        isPlaying: false,
        isPaused: false,
        isVMDPlaying: false
      },
      toggle: toggleJukebox
    };
    if (!facade.__nativeBridgeFacade) {
      facade.__nekoLazyFacade = true;
    }
    if (typeof facade.toggle !== 'function') {
      facade.toggle = toggleJukebox;
    }
    if (typeof facade.init !== 'function') {
      facade.init = function() {};
    }
    facade.ensureRuntime = async function(options) {
      var jukebox = await loadJukeboxScript();
      initJukebox(jukebox);
      if (!jukebox || typeof jukebox.ensureRuntime !== 'function') {
        throw new Error('Jukebox runtime API unavailable');
      }
      return jukebox.ensureRuntime(options || {});
    };
    facade.executeControl = async function(command) {
      var jukebox = await loadJukeboxScript();
      initJukebox(jukebox);
      if (!jukebox || typeof jukebox.executeControl !== 'function') {
        throw new Error('Jukebox control API unavailable');
      }
      return jukebox.executeControl(command || {});
    };
    window.Jukebox = facade;
    return window.Jukebox;
  }

  function loadJukeboxScript() {
    if (isLoadedJukebox(window.Jukebox)) return Promise.resolve(window.Jukebox);
    if (loadPromise) return loadPromise;

    loadPromise = new Promise(function(resolve, reject) {
      var existingScript = document.getElementById(SCRIPT_ID);
      if (existingScript) existingScript.remove();

      var script = document.createElement('script');
      script.id = SCRIPT_ID;
      script.src = buildJukeboxScriptSrc();
      script.async = true;
      script.dataset.nekoJukeboxLazy = 'true';
      script.onload = function() {
        if (window.Jukebox) {
          console.log('[JukeboxLoader] 点歌台资源已加载');
          resolve(window.Jukebox);
        } else {
          reject(new Error('Jukebox global missing after script load'));
        }
      };
      script.onerror = function() {
        script.remove();
        loadPromise = null;
        reject(new Error('Failed to load Jukebox.js'));
      };
      console.log('[JukeboxLoader] 按需加载点歌台资源');
      document.body.appendChild(script);
    });

    return loadPromise;
  }

  function initJukebox(jukebox) {
    if (!jukebox || jukebox.__nekoLazyLoaderInitialized) return;
    if (typeof jukebox.init === 'function') {
      jukebox.init();
    }
    if (typeof jukebox.executeControl === 'function' && !jukebox.__nekoLazyLoaderExecuteWrapped) {
      var originalExecuteControl = jukebox.executeControl;
      jukebox.executeControl = function(command) {
        clearPendingUnload();
        return originalExecuteControl.call(jukebox, command);
      };
      jukebox.__nekoLazyLoaderExecuteWrapped = true;
    }
    if (typeof jukebox.ensureRuntime === 'function' && !jukebox.__nekoLazyLoaderRuntimeWrapped) {
      var originalEnsureRuntime = jukebox.ensureRuntime;
      jukebox.ensureRuntime = function(options) {
        clearPendingUnload();
        return originalEnsureRuntime.call(jukebox, options);
      };
      jukebox.__nekoLazyLoaderRuntimeWrapped = true;
    }
    jukebox.__nekoLazyLoaderInitialized = true;
  }

  function showOrOpenJukebox(jukebox) {
    if (!jukebox || !jukebox.State) return;

    if (jukebox.State.isHidden && typeof jukebox.show === 'function') {
      jukebox.show();
    } else if (jukebox.State.isOpen && typeof jukebox.hide === 'function') {
      jukebox.hide();
    } else if (typeof jukebox.open === 'function') {
      jukebox.open();
    }
  }

  async function toggleJukebox() {
    clearPendingUnload();

    if (toggleInFlight) {
      showInitializing();
      return;
    }

    if (window.Jukebox && window.Jukebox.State && !window.Jukebox.__nekoLazyFacade) {
      showOrOpenJukebox(window.Jukebox);
      return;
    }

    toggleInFlight = true;
    showInitializing();

    try {
      var jukebox = await loadJukeboxScript();
      initJukebox(jukebox);
      showOrOpenJukebox(jukebox);
      hideToast(getInitializingToastDelay(180));
    } catch (error) {
      showInitializeFailed(error);
    } finally {
      toggleInFlight = false;
    }
  }

  function finalizeUnload() {
    unloadTimer = null;
    var jukebox = window.Jukebox;
    if (jukebox && typeof jukebox.cleanupCloseListener === 'function') {
      try {
        jukebox.cleanupCloseListener();
      } catch (_) {}
    }
    if (window.__JukeboxLocaleChangeHandler) {
      try {
        window.removeEventListener('localechange', window.__JukeboxLocaleChangeHandler);
      } catch (_) {}
      window.__JukeboxLocaleChangeHandler = null;
    }
    [
      'Jukebox',
      'Jukebox_playSong',
      'Jukebox_close',
      'Jukebox_hide',
      'Jukebox_updateVolume',
      'Jukebox_logVolumeChange',
      'Jukebox_togglePause',
      '__JukeboxLocaleChangeHandler'
    ].forEach(function(name) {
      try {
        delete window[name];
      } catch (_) {
        window[name] = undefined;
      }
    });
    ensureLazyJukeboxFacade();
    console.log('[JukeboxLoader] 点歌台资源已卸载');
  }

  function unloadJukebox() {
    loadPromise = null;
    toggleInFlight = false;
    console.log('[JukeboxLoader] 点歌台完全关闭，准备卸载资源');

    var script = document.getElementById(SCRIPT_ID);
    if (script) script.remove();

    if (unloadTimer) clearTimeout(unloadTimer);
    unloadTimer = setTimeout(finalizeUnload, 3000);
  }

  function getState() {
    var jukebox = window.Jukebox;
    return {
      hasJukeboxGlobal: !!jukebox,
      hasScriptTag: !!document.getElementById(SCRIPT_ID),
      hasUi: !!document.querySelector('.jukebox-wrapper'),
      isOpen: !!(jukebox && jukebox.State && jukebox.State.isOpen),
      isHidden: !!(jukebox && jukebox.State && jukebox.State.isHidden),
      pendingUnload: !!unloadTimer
    };
  }

  window.addEventListener('neko:jukebox-full-close', unloadJukebox);

  if (typeof window.__nekoJukeboxToggle === 'function') {
    ensureNativeJukeboxFacade();
  }
  ensureLazyJukeboxFacade();

  if (typeof window.__nekoJukeboxToggle !== 'function') {
    window.__nekoJukeboxToggle = toggleJukebox;
    window.__nekoJukeboxToggle.__nekoJukeboxWebLoader = true;
  }
  window.__nekoJukeboxLoader = {
    load: loadJukeboxScript,
    toggle: toggleJukebox,
    unload: unloadJukebox,
    getState: getState
  };
})();
