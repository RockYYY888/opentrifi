from __future__ import annotations

import base64
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from time import monotonic, time
from typing import Callable, Generic, Protocol, TypeVar, cast

from app.services.market_data_parts.common import Quote, SecuritySearchResult
from app.typed_json import (
	JsonValue,
	dumps_versioned_payload,
	expect_decoded_type,
	expect_json_list,
	expect_string,
	get_type_name,
	loads_versioned_payload,
	typed_object,
)

CacheValue = TypeVar("CacheValue")
CACHE_SERIALIZER_VERSION = 2


class RedisCacheClient(Protocol):
	def get(self, key: str | bytes) -> bytes | None: ...

	def set(self, key: str | bytes, value: bytes, ex: int | None = None) -> object: ...

	def delete(self, *keys: str | bytes) -> int: ...

	def scan_iter(self, pattern: str) -> Iterable[bytes | str]: ...


def _decimal_now_from_monotonic() -> Decimal:
	return Decimal(str(monotonic()))


def _decimal_now_from_wall_clock() -> Decimal:
	return Decimal(str(time()))


def _to_json_value(value: object) -> JsonValue:
	if isinstance(value, datetime):
		return typed_object("datetime", value=value.isoformat())
	if isinstance(value, Decimal):
		return typed_object("decimal", value=str(value))
	if isinstance(value, Quote):
		return typed_object(
			"Quote",
			symbol=value.symbol,
			name=value.name,
			price=_to_json_value(value.price),
			currency=value.currency,
			market_time=_to_json_value(value.market_time),
		)
	if isinstance(value, SecuritySearchResult):
		return typed_object(
			"SecuritySearchResult",
			symbol=value.symbol,
			name=value.name,
			market=value.market,
			currency=value.currency,
			exchange=value.exchange,
			source=value.source,
		)
	if isinstance(value, tuple):
		return typed_object("tuple", items=[_to_json_value(item) for item in value])
	if isinstance(value, list):
		return [_to_json_value(item) for item in value]
	if isinstance(value, dict):
		return {str(key): _to_json_value(item) for key, item in value.items()}
	if value is None or isinstance(value, (str, int, bool)):
		return value

	raise TypeError(f"Unsupported cache value type: {type(value).__name__}")


def _from_json_value(value: JsonValue) -> object:
	if isinstance(value, list):
		return [_from_json_value(item) for item in value]
	if not isinstance(value, dict):
		return value

	value_type = get_type_name(value)
	if value_type is None:
		return {str(key): _from_json_value(item) for key, item in value.items()}
	if value_type == "datetime":
		return datetime.fromisoformat(
			expect_string(value["value"], context="datetime.value").replace("Z", "+00:00"),
		)
	if value_type == "decimal":
		return Decimal(expect_string(value["value"], context="decimal.value"))
	if value_type == "tuple":
		return tuple(
			_from_json_value(item)
			for item in expect_json_list(value["items"], context="tuple.items")
		)
	if value_type == "Quote":
		price = expect_decoded_type(
			_from_json_value(value["price"]),
			Decimal,
			context="Quote.price",
		)
		market_time = _from_json_value(value["market_time"])
		if market_time is not None and not isinstance(market_time, datetime):
			raise ValueError("Quote.market_time did not decode to datetime.")
		return Quote(
			symbol=expect_string(value["symbol"], context="Quote.symbol"),
			name=expect_string(value["name"], context="Quote.name"),
			price=price,
			currency=expect_string(value["currency"], context="Quote.currency"),
			market_time=market_time,
		)
	if value_type == "SecuritySearchResult":
		exchange = value.get("exchange")
		source = value.get("source")
		return SecuritySearchResult(
			symbol=expect_string(value["symbol"], context="SecuritySearchResult.symbol"),
			name=expect_string(value["name"], context="SecuritySearchResult.name"),
			market=expect_string(value["market"], context="SecuritySearchResult.market"),
			currency=expect_string(value["currency"], context="SecuritySearchResult.currency"),
			exchange=None if exchange is None else str(exchange),
			source=None if source is None else str(source),
		)

	raise ValueError(f"Unsupported cache JSON type: {value_type}")


def _serialize_entry(entry: CacheEntry[object]) -> bytes:
	return dumps_versioned_payload(
		CACHE_SERIALIZER_VERSION,
		{
			"expires_at": _to_json_value(entry.expires_at),
			"value": _to_json_value(entry.value),
		},
	)


def _deserialize_entry(raw_value: bytes | None) -> CacheEntry[object] | None:
	try:
		payload = loads_versioned_payload(raw_value, CACHE_SERIALIZER_VERSION)
		if payload is None:
			return None
		expires_at = _from_json_value(payload["expires_at"])
		if not isinstance(expires_at, Decimal):
			return None
		return CacheEntry(
			value=_from_json_value(payload["value"]),
			expires_at=expires_at,
		)
	except (KeyError, TypeError, ValueError):
		return None


@dataclass(slots=True)
class CacheEntry(Generic[CacheValue]):
	value: CacheValue
	expires_at: Decimal


class TTLCache(Generic[CacheValue]):
	"""Store values in-process while retaining the last stale value for fallback."""

	def __init__(self, now: Callable[[], Decimal] | None = None) -> None:
		self._entries: dict[str, CacheEntry[CacheValue]] = {}
		self._now = now or _decimal_now_from_monotonic

	def get(self, key: str) -> CacheValue | None:
		entry = self._entries.get(key)
		if entry is None:
			return None
		if entry.expires_at <= self._now():
			return None
		return entry.value

	def get_stale(self, key: str) -> CacheValue | None:
		entry = self._entries.get(key)
		if entry is None:
			return None
		return entry.value

	def set(self, key: str, value: CacheValue, ttl_seconds: Decimal | int) -> CacheValue:
		self._entries[key] = CacheEntry(
			value=value,
			expires_at=self._now() + Decimal(str(ttl_seconds)),
		)
		return value

	def clear(self) -> None:
		self._entries.clear()

	def expire_all(self) -> None:
		"""Mark every entry expired while keeping stale values available for fallback."""
		now = self._now()
		for entry in self._entries.values():
			entry.expires_at = now


class RedisBackedTTLCache(Generic[CacheValue]):
	"""Store cache entries in Redis while retaining stale values for fallback."""

	def __init__(
		self,
		redis_client: RedisCacheClient,
		prefix: str,
		now: Callable[[], Decimal] | None = None,
		stale_ttl_seconds: Decimal | int | None = None,
	) -> None:
		self._redis = redis_client
		self._prefix = prefix
		self._now = now or _decimal_now_from_wall_clock
		self._stale_ttl_seconds = stale_ttl_seconds

	def _entry_key(self, key: str) -> str:
		encoded_key = base64.urlsafe_b64encode(key.encode("utf-8")).decode("ascii")
		return f"{self._prefix}:{encoded_key}"

	def _load_entry(self, key: str) -> CacheEntry[CacheValue] | None:
		raw_value = cast(bytes | None, self._redis.get(self._entry_key(key)))
		entry = _deserialize_entry(raw_value)
		if entry is None:
			return None
		return cast(CacheEntry[CacheValue], entry)

	def get(self, key: str) -> CacheValue | None:
		entry = self._load_entry(key)
		if entry is None:
			return None
		if entry.expires_at <= self._now():
			return None
		return entry.value

	def get_stale(self, key: str) -> CacheValue | None:
		entry = self._load_entry(key)
		if entry is None:
			return None
		return entry.value

	def set(self, key: str, value: CacheValue, ttl_seconds: Decimal | int) -> CacheValue:
		entry = CacheEntry(
			value=value,
			expires_at=self._now() + Decimal(str(ttl_seconds)),
		)
		redis_ttl_seconds = self._stale_ttl_seconds
		if redis_ttl_seconds is None:
			redis_ttl_seconds = max(Decimal(str(ttl_seconds)) * Decimal("60"), Decimal(60 * 60))
		self._redis.set(
			self._entry_key(key),
			_serialize_entry(cast(CacheEntry[object], entry)),
			ex=max(1, int(redis_ttl_seconds)),
		)
		return value

	def clear(self) -> None:
		keys = list(self._redis.scan_iter(f"{self._prefix}:*"))
		if keys:
			self._redis.delete(*keys)

	def expire_all(self) -> None:
		"""Mark every entry expired while keeping stale values available for fallback."""
		now = self._now()
		for redis_key in self._redis.scan_iter(f"{self._prefix}:*"):
			raw_value = self._redis.get(redis_key)
			if raw_value is None:
				continue
			entry = _deserialize_entry(cast(bytes, raw_value))
			if entry is None:
				continue
			entry.expires_at = now
			redis_ttl_seconds = self._stale_ttl_seconds or Decimal(60 * 60)
			self._redis.set(
				redis_key,
				_serialize_entry(entry),
				ex=max(1, int(redis_ttl_seconds)),
			)
