/**
 * 平台特定的存储实现
 *
 * 打包器会根据平台自动选择：
 * - React Native/Metro: index.native.ts (使用静态导入 Platform)
 * - Web/Vite: index.web.ts (直接使用 webStorage)
 *
 * 这个文件作为回退，如果打包器不支持平台特定解析，将使用 Web 实现
 */
import webStorage from "./webStorage";
import type { Storage } from "./types";

const storage: Storage = webStorage;

export default storage;
export type { Storage };

