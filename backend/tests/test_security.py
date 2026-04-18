from collections.abc import Iterator
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.security import (
	hash_password,
	hash_email,
	normalize_email,
	normalize_user_id,
	verify_api_token,
	verify_email,
	verify_password,
)
from app.settings import get_settings


@pytest.fixture(autouse=True)
def reset_settings_cache(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
	for env_name in (
		"ASSET_TRACKER_ALLOWED_HOSTS",
		"ASSET_TRACKER_ALLOWED_ORIGINS",
		"ASSET_TRACKER_APP_ENV",
		"ASSET_TRACKER_DATABASE_URL",
		"ASSET_TRACKER_PUBLIC_ORIGIN",
		"ASSET_TRACKER_REDIS_URL",
		"ASSET_TRACKER_SESSION_SECRET",
	):
		monkeypatch.delenv(env_name, raising=False)

	get_settings.cache_clear()
	yield
	get_settings.cache_clear()


def _build_client() -> TestClient:
	app = FastAPI()

	@app.get("/protected")
	def protected(_: Annotated[None, Depends(verify_api_token)]) -> dict[str, str]:
		return {"status": "ok"}

	return TestClient(app)


def test_settings_default_to_local_development() -> None:
	settings = get_settings()

	assert settings.is_production is False
	assert settings.cors_origins() == [
		"http://localhost:5173",
		"http://127.0.0.1:5173",
		"http://localhost:80",
		"http://127.0.0.1:80",
	]
	assert settings.trusted_hosts() == ["localhost", "127.0.0.1"]


def test_settings_generate_process_local_session_secret_in_development() -> None:
	settings = get_settings()

	first_secret = settings.session_secret_value()
	second_secret = settings.session_secret_value()

	assert first_secret == second_secret
	assert first_secret != "asset-tracker-development-session-secret"
	assert len(first_secret) >= 32


def test_settings_lock_down_same_origin_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setenv("ASSET_TRACKER_APP_ENV", "production")
	monkeypatch.setenv("ASSET_TRACKER_PUBLIC_ORIGIN", "https://finance.example.com/")
	monkeypatch.setenv("ASSET_TRACKER_REDIS_URL", "redis://redis:6379/0")
	monkeypatch.setenv("ASSET_TRACKER_SESSION_SECRET", "session-secret")
	settings = get_settings()

	assert settings.is_production is True
	assert settings.cors_origins() == ["https://finance.example.com"]
	assert settings.trusted_hosts() == ["finance.example.com"]
	assert settings.session_cookie_https_only() is True


def test_settings_allow_non_secure_session_cookie_for_http_origin_in_production(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	monkeypatch.setenv("ASSET_TRACKER_APP_ENV", "production")
	monkeypatch.setenv("ASSET_TRACKER_PUBLIC_ORIGIN", "http://opentrifi.duckdns.org")
	monkeypatch.setenv("ASSET_TRACKER_REDIS_URL", "redis://redis:6379/0")
	monkeypatch.setenv("ASSET_TRACKER_SESSION_SECRET", "session-secret")
	settings = get_settings()

	assert settings.is_production is True
	assert settings.cors_origins() == ["http://opentrifi.duckdns.org"]
	assert settings.session_cookie_https_only() is False


def test_settings_require_redis_url_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setenv("ASSET_TRACKER_APP_ENV", "production")
	monkeypatch.setenv("ASSET_TRACKER_PUBLIC_ORIGIN", "https://finance.example.com/")
	monkeypatch.setenv("ASSET_TRACKER_SESSION_SECRET", "session-secret")
	settings = get_settings()

	with pytest.raises(ValueError, match="ASSET_TRACKER_REDIS_URL"):
		settings.validate_runtime()


def test_settings_require_database_url_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setenv("ASSET_TRACKER_APP_ENV", "production")
	monkeypatch.setenv("ASSET_TRACKER_PUBLIC_ORIGIN", "https://finance.example.com/")
	monkeypatch.setenv("ASSET_TRACKER_REDIS_URL", "redis://redis:6379/0")
	monkeypatch.setenv("ASSET_TRACKER_SESSION_SECRET", "session-secret")
	settings = get_settings()

	with pytest.raises(ValueError, match="ASSET_TRACKER_DATABASE_URL"):
		settings.validate_runtime()


def test_settings_reject_non_postgres_database_url_in_production(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	monkeypatch.setenv("ASSET_TRACKER_APP_ENV", "production")
	monkeypatch.setenv("ASSET_TRACKER_PUBLIC_ORIGIN", "https://finance.example.com/")
	monkeypatch.setenv("ASSET_TRACKER_REDIS_URL", "redis://redis:6379/0")
	monkeypatch.setenv("ASSET_TRACKER_SESSION_SECRET", "session-secret")
	monkeypatch.setenv("ASSET_TRACKER_DATABASE_URL", "mysql+pymysql://user:pass@127.0.0.1:3306/asset_tracker")
	settings = get_settings()

	with pytest.raises(ValueError, match="PostgreSQL"):
		settings.validate_runtime()


def test_settings_validate_runtime_rejects_incomplete_production_config(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	monkeypatch.setenv("ASSET_TRACKER_APP_ENV", "production")
	monkeypatch.setenv("ASSET_TRACKER_PUBLIC_ORIGIN", "https://finance.example.com")

	with pytest.raises(ValueError, match="ASSET_TRACKER_SESSION_SECRET"):
		get_settings().validate_runtime()


def test_verify_api_token_allows_missing_token_when_not_configured() -> None:
	client = _build_client()
	response = client.get("/protected")

	assert response.status_code == 200


def test_verify_api_token_rejects_disallowed_origin(monkeypatch: pytest.MonkeyPatch) -> None:
	client = _build_client()
	response = client.get(
		"/protected",
		headers={
			"Origin": "https://evil.example.com",
		},
	)

	assert response.status_code == 403
	assert response.json() == {"detail": "Origin not allowed."}


def test_verify_api_token_allows_same_origin_requests_in_production(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	monkeypatch.setenv("ASSET_TRACKER_APP_ENV", "production")
	monkeypatch.setenv("ASSET_TRACKER_PUBLIC_ORIGIN", "https://finance.example.com")
	monkeypatch.setenv("ASSET_TRACKER_SESSION_SECRET", "session-secret")
	client = _build_client()
	response = client.get(
		"/protected",
		headers={
			"Origin": "https://finance.example.com",
		},
	)

	assert response.status_code == 200


def test_verify_api_token_allows_production_without_server_token(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	monkeypatch.setenv("ASSET_TRACKER_APP_ENV", "production")
	monkeypatch.setenv("ASSET_TRACKER_PUBLIC_ORIGIN", "https://finance.example.com")
	monkeypatch.setenv("ASSET_TRACKER_SESSION_SECRET", "session-secret")
	client = _build_client()
	response = client.get("/protected", headers={"Origin": "https://finance.example.com"})

	assert response.status_code == 200


def test_normalize_user_id_accepts_lowercase_slug() -> None:
	assert normalize_user_id(" admin_01 ") == "admin_01"


def test_normalize_user_id_rejects_invalid_identifier() -> None:
	with pytest.raises(ValueError):
		normalize_user_id("Admin-01")


def test_normalize_email_accepts_common_address() -> None:
	assert normalize_email(" Admin@Example.com ") == "admin@example.com"


def test_email_digest_round_trip() -> None:
	email_digest = hash_email("admin@example.com")

	assert verify_email("admin@example.com", email_digest) is True
	assert verify_email("other@example.com", email_digest) is False


def test_password_hash_round_trip() -> None:
	password_digest = hash_password("qwer1234")

	assert verify_password("qwer1234", password_digest) is True
	assert verify_password("wrong-pass", password_digest) is False
