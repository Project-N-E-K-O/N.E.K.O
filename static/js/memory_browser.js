(function () {
    'use strict';

    const PARENT_ORIGIN = window.location.origin;
    let currentMemoryFile = null;
    let chatData = [];
    let currentCatName = '';
    let memoryFileRequestId = 0;

    async function loadMemoryFileList() {
        const ul = document.getElementById('memory-file-list');
        ul.innerHTML = `<li style="color:#888; padding: 8px;">${window.t ? window.t('memory.loading') : '加载中...'}</li>`;
        try {
            const resp = await fetch('/api/memory/recent_files');
            const data = await resp.json();
            ul.innerHTML = '';
            if (data.files && data.files.length) {
                // 获取当前猫娘名称
                let currentCatgirl = null;
                try {
                    const catgirlResp = await fetch('/api/characters/current_catgirl');
                    const catgirlData = await catgirlResp.json();
                    currentCatgirl = catgirlData.current_catgirl || null;
                } catch (e) {
                    console.error('获取当前猫娘失败:', e);
                }

                let foundCurrentCatgirl = false;
                data.files.forEach(f => {
                    // 提取猫娘名
                    let match = f.match(/^recent_(.+)\.json$/);
                    let catName = match ? match[1] : f;
                    const li = document.createElement('li');
                    // 按钮样式（使用 DOM API，避免插入未转义内容）
                    const btn = document.createElement('button');
                    btn.className = 'cat-btn';
                    btn.setAttribute('data-filename', f);
                    btn.setAttribute('data-catname', catName);
                    btn.textContent = catName;
                    btn.addEventListener('click', () => selectMemoryFile(f, li, catName));
                    li.appendChild(btn);
                    ul.appendChild(li);

                    // 如果是当前猫娘，自动选择
                    if (currentCatgirl && catName === currentCatgirl && !foundCurrentCatgirl) {
                        foundCurrentCatgirl = true;
                        // 延迟一下确保DOM已渲染
                        setTimeout(() => {
                            selectMemoryFile(f, li, catName);
                        }, 100);
                    }
                });
            } else {
                ul.innerHTML = `<li style="color:#888; padding: 8px;">${window.t ? window.t('memory.noFiles') : '无文件'}</li>`;
            }
        } catch (e) {
            ul.innerHTML = `<li style="color:#e74c3c; padding: 8px;">${window.t ? window.t('memory.loadFailed') : '加载失败'}</li>`;
        }
    }

    function renderChatEdit() {
        const div = document.getElementById('memory-chat-edit');
        // 清空并使用 DOM API 渲染每一条消息，避免将未转义的用户数据插入到 HTML 中
        while (div.firstChild) div.removeChild(div.firstChild);
        chatData.forEach((msg, i) => {
            const container = document.createElement('div');
            container.className = 'chat-item';

            if (msg.role === 'system') {
                let text = msg.text || '';
                // 去掉任何现有的前缀（支持多语言切换时的旧前缀）
                // 定义已知的备忘录前缀列表
                const knownPrefixes = [
                    '先前对话的备忘录: ',
                    'Previous conversation memo: ',
                    '前回の会話のメモ: ',
                    '先前對話的備忘錄: '
                ];
                // 尝试移除已知前缀
                for (const prefix of knownPrefixes) {
                    if (text.startsWith(prefix)) {
                        text = text.slice(prefix.length);
                        break;
                    }
                }

                const contentWrapper = document.createElement('div');
                contentWrapper.className = 'chat-item-content';
                container.appendChild(contentWrapper);

                const memoPrefix = window.t ? window.t('memory.previousMemo') : '先前对话的备忘录: ';
                const label = document.createElement('span');
                label.className = 'memo-label';
                label.textContent = memoPrefix;
                contentWrapper.appendChild(label);

                const ta = document.createElement('textarea');
                ta.className = 'memo-textarea';
                ta.value = text;
                ta.addEventListener('change', function () { updateSystemContent(i, this.value); });
                contentWrapper.appendChild(ta);
            } else if (msg.role === 'ai') {
                // 提取时间戳和正文，健壮处理
                const m = msg.text.match(/^(\[[^\]]+\])([\s\S]*)$/);
                const timeStr = m ? m[1] : '';
                const content = (m && m[2]) ? (m[2] || '').trim() : msg.text;

                const contentWrapper = document.createElement('div');
                contentWrapper.className = 'chat-item-content';
                container.appendChild(contentWrapper);

                const catLabel = currentCatName ? currentCatName : 'AI';
                const speaker = document.createElement('div');
                speaker.className = 'chat-speaker';
                speaker.textContent = catLabel;
                contentWrapper.appendChild(speaker);

                const bubble = document.createElement('div');
                bubble.className = 'chat-bubble';
                bubble.textContent = content;
                contentWrapper.appendChild(bubble);

                if (timeStr) {
                    const timeDiv = document.createElement('div');
                    timeDiv.className = 'chat-time';
                    timeDiv.textContent = timeStr;
                    contentWrapper.appendChild(timeDiv);
                }

                const deleteWrapper = document.createElement('div');
                deleteWrapper.className = 'delete-btn-wrapper';
                const delBtn = document.createElement('button');
                delBtn.className = 'delete-btn';
                delBtn.textContent = window.t ? window.t('memory.delete') : '删除';
                delBtn.addEventListener('click', function () { deleteChat(i); });
                deleteWrapper.appendChild(delBtn);
                container.appendChild(deleteWrapper);
            } else {
                const contentWrapper = document.createElement('div');
                contentWrapper.className = 'chat-item-content';
                container.appendChild(contentWrapper);

                const speaker = document.createElement('div');
                speaker.className = 'chat-speaker';
                speaker.textContent = window.t ? window.t('memory.me') : '我：';
                contentWrapper.appendChild(speaker);

                const bubble = document.createElement('div');
                bubble.className = 'chat-bubble';
                bubble.textContent = msg.text;
                contentWrapper.appendChild(bubble);

                const deleteWrapper = document.createElement('div');
                deleteWrapper.className = 'delete-btn-wrapper';
                const delBtn = document.createElement('button');
                delBtn.className = 'delete-btn';
                delBtn.textContent = window.t ? window.t('memory.delete') : '删除';
                delBtn.addEventListener('click', function () { deleteChat(i); });
                deleteWrapper.appendChild(delBtn);
                container.appendChild(deleteWrapper);
            }

            div.appendChild(container);
        });
    }

    function deleteChat(idx) {
        chatData.splice(idx, 1);
        renderChatEdit();
    }
    // 新增：AI输入框内容变更时，自动拼接时间戳
    function updateAIContent(idx, value) {
        const msg = chatData[idx];
        const m = msg.text.match(/^(\[[^\]]+\])/);
        if (m) {
            chatData[idx].text = m[1] + value;
        } else {
            chatData[idx].text = value;
        }
    }
    function updateSystemContent(idx, value) {
        // 存储时先移除任何现有的前缀，然后加上当前语言的前缀
        // 定义已知的备忘录前缀列表
        const knownPrefixes = [
            '先前对话的备忘录: ',
            'Previous conversation memo: ',
            '前回の会話のメモ: ',
            '先前對話的備忘錄: '
        ];
        // 尝试移除已知前缀
        for (const prefix of knownPrefixes) {
            if (value.startsWith(prefix)) {
                value = value.slice(prefix.length);
                break;
            }
        }
        const memoPrefix = window.t ? window.t('memory.previousMemo') : '先前对话的备忘录: ';
        chatData[idx].text = memoPrefix + value;
    }
    async function selectMemoryFile(filename, li, catName) {
        const requestId = ++memoryFileRequestId;
        currentMemoryFile = filename;
        currentCatName = catName || (li ? li.getAttribute('data-catname') : '');
        Array.from(document.getElementById('memory-file-list').children).forEach(x => x.classList.remove('selected'));
        if (li) li.classList.add('selected');
        const editDiv = document.getElementById('memory-chat-edit');

        // 清空并使用 textContent 设置加载中状态
        editDiv.textContent = '';
        const loadingDiv = document.createElement('div');
        loadingDiv.style.cssText = 'color:#888; padding: 20px; text-align: center;';
        loadingDiv.textContent = window.t ? window.t('memory.loading') : '加载中...';
        editDiv.appendChild(loadingDiv);

        const saveRow = document.getElementById('save-row');
        if (saveRow) {
            saveRow.style.display = 'flex';
        }
        try {
            // 直接获取原始JSON内容
            const resp = await fetch('/api/memory/recent_file?filename=' + encodeURIComponent(filename));
            const data = await resp.json();
            if (requestId !== memoryFileRequestId) {
                return;
            }
            if (data.content) {
                let arr = [];
                try { arr = JSON.parse(data.content); } catch (e) { arr = []; }
                if (requestId !== memoryFileRequestId) {
                    return;
                }
                chatData = arr.map(item => {
                    if (item.type === 'system') {
                        return { role: 'system', text: item.data && item.data.content ? item.data.content : '' };
                    } else if (item.type === 'ai' || item.type === 'human') {
                        let text = '';
                        const content = item.data && item.data.content;
                        if (Array.isArray(content) && content[0] && content[0].type === 'text') {
                            text = content[0].text;
                        } else if (typeof content === 'string') {
                            text = content;
                        }
                        return { role: item.type, text };
                    } else {
                        return null;
                    }
                }).filter(Boolean);
                renderChatEdit();
            } else {
                if (requestId !== memoryFileRequestId) {
                    return;
                }
                chatData = [];
                editDiv.innerHTML = '<div style="color:#888; padding: 20px; text-align: center;">' + (window.t ? window.t('memory.noChatContent') : '无聊天内容') + '</div>';
            }
        } catch (e) {
            if (requestId !== memoryFileRequestId) {
                return;
            }
            chatData = [];
            editDiv.innerHTML = '<div style="color:#e74c3c; padding: 20px; text-align: center;">' + (window.t ? window.t('memory.loadFailed') : '加载失败') + '</div>';
        }
    }
    document.getElementById('save-memory-btn').onclick = async function () {
        if (!currentMemoryFile) {
            showSaveStatus(window.t ? window.t('memory.pleaseSelectFile') : '请先选择文件', false);
            return;
        }
        // 处理备忘录为空的情况
        const memoPrefix = window.t ? window.t('memory.previousMemo') : '先前对话的备忘录: ';
        const memoNone = window.t ? window.t('memory.memoNone') : '无。';
        chatData.forEach(msg => {
            if (msg.role === 'system') {
                let text = msg.text || '';
                if (text.startsWith(memoPrefix)) {
                    text = text.slice(memoPrefix.length);
                }
                if (!text.trim()) {
                    msg.text = memoPrefix + memoNone;
                }
            }
        });
        try {
            const resp = await fetch('/api/memory/recent_file/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ filename: currentMemoryFile, chat: chatData })
            });
            const data = await resp.json();
            if (data.success) {
                showSaveStatus(window.t ? window.t('memory.saveSuccess') : '保存成功', true);

                // 通知父窗口刷新对话上下文
                if (data.need_refresh) {
                    let broadcastSent = false;
                    
                    // 优先使用 BroadcastChannel（跨页面通信）
                    if (typeof BroadcastChannel !== 'undefined') {
                        let channel = null;
                        try {
                            channel = new BroadcastChannel('neko_page_channel');
                            channel.postMessage({
                                action: 'memory_edited',
                                catgirl_name: data.catgirl_name
                            });
                            console.log('[MemoryBrowser] 已通过 BroadcastChannel 发送 memory_edited 消息');
                            broadcastSent = true;
                        } catch (e) {
                            console.error('[MemoryBrowser] BroadcastChannel 发送失败:', e);
                        } finally {
                            if (channel) {
                                channel.close();
                            }
                        }
                    }
                    
                    // 仅当 BroadcastChannel 不可用时，使用 postMessage 作为后备（iframe 场景）
                    if (!broadcastSent && window.parent && window.parent !== window) {
                        window.parent.postMessage({
                            type: 'memory_edited',
                            catgirl_name: data.catgirl_name
                        }, PARENT_ORIGIN);
                        console.log('[MemoryBrowser] 已通过 postMessage 发送 memory_edited 消息（后备方案）');
                    }
                }
            } else {
                const errorMsg = data.error || (window.t ? window.t('common.unknownError') : '未知错误');
                showSaveStatus(window.t ? window.t('memory.saveFailed', { error: errorMsg }) : '保存失败：' + errorMsg, false);
            }
        } catch (e) {
            showSaveStatus(window.t ? window.t('memory.saveFailedGeneral') : '保存失败', false);
        }
    };
    document.getElementById('clear-memory-btn').onclick = function () {
        // 只保留 system 类型（备忘录），其余全部清除
        chatData = chatData.filter(msg => msg.role === 'system');
        renderChatEdit();
        showSaveStatus(window.t ? window.t('memory.clearedMemory') : '已清空近期记忆，未保存', false);
    };
    function showSaveStatus(msg, success) {
        const el = document.getElementById('save-status');
        el.textContent = msg;
        el.style.color = success ? '#27ae60' : '#e74c3c';
        if (success) {
            setTimeout(() => { el.textContent = ''; }, 3000);
        }
    }
    function closeMemoryBrowser() {
        if (window.opener) {
            // 如果是通过 window.open() 打开的，直接关闭
            window.close();
        } else if (window.parent && window.parent !== window) {
            // 如果在 iframe 中，通知父窗口关闭
            window.parent.postMessage({ type: 'close_memory_browser' }, PARENT_ORIGIN);
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
    // 将函数暴露到全局作用域，供 HTML onclick 调用
    window.closeMemoryBrowser = closeMemoryBrowser;
    // 页面加载时隐藏保存按钮
    document.addEventListener('DOMContentLoaded', function () {
        loadMemoryFileList();
        loadReviewConfig();
        document.getElementById('save-row').style.display = 'none';

        // 监听checkbox变化
        const checkbox = document.getElementById('review-toggle-checkbox');
        if (checkbox) {
            checkbox.addEventListener('change', function () {
                toggleReview(this.checked);
            });
        }

        // 监听i18n语言变化
        if (window.i18n) {
            window.i18n.on('languageChanged', function () {
                const checkbox = document.getElementById('review-toggle-checkbox');
                if (checkbox) {
                    updateToggleText(checkbox.checked);
                }
            });
        }

        // Electron白屏修复
        if (document.body) {
            void document.body.offsetHeight;
            const currentOpacity = document.body.style.opacity || '1';
            document.body.style.opacity = '0.99';
            requestAnimationFrame(() => {
                document.body.style.opacity = currentOpacity;
            });
        }
    });

    window.addEventListener('load', function () {
        // 再次强制重绘以确保资源加载后显示
        if (document.body) void document.body.offsetHeight;
    });


    async function loadReviewConfig() {
        try {
            const resp = await fetch('/api/memory/review_config');
            const data = await resp.json();
            const checkbox = document.getElementById('review-toggle-checkbox');

            if (checkbox) {
                checkbox.checked = data.enabled;
            }
            updateToggleText(data.enabled);
        } catch (e) {
            console.error('加载审阅配置失败:', e);
        }
    }

    function updateToggleText(enabled) {
        const textSpan = document.getElementById('review-toggle-text');
        if (!textSpan) return;
        if (enabled) {
            textSpan.setAttribute('data-i18n', 'memory.enabled');
            textSpan.textContent = window.t ? window.t('memory.enabled') : '已开启';
        } else {
            textSpan.setAttribute('data-i18n', 'memory.disabled');
            textSpan.textContent = window.t ? window.t('memory.disabled') : '已关闭';
        }
    }

    async function toggleReview(enabled) {
        try {
            const resp = await fetch('/api/memory/review_config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ enabled: enabled })
            });
            const data = await resp.json();

            if (data.success) {
                updateToggleText(enabled);
            } else {
                // 如果保存失败，恢复原来的状态
                const checkbox = document.getElementById('review-toggle-checkbox');
                if (checkbox) {
                    checkbox.checked = !enabled;
                }
                updateToggleText(!enabled);
            }
        } catch (e) {
            console.error('更新审阅配置失败:', e);
            // 如果请求失败，恢复原来的状态
            const checkbox = document.getElementById('review-toggle-checkbox');
            if (checkbox) {
                checkbox.checked = !enabled;
            }
            updateToggleText(!enabled);
        }
    }

})();

