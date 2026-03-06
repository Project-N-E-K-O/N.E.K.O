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
    }, (aplayerInstance) => {
        if (aplayerInstance) {
            console.log('[APlayer] Successfully injected to chat-container');
            setupInjectedControls(aplayerInstance, config);
        }
    });

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
        const isMini = container.style.width === '300px';
        if (isMini) {
            toggleBtn.style.transform = 'translateX(-50%) scale(1.1)';
        } else {
            toggleBtn.style.transform = 'scale(1.1)';
        }
        toggleBtn.style.background = 'rgba(255, 255, 255, 1)';
    });

    toggleBtn.addEventListener('mouseleave', () => {
        const isMini = container.style.width === '300px';
        if (isMini) {
            toggleBtn.style.transform = 'translateX(-50%) scale(1)';
        } else {
            toggleBtn.style.transform = 'scale(1)';
        }
        toggleBtn.style.background = 'rgba(255, 255, 255, 0.9)';
    });

    toggleBtn.addEventListener('click', () => {
        const isMini = container.style.width === '300px';
        if (isMini) {
            // 展开模式
            container.style.width = '100%';
            container.style.left = '0';
            container.style.bottom = '0';
            container.style.borderRadius = '8px 8px 0 0';
            // 按钮移到右上角，显示折叠图标
            toggleBtn.style.top = '10px';
            toggleBtn.style.left = 'auto';
            toggleBtn.style.right = '10px';
            toggleBtn.style.transform = 'none';
            toggleBtn.innerHTML = '✕';
        } else {
            // 迷你模式
            container.style.width = '300px';
            container.style.left = '10px';
            container.style.bottom = '10px';
            container.style.borderRadius = '8px';
            // 按钮恢复到顶部居中
            toggleBtn.style.top = '-40px';
            toggleBtn.style.left = '50%';
            toggleBtn.style.right = 'auto';
            toggleBtn.style.transform = 'translateX(-50%)';
            toggleBtn.innerHTML = '🎵';
        }
    });

    container.appendChild(toggleBtn);

    window.aplayerInjected = {
        aplayer,
        container,
        containerId: config.containerId,
        toggleBtn,
        show: () => {
            container.style.display = 'block';
            if (config.defaultMiniPlayer) {
                toggleBtn.style.display = 'flex';
            }
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
                if (config.defaultMiniPlayer) {
                    toggleBtn.style.display = 'flex';
                }
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
    // 先销毁播放器实例
    if (window.aplayerInjected && window.aplayerInjected.aplayer) {
        const player = window.aplayerInjected.aplayer;
        if (typeof player.pause === 'function') {
            player.pause();
        }
        if (typeof player.destroy === 'function') {
            player.destroy();
        }
    }
    
    // 使用存储的容器引用，而非硬编码 ID
    const container = window.aplayerInjected?.container || document.getElementById('aplayer-container');
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
    // 优先使用存储的容器引用，回退到默认 ID
    return window.aplayerInjected?.container || document.getElementById('aplayer-container');
}

export function setupAPlayerInChat(options = {}) {
    if (getAPlayerContainer()) {
        console.warn('[APlayer] Already injected, removing old instance');
        removeAPlayerFromChatContainer();
    }

    return injectAPlayerToChatContainer(options);
}
