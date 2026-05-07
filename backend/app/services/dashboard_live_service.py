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
from app.fixed_precision import DECIMAL_ZERO, display_percent, quantize_decimal
from app.services.auth_service import CurrentUserDependency
from app.services.common_service import (
	DASHBOARD_CORRECTION_ACTIONS,
	DASHBOARD_CORRECTION_GRANULARITIES,
	DASHBOARD_SERIES_SCOPES,
	_consume_global_force_refresh_slot,
	_coerce_utc_datetime,
    _current_hour_bucket,
    _filter_dashboard_warnings_for_user,
    _invalidate_dashboard_cache,
    _is_current_minute,
    _server_today_date,
)
from app.services.history_sync_service import _has_holding_history_sync_pending
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

def _summarize_holdings_return_state(
	holdings: list[ValuedHolding],
) -> tuple[Decimal | None, tuple[LiveHoldingReturnPoint, ...]]:
	total_cost_basis_cny = DECIMAL_ZERO
	total_market_value_cny = DECIMAL_ZERO
	points: list[LiveHoldingReturnPoint] = []

	for holding in holdings:
		if (
			holding.cost_basis_price is None
			or holding.cost_basis_price <= 0
			or holding.fx_to_cny <= 0
			or holding.quantity <= 0
			or holding.return_pct is None
		):
			continue

		cost_basis_value_cny = quantize_decimal(
			Decimal(str(holding.cost_basis_price))
			* Decimal(str(holding.quantity))
			* Decimal(str(holding.fx_to_cny)),
		)
		if cost_basis_value_cny <= 0:
			continue

		total_cost_basis_cny += cost_basis_value_cny
		total_market_value_cny += Decimal(str(holding.value_cny))
		points.append(
			LiveHoldingReturnPoint(
				symbol=holding.symbol,
				name=holding.name,
				return_pct=display_percent(holding.return_pct),
			),
		)

	if total_cost_basis_cny <= 0:
		return None, tuple(points)

	return (
		display_percent(((total_market_value_cny - total_cost_basis_cny) / total_cost_basis_cny) * 100),
		tuple(points),
	)

def _build_transient_portfolio_snapshot(
	*,
	user_id: str,
	generated_at: datetime,
	total_value_cny: Decimal,
	has_assets: bool,
) -> PortfolioSnapshot | None:
	if not has_assets:
		return None
	return PortfolioSnapshot(
		user_id=user_id,
		total_value_cny=total_value_cny,
		created_at=generated_at,
	)

def _build_transient_holdings_return_snapshots(
	*,
	user_id: str,
	generated_at: datetime,
	aggregate_return_pct: Decimal | None,
	holding_points: tuple[LiveHoldingReturnPoint, ...],
) -> dict[tuple[str, str | None], HoldingPerformanceSnapshot]:
	snapshots: dict[tuple[str, str | None], HoldingPerformanceSnapshot] = {}
	if aggregate_return_pct is not None:
		snapshots[("TOTAL", None)] = HoldingPerformanceSnapshot(
			user_id=user_id,
			scope="TOTAL",
			symbol=None,
			name="非现金资产",
			return_pct=aggregate_return_pct,
			created_at=generated_at,
		)
	for point in holding_points:
		snapshots[("HOLDING", point.symbol)] = HoldingPerformanceSnapshot(
			user_id=user_id,
			scope="HOLDING",
			symbol=point.symbol,
			name=point.name,
			return_pct=point.return_pct,
			created_at=generated_at,
		)
	return snapshots

def _persist_holdings_return_snapshot(
	session: Session,
	user_id: str,
	hour_bucket: datetime,
	aggregate_return_pct: Decimal | None,
	holding_points: tuple[LiveHoldingReturnPoint, ...],
) -> None:
	hour_start = _current_hour_bucket(hour_bucket)
	hour_end = hour_start + timedelta(hours=1)
	existing_snapshots = list(
		session.exec(
			select(HoldingPerformanceSnapshot)
			.where(HoldingPerformanceSnapshot.user_id == user_id)
			.where(HoldingPerformanceSnapshot.created_at >= hour_start)
			.where(HoldingPerformanceSnapshot.created_at < hour_end)
			.order_by(HoldingPerformanceSnapshot.created_at.desc()),
		),
	)
	indexed_snapshots = {
		(snapshot.scope, snapshot.symbol or ""): snapshot for snapshot in existing_snapshots
	}
	expected_keys: set[tuple[str, str]] = set()

	if aggregate_return_pct is not None:
		key = ("TOTAL", "")
		expected_keys.add(key)
		snapshot = indexed_snapshots.get(key)
		if snapshot is None:
			session.add(
				HoldingPerformanceSnapshot(
					user_id=user_id,
					scope="TOTAL",
					symbol=None,
					name="非现金资产",
					return_pct=aggregate_return_pct,
					created_at=hour_start,
				),
			)
		else:
			snapshot.name = "非现金资产"
			snapshot.return_pct = aggregate_return_pct
			snapshot.created_at = hour_start
			session.add(snapshot)

	for point in holding_points:
		key = ("HOLDING", point.symbol)
		expected_keys.add(key)
		snapshot = indexed_snapshots.get(key)
		if snapshot is None:
			session.add(
				HoldingPerformanceSnapshot(
					user_id=user_id,
					scope="HOLDING",
					symbol=point.symbol,
					name=point.name,
					return_pct=point.return_pct,
					created_at=hour_start,
				),
			)
		else:
			snapshot.name = point.name
			snapshot.return_pct = point.return_pct
			snapshot.created_at = hour_start
			session.add(snapshot)

	for snapshot in existing_snapshots:
		key = (snapshot.scope, snapshot.symbol or "")
		if key not in expected_keys:
			session.delete(snapshot)

	session.commit()

def _persist_hour_snapshot(
	session: Session,
	user_id: str,
	hour_bucket: datetime,
	total_value_cny: Decimal,
) -> None:
	hour_start = _current_hour_bucket(hour_bucket)
	hour_end = hour_start + timedelta(hours=1)
	existing_snapshots = list(
		session.exec(
			select(PortfolioSnapshot)
			.where(PortfolioSnapshot.user_id == user_id)
			.where(PortfolioSnapshot.created_at >= hour_start)
			.where(PortfolioSnapshot.created_at < hour_end)
			.order_by(PortfolioSnapshot.created_at.desc()),
		),
	)
	primary_snapshot = existing_snapshots[0] if existing_snapshots else None

	if primary_snapshot is None:
		session.add(
				PortfolioSnapshot(
					user_id=user_id,
					total_value_cny=total_value_cny,
					created_at=hour_start,
				),
		)
	else:
		primary_snapshot.total_value_cny = total_value_cny
		primary_snapshot.created_at = hour_start
		session.add(primary_snapshot)

	for duplicate_snapshot in existing_snapshots[1:]:
		session.delete(duplicate_snapshot)

	session.commit()

def _roll_live_portfolio_state_if_needed(session: Session, user_id: str, now: datetime) -> None:
	live_portfolio_state = runtime_state.live_portfolio_states.get(user_id)
	if live_portfolio_state is None:
		return

	current_hour = _current_hour_bucket(now)
	if live_portfolio_state.hour_bucket >= current_hour:
		return

	if live_portfolio_state.has_assets_in_bucket or live_portfolio_state.latest_value_cny > 0:
		_persist_hour_snapshot(
			session,
			user_id,
			live_portfolio_state.hour_bucket,
			live_portfolio_state.latest_value_cny,
		)

		runtime_state.live_portfolio_states.pop(user_id, None)

def _roll_live_holdings_return_state_if_needed(
	session: Session,
	user_id: str,
	now: datetime,
) -> None:
	live_holdings_return_state = runtime_state.live_holdings_return_states.get(user_id)
	if live_holdings_return_state is None:
		return

	current_hour = _current_hour_bucket(now)
	if live_holdings_return_state.hour_bucket >= current_hour:
		return

	if (
		live_holdings_return_state.has_tracked_holdings_in_bucket
		or live_holdings_return_state.aggregate_return_pct is not None
	):
		_persist_holdings_return_snapshot(
			session,
			user_id,
			live_holdings_return_state.hour_bucket,
			live_holdings_return_state.aggregate_return_pct,
			live_holdings_return_state.holding_points,
		)

		runtime_state.live_holdings_return_states.pop(user_id, None)

def _update_live_portfolio_state(
	user_id: str,
	now: datetime,
	total_value_cny: Decimal,
	has_assets: bool,
) -> None:
	live_portfolio_state = runtime_state.live_portfolio_states.get(user_id)
	current_hour = _current_hour_bucket(now)
	if live_portfolio_state is None:
		if not has_assets:
			return

		runtime_state.live_portfolio_states[user_id] = LivePortfolioState(
			hour_bucket=current_hour,
			latest_value_cny=total_value_cny,
			latest_generated_at=now,
			has_assets_in_bucket=has_assets,
		)
		return

	if live_portfolio_state.hour_bucket != current_hour:
		if not has_assets:
			runtime_state.live_portfolio_states.pop(user_id, None)
			return

		runtime_state.live_portfolio_states[user_id] = LivePortfolioState(
			hour_bucket=current_hour,
			latest_value_cny=total_value_cny,
			latest_generated_at=now,
			has_assets_in_bucket=has_assets,
		)
		return

	live_portfolio_state.latest_value_cny = total_value_cny
	live_portfolio_state.latest_generated_at = now
	live_portfolio_state.has_assets_in_bucket = (
		live_portfolio_state.has_assets_in_bucket or has_assets
	)
	runtime_state.live_portfolio_states[user_id] = live_portfolio_state

def _update_live_holdings_return_state(
	user_id: str,
	now: datetime,
	aggregate_return_pct: Decimal | None,
	holding_points: tuple[LiveHoldingReturnPoint, ...],
) -> None:
	live_holdings_return_state = runtime_state.live_holdings_return_states.get(user_id)
	current_hour = _current_hour_bucket(now)
	has_tracked_holdings = bool(holding_points)
	has_return_data = has_tracked_holdings or aggregate_return_pct is not None

	if live_holdings_return_state is None:
		if not has_return_data:
			return

		runtime_state.live_holdings_return_states[user_id] = LiveHoldingsReturnState(
			hour_bucket=current_hour,
			latest_generated_at=now,
			aggregate_return_pct=aggregate_return_pct,
			holding_points=holding_points,
			has_tracked_holdings_in_bucket=has_tracked_holdings,
		)
		return

	if live_holdings_return_state.hour_bucket != current_hour:
		if not has_return_data:
			runtime_state.live_holdings_return_states.pop(user_id, None)
			return

		runtime_state.live_holdings_return_states[user_id] = LiveHoldingsReturnState(
			hour_bucket=current_hour,
			latest_generated_at=now,
			aggregate_return_pct=aggregate_return_pct,
			holding_points=holding_points,
			has_tracked_holdings_in_bucket=has_tracked_holdings,
		)
		return

	live_holdings_return_state.latest_generated_at = now
	live_holdings_return_state.aggregate_return_pct = aggregate_return_pct
	live_holdings_return_state.holding_points = holding_points
	live_holdings_return_state.has_tracked_holdings_in_bucket = (
		live_holdings_return_state.has_tracked_holdings_in_bucket or has_tracked_holdings
	)
	runtime_state.live_holdings_return_states[user_id] = live_holdings_return_state
