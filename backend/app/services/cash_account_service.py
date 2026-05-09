from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any

from fastapi import Header, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import or_
from sqlmodel import select

from app.models import (
	AssetMutationAudit,
	CashAccount,
	CashLedgerEntry,
	CashTransfer,
	HoldingTransactionCashSettlement,
)
from app.schemas import (
	AssetMutationAuditRead,
	CashAccountCreate,
	CashAccountRead,
	CashAccountUpdate,
	CashLedgerAdjustmentApplyRead,
	CashLedgerAdjustmentCreate,
	CashLedgerAdjustmentUpdate,
	CashLedgerEntryRead,
	CashTransferApplyRead,
	CashTransferCreate,
	CashTransferRead,
	CashTransferUpdate,
)
from app.services import job_service
from app.services.auth_service import CurrentUserDependency
from app.services.common_service import (
	_build_idempotency_request_hash,
	_capture_model_state,
	_ensure_date_not_future,
	_invalidate_dashboard_cache,
	_load_idempotent_response,
	_normalize_currency,
	_normalize_optional_text,
	_record_asset_mutation,
	_store_idempotent_response,
	_to_asset_mutation_audit_read,
	_touch_model,
)
from app.fixed_precision import (
	DECIMAL_ZERO,
	FIXED_EPSILON,
	FixedNumber,
	decimal_to_float,
	display_money,
	is_effectively_zero,
	quantize_decimal,
	to_decimal,
)
from app.services.holding_projection_service import (
	_convert_cash_amount_between_currencies,
	_create_cash_ledger_entry,
	_delete_cash_ledger_entries_for_holding_transaction,
	_delete_cash_ledger_entries_for_transfer,
	_get_manual_cash_ledger_adjustment,
	_list_cash_ledger_entries_for_account,
	_reconcile_cash_account_initial_ledger_entry,
	_sync_cash_account_balance_from_ledger,
)
from app.services.portfolio_read_service import (
	_to_cash_account_read,
	_to_cash_ledger_entry_read,
	_to_cash_transfer_read,
)
from app.services.service_context import SessionDependency
from app.services.sql_expression import sql_expr


def _delete_cash_account_related_transfers(
	session: SessionDependency,
	*,
	current_user: CurrentUserDependency,
	account: CashAccount,
) -> tuple[set[int], list[int]]:
	related_transfers = list(
		session.exec(
			select(CashTransfer)
			.where(CashTransfer.user_id == current_user.username)
			.where(
				or_(
					sql_expr(CashTransfer.from_account_id) == (account.id or 0),
					sql_expr(CashTransfer.to_account_id) == (account.id or 0),
				),
			),
		),
	)
	affected_other_account_ids: set[int] = set()
	deleted_transfer_ids: list[int] = []

	for transfer in related_transfers:
		if transfer.from_account_id != (account.id or 0):
			affected_other_account_ids.add(transfer.from_account_id)
		if transfer.to_account_id != (account.id or 0):
			affected_other_account_ids.add(transfer.to_account_id)

		_delete_cash_ledger_entries_for_transfer(
			session,
			user_id=current_user.username,
			cash_transfer_id=transfer.id or 0,
		)
		deleted_transfer_ids.append(transfer.id or 0)
		session.delete(transfer)

	return affected_other_account_ids, deleted_transfer_ids


def _delete_cash_account_related_settlements(
	session: SessionDependency,
	*,
	current_user: CurrentUserDependency,
	account: CashAccount,
) -> list[int]:
	settlements = list(
		session.exec(
			select(HoldingTransactionCashSettlement)
			.where(HoldingTransactionCashSettlement.user_id == current_user.username)
			.where(HoldingTransactionCashSettlement.cash_account_id == (account.id or 0)),
		),
	)
	deleted_transaction_ids: list[int] = []

	for settlement in settlements:
		_delete_cash_ledger_entries_for_holding_transaction(
			session,
			user_id=current_user.username,
			holding_transaction_id=settlement.holding_transaction_id,
		)
		deleted_transaction_ids.append(settlement.holding_transaction_id)
		session.delete(settlement)

	return deleted_transaction_ids


def list_asset_mutation_audits(
	current_user: CurrentUserDependency,
	session: SessionDependency,
	limit: int = 200,
	agent_task_id: int | None = None,
) -> list[AssetMutationAuditRead]:
	clamped_limit = max(1, min(limit, 500))
	if agent_task_id is not None and agent_task_id <= 0:
		raise HTTPException(status_code=422, detail="agent_task_id 必须是正整数。")
	statement = (
		select(AssetMutationAudit)
		.where(AssetMutationAudit.user_id == current_user.username)
		.order_by(sql_expr(AssetMutationAudit.created_at).desc())
		.limit(clamped_limit)
	)
	if agent_task_id is not None:
		statement = statement.where(AssetMutationAudit.agent_task_id == agent_task_id)
	rows = list(
		session.exec(statement),
	)
	return [_to_asset_mutation_audit_read(row) for row in rows]

async def list_accounts(
	current_user: CurrentUserDependency,
	session: SessionDependency,
) -> list[CashAccountRead]:
	from app.services.dashboard_query_service import _get_cached_dashboard

	dashboard = await _get_cached_dashboard(session, current_user)
	accounts = list(
		session.exec(
			select(CashAccount)
			.where(CashAccount.user_id == current_user.username)
			.order_by(CashAccount.platform, CashAccount.name),
		),
	)
	valued_account_map = {account.id: account for account in dashboard.cash_accounts}
	items: list[CashAccountRead] = []

	for account in accounts:
		valued_account = valued_account_map.get(account.id or 0)
		items.append(
			CashAccountRead(
				id=account.id or 0,
				name=account.name,
				platform=account.platform,
				currency=account.currency,
				balance=account.balance,
				account_type=account.account_type,
				started_on=account.started_on,
				note=account.note,
				fx_to_cny=valued_account.fx_to_cny if valued_account else None,
				value_cny=valued_account.value_cny if valued_account else None,
			),
		)

	return items


def _resolve_cash_transfer_target_amount(
	*,
	source_amount: FixedNumber,
	source_currency: str,
	target_currency: str,
	provided_target_amount: FixedNumber | None,
) -> Decimal:
	expected_target_amount, _fx_rate = _convert_cash_amount_between_currencies(
		amount=source_amount,
		from_currency=source_currency,
		to_currency=target_currency,
	)
	if provided_target_amount is None:
		return expected_target_amount

	normalized_target_amount = quantize_decimal(provided_target_amount)
	if abs(normalized_target_amount - expected_target_amount) > FIXED_EPSILON:
		raise HTTPException(
			status_code=422,
			detail=(
				f"目标币种金额必须按当前汇率自动换算为 {target_currency}，"
				f"当前应为 {decimal_to_float(display_money(expected_target_amount)):g} {target_currency}。"
			),
		)

	return normalized_target_amount

def create_account(
	payload: CashAccountCreate,
	current_user: CurrentUserDependency,
	session: SessionDependency,
) -> CashAccountRead:
	account = CashAccount(
		user_id=current_user.username,
		name=payload.name.strip(),
		platform=payload.platform.strip(),
		currency=_normalize_currency(payload.currency),
		balance=DECIMAL_ZERO,
		account_type=payload.account_type,
		started_on=payload.started_on,
		note=payload.note,
	)
	session.add(account)
	session.flush()
	_reconcile_cash_account_initial_ledger_entry(
		session,
		account=account,
		target_balance=payload.balance,
	)
	_record_asset_mutation(
		session,
		current_user,
		entity_type="CASH_ACCOUNT",
		entity_id=account.id,
		operation="CREATE",
		before_state=None,
		after_state=_capture_model_state(account),
	)
	job_service.enqueue_user_portfolio_snapshot_rebuild(session, current_user.username)
	session.commit()
	session.refresh(account)
	_invalidate_dashboard_cache(current_user.username)
	return _to_cash_account_read(account)

def update_account(
	account_id: int,
	payload: CashAccountUpdate,
	current_user: CurrentUserDependency,
	session: SessionDependency,
) -> CashAccountRead:
	account = session.get(CashAccount, account_id)
	if account is None or account.user_id != current_user.username:
		raise HTTPException(status_code=404, detail="Account not found.")

	before_state = _capture_model_state(account)
	existing_non_initial_entries = [
		entry
		for entry in _list_cash_ledger_entries_for_account(
			session,
			user_id=current_user.username,
			cash_account_id=account.id or 0,
		)
		if entry.entry_type != "INITIAL_BALANCE"
	]
	next_currency = _normalize_currency(payload.currency)
	if existing_non_initial_entries and next_currency != _normalize_currency(account.currency):
		raise HTTPException(
			status_code=409,
			detail="该现金账户已有交易流水，暂不支持直接修改币种。",
		)

	account.name = payload.name.strip()
	account.platform = payload.platform.strip()
	account.currency = next_currency
	if payload.account_type is not None:
		account.account_type = payload.account_type
	if "started_on" in payload.model_fields_set:
		account.started_on = payload.started_on
	if "note" in payload.model_fields_set:
		account.note = _normalize_optional_text(payload.note)
	_touch_model(account)
	session.add(account)
	_reconcile_cash_account_initial_ledger_entry(
		session,
		account=account,
		target_balance=payload.balance,
	)
	_record_asset_mutation(
		session,
		current_user,
		entity_type="CASH_ACCOUNT",
		entity_id=account.id,
		operation="UPDATE",
		before_state=before_state,
		after_state=_capture_model_state(account),
	)
	job_service.enqueue_user_portfolio_snapshot_rebuild(session, current_user.username)
	session.commit()
	session.refresh(account)
	_invalidate_dashboard_cache(current_user.username)
	return _to_cash_account_read(account)

def delete_account(
	account_id: int,
	current_user: CurrentUserDependency,
	session: SessionDependency,
) -> Response:
	account = session.get(CashAccount, account_id)
	if account is None or account.user_id != current_user.username:
		raise HTTPException(status_code=404, detail="Account not found.")

	before_state = _capture_model_state(account)
	affected_other_account_ids, deleted_transfer_ids = _delete_cash_account_related_transfers(
		session,
		current_user=current_user,
		account=account,
	)
	deleted_transaction_ids = _delete_cash_account_related_settlements(
		session,
		current_user=current_user,
		account=account,
	)
	for affected_account_id in affected_other_account_ids:
		affected_account = session.get(CashAccount, affected_account_id)
		if affected_account is None or affected_account.user_id != current_user.username:
			continue
		_sync_cash_account_balance_from_ledger(session, account=affected_account)
	for entry in _list_cash_ledger_entries_for_account(
		session,
		user_id=current_user.username,
		cash_account_id=account.id or 0,
	):
		session.delete(entry)
	session.delete(account)
	_record_asset_mutation(
		session,
		current_user,
		entity_type="CASH_ACCOUNT",
		entity_id=account_id,
		operation="DELETE",
		before_state=before_state,
		after_state=None,
	)
	for affected_account_id in affected_other_account_ids:
		affected_account = session.get(CashAccount, affected_account_id)
		if affected_account is None or affected_account.user_id != current_user.username:
			continue
		_record_asset_mutation(
			session,
			current_user,
			entity_type="CASH_ACCOUNT",
			entity_id=affected_account.id,
			operation="UPDATE",
			before_state=None,
			after_state=_capture_model_state(affected_account),
			reason=f"CASH_ACCOUNT_DELETE#{account_id}",
		)
	for deleted_transfer_id in deleted_transfer_ids:
		_record_asset_mutation(
			session,
			current_user,
			entity_type="CASH_TRANSFER",
			entity_id=deleted_transfer_id,
			operation="DELETE",
			before_state=None,
			after_state=None,
			reason=f"CASH_ACCOUNT_DELETE#{account_id}",
		)
	for deleted_transaction_id in deleted_transaction_ids:
		_record_asset_mutation(
			session,
			current_user,
			entity_type="HOLDING_CASH_SETTLEMENT",
			entity_id=deleted_transaction_id,
			operation="DELETE",
			before_state=None,
			after_state=None,
			reason=f"CASH_ACCOUNT_DELETE#{account_id}",
		)
	job_service.enqueue_user_portfolio_snapshot_rebuild(session, current_user.username)
	session.commit()
	_invalidate_dashboard_cache(current_user.username)
	return Response(status_code=204)

def list_cash_ledger_entries(
	current_user: CurrentUserDependency,
	session: SessionDependency,
	account_id: int | None = Query(default=None, ge=1),
	limit: int = Query(default=200, ge=1, le=1000),
) -> list[CashLedgerEntryRead]:
	statement = (
		select(CashLedgerEntry)
		.where(CashLedgerEntry.user_id == current_user.username)
		.order_by(
			sql_expr(CashLedgerEntry.happened_on).desc(),
			sql_expr(CashLedgerEntry.created_at).desc(),
			sql_expr(CashLedgerEntry.id).desc(),
		)
		.limit(limit)
	)
	if account_id is not None:
		account = session.get(CashAccount, account_id)
		if account is None or account.user_id != current_user.username:
			raise HTTPException(status_code=404, detail="Account not found.")
		statement = statement.where(CashLedgerEntry.cash_account_id == account_id)

	entries = list(session.exec(statement))
	return [_to_cash_ledger_entry_read(entry) for entry in entries]

def create_cash_ledger_adjustment(
	payload: CashLedgerAdjustmentCreate,
	current_user: CurrentUserDependency,
	session: SessionDependency,
	idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> CashLedgerAdjustmentApplyRead:
	request_hash = _build_idempotency_request_hash(payload)
	idempotent_response = _load_idempotent_response(
		session,
		user_id=current_user.username,
		scope="cash_ledger_adjustment.create",
		idempotency_key=idempotency_key,
		request_hash=request_hash,
		response_model=CashLedgerAdjustmentApplyRead,
	)
	if idempotent_response is not None:
		return idempotent_response

	account = session.get(CashAccount, payload.cash_account_id)
	if account is None or account.user_id != current_user.username:
		raise HTTPException(status_code=404, detail="现金账户不存在。")
	_ensure_date_not_future(payload.happened_on, field_label="账本调整日")

	account_before_state = _capture_model_state(account)
	entry = _create_cash_ledger_entry(
		session,
		user_id=current_user.username,
		cash_account_id=account.id or 0,
		entry_type="MANUAL_ADJUSTMENT",
		amount=payload.amount,
		currency=account.currency,
		happened_on=payload.happened_on,
		note=payload.note or "手工账本调整",
	)
	_sync_cash_account_balance_from_ledger(session, account=account)
	_record_asset_mutation(
		session,
		current_user,
		entity_type="CASH_LEDGER_ADJUSTMENT",
		entity_id=entry.id,
		operation="CREATE",
		before_state=None,
		after_state=_capture_model_state(entry),
		reason=f"CASH_ACCOUNT#{account.id}",
	)
	_record_asset_mutation(
		session,
		current_user,
		entity_type="CASH_ACCOUNT",
		entity_id=account.id,
		operation="UPDATE",
		before_state=account_before_state,
		after_state=_capture_model_state(account),
		reason=f"LEDGER_ADJUSTMENT_CREATE#{entry.id}",
	)
	job_service.enqueue_user_portfolio_snapshot_rebuild(session, current_user.username)
	response = CashLedgerAdjustmentApplyRead(
		entry=_to_cash_ledger_entry_read(entry),
		account=_to_cash_account_read(account),
	)
	_store_idempotent_response(
		session,
		user_id=current_user.username,
		scope="cash_ledger_adjustment.create",
		idempotency_key=idempotency_key,
		request_hash=request_hash,
		response=response,
	)
	session.commit()
	session.refresh(entry)
	session.refresh(account)
	_invalidate_dashboard_cache(current_user.username)
	return response

def update_cash_ledger_adjustment(
	entry_id: int,
	payload: CashLedgerAdjustmentUpdate,
	current_user: CurrentUserDependency,
	session: SessionDependency,
) -> CashLedgerAdjustmentApplyRead:
	entry = _get_manual_cash_ledger_adjustment(
		session,
		user_id=current_user.username,
		entry_id=entry_id,
	)
	account = session.get(CashAccount, entry.cash_account_id)
	if account is None or account.user_id != current_user.username:
		raise HTTPException(status_code=404, detail="现金账户不存在。")

	fields_set = payload.model_fields_set
	if not fields_set:
		return CashLedgerAdjustmentApplyRead(
			entry=_to_cash_ledger_entry_read(entry),
			account=_to_cash_account_read(account),
		)

	entry_before_state = _capture_model_state(entry)
	account_before_state = _capture_model_state(account)
	if payload.amount is not None:
		entry.amount = quantize_decimal(payload.amount)
	if payload.happened_on is not None:
		_ensure_date_not_future(payload.happened_on, field_label="账本调整日")
		entry.happened_on = payload.happened_on
	if "note" in fields_set:
		entry.note = payload.note or "手工账本调整"
	entry.currency = _normalize_currency(account.currency)
	_touch_model(entry)
	session.add(entry)
	session.flush()
	_sync_cash_account_balance_from_ledger(session, account=account)
	_record_asset_mutation(
		session,
		current_user,
		entity_type="CASH_LEDGER_ADJUSTMENT",
		entity_id=entry.id,
		operation="UPDATE",
		before_state=entry_before_state,
		after_state=_capture_model_state(entry),
		reason=f"CASH_ACCOUNT#{account.id}",
	)
	_record_asset_mutation(
		session,
		current_user,
		entity_type="CASH_ACCOUNT",
		entity_id=account.id,
		operation="UPDATE",
		before_state=account_before_state,
		after_state=_capture_model_state(account),
		reason=f"LEDGER_ADJUSTMENT_UPDATE#{entry.id}",
	)
	job_service.enqueue_user_portfolio_snapshot_rebuild(session, current_user.username)
	response = CashLedgerAdjustmentApplyRead(
		entry=_to_cash_ledger_entry_read(entry),
		account=_to_cash_account_read(account),
	)
	session.commit()
	session.refresh(entry)
	session.refresh(account)
	_invalidate_dashboard_cache(current_user.username)
	return response

def delete_cash_ledger_adjustment(
	entry_id: int,
	current_user: CurrentUserDependency,
	session: SessionDependency,
) -> Response:
	entry = _get_manual_cash_ledger_adjustment(
		session,
		user_id=current_user.username,
		entry_id=entry_id,
	)
	account = session.get(CashAccount, entry.cash_account_id)
	if account is None or account.user_id != current_user.username:
		raise HTTPException(status_code=404, detail="现金账户不存在。")

	entry_before_state = _capture_model_state(entry)
	account_before_state = _capture_model_state(account)
	session.delete(entry)
	_sync_cash_account_balance_from_ledger(session, account=account)
	_record_asset_mutation(
		session,
		current_user,
		entity_type="CASH_LEDGER_ADJUSTMENT",
		entity_id=entry_id,
		operation="DELETE",
		before_state=entry_before_state,
		after_state=None,
		reason=f"CASH_ACCOUNT#{account.id}",
	)
	_record_asset_mutation(
		session,
		current_user,
		entity_type="CASH_ACCOUNT",
		entity_id=account.id,
		operation="UPDATE",
		before_state=account_before_state,
		after_state=_capture_model_state(account),
		reason=f"LEDGER_ADJUSTMENT_DELETE#{entry_id}",
	)
	job_service.enqueue_user_portfolio_snapshot_rebuild(session, current_user.username)
	session.commit()
	_invalidate_dashboard_cache(current_user.username)
	return Response(status_code=204)

def list_cash_transfers(
	current_user: CurrentUserDependency,
	session: SessionDependency,
	limit: int = Query(default=100, ge=1, le=500),
) -> list[CashTransferRead]:
	transfers = list(
		session.exec(
			select(CashTransfer)
			.where(CashTransfer.user_id == current_user.username)
			.order_by(
				sql_expr(CashTransfer.transferred_on).desc(),
				sql_expr(CashTransfer.created_at).desc(),
				sql_expr(CashTransfer.id).desc(),
			)
			.limit(limit),
		),
	)
	return [_to_cash_transfer_read(transfer) for transfer in transfers]

def create_cash_transfer(
	payload: CashTransferCreate,
	current_user: CurrentUserDependency,
	session: SessionDependency,
	idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> CashTransferApplyRead:
	request_hash = _build_idempotency_request_hash(payload)
	idempotent_response = _load_idempotent_response(
		session,
		user_id=current_user.username,
		scope="cash_transfer.create",
		idempotency_key=idempotency_key,
		request_hash=request_hash,
		response_model=CashTransferApplyRead,
	)
	if idempotent_response is not None:
		return idempotent_response

	_ensure_date_not_future(payload.transferred_on, field_label="划转日")
	source_account = session.get(CashAccount, payload.from_account_id)
	target_account = session.get(CashAccount, payload.to_account_id)
	if source_account is None or source_account.user_id != current_user.username:
		raise HTTPException(status_code=404, detail="转出账户不存在。")
	if target_account is None or target_account.user_id != current_user.username:
		raise HTTPException(status_code=404, detail="转入账户不存在。")
	if source_account.id == target_account.id:
		raise HTTPException(status_code=422, detail="转出账户和转入账户不能相同。")
	if _normalize_currency(target_account.currency) != "CNY":
		raise HTTPException(status_code=422, detail="转入账户必须是 CNY 现金账户。")
	source_amount = quantize_decimal(payload.source_amount)
	if source_account.balance + FIXED_EPSILON < source_amount:
		raise HTTPException(
			status_code=422,
			detail=(
				f"{source_account.name} 余额不足。当前余额 {decimal_to_float(display_money(source_account.balance)):g} "
				f"{source_account.currency}，本次转出 {decimal_to_float(display_money(source_amount)):g} {source_account.currency}。"
			),
		)

	target_amount = _resolve_cash_transfer_target_amount(
		source_amount=source_amount,
		source_currency=source_account.currency,
		target_currency=target_account.currency,
		provided_target_amount=payload.target_amount,
	)

	transfer = CashTransfer(
		user_id=current_user.username,
		from_account_id=source_account.id or 0,
		to_account_id=target_account.id or 0,
		source_amount=source_amount,
		target_amount=target_amount,
		source_currency=_normalize_currency(source_account.currency),
		target_currency=_normalize_currency(target_account.currency),
		transferred_on=payload.transferred_on,
		note=payload.note,
	)
	session.add(transfer)
	session.flush()
	_create_cash_ledger_entry(
		session,
		user_id=current_user.username,
		cash_account_id=source_account.id or 0,
		entry_type="TRANSFER_OUT",
		amount=-source_amount,
		currency=source_account.currency,
		happened_on=payload.transferred_on,
		note=payload.note or f"划转至 {target_account.name}",
		cash_transfer_id=transfer.id,
	)
	_create_cash_ledger_entry(
		session,
		user_id=current_user.username,
		cash_account_id=target_account.id or 0,
		entry_type="TRANSFER_IN",
		amount=target_amount,
		currency=target_account.currency,
		happened_on=payload.transferred_on,
		note=payload.note or f"来自 {source_account.name} 的划转",
		cash_transfer_id=transfer.id,
	)
	source_before_state = _capture_model_state(source_account)
	target_before_state = _capture_model_state(target_account)
	_sync_cash_account_balance_from_ledger(session, account=source_account)
	_sync_cash_account_balance_from_ledger(session, account=target_account)
	_record_asset_mutation(
		session,
		current_user,
		entity_type="CASH_TRANSFER",
		entity_id=transfer.id,
		operation="CREATE",
		before_state=None,
		after_state=_capture_model_state(transfer),
	)
	_record_asset_mutation(
		session,
		current_user,
		entity_type="CASH_ACCOUNT",
		entity_id=source_account.id,
		operation="UPDATE",
		before_state=source_before_state,
		after_state=_capture_model_state(source_account),
		reason=f"TRANSFER_OUT#{transfer.id}",
	)
	_record_asset_mutation(
		session,
		current_user,
		entity_type="CASH_ACCOUNT",
		entity_id=target_account.id,
		operation="UPDATE",
		before_state=target_before_state,
		after_state=_capture_model_state(target_account),
		reason=f"TRANSFER_IN#{transfer.id}",
	)
	job_service.enqueue_user_portfolio_snapshot_rebuild(session, current_user.username)
	response = CashTransferApplyRead(
		transfer=_to_cash_transfer_read(transfer),
		from_account=_to_cash_account_read(source_account),
		to_account=_to_cash_account_read(target_account),
	)
	_store_idempotent_response(
		session,
		user_id=current_user.username,
		scope="cash_transfer.create",
		idempotency_key=idempotency_key,
		request_hash=request_hash,
		response=response,
	)
	session.commit()
	session.refresh(transfer)
	session.refresh(source_account)
	session.refresh(target_account)
	_invalidate_dashboard_cache(current_user.username)
	return response

def update_cash_transfer(
	transfer_id: int,
	payload: CashTransferUpdate,
	current_user: CurrentUserDependency,
	session: SessionDependency,
) -> CashTransferApplyRead:
	transfer = session.get(CashTransfer, transfer_id)
	if transfer is None or transfer.user_id != current_user.username:
		raise HTTPException(status_code=404, detail="Cash transfer not found.")

	fields_set = payload.model_fields_set
	if not fields_set:
		source_account = session.get(CashAccount, transfer.from_account_id)
		target_account = session.get(CashAccount, transfer.to_account_id)
		if source_account is None or target_account is None:
			raise HTTPException(status_code=404, detail="账户不存在。")
		return CashTransferApplyRead(
			transfer=_to_cash_transfer_read(transfer),
			from_account=_to_cash_account_read(source_account),
			to_account=_to_cash_account_read(target_account),
		)

	current_source_account = session.get(CashAccount, transfer.from_account_id)
	current_target_account = session.get(CashAccount, transfer.to_account_id)
	if current_source_account is None or current_source_account.user_id != current_user.username:
		raise HTTPException(status_code=404, detail="原转出账户不存在。")
	if current_target_account is None or current_target_account.user_id != current_user.username:
		raise HTTPException(status_code=404, detail="原转入账户不存在。")

	account_before_state_map: dict[int, dict[str, Any]] = {}
	for account in (current_source_account, current_target_account):
		account_id = account.id or 0
		if account_id not in account_before_state_map:
			account_before_state_map[account_id] = _capture_model_state(account)

	transfer_before_state = _capture_model_state(transfer)
	_delete_cash_ledger_entries_for_transfer(
		session,
		user_id=current_user.username,
		cash_transfer_id=transfer.id or 0,
	)
	_sync_cash_account_balance_from_ledger(session, account=current_source_account)
	if current_target_account.id != current_source_account.id:
		_sync_cash_account_balance_from_ledger(session, account=current_target_account)

	next_from_account_id = payload.from_account_id or transfer.from_account_id
	next_to_account_id = payload.to_account_id or transfer.to_account_id
	if next_from_account_id == next_to_account_id:
		raise HTTPException(status_code=422, detail="转出账户和转入账户不能相同。")

	source_account = session.get(CashAccount, next_from_account_id)
	target_account = session.get(CashAccount, next_to_account_id)
	if source_account is None or source_account.user_id != current_user.username:
		raise HTTPException(status_code=404, detail="转出账户不存在。")
	if target_account is None or target_account.user_id != current_user.username:
		raise HTTPException(status_code=404, detail="转入账户不存在。")
	if _normalize_currency(target_account.currency) != "CNY":
		raise HTTPException(status_code=422, detail="转入账户必须是 CNY 现金账户。")

	for account in (source_account, target_account):
		account_id = account.id or 0
		if account_id not in account_before_state_map:
			account_before_state_map[account_id] = _capture_model_state(account)

	source_amount = (
		quantize_decimal(payload.source_amount)
		if payload.source_amount is not None
		else transfer.source_amount
	)
	transferred_on = payload.transferred_on or transfer.transferred_on
	if "note" in fields_set:
		note = payload.note
	else:
		note = transfer.note
	_ensure_date_not_future(transferred_on, field_label="划转日")

	if source_account.balance + FIXED_EPSILON < source_amount:
		raise HTTPException(
			status_code=422,
			detail=(
				f"{source_account.name} 余额不足。当前余额 {decimal_to_float(display_money(source_account.balance)):g} "
				f"{source_account.currency}，本次转出 {decimal_to_float(display_money(source_amount)):g} {source_account.currency}。"
			),
		)

	if "target_amount" in fields_set:
		provided_target_amount = payload.target_amount
	elif {"from_account_id", "to_account_id", "source_amount"} & fields_set:
		provided_target_amount = None
	else:
		provided_target_amount = transfer.target_amount

	target_amount = _resolve_cash_transfer_target_amount(
		source_amount=source_amount,
		source_currency=source_account.currency,
		target_currency=target_account.currency,
		provided_target_amount=provided_target_amount,
	)

	transfer.from_account_id = source_account.id or 0
	transfer.to_account_id = target_account.id or 0
	transfer.source_amount = source_amount
	transfer.target_amount = target_amount
	transfer.source_currency = _normalize_currency(source_account.currency)
	transfer.target_currency = _normalize_currency(target_account.currency)
	transfer.transferred_on = transferred_on
	transfer.note = note
	_touch_model(transfer)
	session.add(transfer)
	session.flush()

	_create_cash_ledger_entry(
		session,
		user_id=current_user.username,
		cash_account_id=source_account.id or 0,
		entry_type="TRANSFER_OUT",
		amount=-source_amount,
		currency=source_account.currency,
		happened_on=transferred_on,
		note=note or f"划转至 {target_account.name}",
		cash_transfer_id=transfer.id,
	)
	_create_cash_ledger_entry(
		session,
		user_id=current_user.username,
		cash_account_id=target_account.id or 0,
		entry_type="TRANSFER_IN",
		amount=target_amount,
		currency=target_account.currency,
		happened_on=transferred_on,
		note=note or f"来自 {source_account.name} 的划转",
		cash_transfer_id=transfer.id,
	)
	_sync_cash_account_balance_from_ledger(session, account=source_account)
	if target_account.id != source_account.id:
		_sync_cash_account_balance_from_ledger(session, account=target_account)

	_record_asset_mutation(
		session,
		current_user,
		entity_type="CASH_TRANSFER",
		entity_id=transfer.id,
		operation="UPDATE",
		before_state=transfer_before_state,
		after_state=_capture_model_state(transfer),
		reason="TRANSFER_EDIT",
	)
	for account_id, before_state in account_before_state_map.items():
		account = session.get(CashAccount, account_id)
		if account is None or account.user_id != current_user.username:
			continue
		_record_asset_mutation(
			session,
			current_user,
			entity_type="CASH_ACCOUNT",
			entity_id=account.id,
			operation="UPDATE",
			before_state=before_state,
			after_state=_capture_model_state(account),
			reason=f"TRANSFER_UPDATE#{transfer.id}",
		)

	job_service.enqueue_user_portfolio_snapshot_rebuild(session, current_user.username)
	response = CashTransferApplyRead(
		transfer=_to_cash_transfer_read(transfer),
		from_account=_to_cash_account_read(source_account),
		to_account=_to_cash_account_read(target_account),
	)
	session.commit()
	session.refresh(transfer)
	session.refresh(source_account)
	session.refresh(target_account)
	_invalidate_dashboard_cache(current_user.username)
	return response

def delete_cash_transfer(
	transfer_id: int,
	current_user: CurrentUserDependency,
	session: SessionDependency,
) -> Response:
	transfer = session.get(CashTransfer, transfer_id)
	if transfer is None or transfer.user_id != current_user.username:
		raise HTTPException(status_code=404, detail="Cash transfer not found.")

	source_account = session.get(CashAccount, transfer.from_account_id)
	target_account = session.get(CashAccount, transfer.to_account_id)
	source_before_state = _capture_model_state(source_account) if source_account is not None else None
	target_before_state = _capture_model_state(target_account) if target_account is not None else None
	_delete_cash_ledger_entries_for_transfer(
		session,
		user_id=current_user.username,
		cash_transfer_id=transfer.id or 0,
	)
	session.delete(transfer)
	if source_account is not None and source_account.user_id == current_user.username:
		_sync_cash_account_balance_from_ledger(session, account=source_account)
	if target_account is not None and target_account.user_id == current_user.username:
		_sync_cash_account_balance_from_ledger(session, account=target_account)
	_record_asset_mutation(
		session,
		current_user,
		entity_type="CASH_TRANSFER",
		entity_id=transfer_id,
		operation="DELETE",
		before_state=_capture_model_state(transfer),
		after_state=None,
	)
	if source_account is not None and source_before_state is not None:
		_record_asset_mutation(
			session,
			current_user,
			entity_type="CASH_ACCOUNT",
			entity_id=source_account.id,
			operation="UPDATE",
			before_state=source_before_state,
			after_state=_capture_model_state(source_account),
			reason=f"TRANSFER_DELETE#{transfer_id}",
		)
	if target_account is not None and target_before_state is not None:
		_record_asset_mutation(
			session,
			current_user,
			entity_type="CASH_ACCOUNT",
			entity_id=target_account.id,
			operation="UPDATE",
			before_state=target_before_state,
			after_state=_capture_model_state(target_account),
			reason=f"TRANSFER_DELETE#{transfer_id}",
		)
	job_service.enqueue_user_portfolio_snapshot_rebuild(session, current_user.username)
	session.commit()
	_invalidate_dashboard_cache(current_user.username)
	return Response(status_code=204)

__all__ = ['list_asset_mutation_audits', 'list_accounts', 'create_account', 'update_account', 'delete_account', 'list_cash_ledger_entries', 'create_cash_ledger_adjustment', 'update_cash_ledger_adjustment', 'delete_cash_ledger_adjustment', 'list_cash_transfers', 'create_cash_transfer', 'update_cash_transfer', 'delete_cash_transfer']
