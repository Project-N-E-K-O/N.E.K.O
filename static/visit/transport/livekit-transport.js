/*
 * 串门传输页：LiveKit 适配器（livekit-client 2.22.3，设计稿 §3.4.3 / §3.5.1 / §4.3）。
 *
 * 单层编码：scalabilityMode 必须显式 'L1T1'（SDK 对 vp9 默认三层 SVC）、simulcast 关、
 * maintain-framerate；autoSubscribe:false，host 收到 media{subscribe:true} 才订阅对端视频。
 * 数据通道 topic visit.ctl / visit.text 可靠、visit.lossy 不可靠；对端 vid 已知后定向发送。
 *
 * 猫娘语音（OD-15 v4）：只发父页交来的 TTS 旁路轨（publishTrack 一条 MediaStreamTrack），不开麦克风；
 * source 只能报 Microphone（LiveKit 的音频来源枚举里没有「合成音频」，token 按它授权），
 * dtx 开、red 关（red 会把码率翻倍），maxBitrate = audio_kbps（32）。收到 audio_subscribe 才订阅
 * 对端音频；不 attach（SDK 不出声），mediaStreamTrack 交给控制器、由父页接进自家扬声器音量链。
 */
(function (root) {
  'use strict';

  var NVT = root.NekoVisitTransport = root.NekoVisitTransport || {};
  var SUB_RETRY_BASE_MS = 2000;      // 订阅失败后的首次重试间隔，之后翻倍
  var SUB_RETRY_MAX_MS = 30000;
  var SUPERSEDED_DISCONNECT_MS = 15000;   // 拆掉后兜底断开旧房间对象的延迟（留给接替的页面入房顶号）
  var CLEANUP_BOUND_MS = 8000;            // 兜底断开最多等这么久（父页按 2 + 15 + 8 s 再留余量保留 iframe）

  /** 等退房 / 断开做完，但最多 CLEANUP_BOUND_MS：网络坏着时它可能一直不落定（失败也算完）。 */
  function boundCleanup(p) {
    return Promise.race([
      Promise.resolve(p).then(null, function () {}),
      new Promise(function (resolve) { setTimeout(resolve, CLEANUP_BOUND_MS); }),
    ]).then(function () {});
  }

  function reasonName(LK, reason) {
    var table = LK && LK.DisconnectReason;
    if (table && typeof table[reason] === 'string') return table[reason];
    return reason === undefined || reason === null ? '' : String(reason);
  }

  // 明文 ws:// 只放行回环地址（本地开发环回 livekit-server --dev；与后端 credentials._is_loopback_name 同一规则）
  function isLoopbackLiteral(host) {
    return /^127(?:\.\d{1,3}){3}$/.test(host) || host === '::1' || host === 'localhost';
  }

  function hostAllowed(url, allowedHosts) {
    var host;
    try {
      var parsed = new URL(url);
      // 与后端 urlsplit().hostname 对齐：IPv6 不带方括号
      host = parsed.hostname.toLowerCase().replace(/^\[|\]$/g, '');
      if (parsed.protocol !== 'wss:' && !(parsed.protocol === 'ws:' && isLoopbackLiteral(host))) return false;
    } catch (_) {
      return false;
    }
    return Array.isArray(allowedHosts) && allowedHosts.some(function (h) {
      return typeof h === 'string' && h.toLowerCase() === host;
    });
  }

  function createLivekitAdapter(opts) {
    var LK = opts.sdk;
    var handlers = opts.handlers;
    var room = null;
    var creds = null;
    var joined = false;
    var leaving = false;
    var departing = null;   // leave 发出的 disconnect：destroy 时还没落定就等它做完（有上限）
    var publication = null;
    var publishedTrack = null;
    // 发布 / 撤销按顺序执行：有发布在途时，停止排在它之后、新的发布再排在停止之后，
    // 不会出现「旧发布迟到完成后把新发布撤掉」；没有发布在途时停止同步执行（传输 WS 断开时
    // 要在离房之前就停掉）。roomGen 只在离房时递增：离房后迟到完成的发布不记状态
    var roomGen = 0;
    var opChain = Promise.resolve();
    var publishesInFlight = 0;
    var profilesInFlight = 0;   // 排队 / 进行中的 setParameters：撤销排在它后面，不和它交错
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
    var audioPublication = null;
    var publishedAudio = null;
    var audioSubscribeWanted = false;
    var remoteAudioPublication = null;
    var remoteAudioIdentity = null;
    var remoteAudioTrack = null;
    var audioSubRetries = 0;
    var audioSubRetryTimer = null;
    var subscribeWanted = false;
    var peerVid = opts.peerVid || null;
    var remotePublication = null;
    var remoteIdentity = null;   // remotePublication 属于谁（核验出对端后据此丢掉不匹配的候选）
    var remoteTrack = null;
    var rxMark = null;
    var txMark = null;
    var statsEpoch = 0;   // 收发统计基准的代号：重置后，之前发出、迟到回来的取样不能把旧基准写回去
    var localQuality = null;
    var entering = null;
    var subRetries = 0;
    var subRetryTimer = null;

    function vendorOf(c) {
      return (c && c.vendor && c.vendor.livekit) || {};
    }

    function codecPref(c) {
      var codec = c && c.publish && c.publish.codec;
      return codec === 'vp8' ? 'vp8' : 'vp9';
    }

    function isPeer(identity) {
      return !peerVid || identity === peerVid;
    }

    function newRoom(c) {
      var publishCfg = (c && c.publish) || {};
      return new LK.Room({
        adaptiveStream: false,
        dynacast: false,
        webAudioMix: false,   // 远端语音不进 SDK 自己的 AudioContext：轨交父页出声
        publishDefaults: {
          simulcast: false,
          videoCodec: codecPref(c),
          scalabilityMode: 'L1T1',
          videoEncoding: { maxBitrate: (publishCfg.bitrate_kbps || 528) * 1000, maxFramerate: publishCfg.fps || 30 },
          degradationPreference: 'maintain-framerate',
        },
      });
    }

    function applyProfile(profile) {
      var sender = publication && publication.track && publication.track.sender;
      if (!sender || typeof sender.getParameters !== 'function' || !profile) return Promise.resolve(false);
      var params = sender.getParameters();
      if (!params.encodings || !params.encodings.length) return Promise.resolve(false);
      params.encodings[0].maxBitrate = profile.bitrate * 1000;
      params.encodings[0].maxFramerate = profile.frameRate;
      return sender.setParameters(params).then(function () { return true; });
    }

    /** applyProfile 解析成 false（发送端 / 编码参数还没就绪）同样算没设上。 */
    function profileResult(applied) {
      return applied === true ? true : 'profile_failed';
    }

    function considerPublication(pub, participant) {
      if (!participant || !isPeer(participant.identity)) return;
      if (pub && pub.kind === 'audio') {
        if (pub !== remoteAudioPublication) audioSubRetries = 0;
        remoteAudioPublication = pub;
        remoteAudioIdentity = participant.identity;
        if (audioSubscribeWanted) pub.setSubscribed(true);
        return;
      }
      if (!pub || pub.kind !== 'video') return;
      if (pub !== remotePublication) subRetries = 0;
      remotePublication = pub;
      remoteIdentity = participant.identity;
      if (subscribeWanted) pub.setSubscribed(true);
    }

    function clearSubRetry() {
      if (subRetryTimer !== null) clearTimeout(subRetryTimer);
      subRetryTimer = null;
    }

    function clearAudioSubRetry() {
      if (audioSubRetryTimer !== null) clearTimeout(audioSubRetryTimer);
      audioSubRetryTimer = null;
    }

    /** 对端语音的发布没了（撤销 / 对端离开 / 核验出不是对端）：丢掉状态并通知控制器。 */
    function dropRemoteAudio() {
      clearAudioSubRetry();
      remoteAudioPublication = null;
      remoteAudioIdentity = null;
      remoteAudioTrack = null;
      handlers.onRemoteAudio(null);
    }

    /** 语音订阅失败：与视频的 retrySubscription 对偶。 */
    function retryAudioSubscription(pub) {
      clearAudioSubRetry();
      var gen = roomGen;
      var delay = Math.min(SUB_RETRY_MAX_MS, SUB_RETRY_BASE_MS * Math.pow(2, audioSubRetries));
      audioSubRetries += 1;
      audioSubRetryTimer = setTimeout(function () {
        audioSubRetryTimer = null;
        if (gen !== roomGen || !audioSubscribeWanted || pub !== remoteAudioPublication) return;
        pub.setSubscribed(false);
        pub.setSubscribed(true);
      }, delay);
    }

    /** 订阅失败（暂时性协商失败）：同一发布仍想看时按退避重新请求，直到取消订阅 / 对端撤销发布 / 离房。 */
    function retrySubscription(pub) {
      clearSubRetry();
      var gen = roomGen;
      var delay = Math.min(SUB_RETRY_MAX_MS, SUB_RETRY_BASE_MS * Math.pow(2, subRetries));
      subRetries += 1;
      subRetryTimer = setTimeout(function () {
        subRetryTimer = null;
        if (gen !== roomGen || !subscribeWanted || pub !== remotePublication) return;
        pub.setSubscribed(false);
        pub.setSubscribed(true);
      }, delay);
    }

    function bind() {
      var E = LK.RoomEvent;
      room.on(E.ParticipantConnected, function (p) {
        if (leaving) return;   // 离房期间排队到的进房事件不算（重进后以新房间里的为准）
        if (isPeer(p.identity)) handlers.onPeer('enter', p.identity, null);
      });
      room.on(E.ParticipantDisconnected, function (p, reason) {
        if (!isPeer(p.identity)) return;
        var name = reasonName(LK, reason);
        // 对端主动断开 = 暂定离开（35 s 重入宽限）；其它原因不上报，交给心跳判死
        handlers.onPeer('exit', p.identity, name, name === 'CLIENT_INITIATED');
        if (remotePublication && p.identity === (remotePublication.participantIdentity || p.identity)) {
          remotePublication = null;
          handlers.onRemoteTrack(null);
        }
        if (remoteAudioPublication && p.identity === remoteAudioIdentity) dropRemoteAudio();
      });
      room.on(E.TrackPublished, function (pub, participant) {
        considerPublication(pub, participant);
      });
      room.on(E.TrackUnpublished, function (pub, participant) {
        if (pub && pub === remoteAudioPublication) {
          dropRemoteAudio();
          return;
        }
        if (pub === remotePublication && participant && isPeer(participant.identity)) {
          clearSubRetry();
          remotePublication = null;
          handlers.onRemoteTrack(null);
        }
      });
      room.on(E.TrackSubscribed, function (track, pub, participant) {
        if (track && track.kind === 'audio') {
          // 只认当前那份语音发布；不 attach（SDK 不出声），轨交控制器
          if (!participant || !isPeer(participant.identity) || !audioSubscribeWanted || !pub || pub !== remoteAudioPublication) return;
          audioSubRetries = 0;
          clearAudioSubRetry();
          remoteAudioTrack = track;
          handlers.onRemoteAudio(track.mediaStreamTrack);
          return;
        }
        if (!participant || !isPeer(participant.identity) || track.kind !== 'video' || !subscribeWanted) return;
        // 只认当前那份发布：迟到的旧发布（已被换掉或已撤销）订阅成功时不能把旧轨挂上去
        if (!pub || pub !== remotePublication) return;
        subRetries = 0;
        clearSubRetry();
        remoteTrack = track;
        rxMark = null;
        handlers.onRemoteTrack(track.mediaStreamTrack);
      });
      room.on(E.TrackUnsubscribed, function (track) {
        if (track && track.kind === 'audio') {
          if (track === remoteAudioTrack) {
            remoteAudioTrack = null;
            handlers.onRemoteAudio(null);
          }
          return;
        }
        // 只认当前那条轨：迟到的旧轨取消订阅不能把已经换上的新轨也撤掉
        if (track && track.kind === 'video' && track === remoteTrack) {
          remoteTrack = null;
          handlers.onRemoteTrack(null);
        }
      });
      room.on(E.TrackSubscriptionFailed, function (trackSid, participant) {
        var apub = remoteAudioPublication;
        if (apub && apub.trackSid && trackSid && apub.trackSid === trackSid) {
          if (!audioSubscribeWanted || !participant || !isPeer(participant.identity)) return;
          handlers.onLog('warn', 'livekit audio subscription failed', 'subscribe_failed');
          retryAudioSubscription(apub);
          return;
        }
        var pub = remotePublication;
        if (!pub || !subscribeWanted || !participant || !isPeer(participant.identity)) return;
        if (pub.trackSid && trackSid && pub.trackSid !== trackSid) return;
        handlers.onLog('warn', 'livekit subscription failed', 'subscribe_failed');
        retrySubscription(pub);
      });
      room.on(E.DataReceived, function (payload, participant, kind, topic) {
        if (!participant) return;
        var cmd = NVT.cmdForTopic(topic);
        handlers.onPiece(participant.identity, cmd, payload);
      });
      room.on(E.Reconnecting, function () {
        // 重连前的连接质量不代表重连后的：等下一次质量回调
        localQuality = null;
        if (!leaving) handlers.onState('reconnecting', {});
      });
      room.on(E.Reconnected, function () {
        if (!leaving) handlers.onState('connected', {});
      });
      room.on(E.Disconnected, function (reason) {
        var name = reasonName(LK, reason);
        var wasJoined = joined;
        joined = false;
        if (leaving || name === 'CLIENT_INITIATED') return;
        if (name === 'DUPLICATE_IDENTITY') {
          // 同一 vid 的新 iframe 入房把这里顶下线：只结束自己
          handlers.onState('kicked', { vendor_reason: 'kick' });
          return;
        }
        if (name === 'ROOM_DELETED') {
          handlers.onState('kicked', { vendor_reason: 'room_disband' });
          return;
        }
        if (name === 'PARTICIPANT_REMOVED') {
          handlers.onState('kicked', { vendor_reason: 'banned' });
          return;
        }
        if (wasJoined) {
          // SDK 重连失败（多为旧 token 过期）：用最新凭证手动重新入房
          handlers.onState('reconnecting', { vendor_reason: name });
          handlers.onNeedRejoin();
        }
      });
      room.on(E.ConnectionQualityChanged, function (quality, participant) {
        if (participant && room && participant === room.localParticipant) {
          // 只在质量变化时回调：记住当前值，统计节拍每拍按它取样（currentLossPct）
          localQuality = quality;
          var bad = quality === 'poor' || quality === 'lost';
          handlers.onNetwork({ lossPct: bad ? 100 : 0 });
        }
      });
    }

    return {
      name: 'livekit',
      join: function (c) {
        creds = c;
        leaving = false;
        var v = vendorOf(c);
        if (!hostAllowed(v.url, c.allowed_hosts)) {
          return Promise.reject(Object.assign(new Error('host not allowed'), { errorCode: 'host_not_allowed' }));
        }
        if (room) {
          try {
            room.removeAllListeners();
          } catch (_) {
            // 旧房间对象
          }
        }
        room = newRoom(c);
        bind();
        handlers.onState('joining', {});
        var gen = roomGen;
        var current = room;
        var p = current.connect(v.url, v.token, { autoSubscribe: false });
        // 连接期间已离房、旧连接又迟到成功：再断开那个旧房间对象一次
        entering = p.then(function () {
          if (gen !== roomGen) return Promise.resolve(current.disconnect(false)).catch(function () {});
          return undefined;
        }, function () {});
        return p.then(function () {
          if (gen !== roomGen) return;   // 连接期间已离房：迟到的完成不记状态、不报 joined
          joined = true;
          room.remoteParticipants.forEach(function (p) {
            // 入房前就在的对端不会再触发 ParticipantConnected
            if (isPeer(p.identity)) handlers.onPeer('enter', p.identity, null);
            p.trackPublications.forEach(function (pub) {
              considerPublication(pub, p);
            });
          });
          handlers.onState('joined', {});
        }, function (err) {
          // LiveKit 的连接异常不含凭证；保留一段压缩后的原因，方便区分
          // 信令、ICE 和服务端拒绝，不能只留下笼统的 join_failed。
          var code = err && (err.errorCode || err.code || err.name);
          var message = err && err.message;
          var detail = [code, message].filter(Boolean).join(': ')
            .replace(/\s+/g, ' ').slice(0, 240) || 'join_failed';
          handlers.onLog('warn', 'livekit connect rejected', detail);
          throw err;
        });
      },
      updateCredentials: function (c) {
        creds = c;
      },
      canSend: function () {
        return joined && !leaving && !!room;
      },
      sendPiece: function (cmd, bytes, o) {
        var options = { reliable: cmd !== 3, topic: o.topic };
        if (o.peerVid) options.destinationIdentities = [o.peerVid];
        var sending = room.localParticipant.publishData(bytes, options);
        if (sending && typeof sending.catch === 'function') sending.catch(function () {});
      },
      publish: function (track, profile) {
        publishesInFlight += 1;
        var done = function (v) { publishesInFlight -= 1; return v; };
        return serial(function () {
          if (!room || !joined) return false;
          // 同一条轨已在发布：SDK 整体重连时会按发布时的选项重发（码率 / 帧率回到房间默认），
          // 这里按调用方给的当前 profile 再设一次发送端参数
          if (publication && publishedTrack === track) {
            return applyProfile(profile).then(profileResult, function () { return 'profile_failed'; });
          }
          var gen = roomGen;
          var current = room;
          var before = publication ? current.localParticipant.unpublishTrack(publishedTrack, false) : Promise.resolve();
          return Promise.resolve(before).then(function () {
            return current.localParticipant.publishTrack(track, {
              source: LK.Track.Source.Camera,
              simulcast: false,
              videoCodec: codecPref(creds),
              scalabilityMode: 'L1T1',
              degradationPreference: 'maintain-framerate',
            });
          }).then(function (pub) {
            if (gen !== roomGen) {
              return Promise.resolve(current.localParticipant.unpublishTrack(track, false))
                .then(null, function () {}).then(function () { return false; });
            }
            publication = pub;
            publishedTrack = track;
            txMark = null;
            // 发布时就按传入的 profile 设码率 / 帧率上限（重连恢复的降档级别、兼容模式 15 fps）；
            // 设不上不算发布失败，但要让控制器走 pushProfile 的重试（返回 'profile_failed'）
            return applyProfile(profile).then(profileResult, function () { return 'profile_failed'; });
          });
        }).then(done, function (err) { done(); throw err; });
      },
      /**
       * 构图 / 阶梯 / 取帧帧率变化：同一条轨就地改尺寸后 LiveKit 照常编码新尺寸
       * （T14 验证；不接受时退 replaceTrack），码率上限与帧率上限改在发送端编码参数上。
       */
      updateProfile: function (profile) {
        var gen = roomGen;
        profilesInFlight += 1;
        var done = function (v) { profilesInFlight -= 1; return v; };
        return serial(function () {
          if (gen !== roomGen) return false;
          return applyProfile(profile);
        }).then(done, function (err) { done(); throw err; });
      },
      unpublish: function () {
        var stop = function () {
          if (!room || !publication) return undefined;
          var track = publishedTrack;
          publication = null;
          publishedTrack = null;
          // stopOnUnpublish=false：画布轨留着复用
          return room.localParticipant.unpublishTrack(track, false).catch(function () {});
        };
        if (publishesInFlight > 0 || profilesInFlight > 0) return serial(stop);
        return afterChain(Promise.resolve(stop()));
      },
      /** 发布语音轨（父页 TTS 旁路转成的本页轨）；与视频的 publish 对偶：同一条轨不重复发，换轨先撤旧的。 */
      publishAudio: function (track, kbps) {
        publishesInFlight += 1;
        var done = function (v) { publishesInFlight -= 1; return v; };
        return serial(function () {
          if (!room || !joined) return false;
          if (audioPublication && publishedAudio === track) return true;
          var gen = roomGen;
          var current = room;
          var before = audioPublication ? current.localParticipant.unpublishTrack(publishedAudio, false) : Promise.resolve();
          return Promise.resolve(before).then(function () {
            return current.localParticipant.publishTrack(track, {
              source: LK.Track.Source.Microphone,
              dtx: true,
              red: false,
              forceStereo: false,
              audioPreset: { maxBitrate: kbps * 1000 },
            });
          }).then(function (pub) {
            if (gen !== roomGen) {
              return Promise.resolve(current.localParticipant.unpublishTrack(track, false))
                .then(null, function () {}).then(function () { return false; });
            }
            audioPublication = pub;
            publishedAudio = track;
            return true;
          });
        }).then(done, function (err) { done(); throw err; });
      },
      unpublishAudio: function () {
        var stop = function () {
          if (!room || !audioPublication) return undefined;
          var track = publishedAudio;
          audioPublication = null;
          publishedAudio = null;
          // stopOnUnpublish=false：语音轨归控制器（voice.js），重新发布时复用
          return room.localParticipant.unpublishTrack(track, false).catch(function () {});
        };
        if (publishesInFlight > 0 || profilesInFlight > 0) return serial(stop);
        return afterChain(Promise.resolve(stop()));
      },
      setAudioSubscribe: function (on) {
        audioSubscribeWanted = !!on;
        if (!audioSubscribeWanted) clearAudioSubRetry();
        if (remoteAudioPublication) remoteAudioPublication.setSubscribed(audioSubscribeWanted);
        if (!audioSubscribeWanted) {
          remoteAudioTrack = null;
          handlers.onRemoteAudio(null);
        }
        return Promise.resolve();
      },
      idle: function () {
        return opChain;
      },
      setPeerVid: function (vid) {
        peerVid = vid || peerVid;
        // 语音同理：暂记的不是核验出来的对端就退订、丢掉
        if (peerVid && remoteAudioPublication && remoteAudioIdentity !== peerVid) {
          var staleAudio = remoteAudioPublication;
          dropRemoteAudio();
          try {
            staleAudio.setSubscribed(false);
          } catch (_) {
            // 发布已不在
          }
        }
        // 核验前按「房里另一个人」暂记的远端发布若不是核验出来的对端：退订、丢掉
        if (!peerVid || !remotePublication || remoteIdentity === peerVid) return;
        var stale = remotePublication;
        remotePublication = null;
        remoteIdentity = null;
        remoteTrack = null;
        rxMark = null;
        clearSubRetry();
        try {
          stale.setSubscribed(false);
        } catch (_) {
          // 发布已不在
        }
        handlers.onRemoteTrack(null);
      },
      setSubscribe: function (on) {
        subscribeWanted = !!on;
        if (!subscribeWanted) clearSubRetry();
        if (remotePublication) remotePublication.setSubscribed(subscribeWanted);
        if (!subscribeWanted) handlers.onRemoteTrack(null);
        return Promise.resolve();
      },
      /** 离房；stopTracks=false，画布轨不随之结束。 */
      leave: function () {
        leaving = true;
        joined = false;
        roomGen += 1;
        publication = null;
        publishedTrack = null;
        audioPublication = null;
        publishedAudio = null;
        audioSubRetries = 0;
        clearAudioSubRetry();
        // 同远端视频：离房前控制器已 stopAllMedia 停播对端语音，这里只清状态
        remoteAudioPublication = null;
        remoteAudioIdentity = null;
        remoteAudioTrack = null;
        remotePublication = null;
        remoteTrack = null;
        rxMark = null;
        txMark = null;
        statsEpoch += 1;
        localQuality = null;
        subRetries = 0;
        clearSubRetry();
        if (!room) return Promise.resolve();
        var disconnecting = room.disconnect(false).catch(function () {});
        departing = disconnecting;
        return Promise.all([disconnecting, opChain, entering]).then(function () {});
      },
      /**
       * 拆掉适配器。被同 vid 的新页面顶掉（4409）时不离房：新页面入房会把这边顶下线；但新页面若
       * 没能入房（SDK 加载 / 能力门 / 连接失败），旧连接会一直占着房间——过一会儿兜底断开它。
       * 已经离过房或已被顶下线时再断开一次是空操作。
       */
      /** 返回清理做完的 promise（传输页据此告诉父页可以移除 iframe）。 */
      destroy: function (opts) {
        var r = room;
        room = null;
        if (!r) return Promise.resolve();
        // immediate：控制器丢掉卡住的适配器换新的时用，不留给接替页面顶号，马上断开（中断还没完成的连接）
        var waitMs = opts && opts.immediate ? 0 : SUPERSEDED_DISCONNECT_MS;
        // 已经发出过离房：离房等超时后才拆时断开可能还在路上，等它做完再算清理完成（父页据此才移除 iframe）
        if (leaving) return boundCleanup(departing);
        return new Promise(function (resolve) {
          setTimeout(resolve, waitMs);
        }).then(function () {
          return boundCleanup(Promise.resolve().then(function () { return r.disconnect(false); }));
        });
      },
      remoteVideoAvailable: function () {
        return !!remotePublication;
      },
      stats: function () {
        return {};
      },
      /** 断线 / 重连：收发统计的差分基准作废。 */
      resetStats: function () {
        statsEpoch += 1;
        rxMark = null;
        txMark = null;
      },
      /** 当前连接质量换算的丢包样本（poor / lost 视同超阈值）；未知返回 undefined。 */
      currentLossPct: function () {
        if (!joined || !localQuality) return undefined;
        if (localQuality === 'poor' || localQuality === 'lost') return 100;
        if (localQuality === 'excellent' || localQuality === 'good') return 0;
        return undefined;
      },
      /** host 侧接收统计（rx_kbps / loss_pct / rtt_ms），取自订阅轨的 RTCRtpReceiver；后端要字段齐全才转给对端。 */
      receiverStats: function (nowMs) {
        var receiver = remoteTrack && remoteTrack.receiver;
        if (!receiver || typeof receiver.getStats !== 'function') return Promise.resolve(null);
        var epoch = statsEpoch;
        var sampled = remoteTrack;
        return receiver.getStats().then(function (report) {
          if (epoch !== statsEpoch || remoteTrack !== sampled) return null;
          var inbound = null;
          var rttS = null;
          report.forEach(function (r) {
            if (r.type === 'inbound-rtp' && r.kind === 'video') inbound = r;
            if (r.type === 'candidate-pair' && (r.nominated || r.state === 'succeeded') && typeof r.currentRoundTripTime === 'number') rttS = r.currentRoundTripTime;
          });
          if (!inbound) return null;
          // 差分的时间用报告自己的 timestamp（getStats 完成时间参差时按发起时间算会把码率算偏）；没有才退回调用时刻
          var at = typeof inbound.timestamp === 'number' && isFinite(inbound.timestamp) ? inbound.timestamp : nowMs;
          var mark = { at: at, bytes: inbound.bytesReceived || 0, lost: inbound.packetsLost || 0, recv: inbound.packetsReceived || 0 };
          var prev = rxMark;
          // 慢回来的旧取样（比当前基准还早）不覆盖基准
          if (prev && !(mark.at > prev.at)) return null;
          rxMark = mark;
          var out = {};
          if (rttS !== null) out.rtt_ms = Math.round(rttS * 1000);
          if (prev && mark.at > prev.at) {
            out.rx_kbps = Math.max(0, Math.round((mark.bytes - prev.bytes) * 8 / (mark.at - prev.at)));
            var lost = Math.max(0, mark.lost - prev.lost);
            var got = Math.max(0, mark.recv - prev.recv);
            out.loss_pct = lost + got > 0 ? lost * 100 / (lost + got) : 0;
          }
          return out;
        }).catch(function () { return null; });
      },
      senderStats: function () {
        var track = publication && publication.track;
        if (!track || typeof track.getSenderStats !== 'function') return Promise.resolve(null);
        var epoch = statsEpoch;
        return track.getSenderStats().then(function (list) {
          if (epoch !== statsEpoch || !publication || publication.track !== track) return null;
          var s = Array.isArray(list) ? list[0] : list;
          if (!s) return null;
          var out = {
            enc_fps: s.framesPerSecond,
            tx_w: s.frameWidth,
            tx_h: s.frameHeight,
            qlr: s.qualityLimitationReason,
            enc: s.encoderImplementation,
          };
          // 发送码率与编码帧率：两次取样之间的 bytesSent / framesSent 差（timestamp 单位 ms）。
          // 浏览器不给 framesPerSecond 时用 framesSent 推，不能退回打包器的出帧数（编码器丢帧看不出来）
          if (typeof s.timestamp === 'number') {
            var prev = txMark;
            if (prev && !(s.timestamp > prev.at)) return null;
            txMark = { at: s.timestamp, bytes: s.bytesSent, frames: s.framesSent };
            if (prev && txMark.at > prev.at) {
              var dt = txMark.at - prev.at;
              if (typeof txMark.bytes === 'number' && typeof prev.bytes === 'number' && txMark.bytes >= prev.bytes) {
                out.tx_kbps = Math.round((txMark.bytes - prev.bytes) * 8 / dt);
              }
              if (typeof out.enc_fps !== 'number' && typeof txMark.frames === 'number' && typeof prev.frames === 'number' && txMark.frames >= prev.frames) {
                out.enc_fps = (txMark.frames - prev.frames) * 1000 / dt;
              }
            }
          }
          return out;
        }).catch(function () { return null; });
      },
    };
  }

  /**
   * 能力门 ③：RTCRtpSender / Receiver 的编解码列表。guest 必须能编码凭证选定的 codec
   * （vp9 编不了而 vp8 能编时本侧退到 vp8，经 codec 字段回给控制器）；host 要能解 vp9 与 vp8
   * （对端发哪种由对端的凭证与回退决定）。
   */
  function livekitCapabilities(LK, creds, side) {
    var send = [];
    var recv = [];
    try {
      send = (root.RTCRtpSender.getCapabilities('video') || {}).codecs || [];
      recv = (root.RTCRtpReceiver.getCapabilities('video') || {}).codecs || [];
    } catch (_) {
      // 没有能力查询 = 没有可用编解码
    }
    function names(list) {
      var out = [];
      list.forEach(function (c) {
        var n = String(c.mimeType || '').toLowerCase().replace('video/', '');
        if ((n === 'vp9' || n === 'vp8' || n === 'h264') && out.indexOf(n) === -1) out.push(n);
      });
      return out;
    }
    var enc = names(send);
    var dec = names(recv);
    var supported = typeof LK.isBrowserSupported === 'function' ? LK.isBrowserSupported() : true;
    var selected = creds && creds.publish && creds.publish.codec === 'vp8' ? 'vp8' : 'vp9';
    var codec = enc.indexOf(selected) !== -1 ? selected : (enc.indexOf('vp8') !== -1 ? 'vp8' : null);
    return Promise.resolve({
      transportOk: !!supported && typeof root.RTCPeerConnection === 'function',
      encode: side === 'host' || codec !== null,
      decode: dec.indexOf('vp9') !== -1 && dec.indexOf('vp8') !== -1,
      codec: side === 'guest' ? codec : null,
      codecs: enc.concat(dec.filter(function (n) { return enc.indexOf(n) === -1; })),
    });
  }

  NVT.vendors = NVT.vendors || {};
  NVT.vendors.livekit = {
    globalName: 'LivekitClient',
    script: '/static/libs/livekit-client.umd.js',
    createAdapter: createLivekitAdapter,
    capabilities: livekitCapabilities,
    hostAllowed: hostAllowed,
  };
})(typeof window !== 'undefined' ? window : globalThis);
