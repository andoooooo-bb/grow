#!/usr/bin/env bash
# Grow ローカル開発DB（PostgreSQL）の起動/停止/リセット。
#
# Docker daemon が生きていれば docker compose（postgres:16）を使い、
# 無ければ pg_ctl で .pgdata/ にエフェメラルクラスタを initdb して起動する。
# いずれも port 54329 / db=grow / user=grow / password=grow で揃える
# （DATABASE_URL=postgresql://grow:grow@localhost:54329/grow）。
#
# Usage: scripts/devdb.sh {start|stop|reset}

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PGDATA_DIR="$ROOT_DIR/.pgdata"
LOG_FILE="$PGDATA_DIR/postgres.log"
DB_PORT=54329
DB_NAME=grow
DB_USER=grow
DB_PASSWORD=grow

docker_available() {
  command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1
}

# ---- docker compose backend -------------------------------------------------

docker_start() {
  docker compose -f "$ROOT_DIR/docker-compose.yml" up -d db
  echo "devdb: started (docker compose, port $DB_PORT)"
}

docker_stop() {
  docker compose -f "$ROOT_DIR/docker-compose.yml" stop db
  echo "devdb: stopped (docker compose)"
}

docker_reset() {
  docker compose -f "$ROOT_DIR/docker-compose.yml" down -v
  docker_start
}

# ---- pg_ctl backend ----------------------------------------------------------

pg_running() {
  pg_ctl -D "$PGDATA_DIR" status >/dev/null 2>&1
}

pg_start() {
  if [ ! -d "$PGDATA_DIR" ]; then
    echo "devdb: initdb -> $PGDATA_DIR"
    initdb -D "$PGDATA_DIR" -U "$DB_USER" --auth=trust --encoding=UTF8 --locale=C >/dev/null
  fi
  if pg_running; then
    echo "devdb: already running (pg_ctl, port $DB_PORT)"
  else
    pg_ctl -D "$PGDATA_DIR" -l "$LOG_FILE" \
      -o "-p $DB_PORT -c listen_addresses=localhost" -w start >/dev/null
    echo "devdb: started (pg_ctl, port $DB_PORT)"
  fi
  # DB / パスワードを冪等に整える（trust認証だがdocker構成とURLを揃える）
  psql -h localhost -p "$DB_PORT" -U "$DB_USER" -d postgres -qAtc \
    "ALTER USER $DB_USER PASSWORD '$DB_PASSWORD'" >/dev/null
  if [ "$(psql -h localhost -p "$DB_PORT" -U "$DB_USER" -d postgres -qAtc \
      "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'")" != "1" ]; then
    createdb -h localhost -p "$DB_PORT" -U "$DB_USER" "$DB_NAME"
    echo "devdb: created database '$DB_NAME'"
  fi
}

pg_stop() {
  if pg_running; then
    pg_ctl -D "$PGDATA_DIR" -m fast -w stop >/dev/null
    echo "devdb: stopped (pg_ctl)"
  else
    echo "devdb: not running"
  fi
}

pg_reset() {
  pg_stop
  rm -rf "$PGDATA_DIR"
  pg_start
}

# ---- entrypoint --------------------------------------------------------------

cmd="${1:-}"
case "$cmd" in
  start|stop|reset) ;;
  *)
    echo "Usage: $0 {start|stop|reset}" >&2
    exit 1
    ;;
esac

if docker_available; then
  "docker_$cmd"
else
  "pg_$cmd"
fi
