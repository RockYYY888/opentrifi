import type {
	CashAccountRecord,
	FixedAssetRecord,
	HoldingRecord,
	HoldingTransactionRecord,
	LiabilityRecord,
	OtherAssetRecord,
	SupportedCurrency,
} from "../types/assets";
import { EMPTY_DASHBOARD, type DashboardResponse } from "../types/dashboard";
import type {
	AllocationSlice,
	HoldingReturnSeries,
	TimelinePoint,
	ValuedCashAccount,
	ValuedFixedAsset,
	ValuedHolding,
	ValuedLiability,
	ValuedOtherAsset,
} from "../types/portfolioAnalytics";
import { formatCny } from "../utils/portfolioAnalytics";

const DASHBOARD_CACHE_SCHEMA_VERSION = 1;
const DASHBOARD_CACHE_KEY_PREFIX = "asset-tracker-dashboard-cache:";

export type DashboardCacheSnapshot = {
	schemaVersion: number;
	dashboard: DashboardResponse;
	lastUpdatedAt: string | null;
};

export type AssetManagerSeeds = {
	cashAccounts: CashAccountRecord[];
	holdings: HoldingRecord[];
	fixedAssets: FixedAssetRecord[];
	liabilities: LiabilityRecord[];
	otherAssets: OtherAssetRecord[];
};

function toFiniteNumber(value: unknown, fallbackValue = 0): number {
	return typeof value === "number" && Number.isFinite(value) ? value : fallbackValue;
}

function toNullableFiniteNumber(value: unknown): number | null {
	return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function toObjectArray<T extends object>(value: unknown): T[] {
	if (!Array.isArray(value)) {
		return [];
	}

	return value.filter((item): item is T => item !== null && typeof item === "object");
}

function toStringArray(value: unknown): string[] {
	if (!Array.isArray(value)) {
		return [];
	}

	return value.filter((item): item is string => typeof item === "string");
}

function sanitizeCachedDashboard(value: unknown): DashboardResponse | null {
	if (!value || typeof value !== "object") {
		return null;
	}

	const dashboard = value as Record<string, unknown>;
	return {
		...EMPTY_DASHBOARD,
		server_today: typeof dashboard.server_today === "string" ? dashboard.server_today : "",
		total_value_cny: toFiniteNumber(dashboard.total_value_cny),
		cash_value_cny: toFiniteNumber(dashboard.cash_value_cny),
		holdings_value_cny: toFiniteNumber(dashboard.holdings_value_cny),
		fixed_assets_value_cny: toFiniteNumber(dashboard.fixed_assets_value_cny),
		liabilities_value_cny: toFiniteNumber(dashboard.liabilities_value_cny),
		other_assets_value_cny: toFiniteNumber(dashboard.other_assets_value_cny),
		usd_cny_rate: toNullableFiniteNumber(dashboard.usd_cny_rate),
		hkd_cny_rate: toNullableFiniteNumber(dashboard.hkd_cny_rate),
		cash_accounts: toObjectArray<ValuedCashAccount>(dashboard.cash_accounts),
		holdings: toObjectArray<ValuedHolding>(dashboard.holdings),
		fixed_assets: toObjectArray<ValuedFixedAsset>(dashboard.fixed_assets),
		liabilities: toObjectArray<ValuedLiability>(dashboard.liabilities),
		other_assets: toObjectArray<ValuedOtherAsset>(dashboard.other_assets),
		allocation: toObjectArray<AllocationSlice>(dashboard.allocation),
		second_series: toObjectArray<TimelinePoint>(dashboard.second_series),
		minute_series: toObjectArray<TimelinePoint>(dashboard.minute_series),
		hour_series: toObjectArray<TimelinePoint>(dashboard.hour_series),
		day_series: toObjectArray<TimelinePoint>(dashboard.day_series),
		month_series: toObjectArray<TimelinePoint>(dashboard.month_series),
		year_series: toObjectArray<TimelinePoint>(dashboard.year_series),
		holdings_return_second_series: toObjectArray<TimelinePoint>(
			dashboard.holdings_return_second_series,
		),
		holdings_return_minute_series: toObjectArray<TimelinePoint>(
			dashboard.holdings_return_minute_series,
		),
		holdings_return_hour_series: toObjectArray<TimelinePoint>(
			dashboard.holdings_return_hour_series,
		),
		holdings_return_day_series: toObjectArray<TimelinePoint>(
			dashboard.holdings_return_day_series,
		),
		holdings_return_month_series: toObjectArray<TimelinePoint>(
			dashboard.holdings_return_month_series,
		),
		holdings_return_year_series: toObjectArray<TimelinePoint>(
			dashboard.holdings_return_year_series,
		),
		holding_return_series: toObjectArray<HoldingReturnSeries>(dashboard.holding_return_series),
		recent_holding_transactions: toObjectArray<HoldingTransactionRecord>(
			dashboard.recent_holding_transactions,
		),
		warnings: toStringArray(dashboard.warnings),
	};
}

function normalizeCachedTimestamp(value: unknown): string | null {
	if (typeof value !== "string") {
		return null;
	}

	return Number.isNaN(new Date(value).getTime()) ? null : value;
}

function toSupportedCurrency(value: string, fallback: SupportedCurrency = "CNY"): SupportedCurrency {
	return value === "USD" || value === "HKD" || value === "CNY" ? value : fallback;
}

function getDashboardCacheKey(userId: string): string {
	return `${DASHBOARD_CACHE_KEY_PREFIX}${userId}`;
}

export function readCachedDashboardSnapshot(userId: string): DashboardCacheSnapshot | null {
	if (typeof window === "undefined") {
		return null;
	}

	try {
		const rawValue =
			window.sessionStorage.getItem(getDashboardCacheKey(userId)) ??
			window.localStorage.getItem(getDashboardCacheKey(userId));
		if (!rawValue) {
			return null;
		}

		const parsedValue = JSON.parse(rawValue) as Partial<DashboardCacheSnapshot> | null;
		if (
			!parsedValue ||
			typeof parsedValue !== "object" ||
			!parsedValue.dashboard ||
			typeof parsedValue.dashboard !== "object"
		) {
			return null;
		}
		if (
			typeof parsedValue.schemaVersion === "number" &&
			parsedValue.schemaVersion !== DASHBOARD_CACHE_SCHEMA_VERSION
		) {
			return null;
		}
		const sanitizedDashboard = sanitizeCachedDashboard(parsedValue.dashboard);
		if (sanitizedDashboard === null) {
			return null;
		}

		return {
			schemaVersion: DASHBOARD_CACHE_SCHEMA_VERSION,
			dashboard: sanitizedDashboard,
			lastUpdatedAt: normalizeCachedTimestamp(parsedValue.lastUpdatedAt),
		};
	} catch {
		return null;
	}
}

export function writeCachedDashboardSnapshot(
	userId: string,
	dashboard: DashboardResponse,
	lastUpdatedAt: string | null,
): void {
	if (typeof window === "undefined") {
		return;
	}

	try {
		const serializedSnapshot = JSON.stringify({
			schemaVersion: DASHBOARD_CACHE_SCHEMA_VERSION,
			dashboard,
			lastUpdatedAt,
		} satisfies DashboardCacheSnapshot);
		window.sessionStorage.setItem(getDashboardCacheKey(userId), serializedSnapshot);
		window.localStorage.setItem(getDashboardCacheKey(userId), serializedSnapshot);
	} catch {
		// Ignore storage write failures and continue with in-memory state only.
	}
}

export function isDashboardSnapshotEmpty(dashboard: DashboardResponse): boolean {
	return (
		dashboard.total_value_cny === 0 &&
		dashboard.cash_value_cny === 0 &&
		dashboard.holdings_value_cny === 0 &&
		dashboard.fixed_assets_value_cny === 0 &&
		dashboard.other_assets_value_cny === 0 &&
		dashboard.liabilities_value_cny === 0
	);
}

export function getMillisecondsUntilNextMinute(): number {
	const now = new Date();
	return ((60 - now.getSeconds()) * 1000) - now.getMilliseconds();
}

export function getMillisecondsUntilNextSecond(): number {
	const now = new Date();
	return 1000 - now.getMilliseconds();
}

export function formatLastUpdated(timestamp: string | null): string {
	if (!timestamp) {
		return "等待首次载入";
	}

	const parsedTimestamp = new Date(timestamp);
	if (Number.isNaN(parsedTimestamp.getTime())) {
		return "等待首次载入";
	}

	return new Intl.DateTimeFormat("zh-CN", {
		month: "2-digit",
		day: "2-digit",
		hour: "2-digit",
		minute: "2-digit",
		second: "2-digit",
	}).format(parsedTimestamp);
}

export function formatSummaryCny(value: number): string {
	const absoluteValue = Math.abs(value);
	const sign = value < 0 ? "-" : "";

	if (absoluteValue < 10_000) {
		return formatCny(value);
	}

	if (absoluteValue < 100_000_000) {
		return `${sign}¥${(absoluteValue / 10_000).toFixed(2)}万`;
	}

	return `${sign}¥${(absoluteValue / 100_000_000).toFixed(2)}亿`;
}

export function formatFxRate(rate: number | null | undefined): string {
	if (rate === null || rate === undefined || !Number.isFinite(rate) || rate <= 0) {
		return "--";
	}

	return rate.toFixed(4);
}

export function toAssetManagerSeeds(dashboard: DashboardResponse): AssetManagerSeeds {
	return {
		cashAccounts: dashboard.cash_accounts.map((account) => ({
			id: account.id,
			name: account.name,
			platform: account.platform,
			currency: toSupportedCurrency(account.currency),
			balance: account.balance,
			account_type: account.account_type,
			started_on: account.started_on ?? undefined,
			note: account.note ?? undefined,
			fx_to_cny: account.fx_to_cny,
			value_cny: account.value_cny,
		})),
		holdings: dashboard.holdings.map((holding) => ({
			id: holding.id,
			side: "BUY",
			symbol: holding.symbol,
			name: holding.name,
			quantity: holding.quantity,
			fallback_currency: toSupportedCurrency(holding.fallback_currency),
			cost_basis_price: holding.cost_basis_price ?? undefined,
			market: holding.market,
			broker: holding.broker ?? undefined,
			started_on: holding.started_on ?? undefined,
			note: holding.note ?? undefined,
			price: holding.price,
			price_currency: holding.price_currency,
			value_cny: holding.value_cny,
			return_pct: holding.return_pct ?? undefined,
			last_updated: holding.last_updated,
		})),
		fixedAssets: dashboard.fixed_assets.map((asset) => ({
			id: asset.id,
			name: asset.name,
			category: asset.category,
			current_value_cny: asset.current_value_cny,
			purchase_value_cny: asset.purchase_value_cny ?? undefined,
			started_on: asset.started_on ?? undefined,
			note: asset.note ?? undefined,
			value_cny: asset.value_cny,
			return_pct: asset.return_pct ?? undefined,
		})),
		liabilities: dashboard.liabilities.map((entry) => ({
			id: entry.id,
			name: entry.name,
			category: entry.category,
			currency: toSupportedCurrency(entry.currency),
			balance: entry.balance,
			started_on: entry.started_on ?? undefined,
			note: entry.note ?? undefined,
			fx_to_cny: entry.fx_to_cny,
			value_cny: entry.value_cny,
		})),
		otherAssets: dashboard.other_assets.map((asset) => ({
			id: asset.id,
			name: asset.name,
			category: asset.category,
			current_value_cny: asset.current_value_cny,
			original_value_cny: asset.original_value_cny ?? undefined,
			started_on: asset.started_on ?? undefined,
			note: asset.note ?? undefined,
			value_cny: asset.value_cny,
			return_pct: asset.return_pct ?? undefined,
		})),
	};
}
