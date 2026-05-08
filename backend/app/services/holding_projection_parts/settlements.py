from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from sqlmodel import Session, select

from app.fixed_precision import (
	DECIMAL_ZERO,
	decimal_to_float,
	display_money,
	display_price,
	display_quantity,
	quantize_decimal,
	to_decimal,
)
from app.models import CashAccount, HoldingTransactionCashSettlement, SecurityHoldingTransaction, UserAccount
from app.services import service_context
from app.services.common_service import (
	_capture_model_state,
	_normalize_currency,
	_record_asset_mutation,
	_touch_model,
)
from app.services.holding_projection_parts.cash_ledger import (
	_convert_cash_amount_between_currencies,
	_create_cash_ledger_entry,
	_delete_cash_ledger_entries_for_holding_transaction,
	_list_cash_ledger_entries_for_account,
	_prepend_note_entry,
	_reconcile_cash_account_initial_ledger_entry,
	_sync_cash_account_balance_from_ledger,
)
from app.services.holding_projection_parts.common import AppliedCashSettlement, HOLDING_QUANTITY_EPSILON
from app.services.market_data import QuoteLookupError

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
