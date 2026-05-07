from fastapi import APIRouter

from app.schemas import HoldingTransactionApplyRead, SecurityHoldingTransactionRead
from app.services.holding_transaction_service import (
	create_holding_transaction,
	delete_holding_transaction,
	list_all_holding_transactions,
	list_holding_transactions,
	update_holding_transaction,
)

router = APIRouter()

router.add_api_route(
	"/api/holding-transactions",
	list_all_holding_transactions,
	methods=["GET"],
	response_model=list[SecurityHoldingTransactionRead],
)
router.add_api_route(
	"/api/holdings/{holding_id}/transactions",
	list_holding_transactions,
	methods=["GET"],
	response_model=list[SecurityHoldingTransactionRead],
)
router.add_api_route(
	"/api/holding-transactions",
	create_holding_transaction,
	methods=["POST"],
	response_model=HoldingTransactionApplyRead,
	status_code=201,
)
router.add_api_route(
	"/api/holding-transactions/{transaction_id}",
	update_holding_transaction,
	methods=["PATCH"],
	response_model=HoldingTransactionApplyRead,
)
router.add_api_route(
	"/api/holding-transactions/{transaction_id}",
	delete_holding_transaction,
	methods=["DELETE"],
	status_code=204,
)
