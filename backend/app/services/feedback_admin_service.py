from __future__ import annotations
from datetime import timedelta, timezone
from fastapi import HTTPException, Query
from sqlmodel import select
from app.models import (
	FEEDBACK_CATEGORIES,
	FEEDBACK_PRIORITIES,
	FEEDBACK_SOURCES,
	FEEDBACK_STATUSES,
	InboxMessageVisibility,
	ReleaseNoteDelivery,
	UserFeedback,
	utc_now,
)
from app.schemas import (
    ActionMessageRead,
    AdminFeedbackAcknowledgeUpdate,
    AdminFeedbackClassifyUpdate,
    AdminFeedbackListRead,
    AdminFeedbackRead,
    AdminFeedbackReplyUpdate,
    FeedbackSummaryRead,
    InboxMessageHideCreate,
    UserFeedbackCreate,
    UserFeedbackRead,
)
from app.services.auth_service import CurrentUserDependency, TokenDependency
from app.services.common_service import (
    FEEDBACK_TIMEZONE,
    MAX_DAILY_FEEDBACK_SUBMISSIONS,
    _feedback_day_window,
    _require_admin_user,
)
from app.services.feedback_model_service import (
	_apply_feedback_status_transition,
	_build_admin_feedback_list,
	_feedback_sort_key,
	_is_system_feedback_item,
	_is_user_feedback_item,
	_parse_feedback_filter_values,
	_to_admin_feedback_read,
	_to_feedback_read,
)
from app.services.inbox_service import _load_hidden_message_ids
from app.services.release_note_service import _ensure_release_note_deliveries_for_user
from app.services.service_context import SessionDependency

def list_feedback_for_admin(
	current_user: CurrentUserDependency,
	session: SessionDependency,
	_: TokenDependency,
) -> list[UserFeedbackRead]:
	_require_admin_user(current_user)
	feedback_items = list(session.exec(select(UserFeedback)))
	feedback_items = sorted(feedback_items, key=_feedback_sort_key)
	return [
		_to_feedback_read(feedback)
		for feedback in feedback_items
	]

def list_user_feedback_for_admin(
	current_user: CurrentUserDependency,
	session: SessionDependency,
	_: TokenDependency,
	page: int = Query(default=1, ge=1),
	page_size: int = Query(default=50, ge=1, le=200),
	status: str | None = Query(default=None),
	priority: str | None = Query(default=None),
	include_hidden: bool = Query(default=False),
) -> AdminFeedbackListRead:
	_require_admin_user(current_user)
	status_filter = _parse_feedback_filter_values(
		status,
		allowed_values=FEEDBACK_STATUSES,
		field_name="status",
	)
	priority_filter = _parse_feedback_filter_values(
		priority,
		allowed_values=FEEDBACK_PRIORITIES,
		field_name="priority",
	)
	hidden_feedback_ids = set[int]()
	if not include_hidden:
		hidden_feedback_ids = _load_hidden_message_ids(
			session,
			user_id=current_user.username,
			message_kind="FEEDBACK",
		)
	feedback_items = [
		feedback
		for feedback in session.exec(select(UserFeedback))
		if _is_user_feedback_item(feedback) and (feedback.id or 0) not in hidden_feedback_ids
	]
	return _build_admin_feedback_list(
		items=feedback_items,
		status_filter=status_filter,
		priority_filter=priority_filter,
		page=page,
		page_size=page_size,
	)

def list_system_feedback_for_admin(
	current_user: CurrentUserDependency,
	session: SessionDependency,
	_: TokenDependency,
	page: int = Query(default=1, ge=1),
	page_size: int = Query(default=50, ge=1, le=200),
	status: str | None = Query(default=None),
	priority: str | None = Query(default=None),
	include_hidden: bool = Query(default=False),
) -> AdminFeedbackListRead:
	_require_admin_user(current_user)
	status_filter = _parse_feedback_filter_values(
		status,
		allowed_values=FEEDBACK_STATUSES,
		field_name="status",
	)
	priority_filter = _parse_feedback_filter_values(
		priority,
		allowed_values=FEEDBACK_PRIORITIES,
		field_name="priority",
	)
	hidden_feedback_ids = set[int]()
	if not include_hidden:
		hidden_feedback_ids = _load_hidden_message_ids(
			session,
			user_id=current_user.username,
			message_kind="FEEDBACK",
		)
	feedback_items = [
		feedback
		for feedback in session.exec(select(UserFeedback))
		if _is_system_feedback_item(feedback) and (feedback.id or 0) not in hidden_feedback_ids
	]
	return _build_admin_feedback_list(
		items=feedback_items,
		status_filter=status_filter,
		priority_filter=priority_filter,
		page=page,
		page_size=page_size,
	)

def reply_to_feedback_for_admin(
	feedback_id: int,
	payload: AdminFeedbackReplyUpdate,
	current_user: CurrentUserDependency,
	session: SessionDependency,
	_: TokenDependency,
) -> UserFeedbackRead:
	_require_admin_user(current_user)
	feedback = session.get(UserFeedback, feedback_id)
	if feedback is None:
		raise HTTPException(status_code=404, detail="反馈不存在。")
	if _is_system_feedback_item(feedback):
		raise HTTPException(
			status_code=400,
			detail="系统工单无需回复，请直接关闭或调整状态。",
		)

	now = utc_now()
	feedback.reply_message = payload.reply_message
	feedback.replied_at = now
	feedback.replied_by = current_user.username
	feedback.reply_seen_at = None
	if payload.close and feedback.resolved_at is None:
		feedback.resolved_at = now
		feedback.closed_by = current_user.username
		feedback.status = "RESOLVED"
	else:
		feedback.status = "IN_PROGRESS"
	session.add(feedback)
	session.commit()
	session.refresh(feedback)

	return _to_admin_feedback_read(feedback)

def close_feedback_for_admin(
	feedback_id: int,
	current_user: CurrentUserDependency,
	session: SessionDependency,
	_: TokenDependency,
) -> UserFeedbackRead:
	_require_admin_user(current_user)
	feedback = session.get(UserFeedback, feedback_id)
	if feedback is None:
		raise HTTPException(status_code=404, detail="反馈不存在。")

	if feedback.resolved_at is None:
		feedback.resolved_at = utc_now()
		feedback.closed_by = current_user.username
		feedback.status = "RESOLVED"
		session.add(feedback)
		session.commit()
		session.refresh(feedback)

	return _to_admin_feedback_read(feedback)

def acknowledge_feedback_for_admin(
	feedback_id: int,
	payload: AdminFeedbackAcknowledgeUpdate,
	current_user: CurrentUserDependency,
	session: SessionDependency,
	_: TokenDependency,
) -> AdminFeedbackRead:
	_require_admin_user(current_user)
	feedback = session.get(UserFeedback, feedback_id)
	if feedback is None:
		raise HTTPException(status_code=404, detail="反馈不存在。")
	if feedback.resolved_at is not None:
		raise HTTPException(status_code=400, detail="已关闭工单无法确认。")

	feedback.status = "ACKED"
	feedback.acknowledged_at = utc_now()
	feedback.acknowledged_by = current_user.username
	if "assignee" in payload.model_fields_set:
		feedback.assignee = payload.assignee
	if "ack_deadline" in payload.model_fields_set:
		feedback.ack_deadline = payload.ack_deadline
	if "internal_note" in payload.model_fields_set:
		feedback.internal_note = payload.internal_note
		feedback.internal_note_updated_at = utc_now()
		feedback.internal_note_updated_by = current_user.username
	session.add(feedback)
	session.commit()
	session.refresh(feedback)
	return _to_admin_feedback_read(feedback)

def classify_feedback_for_admin(
	feedback_id: int,
	payload: AdminFeedbackClassifyUpdate,
	current_user: CurrentUserDependency,
	session: SessionDependency,
	_: TokenDependency,
) -> UserFeedbackRead:
	_require_admin_user(current_user)
	feedback = session.get(UserFeedback, feedback_id)
	if feedback is None:
		raise HTTPException(status_code=404, detail="反馈不存在。")

	if "category" in payload.model_fields_set and payload.category is not None:
		feedback.category = payload.category
	if "priority" in payload.model_fields_set and payload.priority is not None:
		feedback.priority = payload.priority
	if "source" in payload.model_fields_set and payload.source is not None:
		feedback.source = payload.source
	if "status" in payload.model_fields_set:
		_apply_feedback_status_transition(
			feedback,
			target_status=payload.status or "OPEN",
			actor_username=current_user.username,
		)
	if "assignee" in payload.model_fields_set:
		feedback.assignee = payload.assignee
	if "ack_deadline" in payload.model_fields_set:
		feedback.ack_deadline = payload.ack_deadline
	if "internal_note" in payload.model_fields_set:
		feedback.internal_note = payload.internal_note
		feedback.internal_note_updated_at = utc_now()
		feedback.internal_note_updated_by = current_user.username

	session.add(feedback)
	session.commit()
	session.refresh(feedback)
	return _to_admin_feedback_read(feedback)
