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
  const gameAudioConfigTemplate = {
    bgm: {
      // 普通 BGM 歌单，适合菜单、结算、一次性胜利音乐等。
      menu: [],

      // 游戏中 BGM 可以由游戏自己决定结构。
      // 示例：打开页面时随机选择一套循环 BGM。
      inGame: {
        variants: [
          {
            id: 'normal-a',
            intro: '/static/game/games/example/audio/normal-a-start.mp3',
            loop: '/static/game/games/example/audio/normal-a-loop.mp3',
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
