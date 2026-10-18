/*
 * 串门传输页：数据通道的无状态转发层（设计稿 §3.5.1 / §4.1）。
 *
 * - fragment / Reassembler 与 utils/visit_wire.py 的 fragment / Reassembler 字节对偶
 *   （信封 {v, r, m, i, n, p}，每片 ≤1000 B 按字节，按 (from_vid, m) 重组、6 s 未齐丢）。
 * - SendPacer：对实际发出的分片按 1 s 滑动窗口限流（≤6144 B、≤20 片），超出排队；
 *   可丢类（cmd 3 与 line_delta）在积压 >10 s 或队列 >200 条时作废，必达类与 cmd 1 永不丢。
 * - TransportCore：把一个 vendor 适配器（trtc-transport.js / livekit-transport.js）包成
 *   VisitTransport 接口；只做分片、限速、分流、丢非当前 visit_id，不读消息语义。
 *
 * 浏览器里挂在 window.NekoVisitTransport 上；node 测试经 vm 载入同一份源码。
 */
(function (root) {
  'use strict';

  var NVT = root.NekoVisitTransport = root.NekoVisitTransport || {};

  var ENVELOPE_VERSION = 1;
  var PIECE_MAX_BYTES = 1000;         // VISIT_PIECE_MAX_BYTES
  var PIECES_MAX = 8;                 // VISIT_PIECES_MAX
  var REASSEMBLY_TIMEOUT_MS = 6000;   // VISIT_REASSEMBLY_TIMEOUT_S
  var REASSEMBLY_MAX_ENTRIES = 32;    // VISIT_REASSEMBLY_MAX_ENTRIES
  var WINDOW_MS = 1000;
  var WINDOW_BYTES = 6144;            // VISIT_VENDOR_WINDOW_BYTES
  var WINDOW_PIECES = 20;             // VISIT_VENDOR_WINDOW_MSGS
  // 收方向每个发送者的片预算：正常对端的发送节流不会超过 WINDOW_BYTES / WINDOW_PIECES，
  // 留一倍余量吸收网络抖动造成的扎堆到达；超出的片在重组之前就丢，不让「只发不完整片」绕过后端的整条限流
  var RECV_WINDOW_BYTES = WINDOW_BYTES * 2;
  var RECV_WINDOW_PIECES = WINDOW_PIECES * 2;
  var RECV_TRACKED_SENDERS = 4;
  var QUEUE_MAX = 200;                // §4.3 send：出站队列上限
  var DROPPABLE_MAX_AGE_MS = 10000;   // 积压 >10 s 作废可丢类
  var BACKPRESSURE_ON_AGE_MS = 1000;  // 队首积压超过它就回 tx_backpressure{on:true}
  var U32_MAX = 4294967295;
  var VISIT_ID_RE = /^[A-Za-z0-9_-]{22}$/;
  var CMDS = { 1: 'visit.ctl', 2: 'visit.text', 3: 'visit.lossy' };

  NVT.constants = {
    PIECE_MAX_BYTES: PIECE_MAX_BYTES,
    PIECES_MAX: PIECES_MAX,
    REASSEMBLY_TIMEOUT_MS: REASSEMBLY_TIMEOUT_MS,
    WINDOW_MS: WINDOW_MS,
    WINDOW_BYTES: WINDOW_BYTES,
    WINDOW_PIECES: WINDOW_PIECES,
    RECV_WINDOW_BYTES: RECV_WINDOW_BYTES,
    RECV_WINDOW_PIECES: RECV_WINDOW_PIECES,
    QUEUE_MAX: QUEUE_MAX,
    DROPPABLE_MAX_AGE_MS: DROPPABLE_MAX_AGE_MS,
    TOPICS: CMDS,
  };

  // ── 编码工具（与 visit_wire._esc1_len / _utf8_len_char 同一规则）─────────

  var SHORT_ESCAPES = { 8: 1, 9: 1, 10: 1, 12: 1, 13: 1 };

  function utf8LenCodePoint(cp) {
    if (cp < 0x80) return 1;
    if (cp < 0x800) return 2;
    if (cp < 0x10000) return 3;
    return 4;
  }

  function esc1Len(cp) {
    if (cp === 0x22 || cp === 0x5c) return 2;
    if (cp < 0x20) return SHORT_ESCAPES[cp] ? 2 : 6;
    return utf8LenCodePoint(cp);
  }

  function utf8Len(str) {
    var n = 0;
    for (var ch of str) n += utf8LenCodePoint(ch.codePointAt(0));
    return n;
  }

  function isLoneSurrogate(cp) {
    return cp >= 0xd800 && cp <= 0xdfff;
  }

  function envelopeText(r, m, i, n, p) {
    // 键序与 Python _envelope_text 一致；JSON.stringify 与 json.dumps(ensure_ascii=False, 紧凑) 同输出
    return JSON.stringify({ v: ENVELOPE_VERSION, r: r, m: m, i: i, n: n, p: p });
  }

  function isInt(v) {
    return typeof v === 'number' && Number.isInteger(v);
  }

  function isU32(v) {
    return isInt(v) && v >= 0 && v <= U32_MAX;
  }

  /**
   * 把一条 payload JSON 文本切成信封分片（字符串），每片 UTF-8 ≤ maxBytes。
   * 与 visit_wire.fragment 逐字节相同：按转义后长度贪心切、不切码点，
   * 信封开销按最终片数的位宽计算。
   */
  function fragment(payloadJson, opts) {
    var visitId = opts && opts.visitId;
    var msgId = opts && opts.msgId;
    var maxBytes = (opts && opts.maxBytes) || PIECE_MAX_BYTES;
    if (typeof visitId !== 'string' || !VISIT_ID_RE.test(visitId)) throw new Error('malformed visit_id');
    if (!isU32(msgId)) throw new Error('msg_id must be a u32');
    if (typeof payloadJson !== 'string') throw new Error('payload_json must be str');
    var r = visitId.slice(0, 8);
    var chars = [];
    var weights = [];
    for (var ch of payloadJson) {
      var cp = ch.codePointAt(0);
      if (isLoneSurrogate(cp)) throw new Error('text is not valid Unicode (lone surrogate)');
      chars.push(ch);
      weights.push(esc1Len(cp));
    }
    var bounds = null;
    for (var width = 1; width <= 3; width++) {
      var cap = Math.pow(10, width) - 1;
      var overhead = utf8Len(envelopeText(r, msgId, cap, cap, ''));
      var budget = maxBytes - overhead;
      if (budget < 6) throw new Error('max_bytes too small for the envelope');
      bounds = greedyBounds(weights, budget);
      if (bounds.length <= cap) break;
      bounds = null;
    }
    if (!bounds) throw new Error('payload too large to fragment');
    var n = bounds.length;
    var pieces = [];
    for (var i = 0; i < n; i++) {
      var text = envelopeText(r, msgId, i, n, chars.slice(bounds[i][0], bounds[i][1]).join(''));
      if (utf8Len(text) > maxBytes) throw new Error('fragment exceeded max_bytes');
      pieces.push(text);
    }
    return pieces;
  }

  function greedyBounds(weights, budget) {
    var bounds = [];
    var start = 0;
    var used = 0;
    for (var idx = 0; idx < weights.length; idx++) {
      if (used + weights[idx] > budget) {
        bounds.push([start, idx]);
        start = idx;
        used = 0;
      }
      used += weights[idx];
    }
    bounds.push([start, weights.length]);
    return bounds;
  }

  // ── 重组（与 visit_wire.Reassembler 同一规则）───────────────────────────

  function Reassembler(opts) {
    var visitId = opts && opts.visitId;
    if (typeof visitId !== 'string' || !VISIT_ID_RE.test(visitId)) throw new Error('malformed visit_id');
    this._r = visitId.slice(0, 8);
    this._timeoutMs = (opts && opts.timeoutMs) || REASSEMBLY_TIMEOUT_MS;
    this._maxEntries = (opts && opts.maxEntries) || REASSEMBLY_MAX_ENTRIES;
    this._maxPieces = (opts && opts.maxPieces) || PIECES_MAX;
    this._maxPieceBytes = (opts && opts.maxPieceBytes) || PIECE_MAX_BYTES;
    this._entries = new Map();
    this.dropped = 0;
    this.timeouts = 0;
    this.evicted = 0;
    this.duplicates = 0;
  }

  Reassembler.prototype.pending = function () {
    return this._entries.size;
  };

  Reassembler.prototype.sweep = function (nowMs) {
    var expired = 0;
    var self = this;
    this._entries.forEach(function (entry, key) {
      if (nowMs - entry.firstAt > self._timeoutMs) {
        self._entries.delete(key);
        expired++;
      }
    });
    this.dropped += expired;
    this.timeouts += expired;
    return expired;
  };

  Reassembler.prototype._parse = function (frag) {
    var text;
    var size;
    if (typeof frag === 'string') {
      text = frag;
      size = utf8Len(frag);
    } else {
      var bytes = frag instanceof Uint8Array ? frag : new Uint8Array(frag);
      size = bytes.byteLength;
      if (size > this._maxPieceBytes) return null;
      try {
        text = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
      } catch (_) {
        return null;
      }
    }
    if (size > this._maxPieceBytes) return null;
    var env;
    try {
      env = JSON.parse(text);
    } catch (_) {
      return null;
    }
    if (!env || typeof env !== 'object' || Array.isArray(env)) return null;
    if (env.v !== ENVELOPE_VERSION || env.r !== this._r) return null;
    if (!isU32(env.m) || !isInt(env.n) || !isInt(env.i) || typeof env.p !== 'string') return null;
    if (env.n < 1 || env.n > this._maxPieces || env.i < 0 || env.i >= env.n) return null;
    return env;
  };

  /** 收一片；整条消息齐了返回 payload 对象，否则 null。 */
  Reassembler.prototype.feed = function (fromVid, frag, nowMs) {
    this.sweep(nowMs);
    var env = this._parse(frag);
    if (!env) {
      this.dropped++;
      return null;
    }
    if (env.n === 1) return this._finish(env.p);
    var key = fromVid + ':' + env.m;
    var entry = this._entries.get(key);
    if (!entry) {
      while (this._entries.size >= this._maxEntries) {
        var oldest = this._entries.keys().next().value;
        this._entries.delete(oldest);
        this.evicted++;
        this.dropped++;
      }
      entry = { n: env.n, pieces: new Array(env.n).fill(null), firstAt: nowMs };
      this._entries.set(key, entry);
    } else if (entry.n !== env.n) {
      this._entries.delete(key);
      this.dropped++;
      return null;
    }
    if (entry.pieces[env.i] !== null) {
      this.duplicates++;
      return null;
    }
    entry.pieces[env.i] = env.p;
    if (entry.pieces.indexOf(null) !== -1) return null;
    this._entries.delete(key);
    return this._finish(entry.pieces.join(''));
  };

  Reassembler.prototype._finish = function (joined) {
    var obj;
    try {
      obj = JSON.parse(joined);
    } catch (_) {
      this.dropped++;
      return null;
    }
    if (!obj || typeof obj !== 'object' || Array.isArray(obj)) {
      this.dropped++;
      return null;
    }
    return obj;
  };

  // ── 出站节奏（1 s 滑动窗口）─────────────────────────────────────────────

  function isDroppable(cmd, payload) {
    return cmd === 3 || (cmd === 2 && payload && payload.t === 'line_delta');
  }

  /**
   * 纯逻辑的出站限流器：enqueue 一条已分片的消息，pump(now) 返回此刻可发的分片。
   * 任意 1 s 滑动窗口内实际发出的分片字节 ≤ windowBytes、片数 ≤ windowPieces。
   * 必达消息（带 seq 的 cmd 1 / 2）按 (cmd, seq) 去重：同一条还没发完时，后端的重传不再入队。
   */
  function SendPacer(opts) {
    opts = opts || {};
    this.windowMs = opts.windowMs || WINDOW_MS;
    this.windowBytes = opts.windowBytes || WINDOW_BYTES;
    this.windowPieces = opts.windowPieces || WINDOW_PIECES;
    this.queueMax = opts.queueMax || QUEUE_MAX;
    this.droppableMaxAgeMs = opts.droppableMaxAgeMs || DROPPABLE_MAX_AGE_MS;
    this.queue = [];          // {cmd, pieces:[{text, bytes}], next, droppable, key, at}
    this.sent = [];           // 窗口内已发分片 {at, bytes}
    this.droppedMessages = 0;
    this.dedupedRetransmits = 0;
  }

  SendPacer.prototype._prune = function (nowMs) {
    while (this.sent.length && nowMs - this.sent[0].at >= this.windowMs) this.sent.shift();
  };

  SendPacer.prototype._windowUse = function () {
    var bytes = 0;
    for (var i = 0; i < this.sent.length; i++) bytes += this.sent[i].bytes;
    return { bytes: bytes, pieces: this.sent.length };
  };

  /** 入队；返回 false 表示这条被去重或作废。 */
  SendPacer.prototype.enqueue = function (msg, nowMs) {
    var key = null;
    if ((msg.cmd === 1 || msg.cmd === 2) && msg.payload && isInt(msg.payload.seq)) {
      key = msg.cmd + ':' + msg.payload.seq;
      for (var i = 0; i < this.queue.length; i++) {
        if (this.queue[i].key === key) {
          this.dedupedRetransmits++;
          return false;
        }
      }
    }
    var droppable = isDroppable(msg.cmd, msg.payload);
    this._expire(nowMs);
    if (this.queue.length >= this.queueMax) {
      if (droppable) {
        this.droppedMessages++;
        return false;
      }
      // 必达类永不丢：先挤掉队里最老的可丢消息（没有就照样入队，队列只因必达类超出上限）
      for (var j = 0; j < this.queue.length; j++) {
        if (this.queue[j].droppable && this.queue[j].next === 0) {
          this.queue.splice(j, 1);
          this.droppedMessages++;
          break;
        }
      }
    }
    this.queue.push({
      cmd: msg.cmd,
      pieces: msg.pieces,
      next: 0,
      droppable: droppable,
      key: key,
      at: nowMs,
    });
    return true;
  };

  SendPacer.prototype._expire = function (nowMs) {
    for (var i = this.queue.length - 1; i >= 0; i--) {
      var item = this.queue[i];
      if (item.droppable && item.next === 0 && nowMs - item.at > this.droppableMaxAgeMs) {
        this.queue.splice(i, 1);
        this.droppedMessages++;
      }
    }
  };

  /** 返回此刻可以发出的分片 [{cmd, text, bytes, last}]，并记入窗口。 */
  SendPacer.prototype.pump = function (nowMs) {
    this._prune(nowMs);
    this._expire(nowMs);
    var out = [];
    var use = this._windowUse();
    while (this.queue.length) {
      var item = this.queue[0];
      var piece = item.pieces[item.next];
      if (use.pieces + 1 > this.windowPieces || use.bytes + piece.bytes > this.windowBytes) break;
      use.pieces += 1;
      use.bytes += piece.bytes;
      this.sent.push({ at: nowMs, bytes: piece.bytes });
      item.next += 1;
      var last = item.next >= item.pieces.length;
      out.push({ cmd: item.cmd, text: piece.text, bytes: piece.bytes, data: piece.data, last: last });
      if (last) this.queue.shift();
    }
    return out;
  };

  /** 下一次可能有分片可发的时刻（ms）；队列空返回 null。 */
  SendPacer.prototype.nextWakeMs = function (nowMs) {
    if (!this.queue.length) return null;
    this._prune(nowMs);
    if (!this.sent.length) return nowMs;
    return this.sent[0].at + this.windowMs;
  };

  SendPacer.prototype.oldestAgeMs = function (nowMs) {
    return this.queue.length ? nowMs - this.queue[0].at : 0;
  };

  SendPacer.prototype.clear = function () {
    this.queue = [];
  };

  // ── TransportCore：vendor 无关的一层 ─────────────────────────────────────

  /**
   * adapter 必须实现：join(creds) / leave(opts) / publish(track, profile) / unpublish() /
   * updateProfile(profile) / setSubscribe(on, peerVid) / sendPiece(cmd, bytes, opts) / stats() /
   * destroy()，并通过 handlers 回调 onPiece(fromVid, cmd, bytes) / onPeer(kind, vid, reason) /
   * onState(state, extra) / onRemoteTrack(track) / onNetwork(info)。
   */
  function TransportCore(opts) {
    this.visitId = opts.visitId;
    this.adapter = null;
    this.reassembler = new Reassembler({ visitId: opts.visitId });
    this.pacer = new SendPacer(opts.pacer);
    this.now = opts.now || function () { return Date.now(); };
    this.schedule = opts.schedule || function (fn, ms) { return setTimeout(fn, ms); };
    this.cancel = opts.cancel || function (h) { clearTimeout(h); };
    this.onRecv = opts.onRecv || function () {};
    this.onBackpressure = opts.onBackpressure || function () {};
    this.msgId = Math.floor(Math.random() * 0x7fffffff);
    this.peerVid = null;
    this.timer = null;
    this.backpressure = false;
    this.droppedUnknownCmd = 0;
    this.droppedFlood = 0;
    this.recvWindows = {};
  }

  /** 按发送者记 1 s 滑动窗口里收到的片；超预算返回 false（这片丢掉、不进重组）。 */
  TransportCore.prototype._chargePiece = function (fromVid, bytes, now) {
    var w = this.recvWindows[fromVid];
    if (!w) {
      var keys = Object.keys(this.recvWindows);
      if (keys.length >= RECV_TRACKED_SENDERS) delete this.recvWindows[keys[0]];
      w = this.recvWindows[fromVid] = { items: [], bytes: 0 };
    }
    while (w.items.length && now - w.items[0].at >= WINDOW_MS) w.bytes -= w.items.shift().bytes;
    var size = bytes && typeof bytes.byteLength === 'number' ? bytes.byteLength : 0;
    if (w.items.length + 1 > RECV_WINDOW_PIECES || w.bytes + size > RECV_WINDOW_BYTES) {
      this.droppedFlood++;
      return false;
    }
    w.items.push({ at: now, bytes: size });
    w.bytes += size;
    return true;
  };

  TransportCore.prototype.attach = function (adapter) {
    this.adapter = adapter;
  };

  TransportCore.prototype.setPeerVid = function (vid) {
    if (typeof vid === 'string' && vid) this.peerVid = vid;
  };

  /** 后端 send{cmd, payload}：序列化、分片、入队、按窗口发。 */
  TransportCore.prototype.send = function (cmd, payload) {
    if (!CMDS[cmd] || !payload || typeof payload !== 'object') return false;
    this.msgId = (this.msgId + 1) % (U32_MAX + 1);
    var texts;
    try {
      texts = fragment(JSON.stringify(payload), { visitId: this.visitId, msgId: this.msgId });
    } catch (_) {
      return false;
    }
    var enc = new TextEncoder();
    var pieces = texts.map(function (t) {
      var b = enc.encode(t);
      return { text: t, bytes: b.byteLength, data: b };
    });
    var accepted = this.pacer.enqueue({ cmd: cmd, payload: payload, pieces: pieces }, this.now());
    this.flush();
    return accepted;
  };

  TransportCore.prototype.flush = function () {
    if (this.timer !== null) {
      this.cancel(this.timer);
      this.timer = null;
    }
    var now = this.now();
    var ready = this.adapter && this.adapter.canSend && this.adapter.canSend();
    if (ready) {
      var out = this.pacer.pump(now);
      for (var i = 0; i < out.length; i++) {
        var piece = out[i];
        var data = piece.data || new TextEncoder().encode(piece.text);
        try {
          this.adapter.sendPiece(piece.cmd, data, { reliable: piece.cmd !== 3, topic: CMDS[piece.cmd], peerVid: this.peerVid });
        } catch (_) {
          // vendor 拒收（超限 / 未入房）：可丢消息丢了就丢了，必达消息由后端 outbox 重传
        }
      }
    }
    this._updateBackpressure(now);
    var wake = this.pacer.nextWakeMs(now);
    if (wake !== null) {
      var self = this;
      this.timer = this.schedule(function () {
        self.timer = null;
        self.flush();
      }, Math.max(ready ? wake - now : 250, 10));
    }
  };

  TransportCore.prototype._updateBackpressure = function (now) {
    var on = this.pacer.queue.length > 0 && this.pacer.oldestAgeMs(now) > BACKPRESSURE_ON_AGE_MS;
    if (!on && this.backpressure && this.pacer.queue.length > 0) on = true;
    if (on !== this.backpressure) {
      this.backpressure = on;
      this.onBackpressure({ on: on, queue: this.pacer.queue.length, dropped: this.pacer.droppedMessages > 0 });
    }
  };

  /** vendor 收到一片：校 cmd、重组、交后端 recv。返回重组出的 payload（测试用）。 */
  TransportCore.prototype.onPiece = function (fromVid, cmd, bytes) {
    if (!CMDS[cmd] || typeof fromVid !== 'string' || !fromVid) {
      this.droppedUnknownCmd++;
      return null;
    }
    var now = this.now();
    if (!this._chargePiece(fromVid, bytes, now)) return null;
    var payload = this.reassembler.feed(fromVid, bytes, now);
    if (payload) this.onRecv({ from_vid: fromVid, cmd: cmd, payload: payload });
    return payload;
  };

  TransportCore.prototype.queueLength = function () {
    return this.pacer.queue.length;
  };

  TransportCore.prototype.reset = function () {
    if (this.timer !== null) this.cancel(this.timer);
    this.timer = null;
    this.pacer.clear();
    // 队列清空，背压标志一起复位：下一次积压才能再报出 on:true 的上升沿。之前报过 on:true 的
    // 要显式补一个下降沿——传输 WS 没断（如同一页面里手动重新入房）时不会再有握手去清后端的标志
    if (this.backpressure) {
      this.backpressure = false;
      this.onBackpressure({ on: false, queue: 0, dropped: false });
    }
  };

  NVT.utf8Len = utf8Len;
  NVT.fragment = fragment;
  NVT.Reassembler = Reassembler;
  NVT.SendPacer = SendPacer;
  NVT.TransportCore = TransportCore;
  NVT.cmdForTopic = function (topic) {
    for (var k in CMDS) if (CMDS[k] === topic) return Number(k);
    return 0;
  };
})(typeof window !== 'undefined' ? window : globalThis);
