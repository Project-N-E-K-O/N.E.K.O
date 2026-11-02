// Part responsibility: character-card data loading, cache synchronization, card metadata, import, and export.

let globalCharacterCards = [];

// 全局变量：当前打开的角色卡ID（用于模态框操作）
let currentCharacterCardId = null;

const CHARACTER_CARD_MODEL_SCAN_RENDER_BUDGET_MS = 2500;
let characterCardLoadSequence = 0;

function getCharacterCardDescriptionFromData(data) {
    if (!data || typeof data !== 'object') {
        return window.t ? window.t('steam.noDescription') : '暂无描述';
    }
    if (data['description']) return data['description'];
    if (data['描述']) return data['描述'];
    if (data['角色卡描述']) return data['角色卡描述'];
    return window.t ? window.t('steam.noDescription') : '暂无描述';
}

function getCharacterCardTagsFromData(data) {
    if (!data || typeof data !== 'object') {
        return [];
    }
    return Array.isArray(data['关键词']) ? data['关键词'] : [];
}

function buildCharacterCardEntry(name, data, id) {
    return {
        id: id,
        name: name,
        description: getCharacterCardDescriptionFromData(data),
        tags: getCharacterCardTagsFromData(data),
        rawData: data || {},
        originalName: name
    };
}

function findCharacterCardIndexByName(name) {
    const cards = Array.isArray(window.characterCards) ? window.characterCards : [];
    return cards.findIndex(card => String(card?.originalName || card?.name || '') === String(name));
}

function getNextCharacterCardId() {
    const cards = Array.isArray(window.characterCards) ? window.characterCards : [];
    let maxId = 0;
    cards.forEach(card => {
        const numericId = Number(card && card.id);
        if (Number.isFinite(numericId)) {
            maxId = Math.max(maxId, numericId);
        }
    });
    return maxId + 1;
}

function buildLocalCatgirlRawData(catgirlName, submittedData, fieldOrder) {
    const cards = Array.isArray(window.characterCards) ? window.characterCards : [];
    const existingIdx = findCharacterCardIndexByName(catgirlName);
    const previousRawData = existingIdx >= 0 && cards[existingIdx]?.rawData && typeof cards[existingIdx].rawData === 'object'
        ? cards[existingIdx].rawData
        : {};
    const allReservedFields = ['档案名', ...getWorkshopHiddenFields()];
    const nextRawData = {};

    // 通用编辑接口会保留系统字段，但会用本次提交的普通字段整体替换旧普通字段。
    Object.keys(previousRawData).forEach(key => {
        if (allReservedFields.includes(key)) {
            nextRawData[key] = previousRawData[key];
        }
    });
    Object.entries(submittedData || {}).forEach(([key, value]) => {
        if (!key || key === '档案名' || allReservedFields.includes(key)) {
            return;
        }
        if (value !== null && value !== undefined && String(value).trim() !== '') {
            nextRawData[key] = value;
        }
    });
    if (Array.isArray(fieldOrder)) {
        setLocalRawDataFieldOrder(nextRawData, fieldOrder);
    }
    return nextRawData;
}

function mergeFreshCatgirlRawDataWithLocal(freshRawData, localRawData) {
    const allReservedFields = ['档案名', ...getWorkshopHiddenFields()];
    const merged = {};

    // 本轮刚保存的普通字段优先；重新拉取的数据只用于补回模型、音色等保留字段。
    Object.entries(localRawData || {}).forEach(([key, value]) => {
        if (!allReservedFields.includes(key)) {
            merged[key] = value;
        }
    });
    Object.entries(localRawData || {}).forEach(([key, value]) => {
        if (allReservedFields.includes(key)) {
            merged[key] = value;
        }
    });
    Object.entries(freshRawData || {}).forEach(([key, value]) => {
        if (allReservedFields.includes(key)) {
            merged[key] = value;
        }
    });
    return merged;
}

function applyLocalVoiceIdToRawData(rawData, voiceId) {
    if (!rawData || typeof rawData !== 'object') return rawData;
    const normalizedVoiceId = (voiceId == null ? '' : String(voiceId)).trim();
    rawData.voice_id = normalizedVoiceId;
    if (rawData.voice && typeof rawData.voice === 'object') {
        rawData.voice.voice_id = normalizedVoiceId;
    }
    return rawData;
}

function syncCharacterCardCache(catgirlName, rawData, options = {}) {
    if (!catgirlName) return;
    if (!Array.isArray(window.characterCards)) {
        window.characterCards = [];
    }

    const existingIdx = findCharacterCardIndexByName(catgirlName);
    const existingCard = existingIdx >= 0 ? window.characterCards[existingIdx] : null;
    const cardId = existingCard?.id ?? getNextCharacterCardId();
    const updatedCard = buildCharacterCardEntry(catgirlName, rawData || {}, cardId);

    if (existingIdx >= 0) {
        window.characterCards[existingIdx] = updatedCard;
    } else {
        window.characterCards.push(updatedCard);
    }
    globalCharacterCards = window.characterCards || [];

    refreshCharacterCardSelectOptions();
    if (options.render !== false) {
        renderCharaCardsView();
    }
}

function waitForCharacterCardModelScanBudget(scanPromise) {
    const eventual = Promise.resolve(scanPromise)
        .then(scanCompleted => scanCompleted === true)
        .catch(error => {
            console.warn('角色卡模型扫描失败，先渲染角色列表:', error);
            return false;
        });

    return new Promise(resolve => {
        let settled = false;
        const finish = inTime => {
            if (settled) return;
            settled = true;
            resolve({ inTime, eventual });
        };

        window.setTimeout(() => finish(false), CHARACTER_CARD_MODEL_SCAN_RENDER_BUDGET_MS);
        eventual.then(scanCompleted => finish(scanCompleted === true));
    });
}

async function collectCharacterSettingsCardsFromModels(idCounter, loadSequence) {
    let nextId = idCounter;
    const newCards = [];
    for (const model of availableModels) {
        // 每个模型外层 fetch 前先校验序列号；旧轮被新一轮 loadCharacterCards 抢占后立刻早退，
        // 避免在大目录下继续打 model_files / *.chara.json 的废请求拖慢最新一轮 I/O
        if (loadSequence !== undefined && loadSequence !== characterCardLoadSequence) {
            return { cards: newCards, nextId };
        }
        try {
            // 调用API获取模型文件列表
            const response = await fetch(`/api/live2d/model_files/${model.name}`);
            if (response.ok) {
                const data = await response.json();
                if (data.success) {
                    // 检查是否有*.chara.json格式的角色卡文件
                    const jsonFiles = data.json_files || [];
                    const characterSettingsFiles = jsonFiles.filter(file =>
                        file.endsWith('.chara.json')
                    );

                    // 如果找到character_settings文件，解析并添加到角色卡列表
                    for (const file of characterSettingsFiles) {
                        if (loadSequence !== undefined && loadSequence !== characterCardLoadSequence) {
                            return { cards: newCards, nextId };
                        }
                        try {
                            // 获取完整的文件内容
                            // 构建正确的文件URL - 从模型配置文件路径推断
                            const modelJsonUrl = model.path;
                            const modelRootUrl = modelJsonUrl.substring(0, modelJsonUrl.lastIndexOf('/') + 1);
                            const fileUrl = modelRootUrl + file;

                            const fileResponse = await fetch(fileUrl);
                            if (fileResponse.ok) {
                                const jsonData = await fileResponse.json();
                                // 检查是否包含"type": "character_settings"
                                if (jsonData && jsonData.type === 'character_settings') {
                                    newCards.push({
                                        id: nextId++,
                                        name: jsonData.name || `${model.name}_settings`,
                                        description: jsonData.description || (window.t ? window.t('steam.characterSettingsFile') : '角色设置文件'),
                                        tags: jsonData.tags || [],
                                        rawData: jsonData  // 保存原始数据，方便详情页使用
                                    });
                                }
                            }
                        } catch (fileError) {
                            console.error(`解析文件${file}失败:`, fileError);
                        }
                    }
                }
            }
        } catch (error) {
            console.error(`获取模型${model.name}文件列表失败:`, error);
        }
    }
    return { cards: newCards, nextId };
}

function mergeCharacterSettingsCardsFromModels(loadSequence, discovered) {
    const cards = discovered?.cards || [];
    if (loadSequence !== characterCardLoadSequence || cards.length === 0) {
        return;
    }
    window.characterCards = (window.characterCards || []).concat(cards);
    globalCharacterCards = window.characterCards || [];
    refreshCharacterCardSelectOptions();
    // 主列表视图也要同步刷新，否则晚到的旧格式兼容卡得等下次整页刷新才会出现
    renderCharaCardsView();
}

function refreshExpandedCardAfterScan(loadSequence) {
    if (loadSequence !== characterCardLoadSequence) return;
    if (!currentCharacterCardId) return;
    const card = (window.characterCards || []).find(c => String(c.id) === String(currentCharacterCardId));
    if (card) {
        // availableModels 在扫描完成后才落地，重跑 expand 让上传/预览按钮基于最新模型列表渲染
        expandCharacterCardSection(card);
    }
}

function refreshCharacterCardSelectOptions() {
    const characterCardSelect = document.getElementById('character-card-select');

    if (!characterCardSelect) {
        return;
    }

    // 保留当前选中值，重建后再恢复，避免异步补卡时把用户已选项清掉
    const previousValue = characterCardSelect.value;

    // 清空现有选项（保留第一个默认选项）
    while (characterCardSelect.options.length > 1) {
        characterCardSelect.remove(1);
    }

    if (window.characterCards && window.characterCards.length > 0) {
        // 填充下拉选项
        window.characterCards.forEach(card => {
            const option = document.createElement('option');
            option.value = card.id;
            option.text = card.name;
            characterCardSelect.add(option);
        });

        // 添加change事件监听器
        characterCardSelect.onchange = function () {
            const selectedId = this.value;
            if (selectedId) {
                // 注意：select.value 返回字符串，card.id 可能是数字或字符串，使用 == 进行宽松比较
                const selectedCard = window.characterCards.find(c => String(c.id) === selectedId);
                if (selectedCard) {
                    expandCharacterCardSection(selectedCard);
                }
            }
        };

        if (previousValue && Array.from(characterCardSelect.options).some(option => option.value === previousValue)) {
            characterCardSelect.value = previousValue;
        }
    }
}

// 加载角色卡列表
async function loadCharacterCards() {
    const loadSequence = ++characterCardLoadSequence;

    // 新一轮加载先失效上一轮的模型扫描缓存：scanModels 现在 fire-and-forget，
    // 若新一轮扫描卡住/失败，本应基于过期清单判断上传可用性会出现假阳性。
    // 清空后扫描完成前 UI 会显示"无可用模型"，是诚实的 loading 信号；
    // refreshExpandedCardAfterScan 在扫描完成后会按当前展开卡重渲染恢复正常状态。
    availableModels = [];
    availableVrmModels = [];
    availableMmdModels = [];
    window.allModels = [];
    window.allVrmModels = [];
    window.allMmdModels = [];

    // 显示加载状态
    const characterCardsList = document.getElementById('character-cards-list');
    if (characterCardsList) {
        characterCardsList.innerHTML = `
            <div class="loading-state">
                <p data-i18n="steam.loadingCharacterCards">正在加载角色卡...</p>
            </div>
        `;
    }

    // 获取角色数据
    const characterData = await loadCharacterData();
    if (!characterData) return;

    // 模型扫描可能受 Linux 新存储根、创意工坊目录或 Steam 状态影响变慢。
    // 角色列表不应被模型扫描阻塞；扫描完成后再用于预览/上传等增强能力。
    const modelScanPromise = scanModels(loadSequence);

    // 转换角色数据为角色卡格式（定义为全局变量，供其他函数使用）
    window.characterCards = [];
    let idCounter = 1;

    // 只处理猫娘数据，忽略其他角色类型（包括主人）
    const catgirls = characterData['猫娘'] || {};
    for (const [name, data] of Object.entries(catgirls)) {
        window.characterCards.push(buildCharacterCardEntry(name, data, idCounter++));
    }

    // 从character_cards文件夹加载角色卡
    try {
        const response = await fetch('/api/characters/character-card/list');
        if (response.ok) {
            const data = await response.json();
            if (data.success) {
                for (const card of data.character_cards) {
                    window.characterCards.push({
                        id: idCounter++,
                        name: card.name,
                        description: card.description,
                        tags: card.tags,
                        rawData: card.rawData
                    });
                }
            }
        }
    } catch (error) {
        console.error('从character_cards文件夹加载角色卡失败:', error);
    }

    // 扫描模型文件夹中的 character_settings JSON 文件仅用于旧格式兼容，不能阻塞角色管理主列表。
    const characterSettingsStartId = idCounter;

    // 渲染角色卡列表（改为下拉选单）
    refreshCharacterCardSelectOptions();

    // 将角色卡列表保存到全局变量（已使用window.characterCards，这里保持兼容）
    globalCharacterCards = window.characterCards || [];

    // 获取当前猫娘
    try {
        const currentResp = await fetch('/api/characters/current_catgirl');
        const currentData = await currentResp.json();
        window._workshopCurrentCatgirl = currentData.current_catgirl || '';
    } catch (e) {
        window._workshopCurrentCatgirl = '';
    }

    // 预取已设置卡面的猫娘名单（避免逐个发起 404 请求）
    await loadCardFaceNames();
    // 预取卡面元数据（作者/创建时间/来源）
    await loadCardMetas();

    // 渲染卡片/列表视图
    renderCharaCardsView();

    // 同步加载我的档案和已隐藏猫娘列表
    loadMasterProfile();
    renderHiddenCatgirls();

    waitForCharacterCardModelScanBudget(modelScanPromise)
        .then(scanBudget => {
            const appendAfterScan = () => collectCharacterSettingsCardsFromModels(characterSettingsStartId, loadSequence)
                .then(discovered => mergeCharacterSettingsCardsFromModels(loadSequence, discovered));

            scanBudget.eventual.then(scanCompleted => {
                if (scanCompleted) {
                    // 扫描成功后回补当前展开角色卡的上传/预览状态，避免用户先点开卡片时停留在旧/空 availableModels
                    refreshExpandedCardAfterScan(loadSequence);
                }
                if (scanBudget.inTime || !scanCompleted) {
                    return null;
                }
                return appendAfterScan();
            }).catch(error => {
                console.warn('角色卡旧格式兼容延迟扫描失败，已保留主列表:', error);
            });

            if (!scanBudget.inTime) {
                return null;
            }
            return appendAfterScan();
        })
        .catch(error => {
            console.warn('角色卡旧格式兼容扫描失败，已保留主列表:', error);
        });
}

// ===== 角色卡 卡片/列表 视图 =====

// 已设置卡面的猫娘名集合（避免无卡面的 404 控制台噪声）
window._cardFaceNames = window._cardFaceNames || new Set();
const CHARACTER_MANAGER_CARD_MAKER_WINDOW_NAME = 'neko_card_maker';
async function loadCardFaceNames() {
    try {
        const resp = await fetch('/api/characters/card-faces');
        if (!resp.ok) return;
        const data = await resp.json();
        if (data && data.success && Array.isArray(data.names)) {
            window._cardFaceNames = new Set(data.names);
        }
    } catch (e) {
        // 忽略，退化为不加载头像
    }
}

function openManagedPopup(url, windowName, features) {
    window._openWindows = window._openWindows || {};
    const existingWindow = window._openWindows[windowName];
    if (existingWindow && !existingWindow.closed) {
        const replacementName = `${windowName}_${Date.now()}_${Math.random().toString(36).slice(2)}`;
        const replacementWindow = window.open(url, replacementName, features);
        if (replacementWindow) {
            try { existingWindow.close(); } catch (_) {}
            try {
                // 随机名只用于绕开旧窗口复用；新窗口接管后恢复固定名称，方便其他上下文继续定位。
                replacementWindow.name = windowName;
            } catch (error) {
                console.warn('更新弹窗名称失败:', error);
            }
            window._openWindows[windowName] = replacementWindow;
            try { replacementWindow.focus(); } catch (_) {}
            return replacementWindow;
        }

        try {
            // 新窗口被拦截时才复用旧窗口，仍然保证内容跟随最后一次打开。
            existingWindow.location.href = new URL(url, window.location.origin).toString();
        } catch (error) {
            console.warn('更新弹窗地址失败:', error);
        }
        if (typeof window.requestOpenedWindowRestore === 'function') {
            window.requestOpenedWindowRestore(existingWindow);
        }
        existingWindow.focus();
        return existingWindow;
    }
    delete window._openWindows[windowName];

    const popup = window.open(url, windowName, features);
    if (popup) {
        window._openWindows[windowName] = popup;
        try { popup.focus(); } catch (_) {}
    }
    return popup;
}
window.openManagedPopup = openManagedPopup;

function refreshOpenCardMetaBlock(name) {
    const panelWrapper = document.getElementById('catgirl-panel-wrapper');
    if (!panelWrapper || !name) return;
    const formName = panelWrapper.querySelector('form [name="档案名"]')?.value;
    if (formName !== name) return;
    const metaBlock = panelWrapper.querySelector('#card-meta-block');
    if (metaBlock && typeof renderCardMetaBlock === 'function') {
        renderCardMetaBlock(metaBlock, name, false);
    }
}

function updateCardMetaAfterFaceChange(name, timestamp) {
    if (!name) return;
    window._cardMetas = window._cardMetas || {};
    const existing = window._cardMetas[name] || {};
    const updatedAt = new Date(timestamp || Date.now()).toISOString();
    window._cardMetas[name] = {
        author: existing.author || '',
        origin: 'self',
        created_at: existing.created_at || updatedAt,
        updated_at: updatedAt
    };
    refreshOpenCardMetaBlock(name);
}

function applyCardFaceUpdated(name, timestamp) {
    if (!name) return;
    const ts = timestamp || Date.now();
    const newSrc = `/api/characters/catgirl/${encodeURIComponent(name)}/card-face?t=${ts}`;
    if (window._cardFaceNames) window._cardFaceNames.add(name);
    updateCardMetaAfterFaceChange(name, ts);

    const panelWrapper = document.getElementById('catgirl-panel-wrapper');
    if (panelWrapper) {
        const formName = panelWrapper.querySelector('form [name="档案名"]')?.value;
        if (formName === name) {
            const cardImage = panelWrapper.querySelector('.catgirl-panel-card-image');
            const placeholder = cardImage?.querySelector('.card-avatar-placeholder');
            if (cardImage) {
                let panelImg = cardImage.querySelector('.card-face-img');
                if (!panelImg) {
                    panelImg = document.createElement('img');
                    panelImg.className = 'card-face-img';
                    panelImg.alt = '角色卡面';
                    cardImage.insertBefore(panelImg, placeholder || cardImage.firstChild);
                }
                panelImg.onload = () => {
                    if (placeholder) placeholder.style.display = 'none';
                };
                panelImg.onerror = () => {
                    if (placeholder) placeholder.style.display = '';
                };
                panelImg.src = newSrc;
            }
        }
    }

    document.querySelectorAll('.chara-card-item').forEach(cardItem => {
        const cardName = cardItem.querySelector('.card-name');
        if (!cardName || cardName.textContent !== name) return;
        const gridAvatar = cardItem.querySelector('.card-avatar');
        if (!gridAvatar) return;
        let gridImg = gridAvatar.querySelector('.card-face-img');
        const gridPlaceholder = gridAvatar.querySelector('.card-avatar-placeholder');
        if (!gridImg) {
            gridImg = document.createElement('img');
            gridImg.className = 'card-face-img';
            gridImg.alt = name;
            if (gridPlaceholder) {
                gridAvatar.insertBefore(gridImg, gridPlaceholder);
            } else {
                gridAvatar.appendChild(gridImg);
            }
        }
        gridImg.onload = () => {
            if (gridPlaceholder) gridPlaceholder.style.display = 'none';
        };
        gridImg.onerror = () => {
            if (gridPlaceholder) gridPlaceholder.style.display = '';
        };
        gridImg.src = newSrc;
    });
}

function handleExternalCardFaceUpdated(data) {
    if (!data || data.type !== 'card-face-updated') return;
    applyCardFaceUpdated(data.name, data.timestamp);
}

(function initCardFaceUpdateEvents() {
    window.addEventListener('message', event => {
        if (event.origin !== window.location.origin) return;
        handleExternalCardFaceUpdated(event.data);
    });
    if (typeof BroadcastChannel === 'function') {
        try {
            const channel = new BroadcastChannel('neko-card-face-events');
            channel.onmessage = event => {
                if (event.origin !== window.location.origin) return;
                handleExternalCardFaceUpdated(event.data);
            };
        } catch (_) {}
    }
    window.addEventListener('storage', event => {
        if (event.key !== 'neko_card_face_event' || !event.newValue) return;
        try {
            handleExternalCardFaceUpdated(JSON.parse(event.newValue));
        } catch (_) {}
    });
})();

async function openModelManagerForCharacterForm(form, fallbackName) {
    let catgirlName = getProfileNameFromCharacterForm(form, fallbackName);
    if (!catgirlName) {
        await showProfileNameRequiredDialog();
        return;
    }
    const nameInput = form?.querySelector?.('[name="档案名"]');
    const shouldCreateCharacter = form && form._isNew && (!form._autoCreated || form._autoCreatedName !== catgirlName);
    if (shouldCreateCharacter) {
        if (form._autoCreated && form._autoCreatedName !== catgirlName) {
            form._autoCreatedDetachedName = form._autoCreatedName;
            form._autoCreated = false;
            form._autoCreatedName = '';
        }
        if (!(await ensureValidCharacterProfileName(catgirlName, nameInput))) {
            return;
        }
    } else if (!(await ensureSafeExistingCharacterPathName(catgirlName, nameInput))) {
        return;
    }

    if (shouldCreateCharacter) {
        try {
            const tmpResp = await fetch('/api/characters/catgirl', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ '档案名': catgirlName })
            });
            if (tmpResp.ok) {
                const tmpResult = await tmpResp.json().catch(() => ({}));
                const createdName = tmpResult.character_name || catgirlName;
                const nameInput = form.querySelector?.('[name="档案名"]');
                if (nameInput) nameInput.value = createdName;
                catgirlName = createdName;
                form._autoCreated = true;
                form._autoCreatedName = createdName;
            } else {
                const errData = await tmpResp.json().catch(() => ({}));
                showMessage((window.t ? window.t('character.tempSaveFailed', { error: errData.error || '' }) : '临时保存失败: ' + (errData.error || '')), 'error');
                return;
            }
        } catch (e) {
            showMessage((window.t ? window.t('character.tempSaveFailed', { error: e.message }) : '临时保存失败: ' + e.message), 'error');
            return;
        }
    }

    const url = '/model_manager?lanlan_name=' + encodeURIComponent(catgirlName);
    if (!window._openSettingsWindows) window._openSettingsWindows = {};
    const existingWindow = window._openSettingsWindows[url];
    if (existingWindow && !existingWindow.closed) {
        if (form && form._autoCreatedDetachedName) {
            await rollbackAutoCreatedCatgirl(form, form._autoCreatedDetachedName);
        }
        if (form && form._autoCreated) form._autoCreatedDependentPopup = existingWindow;
        if (typeof window.requestOpenedWindowRestoreIfMinimized === 'function') {
            window.requestOpenedWindowRestoreIfMinimized(existingWindow);
        }
        return;
    }
    delete window._openSettingsWindows[url];

    const modelManagerFeatures =
        'toolbar=no,location=no,status=no,menubar=no,scrollbars=yes,resizable=yes,width=' + screen.availWidth + ',height=' + screen.availHeight + ',top=0,left=0';
    let reusedModelManagerWindow = false;
    const popup = typeof window.openOrFocusWindow === 'function'
        ? window.openOrFocusWindow(url, 'neko_model_manager_singleton', modelManagerFeatures, {
            onReuse: () => { reusedModelManagerWindow = true; }
        })
        : window.open(url, 'neko_model_manager_singleton', modelManagerFeatures);
    if (!popup) {
        if (typeof showAlert === 'function') await showAlert(window.t ? window.t('character.allowPopups') : '请允许弹窗！');
        // 弹窗被拦截：回滚本次及此前重命名遗留的 detached 临时角色，避免用户直接刷新/关页时残留空记录
        if (form && (form._autoCreated || form._autoCreatedDetachedName)) {
            await rollbackAutoCreatedCatgirl(form);
        }
        return;
    }
    if (reusedModelManagerWindow) {
        if (form && (form._autoCreated || form._autoCreatedDetachedName)) {
            await rollbackAutoCreatedCatgirl(form);
        }
        return;
    }

    window._openSettingsWindows[url] = popup;
    if (form && form._autoCreated) form._autoCreatedDependentPopup = popup;
    const timer = setInterval(() => {
        if (!popup.closed) {
            if (form && popup._modelManagerHasSaved) form._autoCreatedDependentPopupSaved = true;
            return;
        }
        clearInterval(timer);
        if (window._openSettingsWindows[url] === popup) delete window._openSettingsWindows[url];
        if (form && popup._modelManagerHasSaved) form._autoCreatedDependentPopupSaved = true;
        if (form && form._autoCreatedDependentPopup === popup) form._autoCreatedDependentPopup = null;
        if (form && form._autoCreatedRollbackWhenDependentCloses && !form._autoCreatedDependentPopupSaved) {
            rollbackAutoCreatedCatgirl(form).catch(e => console.warn('[角色面板] 延迟回滚临时角色失败:', e));
        }
        if (typeof loadCharacterCards === 'function') {
            loadCharacterCards().catch(e => console.warn('刷新角色列表失败:', e));
        }
    }, 500);
}

function getProfileNameFromCharacterForm(form, fallbackName) {
    return String(form?.querySelector?.('[name="档案名"]')?.value || fallbackName || '').trim();
}

const CHARACTER_PROFILE_RESERVED_ROUTE_NAMES = new Set([
    'l2d',
    'model_manager',
    'live2d_parameter_editor',
    'live2d_emotion_manager',
    'vrm_emotion_manager',
    'mmd_emotion_manager',
    'voice_clone',
    'voice_identity',
    'api_key',
    'character_card_manager',
    'cloudsave_manager',
    'memory_browser',
    'cookies_login',
    'chat',
    'subtitle',
    'agenthud',
    'toast',
    'card_maker',
    'soccer_demo',
    'badminton_demo',
    'air_basketball',
    'jukebox',
    'static',
    'user_live2d',
    'user_live2d_local',
    'user_vrm',
    'user_mmd',
    'user_mods',
    'workshop',
    'api',
    'ws',
    'health',
]);
const CHARACTER_PROFILE_RESERVED_DEVICE_RE = /^(con|prn|aux|nul|clock\$|com[1-9]|lpt[1-9])$/i;
const CHARACTER_PROFILE_ALLOWED_PUNCTUATION = new Set([' ', '_', '-', '(', ')', '（', '）', '·', '・', "'", '’']);
const CHARACTER_PROFILE_NAME_MAX_UNITS = 60;

function countCharacterProfileNameUnits(name) {
    return Array.from(String(name || '')).reduce((total, ch) => total + (ch.codePointAt(0) <= 0x7F ? 1 : 2), 0);
}

function getCharacterProfileNameError(name) {
    const value = String(name || '').trim();
    if (!value) return window.t ? window.t('character.profileNameRequired') : '档案名为必填项';
    if (value.includes('/') || value.includes('\\')) return window.t ? window.t('character.profileNameContainsSlash') : '档案名不能包含路径分隔符(/或\\)';
    if (value.includes('..')) return window.t ? window.t('character.profileNameDotSequence') : '档案名不能包含连续点号(..)';
    if (value === '.' || value.endsWith('.')) return window.t ? window.t('character.profileNameUnsafeDot') : '档案名不能仅由点号组成或以点号结尾';
    if (value.includes('.')) return window.t ? window.t('character.profileNameContainsDot') : '档案名不能包含点号(.)';
    if (CHARACTER_PROFILE_RESERVED_DEVICE_RE.test(value.split('.', 1)[0])) return window.t ? window.t('character.profileNameReservedDevice') : '档案名不能使用 Windows 保留设备名';
    if (CHARACTER_PROFILE_RESERVED_ROUTE_NAMES.has(value)) return window.t ? window.t('character.profileNameReservedRoute') : '此名称是系统保留的路由名称，不能用作档案名';
    for (const ch of value) {
        if (/[\u0000-\u001F\u007F]/.test(ch)) return window.t ? window.t('character.profileNameInvalidChars') : '档案名只能包含文字、数字、空格、下划线、连字符、括号、间隔号(·/・)和撇号';
        if (/[\p{L}\p{N}]/u.test(ch) || CHARACTER_PROFILE_ALLOWED_PUNCTUATION.has(ch)) continue;
        return window.t ? window.t('character.profileNameInvalidChars') : '档案名只能包含文字、数字、空格、下划线、连字符、括号、间隔号(·/・)和撇号';
    }
    if (countCharacterProfileNameUnits(value) > CHARACTER_PROFILE_NAME_MAX_UNITS) return window.t ? window.t('character.profileNameTooLong') : '档案名过长';
    return '';
}

async function showCharacterProfileNameInvalidDialog(message) {
    const text = message || (window.t ? window.t('character.profileNameInvalid') : '档案名无效');
    showMessage(text, 'warning', 6000);
    if (typeof showAlertDialog === 'function') {
        await showAlertDialog(text, { type: 'warning' });
    }
}

async function ensureValidCharacterProfileName(name, input) {
    const error = getCharacterProfileNameError(name);
    if (!error) return true;
    if (input && typeof input.focus === 'function') input.focus();
    await showCharacterProfileNameInvalidDialog(error);
    return false;
}

function getExistingCharacterPathNameError(name) {
    const value = String(name || '').trim();
    if (!isUnsafeCharacterPathSegment(value)) return '';
    if (!value) return window.t ? window.t('character.profileNameRequired') : '档案名为必填项';
    if (value.includes('/') || value.includes('\\')) return window.t ? window.t('character.profileNameContainsSlash') : '档案名不能包含路径分隔符(/或\\)';
    if (value.includes('..')) return window.t ? window.t('character.profileNameDotSequence') : '档案名不能包含连续点号(..)';
    if (value === '.' || value.endsWith('.')) return window.t ? window.t('character.profileNameUnsafeDot') : '档案名不能仅由点号组成或以点号结尾';
    return window.t ? window.t('character.profileNameInvalid') : '档案名无效';
}

async function ensureSafeExistingCharacterPathName(name, input) {
    const error = getExistingCharacterPathNameError(name);
    if (!error) return true;
    if (input && typeof input.focus === 'function') input.focus();
    await showCharacterProfileNameInvalidDialog(error);
    return false;
}

async function showProfileNameRequiredDialog(key = 'character.fillProfileNameFirst', fallback = '请先填写猫娘档案名，然后再设置模型') {
    const message = window.t ? window.t(key) : fallback;
    if (typeof showAlertDialog === 'function') {
        await showAlertDialog(message, { type: 'warning' });
        return;
    }
    showMessage(message, 'warning');
}

// 卡面元数据缓存 { name: { author, origin, created_at, updated_at } }
window._cardMetas = window._cardMetas || {};
async function loadCardMetas() {
    try {
        const resp = await fetch('/api/characters/card-metas');
        if (!resp.ok) return;
        const data = await resp.json();
        if (data && data.success && data.metas && typeof data.metas === 'object') {
            window._cardMetas = data.metas;
        }
    } catch (e) {
        // 忽略，退化为面板内单独请求
    }
}

// 格式化 ISO 时间为本地化短字符串
function _formatCardMetaTime(iso) {
    if (!iso) return '';
    try {
        const d = new Date(iso);
        if (isNaN(d.getTime())) return iso;
        const y = d.getFullYear();
        const m = String(d.getMonth() + 1).padStart(2, '0');
        const day = String(d.getDate()).padStart(2, '0');
        const hh = String(d.getHours()).padStart(2, '0');
        const mm = String(d.getMinutes()).padStart(2, '0');
        return `${y}-${m}-${day} ${hh}:${mm}`;
    } catch (e) { return iso; }
}

// 渲染卡面信息块（作者、创建时间、来源）
function renderCardMetaBlock(container, name, isNew, rawData) {
    container.innerHTML = '';
    if (isNew || !name) {
        const placeholder = document.createElement('div');
        placeholder.className = 'card-meta-placeholder';
        placeholder.dataset.i18n = 'character.cardNotCreated';
        placeholder.textContent = window.t ? window.t('character.cardNotCreated') : '尚未创建角色卡';
        container.appendChild(placeholder);
        return;
    }

    // 优先用缓存，否则惰性请求
    let meta = window._cardMetas && window._cardMetas[name];
    const draw = (m) => {
        container.innerHTML = '';
        const origin = (m && m.origin) || 'self';
        const author = (m && m.author) || '';
        const createdAt = (m && m.created_at) || '';

        const title = document.createElement('div');
        title.className = 'card-meta-title';
        title.dataset.i18n = 'character.cardMeta';
        title.textContent = window.t ? window.t('character.cardMeta') : '卡面信息';
        container.appendChild(title);

        // 来源徽章
        const originRow = document.createElement('div');
        originRow.className = 'card-meta-row card-meta-origin';
        const originLabel = document.createElement('span');
        originLabel.className = 'card-meta-label';
        originLabel.dataset.i18n = 'character.cardOriginLabel';
        originLabel.textContent = window.t ? window.t('character.cardOriginLabel') : '来源';
        const originValue = document.createElement('span');
        originValue.className = 'card-meta-origin-badge origin-' + origin;
        const originKey = origin === 'imported' ? 'character.cardOriginImported'
            : origin === 'steam' ? 'character.cardOriginSteam'
                : origin === 'molili' ? 'character.cardOriginMolili'
                    : 'character.cardOriginSelf';
        const originText = window.t ? window.t(originKey)
            : (origin === 'imported' ? '导入' : origin === 'steam' ? '创意工坊'
                : origin === 'molili' ? 'Molili 导入' : '本地');
        originValue.dataset.i18n = originKey;
        originValue.textContent = originText;
        originRow.appendChild(originLabel);
        originRow.appendChild(originValue);
        container.appendChild(originRow);

        // 作者（可编辑：origin=self / molili——Molili 导入同样是本地持有的卡）
        const authorRow = document.createElement('div');
        authorRow.className = 'card-meta-row card-meta-author';
        const authorLabel = document.createElement('span');
        authorLabel.className = 'card-meta-label';
        authorLabel.dataset.i18n = 'character.cardAuthor';
        authorLabel.textContent = window.t ? window.t('character.cardAuthor') : '作者';
        authorRow.appendChild(authorLabel);

        if (origin === 'self' || origin === 'molili') {
            const authorInput = document.createElement('input');
            authorInput.type = 'text';
            authorInput.className = 'card-meta-author-input';
            authorInput.value = author;
            authorInput.maxLength = 64;
            authorInput.dataset.i18nPlaceholder = 'character.cardAuthorPlaceholder';
            authorInput.placeholder = window.t ? window.t('character.cardAuthorPlaceholder') : '请输入作者';
            let saving = false;
            const saveAuthor = async () => {
                if (saving) return;
                const newVal = (authorInput.value || '').trim();
                if (newVal === (author || '').trim()) return;
                saving = true;
                try {
                    const resp = await fetch('/api/characters/catgirl/' + encodeURIComponent(name) + '/card-meta', {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ author: newVal })
                    });
                    if (!resp.ok) throw new Error('HTTP ' + resp.status);
                    const data = await resp.json();
                    if (window._cardMetas) window._cardMetas[name] = data.meta || { ...m, author: newVal };
                    showMessage(window.t ? window.t('character.cardAuthorUpdated') : '作者已更新', 'success');
                } catch (e) {
                    const errorMessage = e.message || String(e);
                    showMessage(window.t ? window.t('character.cardAuthorUpdateFailed', { error: errorMessage }) : '更新作者失败: ' + errorMessage, 'error');
                    authorInput.value = author;
                } finally { saving = false; }
            };
            authorInput.addEventListener('blur', saveAuthor);
            authorInput.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') { e.preventDefault(); authorInput.blur(); }
            });
            authorRow.appendChild(authorInput);
        } else {
            const authorValue = document.createElement('span');
            authorValue.className = 'card-meta-value card-meta-readonly';
            authorValue.textContent = author || '-';
            authorValue.title = window.t ? window.t('character.cardAuthorReadonly') : '导入/工坊角色卡的作者不可修改';
            authorValue.dataset.i18nTitle = 'character.cardAuthorReadonly';
            authorRow.appendChild(authorValue);
        }
        container.appendChild(authorRow);

        // 创建时间
        if (createdAt) {
            const timeRow = document.createElement('div');
            timeRow.className = 'card-meta-row card-meta-time';
            const timeLabel = document.createElement('span');
            timeLabel.className = 'card-meta-label';
            timeLabel.dataset.i18n = 'character.cardCreatedAt';
            timeLabel.textContent = window.t ? window.t('character.cardCreatedAt') : '创建时间';
            const timeValue = document.createElement('span');
            timeValue.className = 'card-meta-value';
            timeValue.textContent = _formatCardMetaTime(createdAt);
            timeRow.appendChild(timeLabel);
            timeRow.appendChild(timeValue);
            container.appendChild(timeRow);
        }
    };

    if (meta) {
        draw(meta);
    } else {
        // 占位
        const loading = document.createElement('div');
        loading.className = 'card-meta-placeholder';
        loading.textContent = '...';
        container.appendChild(loading);
        // 异步拉取
        fetch('/api/characters/catgirl/' + encodeURIComponent(name) + '/card-meta')
            .then(r => r.ok ? r.json() : null)
            .then(data => {
                if (data && data.meta) {
                    if (window._cardMetas) window._cardMetas[name] = data.meta;
                    draw(data.meta);
                } else {
                    draw(null);
                }
            })
            .catch(() => draw(null));
    }
}

// 从 PNG neKo 辅助块中提取 ZIP 数据
function _extractNekoChunk(uint8Array) {
    if (uint8Array.length < 8) return null;
    if (uint8Array[0] !== 0x89 || uint8Array[1] !== 0x50 || uint8Array[2] !== 0x4E ||
        uint8Array[3] !== 0x47 || uint8Array[4] !== 0x0D || uint8Array[5] !== 0x0A ||
        uint8Array[6] !== 0x1A || uint8Array[7] !== 0x0A) {
        return null;
    }
    const view = new DataView(uint8Array.buffer, uint8Array.byteOffset, uint8Array.byteLength);
    let offset = 8;
    while (offset + 12 <= uint8Array.length) {
        const chunkLen = view.getUint32(offset, false);
        if (chunkLen > 0x7FFFFFFF) return null;
        const chunkEnd = offset + 12 + chunkLen;
        if (chunkEnd > uint8Array.length) return null;
        const t0 = uint8Array[offset + 4];
        const t1 = uint8Array[offset + 5];
        const t2 = uint8Array[offset + 6];
        const t3 = uint8Array[offset + 7];
        if (t0 === 0x6E && t1 === 0x65 && t2 === 0x4B && t3 === 0x6F) {
            const dataStart = offset + 8;
            return uint8Array.slice(dataStart, dataStart + chunkLen);
        }
        if (t0 === 0x49 && t1 === 0x45 && t2 === 0x4E && t3 === 0x44) break;
        offset = chunkEnd;
    }
    return null;
}

// 重入标志：一次 Molili 导入从 preview 到 commit 要好几分钟，期间再选一份
// zip 会并行第二套 preview/确认框/建卡/commit（建出 name(1)、双份 LLM 抽取，
// commit 重入还会对同一角色重复导入）。
let _moliliImportInFlight = false;

async function handleImportCharacterCard(event) {
    const file = event.target.files[0];
    if (!file) return;
    event.target.value = '';

    // .zip 走 Molili 停服归档导入（后端按 manifest 嗅探确认，误选普通 zip 会报
    // "不是 Molili 归档"），PNG / .nekocfg 维持原角色卡流程。
    if (file.name.toLowerCase().endsWith('.zip')) {
        if (_moliliImportInFlight) {
            showMessage(_moliliT('character.moliliImportBusy',
                '已有 Molili 导入在进行中，请等它完成后再选归档'), 'import-error');
            return;
        }
        _moliliImportInFlight = true;
        try {
            await handleMoliliArchiveImport(file);
        } finally {
            _moliliImportInFlight = false;
        }
        return;
    }

    const isNekoFile = file.name.endsWith('.nekocfg');
    const isPngFile = file.type.startsWith('image/') || file.name.endsWith('.png');
    if (!isNekoFile && !isPngFile) {
        showMessage(window.t ? window.t('character.importInvalidFile') : '请选择有效的PNG图片文件、.nekocfg设定文件或 Molili 归档 zip', 'warning');
        return;
    }

    const loadingText = window.t ? window.t('character.importingCard') : '正在导入角色卡...';
    // 导入和解包大角色卡可能超过普通 toast 的展示时长。提示随整个导入流程常驻，
    // 但仍是非模态浮层，不阻塞用户操作页面中的其他功能。
    const importNotice = showMessage(loadingText, 'importing', 0);
    // 先让浏览器绘制提示，再开始可能占用主线程的旧版 PNG 标记扫描。
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));

    try {
        const arrayBuffer = await file.arrayBuffer();
        let fileData;
        if (isNekoFile) {
            fileData = new Uint8Array(arrayBuffer);
        } else {
            const uint8Array = new Uint8Array(arrayBuffer);
            fileData = _extractNekoChunk(uint8Array);
            if (!fileData) {
                // 回退：查找旧版 NEKOCHARA 标记
                const marker = new TextEncoder().encode('NEKOCHARA\x00');
                let markerIndex = -1;
                for (let i = uint8Array.length - marker.length; i >= 0; i--) {
                    let found = true;
                    for (let j = 0; j < marker.length; j++) {
                        if (uint8Array[i + j] !== marker[j]) { found = false; break; }
                    }
                    if (found) { markerIndex = i; break; }
                }
                if (markerIndex === -1 || markerIndex < 8) {
                    throw new Error(window.t ? window.t('character.importNoMarker') : '该图片不是有效的角色卡文件');
                }
                const zipSizeBytes = uint8Array.slice(markerIndex - 8, markerIndex);
                const zipSize = new DataView(zipSizeBytes.buffer).getUint32(0, true);
                if (zipSize <= 0 || zipSize > uint8Array.length) {
                    throw new Error(window.t ? window.t('character.importNoMarker') : '该图片不是有效的角色卡文件');
                }
                const zipStart = markerIndex - 8 - zipSize;
                if (zipStart < 0 || zipStart + zipSize > markerIndex - 8) {
                    throw new Error(window.t ? window.t('character.importNoMarker') : '该图片不是有效的角色卡文件');
                }
                fileData = uint8Array.slice(zipStart, markerIndex - 8);
            }
        }

        const formData = new FormData();
        const blob = new Blob([fileData], { type: isNekoFile ? 'application/octet-stream' : 'application/zip' });
        formData.append('zip_file', blob, isNekoFile ? file.name : 'character_data.zip');
        // 对于 PNG 载体，额外上传原始图片作为卡面回退（老角色卡兼容）
        if (isPngFile) {
            const pngBlob = new Blob([new Uint8Array(arrayBuffer)], { type: 'image/png' });
            formData.append('card_image', pngBlob, file.name || 'card.png');
        }

        const response = await fetch('/api/characters/import-card', { method: 'POST', body: formData });
        if (!response.ok) {
            const errorData = await response.json().catch(() => ({ error: '导入失败' }));
            throw new Error(errorData.error || `HTTP ${response.status}`);
        }
        // 刷新角色卡列表（含 sidecar / 卡面 / 视图重新渲染）
        if (typeof loadCharacterCards === 'function') {
            await loadCharacterCards();
        } else if (typeof loadCharacterData === 'function') {
            await loadCharacterData();
        }

        importNotice.dismiss();
    } catch (error) {
        importNotice.dismiss();
        console.error('导入角色卡失败:', error);
        const errorText = window.t ? window.t('character.importCardFailed', { error: error.message }) : `导入角色卡失败: ${error.message}`;
        showMessage(errorText, 'import-error');
    }
}

// 导入类型选择：同一个「导入角色卡」按钮承载两类完全不同的归档（猫娘计划
// 角色卡 PNG/.nekocfg 与 Molili 停服数据 zip），文件选择器里只看后缀不直观，
// 先让用户显式选类型，再打开对应 accept 的文件选择器。返回 'card'/'molili'/null。
function _showImportTypeDialog() {
    return new Promise(resolve => {
        // 焦点管理：打开时聚焦第一个选项、Tab 困在弹窗内循环、关闭还原焦点——
        // 否则焦点留在遮罩后的导入按钮上，Tab 会穿透到背景控件，Enter 还能
        // 再开一层选择框（Codex/Greptile P2）。
        const previouslyFocused = (document.activeElement
            && typeof document.activeElement.focus === 'function')
            ? document.activeElement : null;
        const overlay = document.createElement('div');
        overlay.className = 'ccm-modal-overlay';
        overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.45);z-index:10002;display:flex;align-items:center;justify-content:center;animation:ccmFadeIn 0.18s ease';

        const dialog = document.createElement('div');
        dialog.style.cssText = 'background:#fff;border-radius:14px;padding:22px 26px 18px;width:440px;max-width:92vw;box-shadow:0 14px 40px rgba(0,0,0,0.25);animation:ccmSlideUp 0.22s ease';
        // 读屏器模态语义（CodeRabbit）
        dialog.setAttribute('role', 'dialog');
        dialog.setAttribute('aria-modal', 'true');
        dialog.setAttribute('aria-labelledby', 'import-type-dialog-title');

        const close = (value) => {
            if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
            document.removeEventListener('keydown', escHandler);
            if (previouslyFocused) previouslyFocused.focus();
            resolve(value);
        };
        const escHandler = (e) => { if (e.key === 'Escape') close(null); };
        document.addEventListener('keydown', escHandler);

        const title = document.createElement('div');
        title.id = 'import-type-dialog-title';
        title.style.cssText = 'font-size:16px;font-weight:700;color:#222;margin-bottom:14px';
        title.textContent = _moliliT('character.importTypeTitle', '选择导入类型');
        dialog.appendChild(title);

        const optionButtons = [];
        [
            {
                key: 'card',
                title: _moliliT('character.importTypeCard', '导入猫娘计划角色卡'),
                desc: _moliliT('character.importTypeCardDesc', 'PNG 或 .nekocfg 角色卡文件'),
            },
            {
                key: 'molili',
                title: _moliliT('character.importTypeMolili', '导入 Molili 数据'),
                desc: _moliliT('character.importTypeMoliliDesc', 'Molili 停服数据归档（.zip）'),
            },
        ].forEach(option => {
            const item = document.createElement('button');
            item.type = 'button';
            item.style.cssText = 'display:block;width:100%;text-align:left;background:#f6f9fc;border:1px solid #d8e2ec;border-radius:10px;padding:12px 16px;margin-bottom:10px;cursor:pointer;font:inherit;transition:border-color 0.15s ease, background 0.15s ease';
            item.addEventListener('mouseenter', () => { item.style.borderColor = '#5bb0e6'; item.style.background = '#eef6fd'; });
            item.addEventListener('mouseleave', () => { item.style.borderColor = '#d8e2ec'; item.style.background = '#f6f9fc'; });
            const optionTitle = document.createElement('div');
            optionTitle.style.cssText = 'font-size:14px;font-weight:600;color:#222';
            optionTitle.textContent = option.title;
            const optionDesc = document.createElement('div');
            optionDesc.style.cssText = 'font-size:12px;color:#7a8794;margin-top:3px';
            optionDesc.textContent = option.desc;
            item.appendChild(optionTitle);
            item.appendChild(optionDesc);
            item.addEventListener('click', () => close(option.key));
            dialog.appendChild(item);
            optionButtons.push(item);
        });

        overlay.appendChild(dialog);
        overlay.addEventListener('click', (e) => { if (e.target === overlay) close(null); });
        _ensureManagerModalKeyframes();
        document.body.appendChild(overlay);
        if (optionButtons[0]) optionButtons[0].focus();
        dialog.addEventListener('keydown', (e) => {
            if (e.key !== 'Tab' || !optionButtons.length) return;
            const first = optionButtons[0];
            const last = optionButtons[optionButtons.length - 1];
            if (e.shiftKey && document.activeElement === first) {
                e.preventDefault();
                last.focus();
            } else if (!e.shiftKey && document.activeElement === last) {
                e.preventDefault();
                first.focus();
            }
        });
    });
}

// 绑定导入按钮（页面加载后）
function _setupImportCardButton() {
    const btn = document.getElementById('chara-import-btn');
    const input = document.getElementById('chara-import-input');
    if (btn && input && !btn._bound) {
        btn._bound = true;
        btn.addEventListener('click', async () => {
            const choice = await _showImportTypeDialog();
            if (!choice) return;
            // 每次按所选类型设置 accept：card 模式不收 .zip（防误选），Molili
            // 模式只收 .zip。类型选错时 change 处理器的分流仍会兜底。
            input.accept = choice === 'molili' ? '.zip' : 'image/png,.nekocfg';
            input.click();
        });
        input.addEventListener('change', handleImportCharacterCard);
    }
}
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _setupImportCardButton);
} else {
    _setupImportCardButton();
}

// ===== Molili 停服归档导入 =====
// 同一个"导入角色卡"入口：选了 .zip 按 Molili 用户数据归档处理。流程：
// preview（后端解析 + 注入扫描，不落盘）→ 确认框（新建角色或选现有角色）→
// 建卡（设定 = role_prompt，走通用字段，随卡可导出）→ 外部记忆导入 commit。
// 建卡与记忆导入是两个独立接口，失败可分别重试：persona 融合按指纹幂等，
// 已落盘的 daily fact 由服务端去重，重复 commit 不会翻倍。

function _moliliT(key, fallback, params) {
    return window.t ? window.t(key, params) : fallback;
}

// 与后端 EXTERNAL_IMPORT_MAX_ARCHIVE_BYTES 一致：超限的 zip 不读进页面（base64
// 后约 4/3 倍，再加 JSON 序列化副本，大文件会把渲染进程撑爆）。
const MOLILI_MAX_ARCHIVE_BYTES = 64 * 1024 * 1024;

// 同一份归档导入超时/部分失败后，重试应导回同一个角色，而不是再建一个
// 「名字(1)」或回到新建模式。按 snapshot_time + 识别名记住目标角色（sessionStorage，
// 读写失败就只是不预选）。
const MOLILI_RETRY_TARGETS_KEY = 'neko_molili_retry_targets';

function _moliliRetryKey(preview) {
    const card = (preview && preview.character_card) || {};
    return `${(preview && preview.snapshot_time) || ''}|${card.suggested_name || ''}`;
}

function _moliliReadRetryTargets() {
    try {
        const raw = window.sessionStorage.getItem(MOLILI_RETRY_TARGETS_KEY);
        const parsed = raw ? JSON.parse(raw) : {};
        return parsed && typeof parsed === 'object' ? parsed : {};
    } catch (_) {
        return {};
    }
}

function _moliliRememberRetryTarget(preview, characterName) {
    try {
        const targets = _moliliReadRetryTargets();
        targets[_moliliRetryKey(preview)] = characterName;
        window.sessionStorage.setItem(MOLILI_RETRY_TARGETS_KEY, JSON.stringify(targets));
    } catch (_) { /* 存不下只是重试时不预选 */ }
}

function _moliliForgetRetryTarget(preview) {
    try {
        const targets = _moliliReadRetryTargets();
        delete targets[_moliliRetryKey(preview)];
        window.sessionStorage.setItem(MOLILI_RETRY_TARGETS_KEY, JSON.stringify(targets));
    } catch (_) { /* ignore */ }
}

// 收尾刷新角色列表：超时/部分失败时服务端往往已建卡并继续落盘（实测形态：
// 浏览器报超时，服务端随后跑完）——不刷新的话新角色要重开页面才可见。
function _moliliPartialDayNotes(partial) {
    const notes = [];
    const failedDays = Number(partial && partial.failed_days) || 0;
    const oversizedDays = Number(partial && partial.oversized_days) || 0;
    if (failedDays > 0) {
        notes.push(_moliliT('character.moliliFailedDaysNote',
            `其中 ${failedDays} 天抽取失败，再导入一次会重试。`, { count: failedDays }));
    }
    if (oversizedDays > 0) {
        notes.push(_moliliT('character.moliliOversizedDaysNote',
            `另有 ${oversizedDays} 天单独超过单次导入上限，需要先拆分这几天的内容。`, { count: oversizedDays }));
    }
    return notes.length ? ' ' + notes.join(' ') : '';
}

function _moliliRefreshCardList() {
    if (typeof loadCharacterCards === 'function') {
        loadCharacterCards().catch(e => console.warn('刷新角色列表失败:', e));
    }
}

// 记忆系统的角色名上限（记忆服务器 validate_lanlan_name 的 max_length=50）比
// 角色卡（60 单位）严：超长的名字能建卡，却导不了记忆、每次重试都失败。在
// 建卡 / 改卡之前就拦下，不留下导了一半的角色。
const MOLILI_MEMORY_NAME_MAX_LENGTH = 50;

// 与后端 _available_character_name 同规则：重名时追加 (1)、(2)…，记忆导入用的
// 是加完后缀的最终名字，长度检查必须按它算。
function _moliliAvailableName(existingNames, requestedName) {
    const taken = new Set(existingNames || []);
    if (!taken.has(requestedName)) return requestedName;
    let counter = 1;
    while (taken.has(`${requestedName}(${counter})`)) counter += 1;
    return `${requestedName}(${counter})`;
}

function _moliliNameFitsMemory(name) {
    if (String(name || '').length <= MOLILI_MEMORY_NAME_MAX_LENGTH) return true;
    showMessage(_moliliT('character.moliliNameTooLong',
        `角色名超过 ${MOLILI_MEMORY_NAME_MAX_LENGTH} 个字符，记忆系统无法导入，请换一个短一些的名字`,
        { max: MOLILI_MEMORY_NAME_MAX_LENGTH }), 'warning');
    return false;
}

// 与 memory_browser.js 的外部导入一致：记忆落盘后通知主窗口丢弃旧上下文，
// 否则正在聊的角色要手动重开会话才看得到新导入的人格/事实。
function _moliliBroadcastMemoryEdited(characterName) {
    if (!characterName) return;
    if (typeof BroadcastChannel !== 'undefined') {
        let channel = null;
        try {
            channel = new BroadcastChannel('neko_page_channel');
            channel.postMessage({ action: 'memory_edited', catgirl_name: characterName });
            return;
        } catch (error) {
            console.warn('[Molili] memory_edited broadcast failed:', error);
        } finally {
            if (channel) channel.close();
        }
    }
    if (window.parent && window.parent !== window) {
        window.parent.postMessage({ type: 'memory_edited', catgirl_name: characterName }, window.location.origin);
    }
}

// 与解析器 _EMOJI_RE 同范围：emoji/装饰符号清理（旧版整块设定比对用）
function _moliliStripEmoji(text) {
    return String(text || '')
        .replace(/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}\u{1F1E6}-\u{1F1FF}\u{FE0F}\u{200D}\u{20E3}]/gu, '')
        .replace(/ {2}/g, ' ')
        .trim();
}

function _moliliFileToBase64(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => {
            const result = String(reader.result || '');
            const commaIndex = result.indexOf(',');
            resolve(commaIndex >= 0 ? result.slice(commaIndex + 1) : result);
        };
        reader.onerror = () => reject(new Error('FileReader failed'));
        reader.readAsDataURL(file);
    });
}

async function handleMoliliArchiveImport(file) {
    if (file.size > MOLILI_MAX_ARCHIVE_BYTES) {
        const maxMb = Math.round(MOLILI_MAX_ARCHIVE_BYTES / 1024 / 1024);
        showMessage(_moliliT('character.moliliArchiveTooLarge',
            `归档超过 ${maxMb} MB，无法导入`, { max: maxMb }), 'import-error');
        return;
    }
    const parseNotice = showMessage(_moliliT('character.moliliParsing', '正在解析 Molili 归档...'), 'importing', 0);
    let archiveB64 = '';
    let preview;
    try {
        archiveB64 = await _moliliFileToBase64(file);
        // character_name 只是请求校验占位，preview 不写任何数据；真实角色在确认框里定
        const response = await fetch('/api/memory/external_import/preview', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ character_name: 'Molili', archive_b64: archiveB64, source_format: 'molili' }),
        });
        preview = await response.json().catch(() => ({}));
        if (!response.ok || !preview.success) {
            throw new Error(preview.error || `HTTP ${response.status}`);
        }
    } catch (error) {
        parseNotice.dismiss();
        console.error('Molili 归档解析失败:', error);
        showMessage(_moliliT('character.moliliParseFailed', `Molili 归档解析失败: ${error.message}`, { error: error.message }), 'import-error');
        return;
    }
    parseNotice.dismiss();

    const choice = await _showMoliliImportDialog(preview, archiveB64);
    if (!choice) return;

    // 主人身份处理：Molili 归档里的身份字段（昵称等）可能与全局主人卡不同。
    // 默认"角色私有"——身份写进该角色的设定提示词，全局主人档案一字不动；
    // 只有明确选择"更新全局"才动主人卡。身份必须在建卡/导记忆之前落地，
    // 这样人格卡同步与融合守卫看到的都是最终值。
    const masterProfile = preview.master_profile || {};
    const hasMasterFields = Object.keys(masterProfile).length > 0;
    // role_prompt 按 ## 标题拆成带 tag 的独立字段：各字段经 persona 同步单独
    // 成条、卡面分区显示。解析器无标题可拆时回退单一"设定"字段（旧行为）。
    // 无原型对象：标题叫 __proto__ 时普通对象赋值会改原型而不是存成字段，
    // 这一节会被静默丢掉；后面的对象展开照常把它复制成普通字段。
    const moliliSectionFields = Object.create(null);
    ((preview.character_card && preview.character_card.sections) || []).forEach(s => {
        if (s.tag && s.text) moliliSectionFields[s.tag] = s.text;
    });
    if (!Object.keys(moliliSectionFields).length) {
        const fallbackSetting = (preview.character_card && preview.character_card.system_prompt) || '';
        if (fallbackSetting) moliliSectionFields['设定'] = fallbackSetting;
    }

    try {
        let characterName = choice.name;
        if (choice.masterOverwrite && hasMasterFields) {
            const listResponse = await fetch('/api/characters?language=zh-CN');
            if (!listResponse.ok) {
                throw new Error(`HTTP ${listResponse.status}`);
            }
            const list = await listResponse.json();
            const masterFields = (list && list['主人']) || {};
            const changed = Object.entries(masterProfile).some(([k, v]) => String(masterFields[k] || '') !== String(v));
            if (changed) {
                const masterNotice = showMessage(_moliliT('character.moliliUpdatingMaster', '正在更新「我的档案」...'), 'importing', 0);
                try {
                    // /api/characters/master 以请求体整卡替换（_reserved 由后端自行保留），
                    // 必须带上现有字段合并后的全量
                    const masterResponse = await fetch('/api/characters/master', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ ...masterFields, ...masterProfile }),
                    });
                    const updated = await masterResponse.json().catch(() => ({}));
                    if (!masterResponse.ok || !updated.success) {
                        throw new Error(updated.error || `HTTP ${masterResponse.status}`);
                    }
                } catch (error) {
                    throw new Error(_moliliT('character.moliliMasterUpdateFailed', `更新「我的档案」失败: ${error.message}`, { error: error.message }));
                } finally {
                    masterNotice.dismiss();
                }
            }
        }
        if (choice.mode === 'create') {
            const cardNotice = showMessage(
                _moliliT('character.moliliCreatingCard', `正在创建角色「${choice.name}」...`, { name: choice.name }),
                'importing', 0);
            let created;
            try {
                const createResponse = await fetch('/api/characters/catgirl', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        '档案名': choice.name,
                        '昵称': choice.name,
                        // role_prompt 按 ## 标题拆成独立字段（各进 persona 记忆、
                        // 卡面分区显示），主人身份是独立字段而非塞进设定。
                        // 均随卡可导出、可编辑；Molili 来源标记写在卡面元数据 origin。
                        ...moliliSectionFields,
                    }),
                });
                created = await createResponse.json().catch(() => ({}));
                if (!createResponse.ok || !created.success) {
                    throw new Error(created.error || `HTTP ${createResponse.status}`);
                }
            } finally {
                cardNotice.dismiss();
            }
            // 重名时后端自动加后缀（如 言叶(1)）：记忆必须导进实际建出的角色
            characterName = created.character_name || choice.name;
            // 兜底复核服务端实际给的名字（名单可能在确认后又多了同名角色）：超长就
            // 停在这里，不去调必然失败的 commit
            if (Number(preview.candidate_count) > 0 && !_moliliNameFitsMemory(characterName)) {
                _moliliRefreshCardList();
                return;
            }
        } else if (Object.keys(moliliSectionFields).length) {
            // 导入到现有角色：分节字段 + 主人身份字段合并进该角色档案。
            // ⚠ PUT /catgirl/{name} 的语义是"未传字段即删除"——必须先 GET 全量
            // 字段、合并增量后整卡回写，绝不能只发增量（否则角色的其他字段
            // 会被整批删掉）。旧版整块"设定"若与本次解析全文一致（= 上次导入
            // 写入的），不传该字段（PUT 会删除它），避免与分节字段重复占
            // persona 预算；用户改过则原样保留。
            const listResponse = await fetch('/api/characters?language=zh-CN');
            if (!listResponse.ok) {
                throw new Error(`HTTP ${listResponse.status}`);
            }
            const list = await listResponse.json();
            const targetCard = (list && list['猫娘'] && list['猫娘'][characterName]) || {};
            // 只回写可编辑字段，过滤口径与角色卡编辑器一致（isCharacterReservedFieldName）：
            // 保留字段由后端自行保留（PUT 不会删它们）；voice_id / model_type 一出现在
            // 请求体里就会被当成「要改音色/模型」去校验（音色暂不可用时整次导入 400）；
            // pngtuber_* 是读取时从 _reserved 展开的兼容字段，回写会变成普通卡片字段。
            const isReservedKey = (key) => key === 'voice_id' || key === 'model_type'
                || (typeof isCharacterReservedFieldName === 'function' && isCharacterReservedFieldName(key));
            const mutableCard = Object.fromEntries(
                Object.entries(targetCard).filter(([key]) => !isReservedKey(key)));
            const updates = { ...mutableCard, ...moliliSectionFields };
            const existingSetting = String(targetCard['设定'] || '');
            const fullText = (preview.character_card && preview.character_card.system_prompt) || '';
            // 只有本次的分节字段换了别的名字时才删旧整块；无标题设定本身就落在
            // 「设定」上，删掉等于把刚写的设定一起删了。
            const sectionsUseSetting = Object.prototype.hasOwnProperty.call(moliliSectionFields, '设定');
            if (!sectionsUseSetting && existingSetting && fullText && _moliliStripEmoji(existingSetting) === fullText) {
                delete updates['设定'];
            }
            const settingResponse = await fetch(`/api/characters/catgirl/${encodeURIComponent(characterName)}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(updates),
            });
            const saved = await settingResponse.json().catch(() => ({}));
            if (!settingResponse.ok || saved.success === false) {
                throw new Error(saved.error || `HTTP ${settingResponse.status}`);
            }
        }

        // 来源徽章：把卡的 origin 标记为 molili（与 本地创作/导入/创意工坊 并列展示）。
        // Best-effort：失败只告警，不阻断导入。
        try {
            const metaResponse = await fetch(`/api/characters/catgirl/${encodeURIComponent(characterName)}/card-meta`);
            const metaNow = await metaResponse.json().catch(() => ({}));
            const currentMeta = metaNow.meta || metaNow || {};
            // 后端只允许 self 改标为 molili；导入 / 创意工坊角色发了也必然 403，直接跳过
            //（这类角色照常导入记忆，只是不加 Molili 来源徽章）
            if (currentMeta.origin === 'self') {
                const saveMetaResponse = await fetch(`/api/characters/catgirl/${encodeURIComponent(characterName)}/card-meta`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ origin: 'molili' }),
                });
                if (saveMetaResponse.ok) {
                    const metaSaved = await saveMetaResponse.json().catch(() => ({}));
                    if (metaSaved.meta && window._cardMetas) {
                        window._cardMetas[characterName] = metaSaved.meta;
                        // 该角色详情面板若已打开，立即重渲染卡面信息，来源徽章不用重开页面才可见
                        if (typeof refreshOpenCardMetaBlock === 'function') refreshOpenCardMetaBlock(characterName);
                    }
                }
            }
        } catch (metaError) {
            console.warn('卡面来源标记失败（不阻断导入）:', metaError);
        }

        // 归档里没有任何记忆候选（只有角色设定）：设定已写好，没有记忆可导，
        // 直接收尾——commit 接口对空候选会回 400「Invalid candidate count」。
        if (!(Number(preview.candidate_count) > 0)) {
            _moliliForgetRetryTarget(preview);
            showMessage(_moliliT('character.moliliImportDone',
                `「${characterName}」导入完成`,
                { name: characterName }),
                'import-success', 0);
            _moliliRefreshCardList();
            return;
        }

        // 进度模态框：与页面弹窗同款覆盖层（保证可见），轮询后端真实计数
        //（persona 按组、逐日按批）刷新进度条与阶段文案。
        // 进度面板：底部居中悬浮条（不阻塞页面交互），轮询后端真实计数
        //（persona 按组、逐日按批）刷新进度条与阶段文案。
        const progressPanel = document.createElement('div');
        progressPanel.style.cssText = 'position:fixed;left:50%;top:96px;transform:translateX(-50%);z-index:10002;background:#fff;border:1px solid #d8e2ec;border-radius:12px;box-shadow:0 10px 30px rgba(0,0,0,0.18);padding:12px 18px;width:440px;max-width:90vw';
        const stageLine = document.createElement('div');
        stageLine.style.cssText = 'font-size:13px;font-weight:600;color:#222;margin-bottom:8px';
        stageLine.textContent = _moliliT('character.moliliStageFusion', '融合人格中...');
        const barOuter = document.createElement('div');
        barOuter.style.cssText = 'height:10px;border-radius:6px;background:#edf1f5;overflow:hidden';
        const barInner = document.createElement('div');
        barInner.style.cssText = 'height:100%;width:8%;border-radius:6px;background:linear-gradient(135deg,#40C5F1,#5dd4f7);transition:width 0.4s ease';
        barOuter.appendChild(barInner);
        const progressNote = document.createElement('div');
        progressNote.style.cssText = 'font-size:12px;color:#888;margin-top:8px';
        progressNote.textContent = _moliliT('character.moliliImporting',
            `正在为「${characterName}」导入记忆，可能需要几分钟，请勿关闭页面...`,
            { name: characterName });
        progressPanel.appendChild(stageLine);
        progressPanel.appendChild(barOuter);
        progressPanel.appendChild(progressNote);
        document.body.appendChild(progressPanel);

        // 预览阶段就知道有没有日常记忆要抽取：决定人格阶段在进度条里占多大比例
        const hasDailyToImport = (Number(preview.daily_extraction_calls) || 0) > 0
            || (Number(preview.counts && preview.counts.daily) || 0) > 0;
        const applyMoliliProgress = (p) => {
            const personaTotal = Number(p.persona_total) || 0;
            const personaDone = Number(p.persona_done) || 0;
            const dailyTotal = Number(p.daily_total) || 0;
            const dailyDone = Number(p.daily_done) || 0;
            const personaFrac = personaTotal ? personaDone / personaTotal : 0;
            if (dailyTotal) {
                const dailyFrac = dailyDone / dailyTotal;
                barInner.style.width = Math.round(100 * (0.25 * personaFrac + 0.75 * dailyFrac)) + '%';
                stageLine.textContent = _moliliT('character.moliliStageDaily',
                    `整理记忆批次 ${dailyDone}/${dailyTotal}...`, { done: dailyDone, total: dailyTotal });
            } else if (personaFrac > 0) {
                // 还没有日常记忆进度：按人格融合进度走，不让进度条停在初始宽度看着像卡住。
                // 后面还有日常记忆要导时只占前 25%（与上面的组合进度衔接，不会先冲到
                // 100% 再掉回去）；归档根本没有日常记忆时人格就是全部进度。阶段文案不变
                const personaShare = hasDailyToImport ? 0.25 : 1;
                barInner.style.width = Math.max(8, Math.round(100 * personaShare * personaFrac)) + '%';
            }
        };

        let moliliPollTimer = null;
        const stopMoliliProgressPolling = () => {
            if (moliliPollTimer) { clearInterval(moliliPollTimer); moliliPollTimer = null; }
            progressPanel.remove();
        };
        moliliPollTimer = setInterval(async () => {
            try {
                const resp = await fetch(`/api/memory/external_import/progress?character_name=${encodeURIComponent(characterName)}`);
                const data = await resp.json();
                console.debug('[Molili] progress poll:', JSON.stringify(data));
                if (data && data.success) applyMoliliProgress(data);
            } catch (e) { /* 忽略单次轮询失败，下一轮再试 */ }
        }, 5000);

        // 记下本次的导入目标（新建或现有角色都记）：commit 超时/部分失败后重开
        // 同一份归档，默认导回这个角色；整次成功后才清掉。
        _moliliRememberRetryTarget(preview, characterName);
        let commit;
        try {
            const controller = new AbortController();
            // 与后端 480s 转发窗口协调：前端留更大余量，保证后端先返回而非前端先断
            const timeoutId = setTimeout(() => controller.abort(), 540000);
            try {
                const commitResponse = await fetch('/api/memory/external_import/commit', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        character_name: characterName,
                        archive_b64: archiveB64,
                        source_format: 'molili',
                        acknowledge_warnings: true,
                    }),
                    signal: controller.signal,
                });
                commit = await commitResponse.json().catch(() => ({}));
                if (!commitResponse.ok || commit.status !== 'success') {
                    const outcome = commit.error_code;
                    if (outcome === 'external_import_timeout') {
                        // 后端转发窗口到点：与前端 AbortController 超时同一种处理
                        const timeoutError = new Error(commit.error || 'timed out');
                        timeoutError.name = 'AbortError';
                        throw timeoutError;
                    }
                    if (outcome === 'external_import_partial' || outcome === 'external_import_too_large') {
                        const partial = commit.partial_import || {};
                        // 已落盘的部分照样通知主窗口刷新上下文
                        if (partial.added_persona > 0 || partial.added_facts > 0) {
                            _moliliBroadcastMemoryEdited(partial.character_name || characterName);
                        }
                        const counts = { persona: partial.added_persona || 0, facts: partial.added_facts || 0 };
                        const remainingDays = Number(partial.remaining_days) || 0;
                        // 抽取失败的天 / 单独超上限的天：后端都有计数，一并告诉用户
                        const dayNotes = _moliliPartialDayNotes(partial);
                        // 分段续导：一次导不完的日期顺延到下次，提示用户再导一次接着往后导
                        const err = new Error((outcome === 'external_import_partial' && remainingDays > 0
                            ? _moliliT('character.moliliImportContinue',
                                `已导入一部分：保存了 ${counts.persona} 条人格、${counts.facts} 条事实，还有 ${remainingDays} 天的日常记忆留到下次。请再执行一次导入，会接着往后导，已导入的内容自动跳过。`,
                                { ...counts, days: remainingDays })
                            : outcome === 'external_import_partial'
                            ? _moliliT('character.moliliImportPartial',
                                `导入中途停止：已保存 ${counts.persona} 条人格、${counts.facts} 条事实。请再执行一次导入补齐，已导入的内容会自动跳过。`,
                                counts)
                            : _moliliT('character.moliliImportTooLarge',
                                `要整理的日常记忆超过单次导入上限，这部分未能导入（已保存 ${counts.persona} 条人格、${counts.facts} 条事实）。`,
                                counts)) + dayNotes);
                        err.moliliOutcome = outcome;
                        throw err;
                    }
                    throw new Error(commit.error || commit.detail || `HTTP ${commitResponse.status}`);
                }
            } finally {
                clearTimeout(timeoutId);
            }
        } finally {
            stopMoliliProgressPolling();
        }

        _moliliForgetRetryTarget(preview);
        _moliliBroadcastMemoryEdited(commit.character_name || characterName);
        showMessage(_moliliT('character.moliliImportDone',
            `「${characterName}」导入完成`,
            { name: characterName }),
            'import-success', 0);
    } catch (error) {
        console.error('Molili 导入失败:', error);
        // partial / too_large：文案已自带结果与下一步，不再套「导入失败」前缀
        const outcome = error && error.moliliOutcome;
        // 超时类失败 ≠ 失败：记忆服务器会继续把整批跑完，且逐日指纹幂等——
        // 稍候重跑一次导入即可收尾，已落盘部分全部跳过
        const message = String((error && error.message) || '');
        const timedOut = (error && error.name === 'AbortError') || /timed out|timeout|ReadTimeout/i.test(message);
        if (outcome) {
            showMessage(error.message, outcome === 'external_import_partial' ? 'import-warning' : 'import-error', 0);
        } else if (timedOut) {
            showMessage(_moliliT('character.moliliCommitTimeout',
                '本次导入耗时较长，服务端可能仍在后台继续处理。请等待约 1 分钟后重新执行一次导入收尾：已完成的日期会自动跳过，只补剩余部分。'),
                'import-warning', 0);
        } else {
            showMessage(_moliliT('character.moliliImportFailed', `Molili 导入失败: ${error.message}`, { error: error.message }), 'import-error');
        }
    }
    // 无论成败都在收尾时刷新列表（见 _moliliRefreshCardList）
    _moliliRefreshCardList();
}

function _showMoliliImportDialog(preview, archiveB64) {
    return new Promise(resolve => {
        const card = preview.character_card || {};
        const counts = preview.counts || {};
        const skipped = preview.skipped || {};
        const suggestedName = card.suggested_name || 'Molili';
        // 没有记忆候选的归档只建卡、不调 commit，不受记忆系统的名字上限约束
        const hasMemoryToImport = Number(preview.candidate_count) > 0;
        // 角色名单加载后填入，用于按后端重名规则推算新建角色的最终名字
        let knownCharacterNames = [];
        let characterNamesState = 'loading';

        const overlay = document.createElement('div');
        overlay.className = 'ccm-modal-overlay';
        overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.45);z-index:10002;display:flex;align-items:center;justify-content:center;animation:ccmFadeIn 0.18s ease';

        const dialog = document.createElement('div');
        dialog.style.cssText = 'background:#fff;border-radius:14px;padding:22px 26px 18px;width:480px;max-width:92vw;max-height:86vh;overflow:auto;box-shadow:0 14px 40px rgba(0,0,0,0.25);animation:ccmSlideUp 0.22s ease';

        const close = (value) => {
            if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
            document.removeEventListener('keydown', escHandler);
            resolve(value);
        };
        const escHandler = (e) => {
            if (e.key === 'Escape') close(null);
        };
        document.addEventListener('keydown', escHandler);

        const title = document.createElement('div');
        title.style.cssText = 'font-size:16px;font-weight:700;color:#222;margin-bottom:10px';
        title.textContent = _moliliT('character.moliliDialogTitle', '导入 Molili 角色');
        dialog.appendChild(title);

        const summary = document.createElement('div');
        summary.style.cssText = 'font-size:13px;color:#555;line-height:1.7;margin-bottom:12px;white-space:pre-wrap;word-break:break-word';
        const summaryLines = [
            _moliliT('character.moliliDetected',
                `检测到角色「${suggestedName}」· 记忆 ${counts.daily || 0} 条`,
                { name: suggestedName, memory: counts.daily || 0 }),
        ];
        const extraAgents = Number(skipped.extra_personality_agents) || 0;
        if (extraAgents > 0) {
            // 多角色归档只导第一个角色：用户需要知道其余角色不会进来
            summaryLines.push(_moliliT('character.moliliExtraAgentsNote',
                `归档中另有 ${extraAgents} 个角色的人格不会导入（本次只导入第一个角色）`,
                { count: extraAgents }));
        }
        const attachmentCount = skipped.attachments?.count || 0;
        const chatCount = Number(skipped.conversation_messages) || 0;
        if (attachmentCount > 0) {
            // 用户需要知情：附件（图）不随记忆迁移
            summaryLines.push(_moliliT('character.moliliAttachmentsNote',
                `附件 ${attachmentCount} 个与原始聊天不会随记忆迁移，如有需要请提前自行保存`,
                { count: attachmentCount }));
        } else if (chatCount > 0) {
            // 纯文本归档没有附件，原始聊天不迁移这件事也要单独告知
            summaryLines.push(_moliliT('character.moliliChatsNote',
                `原始聊天 ${chatCount} 条不会随记忆迁移，如有需要请提前自行保存`,
                { count: chatCount }));
        }
        const ownedItemCount = Number(skipped.owned_items) || 0;
        if (ownedItemCount > 0) {
            // Molili 的道具是游戏数据，不迁移，用户需要知情
            summaryLines.push(_moliliT('character.moliliOwnedItemsNote',
                `道具 ${ownedItemCount} 个不会迁移`,
                { count: ownedItemCount }));
        }
        if ((preview.warning_count || 0) > 0) {
            summaryLines.push(_moliliT('character.moliliWarnings',
                `⚠ 检测到 ${preview.warning_count} 条可疑指令模式，确认导入即代表知悉`,
                { count: preview.warning_count }));
        }
        summary.textContent = summaryLines.join('\n');
        dialog.appendChild(summary);

        // ── 主人身份区：Molili 的身份字段（昵称等）与全局主人卡是两回事。
        // 用户可选把身份同步进全局「我的档案」（全局生效）；不勾则完全不迁移身份。
        const masterProfile = preview.master_profile || {};
        const hasMasterFields = Object.keys(masterProfile).length > 0;
        let masterOverwrite = false;
        let masterCurrentLine = null;
        if (hasMasterFields) {
            const section = document.createElement('div');
            section.style.cssText = 'border:1px solid #e3e8ee;border-radius:10px;padding:10px 12px;margin-bottom:12px;font-size:13px;color:#333';

            const sectionTitle = document.createElement('div');
            sectionTitle.style.cssText = 'font-weight:700;margin-bottom:6px';
            sectionTitle.textContent = _moliliT('character.moliliMasterTitle', '我的身份');
            section.appendChild(sectionTitle);

            const detectedFields = Object.entries(masterProfile).map(([k, v]) => `${k} ${v}`).join('，');
            const detected = document.createElement('div');
            detected.style.cssText = 'color:#555;margin-bottom:4px;line-height:1.5';
            detected.textContent = _moliliT('character.moliliMasterDetected', `Molili 中的你：${detectedFields}`, { fields: detectedFields });
            section.appendChild(detected);

            masterCurrentLine = document.createElement('div');
            masterCurrentLine.style.cssText = 'color:#999;margin-bottom:8px;line-height:1.5';
            masterCurrentLine.textContent = _moliliT('character.moliliMasterCurrentLoading', '「我的档案」加载中...');
            section.appendChild(masterCurrentLine);

            const overwriteLabel = document.createElement('label');
            overwriteLabel.style.cssText = 'display:flex;align-items:flex-start;gap:6px;cursor:pointer;line-height:1.5';
            const overwriteCheck = document.createElement('input');
            overwriteCheck.type = 'checkbox';
            overwriteCheck.addEventListener('change', () => { masterOverwrite = overwriteCheck.checked; });
            const overwriteText = document.createElement('span');
            overwriteText.textContent = _moliliT('character.moliliMasterOverwrite',
                '同步更新「我的档案」为以上身份（注意：我的档案为所有角色共享，此更改会影响它们）');
            overwriteLabel.append(overwriteCheck, overwriteText);
            section.appendChild(overwriteLabel);

            dialog.appendChild(section);
        }

        // 模式选择：新建（默认，预填识别名）或导入现有角色
        const modeWrap = document.createElement('div');
        modeWrap.style.cssText = 'display:flex;flex-direction:column;gap:8px;margin-bottom:12px;font-size:13px;color:#333';

        const createLabel = document.createElement('label');
        createLabel.style.cssText = 'display:flex;align-items:center;gap:6px;cursor:pointer';
        const createRadio = document.createElement('input');
        createRadio.type = 'radio';
        createRadio.name = 'molili-target-mode';
        createRadio.checked = true;
        const createText = document.createElement('span');
        createText.textContent = _moliliT('character.moliliCreateNew', '新建角色：');
        createLabel.append(createRadio, createText);
        const nameInput = document.createElement('input');
        nameInput.type = 'text';
        nameInput.value = suggestedName;
        nameInput.maxLength = 50;
        nameInput.style.cssText = 'flex:1;padding:7px 10px;border:1px solid #dde2e8;border-radius:8px;font-size:13px;outline:none';
        nameInput.addEventListener('focus', () => { createRadio.checked = true; });
        createLabel.appendChild(nameInput);

        const existingLabel = document.createElement('label');
        existingLabel.style.cssText = 'display:flex;align-items:center;gap:6px;cursor:pointer';
        const existingRadio = document.createElement('input');
        existingRadio.type = 'radio';
        existingRadio.name = 'molili-target-mode';
        const existingText = document.createElement('span');
        existingText.textContent = _moliliT('character.moliliImportExisting', '导入到现有角色：');
        existingLabel.append(existingRadio, existingText);
        const existingSelect = document.createElement('select');
        existingSelect.style.cssText = 'flex:1;padding:7px 10px;border:1px solid #dde2e8;border-radius:8px;font-size:13px;background:#fff;outline:none';
        existingSelect.disabled = true;
        existingSelect.appendChild(new Option(_moliliT('character.moliliLoadingCharacters', '加载中...'), ''));
        existingRadio.addEventListener('change', () => {
            nameInput.disabled = existingRadio.checked;
            existingSelect.disabled = !existingRadio.checked;
        });
        createRadio.addEventListener('change', () => {
            nameInput.disabled = existingRadio.checked;
            existingSelect.disabled = !existingRadio.checked;
        });
        existingLabel.appendChild(existingSelect);

        const selectRow = document.createElement('div');
        selectRow.style.cssText = 'display:flex;gap:6px;padding-left:22px';
        selectRow.appendChild(existingSelect);
        modeWrap.append(createLabel, existingLabel, selectRow);
        dialog.appendChild(modeWrap);

        // 现有角色名单（GET /api/characters 的 猫娘 字典即全部档案）
        fetch('/api/characters').then(r => r.json()).then(data => {
            const names = Object.keys((data && data['猫娘']) || {}).sort();
            knownCharacterNames = names;
            characterNamesState = 'loaded';
            existingSelect.innerHTML = '';
            names.forEach(name => existingSelect.appendChild(new Option(name, name)));
            if (!names.length) {
                existingSelect.appendChild(new Option(_moliliT('character.moliliNoCharacters', '（暂无角色）'), ''));
            }
            // 上次用这份归档新建的角色还在 → 默认导回它（重试收尾），不再新建
            const retryTarget = _moliliReadRetryTargets()[_moliliRetryKey(preview)];
            if (retryTarget && names.includes(retryTarget)) {
                existingRadio.checked = true;
                existingSelect.value = retryTarget;
                nameInput.disabled = true;
                existingSelect.disabled = false;
            }
            if (masterCurrentLine) {
                const master = (data && data['主人']) || {};
                const currentText = [master['档案名'], master['昵称']].filter(Boolean).join(' / ') || '—';
                masterCurrentLine.textContent = _moliliT('character.moliliMasterCurrent',
                    `当前「我的档案」：${currentText}`, { current: currentText });
            }
        }).catch(() => {
            existingSelect.innerHTML = '';
            existingSelect.appendChild(new Option(_moliliT('character.moliliLoadFailed', '（列表加载失败）'), ''));
            characterNamesState = 'failed';
        });

        const footer = document.createElement('div');
        footer.style.cssText = 'display:flex;justify-content:flex-end;gap:8px;margin-top:6px';
        const mkBtn = (label, variant) => {
            const b = document.createElement('button');
            b.type = 'button';
            b.textContent = label;
            const base = 'padding:8px 16px;border-radius:10px;font-size:13px;font-weight:600;cursor:pointer;border:none;transition:filter 0.15s,transform 0.1s';
            if (variant === 'primary') {
                b.style.cssText = base + ';background:linear-gradient(135deg,#40C5F1,#5dd4f7);color:#fff;box-shadow:0 2px 6px rgba(64,197,241,0.3)';
            } else {
                b.style.cssText = base + ';background:#f3f5f7;color:#333';
            }
            b.onmouseenter = () => { b.style.filter = 'brightness(1.06)'; b.style.transform = 'translateY(-1px)'; };
            b.onmouseleave = () => { b.style.filter = ''; b.style.transform = ''; };
            return b;
        };
        const cancelBtn = mkBtn(_moliliT('character.moliliCancel', '取消'), 'secondary');
        cancelBtn.onclick = () => close(null);
        const confirmBtn = mkBtn(_moliliT('character.moliliConfirm', '创建并导入'), 'primary');
        confirmBtn.onclick = () => {
            if (createRadio.checked) {
                const name = nameInput.value.trim();
                if (!name) {
                    showMessage(_moliliT('character.moliliNameRequired', '请填写角色名'), 'warning');
                    return;
                }
                // 只有要导记忆时才受记忆系统的名字上限约束；按重名后缀后的最终名字算，
                // 所以名单必须已加载成功（没加载完就算不出后缀）
                if (hasMemoryToImport && characterNamesState !== 'loaded') {
                    showMessage(characterNamesState === 'failed'
                        ? _moliliT('character.moliliLoadFailed', '（列表加载失败）')
                        : _moliliT('character.moliliLoadingCharacters', '加载中...'), 'warning');
                    return;
                }
                if (hasMemoryToImport
                    && !_moliliNameFitsMemory(_moliliAvailableName(knownCharacterNames, name))) return;
                close({ mode: 'create', name, masterOverwrite });
            } else {
                const name = existingSelect.value;
                if (!name) {
                    showMessage(_moliliT('character.moliliPickExisting', '请选择要导入的现有角色'), 'warning');
                    return;
                }
                if (hasMemoryToImport && !_moliliNameFitsMemory(name)) return;
                close({ mode: 'existing', name, masterOverwrite });
            }
        };
        footer.append(cancelBtn, confirmBtn);
        dialog.appendChild(footer);

        overlay.appendChild(dialog);
        overlay.onclick = (e) => { if (e.target === overlay) close(null); };
        // keyframes 由 _createManagerModal 注入过则复用；没有就补一次
        if (!document.getElementById('ccm-modal-keyframes')) {
            const st = document.createElement('style');
            st.id = 'ccm-modal-keyframes';
            st.textContent = '@keyframes ccmFadeIn{from{opacity:0}to{opacity:1}}@keyframes ccmSlideUp{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:translateY(0)}}';
            document.head.appendChild(st);
        }
        document.body.appendChild(overlay);
        nameInput.focus();
    });
}

// ===== API 设置窗口 =====
function buildApiKeySettingsWindowFeatures(width = 1240, height = 940) {
    const availableWidth = Math.max(1, Number(window.screen && (window.screen.availWidth || window.screen.width)) || width);
    const availableHeight = Math.max(1, Number(window.screen && (window.screen.availHeight || window.screen.height)) || height);
    const windowWidth = Math.min(width, Math.max(720, availableWidth - 80));
    const windowHeight = Math.min(height, Math.max(560, availableHeight - 80));
    // 居中走 core 公共 helper：多显示器下叠加当前屏幕偏移，避免副屏弹窗跳回主屏。
    if (typeof window.buildCenteredPopupFeatures === 'function') {
        return window.buildCenteredPopupFeatures(windowWidth, windowHeight);
    }
    const left = Math.max(0, Math.floor((availableWidth - windowWidth) / 2));
    const top = Math.max(0, Math.floor((availableHeight - windowHeight) / 2));
    return `width=${windowWidth},height=${windowHeight},left=${left},top=${top},menubar=no,toolbar=no,location=no,status=no,resizable=yes,scrollbars=yes`;
}

function openApiKeySettings() {
    const url = '/api_key';
    const windowName = 'neko_api_key';
    const features = buildApiKeySettingsWindowFeatures();
    let childWin = null;

    if (typeof window.openOrFocusWindow === 'function') {
        childWin = window.openOrFocusWindow(url, windowName, features);
    } else {
        childWin = window.open(url, windowName, features);
    }

    if (childWin && typeof childWin.focus === 'function') {
        try {
            childWin.focus();
        } catch (error) {
            // 部分浏览器环境不允许主动聚焦，忽略即可。
        }
    }
}

function _setupApiKeySettingsButton() {
    const btn = document.getElementById('api-key-settings-btn');
    if (btn && !btn._bound) {
        btn._bound = true;
        btn.addEventListener('click', openApiKeySettings);
    }
}
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _setupApiKeySettingsButton);
} else {
    _setupApiKeySettingsButton();
}

// ===== 统一弹窗样式 =====
// 与导出角色卡弹窗风格一致的通用 Confirm / Alert / Toast
// 目的：在桌面端网页中也能稳定显示（替换老的 top-corner showMessage / 原生 confirm）。

// 弹窗动画 keyframes 只注入一次；多个弹窗助手共用（未注入时首个弹窗没有
// 入场动画，纯观感问题——CodeRabbit/wehos 可选建议）。
function _ensureManagerModalKeyframes() {
    if (document.getElementById('ccm-modal-keyframes')) return;
    const st = document.createElement('style');
    st.id = 'ccm-modal-keyframes';
    st.textContent = '@keyframes ccmFadeIn{from{opacity:0}to{opacity:1}}@keyframes ccmSlideUp{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:translateY(0)}}@keyframes ccmSlideOut{from{opacity:1;transform:translateY(0)}to{opacity:0;transform:translateY(-8px)}}';
    document.head.appendChild(st);
}

function _createManagerModal({ title, message, variant = 'info', buttons = [], dismissOnOverlay = true, icon = null }) {
    return new Promise(resolve => {
        const overlay = document.createElement('div');
        overlay.className = 'ccm-modal-overlay';
        overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.45);z-index:10002;display:flex;align-items:center;justify-content:center;animation:ccmFadeIn 0.18s ease';

        const dialog = document.createElement('div');
        dialog.style.cssText = 'background:#fff;border-radius:14px;padding:22px 26px 18px;min-width:340px;max-width:90vw;box-shadow:0 14px 40px rgba(0,0,0,0.25);font-family:inherit;animation:ccmSlideUp 0.22s ease';

        const accentColor = {
            info: '#40C5F1',
            success: '#58c38a',
            warning: '#f0ad4e',
            error: '#ff5a5a',
            danger: '#ff5a5a',
        }[variant] || '#40C5F1';

        if (title) {
            const t = document.createElement('div');
            t.style.cssText = 'font-size:16px;font-weight:700;color:#222;margin-bottom:8px;display:flex;align-items:center;gap:8px';
            if (icon) {
                const i = document.createElement('i');
                i.className = 'fa ' + icon;
                i.style.cssText = 'color:' + accentColor + ';font-size:16px';
                t.appendChild(i);
            }
            const ts = document.createElement('span');
            ts.textContent = title;
            t.appendChild(ts);
            dialog.appendChild(t);
        }

        if (message) {
            const d = document.createElement('div');
            d.style.cssText = 'font-size:13px;color:#555;margin-bottom:18px;line-height:1.5;white-space:pre-wrap;word-break:break-word';
            d.textContent = message;
            dialog.appendChild(d);
        }

        const footer = document.createElement('div');
        footer.style.cssText = 'display:flex;justify-content:flex-end;gap:8px;flex-wrap:wrap';

        const mkBtn = (label, btnVariant) => {
            const b = document.createElement('button');
            b.type = 'button';
            b.textContent = label;
            const base = 'padding:8px 16px;border-radius:10px;font-size:13px;font-weight:600;cursor:pointer;border:none;transition:filter 0.15s,transform 0.1s';
            if (btnVariant === 'primary') {
                b.style.cssText = base + ';background:linear-gradient(135deg,#40C5F1,#5dd4f7);color:#fff;box-shadow:0 2px 6px rgba(64,197,241,0.3)';
            } else if (btnVariant === 'danger') {
                b.style.cssText = base + ';background:linear-gradient(135deg,#ff7a7a,#ff5a5a);color:#fff;box-shadow:0 2px 6px rgba(255,90,90,0.3)';
            } else {
                b.style.cssText = base + ';background:#f3f5f7;color:#333';
            }
            b.onmouseenter = () => { b.style.filter = 'brightness(1.06)'; b.style.transform = 'translateY(-1px)'; };
            b.onmouseleave = () => { b.style.filter = ''; b.style.transform = ''; };
            return b;
        };

        const close = (value) => {
            if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
            resolve(value);
        };

        (buttons || []).forEach(bt => {
            const btn = mkBtn(bt.label, bt.variant || 'secondary');
            btn.onclick = () => close(bt.value);
            footer.appendChild(btn);
        });

        dialog.appendChild(footer);
        overlay.appendChild(dialog);
        if (dismissOnOverlay) {
            overlay.onclick = (e) => { if (e.target === overlay) close(null); };
        }
        // ESC 关闭
        const escHandler = (e) => {
            if (e.key === 'Escape') { document.removeEventListener('keydown', escHandler); close(null); }
        };
        document.addEventListener('keydown', escHandler);

        // 注入一次性动画 keyframes
        _ensureManagerModalKeyframes();

        document.body.appendChild(overlay);
    });
}

// 确认对话框（Promise<boolean>）
function showConfirmDialog(message, options = {}) {
    const title = options.title || (window.t ? window.t('common.confirm') : '确认');
    const okText = options.okText || (window.t ? window.t('common.confirm') : '确认');
    const cancelText = options.cancelText || (window.t ? window.t('common.cancel') : '取消');
    const variant = options.danger ? 'danger' : 'info';
    const icon = options.danger ? 'fa-exclamation-triangle' : 'fa-question-circle';
    return _createManagerModal({
        title,
        message,
        variant,
        icon,
        buttons: [
            { label: cancelText, variant: 'secondary', value: false },
            { label: okText, variant: options.danger ? 'danger' : 'primary', value: true },
        ],
    }).then(v => v === true);
}

// 提示对话框（Promise<void>，仅 OK 按钮）
function showAlertDialog(message, options = {}) {
    const typeMap = {
        error:   { titleKey: 'common.error',   fallback: '错误', icon: 'fa-exclamation-circle', variant: 'error' },
        warning: { titleKey: 'common.warning', fallback: '警告', icon: 'fa-exclamation-triangle', variant: 'warning' },
        success: { titleKey: 'common.success', fallback: '成功', icon: 'fa-check-circle', variant: 'success' },
        info:    { titleKey: 'common.alert',   fallback: '提示', icon: 'fa-info-circle', variant: 'info' },
    };
    const t = typeMap[options.type || 'info'];
    const title = options.title || (window.t ? window.t(t.titleKey) : t.fallback);
    const okText = options.okText || (window.t ? window.t('common.ok') : '确定');
    return _createManagerModal({
        title,
        message,
        variant: t.variant,
        icon: t.icon,
        buttons: [{ label: okText, variant: 'primary', value: true }],
    });
}

// ===== 导出角色卡（弹窗：取消 / 仅导出设定 / 导出角色卡） =====
function showExportOptionsModal(catgirlName) {
    return new Promise(resolve => {
        const overlay = document.createElement('div');
        overlay.className = 'export-options-overlay';
        overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.45);z-index:10001;display:flex;align-items:center;justify-content:center';

        const dialog = document.createElement('div');
        dialog.style.cssText = 'background:#fff;border-radius:14px;padding:22px 26px 18px;min-width:360px;max-width:90vw;box-shadow:0 14px 40px rgba(0,0,0,0.25);font-family:inherit';

        const title = document.createElement('div');
        title.style.cssText = 'font-size:16px;font-weight:700;color:#222;margin-bottom:8px';
        title.textContent = (window.t ? window.t('character.exportOptions') : '导出角色卡');
        dialog.appendChild(title);

        const desc = document.createElement('div');
        desc.style.cssText = 'font-size:13px;color:#555;margin-bottom:18px;line-height:1.5';
        const descTpl = window.t ? window.t('character.exportOptionsDesc') : '请选择要导出的内容：';
        desc.textContent = descTpl + ' 「' + catgirlName + '」';
        dialog.appendChild(desc);

        const footer = document.createElement('div');
        footer.style.cssText = 'display:flex;justify-content:flex-end;gap:8px;flex-wrap:wrap';

        const mkBtn = (label, variant) => {
            const b = document.createElement('button');
            b.type = 'button';
            b.textContent = label;
            const base = 'padding:8px 16px;border-radius:10px;font-size:13px;font-weight:600;cursor:pointer;border:none;transition:filter 0.15s,transform 0.1s';
            if (variant === 'primary') {
                b.style.cssText = base + ';background:linear-gradient(135deg,#40C5F1,#5dd4f7);color:#fff;box-shadow:0 2px 6px rgba(64,197,241,0.3)';
            } else {
                b.style.cssText = base + ';background:#f3f5f7;color:#333';
            }
            b.onmouseenter = () => { b.style.filter = 'brightness(1.06)'; b.style.transform = 'translateY(-1px)'; };
            b.onmouseleave = () => { b.style.filter = ''; b.style.transform = ''; };
            return b;
        };

        const cancelBtn = mkBtn(window.t ? window.t('common.cancel') : '取消', 'secondary');
        cancelBtn.onclick = () => { close(); resolve(null); };
        footer.appendChild(cancelBtn);

        const settingsBtn = mkBtn(window.t ? window.t('character.exportSettingsOnly') : '仅导出设定', 'secondary');
        settingsBtn.onclick = () => { close(); resolve('settings-only'); };
        footer.appendChild(settingsBtn);

        const fullBtn = mkBtn(window.t ? window.t('character.exportFull') : '导出角色卡', 'primary');
        fullBtn.onclick = () => { close(); resolve('full'); };
        footer.appendChild(fullBtn);

        dialog.appendChild(footer);
        overlay.appendChild(dialog);
        overlay.onclick = (e) => { if (e.target === overlay) { close(); resolve(null); } };
        document.body.appendChild(overlay);

        function close() { if (overlay.parentNode) overlay.parentNode.removeChild(overlay); }
    });
}

async function _downloadBlobAs(blob, filename, pickerType) {
    // pickerType: { description, accept }，限制保存对话框文件类型
    try {
        if ('showSaveFilePicker' in window && pickerType) {
            const fh = await window.showSaveFilePicker({ suggestedName: filename, types: [pickerType] });
            const w = await fh.createWritable();
            await w.write(blob);
            await w.close();
            return true;
        }
    } catch (err) {
        if (err && err.name === 'AbortError') return false;
        // 其它错误回退到 <a> 下载
    }
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { if (a.parentNode) a.parentNode.removeChild(a); URL.revokeObjectURL(url); }, 0);
    return true;
}

function _filenameFromContentDisposition(headerValue, fallback) {
    if (!headerValue) return fallback;
    const star = headerValue.match(/filename\*=UTF-8''([^;]+)/i);
    if (star) {
        try { return decodeURIComponent(star[1]); } catch (e) { /* fallthrough */ }
    }
    const m = headerValue.match(/filename="([^"]+)"/i);
    if (m) return m[1];
    return fallback;
}

async function exportCharacterCard(catgirlName) {
    let mode;
    try {
        mode = await showExportOptionsModal(catgirlName);
    } catch (e) {
        return;
    }
    if (!mode) return;

    const url = mode === 'settings-only'
        ? `/api/characters/catgirl/${encodeURIComponent(catgirlName)}/export-settings`
        : `/api/characters/catgirl/${encodeURIComponent(catgirlName)}/export`;
    const fallbackName = mode === 'settings-only'
        ? `${catgirlName}_设定.nekocfg`
        : `${catgirlName}.png`;
    const pickerType = mode === 'settings-only'
        ? { description: 'NEKO 设定文件', accept: { 'application/octet-stream': ['.nekocfg'] } }
        : { description: 'NEKO 角色卡 (PNG)', accept: { 'image/png': ['.png'] } };

    const loadingText = window.t ? window.t('character.exportingCard') : '正在导出...';
    showMessage(loadingText, 'info');
    try {
        const resp = await fetch(url, { method: 'GET' });
        if (!resp.ok) {
            const errData = await resp.json().catch(() => ({ error: `HTTP ${resp.status}` }));
            throw new Error(errData.error || `HTTP ${resp.status}`);
        }
        const blob = await resp.blob();
        const filename = _filenameFromContentDisposition(resp.headers.get('Content-Disposition'), fallbackName);
        const ok = await _downloadBlobAs(blob, filename, pickerType);
        if (ok) {
            const successText = window.t ? window.t('character.exportCardSuccess') : '导出成功';
            showMessage(successText, 'success');
        }
    } catch (error) {
        console.error('导出角色卡失败:', error);
        const errorText = window.t ? window.t('character.exportCardFailed', { error: error.message }) : `导出失败: ${error.message}`;
        showMessage(errorText, 'error');
    }
}

// 当前视图模式
