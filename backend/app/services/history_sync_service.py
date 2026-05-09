from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from sqlmodel import Session, select

from app import runtime_state
from app.fixed_precision import DECIMAL_ZERO, quantize_decimal
from app.models import HOLDING_HISTORY_SYNC_STATUSES, HoldingHistorySyncRequest, utc_now
from app.services.common_service import _current_hour_bucket
from app.services.sql_expression import sql_expr

def _enqueue_holding_history_sync_request(
	session: Session,
	*,
	user_id: str,
	trigger_symbol: str | None = None,
) -> None:
	with runtime_state.redis_lock(
		f"holding-history-sync-enqueue:{user_id}",
		timeout=10,
		blocking_timeout=10,
	):
		existing_request = session.exec(
				select(HoldingHistorySyncRequest)
				.where(HoldingHistorySyncRequest.user_id == user_id)
				.order_by(
					sql_expr(HoldingHistorySyncRequest.requested_at).desc(),
					sql_expr(HoldingHistorySyncRequest.id).desc(),
				),
		).first()
		now = utc_now()
		if existing_request is None:
			session.add(
				HoldingHistorySyncRequest(
					user_id=user_id,
					status=HOLDING_HISTORY_SYNC_STATUSES[0],
					trigger_symbol=trigger_symbol,
					requested_at=now,
					started_at=None,
					completed_at=None,
					error_message=None,
				),
			)
			return

		existing_request.status = HOLDING_HISTORY_SYNC_STATUSES[0]
		existing_request.trigger_symbol = trigger_symbol
		existing_request.requested_at = now
		existing_request.started_at = None
		existing_request.completed_at = None
		existing_request.error_message = None
		session.add(existing_request)

def _has_holding_history_sync_pending(session: Session, user_id: str) -> bool:
	request = session.exec(
			select(HoldingHistorySyncRequest.id).where(
				HoldingHistorySyncRequest.user_id == user_id,
				sql_expr(HoldingHistorySyncRequest.status).in_(
				(
					HOLDING_HISTORY_SYNC_STATUSES[0],
					HOLDING_HISTORY_SYNC_STATUSES[1],
				),
			),
		),
	).first()
	return request is not None

def _build_hour_buckets(start_at: datetime, end_at: datetime) -> list[datetime]:
	start_hour = _current_hour_bucket(start_at)
	end_hour = _current_hour_bucket(end_at)
	if end_hour < start_hour:
		return []

	hours: list[datetime] = []
	cursor = start_hour
	while cursor <= end_hour:
		hours.append(cursor)
		cursor += timedelta(hours=1)
	return hours

def _fill_hourly_prices(
	hours: list[datetime],
	known_points: list[tuple[datetime, Decimal]],
	fallback_price: Decimal,
) -> dict[datetime, Decimal]:
	known_map: dict[datetime, Decimal] = {}
	for bucket, price in known_points:
		if price <= 0:
			continue
		known_map[_current_hour_bucket(bucket)] = quantize_decimal(price)

	first_known_price = next(iter(sorted(known_map.values())), None)
	normalized_fallback_price = quantize_decimal(fallback_price)
	default_price = (
		normalized_fallback_price
		if normalized_fallback_price > 0
		else (first_known_price or DECIMAL_ZERO)
	)
	last_known_price: Decimal | None = None

	filled: dict[datetime, Decimal] = {}
	for hour in hours:
		if hour in known_map:
			last_known_price = known_map[hour]
			filled[hour] = known_map[hour]
			continue

		if last_known_price is not None:
			filled[hour] = last_known_price
			continue

		filled[hour] = default_price

	return filled

__all__ = ['_enqueue_holding_history_sync_request', '_has_holding_history_sync_pending', '_build_hour_buckets', '_fill_hourly_prices']
