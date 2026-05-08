from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from fastapi import HTTPException

from app.fixed_precision import (
	DECIMAL_ZERO,
	decimal_to_float,
	display_quantity,
	quantize_decimal,
	to_decimal,
)
from app.models import HOLDING_TRANSACTION_SIDES, SecurityHoldingTransaction
from app.services.common_service import (
	_coerce_utc_datetime,
	_date_start_utc,
	_normalize_currency,
	_normalize_optional_text,
)
from app.services.holding_projection_parts.common import (
	HOLDING_QUANTITY_EPSILON,
	HoldingLot,
	ProjectedHoldingState,
)

def _holding_transaction_side_priority(side: str) -> int:
	if side == "BUY":
		return 0
	if side == "SELL":
		return 1
	return 2

def _holding_transaction_event_at(transaction: SecurityHoldingTransaction) -> datetime:
	return _date_start_utc(transaction.traded_on)

def _holding_transaction_sort_key(
	transaction: SecurityHoldingTransaction,
) -> tuple[datetime, int, datetime, int]:
	return (
		_holding_transaction_event_at(transaction),
		_holding_transaction_side_priority(transaction.side),
		_coerce_utc_datetime(transaction.created_at),
		transaction.id or 0,
	)

def _projected_holding_quantity(state: ProjectedHoldingState) -> Decimal:
	total = sum((lot.quantity for lot in state.lots), DECIMAL_ZERO)
	if total <= HOLDING_QUANTITY_EPSILON:
		return DECIMAL_ZERO
	return quantize_decimal(total)

def _projected_holding_started_on(state: ProjectedHoldingState) -> date | None:
	if not state.lots:
		return None
	return min(lot.traded_on for lot in state.lots)

def _projected_holding_cost_basis(state: ProjectedHoldingState) -> Decimal | None:
	quantity = _projected_holding_quantity(state)
	if quantity <= HOLDING_QUANTITY_EPSILON:
		return None

	total_cost = DECIMAL_ZERO
	for lot in state.lots:
		if lot.cost_per_unit is None:
			return None
		total_cost += lot.quantity * lot.cost_per_unit

	if total_cost <= 0:
		return None
	return quantize_decimal(total_cost / quantity)

def _validate_holding_quantity_for_market(quantity: Decimal | float | int, market: str) -> None:
	normalized_market = market.strip().upper()
	normalized_quantity = to_decimal(quantity)
	if (
		normalized_market not in {"FUND", "CRYPTO"}
		and normalized_quantity != normalized_quantity.to_integral_value()
	):
		raise HTTPException(status_code=422, detail="股票请使用整数数量，基金可使用份额。")

def _normalize_holding_transaction_side(side: str) -> str:
	normalized = side.strip().upper()
	if normalized not in HOLDING_TRANSACTION_SIDES:
		raise HTTPException(
			status_code=422,
			detail=f"交易方向必须是 {', '.join(HOLDING_TRANSACTION_SIDES)}。",
		)
	return normalized

def _apply_holding_transaction_to_state(
	state: ProjectedHoldingState,
	transaction: SecurityHoldingTransaction,
) -> None:
	side = _normalize_holding_transaction_side(transaction.side)
	quantity = max(transaction.quantity, DECIMAL_ZERO)
	if quantity <= HOLDING_QUANTITY_EPSILON:
		return

	state.name = transaction.name or state.name
	state.fallback_currency = _normalize_currency(
		transaction.fallback_currency or state.fallback_currency,
	)
	state.broker = _normalize_optional_text(transaction.broker) or state.broker
	state.note = _normalize_optional_text(transaction.note) or state.note

	if side == "ADJUST":
		cost_per_unit = (
			transaction.price if transaction.price is not None and transaction.price > 0 else None
		)
		state.lots = [
			HoldingLot(
				quantity=quantity,
				traded_on=transaction.traded_on,
				cost_per_unit=cost_per_unit,
			),
		]
		return

	if side == "BUY":
		state.lots.append(
			HoldingLot(
				quantity=quantity,
				traded_on=transaction.traded_on,
				cost_per_unit=transaction.price if transaction.price and transaction.price > 0 else None,
			),
		)
		return

	remaining_to_sell = quantity
	next_lots: list[HoldingLot] = []
	for lot in sorted(state.lots, key=lambda item: item.traded_on):
		if remaining_to_sell <= HOLDING_QUANTITY_EPSILON:
			next_lots.append(lot)
			continue
		if lot.quantity <= remaining_to_sell + HOLDING_QUANTITY_EPSILON:
			remaining_to_sell -= lot.quantity
			continue
		next_lots.append(
			HoldingLot(
				quantity=quantize_decimal(lot.quantity - remaining_to_sell),
				traded_on=lot.traded_on,
				cost_per_unit=lot.cost_per_unit,
			),
		)
		remaining_to_sell = DECIMAL_ZERO

	if remaining_to_sell > HOLDING_QUANTITY_EPSILON:
		raise HTTPException(
			status_code=422,
			detail=(
				f"{state.symbol} 可卖数量不足。当前可卖 "
				f"{decimal_to_float(display_quantity(_projected_holding_quantity(state))):g}，"
				f"请求卖出 {decimal_to_float(display_quantity(quantity)):g}。"
			),
		)

	state.lots = next_lots

def _project_holding_state_from_sorted_transactions(
	transactions: list[SecurityHoldingTransaction],
	*,
	symbol: str,
	market: str,
) -> ProjectedHoldingState | None:
	if not transactions:
		return None

	sorted_transactions = sorted(transactions, key=_holding_transaction_sort_key)
	first = sorted_transactions[0]
	state = ProjectedHoldingState(
		symbol=symbol,
		name=first.name,
		market=market,
		fallback_currency=first.fallback_currency,
		broker=first.broker,
		note=first.note,
		lots=[],
	)
	for transaction in sorted_transactions:
		_apply_holding_transaction_to_state(state, transaction)

	if _projected_holding_quantity(state) <= HOLDING_QUANTITY_EPSILON:
		return None
	return state
