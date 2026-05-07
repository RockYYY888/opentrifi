from fastapi import APIRouter

from app.schemas import CashTransferApplyRead, CashTransferRead
from app.services.cash_account_service import (
	create_cash_transfer,
	delete_cash_transfer,
	list_cash_transfers,
	update_cash_transfer,
)

router = APIRouter()

router.add_api_route(
	"/api/cash-transfers",
	list_cash_transfers,
	methods=["GET"],
	response_model=list[CashTransferRead],
)
router.add_api_route(
	"/api/cash-transfers",
	create_cash_transfer,
	methods=["POST"],
	response_model=CashTransferApplyRead,
	status_code=201,
)
router.add_api_route(
	"/api/cash-transfers/{transfer_id}",
	update_cash_transfer,
	methods=["PATCH"],
	response_model=CashTransferApplyRead,
)
router.add_api_route(
	"/api/cash-transfers/{transfer_id}",
	delete_cash_transfer,
	methods=["DELETE"],
	status_code=204,
)
