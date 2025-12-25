# config.py
# 这里存放所有的公共配置

SERVER_HOST = "127.0.0.1"
SERVER_PORT = 9541  # 你想改端口只改这里就行了
API_PATH = "/api/v1/ws/cosyvoice"

# 拼接完整的 WebSocket 地址
# 方便客户端直接调用
WS_URL = f"ws://{SERVER_HOST}:{SERVER_PORT}{API_PATH}"
print(WS_URL)