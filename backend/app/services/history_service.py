from __future__ import annotations

from decimal import Decimal
from datetime import date, datetime, timedelta

from sqlalchemy import delete, update
from sqlmodel import Session, select

from app import runtime_state
from app.fixed_precision import (
	DECIMAL_ZERO,
	display_money,
	display_percent,
	quantize_decimal,
	to_decimal,
)
from app.models import (
	CashAccount,
	CashLedgerEntry,
	FixedAsset,
	HOLDING_HISTORY_SYNC_STATUSES,
	HoldingPerformanceSnapshot,
	HoldingHistorySyncRequest,
	LiabilityEntry,
	OtherAsset,
	PortfolioSnapshot,
	SecurityHolding,
	SecurityHoldingTransaction,
	UserAccount,
	utc_now,
)
from app.services import service_context
from app.services.common_service import (
	_calculate_return_pct,
	_coerce_utc_datetime,
	_current_hour_bucket,
	_date_start_utc,
	_invalidate_dashboard_cache,
	_normalize_currency,
)
from app.services.history_sync_service import (
	_build_hour_buckets,
	_fill_hourly_prices,
	_has_holding_history_sync_pending,
)
from app.services.market_data import QuoteLookupError
from app.services.holding_projection_service import (
	HOLDING_QUANTITY_EPSILON,
	_ensure_transaction_baseline_from_holding_snapshot,
	ProjectedHoldingState,
	_apply_holding_transaction_to_state,
	_holding_transaction_event_at,
	_holding_transaction_sort_key,
	_projected_holding_cost_basis,
	_projected_holding_quantity,
)


def _backfill_missing_holding_transactions(session: Session, user_id: str) -> None:
	holdings = list(
		session.exec(
			select(SecurityHolding)
			.where(SecurityHolding.user_id == user_id)
			.order_by(SecurityHolding.symbol.asc(), SecurityHolding.market.asc(), SecurityHolding.id.asc()),
		),
	)
	if not holdings:
		return

	existing_pairs = set(
		session.exec(
			select(
				SecurityHoldingTransaction.symbol,
				SecurityHoldingTransaction.market,
			)
			.where(SecurityHoldingTransaction.user_id == user_id)
			.distinct(),
		).all(),
	)
	has_changes = False
	for holding in holdings:
		pair = (holding.symbol, holding.market)
		if pair in existing_pairs:
			continue
		_ensure_transaction_baseline_from_holding_snapshot(
			session,
			holding=holding,
		)
		existing_pairs.add(pair)
		has_changes = True

	if has_changes:
		session.flush()

async def _rebuild_user_holding_history_snapshots(session: Session, user_id: str) -> None:
	now = utc_now()
	end_hour = _current_hour_bucket(now)
	_backfill_missing_holding_transactions(session, user_id)
	transactions = list(
		session.exec(
			select(SecurityHoldingTransaction)
			.where(SecurityHoldingTransaction.user_id == user_id)
			.order_by(
				SecurityHoldingTransaction.symbol,
				SecurityHoldingTransaction.market,
				SecurityHoldingTransaction.traded_on,
				SecurityHoldingTransaction.created_at,
				SecurityHoldingTransaction.id,
			),
		),
	)

	session.exec(
		delete(HoldingPerformanceSnapshot).where(
			HoldingPerformanceSnapshot.user_id == user_id,
			HoldingPerformanceSnapshot.scope.in_(("HOLDING", "TOTAL")),
		),
	)
	session.commit()

	weighted_sum_by_hour: dict[datetime, Decimal] = {}
	total_basis_by_hour: dict[datetime, Decimal] = {}
	history_warnings: list[str] = []
	transactions_by_symbol: dict[tuple[str, str], list[SecurityHoldingTransaction]] = {}

	for transaction in transactions:
		transactions_by_symbol.setdefault(
			(transaction.symbol, transaction.market),
			[],
		).append(transaction)

	for (symbol, market), symbol_transactions in transactions_by_symbol.items():
		sorted_transactions = sorted(symbol_transactions, key=_holding_transaction_sort_key)
		if not sorted_transactions:
			continue

		start_at = _date_start_utc(sorted_transactions[0].traded_on)
		if start_at > end_hour:
			continue

		known_points, history_currency, warnings = await service_context.market_data_client.fetch_hourly_price_series(
			symbol,
			market=market,
			start_at=start_at,
			end_at=end_hour + timedelta(hours=1),
		)
		history_warnings.extend(warnings)

		fallback_price = next(
			(
				item.price
				for item in reversed(sorted_transactions)
				if item.price is not None and item.price > 0
			),
			DECIMAL_ZERO,
		)
		currency_for_pricing = history_currency
		if not known_points or not currency_for_pricing:
			latest_quote, quote_warnings = await service_context.market_data_client.fetch_quote(
				symbol,
				market,
			)
			history_warnings.extend(quote_warnings)
			if latest_quote.price > 0:
				fallback_price = latest_quote.price
			currency_for_pricing = currency_for_pricing or latest_quote.currency

		currency_code = _normalize_currency(
			currency_for_pricing or sorted_transactions[-1].fallback_currency,
		)
		if currency_code == "CNY":
			fx_to_cny = Decimal("1")
		else:
			fx_to_cny, fx_warnings = await service_context.market_data_client.fetch_fx_rate(
				currency_code,
				"CNY",
			)
			fx_to_cny = quantize_decimal(fx_to_cny)
			history_warnings.extend(fx_warnings)

		hours = _build_hour_buckets(start_at, end_hour)
		filled_prices = _fill_hourly_prices(hours, known_points, fallback_price)
		symbol_rows: list[HoldingPerformanceSnapshot] = []
		event_index = 0
		first_transaction = sorted_transactions[0]
		projected_state = ProjectedHoldingState(
			symbol=symbol,
			name=first_transaction.name,
			market=market,
			fallback_currency=first_transaction.fallback_currency,
			broker=first_transaction.broker,
			note=first_transaction.note,
			lots=[],
		)
		for hour in hours:
			while (
				event_index < len(sorted_transactions)
				and _holding_transaction_event_at(sorted_transactions[event_index]) <= hour
			):
				_apply_holding_transaction_to_state(
					projected_state,
					sorted_transactions[event_index],
				)
				event_index += 1

			quantity = _projected_holding_quantity(projected_state)
			if quantity <= HOLDING_QUANTITY_EPSILON:
				continue

			cost_basis_price = _projected_holding_cost_basis(projected_state)
			if cost_basis_price is None or cost_basis_price <= 0:
				continue

			basis_value_cny = cost_basis_price * quantity * fx_to_cny
			if basis_value_cny <= 0:
				continue

			price = quantize_decimal(filled_prices.get(hour, DECIMAL_ZERO))
			return_pct = _calculate_return_pct(price, cost_basis_price)
			if return_pct is None:
				continue

			symbol_rows.append(
				HoldingPerformanceSnapshot(
					user_id=user_id,
					scope="HOLDING",
					symbol=symbol,
					name=projected_state.name,
					return_pct=return_pct,
					created_at=hour,
				),
			)
			weighted_sum_by_hour[hour] = (
				weighted_sum_by_hour.get(hour, DECIMAL_ZERO)
				+ quantize_decimal(return_pct * basis_value_cny)
			)
			total_basis_by_hour[hour] = total_basis_by_hour.get(hour, DECIMAL_ZERO) + basis_value_cny

		if symbol_rows:
			session.add_all(symbol_rows)
			session.commit()

	total_rows: list[HoldingPerformanceSnapshot] = []
	for hour in sorted(total_basis_by_hour):
		total_basis = total_basis_by_hour.get(hour, DECIMAL_ZERO)
		if total_basis <= 0:
			continue
		total_return_pct = display_percent(weighted_sum_by_hour[hour] / total_basis)
		total_rows.append(
			HoldingPerformanceSnapshot(
				user_id=user_id,
				scope="TOTAL",
				symbol=None,
				name="非现金资产",
				return_pct=total_return_pct,
				created_at=hour,
			),
		)
	if total_rows:
		session.add_all(total_rows)
		session.commit()

	if history_warnings:
		service_context.logger.warning(
			"Holding history rebuild warnings for user %s: %s",
			user_id,
			"; ".join(history_warnings[:8]),
		)

	await _rebuild_user_portfolio_snapshots(session, user_id)

def _resolve_asset_start_date(
	started_on: date | None,
	created_at: datetime | None = None,
) -> date | None:
	if started_on is not None:
		return started_on
	if created_at is None:
		return None
	return _coerce_utc_datetime(created_at).date()

async def _rebuild_user_portfolio_snapshots(session: Session, user_id: str) -> None:
	now = utc_now()
	end_hour = _current_hour_bucket(now)
	_backfill_missing_holding_transactions(session, user_id)
	cash_accounts = list(
		session.exec(
			select(CashAccount)
			.where(CashAccount.user_id == user_id)
			.order_by(CashAccount.id.asc()),
		),
	)
	ledger_entries = list(
		session.exec(
			select(CashLedgerEntry)
			.where(CashLedgerEntry.user_id == user_id)
			.order_by(
				CashLedgerEntry.happened_on.asc(),
				CashLedgerEntry.created_at.asc(),
				CashLedgerEntry.id.asc(),
			),
		),
	)
	transactions = list(
		session.exec(
			select(SecurityHoldingTransaction)
			.where(SecurityHoldingTransaction.user_id == user_id)
			.order_by(
				SecurityHoldingTransaction.symbol,
				SecurityHoldingTransaction.market,
				SecurityHoldingTransaction.traded_on,
				SecurityHoldingTransaction.created_at,
				SecurityHoldingTransaction.id,
			),
		),
	)
	fixed_assets = list(
		session.exec(
			select(FixedAsset)
			.where(FixedAsset.user_id == user_id)
			.order_by(FixedAsset.id.asc()),
		),
	)
	liabilities = list(
		session.exec(
			select(LiabilityEntry)
			.where(LiabilityEntry.user_id == user_id)
			.order_by(LiabilityEntry.id.asc()),
		),
	)
	other_assets = list(
		session.exec(
			select(OtherAsset)
			.where(OtherAsset.user_id == user_id)
			.order_by(OtherAsset.id.asc()),
		),
	)

	start_candidates: list[date] = []
	start_candidates.extend(entry.happened_on for entry in ledger_entries)
	start_candidates.extend(transaction.traded_on for transaction in transactions)
	start_candidates.extend(
		filter(
			None,
			[
				_resolve_asset_start_date(asset.started_on, asset.created_at)
				for asset in fixed_assets
			],
		),
	)
	start_candidates.extend(
		filter(
			None,
			[
				_resolve_asset_start_date(asset.started_on, asset.created_at)
				for asset in liabilities
			],
		),
	)
	start_candidates.extend(
		filter(
			None,
			[
				_resolve_asset_start_date(asset.started_on, asset.created_at)
				for asset in other_assets
			],
		),
	)
	if not start_candidates:
		session.exec(delete(PortfolioSnapshot).where(PortfolioSnapshot.user_id == user_id))
		return

	start_at = _date_start_utc(min(start_candidates))
	if start_at > end_hour:
		session.exec(delete(PortfolioSnapshot).where(PortfolioSnapshot.user_id == user_id))
		return

	hours = _build_hour_buckets(start_at, end_hour)
	hour_totals = {hour: DECIMAL_ZERO for hour in hours}
	fx_rate_cache: dict[str, Decimal] = {"CNY": Decimal("1")}

	async def resolve_fx_rate(currency_code: str) -> Decimal:
		normalized_currency = _normalize_currency(currency_code)
		if normalized_currency in fx_rate_cache:
			return fx_rate_cache[normalized_currency]
		try:
			rate, _warnings = await service_context.market_data_client.fetch_fx_rate(
				normalized_currency,
				"CNY",
			)
		except (QuoteLookupError, ValueError):
			rate = DECIMAL_ZERO
		fx_rate_cache[normalized_currency] = quantize_decimal(rate)
		return fx_rate_cache[normalized_currency]

	account_currency_by_id: dict[int, str] = {
		account.id or 0: _normalize_currency(account.currency) for account in cash_accounts
	}
	cash_entries_by_date: dict[date, list[CashLedgerEntry]] = {}
	for entry in ledger_entries:
		account_currency_by_id.setdefault(entry.cash_account_id, _normalize_currency(entry.currency))
		cash_entries_by_date.setdefault(entry.happened_on, []).append(entry)

	cash_event_dates = sorted(cash_entries_by_date)
	cash_event_index = 0
	cash_balances: dict[int, Decimal] = {}
	for hour in hours:
		while (
			cash_event_index < len(cash_event_dates)
			and _date_start_utc(cash_event_dates[cash_event_index]) <= hour
		):
			for entry in cash_entries_by_date[cash_event_dates[cash_event_index]]:
				cash_balances[entry.cash_account_id] = quantize_decimal(
					cash_balances.get(entry.cash_account_id, DECIMAL_ZERO) + entry.amount,
				)
			cash_event_index += 1

		cash_total = DECIMAL_ZERO
		for account_id, balance in cash_balances.items():
			if abs(balance) <= HOLDING_QUANTITY_EPSILON:
				continue
			fx_rate = await resolve_fx_rate(account_currency_by_id.get(account_id, "CNY"))
			cash_total += balance * fx_rate
		hour_totals[hour] += quantize_decimal(cash_total)

	transactions_by_symbol: dict[tuple[str, str], list[SecurityHoldingTransaction]] = {}
	for transaction in transactions:
		transactions_by_symbol.setdefault((transaction.symbol, transaction.market), []).append(transaction)

	for (symbol, market), symbol_transactions in transactions_by_symbol.items():
		sorted_transactions = sorted(symbol_transactions, key=_holding_transaction_sort_key)
		if not sorted_transactions:
			continue

		symbol_start = _date_start_utc(sorted_transactions[0].traded_on)
		try:
			known_points, history_currency, _warnings = await service_context.market_data_client.fetch_hourly_price_series(
				symbol,
				market=market,
				start_at=symbol_start,
				end_at=end_hour + timedelta(hours=1),
			)
		except (QuoteLookupError, ValueError):
			known_points, history_currency = [], None
		fallback_price = next(
			(
				item.price
				for item in reversed(sorted_transactions)
				if item.price is not None and item.price > 0
			),
			DECIMAL_ZERO,
		)
		currency_for_pricing = history_currency
		if not known_points or not currency_for_pricing:
			try:
				latest_quote, _quote_warnings = await service_context.market_data_client.fetch_quote(
					symbol,
					market,
				)
			except (QuoteLookupError, ValueError):
				latest_quote = None
			if latest_quote is not None and latest_quote.price > 0:
				fallback_price = latest_quote.price
			if latest_quote is not None and latest_quote.currency:
				currency_for_pricing = currency_for_pricing or latest_quote.currency

		fx_rate = await resolve_fx_rate(
			currency_for_pricing or sorted_transactions[-1].fallback_currency,
		)
		filled_prices = _fill_hourly_prices(hours, known_points, fallback_price)
		event_index = 0
		first_transaction = sorted_transactions[0]
		projected_state = ProjectedHoldingState(
			symbol=symbol,
			name=first_transaction.name,
			market=market,
			fallback_currency=first_transaction.fallback_currency,
			broker=first_transaction.broker,
			note=first_transaction.note,
			lots=[],
		)
		for hour in hours:
			while (
				event_index < len(sorted_transactions)
				and _holding_transaction_event_at(sorted_transactions[event_index]) <= hour
			):
				_apply_holding_transaction_to_state(
					projected_state,
					sorted_transactions[event_index],
				)
				event_index += 1

			quantity = _projected_holding_quantity(projected_state)
			if quantity <= HOLDING_QUANTITY_EPSILON:
				continue
			price = quantize_decimal(filled_prices.get(hour, DECIMAL_ZERO))
			if price <= 0:
				continue
			hour_totals[hour] += quantize_decimal(quantity * price * fx_rate)

	static_value_deltas: dict[datetime, Decimal] = {}

	def add_static_value(start_date: date | None, value_cny: Decimal) -> None:
		if start_date is None or value_cny == 0:
			return
		bucket = _date_start_utc(start_date)
		if bucket > end_hour:
			return
		static_value_deltas[bucket] = static_value_deltas.get(bucket, DECIMAL_ZERO) + value_cny

	for asset in fixed_assets:
		add_static_value(
			_resolve_asset_start_date(asset.started_on, asset.created_at),
			quantize_decimal(asset.current_value_cny),
		)
	for asset in other_assets:
		add_static_value(
			_resolve_asset_start_date(asset.started_on, asset.created_at),
			quantize_decimal(asset.current_value_cny),
		)
	for liability in liabilities:
		fx_rate = await resolve_fx_rate(liability.currency)
		add_static_value(
			_resolve_asset_start_date(liability.started_on, liability.created_at),
			-quantize_decimal(liability.balance * fx_rate),
		)

	running_static_total = DECIMAL_ZERO
	rows: list[PortfolioSnapshot] = []
	for hour in hours:
		running_static_total += static_value_deltas.get(hour, DECIMAL_ZERO)
		rows.append(
			PortfolioSnapshot(
				user_id=user_id,
				total_value_cny=display_money(hour_totals.get(hour, DECIMAL_ZERO) + running_static_total),
				created_at=hour,
			),
		)

	session.exec(delete(PortfolioSnapshot).where(PortfolioSnapshot.user_id == user_id))
	if rows:
		session.add_all(rows)

	runtime_state.live_portfolio_states.pop(user_id, None)
	runtime_state.live_holdings_return_states.pop(user_id, None)
	_invalidate_dashboard_cache(user_id)


def _claim_next_pending_holding_history_sync_request(
	session: Session,
	*,
	user_id: str | None = None,
) -> HoldingHistorySyncRequest | None:
	now = utc_now()
	request_id_selector = (
		select(HoldingHistorySyncRequest.id)
		.where(HoldingHistorySyncRequest.status == HOLDING_HISTORY_SYNC_STATUSES[0])
		.order_by(HoldingHistorySyncRequest.requested_at.asc(), HoldingHistorySyncRequest.id.asc())
		.limit(1)
	)
	if user_id is not None:
		request_id_selector = request_id_selector.where(HoldingHistorySyncRequest.user_id == user_id)

	request_id_row = session.exec(
		update(HoldingHistorySyncRequest)
		.where(HoldingHistorySyncRequest.id == request_id_selector.scalar_subquery())
		.where(HoldingHistorySyncRequest.status == HOLDING_HISTORY_SYNC_STATUSES[0])
		.values(
			status=HOLDING_HISTORY_SYNC_STATUSES[1],
			started_at=now,
			completed_at=None,
			error_message=None,
		)
		.returning(HoldingHistorySyncRequest.id),
	).first()
	if request_id_row is None:
		session.rollback()
		return None

	session.commit()
	request_id = int(request_id_row[0])
	return session.get(HoldingHistorySyncRequest, request_id)

async def _process_pending_holding_history_sync_requests(
	session: Session,
	*,
	limit: int = 1,
	user_id: str | None = None,
) -> None:
	for _ in range(limit):
		request_row = _claim_next_pending_holding_history_sync_request(
			session,
			user_id=user_id,
		)
		if request_row is None:
			return

		try:
			await _rebuild_user_holding_history_snapshots(session, request_row.user_id)
		except Exception as exc:  # pragma: no cover - defensive path
			service_context.logger.exception(
				"Holding history rebuild failed for user %s.",
				request_row.user_id,
			)
			request_row.status = HOLDING_HISTORY_SYNC_STATUSES[0]
			request_row.error_message = str(exc)[:500]
			request_row.started_at = None
			request_row.completed_at = None
			session.add(request_row)
			session.commit()
			continue

		request_row.status = HOLDING_HISTORY_SYNC_STATUSES[2]
		request_row.error_message = None
		request_row.completed_at = utc_now()
		session.add(request_row)
		session.commit()

__all__ = ['_rebuild_user_holding_history_snapshots', '_resolve_asset_start_date', '_rebuild_user_portfolio_snapshots', '_process_pending_holding_history_sync_requests']
