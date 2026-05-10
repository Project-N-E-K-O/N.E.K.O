const UI_BUNDLES = {
  "zh-CN": {
    "ui.page.title": "喵喵控制中心",
    "ui.onboarding.step1.title": "启动 NapCat",
    "ui.onboarding.step1.desc": "先启动 NapCat，让本地 QQ OneBot 服务进入可连接状态。",
    "ui.onboarding.step1.next": "下一步",
    "ui.onboarding.step1.skip": "跳过引导",
    "ui.onboarding.step2.title": "配置 NapCat 服务",
    "ui.onboarding.step2.desc": "填写 OneBot 地址和访问令牌，只要这两项不为空就视为已完成。",
    "ui.onboarding.step2.next": "下一步",
    "ui.onboarding.step2.skip": "跳过引导",
    "ui.onboarding.step3.title": "添加联系人",
    "ui.onboarding.step3.desc": "至少添加一个信任用户；检测到 trusted_users 不为空时就显示已完成。",
    "ui.onboarding.step3.next": "下一步",
    "ui.onboarding.step3.skip": "跳过引导",
    "ui.onboarding.step4.title": "启动自动回复",
    "ui.onboarding.step4.desc": "点击启用自动回复后，该步骤会写入配置并显示为已完成。",
    "ui.onboarding.step4.next": "完成引导",
    "ui.guide.title": "快速引导",
    "ui.guide.replay": "重新播放引导",
    "ui.guide.summary": "按顺序完成启动 NapCat、配置 NapCat 服务、添加联系人、启动自动回复。",
    "ui.guide.completed": "已完成",
    "ui.guide.pending": "未完成",
    "ui.guide.step1.index": "STEP 1",
    "ui.guide.step1.title": "启动 NapCat",
    "ui.guide.step1.desc": "先启动 NapCat，让本地 QQ OneBot 服务进入可连接状态。",
    "ui.guide.step2.index": "STEP 2",
    "ui.guide.step2.title": "配置 NapCat 服务",
    "ui.guide.step2.desc": "填写 OneBot 地址和访问令牌，只要这两项不为空就视为已完成。",
    "ui.guide.step3.index": "STEP 3",
    "ui.guide.step3.title": "添加联系人",
    "ui.guide.step3.desc": "至少添加一个信任用户；检测到 trusted_users 不为空时就显示已完成。",
    "ui.guide.step4.index": "STEP 4",
    "ui.guide.step4.title": "启动自动回复",
    "ui.guide.step4.desc": "点击启用自动回复后，该步骤会写入配置并显示为已完成。",
    "ui.guide.step4.done_title": "停止自动回复",
    "ui.guide.step4.done_desc": "点击后会停止自动回复，并把该步骤切回未完成状态。",
    "ui.status.title": "运行状态",
    "ui.status.loading": "加载中",
    "ui.status.napcat_pid": "NapCat PID",
    "ui.status.onebot": "OneBot",
    "ui.status.users": "信任用户",
    "ui.status.groups": "信任群聊",
    "ui.status.connected": "已连接",
    "ui.status.disconnected": "未连接",
    "ui.status.online": "在线",
    "ui.status.offline": "离线",
    "ui.status.error": "异常",
    "ui.config.section": "OneBot 服务配置",
    "ui.config.url.label": "通信地址 (URL)",
    "ui.config.url.placeholder": "ws://127.0.0.1:3001",
    "ui.config.token.label": "访问令牌 (TOKEN)",
    "ui.config.token.placeholder": "请输入 Token...",
    "ui.config.path.label": "执行目录 (PATH)",
    "ui.config.path.placeholder": "",
    "ui.config.show_napcat_window.label": "NapCat 启动窗口",
    "ui.config.show_napcat_window.hint": "勾选为前台启动（显示 NapCat 控制台），取消为后台启动。",
    "ui.probability.section": "回复概率设置",
    "ui.probability.summary": "这两个概率只影响未来消息的触发行为，取值范围为 0 到 1。",
    "ui.probability.normal.label": "普通转发概率",
    "ui.probability.normal.hint": "普通用户 / 普通群消息命中后，会按这个概率转发给主人。",
    "ui.probability.truth.label": "开放群回复概率",
    "ui.probability.truth.hint": "开放群里未 @ 机器人时，会按这个概率主动回复。",
    "ui.tabs.users": "小鱼干用户",
    "ui.tabs.groups": "猫圈群聊",
    "ui.actions.add": "添加",
    "ui.actions.cancel": "取消",
    "ui.actions.save": "保存",
    "ui.actions.save_settings": "保存设置",
    "ui.entity_form.user.title": "添加用户",
    "ui.entity_form.group.title": "添加群聊",
    "ui.entity_form.user.number": "用户号码",
    "ui.entity_form.group.number": "群聊号码",
    "ui.entity_form.level": "级别",
    "ui.entity_form.nickname": "昵称",
    "ui.entity_form.required": "请先填写号码",
    "ui.empty.no_items": "暂无数据",
    "ui.defaults.user": "喵呜管理员",
    "ui.defaults.group": "核心猫草群",
    "ui.toast.saved": "设置已保存",
    "ui.toast.save_failed": "保存失败",
    "ui.toast.refreshed": "联系人已刷新",
    "ui.toast.refresh_failed": "刷新失败",
    "ui.toast.load_failed": "加载失败",
    "ui.toast.start_napcat_manual": "请先手动启动 NapCat，再回到这里继续配置。",
    "ui.toast.started": "自动回复已启动",
    "ui.toast.stopped": "自动回复已停止",
    "ui.toast.start_failed": "启动失败",
    "ui.qrcode.title": "登录二维码",
    "ui.qrcode.hint": "启动 NapCat 后会在 NapCat.Shell\\cache\\qrcode.png 生成登录二维码。",
    "ui.qrcode.empty": "暂未检测到二维码，请先启动 NapCat。"
  },
  "en-US": {
    "ui.page.title": "Meow Control Center",
    "ui.onboarding.step1.title": "Start NapCat",
    "ui.onboarding.step1.desc": "Start NapCat first so the local QQ OneBot service becomes reachable.",
    "ui.onboarding.step1.next": "Next",
    "ui.onboarding.step1.skip": "Skip Guide",
    "ui.onboarding.step2.title": "Configure NapCat Service",
    "ui.onboarding.step2.desc": "Fill in the OneBot URL and access token. This step is complete when both are non-empty.",
    "ui.onboarding.step2.next": "Next",
    "ui.onboarding.step2.skip": "Skip Guide",
    "ui.onboarding.step3.title": "Add Contacts",
    "ui.onboarding.step3.desc": "Add at least one trusted user. This step is complete when trusted_users is not empty.",
    "ui.onboarding.step3.next": "Next",
    "ui.onboarding.step3.skip": "Skip Guide",
    "ui.onboarding.step4.title": "Start Auto Reply",
    "ui.onboarding.step4.desc": "After enabling auto reply, this step will be saved as completed.",
    "ui.onboarding.step4.next": "Finish Guide",
    "ui.guide.title": "Quick Guide",
    "ui.guide.replay": "Replay Guide",
    "ui.guide.summary": "Complete Start NapCat, Configure NapCat Service, Add Contacts, and Start Auto Reply in order.",
    "ui.guide.completed": "Completed",
    "ui.guide.pending": "Pending",
    "ui.guide.step1.index": "STEP 1",
    "ui.guide.step1.title": "Start NapCat",
    "ui.guide.step1.desc": "Start NapCat first so the local QQ OneBot service becomes reachable.",
    "ui.guide.step2.index": "STEP 2",
    "ui.guide.step2.title": "Configure NapCat Service",
    "ui.guide.step2.desc": "Fill in the OneBot URL and access token. This step is complete when both are non-empty.",
    "ui.guide.step3.index": "STEP 3",
    "ui.guide.step3.title": "Add Contacts",
    "ui.guide.step3.desc": "Add at least one trusted user. This step is complete when trusted_users is not empty.",
    "ui.guide.step4.index": "STEP 4",
    "ui.guide.step4.title": "Start Auto Reply",
    "ui.guide.step4.desc": "After enabling auto reply, this step will be saved as completed.",
    "ui.guide.step4.done_title": "Stop Auto Reply",
    "ui.guide.step4.done_desc": "Click to stop auto reply and switch this step back to pending.",
    "ui.status.title": "Runtime Status",
    "ui.status.loading": "Loading",
    "ui.status.napcat_pid": "NapCat PID",
    "ui.status.onebot": "OneBot",
    "ui.status.users": "Trusted Users",
    "ui.status.groups": "Trusted Groups",
    "ui.status.connected": "Connected",
    "ui.status.disconnected": "Disconnected",
    "ui.status.online": "Online",
    "ui.status.offline": "Offline",
    "ui.status.error": "Error",
    "ui.config.section": "OneBot Service Settings",
    "ui.config.url.label": "Connection URL",
    "ui.config.url.placeholder": "ws://127.0.0.1:3001",
    "ui.config.token.label": "Access Token",
    "ui.config.token.placeholder": "Enter token...",
    "ui.config.path.label": "Execution Directory (PATH)",
    "ui.config.path.placeholder": "",
    "ui.config.show_napcat_window.label": "NapCat Launch Window",
    "ui.config.show_napcat_window.hint": "Checked = foreground launch with NapCat console. Unchecked = background launch.",
    "ui.probability.section": "Reply Probability Settings",
    "ui.probability.summary": "These two probabilities only affect future message triggers and must be between 0 and 1.",
    "ui.probability.normal.label": "Normal Relay Probability",
    "ui.probability.normal.hint": "Messages from normal users / normal groups will be relayed to the owner with this probability.",
    "ui.probability.truth.label": "Open Group Reply Probability",
    "ui.probability.truth.hint": "In open groups, messages not @mentioning the bot will trigger a proactive reply with this probability.",
    "ui.tabs.users": "Trusted Users",
    "ui.tabs.groups": "Group Chats",
    "ui.actions.add": "Add",
    "ui.actions.cancel": "Cancel",
    "ui.actions.save": "Save",
    "ui.actions.save_settings": "Save Settings",
    "ui.entity_form.user.title": "Add User",
    "ui.entity_form.group.title": "Add Group",
    "ui.entity_form.user.number": "User Number",
    "ui.entity_form.group.number": "Group Number",
    "ui.entity_form.level": "Level",
    "ui.entity_form.nickname": "Nickname",
    "ui.entity_form.required": "Please fill in the number first",
    "ui.empty.no_items": "No data",
    "ui.defaults.user": "Meow Admin",
    "ui.defaults.group": "Core Catnip Group",
    "ui.toast.saved": "Settings saved",
    "ui.toast.save_failed": "Save failed",
    "ui.toast.refreshed": "Contacts refreshed",
    "ui.toast.refresh_failed": "Refresh failed",
    "ui.toast.load_failed": "Load failed",
    "ui.toast.start_napcat_manual": "Please start NapCat manually first, then come back here to continue configuration.",
    "ui.toast.started": "Auto reply started",
    "ui.toast.stopped": "Auto reply stopped",
    "ui.toast.start_failed": "Start failed",
    "ui.qrcode.title": "Login QR Code",
    "ui.qrcode.hint": "After NapCat starts, the login QR code will be generated at NapCat.Shell\\cache\\qrcode.png.",
    "ui.qrcode.empty": "QR code not detected yet. Please start NapCat first."
  }
};

const I18n = {
  _bundle: {},
  _lang: 'zh-CN',
  _pluginId: 'qq_auto_reply',
  _ready: false,

  lang() {
    return this._lang;
  },

  whenReady(fn) {
    if (this._ready) {
      fn();
      return;
    }
    window.addEventListener('i18n-ready', () => fn(), { once: true });
  },

  _queryLocale() {
    try {
      return new URLSearchParams(location.search).get('locale') || '';
    } catch (err) {
      console.warn('Failed to read query locale', err);
      return '';
    }
  },

  _browserLocale() {
    const languages = (navigator.languages && navigator.languages.length)
      ? navigator.languages
      : [navigator.language];
    for (const lang of languages) {
      const raw = String(lang || '').trim();
      const lower = raw.toLowerCase().replace('_', '-');
      if (!lower) continue;
      if (lower === 'zh' || lower.startsWith('zh-')) return 'zh-CN';
      if (lower.startsWith('en')) return 'en-US';
    }
    return 'zh-CN';
  },

  _storageLocale() {
    try {
      const value = String(localStorage.getItem('locale') || '').trim();
      if (!value) return '';
      return value === 'auto' ? this._browserLocale() : value;
    } catch (err) {
      console.warn('Failed to read stored locale', err);
      return '';
    }
  },

  _normalizeLocale(locale) {
    const normalized = String(locale || '').trim().replace('_', '-').toLowerCase();
    if (normalized === 'zh' || normalized.startswith('zh-')) return 'zh-CN';
    if (normalized.startsWith('en')) return 'en-US';
    return 'zh-CN';
  },

  async init(pluginId) {
    this._ready = false;
    this._pluginId = pluginId || this._pluginId;
    const queryLocale = this._queryLocale();
    const storageLocale = this._storageLocale();
    let resolved = queryLocale || storageLocale || 'zh-CN';
    resolved = this._normalizeLocale(resolved);
    this._lang = resolved;
    this._bundle = UI_BUNDLES[resolved] || UI_BUNDLES['zh-CN'] || {};
    document.documentElement.lang = resolved;
    this._ready = true;
  },

  async refresh() {
    await this.init(this._pluginId);
    this.scanDOM();
    window.dispatchEvent(new CustomEvent('qq-auto-reply-i18n-refreshed', { detail: { locale: this._lang } }));
  },

  t(key, fallback) {
    const value = this._bundle[String(key || '')];
    return typeof value === 'string' && value ? value : (fallback || key);
  },

  scanDOM(root = document) {
    root.querySelectorAll('[data-i18n]').forEach((el) => {
      const key = el.getAttribute('data-i18n');
      if (key) {
        el.textContent = this.t(key, el.textContent);
      }
    });
    root.querySelectorAll('[data-i18n-placeholder]').forEach((el) => {
      const key = el.getAttribute('data-i18n-placeholder');
      if (key) {
        el.setAttribute('placeholder', this.t(key, el.getAttribute('placeholder') || ''));
      }
    });
  },
};

window.I18n = I18n;

(function bootstrapI18n() {
  const match = location.pathname.match(/\/plugin\/([^/]+)\/ui\//);
  const pluginId = match ? match[1] : 'qq_auto_reply';
  I18n.init(pluginId).then(() => {
    I18n.scanDOM();
    window.dispatchEvent(new CustomEvent('i18n-ready', { detail: { locale: I18n.lang() } }));
  });
  window.addEventListener('localechange', async () => {
    await I18n.refresh();
  });
})();
