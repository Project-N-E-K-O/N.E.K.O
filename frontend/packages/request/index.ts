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

// 导出 Token 存储实现
export { WebTokenStorage, NativeTokenStorage } from "./src/request-client/tokenStorage";

// 导出存储抽象
export { default as webStorage } from "./src/storage/webStorage";
// nativeStorage 使用动态导入，避免在 Web 环境中加载 React Native 依赖
// 如需使用，请使用: const nativeStorage = await import('@project_neko/request').then(m => m.nativeStorage);
export { default as storage } from "./src/storage/index";
export type { Storage } from "./src/storage/types";

// 提供异步获取 nativeStorage 的函数
export async function getNativeStorage() {
  const module = await import("./src/storage/nativeStorage");
  return module.default;
}

