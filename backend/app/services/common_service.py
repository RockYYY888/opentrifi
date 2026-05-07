from __future__ import annotations

import asyncio
from decimal import Decimal
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlmodel import Session, select

from app import runtime_state
from app.models import (
	ASSET_MUTATION_ACTOR_SOURCES,
	ASSET_MUTATION_OPERATIONS,
	AgentAccessToken,
	AgentTask,
	AgentIdempotencyKey,
	AssetMutationAudit,
	CashAccount,
	CashLedgerEntry,
	CashTransfer,
	FixedAsset,
	HoldingTransactionCashSettlement,
	LiabilityEntry,
	OtherAsset,
	SecurityHolding,
	SecurityHoldingTransaction,
	UserAccount,
	utc_now,
)
from app.schemas import AssetMutationAuditRead
from app.security import normalize_user_id
from app.services import service_context
from app.services.market_data import (
    normalize_symbol as normalize_market_symbol,
    normalize_symbol_for_market as normalize_market_symbol_for_market,
)
from app.fixed_precision import decimal_to_string, display_percent, to_decimal

FEEDBACK_TIMEZONE = ZoneInfo("Asia/Shanghai")
MAX_DAILY_FEEDBACK_SUBMISSIONS = 3
GLOBAL_FORCE_REFRESH_INTERVAL = timedelta(seconds=60)
DASHBOARD_SERIES_SCOPES = ("PORTFOLIO_TOTAL", "HOLDINGS_RETURN_TOTAL", "HOLDING_RETURN")
DASHBOARD_CORRECTION_ACTIONS = ("OVERRIDE", "DELETE")
DASHBOARD_CORRECTION_GRANULARITIES = ("hour", "day", "month", "year")
CACHE_FALLBACK_WARNING_MARKERS = (
    "行情源不可用，已回退到最近缓存值",
    "汇率源不可用，已回退到最近缓存值",
)

def _is_cache_fallback_warning(warning: str) -> bool:
	return any(marker in warning for marker in CACHE_FALLBACK_WARNING_MARKERS)

def _filter_dashboard_warnings_for_user(
	warnings: list[str],
	current_user: UserAccount,
) -> list[str]:
	if current_user.username == "admin":
		return list(warnings)
	return [warning for warning in warnings if not _is_cache_fallback_warning(warning)]

def _normalize_idempotency_key(value: str | None) -> str | None:
	if value is None:
		return None
	normalized = value.strip()
	return normalized or None

def _build_idempotency_request_hash(payload: Any) -> str:
	if hasattr(payload, "model_dump"):
		serialized_payload = payload.model_dump(mode="json")
	else:
		serialized_payload = payload
	return hashlib.sha256(
		json.dumps(serialized_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
			"utf-8",
		),
	).hexdigest()

def _load_idempotency_record(
	session: Session,
	*,
	user_id: str,
	scope: str,
	idempotency_key: str,
) -> AgentIdempotencyKey | None:
	return session.exec(
		select(AgentIdempotencyKey)
		.where(AgentIdempotencyKey.user_id == user_id)
		.where(AgentIdempotencyKey.scope == scope)
		.where(AgentIdempotencyKey.idempotency_key == idempotency_key),
	).first()

def _load_idempotent_response(
	session: Session,
	*,
	user_id: str,
	scope: str,
	idempotency_key: str | None,
	request_hash: str,
	response_model: type[Any],
) -> Any | None:
	normalized_key = _normalize_idempotency_key(idempotency_key)
	if normalized_key is None:
		return None

	record = _load_idempotency_record(
		session,
		user_id=user_id,
		scope=scope,
		idempotency_key=normalized_key,
	)
	if record is None:
		return None
	if record.request_hash != request_hash:
		raise HTTPException(status_code=409, detail="同一幂等键对应的请求参数不一致。")
	return response_model.model_validate(json.loads(record.response_json))

def _store_idempotent_response(
	session: Session,
	*,
	user_id: str,
	scope: str,
	idempotency_key: str | None,
	request_hash: str,
	response: Any,
) -> None:
	normalized_key = _normalize_idempotency_key(idempotency_key)
	if normalized_key is None:
		return

	response_payload = (
		response.model_dump(mode="json")
		if hasattr(response, "model_dump")
		else response
	)
	record = _load_idempotency_record(
		session,
		user_id=user_id,
		scope=scope,
		idempotency_key=normalized_key,
	)
	if record is None:
		record = AgentIdempotencyKey(
			user_id=user_id,
			scope=scope,
			idempotency_key=normalized_key,
			request_hash=request_hash,
			response_json=json.dumps(
				response_payload,
				sort_keys=True,
				separators=(",", ":"),
				ensure_ascii=False,
			),
		)
	else:
		record.request_hash = request_hash
		record.response_json = json.dumps(
			response_payload,
			sort_keys=True,
			separators=(",", ":"),
			ensure_ascii=False,
		)
		_touch_model(record)
	session.add(record)

def _normalize_currency(code: str) -> str:
	return code.strip().upper()

def _normalize_symbol(symbol: str, market: str | None = None) -> str:
	try:
		if market:
			return normalize_market_symbol_for_market(symbol, market)
		return normalize_market_symbol(symbol)
	except ValueError as exc:
		raise HTTPException(status_code=422, detail=str(exc)) from exc

def _normalize_optional_text(value: str | None) -> str | None:
	if value is None:
		return None

	stripped = value.strip()
	return stripped or None

def _json_ready(value: Any) -> Any:
	if isinstance(value, datetime):
		return _coerce_utc_datetime(value).isoformat().replace("+00:00", "Z")
	if isinstance(value, date):
		return value.isoformat()
	if isinstance(value, Decimal):
		return decimal_to_string(value)
	if isinstance(value, dict):
		return {str(key): _json_ready(item) for key, item in value.items()}
	if isinstance(value, (list, tuple)):
		return [_json_ready(item) for item in value]
	return value

def _capture_model_state(
	model: CashAccount
	| CashLedgerEntry
	| CashTransfer
	| SecurityHolding
	| SecurityHoldingTransaction
	| AgentTask
	| FixedAsset
	| LiabilityEntry
	| OtherAsset,
) -> dict[str, Any]:
	return _json_ready(model.model_dump())

def _serialize_audit_state(state: dict[str, Any] | None) -> str | None:
	if state is None:
		return None
	return json.dumps(_json_ready(state), ensure_ascii=False, sort_keys=True)

def _resolve_asset_mutation_actor_source(current_user: UserAccount) -> str:
	candidate_source = runtime_state.current_actor_source_context.get()
	if candidate_source in ASSET_MUTATION_ACTOR_SOURCES:
		if candidate_source == "USER" and current_user.username == "admin":
			return "SYSTEM"
		return candidate_source

	return "SYSTEM" if current_user.username == "admin" else "USER"

def _record_asset_mutation(
	session: Session,
	current_user: UserAccount,
	entity_type: str,
	entity_id: int | None,
	operation: str,
	before_state: dict[str, Any] | None,
	after_state: dict[str, Any] | None,
	reason: str | None = None,
) -> None:
	if operation not in ASSET_MUTATION_OPERATIONS:
		raise ValueError(f"Unsupported asset mutation operation: {operation}")

	session.add(
		AssetMutationAudit(
			user_id=current_user.username,
			actor_user_id=current_user.username,
			actor_source=_resolve_asset_mutation_actor_source(current_user),
			api_key_name=runtime_state.current_api_key_name_context.get(),
			agent_name=runtime_state.current_agent_name_context.get(),
			agent_task_id=runtime_state.current_agent_task_id_context.get(),
			entity_type=entity_type,
			entity_id=entity_id,
			operation=operation,
			before_state=_serialize_audit_state(before_state),
			after_state=_serialize_audit_state(after_state),
			reason=reason,
		),
	)

def _touch_model(
	model: AgentAccessToken
	| CashAccount
	| CashLedgerEntry
	| CashTransfer
	| SecurityHolding
	| SecurityHoldingTransaction
	| HoldingTransactionCashSettlement
	| AgentTask
	| FixedAsset
	| LiabilityEntry
	| OtherAsset
	| UserAccount,
) -> None:
	model.updated_at = utc_now()

def _calculate_return_pct(
	current_value: Decimal | float | int,
	basis_value: Decimal | float | int | None,
) -> Decimal | None:
	normalized_basis = to_decimal(basis_value, default=Decimal("0"))
	if basis_value is None or normalized_basis <= 0:
		return None

	return display_percent(((to_decimal(current_value) - normalized_basis) / normalized_basis) * 100)

def _coerce_utc_datetime(value: datetime) -> datetime:
	"""Normalize persisted datetimes so legacy naive rows compare safely."""
	if value.tzinfo is None:
		return value.replace(tzinfo=timezone.utc)

	return value.astimezone(timezone.utc)

def _current_minute_bucket(value: datetime | None = None) -> datetime:
	timestamp = _coerce_utc_datetime(value or utc_now())
	return timestamp.replace(second=0, microsecond=0)

def _current_second_bucket(value: datetime | None = None) -> datetime:
	timestamp = _coerce_utc_datetime(value or utc_now())
	return timestamp.replace(microsecond=0)

def _current_hour_bucket(value: datetime | None = None) -> datetime:
	timestamp = _coerce_utc_datetime(value or utc_now())
	return timestamp.replace(minute=0, second=0, microsecond=0)

def _feedback_day_window(value: datetime | None = None) -> tuple[datetime, datetime]:
	timestamp = _coerce_utc_datetime(value or utc_now()).astimezone(FEEDBACK_TIMEZONE)
	day_start_local = timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
	day_end_local = day_start_local + timedelta(days=1)
	return day_start_local.astimezone(timezone.utc), day_end_local.astimezone(timezone.utc)

def _server_today_date(value: datetime | None = None) -> date:
	timestamp = _coerce_utc_datetime(value or utc_now()).astimezone(FEEDBACK_TIMEZONE)
	return timestamp.date()

def _ensure_date_not_future(value: date | None, *, field_label: str) -> None:
	if value is None:
		return

	server_today = _server_today_date()
	if value > server_today:
		raise HTTPException(
			status_code=422,
			detail=f"{field_label}不能晚于今日（服务器日期：{server_today.isoformat()}）。",
		)

def _date_start_utc(value: date) -> datetime:
	"""Convert a local calendar date into the UTC timestamp of local 00:00."""
	day_start_local = datetime(
		value.year,
		value.month,
		value.day,
		tzinfo=FEEDBACK_TIMEZONE,
	)
	return day_start_local.astimezone(timezone.utc)

def _is_current_minute(value: datetime | None, now: datetime | None = None) -> bool:
	if value is None:
		return False

	return _current_minute_bucket(value) == _current_minute_bucket(now)

def _is_current_second(value: datetime | None, now: datetime | None = None) -> bool:
	if value is None:
		return False

	return _current_second_bucket(value) == _current_second_bucket(now)

async def _consume_global_force_refresh_slot() -> bool:
	"""Allow at most one cache-clearing force refresh every 60 seconds across all workers."""
	async with runtime_state.async_redis_lock(
		"global-force-refresh",
		timeout=10,
		blocking_timeout=10,
	):
		now = utc_now()
		if (
			runtime_state.get_last_global_force_refresh_at() is not None
			and now - _coerce_utc_datetime(runtime_state.get_last_global_force_refresh_at())
			< GLOBAL_FORCE_REFRESH_INTERVAL
		):
			return False

		runtime_state.set_last_global_force_refresh_at(now)
		return True

def _is_same_hour(value: datetime | None, now: datetime | None = None) -> bool:
	if value is None:
		return False

	return _current_hour_bucket(value) == _current_hour_bucket(now)

def _invalidate_dashboard_cache(user_id: str | None = None) -> None:
	if user_id is None:
		runtime_state.dashboard_cache.clear()
		return

	runtime_state.dashboard_cache.pop(user_id, None)

def _to_asset_mutation_audit_read(audit: AssetMutationAudit) -> AssetMutationAuditRead:
	return AssetMutationAuditRead(
		id=audit.id or 0,
		actor_source=audit.actor_source,
		api_key_name=audit.api_key_name,
		agent_name=audit.agent_name,
		agent_task_id=audit.agent_task_id,
		entity_type=audit.entity_type,
		entity_id=audit.entity_id,
		operation=audit.operation,
		before_state=audit.before_state,
		after_state=audit.after_state,
		reason=audit.reason,
		created_at=audit.created_at,
	)

def _require_admin_user(current_user: UserAccount) -> None:
	if current_user.username != "admin":
		raise HTTPException(status_code=403, detail="仅管理员可访问。")

__all__ = ['_is_cache_fallback_warning', '_filter_dashboard_warnings_for_user', '_normalize_idempotency_key', '_build_idempotency_request_hash', '_load_idempotency_record', '_load_idempotent_response', '_store_idempotent_response', '_normalize_currency', '_normalize_symbol', '_normalize_optional_text', '_json_ready', '_capture_model_state', '_serialize_audit_state', '_record_asset_mutation', '_touch_model', '_calculate_return_pct', '_coerce_utc_datetime', '_current_second_bucket', '_current_minute_bucket', '_current_hour_bucket', '_feedback_day_window', '_server_today_date', '_ensure_date_not_future', '_date_start_utc', '_is_current_second', '_is_current_minute', '_consume_global_force_refresh_slot', '_is_same_hour', '_invalidate_dashboard_cache', '_to_asset_mutation_audit_read', '_require_admin_user', 'CACHE_FALLBACK_WARNING_MARKERS', 'DASHBOARD_CORRECTION_ACTIONS', 'DASHBOARD_CORRECTION_GRANULARITIES', 'DASHBOARD_SERIES_SCOPES', 'FEEDBACK_TIMEZONE', 'GLOBAL_FORCE_REFRESH_INTERVAL', 'MAX_DAILY_FEEDBACK_SUBMISSIONS']
