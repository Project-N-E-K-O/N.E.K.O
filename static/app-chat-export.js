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

    // ======================== State ========================

    var state = {
        isPreparingPreview: false,
        isExporting: false,
        exportFormat: 'markdown',
        imageExportFormat: 'png',
        imageExportStyle: 'neko',
        selectedIds: null,          // Set<string> of ChatMessage.id
        allMessages: [],            // latest snapshot of messages from host
        previewModal: null,         // { backdrop, panel, frame, img, ... }
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
        return {
            id: String(message.id),
            role: role,
            author: author,
            time: time,
            header: header,
            rawRole: message.role,
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
        // images first (they look like links)
        source = source.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, function (_, alt, url) {
            return '<img src="' + url + '" alt="' + alt + '">';
        });
        source = source.replace(/\[([^\]]+)\]\(([^)]+)\)/g, function (_, label, url) {
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

    function loadImageElement(source) {
        return new Promise(function (resolve, reject) {
            var image = new Image();
            image.crossOrigin = 'anonymous';
            image.decoding = 'async';
            image.onload = function () { resolve(image); };
            image.onerror = function () { reject(new Error('Failed to load image asset.')); };
            image.src = source;
        });
    }

    async function inlineImageSourceToDataUrl(source) {
        if (!source) throw new Error('Image source missing.');
        if (/^data:/i.test(source)) return source;
        try {
            var response = await fetch(source, { mode: 'cors' });
            if (!response.ok) throw new Error('Image fetch failed: HTTP ' + response.status);
            return await blobToDataUrl(await response.blob());
        } catch (error) {
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
            var loaded = [];
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
                var image = await promise;
                if (image) {
                    loaded.push({ type: 'image', image: image, alt: descriptor.alt });
                } else {
                    loaded.push({
                        type: 'note',
                        text: (descriptor.alt ? descriptor.alt + ' — ' : '')
                            + translateLabel('chat.exportImageLabel', 'Image')
                    });
                }
            }
            resolved.push({
                id: entry.id,
                role: entry.role,
                author: entry.author,
                time: entry.time,
                rawRole: entry.rawRole,
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
            gradientTop: dark ? '#1e1b4b' : '#fde68a',
            gradientMid: dark ? '#312e81' : '#fca5a5',
            gradientBot: dark ? '#4c1d95' : '#f472b6',
            card:       dark ? 'rgba(15,23,42,0.85)' : 'rgba(255,255,255,0.92)',
            textPrimary: dark ? '#fff8e7' : '#1f2937',
            textSecondary: dark ? '#e5e7eb' : '#4b5563',
            accent:     dark ? '#facc15' : '#db2777'
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

        if (entry.textContent) {
            var lines = wrapTextLines(ctx, entry.textContent, maxWidth);
            segments.push({ kind: 'text', lines: lines, lineHeight: bodyLineHeight });
            height += lines.length * bodyLineHeight;
        }

        if (includeImages && entry.media && entry.media.length > 0) {
            entry.media.forEach(function (item) {
                if (item.type === 'image') {
                    var size = fitImageToWidth(item.image, maxWidth, maxImageHeight || 240);
                    segments.push({ kind: 'image', width: size.width, height: size.height, image: item.image });
                    height += size.height + 8;
                } else if (item.type === 'note' && item.text) {
                    var noteLines = wrapTextLines(ctx, item.text, maxWidth);
                    segments.push({ kind: 'note', lines: noteLines, lineHeight: bodyLineHeight });
                    height += noteLines.length * bodyLineHeight + 4;
                }
            });
        }

        return { segments: segments, height: height };
    }

    function drawSegments(ctx, segments, x, y, options) {
        options = options || {};
        var noteColor = options.noteColor;
        segments.forEach(function (segment) {
            if (segment.kind === 'text') {
                y = drawWrappedText(ctx, segment.lines, x, y, segment.lineHeight);
            } else if (segment.kind === 'image') {
                try { ctx.drawImage(segment.image, x, y, segment.width, segment.height); }
                catch (_) { /* draw failed, skip */ }
                y += segment.height + 8;
            } else if (segment.kind === 'note') {
                if (noteColor) {
                    var prev = ctx.fillStyle;
                    ctx.fillStyle = noteColor;
                    y = drawWrappedText(ctx, segment.lines, x, y, segment.lineHeight) + 4;
                    ctx.fillStyle = prev;
                } else {
                    y = drawWrappedText(ctx, segment.lines, x, y, segment.lineHeight) + 4;
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
        var width = 900;
        var padding = 60;
        var heroHeight = 220;
        var cardPadding = 26;
        var cardGap = 18;
        var titleFont = '800 42px -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif';
        var kickerFont = '700 14px -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif';
        var authorFont = '700 16px -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif';
        var bodyFont = '500 17px -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif';
        var metaFont = '500 12px -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif';
        var bodyLineHeight = 28;
        var cardMaxBodyWidth = width - padding * 2 - cardPadding * 2;

        var measureCanvas = document.createElement('canvas');
        var measureCtx = measureCanvas.getContext('2d');
        var measured = resolvedEntries.map(function (entry) {
            measureCtx.font = bodyFont;
            var body = measureEntryBody(measureCtx, entry, bodyFont, bodyLineHeight, cardMaxBodyWidth, true, 260);
            var cardHeight = cardPadding * 2 + 30 + 6 + body.height;
            return { entry: entry, body: body, cardHeight: cardHeight };
        });
        var cardsHeight = measured.reduce(function (sum, m) { return sum + m.cardHeight + cardGap; }, 0);
        var footerBlock = 50;
        var totalHeight = heroHeight + cardsHeight + padding + footerBlock;

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
        ctx.fillStyle = theme.accent;
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

        ctx.font = '500 14px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif';
        ctx.fillStyle = theme.textSecondary;
        ctx.fillText(buildDisplayTimestamp(now), padding, padding + 30 + titleLines.length * 48 + 4);

        // cards
        var y = heroHeight;
        measured.forEach(function (m) {
            var entry = m.entry;
            var cardX = padding;
            var cardY = y;
            var cardW = width - padding * 2;
            var cardH = m.cardHeight;

            ctx.save();
            ctx.shadowColor = 'rgba(0,0,0,0.15)';
            ctx.shadowBlur = 18;
            ctx.shadowOffsetY = 6;
            ctx.fillStyle = theme.card;
            drawRoundedRect(ctx, cardX, cardY, cardW, cardH, 16);
            ctx.fill();
            ctx.restore();

            // author
            ctx.font = authorFont;
            ctx.fillStyle = theme.accent;
            ctx.fillText(entry.author || entry.role || '', cardX + cardPadding, cardY + cardPadding);

            // meta
            ctx.font = metaFont;
            ctx.fillStyle = theme.textSecondary;
            ctx.fillText([entry.role, entry.time].filter(Boolean).join(' · '),
                cardX + cardPadding, cardY + cardPadding + 20);

            // body
            ctx.font = bodyFont;
            ctx.fillStyle = theme.textPrimary;
            drawSegments(
                ctx,
                m.body.segments,
                cardX + cardPadding,
                cardY + cardPadding + 40,
                { noteColor: theme.textSecondary }
            );

            y += cardH + cardGap;
        });

        // footer
        ctx.font = '700 12px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif';
        ctx.fillStyle = theme.accent;
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

    function createPreviewModal() {
        var backdrop = document.createElement('div');
        backdrop.className = 'chat-export-preview-backdrop';
        backdrop.hidden = true;

        var panel = document.createElement('div');
        panel.className = 'chat-export-preview-panel';
        panel.hidden = true;
        panel.setAttribute('role', 'dialog');
        panel.setAttribute('aria-modal', 'true');

        var header = document.createElement('div');
        header.className = 'chat-export-preview-header';

        var title = document.createElement('h2');
        title.className = 'chat-export-preview-title';
        title.textContent = translateLabel('chat.exportPreviewTitle', 'Export Preview');

        var summary = document.createElement('div');
        summary.className = 'chat-export-preview-summary';

        var closeButton = document.createElement('button');
        closeButton.type = 'button';
        closeButton.className = 'chat-export-preview-close';
        closeButton.setAttribute('aria-label', translateLabel('common.close', 'Close'));
        closeButton.textContent = '\u274C';

        header.appendChild(title);
        header.appendChild(summary);
        header.appendChild(closeButton);

        var selectionSection = document.createElement('div');
        selectionSection.className = 'chat-export-selection-section';

        var selectionToolbar = document.createElement('div');
        selectionToolbar.className = 'chat-export-selection-toolbar';

        var selectAllButton = document.createElement('button');
        selectAllButton.type = 'button';
        selectAllButton.className = 'chat-export-selection-tool';
        selectAllButton.textContent = translateLabel('chat.exportSelectAll', 'Select All');

        var selectNoneButton = document.createElement('button');
        selectNoneButton.type = 'button';
        selectNoneButton.className = 'chat-export-selection-tool';
        selectNoneButton.textContent = translateLabel('chat.exportSelectNone', 'Clear');

        var selectInvertButton = document.createElement('button');
        selectInvertButton.type = 'button';
        selectInvertButton.className = 'chat-export-selection-tool';
        selectInvertButton.textContent = translateLabel('chat.exportSelectInvert', 'Invert');

        selectionToolbar.appendChild(selectAllButton);
        selectionToolbar.appendChild(selectNoneButton);
        selectionToolbar.appendChild(selectInvertButton);

        var selectionList = document.createElement('div');
        selectionList.className = 'chat-export-selection-list';

        selectionSection.appendChild(selectionToolbar);
        selectionSection.appendChild(selectionList);

        var controls = document.createElement('div');
        controls.className = 'chat-export-preview-controls';

        var formatGroup = document.createElement('div');
        formatGroup.className = 'chat-export-format-group';
        controls.appendChild(formatGroup);

        var imageOptions = document.createElement('div');
        imageOptions.className = 'chat-export-image-options';
        controls.appendChild(imageOptions);

        var previewBody = document.createElement('div');
        previewBody.className = 'chat-export-preview-body';

        var frame = document.createElement('iframe');
        frame.className = 'chat-export-preview-frame';
        frame.hidden = true;
        frame.setAttribute('sandbox', 'allow-same-origin');
        frame.setAttribute('title', translateLabel('chat.exportPreviewTitle', 'Export Preview'));

        var previewImageWrap = document.createElement('div');
        previewImageWrap.className = 'chat-export-preview-image-wrap';
        previewImageWrap.hidden = true;
        var previewImage = document.createElement('img');
        previewImage.className = 'chat-export-preview-image';
        previewImage.alt = translateLabel('chat.exportPreviewTitle', 'Export Preview');
        previewImageWrap.appendChild(previewImage);

        var placeholder = document.createElement('div');
        placeholder.className = 'chat-export-preview-placeholder';
        placeholder.textContent = translateLabel('chat.exportPreviewLoading', 'Generating preview...');

        previewBody.appendChild(frame);
        previewBody.appendChild(previewImageWrap);
        previewBody.appendChild(placeholder);

        var footer = document.createElement('div');
        footer.className = 'chat-export-preview-footer';

        var copyButton = document.createElement('button');
        copyButton.type = 'button';
        copyButton.className = 'chat-export-preview-action chat-export-preview-action-copy';
        copyButton.textContent = translateLabel('chat.copyMarkdown', 'Copy Markdown');

        var openWindowButton = document.createElement('button');
        openWindowButton.type = 'button';
        openWindowButton.className = 'chat-export-preview-action chat-export-preview-action-open';
        openWindowButton.textContent = translateLabel('chat.previewOpenWindow', 'Open In Window');

        var downloadButton = document.createElement('button');
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

        document.body.appendChild(backdrop);
        document.body.appendChild(panel);

        var modal = {
            backdrop: backdrop,
            panel: panel,
            title: title,
            summary: summary,
            closeButton: closeButton,
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
            state.allMessages.forEach(function (message) { state.selectedIds.add(message.id); });
            renderSelectionList();
            schedulePreviewRender();
        });
        selectNoneButton.addEventListener('click', function () {
            state.selectedIds.clear();
            renderSelectionList();
            schedulePreviewRender();
        });
        selectInvertButton.addEventListener('click', function () {
            state.allMessages.forEach(function (message) {
                if (state.selectedIds.has(message.id)) {
                    state.selectedIds.delete(message.id);
                } else {
                    state.selectedIds.add(message.id);
                }
            });
            renderSelectionList();
            schedulePreviewRender();
        });

        copyButton.addEventListener('click', handleCopyClick);
        openWindowButton.addEventListener('click', handleOpenWindowClick);
        downloadButton.addEventListener('click', handleDownloadClick);

        return modal;
    }

    function ensurePreviewModal() {
        if (!state.previewModal) {
            state.previewModal = createPreviewModal();
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

        state.allMessages.forEach(function (message) {
            var row = document.createElement('label');
            row.className = 'chat-export-selection-row';

            var checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.className = 'chat-export-selection-checkbox';
            checkbox.checked = state.selectedIds.has(message.id);
            checkbox.addEventListener('change', function () {
                if (checkbox.checked) state.selectedIds.add(message.id);
                else state.selectedIds.delete(message.id);
                updateSummary();
                schedulePreviewRender();
            });

            var meta = document.createElement('div');
            meta.className = 'chat-export-selection-meta';
            var author = document.createElement('span');
            author.className = 'chat-export-selection-author';
            author.textContent = message.author || getRoleLabel(message.role);
            var time = document.createElement('span');
            time.className = 'chat-export-selection-time';
            time.textContent = message.time || '';
            meta.appendChild(author);
            if (message.time) meta.appendChild(time);

            var preview = document.createElement('div');
            preview.className = 'chat-export-selection-preview';
            var previewText = extractBlocksPlainText(message.blocks);
            preview.textContent = previewText.length > 160
                ? previewText.slice(0, 160) + '…'
                : previewText;

            var body = document.createElement('div');
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

        // format chips
        modal.formatGroup.innerHTML = '';
        getExportFormats().forEach(function (format) {
            var chip = document.createElement('button');
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
            var styleGroup = document.createElement('div');
            styleGroup.className = 'chat-export-style-group';
            getImageExportStyles().forEach(function (style) {
                var chip = document.createElement('button');
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

            var formatGroup2 = document.createElement('div');
            formatGroup2.className = 'chat-export-image-format-group';
            getImageExportFormats().forEach(function (format) {
                var chip = document.createElement('button');
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

        // update copy button enabled state
        modal.copyButton.disabled = state.exportFormat !== 'markdown';
        modal.copyButton.textContent = translateLabel('chat.copyMarkdown', 'Copy Markdown');

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
        var modal = ensurePreviewModal();
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

    async function openPreviewModal() {
        var modal = ensurePreviewModal();
        modal.backdrop.hidden = false;
        modal.panel.hidden = false;

        // Force a reflow before adding is-open so the opacity transition fires
        void modal.panel.offsetHeight;

        modal.panel.classList.add('is-open');
        modal.backdrop.classList.add('is-open');
        document.body.classList.add('chat-export-modal-open');

        if (!state.previewEscHandler) {
            state.previewEscHandler = function (event) {
                if (event.key === 'Escape') closePreviewModal();
            };
            document.addEventListener('keydown', state.previewEscHandler);
        }

        renderSelectionList();
        renderControls();
        await renderPreviewModal();
    }

    function closePreviewModal() {
        var modal = state.previewModal;
        if (!modal) return;
        modal.backdrop.hidden = true;
        modal.panel.hidden = true;
        modal.panel.classList.remove('is-open');
        modal.backdrop.classList.remove('is-open');
        document.body.classList.remove('chat-export-modal-open');
        clearPreviewCache();
        if (state.previewEscHandler) {
            document.removeEventListener('keydown', state.previewEscHandler);
            state.previewEscHandler = null;
        }
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
        if (state.exportFormat !== 'markdown') return;
        var entries = getSelectedEntries();
        if (entries.length === 0) {
            showToast('chat.exportSelectionEmpty', 'Select at least one message to export.');
            return;
        }
        try {
            var payload = await getOrBuildPreviewPayload(entries, 'markdown');
            var ok = await copyTextToClipboard(payload.exportData.content);
            if (ok) showToast('chat.copyMarkdownSuccess', 'Markdown copied to clipboard.');
            else showToast('chat.copyMarkdownFailed', 'Failed to copy Markdown.', 4000);
        } catch (error) {
            logExportError('handleCopyClick', error);
            showToast('chat.copyMarkdownFailed', 'Failed to copy Markdown.', 4000);
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
                var imgWin = window.open('', '_blank');
                if (!imgWin) {
                    showToast('chat.previewOpenBlocked', 'Unable to open a new preview window.', 4000);
                    return;
                }
                imgWin.document.write('<!DOCTYPE html><html><head><title>'
                    + escapeHtml(previewTitle)
                    + '</title></head><body style="margin:0;background:#111;display:flex;align-items:center;justify-content:center;min-height:100vh;padding-top:36px;">'
                    + chromeHtml
                    + '<img src="' + imgUrl + '" style="max-width:100%;max-height:calc(100vh - 36px);"/></body></html>');
                imgWin.document.close();
                return;
            }
            var doc = payload.previewDocument;
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
            return;
        }

        state.isPreparingPreview = true;
        try {
            state.allMessages = messages;
            state.selectedIds = new Set(messages.map(function (message) { return message.id; }));
            clearPreviewCache();
            await openPreviewModal();
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

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    window.appChatExport = {
        open: handleExportButtonClick,
        close: closePreviewModal
    };
})();
