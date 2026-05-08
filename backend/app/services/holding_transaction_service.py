from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import Header, HTTPException, Query
from fastapi.responses import Response
from sqlmodel import Session, select

from app.models import CashAccount, SecurityHolding, SecurityHoldingTransaction
from app.schemas import (
    HoldingTransactionApplyRead,
    SecurityHoldingCreate,
    SecurityHoldingRead,
    SecurityHoldingTransactionCreate,
    SecurityHoldingTransactionRead,
    SecurityHoldingTransactionUpdate,
    SecurityHoldingUpdate,
    SecurityQuoteRead,
    SecuritySearchRead,
)
from app.services import job_service, service_context
from app.fixed_precision import (
	DECIMAL_ZERO,
	decimal_to_float,
	display_price,
	display_quantity,
	quantize_decimal,
	quantize_optional_decimal,
	to_decimal,
)
from app.services.auth_service import CurrentUserDependency
from app.services.common_service import (
    _build_idempotency_request_hash,
    _capture_model_state,
    _ensure_date_not_future,
    _invalidate_dashboard_cache,
    _load_idempotent_response,
    _normalize_currency,
    _normalize_optional_text,
    _normalize_symbol,
    _record_asset_mutation,
    _store_idempotent_response,
    _touch_model,
)
from app.services.history_sync_service import _enqueue_holding_history_sync_request
from app.services.holding_projection_service import (
    HOLDING_QUANTITY_EPSILON,
    _apply_buy_funding_handling,
    _apply_sell_proceeds_handling,
    _ensure_transaction_baseline_from_holding_snapshot,
    _get_holding_transaction_cash_settlement,
    _get_latest_holding_transaction_for_symbol,
    _list_holdings_for_symbol,
    _normalize_holding_transaction_side,
    _project_holding_state_from_transactions,
    _projected_holding_quantity,
    _record_holding_transaction_cash_settlement,
    _reset_holding_transactions_from_snapshot,
    _resolve_sell_execution_price_and_currency,
    _reverse_and_delete_holding_transactions_for_symbol,
    _reverse_holding_transaction_cash_settlement,
    _sync_holding_projection_for_symbol,
    _to_holding_transaction_reads,
    _validate_holding_quantity_for_market,
)
from app.services.market_data import QuoteLookupError
from app.services.portfolio_read_service import (
    _to_cash_account_read,
    _to_holding_read,
    _to_holding_transaction_read,
)
from app.services.service_context import SessionDependency

async def list_holdings(
	current_user: CurrentUserDependency,
	session: SessionDependency,
) -> list[SecurityHoldingRead]:
	from app.services.dashboard_query_service import _get_cached_dashboard

	dashboard = await _get_cached_dashboard(session, current_user)
	holdings = list(
		session.exec(
			select(SecurityHolding)
			.where(SecurityHolding.user_id == current_user.username)
			.order_by(SecurityHolding.symbol, SecurityHolding.name),
		),
	)
	valued_holding_map = {holding.id: holding for holding in dashboard.holdings}
	items: list[SecurityHoldingRead] = []

	for holding in holdings:
		valued_holding = valued_holding_map.get(holding.id or 0)
		items.append(
			SecurityHoldingRead(
				id=holding.id or 0,
				symbol=holding.symbol,
				name=holding.name,
				quantity=holding.quantity,
				fallback_currency=holding.fallback_currency,
				cost_basis_price=holding.cost_basis_price,
				market=holding.market,
				broker=holding.broker,
				started_on=holding.started_on,
				note=holding.note,
				price=valued_holding.price if valued_holding else None,
				price_currency=valued_holding.price_currency if valued_holding else None,
				value_cny=valued_holding.value_cny if valued_holding else None,
				return_pct=valued_holding.return_pct if valued_holding else None,
				last_updated=valued_holding.last_updated if valued_holding else None,
			),
		)

	return items

def create_holding(
	payload: SecurityHoldingCreate,
	current_user: CurrentUserDependency,
	session: SessionDependency,
) -> SecurityHoldingRead:
	_ensure_date_not_future(payload.started_on, field_label="持仓日")
	holding = SecurityHolding(
		user_id=current_user.username,
		symbol=_normalize_symbol(payload.symbol, payload.market),
		name=payload.name.strip(),
		quantity=quantize_decimal(payload.quantity),
		fallback_currency=_normalize_currency(payload.fallback_currency),
		cost_basis_price=quantize_optional_decimal(payload.cost_basis_price),
		market=payload.market,
		broker=payload.broker,
		started_on=payload.started_on,
		note=payload.note,
	)
	session.add(holding)
	session.flush()
	_reset_holding_transactions_from_snapshot(
		session,
		holding=holding,
	)
	_record_asset_mutation(
		session,
		current_user,
		entity_type="HOLDING",
		entity_id=holding.id,
		operation="CREATE",
		before_state=None,
		after_state=_capture_model_state(holding),
	)
	_enqueue_holding_history_sync_request(
		session,
		user_id=current_user.username,
		trigger_symbol=holding.symbol,
	)
	job_service.enqueue_user_portfolio_snapshot_rebuild(session, current_user.username)
	session.commit()
	session.refresh(holding)
	_invalidate_dashboard_cache(current_user.username)
	return _to_holding_read(holding)

def update_holding(
	holding_id: int,
	payload: SecurityHoldingUpdate,
	current_user: CurrentUserDependency,
	session: SessionDependency,
) -> SecurityHoldingRead:
	holding = session.get(SecurityHolding, holding_id)
	if holding is None or holding.user_id != current_user.username:
		raise HTTPException(status_code=404, detail="Holding not found.")

	before_state = _capture_model_state(holding)
	creates_adjustment = bool(
		{"quantity", "cost_basis_price", "started_on"} & payload.model_fields_set,
	)
	if payload.started_on is not None:
		_ensure_date_not_future(payload.started_on, field_label="买入日期")
	effective_started_on = payload.started_on if payload.started_on is not None else holding.started_on
	if creates_adjustment and effective_started_on is None:
		raise HTTPException(status_code=422, detail="买入日期为必填项。")
	if payload.quantity is not None:
		_validate_holding_quantity_for_market(payload.quantity, holding.market)

	if creates_adjustment:
		symbol_transactions = list(
			session.exec(
				select(SecurityHoldingTransaction)
				.where(SecurityHoldingTransaction.user_id == current_user.username)
				.where(SecurityHoldingTransaction.symbol == holding.symbol)
				.where(SecurityHoldingTransaction.market == holding.market)
				.order_by(
					SecurityHoldingTransaction.traded_on.asc(),
					SecurityHoldingTransaction.created_at.asc(),
					SecurityHoldingTransaction.id.asc(),
				),
			),
		)
		earliest_transaction = symbol_transactions[0] if symbol_transactions else None
		correction_before_state = None
		correction_operation = "CREATE"

		if (
			earliest_transaction is not None
			and effective_started_on is not None
			and effective_started_on <= earliest_transaction.traded_on
		):
			adjustment_transaction = earliest_transaction
			correction_before_state = _capture_model_state(adjustment_transaction)
			correction_operation = "UPDATE"
		else:
			adjustment_transaction = SecurityHoldingTransaction(
				user_id=current_user.username,
				symbol=holding.symbol,
				name=holding.name,
				side="ADJUST",
				quantity=(
					quantize_decimal(payload.quantity)
					if payload.quantity is not None
					else holding.quantity
				),
				price=(
					quantize_optional_decimal(payload.cost_basis_price)
					if "cost_basis_price" in payload.model_fields_set
					else holding.cost_basis_price
				),
				fallback_currency=_normalize_currency(holding.fallback_currency),
				market=holding.market,
				broker=_normalize_optional_text(payload.broker)
				if "broker" in payload.model_fields_set
				else holding.broker,
				traded_on=effective_started_on,
				note=_normalize_optional_text(payload.note)
				if "note" in payload.model_fields_set
				else holding.note,
			)

		adjustment_transaction.user_id = current_user.username
		adjustment_transaction.symbol = holding.symbol
		adjustment_transaction.name = holding.name
		adjustment_transaction.side = "ADJUST"
		adjustment_transaction.quantity = (
			quantize_decimal(payload.quantity)
			if payload.quantity is not None
			else holding.quantity
		)
		adjustment_transaction.price = (
			quantize_optional_decimal(payload.cost_basis_price)
			if "cost_basis_price" in payload.model_fields_set
			else holding.cost_basis_price
		)
		adjustment_transaction.fallback_currency = _normalize_currency(holding.fallback_currency)
		adjustment_transaction.market = holding.market
		adjustment_transaction.broker = (
			_normalize_optional_text(payload.broker)
			if "broker" in payload.model_fields_set
			else holding.broker
		)
		adjustment_transaction.traded_on = effective_started_on
		adjustment_transaction.note = (
			_normalize_optional_text(payload.note)
			if "note" in payload.model_fields_set
			else holding.note
		)
		if correction_operation == "UPDATE":
			_touch_model(adjustment_transaction)
		session.add(adjustment_transaction)
		session.flush()
		_record_asset_mutation(
			session,
			current_user,
			entity_type="HOLDING_TRANSACTION",
			entity_id=adjustment_transaction.id,
			operation=correction_operation,
			before_state=correction_before_state,
			after_state=_capture_model_state(adjustment_transaction),
			reason=f"HOLDING_EDIT#{holding.id}",
		)
		synced_holding = _sync_holding_projection_for_symbol(
			session,
			user_id=current_user.username,
			symbol=holding.symbol,
			market=holding.market,
		)
		if synced_holding is not None:
			holding = synced_holding
	if "broker" in payload.model_fields_set:
		holding.broker = _normalize_optional_text(payload.broker)
	if "note" in payload.model_fields_set:
		holding.note = _normalize_optional_text(payload.note)
	_touch_model(holding)
	session.add(holding)
	if not creates_adjustment:
		latest_transaction = _get_latest_holding_transaction_for_symbol(
			session,
			user_id=current_user.username,
			symbol=holding.symbol,
			market=holding.market,
		)
		if latest_transaction is not None:
			if "broker" in payload.model_fields_set:
				latest_transaction.broker = _normalize_optional_text(payload.broker)
			if "note" in payload.model_fields_set:
				latest_transaction.note = _normalize_optional_text(payload.note)
			_touch_model(latest_transaction)
			session.add(latest_transaction)
	_record_asset_mutation(
		session,
		current_user,
		entity_type="HOLDING",
		entity_id=holding.id,
		operation="UPDATE",
		before_state=before_state,
		after_state=_capture_model_state(holding),
	)
	if creates_adjustment:
		_enqueue_holding_history_sync_request(
			session,
			user_id=current_user.username,
			trigger_symbol=holding.symbol,
		)
		job_service.enqueue_user_portfolio_snapshot_rebuild(session, current_user.username)
	session.commit()
	session.refresh(holding)
	_invalidate_dashboard_cache(current_user.username)
	return _to_holding_read(holding)

def delete_holding(
	holding_id: int,
	current_user: CurrentUserDependency,
	session: SessionDependency,
) -> Response:
	holding = session.get(SecurityHolding, holding_id)
	if holding is None or holding.user_id != current_user.username:
		raise HTTPException(status_code=404, detail="Holding not found.")

	before_state = _capture_model_state(holding)
	deleted_transactions = _reverse_and_delete_holding_transactions_for_symbol(
		session,
		current_user=current_user,
		symbol=holding.symbol,
		market=holding.market,
	)
	for transaction in deleted_transactions:
		_record_asset_mutation(
			session,
			current_user,
			entity_type="HOLDING_TRANSACTION",
			entity_id=transaction.id,
			operation="DELETE",
			before_state=_capture_model_state(transaction),
			after_state=None,
			reason=f"HOLDING_DELETE#{holding_id}",
		)
	session.delete(holding)
	_record_asset_mutation(
		session,
		current_user,
		entity_type="HOLDING",
		entity_id=holding_id,
		operation="DELETE",
		before_state=before_state,
		after_state=None,
	)
	_enqueue_holding_history_sync_request(
		session,
		user_id=current_user.username,
		trigger_symbol=holding.symbol,
	)
	job_service.enqueue_user_portfolio_snapshot_rebuild(session, current_user.username)
	session.commit()
	_invalidate_dashboard_cache(current_user.username)
	return Response(status_code=204)

def _list_holding_transactions_for_user(
	session: Session,
	*,
	user_id: str,
	symbol: str | None = None,
	market: str | None = None,
	side: str | None = None,
	limit: int = 100,
) -> list[SecurityHoldingTransaction]:
	statement = (
		select(SecurityHoldingTransaction)
		.where(SecurityHoldingTransaction.user_id == user_id)
		.order_by(
			SecurityHoldingTransaction.traded_on.desc(),
			SecurityHoldingTransaction.created_at.desc(),
			SecurityHoldingTransaction.id.desc(),
		)
		.limit(limit)
	)

	if symbol:
		statement = statement.where(
			SecurityHoldingTransaction.symbol == _normalize_symbol(symbol, market),
		)
	if market:
		statement = statement.where(
			SecurityHoldingTransaction.market == market.strip().upper(),
		)
	if side:
		statement = statement.where(
			SecurityHoldingTransaction.side == _normalize_holding_transaction_side(side),
		)

	return list(session.exec(statement))

def list_all_holding_transactions(
	current_user: CurrentUserDependency,
	session: SessionDependency,
	symbol: str | None = Query(default=None),
	market: str | None = Query(default=None),
	side: str | None = Query(default=None),
	limit: int = Query(default=100, ge=1, le=500),
) -> list[SecurityHoldingTransactionRead]:
	transactions = _list_holding_transactions_for_user(
		session,
		user_id=current_user.username,
		symbol=symbol,
		market=market,
		side=side,
		limit=limit,
	)
	return _to_holding_transaction_reads(
		session,
		user_id=current_user.username,
		transactions=transactions,
	)

def list_holding_transactions(
	holding_id: int,
	current_user: CurrentUserDependency,
	session: SessionDependency,
) -> list[SecurityHoldingTransactionRead]:
	holding = session.get(SecurityHolding, holding_id)
	if holding is None or holding.user_id != current_user.username:
		raise HTTPException(status_code=404, detail="Holding not found.")

	_ensure_transaction_baseline_from_holding_snapshot(
		session,
		holding=holding,
	)
	transactions = _list_holding_transactions_for_user(
		session,
		user_id=current_user.username,
		symbol=holding.symbol,
		market=holding.market,
	)
	return _to_holding_transaction_reads(
		session,
		user_id=current_user.username,
		transactions=transactions,
	)

def create_holding_transaction(
	payload: SecurityHoldingTransactionCreate,
	current_user: CurrentUserDependency,
	session: SessionDependency,
	idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> HoldingTransactionApplyRead:
	request_hash = _build_idempotency_request_hash(payload)
	idempotent_response = _load_idempotent_response(
		session,
		user_id=current_user.username,
		scope="holding_transaction.create",
		idempotency_key=idempotency_key,
		request_hash=request_hash,
		response_model=HoldingTransactionApplyRead,
	)
	if idempotent_response is not None:
		return idempotent_response

	_ensure_date_not_future(payload.traded_on, field_label="交易日")
	side = _normalize_holding_transaction_side(payload.side)
	if side not in {"BUY", "SELL"}:
		raise HTTPException(status_code=422, detail="只允许新增买入或卖出交易。")

	normalized_market = payload.market
	normalized_symbol = _normalize_symbol(payload.symbol, normalized_market)
	normalized_currency = _normalize_currency(payload.fallback_currency)
	normalized_broker = _normalize_optional_text(payload.broker)
	normalized_note = _normalize_optional_text(payload.note)
	normalized_name = payload.name.strip()
	sell_proceeds_handling = payload.sell_proceeds_handling or "CREATE_NEW_CASH"
	buy_funding_handling = payload.buy_funding_handling or (
		"DEDUCT_FROM_EXISTING_CASH" if payload.buy_funding_account_id is not None else None
	)

	existing_holdings = _list_holdings_for_symbol(
		session,
		user_id=current_user.username,
		symbol=normalized_symbol,
		market=normalized_market,
	)
	for holding in existing_holdings:
		_ensure_transaction_baseline_from_holding_snapshot(
			session,
			holding=holding,
		)

	normalized_quantity = quantize_decimal(payload.quantity)
	execution_price = (
		quantize_decimal(payload.price)
		if payload.price is not None and to_decimal(payload.price) > 0
		else None
	)
	execution_currency = normalized_currency
	if side == "SELL":
		projected_before = _project_holding_state_from_transactions(
			session,
			user_id=current_user.username,
			symbol=normalized_symbol,
			market=normalized_market,
		)
		available_quantity = (
			_projected_holding_quantity(projected_before)
			if projected_before is not None
			else DECIMAL_ZERO
		)
		if available_quantity + HOLDING_QUANTITY_EPSILON < normalized_quantity:
			raise HTTPException(
				status_code=422,
				detail=(
					f"{normalized_symbol} 可卖数量不足。当前可卖 "
					f"{decimal_to_float(display_quantity(available_quantity)):g}，"
					f"请求卖出 {decimal_to_float(display_quantity(normalized_quantity)):g}。"
				),
			)
		execution_price, execution_currency = _resolve_sell_execution_price_and_currency(
			symbol=normalized_symbol,
			market=normalized_market,
			fallback_currency=normalized_currency,
			payload_price=payload.price,
		)

	transaction = SecurityHoldingTransaction(
		user_id=current_user.username,
		symbol=normalized_symbol,
		name=normalized_name,
		side=side,
		quantity=normalized_quantity,
		price=execution_price,
		fallback_currency=execution_currency,
		market=normalized_market,
		broker=normalized_broker,
		traded_on=payload.traded_on,
		note=normalized_note,
	)
	session.add(transaction)
	session.flush()
	_record_asset_mutation(
		session,
		current_user,
		entity_type="HOLDING_TRANSACTION",
		entity_id=transaction.id,
		operation="CREATE",
		before_state=None,
		after_state=_capture_model_state(transaction),
	)

	holding = _sync_holding_projection_for_symbol(
		session,
		user_id=current_user.username,
		symbol=normalized_symbol,
		market=normalized_market,
	)
	affected_cash_account: CashAccount | None = None
	if side == "SELL" and execution_price is not None:
		applied_cash_settlement = _apply_sell_proceeds_handling(
			session,
			current_user=current_user,
			handling=sell_proceeds_handling,
			target_account_id=payload.sell_proceeds_account_id,
			symbol=normalized_symbol,
			name=normalized_name,
			market=normalized_market,
			quantity=normalized_quantity,
			execution_price=execution_price,
			currency=execution_currency,
			traded_on=payload.traded_on,
			transaction_id=transaction.id,
		)
		if applied_cash_settlement is not None:
			affected_cash_account = applied_cash_settlement.cash_account
			_record_holding_transaction_cash_settlement(
				session,
				current_user=current_user,
				transaction=transaction,
				applied_cash_settlement=applied_cash_settlement,
			)
	elif side == "BUY" and execution_price is not None:
		applied_cash_settlement = _apply_buy_funding_handling(
			session,
			current_user=current_user,
			handling=buy_funding_handling,
			target_account_id=payload.buy_funding_account_id,
			symbol=normalized_symbol,
			name=normalized_name,
			market=normalized_market,
			quantity=normalized_quantity,
			execution_price=execution_price,
			currency=execution_currency,
			traded_on=payload.traded_on,
			transaction_id=transaction.id,
		)
		if applied_cash_settlement is not None:
			affected_cash_account = applied_cash_settlement.cash_account
			_record_holding_transaction_cash_settlement(
				session,
				current_user=current_user,
				transaction=transaction,
				applied_cash_settlement=applied_cash_settlement,
			)
	_enqueue_holding_history_sync_request(
		session,
		user_id=current_user.username,
		trigger_symbol=normalized_symbol,
	)
	job_service.enqueue_user_portfolio_snapshot_rebuild(session, current_user.username)
	session.commit()
	session.refresh(transaction)
	if holding is not None:
		session.refresh(holding)
	if affected_cash_account is not None:
		session.refresh(affected_cash_account)
	settlement = _get_holding_transaction_cash_settlement(
		session,
		user_id=current_user.username,
		holding_transaction_id=transaction.id or 0,
	)
	response = HoldingTransactionApplyRead(
		transaction=_to_holding_transaction_read(transaction, settlement),
		holding=_to_holding_read(holding) if holding is not None else None,
		cash_account=_to_cash_account_read(affected_cash_account)
		if affected_cash_account is not None
		else None,
		sell_proceeds_handling=sell_proceeds_handling if side == "SELL" else None,
	)
	_store_idempotent_response(
		session,
		user_id=current_user.username,
		scope="holding_transaction.create",
		idempotency_key=idempotency_key,
		request_hash=request_hash,
		response=response,
	)
	session.commit()
	_invalidate_dashboard_cache(current_user.username)

	return response

def update_holding_transaction(
	transaction_id: int,
	payload: SecurityHoldingTransactionUpdate,
	current_user: CurrentUserDependency,
	session: SessionDependency,
) -> HoldingTransactionApplyRead:
	transaction = session.get(SecurityHoldingTransaction, transaction_id)
	if transaction is None or transaction.user_id != current_user.username:
		raise HTTPException(status_code=404, detail="Holding transaction not found.")

	if payload.traded_on is not None:
		_ensure_date_not_future(payload.traded_on, field_label="交易日")
	if payload.quantity is not None:
		_validate_holding_quantity_for_market(payload.quantity, transaction.market)
	if transaction.side != "SELL" and (
		payload.sell_proceeds_handling is not None
		or payload.sell_proceeds_account_id is not None
	):
		raise HTTPException(status_code=422, detail="只有卖出交易允许设置卖出回款处理。")
	if transaction.side != "BUY" and (
		payload.buy_funding_handling is not None
		or payload.buy_funding_account_id is not None
	):
		raise HTTPException(status_code=422, detail="只有买入交易允许设置买入扣款处理。")

	original_settlement = _get_holding_transaction_cash_settlement(
		session,
		user_id=current_user.username,
		holding_transaction_id=transaction.id or 0,
	)
	affected_cash_account: CashAccount | None = None
	if original_settlement is not None:
		affected_cash_account = _reverse_holding_transaction_cash_settlement(
			session,
			current_user=current_user,
			transaction=transaction,
		)

	before_state = _capture_model_state(transaction)
	if payload.name is not None:
		transaction.name = payload.name
	if payload.quantity is not None:
		transaction.quantity = quantize_decimal(payload.quantity)
	if "price" in payload.model_fields_set:
		transaction.price = quantize_optional_decimal(payload.price)
	if payload.fallback_currency is not None:
		transaction.fallback_currency = _normalize_currency(payload.fallback_currency)
	if "broker" in payload.model_fields_set:
		transaction.broker = _normalize_optional_text(payload.broker)
	if payload.traded_on is not None:
		transaction.traded_on = payload.traded_on
	if "note" in payload.model_fields_set:
		transaction.note = _normalize_optional_text(payload.note)
	_touch_model(transaction)
	session.add(transaction)

	holding = _sync_holding_projection_for_symbol(
		session,
		user_id=current_user.username,
		symbol=transaction.symbol,
		market=transaction.market,
	)

	sell_proceeds_handling: str | None = None
	buy_funding_handling: str | None = None
	if transaction.side == "SELL":
		sell_proceeds_handling = (
			payload.sell_proceeds_handling
			or (original_settlement.handling if original_settlement is not None else "DISCARD")
		)
		sell_proceeds_account_id = (
			payload.sell_proceeds_account_id
			if "sell_proceeds_account_id" in payload.model_fields_set
			else (
				original_settlement.cash_account_id
				if original_settlement is not None and not original_settlement.auto_created_cash_account
				else None
			)
		)
		if sell_proceeds_handling == "ADD_TO_EXISTING_CASH" and sell_proceeds_account_id is None:
			raise HTTPException(status_code=422, detail="卖出并入现有现金时必须选择目标现金账户。")
		if sell_proceeds_handling != "ADD_TO_EXISTING_CASH":
			sell_proceeds_account_id = None
		if transaction.price is None or transaction.price <= 0:
			raise HTTPException(
				status_code=422,
				detail="卖出交易需要有效成交价后才能重新处理卖出回款。",
			)

		applied_cash_settlement = _apply_sell_proceeds_handling(
			session,
			current_user=current_user,
			handling=sell_proceeds_handling,
			target_account_id=sell_proceeds_account_id,
			symbol=transaction.symbol,
			name=transaction.name,
			market=transaction.market,
			quantity=transaction.quantity,
			execution_price=transaction.price,
			currency=transaction.fallback_currency,
			traded_on=transaction.traded_on,
			transaction_id=transaction.id,
		)
		if applied_cash_settlement is not None:
			affected_cash_account = applied_cash_settlement.cash_account
			_record_holding_transaction_cash_settlement(
				session,
				current_user=current_user,
				transaction=transaction,
				applied_cash_settlement=applied_cash_settlement,
			)
	elif transaction.side == "BUY":
		buy_funding_handling = (
			payload.buy_funding_handling
			or (
				original_settlement.handling
				if original_settlement is not None and original_settlement.flow_direction == "OUTFLOW"
				else (
					"DEDUCT_FROM_EXISTING_CASH"
					if (
						"buy_funding_account_id" in payload.model_fields_set
						and payload.buy_funding_account_id is not None
					)
					else None
				)
			)
		)
		buy_funding_account_id = (
			payload.buy_funding_account_id
			if "buy_funding_account_id" in payload.model_fields_set
			else (
				original_settlement.cash_account_id
				if original_settlement is not None and original_settlement.flow_direction == "OUTFLOW"
				else None
			)
		)
		if transaction.price is None or transaction.price <= 0:
			if buy_funding_handling is not None:
				raise HTTPException(
					status_code=422,
					detail="买入交易需要有效成交价后才能重新处理买入扣款。",
				)
		elif buy_funding_handling is not None or buy_funding_account_id is not None:
			applied_cash_settlement = _apply_buy_funding_handling(
				session,
				current_user=current_user,
				handling=buy_funding_handling,
				target_account_id=buy_funding_account_id,
				symbol=transaction.symbol,
				name=transaction.name,
				market=transaction.market,
				quantity=transaction.quantity,
				execution_price=transaction.price,
				currency=transaction.fallback_currency,
				traded_on=transaction.traded_on,
				transaction_id=transaction.id,
			)
			if applied_cash_settlement is not None:
				affected_cash_account = applied_cash_settlement.cash_account
				_record_holding_transaction_cash_settlement(
					session,
					current_user=current_user,
					transaction=transaction,
					applied_cash_settlement=applied_cash_settlement,
				)

	_record_asset_mutation(
		session,
		current_user,
		entity_type="HOLDING_TRANSACTION",
		entity_id=transaction.id,
		operation="UPDATE",
		before_state=before_state,
		after_state=_capture_model_state(transaction),
	)
	_enqueue_holding_history_sync_request(
		session,
		user_id=current_user.username,
		trigger_symbol=transaction.symbol,
	)
	job_service.enqueue_user_portfolio_snapshot_rebuild(session, current_user.username)
	session.commit()
	session.refresh(transaction)
	if holding is not None:
		session.refresh(holding)
	if affected_cash_account is not None and session.get(CashAccount, affected_cash_account.id) is not None:
		session.refresh(affected_cash_account)
	settlement = _get_holding_transaction_cash_settlement(
		session,
		user_id=current_user.username,
		holding_transaction_id=transaction.id or 0,
	)
	_invalidate_dashboard_cache(current_user.username)

	return HoldingTransactionApplyRead(
		transaction=_to_holding_transaction_read(transaction, settlement),
		holding=_to_holding_read(holding) if holding is not None else None,
		cash_account=_to_cash_account_read(affected_cash_account)
		if affected_cash_account is not None and session.get(CashAccount, affected_cash_account.id) is not None
		else None,
		sell_proceeds_handling=sell_proceeds_handling if transaction.side == "SELL" else None,
	)

def delete_holding_transaction(
	transaction_id: int,
	current_user: CurrentUserDependency,
	session: SessionDependency,
) -> Response:
	transaction = session.get(SecurityHoldingTransaction, transaction_id)
	if transaction is None or transaction.user_id != current_user.username:
		raise HTTPException(status_code=404, detail="Holding transaction not found.")

	before_state = _capture_model_state(transaction)
	symbol = transaction.symbol
	market = transaction.market
	settlement = _get_holding_transaction_cash_settlement(
		session,
		user_id=current_user.username,
		holding_transaction_id=transaction.id or 0,
	)
	if settlement is not None:
		_reverse_holding_transaction_cash_settlement(
			session,
			current_user=current_user,
			transaction=transaction,
		)
	session.delete(transaction)
	_record_asset_mutation(
		session,
		current_user,
		entity_type="HOLDING_TRANSACTION",
		entity_id=transaction_id,
		operation="DELETE",
		before_state=before_state,
		after_state=None,
	)
	_sync_holding_projection_for_symbol(
		session,
		user_id=current_user.username,
		symbol=symbol,
		market=market,
	)
	_enqueue_holding_history_sync_request(
		session,
		user_id=current_user.username,
		trigger_symbol=symbol,
	)
	job_service.enqueue_user_portfolio_snapshot_rebuild(session, current_user.username)
	session.commit()
	_invalidate_dashboard_cache(current_user.username)
	return Response(status_code=204)

async def get_security_quote(
	symbol: str,
	market: str,
	__: CurrentUserDependency,
) -> SecurityQuoteRead:
	normalized_market = market.strip().upper()
	normalized_symbol = _normalize_symbol(symbol, normalized_market)
	try:
		quote, warnings = await service_context.market_data_client.fetch_quote(
			normalized_symbol,
			normalized_market,
		)
	except (QuoteLookupError, ValueError) as exc:
		raise HTTPException(status_code=404, detail=str(exc)) from exc

	return SecurityQuoteRead(
		symbol=quote.symbol,
		name=quote.name,
		market=normalized_market,
		price=display_price(quote.price),
		currency=_normalize_currency(quote.currency),
		market_time=quote.market_time,
		warnings=warnings,
	)

async def search_securities(
	q: str,
	__: CurrentUserDependency,
) -> list[SecuritySearchRead]:
	query = q.strip()
	if not query:
		return []

	return await service_context.market_data_client.search_securities(query)

__all__ = ['list_holdings', 'create_holding', 'update_holding', 'delete_holding', '_list_holding_transactions_for_user', 'list_all_holding_transactions', 'list_holding_transactions', 'create_holding_transaction', 'update_holding_transaction', 'delete_holding_transaction', 'get_security_quote', 'search_securities']
