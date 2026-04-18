from functools import lru_cache
import secrets
from urllib.parse import urlparse

from pydantic import PrivateAttr, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


LOCAL_ORIGINS = [
	"http://localhost:5173",
	"http://127.0.0.1:5173",
	"http://localhost:80",
	"http://127.0.0.1:80",
]
LOCAL_HOSTS = ["localhost", "127.0.0.1"]


def _split_csv(value: str | None) -> list[str]:
	return [item.strip() for item in (value or "").split(",") if item.strip()]


def _normalize_origin(value: str) -> str:
	parsed = urlparse(value.strip())
	if parsed.scheme not in {"http", "https"} or not parsed.netloc:
		raise ValueError(f"Invalid origin: {value!r}")
	return f"{parsed.scheme}://{parsed.netloc}"


def _host_from_origin(value: str) -> str:
	hostname = urlparse(value).hostname
	if not hostname:
		raise ValueError(f"Invalid origin host: {value!r}")
	return hostname


def _unique(values: list[str]) -> list[str]:
	return list(dict.fromkeys(values))


class Settings(BaseSettings):
	"""Runtime configuration for local-only development and hardened deployments."""

	model_config = SettingsConfigDict(
		env_file=".env",
		env_prefix="ASSET_TRACKER_",
		extra="ignore",
	)
	_runtime_session_secret: str | None = PrivateAttr(default=None)

	app_env: str = "development"
	session_secret: SecretStr | None = None
	public_origin: str | None = None
	allowed_origins: str | None = None
	allowed_hosts: str | None = None
	redis_url: str | None = None
	database_url: str | None = None

	@property
	def is_production(self) -> bool:
		return self.app_env.strip().lower() == "production"

	def _configured_session_secret_value(self) -> str | None:
		if self.session_secret is None:
			return None

		secret = self.session_secret.get_secret_value().strip()
		return secret or None

	def session_secret_value(self) -> str:
		configured_secret = self._configured_session_secret_value()
		if configured_secret is not None:
			return configured_secret

		if self.is_production:
			raise ValueError("Production mode requires ASSET_TRACKER_SESSION_SECRET.")

		if self._runtime_session_secret is None:
			self._runtime_session_secret = secrets.token_urlsafe(32)

		return self._runtime_session_secret

	def redis_url_value(self) -> str | None:
		if self.redis_url is None:
			return None
		url = self.redis_url.strip()
		return url or None

	def database_url_value(self) -> str | None:
		if self.database_url is None:
			return None
		url = self.database_url.strip()
		return url or None

	def database_uses_postgresql(self) -> bool:
		database_url = self.database_url_value()
		if database_url is None:
			return False
		return urlparse(database_url).scheme.lower().startswith("postgresql")

	def email_pepper_value(self) -> str:
		configured_secret = self._configured_session_secret_value()
		if configured_secret is not None:
			return configured_secret

		return "asset-tracker-email-digest-pepper"

	def cors_origins(self) -> list[str]:
		configured_origins = [_normalize_origin(item) for item in _split_csv(self.allowed_origins)]
		if configured_origins:
			return configured_origins

		if self.public_origin:
			return [_normalize_origin(self.public_origin)]

		if self.is_production:
			return []

		return LOCAL_ORIGINS.copy()

	def trusted_hosts(self) -> list[str]:
		configured_hosts = _split_csv(self.allowed_hosts)
		if configured_hosts:
			return configured_hosts

		derived_hosts = [_host_from_origin(origin) for origin in self.cors_origins()]
		if not self.is_production:
			derived_hosts.extend(LOCAL_HOSTS)

		return _unique(derived_hosts or LOCAL_HOSTS.copy())

	def is_allowed_origin(self, origin: str) -> bool:
		try:
			normalized_origin = _normalize_origin(origin)
		except ValueError:
			return False

		return normalized_origin in self.cors_origins()

	def session_cookie_https_only(self) -> bool:
		origins = self.cors_origins()
		if not origins:
			return False
		return all(urlparse(origin).scheme == "https" for origin in origins)

	def validate_runtime(self) -> None:
		database_url = self.database_url_value()

		if self.is_production and not (self.public_origin or self.allowed_origins or self.allowed_hosts):
			raise ValueError(
				"Production mode requires ASSET_TRACKER_PUBLIC_ORIGIN, "
				"ASSET_TRACKER_ALLOWED_ORIGINS, or ASSET_TRACKER_ALLOWED_HOSTS.",
			)

		if self.is_production and not self._configured_session_secret_value():
			raise ValueError("Production mode requires ASSET_TRACKER_SESSION_SECRET.")
		if self.is_production and not self.redis_url_value():
			raise ValueError("Production mode requires ASSET_TRACKER_REDIS_URL.")
		if self.is_production and not database_url:
			raise ValueError("Production mode requires ASSET_TRACKER_DATABASE_URL.")
		if database_url and not self.database_uses_postgresql():
			raise ValueError("Asset Tracker only supports PostgreSQL database URLs.")


@lru_cache
def get_settings() -> Settings:
	return Settings()
