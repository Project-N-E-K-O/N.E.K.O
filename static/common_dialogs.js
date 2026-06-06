/**
 * 通用异步模态对话框系统
 * 用于替代 alert(), confirm(), prompt() 等同步弹窗
 * 适用于 Electron 环境
 */

(function() {
    'use strict';

    const _decisionPromptQueue = [];
    let _decisionPromptActive = false;
    const NO_IMPLICIT_CLOSE = Symbol('NO_IMPLICIT_CLOSE');

    function safeT(key, fallback) {
        if (typeof window.safeT === 'function') {
            return window.safeT(key, fallback);
        }
        if (window.t && typeof window.t === 'function') {
            const translated = window.t(key, fallback);
            if (typeof translated === 'string') {
                return translated;
            }
        }
        return typeof fallback === 'string' ? fallback : key;
    }

    function renderMiniMarkdown(text) {
        let content = String(text || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');

        content = content.replace(/^#{1,6}\s+(.+)$/gm, '<strong>$1</strong>');
        content = content.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        content = content.replace(/(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)/g, '<em>$1</em>');
        content = content.replace(
            /^[-•]\s+(.+)$/gm,
            '<li style="margin-left:18px;list-style:disc;text-align:left;">$1</li>'
        );
        content = content.replace(/\n/g, '<br>');
        content = content.replace(/<\/li><br><li/g, '</li><li');
        return content;
    }

    if (typeof window.renderMiniMarkdown !== 'function') {
        window.renderMiniMarkdown = renderMiniMarkdown;
    }

    function applyModalTextContent(node, text, format) {
        if (!node) {
            return;
        }

        if (format === 'markdown') {
            node.classList.add('modal-body-markdown');
            node.innerHTML = renderMiniMarkdown(text);
            return;
        }

        node.textContent = String(text || '');
    }

    // 创建对话框样式
    const style = document.createElement('style');
    style.textContent = `
        .modal-overlay {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.5);
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: 2147483647;
            animation: fadeIn 0.2s ease-out;
            pointer-events: auto !important;
        }

        @keyframes fadeIn {
            from { opacity: 0; }
            to { opacity: 1; }
        }

        @keyframes slideIn {
            from { transform: translateY(-20px); opacity: 0; }
            to { transform: translateY(0); opacity: 1; }
        }

        .modal-dialog {
            background: white;
            border-radius: 12px;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
            min-width: 320px;
            max-width: 500px;
            max-height: 80vh;
            overflow: hidden;
            animation: slideIn 0.3s ease-out;
            pointer-events: auto !important;
        }

        .modal-header {
            padding: 20px 24px 16px;
            border-bottom: 1px solid #e0e0e0;
            pointer-events: auto !important;
        }

        .modal-title {
            margin: 0;
            font-size: 1.2rem;
            font-weight: 600;
            color: #222;
            pointer-events: auto !important;
        }

        .modal-body {
            padding: 20px 24px;
            color: #444;
            font-size: 1rem;
            line-height: 1.6;
            max-height: 60vh;
            overflow-y: auto;
            white-space: pre-wrap;
            pointer-events: auto !important;
        }

        .modal-body-markdown {
            white-space: normal;
        }

        .modal-input {
            width: 100%;
            padding: 10px 12px;
            border: 1px solid #ccc;
            border-radius: 6px;
            font-size: 1rem;
            margin-top: 12px;
            box-sizing: border-box;
            font-family: inherit;
            pointer-events: auto !important;
        }

        .modal-input:focus {
            outline: none;
            border-color: #4f8cff;
            box-shadow: 0 0 0 3px rgba(79, 140, 255, 0.1);
        }

        .modal-footer {
            padding: 16px 24px;
            border-top: 1px solid #e0e0e0;
            display: flex;
            justify-content: flex-end;
            gap: 10px;
            pointer-events: auto !important;
        }

        .modal-btn {
            padding: 8px 20px;
            border: none;
            border-radius: 6px;
            font-size: 1rem;
            cursor: pointer;
            transition: all 0.2s;
            font-weight: 500;
            pointer-events: auto !important;
        }

        .modal-btn:focus {
            outline: none;
            box-shadow: 0 0 0 3px rgba(79, 140, 255, 0.2);
        }

        .modal-btn-primary {
            background: #4f8cff;
            color: white;
        }

        .modal-btn-primary:hover {
            background: #3a7ae8;
        }

        .modal-btn-primary:active {
            background: #2662c8;
        }

        .modal-btn-secondary {
            background: #e0e0e0;
            color: #444;
        }

        .modal-btn-secondary:hover {
            background: #d0d0d0;
        }

        .modal-btn-secondary:active {
            background: #c0c0c0;
        }

        .modal-btn-danger {
            background: #e74c3c;
            color: white;
        }

        .modal-btn-danger:hover {
            background: #d43f2f;
        }

        .modal-btn-danger:active {
            background: #c0392b;
        }

        .modal-overlay-autostart-retention {
            background: rgba(245, 248, 255, 0.62);
        }

        .modal-dialog-autostart-retention {
            position: relative;
            min-width: 0;
            width: min(520px, calc(100vw - 40px));
            max-width: min(520px, calc(100vw - 40px));
            margin-top: 62px;
            padding-top: 78px;
            overflow: visible;
            border: 1px solid rgba(255, 255, 255, 0.88);
            border-radius: 34px;
            background: linear-gradient(180deg, #fff9fb 0%, #eef7ff 100%);
            box-shadow: 0 24px 58px rgba(95, 135, 190, 0.24), inset 0 1px 0 rgba(255,255,255,0.9);
        }

        .autostart-retention-bunny {
            position: absolute;
            top: -84px;
            left: 50%;
            width: 160px;
            height: 146px;
            transform: translateX(-50%);
            transition: transform 0.36s cubic-bezier(0.34, 1.56, 0.64, 1);
            pointer-events: none !important;
        }

        .autostart-retention-bunny-heart {
            position: absolute;
            top: 48px;
            left: 50%;
            z-index: 4;
            color: #ff8fb2;
            font-size: 24px;
            font-weight: 900;
            opacity: 0;
            transform: translateX(-50%) translateY(12px) scale(0.4);
            transition: opacity 0.3s ease, transform 0.4s cubic-bezier(0.34, 1.56, 0.64, 1);
        }

        .autostart-retention-bunny-glow {
            position: absolute;
            left: 50%;
            bottom: 3px;
            width: 190px;
            height: 80px;
            border-radius: 999px;
            background: rgba(163, 217, 255, 0.34);
            filter: blur(18px);
            transform: translateX(-50%);
        }

        .autostart-retention-bunny-ear {
            position: absolute;
            top: 0;
            z-index: 1;
            width: 42px;
            height: 92px;
            border-radius: 999px 999px 24px 24px;
            background: linear-gradient(160deg, #ffffff, #dcefff);
            box-shadow: inset -5px -8px 14px rgba(125, 169, 212, 0.18), inset 4px 5px 10px rgba(255,255,255,0.9);
            transform-origin: bottom center;
            transition: transform 0.42s cubic-bezier(0.34, 1.56, 0.64, 1);
        }

        .autostart-retention-bunny-ear::after {
            content: '';
            position: absolute;
            left: 10px;
            top: 16px;
            width: 22px;
            height: 58px;
            border-radius: 999px;
            background: linear-gradient(180deg, #ffd5e3, #ffc2d4);
            opacity: 0.82;
        }

        .autostart-retention-bunny-ear-left {
            left: 31px;
            transform: rotate(-18deg);
        }

        .autostart-retention-bunny-ear-right {
            right: 31px;
            transform: rotate(18deg);
        }

        .autostart-retention-bunny-head {
            position: absolute;
            left: 50%;
            bottom: 0;
            z-index: 2;
            width: 126px;
            height: 102px;
            border-radius: 58px 58px 48px 48px;
            background: linear-gradient(145deg, #ffffff, #e4f2ff);
            box-shadow: 0 15px 28px rgba(93, 131, 180, 0.20), inset -8px -10px 18px rgba(142, 187, 230, 0.16), inset 8px 8px 18px rgba(255,255,255,0.95);
            transform: translateX(-50%);
            transform-origin: bottom center;
            transition: transform 0.42s cubic-bezier(0.34, 1.56, 0.64, 1);
            animation: autostartRetentionBreathe 2.6s ease-in-out infinite;
        }

        .autostart-retention-bunny-eye {
            position: absolute;
            top: 40px;
            width: 14px;
            height: 14px;
            border-radius: 50%;
            background: #2c3a4a;
            transition: all 0.34s cubic-bezier(0.34, 1.56, 0.64, 1);
        }

        .autostart-retention-bunny-eye::after {
            content: '';
            position: absolute;
            top: 2px;
            right: 3px;
            width: 5px;
            height: 5px;
            border-radius: 50%;
            background: #ffffff;
            transition: all 0.3s ease;
        }

        .autostart-retention-bunny-eye-left { left: 35px; }
        .autostart-retention-bunny-eye-right { right: 35px; }

        .autostart-retention-bunny-blush {
            position: absolute;
            top: 56px;
            width: 24px;
            height: 12px;
            border-radius: 50%;
            background: #ffc2d4;
            filter: blur(4px);
            opacity: 0.58;
            transition: all 0.34s ease;
        }

        .autostart-retention-bunny-blush-left { left: 23px; }
        .autostart-retention-bunny-blush-right { right: 23px; }

        .autostart-retention-bunny-mouth {
            position: absolute;
            top: 58px;
            left: 50%;
            display: flex;
            justify-content: center;
            width: 22px;
            height: 10px;
            transform: translateX(-50%);
            transition: all 0.34s ease;
        }

        .autostart-retention-bunny-mouth::before,
        .autostart-retention-bunny-mouth::after {
            content: '';
            width: 11px;
            height: 9px;
            border-bottom: 3px solid #2c3a4a;
            border-radius: 50%;
            transition: all 0.34s ease;
        }

        .autostart-retention-bunny-mouth::before {
            margin-right: -2px;
            border-right: 3px solid #2c3a4a;
            border-bottom-right-radius: 12px;
            transform: rotate(15deg);
        }

        .autostart-retention-bunny-mouth::after {
            margin-left: -2px;
            border-left: 3px solid #2c3a4a;
            border-bottom-left-radius: 12px;
            transform: rotate(-15deg);
        }

        .autostart-retention-bunny-paw {
            position: absolute;
            bottom: -10px;
            z-index: 3;
            width: 34px;
            height: 42px;
            border-radius: 999px;
            background: linear-gradient(145deg, #ffffff, #e4f2ff);
            box-shadow: inset -4px -5px 10px rgba(142, 187, 230, 0.16), inset 4px 4px 10px rgba(255,255,255,0.9);
        }

        .autostart-retention-bunny-paw-left {
            left: 34px;
            transform: rotate(18deg);
        }

        .autostart-retention-bunny-paw-right {
            right: 34px;
            transform: rotate(-18deg);
        }

        .modal-dialog-autostart-retention .modal-header {
            padding: 4px 36px 8px;
            border-bottom: 0;
            text-align: center;
        }

        .modal-dialog-autostart-retention .modal-title {
            color: #26374d;
            font-size: 25px;
            font-weight: 900;
            line-height: 1.25;
        }

        .modal-dialog-autostart-retention .modal-body {
            padding: 10px 42px 8px;
            color: #61718a;
            font-size: 15px;
            font-weight: 700;
            line-height: 1.65;
            text-align: center;
            white-space: normal;
        }

        .modal-dialog-autostart-retention .modal-note {
            padding: 0 42px 24px !important;
            color: #8795aa !important;
            font-weight: 700;
            text-align: center;
        }

        .modal-dialog-autostart-retention .modal-footer {
            justify-content: center;
            gap: 18px;
            padding: 2px 28px 34px;
            border-top: 0;
        }

        .modal-dialog-autostart-retention .modal-btn {
            min-width: 132px;
            min-height: 46px;
            border-radius: 999px;
            font-weight: 900;
            box-shadow: inset 0 4px 8px rgba(255,255,255,0.48);
        }

        .modal-dialog-autostart-retention .modal-btn-primary {
            background: linear-gradient(135deg, #6bb0f2, #9dcbff);
            box-shadow: 0 14px 28px rgba(107,176,242,0.32), inset 0 4px 8px rgba(255,255,255,0.46);
        }

        .modal-dialog-autostart-retention .modal-btn-primary:hover {
            background: linear-gradient(135deg, #5fa8ee, #8ec2ff);
            transform: translateY(-3px);
        }

        .modal-dialog-autostart-retention .modal-btn-secondary {
            color: #8fa3c0;
            background: rgba(255, 255, 255, 0.78);
            box-shadow: 0 10px 20px rgba(95,135,190,0.12), inset 0 4px 8px rgba(255,255,255,0.72);
        }

        .modal-dialog-autostart-retention .modal-btn-secondary:hover {
            background: #ffffff;
            transform: translateY(-3px);
        }

        .modal-dialog-autostart-retention {
            --exit-yui-blue: #6bb0f2;
            --exit-yui-pink: #ffc2d4;
            --exit-cat-main: linear-gradient(145deg, #ffffff, #e4f2ff);
            --exit-cat-shadow: rgba(142, 187, 230, 0.18);
            --exit-cat-face: #2c3a4a;
            --exit-text-main: #26374d;
            --exit-text-sub: #61718a;
            --exit-card-border: rgba(255,255,255,0.88);
            --exit-card-bg: linear-gradient(180deg, rgba(255,249,251,0.96) 0%, rgba(238,247,255,0.96) 100%);
            --exit-card-shadow: rgba(95,135,190,0.24);
            --exit-button-stay: linear-gradient(135deg, #6bb0f2, #9dcbff);
            --exit-button-leave: rgba(255, 255, 255, 0.78);
            width: min(620px, calc(100vw - 72px));
            max-width: min(620px, calc(100vw - 72px));
            min-height: 220px;
            margin-top: 120px;
            padding: 46px 46px 32px;
            border: 2px solid var(--exit-card-border);
            border-radius: 45px;
            background: var(--exit-card-bg);
            box-shadow: 0 40px 80px var(--exit-card-shadow), inset 0 10px 20px rgba(255,255,255,0.78);
            backdrop-filter: blur(15px);
            -webkit-backdrop-filter: blur(15px);
            color: var(--exit-text-main);
        }

        .modal-dialog-autostart-retention .exit-retention-cat-backglow {
            position: absolute;
            top: -116px;
            left: 50%;
            z-index: 4;
            width: 220px;
            height: 150px;
            border-radius: 50%;
            background: rgba(255, 255, 255, 0.76);
            filter: blur(30px);
            transform: translateX(-50%);
            transition: opacity 0.6s ease, transform 0.72s cubic-bezier(0.23, 1, 0.32, 1), background 0.35s ease;
            pointer-events: none !important;
        }

        .modal-dialog-autostart-retention .exit-retention-cat-character {
            position: absolute;
            top: -132px;
            left: 50%;
            z-index: 22;
            width: 190px;
            height: 160px;
            transform: translateX(-50%);
            transition: opacity 0.54s ease, transform 0.56s cubic-bezier(0.34, 1.56, 0.64, 1);
            pointer-events: none !important;
        }

        .modal-dialog-autostart-retention .exit-retention-cat-heart {
            position: absolute;
            top: -16px;
            left: 50%;
            color: #ff85a2;
            font-size: 24px;
            font-weight: 900;
            opacity: 0;
            transform: translateX(-50%) translateY(10px) scale(0.4);
            transition: opacity 0.3s ease, transform 0.4s cubic-bezier(0.34, 1.56, 0.64, 1);
            pointer-events: none !important;
        }

        .modal-dialog-autostart-retention .exit-retention-cat-head-group {
            position: relative;
            width: 100%;
            height: 100%;
            animation: exitRetentionCatBreathe 3.5s infinite ease-in-out;
            transform-origin: bottom center;
            transition: transform 0.46s cubic-bezier(0.34, 1.56, 0.64, 1);
            pointer-events: none !important;
        }

        .modal-dialog-autostart-retention .exit-retention-cat-head {
            position: absolute;
            bottom: 0;
            z-index: 2;
            width: 100%;
            height: 140px;
            border-radius: 45% 45% 40% 40% / 60% 60% 40% 40%;
            background: var(--exit-cat-main);
            box-shadow: inset -8px -12px 25px var(--exit-cat-shadow), inset 8px 8px 20px rgba(255,255,255,0.95), 0 -5px 20px rgba(163,217,255,0.15);
            pointer-events: none !important;
        }

        .modal-dialog-autostart-retention .exit-retention-cat-ear {
            position: absolute;
            top: -10px;
            z-index: 1;
            width: 55px;
            height: 65px;
            background: var(--exit-cat-main);
            box-shadow: inset -4px -4px 10px var(--exit-cat-shadow), inset 4px 4px 10px rgba(255,255,255,0.95);
            transition: transform 0.46s cubic-bezier(0.34, 1.56, 0.64, 1);
            pointer-events: none !important;
        }

        .modal-dialog-autostart-retention .exit-retention-cat-ear--left { left: 8px; border-radius: 12px 40px 10px 10px; transform: rotate(-22deg); }
        .modal-dialog-autostart-retention .exit-retention-cat-ear--right { right: 8px; border-radius: 40px 12px 10px 10px; transform: rotate(22deg); }
        .modal-dialog-autostart-retention .exit-retention-cat-ear::after {
            content: '';
            position: absolute;
            bottom: 8px;
            width: 32px;
            height: 40px;
            border-radius: inherit;
            background: linear-gradient(180deg, #ffcce5, var(--exit-yui-pink));
            box-shadow: inset 0 2px 6px rgba(0,0,0,0.05);
            opacity: 0.9;
        }

        .modal-dialog-autostart-retention .exit-retention-cat-ear--left::after { left: 12px; transform: rotate(10deg); }
        .modal-dialog-autostart-retention .exit-retention-cat-ear--right::after { right: 12px; transform: rotate(-10deg); }
        .modal-dialog-autostart-retention .exit-retention-cat-face { position: absolute; top: 65px; left: 0; z-index: 3; width: 100%; height: 50px; transition: transform 0.35s ease; pointer-events: none !important; }
        .modal-dialog-autostart-retention .exit-retention-cat-eye { position: absolute; top: 10px; width: 16px; height: 16px; border-radius: 50%; background: var(--exit-cat-face); transition: all 0.36s cubic-bezier(0.34, 1.56, 0.64, 1); }
        .modal-dialog-autostart-retention .exit-retention-cat-eye--left { left: 48px; }
        .modal-dialog-autostart-retention .exit-retention-cat-eye--right { right: 48px; }
        .modal-dialog-autostart-retention .exit-retention-cat-eye::after { content: ''; position: absolute; top: 2px; right: 3px; width: 6px; height: 6px; border-radius: 50%; background: #ffffff; transition: all 0.3s ease; }
        .modal-dialog-autostart-retention .exit-retention-cat-mouth { position: absolute; top: 22px; left: 50%; display: flex; justify-content: center; width: 22px; height: 10px; transform: translateX(-50%); transition: all 0.36s ease; }
        .modal-dialog-autostart-retention .exit-retention-cat-mouth::before,
        .modal-dialog-autostart-retention .exit-retention-cat-mouth::after { content: ''; width: 11px; height: 9px; border-bottom: 3.5px solid var(--exit-cat-face); border-radius: 50%; transition: all 0.36s ease; }
        .modal-dialog-autostart-retention .exit-retention-cat-mouth::before { margin-right: -2px; border-right: 3.5px solid var(--exit-cat-face); border-bottom-right-radius: 12px; transform: rotate(15deg); }
        .modal-dialog-autostart-retention .exit-retention-cat-mouth::after { margin-left: -2px; border-left: 3.5px solid var(--exit-cat-face); border-bottom-left-radius: 12px; transform: rotate(-15deg); }
        .modal-dialog-autostart-retention .exit-retention-cat-blush { position: absolute; top: 20px; width: 24px; height: 12px; border-radius: 50%; background: var(--exit-yui-pink); filter: blur(4px); opacity: 0.72; transition: all 0.36s ease; }
        .modal-dialog-autostart-retention .exit-retention-cat-blush--left { left: 25px; }
        .modal-dialog-autostart-retention .exit-retention-cat-blush--right { right: 25px; }
        .modal-dialog-autostart-retention .exit-retention-cat-paw { position: absolute; bottom: -15px; z-index: 25; width: 40px; height: 50px; border-radius: 25px; background: var(--exit-cat-main); box-shadow: 0 6px 10px rgba(0,0,0,0.06), inset -4px -4px 10px var(--exit-cat-shadow), inset 4px 4px 10px rgba(255,255,255,0.95); transition: transform 0.4s cubic-bezier(0.34, 1.56, 0.64, 1), opacity 0.36s ease; pointer-events: none !important; }
        .modal-dialog-autostart-retention .exit-retention-cat-paw--left { left: 35px; transform: rotate(20deg); }
        .modal-dialog-autostart-retention .exit-retention-cat-paw--right { right: 35px; transform: rotate(-20deg); }

        .modal-dialog-autostart-retention .modal-header {
            padding: 0;
        }

        .modal-dialog-autostart-retention .modal-title {
            margin: 0 0 10px;
            color: var(--exit-text-main);
            font-size: 29px;
            font-weight: 900;
            line-height: 1.22;
            letter-spacing: 0;
            overflow-wrap: anywhere;
        }

        .modal-dialog-autostart-retention .modal-body {
            padding: 0;
            color: var(--exit-text-sub);
            font-size: 16px;
            font-weight: 800;
            line-height: 1.45;
            overflow-wrap: anywhere;
        }

        .modal-dialog-autostart-retention .modal-note {
            padding: 8px 0 0 !important;
            color: var(--exit-text-sub) !important;
            font-size: 13px;
            font-weight: 800;
            line-height: 1.45;
            opacity: 0.78;
        }

        .modal-dialog-autostart-retention .modal-footer {
            gap: 24px;
            padding: 30px 0 0;
        }

        .modal-dialog-autostart-retention .modal-btn {
            min-width: 170px;
            min-height: 0;
            padding: 16px 42px;
            border: 0;
            border-radius: 30px;
            color: #ffffff;
            font-size: 18px;
            font-weight: 900;
            line-height: 1.2;
            letter-spacing: 0;
            white-space: nowrap;
            cursor: pointer;
            position: relative;
            overflow: hidden;
            transition: transform 0.32s cubic-bezier(0.175, 0.885, 0.32, 1.275), box-shadow 0.22s ease, filter 0.22s ease;
        }

        .modal-dialog-autostart-retention .modal-btn-primary { background: var(--exit-button-stay); box-shadow: 0 15px 30px rgba(107,176,242,0.35), inset 0 4px 8px rgba(255,255,255,0.46); }
        .modal-dialog-autostart-retention .modal-btn-secondary { background: var(--exit-button-leave); color: #8fa3c0; box-shadow: 0 10px 20px rgba(0,0,0,0.06), inset 0 4px 8px rgba(255,255,255,0.72); }
        .modal-dialog-autostart-retention .modal-btn:hover,
        .modal-dialog-autostart-retention .modal-btn:focus-visible { transform: translateY(-6px) scale(1.05); outline: none; filter: brightness(1.04); }
        .modal-dialog-autostart-retention .modal-btn:active { transform: scale(0.95); }

        .modal-dialog-autostart-retention.state-curious .exit-retention-cat-head-group { transform: rotate(10deg) translateY(2px); }
        .modal-dialog-autostart-retention.state-curious .exit-retention-cat-ear--left { transform: rotate(-8deg); }
        .modal-dialog-autostart-retention.state-curious .exit-retention-cat-eye { transform: scale(1.13); }
        .modal-dialog-autostart-retention.state-happy .exit-retention-cat-character { transform: translateX(-50%) translateY(18px); }
        .modal-dialog-autostart-retention.state-happy .exit-retention-cat-eye { top: 16px; height: 5px; border-radius: 10px; transform: scaleX(1.2); }
        .modal-dialog-autostart-retention.state-happy .exit-retention-cat-eye::after { opacity: 0; }
        .modal-dialog-autostart-retention.state-happy .exit-retention-cat-mouth::before,
        .modal-dialog-autostart-retention.state-happy .exit-retention-cat-mouth::after { border-bottom: 0; border-top: 3.5px solid var(--exit-cat-face); }
        .modal-dialog-autostart-retention.state-happy .exit-retention-cat-blush { background: #ff7e5f; transform: scale(1.4); opacity: 0.92; }
        .modal-dialog-autostart-retention.state-happy .exit-retention-cat-paw { transform: translateY(-8px) rotate(0deg); }
        .modal-dialog-autostart-retention.state-happy .exit-retention-cat-heart { opacity: 1; transform: translateX(-50%) translateY(-34px) scale(1); }
        .modal-dialog-autostart-retention.state-sad .exit-retention-cat-head-group { transform: translateY(12px); animation: exitRetentionSadTremble 0.3s infinite; }
        .modal-dialog-autostart-retention.state-sad .exit-retention-cat-ear--left { transform: rotate(-65deg) translateY(8px) translateX(-5px); }
        .modal-dialog-autostart-retention.state-sad .exit-retention-cat-ear--right { transform: rotate(65deg) translateY(8px) translateX(5px); }
        .modal-dialog-autostart-retention.state-sad .exit-retention-cat-eye { transform: scale(1.2); background: #2c3a4a; box-shadow: inset 0 -4px 6px rgba(163,217,255,0.8); }
        .modal-dialog-autostart-retention.state-sad .exit-retention-cat-eye::after { top: 5px; right: 2px; width: 9px; height: 9px; box-shadow: -3px -3px 0 rgba(255,255,255,0.6); }
        .modal-dialog-autostart-retention.state-sad .exit-retention-cat-mouth::before,
        .modal-dialog-autostart-retention.state-sad .exit-retention-cat-mouth::after { border-bottom: 0; border-top: 3.5px solid var(--exit-cat-face); }
        .modal-dialog-autostart-retention.state-sad .exit-retention-cat-backglow { background: rgba(163,217,255,0.45); }

        .modal-dialog-autostart-retention.state-curious .autostart-retention-bunny-head {
            animation: none;
            transform: translateX(-50%) rotate(8deg) translateY(3px);
        }

        .modal-dialog-autostart-retention.state-curious .autostart-retention-bunny-ear-left {
            transform: rotate(-8deg);
        }

        .modal-dialog-autostart-retention.state-curious .autostart-retention-bunny-eye {
            transform: scale(1.12);
        }

        .modal-dialog-autostart-retention.state-happy .autostart-retention-bunny {
            transform: translateX(-50%) translateY(14px);
        }

        .modal-dialog-autostart-retention.state-happy .autostart-retention-bunny-eye {
            top: 46px;
            height: 5px;
            border-radius: 10px;
            transform: scaleX(1.2);
        }

        .modal-dialog-autostart-retention.state-happy .autostart-retention-bunny-eye::after {
            opacity: 0;
        }

        .modal-dialog-autostart-retention.state-happy .autostart-retention-bunny-mouth::before,
        .modal-dialog-autostart-retention.state-happy .autostart-retention-bunny-mouth::after {
            border-bottom: 0;
            border-top: 3px solid #2c3a4a;
        }

        .modal-dialog-autostart-retention.state-happy .autostart-retention-bunny-blush {
            background: #ff8fb2;
            opacity: 0.88;
            transform: scale(1.25);
        }

        .modal-dialog-autostart-retention.state-happy .autostart-retention-bunny-paw {
            transform: translateY(-7px) rotate(0deg);
        }

        .modal-dialog-autostart-retention.state-happy .autostart-retention-bunny-heart {
            opacity: 1;
            transform: translateX(-50%) translateY(-34px) scale(1);
        }

        .modal-dialog-autostart-retention.state-sad .autostart-retention-bunny-head {
            animation: autostartRetentionSadTremble 0.3s infinite;
        }

        .modal-dialog-autostart-retention.state-sad .autostart-retention-bunny-ear-left {
            transform: rotate(-58deg) translateY(8px) translateX(-4px);
        }

        .modal-dialog-autostart-retention.state-sad .autostart-retention-bunny-ear-right {
            transform: rotate(58deg) translateY(8px) translateX(4px);
        }

        .modal-dialog-autostart-retention.state-sad .autostart-retention-bunny-eye {
            transform: scale(1.16);
            box-shadow: inset 0 -4px 6px rgba(163,217,255,0.8);
        }

        .modal-dialog-autostart-retention.state-sad .autostart-retention-bunny-eye::after {
            top: 5px;
            right: 2px;
            width: 8px;
            height: 8px;
            box-shadow: -3px -3px 0 rgba(255,255,255,0.6);
        }

        .modal-dialog-autostart-retention.state-sad .autostart-retention-bunny-mouth::before,
        .modal-dialog-autostart-retention.state-sad .autostart-retention-bunny-mouth::after {
            border-bottom: 0;
            border-top: 3px solid #2c3a4a;
        }

        @keyframes autostartRetentionBreathe {
            0%, 100% { transform: translateX(-50%) translateY(0); }
            50% { transform: translateX(-50%) translateY(4px); }
        }

        @keyframes autostartRetentionSadTremble {
            0%, 100% { transform: translateX(-50%) translateY(9px); }
            50% { transform: translateX(calc(-50% + 1px)) translateY(9px); }
        }

        @keyframes exitRetentionCatBreathe {
            0%, 100% { transform: scaleY(1); }
            50% { transform: scaleY(0.97) translateY(3px); }
        }

        @keyframes exitRetentionSadTremble {
            0%, 100% { transform: translateY(12px) translateX(0); }
            50% { transform: translateY(12px) translateX(1px); }
        }

        [data-theme="dark"] .modal-overlay-autostart-retention {
            background: rgba(8, 13, 22, 0.62);
        }

        [data-theme="dark"] .modal-dialog-autostart-retention {
            border-color: rgba(255, 255, 255, 0.08);
            background: linear-gradient(180deg, #1f2837 0%, #121b27 100%);
            box-shadow: 0 24px 58px rgba(0,0,0,0.34), inset 0 1px 0 rgba(255,255,255,0.08);
        }

        [data-theme="dark"] .modal-dialog-autostart-retention .modal-title {
            color: #f5f7fb;
        }

        [data-theme="dark"] .modal-dialog-autostart-retention .modal-body {
            color: #c8d4e5;
        }

        [data-theme="dark"] .modal-dialog-autostart-retention .modal-note {
            color: #9dafc6 !important;
        }

        [data-theme="dark"] .modal-dialog-autostart-retention .modal-btn-secondary {
            color: #d2deee;
            background: rgba(255, 255, 255, 0.10);
            box-shadow: 0 10px 20px rgba(0,0,0,0.22), inset 0 4px 8px rgba(255,255,255,0.10);
        }
    `;
    document.head.appendChild(style);

    /**
     * 创建模态对话框
     */
    function emitDecisionPromptLifecycleEvent(type, detail) {
        try {
            window.dispatchEvent(new CustomEvent(type, {
                detail: Object.assign({ source: 'common-dialogs' }, detail || {})
            }));
        } catch (_) {
            // ignore
        }
    }

    function temporarilyHideReactChatOverlayForModal(modalConfig) {
        if (!modalConfig || modalConfig.skin !== 'autostart-retention') {
            return function noop() {};
        }
        const overlay = document.getElementById('react-chat-window-overlay');
        if (!overlay || overlay.hidden) {
            return function noop() {};
        }

        const body = document.body;
        const hadOpenClass = !!(
            body
            && body.classList
            && typeof body.classList.contains === 'function'
            && body.classList.contains('react-chat-window-open')
        );
        overlay.hidden = true;
        if (body && body.classList && typeof body.classList.remove === 'function') {
            body.classList.remove('react-chat-window-open');
        }

        return function restoreReactChatOverlay() {
            if (overlay.hidden) {
                overlay.hidden = false;
            }
            if (hadOpenClass && body && body.classList && typeof body.classList.add === 'function') {
                body.classList.add('react-chat-window-open');
            }
        };
    }

    function createModal(config) {
        return new Promise((resolve) => {
            const modalConfig = config || {};
            const isAutostartRetentionSkin = modalConfig.skin === 'autostart-retention';
            const isDecisionPrompt = modalConfig.type === 'decision';
            const restoreObscuredUi = temporarilyHideReactChatOverlayForModal(modalConfig);
            let settled = false;
            const dismissValue = Object.prototype.hasOwnProperty.call(modalConfig, 'dismissValue')
                ? modalConfig.dismissValue
                : (modalConfig.type === 'prompt'
                    ? null
                    : (modalConfig.type === 'decision' ? NO_IMPLICIT_CLOSE : false));

            // 创建遮罩层
            const overlay = document.createElement('div');
            overlay.className = 'modal-overlay';
            if (isAutostartRetentionSkin) {
                overlay.classList.add('modal-overlay-autostart-retention');
            }

            // 创建对话框
            const dialog = document.createElement('div');
            dialog.className = 'modal-dialog';
            if (isAutostartRetentionSkin) {
                dialog.classList.add('modal-dialog-autostart-retention');
            }
            dialog.setAttribute('role', 'dialog');
            dialog.setAttribute('aria-modal', 'true');
            dialog.setAttribute('aria-label', modalConfig.title || 'Dialog');
            if (modalConfig.maxWidth) {
                dialog.style.maxWidth = String(modalConfig.maxWidth);
            }

            if (isAutostartRetentionSkin) {
                const backglow = document.createElement('div');
                backglow.className = 'exit-retention-cat-backglow';
                backglow.setAttribute('aria-hidden', 'true');
                dialog.appendChild(backglow);

                const catCharacter = document.createElement('div');
                catCharacter.className = 'exit-retention-cat-character';
                catCharacter.setAttribute('aria-hidden', 'true');
                catCharacter.innerHTML = [
                    '<div class="exit-retention-cat-heart">♥</div>',
                    '<div class="exit-retention-cat-head-group">',
                    '  <div class="exit-retention-cat-ear exit-retention-cat-ear--left"></div>',
                    '  <div class="exit-retention-cat-ear exit-retention-cat-ear--right"></div>',
                    '  <div class="exit-retention-cat-head">',
                    '    <div class="exit-retention-cat-face">',
                    '      <div class="exit-retention-cat-blush exit-retention-cat-blush--left"></div>',
                    '      <div class="exit-retention-cat-eye exit-retention-cat-eye--left"></div>',
                    '      <div class="exit-retention-cat-mouth"></div>',
                    '      <div class="exit-retention-cat-eye exit-retention-cat-eye--right"></div>',
                    '      <div class="exit-retention-cat-blush exit-retention-cat-blush--right"></div>',
                    '    </div>',
                    '  </div>',
                    '</div>',
                    '<div class="exit-retention-cat-paw exit-retention-cat-paw--left"></div>',
                    '<div class="exit-retention-cat-paw exit-retention-cat-paw--right"></div>',
                ].join('');
                dialog.appendChild(catCharacter);
            }

            function setAutostartRetentionState(nextState) {
                if (!isAutostartRetentionSkin) {
                    return;
                }
                dialog.classList.remove('state-curious', 'state-happy', 'state-sad');
                if (nextState) {
                    dialog.classList.add(nextState);
                }
            }

            function bindAutostartRetentionState(target, nextState) {
                if (!isAutostartRetentionSkin || !target) {
                    return;
                }
                target.addEventListener('mouseenter', () => setAutostartRetentionState(nextState));
                target.addEventListener('mouseleave', () => setAutostartRetentionState(''));
                target.addEventListener('focus', () => setAutostartRetentionState(nextState));
                target.addEventListener('blur', () => setAutostartRetentionState(''));
            }

            // 创建标题
            let header = null;
            if (modalConfig.title) {
                header = document.createElement('div');
                header.className = 'modal-header';
                const title = document.createElement('h3');
                title.className = 'modal-title';
                title.textContent = modalConfig.title;
                header.appendChild(title);
                dialog.appendChild(header);
            }

            // 创建内容
            const body = document.createElement('div');
            body.className = 'modal-body';
            applyModalTextContent(body, modalConfig.message || '', modalConfig.messageFormat);

            // 如果是 prompt 类型，添加输入框
            let input = null;
            if (modalConfig.type === 'prompt') {
                input = document.createElement('input');
                input.type = 'text';
                input.className = 'modal-input';
                input.value = modalConfig.defaultValue || '';
                input.placeholder = modalConfig.placeholder || '';

                // 可选的输入属性（如 maxlength 等）
                if (modalConfig.inputAttributes && typeof modalConfig.inputAttributes === 'object') {
                    Object.keys(modalConfig.inputAttributes).forEach((k) => {
                        const v = modalConfig.inputAttributes[k];
                        if (v === undefined || v === null) return;
                        // 兼容部分 DOM 属性（如 maxLength）
                        if (k in input) {
                            try { input[k] = v; } catch (e) { /* ignore */ }
                        }
                        try { input.setAttribute(k, String(v)); } catch (e) { /* ignore */ }
                    });
                }

                const normalizeValue = () => {
                    if (typeof modalConfig.normalize === 'function') {
                        try {
                            const next = modalConfig.normalize(input.value);
                            if (typeof next === 'string' && next !== input.value) {
                                input.value = next;
                            }
                        } catch (e) {
                            // ignore
                        }
                    }
                };

                const validateValue = () => {
                    if (typeof modalConfig.validator === 'function') {
                        try {
                            const err = modalConfig.validator(input.value);
                            if (err) {
                                input.setCustomValidity(String(err));
                                return false;
                            }
                        } catch (e) {
                            // ignore
                        }
                    }
                    input.setCustomValidity('');
                    return true;
                };

                // 绑定输入事件（支持 IME）
                const onInput = () => {
                    normalizeValue();
                    validateValue();
                    if (typeof modalConfig.onInput === 'function') {
                        try { modalConfig.onInput(input); } catch (e) { /* ignore */ }
                    }
                };
                input.addEventListener('input', onInput);
                input.addEventListener('compositionend', onInput);
                // 初次校验
                setTimeout(onInput, 0);
                body.appendChild(input);
            }

            dialog.appendChild(body);
            bindAutostartRetentionState(header, 'state-curious');
            bindAutostartRetentionState(body, 'state-curious');

            let note = null;
            if (modalConfig.note) {
                note = document.createElement('div');
                note.className = 'modal-note';
                note.style.cssText = 'padding:0 24px 20px;color:#64748b;font-size:13px;line-height:1.6;';
                applyModalTextContent(note, modalConfig.note, modalConfig.noteFormat);
                dialog.appendChild(note);
                bindAutostartRetentionState(note, 'state-curious');
            }

            // 创建按钮区域
            const footer = document.createElement('div');
            footer.className = 'modal-footer';

            function finish(value) {
                if (settled) return;
                settled = true;
                if (typeof modalConfig.onResolve === 'function') {
                    try {
                        modalConfig.onResolve(value, {
                            overlay: overlay,
                            dialog: dialog,
                        });
                    } catch (error) {
                        console.warn('[Dialog] onResolve failed:', error);
                    }
                }
                document.removeEventListener('keydown', escHandler);
                overlay.style.animation = 'fadeOut 0.2s ease-out';
                setTimeout(() => {
                    if (overlay.parentNode) {
                        overlay.parentNode.removeChild(overlay);
                    }
                    if (isDecisionPrompt) {
                        emitDecisionPromptLifecycleEvent('neko:decision-prompt-closed', {
                            skin: modalConfig.skin || '',
                            type: modalConfig.type || ''
                        });
                    }
                    restoreObscuredUi();
                    resolve(value);
                }, 200);
            }

            function dismissIfAllowed() {
                if (dismissValue === NO_IMPLICIT_CLOSE) {
                    return;
                }
                finish(dismissValue);
            }

            // 根据类型创建按钮
            if (modalConfig.type === 'alert') {
                const okBtn = document.createElement('button');
                okBtn.className = 'modal-btn modal-btn-primary';
                let okText = modalConfig.okText;
                if (!okText) {
                    okText = safeT('common.ok', '确定');
                }
                okBtn.textContent = okText;
                okBtn.onclick = () => {
                    finish(true);
                };
                footer.appendChild(okBtn);
            } else if (modalConfig.type === 'confirm') {
                const cancelBtn = document.createElement('button');
                cancelBtn.className = 'modal-btn modal-btn-secondary';
                let cancelText = modalConfig.cancelText;
                if (!cancelText) {
                    cancelText = safeT('common.cancel', '取消');
                }
                cancelBtn.textContent = cancelText;
                cancelBtn.onclick = () => {
                    finish(false);
                };
                footer.appendChild(cancelBtn);

                const okBtn = document.createElement('button');
                okBtn.className = modalConfig.danger ? 'modal-btn modal-btn-danger' : 'modal-btn modal-btn-primary';
                let okText = modalConfig.okText;
                if (!okText) {
                    okText = safeT('common.ok', '确定');
                }
                okBtn.textContent = okText;
                okBtn.onclick = () => {
                    finish(true);
                };
                footer.appendChild(okBtn);
            } else if (modalConfig.type === 'prompt') {
                const cancelBtn = document.createElement('button');
                cancelBtn.className = 'modal-btn modal-btn-secondary';
                cancelBtn.textContent = modalConfig.cancelText || safeT('common.cancel', '取消');
                cancelBtn.onclick = () => {
                    finish(null);
                };
                footer.appendChild(cancelBtn);

                const okBtn = document.createElement('button');
                okBtn.className = 'modal-btn modal-btn-primary';
                okBtn.textContent = modalConfig.okText || safeT('common.ok', '确定');
                okBtn.onclick = () => {
                    // 确认前先归一化和校验
                    if (typeof modalConfig.normalize === 'function') {
                        try { input.value = modalConfig.normalize(input.value); } catch (e) { /* ignore */ }
                    }
                    if (typeof modalConfig.validator === 'function') {
                        let err = '';
                        try { err = modalConfig.validator(input.value) || ''; } catch (e) { err = ''; }
                        if (err) {
                            input.setCustomValidity(String(err));
                            if (typeof input.reportValidity === 'function') input.reportValidity();
                            return;
                        }
                    }
                    input.setCustomValidity('');
                    finish(input.value);
                };
                footer.appendChild(okBtn);

                // Enter 键确认
                input.addEventListener('keydown', (e) => {
                    if (e.key === 'Enter') {
                        // Enter 行为与确定按钮一致
                        if (typeof modalConfig.normalize === 'function') {
                            try { input.value = modalConfig.normalize(input.value); } catch (e) { /* ignore */ }
                        }
                        if (typeof modalConfig.validator === 'function') {
                            let err = '';
                            try { err = modalConfig.validator(input.value) || ''; } catch (e) { err = ''; }
                            if (err) {
                                input.setCustomValidity(String(err));
                                if (typeof input.reportValidity === 'function') input.reportValidity();
                                return;
                            }
                        }
                        input.setCustomValidity('');
                        finish(input.value);
                    } else if (e.key === 'Escape' && modalConfig.closeOnEscape !== false) {
                        dismissIfAllowed();
                    }
                });
            } else if (modalConfig.type === 'decision') {
                const buttons = Array.isArray(modalConfig.buttons) && modalConfig.buttons.length > 0
                    ? modalConfig.buttons
                    : [{
                        value: 'confirm',
                        text: safeT('common.confirm', '确认'),
                        variant: 'primary'
                    }];

                buttons.forEach((buttonConfig, index) => {
                    const button = document.createElement('button');
                    const variant = buttonConfig.variant === 'danger'
                        ? 'modal-btn-danger'
                        : (buttonConfig.variant === 'primary' ? 'modal-btn-primary' : 'modal-btn-secondary');
                    button.className = 'modal-btn ' + variant;
                    button.textContent = String(buttonConfig.text || ('Button ' + (index + 1)));
                    if (isAutostartRetentionSkin) {
                        if (buttonConfig.value === 'accept') {
                            bindAutostartRetentionState(button, 'state-happy');
                        } else if (buttonConfig.value === 'later' || buttonConfig.value === 'never') {
                            bindAutostartRetentionState(button, 'state-sad');
                        }
                    }
                    button.onclick = () => {
                        finish(buttonConfig.value);
                    };
                    footer.appendChild(button);
                });
            }

            dialog.appendChild(footer);
            overlay.appendChild(dialog);

            // 点击遮罩层关闭（可选）
            if (modalConfig.closeOnClickOutside !== false) {
                overlay.addEventListener('click', (e) => {
                    if (e.target === overlay) {
                        dismissIfAllowed();
                    }
                });
            }

            // ESC 键关闭
            const escHandler = (e) => {
                if (modalConfig.closeOnEscape === false) {
                    return;
                }
                if (e.key === 'Escape') {
                    dismissIfAllowed();
                }
            };
            document.addEventListener('keydown', escHandler);

            // 添加到页面
            document.body.appendChild(overlay);
            if (isDecisionPrompt) {
                emitDecisionPromptLifecycleEvent('neko:decision-prompt-opened', {
                    skin: modalConfig.skin || '',
                    type: modalConfig.type || ''
                });
            }

            if (typeof modalConfig.onShown === 'function') {
                const notifyShown = function () {
                    Promise.resolve()
                        .then(function () {
                            return modalConfig.onShown({
                                overlay: overlay,
                                dialog: dialog,
                            });
                        })
                        .catch(function (error) {
                            console.warn('[Dialog] onShown failed:', error);
                        });
                };
                if (typeof requestAnimationFrame === 'function') {
                    requestAnimationFrame(notifyShown);
                } else {
                    setTimeout(notifyShown, 0);
                }
            }

            // 自动聚焦
            setTimeout(() => {
                if (input) {
                    input.focus();
                    input.select();
                } else {
                    const primaryBtn = footer.querySelector('.modal-btn-primary');
                    const firstBtn = footer.querySelector('.modal-btn');
                    if (primaryBtn) {
                        primaryBtn.focus();
                    } else if (firstBtn) {
                        firstBtn.focus();
                    }
                }
            }, 100);
        });
    }

    /**
     * 显示警告对话框（替代 alert）
     * @param {string} message - 消息内容
     * @param {string} title - 标题（可选）
     * @returns {Promise<boolean>}
     */
    window.showAlert = function(message, title = null) {
        if (title === null) {
            title = safeT('common.alert', '提示');
        }
        return createModal({
            type: 'alert',
            title: title,
            message: message,
        });
    };

    /**
     * 显示确认对话框（替代 confirm）
     * @param {string} message - 消息内容
     * @param {string} title - 标题（可选）
     * @param {Object} options - 额外选项
     * @returns {Promise<boolean>}
     */
    window.showConfirm = function(message, title = null, options = {}) {
        console.log('[showConfirm] 被调用，参数:', { message, title, options });
        if (title === null) {
            title = safeT('common.confirm', '确认');
        }
        console.log('[showConfirm] 创建对话框，title:', title, 'message:', message);
        const promise = createModal({
            type: 'confirm',
            title: title,
            message: message,
            okText: options.okText,
            cancelText: options.cancelText,
            danger: options.danger || false,
        });
        console.log('[showConfirm] 返回 Promise:', promise);
        return promise;
    };

    /**
     * 显示输入对话框（替代 prompt）
     * @param {string} message - 消息内容
     * @param {string} defaultValue - 默认值
     * @param {string} title - 标题（可选）
     * @returns {Promise<string|null>}
     */
    window.showPrompt = function(message, defaultValue = '', title = null, options = {}) {
        if (title === null) {
            title = safeT('common.input', '输入');
        }
        return createModal({
            type: 'prompt',
            title: title,
            message: message,
            defaultValue: defaultValue,
            placeholder: options.placeholder,
            okText: options.okText,
            cancelText: options.cancelText,
            inputAttributes: options.inputAttributes,
            normalize: options.normalize,
            validator: options.validator,
            onInput: options.onInput,
        });
    };

    window.showDecisionPrompt = function(config = {}) {
        return new Promise((resolve, reject) => {
            _decisionPromptQueue.push({ config, resolve, reject });

            const drainDecisionPromptQueue = () => {
                if (_decisionPromptActive || _decisionPromptQueue.length === 0) {
                    return;
                }

                const nextPrompt = _decisionPromptQueue.shift();
                _decisionPromptActive = true;
                createModal(Object.assign({}, nextPrompt.config, { type: 'decision' }))
                    .then((value) => {
                        nextPrompt.resolve(value);
                    })
                    .catch((error) => {
                        nextPrompt.reject(error);
                    })
                    .finally(() => {
                        _decisionPromptActive = false;
                        drainDecisionPromptQueue();
                    });
            };

            drainDecisionPromptQueue();
        });
    };

    // 添加 fadeOut 动画
    const fadeOutStyle = document.createElement('style');
    fadeOutStyle.textContent = `
        @keyframes fadeOut {
            from { opacity: 1; }
            to { opacity: 0; }
        }
    `;
    document.head.appendChild(fadeOutStyle);

})();

/**
 * 禁用浏览器缩放和关闭标签页快捷键
 * 阻止 Ctrl+/Ctrl-、Ctrl+滚轮 和 Ctrl/Cmd+W 关闭标签页
 */
(function() {
    'use strict';
    
    // 禁用 Ctrl+/-、Ctrl+0 和 Ctrl/Cmd+W 键盘快捷键
    document.addEventListener('keydown', function(event) {
        // 检测 Ctrl 或 Cmd 键（Mac）
        if (event.ctrlKey || event.metaKey) {
            // 禁用加号、减号、等号（=键位常用作+）和数字0
            if (event.key === '+' || 
                event.key === '=' || 
                event.key === '-' || 
                event.key === '_' || 
                event.key === '0' ||
                event.key === 'w' ||
                event.key === 'W') {
                event.preventDefault();
                return false;
            }
        }
    }, { passive: false });
    
    // 禁用 Ctrl + 滚轮缩放
    document.addEventListener('wheel', function(event) {
        if (event.ctrlKey || event.metaKey) {
            event.preventDefault();
            return false;
        }
    }, { passive: false });
    
    // 禁用触控板的双指缩放手势（适用于部分浏览器）
    document.addEventListener('gesturestart', function(event) {
        event.preventDefault();
        return false;
    }, { passive: false });
    
    document.addEventListener('gesturechange', function(event) {
        event.preventDefault();
        return false;
    }, { passive: false });
    
    document.addEventListener('gestureend', function(event) {
        event.preventDefault();
        return false;
    }, { passive: false });
    
    console.log('页面缩放及关闭标签页快捷键已禁用');
})();

/**
 * 共享窗口管理工具
 * 用于防止重复打开同一个窗口
 */
(function() {
    'use strict';
    
    // 初始化已打开窗口的存储
    if (!window._openedWindows) {
        window._openedWindows = {};
    }
    const SHARED_NAMED_WINDOW_PREFIX = 'neko:named-window:';
    const SHARED_NAMED_WINDOW_FOCUS_PREFIX = 'neko:named-window-focus:';
    const SHARED_NAMED_WINDOW_TTL_MS = 5000;
    
    /**
     * 打开或聚焦窗口
     * 如果同名窗口已存在且未关闭，则聚焦到该窗口
     * 否则打开新窗口
     * 
     * @param {string} url - 要打开的 URL
     * @param {string} windowName - 窗口名称（用于标识和重用）
     * @param {string} [features] - 窗口特性（可选，默认为标准设置窗口）
     * @param {{navigateOnReuse?: boolean}} [options] - 复用同名窗口时是否强制导航
     * @returns {Window|null} - 返回窗口对象
     */
    window.openOrFocusWindow = function(url, windowName, features, options) {
        // 默认窗口特性（移除 noopener 以便获取窗口引用）
        const defaultFeatures = 'width=1000,height=800,menubar=no,toolbar=no,location=no,status=no';
        features = features || defaultFeatures;
        const targetUrl = resolveOpenedWindowUrl(url);
        const normalizedOptions = options && typeof options === 'object' ? options : {};

        // 检查窗口是否已打开且未关闭
        const existingWindow = window._openedWindows[windowName];
        if (existingWindow && !existingWindow.closed) {
            if (normalizedOptions.navigateOnReuse) {
                navigateOpenedWindow(existingWindow, targetUrl, !!normalizedOptions.navigateOnReuse);
            }
            applyOpenedWindowFeatures(existingWindow, features);
            requestOpenedWindowRestore(existingWindow);
            existingWindow.focus();
            return existingWindow;
        }

        if (!normalizedOptions.navigateOnReuse && isSharedNamedWindowActive(windowName)) {
            requestSharedNamedWindowFocus(windowName);
            return createSharedNamedWindowProxy(windowName);
        }

        // 没有本地 handle 且目标页未登记为活跃时，直接按 URL 打开。
        // 避免先打开 about:blank；部分 Electron/window.open handler 会因此卡住。
        const newWindow = window.open(targetUrl, windowName, features);
        if (newWindow) {
            cacheOpenedWindow(windowName, newWindow);
            applyOpenedWindowFeatures(newWindow, features);
            requestOpenedWindowRestore(newWindow);
        }
        return newWindow;
    };

    function isReusableNamedWindowName(windowName) {
        if (!windowName) return false;
        const name = String(windowName).toLowerCase();
        return name !== '_blank' && name !== '_self' && name !== '_parent' && name !== '_top';
    }

    function resolveOpenedWindowUrl(url) {
        try {
            return new URL(url, window.location.href).toString();
        } catch (_) {
            return url;
        }
    }

    function getSharedNamedWindowKey(windowName) {
        return SHARED_NAMED_WINDOW_PREFIX + String(windowName || '');
    }

    function getSharedNamedWindowFocusKey(windowName) {
        return SHARED_NAMED_WINDOW_FOCUS_PREFIX + String(windowName || '');
    }

    function isSharedNamedWindowActive(windowName) {
        if (!isReusableNamedWindowName(windowName)) return false;
        try {
            const storage = window.localStorage;
            if (!storage) return false;
            const raw = storage.getItem(getSharedNamedWindowKey(windowName));
            if (!raw) return false;
            const data = JSON.parse(raw);
            const timestamp = Number(data && data.timestamp);
            return Number.isFinite(timestamp) && (Date.now() - timestamp) < SHARED_NAMED_WINDOW_TTL_MS;
        } catch (_) {
            return false;
        }
    }

    function requestSharedNamedWindowFocus(windowName, payload) {
        if (!isReusableNamedWindowName(windowName)) return;
        const message = {
            type: payload ? 'neko:named-window-message' : 'neko:named-window-focus',
            windowName: windowName,
            payload: payload || null,
            timestamp: Date.now()
        };
        try {
            if ('BroadcastChannel' in window) {
                const channel = new BroadcastChannel('neko:named-window');
                channel.postMessage(message);
                channel.close();
            }
        } catch (_) {}
        try {
            window.localStorage.setItem(getSharedNamedWindowFocusKey(windowName), JSON.stringify(message));
        } catch (_) {}
    }

    function createSharedNamedWindowProxy(windowName) {
        const proxy = {
            focus: function() {
                requestSharedNamedWindowFocus(windowName);
            },
            close: function() {},
            postMessage: function(message) {
                requestSharedNamedWindowFocus(windowName, message);
            }
        };
        Object.defineProperty(proxy, 'closed', {
            get: function() {
                return !isSharedNamedWindowActive(windowName);
            }
        });
        return proxy;
    }

    function cacheOpenedWindow(windowName, targetWindow) {
        if (!windowName || !targetWindow) return;
        window._openedWindows[windowName] = targetWindow;

        // 监听窗口关闭事件，清理引用
        const checkClosed = setInterval(() => {
            if (targetWindow.closed) {
                clearInterval(checkClosed);
                // 只有当缓存的引用仍然是这个窗口时才删除
                // 防止在1秒内重新打开同名窗口时误删新窗口的引用
                if (window._openedWindows[windowName] === targetWindow) {
                    delete window._openedWindows[windowName];
                }
            }
        }, 1000);
    }

    function navigateOpenedWindow(targetWindow, url, replace) {
        if (!targetWindow || targetWindow.closed || !url) return;
        try {
            if (replace && targetWindow.location && typeof targetWindow.location.replace === 'function') {
                targetWindow.location.replace(url);
            } else {
                targetWindow.location.href = url;
            }
        } catch (_) {
            try {
                targetWindow.location.href = url;
            } catch (_) {}
        }
    }

    function getOpenedWindowFeatureNumber(features, name) {
        if (!features || !name) return null;
        const pattern = new RegExp('(?:^|,)\\s*' + name + '\\s*=\\s*(-?\\d+)', 'i');
        const match = String(features).match(pattern);
        if (!match) return null;
        const value = Number(match[1]);
        return Number.isFinite(value) ? value : null;
    }

    function applyOpenedWindowFeatures(targetWindow, features) {
        if (!targetWindow || targetWindow.closed || !features) return;
        const width = getOpenedWindowFeatureNumber(features, 'width');
        const height = getOpenedWindowFeatureNumber(features, 'height');
        const left = getOpenedWindowFeatureNumber(features, 'left');
        const top = getOpenedWindowFeatureNumber(features, 'top');
        try {
            if (Number.isFinite(width) && Number.isFinite(height) && width > 0 && height > 0) {
                targetWindow.resizeTo(width, height);
            }
        } catch (_) {}
        try {
            if (Number.isFinite(left) && Number.isFinite(top)) {
                targetWindow.moveTo(left, top);
            }
        } catch (_) {}
    }

    function requestOpenedWindowRestore(targetWindow) {
        if (!targetWindow || targetWindow.closed) return;
        try {
            targetWindow.postMessage({ type: 'neko:restore-window' }, window.location.origin);
        } catch (error) {
            // 目标窗口可能跨域或正在关闭，聚焦兜底即可
        }
    }

    window.requestOpenedWindowRestore = requestOpenedWindowRestore;

    /**
     * 计算「在当前所在显示器可用区域内居中」的 window.open features 字符串。
     * 多显示器下 window.open 的 left/top 是相对整个虚拟桌面原点的坐标，必须叠加当前屏幕
     * 偏移（screen.availLeft/availTop，回退 window.screenX/screenY），否则副屏打开会按主屏
     * 原点居中、跳回主屏。调用方负责把尺寸 clamp 好后传入最终窗口尺寸。
     *
     * @param {number} windowWidth - 最终窗口宽度
     * @param {number} windowHeight - 最终窗口高度
     * @returns {string}
     */
    function buildCenteredPopupFeatures(windowWidth, windowHeight) {
        const screenRef = window.screen || {};
        const width = Math.max(1, Math.floor(Number(windowWidth)) || 1);
        const height = Math.max(1, Math.floor(Number(windowHeight)) || 1);
        const availableWidth = Math.max(width, Number(screenRef.availWidth || screenRef.width) || width);
        const availableHeight = Math.max(height, Number(screenRef.availHeight || screenRef.height) || height);
        const screenLeft = Number.isFinite(screenRef.availLeft) ? screenRef.availLeft : (Number(window.screenX) || 0);
        const screenTop = Number.isFinite(screenRef.availTop) ? screenRef.availTop : (Number(window.screenY) || 0);
        const left = Math.round(screenLeft + Math.max(0, (availableWidth - width) / 2));
        const top = Math.round(screenTop + Math.max(0, (availableHeight - height) / 2));
        return `width=${width},height=${height},left=${left},top=${top},menubar=no,toolbar=no,location=no,status=no,resizable=yes,scrollbars=yes`;
    }

    window.buildCenteredPopupFeatures = buildCenteredPopupFeatures;

    function buildApiKeySettingsWindowFeatures(width = 1240, height = 940) {
        const availableWidth = Math.max(1, Number(window.screen && (window.screen.availWidth || window.screen.width)) || width);
        const availableHeight = Math.max(1, Number(window.screen && (window.screen.availHeight || window.screen.height)) || height);
        const windowWidth = Math.min(width, Math.max(720, availableWidth - 80));
        const windowHeight = Math.min(height, Math.max(560, availableHeight - 80));
        if (typeof window.buildCenteredPopupFeatures === 'function') {
            return window.buildCenteredPopupFeatures(windowWidth, windowHeight);
        }
        const left = Math.max(0, Math.floor((availableWidth - windowWidth) / 2));
        const top = Math.max(0, Math.floor((availableHeight - windowHeight) / 2));
        return `width=${windowWidth},height=${windowHeight},left=${left},top=${top},menubar=no,toolbar=no,location=no,status=no,resizable=yes,scrollbars=yes`;
    }

    window.buildApiKeySettingsWindowFeatures = buildApiKeySettingsWindowFeatures;

    window.addEventListener('message', function(event) {
        if (event.origin !== window.location.origin) return;
        if (!event.data || event.data.type !== 'neko:restore-window') return;
        const api = window.nekoWindowControl;
        if (!api || typeof api.restore !== 'function') return;
        Promise.resolve(api.restore()).catch(function() {
            // 非 Electron 环境下忽略
        });
    });
    
    /**
     * 关闭指定名称的窗口
     * 
     * @param {string} windowName - 窗口名称
     */
    window.closeNamedWindow = function(windowName) {
        const win = window._openedWindows[windowName];
        if (win && !win.closed) {
            win.close();
        }
        delete window._openedWindows[windowName];
    };
    
    /**
     * 统一的页面关闭函数
     * 适用于通过 window.open() 或 iframe 打开的页面
     * 
     * @param {string} [closeMessageType] - 发送给父窗口的消息类型（用于 iframe 模式）
     */
    window.closeCurrentPage = function(closeMessageType) {
        if (window.opener) {
            // 如果是通过 window.open() 打开的，直接关闭
            window.close();
        } else if (window.parent && window.parent !== window) {
            // 如果在 iframe 中，通知父窗口关闭
            if (closeMessageType) {
                window.parent.postMessage({ type: closeMessageType }, '*');
            } else {
                window.parent.postMessage({ type: 'close_page' }, '*');
            }
        } else {
            // 普通页面，返回上一页或主页
            if (window.history.length > 1) {
                window.history.back();
            } else {
                window.location.href = '/';
            }
        }
    };
    
    console.log('窗口管理工具已加载');
})();
