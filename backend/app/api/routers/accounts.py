from fastapi import APIRouter

from app.schemas import (
	AssetRecordRead,
	AssetMutationAuditRead,
	CashAccountRead,
	CashLedgerAdjustmentApplyRead,
	CashLedgerEntryRead,
)
from app.services.asset_record_service import list_asset_records
from app.services.cash_account_service import (
	create_account,
	create_cash_ledger_adjustment,
	delete_account,
	delete_cash_ledger_adjustment,
	list_accounts,
	list_asset_mutation_audits,
	list_cash_ledger_entries,
	update_account,
	update_cash_ledger_adjustment,
)

router = APIRouter()

router.add_api_route("/api/accounts", list_accounts, methods=["GET"], response_model=list[CashAccountRead])
router.add_api_route("/api/accounts", create_account, methods=["POST"], response_model=CashAccountRead, status_code=201)
router.add_api_route("/api/accounts/{account_id}", update_account, methods=["PUT"], response_model=CashAccountRead)
router.add_api_route("/api/accounts/{account_id}", delete_account, methods=["DELETE"], status_code=204)
router.add_api_route(
	"/api/cash-ledger",
	list_cash_ledger_entries,
	methods=["GET"],
	response_model=list[CashLedgerEntryRead],
)
router.add_api_route(
	"/api/cash-ledger/adjustments",
	create_cash_ledger_adjustment,
	methods=["POST"],
	response_model=CashLedgerAdjustmentApplyRead,
	status_code=201,
)
router.add_api_route(
	"/api/cash-ledger/adjustments/{entry_id}",
	update_cash_ledger_adjustment,
	methods=["PATCH"],
	response_model=CashLedgerAdjustmentApplyRead,
)
router.add_api_route(
	"/api/cash-ledger/adjustments/{entry_id}",
	delete_cash_ledger_adjustment,
	methods=["DELETE"],
	status_code=204,
)
router.add_api_route(
	"/api/audit-log",
	list_asset_mutation_audits,
	methods=["GET"],
	response_model=list[AssetMutationAuditRead],
)
router.add_api_route(
	"/api/asset-records",
	list_asset_records,
	methods=["GET"],
	response_model=list[AssetRecordRead],
)
