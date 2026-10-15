/*
 * 串门取景几何（设计稿 §3.4.1）：纯函数，父页 parent-bridge.js 与 node 测试共用。
 *
 * upperBodyRect 与 avatar-portrait.js 的 makeUpperBodyRect 同一比例
 * （宽 = max(w×1.04, h×0.58×aspect)，高 = max(h×0.64, 宽/aspect)，中心 y = top + h×biasY）；
 * avatar-portrait.js 本身不动。fullBodyRect = 包围盒外扩 4% 再按目标宽高比 contain。
 * 矩形一律 {x, y, width, height}。
 */
(function (root) {
  'use strict';

  var HYSTERESIS = { centerPct: 4, sizePct: 8, transitionMs: 300 };   // VISIT_CROP_HYSTERESIS

  function toRect(r) {
    if (!r) return null;
    var x = Number(r.x !== undefined ? r.x : r.left);
    var y = Number(r.y !== undefined ? r.y : r.top);
    var w = Number(r.width !== undefined ? r.width : (Number(r.right) - x));
    var h = Number(r.height !== undefined ? r.height : (Number(r.bottom) - y));
    if (![x, y, w, h].every(isFinite) || w <= 0 || h <= 0) return null;
    return { x: x, y: y, width: w, height: h };
  }

  /** 两个矩形的交集；不相交返回 null。模型包围盒不按视口裁剪，取景前先与视口求交。 */
  function intersectRect(a, b) {
    var p = toRect(a);
    var q = toRect(b);
    if (!p || !q) return null;
    var x = Math.max(p.x, q.x);
    var y = Math.max(p.y, q.y);
    var r = Math.min(p.x + p.width, q.x + q.width);
    var bt = Math.min(p.y + p.height, q.y + q.height);
    if (r <= x || bt <= y) return null;
    return { x: x, y: y, width: r - x, height: bt - y };
  }

  function upperBodyRect(subjectRect, aspect, biasY) {
    var s = toRect(subjectRect);
    if (!s) return null;
    var a = Math.max(0.1, Number(aspect) || 1);
    var b = typeof biasY === 'number' ? biasY : 0.32;
    var w = Math.max(s.width * 1.04, s.height * 0.58 * a);
    var h = Math.max(s.height * 0.64, w / a);
    var cx = s.x + s.width / 2;
    var cy = s.y + s.height * b;
    return { x: cx - w / 2, y: cy - h / 2, width: w, height: h };
  }

  function fullBodyRect(bounds, aspect) {
    var s = toRect(bounds);
    if (!s) return null;
    var a = Math.max(0.1, Number(aspect) || 1);
    var w = s.width * 1.04;
    var h = s.height * 1.04;
    if (w / h > a) h = w / a;
    else w = h * a;
    var cx = s.x + s.width / 2;
    var cy = s.y + s.height / 2;
    return { x: cx - w / 2, y: cy - h / 2, width: w, height: h };
  }

  /**
   * 上半身取景的主体：模型包围盒；有可靠头部框时把顶边收到头顶上方 0.2 个头高
   * （去掉包围盒里头顶上方的空白），没有就退到包围盒本身（结果 = 包围盒上 64%）。
   */
  function upperBodySubject(bounds, headRect) {
    var b = toRect(bounds);
    if (!b) return null;
    var head = toRect(headRect);
    if (!head) return b;
    var top = Math.max(b.y, head.y - head.height * 0.2);
    var bottom = b.y + b.height;
    if (bottom - top < head.height * 2) return b;
    return { x: b.x, y: top, width: b.width, height: bottom - top };
  }

  /** CSS 视口矩形 → 画布后备缓冲像素矩形（对偶 avatar-portrait.js 的 cssRectToPixelRect）。 */
  function cssRectToPixelRect(rect, metrics) {
    var r = toRect(rect);
    if (!r || !metrics) return null;
    var ox = metrics.originX || 0;
    var oy = metrics.originY || 0;
    if (metrics.mirrorX && metrics.cssWidth > 0) {
      // 水平镜像显示：屏幕上的右边缘对应源图的左边缘，打包时再水平翻转回屏幕上的朝向
      return {
        x: Math.round((ox + metrics.cssWidth - r.x - r.width) * metrics.pixelRatioX),
        y: Math.round((r.y - oy) * metrics.pixelRatioY),
        w: Math.max(1, Math.round(r.width * metrics.pixelRatioX)),
        h: Math.max(1, Math.round(r.height * metrics.pixelRatioY)),
        flipX: true,
      };
    }
    return {
      x: Math.round((r.x - ox) * metrics.pixelRatioX),
      y: Math.round((r.y - oy) * metrics.pixelRatioY),
      w: Math.max(1, Math.round(r.width * metrics.pixelRatioX)),
      h: Math.max(1, Math.round(r.height * metrics.pixelRatioY)),
    };
  }

  /** 滞回：中心偏移 <4%（相对旧框尺寸）且尺寸变化 <8% 不动框。 */
  function shouldMove(prev, next, opts) {
    var p = toRect(prev);
    var n = toRect(next);
    if (!n) return false;
    if (!p) return true;
    var o = opts || HYSTERESIS;
    var dx = Math.abs((n.x + n.width / 2) - (p.x + p.width / 2)) / p.width * 100;
    var dy = Math.abs((n.y + n.height / 2) - (p.y + p.height / 2)) / p.height * 100;
    var dw = Math.abs(n.width - p.width) / p.width * 100;
    var dh = Math.abs(n.height - p.height) / p.height * 100;
    return dx >= o.centerPct || dy >= o.centerPct || dw >= o.sizePct || dh >= o.sizePct;
  }

  function lerpRect(a, b, t) {
    var k = Math.max(0, Math.min(1, t));
    return {
      x: a.x + (b.x - a.x) * k,
      y: a.y + (b.y - a.y) * k,
      width: a.width + (b.width - a.width) * k,
      height: a.height + (b.height - a.height) * k,
    };
  }

  root.NekoVisitCropGeometry = {
    HYSTERESIS: HYSTERESIS,
    toRect: toRect,
    intersectRect: intersectRect,
    upperBodyRect: upperBodyRect,
    upperBodySubject: upperBodySubject,
    fullBodyRect: fullBodyRect,
    cssRectToPixelRect: cssRectToPixelRect,
    shouldMove: shouldMove,
    lerpRect: lerpRect,
  };
})(typeof window !== 'undefined' ? window : globalThis);
