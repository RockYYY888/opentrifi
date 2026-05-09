from __future__ import annotations

from typing import Any


def sql_expr(value: object) -> Any:
	"""Expose SQLModel runtime column expressions to static type checkers."""
	return value
