from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import push_release_note_from_changelog as release_note_push
import release_env


REPO_ROOT = Path(__file__).resolve().parents[1]
CHANGELOG_PATH = REPO_ROOT / "CHANGELOG.md"
DEFAULT_BRANCH = "main"
DEFAULT_SERVER_PATH = "~/finance--tracker"
DEFAULT_DEPLOY_COMMAND = (
	"docker compose -f docker-compose.yml -f docker-compose.production.yml "
	"up -d --build --remove-orphans"
)
DEFAULT_VERIFY_COMMAND = (
	"docker compose -f docker-compose.yml -f docker-compose.production.yml ps && "
	"curl -k -fsS https://127.0.0.1/api/health >/dev/null && "
	"docker compose -f docker-compose.yml -f docker-compose.production.yml exec -T redis redis-cli ping >/dev/null && "
	"docker compose -f docker-compose.yml -f docker-compose.production.yml exec -T postgres "
	"sh -lc 'psql -U \"$POSTGRES_USER\" -d \"$POSTGRES_DB\" -c "
	"\"select * from alembic_version;\"' >/dev/null"
)


def _prepare_environment(argv: list[str] | None = None) -> tuple[list[str], Path | None]:
	bootstrap_parser = argparse.ArgumentParser(add_help=False)
	bootstrap_parser.add_argument(
		"--env-file",
		default=None,
		help="Optional env file that provides release deploy defaults.",
	)
	bootstrap_args, remaining_argv = bootstrap_parser.parse_known_args(argv)
	env_file = release_env.resolve_env_file(bootstrap_args.env_file, REPO_ROOT)
	release_env.load_env_defaults(env_file)
	return remaining_argv, env_file


def _run(
	command: list[str],
	*,
	cwd: Path | None = None,
	capture_output: bool = True,
) -> subprocess.CompletedProcess[str]:
	return subprocess.run(
		command,
		check=True,
		text=True,
		cwd=cwd,
		capture_output=capture_output,
	)


def _normalize_version(value: str | None) -> str | None:
	return release_note_push._normalize_version(value)


def _load_changelog_entry(version: str | None) -> dict[str, str]:
	return release_note_push._load_changelog_entry(CHANGELOG_PATH, version)


def _extract_default_title(body: str, release_name: str | None, version: str) -> str:
	return release_note_push._extract_default_title(body, release_name, version)


def _require_clean_worktree() -> None:
	completed = _run(["git", "status", "--porcelain"], cwd=REPO_ROOT)
	if completed.stdout.strip():
		raise RuntimeError("Git worktree is not clean. Commit or stash changes before running release deploy.")


def _require_current_branch(branch: str) -> None:
	completed = _run(["git", "branch", "--show-current"], cwd=REPO_ROOT)
	current_branch = completed.stdout.strip()
	if current_branch != branch:
		raise RuntimeError(f"Current branch is {current_branch or '<detached>'}, expected {branch}.")


def _git_pull_and_push(branch: str) -> None:
	_run(["git", "pull", "--ff-only", "origin", branch], cwd=REPO_ROOT, capture_output=False)
	_run(["git", "push", "origin", branch], cwd=REPO_ROOT, capture_output=False)


def _release_notes_without_url(body: str) -> str:
	lines = [line for line in body.splitlines() if not line.startswith("- GitHub Release:")]
	return "\n".join(lines).strip()


def _create_or_load_github_release(
	*,
	version: str,
	release_title: str,
	release_notes: str,
	branch: str,
) -> dict[str, Any]:
	try:
		return release_note_push._run_gh_release_view(version)
	except RuntimeError as exc:
		if "Unable to inspect GitHub release." not in str(exc) and "release not found" not in str(exc).lower():
			raise

	command = [
		"gh",
		"release",
		"create",
		f"v{version}",
		"--target",
		branch,
		"--title",
		release_title,
		"--notes",
		release_notes,
	]
	_run(command, cwd=REPO_ROOT, capture_output=False)
	return release_note_push._run_gh_release_view(version)


def _update_changelog_release_url(version: str, release_url: str) -> bool:
	content = CHANGELOG_PATH.read_text(encoding="utf-8")
	entry = _load_changelog_entry(version)
	heading = f"## v{version} - {entry['date']}"
	start_index = content.index(heading)
	next_heading_index = content.find("\n## v", start_index + len(heading))
	end_index = len(content) if next_heading_index == -1 else next_heading_index
	entry_block = content[start_index:end_index]
	release_line = f"- GitHub Release: {release_url}"

	if release_line in entry_block:
		return False

	lines = entry_block.rstrip().splitlines()
	lines = [line for line in lines if not line.startswith("- GitHub Release:")]
	lines.append(release_line)
	new_entry_block = "\n".join(lines) + "\n"
	new_content = content[:start_index] + new_entry_block + content[end_index:]
	CHANGELOG_PATH.write_text(new_content, encoding="utf-8")
	return True


def _commit_changelog_release_url(version: str, branch: str) -> None:
	_run(["git", "add", "CHANGELOG.md"], cwd=REPO_ROOT, capture_output=False)
	_run(
		["git", "commit", "-m", f"docs(changelog): add release url for v{version}"],
		cwd=REPO_ROOT,
		capture_output=False,
	)
	_run(["git", "push", "origin", branch], cwd=REPO_ROOT, capture_output=False)


def _build_remote_command(branch: str, server_path: str) -> str:
	return " && ".join(
		[
			f"cd {server_path}",
			f"git checkout {branch}",
			f"git pull --ff-only origin {branch}",
			DEFAULT_DEPLOY_COMMAND,
			DEFAULT_VERIFY_COMMAND,
		],
	)


def _deploy_server_with_password(
	server_ssh: str,
	branch: str,
	server_path: str,
	*,
	password: str,
) -> None:
	try:
		import pexpect
	except ImportError as exc:
		raise RuntimeError(
			"Password-based SSH deploy requires `pexpect`, or configure a non-interactive SSH key instead.",
		) from exc

	command = [
		"ssh",
		"-o",
		"StrictHostKeyChecking=accept-new",
		server_ssh,
		_build_remote_command(branch, server_path),
	]
	child = pexpect.spawn(
		command[0],
		command[1:],
		encoding="utf-8",
		timeout=None,
	)
	child.logfile_read = sys.stdout
	try:
		while True:
			matched_index = child.expect(
				[
					r"(?i)password:",
					r"Are you sure you want to continue connecting",
					r"Permission denied",
					pexpect.EOF,
				],
			)
			if matched_index == 0:
				child.sendline(password)
				continue
			if matched_index == 1:
				child.sendline("yes")
				continue
			if matched_index == 2:
				raise RuntimeError("SSH authentication failed while deploying the server.")
			break
	finally:
		child.close()

	if child.exitstatus not in {0, None} or child.signalstatus is not None:
		raise RuntimeError("Remote deploy command failed.")


def _deploy_server(server_ssh: str, branch: str, server_path: str, *, password: str | None) -> None:
	if password:
		_deploy_server_with_password(
			server_ssh,
			branch,
			server_path,
			password=password,
		)
		return
	_run(
		["ssh", server_ssh, _build_remote_command(branch, server_path)],
		cwd=REPO_ROOT,
		capture_output=False,
	)


def _push_release_note(
	*,
	origin: str,
	admin_api_key: str,
	version: str,
	user_title: str,
	user_content: str,
) -> None:
	command = [
		sys.executable,
		str(REPO_ROOT / "scripts" / "push_release_note_from_changelog.py"),
		"--origin",
		origin,
		"--admin-api-key",
		admin_api_key,
		"--version",
		version,
		"--title",
		user_title,
		"--content",
		user_content,
	]
	_run(command, cwd=REPO_ROOT, capture_output=False)


def main(argv: list[str] | None = None) -> None:
	remaining_argv, env_file = _prepare_environment(argv)
	parser = argparse.ArgumentParser(
		description=(
			"Create or verify the GitHub release, deploy main to the server, and push the "
			"same version into the in-app release-note stream."
		),
	)
	parser.add_argument(
		"--env-file",
		default=str(env_file) if env_file is not None else None,
		help=(
			"Optional env file with defaults such as server SSH, server origin, and the admin "
			"API key. Defaults to .env.release-deploy.local when present."
		),
	)
	parser.add_argument("--version", default=None, help="Defaults to the latest version in CHANGELOG.md.")
	parser.add_argument("--branch", default=DEFAULT_BRANCH)
	parser.add_argument("--release-title", default=None)
	parser.add_argument("--server-ssh", default=release_env.get_env_value("ASSET_TRACKER_SERVER_SSH"))
	parser.add_argument(
		"--server-ssh-password",
		default=release_env.get_env_value("ASSET_TRACKER_SERVER_SSH_PASSWORD"),
		help="Optional SSH password. Prefer SSH keys when available.",
	)
	parser.add_argument("--server-path", default=os.getenv("ASSET_TRACKER_SERVER_PATH", DEFAULT_SERVER_PATH))
	parser.add_argument(
		"--server-origin",
		default=release_env.get_env_value(
			"ASSET_TRACKER_SERVER_ORIGIN",
			"FEEDBACK_API_BASE_URL",
		),
	)
	parser.add_argument(
		"--admin-api-key",
		default=release_env.get_env_value(
			"ASSET_TRACKER_ADMIN_API_KEY",
			"FEEDBACK_ADMIN_API_KEY",
		),
	)
	parser.add_argument("--user-title", required=True)
	parser.add_argument(
		"--bullet",
		action="append",
		dest="bullets",
		default=[],
		help="Repeat 2 to 4 times for the user-facing release-note bullets.",
	)
	parser.add_argument("--dry-run", action="store_true")
	args = parser.parse_args(remaining_argv)

	version = _normalize_version(args.version)
	entry = _load_changelog_entry(version)
	version = entry["version"]

	if not args.server_ssh:
		raise RuntimeError("--server-ssh or ASSET_TRACKER_SERVER_SSH is required.")
	if not args.server_origin:
		raise RuntimeError("--server-origin or ASSET_TRACKER_SERVER_ORIGIN is required.")
	if not args.admin_api_key:
		raise RuntimeError("--admin-api-key or ASSET_TRACKER_ADMIN_API_KEY is required.")
	if not 2 <= len(args.bullets) <= 4:
		raise RuntimeError("Provide 2 to 4 --bullet values for the user-facing release note.")

	user_content = "\n".join(f"- {bullet.strip()}" for bullet in args.bullets if bullet.strip())
	if user_content.count("\n") + 1 < 2:
		raise RuntimeError("Release-note bullets cannot be empty.")

	_require_clean_worktree()
	_require_current_branch(args.branch)
	_git_pull_and_push(args.branch)

	release_notes = _release_notes_without_url(entry["body"])
	release_title = args.release_title or _extract_default_title(release_notes, None, version)
	release_payload = _create_or_load_github_release(
		version=version,
		release_title=release_title,
		release_notes=release_notes,
		branch=args.branch,
	)
	release_url = release_payload["url"]

	changelog_updated = _update_changelog_release_url(version, release_url)
	if changelog_updated:
		_commit_changelog_release_url(version, args.branch)

	plan = {
		"version": version,
		"release_title": release_title,
		"release_url": release_url,
		"env_file": args.env_file,
		"server_ssh": args.server_ssh,
		"server_ssh_password_set": bool(args.server_ssh_password),
		"server_origin": args.server_origin,
		"admin_api_key_set": bool(args.admin_api_key),
		"user_title": args.user_title,
		"user_content": user_content,
		"changelog_updated": changelog_updated,
		"dry_run": args.dry_run,
	}
	print(json.dumps(plan, ensure_ascii=False, indent=2))

	if args.dry_run:
		return

	_deploy_server(
		args.server_ssh,
		args.branch,
		args.server_path,
		password=args.server_ssh_password,
	)
	_push_release_note(
		origin=args.server_origin,
		admin_api_key=args.admin_api_key,
		version=version,
		user_title=args.user_title,
		user_content=user_content,
	)


if __name__ == "__main__":
	try:
		main()
	except RuntimeError as exc:
		print(str(exc), file=sys.stderr)
		raise SystemExit(1) from exc
