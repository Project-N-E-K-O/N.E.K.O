/**
 * forge-drop-tokens.js — 锻造券稀有度色板 / 原因文案（与 BetaMintScreen 对齐）
 *
 * 供 forge-drop-overlay.js / forge-avatar-reaction.js 共用。
 */
(function (global) {
  'use strict';

  var RARITY = {
    UR:  { c: '#ff5d8f', glow: 'rgba(255,93,143,.6)' },
    SSR: { c: '#ffb627', glow: 'rgba(255,182,39,.6)' },
    SR:  { c: '#b06bff', glow: 'rgba(176,107,255,.6)' },
    R:   { c: '#39b7f5', glow: 'rgba(57,183,245,.6)' },
    N:   { c: '#9aa6bd', glow: 'rgba(154,166,189,.5)' },
  };

  var REASON_TEXT = {
    emotion_combo: '喵现在超开心～',
    '5rounds': '陪喵聊了好一会儿',
    idle: '喵一直在等你回来',
    minigame: '陪喵玩了小游戏',
    like_daily: '每日点赞奖励',
  };

  var SOUND_PATHS = {
    N: '/static/sounds/forge/rarity-n.mp3',
    R: '/static/sounds/forge/rarity-r.mp3',
    SR: '/static/sounds/forge/rarity-sr.mp3',
    SSR: '/static/sounds/forge/rarity-ssr.mp3',
    UR: '/static/sounds/forge/rarity-ur.mp3',
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

  function reasonText(reason) {
    return REASON_TEXT[reason] || '一个小小的奇遇';
  }

  global.NekoForgeDropTokens = {
    RARITY: RARITY,
    REASON_TEXT: REASON_TEXT,
    SOUND_PATHS: SOUND_PATHS,
    normalizeRarity: normalizeRarity,
    rarityColor: rarityColor,
    rarityGlow: rarityGlow,
    reasonText: reasonText,
  };
})(typeof window !== 'undefined' ? window : this);
