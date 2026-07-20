// Part responsibility: character-card data loading, cache synchronization, card metadata, import, and export.

let globalCharacterCards = [];

// 全局变量：当前打开的角色卡ID（用于模态框操作）
let currentCharacterCardId = null;

const CHARACTER_CARD_MODEL_SCAN_RENDER_BUDGET_MS = 2500;
let characterCardLoadSequence = 0;

function isCharacterCardLoadStale(loadSequence) {
    return loadSequence !== undefined && loadSequence !== characterCardLoadSequence;
}

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
        if (isCharacterCardLoadStale(loadSequence)) {
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
                        if (isCharacterCardLoadStale(loadSequence)) {
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
    // 页面初始化、工坊同步和导入都可能并发触发刷新。旧请求晚返回时必须整轮退出，
    // 不能只淘汰它的模型扫描，否则它会用导入前的快照覆盖最新列表。
    if (!characterData || isCharacterCardLoadStale(loadSequence)) return;

    // 模型扫描可能受 Linux 新存储根、创意工坊目录或 Steam 状态影响变慢。
    // 角色列表不应被模型扫描阻塞；扫描完成后再用于预览/上传等增强能力。
    const modelScanPromise = scanModels(loadSequence);

    // 先在局部数组中组装完整快照，确认仍是最新一轮后再一次性提交到 UI；
    // 避免并发刷新通过共享 window.characterCards 相互追加或清空。
    const nextCharacterCards = [];
    let idCounter = 1;

    // 只处理猫娘数据，忽略其他角色类型（包括主人）
    const catgirls = characterData['猫娘'] || {};
    for (const [name, data] of Object.entries(catgirls)) {
        nextCharacterCards.push(buildCharacterCardEntry(name, data, idCounter++));
    }

    // 从character_cards文件夹加载角色卡
    try {
        const response = await fetch('/api/characters/character-card/list', { cache: 'no-store' });
        if (response.ok) {
            const data = await response.json();
            if (isCharacterCardLoadStale(loadSequence)) return;
            if (data.success) {
                for (const card of data.character_cards) {
                    nextCharacterCards.push({
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
    if (isCharacterCardLoadStale(loadSequence)) return;

    // 扫描模型文件夹中的 character_settings JSON 文件仅用于旧格式兼容，不能阻塞角色管理主列表。
    const characterSettingsStartId = idCounter;

    // 获取当前猫娘
    let currentCatgirl = '';
    try {
        const currentResp = await fetch('/api/characters/current_catgirl', { cache: 'no-store' });
        const currentData = await currentResp.json();
        currentCatgirl = currentData.current_catgirl || '';
    } catch (e) {}
    if (isCharacterCardLoadStale(loadSequence)) return;

    // 预取已设置卡面的猫娘名单（避免逐个发起 404 请求）
    await loadCardFaceNames(loadSequence);
    if (isCharacterCardLoadStale(loadSequence)) return;
    // 预取卡面元数据（作者/创建时间/来源）
    await loadCardMetas(loadSequence);
    if (isCharacterCardLoadStale(loadSequence)) return;

    // 只有最新一轮加载可以提交共享状态和重绘列表。
    window.characterCards = nextCharacterCards;
    globalCharacterCards = nextCharacterCards;
    window._workshopCurrentCatgirl = currentCatgirl;
    refreshCharacterCardSelectOptions();

    // 渲染卡片/列表视图
    renderCharaCardsView();

    // 显示刷新成功消息
    if (window.characterCards && window.characterCards.length > 0) {
        showMessage(window.t ? window.t('steam.characterCardsRefreshed', { count: window.characterCards.length }) : `已刷新角色卡列表，共 ${window.characterCards.length} 个角色卡`, 'success');
    } else {
        showMessage(window.t ? window.t('steam.characterCardsRefreshedEmpty') : '已刷新角色卡列表，暂无角色卡', 'info');
    }

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
async function loadCardFaceNames(loadSequence) {
    try {
        const resp = await fetch('/api/characters/card-faces', { cache: 'no-store' });
        if (!resp.ok) return;
        const data = await resp.json();
        if (isCharacterCardLoadStale(loadSequence)) return;
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
        if (form && form._autoCreated) form._autoCreatedDependentPopup = existingWindow;
        existingWindow.focus();
        return;
    }
    delete window._openSettingsWindows[url];

    const popup = window.open(url, '_blank',
        'toolbar=no,location=no,status=no,menubar=no,scrollbars=yes,resizable=yes,width=' + screen.availWidth + ',height=' + screen.availHeight + ',top=0,left=0');
    if (!popup) {
        if (typeof showAlert === 'function') await showAlert(window.t ? window.t('character.allowPopups') : '请允许弹窗！');
        // 弹窗被拦截：回滚本次及此前重命名遗留的 detached 临时角色，避免用户直接刷新/关页时残留空记录
        if (form && (form._autoCreated || form._autoCreatedDetachedName)) {
            await rollbackAutoCreatedCatgirl(form);
        }
        return;
    }

    window._openSettingsWindows[url] = popup;
    if (form && form._autoCreated) form._autoCreatedDependentPopup = popup;
    popup.moveTo(0, 0);
    popup.resizeTo(screen.availWidth, screen.availHeight);
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
async function loadCardMetas(loadSequence) {
    try {
        const resp = await fetch('/api/characters/card-metas', { cache: 'no-store' });
        if (!resp.ok) return;
        const data = await resp.json();
        if (isCharacterCardLoadStale(loadSequence)) return;
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
        title.textContent = window.t ? window.t('character.cardMeta') : '卡面信息';
        container.appendChild(title);

        // 来源徽章
        const originRow = document.createElement('div');
        originRow.className = 'card-meta-row card-meta-origin';
        const originLabel = document.createElement('span');
        originLabel.className = 'card-meta-label';
        originLabel.textContent = window.t ? window.t('character.cardOriginLabel') : '来源';
        const originValue = document.createElement('span');
        originValue.className = 'card-meta-origin-badge origin-' + origin;
        const originKey = origin === 'imported' ? 'character.cardOriginImported'
            : origin === 'steam' ? 'character.cardOriginSteam'
                : 'character.cardOriginSelf';
        const originText = window.t ? window.t(originKey) : (origin === 'imported' ? '导入' : origin === 'steam' ? '创意工坊' : '本地');
        originValue.textContent = originText;
        originRow.appendChild(originLabel);
        originRow.appendChild(originValue);
        container.appendChild(originRow);

        // 作者（可编辑：仅 origin=self）
        const authorRow = document.createElement('div');
        authorRow.className = 'card-meta-row card-meta-author';
        const authorLabel = document.createElement('span');
        authorLabel.className = 'card-meta-label';
        authorLabel.textContent = window.t ? window.t('character.cardAuthor') : '作者';
        authorRow.appendChild(authorLabel);

        if (origin === 'self') {
            const authorInput = document.createElement('input');
            authorInput.type = 'text';
            authorInput.className = 'card-meta-author-input';
            authorInput.value = author;
            authorInput.maxLength = 64;
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
            authorRow.appendChild(authorValue);
        }
        container.appendChild(authorRow);

        // 创建时间
        if (createdAt) {
            const timeRow = document.createElement('div');
            timeRow.className = 'card-meta-row card-meta-time';
            const timeLabel = document.createElement('span');
            timeLabel.className = 'card-meta-label';
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

async function handleImportCharacterCard(event) {
    const file = event.target.files[0];
    if (!file) return;
    event.target.value = '';

    const isNekoFile = file.name.endsWith('.nekocfg');
    const isPngFile = file.type.startsWith('image/') || file.name.endsWith('.png');
    if (!isNekoFile && !isPngFile) {
        showMessage(window.t ? window.t('character.importInvalidFile') : '请选择有效的PNG图片文件或.nekocfg设定文件', 'warning');
        return;
    }

    const loadingText = window.t ? window.t('character.importingCard') : '正在导入角色卡...';
    showMessage(loadingText, 'info');

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
        const result = await response.json();

        const successText = window.t ? window.t('character.importCardSuccess', { name: result.character_name }) : `角色卡 "${result.character_name}" 导入成功`;
        showMessage(successText, 'success');

        // 刷新角色卡列表（含 sidecar / 卡面 / 视图重新渲染）
        if (typeof loadCharacterCards === 'function') {
            await loadCharacterCards();
        } else if (typeof loadCharacterData === 'function') {
            await loadCharacterData();
        }
    } catch (error) {
        console.error('导入角色卡失败:', error);
        const errorText = window.t ? window.t('character.importCardFailed', { error: error.message }) : `导入角色卡失败: ${error.message}`;
        showMessage(errorText, 'error');
    }
}

// 绑定导入按钮（页面加载后）
function _setupImportCardButton() {
    const btn = document.getElementById('chara-import-btn');
    const input = document.getElementById('chara-import-input');
    if (btn && input && !btn._bound) {
        btn._bound = true;
        btn.addEventListener('click', () => input.click());
        input.addEventListener('change', handleImportCharacterCard);
    }
}
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _setupImportCardButton);
} else {
    _setupImportCardButton();
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
        if (!document.getElementById('ccm-modal-keyframes')) {
            const st = document.createElement('style');
            st.id = 'ccm-modal-keyframes';
            st.textContent = '@keyframes ccmFadeIn{from{opacity:0}to{opacity:1}}@keyframes ccmSlideUp{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:translateY(0)}}@keyframes ccmSlideOut{from{opacity:1;transform:translateY(0)}to{opacity:0;transform:translateY(-8px)}}';
            document.head.appendChild(st);
        }

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
