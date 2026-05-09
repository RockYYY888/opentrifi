from __future__ import annotations

from sqlalchemy import delete
from sqlmodel import Session, select

from app.fixed_precision import DECIMAL_ZERO
from app.models import (
	HoldingTransactionCashSettlement,
	SecurityHolding,
	SecurityHoldingTransaction,
	UserAccount,
)
from app.schemas import SecurityHoldingTransactionRead
from app.services.common_service import (
	_coerce_utc_datetime,
	_normalize_currency,
	_server_today_date,
	_touch_model,
)
from app.services.holding_projection_parts.common import HOLDING_QUANTITY_EPSILON, ProjectedHoldingState
from app.services.holding_projection_parts.settlements import _reverse_holding_transaction_cash_settlement
from app.services.holding_projection_parts.state import (
	_holding_transaction_sort_key,
	_project_holding_state_from_sorted_transactions,
	_projected_holding_cost_basis,
	_projected_holding_quantity,
	_projected_holding_started_on,
)
from app.services.portfolio_read_service import _to_holding_transaction_read
from app.services.sql_expression import sql_expr

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
			.order_by(sql_expr(SecurityHolding.id).asc()),
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
				sql_expr(SecurityHoldingTransaction.traded_on).desc(),
				sql_expr(SecurityHoldingTransaction.created_at).desc(),
				sql_expr(SecurityHoldingTransaction.id).desc(),
			),
		),
	)
	if not transactions:
		return []

	transaction_ids = [transaction.id for transaction in transactions if transaction.id is not None]
	if transaction_ids:
		session.exec(
			delete(HoldingTransactionCashSettlement)
			.where(sql_expr(HoldingTransactionCashSettlement.user_id) == user_id)
			.where(sql_expr(HoldingTransactionCashSettlement.holding_transaction_id).in_(transaction_ids)),
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
				sql_expr(SecurityHoldingTransaction.traded_on).desc(),
				sql_expr(SecurityHoldingTransaction.created_at).desc(),
				sql_expr(SecurityHoldingTransaction.id).desc(),
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
			.where(sql_expr(HoldingTransactionCashSettlement.user_id) == current_user.username)
			.where(sql_expr(HoldingTransactionCashSettlement.holding_transaction_id).in_(transaction_ids)),
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
			.where(sql_expr(HoldingTransactionCashSettlement.holding_transaction_id).in_(transaction_ids)),
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
			sql_expr(SecurityHoldingTransaction.traded_on).desc(),
			sql_expr(SecurityHoldingTransaction.created_at).desc(),
			sql_expr(SecurityHoldingTransaction.id).desc(),
		)
		.limit(1),
	).first()

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
