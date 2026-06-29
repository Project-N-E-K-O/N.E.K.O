(function () {
  'use strict';

  var racketShuttle = '/static/game/games/badminton/audio/badminton-racket-shuttlecock-0537.mp3';
  var racketShuttleHits = [
    '/static/game/games/badminton/audio/badminton-racket-shuttlecock-hit-1.mp3',
    '/static/game/games/badminton/audio/badminton-racket-shuttlecock-hit-2.mp3',
    '/static/game/games/badminton/audio/badminton-racket-shuttlecock-hit-3.mp3',
    '/static/game/games/badminton/audio/badminton-racket-shuttlecock-hit-4.mp3',
  ];
  var racketShuttleSingle = '/static/game/games/badminton/audio/badminton-racket-shuttlecock-single.mp3';
  var racketSwing = '/static/game/games/badminton/audio/zapsplat_sport_badminton_racket_fast_swing_whoosh_001_76396.mp3';
  var bananaSlipGoofy = '/static/game/games/badminton/audio/badminton-banana-slip-goofy.mp3';
  var octopusInkPoof = '/static/game/games/badminton/audio/badminton-octopus-ink-poof.mp3';

  var badmintonGameAudioConfig = {
    audioMix: {
      bgm: { baseVolume: 0.7, maxVolume: 1 },
      sfx: { baseVolume: 0.85, maxVolume: 1 },
    },
    bgm: {
      startMenu: ['/static/game/games/soccer/audio/Prelude.mp3'],
      inGame: {
        variants: [
          {
            id: 'badminton-rally-theme',
            intro: '/static/game/games/soccer/audio/Battle_Theme_1_S.mp3',
            loop: '/static/game/games/soccer/audio/Battle_Theme_1_L.mp3',
            outro: '/static/game/games/soccer/audio/Battle_Theme_1_E.mp3',
          },
          {
            id: 'badminton-rally',
            gainDb: 1.95,
            intro: '/static/game/games/soccer/audio/Battle_1_S.mp3',
            loop: '/static/game/games/soccer/audio/Battle_1_L.mp3',
            outro: '/static/game/games/soccer/audio/Battle_1_E.mp3',
          },
        ],
      },
      mood: {
        calm: [],
        happy: {
          intro: '/static/game/games/soccer/audio/Chocobos_S.mp3',
          loop: '/static/game/games/soccer/audio/Chocobos_L.mp3',
        },
        angry: {
          loop: '/static/game/games/soccer/audio/纯狐_心之所在_L.mp3',
          outro: '/static/game/games/soccer/audio/纯狐_心之所在_E.mp3',
        },
        relaxed: {
          intro: '/static/game/games/soccer/audio/Chocobos_S.mp3',
          loop: '/static/game/games/soccer/audio/Chocobos_L.mp3',
        },
        sad: [],
        surprised: [],
      },
      // Battle_1_E.mp3 只是临时结算占位。它更像胜利结算，不一定适合作为所有 gameOver。
      // 后续如果要区分玩家胜利 / Yui 胜利 / 非 duel 分数结算，需要再补素材和规则。
      result: { gameOver: [{ src: '/static/game/games/soccer/audio/Battle_1_E.mp3', gainDb: 1.5 }] },
    },
    loopedBgm: {},
    sfx: {
      shot: {
        line_in: [{ src: racketShuttle, gainDb: -3 }],
        net_touch: [{ src: racketShuttle, gainDb: -5 }],
        zone_in: [{ src: racketShuttle, gainDb: -4 }],
        net: [{ src: racketShuttle, gainDb: -8 }],
        out: [{ src: racketShuttle, gainDb: -5 }],
        whoosh: [{ src: racketSwing, gainDb: -5 }],
      },
      shuttleContact: racketShuttleHits.concat([racketShuttleSingle]).map(function (src) { return { src: src, gainDb: -2 }; }),
      net: [{ src: racketShuttle, gainDb: -7 }],
      yuiCheat: {
        bananaSlip: [{ src: bananaSlipGoofy, gainDb: -1 }],
        octopusInk: [{ src: octopusInkPoof, gainDb: -2 }],
      },
      streak: [{ src: '/static/game/games/soccer/audio/Chocobos_S.mp3', gainDb: -6 }],
      record: [{ src: '/static/game/games/soccer/audio/Battle_1_E.mp3', gainDb: -2 }],
    },
  };

  var gameSystem = window.NekoGameSystem || (window.NekoGameSystem = {});
  gameSystem.badminton = gameSystem.badminton || {};
  gameSystem.badminton.audioConfig = badmintonGameAudioConfig;
})();
