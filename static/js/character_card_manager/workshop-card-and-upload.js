// Part responsibility: workshop card presentation, status snapshots, upload flow, and editor-side model/voice scans.

var GLITCH_TIMINGS = [
    {dur:'4.8s',delay:'0s'},   {dur:'5.3s',delay:'1.2s'},
    {dur:'4.5s',delay:'2.7s'}, {dur:'5.7s',delay:'0.4s'},
    {dur:'4.2s',delay:'3.5s'}, {dur:'5.1s',delay:'1.8s'},
    {dur:'4.9s',delay:'2.1s'}, {dur:'5.4s',delay:'0.9s'},
    {dur:'4.6s',delay:'3.2s'},
];
var lastCustomRingText = null;

function buildPreviewRing(customText) {
    var container = document.getElementById('preview-ring-container');
    if (!container) return;
    var text;
    if (customText && typeof customText === 'string') {
        lastCustomRingText = customText;
        text = customText;
    } else if (lastCustomRingText) {
        text = lastCustomRingText;
    } else {
        var key = 'steam.selectCharaToPreview';
        var raw = (typeof window.t === 'function') ? window.t(key) : null;
        text = (raw && raw !== key) ? raw : '请选择角色进行预览';
    }
    var base = Array.from(text);
    var chars = base.concat(base).concat(base);

    var groupSize = base.length;
    var gapExtra = 0.3;
    var totalSlots = chars.length + gapExtra * 3;

    var placeholder = container.closest('.preview-placeholder');
    var availH = placeholder ? placeholder.clientHeight : 0;
    var availW = placeholder ? placeholder.clientWidth : 0;
    var nominalRadius = Math.ceil(totalSlots * 50 / (2 * Math.PI));
    var limits = [];
    if (availH > 80) limits.push((availH - 50) * 0.65);
    if (availW > 80) limits.push((availW - 50 - 42) / 2);
    var containerDriven = limits.length ? Math.max(200, Math.min.apply(null, limits)) : 200;
    var radius = Math.min(nominalRadius, containerDriven);

    var arcPerSlot = radius * 2 * Math.PI / totalSlots;
    var fontSize = Math.max(14, Math.min(42, Math.floor(arcPerSlot) - 4));
    container.style.setProperty('--ring-char-size', fontSize + 'px');

    var yComp = Math.round(radius * Math.sin(10 * Math.PI / 180) * -0.1);
    var tiltDiv = container.closest('.preview-ring-tilt');
    if (tiltDiv) {
        tiltDiv.style.transform = 'translateY(' + yComp + 'px) rotateX(-10deg)';
    }
    container.innerHTML = '';
    chars.forEach(function(ch, i) {
        var group = Math.floor(i / groupSize);
        var posInGroup = i % groupSize;
        var slotIndex = group * (groupSize + gapExtra) + posInGroup;
        var angle = (slotIndex / totalSlots) * 360;
        var span = document.createElement('span');
        span.className = 'ring-char';
        span.textContent = ch;
        span.setAttribute('data-char', ch);
        var t = GLITCH_TIMINGS[i % GLITCH_TIMINGS.length];
        span.style.setProperty('--gdur', t.dur);
        span.style.setProperty('--gdelay', t.delay);
        span.style.transform = 'rotateY(' + angle + 'deg) translateZ(' + radius + 'px)';
        container.appendChild(span);
    });
}
window.buildPreviewRing = buildPreviewRing;

// ====== Steam 标签页内容构建 ======
function buildSteamTabContent(name, rawData, card, container) {
    container.innerHTML = '';

    // 主布局容器
    const layout = document.createElement('div');
    layout.className = 'character-card-layout';
    layout.id = 'character-card-layout';
    layout.style.display = 'flex';

    // ── 上方区域：角色卡信息 + Live2D预览 ──
    const topRow = document.createElement('div');
    topRow.className = 'character-card-top-row';

    // 左上：角色卡信息
    const infoSection = document.createElement('div');
    infoSection.className = 'character-card-info-section';

    const infoLogo = document.createElement('img');
    infoLogo.src = '/static/icons/logo_show.png';
    infoLogo.className = 'card-info-logo';
    infoLogo.alt = '';
    infoSection.appendChild(infoLogo);

    // 标题区
    const headerRow = document.createElement('div');
    headerRow.className = 'card-info-header-row';
    headerRow.innerHTML = `
        <svg class="card-info-bg-hexagons" viewBox="-10 -10 370 310" xmlns="http://www.w3.org/2000/svg">
            <defs><polygon id="hex-header-shape-p" points="25,5 75,5 100,48 75,91 25,91 0,48" fill="#8cd5ff" stroke="#8cd5ff" stroke-width="8" stroke-linejoin="round"/></defs>
            <use href="#hex-header-shape-p" x="120" y="0" opacity="0.05"/>
            <use href="#hex-header-shape-p" x="240" y="50" opacity="0.05"/>
            <use href="#hex-header-shape-p" x="0" y="50" opacity="0.05"/>
            <use href="#hex-header-shape-p" x="120" y="99" opacity="0.05"/>
            <use href="#hex-header-shape-p" x="240" y="149" opacity="0.05"/>
            <use href="#hex-header-shape-p" x="0" y="149" opacity="0.05"/>
            <use href="#hex-header-shape-p" x="120" y="198" opacity="0.05"/>
        </svg>
        <div class="card-info-title-area">
            <div class="card-info-header-text">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="white" xmlns="http://www.w3.org/2000/svg"><path d="M12 2 L14.5 9.5 L22 12 L14.5 14.5 L12 22 L9.5 14.5 L2 12 L9.5 9.5 Z" stroke="#7EC8E3" stroke-width="2" stroke-linejoin="round" fill="white"/></svg>
                <span data-i18n="steam.cardInfoPreview">${window.t ? window.t('steam.cardInfoPreview') : '角色卡信息'}</span>
            </div>
            <img src="/static/icons/paw_ui.png" class="card-info-paw" alt="">
        </div>`;
    infoSection.appendChild(headerRow);

    // 信息正文
    const infoBody = document.createElement('div');
    infoBody.className = 'card-info-body';
    infoBody.innerHTML = `
        <svg class="card-info-bg-hexagons" viewBox="-10 -10 370 310" xmlns="http://www.w3.org/2000/svg">
            <defs><polygon id="hex-body-shape-p" points="25,5 75,5 100,48 75,91 25,91 0,48" fill="#8cd5ff" stroke="#8cd5ff" stroke-width="8" stroke-linejoin="round"/></defs>
            <use href="#hex-body-shape-p" x="120" y="0" opacity="0.05"/>
            <use href="#hex-body-shape-p" x="240" y="50" opacity="0.05"/>
            <use href="#hex-body-shape-p" x="0" y="50" opacity="0.05"/>
            <use href="#hex-body-shape-p" x="120" y="99" opacity="0.05"/>
            <use href="#hex-body-shape-p" x="240" y="149" opacity="0.05"/>
            <use href="#hex-body-shape-p" x="0" y="149" opacity="0.05"/>
            <use href="#hex-body-shape-p" x="120" y="198" opacity="0.05"/>
        </svg>
        <div class="card-info-body-scroll">
            <svg class="card-info-bg-stars" xmlns="http://www.w3.org/2000/svg">
                <defs>
                    <linearGradient id="card-star-gradient-p" x1="0%" y1="0%" x2="100%" y2="100%">
                        <stop offset="0%" stop-color="#ffffff"/><stop offset="100%" stop-color="#8cd5ff"/>
                    </linearGradient>
                    <symbol id="card-rounded-star-p" viewBox="0 0 24 24">
                        <path d="M 12 3 Q 12 12 21 12 Q 12 12 12 21 Q 12 12 3 12 Q 12 12 12 3 Z" fill="#ffffff" stroke="#ffffff" stroke-width="3.5" stroke-linejoin="round"/>
                    </symbol>
                    <pattern id="card-star-pattern-p" x="0" y="0" width="80" height="80" patternUnits="userSpaceOnUse">
                        <use href="#card-rounded-star-p" x="5" y="5" width="15" height="15"/>
                        <use href="#card-rounded-star-p" x="45" y="45" width="15" height="15"/>
                    </pattern>
                    <mask id="card-stars-mask-p"><rect width="100%" height="100%" fill="url(#card-star-pattern-p)"/></mask>
                </defs>
                <rect width="100%" height="100%" fill="url(#card-star-gradient-p)" mask="url(#card-stars-mask-p)"/>
            </svg>
            <div id="card-info-preview">
                <div id="card-info-dynamic-content">
                    <p style="color: #999; text-align: center;" data-i18n="steam.selectCharacterCard">${window.t ? window.t('steam.selectCharacterCard') : '请选择一个角色卡'}</p>
                </div>
            </div>
        </div>`;
    infoSection.appendChild(infoBody);
    topRow.appendChild(infoSection);

    // 右上：模型预览
    const live2dSection = document.createElement('div');
    live2dSection.className = 'character-card-live2d-section';

    const previewTitle = document.createElement('h3');
    previewTitle.id = 'model-preview-title';
    previewTitle.setAttribute('data-i18n', 'steam.live2dPreview');
    previewTitle.textContent = 'Live2D';
    live2dSection.appendChild(previewTitle);

    const previewContainer = document.createElement('div');
    previewContainer.id = 'live2d-preview-container';

    previewContainer.innerHTML = `
        <div id="live2d-preview-content" style="flex: 1; position: relative; min-height: 0; pointer-events: none; background-color: transparent;">
            <canvas id="live2d-preview-canvas" style="display: none; width: 100%; height: 100%; position: absolute; top: 0; left: 0; pointer-events: none;"></canvas>
            <div id="vrm-preview-container" style="display: none; width: 100%; height: 100%; position: absolute; top: 0; left: 0;">
                <canvas id="vrm-preview-canvas" style="width: 100%; height: 100%;"></canvas>
            </div>
            <div id="mmd-preview-container" style="display: none; width: 100%; height: 100%; position: absolute; top: 0; left: 0;">
                <canvas id="mmd-preview-canvas" style="width: 100%; height: 100%;"></canvas>
            </div>
            <div class="preview-placeholder" style="display: flex; justify-content: center; align-items: center; height: 100%; position: relative; z-index: 1; background-color: transparent;">
                <div class="preview-ring-perspective">
                    <div class="preview-ring-tilt">
                        <div id="preview-ring-container" class="preview-ring-container"></div>
                    </div>
                </div>
            </div>
            <div id="live2d-preview-overlay" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; z-index: 100; pointer-events: auto;"></div>
            <button id="live2d-refresh-btn" style="position: absolute; top: 10px; right: 10px; z-index: 101; width: 30px; height: 30px; border: none; border-radius: 50%; background-color: transparent; color: white; cursor: pointer; display: none; justify-content: center; align-items: center; font-size: 16px; pointer-events: auto;" title="${window.t ? window.t('steam.refreshLive2DPreview') : '刷新Live2D预览'}" onclick="refreshLive2DPreview()">↻</button>
        </div>`;
    live2dSection.appendChild(previewContainer);

    // 动作/表情控件
    const controlsDiv = document.createElement('div');
    controlsDiv.id = 'live2d-preview-controls';
    controlsDiv.style.cssText = 'padding: 10px; background-color: #fff; border-top: 1px solid #e0e0e0; margin: 10px 10px 10px 10px; border-radius: 16px;';
    controlsDiv.innerHTML = `
        <div style="display: flex; gap: 10px; margin-bottom: 10px; flex-wrap: wrap;">
            <div style="flex: 1; min-width: 150px;">
                <select id="preview-motion-select" class="control-input" style="width: 100%;"></select>
                <div style="font-size: 11px; color: #888; margin-top: 3px; text-align: center;" data-i18n="character.idleMotionHint">${window.t ? window.t('character.idleMotionHint') : '保存角色时，当前选中的动作将被设为待机动作'}</div>
            </div>
            <div class="btn-play-wrapper">
                <button id="preview-play-motion-btn" class="btn" disabled>
                    <span data-i18n="steam.playMotion">${window.t ? window.t('steam.playMotion') : '播放动作'}</span>
                </button>
            </div>
        </div>
        <div style="display: flex; gap: 10px; flex-wrap: wrap;">
            <div style="flex: 1; min-width: 150px;">
                <select id="preview-expression-select" class="control-input" style="width: 100%;"></select>
            </div>
            <div class="btn-play-wrapper">
                <button id="preview-play-expression-btn" class="btn" disabled>
                    <span data-i18n="steam.playExpression">${window.t ? window.t('steam.playExpression') : '播放表情'}</span>
                </button>
            </div>
        </div>`;
    live2dSection.appendChild(controlsDiv);
    ensurePreviewPlaybackBindings();
    topRow.appendChild(live2dSection);
    layout.appendChild(topRow);

    // ── 下方区域：描述 + 标签和按钮 ──
    const bottomRow = document.createElement('div');
    bottomRow.className = 'character-card-bottom-row';

    // 左下：描述区域
    const descSection = document.createElement('div');
    descSection.className = 'character-card-description-section';

    // 描述标题栏
    const descHeader = document.createElement('div');
    descHeader.className = 'description-header-row';
    descHeader.innerHTML = `
        <div class="description-title-area">
            <div class="description-header-text">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="white" xmlns="http://www.w3.org/2000/svg"><path d="M12 2 L14.5 9.5 L22 12 L14.5 14.5 L12 22 L9.5 14.5 L2 12 L9.5 9.5 Z" stroke="#7EC8E3" stroke-width="2" stroke-linejoin="round" fill="white"/></svg>
                <span data-i18n="steam.characterCardDescription">${window.t ? window.t('steam.characterCardDescription') : '描述'}</span>
                <img src="/static/icons/paw_ui.png" class="description-paw" alt="">
            </div>
        </div>`;
    descSection.appendChild(descHeader);

    // 版权警告
    const copyrightWarning = document.createElement('div');
    copyrightWarning.id = 'copyright-warning';
    copyrightWarning.style.cssText = 'display: none; padding: 8px; background-color: #f8d7da; border: 1px solid #f5c6cb; border-radius: 4px; color: #721c24; margin-bottom: 8px; margin-top: 8px;';
    copyrightWarning.innerHTML = `<strong>⚠️</strong> <span data-i18n="steam.modelCopyrightIssue">${window.t ? window.t('steam.modelCopyrightIssue') : '您的角色形象存在版权问题，无法上传'}</span>`;
    descSection.appendChild(copyrightWarning);

    // 描述输入
    const descGroup = document.createElement('div');
    descGroup.className = 'control-group description-content';
    const descTextarea = document.createElement('textarea');
    descTextarea.id = 'character-card-description';
    descTextarea.className = 'control-input';
    descTextarea.style.cssText = 'white-space: pre-wrap; min-height: 100px; resize: none; overflow-y: auto;';
    descTextarea.placeholder = window.t ? window.t('steam.placeholderCharacterDescription') : '输入角色描述...';
    descTextarea.addEventListener('input', function () {
        if (typeof updateCardPreview === 'function') updateCardPreview();
    });
    descGroup.appendChild(descTextarea);
    descSection.appendChild(descGroup);

    // Workshop 状态区域
    const statusArea = document.createElement('div');
    statusArea.id = 'workshop-status-area';
    statusArea.style.cssText = 'display: none; padding: 8px; background-color: #e7f3ff; border: 1px solid #b3d7ff; border-radius: 4px; margin-top: 8px;';
    statusArea.innerHTML = `
        <div style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px;">
            <div>
                <strong style="color: #0066cc;">✅ <span data-i18n="steam.alreadyUploaded">${window.t ? window.t('steam.alreadyUploaded') : '已上传到创意工坊'}</span></strong>
                <div style="font-size: 12px; color: #666; margin-top: 4px;">
                    <span data-i18n="steam.uploadTime">${window.t ? window.t('steam.uploadTime') : '上传时间'}</span>：<span id="workshop-upload-time">-</span>
                </div>
                <div style="font-size: 12px; color: #666;">
                    <span data-i18n="steam.workshopItemId">${window.t ? window.t('steam.workshopItemId') : '物品ID'}</span>：<span id="workshop-item-id">-</span>
                </div>
            </div>
            <button class="btn btn-secondary btn-sm" onclick="showWorkshopSnapshot()" style="white-space: nowrap;">
                📋 <span data-i18n="steam.viewSnapshot">${window.t ? window.t('steam.viewSnapshot') : '查看已上传版本'}</span>
            </button>
        </div>`;
    descSection.appendChild(statusArea);
    bottomRow.appendChild(descSection);

    // 右下：标签和按钮区域
    const tagsButtonsSection = document.createElement('div');
    tagsButtonsSection.className = 'character-card-tags-buttons-section';

    // 标签区域
    const tagsArea = document.createElement('div');
    tagsArea.className = 'character-card-tags-area';

    const tagsLogo = document.createElement('img');
    tagsLogo.src = '/static/icons/logo_show.png';
    tagsLogo.className = 'card-info-logo';
    tagsLogo.alt = '';
    tagsArea.appendChild(tagsLogo);

    // 标签标题栏
    const tagsHeaderRow = document.createElement('div');
    tagsHeaderRow.className = 'tags-header-row';
    tagsHeaderRow.innerHTML = `
        <div class="tags-title-area">
            <div class="tags-header-text">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="white" xmlns="http://www.w3.org/2000/svg"><path d="M12 2 L14.5 9.5 L22 12 L14.5 14.5 L12 22 L9.5 14.5 L2 12 L9.5 9.5 Z" stroke="#7EC8E3" stroke-width="2" stroke-linejoin="round" fill="white"/></svg>
                <span data-i18n="steam.characterCardTags">${window.t ? window.t('steam.characterCardTags') : '角色卡标签'}</span>
            </div>
            <img src="/static/icons/paw_ui.png" class="tags-paw" alt="">
        </div>`;
    tagsArea.appendChild(tagsHeaderRow);

    // 标签输入
    const tagsControlGroup = document.createElement('div');
    tagsControlGroup.className = 'control-group tags-content';
    const tagInput = document.createElement('input');
    tagInput.type = 'text';
    tagInput.id = 'character-card-tag-input';
    tagInput.className = 'control-input';
    tagInput.placeholder = window.t ? window.t('steam.tagsPlaceholderSpace') : '输入标签，按空格添加';

    // 标签输入事件
    tagInput.addEventListener('input', function (e) {
        if (e.target.value.endsWith(' ') && e.target.value.trim() !== '') {
            e.preventDefault();
            if (typeof addTag === 'function') addTag(e.target.value.trim(), 'character-card');
            e.target.value = '';
        }
    });
    tagInput.addEventListener('keypress', function (e) {
        if (e.key === 'Enter' && e.target.value.trim() !== '') {
            e.preventDefault();
            if (typeof addTag === 'function') addTag(e.target.value.trim(), 'character-card');
            e.target.value = '';
        }
    });
    tagsControlGroup.appendChild(tagInput);

    const tagsWrapper = document.createElement('div');
    tagsWrapper.id = 'character-card-tags-wrapper';
    const tagsContainer = document.createElement('div');
    tagsContainer.className = 'tags-container';
    tagsContainer.id = 'character-card-tags-container';
    tagsWrapper.appendChild(tagsContainer);
    tagsControlGroup.appendChild(tagsWrapper);
    ensureCharacterCardTagScrollControls();
    tagsArea.appendChild(tagsControlGroup);
    tagsButtonsSection.appendChild(tagsArea);

    // 无可上传模型警告
    const noModelsWarning = document.createElement('div');
    noModelsWarning.id = 'no-uploadable-models-warning';
    noModelsWarning.style.cssText = 'display: none; padding: 10px; background-color: #fff3cd; border: 1px solid #ffc107; border-radius: 4px; color: #856404; font-size: 14px; margin-top: 15px;';
    noModelsWarning.innerHTML = `<span data-i18n="steam.noUploadableModels">${window.t ? window.t('steam.noUploadableModels') : '没有可上传的模型，请先在角色管理页面创建自定义模型'}</span>`;
    tagsButtonsSection.appendChild(noModelsWarning);

    // 按钮行
    const buttonsRow = document.createElement('div');
    buttonsRow.className = 'character-card-buttons-row';

    // 上传按钮
    const uploadWrapper = document.createElement('div');
    uploadWrapper.className = 'btn-wrapper';
    const uploadBtn = document.createElement('button');
    uploadBtn.id = 'upload-to-workshop-btn';
    uploadBtn.className = 'btn';
    uploadBtn.disabled = true;
    uploadBtn.style.cssText = 'display: flex; align-items: center; justify-content: center; gap: 6px;';
    uploadBtn.onclick = function () { if (typeof handleUploadToWorkshop === 'function') handleUploadToWorkshop(); };
    const uploadIcon = document.createElement('img');
    uploadIcon.src = '/static/icons/upload_icon.png';
    uploadIcon.style.cssText = 'width: 34px; height: 34px;';
    uploadBtn.appendChild(uploadIcon);
    const uploadText = document.createElement('span');
    uploadText.id = 'upload-btn-text';
    uploadText.setAttribute('data-i18n', 'steam.uploadToWorkshop');
    uploadText.textContent = window.t ? window.t('steam.uploadToWorkshop') : '上传到创意工坊';
    uploadBtn.appendChild(uploadText);
    uploadWrapper.appendChild(uploadBtn);
    buttonsRow.appendChild(uploadWrapper);

    // 在角色管理中编辑按钮
    const editWrapper = document.createElement('div');
    editWrapper.className = 'btn-wrapper';
    const editBtn = document.createElement('button');
    editBtn.className = 'btn';
    editBtn.style.cssText = 'display: flex; align-items: center; justify-content: center; gap: 6px;';
    editBtn.onclick = function () { window.location.href = '/character_card_manager'; };
    const editIcon = document.createElement('img');
    editIcon.src = '/static/icons/cat_icon.png';
    editIcon.style.cssText = 'width: 34px; height: 34px;';
    editBtn.appendChild(editIcon);
    const editText = document.createElement('span');
    editText.setAttribute('data-i18n', 'steam.editInCharaManager');
    editText.textContent = window.t ? window.t('steam.editInCharaManager') : '在角色管理中编辑';
    editBtn.appendChild(editText);
    editWrapper.appendChild(editBtn);
    buttonsRow.appendChild(editWrapper);

    tagsButtonsSection.appendChild(buttonsRow);
    bottomRow.appendChild(tagsButtonsSection);
    layout.appendChild(bottomRow);

    container.appendChild(layout);

    // 初始化预览环形文字
    requestAnimationFrame(function () {
        buildPreviewRing();
        requestAnimationFrame(buildPreviewRing);
        var placeholder = container.querySelector('#live2d-preview-container .preview-placeholder');
        if (placeholder && typeof ResizeObserver !== 'undefined') {
            new ResizeObserver(buildPreviewRing).observe(placeholder);
        }
    });

    // 使用 expandCharacterCardSection 填充数据
    if (card) {
        // 确保 card 有足够的信息
        const cardForExpand = {
            id: card.id || card.name || name,
            name: name,
            originalName: card.originalName || name,
            rawData: rawData,
            tags: card.tags || [],
            description: card.description || ''
        };

        // 确保角色卡列表中包含该卡
        if (window.characterCards) {
            const existingIdx = window.characterCards.findIndex(c => c.id === cardForExpand.id);
            if (existingIdx < 0) {
                window.characterCards.push(cardForExpand);
            }
        }

        expandCharacterCardSection(cardForExpand);
    }
}

// 展开角色卡区域并填充数据
function expandCharacterCardSection(card) {
    // 更新当前打开的角色卡ID
    currentCharacterCardId = card.id;

    // 立即更新角色卡预览，确保用户看到反馈
    updateCardPreview();

    // 获取原始数据，确保存在 - 兼容数据直接在card对象中的情况
    const rawData = card.rawData || card || {};

    // 提取所需信息，同时兼容中英文字段名称
    const nickname = rawData['昵称'] || rawData['档案名'] || rawData['name'] || card.name || '';
    const gender = rawData['性别'] || rawData['gender'] || '';
    const age = rawData['年龄'] || rawData['age'] || '';
    const description = rawData['描述'] || rawData['description'] || card.description || '';
    const systemPrompt = rawData['设定'] || rawData['system_prompt'] || rawData['prompt_setting'] || '';

    // 处理模型默认值 - 兼容 Live2D / VRM / MMD 三种模型类型
    let live2d = rawData['live2d'] || (rawData['model'] && rawData['model']['name']) || '';
    const modelType = rawData['model_type'] || 'live2d';
        const normalizeModelPath = value => {
            if (value && typeof value === 'object' && 'model_path' in value) {
                return String(value.model_path || '');
            }
            return String(value || '');
        };
        const vrmPath = normalizeModelPath(rawData['vrm']);
        const mmdPath = normalizeModelPath(rawData['mmd']);
    // 优先使用 live3d_sub_type（后端权威来源，含 _reserved 迁移路径）
    const explicitLive3dSubType = String(
        rawData['_reserved']?.avatar?.live3d_sub_type
        || rawData['live3d_sub_type']
        || ''
    ).trim().toLowerCase();

    // 判断实际模型类型：优先使用显式 live3d_sub_type，缺失时再根据路径区分 VRM/MMD
    let effectiveModelType = 'live2d';
    let effectiveModelPath = '';
    if (modelType === 'live3d' || modelType === 'vrm') {
        if (explicitLive3dSubType === 'mmd') {
            effectiveModelType = 'mmd';
            effectiveModelPath = mmdPath;
        } else if (explicitLive3dSubType === 'vrm') {
            effectiveModelType = 'vrm';
            effectiveModelPath = vrmPath;
        } else if (mmdPath && !vrmPath) {
            effectiveModelType = 'mmd';
            effectiveModelPath = mmdPath;
        } else if (vrmPath) {
            effectiveModelType = 'vrm';
            effectiveModelPath = vrmPath;
        }
    } else {
        effectiveModelType = 'live2d';
    }

    // 处理音色默认值
    let voiceId = rawData['voice_id'] || (rawData['voice'] && rawData['voice']['voice_id']);

    // 填充可编辑字段（Description 使用 textarea.value）
    const descEl = document.getElementById('character-card-description');
    if (descEl) descEl.value = description || '';

    // 存储当前角色卡的模型名称和类型供后续使用
    window.currentCharacterCardModel = (effectiveModelType !== 'live2d' && effectiveModelPath) ? effectiveModelPath : live2d;
    window.currentCharacterCardModelType = effectiveModelType;
    window.currentCharacterCardModelPath = effectiveModelPath;
    const currentLive2DModelInfo = effectiveModelType === 'live2d' ? getLive2DModelInfo(live2d) : null;
    window.currentCharacterCardModelSource = currentLive2DModelInfo && currentLive2DModelInfo.source ? currentLive2DModelInfo.source : '';
    window._currentCardRawData = rawData;

    // 检查模型是否可上传（检查是否来自static目录）
    const uploadButton = document.getElementById('upload-to-workshop-btn');
    const copyrightWarning = document.getElementById('copyright-warning');
    const noModelsWarning = document.getElementById('no-uploadable-models-warning');

    // 根据模型类型检查是否可上传
    let isModelUploadable = false;
    let hasModel = false;
    if (effectiveModelType === 'vrm' && effectiveModelPath) {
        hasModel = true;
        // VRM：检查路径是否为用户目录（非 /static/vrm/）
        isModelUploadable = availableVrmModels.some(m => m.url === effectiveModelPath || m.path === effectiveModelPath);
        // 也可能路径匹配不上列表（例如路径格式差异），退而检查是否不在 static 目录
        if (!isModelUploadable && !effectiveModelPath.startsWith('/static/')) {
            isModelUploadable = true;
        }
    } else if (effectiveModelType === 'mmd' && effectiveModelPath) {
        hasModel = true;
        // MMD：检查路径是否为用户目录（非 /static/mmd/）
        isModelUploadable = availableMmdModels.some(m => m.url === effectiveModelPath);
        if (!isModelUploadable && !effectiveModelPath.startsWith('/static/')) {
            isModelUploadable = true;
        }
    } else if (live2d) {
        hasModel = true;
        // Live2D：原有逻辑
        const modelInfo = availableModels.find(m => m.name === live2d);
        isModelUploadable = modelInfo !== undefined;
    }

    // 同时检查系统提示词
    const hasSystemPrompt = systemPrompt && systemPrompt.trim() !== '';

    // 决定是否可以上传
    let canUpload = true;
    let disableReason = '';

    if (!hasModel) {
        // 没有模型
        canUpload = false;
        disableReason = window.t ? window.t('steam.noModelSelected') : '未选择模型';
        if (noModelsWarning) noModelsWarning.style.display = 'block';
        if (copyrightWarning) copyrightWarning.style.display = 'none';
    } else if (!isModelUploadable) {
        // 模型存在版权问题（来自static目录）
        canUpload = false;
        disableReason = window.t ? window.t('steam.modelCopyrightIssue') : '您的角色形象存在版权问题，无法上传';
        if (copyrightWarning) copyrightWarning.style.display = 'block';
        if (noModelsWarning) noModelsWarning.style.display = 'none';
    } else {
        // 可以上传
        if (copyrightWarning) copyrightWarning.style.display = 'none';
        if (noModelsWarning) noModelsWarning.style.display = 'none';
    }

    // 更新上传按钮状态
    if (uploadButton) {
        uploadButton.disabled = !canUpload;
        uploadButton.style.opacity = canUpload ? '' : '0.5';
        uploadButton.style.cursor = canUpload ? '' : 'not-allowed';
        uploadButton.title = canUpload ? '' : disableReason;
    }

    // 刷新预览
    if (effectiveModelType === 'vrm' && effectiveModelPath) {
        // 加载 VRM 3D 模型预览
        loadVrmPreview(effectiveModelPath, rawData);
    } else if (effectiveModelType === 'mmd' && effectiveModelPath) {
        // 加载 MMD 3D 模型预览
        loadMmdPreview(effectiveModelPath, rawData);
    } else if (live2d && live2d !== '') {
        // 清理可能残留的 3D 预览
        disposeWorkshopVrm();
        disposeWorkshopMmd();
        hideAll3DPreviews();
        // 恢复 Live2D 标题和控件
        const title = document.getElementById('model-preview-title');
        if (title) {
            title.textContent = 'Live2D';
            title.setAttribute('data-i18n', 'steam.live2dPreview');
        }
        const live2dControls = document.getElementById('live2d-preview-controls');
        if (live2dControls) live2dControls.style.display = '';
        const modelInfoForPreview = availableModels.find(model => model.name === live2d);
        loadLive2DModelByName(live2d, modelInfoForPreview);
    } else {
        // 角色未设置模型，清除现有预览并显示提示
        clearAllModelPreviews(true); // true 表示使用"未设置模型"的提示而非"请选择模型"
    }

    // 更新标签
    const tagsContainer = document.getElementById('character-card-tags-container');
    if (tagsContainer) {
        tagsContainer.innerHTML = '';
        if (card.tags && card.tags.length > 0) {
            card.tags.forEach(tag => {
                const tagElement = document.createElement('span');
                tagElement.className = 'tag';
                tagElement.textContent = tag;
                tagsContainer.appendChild(tagElement);
            });
        }
        requestAnimationFrame(updateCharacterCardTagScrollControls);
    }

    // 显示角色卡区域
    const characterCardLayout = document.getElementById('character-card-layout');
    if (characterCardLayout) {
        characterCardLayout.style.display = 'flex';
        requestAnimationFrame(() => {
            updateCharacterCardTagScrollControls();
        });

        // 仅在非面板上下文中滚动到角色卡区域
        if (!_catgirlPanelOpen) {
            characterCardLayout.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
    }

    // 获取并显示 Workshop 状态
    fetchWorkshopStatus(card.name);
}

// 存储当前角色卡的 Workshop 元数据
let currentWorkshopMeta = null;

// 获取 Workshop 状态
async function fetchWorkshopStatus(characterName) {
    const statusArea = document.getElementById('workshop-status-area');
    const uploadBtn = document.getElementById('upload-to-workshop-btn');
    const uploadBtnText = document.getElementById('upload-btn-text');

    // 重置状态
    statusArea.style.display = 'none';
    currentWorkshopMeta = null;
    if (uploadBtnText) {
        uploadBtnText.textContent = window.t ? window.t('steam.uploadToWorkshop') : '上传到创意工坊';
        uploadBtnText.setAttribute('data-i18n', 'steam.uploadToWorkshop');
    }

    try {
        const response = await fetch(`/api/steam/workshop/meta/${encodeURIComponent(characterName)}`);
        const data = await response.json();

        if (data.success && data.has_uploaded && data.meta) {
            currentWorkshopMeta = data.meta;

            // 显示状态区域
            statusArea.style.display = 'block';

            // 更新显示内容
            const uploadTime = document.getElementById('workshop-upload-time');
            const itemId = document.getElementById('workshop-item-id');

            if (uploadTime && data.meta.last_update) {
                const date = new Date(data.meta.last_update);
                uploadTime.textContent = date.toLocaleString();
            }

            if (itemId && data.meta.workshop_item_id) {
                itemId.textContent = data.meta.workshop_item_id;
            }

            // 修改按钮文字为"更新"
            if (uploadBtnText) {
                uploadBtnText.textContent = window.t ? window.t('steam.updateToWorkshop') : '更新到创意工坊';
                uploadBtnText.setAttribute('data-i18n', 'steam.updateToWorkshop');
            }

        }
    } catch (error) {
        console.error('获取 Workshop 状态失败:', error);
    }
}

// 显示 Workshop 快照
function showWorkshopSnapshot() {
    if (!currentWorkshopMeta || !currentWorkshopMeta.uploaded_snapshot) {
        showMessage(window.t ? window.t('steam.noSnapshotData') : '没有快照数据', 'warning');
        return;
    }

    const snapshot = currentWorkshopMeta.uploaded_snapshot;
    const modal = document.getElementById('workshopSnapshotModal');

    // 填充描述
    const descriptionEl = document.getElementById('snapshot-description');
    descriptionEl.textContent = snapshot.description || (window.t ? window.t('steam.noDescription') : '无描述');

    // 填充标签
    const tagsContainer = document.getElementById('snapshot-tags-container');
    tagsContainer.innerHTML = '';
    if (snapshot.tags && snapshot.tags.length > 0) {
        snapshot.tags.forEach(tag => {
            const tagEl = document.createElement('span');
            tagEl.className = 'tag';
            tagEl.style.cssText = `background-color: #e0e0e0; color: inherit; padding: 4px 8px; border-radius: 4px; font-size: 12px;`;
            tagEl.textContent = tag;
            tagsContainer.appendChild(tagEl);
        });
    } else {
        tagsContainer.textContent = window.t ? window.t('steam.noTags') : '无标签';
    }

    // 填充模型名称
    const modelEl = document.getElementById('snapshot-model');
    modelEl.textContent = snapshot.model_name || (window.t ? window.t('steam.unknownModel') : '未知模型');

    // 计算差异
    const diffArea = document.getElementById('snapshot-diff-area');
    const diffList = document.getElementById('snapshot-diff-list');
    diffList.innerHTML = '';

    let hasDiff = false;

    // 比较描述
    const currentDescription = document.getElementById('character-card-description')?.value.trim() || '';
    if (currentDescription !== (snapshot.description || '')) {
        const li = document.createElement('li');
        li.textContent = window.t ? window.t('steam.descriptionChanged') : '描述已修改';
        diffList.appendChild(li);
        hasDiff = true;
    }

    // 比较标签
    const currentTagElements = document.querySelectorAll('#character-card-tags-container .tag');
    const currentTags = Array.from(currentTagElements).map(el => el.textContent.replace('×', '').trim()).filter(t => t);
    const snapshotTags = snapshot.tags || [];
    if (JSON.stringify(currentTags.sort()) !== JSON.stringify(snapshotTags.sort())) {
        const li = document.createElement('li');
        li.textContent = window.t ? window.t('steam.tagsChanged') : '标签已修改';
        diffList.appendChild(li);
        hasDiff = true;
    }

    // 比较模型
    const currentModel = window.currentCharacterCardModel || '';
    if (currentModel && snapshot.model_name && currentModel !== snapshot.model_name) {
        const li = document.createElement('li');
        li.textContent = window.t ? window.t('steam.modelChanged') : '模型已修改';
        diffList.appendChild(li);
        hasDiff = true;
    }

    diffArea.style.display = hasDiff ? 'block' : 'none';

    // 显示模态框
    modal.style.display = 'flex';
}

// 关闭快照模态框
function closeWorkshopSnapshotModal(event) {
    const modal = document.getElementById('workshopSnapshotModal');
    if (!event || event.target === modal) {
        modal.style.display = 'none';
    }
}

// 加载角色卡
function loadCharacterCard() {
    // 这里将实现加载角色卡的逻辑
    showMessage(window.t ? window.t('steam.characterCardLoaded') : '角色卡已加载', 'info');
}

// 存储临时上传目录路径，供上传时使用
let currentUploadTempFolder = null;
// 标记是否已上传成功
let isUploadCompleted = false;

// 清理临时目录
function cleanupTempFolder(tempFolder, shouldDelete) {
    if (shouldDelete) {
        // 调用API删除临时目录
        fetch('/api/steam/workshop/cleanup-temp-folder', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                temp_folder: tempFolder
            })
        })
            .then(response => {
                if (!response.ok) {
                    return response.json().then(data => {
                        throw new Error(data.error || `HTTP错误，状态码: ${response.status}`);
                    });
                }
                return response.json();
            })
            .then(result => {
                if (result.success) {
                    showMessage(window.t ? window.t('steam.tempFolderDeleted') : '临时目录已删除', 'success');
                } else {
                    console.error('删除临时目录失败:', result.error);
                    showMessage(window.t ? window.t('steam.deleteTempDirectoryFailed', { error: result.error }) : `删除临时目录失败: ${result.error}`, 'error');
                }
                // 清除临时目录路径和上传状态
                currentUploadTempFolder = null;
                isUploadCompleted = false;
            })
            .catch(error => {
                console.error('删除临时目录失败:', error);
                showMessage(window.t ? window.t('steam.deleteTempDirectoryFailed', { error: error.message }) : `删除临时目录失败: ${error.message}`, 'error');
                // 即使删除失败，也清除临时目录路径和上传状态
                currentUploadTempFolder = null;
                isUploadCompleted = false;
            });
    } else {
        showMessage(window.t ? window.t('steam.tempFolderRetained') : '临时目录已保留', 'info');
        // 清除临时目录路径和上传状态
        currentUploadTempFolder = null;
        isUploadCompleted = false;
    }
}

async function handleUploadToWorkshop() {
    try {
        await ensureReservedFieldsLoaded();
        // 检查是否为默认模型
        if (isDefaultModel()) {
            showMessage(window.t ? window.t('steam.defaultModelCannotUpload') : '默认模型无法上传到创意工坊', 'error');
            return;
        }

        // 从已加载的角色卡列表中获取当前角色卡数据
        if (!currentCharacterCardId || !window.characterCards) {
            showMessage(window.t ? window.t('steam.noCharacterCardSelected') : '请先选择一个角色卡', 'error');
            return;
        }

        const currentCard = window.characterCards.find(card => card.id === currentCharacterCardId);
        if (!currentCard) {
            showMessage(window.t ? window.t('steam.characterCardNotFound') : '找不到当前角色卡数据', 'error');
            return;
        }

        // 从角色卡数据中提取信息
        // 现在角色使用的是 rawData 中的数据，只有 description 和 tag 需要从界面获取
        const rawData = currentCard.rawData || currentCard || {};
        // name 是 characters.json 中的唯一 key（如 "小天"、"小九"），直接从 currentCard.name 获取
        const name = currentCard.name;
        // description 可以从界面获取或从 rawData 中获取
        const description = document.getElementById('character-card-description').value.trim() || rawData['描述'] || rawData['description'] || '';
        const currentModelType = window.currentCharacterCardModelType || 'live2d';
        const currentModelPath = window.currentCharacterCardModelPath || '';
        let selectedModelName = window.currentCharacterCardModel || rawData['live2d'] || (rawData['model'] && rawData['model']['name']) || '';
        // VRM/MMD 模型使用路径而非 Live2D 模型名称
        if ((currentModelType === 'vrm' || currentModelType === 'mmd') && currentModelPath) {
            selectedModelName = currentModelPath;
        }
        const voiceId = rawData['voice_id'] || (rawData['voice'] && rawData['voice']['voice_id']) || '';

        // 验证必填字段 - 只验证 description
        const missingFields = [];
        if (!description) {
            missingFields.push(window.t ? window.t('steam.characterCardDescription') : '角色卡描述');
        }

        // 如果有未填写的必填字段，阻止上传并提示
        if (missingFields.length > 0) {
            const fieldsList = missingFields.join(window.t ? window.t('common.fieldSeparator') || '、' : '、');
            showMessage(window.t ? window.t('steam.requiredFieldsMissing', { fields: fieldsList }) : `请先填写以下必填字段：${fieldsList}`, 'error');
            return;
        }

        // 获取当前语言（需要在保存前获取）
        const currentLanguage = typeof i18next !== 'undefined' ? i18next.language : 'zh-CN';

        // 获取角色卡标签（需要在保存前获取）
        const characterCardTags = [];
        const tagElements = document.querySelectorAll('#character-card-tags-container .tag');
        if (tagElements && tagElements.length > 0) {
            tagElements.forEach(tagElement => {
                const tagText = tagElement.textContent.replace('×', '').trim();
                if (tagText) {
                    characterCardTags.push(tagText);
                }
            });
        }

        // 在上传前，先保存角色卡数据到文件
        // 构建完整的角色卡数据对象：直接使用 rawData 作为基础
        // 现在角色使用的是 rawData 中的数据，只覆盖 description 和 tags
        const fullCharaData = { ...rawData };

        // 字段顺序是展示属性，先在删保留字段前抓住它，删完再以顶层 _field_order 挂回。
        // 否则数字 key 的自定义字段名会被下载方按对象枚举顺序提前，复现本次修复要解决的乱序问题。
        const workshopFieldOrder = getStoredCharacterFieldOrder(rawData);

        // 重要：清理系统保留字段，防止恶意数据或循环引用被上传到工坊
        // 这些字段是下载时由系统添加的元数据，不应该出现在工坊角色卡中
        // description/tags 及其中文版本是工坊上传时自动生成的，不属于角色卡原始数据
        // live2d_item_id 是系统自动管理的，不应该上传
        const SYSTEM_RESERVED_FIELDS = getWorkshopReservedFields();
        for (const field of SYSTEM_RESERVED_FIELDS) {
            delete fullCharaData[field];
        }
        // 顺序元数据本身被当作系统保留字段删掉了，这里按显式顺序重新挂回，供下载方按创建顺序渲染。
        if (workshopFieldOrder.length) {
            attachCharacterFieldOrderPayload(fullCharaData, workshopFieldOrder);
        }

        // 重要：添加"档案名"字段，这是下载后解析为 characters.json key 的必需字段
        // name 是 characters.json 中的唯一 key（如 "小天"、"小九"）
        fullCharaData['档案名'] = name;

        // 只覆盖 description 和 tags（这些是从界面获取的）
        if (currentLanguage === 'zh-CN') {
            fullCharaData['描述'] = description;
            fullCharaData['关键词'] = characterCardTags;
        } else {
            fullCharaData['description'] = description;
            fullCharaData['tags'] = characterCardTags;
        }

        // 根据模型类型设置正确的字段
        if (currentModelType === 'vrm' || currentModelType === 'mmd') {
            // VRM/MMD 模型：清除可能残留的旧 live2d 字段，防止元数据冲突
            delete fullCharaData.live2d;
        } else {
            fullCharaData.live2d = selectedModelName;
        }

        // 使用从角色卡数据中提取的voice_id（如果有）
        if (voiceId) {
            fullCharaData['voice_id'] = voiceId;
        }

        // 设置默认模型（排除yui-origin）- 仅限 Live2D 模型类型
        if (currentModelType === 'live2d' && (!selectedModelName || isStaticDefaultLive2DModel(selectedModelName, rawData))) {
            const validModels = availableModels.filter(model =>
                model
                && model.name
                && !hasStaticModelFlag(model)
                && !hasStaticModelFlag(model.modelMetadata)
            );
            if (validModels.length > 0) {
                selectedModelName = validModels[0].name;
            } else {
                showMessage(window.t ? window.t('steam.noAvailableModelsError') : '没有可用的模型', 'error');
                return;
            }
            fullCharaData.live2d = selectedModelName;
        } else if ((currentModelType === 'vrm' || currentModelType === 'mmd') && !selectedModelName) {
            showMessage(window.t ? window.t('steam.noAvailableModelsError') : '没有可用的模型', 'error');
            return;
        }

        // 构建猫娘数据对象（用于上传，使用已保存的完整数据）
        const catgirlData = Object.assign({}, fullCharaData);

        // 构建角色卡文件名
        const charaFileName = `${name}.chara.json`;

        // 构建上传数据
        const uploadData = {
            fullCharaData: fullCharaData,
            catgirlData: catgirlData,
            name: name,
            selectedModelName: selectedModelName,
            modelType: currentModelType,
            charaFileName: charaFileName,
            characterCardTags: characterCardTags
        };

        // 直接进行上传（不再需要保存确认，因为使用的是 rawData 中的原始数据）
        await performUpload(uploadData);
    } catch (error) {
        console.error('handleUploadToWorkshop执行出错:', error);
        showMessage(window.t ? window.t('steam.prepareUploadError', { error: error.message }) : `上传准备出错: ${error.message}`, 'error');
    }
}

// 执行上传
async function performUpload(data) {
    // 显示准备上传状态
    showMessage(window.t ? window.t('steam.preparingUpload') : '正在准备上传...', 'info');

    try {
        // 步骤1: 调用API创建临时目录并复制文件
        // 保存上传数据的名称，供错误处理使用（避免回调中的参数覆盖）
        const uploadDataName = data.name;
        await fetch('/api/steam/workshop/prepare-upload', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                charaData: data.catgirlData,
                modelName: data.selectedModelName,
                modelType: data.modelType || 'live2d',
                fileName: data.charaFileName,
                character_card_name: data.name  // 传递角色卡名称，用于读取 .workshop_meta.json
            })
        })
            .then(response => {
                if (!response.ok) {
                    return response.json().then(data => {
                        // 如果是已上传的错误，显示modal提示
                        if (data.error && (data.error.includes('已上传') || data.error.includes('已存在') || data.error.includes('already been uploaded'))) {
                            // 使用i18n构建错误消息
                            let errorMessage;
                            if (data.workshop_item_id && window.t) {
                                // 从上传数据中获取角色卡名称
                                const cardName = uploadDataName || '未知角色卡';
                                errorMessage = window.t('steam.characterCardAlreadyUploadedWithId', {
                                    name: cardName,
                                    itemId: data.workshop_item_id
                                });
                            } else {
                                errorMessage = data.message || data.error;
                            }
                            // 显示错误消息
                            showMessage(errorMessage, 'error', 10000);
                            // 显示modal提示
                            openDuplicateUploadModal(errorMessage);
                            throw new Error(errorMessage);
                        }
                        throw new Error(data.error || `HTTP错误，状态码: ${response.status}`);
                    });
                }
                return response.json();
            })
            .then(async result => {
                if (result.success) {
                    // 不再显示"上传准备完成"消息，模态框弹出本身就表明准备工作已完成

                    // 保存临时目录路径
                    currentUploadTempFolder = result.temp_folder;
                    // 重置上传完成标志
                    isUploadCompleted = false;

                    // 步骤2: 填充上传表单并打开填写信息窗口
                    const itemTitle = document.getElementById('item-title');
                    const itemDescription = document.getElementById('item-description');
                    const contentFolder = document.getElementById('content-folder');
                    const previewImageInput = document.getElementById('preview-image');
                    const tagsContainer = document.getElementById('tags-container');


                    // 从data中获取名称和描述
                    const cardName = data.name || '';
                    const cardDescription = data.catgirlData?.['描述'] || data.catgirlData?.['description'] || '';

                    // Title 和 Description 现在是 div 元素，使用 textContent
                    if (itemTitle) itemTitle.textContent = cardName;
                    if (itemDescription) {
                        itemDescription.textContent = cardDescription;
                    }
                    // 使用临时目录路径（隐藏字段）
                    if (contentFolder) contentFolder.value = result.temp_folder;
                    // 若后端成功从角色卡卡面复制出预览图，则默认带入；没有卡面时不改动用户当前预览图输入。
                    if (previewImageInput && result.preview_image) {
                        previewImageInput.value = result.preview_image;
                        previewImageInput.classList.remove('error');
                    }
                    await resetWorkshopVoiceReferenceFields(cardName);

                    // 添加角色卡标签到上传标签（允许用户编辑）
                    if (tagsContainer) {
                        tagsContainer.innerHTML = '';

                        // 检查是否包含system_prompt（自定义模板）
                        const catgirlData = data.catgirlData || {};
                        const hasSystemPrompt = catgirlData['设定'] || catgirlData['system_prompt'] || catgirlData['prompt_setting'];

                        // 如果包含system_prompt，先添加锁定的"自定义模板"标签
                        if (hasSystemPrompt && String(hasSystemPrompt).trim() !== '') {
                            const customTemplateTagText = window.t ? window.t('steam.customTemplateTag') : '自定义模板';
                            addTag(customTemplateTagText, '', true); // locked = true
                        }

                        // 从角色卡标签容器中读取当前标签
                        const characterCardTagElements = document.querySelectorAll('#character-card-tags-container .tag');
                        const currentCharacterCardTags = Array.from(characterCardTagElements).map(tag =>
                            tag.textContent.replace('×', '').replace('🔒', '').trim()
                        ).filter(tag => tag);

                        // 如果有角色卡标签，使用它们；否则使用传入的标签
                        const tagsToAdd = currentCharacterCardTags.length > 0 ? currentCharacterCardTags : (data.characterCardTags || []);
                        tagsToAdd.forEach(tag => {
                            // 使用addTag函数，会自动添加删除按钮，允许用户编辑
                            addTag(tag);
                        });

                        // 确保标签输入框可编辑
                        const tagInput = document.getElementById('item-tags');
                        if (tagInput) {
                            tagInput.disabled = false;
                            tagInput.style.opacity = '';
                            tagInput.style.cursor = '';
                            tagInput.style.backgroundColor = '';
                            tagInput.placeholder = window.t ? window.t('steam.tagsPlaceholderInput') : '输入标签，按空格添加';
                        }
                    }

                    // 步骤3: 打开填写信息窗口（modal）
                    toggleUploadSection();
                } else {
                    showMessage(window.t ? window.t('steam.prepareUploadFailedMessage', { error: result.error || (window.t ? window.t('common.unknownError') : '未知错误') }) : `准备上传失败: ${result.error || '未知错误'}`, 'error');
                }
            })
            .catch(error => {
                console.error('准备上传失败:', error);
                showMessage(window.t ? window.t('steam.prepareUploadFailed', { error: error.message }) : `准备上传失败: ${error.message}`, 'error');
            });
    } catch (error) {
        console.error('performUpload执行出错:', error);
        showMessage(window.t ? window.t('steam.uploadExecutionError', { message: error.message }) : `上传执行出错: ${error.message}`, 'error');
    }
}

// 从模态框中编辑角色卡
function editCharacterCardModal() {
    if (currentCharacterCardId) {
        // 展开角色卡编辑区域
        toggleCharacterCardSection();

        // 调用编辑角色卡函数
        editCharacterCard(currentCharacterCardId);
    } else {
        showMessage(window.t ? window.t('steam.noCharacterCardSelectedForEdit') : '未选择要编辑的角色卡', 'error');
    }
}

// 扫描Live2D模型
async function scanModels(loadSequence) {
    showMessage(window.t ? window.t('steam.scanningModels') : '正在扫描模型...', 'info');

    try {
        // 并行获取 Live2D、VRM、MMD 模型列表
        const [live2dResponse, vrmResponse, mmdResponse] = await Promise.all([
            fetch('/api/live2d/models'),
            fetch('/api/model/vrm/models').catch(() => null),
            fetch('/api/model/mmd/models').catch(() => null)
        ]);

        // 处理 Live2D 模型
        if (!live2dResponse.ok) {
            throw new Error(`HTTP错误，状态码: ${live2dResponse.status}`);
        }
        const models = await live2dResponse.json();

        // 过滤掉来自static目录的模型（如默认/版权Live2D），只保留用户文档目录中的模型
        // 这是为了防止上传版权Live2D模型
        const uploadableModels = models.filter(model => model.source !== 'static');

        // 处理 VRM 模型（先收集到局部变量，避免旧轮扫描晚到时回滚新轮结果）
        let scannedAllVrmModels = null;
        let nextAvailableVrmModels = null;
        try {
            if (vrmResponse && vrmResponse.ok) {
                const vrmData = await vrmResponse.json();
                if (vrmData.success && vrmData.models) {
                    scannedAllVrmModels = vrmData.models;
                    nextAvailableVrmModels = vrmData.models.filter(m => m.location !== 'project');
                }
            }
        } catch (e) {
            console.warn('处理VRM模型列表失败:', e);
        }

        // 处理 MMD 模型
        let scannedAllMmdModels = null;
        let nextAvailableMmdModels = null;
        try {
            if (mmdResponse && mmdResponse.ok) {
                const mmdData = await mmdResponse.json();
                if (mmdData.success && mmdData.models) {
                    scannedAllMmdModels = mmdData.models;
                    nextAvailableMmdModels = mmdData.models.filter(m => m.location !== 'project');
                }
            }
        } catch (e) {
            console.warn('处理MMD模型列表失败:', e);
        }

        // 序列号校验：若已被新一轮 loadCharacterCards 触发，丢弃本轮结果，防止旧扫描回滚新数据
        if (loadSequence !== undefined && loadSequence !== characterCardLoadSequence) {
            return false;
        }

        // 提交到全局变量（用于角色卡加载，包括static目录的模型）
        // 注意：6 个全局必须无条件覆写到本轮结果，VRM/MMD 子扫描失败时落 [] 而非沿用旧值；
        // 否则 tab 切换路径里如果 VRM/MMD 端点偶发失败，会保留上一轮的 stale 列表造成假阳性
        window.allModels = models;
        availableModels = uploadableModels;
        window.allVrmModels = scannedAllVrmModels || [];
        availableVrmModels = nextAvailableVrmModels || [];
        window.allMmdModels = scannedAllMmdModels || [];
        availableMmdModels = nextAvailableMmdModels || [];

        // 触发模型扫描完成事件，通知其他组件刷新 UI（具有容错能力）
        try {
            window.dispatchEvent(new CustomEvent('modelsScanned', { detail: { models, uploadableModels } }));
        } catch (e) {
            console.warn('触发 modelsScanned 事件失败:', e);
        }

        // 如果存在 model_manager 分片中的更新函数，也尝试调用（具有容错能力）
        try {
            if (typeof window.updateLive2DModelDropdown === 'function') {
                window.updateLive2DModelDropdown();
            }
        } catch (e) {
            console.warn('更新 Live2D 模型下拉菜单失败:', e);
        }

        try {
            if (typeof window.updateLive2DModelSelectButtonText === 'function') {
                window.updateLive2DModelSelectButtonText();
            }
        } catch (e) {
            console.warn('更新 Live2D 模型选择按钮文字失败:', e);
        }

        return true;

    } catch (error) {
        console.error('扫描模型失败:', error);
        showMessage(window.t ? window.t('steam.modelScanError') : '扫描模型失败', 'error');
        return false;
    }
}

// 全局变量：当前选择的模型信息
let selectedModelInfo = null;

function setLive2DPreviewRefreshButtonState(visible, enabled = visible) {
    const refreshButton = document.getElementById('live2d-refresh-btn');
    if (!refreshButton) return;

    refreshButton.style.display = visible ? 'flex' : 'none';
    refreshButton.disabled = !enabled;
    refreshButton.style.cursor = enabled ? 'pointer' : 'default';
    refreshButton.setAttribute('aria-hidden', visible ? 'false' : 'true');
}

function fitLive2DPreviewModelToContainer(model) {
    if (!live2dPreviewManager || !live2dPreviewManager.pixi_app || !model) return;

    const renderer = live2dPreviewManager.pixi_app.renderer;
    const screenWidth = Number(renderer?.screen?.width) || 0;
    const screenHeight = Number(renderer?.screen?.height) || 0;
    if (screenWidth <= 0 || screenHeight <= 0) return;

    model.anchor.set(0.5, 0.5);
    if (!Number.isFinite(model.scale?.x) || model.scale.x <= 0 || !Number.isFinite(model.scale?.y) || model.scale.y <= 0) {
        model.scale.set(0.18);
    }

    model.x = screenWidth * 0.5;
    model.y = screenHeight * 0.5;

    // Live2DManager 在 addChild 之前会先调用 applyModelSettings。
    // 这时直接依赖 getBounds() 做精确 fitting 并不稳定，先做保守居中，
    // 等模型真正挂到 stage 上后再用 bounds 做二次校正。
    if (!model.parent || typeof model.getBounds !== 'function') return;

    let bounds = null;
    try {
        bounds = model.getBounds();
    } catch (error) {
        console.warn('[CharacterCard] 获取 Live2D 预览 bounds 失败:', error);
        return;
    }

    const initialWidth = Number(bounds?.width) || 0;
    const initialHeight = Number(bounds?.height) || 0;
    if (initialWidth <= 1 || initialHeight <= 1) return;

    const padding = 30;
    const availableWidth = Math.max(80, screenWidth - padding * 2);
    const availableHeight = Math.max(80, screenHeight - padding * 2);
    const scaleRatio = Math.min(availableWidth / initialWidth, availableHeight / initialHeight);

    if (Number.isFinite(scaleRatio) && scaleRatio > 0) {
        const nextScaleX = Math.max(0.02, Math.min(model.scale.x * scaleRatio, 2.5));
        const nextScaleY = Math.max(0.02, Math.min(model.scale.y * scaleRatio, 2.5));
        model.scale.set(nextScaleX, nextScaleY);
    }

    try {
        const fittedBounds = model.getBounds();
        const fittedWidth = Number(fittedBounds?.width) || 0;
        const fittedHeight = Number(fittedBounds?.height) || 0;
        if (fittedWidth > 1 && fittedHeight > 1) {
            const currentCenterX = (Number(fittedBounds.x) || 0) + fittedWidth * 0.5;
            const currentCenterY = (Number(fittedBounds.y) || 0) + fittedHeight * 0.5;
            model.x += (screenWidth * 0.5) - currentCenterX;
            model.y += (screenHeight * 0.5) - currentCenterY;
        }
    } catch (error) {
        console.warn('[CharacterCard] 校正 Live2D 预览位置失败:', error);
    }
}

// 初始化模型选择功能
// 音色相关函数（功能暂未实现）
// 加载音色列表
async function loadVoices() {
    // 显示扫描开始提示
    showMessage(window.t ? window.t('steam.scanningVoices') : '正在扫描音色...', 'info');

    try {
        const response = await fetch('/api/characters/voices');
        const data = await response.json();
        const voiceSelect = document.getElementById('voice-select');
        if (voiceSelect) {
            // 保存完整的音色数据到全局变量
            window.availableVoices = data.voices;

            // 音色数据已加载，用于后续显示音色名称
            const voiceCount = Object.keys(data.voices).length;

            // 显示扫描完成提示
            const successMessage = window.t ? window.t('steam.scanComplete', { count: voiceCount }) : `扫描完成，共找到 ${voiceCount} 个音色`;

            showToast(successMessage);
        }
    } catch (error) {
        console.error('加载音色列表失败:', error);
        showMessage(window.t ? window.t('steam.voiceScanError') : '扫描音色失败', 'error');
    }
}

// 扫描音色功能
function scanVoices() {
    loadVoices();
}

// 更新文件选择显示
function updateFileDisplay() {
    const fileInput = document.getElementById('audioFile');
    const fileNameDisplay = document.getElementById('fileNameDisplay');

    // 检查必要的DOM元素是否存在
    if (!fileInput || !fileNameDisplay) {
        return;
    }

    if (fileInput.files.length > 0) {
        fileNameDisplay.textContent = fileInput.files[0].name;
    } else {
        fileNameDisplay.textContent = window.t ? window.t('steam.voiceReferenceNoFileSelected') : '未选择文件';
    }
}

// 页面加载时获取 lanlan_name
(async function initLanlanName() {
    try {
        // 优先从 URL 获取 lanlan_name
        const urlParams = new URLSearchParams(window.location.search);
        let lanlanName = urlParams.get('lanlan_name') || "";

        // 如果 URL 中没有，从 API 获取
        if (!lanlanName) {
            const response = await fetch('/api/config/page_config');
            const data = await response.json();
            if (data.success) {
                lanlanName = data.lanlan_name || "";
            }
        }

        // 设置到隐藏字段
        if (!document.getElementById('lanlan_name')) {
            const hiddenInput = document.createElement('input');
            hiddenInput.type = 'hidden';
            hiddenInput.id = 'lanlan_name';
            hiddenInput.value = lanlanName;
            document.body.appendChild(hiddenInput);
        } else {
            document.getElementById('lanlan_name').value = lanlanName;
        }
    } catch (error) {
        console.error('获取 lanlan_name 失败:', error);
        if (!document.getElementById('lanlan_name')) {
            const hiddenInput = document.createElement('input');
            hiddenInput.type = 'hidden';
            hiddenInput.id = 'lanlan_name';
            hiddenInput.value = '';
            document.body.appendChild(hiddenInput);
        }
    }
})();

function setFormDisabled(disabled) {
    const audioFileInput = document.getElementById('audioFile');
    const prefixInput = document.getElementById('prefix');
    const registerBtn = document.querySelector('button[onclick="registerVoice()"]');

    if (audioFileInput) audioFileInput.disabled = disabled;
    if (prefixInput) prefixInput.disabled = disabled;
    if (registerBtn) registerBtn.disabled = disabled;
}

async function registerVoice() {
    const fileInput = document.getElementById('audioFile');
    const prefix = document.getElementById('prefix').value.trim();
    const resultDiv = document.getElementById('voice-register-result');

    resultDiv.innerHTML = '';
    resultDiv.className = 'result';

    if (!fileInput.files.length) {
        resultDiv.innerHTML = window.t ? window.t('voice.pleaseUploadFile') : '请选择音频文件';
        resultDiv.className = 'result error';
        resultDiv.style.color = 'red';
        return;
    }

    if (!prefix) {
        resultDiv.innerHTML = window.t ? window.t('voice.pleaseEnterPrefix') : '请填写自定义前缀';
        resultDiv.className = 'result error';
        resultDiv.style.color = 'red';
        return;
    }

    // 验证前缀格式
    const prefixRegex = /^[a-zA-Z0-9]{1,10}$/;
    if (!prefixRegex.test(prefix)) {
        resultDiv.innerHTML = window.t ? window.t('voice.prefixFormatError') : '前缀格式错误：不超过10个字符，只支持数字和英文字母';
        resultDiv.className = 'result error';
        resultDiv.style.color = 'red';
        return;
    }

    setFormDisabled(true);
    resultDiv.innerHTML = window.t ? window.t('voice.registering') : '正在注册声音，请稍后！';
    resultDiv.style.color = 'green';

    const formData = new FormData();
    formData.append('file', fileInput.files[0]);
    formData.append('prefix', prefix);
    const providerSelect = document.getElementById('voice-reference-provider-hint');
    await applyWorkshopVoiceProviderRestrictions(providerSelect);
    const providerValue = providerSelect && providerSelect.value ? providerSelect.value.trim() : '';
    formData.append('provider', providerValue || getFirstAvailableWorkshopVoiceProviderValue(providerSelect) || 'cosyvoice');

    fetch('/api/characters/voice_clone', {
        method: 'POST',
        body: formData
    })
        .then(res => res.json())
        .then(data => {
            if (data.voice_id) {
                if (data.reused) {
                    resultDiv.innerHTML = window.t ? window.t('voice.reusedExisting', { voiceId: data.voice_id }) : '已复用现有音色，跳过上传。voice_id: ' + data.voice_id;
                } else {
                    resultDiv.innerHTML = window.t ? window.t('voice.registerSuccess', { voiceId: data.voice_id }) : '注册成功！voice_id: ' + data.voice_id;
                }
                resultDiv.style.color = 'green';

                // 自动更新voice_id到后端
                const lanlanName = document.getElementById('lanlan_name').value;
                if (lanlanName) {
                    const voiceSwitchOpId = createVoiceConfigSwitchOpId(lanlanName);
                    notifyVoiceConfigSwitching(lanlanName, true, voiceSwitchOpId);
                    fetch(`/api/characters/catgirl/voice_id/${encodeURIComponent(lanlanName)}`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ voice_id: data.voice_id })
                    }).then(resp => resp.json()).then(res => {
                        if (!res.success) {
                            const errorMsg = res.error || (window.t ? window.t('common.unknownError') : '未知错误');
                            resultDiv.innerHTML += '<br><span class="error" style="color: red;">' + (window.t ? window.t('voice.voiceIdSaveFailed', { error: errorMsg }) : 'voice_id自动保存失败: ' + errorMsg) + '</span>';
                        } else {
                            resultDiv.innerHTML += '<br>' + (window.t ? window.t('voice.voiceIdSaved') : 'voice_id已自动保存到角色');
                            // 如果session被结束，页面会自动刷新
                            if (res.session_restarted) {
                                resultDiv.innerHTML += '<br><span style="color: blue;">' + (window.t ? window.t('voice.pageWillRefresh') : '当前页面即将自动刷新以应用新语音') + '</span>';
                                setTimeout(() => {
                                    location.reload();
                                }, 2000);
                            } else {
                                resultDiv.innerHTML += '<br><span style="color: blue;">' + (window.t ? window.t('voice.voiceWillTakeEffect') : '新语音将在下次对话时生效') + '</span>';
                            }
                        }
                    }).catch(e => {
                        resultDiv.innerHTML += '<br><span class="error" style="color: red;">' + (window.t ? window.t('voice.voiceIdSaveRequestError') : 'voice_id自动保存请求出错') + '</span>';
                    }).finally(() => {
                        notifyVoiceConfigSwitching(lanlanName, false, voiceSwitchOpId);
                    });
                }

                // 重新扫描音色以更新列表
                setTimeout(() => {
                    loadVoices();
                }, 1000);
            } else {
                const errorMsg = data.error || (window.t ? window.t('common.unknownError') : '未知错误');
                resultDiv.innerHTML = window.t ? window.t('voice.registerFailed', { error: errorMsg }) : '注册失败：' + errorMsg;
                resultDiv.className = 'result error';
                resultDiv.style.color = 'red';
            }
            setFormDisabled(false);
        })
        .catch(err => {
            const errorMsg = err?.message || err?.toString() || (window.t ? window.t('common.unknownError') : '未知错误');
            resultDiv.textContent = window.t ? window.t('voice.requestError', { error: errorMsg }) : '请求出错：' + errorMsg;
            resultDiv.className = 'result error';
            resultDiv.style.color = 'red';
            setFormDisabled(false);
        });
}

// 页面加载时初始化文件选择显示
window.addEventListener('load', () => {
    // 监听文件选择变化
    const audioFileInput = document.getElementById('audioFile');
    if (audioFileInput) {
        audioFileInput.addEventListener('change', updateFileDisplay);
    }

    // 如果 i18next 已经初始化完成，立即更新
    if (window.i18n && window.i18n.isInitialized) {
        updateFileDisplay();
    } else {
        // 延迟更新，等待 i18next 初始化
        setTimeout(updateFileDisplay, 500);
    }
});

// ====================== VRM/MMD 3D 模型预览 ======================

// 工坊预览专用的 VRM/MMD 管理器实例
