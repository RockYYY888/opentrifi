from app.main import app


EXPECTED_API_ROUTE_METHODS = {
	("DELETE", "/api/accounts/{account_id}"),
	("DELETE", "/api/agent/tokens/{token_id}"),
	("DELETE", "/api/cash-ledger/adjustments/{entry_id}"),
	("DELETE", "/api/cash-transfers/{transfer_id}"),
	("DELETE", "/api/dashboard/corrections/{correction_id}"),
	("DELETE", "/api/fixed-assets/{asset_id}"),
	("DELETE", "/api/holding-transactions/{transaction_id}"),
	("DELETE", "/api/holdings/{holding_id}"),
	("DELETE", "/api/liabilities/{entry_id}"),
	("DELETE", "/api/other-assets/{asset_id}"),
	("GET", "/api/accounts"),
	("GET", "/api/asset-records"),
	("GET", "/api/admin/feedback"),
	("GET", "/api/admin/feedback/system"),
	("GET", "/api/admin/feedback/user"),
	("GET", "/api/admin/release-notes"),
	("GET", "/api/agent/context"),
	("GET", "/api/agent/registrations"),
	("GET", "/api/agent/tasks"),
	("GET", "/api/agent/tokens"),
	("GET", "/api/audit-log"),
	("GET", "/api/auth/session"),
	("GET", "/api/cash-ledger"),
	("GET", "/api/cash-transfers"),
	("GET", "/api/dashboard"),
	("GET", "/api/dashboard/corrections"),
	("GET", "/api/feedback"),
	("GET", "/api/feedback/summary"),
	("GET", "/api/fixed-assets"),
	("GET", "/api/health"),
	("GET", "/api/holding-transactions"),
	("GET", "/api/holdings"),
	("GET", "/api/holdings/{holding_id}/transactions"),
	("GET", "/api/liabilities"),
	("GET", "/api/other-assets"),
	("GET", "/api/release-notes"),
	("GET", "/api/securities/quote"),
	("GET", "/api/securities/search"),
	("PATCH", "/api/auth/email"),
	("PATCH", "/api/cash-ledger/adjustments/{entry_id}"),
	("PATCH", "/api/cash-transfers/{transfer_id}"),
	("PATCH", "/api/holding-transactions/{transaction_id}"),
	("POST", "/api/accounts"),
	("POST", "/api/agent/tokens/issue"),
	("POST", "/api/admin/feedback/{feedback_id}/ack"),
	("POST", "/api/admin/feedback/{feedback_id}/classify"),
	("POST", "/api/admin/feedback/{feedback_id}/close"),
	("POST", "/api/admin/feedback/{feedback_id}/reply"),
	("POST", "/api/admin/release-notes"),
	("POST", "/api/admin/release-notes/publish-changelog"),
	("POST", "/api/admin/release-notes/{release_note_id}/publish"),
	("POST", "/api/agent/tasks"),
	("POST", "/api/agent/tokens"),
	("POST", "/api/auth/login"),
	("POST", "/api/auth/logout"),
	("POST", "/api/auth/register"),
	("POST", "/api/auth/reset-password"),
	("POST", "/api/cash-ledger/adjustments"),
	("POST", "/api/cash-transfers"),
	("POST", "/api/dashboard/corrections"),
	("POST", "/api/feedback"),
	("POST", "/api/feedback/mark-seen"),
	("POST", "/api/fixed-assets"),
	("POST", "/api/holding-transactions"),
	("POST", "/api/liabilities"),
	("POST", "/api/messages/hide"),
	("POST", "/api/other-assets"),
	("POST", "/api/release-notes/mark-seen"),
	("PUT", "/api/accounts/{account_id}"),
	("PUT", "/api/fixed-assets/{asset_id}"),
	("PUT", "/api/holdings/{holding_id}"),
	("PUT", "/api/liabilities/{entry_id}"),
	("PUT", "/api/other-assets/{asset_id}"),
}


def test_api_route_contract_is_stable() -> None:
	actual_routes = {
		(method, route.path)
		for route in app.routes
		for method in getattr(route, "methods", set()) or set()
		if route.path.startswith("/api/") and method not in {"HEAD", "OPTIONS"}
	}

	assert actual_routes == EXPECTED_API_ROUTE_METHODS
	assert len(actual_routes) == 73


def test_openapi_paths_match_route_contract() -> None:
	openapi_schema = app.openapi()
	openapi_routes = {
		(method.upper(), path)
		for path, operations in openapi_schema["paths"].items()
		for method in operations.keys()
		if path.startswith("/api/")
	}

	assert openapi_routes == EXPECTED_API_ROUTE_METHODS
