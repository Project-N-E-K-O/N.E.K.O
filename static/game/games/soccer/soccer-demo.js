(() => {
  const runtimeConfigElement = document.getElementById('soccer-runtime-config');
  let runtimeConfig = {};
  try {
    runtimeConfig = JSON.parse(runtimeConfigElement?.textContent || '{}');
  } catch (error) {
    console.error('[soccer_demo] 运行时配置解析失败:', error);
  }
  window.VRM_DEFAULT_LIGHTING = Object.freeze(runtimeConfig.vrm_defaults || {});

  window.__NEKO_DISABLE_AVATAR_IDLE_THROTTLE__ = true;

    // mini-game 邀请被接受后由 chat.html / Pet 主聊天 window.open 打开本页面，
    // URL 形如 `/soccer_demo?lanlan_name=<active_character>&session_id=<invite_uuid>`。
    // 提前从 query 解出来，覆盖默认 'soccer_demo' 值，避免后端 game route 用错角色。
    // 直接手敲 /soccer_demo 进来时 query 缺失，先通过
    // /api/game/soccer/character 解出当前角色，再启动台词与 game route 请求。
    (function () {
      var params = null;
      try { params = new URLSearchParams(window.location.search); } catch (_) { params = null; }
      var queryLanlan = (params && params.get('lanlan_name')) || '';
      var queryInviteSession = (params && params.get('session_id')) || '';
      window.lanlan_config = window.lanlan_config || {
        lanlan_name: queryLanlan || 'soccer_demo',
        master_name: '', master_profile_name: '', master_nickname: '', master_display_name: '',
        model_type: 'vrm', live3d_sub_type: 'vrm'
      };
      if (queryLanlan) {
        window.lanlan_config.lanlan_name = queryLanlan;
      }
      window.__SoccerResolvedLanlanName = queryLanlan || '';
      // 留个全局让游戏内逻辑可见 invite 来源（例如赛后归档时打个 tag）
      window.__nekoMiniGameInviteSessionId = queryInviteSession || '';
    })();
    window.LanLan1 = window.LanLan1 || {};
    // 注意：mouseTrackingEnabled 要 true，不然 VRM 的眼/头 cursor-follow 不会跟鼠标
    window.mouseTrackingEnabled = true;
    window.live2dFullscreenTrackingEnabled = false;
    // 阻止 vrm-init.js 自动加载默认模型到 #vrm-container（id 对不上会出事），
    // 同时让 manager.loadModel 跳过 setupFloatingButtons（缺 common-ui-hud.js 依赖会崩）
    window._cardExportPage = true;

  const initializeSoccerPage = async () => {
      if (!window.NekoMiniGame || typeof window.NekoMiniGame.connect !== 'function') {
        throw new Error('neko-minigame-sdk.js must load before soccer-demo.js');
      }
      if (!window.NekoMiniGameAvatarHost || typeof window.NekoMiniGameAvatarHost.create !== 'function') {
        throw new Error('neko-minigame-avatar-host.js must load before soccer-demo.js');
      }
      if (!window.NekoMiniGameAudioHost || typeof window.NekoMiniGameAudioHost.create !== 'function') {
        throw new Error('neko-minigame-audio-host.js must load before soccer-demo.js');
      }
      if (typeof window.createSoccerAvatarHost !== 'function') {
        throw new Error('soccer-avatar-host.js must load before soccer-demo.js');
      }
      if (typeof window.createSoccerNekoAdapter !== 'function') {
        throw new Error('soccer-neko-adapter.js must load before soccer-demo.js');
      }
      const soccerAvatarHostProxy = {
        mount(config) {
          if (!window.__SoccerAvatarHost || typeof window.__SoccerAvatarHost.mount !== 'function') {
            throw new Error('soccer avatar host is not ready');
          }
          return window.__SoccerAvatarHost.mount(config);
        },
        dispose() {
          window.__SoccerAvatarHost?.dispose?.();
        },
      };
      const soccerHost = await window.createSoccerNekoAdapter({
        gameType: 'soccer',
        source: 'soccer_demo',
        avatarHost: soccerAvatarHostProxy,
        audioHost: window.NekoMiniGameAudioHost.create({
          storageKeys: {
            bgm: 'neko.soccerGameAudio.bgmVolume',
            sfx: 'neko.soccerGameAudio.sfxVolume',
          },
        }),
      });
      const soccerGame = await window.NekoMiniGame.connect({
        id: 'soccer',
        version: '1.0.0',
        protocolVersion: '1',
        requiredCapabilities: ['runtime', 'logging', 'audio', 'speech-output', 'memory', 'context-read'],
        optionalCapabilities: ['dialogue', 'quick-lines', 'voice-input', 'avatar-renderer', 'storage'],
        contracts: {
          controls: {
            mood: ['calm', 'happy', 'angry', 'relaxed', 'sad', 'surprised'],
            difficulty: ['max', 'lv2', 'lv3', 'lv4'],
            reason: { type: 'string', maxLength: 120 },
          },
        },
      }, {
        // Temporary trusted same-origin transport. Public game code only uses
        // the SDK facade; a later iframe/Electron bridge can replace this
        // transport without changing the public capability calls below.
        transport: soccerHost,
      });
      await soccerHost.migrateLegacySettings(soccerGame);
      const _runtimeSessionId = () => soccerGame.runtime.session.id;
      const _runtimeCharacterName = () => soccerGame.runtime.session.characterName;
      window.__SoccerLoading = (() => {
        const state = { assets: false, route: false, choosing: false, routeStarting: false, started: false };
        const textEl = () => document.getElementById('loading-text');
        const overlayEl = () => document.getElementById('loading-overlay');
        const spinnerEl = () => document.getElementById('loading-spinner');
        const actionsEl = () => document.getElementById('loading-actions');
        const startButtonEl = () => document.getElementById('soccer-start-button');
        const topControlsEl = () => document.getElementById('game-top-controls');
        const exitButtonEl = () => document.getElementById('exit-to-start-button');
        const updateText = (text) => {
          const el = textEl();
          if (el && text) el.textContent = text;
        };
        const showOverlay = () => {
          const overlay = overlayEl();
          if (!overlay) return;
          overlay.style.display = 'flex';
          requestAnimationFrame(() => overlay.classList.remove('hidden'));
        };
        const hideOverlay = () => {
          const overlay = overlayEl();
          if (!overlay) return;
          overlay.classList.add('hidden');
          setTimeout(() => { overlay.style.display = 'none'; }, 420);
        };
        const setSpinnerVisible = (visible) => {
          const el = spinnerEl();
          if (el) el.hidden = !visible;
        };
        const setStartVisible = (visible, enabled = true) => {
          const actions = actionsEl();
          const button = startButtonEl();
          if (actions) actions.hidden = !visible;
          if (button) button.disabled = !enabled;
        };
        const setExitVisible = (visible, enabled = true) => {
          const controls = topControlsEl();
          const button = exitButtonEl();
          if (controls) controls.hidden = false;
          if (button) {
            button.hidden = !visible;
            button.disabled = !enabled;
          }
        };
        const _fillFallback = (fallback, params = {}) => Object.entries(params).reduce(
          (s, [k, v]) => s.replaceAll('{' + '{' + k + '}' + '}', String(v)),
          fallback,
        );
        const _localized = (key, fallback, params = {}) => {
          const fullKey = `soccer.${key}`;
          if (typeof window.t === 'function') {
            const translated = window.t(fullKey, params);
            if (translated && translated !== fullKey && translated !== key) return translated;
          }
          return _fillFallback(fallback, params);
        };
        const syncStartScreen = (fallbackText = '') => {
          if (state.assets && (state.route || state.choosing) && !state.routeStarting && !state.started) {
            updateText(fallbackText || _localized('startScreen.readyToStart', '准备完成，点击开始'));
            setSpinnerVisible(false);
            setStartVisible(true, true);
            setExitVisible(false);
            showOverlay();
            console.log('[SoccerStart] 前期准备完成，等待开始按钮');
            return;
          }
          setSpinnerVisible(!state.assets || state.routeStarting);
          setStartVisible(false);
          setExitVisible(false);
          showOverlay();
        };
        const tryHide = () => {
          if (!state.assets || !state.route || !state.started) return;
          updateText(_localized('startScreen.ready', '准备完成'));
          setSpinnerVisible(false);
          setStartVisible(false);
          setExitVisible(true, true);
          setTimeout(hideOverlay, 180);
        };
        return {
          set(part, text) {
            if (part && Object.prototype.hasOwnProperty.call(state, part)) {
              state[part] = false;
              if (part === 'route') state.routeStarting = true;
            }
            updateText(text);
            syncStartScreen(text);
          },
          isReady() {
            return state.assets && state.route && state.started;
          },
          canStart() {
            return state.assets && state.choosing && !state.routeStarting && !state.started;
          },
          beginStart(text = _localized('loading.beginStartDefault', '分析开局上下文…')) {
            state.choosing = false;
            state.route = false;
            state.routeStarting = true;
            state.started = false;
            updateText(text);
            setSpinnerVisible(true);
            setStartVisible(false);
            setExitVisible(false);
            showOverlay();
          },
          ending(text = _localized('exitGame.endingDefault', 'Ending this match…')) {
            state.route = false;
            state.routeStarting = true;
            state.started = false;
            updateText(text);
            setSpinnerVisible(true);
            setStartVisible(false);
            setExitVisible(false, false);
            showOverlay();
          },
          ended(text = _localized('exitGame.endedDefault', 'Game over. Please close this page. To play again, reopen it.')) {
            state.route = false;
            state.routeStarting = false;
            state.started = false;
            updateText(text);
            setSpinnerVisible(false);
            setStartVisible(false);
            setExitVisible(false, false);
            showOverlay();
          },
          showStart(text = _localized('startScreen.readyToStart', '准备完成，点击开始')) {
            state.route = false;
            state.choosing = true;
            state.routeStarting = false;
            state.started = false;
            syncStartScreen(text);
          },
          startGame() {
            if (!state.assets || !state.route || state.routeStarting) return false;
            state.started = true;
            console.log('[SoccerStart] 开始按钮已点击，进入游戏');
            tryHide();
            return true;
          },
          done(part, text) {
            if (part && Object.prototype.hasOwnProperty.call(state, part)) state[part] = true;
            if (part === 'route') state.routeStarting = false;
            updateText(text);
            if (state.assets && state.route && state.started) tryHide();
            else syncStartScreen(text);
          },
        };
      })();

  void (async () => {
        const THREE = await import('three');
        window.THREE = THREE;
        window.dispatchEvent(new CustomEvent('three-ready'));
        console.log('[soccer_demo] THREE ready');
  })();

    const normalizeSoccerExplicitLanguage = (value) => {
      const language = String(value || '').trim();
      return ['zh-CN', 'zh-TW', 'en', 'ja', 'ko', 'ru', 'es', 'pt'].includes(language)
        ? language
        : '';
    };
    let soccerCharacterInfoPromise = null;
    let soccerCharacterExplicitLanguage = '';
    let soccerCharacterLanguagePreferenceResolved = false;
    let soccerCharacterLanguageRevision = 0;
    const ensureSoccerCharacterInfo = () => {
      if (soccerCharacterInfoPromise) return soccerCharacterInfoPromise;
      soccerCharacterInfoPromise = (async () => {
        const languageRevision = soccerCharacterLanguageRevision;
        const configuredName = String(window.lanlan_config?.lanlan_name || '').trim();
        const requestedName = String(window.__SoccerResolvedLanlanName || configuredName).trim();
        const response = await soccerHost.getCharacter(requestedName);
        if (!response.ok) {
          soccerCharacterInfoPromise = null;
          return {};
        }
        const characterInfo = await response.json();
        const resolvedName = String(characterInfo?.lanlan_name || '').trim();
        if (soccerCharacterLanguageRevision === languageRevision
            && characterInfo?.language_preference_resolved === true) {
          soccerCharacterExplicitLanguage = normalizeSoccerExplicitLanguage(characterInfo?.language);
          soccerCharacterLanguagePreferenceResolved = true;
        }
        if (resolvedName) {
          window.__SoccerResolvedLanlanName = resolvedName;
          if (window.lanlan_config) window.lanlan_config.lanlan_name = resolvedName;
        }
        return characterInfo;
      })().catch((error) => {
        soccerCharacterInfoPromise = null;
        console.warn('[soccer_demo] 获取角色信息失败:', error);
        return {};
      });
      return soccerCharacterInfoPromise;
    };

    const SOCCER_AVATAR_LAYOUT = Object.freeze({
      viewport: Object.freeze({ mode: 'fixed', width: 200, height: 300 }),
      fit: Object.freeze({
        mode: 'contain',
        align: 'bottom-center',
        padding: 6,
        scaleMultiplier: 1,
      }),
      resize: Object.freeze({ mode: 'fixed' }),
    });

    function soccerAvatarMountConfig(slot, model) {
      return {
        slot,
        model,
        viewport: SOCCER_AVATAR_LAYOUT.viewport,
        fit: SOCCER_AVATAR_LAYOUT.fit,
        resize: SOCCER_AVATAR_LAYOUT.resize,
      };
    }

    (async () => {
      const statusEl = document.getElementById('status');
      const soccerLoadingText = (key, fallback) => {
        const fullKey = `soccer.${key}`;
        if (typeof window.t === 'function') {
          const translated = window.t(fullKey);
          if (translated && translated !== fullKey && translated !== key) return translated;
        }
        return fallback;
      };
      const setStatus = (s) => {
        statusEl.textContent = s;
        window.__SoccerLoading?.set('assets', s);
      };
      window.__SoccerAiAvatar = window.__SoccerAiAvatar || { type: 'none', path: '', ready: false };
      const markAiAvatar = (type, path, ready = true) => {
        window.__SoccerAiAvatar = { type, path: path || '', ready: !!ready };
      };
      let avatarEventEmitter = null;

      async function fetchSoccerCharacterInfo() {
        try {
          return await ensureSoccerCharacterInfo();
        } catch (e) {
          console.warn('[soccer_demo] 获取角色信息失败:', e);
          return {};
        }
      }

      function prefersAiVrm(charData) {
        const modelType = String(charData?.model_type || '').toLowerCase();
        const subType = String(charData?.live3d_sub_type || '').toLowerCase();
        return !!charData?.vrm_path && (
          modelType === 'vrm' || (modelType === 'live3d' && subType === 'vrm')
        );
      }

      window.__SoccerAvatarHost = window.createSoccerAvatarHost({
        onAvatarChanged(slot, model) {
          if (slot === 'ai') markAiAvatar(model.type, model.path, true);
          avatarEventEmitter?.(`${slot}-avatar-changed`, model);
        },
      });

      // 等 vrm 模块链加载完
      await new Promise(resolve => {
        if (window.vrmModuleLoaded) return resolve();
        window.addEventListener('vrm-modules-ready', resolve, { once: true });
        window.addEventListener('vrm-modules-failed', e => {
          console.error('[soccer_demo] VRM modules failed:', e.detail);
          setStatus('VRM modules failed: ' + (e.detail?.failedModules || []).join(', '));
          resolve();
        }, { once: true });
      });

      try {
        console.log('[soccer_demo] modules ready, starting VRM init');
        setStatus('initializing VRM renderer…');
        soccerGame.capabilities.require('avatar-renderer');
        setStatus('loading sensei.vrm…');
        window.__SoccerPlayerAvatarController = await soccerGame.avatar.mount(
          soccerAvatarMountConfig('player', {
            type: 'vrm',
            path: '/static/vrm/sensei.vrm',
          }),
        );
        setStatus('VRM ready');

        // --- 接当前猫娘头像作为 AI：当前支持 Live2D / VRM；MMD 继续回退到 Live2D ---
        try {
          const charData = await fetchSoccerCharacterInfo();
          let loadedAiAvatar = false;

          if (prefersAiVrm(charData)) {
            try {
              const aiVrmPath = charData.vrm_path;
              setStatus('loading AI VRM…');
              window.__SoccerAiAvatarController = await soccerGame.avatar.mount(
                soccerAvatarMountConfig('ai', { type: 'vrm', path: aiVrmPath }),
              );
              loadedAiAvatar = true;
              console.log('[soccer_demo] 使用当前角色 VRM 作为 AI:', charData.lanlan_name, aiVrmPath);
            } catch (vrmErr) {
              markAiAvatar('vrm', charData.vrm_path || '', false);
              console.warn('[soccer_demo] AI VRM 加载失败，回退 Live2D:', vrmErr);
            }
          }

          if (!loadedAiAvatar) {
            setStatus('loading AI Live2D…');
            // 后端返回与主页面相同的规范 Live2D 路径；接口异常时才使用同一默认回退。
            const aiL2dPath = charData.live2d_path || '/static/yui-lolita/yui-lolita.model3.json';
            if (charData.live2d_path) {
              console.log('[soccer_demo] 使用当前角色 L2D:', charData.lanlan_name, aiL2dPath);
            }
            window.__SoccerAiAvatarController = await soccerGame.avatar.mount(
              soccerAvatarMountConfig('ai', { type: 'live2d', path: aiL2dPath }),
            );
          }

          setStatus('all ready');
        } catch (avatarErr) {
          markAiAvatar('none', '', false);
          console.error('[soccer_demo] AI avatar failed:', avatarErr);
          setStatus('AI avatar failed: ' + (avatarErr.message || avatarErr));
        }

        window.__SoccerLoading?.done(
          'assets',
          soccerLoadingText('loading.assetsDone', '模型加载完成'),
        );
        setTimeout(() => { statusEl.style.opacity = '0'; statusEl.style.transition = 'opacity 0.8s'; }, 1500);
      } catch (e) {
        console.error('[soccer_demo] VRM init failed:', e);
        setStatus('VRM init failed: ' + (e.message || e));
        window.__SoccerLoading?.done(
          'assets',
          soccerLoadingText('loading.assetsFailed', '模型初始化失败，继续进入游戏'),
        );
      }
    })();

    /* ═══════════════════════════════════════════════════════════════════════════
     *  SoccerDemo 对外 API（挂在 window.SoccerDemo）
     *  给 neko 本体 / 外部脚本（LLM 生成、美术素材、UI 控件）使用的稳定入口。
     * ═══════════════════════════════════════════════════════════════════════════
     *
     *  ── 心情（只作用于 AI；影响速度/冷却/冲量/散射/特殊行为，并同步头像表情）
     *     SoccerDemo.MOODS                  // ['calm','happy','angry','relaxed','sad','surprised']
     *     SoccerDemo.setMood(name)          // 切换心情；会触发 'mood-<name>' 气泡 + onEvent('mood-changed')
     *     SoccerDemo.getMood()              // 当前心情字符串
     *     SoccerDemo.enableMoodRotation(s)  // 启用随机轮换；仅用于 debug 或 LLM 降级后的纯游戏兜底
     *     SoccerDemo.disableMoodRotation()  // 关闭自动轮换
     *
     *  ── 难度（4 档：max → lv2 → lv3 → lv4；当前默认由 LLM 控制）
     *     SoccerDemo.DIFFICULTIES           // ['max','lv2','lv3','lv4']
     *     SoccerDemo.setDifficulty(name)    // 直接设；触发 'diff-<name>' 气泡
     *     SoccerDemo.getDifficulty()
     *     SoccerDemo.cycleDifficulty()      // 手动推进一档
     *
     *     档位差异：
     *       max  完整强度
     *       lv2  踢球 CD ×1.5、前摇 0.35s（移动速度不变）
     *       lv3  lv2 基础上速度 ×0.78
     *       lv4  lv3 基础上 attack 模式强制降为 defend（不主动进攻）
     *
     *  ── 头像（运行时替换，无需刷新）
     *     await SoccerDemo.setPlayerAvatar({ type: 'vrm',    path: '/static/vrm/xxx.vrm' })
     *     await SoccerDemo.setAiAvatar   ({ type: 'live2d', path: '/static/xxx/xxx.model3.json' })
     *     await SoccerDemo.setAiAvatar   ({ type: 'vrm',    path: '/user_vrm/xxx.vrm' })
     *     SoccerDemo.getPlayerAvatar()      // { type, path }
     *     SoccerDemo.getAiAvatar()
     *
     *     注意：目前 player 只支持 vrm、AI 支持 live2d / vrm；MMD 暂不接入。
     *
     *  ── 说话（气泡 + LLM 钩子）
     *     SoccerDemo.say(text, opts?)       // 直接说一句任意文本，返回 bool（是否成功播出）
     *       opts = {
     *         kind?:        string,           // 分类标签，供 LLM / 分析使用
     *         priority?:    number,           // 0-9，越大越优先；低优先级在气泡显示期间被阻塞
     *         duration?:    number,           // ms，省略时按文本长度估算
     *         cooldownKey?: string,           // 节流键；同 key 冷却内不会重复说
     *         cooldownSec?: number,
     *       }
     *     SoccerDemo.triggerScene(kind)     // 按场景 kind 从内建文案池随机抽一条说（见下方场景列表）
     *     SoccerDemo.clearBubble()          // 立刻清当前气泡
     *
     *     文本在输出前会套心情装饰（sad→前缀"……"、surprised→前缀"诶？！"、happy→尾部" ♪"…）
     *
     *  ── 订阅事件流（LLM / 美术 / UI 接入点）
     *     const unsub = SoccerDemo.onSpeak(payload => { ... })
     *     // payload = {
     *     //   text,           // 心情装饰后
     *     //   textRaw,        // 原文
     *     //   mood, kind, priority, durationMs, ts
     *     // }
     *     unsub()                            // 取消订阅
     *     SoccerDemo.offSpeak(fn)
     *
     *     const unsubEv = SoccerDemo.onEvent(({label, meta, time}) => { ... })
     *     // label 取值：
     *     //   'goal-scored'          meta = { side: 'ai', lastTouch }
     *     //   'goal-conceded'        meta = { side: 'player', lastTouch }
     *     //   'own-goal-by-ai'       meta = { side: 'player', lastTouch: 'ai' }
     *     //   'own-goal-by-player'   meta = { side: 'ai', lastTouch: 'player' }
     *     //   'player-kick'          玩家踢球（无 meta）
     *     //   'ai-kick'              AI 踢球
     *     //   'unstick'              球被脱困
     *     //   'mood-changed'         meta = { mood }
     *     //   'difficulty-changed'   meta = { difficulty }
     *     //   'player-avatar-changed'/'ai-avatar-changed'  meta = { type, path }
     *
     *  ── 美术素材接入（替换默认 DOM 气泡）
     *     SoccerDemo.setBubbleRenderer(fn)
     *     // fn(payload) 会在每次 say 时被调用，payload 同 onSpeak。
     *     // 传 null 或非函数会 fallback 回内置默认气泡（#ai-speech-bubble DOM）
     *
     *  ── LLM 接入范式
     *     SoccerDemo.onSpeak(async p => {
     *       if (p.kind === 'goal-scored' || p.kind === 'mood-happy') {
     *         const line = await myLLM(p.kind, p.mood, p.textRaw);
     *         SoccerDemo.say(line, { priority: p.priority + 1, kind: p.kind + '-llm' });
     *       }
     *     });
     *
     *  ── 内建场景 kind（SoccerDemo.triggerScene 会用这些作为 LINES 池的 key）
     *     进球:             'goal-scored' / 'goal-conceded' / 'own-goal-by-ai' / 'own-goal-by-player'
     *     射门/抢断:        'shot-miss' / 'steal' / 'stolen'
     *     场面控制:         'long-attack-possession' / 'long-defense-possession'（按 allowAttack 分支）
     *     观察玩家:         'player-idle' / 'player-charging-long' / 'close-proximity'
     *     空场氛围:         'free-ball' / 'score-boring' / 'no-goal-1min'
     *     物理彩蛋:         'fast-ball' / 'startle-direct' / 'startle-graze' / 'zoneout' / 'unstick'
     *     档位切换:         'diff-max' / 'diff-lv2' / 'diff-lv3' / 'diff-lv4'
     *     心情切换:         'mood-calm' / 'mood-happy' / 'mood-angry' / 'mood-relaxed' / 'mood-sad' / 'mood-surprised'
     *
     *  ── Debug
     *     SoccerDemo._snapshot()            // { mood, difficulty, score, aiMode, aiFreezeSec, startle, ballGhost }
     *
     *  ── 键盘（debug 快捷键）
     *     1-6   仅 ?test=true：切换心情（同 setMood）
     *     U/I/O/P 仅 ?test=true：切换难度 max/lv2/lv3/lv4
     *     [     仅 ?test=true：切换单人模式
     *     R     复位球权和双方位置
     *
     *  ═══════════════════════════════════════════════════════════════════════════
     */
    await (async () => {
      const _formatI18nFallback = (fallback, params = {}) => Object.entries(params || {}).reduce(
        (s, [k, v]) => s.replaceAll('{' + '{' + k + '}' + '}', String(v)),
        fallback,
      );
      const _soccerI18nParam = (name) => '{' + '{' + name + '}' + '}';
      const _i18n = (key, fallback, params) => {
        const fullKey = `soccer.${key}`;
        if (typeof window.t === 'function') {
          const translated = window.t(fullKey, params || {});
          if (translated && translated !== fullKey && translated !== key) return translated;
        }
        return _formatI18nFallback(fallback, params);
      };
      const SOCCER_DOM_I18N_FALLBACKS = {
        'soccer.hint.controls': '鼠标 = 引导玩家 · 按住左键 = 蓄力，松开 = 射门（离球 ' + _soccerI18nParam('distance') + ' 内触发）· R 复位',
        'soccer.hint.aiMood': 'NEKO 的心情和难度会随局势变化',
        'soccer.debugControls.bounds': '启用边界（出界规则）',
        'soccer.debugControls.voiceOutput': '播放 LLM 台词（项目语音）',
        'soccer.debugControls.voiceButton': '调试语音',
        'soccer.debugControls.send': '发送',
        'soccer.debugControls.voiceStatusHidden': '调试 STT：隐藏',
        'soccer.debugControls.voiceVolumeHint': '点击可静音或恢复。无法调整正在播放的语音音量。',
        'soccer.settings.button': '设置',
        'soccer.settings.title': '游戏设置',
        'soccer.settings.game': '游戏',
        'soccer.settings.audio': '音量',
        'soccer.settings.debug': '调试',
        'soccer.voiceChat.label': '语音对话',
        'soccer.voiceChat.connecting': '正在连接主语音入口…',
        'soccer.surrenderReminder.label': '认输提醒',
        'soccer.exitPrompt.continuePlay': '继续玩',
        'soccer.exitPrompt.endGame': '结束游戏',
        'soccer.exitPrompt.stayLonger': '再陪一会',
        'soccer.exitPrompt.rest': '休息',
        'soccer.exitPrompt.neverAgain': '不再提示',
        'soccer.exitPrompt.neverAgainNote': '以后不再自动弹出猫娘认输或休息提示。可在右上角重新开启“认输提醒”。如果猫娘情绪低落，可以用语音或文字哄哄她。',
        'soccer.exitPrompt.surrenderFallback': '喵..认输喵...',
        'soccer.exitPrompt.restFallback': '喵...有点累了，想先安静一会。',
        'soccer.exitGame.button': '退出游戏',
        'soccer.loading.initializingMiniGame': '初始化足球小游戏…',
        'soccer.startTutorial.title': '玩法说明',
        'soccer.startTutorial.move': '移动鼠标引导玩家角色。',
        'soccer.startTutorial.charge': '靠近球时按住左键蓄力，松开射门。',
        'soccer.startTutorial.range': '离球 ' + _soccerI18nParam('distance') + ' 内才会触发踢球。',
        'soccer.startTutorial.reset': '按 R 可以复位球和双方位置。',
        'soccer.startTutorial.ai': 'NEKO 的心情和难度会随局势变化。',
        'soccer.startScreen.startButton': '开始',
        'soccer.memoryOption.label': '本局对话进入记忆（默认不开启）',
        'soccer.memoryOption.hint': '关闭后，玩家输入、NEKO直接回应、事件回应、赛后摘要和后续续接都不会进入或引用记忆。',
      };
      const SOCCER_PLACEHOLDER_FALLBACKS = {
        'soccer.debugControls.voicePlaceholder': '调试：输入最终转写后回车',
      };
      function applySoccerI18nFallbacks() {
        document.querySelectorAll('[data-i18n^="soccer."]').forEach((el) => {
          const key = el.getAttribute('data-i18n');
          const fallback = SOCCER_DOM_I18N_FALLBACKS[key];
          if (!fallback) return;
          const current = String(el.textContent || '').trim();
          if (!current || current === key) {
            let params = {};
            try { params = JSON.parse(el.getAttribute('data-i18n-params') || '{}'); }
            catch (_) { params = {}; }
            el.textContent = _formatI18nFallback(fallback, params);
          }
        });
        document.querySelectorAll('[data-i18n-placeholder^="soccer."]').forEach((el) => {
          const key = el.getAttribute('data-i18n-placeholder');
          const fallback = SOCCER_PLACEHOLDER_FALLBACKS[key];
          if (!fallback) return;
          const current = String(el.getAttribute('placeholder') || '').trim();
          if (!current || current === key) el.setAttribute('placeholder', fallback);
        });
        document.querySelectorAll('[data-i18n-title^="soccer."]').forEach((el) => {
          const key = el.getAttribute('data-i18n-title');
          const fallback = SOCCER_DOM_I18N_FALLBACKS[key];
          if (!fallback) return;
          const current = String(el.getAttribute('title') || '').trim();
          if (!current || current === key) el.setAttribute('title', fallback);
        });
        document.querySelectorAll('[data-i18n-aria^="soccer."]').forEach((el) => {
          const key = el.getAttribute('data-i18n-aria');
          const fallback = SOCCER_DOM_I18N_FALLBACKS[key];
          if (!fallback) return;
          const current = String(el.getAttribute('aria-label') || '').trim();
          if (!current || current === key) el.setAttribute('aria-label', fallback);
        });
      }
      window.addEventListener('localechange', () => setTimeout(applySoccerI18nFallbacks, 0));
      setTimeout(applySoccerI18nFallbacks, 0);
      const _resolveSpeechLang = () => {
        let raw = (typeof window.i18next !== 'undefined' && window.i18next.language) || '';
        if (!raw && typeof localStorage !== 'undefined') {
          try { raw = localStorage.getItem('i18nextLng') || ''; }
          catch (_) { raw = ''; }
        }
        raw = raw
          || (typeof navigator !== 'undefined' && navigator.language)
          || 'zh-CN';
        const tag = String(raw).toLowerCase();
        if (tag.startsWith('zh-tw') || tag === 'zh-hant' || tag.startsWith('zh-hk')) return 'zh-TW';
        if (tag.startsWith('zh')) return 'zh-CN';
        if (tag.startsWith('en')) return 'en-US';
        if (tag.startsWith('ja')) return 'ja-JP';
        if (tag.startsWith('ko')) return 'ko-KR';
        if (tag.startsWith('ru')) return 'ru-RU';
        if (tag.startsWith('es')) return 'es-ES';
        if (tag.startsWith('pt')) return 'pt-BR';
        return raw;
      };
      // In-game internal templates follow the conversation preference. UI copy
      // can switch independently without rewriting the character's language.
      window.SoccerCurrentI18nLang = function () {
        try {
          if (typeof window.i18next !== 'undefined'
              && typeof window.i18next.language === 'string'
              && window.i18next.language) {
            return window.i18next.language;
          }
          if (typeof localStorage !== 'undefined') {
            const cached = localStorage.getItem('i18nextLng');
            if (cached) return cached;
          }
          if (typeof navigator !== 'undefined' && navigator.language) {
            return navigator.language;
          }
        } catch (_) { /* swallow: fetch payload tolerates empty */ }
        return '';
      };
      window.SoccerExplicitConversationLang = function (characterName) {
        if (characterName !== _soccerConversationCharacterName()) return '';
        if (soccerCharacterLanguagePreferenceResolved) {
          return soccerCharacterExplicitLanguage;
        }
        try {
          if (typeof window.getExplicitConversationLanguagePreference === 'function') {
            const liveLanguage = normalizeSoccerExplicitLanguage(
              window.getExplicitConversationLanguagePreference(characterName)
            );
            if (liveLanguage) {
              soccerCharacterExplicitLanguage = liveLanguage;
              return liveLanguage;
            }
          }
        } catch (_) { /* omit unavailable explicit preference */ }
        return soccerCharacterExplicitLanguage;
      };
      const _currentI18nLang = window.SoccerCurrentI18nLang;
      const _explicitConversationLang = window.SoccerExplicitConversationLang;
      const _soccerConversationCharacterName = () => {
        const configuredName = String(window.lanlan_config?.lanlan_name || '').trim();
        return String(
          window.__SoccerResolvedLanlanName
          || (configuredName !== 'soccer_demo' ? configuredName : '')
          || ''
        ).trim();
      };
      const _updateSoccerCharacterExplicitLanguage = (event, cleared) => {
        const detail = event?.detail || {};
        const eventCharacterName = String(detail.character_name || '').trim();
        const currentCharacterName = _soccerConversationCharacterName();
        if (!currentCharacterName) {
          if (eventCharacterName) soccerCharacterLanguageRevision += 1;
          return;
        }
        if (eventCharacterName !== currentCharacterName) return;
        soccerCharacterLanguageRevision += 1;
        soccerCharacterExplicitLanguage = cleared
          ? ''
          : normalizeSoccerExplicitLanguage(detail.language);
        soccerCharacterLanguagePreferenceResolved = true;
      };
      window.addEventListener('neko:conversation-language-changed', (event) => {
        _updateSoccerCharacterExplicitLanguage(event, false);
      });
      window.addEventListener('neko:conversation-language-cleared', (event) => {
        _updateSoccerCharacterExplicitLanguage(event, true);
      });
      window.addEventListener('storage', (event) => {
        const characterName = _soccerConversationCharacterName();
        const storageKey = String(event?.key || '');
        if (!storageKey.startsWith('nekoConversationLanguage:')) return;
        if (!characterName) {
          soccerCharacterLanguageRevision += 1;
          return;
        }
        if (storageKey !== `nekoConversationLanguage:${encodeURIComponent(characterName)}`) return;
        soccerCharacterLanguageRevision += 1;
        soccerCharacterExplicitLanguage = normalizeSoccerExplicitLanguage(event.newValue);
        soccerCharacterLanguagePreferenceResolved = true;
      });
      const _conversationLanguagePayload = () => {
        const payload = {};
        const explicitLanguage = _explicitConversationLang(_soccerConversationCharacterName());
        const renderLanguage = _currentI18nLang();
        if (explicitLanguage) payload.i18n_language = explicitLanguage;
        if (renderLanguage) payload.render_language = renderLanguage;
        return payload;
      };
      const canvas = document.getElementById('game');
      const ctx = canvas.getContext('2d');
      const debugCanvas = document.getElementById('debug');
      const dctx = debugCanvas.getContext('2d');
      const playerEl = document.getElementById('player-vrm-container');
      const aiEl = document.getElementById('ai-l2d-container');

      const CFG = {
        charSize: 80,
        vrmW: 200, vrmH: 300,
        ballRadius: 15,
        goalWidth: 22,
        goalHeight: 200,
        playerMaxSpeed: 520,
        playerAccel: 3200,
        aiMaxSpeed: 520,   // 跟玩家一样
        aiAccel: 3200,
        charDamping: 9,
        ballFriction: 0.55,
        kickImpulse: 950,
        kickRange: 90,
        wallRestitution: 0.7,
        charBallRestitution: 1.25,
      };

      const OPENING_MOVEMENT = {
        liveBallSpeedMin: 120,
        routeBlend: 0.55,
        maxVerticalShiftRatio: 0.32,
        maxProjectionRatio: 1.5,
      };

      // 开局时根据玩家相对球的站位估计出脚方向；球已经被踢出后则改用实际速度。
      // 只采样 AI 当前横向位置附近的上下墙反弹路线，用来给原进攻目标增加
      // 有限的纵向偏移，而不是把 AI 直接变成精确追踪落点的守门员。
      function estimateOpeningAttackRouteY(ball, player, ai, fieldHeight, cfg, openingConfig) {
        const playerCx = player.x + cfg.charSize / 2;
        const playerCy = player.y + cfg.charSize / 2;
        const aiCx = ai.x + cfg.charSize / 2;
        const ballSpeed = Math.hypot(ball.vx, ball.vy);
        const hasLiveBallPath = ballSpeed >= openingConfig.liveBallSpeedMin;
        if (hasLiveBallPath && ball.vx <= 0) return null;
        const useLiveBallPath = hasLiveBallPath;
        const dirX = useLiveBallPath ? ball.vx : ball.x - playerCx;
        const dirY = useLiveBallPath ? ball.vy : ball.y - playerCy;
        const travelX = aiCx - ball.x;
        if (dirX <= cfg.ballRadius || travelX <= 0) return null;

        const maxProjection = fieldHeight * openingConfig.maxProjectionRatio;
        const projectedDeltaY = Math.max(
          -maxProjection,
          Math.min(maxProjection, travelX * dirY / dirX),
        );
        const top = cfg.ballRadius;
        const bottom = fieldHeight - cfg.ballRadius;
        let routeY = ball.y + projectedDeltaY;

        for (
          let bounce = 0;
          bounce < 4 && (routeY < top || routeY > bottom);
          bounce += 1
        ) {
          if (routeY < top) {
            routeY = top + (top - routeY) * cfg.wallRestitution;
          } else {
            routeY = bottom - (routeY - bottom) * cfg.wallRestitution;
          }
        }

        return Math.max(top, Math.min(bottom, routeY));
      }

      // ── 边界系统 ──────────────────────────────────────────────────────
      // 边界 = 球场内缩一定距离的矩形区域，球超出边界视为出界
      const BOUNDARY = {
        enabled: false,
        margin: 40,          // 边界距画布边缘的距离（像素）
        outOfBoundsDelay: 0.5, // 出界后等待时间（秒），让玩家看到球飞出去
      };
      let _outOfBoundsTimer = 0;
      let _outOfBoundsSide = null; // 'player' | 'ai' | null — 最后触球的一方
      let _lastWallBounceSfxAt = 0;
      const WALL_BOUNCE_SFX_COOLDOWN_MS = 500;
      let _startedAsMaxAngry = false;
      let _openingMaxAngryBgmActive = false;

      const boundaryToggle = document.getElementById('boundary-toggle');
      boundaryToggle.addEventListener('change', () => {
        BOUNDARY.enabled = boundaryToggle.checked;
      });
      const voiceOutputToggle = document.getElementById('voice-output-toggle');
      const gameMemoryToggle = document.getElementById('game-memory-toggle');
      const gameVoiceChatControl = document.getElementById('game-voice-chat-control');
      const gameVoiceChatButton = document.getElementById('game-voice-chat-button');
      const gameVoiceChatIcon = document.getElementById('game-voice-chat-icon');
      const gameVoiceChatStatus = document.getElementById('game-voice-chat-status');
      const voiceControls = document.getElementById('voice-controls');
      const voiceMicButton = document.getElementById('voice-mic-button');
      const voiceTextInput = document.getElementById('voice-text-input');
      const voiceSendButton = document.getElementById('voice-send-button');
      const voiceStatusEl = document.getElementById('voice-status');
      const moodDebugPanel = document.getElementById('mood-debug-panel');
      const moodDebugReadout = document.getElementById('mood-debug-readout');
      const startButton = document.getElementById('soccer-start-button');
      const gameTopControls = document.getElementById('game-top-controls');
      const topVoiceControlSlot = document.getElementById('top-voice-control-slot');
      const settingsButton = document.getElementById('soccer-settings-button');
      const settingsPanel = document.getElementById('controls');
      const settingsVoiceControlSlot = document.getElementById('settings-voice-control-slot');
      const settingsDebugGroup = document.getElementById('settings-debug-group');
      const surrenderReminderToggle = document.getElementById('surrender-reminder-toggle');
      const exitToStartButton = document.getElementById('exit-to-start-button');
      const exitPromptOverlay = document.getElementById('exit-prompt-overlay');
      const exitPromptLine = document.getElementById('exit-prompt-line');
      const exitPromptContinueButton = document.getElementById('exit-prompt-continue');
      const exitPromptEndButton = document.getElementById('exit-prompt-end');
      const exitPromptNeverRow = document.getElementById('exit-prompt-never-row');
      const exitPromptNeverNote = document.getElementById('exit-prompt-never-note');
      const exitPromptNeverAgain = document.getElementById('exit-prompt-never-again');
      const bgmVolumeInput = document.getElementById('game-bgm-volume');
      const bgmVolumeValue = document.getElementById('game-bgm-volume-value');
      const bgmMuteButton = document.getElementById('game-bgm-mute');
      const sfxVolumeInput = document.getElementById('game-sfx-volume');
      const sfxVolumeValue = document.getElementById('game-sfx-volume-value');
      const sfxMuteButton = document.getElementById('game-sfx-mute');
      const voiceVolumeInput = document.getElementById('game-voice-volume');
      const voiceVolumeValue = document.getElementById('game-voice-volume-value');
      const voiceMuteButton = document.getElementById('game-voice-mute');
      const SOCCER_VOICE_MIX_STORAGE_KEY = 'settings/voice-mix-percent';
      const DEFAULT_SOCCER_VOICE_MIX_PERCENT = 50;
      const settingsUiAbortController = new AbortController();

      function syncGameVoiceControlPlacement(settingsOpen) {
        if (!gameVoiceChatControl || !topVoiceControlSlot || !settingsVoiceControlSlot) return;
        const targetSlot = settingsOpen ? settingsVoiceControlSlot : topVoiceControlSlot;
        if (gameVoiceChatControl.parentElement !== targetSlot) targetSlot.appendChild(gameVoiceChatControl);
        topVoiceControlSlot.hidden = settingsOpen;
      }

      function setSettingsPanelOpen(open, { restoreFocus = false } = {}) {
        if (!settingsButton || !settingsPanel) return false;
        const next = open === true;
        settingsPanel.hidden = !next;
        settingsButton.setAttribute('aria-expanded', next ? 'true' : 'false');
        syncGameVoiceControlPlacement(next);
        if (next) deactivatePlayerPointerControl();
        if (!next && restoreFocus) settingsButton.focus({ preventScroll: true });
        return next;
      }

      syncGameVoiceControlPlacement(settingsButton?.getAttribute('aria-expanded') === 'true');

      settingsButton?.addEventListener('click', () => {
        setSettingsPanelOpen(settingsButton.getAttribute('aria-expanded') !== 'true');
      }, { signal: settingsUiAbortController.signal });
      document.addEventListener('pointerdown', (e) => {
        if (settingsPanel?.hidden) return;
        if (settingsPanel.contains(e.target) || settingsButton?.contains(e.target)) return;
        setSettingsPanelOpen(false);
      }, { signal: settingsUiAbortController.signal });
      window.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') setSettingsPanelOpen(false, { restoreFocus: true });
      }, { signal: settingsUiAbortController.signal });

      let gameVoiceChatCommandPending = false;
      let gameVoiceChatState = {
        available: false,
        active: false,
        starting: false,
        muted: false,
        capture_owner: 'host',
        transcription_mode: 'unavailable',
        provider: '',
        ready: false,
        transcription_reason: 'voice_inactive',
        reason: 'connecting',
      };

      function _gameVoiceChatStatusText(state = gameVoiceChatState) {
        if (gameVoiceChatCommandPending) {
          return state.active
            ? _i18n('voiceChat.stopping', '正在关闭语音对话…')
            : _i18n('voiceChat.starting', '正在开启语音对话…');
        }
        if (!state.available) {
          if (state.reason === 'connecting') {
            return _i18n('voiceChat.connecting', '正在连接主语音入口…');
          }
          if (state.reason === 'command_failed' || state.reason === 'start_failed' || state.reason === 'stop_failed') {
            return _i18n('voiceChat.failed', '语音对话控制失败，请在主页面重试');
          }
          return _i18n('voiceChat.unavailable', '主语音入口暂不可用');
        }
        if (state.starting) return _i18n('voiceChat.starting', '正在开启语音对话…');
        if (state.active && state.transcription_mode === 'unavailable') {
          return _i18n('voiceChat.failed', '语音对话控制失败，请在主页面重试');
        }
        if (state.active && state.ready !== true) {
          return _i18n('voiceChat.connecting', '正在连接主语音入口…');
        }
        if (state.active && state.muted) return _i18n('voiceChat.muted', '语音对话已开启 · 麦克风静音');
        if (state.active) return _i18n('voiceChat.active', '语音对话已开启');
        return _i18n('voiceChat.idle', '点击开启语音对话');
      }

      function _renderGameVoiceChatControl(nextState = {}) {
        gameVoiceChatState = { ...gameVoiceChatState, ...nextState };
        const active = gameVoiceChatState.active === true;
        const busy = gameVoiceChatCommandPending || gameVoiceChatState.starting === true || gameVoiceChatState.busy === true;
        const available = gameVoiceChatState.available === true;
        const statusText = _gameVoiceChatStatusText(gameVoiceChatState);
        if (gameVoiceChatButton) {
          gameVoiceChatButton.disabled = !available || busy;
          gameVoiceChatButton.setAttribute('aria-pressed', active ? 'true' : 'false');
          const routeUnavailable = active && gameVoiceChatState.transcription_mode === 'unavailable';
          gameVoiceChatButton.dataset.error = (
            routeUnavailable
            || (!available && /failed$/.test(String(gameVoiceChatState.reason || '')))
          ) ? 'true' : 'false';
          const actionLabel = active
            ? _i18n('voiceChat.stop', '关闭语音对话')
            : _i18n('voiceChat.start', '开启语音对话');
          gameVoiceChatButton.setAttribute('aria-label', actionLabel);
          gameVoiceChatButton.title = actionLabel;
        }
        if (gameVoiceChatIcon && gameVoiceChatButton) {
          gameVoiceChatIcon.src = active
            ? gameVoiceChatButton.dataset.iconOn
            : gameVoiceChatButton.dataset.iconOff;
        }
        if (gameVoiceChatStatus) gameVoiceChatStatus.textContent = statusText;
      }

      function _initGameVoiceChatControl() {
        if (!soccerGame.capabilities.has('voice-input')) {
          _renderGameVoiceChatControl({ available: false, reason: 'capability_unavailable' });
          return;
        }
        soccerGame.voice.onState((state) => {
          _renderGameVoiceChatControl(state);
        });
        soccerGame.voice.onTranscript((transcript) => {
          showPlayerTranscriptBubble(transcript);
        });
        soccerGame.voice.onError(({ error, source }) => {
          console.warn(`[SoccerVoiceControl] bridge error | source=${source}:`, error);
          _renderGameVoiceChatControl({ available: false, reason: 'command_failed' });
        });
        _renderGameVoiceChatControl();
      }

      const _refreshGameVoiceChatLocale = () => setTimeout(() => _renderGameVoiceChatControl(), 0);
      window.addEventListener('localechange', _refreshGameVoiceChatLocale);

      gameVoiceChatButton?.addEventListener('click', async () => {
        if (gameVoiceChatButton.disabled || gameVoiceChatCommandPending) return;
        gameVoiceChatCommandPending = true;
        _renderGameVoiceChatControl();
        try {
          const state = await soccerGame.voice.toggle();
          _renderGameVoiceChatControl(state);
          soccerSessionDebugLog('info', 'voice', 'game_voice_control_toggle', '小游戏语音对话开关完成', {
            active: state.active === true,
            muted: state.muted === true,
            reason: state.reason || '',
            capture_owner: state.capture_owner || '',
            transcription_mode: state.transcription_mode || '',
            provider: state.provider || '',
            ready: state.ready === true,
            transcription_reason: state.transcription_reason || '',
          });
        } catch (error) {
          console.warn('[SoccerVoiceControl] toggle failed:', error);
          _renderGameVoiceChatControl({ available: false, reason: 'command_failed' });
        } finally {
          gameVoiceChatCommandPending = false;
          _renderGameVoiceChatControl();
        }
      });
      _initGameVoiceChatControl();

      function _normalizeSoccerVoiceMixPercent(value) {
        const numeric = Number(value);
        if (!Number.isFinite(numeric)) return DEFAULT_SOCCER_VOICE_MIX_PERCENT;
        return Math.round(Math.max(0, Math.min(100, numeric)));
      }

      async function _readSoccerVoiceMixPercent() {
        if (!soccerGame.capabilities.has('storage')) return DEFAULT_SOCCER_VOICE_MIX_PERCENT;
        try {
          const response = await soccerGame.storage.get(SOCCER_VOICE_MIX_STORAGE_KEY);
          const stored = response.data || {};
          return !response.ok || stored.found !== true
            ? DEFAULT_SOCCER_VOICE_MIX_PERCENT
            : _normalizeSoccerVoiceMixPercent(stored.value);
        } catch (_) {
          return DEFAULT_SOCCER_VOICE_MIX_PERCENT;
        }
      }

      let soccerVoiceMixPercent = await _readSoccerVoiceMixPercent();
      let lastNonZeroBgmVolume = 0.45;
      let lastNonZeroSfxVolume = 0.75;
      let lastNonZeroVoiceMixPercent = soccerVoiceMixPercent > 0
        ? soccerVoiceMixPercent
        : DEFAULT_SOCCER_VOICE_MIX_PERCENT;

      function _setSoccerVoiceMixPercent(value, { persist = true } = {}) {
        soccerVoiceMixPercent = _normalizeSoccerVoiceMixPercent(value);
        if (soccerVoiceMixPercent > 0) lastNonZeroVoiceMixPercent = soccerVoiceMixPercent;
        if (persist && soccerGame.capabilities.has('storage')) {
          void soccerGame.storage.set(SOCCER_VOICE_MIX_STORAGE_KEY, soccerVoiceMixPercent).catch((error) => {
            soccerRecoverableLog('[SoccerSettings] 语音混音设置保存失败:', error);
          });
        }
        if (voiceVolumeInput) voiceVolumeInput.value = String(soccerVoiceMixPercent);
        if (voiceVolumeValue) voiceVolumeValue.textContent = `${soccerVoiceMixPercent}%`;
        return soccerVoiceMixPercent;
      }

      function _soccerVoicePlaybackGain() {
        // 足球滑杆的 50% 是项目语音标准响度；100% 允许提升到 2x（约 +6 dB）。
        // 主页面仍会在此基础上应用 N.E.K.O 全局扬声器音量。
        return soccerVoiceMixPercent / DEFAULT_SOCCER_VOICE_MIX_PERCENT;
      }

      const soccerGameAudio = await (async () => {
        const gameSystem = window.NekoGameSystem || {};
        const audioConfig = gameSystem.soccer?.audioConfig || {};
        const config = {
          audioMix: {},
          bgm: {
            startMenu: [],
            inGame: { variants: [] },
            difficulty: {},
            result: {},
            mood: {},
          },
          loopedBgm: {},
          sfx: {},
          ...audioConfig,
        };
        const audio = await soccerGame.audio.mount({
          slot: 'main',
          resources: {
            audioMix: config.audioMix,
            bgm: config.bgm,
            loopedBgm: config.loopedBgm,
            sfx: config.sfx,
          },
          settings: {
            fadeMs: 900,
            maxConcurrent: 12,
            maxPreloadEntries: 128,
            maxPlaylistHistory: 64,
            maxEndWaiters: 32,
          },
        });
        let currentKey = '';
        let preloadRunId = 0;
        let selectedInGameBgm = pickPageInGameBgm(config.bgm.inGame);

        function describeAudioFailure(event, context) {
          if (event?.channel) return { ...event };
          const audio = context?.audio || null;
          const mediaError = audio?.error || null;
          return {
            src: context?.track?.src || audio?.currentSrc || audio?.src || '',
            code: mediaError?.code || '',
            message: mediaError?.message || event?.message || event?.type || String(event || ''),
            networkState: audio?.networkState,
            readyState: audio?.readyState,
          };
        }
        const unsubscribeAudioError = audio.onError((details) => {
          const message = details.channel === 'sfx'
            ? '[SoccerAudio] SFX 播放失败:'
            : '[SoccerAudio] BGM 播放失败，已尝试跳过:';
          console.warn(message, describeAudioFailure(details));
        });

        function isLoopedBgmConfig(value) {
          return value &&
            typeof value === 'object' &&
            !Array.isArray(value) &&
            typeof value.loop === 'string';
        }

        function preloadBgmTree(value) {
          if (!audio || !value) return;
          if (isLoopedBgmConfig(value)) {
            audio.preloadLoopedBgm(value);
            return;
          }
          if (typeof value === 'string' || Array.isArray(value) || value.src) {
            audio.preloadBgm(value);
            return;
          }
          if (typeof value === 'object') {
            Object.values(value).forEach(preloadBgmTree);
          }
        }

        function preloadSfxTree(value) {
          if (!audio || !value) return;
          if (typeof value === 'string' || Array.isArray(value) || value.src) {
            audio.preloadSfx(value);
            return;
          }
          if (typeof value === 'object') {
            Object.values(value).forEach(preloadSfxTree);
          }
        }

        // 足球游戏内音频全部预载，减少第一次切换到对应 BGM / SFX 时的可感知停顿。
        // 注意：HTMLAudio 预载不能保证采样级无缝，只能降低网络读取和首次解码等待。
        function schedulePreloadJobs(jobs) {
          const runId = ++preloadRunId;
          const schedule = (callback) => {
            if (window.requestIdleCallback) {
              window.requestIdleCallback(callback, { timeout: 500 });
              return;
            }
            window.setTimeout(callback, 120);
          };
          const runNext = (index) => {
            if (runId !== preloadRunId || index >= jobs.length) return;
            jobs[index]();
            schedule(() => runNext(index + 1));
          };
          runNext(0);
        }

        function preloadAllAudio() {
          schedulePreloadJobs([
            () => preloadBgmTree(config.bgm.startMenu),
            () => preloadBgmTree(selectedInGameBgm),
            () => preloadBgmTree(config.bgm.difficulty),
            () => preloadBgmTree(config.bgm.mood),
            () => preloadBgmTree(config.bgm.result),
            () => preloadBgmTree(config.loopedBgm),
            () => preloadSfxTree(config.sfx),
          ]);
        }

        function pickPageInGameBgm(inGameConfig) {
          const variants = Array.isArray(inGameConfig?.variants) ? inGameConfig.variants : [];
          if (!variants.length) return isLoopedBgmConfig(inGameConfig) ? inGameConfig : null;
          const index = Math.floor(Math.random() * variants.length);
          return variants[Math.min(index, variants.length - 1)];
        }

        preloadAllAudio();

        function resolvePlaylist() {
          if (!_llm.gameStarted) {
            return { key: 'startMenu', type: 'bgm', playlist: config.bgm.startMenu };
          }

          const moodPlaylist = config.bgm.mood?.[moodKey];
          const difficultyName = DIFFICULTY[difficultyIdx]?.name;
          const isMaxAngry = moodKey === 'angry' && difficultyName === 'max';
          if (!isMaxAngry) {
            // 开场 max + angry 的 plus BGM 只允许覆盖“开场后连续保持 max+angry”的阶段。
            // 一旦玩家/LLM 改过难度或心情导致离开该组合，后续再回到 max+angry 应使用普通 max BGM。
            _openingMaxAngryBgmActive = false;
          }
          if (
            isMaxAngry &&
            _openingMaxAngryBgmActive &&
            moodPlaylist?.openingMax?.loop
          ) {
            return { key: 'mood:angry:opening-max', type: 'loopedBgm', config: moodPlaylist.openingMax };
          }
          if (
            isMaxAngry &&
            moodPlaylist?.max?.loop
          ) {
            return { key: 'mood:angry:max', type: 'loopedBgm', config: moodPlaylist.max };
          }
          if (
            difficultyName === 'lv4' &&
            moodKey !== 'angry' &&
            moodKey !== 'sad' &&
            config.bgm.difficulty?.lv4NonAngry?.loop
          ) {
            return { key: 'difficulty:lv4:non-angry', type: 'loopedBgm', config: config.bgm.difficulty.lv4NonAngry };
          }

          // 正常比赛 BGM：没有命中特殊心情 / 难度 BGM 时统一回到这里。
          // 例如打破 max + angry 组合后，下一次 sync 会切换回 inGame。
          if (selectedInGameBgm?.loop) {
            const selectedId = selectedInGameBgm.id || 'selected';
            return { key: `inGame:${selectedId}`, type: 'loopedBgm', config: selectedInGameBgm };
          }
          return { key: 'inGame', type: 'bgm', playlist: [] };
        }

        function hasPlayableBgmTarget(type, playlist, loopedConfig) {
          if (type === 'loopedBgm') return Boolean(loopedConfig?.loop);
          return Boolean(Array.isArray(playlist) ? playlist.length : playlist);
        }

        function isCurrentBgmTarget(type, playlist, loopedConfig) {
          if (!hasPlayableBgmTarget(type, playlist, loopedConfig)) return true;
          return type === 'loopedBgm'
            ? audio.isCurrentBgm(loopedConfig)
            : audio.isCurrentBgm(playlist);
        }

        function sync(reason = 'sync') {
          if (!audio) return;
          const { key: nextKey, type, playlist, config: loopedConfig } = resolvePlaylist();
          if (nextKey === currentKey && isCurrentBgmTarget(type, playlist, loopedConfig)) return;
          const beforeKey = currentKey || '';
          const details = {
            reason,
            beforeKey,
            nextKey,
            type,
            mood: moodKey,
            difficulty: DIFFICULTY[difficultyIdx]?.name || '',
            score: { player: state.score.player, ai: state.score.ai, round: state.round },
            currentSrc: getCurrentBgmSrc(),
            targetSrcs: type === 'loopedBgm'
              ? [loopedConfig?.intro, loopedConfig?.loop, loopedConfig?.outro].filter(Boolean)
              : (Array.isArray(playlist) ? playlist : [playlist]).map(item => item?.src || item).filter(Boolean),
          };
          try {
            window.SoccerDemoDebugLog?.(
              'info',
              'audio',
              'bgm_switch',
              '足球小游戏 BGM 切换',
              details,
            );
          } catch (_) {}
          currentKey = nextKey;
          if (type === 'loopedBgm') {
            console.log(`[SoccerAudio] 切换循环 BGM=${nextKey} reason=${reason}`);
            void audio.playLoopedBgm(loopedConfig, { id: `soccer:${nextKey}` });
            return;
          }
          console.log(`[SoccerAudio] 切换 BGM 歌单=${nextKey} reason=${reason} tracks=${playlist.length}`);
          void audio.playBgm(playlist, { id: `soccer:${nextKey}` });
        }

        function setConfig(nextConfig = {}) {
          if (nextConfig.bgm && typeof nextConfig.bgm === 'object') {
            const nextMood = nextConfig.bgm.mood;
            const currentMood = config.bgm.mood || {};
            Object.assign(config.bgm, nextConfig.bgm);
            if (nextMood && typeof nextMood === 'object') {
              config.bgm.mood = { ...currentMood, ...nextMood };
            }
            selectedInGameBgm = pickPageInGameBgm(config.bgm.inGame);
          }
          if (nextConfig.sfx && typeof nextConfig.sfx === 'object') {
            Object.assign(config.sfx, nextConfig.sfx);
          }
          if (nextConfig.audioMix && typeof nextConfig.audioMix === 'object') {
            config.audioMix = {
              ...config.audioMix,
              ...nextConfig.audioMix,
              bgm: { ...(config.audioMix?.bgm || {}), ...(nextConfig.audioMix.bgm || {}) },
              sfx: { ...(config.audioMix?.sfx || {}), ...(nextConfig.audioMix.sfx || {}) },
            };
          }
          if (nextConfig.loopedBgm && typeof nextConfig.loopedBgm === 'object') {
            Object.assign(config.loopedBgm, nextConfig.loopedBgm);
          }
          audio?.configure({ audioMix: config.audioMix, bgm: config.bgm, loopedBgm: config.loopedBgm, sfx: config.sfx });
          preloadAllAudio();
          currentKey = '';
          sync('set-config');
        }

        function setBgmVolume(value) {
          return audio ? audio.setBgmVolume(value) : 0;
        }

        function setSfxVolume(value) {
          return audio ? audio.setSfxVolume(value) : 0;
        }

        function playSfx(keyOrAudio, options = {}) {
          return audio ? audio.playSfx(keyOrAudio, options) : Promise.resolve(false);
        }

        function playBgm(keyOrPlaylist, options = {}) {
          return audio ? audio.playBgm(keyOrPlaylist, options) : Promise.resolve(false);
        }

        function waitForBgmEnd(options = {}) {
          return audio ? audio.waitForBgmEnd(options) : Promise.resolve(false);
        }

        function playLoopedBgm(keyOrConfig, options = {}) {
          return audio ? audio.playLoopedBgm(keyOrConfig, options) : Promise.resolve(false);
        }

        function stopLoopedBgm(options = {}) {
          audio?.stopLoopedBgm(options);
        }

        function finishLoopedBgm() {
          return audio ? audio.finishLoopedBgm() : Promise.resolve(false);
        }

        function getBgmVolume() {
          return audio ? audio.getBgmVolume() : 0;
        }

        function getCurrentBgmSrc() {
          return audio ? audio.getCurrentBgmSrc() : '';
        }

        function isCurrentBgm(keyOrConfig) {
          return audio ? audio.isCurrentBgm(keyOrConfig) : false;
        }

        function getSfxVolume() {
          return audio ? audio.getSfxVolume() : 0;
        }

        function unlock() {
          return audio ? audio.unlock() : Promise.resolve(false);
        }

        function stop() {
          currentKey = '';
          audio?.stopBgm();
        }

        function destroy() {
          currentKey = '';
          unsubscribeAudioError();
          audio?.dispose();
        }

        return {
          audio,
          config,
          sync,
          setConfig,
          playBgm,
          waitForBgmEnd,
          playSfx,
          playLoopedBgm,
          stopLoopedBgm,
          finishLoopedBgm,
          setBgmVolume,
          setSfxVolume,
          getBgmVolume,
          getCurrentBgmSrc,
          isCurrentBgm,
          getSfxVolume,
          unlock,
          stop,
          destroy,
        };
      })();
      {
        const gameSystem = window.NekoGameSystem || (window.NekoGameSystem = {});
        gameSystem.soccer = gameSystem.soccer || {};
        gameSystem.soccer.audio = soccerGameAudio;
      }

      // Chrome 等浏览器通常要求点击、按键、触摸这类真实用户激活后才能播放带声音音频；
      // 鼠标移动一般不算用户激活。这里在首次激活时补一次 unlock，让之前被自动播放策略
      // 拒绝的菜单 / 游戏 BGM 有机会恢复。
      function unlockSoccerAudioFromUserActivation() {
        void soccerGameAudio.unlock();
      }
      window.addEventListener('pointerdown', unlockSoccerAudioFromUserActivation, { once: true, capture: true });
      window.addEventListener('keydown', unlockSoccerAudioFromUserActivation, { once: true, capture: true });
      window.addEventListener('touchstart', unlockSoccerAudioFromUserActivation, { once: true, capture: true });

      function _formatVolumePercent(value) {
        return `${Math.round(Math.max(0, Math.min(1, Number(value) || 0)) * 100)}%`;
      }

      function _syncChannelMuteButton(button, muted) {
        if (!button) return;
        button.setAttribute('aria-pressed', String(!!muted));
      }

      function _syncGameAudioVolumeControls() {
        const bgm = soccerGameAudio.getBgmVolume();
        const sfx = soccerGameAudio.getSfxVolume();
        if (bgm > 0) lastNonZeroBgmVolume = bgm;
        if (sfx > 0) lastNonZeroSfxVolume = sfx;
        if (soccerVoiceMixPercent > 0) lastNonZeroVoiceMixPercent = soccerVoiceMixPercent;
        if (bgmVolumeInput) {
          bgmVolumeInput.disabled = !soccerGameAudio.audio;
          bgmVolumeInput.value = String(Math.round(bgm * 100));
        }
        if (bgmVolumeValue) bgmVolumeValue.textContent = _formatVolumePercent(bgm);
        _syncChannelMuteButton(bgmMuteButton, bgm <= 0);
        if (sfxVolumeInput) {
          sfxVolumeInput.disabled = !soccerGameAudio.audio;
          sfxVolumeInput.value = String(Math.round(sfx * 100));
        }
        if (sfxVolumeValue) sfxVolumeValue.textContent = _formatVolumePercent(sfx);
        _syncChannelMuteButton(sfxMuteButton, sfx <= 0);
        if (voiceVolumeInput) voiceVolumeInput.value = String(soccerVoiceMixPercent);
        if (voiceVolumeValue) voiceVolumeValue.textContent = `${soccerVoiceMixPercent}%`;
        _syncChannelMuteButton(voiceMuteButton, soccerVoiceMixPercent <= 0);
      }

      bgmVolumeInput?.addEventListener('input', () => {
        const volume = soccerGameAudio.setBgmVolume(Number(bgmVolumeInput.value) / 100);
        if (volume > 0) lastNonZeroBgmVolume = volume;
        if (bgmVolumeValue) bgmVolumeValue.textContent = _formatVolumePercent(volume);
        _syncChannelMuteButton(bgmMuteButton, volume <= 0);
      });
      sfxVolumeInput?.addEventListener('input', () => {
        const volume = soccerGameAudio.setSfxVolume(Number(sfxVolumeInput.value) / 100);
        if (volume > 0) lastNonZeroSfxVolume = volume;
        if (sfxVolumeValue) sfxVolumeValue.textContent = _formatVolumePercent(volume);
        _syncChannelMuteButton(sfxMuteButton, volume <= 0);
      });
      voiceVolumeInput?.addEventListener('input', () => {
        const percent = _setSoccerVoiceMixPercent(voiceVolumeInput.value, { persist: false });
        _syncChannelMuteButton(voiceMuteButton, percent <= 0);
      });
      voiceVolumeInput?.addEventListener('change', () => {
        _setSoccerVoiceMixPercent(voiceVolumeInput.value, { persist: true });
      });
      bgmMuteButton?.addEventListener('click', () => {
        const current = soccerGameAudio.getBgmVolume();
        if (current > 0) lastNonZeroBgmVolume = current;
        const volume = soccerGameAudio.setBgmVolume(current > 0 ? 0 : lastNonZeroBgmVolume);
        if (bgmVolumeInput) bgmVolumeInput.value = String(Math.round(volume * 100));
        if (bgmVolumeValue) bgmVolumeValue.textContent = _formatVolumePercent(volume);
        _syncChannelMuteButton(bgmMuteButton, volume <= 0);
      });
      sfxMuteButton?.addEventListener('click', () => {
        const current = soccerGameAudio.getSfxVolume();
        if (current > 0) lastNonZeroSfxVolume = current;
        const volume = soccerGameAudio.setSfxVolume(current > 0 ? 0 : lastNonZeroSfxVolume);
        if (sfxVolumeInput) sfxVolumeInput.value = String(Math.round(volume * 100));
        if (sfxVolumeValue) sfxVolumeValue.textContent = _formatVolumePercent(volume);
        _syncChannelMuteButton(sfxMuteButton, volume <= 0);
      });
      voiceMuteButton?.addEventListener('click', () => {
        const current = soccerVoiceMixPercent;
        if (current > 0) lastNonZeroVoiceMixPercent = current;
        const percent = _setSoccerVoiceMixPercent(current > 0 ? 0 : lastNonZeroVoiceMixPercent);
        _syncChannelMuteButton(voiceMuteButton, percent <= 0);
      });
      _syncGameAudioVolumeControls();

      const soccerTestEnabled = new URLSearchParams(window.location.search).get('test') === 'true';
      const debugSttVisible = new URLSearchParams(window.location.search).get('debug_stt') === '1' ||
        window.localStorage?.getItem('soccerDebugStt') === '1';
      if (debugSttVisible && voiceControls) {
        voiceControls.dataset.debugVisible = 'true';
        voiceControls.setAttribute('aria-hidden', 'false');
        console.log('[SoccerVoice][DebugSTT] 调试控件已显示 | 来源=debug_stt/localStorage');
      }
      const debugMoodVisible = soccerTestEnabled ||
        new URLSearchParams(window.location.search).get('debug_mood') === '1' ||
        window.localStorage?.getItem('soccerDebugMood') === '1';
      function readMoodDebugCollapsed() {
        try { return window.localStorage?.getItem('soccerDebugMoodCollapsed') === '1'; }
        catch (_) { return false; }
      }
      const debugMoodCollapsed = readMoodDebugCollapsed();
      let moodDebugMode = debugMoodVisible;
      let moodDebugRotationEnabled = false;
      if (settingsDebugGroup) settingsDebugGroup.hidden = !(debugSttVisible || debugMoodVisible);
      if (debugMoodVisible && moodDebugPanel) {
        moodDebugPanel.dataset.debugVisible = 'true';
        moodDebugPanel.dataset.collapsed = debugMoodCollapsed ? 'true' : 'false';
        moodDebugPanel.setAttribute('aria-hidden', 'false');
        console.log('[SoccerMoodDebug] 调试面板已显示 | 来源=debug_mood/localStorage');
      }
      const isGameRuntimeReady = () => window.__SoccerLoading?.isReady?.() !== false;
      const isGameUiTarget = (target) => !!target?.closest?.('#controls, #loading-overlay, #game-top-controls, #exit-prompt-overlay');
      const _isGameMemoryEnabled = () => gameMemoryToggle ? gameMemoryToggle.checked !== false : true;
      let singlePlayerMode = false;

      const state = {
        player: { x: 0, y: 0, vx: 0, vy: 0 },
        ai:     { x: 0, y: 0, vx: 0, vy: 0, color: '#ef4565' },
        ball:   { x: 0, y: 0, vx: 0, vy: 0 },
        mouse:  { x: 0, y: 0 },
        score:  { player: 0, ai: 0 },
        round:  1,
        flashTimer: 0,
        flashSide: null,
      };
      let openingMovementActive = true;

      function resize() {
        canvas.width = window.innerWidth;
        canvas.height = window.innerHeight;
        debugCanvas.width = window.innerWidth;
        debugCanvas.height = window.innerHeight;
      }
      window.addEventListener('resize', resize);
      resize();

      function resetPositions(servingSide = 0) {
        const W = canvas.width, H = canvas.height;
        state.player.x = W * 0.22 - CFG.charSize/2;
        state.player.y = H * 0.55 - CFG.charSize/2;
        state.player.vx = state.player.vy = 0;
        if (!singlePlayerMode) {
          state.ai.x = W * 0.78 - CFG.charSize/2;
          state.ai.y = H * 0.55 - CFG.charSize/2;
        }
        state.ai.vx = state.ai.vy = 0;
        state.ball.x = W * 0.5;
        state.ball.y = H * 0.55;
        state.ball.vx = servingSide * 80;
        state.ball.vy = 0;
        state.mouse.x = state.player.x + CFG.charSize/2;
        state.mouse.y = state.player.y + CFG.charSize/2;
        openingMovementActive = true;
      }
      resetPositions();

      function toggleSinglePlayerMode(source = 'manual') {
        if (!soccerTestEnabled) return false;
        singlePlayerMode = !singlePlayerMode;
        state.ai.vx = 0;
        state.ai.vy = 0;
        aiWindupRemaining = 0;
        aiWindupAim = null;
        aiWindupTotal = 0;
        aiKickCd = 0.3;
        aiReactSec = 0;
        aiRetreatSec = 0;
        aiRetreatTarget = null;
        aiFreezeSec = 0;
        if (singlePlayerMode) openingMovementActive = false;
        updateMoodDebugPanel();
        soccerSessionDebugLog(
          'info',
          'test_mode',
          'single_player_mode_changed',
          singlePlayerMode ? '已进入单人模式' : '已退出单人模式',
          { enabled: singlePlayerMode, source },
        );
        console.log(`[SoccerTest] 单人模式=${singlePlayerMode ? '开启' : '关闭'} | source=${source}`);
        return singlePlayerMode;
      }

      // 玩家蓄力：按下 = 开始蓄力，松开 = 出脚；冲量 = 1.0 + 0.6*charge（tap=1.0，满蓄=1.6）
      let playerPointerActive = false;
      let playerCharging = false;
      let playerCharge = 0;
      const CHARGE_MAX_SEC = 1.2;

      function deactivatePlayerPointerControl() {
        playerPointerActive = false;
        playerCharging = false;
        playerCharge = 0;
      }

      window.addEventListener('mousemove', e => {
        if (isGameUiTarget(e.target)) {
          deactivatePlayerPointerControl();
          return;
        }
        playerPointerActive = true;
        state.mouse.x = e.clientX;
        state.mouse.y = e.clientY;
      });
      document.addEventListener('mouseleave', deactivatePlayerPointerControl);
      window.addEventListener('mousedown', e => {
        if (isGameUiTarget(e.target)) {
          deactivatePlayerPointerControl();
          return;
        }
        if (!isGameRuntimeReady()) return;
        if (e.button !== 0) return;
        playerPointerActive = true;
        state.mouse.x = e.clientX;
        state.mouse.y = e.clientY;
        playerCharging = true;
        playerCharge = 0;
      });
      window.addEventListener('mouseup', e => {
        if (isGameUiTarget(e.target) || !playerPointerActive) {
          deactivatePlayerPointerControl();
          return;
        }
        if (!isGameRuntimeReady()) return;
        if (e.button !== 0 || !playerCharging) return;
        const { nx, ny } = kickDirFor(state.player);
        const mul = 1.0 + 0.6 * playerCharge;
        kickBall(state.player, nx, ny, mul);
        playerCharging = false;
        playerCharge = 0;
      });
      window.addEventListener('blur', deactivatePlayerPointerControl);
      window.addEventListener('contextmenu', e => e.preventDefault());
      window.addEventListener('keydown', e => {
        if (!isGameRuntimeReady()) return;
        if (isGameUiTarget(e.target)) return;
        if (e.key === 'l' || e.key === 'L') {
          e.preventDefault();
          enableSoccerSessionDebugLog('keyboard_l');
          return;
        }
        if (soccerTestEnabled && (e.key === '[' || e.code === 'BracketLeft')) {
          e.preventDefault();
          toggleSinglePlayerMode('keyboard_bracket_left');
          return;
        }
        if (e.key === 'r' || e.key === 'R') resetPositions();
        // 仅测试 URL 允许人工切换难度与心情。
        if (soccerTestEnabled) {
          // 难度快捷键：u=max，i=lv2，o=lv3，p=lv4。
          const difficultyHotkey = { u: 'max', i: 'lv2', o: 'lv3', p: 'lv4' }[e.key.toLowerCase()];
          if (difficultyHotkey) {
            e.preventDefault();
            setDifficulty(difficultyHotkey, 'difficulty-hotkey');
            return;
          }
          // 1-6 切换 AI 心情（debug）
          const idx = Number(e.key) - 1;
          if (idx >= 0 && idx < MOOD_KEYS.length) setMood(MOOD_KEYS[idx], { manual: true });
        }
      });

      // 通用踢球：从 kicker 中心向 (aimX, aimY) 踢；dirX/dirY 为单位向量，球得到一次性大冲量。
      // 返回是否成功（球必须在射门范围内）。
      function kickBall(kicker, dirX, dirY, impulseScale = 1) {
        if (singlePlayerMode && kicker === state.ai) return false;
        const b = state.ball;
        const cx = kicker.x + CFG.charSize/2, cy = kicker.y + CFG.charSize/2;
        const dx = b.x - cx, dy = b.y - cy;
        const d = Math.hypot(dx, dy);
        if (d > CFG.kickRange + CFG.ballRadius) return false;
        // 抢断/被抢断判定：对方在球周围 100px 内 = 抢断
        const isPlayer = kicker === state.player;
        const opp = isPlayer ? state.ai : state.player;
        const opx = opp.x + CFG.charSize/2, opy = opp.y + CFG.charSize/2;
        const oppNearBall = Math.hypot(b.x - opx, b.y - opy) < 100;
        b.vx = dirX * CFG.kickImpulse * impulseScale + kicker.vx * 0.4;
        b.vy = dirY * CFG.kickImpulse * impulseScale + kicker.vy * 0.4;
        // 边界系统：记录最后触球方
        _outOfBoundsSide = isPlayer ? 'player' : 'ai';
        lastTouchSide = isPlayer ? 'player' : 'ai';
        lastPlayerKickAtMs = isPlayer ? performance.now() : 0;
        playerKickWallBounceForStartle = false;
        if (!isPlayer) openingMovementActive = false;
        logGameEvent(isPlayer ? 'player-kick' : 'ai-kick');
        markBallTouched();
        void soccerGameAudio.playSfx('ball.kick');
        emitEvent(isPlayer ? 'player-kick' : 'ai-kick');
        // AI 把球从玩家附近踢走 → AI 抢到了；玩家把球从 AI 附近踢走 → AI 被抢
        if (oppNearBall) triggerScene(isPlayer ? 'stolen' : 'steal');
        return true;
      }

      // 射门方向 = 从 kicker 中心指向球
      function kickDirFor(kicker) {
        const cx = kicker.x + CFG.charSize/2, cy = kicker.y + CFG.charSize/2;
        const dx = state.ball.x - cx, dy = state.ball.y - cy;
        const d = Math.hypot(dx, dy) || 1;
        return { nx: dx / d, ny: dy / d, d };
      }

      // AI 踢球：根据难度档决定冷却/前摇；根据 mode 决定朝哪儿踢
      let aiKickCd = 0;
      let aiWindupRemaining = 0;   // > 0 时处于前摇（已锁定方向等待出脚）
      let aiWindupAim = null;      // { x, y } 单位向量
      let aiWindupTotal = 0;       // 用来画前摇进度

      function aiTryKick(dt) {
        if (singlePlayerMode) {
          aiWindupRemaining = 0;
          aiWindupAim = null;
          aiWindupTotal = 0;
          return;
        }
        const diff = DIFFICULTY[difficultyIdx];
        const mood = MOODS[moodKey];
        const kickImpulse = 0.95 * mood.kickImpulseMul;
        const rollCd = () => (diff.kickCdMin + Math.random() * (diff.kickCdMax - diff.kickCdMin)) * mood.kickCdMul;

        const a = state.ai, b = state.ball;
        const acx = a.x + CFG.charSize/2, acy = a.y + CFG.charSize/2;
        const inRange = Math.hypot(b.x - acx, b.y - acy) <= CFG.kickRange + CFG.ballRadius;
        if (aiFreezeSec > 0) return;  // 冻结期间不能踢

        // 前摇进行中
        if (aiWindupRemaining > 0) {
          aiWindupRemaining -= dt;
          if (!inRange) {
            aiWindupRemaining = 0; aiWindupAim = null; aiWindupTotal = 0;
            aiKickCd = 0.3;
            return;
          }
          if (aiWindupRemaining <= 0) {
            if (aiWindupAim) kickBall(a, aiWindupAim.x, aiWindupAim.y, kickImpulse);
            aiWindupRemaining = 0; aiWindupAim = null; aiWindupTotal = 0;
            aiKickCd = rollCd();
          }
          return;
        }

        aiKickCd -= dt;
        if (aiKickCd > 0 || !inRange) return;
        if (!diff.allowAttack && aiMode === 'attack') return;

        const W = canvas.width, H = canvas.height;
        let aimX, aimY;
        // 心情给射门加散射（正值 = 更乱，负值 = 更准；在基础随机之外叠加）
        const spreadY = mood.spread > 0 ? (Math.random() - 0.5) * mood.spread * 2 : 0;
        const accuracyMul = mood.spread < 0 ? (1 + mood.spread / 100) : 1; // relaxed 瞄得更准

        if (aiMode === 'defend' || aiMode === 'clear') {
          const upClearer = b.y < H / 2;
          aimX = W * 0.25 - b.x;
          aimY = (upClearer ? 0 : H) - b.y;
          aimY *= 0.4 + Math.random() * 0.3;
          aimY += spreadY;
        } else {
          const playerCy = state.player.y + CFG.charSize/2;
          const shootTop = playerCy > H / 2;
          const cornerY = shootTop
            ? H/2 - CFG.goalHeight/2 + 20
            : H/2 + CFG.goalHeight/2 - 20;
          aimX = 0 - b.x;
          aimY = (cornerY + (Math.random() - 0.5) * 50 * accuracyMul + spreadY) - b.y;
        }
        const aL = Math.hypot(aimX, aimY) || 1;
        const dx = aimX / aL, dy = aimY / aL;

        if (diff.windupSec > 0) {
          aiWindupAim = { x: dx, y: dy };
          aiWindupRemaining = diff.windupSec;
          aiWindupTotal = diff.windupSec;
        } else {
          kickBall(a, dx, dy, kickImpulse);
          aiKickCd = rollCd();
        }
      }

      function stepCharacter(c, tx, ty, maxSpeed, accel, dt) {
        const cx = c.x + CFG.charSize/2, cy = c.y + CFG.charSize/2;
        const dx = tx - cx, dy = ty - cy;
        const d = Math.hypot(dx, dy);
        if (d > 3) {
          c.vx += (dx / d) * accel * dt;
          c.vy += (dy / d) * accel * dt;
        }
        const k = Math.max(0, 1 - CFG.charDamping * dt);
        c.vx *= k; c.vy *= k;
        const v = Math.hypot(c.vx, c.vy);
        if (v > maxSpeed) { c.vx = c.vx / v * maxSpeed; c.vy = c.vy / v * maxSpeed; }
        c.x += c.vx * dt;
        c.y += c.vy * dt;
        c.x = Math.max(0, Math.min(canvas.width  - CFG.charSize, c.x));
        c.y = Math.max(0, Math.min(canvas.height - CFG.charSize, c.y));
      }

      function stepBall(dt) {
        const b = state.ball, r = CFG.ballRadius;
        b.x += b.vx * dt;
        b.y += b.vy * dt;
        const k = Math.max(0, 1 - CFG.ballFriction * dt);
        b.vx *= k; b.vy *= k;
        const W = canvas.width, H = canvas.height;
        const goalY1 = H/2 - CFG.goalHeight/2, goalY2 = H/2 + CFG.goalHeight/2;

        // ── 边界模式：出界检测 ──
        if (BOUNDARY.enabled) {
          const m = BOUNDARY.margin;
          const bLeft = m, bRight = W - m, bTop = m, bBottom = H - m;
          const isOut = b.x - r < bLeft || b.x + r > bRight || b.y - r < bTop || b.y + r > bBottom;
          if (isOut) {
            _outOfBoundsTimer += dt;
            if (_outOfBoundsTimer >= BOUNDARY.outOfBoundsDelay) {
              // 出界：球重置到中场，给对方发球
              // 最后触球方出界 → 对方获得控球权
              const servingSide = _outOfBoundsSide === 'player' ? -1 : +1;
              b.x = W / 2;
              b.y = H / 2;
              b.vx = servingSide * 80;
              b.vy = 0;
              _outOfBoundsTimer = 0;
              triggerScene(_outOfBoundsSide === 'player' ? 'stolen' : 'steal');
              emitEvent('out-of-bounds', { lastTouch: _outOfBoundsSide });
            }
            return; // 出界期间不做墙壁反弹
          } else {
            _outOfBoundsTimer = 0;
          }
        }

        // ── 默认模式：墙壁反弹 ──
        let bouncedOffWall = false;
        if (b.y < r)     { b.y = r;     b.vy = -b.vy * CFG.wallRestitution; bouncedOffWall = true; }
        if (b.y > H - r) { b.y = H - r; b.vy = -b.vy * CFG.wallRestitution; bouncedOffWall = true; }
        const inGoalBand = (b.y > goalY1 && b.y < goalY2);
        if (!inGoalBand) {
          if (b.x < r)     { b.x = r;     b.vx = -b.vx * CFG.wallRestitution; bouncedOffWall = true; }
          if (b.x > W - r) { b.x = W - r; b.vx = -b.vx * CFG.wallRestitution; bouncedOffWall = true; }
        }
        if (bouncedOffWall) {
          const now = performance.now();
          if (now - _lastWallBounceSfxAt >= WALL_BOUNCE_SFX_COOLDOWN_MS) {
            _lastWallBounceSfxAt = now;
            void soccerGameAudio.playSfx('ball.kick');
          }
          markPlayerKickWallBounceForStartle();
        }
      }

      // 球"幽灵"时间：大于 0 时球临时穿透所有角色（unstick 用，让球能从死角飞出去）
      let ballGhostSec = 0;
      let lastTouchSide = null;

      // 圆 vs 圆碰撞（角色是半径 = charSize/2 的圆，中心 = 物理框中心）
      function resolveCharBall(c) {
        if (singlePlayerMode && c === state.ai) return;
        if (ballGhostSec > 0) return;
        const b = state.ball;
        const cx = c.x + CFG.charSize/2, cy = c.y + CFG.charSize/2;
        const charR = CFG.charSize/2;
        const dx = b.x - cx, dy = b.y - cy;
        const d = Math.hypot(dx, dy);
        const minD = charR + CFG.ballRadius;
        if (d >= minD) return;
        const nxn = d === 0 ? 1 : dx / d;
        const nyn = d === 0 ? 0 : dy / d;
        const overlap = minD - d;
        b.x += nxn * overlap;
        b.y += nyn * overlap;
        const rvx = b.vx - c.vx, rvy = b.vy - c.vy;
        const vn = rvx * nxn + rvy * nyn;
        if (vn < 0) {
          const j = -vn * CFG.charBallRestitution;
          b.vx += j * nxn;
          b.vy += j * nyn;
        }
        b.vx += c.vx * 0.15;
        b.vy += c.vy * 0.15;
      }

      function checkGoal() {
        const b = state.ball, r = CFG.ballRadius;
        const W = canvas.width, H = canvas.height;
        const y1 = H/2 - CFG.goalHeight/2, y2 = H/2 + CFG.goalHeight/2;
        if (b.x - r < CFG.goalWidth && b.y > y1 && b.y < y2) {
          state.score.ai++; flash('ai');
          const kind = lastTouchSide === 'player' ? 'own-goal-by-player' : 'goal-scored';
          speechState.scoreAgeAccum = 0;
          speechState.lastGoalTime = performance.now();
          _handlePassiveGuardGoal('ai', kind);
          rebalanceDifficultyForScore(kind);
          triggerScene(kind);
          emitEvent(kind, { side: 'ai', lastTouch: lastTouchSide });
          resetPositions(+1);
          state.round++;
          soccerGameAudio.sync('goal-ai');
          if (DIFFICULTY_AUTOCYCLE_ON_GOAL) cycleDifficulty();
        }
        if (b.x + r > W - CFG.goalWidth && b.y > y1 && b.y < y2) {
          state.score.player++; flash('player');
          const kind = lastTouchSide === 'ai' ? 'own-goal-by-ai' : 'goal-conceded';
          speechState.scoreAgeAccum = 0;
          speechState.lastGoalTime = performance.now();
          _handlePassiveGuardGoal('player', kind);
          rebalanceDifficultyForScore(kind);
          triggerScene(kind);
          emitEvent(kind, { side: 'player', lastTouch: lastTouchSide });
          resetPositions(-1);
          state.round++;
          soccerGameAudio.sync('goal-player');
          if (DIFFICULTY_AUTOCYCLE_ON_GOAL) cycleDifficulty();
        }
      }

      function flash(side) { state.flashTimer = 0.6; state.flashSide = side; }

      // 难度档：从 max（最难）到 lv4（最弱）。普通陪玩默认 lv2 起手，后续由 LLM 控制。
      // 兜底难度统一钉到 lv2：原本的 lv2/lv3 random 让玩家受到的初始压力抖动 1 档，
      // pre-game 端 LLM 又被 prompt 强制建议 neutral_play 选 lv2 → 兜底也对齐 lv2，
      // 让 fallback 路径与 prompt 引导走同一档，避免"LLM 出 lv2 / 兜底出 lv3"的体感
      // 差异。lv3 / lv4 仍可由 balance_hint 推荐 + LLM setDifficulty 切到，不会消失。
      const DIFFICULTY = [
        { name: 'max', kickCdMin: 0.4, kickCdMax: 0.6, windupSec: 0.00, speedMul: 1.00, allowAttack: true },
        { name: 'lv2', kickCdMin: 0.9, kickCdMax: 1.4, windupSec: 0.35, speedMul: 1.00, allowAttack: true },
        { name: 'lv3', kickCdMin: 0.9, kickCdMax: 1.4, windupSec: 0.35, speedMul: 0.78, allowAttack: true },
        { name: 'lv4', kickCdMin: 0.9, kickCdMax: 1.4, windupSec: 0.35, speedMul: 0.78, allowAttack: false },
      ];
      const DEFAULT_DIFFICULTY_INDEX = DIFFICULTY.findIndex(d => d.name === 'lv2');
      let difficultyIdx = DEFAULT_DIFFICULTY_INDEX >= 0 ? DEFAULT_DIFFICULTY_INDEX : 1;
      let startScreenDifficultyOverridden = false;
      function cycleDifficulty() {
        difficultyIdx = (difficultyIdx + 1) % DIFFICULTY.length;
      }
      function setDifficulty(name, opts = {}) {
        const normalized = typeof opts === 'string'
          ? { source: opts, reason: opts }
          : (opts.source ? opts : { ...opts, source: 'manual' });
        return setDifficultyInternal(name, normalized);
      }

      function setDifficultyInternal(name, opts = {}) {
        const i = DIFFICULTY.findIndex(d => d.name === name);
        if (i < 0) return false;
        const source = String(opts.source || '');
        if (
          soccerTestEnabled &&
          !_llm.gameStarted &&
          (source === 'manual' || source === 'difficulty-hotkey')
        ) {
          // A tester's visible start-screen choice wins over a late pre-game
          // context response for this launch.
          startScreenDifficultyOverridden = true;
        }
        if (i === difficultyIdx) return false;
        const before = DIFFICULTY[difficultyIdx].name;
        difficultyIdx = i;
        try {
          window.__SoccerPassiveGuardRecordDifficulty?.(before, name, opts);
        } catch (_) { /* optional debug hook */ }
        triggerScene('diff-' + name);
        emitEvent('difficulty-changed', { difficulty: name });
        if (opts.source) {
          console.log(`[SoccerDifficulty] ${opts.source} | ${before} -> ${name} ${opts.reason || ''}`.trim());
        }
        _passiveGuardDebugLog('State', 'passive_guard_state_change', '难度变化', {
          field: 'difficulty',
          before,
          after: name,
          source: opts.source || 'manual',
          reason: opts.reason || '',
          manual: opts.manual === true || !opts.source || opts.source === 'manual' || opts.source === 'difficulty-hotkey',
        });
        soccerGameAudio.sync(opts.reason || opts.source || 'difficulty-changed');
        return true;
      }

      function targetDifficultyForScoreDiff(scoreDiff) {
        if (scoreDiff >= 10) return 'lv4';
        if (scoreDiff >= 3) return 'lv3';
        if (scoreDiff <= -6) return 'max';
        if (scoreDiff <= -3) return 'lv2';
        return 'lv2';
      }

      const SCORE_DIFF_AUTO_BALANCE_ENABLED = false;

      function rebalanceDifficultyForScore(reason = 'score') {
        // 已废弃的按比分差直接改难度逻辑。
        // 这段只作为历史逻辑和未来 fallback 入口保留；默认关闭。
        // 正常难度调整应走开局上下文、LLM control 或后续统一难度管理，而不是在前端按固定比分差强制覆盖。
        if (!SCORE_DIFF_AUTO_BALANCE_ENABLED) return false;

        const scoreDiff = Number(state.score.ai || 0) - Number(state.score.player || 0);
        const target = targetDifficultyForScoreDiff(scoreDiff);
        return setDifficultyInternal(target, {
          source: 'auto-balance',
          reason: `reason=${reason} scoreDiff=${scoreDiff}`,
        });
      }

      // 心情（只作用于 AI）。键盘 1-6 切换
      //   speedMul:       行动速度倍率
      //   kickCdMul:      冷却倍率
      //   kickImpulseMul: 冲量倍率
      //   spread:         瞄准时 y 轴随机扰动范围（px），负值 = 更准
      //   style:          特殊行为 tag
      const MOODS = {
        calm:      { speedMul: 1.00, kickCdMul: 1.00, kickImpulseMul: 1.00, spread: 0,   style: 'default',     emotion: 'neutral' },
        happy:     { speedMul: 1.00, kickCdMul: 0.90, kickImpulseMul: 1.05, spread: 35,  style: 'aggressive',  emotion: 'happy' },
        angry:     { speedMul: 1.15, kickCdMul: 0.80, kickImpulseMul: 1.25, spread: 80,  style: 'reckless',    emotion: 'angry' },
        relaxed:   { speedMul: 0.85, kickCdMul: 1.30, kickImpulseMul: 1.00, spread: -15, style: 'patient',     emotion: 'relaxed' },
        sad:       { speedMul: 0.70, kickCdMul: 1.50, kickImpulseMul: 0.70, spread: 10,  style: 'zoneout',     emotion: 'sad' },
        surprised: { speedMul: 1.00, kickCdMul: 1.10, kickImpulseMul: 1.00, spread: 70,  style: 'startle',     emotion: 'surprised' },
      };
      const MOOD_KEYS = ['calm', 'happy', 'angry', 'relaxed', 'sad', 'surprised'];
      let moodKey = 'calm';
      function isAutomaticMoodBlocked(opts = {}) {
        return moodDebugMode && opts.manual !== true && opts.force !== true && !moodDebugRotationEnabled;
      }

      function setMood(name, opts = {}) {
        if (!MOODS[name]) return;
        if (isAutomaticMoodBlocked(opts)) return false;
        moodKey = name;
        // 同步 AI 头像表情（Live2D / VRM）
        const emotion = MOODS[name].emotion;
        window.__SoccerAiAvatarController?.setEmotion?.(emotion);
        return true;
      }

      // startle：惊讶心情下，球高速撞来/擦身掠过时冻结 AI 一小段时间。
      // 以前用“速度变化 / dt”当加速度，墙壁反弹和踢球瞬间都会误触发；这里改成
      // 基于球速 + 未来近距离轨迹的几何判定。
      let aiFreezeSec = 0;
      const STARTLE_RULE = {
        directSpeedMin: 700,
        grazeSpeedMin: 850,
        lookaheadSec: 0.55,
        directPadding: 8,
        grazeOuterPadding: 70,
        directCooldownBase: 8,
        grazeCooldownBase: 10,
        cooldownRandomExtra: 5,
        mutualLockSec: 2,
        playerKickTriggerWindowSec: 1,
        freezeSec: 0.25,
      };
      let startleDirectCdSec = 0;
      let startleGrazeCdSec = 0;
      let startleMutualLockSec = 0;
      let lastPlayerKickAtMs = 0;
      let playerKickWallBounceForStartle = false;
      // zoneout：sad 模式下周期性走神
      let zoneoutCooldown = 3 + Math.random() * 2;

      // AI 决策：状态机 + 球预判 + 反应延时
      //   defend：球高速冲向己方球门（右）→ 退守在球和门之间
      //   clear： 球进入己方半场腹地 → 冲向球（即便 stalk 位置出界也直接撞）
      //   attack：默认 → 站到球后方（相对于左门）的位置，冲过去顺带把球推进门
      let aiMode = 'attack';
      let aiReactSec = 0;
      let aiTargetCache = { x: 0, y: 0 };
      // 强制撤退：unstick 触发后 AI 必须离开球一段时间，防止立刻冲回去把球再次卡墙
      let aiRetreatSec = 0;
      let aiRetreatTarget = null;

      function aiDecide(dt) {
        if (singlePlayerMode) {
          state.ai.vx = 0;
          state.ai.vy = 0;
          return;
        }
        const mood = MOODS[moodKey];
        // 强制撤退期间锁定目标，不让 aiDecide 把目标改回球
        if (aiRetreatSec > 0) {
          aiRetreatSec -= dt;
          if (aiRetreatTarget) aiTargetCache = aiRetreatTarget;
          return;
        }
        aiReactSec += dt;
        // relaxed（放松）反应更慢；其他默认 100ms
        const reactThreshold = mood.style === 'patient' ? 0.18 : 0.1;
        if (aiReactSec < reactThreshold) return;
        aiReactSec = 0;

        const b = state.ball;
        const W = canvas.width, H = canvas.height;
        const goalY = H / 2;
        if (openingMovementActive && b.x > W * 0.72) {
          openingMovementActive = false;
        }

        // relaxed 预判更远（提前走位），其他默认 0.25s
        const predictT = mood.style === 'patient' ? 0.4 : 0.25;
        const pbx = b.x + b.vx * predictT;
        const pby = b.y + b.vy * predictT;

        // aggressive（开心）/ reckless（生气）：不切 defend 态，一路进攻
        const skipDefend = mood.style === 'aggressive' || mood.style === 'reckless';
        const ballGoingToOwnGoal = !skipDefend && b.vx > 80 && b.x > W * 0.4;
        const ballDeepInOwnHalf = mood.style === 'reckless' ? false : b.x > W * 0.7;

        const margin = CFG.charSize/2 + 6;

        if (ballGoingToOwnGoal && ballDeepInOwnHalf) {
          aiMode = 'defend';
          const tx = Math.min(W - margin, b.x + 60);
          const ty = Math.max(margin, Math.min(H - margin, b.y));
          aiTargetCache = { x: tx, y: ty };
        } else if (ballDeepInOwnHalf) {
          aiMode = 'clear';
          aiTargetCache = { x: Math.max(margin, Math.min(W - margin, pbx)),
                            y: Math.max(margin, Math.min(H - margin, pby)) };
        } else {
          aiMode = 'attack';
          const openingRouteY = openingMovementActive
            ? estimateOpeningAttackRouteY(b, state.player, state.ai, H, CFG, OPENING_MOVEMENT)
            : null;
          const toGoalX = 0 - pbx;
          const toGoalY = goalY - pby;
          const gl = Math.hypot(toGoalX, toGoalY) || 1;
          const ngx = toGoalX / gl, ngy = toGoalY / gl;
          const stalkDist = CFG.charSize/2 + CFG.ballRadius + 8;
          let tx = pbx - ngx * stalkDist;
          let ty = pby - ngy * stalkDist;
          if (tx < margin || tx > W - margin || ty < margin || ty > H - margin) {
            tx = pbx; ty = pby;
          }
          if (openingRouteY !== null) {
            const maxVerticalShift = H * OPENING_MOVEMENT.maxVerticalShiftRatio;
            const routeShift = Math.max(
              -maxVerticalShift,
              Math.min(maxVerticalShift, openingRouteY - ty),
            );
            ty = Math.max(
              margin,
              Math.min(H - margin, ty + routeShift * OPENING_MOVEMENT.routeBlend),
            );
          }
          aiTargetCache = { x: tx, y: ty };
        }

        // 第四档：只防御不进攻。强行把 attack 改为守在己方半场腹地
        const diff = DIFFICULTY[difficultyIdx];
        if (!diff.allowAttack && aiMode === 'attack') {
          aiMode = 'defend';
          const tx = Math.max(W * 0.65, Math.min(W - margin, b.x + 30));
          const ty = Math.max(margin, Math.min(H - margin, b.y));
          aiTargetCache = { x: tx, y: ty };
        }
      }

      function aiTarget() {
        return aiTargetCache;
      }

      // 事件日志：记录每次 kick / unstick，debug 显示在屏幕左侧。用来排查"球为什么突然转弯"。
      const GAME_EVENTS = [];
      function logGameEvent(label) {
        const b = state.ball;
        GAME_EVENTS.push({ label, time: performance.now(), x: b.x|0, y: b.y|0,
                           vx: b.vx|0, vy: b.vy|0 });
        while (GAME_EVENTS.length > 5) GAME_EVENTS.shift();
      }

      function decayStartleCooldowns(dt) {
        startleDirectCdSec = Math.max(0, startleDirectCdSec - dt);
        startleGrazeCdSec = Math.max(0, startleGrazeCdSec - dt);
        startleMutualLockSec = Math.max(0, startleMutualLockSec - dt);
      }

      function rollStartleCooldown(baseSec) {
        return baseSec + Math.random() * STARTLE_RULE.cooldownRandomExtra;
      }

      function projectedBallPassNearAi() {
        const b = state.ball;
        const speed = Math.hypot(b.vx, b.vy);
        if (speed < Math.min(STARTLE_RULE.directSpeedMin, STARTLE_RULE.grazeSpeedMin)) return null;

        const relVx = b.vx - state.ai.vx;
        const relVy = b.vy - state.ai.vy;
        const relSpeedSq = relVx * relVx + relVy * relVy;
        if (relSpeedSq <= 0.0001) return null;

        const acx = state.ai.x + CFG.charSize / 2;
        const acy = state.ai.y + CFG.charSize / 2;
        const relX = b.x - acx;
        const relY = b.y - acy;
        const tClosest = -(relX * relVx + relY * relVy) / relSpeedSq;
        if (tClosest <= 0 || tClosest > STARTLE_RULE.lookaheadSec) return null;

        const closestX = relX + relVx * tClosest;
        const closestY = relY + relVy * tClosest;
        const closestDist = Math.hypot(closestX, closestY);
        const bodyRadius = CFG.charSize / 2 + CFG.ballRadius;
        const directRadius = bodyRadius + STARTLE_RULE.directPadding;
        const grazeRadius = bodyRadius + STARTLE_RULE.grazeOuterPadding;

        return { speed, tClosest, closestDist, directRadius, grazeRadius };
      }

      function playerKickStartleWindowRemainingSec(now = performance.now()) {
        if (!lastPlayerKickAtMs) return 0;
        const elapsedSec = (now - lastPlayerKickAtMs) / 1000;
        return Math.max(0, STARTLE_RULE.playerKickTriggerWindowSec - elapsedSec);
      }

      function markPlayerKickWallBounceForStartle() {
        if (lastTouchSide === 'player' && playerKickStartleWindowRemainingSec() > 0) {
          playerKickWallBounceForStartle = true;
        }
      }

      function triggerStartle(kind, cooldownBaseSec) {
        aiFreezeSec = STARTLE_RULE.freezeSec;
        aiKickCd = 0;
        const cooldownSec = rollStartleCooldown(cooldownBaseSec);
        if (kind === 'startle-direct') startleDirectCdSec = cooldownSec;
        else startleGrazeCdSec = cooldownSec;
        startleMutualLockSec = STARTLE_RULE.mutualLockSec;
        triggerScene(kind, { cooldownSec });
      }

      // 心情逐帧副作用：startle（惊讶，被高速球吓到）/ zoneout（悲伤，周期性走神）
      function aiMoodTick(dt) {
        decayStartleCooldowns(dt);
        const mood = MOODS[moodKey];

        if (
          mood.style === 'startle' &&
          lastTouchSide === 'player' &&
          playerKickStartleWindowRemainingSec() > 0 &&
          aiFreezeSec <= 0 &&
          startleMutualLockSec <= 0
        ) {
          const pass = projectedBallPassNearAi();
          if (pass) {
            if (
              startleDirectCdSec <= 0 &&
              !playerKickWallBounceForStartle &&
              pass.speed >= STARTLE_RULE.directSpeedMin &&
              pass.closestDist <= pass.directRadius
            ) {
              triggerStartle('startle-direct', STARTLE_RULE.directCooldownBase);
            } else if (
              startleGrazeCdSec <= 0 &&
              pass.speed >= STARTLE_RULE.grazeSpeedMin &&
              pass.closestDist > pass.directRadius &&
              pass.closestDist <= pass.grazeRadius
            ) {
              triggerStartle('startle-graze', STARTLE_RULE.grazeCooldownBase);
            }
          }
        }
        if (mood.style === 'zoneout') {
          zoneoutCooldown -= dt;
          if (zoneoutCooldown <= 0 && aiFreezeSec <= 0) {
            aiFreezeSec = 0.3;
            zoneoutCooldown = 3 + Math.random() * 2;
            triggerScene('zoneout');
          }
        }
      }

      // 球死角脱困：用 500ms 时间窗位移判断卡死（不是逐帧速度，更准）。
      // 触发时：给球朝场中心的软冲量（在现有速度上叠加），同时把 AI 往反方向推一下，
      // 避免 AI 立刻冲回来再次把球卡住。
      const STUCK_WINDOW = 0.5;
      let ballPosHistory = []; // [{t, x, y}]
      let unstickCd = 0;
      function unstickBall(dt) {
        const b = state.ball;
        const W = canvas.width, H = canvas.height;
        const r = CFG.ballRadius;
        const now = performance.now() / 1000;
        ballPosHistory.push({ t: now, x: b.x, y: b.y });
        while (ballPosHistory.length > 2 && now - ballPosHistory[0].t > STUCK_WINDOW * 2) {
          ballPosHistory.shift();
        }
        unstickCd -= dt;
        const oldFrame = ballPosHistory.find(p => now - p.t >= STUCK_WINDOW - 0.05);
        if (!oldFrame) return;
        const displacement = Math.hypot(b.x - oldFrame.x, b.y - oldFrame.y);
        const atWall = b.x < r + 10 || b.x > W - r - 10 || b.y < r + 10 || b.y > H - r - 10;
        if (displacement < 12 && atWall && unstickCd <= 0) {
          const toCenterX = W / 2 - b.x, toCenterY = H / 2 - b.y;
          const l = Math.hypot(toCenterX, toCenterY) || 1;
          // 软冲量（叠加而非覆盖），减少"隔空转弯"的突兀感
          b.vx += (toCenterX / l) * 280;
          b.vy += (toCenterY / l) * 280;
          // 让球短时穿模，从 AI 身体里直接飞出去，再开启碰撞
          ballGhostSec = 0.5;
          if (!singlePlayerMode) {
            // 把 AI 往球的反方向推，并锁定撤退目标 1 秒 —— 不让 aiDecide 把它拉回球那儿
            const ai = state.ai;
            const acx = ai.x + CFG.charSize/2, acy = ai.y + CFG.charSize/2;
            const axdir = (acx - b.x) || 1, aydir = (acy - b.y) || 0;
            const aL = Math.hypot(axdir, aydir) || 1;
            // 撤退目标 = AI 中心 + 反方向 200px，clamp 在场内
            const retreatMargin = CFG.charSize/2 + 6;
            const rtx = Math.max(retreatMargin, Math.min(W - retreatMargin, acx + (axdir / aL) * 200));
            const rty = Math.max(retreatMargin, Math.min(H - retreatMargin, acy + (aydir / aL) * 200));
            aiRetreatTarget = { x: rtx, y: rty };
            // 与 unstickCd 对齐：撤退锁定覆盖整个脱困冷却期，避免 AI 重新冲向同一个墙角
            aiRetreatSec = 1.8;
            aiTargetCache = aiRetreatTarget;
            // 瞬间给 AI 一个反向速度，不然刚解锁撤退目标时它得从零加速
            ai.vx = (axdir / aL) * 400;
            ai.vy = (aydir / aL) * 400;
            aiKickCd = Math.max(aiKickCd, 0.9);
            aiWindupRemaining = 0; aiWindupAim = null; aiWindupTotal = 0;
          }
          unstickCd = 1.8;
          logGameEvent('unstick');
          emitEvent('unstick');
          triggerScene('unstick');
        }
      }

      // ═══════════════════════════════════════════════════════════════════════════
      //  AI 说话子系统（Speech）
      //  外部接口在 window.SoccerDemo 里，详见底部。
      //  - say(text, opts)          : 生成气泡 + 同步触发 onSpeak 监听器（给 LLM 用）
      //  - setBubbleRenderer(fn)    : 美术素材接入时把默认 DOM 气泡替换掉
      //  - onSpeak(cb) / onEvent(cb): 订阅文本流 / 游戏事件流
      // ═══════════════════════════════════════════════════════════════════════════
      const speakListeners = new Set();
      const eventListeners = new Set();
      function emitSpeak(payload) {
        for (const cb of speakListeners) { try { cb(payload); } catch(e) { console.warn('[SoccerDemo] speak listener error', e); } }
      }
      function emitEvent(label, meta = {}) {
        const payload = { label, meta, time: performance.now() };
        for (const cb of eventListeners) { try { cb(payload); } catch(e) { console.warn('[SoccerDemo] event listener error', e); } }
      }
      avatarEventEmitter = emitEvent;

      // 心情装饰：同一句在不同心情下风味不同
      const MOOD_STYLE = {
        calm:      (s) => s,
        happy:     (s) => s.endsWith('~') || s.endsWith('♪') ? s : s + ' ♪',
        angry:     (s) => (s.endsWith('！') ? s : s + '！'),
        relaxed:   (s) => s.replace(/！+/g, '~'),
        sad:       (s) => '……' + s,
        surprised: (s) => (s.startsWith('诶') || s.startsWith('！') ? s : '诶？！' + s),
      };

      // 气泡渲染：默认 DOM 气泡；可通过 setBubbleRenderer 替换
      const bubbleEl = document.getElementById('ai-speech-bubble');
      const playerBubbleEl = document.getElementById('player-speech-bubble');
      let bubbleEndsAt = 0;
      let bubbleHideTimer = null;
      let playerBubbleHideTimer = null;
      let lastPlayerTranscriptKey = '';
      let lastPlayerTranscriptAt = 0;
      const PLAYER_TRANSCRIPT_DEDUPE_MS = 3000;
      function defaultBubbleRenderer({ text, mood, durationMs, sourceLabel }) {
        if (!bubbleEl) return;
        bubbleEl.textContent = text;
        bubbleEl.setAttribute('data-mood', mood);
        // 不再向用户展示 ``data-source-label``（"LLM生成 · 850ms" / "开局上下文" / "快路径
        // 兜底" 等）。这些是开发期溯源/延迟信息，玩家看到只觉冗余、且强调"延迟"反而拉低
        // 体验。``sourceLabel`` 仍由 caller 传进来用于 console 日志与调试，但不渲染到
        // bubble 上方。CSS ``[data-source-label]::before`` 规则保留以备调试模式回插。
        bubbleEl.removeAttribute('data-source-label');
        bubbleEl.classList.add('show');
        if (bubbleHideTimer) clearTimeout(bubbleHideTimer);
        bubbleHideTimer = setTimeout(() => { bubbleEl.classList.remove('show'); }, durationMs);
      }
      function defaultBubbleClear() {
        if (!bubbleEl) return;
        bubbleEl.classList.remove('show');
        if (bubbleHideTimer) { clearTimeout(bubbleHideTimer); bubbleHideTimer = null; }
      }
      let bubbleRenderer = defaultBubbleRenderer;
      let bubbleClearer  = defaultBubbleClear;

      function showPlayerSpeechBubble(text) {
        const clean = String(text || '').trim();
        if (!playerBubbleEl || !clean) return false;
        playerBubbleEl.textContent = clean;
        playerBubbleEl.classList.add('show');
        if (playerBubbleHideTimer) clearTimeout(playerBubbleHideTimer);
        const durationMs = Math.min(6000, Math.max(2200, 1400 + clean.length * 90));
        playerBubbleHideTimer = setTimeout(() => {
          playerBubbleEl.classList.remove('show');
          playerBubbleHideTimer = null;
        }, durationMs);
        return true;
      }

      function showPlayerTranscriptBubble(transcript, { source = 'voice-input' } = {}) {
        const text = String(transcript?.text || transcript?.transcript || '').trim();
        if (!text) return false;
        const requestId = String(transcript?.requestId || transcript?.request_id || '').trim();
        const key = requestId ? `request:${requestId}` : `text:${text}`;
        const now = Date.now();
        if (key === lastPlayerTranscriptKey && (requestId || now - lastPlayerTranscriptAt < PLAYER_TRANSCRIPT_DEDUPE_MS)) {
          return false;
        }
        lastPlayerTranscriptKey = key;
        lastPlayerTranscriptAt = now;
        console.log(`[SoccerVoice][PlayerBubble] 最终转写已显示 | source=${source} request=${requestId || '-'} chars=${text.length}`);
        return showPlayerSpeechBubble(text);
      }

      function clearPlayerSpeechBubble() {
        if (playerBubbleEl) playerBubbleEl.classList.remove('show');
        if (playerBubbleHideTimer) {
          clearTimeout(playerBubbleHideTimer);
          playerBubbleHideTimer = null;
        }
        lastPlayerTranscriptKey = '';
        lastPlayerTranscriptAt = 0;
      }

      function positionSpeechBubble(element, anchorElement, headRatio = 0.15) {
        if (!element || !element.classList.contains('show') || !anchorElement) return;
        const r = anchorElement.getBoundingClientRect();
        if (r.width < 10 || r.left < -5000) return;
        const x = r.left + r.width / 2;
        const y = r.top + r.height * headRatio;
        element.style.left = x + 'px';
        element.style.top  = y + 'px';
      }

      // 每帧同步两个气泡到各自角色头顶。
      function positionBubble() {
        positionSpeechBubble(bubbleEl, aiEl, 0.15);
        positionSpeechBubble(playerBubbleEl, playerEl, 0.15);
      }

      // 冷却管理：全局 + per-key
      const SPEECH_CD = Object.create(null);
      let currentSpeechPriority = 0;
      const DEBUG_SPEECH_VERBOSE = false;
      let DEBUG_WARN_WITH_STACK = false;
      const SHOW_BUILTIN_SPEECH = false;
      const DIFFICULTY_AUTOCYCLE_ON_GOAL = false;
      const REQUEST_CONTROL_REASON = true;
      const SURRENDER_REMINDER_STORAGE_KEY = 'settings/surrender-reminder-enabled';
      const PASSIVE_GUARD_SIDE_CAR_TIMEOUT_MS = 7000;
      const EXIT_PROMPT_LINE_WAIT_MS = 4200;
      const LLM_INTERCEPT_KINDS = new Set([
        'goal-scored', 'goal-conceded',
        'own-goal-by-ai', 'own-goal-by-player',
        'steal', 'stolen',
      ]);
      const LOGGED_BUILTIN_KINDS = new Set([
        'goal-scored', 'goal-conceded',
        'own-goal-by-ai', 'own-goal-by-player',
        'steal', 'stolen',
      ]);
      const EVENT_LABELS = {
        'goal-scored': '猫娘进球',
        'goal-conceded': '猫娘丢球',
        'own-goal-by-ai': '猫娘乌龙球',
        'own-goal-by-player': '玩家乌龙球',
        'user-voice': '玩家语音',
        'mailbox-batch': '累积上下文',
        'light-balance-hint': '轻量平衡提示',
        'send-rescue-hint': '发送救场提示',
        'passive-surrender-hint': '认输台词请求',
        'passive-rest-hint': '休息台词请求',
        'teaching-progress-hint': '教学进度提示',
        'steal': '猫娘抢到球',
        'stolen': '猫娘被抢断',
        'shot-miss': '射门未进',
        'long-attack-possession': '长时间进攻',
        'long-defense-possession': '长时间防守',
        'player-idle': '玩家停住',
        'player-charging-long': '玩家蓄力过久',
        'close-proximity': '距离太近',
        'free-ball': '无人碰球',
        'score-boring': '比分沉闷',
        'no-goal-1min': '长时间无进球',
        'fast-ball': '球速很快',
        'startle': '猫娘受惊',
        'startle-direct': '猫娘被高速球撞来吓到',
        'startle-graze': '猫娘被高速擦身球吓到',
        'zoneout': '猫娘走神',
        'unstick': '球脱困',
        'out-of-bounds': '球出界',
        'mood-calm': '心情变为平静',
        'mood-happy': '心情变为开心',
        'mood-angry': '心情变为生气',
        'mood-relaxed': '心情变为放松',
        'mood-sad': '心情变为悲伤',
        'mood-surprised': '心情变为惊讶',
        'diff-max': '难度最高',
        'diff-lv2': '难度二档',
        'diff-lv3': '难度三档',
        'diff-lv4': '难度四档',
      };
      function eventLabel(kind) {
        if (!kind) return '未知事件';
        if (kind.endsWith('-llm')) {
          const base = kind.slice(0, -4);
          return `${eventLabel(base)} / LLM台词`;
        }
        if (kind.endsWith('-fallback')) {
          const base = kind.slice(0, -9);
          return `${eventLabel(base)} / 内建兜底`;
        }
        return EVENT_LABELS[kind] || kind;
      }
      const MOOD_LABELS = {
        calm: '平静',
        happy: '开心',
        angry: '生气',
        relaxed: '放松',
        sad: '悲伤',
        surprised: '惊讶',
      };
      function moodLabel(mood) {
        if (!mood) return '未知心情';
        return MOOD_LABELS[mood] ? `${MOOD_LABELS[mood]}(${mood})` : mood;
      }
      function debugText(key, fallback, params) {
        return _i18n(`debug.${key}`, fallback, params);
      }
      function debugParam(name) {
        return '{' + '{' + name + '}' + '}';
      }
      function debugMoodLabel(mood) {
        if (singlePlayerMode) return '气跑了';
        const fallback = MOOD_LABELS[mood] || mood || '-';
        return mood ? _i18n(`moods.${mood}`, fallback) : '-';
      }
      function debugDifficultyLabel(name) {
        const fallback = ({ max: '最高', lv2: '二档', lv3: '三档', lv4: '四档' })[name] || name || '-';
        return name ? _i18n(`difficulties.${name}`, fallback) : '-';
      }
      function debugAiModeLabel(mode) {
        const fallback = ({ attack: '进攻', defend: '防守', clear: '解围' })[mode] || mode || '-';
        return mode ? _i18n(`aiModes.${mode}`, fallback) : '-';
      }
      function debugGameEventLabel(label) {
        const fallback = ({
          'player-kick': '玩家踢球',
          'ai-kick': 'AI 踢球',
          unstick: '球脱困',
        })[label] || label || '-';
        return label && Object.prototype.hasOwnProperty.call({
          'player-kick': true,
          'ai-kick': true,
          unstick: true,
        }, label) ? _i18n(`debugEvents.${label}`, fallback) : fallback;
      }
      function soccerRecoverableLog(...args) {
        if (DEBUG_WARN_WITH_STACK) console.warn(...args);
        else console.log(...args);
      }

      const fallbackStatusState = {
        shown: new Set(),
        hitCounts: new Map(),
      };
      const FALLBACK_DIAGNOSTIC_REPEAT_EVERY = 20;

      function _recordFallbackDiagnostic(title, options = {}) {
        const cleanTitle = String(title || '小游戏流程').trim();
        const fallbackText = String(options.fallback || '已使用兜底继续运行').trim();
        const reason = String(options.reason || '').trim();
        const key = String(options.key || `${cleanTitle}:${fallbackText}:${reason}`);
        const hits = (fallbackStatusState.hitCounts.get(key) || 0) + 1;
        fallbackStatusState.hitCounts.set(key, hits);
        const periodicRepeat = hits > 1 && hits % FALLBACK_DIAGNOSTIC_REPEAT_EVERY === 0;
        if (!options.repeat && fallbackStatusState.shown.has(key) && !periodicRepeat) return;
        fallbackStatusState.shown.add(key);

        const message = `${cleanTitle}失败，${fallbackText}${reason ? `（${reason}）` : ''}`;
        soccerRecoverableLog(`[SoccerFallback] ${message}`, options.details || {});
        try {
          window.SoccerDemoDebugLog?.(
            'warning',
            'fallback',
            'fallback_notice',
            message,
            {
              title: cleanTitle,
              fallback: fallbackText,
              reason,
              diagnosticHitCount: hits,
              periodicRepeat,
              ...options.details,
            },
            false,
            { preserveDetails: true },
          );
        } catch (_) {}
      }

      let currentSpeechIsUserReply = false;

      function _isUserReplyBubbleOpts(opts = {}) {
        if (opts.userReply === true || opts.hasUserSpeech || opts.hasUserText) return true;
        const kind = String(opts.kind || '');
        return kind === 'user-voice' || kind === 'user-text' ||
          kind.startsWith('user-voice-') || kind.startsWith('user-text-');
      }

      function canSay(opts) {
        const now = performance.now();
        if (opts.cooldownKey && SPEECH_CD[opts.cooldownKey] > now) return false;
        // 头顶气泡按“最新文本”显示；唯一保护对象是正在显示的用户输入回复，
        // 避免普通游戏事件把正在回应玩家的气泡顶掉。语音优先级只影响播放仲裁。
        if (now < bubbleEndsAt && currentSpeechIsUserReply && !_isUserReplyBubbleOpts(opts)) return false;
        return true;
      }

      function say(raw, opts = {}) {
        if (!raw) return false;
        opts.priority = opts.priority || 0;
        if (!canSay(opts)) return false;
        const kind = opts.kind || 'generic';
        const isLLM = kind.endsWith('-llm');
        const isFallback = kind.endsWith('-fallback');
        const source = isLLM ? 'LLM生成' : (isFallback ? '快路径兜底' : '内建快路径');
        const sourceLabel = opts.sourceLabel || source;
        const shouldWaitForLLM = opts.waitForLLM && !isLLM;
        const now = performance.now();
        const dur = opts.duration || Math.min(3800, 1500 + raw.length * 90);
        if (shouldWaitForLLM) {
          if (DEBUG_SPEECH_VERBOSE || LOGGED_BUILTIN_KINDS.has(kind)) {
            console.log(`[Soccer] 等待LLM | 回合=${state.round} 事件=${eventLabel(kind)}(${kind}) 心情=${moodLabel(moodKey)} 兜底="${raw}"`);
          }
          emitSpeak({
            text: raw,
            textRaw: raw,
            mood: moodKey,
            kind,
            round: state.round,
            source,
            sourceLabel,
            priority: opts.priority,
            durationMs: dur,
            ts: now,
            builtinFallback: raw,
          });
          return true;
        }
        if (!isLLM && !isFallback && !SHOW_BUILTIN_SPEECH) {
          if (opts.cooldownKey && opts.cooldownSec) {
            SPEECH_CD[opts.cooldownKey] = now + opts.cooldownSec * 1000;
          }
          if (DEBUG_SPEECH_VERBOSE || LOGGED_BUILTIN_KINDS.has(kind)) {
            console.log(`[Soccer] 内建隐藏 | 回合=${state.round} 事件=${eventLabel(kind)}(${kind}) 心情=${moodLabel(moodKey)} 原文="${raw}"`);
          }
          return true;
        }
        if (isLLM || DEBUG_SPEECH_VERBOSE || LOGGED_BUILTIN_KINDS.has(kind)) {
          console.log(`[Soccer] ${source} | 回合=${state.round} 事件=${eventLabel(kind)}(${kind}) 心情=${moodLabel(moodKey)} 输出="${raw}"`);
        }
        const styled = (MOOD_STYLE[moodKey] || MOOD_STYLE.calm)(raw);
        currentSpeechPriority = opts.priority;
        currentSpeechIsUserReply = _isUserReplyBubbleOpts(opts);
        bubbleEndsAt = now + dur;
        if (opts.cooldownKey && opts.cooldownSec) {
          SPEECH_CD[opts.cooldownKey] = now + opts.cooldownSec * 1000;
        }
        const payload = {
          text: styled, textRaw: raw,
          mood: moodKey, kind: opts.kind || 'generic',
          round: state.round,
          source, sourceLabel,
          priority: opts.priority, durationMs: dur,
          ts: now,
        };
        try { bubbleRenderer(payload); } catch(e) { console.warn('[SoccerDemo] bubble render error', e); }
        emitSpeak(payload);
        return true;
      }

      // ═══════════════════════════════════════════════════════════════════════════
      //  场景 → 文案：按 kind 分发
      // ═══════════════════════════════════════════════════════════════════════════
      const LINES = {
        'goal-scored':   ['进啦！😼', '我赢定了~', '嘻嘻~'],
        'goal-conceded': ['啊——！', '等等，再来一次！', '可恶……'],
        'own-goal-by-ai': ['……我踢进自己门了？', '刚刚不算！', '呜，脚滑了啦'],
        'own-goal-by-player': ['诶？你帮我进了？', '这是送我的球吗~', '怎么踢错边啦'],
        'shot-miss':     ['啧……', '差一点', '嗯？'],
        'steal':         ['嘿嘿，给我的~', '我拿到了', '哈！'],
        'stolen':        ['哎呀', '啊！', '等等！'],
        'long-attack-possession':   ['得去拿回来！', '等我的'],
        'long-defense-possession':  ['就这样吧~', '你想射就射呗'],
        'player-idle':   ['你打不打？', '怎么不动了？', '在想什么呢'],
        'player-charging-long':  ['蓄这么久？', '快松手嘛~'],
        'close-proximity':  ['你离我太近了！', '喂'],
        'free-ball':     ['球在哪——？', '哎，没人管吗'],
        'score-boring':  ['好无聊啊', '什么时候进球啊'],
        'no-goal-1min':  ['要不……平局？', '手累了……'],
        'fast-ball':     ['好猛！', '哇！'],
        'startle':       ['！', '吓我一跳'],
        'startle-direct':['哇！别撞我！', '吓、吓死我了！'],
        'startle-graze': ['刚刚擦过去了？！', '别从我旁边飞啦！'],
        'zoneout':       ['……啊？在打球呢。', '嗯？到哪了'],
        'unstick':       ['！球怎么不动了', '球卡了？'],
        'diff-max':      ['热身好了哦', '认真模式~'],
        'diff-lv2':      ['稍微休息一下~'],
        'diff-lv3':      ['嗯……体力不太行了'],
        'diff-lv4':      ['打不过，就守着吧'],
        'mood-calm':     ['……'],
        'mood-happy':    ['好开心喵~'],
        'mood-angry':    ['气死我了！'],
        'mood-relaxed':  ['懒得动了……'],
        'mood-sad':      ['……'],
        'mood-surprised':['诶诶？！'],
      };
      const PRIORITY = {
        'goal-scored': 9, 'goal-conceded': 9,
        'own-goal-by-ai': 9, 'own-goal-by-player': 9,
        'diff-max': 7, 'diff-lv2': 7, 'diff-lv3': 7, 'diff-lv4': 7,
        'mood-happy': 6, 'mood-angry': 6, 'mood-sad': 6, 'mood-surprised': 6, 'mood-relaxed': 6, 'mood-calm': 6,
        'unstick': 5, 'startle': 5, 'startle-direct': 5, 'startle-graze': 5, 'zoneout': 5,
        'steal': 4, 'stolen': 4, 'shot-miss': 4,
        'long-attack-possession': 3, 'long-defense-possession': 3,
        'close-proximity': 3, 'fast-ball': 3,
        'player-idle': 2, 'player-charging-long': 2,
        'free-ball': 1, 'score-boring': 1, 'no-goal-1min': 1,
      };
      const COOLDOWN = {
        // key, cooldownSec
        'shot-miss': 4, 'steal': 8, 'stolen': 8,
        'long-attack-possession': 15, 'long-defense-possession': 15,
        'close-proximity': 12, 'fast-ball': 10,
        'player-idle': 20, 'player-charging-long': 15,
        'free-ball': 20, 'score-boring': 30, 'no-goal-1min': 60,
        'startle': 6, 'startle-direct': 8, 'startle-graze': 10, 'zoneout': 6, 'unstick': 4,
      };
      const QUICK_LINE_KEYS = [
        'goal-scored', 'goal-conceded', 'own-goal-by-ai', 'own-goal-by-player',
        'steal', 'stolen', 'player-idle', 'player-charging-long',
        'free-ball', 'startle-direct', 'startle-graze', 'zoneout',
      ];

      function applyGeneratedQuickLines(lines = {}) {
        const applied = {};
        for (const key of QUICK_LINE_KEYS) {
          const pool = Array.isArray(lines[key])
            ? lines[key].map(v => String(v || '').trim()).filter(Boolean).slice(0, 4)
            : [];
          if (!pool.length) continue;
          LINES[key] = pool;
          applied[key] = pool;
        }
        return applied;
      }

      async function loadGeneratedQuickLines() {
        try {
          await ensureSoccerCharacterInfo();
          // quick-lines 在 _startGameRoute 之前就命中 LLM；同时发送显式偏好和
          // render-only 兜底，让首批台词选对模板又不把 UI 语言持久化成角色偏好。
          const resp = await soccerGame.dialogue.quickLines({
            ..._conversationLanguagePayload(),
          });
          const data = resp.data || {};
          if (!resp.ok || data.ok === false) {
            _recordFallbackDiagnostic('快路径台词生成', {
              fallback: '继续使用内建快路径',
              reason: data.reason || data.error || resp.status,
              key: 'quick-lines-http',
            });
            soccerRecoverableLog(`[SoccerQuickLines] 生成失败 | HTTP ${resp.status}，继续使用内建快路径`, LINES);
            return;
          }
          if (!data.ok || !data.lines || !Object.keys(data.lines).length) {
            _recordFallbackDiagnostic('快路径台词生成', {
              fallback: '继续使用内建快路径',
              reason: data.error || 'empty_lines',
              key: 'quick-lines-empty',
            });
            soccerRecoverableLog('[SoccerQuickLines] 生成失败 | 继续使用内建快路径', data);
            return;
          }
          const applied = applyGeneratedQuickLines(data.lines);
          console.log(`[SoccerQuickLines] 已生成 | 角色=${data.character || '未知'} 覆盖=${Object.keys(applied).length} 缺失=${(data.missing || []).join(',') || '无'}`);
          console.log('[SoccerQuickLines] 当前快路径台词字典:', applied);
        } catch (e) {
          _recordFallbackDiagnostic('快路径台词生成', {
            fallback: '继续使用内建快路径',
            reason: String(e),
            key: 'quick-lines-request',
          });
          soccerRecoverableLog('[SoccerQuickLines] 生成请求失败 | 继续使用内建快路径', e);
        }
      }
      loadGeneratedQuickLines();

      function triggerScene(kind, opts = {}) {
        const pool = LINES[kind];
        if (!pool || !pool.length) return false;
        const text = pool[Math.floor(Math.random() * pool.length)];
        return say(text, {
          kind,
          priority: PRIORITY[kind] || 0,
          cooldownKey: kind,
          cooldownSec: opts.cooldownSec ?? COOLDOWN[kind] ?? 0,
          waitForLLM: LLM_INTERCEPT_KINDS.has(kind),
        });
      }

      // ═══════════════════════════════════════════════════════════════════════════
      //  场景触发器：部分在事件点调用（goal/kick/unstick/freeze），部分在 tick 轮询
      // ═══════════════════════════════════════════════════════════════════════════
      const speechState = {
        ballInOppHalfSec: 0,
        playerIdleSec: 0,
        closeProximitySec: 0,
        lastBallTouchTime: 0,     // 球最后被碰的时间（kick 或 unstick）
        lastGoalTime: performance.now(),
        scoreAgeAccum: 0,
        lastFreezeKind: null,
      };

      function speechTick(dt) {
        const b = state.ball;
        const W = canvas.width;

        // 球长时间在对方半场（按 allowAttack 分支）
        if (b.x < W * 0.5) {
          speechState.ballInOppHalfSec += dt;
          if (speechState.ballInOppHalfSec > 8) {
            const diff = DIFFICULTY[difficultyIdx];
            triggerScene(diff.allowAttack ? 'long-attack-possession' : 'long-defense-possession');
            speechState.ballInOppHalfSec = 0;
          }
        } else {
          speechState.ballInOppHalfSec = 0;
        }

        // 玩家 idle 10s
        const pSpeed = Math.hypot(state.player.vx, state.player.vy);
        if (pSpeed < 40) speechState.playerIdleSec += dt;
        else speechState.playerIdleSec = 0;
        if (speechState.playerIdleSec > 10) {
          triggerScene('player-idle');
          speechState.playerIdleSec = 0;
        }

        // 玩家蓄力 1s+ 没放
        if (playerCharging && playerCharge > 0.85) {
          triggerScene('player-charging-long');
        }

        // 玩家靠得太近 2s
        const dxPA = (state.player.x + CFG.charSize/2) - (state.ai.x + CFG.charSize/2);
        const dyPA = (state.player.y + CFG.charSize/2) - (state.ai.y + CFG.charSize/2);
        const distPA = Math.hypot(dxPA, dyPA);
        if (distPA < 90) speechState.closeProximitySec += dt;
        else speechState.closeProximitySec = 0;
        if (speechState.closeProximitySec > 2) {
          triggerScene('close-proximity');
          speechState.closeProximitySec = 0;
        }

        // 球无人管 5s（无 kick / unstick）
        const sinceTouch = (performance.now() - speechState.lastBallTouchTime) / 1000;
        if (sinceTouch > 5) {
          triggerScene('free-ball');
          speechState.lastBallTouchTime = performance.now() - 3000; // 重新计
        }

        // 快球
        if (Math.hypot(b.vx, b.vy) > 1200) {
          triggerScene('fast-ball');
        }

        // 0:0 很久
        speechState.scoreAgeAccum += dt;
        if (state.score.player === 0 && state.score.ai === 0 && speechState.scoreAgeAccum > 30) {
          triggerScene('score-boring');
          speechState.scoreAgeAccum = 0;
        }
        if (speechState.scoreAgeAccum > 60) {
          triggerScene('no-goal-1min');
          rebalanceDifficultyForScore('no-goal-1min');
          speechState.scoreAgeAccum = 0;
        }

        // 冻结：进入时触发一次（hook 在 aiMoodTick 里会检测 aiFreezeSec 从 0 突变 > 0）
      }

      // 外挂到 kickBall / checkGoal / aiMoodTick 的触发已经用 emitEvent + triggerScene 实现，
      // 这里的 speechState.lastBallTouchTime 由 kickBall 和 unstickBall 间接更新
      function markBallTouched() { speechState.lastBallTouchTime = performance.now(); }

      // ═══════════════════════════════════════════════════════════════════════════
      //  心情 20s 随机轮换
      // ═══════════════════════════════════════════════════════════════════════════
      let moodRotateTimer = null;
      function enableMoodRotation(intervalSec = 20, opts = {}) {
        if (moodDebugMode) {
          if (opts.manual !== true) return !!moodRotateTimer;
          moodDebugRotationEnabled = true;
        }
        if (moodRotateTimer) { clearInterval(moodRotateTimer); moodRotateTimer = null; }
        moodRotateTimer = setInterval(() => {
          const others = MOOD_KEYS.filter(k => k !== moodKey);
          const next = others[Math.floor(Math.random() * others.length)];
          setMood(next, { source: 'rotation' });
        }, intervalSec * 1000);
        return true;
      }
      function disableMoodRotation(opts = {}) {
        if (moodDebugMode) {
          if (opts.manual !== true && opts.force !== true) return !moodRotateTimer;
          moodDebugRotationEnabled = false;
        }
        if (moodRotateTimer) { clearInterval(moodRotateTimer); moodRotateTimer = null; }
        return true;
      }

      // 心情切换时播"mood-XX"气泡（重写 setMood 让它走一下这个钩子）
      const __setMoodBase = setMood;
      setMood = function(name, opts = {}) {
        if (!MOODS[name]) return false;
        if (name === moodKey) return __setMoodBase(name, opts);
        const changed = __setMoodBase(name, opts);
        if (!changed) return false;
        triggerScene('mood-' + name);
        emitEvent('mood-changed', { mood: name });
        _passiveGuardDebugLog('State', 'passive_guard_state_change', '心情变化', {
          field: 'mood',
          after: name,
          source: opts.source || (opts.manual ? 'manual' : 'unknown'),
          reason: opts.reason || '',
          manual: opts.manual === true || opts.source === 'manual',
        });
        soccerGameAudio.sync('mood-changed');
        return true;
      };

      const __cycleDiffBase = cycleDifficulty;
      cycleDifficulty = function() {
        __cycleDiffBase();
        triggerScene('diff-' + DIFFICULTY[difficultyIdx].name);
        emitEvent('difficulty-changed', { difficulty: DIFFICULTY[difficultyIdx].name });
        soccerGameAudio.sync('difficulty-changed');
      };

      // ═══════════════════════════════════════════════════════════════════════════
      //  头像切换（最小实现：VRM 路径切换 + L2D 路径切换）
      // ═══════════════════════════════════════════════════════════════════════════
      async function setPlayerAvatar({ type, path } = {}) {
        if (type !== 'vrm') throw new Error('player avatar: only vrm supported');
        if (!path) throw new Error('player avatar: path required');
        soccerGame.capabilities.require('avatar-renderer');
        if (window.__SoccerPlayerAvatarController
            && !window.__SoccerPlayerAvatarController.disposed) {
          await window.__SoccerPlayerAvatarController.setModel({ type, path });
          return;
        }
        window.__SoccerPlayerAvatarController = await soccerGame.avatar.mount(
          soccerAvatarMountConfig('player', { type, path }),
        );
      }

      async function setAiAvatar({ type, path } = {}) {
        if (!path) throw new Error('ai avatar: path required');
        if (!['live2d', 'vrm'].includes(type)) {
          throw new Error('ai avatar: only live2d/vrm supported');
        }
        soccerGame.capabilities.require('avatar-renderer');
        if (window.__SoccerAiAvatarController
            && !window.__SoccerAiAvatarController.disposed) {
          await window.__SoccerAiAvatarController.setModel({ type, path });
          return;
        }
        window.__SoccerAiAvatarController = await soccerGame.avatar.mount(
          soccerAvatarMountConfig('ai', { type, path }),
        );
      }

      function roundDebugNumber(value) {
        const n = Number(value);
        return Number.isFinite(n) ? Math.max(0, n).toFixed(2) : '0.00';
      }

      function clearStartleCooldowns() {
        startleDirectCdSec = 0;
        startleGrazeCdSec = 0;
        startleMutualLockSec = 0;
        lastPlayerKickAtMs = 0;
        playerKickWallBounceForStartle = false;
        updateMoodDebugPanel();
      }

      function getMoodDebugSnapshot() {
        const b = state.ball;
        const scoreDiff = Number(state.score.ai || 0) - Number(state.score.player || 0);
        return {
          debugMode: moodDebugMode,
          mood: singlePlayerMode ? '气跑了' : moodKey,
          singlePlayerMode,
          difficulty: DIFFICULTY[difficultyIdx].name,
          difficultyAutoTarget: targetDifficultyForScoreDiff(scoreDiff),
          scoreDiff,
          moodRotation: moodRotateTimer ? 'on' : 'off',
          moodRotationDebugEnabled: moodDebugRotationEnabled,
          aiMode,
          lastTouchSide,
          playerKickStartleWindowSec: Number(roundDebugNumber(playerKickStartleWindowRemainingSec())),
          playerKickWallBounceForStartle,
          aiFreezeSec: Number(roundDebugNumber(aiFreezeSec)),
          aiKickCdSec: Number(roundDebugNumber(aiKickCd)),
          aiWindupSec: Number(roundDebugNumber(aiWindupRemaining)),
          startleDirectCdSec: Number(roundDebugNumber(startleDirectCdSec)),
          startleGrazeCdSec: Number(roundDebugNumber(startleGrazeCdSec)),
          startleMutualLockSec: Number(roundDebugNumber(startleMutualLockSec)),
          zoneoutCooldownSec: Number(roundDebugNumber(zoneoutCooldown)),
          ballSpeed: Math.round(Math.hypot(b.vx, b.vy)),
          ballGhost: ballGhostSec > 0,
        };
      }

      function setMoodDebugVisible(visible = true, persist = false) {
        if (!moodDebugPanel) return false;
        const shouldShow = !!visible;
        moodDebugMode = shouldShow;
        moodDebugPanel.dataset.debugVisible = shouldShow ? 'true' : 'false';
        moodDebugPanel.setAttribute('aria-hidden', shouldShow ? 'false' : 'true');
        if (shouldShow) {
          moodDebugPanel.dataset.collapsed = readMoodDebugCollapsed() ? 'true' : 'false';
          disableMoodRotation({ force: true });
        } else {
          _syncMoodRotationPolicy('debug-panel-hidden');
        }
        if (persist) {
          try {
            if (shouldShow) window.localStorage?.setItem('soccerDebugMood', '1');
            else window.localStorage?.removeItem('soccerDebugMood');
          } catch (_) { /* storage can be unavailable */ }
        }
        updateMoodDebugPanel();
        return shouldShow;
      }

      function setMoodDebugCollapsed(collapsed = true, persist = false) {
        if (!moodDebugPanel) return false;
        const shouldCollapse = !!collapsed;
        moodDebugPanel.dataset.collapsed = shouldCollapse ? 'true' : 'false';
        if (persist) {
          try {
            if (shouldCollapse) window.localStorage?.setItem('soccerDebugMoodCollapsed', '1');
            else window.localStorage?.removeItem('soccerDebugMoodCollapsed');
          } catch (_) { /* storage can be unavailable */ }
        }
        updateMoodDebugPanel();
        return shouldCollapse;
      }

      function updateMoodDebugPanel() {
        if (!moodDebugPanel || moodDebugPanel.dataset.debugVisible !== 'true') return;
        const snapshot = getMoodDebugSnapshot();
        const collapsed = moodDebugPanel.dataset.collapsed === 'true';
        const collapseButton = moodDebugPanel.querySelector('[data-debug-action="collapse"]');
        if (collapseButton) {
          collapseButton.textContent = collapsed ? '展开' : '收起';
          collapseButton.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
        }
        moodDebugPanel.querySelectorAll('[data-debug-mood]').forEach(btn => {
          btn.dataset.active = btn.dataset.debugMood === moodKey ? 'true' : 'false';
        });
        moodDebugPanel.querySelectorAll('[data-debug-difficulty]').forEach(btn => {
          btn.dataset.active = btn.dataset.debugDifficulty === snapshot.difficulty ? 'true' : 'false';
        });
        const rotationToggleButton = moodDebugPanel.querySelector('[data-debug-action="rotation-toggle"]');
        if (rotationToggleButton) {
          const rotationOn = snapshot.moodRotation === 'on';
          rotationToggleButton.dataset.active = rotationOn ? 'true' : 'false';
          rotationToggleButton.textContent = rotationOn ? '随机心情：开' : '随机心情：关';
        }
        if (moodDebugReadout) {
          moodDebugReadout.textContent = [
            `测试=${snapshot.debugMode ? '开' : '关'}  心情=${snapshot.mood}  难度=${snapshot.difficulty}  随机=${snapshot.moodRotation === 'on' ? '开' : '关'}`,
            `分差=${snapshot.scoreDiff}  自动目标难度=${snapshot.difficultyAutoTarget}`,
            `AI状态=${snapshot.aiMode}  最后触球=${snapshot.lastTouchSide || '-'}  受惊窗口=${roundDebugNumber(snapshot.playerKickStartleWindowSec)}s  撞墙=${snapshot.playerKickWallBounceForStartle ? '是' : '否'}`,
            `冻结=${roundDebugNumber(snapshot.aiFreezeSec)}s`,
            `踢球冷却=${roundDebugNumber(snapshot.aiKickCdSec)}s  前摇=${roundDebugNumber(snapshot.aiWindupSec)}s`,
            `直撞受惊冷却=${roundDebugNumber(snapshot.startleDirectCdSec)}s`,
            `擦身受惊冷却=${roundDebugNumber(snapshot.startleGrazeCdSec)}s`,
            `受惊互斥锁=${roundDebugNumber(snapshot.startleMutualLockSec)}s`,
            `走神冷却=${roundDebugNumber(snapshot.zoneoutCooldownSec)}s  球速=${snapshot.ballSpeed}`,
          ].join('\n');
        }
      }

      // ═══════════════════════════════════════════════════════════════════════════
      //  对外接口
      // ═══════════════════════════════════════════════════════════════════════════
      window.SoccerDemo = {
        MOODS: Object.freeze(MOOD_KEYS.slice()),
        DIFFICULTIES: Object.freeze(DIFFICULTY.map(d => d.name)),
        // mood
        setMood: (name, opts = {}) => setMood(name, opts),
        getMood: () => moodKey,
        enableMoodRotation,
        disableMoodRotation,
        // difficulty
        setDifficulty(name, opts = {}) {
          return setDifficultyInternal(name, opts.source ? opts : { ...opts, source: 'manual' });
        },
        getDifficulty: () => DIFFICULTY[difficultyIdx].name,
        cycleDifficulty: () => cycleDifficulty(),
        // avatar
        setPlayerAvatar,
        setAiAvatar,
        getPlayerAvatar: () => {
          const state = window.__SoccerPlayerAvatarController?.getState?.();
          return state?.model ? { ...state.model, ready: !!state.ready } : { type: 'none', path: '', ready: false };
        },
        getAiAvatar:    () => {
          const state = window.__SoccerAiAvatarController?.getState?.();
          return state?.model ? { ...state.model, ready: !!state.ready } : { type: 'none', path: '', ready: false };
        },
        // speech
        say,
        triggerScene,
        onSpeak(cb) { speakListeners.add(cb); return () => speakListeners.delete(cb); },
        offSpeak(cb) { speakListeners.delete(cb); },
        setBubbleRenderer(fn) {
          bubbleRenderer = typeof fn === 'function' ? fn : defaultBubbleRenderer;
        },
        clearBubble() { bubbleClearer(); bubbleEndsAt = 0; currentSpeechPriority = 0; currentSpeechIsUserReply = false; },
        // events
        onEvent(cb) { eventListeners.add(cb); return () => eventListeners.delete(cb); },
        offEvent(cb) { eventListeners.delete(cb); },
        setWarnStacks(enabled = true) { DEBUG_WARN_WITH_STACK = !!enabled; },
        // boundary
        enableBoundary(enabled = true) {
          BOUNDARY.enabled = enabled;
          boundaryToggle.checked = enabled;
        },
        isBoundaryEnabled: () => BOUNDARY.enabled,
        // 供外部调试：获取内部状态（只读快照）
        _snapshot: () => ({ mood: moodKey, difficulty: DIFFICULTY[difficultyIdx].name,
                            round: state.round,
                            score: { ...state.score }, aiMode, aiFreezeSec, lastTouchSide,
                            singlePlayerMode,
                            playerKickStartleWindowSec: playerKickStartleWindowRemainingSec(),
                            playerKickWallBounceForStartle,
                            startle: {
                              directCdSec: startleDirectCdSec,
                              grazeCdSec: startleGrazeCdSec,
                              mutualLockSec: startleMutualLockSec,
                            },
                            zoneoutCooldownSec: zoneoutCooldown,
                            ballGhost: ballGhostSec > 0 }),
      };

      function handleMoodDebugButton(btn) {
        if (!btn) return;
        const mood = btn.dataset.debugMood;
        const difficulty = btn.dataset.debugDifficulty;
        const action = btn.dataset.debugAction;
        if (!soccerTestEnabled && (mood || difficulty || action === 'rotation-toggle')) return;
        if (mood) {
          window.SoccerDemo.setMood(mood, { manual: true });
        } else if (difficulty) {
          window.SoccerDemo.setDifficulty(difficulty);
        } else if (action === 'rotation-toggle') {
          if (moodRotateTimer) window.SoccerDemo.disableMoodRotation({ manual: true });
          else window.SoccerDemo.enableMoodRotation(20, { manual: true });
        } else if (action === 'clear-startle') {
          clearStartleCooldowns();
        } else if (action === 'collapse') {
          setMoodDebugCollapsed(moodDebugPanel.dataset.collapsed !== 'true', true);
        } else if (action === 'hide') {
          setMoodDebugVisible(false, true);
        }
        updateMoodDebugPanel();
      }

      moodDebugPanel?.querySelectorAll('button').forEach((btn) => {
        if (btn.dataset.debugMood || btn.dataset.debugDifficulty || btn.dataset.debugAction === 'rotation-toggle') {
          btn.disabled = !soccerTestEnabled;
          btn.setAttribute('aria-disabled', String(!soccerTestEnabled));
        }
        btn.addEventListener('pointerdown', (e) => {
          e.preventDefault();
          e.stopPropagation();
        });
        btn.addEventListener('click', (e) => {
          e.preventDefault();
          e.stopPropagation();
          handleMoodDebugButton(btn);
        });
      });

      window.SoccerDemoDebug = {
        show(persist = true) { return setMoodDebugVisible(true, persist); },
        hide(persist = true) { return setMoodDebugVisible(false, persist); },
        collapse(persist = true) { return setMoodDebugCollapsed(true, persist); },
        expand(persist = true) { return setMoodDebugCollapsed(false, persist); },
        toggle(persist = true) {
          return setMoodDebugVisible(moodDebugPanel?.dataset.debugVisible !== 'true', persist);
        },
        mood(name) {
          if (!soccerTestEnabled) return getMoodDebugSnapshot();
          window.SoccerDemo.setMood(name, { manual: true });
          updateMoodDebugPanel();
          return getMoodDebugSnapshot();
        },
        difficulty(name) {
          if (!soccerTestEnabled) return getMoodDebugSnapshot();
          window.SoccerDemo.setDifficulty(name);
          updateMoodDebugPanel();
          return getMoodDebugSnapshot();
        },
        rotation(enabled = true) {
          if (!soccerTestEnabled) return getMoodDebugSnapshot();
          if (enabled) window.SoccerDemo.enableMoodRotation(20, { manual: true });
          else window.SoccerDemo.disableMoodRotation({ manual: true });
          updateMoodDebugPanel();
          return getMoodDebugSnapshot();
        },
        clearStartleCooldowns() { clearStartleCooldowns(); return getMoodDebugSnapshot(); },
        snapshot: getMoodDebugSnapshot,
        log() {
          const snapshot = getMoodDebugSnapshot();
          if (console.table) console.table(snapshot);
          else console.log(snapshot);
          return snapshot;
        },
      };
      updateMoodDebugPanel();

      // 正常 LLM 模式下不自动轮换心情；随机轮换仅保留给 debug 和纯游戏兜底。

      // ═══════════════════════════════════════════════════════════════════════════
      //  LLM 接入（A+B 双簧模式）
      //  A：后端 OmniOfflineClient 决策，生成台词 + 控制指令
      //  B：前端将台词显示为气泡，控制指令应用到游戏状态
      // ═══════════════════════════════════════════════════════════════════════════
      const _llm = {
        preGameContext: null,
        preGameContextSource: '',
        preGameContextError: '',
        pendingOpeningLine: '',
        moodRotationFallbackEnabled: false,
        pending: false,
        cleanedUp: true,
        pendingItems: [],
        flushQueued: false,
        maxPendingItems: 10,
        // 需要 LLM 生成台词的事件类型（高优先级游戏事件）
        llmKinds: new Set([
          'goal-scored', 'goal-conceded',
          'own-goal-by-ai', 'own-goal-by-player',
          'steal', 'stolen',
        ]),
        // 节流：同一 kind 的 LLM 调用间隔（秒）
        cooldowns: {},
        cooldownSec: 8,
        // 局中 Realtime 只作 STT；赛后统一注入摘要，避免频繁 session.update 影响 VAD/STT。
        gameStarted: false,
        gameStartedAt: 0,
        gameStartedAtEpochMs: 0,
        gameMemoryTailCount: null,
        soccerGameMemoryEnabled: false,
        loggedExternalInputKeys: new Set(),
        speechPlaybackState: null,
        voiceArbiter: {
          pending: null,
          inFlight: null,
          timer: null,
          seq: 0,
          waitingForUserReplyGeneration: false,
          userReplyProtectedUntil: 0,
        },
      };

      function _shouldUsePureGameMoodRotationFallback() {
        const source = String(_llm.preGameContextSource || '').trim().toLowerCase();
        const error = String(_llm.preGameContextError || '').trim();
        return source === 'fallback' || !!error;
      }
      function _syncMoodRotationPolicy(reason = 'policy') {
        _llm.moodRotationFallbackEnabled = _shouldUsePureGameMoodRotationFallback();
        if (moodDebugMode) {
          SoccerDemo.disableMoodRotation({ force: true });
        } else if (_llm.moodRotationFallbackEnabled) {
          SoccerDemo.enableMoodRotation(20);
        } else {
          SoccerDemo.disableMoodRotation();
        }
        try {
          window.SoccerDemoDebugLog?.(
            'info',
            'game_state',
            'mood_rotation_policy',
            '足球小游戏随机心情策略',
            {
              enabled: _llm.moodRotationFallbackEnabled,
              reason,
              preGameContextSource: _llm.preGameContextSource || '',
              preGameContextError: _llm.preGameContextError || '',
            },
          );
          console.log(
            `[SoccerMoodRotation] ${_llm.moodRotationFallbackEnabled ? 'enabled' : 'disabled'} | ` +
            `reason=${reason} source=${_llm.preGameContextSource || '-'} error=${_llm.preGameContextError || '-'}`
          );
        } catch (_) {}
      }

      const _soccerSpeechPlaybackLogState = {
        lastSignature: '',
        lastLoggedAt: 0,
        lastHeartbeatAt: 0,
      };
      const SOCCER_SESSION_DEBUG_ENABLE_TIMEOUT_MS = 3500;
      soccerGame.logger.configure({
        enableTimeoutMs: SOCCER_SESSION_DEBUG_ENABLE_TIMEOUT_MS,
      });

      function resetSoccerSessionDebugLogEnableState() {
        soccerGame.logger.reset();
      }
      function _enableSoccerSessionDebugLogAfterRouteStart() {
        return soccerGame.logger.enableAfterRuntimeStart();
      }
      function enableSoccerSessionDebugLog(reason = 'keyboard') {
        return soccerGame.logger.enable(reason);
      }
      function soccerSessionDebugLog(level, category, event, message, details = {}, sensitivePossible = false, options = {}) {
        try {
          soccerGame.logger.log(level, category, event, message, details, sensitivePossible, options);
        } catch (_) {}
      }
      window.SoccerDemoDebugLog = soccerSessionDebugLog;
      window.EnableSoccerSessionDebugLog = enableSoccerSessionDebugLog;

      const passiveGuard = {
        surrenderReminderEnabled: true,
        ordinaryDisabledForCurrentGame: false,
        ordinaryModalShownThisGame: false,
        restModalShownThisGame: false,
        restDismissedThisGame: false,
        modalOpen: false,
        modalType: '',
        modalLineToken: 0,
        lv4PlayerGoalStreak: 0,
        withdrawnRestGoalStreak: 0,
        ordinaryLightHintSent: false,
        ordinarySidecar7Called: false,
        ordinarySidecar8Called: false,
        ordinaryRescueSent: false,
        restLightHintSent: false,
        restSidecar7Called: false,
        restSidecar8Called: false,
        teachingLv4PlayerGoalStreak: 0,
        teachingLv3GoalWindow: [],
        teachingLv3Promoted: false,
        teachingLv2Promoted: false,
        lastDifficultyChange: null,
        sidecarGeneration: 0,
      };

      let surrenderReminderPreference = true;

      async function _loadSurrenderReminderEnabled() {
        if (!soccerGame.capabilities.has('storage')) return surrenderReminderPreference;
        try {
          const response = await soccerGame.storage.get(SURRENDER_REMINDER_STORAGE_KEY);
          const stored = response.data || {};
          if (response.ok && stored.found === true) {
            surrenderReminderPreference = stored.value !== false;
          }
        } catch (_) {
          // Keep the in-memory default when optional storage is unavailable.
        }
        return surrenderReminderPreference;
      }

      function _readSurrenderReminderEnabled() {
        return surrenderReminderPreference;
      }

      function _writeSurrenderReminderEnabled(enabled) {
        surrenderReminderPreference = enabled !== false;
        if (!soccerGame.capabilities.has('storage')) return;
        void soccerGame.storage.set(SURRENDER_REMINDER_STORAGE_KEY, surrenderReminderPreference).catch((error) => {
          soccerRecoverableLog('[SoccerSettings] 认输提醒设置保存失败:', error);
        });
      }

      function _setSurrenderReminderEnabled(enabled, { persist = true, source = 'ui' } = {}) {
        const next = enabled !== false;
        passiveGuard.surrenderReminderEnabled = next;
        if (surrenderReminderToggle) surrenderReminderToggle.checked = next;
        if (persist) _writeSurrenderReminderEnabled(next);
        if (next) {
          passiveGuard.ordinaryDisabledForCurrentGame = false;
          passiveGuard.ordinaryModalShownThisGame = false;
        } else {
          passiveGuard.ordinaryDisabledForCurrentGame = true;
        }
        _passiveGuardDebugLog('Modal', 'passive_guard_modal', '设置认输提醒', {
          action: 'set_surrender_reminder',
          enabled: next,
          source,
          persist,
        });
        console.log(`[Soccer] [PassiveGuard] [Modal] 设置认输提醒 | 来源=${source} 开启=${next ? '是' : '否'} 本场普通功能=${passiveGuard.ordinaryDisabledForCurrentGame ? '停用' : '启用'}`);
      }

      function _resetPassiveGuardForNewGame() {
        const reminderEnabled = _readSurrenderReminderEnabled();
        const nextSidecarGeneration = Number(passiveGuard.sidecarGeneration || 0) + 1;
        Object.assign(passiveGuard, {
          surrenderReminderEnabled: reminderEnabled,
          ordinaryDisabledForCurrentGame: !reminderEnabled,
          ordinaryModalShownThisGame: false,
          restModalShownThisGame: false,
          restDismissedThisGame: false,
          modalOpen: false,
          modalType: '',
          modalLineToken: 0,
          lv4PlayerGoalStreak: 0,
          withdrawnRestGoalStreak: 0,
          ordinaryLightHintSent: false,
          ordinarySidecar7Called: false,
          ordinarySidecar8Called: false,
          ordinaryRescueSent: false,
          restLightHintSent: false,
          restSidecar7Called: false,
          restSidecar8Called: false,
          teachingLv4PlayerGoalStreak: 0,
          teachingLv3GoalWindow: [],
          teachingLv3Promoted: false,
          teachingLv2Promoted: false,
          lastDifficultyChange: null,
          sidecarGeneration: nextSidecarGeneration,
        });
        if (surrenderReminderToggle) surrenderReminderToggle.checked = reminderEnabled;
        _hideExitPrompt();
        _passiveGuardDebugLog('Counter', 'passive_guard_counter', '新局重置', {
          reason: 'new_game_reset',
          reminderEnabled,
        });
        console.log(`[Soccer] [PassiveGuard] [Counter] 新局重置 | 认输提醒=${reminderEnabled ? '开启' : '关闭'}`);
      }

      function _pregameStance() {
        return String(_llm.preGameContext?.gameStance || 'neutral_play');
      }

      function _isTeachingStance() {
        return _pregameStance() === 'teaching';
      }

      function _isWithdrawnStance() {
        return _pregameStance() === 'withdrawn';
      }

      function _isPunishingLikeStance() {
        return _pregameStance() === 'punishing';
      }

      function _scoreDiffPlayerLead() {
        return Number(state.score.player || 0) - Number(state.score.ai || 0);
      }

      function _currentPassiveSnapshot() {
        return {
          reminderEnabled: passiveGuard.surrenderReminderEnabled,
          ordinaryDisabledForCurrentGame: passiveGuard.ordinaryDisabledForCurrentGame,
          ordinaryModalShownThisGame: passiveGuard.ordinaryModalShownThisGame,
          restModalShownThisGame: passiveGuard.restModalShownThisGame,
          lv4PlayerGoalStreak: passiveGuard.lv4PlayerGoalStreak,
          withdrawnRestGoalStreak: passiveGuard.withdrawnRestGoalStreak,
          teachingLv4PlayerGoalStreak: passiveGuard.teachingLv4PlayerGoalStreak,
          teachingLv3GoalWindow: passiveGuard.teachingLv3GoalWindow.slice(-5),
          lastDifficultyChange: passiveGuard.lastDifficultyChange,
        };
      }

      function _passiveGuardDebugLog(section, event, message, details = {}, level = 'info') {
        soccerSessionDebugLog(level, 'passive_guard', event, message, {
          section,
          round: state.round,
          score: {
            player: state.score.player,
            ai: state.score.ai,
            diffPlayerLead: _scoreDiffPlayerLead(),
          },
          mood: moodKey,
          difficulty: DIFFICULTY[difficultyIdx]?.name || '',
          passiveGuard: _currentPassiveSnapshot(),
          ...details,
        }, false, { preserveDetails: true });
      }

      function _passiveGuardLogCounter(reason, extra = {}) {
        _passiveGuardDebugLog('Counter', 'passive_guard_counter', reason, {
          reason,
          stage: extra.stage || '',
          triggerReason: extra.reason || '',
        });
        console.log(
          `[Soccer] [PassiveGuard] [Counter] ${reason} | 回合=${state.round} 比分=${state.score.player}:${state.score.ai} ` +
          `难度=${DIFFICULTY[difficultyIdx]?.name || '未知'} 普通连续=${passiveGuard.lv4PlayerGoalStreak} ` +
          `rest连续=${passiveGuard.withdrawnRestGoalStreak} teaching连续=${passiveGuard.teachingLv4PlayerGoalStreak} ` +
          `阶段=${extra.stage || '无'} 原因=${extra.reason || '无'}`
        );
      }

      function _makePassiveHintItem(kind, textRaw, priority = 7) {
        const snapshot = SoccerDemo._snapshot();
        return {
          type: 'game_event',
          kind,
          label: eventLabel(kind),
          textRaw,
          mood: snapshot.mood,
          priority,
          builtinFallback: '',
          snapshot,
          round: snapshot.round,
          ts: performance.now(),
        };
      }

      function _sendPassiveHint(kind, textRaw, priority = 7) {
        _passiveGuardDebugLog('Hint', 'passive_guard_hint', '发送 PassiveGuard 提示', {
          kind,
          label: eventLabel(kind),
          priority,
          textLength: String(textRaw || '').length,
        });
        console.log(`[Soccer] [PassiveGuard] [Hint] 发送提示 | 类型=${eventLabel(kind)}(${kind}) 回合=${state.round} 比分=${state.score.player}:${state.score.ai} 难度=${DIFFICULTY[difficultyIdx]?.name || '未知'} 目标=${textRaw}`);
        _enqueueLLMItem(_makePassiveHintItem(kind, textRaw, priority));
      }

      function _clearOrdinaryCandidate(reason) {
        passiveGuard.lv4PlayerGoalStreak = 0;
        passiveGuard.ordinaryLightHintSent = false;
        passiveGuard.ordinarySidecar7Called = false;
        passiveGuard.ordinarySidecar8Called = false;
        passiveGuard.ordinaryRescueSent = false;
        _passiveGuardLogCounter('普通候选清零', { reason });
      }

      function _clearRestCandidate(reason) {
        passiveGuard.withdrawnRestGoalStreak = 0;
        passiveGuard.restLightHintSent = false;
        passiveGuard.restSidecar7Called = false;
        passiveGuard.restSidecar8Called = false;
        _passiveGuardLogCounter('rest候选清零', { reason });
      }

      function _isPassiveGuardSidecarCurrent(sessionId, generation) {
        return String(sessionId || '') === String(_runtimeSessionId() || '') &&
          Number(generation || 0) === Number(passiveGuard.sidecarGeneration || 0);
      }

      function _passiveGuardExitPromptCandidateState(promptType, stage, options = {}) {
        const difficulty = DIFFICULTY[difficultyIdx]?.name || '';
        const requiredStage = Math.max(0, Number(stage || 0));
        const allowPreparedModal = options.allowPreparedModal === true;
        const scoreLead = _scoreDiffPlayerLead();
        if (promptType === 'rest') {
          const streak = Number(passiveGuard.withdrawnRestGoalStreak || 0);
          if (!_isWithdrawnStance()) return { active: false, reason: 'not_withdrawn_stance', difficulty, streak, scoreLead };
          if (difficulty !== 'lv4') return { active: false, reason: 'left_lv4', difficulty, streak, scoreLead };
          if (!passiveGuard.surrenderReminderEnabled) return { active: false, reason: 'surrender_reminder_disabled', difficulty, streak, scoreLead };
          if (passiveGuard.restDismissedThisGame) return { active: false, reason: 'rest_dismissed_this_game', difficulty, streak, scoreLead };
          if (passiveGuard.restModalShownThisGame && !allowPreparedModal) return { active: false, reason: 'rest_modal_shown_this_game', difficulty, streak, scoreLead };
          if (streak < requiredStage) return { active: false, reason: 'rest_streak_below_stage', difficulty, streak, scoreLead };
          return { active: true, reason: '', difficulty, streak, scoreLead };
        }
        const streak = Number(passiveGuard.lv4PlayerGoalStreak || 0);
        if (_isTeachingStance()) return { active: false, reason: 'teaching_stance', difficulty, streak, scoreLead };
        if (_isWithdrawnStance()) return { active: false, reason: 'withdrawn_stance', difficulty, streak, scoreLead };
        if (_isPunishingLikeStance()) return { active: false, reason: 'punishing_stance', difficulty, streak, scoreLead };
        if (difficulty !== 'lv4') return { active: false, reason: 'left_lv4', difficulty, streak, scoreLead };
        if (!passiveGuard.surrenderReminderEnabled) return { active: false, reason: 'surrender_reminder_disabled', difficulty, streak, scoreLead };
        if (passiveGuard.ordinaryDisabledForCurrentGame) return { active: false, reason: 'ordinary_disabled_for_current_game', difficulty, streak, scoreLead };
        if (passiveGuard.ordinaryModalShownThisGame && !allowPreparedModal) return { active: false, reason: 'ordinary_modal_shown_this_game', difficulty, streak, scoreLead };
        if (streak < requiredStage && scoreLead < 10) return { active: false, reason: 'ordinary_candidate_below_stage', difficulty, streak, scoreLead };
        return { active: true, reason: '', difficulty, streak, scoreLead };
      }

      function _isExitPromptOpen() {
        return passiveGuard.modalOpen;
      }

      function _setExitPromptButtons(type) {
        const isRest = type === 'rest';
        if (exitPromptContinueButton) {
          exitPromptContinueButton.textContent = isRest
            ? _i18n('exitPrompt.stayLonger', '再陪一会')
            : _i18n('exitPrompt.continuePlay', '继续玩');
          exitPromptContinueButton.classList.toggle('primary', !isRest);
        }
        if (exitPromptEndButton) {
          exitPromptEndButton.textContent = isRest
            ? _i18n('exitPrompt.rest', '休息')
            : _i18n('exitPrompt.endGame', '结束游戏');
          exitPromptEndButton.classList.toggle('secondary-muted', !isRest);
        }
        if (exitPromptNeverRow) exitPromptNeverRow.hidden = isRest;
        if (exitPromptNeverNote) exitPromptNeverNote.hidden = isRest;
        if (exitPromptNeverAgain) exitPromptNeverAgain.checked = false;
      }

      function _showExitPrompt(type, line, { fallback = false } = {}) {
        if (!exitPromptOverlay || !exitPromptLine) return;
        passiveGuard.modalOpen = true;
        passiveGuard.modalType = type;
        _setExitPromptButtons(type);
        const fallbackLine = type === 'rest'
          ? _i18n('exitPrompt.restFallback', '喵...有点累了，想先安静一会。')
          : _i18n('exitPrompt.surrenderFallback', '喵..认输喵...');
        exitPromptLine.textContent = line || fallbackLine;
        exitPromptOverlay.hidden = false;
        _passiveGuardDebugLog('Modal', 'passive_guard_modal', '显示退出提示窗口', {
          action: 'show',
          type,
          fallback,
          lineLength: String(line || '').length,
        });
        console.log(`[Soccer] [PassiveGuard] [Modal] 显示窗口 | 类型=${type === 'rest' ? '休息(rest)' : '认输(surrender)'} fallback=${fallback ? '是' : '否'} 回合=${state.round} 比分=${state.score.player}:${state.score.ai}`);
      }

      function _hideExitPrompt() {
        if (passiveGuard.modalOpen) {
          _passiveGuardDebugLog('Modal', 'passive_guard_modal', '隐藏退出提示窗口', {
            action: 'hide',
            type: passiveGuard.modalType || '',
          });
        }
        passiveGuard.modalOpen = false;
        passiveGuard.modalType = '';
        if (exitPromptOverlay) exitPromptOverlay.hidden = true;
      }

      function _releasePreparedExitPrompt(type) {
        if (type === 'rest') passiveGuard.restModalShownThisGame = false;
        else passiveGuard.ordinaryModalShownThisGame = false;
      }

      function _deliverExitPromptLine(line, type, result = {}, meta = {}) {
        const clean = String(line || '').trim();
        if (!clean) return;
        const kind = type === 'rest' ? 'passive-rest-hint' : 'passive-surrender-hint';
        const displayMeta = {
          kind,
          round: state.round,
          priority: 9,
          voicePriority: 0,
          itemCount: 1,
          hasUserSpeech: false,
          hasUserText: false,
          ...meta,
        };
        SoccerDemo.say(clean, {
          priority: 9,
          kind: `${kind}-llm`,
          sourceLabel: '退出提示',
        });
        void _mirrorGameAssistantText(clean, displayMeta, result);
        void _enqueueGameVoice(clean, displayMeta, result, performance.now());
        _passiveGuardDebugLog('Modal', 'passive_guard_modal', '展示退出提示台词', {
          action: 'deliver_line',
          type,
          kind,
          lineLength: clean.length,
          fallback: !!result?.fallback,
          reason: result?.reason || '',
        });
      }

      async function _requestExitPromptLine(type) {
        const snapshot = SoccerDemo._snapshot();
        const kind = type === 'rest' ? 'passive-rest-hint' : 'passive-surrender-hint';
        const eventPayload = {
          kind,
          round: snapshot.round,
          mood: snapshot.mood,
          textRaw: type === 'rest' ? '休息提示台词请求' : '认输提示台词请求',
          exitPromptRequest: {
            type,
            instruction: type === 'rest'
              ? '请生成一句低落/退缩场景下想休息或停下的猫娘台词，不要标题，不要控制 JSON。'
              : '请生成一句猫娘认输或服软的短台词，不要标题，不要控制 JSON。',
          },
          score: snapshot.score,
          scoreDiff: Number(snapshot.score?.ai || 0) - Number(snapshot.score?.player || 0),
          difficulty: snapshot.difficulty,
          aiMode: snapshot.aiMode,
          requestControlReason: false,
          currentState: snapshot,
          passiveGuard: _currentPassiveSnapshot(),
          preGameContext: _llm.preGameContext || null,
          pendingItems: [],
        };
        const resp = await soccerGame.dialogue.request({
          ..._soccerGameMemoryPolicyPayload(),
          ..._conversationLanguagePayload(),
          event: {
            ...eventPayload,
            ..._soccerGameMemoryPolicyPayload(),
          },
        }, { timeoutMs: EXIT_PROMPT_LINE_WAIT_MS });
        if (!resp.ok) return { line: '', result: { fallback: true, reason: `HTTP ${resp.status}` } };
        const data = resp.data || {};
        return { line: String(data.line || '').trim(), result: data };
      }

      async function _prepareExitPrompt(type, reason = '', { stage = 8 } = {}) {
        if (type === 'surrender') {
          if (!passiveGuard.surrenderReminderEnabled) {
            passiveGuard.ordinaryDisabledForCurrentGame = true;
            _passiveGuardDebugLog('Modal', 'passive_guard_modal', '跳过普通认输窗口', {
              action: 'skip',
              type,
              reason: 'surrender_reminder_disabled',
            });
            console.log(`[Soccer] [PassiveGuard] [Modal] 跳过普通认输窗口 | 原因=认输提醒关闭 处理=按继续游戏`);
            return;
          }
          if (passiveGuard.ordinaryDisabledForCurrentGame || passiveGuard.ordinaryModalShownThisGame) {
            _passiveGuardDebugLog('Modal', 'passive_guard_modal', '跳过普通认输窗口', {
              action: 'skip',
              type,
              reason: passiveGuard.ordinaryDisabledForCurrentGame ? 'ordinary_disabled_for_current_game' : 'ordinary_modal_shown_this_game',
            });
            return;
          }
          passiveGuard.ordinaryModalShownThisGame = true;
        } else if (type === 'rest') {
          if (!passiveGuard.surrenderReminderEnabled) {
            passiveGuard.restDismissedThisGame = true;
            _passiveGuardDebugLog('Modal', 'passive_guard_modal', '跳过休息窗口', {
              action: 'skip',
              type,
              reason: 'surrender_reminder_disabled',
            });
            console.log(`[Soccer] [PassiveGuard] [Modal] 跳过休息窗口 | 原因=认输/休息提醒关闭 处理=按继续游戏`);
            return;
          }
          if (passiveGuard.restModalShownThisGame || passiveGuard.restDismissedThisGame) {
            _passiveGuardDebugLog('Modal', 'passive_guard_modal', '跳过休息窗口', {
              action: 'skip',
              type,
              reason: passiveGuard.restDismissedThisGame ? 'rest_dismissed_this_game' : 'rest_modal_shown_this_game',
            });
            return;
          }
          passiveGuard.restModalShownThisGame = true;
        }
        const token = ++passiveGuard.modalLineToken;
        const fallbackLine = type === 'rest'
          ? _i18n('exitPrompt.restFallback', '喵...有点累了，想先安静一会。')
          : _i18n('exitPrompt.surrenderFallback', '喵..认输喵...');
        _passiveGuardDebugLog('Modal', 'passive_guard_modal', '准备退出提示窗口', {
          action: 'prepare',
          type,
          reason,
          waitMs: EXIT_PROMPT_LINE_WAIT_MS,
          token,
        });
        console.log(`[Soccer] [PassiveGuard] [Modal] 准备窗口 | 类型=${type} 原因=${reason || '无'} 等待=${EXIT_PROMPT_LINE_WAIT_MS}ms`);
        const linePromise = _requestExitPromptLine(type).catch((e) => ({
          line: '',
          result: { fallback: true, reason: String(e) },
        }));
        const timeoutPromise = new Promise((resolve) => setTimeout(() => resolve({ line: '', timeout: true }), EXIT_PROMPT_LINE_WAIT_MS));
        const first = await Promise.race([linePromise, timeoutPromise]);
        if (token !== passiveGuard.modalLineToken) return;
        if (_llm.cleanedUp || !isGameRuntimeReady()) {
          _releasePreparedExitPrompt(type);
          _passiveGuardDebugLog('Modal', 'passive_guard_modal', '跳过退出提示窗口，会话已清理', {
            action: 'skip_cleaned_up_before_show',
            type,
            stage,
            cleanedUp: _llm.cleanedUp,
          });
          console.log(`[Soccer] [PassiveGuard] [Modal] 跳过窗口 | 类型=${type} 阶段=${stage} 原因=会话已清理`);
          return;
        }
        const promptType = type === 'rest' ? 'rest' : 'surrender';
        const candidate = _passiveGuardExitPromptCandidateState(promptType, stage, { allowPreparedModal: true });
        if (!candidate.active) {
          _releasePreparedExitPrompt(type);
          _passiveGuardDebugLog('Modal', 'passive_guard_modal', '跳过退出提示窗口，候选已失效', {
            action: 'skip_inactive_candidate_before_show',
            type,
            stage,
            reason: candidate.reason,
            candidate,
          });
          console.log(`[Soccer] [PassiveGuard] [Modal] 跳过窗口 | 类型=${type} 阶段=${stage} 原因=${candidate.reason || '候选已失效'}`);
          return;
        }
        const firstLine = String(first?.line || '').trim();
        if (!firstLine) {
          _recordFallbackDiagnostic(type === 'rest' ? '休息提示台词生成' : '认输提示台词生成', {
            fallback: '使用固定台词兜底',
            reason: first?.timeout ? 'timeout' : (first?.result?.reason || 'empty_line'),
            key: `exit-prompt-line:${type}:${first?.timeout ? 'timeout' : (first?.result?.reason || 'empty')}`,
          });
        }
        const showedFallbackLine = !firstLine;
        _showExitPrompt(type, firstLine, { fallback: showedFallbackLine });
        if (firstLine) {
          _deliverExitPromptLine(firstLine, type, first.result || {});
        }
        linePromise.then((late) => {
          const lateLine = String(late?.line || '').trim();
          if (!lateLine) return;
          if (
            token !== passiveGuard.modalLineToken ||
            _llm.cleanedUp ||
            !isGameRuntimeReady() ||
            !passiveGuard.modalOpen ||
            passiveGuard.modalType !== type
          ) {
            _passiveGuardDebugLog('Modal', 'passive_guard_modal', '丢弃迟到退出提示台词', {
              action: 'discard_late_line',
              type,
              token,
              lineLength: lateLine.length,
              cleanedUp: _llm.cleanedUp,
            });
            console.log(`[Soccer] [PassiveGuard] [Modal] 丢弃迟到台词 | 类型=${type} 台词="${lateLine}"`);
            return;
          }
          if (exitPromptLine && (exitPromptLine.textContent === fallbackLine || showedFallbackLine)) {
            exitPromptLine.textContent = lateLine;
            _deliverExitPromptLine(lateLine, type, late.result || {});
            _passiveGuardDebugLog('Modal', 'passive_guard_modal', '迟到台词替换 fallback', {
              action: 'replace_fallback_with_late_line',
              type,
              token,
              lineLength: lateLine.length,
            });
            console.log(`[Soccer] [PassiveGuard] [Modal] 迟到台词替换fallback | 类型=${type}`);
          }
        });
      }

      async function _requestPassiveGuardSidecar(stage, trigger = {}, extra = {}) {
        const requestSessionId = _runtimeSessionId();
        const requestGeneration = passiveGuard.sidecarGeneration;
        const body = {
          session_id: requestSessionId,
          ...(_runtimeCharacterName() ? { lanlan_name: _runtimeCharacterName() } : {}),
          ..._conversationLanguagePayload(),
          currentState: SoccerDemo._snapshot(),
          preGameContext: _llm.preGameContext || null,
          passiveGuardState: _currentPassiveSnapshot(),
          stage,
          trigger,
          ...extra,
        };
        const started = performance.now();
        _passiveGuardDebugLog('Sidecar', 'passive_guard_sidecar', '请求 PassiveGuard sidecar', {
          action: 'request',
          stage,
          promptType: extra.promptType || '',
          triggerType: trigger.type || '',
          triggerKind: trigger.kind || '',
          triggerSide: trigger.side || '',
          streak: trigger.streak || 0,
          scoreLead: trigger.scoreLead || 0,
          userSpeechLength: String(extra.userSpeech || '').length,
          timeoutMs: PASSIVE_GUARD_SIDE_CAR_TIMEOUT_MS,
        });
        try {
          const resp = await soccerHost.evaluatePassiveGuard(body, {
            timeoutMs: PASSIVE_GUARD_SIDE_CAR_TIMEOUT_MS,
          });
          const data = await resp.json().catch(() => ({}));
          if (!_isPassiveGuardSidecarCurrent(requestSessionId, requestGeneration)) {
            _passiveGuardDebugLog('Sidecar', 'passive_guard_sidecar', '丢弃过期 PassiveGuard sidecar 结果', {
              action: 'discard_stale_result',
              stage,
              promptType: extra.promptType || '',
              requestSessionId,
              currentSessionId: _runtimeSessionId(),
              requestGeneration,
              currentGeneration: passiveGuard.sidecarGeneration,
              elapsedMs: Math.round(performance.now() - started),
            }, 'warning');
            console.log(`[Soccer] [PassiveGuard] [Sidecar] 丢弃过期结果 | 阶段=${stage} 请求局=${requestSessionId} 当前局=${_runtimeSessionId()}`);
            return { recommendedAction: 'observe_more', exitPromptType: 'none', reasonForDebug: 'stale_sidecar_result' };
          }
          if (!resp.ok || data.ok === false) {
            _recordFallbackDiagnostic('PassiveGuard 判定', {
              fallback: '继续观察兜底',
              reason: data.reason || `HTTP ${resp.status}`,
              key: `passive-guard:${stage}:${data.reason || resp.status}`,
              details: { stage, promptType: extra.promptType || '', httpStatus: resp.status },
            });
            _passiveGuardDebugLog('Sidecar', 'passive_guard_sidecar', 'PassiveGuard sidecar 降级', {
              action: 'degrade',
              stage,
              promptType: extra.promptType || '',
              httpStatus: resp.status,
              reason: data.reason || `HTTP ${resp.status}`,
              elapsedMs: Math.round(performance.now() - started),
            }, 'warning');
            console.log(`[Soccer] [PassiveGuard] [Sidecar] 降级 | 阶段=${stage} 原因=${data.reason || resp.status}`);
            return { recommendedAction: 'observe_more', exitPromptType: 'none', reasonForDebug: data.reason || `HTTP ${resp.status}` };
          }
          _passiveGuardDebugLog('Sidecar', 'passive_guard_sidecar', 'PassiveGuard sidecar 返回结果', {
            action: 'result',
            stage,
            promptType: extra.promptType || '',
            elapsedMs: Math.round(performance.now() - started),
            classification: data.classification || '',
            recommendedAction: data.recommendedAction || 'observe_more',
            exitPromptType: data.exitPromptType || 'none',
            reasonForDebug: data.reasonForDebug || '',
          });
          console.log(
            `[Soccer] [PassiveGuard] [Sidecar] 结果 | 阶段=${stage} 用时=${(performance.now() - started).toFixed(0)}ms ` +
            `分类=${data.classification || '无'} 推荐动作=${data.recommendedAction || 'observe_more'} 退出类型=${data.exitPromptType || 'none'} 原因=${data.reasonForDebug || '无'}`
          );
          return data;
        } catch (e) {
          if (!_isPassiveGuardSidecarCurrent(requestSessionId, requestGeneration)) {
            _passiveGuardDebugLog('Sidecar', 'passive_guard_sidecar', '丢弃过期 PassiveGuard sidecar 异常', {
              action: 'discard_stale_error',
              stage,
              promptType: extra.promptType || '',
              requestSessionId,
              currentSessionId: _runtimeSessionId(),
              requestGeneration,
              currentGeneration: passiveGuard.sidecarGeneration,
              elapsedMs: Math.round(performance.now() - started),
              error: String(e),
            }, 'warning');
            return { recommendedAction: 'observe_more', exitPromptType: 'none', reasonForDebug: 'stale_sidecar_error' };
          }
          _recordFallbackDiagnostic('PassiveGuard 判定', {
            fallback: '继续观察兜底',
            reason: String(e),
            key: `passive-guard:${stage}:request_failed`,
            details: { stage, promptType: extra.promptType || '' },
          });
          _passiveGuardDebugLog('Sidecar', 'passive_guard_sidecar', 'PassiveGuard sidecar 请求失败', {
            action: 'request_failed',
            stage,
            promptType: extra.promptType || '',
            elapsedMs: Math.round(performance.now() - started),
            error: String(e),
          }, 'warning');
          console.log(`[Soccer] [PassiveGuard] [Sidecar] 降级 | 阶段=${stage} 原因=${String(e)}`);
          return { recommendedAction: 'observe_more', exitPromptType: 'none', reasonForDebug: 'request_failed' };
        }
      }

      function _handleSidecarAction(result, { stage, promptType }) {
        const action = String(result?.recommendedAction || 'observe_more');
        _passiveGuardDebugLog('Sidecar', 'passive_guard_sidecar', '处理 PassiveGuard sidecar 动作', {
          action: 'handle_action',
          stage,
          promptType,
          recommendedAction: action,
          exitPromptType: String(result?.exitPromptType || ''),
          reasonForDebug: String(result?.reasonForDebug || ''),
        });
        if (action === 'cancel_candidate') {
          if (promptType === 'rest') _clearRestCandidate('sidecar_cancel_candidate');
          else _clearOrdinaryCandidate('sidecar_cancel_candidate');
          return;
        }
        if (action === 'send_rescue_hint') {
          if (promptType === 'rest') {
            _sendPassiveHint('send-rescue-hint', '猫娘开局状态低落，主人连续进球较多；请温和回应当前陪玩状态，不要强行认输。', 7);
          } else if (!passiveGuard.ordinaryRescueSent) {
            passiveGuard.ordinaryRescueSent = true;
            _sendPassiveHint('send-rescue-hint', '玩家在 lv4 后连续进球较多；如果猫娘不是故意放弃，请自然调整状态，稍微加把劲继续玩。', 7);
          }
          return;
        }
        if (action === 'prepare_exit_prompt' && stage >= 8) {
          const exitType = String(result?.exitPromptType || promptType);
          const candidate = _passiveGuardExitPromptCandidateState(promptType, stage);
          if (!candidate.active) {
            _passiveGuardDebugLog('Sidecar', 'passive_guard_sidecar', '跳过 PassiveGuard 弹窗，候选已失效', {
              action: 'skip_inactive_candidate',
              stage,
              promptType,
              exitPromptType: exitType,
              reason: candidate.reason,
              candidate,
            });
            console.log(`[Soccer] [PassiveGuard] [Sidecar] 跳过弹窗 | 阶段=${stage} 类型=${promptType} 原因=${candidate.reason || '候选已失效'}`);
            return;
          }
          if (exitType === 'rest' && promptType === 'rest') void _prepareExitPrompt('rest', 'sidecar_prepare_exit_prompt', { stage });
          else if (exitType === 'surrender' && promptType === 'surrender') void _prepareExitPrompt('surrender', 'sidecar_prepare_exit_prompt', { stage });
        }
      }

      function _handleTeachingGoal(side, difficulty) {
        if (!_isTeachingStance()) return false;
        if (side === 'ai' || difficulty !== 'lv4') {
          passiveGuard.teachingLv4PlayerGoalStreak = 0;
        }
        if (side === 'player' && difficulty === 'lv4' && !passiveGuard.teachingLv3Promoted) {
          passiveGuard.teachingLv4PlayerGoalStreak++;
          _passiveGuardLogCounter('教学lv4进球计数', { stage: passiveGuard.teachingLv4PlayerGoalStreak, reason: 'player_goal' });
          if (passiveGuard.teachingLv4PlayerGoalStreak >= 3) {
            passiveGuard.teachingLv3Promoted = true;
            passiveGuard.teachingLv4PlayerGoalStreak = 0;
            const changed = SoccerDemo.setDifficulty('lv3', {
              source: 'passive-guard-teaching',
              reason: 'teaching_progress_lv4_to_lv3',
            });
            _sendPassiveHint('teaching-progress-hint', '主人已经连续进球，应该算是熟练了；现在规则层已把教学难度从 lv4 提升到 lv3，请自然说明并稍微认真一点。', 7);
            _passiveGuardDebugLog('Hint', 'passive_guard_teaching', '教学升档', {
              from: 'lv4',
              to: 'lv3',
              changed,
              reason: 'teaching_progress_lv4_to_lv3',
            });
            console.log(`[Soccer] [PassiveGuard] [Hint] 教学升档 | lv4->lv3 changed=${changed ? '是' : '否'}`);
          }
          return true;
        }
        if (side === 'player' && difficulty === 'lv3' && !passiveGuard.teachingLv2Promoted) {
          passiveGuard.teachingLv3GoalWindow.push('player');
          passiveGuard.teachingLv3GoalWindow = passiveGuard.teachingLv3GoalWindow.slice(-5);
          const playerGoals = passiveGuard.teachingLv3GoalWindow.filter(x => x === 'player').length;
          if (playerGoals >= 3) {
            passiveGuard.teachingLv2Promoted = true;
            passiveGuard.teachingLv3GoalWindow = [];
            const changed = SoccerDemo.setDifficulty('lv2', {
              source: 'passive-guard-teaching',
              reason: 'teaching_progress_lv3_to_lv2',
            });
            _sendPassiveHint('teaching-progress-hint', '主人在最近几个进球里已经能稳定得分；现在规则层已把教学难度从 lv3 提升到 lv2，请自然说明并再认真一点。', 7);
            _passiveGuardDebugLog('Hint', 'passive_guard_teaching', '教学升档', {
              from: 'lv3',
              to: 'lv2',
              changed,
              reason: 'teaching_progress_lv3_to_lv2',
            });
            console.log(`[Soccer] [PassiveGuard] [Hint] 教学升档 | lv3->lv2 changed=${changed ? '是' : '否'}`);
          }
          return true;
        }
        if (side === 'ai' && difficulty === 'lv3') {
          passiveGuard.teachingLv3GoalWindow.push('ai');
          passiveGuard.teachingLv3GoalWindow = passiveGuard.teachingLv3GoalWindow.slice(-5);
        }
        return true;
      }

      function _handleWithdrawnGoal(side, difficulty, kind) {
        if (!_isWithdrawnStance()) return false;
        if (side === 'ai' || difficulty !== 'lv4') {
          _clearRestCandidate(side === 'ai' ? 'ai_goal' : 'left_lv4');
          return true;
        }
        if (side !== 'player') return true;
        if (!passiveGuard.surrenderReminderEnabled) {
          if (
            passiveGuard.withdrawnRestGoalStreak > 0 ||
            passiveGuard.restLightHintSent ||
            passiveGuard.restSidecar7Called ||
            passiveGuard.restSidecar8Called
          ) {
            _clearRestCandidate('surrender_reminder_disabled');
          }
          _passiveGuardDebugLog('Modal', 'passive_guard_modal', '休息提醒已关闭', {
            action: 'skip',
            type: 'rest',
            reason: 'surrender_reminder_disabled',
            triggerKind: kind,
          });
          console.log(`[Soccer] [PassiveGuard] [Modal] 休息提醒已关闭 | 处理=不累计rest候选`);
          return true;
        }
        if (passiveGuard.restDismissedThisGame) return true;
        passiveGuard.withdrawnRestGoalStreak++;
        const streak = passiveGuard.withdrawnRestGoalStreak;
        _passiveGuardLogCounter('withdrawn rest进球计数', { stage: streak, reason: kind });
        if (streak >= 5 && !passiveGuard.restLightHintSent) {
          passiveGuard.restLightHintSent = true;
          _sendPassiveHint('light-balance-hint', '主人已经连续进球，猫娘当前开局状态低落；请根据陪玩状态自然回应，看看是否需要放慢、回应关心或调整陪玩方式。', 6);
        }
        if (streak >= 7 && !passiveGuard.restSidecar7Called) {
          passiveGuard.restSidecar7Called = true;
          void _requestPassiveGuardSidecar(7, { type: 'withdrawn_rest_streak', kind, side, streak }, { promptType: 'rest' })
            .then((result) => _handleSidecarAction(result, { stage: 7, promptType: 'rest' }));
        }
        if (streak >= 8 && !passiveGuard.restSidecar8Called) {
          passiveGuard.restSidecar8Called = true;
          void _requestPassiveGuardSidecar(8, { type: 'withdrawn_rest_streak', kind, side, streak }, { promptType: 'rest' })
            .then((result) => _handleSidecarAction(result, { stage: 8, promptType: 'rest' }));
        }
        return true;
      }

      function _handleOrdinaryGoal(side, difficulty, kind) {
        if (_isTeachingStance() || _isWithdrawnStance() || _isPunishingLikeStance()) return;
        if (side === 'ai' || difficulty !== 'lv4') {
          _clearOrdinaryCandidate(side === 'ai' ? 'ai_goal' : 'left_lv4');
          return;
        }
        if (side !== 'player') return;
        passiveGuard.lv4PlayerGoalStreak++;
        const streak = passiveGuard.lv4PlayerGoalStreak;
        const scoreLead = _scoreDiffPlayerLead();
        _passiveGuardLogCounter('普通lv4进球计数', { stage: streak, reason: kind });
        if (!passiveGuard.surrenderReminderEnabled || passiveGuard.ordinaryDisabledForCurrentGame) {
          if (streak >= 5 || scoreLead >= 10) {
            passiveGuard.ordinaryDisabledForCurrentGame = true;
            _passiveGuardDebugLog('Modal', 'passive_guard_modal', '普通认输提醒已关闭', {
              action: 'skip',
              type: 'surrender',
              reason: 'surrender_reminder_disabled_or_current_game_disabled',
              streak,
              scoreLead,
            });
            console.log(`[Soccer] [PassiveGuard] [Modal] 普通认输提醒已关闭 | 处理=按继续游戏 分差=${scoreLead}`);
          }
          return;
        }
        if (streak >= 5 && !passiveGuard.ordinaryLightHintSent) {
          passiveGuard.ordinaryLightHintSent = true;
          _sendPassiveHint('light-balance-hint', '玩家在 lv4 后连续进球较多；请自然回应当前局势，不要强行把疑似摆烂解释成只是休息。', 6);
        }
        if (streak >= 7 && !passiveGuard.ordinarySidecar7Called) {
          passiveGuard.ordinarySidecar7Called = true;
          void _requestPassiveGuardSidecar(7, { type: 'ordinary_lv4_streak', kind, side, streak, scoreLead }, { promptType: 'surrender' })
            .then((result) => _handleSidecarAction(result, { stage: 7, promptType: 'surrender' }));
        }
        if ((streak >= 8 || scoreLead >= 10) && !passiveGuard.ordinarySidecar8Called) {
          passiveGuard.ordinarySidecar8Called = true;
          void _requestPassiveGuardSidecar(8, { type: scoreLead >= 10 ? 'ordinary_score_diff' : 'ordinary_lv4_streak', kind, side, streak, scoreLead }, { promptType: 'surrender' })
            .then((result) => _handleSidecarAction(result, { stage: 8, promptType: 'surrender' }));
        }
      }

      function _handlePassiveGuardGoal(side, kind) {
        const difficulty = DIFFICULTY[difficultyIdx]?.name || '';
        if (_handleTeachingGoal(side, difficulty)) return;
        if (_handleWithdrawnGoal(side, difficulty, kind)) return;
        _handleOrdinaryGoal(side, difficulty, kind);
      }

      function _handlePassiveGuardUserSpeech(text, source = '') {
        const clean = String(text || '').trim();
        if (!clean || passiveGuard.modalOpen) return;
        if (_isWithdrawnStance() && !passiveGuard.restDismissedThisGame && passiveGuard.withdrawnRestGoalStreak > 0) {
          void _requestPassiveGuardSidecar('user_soothing', {
            type: 'withdrawn_user_speech',
            source,
            textLength: clean.length,
          }, {
            promptType: 'rest',
            userSpeech: clean.slice(0, 240),
          }).then((result) => {
            if (String(result?.recommendedAction || '') === 'cancel_candidate') {
              _clearRestCandidate('user_soothing_cancelled');
            }
          });
          return;
        }
        if (
          !_isTeachingStance()
          && !_isPunishingLikeStance()
          && !passiveGuard.ordinaryDisabledForCurrentGame
          && passiveGuard.lv4PlayerGoalStreak > 0
        ) {
          void _requestPassiveGuardSidecar('user_recovery', {
            type: 'ordinary_user_speech',
            source,
            textLength: clean.length,
          }, {
            promptType: 'surrender',
            userSpeech: clean.slice(0, 240),
          }).then((result) => {
            if (String(result?.recommendedAction || '') === 'cancel_candidate') {
              _clearOrdinaryCandidate('user_recovery_cancelled');
            }
          });
        }
      }

      window.__SoccerPassiveGuardRecordDifficulty = (from, to, opts = {}) => {
        passiveGuard.lastDifficultyChange = {
          from,
          to,
          source: opts.source || 'unknown',
          reason: opts.reason || '',
          round: state.round,
          ts: Date.now(),
        };
        if (from === 'lv4' && to !== 'lv4') {
          _clearOrdinaryCandidate('difficulty_left_lv4');
          _clearRestCandidate('difficulty_left_lv4');
        }
      };
      function _soccerGameMemoryPolicyPayload(enabled = _isGameMemoryEnabled()) {
        return {
          soccerGameMemoryEnabled: enabled,
          soccer_game_memory_enabled: enabled,
          soccerGameMemoryPlayerInteractionEnabled: enabled,
          soccer_game_memory_player_interaction_enabled: enabled,
          soccerGameMemoryEventReplyEnabled: enabled,
          soccer_game_memory_event_reply_enabled: enabled,
          soccerGameMemoryArchiveEnabled: enabled,
          soccer_game_memory_archive_enabled: enabled,
          soccerGameMemoryPostgameContextEnabled: enabled,
          soccer_game_memory_postgame_context_enabled: enabled,
          gameMemoryEnabled: enabled,
          game_memory_enabled: enabled,
        };
      }
      gameMemoryToggle?.addEventListener('change', () => {
        _llm.soccerGameMemoryEnabled = _isGameMemoryEnabled();
        console.log(`[SoccerMemory] 本局进入记忆=${_llm.soccerGameMemoryEnabled ? 'on' : 'off'}`);
      });
      const GAME_ROUTE_HEARTBEAT_FETCH_TIMEOUT_MS = 4500;
      const ACCIDENTAL_GAME_ENTRY_GRACE_MS = 10000;
      const DEFAULT_GAME_MEMORY_TAIL_COUNT = 6;
      const MAX_GAME_MEMORY_TAIL_COUNT = 50;
      const GAME_VOICE_ARBITER_DEFAULTS = {
        tailWaitSeconds: 0.8,
        freshTtlSeconds: 2.0,
        userReplyFreshTtlSeconds: 5.0,
        inFlightGuardSeconds: 2.0,
        userReplyGuardMinSeconds: 2.4,
        userReplyGuardMaxSeconds: 10.0,
        userReplySecondsPerChar: 0.18,
      };

      function _resetGameFieldForStartScreen() {
        playerCharging = false;
        playerCharge = 0;
        state.score.player = 0;
        state.score.ai = 0;
        state.round = 1;
        state.flashTimer = 0;
        state.flashSide = null;
        _outOfBoundsTimer = 0;
        _outOfBoundsSide = null;
        resetPositions();

        aiKickCd = 0;
        aiWindupRemaining = 0;
        aiWindupAim = null;
        aiWindupTotal = 0;
        ballGhostSec = 0;
        lastTouchSide = null;
        lastPlayerKickAtMs = 0;
        playerKickWallBounceForStartle = false;
        startleDirectCdSec = 0;
        startleGrazeCdSec = 0;
        startleMutualLockSec = 0;
        zoneoutCooldown = 3 + Math.random() * 2;
        aiMode = 'attack';
        aiReactSec = 0;
        aiTargetCache = { x: state.ball.x, y: state.ball.y };
        aiFreezeSec = 0;
        aiRetreatSec = 0;
        aiRetreatTarget = null;
        ballPosHistory = [];
        unstickCd = 0;
        GAME_EVENTS.length = 0;

        difficultyIdx = DEFAULT_DIFFICULTY_INDEX >= 0 ? DEFAULT_DIFFICULTY_INDEX : 1;
        startScreenDifficultyOverridden = false;
        __setMoodBase('calm', { force: true });
        _syncMoodRotationPolicy('reset-start-screen');
        for (const key of Object.keys(SPEECH_CD)) delete SPEECH_CD[key];
        Object.assign(speechState, {
          ballInOppHalfSec: 0,
          playerIdleSec: 0,
          closeProximitySec: 0,
          lastBallTouchTime: performance.now(),
          lastGoalTime: performance.now(),
          scoreAgeAccum: 0,
          lastFreezeKind: null,
        });
        SoccerDemo.clearBubble();
        clearPlayerSpeechBubble();
        soccerGameAudio.sync('reset-start-screen');
      }

      function _resetGameRouteRuntime({ active = false, newSession = false } = {}) {
        soccerGame.runtime.reset({ newSession });
        _llm.preGameContext = null;
        _llm.preGameContextSource = '';
        _llm.preGameContextError = '';
        _llm.pendingOpeningLine = '';
        _llm.moodRotationFallbackEnabled = false;
        _llm.pending = false;
        _llm.cleanedUp = !active;
        _llm.pendingItems = [];
        _llm.flushQueued = false;
        _llm.cooldowns = {};
        _llm.gameStarted = false;
        _llm.gameStartedAt = 0;
        _llm.gameStartedAtEpochMs = 0;
        _startedAsMaxAngry = false;
        _openingMaxAngryBgmActive = false;
        _llm.gameMemoryTailCount = null;
        _llm.soccerGameMemoryEnabled = _isGameMemoryEnabled();
        _llm.lastVoiceFailure = null;
        _llm.loggedExternalInputKeys.clear();
          _llm.voiceArbiter.pending = null;
          _llm.voiceArbiter.inFlight = null;
          _llm.voiceArbiter.waitingForUserReplyGeneration = false;
          _llm.voiceArbiter.userReplyProtectedUntil = 0;
          if (_llm.voiceArbiter.timer) {
            clearTimeout(_llm.voiceArbiter.timer);
            _llm.voiceArbiter.timer = null;
          }
      }

      function _readSpeechPlaybackState() {
        try {
          return soccerGame.speech.getState();
        } catch (_) {
          return {
            active: false,
            speechId: '',
            turnId: '',
            remainingSeconds: 0,
            updatedAt: 0,
            priority: null,
            reason: 'missing',
          };
        }
      }

      function _speechPlaybackDebugDetails(raw, bridgeSource = '') {
        const updatedAt = Number(raw?.updatedAt || 0);
        return {
          bridgeSource,
          reason: raw?.reason || '',
          active: !!raw?.active,
          speechId: raw?.speechId || '',
          turnId: raw?.turnId || '',
          playbackTurnId: raw?.playbackTurnId || '',
          remainingSeconds: Number(raw?.remainingSeconds || 0),
          pendingAudioWork: !!raw?.pendingAudioWork,
          audioContextState: raw?.audioContextState || '',
          audioContextTime: Number(raw?.audioContextTime || 0),
          scheduledEndAudioTime: Number(raw?.scheduledEndAudioTime || 0),
          playbackStartAudioTime: Number(raw?.playbackStartAudioTime || 0),
          playbackEndAudioTime: Number(raw?.playbackEndAudioTime || 0),
          updatedAt,
          updatedAtIso: updatedAt ? new Date(updatedAt).toISOString() : '',
          source: raw?.source || '',
        };
      }

      function _logSpeechPlaybackState(raw, bridgeSource = '') {
        if (!raw || typeof raw !== 'object') return;
        const details = _speechPlaybackDebugDetails(raw, bridgeSource);
        const now = Date.now();
        const roundedRemaining = Math.round(details.remainingSeconds * 10) / 10;
        const signature = [
          details.reason,
          details.active,
          details.speechId,
          details.turnId,
          details.playbackTurnId,
          details.audioContextState,
          roundedRemaining,
        ].join('|');
        const important = details.reason && details.reason !== 'heartbeat';
        if (!important) {
          if (
            signature === _soccerSpeechPlaybackLogState.lastSignature &&
            now - _soccerSpeechPlaybackLogState.lastHeartbeatAt < 2500
          ) {
            return;
          }
          _soccerSpeechPlaybackLogState.lastHeartbeatAt = now;
        } else if (
          signature === _soccerSpeechPlaybackLogState.lastSignature &&
          now - _soccerSpeechPlaybackLogState.lastLoggedAt < 500
        ) {
          return;
        }
        _soccerSpeechPlaybackLogState.lastSignature = signature;
        _soccerSpeechPlaybackLogState.lastLoggedAt = now;
        soccerSessionDebugLog(
          'info',
          'speech',
          'speech_playback_state',
          '主说话播放器状态更新',
          details,
          false,
          { preserveDetails: true },
        );
      }

      const unsubscribeSpeechPlaybackState = soccerGame.speech.onState((data) => {
        _llm.speechPlaybackState = data;
        _logSpeechPlaybackState(data, data.transportSource || 'sdk');
      });
      const unsubscribeSpeechPlaybackError = soccerGame.speech.onError((error) => {
        console.warn(
          `[SoccerVoice][Arbiter] 播放状态桥接不可用 | source=${error.source || 'sdk'} ` +
          `code=${error.code || 'request_failed'}: ${error.message || ''}`,
        );
      });
      const initialSpeechPlaybackState = _readSpeechPlaybackState();
      _llm.speechPlaybackState = initialSpeechPlaybackState;
      if (initialSpeechPlaybackState.updatedAt) {
        _logSpeechPlaybackState(
          initialSpeechPlaybackState,
          initialSpeechPlaybackState.transportSource || 'sdk-initial',
        );
      }

      function _gameStartedElapsedMs() {
        if (!_llm.gameStarted || !Number.isFinite(Number(_llm.gameStartedAt)) || Number(_llm.gameStartedAt) <= 0) {
          return 0;
        }
        return Math.max(0, performance.now() - Number(_llm.gameStartedAt));
      }

      function _isAccidentalGameEntryExit() {
        if (!_llm.gameStarted) return true;
        return _gameStartedElapsedMs() < ACCIDENTAL_GAME_ENTRY_GRACE_MS;
      }

      function _gameRoutePayload(extra = {}) {
        const visibilityState = document.visibilityState || 'visible';
        const pageVisible = typeof document.hidden === 'boolean'
          ? !document.hidden
          : visibilityState === 'visible';
        const gameStartedElapsedMs = Math.round(_gameStartedElapsedMs());
        const tailCount = Number.isFinite(Number(_llm.gameMemoryTailCount))
          ? Math.max(1, Math.min(Math.floor(Number(_llm.gameMemoryTailCount)), MAX_GAME_MEMORY_TAIL_COUNT))
          : DEFAULT_GAME_MEMORY_TAIL_COUNT;
        const soccerGameMemoryEnabled = _isGameMemoryEnabled();
        _llm.soccerGameMemoryEnabled = soccerGameMemoryEnabled;
        const routeLanlanName = _runtimeCharacterName() || _soccerConversationCharacterName();
        return {
          session_id: _runtimeSessionId(),
          ...(routeLanlanName ? { lanlan_name: routeLanlanName } : {}),
          currentState: SoccerDemo._snapshot(),
          pageVisible,
          visibilityState,
          gameMemoryTailCount: tailCount,
          game_memory_tail_count: tailCount,
          ..._soccerGameMemoryPolicyPayload(soccerGameMemoryEnabled),
          gameStarted: !!_llm.gameStarted,
          game_started: !!_llm.gameStarted,
          gameStartedElapsedMs,
          game_started_elapsed_ms: gameStartedElapsedMs,
          gameStartedAtEpochMs: Math.round(Number(_llm.gameStartedAtEpochMs || 0)),
          // 显式角色偏好与 render-only 兜底分开发送；后端只允许前者更新
          // mgr.user_language，后者仅用于当前请求的模板选择。
          ..._conversationLanguagePayload(),
          ...extra,
        };
      }

      async function _sendGameRouteHeartbeat(force = false) {
        if (_llm.cleanedUp) return;
        return soccerGame.runtime.pulse(force);
      }

      soccerGame.events.on('runtime-inactive', ({ payload: data }) => {
        console.warn('[SoccerRoute] 心跳发现后端路由已结束:', data?.reason || 'inactive');
        _setVoiceStatus(_i18n('voiceStatus.routeEnded', '游戏路由已结束，刷新页面可重新接管'));
        _renderGameVoiceChatControl({ available: false, reason: 'route_ended' });
      });

      soccerGame.events.on('runtime-error', ({ payload = {} }) => {
        if (payload.operation === 'heartbeat') {
          if (payload.data) {
            soccerRecoverableLog('[SoccerRoute] 心跳异常:', payload.data.reason || payload.status);
          } else {
            soccerRecoverableLog('[SoccerRoute] 心跳请求失败:', payload.reason);
          }
          return;
        }
        if (payload.operation === 'drain') {
          soccerRecoverableLog('[SoccerRoute] 外部输入结果拉取失败:', payload.reason || payload.status || payload.data);
        }
      });

      soccerGame.events.on('page-exit', () => {
        settingsUiAbortController.abort();
        window.removeEventListener('localechange', _refreshGameVoiceChatLocale);
        unsubscribeSpeechPlaybackState();
        unsubscribeSpeechPlaybackError();
        soccerGameAudio.destroy();
        _prepareGameLLMSessionCleanup();
      });

        async function _startGameRoute() {
          try {
            window.__SoccerLoading?.beginStart?.(_i18n('loading.beginStartDefault', '分析开局上下文…'));
            resetSoccerSessionDebugLogEnableState();
            await ensureSoccerCharacterInfo();
            const consent = await soccerGame.memory.configureConsent(_isGameMemoryEnabled());
            if (!consent.ok || consent.data?.ok === false) throw new Error('memory_consent_failed');
            const resp = await soccerGame.runtime.start(_gameRoutePayload(_gameRouteStartOptions));
            const data = resp.data || {};
            if (data.ok) {
              await _enableSoccerSessionDebugLogAfterRouteStart();
              if (_runtimeCharacterName()) {
                window.__SoccerResolvedLanlanName = _runtimeCharacterName();
              }
              console.log('[SoccerRoute] 已接管主语音入口/主聊天窗:', data.state);
              const context = await soccerGame.context.read(['pregame-context']);
              if (!context.ok || context.data?.ok === false) throw new Error('pregame_context_read_failed');
              _applyPreGameContext({
                ...data.state,
                preGameContext: context.data?.scopes?.['pregame-context'],
                pre_game_context_source: context.data?.scope_metadata?.['pregame-context']?.source,
                pre_game_context_error: context.data?.scope_metadata?.['pregame-context']?.error,
              });
              window.__SoccerLoading?.done('route', _i18n('loading.routeDone', '开局上下文准备完成'));
              return true;
            } else {
              _recordFallbackDiagnostic('开局路由接管', {
                fallback: '取消本次启动，清理 route 后允许重试',
                reason: data.reason || data.error || 'route_start_failed',
                key: 'route-start-failed',
              });
              soccerRecoverableLog('[SoccerRoute] 接管主入口失败:', data);
              throw new Error(data.reason || 'route_start_failed');
            }
          } catch (e) {
            _recordFallbackDiagnostic('开局路由接管', {
              fallback: '取消本次启动，清理 route 后允许重试',
              reason: String(e),
              key: 'route-start-request-failed',
            });
            soccerRecoverableLog('[SoccerRoute] 接管主入口请求失败:', e);
            throw e;
          }
        }

      function _scoreDiffOf(snapshot) {
        const score = snapshot?.score || { player: 0, ai: 0 };
        return Number(score.ai || 0) - Number(score.player || 0);
      }

      function _setVoiceStatus(text) {
        if (voiceStatusEl) voiceStatusEl.textContent = text;
      }

      function _formatSourceLabel(source) {
        if (!source) return 'unknown';
        if (typeof source === 'string') return source;
        if (source.label) return source.label;
        const parts = [];
        if (source.provider) parts.push(source.provider);
        if (source.model) parts.push(source.model);
        if (source.method) parts.push(`method=${source.method}`);
        if (source.voiceName) parts.push(`voice=${source.voiceName}`);
        if (source.lang) parts.push(`lang=${source.lang}`);
        return parts.join(' / ') || 'unknown';
      }

      function _readGameRouteStartOptions() {
        const raw = window.NEKOSoccerLaunchOptions || window.SoccerDemoLaunchOptions || {};
        const nekoInitiated = raw.nekoInitiated === true;
        const nekoInviteText = nekoInitiated ? String(raw.nekoInviteText || '').trim().slice(0, 120) : '';
        const rawTailCount = raw.gameMemoryTailCount ?? raw.game_memory_tail_count ?? raw.tailCount ?? raw.tail_count;
        const parsedTailCount = Number(rawTailCount);
        const gameMemoryTailCount = Number.isFinite(parsedTailCount)
          ? Math.max(1, Math.min(Math.floor(parsedTailCount), MAX_GAME_MEMORY_TAIL_COUNT))
          : DEFAULT_GAME_MEMORY_TAIL_COUNT;
        const rawMemoryEnabled = (
          raw.soccerGameMemoryEnabled
          ?? raw.soccer_game_memory_enabled
          ?? raw.gameMemoryEnabled
          ?? raw.game_memory_enabled
          ?? raw.memoryEnabled
        );
        const rawMemoryText = String(rawMemoryEnabled ?? '').trim().toLowerCase();
        const soccerGameMemoryEnabled = rawMemoryEnabled === undefined
          ? _isGameMemoryEnabled()
          : !(rawMemoryEnabled === false || ['0', 'false', 'no', 'off'].includes(rawMemoryText));
        _llm.gameMemoryTailCount = gameMemoryTailCount;
        _llm.soccerGameMemoryEnabled = soccerGameMemoryEnabled;
        if (gameMemoryToggle) gameMemoryToggle.checked = soccerGameMemoryEnabled;
        return {
          nekoInitiated,
          nekoInviteText,
          gameMemoryTailCount,
          game_memory_tail_count: gameMemoryTailCount,
          ..._soccerGameMemoryPolicyPayload(soccerGameMemoryEnabled),
        };
      }

      function _isValidSoccerMood(value) {
        return SoccerDemo.MOODS.includes(value);
      }

      function _isValidSoccerDifficulty(value) {
        return SoccerDemo.DIFFICULTIES.includes(value);
      }

      function _applyPreGameContext(routeState = {}) {
        const ctx = routeState?.preGameContext && typeof routeState.preGameContext === 'object'
          ? routeState.preGameContext
          : null;
        _llm.preGameContext = ctx;
        _llm.preGameContextSource = String(routeState?.pre_game_context_source || '');
        _llm.preGameContextError = String(routeState?.pre_game_context_error || '');
        _syncMoodRotationPolicy('pregame-context');

        if (ctx?.initialMood && _isValidSoccerMood(ctx.initialMood)) {
          SoccerDemo.setMood(ctx.initialMood, { source: 'pregame', force: true });
        }
        if (
          !startScreenDifficultyOverridden &&
          ctx?.initialDifficulty &&
          _isValidSoccerDifficulty(ctx.initialDifficulty)
        ) {
          SoccerDemo.setDifficulty(ctx.initialDifficulty, { source: 'pregame' });
        }

        console.log(
          `[SoccerRoute] 开局上下文 | stance=${ctx?.gameStance || 'none'} ` +
          `source=${_llm.preGameContextSource || '-'} error=${_llm.preGameContextError || '-'} ` +
          `mood=${SoccerDemo.getMood()} difficulty=${SoccerDemo.getDifficulty()} ` +
          `rotation=${_llm.moodRotationFallbackEnabled ? 'fallback-enabled' : 'disabled'}`
        );
        if (_llm.preGameContextSource === 'fallback' || _llm.preGameContextError) {
          _recordFallbackDiagnostic('开局上下文分析', {
            fallback: '使用普通陪玩开局兜底',
            reason: _llm.preGameContextError || _llm.preGameContextSource,
            key: `pregame-context:${_llm.preGameContextSource}:${_llm.preGameContextError}`,
            details: {
              preGameContextSource: _llm.preGameContextSource,
              preGameContextError: _llm.preGameContextError,
            },
          });
        } else if (_llm.preGameContextSource === 'fallback_empty_history') {
          _recordFallbackDiagnostic('开局近期记录读取', {
            fallback: '使用空历史继续分析',
            reason: _llm.preGameContextSource,
            key: 'pregame-recent-history-empty',
            details: {
              preGameContextSource: _llm.preGameContextSource,
            },
          });
        }

        const openingLine = String(ctx?.openingLine || '').trim();
        if (openingLine) {
          _llm.pendingOpeningLine = openingLine;
          if (isGameRuntimeReady()) {
            _deliverPendingOpeningLine();
          }
        }
      }

      function _deliverPendingOpeningLine() {
        const openingLine = String(_llm.pendingOpeningLine || '').trim();
        _llm.pendingOpeningLine = '';
        if (openingLine) void _deliverOpeningLine(openingLine);
      }

      function _deliverOpeningLine(line) {
        const clean = String(line || '').trim();
        if (!clean) return;
        if (clean.length > 15) {
          console.warn('[SoccerRoute] 开场白超过 15 字，跳过:', clean);
          return;
        }
        const eventTs = performance.now();
        const snapshot = SoccerDemo._snapshot();
        const meta = {
          kind: 'opening-line',
          round: snapshot.round,
          priority: 1,
          voicePriority: 1,
          itemCount: 1,
          hasUserSpeech: false,
          hasUserText: false,
          source: 'pre_game_context',
        };
        const result = {
          line: clean,
          control: {},
          llm_source: { provider: 'pregame_context', method: _llm.preGameContextSource || 'unknown' },
        };
        const bubbleShown = SoccerDemo.say(clean, {
          priority: 1,
          kind: 'opening-line',
          sourceLabel: '开局上下文',
        });
        void _mirrorGameAssistantText(clean, meta, result);
        void _enqueueGameVoice(clean, meta, result, eventTs);
        console.log(`[SoccerRoute] 开场白 | 气泡=${bubbleShown ? '已显示' : '被保留'} 台词="${clean}"`);
      }

      function _makeMailboxItemFromSpeak(p) {
        const snapshot = SoccerDemo._snapshot();
        return {
          type: 'game_event',
          kind: p.kind,
          label: eventLabel(p.kind),
          textRaw: p.textRaw,
          mood: p.mood,
          priority: p.priority || 0,
          builtinFallback: p.builtinFallback || '',
          snapshot,
          round: p.round,
          ts: p.ts || performance.now(),
        };
      }

      function _makeMailboxItemFromUserSpeech(text) {
        const clean = String(text || '').trim();
        if (!clean) return null;
        const snapshot = SoccerDemo._snapshot();
        return {
          type: 'user_speech',
          kind: 'user-voice',
          label: '玩家语音',
          textRaw: clean,
          mood: snapshot.mood,
          priority: 8,
          snapshot,
          round: snapshot.round,
          ts: performance.now(),
        };
      }

      function _compactMailboxItems(items) {
        // 代码层不做“求饶/挑衅/撒娇”这类语义总结；只做证据保全和机械限流。
        // 玩家语音保留原文与当时快照，游戏事件在过多时保留最近记录和计数摘要。
        if (items.length <= _llm.maxPendingItems) return items;
        const preserved = [];
        const counts = Object.create(null);
        for (const item of items) {
          if (item.type === 'user_speech') preserved.push(item);
          else counts[item.kind] = (counts[item.kind] || 0) + 1;
        }
        const recent = items.slice(-Math.max(4, _llm.maxPendingItems - preserved.length));
        const counted = Object.entries(counts).map(([kind, count]) => ({
          type: 'game_event_count',
          kind,
          label: eventLabel(kind),
          count,
        }));
        return [...preserved.slice(-4), ...recent, ...counted].slice(-_llm.maxPendingItems);
      }

      function _buildMailboxEvent(items, currentState) {
        const first = items[0] || {};
        const score = currentState.score || { player: 0, ai: 0 };
        const base = {
          kind: items.length === 1 ? first.kind : 'mailbox-batch',
          round: currentState.round,
          mood: currentState.mood,
          textRaw: items.length === 1 ? first.textRaw : `mailbox:${items.length}`,
          score,
          scoreDiff: _scoreDiffOf(currentState),
          difficulty: currentState.difficulty,
          aiMode: currentState.aiMode,
          requestControlReason: REQUEST_CONTROL_REASON,
          currentState,
          pendingItems: items,
        };
        if (first.type === 'user_speech') {
          base.userSpeech = first.textRaw;
          base.source = 'voice_input_gate';
        }
        return base;
      }

      async function _mirrorGameAssistantText(line, meta = {}, result = {}) {
        const clean = String(line || '').trim();
        if (!clean || meta?.textAlreadyMirrored) return null;
        const requestId = meta.request_id || result.request_id || `game-llm-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
        const shouldFinalizeTurn = !!(meta.hasUserSpeech || meta.hasUserText || meta.kind === 'user-voice' || meta.kind === 'user-text');
        try {
          const resp = await soccerGame.speech.mirror({
            text: clean,
            source: 'game-llm-result',
            requestId,
            turnId: `game-mirror-${requestId}`,
            finalizeTurn: shouldFinalizeTurn,
            event: {
              kind: meta.kind || 'mailbox',
              round: meta.round,
              priority: meta.priority || 0,
              itemCount: meta.itemCount || 1,
              hasUserSpeech: !!meta.hasUserSpeech,
              hasUserText: !!meta.hasUserText,
              // 关闭“本局进入记忆”后，玩家输入、NEKO直接回应、游戏事件回应、赛后归档和续接上下文都不会写入/引用记忆。
              ..._soccerGameMemoryPolicyPayload(),
              voiceAlreadyHandled: !!meta.voiceAlreadyHandled,
              fallback: !!meta.fallback,
              llmSource: result.llm_source || null,
            },
          });
          const data = resp.data || {};
          if (!data.ok) {
            console.log(`[SoccerMirror] 主聊天窗镜像失败 | 原因=${data.reason || resp.status} 台词="${clean}"`);
          } else {
            console.log(
              `[SoccerMirror] 主聊天窗镜像成功 | 回合=${meta?.round ?? '?'} ` +
              `事件=${eventLabel(meta?.kind)}(${meta?.kind || 'mailbox'}) ` +
              `来源=${meta?.hasUserSpeech || meta?.hasUserText ? 'user_reply' : 'game_event'} 台词="${clean}"`
            );
          }
          return data;
        } catch (e) {
          soccerRecoverableLog('[SoccerMirror] 主聊天窗镜像请求失败:', e);
          return { ok: false, reason: 'request_failed', error: String(e) };
        }
      }

      async function _sendGameSpeech(line, meta = {}, result = {}, options = {}) {
        if (!line) {
          return {
            ok: false,
            reason: 'missing_line',
            voice_source: { provider: 'project_voice_unavailable' },
          };
        }
        if (!voiceOutputToggle?.checked) {
          return {
            ok: false,
            reason: 'voice_output_disabled',
            skipped: true,
            voice_source: { provider: 'project_voice_disabled' },
          };
        }
        _llm.lastVoiceFailure = null;
        const interruptAudio = options.interruptAudio === true;
        const voiceArbiterReason = String(options.reason || '');
        const languagePayload = _conversationLanguagePayload();
        try {
          const resp = await soccerGame.speech.speak({
            text: line,
            source: 'game-llm-result',
            requestId: meta.request_id || result.request_id || '',
            eventKey: meta.kind || 'mailbox',
            priority: Number.isFinite(Number(meta.priority)) ? Number(meta.priority) : 4,
            mirrorText: false,
            emitTurnEnd: false,
            interruptExisting: interruptAudio,
            reuseSynthesizedAudio: !_isUserReplyVoiceMeta(meta),
            relativeGain: _soccerVoicePlaybackGain(),
            reason: voiceArbiterReason,
            language: languagePayload.i18n_language || '',
            renderLanguage: languagePayload.render_language || '',
            event: {
              kind: meta.kind || 'mailbox',
              round: meta.round,
              priority: meta.priority || 0,
              itemCount: meta.itemCount || 1,
              hasUserSpeech: !!meta.hasUserSpeech,
              hasUserText: !!meta.hasUserText,
              // 关闭“本局进入记忆”后，TTS turn-end 也会被后端按足球游戏记忆策略处理。
              ..._soccerGameMemoryPolicyPayload(),
              voiceArbiterReason,
              interruptAudio,
            },
          });
          const data = resp.data || {};
          if (data.ok) {
            data.voice_source = data.voice_source || {
              provider: 'project_tts',
              model: data.method || 'unknown',
              method: data.method || 'unknown',
              lang: data.language || '',
            };
            console.log(
              `[SoccerVoice] 原流水线输出成功 | 方式=${data.method || 'unknown'} ` +
              `语言=${data.language || '-'} 来源=${_formatSourceLabel(data.voice_source)} ` +
              `speech_id=${data.speech_id || '-'} audio_sent=${!!data.audio_sent} ` +
              `cache=${data.cache_status || 'disabled'} ` +
              `interrupt_audio=${interruptAudio} ` +
              `audio_committed=${!!data.audio_committed} response_observed=${!!data.response_observed} ` +
              `audio_observed=${!!data.audio_observed} response_done=${!!data.response_done} ` +
              `line_match=${data.line_match !== undefined ? !!data.line_match : '-'} ` +
              `transcript="${(data.spoken_transcript || '').slice(0, 80)}" bytes=${data.bytes || 0}`
            );
            soccerSessionDebugLog(
              'info',
              'speech',
              'project_voice_result',
              '小游戏项目语音返回成功',
              {
                ok: data.ok,
                method: data.method || 'unknown',
                language: data.language || '',
                speech_id: data.speech_id || '',
                audio_sent: data.audio_sent,
                audio_queued: data.audio_queued,
                cache_status: data.cache_status,
                audio_committed: data.audio_committed,
                response_observed: data.response_observed,
                audio_observed: data.audio_observed,
                response_done: data.response_done,
                line_match: data.line_match,
                bytes: data.bytes,
                voice_source: data.voice_source,
                tts_pipeline: data.tts_pipeline,
                interruptAudio,
                voiceArbiterReason,
                request_id: meta.request_id || result.request_id || '',
                event_kind: meta.kind || 'mailbox',
                round: meta.round,
              },
              false,
              { preserveDetails: true },
            );
            return data;
          }
          const skipDiag = [
            data.audio_sent !== undefined ? `audio_sent=${!!data.audio_sent}` : '',
            data.audio_committed !== undefined ? `audio_committed=${!!data.audio_committed}` : '',
            data.response_observed !== undefined ? `response_observed=${!!data.response_observed}` : '',
            data.audio_observed !== undefined ? `audio_observed=${!!data.audio_observed}` : '',
            data.response_done !== undefined ? `response_done=${!!data.response_done}` : '',
            data.line_match !== undefined ? `line_match=${!!data.line_match}` : '',
            data.use_tts !== undefined ? `use_tts=${!!data.use_tts}` : '',
          ].filter(Boolean).join(' ');
          console.log(
            `[SoccerVoice] 原流水线输出未送达 | 原因=${data.reason || resp.status} ` +
            `来源=${_formatSourceLabel(data.voice_source || { provider: 'project_voice_unavailable' })} ${skipDiag}`
          );
          soccerSessionDebugLog(
            'warning',
            'speech',
            'project_voice_result',
            '小游戏项目语音未送达',
            {
              ok: data.ok,
              status: resp.status,
              reason: data.reason || '',
              audio_sent: data.audio_sent,
              audio_queued: data.audio_queued,
              cache_status: data.cache_status,
              audio_committed: data.audio_committed,
              response_observed: data.response_observed,
              audio_observed: data.audio_observed,
              response_done: data.response_done,
              line_match: data.line_match,
              use_tts: data.use_tts,
              voice_source: data.voice_source || { provider: 'project_voice_unavailable' },
              tts_pipeline: data.tts_pipeline,
              error_type: data.error_type,
              error: data.error,
              interruptAudio,
              voiceArbiterReason,
              request_id: meta.request_id || result.request_id || '',
              event_kind: meta.kind || 'mailbox',
              round: meta.round,
            },
            false,
            { preserveDetails: true },
          );
          _llm.lastVoiceFailure = {
            ok: false,
            reason: data.reason || `HTTP ${resp.status}`,
            voice_source: data.voice_source || { provider: 'project_voice_unavailable' },
            audio_sent: data.audio_sent,
            audio_committed: data.audio_committed,
            response_observed: data.response_observed,
            audio_observed: data.audio_observed,
            response_done: data.response_done,
            line_match: data.line_match,
            spoken_transcript: data.spoken_transcript,
            use_tts: data.use_tts,
          };
          return _llm.lastVoiceFailure;
        } catch (e) {
          console.log(`[SoccerVoice] 原流水线输出请求失败 | ${String(e)}`);
          soccerSessionDebugLog(
            'error',
            'speech',
            'project_voice_request_failed',
            '小游戏项目语音请求失败',
            {
              reason: 'request_failed',
              error: String(e),
              interruptAudio,
              voiceArbiterReason,
              request_id: meta.request_id || result.request_id || '',
              event_kind: meta.kind || 'mailbox',
              round: meta.round,
            },
            false,
            { preserveDetails: true },
          );
          _llm.lastVoiceFailure = {
            ok: false,
            reason: 'request_failed',
            error: String(e),
            voice_source: { provider: 'project_voice_unavailable' },
          };
          return _llm.lastVoiceFailure;
        }
      }

      function _isUserReplyVoiceMeta(meta = {}) {
        return !!(meta.hasUserSpeech || meta.hasUserText || meta.kind === 'user-voice' || meta.kind === 'user-text');
      }

      function _voicePriorityForMeta(meta = {}) {
        const raw = Number(meta.voicePriority);
        if (Number.isFinite(raw) && raw >= 0 && raw <= 5) {
          return Math.floor(raw);
        }
        if (_isUserReplyVoiceMeta(meta)) {
          return 0;
        }
        return 4;
      }

      function _voiceFreshTtlForMeta(meta = {}, priority = 4) {
        const raw = Number(meta.freshTtlSeconds);
        if (Number.isFinite(raw) && raw > 0) return raw;
        if (meta.hasUserSpeech || meta.hasUserText || priority <= 1) {
          return GAME_VOICE_ARBITER_DEFAULTS.userReplyFreshTtlSeconds;
        }
        return GAME_VOICE_ARBITER_DEFAULTS.freshTtlSeconds;
      }

      function _estimateUserReplyVoiceSeconds(line = '') {
        const compactLen = String(line || '').replace(/\s+/g, '').length;
        const rawSeconds = 0.8 + compactLen * GAME_VOICE_ARBITER_DEFAULTS.userReplySecondsPerChar;
        return Math.max(
          GAME_VOICE_ARBITER_DEFAULTS.userReplyGuardMinSeconds,
          Math.min(GAME_VOICE_ARBITER_DEFAULTS.userReplyGuardMaxSeconds, rawSeconds)
        );
      }

        function _protectUserReplyVoice(entry) {
          if (!_isUserReplyVoiceMeta(entry?.meta || {})) return;
          const protectMs = _estimateUserReplyVoiceSeconds(entry.line) * 1000;
          _llm.voiceArbiter.userReplyProtectedUntil = Math.max(
            Number(_llm.voiceArbiter.userReplyProtectedUntil || 0),
            performance.now() + protectMs
          );
        }

        function _clearUserReplyVoiceProtection(entry) {
          if (!_isUserReplyVoiceMeta(entry?.meta || {})) return;
          _llm.voiceArbiter.userReplyProtectedUntil = 0;
        }

      function _currentPlaybackPriority(playback) {
        if (!playback?.active) return null;
        if (Number.isFinite(playback.priority)) return playback.priority;
        return playback.speechId ? 4 : null;
      }

      function _shouldInterruptPlayback(priority, playback) {
        if (!playback?.active) return false;
        if (priority === 0) return true;
        if (priority === 1) {
          const currentPriority = _currentPlaybackPriority(playback);
          return Number.isFinite(currentPriority) && currentPriority > 1;
        }
        return false;
      }

      function _clearVoiceArbiterPending(reason = 'clear') {
        if (_llm.voiceArbiter.timer) {
          clearTimeout(_llm.voiceArbiter.timer);
          _llm.voiceArbiter.timer = null;
        }
        if (_llm.voiceArbiter.pending) {
          console.log(`[SoccerVoice][Arbiter] 清理待播 | 原因=${reason} 台词="${_llm.voiceArbiter.pending.line}"`);
        }
        _llm.voiceArbiter.pending = null;
      }

      function _clearVoiceArbiterInFlight(reason = 'clear') {
        if (_llm.voiceArbiter.inFlight) {
          console.log(`[SoccerVoice][Arbiter] 清理占用中语音 | 原因=${reason} 台词="${_llm.voiceArbiter.inFlight.line}"`);
        }
        _llm.voiceArbiter.inFlight = null;
      }

      function _markUserInputForVoiceArbiter(source = 'user-input') {
        _clearVoiceArbiterPending(source);
        _clearVoiceArbiterInFlight(source);
        _llm.voiceArbiter.waitingForUserReplyGeneration = true;
        _llm.voiceArbiter.userReplyProtectedUntil = 0;
        console.log(`[SoccerVoice][Arbiter] 用户输入打断 | 来源=${source}，等待猫娘对用户输入的回复生成`);
      }

      function _scheduleVoiceArbiterFlush(delayMs, reason) {
        if (_llm.voiceArbiter.timer) clearTimeout(_llm.voiceArbiter.timer);
        _llm.voiceArbiter.timer = setTimeout(() => {
          _llm.voiceArbiter.timer = null;
          void _flushVoiceArbiter(reason);
        }, Math.max(0, delayMs));
      }

      function _storePendingVoice(entry, reason, playback) {
        const pending = _llm.voiceArbiter.pending;
        if (pending && pending.priority < entry.priority && performance.now() <= pending.expiresAt) {
          console.log(
            `[SoccerVoice][Arbiter] 丢弃新候选 | 原因=已有更高优先级 优先级=${entry.priority} ` +
            `现有=${pending.priority} 台词="${entry.line}"`
          );
          return;
        }
        if (pending) {
          console.log(`[SoccerVoice][Arbiter] 替换待播 | 原因=${reason} 旧="${pending.line}" 新="${entry.line}"`);
        } else {
          console.log(`[SoccerVoice][Arbiter] 暂存待播 | 原因=${reason} 台词="${entry.line}"`);
        }
        _llm.voiceArbiter.pending = entry;
        const now = performance.now();
        const remainingMs = playback?.active ? Math.max(0, playback.remainingSeconds * 1000 + 80) : 0;
        const ttlMs = Math.max(0, entry.expiresAt - now);
        _scheduleVoiceArbiterFlush(Math.min(Math.max(remainingMs, 80), Math.max(ttlMs, 80)), reason);
      }

      function _pendingOutranksEntry(entry) {
        const pending = _llm.voiceArbiter.pending;
        return !!(pending && pending.priority < entry.priority && performance.now() <= pending.expiresAt);
      }

      function _setVoiceArbiterInFlight(entry, reason = 'request') {
        const isUserReply = _isUserReplyVoiceMeta(entry?.meta || {});
        const guardMs = isUserReply
          ? _estimateUserReplyVoiceSeconds(entry.line) * 1000
          : Math.max(
              500,
              Math.min(
                entry.freshTtlSeconds * 1000,
                GAME_VOICE_ARBITER_DEFAULTS.inFlightGuardSeconds * 1000
              )
            );
        _llm.voiceArbiter.inFlight = {
          id: entry.id,
          line: entry.line,
          priority: entry.priority,
          speechId: '',
          startedAt: performance.now(),
          expiresAt: performance.now() + guardMs,
          reason,
        };
        if (isUserReply) _protectUserReplyVoice(entry);
      }

      function _updateVoiceArbiterInFlightFromResult(entry, result = {}) {
        const inFlight = _llm.voiceArbiter.inFlight;
        if (!inFlight || inFlight.id !== entry.id) return;
        const speechId = result?.speech_id ? String(result.speech_id) : '';
        if (speechId) {
          inFlight.speechId = speechId;
        }
          const isUserReply = _isUserReplyVoiceMeta(entry?.meta || {});
          const delivered = !!(result?.audio_sent || result?.audio_queued);
          if (!delivered) {
            if (isUserReply) _clearUserReplyVoiceProtection(entry);
            _clearVoiceArbiterInFlight(result?.reason || 'voice-not-delivered');
            return;
          }
          const guardMs = isUserReply
            ? _estimateUserReplyVoiceSeconds(entry.line) * 1000
            : Math.max(500, GAME_VOICE_ARBITER_DEFAULTS.inFlightGuardSeconds * 1000);
        inFlight.expiresAt = Math.max(inFlight.expiresAt, performance.now() + guardMs);
        if (isUserReply) _protectUserReplyVoice(entry);
      }

      function _readVoiceInFlight(playback) {
        const inFlight = _llm.voiceArbiter.inFlight;
        if (!inFlight) return null;
        if (performance.now() > inFlight.expiresAt) {
          _clearVoiceArbiterInFlight('expired');
          return null;
        }
        if (inFlight.speechId && playback?.active && playback.speechId === inFlight.speechId) {
          _clearVoiceArbiterInFlight('playback-observed');
          return null;
        }
        return {
          active: true,
          speechId: inFlight.speechId || '',
          priority: inFlight.priority,
          remainingSeconds: Math.max(0.1, (inFlight.expiresAt - performance.now()) / 1000),
          reason: 'in-flight',
          source: 'in_flight',
        };
      }

      function _readUserReplyProtectionOccupancy() {
        const remainingMs = Number(_llm.voiceArbiter.userReplyProtectedUntil || 0) - performance.now();
        if (remainingMs <= 0) return null;
        return {
          active: true,
          speechId: '',
          priority: 0,
          remainingSeconds: Math.max(0.1, remainingMs / 1000),
          reason: 'user-reply-protected',
          source: 'user_reply_protection',
        };
      }

      function _readVoiceOccupancy() {
        const playback = _readSpeechPlaybackState();
        const inFlight = _readVoiceInFlight(playback);
        const userReplyProtection = _readUserReplyProtectionOccupancy();
        if (userReplyProtection) return userReplyProtection;
        if (!playback?.active) return inFlight || playback;
        if (!inFlight) return playback;
        const playbackPriority = _currentPlaybackPriority(playback);
        if (Number.isFinite(inFlight.priority) && (!Number.isFinite(playbackPriority) || inFlight.priority < playbackPriority)) {
          return inFlight;
        }
        return playback;
      }

      function _requestGameVoiceWithLogging(entry, reason = 'immediate') {
        const voiceStartedAt = performance.now();
        const meta = entry.meta || {};
        const interruptAudio = reason === 'priority-interrupt';
        if (_isUserReplyVoiceMeta(meta)) {
          _llm.voiceArbiter.waitingForUserReplyGeneration = false;
        }
        _setVoiceArbiterInFlight(entry, reason);
        console.log(
          `[SoccerVoice][Arbiter] 放行语音 | 原因=${reason} 优先级=${entry.priority} ` +
          `打断=${interruptAudio} 台词="${entry.line}"`
        );
        return _sendGameSpeech(entry.line, meta, entry.result || {}, {
          reason,
          interruptAudio,
        }).then((voiceResult) => {
          const finalVoiceResult = voiceResult || {};
          const voiceOk = !!finalVoiceResult?.ok;
          const voiceSource = finalVoiceResult?.voice_source || { provider: 'project_voice_unavailable' };
          _updateVoiceArbiterInFlightFromResult(entry, finalVoiceResult);
          if (!voiceOk && voiceResult?.reason !== 'voice_output_disabled') {
            const deliveredButMismatch = !!finalVoiceResult?.audio_sent && finalVoiceResult?.reason === 'spoken_transcript_mismatch';
            console.log(
              `[SoccerVoice] ${deliveredButMismatch ? '项目语音已送达但台词不匹配' : '项目语音未送达'} | 原因=${finalVoiceResult?.reason || 'project_tts_unavailable'} ` +
              `台词来源=${_formatSourceLabel(entry.result?.llm_source)} 台词="${entry.line}"`
            );
          }
          soccerRecoverableLog(
            `[SoccerLLM] 语音结果 | 回合=${meta?.round ?? '?'} 事件=${eventLabel(meta?.kind)}(${meta?.kind}) ` +
            `语音来源=${_formatSourceLabel(voiceSource)} 语音送达=${!!finalVoiceResult?.audio_sent} ` +
            `提交=${!!finalVoiceResult?.audio_committed} 响应=${!!finalVoiceResult?.response_observed} ` +
            `完成=${!!finalVoiceResult?.response_done} 匹配=${finalVoiceResult?.line_match !== undefined ? !!finalVoiceResult.line_match : '-'} ` +
            `状态=${voiceOk ? 'ok' : (finalVoiceResult?.reason || 'failed')} ` +
            `耗时=${(performance.now() - voiceStartedAt).toFixed(0)}ms`
          );
          return finalVoiceResult;
        }).catch((e) => {
          soccerRecoverableLog('[SoccerVoice] 项目语音结果处理失败:', e);
          _clearUserReplyVoiceProtection(entry);
          _clearVoiceArbiterInFlight('request-failed');
          return { ok: false, reason: 'request_failed', error: String(e) };
        });
      }

      async function _flushVoiceArbiter(reason = 'timer') {
        const entry = _llm.voiceArbiter.pending;
        if (!entry) return null;
        if (performance.now() > entry.expiresAt) {
          console.log(`[SoccerVoice][Arbiter] 丢弃过期待播 | 原因=${reason} 台词="${entry.line}"`);
          _llm.voiceArbiter.pending = null;
          return { ok: false, reason: 'voice_candidate_expired', skipped: true };
        }
        if (_llm.voiceArbiter.waitingForUserReplyGeneration && !_isUserReplyVoiceMeta(entry.meta || {})) {
          const ttlMs = Math.max(0, entry.expiresAt - performance.now());
          _scheduleVoiceArbiterFlush(Math.min(Math.max(ttlMs, 120), 300), 'waiting-user-reply-generation');
          return { ok: false, reason: 'waiting_user_reply_generation', queued: true };
        }
        const playback = _readVoiceOccupancy();
        const shouldInterruptPlayback = playback.active && _shouldInterruptPlayback(entry.priority, playback);
        if (
          playback.active &&
          !shouldInterruptPlayback &&
          playback.remainingSeconds > entry.tailWaitSeconds
        ) {
          _storePendingVoice(entry, `仍在播放 ${playback.remainingSeconds.toFixed(2)}s`, playback);
          return { ok: false, reason: 'voice_candidate_waiting', queued: true };
        }
        _llm.voiceArbiter.pending = null;
        return _requestGameVoiceWithLogging(entry, shouldInterruptPlayback ? 'priority-interrupt' : reason);
      }

      function _enqueueGameVoice(line, meta = {}, result = {}, eventTs = performance.now()) {
        const priority = _voicePriorityForMeta(meta);
        const freshTtlSeconds = _voiceFreshTtlForMeta(meta, priority);
        const isUserReplyVoice = _isUserReplyVoiceMeta(meta);
        const entry = {
          id: ++_llm.voiceArbiter.seq,
          line,
          meta,
          result,
          eventTs,
          priority,
          tailWaitSeconds: Number(meta.tailWaitSeconds) > 0 ? Number(meta.tailWaitSeconds) : GAME_VOICE_ARBITER_DEFAULTS.tailWaitSeconds,
          freshTtlSeconds,
          createdAt: performance.now(),
          expiresAt: performance.now() + freshTtlSeconds * 1000,
        };

        if (!voiceOutputToggle?.checked) {
          return _requestGameVoiceWithLogging(entry, 'voice-disabled-check');
        }

        if (_llm.voiceArbiter.waitingForUserReplyGeneration && !isUserReplyVoice) {
          _storePendingVoice(entry, '等待猫娘对用户输入的回复生成', null);
          return Promise.resolve({ ok: false, queued: true, reason: 'waiting_user_reply_generation' });
        }

        const playback = _readVoiceOccupancy();
        if (_pendingOutranksEntry(entry)) {
          console.log(
            `[SoccerVoice][Arbiter] 丢弃新候选 | 原因=已有更高优先级待播 ` +
            `优先级=${entry.priority} 台词="${entry.line}"`
          );
          _scheduleVoiceArbiterFlush(0, 'higher-priority-pending');
          return Promise.resolve({ ok: false, skipped: true, reason: 'higher_priority_pending' });
        }
        if (!playback.active || _shouldInterruptPlayback(priority, playback)) {
          _clearVoiceArbiterPending('immediate-play');
          return _requestGameVoiceWithLogging(entry, playback.active ? 'priority-interrupt' : 'idle');
        }

        if (playback.remainingSeconds <= entry.tailWaitSeconds) {
          _storePendingVoice(entry, `尾部等待 ${playback.remainingSeconds.toFixed(2)}s`, playback);
          return Promise.resolve({ ok: false, queued: true, reason: 'voice_tail_wait' });
        }

        _storePendingVoice(entry, `当前剩余 ${playback.remainingSeconds.toFixed(2)}s`, playback);
        return Promise.resolve({ ok: false, queued: true, reason: 'voice_deferred' });
      }

      function _enqueueLLMItem(item) {
        if (_llm.cleanedUp || !isGameRuntimeReady()) return;
        if (!item) return;
        if (item.type === 'game_event') {
          const now = Date.now();
          const lastCall = _llm.cooldowns[item.kind] || 0;
          const cooldownLeftMs = _llm.cooldownSec * 1000 - (now - lastCall);
          if (cooldownLeftMs > 0) {
            console.log(`[SoccerLLM][Mailbox] 冷却丢弃 | 回合=${item.round} 事件=${eventLabel(item.kind)}(${item.kind}) 剩余=${Math.ceil(cooldownLeftMs)}ms`);
            return;
          }
          _llm.cooldowns[item.kind] = now;
        }
        _llm.pendingItems.push(item);
        _llm.pendingItems = _compactMailboxItems(_llm.pendingItems);
        console.log(
          `[SoccerLLM][Mailbox] 收集 | 回合=${item.round} 类型=${item.type} 事件=${eventLabel(item.kind)}(${item.kind}) ` +
          `队列=${_llm.pendingItems.length} 原文="${item.textRaw || ''}"`
        );
        _scheduleMailboxFlush();
      }

      function _scheduleMailboxFlush() {
        if (_llm.flushQueued) return;
        _llm.flushQueued = true;
        setTimeout(() => {
          _llm.flushQueued = false;
          _flushMailbox();
        }, 0);
      }

      async function _flushMailbox() {
        if (_llm.cleanedUp || !isGameRuntimeReady()) return;
        if (_llm.pending || !_llm.pendingItems.length) return;
        const items = _llm.pendingItems.splice(0, _llm.pendingItems.length);
        const currentState = SoccerDemo._snapshot();
        const eventPayload = _buildMailboxEvent(items, currentState);
        const first = items[0] || {};
        const eventTs = first.ts || performance.now();
        const displayMeta = {
          kind: eventPayload.kind,
          round: currentState.round,
          priority: Math.max(...items.map(i => i.priority || 0), 0),
          builtinFallback: items.length === 1 ? (first.builtinFallback || '') : '',
          itemCount: items.length,
          hasUserSpeech: items.some(i => i.type === 'user_speech'),
        };
        const result = await _requestGameLLM(eventPayload, displayMeta, eventTs);
        await _handleGameLLMResult(result, displayMeta, eventTs);
        if (_llm.pendingItems.length) _scheduleMailboxFlush();
      }

      async function _requestGameLLM(eventPayload, displayMeta, eventTs = performance.now()) {
        _llm.pending = true;
        const requestStartedAt = performance.now();
        const kind = eventPayload.kind || 'unknown';
        const mood = eventPayload.mood;
        const score = eventPayload.score || { player: 0, ai: 0 };
        const scoreDiff = score.ai - score.player;
        try {
          console.log(
            `[SoccerLLM] 请求 | 回合=${eventPayload.round} 事件=${eventLabel(kind)}(${kind}) ` +
            `心情=${moodLabel(mood)} 难度=${eventPayload.difficulty} 分差=${scoreDiff} ` +
            `条目=${displayMeta?.itemCount || 1} 原因=${REQUEST_CONTROL_REASON ? '请求' : '关闭'} ` +
            `分数=${JSON.stringify(score)} 原文="${eventPayload.textRaw || ''}"`
          );
          const resp = await soccerGame.dialogue.request({
            ..._soccerGameMemoryPolicyPayload(),
            ..._conversationLanguagePayload(),
            event: {
              ...eventPayload,
              ..._soccerGameMemoryPolicyPayload(),
            },
          });
          if (!resp.ok) {
            return {
              fallback: true,
              reason: `HTTP ${resp.status}`,
              fallbackNotice: { title: '游戏 LLM 响应', fallback: '使用内建台词兜底' },
            };
          }
          const data = resp.data || {};
          if (data.error) {
            console.warn('[SoccerLLM] 错误:', data.error);
            return {
              fallback: true,
              reason: data.error,
              fallbackNotice: { title: '游戏 LLM 响应', fallback: '使用内建台词兜底' },
            };
          }
          const fetchMs = performance.now() - requestStartedAt;
          const eventToResponseMs = performance.now() - eventTs;
          const metrics = data.metrics || {};
          const control = data.control || {};
          console.log(
            `[SoccerLLM] 完成 | 回合=${eventPayload.round} 事件=${eventLabel(kind)}(${kind}) 总耗时=${eventToResponseMs.toFixed(0)}ms ` +
            `请求=${fetchMs.toFixed(0)}ms 后端LLM=${metrics.llm_ms ?? '?'}ms 后端总=${metrics.total_ms ?? '?'}ms ` +
            `来源=${_formatSourceLabel(data.llm_source)} 台词="${data.line}"`
          );
          const controlText = Object.keys(control).length > 0 ? JSON.stringify(control) : '无';
          console.log(`[SoccerLLM][Control] 返回 | 回合=${eventPayload.round} 事件=${eventLabel(kind)}(${kind}) 指令=${controlText}`);
          if (Object.keys(control).length > 0) {
            soccerSessionDebugLog(
              'info',
              'llm',
              'game_control_returned',
              '小游戏 LLM 返回控制指令',
              {
                round: eventPayload.round,
                kind,
                control,
                mood: SoccerDemo.getMood(),
                difficulty: SoccerDemo.getDifficulty(),
              },
            );
          }
          if (control.reason) {
            console.log(`[SoccerLLM][Control] 理由 | 回合=${eventPayload.round} 事件=${eventLabel(kind)}(${kind}) ${control.reason}`);
          }
          if (data.balance_hint) {
            console.log(
              `[SoccerLLM][Control] 提示 | 回合=${eventPayload.round} 事件=${eventLabel(kind)}(${kind}) ` +
              `局势=${data.balance_hint.state} 强度=${data.balance_hint.intensity} 建议=${data.balance_hint.suggestion || 'free'} 推荐难度=${data.balance_hint.recommendedDifficulty || '无'}`
            );
          }
          return data;
        } catch (e) {
          console.warn('[SoccerLLM] 请求失败:', e);
          return {
            fallback: true,
            reason: String(e || '请求失败'),
            fallbackNotice: { title: '游戏 LLM 请求', fallback: '使用内建台词兜底' },
          };
        } finally {
          _llm.pending = false;
        }
      }

      async function _handleGameLLMResult(result, meta, eventTs) {
        if (_llm.cleanedUp || !isGameRuntimeReady()) return;
        if (result?.skipped) return;
        if (result?.fallback) {
          _recordFallbackDiagnostic(result?.fallbackNotice?.title || '游戏 LLM 响应', {
            fallback: result?.fallbackNotice?.fallback || (meta?.builtinFallback ? '使用内建台词兜底' : '跳过本次台词'),
            reason: result.reason || 'fallback',
            key: `game-llm:${meta?.kind || 'unknown'}:${result.reason || 'fallback'}`,
          });
        }
        if (!result || !result.line) {
          if (meta?.hasUserSpeech || meta?.hasUserText || meta?.kind === 'user-voice' || meta?.kind === 'user-text') {
            _llm.voiceArbiter.waitingForUserReplyGeneration = false;
          }
          if (result?.fallback && meta?.builtinFallback) {
            const fallbackMeta = Object.assign({}, meta, {
              kind: `${meta.kind || 'mailbox'}-fallback`,
              fallback: true,
            });
            const fallbackBubbleShown = SoccerDemo.say(meta.builtinFallback, {
              priority: meta.priority || 0,
              kind: fallbackMeta.kind,
              sourceLabel: 'LLM失败 · 内建兜底',
              hasUserSpeech: !!meta?.hasUserSpeech,
              hasUserText: !!meta?.hasUserText,
            });
            void _mirrorGameAssistantText(meta.builtinFallback, fallbackMeta, result);
            if (!meta?.voiceAlreadyHandled) {
              void _enqueueGameVoice(meta.builtinFallback, fallbackMeta, result, eventTs);
            }
            console.log(
              `[SoccerLLM] 兜底 | 回合=${meta.round} 事件=${eventLabel(meta.kind)}(${meta.kind}) ` +
              `原因=${result.reason || '无LLM台词'} 气泡=${fallbackBubbleShown ? '已更新' : '被保留'} ` +
              `聊天栏=已请求 语音=${meta?.voiceAlreadyHandled ? '后端已处理' : '已请求'} ` +
              `事件到气泡=${(performance.now() - eventTs).toFixed(0)}ms`
            );
          }
          return;
        }

        const bubbleShown = SoccerDemo.say(result.line, {
          priority: Math.min((meta?.priority || 0) + 1, 9),
          kind: (meta?.kind || 'mailbox') + '-llm',
          sourceLabel: `LLM生成 · ${Math.round(performance.now() - eventTs)}ms`,
          hasUserSpeech: !!meta?.hasUserSpeech,
          hasUserText: !!meta?.hasUserText,
        });
        void _mirrorGameAssistantText(result.line, meta, result);
        const shouldRequestVoice = !meta?.voiceAlreadyHandled;
        if (shouldRequestVoice) {
          void _enqueueGameVoice(result.line, meta, result, eventTs);
        }
        console.log(
          `[SoccerLLM] 已处理 | 回合=${meta?.round ?? '?'} 事件=${eventLabel(meta?.kind)}(${meta?.kind}) ` +
          `台词来源=${_formatSourceLabel(result.llm_source)} ` +
          `气泡=${bubbleShown ? '已更新' : '被保留'} 聊天栏=已请求 ` +
          `语音=${shouldRequestVoice ? (voiceOutputToggle?.checked ? '已请求' : '已关闭') : '后端已处理'} ` +
          `事件到气泡=${(performance.now() - eventTs).toFixed(0)}ms`
        );

        // 应用控制指令（心情/难度）
        // 台词允许轻微过期，但控制会真实改变游戏；后续 mailbox 版本需要为控制结果加
        // 状态快照/版本校验。当前 demo 只做合法值校验，主要用于验证 LLM 控制方向。
        if (result.control) {
          const applied = [];
          const ignored = [];
          if (result.control.mood && SoccerDemo.MOODS.includes(result.control.mood)) {
            const beforeMood = SoccerDemo.getMood();
            SoccerDemo.disableMoodRotation();
            const moodChanged = SoccerDemo.setMood(result.control.mood, { source: 'llm-control' });
            if (moodChanged) applied.push(`心情 ${moodLabel(beforeMood)} -> ${moodLabel(result.control.mood)}`);
            else ignored.push(`心情=${result.control.mood}（调试随机关闭）`);
            if (!moodDebugMode && _llm.moodRotationFallbackEnabled) {
              setTimeout(() => SoccerDemo.enableMoodRotation(20), 15000);
            }
          } else if (result.control.mood) {
            ignored.push(`心情=${result.control.mood}`);
          }
          if (result.control.difficulty && SoccerDemo.DIFFICULTIES.includes(result.control.difficulty)) {
            const beforeDifficulty = SoccerDemo.getDifficulty();
            const difficultyChanged = SoccerDemo.setDifficulty(result.control.difficulty, { source: 'llm-control' });
            if (difficultyChanged) applied.push(`难度 ${beforeDifficulty} -> ${result.control.difficulty}`);
          } else if (result.control.difficulty) {
            ignored.push(`难度=${result.control.difficulty}`);
          }
          if (applied.length > 0) {
            console.log(`[SoccerLLM][Control] 应用 | 回合=${meta?.round ?? '?'} ${applied.join('；')}`);
          }
          if (ignored.length > 0) {
            console.log(`[SoccerLLM][Control] 忽略 | 回合=${meta?.round ?? '?'} 非法指令 ${ignored.join('；')}，可选心情=${SoccerDemo.MOODS.join('/')}，可选难度=${SoccerDemo.DIFFICULTIES.join('/')}`);
          }
          if (applied.length > 0 || ignored.length > 0) {
            soccerSessionDebugLog(
              'info',
              'llm',
              'game_control_applied',
              '小游戏 LLM 控制指令处理完成',
              {
                round: meta?.round ?? null,
                requested: result.control,
                applied,
                ignored,
                finalMood: SoccerDemo.getMood(),
                finalDifficulty: SoccerDemo.getDifficulty(),
              },
            );
          }
          }
        }

        function _externalGameRouteInputText(output) {
          const event = output.event || {};
          const meta = output.meta || {};
          return String(event.textRaw || meta.inputText || event.userVoiceText || event.userText || '').trim();
        }

        function _showExternalUserVoiceBubble(output) {
          const event = output.event || {};
          const meta = output.meta || {};
          const isUserVoice = event.kind === 'user-voice'
            || event.type === 'user_voice'
            || meta.hasUserSpeech === true;
          if (!isUserVoice) return false;
          return showPlayerTranscriptBubble({
            text: _externalGameRouteInputText(output),
            requestId: output.request_id || meta.request_id || '',
            timestamp: output.ts || meta.inputTs || Date.now(),
          }, { source: output.source || 'route-drain' });
        }

        function _logExternalGameRouteInput(output) {
          const event = output.event || {};
          const meta = output.meta || {};
          const requestId = output.request_id || meta.request_id || '';
          const inputText = _externalGameRouteInputText(output);
          const kind = event.kind || meta.kind || 'external-input';
          const inputType = event.type || (meta.hasUserSpeech ? 'user_voice' : (meta.hasUserText ? 'user_text' : 'external_input'));
          const round = event.round ?? meta.round ?? '?';
          const key = requestId || `${output.ts || ''}:${kind}:${inputText}`;
          if (key && _llm.loggedExternalInputKeys.has(key)) return;
          if (key) _llm.loggedExternalInputKeys.add(key);
          console.log(
            `[SoccerLLM][Mailbox] 收集 | 回合=${round} 类型=${inputType} 事件=${eventLabel(kind)}(${kind}) ` +
            `队列=外部路由 原文="${inputText}" 来源=${output.source || '-'} 请求=${requestId || '-'}`
          );
        }

      function _performanceTimeFromEpochSeconds(epochSeconds) {
        const epochMs = Number(epochSeconds) * 1000;
        if (!Number.isFinite(epochMs) || epochMs <= 0) return performance.now();
        return performance.now() - Math.max(0, Date.now() - epochMs);
      }

        async function _handleExternalGameRouteOutput(output) {
          if (_llm.cleanedUp) return;
          try {
            if (output && output.type === 'game_voice_stt_gate') return;
            if (output && output.type === 'game_external_input') {
              _showExternalUserVoiceBubble(output);
              _markUserInputForVoiceArbiter(output.source || 'external-route-input');
              _handlePassiveGuardUserSpeech(
                _externalGameRouteInputText(output),
                output.source || output.meta?.kind || 'external-route-input',
              );
              _logExternalGameRouteInput(output);
              return;
            }
            if (!output || output.type !== 'game_llm_result') return;
            const resultMeta = Object.assign({}, output.meta || { kind: 'external-text', voiceAlreadyHandled: true }, {
              request_id: output.request_id || output.meta?.request_id || '',
            });
            const inputEpochSeconds = resultMeta.inputTs || output.input_ts || output.event?.inputTs || output.ts;
            await _handleGameLLMResult(
              output.result || {},
              resultMeta,
              _performanceTimeFromEpochSeconds(inputEpochSeconds)
            );
          } catch (e) {
            soccerRecoverableLog('[SoccerRoute] 外部输入结果处理失败:', e);
          }
        }

      soccerGame.events.on('runtime-output', ({ payload: output }) => (
        _handleExternalGameRouteOutput(output)
      ));

      soccerGame.runtime.configure({
        payload: () => _gameRoutePayload(),
        heartbeat: {
          intervalMs: 2500,
          timeoutMs: GAME_ROUTE_HEARTBEAT_FETCH_TIMEOUT_MS,
        },
        outputs: {
          intervalMs: 700,
          timeoutMs: 8000,
          limit: 50,
        },
        pageExit: {
          payload: () => _gameRouteEndPayload(true, { reason: 'pagehide' }),
        },
      });

      function _prepareGameLLMSessionCleanup() {
        if (_llm.cleanedUp) return false;
        _llm.cleanedUp = true;
        passiveGuard.sidecarGeneration = Number(passiveGuard.sidecarGeneration || 0) + 1;
        _clearVoiceArbiterPending('cleanup');
        _clearVoiceArbiterInFlight('cleanup');
        return true;
      }

      function _gameRouteEndPayload(useBeacon = false, options = {}) {
        const originalReason = options.reason || (useBeacon ? 'pagehide' : 'manual');
        const accidentalExit = options.skipAccidentalEntryCheck !== true && _isAccidentalGameEntryExit();
        const extraPayload = {
          reason: accidentalExit ? 'accidental_page_entry' : originalReason,
          originalReason,
        };
        if (accidentalExit) {
          extraPayload.accidentalGameEntry = true;
          extraPayload.postgameProactive = false;
        }
        if (!accidentalExit && Object.prototype.hasOwnProperty.call(options, 'postgameProactive')) {
          extraPayload.postgameProactive = options.postgameProactive;
        }
        return _gameRoutePayload(extraPayload);
      }

      async function _endGameLLMSession(useBeacon = false, options = {}) {
        if (!_prepareGameLLMSessionCleanup()) return null;
        const payload = _gameRouteEndPayload(useBeacon, options);
        try {
          return await soccerGame.runtime.end(payload, {
            useBeacon,
            onBeaconError: (error) => soccerRecoverableLog('[SoccerLLM] sendBeacon 清理失败:', error),
          });
        } catch (e) {
          soccerRecoverableLog('[SoccerLLM] 清理请求失败:', e);
          return { ok: false, reason: 'request_failed', error: String(e) };
        }
      }

      let _prepareStartInFlight = false;
      let _gameRouteStartOptions = {};
      async function _prepareGameForStartScreen() {
        if (_prepareStartInFlight) return;
        _prepareStartInFlight = true;
        try {
          _renderGameVoiceChatControl({ available: false, reason: 'connecting' });
          _resetGameRouteRuntime({ active: true, newSession: true });
          _resetGameFieldForStartScreen();
          _gameRouteStartOptions = _readGameRouteStartOptions();
          if (gameMemoryToggle) gameMemoryToggle.disabled = false;
          window.__SoccerLoading?.showStart?.();
        } finally {
          _prepareStartInFlight = false;
        }
      }

      async function _startGameFromStartScreen() {
        if (_prepareStartInFlight || _llm.gameStarted || !window.__SoccerLoading?.canStart?.()) return;
        _prepareStartInFlight = true;
        if (gameMemoryToggle) gameMemoryToggle.disabled = true;
        // Unlock browser audio in the user gesture, before network awaits.
        void soccerGameAudio.unlock();
        try {
          await _startGameRoute();
        } catch (error) {
          let released = soccerGame.runtime.state === 'idle';
          if (!released) {
            try {
              const result = await soccerGame.runtime.end(_gameRouteEndPayload(false, { reason: 'start_failed' }));
              released = result.ok && result.data?.ok !== false;
            } catch (_) { /* keep the unresolved generation for page-exit cleanup */ }
          }
          if (released) {
            soccerGame.runtime.reset({ newSession: true });
            if (gameMemoryToggle) gameMemoryToggle.disabled = false;
            window.__SoccerLoading?.showStart?.(_i18n('startScreen.startFailedRetry', '启动失败，请重试'));
          } else {
            window.__SoccerLoading?.ended?.();
          }
          return;
        } finally {
          _prepareStartInFlight = false;
        }
        const started = window.__SoccerLoading?.startGame?.();
        if (!started) return;
        _resetPassiveGuardForNewGame();
        _llm.gameStarted = true;
        _llm.gameStartedAt = performance.now();
        _llm.gameStartedAtEpochMs = Date.now();
        const openingDifficultyName = DIFFICULTY[difficultyIdx]?.name || '';
        _startedAsMaxAngry = openingDifficultyName === 'max' && moodKey === 'angry';
        _openingMaxAngryBgmActive = _startedAsMaxAngry;
        const openingBgmReason = openingDifficultyName === 'max' && moodKey === 'angry'
          ? 'start-game-max-angry'
          : 'start-game';
        console.log(`[SoccerAudio] 开局 BGM 判定 mood=${moodKey} difficulty=${openingDifficultyName} reason=${openingBgmReason}`);
        void soccerGameAudio.unlock();
        soccerGameAudio.sync(openingBgmReason);
        void _sendGameRouteHeartbeat(true);
        _deliverPendingOpeningLine();
      }

      // 注：之前这里有两段一次性 ``data-i18n-key`` localizer（exit button + memoryOption），
      // 在 IIFE 执行时 ``typeof window.t === 'function'`` gate。codex P1 (PR #1149) 指出：
      // i18n-i18next.js 是 ``DOMContentLoaded`` 之后异步 init，IIFE 跑到这一步时 ``window.t``
      // 通常还没挂上，gate skip → 非 zh locale 看到 HTML 兜底中文。修法是改 ``data-i18n``
      // 让框架的 ``updatePageTexts()`` 在 init / localechange 自动接管，全部不再需要手动
      // localizer。各元素的 ``data-i18n="soccer.…"`` 已就位，本块整体删除。

      let _manualEndInFlight = false;
      function _waitMs(ms) {
        return new Promise((resolve) => setTimeout(resolve, ms));
      }

      async function _endGameAndShowClosePrompt() {
        if (_manualEndInFlight || !isGameRuntimeReady()) return;
        _manualEndInFlight = true;
        if (exitToStartButton) exitToStartButton.disabled = true;
        const _t = (key, fallback) => {
          // i18next 在 key 缺失时返回 key 字面量本身——必须等值比对回 fallback，
          // 否则 UI 会出现 "soccer.exitGame.ending" 这种 key 字符串。
          if (typeof window.t !== 'function') return fallback;
          const fullKey = `soccer.${key}`;
          const v = window.t(fullKey);
          return (v && v !== fullKey) ? v : fallback;
        };
        let closeDelayMs = 600;
        try {
          window.__SoccerLoading?.ending?.(_t('exitGame.ending', '正在退出本局…'));
          await _endGameLLMSession(false, {
            reason: 'manual_user_exit',
            postgameProactive: false,
          });
          const playerWon = Number(state.score.player || 0) > Number(state.score.ai || 0);
          const playerWinBgm = soccerGameAudio.config.bgm.result?.playerWin;
          const hasPlayerWinBgm = Array.isArray(playerWinBgm) ? playerWinBgm.length > 0 : Boolean(playerWinBgm);
          if (playerWon && hasPlayerWinBgm) {
            const started = await soccerGameAudio.playBgm(playerWinBgm, {
              id: 'soccer:result:player-win',
              repeat: false,
              fadeMs: 250,
            });
            // 胜利结算 BGM 按 max(2.6 秒, BGM 播放完成) 后再返回。
            // timeoutMs 只是异常兜底，避免浏览器没有 fired ended 时永久卡在退出流程。
            if (started) {
              await Promise.all([
                _waitMs(2600),
                soccerGameAudio.waitForBgmEnd({ timeoutMs: 120000 }),
              ]);
              closeDelayMs = 0;
            } else {
              closeDelayMs = 2600;
            }
          } else {
            soccerGameAudio.stop();
          }
        } finally {
          window.__SoccerLoading?.ended?.(_t('exitGame.ended', '游戏已结束，正在返回…'));
          _manualEndInFlight = false;
          // 退出后：先尝试 window.close()（适用于 main app 用 window.open 弹的子窗口
          // 与 Electron 多窗口）；浏览器无脚本权限关闭时 fall back 到主聊天 SPA 的
          // 根路径。短延迟让用户看到 "正在返回..." 文本，避免页面瞬间消失。
          setTimeout(() => {
            try {
              window.close();
            } catch (_) {}
            // 如果 close 没生效（普通 tab / 用户手敲 URL），跳回 /
            setTimeout(() => {
              if (!window.closed) {
                window.location.assign('/');
              }
            }, 150);
          }, closeDelayMs);
        }
      }

      startButton?.addEventListener('click', _startGameFromStartScreen);
      exitToStartButton?.addEventListener('click', _endGameAndShowClosePrompt);
      surrenderReminderToggle?.addEventListener('change', () => {
        _setSurrenderReminderEnabled(Boolean(surrenderReminderToggle.checked), {
          persist: true,
          source: 'ui_toggle',
        });
      });
      exitPromptContinueButton?.addEventListener('click', () => {
        const type = passiveGuard.modalType;
        if (exitPromptNeverAgain?.checked) {
          _setSurrenderReminderEnabled(false, { persist: true, source: `modal_never_again_${type || 'unknown'}_continue` });
        }
        if (type === 'rest') {
          passiveGuard.restDismissedThisGame = true;
          _clearRestCandidate('rest_continue_clicked');
        } else {
          passiveGuard.ordinaryDisabledForCurrentGame = true;
          _clearOrdinaryCandidate('ordinary_continue_clicked');
        }
        passiveGuard.modalLineToken++;
        _hideExitPrompt();
        console.log(`[Soccer] [PassiveGuard] [Modal] 继续游戏 | 类型=${type || '未知'} 本场普通功能=${passiveGuard.ordinaryDisabledForCurrentGame ? '停用' : '启用'}`);
      });
      exitPromptEndButton?.addEventListener('click', () => {
        const type = passiveGuard.modalType;
        if (exitPromptNeverAgain?.checked) {
          _setSurrenderReminderEnabled(false, { persist: true, source: `modal_never_again_${type || 'unknown'}_end` });
        }
        passiveGuard.modalLineToken++;
        _hideExitPrompt();
        console.log(`[Soccer] [PassiveGuard] [Modal] 结束/休息 | 类型=${type || '未知'} 处理=复用手动退出`);
        void _endGameAndShowClosePrompt();
      });
      await _loadSurrenderReminderEnabled();
      _setSurrenderReminderEnabled(_readSurrenderReminderEnabled(), {
        persist: false,
        source: 'init',
      });
      void _prepareGameForStartScreen();

      // 注册 onSpeak 回调：拦截高优先级事件，调用 LLM 生成台词
      SoccerDemo.onSpeak(async (p) => {
        if (!_llm.llmKinds.has(p.kind)) return;
        _enqueueLLMItem(_makeMailboxItemFromSpeak(p));
      });

      function _submitUserSpeech(text, source = 'debug-text') {
        const clean = String(text || '').trim();
        if (!clean) return;
        const item = _makeMailboxItemFromUserSpeech(clean);
        if (!item) return;
        _handlePassiveGuardUserSpeech(clean, source);
        _markUserInputForVoiceArbiter(source);
        console.log(`[SoccerVoice] 输入 | 来源=${source} 回合=${item.round} 分数=${JSON.stringify(item.snapshot.score)} 原文="${item.textRaw}"`);
        _setVoiceStatus(_i18n('voiceStatus.received', `已收到玩家语音：${item.textRaw}`, { text: item.textRaw }));

        _enqueueLLMItem(item);
      }

      function _submitVoiceTextInput() {
        const text = voiceTextInput?.value || '';
        if (voiceTextInput) voiceTextInput.value = '';
        void _submitUserSpeech(text, 'text-box');
      }

      voiceSendButton?.addEventListener('click', _submitVoiceTextInput);
      voiceTextInput?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') _submitVoiceTextInput();
      });

      let voiceListening = false;

      function _setVoiceListening(next) {
        voiceListening = next;
        voiceMicButton?.classList.toggle('listening', next);
        if (voiceMicButton) voiceMicButton.textContent = next
          ? _i18n('debugButton.holdToSubmit', '松开提交')
          : _i18n('debugButton.idle', '调试语音');
        _setVoiceStatus(next
          ? _i18n('voiceStatus.debugListening', '调试 STT：正在听玩家说话…')
          : _i18n('voiceStatus.debugIdle', '调试 STT：待机'));
      }

      async function _startVoiceRecognition() {
        if (voiceListening || !soccerGame.capabilities.has('voice-input')) return;
        _setVoiceListening(true);
        try {
          const state = await soccerGame.voice.start();
          _renderGameVoiceChatControl(state);
          _setVoiceListening(state?.active === true);
        } catch (error) {
          console.warn('[SoccerVoice] 官方语音入口启动失败:', error);
          _setVoiceListening(false);
        }
      }

      async function _stopVoiceRecognition() {
        if (!voiceListening) return;
        _setVoiceListening(false);
        try {
          const state = await soccerGame.voice.stop();
          _renderGameVoiceChatControl(state);
        } catch (error) {
          console.warn('[SoccerVoice] 官方语音入口关闭失败:', error);
        }
      }

      voiceMicButton?.addEventListener('mousedown', _startVoiceRecognition);
      voiceMicButton?.addEventListener('touchstart', (e) => { e.preventDefault(); _startVoiceRecognition(); }, { passive: false });
      window.addEventListener('mouseup', _stopVoiceRecognition);
      window.addEventListener('touchend', _stopVoiceRecognition);
      if (!soccerGame.capabilities.has('voice-input')) {
        _setVoiceStatus(_i18n('voiceStatus.debugUnsupported', '当前宿主未提供语音输入能力，请用调试文本框提交'));
      }

      let lastT = performance.now();
      function loop(t) {
        if (!isGameRuntimeReady()) {
          lastT = t;
          render();
          requestAnimationFrame(loop);
          return;
        }
        if (_isExitPromptOpen()) {
          lastT = t;
          render();
          requestAnimationFrame(loop);
          return;
        }
        const dt = Math.min(0.033, (t - lastT) / 1000);
        lastT = t;

        const playerControlX = playerPointerActive ? state.mouse.x : state.player.x + CFG.charSize/2;
        const playerControlY = playerPointerActive ? state.mouse.y : state.player.y + CFG.charSize/2;
        stepCharacter(state.player, playerControlX, playerControlY,
          CFG.playerMaxSpeed, CFG.playerAccel, dt);
        if (singlePlayerMode) {
          state.ai.vx = 0;
          state.ai.vy = 0;
        } else {
          aiDecide(dt);
          aiMoodTick(dt);  // 心情副作用：startle/zoneout 计时
          const at = aiTarget();
          const diffCur = DIFFICULTY[difficultyIdx];
          const moodCur = MOODS[moodKey];
          // startle/zoneout 冻结期间 AI 原地不动
          if (aiFreezeSec > 0) {
            aiFreezeSec -= dt;
            stepCharacter(state.ai, state.ai.x + CFG.charSize/2, state.ai.y + CFG.charSize/2,
              1, 1, dt); // 目标就是当前位置 → 阻尼让它停下
          } else {
            const speedFactor = diffCur.speedMul * moodCur.speedMul;
            stepCharacter(state.ai, at.x, at.y,
              CFG.aiMaxSpeed * speedFactor, CFG.aiAccel * speedFactor, dt);
          }
        }
        // 玩家蓄力累积
        if (playerCharging) playerCharge = Math.min(1, playerCharge + dt / CHARGE_MAX_SEC);
        if (ballGhostSec > 0) ballGhostSec -= dt;

        stepBall(dt);
        resolveCharBall(state.player);
        resolveCharBall(state.ai);
        aiTryKick(dt);
        checkGoal();
        unstickBall(dt);
        if (state.flashTimer > 0) state.flashTimer -= dt;

        // 容器定位：脚落在物理框底边，物理框中心（= 碰撞圆心、=debug 红点）就在角色身体处、
        // 比脚高 charSize/2 像素 —— 这样红点看起来是"腰部/胸口"而不是脚底。
        const pcx = state.player.x + CFG.charSize/2;
        const pFeetY = state.player.y + CFG.charSize;
        playerEl.style.transform = `translate3d(${pcx - CFG.vrmW/2}px, ${pFeetY - CFG.vrmH}px, 0)`;

        const acx = state.ai.x + CFG.charSize/2;
        const aFeetY = state.ai.y + CFG.charSize;
        aiEl.style.transform = `translate3d(${acx - CFG.vrmW/2}px, ${aFeetY - CFG.vrmH}px, 0)`;

        // AI 盯着球
        window.__SoccerAiAvatarController?.focus?.({ x: state.ball.x, y: state.ball.y });

        // 说话子系统：场景轮询 + 气泡同步到 AI 头顶
        speechTick(dt);
        positionBubble();
        updateMoodDebugPanel();

        render();
        requestAnimationFrame(loop);
      }

      function render() {
        const W = canvas.width, H = canvas.height;

        // ═══ 底层（game canvas，在角色容器之下）：球场 + 球门 + 闪屏 + 分数 + AI fallback 方块 ═══
        ctx.fillStyle = '#1f6e4a';
        ctx.fillRect(0, 0, W, H);
        ctx.fillStyle = 'rgba(255,255,255,0.03)';
        const stripe = 80;
        for (let x = 0; x < W; x += stripe*2) ctx.fillRect(x, 0, stripe, H);
        ctx.strokeStyle = 'rgba(255,255,255,0.35)';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(W/2, 0); ctx.lineTo(W/2, H);
        ctx.moveTo(W/2 + 90, H/2); ctx.arc(W/2, H/2, 90, 0, Math.PI*2);
        ctx.stroke();
        const y1 = H/2 - CFG.goalHeight/2;
        ctx.fillStyle = 'rgba(61,169,252,0.18)';
        ctx.fillRect(0, y1, CFG.goalWidth, CFG.goalHeight);
        ctx.fillStyle = 'rgba(239,69,101,0.18)';
        ctx.fillRect(W - CFG.goalWidth, y1, CFG.goalWidth, CFG.goalHeight);
        ctx.strokeStyle = 'rgba(255,255,255,0.6)';
        ctx.strokeRect(0, y1, CFG.goalWidth, CFG.goalHeight);
        ctx.strokeRect(W - CFG.goalWidth, y1, CFG.goalWidth, CFG.goalHeight);

        // ── 边界线（启用时绘制）──
        if (BOUNDARY.enabled) {
          const m = BOUNDARY.margin;
          ctx.strokeStyle = 'rgba(255,255,255,0.55)';
          ctx.lineWidth = 2;
          ctx.setLineDash([8, 6]);
          ctx.strokeRect(m, m, W - m * 2, H - m * 2);
          ctx.setLineDash([]);
          // 出界闪烁提示
          if (_outOfBoundsTimer > 0) {
            const flash = Math.sin(_outOfBoundsTimer * 12) * 0.5 + 0.5;
            ctx.strokeStyle = `rgba(255,80,80,${0.4 + flash * 0.5})`;
            ctx.lineWidth = 3;
            ctx.strokeRect(m, m, W - m * 2, H - m * 2);
          }
        }

        // AI：头像没加载成功的话，用方块 fallback
        if (!window.__SoccerAiAvatar?.ready) {
          drawChar(state.ai);
        }

        if (state.flashTimer > 0) {
          const a = Math.min(1, state.flashTimer / 0.6) * 0.35;
          ctx.fillStyle = state.flashSide === 'player'
            ? `rgba(61,169,252,${a})`
            : `rgba(239,69,101,${a})`;
          ctx.fillRect(0, 0, W, H);
        }

        // ═══ 上层（debug canvas，在角色容器之上）：碰撞圆、debug 点、视线、射门范围、预览箭头、球、HUD ═══
        dctx.clearRect(0, 0, W, H);
        const p = state.player;
        const pcx = p.x + CFG.charSize/2, pcy = p.y + CFG.charSize/2;
        const acx = state.ai.x + CFG.charSize/2, acy = state.ai.y + CFG.charSize/2;

        // 玩家碰撞圆
        dctx.strokeStyle = 'rgba(61,169,252,0.7)';
        dctx.lineWidth = 2;
        dctx.setLineDash([4, 4]);
        dctx.beginPath();
        dctx.arc(pcx, pcy, CFG.charSize/2, 0, Math.PI*2);
        dctx.stroke();

        // AI 碰撞圆
        dctx.strokeStyle = 'rgba(239,69,101,0.7)';
        dctx.beginPath();
        dctx.arc(acx, acy, CFG.charSize/2, 0, Math.PI*2);
        dctx.stroke();
        dctx.setLineDash([]);

        // 玩家射门范围
        const distBall = Math.hypot(state.ball.x - pcx, state.ball.y - pcy);
        const inRange = distBall <= CFG.kickRange + CFG.ballRadius;
        dctx.strokeStyle = inRange ? 'rgba(255,255,255,0.85)' : 'rgba(61,169,252,0.35)';
        dctx.lineWidth = 1.5;
        dctx.setLineDash([4, 4]);
        dctx.beginPath();
        dctx.arc(pcx, pcy, CFG.kickRange, 0, Math.PI*2);
        dctx.stroke();
        dctx.setLineDash([]);

        // 玩家射门预览箭头：在 kick range 内时画；蓄力越足，箭头越长越"热"
        if (inRange) {
          const { nx, ny } = kickDirFor(state.player);
          const ahX0 = state.ball.x + nx * CFG.ballRadius;
          const ahY0 = state.ball.y + ny * CFG.ballRadius;
          const aLen = 90 + playerCharge * 110; // 90px (tap) → 200px (满蓄)
          const ahX1 = ahX0 + nx * aLen;
          const ahY1 = ahY0 + ny * aLen;
          // 颜色插值：白 → 金
          const chHue = 60; // 金色 hue
          const chLight = 95 - playerCharge * 35;
          const chSat = 30 + playerCharge * 70;
          dctx.strokeStyle = `hsla(${chHue}, ${chSat}%, ${chLight}%, 0.95)`;
          dctx.lineWidth = 3 + playerCharge * 2;
          dctx.beginPath();
          dctx.moveTo(ahX0, ahY0);
          dctx.lineTo(ahX1, ahY1);
          dctx.stroke();
          const ang = Math.atan2(ny, nx);
          const ah = 14 + playerCharge * 6;
          dctx.fillStyle = dctx.strokeStyle;
          dctx.beginPath();
          dctx.moveTo(ahX1, ahY1);
          dctx.lineTo(ahX1 - ah * Math.cos(ang - 0.45), ahY1 - ah * Math.sin(ang - 0.45));
          dctx.lineTo(ahX1 - ah * Math.cos(ang + 0.45), ahY1 - ah * Math.sin(ang + 0.45));
          dctx.closePath();
          dctx.fill();
        }

        // 玩家身边画蓄力环（无论是否在 range 内都能看到自己蓄到哪儿）
        if (playerCharging && playerCharge > 0.02) {
          dctx.strokeStyle = `hsla(60, ${30 + playerCharge * 70}%, ${95 - playerCharge * 35}%, 0.9)`;
          dctx.lineWidth = 4;
          dctx.beginPath();
          dctx.arc(pcx, pcy, CFG.charSize/2 + 12, -Math.PI/2, -Math.PI/2 + playerCharge * Math.PI * 2);
          dctx.stroke();
        }

        // AI 冻结（惊讶/走神）：大感叹号 + 脉动光晕 + 黑色描边 + 眩晕星圈
        if (aiFreezeSec > 0) {
          const pulse = 0.5 + 0.5 * Math.sin(performance.now() / 90);
          const yTop = acy - CFG.charSize/2 - 70;
          // 光晕背景
          const haloR = 42 + pulse * 10;
          const grad = dctx.createRadialGradient(acx, yTop, 6, acx, yTop, haloR);
          grad.addColorStop(0, `rgba(255,240,120,${0.55 + 0.25 * pulse})`);
          grad.addColorStop(1, 'rgba(255,220,60,0)');
          dctx.fillStyle = grad;
          dctx.beginPath(); dctx.arc(acx, yTop, haloR, 0, Math.PI*2); dctx.fill();
          // 感叹号（大字 + 描边）
          dctx.font = 'bold 84px sans-serif';
          dctx.textAlign = 'center';
          dctx.textBaseline = 'middle';
          dctx.lineWidth = 8;
          dctx.strokeStyle = '#000';
          dctx.strokeText('!', acx, yTop);
          dctx.fillStyle = '#fff347';
          dctx.fillText('!', acx, yTop);
          dctx.textBaseline = 'alphabetic';
          // 眩晕小星圈：AI 头顶三颗随时间旋转的小星
          const t = performance.now() / 260;
          for (let i = 0; i < 3; i++) {
            const a = t + i * (Math.PI * 2 / 3);
            const sx = acx + Math.cos(a) * 34;
            const sy = (acy - CFG.charSize/2 - 12) + Math.sin(a) * 8;
            dctx.fillStyle = '#fff347';
            dctx.beginPath(); dctx.arc(sx, sy, 4, 0, Math.PI*2); dctx.fill();
            dctx.strokeStyle = '#000'; dctx.lineWidth = 1.5; dctx.stroke();
          }
        }

        // debug dots & 连线
        dctx.fillStyle = '#ff4757';
        dctx.beginPath(); dctx.arc(state.mouse.x, state.mouse.y, 6, 0, Math.PI*2); dctx.fill();
        dctx.fillStyle = '#2ed573';
        dctx.beginPath(); dctx.arc(pcx, pcy, 5, 0, Math.PI*2); dctx.fill();
        dctx.fillStyle = '#ef4565';
        dctx.beginPath(); dctx.arc(acx, acy, 5, 0, Math.PI*2); dctx.fill();
        dctx.strokeStyle = 'rgba(255,255,0,0.6)';
        dctx.lineWidth = 1;
        dctx.beginPath(); dctx.moveTo(state.mouse.x, state.mouse.y); dctx.lineTo(pcx, pcy); dctx.stroke();
        dctx.strokeStyle = 'rgba(239,69,101,0.4)';
        dctx.setLineDash([2, 3]);
        dctx.beginPath(); dctx.moveTo(acx, acy); dctx.lineTo(state.ball.x, state.ball.y); dctx.stroke();
        dctx.setLineDash([]);

        // 球（debug 层，这样角色容器不会遮挡球）
        // 幽灵期：半透 + 闪烁轮廓，提示当前球无视角色碰撞
        const ghost = ballGhostSec > 0;
        const flicker = ghost ? (0.45 + 0.3 * Math.sin(performance.now() / 60)) : 1;
        dctx.fillStyle = ghost ? `rgba(255,255,255,${flicker * 0.55})` : '#fff';
        dctx.beginPath();
        dctx.arc(state.ball.x, state.ball.y, CFG.ballRadius, 0, Math.PI*2);
        dctx.fill();
        dctx.strokeStyle = ghost ? `rgba(80,220,255,${flicker})` : '#222';
        dctx.lineWidth = ghost ? 2 : 1.5;
        dctx.stroke();

        // debug 文字
        dctx.fillStyle = '#fff';
        dctx.textAlign = 'left';
        dctx.font = '12px monospace';
        dctx.fillText(debugText('mouse', `鼠标：${debugParam('x')}, ${debugParam('y')}`, { x: state.mouse.x | 0, y: state.mouse.y | 0 }), 12, 20);
        dctx.fillText(debugText('playerCenter', `玩家中心：${debugParam('x')}, ${debugParam('y')}`, { x: pcx | 0, y: pcy | 0 }), 12, 36);
        dctx.fillText(debugText('aiCenter', `AI 中心：${debugParam('x')}, ${debugParam('y')}  状态：${debugParam('mode')}`, {
          x: acx | 0,
          y: acy | 0,
          mode: debugAiModeLabel(aiMode),
        }), 12, 52);
        dctx.fillText(debugText('state', `回合：${debugParam('round')}  难度：${debugParam('difficulty')}  心情：${debugParam('mood')}  蓄力：${debugParam('charge')}%`, {
          round: state.round,
          difficulty: debugDifficultyLabel(DIFFICULTY[difficultyIdx].name),
          mood: debugMoodLabel(moodKey),
          charge: (playerCharge * 100) | 0,
        }), 12, 68);
        const ballSp = Math.hypot(state.ball.vx, state.ball.vy) | 0;
        dctx.fillText(debugText('ball', `球：(${debugParam('x')}, ${debugParam('y')})  速度=${debugParam('speed')}`, {
          x: state.ball.x | 0,
          y: state.ball.y | 0,
          speed: ballSp,
        }), 12, 84);

        // 最近事件（kick / unstick），4 秒内可见，按时间淡出
        const nowMs = performance.now();
        dctx.font = '11px monospace';
        for (let i = 0; i < GAME_EVENTS.length; i++) {
          const ev = GAME_EVENTS[i];
          const ageSec = (nowMs - ev.time) / 1000;
          if (ageSec > 4) continue;
          const alpha = Math.max(0, 1 - ageSec / 4);
          const col = ev.label === 'unstick' ? '255,220,60'
                    : ev.label === 'ai-kick' ? '239,69,101'
                    : '61,169,252';
          dctx.fillStyle = `rgba(${col},${alpha})`;
          dctx.fillText(debugText('event', `${debugParam('age')}秒  ${debugParam('label')}  @(${debugParam('x')},${debugParam('y')})  速度=(${debugParam('vx')},${debugParam('vy')})`, {
            age: ageSec.toFixed(1),
            label: debugGameEventLabel(ev.label),
            x: ev.x,
            y: ev.y,
            vx: ev.vx,
            vy: ev.vy,
          }), 12, 104 + i * 14);
        }

        // 前摇视觉指示：AI 周围环形进度 + 瞄准线
        if (aiWindupRemaining > 0 && aiWindupAim && aiWindupTotal > 0) {
          const progress = 1 - aiWindupRemaining / aiWindupTotal;
          const ringR = CFG.charSize/2 + 8;
          dctx.strokeStyle = 'rgba(255,220,60,0.9)';
          dctx.lineWidth = 3;
          dctx.beginPath();
          dctx.arc(acx, acy, ringR, -Math.PI/2, -Math.PI/2 + progress * Math.PI * 2);
          dctx.stroke();
          // 瞄准线：AI → 球方向延伸
          const len = 60;
          dctx.strokeStyle = 'rgba(255,220,60,0.55)';
          dctx.lineWidth = 2;
          dctx.setLineDash([3, 4]);
          dctx.beginPath();
          dctx.moveTo(acx, acy);
          dctx.lineTo(acx + aiWindupAim.x * len, acy + aiWindupAim.y * len);
          dctx.stroke();
          dctx.setLineDash([]);
        }

        // HUD 分数（debug 层）
        dctx.fillStyle = '#fff';
        dctx.textAlign = 'center';
        dctx.font = 'bold 36px sans-serif';
        dctx.fillText(`${state.score.player}  :  ${state.score.ai}`, W/2, 54);
        dctx.font = 'bold 16px sans-serif';
        dctx.fillText(debugText('roundCenter', `第 ${debugParam('round')} 回合`, { round: state.round }), W/2, 78);
      }

      function drawChar(c) {
        ctx.fillStyle = c.color;
        ctx.fillRect(c.x, c.y, CFG.charSize, CFG.charSize);
        ctx.strokeStyle = 'rgba(0,0,0,0.4)';
        ctx.lineWidth = 2;
        ctx.strokeRect(c.x + 0.5, c.y + 0.5, CFG.charSize - 1, CFG.charSize - 1);
      }

      requestAnimationFrame(loop);
    })();

  };

  const runInitializeSoccerPage = () => {
    void initializeSoccerPage().catch((error) => {
      console.error('[soccer_demo] initialization failed:', error);
    });
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', runInitializeSoccerPage, { once: true });
  } else {
    runInitializeSoccerPage();
  }
})();
