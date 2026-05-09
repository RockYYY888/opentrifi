from __future__ import annotations
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from fastapi import HTTPException, Query
from fastapi.responses import Response
from sqlmodel import Session, select
from app import runtime_state
from app.analytics import bucket_start_utc, build_return_timeline, build_timeline
from app.models import (
    CashAccount,
    DashboardCorrection,
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
from app.runtime_state import (
	DashboardCacheEntry,
	LiveHoldingReturnPoint,
	LiveHoldingsReturnState,
	LivePortfolioState,
)
from app.schemas import (
	AllocationSlice,
	DashboardCorrectionCreate,
	DashboardCorrectionRead,
	DashboardResponse,
	HoldingReturnSeries,
	ValuedHolding,
)
from app.services.auth_service import CurrentUserDependency
from app.services.common_service import (
	DASHBOARD_CORRECTION_ACTIONS,
	DASHBOARD_CORRECTION_GRANULARITIES,
	DASHBOARD_SERIES_SCOPES,
	_consume_global_force_refresh_slot,
	_coerce_utc_datetime,
    _filter_dashboard_warnings_for_user,
    _invalidate_dashboard_cache,
    _is_current_second,
    _server_today_date,
)
from app.fixed_precision import DECIMAL_ZERO, display_money
from app.services.history_sync_service import _has_holding_history_sync_pending
from app.services.dashboard_correction_service import (
	_apply_dashboard_corrections,
	_load_dashboard_correction_lookup,
)
from app.services.dashboard_live_service import (
	_build_transient_holdings_return_snapshots,
	_build_transient_portfolio_snapshot,
	_roll_live_holdings_return_state_if_needed,
	_roll_live_portfolio_state_if_needed,
	_summarize_holdings_return_state,
	_update_live_holdings_return_state,
	_update_live_portfolio_state,
)
from app.services.holding_projection_service import _to_holding_transaction_reads
from app.services.holding_transaction_service import _list_holding_transactions_for_user
from app.services.portfolio_read_service import (
    _load_display_fx_rates,
    _value_cash_accounts,
    _value_fixed_assets,
    _value_holdings,
    _value_liabilities,
    _value_other_assets,
)
from app.services import service_context
from app.services.service_context import SessionDependency
from app.services.sql_expression import sql_expr

async def _refresh_user_dashboards(
	session: Session,
	users: list[UserAccount],
	*,
	clear_market_data: bool = False,
) -> None:
	if clear_market_data:
		service_context.market_data_client.clear_runtime_caches()

	for user in users:
		await _get_cached_dashboard(session, user, force_refresh=True)

def _load_series(session: Session, user_id: str, since: datetime) -> list[PortfolioSnapshot]:
	return list(
		session.exec(
				select(PortfolioSnapshot)
				.where(PortfolioSnapshot.user_id == user_id)
				.where(PortfolioSnapshot.created_at >= since)
				.order_by(sql_expr(PortfolioSnapshot.created_at).asc()),
		),
	)

def _load_series_with_live_snapshot(
	session: Session,
	user_id: str,
	since: datetime,
	*,
	live_snapshot: PortfolioSnapshot | None = None,
) -> list[PortfolioSnapshot]:
	snapshots = _load_series(session, user_id, since)
	if live_snapshot is not None and live_snapshot.created_at >= _coerce_utc_datetime(since):
		snapshots.append(live_snapshot)
	return snapshots

def _load_realtime_portfolio_series(
	session: Session,
	user_id: str,
	since: datetime,
) -> list[PortfolioSnapshot]:
	return [
		PortfolioSnapshot(
			user_id=snapshot.user_id,
			total_value_cny=snapshot.total_value_cny,
			created_at=snapshot.created_at,
		)
		for snapshot in session.exec(
				select(RealtimePortfolioSnapshot)
				.where(RealtimePortfolioSnapshot.user_id == user_id)
				.where(RealtimePortfolioSnapshot.created_at >= since)
				.order_by(sql_expr(RealtimePortfolioSnapshot.created_at).asc()),
		)
	]

def _load_hybrid_portfolio_series_with_live_snapshot(
	session: Session,
	user_id: str,
	*,
	historical_since: datetime | None = None,
	realtime_since: datetime,
	live_snapshot: PortfolioSnapshot | None = None,
) -> list[PortfolioSnapshot]:
	snapshots: list[PortfolioSnapshot] = []
	if historical_since is not None:
		snapshots.extend(_load_series(session, user_id, historical_since))
	snapshots.extend(_load_realtime_portfolio_series(session, user_id, realtime_since))
	if live_snapshot is not None and live_snapshot.created_at >= _coerce_utc_datetime(realtime_since):
		snapshots.append(live_snapshot)
	return snapshots

def _load_holdings_return_series(
	session: Session,
	user_id: str,
	since: datetime,
	scope: str,
	symbol: str | None = None,
) -> list[HoldingPerformanceSnapshot]:
	statement = (
		select(HoldingPerformanceSnapshot)
		.where(HoldingPerformanceSnapshot.user_id == user_id)
			.where(HoldingPerformanceSnapshot.created_at >= since)
			.where(HoldingPerformanceSnapshot.scope == scope)
			.order_by(sql_expr(HoldingPerformanceSnapshot.created_at).asc())
		)
	if symbol is None:
		statement = statement.where(sql_expr(HoldingPerformanceSnapshot.symbol).is_(None))
	else:
		statement = statement.where(HoldingPerformanceSnapshot.symbol == symbol)

	return list(session.exec(statement))

def _load_holdings_return_series_with_live_snapshot(
	session: Session,
	user_id: str,
	since: datetime,
	scope: str,
	symbol: str | None = None,
	default_name: str | None = None,
	*,
	live_snapshots: dict[tuple[str, str | None], HoldingPerformanceSnapshot] | None = None,
) -> list[HoldingPerformanceSnapshot]:
	snapshots = _load_holdings_return_series(session, user_id, since, scope, symbol)
	if live_snapshots is None:
		return snapshots

	live_snapshot = live_snapshots.get((scope, symbol))
	if live_snapshot is not None and live_snapshot.created_at >= _coerce_utc_datetime(since):
		snapshots.append(live_snapshot)
	return snapshots

def _load_realtime_holdings_return_series(
	session: Session,
	user_id: str,
	since: datetime,
	scope: str,
	symbol: str | None = None,
) -> list[HoldingPerformanceSnapshot]:
	statement = (
		select(RealtimeHoldingPerformanceSnapshot)
		.where(RealtimeHoldingPerformanceSnapshot.user_id == user_id)
			.where(RealtimeHoldingPerformanceSnapshot.created_at >= since)
			.where(RealtimeHoldingPerformanceSnapshot.scope == scope)
			.order_by(sql_expr(RealtimeHoldingPerformanceSnapshot.created_at).asc())
		)
	if symbol is None:
		statement = statement.where(sql_expr(RealtimeHoldingPerformanceSnapshot.symbol).is_(None))
	else:
		statement = statement.where(RealtimeHoldingPerformanceSnapshot.symbol == symbol)

	return [
		HoldingPerformanceSnapshot(
			user_id=snapshot.user_id,
			scope=snapshot.scope,
			symbol=snapshot.symbol,
			name=snapshot.name,
			return_pct=snapshot.return_pct,
			created_at=snapshot.created_at,
		)
		for snapshot in session.exec(statement)
	]

def _load_hybrid_holdings_return_series_with_live_snapshot(
	session: Session,
	user_id: str,
	*,
	realtime_since: datetime,
	scope: str,
	symbol: str | None = None,
	default_name: str | None = None,
	historical_since: datetime | None = None,
	live_snapshots: dict[tuple[str, str | None], HoldingPerformanceSnapshot] | None = None,
) -> list[HoldingPerformanceSnapshot]:
	snapshots: list[HoldingPerformanceSnapshot] = []
	if historical_since is not None:
		snapshots.extend(
			_load_holdings_return_series(
				session,
				user_id,
				historical_since,
				scope,
				symbol=symbol,
			),
		)
	snapshots.extend(
		_load_realtime_holdings_return_series(
			session,
			user_id,
			realtime_since,
			scope,
			symbol=symbol,
		),
	)
	if live_snapshots is None:
		return snapshots

	live_snapshot = live_snapshots.get((scope, symbol))
	if live_snapshot is not None and live_snapshot.created_at >= _coerce_utc_datetime(realtime_since):
		snapshots.append(live_snapshot)
	return snapshots

async def _build_dashboard(session: Session, user: UserAccount) -> DashboardResponse:
	user_id = user.username
	now = utc_now()
	fx_rate_overrides, usd_cny_rate, hkd_cny_rate, fx_display_warnings = await _load_display_fx_rates(
		prefer_stale_market_data=True,
	)

	accounts = list(
		session.exec(
			select(CashAccount)
			.where(CashAccount.user_id == user_id)
			.order_by(CashAccount.platform, CashAccount.name),
		),
	)
	holdings = list(
		session.exec(
			select(SecurityHolding)
			.where(SecurityHolding.user_id == user_id)
			.order_by(SecurityHolding.symbol, SecurityHolding.name),
		),
	)
	fixed_assets = list(
		session.exec(
			select(FixedAsset)
			.where(FixedAsset.user_id == user_id)
			.order_by(FixedAsset.category, FixedAsset.name),
		),
	)
	liabilities = list(
		session.exec(
			select(LiabilityEntry)
			.where(LiabilityEntry.user_id == user_id)
			.order_by(LiabilityEntry.category, LiabilityEntry.name),
		),
	)
	other_assets = list(
		session.exec(
			select(OtherAsset)
			.where(OtherAsset.user_id == user_id)
			.order_by(OtherAsset.category, OtherAsset.name),
		),
	)
	history_sync_pending = _has_holding_history_sync_pending(session, user_id)

	valued_accounts, cash_value_cny, account_warnings = await _value_cash_accounts(
		accounts,
		fx_rate_overrides,
		prefer_stale_market_data=True,
	)
	valued_holdings, holdings_value_cny, holding_warnings = await _value_holdings(
		holdings,
		fx_rate_overrides,
		force_pending=history_sync_pending,
		prefer_stale_market_data=True,
	)
	valued_fixed_assets, fixed_assets_value_cny = _value_fixed_assets(fixed_assets)
	valued_liabilities, liabilities_value_cny, liability_warnings = await _value_liabilities(
		liabilities,
		fx_rate_overrides,
		prefer_stale_market_data=True,
	)
	valued_other_assets, other_assets_value_cny = _value_other_assets(other_assets)
	total_value_cny = display_money(
		cash_value_cny
		+ holdings_value_cny
		+ fixed_assets_value_cny
		+ other_assets_value_cny
		- liabilities_value_cny,
	)
	has_assets = bool(accounts or holdings or fixed_assets or liabilities or other_assets)
	aggregate_holdings_return_pct, holding_return_points = _summarize_holdings_return_state(
		valued_holdings,
	)
	_roll_live_portfolio_state_if_needed(session, user_id, now)
	_roll_live_holdings_return_state_if_needed(session, user_id, now)
	_update_live_portfolio_state(user_id, now, total_value_cny, has_assets)
	_update_live_holdings_return_state(
		user_id,
		now,
		aggregate_holdings_return_pct,
		holding_return_points,
	)
	live_portfolio_snapshot = _build_transient_portfolio_snapshot(
		user_id=user_id,
		generated_at=now,
		total_value_cny=total_value_cny,
		has_assets=has_assets,
	)
	live_holdings_return_snapshots = _build_transient_holdings_return_snapshots(
		user_id=user_id,
		generated_at=now,
		aggregate_return_pct=aggregate_holdings_return_pct,
		holding_points=holding_return_points,
	)
	correction_lookup = _load_dashboard_correction_lookup(session, user_id)

	second_series_raw = build_timeline(
		_load_hybrid_portfolio_series_with_live_snapshot(
			session,
			user_id,
			realtime_since=now - timedelta(seconds=61),
			live_snapshot=live_portfolio_snapshot,
		),
		"second",
	)
	minute_series_raw = build_timeline(
		_load_hybrid_portfolio_series_with_live_snapshot(
			session,
			user_id,
			historical_since=_coerce_utc_datetime(now - timedelta(hours=1)).replace(
				minute=0,
				second=0,
				microsecond=0,
			),
			realtime_since=now - timedelta(minutes=61),
			live_snapshot=live_portfolio_snapshot,
		),
		"minute",
	)
	hour_series_raw = build_timeline(
		_load_series_with_live_snapshot(
			session,
			user_id,
			now - timedelta(hours=24),
			live_snapshot=live_portfolio_snapshot,
		),
		"hour",
	)
	day_series_raw = build_timeline(
		_load_series_with_live_snapshot(
			session,
			user_id,
			now - timedelta(days=30),
			live_snapshot=live_portfolio_snapshot,
		),
		"day",
	)
	month_series_raw = build_timeline(
		_load_series_with_live_snapshot(
			session,
			user_id,
			now - timedelta(days=366),
			live_snapshot=live_portfolio_snapshot,
		),
		"month",
	)
	year_series_raw = build_timeline(
		_load_series_with_live_snapshot(
			session,
			user_id,
			now - timedelta(days=366 * 5),
			live_snapshot=live_portfolio_snapshot,
		),
		"year",
	)
	hour_series = _apply_dashboard_corrections(
		hour_series_raw,
		correction_lookup,
		series_scope="PORTFOLIO_TOTAL",
		granularity="hour",
	)
	day_series = _apply_dashboard_corrections(
		day_series_raw,
		correction_lookup,
		series_scope="PORTFOLIO_TOTAL",
		granularity="day",
	)
	month_series = _apply_dashboard_corrections(
		month_series_raw,
		correction_lookup,
		series_scope="PORTFOLIO_TOTAL",
		granularity="month",
	)
	year_series = _apply_dashboard_corrections(
		year_series_raw,
		correction_lookup,
		series_scope="PORTFOLIO_TOTAL",
		granularity="year",
	)

	holdings_return_second_series_raw = build_return_timeline(
		_load_hybrid_holdings_return_series_with_live_snapshot(
			session,
			user_id,
			realtime_since=now - timedelta(seconds=61),
			scope="TOTAL",
			default_name="非现金资产",
			live_snapshots=live_holdings_return_snapshots,
		),
		"second",
	)
	holdings_return_minute_series_raw = build_return_timeline(
		_load_hybrid_holdings_return_series_with_live_snapshot(
			session,
			user_id,
			historical_since=_coerce_utc_datetime(now - timedelta(hours=1)).replace(
				minute=0,
				second=0,
				microsecond=0,
			),
			realtime_since=now - timedelta(minutes=61),
			scope="TOTAL",
			default_name="非现金资产",
			live_snapshots=live_holdings_return_snapshots,
		),
		"minute",
	)
	holdings_return_hour_series_raw = build_return_timeline(
		_load_holdings_return_series_with_live_snapshot(
			session,
			user_id,
			now - timedelta(hours=24),
			"TOTAL",
			default_name="非现金资产",
			live_snapshots=live_holdings_return_snapshots,
		),
		"hour",
	)
	holdings_return_day_series_raw = build_return_timeline(
		_load_holdings_return_series_with_live_snapshot(
			session,
			user_id,
			now - timedelta(days=30),
			"TOTAL",
			default_name="非现金资产",
			live_snapshots=live_holdings_return_snapshots,
		),
		"day",
	)
	holdings_return_month_series_raw = build_return_timeline(
		_load_holdings_return_series_with_live_snapshot(
			session,
			user_id,
			now - timedelta(days=366),
			"TOTAL",
			default_name="非现金资产",
			live_snapshots=live_holdings_return_snapshots,
		),
		"month",
	)
	holdings_return_year_series_raw = build_return_timeline(
		_load_holdings_return_series_with_live_snapshot(
			session,
			user_id,
			now - timedelta(days=366 * 5),
			"TOTAL",
			default_name="非现金资产",
			live_snapshots=live_holdings_return_snapshots,
		),
		"year",
	)
	holdings_return_hour_series = _apply_dashboard_corrections(
		holdings_return_hour_series_raw,
		correction_lookup,
		series_scope="HOLDINGS_RETURN_TOTAL",
		granularity="hour",
	)
	holdings_return_day_series = _apply_dashboard_corrections(
		holdings_return_day_series_raw,
		correction_lookup,
		series_scope="HOLDINGS_RETURN_TOTAL",
		granularity="day",
	)
	holdings_return_month_series = _apply_dashboard_corrections(
		holdings_return_month_series_raw,
		correction_lookup,
		series_scope="HOLDINGS_RETURN_TOTAL",
		granularity="month",
	)
	holdings_return_year_series = _apply_dashboard_corrections(
		holdings_return_year_series_raw,
		correction_lookup,
		series_scope="HOLDINGS_RETURN_TOTAL",
		granularity="year",
	)
	recent_holding_transactions = _to_holding_transaction_reads(
		session,
		user_id=user.username,
		transactions=_list_holding_transactions_for_user(
			session,
			user_id=user.username,
			limit=5000,
		),
	)
	holding_return_series = []
	for holding in valued_holdings:
		if holding.cost_basis_price is None:
			continue

		holding_hour_series_raw = build_return_timeline(
			_load_holdings_return_series_with_live_snapshot(
				session,
				user_id,
				now - timedelta(hours=24),
				"HOLDING",
				symbol=holding.symbol,
				default_name=holding.name,
				live_snapshots=live_holdings_return_snapshots,
			),
			"hour",
		)
		holding_day_series_raw = build_return_timeline(
			_load_holdings_return_series_with_live_snapshot(
				session,
				user_id,
				now - timedelta(days=30),
				"HOLDING",
				symbol=holding.symbol,
				default_name=holding.name,
				live_snapshots=live_holdings_return_snapshots,
			),
			"day",
		)
		holding_month_series_raw = build_return_timeline(
			_load_holdings_return_series_with_live_snapshot(
				session,
				user_id,
				now - timedelta(days=366),
				"HOLDING",
				symbol=holding.symbol,
				default_name=holding.name,
				live_snapshots=live_holdings_return_snapshots,
			),
			"month",
		)
		holding_year_series_raw = build_return_timeline(
			_load_holdings_return_series_with_live_snapshot(
				session,
				user_id,
				now - timedelta(days=366 * 5),
				"HOLDING",
				symbol=holding.symbol,
				default_name=holding.name,
				live_snapshots=live_holdings_return_snapshots,
			),
			"year",
		)

		holding_return_series.append(
			HoldingReturnSeries(
				symbol=holding.symbol,
				name=holding.name,
				quantity=holding.quantity,
				second_series=build_return_timeline(
					_load_hybrid_holdings_return_series_with_live_snapshot(
						session,
						user_id,
						realtime_since=now - timedelta(seconds=61),
						scope="HOLDING",
						symbol=holding.symbol,
						default_name=holding.name,
						live_snapshots=live_holdings_return_snapshots,
					),
					"second",
				),
				minute_series=build_return_timeline(
					_load_hybrid_holdings_return_series_with_live_snapshot(
						session,
						user_id,
						historical_since=_coerce_utc_datetime(now - timedelta(hours=1)).replace(
							minute=0,
							second=0,
							microsecond=0,
						),
						realtime_since=now - timedelta(minutes=61),
						scope="HOLDING",
						symbol=holding.symbol,
						default_name=holding.name,
						live_snapshots=live_holdings_return_snapshots,
					),
					"minute",
				),
				hour_series=_apply_dashboard_corrections(
					holding_hour_series_raw,
					correction_lookup,
					series_scope="HOLDING_RETURN",
					granularity="hour",
					symbol=holding.symbol,
				),
				day_series=_apply_dashboard_corrections(
					holding_day_series_raw,
					correction_lookup,
					series_scope="HOLDING_RETURN",
					granularity="day",
					symbol=holding.symbol,
				),
				month_series=_apply_dashboard_corrections(
					holding_month_series_raw,
					correction_lookup,
					series_scope="HOLDING_RETURN",
					granularity="month",
					symbol=holding.symbol,
				),
				year_series=_apply_dashboard_corrections(
					holding_year_series_raw,
					correction_lookup,
					series_scope="HOLDING_RETURN",
					granularity="year",
					symbol=holding.symbol,
				),
			),
		)

	dashboard_warnings = [
		*(
			["持仓历史更新中，曲线会在回填完成后自动同步。"]
			if history_sync_pending
			else []
		),
		*fx_display_warnings,
		*account_warnings,
		*holding_warnings,
		*liability_warnings,
	]

	return DashboardResponse(
		server_today=_server_today_date(now),
		total_value_cny=total_value_cny,
		cash_value_cny=cash_value_cny,
		holdings_value_cny=holdings_value_cny,
		fixed_assets_value_cny=fixed_assets_value_cny,
		liabilities_value_cny=liabilities_value_cny,
		other_assets_value_cny=other_assets_value_cny,
		usd_cny_rate=usd_cny_rate,
		hkd_cny_rate=hkd_cny_rate,
		cash_accounts=valued_accounts,
		holdings=valued_holdings,
		fixed_assets=valued_fixed_assets,
		liabilities=valued_liabilities,
		other_assets=valued_other_assets,
		allocation=[
			AllocationSlice(label=label, value=value)
			for label, value in (
				("现金", cash_value_cny),
				("投资类", holdings_value_cny),
				("固定资产", fixed_assets_value_cny),
				("其他", other_assets_value_cny),
			)
			if value > 0
		],
		second_series=second_series_raw,
		minute_series=minute_series_raw,
		hour_series=hour_series,
		day_series=day_series,
		month_series=month_series,
		year_series=year_series,
		holdings_return_second_series=holdings_return_second_series_raw,
		holdings_return_minute_series=holdings_return_minute_series_raw,
		holdings_return_hour_series=holdings_return_hour_series,
		holdings_return_day_series=holdings_return_day_series,
		holdings_return_month_series=holdings_return_month_series,
		holdings_return_year_series=holdings_return_year_series,
		holding_return_series=holding_return_series,
		recent_holding_transactions=recent_holding_transactions,
		warnings=_filter_dashboard_warnings_for_user(dashboard_warnings, user),
	)

async def _get_cached_dashboard(
	session: Session,
	user: UserAccount,
	force_refresh: bool = False,
) -> DashboardResponse:
	cache_entry = runtime_state.dashboard_cache.get(user.username)

	if (
		not force_refresh
		and cache_entry is not None
		and _is_current_second(cache_entry.generated_at)
	):
		return cache_entry.dashboard

	async with runtime_state.async_redis_lock(
		f"dashboard-cache:{user.username}",
		timeout=15,
		blocking_timeout=15,
	):
		cache_entry = runtime_state.dashboard_cache.get(user.username)
		if (
			not force_refresh
			and cache_entry is not None
			and _is_current_second(cache_entry.generated_at)
		):
			return cache_entry.dashboard

		dashboard = await _build_dashboard(session, user)
		runtime_state.dashboard_cache[user.username] = DashboardCacheEntry(
			dashboard=dashboard,
			generated_at=utc_now(),
		)
		return dashboard

def healthcheck() -> dict[str, str]:
	return {"status": "ok"}

async def get_dashboard(
	current_user: CurrentUserDependency,
	session: SessionDependency,
	refresh: bool = False,
) -> DashboardResponse:
	if refresh:
		if await _consume_global_force_refresh_slot():
			from app.services import realtime_analytics_service

			service_context.market_data_client.clear_runtime_caches()
			await realtime_analytics_service.sample_realtime_analytics_once(
				utc_now(),
				session=session,
			)
		_invalidate_dashboard_cache(current_user.username)
		return await _get_cached_dashboard(
			session,
			current_user,
			force_refresh=True,
		)

	return await _get_cached_dashboard(session, current_user)
