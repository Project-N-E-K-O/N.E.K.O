#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Nginx 配置渲染层
#
# 被 docker/entrypoint.sh source（也可被 docker/nginx/test-render.sh source，
# 后者把路径指向临时 prefix，从而在 CI 里验证同一套渲染逻辑）。
#
# 模板 → 运行时路径的映射：
#   nginx.conf.template        → $NGINX_CONF_PATH          （/etc/nginx/nginx.conf）
#   neko-proxy.conf.template   → $NGINX_SITE_PATH          （/etc/nginx/conf.d/neko-proxy.conf）
#   server.http.conf.template  → $NGINX_NEKO_DIR/listener.conf
#   server.tls.conf.template   → $NGINX_NEKO_DIR/listener.conf   （同一目标的另一分支）
#   locations.conf.template    → $NGINX_NEKO_DIR/locations.conf
#   snippets/*.conf            → $NGINX_NEKO_DIR/snippets/*.conf
#
# 占位符约定：@@NAME@@。不用 ${NAME} 是因为 nginx 配置里到处都是 $变量，
# 用 @@ 定界之后模板中的 $http_host 之类保持字面量，读写都不需要转义。
#
# 安全约定：证书路径只放进未导出的 NGINX_SSL_CERT_PATH / NGINX_SSL_KEY_PATH。
# 不要导出成 SSL_CERT_FILE —— 那是 OpenSSL 约定用于指定 CA 包的变量，污染它会
# 影响同容器内所有 Python/curl 子进程的证书校验。
# ─────────────────────────────────────────────────────────────────────────────

# 路径默认值。生产由 entrypoint 使用；测试用环境变量覆盖到临时 prefix。
NGINX_TEMPLATE_DIR="${NGINX_TEMPLATE_DIR:-/app/docker/nginx}"
NGINX_NEKO_DIR="${NGINX_NEKO_DIR:-/etc/nginx/neko}"
NGINX_CONF_D_DIR="${NGINX_CONF_D_DIR:-/etc/nginx/conf.d}"
NGINX_CONF_PATH="${NGINX_CONF_PATH:-/etc/nginx/nginx.conf}"
NGINX_SITE_PATH="${NGINX_SITE_PATH:-$NGINX_CONF_D_DIR/neko-proxy.conf}"

# sed 替换串里 & 与分隔符需要转义；分隔符固定为 |。
nginx_sed_escape() {
    printf '%s' "$1" | sed -e 's/[&|]/\\&/g'
}

# render_nginx_template <模板> <目标>
render_nginx_template() {
    local src="$1" dst="$2"

    if [ ! -r "$src" ]; then
        echo "❌ Nginx template missing or unreadable: $src" >&2
        return 1
    fi

    mkdir -p "$(dirname "$dst")"
    # sed 或 mv 失败必须把状态传出去：否则渲染出一半的配置也能"成功"，
    # 启动流程会继续往下走，最后 nginx -t 或运行期才炸。
    if ! sed \
        -e "s|@@NGINX_PORT@@|$(nginx_sed_escape "${NGINX_PORT}")|g" \
        -e "s|@@NGINX_SSL_PORT@@|$(nginx_sed_escape "${NGINX_SSL_PORT}")|g" \
        -e "s|@@NEKO_MAIN_SERVER_PORT@@|$(nginx_sed_escape "${NEKO_MAIN_SERVER_PORT}")|g" \
        -e "s|@@NEKO_MEMORY_SERVER_PORT@@|$(nginx_sed_escape "${NEKO_MEMORY_SERVER_PORT}")|g" \
        -e "s|@@NEKO_TOOL_SERVER_PORT@@|$(nginx_sed_escape "${NEKO_TOOL_SERVER_PORT}")|g" \
        -e "s|@@NEKO_USER_PLUGIN_SERVER_PORT@@|$(nginx_sed_escape "${NEKO_USER_PLUGIN_SERVER_PORT}")|g" \
        -e "s|@@NGINX_SSL_CERT@@|$(nginx_sed_escape "${NGINX_SSL_CERT_PATH}")|g" \
        -e "s|@@NGINX_SSL_KEY@@|$(nginx_sed_escape "${NGINX_SSL_KEY_PATH}")|g" \
        -e "s|@@NGINX_NEKO_DIR@@|$(nginx_sed_escape "${NGINX_NEKO_DIR}")|g" \
        -e "s|@@NGINX_CONF_D_DIR@@|$(nginx_sed_escape "${NGINX_CONF_D_DIR}")|g" \
        "$src" > "$dst.tmp"; then
        rm -f "$dst.tmp"
        echo "❌ Failed to render $src -> $dst" >&2
        return 1
    fi
    if ! mv -f "$dst.tmp" "$dst"; then
        rm -f "$dst.tmp"
        echo "❌ Failed to publish $dst" >&2
        return 1
    fi
}

# render_nginx_configuration <证书路径> <私钥路径>
render_nginx_configuration() {
    local cert_file="$1" key_file="$2"
    local required_var snippet

    if [ ! -d "$NGINX_TEMPLATE_DIR" ]; then
        echo "❌ Nginx template directory not found: $NGINX_TEMPLATE_DIR" >&2
        return 1
    fi

    for required_var in NGINX_PORT NGINX_SSL_PORT NEKO_MAIN_SERVER_PORT \
                        NEKO_MEMORY_SERVER_PORT NEKO_TOOL_SERVER_PORT NEKO_USER_PLUGIN_SERVER_PORT; do
        if [ -z "${!required_var:-}" ]; then
            echo "❌ $required_var is not set; refusing to render Nginx configuration" >&2
            return 1
        fi
    done

    # 故意不 export：见文件头「安全约定」。
    NGINX_SSL_CERT_PATH="$cert_file"
    NGINX_SSL_KEY_PATH="$key_file"

    # L0 + L1b
    render_nginx_template "$NGINX_TEMPLATE_DIR/nginx.conf.template" "$NGINX_CONF_PATH" || return 1
    render_nginx_template "$NGINX_TEMPLATE_DIR/neko-proxy.conf.template" "$NGINX_SITE_PATH" || return 1

    # L1a：整个部署里 HTTP / HTTPS 两种模式的唯一差异
    if [ "${DISABLE_SSL:-0}" = "1" ]; then
        echo "🌐 Generating HTTP-only configuration (SSL disabled)..."
        render_nginx_template "$NGINX_TEMPLATE_DIR/server.http.conf.template" "$NGINX_NEKO_DIR/listener.conf" || return 1
    else
        echo "🌐 Generating HTTP+HTTPS configuration..."
        render_nginx_template "$NGINX_TEMPLATE_DIR/server.tls.conf.template" "$NGINX_NEKO_DIR/listener.conf" || return 1
    fi

    # L1c：路由表，两种模式共用同一份
    render_nginx_template "$NGINX_TEMPLATE_DIR/locations.conf.template" "$NGINX_NEKO_DIR/locations.conf" || return 1

    # 片段：自身不含端口类占位符，但统一走同一渲染路径，日后加变量无需改流程
    for snippet in "$NGINX_TEMPLATE_DIR"/snippets/*.conf; do
        if [ ! -e "$snippet" ]; then
            echo "❌ No Nginx snippets found in $NGINX_TEMPLATE_DIR/snippets" >&2
            return 1
        fi
        render_nginx_template "$snippet" "$NGINX_NEKO_DIR/snippets/$(basename "$snippet")" || return 1
    done

    # 渲染后自检：模板改名或漏配变量时，宁可在这里失败也不要交给 nginx -t 猜
    local rendered_unreplaced
    # 只看非注释行：模板注释里会提到占位符的写法本身
    rendered_unreplaced=$(grep -Rn '@@[A-Z_]*@@' "$NGINX_CONF_PATH" "$NGINX_SITE_PATH" "$NGINX_NEKO_DIR" 2>/dev/null \
        | grep -vE ':[0-9]+:[[:space:]]*#' || true)
    if [ -n "$rendered_unreplaced" ]; then
        echo "❌ Unreplaced placeholder left in rendered Nginx configuration:" >&2
        echo "$rendered_unreplaced" >&2
        return 1
    fi
}
