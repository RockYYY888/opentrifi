from datetime import datetime, timezone
from decimal import Decimal

from app.fixed_precision import decimal_to_string
from app.schemas import HoldingReturnSeries, SecurityHoldingRead, TimelinePoint, UserFeedbackRead


def test_user_feedback_read_serializes_naive_timestamps_as_explicit_utc() -> None:
	record = UserFeedbackRead(
		id=1,
		user_id="tester",
		message="反馈内容",
		category="USER_REQUEST",
		priority="MEDIUM",
		source="USER",
		status="OPEN",
		is_system=False,
		created_at=datetime(2026, 3, 1, 4, 20, 51, 753577),
	)

	payload = record.model_dump(mode="json")

	assert payload["created_at"] == "2026-03-01T04:20:51.753577Z"


def test_security_holding_read_serializes_aware_timestamps_with_utc_marker() -> None:
	record = SecurityHoldingRead(
		id=1,
		symbol="AAPL",
		name="Apple Inc.",
		quantity=Decimal("1"),
		fallback_currency="USD",
		market="US",
		last_updated=datetime(2026, 3, 1, 4, 20, 51, tzinfo=timezone.utc),
	)

	payload = record.model_dump(mode="json")

	assert payload["last_updated"] == "2026-03-01T04:20:51Z"


def test_holding_return_series_includes_quantity_field() -> None:
	record = HoldingReturnSeries(
		symbol="0700.HK",
		name="腾讯控股",
		quantity=Decimal("120"),
		hour_series=[],
		day_series=[],
		month_series=[],
		year_series=[],
	)

	payload = record.model_dump(mode="json")

	assert payload["quantity"] == "120"


def test_financial_read_models_serialize_decimal_strings_without_float_drift() -> None:
	record = SecurityHoldingRead(
		id=1,
		symbol="AAPL",
		name="Apple Inc.",
		quantity=Decimal("0.1") + Decimal("0.2"),
		fallback_currency="USD",
		cost_basis_price=Decimal("188.50000000"),
		market="US",
		price=Decimal("199.01000000"),
		value_cny=Decimal("1393.07000000"),
		return_pct=Decimal("5.58"),
	)

	payload = record.model_dump(mode="json")

	assert payload["quantity"] == "0.3"
	assert payload["cost_basis_price"] == "188.50000000"
	assert payload["price"] == "199.01000000"
	assert payload["value_cny"] == "1393.07000000"
	assert payload["return_pct"] == "5.58"


def test_timeline_point_serializes_decimal_value_for_chart_adapter() -> None:
	point = TimelinePoint(
		label="05-07",
		value=Decimal("0.1") + Decimal("0.2"),
		timestamp_utc=datetime(2026, 5, 7, tzinfo=timezone.utc),
	)

	payload = point.model_dump(mode="json")

	assert payload["value"] == "0.3"


def test_decimal_to_string_preserves_fixed_precision_scale() -> None:
	assert decimal_to_string(Decimal("123.45000000")) == "123.45000000"
