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

if [ "${FAIL_PREFLIGHT:-false}" = "true" ]; then
  case "$*" in
    *" config --quiet") exit 1 ;;
  esac
fi

if [ "${FAIL_NEW_VERSION:-false}" = "true" ]; then
  case "$*" in
    *" up "*)
      version="$(sed -n 's/^UI_VERSION=//p' .env.staging)"
      [ "$version" != "sha-bbbb" ] || exit 1
      ;;
  esac
fi
EOF
chmod +x "$test_dir/bin/docker"

run_deploy() {
  (
    cd "$test_dir"
    PATH="$test_dir/bin:$PATH" ./scripts/deploy-service.sh staging ui "$1"
  )
}

assert_versions() {
  expected_ui_version="$1"
  expected_api_version="$2"
  grep -qx "UI_VERSION=$expected_ui_version" "$test_dir/.env.staging"
  grep -qx "API_VERSION=$expected_api_version" "$test_dir/.env.staging"
}

write_environment() {
  rm -f "$test_dir/.deploy/staging/ui.previous"
  cat > "$test_dir/.env.staging" <<'EOF'
DB_NAME=boero_staging
UI_IMAGE=ghcr.io/typeitorg/boero-ui
UI_VERSION=sha-aaaa
API_IMAGE=ghcr.io/typeitorg/boero-api
API_VERSION=sha-cccc
EOF
  chmod 600 "$test_dir/.env.staging"
  : > "$test_dir/compose.yaml"
  : > "$test_dir/compose.staging.yaml"
}

write_environment
run_deploy sha-bbbb
assert_versions sha-bbbb sha-cccc
grep -qx 'DB_NAME=boero_staging' "$test_dir/.env.staging"
grep -qx 'sha-aaaa' "$test_dir/.deploy/staging/ui.previous"

write_environment
if FAIL_NEW_VERSION=true run_deploy sha-bbbb; then
  echo "Expected the unhealthy deployment to fail" >&2
  exit 1
fi
assert_versions sha-aaaa sha-cccc

write_environment
if FAIL_PREFLIGHT=true run_deploy sha-bbbb; then
  echo "Expected the invalid configuration to fail preflight" >&2
  exit 1
fi
assert_versions sha-aaaa sha-cccc
test ! -e "$test_dir/.deploy/staging/ui.previous"

grep -q 'api-logs:/app/logs' "$root_dir/compose.yaml"
grep -q 'boero-api-logs-staging' "$root_dir/compose.staging.yaml"
grep -q 'boero-api-logs-prod' "$root_dir/compose.production.yaml"

echo "deploy-service tests passed"
