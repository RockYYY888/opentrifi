from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
from typing import Final, Generic, TypeAlias, TypeVar

JsonScalar: TypeAlias = None | bool | int | str
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]

JSON_TYPE_KEY: Final = "__type__"

DecodedType = TypeVar("DecodedType")


@dataclass(frozen=True, slots=True)
class TypedJsonCodec(Generic[DecodedType]):
	"""Versioned JSON codec for Redis/runtime values.

	The codec only accepts primitive JSON values. Floats are intentionally rejected so
	runtime state cannot accidentally reintroduce approximate numeric values.
	"""

	version: int
	encode: Callable[[DecodedType], JsonValue]
	decode: Callable[[JsonValue], DecodedType]

	def dumps(self, value: DecodedType) -> bytes:
		return dumps_versioned_payload(
			self.version,
			{"value": self.encode(value)},
		)

	def loads(self, raw_value: bytes | None) -> DecodedType | None:
		payload = loads_versioned_payload(raw_value, self.version)
		if payload is None:
			return None
		try:
			return self.decode(payload["value"])
		except (KeyError, TypeError, ValueError):
			return None


def typed_object(type_name: str, **fields: JsonValue) -> JsonObject:
	return {JSON_TYPE_KEY: type_name, **fields}


def get_type_name(value: JsonObject) -> str | None:
	type_name = value.get(JSON_TYPE_KEY)
	if type_name is None:
		return None
	if not isinstance(type_name, str):
		raise ValueError("Typed JSON object has a non-string type marker.")
	return type_name


def dumps_versioned_payload(version: int, payload: Mapping[str, JsonValue]) -> bytes:
	envelope: JsonObject = {"version": version, **dict(payload)}
	return json.dumps(
		envelope,
		ensure_ascii=False,
		separators=(",", ":"),
		sort_keys=True,
	).encode("utf-8")


def loads_versioned_payload(raw_value: bytes | None, version: int) -> JsonObject | None:
	if raw_value is None:
		return None
	try:
		payload = expect_json_object(json.loads(raw_value.decode("utf-8")))
		if payload.get("version") != version:
			return None
		return payload
	except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
		return None


def expect_json_value(value: object, *, context: str = "value") -> JsonValue:
	if value is None or isinstance(value, (bool, int, str)):
		return value
	if isinstance(value, list):
		return [
			expect_json_value(item, context=f"{context}[]")
			for item in value
		]
	if isinstance(value, dict):
		return {
			str(key): expect_json_value(item, context=f"{context}.{key}")
			for key, item in value.items()
		}
	raise ValueError(f"{context} is not a supported JSON value.")


def expect_json_object(value: object, *, context: str = "value") -> JsonObject:
	json_value = expect_json_value(value, context=context)
	if not isinstance(json_value, dict):
		raise ValueError(f"{context} is not a JSON object.")
	return json_value


def expect_json_list(value: object, *, context: str = "value") -> list[JsonValue]:
	json_value = expect_json_value(value, context=context)
	if not isinstance(json_value, list):
		raise ValueError(f"{context} is not a JSON list.")
	return json_value


def expect_string(value: object, *, context: str = "value") -> str:
	if not isinstance(value, str):
		raise ValueError(f"{context} is not a string.")
	return value


def expect_bool(value: object, *, context: str = "value") -> bool:
	if not isinstance(value, bool):
		raise ValueError(f"{context} is not a boolean.")
	return value


def expect_int(value: object, *, context: str = "value") -> int:
	if isinstance(value, bool) or not isinstance(value, int):
		raise ValueError(f"{context} is not an integer.")
	return value


def expect_decoded_type(
	value: object,
	expected_type: type[DecodedType],
	*,
	context: str = "value",
) -> DecodedType:
	if not isinstance(value, expected_type):
		raise ValueError(f"{context} did not decode to {expected_type.__name__}.")
	return value
