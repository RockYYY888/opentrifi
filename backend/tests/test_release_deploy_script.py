from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
SCRIPT_PATH = SCRIPTS_DIR / "release_deploy_and_broadcast.py"
SPEC = importlib.util.spec_from_file_location("release_deploy_and_broadcast", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
release_deploy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_deploy)


def test_deploy_server_uses_plain_ssh_without_password(monkeypatch) -> None:
	recorded_calls: list[tuple[list[str], Path, bool]] = []

	def fake_run(command: list[str], *, cwd: Path | None = None, capture_output: bool = True):
		recorded_calls.append((command, cwd or Path.cwd(), capture_output))
		return None

	monkeypatch.setattr(release_deploy, "_run", fake_run)

	release_deploy._deploy_server(
		"asset-tracker-aws",
		"main",
		"~/finance--tracker",
		password=None,
	)

	assert recorded_calls == [
		(
			[
				"ssh",
				"asset-tracker-aws",
				release_deploy._build_remote_command("main", "~/finance--tracker"),
			],
			release_deploy.REPO_ROOT,
			False,
		),
	]


def test_deploy_server_uses_password_helper_when_password_present(monkeypatch) -> None:
	recorded_calls: list[tuple[str, str, str, str]] = []

	def fake_password_deploy(
		server_ssh: str,
		branch: str,
		server_path: str,
		*,
		password: str,
	) -> None:
		recorded_calls.append((server_ssh, branch, server_path, password))

	monkeypatch.setattr(release_deploy, "_deploy_server_with_password", fake_password_deploy)

	release_deploy._deploy_server(
		"asset-tracker-aws",
		"main",
		"~/finance--tracker",
		password="secret",
	)

	assert recorded_calls == [
		("asset-tracker-aws", "main", "~/finance--tracker", "secret"),
	]
