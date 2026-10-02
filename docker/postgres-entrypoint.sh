#!/bin/sh
set -eu

app_db="${DB_NAME:-${POSTGRES_DB:-postgres}}"

docker-entrypoint.sh postgres &
postgres_pid=$!

cleanup() {
  kill "$postgres_pid" 2>/dev/null || true
  wait "$postgres_pid" 2>/dev/null || true
}

trap cleanup INT TERM

# The official entrypoint starts a temporary Unix-socket-only server during initdb.
# Wait for the final TCP server before CREATE DATABASE, otherwise its initialization
# shutdown can interrupt the CREATE on a fresh QA volume.
until pg_isready -h 127.0.0.1 -U "$POSTGRES_USER" -d postgres >/dev/null 2>&1; do
  sleep 1
done

db_exists="$(psql -U "$POSTGRES_USER" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '$app_db'")"

if [ "$db_exists" != "1" ]; then
  psql -U "$POSTGRES_USER" -d postgres -c "CREATE DATABASE \"$app_db\""
fi

wait "$postgres_pid"

