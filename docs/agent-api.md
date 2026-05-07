# Agent API

This reference defines the HTTP contract for agent runtimes, scheduled jobs, and audited
automation that operate an OpenTraFi account. It covers every bearer-token-compatible operation
exposed by the backend, including account identity, portfolio mutation, feedback triage, and
release-note workflows.

Interactive browser login, registration, password reset, and logout remain part of the web
application session flow. This document is limited to bearer-token agent and developer access.

## Scope

- Supported clients: agent runtimes, CLI automation, and service integrations that act on behalf
  of a single app account.
- Supported auth context: `Authorization: Bearer <api_key>` for every authenticated call.
- Authoritative transport: HTTPS JSON over the REST endpoints listed below.

## Authentication

### Required Headers

| Header | Required | Applies To | Description |
| --- | --- | --- | --- |
| `Authorization: Bearer <api_key>` | Yes | Every authenticated route in this document | Account-scoped API key issued by the backend and validated on every request |
| `Agent-Name: <name>` | No | Any authenticated route | Non-empty values mark the request as agent traffic, update agent registration state, and attribute downstream audit rows to that agent |
| `Idempotency-Key: <unique_key>` | Recommended for supported create routes | `POST` create routes listed in [Idempotency](#idempotency) | Prevents duplicate side effects for retried calls |
| `Content-Type: application/json` | Yes for JSON request bodies | All `POST`, `PUT`, and `PATCH` routes with request bodies | JSON request encoding |

Notes:

- Omit `Agent-Name`, send it as an empty string, or send the literal string `false` to keep the
  request classified as a direct API call rather than an agent call.
- Agent registrations are keyed by account plus normalized non-empty `Agent-Name`. Distinct names
  become distinct registered agents.

### Register An Agent

To register or refresh an agent entry for the current account:

1. Create or reuse an active API key for that account.
2. Send the API key in `Authorization: Bearer <api_key>`.
3. Add a non-empty `Agent-Name: <name>` header to any authenticated request in this document.
4. Keep using the same `Agent-Name` value to continue attributing later activity to that agent.

Requests that omit `Agent-Name`, send it as an empty string, or send the literal string `false`
stay classified as direct API traffic and do not create or refresh an agent registration.

## Decimal Values

Financial values are deterministic fixed-precision decimals. Request bodies accept either JSON
numbers or decimal strings for amount, price, quantity, foreign-exchange rate, and return-rate
fields. The backend converts them to `Decimal` immediately. Read responses return financial values
as decimal strings so clients do not depend on binary floating-point behavior.

| Category | Request Type | Response Type | Examples |
| --- | --- | --- | --- |
| Money and prices | `number` or `string` | `string` | `"1000.00"`, `"188.50000000"` |
| Quantities | `number` or `string` | `string` | `"1200"`, `"0.12500000"` |
| Foreign-exchange rates | `number` or `string` | `string` | `"7.234500"` |
| Return percentages | `number` or `string` | `string` | `"-3.25"` |

Agents should prefer decimal strings in requests when exact reproducibility matters.

### API Key Lifecycle Endpoints

| Method | Path | Auth Context | Purpose |
| --- | --- | --- | --- |
| `POST` | `/api/agent/tokens` | Bearer API key | Create a new named API key for the current account |
| `GET` | `/api/agent/tokens` | Bearer API key | List existing API keys for the current account |
| `DELETE` | `/api/agent/tokens/{token_id}` | Bearer API key | Revoke an API key |

### `POST /api/agent/tokens`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | `string` | Yes | Human-readable API key name; must be unique among active keys on the same account |
| `expires_in_days` | `integer` | No | API key lifetime in days; omit for a non-expiring key |

Notes:

- The backend stores only a one-way digest plus a short key preview such as `sk-ab***********`.
- The full API key is returned exactly once in the create response.
- Issued API keys use the `sk-` prefix.
- Each account may hold up to five active API keys at the same time.
- Each account may create up to ten API keys per server day.
- API key self-service requires an already valid API key for the same account.

### Example: Create An API Key For The Current Account

```bash
curl -X POST http://127.0.0.1:80/api/agent/tokens \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <existing_api_key>' \
  -d '{
    "name": "local-cli"
  }'
```

### Example: Verify A Newly Issued API Key

```bash
curl http://127.0.0.1:80/api/auth/session \
  -H 'Authorization: Bearer <access_token>'
```

Expected response:

```json
{
  "user_id": "tester",
  "email": "tester@example.com"
}
```

## Route Catalog

### Account Identity

| Method | Path | Purpose | Notes |
| --- | --- | --- | --- |
| `GET` | `/api/auth/session` | Resolve the current authenticated account | Returns the caller identity represented by the supplied API key |
| `PATCH` | `/api/auth/email` | Update the current account email | Updates the email for the authenticated account |

### Agent Coordination And Health

| Method | Path | Purpose | Notes |
| --- | --- | --- | --- |
| `GET` | `/api/health` | Service availability probe | Unauthenticated health check |
| `GET` | `/api/agent/context` | Agent workspace summary | Returns portfolio summary, holdings, cash accounts, recent transactions, warnings, and pending sync count |
| `GET` | `/api/agent/registrations` | Agent registration inventory | Admin may pass `include_all_users=true` to inspect all accounts |
| `GET` | `/api/agent/tasks` | Agent task history | Lists structured task envelopes and execution results |
| `POST` | `/api/agent/tasks` | Create an agent task | Enqueues a validated task envelope for asynchronous execution |

### Portfolio Projections, Audit, And Search

| Method | Path | Purpose | Notes |
| --- | --- | --- | --- |
| `GET` | `/api/dashboard` | Full dashboard projection | Returns timeline series, summaries, and derived analytics |
| `GET` | `/api/dashboard/corrections` | List manual dashboard corrections | Lists explicit time-bucket corrections |
| `POST` | `/api/dashboard/corrections` | Create a dashboard correction | Inserts a manual correction for a dashboard series bucket |
| `DELETE` | `/api/dashboard/corrections/{correction_id}` | Delete a dashboard correction | Removes a previously recorded correction |
| `GET` | `/api/asset-records` | Immutable asset record history | Supports asset-class, operation-kind, and source filters |
| `GET` | `/api/audit-log` | Mutation audit history | Supports filtering by `agent_task_id` |
| `GET` | `/api/securities/search` | Symbol discovery | Searches supported market-data instruments |
| `GET` | `/api/securities/quote` | Latest quote lookup | Returns cached or live market data for one symbol |

### Cash Accounts, Ledger, And Transfers

| Method | Path | Purpose | Notes |
| --- | --- | --- | --- |
| `GET` | `/api/accounts` | List cash accounts | Returns all cash accounts for the current user |
| `POST` | `/api/accounts` | Create a cash account | Creates an account and its baseline ledger state |
| `PUT` | `/api/accounts/{account_id}` | Update a cash account | Reconciles the baseline ledger entry when balance changes |
| `DELETE` | `/api/accounts/{account_id}` | Delete a cash account | Allowed only when the account has no non-baseline ledger activity |
| `GET` | `/api/cash-ledger` | List cash-ledger entries | Supports `account_id` and `limit` |
| `POST` | `/api/cash-ledger/adjustments` | Create a manual cash correction | Appends a `MANUAL_ADJUSTMENT` ledger entry |
| `PATCH` | `/api/cash-ledger/adjustments/{entry_id}` | Update a manual cash correction | Only valid for manual adjustment rows |
| `DELETE` | `/api/cash-ledger/adjustments/{entry_id}` | Delete a manual cash correction | Rolls back the adjustment effect |
| `GET` | `/api/cash-transfers` | List cash transfers | Supports `limit` |
| `POST` | `/api/cash-transfers` | Create a cash transfer | Writes paired ledger effects between two accounts |
| `PATCH` | `/api/cash-transfers/{transfer_id}` | Update a cash transfer | Replays both sides of the transfer |
| `DELETE` | `/api/cash-transfers/{transfer_id}` | Delete a cash transfer | Rolls back paired ledger effects |

### Holdings And Trading

| Method | Path | Purpose | Notes |
| --- | --- | --- | --- |
| `GET` | `/api/holdings` | List current holdings | Metadata read only |
| `PUT` | `/api/holdings/{holding_id}` | Update holding metadata | Valid for note, broker, and metadata-only edits |
| `DELETE` | `/api/holdings/{holding_id}` | Delete a holding | Removes the holding, its transaction history, and linked sell-side cash effects |
| `GET` | `/api/holdings/{holding_id}/transactions` | List transactions for one holding | Per-holding transaction history |
| `GET` | `/api/holding-transactions` | List all holding transactions | Supports symbol, market, side, and limit filters |
| `POST` | `/api/holding-transactions` | Create a buy or sell transaction | Source of truth for security position changes |
| `PATCH` | `/api/holding-transactions/{transaction_id}` | Update a transaction | Replays holding projection and linked cash settlement effects |
| `DELETE` | `/api/holding-transactions/{transaction_id}` | Delete a transaction | Rolls back the transaction and linked cash effects |

### Other Asset Classes

| Method | Path | Purpose | Notes |
| --- | --- | --- | --- |
| `GET` | `/api/fixed-assets` | List fixed assets | Non-security asset inventory |
| `POST` | `/api/fixed-assets` | Create a fixed asset | Creates a manual fixed-asset entry |
| `PUT` | `/api/fixed-assets/{asset_id}` | Update a fixed asset | Replaces the current fixed-asset snapshot |
| `DELETE` | `/api/fixed-assets/{asset_id}` | Delete a fixed asset | Removes the fixed-asset entry |
| `GET` | `/api/liabilities` | List liabilities | Liability inventory |
| `POST` | `/api/liabilities` | Create a liability | Creates a liability entry |
| `PUT` | `/api/liabilities/{entry_id}` | Update a liability | Replaces the current liability snapshot |
| `DELETE` | `/api/liabilities/{entry_id}` | Delete a liability | Removes the liability entry |
| `GET` | `/api/other-assets` | List other assets | Generic non-security asset inventory |
| `POST` | `/api/other-assets` | Create another asset entry | Creates a manual other-asset entry |
| `PUT` | `/api/other-assets/{asset_id}` | Update another asset entry | Replaces the current snapshot |
| `DELETE` | `/api/other-assets/{asset_id}` | Delete another asset entry | Removes the asset entry |

### Feedback, Inbox, And Triage

| Method | Path | Purpose | Notes |
| --- | --- | --- | --- |
| `POST` | `/api/feedback` | Create a feedback or system message | Non-admin callers are normalized to user feedback; admin callers may create system items |
| `GET` | `/api/feedback` | List visible feedback for the current user | Hidden messages are excluded |
| `POST` | `/api/feedback/mark-seen` | Mark replied feedback as seen | Applies to visible replied user feedback items |
| `GET` | `/api/feedback/summary` | Resolve inbox badge state | Returns unread or open counts depending on caller role |
| `POST` | `/api/messages/hide` | Hide one inbox message for the current user | Supports feedback and release-note message kinds |
| `GET` | `/api/admin/feedback` | List all feedback rows for administrators | Unpaginated raw admin feed |
| `GET` | `/api/admin/feedback/user` | List paginated user feedback for administrators | Supports paging, filters, and optional hidden-item inclusion |
| `GET` | `/api/admin/feedback/system` | List paginated system feedback for administrators | Supports paging, filters, and optional hidden-item inclusion |
| `POST` | `/api/admin/feedback/{feedback_id}/reply` | Reply to one feedback item | Admin only; system items cannot be replied to |
| `POST` | `/api/admin/feedback/{feedback_id}/close` | Close one feedback item | Admin only |
| `POST` | `/api/admin/feedback/{feedback_id}/ack` | Acknowledge and assign one feedback item | Admin only; valid only while the item remains open |
| `POST` | `/api/admin/feedback/{feedback_id}/classify` | Update admin triage fields | Admin only |

### Release Note Operations

| Method | Path | Purpose | Notes |
| --- | --- | --- | --- |
| `GET` | `/api/admin/release-notes` | List release-note drafts and published entries | Admin only |
| `POST` | `/api/admin/release-notes` | Create a release-note draft | Admin only |
| `POST` | `/api/admin/release-notes/publish-changelog` | Upsert and publish a changelog-backed release note | Admin only |
| `POST` | `/api/admin/release-notes/{release_note_id}/publish` | Publish an existing release-note draft | Admin only |
| `GET` | `/api/release-notes` | Read the current release-note stream for the caller | Returns the visible aggregated update stream |
| `POST` | `/api/release-notes/mark-seen` | Mark visible release notes as seen | No request body |

## Query Parameter Reference

| Endpoint | Parameter | Type | Required | Default | Description |
| --- | --- | --- | --- | --- | --- |
| `GET /api/admin/feedback/user` | `page` | `integer` | No | `1` | One-based page index |
| `GET /api/admin/feedback/user` | `page_size` | `integer` | No | `50` | Maximum items returned per page |
| `GET /api/admin/feedback/user` | `status` | `string` | No | None | Comma-separated feedback status filter |
| `GET /api/admin/feedback/user` | `priority` | `string` | No | None | Comma-separated priority filter |
| `GET /api/admin/feedback/user` | `include_hidden` | `boolean` | No | `false` | Includes previously hidden feedback rows in the result set |
| `GET /api/admin/feedback/system` | `page` | `integer` | No | `1` | One-based page index |
| `GET /api/admin/feedback/system` | `page_size` | `integer` | No | `50` | Maximum items returned per page |
| `GET /api/admin/feedback/system` | `status` | `string` | No | None | Comma-separated feedback status filter |
| `GET /api/admin/feedback/system` | `priority` | `string` | No | None | Comma-separated priority filter |
| `GET /api/admin/feedback/system` | `include_hidden` | `boolean` | No | `false` | Includes previously hidden feedback rows in the result set |
| `GET /api/agent/context` | `refresh` | `boolean` | No | `false` | Forces a dashboard refresh before the context is assembled |
| `GET /api/agent/context` | `transaction_limit` | `integer` | No | `50` | Maximum recent holding transactions returned in the context payload |
| `GET /api/agent/registrations` | `include_all_users` | `boolean` | No | `false` | Admin-only cross-account registration view |
| `GET /api/agent/tasks` | `limit` | `integer` | No | `50` | Maximum tasks returned |
| `GET /api/dashboard` | `refresh` | `boolean` | No | `false` | Forces a dashboard rebuild before responding |
| `GET /api/cash-ledger` | `account_id` | `integer` | No | None | Restricts the ledger to a single account |
| `GET /api/cash-ledger` | `limit` | `integer` | No | `200` | Maximum ledger entries returned |
| `GET /api/cash-transfers` | `limit` | `integer` | No | `100` | Maximum transfers returned |
| `GET /api/holding-transactions` | `symbol` | `string` | No | None | Restricts results to one security symbol |
| `GET /api/holding-transactions` | `market` | `string` | No | None | Restricts results to one market code |
| `GET /api/holding-transactions` | `side` | `string` | No | None | Restricts results to `BUY` or `SELL` |
| `GET /api/holding-transactions` | `limit` | `integer` | No | `100` | Maximum transactions returned |
| `GET /api/asset-records` | `limit` | `integer` | No | `200` | Maximum immutable records returned |
| `GET /api/asset-records` | `asset_class` | `string` | No | None | Filters records by asset-class code |
| `GET /api/asset-records` | `operation_kind` | `string` | No | None | Filters records by mutation kind |
| `GET /api/asset-records` | `source` | `string` | No | None | Filters records by source, for example `AGENT` |
| `GET /api/audit-log` | `limit` | `integer` | No | `200` | Maximum audit rows returned |
| `GET /api/audit-log` | `agent_task_id` | `integer` | No | None | Restricts the audit log to one agent task |
| `GET /api/securities/search` | `q` | `string` | Yes | None | Search term for symbol lookup |
| `GET /api/securities/quote` | `symbol` | `string` | Yes | None | Security symbol |
| `GET /api/securities/quote` | `market` | `string` | Yes | None | Market code used by the quote provider |

## Request Body Reference

### `PATCH /api/auth/email`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `email` | `string` | Yes | New account email address |

### `POST /api/agent/tasks`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `task_type` | `string` | Yes | Enumerated task type |
| `payload` | `object` | No | Task payload matching the selected `task_type` |

Supported `task_type` values:

| `task_type` | Expected Payload |
| --- | --- |
| `CREATE_BUY_TRANSACTION` | Fields from [`POST /api/holding-transactions`](#post-apiholding-transactions-and-patch-apiholding-transactionstransaction_id) |
| `CREATE_SELL_TRANSACTION` | Fields from [`POST /api/holding-transactions`](#post-apiholding-transactions-and-patch-apiholding-transactionstransaction_id) |
| `UPDATE_HOLDING_TRANSACTION` | Fields from [`PATCH /api/holding-transactions/{transaction_id}`](#post-apiholding-transactions-and-patch-apiholding-transactionstransaction_id), plus the target transaction ID in the task payload contract |
| `CREATE_CASH_TRANSFER` | Fields from [`POST /api/cash-transfers`](#post-apicash-transfers-and-patch-apicash-transferstransfer_id) |
| `UPDATE_CASH_TRANSFER` | Fields from [`PATCH /api/cash-transfers/{transfer_id}`](#post-apicash-transfers-and-patch-apicash-transferstransfer_id), plus the target transfer ID in the task payload contract |
| `CREATE_CASH_LEDGER_ADJUSTMENT` | Fields from [`POST /api/cash-ledger/adjustments`](#post-apicash-ledgeradjustments-and-patch-apicash-ledgeradjustmentsentry_id) |
| `UPDATE_CASH_LEDGER_ADJUSTMENT` | Fields from [`PATCH /api/cash-ledger/adjustments/{entry_id}`](#post-apicash-ledgeradjustments-and-patch-apicash-ledgeradjustmentsentry_id), plus the target entry ID in the task payload contract |
| `DELETE_CASH_LEDGER_ADJUSTMENT` | The target entry ID for a manual adjustment |

### `POST /api/feedback`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `message` | `string` | Yes | Feedback body or system message body |
| `category` | `string` | No | Admin callers may select a category; non-admin callers are normalized to `USER_REQUEST` |
| `priority` | `string` | No | Admin callers may select a priority; non-admin callers are normalized to `MEDIUM` |
| `source` | `string` | No | Admin callers may identify the source, for example `SYSTEM` or `API_MONITOR` |
| `fingerprint` | `string` | No | Admin-only dedupe key for repeated system events |
| `dedupe_window_minutes` | `integer` | No | Admin-only dedupe window for repeated system events |

`POST /api/feedback/mark-seen` has no request body. It marks all visible replied feedback items
for the current user as seen.

### `POST /api/messages/hide`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `message_kind` | `string` | Yes | Inbox message type: `FEEDBACK` or `RELEASE_NOTE` |
| `message_id` | `integer` | Yes | Feedback ID or release-note delivery ID to hide |

### `POST /api/admin/feedback/{feedback_id}/reply`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `reply_message` | `string` | Yes | Admin reply body |
| `close` | `boolean` | No | Closes the item after replying when `true` |

`POST /api/admin/feedback/{feedback_id}/close` has no request body.

### `POST /api/admin/feedback/{feedback_id}/ack`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `assignee` | `string` | No | Assigned operator |
| `ack_deadline` | `string` | No | Acknowledgement or handling deadline in ISO 8601 format |
| `internal_note` | `string` | No | Internal triage note |

### `POST /api/admin/feedback/{feedback_id}/classify`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `category` | `string` | No | Feedback category |
| `priority` | `string` | No | Feedback priority |
| `source` | `string` | No | Feedback source |
| `status` | `string` | No | Feedback workflow status |
| `assignee` | `string` | No | Assigned operator |
| `ack_deadline` | `string` | No | Handling deadline in ISO 8601 format |
| `internal_note` | `string` | No | Internal triage note |

### `POST /api/dashboard/corrections`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `series_scope` | `string` | Yes | Target series family, for example portfolio total or holding return |
| `symbol` | `string` | No | Required when the correction targets a single security series |
| `granularity` | `string` | Yes | Bucket granularity, such as hourly or daily |
| `bucket_utc` | `string` | Yes | Target bucket timestamp in UTC |
| `action` | `string` | Yes | Correction action to apply |
| `corrected_value` | `number` or `string` | No | Replacement decimal value when the action requires one |
| `reason` | `string` | Yes | Human-readable audit reason |

### `POST /api/accounts` And `PUT /api/accounts/{account_id}`

| Field | Type | Required On Create | Allowed On Update | Description |
| --- | --- | --- | --- | --- |
| `name` | `string` | Yes | Yes | Account display name |
| `platform` | `string` | Yes | Yes | Broker, bank, or platform name |
| `currency` | `string` | No | Yes | Account currency; defaults to the backend standard when omitted |
| `balance` | `number` or `string` | Yes | Yes | Current account balance |
| `account_type` | `string` | No | Yes | Optional account classification |
| `started_on` | `string` | No | Yes | Account inception date |
| `note` | `string` | No | Yes | Free-form metadata |

### `POST /api/cash-ledger/adjustments` And `PATCH /api/cash-ledger/adjustments/{entry_id}`

| Field | Type | Required On Create | Allowed On Update | Description |
| --- | --- | --- | --- | --- |
| `cash_account_id` | `integer` | Yes | No | Target cash account |
| `amount` | `number` or `string` | Yes | Yes | Signed adjustment amount |
| `happened_on` | `string` | Yes | Yes | Effective date |
| `note` | `string` | No | Yes | Audit note |

### `POST /api/cash-transfers` And `PATCH /api/cash-transfers/{transfer_id}`

| Field | Type | Required On Create | Allowed On Update | Description |
| --- | --- | --- | --- | --- |
| `from_account_id` | `integer` | Yes | Yes | Source cash account |
| `to_account_id` | `integer` | Yes | Yes | Destination cash account |
| `source_amount` | `number` or `string` | Yes | Yes | Amount deducted from the source account |
| `target_amount` | `number` or `string` | No | Yes | Optional destination amount for FX transfers |
| `transferred_on` | `string` | Yes | Yes | Effective transfer date |
| `note` | `string` | No | Yes | Audit note |

### `PUT /api/holdings/{holding_id}`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `quantity` | `number` or `string` | No | Correction field; prefer transaction edits for quantity changes |
| `cost_basis_price` | `number` or `string` | No | Correction field; prefer transaction edits for cost changes |
| `started_on` | `string` | No | Correction field; prefer transaction edits for date changes |
| `broker` | `string` | No | Holding metadata |
| `note` | `string` | No | Holding metadata |

`PUT /api/holdings/{holding_id}` is intended for metadata-only edits. For quantity, cost, or trade-date
corrections, use `PATCH /api/holding-transactions/{transaction_id}`.

### `POST /api/holding-transactions` And `PATCH /api/holding-transactions/{transaction_id}`

| Field | Type | Required On Create | Allowed On Update | Description |
| --- | --- | --- | --- | --- |
| `side` | `string` | No | No | Trade side; the backend infers semantics from the create path and payload rules |
| `symbol` | `string` | Yes | No | Security symbol |
| `name` | `string` | Yes | Yes | Security display name |
| `quantity` | `number` or `string` | Yes | Yes | Trade quantity |
| `price` | `number` or `string` | No | Yes | Per-unit trade price |
| `fallback_currency` | `string` | No | Yes | Currency used when quote metadata is unavailable |
| `market` | `string` | No | No | Market code |
| `broker` | `string` | No | Yes | Broker label |
| `traded_on` | `string` | Yes | Yes | Trade date |
| `note` | `string` | No | Yes | Audit note |
| `sell_proceeds_handling` | `string` | No | Yes | Sell-side cash settlement rule |
| `sell_proceeds_account_id` | `integer` | No | Yes | Cash account that receives sell proceeds |
| `buy_funding_handling` | `string` | No | Yes | Buy-side funding rule |
| `buy_funding_account_id` | `integer` | No | Yes | Cash account that funds a buy |

### `POST /api/fixed-assets` And `PUT /api/fixed-assets/{asset_id}`

| Field | Type | Required On Create | Allowed On Update | Description |
| --- | --- | --- | --- | --- |
| `name` | `string` | Yes | Yes | Asset name |
| `category` | `string` | No | Yes | Asset category |
| `current_value_cny` | `number` or `string` | Yes | Yes | Current value in CNY |
| `started_on` | `string` | No | Yes | Effective start date |
| `note` | `string` | No | Yes | Metadata note |
| `purchase_value_cny` | `number` or `string` | No | Yes | Original purchase value in CNY |

### `POST /api/liabilities` And `PUT /api/liabilities/{entry_id}`

| Field | Type | Required On Create | Allowed On Update | Description |
| --- | --- | --- | --- | --- |
| `name` | `string` | Yes | Yes | Liability name |
| `category` | `string` | No | Yes | Liability category |
| `currency` | `string` | No | Yes | Liability currency |
| `balance` | `number` or `string` | Yes | Yes | Current liability balance |
| `started_on` | `string` | No | Yes | Effective start date |
| `note` | `string` | No | Yes | Metadata note |

### `POST /api/other-assets` And `PUT /api/other-assets/{asset_id}`

| Field | Type | Required On Create | Allowed On Update | Description |
| --- | --- | --- | --- | --- |
| `name` | `string` | Yes | Yes | Asset name |
| `category` | `string` | No | Yes | Asset category |
| `current_value_cny` | `number` or `string` | Yes | Yes | Current value in CNY |
| `started_on` | `string` | No | Yes | Effective start date |
| `note` | `string` | No | Yes | Metadata note |
| `original_value_cny` | `number` or `string` | No | Yes | Original booked value in CNY |

### `POST /api/admin/release-notes` And `POST /api/admin/release-notes/publish-changelog`

| Field | Type | Required On Draft Create | Required On Publish-Changelog | Description |
| --- | --- | --- | --- | --- |
| `version` | `string` | Yes | Yes | Semantic version in `x.y.z` format |
| `title` | `string` | Yes | Yes | Release-note title |
| `content` | `string` | Yes | Yes | Markdown-compatible release-note body |
| `source_feedback_ids` | `array<integer>` | No | No | Linked feedback IDs |
| `release_url` | `string` | No | No | Optional GitHub release URL appended during changelog publishing |

`POST /api/admin/release-notes/{release_note_id}/publish` and `POST /api/release-notes/mark-seen`
have no request body.

## Idempotency

The backend accepts `Idempotency-Key` on the following create routes:

| Method | Path | Behavior |
| --- | --- | --- |
| `POST` | `/api/holding-transactions` | Replays the original trade-creation response when the same key is reused with the same body |
| `POST` | `/api/cash-transfers` | Replays the original transfer-creation response when the same key is reused with the same body |
| `POST` | `/api/cash-ledger/adjustments` | Replays the original adjustment-creation response when the same key is reused with the same body |
| `POST` | `/api/agent/tasks` | Replays the original task-creation response when the same key is reused with the same body |

If the same idempotency key is reused with a different request body, the backend returns `409 Conflict`.

## Execution Semantics

- `POST /api/agent/tasks` returns the created task immediately with status `PENDING`.
- Task status progresses through `PENDING`, `RUNNING`, `DONE`, or `FAILED`.
- Background execution is performed by the dedicated `worker` process.
- Agent registrations are durable identities for runtime instances.
- Agent tokens are revocable credentials attached to a registration.
- Registration status is `ACTIVE` while at least one token remains usable; otherwise it becomes
  `INACTIVE`.
- Dashboard reads remain read-only. Projection rebuild jobs are not executed inline by
  `GET /api/dashboard`.

## Common Status Codes

| Status | Meaning |
| --- | --- |
| `200` | Successful read, update, or synchronous delete acknowledgement |
| `201` | Successful create |
| `204` | Successful delete with no response body |
| `401` | Authentication failed or the bearer token is invalid |
| `409` | Idempotency conflict or domain-level conflict |
| `422` | Request validation failed |

## Minimal Call Patterns

### Open A Trading Session

1. `GET /api/agent/context`
2. `GET /api/securities/search`
3. `GET /api/securities/quote`
4. `POST /api/holding-transactions`
5. `GET /api/agent/context`

### Move Cash Between Accounts

1. `GET /api/accounts`
2. `GET /api/cash-ledger`
3. `POST /api/cash-transfers`
4. `GET /api/agent/context`

### Review A Completed Agent Task

1. `POST /api/agent/tasks`
2. `GET /api/agent/tasks`
3. `GET /api/audit-log?agent_task_id=<task_id>`
4. `GET /api/asset-records?source=AGENT`

### Triage Admin Feedback

1. `GET /api/admin/feedback/system?page=1&page_size=50`
2. `POST /api/admin/feedback/{feedback_id}/ack`
3. `POST /api/admin/feedback/{feedback_id}/classify`
4. `POST /api/admin/feedback/{feedback_id}/close`

### Publish A Release Update

1. `GET /api/admin/release-notes`
2. `POST /api/admin/release-notes/publish-changelog`
3. `GET /api/release-notes`
4. `POST /api/release-notes/mark-seen`

## Examples

### Create A Buy Transaction

```bash
curl -X POST http://127.0.0.1:80/api/holding-transactions \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <access_token>' \
  -H 'Idempotency-Key: buy-aapl-20260324-001' \
  -d '{
    "symbol": "AAPL",
    "name": "Apple",
    "quantity": "2",
    "price": "188.50000000",
    "fallback_currency": "USD",
    "market": "US",
    "broker": "Futu",
    "traded_on": "2026-03-24",
    "note": "agent buy",
    "buy_funding_handling": "DEDUCT_FROM_EXISTING_CASH",
    "buy_funding_account_id": 3
  }'
```

### Create A Cash Transfer

```bash
curl -X POST http://127.0.0.1:80/api/cash-transfers \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <access_token>' \
  -H 'Idempotency-Key: transfer-20260324-001' \
  -d '{
    "from_account_id": 3,
    "to_account_id": 9,
    "source_amount": "500.00",
    "transferred_on": "2026-03-24",
    "note": "rebalance broker cash"
  }'
```

### Create A Dashboard Correction

```bash
curl -X POST http://127.0.0.1:80/api/dashboard/corrections \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <access_token>' \
  -d '{
    "series_scope": "PORTFOLIO_TOTAL",
    "granularity": "DAY",
    "bucket_utc": "2026-03-24T00:00:00Z",
    "action": "SET_VALUE",
    "corrected_value": 103284.12,
    "reason": "Reconcile an imported gap after broker backfill"
  }'
```

### Create A Liability Entry

```bash
curl -X POST http://127.0.0.1:80/api/liabilities \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <access_token>' \
  -d '{
    "name": "Mortgage",
    "category": "HOUSING",
    "currency": "CNY",
    "balance": 820000,
    "started_on": "2025-11-01",
    "note": "Primary residence mortgage"
  }'
```

### Submit A System Feedback Message

```bash
curl -X POST http://127.0.0.1:80/api/feedback \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <access_token>' \
  -H 'Agent-Name: api-monitor-bot' \
  -d '{
    "message": "[SYSTEM] API latency exceeded 5 minutes",
    "category": "SYSTEM_ALERT",
    "priority": "HIGH",
    "source": "API_MONITOR",
    "fingerprint": "api-monitor-latency",
    "dedupe_window_minutes": 60
  }'
```

### Publish A Changelog-Backed Release Note

```bash
curl -X POST http://127.0.0.1:80/api/admin/release-notes/publish-changelog \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <access_token>' \
  -H 'Agent-Name: release-bot' \
  -d '{
    "version": "0.7.1",
    "title": "Chart Comparison and Agent API Updates",
    "content": "- Restored the default full-range comparison values in both trend charts\\n- Expanded the agent API reference with formal parameter tables and examples",
    "source_feedback_ids": [3, 4],
    "release_url": "https://github.com/example/opentrifi/releases/tag/v0.7.1"
  }'
```

## Secret Handling

- Treat the returned API key as a write-capable secret. Store it in a secret manager, keychain, or
  environment variable immediately after creation.
- The full API key is intentionally not returned by `GET /api/agent/tokens`; only the masked hint
  remains available for later administration.
- Do not send broker passwords, broker API secrets, or unrelated credentials through asset and
  transaction routes.

The backend is a portfolio and transaction system. Broker execution, broker credential vaulting, and
broker-specific trade adapters should remain separate integration concerns with their own security
controls and audit boundaries.
