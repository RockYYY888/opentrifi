from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal
from fnmatch import fnmatch
import os

import pytest
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import inspect
from sqlalchemy import text

import app.database as database
from app import runtime_state
from app.schemas import DashboardResponse

CURRENT_SCHEMA_REVISION = "20260507_01"
HOT_QUERY_INDEXES = {
	"securityholdingtransaction": {
		"ix_sht_user_symbol_market_traded_created_id",
		"ix_sht_user_traded_created_id",
	},
	"portfoliosnapshot": {"ix_portfoliosnapshot_user_created"},
	"holdingperformancesnapshot": {
		"ix_hps_user_scope_symbol_created",
	},
	"realtimeportfoliosnapshot": {"ix_rps_user_created"},
	"realtimeholdingperformancesnapshot": {
		"ix_rhps_user_scope_symbol_created",
	},
	"assetmutationaudit": {
		"ix_ama_user_created",
		"ix_ama_user_agent_task_created",
	},
	"userfeedback": {
		"ix_userfeedback_source_status_priority_created_id",
		"ix_userfeedback_status_priority_created_id",
		"ix_userfeedback_user_created_id",
	},
}


class InMemoryRedis:
	def __init__(self) -> None:
		self.values: dict[str, bytes] = {}
		self.sets: dict[str, set[str]] = {}
		self.lists: dict[str, list[bytes]] = {}

	def _normalize_key(self, key: str | bytes) -> str:
		return key.decode("utf-8") if isinstance(key, bytes) else key

	def ping(self) -> bool:
		return True

	def set(self, key: str | bytes, value: bytes, ex: int | None = None) -> None:
		del ex
		self.values[self._normalize_key(key)] = value

	def get(self, key: str | bytes) -> bytes | None:
		return self.values.get(self._normalize_key(key))

	def delete(self, *keys: str | bytes) -> int:
		deleted_count = 0
		for key in keys:
			normalized_key = self._normalize_key(key)
			if normalized_key in self.values:
				del self.values[normalized_key]
				deleted_count += 1
			if normalized_key in self.sets:
				del self.sets[normalized_key]
				deleted_count += 1
			if normalized_key in self.lists:
				del self.lists[normalized_key]
				deleted_count += 1
		return deleted_count

	def scan_iter(self, pattern: str):
		all_keys = {
			*self.values.keys(),
			*self.sets.keys(),
			*self.lists.keys(),
		}
		for key in sorted(all_keys):
			if fnmatch(key, pattern):
				yield key.encode("utf-8")

	def sadd(self, key: str, value: str) -> None:
		self.sets.setdefault(key, set()).add(value)

	def srem(self, key: str, value: str) -> None:
		self.sets.setdefault(key, set()).discard(value)

	def sismember(self, key: str, value: str) -> bool:
		return value in self.sets.get(key, set())

	def smembers(self, key: str) -> set[bytes]:
		return {value.encode("utf-8") for value in self.sets.get(key, set())}

	def scard(self, key: str) -> int:
		return len(self.sets.get(key, set()))

	def rpush(self, key: str, value: bytes) -> None:
		self.lists.setdefault(key, []).append(value)

	def lpop(self, key: str) -> bytes | None:
		values = self.lists.setdefault(key, [])
		if not values:
			return None
		return values.pop(0)

	def blpop(self, key: str, timeout: int) -> tuple[str, bytes] | None:
		del timeout
		value = self.lpop(key)
		if value is None:
			return None
		return key, value

	def llen(self, key: str) -> int:
		return len(self.lists.get(key, []))


def _empty_dashboard() -> DashboardResponse:
	return DashboardResponse(
		server_today=date(2026, 5, 7),
		total_value_cny=0,
		cash_value_cny=0,
		holdings_value_cny=0,
		fixed_assets_value_cny=0,
		liabilities_value_cny=0,
		other_assets_value_cny=0,
		cash_accounts=[],
		holdings=[],
		fixed_assets=[],
		liabilities=[],
		other_assets=[],
		allocation=[],
		hour_series=[],
		day_series=[],
		month_series=[],
		year_series=[],
		holdings_return_hour_series=[],
		holdings_return_day_series=[],
		holdings_return_month_series=[],
		holdings_return_year_series=[],
		holding_return_series=[],
		warnings=[],
	)


def test_validate_runtime_redis_connection_raises_when_ping_fails(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	class FailingRedisClient:
		def ping(self) -> bool:
			raise RedisConnectionError("unreachable")

	monkeypatch.setattr(runtime_state, "redis_url", "redis://127.0.0.1:6380/0")
	monkeypatch.setattr(runtime_state, "redis_client", FailingRedisClient())

	with pytest.raises(RuntimeError, match="Unable to connect to Redis"):
		runtime_state.validate_runtime_redis_connection()


def test_runtime_json_serializer_round_trips_runtime_state_objects() -> None:
	redis_client = InMemoryRedis()
	now = datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)
	dashboard_cache = runtime_state.RedisBackedDict[str, runtime_state.DashboardCacheEntry](
		redis_client,  # type: ignore[arg-type]
		f"{runtime_state.RUNTIME_KEY_PREFIX}:test-dashboard-cache",
	)
	login_attempts = runtime_state.RedisBackedDict[
		tuple[str, str],
		runtime_state.LoginAttemptState,
	](
		redis_client,  # type: ignore[arg-type]
		f"{runtime_state.RUNTIME_KEY_PREFIX}:test-login-attempts",
	)
	live_portfolio = runtime_state.RedisBackedDict[str, runtime_state.LivePortfolioState](
		redis_client,  # type: ignore[arg-type]
		f"{runtime_state.RUNTIME_KEY_PREFIX}:test-live-portfolio",
	)
	live_returns = runtime_state.RedisBackedDict[str, runtime_state.LiveHoldingsReturnState](
		redis_client,  # type: ignore[arg-type]
		f"{runtime_state.RUNTIME_KEY_PREFIX}:test-live-returns",
	)
	queue = runtime_state.RedisBackedQueue(
		redis_client,  # type: ignore[arg-type]
		f"{runtime_state.RUNTIME_KEY_PREFIX}:test-queue",
	)
	queued_users = runtime_state.RedisBackedSet(
		redis_client,  # type: ignore[arg-type]
		f"{runtime_state.RUNTIME_KEY_PREFIX}:test-queued-users",
	)
	scalar = runtime_state.RedisBackedScalar[datetime](
		redis_client,  # type: ignore[arg-type]
		f"{runtime_state.RUNTIME_KEY_PREFIX}:test-scalar",
	)

	dashboard_cache["tester"] = runtime_state.DashboardCacheEntry(
		dashboard=_empty_dashboard(),
		generated_at=now,
	)
	login_attempts[("127.0.0.1", "tester")] = runtime_state.LoginAttemptState(
		attempt_timestamps=[now],
		consecutive_failed_attempts=2,
		last_attempt_at=now,
	)
	live_portfolio["tester"] = runtime_state.LivePortfolioState(
		hour_bucket=now,
		latest_value_cny=Decimal("123.45"),
		latest_generated_at=now,
		has_assets_in_bucket=True,
	)
	live_returns["tester"] = runtime_state.LiveHoldingsReturnState(
		hour_bucket=now,
		latest_generated_at=now,
		aggregate_return_pct=Decimal("1.25"),
		holding_points=(
			runtime_state.LiveHoldingReturnPoint(
				symbol="AAPL",
				name="Apple",
				return_pct=Decimal("1.25"),
			),
		),
		has_tracked_holdings_in_bucket=True,
	)
	queue.put_nowait("tester")
	queued_users.add("tester")
	scalar.set(now)

	assert dashboard_cache["tester"].dashboard.server_today == date(2026, 5, 7)
	assert login_attempts[("127.0.0.1", "tester")].consecutive_failed_attempts == 2
	assert live_portfolio["tester"].latest_value_cny == Decimal("123.45")
	assert live_returns["tester"].holding_points[0].symbol == "AAPL"
	assert queue.get_nowait() == "tester"
	assert "tester" in queued_users
	assert scalar.get() == now
	assert all(raw_value.startswith(b'{"') for raw_value in redis_client.values.values())


def test_runtime_queue_async_get_uses_json_serializer() -> None:
	redis_client = InMemoryRedis()
	queue = runtime_state.RedisBackedQueue(
		redis_client,  # type: ignore[arg-type]
		f"{runtime_state.RUNTIME_KEY_PREFIX}:test-async-queue",
	)

	queue.put_nowait("queued-user")

	assert asyncio.run(queue.get()) == "queued-user"


def test_runtime_json_deserializer_ignores_legacy_pickle_payloads(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	redis_client = InMemoryRedis()
	legacy_key = f"{runtime_state.LEGACY_RUNTIME_KEY_PREFIX}:dashboard-cache:old"
	current_key = f"{runtime_state.RUNTIME_KEY_PREFIX}:dashboard-cache:old"
	redis_client.set(legacy_key, b"\\x80\\x05legacy-pickle")
	redis_client.set(current_key, b"\\x80\\x05legacy-pickle")
	monkeypatch.setattr(runtime_state, "redis_client", redis_client)

	runtime_state.clear_legacy_runtime_keys()

	assert legacy_key not in redis_client.values
	assert current_key in redis_client.values
	assert runtime_state._deserialize(redis_client.get(current_key)) is None


def test_init_db_stamps_legacy_schema_without_version_table(
	empty_postgres_engine,
	postgres_database_url: str,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	engine = empty_postgres_engine
	database.SQLModel.metadata.create_all(engine)

	monkeypatch.setattr(database, "DATABASE_URL", postgres_database_url)
	monkeypatch.setattr(database, "engine", engine)

	database.init_db()

	with engine.connect() as connection:
		version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()

	assert version == CURRENT_SCHEMA_REVISION


def test_init_db_rejects_partial_legacy_schema_without_version_table(
	empty_postgres_engine,
	postgres_database_url: str,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	engine = empty_postgres_engine
	with engine.begin() as connection:
		connection.execute(text("CREATE TABLE useraccount (username TEXT PRIMARY KEY)"))

	monkeypatch.setattr(database, "DATABASE_URL", postgres_database_url)
	monkeypatch.setattr(database, "engine", engine)

	with pytest.raises(RuntimeError, match="Missing tables"):
		database.init_db()


def test_init_db_applies_migrations_to_empty_database(
	empty_postgres_engine,
	postgres_database_url: str,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	engine = empty_postgres_engine

	monkeypatch.setattr(database, "DATABASE_URL", postgres_database_url)
	monkeypatch.setattr(database, "engine", engine)

	database.init_db()

	with engine.connect() as connection:
		table_names = set(inspect(connection).get_table_names())
		version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()

	assert "useraccount" in table_names
	assert "cashaccount" in table_names
	assert version == CURRENT_SCHEMA_REVISION


def test_init_db_creates_hot_query_composite_indexes(
	empty_postgres_engine,
	postgres_database_url: str,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	engine = empty_postgres_engine

	monkeypatch.setattr(database, "DATABASE_URL", postgres_database_url)
	monkeypatch.setattr(database, "engine", engine)

	database.init_db()

	with engine.connect() as connection:
		inspector = inspect(connection)
		for table_name, expected_indexes in HOT_QUERY_INDEXES.items():
			actual_indexes = {
				index["name"]
				for index in inspector.get_indexes(table_name)
			}
			assert expected_indexes <= actual_indexes


@pytest.mark.integration
def test_configured_redis_endpoint_is_reachable() -> None:
	redis_url = os.getenv("ASSET_TRACKER_REDIS_URL", runtime_state.redis_url)
	client = Redis.from_url(redis_url)

	try:
		assert client.ping() is True
	finally:
		client.close()
