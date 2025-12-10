import webStorage from "./webStorage";
import type { Storage } from "./types";

/**
 * Web 环境的存储实现
 * 使用浏览器原生的 localStorage
 */
const storage: Storage = webStorage;

export default storage;
export type { Storage };

