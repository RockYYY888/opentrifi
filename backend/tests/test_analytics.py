from datetime import datetime, timezone
from decimal import Decimal

from app.analytics import build_return_timeline, build_timeline
from app.models import HoldingPerformanceSnapshot, PortfolioSnapshot
from app.services.common_service import _is_current_minute


def D(value: str | int | float) -> Decimal:
	return Decimal(str(value))


def make_snapshot(timestamp: datetime, total: str | int | float) -> PortfolioSnapshot:
	return PortfolioSnapshot(user_id="tester", created_at=timestamp, total_value_cny=D(total))


def make_return_snapshot(
	timestamp: datetime,
	return_pct: str | int | float,
	symbol: str = "TOTAL",
) -> HoldingPerformanceSnapshot:
	return HoldingPerformanceSnapshot(
		user_id="tester",
		scope="TOTAL" if symbol == "TOTAL" else "HOLDING",
		symbol=None if symbol == "TOTAL" else symbol,
		name=None if symbol == "TOTAL" else symbol,
		return_pct=D(return_pct),
		created_at=timestamp,
	)


def test_build_timeline_uses_latest_snapshot_per_hour_bucket() -> None:
	snapshots = [
		make_snapshot(datetime(2026, 2, 28, 9, 0, tzinfo=timezone.utc), 1000),
		make_snapshot(datetime(2026, 2, 28, 9, 30, tzinfo=timezone.utc), 1200),
		make_snapshot(datetime(2026, 2, 28, 10, 0, tzinfo=timezone.utc), 1400),
	]

	series = build_timeline(snapshots, "hour")

	assert [point.label for point in series] == ["02-28 17:00", "02-28 18:00"]
	assert [point.value for point in series] == [1200, 1400]
	assert [point.corrected for point in series] == [False, False]
	assert [point.timestamp_utc for point in series] == [
		datetime(2026, 2, 28, 9, 0, tzinfo=timezone.utc),
		datetime(2026, 2, 28, 10, 0, tzinfo=timezone.utc),
	]


def test_build_timeline_uses_latest_snapshot_per_day_bucket() -> None:
	snapshots = [
		make_snapshot(datetime(2026, 2, 27, 10, 0, tzinfo=timezone.utc), 900),
		make_snapshot(datetime(2026, 2, 27, 18, 0, tzinfo=timezone.utc), 1100),
		make_snapshot(datetime(2026, 2, 28, 10, 0, tzinfo=timezone.utc), 1200),
	]

	series = build_timeline(snapshots, "day")

	assert [point.label for point in series] == ["02-27", "02-28"]
	assert [point.value for point in series] == [900, 1200]


def test_build_timeline_uses_latest_snapshot_per_month_bucket() -> None:
	snapshots = [
		make_snapshot(datetime(2025, 12, 1, 10, 0, tzinfo=timezone.utc), 900),
		make_snapshot(datetime(2025, 12, 18, 18, 0, tzinfo=timezone.utc), 1100),
		make_snapshot(datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc), 1200),
	]

	series = build_timeline(snapshots, "month")

	assert [point.label for point in series] == ["2025-12", "2026-01"]
	assert [point.value for point in series] == [1100, 1200]


def test_build_timeline_uses_latest_snapshot_per_year_bucket() -> None:
	snapshots = [
		make_snapshot(datetime(2025, 2, 1, 10, 0, tzinfo=timezone.utc), 900),
		make_snapshot(datetime(2025, 12, 18, 18, 0, tzinfo=timezone.utc), 1100),
		make_snapshot(datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc), 1200),
	]

	series = build_timeline(snapshots, "year")

	assert [point.label for point in series] == ["2025", "2026"]
	assert [point.value for point in series] == [1100, 1200]


def test_build_timeline_handles_naive_and_aware_snapshot_timestamps() -> None:
	snapshots = [
		make_snapshot(datetime(2026, 3, 1, 3, 15), 1000),
		make_snapshot(datetime(2026, 3, 1, 3, 45, tzinfo=timezone.utc), 1250),
	]

	series = build_timeline(snapshots, "hour")

	assert [point.label for point in series] == ["03-01 11:00"]
	assert [point.value for point in series] == [1250]


def test_build_return_timeline_uses_latest_snapshot_per_hour_bucket() -> None:
	snapshots = [
		make_return_snapshot(datetime(2026, 3, 1, 3, 5), 1.5),
		make_return_snapshot(datetime(2026, 3, 1, 3, 50, tzinfo=timezone.utc), 2.25),
		make_return_snapshot(datetime(2026, 3, 1, 4, 0, tzinfo=timezone.utc), -0.75),
	]

	series = build_return_timeline(snapshots, "hour")

	assert [point.label for point in series] == ["03-01 11:00", "03-01 12:00"]
	assert [point.value for point in series] == [2.25, -0.75]
	assert [point.corrected for point in series] == [False, False]


def test_is_current_minute_matches_same_bucket() -> None:
	now = datetime(2026, 3, 1, 3, 15, 42, tzinfo=timezone.utc)
	cached_at = datetime(2026, 3, 1, 3, 15, 1, tzinfo=timezone.utc)

	assert _is_current_minute(cached_at, now) is True


def test_is_current_minute_rejects_previous_bucket() -> None:
	now = datetime(2026, 3, 1, 3, 15, 0, tzinfo=timezone.utc)
	cached_at = datetime(2026, 3, 1, 3, 14, 59, tzinfo=timezone.utc)

	assert _is_current_minute(cached_at, now) is False
