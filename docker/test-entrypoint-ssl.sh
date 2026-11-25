#!/bin/bash
# Isolated tests for docker/entrypoint.sh SSL certificate handling (R1111-07).
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "$0")" && pwd -P)
REPO_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd -P)
WORK=$(mktemp -d)
trap 'rm -rf -- "$WORK"' EXIT

export NGINX_TEMPLATE_DIR="$REPO_ROOT/docker/nginx"
# shellcheck source=docker/entrypoint.sh
. "$REPO_ROOT/docker/entrypoint.sh"

FAILED=0
pass() { printf '  PASS: %s\n' "$1"; }
fail() { printf '  FAIL: %s\n' "$1" >&2; FAILED=1; }

_make_openssl_cnf() {
    local cnf_path="$1"
    local cn="$2"
    cat > "$cnf_path" <<EOF
[req]
prompt = no
distinguished_name = dn

[dn]
CN = $cn
EOF
}

make_valid_pair() {
    local dir="$1"
    mkdir -p "$dir"
    local cnf="$dir/openssl.cnf"
    _make_openssl_cnf "$cnf" "valid.example"
    openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
        -config "$cnf" \
        -keyout "$dir/N.E.K.O.key" -out "$dir/N.E.K.O.crt" >/dev/null 2>&1
    rm -f "$cnf"
}

echo "== SSL-1 fresh directory generates self-signed pair =="
s1="$WORK/s1"
mkdir -p "$s1"
if ensure_ssl_certificates "$s1/N.E.K.O.crt" "$s1/N.E.K.O.key" >/dev/null 2>&1 &&
   validate_ssl_certificate "$s1/N.E.K.O.crt" "$s1/N.E.K.O.key" >/dev/null 2>&1; then
    pass "fresh directory generated valid SSL pair"
else
    fail "fresh directory failed to generate valid SSL pair"
fi

echo "== SSL-2 default AUTO_REGENERATE_CERT=0 preserves encrypted/mismatched pair =="
s2="$WORK/s2"
mkdir -p "$s2"
_make_openssl_cnf "$s2/openssl.cnf" "encrypted.example"
openssl genrsa -aes256 -passout pass:secret123 -out "$s2/N.E.K.O.key" 2048 >/dev/null 2>&1
openssl req -new -x509 -key "$s2/N.E.K.O.key" -passin pass:secret123 -days 1 \
    -config "$s2/openssl.cnf" -out "$s2/N.E.K.O.crt" >/dev/null 2>&1
rm -f "$s2/openssl.cnf"
orig_crt=$(cksum "$s2/N.E.K.O.crt")
orig_key=$(cksum "$s2/N.E.K.O.key")
if (unset AUTO_REGENERATE_CERT DISABLE_SSL; ensure_ssl_certificates "$s2/N.E.K.O.crt" "$s2/N.E.K.O.key") >/dev/null 2>&1; then
    fail "passphrase-protected key should fail startup by default"
else
    if [ "$(cksum "$s2/N.E.K.O.crt")" = "$orig_crt" ] && [ "$(cksum "$s2/N.E.K.O.key")" = "$orig_key" ]; then
        pass "default mode preserved encrypted key and cert untouched"
    else
        fail "default mode modified or deleted encrypted key/cert"
    fi
fi

echo "== SSL-3 default AUTO_REGENERATE_CERT=0 preserves partial cert-only file =="
s3="$WORK/s3"
make_valid_pair "$s3"
rm -f "$s3/N.E.K.O.key"
orig_partial=$(cksum "$s3/N.E.K.O.crt")
if (unset AUTO_REGENERATE_CERT DISABLE_SSL; ensure_ssl_certificates "$s3/N.E.K.O.crt" "$s3/N.E.K.O.key") >/dev/null 2>&1; then
    fail "partial cert-only state should fail startup by default"
else
    if [ -f "$s3/N.E.K.O.crt" ] && [ "$(cksum "$s3/N.E.K.O.crt")" = "$orig_partial" ] && [ ! -e "$s3/N.E.K.O.key" ]; then
        pass "default mode preserved partial cert untouched"
    else
        fail "default mode deleted or replaced partial cert"
    fi
fi

echo "== SSL-4 DISABLE_SSL=1 continues while preserving invalid/partial cert =="
if (unset AUTO_REGENERATE_CERT; DISABLE_SSL=1 ensure_ssl_certificates "$s3/N.E.K.O.crt" "$s3/N.E.K.O.key") >/dev/null 2>&1; then
    if [ -f "$s3/N.E.K.O.crt" ] && [ "$(cksum "$s3/N.E.K.O.crt")" = "$orig_partial" ]; then
        pass "DISABLE_SSL=1 continued and preserved partial cert"
    else
        fail "DISABLE_SSL=1 altered partial cert"
    fi
else
    fail "DISABLE_SSL=1 should return 0"
fi

echo "== SSL-5 AUTO_REGENERATE_CERT=1 archives invalid files to .invalid-<ts> and regenerates =="
s5="$WORK/s5"
mkdir -p "$s5"
printf 'corrupt-cert\n' > "$s5/N.E.K.O.crt"
printf 'corrupt-key\n' > "$s5/N.E.K.O.key"
if (AUTO_REGENERATE_CERT=1 ensure_ssl_certificates "$s5/N.E.K.O.crt" "$s5/N.E.K.O.key") >/dev/null 2>&1; then
    archived_crt=$(ls "$s5"/N.E.K.O.crt.invalid-* 2>/dev/null | head -1 || true)
    archived_key=$(ls "$s5"/N.E.K.O.key.invalid-* 2>/dev/null | head -1 || true)
    if [ -n "$archived_crt" ] && [ -n "$archived_key" ] &&
       grep -qF 'corrupt-cert' "$archived_crt" &&
       grep -qF 'corrupt-key' "$archived_key" &&
       validate_ssl_certificate "$s5/N.E.K.O.crt" "$s5/N.E.K.O.key" >/dev/null 2>&1; then
        pass "AUTO_REGENERATE_CERT=1 archived invalid pair and regenerated valid pair"
    else
        fail "AUTO_REGENERATE_CERT=1 did not archive invalid files or regenerate valid pair"
    fi
else
    fail "AUTO_REGENERATE_CERT=1 failed"
fi

echo "== SSL-6 failed archive refuses to overwrite existing SSL files =="
s6="$WORK/s6"
mkdir -p "$s6"
printf 'keep-existing-cert\n' > "$s6/N.E.K.O.crt"
printf 'keep-existing-key\n' > "$s6/N.E.K.O.key"
s6_crt_sum=$(cksum "$s6/N.E.K.O.crt")
s6_key_sum=$(cksum "$s6/N.E.K.O.key")
if ! (
    AUTO_REGENERATE_CERT=1
    mv() { return 1; }
    ensure_ssl_certificates "$s6/N.E.K.O.crt" "$s6/N.E.K.O.key"
) >/dev/null 2>&1; then
    if [ "$(cksum "$s6/N.E.K.O.crt")" = "$s6_crt_sum" ] && [ "$(cksum "$s6/N.E.K.O.key")" = "$s6_key_sum" ]; then
        pass "AUTO_REGENERATE_CERT=1 aborted without overwriting when archive mv failed"
    else
        fail "AUTO_REGENERATE_CERT=1 overwrote original files after archive mv failed"
    fi
else
    fail "AUTO_REGENERATE_CERT=1 should fail when archive mv fails"
fi

exit "$FAILED"
