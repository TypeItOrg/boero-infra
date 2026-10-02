#!/bin/sh
set -eu

environment="${1:-}"
case "$environment" in
  qa|staging|production) ;;
  *) echo "Environment must be qa, staging or production" >&2; exit 1 ;;
esac

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo_dir"
env_file=".env.$environment"
if [ ! -f "$env_file" ]; then
  echo "Missing $env_file" >&2
  exit 1
fi

run_compose() {
  docker compose --env-file "$env_file" -f compose.yaml -f "compose.$environment.yaml" "$@"
}

# Validate before volume creation, including when invoked through make -j.
run_compose config --quiet
"$repo_dir/scripts/prepare-volumes.sh" "$environment"
run_compose pull
run_compose run --rm --no-deps api-storage-init
run_compose up -d --wait --wait-timeout 600
