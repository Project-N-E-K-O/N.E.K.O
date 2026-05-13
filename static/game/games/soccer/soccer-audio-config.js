(function () {
  'use strict';

  /**
   * @type {{
   *   audioMix: {
   *     bgm: {
   *       baseVolume: number,
   *       maxVolume: number,
   *     },
   *     sfx: {
   *       baseVolume: number,
   *       maxVolume: number,
   *     },
   *   },
   *   bgm: {
   *     startMenu: string[],
   *     inGame: {
   *       variants: Array<{
   *         id: string,
   *         gainDb?: number,
   *         intro?: string,
   *         loop: string,
   *         outro?: string,
   *       }>,
   *     },
   *     difficulty: {
   *       lv4NonAngry: {
   *         gainDb?: number,
   *         intro?: string,
   *         loop: string,
   *         outro?: string,
   *       },
   *     },
   *     result: {
   *       playerWin: Array<string|{src: string, gainDb?: number}>,
   *     },
   *     mood: {
   *       calm: string[],
   *       happy: string[],
   *       angry: {
   *         default: string[],
   *         openingMax: {
   *           gainDb?: number,
   *           loop: string,
   *           outro?: string,
   *         },
   *         max: {
   *           gainDb?: number,
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
    // 本游戏混音基准：
    // - 音量条仍是玩家看到的 0-100%。
    // - baseVolume 表示音量条 100% 时，本游戏默认输出到多大。
    // - 推荐先用 scripts/analyze_game_audio_loudness.js 扫描素材，再按报告填写 gainDb。
    // - gainDb 是播放时软增益，适合小幅响度校准；极端过小 / 过大的素材应优先处理音频文件本身。
    // - volumeMultiplier 只用于主观设计调整，不建议作为批量响度校准字段。
    // - 可让 AI 读取工具报告后，协助判断哪些素材适合写 gainDb，哪些素材需要人工确认或重新处理。
    // - 单个音频素材不写 gainDb / volumeMultiplier 时默认不额外增益 / 衰减。
    // - maxVolume 是最终上限，不能超过浏览器 Audio.volume 的硬上限 1。
    // 示例：
    // node scripts/analyze_game_audio_loudness.js --config static/game/games/soccer/soccer-audio-config.js
    audioMix: {
      bgm: { baseVolume: 0.7, maxVolume: 1 },
      sfx: { baseVolume: 0.85, maxVolume: 1 },
    },
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
            gainDb: 1.95,
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
        playerWin: [{ src: '/static/game/games/soccer/audio/Battle_1_E.mp3', gainDb: 1.5 }],
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
            gainDb: -2.94,
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
