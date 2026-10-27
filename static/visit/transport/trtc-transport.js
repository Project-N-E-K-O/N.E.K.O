/*
 * 串门传输页：TRTC 适配器（trtc-sdk-v5 5.20.1，设计稿 §3.5.1 / §4.3）。
 *
 * 视频只发自定义画布轨，远端视频 startRemoteVideo({view:null}) 只取轨不渲染。数据通道用
 * sendCustomMessage（cmdId 1/2/3，全房广播，靠信封 r 与发送者绑定过滤）。
 *
 * 猫娘语音（OD-15 v4）：只发父页交来的 TTS 旁路轨 startLocalAudio({option:{audioTrack}})，全目录
 * 不调用麦克风采集；单声道 48 kHz，码率上限 audio_kbps（32）。入房仍 autoReceiveAudio:false，
 * 收到 audio_subscribe 才 muteRemoteAudio(peer, false) 订阅；SDK 自己的播放音量压到 0，
 * 轨交给控制器、由父页接进自家扬声器音量链出声（不双响）。
 *
 * 5.20.1 没有 updatePrivateMapKey：续期凭证只替换本地保存的那份，手动重新入房时用最新的。
 */
(function (root) {
  'use strict';

  var NVT = root.NekoVisitTransport = root.NekoVisitTransport || {};
  var REMOTE_RETRY_BASE_MS = 2000;    // startRemoteVideo 失败后的首次重试间隔，之后翻倍
  var REMOTE_RETRY_MAX_MS = 30000;    // 重试间隔上限（一直重试到取消订阅 / 对端视频不可用 / 离房）
  var SUPERSEDED_EXIT_MS = 15000;     // 拆掉后兜底退出旧房间的延迟（留给接替的页面入房顶号）
  var CLEANUP_BOUND_MS = 8000;        // 兜底退房最多等这么久（父页按 2 + 15 + 8 s 再留余量保留 iframe）

  /** 等退房 / 断开做完，但最多 CLEANUP_BOUND_MS：网络坏着时它可能一直不落定（失败也算完）。 */
  function boundCleanup(p) {
    return Promise.race([
      Promise.resolve(p).then(null, function () {}),
      new Promise(function (resolve) { setTimeout(resolve, CLEANUP_BOUND_MS); }),
    ]).then(function () {});
  }

  function createTrtcAdapter(opts) {
    var TRTC = opts.sdk;
    var handlers = opts.handlers;
    var trtc = null;
    var creds = null;
    var joined = false;
    var leaving = false;
    var departing = null;   // leave 发出的 exitRoom：destroy 时还没落定就等它做完（有上限）
    var publishing = false;
    var publishedTrack = null;
    // 发布 / 撤销按顺序执行：有发布在途时，停止排在它之后、新的发布再排在停止之后，
    // 不会出现「旧发布迟到完成后把新发布撤掉」；没有发布在途时停止同步执行（传输 WS 断开时
    // 要在离房之前就停掉）。roomGen 只在离房时递增：离房后迟到完成的发布不记状态
    var roomGen = 0;
    var opChain = Promise.resolve();
    var publishesInFlight = 0;
    var profilesInFlight = 0;   // 排队 / 进行中的 updateLocalVideo：它默认 publish:true，撤销要排在它后面
    function serial(fn) {
      var p = opChain.then(fn);
      opChain = p.then(null, function () {});
      return p;
    }
    function afterChain(p) {
      opChain = opChain.then(function () { return p; }).then(null, function () {});
      return p;
    }
    // 语音：与视频同一条队列、同一个 roomGen（发布中的轨 / 远端订阅的状态机与视频对偶）
    var audioPublishing = false;
    var publishedAudio = null;
    var audioSubscribeWanted = false;
    var remoteAudioUser = null;
    var remoteAudioStarted = false;
    var remoteAudioAttempt = 0;
    var remoteAudioRetries = 0;
    var remoteAudioRetryTimer = null;
    var subscribeWanted = false;
    var peerVid = opts.peerVid || null;
    var remoteVideoUser = null;
    var remoteStarted = false;
    var remoteAttempt = 0;   // 订阅尝试编号：旧尝试迟到的成功 / 失败不能改新尝试的状态
    var remoteRetries = 0;
    var remoteRetryTimer = null;
    var entering = null;
    var lastStats = null;
    var lastNetwork = null;

    function vendorOf(c) {
      return (c && c.vendor && c.vendor.trtc) || {};
    }

    function isPeer(userId) {
      // host 在对端 hello 核验前不知道 peer_vid：房里另一个人就是候选对端（Servers 保证每房 2 人）
      return !peerVid || userId === peerVid;
    }

    function bind() {
      var E = TRTC.EVENT;
      trtc.on(E.REMOTE_USER_ENTER, function (ev) {
        if (leaving) return;   // 离房期间排队到的进房事件不算（重进后以新房间里的为准）
        if (isPeer(ev.userId)) handlers.onPeer('enter', ev.userId, null);
      });
      trtc.on(E.REMOTE_USER_EXIT, function (ev) {
        if (!isPeer(ev.userId)) return;
        // reason 0 = 对端主动离开（暂定离开，后端起 35 s 重入宽限）；1 超时交给心跳，不上报
        var explicit = ev.reason === 0 || ev.reason === undefined;
        handlers.onPeer('exit', ev.userId, explicit ? '0' : String(ev.reason), explicit);
        if (ev.userId === remoteVideoUser) {
          remoteVideoUser = null;
          remoteStarted = false;
          remoteAttempt += 1;
          handlers.onRemoteTrack(null);
        }
        if (ev.userId === remoteAudioUser) dropRemoteAudio();
      });
      trtc.on(E.REMOTE_AUDIO_AVAILABLE, function (ev) {
        if (!isPeer(ev.userId)) return;
        remoteAudioUser = ev.userId;
        remoteAudioRetries = 0;
        maybeStartRemoteAudio();
      });
      trtc.on(E.REMOTE_AUDIO_UNAVAILABLE, function (ev) {
        if (ev.userId === remoteAudioUser) dropRemoteAudio();
      });
      trtc.on(E.REMOTE_VIDEO_AVAILABLE, function (ev) {
        if (!isPeer(ev.userId) || ev.streamType !== TRTC.TYPE.STREAM_TYPE_MAIN) return;
        remoteVideoUser = ev.userId;
        remoteRetries = 0;
        maybeStartRemote();
      });
      trtc.on(E.REMOTE_VIDEO_UNAVAILABLE, function (ev) {
        if (ev.userId !== remoteVideoUser) return;
        remoteVideoUser = null;
        remoteStarted = false;
        remoteAttempt += 1;
        clearRemoteRetry();
        handlers.onRemoteTrack(null);
      });
      trtc.on(E.TRACK, function (ev) {
        if (ev && ev.track && ev.track.kind === 'video' && ev.userId && ev.userId === remoteVideoUser && subscribeWanted) {
          handlers.onRemoteTrack(ev.track);
        }
        if (ev && ev.track && ev.track.kind === 'audio' && ev.userId && ev.userId === remoteAudioUser && remoteAudioStarted && audioSubscribeWanted) {
          handlers.onRemoteAudio(ev.track);
        }
      });
      trtc.on(E.CUSTOM_MESSAGE, function (ev) {
        handlers.onPiece(ev.userId, ev.cmdId, new Uint8Array(ev.data));
      });
      trtc.on(E.CONNECTION_STATE_CHANGED, function (ev) {
        if (leaving) return;
        if (ev.state === 'RECONNECTING') {
          handlers.onState('reconnecting', {});
        } else if (ev.state === 'CONNECTED' && ev.prevState !== 'CONNECTING') {
          handlers.onState('connected', {});
        } else if (ev.state === 'DISCONNECTED' && joined) {
          // SDK 放弃自动重连（常见于旧凭证鉴权失败）：用最新凭证手动重新入房
          joined = false;
          handlers.onState('reconnecting', {});
          handlers.onNeedRejoin();
        }
      });
      trtc.on(E.KICKED_OUT, function (ev) {
        joined = false;
        handlers.onState('kicked', { vendor_reason: String(ev && ev.reason) });
      });
      trtc.on(E.NETWORK_QUALITY, function (ev) {
        lastNetwork = ev;
        handlers.onNetwork({ lossPct: ev.uplinkLoss, rttMs: ev.uplinkRTT });
      });
      trtc.on(E.STATISTICS, function (s) {
        lastStats = s;
      });
      trtc.on(E.ERROR, function (err) {
        handlers.onLog('warn', 'trtc error', String(err && err.code));
      });
    }

    // 订阅 / 取消订阅与发布共用一条队列：离房的「完成」也等它们，迟到的旧 stopRemoteVideo
    // 不会落在重新入房后的新订阅上
    function clearRemoteRetry() {
      if (remoteRetryTimer !== null) clearTimeout(remoteRetryTimer);
      remoteRetryTimer = null;
    }

    function maybeStartRemote() {
      if (!subscribeWanted || !remoteVideoUser || remoteStarted || !trtc) return;
      clearRemoteRetry();
      remoteStarted = true;
      var attempt = ++remoteAttempt;
      var user = remoteVideoUser;
      var gen = roomGen;
      serial(function () {
        if (gen !== roomGen || !trtc) return undefined;
        return trtc.startRemoteVideo({ userId: user, streamType: TRTC.TYPE.STREAM_TYPE_MAIN, view: null })
          .then(function () {
            if (gen !== roomGen || attempt !== remoteAttempt) return;
            remoteRetries = 0;
            var track = trtc.getVideoTrack({ userId: user, streamType: TRTC.TYPE.STREAM_TYPE_MAIN });
            if (track && subscribeWanted && user === remoteVideoUser) handlers.onRemoteTrack(track);
          }, function (err) {
            handlers.onLog('warn', 'trtc startRemoteVideo failed', String(err && err.code));
            // 已被更新的尝试取代（对端视频先不可用再可用、取消后又订阅）：旧失败不清新尝试的状态、不排重试
            if (attempt !== remoteAttempt) return;
            remoteStarted = false;
            // REMOTE_VIDEO_AVAILABLE 只在可用性变化时来一次：同一订阅仍想要时按退避间隔一直重试
            if (gen !== roomGen) return;
            var delay = Math.min(REMOTE_RETRY_MAX_MS, REMOTE_RETRY_BASE_MS * Math.pow(2, remoteRetries));
            remoteRetries += 1;
            clearRemoteRetry();
            remoteRetryTimer = setTimeout(function () {
              remoteRetryTimer = null;
              if (gen === roomGen && user === remoteVideoUser && attempt === remoteAttempt) maybeStartRemote();
            }, delay);
          });
      });
    }

    // ── 对端语音（与远端视频的订阅状态机对偶）──
    function clearRemoteAudioRetry() {
      if (remoteAudioRetryTimer !== null) clearTimeout(remoteAudioRetryTimer);
      remoteAudioRetryTimer = null;
    }

    /** 对端音频不可用 / 对端离开：丢掉状态（SDK 已不再推流，不用再退订）。 */
    function dropRemoteAudio() {
      remoteAudioUser = null;
      remoteAudioStarted = false;
      remoteAudioAttempt += 1;
      clearRemoteAudioRetry();
      handlers.onRemoteAudio(null);
    }

    function maybeStartRemoteAudio() {
      if (!audioSubscribeWanted || !remoteAudioUser || remoteAudioStarted || !trtc) return;
      clearRemoteAudioRetry();
      remoteAudioStarted = true;
      var attempt = ++remoteAudioAttempt;
      var user = remoteAudioUser;
      var gen = roomGen;
      serial(function () {
        if (gen !== roomGen || !trtc) return undefined;
        return trtc.muteRemoteAudio(user, false).then(function () {
          if (gen !== roomGen || attempt !== remoteAudioAttempt) return;
          remoteAudioRetries = 0;
          // SDK 自带的播放静音：出声走父页的扬声器音量链（与 TTS 同一个音量 / 输出设备），不双响
          var muting = trtc.setRemoteAudioVolume(user, 0);
          if (muting && typeof muting.catch === 'function') muting.catch(function () {});
          var track = trtc.getAudioTrack({ userId: user });
          if (track && audioSubscribeWanted && user === remoteAudioUser) handlers.onRemoteAudio(track);
        }, function (err) {
          handlers.onLog('warn', 'trtc muteRemoteAudio failed', String(err && err.code));
          if (attempt !== remoteAudioAttempt) return;
          remoteAudioStarted = false;
          if (gen !== roomGen) return;
          var delay = Math.min(REMOTE_RETRY_MAX_MS, REMOTE_RETRY_BASE_MS * Math.pow(2, remoteAudioRetries));
          remoteAudioRetries += 1;
          clearRemoteAudioRetry();
          remoteAudioRetryTimer = setTimeout(function () {
            remoteAudioRetryTimer = null;
            if (gen === roomGen && user === remoteAudioUser && attempt === remoteAudioAttempt) maybeStartRemoteAudio();
          }, delay);
        });
      });
    }

    /** 退订对端语音（已开始订阅时排进队列 muteRemoteAudio(true)）。 */
    function stopRemoteAudio(user) {
      remoteAudioStarted = false;
      remoteAudioAttempt += 1;
      clearRemoteAudioRetry();
      handlers.onRemoteAudio(null);
      if (!trtc || !user) return Promise.resolve();
      var gen = roomGen;
      return serial(function () {
        if (gen !== roomGen || !trtc) return undefined;
        return trtc.muteRemoteAudio(user, true).catch(function () {});
      });
    }

    function enterRoom() {
      var v = vendorOf(creds);
      return trtc.enterRoom({
        sdkAppId: v.sdk_app_id,
        userId: v.user_id,
        userSig: v.user_sig,
        privateMapKey: v.private_map_key,
        strRoomId: v.str_room_id,
        scene: TRTC.TYPE.SCENE_RTC,
        role: TRTC.TYPE.ROLE_ANCHOR,
        autoReceiveVideo: false,
        autoReceiveAudio: false,
      });
    }

    return {
      name: 'trtc',
      join: function (c) {
        creds = c;
        leaving = false;
        if (!trtc) {
          trtc = TRTC.create();
          bind();
        }
        handlers.onState('joining', {});
        var gen = roomGen;
        var p = enterRoom();
        // 离房的「完成」要等这次入房结束：否则重连可能在同一实例上再 enterRoom，与迟到的旧入房交错。
        // 入房期间已离房、旧入房又迟到成功：先发的 exitRoom 可能落在它之前，这里再退一次
        entering = p.then(function () {
          if (gen !== roomGen && trtc) return trtc.exitRoom().catch(function () {});
          return undefined;
        }, function () {});
        return p.then(function () {
          if (gen !== roomGen) return;   // 入房期间已离房：迟到的完成不记状态、不报 joined
          joined = true;
          // 防 SDK 自动把远端切到小流（串门只有一路主流）
          var stopping = trtc.stopPlugin('SmallStreamAutoSwitcher');
          if (stopping && typeof stopping.catch === 'function') stopping.catch(function () {});
          handlers.onState('joined', {});
        });
      },
      updateCredentials: function (c) {
        creds = c;
      },
      canSend: function () {
        return joined && !leaving;
      },
      sendPiece: function (cmd, bytes) {
        var buf = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
        trtc.sendCustomMessage({ cmdId: cmd, data: buf });
      },
      publish: function (track, profile) {
        publishesInFlight += 1;
        var done = function (v) { publishesInFlight -= 1; return v; };
        return serial(function () {
          if (!trtc || !joined) return false;
          // 同一条轨已在发布：重复的 media 快照不再触发 SDK 调用（尺寸变化走 updateProfile）
          if (publishing && publishedTrack === track) return true;
          var config = { publish: true, option: { videoTrack: track, profile: profile } };
          var gen = roomGen;
          var p = publishing ? trtc.updateLocalVideo(config) : trtc.startLocalVideo(config);
          // 发布成功才记状态：失败（暂时性）时下一次 media{publish:true} 还要真的再发一次
          return p.then(function () {
            if (gen !== roomGen) {
              // 离房后才完成的发布：本地视频已不属于任何房间，停掉；停止也留在队列里等它完成，
              // 「离房完成」之后的重新入房不会被这次迟到的停止波及
              if (!trtc) return false;
              return trtc.stopLocalVideo().then(null, function () {}).then(function () { return false; });
            }
            publishing = true;
            publishedTrack = track;
            return true;
          }, function (err) {
            if (publishedTrack !== track) publishedTrack = null;
            throw err;
          });
        }).then(done, function (err) { done(); throw err; });
      },
      /** 与发布 / 撤销共用一条队列：离房的完成等它，先后两次修改不会倒序生效。 */
      updateProfile: function (profile) {
        var gen = roomGen;
        profilesInFlight += 1;
        var done = function (v) { profilesInFlight -= 1; return v; };
        return serial(function () {
          if (!trtc || !publishing || gen !== roomGen) return false;
          return trtc.updateLocalVideo({ option: { profile: profile } }).then(function () { return true; });
        }).then(done, function (err) { done(); throw err; });
      },
      unpublish: function () {
        var stop = function () {
          if (!trtc || !publishing) return undefined;
          publishing = false;
          publishedTrack = null;
          return trtc.stopLocalVideo().catch(function () {});
        };
        if (publishesInFlight > 0 || profilesInFlight > 0) return serial(stop);
        return afterChain(Promise.resolve(stop()));
      },
      /** 发布语音轨（父页 TTS 旁路转成的本页轨）；与视频的 publish 对偶：同一条轨不重复调 SDK，换轨走 updateLocalAudio。 */
      publishAudio: function (track, kbps) {
        publishesInFlight += 1;
        var done = function (v) { publishesInFlight -= 1; return v; };
        return serial(function () {
          if (!trtc || !joined) return false;
          if (audioPublishing && publishedAudio === track) return true;
          var config = { publish: true, option: { audioTrack: track, profile: { sampleRate: 48000, channelCount: 1, bitrate: kbps } } };
          var gen = roomGen;
          var p = audioPublishing ? trtc.updateLocalAudio(config) : trtc.startLocalAudio(config);
          return p.then(function () {
            if (gen !== roomGen) {
              if (!trtc) return false;
              return trtc.stopLocalAudio().then(null, function () {}).then(function () { return false; });
            }
            audioPublishing = true;
            publishedAudio = track;
            return true;
          }, function (err) {
            if (publishedAudio !== track) publishedAudio = null;
            throw err;
          });
        }).then(done, function (err) { done(); throw err; });
      },
      unpublishAudio: function () {
        var stop = function () {
          if (!trtc || !audioPublishing) return undefined;
          audioPublishing = false;
          publishedAudio = null;
          return trtc.stopLocalAudio().catch(function () {});
        };
        if (publishesInFlight > 0 || profilesInFlight > 0) return serial(stop);
        return afterChain(Promise.resolve(stop()));
      },
      setAudioSubscribe: function (on) {
        audioSubscribeWanted = !!on;
        if (audioSubscribeWanted) {
          maybeStartRemoteAudio();
          return Promise.resolve();
        }
        var wasStarted = remoteAudioStarted;
        if (!wasStarted) {
          clearRemoteAudioRetry();
          handlers.onRemoteAudio(null);
          return Promise.resolve();
        }
        return stopRemoteAudio(remoteAudioUser);
      },
      /** 队列里的发布 / 撤销都执行完（销毁前等它，免得排队的停止落在已销毁的实例上）。 */
      idle: function () {
        return opChain;
      },
      setPeerVid: function (vid) {
        peerVid = vid || peerVid;
        // 语音同理：暂记的不是核验出来的对端就退订、丢掉
        if (peerVid && remoteAudioUser && remoteAudioUser !== peerVid) {
          var staleAudio = remoteAudioUser;
          var audioStarted = remoteAudioStarted;
          remoteAudioUser = null;
          if (audioStarted) stopRemoteAudio(staleAudio);
          else dropRemoteAudio();
        }
        // 核验前按「房里另一个人」暂记的远端视频若不是核验出来的对端：丢掉，已经开始的订阅停掉
        if (!peerVid || !remoteVideoUser || remoteVideoUser === peerVid) return;
        var stale = remoteVideoUser;
        var wasStarted = remoteStarted;
        remoteVideoUser = null;
        remoteStarted = false;
        remoteAttempt += 1;
        clearRemoteRetry();
        handlers.onRemoteTrack(null);
        if (!wasStarted || !trtc) return;
        var gen = roomGen;
        serial(function () {
          if (gen !== roomGen || !trtc) return undefined;
          return trtc.stopRemoteVideo({ userId: stale, streamType: TRTC.TYPE.STREAM_TYPE_MAIN }).catch(function () {});
        });
      },
      setSubscribe: function (on) {
        subscribeWanted = !!on;
        if (subscribeWanted) {
          maybeStartRemote();
          return Promise.resolve();
        }
        clearRemoteRetry();
        handlers.onRemoteTrack(null);
        if (!trtc || !remoteStarted || !remoteVideoUser) return Promise.resolve();
        remoteStarted = false;
        remoteAttempt += 1;
        var user = remoteVideoUser;
        var gen = roomGen;
        return serial(function () {
          if (gen !== roomGen || !trtc) return undefined;
          return trtc.stopRemoteVideo({ userId: user, streamType: TRTC.TYPE.STREAM_TYPE_MAIN }).catch(function () {});
        });
      },
      /** 停媒体并离房；画布轨不 stop（自定义轨归调用方，重连后复用）。 */
      leave: function () {
        leaving = true;
        joined = false;
        remoteStarted = false;
        remoteAttempt += 1;
        remoteRetries = 0;
        clearRemoteRetry();
        roomGen += 1;
        publishing = false;
        publishedTrack = null;
        audioPublishing = false;
        publishedAudio = null;
        remoteAudioUser = null;
        remoteAudioStarted = false;
        remoteAudioAttempt += 1;
        remoteAudioRetries = 0;
        clearRemoteAudioRetry();
        // 同远端视频：离房前控制器已 stopAllMedia 停播对端语音，这里只清状态
        if (!trtc) return Promise.resolve();
        // exitRoom 立即发出；交给重连的「离房完成」还要等在途的发布 / 撤销跑完，
        // 免得旧的 SDK 调用与新一次 enterRoom 交错
        var exiting = trtc.exitRoom().catch(function () {});
        departing = exiting;
        return Promise.all([exiting, opChain, entering]).then(function () {});
      },
      /**
       * 拆掉适配器，返回清理做完的 promise。TRTC 的 destroy 要求先 exitRoom：被同 vid 的新页面顶掉（4409）时不离房、
       * 留给接替页面入房顶号；接替页面没能入房时，过一会儿兜底 exitRoom 再 destroy，旧实例不一直占着房间。
       */
      destroy: function (opts) {
        var t = trtc;
        trtc = null;
        if (!t) return;
        // immediate：控制器丢掉卡住的适配器换新的时用，不留给接替页面顶号，马上退房（中断还没完成的进房）
        var waitMs = opts && opts.immediate ? 0 : SUPERSEDED_EXIT_MS;
        var destroyNow = function () {
          try {
            t.destroy();
          } catch (_) {
            // 已销毁
          }
        };
        // 已经发出过离房：离房等超时后才拆时 exitRoom 可能还在路上，等它做完再销毁（TRTC 要求先退房）、再算清理完成
        if (leaving) return boundCleanup(departing).then(destroyNow);
        if (!(joined || entering)) {
          destroyNow();
          return Promise.resolve();
        }
        return new Promise(function (resolve) {
          setTimeout(resolve, waitMs);
        }).then(function () {
          return boundCleanup(Promise.resolve().then(function () { return t.exitRoom(); }));
        }).then(destroyNow);
      },
      remoteVideoAvailable: function () {
        return !!remoteVideoUser;
      },
      /** 断线 / 重连：丢掉断线前的统计快照，重连后等 SDK 的新统计。 */
      resetStats: function () {
        lastStats = null;
        lastNetwork = null;
      },
      stats: function () {
        var out = {};
        if (lastStats) {
          var big = (lastStats.localStatistics && lastStats.localStatistics.video || []).filter(function (v) { return v.videoType === 'big'; })[0];
          if (big) {
            out.enc_fps = big.frameRate;
            out.tx_kbps = big.bitrate;
            out.tx_w = big.width;
            out.tx_h = big.height;
          }
          var remote = (lastStats.remoteStatistics || [])[0];
          var rv = remote && (remote.video || []).filter(function (v) { return v.videoType === 'big'; })[0];
          if (rv) out.rx_kbps = rv.bitrate;
          out.rtt_ms = lastStats.rtt;
          // 订阅方（host）报的是接收样本：用服务端→本端的下行丢包；发布方（guest）用上行丢包
          out.loss_pct = subscribeWanted ? lastStats.downLoss : lastStats.upLoss;
        }
        var netLoss = lastNetwork && (subscribeWanted ? lastNetwork.downlinkLoss : lastNetwork.uplinkLoss);
        if (typeof netLoss === 'number') out.loss_pct = netLoss;
        return out;
      },
    };
  }

  /** 能力门 ③：TRTC.isSupported() 的 detail → transport_ok / 编解码列表。 */
  function trtcCapabilities(TRTC) {
    return TRTC.isSupported().then(function (res) {
      var d = (res && res.detail) || {};
      var codecs = [];
      if (d.isH264EncodeSupported || d.isH264DecodeSupported) codecs.push('h264');
      if (d.isVp8EncodeSupported || d.isVp8DecodeSupported) codecs.push('vp8');
      return {
        // result 是 SDK 的总判定（还看浏览器、媒体设备等），detail 只是其中几项
        transportOk: !!(res && res.result === true) && d.isWebRTCSupported !== false && typeof root.RTCPeerConnection === 'function',
        encode: !!(d.isH264EncodeSupported || d.isVp8EncodeSupported),
        decode: !!(d.isH264DecodeSupported || d.isVp8DecodeSupported),
        codecs: codecs,
      };
    });
  }

  NVT.vendors = NVT.vendors || {};
  NVT.vendors.trtc = {
    globalName: 'TRTC',
    script: '/static/libs/trtc.js',
    createAdapter: createTrtcAdapter,
    capabilities: trtcCapabilities,
  };
})(typeof window !== 'undefined' ? window : globalThis);
