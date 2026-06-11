(function () {
  'use strict';

  // 足球游戏临时根挂载点：
  // - 这里只负责创建当前足球页需要的游戏系统命名空间。
  // - 不在这里实现音频系统，不在这里写具体播放逻辑。
  // - 等足球 HTML 拆分完成后，这个临时文件应由正式的足球游戏入口文件替代。
  // - 当前目标是避免把 GameAudioSystem、SoccerGameAudioConfig、SoccerGameAudio
  //   这类对象散落挂到 window 最外层。
  const gameSystem = window.NekoGameSystem || (window.NekoGameSystem = {});
  gameSystem.soccer = gameSystem.soccer || {};
})();
