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

  function open(url) {
    if (!url) return;
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
    frame.src = url;
    frame.setAttribute('allow', 'clipboard-read; clipboard-write');

    win.appendChild(bar);
    win.appendChild(frame);
    backdrop.appendChild(win);
    document.body.appendChild(backdrop);
    document.addEventListener('keydown', onKey, true);
  }

  window.openSocialEmbed = open;
  window.closeSocialEmbed = close;
})();
