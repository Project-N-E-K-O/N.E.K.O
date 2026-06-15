(function () {
  'use strict';

  var basketballGameAudioConfig = {
    audioMix: {
      bgm: { baseVolume: 0.7, maxVolume: 1 },
      sfx: { baseVolume: 0.85, maxVolume: 1 },
    },
    bgm: {
      startMenu: ['/static/game/games/soccer/audio/Prelude.mp3'],
      inGame: {
        variants: [
          {
            id: 'basketball-battle-theme',
            intro: '/static/game/games/soccer/audio/Battle_Theme_1_S.mp3',
            loop: '/static/game/games/soccer/audio/Battle_Theme_1_L.mp3',
            outro: '/static/game/games/soccer/audio/Battle_Theme_1_E.mp3',
          },
          {
            id: 'basketball-battle',
            gainDb: 1.95,
            intro: '/static/game/games/soccer/audio/Battle_1_S.mp3',
            loop: '/static/game/games/soccer/audio/Battle_1_L.mp3',
            outro: '/static/game/games/soccer/audio/Battle_1_E.mp3',
          },
        ],
      },
      mood: {
        calm: ['/static/game/games/soccer/audio/Prelude.mp3'],
        happy: [{ src: '/static/game/games/soccer/audio/Chocobos_S.mp3', gainDb: 0.59 }],
        angry: [{ src: '/static/game/games/soccer/audio/纯狐_心之所在_plus_L.mp3', gainDb: -2.94 }],
        relaxed: ['/static/game/games/soccer/audio/Chocobos_L.mp3'],
        sad: ['/static/game/games/soccer/audio/Prelude.mp3'],
        surprised: [{ src: '/static/game/games/soccer/audio/Battle_1_E.mp3', gainDb: 1.5 }],
      },
      result: { gameOver: [{ src: '/static/game/games/soccer/audio/Battle_1_E.mp3', gainDb: 1.5 }] },
    },
    loopedBgm: {},
    sfx: {
      shot: {
        swish: [{ src: '/static/game/games/soccer/audio/hitboll.mp3', gainDb: -4 }],
        bank: [{ src: '/static/game/games/soccer/audio/hitboll.mp3', gainDb: -2 }],
        rimIn: [{ src: '/static/game/games/soccer/audio/hitboll.mp3', gainDb: -1 }],
        rimOut: [{ src: '/static/game/games/soccer/audio/hitboll.mp3', gainDb: 0 }],
        airBall: [{ src: '/static/game/games/soccer/audio/hitboll.mp3', gainDb: -7 }],
        whoosh: [{ src: '/static/game/games/soccer/audio/hitboll.mp3', gainDb: -8 }],
      },
      rim: [{ src: '/static/game/games/soccer/audio/hitboll.mp3', gainDb: -1 }],
      streak: [{ src: '/static/game/games/soccer/audio/Chocobos_S.mp3', gainDb: -6 }],
      record: [{ src: '/static/game/games/soccer/audio/Battle_1_E.mp3', gainDb: -2 }],
    },
  };

  var gameSystem = window.NekoGameSystem || (window.NekoGameSystem = {});
  gameSystem.basketball = gameSystem.basketball || {};
  gameSystem.basketball.audioConfig = basketballGameAudioConfig;
})();
