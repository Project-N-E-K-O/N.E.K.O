/**
 * 统一导出文件
 * 根据环境自动选择 Web 或 Native 实现
 */

// 导出类型
export type {
  RequestClientConfig,
  TokenStorage,
  TokenRefreshFn,
  TokenRefreshResult,
  QueuedRequest
} from "./src/request-client/types";

// 导出创建函数
export { createRequestClient } from "./createClient";

// 导出 Token 存储实现（注意：默认入口必须保持 Web/SSR 安全，避免 Metro(Web) 解析 RN 依赖）
export { WebTokenStorage } from "./src/request-client/tokenStorage.web";

// 导出存储抽象
export { default as webStorage } from "./src/storage/webStorage";
export { default as storage } from "./src/storage/index";
export type { Storage } from "./src/storage/types";

