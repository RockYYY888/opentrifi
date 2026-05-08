import type {
	PortfolioInsightSummary,
	TimelinePoint,
	ValuedCashAccount,
	ValuedHolding,
} from "../../types/portfolioAnalytics";

export function summarizeTimeline(series: TimelinePoint[]): {
	startLabel: string | null;
	startValue: number;
	latestLabel: string | null;
	latestValue: number;
	changeValue: number;
	changeRatio: number | null;
} {
	const latestPoint = series[series.length - 1];
	const startPoint = series[0];
	const latestValue = latestPoint?.value ?? 0;
	const startValue = startPoint?.value ?? latestValue;
	const changeValue = latestValue - startValue;
	const changeRatio = Math.abs(startValue) > 1e-6 ? changeValue / startValue : null;

	return {
		startLabel: startPoint?.label ?? null,
		startValue,
		latestLabel: latestPoint?.label ?? null,
		latestValue,
		changeValue,
		changeRatio,
	};
}

/**
 * Calculates the geometric mean of step-over-step return changes for the active timeline grain.
 * Timeline values are stored as return percentages, so they are converted into growth factors first.
 */
export function summarizeCompoundedStepRate(series: TimelinePoint[]): number {
	const validPoints = series.filter(
		(point) => Number.isFinite(point.value) && (1 + point.value / 100) > 0,
	);

	if (validPoints.length < 2) {
		return 0;
	}

	let cumulativeRatio = 1;
	let intervalCount = 0;

	for (let index = 1; index < validPoints.length; index += 1) {
		const previousFactor = 1 + validPoints[index - 1].value / 100;
		const currentFactor = 1 + validPoints[index].value / 100;

		if (previousFactor <= 0 || currentFactor <= 0) {
			continue;
		}

		cumulativeRatio *= currentFactor / previousFactor;
		intervalCount += 1;
	}

	if (intervalCount === 0) {
		return 0;
	}

	return (Math.pow(cumulativeRatio, 1 / intervalCount) - 1) * 100;
}

/**
 * Calculates the geometric mean of step-over-step changes for positive value series.
 */
export function summarizeCompoundedValueStepRate(series: TimelinePoint[]): number {
	const validPoints = series.filter(
		(point) => Number.isFinite(point.value) && point.value > 0,
	);

	if (validPoints.length < 2) {
		return 0;
	}

	let cumulativeRatio = 1;
	let intervalCount = 0;

	for (let index = 1; index < validPoints.length; index += 1) {
		const previousValue = validPoints[index - 1].value;
		const currentValue = validPoints[index].value;

		if (previousValue <= 0 || currentValue <= 0) {
			continue;
		}

		cumulativeRatio *= currentValue / previousValue;
		intervalCount += 1;
	}

	if (intervalCount === 0) {
		return 0;
	}

	return (Math.pow(cumulativeRatio, 1 / intervalCount) - 1) * 100;
}

/**
 * Calculates the average step-over-step delta for timeline values.
 */
export function summarizeAverageStepDelta(series: TimelinePoint[]): number {
	const validPoints = series.filter((point) => Number.isFinite(point.value));

	if (validPoints.length < 2) {
		return 0;
	}

	let cumulativeDelta = 0;
	let intervalCount = 0;

	for (let index = 1; index < validPoints.length; index += 1) {
		cumulativeDelta += validPoints[index].value - validPoints[index - 1].value;
		intervalCount += 1;
	}

	if (intervalCount === 0) {
		return 0;
	}

	return cumulativeDelta / intervalCount;
}

export function summarizePortfolioInsights(
	totalValueCny: number,
	cashAccounts: ValuedCashAccount[],
	holdings: ValuedHolding[],
): PortfolioInsightSummary {
	const sortedHoldings = [...holdings]
		.filter((holding) => holding.value_cny > 0)
		.sort((left, right) => right.value_cny - left.value_cny);
	const topHolding = sortedHoldings[0] ?? null;
	const topThreeValue = sortedHoldings
		.slice(0, 3)
		.reduce((sum, holding) => sum + holding.value_cny, 0);
	const totalCashValue = cashAccounts.reduce((sum, account) => sum + account.value_cny, 0);
	const safeDenominator = totalValueCny > 0 ? totalValueCny : totalCashValue + topThreeValue;
	const uniquePlatforms = new Set(
		cashAccounts
			.map((account) => account.platform.trim())
			.filter((platform) => platform.length > 0),
	);

	return {
		cashRatio: safeDenominator > 0 ? totalCashValue / safeDenominator : 0,
		topHolding,
		topHoldingRatio: topHolding && safeDenominator > 0
			? topHolding.value_cny / safeDenominator
			: 0,
		topThreeRatio: safeDenominator > 0 ? topThreeValue / safeDenominator : 0,
		holdingsCount: sortedHoldings.length,
		cashAccountCount: cashAccounts.length,
		platformCount: uniquePlatforms.size,
	};
}
