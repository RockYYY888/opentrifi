import asyncio
from collections.abc import Iterator
from datetime import date, timedelta
import threading

import pytest
from sqlalchemy import text
from sqlmodel import Session, select

import app.database as database
from app import runtime_state
import app.main as main
import app.worker as worker
from app.models import HOLDING_HISTORY_SYNC_STATUSES, HoldingHistorySyncRequest, OutboxJob, UserAccount, utc_now
from app.schemas import SecurityHoldingTransactionCreate
from app.security import hash_password
from app.services import dashboard_query_service, history_service, job_service, service_context
from app.services.holding_transaction_service import create_holding_transaction


class StaticDashboardMarketDataClient:
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

	async def fetch_quote(
		self,
		symbol: str,
		market: str | None = None,
		*,
		prefer_stale: bool = False,
		schedule_stale_refresh: bool = True,
	):
		del symbol, market, prefer_stale, schedule_stale_refresh
		raise AssertionError("Quote lookup should not run for an empty dashboard test.")

	async def fetch_hourly_price_series(self, *args, **kwargs):
		return [], "CNY", []

	def clear_runtime_caches(self, *, clear_search: bool = False) -> None:
		return None


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


def _stall_first_thread_commit(
	monkeypatch: pytest.MonkeyPatch,
	*,
	thread_name: str,
) -> tuple[threading.Event, threading.Event]:
	original_commit = Session.commit
	first_commit_started = threading.Event()
	release_first_commit = threading.Event()

	def delayed_commit(self: Session) -> None:
		if threading.current_thread().name == thread_name and not first_commit_started.is_set():
			first_commit_started.set()
			if not release_first_commit.wait(timeout=5):
				raise AssertionError("Timed out waiting to release the first blocked commit.")
		original_commit(self)

	monkeypatch.setattr(Session, "commit", delayed_commit)
	return first_commit_started, release_first_commit


def _reset_snapshot_runtime_state() -> None:
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

	with Session(engine) as db_session:
		yield db_session


@pytest.fixture(autouse=True)
def reset_runtime_state() -> Iterator[None]:
	main.dashboard_cache.clear()
	main.login_attempt_states.clear()
	_reset_snapshot_runtime_state()
	yield
	main.dashboard_cache.clear()
	main.login_attempt_states.clear()
	_reset_snapshot_runtime_state()


def make_user(session: Session, username: str = "tester") -> UserAccount:
	user = UserAccount(
		username=username,
		password_digest=hash_password("qwer1234"),
	)
	session.add(user)
	session.commit()
	session.refresh(user)
	return user


def test_api_lifespan_only_initializes_db(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	call_order: list[str] = []

	def fake_validate_runtime() -> None:
		call_order.append("validate_runtime")

	def fake_validate_runtime_redis_connection() -> None:
		call_order.append("validate_runtime_redis_connection")

	def fake_init_db() -> None:
		call_order.append("init_db")

	monkeypatch.setattr(main, "init_db", fake_init_db)
	monkeypatch.setattr(main, "validate_runtime_redis_connection", fake_validate_runtime_redis_connection)
	monkeypatch.setattr(
		main,
		"settings",
		type("FakeSettings", (), {"validate_runtime": staticmethod(fake_validate_runtime)})(),
	)

	async def exercise_lifespan() -> None:
		async with main.lifespan(main.app):
			call_order.append("inside_lifespan")

	asyncio.run(exercise_lifespan())

	assert call_order == [
		"validate_runtime",
		"validate_runtime_redis_connection",
		"init_db",
		"inside_lifespan",
	]


def test_engine_recovers_from_closed_pooled_connection(
	postgres_database_url: str,
) -> None:
	engine = database._build_engine(postgres_database_url)

	with Session(engine) as session:
		assert session.exec(text("select 1")).one() == (1,)

	pooled_connection = engine.raw_connection()
	driver_connection = pooled_connection.driver_connection
	pooled_connection.close()
	driver_connection.close()

	with Session(engine) as session:
		assert session.exec(text("select 1")).one() == (1,)

	engine.dispose()


def test_worker_lifecycle_starts_background_job_worker_after_runtime_checks(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	call_order: list[str] = []

	def fake_validate_runtime() -> None:
		call_order.append("validate_runtime")

	def fake_validate_runtime_redis_connection() -> None:
		call_order.append("validate_runtime_redis_connection")

	def fake_start_background_job_worker() -> None:
		call_order.append("start_background_job_worker")

	async def fake_stop_background_job_worker() -> None:
		call_order.append("stop_background_job_worker")

	class FakeLoop:
		def add_signal_handler(self, _sig, callback) -> None:
			call_order.append("add_signal_handler")
			if call_order.count("add_signal_handler") == 2:
				callback()

	class FakeSettings:
		def validate_runtime(self) -> None:
			fake_validate_runtime()

	monkeypatch.setattr(worker, "settings", FakeSettings())
	monkeypatch.setattr(worker, "validate_runtime_redis_connection", fake_validate_runtime_redis_connection)
	monkeypatch.setattr(worker, "start_background_job_worker", fake_start_background_job_worker)
	monkeypatch.setattr(worker, "stop_background_job_worker", fake_stop_background_job_worker)
	monkeypatch.setattr(asyncio, "get_running_loop", lambda: FakeLoop())

	asyncio.run(worker.run_worker())

	assert call_order == [
		"validate_runtime",
		"validate_runtime_redis_connection",
		"add_signal_handler",
		"add_signal_handler",
		"start_background_job_worker",
		"stop_background_job_worker",
	]


def test_snapshot_rebuild_enqueue_deduplicates_pending_jobs(session: Session) -> None:
	job_service.enqueue_user_portfolio_snapshot_rebuild(session, " tester ")
	job_service.enqueue_user_portfolio_snapshot_rebuild(session, "tester")
	job_service.enqueue_user_portfolio_snapshot_rebuild(session, "tester")
	session.commit()

	jobs = list(session.exec(select(OutboxJob)).all())
	assert len(jobs) == 1
	assert jobs[0].job_type == "SNAPSHOT_REBUILD"
	assert jobs[0].user_id == "tester"
	assert jobs[0].status == "PENDING"


def test_snapshot_rebuild_enqueue_deduplicates_across_concurrent_sessions(
	session: Session,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	current_user = make_user(session)
	monkeypatch.setattr(runtime_state, "redis_client", FakeRedisLockClient())
	first_commit_started, release_first_commit = _stall_first_thread_commit(
		monkeypatch,
		thread_name="enqueue-1",
	)
	claimed_job_ids: list[int] = []
	errors: list[Exception] = []

	def enqueue_job() -> None:
		try:
			with Session(database.engine) as thread_session:
				job = job_service.enqueue_user_portfolio_snapshot_rebuild(
					thread_session,
					current_user.username,
				)
				thread_session.commit()
				claimed_job_ids.append(job.id or 0)
		except Exception as exc:  # pragma: no cover - test synchronization path
			errors.append(exc)

	first_thread = threading.Thread(target=enqueue_job, name="enqueue-1")
	second_thread = threading.Thread(target=enqueue_job, name="enqueue-2")
	first_thread.start()
	assert first_commit_started.wait(timeout=5)
	second_thread.start()
	release_first_commit.set()
	first_thread.join(timeout=5)
	second_thread.join(timeout=5)

	assert errors == []
	jobs = list(session.exec(select(OutboxJob).order_by(OutboxJob.id.asc())))
	assert len(jobs) == 1
	assert claimed_job_ids == [jobs[0].id, jobs[0].id]


def test_claim_next_pending_job_is_atomic_across_sessions(
	session: Session,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	first_user = make_user(session, "alpha")
	second_user = make_user(session, "beta")
	first_job = job_service.enqueue_user_portfolio_snapshot_rebuild(session, first_user.username)
	second_job = job_service.enqueue_user_portfolio_snapshot_rebuild(session, second_user.username)
	session.commit()
	first_commit_started, release_first_commit = _stall_first_thread_commit(
		monkeypatch,
		thread_name="claim-1",
	)
	claimed_job_ids: list[int] = []
	errors: list[Exception] = []

	def claim_job() -> None:
		try:
			with Session(database.engine) as thread_session:
				job = job_service._claim_next_pending_job(thread_session)
				claimed_job_ids.append(0 if job is None else (job.id or 0))
		except Exception as exc:  # pragma: no cover - test synchronization path
			errors.append(exc)

	first_thread = threading.Thread(target=claim_job, name="claim-1")
	second_thread = threading.Thread(target=claim_job, name="claim-2")
	first_thread.start()
	assert first_commit_started.wait(timeout=5)
	second_thread.start()
	release_first_commit.set()
	first_thread.join(timeout=5)
	second_thread.join(timeout=5)

	assert errors == []
	assert sorted(claimed_job_ids) == sorted([first_job.id or 0, second_job.id or 0])


def test_claim_next_pending_holding_history_request_is_atomic_across_sessions(
	session: Session,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	now = utc_now()
	session.add_all(
		[
			HoldingHistorySyncRequest(
				user_id="alpha",
				status=HOLDING_HISTORY_SYNC_STATUSES[0],
				requested_at=now,
			),
			HoldingHistorySyncRequest(
				user_id="beta",
				status=HOLDING_HISTORY_SYNC_STATUSES[0],
				requested_at=now + timedelta(seconds=1),
			),
		],
	)
	session.commit()
	requests = list(
		session.exec(
			select(HoldingHistorySyncRequest).order_by(HoldingHistorySyncRequest.id.asc()),
		),
	)
	first_commit_started, release_first_commit = _stall_first_thread_commit(
		monkeypatch,
		thread_name="history-claim-1",
	)
	claimed_request_ids: list[int] = []
	errors: list[Exception] = []

	def claim_request() -> None:
		try:
			with Session(database.engine) as thread_session:
				request = history_service._claim_next_pending_holding_history_sync_request(
					thread_session,
				)
				claimed_request_ids.append(0 if request is None else (request.id or 0))
		except Exception as exc:  # pragma: no cover - test synchronization path
			errors.append(exc)

	first_thread = threading.Thread(target=claim_request, name="history-claim-1")
	second_thread = threading.Thread(target=claim_request, name="history-claim-2")
	first_thread.start()
	assert first_commit_started.wait(timeout=5)
	second_thread.start()
	release_first_commit.set()
	first_thread.join(timeout=5)
	second_thread.join(timeout=5)

	assert errors == []
	assert sorted(claimed_request_ids) == sorted([request.id or 0 for request in requests])


def test_create_holding_transaction_only_schedules_snapshot_rebuild(
	session: Session,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	current_user = make_user(session)

	async def fail_sync_rebuild(*_args, **_kwargs) -> None:
		raise AssertionError("Holding writes should not rebuild snapshots synchronously.")

	monkeypatch.setattr(
		history_service,
		"_rebuild_user_portfolio_snapshots",
		fail_sync_rebuild,
	)

	applied_transaction = create_holding_transaction(
		SecurityHoldingTransactionCreate(
			side="BUY",
			symbol="AAPL",
			name="Apple",
			market="US",
			quantity=2,
			price=180,
			fallback_currency="USD",
			traded_on=date(2026, 3, 9),
		),
		current_user,
		session,
		None,
	)

	assert applied_transaction.transaction.symbol == "AAPL"
	jobs = list(session.exec(select(OutboxJob)).all())
	assert len(jobs) == 1
	assert jobs[0].job_type == "SNAPSHOT_REBUILD"
	assert jobs[0].user_id == current_user.username


def test_get_cached_dashboard_does_not_execute_snapshot_jobs_inline(
	session: Session,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	current_user = make_user(session)
	job_service.enqueue_user_portfolio_snapshot_rebuild(session, current_user.username)
	session.commit()

	async def fail_sync_rebuild(*_args, **_kwargs) -> None:
		raise AssertionError("Dashboard reads should not rebuild snapshots inline.")

	monkeypatch.setattr(history_service, "_rebuild_user_portfolio_snapshots", fail_sync_rebuild)
	monkeypatch.setattr(service_context, "market_data_client", StaticDashboardMarketDataClient())

	dashboard = asyncio.run(dashboard_query_service._get_cached_dashboard(session, current_user))

	assert dashboard.total_value_cny == 0
	job = session.exec(select(OutboxJob)).one()
	assert job.status == "PENDING"


def test_background_job_worker_processes_snapshot_rebuild_job(
	session: Session,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	current_user = make_user(session)
	job = job_service.enqueue_user_portfolio_snapshot_rebuild(session, current_user.username)
	session.commit()
	rebuilt_users: list[str] = []

	async def fake_rebuild(_session: Session, user_id: str) -> None:
		rebuilt_users.append(user_id)

	monkeypatch.setattr(history_service, "_rebuild_user_portfolio_snapshots", fake_rebuild)

	assert asyncio.run(job_service.process_next_background_job()) is True

	session.refresh(job)
	assert rebuilt_users == [current_user.username]
	assert job.status == "DONE"
