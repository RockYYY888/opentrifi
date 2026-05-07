from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import re
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import Depends, HTTPException, Request
from fastapi.responses import Response
from sqlmodel import select

from app import runtime_state
from app.models import (
	AGENT_REGISTRATION_STATUSES,
	AgentAccessToken,
	AgentRegistration,
	UserAccount,
	utc_now,
)
from app.schemas import (
	ActionMessageRead,
	AgentTokenCreate,
	AgentTokenIssueCreate,
	AgentTokenIssueRead,
	AgentTokenRead,
	AuthLoginCredentials,
	AuthRegisterCredentials,
	AuthSessionRead,
	PasswordResetRequest,
	UserEmailUpdate,
)
from app.security import (
	extract_bearer_token,
	generate_agent_token,
	hash_agent_token,
	hash_email,
	hash_password,
	normalize_user_id,
	require_session_user_id,
	verify_api_token,
	verify_email,
	verify_password,
)
from app.services.service_context import SessionDependency

TokenDependency = Annotated[None, Depends(verify_api_token)]
LOGIN_ATTEMPT_WINDOW = timedelta(minutes=1)
MAX_LOGIN_ATTEMPTS_PER_WINDOW = 8
FAILED_LOGIN_FORGOT_PASSWORD_THRESHOLD = 5
MAX_LOGIN_DEVICE_ID_LENGTH = 120
LOGIN_ATTEMPT_STATE_TTL = timedelta(hours=24)
AGENT_TOKEN_LAST_USED_UPDATE_INTERVAL = timedelta(minutes=1)
MAX_ACTIVE_AGENT_TOKENS_PER_USER = 5
MAX_DAILY_AGENT_TOKEN_CREATIONS = 10
AUTHENTICATED_REQUESTS_PER_SECOND = 10
AGENT_REGISTRATION_ACTIVE_WINDOW = timedelta(hours=24)
SERVER_DAY_TIMEZONE = ZoneInfo("Asia/Shanghai")
AGENT_RUNTIME_NAME_PATTERN = re.compile(r"^[\w .:/-]{1,80}$", re.UNICODE)
AGENT_TOKEN_HINT_VISIBLE_LENGTH = 2
AGENT_TOKEN_HINT_MASK_LENGTH = 11
GENERIC_AGENT_TOKEN_HINT_PLACEHOLDER = "sk-xx***********"


def _coerce_utc_datetime(value: datetime) -> datetime:
	if value.tzinfo is None:
		return value.replace(tzinfo=timezone.utc)
	return value.astimezone(timezone.utc)


def _touch_model(model: AgentAccessToken | AgentRegistration | UserAccount) -> None:
	if hasattr(model, "updated_at"):
		model.updated_at = utc_now()


def _get_user(session: SessionDependency, user_id: str) -> UserAccount | None:
	return session.get(UserAccount, normalize_user_id(user_id))


def _get_agent_access_token_by_digest(
	session: SessionDependency,
	token_digest: str,
) -> AgentAccessToken | None:
	return session.exec(
		select(AgentAccessToken).where(AgentAccessToken.token_digest == token_digest),
	).first()


def _get_agent_registration_by_name(
	session: SessionDependency,
	*,
	user_id: str,
	name: str,
) -> AgentRegistration | None:
	return session.exec(
		select(AgentRegistration)
		.where(AgentRegistration.user_id == user_id)
		.where(AgentRegistration.name == name),
	).first()


def _normalize_agent_registration_name(name: str) -> str:
	normalized_name = name.strip()
	if not normalized_name:
		raise HTTPException(status_code=422, detail="Agent 名称不能为空。")
	if any(ord(character) < 32 for character in normalized_name):
		raise HTTPException(status_code=422, detail="Agent 名称不能包含换行或控制字符。")
	if not AGENT_RUNTIME_NAME_PATTERN.fullmatch(normalized_name):
		raise HTTPException(
			status_code=422,
			detail="Agent 名称仅支持字母、数字、空格，以及 . _ / : - 。",
		)
	return normalized_name


def _list_agent_access_tokens_for_user(
	session: SessionDependency,
	*,
	user_id: str,
) -> list[AgentAccessToken]:
	return list(
		session.exec(
			select(AgentAccessToken)
			.where(AgentAccessToken.user_id == user_id)
			.order_by(AgentAccessToken.created_at.desc(), AgentAccessToken.id.desc()),
		),
	)


def _list_active_agent_access_tokens_for_user(
	session: SessionDependency,
	*,
	user_id: str,
	now: datetime | None = None,
) -> list[AgentAccessToken]:
	now_value = now or utc_now()
	return [
		token
		for token in _list_agent_access_tokens_for_user(session, user_id=user_id)
		if _is_agent_token_active(token, now_value)
	]


def _is_agent_token_active(token: AgentAccessToken, now: datetime) -> bool:
	if token.revoked_at is not None:
		return False
	if token.expires_at is not None and _coerce_utc_datetime(token.expires_at) <= now:
		return False
	return True


def _revoke_agent_token_row(
	session: SessionDependency,
	token: AgentAccessToken,
	*,
	revoked_at: datetime | None = None,
) -> bool:
	if token.revoked_at is not None:
		return False

	token.revoked_at = revoked_at or utc_now()
	_touch_model(token)
	session.add(token)
	registrations = list(
		session.exec(
			select(AgentRegistration)
			.where(AgentRegistration.user_id == token.user_id)
			.where(AgentRegistration.latest_api_key_name == token.name),
		),
	)
	for registration in registrations:
		registration.status = AGENT_REGISTRATION_STATUSES[1]
		registration.latest_api_key_name = None
		_touch_model(registration)
		session.add(registration)
	return True


def _sync_expired_agent_tokens_for_user(
	session: SessionDependency,
	*,
	user_id: str,
	now: datetime | None = None,
) -> None:
	now_value = now or utc_now()
	updated = False
	for token in _list_agent_access_tokens_for_user(session, user_id=user_id):
		if token.revoked_at is not None or token.expires_at is None:
			continue
		if _coerce_utc_datetime(token.expires_at) > now_value:
			continue
		updated = _revoke_agent_token_row(
			session,
			token,
			revoked_at=now_value,
		) or updated

	if updated:
		session.commit()


def _current_server_day_window(now: datetime) -> tuple[datetime, datetime]:
	local_now = _coerce_utc_datetime(now).astimezone(SERVER_DAY_TIMEZONE)
	local_day_start = datetime.combine(local_now.date(), datetime.min.time(), tzinfo=SERVER_DAY_TIMEZONE)
	local_day_end = local_day_start + timedelta(days=1)
	return (
		local_day_start.astimezone(timezone.utc),
		local_day_end.astimezone(timezone.utc),
	)


def _count_today_agent_token_creations_for_user(
	session: SessionDependency,
	*,
	user_id: str,
	now: datetime,
) -> int:
	day_start, day_end = _current_server_day_window(now)
	return len(
		list(
			session.exec(
				select(AgentAccessToken.id)
				.where(AgentAccessToken.user_id == user_id)
				.where(AgentAccessToken.created_at >= day_start)
				.where(AgentAccessToken.created_at < day_end),
			),
		),
	)


def _ensure_agent_registration(
	session: SessionDependency,
	*,
	current_user: UserAccount,
	name: str,
	api_key_name: str | None,
	seen_at: datetime,
) -> AgentRegistration:
	registration_name = _normalize_agent_registration_name(name)
	registration = _get_agent_registration_by_name(
		session,
		user_id=current_user.username,
		name=registration_name,
	)
	if registration is None:
		registration = AgentRegistration(
			user_id=current_user.username,
			name=registration_name,
			status=AGENT_REGISTRATION_STATUSES[0],
			request_count=1,
			latest_api_key_name=api_key_name,
			last_seen_at=seen_at,
		)
	else:
		registration.status = AGENT_REGISTRATION_STATUSES[0]
		registration.request_count += 1
		registration.latest_api_key_name = api_key_name
		registration.last_seen_at = seen_at
		_touch_model(registration)

	session.add(registration)
	session.flush()
	return registration


def _normalize_agent_name_header(raw_value: str | None) -> str | None:
	if raw_value is None:
		return None
	normalized = raw_value.strip()
	if not normalized:
		return None
	if normalized.casefold() == "false":
		return None
	return _normalize_agent_registration_name(normalized)


def _enforce_authenticated_request_rate_limit(
	user_id: str,
	*,
	now: datetime,
) -> None:
	utc_now_value = _coerce_utc_datetime(now)
	second_bucket = int(utc_now_value.timestamp())
	key = f"asset-tracker:rate-limit:{normalize_user_id(user_id)}:{second_bucket}"
	current_count = runtime_state.redis_client.incr(key)
	if current_count == 1:
		runtime_state.redis_client.expire(key, 2)
	if current_count > AUTHENTICATED_REQUESTS_PER_SECOND:
		raise HTTPException(
			status_code=429,
			detail=f"同一账号每秒最多请求 {AUTHENTICATED_REQUESTS_PER_SECOND} 次，请稍后重试。",
		)


def _resolve_agent_token_expiry(expires_in_days: int | None) -> datetime | None:
	if expires_in_days is None:
		return None
	return utc_now() + timedelta(days=expires_in_days)


def _build_agent_token_hint(visible_fragment: str) -> str:
	visible_value = (visible_fragment or "xx")[:AGENT_TOKEN_HINT_VISIBLE_LENGTH].ljust(
		AGENT_TOKEN_HINT_VISIBLE_LENGTH,
		"x",
	)
	return f"sk-{visible_value}{'*' * AGENT_TOKEN_HINT_MASK_LENGTH}"


def _format_agent_token_hint(raw_token: str) -> str:
	return _build_agent_token_hint(raw_token[3:3 + AGENT_TOKEN_HINT_VISIBLE_LENGTH])


def _normalize_agent_token_hint_for_output(token_hint: str) -> str:
	normalized = token_hint.strip()
	if normalized.startswith("sk-"):
		visible_fragment = normalized[3:].replace("*", "")[:AGENT_TOKEN_HINT_VISIBLE_LENGTH]
		return _build_agent_token_hint(visible_fragment)
	return GENERIC_AGENT_TOKEN_HINT_PLACEHOLDER


def _to_agent_token_read(token: AgentAccessToken) -> AgentTokenRead:
	return AgentTokenRead(
		id=token.id or 0,
		name=token.name,
		token_hint=_normalize_agent_token_hint_for_output(token.token_hint),
		created_at=token.created_at,
		updated_at=token.updated_at,
		last_used_at=token.last_used_at,
		expires_at=token.expires_at,
		revoked_at=token.revoked_at,
	)


def _create_agent_access_token(
	session: SessionDependency,
	*,
	current_user: UserAccount,
	name: str,
	expires_in_days: int | None,
) -> tuple[AgentAccessToken, str]:
	now = utc_now()
	lock_name = f"agent-token-create:{current_user.username}"
	with runtime_state.redis_lock(lock_name, timeout=10, blocking_timeout=10):
		_sync_expired_agent_tokens_for_user(
			session,
			user_id=current_user.username,
			now=now,
		)
		existing_tokens = _list_agent_access_tokens_for_user(
			session,
			user_id=current_user.username,
		)
		active_tokens = [
			token for token in existing_tokens if _is_agent_token_active(token, now)
		]
		if len(active_tokens) >= MAX_ACTIVE_AGENT_TOKENS_PER_USER:
			raise HTTPException(
				status_code=409,
				detail="每个账号最多保留 5 个有效 API Key，请先删除旧 Key。",
			)
		if _count_today_agent_token_creations_for_user(
			session,
			user_id=current_user.username,
			now=now,
		) >= MAX_DAILY_AGENT_TOKEN_CREATIONS:
			raise HTTPException(
				status_code=429,
				detail="同一账号每天最多生成 10 次 API Key，请明天再试。",
			)

		normalized_name = name.strip()
		if any(token.name.casefold() == normalized_name.casefold() for token in active_tokens):
			raise HTTPException(
				status_code=409,
				detail="当前账号已经存在同名的有效 API Key，请使用新的名称。",
			)

		for _ in range(8):
			raw_token = generate_agent_token()
			token_digest = hash_agent_token(raw_token)
			if _get_agent_access_token_by_digest(session, token_digest) is not None:
				continue
			token = AgentAccessToken(
				user_id=current_user.username,
				agent_registration_id=None,
				name=normalized_name,
				token_digest=token_digest,
				token_hint=_format_agent_token_hint(raw_token),
				expires_at=_resolve_agent_token_expiry(expires_in_days),
			)
			session.add(token)
			session.commit()
			session.refresh(token)
			return token, raw_token

	raise RuntimeError("Unable to generate a unique API key after multiple attempts.")


def _normalize_client_device_id(raw_device_id: str | None) -> str | None:
	if raw_device_id is None:
		return None
	normalized = raw_device_id.strip()
	if not normalized:
		return None
	return normalized[:MAX_LOGIN_DEVICE_ID_LENGTH]


def _build_login_attempt_key(request: Request, user_id: str) -> tuple[str, str]:
	explicit_device_id = _normalize_client_device_id(
		request.headers.get("X-Client-Device-Id"),
	)
	if explicit_device_id is not None:
		return normalize_user_id(user_id), f"device:{explicit_device_id}"

	client_host = request.client.host if request.client is not None else "unknown"
	user_agent = (request.headers.get("user-agent") or "").strip().lower()
	fallback_seed = f"{client_host}|{user_agent}"
	fallback_hash = hashlib.sha256(fallback_seed.encode("utf-8")).hexdigest()[:24]
	return normalize_user_id(user_id), f"fallback:{fallback_hash}"


def _prune_login_attempt_timestamps(
	attempt_timestamps: list[datetime],
	now: datetime,
) -> list[datetime]:
	window_start = now - LOGIN_ATTEMPT_WINDOW
	return [timestamp for timestamp in attempt_timestamps if timestamp >= window_start]


def _login_attempt_lock_name(attempt_key: tuple[str, str]) -> str:
	user_id, device_key = attempt_key
	digest = hashlib.sha256(f"{user_id}|{device_key}".encode("utf-8")).hexdigest()
	return f"login-attempt:{digest}"


def _cleanup_expired_login_attempt_states(now: datetime) -> None:
	expired_before = now - LOGIN_ATTEMPT_STATE_TTL
	expired_keys = [
		key
		for key, state in runtime_state.login_attempt_states.items()
		if state.last_attempt_at < expired_before
	]
	for key in expired_keys:
		runtime_state.login_attempt_states.pop(key, None)


def _reserve_login_attempt(
	attempt_key: tuple[str, str],
	now: datetime,
) -> None:
	with runtime_state.redis_lock(_login_attempt_lock_name(attempt_key), timeout=5, blocking_timeout=5):
		state = runtime_state.login_attempt_states.get(attempt_key)
		if state is None:
			state = runtime_state.LoginAttemptState(
				attempt_timestamps=[],
				consecutive_failed_attempts=0,
				last_attempt_at=now,
			)
			runtime_state.login_attempt_states[attempt_key] = state

		state.attempt_timestamps = _prune_login_attempt_timestamps(
			state.attempt_timestamps,
			now,
		)
		if len(state.attempt_timestamps) >= MAX_LOGIN_ATTEMPTS_PER_WINDOW:
			raise HTTPException(
				status_code=429,
				detail="同一设备同一账号 1 分钟内最多尝试 8 次，请稍后再试。",
			)

		state.attempt_timestamps.append(now)
		state.last_attempt_at = now
		runtime_state.login_attempt_states[attempt_key] = state

		if len(runtime_state.login_attempt_states) > 2048:
			_cleanup_expired_login_attempt_states(now)


def _record_failed_login_attempt(
	attempt_key: tuple[str, str],
	now: datetime,
) -> int:
	with runtime_state.redis_lock(_login_attempt_lock_name(attempt_key), timeout=5, blocking_timeout=5):
		state = runtime_state.login_attempt_states.get(attempt_key)
		if state is None:
			state = runtime_state.LoginAttemptState(
				attempt_timestamps=[now],
				consecutive_failed_attempts=0,
				last_attempt_at=now,
			)
			runtime_state.login_attempt_states[attempt_key] = state

		state.consecutive_failed_attempts += 1
		state.last_attempt_at = now
		runtime_state.login_attempt_states[attempt_key] = state
		return state.consecutive_failed_attempts


def _record_successful_login(attempt_key: tuple[str, str], now: datetime) -> None:
	with runtime_state.redis_lock(_login_attempt_lock_name(attempt_key), timeout=5, blocking_timeout=5):
		state = runtime_state.login_attempt_states.get(attempt_key)
		if state is None:
			return
		state.consecutive_failed_attempts = 0
		state.last_attempt_at = now
		runtime_state.login_attempt_states[attempt_key] = state


def _authenticate_user_account(
	session: SessionDependency,
	credentials: AuthLoginCredentials,
	*,
	attempt_key: tuple[str, str] | None = None,
) -> UserAccount:
	now = utc_now()
	login_attempt_key = attempt_key or (normalize_user_id(credentials.user_id), "device:unknown")
	_reserve_login_attempt(login_attempt_key, now)
	user = _get_user(session, credentials.user_id)
	if user is None or not verify_password(credentials.password, user.password_digest):
		failed_attempts = _record_failed_login_attempt(login_attempt_key, now)
		if failed_attempts >= FAILED_LOGIN_FORGOT_PASSWORD_THRESHOLD:
			raise HTTPException(
				status_code=401,
				detail=(
					"账号或密码错误。已连续输错 5 次，是否忘记密码？"
					"可点击“忘记密码”重设。"
				),
			)
		raise HTTPException(status_code=401, detail="账号或密码错误。")

	_record_successful_login(login_attempt_key, now)
	return user


def _authenticate_agent_access_token(
	session: SessionDependency,
	raw_token: str,
	*,
	agent_name: str | None,
) -> UserAccount:
	try:
		token_digest = hash_agent_token(raw_token)
	except ValueError as exc:
		raise HTTPException(status_code=401, detail="API Key 无效。") from exc

	token = _get_agent_access_token_by_digest(session, token_digest)
	if token is None or token.revoked_at is not None:
		raise HTTPException(status_code=401, detail="API Key 无效。")

	now = utc_now()
	if token.expires_at is not None and _coerce_utc_datetime(token.expires_at) <= now:
		_revoke_agent_token_row(session, token, revoked_at=now)
		session.commit()
		raise HTTPException(status_code=401, detail="API Key 已过期。")

	user = _get_user(session, token.user_id)
	if user is None:
		raise HTTPException(status_code=401, detail="API Key 对应账号不存在。")
	request_source = "AGENT" if agent_name else "API"
	runtime_state.current_actor_source_context.set(request_source)
	runtime_state.current_api_key_name_context.set(token.name)
	runtime_state.current_agent_name_context.set(agent_name)

	if token.last_used_at is None or (
		now - _coerce_utc_datetime(token.last_used_at)
	) >= AGENT_TOKEN_LAST_USED_UPDATE_INTERVAL:
		token.last_used_at = now
		_touch_model(token)
		session.add(token)
		session.commit()
	if agent_name is not None:
		lock_name = f"agent-registration:{user.username}:{agent_name.casefold()}"
		with runtime_state.redis_lock(lock_name, timeout=10, blocking_timeout=10):
			_ensure_agent_registration(
				session,
				current_user=user,
				name=agent_name,
				api_key_name=token.name,
				seen_at=now,
			)
			session.commit()

	return user


def get_current_user(
	request: Request,
	session: SessionDependency,
	_: TokenDependency,
) -> UserAccount:
	authorization = request.headers.get("authorization")
	bearer_token = extract_bearer_token(authorization)
	if authorization and bearer_token is None:
		raise HTTPException(status_code=401, detail="Authorization 头必须使用 Bearer API Key。")

	if bearer_token is not None:
		current_user = _authenticate_agent_access_token(
			session,
			bearer_token,
			agent_name=_normalize_agent_name_header(request.headers.get("Agent-Name")),
		)
		_enforce_authenticated_request_rate_limit(current_user.username, now=utc_now())
		return current_user

	current_user = get_session_current_user(request, session, None)
	_enforce_authenticated_request_rate_limit(current_user.username, now=utc_now())
	return current_user


def get_session_current_user(
	request: Request,
	session: SessionDependency,
	_: TokenDependency,
) -> UserAccount:
	user_id = require_session_user_id(request)
	user = _get_user(session, user_id)
	if user is None:
		request.session.clear()
		raise HTTPException(status_code=401, detail="请重新登录。")
	runtime_state.current_api_key_name_context.set(None)
	runtime_state.current_agent_name_context.set(None)
	runtime_state.current_actor_source_context.set(
		"SYSTEM" if user.username == "admin" else "USER",
	)
	return user


SessionCurrentUserDependency = Annotated[UserAccount, Depends(get_session_current_user)]
CurrentUserDependency = Annotated[UserAccount, Depends(get_current_user)]


def _create_user_account(
	session: SessionDependency,
	credentials: AuthRegisterCredentials,
) -> UserAccount:
	if _get_user(session, credentials.user_id) is not None:
		raise HTTPException(status_code=409, detail="用户名已存在。")

	email_digest = hash_email(credentials.email)
	if session.exec(select(UserAccount).where(UserAccount.email_digest == email_digest)).first():
		raise HTTPException(status_code=409, detail="该邮箱已被其他账号使用。")

	user = UserAccount(
		username=credentials.user_id,
		email=credentials.email,
		password_digest=hash_password(credentials.password),
		email_digest=email_digest,
	)
	session.add(user)
	session.commit()
	session.refresh(user)
	return user


def _update_user_email(
	session: SessionDependency,
	current_user: UserAccount,
	payload: UserEmailUpdate,
) -> UserAccount:
	email_digest = hash_email(payload.email)
	existing_user = session.exec(
		select(UserAccount).where(UserAccount.email_digest == email_digest),
	).first()
	if existing_user is not None and existing_user.username != current_user.username:
		raise HTTPException(status_code=409, detail="该邮箱已被其他账号使用。")

	current_user.email = payload.email
	current_user.email_digest = email_digest
	current_user.updated_at = utc_now()
	session.add(current_user)
	session.commit()
	session.refresh(current_user)
	return current_user


def _reset_user_password_with_email(
	session: SessionDependency,
	payload: PasswordResetRequest,
) -> UserAccount:
	user = _get_user(session, payload.user_id)
	if user is None or not verify_email(payload.email, user.email_digest):
		raise HTTPException(status_code=401, detail="用户名或邮箱不匹配。")

	user.password_digest = hash_password(payload.new_password)
	user.updated_at = utc_now()
	session.add(user)
	session.commit()
	session.refresh(user)
	return user


def get_auth_session(
	current_user: CurrentUserDependency,
) -> AuthSessionRead:
	return AuthSessionRead(user_id=current_user.username, email=current_user.email)


def register_user(
	request: Request,
	payload: AuthRegisterCredentials,
	_: TokenDependency,
	session: SessionDependency,
) -> AuthSessionRead:
	user = _create_user_account(session, payload)
	request.session["user_id"] = user.username
	return AuthSessionRead(user_id=user.username, email=user.email)


def login_user(
	request: Request,
	payload: AuthLoginCredentials,
	_: TokenDependency,
	session: SessionDependency,
) -> AuthSessionRead:
	attempt_key = _build_login_attempt_key(request, payload.user_id)
	user = _authenticate_user_account(
		session,
		payload,
		attempt_key=attempt_key,
	)
	request.session["user_id"] = user.username
	return AuthSessionRead(user_id=user.username, email=user.email)


def reset_password_with_email(
	payload: PasswordResetRequest,
	_: TokenDependency,
	session: SessionDependency,
) -> ActionMessageRead:
	_reset_user_password_with_email(session, payload)
	return ActionMessageRead(message="密码已重置，请使用新密码登录。")


def update_user_email(
	request: Request,
	payload: UserEmailUpdate,
	current_user: CurrentUserDependency,
	session: SessionDependency,
	_: TokenDependency,
) -> AuthSessionRead:
	user = _update_user_email(session, current_user, payload)
	request.session["user_id"] = user.username
	return AuthSessionRead(user_id=user.username, email=user.email)


def logout_user(request: Request, _: TokenDependency) -> Response:
	request.session.clear()
	return Response(status_code=204)


def issue_agent_token_with_password(
	request: Request,
	payload: AgentTokenIssueCreate,
	_: TokenDependency,
	session: SessionDependency,
) -> AgentTokenIssueRead:
	attempt_key = _build_login_attempt_key(request, payload.user_id)
	current_user = _authenticate_user_account(
		session,
		AuthLoginCredentials(user_id=payload.user_id, password=payload.password),
		attempt_key=attempt_key,
	)
	token, raw_token = _create_agent_access_token(
		session,
		current_user=current_user,
		name=payload.name,
		expires_in_days=payload.expires_in_days,
	)
	return AgentTokenIssueRead(
		**_to_agent_token_read(token).model_dump(),
		access_token=raw_token,
	)


def create_agent_token_for_current_session(
	payload: AgentTokenCreate,
	current_user: CurrentUserDependency,
	session: SessionDependency,
) -> AgentTokenIssueRead:
	token, raw_token = _create_agent_access_token(
		session,
		current_user=current_user,
		name=payload.name,
		expires_in_days=payload.expires_in_days,
	)
	return AgentTokenIssueRead(
		**_to_agent_token_read(token).model_dump(),
		access_token=raw_token,
	)


def list_agent_tokens(
	current_user: CurrentUserDependency,
	session: SessionDependency,
) -> list[AgentTokenRead]:
	_sync_expired_agent_tokens_for_user(
		session,
		user_id=current_user.username,
	)
	tokens = _list_active_agent_access_tokens_for_user(
		session,
		user_id=current_user.username,
	)
	return [_to_agent_token_read(token) for token in tokens]


def revoke_agent_token(
	token_id: int,
	current_user: CurrentUserDependency,
	session: SessionDependency,
) -> ActionMessageRead:
	token = session.get(AgentAccessToken, token_id)
	if token is None or token.user_id != current_user.username:
		raise HTTPException(status_code=404, detail="API Key 不存在。")

	if token.revoked_at is None:
		_revoke_agent_token_row(session, token)
		session.commit()

	return ActionMessageRead(message="API Key 已撤销。")


__all__ = [
	"AGENT_TOKEN_LAST_USED_UPDATE_INTERVAL",
	"CurrentUserDependency",
	"MAX_ACTIVE_AGENT_TOKENS_PER_USER",
	"SessionCurrentUserDependency",
	"TokenDependency",
	"_authenticate_agent_access_token",
	"_authenticate_user_account",
	"_build_login_attempt_key",
	"_create_agent_access_token",
	"_ensure_agent_registration",
	"_enforce_authenticated_request_rate_limit",
	"_get_agent_registration_by_name",
	"_is_agent_token_active",
	"_list_agent_access_tokens_for_user",
	"_list_active_agent_access_tokens_for_user",
	"_create_user_account",
	"_get_user",
	"_normalize_agent_name_header",
	"_reset_user_password_with_email",
	"_to_agent_token_read",
	"_update_user_email",
	"create_agent_token_for_current_session",
	"get_auth_session",
	"get_current_user",
	"get_session_current_user",
	"issue_agent_token_with_password",
	"list_agent_tokens",
	"login_user",
	"logout_user",
	"register_user",
	"reset_password_with_email",
	"revoke_agent_token",
	"update_user_email",
]
