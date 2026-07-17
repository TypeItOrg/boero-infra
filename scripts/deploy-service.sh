#!/bin/sh
set -eu

environment="${1:-}"
service="${2:-}"
version="${3:-}"

case "$environment" in
  staging|production) ;;
  *) echo "Environment must be staging or production" >&2; exit 1 ;;
esac

case "$service" in
  ui) version_key="UI_VERSION" ;;
  api) version_key="API_VERSION" ;;
  *) echo "Service must be ui or api" >&2; exit 1 ;;
esac

case "$version" in
  sha-*) commit_sha="${version#sha-}" ;;
  *) echo "Version must use the immutable sha-<commit> format" >&2; exit 1 ;;
esac

case "$commit_sha" in
  ""|*[!0-9a-f]*) echo "Version must contain a lowercase hexadecimal commit SHA" >&2; exit 1 ;;
esac

env_file=".env.$environment"
compose_file="compose.$environment.yaml"
state_dir=".deploy/$environment"
previous_file="$state_dir/$service.previous"

if [ ! -f "$env_file" ]; then
  echo "Missing $env_file" >&2
  exit 1
fi

mkdir -p "$state_dir"

current_version="$(sed -n "s/^${version_key}=//p" "$env_file" | tail -n 1)"
if [ -z "$current_version" ]; then
  echo "Missing $version_key in $env_file" >&2
  exit 1
fi

update_version() {
  target_version="$1"
  temporary_file="$(mktemp)"
  awk -v key="$version_key" -v value="$target_version" '
    index($0, key "=") == 1 { print key "=" value; next }
    { print }
  ' "$env_file" > "$temporary_file"
  install -m 600 "$temporary_file" "$env_file"
  rm -f "$temporary_file"
}

deploy_version() {
  target_version="$1"
  update_version "$target_version" || return
  docker compose --env-file "$env_file" -f "$compose_file" pull "$service" || return
  docker compose --env-file "$env_file" -f "$compose_file" up \
    -d --no-deps --wait --wait-timeout 120 "$service"
}

if [ "$current_version" = "$version" ]; then
  echo "$service already uses $version; verifying deployment"
fi

printf '%s\n' "$current_version" > "$previous_file"

if deploy_version "$version"; then
  echo "Deployed $service $version to $environment"
  exit 0
fi

echo "Deployment failed; restoring $service $current_version" >&2
if deploy_version "$current_version"; then
  echo "Rollback completed for $service" >&2
else
  echo "Rollback failed for $service; manual recovery is required" >&2
fi
exit 1
