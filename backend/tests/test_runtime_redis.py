from __future__ import annotations

import os

import pytest
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import inspect
from sqlalchemy import text

import app.database as database
from app import runtime_state

CURRENT_SCHEMA_REVISION = "20260507_01"
HOT_QUERY_INDEXES = {
	"securityholdingtransaction": {
		"ix_securityholdingtransaction_user_symbol_market_traded_created_id",
		"ix_securityholdingtransaction_user_traded_created_id",
	},
	"portfoliosnapshot": {"ix_portfoliosnapshot_user_created"},
	"holdingperformancesnapshot": {
		"ix_holdingperformancesnapshot_user_scope_symbol_created",
	},
	"realtimeportfoliosnapshot": {"ix_realtimeportfoliosnapshot_user_created"},
	"realtimeholdingperformancesnapshot": {
		"ix_realtimeholdingperformancesnapshot_user_scope_symbol_created",
	},
	"assetmutationaudit": {
		"ix_assetmutationaudit_user_created",
		"ix_assetmutationaudit_user_agent_task_created",
	},
	"userfeedback": {
		"ix_userfeedback_source_status_priority_created_id",
		"ix_userfeedback_status_priority_created_id",
		"ix_userfeedback_user_created_id",
	},
}


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
