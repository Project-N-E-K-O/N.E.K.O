
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

async function InitializationTouchSet(characterJson) {
    
    while(typeof window.t !== 'function'){
        await new Promise(resolve => setTimeout(resolve, 500));
    }

    const modelType = localStorage.getItem('modelType') || 'live2d';
    const isVRMActive = window.vrmManager && window.vrmManager.currentModel;
    const isMMDActive = window.mmdManager && window.mmdManager.currentModel;
    if (modelType !== 'live2d' || isVRMActive || isMMDActive) {
        console.log('[TouchSet] 当前模型类型不是 Live2D，跳过触摸配置初始化');
        return;
    }

            
    if (!characterJson){
        // // 获取角色名称
        // const lanlanName = await getLanlanName();
        
        // 优先从 URL 获取
        const urlParams = new URLSearchParams(window.location.search);
        let lanlanName = urlParams.get('lanlan_name') || '';
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
            return;
        }


        const response = await fetch('/api/characters');
        const charactersJson = await response.json();
        characterJson = charactersJson.猫娘[lanlanName]
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

    const touchSet = characterJson._reserved?.touch_set || {};
    
    if(!touchSet[window.live2dManager.modelName]){
        touchSet[window.live2dManager.modelName] = {"default":{"motions": [], "expressions": []}}
    }
    window.live2dManager.touchSet = touchSet;
    window.live2dManager.touchSetFilter = {}
    window.live2dManager.touchSetHitEventLock = false

    window.live2dManager.setupHitAreaInteraction(model)
}

async function saveTouchSetToServer() {
    const modelName = window.live2dManager?.modelName;
    const lanlanName = new URLSearchParams(window.location.search).get('lanlan_name') || window.lanlan_config?.lanlan_name;
    
    if (!modelName || !lanlanName) {
        console.error('[TouchSet] 无法保存：缺少模型名称或角色名称');
        return false;
    }
    
    const touchSetData = collectAllTouchSetData();
    
    try {
        const response = await fetch(`/api/characters/catgirl/${encodeURIComponent(lanlanName)}/touch_set`, {
            method: 'PATCH',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                model_name: modelName,
                touch_set: touchSetData
            })
        });
        
        const result = await response.json();
        
        if (result.success) {
            window.live2dManager.touchSet[modelName] = touchSetData;
            console.log(`[TouchSet] 已保存模型 ${modelName} 的触摸配置到服务器`);
            return true;
        } else {
            console.error('[TouchSet] 保存失败:', result.error);
            return false;
        }
    } catch (error) {
        console.error('[TouchSet] 保存请求失败:', error);
        return false;
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
        
        touchSetData[hitAreaId] = {
            motions: motions,
            expressions: expressions
        };
    });
    
    return touchSetData;
}

function showTouchSetConfigWindow(hitAreas, motions, expressions){
    
    const floatingWindow = createTouchConfigFloatingWindow({
        title: window.t('live2d.touchAnim.title', '触摸动画配置'),
        showCloseButton: true
    })
    
    const container = floatingWindow.getContentContainer()
    
    const nowmodle = window.live2dManager?.modelName || '';
    const TouchSet = window.live2dManager?.touchSet?.[nowmodle] || {};
    
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
    
    const configDiv = document.createElement("div")
    configDiv.className = "hitarea-config"
    
    const hitAreasCopy = [...hitAreas]
    const defaultHitArea = { id: "default", Name: "default" }
    hitAreasCopy.unshift(defaultHitArea)
    
    hitAreasCopy.forEach(hitArea => {
        const hitAreaId = hitArea.id || hitArea.Id
        const hitAreaName = hitArea.Name || hitAreaId
        
        const itemDiv = document.createElement("div")
        itemDiv.className = "hitarea-item"
        
        const titleDiv = document.createElement("div")
        titleDiv.className = "hitarea-title"
        titleDiv.dataset.hitAreaId = hitAreaId
        if (hitAreaId === "default") {
            titleDiv.textContent = window.t('live2d.touchAnim.defaultClickAnim', '默认点击动画')
        } else {
            titleDiv.textContent = `HitAreaID: ${hitAreaName}`
        }
        itemDiv.appendChild(titleDiv)
        
        const motionSection = document.createElement("div")
        motionSection.className = "hitarea-section touch_set_motion"
        
        const motionLabel = document.createElement("label")
        motionLabel.className = "hitarea-label"
        motionLabel.textContent = window.t('live2d.touchAnim.selectMotion', '绑定动作') + ":"
        motionSection.appendChild(motionLabel)
        
        const selectedMotions = TouchSet[hitAreaId]?.motions || [];
        const motionOptionsSet = new Set()
        Object.keys(motions).forEach(groupName => {
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
        const motionOptions = Array.from(motionOptionsSet).sort((a, b) => a.localeCompare(b))
        const motionMultiselect = createMultiSelect("motion", motionOptions, selectedMotions, hitAreaId)
        motionSection.appendChild(motionMultiselect)
        itemDiv.appendChild(motionSection)
        
        const expressionSection = document.createElement("div")
        expressionSection.className = "hitarea-section touch_set_expression"
        
        const expressionLabel = document.createElement("label")
        expressionLabel.className = "hitarea-label"
        expressionLabel.textContent = window.t('live2d.touchAnim.selectExpression', '绑定表情') + ":"
        expressionSection.appendChild(expressionLabel)
        
        const selectedExpressions = TouchSet[hitAreaId]?.expressions || [];
        const expressionMultiselect = createMultiSelect("expression", expressions.map(e => e.Name), selectedExpressions, hitAreaId)
        expressionSection.appendChild(expressionMultiselect)
        itemDiv.appendChild(expressionSection)
        
        configDiv.appendChild(itemDiv)
    })
    
    container.appendChild(configDiv)
    
    setTimeout(() => {
        document.addEventListener('click', closeAllMultiselects)
    }, 100)
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
        closeButton.innerHTML = '<img src="/static/icons/close_button.png" alt="关闭">'
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

touchPage_init()
InitializationTouchSet();
