(function () {
  'use strict';

  // 游戏音频配置模板。
  // 这个文件只作为开发参考，不需要被页面加载。
  //
  // 推荐规则：
  // - 每个游戏维护自己的配置文件，放在 static/game/games/<gameType>/ 下。
  // - 音频系统只负责播放、停止、音量、缓存、循环段和音效叠加。
  // - 当前场景、心情、难度、比分等判断由具体游戏自己完成。
  // - 游戏可以直接把 { intro, loop, outro } 对象传给 playLoopedBgm()。
  // - 只有想通过字符串路径调用循环 BGM 时，才需要填写 loopedBgm。
  //
  // 循环 BGM 对象格式：
  // - intro 可选，只播放一次。
  // - loop 必填，作为循环段反复播放。
  // - outro 可选，finishLoopedBgm() 收尾时在当前 loop 段结束后播放。
  // - 如果 intro / loop / outro 来自同一首素材的拆分，响度工具会优先把三段拼接成组级结果。
  // - 组级响度校准优先写在同级 gainDb，让 intro / loop / outro 继承同一个增益。
  // - 已经写过 gainDb / volumeMultiplier 的资源会被视为已调整，响度工具只沿用，不给新建议。
  const gameAudioConfigTemplate = {
    // 可选：本游戏混音基准。
    // 不写 audioMix 时等价于 baseVolume=1、maxVolume=1，沿用旧音量行为。
    // 单个音频素材不写 gainDb / volumeMultiplier 时默认不额外增益 / 衰减。
    // 推荐用 scripts/analyze_game_audio_loudness.js 生成响度报告后填写 gainDb。
    // 文本报告面向人工阅读；JSON 报告面向工具 / AI 读取，字段保持稳定。
    // 默认建议增益绝对值 <= 0.3 dB 时按“差异很小，可忽略”处理，不需要写配置。
    // gainDb 是播放时软增益，适合小幅响度校准；极端过小 / 过大的素材应优先处理音频文件本身。
    // volumeMultiplier 只用于主观设计调整，不建议作为批量响度校准字段。
    // 可让 AI 读取工具报告后，协助判断哪些素材适合写 gainDb，哪些素材需要人工确认或重新处理。
    // AI 使用建议：
    // 1. 先运行响度分析工具，不直接自动改音频文件。
    // 2. 已有 gainDb / volumeMultiplier 的条目沿用已有配置，不覆盖。
    // 3. 循环 BGM 优先看组级建议，把统一 gainDb 写在 intro / loop / outro 同级。
    // 4. <= 0.3 dB 的极小差异默认忽略。
    // 5. 超过忽略阈值的小幅差异可写 gainDb。
    // 6. 可能撞上 Audio.volume=1 上限或增益过大的素材，需要人工确认，优先处理音频文件本身。
    // 7. 修改配置后再次运行工具，并结合浏览器听感验证。
    // 示例：
    // node scripts/analyze_game_audio_loudness.js --config static/game/games/soccer/soccer-audio-config.js
    // node scripts/analyze_game_audio_loudness.js --config static/game/games/soccer/soccer-audio-config.js --format json
    // node scripts/analyze_game_audio_loudness.js --config static/game/games/soccer/soccer-audio-config.js --ignore-gain-db 0.3
    audioMix: {
      bgm: {
        // 音量条 100% 时，本游戏 BGM 默认输出到 70%。
        baseVolume: 0.7,
        // 最终输出上限；浏览器 Audio.volume 硬上限仍是 1。
        maxVolume: 1,
      },
      sfx: {
        baseVolume: 0.85,
        maxVolume: 1,
      },
    },

    bgm: {
      // 普通 BGM 歌单，适合菜单、结算、一次性胜利音乐等。
      // 字符串写法表示该素材不额外增益 / 衰减。
      menu: ['/static/game/games/example/audio/menu.mp3'],

      // 游戏中 BGM 可以由游戏自己决定结构。
      // 示例：打开页面时随机选择一套循环 BGM。
      inGame: {
        variants: [
          {
            id: 'normal-a',
            intro: '/static/game/games/example/audio/normal-a-start.mp3',
            loop: {
              src: '/static/game/games/example/audio/normal-a-loop.mp3',
              // 可选：素材响度校准，单位 dB。推荐来自 LUFS 扫描报告。
              gainDb: -2.5,
              // 可选：设计倍率。通常不要用它做响度校准；不写就是 1。
              volumeMultiplier: 1.15,
            },
            outro: '/static/game/games/example/audio/normal-a-end.mp3',
          },
        ],
      },

      // 心情、难度、局势等都只是游戏自己的资源分类。
      // 音频系统不会理解这些字段，也不会自动判断何时播放。
      mood: {},
      difficulty: {},
      result: {},
    },

    // 可选：命名循环 BGM 注册表。
    // 只有调用方希望这样写时才需要：
    //   audio.playLoopedBgm('battle.normal')
    // 如果调用方已经拿到了具体对象，也可以直接：
    //   audio.playLoopedBgm(gameAudioConfig.bgm.inGame.variants[0])
    loopedBgm: {
      battle: {
        normal: {
          intro: '/static/game/games/example/audio/battle-start.mp3',
          loop: '/static/game/games/example/audio/battle-loop.mp3',
          outro: '/static/game/games/example/audio/battle-end.mp3',
        },
      },
    },

    // 音效资源表。音效允许叠加播放，适合踢球、碰撞、按钮等短音。
    sfx: {
      ball: {
        kick: ['/static/game/games/example/audio/kick.mp3'],
      },
    },
  };
  void gameAudioConfigTemplate;
})();
