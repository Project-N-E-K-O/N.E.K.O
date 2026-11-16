# 前端测试

## 几种前端测试各管什么

| 形态 | 在哪 | 管什么 | CI |
|---|---|---|---|
| Python 静态测试 | `tests/unit/test_*_static.py` 等 | 对 JS、CSS、模板源码做正则断言：某个调用在不在、某个字段路由到哪 | 跑 |
| pytest 起 node 跑真模块 | `tests/unit` 里用 `tests/node_harness.py` 的用例 | 把真实的前端模块放进 `vm` 沙箱执行，断言行为 | 跑 |
| `node:test` 文件 | `tests/unit/*.test.js`、`tests/frontend/*.cjs`、`static/*.test.cjs` | 同上，但由 `node --test` 直接跑，pytest 不收集 | `tests/unit/*.test.js` 全跑；另两处只跑手列清单里的 |
| vitest | `frontend/react-neko-chat`、`frontend/plugin-manager` | 组件和工具函数 | 跑 |
| Playwright 页面测试 | `tests/frontend/test_*.py` | 起真实页面点一遍 | 基本不跑 |

## 静态源码测试抓不到并发缺陷

静态测试能钉住「某个调用存在」，抓不到「两个并发流程互相踩状态」。典型的漏网形态：收尾代码拆的是模块级的共享对象，而不是本次尝试自己的对象，两次重叠的启动里被抢占的那次把胜出那次的资源拆掉了；所有静态测试全绿。

并发、取消、重入这类行为，用 node 跑真模块来测：

- 起 node 一律走 `tests/node_harness.py` 的 `run_node_script()`（写临时文件再跑）或 `run_node_stdin()`。`tests/unit/test_node_harness_contract.py` 会检查没有人绕过它。原因写在 `tests/node_harness.py` 开头：`node -e` 会撞 Windows 命令行 32767 字符的上限，不指定编码时带中日韩文字的脚本在英文 Windows 上直接失败。
- 参考实现：`tests/unit/test_mic_start_race.py`。模块路径用占位符加 `.replace(占位符, json.dumps(str(路径)))` 注入；找不到 node 时怎么处理（跳过还是失败）由各用例自己定。

写沙箱时的坑：

1. Node 22 里 `global.navigator` 是只读的，`global.navigator = {...}` 静默失败，模块拿到的是内置的那个。用 `vm.createContext(sandbox)` 把 `navigator` 放进沙箱对象，不要碰 `globalThis`。
2. `console` 的桩要补全（`dir`、`debug`、`trace`、`group` 等）。缺一个，变异验证时用例会红在 `console.dir is not a function` 上，而不是红在真正的断言上。
3. 模块末尾常有延迟执行的副作用。把 `setTimeout`、`setInterval`、`requestAnimationFrame` 换成桩，node 才能自然退出。
4. 桩要有真实的副作用。「断开连接」的桩如果是空函数，被测代码少做一次断开也看不出来。

## ⚠️ node harness 挂住会以 0 退出

harness 的常见写法是 `async function run() {...}; run().catch(...)`。如果 `run()` 里 `await` 到一个永远不结束的 promise，又没有别的定时器，事件循环空了，node 直接以 0 退出：后面的断言一条没跑，用例却是绿的。这类 harness 恰恰是用来测「会不会挂住」的。

做法：在 `run()` 的最后一行置一个完成标记，并在退出钩子里检查它。现成写法见 `tests/unit/test_voice_start_failure_static.py`：

```js
let completed = false;
process.on('exit', () => {
  if (!completed) { console.error('run() never finished'); process.exitCode = 1; }
});
```

变异之后用例仍然绿时，先怀疑 harness 挂住后静默退出了。

## node 用例超时怎么分诊

不要先去调大超时。`tests/node_harness.py` 用 `node --require` 预加载了一个看门狗模块，把两种情况分开，机制写在它的 docstring 里：

- 退出码 **87**：脚本自己没有结束（忘了清的定时器、没人结束的 promise）。这是 harness 的真缺陷，重试多少次都该红，输出里会点名是什么在占着事件循环。
- `subprocess.TimeoutExpired`：node 根本没跑到脚本（进程创建或启动卡住）。launcher 会自动重试，脚本的任何超时设置都帮不上忙。
- 已知边界：看门狗挂在 CommonJS 的编译钩子上，ES module 形式的 harness 不受它保护。现有 harness 全是 CommonJS，要引入 ES module 形式得先改这里。
- 同步死循环（`while (true) {}`）看门狗打断不了。

改看门狗本身时最容易错的是「脚本的预算从什么时候算起」：进程启动、预加载模块的加载、读脚本文件的时间都不能算进脚本的预算，否则启动慢会被报成脚本超时，并且顶替掉本该发生的重试。

## 只抽几个函数放进 `vm` 的测试

`tests/unit/*.test.js` 和 `tests/frontend/*.cjs` 里很多用例用 `readFunction` / `sourceBetween` 这样的辅助函数，从一两个源文件里抽出几个函数放进 `vm` 跑，其余依赖由测试自己打桩。

- 症状：把重复逻辑收成一个定义在**别的文件**里的 helper 之后，这些用例抛 `ReferenceError`，CI 的 frontend-contract (Node) 变红。
- 做法：改 `static/` 下的函数（尤其是新增或改名跨文件的 helper）之后，除了相关的 `.cjs`，再跑一遍 `node --test tests/unit/*.test.js`；搜一下测试里给旧函数名打桩的地方，同步改。
- 不要为了迁就测试，在产品代码里加回 `typeof x === 'function'` 这样的守卫。
- ⚠️ 补完桩之后做一次变异验证。出现过这种情况：用例之前能过，只是因为 `typeof` 判断直接返回了 false，被测逻辑根本没跑到。

## Python 守卫会读前端的 CSS

`tests/unit` 里有 Python 测试直接用正则读 `frontend/react-neko-chat/src/styles.css`，按选择器检查**具体的属性名**（例如要求写 `background-image`，写成 `background:` 简写就失败）。前端 CSS 的一部分契约放在后端的 pytest 里，vitest 和构建全绿也抓不到。

- 动了这个文件（包括合并 main 时解冲突）之后，跑一遍读它的测试：

  ```bash
  grep -rlE "react-neko-chat.*styles\.css" tests --include=*.py
  ```

- 解冲突时保留对方分支的属性名，不要顺手改成简写。

## 靠切片取代码块的静态测试

用 `source.split(锚点)` 或「某个标记之后的第一个 `catch`」这种方式取代码块的测试，在源码里新增一处同名锚点时会静默改指到别处，而且往往仍然是绿的。改了前端源码之后，回头看一遍引用了同一段代码的静态测试还在不在测原来那段。详见 `guard-test-failure-modes.md` 第 4 类。

## 页面测试要先有构建产物

`static/react/neko-chat/` 是构建产物，不进版本库。在一个刚检出、没构建过的目录里跑 `tests/frontend/test_*_browser.py` 这类 Playwright 用例，页面会报 `React chat bundle failed to load`，按钮不渲染，点击等 30 秒后超时，看起来像被测代码坏了。

- 先在 `frontend/react-neko-chat` 里 `npm run build`，再回检出根跑 pytest。
- 只有改了 React 源码才需要重新构建。`static/app/**` 这类宿主侧的 JS 是直接提供的，改完不用构建。
- PR 只带源码，不提交构建产物。

## 在不可见的页面里验证前端代码

用自动化驱动的浏览器页面如果处于不可见状态（`document.hidden` 为真，例如标签页或面板被隐藏），会有这些现象（实测观察）：

- 视口宽高可能是 0，`getBoundingClientRect` 全 0。先显式设视口尺寸。
- `requestAnimationFrame` 不触发。在被测脚本加载之前，只在 `document.hidden` 时把它换成 `setTimeout(cb, 16)` 的垫片。
- CSS 过渡停在起点。读终值之前对 `el.getAnimations()` 逐个调 `finish()`。

另外：

- 自己写的 Python 静态文件服务器要用多线程的（`ThreadingTCPServer`）。单线程的会被 Chromium 预连接的空闲连接卡住，后面的请求全部失败，日志里还看不到请求。
- 要验证的修复藏在一个大函数里时，在测试页里读取源文件、按唯一锚点切出那段真实代码来执行，比手抄一份可靠。
- 用 jsdom 判断「某个按钮点不点得到」时用真实的 `el.click()`。`dispatchEvent` 会绕过 `disabled`，结论是反的。
