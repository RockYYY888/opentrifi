from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from sqlmodel import select

from app.fixed_precision import (
	decimal_to_float,
	display_money,
	display_percent,
	display_price,
	display_quantity,
	to_decimal,
)
from app.models import AssetMutationAudit, SecurityHoldingTransaction
from app.schemas import AssetRecordRead
from app.services.auth_service import CurrentUserDependency
from app.services.holding_projection_service import (
	_project_holding_state_from_sorted_transactions,
	_projected_holding_cost_basis,
)
from app.services.service_context import SessionDependency
from app.services.sql_expression import sql_expr

ASSET_RECORD_CLASSES = ("cash", "investment", "fixed", "liability", "other")
ASSET_RECORD_OPERATIONS = ("NEW", "EDIT", "DELETE", "BUY", "SELL", "TRANSFER", "ADJUST")
ASSET_RECORD_SOURCES = ("USER", "SYSTEM", "API", "AGENT")
CANONICAL_AUDIT_ENTITY_TYPES = (
	"CASH_ACCOUNT",
	"CASH_TRANSFER",
	"CASH_LEDGER_ADJUSTMENT",
	"HOLDING",
	"HOLDING_TRANSACTION",
	"FIXED_ASSET",
	"LIABILITY",
	"OTHER_ASSET",
)
SIDE_EFFECT_CASH_ACCOUNT_REASON_PREFIXES = (
	"TRANSFER_",
	"LEDGER_ADJUSTMENT_",
	"SELL_PROCEEDS",
	"BUY_FUNDING",
)


def _parse_audit_state(value: str | None) -> dict[str, Any] | None:
	if not value:
		return None
	try:
		parsed = json.loads(value)
	except json.JSONDecodeError:
		return None
	return parsed if isinstance(parsed, dict) else None


def _normalize_asset_record_filter(
	value: str | None,
	*,
	allowed_values: tuple[str, ...],
	field_label: str,
	uppercase: bool = False,
) -> str | None:
	if value is None:
		return None
	normalized = value.strip().upper() if uppercase else value.strip().lower()
	if not normalized:
		return None
	if normalized not in allowed_values:
		raise HTTPException(
			status_code=422,
			detail=f"{field_label} 必须是 {', '.join(allowed_values)} 之一。",
		)
	return normalized


def _resolve_audit_source(audit: AssetMutationAudit) -> str:
	if audit.actor_source in {"USER", "SYSTEM"}:
		return audit.actor_source
	if audit.agent_name:
		return "AGENT"
	if audit.api_key_name or audit.agent_task_id is not None or audit.actor_source in {"API", "AGENT"}:
		return "API"
	return "USER"


def _is_cash_account_business_record(audit: AssetMutationAudit) -> bool:
	reason = (audit.reason or "").strip().upper()
	if audit.operation == "CREATE":
		return not reason.startswith("AUTO_SELL_PROCEEDS")
	if audit.operation == "UPDATE":
		return not any(reason.startswith(prefix) for prefix in SIDE_EFFECT_CASH_ACCOUNT_REASON_PREFIXES)
	return True


def _is_numeric_value(value: Any) -> bool:
	if value is None or isinstance(value, bool):
		return False
	try:
		to_decimal(value)
	except (ArithmeticError, TypeError, ValueError):
		return False
	return True


def _resolve_cash_account_record(audit: AssetMutationAudit) -> AssetRecordRead | None:
	if not _is_cash_account_business_record(audit):
		return None

	state = _parse_audit_state(audit.after_state if audit.operation != "DELETE" else audit.before_state)
	if state is None:
		return None

	operation_kind = "NEW" if audit.operation == "CREATE" else audit.operation
	if operation_kind == "UPDATE":
		operation_kind = "EDIT"

	balance = state.get("balance")
	currency = state.get("currency")
	name = str(state.get("name") or "现金账户").strip()
	platform = str(state.get("platform") or "").strip()
	summary_prefix = {
		"NEW": "新建现金账户",
		"EDIT": "编辑现金账户",
		"DELETE": "删除现金账户",
	}.get(operation_kind, "现金账户记录")
	summary = f"{summary_prefix} · {platform}" if platform else summary_prefix
	if _is_numeric_value(balance) and currency:
		summary = f"{summary} · {decimal_to_float(display_money(balance)):g} {currency}"

	return AssetRecordRead(
		id=audit.id or 0,
		source=_resolve_audit_source(audit),
		api_key_name=audit.api_key_name,
		agent_name=audit.agent_name,
		agent_task_id=audit.agent_task_id,
		asset_class="cash",
		operation_kind=operation_kind,
		entity_type=audit.entity_type,
		entity_id=audit.entity_id,
		title=name,
		summary=summary,
		effective_date=None,
		amount=display_money(balance) if _is_numeric_value(balance) else None,
		currency=str(currency).upper() if currency else None,
		created_at=audit.created_at,
	)


def _resolve_cash_transfer_summary(state: dict[str, Any]) -> str:
	source_amount = state.get("source_amount")
	target_amount = state.get("target_amount")
	source_currency = state.get("source_currency")
	target_currency = state.get("target_currency")
	from_account_id = state.get("from_account_id")
	to_account_id = state.get("to_account_id")
	segments = [f"账户 #{from_account_id} → 账户 #{to_account_id}"]
	if _is_numeric_value(source_amount) and source_currency:
		segments.append(f"{decimal_to_float(display_money(source_amount)):g} {source_currency}")
	if (
		_is_numeric_value(target_amount)
		and target_currency
		and (
			target_currency != source_currency
			or target_amount != source_amount
		)
	):
		segments.append(
			f"转入 {decimal_to_float(display_money(target_amount)):g} {target_currency}",
		)
	return " · ".join(segments)


def _resolve_cash_transfer_record(audit: AssetMutationAudit) -> AssetRecordRead | None:
	state = _parse_audit_state(audit.after_state if audit.operation != "DELETE" else audit.before_state)
	if state is None:
		return None

	operation_kind = "TRANSFER" if audit.operation == "CREATE" else audit.operation
	if operation_kind == "UPDATE":
		operation_kind = "EDIT"

	return AssetRecordRead(
		id=audit.id or 0,
		source=_resolve_audit_source(audit),
		api_key_name=audit.api_key_name,
		agent_name=audit.agent_name,
		agent_task_id=audit.agent_task_id,
		asset_class="cash",
		operation_kind=operation_kind,
		entity_type=audit.entity_type,
		entity_id=audit.entity_id,
		title="账户划转",
		summary=_resolve_cash_transfer_summary(state),
		effective_date=state.get("transferred_on"),
		amount=(
			display_money(state["source_amount"])
			if _is_numeric_value(state.get("source_amount"))
			else None
		),
		currency=str(state.get("source_currency") or "").upper() or None,
		created_at=audit.created_at,
	)


def _resolve_cash_adjustment_record(audit: AssetMutationAudit) -> AssetRecordRead | None:
	state = _parse_audit_state(audit.after_state if audit.operation != "DELETE" else audit.before_state)
	if state is None:
		return None

	operation_kind = "ADJUST"
	if audit.operation == "DELETE":
		operation_kind = "DELETE"
	elif audit.operation == "UPDATE":
		operation_kind = "EDIT"

	return AssetRecordRead(
		id=audit.id or 0,
		source=_resolve_audit_source(audit),
		api_key_name=audit.api_key_name,
		agent_name=audit.agent_name,
		agent_task_id=audit.agent_task_id,
		asset_class="cash",
		operation_kind=operation_kind,
		entity_type=audit.entity_type,
		entity_id=audit.entity_id,
		title="现金余额调整",
		summary=state.get("note") or "手工账本调整",
		effective_date=state.get("happened_on"),
		amount=(
			display_money(state["amount"])
			if _is_numeric_value(state.get("amount"))
			else None
		),
		currency=str(state.get("currency") or "").upper() or None,
		created_at=audit.created_at,
	)


def _resolve_holding_title(state: dict[str, Any]) -> tuple[str, str | None]:
	symbol = str(state.get("symbol") or "").strip() or None
	name = str(state.get("name") or "").strip()
	if name and symbol:
		return f"{name} ({symbol})", symbol
	if name:
		return name, symbol
	return symbol or "投资记录", symbol


def _resolve_investment_profit_map(
	audits: list[AssetMutationAudit],
) -> dict[int, tuple[Decimal, str, Decimal] | None]:
	profit_map: dict[int, tuple[Decimal, str, Decimal] | None] = {}
	transaction_versions: dict[int, SecurityHoldingTransaction] = {}

	for audit in sorted(audits, key=lambda item: (item.created_at, item.id or 0)):
		if audit.entity_type != "HOLDING_TRANSACTION":
			continue

		next_state = _parse_audit_state(audit.after_state)
		previous_state = _parse_audit_state(audit.before_state)
		current_state = next_state if audit.operation != "DELETE" else previous_state
		if current_state is None:
			continue

		try:
			next_transaction = SecurityHoldingTransaction.model_validate(current_state)
		except Exception:
			continue

		if next_transaction.side == "SELL":
			symbol = next_transaction.symbol
			market = next_transaction.market
			transactions_before = [
				transaction
				for transaction in transaction_versions.values()
				if transaction.symbol == symbol and transaction.market == market
			]
			if audit.operation == "UPDATE":
				transactions_before = [
					transaction
					for transaction in transactions_before
					if (transaction.id or 0) != (audit.entity_id or 0)
				]
			projected_before = _project_holding_state_from_sorted_transactions(
				transactions_before,
				symbol=symbol,
				market=market,
			)
			cost_basis_price = (
				_projected_holding_cost_basis(projected_before)
				if projected_before is not None
				else None
			)
			if (
				cost_basis_price is not None
				and next_transaction.price is not None
				and next_transaction.price > 0
				and next_transaction.quantity > 0
			):
				profit_amount = display_money(
					next_transaction.quantity * (next_transaction.price - cost_basis_price),
				)
				profit_rate_pct = display_percent(
					((next_transaction.price - cost_basis_price) / cost_basis_price) * 100,
				)
				profit_map[audit.id or 0] = (
					profit_amount,
					next_transaction.fallback_currency,
					profit_rate_pct,
				)
			else:
				profit_map[audit.id or 0] = None

		if audit.operation == "DELETE":
			transaction_versions.pop(audit.entity_id or 0, None)
		else:
			transaction_versions[audit.entity_id or 0] = next_transaction

	return profit_map


def _resolve_holding_transaction_record(
	audit: AssetMutationAudit,
	profit_map: dict[int, tuple[Decimal, str, Decimal] | None],
) -> AssetRecordRead | None:
	state = _parse_audit_state(audit.after_state if audit.operation != "DELETE" else audit.before_state)
	if state is None:
		return None

	title, symbol = _resolve_holding_title(state)
	side = str(state.get("side") or "").upper()
	price = state.get("price")
	quantity = state.get("quantity")
	currency = str(state.get("fallback_currency") or "").upper() or None
	if side == "BUY":
		operation_kind = "BUY" if audit.operation == "CREATE" else "EDIT"
		summary_prefix = "新增买入" if audit.operation == "CREATE" else "编辑投资记录"
	elif side == "SELL":
		operation_kind = "SELL" if audit.operation == "CREATE" else "EDIT"
		summary_prefix = "新增卖出" if audit.operation == "CREATE" else "编辑投资记录"
	elif side == "ADJUST":
		operation_kind = "EDIT"
		summary_prefix = "编辑持仓"
	else:
		operation_kind = "DELETE"
		summary_prefix = "删除投资记录"

	summary = summary_prefix
	if _is_numeric_value(quantity):
		summary = f"{summary} · 数量 {decimal_to_float(display_quantity(quantity)):g}"
	if _is_numeric_value(price) and currency:
		summary = f"{summary} · 价格 {decimal_to_float(display_price(price)):g} {currency}"

	profit_tuple = profit_map.get(audit.id or 0)
	return AssetRecordRead(
		id=audit.id or 0,
		source=_resolve_audit_source(audit),
		api_key_name=audit.api_key_name,
		agent_name=audit.agent_name,
		agent_task_id=audit.agent_task_id,
		asset_class="investment",
		operation_kind=operation_kind,
		entity_type=audit.entity_type,
		entity_id=audit.entity_id,
		title=title,
		summary=summary,
		symbol=symbol,
		effective_date=state.get("traded_on"),
		amount=display_price(price) if _is_numeric_value(price) else None,
		currency=currency,
		profit_amount=profit_tuple[0] if profit_tuple is not None else None,
		profit_currency=profit_tuple[1] if profit_tuple is not None else None,
		profit_rate_pct=profit_tuple[2] if profit_tuple is not None else None,
		created_at=audit.created_at,
	)


def _resolve_holding_delete_record(audit: AssetMutationAudit) -> AssetRecordRead | None:
	state = _parse_audit_state(audit.before_state)
	if state is None:
		return None

	title, symbol = _resolve_holding_title(state)
	return AssetRecordRead(
		id=audit.id or 0,
		source=_resolve_audit_source(audit),
		api_key_name=audit.api_key_name,
		agent_name=audit.agent_name,
		agent_task_id=audit.agent_task_id,
		asset_class="investment",
		operation_kind="DELETE",
		entity_type=audit.entity_type,
		entity_id=audit.entity_id,
		title=title,
		summary="删除持仓",
		symbol=symbol,
		effective_date=state.get("started_on"),
		created_at=audit.created_at,
	)


def _resolve_asset_entry_record(
	audit: AssetMutationAudit,
	*,
	asset_class: str,
	title_fallback: str,
	amount_field: str,
	currency: str | None = None,
) -> AssetRecordRead | None:
	state = _parse_audit_state(audit.after_state if audit.operation != "DELETE" else audit.before_state)
	if state is None:
		return None

	operation_kind = "NEW" if audit.operation == "CREATE" else audit.operation
	if operation_kind == "UPDATE":
		operation_kind = "EDIT"
	title = str(state.get("name") or title_fallback).strip() or title_fallback
	amount = state.get(amount_field)

	return AssetRecordRead(
		id=audit.id or 0,
		source=_resolve_audit_source(audit),
		api_key_name=audit.api_key_name,
		agent_name=audit.agent_name,
		agent_task_id=audit.agent_task_id,
		asset_class=asset_class,
		operation_kind=operation_kind,
		entity_type=audit.entity_type,
		entity_id=audit.entity_id,
		title=title,
		summary={
			"NEW": f"新建{title_fallback}",
			"EDIT": f"编辑{title_fallback}",
			"DELETE": f"删除{title_fallback}",
		}.get(operation_kind, title_fallback),
		effective_date=state.get("started_on"),
		amount=display_money(amount) if _is_numeric_value(amount) else None,
		currency=currency,
		created_at=audit.created_at,
	)


def _build_asset_record(
	audit: AssetMutationAudit,
	profit_map: dict[int, tuple[Decimal, str, Decimal] | None],
) -> AssetRecordRead | None:
	if audit.entity_type == "CASH_ACCOUNT":
		return _resolve_cash_account_record(audit)
	if audit.entity_type == "CASH_TRANSFER":
		return _resolve_cash_transfer_record(audit)
	if audit.entity_type == "CASH_LEDGER_ADJUSTMENT":
		return _resolve_cash_adjustment_record(audit)
	if audit.entity_type == "HOLDING_TRANSACTION":
		return _resolve_holding_transaction_record(audit, profit_map)
	if audit.entity_type == "HOLDING":
		return _resolve_holding_delete_record(audit) if audit.operation == "DELETE" else None
	if audit.entity_type == "FIXED_ASSET":
		return _resolve_asset_entry_record(
			audit,
			asset_class="fixed",
			title_fallback="固定资产",
			amount_field="current_value_cny",
			currency="CNY",
		)
	if audit.entity_type == "LIABILITY":
		state = _parse_audit_state(audit.after_state if audit.operation != "DELETE" else audit.before_state)
		return _resolve_asset_entry_record(
			audit,
			asset_class="liability",
			title_fallback="负债",
			amount_field="balance",
			currency=str(state.get("currency") or "").upper() or None if state else None,
		)
	if audit.entity_type == "OTHER_ASSET":
		return _resolve_asset_entry_record(
			audit,
			asset_class="other",
			title_fallback="其他资产",
			amount_field="current_value_cny",
			currency="CNY",
		)
	return None


def list_asset_records(
	current_user: CurrentUserDependency,
	session: SessionDependency,
	limit: int = 200,
	asset_class: str | None = None,
	operation_kind: str | None = None,
	source: str | None = None,
) -> list[AssetRecordRead]:
	clamped_limit = max(1, min(limit, 500))
	normalized_asset_class = _normalize_asset_record_filter(
		asset_class,
		allowed_values=ASSET_RECORD_CLASSES,
		field_label="asset_class",
	)
	normalized_operation_kind = _normalize_asset_record_filter(
		operation_kind,
		allowed_values=ASSET_RECORD_OPERATIONS,
		field_label="operation_kind",
		uppercase=True,
	)
	normalized_source = _normalize_asset_record_filter(
		source,
		allowed_values=ASSET_RECORD_SOURCES,
		field_label="source",
		uppercase=True,
	)

	fetch_limit = min(max(clamped_limit * 12, 400), 5000)
	candidate_audits = list(
		session.exec(
				select(AssetMutationAudit)
				.where(AssetMutationAudit.user_id == current_user.username)
				.where(sql_expr(AssetMutationAudit.entity_type).in_(CANONICAL_AUDIT_ENTITY_TYPES))
				.order_by(
					sql_expr(AssetMutationAudit.created_at).desc(),
					sql_expr(AssetMutationAudit.id).desc(),
				)
				.limit(fetch_limit),
		),
	)
	investment_profit_map = _resolve_investment_profit_map(list(reversed(candidate_audits)))
	records: list[AssetRecordRead] = []

	for audit in candidate_audits:
		record = _build_asset_record(audit, investment_profit_map)
		if record is None:
			continue
		if normalized_asset_class is not None and record.asset_class != normalized_asset_class:
			continue
		if normalized_operation_kind is not None and record.operation_kind != normalized_operation_kind:
			continue
		if normalized_source is not None and record.source != normalized_source:
			continue
		records.append(record)
		if len(records) >= clamped_limit:
			break

	return records


__all__ = ["list_asset_records"]
