// 全局类型声明文件
// 用于声明来自 public/static/ 目录下 JS 文件的全局变量和函数

interface Window {
  // API 相关
  buildApiUrl?: (path: string) => string;
  fetchWithBaseUrl?: (url: string, options?: RequestInit) => Promise<Response>;
  API_BASE_URL?: string;

  // 配置相关
  lanlan_config?: {
    lanlan_name: string;
  };
  cubism4Model?: string;
  focus_mode?: boolean;
  pageConfigReady?: Promise<boolean>;

  // 菜单跟踪
  activeMenuCount?: number;
  markMenuOpen?: () => void;
  markMenuClosed?: () => void;

  // Live2D 相关
  live2dManager?: {
    getCurrentModel: () => any;
    loadModel: (path: string, options?: any) => Promise<void>;
    loadUserPreferences: () => Promise<any[]>;
    getEmotionMapping: () => any;
    modelRootPath?: string;
  };
  LanLan1?: {
    live2dModel?: any;
    currentModel?: any;
    emotionMapping?: any;
  };
  PIXI?: any;

  // 应用初始化函数
  showStatusToast?: (message: string, duration?: number) => void;
}

// 声明全局变量（可以直接访问，不需要 window.）
declare var live2dManager: Window["live2dManager"];
declare var LanLan1: Window["LanLan1"];
declare var PIXI: any;
declare var lanlan_config: Window["lanlan_config"];
declare var cubism4Model: string | undefined;
declare var focus_mode: boolean | undefined;
declare var activeMenuCount: number | undefined;

// 全局函数声明
declare function showStatusToast(message: string, duration?: number): void;

