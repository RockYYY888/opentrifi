from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime, timedelta
from time import monotonic
from typing import TypeVar

from sqlalchemy import delete
from sqlmodel import Session, select

from app import runtime_state
from app.database import engine
from app.models import (
	CashAccount,
	FixedAsset,
	HoldingPerformanceSnapshot,
	LiabilityEntry,
	OtherAsset,
	PortfolioSnapshot,
	RealtimeHoldingPerformanceSnapshot,
	RealtimePortfolioSnapshot,
	SecurityHolding,
	UserAccount,
	utc_now,
)
from app.runtime_state import LiveHoldingReturnPoint
from app.fixed_precision import (
	DECIMAL_ZERO,
	display_money,
	display_percent,
	multiply_decimals,
	quantize_decimal,
	to_decimal,
)
from app.services import service_context
from app.services.common_service import (
	_calculate_return_pct,
	_coerce_utc_datetime,
	_current_hour_bucket,
	_current_second_bucket,
	_is_current_second,
	_normalize_currency,
)
from app.services.market_data import Quote, QuoteLookupError
from app.services.sql_expression import sql_expr

REALTIME_SERIES_RETENTION = timedelta(hours=1, minutes=5)
REALTIME_SAMPLER_INTERVAL_SECONDS = 1.0
REALTIME_SAMPLER_LOCK_NAME = "realtime-analytics-sampler"
REALTIME_MARKET_DATA_CONCURRENCY = 16
GroupedItem = TypeVar("GroupedItem")


@dataclass(slots=True)
class UserRealtimeAnalyticsState:
	user_id: str
	total_value_cny: Decimal
	has_assets: bool
	aggregate_return_pct: Decimal | None
	holding_points: tuple[LiveHoldingReturnPoint, ...]


@dataclass(slots=True)
class RealtimeMarketDataPrefetch:
	quotes_by_pair: dict[tuple[str, str], Quote]
	fx_rates: dict[str, Decimal]
	unique_symbol_count: int
	quote_failure_count: int
	fx_failure_count: int


@dataclass(slots=True)
class RealtimeSampleStats:
	user_count: int = 0
	unique_symbol_count: int = 0
	quote_failure_count: int = 0
	fx_failure_count: int = 0
	deleted_portfolio_snapshot_count: int = 0
	deleted_return_snapshot_count: int = 0


def _group_by_user_id(
	items: Iterable[GroupedItem],
	get_user_id: Callable[[GroupedItem], str],
) -> dict[str, list[GroupedItem]]:
	grouped: dict[str, list[GroupedItem]] = defaultdict(list)
	for item in items:
		grouped[get_user_id(item)].append(item)
	return grouped


def _load_assets_for_all_users(
	session: Session,
) -> tuple[
	list[str],
	dict[str, list[CashAccount]],
	dict[str, list[SecurityHolding]],
	dict[str, list[FixedAsset]],
	dict[str, list[LiabilityEntry]],
	dict[str, list[OtherAsset]],
]:
	all_user_ids = set(session.exec(select(UserAccount.username)))
	accounts = list(
		session.exec(
			select(CashAccount)
			.order_by(sql_expr(CashAccount.user_id).asc(), sql_expr(CashAccount.id).asc()),
		),
	)
	holdings = list(
		session.exec(
			select(SecurityHolding)
			.order_by(
				sql_expr(SecurityHolding.user_id).asc(),
				sql_expr(SecurityHolding.symbol).asc(),
				sql_expr(SecurityHolding.id).asc(),
			),
		),
	)
	fixed_assets = list(
		session.exec(
			select(FixedAsset)
			.order_by(sql_expr(FixedAsset.user_id).asc(), sql_expr(FixedAsset.id).asc()),
		),
	)
	liabilities = list(
		session.exec(
			select(LiabilityEntry)
			.order_by(sql_expr(LiabilityEntry.user_id).asc(), sql_expr(LiabilityEntry.id).asc()),
		),
	)
	other_assets = list(
		session.exec(
			select(OtherAsset)
			.order_by(sql_expr(OtherAsset.user_id).asc(), sql_expr(OtherAsset.id).asc()),
		),
	)
	accounts_by_user = _group_by_user_id(accounts, lambda item: item.user_id)
	holdings_by_user = _group_by_user_id(holdings, lambda item: item.user_id)
	fixed_assets_by_user = _group_by_user_id(fixed_assets, lambda item: item.user_id)
	liabilities_by_user = _group_by_user_id(liabilities, lambda item: item.user_id)
	other_assets_by_user = _group_by_user_id(other_assets, lambda item: item.user_id)
	user_ids = sorted({
		*all_user_ids,
		*accounts_by_user.keys(),
		*holdings_by_user.keys(),
		*fixed_assets_by_user.keys(),
		*liabilities_by_user.keys(),
		*other_assets_by_user.keys(),
	})
	return (
		user_ids,
		accounts_by_user,
		holdings_by_user,
		fixed_assets_by_user,
		liabilities_by_user,
		other_assets_by_user,
	)


async def _prefetch_quotes(
	holdings: list[SecurityHolding],
) -> tuple[dict[tuple[str, str], Quote], int, int]:
	unique_pairs = {
		(holding.symbol, holding.market)
		for holding in holdings
	}
	if not unique_pairs:
		return {}, 0, 0
	semaphore = asyncio.Semaphore(REALTIME_MARKET_DATA_CONCURRENCY)

	async def load_quote(symbol: str, market: str) -> tuple[tuple[str, str], Quote | None]:
		async with semaphore:
			try:
				quote, _warnings = await service_context.market_data_client.fetch_quote(symbol, market)
			except QuoteLookupError as exc:
				service_context.logger.warning(
					"Realtime analytics sampler could not load quote for %s (%s): %s",
					symbol,
					market,
					exc,
				)
				return (symbol, market), None
			return (symbol, market), quote

	quote_results = await asyncio.gather(
		*(load_quote(symbol, market) for symbol, market in sorted(unique_pairs)),
	)
	quotes_by_pair = {
		pair: quote
		for pair, quote in quote_results
		if quote is not None
	}
	return quotes_by_pair, len(unique_pairs), len(unique_pairs) - len(quotes_by_pair)


async def _prefetch_fx_rates(
	currencies: Iterable[str],
) -> tuple[dict[str, Decimal], int]:
	normalized_codes = {
		_normalize_currency(currency)
		for currency in currencies
		if str(currency).strip()
	}
	rates: dict[str, Decimal] = {"CNY": Decimal("1")}
	requested_codes = sorted(code for code in normalized_codes if code != "CNY")
	if not requested_codes:
		return rates, 0
	semaphore = asyncio.Semaphore(REALTIME_MARKET_DATA_CONCURRENCY)

	async def load_rate(currency_code: str) -> tuple[str, Decimal | None]:
		async with semaphore:
			try:
				rate, _warnings = await service_context.market_data_client.fetch_fx_rate(
					currency_code,
					"CNY",
				)
			except (QuoteLookupError, ValueError) as exc:
				service_context.logger.warning(
					"Realtime analytics sampler could not load FX rate for %s/CNY: %s",
					currency_code,
					exc,
				)
				return currency_code, None
			return currency_code, quantize_decimal(rate)

	for currency_code, rate in await asyncio.gather(
		*(load_rate(currency_code) for currency_code in requested_codes),
	):
		if rate is not None and rate > 0:
			rates[currency_code] = rate

	return rates, len(requested_codes) - (len(rates) - 1)


async def _prefetch_market_data_for_sample(
	*,
	accounts_by_user: dict[str, list[CashAccount]],
	holdings_by_user: dict[str, list[SecurityHolding]],
	liabilities_by_user: dict[str, list[LiabilityEntry]],
) -> RealtimeMarketDataPrefetch:
	all_holdings = [
		holding
		for holdings in holdings_by_user.values()
		for holding in holdings
	]
	quotes_by_pair, unique_symbol_count, quote_failure_count = await _prefetch_quotes(all_holdings)
	fx_rates, fx_failure_count = await _prefetch_fx_rates(
		[
			*(account.currency for accounts in accounts_by_user.values() for account in accounts),
			*(entry.currency for entries in liabilities_by_user.values() for entry in entries),
			*(quote.currency for quote in quotes_by_pair.values()),
		],
	)
	return RealtimeMarketDataPrefetch(
		quotes_by_pair=quotes_by_pair,
		fx_rates=fx_rates,
		unique_symbol_count=unique_symbol_count,
		quote_failure_count=quote_failure_count,
		fx_failure_count=fx_failure_count,
	)


def _build_user_realtime_state(
	*,
	user_id: str,
	accounts: list[CashAccount],
	holdings: list[SecurityHolding],
	fixed_assets: list[FixedAsset],
	liabilities: list[LiabilityEntry],
	other_assets: list[OtherAsset],
	quotes_by_pair: dict[tuple[str, str], Quote],
	fx_rates: dict[str, Decimal],
) -> UserRealtimeAnalyticsState:
	has_assets = bool(accounts or holdings or fixed_assets or liabilities or other_assets)
	cash_total = sum(
		(
			display_money(
				multiply_decimals(
					account.balance,
					fx_rates.get(_normalize_currency(account.currency), DECIMAL_ZERO),
				),
			)
			for account in accounts
		),
		DECIMAL_ZERO,
	)
	fixed_asset_total = sum((display_money(asset.current_value_cny) for asset in fixed_assets), DECIMAL_ZERO)
	other_asset_total = sum((display_money(asset.current_value_cny) for asset in other_assets), DECIMAL_ZERO)
	liability_total = sum(
		(
			display_money(
				multiply_decimals(
					entry.balance,
					fx_rates.get(_normalize_currency(entry.currency), DECIMAL_ZERO),
				),
			)
			for entry in liabilities
		),
		DECIMAL_ZERO,
	)

	holdings_total = DECIMAL_ZERO
	total_cost_basis_cny = DECIMAL_ZERO
	total_market_value_cny = DECIMAL_ZERO
	holding_points: list[LiveHoldingReturnPoint] = []

	for holding in holdings:
		quote = quotes_by_pair.get((holding.symbol, holding.market))
		quote_price = quantize_decimal(quote.price) if quote is not None else DECIMAL_ZERO
		if quote is None or quote_price <= 0 or holding.quantity <= 0:
			continue

		currency_code = _normalize_currency(quote.currency or holding.fallback_currency)
		fx_to_cny = fx_rates.get(currency_code, DECIMAL_ZERO)
		if fx_to_cny <= 0:
			continue

		market_value_cny = display_money(
			multiply_decimals(multiply_decimals(holding.quantity, quote_price), fx_to_cny),
		)
		holdings_total += market_value_cny
		return_pct = _calculate_return_pct(quote_price, holding.cost_basis_price)
		if (
			return_pct is None
			or holding.cost_basis_price is None
			or holding.cost_basis_price <= 0
		):
			continue

		total_cost_basis_cny += quantize_decimal(holding.cost_basis_price * holding.quantity * fx_to_cny)
		total_market_value_cny += market_value_cny
		holding_points.append(
			LiveHoldingReturnPoint(
				symbol=holding.symbol,
				name=holding.name,
				return_pct=display_percent(return_pct),
			),
		)

	aggregate_return_pct: Decimal | None = None
	if total_cost_basis_cny > 0:
		aggregate_return_pct = display_percent(
			((total_market_value_cny - total_cost_basis_cny) / total_cost_basis_cny) * 100,
		)

	return UserRealtimeAnalyticsState(
		user_id=user_id,
		total_value_cny=display_money(
			cash_total + holdings_total + fixed_asset_total + other_asset_total - liability_total,
		),
		has_assets=has_assets,
		aggregate_return_pct=aggregate_return_pct,
		holding_points=tuple(holding_points),
	)


def _upsert_hourly_snapshots(
	session: Session,
	*,
	user_states: list[UserRealtimeAnalyticsState],
	hour_bucket: datetime,
) -> None:
	user_ids = [state.user_id for state in user_states]
	if not user_ids:
		return

	existing_portfolio_snapshots = {
		snapshot.user_id: snapshot
			for snapshot in session.exec(
				select(PortfolioSnapshot)
				.where(sql_expr(PortfolioSnapshot.user_id).in_(user_ids))
				.where(PortfolioSnapshot.created_at == hour_bucket),
			)
	}
	existing_return_snapshots = defaultdict(list)
	for snapshot in session.exec(
		select(HoldingPerformanceSnapshot)
		.where(sql_expr(HoldingPerformanceSnapshot.user_id).in_(user_ids))
		.where(HoldingPerformanceSnapshot.created_at == hour_bucket),
	):
		existing_return_snapshots[snapshot.user_id].append(snapshot)

	for state in user_states:
		portfolio_snapshot = existing_portfolio_snapshots.get(state.user_id)
		if state.has_assets:
			if portfolio_snapshot is None:
				session.add(
					PortfolioSnapshot(
						user_id=state.user_id,
						total_value_cny=state.total_value_cny,
						created_at=hour_bucket,
					),
				)
			else:
				portfolio_snapshot.total_value_cny = state.total_value_cny
				portfolio_snapshot.created_at = hour_bucket
				session.add(portfolio_snapshot)
		elif portfolio_snapshot is not None:
			session.delete(portfolio_snapshot)

		indexed_existing = {
			(snapshot.scope, snapshot.symbol or ""): snapshot
			for snapshot in existing_return_snapshots.get(state.user_id, [])
		}
		expected_keys: set[tuple[str, str]] = set()
		if state.aggregate_return_pct is not None:
			expected_keys.add(("TOTAL", ""))
			total_snapshot = indexed_existing.get(("TOTAL", ""))
			if total_snapshot is None:
				session.add(
					HoldingPerformanceSnapshot(
						user_id=state.user_id,
						scope="TOTAL",
						symbol=None,
						name="非现金资产",
						return_pct=state.aggregate_return_pct,
						created_at=hour_bucket,
					),
				)
			else:
				total_snapshot.name = "非现金资产"
				total_snapshot.return_pct = state.aggregate_return_pct
				total_snapshot.created_at = hour_bucket
				session.add(total_snapshot)

		for point in state.holding_points:
			key = ("HOLDING", point.symbol)
			expected_keys.add(key)
			holding_snapshot = indexed_existing.get(key)
			if holding_snapshot is None:
				session.add(
					HoldingPerformanceSnapshot(
						user_id=state.user_id,
						scope="HOLDING",
						symbol=point.symbol,
						name=point.name,
						return_pct=point.return_pct,
						created_at=hour_bucket,
					),
				)
			else:
				holding_snapshot.name = point.name
				holding_snapshot.return_pct = point.return_pct
				holding_snapshot.created_at = hour_bucket
				session.add(holding_snapshot)

		for key, snapshot in indexed_existing.items():
			if key not in expected_keys:
				session.delete(snapshot)


def _upsert_realtime_snapshots(
	session: Session,
	*,
	user_states: list[UserRealtimeAnalyticsState],
	second_bucket: datetime,
) -> None:
	user_ids = [state.user_id for state in user_states]
	if not user_ids:
		return

	existing_portfolio_snapshots = {
		snapshot.user_id: snapshot
			for snapshot in session.exec(
				select(RealtimePortfolioSnapshot)
				.where(sql_expr(RealtimePortfolioSnapshot.user_id).in_(user_ids))
				.where(RealtimePortfolioSnapshot.created_at == second_bucket),
			)
	}
	existing_return_snapshots = defaultdict(list)
	for snapshot in session.exec(
		select(RealtimeHoldingPerformanceSnapshot)
		.where(sql_expr(RealtimeHoldingPerformanceSnapshot.user_id).in_(user_ids))
		.where(RealtimeHoldingPerformanceSnapshot.created_at == second_bucket),
	):
		existing_return_snapshots[snapshot.user_id].append(snapshot)

	for state in user_states:
		portfolio_snapshot = existing_portfolio_snapshots.get(state.user_id)
		if state.has_assets:
			if portfolio_snapshot is None:
				session.add(
					RealtimePortfolioSnapshot(
						user_id=state.user_id,
						total_value_cny=state.total_value_cny,
						created_at=second_bucket,
					),
				)
			else:
				portfolio_snapshot.total_value_cny = state.total_value_cny
				portfolio_snapshot.created_at = second_bucket
				session.add(portfolio_snapshot)
		elif portfolio_snapshot is not None:
			session.delete(portfolio_snapshot)

		indexed_existing = {
			(snapshot.scope, snapshot.symbol or ""): snapshot
			for snapshot in existing_return_snapshots.get(state.user_id, [])
		}
		expected_keys: set[tuple[str, str]] = set()
		if state.aggregate_return_pct is not None:
			expected_keys.add(("TOTAL", ""))
			total_snapshot = indexed_existing.get(("TOTAL", ""))
			if total_snapshot is None:
				session.add(
					RealtimeHoldingPerformanceSnapshot(
						user_id=state.user_id,
						scope="TOTAL",
						symbol=None,
						name="非现金资产",
						return_pct=state.aggregate_return_pct,
						created_at=second_bucket,
					),
				)
			else:
				total_snapshot.name = "非现金资产"
				total_snapshot.return_pct = state.aggregate_return_pct
				total_snapshot.created_at = second_bucket
				session.add(total_snapshot)

		for point in state.holding_points:
			key = ("HOLDING", point.symbol)
			expected_keys.add(key)
			holding_snapshot = indexed_existing.get(key)
			if holding_snapshot is None:
				session.add(
					RealtimeHoldingPerformanceSnapshot(
						user_id=state.user_id,
						scope="HOLDING",
						symbol=point.symbol,
						name=point.name,
						return_pct=point.return_pct,
						created_at=second_bucket,
					),
				)
			else:
				holding_snapshot.name = point.name
				holding_snapshot.return_pct = point.return_pct
				holding_snapshot.created_at = second_bucket
				session.add(holding_snapshot)

		for key, snapshot in indexed_existing.items():
			if key not in expected_keys:
				session.delete(snapshot)


def _purge_expired_realtime_snapshots(
	session: Session,
	*,
	now: datetime,
) -> tuple[int, int]:
	cutoff = _coerce_utc_datetime(now) - REALTIME_SERIES_RETENTION
	portfolio_result = session.exec(
		delete(RealtimePortfolioSnapshot).where(sql_expr(RealtimePortfolioSnapshot.created_at) < cutoff),
	)
	return_result = session.exec(
		delete(RealtimeHoldingPerformanceSnapshot).where(
			sql_expr(RealtimeHoldingPerformanceSnapshot.created_at) < cutoff,
		),
	)
	return int(portfolio_result.rowcount or 0), int(return_result.rowcount or 0)


async def _sample_realtime_analytics_once_with_session(
	session: Session,
	*,
	sampled_at: datetime,
) -> RealtimeSampleStats:
	started_at = monotonic()
	second_bucket = _current_second_bucket(sampled_at)
	hour_bucket = _current_hour_bucket(sampled_at)
	(
		user_ids,
		accounts_by_user,
		holdings_by_user,
		fixed_assets_by_user,
		liabilities_by_user,
		other_assets_by_user,
	) = _load_assets_for_all_users(session)
	stats = RealtimeSampleStats(user_count=len(user_ids))
	if not user_ids:
		if second_bucket.second == 0:
			(
				stats.deleted_portfolio_snapshot_count,
				stats.deleted_return_snapshot_count,
			) = _purge_expired_realtime_snapshots(session, now=sampled_at)
			session.commit()
		service_context.logger.info(
			"Realtime analytics sample completed in %.3fs "
			"(users=%s unique_symbols=%s quote_failures=%s fx_failures=%s "
			"deleted_portfolio=%s deleted_returns=%s).",
			monotonic() - started_at,
			stats.user_count,
			stats.unique_symbol_count,
			stats.quote_failure_count,
			stats.fx_failure_count,
			stats.deleted_portfolio_snapshot_count,
			stats.deleted_return_snapshot_count,
		)
		return stats

	market_data = await _prefetch_market_data_for_sample(
		accounts_by_user=accounts_by_user,
		holdings_by_user=holdings_by_user,
		liabilities_by_user=liabilities_by_user,
	)
	stats.unique_symbol_count = market_data.unique_symbol_count
	stats.quote_failure_count = market_data.quote_failure_count
	stats.fx_failure_count = market_data.fx_failure_count

	user_states = [
		_build_user_realtime_state(
			user_id=user_id,
			accounts=accounts_by_user.get(user_id, []),
			holdings=holdings_by_user.get(user_id, []),
			fixed_assets=fixed_assets_by_user.get(user_id, []),
			liabilities=liabilities_by_user.get(user_id, []),
			other_assets=other_assets_by_user.get(user_id, []),
			quotes_by_pair=market_data.quotes_by_pair,
			fx_rates=market_data.fx_rates,
		)
		for user_id in user_ids
	]

	_upsert_hourly_snapshots(
		session,
		user_states=user_states,
		hour_bucket=hour_bucket,
	)
	_upsert_realtime_snapshots(
		session,
		user_states=user_states,
		second_bucket=second_bucket,
	)
	if second_bucket.second == 0:
		(
			stats.deleted_portfolio_snapshot_count,
			stats.deleted_return_snapshot_count,
		) = _purge_expired_realtime_snapshots(session, now=sampled_at)

	session.commit()
	service_context.logger.info(
		"Realtime analytics sample completed in %.3fs "
		"(users=%s unique_symbols=%s quote_failures=%s fx_failures=%s "
		"deleted_portfolio=%s deleted_returns=%s).",
		monotonic() - started_at,
		stats.user_count,
		stats.unique_symbol_count,
		stats.quote_failure_count,
		stats.fx_failure_count,
		stats.deleted_portfolio_snapshot_count,
		stats.deleted_return_snapshot_count,
	)
	return stats


async def sample_realtime_analytics_once(
	now: datetime | None = None,
	*,
	session: Session | None = None,
) -> None:
	sampled_at = _coerce_utc_datetime(now or utc_now())
	if session is not None:
		await _sample_realtime_analytics_once_with_session(
			session,
			sampled_at=sampled_at,
		)
	else:
		with Session(engine) as owned_session:
			await _sample_realtime_analytics_once_with_session(
				owned_session,
				sampled_at=sampled_at,
			)


async def realtime_analytics_sampler() -> None:
	while True:
		started_at = monotonic()
		try:
			now = utc_now()
			if _is_current_second(runtime_state.get_last_realtime_analytics_sampled_at(), now):
				await asyncio.sleep(0.05)
				continue

			async with runtime_state.async_redis_lock(
				REALTIME_SAMPLER_LOCK_NAME,
				timeout=REALTIME_SAMPLER_INTERVAL_SECONDS,
				blocking_timeout=0.01,
			):
				now = utc_now()
				if _is_current_second(runtime_state.get_last_realtime_analytics_sampled_at(), now):
					continue

				await sample_realtime_analytics_once(now)
				runtime_state.set_last_realtime_analytics_sampled_at(
					_current_second_bucket(now),
				)
		except RuntimeError:
			pass
		except asyncio.CancelledError:
			raise
		except Exception:
			service_context.logger.exception("Realtime analytics sampler failed.")

		elapsed = monotonic() - started_at
		await asyncio.sleep(max(0.05, REALTIME_SAMPLER_INTERVAL_SECONDS - elapsed))


def start_realtime_analytics_sampler() -> asyncio.Task[None]:
	if (
		runtime_state.realtime_analytics_sampler_task is None
		or runtime_state.realtime_analytics_sampler_task.done()
	):
		runtime_state.realtime_analytics_sampler_task = asyncio.create_task(
			realtime_analytics_sampler(),
		)
	return runtime_state.realtime_analytics_sampler_task


async def stop_realtime_analytics_sampler() -> None:
	if runtime_state.realtime_analytics_sampler_task is None:
		return
	runtime_state.realtime_analytics_sampler_task.cancel()
	try:
		await runtime_state.realtime_analytics_sampler_task
	except asyncio.CancelledError:
		pass
	runtime_state.realtime_analytics_sampler_task = None
