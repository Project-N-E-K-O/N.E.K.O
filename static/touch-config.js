
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
    // 根据当前模型 ID 获取对应配置
    // const live2dManager = window.live2dManager;
    // const modelId = live2dManager?.modelName || '';
    window.touchSet = structuredClone( touchSet || {}) ;

    window.live2dManager.setupHitAreaInteraction(model)
    window.live2dManager.touchSetFilter = {}
    window.live2dManager.touchSetHitEventLock = false
}

function copyTouchSet(){
    window.live2dManager.touchSet = structuredClone(window.touchSet || {})
}
function saveTempTouchSet(){
    // const 用于检查的变量A = window.touchSet //仅调试时检查不做实际用处
    // const 用于检查的变量B = window.live2dManager?.touchSet //仅调试时检查不做实际用处
    const nowmodle = window.live2dManager?.modelName || '';
    function getTagblock(T,tagname){
        for(let i = 0; i < T.children.length;i++){
            if (Array.from(T.children[i].classList).includes(tagname)){
                return T.children[i]
            }
        }
        return null
    }
    const nowmodle_touchSet = {};
    
    const hitAreaItems = document.querySelectorAll('.hitarea-item');
    hitAreaItems.forEach(item => {
        const titleElement = item.querySelector('.hitarea-title');
        const hitAreaId = titleElement.dataset.hitAreaId || titleElement.textContent.replace('HitAreaID: ', '');



        let motionMSdomlist = getTagblock(item,'touch_set_motion')
        let expressionMSdomlist = getTagblock(item,'touch_set_expression')
        const motionMS = []
        const expressionMS = []
        
        if (motionMSdomlist !== null){
            const ddddd = Array.from(motionMSdomlist.children[1].children[0].children[0].children)
            ddddd.forEach((i) => {
                if (i.textContent){
                    motionMS.push(i.textContent)
                }
            })
        }

        if (expressionMSdomlist !== null){
            const ddddd = Array.from(expressionMSdomlist.children[1].children[0].children[0].children)
            ddddd.forEach((i) => {
                if (i.textContent){
                    expressionMS.push(i.textContent)
                }
            })
        }



        nowmodle_touchSet[hitAreaId] = {motions:motionMS,expressions:expressionMS}

    });
    
    window.touchSet[nowmodle] = nowmodle_touchSet;
    console.log("触摸反应 配置已存储到 window.touchSet:", window.touchSet[nowmodle]);
}

function showTouchSetConfigWindow(hitAreas, motions, expressions){
    
    const floatingWindow = createTouchConfigFloatingWindow({
        title: window.t('live2d.touchAnim.title', '触摸动画配置'),
        showCloseButton: false
    })
    
    const container = floatingWindow.getContentContainer()
    
    const style = document.createElement("style")
    style.textContent = `
        .hitarea-config {
            max-height: 500px;
            overflow-y: auto;
        }
        .hitarea-item {
            margin-bottom: 20px;
            padding: 15px;
            background: #f5f5f5;
            border-radius: 8px;
            border-left:4px solid #40C5F1;
        }
        .hitarea-title {
            font-weight: bold;
            color: #333;
            margin-bottom: 12px;
            font-size: 14px;
        }
        .hitarea-section {
            margin-bottom: 10px;
        }
        .hitarea-label {
            display: block;
            font-size: 12px;
            color: #666;
            margin-bottom: 6px;
        }
        .custom-multiselect {
            position: relative;
            width: 100%;
        }
        .multiselect-header {
            width: 90%;
            padding: 12px 16px;
            background: white;
            border: 2px solid #ddd;
            border-radius: 8px;
            color: #40C5F1;
            font-size: 14px;
            cursor: pointer;
            transition: all 0.2s ease;
            display: flex;
            justify-content: space-between;
            align-items: center;
            min-height: 46px;
            user-select: none;
        }
        .multiselect-header:hover {
            border-color: #40C5F1;
            transform: translateY(-1px);
        }
        .multiselect-header::after {
            content: '';
            width: 12px;
            height: 12px;
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath fill='%2340C5F1' d='M6 9L1 4h10z'/%3E%3C/svg%3E");
            background-repeat: no-repeat;
            background-position: center;
            transition: transform 0.2s ease;
        }
        .custom-multiselect.active .multiselect-header::after {
            transform: rotate(180deg);
        }
        .multiselect-options {
            position: absolute;
            top: calc(100% + 8px);
            left: 0;
            width: 100%;
            background: white;
            border: 2px solid #ddd;
            border-radius: 8px;
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.15);
            z-index: 100;
            max-height: 250px;
            overflow-y: auto;
            display: none;
            padding: 8px;
            scrollbar-width: thin;
            scrollbar-color: #96e8ff #f0f8ff;
        }
        .multiselect-options::-webkit-scrollbar {
            width: 8px;
        }
        .multiselect-options::-webkit-scrollbar-track {
            background: #f0f8ff;
            border-radius: 4px;
        }
        .multiselect-options::-webkit-scrollbar-thumb {
            background: #96e8ff;
            border-radius: 4px;
        }
        .multiselect-options::-webkit-scrollbar-thumb:hover {
            background: #7dd3ff;
        }
        .custom-multiselect.active .multiselect-options {
            display: block;
        }
        .multiselect-item {
            padding: 10px 12px;
            border-radius: 6px;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 10px;
            transition: background 0.2s ease;
            font-size: 13px;
            color: #333;
        }
        .multiselect-item:hover {
            background: #e3f4ff;
        }
        .multiselect-item input[type="checkbox"] {
            width: 16px;
            height: 16px;
            cursor: pointer;
            accent-color: #40C5F1;
        }
        .multiselect-item span {
            flex: 1;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .multiselect-header .selected-text {
            flex: 1;
            display: flex;
            flex-wrap: wrap;
            gap: 4px;
            overflow: hidden;
            max-height: 70px;
        }
        .selected-tag {
            background: #e3f4ff;
            color: #22b3ff;
            padding: 2px 8px;
            border-radius: 12px;
            font-size: 11px;
            border: 1px solid #b3e5fc;
            white-space: nowrap;
            max-width: 120px;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .multiselect-header .selected-count {
            background: #40C5F1;
            color: white;
            padding: 2px 8px;
            border-radius: 10px;
            font-size: 12px;
            margin-left: 8px;
            flex-shrink: 0;
        }
        .hitarea-buttons {
            display: flex;
            gap: 10px;
            margin-top: 20px;
        }
        .hitarea-btn {
            flex: 1;
            padding: 10px 20px;
            border: none;
            border-radius: 6px;
            font-size: 14px;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.2s ease;
        }
        .hitarea-btn-primary {
            background: #40C5F1;
            color: white;
        }
        .hitarea-btn-primary:hover {
            background: #22b3ff;
            transform: translateY(-1px);
        }
        .hitarea-btn-secondary {
            background: white;
            color: #40C5F1;
            border: 1px solid #40C5F1;
        }
        .hitarea-btn-secondary:hover {
            background: #f0f8ff;
        }
    `
    container.appendChild(style)
    
    const nowmodle = window.live2dManager?.modelName || '';
    const TouchSet = window.touchSet[nowmodle] || {};
    
    const closeButton = document.createElement("button")
    closeButton.className = "hitarea-btn hitarea-btn-secondary"
    closeButton.textContent = window.t('live2d.touchAnim.close', '关闭')
    closeButton.style.cssText = `
        order: 999;
        margin-top: 20px;
    `

    const cleanupMultiselect = () => {
        document.removeEventListener('click', closeAllMultiselects);
    };
    closeButton.onclick = function(){
        saveTempTouchSet()
        cleanupMultiselect()
        console.log("[TouchSet] 暂存完成")
        floatingWindow.close()
    }
    
    container.appendChild(closeButton)
    
    const configDiv = document.createElement("div")
    configDiv.className = "hitarea-config"
    configDiv.id = configDiv.className
    
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
                        motionOptionsSet.add(motion.File.split("motions/")[1].replace(".motion3","").replace(".json",""))
                    }
                })
            }
        })
        const motionOptions = Array.from(motionOptionsSet).sort((a, b) => a.localeCompare(b))
        const motionMultiselect = createMultiSelect("motion", motionOptions, selectedMotions)
        motionSection.appendChild(motionMultiselect)
        itemDiv.appendChild(motionSection)
        
        const expressionSection = document.createElement("div")
        expressionSection.className = "hitarea-section touch_set_expression"
        
        const expressionLabel = document.createElement("label")
        expressionLabel.className = "hitarea-label"
        expressionLabel.textContent = window.t('live2d.touchAnim.selectExpression', '绑定表情') + ":"
        expressionSection.appendChild(expressionLabel)
        
        const selectedExpressions = TouchSet[hitAreaId]?.expressions || [];
        const expressionMultiselect = createMultiSelect("expression", expressions.map(e => e.Name), selectedExpressions)
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

function createMultiSelect(type, options, selectedValues = []){
    
    const multiselect = document.createElement("div")
    multiselect.className = "custom-multiselect"
    
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
        }
    })
    
    multiselect.appendChild(optionsDiv)
    
    header.onclick = function(e){
        e.stopPropagation()
        multiselect.classList.toggle("active")
        header.setAttribute("aria-expanded", multiselect.classList.contains("active"))
    }
    
    updateMultiSelectHeader(multiselect)
    
    return multiselect
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
    // console.error("createTouchConfigFloatingWindow()")
    const {
        title = "HitArea 信息",
        content = null,
        showCloseButton = true
    } = options

    const overlay = document.createElement("div")
    overlay.style.cssText = `
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        background: rgba(0, 0, 0, 0.5);
        display: flex;
        justify-content: center;
        align-items: center;
        z-index: 10000;
    `
    
    const modal = document.createElement("div")
    modal.style.cssText = `
        background: white;
        padding: 30px;
        border-radius: 10px;
        max-width: 500px;
        width: 90%;
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);
        font-family: Arial, sans-serif;
    `
    
    const titleElement = document.createElement("h3")
    titleElement.textContent = title
    titleElement.style.cssText = `
        margin: 0 0 20px 0;
        color: #333;
    `
    modal.appendChild(titleElement)
    
    const contentContainer = document.createElement("div")
    contentContainer.style.cssText = `
        display: flex;
        flex-direction: column;
        line-height: 1.6;
        color: #555;
    `
    modal.appendChild(contentContainer)
    
    if (content) {
        const contentDiv = document.createElement("div")
        contentDiv.innerHTML = content
        contentContainer.appendChild(contentDiv)
    }
    
    if (showCloseButton) {
        const closeButton = document.createElement("button")
        closeButton.textContent = "关闭"
        closeButton.style.cssText = `
            padding: 10px 20px;
            background: #007bff;
            color: white;
            border: none;
            border-radius: 5px;
            cursor: pointer;
            font-size: 14px;
            margin-top: 20px;
        `
        closeButton.onmouseover = function(){
            this.style.background = "#0056b3"
        }
        closeButton.onmouseout = function(){
            this.style.background = "#007bff"
        }
        closeButton.onclick = function(){
            document.body.removeChild(overlay)
        }
        contentContainer.appendChild(closeButton)
    }
    
    overlay.appendChild(modal)
    document.body.appendChild(overlay)
    
    overlay.onclick = function(e){
        if (e.target === overlay) {
            // 取消点击外部关闭窗口的功能
            // document.body.removeChild(overlay)
        }
    }
    
    return {
        getContentContainer: function(){
            return contentContainer
        },
        close: function(cleanup){
            if (typeof cleanup === 'function') cleanup();
            document.body.removeChild(overlay)
        },
        setTitle: function(text){
            titleElement.textContent = text
        }
    }
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
