from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from sqlmodel import Session, select

from app.fixed_precision import DECIMAL_ZERO, FixedNumber, quantize_decimal, to_decimal
from app.models import CashAccount, CashLedgerEntry
from app.services import service_context
from app.services.common_service import (
	_coerce_utc_datetime,
	_normalize_currency,
	_normalize_optional_text,
	_touch_model,
)
from app.services.market_data import QuoteLookupError
from app.services.sql_expression import sql_expr

def _prepend_note_entry(existing_note: str | None, entry: str) -> str:
	normalized_existing = _normalize_optional_text(existing_note)
	normalized_entry = entry.strip()
	combined_note = (
		normalized_entry
		if normalized_existing is None
		else f"{normalized_entry}\n{normalized_existing}"
	)
	if len(combined_note) <= 500:
		return combined_note
	return combined_note[:497].rstrip() + "..."

def _convert_cash_amount_between_currencies(
	*,
	amount: FixedNumber,
	from_currency: str,
	to_currency: str,
) -> tuple[Decimal, Decimal]:
	source_currency = _normalize_currency(from_currency)
	target_currency = _normalize_currency(to_currency)
	if source_currency == target_currency:
		return quantize_decimal(amount), Decimal("1")

	try:
		rate, _warnings = asyncio.run(
			service_context.market_data_client.fetch_fx_rate(source_currency, target_currency),
		)
	except (QuoteLookupError, ValueError) as exc:
		raise HTTPException(
			status_code=422,
			detail=f"无法将现金金额从 {source_currency} 换算为 {target_currency}: {exc}",
		) from exc

	normalized_rate = quantize_decimal(rate)
	return quantize_decimal(to_decimal(amount) * normalized_rate), normalized_rate

def _list_cash_ledger_entries_for_account(
	session: Session,
	*,
	user_id: str,
	cash_account_id: int,
) -> list[CashLedgerEntry]:
	return list(
		session.exec(
			select(CashLedgerEntry)
			.where(CashLedgerEntry.user_id == user_id)
			.where(CashLedgerEntry.cash_account_id == cash_account_id)
			.order_by(
				sql_expr(CashLedgerEntry.happened_on).asc(),
				sql_expr(CashLedgerEntry.created_at).asc(),
				sql_expr(CashLedgerEntry.id).asc(),
			),
		),
	)

def _get_cash_account_initial_ledger_entry(
	session: Session,
	*,
	user_id: str,
	cash_account_id: int,
) -> CashLedgerEntry | None:
	return session.exec(
		select(CashLedgerEntry)
		.where(CashLedgerEntry.user_id == user_id)
		.where(CashLedgerEntry.cash_account_id == cash_account_id)
		.where(CashLedgerEntry.entry_type == "INITIAL_BALANCE")
		.where(sql_expr(CashLedgerEntry.holding_transaction_id).is_(None))
		.where(sql_expr(CashLedgerEntry.cash_transfer_id).is_(None))
		.order_by(sql_expr(CashLedgerEntry.created_at).asc(), sql_expr(CashLedgerEntry.id).asc()),
	).first()

def _sum_cash_account_ledger_balance(
	session: Session,
	*,
	user_id: str,
	cash_account_id: int,
	exclude_entry_id: int | None = None,
) -> Decimal:
	entries = _list_cash_ledger_entries_for_account(
		session,
		user_id=user_id,
		cash_account_id=cash_account_id,
	)
	total = DECIMAL_ZERO
	for entry in entries:
		if exclude_entry_id is not None and entry.id == exclude_entry_id:
			continue
		total += entry.amount
	return quantize_decimal(total)

def _sync_cash_account_balance_from_ledger(
	session: Session,
	*,
	account: CashAccount,
) -> Decimal:
	account.balance = _sum_cash_account_ledger_balance(
		session,
		user_id=account.user_id,
		cash_account_id=account.id or 0,
	)
	_touch_model(account)
	session.add(account)
	session.flush()
	return account.balance

def _create_cash_ledger_entry(
	session: Session,
	*,
	user_id: str,
	cash_account_id: int,
	entry_type: str,
	amount: FixedNumber,
	currency: str,
	happened_on: date,
	note: str | None = None,
	holding_transaction_id: int | None = None,
	cash_transfer_id: int | None = None,
) -> CashLedgerEntry:
	entry = CashLedgerEntry(
		user_id=user_id,
		cash_account_id=cash_account_id,
		entry_type=entry_type,
		amount=quantize_decimal(amount),
		currency=_normalize_currency(currency),
		happened_on=happened_on,
		note=_normalize_optional_text(note),
		holding_transaction_id=holding_transaction_id,
		cash_transfer_id=cash_transfer_id,
	)
	session.add(entry)
	session.flush()
	return entry

def _reconcile_cash_account_initial_ledger_entry(
	session: Session,
	*,
	account: CashAccount,
	target_balance: FixedNumber,
) -> CashLedgerEntry:
	initial_entry = _get_cash_account_initial_ledger_entry(
		session,
		user_id=account.user_id,
		cash_account_id=account.id or 0,
	)
	non_initial_total = _sum_cash_account_ledger_balance(
		session,
		user_id=account.user_id,
		cash_account_id=account.id or 0,
		exclude_entry_id=initial_entry.id if initial_entry is not None else None,
	)
	started_on = account.started_on or _coerce_utc_datetime(account.created_at).date()
	required_initial_amount = quantize_decimal(to_decimal(target_balance) - non_initial_total)
	if initial_entry is None:
		initial_entry = CashLedgerEntry(
			user_id=account.user_id,
			cash_account_id=account.id or 0,
			entry_type="INITIAL_BALANCE",
			amount=required_initial_amount,
			currency=_normalize_currency(account.currency),
			happened_on=started_on,
			note="账户初始余额",
		)
	else:
		initial_entry.amount = required_initial_amount
		initial_entry.currency = _normalize_currency(account.currency)
		initial_entry.happened_on = started_on
		initial_entry.note = "账户初始余额"
		_touch_model(initial_entry)
	session.add(initial_entry)
	session.flush()
	_sync_cash_account_balance_from_ledger(session, account=account)
	return initial_entry

def _delete_cash_ledger_entries_for_holding_transaction(
	session: Session,
	*,
	user_id: str,
	holding_transaction_id: int,
) -> list[CashLedgerEntry]:
	entries = list(
		session.exec(
			select(CashLedgerEntry)
			.where(CashLedgerEntry.user_id == user_id)
			.where(CashLedgerEntry.holding_transaction_id == holding_transaction_id),
		),
	)
	for entry in entries:
		session.delete(entry)
	return entries

def _delete_cash_ledger_entries_for_transfer(
	session: Session,
	*,
	user_id: str,
	cash_transfer_id: int,
) -> list[CashLedgerEntry]:
	entries = list(
		session.exec(
			select(CashLedgerEntry)
			.where(CashLedgerEntry.user_id == user_id)
			.where(CashLedgerEntry.cash_transfer_id == cash_transfer_id),
		),
	)
	for entry in entries:
		session.delete(entry)
	return entries

def _get_manual_cash_ledger_adjustment(
	session: Session,
	*,
	user_id: str,
	entry_id: int,
) -> CashLedgerEntry:
	entry = session.get(CashLedgerEntry, entry_id)
	if entry is None or entry.user_id != user_id:
		raise HTTPException(status_code=404, detail="Cash ledger adjustment not found.")
	if entry.entry_type != "MANUAL_ADJUSTMENT":
		raise HTTPException(status_code=422, detail="只有手工账本调整允许直接编辑。")
	if entry.holding_transaction_id is not None or entry.cash_transfer_id is not None:
		raise HTTPException(status_code=422, detail="该账本记录由系统生成，不能直接修改。")
	return entry
