#!/bin/bash
# N.E.K.O Survey Server 一键部署脚本
#
# 用法：
#   cd local_server/survey_server/deploy && ./setup.sh
#   或从任意目录运行：
#   bash /path/to/deploy/setup.sh
#
# 前置条件：Python 3.10+, pip
# 与 telemetry 互不依赖，可与 neko-telemetry 同机共存（不同端口 8100）。

set -e

# 解析脚本自身所在目录，不依赖 CWD
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

INSTALL_DIR="/opt/neko-survey"
SERVICE_NAME="neko-survey"
PORT=8100

# 自动检测 nobody 的组名（Debian 用 nogroup，CentOS/RHEL 用 nobody）
if getent group nogroup &>/dev/null; then
    RUN_GROUP="nogroup"
else
    RUN_GROUP="nobody"
fi

echo "========================================="
echo "  N.E.K.O Survey Server Setup"
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
        echo "   Please run this script from the deploy/ directory inside the survey_server package."
        exit 1
    fi
done

# 创建目录并提前在独立的 root 所有暂存目录构建全新虚拟环境，安装成功后再切换，
# 既不以 root 运行旧 nobody 目录中的 pip，也不会在安装失败时破坏线上服务
echo "→ Installing to $INSTALL_DIR ..."
if [ -L "$INSTALL_DIR/venvs" ]; then
    sudo rm -f "$INSTALL_DIR/venvs"
fi
sudo mkdir -p "$INSTALL_DIR/data" "$INSTALL_DIR/venvs"
sudo chown root:root "$INSTALL_DIR" "$INSTALL_DIR/venvs"
sudo chmod 755 "$INSTALL_DIR" "$INSTALL_DIR/venvs"

STAGING_VENV="$INSTALL_DIR/venvs/venv-$(date +%Y%m%d%H%M%S)-$$"
TMP_LINK=""
cleanup_staging_venv() {
    if [ -n "${TMP_LINK:-}" ] && [ -L "$TMP_LINK" ]; then
        sudo rm -f "$TMP_LINK"
    fi
    if [ -n "${STAGING_VENV:-}" ] && [ -d "$STAGING_VENV" ]; then
        sudo rm -rf "$STAGING_VENV"
    fi
}
trap cleanup_staging_venv EXIT

echo "→ Creating virtualenv in $STAGING_VENV ..."
sudo python3 -m venv "$STAGING_VENV"
echo "→ Installing dependencies ..."
sudo "$STAGING_VENV/bin/pip" install --no-cache-dir --require-hashes --only-binary :all: -r "$SOURCE_DIR/requirements.txt" -i https://pypi.org/simple/
sudo chown -R root:root "$STAGING_VENV"
sudo chmod -R a+rX,go-w "$STAGING_VENV"

# 仅在新依赖全部就绪后再安全替换脚本、清单并原子切换 venv 指向
for f in server.py models.py security.py storage.py requirements.txt; do
    sudo rm -f "$INSTALL_DIR/$f"
    sudo install -o root -g root -m 644 "$SOURCE_DIR/$f" "$INSTALL_DIR/$f"
done

OLD_LEGACY_VENV=""
if [ -e "$INSTALL_DIR/venv" ] && [ ! -L "$INSTALL_DIR/venv" ]; then
    OLD_LEGACY_VENV="$INSTALL_DIR/venv.legacy.$$"
    sudo mv "$INSTALL_DIR/venv" "$OLD_LEGACY_VENV"
fi
TMP_LINK="$INSTALL_DIR/venv.link.$$"
sudo ln -sfn "$STAGING_VENV" "$TMP_LINK"
sudo mv -Tf "$TMP_LINK" "$INSTALL_DIR/venv"
sudo chown -h root:root "$INSTALL_DIR/venv"
ACTIVE_VENV="$STAGING_VENV"
TMP_LINK=""
STAGING_VENV=""

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
CREDENTIALS_FILE="$INSTALL_DIR/survey.env"
echo "→ Writing credentials to $CREDENTIALS_FILE (mode 0600, owner root) ..."
sudo tee "$CREDENTIALS_FILE" > /dev/null << EOF
SURVEY_ADMIN_TOKEN=$ADMIN_TOKEN
EOF
sudo chown root:root "$CREDENTIALS_FILE"
sudo chmod 600 "$CREDENTIALS_FILE"

# 安装 systemd service
echo "→ Installing systemd service ..."
sudo tee /etc/systemd/system/$SERVICE_NAME.service > /dev/null << EOF
[Unit]
Description=N.E.K.O Survey Collection Server
After=network.target

[Service]
Type=simple
User=nobody
Group=$RUN_GROUP
WorkingDirectory=$INSTALL_DIR
ExecStart=$PYTHON_BIN server.py --port $PORT
EnvironmentFile=$CREDENTIALS_FILE
Environment=SURVEY_DB_PATH=$INSTALL_DIR/data/survey.db
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

# 设置目录权限（venv 保持 root 所有且只读，仅 data 需要 nobody 可写）
sudo chown -R root:root "$ACTIVE_VENV"
sudo chmod -R a+rX,go-w "$ACTIVE_VENV"
sudo chown -R nobody:$RUN_GROUP "$INSTALL_DIR/data"

# 启动 / 重启服务后再清理旧版本环境
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

if [ -n "$OLD_LEGACY_VENV" ] && [ -e "$OLD_LEGACY_VENV" ]; then
    sudo rm -rf "$OLD_LEGACY_VENV"
fi
ACTIVE_VENV_NAME="$(basename "$ACTIVE_VENV")"
for old_venv in "$INSTALL_DIR/venvs"/venv-*; do
    [ -e "$old_venv" ] || continue
    if [ "$(basename "$old_venv")" != "$ACTIVE_VENV_NAME" ]; then
        sudo rm -rf "$old_venv"
    fi
done

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
echo "  查询/导出（需 admin token）:"
echo "    curl -H 'Authorization: Bearer $ADMIN_TOKEN' \\"
echo "         'http://YOUR_SERVER_IP:$PORT/api/v1/admin/summary'"
echo ""
echo "  客户端配置 (utils/survey_client.py):"
echo "    _SURVEY_SERVER_URL = \"http://YOUR_SERVER_IP:$PORT\""
echo ""
