// 允许的来源列表
const ALLOWED_ORIGINS = [window.location.origin];

// 关闭页面函数
function closeVoiceClonePage() {
    if (window.opener) {
        // 如果是通过 window.open() 打开的，直接关闭
        window.close();
    } else if (window.parent && window.parent !== window) {
        // 如果在 iframe 中，通知父窗口关闭
        window.parent.postMessage({ type: 'close_voice_clone' }, window.location.origin);
    } else {
        // 否则尝试关闭窗口
        // 注意：如果是用户直接访问的页面，浏览器可能不允许关闭
        // 在这种情况下，可以尝试返回上一页或显示提示
        if (window.history.length > 1) {
            window.history.back();
        } else {
            window.close();
            // 如果 window.close() 失败（页面仍然存在），可以显示提示
            setTimeout(() => {
                if (!window.closed) {
                    // 窗口未能关闭，返回主页
                    window.location.href = '/';
                }
            }, 100);
        }
    }
}

// 更新文件选择显示
function updateFileDisplay() {
    const fileInput = document.getElementById('audioFile');
    const fileNameDisplay = document.getElementById('fileNameDisplay');
    if (!fileInput || !fileNameDisplay) {
        return; // 如果元素不存在，直接返回
    }
    if (fileInput.files.length > 0) {
        fileNameDisplay.textContent = fileInput.files[0].name;
    } else {
        fileNameDisplay.textContent = window.t ? window.t('voice.noFileSelected') : '未选择文件';
    }
}

// 监听文件选择变化
document.addEventListener('DOMContentLoaded', () => {
    const audioFile = document.getElementById('audioFile');
    if (audioFile) {
        audioFile.addEventListener('change', updateFileDisplay);
    } else {
        console.error('未找到 audioFile 元素');
    }
});

// 更新文件选择按钮的 data-text 属性（用于文字描边效果）
function updateFileButtonText() {
    const fileText = document.querySelector('.file-text');
    if (fileText) {
        const text = fileText.textContent || fileText.innerText;
        fileText.setAttribute('data-text', text);
    }
}

// 更新注册音色按钮的 data-text 属性（用于文字描边效果）
function updateRegisterButtonText() {
    const registerText = document.querySelector('.register-text');
    if (registerText) {
        const text = registerText.textContent || registerText.innerText;
        registerText.setAttribute('data-text', text);
    }
}

// 监听 i18n 更新事件，同步更新 data-text
if (window.i18n) {
    window.i18n.on('languageChanged', function () {
        updateFileButtonText();
        updateRegisterButtonText();
    });
    // 监听所有翻译更新
    const originalChangeLanguage = window.i18n.changeLanguage;
    if (originalChangeLanguage) {
        window.i18n.changeLanguage = function (...args) {
            const result = originalChangeLanguage.apply(this, args);
            if (result && typeof result.then === 'function') {
                result.then(() => {
                    setTimeout(() => {
                        updateFileButtonText();
                        updateRegisterButtonText();
                    }, 100);
                });
            } else {
                setTimeout(() => {
                    updateFileButtonText();
                    updateRegisterButtonText();
                }, 100);
            }
            return result;
        };
    }
}

// 使用 MutationObserver 监听文字内容变化
const fileTextObserver = new MutationObserver(function (mutations) {
    mutations.forEach(function (mutation) {
        if (mutation.type === 'childList' || mutation.type === 'characterData') {
            updateFileButtonText();
        }
    });
});

const registerTextObserver = new MutationObserver(function (mutations) {
    mutations.forEach(function (mutation) {
        if (mutation.type === 'childList' || mutation.type === 'characterData') {
            updateRegisterButtonText();
        }
    });
});

// 页面加载时更新文件选择显示
// 如果 i18next 已经初始化完成，立即更新
if (window.i18n && window.i18n.isInitialized) {
    updateFileDisplay();
    updateFileButtonText();
    updateRegisterButtonText();
    const fileText = document.querySelector('.file-text');
    if (fileText) {
        fileTextObserver.observe(fileText, {
            childList: true,
            characterData: true,
            subtree: true
        });
    }
    const registerText = document.querySelector('.register-text');
    if (registerText) {
        registerTextObserver.observe(registerText, {
            childList: true,
            characterData: true,
            subtree: true
        });
    }
} else {
    // 延迟更新，等待 i18next 初始化
    setTimeout(() => {
        updateFileDisplay();
        updateFileButtonText();
        updateRegisterButtonText();
        const fileText = document.querySelector('.file-text');
        if (fileText) {
            fileTextObserver.observe(fileText, {
                childList: true,
                characterData: true,
                subtree: true
            });
        }
        const registerText = document.querySelector('.register-text');
        if (registerText) {
            registerTextObserver.observe(registerText, {
                childList: true,
                characterData: true,
                subtree: true
            });
        }
    }, 500);
}

// 页面加载时获取 lanlan_name
(async function initLanlanName() {
    // Electron白屏修复
    if (document.body) {
        void document.body.offsetHeight;
        const currentOpacity = document.body.style.opacity || '1';
        document.body.style.opacity = '0.99';
        requestAnimationFrame(() => {
            document.body.style.opacity = currentOpacity;
        });
    }

    const lanlanInput = document.getElementById('lanlan_name');

    try {
        // 优先从 URL 获取 lanlan_name
        const urlParams = new URLSearchParams(window.location.search);
        let lanlanName = urlParams.get('lanlan_name') || "";

        // 如果 URL 中没有，从 API 获取
        if (!lanlanName) {
            const response = await fetch('/api/config/page_config');
            if (!response.ok) {
                throw new Error(`API returned ${response.status}`);
            }
            const data = await response.json();
            if (data.success) {
                lanlanName = data.lanlan_name || "";
            }
        }

        // 设置到隐藏字段
        if (lanlanInput) {
            lanlanInput.value = lanlanName;
        }
    } catch (error) {
        console.error('获取 lanlan_name 失败:', error);
        if (lanlanInput) {
            lanlanInput.value = "";
        }
    }
})();

function setFormDisabled(disabled) {
    const audioFile = document.getElementById('audioFile');
    const refLanguage = document.getElementById('refLanguage');
    const prefix = document.getElementById('prefix');
    if (audioFile) audioFile.disabled = disabled;
    if (refLanguage) refLanguage.disabled = disabled;
    if (prefix) prefix.disabled = disabled;
    // 禁用所有按钮
    const buttons = document.querySelectorAll('button');
    if (buttons && buttons.length > 0) {
        buttons.forEach(btn => {
            if (btn) btn.disabled = disabled;
        });
    }
}

function registerVoice() {
    const fileInput = document.getElementById('audioFile');
    const refLanguage = document.getElementById('refLanguage').value;
    const prefix = document.getElementById('prefix').value.trim();
    const resultDiv = document.getElementById('result');

    // 清空现有内容并重置类名
    resultDiv.textContent = '';
    resultDiv.className = 'result';

    if (!fileInput.files.length || !prefix) {
        resultDiv.textContent = window.t ? window.t('voice.pleaseUploadFile') : '请上传音频文件并填写前缀';
        resultDiv.className = 'result error';
        return;
    }
    setFormDisabled(true);
    resultDiv.textContent = window.t ? window.t('voice.registering') : '正在注册声音，请稍后！';
    resultDiv.className = 'result';
    const formData = new FormData();
    formData.append('file', fileInput.files[0]);
    formData.append('ref_language', refLanguage);
    formData.append('prefix', prefix);
    fetch('/api/characters/voice_clone', {
        method: 'POST',
        body: formData
    })
        .then(async res => {
            const data = await res.json();
            if (!res.ok) {
                // 从响应体中提取详细错误信息
                const errorMsg = data.error || data.detail || `API returned ${res.status}`;
                throw new Error(errorMsg);
            }
            return data;
        })
        .then(data => {
            if (data.voice_id) {
                resultDiv.textContent = window.t ? window.t('voice.registerSuccess', { voiceId: data.voice_id }) : '注册成功！voice_id: ' + data.voice_id;
                // 刷新音色列表
                setTimeout(() => {
                    if (typeof loadVoices === 'function') {
                        loadVoices();
                    }
                }, 1000);
                // 自动更新voice_id到后端
                const lanlanName = document.getElementById('lanlan_name').value;
                if (lanlanName) {
                    fetch(`/api/characters/catgirl/voice_id/${encodeURIComponent(lanlanName)}`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ voice_id: data.voice_id })
                    }).then(resp => {
                        if (!resp.ok) {
                            throw new Error(`API returned ${resp.status}`);
                        }
                        return resp.json();
                    }).then(res => {
                        if (!res.success) {
                            const errorMsg = res.error || (window.t ? window.t('common.unknownError') : '未知错误');
                            const errorSpan = document.createElement('span');
                            errorSpan.className = 'error';
                            errorSpan.textContent = (window.t ? window.t('voice.voiceIdSaveFailed', { error: errorMsg }) : 'voice_id自动保存失败: ' + errorMsg);
                            resultDiv.appendChild(document.createElement('br'));
                            resultDiv.appendChild(errorSpan);
                        } else {
                            const successMsg = document.createElement('span');
                            successMsg.textContent = (window.t ? window.t('voice.voiceIdSaved') : 'voice_id已自动保存到角色');
                            resultDiv.appendChild(document.createElement('br'));
                            resultDiv.appendChild(successMsg);

                            // 如果session被结束，页面会自动刷新
                            const statusSpan = document.createElement('span');
                            statusSpan.style.color = 'blue';
                            if (res.session_restarted) {
                                statusSpan.textContent = (window.t ? window.t('voice.pageWillRefresh') : '当前页面即将自动刷新以应用新语音');
                            } else {
                                statusSpan.textContent = (window.t ? window.t('voice.voiceWillTakeEffect') : '新语音将在下次对话时生效');
                            }
                            resultDiv.appendChild(document.createElement('br'));
                            resultDiv.appendChild(statusSpan);

                            // 通知父页面voice_id已更新
                            const payload = { type: 'voice_id_updated', voice_id: data.voice_id, lanlan_name: lanlanName, session_restarted: res.session_restarted };
                            if (window.parent !== window) {
                                try { window.parent.postMessage(payload, window.location.origin); } catch (e) {}
                            }
                            if (window.opener && !window.opener.closed) {
                                try { window.opener.postMessage(payload, window.location.origin); } catch (e) {}
                            }
                        }
                    }).catch(e => {
                        const errorSpan = document.createElement('span');
                        errorSpan.className = 'error';
                        errorSpan.textContent = (window.t ? window.t('voice.voiceIdSaveRequestError') : 'voice_id自动保存请求出错');
                        resultDiv.appendChild(document.createElement('br'));
                        resultDiv.appendChild(errorSpan);
                    });
                }
            } else {
                const errorMsg = data.error || (window.t ? window.t('common.unknownError') : '未知错误');
                resultDiv.textContent = window.t ? window.t('voice.registerFailed', { error: errorMsg }) : '注册失败：' + errorMsg;
                resultDiv.className = 'result error';
            }
            setFormDisabled(false);
        })
        .catch(err => {
            const errorMsg = err?.message || err?.toString() || (window.t ? window.t('common.unknownError') : '未知错误');
            resultDiv.textContent = window.t ? window.t('voice.requestError', { error: errorMsg }) : '请求出错：' + errorMsg;
            resultDiv.className = 'result error';
            setFormDisabled(false);
        });
}

// 监听API Key变更事件
window.addEventListener('message', function (event) {
    if (!ALLOWED_ORIGINS.includes(event.origin)) return;
    if (event.data.type === 'api_key_changed') {
        // API Key已更改，可以在这里添加其他需要的处理逻辑
        console.log('API Key已更改，音色注册页面已收到通知');
        // 刷新音色列表
        loadVoices();
    }
});

// 加载音色列表
async function loadVoices() {
    const container = document.getElementById('voice-list-container');
    const refreshBtn = document.getElementById('refresh-voices-btn');

    if (!container) return;

    // 显示加载状态
    const loadingText = window.t ? window.t('voice.loading') : '加载中...';
    container.textContent = '';
    const loadingDiv = document.createElement('div');
    loadingDiv.style.textAlign = 'center';
    loadingDiv.style.color = '#999';
    loadingDiv.style.padding = '20px';
    loadingDiv.id = 'voice-list-loading';
    const loadingSpan = document.createElement('span');
    loadingSpan.textContent = loadingText;
    loadingDiv.appendChild(loadingSpan);
    container.appendChild(loadingDiv);

    if (refreshBtn) refreshBtn.disabled = true;

    try {
        const response = await fetch('/api/characters/voices');
        if (!response.ok) {
            throw new Error(`API returned ${response.status}`);
        }
        const data = await response.json();

        if (!data.voices || Object.keys(data.voices).length === 0) {
            const noVoicesText = window.t ? window.t('voice.noVoices') : '暂无已注册音色';
            container.textContent = '';
            const emptyDiv = document.createElement('div');
            emptyDiv.className = 'voice-list-empty';
            const emptySpan = document.createElement('span');
            emptySpan.textContent = noVoicesText;
            emptyDiv.appendChild(emptySpan);
            container.appendChild(emptyDiv);
            return;
        }

        // 清空容器
        container.textContent = '';

        // 按创建时间排序（如果有）
        const voicesArray = Object.entries(data.voices).map(([voiceId, voiceData]) => ({
            voiceId,
            ...voiceData
        }));

        // 如果有创建时间，按时间倒序排列
        voicesArray.sort((a, b) => {
            if (a.created_at && b.created_at) {
                return new Date(b.created_at) - new Date(a.created_at);
            }
            return 0;
        });

        // 创建音色列表项
        voicesArray.forEach(({ voiceId, prefix, created_at }) => {
            const item = document.createElement('div');
            item.className = 'voice-list-item';

            const voiceName = prefix || voiceId;
            const displayName = voiceName.length > 30 ? voiceName.substring(0, 30) + '...' : voiceName;

            let dateStr = '';
            if (created_at) {
                try {
                    const date = new Date(created_at);
                    // 使用 i18n locale，回退到 navigator.language，最后回退到 'en-US'
                    const locale = (window.i18n && window.i18n.language) || navigator.language || 'en-US';
                    dateStr = date.toLocaleString(locale, {
                        year: 'numeric',
                        month: '2-digit',
                        day: '2-digit',
                        hour: '2-digit',
                        minute: '2-digit'
                    });
                } catch (e) {
                    // 忽略日期解析错误
                }
            }

            const deleteBtn = document.createElement('button');
            deleteBtn.className = 'voice-delete-btn';
            const deleteText = window.t ? window.t('voice.delete') : '删除';
            const deleteImg = document.createElement('img');
            deleteImg.src = '/static/icons/delete.png';
            deleteImg.alt = '';
            deleteBtn.appendChild(deleteImg);
            deleteBtn.appendChild(document.createTextNode(deleteText));
            deleteBtn.onclick = () => deleteVoice(voiceId, displayName);

            const infoDiv = document.createElement('div');
            infoDiv.className = 'voice-info';

            const nameDiv = document.createElement('div');
            nameDiv.className = 'voice-name';
            nameDiv.textContent = displayName;
            infoDiv.appendChild(nameDiv);

            const idDiv = document.createElement('div');
            idDiv.className = 'voice-id';
            idDiv.textContent = `ID: ${voiceId}`;
            infoDiv.appendChild(idDiv);

            if (dateStr) {
                const dateDiv = document.createElement('div');
                dateDiv.className = 'voice-date';
                dateDiv.textContent = dateStr;
                infoDiv.appendChild(dateDiv);
            }

            item.appendChild(infoDiv);
            item.appendChild(deleteBtn);

            container.appendChild(item);
        });

    } catch (error) {
        console.error('加载音色列表失败:', error);
        const loadErrorText = window.t ? window.t('voice.loadError') : '加载失败，请稍后重试';
        container.textContent = '';
        const errorDiv = document.createElement('div');
        errorDiv.className = 'voice-list-empty';
        errorDiv.style.color = '#f44336';
        const errorSpan = document.createElement('span');
        errorSpan.textContent = loadErrorText;
        errorDiv.appendChild(errorSpan);
        container.appendChild(errorDiv);
    } finally {
        if (refreshBtn) refreshBtn.disabled = false;
    }
}

// 删除音色
async function deleteVoice(voiceId, voiceName) {
    const confirmMsg = window.t
        ? window.t('voice.confirmDelete', { name: voiceName })
        : `确定要删除音色"${voiceName}"吗？此操作不可恢复。`;

    if (!confirm(confirmMsg)) {
        return;
    }

    const container = document.getElementById('voice-list-container');
    const refreshBtn = document.getElementById('refresh-voices-btn');

    if (!container) return;

    // 禁用刷新按钮
    if (refreshBtn) refreshBtn.disabled = true;

    // 显示删除中状态
    container.textContent = '';
    const deletingDiv = document.createElement('div');
    deletingDiv.style.textAlign = 'center';
    deletingDiv.style.color = '#999';
    deletingDiv.style.padding = '20px';
    const deletingSpan = document.createElement('span');
    deletingSpan.textContent = window.t ? window.t('voice.deleting') : '删除中...';
    deletingDiv.appendChild(deletingSpan);
    container.appendChild(deletingDiv);

    try {
        const response = await fetch(`/api/characters/voices/${encodeURIComponent(voiceId)}`, {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' }
        });

        const data = await response.json();

        if (response.ok && data.success) {
            // 删除成功，刷新列表
            await loadVoices();
            // 显示成功消息
            const resultDiv = document.getElementById('result');
            if (resultDiv) {
                resultDiv.textContent = window.t
                    ? window.t('voice.deleteSuccess', { name: voiceName })
                    : `音色"${voiceName}"已成功删除`;
                resultDiv.className = 'result';
                // 3秒后清除消息
                setTimeout(() => {
                    resultDiv.textContent = '';
                }, 3000);
            }
        } else {
            // 删除失败，重新加载列表以恢复事件处理器
            const errorMsg = data.error || (window.t ? window.t('voice.deleteFailed') : '删除失败');
            alert(errorMsg);
            await loadVoices();
        }
    } catch (error) {
        console.error('删除音色失败:', error);
        const errorMsg = window.t
            ? window.t('voice.deleteError', { error: error.message })
            : `删除失败: ${error.message}`;
        alert(errorMsg);
        // 重新加载列表以恢复事件处理器
        await loadVoices();
    } finally {
        if (refreshBtn) refreshBtn.disabled = false;
    }
}

// 页面加载时自动加载音色列表
(async function initVoiceList() {
    // 等待 i18n 初始化完成
    const waitForI18n = () => {
        if (window.i18n && window.i18n.isInitialized && typeof window.t === 'function') {
            // 确保页面文本已更新
            if (typeof window.updatePageTexts === 'function') {
                window.updatePageTexts();
            }
            // 等待页面完全加载后再加载音色列表
            setTimeout(loadVoices, 500);
        } else {
            // 继续等待
            setTimeout(waitForI18n, 100);
        }
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', waitForI18n);
    } else {
        waitForI18n();
    }
})();

