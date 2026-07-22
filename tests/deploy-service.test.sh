#!/bin/sh
set -eu

root_dir="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
test_dir="$(mktemp -d)"
trap 'rm -rf "$test_dir"' EXIT

mkdir -p "$test_dir/bin" "$test_dir/scripts"
cp "$root_dir/scripts/deploy-service.sh" "$test_dir/scripts/deploy-service.sh"

cat > "$test_dir/bin/docker" <<'EOF'
#!/bin/sh
set -eu

if [ "${FAIL_NEW_VERSION:-false}" = "true" ] && [ "$*" != "${*% up *}" ]; then
  version="$(sed -n 's/^UI_VERSION=//p' .env.staging)"
  [ "$version" != "sha-bbbb" ] || exit 1
fi
EOF
chmod +x "$test_dir/bin/docker"

run_deploy() {
  (
    cd "$test_dir"
    PATH="$test_dir/bin:$PATH" ./scripts/deploy-service.sh staging ui "$1"
  )
}

write_environment() {
  cat > "$test_dir/.env.staging" <<'EOF'
UI_VERSION=sha-aaaa
API_VERSION=sha-cccc
EOF
  chmod 600 "$test_dir/.env.staging"
  : > "$test_dir/compose.staging.yaml"
}

write_environment
run_deploy sha-bbbb
grep -qx 'UI_VERSION=sha-bbbb' "$test_dir/.env.staging"
grep -qx 'API_VERSION=sha-cccc' "$test_dir/.env.staging"
grep -qx 'sha-aaaa' "$test_dir/.deploy/staging/ui.previous"

write_environment
if FAIL_NEW_VERSION=true run_deploy sha-bbbb; then
  echo "Expected the unhealthy deployment to fail" >&2
  exit 1
fi
grep -qx 'UI_VERSION=sha-aaaa' "$test_dir/.env.staging"
grep -qx 'API_VERSION=sha-cccc' "$test_dir/.env.staging"

grep -q 'api-logs:/app/logs' "$root_dir/compose.staging.yaml"
grep -q 'boero-api-logs-staging' "$root_dir/compose.staging.yaml"
grep -q 'api-logs:/app/logs' "$root_dir/compose.production.yaml"
grep -q 'boero-api-logs-prod' "$root_dir/compose.production.yaml"

echo "deploy-service tests passed"


