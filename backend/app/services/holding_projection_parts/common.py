from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.fixed_precision import FIXED_EPSILON
from app.models import CashAccount

HOLDING_QUANTITY_EPSILON = FIXED_EPSILON

class AppliedCashSettlement:
	cash_account: CashAccount
	settled_amount: Decimal
	settled_currency: str
	handling: str
	flow_direction: str
	ledger_entry_type: str
	auto_created_cash_account: bool

@dataclass(slots=True)

class HoldingLot:
	quantity: Decimal
	traded_on: date
	cost_per_unit: Decimal | None

@dataclass(slots=True)

class ProjectedHoldingState:
	symbol: str
	name: str
	market: str
	fallback_currency: str
	broker: str | None
	note: str | None
	lots: list[HoldingLot]
