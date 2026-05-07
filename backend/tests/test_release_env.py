from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "release_env.py"
SPEC = importlib.util.spec_from_file_location("release_env", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
release_env = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_env)


def test_parse_env_file_supports_quotes_export_and_comments(tmp_path: Path) -> None:
	env_file = tmp_path / ".env.release-deploy.local"
	env_file.write_text(
		"\n".join(
			[
				"# comment",
				'ASSET_TRACKER_SERVER_SSH="asset-tracker-aws"',
				"export ASSET_TRACKER_SERVER_PATH='~/finance--tracker'",
				"ASSET_TRACKER_ADMIN_API_KEY=",
			],
		)
		+ "\n",
		encoding="utf-8",
	)

	assert release_env.parse_env_file(env_file) == {
		"ASSET_TRACKER_SERVER_SSH": "asset-tracker-aws",
		"ASSET_TRACKER_SERVER_PATH": "~/finance--tracker",
		"ASSET_TRACKER_ADMIN_API_KEY": "",
	}


def test_load_env_defaults_preserves_existing_environment(
	tmp_path: Path,
	monkeypatch,
) -> None:
	env_file = tmp_path / ".env.release-deploy.local"
	env_file.write_text(
		'ASSET_TRACKER_SERVER_ORIGIN="https://opentrifi.duckdns.org"\n',
		encoding="utf-8",
	)
	monkeypatch.setenv("ASSET_TRACKER_SERVER_ORIGIN", "https://existing.example.com")

	loaded_values = release_env.load_env_defaults(env_file)

	assert loaded_values["ASSET_TRACKER_SERVER_ORIGIN"] == "https://opentrifi.duckdns.org"
	assert release_env.get_env_value("ASSET_TRACKER_SERVER_ORIGIN") == "https://existing.example.com"


def test_resolve_env_file_uses_release_deploy_local(
	tmp_path: Path,
) -> None:
	release_file = tmp_path / ".env.release-deploy.local"
	release_file.write_text("ASSET_TRACKER_ADMIN_USER=admin\n", encoding="utf-8")

	resolved = release_env.resolve_env_file(None, tmp_path)

	assert resolved == release_file
