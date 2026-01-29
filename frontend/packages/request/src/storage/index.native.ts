import { Platform } from "react-native";
import webStorage from "./webStorage";
import nativeStorage from "./nativeStorage";
import type { Storage } from "./types";

/**
 * React Native 环境的存储实现
 * 使用静态导入 Platform 进行平台检测
 */
const storage: Storage = Platform.OS !== "web" ? nativeStorage : webStorage;

export default storage;
export type { Storage };

