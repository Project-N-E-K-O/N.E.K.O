/**
 * APlayer 注入器 - 将 APlayer 集成到 common-ui 中
 */

import { initializeAPlayer, destroyAPlayer } from './main.js';

const APLAYER_CONFIG = {
    containerId: 'aplayer-container',
    defaultPosition: 'bottom-left',
    defaultTheme: 'dark',
    defaultAutoHide: true,
    defaultMiniPlayer: true
};

export async function injectAPlayerToChatContainer(options = {}) {
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

    // 异步等待实例加载
    const aplayer = await initializeAPlayer({
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
    toggleBtn.innerHTML = '<i class="fas fa-music"></i>';
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
        fontSize: '18px',
        color: '#4f8cff',
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
            toggleBtn.style.top = '10px';
            toggleBtn.style.left = 'auto';
            toggleBtn.style.right = '10px';
            toggleBtn.style.transform = 'none';
            toggleBtn.innerHTML = '<i class="fas fa-times"></i>';
        } else {
            // 迷你模式
            container.style.width = '300px';
            container.style.left = '10px';
            container.style.bottom = '10px';
            container.style.borderRadius = '8px';
            toggleBtn.style.top = '-40px';
            toggleBtn.style.left = '50%';
            toggleBtn.style.right = 'auto';
            toggleBtn.style.transform = 'translateX(-50%)';
            toggleBtn.innerHTML = '<i class="fas fa-music"></i>';
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
    // 统一调用 main.js 的原生销毁方法处理
    destroyAPlayer(); 

    if (window.aplayerInjected) {
        delete window.aplayerInjected;
        console.log('[APlayer] Removed from chat-container');
    }
}

export function getAPlayerInstance() {
    return window.aplayerInjected ? window.aplayerInjected.aplayer : null;
}

export function getAPlayerContainer() {
    return window.aplayerInjected?.container || document.getElementById('aplayer-container');
}

export async function setupAPlayerInChat(options = {}) {
    if (getAPlayerContainer()) {
        console.warn('[APlayer] Already injected, removing old instance');
        removeAPlayerFromChatContainer();
    }

    return await injectAPlayerToChatContainer(options);
}