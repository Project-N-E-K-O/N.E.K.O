/**
 * APlayer 注入器 - 将 APlayer 集成到 common-ui 中
 * 功能:
 *  - 在聊天容器内嵌入 APlayer 播放器
 *  - 提供播放器的显示/隐藏控制
 *  - 支持主题和位置配置
 */

import { initializeAPlayer } from './main.js';

const APLAYER_CONFIG = {
    containerId: 'aplayer-container',
    defaultPosition: 'bottom-left',
    defaultTheme: 'dark',
    defaultAutoHide: true,
    defaultMiniPlayer: true
};

export function injectAPlayerToChatContainer(options = {}) {
    const config = {
        ...APLAYER_CONFIG,
        ...options
    };

    const chatContainer = document.getElementById('chat-container');
    if (!chatContainer) {
        console.error('[APlayer] Cannot inject: chat-container not found');
        return null;
    }

    const aplayerContainer = document.createElement('div');
    aplayerContainer.id = config.containerId;
    aplayerContainer.className = 'aplayer-injected';
    
    Object.assign(aplayerContainer.style, {
        position: 'absolute',
        bottom: '10px',
        left: '10px',
        width: '300px',
        zIndex: '100',
        transition: 'all 0.3s ease'
    });

    chatContainer.appendChild(aplayerContainer);

    const aplayer = initializeAPlayer({
        container: aplayerContainer,
        ...options
    });

    if (aplayer) {
        console.log('[APlayer] Successfully injected to chat-container');
        setupInjectedControls(aplayer, config);
    }

    return aplayer;
}

function setupInjectedControls(aplayer, config) {
    const container = document.getElementById(config.containerId);
    if (!container) return;

    const toggleBtn = document.createElement('button');
    toggleBtn.id = 'aplayer-toggle-btn';
    toggleBtn.innerHTML = '🎵';
    Object.assign(toggleBtn.style, {
        position: 'absolute',
        top: '-40px',
        left: '50%',
        transform: 'translateX(-50%)',
        width: '40px',
        height: '40px',
        borderRadius: '50%',
        background: 'rgba(255, 255, 255, 0.9)',
        border: '2px solid #4f8cff',
        fontSize: '20px',
        cursor: 'pointer',
        zIndex: '101',
        transition: 'all 0.2s ease',
        display: config.defaultMiniPlayer ? 'flex' : 'none',
        alignItems: 'center',
        justifyContent: 'center'
    });

    toggleBtn.addEventListener('mouseenter', () => {
        toggleBtn.style.transform = 'translateX(-50%) scale(1.1)';
        toggleBtn.style.background = 'rgba(255, 255, 255, 1)';
    });

    toggleBtn.addEventListener('mouseleave', () => {
        toggleBtn.style.transform = 'translateX(-50%) scale(1)';
        toggleBtn.style.background = 'rgba(255, 255, 255, 0.9)';
    });

    toggleBtn.addEventListener('click', () => {
        const isMini = container.style.width === '300px';
        if (isMini) {
            container.style.width = '100%';
            container.style.left = '0';
            container.style.bottom = '0';
            container.style.borderRadius = '8px 8px 0 0';
            toggleBtn.style.display = 'none';
        } else {
            container.style.width = '300px';
            container.style.left = '10px';
            container.style.bottom = '10px';
            container.style.borderRadius = '8px';
            toggleBtn.style.display = 'flex';
        }
    });

    container.appendChild(toggleBtn);

    window.aplayerInjected = {
        aplayer,
        container,
        toggleBtn,
        show: () => {
            container.style.display = 'block';
            if (config.defaultMiniPlayer) toggleBtn.style.display = 'flex';
        },
        hide: () => {
            container.style.display = 'none';
        },
        toggle: () => {
            const isVisible = container.style.display !== 'none';
            if (isVisible) {
                container.style.display = 'none';
            } else {
                container.style.display = 'block';
                if (config.defaultMiniPlayer) toggleBtn.style.display = 'flex';
            }
        },
        setMiniPlayer: (enabled) => {
            config.defaultMiniPlayer = enabled;
            toggleBtn.style.display = enabled ? 'flex' : 'none';
        },
        setTheme: (theme) => {
            container.classList.remove('aplayer-theme-dark', 'aplayer-theme-light');
            container.classList.add(`aplayer-theme-${theme}`);
        }
    };

    window.aplayerControls = window.aplayerControls || {};
    window.aplayerControls.showPlayer = window.aplayerControls.showPlayer || window.aplayerInjected.show;
    window.aplayerControls.hidePlayer = window.aplayerControls.hidePlayer || window.aplayerInjected.hide;
    window.aplayerControls.togglePlayer = window.aplayerControls.togglePlayer || window.aplayerInjected.toggle;
}

export function removeAPlayerFromChatContainer() {
    const container = document.getElementById('aplayer-container');
    if (container) {
        container.remove();
        console.log('[APlayer] Removed from chat-container');
    }

    if (window.aplayerInjected) {
        delete window.aplayerInjected;
    }
    
    if (window.aplayer) {
        window.aplayer = null;
    }
}

export function getAPlayerInstance() {
    return window.aplayerInjected ? window.aplayerInjected.aplayer : null;
}

export function getAPlayerContainer() {
    return document.getElementById('aplayer-container');
}

export function setupAPlayerInChat(options = {}) {
    if (getAPlayerContainer()) {
        console.warn('[APlayer] Already injected, removing old instance');
        removeAPlayerFromChatContainer();
    }

    return injectAPlayerToChatContainer(options);
}
