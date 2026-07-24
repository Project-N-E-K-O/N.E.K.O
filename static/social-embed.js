// ===== 猫娘社区「页内嵌入拟态窗」 =====
// 在应用内用 iframe 模态打开云端社区（默认 :8080），**不调系统浏览器 / 不开新标签页**。
// 由 static/app-ui.js 的 live2d-social-click handler 调用：window.openSocialEmbed(url)。
//
// ⚠️ NEKO 桌宠给 html/body 设 pointer-events:none 做桌面点击穿透；任何挂 body 下的 overlay
//    会继承到 → 鼠标点不动。故 backdrop / 窗 / iframe / 关闭按钮全部显式 pointer-events:auto
//    + 高 z-index（2000000）兜底。
(function () {
  'use strict';

  var BACKDROP_ID = 'neko-social-embed-backdrop';

  function close() {
    var el = document.getElementById(BACKDROP_ID);
    if (el && el.parentNode) el.parentNode.removeChild(el);
    document.removeEventListener('keydown', onKey, true);
    document.removeEventListener('pointermove', onDragMove, true);
    document.removeEventListener('pointerup', onDragEnd, true);
    document.removeEventListener('pointercancel', onDragEnd, true);
    document.removeEventListener('mouseup', onDragEnd, true);
    document.removeEventListener('pointermove', onResizeMove, true);
    document.removeEventListener('pointerup', onResizeEnd, true);
    document.removeEventListener('pointercancel', onResizeEnd, true);
    document.removeEventListener('mouseup', onResizeEnd, true);
    window.removeEventListener('pointerup', onDragEnd, true);
    window.removeEventListener('mouseup', onDragEnd, true);
    window.removeEventListener('pointerup', onResizeEnd, true);
    window.removeEventListener('mouseup', onResizeEnd, true);
    window.removeEventListener('blur', onDragEnd, true);
    window.removeEventListener('blur', onResizeEnd, true);
    window.removeEventListener('resize', onResize, true);
    restoreDragHitTesting();
    restoreResizeHitTesting();
    dragState = null;
    resizeState = null;
  }

  var dragState = null;
  var resizeState = null;
  var RESIZE_HANDLES = ['n', 'e', 's', 'w', 'ne', 'nw', 'se', 'sw'];

  function clamp(value, min, max) {
    return Math.min(Math.max(value, min), max);
  }

  function keepWindowInViewport(win) {
    var margin = 12;
    var rect = win.getBoundingClientRect();
    win.style.left = clamp(rect.left, margin, Math.max(margin, window.innerWidth - rect.width - margin)) + 'px';
    win.style.top = clamp(rect.top, margin, Math.max(margin, window.innerHeight - rect.height - margin)) + 'px';
    win.style.width = Math.min(rect.width, Math.max(320, window.innerWidth - margin * 2)) + 'px';
    win.style.height = Math.min(rect.height, Math.max(260, window.innerHeight - margin * 2)) + 'px';
  }

  function onDragMove(e) {
    if (!dragState) return;
    if (dragState.pointerId != null && e.pointerId != null && e.pointerId !== dragState.pointerId) return;
    if (e.buttons === 0 && e.pointerType !== 'touch') {
      onDragEnd(e);
      return;
    }
    e.preventDefault();
    var maxLeft = Math.max(dragState.margin, window.innerWidth - dragState.width - dragState.margin);
    var maxTop = Math.max(dragState.margin, window.innerHeight - dragState.height - dragState.margin);
    dragState.win.style.left = clamp(dragState.startLeft + e.clientX - dragState.startX, dragState.margin, maxLeft) + 'px';
    dragState.win.style.top = clamp(dragState.startTop + e.clientY - dragState.startY, dragState.margin, maxTop) + 'px';
  }

  function restoreDragHitTesting() {
    if (!dragState) return;
    if (dragState.frame) dragState.frame.style.pointerEvents = dragState.prevFramePointerEvents || '';
    if (dragState.bar && dragState.pointerId != null && typeof dragState.bar.releasePointerCapture === 'function') {
      try { dragState.bar.releasePointerCapture(dragState.pointerId); } catch (_) { /* pointer already released */ }
    }
  }

  function onDragEnd(e) {
    if (dragState && e && dragState.pointerId != null && e.pointerId != null && e.pointerId !== dragState.pointerId) return;
    if (dragState && dragState.bar) dragState.bar.classList.remove('is-dragging');
    restoreDragHitTesting();
    document.removeEventListener('pointermove', onDragMove, true);
    document.removeEventListener('pointerup', onDragEnd, true);
    document.removeEventListener('pointercancel', onDragEnd, true);
    document.removeEventListener('mouseup', onDragEnd, true);
    window.removeEventListener('pointerup', onDragEnd, true);
    window.removeEventListener('mouseup', onDragEnd, true);
    window.removeEventListener('blur', onDragEnd, true);
    dragState = null;
  }

  function startDrag(e, win, bar) {
    if (e.button !== 0 || e.target.closest('.neko-social-embed-close')) return;
    if (dragState) onDragEnd();
    var rect = win.getBoundingClientRect();
    var frame = win.querySelector('.neko-social-embed-iframe');
    dragState = {
      win: win,
      bar: bar,
      frame: frame,
      prevFramePointerEvents: frame ? frame.style.pointerEvents : '',
      pointerId: e.pointerId,
      startX: e.clientX,
      startY: e.clientY,
      startLeft: rect.left,
      startTop: rect.top,
      width: rect.width,
      height: rect.height,
      margin: 12
    };
    win.style.left = rect.left + 'px';
    win.style.top = rect.top + 'px';
    if (frame) frame.style.pointerEvents = 'none';
    if (typeof bar.setPointerCapture === 'function' && e.pointerId != null) {
      try { bar.setPointerCapture(e.pointerId); } catch (_) { /* ignore unsupported capture */ }
    }
    bar.classList.add('is-dragging');
    document.addEventListener('pointermove', onDragMove, true);
    document.addEventListener('pointerup', onDragEnd, true);
    document.addEventListener('pointercancel', onDragEnd, true);
    document.addEventListener('mouseup', onDragEnd, true);
    window.addEventListener('pointerup', onDragEnd, true);
    window.addEventListener('mouseup', onDragEnd, true);
    window.addEventListener('blur', onDragEnd, true);
    e.preventDefault();
  }

  function getResizeMinSize() {
    return {
      width: Math.min(520, Math.max(320, window.innerWidth - 24)),
      height: Math.min(420, Math.max(260, window.innerHeight - 24))
    };
  }

  function restoreResizeHitTesting() {
    if (!resizeState) return;
    if (resizeState.frame) resizeState.frame.style.pointerEvents = resizeState.prevFramePointerEvents || '';
    if (resizeState.handle && resizeState.pointerId != null && typeof resizeState.handle.releasePointerCapture === 'function') {
      try { resizeState.handle.releasePointerCapture(resizeState.pointerId); } catch (_) { /* pointer already released */ }
    }
  }

  function onResizeMove(e) {
    if (!resizeState) return;
    if (resizeState.pointerId != null && e.pointerId != null && e.pointerId !== resizeState.pointerId) return;
    if (e.buttons === 0 && e.pointerType !== 'touch') {
      onResizeEnd(e);
      return;
    }
    e.preventDefault();

    var edge = resizeState.edge;
    var dx = e.clientX - resizeState.startX;
    var dy = e.clientY - resizeState.startY;
    var min = getResizeMinSize();
    var margin = resizeState.margin;
    var viewportRight = window.innerWidth - margin;
    var viewportBottom = window.innerHeight - margin;
    var left = resizeState.startLeft;
    var top = resizeState.startTop;
    var width = resizeState.startWidth;
    var height = resizeState.startHeight;
    var right = resizeState.startLeft + resizeState.startWidth;
    var bottom = resizeState.startTop + resizeState.startHeight;

    if (edge.indexOf('e') !== -1) {
      width = clamp(resizeState.startWidth + dx, min.width, viewportRight - left);
    }
    if (edge.indexOf('s') !== -1) {
      height = clamp(resizeState.startHeight + dy, min.height, viewportBottom - top);
    }
    if (edge.indexOf('w') !== -1) {
      left = clamp(resizeState.startLeft + dx, margin, right - min.width);
      width = right - left;
    }
    if (edge.indexOf('n') !== -1) {
      top = clamp(resizeState.startTop + dy, margin, bottom - min.height);
      height = bottom - top;
    }

    resizeState.win.style.left = left + 'px';
    resizeState.win.style.top = top + 'px';
    resizeState.win.style.width = width + 'px';
    resizeState.win.style.height = height + 'px';
  }

  function onResizeEnd(e) {
    if (resizeState && e && resizeState.pointerId != null && e.pointerId != null && e.pointerId !== resizeState.pointerId) return;
    if (resizeState && resizeState.win) resizeState.win.classList.remove('is-resizing');
    restoreResizeHitTesting();
    document.removeEventListener('pointermove', onResizeMove, true);
    document.removeEventListener('pointerup', onResizeEnd, true);
    document.removeEventListener('pointercancel', onResizeEnd, true);
    document.removeEventListener('mouseup', onResizeEnd, true);
    window.removeEventListener('pointerup', onResizeEnd, true);
    window.removeEventListener('mouseup', onResizeEnd, true);
    window.removeEventListener('blur', onResizeEnd, true);
    resizeState = null;
  }

  function startResize(e, win, handle, edge) {
    if (e.button !== 0) return;
    if (dragState) onDragEnd();
    if (resizeState) onResizeEnd();
    var rect = win.getBoundingClientRect();
    var frame = win.querySelector('.neko-social-embed-iframe');
    resizeState = {
      win: win,
      handle: handle,
      frame: frame,
      prevFramePointerEvents: frame ? frame.style.pointerEvents : '',
      pointerId: e.pointerId,
      edge: edge,
      startX: e.clientX,
      startY: e.clientY,
      startLeft: rect.left,
      startTop: rect.top,
      startWidth: rect.width,
      startHeight: rect.height,
      margin: 12
    };
    win.style.left = rect.left + 'px';
    win.style.top = rect.top + 'px';
    win.style.width = rect.width + 'px';
    win.style.height = rect.height + 'px';
    if (frame) frame.style.pointerEvents = 'none';
    if (typeof handle.setPointerCapture === 'function' && e.pointerId != null) {
      try { handle.setPointerCapture(e.pointerId); } catch (_) { /* ignore unsupported capture */ }
    }
    win.classList.add('is-resizing');
    document.addEventListener('pointermove', onResizeMove, true);
    document.addEventListener('pointerup', onResizeEnd, true);
    document.addEventListener('pointercancel', onResizeEnd, true);
    document.addEventListener('mouseup', onResizeEnd, true);
    window.addEventListener('pointerup', onResizeEnd, true);
    window.addEventListener('mouseup', onResizeEnd, true);
    window.addEventListener('blur', onResizeEnd, true);
    e.preventDefault();
  }

  function appendResizeHandles(win) {
    RESIZE_HANDLES.forEach(function (edge) {
      var handle = document.createElement('div');
      handle.className = 'neko-social-embed-resize-handle neko-social-embed-resize-' + edge;
      handle.setAttribute('aria-hidden', 'true');
      handle.addEventListener('pointerdown', function (e) { startResize(e, win, handle, edge); });
      win.appendChild(handle);
    });
  }

  function onResize() {
    var el = document.getElementById(BACKDROP_ID);
    var win = el && el.querySelector('.neko-social-embed-window');
    if (win) keepWindowInViewport(win);
  }

  function onKey(e) {
    if (e.key === 'Escape') { e.stopPropagation(); close(); }
  }

  function withCacheBust(url) {
    var busted = new URL(url.toString());
    busted.searchParams.set('_neko_embed_v', String(Date.now()));
    return busted.toString();
  }

  // 返回 true=已打开 / false=拒绝（URL 缺失/非法/非 http(s)）。调用方据此提示失败、不再当成功路径。
  function open(url) {
    if (!url) return false;
    // 安全：只放行 http/https，挡掉 javascript:/data: 等（social_base_url 来自 env，
    // 误配/被篡改时防把不可信页面高权限嵌进主界面）。
    var parsed;
    try {
      parsed = new URL(url, window.location.href);
    } catch (e) {
      console.warn('[social-embed] invalid url:', url);
      return false;
    }
    if (!/^https?:$/.test(parsed.protocol)) {
      console.warn('[social-embed] blocked non-http(s) url:', parsed.protocol);
      return false;
    }
    close(); // 已开则先关，避免叠加多个

    var backdrop = document.createElement('div');
    backdrop.id = BACKDROP_ID;
    backdrop.className = 'neko-social-embed-backdrop';

    var win = document.createElement('div');
    win.className = 'neko-social-embed-window';

    var bar = document.createElement('div');
    bar.className = 'neko-social-embed-titlebar';
    bar.addEventListener('pointerdown', function (e) { startDrag(e, win, bar); });

    var title = document.createElement('div');
    title.className = 'neko-social-embed-title';
    var dot = document.createElement('span');
    dot.className = 'neko-social-embed-dot';
    title.appendChild(dot);
    title.appendChild(document.createTextNode((window.t && window.t('buttons.social')) || 'Neko Community'));

    var closeBtn = document.createElement('button');
    closeBtn.className = 'neko-social-embed-close';
    closeBtn.type = 'button';
    closeBtn.setAttribute('aria-label', (window.t && window.t('common.close')) || 'Close');
    closeBtn.textContent = '✕'; // ✕
    closeBtn.addEventListener('click', close);

    bar.appendChild(title);
    bar.appendChild(closeBtn);

    var frame = document.createElement('iframe');
    frame.className = 'neko-social-embed-iframe';
    frame.src = withCacheBust(parsed);
    frame.setAttribute('allow', 'clipboard-read; clipboard-write');
    // 沙箱限权：社区是云端独立站，只给它需要的最小权限（脚本/表单/自身 origin 的 storage-cookie/弹窗），
    // 挡掉顶层跳转等越权；referrer 不外泄。allow-same-origin 必需（社区在 iframe 内按自己 :8080 origin 登录）。
    frame.setAttribute('sandbox', 'allow-scripts allow-forms allow-same-origin allow-popups');
    frame.setAttribute('referrerpolicy', 'no-referrer');

    win.appendChild(bar);
    win.appendChild(frame);
    appendResizeHandles(win);
    backdrop.appendChild(win);
    document.body.appendChild(backdrop);
    keepWindowInViewport(win);
    window.addEventListener('resize', onResize, true);
    document.addEventListener('keydown', onKey, true);
    return true;
  }

  window.openSocialEmbed = open;
  window.closeSocialEmbed = close;
})();
