from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.fixed_precision import is_integral_decimal, quantize_decimal
from app.schemas import CashLedgerAdjustmentCreate, CashTransferCreate, SecurityHoldingTransactionCreate


def test_quantize_decimal_normalizes_float_artifacts() -> None:
	assert quantize_decimal(Decimal(str(0.1 + 0.2))) == Decimal("0.30000000")
	assert is_integral_decimal(Decimal("3.00000000")) is True
	assert is_integral_decimal(Decimal("3.50000000")) is False


def test_security_holding_transaction_create_rejects_fractional_stock_quantity() -> None:
	with pytest.raises(ValidationError, match="股票请使用整数数量"):
		SecurityHoldingTransactionCreate.model_validate(
			{
				"side": "BUY",
				"symbol": "AAPL",
				"name": "Apple",
				"quantity": "1.5",
				"price": "180.123456789",
				"fallback_currency": "USD",
				"market": "US",
				"traded_on": "2026-03-27",
			},
		)


def test_cash_amount_schemas_quantize_to_fixed_precision() -> None:
	transfer = CashTransferCreate.model_validate(
		{
			"from_account_id": 1,
			"to_account_id": 2,
			"source_amount": "100.123456789",
			"target_amount": "99.876543219",
			"transferred_on": "2026-03-27",
		},
	)
	assert transfer.source_amount == Decimal("100.12345679")
	assert transfer.target_amount == Decimal("99.87654322")

	with pytest.raises(ValidationError, match="调整金额不能为 0"):
		CashLedgerAdjustmentCreate.model_validate(
			{
				"cash_account_id": 1,
				"amount": "0.000000001",
				"happened_on": "2026-03-27",
			},
		)
