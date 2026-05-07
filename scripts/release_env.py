from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Iterable


DEFAULT_RELEASE_ENV_FILE_NAMES = (
	".env.release-deploy.local",
)


def parse_env_file(path: Path) -> dict[str, str]:
	values: dict[str, str] = {}
	for raw_line in path.read_text(encoding="utf-8").splitlines():
		line = raw_line.strip()
		if not line or line.startswith("#"):
			continue
		if line.startswith("export "):
			line = line[len("export ") :].strip()
		if "=" not in line:
			continue

		key, raw_value = line.split("=", 1)
		key = key.strip()
		value = raw_value.strip()
		if not key:
			continue
		if value and value[0] in {'"', "'"}:
			try:
				parsed = ast.literal_eval(value)
			except (SyntaxError, ValueError):
				parsed = value.strip("\"'")
			value = str(parsed)
		values[key] = value
	return values


def load_env_defaults(path: Path | None) -> dict[str, str]:
	if path is None or not path.exists():
		return {}

	values = parse_env_file(path)
	for key, value in values.items():
		os.environ.setdefault(key, value)
	return values


def resolve_env_file(
	explicit_path: str | None,
	repo_root: Path,
	*,
	default_names: Iterable[str] = DEFAULT_RELEASE_ENV_FILE_NAMES,
) -> Path | None:
	if explicit_path:
		return Path(explicit_path).expanduser()

	for name in default_names:
		candidate = repo_root / name
		if candidate.exists():
			return candidate
	return None


def get_env_value(*names: str) -> str | None:
	for name in names:
		value = os.getenv(name)
		if value is None:
			continue
		normalized = value.strip()
		if normalized:
			return normalized
	return None
