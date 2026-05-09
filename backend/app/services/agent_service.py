from __future__ import annotations

from datetime import datetime
import json
from typing import Annotated

from fastapi import Header, HTTPException, Query
from sqlmodel import select

from app import runtime_state
from app.models import (
	AGENT_REGISTRATION_STATUSES,
	AGENT_TASK_STATUSES,
	AgentAccessToken,
	HOLDING_HISTORY_SYNC_STATUSES,
	AgentRegistration,
	AgentTask,
	HoldingHistorySyncRequest,
	utc_now,
)
from app.schemas import AgentContextRead, AgentRegistrationRead, AgentTaskCreate, AgentTaskRead
from app.services import job_service
from app.services.auth_service import (
	AGENT_REGISTRATION_ACTIVE_WINDOW,
	CurrentUserDependency,
	_is_agent_token_active,
	create_agent_token_for_current_session,
	issue_agent_token_with_password,
	list_agent_tokens,
	revoke_agent_token,
)
from app.services.common_service import (
	_build_idempotency_request_hash,
	_coerce_utc_datetime,
	_load_idempotent_response,
	_store_idempotent_response,
)
from app.services.holding_projection_service import _to_holding_transaction_reads
from app.services.holding_transaction_service import _list_holding_transactions_for_user
from app.services.service_context import SessionDependency
from app.services.sql_expression import sql_expr


def _resolve_request_source(
	request_source: str | None,
	*,
	api_key_name: str | None,
	agent_name: str | None,
) -> str:
	if agent_name and agent_name.strip():
		return "AGENT"
	if api_key_name and api_key_name.strip():
		return "API"
	if request_source in {"USER", "SYSTEM"}:
		return request_source
	return "API"


def _to_agent_task_read(task: AgentTask) -> AgentTaskRead:
	return AgentTaskRead(
		id=task.id or 0,
		request_source=_resolve_request_source(
			task.request_source,
			api_key_name=task.api_key_name,
			agent_name=task.agent_name,
		),
		api_key_name=task.api_key_name,
		agent_name=task.agent_name,
		task_type=task.task_type,
		status=task.status,
		payload=json.loads(task.input_json),
		result=json.loads(task.result_json) if task.result_json else None,
		error_message=task.error_message,
		created_at=task.created_at,
		updated_at=task.updated_at,
		completed_at=task.completed_at,
	)


def _resolve_agent_registration_status(
	registration: AgentRegistration,
	*,
	active_api_key_names: set[str],
	now: datetime | None = None,
) -> str:
	now_value = now or utc_now()
	if registration.last_seen_at is None:
		return AGENT_REGISTRATION_STATUSES[1]
	if registration.latest_api_key_name not in active_api_key_names:
		return AGENT_REGISTRATION_STATUSES[1]
	if now_value - _coerce_utc_datetime(registration.last_seen_at) <= AGENT_REGISTRATION_ACTIVE_WINDOW:
		return AGENT_REGISTRATION_STATUSES[0]
	return AGENT_REGISTRATION_STATUSES[1]


def _to_agent_registration_read(
	registration: AgentRegistration,
	*,
	active_api_key_names: set[str],
	now: datetime | None = None,
) -> AgentRegistrationRead:
	now_value = now or utc_now()
	latest_api_key_name = (
		registration.latest_api_key_name
		if registration.latest_api_key_name in active_api_key_names
		else None
	)
	return AgentRegistrationRead(
		id=registration.id or 0,
		user_id=registration.user_id,
		name=registration.name,
		status=_resolve_agent_registration_status(
			registration,
			active_api_key_names=active_api_key_names,
			now=now_value,
		),
		request_count=registration.request_count,
		latest_api_key_name=latest_api_key_name,
		last_used_at=registration.last_seen_at,
		last_seen_at=registration.last_seen_at,
		created_at=registration.created_at,
		updated_at=registration.updated_at,
	)


def _list_active_api_key_names_by_user(
	session: SessionDependency,
	*,
	user_ids: set[str],
	now: datetime | None = None,
) -> dict[str, set[str]]:
	if not user_ids:
		return {}

	now_value = now or utc_now()
	tokens = list(
		session.exec(
			select(AgentAccessToken).where(sql_expr(AgentAccessToken.user_id).in_(sorted(user_ids))),
		),
	)
	active_names_by_user: dict[str, set[str]] = {}
	for token in tokens:
		if not _is_agent_token_active(token, now_value):
			continue
		active_names_by_user.setdefault(token.user_id, set()).add(token.name)
	return active_names_by_user

async def get_agent_context(
	current_user: CurrentUserDependency,
	session: SessionDependency,
	refresh: bool = False,
	transaction_limit: int = Query(default=50, ge=1, le=500),
) -> AgentContextRead:
	from app.services.dashboard_query_service import get_dashboard

	dashboard = await get_dashboard(current_user, session, refresh)
	recent_transactions = _list_holding_transactions_for_user(
		session,
		user_id=current_user.username,
		limit=transaction_limit,
	)
	pending_history_sync_requests = len(
		list(
			session.exec(
				select(HoldingHistorySyncRequest.id).where(
					HoldingHistorySyncRequest.user_id == current_user.username,
					HoldingHistorySyncRequest.status != HOLDING_HISTORY_SYNC_STATUSES[2],
				),
			),
		),
	)
	return AgentContextRead(
		user_id=current_user.username,
		generated_at=utc_now(),
		server_today=dashboard.server_today,
		total_value_cny=dashboard.total_value_cny,
		cash_value_cny=dashboard.cash_value_cny,
		holdings_value_cny=dashboard.holdings_value_cny,
		fixed_assets_value_cny=dashboard.fixed_assets_value_cny,
		liabilities_value_cny=dashboard.liabilities_value_cny,
		other_assets_value_cny=dashboard.other_assets_value_cny,
		usd_cny_rate=dashboard.usd_cny_rate,
		hkd_cny_rate=dashboard.hkd_cny_rate,
		allocation=dashboard.allocation,
		cash_accounts=dashboard.cash_accounts,
		holdings=dashboard.holdings,
		recent_holding_transactions=_to_holding_transaction_reads(
			session,
			user_id=current_user.username,
			transactions=recent_transactions,
		),
		pending_history_sync_requests=pending_history_sync_requests,
		warnings=dashboard.warnings,
	)

def list_agent_tasks(
	current_user: CurrentUserDependency,
	session: SessionDependency,
	limit: int = Query(default=50, ge=1, le=200),
) -> list[AgentTaskRead]:
	tasks = list(
		session.exec(
				select(AgentTask)
				.where(AgentTask.user_id == current_user.username)
				.order_by(sql_expr(AgentTask.created_at).desc(), sql_expr(AgentTask.id).desc())
			.limit(limit),
		),
	)
	return [_to_agent_task_read(task) for task in tasks]


def list_agent_registrations(
	current_user: CurrentUserDependency,
	session: SessionDependency,
	include_all_users: bool = Query(default=False),
) -> list[AgentRegistrationRead]:
	if include_all_users and current_user.username != "admin":
		raise HTTPException(status_code=403, detail="仅管理员可查看所有账号的 Agent 接入。")

	statement = select(AgentRegistration)
	if not include_all_users:
		statement = statement.where(AgentRegistration.user_id == current_user.username)

	registrations = list(
		session.exec(
				statement
				.where(AgentRegistration.request_count > 0)
				.order_by(
					sql_expr(AgentRegistration.updated_at).desc(),
					sql_expr(AgentRegistration.id).desc(),
				),
		),
	)
	now = utc_now()
	active_api_key_names_by_user = _list_active_api_key_names_by_user(
		session,
		user_ids={registration.user_id for registration in registrations},
		now=now,
	)
	return [
		_to_agent_registration_read(
			registration,
			active_api_key_names=active_api_key_names_by_user.get(registration.user_id, set()),
			now=now,
		)
		for registration in registrations
	]

def create_agent_task(
	payload: AgentTaskCreate,
	current_user: CurrentUserDependency,
	session: SessionDependency,
	idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> AgentTaskRead:
	request_hash = _build_idempotency_request_hash(payload)
	idempotent_response = _load_idempotent_response(
		session,
		user_id=current_user.username,
		scope="agent_task.create",
		idempotency_key=idempotency_key,
		request_hash=request_hash,
		response_model=AgentTaskRead,
	)
	if idempotent_response is not None:
		existing_task = session.get(AgentTask, idempotent_response.id)
		return _to_agent_task_read(existing_task) if existing_task is not None else idempotent_response

	task = AgentTask(
		user_id=current_user.username,
		request_source=runtime_state.current_actor_source_context.get(),
		api_key_name=runtime_state.current_api_key_name_context.get(),
		agent_name=runtime_state.current_agent_name_context.get(),
		task_type=payload.task_type,
		status=AGENT_TASK_STATUSES[0],
		input_json=json.dumps(payload.payload, sort_keys=True, ensure_ascii=False),
	)
	session.add(task)
	session.flush()
	job_service.enqueue_agent_task_execution(
		session,
		user_id=current_user.username,
		agent_task_id=task.id or 0,
	)
	response = _to_agent_task_read(task)
	_store_idempotent_response(
		session,
		user_id=current_user.username,
		scope="agent_task.create",
		idempotency_key=idempotency_key,
		request_hash=request_hash,
		response=response,
	)
	session.commit()
	session.refresh(task)
	return _to_agent_task_read(task)

__all__ = [
	'_to_agent_task_read',
	'_to_agent_registration_read',
	'create_agent_task',
	'create_agent_token_for_current_session',
	'get_agent_context',
	'issue_agent_token_with_password',
	'list_agent_registrations',
	'list_agent_tasks',
	'list_agent_tokens',
	'revoke_agent_token',
]
