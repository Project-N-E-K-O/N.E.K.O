/**
 * 智能空白音频片段移除功能
 *
 * 当用户完成音频文件上传后，系统后端调用音频处理引擎对上传文件进行 RMS 能量检测分析，
 * 识别静音段落（-40 dBFS 以下且连续 ≥ 500 ms），然后弹出模态对话框展示结果。
 */

// ==================== 全局状态 ====================
let _silenceState = {
    analysisResult: null,       // 分析结果
    trimmedAudioBase64: null,   // 裁剪后的音频 base64
    trimmedFilename: null,      // 裁剪后的文件名
    trimmedMd5: null,           // MD5 校验值
    trimTaskId: null,           // 裁剪任务 ID
    progressPollTimer: null,    // 进度轮询定时器
    useTrimmedForUpload: false, // 是否使用裁剪后的音频进行注册
    originalFile: null,         // 原始文件引用
    opToken: null,              // 操作令牌，用于防止过期响应覆盖新状态
};

// ==================== 入口：文件选择后自动分析 ====================

/**
 * 在文件选择后触发静音分析
 * 此函数在 audioFile 的 change 事件中被调用
 */
async function onAudioFileSelected(file) {
    if (!file) return;

    // 重置状态
    _silenceState = {
        analysisResult: null,
        trimmedAudioBase64: null,
        trimmedFilename: null,
        trimmedMd5: null,
        trimTaskId: null,
        progressPollTimer: null,
        useTrimmedForUpload: false,
        originalFile: file,
        opToken: null,
    };

    // 开始静音分析
    try {
        const formData = new FormData();
        formData.append('file', file);

        const resp = await fetch('/api/characters/audio/analyze_silence', {
            method: 'POST',
            body: formData,
        });

        const data = await resp.json();

        if (!resp.ok) {
            console.warn('静音分析接口返回错误:', data.error);
            return; // 不阻塞正常流程
        }

        if (data.success && data.has_silence) {
            _silenceState.analysisResult = data;
            showSilenceModal(data);
        }
        // 如果没有检测到静音，不弹窗，用户正常上传
    } catch (e) {
        console.warn('静音分析请求失败:', e);
        // 不阻塞正常流程
    }
}

// ==================== 模态对话框 ====================

function showSilenceModal(data) {
    const modal = document.getElementById('silenceModal');
    if (!modal) return;

    // 填充数据
    setText('silenceOriginalDuration', data.original_duration || '--:--');
    setText('silenceSilenceDuration', data.silence_duration || '--:--');
    setText('silenceEstimatedDuration', data.estimated_duration || '--:--');
    setText('silenceSavingPct', (data.saving_percentage || 0) + '%');

    // 重置 UI 状态
    showElement('silenceAnalysisResult', true);
    showElement('silenceProgressSection', false);
    showElement('silenceDownloadSection', false);
    showElement('silenceBtnTrim', true);
    showElement('silenceBtnOriginal', true);
    showElement('silenceBtnCancel', false);
    showElement('silenceModalFooter', true);

    modal.style.display = 'flex';
}

function closeSilenceModal() {
    const modal = document.getElementById('silenceModal');
    if (modal) modal.style.display = 'none';

    // 如果有正在进行的裁剪任务，通知后端取消
    if (_silenceState.trimTaskId && !_silenceState.trimmedAudioBase64) {
        fetch(`/api/characters/audio/trim_cancel/${_silenceState.trimTaskId}`, {
            method: 'POST',
        }).catch(() => {});
    }

    // 停止进度轮询
    stopProgressPolling();

    // 轮换 opToken 使任何迟到的回调失效
    _silenceState.opToken = null;
}

// ==================== 操作处理 ====================

/**
 * 用户点击"使用智能裁剪音频"
 */
async function useTrimmedAudio() {
    if (!_silenceState.originalFile) {
        closeSilenceModal();
        return;
    }

    // 如果已完成裁剪，此按钮充当"确认使用裁剪后音频"，直接关闭弹窗
    if (_silenceState.trimmedAudioBase64) {
        closeSilenceModal();
        return;
    }

    // 切换 UI 到处理模式
    showElement('silenceBtnTrim', false);
    showElement('silenceBtnOriginal', false);
    showElement('silenceBtnCancel', true);
    showElement('silenceProgressSection', true);

    updateProgress(0, window.t ? window.t('voice.silenceModal.analyzing') : '分析中...');

    // 生成客户端 task ID 和操作令牌
    const taskId = crypto.randomUUID();
    const opToken = crypto.randomUUID();
    _silenceState.trimTaskId = taskId;
    _silenceState.opToken = opToken;

    try {
        const formData = new FormData();
        formData.append('file', _silenceState.originalFile);
        formData.append('task_id', taskId);

        // 立即开始轮询进度
        startProgressPolling(taskId, opToken);

        const resp = await fetch('/api/characters/audio/trim_silence', {
            method: 'POST',
            body: formData,
        });

        // 请求完成，停止轮询
        stopProgressPolling();

        // 检查操作令牌是否仍然有效（防止过期响应覆盖新状态）
        if (_silenceState.opToken !== opToken) return;

        const data = await resp.json();

        if (!resp.ok) {
            throw new Error(data.error || `API 返回 ${resp.status}`);
        }

        if (data.cancelled) {
            updateProgress(0, window.t ? window.t('voice.silenceModal.cancelled') : '已取消');
            setTimeout(() => {
                resetModalToAnalysis();
            }, 1000);
            return;
        }

        if (data.success && data.has_changes) {
            // 裁剪成功
            _silenceState.trimmedAudioBase64 = data.audio_base64;
            _silenceState.trimmedFilename = data.filename;
            _silenceState.trimmedMd5 = data.md5;
            _silenceState.useTrimmedForUpload = true;

            updateProgress(100, window.t ? window.t('voice.silenceModal.done') : '完成');

            // 显示下载区域
            showElement('silenceProgressSection', false);
            showElement('silenceDownloadSection', true);
            setText('silenceMd5', `MD5: ${data.md5}`);

            // 更新底部按钮：裁剪按钮变为"使用裁剪后音频"确认按钮
            const trimBtnSpan = document.querySelector('#silenceBtnTrim span');
            if (trimBtnSpan) {
                trimBtnSpan.setAttribute('data-i18n', 'voice.silenceModal.confirmTrimmed');
                trimBtnSpan.textContent = window.t
                    ? window.t('voice.silenceModal.confirmTrimmed')
                    : '使用智能裁切后的音频';
            }
            showElement('silenceBtnCancel', false);
            showElement('silenceBtnOriginal', true);
            showElement('silenceBtnTrim', true);

            // 替换注册用的文件为裁剪后的文件
            _replaceFileInputWithTrimmed(data);

        } else if (data.success && !data.has_changes) {
            // 没有需要移除的静音
            const msg = window.t ? window.t('voice.silenceModal.noSilence') : '未检测到可移除的静音段';
            updateProgress(100, msg);
            setTimeout(() => {
                closeSilenceModal();
            }, 1500);
        }

    } catch (e) {
        stopProgressPolling();
        console.error('裁剪失败:', e);
        const errMsg = window.t
            ? window.t('voice.silenceModal.trimError', { error: e.message })
            : `裁剪失败: ${e.message}`;
        updateProgress(0, errMsg);

        // 恢复按钮
        showElement('silenceBtnCancel', false);
        showElement('silenceBtnTrim', true);
        showElement('silenceBtnOriginal', true);
    }
}

/**
 * 用户点击"上传原音频"
 */
function useOriginalAudio() {
    _silenceState.useTrimmedForUpload = false;
    _silenceState.trimmedAudioBase64 = null;
    _restoreOriginalFileInput();
    closeSilenceModal();
}

/**
 * 取消裁剪任务
 */
async function cancelTrimTask() {
    // 停止进度轮询
    stopProgressPolling();

    // 使当前 opToken 失效
    _silenceState.opToken = null;

    if (_silenceState.trimTaskId) {
        try {
            await fetch(`/api/characters/audio/trim_cancel/${_silenceState.trimTaskId}`, {
                method: 'POST',
            });
        } catch (e) {
            console.warn('取消任务请求失败:', e);
        }
    }

    resetModalToAnalysis();
}

// ==================== 进度轮询 ====================

/**
 * 开始轮询裁剪任务进度
 */
function startProgressPolling(taskId, opToken) {
    stopProgressPolling();
    _silenceState.progressPollTimer = setInterval(async () => {
        // 如果 opToken 不再匹配，停止轮询
        if (_silenceState.opToken !== opToken) {
            stopProgressPolling();
            return;
        }
        try {
            const resp = await fetch(`/api/characters/audio/trim_progress/${taskId}`);
            const data = await resp.json();
            if (data.progress !== undefined) {
                const phaseText = data.phase === 'analyzing'
                    ? (window.t ? window.t('voice.silenceModal.analyzing') : '分析中...')
                    : (window.t ? window.t('voice.silenceModal.trimming') : '裁剪中...');
                updateProgress(data.progress, phaseText);
            }
            if (data.progress >= 100 || data.phase === 'done') {
                stopProgressPolling();
            }
        } catch (_) {
            // 忽略轮询错误
        }
    }, 500);
}

/**
 * 停止进度轮询
 */
function stopProgressPolling() {
    if (_silenceState.progressPollTimer) {
        clearInterval(_silenceState.progressPollTimer);
        _silenceState.progressPollTimer = null;
    }
}

/**
 * 下载裁剪后的音频
 */
function downloadTrimmedAudio() {
    if (!_silenceState.trimmedAudioBase64) {
        return;
    }

    const byteString = atob(_silenceState.trimmedAudioBase64);
    const ab = new ArrayBuffer(byteString.length);
    const ia = new Uint8Array(ab);
    for (let i = 0; i < byteString.length; i++) {
        ia[i] = byteString.charCodeAt(i);
    }
    const blob = new Blob([ab], { type: 'audio/wav' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = _silenceState.trimmedFilename || 'trimmed_audio.wav';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

// ==================== 文件替换 ====================

/**
 * 将 file input 的文件替换为裁剪后的音频
 */
function _replaceFileInputWithTrimmed(data) {
    if (!_silenceState.trimmedAudioBase64) return;

    const byteString = atob(_silenceState.trimmedAudioBase64);
    const ab = new ArrayBuffer(byteString.length);
    const ia = new Uint8Array(ab);
    for (let i = 0; i < byteString.length; i++) {
        ia[i] = byteString.charCodeAt(i);
    }

    const filename = _silenceState.trimmedFilename || 'trimmed_audio.wav';
    const blob = new Blob([ab], { type: 'audio/wav' });
    const trimmedFile = new File([blob], filename, { type: 'audio/wav' });

    // 使用 DataTransfer 来设置 file input 的文件
    const dt = new DataTransfer();
    dt.items.add(trimmedFile);
    const fileInput = document.getElementById('audioFile');
    if (fileInput) {
        fileInput.files = dt.files;
    }

    // 更新文件名显示
    const fileNameDisplay = document.getElementById('fileNameDisplay');
    if (fileNameDisplay) {
        const trimLabel = window.t ? window.t('voice.silenceModal.trimmedLabel') : '[已裁剪]';
        fileNameDisplay.textContent = `${trimLabel} ${filename}`;
        fileNameDisplay.style.color = '#4CAF50';
    }
}

/**
 * 恢复原始文件到 file input
 */
function _restoreOriginalFileInput() {
    if (!_silenceState.originalFile) return;

    const dt = new DataTransfer();
    dt.items.add(_silenceState.originalFile);
    const fileInput = document.getElementById('audioFile');
    if (fileInput) {
        fileInput.files = dt.files;
    }

    // 恢复原始文件名显示
    const fileNameDisplay = document.getElementById('fileNameDisplay');
    if (fileNameDisplay) {
        fileNameDisplay.textContent = _silenceState.originalFile.name;
        fileNameDisplay.style.color = '';
    }
}

// ==================== UI 辅助 ====================

function setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
}

function showElement(id, show) {
    const el = document.getElementById(id);
    if (el) el.style.display = show ? '' : 'none';
}

function updateProgress(pct, phaseText) {
    const bar = document.getElementById('silenceProgressBar');
    const pctEl = document.getElementById('silenceProgressPct');
    const phaseEl = document.getElementById('silenceProgressPhase');

    if (bar) bar.style.width = pct + '%';
    if (pctEl) pctEl.textContent = pct + '%';
    if (phaseEl && phaseText) phaseEl.textContent = phaseText;
}

function resetModalToAnalysis() {
    showElement('silenceProgressSection', false);
    showElement('silenceDownloadSection', false);
    showElement('silenceBtnTrim', true);
    showElement('silenceBtnOriginal', true);
    showElement('silenceBtnCancel', false);

    // 恢复裁剪按钮的原始文字
    const trimBtnSpan = document.querySelector('#silenceBtnTrim span');
    if (trimBtnSpan) {
        trimBtnSpan.setAttribute('data-i18n', 'voice.silenceModal.useTrimmed');
        trimBtnSpan.textContent = window.t
            ? window.t('voice.silenceModal.useTrimmed')
            : '使用智能裁剪音频';
    }
}

// ==================== 注册事件钩子 ====================

/**
 * 重写 audioFile 的 change 事件，在原有逻辑之后加入静音分析
 */
(function hookFileInput() {
    function attachHook() {
        const audioFile = document.getElementById('audioFile');
        if (!audioFile) {
            setTimeout(attachHook, 200);
            return;
        }

        audioFile.addEventListener('change', function () {
            if (this.files && this.files.length > 0) {
                onAudioFileSelected(this.files[0]);
            }
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', attachHook);
    } else {
        attachHook();
    }
})();
