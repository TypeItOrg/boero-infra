#!/bin/sh
set -eu

environment="${1:-}"
service="${2:-}"
previous_file=".deploy/$environment/$service.previous"

if [ ! -f "$previous_file" ]; then
  echo "No previous version recorded for $service in $environment" >&2
  exit 1
fi

previous_version="$(cat "$previous_file")"
exec "$(dirname "$0")/deploy-service.sh" "$environment" "$service" "$previous_version"

