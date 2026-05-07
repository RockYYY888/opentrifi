from collections.abc import Generator, Iterator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlmodel import Session, create_engine

from app.settings import get_settings

DEFAULT_LOCAL_DATABASE_URL = (
	"postgresql+psycopg://asset_tracker:asset_tracker@127.0.0.1:5433/asset_tracker"
)
DATABASE_URL = get_settings().database_url_value() or DEFAULT_LOCAL_DATABASE_URL
ALEMBIC_CONFIG_PATH = Path(__file__).resolve().parent.parent / "alembic.ini"
MIGRATION_ADVISORY_LOCK_ID = 88290045133101


def _build_engine(database_url: str):
	engine = create_engine(
		database_url,
		pool_pre_ping=True,
	)
	return engine


engine = _build_engine(DATABASE_URL)


def _build_alembic_config() -> Config:
	config = Config(str(ALEMBIC_CONFIG_PATH))
	config.set_main_option("script_location", str(ALEMBIC_CONFIG_PATH.parent / "alembic"))
	config.set_main_option("sqlalchemy.url", DATABASE_URL)
	return config


@contextmanager
def _migration_lock() -> Iterator[None]:
	with engine.connect() as connection:
		connection.execute(
			text("SELECT pg_advisory_lock(:lock_id)"),
			{"lock_id": MIGRATION_ADVISORY_LOCK_ID},
		)
		try:
			yield
		finally:
			connection.execute(
				text("SELECT pg_advisory_unlock(:lock_id)"),
				{"lock_id": MIGRATION_ADVISORY_LOCK_ID},
			)


def init_db() -> None:
	"""Apply schema migrations on startup."""
	with _migration_lock():
		alembic_config = _build_alembic_config()
		command.upgrade(alembic_config, "head")


def get_session() -> Generator[Session, None, None]:
	"""Yield a database session for request handlers."""
	with Session(engine) as session:
		yield session
