from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import TypeAlias

FixedNumber: TypeAlias = Decimal | int | str

DB_QUANTUM = Decimal("0.00000001")
MONEY_QUANTUM = DB_QUANTUM
PRICE_QUANTUM = DB_QUANTUM
QUANTITY_QUANTUM = DB_QUANTUM
FX_RATE_QUANTUM = DB_QUANTUM
PERCENT_QUANTUM = Decimal("0.01")
INTEGER_QUANTUM = Decimal("1")

DISPLAY_MONEY_QUANTUM = Decimal("0.01")
DISPLAY_PRICE_QUANTUM = Decimal("0.0001")
DISPLAY_QUANTITY_QUANTUM = Decimal("0.0001")
DISPLAY_FX_RATE_QUANTUM = Decimal("0.000001")
DISPLAY_PERCENT_QUANTUM = Decimal("0.01")

DECIMAL_ZERO = Decimal("0")
FIXED_EPSILON = DB_QUANTUM


def to_decimal(value: FixedNumber | None, *, default: Decimal = DECIMAL_ZERO) -> Decimal:
	if value is None:
		return default
	if isinstance(value, Decimal):
		return value
	return Decimal(str(value))


def decimal_or_none(value: FixedNumber | None) -> Decimal | None:
	if value is None:
		return None
	return to_decimal(value)


def quantize_decimal(value: FixedNumber | None, *, quantum: Decimal = DB_QUANTUM) -> Decimal:
	return to_decimal(value).quantize(quantum, rounding=ROUND_HALF_UP)


def quantize_optional_decimal(
	value: FixedNumber | None,
	*,
	quantum: Decimal = DB_QUANTUM,
) -> Decimal | None:
	if value is None:
		return None
	return quantize_decimal(value, quantum=quantum)


def add_decimals(*values: FixedNumber | None, quantum: Decimal = DB_QUANTUM) -> Decimal:
	total = sum((to_decimal(value) for value in values), DECIMAL_ZERO)
	return quantize_decimal(total, quantum=quantum)


def multiply_decimals(
	left: FixedNumber | None,
	right: FixedNumber | None,
	*,
	quantum: Decimal = DB_QUANTUM,
) -> Decimal:
	return quantize_decimal(to_decimal(left) * to_decimal(right), quantum=quantum)


def divide_decimals(
	numerator: FixedNumber | None,
	denominator: FixedNumber | None,
	*,
	quantum: Decimal = DB_QUANTUM,
) -> Decimal:
	normalized_denominator = to_decimal(denominator)
	if normalized_denominator == 0:
		raise ZeroDivisionError("denominator cannot be zero")
	return quantize_decimal(
		to_decimal(numerator) / normalized_denominator,
		quantum=quantum,
	)


def is_effectively_zero(value: FixedNumber | None, *, epsilon: Decimal = FIXED_EPSILON) -> bool:
	return abs(to_decimal(value)) <= epsilon


def is_integral_decimal(value: FixedNumber | None) -> bool:
	normalized = quantize_decimal(value)
	return normalized == normalized.quantize(INTEGER_QUANTUM, rounding=ROUND_HALF_UP)


def display_money(value: FixedNumber | None) -> Decimal:
	return quantize_decimal(value, quantum=DISPLAY_MONEY_QUANTUM)


def display_price(value: FixedNumber | None) -> Decimal:
	return quantize_decimal(value, quantum=DISPLAY_PRICE_QUANTUM)


def display_quantity(value: FixedNumber | None) -> Decimal:
	return quantize_decimal(value, quantum=DISPLAY_QUANTITY_QUANTUM)


def display_fx_rate(value: FixedNumber | None) -> Decimal:
	return quantize_decimal(value, quantum=DISPLAY_FX_RATE_QUANTUM)


def display_percent(value: FixedNumber | None) -> Decimal:
	return quantize_decimal(value, quantum=DISPLAY_PERCENT_QUANTUM)


def decimal_to_float(value: FixedNumber | None) -> float | None:
	if value is None:
		return None
	return float(to_decimal(value))


def decimal_to_string(value: FixedNumber | None) -> str | None:
	if value is None:
		return None
	return format(to_decimal(value), "f")
