from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator, Iterator, MutableMapping, MutableSet
from contextlib import asynccontextmanager, contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import json
import threading
from typing import Generic, TypeVar

from redis import Redis
from redis.exceptions import LockError, RedisError

from app.schemas import DashboardResponse
from app.settings import get_settings

KeyType = TypeVar("KeyType")
ValueType = TypeVar("ValueType")
ScalarType = TypeVar("ScalarType")


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
LEGACY_RUNTIME_KEY_PREFIX = "asset-tracker:runtime"
JSON_TYPE_KEY = "__type__"


def _datetime_to_json(value: datetime) -> str:
	return value.isoformat()


def _datetime_from_json(value: str) -> datetime:
	return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _to_json_value(value: object) -> object:
	if isinstance(value, datetime):
		return {JSON_TYPE_KEY: "datetime", "value": _datetime_to_json(value)}
	if isinstance(value, Decimal):
		return {JSON_TYPE_KEY: "decimal", "value": str(value)}
	if isinstance(value, tuple):
		return {JSON_TYPE_KEY: "tuple", "items": [_to_json_value(item) for item in value]}
	if isinstance(value, DashboardResponse):
		return {
			JSON_TYPE_KEY: "DashboardResponse",
			"value": value.model_dump(mode="json"),
		}
	if isinstance(value, DashboardCacheEntry):
		return {
			JSON_TYPE_KEY: "DashboardCacheEntry",
			"dashboard": _to_json_value(value.dashboard),
			"generated_at": _to_json_value(value.generated_at),
		}
	if isinstance(value, LivePortfolioState):
		return {
			JSON_TYPE_KEY: "LivePortfolioState",
			"hour_bucket": _to_json_value(value.hour_bucket),
			"latest_value_cny": _to_json_value(value.latest_value_cny),
			"latest_generated_at": _to_json_value(value.latest_generated_at),
			"has_assets_in_bucket": value.has_assets_in_bucket,
		}
	if isinstance(value, LiveHoldingReturnPoint):
		return {
			JSON_TYPE_KEY: "LiveHoldingReturnPoint",
			"symbol": value.symbol,
			"name": value.name,
			"return_pct": _to_json_value(value.return_pct),
		}
	if isinstance(value, LiveHoldingsReturnState):
		return {
			JSON_TYPE_KEY: "LiveHoldingsReturnState",
			"hour_bucket": _to_json_value(value.hour_bucket),
			"latest_generated_at": _to_json_value(value.latest_generated_at),
			"aggregate_return_pct": _to_json_value(value.aggregate_return_pct),
			"holding_points": [_to_json_value(point) for point in value.holding_points],
			"has_tracked_holdings_in_bucket": value.has_tracked_holdings_in_bucket,
		}
	if isinstance(value, LoginAttemptState):
		return {
			JSON_TYPE_KEY: "LoginAttemptState",
			"attempt_timestamps": [_to_json_value(item) for item in value.attempt_timestamps],
			"consecutive_failed_attempts": value.consecutive_failed_attempts,
			"last_attempt_at": _to_json_value(value.last_attempt_at),
		}
	if isinstance(value, list):
		return [_to_json_value(item) for item in value]
	if isinstance(value, dict):
		return {
			str(key): _to_json_value(item)
			for key, item in value.items()
		}
	if value is None or isinstance(value, (str, int, float, bool)):
		return value

	raise TypeError(f"Unsupported runtime state value type: {type(value).__name__}")


def _from_json_value(value: object) -> object:
	if isinstance(value, list):
		return [_from_json_value(item) for item in value]
	if not isinstance(value, dict):
		return value

	value_type = value.get(JSON_TYPE_KEY)
	if value_type is None:
		return {
			str(key): _from_json_value(item)
			for key, item in value.items()
		}
	if value_type == "datetime":
		return _datetime_from_json(str(value["value"]))
	if value_type == "decimal":
		return Decimal(str(value["value"]))
	if value_type == "tuple":
		return tuple(_from_json_value(item) for item in value["items"])  # type: ignore[index]
	if value_type == "DashboardResponse":
		return DashboardResponse.model_validate(value["value"])
	if value_type == "DashboardCacheEntry":
		return DashboardCacheEntry(
			dashboard=_from_json_value(value["dashboard"]),  # type: ignore[arg-type]
			generated_at=_from_json_value(value["generated_at"]),  # type: ignore[arg-type]
		)
	if value_type == "LivePortfolioState":
		return LivePortfolioState(
			hour_bucket=_from_json_value(value["hour_bucket"]),  # type: ignore[arg-type]
			latest_value_cny=_from_json_value(value["latest_value_cny"]),  # type: ignore[arg-type]
			latest_generated_at=_from_json_value(value["latest_generated_at"]),  # type: ignore[arg-type]
			has_assets_in_bucket=bool(value["has_assets_in_bucket"]),
		)
	if value_type == "LiveHoldingReturnPoint":
		return LiveHoldingReturnPoint(
			symbol=str(value["symbol"]),
			name=str(value["name"]),
			return_pct=_from_json_value(value["return_pct"]),  # type: ignore[arg-type]
		)
	if value_type == "LiveHoldingsReturnState":
		return LiveHoldingsReturnState(
			hour_bucket=_from_json_value(value["hour_bucket"]),  # type: ignore[arg-type]
			latest_generated_at=_from_json_value(value["latest_generated_at"]),  # type: ignore[arg-type]
			aggregate_return_pct=_from_json_value(value["aggregate_return_pct"]),  # type: ignore[arg-type]
			holding_points=tuple(
				_from_json_value(point)
				for point in value["holding_points"]  # type: ignore[index]
			),  # type: ignore[arg-type]
			has_tracked_holdings_in_bucket=bool(value["has_tracked_holdings_in_bucket"]),
		)
	if value_type == "LoginAttemptState":
		return LoginAttemptState(
			attempt_timestamps=[
				_from_json_value(item)
				for item in value["attempt_timestamps"]  # type: ignore[index]
			],  # type: ignore[list-item]
			consecutive_failed_attempts=int(value["consecutive_failed_attempts"]),
			last_attempt_at=_from_json_value(value["last_attempt_at"]),  # type: ignore[arg-type]
		)

	raise ValueError(f"Unsupported runtime state JSON type: {value_type}")


def _serialize(value: object) -> bytes:
	payload = {
		"version": RUNTIME_SERIALIZER_VERSION,
		"value": _to_json_value(value),
	}
	return json.dumps(
		payload,
		ensure_ascii=False,
		separators=(",", ":"),
		sort_keys=True,
	).encode("utf-8")


def _deserialize(raw_value: bytes | None) -> object | None:
	if raw_value is None:
		return None
	try:
		payload = json.loads(raw_value.decode("utf-8"))
		if not isinstance(payload, dict):
			return None
		if payload.get("version") != RUNTIME_SERIALIZER_VERSION:
			return None
		return _from_json_value(payload.get("value"))
	except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
		return None


def _serialize_key(value: object) -> str:
	return base64.urlsafe_b64encode(_serialize(value)).decode("ascii")


def _deserialize_key(value: str) -> object | None:
	return _deserialize(base64.urlsafe_b64decode(value.encode("ascii")))


def _redis_key_to_text(redis_key: bytes | str) -> str:
	return redis_key.decode("utf-8") if isinstance(redis_key, bytes) else redis_key


def _clear_prefixed_keys(redis_client: Redis, prefix: str) -> None:
	keys = list(redis_client.scan_iter(f"{prefix}:*"))
	if keys:
		redis_client.delete(*keys)


def clear_legacy_runtime_keys() -> None:
	_clear_prefixed_keys(redis_client, LEGACY_RUNTIME_KEY_PREFIX)


def _runtime_lock_key(name: str) -> str:
	return f"{RUNTIME_KEY_PREFIX}:lock:{name}"


class RedisBackedDict(MutableMapping[KeyType, ValueType], Generic[KeyType, ValueType]):
	def __init__(
		self,
		redis_client: Redis,
		prefix: str,
		*,
		ttl_seconds: int | None = None,
	) -> None:
		self._redis = redis_client
		self._prefix = prefix
		self._ttl_seconds = ttl_seconds

	def _entry_key(self, key: KeyType) -> str:
		return f"{self._prefix}:{_serialize_key(key)}"

	def __getitem__(self, key: KeyType) -> ValueType:
		value = self.get(key)
		if value is None:
			raise KeyError(key)
		return value

	def __setitem__(self, key: KeyType, value: ValueType) -> None:
		self._redis.set(
			self._entry_key(key),
			_serialize(value),
			ex=self._ttl_seconds,
		)

	def __delitem__(self, key: KeyType) -> None:
		if self._redis.delete(self._entry_key(key)) == 0:
			raise KeyError(key)

	def __iter__(self) -> Iterator[KeyType]:
		for key, _value in self.items():
			yield key

	def __len__(self) -> int:
		return sum(1 for _ in self._redis.scan_iter(f"{self._prefix}:*"))

	def clear(self) -> None:
		_clear_prefixed_keys(self._redis, self._prefix)

	def get(self, key: KeyType, default: ValueType | None = None) -> ValueType | None:
		value = _deserialize(self._redis.get(self._entry_key(key)))
		if value is None:
			return default
		return value  # type: ignore[return-value]

	def items(self) -> Iterator[tuple[KeyType, ValueType]]:
		for redis_key in self._redis.scan_iter(f"{self._prefix}:*"):
			raw_value = self._redis.get(redis_key)
			if raw_value is None:
				continue
			decoded_key = _redis_key_to_text(redis_key)
			key_fragment = decoded_key[len(self._prefix) + 1 :]
			deserialized_key = _deserialize_key(key_fragment)
			deserialized_value = _deserialize(raw_value)
			if deserialized_key is None or deserialized_value is None:
				continue
			yield (
				deserialized_key,  # type: ignore[misc]
				deserialized_value,  # type: ignore[misc]
			)

	def pop(self, key: KeyType, default: ValueType | None = None) -> ValueType | None:
		stored_value = self.get(key)
		if stored_value is None:
			return default
		self._redis.delete(self._entry_key(key))
		return stored_value


class RedisBackedSet(MutableSet[str]):
	def __init__(self, redis_client: Redis, key: str) -> None:
		self._redis = redis_client
		self._key = key

	def add(self, value: str) -> None:
		self._redis.sadd(self._key, value)

	def discard(self, value: str) -> None:
		self._redis.srem(self._key, value)

	def __contains__(self, value: object) -> bool:
		if not isinstance(value, str):
			return False
		return bool(self._redis.sismember(self._key, value))

	def __iter__(self) -> Iterator[str]:
		for member in self._redis.smembers(self._key):
			yield member.decode("utf-8")

	def __len__(self) -> int:
		return int(self._redis.scard(self._key))

	def clear(self) -> None:
		self._redis.delete(self._key)


class RedisBackedQueue:
	def __init__(self, redis_client: Redis, key: str) -> None:
		self._redis = redis_client
		self._key = key

	def put_nowait(self, value: str) -> None:
		self._redis.rpush(self._key, _serialize(value))

	async def get(self) -> str:
		while True:
			result = await asyncio.to_thread(self._redis.blpop, self._key, 1)
			if result is not None:
				_value_key, payload = result
				return _deserialize(payload)  # type: ignore[return-value]

	def get_nowait(self) -> str:
		payload = self._redis.lpop(self._key)
		if payload is None:
			raise asyncio.QueueEmpty
		return _deserialize(payload)  # type: ignore[return-value]

	def qsize(self) -> int:
		return int(self._redis.llen(self._key))

	def task_done(self) -> None:
		return None

	def clear(self) -> None:
		self._redis.delete(self._key)


class RedisBackedScalar(Generic[ScalarType]):
	def __init__(self, redis_client: Redis, key: str) -> None:
		self._redis = redis_client
		self._key = key

	def get(self) -> ScalarType | None:
		return _deserialize(self._redis.get(self._key))  # type: ignore[return-value]

	def set(self, value: ScalarType | None) -> None:
		if value is None:
			self._redis.delete(self._key)
			return
		self._redis.set(self._key, _serialize(value))

	def clear(self) -> None:
		self._redis.delete(self._key)


settings = get_settings()
DEFAULT_LOCAL_REDIS_URL = "redis://127.0.0.1:6380/0"
DASHBOARD_CACHE_TTL_SECONDS = 10 * 60
LIVE_RUNTIME_STATE_TTL_SECONDS = 2 * 60 * 60
LOGIN_ATTEMPT_TTL_SECONDS = 24 * 60 * 60
redis_url = settings.redis_url_value() or DEFAULT_LOCAL_REDIS_URL
redis_client: Redis = Redis.from_url(redis_url)

dashboard_cache: MutableMapping[str, DashboardCacheEntry] = RedisBackedDict[str, DashboardCacheEntry](
	redis_client,
	f"{RUNTIME_KEY_PREFIX}:dashboard-cache",
	ttl_seconds=DASHBOARD_CACHE_TTL_SECONDS,
)
live_portfolio_states: MutableMapping[str, LivePortfolioState] = RedisBackedDict[str, LivePortfolioState](
	redis_client,
	f"{RUNTIME_KEY_PREFIX}:live-portfolio",
	ttl_seconds=LIVE_RUNTIME_STATE_TTL_SECONDS,
)
live_holdings_return_states: MutableMapping[str, LiveHoldingsReturnState] = RedisBackedDict[
	str,
	LiveHoldingsReturnState,
](
	redis_client,
	f"{RUNTIME_KEY_PREFIX}:live-holdings-return",
	ttl_seconds=LIVE_RUNTIME_STATE_TTL_SECONDS,
)
login_attempt_states: MutableMapping[tuple[str, str], LoginAttemptState] = RedisBackedDict[
	tuple[str, str],
	LoginAttemptState,
](
	redis_client,
	f"{RUNTIME_KEY_PREFIX}:login-attempts",
	ttl_seconds=LOGIN_ATTEMPT_TTL_SECONDS,
)
snapshot_rebuild_queue = RedisBackedQueue(
	redis_client,
	f"{RUNTIME_KEY_PREFIX}:snapshot-rebuild-queue",
)
snapshot_rebuild_users_in_queue: MutableSet[str] = RedisBackedSet(
	redis_client,
	f"{RUNTIME_KEY_PREFIX}:snapshot-rebuild-users",
)
_last_global_force_refresh_at_store = RedisBackedScalar[datetime](
	redis_client,
	f"{RUNTIME_KEY_PREFIX}:last-global-force-refresh-at",
)
_last_realtime_analytics_sampled_at_store = RedisBackedScalar[datetime](
	redis_client,
	f"{RUNTIME_KEY_PREFIX}:last-realtime-analytics-sampled-at",
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
			clear_legacy_runtime_keys()
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
	timeout: float = 30,
	blocking_timeout: float = 30,
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
	timeout: float = 30,
	blocking_timeout: float = 30,
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
