// Part responsibility: workshop subscriptions, pagination, item details, and automatic character-card scanning.

let allSubscriptions = []; // 存储所有订阅物品
let currentPage = 1;
let itemsPerPage = 10;
let totalPages = 1;
let currentSortField = 'timeAdded'; // 默认按添加时间排序
let currentSortOrder = 'desc'; // 默认降序

function getWorkshopManagerLanlanName() {
    const urlParams = new URLSearchParams(window.location.search);
    return urlParams.get('lanlan_name') || '';
}

function openWorkshopVoiceClone(itemId) {
    const params = new URLSearchParams({
        workshop_item_id: String(itemId),
        source: 'workshop'
    });
    const lanlanName = getWorkshopManagerLanlanName();
    if (lanlanName) {
        params.set('lanlan_name', lanlanName);
    }

    const url = `/voice_clone?${params.toString()}`;
    const popup = window.open(url, `workshopVoiceClone_${itemId}`, 'width=920,height=860,scrollbars=yes,resizable=yes');
    if (!popup) {
        window.location.href = url;
    }
}

// escapeHtml 已在上方定义（DOM-based，非 string 走 String(text) 转换）

// 安全获取作者显示名（始终返回字符串，兼容 item 为 null/undefined）
function safeAuthorName(item) {
    const raw = item?.authorName || (item?.steamIDOwner != null ? String(item.steamIDOwner) : '');
    return String(raw) || (window.t ? window.t('steam.unknownAuthor') : '未知作者');
}

// 加载订阅物品
function loadSubscriptions() {
    const subscriptionsList = document.getElementById('subscriptions-list');
    subscriptionsList.innerHTML = `<div class="empty-state"><p>${window.t ? window.t('steam.loadingSubscriptions') : '正在加载您的订阅物品...'}</p></div>`;

    // 调用后端API获取订阅物品列表
    fetch('/api/steam/workshop/subscribed-items')
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            return response.json();
        })
        .then(data => {
            if (!data.success) {
                subscriptionsList.innerHTML = `<div class="empty-state"><p>${window.t ? window.t('steam.fetchFailed') : 'Failed to fetch subscribed items'}: ${data.error || (window.t ? window.t('common.unknownError') : 'Unknown error')}</p></div>`;
                // 如果有消息提示，显示给用户
                if (data.message) {
                    showMessage(data.message, 'error');
                }
                updatePagination(); // 更新分页状态
                return;
            }

            // 保存所有订阅物品到全局变量
            allSubscriptions = data.items || [];

            // 【成就】有订阅物品时解锁创意工坊成就
            if (allSubscriptions.length > 0) {
                if (window.parent && window.parent.unlockAchievement) {
                    window.parent.unlockAchievement('ACH_WORKSHOP_USE').catch(err => {
                        console.error('解锁创意工坊成就失败:', err);
                    });
                } else if (window.opener && window.opener.unlockAchievement) {
                    window.opener.unlockAchievement('ACH_WORKSHOP_USE').catch(err => {
                        console.error('解锁创意工坊成就失败:', err);
                    });
                } else if (window.unlockAchievement) {
                    window.unlockAchievement('ACH_WORKSHOP_USE').catch(err => {
                        console.error('解锁创意工坊成就失败:', err);
                    });
                }
            }

            // 应用排序（从下拉框获取排序方式）
            const sortSelect = document.getElementById('sort-subscription');
            if (sortSelect) {
                const [field, order] = sortSelect.value.split('_');
                sortSubscriptions(field, order);
            } else {
                // 默认按日期降序排序
                sortSubscriptions('date', 'desc');
            }

            // 计算总页数
            totalPages = Math.ceil(allSubscriptions.length / itemsPerPage);
            if (totalPages < 1) totalPages = 1;
            if (currentPage > totalPages) currentPage = totalPages;

            // 显示当前页的数据
            renderSubscriptionsPage();

            // 更新分页UI
            updatePagination();
        })
        .catch(error => {
            console.error('获取订阅物品失败:', error);
            subscriptionsList.innerHTML = `<div class="empty-state"><p>${window.t ? window.t('steam.fetchFailed') : '获取订阅物品失败'}: ${error.message}</p></div>`;
            showMessage(window.t ? window.t('steam.cannotConnectToServer') : '无法连接到服务器，请稍后重试', 'error');
        });
}

// 渲染当前页的订阅物品
function renderSubscriptionsPage() {
    const subscriptionsList = document.getElementById('subscriptions-list');

    if (allSubscriptions.length === 0) {
        subscriptionsList.innerHTML = `<div class="empty-state"><p>${window.t ? window.t('steam.noSubscriptions') : 'You haven\'t subscribed to any workshop items yet'}</p></div>`;
        return;
    }

    // 计算当前页的数据范围
    const startIndex = (currentPage - 1) * itemsPerPage;
    const endIndex = startIndex + itemsPerPage;
    const currentItems = allSubscriptions.slice(startIndex, endIndex);

    // 生成卡片HTML
    subscriptionsList.innerHTML = currentItems.map(item => {
        // 格式化物品数据为前端所需格式
        // 确保publishedFileId转换为字符串，避免类型错误
        const formattedItem = {
            id: String(item.publishedFileId),
            rawName: item.title || `${window.t ? window.t('steam.unknownItem') : '未知物品'}_${String(item.publishedFileId)}`,
            name: escapeHtml(item.title || `${window.t ? window.t('steam.unknownItem') : '未知物品'}_${String(item.publishedFileId)}`),
            author: escapeHtml(safeAuthorName(item)),
            rawAuthor: safeAuthorName(item),
            subscribedDate: item.timeAdded ? new Date(item.timeAdded * 1000).toLocaleDateString() : (window.t ? window.t('steam.unknownDate') : '未知日期'),
            lastUpdated: item.timeUpdated ? new Date(item.timeUpdated * 1000).toLocaleDateString() : (window.t ? window.t('steam.unknownDate') : '未知日期'),
            size: formatFileSize(item.fileSizeOnDisk || item.fileSize || 0),
            previewUrl: encodeURI(item.previewUrl || item.previewImageUrl || '../static/icons/Steam_icon_logo.png'),
            state: item.state || {},
            // 添加安装路径信息
            installedFolder: item.installedFolder || '',
            description: escapeHtml(item.description || (window.t ? window.t('steam.noDescription') : '暂无描述')),
            timeAdded: item.timeAdded || 0,
            fileSize: item.fileSizeOnDisk || item.fileSize || 0,
            voiceReferenceAvailable: !!item.voiceReferenceAvailable,
            voiceReferenceDisplayName: escapeHtml(item.voiceReference?.displayName || ''),
        };

        // 确定状态类和文本
        let statusClass = 'status-subscribed';
        let statusText = window.t ? window.t('steam.status.subscribed') : '已订阅';

        if (formattedItem.state.downloading) {
            statusClass = 'status-downloading';
            statusText = window.t ? window.t('steam.status.downloading') : '下载中';
        } else if (formattedItem.state.needsUpdate) {
            statusClass = 'status-needs-update';
            statusText = window.t ? window.t('steam.status.needsUpdate') : '需要更新';
        } else if (formattedItem.state.installed) {
            statusClass = 'status-installed';
            statusText = window.t ? window.t('steam.status.installed') : '已安装';
        }

        return `
            <div class="workshop-card">
                <div class="card-header">
                    <img src="${formattedItem.previewUrl}" alt="${formattedItem.name}" class="card-image" onerror="this.src='../static/icons/Steam_icon_logo.png'">
                    <div class="status-badge ${statusClass}">
                        <svg class="badge-bg" viewBox="-5 -5 115 115">
                            <path d="M6.104,38.038 C1.841,45.421 1.841,54.579 6.104,61.962 L18.785,83.923 C23.048,91.306 30.979,95.885 39.505,95.885 L64.865,95.885 C73.391,95.885 81.322,91.306 85.585,83.923 L98.266,61.962 C102.529,54.579 102.529,45.421 98.266,38.038 L85.585,16.077 C81.322,8.694 73.391,4.115 64.865,4.115 L39.505,4.115 C30.979,4.115 23.048,8.694 18.785,16.077 Z"
                                  fill="#21b8ff"
                                  stroke="#dcf4ff"
                                  stroke-width="8" />
                        </svg>
                        <div class="badge-text">${statusText}</div>
                    </div>
                </div>
                <div class="card-content">
                    <h3 class="card-title">${formattedItem.name}<img src="/static/icons/paw_ui.png" class="card-title-paw" alt=""></h3>
                    <div class="author-info">
                        <div class="author-avatar">${escapeHtml(String(formattedItem.rawAuthor).substring(0, 2).toUpperCase())}</div>
                        <span>${window.t ? window.t('steam.author') : '作者:'} ${formattedItem.author}</span>
                    </div>
                    <div class="card-info-grid">
                        <div class="card-info-item"><span class="info-label">${window.t ? window.t('steam.subscribed_date') : '订阅日期:'}</span> <span class="info-value">${formattedItem.subscribedDate}</span></div>
                        <div class="card-info-item"><span class="info-label">${window.t ? window.t('steam.last_updated') : '上次更新:'}</span> <span class="info-value">${formattedItem.lastUpdated}</span></div>
                        <div class="card-info-item"><span class="info-label">${window.t ? window.t('steam.size') : '大小:'}</span> <span class="info-value">${formattedItem.size}</span></div>
                    </div>
                    ${formattedItem.state && formattedItem.state.downloading && item.downloadProgress ?
                `<div class="download-progress">
                            <div class="progress-bar">
                                <div class="progress-fill" style="width: ${item.downloadProgress.percentage}%">
                                    ${item.downloadProgress.percentage.toFixed(1)}%
                                </div>
                            </div>
                        </div>` : ''
            }
                    <div class="card-actions">
                        ${formattedItem.voiceReferenceAvailable ? `
                        <button class="button button-primary" onclick="openWorkshopVoiceClone('${formattedItem.id}')" title="${formattedItem.voiceReferenceDisplayName || ''}" style="margin-bottom: 8px;">
                            ${window.t ? window.t('steam.openVoiceClone') : '在语音克隆页打开'}
                        </button>` : ''}
                        <button class="button button-primary" data-item-id="${formattedItem.id}" data-item-name="${formattedItem.name}" onclick="addWorkshopCharacterCardFromSubscription(this)" style="margin-bottom: 8px;">${window.t ? window.t('steam.workshopAddCharacterCard') : '加入角色卡'}</button>
                        <button class="button button-danger" data-item-id="${formattedItem.id}" data-item-name="${formattedItem.name}" onclick="unsubscribeItem(this.dataset.itemId, this.dataset.itemName)">${window.t ? window.t('steam.unsubscribe') : '取消订阅'}</button>
                    </div>
                </div>
            </div>
        `;
    }).join('');
}

function formatWorkshopCharacterNameList(names) {
    const list = Array.isArray(names)
        ? names.map(name => String(name || '').trim()).filter(Boolean)
        : [];
    return list.length > 0 ? list.join('、') : (window.t ? window.t('steam.unknownCharacterCard') : '未知角色卡');
}

async function showWorkshopCharacterAddAlert(message, type = 'info') {
    if (typeof showAlertDialog === 'function') {
        const title = type === 'info'
            ? (window.t ? window.t('steam.characterCardAlreadyExistsTitle') : '角色卡已存在')
            : (window.t ? window.t('common.warning') : '提示');
        await showAlertDialog(message, {
            type,
            title,
        });
        return;
    }
    window.alert(message);
}

async function addWorkshopCharacterCardFromSubscription(button) {
    const itemId = button?.dataset?.itemId || '';
    if (!itemId) return;

    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = window.t ? window.t('steam.workshopAddingCharacterCard') : '正在加入...';

    try {
        const response = await fetch(`/api/steam/workshop/sync-character/${encodeURIComponent(itemId)}`, {
            method: 'POST',
        });
        let data = {};
        try {
            data = await response.json();
        } catch (_) {
            data = {};
        }

        if (data.code === 'WORKSHOP_CHARACTER_ALREADY_EXISTS') {
            const namesText = formatWorkshopCharacterNameList(data.existing_character_names);
            const message = window.t
                ? window.t('steam.characterCardAlreadyExistsMessage', { names: namesText })
                : `角色卡已存在：${namesText}`;
            await showWorkshopCharacterAddAlert(message, 'info');
            return;
        }

        if (!response.ok || !data.success) {
            const fallbackError = data.error || data.message || (window.t ? window.t('common.unknownError') : 'Unknown error');
            const key = data.code === 'WORKSHOP_CHARACTER_NOT_FOUND'
                ? 'steam.workshopCharacterNotFound'
                : 'steam.workshopCharacterAddFailed';
            const message = window.t
                ? window.t(key, { error: fallbackError })
                : (data.code === 'WORKSHOP_CHARACTER_NOT_FOUND'
                    ? '此订阅内容中未找到可加入的角色卡，请确认内容已下载完成。'
                    : `加入角色卡失败: ${fallbackError}`);
            await showWorkshopCharacterAddAlert(message, 'warning');
            return;
        }

        const namesText = formatWorkshopCharacterNameList(data.added_character_names);
        const successMessage = window.t
            ? window.t('steam.workshopCharacterAdded', { names: namesText })
            : `已加入角色卡：${namesText}`;
        showMessage(successMessage, 'success');
        try {
            await loadCharacterCards();
        } catch (refreshError) {
            console.warn('刷新角色卡列表失败:', refreshError);
            const refreshMessage = window.t
                ? window.t('steam.characterCardsRefreshFailed', { error: refreshError.message })
                : `刷新列表失败: ${refreshError.message}`;
            showMessage(refreshMessage, 'warning');
        }
    } catch (error) {
        const message = window.t
            ? window.t('steam.workshopCharacterAddFailed', { error: error.message })
            : `加入角色卡失败: ${error.message}`;
        showMessage(message, 'error');
    } finally {
        button.disabled = false;
        button.textContent = originalText;
    }
}

// 更新分页控件
function updatePagination() {
    const pagination = document.querySelector('.pagination');
    if (!pagination) return;

    const prevBtn = pagination.querySelector('.pagination-btn-wrapper:first-child button');
    const nextBtn = pagination.querySelector('.pagination-btn-wrapper:last-child button');
    const pageInfo = pagination.querySelector('span');

    // 更新页码信息
    if (pageInfo) {
        const options = { currentPage: currentPage, totalPages: totalPages };
        pageInfo.setAttribute('data-i18n-options', JSON.stringify(options));
        pageInfo.textContent = window.t ? window.t('steam.pagination', options) : `${currentPage} / ${totalPages}`;
    }

    // 更新上一页按钮状态
    if (prevBtn) {
        prevBtn.disabled = currentPage <= 1;
    }

    // 更新下一页按钮状态
    if (nextBtn) {
        nextBtn.disabled = currentPage >= totalPages;
    }
}

// 前往上一页
function goToPrevPage() {
    if (currentPage > 1) {
        currentPage--;
        renderSubscriptionsPage();
        updatePagination();
    }
}

// 前往下一页
function goToNextPage() {
    if (currentPage < totalPages) {
        currentPage++;
        renderSubscriptionsPage();
        updatePagination();
    }
}

// 排序订阅物品
function sortSubscriptions(field, order) {
    if (allSubscriptions.length <= 1) return;

    allSubscriptions.sort((a, b) => {
        let aValue, bValue;

        // 根据不同字段获取对应的值
        switch (field) {
            case 'name':
                aValue = (a.title || String(a.publishedFileId || '')).toLowerCase();
                bValue = (b.title || String(b.publishedFileId || '')).toLowerCase();
                break;
            case 'date':
                aValue = a.timeAdded || 0;
                bValue = b.timeAdded || 0;
                break;
            case 'size':
                aValue = a.fileSizeOnDisk || a.fileSize || 0;
                bValue = b.fileSizeOnDisk || b.fileSize || 0;
                break;
            case 'update':
                aValue = a.timeUpdated || 0;
                bValue = b.timeUpdated || 0;
                break;
            default:
                // 默认按名称排序
                aValue = (a.title || String(a.publishedFileId || '')).toLowerCase();
                bValue = (b.title || String(b.publishedFileId || '')).toLowerCase();
        }

        // 处理空值
        if (aValue === undefined || aValue === null) aValue = '';
        if (bValue === undefined || bValue === null) bValue = '';

        // 字符串比较
        if (typeof aValue === 'string') {
            return order === 'asc' ?
                aValue.localeCompare(bValue) :
                bValue.localeCompare(aValue);
        }
        // 数字比较
        return order === 'asc' ?
            (aValue - bValue) :
            (bValue - aValue);
    });
}

// 应用排序
function applySort(sortValue) {
    // 解析排序值
    const [field, order] = sortValue.split('_');

    // 重置到第一页
    currentPage = 1;

    // 应用排序
    sortSubscriptions(field, order);

    // 重新渲染页面
    renderSubscriptionsPage();

    // 更新分页
    updatePagination();
}

// 过滤订阅物品
function filterSubscriptions(searchTerm) {
    // 简单实现过滤功能
    searchTerm = searchTerm.toLowerCase().trim();

    // 保存原始数据
    if (window.originalSubscriptions === undefined) {
        window.originalSubscriptions = [...allSubscriptions];
    }

    // 如果搜索词为空，恢复原始数据
    if (!searchTerm) {
        if (window.originalSubscriptions) {
            allSubscriptions = [...window.originalSubscriptions];
        }
        // 重新应用当前排序
        const sortSelect = document.getElementById('sort-subscription');
        if (sortSelect) {
            applySort(sortSelect.value);
        }
        return;
    }

    // 过滤物品
    let itemsToFilter = window.originalSubscriptions || [...allSubscriptions];
    const filteredItems = itemsToFilter.filter(item => {
        const title = (item.title || '').toLowerCase();
        return title.includes(searchTerm);
    });

    allSubscriptions = filteredItems;

    // 重新计算分页
    totalPages = Math.ceil(allSubscriptions.length / itemsPerPage);
    if (totalPages < 1) totalPages = 1;
    if (currentPage > totalPages) currentPage = totalPages;

    // 渲染过滤后的结果
    renderSubscriptionsPage();
    updatePagination();
}

// 格式化文件大小
function formatFileSize(bytes) {
    if (bytes === 0 || bytes === undefined) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

// 获取状态文本
function getStatusText(state) {
    if (state.downloading) {
        return window.t ? window.t('steam.status.downloading') : '下载中';
    } else if (state.needsUpdate) {
        return window.t ? window.t('steam.status.needsUpdate') : '需要更新';
    } else if (state.installed) {
        return window.t ? window.t('steam.status.installed') : '已安装';
    } else if (state.subscribed) {
        return window.t ? window.t('steam.status.subscribed') : '已订阅';
    } else {
        return window.t ? window.t('steam.status.unknown') : '未知';
    }
}

// 打开模态框
function openModal() {
    const modal = document.getElementById('itemDetailsModal');
    modal.style.display = 'flex';
    // 阻止页面滚动
    document.documentElement.style.overflowY = 'hidden';
}

// 关闭模态框
function closeModal() {
    const modal = document.getElementById('itemDetailsModal');
    modal.style.display = 'none';
    // 恢复页面滚动
    document.documentElement.style.overflowY = '';
}

// 点击模态框外部关闭
function closeModalOnOutsideClick(event) {
    const modal = document.getElementById('itemDetailsModal');
    if (event.target === modal) {
        closeModal();
    }
}


// 查看物品详情
function viewItemDetails(itemId) {
    // 显示加载消息
    showMessage(window.t ? window.t('steam.loadingItemDetailsById', { id: itemId }) : `正在加载物品ID: ${itemId} 的详细信息...`, 'success');

    // 调用后端API获取物品详情
    fetch(`/api/steam/workshop/item/${itemId}`)
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            return response.json();
        })
        .then(data => {
            if (!data.success) {
                showMessage(window.t ? window.t('steam.getItemDetailsFailedWithError', { error: data.error || (window.t ? window.t('common.unknownError') : '未知错误') }) : `获取物品详情失败: ${data.error || '未知错误'}`, 'error');
                return;
            }

            const item = data.item;
            const formattedItem = {
                id: item.publishedFileId.toString(),
                name: item.title,
                author: escapeHtml(safeAuthorName(item)),
                rawAuthor: safeAuthorName(item),
                subscribedDate: new Date(item.timeAdded * 1000).toLocaleDateString(),
                lastUpdated: new Date(item.timeUpdated * 1000).toLocaleDateString(),
                size: formatFileSize(item.fileSize),
                previewUrl: item.previewUrl || item.previewImageUrl || '../static/icons/Steam_icon_logo.png',
                description: escapeHtml(item.description || (window.t ? window.t('steam.noDescription') : '暂无描述')),
                downloadCount: 'N/A',
                rating: 'N/A',
                tags: [window.t ? window.t('steam.defaultTagMod') : '模组'], // 默认标签，实际应用中应该从API获取
                state: item.state || {} // 添加state属性，确保后续代码可以正常访问
            };

            // 确定状态类和文本
            let statusClass = 'status-subscribed';
            let statusText = getStatusText(formattedItem.state || {});

            if (formattedItem.state && formattedItem.state.downloading) {
                statusClass = 'status-downloading';
            } else if (formattedItem.state && formattedItem.state.needsUpdate) {
                statusClass = 'status-needs-update';
            } else if (formattedItem.state && formattedItem.state.installed) {
                statusClass = 'status-installed';
            }

            // 获取作者头像（使用首字母作为占位符）
            const authorInitial = escapeHtml(String(formattedItem.rawAuthor).substring(0, 2).toUpperCase());

            // 更新模态框内容
            document.getElementById('modalTitle').textContent = formattedItem.name;

            const detailContent = document.getElementById('itemDetailContent');
            detailContent.innerHTML = `
            <img src="${formattedItem.previewUrl}" alt="${formattedItem.name}" class="item-preview-large" onerror="this.src='../static/icons/Steam_icon_logo.png'">

            <div class="item-info-grid">
                <p class="item-info-item">
                    <span class="item-info-label">${window.t ? window.t('steam.author') : '作者:'}</span>
                    <div class="author-info">
                        <div class="author-avatar">${authorInitial}</div>
                        <span>${formattedItem.author}</span>
                    </div>
                </p>
                <p class="item-info-item"><span class="item-info-label">${window.t ? window.t('steam.subscribed_date') : '订阅日期:'}</span> ${formattedItem.subscribedDate}</p>
                <p class="item-info-item"><span class="item-info-label">${window.t ? window.t('steam.last_updated') : '上次更新:'}</span> ${formattedItem.lastUpdated}</p>
                <p class="item-info-item"><span class="item-info-label">${window.t ? window.t('steam.size') : '大小:'}</span> ${formattedItem.size}</p>
                <p class="item-info-item">
                    <span class="item-info-label">${window.t ? window.t('steam.status_label') : '状态:'}</span>
                    <span class="status-badge ${statusClass}">${statusText}</span>
                </p>
                <p class="item-info-item"><span class="item-info-label">${window.t ? window.t('steam.download_count') : '下载次数:'}</span> ${formattedItem.downloadCount}</p>
                ${formattedItem.state && formattedItem.state.downloading && item.downloadProgress ?
                    `<p class="item-info-item" style="grid-column: span 2;">
                        <div class="download-progress">
                            <div class="progress-bar">
                                <div class="progress-fill" style="width: ${item.downloadProgress.percentage}%">
                                    ${item.downloadProgress.percentage.toFixed(1)}%
                                </div>
                            </div>
                        </div>
                    </p>` : ''
                }
            </div>

            <div>
                <h4>${window.t ? window.t('steam.tags') : '标签'}</h4>
                <div class="tags-container">
                    ${formattedItem.tags.map(tag => `
                        <div class="tag">${tag}</div>
                    `).join('')}
                </div>
            </div>

            <div>
                <h4>${window.t ? window.t('steam.description') : '描述'}</h4>
                <p class="item-description">${formattedItem.description}</p>
            </div>
        `;

            // 打开模态框
            openModal();
        })
        .catch(error => {
            console.error('获取物品详情失败:', error);
            showMessage(window.t ? window.t('steam.cannotLoadItemDetails') : '无法加载物品详情', 'error');
        });
}

// 取消订阅功能
function unsubscribeItem(itemId, itemName) {
    if (!confirm(window.t ? window.t('steam.unsubscribeConfirm', { name: itemName }) : `确定要取消订阅 "${itemName}" 吗？`)) {
        return;
    }

    // 查找当前卡片并添加移除动画效果（用于回滚）
    let pendingCard = null;
    const cards = document.querySelectorAll('.workshop-card');
    for (let card of cards) {
        const cardTitleEl = card.querySelector('.card-title');
        if (cardTitleEl && cardTitleEl.textContent === itemName) {
            pendingCard = card;
            card.style.opacity = '0.6';
            card.style.transform = 'scale(0.95)';
            break;
        }
    }

    const restoreCard = () => {
        if (pendingCard) {
            pendingCard.style.opacity = '';
            pendingCard.style.transform = '';
        }
    };

    // 调用后端API执行取消订阅操作
    showMessage(window.t ? window.t('steam.cancellingSubscription', { name: itemName }) : `Cancelling subscription to "${itemName}"...`, 'success');

    fetch('/api/steam/workshop/unsubscribe', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({ item_id: itemId })
    })
        .then(async response => {
            // 统一解析响应体，即使非 2xx 也尝试读取 JSON，以便展示后端的 error/message
            let data = null;
            try {
                data = await response.json();
            } catch (_) {
                data = null;
            }
            return { response, data };
        })
        .then(({ response, data }) => {
            // 诊断日志：只记录状态/计数，避免把 cleanup_summary 里的本地路径 /
            // 角色名直接落到浏览器或 Electron 日志里，泄露用户信息。
            const summaryForLog = data && data.cleanup_summary ? data.cleanup_summary : {};
            console.info('[unsubscribe response]', {
                status: response.status,
                ok: response.ok,
                success: !!(data && data.success),
                code: data && data.code,
                status_text: data && data.status,
                has_cleanup_summary: !!(data && data.cleanup_summary),
                cleaned_count: Array.isArray(summaryForLog.cleaned_characters) ? summaryForLog.cleaned_characters.length : 0,
                removed_memory_count: Array.isArray(summaryForLog.removed_memory_paths) ? summaryForLog.removed_memory_paths.length : 0,
                error_count: Array.isArray(summaryForLog.errors) ? summaryForLog.errors.length : 0,
            });
            if (!response.ok) {
                // 后端前置校验失败：按 code 映射到本地化 key，避免把后端
                // 硬编码的中文 error 文案直接甩给英文/繁中用户。
                const code = data && data.code;
                if (code === 'CURRENT_CATGIRL_IN_USE') {
                    const characterName = data.character_name || itemName;
                    const blockedMsg = (window.t ? window.t('steam.unsubscribeCurrentCatgirlBlocked', { name: characterName }) : '') || data.error || `不能取消订阅当前正在使用的猫娘「${characterName}」，请先切换到其他角色后再取消订阅。`;
                    // 优先使用 toast；同时用 alert 兜底，确保在 toast 被其它高层 overlay
                    // 遮挡时用户仍能看到阻断原因（这是阻断性 action，用户必须知情）
                    showMessage(blockedMsg, 'warning', 6000);
                    try { window.alert(blockedMsg); } catch (_) { /* 忽略 alert 被禁用 */ }
                    restoreCard();
                    return;
                }
                if (code === 'LOCAL_CONFIG_CLEANUP_FAILED') {
                    const msg = (window.t ? window.t('steam.unsubscribeLocalConfigCleanupFailed') : '') || data.error || '本地角色配置清理失败，已取消本次 Steam 退订请求，请修复后重试。';
                    showMessage(msg, 'error', 8000);
                    try { window.alert(msg); } catch (_) { /* ignore */ }
                    restoreCard();
                    return;
                }
                if (code === 'STEAM_UNSUBSCRIBE_FAILED') {
                    const detail = (data && data.error) || `HTTP ${response.status}`;
                    const msg = (window.t ? window.t('steam.unsubscribeSteamRequestFailed', { error: detail }) : '') || `Steam 退订请求发送失败: ${detail}`;
                    showMessage(msg, 'error', 8000);
                    restoreCard();
                    return;
                }
                const errorMsg = (data && (data.error || data.message)) || `HTTP ${response.status}`;
                showMessage(window.t ? window.t('steam.unsubscribeFailed', { error: errorMsg }) : `取消订阅失败: ${errorMsg}`, 'error');
                restoreCard();
                return;
            }

            if (data && data.success) {
                // 显示异步操作状态
                let statusMessage = window.t ? window.t('steam.unsubscribeAccepted', { name: itemName }) : `已接受取消订阅: ${itemName}`;
                if (data.status === 'accepted') {
                    statusMessage = window.t ? window.t('steam.unsubscribeProcessing', { name: itemName }) : `正在处理取消订阅: ${itemName}`;
                }
                showMessage(statusMessage, 'success');

                // 同步清理汇总：让用户直接看到"角色卡和记忆删了多少"（诊断价值）
                const summary = data.cleanup_summary || {};
                const cleanedChars = Array.isArray(summary.cleaned_characters) ? summary.cleaned_characters : [];
                const removedPaths = Array.isArray(summary.removed_memory_paths) ? summary.removed_memory_paths : [];
                const errors = Array.isArray(summary.errors) ? summary.errors : [];

                if (cleanedChars.length > 0 || removedPaths.length > 0) {
                    const charactersStr = cleanedChars.join('、') || '-';
                    const detailMsg = (window.t ? window.t('steam.unsubscribeCleanupDetail', {
                        characterCount: cleanedChars.length,
                        characters: charactersStr,
                        memoryPathCount: removedPaths.length,
                    }) : '') || `已清理角色卡: ${cleanedChars.length} 个（${charactersStr}）；已删除记忆路径: ${removedPaths.length} 条`;
                    showMessage(detailMsg, 'success', 6000);
                    // 只记录计数，避免 removed_memory_paths 里的本地路径被日志收集
                    console.info('[unsubscribe cleanup summary]', {
                        cleaned_count: cleanedChars.length,
                        removed_memory_count: removedPaths.length,
                        error_count: errors.length,
                    });
                } else if ((summary.candidate_characters || []).length === 0) {
                    // 后端没在 characters.json 中找到关联角色（反向索引空 + 磁盘扫描空）
                    console.warn('[unsubscribe] 未找到与该物品关联的角色，仅删除订阅文件夹');
                    const noAssocMsg = (window.t && window.t('steam.unsubscribeNoAssociation')) || '未找到与此订阅关联的角色，仅删除了订阅文件夹；若有残留记忆请手动处理';
                    showMessage(noAssocMsg, 'warning', 6000);
                }
                if (errors.length > 0) {
                    // 只记录数量和 stage，避免 error.error 里的路径 / 角色名泄露
                    console.warn('[unsubscribe cleanup errors]', {
                        count: errors.length,
                        stages: errors.map((e) => e && e.stage).filter(Boolean),
                    });
                    const firstErr = errors[0] || {};
                    const errMsg = (window.t ? window.t('steam.unsubscribeCleanupErrors', {
                        count: errors.length,
                        stage: firstErr.stage || '',
                        error: firstErr.error || '',
                    }) : '') || `清理过程出现 ${errors.length} 个错误，首个: ${firstErr.stage || ''} -> ${firstErr.error || ''}`;
                    showMessage(errMsg, 'warning', 8000);
                    try { window.alert(errMsg); } catch (_) { /* ignore */ }
                }

                // 乐观更新：立即在本地列表里剔除该条目，UI 无需等 Steam 回调即可看到
                // "已消失"的视觉反馈。即便 Steam 端还没完成剔除（后端 /subscribed-items
                // 仍可能短暂返回它），下一次 loadSubscriptions 会用后端数据覆盖。
                try {
                    if (Array.isArray(allSubscriptions)) {
                        const before = allSubscriptions.length;
                        allSubscriptions = allSubscriptions.filter(
                            (item) => String(item && item.publishedFileId) !== String(itemId)
                        );
                        if (allSubscriptions.length !== before) {
                            totalPages = Math.max(1, Math.ceil(allSubscriptions.length / itemsPerPage));
                            if (currentPage > totalPages) currentPage = totalPages;
                            renderSubscriptionsPage();
                            updatePagination();
                        }
                    }
                } catch (optErr) {
                    console.warn('[unsubscribe] 乐观更新失败，将依赖下一次 loadSubscriptions:', optErr);
                }

                // accepted 表示 Steam/后端取消订阅还在异步收敛；立即 loadSubscriptions
                // 会把刚刚乐观剔除的卡片重新拉回来。延迟一次，等 Steam 端完成剔除后再刷。
                // 其它状态（同步完成）直接刷新即可。
                if (data.status === 'accepted') {
                    setTimeout(loadSubscriptions, 1500);
                } else {
                    loadSubscriptions();
                }
            } else {
                const errorMsg = (data && (data.error || data.message)) || (window.t ? window.t('common.unknownError') : '未知错误');
                showMessage(window.t ? window.t('steam.unsubscribeFailed', { error: errorMsg }) : `取消订阅失败: ${errorMsg}`, 'error');
                restoreCard();
            }
        })
        .catch(error => {
            console.error('取消订阅失败:', error);
            showMessage(window.t ? window.t('steam.unsubscribeError') : '取消订阅失败', 'error');
            restoreCard();
        });
}

// 全局变量：存储所有可用模型信息
let availableModels = [];
// VRM/MMD 模型列表
let availableVrmModels = [];
let availableMmdModels = [];

// 自动扫描创意工坊角色卡并添加到系统（仅同步角色卡，不再自动注册参考语音）
async function autoScanAndAddWorkshopCharacterCards() {
    try {
        try {
            const syncResponse = await fetch('/api/steam/workshop/sync-characters', { method: 'POST' });
            if (!syncResponse.ok) {
                console.error(`[工坊同步] 服务端返回错误: HTTP ${syncResponse.status} ${syncResponse.statusText}`);
            } else {
                const syncResult = await syncResponse.json();
                if (syncResult.success) {
                    const backfilledFaces = Number(syncResult.backfilled_faces || 0);
                    if (syncResult.added > 0 || backfilledFaces > 0) {
                        console.log(`[工坊同步] 服务端同步完成：新增 ${syncResult.added} 个角色卡，回填 ${backfilledFaces} 个封面，跳过 ${syncResult.skipped} 个已存在`);
                        // 刷新角色卡列表
                        loadCharacterCards();
                    } else {
                        console.log('[工坊同步] 服务端同步完成：无新增角色卡');
                    }
                } else {
                    console.error(`[工坊同步] 服务端同步失败: ${syncResult.error || '未知错误'}`, syncResult);
                }
            }
        } catch (syncError) {
            console.error('[工坊同步] 服务端角色卡同步请求失败:', syncError);
        }
    } catch (error) {
        console.error('自动扫描和添加角色卡失败:', error);
    }
}

// 扫描单个角色卡文件
async function scanCharaFile(filePath, itemId, itemTitle) {
    try {
        await ensureReservedFieldsLoaded();
        // 使用新的read-file API读取文件内容
        const readResponse = await fetch(`/api/steam/workshop/read-file?path=${encodeURIComponent(filePath)}`);
        const readResult = await readResponse.json();

        if (readResult.success) {
            // 解析文件内容
            const charaData = JSON.parse(readResult.content);

            // 档案名是必需字段，用作 characters.json 中的 key
            if (!charaData['档案名']) {
                return;
            }

            const charaName = charaData['档案名'];

            // 工坊保留字段 - 这些字段不应该从外部角色卡数据中读取
            // description/tags 及其中文版本是工坊上传时自动生成的，不属于角色卡原始数据
            // live2d_item_id 是系统自动管理的，不应该从外部数据读取
            const RESERVED_FIELDS = getWorkshopReservedFields();

            // 转换为符合catgirl API格式的数据（不包含保留字段）
            const catgirlFormat = {
                '档案名': charaName
            };

            // 跳过的字段：档案名（已处理）、保留字段
            const skipKeys = ['档案名', ...RESERVED_FIELDS];

            const fieldOrder = [];
            // 工坊导入要保留 live2d/model_type/vrm 等模型字段（仅靠 skipKeys 过滤工坊元数据），
            // 不能套用渲染路径对系统保留名的剔除，否则导入卡会丢失模型绑定、开成错误或缺失的模型。
            getOrderedCharacterFieldKeys(charaData, skipKeys, { skipReservedNames: false }).forEach(key => {
                const value = charaData[key];
                if (value !== undefined && value !== null && value !== '') {
                    catgirlFormat[key] = value;
                    fieldOrder.push(key);
                }
            });
            attachCharacterFieldOrderPayload(catgirlFormat, fieldOrder);

            // 重要：如果角色卡有 live2d 字段，需要同时保存 live2d_item_id
            // 这样首页加载时才能正确构建工坊模型的路径
            if (catgirlFormat['live2d'] && itemId) {
                catgirlFormat['live2d_item_id'] = String(itemId);
            }

            // 调用catgirl API添加到系统
            const addResponse = await fetch('/api/characters/catgirl', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(catgirlFormat)
            });

            const addResult = await addResponse.json();

            if (addResult.success) {
                // 延迟刷新角色卡列表，确保数据已保存
                setTimeout(() => {
                    loadCharacterCards();
                }, 500);
            } else {
                const errorMsg = `角色卡 ${charaName} 已存在或添加失败: ${addResult.error}`;
                console.log(errorMsg);
                showMessage(errorMsg, 'warning');
            }
        } else if (readResult.error !== '文件不存在') {
            console.error(`读取角色卡文件 ${filePath} 失败:`, readResult.error);
        }
    } catch (error) {
        if (error.message !== 'Failed to fetch') {
            console.error(`处理角色卡文件 ${filePath} 时出错:`, error);
        }
    }
}

// 检查Steam状态，未运行时弹窗提醒
async function checkSteamStatus() {
    try {
        const response = await fetch('/api/steam/workshop/status');
        if (!response.ok) return;
        const data = await response.json();
        if (data.success && !data.steamworks_initialized) {
            const title = window.t ? window.t('steam.steamNotRunningTitle') : 'Steam 未运行';
            const message = window.t ? window.t('steam.steamNotRunningMessage') : '检测到Steam客户端未运行或未登录。\n\n创意工坊功能需要Steam客户端支持，请：\n1. 下载并安装Steam客户端\n2. 启动Steam并登录您的账号\n3. 重新打开此页面';
            showAlert(message, title);
        }
    } catch (e) {
        console.error('Steam status check failed:', e);
    }
}

// 初始化页面
window.addEventListener('load', function () {
    // 检查是否需要切换到特定标签页
    const lastActiveTab = localStorage.getItem('lastActiveTab');
    if (lastActiveTab) {
        switchTab(lastActiveTab);
        // 清除存储的标签页信息
        localStorage.removeItem('lastActiveTab');
    }

    // 标签仅从后端读取，不提供手动添加功能
    // addCharacterCardTag('character-card', window.t ? window.t('steam.defaultTagCharacter') : 'Character');

    // 初始化i18n文本
    if (document.getElementById('loading-text')) {
        document.getElementById('loading-text').textContent = window.t ? window.t('steam.loadingSubscriptions') : '正在加载您的订阅物品...';
    }
    if (document.getElementById('reload-button')) {
        document.getElementById('reload-button').textContent = window.t ? window.t('steam.reload') : '重新加载';
    }
    if (document.getElementById('search-subscription')) {
        document.getElementById('search-subscription').placeholder = window.t ? window.t('steam.searchPlaceholder') : '搜索订阅内容...';
    }
    updateReferenceAudioDisplay();

    // 页面加载时自动加载订阅内容
    loadSubscriptions();

    // 页面加载时自动加载角色卡
    loadCharacterCards();

    // 页面加载时自动扫描创意工坊角色卡并添加到系统
    autoScanAndAddWorkshopCharacterCards();

    // 监听语言变化事件，刷新当前页面显示
    // 仅使用 localechange，因为 i18next languageChanged 已会触发 localechange
    function updateLocaleDependent() {
        loadSubscriptions();
        syncTitleDataText();
    }
    updateLocaleDependent();
    window.addEventListener('localechange', updateLocaleDependent);

});

// 角色卡相关函数

// 同步标题 data-text 属性（i18n 更新后伪元素需要同步）
function syncTitleDataText() {
    const titleH2 = document.querySelector('.page-title-bar h2');
    if (titleH2) {
        titleH2.setAttribute('data-text', titleH2.textContent);
    }
}

// 加载角色卡列表
// 加载角色卡数据
async function loadCharacterData() {
    try {
        const resp = await fetch('/api/characters', { cache: 'no-store' });
        if (!resp.ok) {
            throw new Error(`HTTP ${resp.status}`);
        }
        return await resp.json();
    } catch (error) {
        console.error('加载角色数据失败:', error);
        showMessage(window.t ? window.t('steam.loadCharacterDataFailed', { error: error.message || String(error) }) : '加载角色数据失败', 'error');
        return null;
    }
}

// 全局变量：角色卡列表
