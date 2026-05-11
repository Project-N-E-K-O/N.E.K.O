(function () {
  'use strict';

  /**
   * @type {{
   *   bgm: {
   *     startMenu: string[],
   *     inGame: string[],
   *     mood: {
   *       calm: string[],
   *       happy: string[],
   *       angry: {
   *         default: string[],
   *         openingMax: string[],
   *         max: string[],
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
      // 当前先留空，补入素材后即可作为普通游戏中 BGM 使用。
      inGame: [],
      mood: {
        calm: [],
        happy: [],
        angry: {
          default: [],
          openingMax: [
            '/static/game/games/soccer/audio/纯狐_心之所在_plus.mp3',
          ],
          max: [
            '/static/game/games/soccer/audio/纯狐_心之所在.mp3', // Vlizzurd - https://www.youtube.com/watch?v=_aKMzlpGg-E
          ],
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
