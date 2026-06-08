// ===== 猫娘社区「页内嵌入拟态窗」 =====
// 在应用内用 iframe 模态打开云端社区（默认 :8080），**不调系统浏览器 / 不开新标签页**。
// 由 static/app-ui.js 的 live2d-social-click handler 调用：window.openSocialEmbed(url)。
//
// ⚠️ NEKO 桌宠给 html/body 设 pointer-events:none 做桌面点击穿透；任何挂 body 下的 overlay
//    会继承到 → 鼠标点不动。故 backdrop / 窗 / iframe / 关闭按钮全部显式 pointer-events:auto
//    + 高 z-index（2000000），镜像 static/card-drop.js 的做法。
(function () {
  'use strict';

  var BACKDROP_ID = 'neko-social-embed-backdrop';

  function close() {
    var el = document.getElementById(BACKDROP_ID);
    if (el && el.parentNode) el.parentNode.removeChild(el);
    document.removeEventListener('keydown', onKey, true);
  }

  function onKey(e) {
    if (e.key === 'Escape') { e.stopPropagation(); close(); }
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
    // 点遮罩空白处（不是窗本身）关闭
    backdrop.addEventListener('mousedown', function (e) {
      if (e.target === backdrop) close();
    });

    var win = document.createElement('div');
    win.className = 'neko-social-embed-window';

    var bar = document.createElement('div');
    bar.className = 'neko-social-embed-titlebar';

    var title = document.createElement('div');
    title.className = 'neko-social-embed-title';
    var dot = document.createElement('span');
    dot.className = 'neko-social-embed-dot';
    title.appendChild(dot);
    title.appendChild(document.createTextNode('猫娘社区'));

    var closeBtn = document.createElement('button');
    closeBtn.className = 'neko-social-embed-close';
    closeBtn.type = 'button';
    closeBtn.setAttribute('aria-label', '关闭');
    closeBtn.textContent = '✕'; // ✕
    closeBtn.addEventListener('click', close);

    bar.appendChild(title);
    bar.appendChild(closeBtn);

    var frame = document.createElement('iframe');
    frame.className = 'neko-social-embed-iframe';
    frame.src = parsed.toString();
    frame.setAttribute('allow', 'clipboard-read; clipboard-write');
    // 沙箱限权：社区是云端独立站，只给它需要的最小权限（脚本/表单/自身 origin 的 storage-cookie/弹窗），
    // 挡掉顶层跳转等越权；referrer 不外泄。allow-same-origin 必需（社区在 iframe 内按自己 :8080 origin 登录）。
    frame.setAttribute('sandbox', 'allow-scripts allow-forms allow-same-origin allow-popups');
    frame.setAttribute('referrerpolicy', 'no-referrer');

    win.appendChild(bar);
    win.appendChild(frame);
    backdrop.appendChild(win);
    document.body.appendChild(backdrop);
    document.addEventListener('keydown', onKey, true);
    return true;
  }

  window.openSocialEmbed = open;
  window.closeSocialEmbed = close;
})();
