from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any
from urllib import error, request

import release_env

CHANGELOG_HEADING_PATTERN = re.compile(r"^## v(?P<version>\d+\.\d+\.\d+) - (?P<date>\d{4}-\d{2}-\d{2})$")
REPO_ROOT = Path(__file__).resolve().parents[1]


def _prepare_environment(argv: list[str] | None = None) -> tuple[list[str], Path | None]:
	bootstrap_parser = argparse.ArgumentParser(add_help=False)
	bootstrap_parser.add_argument(
		"--env-file",
		default=None,
		help="Optional env file that provides release publishing defaults.",
	)
	bootstrap_args, remaining_argv = bootstrap_parser.parse_known_args(argv)
	env_file = release_env.resolve_env_file(bootstrap_args.env_file, REPO_ROOT)
	release_env.load_env_defaults(env_file)
	return remaining_argv, env_file


def _normalize_origin(origin: str) -> str:
	return origin.rstrip("/")


def _normalize_optional_text(value: str | None) -> str | None:
	if value is None:
		return None
	normalized = value.strip()
	return normalized or None


def _normalize_version(version: str | None) -> str | None:
	if version is None:
		return None
	normalized = version.strip()
	if normalized.startswith("v"):
		normalized = normalized[1:]
	return normalized or None


def _ensure_clean_changelog(changelog_path: Path) -> None:
	try:
		repo_root_process = subprocess.run(
			[
				"git",
				"-C",
				str(changelog_path.parent),
				"rev-parse",
				"--show-toplevel",
			],
			check=True,
			text=True,
			capture_output=True,
		)
	except (FileNotFoundError, subprocess.CalledProcessError):
		return

	repo_root = Path(repo_root_process.stdout.strip())
	try:
		relative_path = changelog_path.resolve().relative_to(repo_root)
	except ValueError:
		return

	for diff_args in (
		["git", "-C", str(repo_root), "diff", "--quiet", "--", str(relative_path)],
		["git", "-C", str(repo_root), "diff", "--cached", "--quiet", "--", str(relative_path)],
	):
		completed = subprocess.run(diff_args, check=False)
		if completed.returncode == 1:
			raise RuntimeError("CHANGELOG.md has local modifications. Commit or stash it before publishing.")
		if completed.returncode != 0:
			raise RuntimeError("Unable to verify CHANGELOG.md git status.")


def _load_changelog_entry(changelog_path: Path, version: str | None) -> dict[str, str]:
	content = changelog_path.read_text(encoding="utf-8")
	lines = content.splitlines()
	entries: list[dict[str, str]] = []
	current_entry: dict[str, str] | None = None
	current_body: list[str] = []

	for line in lines:
		heading_match = CHANGELOG_HEADING_PATTERN.match(line.strip())
		if heading_match is not None:
			if current_entry is not None:
				current_entry["body"] = "\n".join(current_body).strip()
				entries.append(current_entry)
			current_entry = heading_match.groupdict()
			current_body = []
			continue
		if current_entry is not None:
			current_body.append(line)

	if current_entry is not None:
		current_entry["body"] = "\n".join(current_body).strip()
		entries.append(current_entry)

	if not entries:
		raise RuntimeError("CHANGELOG.md does not contain any version sections.")

	if version is None:
		return entries[0]

	for entry in entries:
		if entry["version"] == version:
			return entry

	raise RuntimeError(f"Version {version} was not found in {changelog_path}.")


def _extract_default_title(body: str, release_name: str | None, version: str) -> str:
	if release_name:
		for prefix in (f"v{version} - ", f"v{version} – ", f"v{version}: "):
			if release_name.startswith(prefix):
				candidate = release_name[len(prefix) :].strip()
				if candidate:
					return candidate
		if release_name.strip():
			return release_name.strip()

	for line in body.splitlines():
		stripped = line.strip()
		if stripped.startswith("- "):
			return stripped[2:].strip()
		if stripped:
			return stripped

	return f"v{version} Update"


def _run_gh_release_view(version: str) -> dict[str, Any]:
	try:
		completed = subprocess.run(
			[
				"gh",
				"release",
				"view",
				f"v{version}",
				"--json",
				"tagName,url,name,isDraft,isPrerelease",
			],
			check=True,
			text=True,
			capture_output=True,
		)
	except FileNotFoundError as exc:
		raise RuntimeError("`gh` is required to verify the GitHub release.") from exc
	except subprocess.CalledProcessError as exc:
		raise RuntimeError(exc.stderr.strip() or exc.stdout.strip() or "Unable to inspect GitHub release.") from exc

	release_payload = json.loads(completed.stdout)
	if release_payload.get("tagName") != f"v{version}":
		raise RuntimeError("GitHub release tag does not match the changelog version.")
	if release_payload.get("isDraft") is True:
		raise RuntimeError("GitHub release is still a draft.")
	if release_payload.get("isPrerelease") is True:
		raise RuntimeError("GitHub release is still marked as a prerelease.")
	return release_payload


def _json_request(
	opener: request.OpenerDirector,
	*,
	method: str,
	url: str,
	payload: dict[str, Any] | None,
	authorization: str | None = None,
) -> dict[str, Any]:
	body = None if payload is None else json.dumps(payload).encode("utf-8")
	headers = {"Content-Type": "application/json"}
	if authorization:
		headers["Authorization"] = authorization
	req = request.Request(url, data=body, headers=headers, method=method)
	try:
		with opener.open(req) as response:
			response_body = response.read().decode("utf-8")
	except error.HTTPError as exc:
		error_body = exc.read().decode("utf-8", errors="replace")
		raise RuntimeError(f"{method} {url} failed: {exc.code} {error_body}") from exc
	return {} if not response_body else json.loads(response_body)


def _publish_release_note_with_admin_api_key(
	opener: request.OpenerDirector,
	*,
	origin: str,
	admin_api_key: str,
	payload: dict[str, Any],
) -> dict[str, Any]:
	return _json_request(
		opener,
		method="POST",
		url=f"{origin}/api/admin/release-notes/publish-changelog",
		payload=payload,
		authorization=f"Bearer {admin_api_key}",
	)


def main(argv: list[str] | None = None) -> None:
	remaining_argv, env_file = _prepare_environment(argv)
	parser = argparse.ArgumentParser(
		description="Publish the local changelog version into the server release-note inbox stream.",
	)
	parser.add_argument(
		"--env-file",
		default=str(env_file) if env_file is not None else None,
		help=(
			"Optional env file with defaults such as origin and the admin API key. "
			"Defaults to .env.release-deploy.local when present."
		),
	)
	parser.add_argument(
		"--origin",
		default=release_env.get_env_value(
			"ASSET_TRACKER_SERVER_ORIGIN",
			"FEEDBACK_API_BASE_URL",
		),
		help="Server origin, for example https://finance.example.com or http://127.0.0.1:80",
	)
	parser.add_argument(
		"--admin-api-key",
		default=release_env.get_env_value(
			"ASSET_TRACKER_ADMIN_API_KEY",
			"FEEDBACK_ADMIN_API_KEY",
		),
		help="Admin API key used to publish the changelog-backed release note.",
	)
	parser.add_argument(
		"--version",
		default=None,
		help="Semantic version to publish. Defaults to the latest version block in CHANGELOG.md.",
	)
	parser.add_argument(
		"--title",
		default=None,
		help="Optional release-note title override. Defaults to the GitHub release name or first changelog bullet.",
	)
	parser.add_argument(
		"--content",
		default=None,
		help=(
			"Optional release-note content override. "
			"Use this for a shorter user-facing summary than the full changelog body."
		),
	)
	parser.add_argument(
		"--changelog",
		type=Path,
		default=REPO_ROOT / "CHANGELOG.md",
		help="Path to CHANGELOG.md.",
	)
	parser.add_argument(
		"--dry-run",
		action="store_true",
		help="Print the resolved payload without sending it to the server.",
	)
	args = parser.parse_args(remaining_argv)

	if not args.origin:
		raise RuntimeError("--origin or ASSET_TRACKER_SERVER_ORIGIN is required.")
	if not args.admin_api_key:
		raise RuntimeError("--admin-api-key or ASSET_TRACKER_ADMIN_API_KEY is required.")

	version = _normalize_version(args.version)
	_ensure_clean_changelog(args.changelog)
	entry = _load_changelog_entry(args.changelog, version)
	release_payload = _run_gh_release_view(entry["version"])
	title = args.title or _extract_default_title(
		entry["body"],
		release_payload.get("name"),
		entry["version"],
	)
	content = _normalize_optional_text(args.content) or entry["body"]
	payload = {
		"version": entry["version"],
		"title": title,
		"content": content,
		"release_url": release_payload.get("url"),
		"source_feedback_ids": [],
	}

	print(
		json.dumps(
			{
				"origin": _normalize_origin(args.origin),
				"version": payload["version"],
				"title": payload["title"],
				"content": payload["content"],
				"release_url": payload["release_url"],
				"env_file": args.env_file,
				"dry_run": args.dry_run,
			},
			ensure_ascii=False,
			indent=2,
		),
	)

	if args.dry_run:
		return

	opener = request.build_opener()
	origin = _normalize_origin(args.origin)
	response_payload = _publish_release_note_with_admin_api_key(
		opener,
		origin=origin,
		admin_api_key=args.admin_api_key,
		payload=payload,
	)
	print(json.dumps(response_payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
	try:
		main()
	except RuntimeError as exc:
		print(str(exc), file=sys.stderr)
		raise SystemExit(1) from exc
