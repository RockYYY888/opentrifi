from __future__ import annotations

import asyncio
import base64
from collections.abc import Iterator, MutableMapping, MutableSet
from typing import Generic, Protocol, TypeVar

KeyType = TypeVar("KeyType")
ValueType = TypeVar("ValueType")
ScalarType = TypeVar("ScalarType")
QueueValue = TypeVar("QueueValue")


class BinaryCodec(Protocol[ValueType]):
	def dumps(self, value: ValueType) -> bytes: ...

	def loads(self, raw_value: bytes | None) -> ValueType | None: ...


class RedisStoreClient(Protocol):
	def get(self, key: str | bytes) -> bytes | None: ...

	def set(self, key: str | bytes, value: bytes, ex: int | None = None) -> object: ...

	def delete(self, *keys: str | bytes) -> int: ...

	def scan_iter(self, pattern: str) -> Iterator[bytes | str]: ...

	def sadd(self, key: str, value: str) -> object: ...

	def srem(self, key: str, value: str) -> object: ...

	def sismember(self, key: str, value: str) -> bool: ...

	def smembers(self, key: str) -> set[bytes | str]: ...

	def scard(self, key: str) -> int: ...

	def rpush(self, key: str, value: bytes) -> object: ...

	def lpop(self, key: str) -> bytes | None: ...

	def blpop(self, key: str, timeout: int) -> tuple[bytes | str, bytes] | None: ...

	def llen(self, key: str) -> int: ...


def redis_key_to_text(redis_key: bytes | str) -> str:
	return redis_key.decode("utf-8") if isinstance(redis_key, bytes) else redis_key


def clear_prefixed_keys(redis_client: RedisStoreClient, prefix: str) -> None:
	keys = list(redis_client.scan_iter(f"{prefix}:*"))
	if keys:
		redis_client.delete(*keys)


class RedisBackedDict(MutableMapping[KeyType, ValueType], Generic[KeyType, ValueType]):
	def __init__(
		self,
		redis_client: RedisStoreClient,
		prefix: str,
		*,
		key_codec: BinaryCodec[KeyType],
		value_codec: BinaryCodec[ValueType],
		ttl_seconds: int | None = None,
	) -> None:
		self._redis = redis_client
		self._prefix = prefix
		self._key_codec = key_codec
		self._value_codec = value_codec
		self._ttl_seconds = ttl_seconds

	def _serialize_key(self, key: KeyType) -> str:
		return base64.urlsafe_b64encode(self._key_codec.dumps(key)).decode("ascii")

	def _deserialize_key(self, key: str) -> KeyType | None:
		try:
			return self._key_codec.loads(base64.urlsafe_b64decode(key.encode("ascii")))
		except (ValueError, TypeError):
			return None

	def _entry_key(self, key: KeyType) -> str:
		return f"{self._prefix}:{self._serialize_key(key)}"

	def __getitem__(self, key: KeyType) -> ValueType:
		value = self.get(key)
		if value is None:
			raise KeyError(key)
		return value

	def __setitem__(self, key: KeyType, value: ValueType) -> None:
		self._redis.set(
			self._entry_key(key),
			self._value_codec.dumps(value),
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
		clear_prefixed_keys(self._redis, self._prefix)

	def get(self, key: KeyType, default: ValueType | None = None) -> ValueType | None:
		value = self._value_codec.loads(self._redis.get(self._entry_key(key)))
		if value is None:
			return default
		return value

	def items(self) -> Iterator[tuple[KeyType, ValueType]]:
		for redis_key in self._redis.scan_iter(f"{self._prefix}:*"):
			raw_value = self._redis.get(redis_key)
			if raw_value is None:
				continue
			decoded_key = redis_key_to_text(redis_key)
			key_fragment = decoded_key[len(self._prefix) + 1 :]
			key = self._deserialize_key(key_fragment)
			value = self._value_codec.loads(raw_value)
			if key is None or value is None:
				continue
			yield key, value

	def pop(self, key: KeyType, default: ValueType | None = None) -> ValueType | None:
		stored_value = self.get(key)
		if stored_value is None:
			return default
		self._redis.delete(self._entry_key(key))
		return stored_value


class RedisBackedSet(MutableSet[str]):
	def __init__(self, redis_client: RedisStoreClient, key: str) -> None:
		self._redis = redis_client
		self._key = key

	def add(self, value: str) -> None:
		self._redis.sadd(self._key, value)

	def discard(self, value: str) -> None:
		self._redis.srem(self._key, value)

	def __contains__(self, value: object) -> bool:
		if not isinstance(value, str):
			return False
		return self._redis.sismember(self._key, value)

	def __iter__(self) -> Iterator[str]:
		for member in self._redis.smembers(self._key):
			yield redis_key_to_text(member)

	def __len__(self) -> int:
		return self._redis.scard(self._key)

	def clear(self) -> None:
		self._redis.delete(self._key)


class RedisBackedQueue(Generic[QueueValue]):
	def __init__(
		self,
		redis_client: RedisStoreClient,
		key: str,
		*,
		value_codec: BinaryCodec[QueueValue],
	) -> None:
		self._redis = redis_client
		self._key = key
		self._value_codec = value_codec

	def put_nowait(self, value: QueueValue) -> None:
		self._redis.rpush(self._key, self._value_codec.dumps(value))

	async def get(self) -> QueueValue:
		while True:
			result = await asyncio.to_thread(self._redis.blpop, self._key, 1)
			if result is None:
				continue
			_value_key, payload = result
			value = self._value_codec.loads(payload)
			if value is not None:
				return value

	def get_nowait(self) -> QueueValue:
		payload = self._redis.lpop(self._key)
		if payload is None:
			raise asyncio.QueueEmpty
		value = self._value_codec.loads(payload)
		if value is None:
			raise asyncio.QueueEmpty
		return value

	def qsize(self) -> int:
		return self._redis.llen(self._key)

	def task_done(self) -> None:
		return None

	def clear(self) -> None:
		self._redis.delete(self._key)


class RedisBackedScalar(Generic[ScalarType]):
	def __init__(
		self,
		redis_client: RedisStoreClient,
		key: str,
		*,
		value_codec: BinaryCodec[ScalarType],
	) -> None:
		self._redis = redis_client
		self._key = key
		self._value_codec = value_codec

	def get(self) -> ScalarType | None:
		return self._value_codec.loads(self._redis.get(self._key))

	def set(self, value: ScalarType | None) -> None:
		if value is None:
			self._redis.delete(self._key)
			return
		self._redis.set(self._key, self._value_codec.dumps(value))

	def clear(self) -> None:
		self._redis.delete(self._key)
