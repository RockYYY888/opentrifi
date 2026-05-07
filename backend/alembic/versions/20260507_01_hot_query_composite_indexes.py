"""add composite indexes for hot dashboard and audit queries

Revision ID: 20260507_01
Revises: 20260327_01
Create Date: 2026-05-07 22:30:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260507_01"
down_revision: Union[str, Sequence[str], None] = "20260327_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX_SPECS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
	(
		"ix_securityholdingtransaction_user_symbol_market_traded_created_id",
		"securityholdingtransaction",
		("user_id", "symbol", "market", "traded_on", "created_at", "id"),
	),
	(
		"ix_securityholdingtransaction_user_traded_created_id",
		"securityholdingtransaction",
		("user_id", "traded_on", "created_at", "id"),
	),
	(
		"ix_portfoliosnapshot_user_created",
		"portfoliosnapshot",
		("user_id", "created_at"),
	),
	(
		"ix_holdingperformancesnapshot_user_scope_symbol_created",
		"holdingperformancesnapshot",
		("user_id", "scope", "symbol", "created_at"),
	),
	(
		"ix_realtimeportfoliosnapshot_user_created",
		"realtimeportfoliosnapshot",
		("user_id", "created_at"),
	),
	(
		"ix_realtimeholdingperformancesnapshot_user_scope_symbol_created",
		"realtimeholdingperformancesnapshot",
		("user_id", "scope", "symbol", "created_at"),
	),
	(
		"ix_assetmutationaudit_user_created",
		"assetmutationaudit",
		("user_id", "created_at"),
	),
	(
		"ix_assetmutationaudit_user_agent_task_created",
		"assetmutationaudit",
		("user_id", "agent_task_id", "created_at"),
	),
	(
		"ix_userfeedback_source_status_priority_created_id",
		"userfeedback",
		("source", "status", "priority", "created_at", "id"),
	),
	(
		"ix_userfeedback_status_priority_created_id",
		"userfeedback",
		("status", "priority", "created_at", "id"),
	),
	(
		"ix_userfeedback_user_created_id",
		"userfeedback",
		("user_id", "created_at", "id"),
	),
)


def _existing_indexes(inspector: sa.Inspector, table_name: str) -> set[str]:
	return {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade() -> None:
	bind = op.get_bind()
	inspector = sa.inspect(bind)
	table_names = set(inspector.get_table_names())

	for index_name, table_name, columns in INDEX_SPECS:
		if table_name not in table_names:
			continue
		if index_name in _existing_indexes(inspector, table_name):
			continue
		op.create_index(index_name, table_name, list(columns), unique=False)


def downgrade() -> None:
	bind = op.get_bind()
	inspector = sa.inspect(bind)
	table_names = set(inspector.get_table_names())

	for index_name, table_name, _columns in reversed(INDEX_SPECS):
		if table_name not in table_names:
			continue
		if index_name not in _existing_indexes(inspector, table_name):
			continue
		op.drop_index(index_name, table_name=table_name)
