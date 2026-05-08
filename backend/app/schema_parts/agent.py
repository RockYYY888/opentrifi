from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.models import AGENT_TASK_STATUSES, AGENT_TASK_TYPES
from app.schema_parts.common import UtcTimestampResponseModel, _normalize_choice

class AgentTaskCreate(BaseModel):
	task_type: str = Field(min_length=1, max_length=40)
	payload: dict[str, Any] = Field(default_factory=dict)

	@field_validator("task_type", mode="before")
	@classmethod
	def validate_task_type(cls, value: str | None) -> str | None:
		return _normalize_choice(value, AGENT_TASK_TYPES, "task_type")

class AgentTaskRead(UtcTimestampResponseModel):
	id: int
	request_source: str
	api_key_name: str | None = None
	agent_name: str | None = None
	task_type: str
	status: str
	payload: dict[str, Any]
	result: dict[str, Any] | None = None
	error_message: str | None = None
	created_at: datetime
	updated_at: datetime
	completed_at: datetime | None = None

	@field_validator("task_type", mode="before")
	@classmethod
	def validate_task_type(cls, value: str | None) -> str | None:
		return _normalize_choice(value, AGENT_TASK_TYPES, "task_type")

	@field_validator("status", mode="before")
	@classmethod
	def validate_status(cls, value: str | None) -> str | None:
		return _normalize_choice(value, AGENT_TASK_STATUSES, "status")

class AgentRegistrationRead(UtcTimestampResponseModel):
	id: int
	user_id: str
	name: str
	status: str
	request_count: int
	latest_api_key_name: str | None = None
	last_used_at: datetime | None = None
	last_seen_at: datetime | None = None
	created_at: datetime
	updated_at: datetime
