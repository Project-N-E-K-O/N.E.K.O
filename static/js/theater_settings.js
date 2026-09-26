(function () {
  'use strict';
  var api = '/api/theater-numeric/options';
  var order = ['evaluator','review','dispute','review_delivery','review_contract','suggestion_fill','history_lookup','actor_retry'];
  var currentModules = null;
  var labels = {
    evaluator: '前置判定', review: '快速复核', dispute: '争议复查', review_delivery: '交付校验',
    review_contract: '边界校验', suggestion_fill: '补推荐', history_lookup: '历史查找', actor_retry: '输出重试'
  };
  var details = {
    evaluator: '判定数值变化、路线条件和是否满足转场。关闭后数值不再推进。',
    review: '复核玩家授权、动作归属、已公开去向和作者边界。',
    dispute: '对首次出现争议的判断追加一次独立复查。',
    review_delivery: '检查作者要求保留的关键道具是否在正文中交付。',
    review_contract: '检查是否提前到达后续阶段或违反作者禁令。',
    suggestion_fill: '推荐不足 2—3 条时，再请求一次模型补齐推荐。',
    history_lookup: '需要回忆旧事时查找当前 Session 的历史原文。',
    actor_retry: '演员输出不合格时自动重试一次，减少整轮回滚。'
  };
  var labelKeys = {
    evaluator: 'theater.moduleOpt.evaluator', review: 'theater.moduleOpt.review', dispute: 'theater.moduleOpt.dispute',
    review_delivery: 'theater.moduleOpt.review_delivery', review_contract: 'theater.moduleOpt.review_contract',
    suggestion_fill: 'theater.moduleOpt.suggestion_fill', history_lookup: 'theater.moduleOpt.history_lookup', actor_retry: 'theater.moduleOpt.actor_retry'
  };
  var detailKeys = {
    evaluator: 'theater.moduleOptDetail.evaluator', review: 'theater.moduleOptDetail.review', dispute: 'theater.moduleOptDetail.dispute',
    review_delivery: 'theater.moduleOptDetail.review_delivery', review_contract: 'theater.moduleOptDetail.review_contract',
    suggestion_fill: 'theater.moduleOptDetail.suggestion_fill', history_lookup: 'theater.moduleOptDetail.history_lookup', actor_retry: 'theater.moduleOptDetail.actor_retry'
  };
  function t(key, fallback) {
    if (typeof window.t === 'function') {
      var value = window.t(key);
      if (typeof value === 'string' && value && value !== key) return value;
    }
    return fallback;
  }
  function request(options) {
    var transport = window.nekoTheaterTransport;
    if (!transport || typeof transport.requestJson !== 'function') return Promise.reject(new Error('numeric_theater_transport_unavailable'));
    return transport.requestJson(api, options || {});
  }
  function feedback(text, error) {
    var node = document.getElementById('theater-settings-feedback');
    node.textContent = text || '';
    node.dataset.tone = error ? 'error' : 'ok';
  }
  function render(modules) {
    currentModules = modules || {};
    var host = document.getElementById('theater-settings-list'); host.textContent = '';
    order.forEach(function (key) {
      if (!Object.prototype.hasOwnProperty.call(currentModules, key)) return;
      var row = document.createElement('article'); row.className = 'theater-setting-row';
      var copy = document.createElement('div'); copy.className = 'theater-setting-copy';
      var heading = document.createElement('div'); heading.className = 'theater-setting-heading';
      var title = document.createElement('strong'); title.textContent = t(labelKeys[key], labels[key]);
      var tip = document.createElement('button'); tip.type = 'button'; tip.className = 'theater-setting-tip'; tip.textContent = '?'; tip.setAttribute('aria-label', t('theater.settingsTip', '查看说明'));
      var description = document.createElement('p'); description.textContent = t(detailKeys[key], details[key]); description.hidden = true;
      tip.addEventListener('click', function () { description.hidden = !description.hidden; });
      heading.append(title, tip); copy.append(heading, description);
      var label = document.createElement('label'); label.className = 'theater-switch'; label.setAttribute('aria-label', t(labelKeys[key], labels[key]));
      var input = document.createElement('input'); input.type = 'checkbox'; input.checked = currentModules[key] === true;
      var slider = document.createElement('span'); slider.className = 'theater-switch-slider'; label.append(input, slider);
      input.addEventListener('change', function () {
        var previous = currentModules[key] === true;
        input.disabled = true;
        var payload = {}; payload[key] = input.checked;
        request({method: 'POST', body: {modules: payload}}).then(function (data) {
          if (!data || !data.modules) throw new Error('numeric_theater_options_save_failed');
          render(data.modules);
          feedback(t('theater.settingsSaved', '设置已保存。'), false);
        }).catch(function () {
          input.checked = previous;
          input.disabled = false;
          feedback(t('theater.settingsSaveFailed', '设置保存失败，请重试。'), true);
        });
      });
      row.append(copy, label); host.appendChild(row);
    });
  }
  window.addEventListener('localechange', function () { if (currentModules) render(currentModules); });
  document.addEventListener('DOMContentLoaded', function () {
    document.getElementById('theater-settings-back').addEventListener('click', function () { window.location.href = '/theater'; });
    request().then(function (data) { if (!data || !data.modules) throw new Error('numeric_theater_options_load_failed'); render(data.modules); }).catch(function () { feedback(t('theater.settingsLoadFailed', '设置读取失败，请返回剧本库后重试。'), true); });
  });
})();
