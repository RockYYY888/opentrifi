#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys


DEFAULT_BASELINE = 0
ERROR_COUNT_PATTERN = re.compile(r"(?P<count>\d+)\s+errors?")


def _parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(
		description="Run Pyright and fail only when the error count exceeds the ratchet baseline.",
	)
	parser.add_argument(
		"--baseline",
		type=int,
		default=DEFAULT_BASELINE,
		help="Maximum allowed Pyright error count.",
	)
	parser.add_argument(
		"targets",
		nargs=argparse.REMAINDER,
		help="Optional Pyright targets after --, for example: -- app tests.",
	)
	return parser.parse_args()


def _normalize_targets(raw_targets: list[str]) -> list[str]:
	if raw_targets and raw_targets[0] == "--":
		return raw_targets[1:]
	return raw_targets or ["app", "tests"]


def _extract_error_count(output: str, returncode: int) -> int:
	matches = list(ERROR_COUNT_PATTERN.finditer(output))
	if matches:
		return int(matches[-1].group("count"))
	return 0 if returncode == 0 else sys.maxsize


def main() -> int:
	args = _parse_args()
	command = ["uv", "run", "pyright", *_normalize_targets(args.targets)]
	completed = subprocess.run(command, check=False, text=True, capture_output=True)
	output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
	error_count = _extract_error_count(output, completed.returncode)
	if error_count > args.baseline:
		if output:
			print(output, end="" if output.endswith("\n") else "\n")
		print(
			f"Pyright ratchet failed: {error_count} errors exceeds baseline {args.baseline}.",
			file=sys.stderr,
		)
		return 1

	if os.environ.get("PYRIGHT_RATCHET_SHOW_OUTPUT") == "1" and output:
		print(output, end="" if output.endswith("\n") else "\n")
	print(f"Pyright ratchet passed: {error_count} errors within baseline {args.baseline}.")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
