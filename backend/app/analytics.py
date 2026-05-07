from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Iterable, TypeVar
from zoneinfo import ZoneInfo

from app.fixed_precision import display_money, display_percent
from app.models import HoldingPerformanceSnapshot, PortfolioSnapshot
from app.schemas import TimelinePoint

SeriesSnapshot = TypeVar("SeriesSnapshot")
DISPLAY_TIMEZONE = ZoneInfo("Asia/Shanghai")


def _coerce_utc_datetime(value: datetime) -> datetime:
	if value.tzinfo is None:
		return value.replace(tzinfo=timezone.utc)

	return value.astimezone(timezone.utc)


def _bucket_label(timestamp: datetime, granularity: str) -> str:
	normalized_timestamp = _coerce_utc_datetime(timestamp).astimezone(DISPLAY_TIMEZONE)
	if granularity == "second":
		return normalized_timestamp.strftime("%m-%d %H:%M:%S")
	if granularity == "minute":
		return normalized_timestamp.strftime("%m-%d %H:%M")
	if granularity == "hour":
		return normalized_timestamp.strftime("%m-%d %H:00")
	if granularity == "day":
		return normalized_timestamp.strftime("%m-%d")
	if granularity == "month":
		return normalized_timestamp.strftime("%Y-%m")
	if granularity == "year":
		return normalized_timestamp.strftime("%Y")
	msg = f"Unsupported granularity: {granularity}"
	raise ValueError(msg)


def bucket_start_utc(timestamp: datetime, granularity: str) -> datetime:
	normalized_timestamp = _coerce_utc_datetime(timestamp).astimezone(DISPLAY_TIMEZONE)
	if granularity == "second":
		bucket_start_local = normalized_timestamp.replace(microsecond=0)
	elif granularity == "minute":
		bucket_start_local = normalized_timestamp.replace(second=0, microsecond=0)
	elif granularity == "hour":
		bucket_start_local = normalized_timestamp.replace(minute=0, second=0, microsecond=0)
	elif granularity == "day":
		bucket_start_local = normalized_timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
	elif granularity == "month":
		bucket_start_local = normalized_timestamp.replace(
			day=1,
			hour=0,
			minute=0,
			second=0,
			microsecond=0,
		)
	elif granularity == "year":
		bucket_start_local = normalized_timestamp.replace(
			month=1,
			day=1,
			hour=0,
			minute=0,
			second=0,
			microsecond=0,
		)
	else:
		msg = f"Unsupported granularity: {granularity}"
		raise ValueError(msg)
	return bucket_start_local.astimezone(timezone.utc)


def _build_timeline_from_snapshots(
	snapshots: Iterable[SeriesSnapshot],
	granularity: str,
	get_created_at: Callable[[SeriesSnapshot], datetime],
	get_value: Callable[[SeriesSnapshot], Decimal],
	format_value: Callable[[Decimal], Decimal],
) -> list[TimelinePoint]:
	buckets: dict[datetime, SeriesSnapshot] = {}
	for snapshot in snapshots:
		snapshot_created_at = _coerce_utc_datetime(get_created_at(snapshot))
		bucket_utc = bucket_start_utc(snapshot_created_at, granularity)
		current = buckets.get(bucket_utc)
		current_created_at = (
			_coerce_utc_datetime(get_created_at(current)) if current is not None else None
		)
		if current is None or (
			current_created_at is not None and snapshot_created_at >= current_created_at
		):
			buckets[bucket_utc] = snapshot

	return [
		TimelinePoint(
			label=_bucket_label(bucket_utc, granularity),
			value=format_value(get_value(snapshot)),
			timestamp_utc=bucket_utc,
			corrected=False,
		)
		for bucket_utc, snapshot in sorted(
			buckets.items(),
			key=lambda item: item[0],
		)
	]


def build_timeline(
	snapshots: Iterable[PortfolioSnapshot],
	granularity: str,
) -> list[TimelinePoint]:
	"""Collapse snapshots to the latest point in each reporting bucket."""
	return _build_timeline_from_snapshots(
		snapshots,
		granularity,
		get_created_at=lambda snapshot: snapshot.created_at,
		get_value=lambda snapshot: snapshot.total_value_cny,
		format_value=display_money,
	)


def build_return_timeline(
	snapshots: Iterable[HoldingPerformanceSnapshot],
	granularity: str,
) -> list[TimelinePoint]:
	return _build_timeline_from_snapshots(
		snapshots,
		granularity,
		get_created_at=lambda snapshot: snapshot.created_at,
		get_value=lambda snapshot: snapshot.return_pct,
		format_value=display_percent,
	)
