/**
 * Trusted Avatar engine adapter for the soccer mini-game.
 *
 * Public game logic must use NekoMiniGame.avatar. This file is the temporary
 * same-origin host implementation that binds the reusable SDK Avatar host to
 * N.E.K.O's official Live2D and VRM managers.
 */
(() => {
  'use strict';

  function fitVrmManagerCamera(manager, containerId, label = 'VRM', viewport = null, fit = {}) {
    const THREE = window.THREE;
    const model = manager?.currentModel?.vrm?.scene;
    if (!THREE || !manager?.camera || !model) return;
    const container = document.getElementById(containerId);
    const size = {
      width: Number(viewport?.width || container?.clientWidth || 200),
      height: Number(viewport?.height || container?.clientHeight || 300),
    };
    manager.renderer?.setSize?.(size.width, size.height, false);
    manager.effect?.setSize?.(size.width, size.height);
    const layout = window.NekoMiniGameAvatarHost.fitPerspectiveModel(
      THREE, model, manager.camera, size, fit,
    );
    // Mouse-follow uses this target. Preserve the SDK camera projection and
    // model transform; updating orbit controls here would overwrite the fit.
    manager._cameraTarget = new THREE.Box3().setFromObject(model).getCenter(new THREE.Vector3());
    manager.controls?.target?.copy?.(manager._cameraTarget);
    return layout;
  }

  function isVrm0(gltf, vrm) {
    const extensions = gltf?.parser?.json?.extensionsUsed || [];
    if (extensions.includes('VRMC_vrm')) return false;
    if (extensions.includes('VRM')) return true;
    const version = vrm?.meta?.metaVersion || vrm?.meta?.vrmVersion;
    return typeof version === 'string' && version.startsWith('0');
  }

  function vrmBoneNode(vrm, boneName) {
    const humanoid = vrm?.humanoid;
    if (!humanoid) return null;
    try {
      const raw = humanoid.getRawBoneNode?.(boneName);
      if (raw) return raw;
      const normalized = humanoid.getNormalizedBoneNode?.(boneName);
      if (normalized) return normalized;
    } catch (_) { /* malformed humanoid metadata */ }
    return humanoid.humanBones?.[boneName]?.node || null;
  }

  function countReversedVrmBonePairs(vrm) {
    const THREE = window.THREE;
    if (!THREE || !vrm?.scene) return { reversed: 0, checked: 0 };
    const pairs = [
      ['leftEye', 'rightEye'],
      ['leftUpperArm', 'rightUpperArm'],
      ['leftLowerArm', 'rightLowerArm'],
      ['leftHand', 'rightHand'],
    ];
    const leftPosition = new THREE.Vector3();
    const rightPosition = new THREE.Vector3();
    let reversed = 0;
    let checked = 0;
    vrm.scene.updateMatrixWorld(true);
    for (const [leftName, rightName] of pairs) {
      const left = vrmBoneNode(vrm, leftName);
      const right = vrmBoneNode(vrm, rightName);
      if (!left || !right) continue;
      left.getWorldPosition(leftPosition);
      right.getWorldPosition(rightPosition);
      if (!Number.isFinite(leftPosition.x) || !Number.isFinite(rightPosition.x)) continue;
      if (Math.abs(leftPosition.x - rightPosition.x) < 0.001) continue;
      checked += 1;
      if (leftPosition.x < rightPosition.x) reversed += 1;
    }
    return { reversed, checked };
  }

  function sampleVrmHeadFaceZ(vrm) {
    const THREE = window.THREE;
    if (!THREE || !vrm?.scene) return null;
    const namePattern = /(head|face|eye|eyeline|eyelash|hitomi|sirome|头|脸|眼|眉|睫|瞳)/i;
    const point = new THREE.Vector3();
    let positive = 0;
    let negative = 0;
    vrm.scene.updateMatrixWorld(true);
    vrm.scene.traverse((object) => {
      if (!object?.isMesh || !object.geometry?.attributes?.position) return;
      const materialNames = Array.isArray(object.material)
        ? object.material.map((material) => material?.name || '').join(' ')
        : (object.material?.name || '');
      if (!namePattern.test(`${object.name || ''} ${materialNames}`)) return;
      const positions = object.geometry.attributes.position;
      const step = Math.max(1, Math.floor(positions.count / 1200));
      for (let index = 0; index < positions.count; index += step) {
        point.fromBufferAttribute(positions, index);
        object.localToWorld(point);
        if (point.z > 0.001) positive += 1;
        else if (point.z < -0.001) negative += 1;
      }
    });
    return positive + negative > 0 ? { positive, negative } : null;
  }

  function applyVrm0FixedCameraFacingFix(gltf, vrm, manager) {
    let shouldNormalize = false;
    if (isVrm0(gltf, vrm)) {
      const bonePairs = countReversedVrmBonePairs(vrm);
      const headFaceZ = sampleVrmHeadFaceZ(vrm);
      shouldNormalize = bonePairs.reversed >= 2 || (
        bonePairs.reversed === 1
        && !!headFaceZ
        && headFaceZ.negative > headFaceZ.positive * 1.25
      );
    }
    if (manager) manager.__soccerFixedCameraNormalizeYaw = shouldNormalize;
    if (shouldNormalize && vrm?.scene?.rotation) {
      vrm.scene.rotation.y = Math.PI;
      vrm.scene.updateMatrixWorld?.(true);
    }
    return shouldNormalize;
  }

  async function loadVrmIntoManager(manager, path, options = {}) {
    const {
      canvasId,
      containerId,
      label = 'VRM',
      viewport = null,
      fit = {},
      signal,
      isCurrent,
      assertLive,
    } = options;
    assertLive();
    if (!manager) throw new Error(`${label}: VRM manager missing`);
    if (!path) throw new Error(`${label}: VRM path required`);
    if (!canvasId || !containerId) throw new Error(`${label}: canvas/container required`);
    if (!manager.scene || !manager.camera || !manager.renderer) {
      await manager.core.init(canvasId, containerId, null, {
        embed: true,
        resizeMode: 'fixed',
      });
      // init can allocate renderer resources after the controller was disposed.
      assertLive(() => observeAsyncDisposal(manager.dispose?.(), label));
    }
    const [{ GLTFLoader }, vrmModule] = await Promise.all([
      import('three/addons/loaders/GLTFLoader.js'),
      import('@pixiv/three-vrm'),
    ]);
    assertLive();
    const loader = new GLTFLoader();
    loader.register((parser) => new vrmModule.VRMLoaderPlugin(parser));
    const gltf = await new Promise((resolve, reject) => loader.load(path, resolve, null, reject));
    const vrm = gltf.userData.vrm;
    // The loader has no AbortSignal contract. Release a late scene before any
    // attachment or animation touches a disposed manager.
    assertLive(() => vrmModule.VRMUtils.deepDispose(vrm?.scene || gltf.scene));
    if (!vrm) throw new Error(`${label}: loaded file is not a valid VRM`);
    applyVrm0FixedCameraFacingFix(gltf, vrm, manager);

    if (manager.currentModel?.vrm?.scene) {
      const oldScene = manager.currentModel.vrm.scene;
      window.NekoMiniGameAvatarHost.releasePerspectiveReference(oldScene, manager.camera);
      manager.scene.remove(oldScene);
      try { vrmModule.VRMUtils?.deepDispose?.(oldScene); }
      catch (error) { console.warn(`[${label}] deepDispose failed:`, error); }
    }
    manager.scene.add(vrm.scene);
    manager.currentModel = { vrm, gltf, scene: vrm.scene, url: path };
    // Do not display or fit the loader's T-pose. The reference animation is
    // prepared below within this controller's cancellable load lifecycle.
    vrm.scene.visible = false;

    if (manager.renderer?.domElement) {
      manager.renderer.domElement.style.opacity = '1';
      manager.renderer.domElement.style.display = 'block';
    }
    if (typeof manager.startAnimateLoop === 'function' && !manager._animationFrameId) {
      manager.startAnimateLoop();
    }
    manager._initMouseLookAtTracking?.();
    manager.interaction?.enableMouseTracking?.(true);
    manager._cursorFollow?.setEnabled?.(true);
    const modelName = path.split('/').pop()?.replace(/\.vrm$/i, '') || '';
    try { await manager.expression?.loadMoodMap?.(modelName); }
    catch (error) { console.warn(`[${label}] mood map load failed:`, error); }
    assertLive();
    // Soccer already presents wait03 as its idle, so preparation also starts
    // its presentation animation; there is no second animation load here.
    await window.NekoMiniGameAvatarHost.preparePerspectiveReference(window.THREE, manager, {
      type: 'vrm', signal, isCurrent,
    });
    assertLive();
    fitVrmManagerCamera(manager, containerId, label, viewport, fit);
    vrm.scene.visible = true;
    return manager.currentModel;
  }

  function observeAsyncDisposal(result, label) {
    if (result && typeof result.catch === 'function') {
      result.catch((error) => console.warn(`[soccer-avatar-host] ${label} dispose failed:`, error));
    }
  }

  window.createSoccerAvatarHost = function createSoccerAvatarHost(options = {}) {
    if (!window.NekoMiniGameAvatarHost?.create) {
      throw new Error('NekoMiniGameAvatarHost is unavailable');
    }
    const onAvatarChanged = typeof options.onAvatarChanged === 'function'
      ? options.onAvatarChanged
      : () => {};
    let metadataHost = null;
    let hostDisposed = false;
    function createExtendedHost() {
      if (hostDisposed) throw new Error('soccer Avatar host is disposed');
      if (!window.NekoMiniGameDrawingAvatarHost?.create) throw new Error('Character renderer is unavailable');
      return window.NekoMiniGameDrawingAvatarHost.create({
        windowImpl: window, documentImpl: document, fetchImpl: options.fetchImpl,
        slot: 'ai', containerId: 'ai-l2d-container', extendedOnly: true,
        mmdContainerId: 'soccer-mmd-container', mmdCanvasId: 'soccer-mmd-canvas',
        pngContainerId: 'soccer-pngtuber-container',
      });
    }
    async function getCharacter(name, requestOptions) {
      const base = await options.characterSource.getCharacter(name, requestOptions);
      if (!base || hostDisposed) return null;
      if (base.model && ['live2d', 'vrm'].includes(base.model.type)) return base;
      if (!metadataHost) metadataHost = createExtendedHost();
      const configured = await metadataHost.getCharacter(base.name, requestOptions);
      if (hostDisposed || requestOptions?.signal?.aborted) return null;
      return configured ? { ...base, ...configured } : base;
    }

    function markAiAvatar(type, path, ready = true) {
      window.__SoccerAiAvatar = { type, path: path || '', ready: !!ready };
    }

    function pauseAiRenderer(type) {
      try {
        if (type === 'live2d') window.live2dManager?.pauseRendering?.();
        else if (type === 'vrm') window.aiVrmManager?.pauseRendering?.();
      } catch (error) {
        console.warn(`[soccer-avatar-host] pause AI ${type} failed:`, error);
      }
    }

    function resumeAiRenderer(type) {
      try {
        if (type === 'live2d') window.live2dManager?.resumeRendering?.();
        else if (type === 'vrm') window.aiVrmManager?.resumeRendering?.();
      } catch (error) {
        console.warn(`[soccer-avatar-host] resume AI ${type} failed:`, error);
      }
    }

    async function ensureLive2DReady(viewport) {
      const manager = window.live2dManager;
      if (!manager) throw new Error('live2dManager missing');
      await manager.initPIXI('ai-l2d-canvas', 'ai-l2d-container', {
        width: viewport.width,
        height: viewport.height,
        resizeMode: 'fixed',
      });
      for (const name of ['setupFloatingButtons', 'setupHTMLLockIcon', 'setupReturnButtonContainerDrag']) {
        if (typeof manager[name] !== 'function') manager[name] = () => {};
      }
    }

    function focusAiVrm(point) {
      const follow = window.aiVrmManager?._cursorFollow;
      if (!follow) return false;
      follow._rawMouseX = point.x;
      follow._rawMouseY = point.y;
      follow._hasPointerInput = true;
      follow._lastPointerMoveAt = performance.now();
      if (follow.setEnabled && !follow.isEnabled?.()) follow.setEnabled(true);
      return true;
    }

    function focusAiLive2D(point) {
      const focusController = window.live2dManager?.currentModel?.internalModel?.focusController;
      const canvas = document.getElementById('ai-l2d-canvas');
      if (!focusController || !canvas) return false;
      const rect = canvas.getBoundingClientRect();
      if (rect.width < 10) return false;
      const centerX = rect.left + rect.width / 2;
      const centerY = rect.top + rect.height / 2;
      focusController.focus(
        Math.max(-1, Math.min(1, (point.x - centerX) / 400)),
        Math.max(-1, Math.min(1, -(point.y - centerY) / 400)),
      );
      return true;
    }

    function setAiEmotion(type, emotion) {
      try {
        if (type === 'vrm') window.aiVrmManager?.expression?.setMood?.(emotion);
        else window.live2dManager?.setEmotion?.(emotion);
        return true;
      } catch (_) {
        return false;
      }
    }

    function createController({ config, viewport, signal, fitLive2DModel }) {
      const slot = config.slot;
      if (!['player', 'ai'].includes(slot)) {
        throw new Error(`soccer avatar slot is unsupported: ${slot}`);
      }
      const state = {
        disposed: false,
        model: null,
        viewport,
        fit: config.fit || {},
        layout: null,
        managers: new Set(),
        pendingWaits: new Set(),
        paused: false,
        speaking: false,
        speechTarget: null,
        mouthCore: null,
        mouthParameter: '',
        mouthFrame: null,
      };
      const speechAnalyser = window.NekoMiniGameAvatarHost.createSpeechAnalyser();
      let extendedHost = null;
      let extendedController = null;
      function clearAiModel() {
        state.model = null;
        state.layout = null;
        markAiAvatar('none', '', false);
      }
      function releaseExtended() {
        const retiring = extendedHost;
        extendedHost = null;
        extendedController = null;
        // Once the extended renderer is released its old model is unavailable,
        // including while a replacement is loading or after that load fails.
        if (retiring && ['mmd', 'pngtuber'].includes(state.model?.type)) {
          clearAiModel();
        }
        return retiring?.dispose();
      }
      const onExtendedAbort = () => observeAsyncDisposal(releaseExtended(), 'extended');
      signal?.addEventListener?.('abort', onExtendedAbort, { once: true });

      function stopSpeaking() {
        if (extendedController) observeAsyncDisposal(extendedController.setSpeechPlayback({ active: false }), 'extended speech');
        state.speaking = false;
        speechAnalyser.clear();
        if (state.mouthFrame !== null) {
          window.cancelAnimationFrame?.(state.mouthFrame);
          state.mouthFrame = null;
        }
        try { state.speechTarget?.stopLipSync?.(); } catch (_) { /* renderer retired */ }
        try { state.mouthCore?.setParameterValueById?.(state.mouthParameter, 0); }
        catch (_) { /* renderer retired */ }
        state.speechTarget = null;
        state.mouthCore = null;
        state.mouthParameter = '';
      }

      function setSpeechPlayback(frame) {
        if (extendedController) return extendedController.setSpeechPlayback(frame);
        // The player slot represents the human, including when it is the only
        // mounted renderer during AI loading/failure. Never animate it for TTS.
        if (slot !== 'ai' || state.disposed || signal?.aborted || state.paused || !state.model
            || !frame?.active || !speechAnalyser.update(frame.mouthFrame)) {
          stopSpeaking();
          return false;
        }
        if (state.speaking) return true;
        try {
          if (state.model.type === 'vrm') {
            const manager = slot === 'player' ? window.vrmManager : window.aiVrmManager;
            if (typeof manager?.animation?.startLipSync !== 'function') return false;
            state.speechTarget = manager.animation;
            state.speaking = true;
            state.speechTarget.startLipSync(speechAnalyser);
          } else {
            const core = window.live2dManager?.currentModel?.internalModel?.coreModel;
            if (typeof core?.setParameterValueById !== 'function'
                || typeof window.requestAnimationFrame !== 'function') return false;
            const parameter = ['ParamMouthOpenY', 'ParamMouthOpen', 'ParamA', 'ParamO'].find(id => (
              typeof core.getParameterIndex !== 'function' || core.getParameterIndex(id) >= 0
            ));
            if (!parameter) return false;
            state.mouthCore = core;
            state.mouthParameter = parameter;
            state.speaking = true;
            // One fixed buffer and one RAF per active controller. SDK owns
            // playback identity and expiry; this provider only renders samples.
            const samples = new Uint8Array(512);
            let mouth = 0;
            const animate = () => {
              state.mouthFrame = null;
              if (!state.speaking || state.disposed || state.paused) return;
              try {
                speechAnalyser.getByteTimeDomainData(samples);
                let sum = 0;
                for (const byte of samples) sum += ((byte - 128) / 128) ** 2;
                mouth = mouth * 0.55 + Math.min(1, Math.sqrt(sum / samples.length) * 10) * 0.45;
                core.setParameterValueById(parameter, mouth);
                state.mouthFrame = window.requestAnimationFrame(animate);
              } catch (_) { stopSpeaking(); }
            };
            animate();
          }
          return state.speaking;
        } catch (_) {
          stopSpeaking();
          return false;
        }
      }

      function lifecycleError(code, message) {
        const error = new Error(message);
        error.code = code;
        if (code === 'cancelled') error.name = 'AbortError';
        return error;
      }

      function assertLive(cleanup) {
        if (!state.disposed && !signal?.aborted) return;
        try { cleanup?.(); }
        catch (error) { console.warn(`[soccer-avatar-host] ${slot} late cleanup failed:`, error); }
        throw lifecycleError(state.disposed ? 'disposed' : 'cancelled',
          `soccer avatar slot is no longer active: ${slot}`);
      }

      function waitForLive2DModel(manager, path, loadPromise, startedWithSameModel, loadToken) {
        return new Promise((resolve, reject) => {
          const startedAt = Date.now();
          let settled = false;
          let loadFinished = false;
          let abandoned = false;
          let abandonedToken = null;
          const waitState = { timer: null, cancel: null };
          const abandonLoad = () => {
            if (abandoned) return;
            abandoned = true;
            // Only invalidate the load this controller actually started. A
            // rejected busy request must not cancel another owner's token.
            if (loadToken != null && manager._activeLoadToken === loadToken) {
              abandonedToken = ++manager._activeLoadToken;
            }
          };
          const releaseLateModel = (result) => {
            if (!abandoned) return;
            const ownsCurrent = abandonedToken != null && manager._activeLoadToken === abandonedToken;
            const model = result || (ownsCurrent ? manager.currentModel : null);
            if (!model) return;
            if (manager.currentModel === model) manager.currentModel = null;
            try { if (!model.destroyed) model.destroy?.({ children: true }); }
            catch (error) { console.warn('[soccer-avatar-host] late Live2D cleanup failed:', error); }
          };
          const cleanup = () => {
            if (waitState.timer != null) {
              window.clearTimeout(waitState.timer);
              waitState.timer = null;
            }
            signal?.removeEventListener?.('abort', onAbort);
            state.pendingWaits.delete(waitState);
          };
          const finish = (callback, value) => {
            if (settled) return;
            if (callback === reject) {
              abandonLoad();
              releaseLateModel();
            }
            settled = true;
            cleanup();
            callback(value);
          };
          waitState.cancel = (code = 'cancelled', message = 'Live2D model loading was cancelled') => {
            finish(reject, lifecycleError(code, message));
          };
          const onAbort = () => waitState.cancel();
          const poll = () => {
            if (settled) return;
            if (waitState.timer != null) {
              window.clearTimeout(waitState.timer);
              waitState.timer = null;
            }
            if (state.disposed) {
              waitState.cancel('disposed', `soccer avatar slot is disposed: ${slot}`);
              return;
            }
            if (signal?.aborted) {
              onAbort();
              return;
            }
            const current = manager.currentModel;
            const currentUrl = current?.internalModel?.settings?.url || '';
            const matches = currentUrl === path || currentUrl.endsWith(path);
            if (loadFinished && current?.width > 0 && (matches || startedWithSameModel)) {
              finish(resolve);
              return;
            }
            if (Date.now() - startedAt > 20000) {
              finish(reject, new Error('Live2D model not ready within 20s'));
              return;
            }
            waitState.timer = window.setTimeout(poll, 150);
          };
          state.pendingWaits.add(waitState);
          signal?.addEventListener?.('abort', onAbort, { once: true });
          Promise.resolve(loadPromise).then((model) => {
            loadFinished = true;
            if (abandoned || state.disposed || signal?.aborted) {
              abandonLoad();
              // The manager's fallback path can assign a model before checking
              // its token. Release that exact late result without destroying a
              // replacement manager or a successor's current model.
              releaseLateModel(model);
              return;
            }
            poll();
          }).catch((error) => {
            finish(reject, error);
            releaseLateModel();
          });
          poll();
        });
      }

      return {
        setSpeechPlayback,
        async setModel(model) {
          assertLive();
          stopSpeaking();
          if (slot === 'player') {
            if (model.type !== 'vrm') throw new Error('player avatar: only vrm supported');
            if (typeof window.VRMManager !== 'function') throw new Error('VRMManager class not found');
            const manager = window.vrmManager || new window.VRMManager();
            window.vrmManager = manager;
            state.managers.add(manager);
            await loadVrmIntoManager(manager, model.path, {
              canvasId: 'player-vrm-canvas',
              containerId: 'player-vrm-container',
              label: 'Player',
              viewport: state.viewport,
              fit: state.fit,
              signal,
              isCurrent: () => !state.disposed && !signal?.aborted,
              assertLive,
            });
            assertLive();
            state.model = model;
            if (state.paused) manager.pauseRendering?.();
            onAvatarChanged('player', model, true);
            return;
          }

          const previousType = state.model?.type || window.__SoccerAiAvatar?.type;
          await releaseExtended();
          assertLive();
          const isExtended = ['mmd', 'pngtuber'].includes(model.type);
          // Legacy renderers become hidden and paused during this transition.
          // They are no longer usable even if creating/loading the new host
          // fails. Publish readiness only after the replacement succeeds.
          if (isExtended) clearAiModel();
          const legacyCanvas = document.getElementById('ai-l2d-canvas');
          if (legacyCanvas?.style) legacyCanvas.style.display = isExtended ? 'none' : 'block';
          for (const id of ['soccer-mmd-container', 'soccer-pngtuber-container']) {
            const layer = document.getElementById(id);
            if (layer) { layer.hidden = true; if (layer.style) layer.style.display = 'none'; }
          }
          if (model.type === 'vrm') {
            if (typeof window.VRMManager !== 'function') throw new Error('VRMManager class not found');
            const manager = window.aiVrmManager || new window.VRMManager();
            window.aiVrmManager = manager;
            state.managers.add(manager);
            pauseAiRenderer('live2d');
            try {
              await loadVrmIntoManager(manager, model.path, {
                canvasId: 'ai-l2d-canvas',
                containerId: 'ai-l2d-container',
                label: 'AI VRM',
                viewport: state.viewport,
                fit: state.fit,
                signal,
                isCurrent: () => !state.disposed && !signal?.aborted,
                assertLive,
              });
            } catch (error) {
              assertLive();
              if (previousType === 'live2d' && !state.paused) resumeAiRenderer('live2d');
              throw error;
            }
            assertLive();
            resumeAiRenderer('vrm');
          } else if (model.type === 'live2d') {
            const manager = window.live2dManager;
            if (!manager) throw new Error('live2dManager missing');
            state.managers.add(manager);
            pauseAiRenderer('vrm');
            try {
              await ensureLive2DReady(state.viewport);
              assertLive(() => manager.destroy?.());
              const previousUrl = manager.currentModel?.internalModel?.settings?.url || '';
              const startedWithSameModel = previousUrl === model.path || previousUrl.endsWith(model.path);
              const previousLoadToken = manager._activeLoadToken;
              const loadPromise = manager.loadModel(model.path);
              const loadToken = Number.isFinite(manager._activeLoadToken)
                && manager._activeLoadToken !== previousLoadToken ? manager._activeLoadToken : null;
              await waitForLive2DModel(manager, model.path, loadPromise, startedWithSameModel, loadToken);
            } catch (error) {
              assertLive();
              if (previousType === 'vrm' && !state.paused) resumeAiRenderer('vrm');
              throw error;
            }
            assertLive();
            resumeAiRenderer('live2d');
          } else if (isExtended) {
            pauseAiRenderer('live2d');
            pauseAiRenderer('vrm');
            extendedHost = createExtendedHost();
            try {
              const controller = await extendedHost.mount({
                ...config, slot: 'ai', model, viewport: { mode: 'fixed', ...state.viewport }, fit: state.fit,
                characterName: config.characterName || window.__SoccerResolvedLanlanName,
              });
              assertLive(() => controller.dispose());
              extendedController = controller;
              if (state.paused) controller.pause();
            } catch (error) {
              await releaseExtended();
              throw error;
            }
          } else {
            throw new Error('ai avatar: unsupported model type');
          }
          // Failed Live2D loads restore the old VRM. Keep its standing fit
          // reference until replacement commits; disposal also releases it.
          if (previousType === 'vrm' && model.type !== 'vrm') {
            window.NekoMiniGameAvatarHost.releasePerspectiveReference(
              window.aiVrmManager?.currentModel?.vrm?.scene, window.aiVrmManager?.camera,
            );
          }
          state.model = model;
          if (state.paused) pauseAiRenderer(model.type);
          markAiAvatar(model.type, model.path, true);
          onAvatarChanged('ai', model, true);
        },
        focus(point) {
          if (state.disposed || slot !== 'ai') return false;
          // Pitch coordinates must not translate a model inside its fixed viewport.
          if (extendedController) return false;
          return state.model?.type === 'vrm' ? focusAiVrm(point) : focusAiLive2D(point);
        },
        setEmotion(name) {
          if (state.disposed) return false;
          if (extendedController) return extendedController.setEmotion(name);
          if (slot === 'ai') return setAiEmotion(state.model?.type, name);
          window.vrmManager?.expression?.setMood?.(name);
          return true;
        },
        pause() {
          if (state.disposed) return false;
          state.paused = true;
          stopSpeaking();
          extendedController?.pause();
          if (slot === 'ai') pauseAiRenderer(state.model?.type);
          else window.vrmManager?.pauseRendering?.();
          return true;
        },
        resume() {
          if (state.disposed) return false;
          state.paused = false;
          extendedController?.resume();
          if (slot === 'ai') resumeAiRenderer(state.model?.type);
          else window.vrmManager?.resumeRendering?.();
          return true;
        },
        getState() {
          return {
            slot,
            ready: !state.disposed && !!state.model,
            paused: state.paused,
            speaking: extendedController?.getState?.().speaking || state.speaking,
            model: state.model ? { ...state.model } : null,
            layout: extendedController?.getState?.().layout || state.layout,
          };
        },
        resize(nextViewport, fit = state.fit) {
          if (state.disposed) return false;
          state.viewport = nextViewport;
          state.fit = fit;
          if (extendedController) return extendedController.resize(nextViewport, fit);
          if (state.model?.type === 'live2d') {
            const manager = window.live2dManager;
            const renderer = manager?.pixi_app?.renderer;
            if (renderer?.screen
                && (renderer.screen.width !== nextViewport.width
                  || renderer.screen.height !== nextViewport.height)) {
              renderer.resize(nextViewport.width, nextViewport.height);
            }
            const model = manager?.currentModel;
            if (model?.width > 0 && model?.height > 0) {
              state.layout = fitLive2DModel(model, nextViewport, fit);
              model.alpha = 1;
              if (manager.pixi_app?.view) manager.pixi_app.view.style.opacity = '1';
            }
            return true;
          }
          if (state.model?.type === 'vrm') {
            state.layout = fitVrmManagerCamera(
              slot === 'player' ? window.vrmManager : window.aiVrmManager,
              slot === 'player' ? 'player-vrm-container' : 'ai-l2d-container',
              slot === 'player' ? 'Player' : 'AI VRM',
              nextViewport,
              fit,
            );
            return true;
          }
          return false;
        },
        dispose() {
          if (state.disposed) return;
          stopSpeaking();
          state.disposed = true;
          signal?.removeEventListener?.('abort', onExtendedAbort);
          observeAsyncDisposal(releaseExtended(), 'extended');
          for (const waitState of Array.from(state.pendingWaits)) {
            waitState.cancel?.('disposed', `soccer avatar slot is disposed: ${slot}`);
          }
          state.pendingWaits.clear();
          for (const manager of state.managers) {
            if (manager === window.live2dManager) {
              try { manager.destroy?.(); }
              catch (error) { console.warn('[soccer-avatar-host] Live2D dispose failed:', error); }
            } else {
              window.NekoMiniGameAvatarHost.releasePerspectiveReference(
                manager.currentModel?.vrm?.scene, manager.camera,
              );
              try { observeAsyncDisposal(manager.dispose?.(), slot); }
              catch (error) { console.warn(`[soccer-avatar-host] ${slot} VRM dispose failed:`, error); }
            }
            if (slot === 'player' && window.vrmManager === manager) window.vrmManager = null;
            if (slot === 'ai' && window.aiVrmManager === manager) window.aiVrmManager = null;
          }
          state.managers.clear();
          if (slot === 'ai') markAiAvatar('none', '', false);
        },
      };
    }

    const rendererHost = window.NekoMiniGameAvatarHost.create({
      slots: {
        player: {
          containerId: 'player-vrm-container',
          createController,
        },
        ai: {
          containerId: 'ai-l2d-container',
          createController,
        },
      },
    });
    return Object.freeze({
      ...(options.characterSource ? {
        getCharacter,
        getCurrentCharacter: (requestOptions) => getCharacter('', requestOptions),
        listCharacters: (requestOptions) => options.characterSource.listCharacters(requestOptions),
      } : {}),
      mount: (config) => rendererHost.mount(config),
      get activeCount() { return rendererHost.activeCount; },
      get pendingCount() { return rendererHost.pendingCount; },
      dispose() {
        if (hostDisposed) return;
        hostDisposed = true;
        observeAsyncDisposal(metadataHost?.dispose(), 'metadata');
        metadataHost = null;
        return rendererHost.dispose();
      },
    });
  };
})();
