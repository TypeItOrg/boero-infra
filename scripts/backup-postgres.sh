#!/bin/sh
set -eu

environment="${1:-}"

case "$environment" in
  staging|production) ;;
  *) echo "Environment must be staging or production" >&2; exit 1 ;;
esac

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo_dir"

env_file=".env.$environment"
environment_compose_file="compose.$environment.yaml"

if [ ! -f "$env_file" ]; then
  echo "Missing $env_file" >&2
  exit 1
fi

read_env_value() {
  key="$1"
  sed -n "s/^${key}=//p" "$env_file" | tail -n 1
}

backup_root="${BACKUP_DIR:-$(read_env_value BACKUP_DIR)}"
retention_days="${BACKUP_RETENTION_DAYS:-$(read_env_value BACKUP_RETENTION_DAYS)}"
backup_root="${backup_root:-/var/backups/boero}"
retention_days="${retention_days:-7}"

case "$backup_root" in
  /*) ;;
  *) echo "BACKUP_DIR must be an absolute path" >&2; exit 1 ;;
esac

case "$retention_days" in
  ''|*[!0-9]*) echo "BACKUP_RETENTION_DAYS must be a positive integer" >&2; exit 1 ;;
  0) echo "BACKUP_RETENTION_DAYS must be greater than zero" >&2; exit 1 ;;
esac

backup_dir="$backup_root/$environment"
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
final_file="$backup_dir/boero-$environment-$timestamp.dump"
temporary_file="$backup_dir/.boero-$environment-$timestamp.dump.tmp"

umask 077
mkdir -p "$backup_dir"
trap 'rm -f "$temporary_file"' EXIT HUP INT TERM

run_compose() {
  docker compose \
    --env-file "$env_file" \
    -f compose.yaml \
    -f "$environment_compose_file" \
    "$@"
}

run_compose exec -T postgres sh -c \
  'exec pg_dump --format=custom --dbname="$DB_NAME" --username="$POSTGRES_USER"' \
  > "$temporary_file"

run_compose exec -T postgres pg_restore --list < "$temporary_file" > /dev/null

chmod 600 "$temporary_file"
mv "$temporary_file" "$final_file"
trap - EXIT HUP INT TERM

# Retention only runs after the new archive has passed pg_restore validation.
find "$backup_dir" -type f -name "boero-$environment-*.dump" -mtime "+$retention_days" -delete

echo "Validated PostgreSQL backup created at $final_file"
