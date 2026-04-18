/**
 * app-chat-text-utils.js — 聊天文本共享小工具
 *
 * 唯一职责：把 AI 文本的结构化富文本检测集中到一份实现。
 * 之前 app-chat.js 和 app-chat-adapter.js 各自持有一份字面量一致的
 * looksLikeStructuredRichText 拷贝（后者注释写着"从 app-chat.js 复刻"），
 * 任何一边补规则、修误判、调顺序，两条路径（老 DOM 气泡 / React adapter）
 * 就会对同一段文本给出不同的 window._turnIsStructured，字幕占位和 turn_end
 * 收尾会从此分叉。（CodeRabbit nitpick on PR #778）
 *
 * 加载顺序：必须在 app-chat.js 和 app-chat-adapter.js 之前 include。
 * 不 import subtitle.js / websocket 任何符号；本文件是叶子，不依赖其它业务模块。
 */
(function () {
    'use strict';

    // 归一化换行：CRLF → LF，保证下方正则在 Windows 输入下也稳定
    function normalizeGeminiText(s) {
        return (s || '').replace(/\r\n/g, '\n');
    }

    // 结构化富文本识别：命中任一则本轮字幕走 [markdown] 占位、turn_end 跳翻译。
    // 覆盖：
    //   - 代码块 ``` ... ``` / 行首 ```
    //   - 块级 LaTeX $$ ... $$ / 行内单 $ ... $
    //   - markdown 标题 / 列表 / 有序列表 / 引用
    //   - markdown 表格（至少一行带分隔线 |---|---|）
    function looksLikeStructuredRichText(text) {
        var s = normalizeGeminiText(text || '');
        return /```[\s\S]*```/.test(s)
            || /(?:^|\n)```/.test(s)
            || /\$\$[\s\S]*\$\$/.test(s)
            || /(?<!\$)\$(?!\$)[^$\n]+(?<!\$)\$(?!\$)/.test(s)
            || /(?:^|\n)\s{0,3}(?:#{1,6}\s|[-*+]\s|\d+\.\s|>\s)/.test(s)
            || /(?:^|\n)\|.+\|.+(?:\n|\r\n)\|(?:[-: ]+\|){1,}/.test(s);
    }

    window.appChatTextUtils = window.appChatTextUtils || {};
    window.appChatTextUtils.normalizeGeminiText = normalizeGeminiText;
    window.appChatTextUtils.looksLikeStructuredRichText = looksLikeStructuredRichText;
})();
