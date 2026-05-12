(function () {
  'use strict';

  /**
   * @type {{
   *   bgm: {
   *     startMenu: string[],
   *     inGame: {
   *       intro?: string,
   *       loop: string,
   *       outro?: string,
   *     },
   *     mood: {
   *       calm: string[],
   *       happy: string[],
   *       angry: {
   *         default: string[],
   *         openingMax: {
   *           loop: string,
   *           outro?: string,
   *         },
   *         max: {
   *           loop: string,
   *           outro?: string,
   *         },
   *       },
   *       relaxed: string[],
   *       sad: string[],
   *       surprised: string[],
   *     },
   *   },
   *   loopedBgm: Record<string, {
   *     intro?: string,
   *     loop: string,
   *     outro?: string,
   *   }>,
   *   sfx: {
   *     ball: {
   *       kick: string[],
   *     },
   *     goal: string[],
   *   },
   * }}
   */
  const soccerGameAudioConfig = {
    bgm: {
      startMenu: ['/static/game/games/soccer/audio/Prelude.mp3'],
      // 正常比赛 BGM 入口：离开 max + angry 特例后会回到这里。
      // S 是进入段，L 是循环段，E 是收尾段。
      inGame: {
        // FINAL FANTASY II - Battle Theme 1 
        intro: '/static/game/games/soccer/audio/Battle_Theme_1_S.mp3',
        loop: '/static/game/games/soccer/audio/Battle_Theme_1_L.mp3',
        outro: '/static/game/games/soccer/audio/Battle_Theme_1_E.mp3',
      },
      mood: {
        calm: [],
        happy: [],
        angry: {
          default: [],
          // 开场即 max + angry 时使用循环 BGM：
          // - loop 作为比赛中循环段持续播放。
          // - outro 在 finishLoopedBgm() 收尾时播放。
          openingMax: {
            // 东方绀珠传　～ Legacy of Lunatic Kingdom. - Pure Furies　～ 心之所在
            loop: '/static/game/games/soccer/audio/纯狐_心之所在_plus_L.mp3',
            outro: '/static/game/games/soccer/audio/纯狐_心之所在_plus_E.mp3',
          },
          // 非开场后续进入 max + angry 时使用另一套循环 BGM。
          max: {
            // Vlizzurd - https://www.youtube.com/watch?v=_aKMzlpGg-E
            loop: '/static/game/games/soccer/audio/纯狐_心之所在_L.mp3', 
            outro: '/static/game/games/soccer/audio/纯狐_心之所在_E.mp3',
          },
        },
        relaxed: [],
        sad: [],
        surprised: [],
      },
    },
    // 循环 BGM 入口：
    // - intro 可选，只播放一次。
    // - loop 必填，作为循环段反复播放。
    // - outro 可选，finishLoopedBgm() 收尾时在当前段结束后播放。
    loopedBgm: {},
    sfx: {
      ball: {
        kick: ['/static/game/games/soccer/audio/hitboll.mp3'],
      },
      goal: [],
    },
  };

  const gameSystem = window.NekoGameSystem || (window.NekoGameSystem = {});
  gameSystem.soccer = gameSystem.soccer || {};
  gameSystem.soccer.audioConfig = soccerGameAudioConfig;
})();
