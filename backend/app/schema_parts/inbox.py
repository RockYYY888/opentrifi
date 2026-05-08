from __future__ import annotations

from datetime import datetime
import re
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

from app.models import (
	FEEDBACK_CATEGORIES,
	FEEDBACK_PRIORITIES,
	FEEDBACK_SOURCES,
	FEEDBACK_STATUSES,
	INBOX_MESSAGE_KINDS,
)
from app.schema_parts.common import (
	UtcTimestampResponseModel,
	_normalize_choice,
	_normalize_optional_text,
	_normalize_required_text,
)
from app.security import normalize_email, normalize_user_id

SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")

class ActionMessageRead(BaseModel):
	message: str

class UserEmailUpdate(BaseModel):
	email: str = Field(min_length=3, max_length=320)

	@field_validator("email", mode="before")
	@classmethod
	def validate_email(cls, value: str) -> str:
		return normalize_email(value)

class UserFeedbackCreate(BaseModel):
	message: str = Field(min_length=5, max_length=1000)
	category: str | None = Field(default=None, max_length=32)
	priority: str | None = Field(default=None, max_length=16)
	source: str | None = Field(default=None, max_length=32)
	fingerprint: str | None = Field(default=None, max_length=96)
	dedupe_window_minutes: int | None = Field(default=None, ge=1, le=10_080)

	@field_validator("message", mode="before")
	@classmethod
	def normalize_message(cls, value: str) -> str:
		return _normalize_required_text(value, "message")

	@field_validator("category", mode="before")
	@classmethod
	def normalize_category(cls, value: str | None) -> str | None:
		return _normalize_choice(value, FEEDBACK_CATEGORIES, "category")

	@field_validator("priority", mode="before")
	@classmethod
	def normalize_priority(cls, value: str | None) -> str | None:
		return _normalize_choice(value, FEEDBACK_PRIORITIES, "priority")

	@field_validator("source", mode="before")
	@classmethod
	def normalize_source(cls, value: str | None) -> str | None:
		return _normalize_choice(value, FEEDBACK_SOURCES, "source")

	@field_validator("fingerprint", mode="before")
	@classmethod
	def normalize_fingerprint(cls, value: str | None) -> str | None:
		return _normalize_optional_text(value)

class UserFeedbackRead(UtcTimestampResponseModel):
	id: int
	user_id: str
	message: str
	category: str
	priority: str
	source: str
	status: str
	is_system: bool
	reply_message: str | None = None
	replied_at: datetime | None = None
	replied_by: str | None = None
	reply_seen_at: datetime | None = None
	resolved_at: datetime | None = None
	closed_by: str | None = None
	created_at: datetime

class AdminFeedbackRead(UserFeedbackRead):
	assignee: str | None = None
	acknowledged_at: datetime | None = None
	acknowledged_by: str | None = None
	ack_deadline: datetime | None = None
	internal_note: str | None = None
	internal_note_updated_at: datetime | None = None
	internal_note_updated_by: str | None = None
	fingerprint: str | None = None
	dedupe_window_minutes: int | None = None
	occurrence_count: int = 1
	last_seen_at: datetime | None = None

class AdminFeedbackListRead(BaseModel):
	items: list[AdminFeedbackRead]
	total: int
	page: int
	page_size: int
	has_more: bool

class FeedbackSummaryRead(BaseModel):
	inbox_count: int
	mode: str

class AdminFeedbackReplyUpdate(BaseModel):
	reply_message: str = Field(min_length=1, max_length=2000)
	close: bool = False

	@field_validator("reply_message", mode="before")
	@classmethod
	def normalize_reply_message(cls, value: str) -> str:
		return _normalize_required_text(value, "reply_message")

class AdminFeedbackClassifyUpdate(BaseModel):
	category: str | None = Field(default=None, max_length=32)
	priority: str | None = Field(default=None, max_length=16)
	source: str | None = Field(default=None, max_length=32)
	status: str | None = Field(default=None, max_length=16)
	assignee: str | None = Field(default=None, max_length=32)
	ack_deadline: datetime | None = Field(default=None)
	internal_note: str | None = Field(default=None, max_length=3000)

	@field_validator("category", mode="before")
	@classmethod
	def normalize_category(cls, value: str | None) -> str | None:
		return _normalize_choice(value, FEEDBACK_CATEGORIES, "category")

	@field_validator("priority", mode="before")
	@classmethod
	def normalize_priority(cls, value: str | None) -> str | None:
		return _normalize_choice(value, FEEDBACK_PRIORITIES, "priority")

	@field_validator("source", mode="before")
	@classmethod
	def normalize_source(cls, value: str | None) -> str | None:
		return _normalize_choice(value, FEEDBACK_SOURCES, "source")

	@field_validator("status", mode="before")
	@classmethod
	def normalize_status(cls, value: str | None) -> str | None:
		return _normalize_choice(value, FEEDBACK_STATUSES, "status")

	@field_validator("assignee", mode="before")
	@classmethod
	def normalize_assignee(cls, value: str | None) -> str | None:
		if value is None:
			return None
		return normalize_user_id(value)

	@field_validator("internal_note", mode="before")
	@classmethod
	def normalize_internal_note(cls, value: str | None) -> str | None:
		return _normalize_optional_text(value)

class AdminFeedbackAcknowledgeUpdate(BaseModel):
	assignee: str | None = Field(default=None, max_length=32)
	ack_deadline: datetime | None = Field(default=None)
	internal_note: str | None = Field(default=None, max_length=3000)

	@field_validator("assignee", mode="before")
	@classmethod
	def normalize_assignee(cls, value: str | None) -> str | None:
		if value is None:
			return None
		return normalize_user_id(value)

	@field_validator("internal_note", mode="before")
	@classmethod
	def normalize_internal_note(cls, value: str | None) -> str | None:
		return _normalize_optional_text(value)

class InboxMessageHideCreate(BaseModel):
	message_kind: str = Field(max_length=24)
	message_id: int = Field(gt=0)

	@field_validator("message_kind", mode="before")
	@classmethod
	def normalize_message_kind(cls, value: str) -> str:
		normalized = _normalize_required_text(value, "message_kind").upper()
		if normalized not in INBOX_MESSAGE_KINDS:
			raise ValueError(f"message_kind must be one of: {', '.join(INBOX_MESSAGE_KINDS)}")
		return normalized


SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")

class ReleaseNoteCreate(BaseModel):
	version: str = Field(min_length=1, max_length=32)
	title: str = Field(min_length=1, max_length=120)
	content: str = Field(min_length=1, max_length=6000)
	source_feedback_ids: list[int] = Field(default_factory=list)

	@field_validator("version", mode="before")
	@classmethod
	def validate_version(cls, value: str) -> str:
		normalized = _normalize_required_text(value, "version")
		if SEMVER_PATTERN.match(normalized) is None:
			raise ValueError("version must match semantic version format: x.y.z")
		return normalized

	@field_validator("title", "content", mode="before")
	@classmethod
	def normalize_required_fields(cls, value: str, info: Any) -> str:
		return _normalize_required_text(value, info.field_name)

	@field_validator("source_feedback_ids")
	@classmethod
	def validate_source_feedback_ids(cls, value: list[int]) -> list[int]:
		normalized_ids = sorted(set(value))
		if any(item <= 0 for item in normalized_ids):
			raise ValueError("source_feedback_ids must contain positive integers only.")
		return normalized_ids

class ReleaseNotePublishChangelogCreate(ReleaseNoteCreate):
	release_url: str | None = Field(default=None, max_length=500)

	@field_validator("release_url", mode="before")
	@classmethod
	def normalize_release_url(cls, value: str | None) -> str | None:
		normalized = _normalize_optional_text(value)
		if normalized is None:
			return None

		parsed = urlparse(normalized)
		if parsed.scheme not in {"http", "https"} or not parsed.netloc:
			raise ValueError("release_url must be a valid http or https URL.")

		return normalized

class ReleaseNoteRead(UtcTimestampResponseModel):
	id: int
	version: str
	title: str
	content: str
	source_feedback_ids: list[int]
	created_by: str
	created_at: datetime
	published_at: datetime | None = None
	delivery_count: int = 0

class ReleaseNoteDeliveryRead(UtcTimestampResponseModel):
	delivery_id: int
	release_note_id: int
	version: str
	title: str
	content: str
	source_feedback_ids: list[int]
	delivered_at: datetime
	seen_at: datetime | None = None
	published_at: datetime
