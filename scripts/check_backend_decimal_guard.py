#!/usr/bin/env python3
from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_APP = REPO_ROOT / "backend" / "app"
ALLOWED_FLOAT_NAME_FILES = {
	"fixed_precision.py",
}
ALLOWED_FLOAT_LITERAL_FILES = {
	"services/market_data_parts/client.py",
	"services/job_service.py",
	"services/realtime_analytics_service.py",
}


def _relative_path(path: Path) -> str:
	return path.relative_to(BACKEND_APP).as_posix()


def _node_location(path: Path, node: ast.AST) -> str:
	return f"{path.relative_to(REPO_ROOT)}:{getattr(node, 'lineno', 1)}"


def _is_float_name_violation(path: Path, node: ast.Name) -> bool:
	if node.id != "float":
		return False
	return _relative_path(path) not in ALLOWED_FLOAT_NAME_FILES


def _is_float_literal_violation(path: Path, node: ast.Constant) -> bool:
	if not isinstance(node.value, float):
		return False
	relative_path = _relative_path(path)
	if relative_path in ALLOWED_FLOAT_LITERAL_FILES:
		return False
	return relative_path != "fixed_precision.py"


def _find_violations(path: Path) -> list[str]:
	tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
	violations: list[str] = []
	for node in ast.walk(tree):
		if isinstance(node, ast.Name) and _is_float_name_violation(path, node):
			violations.append(f"{_node_location(path, node)} uses the float type directly")
		if isinstance(node, ast.Constant) and _is_float_literal_violation(path, node):
			violations.append(f"{_node_location(path, node)} uses a float literal")
	return violations


def main() -> int:
	violations: list[str] = []
	for path in sorted(BACKEND_APP.rglob("*.py")):
		violations.extend(_find_violations(path))

	if not violations:
		print("Backend Decimal guard passed.")
		return 0

	print("Backend Decimal guard failed. Use Decimal for financial values.")
	for violation in violations:
		print(f"- {violation}")
	return 1


if __name__ == "__main__":
	raise SystemExit(main())
