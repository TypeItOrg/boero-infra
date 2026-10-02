#!/bin/sh
set -eu

environment="${1:-}"
case "$environment" in
  qa|staging) suffix="$environment" ;;
  production) suffix=prod ;;
  *) echo "Environment must be qa, staging or production" >&2; exit 1 ;;
esac

# Keep historical staging/prod storage names; QA has independent persistent resources.
for name in ui-next-cache api-postgres-data api-redis-data api-logs api-enrollment-storage; do
  docker volume create "boero-$name-$suffix"
done
