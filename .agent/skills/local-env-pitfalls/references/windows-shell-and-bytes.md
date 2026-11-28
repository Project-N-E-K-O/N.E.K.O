# Windows 上的 shell、编码和「写坏的字节」

## Bash 和 PowerShell 不要混写

Windows 上常常同时有 Git Bash 和 PowerShell 两个 shell，手感容易串。

- `@'...'@` 是 PowerShell 的 here-string。在 bash 里 `git commit -m @'...'@` 会把首尾的 `@` 当字面量写进提交说明。bash 里写多行提交说明用临时文件加 `git commit -F <文件>`，或者 `git commit -F - <<'EOF'`。
- 反引号在 bash 的双引号字符串里是命令替换。`gh ... -f body="…带反引号的 markdown…"` 里那段会被当命令执行，正文里留下一个缺了词的句子，而且不报错。给 `gh` 传 markdown 一律走文件。
- PowerShell 5.1 没有 `&&` 和 `||`。要「前一条成功才执行后一条」，写成 `A; if ($?) { B }`。
- PowerShell 不替外部命令展开通配符，Git Bash 会。
- 把脚本通过 heredoc 喂给另一个 shell 进程时，外层没导出的变量在里面是空的。变量在脚本里面定义。出过的事：重定向目标变成了根目录下的文件，命令一条都没执行，「已登记就跳过」的检查也静默失效。

## 退出码

- Git Bash 默认没开 `pipefail`。`A | B` 的退出码是 B 的，`A | tail -3 && C` 里 C 总会执行。
- 用 `;` 把测试和提交串起来同样拦不住失败。
- 要用 A 的结果当闸门：`A > log.txt 2>&1; rc=$?; tail -3 log.txt; [ $rc -eq 0 ] && C`，或者 `${PIPESTATUS[0]}`。
- 脚本里的 `2>/dev/null` 会把「命令不存在」「表达式语法错」一起吞掉，输出为空，看起来和「没有结果」一模一样。排查阶段用 `2>&1`。

## 编码

- 子进程的 stdout 接到管道时，Python 按系统的 ANSI 代码页编码。英文 Windows（包括 CI 的机器）是 cp1252，打印箭头、中日韩文字会抛 `UnicodeEncodeError`；中文系统的代码页能编码这些字符，所以本机复现不了。测试里起子进程时设 `PYTHONIOENCODING=utf-8`。本地模拟 CI：`PYTHONIOENCODING=cp1252 uv run python <脚本>`。
- `subprocess.run(..., text=True)` 不写 `encoding` 时用系统区域设置。显式写 `encoding="utf-8"`。
- Python 文本模式写文件默认把换行写成 CRLF，git 检出时按配置可能写成 LF。同一个文件被两种方式轮流写，行尾会来回翻，整文件变成差异。脚本读写源码用 `newline=""` 或字节模式。
- 命令替换 `$(cat 文件)` 会吃掉结尾的所有换行，拿它「备份再写回」会把文件末尾的换行抹掉。备份用 `cp`。

## 路径长度

- Windows 默认的路径上限是 260 字符。深层的临时目录加上长文件名很容易超过。症状五花八门：`FileNotFoundError`、WinError 206、`os.replace` 失败后被上层吞掉。
- 遇到只在本机出现、报错里路径很长的失败，先量路径长度。
- worktree、pytest 的 `--basetemp` 选短路径。
- 命令行长度也有上限（32767 字符）。把整段脚本放在命令行参数里（`node -e <脚本>`）会在进程启动前就失败，脚本要写进临时文件再执行。

## 文件占用

- 正在运行的应用会锁住它加载的 `.pyd` 和可执行文件。这时改动同一个 `.venv`（同步依赖、重装包）会半途失败。
- 测试进程被杀之后可能留下孤儿进程（pytest 的子进程、node），继续占着 `.venv` 里的可执行文件或临时目录。之后的命令报拒绝访问时，先查有没有残留进程：`Get-Process | ? ProcessName -match 'pytest|python|node'`，确认路径属于当前检出的再结束。
- 杀进程要杀整棵树：`taskkill /F /T /PID <pid>`。只杀直接子进程，孙进程会握着管道让父进程的读取永远不返回。

## 在 Windows 上跑 POSIX 专用的测试

硬链接、原子改名、权限位这类 POSIX 专用分支的用例在 Windows 上是跳过的，CI 的 Python 单测也几乎全在 Windows 上。本机有 WSL 时可以在 Linux 文件系统上实跑，限制和要点：

- Windows 的 `.venv` 里的编译扩展在 Linux 下用不了。只适合只依赖标准库就能导入的模块；要 FastAPI 之类的路由测试跑不了，除非在 WSL 里另建一套环境。
- 没有 pytest-randomly 时要加 `-o addopts=''`，否则 `pytest.ini` 里的 `--randomly-seed` 会报参数错。仓库的 conftest 依赖一堆第三方包，环境不全时加 `--noconftest`。
- `--basetemp` 指到 Linux 自己的文件系统。挂载进来的 Windows 盘上，硬链接和原子改名的语义不一样。

## ⚠️ 转义序列被多解释一层，落盘的是真实字节

### 现象

想往文件里写的是**源码文本**（比如 Python 源码里的「反斜杠加 x00」这几个字符），实际落盘的是它代表的那个字节。发生的位置不只一处：

- bash 的 heredoc 把内容喂给 `python -`，即使是带引号的 heredoc；
- `python -c "..."`；
- 编辑工具的参数里直接写转义序列；
- 用脚本生成另一个脚本（生成 JS、生成 workflow、生成测试文件）。

见过的后果，按隐蔽程度排：

1. 空字节：`.py` 被判成二进制，导入时报 `source code string cannot contain null bytes`，一批测试文件收集失败。
2. 换行：字符串字面量被真换行截断，`unterminated string literal`。
3. ⚠️ 不可见的控制字符：想写正则的单词边界，落盘的是退格符。那一行读出来像是「没写进去」，文件照样能导入、测试照样跑，只是正则要求匹配一个字面的退格符，相关用例静默失败。
4. ⚠️ Unicode 转义被提前解释成引号：生成出语法合法、语义是垃圾的源码，`ast.parse` 照样通过。
5. 孤立的代理字符：`write_text` 先清空文件再编码，编码失败，整个文件变成空的。

用同一条通道去修（再写一个带反斜杠的 `replace`）会再被吃一次：要找的串和要换成的串落地后一模一样，`replace` 什么都没换，断言还通过。

### 规则

写锚点或替换串之前先问一句：这段里有没有反斜杠？

1. 能不用就不用。分隔符、指纹的连接符选可见字符（例如竖线加空格）。测试数据如果只关心长度，就用不需要转义的内容。
2. 锚点挑一行不含反斜杠的。
3. 必须生成反斜杠时用 `chr(92)` 拼；引号用 `chr(34)` / `chr(39)`；换行用 `chr(10)`；字节用 `bytes([1, 0])`；JS 里用 `String.fromCharCode(10)`。中日韩文字直接写字面量，不受影响。
4. 改写脚本一律先编码再写：`data = s.encode("utf-8")` 成功之后才 `write_bytes(data)`。
5. 生成给别的工具执行的脚本，用字节模式写，或者事后把 CRLF 转成 LF。

### 写完立刻检查

```bash
uv run --no-sync python -c "import sys; b=open(sys.argv[1],'rb').read(); print('nul', b.count(bytes([0])), 'ctrl', sum(1 for c in b if c < 32 and c not in (9, 10, 13)))" <文件>
uv run --no-sync python -m py_compile <文件>
```

- 两个计数都应该是 0。
- 语法通过不等于内容对。**回读生成物本身**，看那一行是不是想要的样子；怀疑有不可见字符时打印那一行的 `repr()`。
- 症状「明明写了，读出来那一段是空的」就是它。

### 已经写坏了

- 文件的其余部分已经提交过：`git show HEAD:<路径>` 取回来重做最干净。
- 否则用 Python 按字节定位、截断或替换，不要再用带转义的字符串去匹配。
