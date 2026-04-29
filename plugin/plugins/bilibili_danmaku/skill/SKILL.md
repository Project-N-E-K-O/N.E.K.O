# N.E.K.O 插件模块设计原则（自动遵循）

## 一、基础架构（强制）
1. **类装饰器**：必须使用 `@neko_plugin`，类继承 `NekoPluginBase`。
2. **生命周期**：至少实现 `@lifecycle(id="startup")` 和 `@lifecycle(id="shutdown")`，返回 `Ok` / `Err`。
3. **入口点**：使用 `@plugin_entry` 装饰，必须包含 `**_` 参数，返回 `Ok(...)` 或 `Err(SdkError(...))`，禁止直接返回 dict 或抛异常。
4. **Result 类型**：所有跨插件调用、可能失败的内部操作都返回 `Ok` / `Err`，消费时用 `isinstance(result, Ok)` 或 `unwrap_or`。

## 二、代码风格（硬性）
5. **对称性**：多个同类组件（如不同 TTS provider）必须保持目录结构和处理路径对称，不得出现数字后缀（如 `_2`）。
6. **异步非阻塞**：在 async 函数中禁止同步文件 IO、同步 SQLite、同步 HTTP、CPU 密集循环、`time.sleep`。文件 IO 用 `atomic_write_*_async` / `read_json_async`；SQLite 用 `a*` 版本；HTTP 用 `httpx.AsyncClient`；CPU 密集用 `await asyncio.to_thread(...)`。
7. **日志规范**：使用 `self.logger` 输出调试/信息/警告。任何涉及用户原始对话的内容必须用 `print`，不得用 `logger`。
8. **i18n 同步**：修改用户可见字符串时，必须同时更新 `en.json, ja.json, ko.json, zh-CN.json, zh-TW.json, ru.json` 六个文件，缺一不可。

## 三、数据与存储
9. **持久化**：使用 `PluginStore` 做键值存储，或 `self.data_path()` 获取 `data/` 目录下的文件（如 `self.data_path("cache.db")`）。
10. **配置**：插件自身配置放在 `self.config_dir / "config.json"`，使用 `PluginConfig` 读取。

## 四、跨插件与事件
11. **跨插件调用**：通过 `self.plugins.call_entry("other_plugin:entry_id", args)`，必须检查返回的 `Err`。
12. **事件总线**：`self.bus.emit()` 发布事件，`self.bus.on()` 订阅（通常在 startup 中订阅）。
13. **定时任务**：使用 `@timer_interval`，注意共享状态加锁（`threading.Lock` 或 `asyncio.Lock`）。

## 五、目录与部署
14. **目录结构**：