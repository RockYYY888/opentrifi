from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from datetime import date, datetime

from sqlalchemy import delete
from fastapi import HTTPException
from sqlmodel import Session, select

from app.models import (
    HOLDING_TRANSACTION_SIDES,
    CashAccount,
    CashLedgerEntry,
    HoldingTransactionCashSettlement,
    SecurityHolding,
    SecurityHoldingTransaction,
    UserAccount,
)
from app.schemas import SecurityHoldingTransactionRead
from app.services import service_context
from app.fixed_precision import (
	DECIMAL_ZERO,
	FIXED_EPSILON,
	decimal_to_float,
	display_money,
	display_price,
	display_quantity,
	quantize_decimal,
	quantize_optional_decimal,
	to_decimal,
)
from app.services.common_service import (
	_capture_model_state,
	_coerce_utc_datetime,
	_date_start_utc,
	_normalize_currency,
	_normalize_optional_text,
	_record_asset_mutation,
    _server_today_date,
    _touch_model,
)
from app.services.market_data import QuoteLookupError
from app.services.portfolio_read_service import _to_holding_transaction_read

HOLDING_QUANTITY_EPSILON = FIXED_EPSILON

@dataclass(slots=True)
class AppliedCashSettlement:
	cash_account: CashAccount
	settled_amount: Decimal
	settled_currency: str
	handling: str
	flow_direction: str
	ledger_entry_type: str
	auto_created_cash_account: bool

@dataclass(slots=True)
class HoldingLot:
	quantity: Decimal
	traded_on: date
	cost_per_unit: Decimal | None

@dataclass(slots=True)
class ProjectedHoldingState:
	symbol: str
	name: str
	market: str
	fallback_currency: str
	broker: str | None
	note: str | None
	lots: list[HoldingLot]

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

def _list_holdings_for_symbol(
	session: Session,
	*,
	user_id: str,
	symbol: str,
	market: str,
) -> list[SecurityHolding]:
	return list(
		session.exec(
			select(SecurityHolding)
			.where(SecurityHolding.user_id == user_id)
			.where(SecurityHolding.symbol == symbol)
			.where(SecurityHolding.market == market)
			.order_by(SecurityHolding.id.asc()),
		),
	)

def _delete_holding_transactions_for_symbol(
	session: Session,
	*,
	user_id: str,
	symbol: str,
	market: str,
) -> list[SecurityHoldingTransaction]:
	transactions = list(
		session.exec(
			select(SecurityHoldingTransaction)
			.where(SecurityHoldingTransaction.user_id == user_id)
			.where(SecurityHoldingTransaction.symbol == symbol)
			.where(SecurityHoldingTransaction.market == market)
			.order_by(
				SecurityHoldingTransaction.traded_on.desc(),
				SecurityHoldingTransaction.created_at.desc(),
				SecurityHoldingTransaction.id.desc(),
			),
		),
	)
	if not transactions:
		return []

	transaction_ids = [transaction.id for transaction in transactions if transaction.id is not None]
	if transaction_ids:
		session.exec(
			delete(HoldingTransactionCashSettlement)
			.where(HoldingTransactionCashSettlement.user_id == user_id)
			.where(HoldingTransactionCashSettlement.holding_transaction_id.in_(transaction_ids)),
		)

	for transaction in transactions:
		session.delete(transaction)

	return transactions

def _reverse_and_delete_holding_transactions_for_symbol(
	session: Session,
	*,
	current_user: UserAccount,
	symbol: str,
	market: str,
) -> list[SecurityHoldingTransaction]:
	transactions = list(
		session.exec(
			select(SecurityHoldingTransaction)
			.where(SecurityHoldingTransaction.user_id == current_user.username)
			.where(SecurityHoldingTransaction.symbol == symbol)
			.where(SecurityHoldingTransaction.market == market)
			.order_by(
				SecurityHoldingTransaction.traded_on.desc(),
				SecurityHoldingTransaction.created_at.desc(),
				SecurityHoldingTransaction.id.desc(),
			),
		),
	)
	if not transactions:
		return []

	for transaction in transactions:
		if transaction.side != "SELL":
			continue
		_reverse_holding_transaction_cash_settlement(
			session,
			current_user=current_user,
			transaction=transaction,
		)

	transaction_ids = [transaction.id for transaction in transactions if transaction.id is not None]
	if transaction_ids:
		session.exec(
			delete(HoldingTransactionCashSettlement)
			.where(HoldingTransactionCashSettlement.user_id == current_user.username)
			.where(HoldingTransactionCashSettlement.holding_transaction_id.in_(transaction_ids)),
		)

	for transaction in transactions:
		session.delete(transaction)

	return transactions

def _list_holding_transaction_settlements(
	session: Session,
	*,
	user_id: str,
	transaction_ids: list[int],
) -> dict[int, HoldingTransactionCashSettlement]:
	if not transaction_ids:
		return {}

	settlements = list(
		session.exec(
			select(HoldingTransactionCashSettlement)
			.where(HoldingTransactionCashSettlement.user_id == user_id)
			.where(HoldingTransactionCashSettlement.holding_transaction_id.in_(transaction_ids)),
		),
	)
	return {
		settlement.holding_transaction_id: settlement
		for settlement in settlements
	}

def _to_holding_transaction_reads(
	session: Session,
	*,
	user_id: str,
	transactions: list[SecurityHoldingTransaction],
) -> list[SecurityHoldingTransactionRead]:
	settlement_map = _list_holding_transaction_settlements(
		session,
		user_id=user_id,
		transaction_ids=[
			transaction.id
			for transaction in transactions
			if transaction.id is not None
		],
	)
	return [
		_to_holding_transaction_read(
			transaction,
			settlement_map.get(transaction.id or 0),
		)
		for transaction in transactions
	]

def _ensure_transaction_baseline_from_holding_snapshot(
	session: Session,
	*,
	holding: SecurityHolding,
) -> None:
	existing_transaction = session.exec(
		select(SecurityHoldingTransaction.id)
		.where(SecurityHoldingTransaction.user_id == holding.user_id)
		.where(SecurityHoldingTransaction.symbol == holding.symbol)
		.where(SecurityHoldingTransaction.market == holding.market)
		.limit(1),
	).first()
	if existing_transaction is not None:
		return

	baseline_date = holding.started_on or _server_today_date(
		_coerce_utc_datetime(holding.created_at),
	)
	session.add(
		SecurityHoldingTransaction(
			user_id=holding.user_id,
			symbol=holding.symbol,
			name=holding.name,
			side="BUY",
			quantity=max(holding.quantity, DECIMAL_ZERO),
			price=holding.cost_basis_price if holding.cost_basis_price and holding.cost_basis_price > 0 else None,
			fallback_currency=_normalize_currency(holding.fallback_currency),
			market=holding.market,
			broker=holding.broker,
			traded_on=baseline_date,
			note=holding.note,
		),
	)

def _reset_holding_transactions_from_snapshot(
	session: Session,
	*,
	holding: SecurityHolding,
) -> SecurityHoldingTransaction | None:
	_delete_holding_transactions_for_symbol(
		session,
		user_id=holding.user_id,
		symbol=holding.symbol,
		market=holding.market,
	)
	if holding.quantity <= HOLDING_QUANTITY_EPSILON:
		return None

	baseline_date = holding.started_on or _server_today_date(
		_coerce_utc_datetime(holding.created_at),
	)
	transaction = SecurityHoldingTransaction(
		user_id=holding.user_id,
		symbol=holding.symbol,
		name=holding.name,
		side="BUY",
		quantity=max(holding.quantity, DECIMAL_ZERO),
		price=holding.cost_basis_price if holding.cost_basis_price and holding.cost_basis_price > 0 else None,
		fallback_currency=_normalize_currency(holding.fallback_currency),
		market=holding.market,
		broker=holding.broker,
		traded_on=baseline_date,
		note=holding.note,
	)
	session.add(transaction)
	return transaction

def _get_latest_holding_transaction_for_symbol(
	session: Session,
	*,
	user_id: str,
	symbol: str,
	market: str,
) -> SecurityHoldingTransaction | None:
	return session.exec(
		select(SecurityHoldingTransaction)
		.where(SecurityHoldingTransaction.user_id == user_id)
		.where(SecurityHoldingTransaction.symbol == symbol)
		.where(SecurityHoldingTransaction.market == market)
		.order_by(
			SecurityHoldingTransaction.traded_on.desc(),
			SecurityHoldingTransaction.created_at.desc(),
			SecurityHoldingTransaction.id.desc(),
		)
		.limit(1),
	).first()

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

def _project_holding_state_from_transactions(
	session: Session,
	*,
	user_id: str,
	symbol: str,
	market: str,
) -> ProjectedHoldingState | None:
	transactions = list(
		session.exec(
			select(SecurityHoldingTransaction)
			.where(SecurityHoldingTransaction.user_id == user_id)
			.where(SecurityHoldingTransaction.symbol == symbol)
			.where(SecurityHoldingTransaction.market == market),
		),
	)
	if not transactions:
		return None

	return _project_holding_state_from_sorted_transactions(
		transactions,
		symbol=symbol,
		market=market,
	)

def _sync_holding_projection_for_symbol(
	session: Session,
	*,
	user_id: str,
	symbol: str,
	market: str,
) -> SecurityHolding | None:
	existing_holdings = _list_holdings_for_symbol(
		session,
		user_id=user_id,
		symbol=symbol,
		market=market,
	)
	primary_holding = existing_holdings[0] if existing_holdings else None
	for stale_holding in existing_holdings[1:]:
		session.delete(stale_holding)

	projected_state = _project_holding_state_from_transactions(
		session,
		user_id=user_id,
		symbol=symbol,
		market=market,
	)
	if projected_state is None:
		if primary_holding is not None:
			session.delete(primary_holding)
		return None

	quantity = _projected_holding_quantity(projected_state)
	started_on = _projected_holding_started_on(projected_state)
	cost_basis_price = _projected_holding_cost_basis(projected_state)
	if primary_holding is None:
		primary_holding = SecurityHolding(
			user_id=user_id,
			symbol=symbol,
			name=projected_state.name,
			quantity=quantity,
			fallback_currency=projected_state.fallback_currency,
			cost_basis_price=cost_basis_price,
			market=market,
			broker=projected_state.broker,
			started_on=started_on,
			note=projected_state.note,
		)
	else:
		primary_holding.name = projected_state.name
		primary_holding.quantity = quantity
		primary_holding.fallback_currency = projected_state.fallback_currency
		primary_holding.cost_basis_price = cost_basis_price
		primary_holding.market = market
		primary_holding.broker = projected_state.broker
		primary_holding.started_on = started_on
		primary_holding.note = projected_state.note
		_touch_model(primary_holding)

	session.add(primary_holding)
	session.flush()
	return primary_holding

def _resolve_sell_execution_price_and_currency(
	*,
	symbol: str,
	market: str,
	fallback_currency: str,
	payload_price: Decimal | float | int | None,
) -> tuple[Decimal, str]:
	resolved_price = (
		quantize_decimal(payload_price)
		if payload_price is not None and to_decimal(payload_price) > 0
		else None
	)
	resolved_currency = _normalize_currency(fallback_currency)

	if resolved_price is None:
		try:
			quote, _warnings = asyncio.run(
				service_context.market_data_client.fetch_quote(symbol, market),
			)
			if quote.price > 0:
				resolved_price = quantize_decimal(quote.price)
			if quote.currency:
				resolved_currency = _normalize_currency(quote.currency)
		except (QuoteLookupError, ValueError):
			# Fallback to payload-provided price/currency when live quote is temporarily unavailable.
			pass

	if resolved_price is None or resolved_price <= 0:
		raise HTTPException(
			status_code=422,
			detail="卖出交易缺少可用价格，请稍后重试或手动提供成交价。",
		)

	return quantize_decimal(resolved_price), resolved_currency

def _build_sell_proceeds_note(
	*,
	symbol: str,
	name: str,
	market: str,
	quantity: Decimal,
	execution_price: Decimal,
	source_currency: str,
	transaction_id: int | None,
	settled_amount: Decimal | None = None,
	settled_currency: str | None = None,
) -> str:
	note = (
		f"来源：卖出 {name}({symbol}) [{market}] "
		f"数量 {decimal_to_float(display_quantity(quantity)):g}，"
		f"成交价 {decimal_to_float(display_price(execution_price)):g} {_normalize_currency(source_currency)}"
	)
	if settled_amount is not None and settled_currency:
		note += (
			f"，自动入账 {decimal_to_float(display_money(settled_amount)):g} "
			f"{_normalize_currency(settled_currency)}"
		)
	if transaction_id is not None:
		note += f"，交易ID #{transaction_id}"
	return note

def _prepend_note_entry(existing_note: str | None, entry: str) -> str:
	normalized_existing = _normalize_optional_text(existing_note)
	normalized_entry = entry.strip()
	combined_note = (
		normalized_entry
		if normalized_existing is None
		else f"{normalized_entry}\n{normalized_existing}"
	)
	if len(combined_note) <= 500:
		return combined_note
	return combined_note[:497].rstrip() + "..."

def _convert_cash_amount_between_currencies(
	*,
	amount: Decimal | float | int,
	from_currency: str,
	to_currency: str,
) -> tuple[Decimal, Decimal]:
	source_currency = _normalize_currency(from_currency)
	target_currency = _normalize_currency(to_currency)
	if source_currency == target_currency:
		return quantize_decimal(amount), Decimal("1")

	try:
		rate, _warnings = asyncio.run(
			service_context.market_data_client.fetch_fx_rate(source_currency, target_currency),
		)
	except (QuoteLookupError, ValueError) as exc:
		raise HTTPException(
			status_code=422,
			detail=f"无法将现金金额从 {source_currency} 换算为 {target_currency}: {exc}",
		) from exc

	normalized_rate = quantize_decimal(rate)
	return quantize_decimal(to_decimal(amount) * normalized_rate), normalized_rate

def _list_cash_ledger_entries_for_account(
	session: Session,
	*,
	user_id: str,
	cash_account_id: int,
) -> list[CashLedgerEntry]:
	return list(
		session.exec(
			select(CashLedgerEntry)
			.where(CashLedgerEntry.user_id == user_id)
			.where(CashLedgerEntry.cash_account_id == cash_account_id)
			.order_by(
				CashLedgerEntry.happened_on.asc(),
				CashLedgerEntry.created_at.asc(),
				CashLedgerEntry.id.asc(),
			),
		),
	)

def _get_cash_account_initial_ledger_entry(
	session: Session,
	*,
	user_id: str,
	cash_account_id: int,
) -> CashLedgerEntry | None:
	return session.exec(
		select(CashLedgerEntry)
		.where(CashLedgerEntry.user_id == user_id)
		.where(CashLedgerEntry.cash_account_id == cash_account_id)
		.where(CashLedgerEntry.entry_type == "INITIAL_BALANCE")
		.where(CashLedgerEntry.holding_transaction_id.is_(None))
		.where(CashLedgerEntry.cash_transfer_id.is_(None))
		.order_by(CashLedgerEntry.created_at.asc(), CashLedgerEntry.id.asc()),
	).first()

def _sum_cash_account_ledger_balance(
	session: Session,
	*,
	user_id: str,
	cash_account_id: int,
	exclude_entry_id: int | None = None,
) -> Decimal:
	entries = _list_cash_ledger_entries_for_account(
		session,
		user_id=user_id,
		cash_account_id=cash_account_id,
	)
	total = DECIMAL_ZERO
	for entry in entries:
		if exclude_entry_id is not None and entry.id == exclude_entry_id:
			continue
		total += entry.amount
	return quantize_decimal(total)

def _sync_cash_account_balance_from_ledger(
	session: Session,
	*,
	account: CashAccount,
) -> Decimal:
	account.balance = _sum_cash_account_ledger_balance(
		session,
		user_id=account.user_id,
		cash_account_id=account.id or 0,
	)
	_touch_model(account)
	session.add(account)
	session.flush()
	return account.balance

def _create_cash_ledger_entry(
	session: Session,
	*,
	user_id: str,
	cash_account_id: int,
	entry_type: str,
	amount: Decimal | float | int,
	currency: str,
	happened_on: date,
	note: str | None = None,
	holding_transaction_id: int | None = None,
	cash_transfer_id: int | None = None,
) -> CashLedgerEntry:
	entry = CashLedgerEntry(
		user_id=user_id,
		cash_account_id=cash_account_id,
		entry_type=entry_type,
		amount=quantize_decimal(amount),
		currency=_normalize_currency(currency),
		happened_on=happened_on,
		note=_normalize_optional_text(note),
		holding_transaction_id=holding_transaction_id,
		cash_transfer_id=cash_transfer_id,
	)
	session.add(entry)
	session.flush()
	return entry

def _reconcile_cash_account_initial_ledger_entry(
	session: Session,
	*,
	account: CashAccount,
	target_balance: Decimal | float | int,
) -> CashLedgerEntry:
	initial_entry = _get_cash_account_initial_ledger_entry(
		session,
		user_id=account.user_id,
		cash_account_id=account.id or 0,
	)
	non_initial_total = _sum_cash_account_ledger_balance(
		session,
		user_id=account.user_id,
		cash_account_id=account.id or 0,
		exclude_entry_id=initial_entry.id if initial_entry is not None else None,
	)
	started_on = account.started_on or _coerce_utc_datetime(account.created_at).date()
	required_initial_amount = quantize_decimal(to_decimal(target_balance) - non_initial_total)
	if initial_entry is None:
		initial_entry = CashLedgerEntry(
			user_id=account.user_id,
			cash_account_id=account.id or 0,
			entry_type="INITIAL_BALANCE",
			amount=required_initial_amount,
			currency=_normalize_currency(account.currency),
			happened_on=started_on,
			note="账户初始余额",
		)
	else:
		initial_entry.amount = required_initial_amount
		initial_entry.currency = _normalize_currency(account.currency)
		initial_entry.happened_on = started_on
		initial_entry.note = "账户初始余额"
		_touch_model(initial_entry)
	session.add(initial_entry)
	session.flush()
	_sync_cash_account_balance_from_ledger(session, account=account)
	return initial_entry

def _delete_cash_ledger_entries_for_holding_transaction(
	session: Session,
	*,
	user_id: str,
	holding_transaction_id: int,
) -> list[CashLedgerEntry]:
	entries = list(
		session.exec(
			select(CashLedgerEntry)
			.where(CashLedgerEntry.user_id == user_id)
			.where(CashLedgerEntry.holding_transaction_id == holding_transaction_id),
		),
	)
	for entry in entries:
		session.delete(entry)
	return entries

def _delete_cash_ledger_entries_for_transfer(
	session: Session,
	*,
	user_id: str,
	cash_transfer_id: int,
) -> list[CashLedgerEntry]:
	entries = list(
		session.exec(
			select(CashLedgerEntry)
			.where(CashLedgerEntry.user_id == user_id)
			.where(CashLedgerEntry.cash_transfer_id == cash_transfer_id),
		),
	)
	for entry in entries:
		session.delete(entry)
	return entries

def _get_manual_cash_ledger_adjustment(
	session: Session,
	*,
	user_id: str,
	entry_id: int,
) -> CashLedgerEntry:
	entry = session.get(CashLedgerEntry, entry_id)
	if entry is None or entry.user_id != user_id:
		raise HTTPException(status_code=404, detail="Cash ledger adjustment not found.")
	if entry.entry_type != "MANUAL_ADJUSTMENT":
		raise HTTPException(status_code=422, detail="只有手工账本调整允许直接编辑。")
	if entry.holding_transaction_id is not None or entry.cash_transfer_id is not None:
		raise HTTPException(status_code=422, detail="该账本记录由系统生成，不能直接修改。")
	return entry

def _create_auto_cash_account_from_sell(
	session: Session,
	*,
	current_user: UserAccount,
	symbol: str,
	name: str,
	market: str,
	quantity: Decimal,
	execution_price: Decimal,
	currency: str,
	traded_on: date,
	transaction_id: int | None,
) -> AppliedCashSettlement:
	proceeds = quantize_decimal(quantity * execution_price)
	cash_entry = CashAccount(
		user_id=current_user.username,
		name=f"{symbol} 卖出回款",
		platform="交易回款",
		currency=_normalize_currency(currency),
		balance=DECIMAL_ZERO,
		account_type="OTHER",
		started_on=traded_on,
		note=_build_sell_proceeds_note(
			symbol=symbol,
			name=name,
			market=market,
			quantity=quantity,
			execution_price=execution_price,
			source_currency=currency,
			transaction_id=transaction_id,
		),
	)
	session.add(cash_entry)
	session.flush()
	_reconcile_cash_account_initial_ledger_entry(
		session,
		account=cash_entry,
		target_balance=DECIMAL_ZERO,
	)
	_create_cash_ledger_entry(
		session,
		user_id=current_user.username,
		cash_account_id=cash_entry.id or 0,
		entry_type="SELL_PROCEEDS",
		amount=proceeds,
		currency=cash_entry.currency,
		happened_on=traded_on,
		note=_build_sell_proceeds_note(
			symbol=symbol,
			name=name,
			market=market,
			quantity=quantity,
			execution_price=execution_price,
			source_currency=currency,
			transaction_id=transaction_id,
		),
		holding_transaction_id=transaction_id,
	)
	_sync_cash_account_balance_from_ledger(session, account=cash_entry)
	_record_asset_mutation(
		session,
		current_user,
		entity_type="CASH_ACCOUNT",
		entity_id=cash_entry.id,
		operation="CREATE",
		before_state=None,
		after_state=_capture_model_state(cash_entry),
		reason=f"AUTO_SELL_PROCEEDS#{transaction_id}" if transaction_id is not None else "AUTO_SELL_PROCEEDS",
	)
	return AppliedCashSettlement(
		cash_account=cash_entry,
		settled_amount=proceeds,
		settled_currency=_normalize_currency(currency),
		handling="CREATE_NEW_CASH",
		flow_direction="INFLOW",
		ledger_entry_type="SELL_PROCEEDS",
		auto_created_cash_account=True,
	)

def _add_sell_proceeds_to_existing_cash_account(
	session: Session,
	*,
	current_user: UserAccount,
	account_id: int,
	symbol: str,
	name: str,
	market: str,
	quantity: Decimal,
	execution_price: Decimal,
	source_currency: str,
	traded_on: date,
	transaction_id: int | None,
) -> AppliedCashSettlement:
	account = session.get(CashAccount, account_id)
	if account is None or account.user_id != current_user.username:
		raise HTTPException(status_code=404, detail="目标现金账户不存在。")

	proceeds = quantize_decimal(quantity * execution_price)
	converted_amount, _fx_rate = _convert_cash_amount_between_currencies(
		amount=proceeds,
		from_currency=source_currency,
		to_currency=account.currency,
	)
	before_state = _capture_model_state(account)
	account.note = _prepend_note_entry(
		account.note,
		_build_sell_proceeds_note(
			symbol=symbol,
			name=name,
			market=market,
			quantity=quantity,
			execution_price=execution_price,
			source_currency=source_currency,
			settled_amount=converted_amount,
			settled_currency=account.currency,
			transaction_id=transaction_id,
		),
	)
	_create_cash_ledger_entry(
		session,
		user_id=current_user.username,
		cash_account_id=account.id or 0,
		entry_type="SELL_PROCEEDS",
		amount=converted_amount,
		currency=account.currency,
		happened_on=traded_on,
		note=_build_sell_proceeds_note(
			symbol=symbol,
			name=name,
			market=market,
			quantity=quantity,
			execution_price=execution_price,
			source_currency=source_currency,
			settled_amount=converted_amount,
			settled_currency=account.currency,
			transaction_id=transaction_id,
		),
		holding_transaction_id=transaction_id,
	)
	_sync_cash_account_balance_from_ledger(session, account=account)
	_record_asset_mutation(
		session,
		current_user,
		entity_type="CASH_ACCOUNT",
		entity_id=account.id,
		operation="UPDATE",
		before_state=before_state,
		after_state=_capture_model_state(account),
		reason=f"SELL_PROCEEDS#{transaction_id}" if transaction_id is not None else "SELL_PROCEEDS",
	)
	return AppliedCashSettlement(
		cash_account=account,
		settled_amount=converted_amount,
		settled_currency=_normalize_currency(account.currency),
		handling="ADD_TO_EXISTING_CASH",
		flow_direction="INFLOW",
		ledger_entry_type="SELL_PROCEEDS",
		auto_created_cash_account=False,
	)

def _build_cash_settlement_reversal_note(
	*,
	transaction_id: int,
	settled_amount: Decimal,
	settled_currency: str,
	flow_direction: str,
) -> str:
	action_label = "回款入账" if flow_direction == "INFLOW" else "买入扣款"
	return (
		f"冲销：撤回交易ID #{transaction_id} 的{action_label} "
		f"{decimal_to_float(display_money(settled_amount)):g} {_normalize_currency(settled_currency)}"
	)

def _build_buy_funding_note(
	*,
	symbol: str,
	name: str,
	market: str,
	quantity: Decimal,
	execution_price: Decimal,
	source_currency: str,
	transaction_id: int | None,
	settled_amount: Decimal | None = None,
	settled_currency: str | None = None,
) -> str:
	note = (
		f"用途：买入 {name}({symbol}) [{market}] "
		f"数量 {decimal_to_float(display_quantity(quantity)):g}，"
		f"成交价 {decimal_to_float(display_price(execution_price)):g} {_normalize_currency(source_currency)}"
	)
	if settled_amount is not None and settled_currency:
		note += (
			f"，自动扣款 {decimal_to_float(display_money(settled_amount)):g} "
			f"{_normalize_currency(settled_currency)}"
		)
	if transaction_id is not None:
		note += f"，交易ID #{transaction_id}"
	return note

def _get_holding_transaction_cash_settlement(
	session: Session,
	*,
	user_id: str,
	holding_transaction_id: int,
) -> HoldingTransactionCashSettlement | None:
	return session.exec(
		select(HoldingTransactionCashSettlement)
		.where(HoldingTransactionCashSettlement.user_id == user_id)
		.where(HoldingTransactionCashSettlement.holding_transaction_id == holding_transaction_id),
	).first()

def _record_holding_transaction_cash_settlement(
	session: Session,
	*,
	current_user: UserAccount,
	transaction: SecurityHoldingTransaction,
	applied_cash_settlement: AppliedCashSettlement,
) -> HoldingTransactionCashSettlement:
	settlement = _get_holding_transaction_cash_settlement(
		session,
		user_id=current_user.username,
		holding_transaction_id=transaction.id or 0,
	)
	if settlement is None:
		settlement = HoldingTransactionCashSettlement(
			user_id=current_user.username,
			holding_transaction_id=transaction.id or 0,
			cash_account_id=applied_cash_settlement.cash_account.id or 0,
			handling=applied_cash_settlement.handling,
			settled_amount=applied_cash_settlement.settled_amount,
			settled_currency=applied_cash_settlement.settled_currency,
			source_amount=quantize_decimal(
				transaction.quantity * (transaction.price or DECIMAL_ZERO),
			),
			source_currency=_normalize_currency(transaction.fallback_currency),
			flow_direction=applied_cash_settlement.flow_direction,
			auto_created_cash_account=applied_cash_settlement.auto_created_cash_account,
		)
	else:
		settlement.cash_account_id = applied_cash_settlement.cash_account.id or 0
		settlement.handling = applied_cash_settlement.handling
		settlement.settled_amount = applied_cash_settlement.settled_amount
		settlement.settled_currency = applied_cash_settlement.settled_currency
		settlement.source_amount = quantize_decimal(
			transaction.quantity * (transaction.price or DECIMAL_ZERO),
		)
		settlement.source_currency = _normalize_currency(transaction.fallback_currency)
		settlement.flow_direction = applied_cash_settlement.flow_direction
		settlement.auto_created_cash_account = applied_cash_settlement.auto_created_cash_account
		_touch_model(settlement)

	session.add(settlement)
	session.flush()
	return settlement

def _reverse_holding_transaction_cash_settlement(
	session: Session,
	*,
	current_user: UserAccount,
	transaction: SecurityHoldingTransaction,
) -> CashAccount | None:
	settlement = _get_holding_transaction_cash_settlement(
		session,
		user_id=current_user.username,
		holding_transaction_id=transaction.id or 0,
	)
	if settlement is None:
		return None

	account = session.get(CashAccount, settlement.cash_account_id)
	if account is None or account.user_id != current_user.username:
		_delete_cash_ledger_entries_for_holding_transaction(
			session,
			user_id=current_user.username,
			holding_transaction_id=transaction.id or 0,
		)
		session.delete(settlement)
		return None

	before_state = _capture_model_state(account)
	_delete_cash_ledger_entries_for_holding_transaction(
		session,
		user_id=current_user.username,
		holding_transaction_id=transaction.id or 0,
	)
	_sync_cash_account_balance_from_ledger(session, account=account)
	account_should_delete = (
		settlement.auto_created_cash_account
		and account.platform == "交易回款"
		and account.balance <= HOLDING_QUANTITY_EPSILON
		and len(
			[
				entry
				for entry in _list_cash_ledger_entries_for_account(
					session,
					user_id=current_user.username,
					cash_account_id=account.id or 0,
				)
				if entry.entry_type != "INITIAL_BALANCE"
			],
		)
		== 0
	)
	if account_should_delete:
		for entry in _list_cash_ledger_entries_for_account(
			session,
			user_id=current_user.username,
			cash_account_id=account.id or 0,
		):
			session.delete(entry)
		session.delete(account)
		_record_asset_mutation(
			session,
			current_user,
			entity_type="CASH_ACCOUNT",
			entity_id=account.id,
			operation="DELETE",
			before_state=before_state,
			after_state=None,
			reason=f"SELL_PROCEEDS_REVERSAL#{transaction.id}",
		)
	else:
		account.note = _prepend_note_entry(
			account.note,
			_build_cash_settlement_reversal_note(
				transaction_id=transaction.id or 0,
				settled_amount=settlement.settled_amount,
				settled_currency=settlement.settled_currency,
				flow_direction=settlement.flow_direction,
			),
		)
		session.add(account)
		_record_asset_mutation(
			session,
			current_user,
			entity_type="CASH_ACCOUNT",
			entity_id=account.id,
			operation="UPDATE",
			before_state=before_state,
			after_state=_capture_model_state(account),
			reason=f"SELL_PROCEEDS_REVERSAL#{transaction.id}",
		)

	session.delete(settlement)
	return account

def _apply_buy_funding_handling(
	session: Session,
	*,
	current_user: UserAccount,
	handling: str | None,
	target_account_id: int | None,
	symbol: str,
	name: str,
	market: str,
	quantity: Decimal,
	execution_price: Decimal,
	currency: str,
	traded_on: date,
	transaction_id: int | None,
) -> AppliedCashSettlement | None:
	effective_handling = handling or (
		"DEDUCT_FROM_EXISTING_CASH" if target_account_id is not None else None
	)
	if effective_handling is None:
		return None
	if effective_handling != "DEDUCT_FROM_EXISTING_CASH":
		raise HTTPException(status_code=422, detail="当前只支持从现有现金账户扣款。")
	if target_account_id is None:
		raise HTTPException(status_code=422, detail="买入从现金账户扣款时必须选择目标现金账户。")

	account = session.get(CashAccount, target_account_id)
	if account is None or account.user_id != current_user.username:
		raise HTTPException(status_code=404, detail="目标现金账户不存在。")

	gross_amount = quantize_decimal(quantity * execution_price)
	settled_amount, _fx_rate = _convert_cash_amount_between_currencies(
		amount=gross_amount,
		from_currency=currency,
		to_currency=account.currency,
	)
	if account.balance + HOLDING_QUANTITY_EPSILON < settled_amount:
		raise HTTPException(
			status_code=422,
			detail=(
				f"{account.name} 余额不足。当前余额 "
				f"{decimal_to_float(display_money(account.balance)):g} {account.currency}，"
				f"本次扣款 {decimal_to_float(display_money(settled_amount)):g} {account.currency}。"
			),
		)

	before_state = _capture_model_state(account)
	account.note = _prepend_note_entry(
		account.note,
		_build_buy_funding_note(
			symbol=symbol,
			name=name,
			market=market,
			quantity=quantity,
			execution_price=execution_price,
			source_currency=currency,
			settled_amount=settled_amount,
			settled_currency=account.currency,
			transaction_id=transaction_id,
		),
	)
	_create_cash_ledger_entry(
		session,
		user_id=current_user.username,
		cash_account_id=account.id or 0,
		entry_type="BUY_FUNDING",
		amount=-settled_amount,
		currency=account.currency,
		happened_on=traded_on,
		note=_build_buy_funding_note(
			symbol=symbol,
			name=name,
			market=market,
			quantity=quantity,
			execution_price=execution_price,
			source_currency=currency,
			settled_amount=settled_amount,
			settled_currency=account.currency,
			transaction_id=transaction_id,
		),
		holding_transaction_id=transaction_id,
	)
	_sync_cash_account_balance_from_ledger(session, account=account)
	_record_asset_mutation(
		session,
		current_user,
		entity_type="CASH_ACCOUNT",
		entity_id=account.id,
		operation="UPDATE",
		before_state=before_state,
		after_state=_capture_model_state(account),
		reason=f"BUY_FUNDING#{transaction_id}" if transaction_id is not None else "BUY_FUNDING",
	)
	return AppliedCashSettlement(
		cash_account=account,
		settled_amount=settled_amount,
		settled_currency=_normalize_currency(account.currency),
		handling="DEDUCT_FROM_EXISTING_CASH",
		flow_direction="OUTFLOW",
		ledger_entry_type="BUY_FUNDING",
		auto_created_cash_account=False,
	)

def _apply_sell_proceeds_handling(
	session: Session,
	*,
	current_user: UserAccount,
	handling: str,
	target_account_id: int | None,
	symbol: str,
	name: str,
	market: str,
	quantity: Decimal,
	execution_price: Decimal,
	currency: str,
	traded_on: date,
	transaction_id: int | None,
) -> AppliedCashSettlement | None:
	if handling == "DISCARD":
		return None
	if handling == "ADD_TO_EXISTING_CASH":
		if target_account_id is None:
			raise HTTPException(status_code=422, detail="卖出并入现有现金时必须选择目标现金账户。")
		return _add_sell_proceeds_to_existing_cash_account(
			session,
			current_user=current_user,
			account_id=target_account_id,
			symbol=symbol,
			name=name,
			market=market,
				quantity=quantity,
				execution_price=execution_price,
				source_currency=currency,
				traded_on=traded_on,
				transaction_id=transaction_id,
			)
	return _create_auto_cash_account_from_sell(
		session,
		current_user=current_user,
		symbol=symbol,
		name=name,
		market=market,
		quantity=quantity,
		execution_price=execution_price,
		currency=currency,
		traded_on=traded_on,
		transaction_id=transaction_id,
	)

__all__ = ['AppliedCashSettlement', 'HoldingLot', 'ProjectedHoldingState', '_holding_transaction_side_priority', '_holding_transaction_event_at', '_holding_transaction_sort_key', '_projected_holding_quantity', '_projected_holding_started_on', '_projected_holding_cost_basis', '_validate_holding_quantity_for_market', '_normalize_holding_transaction_side', '_list_holdings_for_symbol', '_delete_holding_transactions_for_symbol', '_reverse_and_delete_holding_transactions_for_symbol', '_list_holding_transaction_settlements', '_to_holding_transaction_reads', '_ensure_transaction_baseline_from_holding_snapshot', '_reset_holding_transactions_from_snapshot', '_get_latest_holding_transaction_for_symbol', '_apply_holding_transaction_to_state', '_project_holding_state_from_sorted_transactions', '_project_holding_state_from_transactions', '_sync_holding_projection_for_symbol', '_resolve_sell_execution_price_and_currency', '_build_sell_proceeds_note', '_prepend_note_entry', '_convert_cash_amount_between_currencies', '_list_cash_ledger_entries_for_account', '_get_cash_account_initial_ledger_entry', '_sum_cash_account_ledger_balance', '_sync_cash_account_balance_from_ledger', '_create_cash_ledger_entry', '_reconcile_cash_account_initial_ledger_entry', '_delete_cash_ledger_entries_for_holding_transaction', '_delete_cash_ledger_entries_for_transfer', '_get_manual_cash_ledger_adjustment', '_create_auto_cash_account_from_sell', '_add_sell_proceeds_to_existing_cash_account', '_build_cash_settlement_reversal_note', '_build_buy_funding_note', '_get_holding_transaction_cash_settlement', '_record_holding_transaction_cash_settlement', '_reverse_holding_transaction_cash_settlement', '_apply_buy_funding_handling', '_apply_sell_proceeds_handling']
