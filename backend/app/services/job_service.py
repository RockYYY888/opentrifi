from __future__ import annotations

import asyncio
from contextlib import suppress
import json
import logging
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import update
from sqlmodel import Session, select

from app import runtime_state
from app.database import engine
from app.models import AgentTask, OutboxJob, UserAccount, utc_now
from app.schemas import (
	ActionMessageRead,
	CashLedgerAdjustmentCreate,
	CashLedgerAdjustmentUpdate,
	CashTransferCreate,
	CashTransferUpdate,
	SecurityHoldingTransactionCreate,
	SecurityHoldingTransactionUpdate,
)
logger = logging.getLogger(__name__)

SNAPSHOT_REBUILD_JOB_TYPE = "SNAPSHOT_REBUILD"
AGENT_TASK_EXECUTION_JOB_TYPE = "AGENT_TASK_EXECUTION"
PENDING_JOB_STATUS = "PENDING"
RUNNING_JOB_STATUS = "RUNNING"
DONE_JOB_STATUS = "DONE"
FAILED_JOB_STATUS = "FAILED"
JOB_POLL_INTERVAL = 0.2


def _normalize_job_user_id(user_id: str | None) -> str | None:
	if user_id is None:
		return None
	normalized = user_id.strip()
	return normalized or None


def _serialize_job_payload(payload: dict[str, Any]) -> str:
	return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _touch_job(job: OutboxJob, *, now: datetime | None = None) -> None:
	job.updated_at = now or utc_now()


def _load_active_job_by_dedup_key(
	session: Session,
	*,
	dedup_key: str,
) -> OutboxJob | None:
	return session.exec(
		select(OutboxJob)
		.where(OutboxJob.dedup_key == dedup_key)
		.where(OutboxJob.status.in_((PENDING_JOB_STATUS, RUNNING_JOB_STATUS)))
		.order_by(OutboxJob.id.desc()),
	).first()


def _enqueue_job(
	session: Session,
	*,
	job_type: str,
	user_id: str | None,
	payload: dict[str, Any],
	dedup_key: str | None = None,
) -> OutboxJob:
	if dedup_key is None:
		job = OutboxJob(
			user_id=_normalize_job_user_id(user_id),
			job_type=job_type,
			status=PENDING_JOB_STATUS,
			dedup_key=dedup_key,
			payload_json=_serialize_job_payload(payload),
		)
		session.add(job)
		session.flush()
		return job

	with runtime_state.redis_lock(f"job-enqueue:{dedup_key}", timeout=10, blocking_timeout=10):
		existing = _load_active_job_by_dedup_key(session, dedup_key=dedup_key)
		if existing is not None:
			return existing

		job = OutboxJob(
			user_id=_normalize_job_user_id(user_id),
			job_type=job_type,
			status=PENDING_JOB_STATUS,
			dedup_key=dedup_key,
			payload_json=_serialize_job_payload(payload),
		)
		session.add(job)
		session.flush()
		return job


def enqueue_user_portfolio_snapshot_rebuild(session: Session, user_id: str) -> OutboxJob:
	normalized_user_id = _normalize_job_user_id(user_id)
	if normalized_user_id is None:
		raise ValueError("user_id is required for snapshot rebuild jobs.")
	return _enqueue_job(
		session,
		job_type=SNAPSHOT_REBUILD_JOB_TYPE,
		user_id=normalized_user_id,
		payload={"user_id": normalized_user_id},
		dedup_key=f"snapshot-rebuild:{normalized_user_id}",
	)


def enqueue_agent_task_execution(
	session: Session,
	*,
	user_id: str,
	agent_task_id: int,
) -> OutboxJob:
	normalized_user_id = _normalize_job_user_id(user_id)
	if normalized_user_id is None:
		raise ValueError("user_id is required for agent task jobs.")
	return _enqueue_job(
		session,
		job_type=AGENT_TASK_EXECUTION_JOB_TYPE,
		user_id=normalized_user_id,
		payload={
			"user_id": normalized_user_id,
			"agent_task_id": agent_task_id,
		},
		dedup_key=f"agent-task:{agent_task_id}",
	)


def _claim_next_pending_job(session: Session) -> OutboxJob | None:
	now = utc_now()
	job_id_row = session.exec(
		update(OutboxJob)
		.where(
			OutboxJob.id
			== select(OutboxJob.id)
			.where(OutboxJob.status == PENDING_JOB_STATUS)
			.where(OutboxJob.available_at <= now)
			.order_by(OutboxJob.created_at.asc(), OutboxJob.id.asc())
			.limit(1)
			.scalar_subquery(),
		)
		.where(OutboxJob.status == PENDING_JOB_STATUS)
		.values(
			status=RUNNING_JOB_STATUS,
			started_at=now,
			completed_at=None,
			last_error=None,
			attempt_count=OutboxJob.attempt_count + 1,
			updated_at=now,
		)
		.returning(OutboxJob.id),
	).first()
	if job_id_row is None:
		session.rollback()
		return None

	session.commit()
	job_id = int(job_id_row[0])
	return session.get(OutboxJob, job_id)


def _complete_job(session: Session, job_id: int) -> None:
	job = session.get(OutboxJob, job_id)
	if job is None:
		return
	job.status = DONE_JOB_STATUS
	job.completed_at = utc_now()
	job.last_error = None
	_touch_job(job)
	session.add(job)
	session.commit()


def _fail_job(session: Session, job_id: int, exc: Exception) -> None:
	job = session.get(OutboxJob, job_id)
	if job is None:
		return
	job.status = FAILED_JOB_STATUS
	job.completed_at = utc_now()
	job.last_error = (
		exc.detail
		if isinstance(exc, HTTPException) and isinstance(exc.detail, str)
		else str(exc)
	)[:1000]
	_touch_job(job)
	session.add(job)
	session.commit()


async def _process_snapshot_rebuild_job(session: Session, job: OutboxJob) -> None:
	from app.services.common_service import _invalidate_dashboard_cache
	from app.services.history_service import _process_pending_holding_history_sync_requests, _rebuild_user_portfolio_snapshots
	from app.services.history_sync_service import _has_holding_history_sync_pending

	payload = json.loads(job.payload_json)
	user_id = _normalize_job_user_id(payload.get("user_id") or job.user_id)
	if user_id is None:
		return

	processed_history = False
	while _has_holding_history_sync_pending(session, user_id):
		processed_history = True
		await _process_pending_holding_history_sync_requests(
			session,
			limit=1,
			user_id=user_id,
		)

	if not processed_history:
		await _rebuild_user_portfolio_snapshots(session, user_id)
		session.commit()

	_invalidate_dashboard_cache(user_id)


def _coerce_agent_result_payload(result: Any) -> dict[str, Any]:
	if hasattr(result, "model_dump"):
		return result.model_dump(mode="json")
	if isinstance(result, dict):
		return result
	raise TypeError("Unsupported agent task result payload.")


def _execute_agent_task_command(
	session: Session,
	*,
	task: AgentTask,
	current_user: UserAccount,
) -> dict[str, Any]:
	from app.services.cash_account_service import (
		create_cash_ledger_adjustment,
		create_cash_transfer,
		delete_cash_ledger_adjustment,
		update_cash_ledger_adjustment,
		update_cash_transfer,
	)
	from app.services.holding_transaction_service import (
		create_holding_transaction,
		update_holding_transaction,
	)

	payload = json.loads(task.input_json)
	if task.task_type == "CREATE_BUY_TRANSACTION":
		result = create_holding_transaction(
			SecurityHoldingTransactionCreate(
				side="BUY",
				**payload,
			),
			current_user,
			session,
			None,
		)
	elif task.task_type == "CREATE_SELL_TRANSACTION":
		result = create_holding_transaction(
			SecurityHoldingTransactionCreate(
				side="SELL",
				**payload,
			),
			current_user,
			session,
			None,
		)
	elif task.task_type == "UPDATE_HOLDING_TRANSACTION":
		transaction_id = int(payload.get("transaction_id") or 0)
		if transaction_id <= 0:
			raise HTTPException(status_code=422, detail="transaction_id 为必填项。")
		update_payload = dict(payload)
		update_payload.pop("transaction_id", None)
		result = update_holding_transaction(
			transaction_id,
			SecurityHoldingTransactionUpdate(**update_payload),
			current_user,
			session,
		)
	elif task.task_type == "CREATE_CASH_TRANSFER":
		result = create_cash_transfer(
			CashTransferCreate(**payload),
			current_user,
			session,
			None,
		)
	elif task.task_type == "UPDATE_CASH_TRANSFER":
		transfer_id = int(payload.get("transfer_id") or 0)
		if transfer_id <= 0:
			raise HTTPException(status_code=422, detail="transfer_id 为必填项。")
		update_payload = dict(payload)
		update_payload.pop("transfer_id", None)
		result = update_cash_transfer(
			transfer_id,
			CashTransferUpdate(**update_payload),
			current_user,
			session,
		)
	elif task.task_type == "CREATE_CASH_LEDGER_ADJUSTMENT":
		result = create_cash_ledger_adjustment(
			CashLedgerAdjustmentCreate(**payload),
			current_user,
			session,
			None,
		)
	elif task.task_type == "UPDATE_CASH_LEDGER_ADJUSTMENT":
		entry_id = int(payload.get("entry_id") or 0)
		if entry_id <= 0:
			raise HTTPException(status_code=422, detail="entry_id 为必填项。")
		update_payload = dict(payload)
		update_payload.pop("entry_id", None)
		result = update_cash_ledger_adjustment(
			entry_id,
			CashLedgerAdjustmentUpdate(**update_payload),
			current_user,
			session,
		)
	elif task.task_type == "DELETE_CASH_LEDGER_ADJUSTMENT":
		entry_id = int(payload.get("entry_id") or 0)
		if entry_id <= 0:
			raise HTTPException(status_code=422, detail="entry_id 为必填项。")
		delete_cash_ledger_adjustment(entry_id, current_user, session)
		result = ActionMessageRead(message="手工账本调整已删除。")
	else:
		raise HTTPException(status_code=422, detail="不支持的任务类型。")

	return _coerce_agent_result_payload(result)


async def _process_agent_task_execution_job(session: Session, job: OutboxJob) -> None:
	payload = json.loads(job.payload_json)
	task_id = int(payload.get("agent_task_id") or 0)
	if task_id <= 0:
		return

	task = session.get(AgentTask, task_id)
	if task is None:
		return
	if task.status == DONE_JOB_STATUS:
		return

	current_user = session.get(UserAccount, task.user_id)
	if current_user is None:
		raise HTTPException(status_code=404, detail="Agent task user not found.")

	task.status = RUNNING_JOB_STATUS
	task.error_message = None
	task.completed_at = None
	task.result_json = None
	task.updated_at = utc_now()
	session.add(task)
	session.commit()
	session.refresh(task)
	try:
		result_payload = await asyncio.to_thread(
			_execute_agent_task_command_in_new_session,
			task.id or 0,
		)
	except Exception as exc:
		task = session.get(AgentTask, task_id)
		if task is not None:
			task.status = FAILED_JOB_STATUS
			task.error_message = (
				exc.detail
				if isinstance(exc, HTTPException) and isinstance(exc.detail, str)
				else str(exc)
			)[:1000]
			task.completed_at = utc_now()
			task.updated_at = utc_now()
			session.add(task)
			session.commit()
		raise

	task = session.get(AgentTask, task_id)
	if task is None:
		return
	task.status = DONE_JOB_STATUS
	task.result_json = json.dumps(result_payload, sort_keys=True, ensure_ascii=False)
	task.error_message = None
	task.completed_at = utc_now()
	task.updated_at = utc_now()
	session.add(task)
	session.commit()


def _execute_agent_task_command_in_new_session(task_id: int) -> dict[str, Any]:
	with Session(engine) as session:
		task = session.get(AgentTask, task_id)
		if task is None:
			raise HTTPException(status_code=404, detail="Agent task not found.")
		current_user = session.get(UserAccount, task.user_id)
		if current_user is None:
			raise HTTPException(status_code=404, detail="Agent task user not found.")

		context_token = runtime_state.current_agent_task_id_context.set(task.id or 0)
		source_token = runtime_state.current_actor_source_context.set(task.request_source)
		api_key_name_token = runtime_state.current_api_key_name_context.set(task.api_key_name)
		agent_name_token = runtime_state.current_agent_name_context.set(task.agent_name)
		try:
			return _execute_agent_task_command(
				session,
				task=task,
				current_user=current_user,
			)
		finally:
			runtime_state.current_agent_name_context.reset(agent_name_token)
			runtime_state.current_api_key_name_context.reset(api_key_name_token)
			runtime_state.current_actor_source_context.reset(source_token)
			runtime_state.current_agent_task_id_context.reset(context_token)


async def _process_claimed_job(job_id: int) -> None:
	with Session(engine) as session:
		job = session.get(OutboxJob, job_id)
		if job is None:
			return
		try:
			if job.job_type == SNAPSHOT_REBUILD_JOB_TYPE:
				await _process_snapshot_rebuild_job(session, job)
			elif job.job_type == AGENT_TASK_EXECUTION_JOB_TYPE:
				await _process_agent_task_execution_job(session, job)
			else:
				raise ValueError(f"Unsupported outbox job type: {job.job_type}")
		except Exception as exc:  # pragma: no cover - defensive worker path
			logger.exception("Background job %s failed.", job_id)
			session.rollback()
			_fail_job(session, job_id, exc)
			return

		_complete_job(session, job_id)


async def process_next_background_job() -> bool:
	with Session(engine) as session:
		job = _claim_next_pending_job(session)
		if job is None:
			return False
		job_id = job.id or 0

	await _process_claimed_job(job_id)
	return True


async def process_all_pending_background_jobs(*, limit: int = 100) -> int:
	processed = 0
	while processed < limit:
		if not await process_next_background_job():
			break
		processed += 1
	return processed


def reset_running_jobs_to_pending() -> None:
	with Session(engine) as session:
		running_jobs = list(
			session.exec(
				select(OutboxJob).where(OutboxJob.status == RUNNING_JOB_STATUS),
			),
		)
		if not running_jobs:
			return
		for job in running_jobs:
			job.status = PENDING_JOB_STATUS
			job.started_at = None
			job.completed_at = None
			_touch_job(job)
			session.add(job)
		session.commit()


async def background_job_worker() -> None:
	while True:
		processed = await process_next_background_job()
		if not processed:
			await asyncio.sleep(JOB_POLL_INTERVAL)


def start_background_job_worker() -> asyncio.Task[None]:
	reset_running_jobs_to_pending()
	if (
		runtime_state.background_job_worker_task is None
		or runtime_state.background_job_worker_task.done()
	):
		runtime_state.background_job_worker_task = asyncio.create_task(background_job_worker())
	return runtime_state.background_job_worker_task


async def stop_background_job_worker() -> None:
	if runtime_state.background_job_worker_task is None:
		return
	runtime_state.background_job_worker_task.cancel()
	with suppress(asyncio.CancelledError):
		await runtime_state.background_job_worker_task
	runtime_state.background_job_worker_task = None
