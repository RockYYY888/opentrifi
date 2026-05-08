from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.schema_parts.common import (
	AGENT_TOKEN_NAME_PATTERN,
	UtcTimestampResponseModel,
	_normalize_required_text,
)
from app.security import normalize_email, normalize_user_id, validate_password_strength

class AuthRegisterCredentials(BaseModel):
	user_id: str = Field(min_length=3, max_length=32)
	email: str = Field(min_length=3, max_length=320)
	password: str = Field(min_length=8, max_length=128)

	@field_validator("user_id", mode="before")
	@classmethod
	def validate_user_id(cls, value: str) -> str:
		return normalize_user_id(value)

	@field_validator("password", mode="before")
	@classmethod
	def validate_password(cls, value: str) -> str:
		return validate_password_strength(value)

	@field_validator("email", mode="before")
	@classmethod
	def validate_email(cls, value: str) -> str:
		return normalize_email(value)

class AuthLoginCredentials(BaseModel):
	user_id: str = Field(min_length=3, max_length=32)
	password: str = Field(min_length=1, max_length=128)

	@field_validator("user_id", mode="before")
	@classmethod
	def validate_user_id(cls, value: str) -> str:
		return normalize_user_id(value)

class AuthSessionRead(BaseModel):
	user_id: str
	email: str | None = None

class AgentTokenCreate(BaseModel):
	name: str = Field(min_length=3, max_length=80)
	expires_in_days: int | None = Field(default=None, ge=1, le=3650)

	@field_validator("name", mode="before")
	@classmethod
	def normalize_name(cls, value: str) -> str:
		name = _normalize_required_text(value, "name")
		if any(ord(character) < 32 for character in name):
			raise ValueError("API Key 名称不能包含换行或控制字符。")
		if not AGENT_TOKEN_NAME_PATTERN.fullmatch(name):
			raise ValueError(
				"API Key 名称仅支持小写字母和连字符（-），例如 daily-sync。",
			)
		return name

class AgentTokenIssueCreate(AgentTokenCreate):
	user_id: str = Field(min_length=3, max_length=32)
	password: str = Field(min_length=1, max_length=128)

	@field_validator("user_id", mode="before")
	@classmethod
	def validate_user_id(cls, value: str) -> str:
		return normalize_user_id(value)

class AgentTokenRead(UtcTimestampResponseModel):
	id: int
	name: str
	token_hint: str
	created_at: datetime
	updated_at: datetime
	last_used_at: datetime | None = None
	expires_at: datetime | None = None
	revoked_at: datetime | None = None

class AgentTokenIssueRead(AgentTokenRead):
	access_token: str

class PasswordResetRequest(BaseModel):
	user_id: str = Field(min_length=3, max_length=32)
	email: str = Field(min_length=3, max_length=320)
	new_password: str = Field(min_length=8, max_length=128)

	@field_validator("user_id", mode="before")
	@classmethod
	def validate_user_id(cls, value: str) -> str:
		return normalize_user_id(value)

	@field_validator("email", mode="before")
	@classmethod
	def validate_email(cls, value: str) -> str:
		return normalize_email(value)

	@field_validator("new_password", mode="before")
	@classmethod
	def validate_password(cls, value: str) -> str:
		return validate_password_strength(value)
