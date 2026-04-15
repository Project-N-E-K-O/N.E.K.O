# CodeRabbit 误报说明

> 本文件记录 CodeRabbit 对 `bilibili_danmaku` 插件的一条误报，供后续 Review 参考。

---

## 误报条目

### [误报] WebSocket 收包未先拆包

**CodeRabbit 原文**（摘录）：

> `async for message in ws` 收到的是整帧字节流，但这里对 bytes 只调用了一次 `_process_packet(message)`。如果服务端把多个未压缩包拼在同一个 WebSocket frame 里，第一包之后的数据就不会再被解析了。你前面已经有 `_split_packets()` 了，建议在最外层先把每一帧拆开再逐个处理。

**相关代码**（`danmaku_core.py`，`_connect_once`）：

```python
async for message in ws:
    if self._stop_event.is_set():
        break
    try:
        if isinstance(message, bytes):
            self._process_packet(message)
        # str 消息忽略
    except Exception as e:
        self._log(f"处理消息异常: {e}", "debug")
```

---

## 为何是误报

CodeRabbit 的担忧是："多个业务包可能拼在同一 WebSocket frame 里"。但在本插件的协议处理路径中，**这种情况已经被正确处理**，原因如下：

### B站弹幕 WebSocket 的包结构

B站弹幕服务器每个 WebSocket frame 只包含**一个顶层协议包**（`operation = OPERATION_SEND_MSG`）。真正的"多包拼帧"情况只发生在**压缩载荷解压后**——服务器会把多条弹幕消息压缩打包成一个 payload，解压后才会出现多个子包。

### 现有代码已正确处理

`_process_packet()` 中的 `OPERATION_SEND_MSG` 分支（第 537~557 行）已经区分了两种情况：

```python
elif operation == OPERATION_SEND_MSG:
    if proto_ver in (PROTOCOL_VERSION_ZLIB, PROTOCOL_VERSION_BROTLI):
        # ✅ 压缩包：解压后用 _split_packets() 拆分多个子包，逐个递归处理
        decompressed = _decompress(body, proto_ver)
        for pkt in _split_packets(decompressed):
            self._process_packet(pkt)
    else:
        # ✅ 未压缩包：单个 JSON，直接解析
        msg = json.loads(body.decode("utf-8"))
        self._dispatch_message(cmd, msg)
```

- **压缩协议（zlib/brotli）**：解压后调用 `_split_packets()` 拆成多包，再逐个递归 `_process_packet()`。多包拼帧的情况在这里完全覆盖。
- **未压缩协议**：每帧对应单条 JSON 消息，直接解析，不存在多包问题。

### CodeRabbit 的逻辑混淆点

CodeRabbit 误以为需要在 `async for message in ws` 层面先调用 `_split_packets()` 拆帧，但实际上：

1. `websockets` 库的 `async for message in ws` 已经以**帧为单位**逐个 yield，不会把两帧合并
2. "多个子包"是**压缩解压后的内部结构**，不是 WebSocket 帧层面的多包
3. `_split_packets()` 已经在 `_process_packet()` 的**正确位置**被调用（解压后）

### 结论

现有代码对 B站弹幕 WebSocket 协议的处理**完整且正确**，无需在收包入口增加额外拆包逻辑。该条建议属于对协议结构的理解偏差，**不需要修改**。

---

*记录时间：2026-04-03*
