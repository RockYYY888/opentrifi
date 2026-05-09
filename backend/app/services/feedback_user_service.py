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
	_is_system_feedback_item,
	_normalize_feedback_choice,
	_to_feedback_read,
)
from app.services.inbox_service import _load_hidden_message_ids
from app.services.release_note_service import _ensure_release_note_deliveries_for_user
from app.services.service_context import SessionDependency
from app.services.sql_expression import sql_expr

def submit_feedback(
	payload: UserFeedbackCreate,
	current_user: CurrentUserDependency,
	session: SessionDependency,
) -> UserFeedbackRead:
	requested_category = _normalize_feedback_choice(
		payload.category,
		FEEDBACK_CATEGORIES,
		"USER_REQUEST",
	) if payload.category is not None else None
	requested_priority = _normalize_feedback_choice(
		payload.priority,
		FEEDBACK_PRIORITIES,
		"MEDIUM",
	) if payload.priority is not None else None
	requested_source = _normalize_feedback_choice(
		payload.source,
		FEEDBACK_SOURCES,
		"USER",
	) if payload.source is not None else None
	requested_fingerprint = (payload.fingerprint or "").strip() or None
	requested_dedupe_window_minutes = payload.dedupe_window_minutes

	if current_user.username == "admin":
		category = requested_category
		source = requested_source

		if category is None:
			if source in {"SYSTEM", "API_MONITOR", "TRADING_AGENT"}:
				category = "SYSTEM_TASK"
			else:
				category = "USER_REQUEST"

		if source is None:
			source = "SYSTEM" if category.startswith("SYSTEM_") else "ADMIN"

		# System feedback must never remain USER source, otherwise it can hit user daily limit.
		if category.startswith("SYSTEM_") and source == "USER":
			source = "SYSTEM"

		if category == "USER_REQUEST" and source in {"SYSTEM", "API_MONITOR", "TRADING_AGENT"}:
			category = "SYSTEM_TASK"

		default_priority = "MEDIUM"
		if category == "SYSTEM_ALERT":
			default_priority = "HIGH"
		elif category == "SYSTEM_HEARTBEAT":
			default_priority = "LOW"
		priority = requested_priority or default_priority
	else:
		category = "USER_REQUEST"
		priority = "MEDIUM"
		source = "USER"
		requested_fingerprint = None
		requested_dedupe_window_minutes = None

	if source == "USER" and category == "USER_REQUEST":
		day_start, day_end = _feedback_day_window()
		submission_count = len(
			list(
				session.exec(
					select(UserFeedback.id).where(
						UserFeedback.user_id == current_user.username,
						UserFeedback.created_at >= day_start,
						UserFeedback.created_at < day_end,
					),
				),
			),
		)
		if submission_count >= MAX_DAILY_FEEDBACK_SUBMISSIONS:
			raise HTTPException(status_code=429, detail="今日反馈次数已达上限，请明天再试。")

	now = utc_now()
	if (
		current_user.username == "admin"
		and source in {"SYSTEM", "API_MONITOR", "TRADING_AGENT"}
		and requested_fingerprint is not None
		and requested_dedupe_window_minutes is not None
	):
		window_start = now - timedelta(minutes=requested_dedupe_window_minutes)
		existing_feedback = session.exec(
			select(UserFeedback)
			.where(
				UserFeedback.user_id == current_user.username,
				UserFeedback.source == source,
				UserFeedback.category == category,
				UserFeedback.fingerprint == requested_fingerprint,
				UserFeedback.created_at >= window_start,
			)
			.order_by(sql_expr(UserFeedback.created_at).desc(), sql_expr(UserFeedback.id).desc()),
		).first()
		if existing_feedback is not None:
			existing_feedback.occurrence_count = max(1, existing_feedback.occurrence_count) + 1
			existing_feedback.last_seen_at = now
			if existing_feedback.resolved_at is not None and _is_system_feedback_item(existing_feedback):
				existing_feedback.resolved_at = None
				existing_feedback.closed_by = None
				existing_feedback.status = "OPEN"
			session.add(existing_feedback)
			session.commit()
			session.refresh(existing_feedback)
			return _to_feedback_read(existing_feedback)

	auto_resolve = (
		category == "SYSTEM_HEARTBEAT"
		and priority == "LOW"
		and source in {"SYSTEM", "API_MONITOR", "TRADING_AGENT"}
	)
	feedback = UserFeedback(
		user_id=current_user.username,
		message=payload.message,
		category=category,
		priority=priority,
		source=source,
		status="RESOLVED" if auto_resolve else "OPEN",
		resolved_at=now if auto_resolve else None,
		closed_by="system-auto" if auto_resolve else None,
		fingerprint=requested_fingerprint,
		dedupe_window_minutes=requested_dedupe_window_minutes,
		occurrence_count=1,
		last_seen_at=now,
	)
	session.add(feedback)
	session.commit()
	session.refresh(feedback)
	return _to_feedback_read(feedback)

def list_feedback_for_current_user(
	current_user: CurrentUserDependency,
	session: SessionDependency,
	_: TokenDependency,
) -> list[UserFeedbackRead]:
	hidden_feedback_ids = _load_hidden_message_ids(
		session,
		user_id=current_user.username,
		message_kind="FEEDBACK",
	)
	feedback_items = list(
		session.exec(
			select(UserFeedback)
			.where(UserFeedback.user_id == current_user.username)
			.order_by(sql_expr(UserFeedback.created_at).desc()),
		),
	)
	visible_feedback_items = [
		feedback for feedback in feedback_items if (feedback.id or 0) not in hidden_feedback_ids
	]
	return [_to_feedback_read(feedback) for feedback in visible_feedback_items]

def mark_feedback_seen_for_current_user(
	current_user: CurrentUserDependency,
	session: SessionDependency,
	_: TokenDependency,
) -> ActionMessageRead:
	hidden_feedback_ids = _load_hidden_message_ids(
		session,
		user_id=current_user.username,
		message_kind="FEEDBACK",
	)
	feedback_items = list(
		session.exec(
			select(UserFeedback).where(
				UserFeedback.user_id == current_user.username,
				sql_expr(UserFeedback.replied_at).is_not(None),
				sql_expr(UserFeedback.reply_seen_at).is_(None),
			),
		),
	)
	feedback_items = [
		item for item in feedback_items if (item.id or 0) not in hidden_feedback_ids
	]
	if not feedback_items:
		return ActionMessageRead(message="没有新的回复。")

	now = utc_now()
	for feedback in feedback_items:
		feedback.reply_seen_at = now
		session.add(feedback)

	session.commit()
	return ActionMessageRead(message="消息已标记为已读。")

def get_feedback_summary(
	current_user: CurrentUserDependency,
	session: SessionDependency,
	_: TokenDependency,
) -> FeedbackSummaryRead:
	hidden_feedback_ids = _load_hidden_message_ids(
		session,
		user_id=current_user.username,
		message_kind="FEEDBACK",
	)
	if current_user.username == "admin":
		_ensure_release_note_deliveries_for_user(session, current_user.username)
		hidden_release_note_delivery_ids = _load_hidden_message_ids(
			session,
			user_id=current_user.username,
			message_kind="RELEASE_NOTE",
		)
		feedback_inbox_count = len(
			[
				feedback_id
				for feedback_id in session.exec(
					select(UserFeedback.id).where(sql_expr(UserFeedback.resolved_at).is_(None)),
				)
				if feedback_id is not None and int(feedback_id) not in hidden_feedback_ids
			],
		)
		release_note_unread_count = 1 if any(
			delivery_id is not None and int(delivery_id) not in hidden_release_note_delivery_ids
			for delivery_id in session.exec(
				select(ReleaseNoteDelivery.id).where(
					ReleaseNoteDelivery.user_id == current_user.username,
					sql_expr(ReleaseNoteDelivery.seen_at).is_(None),
				),
			)
		) else 0
		return FeedbackSummaryRead(
			inbox_count=feedback_inbox_count + release_note_unread_count,
			mode="admin-open",
		)

	_ensure_release_note_deliveries_for_user(session, current_user.username)
	hidden_release_note_delivery_ids = _load_hidden_message_ids(
		session,
		user_id=current_user.username,
		message_kind="RELEASE_NOTE",
	)
	feedback_unread_count = len(
		[
			feedback_id
			for feedback_id in session.exec(
				select(UserFeedback.id).where(
					UserFeedback.user_id == current_user.username,
					sql_expr(UserFeedback.replied_at).is_not(None),
					sql_expr(UserFeedback.reply_seen_at).is_(None),
				),
			)
			if feedback_id is not None and int(feedback_id) not in hidden_feedback_ids
		],
	)
	release_note_unread_count = 1 if any(
		delivery_id is not None and int(delivery_id) not in hidden_release_note_delivery_ids
		for delivery_id in session.exec(
			select(ReleaseNoteDelivery.id).where(
				ReleaseNoteDelivery.user_id == current_user.username,
				sql_expr(ReleaseNoteDelivery.seen_at).is_(None),
			),
		)
	) else 0
	return FeedbackSummaryRead(
		inbox_count=feedback_unread_count + release_note_unread_count,
		mode="user-unread",
	)

def hide_inbox_message_for_current_user(
	payload: InboxMessageHideCreate,
	current_user: CurrentUserDependency,
	session: SessionDependency,
	_: TokenDependency,
) -> ActionMessageRead:
	message_kind = payload.message_kind
	message_id = payload.message_id

	if message_kind == "FEEDBACK":
		feedback = session.get(UserFeedback, message_id)
		if feedback is None:
			raise HTTPException(status_code=404, detail="消息不存在。")
		if current_user.username != "admin" and feedback.user_id != current_user.username:
			raise HTTPException(status_code=403, detail="无权移除该消息。")
	elif message_kind == "RELEASE_NOTE":
		delivery = session.get(ReleaseNoteDelivery, message_id)
		if delivery is None:
			raise HTTPException(status_code=404, detail="消息不存在。")
		if delivery.user_id != current_user.username:
			raise HTTPException(status_code=403, detail="无权移除该消息。")
	else:
		raise HTTPException(status_code=400, detail="message_kind 无效。")

	existing_visibility = session.exec(
		select(InboxMessageVisibility).where(
			InboxMessageVisibility.user_id == current_user.username,
			InboxMessageVisibility.message_kind == message_kind,
			InboxMessageVisibility.message_id == message_id,
		),
	).first()
	if existing_visibility is not None:
		return ActionMessageRead(message="消息已从当前列表移除。")

	visibility = InboxMessageVisibility(
		user_id=current_user.username,
		message_kind=message_kind,
		message_id=message_id,
	)
	session.add(visibility)
	session.commit()
	return ActionMessageRead(message="消息已从当前列表移除。")
