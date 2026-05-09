#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${ROOT_DIR}/docker-compose.yml"

if ! command -v docker >/dev/null 2>&1; then
	printf "Docker CLI was not found. Install Docker Desktop and re-run this script.\n" >&2
	exit 1
fi

if ! docker info >/dev/null 2>&1; then
	printf "Docker daemon is not running. Start Docker Desktop and re-run this script.\n" >&2
	exit 1
fi

docker compose -f "${COMPOSE_FILE}" up -d postgres redis

wait_for_postgres() {
	for _ in {1..40}; do
		if docker compose -f "${COMPOSE_FILE}" exec -T postgres \
			pg_isready -U asset_tracker -d asset_tracker >/dev/null 2>&1; then
			return 0
		fi
		sleep 1
	done
	printf "Postgres did not become healthy within 40 seconds.\n" >&2
	return 1
}

wait_for_redis() {
	for _ in {1..40}; do
		if [ "$(docker compose -f "${COMPOSE_FILE}" exec -T redis redis-cli ping 2>/dev/null)" = "PONG" ]; then
			return 0
		fi
		sleep 1
	done
	printf "Redis did not become healthy within 40 seconds.\n" >&2
	return 1
}

wait_for_postgres
wait_for_redis

export ASSET_TRACKER_DATABASE_URL="${ASSET_TRACKER_DATABASE_URL:-postgresql+psycopg://asset_tracker:asset_tracker@127.0.0.1:5433/asset_tracker}"
export ASSET_TRACKER_REDIS_URL="${ASSET_TRACKER_REDIS_URL:-redis://127.0.0.1:6380/0}"
export ASSET_TRACKER_TEST_DATABASE_URL="${ASSET_TRACKER_TEST_DATABASE_URL:-postgresql+psycopg://asset_tracker:asset_tracker@127.0.0.1:5433/asset_tracker_test}"
export ASSET_TRACKER_TEST_DATABASE_ADMIN_URL="${ASSET_TRACKER_TEST_DATABASE_ADMIN_URL:-postgresql+psycopg://asset_tracker:asset_tracker@127.0.0.1:5433/postgres}"

cd "${ROOT_DIR}/backend"

uv run python -m compileall app
uv run ruff check app tests ../scripts
uv run python ../scripts/check_backend_decimal_guard.py
uv run python ../scripts/check_pyright_ratchet.py --baseline "${ASSET_TRACKER_PYRIGHT_BASELINE:-0}" -- app tests

uv run pytest "$@"
