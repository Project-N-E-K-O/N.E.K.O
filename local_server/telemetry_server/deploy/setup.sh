#!/bin/bash
# N.E.K.O Telemetry Server 一键部署脚本
#
# 用法：
#   curl -sSL https://your-repo/setup.sh | bash
#   或
#   chmod +x setup.sh && ./setup.sh
#
# 前置条件：Python 3.10+, pip

set -e

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

# 创建目录
echo "→ Installing to $INSTALL_DIR ..."
sudo mkdir -p "$INSTALL_DIR/data"

# 复制文件
sudo cp server.py models.py security.py storage.py requirements.txt "$INSTALL_DIR/"

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
Environment=TELEMETRY_ADMIN_TOKEN=$ADMIN_TOKEN
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
