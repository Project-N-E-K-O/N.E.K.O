(function () {
  'use strict';

  /**
   * @type {{
   *   bgm: {
   *     startMenu: string[],
   *     inGame: {
   *       variants: Array<{
   *         id: string,
   *         intro?: string,
   *         loop: string,
   *         outro?: string,
   *       }>,
   *     },
   *     difficulty: {
   *       lv4NonAngry: {
   *         intro?: string,
   *         loop: string,
   *         outro?: string,
   *       },
   *     },
   *     result: {
   *       playerWin: string[],
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
      // 每次打开页面时从 variants 中随机选一套作为本次页面生命周期的正常比赛 BGM。
      // 预加载只会加载被选中的那套，避免同时加载未使用的对应 BGM。
      inGame: {
        variants: [
          {
            id: 'battle-theme-1',
            // FINAL FANTASY II - Battle Theme 1
            intro: '/static/game/games/soccer/audio/Battle_Theme_1_S.mp3',
            loop: '/static/game/games/soccer/audio/Battle_Theme_1_L.mp3',
            outro: '/static/game/games/soccer/audio/Battle_Theme_1_E.mp3',
          },
          {
            id: 'battle-1',
            // FINAL FANTASY III - Battle 1 ~ Fanfare
            intro: '/static/game/games/soccer/audio/Battle_1_S.mp3',
            loop: '/static/game/games/soccer/audio/Battle_1_L.mp3',
          },
        ],
      },
      difficulty: {
        // 最低难度 lv4 且非 angry / sad 时切到轻松 BGM。
        // FINAL FANTASY III - Chocobos!
        lv4NonAngry: {
          intro: '/static/game/games/soccer/audio/Chocobos_S.mp3',
          loop: '/static/game/games/soccer/audio/Chocobos_L.mp3',
        },
      },
      result: {
        // 结束游戏时，如果玩家比分高于猫娘，播放一次，不循环。
        playerWin: ['/static/game/games/soccer/audio/Battle_1_E.mp3'],
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
