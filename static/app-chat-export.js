/**
 * app-chat-export.js — Chat export for the React-first N.E.K.O chat window.
 *
 * Reads chat messages from window.reactChatWindowHost.getState() (the typed
 * React state managed by app-react-chat-window.js), renders a preview modal
 * with per-message checkbox selection, and produces Markdown or Canvas-based
 * image downloads. Pure client-side — no backend endpoints are required.
 *
 * Triggered by the #exportConversationButton element in templates/index.html.
 *
 * Originally ported from wislap-N.E.K.O/static/app-chat-export.js, with all
 * DOM-scraping and in-place marquee selection removed and replaced by a
 * modal-driven selection UX that consumes the React ChatMessage schema.
 */
(function () {
    'use strict';

    var MAX_EXPORT_SELECTION = 100;
    var DEFAULT_USER_EXPORT_AVATAR = '/static/icons/avatar/master-avatar.png';

    // ======================== State ========================

    var state = {
        isPreparingPreview: false,
        isExporting: false,
        isCopying: false,
        exportFormat: 'image',
        imageExportFormat: 'png',
        imageExportStyle: 'neko',
        selectedIds: null,          // Set<string> of ChatMessage.id
        allMessages: [],            // latest snapshot of messages from host
        previewModal: null,         // { backdrop, panel, frame, img, ... }
        previewWindow: null,        // independent export window
        previewEscHandler: null,
        previewCache: new Map(),    // cacheKey -> { payload }
        previewCurrentCacheKey: '',
        isPreviewRendering: false,
        previewRenderToken: 0
    };

    // ======================== Utilities ========================

    function translateText(key, fallback, params) {
        if (typeof window.t === 'function') {
            try {
                var translated = params ? window.t(key, params) : window.t(key);
                if (translated && translated !== key) return translated;
            } catch (_) {}
        }
        if (params && fallback) {
            return String(fallback).replace(/\{\{(\w+)\}\}/g, function (_, name) {
                return Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : '';
            });
        }
        return fallback;
    }

    function translateLabel(key, fallback) {
        return translateText(key, fallback);
    }

    function showToast(key, fallback, duration) {
        if (typeof window.showStatusToast !== 'function') return;
        window.showStatusToast(translateLabel(key, fallback), duration || 3000);
    }

    function showToastMessage(message, duration) {
        if (typeof window.showStatusToast !== 'function') return;
        window.showStatusToast(String(message || ''), duration || 3000);
    }

    function logExportError(scope, error) {
        console.error('[app-chat-export] ' + scope + ':', error);
    }

    function getErrorMessage(error) {
        if (!error) return 'Unknown error';
        if (typeof error === 'string') return error;
        if (error && typeof error.message === 'string' && error.message) return error.message;
        try { return JSON.stringify(error); } catch (_) { return String(error); }
    }

    function escapeHtml(text) {
        return String(text == null ? '' : text).replace(/[&<>"']/g, function (char) {
            switch (char) {
                case '&': return '&amp;';
                case '<': return '&lt;';
                case '>': return '&gt;';
                case '"': return '&quot;';
                case '\'': return '&#39;';
                default: return char;
            }
        });
    }

    /** Return true when the given URL string uses a safe protocol. */
    function isSafeUrl(url) {
        if (!url) return false;
        try {
            // Handle protocol-relative or schemeless URLs gracefully
            var parsed = new URL(url, window.location.href);
            var protocol = parsed.protocol;
            return protocol === 'http:' || protocol === 'https:' || protocol === 'data:' || protocol === 'blob:';
        } catch (_) {
            return false;
        }
    }

    /**
     * Strip unsafe protocol URLs from anchor href and img/iframe src attributes
     * inside an HTML string. Attributes that fail isSafeUrl are replaced with
     * a safe empty value so the surrounding markup is preserved.
     */
    function sanitizeHtmlUrls(html) {
        return String(html || '').replace(
            /(<(?:a|img|iframe)\b[^>]*?\s)(href|src)(=["'])([^"']*)(["'])/gi,
            function (match, before, attr, eq, value, quote) {
                if (isSafeUrl(value)) return match;
                // Replace dangerous URL with a harmless empty string
                return before + attr + eq + quote;
            }
        );
    }

    function escapeMarkdown(text) {
        return String(text == null ? '' : text).replace(/([\\`*_\{\}\[\]\(\)#+\-\.!>|])/g, '\\$1');
    }

    function padZero(value) {
        return String(value).padStart(2, '0');
    }

    function buildFileTimestamp(date) {
        return String(date.getFullYear())
            + padZero(date.getMonth() + 1)
            + padZero(date.getDate())
            + '-'
            + padZero(date.getHours())
            + padZero(date.getMinutes())
            + padZero(date.getSeconds());
    }

    function buildDisplayTimestamp(date) {
        try {
            return date.toLocaleString(document.documentElement.lang || undefined);
        } catch (_) {
            return date.toISOString();
        }
    }

    function getExportBaseFileName(date) {
        return 'neko-conversation-export-' + buildFileTimestamp(date);
    }

    function waitForNextPaint() {
        return new Promise(function (resolve) {
            if (typeof window.requestAnimationFrame === 'function') {
                window.requestAnimationFrame(function () { resolve(); });
                return;
            }
            setTimeout(resolve, 0);
        });
    }

    // ======================== React State Adapter ========================

    function getReactChatHost() {
        return window.reactChatWindowHost || null;
    }

    function getReactMessages() {
        var host = getReactChatHost();
        if (!host || typeof host.getState !== 'function') return [];
        try {
            var snapshot = host.getState();
            var list = (snapshot && Array.isArray(snapshot.messages)) ? snapshot.messages : [];
            return list.filter(function (message) {
                return message && message.id && Array.isArray(message.blocks);
            });
        } catch (error) {
            logExportError('getReactMessages', error);
            return [];
        }
    }

    function getRoleLabel(role) {
        if (role === 'user') return translateLabel('chat.exportUser', 'User');
        if (role === 'assistant' || role === 'tool') {
            return translateLabel('chat.exportAssistant', 'N.E.K.O.');
        }
        return '';
    }

    function extractBlocksPlainText(blocks) {
        if (!Array.isArray(blocks)) return '';
        var parts = [];
        blocks.forEach(function (block) {
            if (!block || typeof block !== 'object') return;
            if (block.type === 'text') {
                if (block.text) parts.push(String(block.text));
                return;
            }
            if (block.type === 'image') {
                var alt = block.alt ? String(block.alt) : '';
                parts.push('[' + translateLabel('chat.exportImageLabel', 'Image')
                    + (alt ? ': ' + alt : '') + ']');
                return;
            }
            if (block.type === 'link') {
                var title = block.title ? String(block.title) : String(block.url || '');
                parts.push(title + ' (' + String(block.url || '') + ')');
                return;
            }
            if (block.type === 'status') {
                if (block.text) parts.push(String(block.text));
                return;
            }
            if (block.type === 'buttons' && Array.isArray(block.buttons)) {
                var labels = block.buttons.map(function (button) {
                    return button && button.label ? String(button.label) : '';
                }).filter(Boolean);
                if (labels.length > 0) parts.push('[' + labels.join(' | ') + ']');
            }
        });
        return parts.join('\n').trim();
    }

    function blocksToMarkdown(blocks) {
        if (!Array.isArray(blocks)) return '';
        var lines = [];
        blocks.forEach(function (block) {
            if (!block || typeof block !== 'object') return;
            if (block.type === 'text') {
                if (block.text) lines.push(String(block.text));
                return;
            }
            if (block.type === 'image') {
                var alt = block.alt ? String(block.alt).replace(/\]/g, ' ') : '';
                var url = String(block.url || '');
                lines.push('![' + alt + '](' + url + ')');
                return;
            }
            if (block.type === 'link') {
                var title = block.title ? String(block.title).replace(/\]/g, ' ') : String(block.url || '');
                lines.push('[' + title + '](' + String(block.url || '') + ')');
                if (block.description) lines.push('> ' + String(block.description));
                return;
            }
            if (block.type === 'status') {
                if (block.text) lines.push('> ' + String(block.text));
                return;
            }
            if (block.type === 'buttons' && Array.isArray(block.buttons)) {
                var labels = block.buttons.map(function (button) {
                    return button && button.label ? '`' + String(button.label) + '`' : '';
                }).filter(Boolean);
                if (labels.length > 0) lines.push(labels.join(' · '));
            }
        });
        return lines.join('\n\n').trim();
    }

    function collectImageDescriptors(blocks) {
        if (!Array.isArray(blocks)) return [];
        var result = [];
        blocks.forEach(function (block) {
            if (block && block.type === 'image' && block.url) {
                result.push({
                    type: 'image',
                    source: String(block.url),
                    alt: block.alt ? String(block.alt) : ''
                });
            }
        });
        return result;
    }

    function buildExportEntry(message) {
        var role = getRoleLabel(message.role);
        var author = message.author ? String(message.author) : '';
        var time = message.time ? String(message.time) : '';
        var header = [author, time].filter(Boolean).join(' · ');
        var avatarUrl = message.avatarUrl ? String(message.avatarUrl) : '';
        if (!avatarUrl && message.role === 'user') {
            avatarUrl = DEFAULT_USER_EXPORT_AVATAR;
        }
        var avatarLabel = message.avatarLabel ? String(message.avatarLabel) : '';
        if (!avatarLabel) {
            avatarLabel = (author || role || '?').trim().slice(0, 2).toUpperCase();
        }
        return {
            id: String(message.id),
            role: role,
            author: author,
            time: time,
            header: header,
            rawRole: message.role,
            avatarUrl: avatarUrl,
            avatarLabel: avatarLabel,
            textContent: extractBlocksPlainText(message.blocks),
            markdownContent: blocksToMarkdown(message.blocks),
            mediaDescriptors: collectImageDescriptors(message.blocks),
            blocks: message.blocks
        };
    }

    function buildExportEntriesFromMessages(messages) {
        return (messages || []).map(buildExportEntry);
    }

    // ======================== Format definitions ========================

    function getExportFormats() {
        return [
            {
                id: 'markdown',
                extension: 'md',
                mimeType: 'text/markdown;charset=utf-8',
                label: translateLabel('chat.exportFormatMarkdown', 'Markdown')
            },
            {
                id: 'image',
                extension: 'png',
                mimeType: 'image/png',
                label: translateLabel('chat.exportFormatImage', 'Image')
            }
        ];
    }

    function getImageExportFormats() {
        return [
            { id: 'png', extension: 'png', mimeType: 'image/png', quality: undefined,
              label: translateLabel('chat.exportImageFormatPng', 'PNG') },
            { id: 'jpeg', extension: 'jpg', mimeType: 'image/jpeg', quality: 0.92,
              label: translateLabel('chat.exportImageFormatJpeg', 'JPEG') },
            { id: 'webp', extension: 'webp', mimeType: 'image/webp', quality: 0.92,
              label: translateLabel('chat.exportImageFormatWebp', 'WebP') }
        ];
    }

    function getImageExportStyles() {
        return [
            { id: 'neko',     label: translateLabel('chat.exportImageStyleNeko',     'N.E.K.O') },
            { id: 'original', label: translateLabel('chat.exportImageStyleOriginal', 'Original') },
            { id: 'poster',   label: translateLabel('chat.exportImageStylePoster',   'Fresh') },
            { id: 'lyrics',   label: translateLabel('chat.exportImageStyleLyrics',   'Lyrics') }
        ];
    }

    function getCurrentExportFormat() {
        var formats = getExportFormats();
        return formats.find(function (f) { return f.id === state.exportFormat; }) || formats[0];
    }

    function getCurrentImageExportFormat() {
        var formats = getImageExportFormats();
        return formats.find(function (f) { return f.id === state.imageExportFormat; }) || formats[0];
    }

    function getCurrentImageExportStyle() {
        var styles = getImageExportStyles();
        return styles.find(function (s) { return s.id === state.imageExportStyle; }) || styles[0];
    }

    // ======================== Markdown export ========================

    function buildMarkdownExportDocument(entries, now) {
        var title = translateLabel('chat.exportFileTitle', 'Project N.E.K.O Conversation Export');
        var generatedAtLabel = translateLabel('chat.exportGeneratedAt', 'Exported At');
        var lines = [
            '# ' + title,
            '',
            generatedAtLabel + ': ' + buildDisplayTimestamp(now),
            ''
        ];
        entries.forEach(function (entry) {
            var headerParts = [];
            if (entry.role) headerParts.push(entry.role);
            if (entry.author && entry.author !== entry.role) headerParts.push(entry.author);
            if (entry.time) headerParts.push(entry.time);
            if (headerParts.length > 0) {
                lines.push('## ' + headerParts.join(' · '));
            }
            if (entry.markdownContent) {
                lines.push(entry.markdownContent);
            }
            lines.push('');
        });
        var content = lines.join('\n').replace(/\n{3,}/g, '\n\n').trim() + '\n';
        return {
            fileName: getExportBaseFileName(now) + '.md',
            contentType: 'text/markdown;charset=utf-8',
            content: content
        };
    }

    // ======================== Markdown → HTML (preview) ========================

    function renderInlineMarkdown(text) {
        var source = String(text || '');
        source = escapeHtml(source);
        // images first (they look like links) – only emit src/href for safe URLs
        source = source.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, function (_, alt, url) {
            var safeUrl = isSafeUrl(url) ? url : '';
            return '<img src="' + safeUrl + '" alt="' + alt + '">';
        });
        source = source.replace(/\[([^\]]+)\]\(([^)]+)\)/g, function (_, label, url) {
            if (!isSafeUrl(url)) return label;
            return '<a href="' + url + '" target="_blank" rel="noopener noreferrer">' + label + '</a>';
        });
        source = source.replace(/`([^`]+)`/g, '<code>$1</code>');
        source = source.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
        source = source.replace(/(^|[^\*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
        return source;
    }

    function renderMarkdownAsHtml(markdownContent) {
        var lines = String(markdownContent || '').split(/\r?\n/);
        var html = [];
        var paragraphBuffer = [];
        var inList = false;

        function flushParagraph() {
            if (paragraphBuffer.length === 0) return;
            html.push('<p>' + renderInlineMarkdown(paragraphBuffer.join(' ')) + '</p>');
            paragraphBuffer = [];
        }
        function closeList() {
            if (inList) { html.push('</ul>'); inList = false; }
        }

        for (var i = 0; i < lines.length; i += 1) {
            var line = lines[i];
            if (line.trim() === '') { flushParagraph(); closeList(); continue; }
            var headingMatch = line.match(/^(#{1,6})\s+(.+)$/);
            if (headingMatch) {
                flushParagraph(); closeList();
                var level = headingMatch[1].length;
                html.push('<h' + level + '>' + renderInlineMarkdown(headingMatch[2]) + '</h' + level + '>');
                continue;
            }
            var quoteMatch = line.match(/^>\s?(.*)$/);
            if (quoteMatch) {
                flushParagraph(); closeList();
                html.push('<blockquote>' + renderInlineMarkdown(quoteMatch[1]) + '</blockquote>');
                continue;
            }
            var listMatch = line.match(/^[-*+]\s+(.+)$/);
            if (listMatch) {
                flushParagraph();
                if (!inList) { html.push('<ul>'); inList = true; }
                html.push('<li>' + renderInlineMarkdown(listMatch[1]) + '</li>');
                continue;
            }
            paragraphBuffer.push(line);
        }
        flushParagraph();
        closeList();

        return html.join('\n');
    }

    function buildMarkdownPreviewDocument(markdownContent) {
        var bodyHtml = renderMarkdownAsHtml(markdownContent);
        var css = [
            'html,body{margin:0;padding:0;background:#fafbfc;color:#1f2933;',
            'font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;',
            'font-size:14px;line-height:1.7;}',
            '.preview-wrap{max-width:780px;margin:0 auto;padding:28px 32px;}',
            '.preview-wrap h1{font-size:1.72rem;padding-bottom:0.32em;border-bottom:1px solid #e2e8f0;margin-top:0;}',
            '.preview-wrap h2{font-size:1.3rem;margin-top:1.4em;color:#334155;}',
            '.preview-wrap h3{font-size:1.1rem;}',
            '.preview-wrap p{margin:0.75em 0;}',
            '.preview-wrap blockquote{border-left:3px solid #94a3b8;margin:0.75em 0;padding:0.2em 0.8em;color:#475569;background:#f1f5f9;}',
            '.preview-wrap code{background:#f1f5f9;padding:0.1em 0.35em;border-radius:4px;font-family:"SFMono-Regular",Consolas,"Liberation Mono",monospace;font-size:0.92em;}',
            '.preview-wrap ul{padding-left:1.4em;}',
            '.preview-wrap img{max-width:100%;height:auto;border-radius:6px;margin:0.5em 0;}',
            '.preview-wrap a{color:#2563eb;text-decoration:none;}',
            '.preview-wrap a:hover{text-decoration:underline;}',
            '@media (prefers-color-scheme:dark){html,body{background:#111827;color:#e5e7eb;}.preview-wrap h1{border-color:#374151;}.preview-wrap h2{color:#cbd5e1;}.preview-wrap blockquote{background:#1f2937;color:#9ca3af;border-color:#4b5563;}.preview-wrap code{background:#1f2937;}}'
        ].join('');
        return '<!DOCTYPE html><html lang="' + escapeHtml(document.documentElement.lang || 'en')
            + '"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'
            + escapeHtml(translateLabel('chat.exportFileTitle', 'Project N.E.K.O Conversation Export'))
            + '</title><style>' + css + '</style></head><body><div class="preview-wrap">'
            + bodyHtml + '</div></body></html>';
    }

    // ======================== Image export — shared utilities ========================

    function blobToDataUrl(blob) {
        return new Promise(function (resolve, reject) {
            var reader = new FileReader();
            reader.onload = function () { resolve(String(reader.result || '')); };
            reader.onerror = function () { reject(reader.error || new Error('Failed to read blob.')); };
            reader.readAsDataURL(blob);
        });
    }

    function canvasToBlob(canvas, mimeType, quality) {
        return new Promise(function (resolve, reject) {
            canvas.toBlob(function (value) {
                if (value) resolve(value);
                else reject(new Error('Failed to encode image.'));
            }, mimeType, quality);
        });
    }

    function loadImageElement(source, timeoutMs) {
        if (timeoutMs === undefined) timeoutMs = 10000;
        return new Promise(function (resolve, reject) {
            var image = new Image();
            var timer = null;
            function cleanup() {
                if (timer) { clearTimeout(timer); timer = null; }
                image.onload = null;
                image.onerror = null;
            }
            image.crossOrigin = 'anonymous';
            image.decoding = 'async';
            image.onload = function () { cleanup(); resolve(image); };
            image.onerror = function () { cleanup(); reject(new Error('Failed to load image asset.')); };
            if (timeoutMs > 0) {
                timer = setTimeout(function () {
                    cleanup();
                    image.src = '';
                    reject(new Error('Image load timed out after ' + timeoutMs + 'ms.'));
                }, timeoutMs);
            }
            image.src = source;
        });
    }

    async function inlineImageSourceToDataUrl(source) {
        if (!source) throw new Error('Image source missing.');
        if (/^data:/i.test(source)) return source;
        var controller = new AbortController();
        var timeout = setTimeout(function () { controller.abort(); }, 5000);
        try {
            var response = await fetch(source, { mode: 'cors', signal: controller.signal });
            clearTimeout(timeout);
            if (!response.ok) throw new Error('Image fetch failed: HTTP ' + response.status);
            return await blobToDataUrl(await response.blob());
        } catch (error) {
            clearTimeout(timeout);
            // Fall back to direct URL (may still work cross-origin if CORS-allowed)
            return source;
        }
    }

    async function resolveImageEntryMedia(entries) {
        var cache = new Map();
        var resolved = [];
        for (var i = 0; i < entries.length; i += 1) {
            var entry = entries[i];
            var mediaList = entry.mediaDescriptors || [];
            var imageDescriptors = [];
            var promises = [];
            var avatarPromise = null;
            if (entry.avatarUrl) {
                avatarPromise = cache.get('avatar:' + entry.avatarUrl);
                if (!avatarPromise) {
                    avatarPromise = inlineImageSourceToDataUrl(entry.avatarUrl)
                        .then(loadImageElement)
                        .catch(function (error) {
                            logExportError('resolveImageEntryAvatar', error);
                            return null;
                        });
                    cache.set('avatar:' + entry.avatarUrl, avatarPromise);
                }
            }
            for (var j = 0; j < mediaList.length; j += 1) {
                var descriptor = mediaList[j];
                if (!descriptor || descriptor.type !== 'image') continue;
                var key = descriptor.source || descriptor.alt || ('image-' + i + '-' + j);
                var promise = cache.get(key);
                if (!promise) {
                    promise = inlineImageSourceToDataUrl(descriptor.source)
                        .then(loadImageElement)
                        .catch(function (error) {
                            logExportError('resolveImageEntryMedia', error);
                            return null;
                        });
                    cache.set(key, promise);
                }
                imageDescriptors.push(descriptor);
                promises.push(promise);
            }
            var images = await Promise.all(promises);
            var loaded = [];
            for (var k = 0; k < images.length; k += 1) {
                if (images[k]) {
                    loaded.push({ type: 'image', image: images[k], alt: imageDescriptors[k].alt });
                } else {
                    loaded.push({
                        type: 'note',
                        text: (imageDescriptors[k].alt ? imageDescriptors[k].alt + ' — ' : '')
                            + translateLabel('chat.exportImageLabel', 'Image')
                    });
                }
            }
            var avatarImage = avatarPromise ? await avatarPromise : null;
            resolved.push({
                id: entry.id,
                role: entry.role,
                author: entry.author,
                time: entry.time,
                rawRole: entry.rawRole,
                avatarLabel: entry.avatarLabel,
                avatarImage: avatarImage,
                textContent: entry.textContent,
                media: loaded
            });
            if ((i + 1) % 2 === 0) await waitForNextPaint();
        }
        return resolved;
    }

    function isDarkTheme() {
        return document.documentElement
            && document.documentElement.getAttribute('data-theme') === 'dark';
    }

    // Canvas helpers: wrap text to a max width and return array of lines.
    function wrapTextLines(ctx, text, maxWidth) {
        var result = [];
        var paragraphs = String(text || '').split(/\n/);
        paragraphs.forEach(function (paragraph) {
            if (paragraph.length === 0) { result.push(''); return; }
            var current = '';
            for (var i = 0; i < paragraph.length; i += 1) {
                var ch = paragraph[i];
                var candidate = current + ch;
                if (ctx.measureText(candidate).width > maxWidth && current.length > 0) {
                    result.push(current);
                    current = ch;
                } else {
                    current = candidate;
                }
            }
            if (current.length > 0) result.push(current);
        });
        return result.length > 0 ? result : [''];
    }

    function drawWrappedText(ctx, lines, x, y, lineHeight) {
        lines.forEach(function (line, index) {
            ctx.fillText(line, x, y + index * lineHeight);
        });
        return y + lines.length * lineHeight;
    }

    function drawWrappedTextAligned(ctx, lines, x, y, lineHeight, align, maxWidth) {
        var resolvedAlign = align === 'right' ? 'right' : 'left';
        var width = Number.isFinite(maxWidth) ? maxWidth : 0;
        lines.forEach(function (line, index) {
            var drawX = x;
            if (resolvedAlign === 'right' && width > 0) {
                drawX = x + width - ctx.measureText(line).width;
            }
            ctx.fillText(line, drawX, y + index * lineHeight);
        });
        return y + lines.length * lineHeight;
    }

    function drawRoundedRect(ctx, x, y, width, height, radius) {
        var r = Math.max(0, Math.min(radius, Math.min(width, height) / 2));
        ctx.beginPath();
        ctx.moveTo(x + r, y);
        ctx.lineTo(x + width - r, y);
        ctx.quadraticCurveTo(x + width, y, x + width, y + r);
        ctx.lineTo(x + width, y + height - r);
        ctx.quadraticCurveTo(x + width, y + height, x + width - r, y + height);
        ctx.lineTo(x + r, y + height);
        ctx.quadraticCurveTo(x, y + height, x, y + height - r);
        ctx.lineTo(x, y + r);
        ctx.quadraticCurveTo(x, y, x + r, y);
        ctx.closePath();
    }

    function fitImageToWidth(image, maxWidth, maxHeight) {
        if (!image || !image.width || !image.height) return { width: 0, height: 0 };
        var ratio = Math.min(maxWidth / image.width, 1);
        var w = image.width * ratio;
        var h = image.height * ratio;
        if (h > maxHeight) {
            var ratio2 = maxHeight / h;
            w = w * ratio2;
            h = h * ratio2;
        }
        return { width: Math.round(w), height: Math.round(h) };
    }

    function drawAvatarCircle(ctx, x, y, size, options) {
        options = options || {};
        var radius = size / 2;
        var image = options.image || null;
        var label = String(options.label || '?').trim().slice(0, 2) || '?';
        var background = options.background || '#e5e7eb';
        var textColor = options.textColor || '#111827';
        var borderColor = options.borderColor || 'rgba(255,255,255,0.4)';

        ctx.save();
        ctx.beginPath();
        ctx.arc(x + radius, y + radius, radius, 0, Math.PI * 2);
        ctx.closePath();
        ctx.clip();
        if (image) {
            try {
                ctx.drawImage(image, x, y, size, size);
            } catch (_) {
                ctx.fillStyle = background;
                ctx.fillRect(x, y, size, size);
            }
        } else {
            ctx.fillStyle = background;
            ctx.fillRect(x, y, size, size);
        }
        ctx.restore();

        ctx.save();
        ctx.strokeStyle = borderColor;
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.arc(x + radius, y + radius, Math.max(0, radius - 0.75), 0, Math.PI * 2);
        ctx.stroke();
        ctx.restore();

        if (!image) {
            ctx.save();
            ctx.font = '700 ' + Math.max(12, Math.floor(size * 0.38)) + 'px -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif';
            ctx.fillStyle = textColor;
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(label, x + radius, y + radius + 1);
            ctx.restore();
        }
    }

    // ======================== Image export — 4 styles ========================
    //
    // Each renderer takes `resolvedEntries` (the output of resolveImageEntryMedia)
    // and returns a Promise<HTMLCanvasElement>. A shared prerender pass computes
    // layout, and a final draw pass paints the pixels.

    function getNekoTheme() {
        var dark = isDarkTheme();
        return {
            background: dark ? '#0f1317' : '#f5f7fa',
            card:       dark ? '#1a2029' : '#ffffff',
            cardBorder: dark ? '#2b3340' : '#e2e8f0',
            accentUser: dark ? '#60a5fa' : '#2563eb',
            accentBot:  dark ? '#a78bfa' : '#7c3aed',
            textPrimary: dark ? '#e8eaed' : '#1f2937',
            textSecondary: dark ? '#9ca3af' : '#6b7280',
            shadow: dark ? 'rgba(0,0,0,0.5)' : 'rgba(15,23,42,0.08)'
        };
    }

    function getPosterTheme() {
        var dark = isDarkTheme();
        return {
            gradientTop: dark ? '#1e1b4b' : '#fff3bf',
            gradientMid: dark ? '#312e81' : '#ffd6e7',
            gradientBot: dark ? '#4c1d95' : '#ffb4d4',
            textPrimary: dark ? '#fff8e7' : '#1f2937',
            textSecondary: dark ? '#e5e7eb' : '#5b6472',
            headerAccent: dark ? '#f9a8d4' : '#db2777',
            assistantCard: dark ? 'rgba(50,22,45,0.82)' : 'rgba(255,247,251,0.95)',
            assistantBorder: dark ? 'rgba(244,114,182,0.34)' : 'rgba(236,72,153,0.30)',
            assistantText: dark ? '#ffb3d1' : '#be185d',
            assistantMeta: dark ? 'rgba(255,205,226,0.84)' : '#be5b8f',
            userCard: dark ? 'rgba(64,50,18,0.82)' : 'rgba(255,251,235,0.95)',
            userBorder: dark ? 'rgba(250,204,21,0.30)' : 'rgba(245,158,11,0.32)',
            userText: dark ? '#ffe082' : '#9a6700',
            userMeta: dark ? 'rgba(255,234,158,0.82)' : '#a67c12'
        };
    }

    function getLyricsTheme() {
        return {
            backgroundTop: '#0a1018',
            backgroundBot: '#182636',
            glowA: 'rgba(104,198,255,0.18)',
            glowB: 'rgba(77,236,188,0.12)',
            card: 'rgba(9,16,24,0.68)',
            cardBorder: 'rgba(255,255,255,0.08)',
            badgeBg: 'rgba(104,198,255,0.14)',
            badgeText: '#8fdcff',
            title: '#f4fbff',
            lyricAssistant: '#f8fdff',
            lyricUser: 'rgba(218,229,239,0.72)',
            meta: 'rgba(219,236,248,0.72)',
            roleAssistant: 'rgba(255,255,255,0.92)',
            roleUser: 'rgba(196,214,226,0.8)'
        };
    }

    function getOriginalTheme() {
        var dark = isDarkTheme();
        return {
            pageTop: dark ? '#10161d' : '#edf3f7',
            pageBot: dark ? '#171f29' : '#dfe9ef',
            headerBg: dark ? '#2a2a2a' : '#f7f8fa',
            headerText: dark ? 'rgba(255,255,255,0.9)' : 'rgba(0,0,0,0.9)',
            contentBg: dark ? 'rgba(25,25,25,0.8)' : 'rgba(249,249,249,0.9)',
            assistantBubble: dark ? 'rgba(42,123,196,0.25)' : 'rgba(68,183,254,0.18)',
            assistantText: dark ? '#e0e0e0' : '#333333',
            userBubble: dark ? '#2a7bc4' : '#44b7fe',
            userText: '#ffffff',
            metaText: dark ? '#8b95a1' : '#64748b',
            border: dark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.06)'
        };
    }

    // ----- Shared body layout measurement -----

    function measureEntryBody(ctx, entry, bodyFont, bodyLineHeight, maxWidth, includeImages, maxImageHeight) {
        ctx.font = bodyFont;
        var segments = [];
        var height = 0;
        var widthUsed = 0;

        if (entry.textContent) {
            var lines = wrapTextLines(ctx, entry.textContent, maxWidth);
            segments.push({ kind: 'text', lines: lines, lineHeight: bodyLineHeight });
            height += lines.length * bodyLineHeight;
            lines.forEach(function (line) {
                widthUsed = Math.max(widthUsed, ctx.measureText(line).width);
            });
        }

        if (includeImages && entry.media && entry.media.length > 0) {
            entry.media.forEach(function (item) {
                if (item.type === 'image') {
                    var size = fitImageToWidth(item.image, maxWidth, maxImageHeight || 240);
                    segments.push({ kind: 'image', width: size.width, height: size.height, image: item.image });
                    height += size.height + 8;
                    widthUsed = Math.max(widthUsed, size.width);
                } else if (item.type === 'note' && item.text) {
                    var noteLines = wrapTextLines(ctx, item.text, maxWidth);
                    segments.push({ kind: 'note', lines: noteLines, lineHeight: bodyLineHeight });
                    height += noteLines.length * bodyLineHeight + 4;
                    noteLines.forEach(function (line) {
                        widthUsed = Math.max(widthUsed, ctx.measureText(line).width);
                    });
                }
            });
        }

        return { segments: segments, height: height, width: Math.ceil(widthUsed) };
    }

    function drawSegments(ctx, segments, x, y, options) {
        options = options || {};
        var noteColor = options.noteColor;
        var textAlign = options.align === 'right' ? 'right' : 'left';
        var maxWidth = Number.isFinite(options.maxWidth) ? options.maxWidth : 0;
        segments.forEach(function (segment) {
            if (segment.kind === 'text') {
                y = drawWrappedTextAligned(ctx, segment.lines, x, y, segment.lineHeight, textAlign, maxWidth);
            } else if (segment.kind === 'image') {
                var imageX = x;
                if (textAlign === 'right' && maxWidth > 0) {
                    imageX = x + Math.max(0, maxWidth - segment.width);
                }
                try { ctx.drawImage(segment.image, imageX, y, segment.width, segment.height); }
                catch (_) { /* draw failed, skip */ }
                y += segment.height + 8;
            } else if (segment.kind === 'note') {
                if (noteColor) {
                    var prev = ctx.fillStyle;
                    ctx.fillStyle = noteColor;
                    y = drawWrappedTextAligned(ctx, segment.lines, x, y, segment.lineHeight, textAlign, maxWidth) + 4;
                    ctx.fillStyle = prev;
                } else {
                    y = drawWrappedTextAligned(ctx, segment.lines, x, y, segment.lineHeight, textAlign, maxWidth) + 4;
                }
            }
        });
        return y;
    }

    // ----- Style: neko (default card layout) -----

    async function renderNekoStyleCanvas(resolvedEntries, now) {
        var theme = getNekoTheme();
        var scale = 2;
        var width = 800;
        var padding = 36;
        var cardPadding = 22;
        var cardGap = 18;
        var headerFont = '700 26px -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif';
        var subtitleFont = '400 13px -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif';
        var authorFont = '600 15px -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif';
        var bodyFont = '400 15px -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif';
        var metaFont = '400 12px -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif';
        var bodyLineHeight = 24;
        var maxBodyWidth = width - padding * 2 - cardPadding * 2;

        // Measurement pass
        var measureCanvas = document.createElement('canvas');
        var measureCtx = measureCanvas.getContext('2d');
        var measuredEntries = resolvedEntries.map(function (entry) {
            measureCtx.font = bodyFont;
            var body = measureEntryBody(measureCtx, entry, bodyFont, bodyLineHeight, maxBodyWidth, true, 240);
            var headerHeight = 24;  // author + time row
            var cardHeight = cardPadding * 2 + headerHeight + 8 + body.height;
            return { entry: entry, body: body, cardHeight: cardHeight };
        });

        var totalCardsHeight = measuredEntries.reduce(function (sum, m) {
            return sum + m.cardHeight + cardGap;
        }, 0);
        var headerBlock = 70;
        var footerBlock = 40;
        var totalHeight = padding + headerBlock + totalCardsHeight + footerBlock;

        var canvas = document.createElement('canvas');
        canvas.width = width * scale;
        canvas.height = totalHeight * scale;
        var ctx = canvas.getContext('2d');
        ctx.scale(scale, scale);
        ctx.imageSmoothingEnabled = true;
        ctx.imageSmoothingQuality = 'high';

        // background
        ctx.fillStyle = theme.background;
        ctx.fillRect(0, 0, width, totalHeight);

        // title
        ctx.fillStyle = theme.textPrimary;
        ctx.font = headerFont;
        ctx.textBaseline = 'top';
        ctx.fillText(
            translateLabel('chat.exportFileTitle', 'Project N.E.K.O Conversation Export'),
            padding, padding
        );
        ctx.font = subtitleFont;
        ctx.fillStyle = theme.textSecondary;
        ctx.fillText(
            translateLabel('chat.exportGeneratedAt', 'Exported At') + ': ' + buildDisplayTimestamp(now),
            padding, padding + 34
        );

        // cards
        var y = padding + headerBlock;
        measuredEntries.forEach(function (m) {
            var entry = m.entry;
            var cardHeight = m.cardHeight;
            var cardX = padding;
            var cardY = y;
            var cardW = width - padding * 2;

            // card background + border
            ctx.save();
            ctx.shadowColor = theme.shadow;
            ctx.shadowBlur = 8;
            ctx.shadowOffsetY = 2;
            ctx.fillStyle = theme.card;
            drawRoundedRect(ctx, cardX, cardY, cardW, cardHeight, 12);
            ctx.fill();
            ctx.restore();
            ctx.strokeStyle = theme.cardBorder;
            ctx.lineWidth = 1;
            drawRoundedRect(ctx, cardX + 0.5, cardY + 0.5, cardW - 1, cardHeight - 1, 12);
            ctx.stroke();

            // accent bar
            var accent = entry.rawRole === 'user' ? theme.accentUser : theme.accentBot;
            ctx.fillStyle = accent;
            drawRoundedRect(ctx, cardX, cardY + 14, 4, cardHeight - 28, 2);
            ctx.fill();

            // author + time row
            ctx.font = authorFont;
            ctx.fillStyle = accent;
            ctx.fillText(entry.author || entry.role || '', cardX + cardPadding, cardY + cardPadding);

            ctx.font = metaFont;
            ctx.fillStyle = theme.textSecondary;
            var metaText = [entry.role, entry.time].filter(Boolean).join(' · ');
            var metaWidth = ctx.measureText(metaText).width;
            ctx.fillText(metaText, cardX + cardW - cardPadding - metaWidth, cardY + cardPadding + 2);

            // body
            ctx.font = bodyFont;
            ctx.fillStyle = theme.textPrimary;
            drawSegments(
                ctx,
                m.body.segments,
                cardX + cardPadding,
                cardY + cardPadding + 24 + 6,
                { noteColor: theme.textSecondary }
            );

            y += cardHeight + cardGap;
        });

        // footer
        ctx.font = metaFont;
        ctx.fillStyle = theme.textSecondary;
        ctx.textAlign = 'center';
        ctx.fillText('N.E.K.O · ' + buildDisplayTimestamp(now), width / 2, totalHeight - 28);
        ctx.textAlign = 'start';

        return canvas;
    }

    // ----- Style: original (chat-app mockup) -----

    async function renderOriginalStyleCanvas(resolvedEntries, now) {
        var theme = getOriginalTheme();
        var scale = 2;
        var width = 520;
        var outerPadding = 30;
        var panelRadius = 12;
        var headerHeight = 52;
        var contentPaddingX = 16;
        var messageGap = 12;
        var bubblePaddingX = 14;
        var bubblePaddingY = 10;
        var bubbleRadius = 14;
        var bodyLineHeight = 22;
        var bodyFont = '400 15px -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif';
        var headerFont = '600 15px -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif';
        var metaFont = '400 11px -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif';
        var panelWidth = width - outerPadding * 2;
        var contentWidth = panelWidth - contentPaddingX * 2;
        var bubbleMaxWidth = Math.floor(contentWidth * 0.78);

        // measurement
        var measureCanvas = document.createElement('canvas');
        var measureCtx = measureCanvas.getContext('2d');
        measureCtx.font = bodyFont;
        var measured = resolvedEntries.map(function (entry) {
            var body = measureEntryBody(
                measureCtx, entry, bodyFont, bodyLineHeight,
                bubbleMaxWidth - bubblePaddingX * 2, true, 200
            );
            var bubbleHeight = bubblePaddingY * 2 + body.height + 16;  // extra for meta row
            return { entry: entry, body: body, bubbleHeight: bubbleHeight };
        });
        var messagesHeight = measured.reduce(function (sum, m) {
            return sum + m.bubbleHeight + messageGap;
        }, 0);
        var contentHeight = messagesHeight + 24;
        var totalPanelHeight = headerHeight + contentHeight + 16;
        var totalHeight = outerPadding * 2 + totalPanelHeight;

        var canvas = document.createElement('canvas');
        canvas.width = width * scale;
        canvas.height = totalHeight * scale;
        var ctx = canvas.getContext('2d');
        ctx.scale(scale, scale);
        ctx.imageSmoothingEnabled = true;
        ctx.imageSmoothingQuality = 'high';

        // backdrop gradient
        var bgGradient = ctx.createLinearGradient(0, 0, 0, totalHeight);
        bgGradient.addColorStop(0, theme.pageTop);
        bgGradient.addColorStop(1, theme.pageBot);
        ctx.fillStyle = bgGradient;
        ctx.fillRect(0, 0, width, totalHeight);

        // panel
        var panelX = outerPadding;
        var panelY = outerPadding;
        ctx.save();
        ctx.shadowColor = 'rgba(0,0,0,0.12)';
        ctx.shadowBlur = 18;
        ctx.shadowOffsetY = 6;
        ctx.fillStyle = theme.contentBg;
        drawRoundedRect(ctx, panelX, panelY, panelWidth, totalPanelHeight, panelRadius);
        ctx.fill();
        ctx.restore();
        ctx.strokeStyle = theme.border;
        ctx.lineWidth = 1;
        drawRoundedRect(ctx, panelX + 0.5, panelY + 0.5, panelWidth - 1, totalPanelHeight - 1, panelRadius);
        ctx.stroke();

        // header
        ctx.save();
        ctx.beginPath();
        drawRoundedRect(ctx, panelX, panelY, panelWidth, headerHeight, panelRadius);
        ctx.clip();
        ctx.fillStyle = theme.headerBg;
        ctx.fillRect(panelX, panelY, panelWidth, headerHeight);
        ctx.restore();
        ctx.strokeStyle = theme.border;
        ctx.beginPath();
        ctx.moveTo(panelX, panelY + headerHeight + 0.5);
        ctx.lineTo(panelX + panelWidth, panelY + headerHeight + 0.5);
        ctx.stroke();
        ctx.font = headerFont;
        ctx.fillStyle = theme.headerText;
        ctx.textBaseline = 'middle';
        ctx.fillText(translateLabel('chat.title', 'Chat'), panelX + contentPaddingX, panelY + headerHeight / 2);
        ctx.textBaseline = 'top';

        // messages
        var messageY = panelY + headerHeight + 16;
        measured.forEach(function (m) {
            var entry = m.entry;
            var isUser = entry.rawRole === 'user';
            var bubbleWidth = bubbleMaxWidth;
            var bubbleX = isUser
                ? (panelX + panelWidth - contentPaddingX - bubbleWidth)
                : (panelX + contentPaddingX);

            // bubble
            ctx.fillStyle = isUser ? theme.userBubble : theme.assistantBubble;
            drawRoundedRect(ctx, bubbleX, messageY, bubbleWidth, m.bubbleHeight, bubbleRadius);
            ctx.fill();

            // meta (author + time)
            ctx.font = metaFont;
            ctx.fillStyle = isUser
                ? 'rgba(255,255,255,0.85)'
                : theme.metaText;
            var metaText = [entry.author, entry.time].filter(Boolean).join(' · ');
            ctx.fillText(metaText, bubbleX + bubblePaddingX, messageY + bubblePaddingY);

            // body
            ctx.font = bodyFont;
            ctx.fillStyle = isUser ? theme.userText : theme.assistantText;
            drawSegments(
                ctx,
                m.body.segments,
                bubbleX + bubblePaddingX,
                messageY + bubblePaddingY + 14,
                { noteColor: isUser ? 'rgba(255,255,255,0.8)' : theme.metaText }
            );

            messageY += m.bubbleHeight + messageGap;
        });

        return canvas;
    }

    // ----- Style: poster (hero gradient) -----

    async function renderPosterStyleCanvas(resolvedEntries, now) {
        var theme = getPosterTheme();
        var scale = 2;
        var width = 760;
        var padding = 42;
        var heroHeight = 200;
        var cardPadding = 22;
        var cardGap = 18;
        var titleFont = '800 42px -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif';
        var kickerFont = '700 14px -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif';
        var authorFont = '700 16px -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif';
        var bodyFont = '500 17px -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif';
        var metaFont = '500 12px -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif';
        var bodyLineHeight = 28;
        var avatarSize = 34;
        var avatarGap = 12;
        var maxTrackWidth = width - padding * 2;
        var cardMaxWidth = Math.floor(maxTrackWidth * 0.82);
        var cardMinWidth = 200;
        var cardInnerMaxWidth = cardMaxWidth - cardPadding * 2;
        var contentMaxWidth = Math.floor(cardInnerMaxWidth * 0.88);
        var overlapDepth = 28;

        var measureCanvas = document.createElement('canvas');
        var measureCtx = measureCanvas.getContext('2d');
        var measured = resolvedEntries.map(function (entry) {
            measureCtx.font = bodyFont;
            var body = measureEntryBody(measureCtx, entry, bodyFont, bodyLineHeight, contentMaxWidth, true, 240);
            measureCtx.font = authorFont;
            var authorWidth = measureCtx.measureText(entry.author || entry.role || '').width;
            measureCtx.font = metaFont;
            var metaWidth = measureCtx.measureText([entry.role, entry.time].filter(Boolean).join(' · ')).width;
            var headerTextWidth = Math.max(authorWidth, metaWidth);
            var bodyWidth = Math.max(0, body.width || 0);
            var headerWidth = avatarSize + avatarGap + headerTextWidth;
            var innerWidth = Math.max(bodyWidth, headerWidth);
            var cardWidth = Math.max(cardMinWidth, Math.min(cardMaxWidth, Math.ceil(innerWidth + cardPadding * 2)));
            var cardHeight = cardPadding * 2 + 30 + 6 + body.height;
            return { entry: entry, body: body, cardHeight: cardHeight, cardWidth: cardWidth };
        });
        var layouts = [];
        var cursorY = heroHeight;
        var maxBottom = heroHeight;
        measured.forEach(function (m, index) {
            var isUser = m.entry.rawRole === 'user';
            var cardX = isUser
                ? (padding + maxTrackWidth - m.cardWidth)
                : padding;
            var cardY = cursorY;
            if (index > 0) {
                var prev = layouts[index - 1];
                if (prev && prev.isUser !== isUser) {
                    var overlap = Math.min(overlapDepth, Math.floor(Math.min(prev.cardH, m.cardHeight) * 0.24));
                    cardY = Math.max(heroHeight, cardY - overlap);
                }
            }
            var layout = {
                entry: m.entry,
                body: m.body,
                isUser: isUser,
                cardX: cardX,
                cardY: cardY,
                cardW: m.cardWidth,
                cardH: m.cardHeight
            };
            layouts.push(layout);
            cursorY = cardY + m.cardHeight + cardGap;
            maxBottom = Math.max(maxBottom, cardY + m.cardHeight);
        });
        var decorIcons = await Promise.all([
            loadImageElement('/static/icons/paw_ui.png', 4000).catch(function () { return null; }),
            loadImageElement('/static/icons/chat_bubble.png', 4000).catch(function () { return null; }),
            loadImageElement('/static/icons/star.png', 4000).catch(function () { return null; }),
            loadImageElement('/static/icons/cat_icon.png', 4000).catch(function () { return null; })
        ]);
        var pawIcon = decorIcons[0];
        var bubbleIcon = decorIcons[1];
        var starIcon = decorIcons[2];
        var catIcon = decorIcons[3];
        var footerBlock = 50;
        var totalHeight = maxBottom + padding + footerBlock;

        var canvas = document.createElement('canvas');
        canvas.width = width * scale;
        canvas.height = totalHeight * scale;
        var ctx = canvas.getContext('2d');
        ctx.scale(scale, scale);
        ctx.imageSmoothingEnabled = true;
        ctx.imageSmoothingQuality = 'high';

        // gradient background
        var bg = ctx.createLinearGradient(0, 0, width, totalHeight);
        bg.addColorStop(0, theme.gradientTop);
        bg.addColorStop(0.5, theme.gradientMid);
        bg.addColorStop(1, theme.gradientBot);
        ctx.fillStyle = bg;
        ctx.fillRect(0, 0, width, totalHeight);

        // hero area
        ctx.font = kickerFont;
        ctx.fillStyle = theme.headerAccent;
        ctx.textBaseline = 'top';
        ctx.fillText(
            translateLabel('chat.exportPosterSubtitle', 'Shared from N.E.K.O').toUpperCase(),
            padding, padding
        );
        ctx.font = titleFont;
        ctx.fillStyle = theme.textPrimary;
        var title = translateLabel('chat.exportPosterTitle', 'Conversation Highlights');
        var titleLines = wrapTextLines(ctx, title, width - padding * 2);
        drawWrappedText(ctx, titleLines, padding, padding + 30, 48);

        if (pawIcon) {
            ctx.save();
            ctx.globalAlpha = 0.95;
            ctx.drawImage(pawIcon, padding + 250, padding - 6, 28, 28);
            ctx.restore();
        }
        if (starIcon) {
            ctx.save();
            ctx.globalAlpha = 0.82;
            ctx.drawImage(starIcon, padding + 292, padding + 18, 18, 18);
            ctx.restore();
        }
        if (bubbleIcon) {
            ctx.save();
            ctx.globalAlpha = 0.18;
            ctx.drawImage(bubbleIcon, width - padding - 88, padding + 6, 52, 52);
            ctx.restore();
        }
        if (catIcon) {
            ctx.save();
            ctx.globalAlpha = 0.16;
            ctx.drawImage(catIcon, width - padding - 132, padding + 56, 78, 78);
            ctx.restore();
        }

        ctx.font = '500 14px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif';
        ctx.fillStyle = theme.textSecondary;
        ctx.fillText(buildDisplayTimestamp(now), padding, padding + 30 + titleLines.length * 48 + 4);

        // cards
        layouts.forEach(function (layout) {
            var entry = layout.entry;
            var isUser = layout.isUser;
            var cardX = layout.cardX;
            var cardY = layout.cardY;
            var cardW = layout.cardW;
            var cardH = layout.cardH;
            var maxInnerWidth = Math.max(0, cardW - cardPadding * 2);
            var textMaxWidth = Math.max(64, Math.min(Math.max(0, layout.body.width || 0), maxInnerWidth));
            if (!layout.body.width) {
                textMaxWidth = maxInnerWidth;
            }
            var textX = isUser
                ? (cardX + cardW - cardPadding - textMaxWidth)
                : (cardX + cardPadding);
            var avatarX = isUser
                ? (cardX + cardW - cardPadding - avatarSize)
                : textX;
            var avatarY = cardY + cardPadding - 2;
            var authorAnchorX = isUser
                ? (avatarX - avatarGap)
                : (avatarX + avatarSize + avatarGap);

            ctx.save();
            ctx.shadowColor = 'rgba(0,0,0,0.15)';
            ctx.shadowBlur = 18;
            ctx.shadowOffsetY = 6;
            ctx.fillStyle = isUser ? theme.userCard : theme.assistantCard;
            drawRoundedRect(ctx, cardX, cardY, cardW, cardH, 16);
            ctx.fill();
            ctx.restore();
            ctx.strokeStyle = isUser ? theme.userBorder : theme.assistantBorder;
            ctx.lineWidth = 1;
            drawRoundedRect(ctx, cardX + 0.5, cardY + 0.5, cardW - 1, cardH - 1, 16);
            ctx.stroke();

            drawAvatarCircle(ctx, avatarX, avatarY, avatarSize, {
                image: layout.entry.avatarImage,
                label: layout.entry.avatarLabel || layout.entry.author || layout.entry.role,
                background: isUser ? 'rgba(255,232,163,0.95)' : 'rgba(255,210,228,0.95)',
                textColor: isUser ? '#8a5a00' : '#9f1853',
                borderColor: isUser ? theme.userBorder : theme.assistantBorder
            });

            // author
            ctx.font = authorFont;
            ctx.fillStyle = isUser ? theme.userText : theme.assistantText;
            ctx.textAlign = isUser ? 'right' : 'left';
            ctx.fillText(
                entry.author || entry.role || '',
                authorAnchorX,
                cardY + cardPadding
            );

            // meta
            ctx.font = metaFont;
            ctx.fillStyle = isUser ? theme.userMeta : theme.assistantMeta;
            ctx.textAlign = isUser ? 'right' : 'left';
            ctx.fillText(
                [entry.role, entry.time].filter(Boolean).join(' · '),
                authorAnchorX,
                cardY + cardPadding + 20
            );

            // body
            ctx.font = bodyFont;
            ctx.fillStyle = isUser ? theme.userText : theme.assistantText;
            ctx.textAlign = 'left';
            drawSegments(
                ctx,
                layout.body.segments,
                textX,
                cardY + cardPadding + 40,
                {
                    noteColor: isUser ? theme.userMeta : theme.assistantMeta,
                    align: isUser ? 'right' : 'left',
                    maxWidth: textMaxWidth
                }
            );
        });

        // footer
        ctx.font = '700 12px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif';
        ctx.fillStyle = theme.headerAccent;
        ctx.textAlign = 'center';
        ctx.fillText('N.E.K.O.', width / 2, totalHeight - 34);
        ctx.textAlign = 'start';

        return canvas;
    }

    // ----- Style: lyrics (dark poetic layout) -----

    async function renderLyricsStyleCanvas(resolvedEntries, now) {
        var theme = getLyricsTheme();
        var scale = 2;
        var width = 1024;
        var outerPadding = 42;
        var frameRadius = 28;
        var headerPaddingX = 52;
        var headerPaddingTop = 42;
        var headerPaddingBottom = 30;
        var listPadding = 42;
        var cardGap = 26;
        var lyricLineHeight = 44;
        var noteLineHeight = 22;
        var frameWidth = width - outerPadding * 2;

        var titleFont = '700 32px "Segoe UI",Arial,sans-serif';
        var metaFont = '600 14px "Segoe UI",Arial,sans-serif';
        var kickerFont = '700 11px "Segoe UI",Arial,sans-serif';
        var roleFont = '700 12px "Segoe UI",Arial,sans-serif';
        var lyricAssistantFont = '700 32px "Segoe UI",Arial,sans-serif';
        var lyricUserFont = '600 26px "Segoe UI",Arial,sans-serif';
        var noteFont = '500 15px "Segoe UI",Arial,sans-serif';

        var textMaxWidth = frameWidth - listPadding * 2;

        var measureCanvas = document.createElement('canvas');
        var measureCtx = measureCanvas.getContext('2d');

        var measured = resolvedEntries.map(function (entry) {
            var isAssistant = entry.rawRole !== 'user';
            measureCtx.font = isAssistant ? lyricAssistantFont : lyricUserFont;
            var lyricLines = entry.textContent ? wrapTextLines(measureCtx, entry.textContent, textMaxWidth) : [];
            var noteLines = [];
            if (entry.media && entry.media.length > 0) {
                measureCtx.font = noteFont;
                entry.media.forEach(function (m) {
                    if (m.type === 'note' && m.text) {
                        noteLines = noteLines.concat(wrapTextLines(measureCtx, m.text, textMaxWidth));
                    } else if (m.type === 'image') {
                        noteLines.push('[' + translateLabel('chat.exportImageLabel', 'Image')
                            + (m.alt ? ': ' + m.alt : '') + ']');
                    }
                });
            }
            var blockHeight = 24  // role
                + lyricLines.length * lyricLineHeight
                + noteLines.length * noteLineHeight + (noteLines.length > 0 ? 8 : 0)
                + 12;  // bottom gap
            return {
                entry: entry,
                isAssistant: isAssistant,
                lyricLines: lyricLines,
                noteLines: noteLines,
                blockHeight: blockHeight
            };
        });

        measureCtx.font = titleFont;
        var title = translateLabel('chat.exportFileTitle', 'Project N.E.K.O Conversation Export');
        var titleLines = wrapTextLines(measureCtx, title, frameWidth - headerPaddingX * 2);
        var headerHeight = headerPaddingTop + titleLines.length * 40 + 12 + 20 + headerPaddingBottom;

        var listHeight = measured.reduce(function (sum, m) { return sum + m.blockHeight + cardGap; }, 0);
        var frameHeight = headerHeight + listHeight + listPadding;
        var totalHeight = outerPadding * 2 + frameHeight;

        var canvas = document.createElement('canvas');
        canvas.width = width * scale;
        canvas.height = totalHeight * scale;
        var ctx = canvas.getContext('2d');
        ctx.scale(scale, scale);
        ctx.imageSmoothingEnabled = true;
        ctx.imageSmoothingQuality = 'high';

        // background
        var bg = ctx.createLinearGradient(0, 0, 0, totalHeight);
        bg.addColorStop(0, theme.backgroundTop);
        bg.addColorStop(1, theme.backgroundBot);
        ctx.fillStyle = bg;
        ctx.fillRect(0, 0, width, totalHeight);

        // ambient glows
        var glow1 = ctx.createRadialGradient(width * 0.2, totalHeight * 0.25, 0, width * 0.2, totalHeight * 0.25, 520);
        glow1.addColorStop(0, theme.glowA);
        glow1.addColorStop(1, 'rgba(0,0,0,0)');
        ctx.fillStyle = glow1;
        ctx.fillRect(0, 0, width, totalHeight);
        var glow2 = ctx.createRadialGradient(width * 0.85, totalHeight * 0.8, 0, width * 0.85, totalHeight * 0.8, 520);
        glow2.addColorStop(0, theme.glowB);
        glow2.addColorStop(1, 'rgba(0,0,0,0)');
        ctx.fillStyle = glow2;
        ctx.fillRect(0, 0, width, totalHeight);

        // frame
        ctx.fillStyle = theme.card;
        drawRoundedRect(ctx, outerPadding, outerPadding, frameWidth, frameHeight, frameRadius);
        ctx.fill();
        ctx.strokeStyle = theme.cardBorder;
        drawRoundedRect(ctx, outerPadding + 0.5, outerPadding + 0.5, frameWidth - 1, frameHeight - 1, frameRadius);
        ctx.stroke();

        // header content
        ctx.textBaseline = 'top';
        ctx.font = kickerFont;
        ctx.fillStyle = theme.badgeText;
        var kicker = translateLabel('chat.exportPosterSubtitle', 'Shared from N.E.K.O').toUpperCase();
        var kickerX = outerPadding + headerPaddingX;
        var kickerY = outerPadding + headerPaddingTop;
        // badge background
        var kickerW = ctx.measureText(kicker).width + 20;
        ctx.fillStyle = theme.badgeBg;
        drawRoundedRect(ctx, kickerX - 10, kickerY - 6, kickerW, 22, 11);
        ctx.fill();
        ctx.fillStyle = theme.badgeText;
        ctx.fillText(kicker, kickerX, kickerY);

        ctx.font = titleFont;
        ctx.fillStyle = theme.title;
        drawWrappedText(ctx, titleLines, kickerX, kickerY + 28, 40);

        ctx.font = metaFont;
        ctx.fillStyle = theme.meta;
        ctx.fillText(buildDisplayTimestamp(now),
            kickerX, kickerY + 28 + titleLines.length * 40 + 8);

        // divider
        var dividerY = outerPadding + headerHeight;
        ctx.strokeStyle = theme.cardBorder;
        ctx.beginPath();
        ctx.moveTo(outerPadding + listPadding, dividerY);
        ctx.lineTo(outerPadding + frameWidth - listPadding, dividerY);
        ctx.stroke();

        // entries
        var y = dividerY + 24;
        measured.forEach(function (m) {
            var entry = m.entry;
            var isAssistant = m.isAssistant;
            var textX = outerPadding + listPadding;

            ctx.font = roleFont;
            ctx.fillStyle = isAssistant ? theme.roleAssistant : theme.roleUser;
            ctx.fillText(((entry.role || '') + (entry.time ? ' · ' + entry.time : '')).toUpperCase(),
                textX, y);
            y += 20;

            ctx.font = isAssistant ? lyricAssistantFont : lyricUserFont;
            ctx.fillStyle = isAssistant ? theme.lyricAssistant : theme.lyricUser;
            drawWrappedText(ctx, m.lyricLines, textX, y, lyricLineHeight);
            y += m.lyricLines.length * lyricLineHeight;

            if (m.noteLines.length > 0) {
                ctx.font = noteFont;
                ctx.fillStyle = theme.meta;
                y += 6;
                drawWrappedText(ctx, m.noteLines, textX, y, noteLineHeight);
                y += m.noteLines.length * noteLineHeight;
            }

            y += cardGap - 12;
        });

        return canvas;
    }

    async function renderImageCanvas(resolvedEntries, styleId, now) {
        if (styleId === 'original') return renderOriginalStyleCanvas(resolvedEntries, now);
        if (styleId === 'poster') return renderPosterStyleCanvas(resolvedEntries, now);
        if (styleId === 'lyrics') return renderLyricsStyleCanvas(resolvedEntries, now);
        return renderNekoStyleCanvas(resolvedEntries, now);
    }

    async function buildImageExportDocument(entries, now) {
        var style = getCurrentImageExportStyle();
        var format = getCurrentImageExportFormat();
        var resolved = await resolveImageEntryMedia(entries);
        var canvas = await renderImageCanvas(resolved, style.id, now);

        var blob;
        try {
            blob = await canvasToBlob(canvas, format.mimeType, format.quality);
        } catch (error) {
            throw new Error(translateText(
                'chat.exportImageFormatUnsupported',
                format.label + ' export is not supported in the current environment.',
                { format: format.label }
            ));
        }
        var previewBlob;
        try {
            previewBlob = await canvasToBlob(canvas, 'image/png');
        } catch (error) {
            previewBlob = blob;
        }

        return {
            fileName: getExportBaseFileName(now) + '-' + style.id + '.' + format.extension,
            contentType: format.mimeType,
            content: blob,
            previewBlob: previewBlob,
            width: canvas.width,
            height: canvas.height
        };
    }

    // ======================== Dispatcher ========================

    async function buildExportDocument(entries, formatId) {
        var now = new Date();
        if (formatId === 'image') {
            return buildImageExportDocument(entries, now);
        }
        return buildMarkdownExportDocument(entries, now);
    }

    // ======================== Download + copy ========================

    function downloadExportFile(fileName, content, contentType) {
        var blob = content instanceof Blob
            ? content
            : new Blob([content], { type: contentType });
        var url = URL.createObjectURL(blob);
        var link = document.createElement('a');
        link.href = url;
        link.download = fileName;
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    }

    async function copyTextToClipboard(text) {
        var value = String(text || '');
        if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
            try {
                await navigator.clipboard.writeText(value);
                return true;
            } catch (_) { /* fall through */ }
        }
        try {
            var textarea = document.createElement('textarea');
            textarea.value = value;
            textarea.setAttribute('readonly', 'readonly');
            textarea.style.position = 'fixed';
            textarea.style.opacity = '0';
            textarea.style.pointerEvents = 'none';
            textarea.style.left = '-9999px';
            document.body.appendChild(textarea);
            textarea.select();
            var ok = document.execCommand && document.execCommand('copy');
            document.body.removeChild(textarea);
            return !!ok;
        } catch (_) {
            return false;
        }
    }

    async function copyImageToClipboard(blob) {
        if (!navigator.clipboard || typeof navigator.clipboard.write !== 'function') {
            return false;
        }
        try {
            var pngBlob = blob;
            if (blob.type !== 'image/png') {
                // Clipboard API requires image/png — convert via canvas round-trip
                var img = new Image();
                var loaded = new Promise(function (resolve, reject) {
                    img.onload = resolve;
                    img.onerror = reject;
                });
                var blobUrl = URL.createObjectURL(blob);
                try {
                    img.src = blobUrl;
                    await loaded;
                    var cvs = document.createElement('canvas');
                    cvs.width = img.naturalWidth;
                    cvs.height = img.naturalHeight;
                    cvs.getContext('2d').drawImage(img, 0, 0);
                    pngBlob = await new Promise(function (resolve, reject) {
                        cvs.toBlob(function (b) { b ? resolve(b) : reject(new Error('toBlob failed')); }, 'image/png');
                    });
                } finally {
                    URL.revokeObjectURL(blobUrl);
                }
            }
            await navigator.clipboard.write([
                new ClipboardItem({ 'image/png': pngBlob })
            ]);
            return true;
        } catch (_) {
            return false;
        }
    }

    // ======================== Preview cache ========================

    function buildPreviewCacheKey(entries, formatId) {
        var currentFormatId = formatId || getCurrentExportFormat().id;
        var locale = document.documentElement.lang || '';
        var signature = (entries || []).map(function (entry) {
            return entry.id + ':' + (entry.textContent || '').length + ':' + (entry.mediaDescriptors ? entry.mediaDescriptors.length : 0);
        }).join('|');
        var imageStyleId = currentFormatId === 'image' ? getCurrentImageExportStyle().id : '';
        var imageFormatId = currentFormatId === 'image' ? getCurrentImageExportFormat().id : '';
        return [currentFormatId, imageStyleId, imageFormatId, locale, signature].join('::');
    }

    function revokePreviewPayload(payload) {
        if (payload && payload.previewUrl) {
            URL.revokeObjectURL(payload.previewUrl);
        }
    }

    function clearPreviewCache() {
        state.previewCache.forEach(function (entry) {
            if (entry && entry.payload) revokePreviewPayload(entry.payload);
        });
        state.previewCache.clear();
        state.previewCurrentCacheKey = '';
    }

    async function getOrBuildPreviewPayload(entries, formatId) {
        var targetFormatId = formatId || getCurrentExportFormat().id;
        var cacheKey = buildPreviewCacheKey(entries, targetFormatId);
        var cached = state.previewCache.get(cacheKey);
        if (cached && cached.payload) {
            return {
                cacheKey: cacheKey,
                exportData: cached.payload.exportData,
                previewKind: cached.payload.previewKind,
                previewUrl: cached.payload.previewUrl,
                previewDocument: cached.payload.previewDocument,
                fromCache: true
            };
        }
        if (cached && cached.promise) return cached.promise;

        var buildPromise = (async function () {
            var exportData = await buildExportDocument(entries, targetFormatId);
            var payload;
            if (targetFormatId === 'image') {
                payload = {
                    exportData: exportData,
                    previewKind: 'image',
                    previewUrl: URL.createObjectURL(exportData.previewBlob)
                };
            } else {
                payload = {
                    exportData: exportData,
                    previewKind: 'document',
                    previewDocument: buildMarkdownPreviewDocument(exportData.content)
                };
            }
            state.previewCache.set(cacheKey, { payload: payload });
            return {
                cacheKey: cacheKey,
                exportData: exportData,
                previewKind: payload.previewKind,
                previewUrl: payload.previewUrl,
                previewDocument: payload.previewDocument,
                fromCache: false
            };
        })();

        state.previewCache.set(cacheKey, { promise: buildPromise });
        try {
            return await buildPromise;
        } catch (error) {
            var current = state.previewCache.get(cacheKey);
            if (current && current.promise === buildPromise) {
                state.previewCache.delete(cacheKey);
            }
            throw error;
        }
    }

    // ======================== Preview modal ========================

    function createPreviewModal(targetDocument) {
        var doc = targetDocument || document;
        var backdrop = doc.createElement('div');
        backdrop.className = 'chat-export-preview-backdrop';
        backdrop.hidden = true;

        var panel = doc.createElement('div');
        panel.className = 'chat-export-preview-panel';
        panel.hidden = true;
        panel.setAttribute('role', 'dialog');
        panel.setAttribute('aria-modal', 'true');

        var header = doc.createElement('div');
        header.className = 'chat-export-preview-header container-header';

        var title = doc.createElement('h2');
        title.className = 'chat-export-preview-title';
        title.textContent = translateLabel('chat.exportPreviewTitle', 'Export Preview');
        title.setAttribute('data-text', title.textContent);

        var summary = doc.createElement('div');
        summary.className = 'chat-export-preview-summary';

        var closeButton = doc.createElement('button');
        closeButton.type = 'button';
        closeButton.className = 'chat-export-preview-close close-btn';
        closeButton.setAttribute('aria-label', translateLabel('common.close', 'Close'));
        var closeIcon = doc.createElement('img');
        closeIcon.src = '/static/icons/close_button.png';
        closeIcon.alt = translateLabel('common.close', 'Close');
        closeButton.appendChild(closeIcon);

        header.appendChild(title);
        header.appendChild(summary);
        header.appendChild(closeButton);

        var selectionSection = doc.createElement('div');
        selectionSection.className = 'chat-export-selection-section';

        var selectionToolbar = doc.createElement('div');
        selectionToolbar.className = 'chat-export-selection-toolbar';

        var selectAllButton = doc.createElement('button');
        selectAllButton.type = 'button';
        selectAllButton.className = 'chat-export-selection-tool';
        selectAllButton.textContent = translateLabel('chat.exportSelectAll', 'Select All');

        var selectNoneButton = doc.createElement('button');
        selectNoneButton.type = 'button';
        selectNoneButton.className = 'chat-export-selection-tool';
        selectNoneButton.textContent = translateLabel('chat.exportSelectNone', 'Clear');

        var selectInvertButton = doc.createElement('button');
        selectInvertButton.type = 'button';
        selectInvertButton.className = 'chat-export-selection-tool';
        selectInvertButton.textContent = translateLabel('chat.exportSelectInvert', 'Invert');

        selectionToolbar.appendChild(selectAllButton);
        selectionToolbar.appendChild(selectNoneButton);
        selectionToolbar.appendChild(selectInvertButton);

        var selectionList = doc.createElement('div');
        selectionList.className = 'chat-export-selection-list';

        selectionSection.appendChild(selectionToolbar);
        selectionSection.appendChild(selectionList);

        var controls = doc.createElement('div');
        controls.className = 'chat-export-preview-controls';

        var formatGroup = doc.createElement('div');
        formatGroup.className = 'chat-export-format-group';
        controls.appendChild(formatGroup);

        var imageOptions = doc.createElement('div');
        imageOptions.className = 'chat-export-image-options';
        controls.appendChild(imageOptions);

        var previewBody = doc.createElement('div');
        previewBody.className = 'chat-export-preview-body';

        var frame = doc.createElement('iframe');
        frame.className = 'chat-export-preview-frame';
        frame.hidden = true;
        frame.setAttribute('sandbox', 'allow-same-origin');
        frame.setAttribute('title', translateLabel('chat.exportPreviewTitle', 'Export Preview'));

        var previewImageWrap = doc.createElement('div');
        previewImageWrap.className = 'chat-export-preview-image-wrap';
        previewImageWrap.hidden = true;
        var previewImage = doc.createElement('img');
        previewImage.className = 'chat-export-preview-image';
        previewImage.alt = translateLabel('chat.exportPreviewTitle', 'Export Preview');
        previewImageWrap.appendChild(previewImage);

        var placeholder = doc.createElement('div');
        placeholder.className = 'chat-export-preview-placeholder';
        placeholder.textContent = translateLabel('chat.exportPreviewLoading', 'Generating preview...');

        previewBody.appendChild(frame);
        previewBody.appendChild(previewImageWrap);
        previewBody.appendChild(placeholder);

        var footer = doc.createElement('div');
        footer.className = 'chat-export-preview-footer';

        var copyButton = doc.createElement('button');
        copyButton.type = 'button';
        copyButton.className = 'chat-export-preview-action chat-export-preview-action-copy';
        copyButton.textContent = translateLabel('chat.copyToClipboard', 'Copy to Clipboard');

        var openWindowButton = doc.createElement('button');
        openWindowButton.type = 'button';
        openWindowButton.className = 'chat-export-preview-action chat-export-preview-action-open';
        openWindowButton.textContent = translateLabel('chat.previewOpenWindow', 'Open In Window');

        var downloadButton = doc.createElement('button');
        downloadButton.type = 'button';
        downloadButton.className = 'chat-export-preview-action chat-export-preview-action-download chat-export-preview-action-primary';
        downloadButton.textContent = translateLabel('chat.confirmExportAs', 'Export {{format}}', {
            format: translateLabel('chat.exportFormatMarkdown', 'Markdown')
        });

        footer.appendChild(copyButton);
        footer.appendChild(openWindowButton);
        footer.appendChild(downloadButton);

        panel.appendChild(header);
        panel.appendChild(selectionSection);
        panel.appendChild(controls);
        panel.appendChild(previewBody);
        panel.appendChild(footer);

        doc.body.appendChild(backdrop);
        doc.body.appendChild(panel);

        var modal = {
            backdrop: backdrop,
            panel: panel,
            title: title,
            summary: summary,
            closeButton: closeButton,
            closeIcon: closeIcon,
            selectionToolbar: selectionToolbar,
            selectAllButton: selectAllButton,
            selectNoneButton: selectNoneButton,
            selectInvertButton: selectInvertButton,
            selectionList: selectionList,
            formatGroup: formatGroup,
            imageOptions: imageOptions,
            previewBody: previewBody,
            frame: frame,
            previewImageWrap: previewImageWrap,
            previewImage: previewImage,
            placeholder: placeholder,
            copyButton: copyButton,
            openWindowButton: openWindowButton,
            downloadButton: downloadButton
        };

        closeButton.addEventListener('click', closePreviewModal);
        backdrop.addEventListener('click', closePreviewModal);
        panel.addEventListener('click', function (e) { e.stopPropagation(); });

        selectAllButton.addEventListener('click', function () {
            state.selectedIds.clear();
            var limit = Math.min(state.allMessages.length, MAX_EXPORT_SELECTION);
            for (var i = 0; i < limit; i++) {
                state.selectedIds.add(state.allMessages[i].id);
            }
            if (state.allMessages.length > MAX_EXPORT_SELECTION) {
                showToastMessage(translateText('chat.exportSelectionLimit',
                    'Selection is limited to {{max}} messages.',
                    { max: MAX_EXPORT_SELECTION }), 3000);
            }
            renderSelectionList();
            schedulePreviewRender();
        });
        selectNoneButton.addEventListener('click', function () {
            state.selectedIds.clear();
            renderSelectionList();
            schedulePreviewRender();
        });
        selectInvertButton.addEventListener('click', function () {
            var inverted = new Set();
            state.allMessages.forEach(function (message) {
                if (!state.selectedIds.has(message.id)) {
                    inverted.add(message.id);
                }
            });
            if (inverted.size > MAX_EXPORT_SELECTION) {
                var trimmed = new Set();
                var iter = inverted.values();
                for (var i = 0; i < MAX_EXPORT_SELECTION; i++) {
                    trimmed.add(iter.next().value);
                }
                inverted = trimmed;
                showToastMessage(translateText('chat.exportSelectionLimit',
                    'Selection is limited to {{max}} messages.',
                    { max: MAX_EXPORT_SELECTION }), 3000);
            }
            state.selectedIds = inverted;
            renderSelectionList();
            schedulePreviewRender();
        });

        copyButton.addEventListener('click', handleCopyClick);
        openWindowButton.addEventListener('click', handleOpenWindowClick);
        downloadButton.addEventListener('click', handleDownloadClick);

        // Update localized modal attributes when the app locale changes
        var localeHandler = function () {
            closeButton.setAttribute('aria-label', translateLabel('common.close', 'Close'));
            title.textContent = translateLabel('chat.exportPreviewTitle', 'Export Preview');
            title.setAttribute('data-text', title.textContent);
            frame.setAttribute('title', translateLabel('chat.exportPreviewTitle', 'Export Preview'));
            previewImage.alt = translateLabel('chat.exportPreviewTitle', 'Export Preview');
            closeIcon.alt = translateLabel('common.close', 'Close');
            selectAllButton.textContent = translateLabel('chat.exportSelectAll', 'Select All');
            selectNoneButton.textContent = translateLabel('chat.exportSelectNone', 'Clear');
            selectInvertButton.textContent = translateLabel('chat.exportSelectInvert', 'Invert');
            copyButton.textContent = translateLabel('chat.copyToClipboard', 'Copy to Clipboard');
            openWindowButton.textContent = translateLabel('chat.previewOpenWindow', 'Open In Window');
        };
        window.addEventListener('localechange', localeHandler);
        modal._localeHandler = localeHandler;

        return modal;
    }

    function getPreviewModalDocument(modal) {
        if (modal && modal.panel && modal.panel.ownerDocument) return modal.panel.ownerDocument;
        if (state.previewWindow && !state.previewWindow.closed && state.previewWindow.document) return state.previewWindow.document;
        return document;
    }

    function detachPreviewHandlers(modal) {
        var modalDocument = getPreviewModalDocument(modal);
        if (state.previewEscHandler) {
            try {
                modalDocument.removeEventListener('keydown', state.previewEscHandler);
            } catch (_) {}
            if (state.previewWindow && !state.previewWindow.closed && state.previewWindow.document !== modalDocument) {
                try {
                    state.previewWindow.document.removeEventListener('keydown', state.previewEscHandler);
                } catch (_) {}
            }
            state.previewEscHandler = null;
        }
        if (modal && modal._localeHandler) {
            window.removeEventListener('localechange', modal._localeHandler);
            modal._localeHandler = null;
        }
        if (state.previewWindow && state.previewWindow._localeHandler) {
            window.removeEventListener('localechange', state.previewWindow._localeHandler);
            state.previewWindow._localeHandler = null;
        }
    }

    function disposePreviewModal(closeWindow, destroyWindow) {
        var modal = state.previewModal;
        var previewWindow = state.previewWindow;
        var shouldDestroyWindow = !!(destroyWindow || closeWindow || (previewWindow && previewWindow.closed));
        state.previewRenderToken += 1;
        if (modal) {
            var modalDocument = getPreviewModalDocument(modal);
            try {
                modal.backdrop.hidden = true;
                modal.panel.hidden = true;
                modal.panel.classList.remove('is-open');
                modal.backdrop.classList.remove('is-open');
                if (modalDocument.body) modalDocument.body.classList.remove('chat-export-modal-open');
            } catch (_) {}
        }
        detachPreviewHandlers(modal);
        state.previewModal = null;
        if (shouldDestroyWindow && previewWindow && previewWindow._chatExportBeforeUnloadHandler) {
            try {
                previewWindow.removeEventListener('beforeunload', previewWindow._chatExportBeforeUnloadHandler);
            } catch (_) {}
            previewWindow._chatExportBeforeUnloadHandler = null;
        }
        if (closeWindow && previewWindow && !previewWindow.closed) {
            previewWindow.close();
        }
        if (shouldDestroyWindow && state.previewWindow === previewWindow) {
            state.previewWindow = null;
        }
    }

    function isPreviewModalUsable(modal, doc) {
        return !!(modal
            && modal.panel
            && modal.backdrop
            && modal.panel.ownerDocument === doc
            && modal.backdrop.ownerDocument === doc
            && modal.panel.isConnected
            && modal.backdrop.isConnected);
    }

    function ensurePreviewModal(targetDocument) {
        var doc = targetDocument || document;
        if (!isPreviewModalUsable(state.previewModal, doc)) {
            disposePreviewModal(false);
            state.previewModal = createPreviewModal(doc);
        }
        return state.previewModal;
    }

    function getSelectedEntries() {
        if (!state.allMessages || state.allMessages.length === 0) return [];
        return state.allMessages
            .filter(function (message) { return state.selectedIds.has(message.id); })
            .map(buildExportEntry);
    }

    function updateSummary() {
        var modal = state.previewModal;
        if (!modal) return;
        var selectedCount = state.selectedIds.size;
        var totalCount = state.allMessages.length;
        modal.summary.textContent = translateText(
            'chat.exportSelectionCount',
            'Selected {{selected}} / {{total}}',
            { selected: selectedCount, total: totalCount }
        );
    }

    function renderSelectionList() {
        var modal = state.previewModal;
        if (!modal) return;
        modal.selectionList.innerHTML = '';

        var doc = modal.selectionList.ownerDocument || document;
        state.allMessages.forEach(function (message) {
            var row = doc.createElement('label');
            row.className = 'chat-export-selection-row';

            var checkbox = doc.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.className = 'chat-export-selection-checkbox';
            checkbox.checked = state.selectedIds.has(message.id);
            checkbox.addEventListener('change', function () {
                if (checkbox.checked) {
                    if (state.selectedIds.size >= MAX_EXPORT_SELECTION) {
                        checkbox.checked = false;
                        showToastMessage(translateText('chat.exportSelectionLimit',
                            'Selection is limited to {{max}} messages.',
                            { max: MAX_EXPORT_SELECTION }), 3000);
                        return;
                    }
                    state.selectedIds.add(message.id);
                } else {
                    state.selectedIds.delete(message.id);
                }
                updateSummary();
                schedulePreviewRender();
            });

            var meta = doc.createElement('div');
            meta.className = 'chat-export-selection-meta';
            var author = doc.createElement('span');
            author.className = 'chat-export-selection-author';
            author.textContent = message.author || getRoleLabel(message.role);
            var time = doc.createElement('span');
            time.className = 'chat-export-selection-time';
            time.textContent = message.time || '';
            meta.appendChild(author);
            if (message.time) meta.appendChild(time);

            var preview = doc.createElement('div');
            preview.className = 'chat-export-selection-preview';
            var previewText = extractBlocksPlainText(message.blocks);
            preview.textContent = previewText.length > 160
                ? previewText.slice(0, 160) + '…'
                : previewText;

            var body = doc.createElement('div');
            body.className = 'chat-export-selection-body';
            body.appendChild(meta);
            body.appendChild(preview);

            row.appendChild(checkbox);
            row.appendChild(body);
            modal.selectionList.appendChild(row);
        });

        updateSummary();
    }

    function renderControls() {
        var modal = state.previewModal;
        if (!modal) return;
        var doc = modal.formatGroup.ownerDocument || document;

        // format chips
        modal.formatGroup.innerHTML = '';
        getExportFormats().forEach(function (format) {
            var chip = doc.createElement('button');
            chip.type = 'button';
            chip.className = 'chat-export-format-chip';
            chip.dataset.formatId = format.id;
            chip.textContent = format.label;
            if (format.id === state.exportFormat) chip.classList.add('is-active');
            chip.addEventListener('click', function () {
                if (state.exportFormat === format.id) return;
                state.exportFormat = format.id;
                renderControls();
                schedulePreviewRender();
            });
            modal.formatGroup.appendChild(chip);
        });

        // image options (style + format)
        modal.imageOptions.innerHTML = '';
        if (state.exportFormat === 'image') {
            var styleGroup = doc.createElement('div');
            styleGroup.className = 'chat-export-style-group';
            getImageExportStyles().forEach(function (style) {
                var chip = doc.createElement('button');
                chip.type = 'button';
                chip.className = 'chat-export-style-chip';
                chip.textContent = style.label;
                if (style.id === state.imageExportStyle) chip.classList.add('is-active');
                chip.addEventListener('click', function () {
                    if (state.imageExportStyle === style.id) return;
                    state.imageExportStyle = style.id;
                    renderControls();
                    schedulePreviewRender();
                });
                styleGroup.appendChild(chip);
            });
            modal.imageOptions.appendChild(styleGroup);

            var formatGroup2 = doc.createElement('div');
            formatGroup2.className = 'chat-export-image-format-group';
            getImageExportFormats().forEach(function (format) {
                var chip = doc.createElement('button');
                chip.type = 'button';
                chip.className = 'chat-export-image-format-chip';
                chip.textContent = format.label;
                if (format.id === state.imageExportFormat) chip.classList.add('is-active');
                chip.addEventListener('click', function () {
                    if (state.imageExportFormat === format.id) return;
                    state.imageExportFormat = format.id;
                    renderControls();
                    schedulePreviewRender();
                });
                formatGroup2.appendChild(chip);
            });
            modal.imageOptions.appendChild(formatGroup2);
        }

        // update copy button label based on format
        modal.copyButton.disabled = state.isCopying;
        modal.copyButton.textContent = translateLabel('chat.copyToClipboard', 'Copy to Clipboard');

        // update download button label
        var currentFormat = getCurrentExportFormat();
        modal.downloadButton.textContent = translateText(
            'chat.confirmExportAs',
            'Export {{format}}',
            { format: currentFormat.label }
        );
    }

    function schedulePreviewRender() {
        if (!state.previewModal) return;
        state.previewRenderToken += 1;
        var myToken = state.previewRenderToken;
        requestAnimationFrame(function () {
            if (myToken !== state.previewRenderToken) return;
            renderPreviewModal();
        });
    }

    async function renderPreviewModal() {
        var modal = state.previewModal || ensurePreviewModal();
        var entries = getSelectedEntries();

        renderControls();
        updateSummary();

        var formatId = state.exportFormat;

        if (entries.length === 0) {
            modal.frame.hidden = true;
            modal.previewImageWrap.hidden = true;
            modal.placeholder.hidden = false;
            modal.placeholder.textContent = translateLabel('chat.exportPreviewEmpty', 'There is nothing selected to preview.');
            modal.downloadButton.disabled = true;
            modal.openWindowButton.disabled = true;
            return;
        }

        modal.downloadButton.disabled = false;
        modal.openWindowButton.disabled = false;

        if (state.isPreviewRendering) return;
        state.isPreviewRendering = true;
        modal.placeholder.hidden = false;
        modal.placeholder.textContent = translateLabel('chat.exportPreviewLoading', 'Generating preview...');

        var myToken = state.previewRenderToken;

        try {
            var payload = await getOrBuildPreviewPayload(entries, formatId);
            if (myToken !== state.previewRenderToken) return;
            state.previewCurrentCacheKey = payload.cacheKey;

            if (payload.previewKind === 'image') {
                modal.previewImage.src = payload.previewUrl;
                modal.previewImageWrap.hidden = false;
                modal.frame.hidden = true;
                modal.placeholder.hidden = true;
            } else {
                modal.frame.srcdoc = payload.previewDocument;
                modal.frame.hidden = false;
                modal.previewImageWrap.hidden = true;
                modal.placeholder.hidden = true;
            }
        } catch (error) {
            logExportError('renderPreviewModal', error);
            modal.placeholder.hidden = false;
            modal.placeholder.textContent = translateLabel('chat.exportPreviewFailed', 'Failed to build the preview.')
                + ': ' + getErrorMessage(error);
            modal.frame.hidden = true;
            modal.previewImageWrap.hidden = true;
        } finally {
            state.isPreviewRendering = false;
        }
    }

    function buildExportWindowFeatures() {
        var width = Math.min(980, Math.max(760, Math.round((window.screen && window.screen.availWidth ? window.screen.availWidth : 980) * 0.72)));
        var height = Math.min(860, Math.max(620, Math.round((window.screen && window.screen.availHeight ? window.screen.availHeight : 760) * 0.78)));
        var left = Math.max(0, Math.round(((window.screen && window.screen.availWidth ? window.screen.availWidth : width) - width) / 2));
        var top = Math.max(0, Math.round(((window.screen && window.screen.availHeight ? window.screen.availHeight : height) - height) / 2));
        return 'popup=yes,toolbar=no,location=no,status=no,menubar=no,scrollbars=yes,resizable=yes,width='
            + width + ',height=' + height + ',left=' + left + ',top=' + top;
    }

    function openExportPreviewWindow() {
        var previewTitle = translateLabel('chat.exportPreviewTitle', 'Export Preview');
        var isExistingWindow = !!(state.previewWindow && !state.previewWindow.closed);
        var previewWindow = isExistingWindow
            ? state.previewWindow
            : window.open('', 'neko-chat-export-preview', buildExportWindowFeatures());
        if (!previewWindow) return null;

        if (isExistingWindow) {
            disposePreviewModal(false);
        }
        state.previewWindow = previewWindow;
        var doc = previewWindow.document;
        doc.open();
        doc.write('<!DOCTYPE html><html><head><meta charset="utf-8">'
            + '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
            + '<title>' + escapeHtml(previewTitle) + '</title>'
            + '<link rel="stylesheet" href="/static/css/api_key_settings.css">'
            + '<link rel="stylesheet" href="/static/css/index.css">'
            + '<link rel="stylesheet" href="/static/css/dark-mode.css">'
            + '<style>'
            + 'html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#f8fafc;}'
            + 'body.chat-export-window{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;padding:0;padding-bottom:0;min-height:100vh;}'
            + 'body.chat-export-window .chat-export-preview-backdrop{display:none!important;}'
            + 'body.chat-export-window .chat-export-preview-panel{position:fixed;inset:0;top:0;left:0;transform:none;width:100vw;height:100vh;max-height:none;border-radius:0;box-shadow:none;opacity:1;background:#fff;}'
            + 'body.chat-export-window .chat-export-preview-header.container-header{position:sticky;top:0;z-index:100;flex-shrink:0;border-bottom:0;}'
            + 'body.chat-export-window .chat-export-preview-title{white-space:nowrap;}'
            + 'body.chat-export-window .chat-export-preview-summary{color:rgba(255,255,255,.92);font-weight:600;text-shadow:0 1px 2px rgba(0,80,140,.24);}'
            + 'body.chat-export-window .chat-export-preview-close img{pointer-events:none;}'
            + 'body.chat-export-window .chat-export-preview-body{max-height:none;}'
            + '</style></head><body class="chat-export-window"></body></html>');
        doc.close();
        previewWindow.focus();
        if (!previewWindow._chatExportBeforeUnloadHandler) {
            var beforeUnloadHandler = function () {
                if (state.previewWindow === previewWindow) {
                    disposePreviewModal(false, true);
                }
            };
            previewWindow._chatExportBeforeUnloadHandler = beforeUnloadHandler;
            previewWindow.addEventListener('beforeunload', beforeUnloadHandler, { once: true });
        }
        return previewWindow;
    }

    async function openPreviewModal(previewWindow) {
        previewWindow = previewWindow || openExportPreviewWindow();
        if (!previewWindow) {
            showToast('chat.previewOpenBlocked', 'Unable to open a new preview window.', 4000);
            return;
        }
        var modal = ensurePreviewModal(previewWindow.document);

        // Re-register localeHandler if it was removed on previous close
        if (!modal._localeHandler) {
            var localeHandler = function () {
                modal.closeButton.setAttribute('aria-label', translateLabel('common.close', 'Close'));
                modal.title.textContent = translateLabel('chat.exportPreviewTitle', 'Export Preview');
                modal.title.setAttribute('data-text', modal.title.textContent);
                modal.frame.setAttribute('title', translateLabel('chat.exportPreviewTitle', 'Export Preview'));
                modal.previewImage.alt = translateLabel('chat.exportPreviewTitle', 'Export Preview');
                if (modal.closeIcon) modal.closeIcon.alt = translateLabel('common.close', 'Close');
                modal.selectAllButton.textContent = translateLabel('chat.exportSelectAll', 'Select All');
                modal.selectNoneButton.textContent = translateLabel('chat.exportSelectNone', 'Clear');
                modal.selectInvertButton.textContent = translateLabel('chat.exportSelectInvert', 'Invert');
                modal.copyButton.textContent = translateLabel('chat.copyToClipboard', 'Copy to Clipboard');
                modal.openWindowButton.textContent = translateLabel('chat.previewOpenWindow', 'Open In Window');
            };
            window.addEventListener('localechange', localeHandler);
            modal._localeHandler = localeHandler;
        }

        modal.backdrop.hidden = true;
        modal.panel.hidden = false;

        // Force a reflow before adding is-open so the opacity transition fires
        void modal.panel.offsetHeight;

        modal.panel.classList.add('is-open');
        modal.backdrop.classList.add('is-open');
        modal.panel.ownerDocument.body.classList.add('chat-export-modal-open');

        if (!state.previewEscHandler) {
            state.previewEscHandler = function (event) {
                if (event.key === 'Escape') closePreviewModal();
            };
            modal.panel.ownerDocument.addEventListener('keydown', state.previewEscHandler);
        }

        renderSelectionList();
        renderControls();
        await renderPreviewModal();
    }

    function closePreviewModal() {
        disposePreviewModal(true);
        clearPreviewCache();
    }

    // ======================== Action handlers ========================

    async function handleDownloadClick() {
        if (state.isExporting) return;
        var entries = getSelectedEntries();
        if (entries.length === 0) {
            showToast('chat.exportSelectionEmpty', 'Select at least one message to export.');
            return;
        }
        state.isExporting = true;
        var modal = state.previewModal;
        if (modal) modal.downloadButton.disabled = true;
        try {
            var payload = await getOrBuildPreviewPayload(entries, state.exportFormat);
            var data = payload.exportData;
            downloadExportFile(data.fileName, data.content, data.contentType);
            showToast('chat.exportSuccess', 'Conversation exported successfully');
        } catch (error) {
            logExportError('handleDownloadClick', error);
            showToastMessage(getErrorMessage(error), 4000);
        } finally {
            state.isExporting = false;
            if (modal) modal.downloadButton.disabled = false;
        }
    }

    async function handleCopyClick() {
        if (state.isCopying) return;
        var entries = getSelectedEntries();
        if (entries.length === 0) {
            showToast('chat.exportSelectionEmpty', 'Select at least one message to export.');
            return;
        }
        state.isCopying = true;
        var modal = state.previewModal;
        if (modal) modal.copyButton.disabled = true;
        try {
            if (state.exportFormat === 'image') {
                var imgPayload = await getOrBuildPreviewPayload(entries, 'image');
                var imgBlob = imgPayload.exportData.previewBlob || imgPayload.exportData.content;
                var imgOk = await copyImageToClipboard(imgBlob);
                if (imgOk) showToast('chat.copyImageSuccess', 'Image copied to clipboard.');
                else showToast('chat.copyImageFailed', 'Failed to copy image to clipboard.', 4000);
            } else {
                var mdPayload = await getOrBuildPreviewPayload(entries, 'markdown');
                var mdOk = await copyTextToClipboard(mdPayload.exportData.content);
                if (mdOk) showToast('chat.copyMarkdownSuccess', 'Markdown copied to clipboard.');
                else showToast('chat.copyMarkdownFailed', 'Failed to copy Markdown.', 4000);
            }
        } catch (error) {
            logExportError('handleCopyClick', error);
            if (state.exportFormat === 'image') {
                showToast('chat.copyImageFailed', 'Failed to copy image to clipboard.', 4000);
            } else {
                showToast('chat.copyMarkdownFailed', 'Failed to copy Markdown.', 4000);
            }
        } finally {
            state.isCopying = false;
            if (modal) modal.copyButton.disabled = false;
        }
    }

    /** Build the HTML for a draggable title-bar with a close button (for frameless Electron windows). */
    function buildWindowChromeHtml(title) {
        var closeLabel = escapeHtml(translateLabel('chat.previewClose', 'Close'));
        var scrollbarCss = '<style>'
            + '::-webkit-scrollbar{width:8px;height:8px;}'
            + '::-webkit-scrollbar-track{background:transparent;}'
            + '::-webkit-scrollbar-thumb{background:rgba(140,140,140,0.4);border-radius:4px;}'
            + '::-webkit-scrollbar-thumb:hover{background:rgba(140,140,140,0.6);}'
            + '::-webkit-scrollbar-corner{background:transparent;}'
            + '@media (prefers-color-scheme:dark){'
            + '::-webkit-scrollbar-thumb{background:rgba(200,200,200,0.25);}'
            + '::-webkit-scrollbar-thumb:hover{background:rgba(200,200,200,0.4);}'
            + '}'
            + '</style>';
        return scrollbarCss
            + '<div style="position:fixed;top:0;left:0;right:0;height:36px;display:flex;align-items:center;'
            + 'justify-content:space-between;background:rgba(30,30,30,0.85);-webkit-app-region:drag;z-index:9999;'
            + 'padding:0 8px;user-select:none;backdrop-filter:blur(6px);">'
            + '<span style="color:#ccc;font-size:13px;margin-left:8px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">'
            + escapeHtml(title) + '</span>'
            + '<button onclick="window.close()" title="' + closeLabel + '" style="-webkit-app-region:no-drag;'
            + 'background:none;border:none;color:#fff;font-size:20px;cursor:pointer;width:36px;height:36px;'
            + 'display:flex;align-items:center;justify-content:center;border-radius:4px;flex-shrink:0;" '
            + 'onmouseover="this.style.background=\'#e81123\'" onmouseout="this.style.background=\'none\'">&times;</button>'
            + '</div>';
    }

    async function handleOpenWindowClick() {
        var entries = getSelectedEntries();
        if (entries.length === 0) {
            showToast('chat.exportSelectionEmpty', 'Select at least one message to export.');
            return;
        }
        try {
            var payload = await getOrBuildPreviewPayload(entries, state.exportFormat);
            var previewTitle = translateLabel('chat.exportPreviewTitle', 'Export Preview');
            var chromeHtml = buildWindowChromeHtml(previewTitle);
            if (payload.previewKind === 'image') {
                var imgUrl = payload.previewUrl;
                if (!isSafeUrl(imgUrl)) {
                    showToast('chat.previewOpenFailed', 'The preview URL uses an unsupported protocol.', 4000);
                    return;
                }
                var imgWin = window.open('', '_blank');
                if (!imgWin) {
                    showToast('chat.previewOpenBlocked', 'Unable to open a new preview window.', 4000);
                    return;
                }
                imgWin.document.write('<!DOCTYPE html><html><head><title>'
                    + escapeHtml(previewTitle)
                    + '</title></head><body style="margin:0;background:#111;display:flex;align-items:center;justify-content:center;min-height:100vh;padding-top:36px;">'
                    + chromeHtml
                    + '<img src="' + escapeHtml(imgUrl) + '" style="max-width:100%;max-height:calc(100vh - 36px);"/></body></html>');
                imgWin.document.close();
                return;
            }
            var doc = payload.previewDocument;
            // Sanitize any unsafe protocol URLs before injecting into the new window
            doc = sanitizeHtmlUrls(doc);
            // Inject window chrome (title bar + close button) into the preview document
            doc = doc.replace(/(<body[^>]*>)/, '$1' + chromeHtml + '<div style="padding-top:36px;">');
            doc = doc.replace(/<\/body>/, '</div></body>');
            var win = window.open('', '_blank');
            if (!win) {
                showToast('chat.previewOpenBlocked', 'Unable to open a new preview window.', 4000);
                return;
            }
            win.document.write(doc);
            win.document.close();
        } catch (error) {
            logExportError('handleOpenWindowClick', error);
            showToast('chat.previewOpenFailed', 'Failed to open the preview window.', 4000);
        }
    }

    // ======================== Entry point ========================

    async function handleExportButtonClick(event) {
        if (event) {
            event.preventDefault();
            event.stopPropagation();
        }
        if (state.isPreparingPreview) {
            return;
        }

        var previewWindow = openExportPreviewWindow();
        if (!previewWindow) {
            showToast('chat.previewOpenBlocked', 'Unable to open a new preview window.', 4000);
            return;
        }

        var host = getReactChatHost();
        if (host && typeof host.ensureBundleLoaded === 'function') {
            try {
                await host.ensureBundleLoaded();
            } catch (error) {
                logExportError('ensureBundleLoaded', error);
            }
        }

        var messages = getReactMessages();
        if (messages.length === 0) {
            showToast('chat.exportEmpty', 'There is no conversation to export yet.', 3000);
            closePreviewModal();
            return;
        }

        state.isPreparingPreview = true;
        try {
            state.allMessages = messages;
            state.selectedIds = new Set();
            clearPreviewCache();
            await openPreviewModal(previewWindow);
        } catch (error) {
            logExportError('handleExportButtonClick', error);
            showToastMessage(
                translateLabel('chat.exportPreviewFailed', 'Failed to build the preview.')
                + ': ' + getErrorMessage(error),
                5000
            );
        } finally {
            state.isPreparingPreview = false;
        }
    }

    function init() {
        var button = document.getElementById('exportConversationButton');
        if (!button) return;

        button.addEventListener('click', handleExportButtonClick);

        window.addEventListener('localechange', function () {
            if (!state.previewModal || state.previewModal.panel.hidden) return;
            clearPreviewCache();
            renderSelectionList();
            renderControls();
            schedulePreviewRender();
        });
    }

    async function initAfterStorageBarrier() {
        if (typeof window.waitForStorageLocationStartupBarrier === 'function') {
            try {
                await window.waitForStorageLocationStartupBarrier();
            } catch (_) {}
        } else if (window.__nekoStorageLocationStartupBarrier
            && typeof window.__nekoStorageLocationStartupBarrier.then === 'function') {
            try {
                await window.__nekoStorageLocationStartupBarrier;
            } catch (_) {}
        }
        init();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initAfterStorageBarrier);
    } else {
        initAfterStorageBarrier();
    }

    window.appChatExport = {
        open: handleExportButtonClick,
        close: closePreviewModal
    };
})();
