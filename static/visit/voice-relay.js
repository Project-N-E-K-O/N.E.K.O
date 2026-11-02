/*
 * 串门父页：猫娘语音中继（设计稿 OD-15 v4）。只在 index.html（Pet 窗 / 网页单窗口）加载，
 * 由 parent-bridge.js 按传输页的消息驱动；TTS 只在这个页面播放（多窗口时 chat 窗收不到音频），
 * 所以开发单窗口与 Electron 分发多窗口走的是同一条路径。
 *
 * - 发送：传输页报 voice{on:true} → 从自家 TTS 播放链旁路出一路单声道 MediaStream
 *   （appAudioPlayback.openAssistantVoiceTap），同步交给 iframe 的 __nekoVisitFrameSink.setLocalVoice；
 *   voice{on:false} / 换 iframe / 场次结束就收回。只旁路 TTS，不碰麦克风。
 * - 接收：传输页报 remote_audio{on:true} → 取 __nekoVisitFrameSink.remoteAudioTrack()，经
 *   appAudioPlayback.playExternalVoiceTrack 接进自家扬声器增益（音量 / 静音 / 输出设备与 TTS 一致，
 *   不经口型分析器、不会回流进旁路）；on:false / 换 iframe / 场次结束就断开。
 * 开 / 接都是异步的（可能要先建 AudioContext）：每次变化加代号，迟到的结果按代号丢掉并就地释放。
 */
(function (root) {
  'use strict';

  function createVoiceRelay(env) {
    var win = env.window;
    var audio = function () { return env.audio || win.appAudioPlayback || null; };
    var log = env.log || function () {};
    var local = { gen: 0, handle: null, sink: null };
    var remote = { gen: 0, handle: null, track: null };

    function closeLocal() {
      local.gen += 1;
      var h = local.handle;
      var sink = local.sink;
      local.handle = null;
      local.sink = null;
      if (sink && typeof sink.setLocalVoice === 'function') {
        try {
          sink.setLocalVoice(null);
        } catch (_) {
          // iframe 正在卸载
        }
      }
      if (h) h.close();
    }

    /** 传输页要（on）/ 不要自家语音旁路。sink = 该 iframe 的 __nekoVisitFrameSink。 */
    function setLocalWanted(on, sink) {
      if (!on || !sink) {
        closeLocal();
        return Promise.resolve(false);
      }
      if (local.sink === sink && local.handle) return Promise.resolve(true);
      closeLocal();
      var gen = local.gen;
      local.sink = sink;
      var a = audio();
      if (!a || typeof a.openAssistantVoiceTap !== 'function') return Promise.resolve(false);
      return Promise.resolve().then(function () { return a.openAssistantVoiceTap(); }).then(function (h) {
        if (!h) return false;
        if (gen !== local.gen) {
          h.close();
          return false;
        }
        local.handle = h;
        sink.setLocalVoice(h.stream);
        return true;
      }).catch(function (err) {
        log('warn', 'visit voice tap failed: ' + (err && err.name));
        return false;
      });
    }

    function stopRemote() {
      remote.gen += 1;
      var h = remote.handle;
      remote.handle = null;
      remote.track = null;
      if (h) h.stop();
    }

    /** 对端语音轨（null = 停播）。同一条轨重复交来不重接。 */
    function setRemoteTrack(track) {
      if (!track) {
        stopRemote();
        return Promise.resolve(false);
      }
      if (track === remote.track && remote.handle) return Promise.resolve(true);
      stopRemote();
      var gen = remote.gen;
      remote.track = track;
      var a = audio();
      if (!a || typeof a.playExternalVoiceTrack !== 'function') return Promise.resolve(false);
      return Promise.resolve().then(function () { return a.playExternalVoiceTrack(track); }).then(function (h) {
        if (!h) return false;
        if (gen !== remote.gen) {
          h.stop();
          return false;
        }
        remote.handle = h;
        return true;
      }).catch(function (err) {
        log('warn', 'visit peer voice playback failed: ' + (err && err.name));
        return false;
      });
    }

    return {
      setLocalWanted: setLocalWanted,
      setRemoteTrack: setRemoteTrack,
      /** 换 iframe / 场次结束：两个方向都收掉。 */
      reset: function () {
        closeLocal();
        stopRemote();
      },
      _local: local,
      _remote: remote,
    };
  }

  root.NekoVisitVoiceRelay = { createVoiceRelay: createVoiceRelay };
})(typeof window !== 'undefined' ? window : globalThis);
