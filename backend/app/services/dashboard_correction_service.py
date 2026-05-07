from __future__ import annotations
from datetime import datetime, timedelta
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
from app.fixed_precision import display_money, display_percent
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
    _current_hour_bucket,
    _filter_dashboard_warnings_for_user,
    _invalidate_dashboard_cache,
    _is_current_minute,
    _server_today_date,
)
from app.services.history_sync_service import _has_holding_history_sync_pending
from app.services.portfolio_service import (
    _load_display_fx_rates,
    _value_cash_accounts,
    _value_fixed_assets,
    _value_holdings,
    _value_liabilities,
    _value_other_assets,
)
from app.services import service_context
from app.services.service_context import SessionDependency

def _to_dashboard_correction_read(correction: DashboardCorrection) -> DashboardCorrectionRead:
	return DashboardCorrectionRead(
		id=correction.id or 0,
		series_scope=correction.series_scope,
		symbol=correction.symbol,
		granularity=correction.granularity,
		bucket_utc=correction.bucket_utc,
		action=correction.action,
		corrected_value=correction.corrected_value,
		reason=correction.reason,
		created_at=correction.created_at,
		updated_at=correction.updated_at,
	)

def _correction_key(
	series_scope: str,
	symbol: str | None,
	granularity: str,
	bucket_utc: datetime,
) -> tuple[str, str, str, datetime]:
	return (
		series_scope,
		(symbol or "").upper(),
		granularity,
		_coerce_utc_datetime(bucket_utc),
	)

def _load_dashboard_correction_lookup(
	session: Session,
	user_id: str,
) -> dict[tuple[str, str, str, datetime], DashboardCorrection]:
	rows = list(
		session.exec(
			select(DashboardCorrection)
			.where(DashboardCorrection.user_id == user_id)
			.order_by(DashboardCorrection.bucket_utc.asc(), DashboardCorrection.updated_at.asc()),
		),
	)
	lookup: dict[tuple[str, str, str, datetime], DashboardCorrection] = {}
	for row in rows:
		lookup[_correction_key(row.series_scope, row.symbol, row.granularity, row.bucket_utc)] = row
	return lookup

def _apply_dashboard_corrections(
	points: list[Any],
	correction_lookup: dict[tuple[str, str, str, datetime], DashboardCorrection],
	*,
	series_scope: str,
	granularity: str,
	symbol: str | None = None,
) -> list[Any]:
	corrected_points: list[Any] = []
	def _display_corrected_value(raw_value: Any):
		if series_scope == "PORTFOLIO_TOTAL":
			return display_money(raw_value)
		return display_percent(raw_value)
	for point in points:
		point_timestamp = _coerce_utc_datetime(point.timestamp_utc)
		correction = correction_lookup.get(
			_correction_key(series_scope, symbol, granularity, point_timestamp),
		)
		if correction is None:
			corrected_points.append(point)
			continue

		if correction.action == "DELETE":
			continue

		updated_value = point.value
		if correction.corrected_value is not None:
			updated_value = _display_corrected_value(correction.corrected_value)

		corrected_points.append(
			point.model_copy(
				update={
					"value": updated_value,
					"corrected": True,
				},
			),
		)
	return corrected_points

def create_dashboard_correction(
	payload: DashboardCorrectionCreate,
	current_user: CurrentUserDependency,
	session: SessionDependency,
) -> DashboardCorrectionRead:
	if payload.series_scope not in DASHBOARD_SERIES_SCOPES:
		raise HTTPException(status_code=422, detail="Unsupported series_scope.")
	if payload.action not in DASHBOARD_CORRECTION_ACTIONS:
		raise HTTPException(status_code=422, detail="Unsupported correction action.")
	if payload.granularity not in DASHBOARD_CORRECTION_GRANULARITIES:
		raise HTTPException(status_code=422, detail="Unsupported granularity.")

	bucket_utc = bucket_start_utc(payload.bucket_utc, payload.granularity)
	corrected_value = payload.corrected_value
	if corrected_value is not None:
		corrected_value = (
			display_money(corrected_value)
			if payload.series_scope == "PORTFOLIO_TOTAL"
			else display_percent(corrected_value)
		)
	correction = DashboardCorrection(
		user_id=current_user.username,
		series_scope=payload.series_scope,
		symbol=payload.symbol.upper() if payload.symbol else None,
		granularity=payload.granularity,
		bucket_utc=bucket_utc,
		action=payload.action,
		corrected_value=corrected_value,
		reason=payload.reason,
	)
	session.add(correction)
	session.commit()
	session.refresh(correction)
	_invalidate_dashboard_cache(current_user.username)
	return _to_dashboard_correction_read(correction)

def list_dashboard_corrections(
	current_user: CurrentUserDependency,
	session: SessionDependency,
) -> list[DashboardCorrectionRead]:
	corrections = list(
		session.exec(
			select(DashboardCorrection)
			.where(DashboardCorrection.user_id == current_user.username)
			.order_by(DashboardCorrection.bucket_utc.desc(), DashboardCorrection.updated_at.desc()),
		),
	)
	return [_to_dashboard_correction_read(correction) for correction in corrections]

def delete_dashboard_correction(
	correction_id: int,
	current_user: CurrentUserDependency,
	session: SessionDependency,
) -> Response:
	correction = session.get(DashboardCorrection, correction_id)
	if correction is None or correction.user_id != current_user.username:
		raise HTTPException(status_code=404, detail="Dashboard correction not found.")

	session.delete(correction)
	session.commit()
	_invalidate_dashboard_cache(current_user.username)
	return Response(status_code=204)
