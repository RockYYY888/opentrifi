from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from sqlmodel import Session, select
from starlette.requests import Request

from app import runtime_state
from app.models import AgentRegistration, AgentTask, CashAccount, CashTransfer, UserAccount
from app.schemas import AgentTaskCreate, AgentTokenCreate, CashAccountCreate
from app.services.agent_service import create_agent_task, list_agent_registrations, list_agent_tasks
from app.services.asset_record_service import list_asset_records
from app.services.auth_service import (
	AGENT_REGISTRATION_ACTIVE_WINDOW,
	create_agent_token_for_current_session,
	get_current_user,
	revoke_agent_token,
)
from app.services.cash_account_service import create_account
from app.services import job_service
from app.services.sql_expression import sql_expr

DEMO_MARKER = "[agent-workspace-demo]"
DEMO_ACTIVE_AGENT_NAME = "rebalancer-bot"
DEMO_INACTIVE_AGENT_NAME = "history-audit-bot"
DEMO_SUPPORT_ACCOUNT_NAME = "Agent 任务主账户"
DEMO_DIRECT_ACCOUNT_NAME = "Agent API 沙盒账户"
DEMO_SUPPORT_ACCOUNT_NOTE = f"{DEMO_MARKER} 任务执行资金账户"
DEMO_DIRECT_ACCOUNT_NOTE = f"{DEMO_MARKER} Agent 直连 API 创建"
DEMO_CREATE_TRANSFER_NOTE = f"{DEMO_MARKER} 任务划转演示"
DEMO_UPDATE_TRANSFER_NOTE = f"{DEMO_MARKER} 任务审计修正"
DEMO_LEDGER_NOTE = f"{DEMO_MARKER} 对账补差"


@dataclass(frozen=True)
class AgentWorkspaceDemoSeedSummary:
	registrations: int
	active_registrations: int
	tasks: int
	direct_api_records: int
	agent_records: int


def _build_bearer_request(access_token: str) -> Request:
	return _build_bearer_request_with_agent_name(access_token, agent_name=None)


def _build_bearer_request_with_agent_name(
	access_token: str,
	*,
	agent_name: str | None,
) -> Request:
	headers: list[tuple[bytes, bytes]] = [
		(b"authorization", f"Bearer {access_token}".encode("utf-8")),
	]
	if agent_name:
		headers.append((b"agent-name", agent_name.encode("utf-8")))
	scope = {
		"type": "http",
		"method": "POST",
		"path": "/api/agent/demo-seed",
		"scheme": "http",
		"http_version": "1.1",
		"query_string": b"",
		"headers": headers,
		"client": ("127.0.0.1", 12345),
		"session": {},
	}
	return Request(scope)


def _run_pending_jobs(limit: int = 20) -> int:
	return asyncio.run(job_service.process_all_pending_background_jobs(limit=limit))


def _get_registration(
	session: Session,
	*,
	user_id: str,
	name: str,
) -> AgentRegistration | None:
	return session.exec(
		select(AgentRegistration)
		.where(AgentRegistration.user_id == user_id)
		.where(AgentRegistration.name == name),
	).first()


def _get_demo_cash_account(
	session: Session,
	*,
	user_id: str,
	name: str,
) -> CashAccount | None:
	return session.exec(
		select(CashAccount)
		.where(CashAccount.user_id == user_id)
		.where(CashAccount.name == name),
	).first()


def _task_exists(session: Session, *, user_id: str, note_marker: str) -> bool:
	return session.exec(
			select(AgentTask.id)
			.where(AgentTask.user_id == user_id)
			.where(sql_expr(AgentTask.input_json).contains(note_marker)),
	).first() is not None


def _authenticate_with_agent_token(
	session: Session,
	*,
	access_token: str,
	agent_name: str | None = None,
) -> UserAccount:
	return get_current_user(
		_build_bearer_request_with_agent_name(access_token, agent_name=agent_name),
		session,
		None,
	)


def _reset_authenticated_request_contexts() -> None:
	runtime_state.current_actor_source_context.set("USER")
	runtime_state.current_api_key_name_context.set(None)
	runtime_state.current_agent_name_context.set(None)


def _set_agent_request_context(*, api_key_name: str, agent_name: str) -> None:
	runtime_state.current_actor_source_context.set("AGENT")
	runtime_state.current_api_key_name_context.set(api_key_name)
	runtime_state.current_agent_name_context.set(agent_name)


def _ensure_support_account(
	session: Session,
	*,
	current_user: UserAccount,
) -> CashAccount:
	account = _get_demo_cash_account(
		session,
		user_id=current_user.username,
		name=DEMO_SUPPORT_ACCOUNT_NAME,
	)
	if account is not None:
		return account

	create_account(
		CashAccountCreate(
				name=DEMO_SUPPORT_ACCOUNT_NAME,
				platform="Sandbox",
				currency="CNY",
				balance=Decimal("240"),
			account_type="BANK",
			note=DEMO_SUPPORT_ACCOUNT_NOTE,
		),
		current_user,
		session,
	)
	account = _get_demo_cash_account(
		session,
		user_id=current_user.username,
		name=DEMO_SUPPORT_ACCOUNT_NAME,
	)
	if account is None:
		raise RuntimeError("Failed to create demo support account.")
	return account


def _ensure_active_agent_registration(
	session: Session,
	*,
	current_user: UserAccount,
) -> str | None:
	registration = _get_registration(
		session,
		user_id=current_user.username,
		name=DEMO_ACTIVE_AGENT_NAME,
	)
	if registration is not None and registration.status == "ACTIVE":
		return None

	token = create_agent_token_for_current_session(
		AgentTokenCreate(name=DEMO_ACTIVE_AGENT_NAME, expires_in_days=365),
		current_user,
		session,
	)
	try:
		_authenticate_with_agent_token(
			session,
			access_token=token.access_token,
			agent_name=DEMO_ACTIVE_AGENT_NAME,
		)
	finally:
		_reset_authenticated_request_contexts()
	return token.access_token


def _ensure_inactive_agent_registration(
	session: Session,
	*,
	current_user: UserAccount,
) -> None:
	registration = _get_registration(
		session,
		user_id=current_user.username,
		name=DEMO_INACTIVE_AGENT_NAME,
	)
	if registration is not None and registration.status == "INACTIVE":
		return

	token = create_agent_token_for_current_session(
		AgentTokenCreate(name=DEMO_INACTIVE_AGENT_NAME, expires_in_days=30),
		current_user,
		session,
	)
	try:
		_authenticate_with_agent_token(
			session,
			access_token=token.access_token,
			agent_name=DEMO_INACTIVE_AGENT_NAME,
		)
	finally:
		_reset_authenticated_request_contexts()
	revoke_agent_token(token.id, current_user, session)
	registration = _get_registration(
		session,
		user_id=current_user.username,
		name=DEMO_INACTIVE_AGENT_NAME,
	)
	if registration is not None:
		registration.status = "INACTIVE"
		if registration.last_seen_at is None:
			registration.last_seen_at = registration.created_at
		registration.last_seen_at = registration.last_seen_at - (
			AGENT_REGISTRATION_ACTIVE_WINDOW + timedelta(minutes=1)
		)
		session.add(registration)
		session.commit()


def _ensure_direct_api_account(
	session: Session,
	*,
	current_user: UserAccount,
	active_access_token: str | None,
) -> CashAccount:
	account = _get_demo_cash_account(
		session,
		user_id=current_user.username,
		name=DEMO_DIRECT_ACCOUNT_NAME,
	)
	if account is not None:
		return account
	if active_access_token is None:
		token = create_agent_token_for_current_session(
			AgentTokenCreate(name=DEMO_ACTIVE_AGENT_NAME, expires_in_days=365),
			current_user,
			session,
		)
		active_access_token = token.access_token
	try:
		agent_user = _authenticate_with_agent_token(session, access_token=active_access_token)
		create_account(
			CashAccountCreate(
					name=DEMO_DIRECT_ACCOUNT_NAME,
					platform="Agent API",
					currency="CNY",
					balance=Decimal("20"),
				account_type="OTHER",
				note=DEMO_DIRECT_ACCOUNT_NOTE,
			),
			agent_user,
			session,
		)
	finally:
		_reset_authenticated_request_contexts()
	account = _get_demo_cash_account(
		session,
		user_id=current_user.username,
		name=DEMO_DIRECT_ACCOUNT_NAME,
	)
	if account is None:
		raise RuntimeError("Failed to create demo direct API account.")
	return account


def _ensure_create_transfer_task(
	session: Session,
	*,
	current_user: UserAccount,
	source_account_id: int,
	target_account_id: int,
) -> None:
	if _task_exists(session, user_id=current_user.username, note_marker=DEMO_CREATE_TRANSFER_NOTE):
		return

	try:
		_set_agent_request_context(
			api_key_name=DEMO_ACTIVE_AGENT_NAME,
			agent_name=DEMO_ACTIVE_AGENT_NAME,
		)
		create_agent_task(
			AgentTaskCreate(
				task_type="CREATE_CASH_TRANSFER",
				payload={
					"from_account_id": source_account_id,
					"to_account_id": target_account_id,
					"source_amount": 60,
					"transferred_on": "2026-03-13",
					"note": DEMO_CREATE_TRANSFER_NOTE,
				},
			),
			current_user,
			session,
			"agent-demo-create-transfer",
		)
	finally:
		_reset_authenticated_request_contexts()
	_run_pending_jobs()
	session.expire_all()


def _resolve_demo_transfer_id(
	session: Session,
	*,
	user_id: str,
) -> int | None:
	updated_transfer = session.exec(
			select(CashTransfer)
			.where(CashTransfer.user_id == user_id)
			.where(CashTransfer.note == DEMO_UPDATE_TRANSFER_NOTE)
			.order_by(sql_expr(CashTransfer.updated_at).desc(), sql_expr(CashTransfer.id).desc())
		).first()
	if updated_transfer is not None:
		return updated_transfer.id

	created_transfer = session.exec(
			select(CashTransfer)
			.where(CashTransfer.user_id == user_id)
			.where(CashTransfer.note == DEMO_CREATE_TRANSFER_NOTE)
			.order_by(sql_expr(CashTransfer.updated_at).desc(), sql_expr(CashTransfer.id).desc())
		).first()
	return created_transfer.id if created_transfer is not None else None


def _ensure_update_transfer_task(
	session: Session,
	*,
	current_user: UserAccount,
	transfer_id: int | None,
) -> None:
	if transfer_id is None:
		return
	if _task_exists(session, user_id=current_user.username, note_marker=DEMO_UPDATE_TRANSFER_NOTE):
		return

	try:
		_set_agent_request_context(
			api_key_name=DEMO_ACTIVE_AGENT_NAME,
			agent_name=DEMO_ACTIVE_AGENT_NAME,
		)
		create_agent_task(
			AgentTaskCreate(
				task_type="UPDATE_CASH_TRANSFER",
				payload={
					"transfer_id": transfer_id,
					"source_amount": 45,
					"transferred_on": "2026-03-14",
					"note": DEMO_UPDATE_TRANSFER_NOTE,
				},
			),
			current_user,
			session,
			"agent-demo-update-transfer",
		)
	finally:
		_reset_authenticated_request_contexts()
	_run_pending_jobs()
	session.expire_all()


def _ensure_ledger_adjustment_task(
	session: Session,
	*,
	current_user: UserAccount,
	cash_account_id: int,
) -> None:
	if _task_exists(session, user_id=current_user.username, note_marker=DEMO_LEDGER_NOTE):
		return

	try:
		_set_agent_request_context(
			api_key_name=DEMO_ACTIVE_AGENT_NAME,
			agent_name=DEMO_ACTIVE_AGENT_NAME,
		)
		create_agent_task(
			AgentTaskCreate(
				task_type="CREATE_CASH_LEDGER_ADJUSTMENT",
				payload={
					"cash_account_id": cash_account_id,
					"amount": 8,
					"happened_on": "2026-03-14",
					"note": DEMO_LEDGER_NOTE,
				},
			),
			current_user,
			session,
			"agent-demo-ledger-adjustment",
		)
	finally:
		_reset_authenticated_request_contexts()
	_run_pending_jobs()
	session.expire_all()


def seed_agent_workspace_demo(
	session: Session,
	*,
	user_id: str = "admin",
) -> AgentWorkspaceDemoSeedSummary:
	current_user = session.get(UserAccount, user_id)
	if current_user is None:
		raise ValueError(f"User '{user_id}' does not exist.")

	support_account = _ensure_support_account(session, current_user=current_user)
	active_access_token = _ensure_active_agent_registration(session, current_user=current_user)
	_ensure_inactive_agent_registration(session, current_user=current_user)
	direct_account = _ensure_direct_api_account(
		session,
		current_user=current_user,
		active_access_token=active_access_token,
	)
	_ensure_create_transfer_task(
		session,
		current_user=current_user,
		source_account_id=support_account.id or 0,
		target_account_id=direct_account.id or 0,
	)
	_ensure_update_transfer_task(
		session,
		current_user=current_user,
		transfer_id=_resolve_demo_transfer_id(session, user_id=current_user.username),
	)
	_ensure_ledger_adjustment_task(
		session,
		current_user=current_user,
		cash_account_id=direct_account.id or 0,
	)

	session.expire_all()
	registrations = list_agent_registrations(current_user, session, include_all_users=False)
	tasks = list_agent_tasks(current_user, session, limit=50)
	records = list_asset_records(current_user, session, limit=200)
	agent_records = [record for record in records if record.source == "AGENT"]
	direct_api_records = [record for record in records if record.source == "API"]

	return AgentWorkspaceDemoSeedSummary(
		registrations=len(registrations),
		active_registrations=sum(
			1 for registration in registrations if registration.status == "ACTIVE"
		),
		tasks=len(tasks),
		direct_api_records=len(direct_api_records),
		agent_records=len(agent_records),
	)


__all__ = [
	"AgentWorkspaceDemoSeedSummary",
	"seed_agent_workspace_demo",
]
