import asyncio
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import threading
from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlmodel import Session, select

from app import runtime_state
import app.main as main
from app.runtime_state import LiveHoldingReturnPoint
from app.services.asset_entry_service import (
	create_fixed_asset,
	create_liability,
	create_other_asset,
)
from app.services.auth_service import (
	_authenticate_user_account,
	_create_user_account,
	_update_user_email,
	_reset_user_password_with_email,
)
from app.services.cash_account_service import (
	create_account,
	create_cash_ledger_adjustment,
	create_cash_transfer,
	delete_cash_ledger_adjustment,
	delete_account,
	list_accounts,
	update_account,
	update_cash_ledger_adjustment,
	update_cash_transfer,
)
from app.services.common_service import _coerce_utc_datetime
from app.services.dashboard_live_service import (
	_persist_hour_snapshot,
	_persist_holdings_return_snapshot,
	_summarize_holdings_return_state,
)
from app.services.holding_transaction_service import (
	create_holding,
	create_holding_transaction,
	delete_holding,
	delete_holding_transaction,
	list_holding_transactions,
	list_holdings,
	update_holding,
	update_holding_transaction,
)
from app.models import (
	CashAccount,
	CashLedgerEntry,
	CashTransfer,
	FixedAsset,
	HoldingHistorySyncRequest,
	HoldingPerformanceSnapshot,
	HoldingTransactionCashSettlement,
	LiabilityEntry,
	OtherAsset,
	PortfolioSnapshot,
	RealtimeHoldingPerformanceSnapshot,
	RealtimePortfolioSnapshot,
	SecurityHolding,
	SecurityHoldingTransaction,
	UserAccount,
)
from app.schemas import (
	AuthLoginCredentials,
	AuthRegisterCredentials,
	CashAccountCreate,
	CashTransferCreate,
	CashTransferUpdate,
	CashAccountUpdate,
	CashLedgerAdjustmentCreate,
	CashLedgerAdjustmentUpdate,
	DashboardResponse,
	FixedAssetCreate,
	LiabilityEntryCreate,
	OtherAssetCreate,
	PasswordResetRequest,
	SecurityHoldingCreate,
	SecurityHoldingTransactionCreate,
	SecurityHoldingTransactionUpdate,
	SecurityHoldingUpdate,
	UserEmailUpdate,
	ValuedHolding,
)
from app.security import verify_password
from app.services.market_data import Quote, QuoteLookupError
from app.services import (
	common_service,
	dashboard_query_service,
	history_service,
	service_context,
)
import app.services.realtime_analytics_service as realtime_analytics_service


def D(value: str | int | float) -> Decimal:
	return Decimal(str(value))


def sql_expr(value: object) -> Any:
	return value


class _InMemoryRedisLock:
	def __init__(self, lock: threading.Lock) -> None:
		self._lock = lock

	def acquire(self, blocking: bool = True) -> bool:
		return self._lock.acquire(blocking=blocking)

	def release(self) -> None:
		self._lock.release()


class FakeRedisLockClient:
	def __init__(self) -> None:
		self._locks: dict[str, threading.Lock] = {}
		self._guard = threading.Lock()

	def lock(
		self,
		name: str,
		*,
		timeout: float | None = None,
		blocking_timeout: float | None = None,
		thread_local: bool | None = None,
	) -> _InMemoryRedisLock:
		del timeout, blocking_timeout, thread_local
		with self._guard:
			lock = self._locks.setdefault(name, threading.Lock())
		return _InMemoryRedisLock(lock)


class StaticMarketDataClient:
	async def fetch_fx_rate(
		self,
		from_currency: str,
		to_currency: str,
		*,
		prefer_stale: bool = False,
		schedule_stale_refresh: bool = True,
	) -> tuple[Decimal, list[str]]:
		del prefer_stale, schedule_stale_refresh
		if from_currency.upper() == to_currency.upper():
			return Decimal("1"), []
		return Decimal("7"), []

	async def fetch_hourly_price_series(
		self,
		symbol: str,
		*,
		market: str | None = None,
		start_at: datetime,
		end_at: datetime,
	) -> tuple[list[tuple[datetime, Decimal]], str | None, list[str]]:
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
				price=Decimal("100"),
				currency="USD",
				market_time=datetime(2026, 3, 1, tzinfo=timezone.utc),
			),
			[],
		)

	def clear_runtime_caches(self, *, clear_search: bool = False) -> None:
		return None


class WarningMarketDataClient(StaticMarketDataClient):
	FALLBACK_WARNING = (
		"1810.HK 行情源不可用，已回退到最近缓存值: "
		"Eastmoney quote request failed for 1810.HK (HTTP 429 Too Many Requests)."
	)
	DELAY_WARNING = "1810.HK 行情返回延迟，展示最近成交价。"

	async def fetch_quote(
		self,
		symbol: str,
		market: str | None = None,
		*,
		prefer_stale: bool = False,
		schedule_stale_refresh: bool = True,
	) -> tuple[Quote, list[str]]:
		quote, _warnings = await super().fetch_quote(
			symbol,
			market,
			prefer_stale=prefer_stale,
			schedule_stale_refresh=schedule_stale_refresh,
		)
		return quote, [self.FALLBACK_WARNING, self.DELAY_WARNING]


def _reset_async_runtime_state() -> None:
	runtime_state.set_last_global_force_refresh_at(None)
	runtime_state.set_last_realtime_analytics_sampled_at(None)
	runtime_state.snapshot_rebuild_users_in_queue.clear()
	runtime_state.snapshot_rebuild_worker_task = None
	while True:
		try:
			runtime_state.snapshot_rebuild_queue.get_nowait()
		except asyncio.QueueEmpty:
			break
		runtime_state.snapshot_rebuild_queue.task_done()


@pytest.fixture
def session(postgres_engine) -> Iterator[Session]:
	engine = postgres_engine
	_reset_async_runtime_state()

	with Session(engine) as db_session:
		yield db_session
	_reset_async_runtime_state()


@pytest.fixture(autouse=True)
def reset_login_attempt_state() -> Iterator[None]:
	main.login_attempt_states.clear()
	yield
	main.login_attempt_states.clear()
	_reset_async_runtime_state()


def make_user(session: Session, username: str = "tester") -> UserAccount:
	user = UserAccount(
		username=username,
		password_digest="scrypt$16384$8$1$bc13ea73dad1a1d781e1bf06e769ccda$"
		"de4af04355be41e4ec61f7dc8b3c19fcc4fc940ba47784324063d4169d57e80a"
		"14cc1588be5fea70338075226ff4b32aafe37ab0a114d05b70e0a2364a0d2bf7",
	)
	session.add(user)
	session.commit()
	return user


def test_create_account_persists_account_type_and_note(session: Session) -> None:
	current_user = make_user(session)
	account = create_account(
		CashAccountCreate(
			name="Emergency Fund",
			platform="Alipay",
			currency="cny",
			balance=D("1280.5"),
			account_type="alipay",
			started_on=date(2026, 3, 1),
			note="  spare cash  ",
		),
		current_user,
		session,
	)

	assert account.id is not None
	assert account.currency == "CNY"
	assert account.account_type == "ALIPAY"
	assert account.started_on == date(2026, 3, 1)
	assert account.note == "spare cash"

	stored_account = session.get(CashAccount, account.id)
	assert stored_account is not None
	assert stored_account.user_id == current_user.username
	assert stored_account.started_on == date(2026, 3, 1)
	assert stored_account.account_type == "ALIPAY"
	assert stored_account.note == "spare cash"


def test_update_account_keeps_new_fields_when_omitted_from_payload(session: Session) -> None:
	current_user = make_user(session)
	account = create_account(
		CashAccountCreate(
			name="Wallet",
			platform="Cash",
			currency="cny",
			balance=D("50"),
			account_type="cash",
			note="Daily spending",
		),
		current_user,
		session,
	)

	updated_account = update_account(
		account.id or 0,
		CashAccountUpdate(
			name="Pocket Wallet",
			platform="Cash",
			currency="usd",
			balance=D("66.5"),
		),
		current_user,
		session,
	)

	assert updated_account.name == "Pocket Wallet"
	assert updated_account.currency == "USD"
	assert updated_account.balance == 66.5
	assert updated_account.account_type == "CASH"
	assert updated_account.note == "Daily spending"


def test_delete_account_removes_record(session: Session) -> None:
	current_user = make_user(session)
	account = create_account(
		CashAccountCreate(
			name="Checking",
			platform="Bank",
			currency="cny",
			balance=D("800"),
			account_type="bank",
		),
		current_user,
		session,
	)

	response = delete_account(account.id or 0, current_user, session)

	assert response.status_code == 204
	assert session.exec(select(CashAccount)).all() == []


def test_delete_account_cascades_related_cash_transfers_and_rebalances_other_accounts(
	session: Session,
) -> None:
	current_user = make_user(session)
	source_account = create_account(
		CashAccountCreate(
			name="主账户",
			platform="Bank",
			currency="cny",
			balance=D("500"),
			account_type="bank",
		),
		current_user,
		session,
	)
	target_account = create_account(
		CashAccountCreate(
			name="备用金",
			platform="Cash",
			currency="cny",
			balance=D("200"),
			account_type="cash",
		),
		current_user,
		session,
	)
	created_transfer = create_cash_transfer(
		CashTransferCreate(
			from_account_id=source_account.id or 0,
			to_account_id=target_account.id or 0,
			source_amount=D("100"),
			transferred_on=date(2026, 3, 2),
			note="首次划转",
		),
		current_user,
		session,
	)

	assert created_transfer.to_account.balance == 300.0

	response = delete_account(source_account.id or 0, current_user, session)

	assert response.status_code == 204
	assert session.get(CashAccount, source_account.id) is None
	refreshed_target_account = session.get(CashAccount, target_account.id)
	assert refreshed_target_account is not None
	assert refreshed_target_account.balance == 200.0
	assert session.exec(select(CashTransfer)).all() == []
	assert session.exec(
		select(CashLedgerEntry).where(sql_expr(CashLedgerEntry.cash_transfer_id).is_not(None)),
	).all() == []


def test_delete_account_cascades_holding_cash_settlement_but_keeps_trade_record(
	session: Session,
) -> None:
	current_user = make_user(session)
	cash_account = create_account(
		CashAccountCreate(
			name="主账户",
			platform="Bank",
			currency="cny",
			balance=D("1000"),
			account_type="bank",
		),
		current_user,
		session,
	)

	applied_buy = create_holding_transaction(
		SecurityHoldingTransactionCreate(
			side="BUY",
			symbol="aapl",
			name="Apple",
			quantity=D("1"),
			price=D("100"),
			fallback_currency="usd",
			market="us",
			traded_on=date(2026, 3, 1),
			buy_funding_handling="DEDUCT_FROM_EXISTING_CASH",
			buy_funding_account_id=cash_account.id,
		),
		current_user,
		session,
	)

	response = delete_account(cash_account.id or 0, current_user, session)

	assert response.status_code == 204
	assert session.get(CashAccount, cash_account.id) is None
	assert session.exec(select(HoldingTransactionCashSettlement)).all() == []
	assert session.exec(
		select(CashLedgerEntry)
		.where(CashLedgerEntry.holding_transaction_id == applied_buy.transaction.id),
	).all() == []
	assert session.get(SecurityHoldingTransaction, applied_buy.transaction.id) is not None


def test_delete_holding_transaction_cleans_stale_cash_settlement_without_blocking(
	session: Session,
) -> None:
	current_user = make_user(session)
	cash_account = create_account(
		CashAccountCreate(
			name="主账户",
			platform="Bank",
			currency="cny",
			balance=D("1000"),
			account_type="bank",
		),
		current_user,
		session,
	)

	applied_buy = create_holding_transaction(
		SecurityHoldingTransactionCreate(
			side="BUY",
			symbol="aapl",
			name="Apple",
			quantity=D("1"),
			price=D("100"),
			fallback_currency="usd",
			market="us",
			traded_on=date(2026, 3, 1),
			buy_funding_handling="DEDUCT_FROM_EXISTING_CASH",
			buy_funding_account_id=cash_account.id,
		),
		current_user,
		session,
	)
	stale_account = session.get(CashAccount, cash_account.id)
	assert stale_account is not None
	session.delete(stale_account)
	session.commit()

	response = delete_holding_transaction(applied_buy.transaction.id or 0, current_user, session)

	assert response.status_code == 204
	assert session.get(SecurityHoldingTransaction, applied_buy.transaction.id) is None
	assert session.exec(select(HoldingTransactionCashSettlement)).all() == []
	assert session.exec(
		select(CashLedgerEntry)
		.where(CashLedgerEntry.holding_transaction_id == applied_buy.transaction.id),
	).all() == []


def test_delete_account_returns_404_when_missing(session: Session) -> None:
	current_user = make_user(session)
	with pytest.raises(HTTPException) as error:
		delete_account(9999, current_user, session)

	assert error.value.status_code == 404
	assert error.value.detail == "Account not found."


def test_authenticate_user_account_rejects_short_wrong_password_with_401(
	session: Session,
) -> None:
	make_user(session)

	with pytest.raises(HTTPException) as error:
		_authenticate_user_account(
			session,
			AuthLoginCredentials(user_id="tester", password="short"),
		)

	assert error.value.status_code == 401
	assert error.value.detail == "账号或密码错误。"


def test_authenticate_user_account_prompts_password_reset_after_five_wrong_attempts(
	session: Session,
) -> None:
	make_user(session)
	attempt_key = ("tester", "device:test-browser")
	credentials = AuthLoginCredentials(user_id="tester", password="wrong-password")

	for _ in range(4):
		with pytest.raises(HTTPException) as error:
			_authenticate_user_account(
				session,
				credentials,
				attempt_key=attempt_key,
			)
		assert error.value.status_code == 401
		assert error.value.detail == "账号或密码错误。"

	with pytest.raises(HTTPException) as error:
		_authenticate_user_account(
			session,
			credentials,
			attempt_key=attempt_key,
		)

	assert error.value.status_code == 401
	assert "是否忘记密码" in error.value.detail


def test_authenticate_user_account_rate_limits_after_eight_attempts_in_one_minute(
	session: Session,
) -> None:
	make_user(session)
	attempt_key = ("tester", "device:test-browser")
	credentials = AuthLoginCredentials(user_id="tester", password="wrong-password")

	for _ in range(8):
		with pytest.raises(HTTPException) as error:
			_authenticate_user_account(
				session,
				credentials,
				attempt_key=attempt_key,
			)
		assert error.value.status_code == 401

	with pytest.raises(HTTPException) as error:
		_authenticate_user_account(
			session,
			credentials,
			attempt_key=attempt_key,
		)

	assert error.value.status_code == 429
	assert "1 分钟内最多尝试 8 次" in error.value.detail


def test_authenticate_user_account_uses_cluster_safe_login_attempt_lock(
	session: Session,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	make_user(session)

	class ExplodingProcessLocalLock:
		def __enter__(self) -> None:
			raise AssertionError("Process-local login lock should not be used anymore.")

		def __exit__(self, exc_type, exc, tb) -> bool:
			return False

	monkeypatch.setattr(runtime_state, "redis_client", FakeRedisLockClient())
	monkeypatch.setattr(runtime_state, "login_attempts_lock", ExplodingProcessLocalLock())

	with pytest.raises(HTTPException) as error:
		_authenticate_user_account(
			session,
			AuthLoginCredentials(user_id="tester", password="wrong-password"),
			attempt_key=("tester", "device:test-browser"),
		)

	assert error.value.status_code == 401
	assert error.value.detail == "账号或密码错误。"


def test_authenticate_user_account_success_resets_consecutive_failed_counter(
	session: Session,
) -> None:
	make_user(session)
	attempt_key = ("tester", "device:test-browser")
	wrong_credentials = AuthLoginCredentials(user_id="tester", password="wrong-password")

	for _ in range(5):
		with pytest.raises(HTTPException):
			_authenticate_user_account(
				session,
				wrong_credentials,
				attempt_key=attempt_key,
			)

	user = _authenticate_user_account(
		session,
		AuthLoginCredentials(user_id="tester", password="qwer1234"),
		attempt_key=attempt_key,
	)
	assert user.username == "tester"

	with pytest.raises(HTTPException) as error:
		_authenticate_user_account(
			session,
			wrong_credentials,
			attempt_key=attempt_key,
		)

	assert error.value.status_code == 401
	assert error.value.detail == "账号或密码错误。"


def test_create_user_account_persists_email_digest(session: Session) -> None:
	user = _create_user_account(
		session,
		AuthRegisterCredentials(
			user_id="email_tester",
			email="email@example.com",
			password="qwer1234",
		),
	)

	assert user.email_digest is not None
	assert verify_password("qwer1234", user.password_digest) is True


def test_reset_user_password_with_matching_email(session: Session) -> None:
	_create_user_account(
		session,
		AuthRegisterCredentials(
			user_id="recover_me",
			email="recover@example.com",
			password="qwer1234",
		),
	)

	user = _reset_user_password_with_email(
		session,
		PasswordResetRequest(
			user_id="recover_me",
			email="recover@example.com",
			new_password="asdf5678",
		),
	)

	assert verify_password("asdf5678", user.password_digest) is True


def test_update_user_email_refreshes_visible_email_and_digest(session: Session) -> None:
	current_user = _create_user_account(
		session,
		AuthRegisterCredentials(
			user_id="mail_owner",
			email="old@example.com",
			password="qwer1234",
		),
	)

	updated_user = _update_user_email(
		session,
		current_user,
		UserEmailUpdate(email="new@example.com"),
	)

	assert updated_user.email == "new@example.com"
	assert updated_user.email_digest is not None


def test_persist_hour_snapshot_compacts_rows_within_the_same_hour(session: Session) -> None:
	session.add(
		PortfolioSnapshot(
			user_id="tester",
			total_value_cny=D("1000"),
			created_at=datetime(2026, 3, 1, 3, 12, tzinfo=timezone.utc),
		),
	)
	session.add(
		PortfolioSnapshot(
			user_id="tester",
			total_value_cny=D("1200"),
			created_at=datetime(2026, 3, 1, 3, 41, tzinfo=timezone.utc),
		),
	)
	session.commit()

	_persist_hour_snapshot(
		session,
		"tester",
		datetime(2026, 3, 1, 3, 0, tzinfo=timezone.utc),
		D("1500"),
	)

	snapshots = session.exec(
		select(PortfolioSnapshot).order_by(sql_expr(PortfolioSnapshot.created_at).asc()),
	).all()

	assert len(snapshots) == 1
	assert snapshots[0].total_value_cny == 1500
	assert _coerce_utc_datetime(snapshots[0].created_at) == datetime(
		2026,
		3,
		1,
		3,
		0,
		tzinfo=timezone.utc,
	)


def test_persist_holdings_return_snapshot_compacts_rows_within_the_same_hour(
	session: Session,
) -> None:
	session.add(
		HoldingPerformanceSnapshot(
			user_id="tester",
			scope="TOTAL",
			symbol=None,
			name="非现金资产",
			return_pct=D("1.5"),
			created_at=datetime(2026, 3, 1, 3, 12, tzinfo=timezone.utc),
		),
	)
	session.add(
		HoldingPerformanceSnapshot(
			user_id="tester",
			scope="HOLDING",
			symbol="0700.HK",
			name="腾讯控股",
			return_pct=D("2.2"),
			created_at=datetime(2026, 3, 1, 3, 16, tzinfo=timezone.utc),
		),
	)
	session.commit()

	_persist_holdings_return_snapshot(
		session,
		"tester",
		datetime(2026, 3, 1, 3, 0, tzinfo=timezone.utc),
		D("3.5"),
		(LiveHoldingReturnPoint(symbol="0700.HK", name="腾讯控股", return_pct=D("4.25")),),
	)

	snapshots = session.exec(
		select(HoldingPerformanceSnapshot).order_by(sql_expr(HoldingPerformanceSnapshot.scope).asc()),
	).all()

	assert len(snapshots) == 2
	assert snapshots[0].scope == "HOLDING"
	assert snapshots[0].return_pct == 4.25
	assert snapshots[1].scope == "TOTAL"
	assert snapshots[1].return_pct == 3.5


def test_summarize_holdings_return_state_returns_weighted_aggregate() -> None:
	aggregate_return_pct, holding_points = _summarize_holdings_return_state(
		[
			ValuedHolding(
				id=1,
				symbol="0700.HK",
				name="腾讯控股",
				quantity=D("100"),
				fallback_currency="HKD",
				cost_basis_price=D("500"),
				market="HK",
				price=D("550"),
				price_currency="HKD",
				fx_to_cny=D("0.8"),
				value_cny=D("44000"),
				return_pct=D("10.0"),
			),
			ValuedHolding(
				id=2,
				symbol="9988.HK",
				name="阿里巴巴-W",
				quantity=D("200"),
				fallback_currency="HKD",
				cost_basis_price=D("100"),
				market="HK",
				price=D("90"),
				price_currency="HKD",
				fx_to_cny=D("0.8"),
				value_cny=D("14400"),
				return_pct=D("-10.0"),
			),
		],
	)

	assert aggregate_return_pct == Decimal("4.29")
	assert [point.symbol for point in holding_points] == ["0700.HK", "9988.HK"]


def test_list_accounts_returns_valued_balances(
	session: Session,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	current_user = make_user(session)
	create_account(
		CashAccountCreate(
			name="Checking",
			platform="Bank",
			currency="usd",
			balance=D("100"),
			account_type="bank",
		),
		current_user,
		session,
	)
	monkeypatch.setattr(service_context, "market_data_client", StaticMarketDataClient())

	accounts = asyncio.run(list_accounts(current_user, session))

	assert len(accounts) == 1
	assert accounts[0].fx_to_cny == 7.0
	assert accounts[0].value_cny == 700.0


def test_list_accounts_scopes_results_to_current_user(
	session: Session,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	first_user = make_user(session, "first_user")
	second_user = make_user(session, "second_user")
	create_account(
		CashAccountCreate(
			name="Checking",
			platform="Bank",
			currency="cny",
			balance=D("50"),
			account_type="bank",
		),
		first_user,
		session,
	)
	monkeypatch.setattr(service_context, "market_data_client", StaticMarketDataClient())

	accounts = asyncio.run(list_accounts(second_user, session))

	assert accounts == []


def test_cash_account_schema_rejects_invalid_account_type() -> None:
	with pytest.raises(ValidationError):
		CashAccountCreate(
			name="Wallet",
			platform="Cash",
			currency="CNY",
			balance=D("10"),
			account_type="BROKERAGE",
		)


def test_holding_transaction_buy_rejects_sell_proceeds_fields() -> None:
	with pytest.raises(
		ValidationError,
		match="买入交易不支持卖出回款处理选项。",
	):
		SecurityHoldingTransactionCreate(
			side="BUY",
			symbol="AAPL",
			name="Apple",
			quantity=D("1"),
			fallback_currency="USD",
			market="US",
			traded_on=date(2026, 3, 1),
			sell_proceeds_handling="DISCARD",
		)


def test_holding_transaction_sell_requires_target_account_for_existing_cash_strategy() -> None:
	with pytest.raises(
		ValidationError,
		match="卖出并入现有现金时必须选择目标现金账户。",
	):
		SecurityHoldingTransactionCreate(
			side="SELL",
			symbol="AAPL",
			name="Apple",
			quantity=D("1"),
			fallback_currency="USD",
			market="US",
			traded_on=date(2026, 3, 1),
			sell_proceeds_handling="ADD_TO_EXISTING_CASH",
		)


def test_create_holding_persists_market_broker_and_note(session: Session) -> None:
	current_user = make_user(session)
	holding = create_holding(
		SecurityHoldingCreate(
			symbol="aapl",
			name="Apple",
			quantity=D("3"),
			fallback_currency="usd",
			cost_basis_price=D("92.5"),
			market="us",
			broker="  IBKR  ",
			started_on=date(2026, 2, 14),
			note="  long term  ",
		),
		current_user,
		session,
	)

	assert holding.id is not None
	assert holding.symbol == "AAPL"
	assert holding.fallback_currency == "USD"
	assert holding.cost_basis_price == 92.5
	assert holding.market == "US"
	assert holding.broker == "IBKR"
	assert holding.started_on == date(2026, 2, 14)
	assert holding.note == "long term"

	stored_holding = session.get(SecurityHolding, holding.id)
	assert stored_holding is not None
	assert stored_holding.user_id == current_user.username
	assert stored_holding.started_on == date(2026, 2, 14)
	assert stored_holding.cost_basis_price == 92.5
	assert stored_holding.market == "US"
	assert stored_holding.broker == "IBKR"
	assert stored_holding.note == "long term"


def test_create_holding_bootstraps_buy_transaction_baseline(session: Session) -> None:
	current_user = make_user(session)
	create_holding(
		SecurityHoldingCreate(
			symbol="aapl",
			name="Apple",
			quantity=D("2"),
			fallback_currency="usd",
			cost_basis_price=D("88"),
			market="us",
			started_on=date(2026, 2, 14),
		),
		current_user,
		session,
	)

	transactions = list(
		session.exec(
			select(SecurityHoldingTransaction)
			.where(SecurityHoldingTransaction.user_id == current_user.username),
		),
	)
	assert len(transactions) == 1
	assert transactions[0].side == "BUY"
	assert transactions[0].quantity == 2
	assert transactions[0].price == 88
	assert transactions[0].traded_on == date(2026, 2, 14)


def test_holding_transaction_buy_and_sell_rebuilds_position_from_lots(session: Session) -> None:
	current_user = make_user(session)
	holding = create_holding(
		SecurityHoldingCreate(
			symbol="aapl",
			name="Apple",
			quantity=D("2"),
			fallback_currency="usd",
			cost_basis_price=D("80"),
			market="us",
			started_on=date(2026, 2, 14),
		),
		current_user,
		session,
	)

	applied_buy = create_holding_transaction(
		SecurityHoldingTransactionCreate(
			side="BUY",
			symbol="aapl",
			name="Apple",
			quantity=D("1"),
			price=D("100"),
			fallback_currency="usd",
			market="us",
			traded_on=date(2026, 2, 1),
		),
		current_user,
		session,
	)
	assert applied_buy.holding is not None
	assert applied_buy.holding.quantity == 3
	assert applied_buy.holding.started_on == date(2026, 2, 1)
	assert applied_buy.holding.cost_basis_price == Decimal("86.6667")

	applied_sell = create_holding_transaction(
		SecurityHoldingTransactionCreate(
			side="SELL",
			symbol="aapl",
			name="Apple",
			quantity=D("2"),
			price=D("120"),
			fallback_currency="usd",
			market="us",
			traded_on=date(2026, 2, 20),
		),
		current_user,
		session,
	)
	assert applied_sell.holding is not None
	assert applied_sell.holding.quantity == 1
	assert applied_sell.holding.started_on == date(2026, 2, 14)
	assert applied_sell.holding.cost_basis_price == Decimal("80.0000")

	holding_transactions = list_holding_transactions(holding.id or 0, current_user, session)
	assert len(holding_transactions) == 3
	assert [item.side for item in holding_transactions] == ["SELL", "BUY", "BUY"]


def test_holding_sell_transaction_auto_creates_cash_entry_with_source(
	session: Session,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	current_user = make_user(session)
	monkeypatch.setattr(service_context, "market_data_client", StaticMarketDataClient())
	create_holding(
		SecurityHoldingCreate(
			symbol="aapl",
			name="Apple",
			quantity=D("2"),
			fallback_currency="usd",
			cost_basis_price=D("80"),
			market="us",
			started_on=date(2026, 2, 14),
		),
		current_user,
		session,
	)

	applied_sell = create_holding_transaction(
		SecurityHoldingTransactionCreate(
			side="SELL",
			symbol="aapl",
			name="Apple",
			quantity=D("1"),
			price=None,
			fallback_currency="usd",
			market="us",
			traded_on=date(2026, 3, 1),
		),
		current_user,
		session,
	)

	assert applied_sell.transaction.price == 100.0
	assert applied_sell.transaction.fallback_currency == "USD"
	assert applied_sell.sell_proceeds_handling == "CREATE_NEW_CASH"
	assert applied_sell.cash_account is not None

	cash_entries = list(
		session.exec(
			select(CashAccount)
			.where(CashAccount.user_id == current_user.username)
			.order_by(sql_expr(CashAccount.created_at).asc()),
		),
	)
	assert len(cash_entries) == 1
	assert cash_entries[0].platform == "交易回款"
	assert cash_entries[0].currency == "USD"
	assert cash_entries[0].balance == 100.0
	assert cash_entries[0].started_on == date(2026, 3, 1)
	assert "来源：卖出 Apple(AAPL)" in (cash_entries[0].note or "")
	assert "交易ID #" in (cash_entries[0].note or "")


def test_holding_sell_transaction_keeps_user_supplied_execution_price(
	session: Session,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	current_user = make_user(session)
	monkeypatch.setattr(service_context, "market_data_client", StaticMarketDataClient())
	create_holding(
		SecurityHoldingCreate(
			symbol="aapl",
			name="Apple",
			quantity=D("2"),
			fallback_currency="usd",
			cost_basis_price=D("80"),
			market="us",
			started_on=date(2026, 2, 14),
		),
		current_user,
		session,
	)

	applied_sell = create_holding_transaction(
		SecurityHoldingTransactionCreate(
			side="SELL",
			symbol="aapl",
			name="Apple",
			quantity=D("1"),
			price=D("12"),
			fallback_currency="usd",
			market="us",
			traded_on=date(2026, 3, 1),
		),
		current_user,
		session,
	)

	assert applied_sell.transaction.price == 12.0
	assert applied_sell.transaction.fallback_currency == "USD"

	cash_entries = list(
		session.exec(
			select(CashAccount)
			.where(CashAccount.user_id == current_user.username)
			.order_by(sql_expr(CashAccount.created_at).asc()),
		),
	)
	assert len(cash_entries) == 1
	assert cash_entries[0].balance == 12.0


def test_holding_sell_transaction_can_discard_proceeds(
	session: Session,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	current_user = make_user(session)
	monkeypatch.setattr(service_context, "market_data_client", StaticMarketDataClient())
	create_holding(
		SecurityHoldingCreate(
			symbol="aapl",
			name="Apple",
			quantity=D("2"),
			fallback_currency="usd",
			cost_basis_price=D("80"),
			market="us",
			started_on=date(2026, 2, 14),
		),
		current_user,
		session,
	)

	applied_sell = create_holding_transaction(
		SecurityHoldingTransactionCreate(
			side="SELL",
			symbol="aapl",
			name="Apple",
			quantity=D("1"),
			price=D("50"),
			fallback_currency="usd",
			market="us",
			traded_on=date(2026, 3, 1),
			sell_proceeds_handling="DISCARD",
		),
		current_user,
		session,
	)

	assert applied_sell.sell_proceeds_handling == "DISCARD"
	assert applied_sell.cash_account is None
	assert (
		session.exec(
			select(CashAccount).where(CashAccount.user_id == current_user.username),
		).first()
		is None
	)


def test_holding_sell_transaction_can_merge_proceeds_into_existing_cash_account(
	session: Session,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	current_user = make_user(session)
	monkeypatch.setattr(service_context, "market_data_client", StaticMarketDataClient())
	create_holding(
		SecurityHoldingCreate(
			symbol="aapl",
			name="Apple",
			quantity=D("2"),
			fallback_currency="usd",
			cost_basis_price=D("80"),
			market="us",
			started_on=date(2026, 2, 14),
		),
		current_user,
		session,
	)
	cash_account = create_account(
		CashAccountCreate(
			name="主账户",
			platform="Bank",
			currency="cny",
			balance=D("200"),
			account_type="bank",
			note="长期备用金",
		),
		current_user,
		session,
	)

	applied_sell = create_holding_transaction(
		SecurityHoldingTransactionCreate(
			side="SELL",
			symbol="aapl",
			name="Apple",
			quantity=D("1"),
			price=D("88"),
			fallback_currency="usd",
			market="us",
			traded_on=date(2026, 3, 1),
			sell_proceeds_handling="ADD_TO_EXISTING_CASH",
			sell_proceeds_account_id=cash_account.id,
		),
		current_user,
		session,
	)

	assert applied_sell.sell_proceeds_handling == "ADD_TO_EXISTING_CASH"
	assert applied_sell.cash_account is not None
	assert applied_sell.cash_account.id == cash_account.id
	assert applied_sell.cash_account.currency == "CNY"
	assert applied_sell.cash_account.balance == 816.0
	assert "自动入账 616 CNY" in (applied_sell.cash_account.note or "")
	assert "长期备用金" in (applied_sell.cash_account.note or "")

	updated_account = session.get(CashAccount, cash_account.id)
	assert updated_account is not None
	assert updated_account.balance == 816.0
	assert updated_account.started_on == cash_account.started_on


def test_holding_buy_transaction_can_deduct_from_existing_cash_account(
	session: Session,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	current_user = make_user(session)
	monkeypatch.setattr(service_context, "market_data_client", StaticMarketDataClient())
	monkeypatch.setattr(realtime_analytics_service, "engine", session.get_bind())
	cash_account = create_account(
		CashAccountCreate(
			name="主账户",
			platform="Bank",
			currency="cny",
			balance=D("1000"),
			account_type="bank",
		),
		current_user,
		session,
	)

	applied_buy = create_holding_transaction(
		SecurityHoldingTransactionCreate(
			side="BUY",
			symbol="aapl",
			name="Apple",
			quantity=D("1"),
			price=D("100"),
			fallback_currency="usd",
			market="us",
			traded_on=date(2026, 3, 1),
			buy_funding_handling="DEDUCT_FROM_EXISTING_CASH",
			buy_funding_account_id=cash_account.id,
		),
		current_user,
		session,
	)

	assert applied_buy.cash_account is not None
	assert applied_buy.cash_account.id == cash_account.id
	assert applied_buy.cash_account.balance == 300.0
	stored_settlement = session.exec(select(HoldingTransactionCashSettlement)).one()
	assert stored_settlement.flow_direction == "OUTFLOW"
	assert stored_settlement.handling == "DEDUCT_FROM_EXISTING_CASH"
	ledger_entries = list(
		session.exec(
			select(CashLedgerEntry)
			.where(CashLedgerEntry.holding_transaction_id == applied_buy.transaction.id),
		),
	)
	assert len(ledger_entries) == 1
	assert ledger_entries[0].entry_type == "BUY_FUNDING"
	assert ledger_entries[0].amount == -700.0


def test_holding_transaction_sell_rejects_when_quantity_is_insufficient(
	session: Session,
) -> None:
	current_user = make_user(session)
	create_holding(
		SecurityHoldingCreate(
			symbol="aapl",
			name="Apple",
			quantity=D("1"),
			fallback_currency="usd",
			cost_basis_price=D("80"),
			market="us",
		),
		current_user,
		session,
	)

	with pytest.raises(HTTPException) as error:
		create_holding_transaction(
			SecurityHoldingTransactionCreate(
				side="SELL",
				symbol="aapl",
				name="Apple",
				quantity=D("2"),
				price=D("120"),
				fallback_currency="usd",
				market="us",
				traded_on=date(2026, 3, 1),
			),
			current_user,
			session,
		)

	assert error.value.status_code == 422
	assert "可卖数量不足" in str(error.value.detail)


def test_delete_holding_transaction_reconciles_holding_projection(session: Session) -> None:
	current_user = make_user(session)
	holding = create_holding(
		SecurityHoldingCreate(
			symbol="aapl",
			name="Apple",
			quantity=D("1"),
			fallback_currency="usd",
			cost_basis_price=D("80"),
			market="us",
		),
		current_user,
		session,
	)
	applied = create_holding_transaction(
		SecurityHoldingTransactionCreate(
			side="BUY",
			symbol="aapl",
			name="Apple",
			quantity=D("1"),
			price=D("120"),
			fallback_currency="usd",
			market="us",
			traded_on=date(2026, 3, 1),
		),
		current_user,
		session,
	)

	response = delete_holding_transaction(applied.transaction.id, current_user, session)
	assert response.status_code == 204

	stored_holding = session.get(SecurityHolding, holding.id)
	assert stored_holding is not None
	assert stored_holding.quantity == 1
	assert stored_holding.cost_basis_price == 80

	remaining_transactions = list(
		session.exec(
			select(SecurityHoldingTransaction)
			.where(SecurityHoldingTransaction.user_id == current_user.username)
			.where(SecurityHoldingTransaction.symbol == "AAPL"),
		),
	)
	assert len(remaining_transactions) == 1
	assert remaining_transactions[0].side == "BUY"


def test_delete_sell_transaction_reverses_existing_cash_settlement(
	session: Session,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	current_user = make_user(session)
	monkeypatch.setattr(service_context, "market_data_client", StaticMarketDataClient())
	create_holding(
		SecurityHoldingCreate(
			symbol="aapl",
			name="Apple",
			quantity=D("2"),
			fallback_currency="usd",
			cost_basis_price=D("80"),
			market="us",
			started_on=date(2026, 2, 14),
		),
		current_user,
		session,
	)
	cash_account = create_account(
		CashAccountCreate(
			name="主账户",
			platform="Bank",
			currency="cny",
			balance=D("200"),
			account_type="bank",
		),
		current_user,
		session,
	)
	applied_sell = create_holding_transaction(
		SecurityHoldingTransactionCreate(
			side="SELL",
			symbol="aapl",
			name="Apple",
			quantity=D("1"),
			price=D("88"),
			fallback_currency="usd",
			market="us",
			traded_on=date(2026, 3, 1),
			sell_proceeds_handling="ADD_TO_EXISTING_CASH",
			sell_proceeds_account_id=cash_account.id,
		),
		current_user,
		session,
	)

	assert applied_sell.cash_account is not None
	assert applied_sell.cash_account.balance == 816.0

	response = delete_holding_transaction(applied_sell.transaction.id, current_user, session)

	assert response.status_code == 204
	updated_account = session.get(CashAccount, cash_account.id)
	assert updated_account is not None
	assert updated_account.balance == 200.0
	assert session.exec(select(HoldingTransactionCashSettlement)).all() == []


def test_update_holding_transaction_rebuilds_projection_and_cash_settlement(
	session: Session,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	current_user = make_user(session)
	monkeypatch.setattr(service_context, "market_data_client", StaticMarketDataClient())
	create_holding(
		SecurityHoldingCreate(
			symbol="aapl",
			name="Apple",
			quantity=D("2"),
			fallback_currency="usd",
			cost_basis_price=D("80"),
			market="us",
			started_on=date(2026, 2, 14),
		),
		current_user,
		session,
	)
	cash_account = create_account(
		CashAccountCreate(
			name="主账户",
			platform="Bank",
			currency="cny",
			balance=D("200"),
			account_type="bank",
		),
		current_user,
		session,
	)
	applied_sell = create_holding_transaction(
		SecurityHoldingTransactionCreate(
			side="SELL",
			symbol="aapl",
			name="Apple",
			quantity=D("1"),
			price=D("88"),
			fallback_currency="usd",
			market="us",
			traded_on=date(2026, 3, 1),
			sell_proceeds_handling="ADD_TO_EXISTING_CASH",
			sell_proceeds_account_id=cash_account.id,
		),
		current_user,
		session,
	)

	updated = update_holding_transaction(
		applied_sell.transaction.id,
		SecurityHoldingTransactionUpdate(
			quantity=D("2"),
			traded_on=date(2026, 2, 20),
			note="sold all",
		),
		current_user,
		session,
	)

	assert updated.transaction.quantity == 2
	assert updated.transaction.traded_on == date(2026, 2, 20)
	assert updated.transaction.note == "sold all"
	assert updated.holding is None
	assert updated.cash_account is not None
	assert updated.cash_account.balance == 1432.0
	assert (
		session.exec(
			select(SecurityHolding).where(SecurityHolding.user_id == current_user.username),
		).first()
		is None
	)
	settlement = session.exec(select(HoldingTransactionCashSettlement)).one()
	assert settlement.holding_transaction_id == applied_sell.transaction.id
	assert settlement.cash_account_id == cash_account.id
	assert settlement.settled_amount == 1232.0


def test_create_cash_transfer_records_ledger_and_updates_balances(
	session: Session,
) -> None:
	current_user = make_user(session)
	from_account = create_account(
		CashAccountCreate(
			name="主账户",
			platform="Bank",
			currency="cny",
			balance=D("1000"),
			account_type="bank",
		),
		current_user,
		session,
	)
	to_account = create_account(
		CashAccountCreate(
			name="备用金",
			platform="Cash",
			currency="cny",
			balance=D("200"),
			account_type="cash",
		),
		current_user,
		session,
	)

	applied_transfer = create_cash_transfer(
		CashTransferCreate(
			from_account_id=from_account.id or 0,
			to_account_id=to_account.id or 0,
			source_amount=D("300"),
			transferred_on=date(2026, 3, 2),
			note="周转",
		),
		current_user,
		session,
	)

	assert applied_transfer.from_account.balance == 700.0
	assert applied_transfer.to_account.balance == 500.0
	stored_transfer = session.exec(select(CashTransfer)).one()
	assert stored_transfer.source_amount == 300.0
	ledger_entries = list(
		session.exec(
			select(CashLedgerEntry)
			.where(CashLedgerEntry.cash_transfer_id == stored_transfer.id),
		),
	)
	assert len(ledger_entries) == 2
	assert sorted(entry.entry_type for entry in ledger_entries) == ["TRANSFER_IN", "TRANSFER_OUT"]


def test_create_cash_transfer_converts_supported_source_currency_into_cny_target(
	session: Session,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	current_user = make_user(session)
	monkeypatch.setattr(service_context, "market_data_client", StaticMarketDataClient())
	from_account = create_account(
		CashAccountCreate(
			name="美元账户",
			platform="Bank",
			currency="usd",
			balance=D("100"),
			account_type="bank",
		),
		current_user,
		session,
	)
	to_account = create_account(
		CashAccountCreate(
			name="人民币账户",
			platform="Cash",
			currency="cny",
			balance=D("20"),
			account_type="cash",
		),
		current_user,
		session,
	)

	applied_transfer = create_cash_transfer(
		CashTransferCreate(
			from_account_id=from_account.id or 0,
			to_account_id=to_account.id or 0,
			source_amount=D("10"),
			transferred_on=date(2026, 3, 2),
		),
		current_user,
		session,
	)

	assert applied_transfer.transfer.source_currency == "USD"
	assert applied_transfer.transfer.target_currency == "CNY"
	assert applied_transfer.transfer.target_amount == 70
	assert applied_transfer.from_account.balance == 90
	assert applied_transfer.to_account.balance == 90


def test_create_cash_transfer_rejects_non_cny_target_or_manual_target_override(
	session: Session,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	current_user = make_user(session)
	monkeypatch.setattr(service_context, "market_data_client", StaticMarketDataClient())
	from_account = create_account(
		CashAccountCreate(
			name="港币账户",
			platform="Bank",
			currency="hkd",
			balance=D("100"),
			account_type="bank",
		),
		current_user,
		session,
	)
	usd_target = create_account(
		CashAccountCreate(
			name="美元账户",
			platform="Cash",
			currency="usd",
			balance=D("10"),
			account_type="cash",
		),
		current_user,
		session,
	)
	cny_target = create_account(
		CashAccountCreate(
			name="人民币账户",
			platform="Cash",
			currency="cny",
			balance=D("0"),
			account_type="cash",
		),
		current_user,
		session,
	)

	with pytest.raises(HTTPException) as non_cny_error:
		create_cash_transfer(
			CashTransferCreate(
				from_account_id=from_account.id or 0,
				to_account_id=usd_target.id or 0,
				source_amount=D("5"),
				transferred_on=date(2026, 3, 2),
			),
			current_user,
			session,
		)

	assert non_cny_error.value.status_code == 422
	assert non_cny_error.value.detail == "转入账户必须是 CNY 现金账户。"

	with pytest.raises(HTTPException) as mismatch_error:
		create_cash_transfer(
			CashTransferCreate(
				from_account_id=from_account.id or 0,
				to_account_id=cny_target.id or 0,
				source_amount=D("5"),
				target_amount=D("1"),
				transferred_on=date(2026, 3, 2),
			),
			current_user,
			session,
		)

	assert mismatch_error.value.status_code == 422
	assert "目标币种金额必须按当前汇率自动换算为 CNY" in mismatch_error.value.detail


def test_update_cash_transfer_replays_ledger_and_account_balances(session: Session) -> None:
	current_user = make_user(session)
	from_account = create_account(
		CashAccountCreate(
			name="主账户",
			platform="Bank",
			currency="CNY",
			balance=D("500"),
			account_type="BANK",
		),
		current_user,
		session,
	)
	to_account = create_account(
		CashAccountCreate(
			name="备用金",
			platform="Cash",
			currency="CNY",
			balance=D("200"),
			account_type="CASH",
		),
		current_user,
		session,
	)
	created_transfer = create_cash_transfer(
		CashTransferCreate(
			from_account_id=from_account.id or 0,
			to_account_id=to_account.id or 0,
			source_amount=D("100"),
			transferred_on=date(2026, 3, 2),
			note="首次划转",
		),
		current_user,
		session,
	)

	updated_transfer = update_cash_transfer(
		created_transfer.transfer.id,
		CashTransferUpdate(
			source_amount=D("40"),
			transferred_on=date(2026, 3, 3),
			note="修正金额",
		),
		current_user,
		session,
	)

	assert updated_transfer.transfer.source_amount == 40
	assert updated_transfer.transfer.target_amount == 40
	assert updated_transfer.transfer.transferred_on == date(2026, 3, 3)
	assert updated_transfer.from_account.balance == 460.0
	assert updated_transfer.to_account.balance == 240.0

	stored_ledger_entries = list(
		session.exec(
			select(CashLedgerEntry)
			.where(CashLedgerEntry.cash_transfer_id == created_transfer.transfer.id)
			.order_by(sql_expr(CashLedgerEntry.amount).asc()),
		),
	)
	assert len(stored_ledger_entries) == 2
	assert stored_ledger_entries[0].amount == -40
	assert stored_ledger_entries[1].amount == 40


def test_manual_cash_ledger_adjustment_create_update_delete_reconciles_balance(
	session: Session,
) -> None:
	current_user = make_user(session)
	account = create_account(
		CashAccountCreate(
			name="主账户",
			platform="Bank",
			currency="CNY",
			balance=D("100"),
			account_type="BANK",
		),
		current_user,
		session,
	)

	created_adjustment = create_cash_ledger_adjustment(
		CashLedgerAdjustmentCreate(
			cash_account_id=account.id or 0,
			amount=D("25"),
			happened_on=date(2026, 3, 4),
			note="补记入账",
		),
		current_user,
		session,
	)
	assert created_adjustment.account.balance == 125.0
	assert created_adjustment.entry.entry_type == "MANUAL_ADJUSTMENT"

	updated_adjustment = update_cash_ledger_adjustment(
		created_adjustment.entry.id,
		CashLedgerAdjustmentUpdate(
			amount=D("-10"),
			note="修正差额",
		),
		current_user,
		session,
	)
	assert updated_adjustment.entry.amount == -10
	assert updated_adjustment.account.balance == 90.0

	response = delete_cash_ledger_adjustment(
		created_adjustment.entry.id,
		current_user,
		session,
	)
	assert response.status_code == 204
	refreshed_account = session.get(CashAccount, account.id)
	assert refreshed_account is not None
	assert refreshed_account.balance == 100.0
	assert session.get(CashLedgerEntry, created_adjustment.entry.id) is None


def test_build_dashboard_replays_total_series_from_cash_ledger_and_holding_transactions(
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
			balance=D("1000"),
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
			quantity=D("1"),
			price=D("100"),
			fallback_currency="USD",
			market="US",
			traded_on=date(2026, 3, 1),
			buy_funding_handling="DEDUCT_FROM_EXISTING_CASH",
			buy_funding_account_id=cash_account.id,
		),
		current_user,
		session,
	)

	dashboard = asyncio.run(dashboard_query_service._build_dashboard(session, current_user))

	assert dashboard.cash_value_cny == 300.0
	assert dashboard.holdings_value_cny == 700.0
	assert dashboard.total_value_cny == 1000.0
	assert any(point.value == 1000.0 for point in dashboard.hour_series)
	assert len(dashboard.recent_holding_transactions) == 1
	assert dashboard.recent_holding_transactions[0].symbol == "AAPL"
	assert dashboard.recent_holding_transactions[0].side == "BUY"


def test_build_dashboard_persists_previous_live_hour_snapshot_when_hour_rolls(
	session: Session,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	current_user = make_user(session)
	monkeypatch.setattr(service_context, "market_data_client", StaticMarketDataClient())

	session.add(
		CashAccount(
			user_id=current_user.username,
			name="主账户",
			platform="Bank",
			currency="CNY",
			balance=D("100.0"),
			account_type="BANK",
		),
	)
	session.commit()

	first_now = datetime(2026, 3, 17, 4, 35, tzinfo=timezone.utc)
	second_now = first_now + timedelta(hours=1, minutes=5)
	monkeypatch.setattr(dashboard_query_service, "utc_now", lambda: first_now)

	first_dashboard = asyncio.run(dashboard_query_service._build_dashboard(session, current_user))
	first_snapshots = list(
		session.exec(
			select(PortfolioSnapshot)
			.where(PortfolioSnapshot.user_id == current_user.username)
			.order_by(sql_expr(PortfolioSnapshot.created_at).asc()),
		),
	)

	assert first_dashboard.total_value_cny == 100.0
	assert len(first_snapshots) == 0

	monkeypatch.setattr(dashboard_query_service, "utc_now", lambda: second_now)
	second_dashboard = asyncio.run(dashboard_query_service._build_dashboard(session, current_user))
	second_snapshots = list(
		session.exec(
			select(PortfolioSnapshot)
			.where(PortfolioSnapshot.user_id == current_user.username)
			.order_by(sql_expr(PortfolioSnapshot.created_at).asc()),
		),
	)

	assert second_dashboard.total_value_cny == 100.0
	assert len(second_snapshots) == 1
	assert _coerce_utc_datetime(second_snapshots[0].created_at) == first_now.replace(
		minute=0,
		second=0,
		microsecond=0,
	)
	assert second_snapshots[0].total_value_cny == 100.0


def test_realtime_sampler_populates_second_and_minute_dashboard_series(
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
			balance=D("1000"),
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
			quantity=D("1"),
			price=D("100"),
			fallback_currency="USD",
			market="US",
			traded_on=date(2026, 3, 26),
			buy_funding_handling="DEDUCT_FROM_EXISTING_CASH",
			buy_funding_account_id=cash_account.id,
		),
		current_user,
		session,
	)

	first_sample_at = datetime(2026, 3, 26, 3, 0, 1, tzinfo=timezone.utc)
	second_sample_at = first_sample_at + timedelta(minutes=1, seconds=1)
	asyncio.run(
		realtime_analytics_service.sample_realtime_analytics_once(
			first_sample_at,
			session=session,
		),
	)
	asyncio.run(
		realtime_analytics_service.sample_realtime_analytics_once(
			second_sample_at,
			session=session,
		),
	)

	portfolio_rows = list(
		session.exec(
			select(RealtimePortfolioSnapshot)
			.where(RealtimePortfolioSnapshot.user_id == current_user.username)
			.order_by(sql_expr(RealtimePortfolioSnapshot.created_at).asc()),
		),
	)
	return_rows = list(
		session.exec(
			select(RealtimeHoldingPerformanceSnapshot)
			.where(RealtimeHoldingPerformanceSnapshot.user_id == current_user.username)
			.order_by(sql_expr(RealtimeHoldingPerformanceSnapshot.created_at).asc()),
		),
	)
	assert len(portfolio_rows) == 2
	assert any(row.scope == "TOTAL" for row in return_rows)
	assert any(row.scope == "HOLDING" and row.symbol == "AAPL" for row in return_rows)

	monkeypatch.setattr(dashboard_query_service, "utc_now", lambda: second_sample_at)
	dashboard = asyncio.run(dashboard_query_service._build_dashboard(session, current_user))

	assert [point.label for point in dashboard.second_series] == [
		"03-26 11:00:01",
		"03-26 11:01:02",
	]
	assert [point.value for point in dashboard.second_series] == [1000.0, 1000.0]
	assert [point.label for point in dashboard.minute_series] == [
		"03-26 11:00",
		"03-26 11:01",
	]
	assert [point.value for point in dashboard.minute_series] == [1000.0, 1000.0]
	assert [point.label for point in dashboard.holdings_return_second_series] == [
		"03-26 11:00:01",
		"03-26 11:01:02",
	]
	assert [point.label for point in dashboard.holdings_return_minute_series] == [
		"03-26 11:00",
		"03-26 11:01",
	]
	assert dashboard.holding_return_series[0].second_series is not None
	assert dashboard.holding_return_series[0].minute_series is not None
	assert [point.label for point in (dashboard.holding_return_series[0].second_series or [])] == [
		"03-26 11:00:01",
		"03-26 11:01:02",
	]


def test_realtime_sampler_samples_all_users_with_one_quote_fetch_per_unique_symbol(
	session: Session,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	first_user = make_user(session, "first_sampler_user")
	second_user = make_user(session, "second_sampler_user")
	quote_calls: list[tuple[str, str | None]] = []
	fx_calls: list[tuple[str, str]] = []

	class CountingMarketDataClient(StaticMarketDataClient):
		async def fetch_quote(
			self,
			symbol: str,
			market: str | None = None,
			*,
			prefer_stale: bool = False,
			schedule_stale_refresh: bool = True,
		) -> tuple[Quote, list[str]]:
			del prefer_stale, schedule_stale_refresh
			quote_calls.append((symbol, market))
			return await super().fetch_quote(
				symbol,
				market,
				prefer_stale=False,
				schedule_stale_refresh=True,
			)

		async def fetch_fx_rate(
			self,
			from_currency: str,
			to_currency: str,
			*,
			prefer_stale: bool = False,
			schedule_stale_refresh: bool = True,
		) -> tuple[Decimal, list[str]]:
			del prefer_stale, schedule_stale_refresh
			fx_calls.append((from_currency, to_currency))
			return await super().fetch_fx_rate(
				from_currency,
				to_currency,
				prefer_stale=False,
				schedule_stale_refresh=True,
			)

	monkeypatch.setattr(service_context, "market_data_client", CountingMarketDataClient())

	first_account = create_account(
		CashAccountCreate(
			name="主账户 A",
			platform="Bank",
			currency="CNY",
			balance=D("1000"),
			account_type="BANK",
		),
		first_user,
		session,
	)
	second_account = create_account(
		CashAccountCreate(
			name="主账户 B",
			platform="Bank",
			currency="CNY",
			balance=D("1000"),
			account_type="BANK",
		),
		second_user,
		session,
	)
	for current_user, account_id in (
		(first_user, first_account.id),
		(second_user, second_account.id),
	):
		create_holding_transaction(
			SecurityHoldingTransactionCreate(
				side="BUY",
				symbol="AAPL",
				name="Apple",
				quantity=D("1"),
				price=D("100"),
				fallback_currency="USD",
				market="US",
				traded_on=date(2026, 3, 26),
				buy_funding_handling="DEDUCT_FROM_EXISTING_CASH",
				buy_funding_account_id=account_id,
			),
			current_user,
			session,
		)

	quote_calls.clear()
	fx_calls.clear()

	asyncio.run(
		realtime_analytics_service.sample_realtime_analytics_once(
			datetime(2026, 3, 26, 3, 0, 1, tzinfo=timezone.utc),
			session=session,
		),
	)

	assert quote_calls == [("AAPL", "US")]
	assert fx_calls == [("USD", "CNY")]
	portfolio_user_ids = {
		row.user_id
		for row in session.exec(
			select(RealtimePortfolioSnapshot).order_by(sql_expr(RealtimePortfolioSnapshot.user_id).asc()),
		)
	}
	assert portfolio_user_ids == {first_user.username, second_user.username}


def test_realtime_sampler_keeps_no_asset_users_in_batch_without_writing_snapshots(
	session: Session,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	current_user = make_user(session, "empty_realtime_user")
	monkeypatch.setattr(service_context, "market_data_client", StaticMarketDataClient())

	stats = asyncio.run(
		realtime_analytics_service._sample_realtime_analytics_once_with_session(
			session,
			sampled_at=datetime(2026, 3, 26, 3, 0, 1, tzinfo=timezone.utc),
		),
	)

	assert stats.user_count == 1
	assert stats.unique_symbol_count == 0
	assert not list(
		session.exec(
			select(RealtimePortfolioSnapshot).where(
				RealtimePortfolioSnapshot.user_id == current_user.username,
			),
		),
	)
	assert not list(
		session.exec(
			select(RealtimeHoldingPerformanceSnapshot).where(
				RealtimeHoldingPerformanceSnapshot.user_id == current_user.username,
			),
		),
	)


def test_realtime_sampler_records_market_data_failures_without_dropping_cash_snapshot(
	session: Session,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	current_user = make_user(session, "quote_failure_user")
	create_account(
		CashAccountCreate(
			name="主账户",
			platform="Bank",
			currency="CNY",
			balance=D("1000"),
			account_type="BANK",
		),
		current_user,
		session,
	)
	create_holding(
		SecurityHoldingCreate(
			symbol="AAPL",
			name="Apple",
			quantity=D("1"),
			fallback_currency="USD",
			cost_basis_price=D("100"),
			market="US",
			started_on=date(2026, 3, 26),
		),
		current_user,
		session,
	)

	class FailingQuoteMarketDataClient(StaticMarketDataClient):
		async def fetch_quote(
			self,
			symbol: str,
			market: str | None = None,
			*,
			prefer_stale: bool = False,
			schedule_stale_refresh: bool = True,
		) -> tuple[Quote, list[str]]:
			del symbol, market, prefer_stale, schedule_stale_refresh
			raise QuoteLookupError("provider unavailable")

	monkeypatch.setattr(service_context, "market_data_client", FailingQuoteMarketDataClient())

	stats = asyncio.run(
		realtime_analytics_service._sample_realtime_analytics_once_with_session(
			session,
			sampled_at=datetime(2026, 3, 26, 3, 0, 1, tzinfo=timezone.utc),
		),
	)

	portfolio_rows = list(
		session.exec(
			select(RealtimePortfolioSnapshot).where(
				RealtimePortfolioSnapshot.user_id == current_user.username,
			),
		),
	)
	return_rows = list(
		session.exec(
			select(RealtimeHoldingPerformanceSnapshot).where(
				RealtimeHoldingPerformanceSnapshot.user_id == current_user.username,
			),
		),
	)

	assert stats.user_count == 1
	assert stats.unique_symbol_count == 1
	assert stats.quote_failure_count == 1
	assert stats.fx_failure_count == 0
	assert len(portfolio_rows) == 1
	assert portfolio_rows[0].total_value_cny == Decimal("1000.00000000")
	assert return_rows == []


def test_realtime_sampler_purges_expired_snapshots_in_bulk(session: Session) -> None:
	current_user = make_user(session, "purge_realtime_user")
	now = datetime(2026, 3, 26, 3, 0, 0, tzinfo=timezone.utc)
	expired_at = now - realtime_analytics_service.REALTIME_SERIES_RETENTION - timedelta(seconds=1)
	fresh_at = now - timedelta(minutes=5)
	session.add_all(
		[
			RealtimePortfolioSnapshot(
				user_id=current_user.username,
				total_value_cny=Decimal("1"),
				created_at=expired_at,
			),
			RealtimePortfolioSnapshot(
				user_id=current_user.username,
				total_value_cny=Decimal("2"),
				created_at=fresh_at,
			),
			RealtimeHoldingPerformanceSnapshot(
				user_id=current_user.username,
				scope="TOTAL",
				symbol=None,
				name="非现金资产",
				return_pct=Decimal("1"),
				created_at=expired_at,
			),
			RealtimeHoldingPerformanceSnapshot(
				user_id=current_user.username,
				scope="TOTAL",
				symbol=None,
				name="非现金资产",
				return_pct=Decimal("2"),
				created_at=fresh_at,
			),
		],
	)
	session.commit()

	deleted_counts = realtime_analytics_service._purge_expired_realtime_snapshots(
		session,
		now=now,
	)
	session.commit()

	assert deleted_counts == (1, 1)
	assert [
		row.total_value_cny
		for row in session.exec(select(RealtimePortfolioSnapshot))
	] == [Decimal("2.00000000")]
	assert [
		row.return_pct
		for row in session.exec(select(RealtimeHoldingPerformanceSnapshot))
	] == [Decimal("2.00000000")]


def test_create_holding_rejects_future_started_on_based_on_server_date(
	session: Session,
) -> None:
	current_user = make_user(session)
	future_started_on = (datetime.now(timezone.utc) + timedelta(days=2)).date()

	with pytest.raises(HTTPException) as error:
		create_holding(
			SecurityHoldingCreate(
				symbol="aapl",
				name="Apple",
				quantity=D("2"),
				fallback_currency="usd",
				market="us",
				started_on=future_started_on,
			),
			current_user,
			session,
		)

	assert error.value.status_code == 422
	assert "持仓日不能晚于今日" in error.value.detail


def test_create_holding_transaction_rejects_future_traded_on_based_on_server_date(
	session: Session,
) -> None:
	current_user = make_user(session)
	future_traded_on = (datetime.now(timezone.utc) + timedelta(days=2)).date()

	with pytest.raises(HTTPException) as error:
		create_holding_transaction(
			SecurityHoldingTransactionCreate(
				side="BUY",
				symbol="aapl",
				name="Apple",
				quantity=D("1"),
				price=D("100"),
				fallback_currency="usd",
				market="us",
				traded_on=future_traded_on,
			),
			current_user,
			session,
		)

	assert error.value.status_code == 422
	assert "交易日不能晚于今日" in error.value.detail


def test_update_holding_rebases_earliest_transaction_for_backdated_holding_correction(
	session: Session,
) -> None:
	current_user = make_user(session)
	holding = create_holding(
		SecurityHoldingCreate(
			symbol="aapl",
			name="Apple",
			quantity=D("2"),
			fallback_currency="usd",
			market="us",
			broker="IBKR",
			started_on=date(2026, 2, 14),
			note="original note",
		),
		current_user,
		session,
	)
	create_holding_transaction(
		SecurityHoldingTransactionCreate(
			side="BUY",
			symbol="AAPL",
			name="Apple",
			quantity=D("1"),
			price=D("100"),
			fallback_currency="USD",
			market="US",
			broker="IBKR",
			traded_on=date(2026, 3, 1),
			note="original note",
		),
		current_user,
		session,
	)

	updated_holding = update_holding(
		holding.id or 0,
		SecurityHoldingUpdate(
			quantity=D("4"),
			cost_basis_price=D("118"),
			started_on=date(2026, 2, 10),
		),
		current_user,
		session,
	)

	assert updated_holding.symbol == "AAPL"
	assert updated_holding.name == "Apple"
	assert updated_holding.quantity == 5
	assert updated_holding.fallback_currency == "USD"
	assert updated_holding.cost_basis_price == Decimal("114.4000")
	assert updated_holding.market == "US"
	assert updated_holding.broker == "IBKR"
	assert updated_holding.note == "original note"
	assert updated_holding.started_on == date(2026, 2, 10)

	transactions = list(
		session.exec(
			select(SecurityHoldingTransaction)
			.where(SecurityHoldingTransaction.user_id == current_user.username),
		),
	)
	adjustments = [item for item in transactions if item.side == "ADJUST"]
	assert len(transactions) == 2
	assert len(adjustments) == 1
	assert adjustments[0].symbol == "AAPL"
	assert adjustments[0].market == "US"
	assert adjustments[0].quantity == 4
	assert adjustments[0].price == 118
	assert adjustments[0].traded_on == date(2026, 2, 10)


def test_update_holding_accepts_holding_correction_fields() -> None:
	payload = SecurityHoldingUpdate(
		quantity=D("4"),
		cost_basis_price=D("118"),
		started_on=date(2026, 2, 10),
	)

	assert payload.quantity == 4
	assert payload.cost_basis_price == 118
	assert payload.started_on == date(2026, 2, 10)


def test_delete_holding_removes_record(session: Session) -> None:
	current_user = make_user(session)
	holding = create_holding(
		SecurityHoldingCreate(
			symbol="aapl",
			name="Apple",
			quantity=D("2"),
			fallback_currency="usd",
			market="us",
		),
		current_user,
		session,
	)

	response = delete_holding(holding.id or 0, current_user, session)

	assert response.status_code == 204
	assert session.exec(select(SecurityHolding)).all() == []
	assert session.exec(select(SecurityHoldingTransaction)).all() == []


def test_delete_holding_reverses_linked_sell_cash_settlements(
	session: Session,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	current_user = make_user(session)
	monkeypatch.setattr(service_context, "market_data_client", StaticMarketDataClient())
	holding = create_holding(
		SecurityHoldingCreate(
			symbol="aapl",
			name="Apple",
			quantity=D("2"),
			fallback_currency="usd",
			cost_basis_price=D("80"),
			market="us",
			started_on=date(2026, 2, 14),
		),
		current_user,
		session,
	)
	cash_account = create_account(
		CashAccountCreate(
			name="主账户",
			platform="Bank",
			currency="cny",
			balance=D("200"),
			account_type="bank",
		),
		current_user,
		session,
	)
	create_holding_transaction(
		SecurityHoldingTransactionCreate(
			side="SELL",
			symbol="aapl",
			name="Apple",
			quantity=D("1"),
			price=D("88"),
			fallback_currency="usd",
			market="us",
			traded_on=date(2026, 3, 1),
			sell_proceeds_handling="ADD_TO_EXISTING_CASH",
			sell_proceeds_account_id=cash_account.id,
		),
		current_user,
		session,
	)

	response = delete_holding(holding.id or 0, current_user, session)

	assert response.status_code == 204
	updated_account = session.get(CashAccount, cash_account.id)
	assert updated_account is not None
	assert updated_account.balance == 200.0
	assert session.exec(select(SecurityHolding)).all() == []
	assert session.exec(select(SecurityHoldingTransaction)).all() == []
	assert session.exec(select(HoldingTransactionCashSettlement)).all() == []


def test_delete_holding_returns_404_when_missing(session: Session) -> None:
	current_user = make_user(session)
	with pytest.raises(HTTPException) as error:
		delete_holding(9999, current_user, session)

	assert error.value.status_code == 404
	assert error.value.detail == "Holding not found."


def test_list_holdings_returns_enriched_quote_fields(
	session: Session,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	current_user = make_user(session)
	create_holding(
		SecurityHoldingCreate(
			symbol="aapl",
			name="Apple",
			quantity=D("2"),
			fallback_currency="usd",
			cost_basis_price=D("80"),
			market="us",
		),
		current_user,
		session,
	)
	monkeypatch.setattr(service_context, "market_data_client", StaticMarketDataClient())

	holdings = asyncio.run(list_holdings(current_user, session))

	assert len(holdings) == 1
	assert holdings[0].price == 100.0
	assert holdings[0].price_currency == "USD"
	assert holdings[0].value_cny == 1400.0
	assert holdings[0].cost_basis_price == 80
	assert holdings[0].return_pct == 25.0


def test_create_holding_returns_enriched_quote_fields_immediately(
	session: Session,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	current_user = make_user(session)
	monkeypatch.setattr(service_context, "market_data_client", StaticMarketDataClient())

	holding = create_holding(
		SecurityHoldingCreate(
			symbol="aapl",
			name="Apple",
			quantity=D("2"),
			fallback_currency="usd",
			cost_basis_price=D("80"),
			market="us",
		),
		current_user,
		session,
	)

	assert holding.price == 100.0
	assert holding.price_currency == "USD"
	assert holding.value_cny == 1400.0
	assert holding.return_pct == 25.0


def test_holding_schema_rejects_invalid_market() -> None:
	with pytest.raises(ValidationError):
		SecurityHoldingCreate(
			symbol="AAPL",
			name="Apple",
			quantity=D("1"),
			fallback_currency="USD",
			market="JP",
		)


def test_holding_schema_rejects_fractional_stock_quantity() -> None:
	with pytest.raises(ValidationError):
		SecurityHoldingCreate(
			symbol="AAPL",
			name="Apple",
			quantity=D("1.5"),
			fallback_currency="USD",
			market="US",
		)


def test_holding_schema_allows_fractional_fund_units() -> None:
	holding = SecurityHoldingCreate(
		symbol="159915.SZ",
		name="创业板 ETF",
		quantity=D("1.5"),
		fallback_currency="CNY",
		market="FUND",
	)

	assert holding.quantity == 1.5


def test_holding_schema_allows_fractional_crypto_units() -> None:
	holding = SecurityHoldingCreate(
		symbol="BTC-USD",
		name="Bitcoin",
		quantity=D("0.25"),
		fallback_currency="USD",
		market="CRYPTO",
	)

	assert holding.quantity == 0.25


def test_create_new_asset_categories_persists_records(session: Session) -> None:
	current_user = make_user(session)

	fixed_asset = create_fixed_asset(
		FixedAssetCreate(
			name="Primary Home",
			category="real_estate",
			current_value_cny=D("2000000"),
			purchase_value_cny=D("1800000"),
			started_on=date(2024, 1, 1),
			note="  family use  ",
		),
		current_user,
		session,
	)
	liability = create_liability(
		LiabilityEntryCreate(
			name="Mortgage",
			category="mortgage",
			currency="cny",
			balance=D("500000"),
			started_on=date(2024, 1, 2),
			note="  monthly repayment  ",
		),
		current_user,
		session,
	)
	other_asset = create_other_asset(
		OtherAssetCreate(
			name="Friend Loan",
			category="receivable",
			current_value_cny=D("20000"),
			original_value_cny=D("18000"),
			started_on=date(2025, 5, 6),
			note="  due next quarter  ",
		),
		current_user,
		session,
	)

	assert fixed_asset.category == "REAL_ESTATE"
	assert fixed_asset.return_pct == Decimal("11.11")
	assert fixed_asset.started_on == date(2024, 1, 1)
	assert liability.category == "MORTGAGE"
	assert liability.started_on == date(2024, 1, 2)
	assert other_asset.category == "RECEIVABLE"
	assert other_asset.started_on == date(2025, 5, 6)
	assert other_asset.return_pct == Decimal("11.11")

	assert session.exec(select(FixedAsset)).one().user_id == current_user.username
	assert session.exec(select(LiabilityEntry)).one().user_id == current_user.username
	assert session.exec(select(OtherAsset)).one().user_id == current_user.username


def test_asset_currency_schemas_restrict_supported_currencies() -> None:
	with pytest.raises(ValidationError, match="currency must be one of: CNY, USD, HKD."):
		CashAccountCreate(
			name="JPY Account",
			platform="Bank",
			currency="jpy",
			balance=D("100"),
			account_type="bank",
		)

	with pytest.raises(ValidationError, match="fallback_currency must be one of: CNY, USD, HKD."):
		SecurityHoldingTransactionCreate(
			side="BUY",
			symbol="AAPL",
			name="Apple",
			quantity=D("1"),
			price=D("100"),
			fallback_currency="eur",
			market="US",
			traded_on=date(2026, 3, 1),
		)

	with pytest.raises(ValidationError, match="currency must be one of: CNY, USD, HKD."):
		LiabilityEntryCreate(
			name="Mortgage",
			category="mortgage",
			currency="jpy",
			balance=D("500000"),
		)


def test_build_dashboard_subtracts_liabilities_from_total(
	session: Session,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	current_user = make_user(session)
	create_account(
		CashAccountCreate(
			name="Checking",
			platform="Bank",
			currency="cny",
			balance=D("1000"),
			account_type="bank",
		),
		current_user,
		session,
	)
	create_fixed_asset(
		FixedAssetCreate(
			name="Primary Home",
			category="real_estate",
			current_value_cny=D("500000"),
		),
		current_user,
		session,
	)
	create_other_asset(
		OtherAssetCreate(
			name="Receivable",
			category="receivable",
			current_value_cny=D("20000"),
		),
		current_user,
		session,
	)
	create_liability(
		LiabilityEntryCreate(
			name="Mortgage",
			category="mortgage",
			currency="cny",
			balance=D("120000"),
		),
		current_user,
		session,
	)
	monkeypatch.setattr(service_context, "market_data_client", StaticMarketDataClient())

	dashboard = asyncio.run(dashboard_query_service._build_dashboard(session, current_user))

	assert dashboard.cash_value_cny == 1000.0
	assert dashboard.fixed_assets_value_cny == 500_000.0
	assert dashboard.other_assets_value_cny == 20_000.0
	assert dashboard.liabilities_value_cny == 120_000.0
	assert dashboard.total_value_cny == 401_000.0
	assert [slice.label for slice in dashboard.allocation] == ["现金", "固定资产", "其他"]


def test_build_dashboard_converts_usd_liabilities_to_cny(
	session: Session,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	current_user = make_user(session)
	create_account(
		CashAccountCreate(
			name="Checking",
			platform="Bank",
			currency="cny",
			balance=D("1000"),
			account_type="bank",
		),
		current_user,
		session,
	)
	create_liability(
		LiabilityEntryCreate(
			name="USD Credit",
			category="credit_card",
			currency="usd",
			balance=D("100"),
		),
		current_user,
		session,
	)
	monkeypatch.setattr(service_context, "market_data_client", StaticMarketDataClient())

	dashboard = asyncio.run(dashboard_query_service._build_dashboard(session, current_user))

	assert dashboard.usd_cny_rate == 7.0
	assert dashboard.hkd_cny_rate == 7.0
	assert dashboard.cash_value_cny == 1_000.0
	assert dashboard.liabilities_value_cny == 700.0
	assert dashboard.total_value_cny == 300.0


def test_build_dashboard_hides_fallback_cache_warning_for_non_admin(
	session: Session,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	current_user = make_user(session, "normal_user")
	create_holding(
		SecurityHoldingCreate(
			symbol="1810.HK",
			name="Xiaomi",
			quantity=D("2"),
			fallback_currency="hkd",
			market="hk",
			cost_basis_price=D("12.5"),
		),
		current_user,
		session,
	)
	monkeypatch.setattr(service_context, "market_data_client", WarningMarketDataClient())
	monkeypatch.setattr(
		dashboard_query_service,
		"_has_holding_history_sync_pending",
		lambda *_args, **_kwargs: False,
	)

	dashboard = asyncio.run(dashboard_query_service._build_dashboard(session, current_user))

	assert not any("已回退到最近缓存值" in warning for warning in dashboard.warnings)
	assert WarningMarketDataClient.DELAY_WARNING in dashboard.warnings


def test_build_dashboard_keeps_fallback_cache_warning_for_admin(
	session: Session,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	current_user = make_user(session, "admin")
	create_holding(
		SecurityHoldingCreate(
			symbol="1810.HK",
			name="Xiaomi",
			quantity=D("2"),
			fallback_currency="hkd",
			market="hk",
			cost_basis_price=D("12.5"),
		),
		current_user,
		session,
	)
	monkeypatch.setattr(service_context, "market_data_client", WarningMarketDataClient())
	monkeypatch.setattr(
		dashboard_query_service,
		"_has_holding_history_sync_pending",
		lambda *_args, **_kwargs: False,
	)

	dashboard = asyncio.run(dashboard_query_service._build_dashboard(session, current_user))

	assert WarningMarketDataClient.FALLBACK_WARNING in dashboard.warnings
	assert WarningMarketDataClient.DELAY_WARNING in dashboard.warnings


def test_holding_update_keeps_existing_history_sync_request(session: Session) -> None:
	current_user = make_user(session, "holding_sync_queue_user")
	holding = create_holding(
		SecurityHoldingCreate(
			symbol="AAPL",
			name="Apple",
			quantity=D("2"),
			fallback_currency="usd",
			cost_basis_price=D("80"),
			market="us",
			started_on=date(2026, 3, 1),
		),
		current_user,
		session,
	)

	requests_after_create = list(
		session.exec(
			select(HoldingHistorySyncRequest).where(
				HoldingHistorySyncRequest.user_id == current_user.username,
			),
		),
	)
	assert len(requests_after_create) == 1
	assert requests_after_create[0].status == "PENDING"

	update_holding(
		holding.id or 0,
		SecurityHoldingUpdate(
			broker="Futu",
			note="core position",
		),
		current_user,
		session,
	)

	requests_after_update = list(
		session.exec(
			select(HoldingHistorySyncRequest).where(
				HoldingHistorySyncRequest.user_id == current_user.username,
			),
		),
	)
	assert len(requests_after_update) == 1
	assert requests_after_update[0].status == "PENDING"
	transactions = list(
		session.exec(
			select(SecurityHoldingTransaction).where(
				SecurityHoldingTransaction.user_id == current_user.username,
			),
		),
	)
	assert len(transactions) == 1
	assert transactions[0].traded_on == date(2026, 3, 1)


def test_process_pending_holding_history_sync_rebuilds_hourly_rows(
	session: Session,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	current_user = make_user(session, "holding_sync_rebuild_user")
	fixed_now = datetime(2026, 3, 5, 10, 0, tzinfo=timezone.utc)

	class HistoryAwareMarketDataClient(StaticMarketDataClient):
		async def fetch_hourly_price_series(
			self,
			symbol: str,
			*,
			market: str | None = None,
			start_at: datetime,
			end_at: datetime,
		) -> tuple[list[tuple[datetime, Decimal]], str | None, list[str]]:
			return [
				(start_at.replace(minute=0, second=0, microsecond=0), D("80")),
				((end_at - timedelta(hours=1)).replace(minute=0, second=0, microsecond=0), D("100")),
			], "USD", []

	monkeypatch.setattr(service_context, "market_data_client", HistoryAwareMarketDataClient())
	monkeypatch.setattr(history_service, "utc_now", lambda: fixed_now)

	create_holding(
		SecurityHoldingCreate(
			symbol="AAPL",
			name="Apple",
			quantity=D("1"),
			fallback_currency="usd",
			cost_basis_price=D("80"),
			market="us",
			started_on=date(2026, 3, 4),
		),
		current_user,
		session,
	)

	asyncio.run(history_service._process_pending_holding_history_sync_requests(session, limit=1))

	request = session.exec(
		select(HoldingHistorySyncRequest).where(
			HoldingHistorySyncRequest.user_id == current_user.username,
		),
	).one()
	assert request.status == "DONE"
	assert request.completed_at is not None

	start_bucket = datetime(2026, 3, 3, 16, 0, tzinfo=timezone.utc)
	end_bucket = datetime(2026, 3, 5, 10, 0, tzinfo=timezone.utc)

	holding_rows = list(
		session.exec(
			select(HoldingPerformanceSnapshot)
			.where(HoldingPerformanceSnapshot.user_id == current_user.username)
			.where(HoldingPerformanceSnapshot.scope == "HOLDING")
			.where(HoldingPerformanceSnapshot.symbol == "AAPL")
			.order_by(sql_expr(HoldingPerformanceSnapshot.created_at).asc()),
		),
	)
	assert holding_rows
	assert _coerce_utc_datetime(holding_rows[0].created_at) == start_bucket
	assert _coerce_utc_datetime(holding_rows[-1].created_at) == end_bucket

	total_rows = list(
		session.exec(
			select(HoldingPerformanceSnapshot)
			.where(HoldingPerformanceSnapshot.user_id == current_user.username)
			.where(HoldingPerformanceSnapshot.scope == "TOTAL")
			.order_by(sql_expr(HoldingPerformanceSnapshot.created_at).asc()),
		),
	)
	assert total_rows
	assert len(total_rows) == len(holding_rows)


def test_process_pending_holding_history_sync_uses_transaction_state_per_period(
	session: Session,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	current_user = make_user(session, "holding_history_source_user")
	fixed_now = datetime(2026, 3, 5, 10, 0, tzinfo=timezone.utc)
	first_bucket = datetime(2026, 3, 3, 16, 0, tzinfo=timezone.utc)
	second_trade_bucket = datetime(2026, 3, 4, 16, 0, tzinfo=timezone.utc)

	class HistoryAwareMarketDataClient(StaticMarketDataClient):
		async def fetch_hourly_price_series(
			self,
			symbol: str,
			*,
			market: str | None = None,
			start_at: datetime,
			end_at: datetime,
		) -> tuple[list[tuple[datetime, Decimal]], str | None, list[str]]:
			return [
				(first_bucket, D("80")),
				(second_trade_bucket, D("100")),
			], "USD", []

	monkeypatch.setattr(service_context, "market_data_client", HistoryAwareMarketDataClient())
	monkeypatch.setattr(history_service, "utc_now", lambda: fixed_now)

	create_holding(
		SecurityHoldingCreate(
			symbol="AAPL",
			name="Apple",
			quantity=D("1"),
			fallback_currency="usd",
			cost_basis_price=D("80"),
			market="us",
			started_on=date(2026, 3, 4),
		),
		current_user,
		session,
	)
	create_holding_transaction(
		SecurityHoldingTransactionCreate(
			side="BUY",
			symbol="AAPL",
			name="Apple",
			quantity=D("1"),
			price=D("100"),
			fallback_currency="USD",
			market="US",
			traded_on=date(2026, 3, 5),
		),
		current_user,
		session,
	)

	asyncio.run(history_service._process_pending_holding_history_sync_requests(session, limit=1))

	holding_rows = list(
		session.exec(
			select(HoldingPerformanceSnapshot)
			.where(HoldingPerformanceSnapshot.user_id == current_user.username)
			.where(HoldingPerformanceSnapshot.scope == "HOLDING")
			.where(HoldingPerformanceSnapshot.symbol == "AAPL")
			.order_by(sql_expr(HoldingPerformanceSnapshot.created_at).asc()),
		),
	)
	assert holding_rows

	row_by_hour = {
		_coerce_utc_datetime(row.created_at): row.return_pct for row in holding_rows
	}
	assert row_by_hour[first_bucket] == Decimal("0")
	assert row_by_hour[second_trade_bucket] == Decimal("11.11000000")


def test_process_pending_holding_history_sync_preserves_prior_hours_for_backfilled_buy(
	session: Session,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	current_user = make_user(session, "backfilled_buy_user")
	fixed_now = datetime(2026, 3, 25, 10, 0, tzinfo=timezone.utc)
	before_second_buy_bucket = common_service._date_start_utc(date(2026, 3, 21))
	second_buy_bucket = common_service._date_start_utc(date(2026, 3, 22))

	class ConstantHoldingValueMarketDataClient(StaticMarketDataClient):
		async def fetch_hourly_price_series(
			self,
			symbol: str,
			*,
			market: str | None = None,
			start_at: datetime,
			end_at: datetime,
		) -> tuple[list[tuple[datetime, Decimal]], str | None, list[str]]:
			hours: list[tuple[datetime, Decimal]] = []
			cursor = start_at.replace(minute=0, second=0, microsecond=0)
			while cursor < end_at:
				hours.append((cursor, Decimal("20")))
				cursor += timedelta(hours=1)
			return hours, "USD", []

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
					name="Alibaba",
					price=Decimal("20"),
					currency="USD",
					market_time=fixed_now,
				),
				[],
			)

	monkeypatch.setattr(service_context, "market_data_client", ConstantHoldingValueMarketDataClient())
	monkeypatch.setattr(history_service, "utc_now", lambda: fixed_now)

	cash_account = create_account(
		CashAccountCreate(
			name="主账户",
			platform="Bank",
			currency="CNY",
			balance=D("210000"),
			account_type="BANK",
			started_on=date(2026, 3, 1),
		),
		current_user,
		session,
	)
	create_holding_transaction(
		SecurityHoldingTransactionCreate(
			side="BUY",
			symbol="BABA",
			name="Alibaba",
			quantity=D("1000"),
			price=D("10"),
			fallback_currency="USD",
			market="US",
			traded_on=date(2026, 3, 1),
			buy_funding_handling="DEDUCT_FROM_EXISTING_CASH",
			buy_funding_account_id=cash_account.id,
		),
		current_user,
		session,
	)
	create_holding_transaction(
		SecurityHoldingTransactionCreate(
			side="BUY",
			symbol="BABA",
			name="Alibaba",
			quantity=D("400"),
			price=D("10"),
			fallback_currency="USD",
			market="US",
			traded_on=date(2026, 3, 22),
			buy_funding_handling="DEDUCT_FROM_EXISTING_CASH",
			buy_funding_account_id=cash_account.id,
		),
		current_user,
		session,
	)

	asyncio.run(history_service._process_pending_holding_history_sync_requests(session, limit=1))

	portfolio_rows = list(
		session.exec(
			select(PortfolioSnapshot)
			.where(PortfolioSnapshot.user_id == current_user.username)
			.order_by(sql_expr(PortfolioSnapshot.created_at).asc()),
		),
	)
	assert portfolio_rows

	row_by_hour = {
		_coerce_utc_datetime(row.created_at): row.total_value_cny for row in portfolio_rows
	}
	assert row_by_hour[before_second_buy_bucket] == Decimal("280000.00000000")
	assert row_by_hour[second_buy_bucket] == Decimal("308000.00000000")


def test_process_pending_holding_history_sync_applies_holding_adjustment_on_effective_date(
	session: Session,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	current_user = make_user(session, "backdated_adjust_user")
	fixed_now = datetime(2026, 3, 25, 10, 0, tzinfo=timezone.utc)
	before_adjust_bucket = common_service._date_start_utc(date(2026, 3, 21))
	adjust_bucket = common_service._date_start_utc(date(2026, 3, 22))

	class ConstantHoldingValueMarketDataClient(StaticMarketDataClient):
		async def fetch_hourly_price_series(
			self,
			symbol: str,
			*,
			market: str | None = None,
			start_at: datetime,
			end_at: datetime,
		) -> tuple[list[tuple[datetime, Decimal]], str | None, list[str]]:
			hours: list[tuple[datetime, Decimal]] = []
			cursor = start_at.replace(minute=0, second=0, microsecond=0)
			while cursor < end_at:
				hours.append((cursor, Decimal("20")))
				cursor += timedelta(hours=1)
			return hours, "USD", []

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
					name="Alibaba",
					price=Decimal("20"),
					currency="USD",
					market_time=fixed_now,
				),
				[],
			)

	monkeypatch.setattr(service_context, "market_data_client", ConstantHoldingValueMarketDataClient())
	monkeypatch.setattr(history_service, "utc_now", lambda: fixed_now)

	holding = create_holding(
		SecurityHoldingCreate(
			symbol="BABA",
			name="Alibaba",
			quantity=D("1000"),
			fallback_currency="USD",
			cost_basis_price=D("10"),
			market="US",
			started_on=date(2026, 3, 1),
		),
		current_user,
		session,
	)

	update_holding(
		holding.id or 0,
		SecurityHoldingUpdate(
			quantity=D("1400"),
			cost_basis_price=D("10"),
			started_on=date(2026, 3, 22),
		),
		current_user,
		session,
	)

	asyncio.run(history_service._process_pending_holding_history_sync_requests(session, limit=1))

	portfolio_rows = list(
		session.exec(
			select(PortfolioSnapshot)
			.where(PortfolioSnapshot.user_id == current_user.username)
			.order_by(sql_expr(PortfolioSnapshot.created_at).asc()),
		),
	)
	assert portfolio_rows

	row_by_hour = {
		_coerce_utc_datetime(row.created_at): row.total_value_cny for row in portfolio_rows
	}
	assert row_by_hour[before_adjust_bucket] == Decimal("140000.00000000")
	assert row_by_hour[adjust_bucket] == Decimal("196000.00000000")

	adjustment_transactions = list(
		session.exec(
			select(SecurityHoldingTransaction)
			.where(SecurityHoldingTransaction.user_id == current_user.username)
			.where(SecurityHoldingTransaction.side == "ADJUST"),
		),
	)
	assert len(adjustment_transactions) == 1
	assert adjustment_transactions[0].traded_on == date(2026, 3, 22)


def test_rebuild_user_holding_history_snapshots_backfills_holdings_without_transactions(
	session: Session,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	current_user = make_user(session, "holding_backfill_user")
	fixed_now = datetime(2026, 3, 25, 10, 0, tzinfo=timezone.utc)

	class ConstantHoldingValueMarketDataClient(StaticMarketDataClient):
		async def fetch_hourly_price_series(
			self,
			symbol: str,
			*,
			market: str | None = None,
			start_at: datetime,
			end_at: datetime,
		) -> tuple[list[tuple[datetime, Decimal]], str | None, list[str]]:
			hours: list[tuple[datetime, Decimal]] = []
			cursor = start_at.replace(minute=0, second=0, microsecond=0)
			while cursor < end_at:
				hours.append((cursor, Decimal("20")))
				cursor += timedelta(hours=1)
			return hours, "USD", []

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
					name="Projected Holding",
					price=Decimal("20"),
					currency="USD",
					market_time=fixed_now,
				),
				[],
			)

	monkeypatch.setattr(service_context, "market_data_client", ConstantHoldingValueMarketDataClient())
	monkeypatch.setattr(history_service, "utc_now", lambda: fixed_now)

	session.add(
		SecurityHolding(
			user_id=current_user.username,
			symbol="AAPL",
			name="Apple",
			quantity=D("2"),
			fallback_currency="USD",
			cost_basis_price=D("10"),
			market="US",
			started_on=None,
			created_at=datetime(2026, 3, 1, 2, 0, tzinfo=timezone.utc),
		),
	)
	session.commit()

	asyncio.run(history_service._rebuild_user_holding_history_snapshots(session, current_user.username))

	backfilled_transactions = list(
		session.exec(
			select(SecurityHoldingTransaction)
			.where(SecurityHoldingTransaction.user_id == current_user.username)
			.order_by(sql_expr(SecurityHoldingTransaction.id).asc()),
		),
	)
	assert len(backfilled_transactions) == 1
	assert backfilled_transactions[0].symbol == "AAPL"
	assert backfilled_transactions[0].side == "BUY"
	assert backfilled_transactions[0].quantity == 2
	assert backfilled_transactions[0].traded_on == date(2026, 3, 1)

	holding_rows = list(
		session.exec(
			select(HoldingPerformanceSnapshot)
			.where(HoldingPerformanceSnapshot.user_id == current_user.username)
			.where(HoldingPerformanceSnapshot.scope == "HOLDING")
			.where(HoldingPerformanceSnapshot.symbol == "AAPL"),
		),
	)
	portfolio_rows = list(
		session.exec(
			select(PortfolioSnapshot)
			.where(PortfolioSnapshot.user_id == current_user.username),
		),
	)
	assert holding_rows
	assert portfolio_rows


def test_get_dashboard_refresh_clears_runtime_cache_and_forces_rebuild(
	session: Session,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	current_user = make_user(session)
	refresh_calls = {"cache_clear": 0, "global_sample": 0}
	captured_args: dict[str, bool] = {}

	class RefreshAwareClient(StaticMarketDataClient):
		def clear_runtime_caches(self, *, clear_search: bool = False) -> None:
			refresh_calls["cache_clear"] += 1

	async def fake_sample_realtime_analytics_once(
		now: datetime | None = None,
		*,
		session: Session | None = None,
	) -> None:
		del now
		assert session is not None
		assert session is session_ref
		refresh_calls["global_sample"] += 1

	async def fake_get_cached_dashboard(
		db_session: Session,
		user: UserAccount,
		force_refresh: bool = False,
	) -> DashboardResponse:
		captured_args["force_refresh"] = force_refresh
		assert user.username == current_user.username
		assert db_session is session
		return DashboardResponse(
			server_today=date(2026, 3, 1),
			total_value_cny=D("0"),
			cash_value_cny=D("0"),
			holdings_value_cny=D("0"),
			fixed_assets_value_cny=D("0"),
			liabilities_value_cny=D("0"),
			other_assets_value_cny=D("0"),
			usd_cny_rate=None,
			hkd_cny_rate=None,
			cash_accounts=[],
			holdings=[],
			fixed_assets=[],
			liabilities=[],
			other_assets=[],
			allocation=[],
			hour_series=[],
			day_series=[],
			month_series=[],
			year_series=[],
			holdings_return_hour_series=[],
			holdings_return_day_series=[],
			holdings_return_month_series=[],
			holdings_return_year_series=[],
			holding_return_series=[],
			warnings=[],
		)

	session_ref = session
	monkeypatch.setattr(service_context, "market_data_client", RefreshAwareClient())
	monkeypatch.setattr(
		realtime_analytics_service,
		"sample_realtime_analytics_once",
		fake_sample_realtime_analytics_once,
	)
	monkeypatch.setattr(dashboard_query_service, "_get_cached_dashboard", fake_get_cached_dashboard)

	asyncio.run(dashboard_query_service.get_dashboard(current_user, session, True))

	assert refresh_calls["cache_clear"] == 1
	assert refresh_calls["global_sample"] == 1
	assert captured_args["force_refresh"] is True


def test_get_dashboard_refresh_only_clears_runtime_cache_once_within_global_window(
	session: Session,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	current_user = make_user(session)
	refresh_calls = {"cache_clear": 0, "dashboard_rebuild": 0, "global_sample": 0}

	class RefreshAwareClient(StaticMarketDataClient):
		def clear_runtime_caches(self, *, clear_search: bool = False) -> None:
			refresh_calls["cache_clear"] += 1

	async def fake_sample_realtime_analytics_once(
		now: datetime | None = None,
		*,
		session: Session | None = None,
	) -> None:
		del now
		assert session is not None
		assert session is session_ref
		refresh_calls["global_sample"] += 1

	async def fake_get_cached_dashboard(
		db_session: Session,
		user: UserAccount,
		force_refresh: bool = False,
	) -> DashboardResponse:
		assert db_session is session
		assert user.username == current_user.username
		assert force_refresh is True
		refresh_calls["dashboard_rebuild"] += 1
		return DashboardResponse(
			server_today=date(2026, 3, 1),
			total_value_cny=D("0"),
			cash_value_cny=D("0"),
			holdings_value_cny=D("0"),
			fixed_assets_value_cny=D("0"),
			liabilities_value_cny=D("0"),
			other_assets_value_cny=D("0"),
			usd_cny_rate=None,
			hkd_cny_rate=None,
			cash_accounts=[],
			holdings=[],
			fixed_assets=[],
			liabilities=[],
			other_assets=[],
			allocation=[],
			hour_series=[],
			day_series=[],
			month_series=[],
			year_series=[],
			holdings_return_hour_series=[],
			holdings_return_day_series=[],
			holdings_return_month_series=[],
			holdings_return_year_series=[],
			holding_return_series=[],
			warnings=[],
		)

	session_ref = session
	monkeypatch.setattr(service_context, "market_data_client", RefreshAwareClient())
	monkeypatch.setattr(
		realtime_analytics_service,
		"sample_realtime_analytics_once",
		fake_sample_realtime_analytics_once,
	)
	monkeypatch.setattr(dashboard_query_service, "_get_cached_dashboard", fake_get_cached_dashboard)
	runtime_state.set_last_global_force_refresh_at(None)

	asyncio.run(dashboard_query_service.get_dashboard(current_user, session, True))
	asyncio.run(dashboard_query_service.get_dashboard(current_user, session, True))

	assert refresh_calls["cache_clear"] == 1
	assert refresh_calls["global_sample"] == 1
	assert refresh_calls["dashboard_rebuild"] == 2


def test_refresh_user_dashboards_clears_market_data_once_per_cycle(
	session: Session,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	current_user = make_user(session)
	refresh_calls = {"cache_clear": 0, "dashboard_rebuild": 0}

	class RefreshAwareClient(StaticMarketDataClient):
		def clear_runtime_caches(self, *, clear_search: bool = False) -> None:
			refresh_calls["cache_clear"] += 1

	async def fake_get_cached_dashboard(
		db_session: Session,
		user: UserAccount,
		force_refresh: bool = False,
	) -> DashboardResponse:
		assert db_session is session
		assert user.username == current_user.username
		assert force_refresh is True
		refresh_calls["dashboard_rebuild"] += 1
		return DashboardResponse(
			server_today=date(2026, 3, 1),
			total_value_cny=D("0"),
			cash_value_cny=D("0"),
			holdings_value_cny=D("0"),
			fixed_assets_value_cny=D("0"),
			liabilities_value_cny=D("0"),
			other_assets_value_cny=D("0"),
			usd_cny_rate=None,
			hkd_cny_rate=None,
			cash_accounts=[],
			holdings=[],
			fixed_assets=[],
			liabilities=[],
			other_assets=[],
			allocation=[],
			hour_series=[],
			day_series=[],
			month_series=[],
			year_series=[],
			holdings_return_hour_series=[],
			holdings_return_day_series=[],
			holdings_return_month_series=[],
			holdings_return_year_series=[],
			holding_return_series=[],
			warnings=[],
		)

	monkeypatch.setattr(service_context, "market_data_client", RefreshAwareClient())
	monkeypatch.setattr(dashboard_query_service, "_get_cached_dashboard", fake_get_cached_dashboard)

	asyncio.run(dashboard_query_service._refresh_user_dashboards(session, [current_user], clear_market_data=True))

	assert refresh_calls["cache_clear"] == 1
	assert refresh_calls["dashboard_rebuild"] == 1
