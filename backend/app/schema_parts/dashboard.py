from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.fixed_precision import quantize_optional_decimal
from app.models import (
	DASHBOARD_CORRECTION_ACTIONS,
	DASHBOARD_CORRECTION_GRANULARITIES,
	DASHBOARD_SERIES_SCOPES,
)
from app.schema_parts.assets import CashAccountRead
from app.schema_parts.common import (
	UtcTimestampResponseModel,
	_normalize_choice,
	_normalize_optional_text,
)
from app.schema_parts.holdings import SecurityHoldingTransactionRead

class SecuritySearchRead(BaseModel):
	symbol: str
	name: str
	market: str
	currency: str
	exchange: Optional[str] = None
	source: Optional[str] = None

class SecurityQuoteRead(UtcTimestampResponseModel):
	symbol: str
	name: str
	market: str
	price: Decimal
	currency: str
	market_time: datetime | None = None
	warnings: list[str]

class ValuedCashAccount(BaseModel):
	id: int
	name: str
	platform: str
	balance: Decimal
	currency: str
	account_type: str
	started_on: Optional[date] = None
	note: Optional[str] = None
	fx_to_cny: Decimal
	value_cny: Decimal

class ValuedHolding(UtcTimestampResponseModel):
	id: int
	symbol: str
	name: str
	quantity: Decimal
	fallback_currency: str
	cost_basis_price: Optional[Decimal] = None
	market: str
	broker: Optional[str] = None
	started_on: Optional[date] = None
	note: Optional[str] = None
	price: Decimal
	price_currency: str
	fx_to_cny: Decimal
	value_cny: Decimal
	return_pct: Optional[Decimal] = None
	last_updated: Optional[datetime] = None

class ValuedFixedAsset(BaseModel):
	id: int
	name: str
	category: str
	current_value_cny: Decimal
	purchase_value_cny: Optional[Decimal] = None
	started_on: Optional[date] = None
	note: Optional[str] = None
	value_cny: Decimal
	return_pct: Optional[Decimal] = None

class ValuedLiabilityEntry(BaseModel):
	id: int
	name: str
	category: str
	currency: str
	balance: Decimal
	started_on: Optional[date] = None
	note: Optional[str] = None
	fx_to_cny: Decimal
	value_cny: Decimal

class ValuedOtherAsset(BaseModel):
	id: int
	name: str
	category: str
	current_value_cny: Decimal
	original_value_cny: Optional[Decimal] = None
	started_on: Optional[date] = None
	note: Optional[str] = None
	value_cny: Decimal
	return_pct: Optional[Decimal] = None

class AllocationSlice(BaseModel):
	label: str
	value: Decimal

class TimelinePoint(BaseModel):
	label: str
	value: Decimal
	timestamp_utc: datetime
	corrected: bool = False

class DashboardCorrectionCreate(BaseModel):
	series_scope: str = Field(min_length=1, max_length=32)
	symbol: str | None = Field(default=None, max_length=64)
	granularity: str = Field(min_length=3, max_length=8)
	bucket_utc: datetime
	action: str = Field(min_length=6, max_length=16)
	corrected_value: Decimal | None = None
	reason: str = Field(min_length=1, max_length=500)

	@field_validator("series_scope", mode="before")
	@classmethod
	def validate_series_scope(cls, value: str | None) -> str | None:
		return _normalize_choice(value, DASHBOARD_SERIES_SCOPES, "series_scope")

	@field_validator("granularity", mode="before")
	@classmethod
	def validate_granularity(cls, value: str | None) -> str | None:
		if value is None:
			return None
		normalized = value.strip().lower()
		if normalized not in DASHBOARD_CORRECTION_GRANULARITIES:
			raise ValueError(
				f"granularity must be one of: {', '.join(DASHBOARD_CORRECTION_GRANULARITIES)}.",
			)
		return normalized

	@field_validator("action", mode="before")
	@classmethod
	def validate_action(cls, value: str | None) -> str | None:
		return _normalize_choice(value, DASHBOARD_CORRECTION_ACTIONS, "action")

	@field_validator("symbol", "reason", mode="before")
	@classmethod
	def normalize_optional_fields(cls, value: str | None) -> str | None:
		return _normalize_optional_text(value)

	@field_validator("corrected_value", mode="before")
	@classmethod
	def normalize_corrected_value(cls, value: Any) -> Decimal | None:
		return quantize_optional_decimal(value)

	@model_validator(mode="after")
	def validate_corrected_value(self) -> DashboardCorrectionCreate:
		if self.action == "OVERRIDE" and self.corrected_value is None:
			raise ValueError("corrected_value is required when action is OVERRIDE.")
		if self.action == "DELETE" and self.corrected_value is not None:
			raise ValueError("corrected_value must be omitted when action is DELETE.")
		if self.series_scope != "HOLDING_RETURN":
			self.symbol = None
		elif self.symbol is None:
			raise ValueError("symbol is required for HOLDING_RETURN corrections.")
		return self

class DashboardCorrectionRead(UtcTimestampResponseModel):
	id: int
	series_scope: str
	symbol: str | None = None
	granularity: str
	bucket_utc: datetime
	action: str
	corrected_value: Decimal | None = None
	reason: str
	created_at: datetime
	updated_at: datetime

class AssetMutationAuditRead(UtcTimestampResponseModel):
	id: int
	actor_source: str
	api_key_name: str | None = None
	agent_name: str | None = None
	agent_task_id: int | None = None
	entity_type: str
	entity_id: int | None = None
	operation: str
	before_state: str | None = None
	after_state: str | None = None
	reason: str | None = None
	created_at: datetime

class AssetRecordRead(UtcTimestampResponseModel):
	id: int
	source: str
	api_key_name: str | None = None
	agent_name: str | None = None
	agent_task_id: int | None = None
	asset_class: str
	operation_kind: str
	entity_type: str
	entity_id: int | None = None
	title: str
	summary: str | None = None
	symbol: str | None = None
	effective_date: date | None = None
	amount: Decimal | None = None
	currency: str | None = None
	profit_amount: Decimal | None = None
	profit_currency: str | None = None
	profit_rate_pct: Decimal | None = None
	created_at: datetime

class HoldingReturnSeries(BaseModel):
	symbol: str
	name: str
	quantity: Decimal
	second_series: list[TimelinePoint] = Field(default_factory=list)
	minute_series: list[TimelinePoint] = Field(default_factory=list)
	hour_series: list[TimelinePoint]
	day_series: list[TimelinePoint]
	month_series: list[TimelinePoint]
	year_series: list[TimelinePoint]

class DashboardResponse(BaseModel):
	server_today: date
	total_value_cny: Decimal
	cash_value_cny: Decimal
	holdings_value_cny: Decimal
	fixed_assets_value_cny: Decimal
	liabilities_value_cny: Decimal
	other_assets_value_cny: Decimal
	usd_cny_rate: Optional[Decimal] = None
	hkd_cny_rate: Optional[Decimal] = None
	cash_accounts: list[ValuedCashAccount]
	holdings: list[ValuedHolding]
	fixed_assets: list[ValuedFixedAsset]
	liabilities: list[ValuedLiabilityEntry]
	other_assets: list[ValuedOtherAsset]
	allocation: list[AllocationSlice]
	second_series: list[TimelinePoint] = Field(default_factory=list)
	minute_series: list[TimelinePoint] = Field(default_factory=list)
	hour_series: list[TimelinePoint]
	day_series: list[TimelinePoint]
	month_series: list[TimelinePoint]
	year_series: list[TimelinePoint]
	holdings_return_second_series: list[TimelinePoint] = Field(default_factory=list)
	holdings_return_minute_series: list[TimelinePoint] = Field(default_factory=list)
	holdings_return_hour_series: list[TimelinePoint]
	holdings_return_day_series: list[TimelinePoint]
	holdings_return_month_series: list[TimelinePoint]
	holdings_return_year_series: list[TimelinePoint]
	holding_return_series: list[HoldingReturnSeries]
	recent_holding_transactions: list[SecurityHoldingTransactionRead] = Field(default_factory=list)
	warnings: list[str]

class AgentContextRead(UtcTimestampResponseModel):
	user_id: str
	generated_at: datetime
	server_today: date
	total_value_cny: Decimal
	cash_value_cny: Decimal
	holdings_value_cny: Decimal
	fixed_assets_value_cny: Decimal
	liabilities_value_cny: Decimal
	other_assets_value_cny: Decimal
	usd_cny_rate: Optional[Decimal] = None
	hkd_cny_rate: Optional[Decimal] = None
	allocation: list[AllocationSlice]
	cash_accounts: list[ValuedCashAccount]
	holdings: list[ValuedHolding]
	recent_holding_transactions: list[SecurityHoldingTransactionRead]
	pending_history_sync_requests: int
	warnings: list[str]
