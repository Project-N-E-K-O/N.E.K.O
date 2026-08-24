/**
 * forge-drop-tokens.js — 锻造券稀有度色板 / 原因文案（与 BetaMintScreen 对齐）
 *
 * 供 forge-drop-overlay.js / forge-avatar-reaction.js 共用。
 */
(function (global) {
  'use strict';

  var RARITY = {
    UR:  { c: '#ff536f', glow: 'rgba(255,83,111,.64)' },
    SSR: { c: '#ff6478', glow: 'rgba(255,100,120,.6)' },
    SR:  { c: '#ff5daa', glow: 'rgba(255,93,170,.58)' },
    R:   { c: '#9d5cff', glow: 'rgba(157,92,255,.58)' },
    N:   { c: '#47c8f5', glow: 'rgba(71,200,245,.52)' },
  };

  var TICKET_PATHS = {
    UR: '/static/assets/forge-tickets/forge-ticket-ur.png?v=20260718-hd',
    SSR: '/static/assets/forge-tickets/forge-ticket-ssr.png?v=20260717-hd',
    SR: '/static/assets/forge-tickets/forge-ticket-sr.png?v=20260717-hd',
    R: '/static/assets/forge-tickets/forge-ticket-r.png?v=20260717-hd',
    N: '/static/assets/forge-tickets/forge-ticket-n.png?v=20260717-hd',
  };

  var REASON_TEXT = {
    emotion_combo: '喵现在超开心～',
    '5rounds': '陪喵聊了好一会儿',
    idle: '喵一直在等你回来',
    minigame: '陪喵玩了小游戏',
    like_daily: '每日点赞奖励',
  };

  var SOUND_PATHS = {
    N: '/static/sounds/forge/rarity-n.mp3?v=20260718-user',
    R: '/static/sounds/forge/rarity-r.mp3?v=20260718-user',
    SR: '/static/sounds/forge/rarity-sr.wav?v=20260718-user',
    SSR: '/static/sounds/forge/rarity-ssr.mp3?v=20260718-user',
    UR: '/static/sounds/forge/rarity-ur.mp3?v=20260718-user',
  };

  function normalizeRarity(rarity) {
    var key = String(rarity || 'N').toUpperCase();
    return Object.prototype.hasOwnProperty.call(RARITY, key) ? key : 'N';
  }

  function rarityColor(rarity) {
    return RARITY[normalizeRarity(rarity)].c;
  }

  function rarityGlow(rarity) {
    return RARITY[normalizeRarity(rarity)].glow;
  }

  function ticketPath(rarity) {
    return TICKET_PATHS[normalizeRarity(rarity)];
  }

  function reasonText(reason) {
    return REASON_TEXT[reason] || '一个小小的奇遇';
  }

  global.NekoForgeDropTokens = {
    RARITY: RARITY,
    TICKET_PATHS: TICKET_PATHS,
    REASON_TEXT: REASON_TEXT,
    SOUND_PATHS: SOUND_PATHS,
    normalizeRarity: normalizeRarity,
    rarityColor: rarityColor,
    rarityGlow: rarityGlow,
    ticketPath: ticketPath,
    reasonText: reasonText,
  };
})(typeof window !== 'undefined' ? window : this);
