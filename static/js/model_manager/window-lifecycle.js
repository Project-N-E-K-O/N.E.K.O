window.addEventListener('beforeunload', (e) => {
    notifyCardMakerFallbackOwnerClosing();

    if (isModelManagerSettingsWaiting()) {
        const message = getModelManagerSettingsWaitingMessage();
        setModelManagerStatusText(message);
        e.preventDefault();
        e.returnValue = message;
        return message;
    }

    // 尝试退出全屏
    if (isFullscreen()) {
        try {
            exitFullscreen();
        } catch (err) {
            console.log('退出全屏失败:', err);
        }
    }

    if (window.opener) {
        // 如果用户已保存过设置，通知主页重载模型（兜底：用户可能直接关闭窗口而非点击返回按钮）
        if (window._modelManagerHasSaved && window._modelManagerLanlanName && window._modelManagerLanlanName.trim() !== '') {
            sendMessageToMainPage('reload_model', { lanlan_name: window._modelManagerLanlanName });
        }
    }

});
