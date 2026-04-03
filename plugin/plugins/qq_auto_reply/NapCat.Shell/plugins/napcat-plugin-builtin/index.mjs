import fs from 'fs';
import path from 'path';

var EventType = /* @__PURE__ */ ((EventType2) => {
  EventType2["META"] = "meta_event";
  EventType2["REQUEST"] = "request";
  EventType2["NOTICE"] = "notice";
  EventType2["MESSAGE"] = "message";
  EventType2["MESSAGE_SENT"] = "message_sent";
  return EventType2;
})(EventType || {});

const startTime = Date.now();
let logger = null;
let currentConfig = {
  prefix: "#napcat",
  enableReply: true,
  description: "这是一个内置插件的配置示例"
};
let plugin_config_ui = [];
const plugin_init = async (ctx) => {
  logger = ctx.logger;
  logger.info("NapCat 内置插件已初始化");
  plugin_config_ui = ctx.NapCatConfig.combine(
    ctx.NapCatConfig.html('<div style="padding: 10px; background: rgba(0,0,0,0.05); border-radius: 8px;"><h3>👋 Welcome to NapCat Builtin Plugin</h3><p>This is a demonstration of the plugin configuration interface with reactive fields.</p></div>'),
    ctx.NapCatConfig.text("prefix", "Command Prefix", "#napcat", "The prefix to trigger the version info command"),
    ctx.NapCatConfig.boolean("enableReply", "Enable Reply", true, "Switch to enable or disable the reply functionality"),
    // 代表监听 apiUrl 字段的变化
    ctx.NapCatConfig.text("apiUrl", "API URL", "", "Enter an API URL to load available endpoints", true),
    ctx.NapCatConfig.select("theme", "Theme Selection", [
      { label: "Light Mode", value: "light" },
      { label: "Dark Mode", value: "dark" },
      { label: "Auto", value: "auto" }
    ], "light", "Select a theme for the response (Demo purpose only)"),
    ctx.NapCatConfig.multiSelect("features", "Enabled Features", [
      { label: "Version Info", value: "version" },
      { label: "Status Report", value: "status" },
      { label: "Debug Log", value: "debug" }
    ], ["version"], "Select features to enable"),
    ctx.NapCatConfig.text("description", "Description", "这是一个内置插件的配置示例", "A multi-line text area for notes")
  );
  try {
    if (fs.existsSync(ctx.configPath)) {
      const savedConfig = JSON.parse(fs.readFileSync(ctx.configPath, "utf-8"));
      Object.assign(currentConfig, savedConfig);
    }
  } catch (e) {
    logger?.warn("Failed to load config", e);
  }
  ctx.router.static("/static", "webui");
  ctx.router.staticOnMem("/dynamic", [
    {
      path: "/info.json",
      contentType: "application/json",
      // 使用生成器函数动态生成内容
      content: () => JSON.stringify({
        pluginName: ctx.pluginName,
        generatedAt: (/* @__PURE__ */ new Date()).toISOString(),
        uptime: Date.now() - startTime,
        config: currentConfig
      }, null, 2)
    },
    {
      path: "/readme.txt",
      contentType: "text/plain",
      content: `NapCat Builtin Plugin
=====================
This is a demonstration of the staticOnMem feature.
Plugin: ${ctx.pluginName}
Path: ${ctx.pluginPath}`
    }
  ]);
  ctx.router.get("/status", (_req, res) => {
    const uptime = Date.now() - startTime;
    res.json({
      code: 0,
      data: {
        pluginName: ctx.pluginName,
        uptime,
        uptimeFormatted: formatUptime(uptime),
        config: currentConfig,
        platform: process.platform,
        arch: process.arch
      }
    });
  });
  ctx.router.get("/config", (_req, res) => {
    res.json({
      code: 0,
      data: currentConfig
    });
  });
  ctx.router.post("/config", (req, res) => {
    try {
      const newConfig = req.body;
      Object.assign(currentConfig, newConfig);
      const configDir = path.dirname(ctx.configPath);
      if (!fs.existsSync(configDir)) {
        fs.mkdirSync(configDir, { recursive: true });
      }
      fs.writeFileSync(ctx.configPath, JSON.stringify(currentConfig, null, 2), "utf-8");
      res.json({ code: 0, message: "Config saved successfully" });
    } catch (e) {
      res.status(500).json({ code: -1, message: e.message });
    }
  });
  ctx.router.getNoAuth("/public/info", (_req, res) => {
    const uptime = Date.now() - startTime;
    res.json({
      code: 0,
      data: {
        pluginName: ctx.pluginName,
        uptime,
        uptimeFormatted: formatUptime(uptime),
        platform: process.platform
      }
    });
  });
  ctx.router.getNoAuth("/health", (_req, res) => {
    res.json({
      code: 0,
      data: {
        status: "ok",
        timestamp: (/* @__PURE__ */ new Date()).toISOString()
      }
    });
  });
  ctx.router.get("/call-plugin/:pluginId", (req, res) => {
    const { pluginId } = req.params;
    if (!pluginId) {
      res.status(400).json({
        code: -1,
        message: "Plugin ID is required"
      });
      return;
    }
    const targetPlugin = ctx.getPluginExports(pluginId);
    if (!targetPlugin) {
      res.status(404).json({
        code: -1,
        message: `Plugin '${pluginId}' not found or not loaded`
      });
      return;
    }
    res.json({
      code: 0,
      data: {
        pluginId,
        hasInit: typeof targetPlugin.plugin_init === "function",
        hasOnMessage: typeof targetPlugin.plugin_onmessage === "function",
        hasOnEvent: typeof targetPlugin.plugin_onevent === "function",
        hasCleanup: typeof targetPlugin.plugin_cleanup === "function",
        hasConfigSchema: Array.isArray(targetPlugin.plugin_config_schema),
        hasConfigUI: Array.isArray(targetPlugin.plugin_config_ui)
      }
    });
  });
  ctx.router.page({
    path: "dashboard",
    title: "插件仪表盘",
    icon: "📊",
    htmlFile: "webui/dashboard.html",
    description: "查看内置插件的运行状态和配置"
  });
  logger.info("WebUI 路由已注册:");
  logger.info("  - API 路由(需认证): /api/Plugin/ext/" + ctx.pluginName + "/");
  logger.info("  - API 路由(无认证): /plugin/" + ctx.pluginName + "/api/");
  logger.info("  - 扩展页面: /plugin/" + ctx.pluginName + "/page/dashboard");
  logger.info("  - 静态资源: /plugin/" + ctx.pluginName + "/files/static/");
  logger.info("  - 内存资源: /plugin/" + ctx.pluginName + "/mem/dynamic/");
};
const plugin_get_config = async () => {
  return currentConfig;
};
const plugin_set_config = async (ctx, config) => {
  currentConfig = config;
  if (ctx && ctx.configPath) {
    try {
      const configPath = ctx.configPath;
      const configDir = path.dirname(configPath);
      if (!fs.existsSync(configDir)) {
        fs.mkdirSync(configDir, { recursive: true });
      }
      fs.writeFileSync(configPath, JSON.stringify(config, null, 2), "utf-8");
    } catch (e) {
      logger?.error("Failed to save config", e);
      throw e;
    }
  }
};
const plugin_config_controller = async (_ctx, ui, initialConfig) => {
  logger?.info("配置控制器已初始化", initialConfig);
  if (initialConfig["apiUrl"]) {
    await loadEndpointsForUrl(ui, initialConfig["apiUrl"]);
  }
  return () => {
    logger?.info("配置控制器已清理");
  };
};
const plugin_on_config_change = async (_ctx, ui, key, value, _currentConfig) => {
  logger?.info(`配置字段变化: ${key} = ${value}`);
  if (key === "apiUrl") {
    await loadEndpointsForUrl(ui, value);
  }
};
async function loadEndpointsForUrl(ui, apiUrl) {
  if (!apiUrl) {
    ui.removeField("apiEndpoints");
    return;
  }
  const mockEndpoints = [
    { label: `${apiUrl}/users`, value: "/users" },
    { label: `${apiUrl}/posts`, value: "/posts" },
    { label: `${apiUrl}/comments`, value: "/comments" },
    { label: `${apiUrl}/albums`, value: "/albums" }
  ];
  const currentSchema = ui.getCurrentConfig();
  if ("apiEndpoints" in currentSchema) {
    ui.updateField("apiEndpoints", {
      options: mockEndpoints,
      description: `从 ${apiUrl} 加载的端点`
    });
  } else {
    ui.addField({
      key: "apiEndpoints",
      type: "multi-select",
      label: "API Endpoints",
      description: `从 ${apiUrl} 加载的端点`,
      options: mockEndpoints,
      default: []
    }, "apiUrl");
  }
}
const plugin_onmessage = async (_ctx, event) => {
  if (currentConfig.enableReply === false) {
    return;
  }
  const prefix = currentConfig.prefix || "#napcat";
  if (event.post_type !== EventType.MESSAGE || !event.raw_message.startsWith(prefix)) {
    return;
  }
  try {
    const versionInfo = await getVersionInfo(_ctx.actions, _ctx.adapterName, _ctx.pluginManager.config);
    if (!versionInfo) return;
    const message = formatVersionMessage(versionInfo);
    await sendMessage(_ctx.actions, event, message, _ctx.adapterName, _ctx.pluginManager.config);
    logger?.info("已回复版本信息");
  } catch (error) {
    logger?.error("处理消息时发生错误:", error);
  }
};
async function getVersionInfo(actions, adapter, config) {
  if (!actions) return null;
  try {
    const data = await actions.call("get_version_info", void 0, adapter, config);
    return {
      appName: data.app_name,
      appVersion: data.app_version,
      protocolVersion: data.protocol_version
    };
  } catch (error) {
    logger?.error("获取版本信息失败:", error);
    return null;
  }
}
function formatUptime(ms) {
  const seconds = Math.floor(ms / 1e3);
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);
  if (days > 0) {
    return `${days}天 ${hours % 24}小时 ${minutes % 60}分钟`;
  } else if (hours > 0) {
    return `${hours}小时 ${minutes % 60}分钟`;
  } else if (minutes > 0) {
    return `${minutes}分钟 ${seconds % 60}秒`;
  } else {
    return `${seconds}秒`;
  }
}
function formatVersionMessage(info) {
  const uptime = Date.now() - startTime;
  return `NapCat 信息
版本: ${info.appVersion}
平台: ${process.platform}${process.arch === "x64" ? " (64-bit)" : ""}
运行时间: ${formatUptime(uptime)}`;
}
async function sendMessage(actions, event, message, adapter, config) {
  const params = {
    message,
    message_type: event.message_type,
    ...event.message_type === "group" && event.group_id ? { group_id: String(event.group_id) } : {},
    ...event.message_type === "private" && event.user_id ? { user_id: String(event.user_id) } : {}
  };
  try {
    await actions.call("send_msg", params, adapter, config);
  } catch (error) {
    logger?.error("发送消息失败:", error);
  }
}

export { plugin_config_controller, plugin_config_ui, plugin_get_config, plugin_init, plugin_on_config_change, plugin_onmessage, plugin_set_config };
