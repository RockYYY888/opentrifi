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
from app.services.inbox_service import _load_hidden_message_ids
from app.services.release_note_service import _ensure_release_note_deliveries_for_user
from app.services.service_context import SessionDependency

def _normalize_feedback_choice(
	value: str | None,
	allowed_values: tuple[str, ...],
	fallback: str,
) -> str:
	if value is None:
		return fallback

	normalized = value.strip().upper()
	if normalized in allowed_values:
		return normalized
	return fallback

def _is_system_feedback_item(feedback: UserFeedback) -> bool:
	category = _normalize_feedback_choice(
		feedback.category,
		FEEDBACK_CATEGORIES,
		"USER_REQUEST",
	)
	source = _normalize_feedback_choice(
		feedback.source,
		FEEDBACK_SOURCES,
		"USER",
	)
	return category.startswith("SYSTEM_") or source != "USER"

def _is_user_feedback_item(feedback: UserFeedback) -> bool:
	return not _is_system_feedback_item(feedback)

def _derive_feedback_status(feedback: UserFeedback) -> str:
	if feedback.resolved_at is not None:
		return "RESOLVED"

	status = _normalize_feedback_choice(
		feedback.status,
		FEEDBACK_STATUSES,
		"OPEN",
	)
	if status == "RESOLVED":
		return "OPEN"
	if status == "ACKED":
		return "ACKED"
	if status == "IN_PROGRESS":
		return "IN_PROGRESS"
	if status == "SILENCED":
		return "SILENCED"
	if feedback.replied_at is not None:
		return "IN_PROGRESS"
	return "OPEN"

def _feedback_sort_key(feedback: UserFeedback) -> tuple[int, int, int, int, int, int, int]:
	status_rank = {
		"OPEN": 0,
		"ACKED": 1,
		"IN_PROGRESS": 2,
		"SILENCED": 3,
		"RESOLVED": 4,
	}
	priority_rank = {
		"HIGH": 0,
		"MEDIUM": 1,
		"LOW": 2,
	}
	status_value = _derive_feedback_status(feedback)
	priority_value = _normalize_feedback_choice(
		feedback.priority,
		FEEDBACK_PRIORITIES,
		"MEDIUM",
	)
	created_at = feedback.created_at
	if created_at.tzinfo is None:
		created_at = created_at.replace(tzinfo=timezone.utc)
	created_at = created_at.astimezone(timezone.utc)
	return (
		status_rank.get(status_value, 3),
		priority_rank.get(priority_value, 3),
		-created_at.toordinal(),
		-created_at.hour,
		-created_at.minute,
		-created_at.second,
		-created_at.microsecond,
	)

def _to_feedback_read(feedback: UserFeedback) -> UserFeedbackRead:
	category = _normalize_feedback_choice(
		feedback.category,
		FEEDBACK_CATEGORIES,
		"USER_REQUEST",
	)
	priority = _normalize_feedback_choice(
		feedback.priority,
		FEEDBACK_PRIORITIES,
		"MEDIUM",
	)
	source = _normalize_feedback_choice(
		feedback.source,
		FEEDBACK_SOURCES,
		"USER",
	)
	status = _derive_feedback_status(feedback)
	return UserFeedbackRead(
		id=feedback.id or 0,
		user_id=feedback.user_id,
		message=feedback.message,
		category=category,
		priority=priority,
		source=source,
		status=status,
		is_system=_is_system_feedback_item(feedback),
		reply_message=feedback.reply_message,
		replied_at=feedback.replied_at,
		replied_by=feedback.replied_by,
		reply_seen_at=feedback.reply_seen_at,
		resolved_at=feedback.resolved_at,
		closed_by=feedback.closed_by,
		created_at=feedback.created_at,
	)

def _to_admin_feedback_read(feedback: UserFeedback) -> AdminFeedbackRead:
	base_read = _to_feedback_read(feedback)
	return AdminFeedbackRead(
		**base_read.model_dump(),
		assignee=feedback.assignee,
		acknowledged_at=feedback.acknowledged_at,
		acknowledged_by=feedback.acknowledged_by,
		ack_deadline=feedback.ack_deadline,
		internal_note=feedback.internal_note,
		internal_note_updated_at=feedback.internal_note_updated_at,
		internal_note_updated_by=feedback.internal_note_updated_by,
		fingerprint=feedback.fingerprint,
		dedupe_window_minutes=feedback.dedupe_window_minutes,
		occurrence_count=max(1, feedback.occurrence_count),
		last_seen_at=feedback.last_seen_at,
	)

def _parse_feedback_filter_values(
	raw_value: str | None,
	*,
	allowed_values: tuple[str, ...],
	field_name: str,
) -> set[str] | None:
	if raw_value is None:
		return None

	parsed_values = {
		item.strip().upper()
		for item in raw_value.split(",")
		if item.strip()
	}
	if not parsed_values:
		return None

	invalid_values = sorted(value for value in parsed_values if value not in allowed_values)
	if invalid_values:
		raise HTTPException(
			status_code=400,
			detail=(
				f"{field_name} contains invalid values: {', '.join(invalid_values)}. "
				f"Allowed: {', '.join(allowed_values)}"
			),
		)
	return parsed_values

def _apply_feedback_status_transition(
	feedback: UserFeedback,
	*,
	target_status: str,
	actor_username: str,
) -> None:
	is_system_item = _is_system_feedback_item(feedback)
	if target_status == "SILENCED" and not is_system_item:
		raise HTTPException(status_code=400, detail="仅系统工单可设置为 SILENCED。")

	now = utc_now()
	if target_status == "RESOLVED":
		if feedback.resolved_at is None:
			feedback.resolved_at = now
		feedback.closed_by = actor_username
		feedback.status = "RESOLVED"
		return

	if feedback.resolved_at is not None:
		feedback.resolved_at = None
		feedback.closed_by = None

	if target_status == "ACKED":
		feedback.acknowledged_at = now
		feedback.acknowledged_by = actor_username
	elif target_status == "OPEN":
		feedback.acknowledged_at = None
		feedback.acknowledged_by = None

	feedback.status = target_status

def _build_admin_feedback_list(
	*,
	items: list[UserFeedback],
	status_filter: set[str] | None,
	priority_filter: set[str] | None,
	page: int,
	page_size: int,
) -> AdminFeedbackListRead:
	filtered_items = items
	if status_filter is not None:
		filtered_items = [
			item for item in filtered_items if _derive_feedback_status(item) in status_filter
		]
	if priority_filter is not None:
		filtered_items = [
			item
			for item in filtered_items
			if _normalize_feedback_choice(item.priority, FEEDBACK_PRIORITIES, "MEDIUM")
			in priority_filter
		]

	sorted_items = sorted(filtered_items, key=_feedback_sort_key)
	total_items = len(sorted_items)
	offset = (page - 1) * page_size
	page_items = sorted_items[offset: offset + page_size]
	return AdminFeedbackListRead(
		items=[_to_admin_feedback_read(item) for item in page_items],
		total=total_items,
		page=page,
		page_size=page_size,
		has_more=offset + page_size < total_items,
	)
