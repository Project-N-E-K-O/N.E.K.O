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
   *       angry: string[],
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
   *   useMoodBgm: boolean,
   * }}
   */
  const soccerGameAudioConfig = {
    bgm: {
      startMenu: [],
      inGame: [],
      mood: {
        calm: [],
        happy: [],
        angry: [],
        relaxed: [],
        sad: [],
        surprised: [],
      },
    },
    sfx: {
      ball: {
        kick: [],
      },
      goal: [],
    },
    useMoodBgm: false,
  };

  const gameSystem = window.NekoGameSystem || (window.NekoGameSystem = {});
  gameSystem.soccer = gameSystem.soccer || {};
  gameSystem.soccer.audioConfig = soccerGameAudioConfig;
})();
