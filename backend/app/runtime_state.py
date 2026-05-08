from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator, MutableMapping, MutableSet
from contextlib import asynccontextmanager, contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import threading
from typing import cast

from redis import Redis
from redis.exceptions import LockError, RedisError

from app.redis_store import (
	RedisBackedDict,
	RedisBackedQueue,
	RedisBackedScalar,
	RedisBackedSet,
	RedisStoreClient,
)
from app.schemas import DashboardResponse
from app.settings import get_settings
from app.typed_json import (
	JsonValue,
	TypedJsonCodec,
	expect_bool,
	expect_decoded_type,
	expect_json_list,
	expect_json_object,
	expect_string,
	get_type_name,
	typed_object,
)


@dataclass(slots=True)
class DashboardCacheEntry:
	dashboard: DashboardResponse
	generated_at: datetime


@dataclass(slots=True)
class LivePortfolioState:
	hour_bucket: datetime
	latest_value_cny: Decimal
	latest_generated_at: datetime
	has_assets_in_bucket: bool


@dataclass(slots=True)
class LiveHoldingReturnPoint:
	symbol: str
	name: str
	return_pct: Decimal


@dataclass(slots=True)
class LiveHoldingsReturnState:
	hour_bucket: datetime
	latest_generated_at: datetime
	aggregate_return_pct: Decimal | None
	holding_points: tuple[LiveHoldingReturnPoint, ...]
	has_tracked_holdings_in_bucket: bool


@dataclass(slots=True)
class LoginAttemptState:
	attempt_timestamps: list[datetime]
	consecutive_failed_attempts: int
	last_attempt_at: datetime


RUNTIME_SERIALIZER_VERSION = 2
RUNTIME_KEY_PREFIX = "asset-tracker:v2:runtime"


def _datetime_to_json(value: datetime) -> str:
	return value.isoformat()


def _datetime_from_json(value: str) -> datetime:
	return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _to_json_value(value: object) -> JsonValue:
	if isinstance(value, datetime):
		return typed_object("datetime", value=_datetime_to_json(value))
	if isinstance(value, Decimal):
		return typed_object("decimal", value=str(value))
	if isinstance(value, tuple):
		return typed_object("tuple", items=[_to_json_value(item) for item in value])
	if isinstance(value, DashboardResponse):
		return typed_object(
			"DashboardResponse",
			value=_to_json_value(value.model_dump(mode="json")),
		)
	if isinstance(value, DashboardCacheEntry):
		return typed_object(
			"DashboardCacheEntry",
			dashboard=_to_json_value(value.dashboard),
			generated_at=_to_json_value(value.generated_at),
		)
	if isinstance(value, LivePortfolioState):
		return typed_object(
			"LivePortfolioState",
			hour_bucket=_to_json_value(value.hour_bucket),
			latest_value_cny=_to_json_value(value.latest_value_cny),
			latest_generated_at=_to_json_value(value.latest_generated_at),
			has_assets_in_bucket=value.has_assets_in_bucket,
		)
	if isinstance(value, LiveHoldingReturnPoint):
		return typed_object(
			"LiveHoldingReturnPoint",
			symbol=value.symbol,
			name=value.name,
			return_pct=_to_json_value(value.return_pct),
		)
	if isinstance(value, LiveHoldingsReturnState):
		return typed_object(
			"LiveHoldingsReturnState",
			hour_bucket=_to_json_value(value.hour_bucket),
			latest_generated_at=_to_json_value(value.latest_generated_at),
			aggregate_return_pct=_to_json_value(value.aggregate_return_pct),
			holding_points=[_to_json_value(point) for point in value.holding_points],
			has_tracked_holdings_in_bucket=value.has_tracked_holdings_in_bucket,
		)
	if isinstance(value, LoginAttemptState):
		return typed_object(
			"LoginAttemptState",
			attempt_timestamps=[_to_json_value(item) for item in value.attempt_timestamps],
			consecutive_failed_attempts=value.consecutive_failed_attempts,
			last_attempt_at=_to_json_value(value.last_attempt_at),
		)
	if isinstance(value, list):
		return [_to_json_value(item) for item in value]
	if isinstance(value, dict):
		return {
			str(key): _to_json_value(item)
			for key, item in value.items()
		}
	if value is None or isinstance(value, (str, int, bool)):
		return value

	raise TypeError(f"Unsupported runtime state value type: {type(value).__name__}")


def _from_json_value(value: JsonValue) -> object:
	if isinstance(value, list):
		return [_from_json_value(item) for item in value]
	if not isinstance(value, dict):
		return value

	value_type = get_type_name(value)
	if value_type is None:
		return {
			str(key): _from_json_value(item)
			for key, item in value.items()
		}
	if value_type == "datetime":
		return _datetime_from_json(expect_string(value["value"], context="datetime.value"))
	if value_type == "decimal":
		return Decimal(expect_string(value["value"], context="decimal.value"))
	if value_type == "tuple":
		return tuple(
			_from_json_value(item)
			for item in expect_json_list(value["items"], context="tuple.items")
		)
	if value_type == "DashboardResponse":
		return DashboardResponse.model_validate(expect_json_object(value["value"]))
	if value_type == "DashboardCacheEntry":
		dashboard = expect_decoded_type(
			_from_json_value(value["dashboard"]),
			DashboardResponse,
			context="DashboardCacheEntry.dashboard",
		)
		generated_at = expect_decoded_type(
			_from_json_value(value["generated_at"]),
			datetime,
			context="DashboardCacheEntry.generated_at",
		)
		return DashboardCacheEntry(
			dashboard=dashboard,
			generated_at=generated_at,
		)
	if value_type == "LivePortfolioState":
		hour_bucket = expect_decoded_type(
			_from_json_value(value["hour_bucket"]),
			datetime,
			context="LivePortfolioState.hour_bucket",
		)
		latest_value_cny = expect_decoded_type(
			_from_json_value(value["latest_value_cny"]),
			Decimal,
			context="LivePortfolioState.latest_value_cny",
		)
		latest_generated_at = expect_decoded_type(
			_from_json_value(value["latest_generated_at"]),
			datetime,
			context="LivePortfolioState.latest_generated_at",
		)
		return LivePortfolioState(
			hour_bucket=hour_bucket,
			latest_value_cny=latest_value_cny,
			latest_generated_at=latest_generated_at,
			has_assets_in_bucket=expect_bool(
				value["has_assets_in_bucket"],
				context="LivePortfolioState.has_assets_in_bucket",
			),
		)
	if value_type == "LiveHoldingReturnPoint":
		return_pct = expect_decoded_type(
			_from_json_value(value["return_pct"]),
			Decimal,
			context="LiveHoldingReturnPoint.return_pct",
		)
		return LiveHoldingReturnPoint(
			symbol=expect_string(value["symbol"], context="LiveHoldingReturnPoint.symbol"),
			name=expect_string(value["name"], context="LiveHoldingReturnPoint.name"),
			return_pct=return_pct,
		)
	if value_type == "LiveHoldingsReturnState":
		hour_bucket = expect_decoded_type(
			_from_json_value(value["hour_bucket"]),
			datetime,
			context="LiveHoldingsReturnState.hour_bucket",
		)
		latest_generated_at = expect_decoded_type(
			_from_json_value(value["latest_generated_at"]),
			datetime,
			context="LiveHoldingsReturnState.latest_generated_at",
		)
		aggregate_return_value = _from_json_value(value["aggregate_return_pct"])
		if aggregate_return_value is not None and not isinstance(aggregate_return_value, Decimal):
			raise ValueError("LiveHoldingsReturnState.aggregate_return_pct did not decode to Decimal.")
		holding_points = tuple(
			expect_decoded_type(
				_from_json_value(point),
				LiveHoldingReturnPoint,
				context="LiveHoldingsReturnState.holding_points[]",
			)
			for point in expect_json_list(
				value["holding_points"],
				context="LiveHoldingsReturnState.holding_points",
			)
		)
		return LiveHoldingsReturnState(
			hour_bucket=hour_bucket,
			latest_generated_at=latest_generated_at,
			aggregate_return_pct=aggregate_return_value,
			holding_points=holding_points,
			has_tracked_holdings_in_bucket=expect_bool(
				value["has_tracked_holdings_in_bucket"],
				context="LiveHoldingsReturnState.has_tracked_holdings_in_bucket",
			),
		)
	if value_type == "LoginAttemptState":
		attempt_timestamps = [
			expect_decoded_type(
				_from_json_value(item),
				datetime,
				context="LoginAttemptState.attempt_timestamps[]",
			)
			for item in expect_json_list(
				value["attempt_timestamps"],
				context="LoginAttemptState.attempt_timestamps",
			)
		]
		last_attempt_at = expect_decoded_type(
			_from_json_value(value["last_attempt_at"]),
			datetime,
			context="LoginAttemptState.last_attempt_at",
		)
		return LoginAttemptState(
			attempt_timestamps=attempt_timestamps,
			consecutive_failed_attempts=int(value["consecutive_failed_attempts"]),
			last_attempt_at=last_attempt_at,
		)

	raise ValueError(f"Unsupported runtime state JSON type: {value_type}")


_RUNTIME_CODEC = TypedJsonCodec[object](
	version=RUNTIME_SERIALIZER_VERSION,
	encode=_to_json_value,
	decode=_from_json_value,
)
_RUNTIME_STR_CODEC = cast(TypedJsonCodec[str], _RUNTIME_CODEC)
_RUNTIME_LOGIN_ATTEMPT_KEY_CODEC = cast(TypedJsonCodec[tuple[str, str]], _RUNTIME_CODEC)
_RUNTIME_DASHBOARD_CACHE_CODEC = cast(TypedJsonCodec[DashboardCacheEntry], _RUNTIME_CODEC)
_RUNTIME_LIVE_PORTFOLIO_CODEC = cast(TypedJsonCodec[LivePortfolioState], _RUNTIME_CODEC)
_RUNTIME_LIVE_HOLDINGS_RETURN_CODEC = cast(TypedJsonCodec[LiveHoldingsReturnState], _RUNTIME_CODEC)
_RUNTIME_LOGIN_ATTEMPT_CODEC = cast(TypedJsonCodec[LoginAttemptState], _RUNTIME_CODEC)
_RUNTIME_DATETIME_CODEC = cast(TypedJsonCodec[datetime], _RUNTIME_CODEC)


def _serialize(value: object) -> bytes:
	return _RUNTIME_CODEC.dumps(value)


def _deserialize(raw_value: bytes | None) -> object | None:
	return _RUNTIME_CODEC.loads(raw_value)


def _runtime_lock_key(name: str) -> str:
	return f"{RUNTIME_KEY_PREFIX}:lock:{name}"


settings = get_settings()
DEFAULT_LOCAL_REDIS_URL = "redis://127.0.0.1:6380/0"
DASHBOARD_CACHE_TTL_SECONDS = 10 * 60
LIVE_RUNTIME_STATE_TTL_SECONDS = 2 * 60 * 60
LOGIN_ATTEMPT_TTL_SECONDS = 24 * 60 * 60
redis_url = settings.redis_url_value() or DEFAULT_LOCAL_REDIS_URL
redis_client: Redis = Redis.from_url(redis_url)
redis_store_client = cast(RedisStoreClient, redis_client)

dashboard_cache: MutableMapping[str, DashboardCacheEntry] = RedisBackedDict[str, DashboardCacheEntry](
	redis_store_client,
	f"{RUNTIME_KEY_PREFIX}:dashboard-cache",
	key_codec=_RUNTIME_STR_CODEC,
	value_codec=_RUNTIME_DASHBOARD_CACHE_CODEC,
	ttl_seconds=DASHBOARD_CACHE_TTL_SECONDS,
)
live_portfolio_states: MutableMapping[str, LivePortfolioState] = RedisBackedDict[str, LivePortfolioState](
	redis_store_client,
	f"{RUNTIME_KEY_PREFIX}:live-portfolio",
	key_codec=_RUNTIME_STR_CODEC,
	value_codec=_RUNTIME_LIVE_PORTFOLIO_CODEC,
	ttl_seconds=LIVE_RUNTIME_STATE_TTL_SECONDS,
)
live_holdings_return_states: MutableMapping[str, LiveHoldingsReturnState] = RedisBackedDict[
	str,
	LiveHoldingsReturnState,
](
	redis_store_client,
	f"{RUNTIME_KEY_PREFIX}:live-holdings-return",
	key_codec=_RUNTIME_STR_CODEC,
	value_codec=_RUNTIME_LIVE_HOLDINGS_RETURN_CODEC,
	ttl_seconds=LIVE_RUNTIME_STATE_TTL_SECONDS,
)
login_attempt_states: MutableMapping[tuple[str, str], LoginAttemptState] = RedisBackedDict[
	tuple[str, str],
	LoginAttemptState,
](
	redis_store_client,
	f"{RUNTIME_KEY_PREFIX}:login-attempts",
	key_codec=_RUNTIME_LOGIN_ATTEMPT_KEY_CODEC,
	value_codec=_RUNTIME_LOGIN_ATTEMPT_CODEC,
	ttl_seconds=LOGIN_ATTEMPT_TTL_SECONDS,
)
snapshot_rebuild_queue = RedisBackedQueue[str](
	redis_store_client,
	f"{RUNTIME_KEY_PREFIX}:snapshot-rebuild-queue",
	value_codec=_RUNTIME_STR_CODEC,
)
snapshot_rebuild_users_in_queue: MutableSet[str] = RedisBackedSet(
	redis_store_client,
	f"{RUNTIME_KEY_PREFIX}:snapshot-rebuild-users",
)
_last_global_force_refresh_at_store = RedisBackedScalar[datetime](
	redis_store_client,
	f"{RUNTIME_KEY_PREFIX}:last-global-force-refresh-at",
	value_codec=_RUNTIME_DATETIME_CODEC,
)
_last_realtime_analytics_sampled_at_store = RedisBackedScalar[datetime](
	redis_store_client,
	f"{RUNTIME_KEY_PREFIX}:last-realtime-analytics-sampled-at",
	value_codec=_RUNTIME_DATETIME_CODEC,
)

dashboard_cache_lock = asyncio.Lock()
global_force_refresh_lock = asyncio.Lock()
holding_history_sync_lock = asyncio.Lock()
login_attempts_lock = threading.Lock()
current_agent_task_id_context: ContextVar[int | None] = ContextVar(
	"current_agent_task_id",
	default=None,
)
current_actor_source_context: ContextVar[str] = ContextVar(
	"current_actor_source",
	default="USER",
)
current_api_key_name_context: ContextVar[str | None] = ContextVar(
	"current_api_key_name",
	default=None,
)
current_agent_name_context: ContextVar[str | None] = ContextVar(
	"current_agent_name",
	default=None,
)
background_refresh_task: asyncio.Task[None] | None = None
snapshot_rebuild_worker_task: asyncio.Task[None] | None = None
background_job_worker_task: asyncio.Task[None] | None = None
realtime_analytics_sampler_task: asyncio.Task[None] | None = None


def validate_runtime_redis_connection() -> None:
	"""Fail fast when runtime storage cannot reach the configured Redis endpoint."""
	try:
		if redis_client.ping():
			return
	except RedisError as exc:
		raise RuntimeError(f"Unable to connect to Redis at {redis_url}.") from exc

	raise RuntimeError(f"Redis ping returned an unexpected response for {redis_url}.")


def get_last_global_force_refresh_at() -> datetime | None:
	return _last_global_force_refresh_at_store.get()


def set_last_global_force_refresh_at(value: datetime | None) -> None:
	_last_global_force_refresh_at_store.set(value)


def get_last_realtime_analytics_sampled_at() -> datetime | None:
	return _last_realtime_analytics_sampled_at_store.get()


def set_last_realtime_analytics_sampled_at(value: datetime | None) -> None:
	_last_realtime_analytics_sampled_at_store.set(value)


@contextmanager
def redis_lock(
	name: str,
	*,
	timeout: int = 30,
	blocking_timeout: int = 30,
) -> Iterator[None]:
	lock = redis_client.lock(
		_runtime_lock_key(name),
		timeout=timeout,
		blocking_timeout=blocking_timeout,
		thread_local=False,
	)
	acquired = lock.acquire(blocking=True)
	if not acquired:
		raise RuntimeError(f"Unable to acquire Redis runtime lock {name!r}.")

	try:
		yield
	finally:
		with suppress(LockError):
			lock.release()


@asynccontextmanager
async def async_redis_lock(
	name: str,
	*,
	timeout: int = 30,
	blocking_timeout: int = 30,
) -> AsyncIterator[None]:
	lock = redis_client.lock(
		_runtime_lock_key(name),
		timeout=timeout,
		blocking_timeout=blocking_timeout,
		thread_local=False,
	)
	acquired = await asyncio.to_thread(lock.acquire, blocking=True)
	if not acquired:
		raise RuntimeError(f"Unable to acquire Redis runtime lock {name!r}.")

	try:
		yield
	finally:
		with suppress(LockError):
			await asyncio.to_thread(lock.release)


def clear_snapshot_runtime_state() -> None:
	global snapshot_rebuild_worker_task, background_job_worker_task, realtime_analytics_sampler_task
	snapshot_rebuild_users_in_queue.clear()
	snapshot_rebuild_worker_task = None
	snapshot_rebuild_queue.clear()
	background_job_worker_task = None
	realtime_analytics_sampler_task = None
	set_last_realtime_analytics_sampled_at(None)
