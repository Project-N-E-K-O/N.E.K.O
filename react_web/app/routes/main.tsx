import { useEffect, useRef } from "react";
import "./main.css";
import { buildApiUrl, fetchWithBaseUrl } from "../utils/api";
import { ExampleButton } from "../components/ExampleButton";

export function meta() {
  return [
    { title: "Project N.E.K.O." },
    { name: "description", content: "Lanlan Terminal" },
  ];
}

export function links() {
  const STATIC_SERVER_URL = (import.meta.env.VITE_STATIC_SERVER_URL as string) || "http://localhost:48911";
  
  return [
    { rel: "icon", type: "image/x-icon", href: `${STATIC_SERVER_URL}/static/favicon.ico` },
    { rel: "manifest", href: `${STATIC_SERVER_URL}/static/manifest.json` },
    { rel: "apple-touch-icon", href: `${STATIC_SERVER_URL}/static/xiaoba_192.png` },
  ];
}

export default function Main() {
  const scriptsLoadedRef = useRef(false);

  useEffect(() => {
    if (scriptsLoadedRef.current) return;
    scriptsLoadedRef.current = true;

    // API Base URL 配置
    // 可以通过环境变量或配置来设置，默认为空（使用相对路径）

    const STATIC_SERVER_URL = (import.meta.env.VITE_STATIC_SERVER_URL as string) || "http://localhost:48911";
    const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL as string) || "http://localhost:48911";

    // 全局错误处理：忽略 static 相关的加载错误（仅在开发环境）
    const originalConsoleError = console.error;
    const isDev = import.meta.env.DEV;
    
    if (isDev) {
      console.error = (...args: any[]) => {
        const errorMessage = args.join(' ');
        // 忽略包含 /static/ 的错误信息
        if (errorMessage.includes('/static/') || 
            (errorMessage.includes('static') && errorMessage.includes('Failed to load'))) {
          // 静默忽略，不输出到控制台
          return;
        }
        // 其他错误正常输出
        originalConsoleError.apply(console, args);
      };

      // 拦截全局错误事件，忽略 static 相关的资源加载错误
      const handleError = (event: ErrorEvent) => {
        const errorSource = event.filename || event.message || '';
        // 如果是 static 相关的错误，阻止默认行为（不显示在控制台）
        if (errorSource.includes('/static/') || 
            (errorSource.includes('static') && errorSource.includes('Failed to load'))) {
          event.preventDefault();
          event.stopPropagation();
          return false;
        }
        return true;
      };

      // 拦截资源加载错误
      const handleResourceError = (event: Event) => {
        const target = event.target as HTMLElement;
        if (target) {
          const src = (target as HTMLImageElement).src || 
                     (target as HTMLScriptElement).src || 
                     (target as HTMLLinkElement).href || '';
          // 如果是 static 相关的资源加载失败，静默处理
          if (src.includes('/static/')) {
            event.preventDefault();
            event.stopPropagation();
            return false;
          }
        }
        return true;
      };

      window.addEventListener('error', handleError, true);
      window.addEventListener('error', handleResourceError, true);

      // 保存清理函数引用
      (window as any).__staticErrorHandlers = {
        handleError,
        handleResourceError,
        originalConsoleError
      };
    }

    // 设置 CSS 变量供 CSS 文件使用
    document.documentElement.style.setProperty('--toast-background-url', `url('${STATIC_SERVER_URL}/static/icons/toast_background.png')`);

    // ============================================
    // React 中访问全局变量的多种方法：
    // ============================================
    // 1. 使用 window（最常用）
    //    window.live2dManager 或 (window as any).live2dManager
    //
    // 2. 使用 globalThis（ES2020 标准，浏览器中等于 window）
    //    globalThis.live2dManager
    //
    // 3. 直接访问（需要类型声明文件 global.d.ts）
    //    live2dManager（无需 window. 前缀）
    //
    // 4. document 可以直接访问，不需要通过 window
    //    document.getElementById() 或 window.document.getElementById()
    // ============================================

    // 构建静态资源 URL 的工具函数，供外部 JS 文件使用
    (window as any).buildStaticUrl = (path: string): string => {
      // 如果 path 已经是完整 URL，直接返回
      if (path.startsWith("http://") || path.startsWith("https://")) {
        return path;
      }
      // 确保 path 以 / 开头
      const cleanPath = path.startsWith("/") ? path : `/${path}`;
      // 移除 STATIC_SERVER_URL 末尾的 /，避免双斜杠
      const base = STATIC_SERVER_URL.replace(/\/$/, "");
      return `${base}${cleanPath}`;
    };

    // 设置全局变量，供外部 JS 文件使用
    (window as any).STATIC_SERVER_URL = STATIC_SERVER_URL;

    // 拦截机制：在外部 JS 文件加载之前，设置路径拦截
    // 拦截所有以 /static/ 开头的路径，自动转换为使用 STATIC_SERVER_URL 的完整路径
    (function() {
      // 辅助函数：转换静态资源路径
      const convertStaticPath = (value: string): string => {
        if (typeof value === 'string' && value.startsWith('/static/')) {
          return (window as any).buildStaticUrl(value);
        }
        return value;
      };

      // 拦截 HTMLImageElement 的 src 属性
      const originalSrcDescriptor = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, 'src');
      if (originalSrcDescriptor) {
        Object.defineProperty(HTMLImageElement.prototype, 'src', {
          set: function(value: string) {
            const convertedValue = convertStaticPath(value);
            if (originalSrcDescriptor.set) {
              originalSrcDescriptor.set.call(this, convertedValue);
            }
          },
          get: function() {
            if (originalSrcDescriptor.get) {
              return originalSrcDescriptor.get.call(this);
            }
            return this.getAttribute('src') || '';
          },
          configurable: true
        });
      }

      // 拦截 setAttribute 方法，处理 src 属性
      const originalSetAttribute = Element.prototype.setAttribute;
      Element.prototype.setAttribute = function(name: string, value: string) {
        if (name === 'src' && this instanceof HTMLImageElement) {
          value = convertStaticPath(value);
        }
        return originalSetAttribute.call(this, name, value);
      };

      // 拦截 cssText 属性设置（用于处理 style.cssText = "..." 的情况）
      const originalCssTextDescriptor = Object.getOwnPropertyDescriptor(CSSStyleDeclaration.prototype, 'cssText');
      if (originalCssTextDescriptor) {
        Object.defineProperty(CSSStyleDeclaration.prototype, 'cssText', {
          set: function(value: string) {
            if (typeof value === 'string') {
              // 替换 CSS 中的 url('/static/...') 路径
              value = value.replace(/url\(['"]?(\/static\/[^'"]+)['"]?\)/g, (match, path) => {
                return `url('${(window as any).buildStaticUrl(path)}')`;
              });
            }
            if (originalCssTextDescriptor.set) {
              originalCssTextDescriptor.set.call(this, value);
            }
          },
          get: function() {
            if (originalCssTextDescriptor.get) {
              return originalCssTextDescriptor.get.call(this);
            }
            return '';
          },
          configurable: true
        });
      }

      // 拦截 backgroundImage 等可能包含 url() 的 CSS 属性
      const cssPropertiesWithUrl = ['backgroundImage', 'background', 'listStyleImage', 'borderImageSource', 'cursor'];
      cssPropertiesWithUrl.forEach(prop => {
        const originalDescriptor = Object.getOwnPropertyDescriptor(CSSStyleDeclaration.prototype, prop);
        if (originalDescriptor) {
          Object.defineProperty(CSSStyleDeclaration.prototype, prop, {
            set: function(value: string) {
              if (typeof value === 'string') {
                // 替换 CSS 中的 url('/static/...') 路径
                value = value.replace(/url\(['"]?(\/static\/[^'"]+)['"]?\)/g, (match, path) => {
                  return `url('${(window as any).buildStaticUrl(path)}')`;
                });
              }
              if (originalDescriptor.set) {
                originalDescriptor.set.call(this, value);
              }
            },
            get: function() {
              if (originalDescriptor.get) {
                return originalDescriptor.get.call(this);
              }
              return '';
            },
            configurable: true
          });
        }
      });
    })();

    // 将工具函数暴露到全局，供 JS 文件使用（JS 文件暂时不改，但可以使用这些函数）
    // 方法1: 使用 window（当前方法）
    window.buildApiUrl = buildApiUrl;
    window.fetchWithBaseUrl = fetchWithBaseUrl;
    window.API_BASE_URL = API_BASE_URL;
    
    // 方法2: 使用 globalThis（等同于 window，但更标准）
    // globalThis.buildApiUrl = buildApiUrl;

    // 页面配置 - 从 URL 或 API 获取（初始化为空值）
    let lanlan_config = {
      lanlan_name: "",
    };
    window.lanlan_config = lanlan_config;
    let cubism4Model = "";
    
    // 异步获取页面配置
    async function loadPageConfig(): Promise<boolean> {
      try {
        // 优先从 URL 获取 lanlan_name
        const urlParams = new URLSearchParams(window.location.search);
        let lanlanNameFromUrl = urlParams.get("lanlan_name") || "";

        // 从路径中提取 lanlan_name (例如 /{lanlan_name})
        if (!lanlanNameFromUrl) {
          const pathParts = window.location.pathname.split("/").filter(Boolean);
          if (
            pathParts.length > 0 &&
            !["focus", "api", "static", "templates"].includes(pathParts[0])
          ) {
            lanlanNameFromUrl = decodeURIComponent(pathParts[0]);
          }
        }

        // 从 API 获取配置
        const apiUrl = lanlanNameFromUrl
          ? buildApiUrl(
              `/api/config/page_config?lanlan_name=${encodeURIComponent(
                lanlanNameFromUrl
              )}`
            )
          : buildApiUrl("/api/config/page_config");

        const response = await fetchWithBaseUrl(apiUrl);
        const data = await response.json();

        if (data.success) {
          // 使用 URL 中的 lanlan_name（如果有），否则使用 API 返回的
          lanlan_config.lanlan_name =
            lanlanNameFromUrl || data.lanlan_name || "";
          window.lanlan_config = lanlan_config;
          cubism4Model = data.model_path || "";
          window.cubism4Model = cubism4Model;

          // 动态设置页面标题
          document.title = `${lanlan_config.lanlan_name} Terminal - Project Lanlan`;

          console.log("页面配置加载成功:", {
            lanlan_name: lanlan_config.lanlan_name,
            model_path: cubism4Model,
          });
          return true;
        } else {
          console.error("获取页面配置失败:", data.error);
          // 使用默认值
          lanlan_config.lanlan_name = "";
          window.lanlan_config = lanlan_config;
          cubism4Model = "";
          window.cubism4Model = cubism4Model;
          return false;
        }
      } catch (error) {
        console.error("加载页面配置时出错:", error);
        // 使用默认值
        lanlan_config.lanlan_name = "";
        window.lanlan_config = lanlan_config;
        cubism4Model = "";
        window.cubism4Model = cubism4Model;
        return false;
      }
    }

    // 标记配置是否已加载
    (window as any).pageConfigReady = loadPageConfig();

    // 全局菜单跟踪机制
    // 方法1: 使用 window（类型安全）
    window.activeMenuCount = 0;
    window.markMenuOpen = function () {
      window.activeMenuCount = (window.activeMenuCount || 0) + 1;
    };
    window.markMenuClosed = function () {
      window.activeMenuCount = Math.max(
        0,
        (window.activeMenuCount || 0) - 1
      );
    };
    
    // 方法2: 使用 globalThis
    // globalThis.activeMenuCount = 0;
    
    // 方法3: 直接访问（如果已在 global.d.ts 中声明）
    // activeMenuCount = 0;

    // Beacon功能 - 页面关闭时发送信号给服务器
    let beaconSent = false;
    function sendBeacon() {
      if (beaconSent) return;
      beaconSent = true;

      try {
        const beaconUrl = buildApiUrl("/api/beacon/shutdown");
        const success = navigator.sendBeacon(
          beaconUrl,
          JSON.stringify({
            timestamp: Date.now(),
            action: "shutdown",
          })
        );

        if (success) {
          console.log("Beacon信号已发送");
        } else {
          console.warn("Beacon发送失败，尝试使用fetch");
          fetchWithBaseUrl("/api/beacon/shutdown", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              timestamp: Date.now(),
              action: "shutdown",
            }),
            keepalive: true,
          }).catch((err) => console.log("备用beacon发送失败:", err));
        }
      } catch (e) {
        console.log("Beacon发送异常:", e);
      }
    }

    window.addEventListener("beforeunload", sendBeacon);
    window.addEventListener("unload", sendBeacon);

    // 页面间通信：监听来自模型设置页面的消息
    async function handlePageMessage(event: StorageEvent) {
      if (event.key === "nekopage_message") {
        try {
          const message = JSON.parse(event.newValue || "{}");
          if (message && message.action) {
            switch (message.action) {
              case "hide_main_ui":
                console.log("接收到隐藏主界面命令");
                hideMainUI();
                break;
              case "show_main_ui":
                console.log("接收到显示主界面命令");
                await showMainUI();
                break;
            }
          }
        } catch (e) {
          console.log("解析页面消息失败:", e);
        }
      }
    }

    // 隐藏主界面的函数
    // document 可以直接访问，不需要通过 window
    // document === window.document（两者等价）
    function hideMainUI() {
      // 直接访问 document（推荐）
      const live2dContainer = document.getElementById("live2d-container");
      const chatContainer = document.getElementById("chat-container");
      
      // 或者通过 window.document（也可以，但不必要）
      // const live2dContainer = window.document.getElementById("live2d-container");

      if (live2dContainer) {
        live2dContainer.style.display = "none";
      }
      if (chatContainer) {
        chatContainer.style.display = "none";
      }

      document.body.setAttribute("data-ui-hidden", "true");
    }

    // 显示主界面的函数
    async function showMainUI() {
      console.log("显示主界面（弹出窗口已关闭）");

      const live2dContainer = document.getElementById("live2d-container");
      const chatContainer = document.getElementById("chat-container");

      if (live2dContainer) {
        live2dContainer.style.display = "";
      }
      if (chatContainer) {
        chatContainer.style.display = "";
      }

      document.body.removeAttribute("data-ui-hidden");

      // 检查模型是否需要重新加载
      try {
        console.log("检查模型配置是否有更新...");
        // 访问全局变量示例：
        // 方法1: window（类型安全，推荐）
        const currentLanlanName = window.lanlan_config?.lanlan_name;
        
        // 方法2: globalThis
        // const currentLanlanName = globalThis.lanlan_config?.lanlan_name;
        
        // 方法3: 直接访问（如果已在 global.d.ts 中声明）
        // const currentLanlanName = lanlan_config?.lanlan_name;
        const charResponse = await fetchWithBaseUrl("/api/characters");
        if (!charResponse.ok) {
          console.warn("获取角色配置失败，跳过模型检查");
          return;
        }
        const charactersData = await charResponse.json();
        if (!currentLanlanName) {
          console.warn("当前角色名称为空，跳过模型检查");
          return;
        }
        const catgirlConfig = charactersData["猫娘"]?.[currentLanlanName];
        if (!catgirlConfig) {
          console.warn(
            `未找到角色 ${currentLanlanName} 的配置，跳过模型检查`
          );
          return;
        }
        const newModelName = catgirlConfig.live2d || "mao_pro";
        const modelsResponse = await fetchWithBaseUrl("/api/live2d/models");
        if (!modelsResponse.ok) {
          console.warn("获取模型列表失败，跳过模型检查");
          return;
        }
        const models = await modelsResponse.json();
        const modelInfo = models.find((m: any) => m.name === newModelName);
        if (!modelInfo) {
          console.warn(`未找到模型 ${newModelName}，跳过模型检查`);
          return;
        }
        const newModelPath = modelInfo.path;
        // 访问全局对象示例：
        // 方法1: window（类型安全，推荐）
        const currentModel = window.live2dManager?.getCurrentModel();
        
        // 方法2: globalThis
        // const currentModel = globalThis.live2dManager?.getCurrentModel();
        
        // 方法3: 直接访问（如果已在 global.d.ts 中声明）
        // const currentModel = live2dManager?.getCurrentModel();
        let currentModelPath = "";
        if (currentModel && currentModel.url) {
          currentModelPath = currentModel.url;
        } else if (window.live2dManager?.modelRootPath) {
          currentModelPath = window.live2dManager.modelRootPath;
        }

        // 调用全局对象的方法
        const preferences = await window.live2dManager?.loadUserPreferences();
        let modelPreferences = null;
        if (preferences && preferences.length > 0) {
          modelPreferences = preferences.find(
            (p: any) => p && p.model_path === newModelPath
          );
        }

        const needReload =
          !currentModelPath ||
          !newModelPath.includes(
            currentModelPath.split("/").filter(Boolean).pop() || "___invalid___"
          );

        if (needReload) {
          console.log(`检测到模型变化，重新加载: ${newModelPath}`);
          await window.live2dManager?.loadModel(newModelPath, {
            preferences: modelPreferences,
            isMobile: window.innerWidth <= 768,
          });

          // 访问和设置全局对象属性（类型安全）
          if (window.LanLan1 && window.live2dManager) {
            window.LanLan1.live2dModel = window.live2dManager.getCurrentModel();
            window.LanLan1.currentModel = window.live2dManager.getCurrentModel();
            window.LanLan1.emotionMapping = window.live2dManager.getEmotionMapping();
          }

          console.log("模型已重新加载");
        } else {
          console.log("模型未改变，但重新应用用户偏好设置");
          if (modelPreferences && currentModel) {
            if (modelPreferences.position) {
              currentModel.x = modelPreferences.position.x || currentModel.x;
              currentModel.y = modelPreferences.position.y || currentModel.y;
              console.log("已应用位置设置:", modelPreferences.position);
            }
            if (modelPreferences.scale) {
              currentModel.scale.set(
                modelPreferences.scale.x || currentModel.scale.x,
                modelPreferences.scale.y || currentModel.scale.y
              );
              console.log("已应用缩放设置:", modelPreferences.scale);
            }
          }
        }
      } catch (error) {
        console.error("检查/重新加载模型时出错:", error);
      }
    }

    window.addEventListener("storage", handlePageMessage);
    window.addEventListener("beforeunload", () => {
      window.removeEventListener("storage", handlePageMessage);
    });

    // 按顺序加载外部脚本（有依赖关系）
    const loadScript = (src: string): Promise<void> => {
      return new Promise((resolve, reject) => {
        // 检查脚本是否已经加载
        const existingScript = document.querySelector(`script[src="${src}"]`);
        if (existingScript) {
          resolve();
          return;
        }

        const script = document.createElement("script");
        script.src = src;
        script.async = false; // 改为 false 以确保顺序执行
        script.onload = () => {
          console.log(`脚本加载完成: ${src}`);
          resolve();
        };
        script.onerror = () => {
          // 对于 static 相关的脚本加载失败，静默处理（不输出错误，也不 reject）
          if (src.includes('/static/')) {
            console.warn(`静态脚本加载失败（已忽略）: ${src}`);
            resolve(); // 静默成功，避免阻塞后续脚本加载
          } else {
            console.error(`脚本加载失败: ${src}`);
            reject(new Error(`Failed to load script: ${src}`));
          }
        };
        document.body.appendChild(script);
      });
    };

    // 设置 API_BASE_URL 供 api 拦截器使用（必须在拦截器脚本加载之前设置）
    (window as any).API_BASE_URL = API_BASE_URL;

    const scripts = [
      `${STATIC_SERVER_URL}/static/api_interceptor.js`, // API 拦截器（必须在最前面加载）
      `${STATIC_SERVER_URL}/static/libs/pixi.min.js`, // PixiJS v7
      `${STATIC_SERVER_URL}/static/libs/live2d.min.js`, // Cubism 2.1核心库
      `${STATIC_SERVER_URL}/static/libs/live2dcubismcore.min.js`, // Cubism 4核心库
      `${STATIC_SERVER_URL}/static/libs/index.min.js`, // pixi-live2d-display（依赖 PixiJS）
      `${STATIC_SERVER_URL}/static/live2d.js`, // 依赖前面的库
      `${STATIC_SERVER_URL}/static/common_ui.js`, // 依赖前面的库
      `${STATIC_SERVER_URL}/static/app.js`, // 依赖前面的库
    ];

    // 对话区提示框逻辑
    const initChatTooltip = () => {
      const chatTooltip = document.getElementById("chat-tooltip");
      const textInputBox = document.getElementById("textInputBox");
      const chatContainer = document.getElementById("chat-container");
      const toggleBtn = document.getElementById("toggle-chat-btn");
      let autoCollapseTimer: NodeJS.Timeout | null = null;

      const hasVisitedBefore = localStorage.getItem("chat_tooltip_shown");

      if (chatTooltip && textInputBox && chatContainer && toggleBtn) {
        if (!hasVisitedBefore) {
          chatTooltip.classList.remove("hidden");
          localStorage.setItem("chat_tooltip_shown", "true");
        } else {
          chatTooltip.classList.add("hidden");
        }

        const handleFocus = () => {
          if (autoCollapseTimer) {
            clearTimeout(autoCollapseTimer);
            autoCollapseTimer = null;
            console.log("用户聚焦文本框，取消自动折叠");
          }
          chatTooltip.classList.add("hidden");
          textInputBox.removeEventListener("focus", handleFocus);
        };

        const handleToggleClick = () => {
          if (autoCollapseTimer) {
            clearTimeout(autoCollapseTimer);
            autoCollapseTimer = null;
            console.log("用户手动折叠对话区，取消自动折叠");
          }
          chatTooltip.classList.add("hidden");
          toggleBtn.removeEventListener("click", handleToggleClick);
        };

        textInputBox.addEventListener("focus", handleFocus);
        toggleBtn.addEventListener("click", handleToggleClick);

        autoCollapseTimer = setTimeout(() => {
          // 访问全局变量（类型安全）
          if ((window.activeMenuCount || 0) > 0) {
            console.log("检测到活动菜单，跳过自动折叠");
            return;
          }

          chatTooltip.classList.add("hidden");
          setTimeout(() => {
            if ((window.activeMenuCount || 0) > 0) {
              console.log("检测到活动菜单，跳过自动折叠");
              return;
            }
            (toggleBtn as HTMLButtonElement).click();
            console.log("自动折叠对话区");
          }, 300);
        }, 5000);

        console.log("页面加载完成，5秒后将自动折叠对话区（除非有活动菜单）");
      }
    };

    // 等待配置加载完成后再加载脚本
    (async () => {
      try {
        // 等待配置加载完成
        await (window as any).pageConfigReady;

        // 按顺序加载所有脚本
        for (const src of scripts) {
          await loadScript(src);
        }
        console.log("所有脚本加载完成");

        // app.js 现在会在脚本加载时自动检查 DOM 状态并初始化
        // 如果 DOM 已经 ready，它会立即调用初始化函数
        // 如果 DOM 还没 ready，它会等待 DOMContentLoaded 或 load 事件
        console.log("脚本加载完成，app.js 将自动初始化（已修改为支持动态加载）");

        // 调用全局函数（类型安全）
        // 方法1: window.函数名()

        // 方法2: globalThis.函数名()
        // globalThis.initLive2DManager?.();

        // 方法3: 直接调用（如果已在 global.d.ts 中声明）
        // initLive2DManager();

        // 等待 DOM 完全准备好后再初始化对话区提示框
        const initAfterScripts = () => {
          setTimeout(initChatTooltip, 100);
        };

        if (document.readyState === "loading") {
          window.addEventListener("load", initAfterScripts);
        } else {
          initAfterScripts();
        }
      } catch (error) {
        console.error("脚本加载过程中出错:", error);
      }
    })();

    return () => {
      // 清理函数
      window.removeEventListener("beforeunload", sendBeacon);
      window.removeEventListener("unload", sendBeacon);
      // 移除错误处理监听器（如果存在）
      if ((window as any).__staticErrorHandlers) {
        const handlers = (window as any).__staticErrorHandlers;
        window.removeEventListener('error', handlers.handleError, true);
        window.removeEventListener('error', handlers.handleResourceError, true);
        // 恢复原始的 console.error
        console.error = handlers.originalConsoleError;
        delete (window as any).__staticErrorHandlers;
      }
    };
  }, []);

  useEffect(() => {
    // 设置 meta 标签
    const metaTags = [
      { name: "apple-mobile-web-app-capable", content: "yes" },
      { name: "apple-mobile-web-app-status-bar-style", content: "black" },
    ];

    metaTags.forEach(({ name, content }) => {
      let meta = document.querySelector(`meta[name="${name}"]`);
      if (!meta) {
        meta = document.createElement("meta");
        meta.setAttribute("name", name);
        document.head.appendChild(meta);
      }
      meta.setAttribute("content", content);
    });

    // 设置 body 和 html 样式
    document.documentElement.style.height = "100%";
    document.documentElement.style.margin = "0";
    document.documentElement.style.padding = "0";
    document.documentElement.style.background = "transparent";
    document.documentElement.style.overflow = "hidden";
    document.documentElement.style.pointerEvents = "none";

    document.body.style.height = "100%";
    document.body.style.margin = "0";
    document.body.style.padding = "0";
    document.body.style.background = "transparent";
    document.body.style.overflow = "hidden";
    document.body.style.pointerEvents = "none";

    return () => {
      // 清理函数
      metaTags.forEach(({ name }) => {
        const meta = document.querySelector(`meta[name="${name}"]`);
        if (meta) {
          meta.remove();
        }
      });
    };
  }, []);

  return (
    <div className="container">
        {/* ExampleButton 在左上角 */}
        <div style={{ 
          position: "fixed", 
          top: "20px", 
          left: "20px", 
          zIndex: 99998,
          pointerEvents: "auto"
        }}>
          <ExampleButton 
            buttonText="打开 Modal"
            onSave={(text1, text2) => {
              console.log("保存的内容:", text1, text2);
            }}
          />
        </div>

        {/* 旧的按钮面板 */}
        <div id="sidebar" style={{ display: "none" }}>
          <div id="sidebarbox">
            <button id="micButton" className="side-btn">
              🎤 开始语音
            </button>
            <button id="muteButton" className="side-btn" disabled>
              ⏸️ 休息一下
            </button>
            <button id="screenButton" className="side-btn" disabled>
              🖥️ 屏幕共享
            </button>
            <button id="stopButton" className="side-btn" disabled>
              🛑 停止共享
            </button>
            <button id="resetSessionButton" className="side-btn">
              👋 请她离开
            </button>
            <div id="status"></div>
          </div>
        </div>

        {/* Status 气泡框 */}
        <div id="status-toast"></div>

        {/* 聊天容器 */}
        <div id="chat-container">
          <div id="chat-header">
            <span id="chat-title">💬 对话</span>
          </div>
          <button id="toggle-chat-btn" title="最小化">
            —
          </button>
          <div id="chat-tooltip">✨ 对话区</div>
          <div id="chat-content-wrapper">
            <div id="chatContainer"></div>
          </div>
          <div id="text-input-area">
            <div id="screenshot-thumbnail-container">
              <div id="screenshots-header">
                <span id="screenshots-title">
                  📸 待发送截图 (<span id="screenshot-count">0</span>)
                </span>
                <button id="clear-all-screenshots">清空全部</button>
              </div>
              <div id="screenshots-list"></div>
            </div>
            <div id="text-input-row">
              <textarea
                id="textInputBox"
                placeholder="文字聊天模式...回车发送，Shift+回车换行"
                tabIndex={0}
              ></textarea>
              <div id="button-group">
                <button id="textSendButton">📤 发送</button>
                <button id="screenshotButton">
                  <span className="desktop-text">📸 截图</span>
                  <span className="mobile-text">📷 拍照</span>
                </button>
              </div>
            </div>
          </div>
        </div>

        {/* Live2D 容器 */}
        <div id="live2d-container">
          <canvas id="live2d-canvas"></canvas>
        </div>
    </div>
  );
}

