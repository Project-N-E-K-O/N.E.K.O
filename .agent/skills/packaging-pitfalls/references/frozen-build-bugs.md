# 冻结版才出现的问题

这一类问题的共同点：源码模式正常、单元测试全绿，只有 Nuitka 编出来的二进制出错。PR 的 CI 不编 Nuitka，所以只能靠 nightly、本地最小复现和静态守卫发现。

## Nuitka 4.3.1 丢返回值（#3491）

**症状**：冻结版里某个函数的返回值变成 `None`；同步函数还可能静默退出（退出码 0）或段错误。

**触发形状**：`try` 块里 `return`，同一个 `try` 的 `finally` 里含 `while` 循环。

```python
try:
    return value          # 返回值在这里被丢掉
finally:
    while not done():     # finally 里有 while
        ...
```

不受影响的写法：`finally` 里用 `for`；或者先把值存进变量，`try/finally` 结束后再 `return`。4.2.x 正常。

**现行规则**：

- `build-desktop.yml` 和 `build-desktop-linux.yml` 都锁 `nuitka==4.2.2`，本地打包脚本用同一个版本。三处一起改。
- 守卫 `tests/unit/test_no_return_through_looping_finally.py` 用 AST 扫描所有可能进包的 `.py`，发现这个形状就失败。被它拦下时按「先存值、后 return」改，`main_logic/voice_identity_service/resource_manager.py` 的 `_run_worker` 是现成的例子。
- 升级 Nuitka 之前，先用上面这个最小形状编一个小程序，确认新版已经修好；不要为了跟上新版直接解锁。

**怎么查**：冻结版里「返回值莫名其妙是 `None`」，而同一段代码源码模式没问题，先怀疑编译器，再怀疑业务逻辑。当时的现场是唤醒词冒烟拿 worker 结果做下标访问时报 `TypeError`。

## PyAV 的影子 `.py`（#3212）

**症状**：冻结版 import `av` 的子模块时报 `No module named 'cython'`。实测观察：macOS 上如果包里恰好带了 Cython，报的是 `Shadow` 模块相关的另一种错误。

**根因**：PyAV 的 wheel 里，一部分扩展模块旁边带着同名的 Cython 纯 Python 模式 `.py` 源码（里面有 `import cython`，不一定在首行）。`--include-package=av` 会遍历目录，把这些 `.py` 编进二进制，运行时盖掉真正的 `.pyd` / `.so`。

**现行做法**（三处 Nuitka 步骤一致，由 `tests/unit/test_pyav_packaging_contract.py` 守着）：

- 用 `--include-module=av`，让子模块走正常的 import 解析去选扩展模块；
- 加 `--no-prefer-source-code`，把「扩展模块优先」显式钉住。Nuitka 把这个默认值标为未决，翻成源码优先就会复现同样的失败；
- 不允许出现 `--include-package=av` 或 `--prefer-source-code`。

Nuitka 版本现在虽然锁了，这两个参数和契约测试仍然保留：它们表达的是「必须用扩展模块」这个意图，不依赖某个版本的默认行为。

**连带的一点**：`av.video.frame.to_image()` 在扩展模块内部才 import PIL，Nuitka 看不到这条依赖，目前靠别的模块顶层 `import PIL` 把它带进包。`scripts/check_frozen_media.py` 的冒烟覆盖这条路径；如果哪天清理掉了那些顶层 import，这个冒烟会红。

**推广**：其他 wheel 只要是「同名 `.py` 和扩展模块并存」，理论上同样会中招。新增带原生扩展的依赖、并且打算用 `--include-package` 时，先看一眼 site-packages 里它的目录结构。

## 冻结版的验证都在哪

`build-desktop.yml` 的 `build-python` 任务在 Nuitka 之后依次跑：

| 步骤 | 查什么 | 平台 |
|---|---|---|
| `Smoke test frozen Owner voice identity runtime` | 起冻结的可执行文件跑声纹冒烟，看退出码和成功标记 | Windows |
| `Verify bundled offline assets` | 向量模型、tiktoken 缓存、端点检测和声纹权重逐个文件非空，且权重只打包一份 | 全部 |
| `Verify frozen Windows wake runtime with pinned model` | 唤醒词运行时加模型的冒烟 | Windows |
| `Verify Nuitka dist invariants` | `check_nuitka_dist.py`（资源在不在、插件载荷和分阶段清单是否逐字节一致）和 `check_frozen_media.py`（PyAV、编解码、worker） | 全部 |
| `Verify dist CPU architecture` | `check_dist_arch.py` 读每个原生二进制的头 | 全部 |

`check_nuitka_dist.py` 明确不做两件事：不启动可执行文件；不检查编进二进制的 Python 模块（从外面枚举不出来）。所以「某个包没被编进去」这类问题，它查不到，要靠冒烟或者源码层的守卫。

给冻结版加新能力时，如果它依赖动态导入、子进程或原生库，考虑照声纹和唤醒词的做法加一个环境变量开关的冒烟入口。

## 本地最小 Nuitka 复现的注意事项

以下是在 Windows 上实测观察到的：

- 输出目录用短路径。路径太长时拷贝 DLL 会因为超过系统路径长度上限而失败。
- 多个 Nuitka 构建不要并行跑，它们共用缓存目录，会互相抢文件。
- 工作目录不要设在仓库里，Nuitka 的崩溃报告会写进当前目录。
- 非 standalone 模式编出来的程序，运行前要把 Python 安装目录加进 `PATH`，否则找不到 `python311.dll`。

## 编进二进制的常量

- `config/application.py` 的 `APP_VERSION` 被编进可执行文件。改完必须全量重编；只重跑校验、签名和封包的「续跑」出来的包，版本号还是旧的。
- 判断一个 dist 是什么时候编的，不要看可执行文件的修改时间（签名会刷新它），看 Nuitka 中间目录里生成的 `.c` 文件的时间。
- 不要用搜字符串的办法在可执行文件里确认版本号：包里的更新日志等数据本身带着多个版本的字符串，新旧版本都能搜到。
