
const TOUCH_CUSTOM_AREA_PREFIX = 'custom_'
const TOUCH_CUSTOM_AREA_MIN_RATIO = 0.01

function interpolateTouchAnimText(text, options) {
    if (!options || typeof text !== 'string') return text
    return text.replace(/\{\{\s*([A-Za-z0-9_]+)\s*\}\}/g, (match, name) => {
        const value = options[name]
        return value === null || value === undefined ? match : String(value)
    })
}

function touchAnimText(key, fallback, options) {
    const fullKey = key.startsWith('live2d.') ? key : `live2d.touchAnim.${key}`
    let text = fallback
    const params = Object.assign({}, options || {}, { defaultValue: fallback })

    if (window.i18next && typeof window.i18next.t === 'function') {
        text = window.i18next.t(fullKey, params)
    } else if (typeof window.t === 'function') {
        text = window.t(fullKey, params)
    }

    if (!text || text === fullKey) text = fallback
    return interpolateTouchAnimText(text, options)
}

function clampTouchAreaValue(value, min = 0, max = 1) {
    const n = Number(value)
    if (!Number.isFinite(n)) return min
    return Math.max(min, Math.min(max, n))
}

function normalizeCustomTouchAreaRect(rect) {
    if (!rect || typeof rect !== 'object') return null
    const x = clampTouchAreaValue(rect.x)
    const y = clampTouchAreaValue(rect.y)
    const width = clampTouchAreaValue(rect.width, 0, 1 - x)
    const height = clampTouchAreaValue(rect.height, 0, 1 - y)
    if (width < TOUCH_CUSTOM_AREA_MIN_RATIO || height < TOUCH_CUSTOM_AREA_MIN_RATIO) return null
    return { x, y, width, height }
}

function parseCustomTouchAreaCreatedAt(area, fallbackId) {
    const explicitCreatedAt = Number(area?.createdAt)
    if (Number.isFinite(explicitCreatedAt) && explicitCreatedAt > 0) return explicitCreatedAt

    const id = String(area?.id || fallbackId || '').trim()
    const match = id.match(/^custom_([0-9a-z]+)_/i)
    if (match) {
        const parsed = parseInt(match[1], 36)
        if (Number.isFinite(parsed) && parsed > 0) return parsed
    }
    return null
}

function normalizeCustomTouchArea(area, fallbackId) {
    if (!area || typeof area !== 'object') return null
    const rect = normalizeCustomTouchAreaRect(area.rect)
    if (!rect) return null
    const id = String(area.id || fallbackId || '').trim()
    if (!id) return null
    const normalized = {
        id,
        type: 'rect',
        name: String(area.name || id).trim() || id,
        rect
    }
    const createdAt = parseCustomTouchAreaCreatedAt(area, id)
    if (createdAt !== null) normalized.createdAt = createdAt
    return normalized
}

function normalizePixiBoundsRect(bounds) {
    if (!bounds) return null
    const firstFiniteNumber = (...values) => {
        for (const value of values) {
            const n = Number(value)
            if (Number.isFinite(n)) return n
        }
        return null
    }

    let width = firstFiniteNumber(bounds.width)
    let height = firstFiniteNumber(bounds.height)
    let left = firstFiniteNumber(bounds.left, bounds.x, bounds.minX)
    let top = firstFiniteNumber(bounds.top, bounds.y, bounds.minY)
    let right = firstFiniteNumber(bounds.right, bounds.maxX, left !== null && width !== null ? left + width : null)
    let bottom = firstFiniteNumber(bounds.bottom, bounds.maxY, top !== null && height !== null ? top + height : null)

    if ((width === null || width <= 0) && left !== null && right !== null) width = right - left
    if ((height === null || height <= 0) && top !== null && bottom !== null) height = bottom - top
    if (left === null && right !== null && width !== null) left = right - width
    if (top === null && bottom !== null && height !== null) top = bottom - height
    if (right === null && left !== null && width !== null) right = left + width
    if (bottom === null && top !== null && height !== null) bottom = top + height

    if (![left, top, right, bottom, width, height].every(Number.isFinite)) return null
    if (width <= 0 || height <= 0) return null

    return { left, top, right, bottom, width, height }
}

function getCustomTouchAreaSortValue(area, fallbackIndex = 0) {
    const createdAt = parseCustomTouchAreaCreatedAt(area, area?.id)
    if (createdAt !== null) return createdAt
    return Number.MAX_SAFE_INTEGER + fallbackIndex
}

function compareCustomTouchAreaRecords(a, b) {
    const orderA = getCustomTouchAreaSortValue(a.area, a.index)
    const orderB = getCustomTouchAreaSortValue(b.area, b.index)
    if (orderA !== orderB) return orderA - orderB
    return a.index - b.index
}

function getCustomTouchAreaRecordsFromSet(touchSet, nativeIds = new Set()) {
    return Object.entries(touchSet || {})
        .map(([id, entry], index) => ({
            area: normalizeCustomTouchArea(entry?.customArea, id),
            index
        }))
        .filter(record => record.area && !nativeIds.has(record.area.id))
        .sort(compareCustomTouchAreaRecords)
}

function rectIntersection(a, b) {
    if (!a || !b) return null
    const left = Math.max(a.x, b.x)
    const top = Math.max(a.y, b.y)
    const right = Math.min(a.x + a.width, b.x + b.width)
    const bottom = Math.min(a.y + a.height, b.y + b.height)
    if (right <= left || bottom <= top) return null
    return { x: left, y: top, width: right - left, height: bottom - top }
}

function subtractRect(rect, cutter, minSize = 0.0001) {
    const intersection = rectIntersection(rect, cutter)
    if (!intersection) return [rect]

    const rectRight = rect.x + rect.width
    const rectBottom = rect.y + rect.height
    const cutRight = intersection.x + intersection.width
    const cutBottom = intersection.y + intersection.height
    const pieces = []

    if (intersection.y - rect.y > minSize) {
        pieces.push({ x: rect.x, y: rect.y, width: rect.width, height: intersection.y - rect.y })
    }
    if (rectBottom - cutBottom > minSize) {
        pieces.push({ x: rect.x, y: cutBottom, width: rect.width, height: rectBottom - cutBottom })
    }
    if (intersection.x - rect.x > minSize) {
        pieces.push({ x: rect.x, y: intersection.y, width: intersection.x - rect.x, height: intersection.height })
    }
    if (rectRight - cutRight > minSize) {
        pieces.push({ x: cutRight, y: intersection.y, width: rectRight - cutRight, height: intersection.height })
    }

    return pieces.filter(piece => piece.width > minSize && piece.height > minSize)
}

function subtractRects(rects, cutters, minSize = 0.0001) {
    return cutters.reduce((remainingRects, cutter) => {
        return remainingRects.flatMap(rect => subtractRect(rect, cutter, minSize))
    }, rects).filter(rect => rect.width > minSize && rect.height > minSize)
}

function isCustomTouchSetEntry(entry) {
    return !!normalizeCustomTouchArea(entry?.customArea)
}

function parseTouchCustomAreaDataset(rawValue) {
    if (!rawValue) return null
    try {
        return JSON.parse(rawValue)
    } catch (_) {
        return null
    }
}

function createCustomTouchAreaId(createdAt = Date.now()) {
    const timestamp = Number.isFinite(Number(createdAt)) && Number(createdAt) > 0 ? Number(createdAt) : Date.now()
    const randomPart = Math.random().toString(36).slice(2, 8)
    return `${TOUCH_CUSTOM_AREA_PREFIX}${Math.round(timestamp).toString(36)}_${randomPart}`
}

function getCurrentModelTouchSet() {
    const modelName = window.live2dManager?.modelName || ''
    if (!modelName) return {}
    if (!window.live2dManager.touchSet) window.live2dManager.touchSet = {}
    if (!window.live2dManager.touchSet[modelName]) {
        window.live2dManager.touchSet[modelName] = { default: { motions: [], expressions: [] } }
    }
    return window.live2dManager.touchSet[modelName]
}

function syncTouchSetDataToManager(touchSetData) {
    const modelName = window.live2dManager?.modelName || ''
    if (!modelName || !window.live2dManager) return touchSetData
    if (!window.live2dManager.touchSet) window.live2dManager.touchSet = {}
    window.live2dManager.touchSet[modelName] = touchSetData
    return touchSetData
}

function syncCurrentTouchSetConfigToManager() {
    return syncTouchSetDataToManager(collectAllTouchSetData())
}

function touchPage_open(){

    try {
        const live2dManager = window.live2dManager
        if (!live2dManager) {
            createTouchConfigFloatingWindow({ content: window.t('live2d.touchAnim.managerNotFound', 'Live2DManager 未找到') })
            return
        }
        
        const model = live2dManager.getCurrentModel()
        if (!model) {
            createTouchConfigFloatingWindow({ content: window.t('live2d.touchAnim.modelNotFound', '当前没有加载模型') })
            return
        }
        
        const internalModel = model.internalModel
        if (!internalModel || !internalModel.settings) {
            createTouchConfigFloatingWindow({ content: window.t('live2d.touchAnim.modelDataNotReady', '模型内部数据未准备好') })
            return
        }
        
        const hitAreas = internalModel.settings.hitAreas || []
        
        const settings = internalModel.settings.json
        const motions = settings.FileReferences?.Motions || {}
        const expressions = settings.FileReferences?.Expressions || []
        
        showTouchSetConfigWindow(hitAreas, motions, expressions)
    } catch (error) {
        createTouchConfigFloatingWindow({ content: `错误: ${error.message}` })
        console.error("获取 HitAreas 失败:", error)
    }
}

// 并发刷新世代号：连续保存/保存+热切换会同时跑多个 InitializationTouchSet，
// 较早发出的 /api/characters GET 可能较晚返回；提交前丢弃非最新世代的结果，
// 防止旧快照覆盖新配置。
let touchSetInitializationGeneration = 0

async function InitializationTouchSet(characterJson, options = {}) {
    while(typeof window.t !== 'function'){
        await new Promise(resolve => setTimeout(resolve, 500));
    }

    const modelType = localStorage.getItem('modelType') || 'live2d';
    const isVRMActive = window.vrmManager && window.vrmManager.currentModel;
    const isMMDActive = window.mmdManager && window.mmdManager.currentModel;
    // allowLive2DReload：调用方刚完成 Live2D 模型加载/热切换时使用。此场景下
    // localStorage.modelType 和 vrm/mmdManager.currentModel 可能还是切换前的
    // 旧值（热切换只暂停 VRM/MMD 渲染、不清 currentModel），守卫会误拦刷新。
    if (!options.allowLive2DReload && (modelType !== 'live2d' || isVRMActive || isMMDActive)) {
        console.log('[TouchSet] 当前模型类型不是 Live2D，跳过触摸配置初始化');
        return false;
    }

    // 世代号在类型守卫之后才递增：被守卫拦下的调用（如 VRM 残留状态下收到
    // 保存广播）不得让更早在途的有效刷新（VRM→Live2D 切换窗口内的加载钩子
    // 读）被判为过期丢弃。
    const initializationGeneration = ++touchSetInitializationGeneration

    if (!characterJson){
        // // 获取角色名称
        // const lanlanName = await getLanlanName();

        // 解析优先级：显式指定（角色切换钩子传入新角色名）> 本页面当前角色
        // （lanlan_config 会随原地切角色更新；URL 在切角色后是旧值）> URL >
        // page_config。模型管理页没有 lanlan_config，按其 URL/服务端当前角色
        // 解析，行为不变。
        const urlParams = new URLSearchParams(window.location.search);
        let lanlanName = String(options.lanlanName || '').trim()
            || String(window.lanlan_config?.lanlan_name || '').trim()
            || urlParams.get('lanlan_name') || '';
        // 如果 URL 中没有，从 API 获取（使用 RequestHelper）
        if (!lanlanName) {
            try {
                const data = await fetch('/api/config/page_config');

                if (data.ok) {
                    const jsonData = await data.json();
                    lanlanName = jsonData.lanlan_name || '';
                }
            } catch (error) {
                console.error('获取 lanlan_name 失败:', error);
            }
        }

        if (!lanlanName) {
            // 角色名解析失败（如 page_config 瞬时故障）：未提交任何数据。
            // 返回 false 让调用方（待刷新标记）保留重试机会。
            return false;
        }


        const response = await fetch('/api/characters');
        const charactersJson = await response.json();
        characterJson = charactersJson.猫娘[lanlanName]
        if (!characterJson) {
            // 角色不存在/数据缺失：同样视为未提交，避免 undefined 解引用。
            console.warn('[TouchSet] 未找到角色的触摸配置数据:', lanlanName)
            return false;
        }
    }else{
        // 呃
    }
    let model
    for(let i = 0;i<5;i++){
        model = window.live2dManager.getCurrentModel()
        if (model){
            break
        }else{
            console.warn(`[TouchSet] 模型不存在，等待 1 秒后重试 (${i+1}/5)`)
            await new Promise(resolve => setTimeout(resolve, 1000));
        }
    }

    const modelName = window.live2dManager.modelName || ''
    const touchSet = characterJson._reserved?.touch_set || {};

    // 过期响应丢弃：已有更新的刷新在途时，本次旧结果不得提交，
    // 防止较晚完成的旧 GET 覆盖较新的触摸配置。返回 'superseded' 让调用方
    // 区分：新调用接管提交与失败重试，旧调用不得安排重试（重试的世代号
    // 递增会丢弃新调用的在途结果）。
    if (initializationGeneration !== touchSetInitializationGeneration) {
        console.log('[TouchSet] 有更新的触摸配置刷新在途，丢弃本次过期结果')
        return 'superseded';
    }

    if(modelName && !touchSet[modelName]){
        touchSet[modelName] = {"default":{"motions": [], "expressions": []}}
    }
    window.live2dManager.touchSet = touchSet;
    window.live2dManager._touchSetLoadedAt = Date.now()
    window.live2dManager.touchSetFilter = {}
    window.live2dManager.touchSetHitEventLock = false

    if (!model) {
        // 模型就绪前不能把 undefined 交给 setupHitAreaInteraction：那会让原生
        // HitArea 监听在本页面会话里一直处于未接线状态且无人补救。触摸数据
        // 已提交（返回 true），接线由模型加载完成后的 nekoRefreshLive2DTouchSet() 补上。
        console.warn('[TouchSet] 等待期内模型未就绪，触摸数据已生效，HitArea 接线待模型加载完成后补上')
        return true;
    }

    window.live2dManager.setupHitAreaInteraction(model)
    return true;
}

const TOUCH_SET_SAVE_TIMEOUT_MS = 10000

async function saveTouchSetToServer() {
    const modelName = window.live2dManager?.modelName;
    const lanlanName = new URLSearchParams(window.location.search).get('lanlan_name') || window.lanlan_config?.lanlan_name;
    
    if (!modelName || !lanlanName) {
        console.error('[TouchSet] 无法保存：缺少模型名称或角色名称');
        return false;
    }
    
    const touchSetData = syncCurrentTouchSetConfigToManager();
    
    const saveController = typeof AbortController !== 'undefined' ? new AbortController() : null
    const saveTimeout = saveController
        ? setTimeout(() => saveController.abort(), TOUCH_SET_SAVE_TIMEOUT_MS)
        : null

    try {
        const response = await fetch(`/api/characters/catgirl/${encodeURIComponent(lanlanName)}/touch_set`, {
            method: 'PATCH',
            headers: {
                'Content-Type': 'application/json'
            },
            ...(saveController ? { signal: saveController.signal } : {}),
            body: JSON.stringify({
                model_name: modelName,
                touch_set: touchSetData
            })
        });
        
        const result = await response.json();

        if (result.success) {
            syncTouchSetDataToManager(touchSetData);
            broadcastTouchSetUpdated(modelName)
            console.log(`[TouchSet] 已保存模型 ${modelName} 的触摸配置到服务器`);
            return true;
        } else {
            console.error('[TouchSet] 保存失败:', result.error);
            return false;
        }
    } catch (error) {
        console.error('[TouchSet] 保存请求失败:', error);
        return false;
    } finally {
        if (saveTimeout) clearTimeout(saveTimeout)
    }
}

function collectAllTouchSetData() {
    const touchSetData = {};
    
    const hitAreaItems = document.querySelectorAll('.hitarea-item');
    hitAreaItems.forEach(item => {
        const titleElement = item.querySelector('.hitarea-title');
        const hitAreaId = titleElement.dataset.hitAreaId || titleElement.textContent.replace('HitAreaID: ', '');
        
        const motionMultiselect = item.querySelector('.custom-multiselect[data-type="motion"]');
        const expressionMultiselect = item.querySelector('.custom-multiselect[data-type="expression"]');
        
        const motions = motionMultiselect ? 
            Array.from(motionMultiselect.querySelectorAll('input[type="checkbox"]:checked')).map(cb => cb.value) : [];
        const expressions = expressionMultiselect ? 
            Array.from(expressionMultiselect.querySelectorAll('input[type="checkbox"]:checked')).map(cb => cb.value) : [];
        
        const entry = {
            motions: motions,
            expressions: expressions
        }

        const customArea = normalizeCustomTouchArea(parseTouchCustomAreaDataset(item.dataset.customArea), hitAreaId)
        if (customArea) {
            entry.customArea = customArea
        }

        touchSetData[hitAreaId] = entry;
    });
    
    return touchSetData;
}

function showTouchSetConfigWindow(hitAreas, motions, expressions){
    
    const floatingWindow = createTouchConfigFloatingWindow({
        title: touchAnimText('title', '触摸动画配置'),
        showCloseButton: true
    })
    
    const container = floatingWindow.getContentContainer()
    
    const nowmodle = window.live2dManager?.modelName || '';
    const TouchSet = getCurrentModelTouchSet();
    
    const cleanupMultiselect = () => {
        document.removeEventListener('click', closeAllMultiselects);
    };
    floatingWindow.onClose = function(){
        if (autoSaveTimeout) {
            clearTimeout(autoSaveTimeout)
            autoSaveTimeout = null
        }
        saveTouchSetToServer()
        cleanupMultiselect()
        console.log("[TouchSet] 配置窗口已关闭")
    }

    const toolbar = document.createElement("div")
    toolbar.className = "touch-config-toolbar"

    const customAreaButton = document.createElement("button")
    customAreaButton.type = "button"
    customAreaButton.className = "hitarea-btn hitarea-btn-secondary touch-custom-area-btn"
    customAreaButton.textContent = touchAnimText('customAreaButton', '自定义区域')
    customAreaButton.onclick = function(e) {
        e.stopPropagation()
        const latestTouchSet = collectAllTouchSetData()
        window.live2dManager.touchSet[nowmodle] = latestTouchSet
        openCustomTouchAreaWindow({
            area: null,
            touchSet: latestTouchSet,
            onSave: (customArea) => {
                const currentData = collectAllTouchSetData()
                currentData[customArea.id] = currentData[customArea.id] || { motions: [], expressions: [] }
                currentData[customArea.id].customArea = customArea
                window.live2dManager.touchSet[nowmodle] = currentData
                renderTouchAreaConfigList(configDiv, hitAreas, motions, expressions, currentData)
                triggerAutoSave()
            }
        })
    }
    toolbar.appendChild(customAreaButton)
    container.appendChild(toolbar)
    
    const configDiv = document.createElement("div")
    configDiv.className = "hitarea-config"
    renderTouchAreaConfigList(configDiv, hitAreas, motions, expressions, TouchSet)
    
    container.appendChild(configDiv)
    
    setTimeout(() => {
        document.addEventListener('click', closeAllMultiselects)
    }, 100)
}

function getMotionOptionsForTouchConfig(motions) {
    const motionOptionsSet = new Set()
    Object.keys(motions || {}).forEach(groupName => {
        const motionGroup = motions[groupName]
        if (Array.isArray(motionGroup)) {
            motionGroup.forEach(motion => {
                if (motion.File) {
                    const parts = motion.File.split("motions/")
                    const raw = parts.length > 1 ? parts[parts.length - 1] : motion.File.split("/").pop() || motion.File
                    motionOptionsSet.add(raw.replace(".motion3","").replace(".json",""))
                }
            })
        }
    })
    return Array.from(motionOptionsSet).sort((a, b) => a.localeCompare(b))
}

function getCustomTouchAreasFromSet(touchSet, nativeIds) {
    return getCustomTouchAreaRecordsFromSet(touchSet, nativeIds).map(record => record.area)
}

function renderTouchAreaConfigList(configDiv, hitAreas, motions, expressions, touchSet) {
    configDiv.innerHTML = ''

    const nativeHitAreas = Array.isArray(hitAreas) ? hitAreas : []
    const hitAreasCopy = [{ id: "default", Name: "default" }, ...nativeHitAreas]
    const nativeIds = new Set(hitAreasCopy.map(hitArea => hitArea.id || hitArea.Id).filter(Boolean))
    const customHitAreas = getCustomTouchAreasFromSet(touchSet, nativeIds)

    const motionOptions = getMotionOptionsForTouchConfig(motions)
    const expressionOptions = Array.isArray(expressions) ? expressions.map(e => e.Name).filter(Boolean) : []

    hitAreasCopy.forEach(hitArea => {
        configDiv.appendChild(createTouchAreaConfigItem(hitArea, {
            touchSet,
            motionOptions,
            expressionOptions,
            isCustom: false,
            onRefresh: () => renderTouchAreaConfigList(configDiv, hitAreas, motions, expressions, getCurrentModelTouchSet())
        }))
    })

    customHitAreas.forEach(customArea => {
        configDiv.appendChild(createTouchAreaConfigItem({
            id: customArea.id,
            Name: customArea.name,
            customArea
        }, {
            touchSet,
            motionOptions,
            expressionOptions,
            isCustom: true,
            onRefresh: () => renderTouchAreaConfigList(configDiv, hitAreas, motions, expressions, getCurrentModelTouchSet())
        }))
    })
}

function showTouchConfirmFallback({ title, message, okText, cancelText, danger = false }) {
    return new Promise(resolve => {
        const floatingWindow = createTouchConfigFloatingWindow({ title, showCloseButton: true })
        const content = floatingWindow.getContentContainer()
        if (content.parentElement) content.parentElement.classList.add('touch-confirm-window')

        const messageEl = document.createElement('p')
        messageEl.className = 'touch-confirm-message'
        messageEl.textContent = message
        content.appendChild(messageEl)

        const buttons = document.createElement('div')
        buttons.className = 'hitarea-buttons touch-confirm-buttons'

        const cancelButton = document.createElement('button')
        cancelButton.type = 'button'
        cancelButton.className = 'hitarea-btn hitarea-btn-secondary'
        cancelButton.textContent = cancelText
        buttons.appendChild(cancelButton)

        const okButton = document.createElement('button')
        okButton.type = 'button'
        okButton.className = danger ? 'hitarea-btn hitarea-btn-danger' : 'hitarea-btn hitarea-btn-primary'
        okButton.textContent = okText
        buttons.appendChild(okButton)

        content.appendChild(buttons)

        let resolved = false
        const finish = (value) => {
            if (resolved) return
            resolved = true
            floatingWindow.close()
            resolve(value)
        }

        cancelButton.onclick = () => finish(false)
        okButton.onclick = () => finish(true)
        floatingWindow.onClose = () => {
            if (!resolved) {
                resolved = true
                resolve(false)
            }
        }
        setTimeout(() => cancelButton.focus(), 0)
    })
}

async function confirmDeleteCustomTouchArea(customArea) {
    const areaName = customArea?.name || ''
    const title = touchAnimText('deleteCustomAreaConfirmTitle', '删除自定义区域')
    const message = touchAnimText(
        'deleteCustomAreaConfirm',
        '确定要删除自定义区域“{{name}}”吗？删除后该区域绑定的动作和表情也会移除。',
        { name: areaName }
    )
    const okText = touchAnimText('deleteCustomArea', '删除')
    const cancelText = touchAnimText('cancel', '取消')

    if (typeof window.showConfirm === 'function') {
        return await window.showConfirm(message, title, {
            danger: true,
            okText,
            cancelText
        })
    }

    return await showTouchConfirmFallback({
        title,
        message,
        okText,
        cancelText,
        danger: true
    })
}

function createTouchAreaConfigItem(hitArea, options) {
    const hitAreaId = hitArea.id || hitArea.Id
    const hitAreaName = hitArea.Name || hitAreaId
    const customArea = normalizeCustomTouchArea(hitArea.customArea, hitAreaId)

    const itemDiv = document.createElement("div")
    itemDiv.className = "hitarea-item"
    if (customArea) {
        itemDiv.dataset.customArea = JSON.stringify(customArea)
    }

    const titleRow = document.createElement("div")
    titleRow.className = "hitarea-title-row"

    const titleDiv = document.createElement("div")
    titleDiv.className = "hitarea-title"
    titleDiv.dataset.hitAreaId = hitAreaId
    if (hitAreaId === "default") {
        titleDiv.textContent = touchAnimText('defaultClickAnim', '默认点击动画')
    } else if (customArea) {
        titleDiv.textContent = touchAnimText('customAreaTitle', '自定义区域：{{name}}', { name: customArea.name })
    } else {
        titleDiv.textContent = `HitAreaID: ${hitAreaName}`
    }
    titleRow.appendChild(titleDiv)

    if (customArea) {
        const actionWrap = document.createElement("div")
        actionWrap.className = "hitarea-custom-actions"

        const editButton = document.createElement("button")
        editButton.type = "button"
        editButton.className = "hitarea-icon-btn"
        editButton.textContent = touchAnimText('editCustomArea', '编辑')
        editButton.onclick = function(e) {
            e.stopPropagation()
            const currentData = collectAllTouchSetData()
            window.live2dManager.touchSet[window.live2dManager.modelName] = currentData
            openCustomTouchAreaWindow({
                area: customArea,
                touchSet: currentData,
                onSave: (updatedArea) => {
                    const latestData = collectAllTouchSetData()
                    latestData[updatedArea.id] = latestData[updatedArea.id] || { motions: [], expressions: [] }
                    latestData[updatedArea.id].customArea = updatedArea
                    window.live2dManager.touchSet[window.live2dManager.modelName] = latestData
                    if (typeof options.onRefresh === 'function') options.onRefresh()
                    triggerAutoSave()
                }
            })
        }

        const deleteButton = document.createElement("button")
        deleteButton.type = "button"
        deleteButton.className = "hitarea-icon-btn hitarea-icon-btn-danger"
        deleteButton.textContent = touchAnimText('deleteCustomArea', '删除')
        deleteButton.onclick = async function(e) {
            e.stopPropagation()
            const ok = await confirmDeleteCustomTouchArea(customArea)
            if (!ok) return
            const latestData = collectAllTouchSetData()
            delete latestData[customArea.id]
            window.live2dManager.touchSet[window.live2dManager.modelName] = latestData
            if (typeof options.onRefresh === 'function') options.onRefresh()
            triggerAutoSave()
        }

        actionWrap.appendChild(editButton)
        actionWrap.appendChild(deleteButton)
        titleRow.appendChild(actionWrap)
    }

    itemDiv.appendChild(titleRow)

    const motionSection = document.createElement("div")
    motionSection.className = "hitarea-section touch_set_motion"

    const motionLabel = document.createElement("label")
    motionLabel.className = "hitarea-label"
    motionLabel.textContent = touchAnimText('selectMotion', '绑定动作') + ":"
    motionSection.appendChild(motionLabel)

    const selectedMotions = options.touchSet[hitAreaId]?.motions || []
    const motionMultiselect = createMultiSelect("motion", options.motionOptions, selectedMotions, hitAreaId)
    motionSection.appendChild(motionMultiselect)
    itemDiv.appendChild(motionSection)

    const expressionSection = document.createElement("div")
    expressionSection.className = "hitarea-section touch_set_expression"

    const expressionLabel = document.createElement("label")
    expressionLabel.className = "hitarea-label"
    expressionLabel.textContent = touchAnimText('selectExpression', '绑定表情') + ":"
    expressionSection.appendChild(expressionLabel)

    const selectedExpressions = options.touchSet[hitAreaId]?.expressions || []
    const expressionMultiselect = createMultiSelect("expression", options.expressionOptions, selectedExpressions, hitAreaId)
    expressionSection.appendChild(expressionMultiselect)
    itemDiv.appendChild(expressionSection)

    return itemDiv
}

function openCustomTouchAreaWindow(options = {}) {
    const manager = window.live2dManager
    const model = manager?.getCurrentModel?.()
    if (!manager || !model) {
        createTouchConfigFloatingWindow({ content: touchAnimText('previewUnavailable', '当前无法打开自定义区域预览') })
        return
    }

    const editingArea = normalizeCustomTouchArea(options.area, options.area?.id)
    const draftCreatedAt = editingArea?.createdAt || Date.now()
    const draftAreaId = editingArea?.id || createCustomTouchAreaId(draftCreatedAt)
    const title = editingArea
        ? touchAnimText('editCustomAreaTitle', '编辑自定义区域')
        : touchAnimText('newCustomAreaTitle', '新建自定义区域')

    const floatingWindow = createTouchConfigFloatingWindow({ title, showCloseButton: true })
    const content = floatingWindow.getContentContainer()
    if (content.parentElement) content.parentElement.classList.add('touch-custom-window')

    const nameRow = document.createElement('div')
    nameRow.className = 'touch-custom-name-row'

    const nameLabel = document.createElement('label')
    nameLabel.className = 'hitarea-label'
    nameLabel.textContent = touchAnimText('customAreaName', '区域名称') + ':'
    nameRow.appendChild(nameLabel)

    const nameInput = document.createElement('input')
    nameInput.className = 'touch-custom-name-input'
    nameInput.type = 'text'
    nameInput.maxLength = 40
    const existingCount = Object.values(options.touchSet || {}).filter(isCustomTouchSetEntry).length
    nameInput.value = editingArea?.name || touchAnimText('customAreaNameDefault', '自定义区域 {{index}}', { index: existingCount + 1 })
    nameRow.appendChild(nameInput)
    content.appendChild(nameRow)

    const previewWrap = document.createElement('div')
    previewWrap.className = 'touch-custom-preview-wrap'

    const previewLive2dCanvas = document.createElement('canvas')
    previewLive2dCanvas.className = 'touch-custom-live2d-canvas'
    previewWrap.appendChild(previewLive2dCanvas)

    const previewCanvas = document.createElement('canvas')
    previewCanvas.className = 'touch-custom-preview-canvas'
    previewWrap.appendChild(previewCanvas)

    const modelBoundsBox = document.createElement('div')
    modelBoundsBox.className = 'touch-custom-model-bounds'
    previewWrap.appendChild(modelBoundsBox)

    const selectionBox = document.createElement('div')
    selectionBox.className = 'touch-custom-selection'
    ;['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'].forEach(handle => {
        const handleEl = document.createElement('span')
        handleEl.className = `touch-custom-resize-handle touch-custom-resize-${handle}`
        handleEl.dataset.resizeHandle = handle
        selectionBox.appendChild(handleEl)
    })
    previewWrap.appendChild(selectionBox)

    const hoverLabel = document.createElement('div')
    hoverLabel.className = 'touch-custom-hover-label'
    previewWrap.appendChild(hoverLabel)

    content.appendChild(previewWrap)

    const status = document.createElement('div')
    status.className = 'touch-custom-status'
    status.setAttribute('aria-live', 'polite')
    content.appendChild(status)

    let saveBeforeCloseInProgress = false
    const closeFloatingWindow = floatingWindow.close.bind(floatingWindow)
    floatingWindow.close = function(cleanup) {
        if (saveBeforeCloseInProgress) {
            status.textContent = window.t
                ? window.t('live2d.savingSettings', '正在保存设置...')
                : '正在保存设置...'
            return
        }
        closeFloatingWindow(cleanup)
    }

    const buttons = document.createElement('div')
    buttons.className = 'hitarea-buttons touch-custom-buttons'

    const cancelButton = document.createElement('button')
    cancelButton.type = 'button'
    cancelButton.className = 'hitarea-btn hitarea-btn-secondary'
    cancelButton.textContent = touchAnimText('cancel', '取消')
    cancelButton.onclick = () => floatingWindow.close()
    buttons.appendChild(cancelButton)

    const saveButton = document.createElement('button')
    saveButton.type = 'button'
    saveButton.className = 'hitarea-btn hitarea-btn-primary'
    saveButton.textContent = touchAnimText('saveCustomArea', '保存区域')
    buttons.appendChild(saveButton)

    content.appendChild(buttons)

    let previewMetrics = null
    let animationFrameId = null
    let drawing = false
    let interactionMode = null
    let resizeHandle = null
    let interactionStartPoint = null
    let interactionStartRect = null
    let selectionRect = null
    let lastPreviewPointer = null
    let hoverLabelState = {
        visible: false,
        currentX: 0,
        currentY: 0,
        targetX: 0,
        targetY: 0,
        initialized: false
    }
    let initialSelectionApplied = false
    let previewBaseBounds = null
    let previewApp = null
    let previewModel = null
    let previewModelReady = false
    let previewDisposed = false
    let previewLoadStarted = false
    let previewModelFitted = false
    let previewRendererSize = { width: 0, height: 0 }
    const MIN_SELECTION_SIZE = 10
    const RESIZE_HIT_PADDING = 10
    const HOVER_LABEL_OFFSET_X = 16
    const HOVER_LABEL_OFFSET_Y = 8
    const HOVER_LABEL_DAMPING = 0.22
    const previewBaseAreaRecords = getCustomTouchAreaRecordsFromSet(options.touchSet || {})
        .filter(record => record.area.id !== editingArea?.id)

    function getPreviewWrapSize() {
        return {
            width: Math.max(1, previewWrap.clientWidth || previewWrap.offsetWidth || 1),
            height: Math.max(1, previewWrap.clientHeight || previewWrap.offsetHeight || 1)
        }
    }

    function getPreviewModelPath() {
        const candidates = [
            manager?._lastLoadedModelPath,
            manager?.lastLoadedModelPath,
            model?.internalModel?.settings?.url,
            model?.internalModel?.settings?.json?.url,
            model?.internalModel?.settings?.json?.Url,
            model?.url
        ]
        return candidates.find(candidate => typeof candidate === 'string' && candidate.trim())
    }

    function getPreviewLive2DModelClass() {
        return window.Live2DModel
            || window.PIXI?.live2d?.Live2DModel
            || window.PIXI?.live2d?.cubism4?.Live2DModel
    }

    function getCoreParameterId(coreModel, index) {
        if (!coreModel || !Number.isInteger(index) || index < 0) return null
        try {
            const id = coreModel._parameterIds?.[index]
                ?? coreModel._model?.parameters?.ids?.[index]
                ?? coreModel.model?.parameters?.ids?.[index]
                ?? coreModel.parameters?.ids?.[index]
            if (id) return String(id)
        } catch (_) {}
        try {
            if (typeof coreModel.getParameterId === 'function') {
                const id = coreModel.getParameterId(index)
                if (id) return String(id)
            }
        } catch (_) {}
        return null
    }

    function copyCurrentModelParametersToPreview() {
        const sourceCoreModel = model?.internalModel?.coreModel
        const previewCoreModel = previewModel?.internalModel?.coreModel
        if (!sourceCoreModel || !previewCoreModel) return

        const sourceCount = typeof sourceCoreModel.getParameterCount === 'function'
            ? Number(sourceCoreModel.getParameterCount())
            : 0
        const previewCount = typeof previewCoreModel.getParameterCount === 'function'
            ? Number(previewCoreModel.getParameterCount())
            : 0
        if (!Number.isFinite(sourceCount) || sourceCount <= 0) return

        for (let index = 0; index < sourceCount; index += 1) {
            const paramId = getCoreParameterId(sourceCoreModel, index)
            if (paramId && /(?:opacity|visibility)/i.test(paramId)) continue

            let targetIndex = -1
            if (paramId && typeof previewCoreModel.getParameterIndex === 'function') {
                try {
                    targetIndex = previewCoreModel.getParameterIndex(paramId)
                } catch (_) {}
            }
            if (targetIndex < 0 && index < previewCount) targetIndex = index
            if (targetIndex < 0) continue

            try {
                const value = Number(sourceCoreModel.getParameterValueByIndex(index))
                if (!Number.isFinite(value)) continue
                previewCoreModel.setParameterValueByIndex(targetIndex, value)
            } catch (_) {}
        }
    }

    function resizePreviewRenderer() {
        if (!previewApp?.renderer) return false
        const size = getPreviewWrapSize()
        if (previewRendererSize.width === size.width && previewRendererSize.height === size.height) return false

        previewRendererSize = size
        try {
            previewApp.renderer.resize(size.width, size.height)
        } catch (_) {}
        previewBaseBounds = null
        previewModelFitted = false
        return true
    }

    function fitPreviewModelToWrap() {
        if (!previewModel || !previewApp?.renderer) return false
        const size = getPreviewWrapSize()

        try {
            previewModel.x = 0
            previewModel.y = 0
            if (previewModel.scale && typeof previewModel.scale.set === 'function') {
                previewModel.scale.set(1, 1)
            } else if (previewModel.scale) {
                previewModel.scale.x = 1
                previewModel.scale.y = 1
            }
            previewApp.renderer.render(previewApp.stage)
        } catch (_) {}

        const bounds = normalizePixiBoundsRect(previewModel.getBounds?.())
        if (!bounds || bounds.width <= 0 || bounds.height <= 0) return false

        const paddedWidth = bounds.width * 1.18
        const paddedHeight = bounds.height * 1.18
        const scale = Math.min(size.width / paddedWidth, size.height / paddedHeight)
        if (!Number.isFinite(scale) || scale <= 0) return false

        if (previewModel.scale && typeof previewModel.scale.set === 'function') {
            previewModel.scale.set(scale, scale)
        } else if (previewModel.scale) {
            previewModel.scale.x = scale
            previewModel.scale.y = scale
        }
        previewModel.x = (size.width - bounds.width * scale) / 2 - bounds.left * scale
        previewModel.y = (size.height - bounds.height * scale) / 2 - bounds.top * scale
        previewBaseBounds = null

        try {
            previewApp.renderer.render(previewApp.stage)
        } catch (_) {}
        return true
    }

    async function loadPreviewModel() {
        const modelPath = getPreviewModelPath()
        const PixiApplication = window.PIXI?.Application
        const Live2DModelClass = getPreviewLive2DModelClass()
        if (!modelPath || !PixiApplication || !Live2DModelClass) {
            throw new Error('Live2D preview dependencies are unavailable')
        }

        const size = getPreviewWrapSize()
        previewApp = new PixiApplication({
            view: previewLive2dCanvas,
            width: size.width,
            height: size.height,
            transparent: true,
            backgroundAlpha: 0,
            autoDensity: true,
            resolution: window.devicePixelRatio || 1
        })
        previewRendererSize = size
        previewLoadStarted = true

        const loadedModel = await Live2DModelClass.from(modelPath, { autoFocus: false })
        if (previewDisposed) {
            try {
                loadedModel?.destroy?.({ children: true, texture: false, baseTexture: false })
            } catch (_) {}
            return
        }

        previewModel = loadedModel
        previewApp.stage.addChild(previewModel)
        copyCurrentModelParametersToPreview()
        previewModelReady = true
        previewLive2dCanvas.classList.add('is-ready')
        resizePreviewRenderer()
        previewModelFitted = fitPreviewModelToWrap()
    }

    function destroyPreviewModel() {
        previewDisposed = true
        previewModelReady = false
        previewModelFitted = false
        previewBaseBounds = null
        previewLive2dCanvas.classList.remove('is-ready')

        const modelToDestroy = previewModel
        const appToDestroy = previewApp
        previewModel = null
        previewApp = null

        try {
            if (modelToDestroy?.parent) modelToDestroy.parent.removeChild(modelToDestroy)
        } catch (_) {}
        try {
            modelToDestroy?.destroy?.({ children: true, texture: false, baseTexture: false })
        } catch (_) {}
        try {
            appToDestroy?.destroy?.(false, { children: true, texture: false, baseTexture: false })
        } catch (_) {
            try {
                appToDestroy?.destroy?.(false)
            } catch (_) {}
        }
    }

    function getPreviewBaseBounds() {
        if (previewBaseBounds) return previewBaseBounds
        if (!previewModelReady || !previewModel) return null
        try {
            const bounds = normalizePixiBoundsRect(previewModel.getBounds?.())
            if (!bounds) return null
            previewBaseBounds = {
                left: bounds.left,
                top: bounds.top,
                width: bounds.width,
                height: bounds.height,
                right: bounds.left + bounds.width,
                bottom: bounds.top + bounds.height
            }
        } catch (_) {
            return null
        }
        return previewBaseBounds
    }

    function updatePreviewMetrics() {
        const wrapWidth = previewWrap.clientWidth || 1
        const wrapHeight = previewWrap.clientHeight || 1
        const bounds = getPreviewBaseBounds()
        const modelRect = bounds ? {
            x: bounds.left,
            y: bounds.top,
            width: bounds.width,
            height: bounds.height
        } : null
        previewMetrics = {
            wrapWidth,
            wrapHeight,
            modelRect
        }
        return previewMetrics
    }

    function getModelPreviewRect() {
        const metrics = previewMetrics || updatePreviewMetrics()
        return metrics.modelRect
    }

    function applyBoxRect(element, rect) {
        if (!rect || rect.width <= 0 || rect.height <= 0) {
            element.style.display = 'none'
            return
        }
        element.style.display = 'block'
        element.style.left = `${rect.x}px`
        element.style.top = `${rect.y}px`
        element.style.width = `${rect.width}px`
        element.style.height = `${rect.height}px`
    }

    function clampPointToRect(point, rect) {
        return {
            x: Math.max(rect.x, Math.min(point.x, rect.x + rect.width)),
            y: Math.max(rect.y, Math.min(point.y, rect.y + rect.height))
        }
    }

    function cloneRect(rect) {
        return rect ? { x: rect.x, y: rect.y, width: rect.width, height: rect.height } : null
    }

    function pointInRect(point, rect) {
        return !!(point && rect
            && point.x >= rect.x && point.x <= rect.x + rect.width
            && point.y >= rect.y && point.y <= rect.y + rect.height)
    }

    function clampSelectionRectToModel(rect, modelRect) {
        if (!rect || !modelRect) return null
        let width = Math.max(MIN_SELECTION_SIZE, Math.min(rect.width, modelRect.width))
        let height = Math.max(MIN_SELECTION_SIZE, Math.min(rect.height, modelRect.height))
        let x = Math.max(modelRect.x, Math.min(rect.x, modelRect.x + modelRect.width - width))
        let y = Math.max(modelRect.y, Math.min(rect.y, modelRect.y + modelRect.height - height))
        return { x, y, width, height }
    }

    function getResizeHandleAtPoint(point) {
        if (!selectionRect || !point) return null
        const withinX = point.x >= selectionRect.x - RESIZE_HIT_PADDING
            && point.x <= selectionRect.x + selectionRect.width + RESIZE_HIT_PADDING
        const withinY = point.y >= selectionRect.y - RESIZE_HIT_PADDING
            && point.y <= selectionRect.y + selectionRect.height + RESIZE_HIT_PADDING
        if (!withinX || !withinY) return null

        const nearLeft = Math.abs(point.x - selectionRect.x) <= RESIZE_HIT_PADDING
        const nearRight = Math.abs(point.x - (selectionRect.x + selectionRect.width)) <= RESIZE_HIT_PADDING
        const nearTop = Math.abs(point.y - selectionRect.y) <= RESIZE_HIT_PADDING
        const nearBottom = Math.abs(point.y - (selectionRect.y + selectionRect.height)) <= RESIZE_HIT_PADDING

        if (nearTop && nearLeft) return 'nw'
        if (nearTop && nearRight) return 'ne'
        if (nearBottom && nearRight) return 'se'
        if (nearBottom && nearLeft) return 'sw'
        if (nearTop) return 'n'
        if (nearRight) return 'e'
        if (nearBottom) return 's'
        if (nearLeft) return 'w'
        return null
    }

    function cursorForResizeHandle(handle) {
        const cursorMap = {
            n: 'ns-resize',
            s: 'ns-resize',
            e: 'ew-resize',
            w: 'ew-resize',
            ne: 'nesw-resize',
            sw: 'nesw-resize',
            nw: 'nwse-resize',
            se: 'nwse-resize'
        }
        return cursorMap[handle] || 'crosshair'
    }

    function updatePreviewCursor(point) {
        if (drawing) return
        const handle = getResizeHandleAtPoint(point)
        if (handle) {
            previewWrap.style.cursor = cursorForResizeHandle(handle)
        } else if (pointInRect(point, selectionRect)) {
            previewWrap.style.cursor = 'move'
        } else {
            previewWrap.style.cursor = 'crosshair'
        }
    }

    function rectFromResize(startRect, handle, currentPoint, startPoint, modelRect) {
        if (!startRect || !handle || !currentPoint || !startPoint || !modelRect) return startRect
        let left = startRect.x
        let top = startRect.y
        let right = startRect.x + startRect.width
        let bottom = startRect.y + startRect.height
        const dx = currentPoint.x - startPoint.x
        const dy = currentPoint.y - startPoint.y

        if (handle.includes('w')) left += dx
        if (handle.includes('e')) right += dx
        if (handle.includes('n')) top += dy
        if (handle.includes('s')) bottom += dy

        left = Math.max(modelRect.x, Math.min(left, modelRect.x + modelRect.width))
        right = Math.max(modelRect.x, Math.min(right, modelRect.x + modelRect.width))
        top = Math.max(modelRect.y, Math.min(top, modelRect.y + modelRect.height))
        bottom = Math.max(modelRect.y, Math.min(bottom, modelRect.y + modelRect.height))

        if (right - left < MIN_SELECTION_SIZE) {
            if (handle.includes('w')) left = right - MIN_SELECTION_SIZE
            else right = left + MIN_SELECTION_SIZE
        }
        if (bottom - top < MIN_SELECTION_SIZE) {
            if (handle.includes('n')) top = bottom - MIN_SELECTION_SIZE
            else bottom = top + MIN_SELECTION_SIZE
        }

        return clampSelectionRectToModel({
            x: Math.min(left, right),
            y: Math.min(top, bottom),
            width: Math.abs(right - left),
            height: Math.abs(bottom - top)
        }, modelRect)
    }

    function pointFromPointerEvent(event) {
        const wrapRect = previewWrap.getBoundingClientRect()
        return {
            x: event.clientX - wrapRect.left,
            y: event.clientY - wrapRect.top
        }
    }

    function rectFromPoints(a, b) {
        return {
            x: Math.min(a.x, b.x),
            y: Math.min(a.y, b.y),
            width: Math.abs(a.x - b.x),
            height: Math.abs(a.y - b.y)
        }
    }

    function normalizedRectToPreviewRect(rect, modelRect) {
        return {
            x: modelRect.x + rect.x * modelRect.width,
            y: modelRect.y + rect.y * modelRect.height,
            width: rect.width * modelRect.width,
            height: rect.height * modelRect.height
        }
    }

    function getDraftArea(rect) {
        if (!rect) return null
        return {
            id: draftAreaId,
            type: 'rect',
            name: nameInput.value.trim() || draftAreaId,
            createdAt: draftCreatedAt,
            rect
        }
    }

    function getLayeredPreviewAreaRecords(draftRect = null) {
        const records = previewBaseAreaRecords.map(record => ({
            area: record.area,
            index: record.index,
            isDraft: false
        }))
        const draftArea = getDraftArea(draftRect)
        if (draftArea) {
            records.push({
                area: draftArea,
                index: Number.MAX_SAFE_INTEGER,
                isDraft: true
            })
        }
        return records.sort(compareCustomTouchAreaRecords)
    }

    function getEffectivePiecesForArea(area, previousAreas) {
        if (!area?.rect) return []
        return subtractRects([area.rect], previousAreas.map(item => item.rect), 0.0001)
    }

    function getEffectivePiecesForDraft(draftRect) {
        const records = getLayeredPreviewAreaRecords(draftRect)
        const previousAreas = []
        for (const record of records) {
            const pieces = getEffectivePiecesForArea(record.area, previousAreas)
            if (record.isDraft) return pieces
            previousAreas.push(record.area)
        }
        return []
    }

    function getAreaBoundarySegments(pieces) {
        const EPS = 0.000001
        const coordKey = value => String(Math.round(Number(value) * 1000000))
        const collectCoords = (values) => {
            const map = new Map()
            values.forEach(value => {
                const n = Number(value)
                if (!Number.isFinite(n)) return
                const key = coordKey(n)
                if (!map.has(key)) map.set(key, n)
            })
            return Array.from(map.values()).sort((a, b) => a - b)
        }

        const xs = collectCoords(pieces.flatMap(piece => [piece.x, piece.x + piece.width]))
        const ys = collectCoords(pieces.flatMap(piece => [piece.y, piece.y + piece.height]))
        if (xs.length < 2 || ys.length < 2) return []

        const covered = []
        for (let xIndex = 0; xIndex < xs.length - 1; xIndex += 1) {
            covered[xIndex] = []
            for (let yIndex = 0; yIndex < ys.length - 1; yIndex += 1) {
                const left = xs[xIndex]
                const right = xs[xIndex + 1]
                const top = ys[yIndex]
                const bottom = ys[yIndex + 1]
                if (right - left <= EPS || bottom - top <= EPS) {
                    covered[xIndex][yIndex] = false
                    continue
                }
                covered[xIndex][yIndex] = pieces.some(piece => {
                    return left >= piece.x - EPS
                        && right <= piece.x + piece.width + EPS
                        && top >= piece.y - EPS
                        && bottom <= piece.y + piece.height + EPS
                })
            }
        }

        const isCovered = (xIndex, yIndex) => !!(covered[xIndex] && covered[xIndex][yIndex])
        const segments = []
        for (let xIndex = 0; xIndex < xs.length - 1; xIndex += 1) {
            for (let yIndex = 0; yIndex < ys.length - 1; yIndex += 1) {
                if (!isCovered(xIndex, yIndex)) continue
                const left = xs[xIndex]
                const right = xs[xIndex + 1]
                const top = ys[yIndex]
                const bottom = ys[yIndex + 1]

                if (!isCovered(xIndex, yIndex - 1)) segments.push({ x1: left, y1: top, x2: right, y2: top })
                if (!isCovered(xIndex + 1, yIndex)) segments.push({ x1: right, y1: top, x2: right, y2: bottom })
                if (!isCovered(xIndex, yIndex + 1)) segments.push({ x1: right, y1: bottom, x2: left, y2: bottom })
                if (!isCovered(xIndex - 1, yIndex)) segments.push({ x1: left, y1: bottom, x2: left, y2: top })
            }
        }

        return segments
    }

    function drawAreaBoundary(ctx, pieces, modelRect, stroke, lineWidth) {
        const segments = getAreaBoundarySegments(pieces)
        if (segments.length === 0) return

        ctx.save()
        ctx.strokeStyle = stroke
        ctx.lineWidth = lineWidth
        ctx.setLineDash([])
        ctx.beginPath()
        segments.forEach(segment => {
            const start = normalizedRectToPreviewRect({ x: segment.x1, y: segment.y1, width: 0, height: 0 }, modelRect)
            const end = normalizedRectToPreviewRect({ x: segment.x2, y: segment.y2, width: 0, height: 0 }, modelRect)
            ctx.moveTo(start.x, start.y)
            ctx.lineTo(end.x, end.y)
        })
        ctx.stroke()
        ctx.restore()
    }

    function drawCustomAreaOverlays(ctx, modelRect) {
        if (!modelRect) return
        const draftRect = selectionToNormalizedRect()
        const records = getLayeredPreviewAreaRecords(draftRect)
        const previousAreas = []

        records.forEach((record, layerIndex) => {
            const effectivePieces = getEffectivePiecesForArea(record.area, previousAreas)
            const isDraft = record.isDraft
            const hue = isDraft ? 198 : (192 + (layerIndex * 42) % 120)
            const fill = isDraft ? 'rgba(64, 197, 241, 0.32)' : `hsla(${hue}, 76%, 48%, 0.22)`
            const stroke = isDraft ? 'rgba(34, 179, 255, 0.96)' : `hsla(${hue}, 76%, 36%, 0.72)`

            ctx.save()
            effectivePieces.forEach(piece => {
                const previewPiece = normalizedRectToPreviewRect(piece, modelRect)
                ctx.fillStyle = fill
                ctx.fillRect(previewPiece.x, previewPiece.y, previewPiece.width, previewPiece.height)
            })

            drawAreaBoundary(ctx, effectivePieces, modelRect, stroke, isDraft ? 2 : 1.5)
            ctx.restore()

            previousAreas.push(record.area)
        })
    }

    function findHoveredCustomArea(point) {
        const modelRect = getModelPreviewRect()
        if (!point || !modelRect) return null
        const draftRect = selectionToNormalizedRect()
        const records = getLayeredPreviewAreaRecords(draftRect)
        const previousAreas = []

        for (const record of records) {
            const effectivePieces = getEffectivePiecesForArea(record.area, previousAreas)
            const hit = effectivePieces.some(piece => pointInRect(point, normalizedRectToPreviewRect(piece, modelRect)))
            if (hit) return record.area
            previousAreas.push(record.area)
        }

        return null
    }

    function hideHoverLabel() {
        hoverLabelState.visible = false
        hoverLabel.classList.remove('is-visible')
    }

    function updateHoverLabelTarget(point) {
        if (!point || drawing) {
            hideHoverLabel()
            return
        }

        const hoveredArea = findHoveredCustomArea(point)
        if (!hoveredArea) {
            hideHoverLabel()
            return
        }

        const labelText = hoveredArea.name || hoveredArea.id
        if (hoverLabel.textContent !== labelText) hoverLabel.textContent = labelText
        hoverLabel.classList.add('is-visible')
        hoverLabelState.visible = true

        const labelWidth = hoverLabel.offsetWidth || 120
        const labelHeight = hoverLabel.offsetHeight || 28
        const wrapWidth = previewWrap.clientWidth || 1
        const wrapHeight = previewWrap.clientHeight || 1
        const targetX = Math.max(8, Math.min(point.x + HOVER_LABEL_OFFSET_X, wrapWidth - labelWidth - 8))
        const targetY = Math.max(8, Math.min(point.y + HOVER_LABEL_OFFSET_Y, wrapHeight - labelHeight - 8))

        hoverLabelState.targetX = targetX
        hoverLabelState.targetY = targetY
        if (!hoverLabelState.initialized) {
            hoverLabelState.currentX = targetX
            hoverLabelState.currentY = targetY
            hoverLabelState.initialized = true
        }
    }

    function updateHoverLabelPosition() {
        if (!hoverLabelState.visible) {
            hoverLabelState.initialized = false
            return
        }

        hoverLabelState.currentX += (hoverLabelState.targetX - hoverLabelState.currentX) * HOVER_LABEL_DAMPING
        hoverLabelState.currentY += (hoverLabelState.targetY - hoverLabelState.currentY) * HOVER_LABEL_DAMPING
        hoverLabel.style.transform = `translate3d(${hoverLabelState.currentX}px, ${hoverLabelState.currentY}px, 0)`
    }

    function applyInitialSelection() {
        if (initialSelectionApplied || !editingArea) return
        const modelRect = getModelPreviewRect()
        if (!modelRect) return
        selectionRect = {
            x: modelRect.x + editingArea.rect.x * modelRect.width,
            y: modelRect.y + editingArea.rect.y * modelRect.height,
            width: editingArea.rect.width * modelRect.width,
            height: editingArea.rect.height * modelRect.height
        }
        applyBoxRect(selectionBox, selectionRect)
        initialSelectionApplied = true
    }

    function drawPreviewFrame() {
        const selectionBeforeLayout = !drawing && selectionRect ? selectionToNormalizedRect() : null
        const previewSizeChanged = resizePreviewRenderer()
        if (previewModelReady && (!previewModelFitted || previewSizeChanged)) {
            previewModelFitted = fitPreviewModelToWrap()
        }
        const metrics = updatePreviewMetrics()
        if (selectionBeforeLayout && metrics.modelRect) {
            selectionRect = normalizedRectToPreviewRect(selectionBeforeLayout, metrics.modelRect)
            applyBoxRect(selectionBox, selectionRect)
        }
        const dpr = window.devicePixelRatio || 1
        const targetWidth = Math.max(1, Math.round(metrics.wrapWidth * dpr))
        const targetHeight = Math.max(1, Math.round(metrics.wrapHeight * dpr))
        if (previewCanvas.width !== targetWidth || previewCanvas.height !== targetHeight) {
            previewCanvas.width = targetWidth
            previewCanvas.height = targetHeight
        }
        const ctx = previewCanvas.getContext('2d')
        if (ctx) {
            ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
            ctx.clearRect(0, 0, metrics.wrapWidth, metrics.wrapHeight)
        }

        const modelPreviewRect = getModelPreviewRect()
        if (ctx) drawCustomAreaOverlays(ctx, modelPreviewRect)
        applyBoxRect(modelBoundsBox, modelPreviewRect)
        applyInitialSelection()
        if (lastPreviewPointer && !drawing) updateHoverLabelTarget(lastPreviewPointer)
        updateHoverLabelPosition()
        animationFrameId = requestAnimationFrame(drawPreviewFrame)
    }

    function selectionToNormalizedRect() {
        const modelRect = getModelPreviewRect()
        if (!selectionRect || !modelRect || modelRect.width <= 0 || modelRect.height <= 0) return null
        if (selectionRect.width < 6 || selectionRect.height < 6) return null
        return normalizeCustomTouchAreaRect({
            x: (selectionRect.x - modelRect.x) / modelRect.width,
            y: (selectionRect.y - modelRect.y) / modelRect.height,
            width: selectionRect.width / modelRect.width,
            height: selectionRect.height / modelRect.height
        })
    }

    previewWrap.addEventListener('pointerdown', (event) => {
        event.preventDefault()
        const modelRect = getModelPreviewRect()
        if (!modelRect) {
            status.textContent = touchAnimText('modelBoundsUnavailable', '无法读取模型边界')
            return
        }
        drawing = true
        status.textContent = ''
        const startPoint = clampPointToRect(pointFromPointerEvent(event), modelRect)
        lastPreviewPointer = startPoint
        hideHoverLabel()
        const hitHandle = getResizeHandleAtPoint(startPoint)
        interactionStartPoint = startPoint
        interactionStartRect = cloneRect(selectionRect)
        resizeHandle = hitHandle

        if (hitHandle && selectionRect) {
            interactionMode = 'resize'
        } else if (pointInRect(startPoint, selectionRect)) {
            interactionMode = 'move'
            resizeHandle = null
        } else {
            interactionMode = 'draw'
            resizeHandle = null
            selectionRect = { x: startPoint.x, y: startPoint.y, width: 0, height: 0 }
        }
        applyBoxRect(selectionBox, selectionRect)
        selectionBox.classList.add('is-editing')
        previewWrap.setPointerCapture(event.pointerId)
    })

    previewWrap.addEventListener('pointermove', (event) => {
        const modelRect = getModelPreviewRect()
        if (!modelRect) return
        const rawPoint = pointFromPointerEvent(event)
        lastPreviewPointer = rawPoint
        if (!drawing) {
            updatePreviewCursor(rawPoint)
            updateHoverLabelTarget(rawPoint)
            return
        }
        hideHoverLabel()
        const current = clampPointToRect(pointFromPointerEvent(event), modelRect)
        if (interactionMode === 'move' && interactionStartRect && interactionStartPoint) {
            selectionRect = clampSelectionRectToModel({
                x: interactionStartRect.x + current.x - interactionStartPoint.x,
                y: interactionStartRect.y + current.y - interactionStartPoint.y,
                width: interactionStartRect.width,
                height: interactionStartRect.height
            }, modelRect)
        } else if (interactionMode === 'resize') {
            selectionRect = rectFromResize(interactionStartRect, resizeHandle, current, interactionStartPoint, modelRect)
        } else {
            selectionRect = rectFromPoints(interactionStartPoint, current)
        }
        applyBoxRect(selectionBox, selectionRect)
    })

    const finishDrawing = (event) => {
        if (!drawing) return
        drawing = false
        interactionMode = null
        resizeHandle = null
        interactionStartPoint = null
        interactionStartRect = null
        selectionBox.classList.remove('is-editing')
        const point = pointFromPointerEvent(event)
        lastPreviewPointer = point
        updatePreviewCursor(point)
        updateHoverLabelTarget(point)
        try {
            previewWrap.releasePointerCapture(event.pointerId)
        } catch (_) {}
    }
    previewWrap.addEventListener('pointerup', finishDrawing)
    previewWrap.addEventListener('pointercancel', finishDrawing)
    previewWrap.addEventListener('pointerleave', () => {
        lastPreviewPointer = null
        hideHoverLabel()
    })

    async function waitForActiveTouchSetSave() {
        const startedAt = Date.now()
        while (isSaving) {
            if (Date.now() - startedAt >= TOUCH_SET_SAVE_TIMEOUT_MS) {
                console.error('[TouchSet] Waiting for active save timed out')
                return false
            }
            await new Promise(resolve => setTimeout(resolve, 50))
        }
        return true
    }

    async function flushTouchSetSaveBeforePreviewClose() {
        if (autoSaveTimeout) {
            clearTimeout(autoSaveTimeout)
            autoSaveTimeout = null
        }

        const saveAvailable = await waitForActiveTouchSetSave()
        if (!saveAvailable) return false
        if (autoSaveTimeout) {
            clearTimeout(autoSaveTimeout)
            autoSaveTimeout = null
        }

        isSaving = true
        try {
            const success = await saveTouchSetToServer()
            if (success) {
                showSaveIndicator()
            }
            return success
        } finally {
            isSaving = false
        }
    }

    async function drainTouchSetSaveBeforePreviewReload() {
        let shouldFlushQueuedSave = !!autoSaveTimeout
        if (shouldFlushQueuedSave) {
            clearTimeout(autoSaveTimeout)
            autoSaveTimeout = null
        }

        const saveAvailable = await waitForActiveTouchSetSave()
        if (!saveAvailable) return false
        if (autoSaveTimeout) {
            clearTimeout(autoSaveTimeout)
            autoSaveTimeout = null
            shouldFlushQueuedSave = true
        }
        if (!shouldFlushQueuedSave) return true

        isSaving = true
        try {
            return await saveTouchSetToServer()
        } finally {
            isSaving = false
        }
    }

    saveButton.onclick = async function() {
        if (saveButton.disabled) return
        const rect = selectionToNormalizedRect()
        if (!rect) {
            status.textContent = touchAnimText('selectAreaFirst', '请先框选一个有效区域')
            return
        }
        const effectivePieces = getEffectivePiecesForDraft(rect)
        if (effectivePieces.length === 0) {
            status.textContent = touchAnimText('customAreaCoveredByExisting', '该区域已被更早创建的区域完全覆盖')
            return
        }
        const id = draftAreaId
        const name = nameInput.value.trim() || id
        if (typeof options.onSave === 'function') {
            options.onSave({
                id,
                type: 'rect',
                name,
                createdAt: draftCreatedAt,
                rect
            })
        }
        saveButton.disabled = true
        cancelButton.disabled = true
        saveBeforeCloseInProgress = true
        try {
            const saveSuccess = await flushTouchSetSaveBeforePreviewClose()
            if (!saveSuccess) {
                status.textContent = window.t
                    ? window.t('live2d.saveFailedGeneral', '保存失败!')
                    : '保存失败!'
                return
            }
        } finally {
            saveBeforeCloseInProgress = false
            saveButton.disabled = false
            cancelButton.disabled = false
        }
        floatingWindow.close()
    }

    floatingWindow.onClose = async function() {
        if (animationFrameId) {
            cancelAnimationFrame(animationFrameId)
            animationFrameId = null
        }
        const shouldReloadSourceModel = previewLoadStarted
        const canReloadSourceModel = shouldReloadSourceModel
            ? await drainTouchSetSaveBeforePreviewReload()
            : true
        destroyPreviewModel()
        if (shouldReloadSourceModel && canReloadSourceModel && typeof window.reloadCurrentLive2DModelInModelManager === 'function') {
            const reloadPromise = window.reloadCurrentLive2DModelInModelManager({
                reason: 'custom-touch-area-preview-close'
            })
            if (reloadPromise && typeof reloadPromise.catch === 'function') {
                reloadPromise.catch(error => {
                    console.warn('[TouchConfig] Failed to reload model manager Live2D model:', error)
                })
            }
        }
    }

    loadPreviewModel().catch(error => {
        if (previewDisposed) return
        console.warn('[TouchConfig] Failed to load custom touch area preview model:', error)
        status.textContent = touchAnimText('previewUnavailable', '当前无法打开自定义区域预览')
    })
    animationFrameId = requestAnimationFrame(drawPreviewFrame)
}

function closeAllMultiselects(e){
    if (!e.target.closest('.custom-multiselect')) {
        document.querySelectorAll('.custom-multiselect.active').forEach(ms => {
            ms.classList.remove('active')
            const h = ms.querySelector('.multiselect-header')
            if (h) h.setAttribute('aria-expanded', 'false')
        })
    }
}

function createMultiSelect(type, options, selectedValues = [], hitAreaId){
    
    const multiselect = document.createElement("div")
    multiselect.className = "custom-multiselect"
    multiselect.dataset.type = type
    multiselect.dataset.hitAreaId = hitAreaId
    
    const header = document.createElement("div")
    header.className = "multiselect-header"
    header.setAttribute("role", "button")
    header.setAttribute("aria-haspopup", "listbox")
    header.setAttribute("aria-expanded", "false")
    
    const selectedText = document.createElement("span")
    selectedText.className = "selected-text"
    selectedText.textContent = type === "motion" ? window.t('live2d.selectMotion', '选择动作') : window.t('live2d.selectExpression', '选择表情')
    header.appendChild(selectedText)
    
    multiselect.appendChild(header)
    
    const optionsDiv = document.createElement("div")
    optionsDiv.className = "multiselect-options"
    
    options.forEach(option => {
        const item = document.createElement("div")
        item.className = "multiselect-item"
        
        const checkbox = document.createElement("input")
        checkbox.type = "checkbox"
        checkbox.value = option
        
        if (selectedValues.includes(option)) {
            checkbox.checked = true
        }
        
        const label = document.createElement("span")
        label.textContent = option
        
        item.appendChild(checkbox)
        item.appendChild(label)
        optionsDiv.appendChild(item)
        
        item.onclick = function(e){
            if (e.target !== checkbox) {
                checkbox.checked = !checkbox.checked
            }
            updateMultiSelectHeader(multiselect)
            triggerAutoSave()
        }
        
        checkbox.onchange = function(){
            updateMultiSelectHeader(multiselect)
            triggerAutoSave()
        }
    })
    
    multiselect.appendChild(optionsDiv)
    
    header.onclick = function(e){
        e.stopPropagation()
        const isActive = multiselect.classList.contains("active")
        
        if (!isActive) {
            const headerRect = header.getBoundingClientRect()
            const spaceBelow = window.innerHeight - headerRect.bottom
            const optionsHeight = 250
            
            if (spaceBelow < optionsHeight) {
                multiselect.classList.add("open-up")
            } else {
                multiselect.classList.remove("open-up")
            }
        }
        
        multiselect.classList.toggle("active")
        header.setAttribute("aria-expanded", !isActive)
        
        if (!isActive) {
            requestAnimationFrame(() => {
                if (optionsDiv.scrollHeight > optionsDiv.clientHeight) {
                    optionsDiv.classList.add('has-scrollbar')
                } else {
                    optionsDiv.classList.remove('has-scrollbar')
                }
            })
        }
    }
    
    updateMultiSelectHeader(multiselect)
    
    return multiselect
}

let autoSaveTimeout = null
let isSaving = false

function triggerAutoSave() {
    syncCurrentTouchSetConfigToManager()

    if (autoSaveTimeout) {
        clearTimeout(autoSaveTimeout)
    }
    
    autoSaveTimeout = setTimeout(async () => {
        if (isSaving) {
            triggerAutoSave()
            return
        }
        
        isSaving = true
        try {
            const success = await saveTouchSetToServer()
            
            if (success) {
                showSaveIndicator()
            }
        } finally {
            isSaving = false
        }
    }, 500)
}

function showSaveIndicator() {
    let indicator = document.getElementById('touch-set-save-indicator')
    if (!indicator) {
        indicator = document.createElement('div')
        indicator.id = 'touch-set-save-indicator'
        indicator.textContent = window.t('live2d.touchAnim.saved', '已保存')
        document.body.appendChild(indicator)
    }
    
    indicator.textContent = window.t('live2d.touchAnim.saved', '已保存')
    indicator.style.opacity = '1'
    
    setTimeout(() => {
        indicator.style.opacity = '0'
    }, 1500)
}

function updateMultiSelectHeader(multiselect){
    const checkboxes = multiselect.querySelectorAll('input[type="checkbox"]:checked')
    const headerContainer = multiselect.querySelector('.selected-text')
    
    headerContainer.innerHTML = ''
    
    if (checkboxes.length === 0) {
        headerContainer.textContent = window.t('live2d.touchAnim.select', '选择')
    } else {
        checkboxes.forEach(cb => {
            const label = cb.closest('.multiselect-item').querySelector('span').textContent
            const tag = document.createElement('span')
            tag.className = 'selected-tag'
            tag.textContent = label
            headerContainer.appendChild(tag)
        })
    }
}

function createTouchConfigFloatingWindow(options = {}){
    const {
        title = "HitArea 信息",
        content = null,
        showCloseButton = true
    } = options

    const overlay = document.createElement("div")
    overlay.className = "touch-config-overlay"
    
    const modal = document.createElement("div")
    modal.className = "touch-config-window"
    
    const header = document.createElement("div")
    header.className = "touch-config-header"
    
    const titleElement = document.createElement("h3")
    titleElement.textContent = title
    titleElement.dataset.text = title
    header.appendChild(titleElement)
    
    if (showCloseButton) {
        const closeButton = document.createElement("button")
        closeButton.className = "touch-config-close"
        closeButton.innerHTML = '<img src="/static/icons/close_button.png" alt="关闭" draggable="false">'
        closeButton.onclick = function(){
            windowObj.close()
        }
        header.appendChild(closeButton)
    }
    
    modal.appendChild(header)
    
    const contentContainer = document.createElement("div")
    contentContainer.className = "touch-config-content"
    modal.appendChild(contentContainer)
    
    if (content) {
        const contentDiv = document.createElement("div")
        contentDiv.innerHTML = content
        contentContainer.appendChild(contentDiv)
    }
    
    overlay.appendChild(modal)
    document.body.appendChild(overlay)
    
    const windowObj = {
        onClose: null,
        getContentContainer: function(){
            return contentContainer
        },
        close: function(cleanup){
            if (typeof cleanup === 'function') cleanup();
            if (typeof windowObj.onClose === 'function') windowObj.onClose();
            document.body.removeChild(overlay)
        },
        setTitle: function(text){
            titleElement.textContent = text
            titleElement.dataset.text = text
        }
    }

    overlay.onclick = function(e){
        if (e.target === overlay) {
            windowObj.close()
        }
    }

    return windowObj
}


async function touchPage_init(){

    
    while(typeof window.t !== 'function'){
        await new Promise(resolve => setTimeout(resolve, 500));
    }

    
    
    function sset(s,d){
        Object.keys(d).forEach((key) => {
            if (key == "innerHTML"){
                s.innerHTML=d[key]
            }else{
                s.setAttribute(key, d[key])
            }
        })
    }

    // const modelType = localStorage.getItem('modelType')

    // if ( modelType != 'live2d'){
    //     // 先弄着live2d罢
    //     return
    // }
    const touch_set_block =  document.getElementById("touch_set")

    if( touch_set_block == null){
        // 是主界面
        return 
    }

    const d = document.createElement("button")
    touch_set_block.appendChild(d)
    sset(d,{id:"touch-anim-btn","class":"btn btn-primary",type:"button","data-i18n-title":"live2d.touchAnim.title"})
    
    const icon = document.createElement("img")
    sset(icon,{src:"/static/icons/persistent_expression_icon.png?v=1",class:"persistent-expression-icon","data-i18n-alt":"live2d.touchAnim.title"})
    d.appendChild(icon)
    
    const text = document.createElement("span")
    const displayText = window.t('live2d.touchAnim.title', '触摸动画配置')
    sset(text,{id:"touch-anim-text","class":"round-stroke-text","data-i18n":"live2d.touchAnim.title","data-text":displayText,"innerHTML":displayText})
    d.appendChild(text)
    
    d.onclick = function(){
        touchPage_open(d)
    }

}

// ===== 模型加载完成 / 跨窗口保存后的触摸配置刷新 =====
// 主页面只在启动时快照一次 touch_set（InitializationTouchSet）。模型管理页保存
// 的自定义触摸区域只落盘不推送，运行中的主页面永远看不到，直到手动刷新页面。
// 这里提供两个刷新入口：
//   1. nekoRefreshLive2DTouchSet：给"模型加载完成"的钩子用——数据刚拉取过就只给
//      新模型补上原生 HitArea 接线，过期则重新拉取；
//   2. neko_touch_set_channel：给"模型管理页保存成功"用——广播一个轻量信号，
//      主页面收到后强制重拉，保存后无需刷新主页面即生效。

const TOUCH_SET_LOADED_REUSE_WINDOW_MS = 5000
const TOUCH_SET_RETRY_DELAY_MS = 1500

async function refreshTouchSetForLoadedModel(options = {}) {
    const manager = window.live2dManager
    if (!manager || typeof manager.getCurrentModel !== 'function') return false
    const model = manager.getCurrentModel()
    if (!model || model.destroyed) {
        // 模型尚未就绪（如保存广播先于初始模型加载到达）：直接丢弃通知会让
        // 启动初始化取走的保存前快照一直生效。记录待强制刷新标记，模型加载
        // 完成钩子下次调用本函数时消费，保证本次保存最终生效。
        manager._touchSetPendingForceRefetch = true
        return false
    }

    const pendingForceRefetch = manager._touchSetPendingForceRefetch === true
    manager._touchSetPendingForceRefetch = false
    const loadedAt = Number(manager._touchSetLoadedAt || 0)
    const shouldRefetch = options.forceRefetch === true || pendingForceRefetch
        || (Date.now() - loadedAt) > TOUCH_SET_LOADED_REUSE_WINDOW_MS

    // 未实际提交（角色名解析暂时失败、守卫拦截、被更新刷新世代取代等）时的
    // 收尾：按既有规则恢复承载保存通知的标记，并安排一次有界重试——本刷新
    // 可能抢占了仍在途的启动初始化（世代号已使其失效），若本刷新又瞬时失败，
    // 页面会既无触摸数据也无原生接线，直到下次重载。
    const handleUncommitted = () => {
        if (pendingForceRefetch || options.forceRefetch === true) {
            manager._touchSetPendingForceRefetch = true
        }
        if (!options.isRetry) {
            // 重试绑定发起时的角色：1.5s 后若已再次切换角色，这条旧角色的
            // 重试必须作废——否则它会以更新的世代号把旧角色的配置提交到
            // 新角色的模型上。
            const retryLanlanName = String(options.lanlanName || window.lanlan_config?.lanlan_name || '')
            setTimeout(() => {
                const currentName = String(window.lanlan_config?.lanlan_name || '')
                if (retryLanlanName && currentName && retryLanlanName !== currentName) {
                    console.log('[TouchSet] 已切换角色，丢弃过期角色的触摸配置重试:', retryLanlanName)
                    return
                }
                refreshTouchSetForLoadedModel(Object.assign({}, options, { isRetry: true }))
                    .catch((retryError) => {
                        console.warn('[TouchSet] 触摸配置重取重试失败:', retryError)
                    })
            }, TOUCH_SET_RETRY_DELAY_MS)
        }
    }

    if (shouldRefetch) {
        try {
            const committed = await InitializationTouchSet(undefined, {
                allowLive2DReload: options.allowLive2DReload === true,
                lanlanName: options.lanlanName
            })
            if (committed === 'superseded') {
                // 被更新刷新取代：提交与失败重试均由新调用接管；此处安排重试
                // 反而会以旧世代号丢弃新调用的在途结果。
                return true
            }
            if (committed !== true) {
                handleUncommitted()
            }
        } catch (error) {
            handleUncommitted()
            throw error
        }
        return true
    }

    // 数据是刚拉取的（启动初始化刚完成），只需要给新加载的模型补上原生 HitArea 接线。
    manager.touchSetFilter = {}
    manager.touchSetHitEventLock = false
    manager.setupHitAreaInteraction(model)
    return true
}

window.nekoRefreshLive2DTouchSet = refreshTouchSetForLoadedModel

const TOUCH_SET_SYNC_CHANNEL_NAME = 'neko_touch_set_channel'
let touchSetSyncChannel = null
try {
    if (typeof BroadcastChannel !== 'undefined') {
        touchSetSyncChannel = new BroadcastChannel(TOUCH_SET_SYNC_CHANNEL_NAME)
    }
} catch (_) {
    touchSetSyncChannel = null
}

function broadcastTouchSetUpdated(modelName) {
    try {
        if (touchSetSyncChannel) {
            touchSetSyncChannel.postMessage({
                model_name: modelName || '',
                timestamp: Date.now()
            })
        }
    } catch (_) {}
}

if (touchSetSyncChannel) {
    touchSetSyncChannel.onmessage = (event) => {
        const message = event && event.data
        if (!message || typeof message !== 'object') return
        const manager = window.live2dManager
        if (!manager) return
        const incomingModel = String(message.model_name || '')
        const currentModelName = String(manager.modelName || '')
        // 消息指向别的模型时跳过：那个模型的配置会在 reload_model 完成后的
        // 刷新钩子里生效，这里不提前把别模型的配置拉进来。
        if (incomingModel && currentModelName && incomingModel !== currentModelName) return
        // 绕过守卫与否按真实启用的渲染器定：lanlan_config.model_type 由热切换
        // Live2D 分支回写，比 localStorage/vrmManager 的残留指针可靠。Live2D
        // 真正在用时才旁路；VRM/PNGTuber 在用时保留守卫（触摸配置无需生效）。
        const activeType = String(window.lanlan_config?.model_type || 'live2d').toLowerCase()
        refreshTouchSetForLoadedModel({
            forceRefetch: true,
            allowLive2DReload: activeType === 'live2d'
        }).catch((error) => {
            console.warn('[TouchSet] 响应保存通知刷新触摸配置失败:', error)
        })
    }
}

async function startTouchConfigAfterStorageBarrier() {
    if (typeof window.waitForStorageLocationStartupBarrier === 'function') {
        try {
            await window.waitForStorageLocationStartupBarrier();
        } catch (_) {}
    } else if (window.__nekoStorageLocationStartupBarrier
        && typeof window.__nekoStorageLocationStartupBarrier.then === 'function') {
        try {
            await window.__nekoStorageLocationStartupBarrier;
        } catch (_) {}
    }

    touchPage_init()
    InitializationTouchSet();
}

startTouchConfigAfterStorageBarrier()
