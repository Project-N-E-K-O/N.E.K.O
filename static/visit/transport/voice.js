/*
 * 串门传输页：猫娘语音的本页音频轨（设计稿 OD-15 v4）。
 *
 * 发送：父页把自家猫娘 TTS 播放链的旁路（MediaStream，见 app-audio-playback.js
 * openAssistantVoiceTap）交进来；两家 SDK 都按本页 realm 的 MediaStreamTrack 校验
 * （TRTC 的 audioTrack 参数是 instanceOf 校验），父页 realm 的轨直接交给 SDK 会被拒，
 * 这里用本页自己的 AudioContext 转一手（MediaStreamSource → MediaStreamDestination）。
 * 只转父页给的 TTS 旁路，不采麦克风。
 *
 * 接收：对端音频轨挂在一个静音的 <audio> 上（Chromium 里远端 WebRTC 音频不挂媒体元素时
 * 接进 Web Audio 是静音的），真正出声由父页接进自家扬声器音量链（音量 / 输出设备与 TTS 一致）。
 */
(function (root) {
  'use strict';

  var NVT = root.NekoVisitTransport = root.NekoVisitTransport || {};

  function VoiceBridge(win) {
    this.win = win;
    this.ctx = null;
    this.input = null;     // 父页交来的 TTS 旁路 MediaStream
    this.source = null;    // input 在本页 AudioContext 里的源节点
    this.dest = null;
    this.remoteEl = null;
    this.remoteTrack = null;
  }

  var V = VoiceBridge.prototype;

  /** 换 TTS 旁路输入（null = 父页收回）；已建好的输出轨保留，下一路输入接到同一条轨上。 */
  V.setInput = function (stream) {
    if (stream === this.input) return;
    this.input = stream || null;
    if (this.source) {
      try {
        this.source.disconnect();
      } catch (_) {
        // 已断开
      }
    }
    this.source = null;
    if (this.input && this.ctx && this.dest) this.connectSource();
  };

  V.hasInput = function () {
    return !!this.input;
  };

  V.connectSource = function () {
    try {
      this.source = this.ctx.createMediaStreamSource(this.input);
      this.source.connect(this.dest);
    } catch (_) {
      this.source = null;
    }
  };

  /**
   * 本页 realm 的发送轨；还没有输入时给 null。轨被 SDK 停掉（TRTC stopLocalAudio 会 stop 掉它）就重建一条，
   * 与画布轨的 ensureTrack 同一约定。
   */
  V.ensureTrack = function () {
    if (!this.input) return null;
    var Ctx = this.win.AudioContext || this.win.webkitAudioContext;
    if (!Ctx) return null;
    if (!this.ctx) {
      try {
        this.ctx = new Ctx();
      } catch (_) {
        return null;
      }
    }
    if (this.ctx.state === 'suspended' && typeof this.ctx.resume === 'function') {
      this.ctx.resume().then(null, function () {});
    }
    var track = this.dest && this.dest.stream.getAudioTracks()[0];
    if (!track || track.readyState === 'ended') {
      if (this.source) {
        try {
          this.source.disconnect();
        } catch (_) {
          // 已断开
        }
        this.source = null;
      }
      this.dest = this.ctx.createMediaStreamDestination();
      this.dest.channelCount = 1;
      track = this.dest.stream.getAudioTracks()[0];
    }
    if (!this.source) this.connectSource();
    return track || null;
  };

  /** 对端音频轨（null = 不再有）：挂到静音 <audio> 上保证有数据流，父页按 remoteTrack() 取走接进扬声器。 */
  V.setRemote = function (track, doc) {
    this.remoteTrack = track || null;
    if (!this.remoteTrack) {
      if (this.remoteEl) {
        try {
          this.remoteEl.pause();
        } catch (_) {
          // 已卸载
        }
        this.remoteEl.srcObject = null;
      }
      return;
    }
    if (!this.remoteEl && doc && typeof doc.createElement === 'function') {
      var el = doc.createElement('audio');
      el.muted = true;
      el.autoplay = true;
      el.setAttribute('aria-hidden', 'true');
      if (doc.body) doc.body.appendChild(el);
      this.remoteEl = el;
    }
    if (!this.remoteEl) return;
    var MS = this.win.MediaStream;
    this.remoteEl.srcObject = MS ? new MS([this.remoteTrack]) : null;
    var playing = this.remoteEl.play && this.remoteEl.play();
    if (playing && typeof playing.catch === 'function') playing.catch(function () {});
  };

  /** 拆页面：停输出轨、关本页 AudioContext、摘掉对端音频。父页的旁路由父页自己收。 */
  V.close = function (doc) {
    this.setRemote(null, doc);
    this.setInput(null);
    var track = this.dest && this.dest.stream.getAudioTracks()[0];
    if (track) {
      try {
        track.stop();
      } catch (_) {
        // 已结束
      }
    }
    this.dest = null;
    if (this.ctx && typeof this.ctx.close === 'function') this.ctx.close().then(null, function () {});
    this.ctx = null;
  };

  NVT.voice = { VoiceBridge: VoiceBridge };
})(typeof window !== 'undefined' ? window : globalThis);
