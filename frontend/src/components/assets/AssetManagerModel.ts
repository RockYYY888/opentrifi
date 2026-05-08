import type {
	CashAccountFormDraft,
	CashAccountInput,
	CashAccountRecord,
	FixedAssetFormDraft,
	FixedAssetInput,
	FixedAssetRecord,
	HoldingFormDraft,
	HoldingInput,
	HoldingRecord,
	HoldingTransactionRecord,
	LiabilityFormDraft,
	LiabilityInput,
	LiabilityRecord,
	OtherAssetFormDraft,
	OtherAssetInput,
	OtherAssetRecord,
} from "../../types/assets";

export type AssetSection = "cash" | "investment" | "fixed" | "liability" | "other";
export type AssetResource =
	| "cashAccounts"
	| "cashTransfers"
	| "cashLedger"
	| "holdings"
	| "holdingTransactions"
	| "fixedAssets"
	| "liabilities"
	| "otherAssets";

export type SummarySection = {
	key: AssetSection;
	label: string;
	count: number | string;
};

export const ACTIVE_SECTION_STORAGE_KEY = "asset-manager-active-section";
export const SECTION_RESOURCES: Record<AssetSection, AssetResource[]> = {
	cash: ["cashAccounts"],
	investment: ["cashAccounts", "holdings", "holdingTransactions"],
	fixed: ["fixedAssets"],
	liability: ["liabilities"],
	other: ["otherAssets"],
};

export const EMPTY_CASH_ACCOUNTS: CashAccountRecord[] = [];
export const EMPTY_HOLDINGS: HoldingRecord[] = [];
export const EMPTY_HOLDING_TRANSACTIONS: HoldingTransactionRecord[] = [];
export const EMPTY_FIXED_ASSETS: FixedAssetRecord[] = [];
export const EMPTY_LIABILITIES: LiabilityRecord[] = [];
export const EMPTY_OTHER_ASSETS: OtherAssetRecord[] = [];

const EMPTY_LOADED_RESOURCES: Record<AssetResource, boolean> = {
	cashAccounts: false,
	cashTransfers: false,
	cashLedger: false,
	holdings: false,
	holdingTransactions: false,
	fixedAssets: false,
	liabilities: false,
	otherAssets: false,
};

export type AssetManagerInitialData = {
	initialCashAccounts?: CashAccountRecord[];
	initialHoldings?: HoldingRecord[];
	initialFixedAssets?: FixedAssetRecord[];
	initialLiabilities?: LiabilityRecord[];
	initialOtherAssets?: OtherAssetRecord[];
};

export function getLoadedResourcesFromInitialData(
	initialData: AssetManagerInitialData,
): Record<AssetResource, boolean> {
	return {
		...EMPTY_LOADED_RESOURCES,
		cashAccounts: initialData.initialCashAccounts !== undefined,
		holdings: initialData.initialHoldings !== undefined,
		fixedAssets: initialData.initialFixedAssets !== undefined,
		liabilities: initialData.initialLiabilities !== undefined,
		otherAssets: initialData.initialOtherAssets !== undefined,
	};
}

function isAssetSection(value: string): value is AssetSection {
	return value === "cash" ||
		value === "investment" ||
		value === "fixed" ||
		value === "liability" ||
		value === "other";
}

export function readInitialSection(defaultSection: AssetSection): AssetSection {
	if (typeof window === "undefined") {
		return defaultSection;
	}

	try {
		const storedSection = window.sessionStorage.getItem(ACTIVE_SECTION_STORAGE_KEY);
		return storedSection && isAssetSection(storedSection) ? storedSection : defaultSection;
	} catch {
		return defaultSection;
	}
}

export function toCashDraft(record: CashAccountRecord): CashAccountFormDraft {
	return {
		name: record.name,
		currency: record.currency,
		balance: String(record.balance),
		account_type: record.account_type,
		started_on: record.started_on ?? "",
		note: record.note ?? "",
	};
}

export function toHoldingDraft(record: HoldingRecord): HoldingFormDraft {
	return {
		side: "BUY",
		symbol: record.symbol,
		name: record.name,
		quantity: String(record.quantity),
		fallback_currency: record.fallback_currency,
		cost_basis_price: record.cost_basis_price != null ? String(record.cost_basis_price) : "",
		market: record.market,
		broker: record.broker ?? "",
		started_on: record.started_on ?? "",
		note: record.note ?? "",
		sell_proceeds_handling: "CREATE_NEW_CASH",
		sell_proceeds_account_id: "",
		buy_funding_handling: "",
		buy_funding_account_id: "",
	};
}

export function toFixedAssetDraft(record: FixedAssetRecord): FixedAssetFormDraft {
	return {
		name: record.name,
		category: record.category,
		current_value_cny: String(record.current_value_cny),
		purchase_value_cny: record.purchase_value_cny != null ? String(record.purchase_value_cny) : "",
		started_on: record.started_on ?? "",
		note: record.note ?? "",
	};
}

export function toLiabilityDraft(record: LiabilityRecord): LiabilityFormDraft {
	return {
		name: record.name,
		category: record.category,
		currency: record.currency,
		balance: String(record.balance),
		started_on: record.started_on ?? "",
		note: record.note ?? "",
	};
}

export function toOtherAssetDraft(record: OtherAssetRecord): OtherAssetFormDraft {
	return {
		name: record.name,
		category: record.category,
		current_value_cny: String(record.current_value_cny),
		original_value_cny: record.original_value_cny != null ? String(record.original_value_cny) : "",
		started_on: record.started_on ?? "",
		note: record.note ?? "",
	};
}

export function createLocalCashAccount(
	payload: CashAccountInput,
	nextId: number,
): CashAccountRecord {
	return {
		id: nextId,
		...payload,
		note: payload.note,
		value_cny: payload.currency === "CNY" ? payload.balance : 0,
		fx_to_cny: payload.currency === "CNY" ? 1 : null,
	};
}

export function updateLocalCashAccount(
	currentRecord: CashAccountRecord,
	payload: CashAccountInput,
): CashAccountRecord {
	return {
		...currentRecord,
		...payload,
		note: payload.note,
		value_cny: payload.currency === "CNY" ? payload.balance : currentRecord.value_cny ?? 0,
		fx_to_cny: payload.currency === "CNY" ? 1 : currentRecord.fx_to_cny ?? null,
	};
}

export function createLocalHolding(payload: HoldingInput, nextId: number): HoldingRecord | null {
	if (payload.side === "SELL") {
		return null;
	}

	return {
		id: nextId,
		side: payload.side,
		symbol: payload.symbol,
		name: payload.name,
		quantity: payload.quantity,
		fallback_currency: payload.fallback_currency,
		cost_basis_price: payload.cost_basis_price,
		market: payload.market,
		broker: payload.broker,
		started_on: payload.started_on,
		note: payload.note,
		price: null,
		price_currency: payload.fallback_currency,
		value_cny: 0,
		return_pct: null,
		last_updated: null,
	};
}

export function updateLocalHolding(
	currentRecord: HoldingRecord,
	payload: HoldingInput,
): HoldingRecord {
	return {
		...currentRecord,
		side: "BUY",
		symbol: payload.symbol,
		name: payload.name,
		quantity: payload.quantity,
		fallback_currency: payload.fallback_currency,
		cost_basis_price: payload.cost_basis_price,
		market: payload.market,
		broker: payload.broker,
		started_on: payload.started_on,
		note: payload.note,
		price_currency: currentRecord.price_currency ?? payload.fallback_currency,
	};
}

export function createLocalFixedAsset(
	payload: FixedAssetInput,
	nextId: number,
): FixedAssetRecord {
	return {
		id: nextId,
		...payload,
		purchase_value_cny: payload.purchase_value_cny,
		note: payload.note,
		value_cny: payload.current_value_cny,
		return_pct: payload.purchase_value_cny
			? Number(
				(
					((payload.current_value_cny - payload.purchase_value_cny) /
						payload.purchase_value_cny) *
					100
				).toFixed(2),
			)
			: null,
	};
}

export function updateLocalFixedAsset(
	currentRecord: FixedAssetRecord,
	payload: FixedAssetInput,
): FixedAssetRecord {
	return {
		...currentRecord,
		...payload,
		purchase_value_cny: payload.purchase_value_cny,
		note: payload.note,
		value_cny: payload.current_value_cny,
		return_pct: payload.purchase_value_cny
			? Number(
				(
					((payload.current_value_cny - payload.purchase_value_cny) /
						payload.purchase_value_cny) *
					100
				).toFixed(2),
			)
			: null,
	};
}

export function createLocalLiability(
	payload: LiabilityInput,
	nextId: number,
): LiabilityRecord {
	return {
		id: nextId,
		...payload,
		note: payload.note,
		value_cny: payload.currency === "CNY" ? payload.balance : 0,
		fx_to_cny: payload.currency === "CNY" ? 1 : null,
	};
}

export function updateLocalLiability(
	currentRecord: LiabilityRecord,
	payload: LiabilityInput,
): LiabilityRecord {
	return {
		...currentRecord,
		...payload,
		note: payload.note,
		value_cny: payload.currency === "CNY" ? payload.balance : currentRecord.value_cny ?? 0,
		fx_to_cny: payload.currency === "CNY" ? 1 : currentRecord.fx_to_cny ?? null,
	};
}

export function createLocalOtherAsset(
	payload: OtherAssetInput,
	nextId: number,
): OtherAssetRecord {
	return {
		id: nextId,
		...payload,
		original_value_cny: payload.original_value_cny,
		note: payload.note,
		value_cny: payload.current_value_cny,
		return_pct: payload.original_value_cny
			? Number(
				(
					((payload.current_value_cny - payload.original_value_cny) /
						payload.original_value_cny) *
					100
				).toFixed(2),
			)
			: null,
	};
}

export function updateLocalOtherAsset(
	currentRecord: OtherAssetRecord,
	payload: OtherAssetInput,
): OtherAssetRecord {
	return {
		...currentRecord,
		...payload,
		original_value_cny: payload.original_value_cny,
		note: payload.note,
		value_cny: payload.current_value_cny,
		return_pct: payload.original_value_cny
			? Number(
				(
					((payload.current_value_cny - payload.original_value_cny) /
						payload.original_value_cny) *
					100
				).toFixed(2),
			)
			: null,
	};
}
