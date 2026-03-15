#!/bin/bash
# N.E.K.O Telemetry Server 一键部署脚本
#
# 用法：
#   cd local_server/telemetry_server/deploy && ./setup.sh
#   或从任意目录运行：
#   bash /path/to/deploy/setup.sh
#
# 前置条件：Python 3.10+, pip

set -e

# 解析脚本自身所在目录，不依赖 CWD
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

INSTALL_DIR="/opt/neko-telemetry"
SERVICE_NAME="neko-telemetry"
PORT=8099

# 自动检测 nobody 的组名（Debian 用 nogroup，CentOS/RHEL 用 nobody）
if getent group nogroup &>/dev/null; then
    RUN_GROUP="nogroup"
else
    RUN_GROUP="nobody"
fi

echo "========================================="
echo "  N.E.K.O Telemetry Server Setup"
echo "========================================="

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python3 not found. Install it first:"
    echo "   apt install python3 python3-pip   (Debian/Ubuntu)"
    echo "   yum install python3 python3-pip   (CentOS/RHEL)"
    exit 1
fi

PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "✓ Python $PYTHON_VERSION"

# 检查配套文件是否存在
for f in server.py models.py security.py storage.py requirements.txt; do
    if [ ! -f "$SOURCE_DIR/$f" ]; then
        echo "❌ Missing file: $SOURCE_DIR/$f"
        echo "   Please run this script from the deploy/ directory inside the telemetry_server package."
        exit 1
    fi
done

# 创建目录
echo "→ Installing to $INSTALL_DIR ..."
sudo mkdir -p "$INSTALL_DIR/data"

# 复制文件（从脚本所在目录的父目录）
sudo cp "$SOURCE_DIR/server.py" "$SOURCE_DIR/models.py" "$SOURCE_DIR/security.py" \
        "$SOURCE_DIR/storage.py" "$SOURCE_DIR/requirements.txt" "$INSTALL_DIR/"

# 创建虚拟环境并安装依赖
echo "→ Creating virtualenv ..."
sudo python3 -m venv "$INSTALL_DIR/venv"
echo "→ Upgrading pip ..."
sudo "$INSTALL_DIR/venv/bin/pip" install --upgrade pip -i https://pypi.org/simple/
echo "→ Installing dependencies ..."
sudo "$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt" -i https://pypi.org/simple/

PYTHON_BIN="$INSTALL_DIR/venv/bin/python3"

# 生成 admin token
ADMIN_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
echo ""
echo "┌─────────────────────────────────────────────────┐"
echo "│  ★ 你的 Admin Token（请保存好）:                  │"
echo "│  $ADMIN_TOKEN  │"
echo "└─────────────────────────────────────────────────┘"
echo ""

# 将敏感凭据写入 root-only 环境文件，不在 systemd unit 中明文暴露
CREDENTIALS_FILE="$INSTALL_DIR/telemetry.env"
echo "→ Writing credentials to $CREDENTIALS_FILE (mode 0600, owner root) ..."
sudo tee "$CREDENTIALS_FILE" > /dev/null << EOF
TELEMETRY_ADMIN_TOKEN=$ADMIN_TOKEN
EOF
sudo chown root:root "$CREDENTIALS_FILE"
sudo chmod 600 "$CREDENTIALS_FILE"

# 安装 systemd service
echo "→ Installing systemd service ..."
sudo tee /etc/systemd/system/$SERVICE_NAME.service > /dev/null << EOF
[Unit]
Description=N.E.K.O Telemetry Collection Server
After=network.target

[Service]
Type=simple
User=nobody
Group=$RUN_GROUP
WorkingDirectory=$INSTALL_DIR
ExecStart=$PYTHON_BIN server.py --port $PORT
EnvironmentFile=$CREDENTIALS_FILE
Environment=TELEMETRY_DB_PATH=$INSTALL_DIR/data/telemetry.db
Restart=always
RestartSec=5
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$INSTALL_DIR/data
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# 设置目录权限
sudo chown -R nobody:$RUN_GROUP "$INSTALL_DIR/data"

# 启动
sudo systemctl daemon-reload
sudo systemctl enable --now $SERVICE_NAME

echo ""
sleep 1

# 验证
if curl -sf http://localhost:$PORT/health > /dev/null 2>&1; then
    echo "✅ Server is running on port $PORT"
else
    echo "⚠  Server may still be starting, check: systemctl status $SERVICE_NAME"
fi

echo ""
echo "========================================="
echo "  部署完成！"
echo "========================================="
echo ""
echo "  服务管理:"
echo "    systemctl status $SERVICE_NAME     # 状态"
echo "    journalctl -u $SERVICE_NAME -f     # 日志"
echo "    systemctl restart $SERVICE_NAME    # 重启"
echo ""
echo "  仪表盘:"
echo "    curl -H 'Authorization: Bearer $ADMIN_TOKEN' \\"
echo "         http://YOUR_SERVER_IP:$PORT/api/v1/admin/dashboard"
echo ""
echo "  客户端配置 (token_tracker.py):"
echo "    _TELEMETRY_SERVER_URL = \"http://YOUR_SERVER_IP:$PORT\""
echo ""
