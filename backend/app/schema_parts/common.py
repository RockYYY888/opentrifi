from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import re
from typing import Any

from pydantic import BaseModel, field_serializer

from app.fixed_precision import FIXED_EPSILON, quantize_decimal, quantize_optional_decimal

AGENT_TOKEN_NAME_PATTERN = re.compile(r"^[a-z]+(?:-[a-z]+)*$")


def _normalize_optional_text(value: str | None) -> str | None:
	if value is None:
		return None

	stripped = value.strip()
	return stripped or None


def _normalize_required_text(value: str, field_name: str) -> str:
	stripped = value.strip()
	if not stripped:
		raise ValueError(f"{field_name} cannot be empty.")

	return stripped


def _normalize_choice(
	value: str | None,
	allowed_values: tuple[str, ...],
	field_name: str,
) -> str | None:
	if value is None:
		return None

	normalized = value.strip().upper()
	if normalized not in allowed_values:
		raise ValueError(f"{field_name} must be one of: {', '.join(allowed_values)}.")

	return normalized


def _coerce_utc_datetime(value: datetime) -> datetime:
	if value.tzinfo is None:
		return value.replace(tzinfo=timezone.utc)

	return value.astimezone(timezone.utc)


def _serialize_utc_datetime(value: datetime) -> str:
	return _coerce_utc_datetime(value).isoformat().replace("+00:00", "Z")


def _normalize_positive_decimal(value: Any, field_label: str) -> Decimal:
	decimal_value = quantize_decimal(value)
	if decimal_value <= FIXED_EPSILON:
		raise ValueError(f"{field_label}必须大于 0。")
	return decimal_value


def _normalize_non_negative_decimal(value: Any, field_label: str) -> Decimal:
	decimal_value = quantize_decimal(value)
	if decimal_value < 0:
		raise ValueError(f"{field_label}不能为负数。")
	return decimal_value


def _normalize_optional_positive_decimal(value: Any, field_label: str) -> Decimal | None:
	decimal_value = quantize_optional_decimal(value)
	if decimal_value is None:
		return None
	if decimal_value <= FIXED_EPSILON:
		raise ValueError(f"{field_label}必须大于 0。")
	return decimal_value


def _normalize_non_zero_decimal(value: Any, field_label: str) -> Decimal:
	decimal_value = quantize_decimal(value)
	if abs(decimal_value) <= FIXED_EPSILON:
		raise ValueError(f"{field_label}不能为 0。")
	return decimal_value


def _normalize_optional_non_zero_decimal(value: Any, field_label: str) -> Decimal | None:
	if value is None:
		return None
	return _normalize_non_zero_decimal(value, field_label)


class UtcTimestampResponseModel(BaseModel):
	@field_serializer("*", when_used="json", check_fields=False)
	def serialize_datetime_fields(self, value: Any) -> Any:
		if isinstance(value, datetime):
			return _serialize_utc_datetime(value)

		return value
