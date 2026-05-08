import asyncio
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
import re

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlmodel import Session, select
from starlette.requests import Request

import app.database as database
from app import runtime_state
import app.main as main
from app.services.agent_service import (
	create_agent_task,
	get_agent_context,
	list_agent_registrations,
	list_agent_tasks,
)
from app.services.auth_service import get_current_user, revoke_agent_token
from app.services.cash_account_service import (
	create_account,
	create_cash_transfer,
	list_asset_mutation_audits,
)
from app.services.holding_transaction_service import (
	create_holding_transaction,
	get_security_quote,
	list_all_holding_transactions,
)
from app.models import (
	AgentAccessToken,
	AgentRegistration,
	AgentTask,
	AssetMutationAudit,
	CashAccount,
	CashLedgerEntry,
	CashTransfer,
	OutboxJob,
	SecurityHoldingTransaction,
	UserAccount,
)
from app.schemas import (
	AgentTokenCreate,
	AgentTaskCreate,
	AllocationSlice,
	AuthLoginCredentials,
	CashAccountCreate,
	CashTransferCreate,
	DashboardResponse,
	SecurityHoldingTransactionCreate,
	ValuedCashAccount,
	ValuedHolding,
)
from app.security import hash_password
from app.services.market_data import Quote
from app.services.auth_service import (
	MAX_ACTIVE_AGENT_TOKENS_PER_USER,
	MAX_DAILY_AGENT_TOKEN_CREATIONS,
	create_agent_token_for_current_session,
	login_user,
	list_agent_tokens,
)
from app.services import dashboard_query_service, job_service, service_context
from app.services.agent_demo_service import seed_agent_workspace_demo
from app.services.asset_record_service import list_asset_records

TOKEN_NAME_SUFFIXES = (
	"alpha",
	"beta",
	"gamma",
	"delta",
	"epsilon",
	"zeta",
	"eta",
	"theta",
	"iota",
	"kappa",
)


class StaticMarketDataClient:
	async def fetch_fx_rate(
		self,
		from_currency: str,
		to_currency: str,
		*,
		prefer_stale: bool = False,
		schedule_stale_refresh: bool = True,
	) -> tuple[float, list[str]]:
		del prefer_stale, schedule_stale_refresh
		if from_currency.upper() == to_currency.upper():
			return 1.0, []
		return 7.0, []

	async def fetch_hourly_price_series(
		self,
		symbol: str,
		*,
		market: str | None = None,
		start_at: datetime,
		end_at: datetime,
	) -> tuple[list[tuple[datetime, float]], str | None, list[str]]:
		return [], "USD", []

	async def fetch_quote(
		self,
		symbol: str,
		market: str | None = None,
		*,
		prefer_stale: bool = False,
		schedule_stale_refresh: bool = True,
	) -> tuple[Quote, list[str]]:
		del market, prefer_stale, schedule_stale_refresh
		return (
			Quote(
				symbol=symbol,
				name="Apple",
				price=188.5,
				currency="USD",
				market_time=datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc),
			),
			["cache-hit"],
		)

	def clear_runtime_caches(self, *, clear_search: bool = False) -> None:
		return None


def _reset_async_runtime_state() -> None:
	runtime_state.set_last_global_force_refresh_at(None)
	runtime_state.background_job_worker_task = None
	runtime_state.snapshot_rebuild_users_in_queue.clear()
	runtime_state.snapshot_rebuild_worker_task = None
	while True:
		try:
			runtime_state.snapshot_rebuild_queue.get_nowait()
		except asyncio.QueueEmpty:
			break
		runtime_state.snapshot_rebuild_queue.task_done()


@pytest.fixture
def session(postgres_engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[Session]:
	engine = postgres_engine
	monkeypatch.setattr(database, "engine", engine)
	monkeypatch.setattr(job_service, "engine", engine)
	_reset_async_runtime_state()

	with Session(engine) as db_session:
		yield db_session
	_reset_async_runtime_state()


@pytest.fixture(autouse=True)
def reset_runtime_state() -> Iterator[None]:
	main.dashboard_cache.clear()
	main.login_attempt_states.clear()
	_reset_async_runtime_state()
	yield
	main.dashboard_cache.clear()
	main.login_attempt_states.clear()
	_reset_async_runtime_state()


def make_user(session: Session, username: str = "tester") -> UserAccount:
	user = UserAccount(
		username=username,
		password_digest=hash_password("qwer1234"),
	)
	session.add(user)
	session.commit()
	session.refresh(user)
	return user


def build_request(
	*,
	method: str = "GET",
	path: str = "/",
	headers: dict[str, str] | None = None,
	session_data: dict[str, object] | None = None,
) -> Request:
	scope = {
		"type": "http",
		"method": method,
		"path": path,
		"scheme": "http",
		"http_version": "1.1",
		"query_string": b"",
		"headers": [
			(key.lower().encode("utf-8"), value.encode("utf-8"))
			for key, value in (headers or {}).items()
		],
		"client": ("127.0.0.1", 12345),
		"session": session_data or {},
	}
	return Request(scope)


def run_background_jobs(limit: int = 20) -> int:
	return asyncio.run(job_service.process_all_pending_background_jobs(limit=limit))


def test_create_agent_token_and_use_bearer_auth(session: Session) -> None:
	current_user = make_user(session)

	issued_token = create_agent_token_for_current_session(
		AgentTokenCreate(name="local-cli", expires_in_days=30),
		current_user,
		session,
	)

	assert issued_token.access_token.startswith("sk-")
	stored_token = session.exec(select(AgentAccessToken)).one()
	assert stored_token.user_id == "tester"
	assert stored_token.agent_registration_id is None
	assert stored_token.name == "local-cli"
	assert stored_token.token_hint.startswith("sk-")
	assert re.fullmatch(r"sk-[A-Za-z0-9_-]{2}\*{11}", stored_token.token_hint) is not None
	assert session.exec(select(AgentRegistration)).all() == []

	authenticated_user = get_current_user(
		build_request(
			headers={"Authorization": f"Bearer {issued_token.access_token}"},
		),
		session,
		None,
	)

	assert authenticated_user.username == "tester"
	session.refresh(stored_token)
	assert stored_token.last_used_at is not None
	assert session.exec(select(AgentRegistration)).all() == []

	agent_user = get_current_user(
		build_request(
			headers={
				"Authorization": f"Bearer {issued_token.access_token}",
				"Agent-Name": "quant-runner",
			},
		),
		session,
		None,
	)

	assert agent_user.username == "tester"
	stored_registration = session.exec(select(AgentRegistration)).one()
	assert stored_registration.user_id == "tester"
	assert stored_registration.name == "quant-runner"
	assert stored_registration.last_seen_at is not None
	assert stored_registration.request_count == 1
	assert stored_registration.latest_api_key_name == "local-cli"

	registrations = list_agent_registrations(agent_user, session, include_all_users=False)
	assert len(registrations) == 1
	assert registrations[0].name == "quant-runner"
	assert registrations[0].status == "ACTIVE"
	assert registrations[0].user_id == "tester"
	assert registrations[0].request_count == 1
	assert registrations[0].latest_api_key_name == "local-cli"


def test_get_current_user_accepts_browser_session_without_bearer(session: Session) -> None:
	current_user = make_user(session)

	authenticated_user = get_current_user(
		build_request(session_data={"user_id": current_user.username}),
		session,
		None,
	)

	assert authenticated_user.username == "tester"


def test_login_user_sets_session_for_follow_up_browser_requests(session: Session) -> None:
	make_user(session)
	request = build_request(
		method="POST",
		path="/api/auth/login",
		headers={"X-Client-Device-Id": "browser-1"},
		session_data={},
	)

	response = login_user(
		request,
		AuthLoginCredentials(user_id="tester", password="qwer1234"),
		None,
		session,
	)

	assert response.user_id == "tester"
	assert request.session["user_id"] == "tester"


def test_create_agent_token_for_current_session_only_returns_secret_once(session: Session) -> None:
	current_user = make_user(session)

	issued_token = create_agent_token_for_current_session(
		AgentTokenCreate(name="local-cli"),
		current_user,
		session,
	)

	assert issued_token.access_token.startswith("sk-")
	assert issued_token.expires_at is None
	assert re.fullmatch(r"sk-[A-Za-z0-9_-]{2}\*{11}", issued_token.token_hint) is not None

	listed_tokens = list_agent_tokens(current_user, session)
	assert len(listed_tokens) == 1
	assert listed_tokens[0].name == "local-cli"
	assert listed_tokens[0].token_hint == issued_token.token_hint
	assert not hasattr(listed_tokens[0], "access_token")


def test_list_agent_tokens_discards_revoked_keys(session: Session) -> None:
	current_user = make_user(session)

	create_agent_token_for_current_session(
		AgentTokenCreate(name="local-cli"),
		current_user,
		session,
	)
	revoked_issue = create_agent_token_for_current_session(
		AgentTokenCreate(name="stale-key"),
		current_user,
		session,
	)
	revoked_token = session.exec(
		select(AgentAccessToken).where(AgentAccessToken.name == "stale-key"),
	).one()
	assert revoked_issue.access_token.startswith("sk-")

	revoke_agent_token(revoked_token.id or 0, current_user, session)

	listed_tokens = list_agent_tokens(current_user, session)

	assert [token.name for token in listed_tokens] == ["local-cli"]


def test_list_agent_tokens_normalizes_malformed_token_hints(session: Session) -> None:
	current_user = make_user(session)
	issued_token = create_agent_token_for_current_session(
		AgentTokenCreate(name="local-cli"),
		current_user,
		session,
	)
	token_row = session.exec(
		select(AgentAccessToken).where(AgentAccessToken.name == "local-cli"),
	).one()
	token_row.token_hint = "...abc123"
	session.add(token_row)
	session.commit()

	listed_tokens = list_agent_tokens(current_user, session)

	assert issued_token.access_token.startswith("sk-")
	assert listed_tokens[0].token_hint == "sk-xx***********"


def test_agent_token_creation_rejects_more_than_five_active_keys(session: Session) -> None:
	current_user = make_user(session)

	for index in range(MAX_ACTIVE_AGENT_TOKENS_PER_USER):
		create_agent_token_for_current_session(
			AgentTokenCreate(name=f"worker-{TOKEN_NAME_SUFFIXES[index]}"),
			current_user,
			session,
		)

	with pytest.raises(HTTPException) as error:
		create_agent_token_for_current_session(
			AgentTokenCreate(name="worker-lambda"),
			current_user,
			session,
		)

	assert error.value.status_code == 409
	assert error.value.detail == "每个账号最多保留 5 个有效 API Key，请先删除旧 Key。"


def test_agent_token_creation_rejects_more_than_ten_daily_creations(session: Session) -> None:
	current_user = make_user(session)

	for index in range(MAX_DAILY_AGENT_TOKEN_CREATIONS):
		issued_token = create_agent_token_for_current_session(
			AgentTokenCreate(name=f"rotation-{TOKEN_NAME_SUFFIXES[index]}"),
			current_user,
			session,
		)
		token_row = session.exec(
			select(AgentAccessToken).where(
				AgentAccessToken.name == f"rotation-{TOKEN_NAME_SUFFIXES[index]}",
			),
		).one()
		revoke_agent_token(token_row.id or 0, current_user, session)
		assert issued_token.access_token.startswith("sk-")

	with pytest.raises(HTTPException) as error:
		create_agent_token_for_current_session(
			AgentTokenCreate(name="rotation-lambda"),
			current_user,
			session,
		)

	assert error.value.status_code == 429
	assert error.value.detail == "同一账号每天最多生成 10 次 API Key，请明天再试。"


def test_agent_token_creation_rejects_duplicate_active_names(session: Session) -> None:
	current_user = make_user(session)

	create_agent_token_for_current_session(
		AgentTokenCreate(name="daily-sync"),
		current_user,
		session,
	)

	with pytest.raises(HTTPException) as error:
		create_agent_token_for_current_session(
			AgentTokenCreate(name="daily-sync"),
			current_user,
			session,
		)

	assert error.value.status_code == 409
	assert error.value.detail == "当前账号已经存在同名的有效 API Key，请使用新的名称。"


def test_agent_token_name_must_use_lowercase_slug_format() -> None:
	with pytest.raises(ValidationError) as error:
		AgentTokenCreate(name="nightly-1")

	assert "API Key 名称仅支持小写字母和连字符" in str(error.value)


def test_listing_tokens_auto_revokes_expired_keys_and_marks_registration_inactive(
	session: Session,
) -> None:
	current_user = make_user(session)
	issued_token = create_agent_token_for_current_session(
		AgentTokenCreate(name="expiring-key", expires_in_days=30),
		current_user,
		session,
	)
	agent_user = get_current_user(
		build_request(
			headers={
				"Authorization": f"Bearer {issued_token.access_token}",
				"Agent-Name": "portfolio-copilot",
			},
		),
		session,
		None,
	)
	token_row = session.exec(
		select(AgentAccessToken).where(AgentAccessToken.name == "expiring-key"),
	).one()
	token_row.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
	session.add(token_row)
	session.commit()

	listed_tokens = list_agent_tokens(current_user, session)
	registrations = list_agent_registrations(agent_user, session, include_all_users=False)

	assert listed_tokens == []
	session.refresh(token_row)
	assert token_row.revoked_at is not None
	assert len(registrations) == 1
	assert registrations[0].status == "INACTIVE"
	assert registrations[0].latest_api_key_name is None


def test_expired_agent_token_is_rejected_during_bearer_auth(session: Session) -> None:
	current_user = make_user(session)
	issued_token = create_agent_token_for_current_session(
		AgentTokenCreate(name="soon-expired", expires_in_days=7),
		current_user,
		session,
	)
	token_row = session.exec(
		select(AgentAccessToken).where(AgentAccessToken.name == "soon-expired"),
	).one()
	token_row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
	session.add(token_row)
	session.commit()

	with pytest.raises(HTTPException) as error:
		get_current_user(
			build_request(
				headers={"Authorization": f"Bearer {issued_token.access_token}"},
			),
			session,
			None,
		)

	assert error.value.status_code == 401
	assert error.value.detail == "API Key 已过期。"
	session.refresh(token_row)
	assert token_row.revoked_at is not None


def test_direct_api_bearer_asset_write_is_recorded_as_api_source(session: Session) -> None:
	current_user = make_user(session)
	issued_token = create_agent_token_for_current_session(
		AgentTokenCreate(name="agent-audit-source", expires_in_days=30),
		current_user,
		session,
	)
	agent_user = get_current_user(
		build_request(headers={"Authorization": f"Bearer {issued_token.access_token}"}),
		session,
		None,
	)

	create_account(
		CashAccountCreate(
			name="Agent Wallet",
			platform="API",
			currency="CNY",
			balance=200,
			account_type="BANK",
		),
		agent_user,
		session,
	)

	mutations = list_asset_mutation_audits(agent_user, session, limit=20)
	assert mutations[0].actor_source == "API"
	assert mutations[0].api_key_name == "agent-audit-source"
	assert mutations[0].agent_name is None

	records = list_asset_records(
		agent_user,
		session,
		asset_class="cash",
		operation_kind="NEW",
		source="API",
	)
	assert len(records) == 1
	assert records[0].title == "Agent Wallet"
	assert records[0].source == "API"
	assert records[0].api_key_name == "agent-audit-source"
	assert records[0].agent_name is None


def test_agent_named_bearer_asset_write_registers_agent_and_audit_metadata(session: Session) -> None:
	current_user = make_user(session)
	issued_token = create_agent_token_for_current_session(
		AgentTokenCreate(name="portfolio-agent", expires_in_days=30),
		current_user,
		session,
	)
	agent_user = get_current_user(
		build_request(
			headers={
				"Authorization": f"Bearer {issued_token.access_token}",
				"Agent-Name": "portfolio-copilot",
			},
		),
		session,
		None,
	)

	create_account(
		CashAccountCreate(
			name="Agent Registered Wallet",
			platform="API",
			currency="CNY",
			balance=320,
			account_type="BANK",
		),
		agent_user,
		session,
	)

	registrations = list_agent_registrations(agent_user, session, include_all_users=False)
	assert len(registrations) == 1
	assert registrations[0].name == "portfolio-copilot"
	assert registrations[0].request_count == 1
	assert registrations[0].latest_api_key_name == "portfolio-agent"

	mutations = list_asset_mutation_audits(agent_user, session, limit=20)
	assert mutations[0].actor_source == "AGENT"
	assert mutations[0].api_key_name == "portfolio-agent"
	assert mutations[0].agent_name == "portfolio-copilot"

	records = list_asset_records(
		agent_user,
		session,
		asset_class="cash",
		operation_kind="NEW",
		source="AGENT",
	)
	assert len(records) == 1
	assert records[0].title == "Agent Registered Wallet"
	assert records[0].source == "AGENT"
	assert records[0].api_key_name == "portfolio-agent"
	assert records[0].agent_name == "portfolio-copilot"


def test_revoked_agent_token_can_no_longer_authenticate(session: Session) -> None:
	current_user = make_user(session)
	issued_token = create_agent_token_for_current_session(
		AgentTokenCreate(name="revoked-token", expires_in_days=30),
		current_user,
		session,
	)
	token_row = session.exec(select(AgentAccessToken)).one()

	response = revoke_agent_token(token_row.id or 0, current_user, session)

	assert response.message == "API Key 已撤销。"
	with pytest.raises(HTTPException) as error:
		get_current_user(
			build_request(
				headers={"Authorization": f"Bearer {issued_token.access_token}"},
			),
			session,
			None,
		)

	assert error.value.status_code == 401
	assert error.value.detail == "API Key 无效。"


def test_revoking_agent_token_marks_registration_inactive_and_hides_latest_key(
	session: Session,
) -> None:
	current_user = make_user(session)
	issued_token = create_agent_token_for_current_session(
		AgentTokenCreate(name="portfolio-agent", expires_in_days=30),
		current_user,
		session,
	)
	agent_user = get_current_user(
		build_request(
			headers={
				"Authorization": f"Bearer {issued_token.access_token}",
				"Agent-Name": "portfolio-copilot",
			},
		),
		session,
		None,
	)
	token_row = session.exec(
		select(AgentAccessToken).where(AgentAccessToken.name == "portfolio-agent"),
	).one()

	revoke_agent_token(token_row.id or 0, current_user, session)
	registrations = list_agent_registrations(agent_user, session, include_all_users=False)

	assert len(registrations) == 1
	assert registrations[0].name == "portfolio-copilot"
	assert registrations[0].status == "INACTIVE"
	assert registrations[0].latest_api_key_name is None


def test_stale_agent_metadata_without_agent_name_is_treated_as_direct_api(session: Session) -> None:
	current_user = make_user(session)
	task = AgentTask(
		user_id=current_user.username,
		request_source="AGENT",
		api_key_name="local-cli",
		agent_name=None,
		task_type="CREATE_CASH_TRANSFER",
		status="DONE",
		input_json=json.dumps({"note": "direct-api"}),
		result_json=json.dumps({"ok": True}),
	)
	audit = AssetMutationAudit(
		user_id=current_user.username,
		actor_user_id=current_user.username,
		actor_source="AGENT",
		api_key_name="local-cli",
		agent_name=None,
		agent_task_id=91,
		entity_type="CASH_ACCOUNT",
		entity_id=7,
		operation="CREATE",
		after_state=json.dumps(
			{
				"name": "API Wallet",
				"platform": "API",
				"balance": 120,
				"currency": "CNY",
			},
		),
	)
	session.add(task)
	session.add(audit)
	session.commit()

	tasks = list_agent_tasks(current_user, session, limit=50)
	records = list_asset_records(current_user, session, source="API")

	assert tasks[0].request_source == "API"
	assert tasks[0].agent_name is None
	assert records[0].source == "API"
	assert records[0].api_key_name == "local-cli"
	assert records[0].agent_name is None


def test_admin_can_list_agent_registrations_across_all_accounts(session: Session) -> None:
	admin_user = make_user(session, "admin")
	alice_user = make_user(session, "alice")
	bob_user = make_user(session, "bob")

	alice_token = create_agent_token_for_current_session(
		AgentTokenCreate(name="alpha", expires_in_days=30),
		alice_user,
		session,
	)
	get_current_user(
		build_request(
			headers={
				"Authorization": f"Bearer {alice_token.access_token}",
				"Agent-Name": "alpha-bot",
			},
		),
		session,
		None,
	)
	bob_token = create_agent_token_for_current_session(
		AgentTokenCreate(name="beta", expires_in_days=30),
		bob_user,
		session,
	)
	get_current_user(
		build_request(
			headers={
				"Authorization": f"Bearer {bob_token.access_token}",
				"Agent-Name": "beta-bot",
			},
		),
		session,
		None,
	)

	registrations = list_agent_registrations(admin_user, session, include_all_users=True)

	assert {(item.user_id, item.name) for item in registrations} == {
		("alice", "alpha-bot"),
		("bob", "beta-bot"),
	}


def test_list_all_holding_transactions_supports_symbol_filter(
	session: Session,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	current_user = make_user(session)
	monkeypatch.setattr(service_context, "market_data_client", StaticMarketDataClient())

	create_holding_transaction(
		SecurityHoldingTransactionCreate(
			side="BUY",
			symbol="AAPL",
			name="Apple",
			quantity=2,
			price=180,
			fallback_currency="USD",
			market="US",
			traded_on=date(2026, 3, 8),
		),
		current_user,
		session,
	)
	create_holding_transaction(
		SecurityHoldingTransactionCreate(
			side="BUY",
			symbol="TSLA",
			name="Tesla",
			quantity=1,
			price=220,
			fallback_currency="USD",
			market="US",
			traded_on=date(2026, 3, 9),
		),
		current_user,
		session,
	)

	transactions = list_all_holding_transactions(
		current_user,
		session,
		symbol="AAPL",
		market="US",
		side=None,
		limit=50,
	)

	assert len(transactions) == 1
	assert transactions[0].symbol == "AAPL"
	assert transactions[0].market == "US"


def test_list_all_holding_transactions_includes_sell_cash_settlement_metadata(
	session: Session,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	current_user = make_user(session)
	monkeypatch.setattr(service_context, "market_data_client", StaticMarketDataClient())

	create_holding_transaction(
		SecurityHoldingTransactionCreate(
			side="BUY",
			symbol="AAPL",
			name="Apple",
			quantity=2,
			price=180,
			fallback_currency="USD",
			market="US",
			traded_on=date(2026, 3, 8),
		),
		current_user,
		session,
	)
	cash_account = create_account(
		CashAccountCreate(
			name="Broker Cash",
			platform="Futu",
			currency="CNY",
			balance=500,
			account_type="BANK",
		),
		current_user,
		session,
	)
	create_holding_transaction(
		SecurityHoldingTransactionCreate(
			side="SELL",
			symbol="AAPL",
			name="Apple",
			quantity=1,
			price=188.5,
			fallback_currency="USD",
			market="US",
			traded_on=date(2026, 3, 9),
			sell_proceeds_handling="ADD_TO_EXISTING_CASH",
			sell_proceeds_account_id=cash_account.id,
		),
		current_user,
		session,
	)

	transactions = list_all_holding_transactions(
		current_user,
		session,
		symbol="AAPL",
		market="US",
		side=None,
		limit=50,
	)

	sell_transaction = next(item for item in transactions if item.side == "SELL")
	assert sell_transaction.sell_proceeds_handling == "ADD_TO_EXISTING_CASH"
	assert sell_transaction.sell_proceeds_account_id == cash_account.id


def test_get_security_quote_returns_live_quote_for_agent(
	session: Session,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	current_user = make_user(session)
	monkeypatch.setattr(service_context, "market_data_client", StaticMarketDataClient())

	quote = asyncio.run(get_security_quote("aapl", "us", current_user))

	assert quote.symbol == "AAPL"
	assert quote.market == "US"
	assert quote.price == 188.5
	assert quote.currency == "USD"
	assert quote.warnings == ["cache-hit"]


def test_get_agent_context_returns_dashboard_summary_and_recent_transactions(
	session: Session,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	current_user = make_user(session)
	monkeypatch.setattr(service_context, "market_data_client", StaticMarketDataClient())
	create_account(
		CashAccountCreate(
			name="Broker Cash",
			platform="Futu",
			currency="USD",
			balance=500,
			account_type="BANK",
		),
		current_user,
		session,
	)
	create_holding_transaction(
		SecurityHoldingTransactionCreate(
			side="BUY",
			symbol="AAPL",
			name="Apple",
			quantity=2,
			price=180,
			fallback_currency="USD",
			market="US",
			traded_on=date(2026, 3, 9),
		),
		current_user,
		session,
	)

	async def fake_get_dashboard(
		user: UserAccount,
		db_session: Session,
		refresh: bool = False,
	) -> DashboardResponse:
		assert user.username == current_user.username
		assert db_session is session
		assert refresh is False
		return DashboardResponse(
			server_today=date(2026, 3, 9),
			total_value_cny=10000,
			cash_value_cny=3500,
			holdings_value_cny=6500,
			fixed_assets_value_cny=0,
			liabilities_value_cny=0,
			other_assets_value_cny=0,
			usd_cny_rate=7.0,
			hkd_cny_rate=0.92,
			cash_accounts=[
				ValuedCashAccount(
					id=1,
					name="Broker Cash",
					platform="Futu",
					balance=500,
					currency="USD",
					account_type="BANK",
					fx_to_cny=7.0,
					value_cny=3500,
				),
			],
			holdings=[
				ValuedHolding(
					id=1,
					symbol="AAPL",
					name="Apple",
					quantity=2,
					fallback_currency="USD",
					cost_basis_price=180,
					market="US",
					price=188.5,
					price_currency="USD",
					fx_to_cny=7.0,
					value_cny=2639,
					return_pct=4.72,
					last_updated=datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc),
				),
			],
			fixed_assets=[],
			liabilities=[],
			other_assets=[],
			allocation=[AllocationSlice(label="投资类", value=6500)],
			hour_series=[],
			day_series=[],
			month_series=[],
			year_series=[],
			holdings_return_hour_series=[],
			holdings_return_day_series=[],
			holdings_return_month_series=[],
			holdings_return_year_series=[],
			holding_return_series=[],
			warnings=["quote-cache-hit"],
		)

	monkeypatch.setattr(dashboard_query_service, "get_dashboard", fake_get_dashboard)

	context = asyncio.run(
		get_agent_context(
			current_user,
			session,
			refresh=False,
			transaction_limit=10,
		),
	)

	assert context.user_id == current_user.username
	assert context.total_value_cny == 10000
	assert context.pending_history_sync_requests == 1
	assert len(context.cash_accounts) == 1
	assert len(context.holdings) == 1
	assert len(context.recent_holding_transactions) == 1
	assert context.recent_holding_transactions[0].symbol == "AAPL"
	assert context.warnings == ["quote-cache-hit"]


def test_create_holding_transaction_replays_by_idempotency_key(
	session: Session,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	current_user = make_user(session)
	monkeypatch.setattr(service_context, "market_data_client", StaticMarketDataClient())

	first = create_holding_transaction(
		SecurityHoldingTransactionCreate(
			side="BUY",
			symbol="AAPL",
			name="Apple",
			quantity=1,
			price=180,
			fallback_currency="USD",
			market="US",
			traded_on=date(2026, 3, 9),
		),
		current_user,
		session,
		"buy-001",
	)
	second = create_holding_transaction(
		SecurityHoldingTransactionCreate(
			side="BUY",
			symbol="AAPL",
			name="Apple",
			quantity=1,
			price=180,
			fallback_currency="USD",
			market="US",
			traded_on=date(2026, 3, 9),
		),
		current_user,
		session,
		"buy-001",
	)

	assert first.transaction.id == second.transaction.id
	assert len(session.exec(select(SecurityHoldingTransaction)).all()) == 1


def test_list_holding_transactions_includes_buy_funding_metadata(
	session: Session,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	current_user = make_user(session)
	monkeypatch.setattr(service_context, "market_data_client", StaticMarketDataClient())
	cash_account = create_account(
		CashAccountCreate(
			name="主账户",
			platform="Bank",
			currency="CNY",
			balance=2000,
			account_type="BANK",
		),
		current_user,
		session,
	)
	create_holding_transaction(
		SecurityHoldingTransactionCreate(
			side="BUY",
			symbol="AAPL",
			name="Apple",
			quantity=1,
			price=180,
			fallback_currency="USD",
			market="US",
			traded_on=date(2026, 3, 9),
			buy_funding_handling="DEDUCT_FROM_EXISTING_CASH",
			buy_funding_account_id=cash_account.id,
		),
		current_user,
		session,
	)

	transactions = list_all_holding_transactions(
		current_user,
		session,
		symbol="AAPL",
		market="US",
		side=None,
		limit=50,
	)

	assert transactions[0].buy_funding_handling == "DEDUCT_FROM_EXISTING_CASH"
	assert transactions[0].buy_funding_account_id == cash_account.id


def test_create_cash_transfer_replays_by_idempotency_key(session: Session) -> None:
	current_user = make_user(session)
	source_account = create_account(
		CashAccountCreate(
			name="主账户",
			platform="Bank",
			currency="CNY",
			balance=1000,
			account_type="BANK",
		),
		current_user,
		session,
	)
	target_account = create_account(
		CashAccountCreate(
			name="备用金",
			platform="Cash",
			currency="CNY",
			balance=100,
			account_type="CASH",
		),
		current_user,
		session,
	)

	first = create_cash_transfer(
		CashTransferCreate(
			from_account_id=source_account.id or 0,
			to_account_id=target_account.id or 0,
			source_amount=200,
			transferred_on=date(2026, 3, 9),
		),
		current_user,
		session,
		"transfer-001",
	)
	second = create_cash_transfer(
		CashTransferCreate(
			from_account_id=source_account.id or 0,
			to_account_id=target_account.id or 0,
			source_amount=200,
			transferred_on=date(2026, 3, 9),
		),
		current_user,
		session,
		"transfer-001",
	)

	assert first.transfer.id == second.transfer.id
	assert len(session.exec(select(CashTransfer)).all()) == 1


def test_create_agent_task_executes_cash_transfer(session: Session) -> None:
	current_user = make_user(session)
	source_account = create_account(
		CashAccountCreate(
			name="主账户",
			platform="Bank",
			currency="CNY",
			balance=500,
			account_type="BANK",
		),
		current_user,
		session,
	)
	target_account = create_account(
		CashAccountCreate(
			name="零钱",
			platform="Cash",
			currency="CNY",
			balance=0,
			account_type="CASH",
		),
		current_user,
		session,
	)

	task = create_agent_task(
		AgentTaskCreate(
			task_type="CREATE_CASH_TRANSFER",
			payload={
				"from_account_id": source_account.id,
				"to_account_id": target_account.id,
				"source_amount": 120,
				"transferred_on": "2026-03-09",
			},
		),
		current_user,
		session,
		"agent-task-001",
	)

	assert task.status == "PENDING"
	assert task.result is None
	jobs = list(
		session.exec(
			select(OutboxJob).where(OutboxJob.job_type == "AGENT_TASK_EXECUTION"),
		).all(),
	)
	assert len(jobs) == 1

	assert run_background_jobs() >= 1

	session.expire_all()
	source_account_row = session.get(CashAccount, source_account.id)
	target_account_row = session.get(CashAccount, target_account.id)
	stored_task = session.get(AgentTask, task.id)
	assert source_account_row is not None
	assert target_account_row is not None
	assert source_account_row.balance == 380
	assert target_account_row.balance == 120
	assert stored_task is not None
	assert stored_task.status == "DONE"
	assert stored_task.result_json is not None
	result_payload = json.loads(stored_task.result_json)
	assert Decimal(result_payload["transfer"]["source_amount"]) == Decimal("120")
	assert len(session.exec(select(AgentTask)).all()) == 1


def test_agent_task_update_cash_transfer_links_mutation_audit(session: Session) -> None:
	current_user = make_user(session)
	source_account = create_account(
		CashAccountCreate(
			name="主账户",
			platform="Bank",
			currency="CNY",
			balance=600,
			account_type="BANK",
		),
		current_user,
		session,
	)
	target_account = create_account(
		CashAccountCreate(
			name="备用金",
			platform="Cash",
			currency="CNY",
			balance=0,
			account_type="CASH",
		),
		current_user,
		session,
	)
	created_transfer = create_cash_transfer(
		CashTransferCreate(
			from_account_id=source_account.id or 0,
			to_account_id=target_account.id or 0,
			source_amount=120,
			transferred_on=date(2026, 3, 9),
		),
		current_user,
		session,
	)

	task = create_agent_task(
		AgentTaskCreate(
			task_type="UPDATE_CASH_TRANSFER",
			payload={
				"transfer_id": created_transfer.transfer.id,
				"source_amount": 80,
				"transferred_on": "2026-03-10",
				"note": "agent corrected transfer",
			},
		),
		current_user,
		session,
		"agent-task-transfer-update-001",
	)

	assert task.status == "PENDING"
	assert task.result is None
	assert run_background_jobs() >= 1
	session.expire_all()
	stored_task = session.get(AgentTask, task.id)
	assert stored_task is not None
	assert stored_task.status == "DONE"
	assert stored_task.result_json is not None
	result_payload = json.loads(stored_task.result_json)
	assert Decimal(result_payload["transfer"]["source_amount"]) == Decimal("80")
	mutations = list_asset_mutation_audits(
		current_user,
		session,
		limit=20,
		agent_task_id=task.id,
	)
	assert any(item.entity_type == "CASH_TRANSFER" for item in mutations)
	assert any(item.entity_type == "CASH_ACCOUNT" for item in mutations)
	db_mutations = list(
		session.exec(
			select(AssetMutationAudit).where(AssetMutationAudit.agent_task_id == task.id),
		),
	)
	assert len(db_mutations) >= 2


def test_agent_task_can_create_and_delete_manual_cash_ledger_adjustment(
	session: Session,
) -> None:
	current_user = make_user(session)
	account = create_account(
		CashAccountCreate(
			name="主账户",
			platform="Bank",
			currency="CNY",
			balance=100,
			account_type="BANK",
		),
		current_user,
		session,
	)

	create_task = create_agent_task(
		AgentTaskCreate(
			task_type="CREATE_CASH_LEDGER_ADJUSTMENT",
			payload={
				"cash_account_id": account.id,
				"amount": 15,
				"happened_on": "2026-03-10",
				"note": "agent manual adjustment",
			},
		),
		current_user,
		session,
		"agent-task-ledger-create-001",
	)

	assert create_task.status == "PENDING"
	assert run_background_jobs() >= 1
	session.expire_all()
	stored_create_task = session.get(AgentTask, create_task.id)
	assert stored_create_task is not None
	assert stored_create_task.status == "DONE"
	assert stored_create_task.result_json is not None
	entry_id = int(json.loads(stored_create_task.result_json)["entry"]["id"])
	entry = session.get(CashLedgerEntry, entry_id)
	assert entry is not None
	assert entry.entry_type == "MANUAL_ADJUSTMENT"

	delete_task = create_agent_task(
		AgentTaskCreate(
			task_type="DELETE_CASH_LEDGER_ADJUSTMENT",
			payload={
				"entry_id": entry_id,
			},
		),
		current_user,
		session,
		"agent-task-ledger-delete-001",
	)

	assert delete_task.status == "PENDING"
	assert run_background_jobs() >= 1
	session.expire_all()
	stored_delete_task = session.get(AgentTask, delete_task.id)
	assert stored_delete_task is not None
	assert stored_delete_task.status == "DONE"
	assert stored_delete_task.result_json is not None
	assert json.loads(stored_delete_task.result_json) == {"message": "手工账本调整已删除。"}
	assert session.get(CashLedgerEntry, entry_id) is None
	mutations = list_asset_mutation_audits(
		current_user,
		session,
		limit=20,
		agent_task_id=delete_task.id,
	)
	assert any(item.entity_type == "CASH_LEDGER_ADJUSTMENT" for item in mutations)


def test_seed_agent_workspace_demo_creates_registrations_tasks_and_records(
	session: Session,
) -> None:
	admin_user = make_user(session, "admin")

	summary = seed_agent_workspace_demo(session, user_id=admin_user.username)

	registrations = list_agent_registrations(admin_user, session, include_all_users=False)
	tasks = list_asset_mutation_audits(admin_user, session, limit=50)
	agent_records = list_asset_records(admin_user, session, source="AGENT", limit=120)
	direct_api_records = list_asset_records(admin_user, session, source="API", limit=120)

	assert summary.registrations == 2
	assert summary.active_registrations == 1
	assert summary.tasks == 3
	assert summary.direct_api_records == 1
	assert summary.agent_records == len(agent_records)
	assert {(item.name, item.status) for item in registrations} == {
		("history-audit-bot", "INACTIVE"),
		("rebalancer-bot", "ACTIVE"),
	}
	assert any(task.agent_task_id is not None for task in tasks)
	assert any(record.title == "Agent API 沙盒账户" for record in direct_api_records)
	assert any(record.agent_task_id is not None for record in agent_records)


def test_seed_agent_workspace_demo_is_idempotent(session: Session) -> None:
	admin_user = make_user(session, "admin")

	first = seed_agent_workspace_demo(session, user_id=admin_user.username)
	second = seed_agent_workspace_demo(session, user_id=admin_user.username)

	registrations = list_agent_registrations(admin_user, session, include_all_users=False)
	agent_records = list_asset_records(admin_user, session, source="AGENT", limit=120)
	direct_api_records = list_asset_records(admin_user, session, source="API", limit=120)

	assert first == second
	assert len(registrations) == 2
	assert len(session.exec(select(AgentTask)).all()) == 3
	assert len(agent_records) == first.agent_records
	assert len(direct_api_records) == first.direct_api_records
