from __future__ import annotations

import json
from typing import Final

from fastapi import HTTPException
from sqlmodel import Session, select

from app.models import ReleaseNote, ReleaseNoteDelivery, UserAccount, utc_now
from app.schemas import (
	ActionMessageRead,
	ReleaseNoteCreate,
	ReleaseNoteDeliveryRead,
	ReleaseNotePublishChangelogCreate,
	ReleaseNoteRead,
)
from app.services.auth_service import CurrentUserDependency, TokenDependency
from app.services.common_service import FEEDBACK_TIMEZONE, _require_admin_user
from app.services.inbox_service import _load_hidden_message_ids
from app.services.service_context import SessionDependency
from app.services.sql_expression import sql_expr

GITHUB_RELEASE_LABEL: Final[str] = "GitHub Release:"


def _encode_source_feedback_ids(source_feedback_ids: list[int]) -> str | None:
	if not source_feedback_ids:
		return None

	return json.dumps(sorted(set(source_feedback_ids)), ensure_ascii=False)

def _decode_source_feedback_ids(payload: str | None) -> list[int]:
	if not payload:
		return []

	try:
		raw_value = json.loads(payload)
	except json.JSONDecodeError:
		return []

	if not isinstance(raw_value, list):
		return []

	source_feedback_ids: list[int] = []
	for item in raw_value:
		if not isinstance(item, int) or item <= 0:
			continue
		source_feedback_ids.append(item)

	return sorted(set(source_feedback_ids))

def _count_release_note_deliveries(session: Session, release_note_id: int) -> int:
	return len(
		list(
			session.exec(
				select(ReleaseNoteDelivery.id).where(
					ReleaseNoteDelivery.release_note_id == release_note_id,
				),
			),
		),
	)

def _to_release_note_read(
	session: Session,
	release_note: ReleaseNote,
) -> ReleaseNoteRead:
	return ReleaseNoteRead(
		id=release_note.id or 0,
		version=release_note.version,
		title=release_note.title,
		content=release_note.content,
		source_feedback_ids=_decode_source_feedback_ids(release_note.source_feedback_ids_json),
		created_by=release_note.created_by,
		created_at=release_note.created_at,
		published_at=release_note.published_at,
		delivery_count=_count_release_note_deliveries(session, release_note.id or 0),
	)

def _to_release_note_delivery_read(
	delivery: ReleaseNoteDelivery,
	release_note: ReleaseNote,
	*,
	title_override: str | None = None,
	content_override: str | None = None,
) -> ReleaseNoteDeliveryRead:
	return ReleaseNoteDeliveryRead(
		delivery_id=delivery.id or 0,
		release_note_id=release_note.id or 0,
		version=release_note.version,
		title=title_override if title_override is not None else release_note.title,
		content=content_override if content_override is not None else release_note.content,
		source_feedback_ids=_decode_source_feedback_ids(release_note.source_feedback_ids_json),
		delivered_at=delivery.delivered_at,
		seen_at=delivery.seen_at,
		published_at=release_note.published_at or delivery.delivered_at,
	)

def _list_published_release_notes_desc(session: Session) -> list[ReleaseNote]:
	return list(
		session.exec(
			select(ReleaseNote)
			.where(sql_expr(ReleaseNote.published_at).is_not(None))
			.order_by(sql_expr(ReleaseNote.published_at).desc(), sql_expr(ReleaseNote.id).desc()),
		),
	)

def _get_latest_published_release_note(session: Session) -> ReleaseNote | None:
	return session.exec(
		select(ReleaseNote)
		.where(sql_expr(ReleaseNote.published_at).is_not(None))
		.order_by(sql_expr(ReleaseNote.published_at).desc(), sql_expr(ReleaseNote.id).desc()),
	).first()


def _parse_semver(version: str) -> tuple[int, int, int]:
	major, minor, patch = version.split(".")
	return int(major), int(minor), int(patch)


def _build_release_note_content(content: str, release_url: str | None) -> str:
	normalized_content = content.strip()
	if release_url is None or release_url in normalized_content:
		return normalized_content
	return f"{normalized_content}\n\n{GITHUB_RELEASE_LABEL} {release_url}"


def _assert_release_note_version_is_not_older_than_latest_published(
	session: Session,
	version: str,
) -> None:
	latest_release_note = _get_latest_published_release_note(session)
	if latest_release_note is None:
		return
	if _parse_semver(version) < _parse_semver(latest_release_note.version):
		raise HTTPException(
			status_code=409,
			detail="Release note version cannot be older than the latest published version.",
		)

def _format_release_note_stream_content(release_notes: list[ReleaseNote]) -> str:
	if not release_notes:
		return ""

	sections: list[str] = []
	for release_note in release_notes:
		published_at = (release_note.published_at or release_note.created_at).astimezone(
			FEEDBACK_TIMEZONE,
		)
		source_feedback_ids = _decode_source_feedback_ids(release_note.source_feedback_ids_json)
		source_feedback_line = ""
		if source_feedback_ids:
			source_feedback_line = (
				"\nLinked feedback: "
				+ ", ".join(f"#{feedback_id}" for feedback_id in source_feedback_ids)
			)

		sections.append(
			"\n".join(
				[
					f"## v{release_note.version} · {published_at:%Y-%m-%d %H:%M}",
					release_note.title,
					"",
					release_note.content,
					source_feedback_line,
				],
			).strip(),
		)

	return "# Product Updates\n\n" + "\n\n---\n\n".join(sections)

def _upsert_release_note_stream_delivery(
	session: Session,
	*,
	user_id: str,
	release_note: ReleaseNote,
	reset_seen: bool,
) -> bool:
	release_note_id = release_note.id
	if release_note_id is None:
		return False

	deliveries = list(
		session.exec(
			select(ReleaseNoteDelivery)
			.where(ReleaseNoteDelivery.user_id == user_id)
			.order_by(
				sql_expr(ReleaseNoteDelivery.delivered_at).desc(),
				sql_expr(ReleaseNoteDelivery.id).desc(),
			),
		),
	)

	target_delivered_at = release_note.published_at or utc_now()
	changed = False
	if not deliveries:
		session.add(
			ReleaseNoteDelivery(
				release_note_id=release_note_id,
				user_id=user_id,
				delivered_at=target_delivered_at,
				seen_at=None,
			),
		)
		return True

	primary_delivery = deliveries[0]
	is_new_release_for_user = primary_delivery.release_note_id != release_note_id
	if primary_delivery.release_note_id != release_note_id:
		primary_delivery.release_note_id = release_note_id
		changed = True
	if primary_delivery.delivered_at != target_delivered_at:
		primary_delivery.delivered_at = target_delivered_at
		changed = True
	if is_new_release_for_user or reset_seen:
		if primary_delivery.seen_at is not None:
			primary_delivery.seen_at = None
			changed = True
	session.add(primary_delivery)

	for stale_delivery in deliveries[1:]:
		session.delete(stale_delivery)
		changed = True

	return changed

def _ensure_release_note_deliveries_for_user(session: Session, user_id: str) -> None:
	latest_release_note = _get_latest_published_release_note(session)
	if latest_release_note is None:
		return

	if _upsert_release_note_stream_delivery(
		session,
		user_id=user_id,
		release_note=latest_release_note,
		reset_seen=False,
	):
		session.commit()


def _sync_release_note_deliveries_for_all_users(
	session: SessionDependency,
	*,
	release_note: ReleaseNote,
	reset_seen: bool,
) -> bool:
	recipient_ids = list(session.exec(select(UserAccount.username)))
	updated_delivery = False
	for recipient_id in recipient_ids:
		if _upsert_release_note_stream_delivery(
			session,
			user_id=recipient_id,
			release_note=release_note,
			reset_seen=reset_seen,
		):
			updated_delivery = True

	if updated_delivery:
		session.commit()

	return updated_delivery


def _publish_release_note(
	session: SessionDependency,
	*,
	release_note: ReleaseNote,
	current_user: UserAccount,
) -> ReleaseNote:
	if release_note.published_at is None:
		release_note.published_at = utc_now()
		session.add(release_note)
		session.commit()
		session.refresh(release_note)

	_sync_release_note_deliveries_for_all_users(
		session,
		release_note=release_note,
		reset_seen=True,
	)

	return release_note

def list_release_notes_for_admin(
	current_user: CurrentUserDependency,
	session: SessionDependency,
	_: TokenDependency,
) -> list[ReleaseNoteRead]:
	_require_admin_user(current_user)
	release_notes = list(
		session.exec(
			select(ReleaseNote).order_by(sql_expr(ReleaseNote.created_at).desc()),
		),
	)
	return [_to_release_note_read(session, release_note) for release_note in release_notes]

def create_release_note_for_admin(
	payload: ReleaseNoteCreate,
	current_user: CurrentUserDependency,
	session: SessionDependency,
	_: TokenDependency,
) -> ReleaseNoteRead:
	_require_admin_user(current_user)
	existing_release_note = session.exec(
		select(ReleaseNote).where(ReleaseNote.version == payload.version),
	).first()
	if existing_release_note is not None:
		raise HTTPException(status_code=409, detail="This release note version already exists.")

	release_note = ReleaseNote(
		version=payload.version,
		title=payload.title,
		content=payload.content,
		source_feedback_ids_json=_encode_source_feedback_ids(payload.source_feedback_ids),
		created_by=current_user.username,
	)
	session.add(release_note)
	session.commit()
	session.refresh(release_note)
	return _to_release_note_read(session, release_note)


def publish_changelog_release_note_for_admin(
	payload: ReleaseNotePublishChangelogCreate,
	current_user: CurrentUserDependency,
	session: SessionDependency,
	_: TokenDependency,
) -> ReleaseNoteRead:
	_require_admin_user(current_user)
	encoded_source_feedback_ids = _encode_source_feedback_ids(payload.source_feedback_ids)
	normalized_content = _build_release_note_content(payload.content, payload.release_url)

	release_note = session.exec(
		select(ReleaseNote).where(ReleaseNote.version == payload.version),
	).first()
	if release_note is None:
		_assert_release_note_version_is_not_older_than_latest_published(session, payload.version)
		release_note = ReleaseNote(
			version=payload.version,
			title=payload.title,
			content=normalized_content,
			source_feedback_ids_json=encoded_source_feedback_ids,
			created_by=current_user.username,
		)
		session.add(release_note)
		session.commit()
		session.refresh(release_note)
	else:
		if release_note.published_at is None:
			_assert_release_note_version_is_not_older_than_latest_published(
				session,
				payload.version,
			)

		existing_payload_matches = (
			release_note.title == payload.title
			and release_note.content == normalized_content
			and release_note.source_feedback_ids_json == encoded_source_feedback_ids
		)
		if release_note.published_at is not None:
			if not existing_payload_matches:
				raise HTTPException(
					status_code=409,
					detail=(
						"This release note version is already published and does not match "
						"the submitted changelog content."
					),
				)
			_sync_release_note_deliveries_for_all_users(
				session,
				release_note=release_note,
				reset_seen=False,
			)
			return _to_release_note_read(session, release_note)

		release_note.title = payload.title
		release_note.content = normalized_content
		release_note.source_feedback_ids_json = encoded_source_feedback_ids
		session.add(release_note)
		session.commit()
		session.refresh(release_note)

	_publish_release_note(
		session,
		release_note=release_note,
		current_user=current_user,
	)
	return _to_release_note_read(session, release_note)

def list_release_notes_for_current_user(
	current_user: CurrentUserDependency,
	session: SessionDependency,
	_: TokenDependency,
) -> list[ReleaseNoteDeliveryRead]:
	_ensure_release_note_deliveries_for_user(session, current_user.username)
	hidden_delivery_ids = _load_hidden_message_ids(
		session,
		user_id=current_user.username,
		message_kind="RELEASE_NOTE",
	)
	rows = list(
		session.exec(
		select(ReleaseNoteDelivery, ReleaseNote)
		.join(ReleaseNote, sql_expr(ReleaseNote.id) == ReleaseNoteDelivery.release_note_id)
		.where(
			ReleaseNoteDelivery.user_id == current_user.username,
			sql_expr(ReleaseNote.published_at).is_not(None),
		)
		.order_by(
			sql_expr(ReleaseNoteDelivery.delivered_at).desc(),
			sql_expr(ReleaseNoteDelivery.id).desc(),
		),
	),
	)
	if not rows:
		return []

	visible_row = next(
		(
			(delivery, release_note)
			for delivery, release_note in rows
			if (delivery.id or 0) not in hidden_delivery_ids
		),
		None,
	)
	if visible_row is None:
		return []
	delivery, latest_release_note = visible_row
	stream_content = _format_release_note_stream_content(_list_published_release_notes_desc(session))
	return [
		_to_release_note_delivery_read(
			delivery,
			latest_release_note,
			title_override="Product Updates",
			content_override=stream_content,
		),
	]

def mark_release_notes_seen_for_current_user(
	current_user: CurrentUserDependency,
	session: SessionDependency,
	_: TokenDependency,
) -> ActionMessageRead:
	_ensure_release_note_deliveries_for_user(session, current_user.username)
	hidden_delivery_ids = _load_hidden_message_ids(
		session,
		user_id=current_user.username,
		message_kind="RELEASE_NOTE",
	)
	pending_items = list(
		session.exec(
			select(ReleaseNoteDelivery).where(
				ReleaseNoteDelivery.user_id == current_user.username,
				sql_expr(ReleaseNoteDelivery.seen_at).is_(None),
			),
		),
	)
	pending_items = [
		item for item in pending_items if (item.id or 0) not in hidden_delivery_ids
	]
	if not pending_items:
		return ActionMessageRead(message="No new release notes.")

	now = utc_now()
	for delivery in pending_items:
		delivery.seen_at = now
		session.add(delivery)

	session.commit()
	return ActionMessageRead(message="Release notes marked as seen.")

def publish_release_note_for_admin(
	release_note_id: int,
	current_user: CurrentUserDependency,
	session: SessionDependency,
	_: TokenDependency,
) -> ReleaseNoteRead:
	_require_admin_user(current_user)
	release_note = session.get(ReleaseNote, release_note_id)
	if release_note is None:
		raise HTTPException(status_code=404, detail="Release note not found.")

	_publish_release_note(
		session,
		release_note=release_note,
		current_user=current_user,
	)
	return _to_release_note_read(session, release_note)

__all__ = ['_encode_source_feedback_ids', '_decode_source_feedback_ids', '_count_release_note_deliveries', '_to_release_note_read', '_to_release_note_delivery_read', '_list_published_release_notes_desc', '_get_latest_published_release_note', '_parse_semver', '_build_release_note_content', '_assert_release_note_version_is_not_older_than_latest_published', '_format_release_note_stream_content', '_upsert_release_note_stream_delivery', '_ensure_release_note_deliveries_for_user', '_publish_release_note', 'list_release_notes_for_admin', 'create_release_note_for_admin', 'publish_changelog_release_note_for_admin', 'list_release_notes_for_current_user', 'mark_release_notes_seen_for_current_user', 'publish_release_note_for_admin']
