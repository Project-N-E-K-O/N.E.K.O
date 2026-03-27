/**
 * Avatar Portrait
 * 从当前已加载的 Live2D / VRM / MMD 模型中提取头像裁剪图。
 *
 * 设计目标：
 * 1. 不重建模型，不侵入现有渲染循环
 * 2. 统一输出接口，便于导出、分享卡、资料头像等场景复用
 * 3. 优先利用头骨/包围盒，尽量得到稳定的“头像感”构图
 */
(function attachAvatarPortrait(global) {
    'use strict';

    const DEFAULTS = Object.freeze({
        width: 512,
        height: 512,
        padding: 0.12,
        background: 'transparent',
        shape: 'square', // square | rounded | circle
        radius: 28,
        mimeType: 'image/png',
        quality: 0.92,
        includeBlob: false,
        includeDataUrl: false,
        modelType: null
    });

    function clamp(value, min, max) {
        return Math.min(max, Math.max(min, value));
    }

    function finiteOr(value, fallback) {
        return Number.isFinite(value) ? value : fallback;
    }

    function createError(message) {
        return new Error('[avatar-portrait] ' + message);
    }

    function createCanvasExportError(error) {
        const message = String(error?.message || error || '');
        if (message.includes('Tainted canvases may not be exported')) {
            return createError('当前模型画布已被跨域资源污染，暂时无法导出头像。请确保模型贴图/图片资源与当前页面同源，或服务端已正确设置 CORS。');
        }
        return createError(message || '头像画布导出失败');
    }

    function assertCanvasReady(canvas) {
        const width = finiteOr(canvas?.width, 0);
        const height = finiteOr(canvas?.height, 0);
        if (!canvas || width <= 0 || height <= 0) {
            throw createError('模型画布尚未就绪，无法提取头像');
        }
    }

    function normalizeModelType(modelType) {
        const raw = String(modelType || global.lanlan_config?.model_type || '').toLowerCase();
        if (raw === 'vrm') return 'vrm';
        if (raw === 'mmd') return 'mmd';
        if (raw === 'live2d') return 'live2d';
        if (raw === 'live3d') {
            const subType = String(global.lanlan_config?.live3d_sub_type || '').toLowerCase();
            if (subType === 'mmd') return 'mmd';
            if (subType === 'vrm') return 'vrm';
            if (global.mmdManager?.currentModel?.mesh) return 'mmd';
            return 'vrm';
        }
        if (global.mmdManager?.currentModel?.mesh) return 'mmd';
        if (global.vrmManager?.currentModel?.vrm?.scene) return 'vrm';
        if (global.live2dManager?.getCurrentModel?.()) return 'live2d';
        return 'live2d';
    }

    function getCanvasMetrics(canvas) {
        const rect = canvas?.getBoundingClientRect?.();
        const cssWidth = finiteOr(rect?.width, 0) || finiteOr(canvas?.clientWidth, 0) || finiteOr(canvas?.width, 0) || 1;
        const cssHeight = finiteOr(rect?.height, 0) || finiteOr(canvas?.clientHeight, 0) || finiteOr(canvas?.height, 0) || 1;
        const pixelWidth = finiteOr(canvas?.width, 0) || Math.round(cssWidth);
        const pixelHeight = finiteOr(canvas?.height, 0) || Math.round(cssHeight);
        return {
            rect,
            cssWidth,
            cssHeight,
            pixelWidth,
            pixelHeight,
            pixelRatioX: pixelWidth / cssWidth,
            pixelRatioY: pixelHeight / cssHeight
        };
    }

    function roundRectPath(ctx, x, y, width, height, radius) {
        const r = clamp(radius || 0, 0, Math.min(width, height) / 2);
        ctx.beginPath();
        if (r <= 0) {
            ctx.rect(x, y, width, height);
            return;
        }
        ctx.moveTo(x + r, y);
        ctx.arcTo(x + width, y, x + width, y + height, r);
        ctx.arcTo(x + width, y + height, x, y + height, r);
        ctx.arcTo(x, y + height, x, y, r);
        ctx.arcTo(x, y, x + width, y, r);
        ctx.closePath();
    }

    function clipOutputShape(ctx, width, height, options) {
        if (options.shape === 'circle') {
            ctx.beginPath();
            ctx.arc(width / 2, height / 2, Math.min(width, height) / 2, 0, Math.PI * 2);
            ctx.clip();
            return;
        }
        if (options.shape === 'rounded') {
            roundRectPath(ctx, 0, 0, width, height, options.radius);
            ctx.clip();
        }
    }

    function maybeFillBackground(ctx, width, height, background) {
        if (!background || background === 'transparent') {
            return;
        }
        ctx.fillStyle = background;
        ctx.fillRect(0, 0, width, height);
    }

    function projectWorldToCss(worldPosition, camera, metrics, Vector3Ctor) {
        const point = worldPosition.clone ? worldPosition.clone() : new Vector3Ctor(worldPosition.x, worldPosition.y, worldPosition.z);
        point.project(camera);
        return {
            x: (point.x * 0.5 + 0.5) * metrics.cssWidth,
            y: (-point.y * 0.5 + 0.5) * metrics.cssHeight
        };
    }

    function computeProjectedBoxCss(object3D, camera, metrics, THREE) {
        const box = new THREE.Box3().setFromObject(object3D);
        if (!Number.isFinite(box.min.x) || !Number.isFinite(box.max.x)) {
            throw createError('无法计算模型包围盒');
        }

        const corners = [
            new THREE.Vector3(box.min.x, box.min.y, box.min.z),
            new THREE.Vector3(box.min.x, box.min.y, box.max.z),
            new THREE.Vector3(box.min.x, box.max.y, box.min.z),
            new THREE.Vector3(box.min.x, box.max.y, box.max.z),
            new THREE.Vector3(box.max.x, box.min.y, box.min.z),
            new THREE.Vector3(box.max.x, box.min.y, box.max.z),
            new THREE.Vector3(box.max.x, box.max.y, box.min.z),
            new THREE.Vector3(box.max.x, box.max.y, box.max.z)
        ];

        let minX = Infinity;
        let maxX = -Infinity;
        let minY = Infinity;
        let maxY = -Infinity;

        for (const corner of corners) {
            corner.project(camera);
            const x = (corner.x * 0.5 + 0.5) * metrics.cssWidth;
            const y = (-corner.y * 0.5 + 0.5) * metrics.cssHeight;
            minX = Math.min(minX, x);
            maxX = Math.max(maxX, x);
            minY = Math.min(minY, y);
            maxY = Math.max(maxY, y);
        }

        return sanitizeCssRect({
            x: minX,
            y: minY,
            width: maxX - minX,
            height: maxY - minY
        }, metrics);
    }

    function sanitizeCssRect(rect, metrics) {
        const width = Math.max(1, finiteOr(rect?.width, 0));
        const height = Math.max(1, finiteOr(rect?.height, 0));
        const x = clamp(finiteOr(rect?.x, 0), -metrics.cssWidth, metrics.cssWidth * 2);
        const y = clamp(finiteOr(rect?.y, 0), -metrics.cssHeight, metrics.cssHeight * 2);
        return { x, y, width, height };
    }

    function expandRect(rect, factor) {
        const extraX = rect.width * factor;
        const extraY = rect.height * factor;
        return {
            x: rect.x - extraX,
            y: rect.y - extraY,
            width: rect.width + extraX * 2,
            height: rect.height + extraY * 2
        };
    }

    function makePortraitRectFromAnchor(anchor, subjectRect, options) {
        const aspect = Math.max(0.1, options.width / options.height);
        const subjectWidth = Math.max(1, subjectRect.width);
        const subjectHeight = Math.max(1, subjectRect.height);
        const baseSize = Math.max(subjectWidth, subjectHeight);
        const portraitWidth = Math.max(
            subjectWidth * 1.02,
            baseSize * 0.58
        );
        const portraitHeight = Math.max(
            subjectHeight * 0.64,
            portraitWidth / aspect
        );
        const centerX = anchor.x;
        const centerY = anchor.y + portraitHeight * 0.17;
        return {
            x: centerX - portraitWidth / 2,
            y: centerY - portraitHeight / 2,
            width: portraitWidth,
            height: portraitHeight
        };
    }

    function makeHeadshotRectFromAnchor(anchor, headSize, options, config = {}) {
        const aspect = Math.max(0.1, options.width / options.height);
        const widthInHeads = finiteOr(config.widthInHeads, 2.1);
        const heightInHeads = finiteOr(config.heightInHeads, 2.5);
        const yOffsetInHeads = finiteOr(config.yOffsetInHeads, 0.4);

        let width = Math.max(1, headSize * widthInHeads);
        let height = Math.max(1, headSize * heightInHeads);

        if ((width / height) < aspect) {
            width = height * aspect;
        } else {
            height = width / aspect;
        }

        return {
            x: anchor.x - width / 2,
            y: anchor.y + headSize * yOffsetInHeads - height / 2,
            width,
            height
        };
    }

    function makeUpperBodyRect(subjectRect, options, biasY) {
        const aspect = Math.max(0.1, options.width / options.height);
        const portraitWidth = Math.max(subjectRect.width * 1.04, subjectRect.height * 0.58 * aspect);
        const portraitHeight = Math.max(subjectRect.height * 0.64, portraitWidth / aspect);
        const centerX = subjectRect.x + subjectRect.width / 2;
        const centerY = subjectRect.y + subjectRect.height * biasY;
        return {
            x: centerX - portraitWidth / 2,
            y: centerY - portraitHeight / 2,
            width: portraitWidth,
            height: portraitHeight
        };
    }

    function makeSubjectFallbackHeadshotRect(subjectRect, options, config = {}) {
        const subjectWidth = Math.max(1, subjectRect.width);
        const subjectHeight = Math.max(1, subjectRect.height);
        const estimatedHeadSize = Math.max(
            subjectWidth * finiteOr(config.widthFactor, 0.33),
            subjectHeight * finiteOr(config.heightFactor, 0.21)
        );

        return makeHeadshotRectFromAnchor({
            x: subjectRect.x + subjectWidth * finiteOr(config.anchorX, 0.5),
            y: subjectRect.y + subjectHeight * finiteOr(config.anchorY, 0.16)
        }, estimatedHeadSize, options, {
            widthInHeads: finiteOr(config.widthInHeads, 1.72),
            heightInHeads: finiteOr(config.heightInHeads, 1.95),
            yOffsetInHeads: finiteOr(config.yOffsetInHeads, 0.24)
        });
    }

    function applyPadding(rect, options) {
        return expandRect(rect, clamp(options.padding, 0, 0.5));
    }

    function clampRectToCanvas(rect, metrics) {
        const x = clamp(rect.x, 0, metrics.cssWidth - 1);
        const y = clamp(rect.y, 0, metrics.cssHeight - 1);
        const right = clamp(rect.x + rect.width, x + 1, metrics.cssWidth);
        const bottom = clamp(rect.y + rect.height, y + 1, metrics.cssHeight);
        return {
            x,
            y,
            width: Math.max(1, right - x),
            height: Math.max(1, bottom - y)
        };
    }

    function cssRectToPixelRect(rect, metrics) {
        return {
            x: Math.round(rect.x * metrics.pixelRatioX),
            y: Math.round(rect.y * metrics.pixelRatioY),
            width: Math.max(1, Math.round(rect.width * metrics.pixelRatioX)),
            height: Math.max(1, Math.round(rect.height * metrics.pixelRatioY))
        };
    }

    function createOutputCanvas(width, height) {
        const canvas = document.createElement('canvas');
        canvas.width = Math.max(1, Math.round(width));
        canvas.height = Math.max(1, Math.round(height));
        return canvas;
    }

    function canvasToBlob(canvas, mimeType, quality) {
        return new Promise((resolve, reject) => {
            try {
                canvas.toBlob((blob) => {
                    if (blob) {
                        resolve(blob);
                        return;
                    }
                    reject(createError('无法将头像画布编码为 Blob'));
                }, mimeType, quality);
            } catch (error) {
                reject(createCanvasExportError(error));
            }
        });
    }

    function canvasToDataUrl(canvas, mimeType, quality) {
        try {
            return canvas.toDataURL(mimeType, quality);
        } catch (error) {
            throw createCanvasExportError(error);
        }
    }

    function savePixiDisplayState(displayObject) {
        return {
            x: displayObject.x,
            y: displayObject.y,
            scaleX: displayObject.scale?.x,
            scaleY: displayObject.scale?.y,
            rotation: displayObject.rotation,
            skewX: displayObject.skew?.x,
            skewY: displayObject.skew?.y,
            pivotX: displayObject.pivot?.x,
            pivotY: displayObject.pivot?.y,
            visible: displayObject.visible,
            alpha: displayObject.alpha,
            anchorX: typeof displayObject.anchor?.x === 'number' ? displayObject.anchor.x : null,
            anchorY: typeof displayObject.anchor?.y === 'number' ? displayObject.anchor.y : null
        };
    }

    function restorePixiDisplayState(displayObject, state) {
        displayObject.x = state.x;
        displayObject.y = state.y;
        if (displayObject.scale && Number.isFinite(state.scaleX) && Number.isFinite(state.scaleY)) {
            displayObject.scale.set(state.scaleX, state.scaleY);
        }
        displayObject.rotation = state.rotation;
        if (displayObject.skew && Number.isFinite(state.skewX) && Number.isFinite(state.skewY)) {
            displayObject.skew.set(state.skewX, state.skewY);
        }
        if (displayObject.pivot && Number.isFinite(state.pivotX) && Number.isFinite(state.pivotY)) {
            displayObject.pivot.set(state.pivotX, state.pivotY);
        }
        if (displayObject.anchor && state.anchorX !== null && state.anchorY !== null) {
            displayObject.anchor.set(state.anchorX, state.anchorY);
        }
        displayObject.visible = state.visible;
        displayObject.alpha = state.alpha;
    }

    function getPixiExtractCanvas(renderer, target) {
        if (renderer?.plugins?.extract?.canvas) {
            return renderer.plugins.extract.canvas(target);
        }
        if (renderer?.extract?.canvas) {
            return renderer.extract.canvas(target);
        }
        throw createError('当前 PIXI 渲染器不支持离屏头像提取');
    }

    function getLive2dDrawableLogicalRect(internalModel, drawableIndex) {
        if (!internalModel || typeof internalModel.getDrawableBounds !== 'function') {
            return null;
        }

        const rect = internalModel.getDrawableBounds(drawableIndex, {});
        if (!rect || !Number.isFinite(rect.x) || !Number.isFinite(rect.y) ||
            !Number.isFinite(rect.width) || !Number.isFinite(rect.height)) {
            return null;
        }

        return {
            x: rect.x,
            y: rect.y,
            width: Math.max(1, rect.width),
            height: Math.max(1, rect.height)
        };
    }

    function getLive2dModelLogicalRect(model) {
        const internalModel = model?.internalModel;
        const drawableCount = internalModel?.coreModel?.getDrawableCount?.();
        if (!internalModel || !Number.isInteger(drawableCount) || drawableCount <= 0) {
            return null;
        }

        let minX = Infinity;
        let maxX = -Infinity;
        let minY = Infinity;
        let maxY = -Infinity;

        for (let index = 0; index < drawableCount; index += 1) {
            const rect = getLive2dDrawableLogicalRect(internalModel, index);
            if (!rect) continue;
            minX = Math.min(minX, rect.x);
            maxX = Math.max(maxX, rect.x + rect.width);
            minY = Math.min(minY, rect.y);
            maxY = Math.max(maxY, rect.y + rect.height);
        }

        if (!Number.isFinite(minX) || !Number.isFinite(maxX) || !Number.isFinite(minY) || !Number.isFinite(maxY)) {
            return null;
        }

        return {
            x: minX,
            y: minY,
            width: Math.max(1, maxX - minX),
            height: Math.max(1, maxY - minY)
        };
    }

    function mapLive2dLogicalRectToCss(logicalRect, modelLogicalRect, modelBoundsCss, metrics) {
        if (!logicalRect || !modelLogicalRect || !modelBoundsCss) {
            return null;
        }

        const logicalWidth = Math.max(1, modelLogicalRect.width);
        const logicalHeight = Math.max(1, modelLogicalRect.height);

        const relLeft = (logicalRect.x - modelLogicalRect.x) / logicalWidth;
        const relTop = (logicalRect.y - modelLogicalRect.y) / logicalHeight;
        const relWidth = logicalRect.width / logicalWidth;
        const relHeight = logicalRect.height / logicalHeight;

        return sanitizeCssRect({
            x: modelBoundsCss.x + modelBoundsCss.width * relLeft,
            y: modelBoundsCss.y + modelBoundsCss.height * relTop,
            width: modelBoundsCss.width * relWidth,
            height: modelBoundsCss.height * relHeight
        }, metrics);
    }

    function getLive2dHeadRect(model, metrics) {
        const internalModel = model?.internalModel;
        const rawHitAreas = internalModel?.hitAreas;
        if (!rawHitAreas || typeof rawHitAreas !== 'object') {
            return null;
        }

        const entries = Object.keys(rawHitAreas)
            .map((key) => rawHitAreas[key])
            .filter((item) => item && Number.isInteger(item.index));

        if (entries.length === 0) {
            return null;
        }

        const matchers = [
            /(^|[^a-z])face([^a-z]|$)/i,
            /(^|[^a-z])head([^a-z]|$)/i,
            /hitareaface/i,
            /hitareahead/i,
            /顔|脸|頭/
        ];

        const preferredEntry = entries.find((entry) => {
            const haystack = String(entry.name || entry.id || '').toLowerCase();
            return matchers.some((matcher) => matcher.test(haystack));
        });

        if (!preferredEntry) {
            return null;
        }

        const logicalHeadRect = getLive2dDrawableLogicalRect(internalModel, preferredEntry.index);
        const logicalModelRect = getLive2dModelLogicalRect(model);
        const modelBoundsCss = sanitizeCssRect(model.getBounds(), metrics);
        const rect = mapLive2dLogicalRectToCss(logicalHeadRect, logicalModelRect, modelBoundsCss, metrics);
        if (!logicalHeadRect || !logicalModelRect || !rect) {
            return null;
        }

        return rect;
    }

    function buildLive2dHeadshotRect(model, metrics, options) {
        const bounds = sanitizeCssRect(model.getBounds(), metrics);
        const headRect = getLive2dHeadRect(model, metrics);

        if (headRect) {
            const headSize = Math.max(headRect.height, headRect.width * 1.06);
            return makeHeadshotRectFromAnchor({
                x: headRect.x + headRect.width / 2,
                y: headRect.y + headRect.height * 0.42
            }, headSize, options, {
                widthInHeads: 1.46,
                heightInHeads: 1.68,
                yOffsetInHeads: 0.18
            });
        }

        return makeSubjectFallbackHeadshotRect(bounds, options, {
            widthFactor: 0.3,
            heightFactor: 0.2,
            anchorY: 0.15,
            widthInHeads: 1.52,
            heightInHeads: 1.74,
            yOffsetInHeads: 0.2
        });
    }

    function renderLive2dPortraitSource(ctx, options) {
        const PIXI = global.PIXI;
        const renderer = ctx.app?.renderer;
        if (!PIXI || !renderer || typeof renderer.generateTexture !== 'function') {
            return null;
        }

        const model = ctx.model;
        const originalParent = model.parent || null;
        const originalIndex = originalParent && typeof originalParent.getChildIndex === 'function'
            ? originalParent.getChildIndex(model)
            : -1;
        const savedState = savePixiDisplayState(model);
        const tempStage = new PIXI.Container();
        const scaleSignX = savedState.scaleX < 0 ? -1 : 1;
        const scaleSignY = savedState.scaleY < 0 ? -1 : 1;
        let renderTexture = null;

        try {
            if (originalParent) {
                originalParent.removeChild(model);
            }
            tempStage.addChild(model);

            model.visible = true;
            model.alpha = 1;
            const viewportWidth = Math.max(768, Math.round(options.width * 2));
            const viewportHeight = Math.max(768, Math.round(options.height * 2));
            const viewportMetrics = {
                cssWidth: viewportWidth,
                cssHeight: viewportHeight,
                pixelWidth: viewportWidth,
                pixelHeight: viewportHeight,
                pixelRatioX: 1,
                pixelRatioY: 1
            };

            model.x = viewportWidth * 0.5;
            model.y = viewportHeight * 0.62;
            if (model.scale) {
                model.scale.set(scaleSignX, scaleSignY);
            }
            if (model.anchor) {
                model.anchor.set(savedState.anchorX ?? 0.5, savedState.anchorY ?? 0.5);
            }

            const targetHeadHeight = viewportHeight * 0.6;
            const targetHeadCenterX = viewportWidth * 0.5;
            const targetHeadCenterY = viewportHeight * 0.35;

            for (let pass = 0; pass < 3; pass += 1) {
                renderer.render(tempStage);

                const headRect = getLive2dHeadRect(model, viewportMetrics);
                const bounds = sanitizeCssRect(model.getBounds(), viewportMetrics);
                const activeHeadRect = headRect || {
                    x: bounds.x + bounds.width * 0.28,
                    y: bounds.y + bounds.height * 0.08,
                    width: Math.max(1, bounds.width * 0.42),
                    height: Math.max(1, bounds.height * 0.3)
                };

                const currentHeadHeight = Math.max(activeHeadRect.height, activeHeadRect.width * 0.92, 1);
                const scaleAdjust = clamp(targetHeadHeight / currentHeadHeight, 0.35, 3.2);

                if (Math.abs(scaleAdjust - 1) > 0.02 && model.scale) {
                    model.scale.set(model.scale.x * scaleAdjust, model.scale.y * scaleAdjust);
                    renderer.render(tempStage);
                }

                const adjustedHeadRect = getLive2dHeadRect(model, viewportMetrics) || activeHeadRect;
                const adjustedHeadCenterX = adjustedHeadRect.x + adjustedHeadRect.width / 2;
                const adjustedHeadCenterY = adjustedHeadRect.y + adjustedHeadRect.height * 0.42;

                model.x += targetHeadCenterX - adjustedHeadCenterX;
                model.y += targetHeadCenterY - adjustedHeadCenterY;
            }

            renderer.render(tempStage);

            const resolution = Math.max(2, Math.ceil(global.devicePixelRatio || 1));
            renderTexture = renderer.generateTexture(tempStage, {
                region: new PIXI.Rectangle(0, 0, viewportWidth, viewportHeight),
                resolution
            });

            const extractedCanvas = getPixiExtractCanvas(renderer, renderTexture);
            const cropRectCss = clampRectToCanvas(
                applyPadding(buildLive2dHeadshotRect(model, viewportMetrics, options), options),
                viewportMetrics
            );
            const cropRectPixels = cssRectToPixelRect(cropRectCss, {
                ...viewportMetrics,
                pixelWidth: extractedCanvas.width,
                pixelHeight: extractedCanvas.height,
                pixelRatioX: extractedCanvas.width / viewportWidth,
                pixelRatioY: extractedCanvas.height / viewportHeight
            });
            return {
                canvas: extractedCanvas,
                cropRectCss,
                cropRectPixels,
                sourceCanvas: extractedCanvas,
                modelType: 'live2d'
            };
        } catch (error) {
            console.warn('[avatar-portrait] Live2D 离屏头像渲染失败，回退到屏幕画布裁剪:', error);
            return null;
        } finally {
            restorePixiDisplayState(model, savedState);
            tempStage.removeChild(model);
            if (originalParent) {
                if (originalIndex >= 0 && originalIndex <= originalParent.children.length) {
                    originalParent.addChildAt(model, originalIndex);
                } else {
                    originalParent.addChild(model);
                }
            }
            try {
                renderer.render(ctx.app.stage);
            } catch (_) {}
            if (renderTexture && typeof renderTexture.destroy === 'function') {
                renderTexture.destroy(true);
            }
        }
    }

    function getLive2DAdapter() {
        return {
            type: 'live2d',
            getContext() {
                const manager = global.live2dManager;
                const model = manager?.getCurrentModel?.();
                const app = manager?.getPIXIApp?.() || manager?.pixi_app;
                const canvas = app?.renderer?.view || document.getElementById('live2d-canvas');
                if (!manager || !model || !app || !canvas) {
                    throw createError('当前没有可用的 Live2D 模型');
                }
                if (manager._isModelReadyForInteraction === false) {
                    throw createError('Live2D 模型仍在加载中，请稍后再试');
                }
                return { manager, model, app, canvas };
            },
            prepare(ctx) {
                try {
                    ctx.app.renderer.render(ctx.app.stage);
                } catch (_) {}
            },
            renderSource(ctx, options) {
                return renderLive2dPortraitSource(ctx, options);
            },
            getCropRect(ctx, options) {
                const metrics = getCanvasMetrics(ctx.canvas);
                return clampRectToCanvas(
                    applyPadding(buildLive2dHeadshotRect(ctx.model, metrics, options), options),
                    metrics
                );
            }
        };
    }

    function getVrmHeadAnchor(model, camera, metrics, THREE) {
        const humanoid = model?.vrm?.humanoid;
        if (!humanoid) return null;

        const headBone = humanoid.getNormalizedBoneNode('head');
        const neckBone = humanoid.getNormalizedBoneNode('neck');
        if (!headBone) return null;

        headBone.updateMatrixWorld(true);
        const headWorld = new THREE.Vector3();
        headBone.getWorldPosition(headWorld);
        const headCss = projectWorldToCss(headWorld, camera, metrics, THREE.Vector3);

        let headHeight = 0;
        if (neckBone) {
            neckBone.updateMatrixWorld(true);
            const neckWorld = new THREE.Vector3();
            neckBone.getWorldPosition(neckWorld);
            const neckCss = projectWorldToCss(neckWorld, camera, metrics, THREE.Vector3);
            headHeight = Math.hypot(headCss.x - neckCss.x, headCss.y - neckCss.y) * 2.4;
        }

        return {
            x: headCss.x,
            y: headCss.y,
            headHeight
        };
    }

    function getVrmAdapter() {
        return {
            type: 'vrm',
            getContext() {
                const manager = global.vrmManager;
                const model = manager?.getCurrentModel?.() || manager?.currentModel;
                const canvas = manager?.renderer?.domElement || document.getElementById('vrm-canvas');
                if (!manager || !model?.vrm?.scene || !manager.camera || !canvas) {
                    throw createError('当前没有可用的 VRM 模型');
                }
                if (manager._isModelReadyForInteraction === false) {
                    throw createError('VRM 模型仍在加载中，请稍后再试');
                }
                return { manager, model, canvas };
            },
            prepare(ctx) {
                try {
                    ctx.manager.currentModel?.vrm?.scene?.updateMatrixWorld?.(true);
                    ctx.manager.renderer?.render?.(ctx.manager.scene, ctx.manager.camera);
                } catch (_) {}
            },
            getCropRect(ctx, options) {
                const THREE = global.THREE;
                if (!THREE) {
                    throw createError('THREE 尚未就绪，无法提取 VRM 头像');
                }

                const metrics = getCanvasMetrics(ctx.canvas);
                ctx.model.vrm.scene.updateMatrixWorld(true);
                const subjectRect = computeProjectedBoxCss(ctx.model.vrm.scene, ctx.manager.camera, metrics, THREE);
                const headAnchor = getVrmHeadAnchor(ctx.model, ctx.manager.camera, metrics, THREE);

                let portraitRect;
                if (headAnchor) {
                    const normalizedHeadHeight = Math.max(
                        headAnchor.headHeight,
                        subjectRect.height * 0.15,
                        subjectRect.width * 0.14
                    );
                    portraitRect = makeHeadshotRectFromAnchor({
                        x: headAnchor.x,
                        y: headAnchor.y
                    }, normalizedHeadHeight, options, {
                        widthInHeads: 1.68,
                        heightInHeads: 1.92,
                        yOffsetInHeads: 0.24
                    });
                } else {
                    portraitRect = makeSubjectFallbackHeadshotRect(subjectRect, options, {
                        widthFactor: 0.26,
                        heightFactor: 0.19,
                        anchorY: 0.14,
                        widthInHeads: 1.72,
                        heightInHeads: 1.96,
                        yOffsetInHeads: 0.24
                    });
                }

                return clampRectToCanvas(applyPadding(portraitRect, options), metrics);
            }
        };
    }

    function findMmdHeadAnchor(mesh, camera, metrics, THREE) {
        const bones = mesh?.skeleton?.bones;
        if (!Array.isArray(bones) || bones.length === 0) return null;

        const headExact = ['頭', 'head', 'Head', 'あたま'];
        const neckExact = ['首', 'neck', 'Neck', 'くび'];
        const headExclude = ['先端', 'tip', 'Tip', 'end', 'End'];

        let headBone = null;
        let neckBone = null;

        for (const bone of bones) {
            const name = String(bone?.name || '');
            if (!headBone && headExact.some((token) => name === token)) {
                headBone = bone;
            }
            if (!neckBone && neckExact.some((token) => name === token)) {
                neckBone = bone;
            }
            if (headBone && neckBone) break;
        }

        if (!headBone || !neckBone) {
            for (const bone of bones) {
                const name = String(bone?.name || '');
                if (!headBone && headExact.some((token) => name.includes(token)) &&
                    !headExclude.some((token) => name.includes(token))) {
                    headBone = bone;
                }
                if (!neckBone && neckExact.some((token) => name.includes(token))) {
                    neckBone = bone;
                }
                if (headBone && neckBone) break;
            }
        }

        if (!headBone) return null;

        headBone.updateMatrixWorld(true);
        const headWorld = new THREE.Vector3();
        headBone.getWorldPosition(headWorld);
        const headCss = projectWorldToCss(headWorld, camera, metrics, THREE.Vector3);

        let headHeight = 0;
        if (neckBone) {
            neckBone.updateMatrixWorld(true);
            const neckWorld = new THREE.Vector3();
            neckBone.getWorldPosition(neckWorld);
            const neckCss = projectWorldToCss(neckWorld, camera, metrics, THREE.Vector3);
            headHeight = Math.hypot(headCss.x - neckCss.x, headCss.y - neckCss.y) * 2.35;
        }

        return {
            x: headCss.x,
            y: headCss.y,
            headHeight
        };
    }

    function getMmdAdapter() {
        return {
            type: 'mmd',
            getContext() {
                const manager = global.mmdManager;
                const model = manager?.getCurrentModel?.() || manager?.currentModel;
                const canvas = manager?.renderer?.domElement || manager?.canvas || document.getElementById('mmd-canvas');
                if (!manager || !model?.mesh || !manager.camera || !canvas) {
                    throw createError('当前没有可用的 MMD 模型');
                }
                if (manager._isModelReadyForInteraction === false) {
                    throw createError('MMD 模型仍在加载中，请稍后再试');
                }
                return { manager, model, canvas };
            },
            prepare(ctx) {
                try {
                    ctx.model.mesh?.updateMatrixWorld?.(true);
                    ctx.manager.renderer?.render?.(ctx.manager.scene, ctx.manager.camera);
                } catch (_) {}
            },
            getCropRect(ctx, options) {
                const THREE = global.THREE;
                if (!THREE) {
                    throw createError('THREE 尚未就绪，无法提取 MMD 头像');
                }

                const metrics = getCanvasMetrics(ctx.canvas);
                ctx.model.mesh.updateMatrixWorld(true);
                const subjectRect = computeProjectedBoxCss(ctx.model.mesh, ctx.manager.camera, metrics, THREE);
                const headAnchor = findMmdHeadAnchor(ctx.model.mesh, ctx.manager.camera, metrics, THREE);

                let portraitRect;
                if (headAnchor) {
                    const normalizedHeadHeight = Math.max(
                        headAnchor.headHeight,
                        subjectRect.height * 0.14,
                        subjectRect.width * 0.13
                    );
                    portraitRect = makeHeadshotRectFromAnchor({
                        x: headAnchor.x,
                        y: headAnchor.y
                    }, normalizedHeadHeight, options, {
                        widthInHeads: 1.7,
                        heightInHeads: 1.96,
                        yOffsetInHeads: 0.26
                    });
                } else {
                    portraitRect = makeSubjectFallbackHeadshotRect(subjectRect, options, {
                        widthFactor: 0.25,
                        heightFactor: 0.18,
                        anchorY: 0.14,
                        widthInHeads: 1.74,
                        heightInHeads: 2.0,
                        yOffsetInHeads: 0.24
                    });
                }

                return clampRectToCanvas(applyPadding(portraitRect, options), metrics);
            }
        };
    }

    function getAdapter(modelType) {
        const normalizedType = normalizeModelType(modelType);
        if (normalizedType === 'vrm') return getVrmAdapter();
        if (normalizedType === 'mmd') return getMmdAdapter();
        return getLive2DAdapter();
    }

    async function capture(options = {}) {
        const finalOptions = { ...DEFAULTS, ...options };
        const adapter = getAdapter(finalOptions.modelType);
        const context = adapter.getContext();
        adapter.prepare(context);

        if (typeof adapter.renderSource === 'function') {
            const renderedSource = adapter.renderSource(context, finalOptions);
            if (renderedSource && renderedSource.canvas) {
                const outputCanvas = createOutputCanvas(finalOptions.width, finalOptions.height);
                const outputCtx = outputCanvas.getContext('2d');

                if (!outputCtx) {
                    throw createError('无法创建头像导出画布');
                }

                outputCtx.save();
                clipOutputShape(outputCtx, outputCanvas.width, outputCanvas.height, finalOptions);
                maybeFillBackground(outputCtx, outputCanvas.width, outputCanvas.height, finalOptions.background);

                const sourceCropRect = renderedSource.cropRectPixels || {
                    x: 0,
                    y: 0,
                    width: renderedSource.canvas.width,
                    height: renderedSource.canvas.height
                };
                outputCtx.drawImage(
                    renderedSource.canvas,
                    sourceCropRect.x,
                    sourceCropRect.y,
                    sourceCropRect.width,
                    sourceCropRect.height,
                    0,
                    0,
                    outputCanvas.width,
                    outputCanvas.height
                );
                outputCtx.restore();

                const result = {
                    modelType: renderedSource.modelType || adapter.type,
                    canvas: outputCanvas,
                    cropRectCss: renderedSource.cropRectCss || {
                        x: 0,
                        y: 0,
                        width: renderedSource.canvas.width,
                        height: renderedSource.canvas.height
                    },
                    cropRectPixels: renderedSource.cropRectPixels || {
                        x: 0,
                        y: 0,
                        width: renderedSource.canvas.width,
                        height: renderedSource.canvas.height
                    },
                    sourceCanvas: renderedSource.sourceCanvas || renderedSource.canvas
                };

                if (finalOptions.includeBlob) {
                    result.blob = await canvasToBlob(outputCanvas, finalOptions.mimeType, finalOptions.quality);
                }
                if (finalOptions.includeDataUrl) {
                    result.dataUrl = canvasToDataUrl(outputCanvas, finalOptions.mimeType, finalOptions.quality);
                }

                return result;
            }
        }

        const sourceCanvas = context.canvas;
        if (!sourceCanvas) {
            throw createError('找不到模型渲染画布');
        }
        assertCanvasReady(sourceCanvas);

        const sourceMetrics = getCanvasMetrics(sourceCanvas);
        const cssCropRect = adapter.getCropRect(context, finalOptions);
        const pixelCropRect = cssRectToPixelRect(cssCropRect, sourceMetrics);
        const outputCanvas = createOutputCanvas(finalOptions.width, finalOptions.height);
        const outputCtx = outputCanvas.getContext('2d');

        if (!outputCtx) {
            throw createError('无法创建头像导出画布');
        }

        outputCtx.save();
        clipOutputShape(outputCtx, outputCanvas.width, outputCanvas.height, finalOptions);
        maybeFillBackground(outputCtx, outputCanvas.width, outputCanvas.height, finalOptions.background);
        outputCtx.drawImage(
            sourceCanvas,
            pixelCropRect.x,
            pixelCropRect.y,
            pixelCropRect.width,
            pixelCropRect.height,
            0,
            0,
            outputCanvas.width,
            outputCanvas.height
        );
        outputCtx.restore();

        const result = {
            modelType: adapter.type,
            canvas: outputCanvas,
            cropRectCss: cssCropRect,
            cropRectPixels: pixelCropRect,
            sourceCanvas
        };

        if (finalOptions.includeBlob) {
            result.blob = await canvasToBlob(outputCanvas, finalOptions.mimeType, finalOptions.quality);
        }
        if (finalOptions.includeDataUrl) {
            result.dataUrl = canvasToDataUrl(outputCanvas, finalOptions.mimeType, finalOptions.quality);
        }

        return result;
    }

    async function captureToBlob(options = {}) {
        const result = await capture({ ...options, includeBlob: true });
        return result.blob;
    }

    async function captureToDataURL(options = {}) {
        const result = await capture({ ...options, includeDataUrl: true });
        return result.dataUrl;
    }

    const api = {
        normalizeModelType,
        capture,
        captureToBlob,
        captureToDataURL
    };

    global.avatarPortrait = api;
    global.captureCurrentAvatarPortrait = capture;
    global.getCurrentAvatarPortrait = capture;
})(window);
