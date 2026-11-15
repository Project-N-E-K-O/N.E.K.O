#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Nginx 渲染层回归测试
#
#   bash docker/nginx/test-render.sh
#
# 不需要容器、不需要 root、不需要网络。PATH 里有 nginx 时额外跑一次 nginx -t。
#
# 覆盖的不变量：
#   T1 两种模式都能渲染，且渲染结果里没有残留的 @@占位符
#   T2 每个 include 目标都真实存在（绝对路径 / glob 逐条验证）
#   T3 两种模式的差异只允许出现在 listener.conf
#   T4 分层约束：监听器只出现在 listener.conf；路由表与 TLS 无关且两模式共用
#   T5 （可选）对两个模式的渲染结果分别执行 nginx -t
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "$0")" && pwd -P)
WORK=$(mktemp -d)
PREFIX="$WORK/prefix"
trap 'rm -rf -- "$WORK"' EXIT

FAILED=0
pass() { printf '  ✅ %s\n' "$1"; }
fail() { printf '  ❌ %s\n' "$1" >&2; FAILED=1; }
info() { printf '  ℹ️  %s\n' "$1"; }

# render_mode <http|tls> <prefix>
render_mode() {
    local mode="$1" prefix="$2"
    (
        export NGINX_TEMPLATE_DIR="$SCRIPT_DIR"
        export NGINX_NEKO_DIR="$prefix/etc/nginx/neko"
        export NGINX_CONF_D_DIR="$prefix/etc/nginx/conf.d"
        export NGINX_CONF_PATH="$prefix/etc/nginx/nginx.conf"
        export NGINX_SITE_PATH="$prefix/etc/nginx/conf.d/neko-proxy.conf"
        # 刻意用与生产默认不同的端口：如果模板里还留着硬编码的 48911/48912/
        # 48915/48916，T4 的"无硬编码"断言就会红，注入失败也藏不住。
        export NGINX_PORT=8080
        export NGINX_SSL_PORT=8443
        export NEKO_MAIN_SERVER_PORT=58911
        export NEKO_MEMORY_SERVER_PORT=58912
        export NEKO_TOOL_SERVER_PORT=58915
        export NEKO_USER_PLUGIN_SERVER_PORT=58916
        if [ "$mode" = "http" ]; then
            export DISABLE_SSL=1
        fi
        # shellcheck source=render.sh
        . "$SCRIPT_DIR/render.sh"
        # 证书路径放在 prefix 内：T5 的 nginx -t 会真的去读证书文件，
        # 用 /home/neko/... 会在测试机上找不到文件而误报（CI 上就踩到了）。
        # 快照比较时 prefix 会被归一化成 @NGINX_PREFIX@，不影响 T3/T4。
        render_nginx_configuration "$prefix/ssl/N.E.K.O.crt" "$prefix/ssl/N.E.K.O.key"
    ) >/dev/null
}

# 两次渲染写到同一个 prefix，逐次快照。快照时把 prefix 归一化成 @NGINX_PREFIX@：
# 否则「两种模式差异」会被注入的绝对路径差异淹没，比较就失去意义。
snapshot() {
    local label="$1"
    local dest="$WORK/snap-$label"
    rm -rf "$dest"
    mkdir -p "$dest"
    cp -a "$PREFIX/." "$dest/"
    find "$dest" -type f -exec sed -i "s|$PREFIX|@NGINX_PREFIX@|g" {} +
}

echo "== 渲染两种模式 =="
render_mode http "$PREFIX" && pass "HTTP-only 模式渲染完成" || fail "HTTP-only 模式渲染失败"
snapshot http
render_mode tls "$PREFIX" && pass "HTTP+HTTPS 模式渲染完成" || fail "HTTP+HTTPS 模式渲染失败"
snapshot tls

echo
echo "== T1 无残留占位符 =="
leftover=$(grep -Rn '@@' "$WORK/snap-http" "$WORK/snap-tls" 2>/dev/null | grep -vE ':[0-9]+:[[:space:]]*#' || true)
if [ -z "$leftover" ]; then
    pass "渲染结果中无 @@占位符"
else
    fail "渲染结果中存在未替换的占位符:"
    printf '%s\n' "$leftover" | sed 's/^/     /' >&2
fi

echo
echo "== T2 include 目标存在 =="
missing=0
external=0
for snap in "$WORK/snap-http" "$WORK/snap-tls"; do
    while IFS= read -r raw; do
        target="${raw//@NGINX_PREFIX@/$PREFIX}"
        case "$target" in
            *'*'*)
                if [ -z "$(compgen -G "$target" || true)" ]; then
                    fail "include glob 未匹配到任何文件: $target"
                    missing=1
                fi
                ;;
            "$PREFIX"/*)
                if [ ! -e "$target" ]; then
                    fail "include 目标不存在: $target"
                    missing=1
                fi
                ;;
            *)
                external=$((external + 1))
                ;;
        esac
    done < <(grep -Rho 'include[[:space:]]\+[^;]*;' "$snap" \
             | sed -e 's/^include[[:space:]]\+//' -e 's/;$//' | sort -u)
done
[ "$missing" = "0" ] && pass "生成目录内的 include 目标全部存在"
info "另有 $external 个发行版外部 include（/etc/nginx/mime.types）不在本次校验范围"

echo
echo "== T3 两种模式差异只在 listener.conf =="
: > "$WORK/changed.txt"
while IFS= read -r rel; do
    if [ ! -e "$WORK/snap-tls/$rel" ] || ! cmp -s "$WORK/snap-http/$rel" "$WORK/snap-tls/$rel"; then
        printf '%s\n' "$rel" >> "$WORK/changed.txt"
    fi
done < <(cd "$WORK/snap-http" && find . -type f | sort)
while IFS= read -r rel; do
    [ -e "$WORK/snap-http/$rel" ] || printf '%s\n' "$rel" >> "$WORK/changed.txt"
done < <(cd "$WORK/snap-tls" && find . -type f | sort)

expected="./etc/nginx/neko/listener.conf"
actual=$(sort -u "$WORK/changed.txt")
if [ "$actual" = "$expected" ]; then
    pass "唯一差异文件是 $expected"
else
    fail "差异文件集合不符合预期（应只有 $expected）:"
    printf '%s\n' "$actual" | sed 's/^/     /' >&2
fi

echo
echo "== T4 分层约束 =="
if grep -q '^listen ' "$WORK/snap-tls/etc/nginx/neko/listener.conf" \
   && grep -q '^ssl_certificate ' "$WORK/snap-tls/etc/nginx/neko/listener.conf"; then
    pass "listener.conf 持有监听与 TLS 指令"
else
    fail "listener.conf 缺少监听 / TLS 指令"
fi

if grep -qE '^[[:space:]]*(listen|ssl_)' "$WORK/snap-tls/etc/nginx/neko/locations.conf"; then
    fail "locations.conf 出现 listen / ssl_ 指令：路由表必须与 TLS 无关"
else
    pass "locations.conf 与 TLS 无关"
fi

if cmp -s "$WORK/snap-http/etc/nginx/neko/locations.conf" "$WORK/snap-tls/etc/nginx/neko/locations.conf"; then
    pass "两种模式共用同一份路由表"
else
    fail "两种模式的路由表不一致"
fi

injected_ok=1
for port in 58911 58912 58915 58916; do
    grep -q "127.0.0.1:$port" "$WORK/snap-tls/etc/nginx/neko/locations.conf" || {
        fail "路由表里找不到注入的端口 $port"
        injected_ok=0
    }
done
[ "$injected_ok" = "1" ] && pass "四个上游端口全部由环境变量注入"

hardcoded=$(grep -nE '127\.0\.0\.1:(48911|48912|48915|48916)\b' \
    "$WORK/snap-tls/etc/nginx/neko/locations.conf" || true)
if [ -z "$hardcoded" ]; then
    pass "路由表中没有残留的硬编码上游端口"
else
    fail "路由表仍有硬编码端口（改了环境变量会导致反代打错后端）:"
    printf '%s\n' "$hardcoded" | sed 's/^/     /' >&2
fi

# 数量不写死：合法新增一条带 proxy_pass 的路由不该把测试判红。
# 断言的是"渲染没有丢或凭空加 location"—— 与模板里的条数一致。
route_count=$(grep -cE '^location' "$WORK/snap-http/etc/nginx/neko/locations.conf" || true)
template_count=$(grep -cE '^location' "$SCRIPT_DIR/locations.conf.template" || true)
if [ "$route_count" = "$template_count" ]; then
    pass "路由表条数与模板一致（$route_count 条）"
else
    fail "路由表条数与模板不一致：渲染 $route_count / 模板 $template_count"
fi

# 条数一致还不够：删掉 /static/ 时渲染与模板会同时减一，上面的断言照样通过，
# 而 /static/ 会掉进 location / 走主服务代理。必需路由逐条点名。
#
# 两个坑（第一版都踩了）：
#   1. for want in $REQUIRED_ROUTES 会按空格切词，"location = /health" 被拆成
#      三个词，其中 "location" 在任何一条路由里都能命中 —— 等于没检查。
#   2. 不带 { 的子串匹配会被注释里的路径命中。
# 所以：逐行读取 + 整行精确匹配（grep -qxF，含结尾的 {）。
missing_route=0
while IFS= read -r want; do
    [ -n "$want" ] || continue
    if ! grep -qxF -- "$want" "$WORK/snap-http/etc/nginx/neko/locations.conf"; then
        fail "必需路由缺失：$want"
        missing_route=1
    fi
done <<'REQUIRED'
location = /security/csrf-token {
location /memory/ {
location /agent/ {
location /static/ {
location = /health {
location ~ /\. {
REQUIRED
[ "$missing_route" = "0" ] && pass "必需路由全部在位（静态别名 / 健康检查 / 记忆 / Agent / CSRF 引导）"

echo
echo "== T6 路由表契约：每个 location 要么反代，要么在直出白名单 =="
# 实例访问门禁做在应用层，所以 nginx 直出的路径天然在门禁之外。中枢会不断新增
# 对外端点（会话事件流、文件同步、设备注册…），任何一个图省事写成直出，就是一个
# 没有任何症状的永久口子。这条断言把它变成 CI 红灯。
#
# 白名单是当前仅有的三条直出，新增必须在这里显式登记（并在 README 里说明理由）。
# 用固定行匹配（grep -F）而不是 case 模式：隐藏文件那条的匹配串里有一个字面
# 反斜杠，写成 case 模式要 \\ 才能表达，既不可读也容易写错（第一版就写错了，
# 结果 "location ~ /private" 这种本该拒绝的直出也能通过）。
DIRECT_ALLOWLIST='location = /health
location /static/
location ~ /\.'
mapfile -t ROUTE_VERDICTS < <(awk '
    /^location/ { name=$0; sub(/[[:space:]]*\{[[:space:]]*$/, "", name); has=0; inb=1; next }
    inb && /proxy_pass/ { has=1 }
    inb && /^\}/ { print (has ? "PROXY " : "DIRECT ") name; inb=0 }
' "$WORK/snap-tls/etc/nginx/neko/locations.conf")

# 解析自检：解析器坏掉时不能"零条 → 全过"。比的是"解析出几条 vs 文件里有几条"，
# 不是固定数字 —— 否则合法新增一条带 proxy_pass 的路由也会把 T6 判红。
# 这一条自己也要被验证：负向测试（临时加一条**直出** location）必须让 T6 变红。
location_lines=$(grep -cE '^location' "$WORK/snap-tls/etc/nginx/neko/locations.conf" || true)
if [ "${#ROUTE_VERDICTS[@]}" -ne "$location_lines" ]; then
    fail "路由表解析异常：文件里有 $location_lines 条 location，只解析出 ${#ROUTE_VERDICTS[@]} 条（T6 已失去意义）"
else
    DIRECT_ALLOWLIST_VIOLATION=0
    for entry in "${ROUTE_VERDICTS[@]}"; do
        verdict="${entry%% *}"
        name="${entry#* }"
        [ "$verdict" = "PROXY" ] && continue
        if printf '%s\n' "$DIRECT_ALLOWLIST" | grep -qxF -- "$name"; then
            continue
        fi
        fail "location 既没有 proxy_pass、也不在直出白名单：$name"
        DIRECT_ALLOWLIST_VIOLATION=1
    done
    [ "$DIRECT_ALLOWLIST_VIOLATION" = "0" ] && pass "所有 location 要么反代要么在白名单内"
fi

echo
echo "== T5 nginx -t（可选） =="
if command -v nginx >/dev/null 2>&1 && command -v openssl >/dev/null 2>&1 \
   && { [ "$(id -u)" = "0" ] || [ -w /var/log/nginx ]; }; then
    for mode in http tls; do
        t5="$WORK/t5-$mode"
        mkdir -p "$t5/ssl"
        # nginx -t 会真的去加载证书，测试里造一张一次性自签证书给它
        if ! openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj "/CN=localhost" \
                -keyout "$t5/ssl/N.E.K.O.key" -out "$t5/ssl/N.E.K.O.crt" >/dev/null 2>&1; then
            info "跳过 T5：无法生成测试证书（openssl 不可用？）"
            break
        fi
        render_mode "$mode" "$t5" >/dev/null
        if nginx -t -c "$t5/etc/nginx/nginx.conf" -p "$t5" 2>&1 | sed 's/^/     /'; then
            pass "nginx 接受 $mode 模式的渲染结果"
        else
            fail "nginx -t 拒绝了 $mode 模式的渲染结果"
        fi
    done
else
    info "跳过：未找到 nginx / openssl，或 /var/log/nginx 不可写（CI 里用 root + nginx 包运行）"
fi

echo
if [ "$FAILED" = "0" ]; then
    echo "✅ docker/nginx 渲染层测试全部通过"
else
    echo "❌ docker/nginx 渲染层测试存在失败项" >&2
fi
exit "$FAILED"
