from __future__ import annotations

import asyncio
from decimal import Decimal

from app.models import (
    CashAccount,
    CashLedgerEntry,
    CashTransfer,
    FixedAsset,
    HoldingTransactionCashSettlement,
    LiabilityEntry,
    OtherAsset,
    SecurityHolding,
    SecurityHoldingTransaction,
)
from app.schemas import (
    CashAccountRead,
    CashLedgerEntryRead,
    CashTransferRead,
    LiabilityEntryRead,
    SecurityHoldingRead,
    SecurityHoldingTransactionRead,
    ValuedCashAccount,
    ValuedFixedAsset,
    ValuedHolding,
    ValuedLiabilityEntry,
    ValuedOtherAsset,
)
from app.services import service_context
from app.services.common_service import _calculate_return_pct, _normalize_currency
from app.fixed_precision import (
	DECIMAL_ZERO,
	display_fx_rate,
	display_money,
	display_price,
	display_quantity,
	multiply_decimals,
	quantize_decimal,
	to_decimal,
)
from app.services.market_data import QuoteLookupError


async def _load_display_fx_rates(
	*,
	prefer_stale_market_data: bool = False,
) -> tuple[dict[str, Decimal], Decimal | None, Decimal | None, list[str]]:
	"""Load top-level display FX rates and reuse them in dashboard valuation."""
	rates: dict[str, Decimal] = {"CNY": Decimal("1")}
	warnings: list[str] = []
	usd_cny_rate: Decimal | None = None
	hkd_cny_rate: Decimal | None = None

	async def load_rate(currency_code: str) -> tuple[str, Decimal | None, list[str]]:
		try:
			if prefer_stale_market_data:
				rate, rate_warnings = await service_context.market_data_client.fetch_fx_rate(
					currency_code,
					"CNY",
					prefer_stale=True,
					schedule_stale_refresh=False,
				)
			else:
				rate, rate_warnings = await service_context.market_data_client.fetch_fx_rate(
					currency_code,
					"CNY",
				)
		except (QuoteLookupError, ValueError) as exc:
			return currency_code, None, [f"{currency_code}/CNY 汇率拉取失败: {exc}"]
		return currency_code, rate, rate_warnings

	for currency_code, rate, rate_warnings in await asyncio.gather(
		*(load_rate(currency_code) for currency_code in ("USD", "HKD")),
	):
		if rate is None:
			warnings.extend(rate_warnings)
			continue
		normalized_rate = quantize_decimal(rate)
		rates[currency_code] = normalized_rate
		warnings.extend(rate_warnings)
		if currency_code == "USD":
			usd_cny_rate = display_fx_rate(normalized_rate)
		else:
			hkd_cny_rate = display_fx_rate(normalized_rate)

	return rates, usd_cny_rate, hkd_cny_rate, warnings

async def _value_cash_accounts(
	accounts: list[CashAccount],
	fx_rate_overrides: dict[str, Decimal] | None = None,
	*,
	prefer_stale_market_data: bool = False,
) -> tuple[list[ValuedCashAccount], Decimal, list[str]]:
	items: list[ValuedCashAccount] = []
	total = DECIMAL_ZERO
	warnings: list[str] = []

	for account in accounts:
		currency_code = _normalize_currency(account.currency)
		try:
			override_rate = fx_rate_overrides.get(currency_code) if fx_rate_overrides else None
			if override_rate is not None:
				fx_rate = override_rate
				fx_warnings: list[str] = []
			else:
				if prefer_stale_market_data:
					fx_rate, fx_warnings = await service_context.market_data_client.fetch_fx_rate(
						currency_code,
						"CNY",
						prefer_stale=True,
						schedule_stale_refresh=False,
					)
				else:
					fx_rate, fx_warnings = await service_context.market_data_client.fetch_fx_rate(
						currency_code,
						"CNY",
					)
			fx_rate = quantize_decimal(fx_rate)
			value_cny = display_money(multiply_decimals(account.balance, fx_rate))
			warnings.extend(fx_warnings)
		except (QuoteLookupError, ValueError) as exc:
			fx_rate = DECIMAL_ZERO
			value_cny = DECIMAL_ZERO
			warnings.append(f"现金账户 {account.name} 换汇失败: {exc}")

		items.append(
			ValuedCashAccount(
				id=account.id or 0,
				name=account.name,
				platform=account.platform,
				balance=display_money(account.balance),
				currency=account.currency,
				account_type=account.account_type,
				started_on=account.started_on,
				note=account.note,
				fx_to_cny=display_fx_rate(fx_rate),
				value_cny=value_cny,
			),
		)
		total += value_cny

	return items, display_money(total), warnings


async def _value_holding(
	holding: SecurityHolding,
	fx_rate_overrides: dict[str, Decimal] | None = None,
	*,
	force_pending: bool = False,
	prefer_stale_market_data: bool = False,
) -> tuple[ValuedHolding, Decimal, list[str]]:
	warnings: list[str] = []

	try:
		if prefer_stale_market_data:
			quote, quote_warnings = await service_context.market_data_client.fetch_quote(
				holding.symbol,
				holding.market,
				prefer_stale=True,
				schedule_stale_refresh=False,
			)
		else:
			quote, quote_warnings = await service_context.market_data_client.fetch_quote(
				holding.symbol,
				holding.market,
			)
		currency_code = _normalize_currency(quote.currency)
		override_rate = fx_rate_overrides.get(currency_code) if fx_rate_overrides else None
		if override_rate is not None:
			fx_rate = override_rate
			fx_warnings: list[str] = []
		else:
			if prefer_stale_market_data:
				fx_rate, fx_warnings = await service_context.market_data_client.fetch_fx_rate(
					currency_code,
					"CNY",
					prefer_stale=True,
					schedule_stale_refresh=False,
				)
			else:
				fx_rate, fx_warnings = await service_context.market_data_client.fetch_fx_rate(
					currency_code,
					"CNY",
				)
		fx_rate = quantize_decimal(fx_rate)
		price = display_price(quote.price)
		value_cny = display_money(
			multiply_decimals(
				multiply_decimals(holding.quantity, price),
				fx_rate,
			),
		)
		price_currency = currency_code
		last_updated = quote.market_time
		warnings.extend(quote_warnings)
		warnings.extend(fx_warnings)
	except (QuoteLookupError, ValueError) as exc:
		service_context.logger.warning(
			"Quote lookup still pending for %s: %s",
			holding.symbol,
			exc,
		)
		value_cny = DECIMAL_ZERO
		price = DECIMAL_ZERO
		price_currency = holding.fallback_currency
		fx_rate = DECIMAL_ZERO
		last_updated = None
		warnings.append(f"持仓 {holding.symbol} 行情更新中")

	return (
			ValuedHolding(
				id=holding.id or 0,
				symbol=holding.symbol,
				name=holding.name,
				quantity=display_quantity(holding.quantity),
				fallback_currency=holding.fallback_currency,
				cost_basis_price=display_price(holding.cost_basis_price)
				if holding.cost_basis_price is not None
				else None,
			market=holding.market,
			broker=holding.broker,
			started_on=holding.started_on,
			note=holding.note,
				price=price,
				price_currency=price_currency,
				fx_to_cny=display_fx_rate(fx_rate),
				value_cny=value_cny,
				return_pct=_calculate_return_pct(price, holding.cost_basis_price)
				if price > 0
				else None,
				last_updated=None if force_pending else last_updated,
			),
		value_cny,
		warnings,
	)


async def _value_holdings(
	holdings: list[SecurityHolding],
	fx_rate_overrides: dict[str, Decimal] | None = None,
	*,
	force_pending: bool = False,
	prefer_stale_market_data: bool = False,
) -> tuple[list[ValuedHolding], Decimal, list[str]]:
	if not holdings:
		return [], DECIMAL_ZERO, []

	valued_results = await asyncio.gather(
		*(
			_value_holding(
				holding,
				fx_rate_overrides,
				force_pending=force_pending,
				prefer_stale_market_data=prefer_stale_market_data,
			)
			for holding in holdings
		),
	)
	items = [item for item, _value_cny, _warnings in valued_results]
	total = display_money(sum((value_cny for _item, value_cny, _warnings in valued_results), DECIMAL_ZERO))
	warnings = [
		warning
		for _item, _value_cny, holding_warnings in valued_results
		for warning in holding_warnings
	]
	return items, total, warnings

def _value_fixed_assets(
	assets: list[FixedAsset],
) -> tuple[list[ValuedFixedAsset], Decimal]:
	items: list[ValuedFixedAsset] = []
	total = DECIMAL_ZERO

	for asset in assets:
		value_cny = display_money(asset.current_value_cny)
		items.append(
			ValuedFixedAsset(
				id=asset.id or 0,
				name=asset.name,
				category=asset.category,
				current_value_cny=value_cny,
				purchase_value_cny=display_money(asset.purchase_value_cny)
				if asset.purchase_value_cny is not None
				else None,
				started_on=asset.started_on,
				note=asset.note,
				value_cny=value_cny,
				return_pct=_calculate_return_pct(value_cny, asset.purchase_value_cny),
			),
		)
		total += value_cny

	return items, display_money(total)

async def _value_liabilities(
	entries: list[LiabilityEntry],
	fx_rate_overrides: dict[str, Decimal] | None = None,
	*,
	prefer_stale_market_data: bool = False,
) -> tuple[list[ValuedLiabilityEntry], Decimal, list[str]]:
	items: list[ValuedLiabilityEntry] = []
	total = DECIMAL_ZERO
	warnings: list[str] = []

	for entry in entries:
		currency_code = _normalize_currency(entry.currency)
		try:
			override_rate = fx_rate_overrides.get(currency_code) if fx_rate_overrides else None
			if override_rate is not None:
				fx_rate = override_rate
				fx_warnings: list[str] = []
			else:
				if prefer_stale_market_data:
					fx_rate, fx_warnings = await service_context.market_data_client.fetch_fx_rate(
						currency_code,
						"CNY",
						prefer_stale=True,
						schedule_stale_refresh=False,
					)
				else:
					fx_rate, fx_warnings = await service_context.market_data_client.fetch_fx_rate(
						currency_code,
						"CNY",
					)
			fx_rate = quantize_decimal(fx_rate)
			value_cny = display_money(multiply_decimals(entry.balance, fx_rate))
			warnings.extend(fx_warnings)
		except (QuoteLookupError, ValueError) as exc:
			fx_rate = DECIMAL_ZERO
			value_cny = DECIMAL_ZERO
			warnings.append(f"负债 {entry.name} 换汇失败: {exc}")

		items.append(
			ValuedLiabilityEntry(
				id=entry.id or 0,
				name=entry.name,
				category=entry.category,
				currency=entry.currency,
				balance=display_money(entry.balance),
				started_on=entry.started_on,
				note=entry.note,
				fx_to_cny=display_fx_rate(fx_rate),
				value_cny=value_cny,
			),
		)
		total += value_cny

	return items, display_money(total), warnings

def _value_other_assets(
	assets: list[OtherAsset],
) -> tuple[list[ValuedOtherAsset], Decimal]:
	items: list[ValuedOtherAsset] = []
	total = DECIMAL_ZERO

	for asset in assets:
		value_cny = display_money(asset.current_value_cny)
		items.append(
			ValuedOtherAsset(
				id=asset.id or 0,
				name=asset.name,
				category=asset.category,
				current_value_cny=value_cny,
				original_value_cny=display_money(asset.original_value_cny)
				if asset.original_value_cny is not None
				else None,
				started_on=asset.started_on,
				note=asset.note,
				value_cny=value_cny,
				return_pct=_calculate_return_pct(value_cny, asset.original_value_cny),
			),
		)
		total += value_cny

	return items, display_money(total)

def _to_cash_account_read(account: CashAccount) -> CashAccountRead:
	valued_accounts, _, _warnings = asyncio.run(_value_cash_accounts([account]))
	valued_account = valued_accounts[0] if valued_accounts else None
	return CashAccountRead(
		id=account.id or 0,
		name=account.name,
		platform=account.platform,
		currency=account.currency,
		balance=display_money(account.balance),
		account_type=account.account_type,
		started_on=account.started_on,
		note=account.note,
		fx_to_cny=valued_account.fx_to_cny if valued_account else None,
		value_cny=valued_account.value_cny if valued_account else None,
	)

def _to_cash_ledger_entry_read(entry: CashLedgerEntry) -> CashLedgerEntryRead:
	return CashLedgerEntryRead(
		id=entry.id or 0,
		cash_account_id=entry.cash_account_id,
		entry_type=entry.entry_type,
		amount=quantize_decimal(entry.amount),
		currency=entry.currency,
		happened_on=entry.happened_on,
		note=entry.note,
		holding_transaction_id=entry.holding_transaction_id,
		cash_transfer_id=entry.cash_transfer_id,
		created_at=entry.created_at,
		updated_at=entry.updated_at,
	)

def _to_cash_transfer_read(transfer: CashTransfer) -> CashTransferRead:
	return CashTransferRead(
		id=transfer.id or 0,
		from_account_id=transfer.from_account_id,
		to_account_id=transfer.to_account_id,
		source_amount=quantize_decimal(transfer.source_amount),
		target_amount=quantize_decimal(transfer.target_amount),
		source_currency=transfer.source_currency,
		target_currency=transfer.target_currency,
		transferred_on=transfer.transferred_on,
		note=transfer.note,
		created_at=transfer.created_at,
		updated_at=transfer.updated_at,
	)

def _to_holding_read(holding: SecurityHolding) -> SecurityHoldingRead:
	valued_holdings, _, _warnings = asyncio.run(_value_holdings([holding]))
	valued_holding = valued_holdings[0] if valued_holdings else None
	return SecurityHoldingRead(
		id=holding.id or 0,
		symbol=holding.symbol,
		name=holding.name,
		quantity=display_quantity(holding.quantity),
		fallback_currency=holding.fallback_currency,
		cost_basis_price=display_price(holding.cost_basis_price)
		if holding.cost_basis_price is not None
		else None,
		market=holding.market,
		broker=holding.broker,
		started_on=holding.started_on,
		note=holding.note,
		price=valued_holding.price if valued_holding else None,
		price_currency=valued_holding.price_currency if valued_holding else None,
		value_cny=valued_holding.value_cny if valued_holding else None,
		return_pct=valued_holding.return_pct if valued_holding else None,
		last_updated=valued_holding.last_updated if valued_holding else None,
	)

def _to_holding_transaction_read(
	transaction: SecurityHoldingTransaction,
	settlement: HoldingTransactionCashSettlement | None = None,
) -> SecurityHoldingTransactionRead:
	sell_proceeds_handling: str | None = None
	sell_proceeds_account_id: int | None = None
	buy_funding_handling: str | None = None
	buy_funding_account_id: int | None = None
	if settlement is not None:
		if settlement.flow_direction == "INFLOW":
			sell_proceeds_handling = settlement.handling
			sell_proceeds_account_id = settlement.cash_account_id
		elif settlement.flow_direction == "OUTFLOW":
			buy_funding_handling = settlement.handling
			buy_funding_account_id = settlement.cash_account_id

	return SecurityHoldingTransactionRead(
		id=transaction.id or 0,
		symbol=transaction.symbol,
		name=transaction.name,
		side=transaction.side,
		quantity=display_quantity(transaction.quantity),
		price=display_price(transaction.price) if transaction.price is not None else None,
		fallback_currency=transaction.fallback_currency,
		market=transaction.market,
		broker=transaction.broker,
		traded_on=transaction.traded_on,
		note=transaction.note,
		sell_proceeds_handling=sell_proceeds_handling,
		sell_proceeds_account_id=sell_proceeds_account_id,
		buy_funding_handling=buy_funding_handling,
		buy_funding_account_id=buy_funding_account_id,
		created_at=transaction.created_at,
		updated_at=transaction.updated_at,
	)

def _to_liability_read(entry: LiabilityEntry) -> LiabilityEntryRead:
	valued_entries, _, _warnings = asyncio.run(_value_liabilities([entry]))
	valued_entry = valued_entries[0] if valued_entries else None
	return LiabilityEntryRead(
		id=entry.id or 0,
		name=entry.name,
		category=entry.category,
		currency=entry.currency,
		balance=display_money(entry.balance),
		started_on=entry.started_on,
		note=entry.note,
		fx_to_cny=valued_entry.fx_to_cny if valued_entry else None,
		value_cny=valued_entry.value_cny if valued_entry else None,
	)

__all__ = ['_load_display_fx_rates', '_value_cash_accounts', '_value_holdings', '_value_fixed_assets', '_value_liabilities', '_value_other_assets', '_to_cash_account_read', '_to_cash_ledger_entry_read', '_to_cash_transfer_read', '_to_holding_read', '_to_holding_transaction_read', '_to_liability_read']
