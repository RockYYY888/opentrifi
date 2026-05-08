from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.fixed_precision import is_integral_decimal
from app.models import (
	BUY_FUNDING_HANDLINGS,
	CASH_LEDGER_ENTRY_TYPES,
	HOLDING_TRANSACTION_SIDES,
	SECURITY_MARKETS,
	SELL_PROCEEDS_HANDLINGS,
	SUPPORTED_CURRENCIES,
)
from app.schema_parts.assets import CashAccountRead
from app.schema_parts.common import (
	UtcTimestampResponseModel,
	_normalize_choice,
	_normalize_non_zero_decimal,
	_normalize_optional_non_zero_decimal,
	_normalize_optional_positive_decimal,
	_normalize_optional_text,
	_normalize_positive_decimal,
	_normalize_required_text,
)

class SecurityHoldingCreate(BaseModel):
	symbol: str = Field(min_length=1, max_length=32)
	name: str = Field(min_length=1, max_length=120)
	quantity: Decimal
	fallback_currency: str = Field(default="CNY", min_length=3, max_length=8)
	cost_basis_price: Decimal | None = None
	market: str = Field(default="OTHER", min_length=2, max_length=16)
	broker: Optional[str] = Field(default=None, max_length=120)
	started_on: Optional[date] = None
	note: Optional[str] = Field(default=None, max_length=500)

	@field_validator("market", mode="before")
	@classmethod
	def validate_market(cls, value: str | None) -> str | None:
		return _normalize_choice(value, SECURITY_MARKETS, "market")

	@field_validator("fallback_currency", mode="before")
	@classmethod
	def validate_fallback_currency(cls, value: str | None) -> str | None:
		return _normalize_choice(value, SUPPORTED_CURRENCIES, "fallback_currency")

	@field_validator("quantity", mode="before")
	@classmethod
	def normalize_quantity(cls, value: Any) -> Decimal:
		return _normalize_positive_decimal(value, "持仓数量")

	@field_validator("cost_basis_price", mode="before")
	@classmethod
	def normalize_cost_basis_price(cls, value: Any) -> Decimal | None:
		return _normalize_optional_positive_decimal(value, "持仓成本价")

	@field_validator("broker", "note", mode="before")
	@classmethod
	def normalize_optional_fields(cls, value: str | None) -> str | None:
		return _normalize_optional_text(value)

	@model_validator(mode="after")
	def validate_quantity_for_market(self) -> SecurityHoldingCreate:
		if self.market not in {"FUND", "CRYPTO"} and not is_integral_decimal(self.quantity):
			raise ValueError("股票请使用整数数量，基金可使用份额。")
		return self

class SecurityHoldingUpdate(BaseModel):
	model_config = ConfigDict(extra="forbid")

	quantity: Decimal | None = None
	cost_basis_price: Decimal | None = None
	started_on: Optional[date] = None
	broker: Optional[str] = Field(default=None, max_length=120)
	note: Optional[str] = Field(default=None, max_length=500)

	@field_validator("quantity", mode="before")
	@classmethod
	def normalize_quantity(cls, value: Any) -> Decimal | None:
		return _normalize_optional_positive_decimal(value, "持仓数量")

	@field_validator("cost_basis_price", mode="before")
	@classmethod
	def normalize_cost_basis_price(cls, value: Any) -> Decimal | None:
		return _normalize_optional_positive_decimal(value, "持仓成本价")

	@field_validator("broker", "note", mode="before")
	@classmethod
	def normalize_optional_fields(cls, value: str | None) -> str | None:
		return _normalize_optional_text(value)

class SecurityHoldingRead(UtcTimestampResponseModel):
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
	price: Optional[Decimal] = None
	price_currency: Optional[str] = None
	value_cny: Optional[Decimal] = None
	return_pct: Optional[Decimal] = None
	last_updated: Optional[datetime] = None

class SecurityHoldingTransactionCreate(BaseModel):
	side: str = Field(default="BUY", min_length=3, max_length=12)
	symbol: str = Field(min_length=1, max_length=32)
	name: str = Field(min_length=1, max_length=120)
	quantity: Decimal
	price: Decimal | None = None
	fallback_currency: str = Field(default="CNY", min_length=3, max_length=8)
	market: str = Field(default="OTHER", min_length=2, max_length=16)
	broker: Optional[str] = Field(default=None, max_length=120)
	traded_on: date
	note: Optional[str] = Field(default=None, max_length=500)
	sell_proceeds_handling: Optional[str] = Field(default=None, min_length=7, max_length=32)
	sell_proceeds_account_id: Optional[int] = Field(default=None, ge=1)
	buy_funding_handling: Optional[str] = Field(default=None, min_length=10, max_length=32)
	buy_funding_account_id: Optional[int] = Field(default=None, ge=1)

	@field_validator("side", mode="before")
	@classmethod
	def validate_side(cls, value: str | None) -> str | None:
		return _normalize_choice(value, HOLDING_TRANSACTION_SIDES, "side")

	@field_validator("market", mode="before")
	@classmethod
	def validate_market(cls, value: str | None) -> str | None:
		return _normalize_choice(value, SECURITY_MARKETS, "market")

	@field_validator("fallback_currency", mode="before")
	@classmethod
	def validate_fallback_currency(cls, value: str | None) -> str | None:
		return _normalize_choice(value, SUPPORTED_CURRENCIES, "fallback_currency")

	@field_validator("quantity", mode="before")
	@classmethod
	def normalize_quantity(cls, value: Any) -> Decimal:
		return _normalize_positive_decimal(value, "成交数量")

	@field_validator("price", mode="before")
	@classmethod
	def normalize_price(cls, value: Any) -> Decimal | None:
		return _normalize_optional_positive_decimal(value, "成交价格")

	@field_validator("sell_proceeds_handling", mode="before")
	@classmethod
	def validate_sell_proceeds_handling(cls, value: str | None) -> str | None:
		return _normalize_choice(value, SELL_PROCEEDS_HANDLINGS, "sell_proceeds_handling")

	@field_validator("buy_funding_handling", mode="before")
	@classmethod
	def validate_buy_funding_handling(cls, value: str | None) -> str | None:
		return _normalize_choice(value, BUY_FUNDING_HANDLINGS, "buy_funding_handling")

	@field_validator("broker", "note", mode="before")
	@classmethod
	def normalize_optional_fields(cls, value: str | None) -> str | None:
		return _normalize_optional_text(value)

	@model_validator(mode="after")
	def validate_quantity_for_market(self) -> SecurityHoldingTransactionCreate:
		if self.market not in {"FUND", "CRYPTO"} and not is_integral_decimal(self.quantity):
			raise ValueError("股票请使用整数数量，基金可使用份额。")

		if self.side == "BUY":
			if self.sell_proceeds_handling is not None or self.sell_proceeds_account_id is not None:
				raise ValueError("买入交易不支持卖出回款处理选项。")
			effective_funding = (
				self.buy_funding_handling
				or ("DEDUCT_FROM_EXISTING_CASH" if self.buy_funding_account_id is not None else None)
			)
			if effective_funding == "DEDUCT_FROM_EXISTING_CASH" and self.buy_funding_account_id is None:
				raise ValueError("买入从现金账户扣款时必须选择目标现金账户。")
			if effective_funding != "DEDUCT_FROM_EXISTING_CASH" and self.buy_funding_account_id is not None:
				raise ValueError("只有从现有现金账户扣款时才允许传入目标现金账户。")
			return self

		if self.buy_funding_handling is not None or self.buy_funding_account_id is not None:
			raise ValueError("卖出交易不支持买入扣款处理选项。")

		effective_handling = self.sell_proceeds_handling or "CREATE_NEW_CASH"
		if effective_handling == "ADD_TO_EXISTING_CASH" and self.sell_proceeds_account_id is None:
			raise ValueError("卖出并入现有现金时必须选择目标现金账户。")
		if effective_handling != "ADD_TO_EXISTING_CASH" and self.sell_proceeds_account_id is not None:
			raise ValueError("只有并入现有现金时才允许传入目标现金账户。")

		return self

class SecurityHoldingTransactionUpdate(BaseModel):
	model_config = ConfigDict(extra="forbid")

	name: Optional[str] = Field(default=None, min_length=1, max_length=120)
	quantity: Decimal | None = None
	price: Decimal | None = None
	fallback_currency: Optional[str] = Field(default=None, min_length=3, max_length=8)
	broker: Optional[str] = Field(default=None, max_length=120)
	traded_on: Optional[date] = None
	note: Optional[str] = Field(default=None, max_length=500)
	sell_proceeds_handling: Optional[str] = Field(default=None, min_length=7, max_length=32)
	sell_proceeds_account_id: Optional[int] = Field(default=None, ge=1)
	buy_funding_handling: Optional[str] = Field(default=None, min_length=10, max_length=32)
	buy_funding_account_id: Optional[int] = Field(default=None, ge=1)

	@field_validator("name", mode="before")
	@classmethod
	def normalize_name(cls, value: str | None) -> str | None:
		if value is None:
			return None
		return _normalize_required_text(value, "name")

	@field_validator("quantity", mode="before")
	@classmethod
	def normalize_quantity(cls, value: Any) -> Decimal | None:
		return _normalize_optional_positive_decimal(value, "成交数量")

	@field_validator("price", mode="before")
	@classmethod
	def normalize_price(cls, value: Any) -> Decimal | None:
		return _normalize_optional_positive_decimal(value, "成交价格")

	@field_validator("sell_proceeds_handling", mode="before")
	@classmethod
	def validate_sell_proceeds_handling(cls, value: str | None) -> str | None:
		return _normalize_choice(value, SELL_PROCEEDS_HANDLINGS, "sell_proceeds_handling")

	@field_validator("fallback_currency", mode="before")
	@classmethod
	def validate_fallback_currency(cls, value: str | None) -> str | None:
		return _normalize_choice(value, SUPPORTED_CURRENCIES, "fallback_currency")

	@field_validator("buy_funding_handling", mode="before")
	@classmethod
	def validate_buy_funding_handling(cls, value: str | None) -> str | None:
		return _normalize_choice(value, BUY_FUNDING_HANDLINGS, "buy_funding_handling")

	@field_validator("broker", "note", mode="before")
	@classmethod
	def normalize_optional_fields(cls, value: str | None) -> str | None:
		return _normalize_optional_text(value)

	@model_validator(mode="after")
	def validate_sell_proceeds_fields(self) -> SecurityHoldingTransactionUpdate:
		if (
			self.sell_proceeds_handling is not None
			and self.sell_proceeds_handling != "ADD_TO_EXISTING_CASH"
			and self.sell_proceeds_account_id is not None
		):
			raise ValueError("只有并入现有现金时才允许传入目标现金账户。")
		if (
			self.buy_funding_handling is not None
			and self.buy_funding_handling != "DEDUCT_FROM_EXISTING_CASH"
			and self.buy_funding_account_id is not None
		):
			raise ValueError("只有从现有现金账户扣款时才允许传入目标现金账户。")
		return self

class SecurityHoldingTransactionRead(UtcTimestampResponseModel):
	id: int
	symbol: str
	name: str
	side: str
	quantity: Decimal
	price: Optional[Decimal] = None
	fallback_currency: str
	market: str
	broker: Optional[str] = None
	traded_on: date
	note: Optional[str] = None
	sell_proceeds_handling: Optional[str] = None
	sell_proceeds_account_id: Optional[int] = None
	buy_funding_handling: Optional[str] = None
	buy_funding_account_id: Optional[int] = None
	created_at: datetime
	updated_at: datetime

class HoldingTransactionApplyRead(UtcTimestampResponseModel):
	transaction: SecurityHoldingTransactionRead
	holding: SecurityHoldingRead | None = None
	cash_account: CashAccountRead | None = None
	sell_proceeds_handling: str | None = None

class CashLedgerEntryRead(UtcTimestampResponseModel):
	id: int
	cash_account_id: int
	entry_type: str
	amount: Decimal
	currency: str
	happened_on: date
	note: str | None = None
	holding_transaction_id: int | None = None
	cash_transfer_id: int | None = None
	created_at: datetime
	updated_at: datetime

	@field_validator("entry_type", mode="before")
	@classmethod
	def validate_entry_type(cls, value: str | None) -> str | None:
		return _normalize_choice(value, CASH_LEDGER_ENTRY_TYPES, "entry_type")

class CashTransferCreate(BaseModel):
	from_account_id: int = Field(ge=1)
	to_account_id: int = Field(ge=1)
	source_amount: Decimal
	target_amount: Decimal | None = None
	transferred_on: date
	note: Optional[str] = Field(default=None, max_length=500)

	@field_validator("source_amount", mode="before")
	@classmethod
	def normalize_source_amount(cls, value: Any) -> Decimal:
		return _normalize_positive_decimal(value, "转出金额")

	@field_validator("target_amount", mode="before")
	@classmethod
	def normalize_target_amount(cls, value: Any) -> Decimal | None:
		return _normalize_optional_positive_decimal(value, "转入金额")

	@field_validator("note", mode="before")
	@classmethod
	def normalize_note(cls, value: str | None) -> str | None:
		return _normalize_optional_text(value)

	@model_validator(mode="after")
	def validate_accounts(self) -> CashTransferCreate:
		if self.from_account_id == self.to_account_id:
			raise ValueError("转出账户和转入账户不能相同。")
		return self

class CashTransferUpdate(BaseModel):
	from_account_id: int | None = Field(default=None, ge=1)
	to_account_id: int | None = Field(default=None, ge=1)
	source_amount: Decimal | None = None
	target_amount: Decimal | None = None
	transferred_on: date | None = None
	note: Optional[str] = Field(default=None, max_length=500)

	@field_validator("source_amount", mode="before")
	@classmethod
	def normalize_source_amount(cls, value: Any) -> Decimal | None:
		return _normalize_optional_positive_decimal(value, "转出金额")

	@field_validator("target_amount", mode="before")
	@classmethod
	def normalize_target_amount(cls, value: Any) -> Decimal | None:
		return _normalize_optional_positive_decimal(value, "转入金额")

	@field_validator("note", mode="before")
	@classmethod
	def normalize_note(cls, value: str | None) -> str | None:
		return _normalize_optional_text(value)

	@model_validator(mode="after")
	def validate_accounts(self) -> CashTransferUpdate:
		if self.from_account_id is not None and self.from_account_id == self.to_account_id:
			raise ValueError("转出账户和转入账户不能相同。")
		return self

class CashTransferRead(UtcTimestampResponseModel):
	id: int
	from_account_id: int
	to_account_id: int
	source_amount: Decimal
	target_amount: Decimal
	source_currency: str
	target_currency: str
	transferred_on: date
	note: str | None = None
	created_at: datetime
	updated_at: datetime

class CashTransferApplyRead(UtcTimestampResponseModel):
	transfer: CashTransferRead
	from_account: CashAccountRead
	to_account: CashAccountRead

class CashLedgerAdjustmentCreate(BaseModel):
	cash_account_id: int = Field(ge=1)
	amount: Decimal
	happened_on: date
	note: Optional[str] = Field(default=None, max_length=500)

	@field_validator("amount", mode="before")
	@classmethod
	def validate_amount(cls, value: Any) -> Decimal:
		return _normalize_non_zero_decimal(value, "调整金额")

	@field_validator("note", mode="before")
	@classmethod
	def normalize_note(cls, value: str | None) -> str | None:
		return _normalize_optional_text(value)

class CashLedgerAdjustmentUpdate(BaseModel):
	amount: Decimal | None = None
	happened_on: date | None = None
	note: Optional[str] = Field(default=None, max_length=500)

	@field_validator("amount", mode="before")
	@classmethod
	def validate_amount(cls, value: Any) -> Decimal | None:
		return _normalize_optional_non_zero_decimal(value, "调整金额")

	@field_validator("note", mode="before")
	@classmethod
	def normalize_note(cls, value: str | None) -> str | None:
		return _normalize_optional_text(value)

class CashLedgerAdjustmentApplyRead(UtcTimestampResponseModel):
	entry: CashLedgerEntryRead
	account: CashAccountRead
