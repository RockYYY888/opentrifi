from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

from app.models import (
	CASH_ACCOUNT_TYPES,
	FIXED_ASSET_CATEGORIES,
	LIABILITY_CATEGORIES,
	LIABILITY_CURRENCIES,
	OTHER_ASSET_CATEGORIES,
	SUPPORTED_CURRENCIES,
)
from app.schema_parts.common import (
	_normalize_choice,
	_normalize_non_negative_decimal,
	_normalize_optional_positive_decimal,
	_normalize_optional_text,
	_normalize_positive_decimal,
)

class CashAccountCreate(BaseModel):
	name: str = Field(min_length=1, max_length=80)
	platform: str = Field(min_length=1, max_length=80)
	currency: str = Field(default="CNY", min_length=3, max_length=8)
	balance: Decimal
	account_type: str = Field(default="OTHER", min_length=4, max_length=20)
	started_on: Optional[date] = None
	note: Optional[str] = Field(default=None, max_length=500)

	@field_validator("account_type", mode="before")
	@classmethod
	def validate_account_type(cls, value: str | None) -> str | None:
		return _normalize_choice(value, CASH_ACCOUNT_TYPES, "account_type")

	@field_validator("currency", mode="before")
	@classmethod
	def validate_currency(cls, value: str | None) -> str | None:
		return _normalize_choice(value, SUPPORTED_CURRENCIES, "currency")

	@field_validator("balance", mode="before")
	@classmethod
	def normalize_balance(cls, value: Any) -> Decimal:
		return _normalize_non_negative_decimal(value, "余额")

	@field_validator("note", mode="before")
	@classmethod
	def normalize_note(cls, value: str | None) -> str | None:
		return _normalize_optional_text(value)

class CashAccountUpdate(BaseModel):
	name: str = Field(min_length=1, max_length=80)
	platform: str = Field(min_length=1, max_length=80)
	currency: str = Field(default="CNY", min_length=3, max_length=8)
	balance: Decimal
	account_type: Optional[str] = Field(default=None, min_length=4, max_length=20)
	started_on: Optional[date] = None
	note: Optional[str] = Field(default=None, max_length=500)

	@field_validator("account_type", mode="before")
	@classmethod
	def validate_account_type(cls, value: str | None) -> str | None:
		return _normalize_choice(value, CASH_ACCOUNT_TYPES, "account_type")

	@field_validator("currency", mode="before")
	@classmethod
	def validate_currency(cls, value: str | None) -> str | None:
		return _normalize_choice(value, SUPPORTED_CURRENCIES, "currency")

	@field_validator("balance", mode="before")
	@classmethod
	def normalize_balance(cls, value: Any) -> Decimal:
		return _normalize_non_negative_decimal(value, "余额")

	@field_validator("note", mode="before")
	@classmethod
	def normalize_note(cls, value: str | None) -> str | None:
		return _normalize_optional_text(value)

class CashAccountRead(BaseModel):
	id: int
	name: str
	platform: str
	currency: str
	balance: Decimal
	account_type: str
	started_on: Optional[date] = None
	note: Optional[str] = None
	fx_to_cny: Optional[Decimal] = None
	value_cny: Optional[Decimal] = None

class FixedAssetBase(BaseModel):
	name: str = Field(min_length=1, max_length=120)
	category: str = Field(default="OTHER", min_length=4, max_length=24)
	current_value_cny: Decimal
	started_on: Optional[date] = None
	note: Optional[str] = Field(default=None, max_length=500)

	@field_validator("category", mode="before")
	@classmethod
	def validate_category(cls, value: str | None) -> str | None:
		return _normalize_choice(value, FIXED_ASSET_CATEGORIES, "category")

	@field_validator("current_value_cny", mode="before")
	@classmethod
	def normalize_current_value_cny(cls, value: Any) -> Decimal:
		return _normalize_positive_decimal(value, "当前价值")

	@field_validator("note", mode="before")
	@classmethod
	def normalize_note(cls, value: str | None) -> str | None:
		return _normalize_optional_text(value)

class FixedAssetCreate(FixedAssetBase):
	purchase_value_cny: Decimal | None = None

	@field_validator("purchase_value_cny", mode="before")
	@classmethod
	def normalize_purchase_value_cny(cls, value: Any) -> Decimal | None:
		return _normalize_optional_positive_decimal(value, "买入价值")

class FixedAssetUpdate(FixedAssetBase):
	purchase_value_cny: Decimal | None = None

	@field_validator("purchase_value_cny", mode="before")
	@classmethod
	def normalize_purchase_value_cny(cls, value: Any) -> Decimal | None:
		return _normalize_optional_positive_decimal(value, "买入价值")

class FixedAssetRead(BaseModel):
	id: int
	name: str
	category: str
	current_value_cny: Decimal
	purchase_value_cny: Optional[Decimal] = None
	started_on: Optional[date] = None
	note: Optional[str] = None
	value_cny: Decimal
	return_pct: Optional[Decimal] = None

class LiabilityEntryCreate(BaseModel):
	name: str = Field(min_length=1, max_length=120)
	category: str = Field(default="OTHER", min_length=4, max_length=24)
	currency: str = Field(default="CNY", min_length=3, max_length=8)
	balance: Decimal
	started_on: Optional[date] = None
	note: Optional[str] = Field(default=None, max_length=500)

	@field_validator("category", mode="before")
	@classmethod
	def validate_category(cls, value: str | None) -> str | None:
		return _normalize_choice(value, LIABILITY_CATEGORIES, "category")

	@field_validator("currency", mode="before")
	@classmethod
	def validate_currency(cls, value: str | None) -> str | None:
		return _normalize_choice(value, LIABILITY_CURRENCIES, "currency")

	@field_validator("balance", mode="before")
	@classmethod
	def normalize_balance(cls, value: Any) -> Decimal:
		return _normalize_non_negative_decimal(value, "负债余额")

	@field_validator("note", mode="before")
	@classmethod
	def normalize_note(cls, value: str | None) -> str | None:
		return _normalize_optional_text(value)

class LiabilityEntryUpdate(BaseModel):
	name: str = Field(min_length=1, max_length=120)
	category: Optional[str] = Field(default=None, min_length=4, max_length=24)
	currency: str = Field(default="CNY", min_length=3, max_length=8)
	balance: Decimal
	started_on: Optional[date] = None
	note: Optional[str] = Field(default=None, max_length=500)

	@field_validator("category", mode="before")
	@classmethod
	def validate_category(cls, value: str | None) -> str | None:
		return _normalize_choice(value, LIABILITY_CATEGORIES, "category")

	@field_validator("currency", mode="before")
	@classmethod
	def validate_currency(cls, value: str | None) -> str | None:
		return _normalize_choice(value, LIABILITY_CURRENCIES, "currency")

	@field_validator("balance", mode="before")
	@classmethod
	def normalize_balance(cls, value: Any) -> Decimal:
		return _normalize_non_negative_decimal(value, "负债余额")

	@field_validator("note", mode="before")
	@classmethod
	def normalize_note(cls, value: str | None) -> str | None:
		return _normalize_optional_text(value)

class LiabilityEntryRead(BaseModel):
	id: int
	name: str
	category: str
	currency: str
	balance: Decimal
	started_on: Optional[date] = None
	note: Optional[str] = None
	fx_to_cny: Optional[Decimal] = None
	value_cny: Optional[Decimal] = None

class OtherAssetBase(BaseModel):
	name: str = Field(min_length=1, max_length=120)
	category: str = Field(default="OTHER", min_length=4, max_length=24)
	current_value_cny: Decimal
	started_on: Optional[date] = None
	note: Optional[str] = Field(default=None, max_length=500)

	@field_validator("category", mode="before")
	@classmethod
	def validate_category(cls, value: str | None) -> str | None:
		return _normalize_choice(value, OTHER_ASSET_CATEGORIES, "category")

	@field_validator("current_value_cny", mode="before")
	@classmethod
	def normalize_current_value_cny(cls, value: Any) -> Decimal:
		return _normalize_positive_decimal(value, "当前价值")

	@field_validator("note", mode="before")
	@classmethod
	def normalize_note(cls, value: str | None) -> str | None:
		return _normalize_optional_text(value)

class OtherAssetCreate(OtherAssetBase):
	original_value_cny: Decimal | None = None

	@field_validator("original_value_cny", mode="before")
	@classmethod
	def normalize_original_value_cny(cls, value: Any) -> Decimal | None:
		return _normalize_optional_positive_decimal(value, "原始价值")

class OtherAssetUpdate(OtherAssetBase):
	original_value_cny: Decimal | None = None

	@field_validator("original_value_cny", mode="before")
	@classmethod
	def normalize_original_value_cny(cls, value: Any) -> Decimal | None:
		return _normalize_optional_positive_decimal(value, "原始价值")

class OtherAssetRead(BaseModel):
	id: int
	name: str
	category: str
	current_value_cny: Decimal
	original_value_cny: Optional[Decimal] = None
	started_on: Optional[date] = None
	note: Optional[str] = None
	value_cny: Decimal
	return_pct: Optional[Decimal] = None
