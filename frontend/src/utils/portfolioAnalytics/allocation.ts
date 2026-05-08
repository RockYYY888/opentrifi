import {
	getCashAccountTypeLabel,
	getFixedAssetCategoryLabel,
	getLiabilityCategoryLabel,
	getOtherAssetCategoryLabel,
} from "../../types/assets";
import type {
	AllocationBreakdownGroup,
	AllocationBreakdownItem,
	AllocationSlice,
	BreakdownChartItem,
	ChartLegendItem,
	ValuedCashAccount,
	ValuedFixedAsset,
	ValuedHolding,
	ValuedLiability,
	ValuedOtherAsset,
} from "../../types/portfolioAnalytics";
import { getChartColors } from "./visualConfig";

function getColor(index: number): string {
	const chartColors = getChartColors();
	return chartColors[index % chartColors.length]!;
}

export function buildAllocationLegend(
	allocation: AllocationSlice[],
	totalValueCny: number,
): ChartLegendItem[] {
	const positiveAssetTotal = allocation.reduce((sum, slice) => sum + Math.max(slice.value, 0), 0);
	const denominator = positiveAssetTotal > 0 ? positiveAssetTotal : Math.max(totalValueCny, 0);

	return allocation
		.filter((slice) => slice.value > 0)
		.map((slice, index) => ({
			label: slice.label,
			value_cny: slice.value,
			percentage: denominator > 0 ? slice.value / denominator : 0,
			color: getColor(index),
		}));
}

type AllocationBreakdownSeed = {
	label: string;
	value_cny: number;
};

function aggregateBreakdownSeeds(
	items: AllocationBreakdownSeed[],
): AllocationBreakdownSeed[] {
	const groupedItems = new Map<string, number>();

	for (const item of items) {
		if (item.value_cny <= 0) {
			continue;
		}

		groupedItems.set(item.label, (groupedItems.get(item.label) ?? 0) + item.value_cny);
	}

	return [...groupedItems.entries()]
		.map(([label, value_cny]) => ({ label, value_cny }))
		.sort((left, right) => right.value_cny - left.value_cny);
}

function buildAllocationBreakdownItems(
	items: AllocationBreakdownSeed[],
	categoryTotal: number,
	positiveAssetTotal: number,
): AllocationBreakdownItem[] {
	return aggregateBreakdownSeeds(items).map((item, index) => ({
		label: item.label,
		value_cny: item.value_cny,
		category_percentage: categoryTotal > 0 ? item.value_cny / categoryTotal : 0,
		overall_percentage: positiveAssetTotal > 0 ? item.value_cny / positiveAssetTotal : 0,
		color: getColor(index),
	}));
}

function getCashAllocationLabel(account: ValuedCashAccount): string {
	const name = account.name.trim();
	if (name) {
		return name;
	}

	const platform = account.platform.trim();
	if (platform) {
		return platform;
	}

	return getCashAccountTypeLabel(account.account_type);
}

function getHoldingAllocationLabel(holding: ValuedHolding): string {
	return holding.name.trim() || holding.symbol.trim() || "未命名持仓";
}

function getFixedAssetAllocationLabel(asset: ValuedFixedAsset): string {
	return asset.name.trim() || getFixedAssetCategoryLabel(asset.category);
}

function getOtherAssetAllocationLabel(asset: ValuedOtherAsset): string {
	return asset.name.trim() || getOtherAssetCategoryLabel(asset.category);
}

export function buildAllocationBreakdownGroups(
	allocation: AllocationSlice[],
	totalValueCny: number,
	cashAccounts: ValuedCashAccount[],
	holdings: ValuedHolding[],
	fixedAssets: ValuedFixedAsset[],
	otherAssets: ValuedOtherAsset[],
): AllocationBreakdownGroup[] {
	const legendItems = buildAllocationLegend(allocation, totalValueCny);
	const positiveAssetTotal = legendItems.reduce((sum, item) => sum + item.value_cny, 0);
	const cashItems = cashAccounts
		.filter((account) => account.value_cny > 0)
		.map((account) => ({
			label: getCashAllocationLabel(account),
			value_cny: account.value_cny,
		}));
	const holdingItems = holdings
		.filter((holding) => holding.value_cny > 0)
		.map((holding) => ({
			label: getHoldingAllocationLabel(holding),
			value_cny: holding.value_cny,
		}));
	const fixedAssetItems = fixedAssets
		.filter((asset) => asset.value_cny > 0)
		.map((asset) => ({
			label: getFixedAssetAllocationLabel(asset),
			value_cny: asset.value_cny,
		}));
	const otherAssetItems = otherAssets
		.filter((asset) => asset.value_cny > 0)
		.map((asset) => ({
			label: getOtherAssetAllocationLabel(asset),
			value_cny: asset.value_cny,
		}));

	const groupedItemsByCategory = new Map<string, AllocationBreakdownSeed[]>([
		["现金", cashItems],
		["投资类", holdingItems],
		["固定资产", fixedAssetItems],
		["其他", otherAssetItems],
	]);

	return legendItems.map((item) => ({
		label: item.label,
		value_cny: item.value_cny,
		percentage: item.percentage,
		items: buildAllocationBreakdownItems(
			groupedItemsByCategory.get(item.label) ?? [],
			item.value_cny,
			positiveAssetTotal,
		),
	}));
}

export function buildHoldingsBreakdown(
	holdings: ValuedHolding[],
	limit = 5,
): BreakdownChartItem[] {
	const sortedHoldings = [...holdings]
		.filter((holding) => holding.value_cny > 0)
		.sort((left, right) => right.value_cny - left.value_cny);
	const totalHoldingsValue = sortedHoldings.reduce(
		(sum, holding) => sum + holding.value_cny,
		0,
	);

	if (totalHoldingsValue === 0) {
		return [];
	}

	const leadingItems = sortedHoldings.slice(0, limit).map((holding, index) => ({
		label: holding.name || holding.symbol,
		value_cny: holding.value_cny,
		percentage: holding.value_cny / totalHoldingsValue,
		color: getColor(index),
	}));
	const remainingValue = sortedHoldings
		.slice(limit)
		.reduce((sum, holding) => sum + holding.value_cny, 0);

	if (remainingValue <= 0) {
		return leadingItems;
	}

	return [
		...leadingItems,
		{
			label: "其余持仓",
			value_cny: remainingValue,
			percentage: remainingValue / totalHoldingsValue,
			color: getColor(leadingItems.length),
		},
	];
}

export function buildPlatformBreakdown(
	cashAccounts: ValuedCashAccount[],
	holdings: ValuedHolding[],
	fixedAssets: ValuedFixedAsset[],
	liabilities: ValuedLiability[],
	otherAssets: ValuedOtherAsset[],
): BreakdownChartItem[] {
	const platformTotals = new Map<string, number>();

	for (const account of cashAccounts) {
		const key = account.platform.trim() || "未命名平台";
		platformTotals.set(key, (platformTotals.get(key) ?? 0) + account.value_cny);
	}

	for (const holding of holdings) {
		if (holding.value_cny <= 0) {
			continue;
		}

		const key = holding.broker?.trim() || "投资类（未标记来源）";
		platformTotals.set(
			key,
			(platformTotals.get(key) ?? 0) + holding.value_cny,
		);
	}

	for (const asset of fixedAssets) {
		if (asset.value_cny <= 0) {
			continue;
		}

		const key = `固定资产 · ${getFixedAssetCategoryLabel(asset.category)}`;
		platformTotals.set(key, (platformTotals.get(key) ?? 0) + asset.value_cny);
	}

	for (const entry of liabilities) {
		if (entry.value_cny <= 0) {
			continue;
		}

		const key = `负债 · ${getLiabilityCategoryLabel(entry.category)}`;
		platformTotals.set(key, (platformTotals.get(key) ?? 0) + entry.value_cny);
	}

	for (const asset of otherAssets) {
		if (asset.value_cny <= 0) {
			continue;
		}

		const key = `其他 · ${getOtherAssetCategoryLabel(asset.category)}`;
		platformTotals.set(key, (platformTotals.get(key) ?? 0) + asset.value_cny);
	}

	const sortedEntries = [...platformTotals.entries()]
		.filter(([, value]) => value > 0)
		.sort((left, right) => right[1] - left[1]);
	const totalValue = sortedEntries.reduce((sum, [, value]) => sum + value, 0);

	return sortedEntries.map(([label, value], index) => ({
		label,
		value_cny: value,
		percentage: totalValue > 0 ? value / totalValue : 0,
		color: getColor(index),
	}));
}
