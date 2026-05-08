from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import json
from time import monotonic, time
from typing import Callable, Generic, TypeVar, cast

from redis import Redis

from app.services.market_data_parts.common import Quote, SecuritySearchResult

CacheValue = TypeVar("CacheValue")
CACHE_SERIALIZER_VERSION = 2
CACHE_JSON_TYPE_KEY = "__type__"


def _decimal_now_from_monotonic() -> Decimal:
	return Decimal(str(monotonic()))


def _decimal_now_from_wall_clock() -> Decimal:
	return Decimal(str(time()))


def _to_json_value(value: object) -> object:
	if isinstance(value, datetime):
		return {CACHE_JSON_TYPE_KEY: "datetime", "value": value.isoformat()}
	if isinstance(value, Decimal):
		return {CACHE_JSON_TYPE_KEY: "decimal", "value": str(value)}
	if isinstance(value, Quote):
		return {
			CACHE_JSON_TYPE_KEY: "Quote",
			"symbol": value.symbol,
			"name": value.name,
			"price": _to_json_value(value.price),
			"currency": value.currency,
			"market_time": _to_json_value(value.market_time),
		}
	if isinstance(value, SecuritySearchResult):
		return {
			CACHE_JSON_TYPE_KEY: "SecuritySearchResult",
			"symbol": value.symbol,
			"name": value.name,
			"market": value.market,
			"currency": value.currency,
			"exchange": value.exchange,
			"source": value.source,
		}
	if isinstance(value, tuple):
		return {CACHE_JSON_TYPE_KEY: "tuple", "items": [_to_json_value(item) for item in value]}
	if isinstance(value, list):
		return [_to_json_value(item) for item in value]
	if isinstance(value, dict):
		return {str(key): _to_json_value(item) for key, item in value.items()}
	if value is None or isinstance(value, (str, int, bool)):
		return value

	raise TypeError(f"Unsupported cache value type: {type(value).__name__}")


def _from_json_value(value: object) -> object:
	if isinstance(value, list):
		return [_from_json_value(item) for item in value]
	if not isinstance(value, dict):
		return value

	value_type = value.get(CACHE_JSON_TYPE_KEY)
	if value_type is None:
		return {str(key): _from_json_value(item) for key, item in value.items()}
	if value_type == "datetime":
		return datetime.fromisoformat(str(value["value"]).replace("Z", "+00:00"))
	if value_type == "decimal":
		return Decimal(str(value["value"]))
	if value_type == "tuple":
		return tuple(_from_json_value(item) for item in value["items"])  # type: ignore[index]
	if value_type == "Quote":
		return Quote(
			symbol=str(value["symbol"]),
			name=str(value["name"]),
			price=cast(Decimal, _from_json_value(value["price"])),
			currency=str(value["currency"]),
			market_time=cast(datetime | None, _from_json_value(value["market_time"])),
		)
	if value_type == "SecuritySearchResult":
		exchange = value.get("exchange")
		source = value.get("source")
		return SecuritySearchResult(
			symbol=str(value["symbol"]),
			name=str(value["name"]),
			market=str(value["market"]),
			currency=str(value["currency"]),
			exchange=None if exchange is None else str(exchange),
			source=None if source is None else str(source),
		)

	raise ValueError(f"Unsupported cache JSON type: {value_type}")


def _serialize_entry(entry: CacheEntry[object]) -> bytes:
	payload = {
		"version": CACHE_SERIALIZER_VERSION,
		"expires_at": _to_json_value(entry.expires_at),
		"value": _to_json_value(entry.value),
	}
	return json.dumps(
		payload,
		ensure_ascii=False,
		separators=(",", ":"),
		sort_keys=True,
	).encode("utf-8")


def _deserialize_entry(raw_value: bytes | None) -> CacheEntry[object] | None:
	if raw_value is None:
		return None
	try:
		payload = json.loads(raw_value.decode("utf-8"))
		if not isinstance(payload, dict):
			return None
		if payload.get("version") != CACHE_SERIALIZER_VERSION:
			return None
		expires_at = _from_json_value(payload["expires_at"])
		if not isinstance(expires_at, Decimal):
			return None
		return CacheEntry(
			value=_from_json_value(payload["value"]),
			expires_at=expires_at,
		)
	except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
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
		redis_client: Redis,
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
