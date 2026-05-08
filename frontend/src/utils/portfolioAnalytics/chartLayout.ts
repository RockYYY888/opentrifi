import type { TimelinePoint, TimelineRange } from "../../types/portfolioAnalytics";
import { truncateLabel } from "./formatters";
import { clamp } from "./math";

type TimelineAxisLabelOptions = {
	compact?: boolean;
	range?: TimelineRange;
};

export function getBarChartHeight(itemCount: number): number {
	return Math.max(260, itemCount * 52);
}

/**
 * Formats timeline labels for narrow viewports to prevent axis overflow.
 */
export function formatTimelineAxisLabel(
	label: string,
	options: TimelineAxisLabelOptions | boolean = false,
): string {
	const compact = typeof options === "boolean" ? options : (options.compact ?? false);
	const range = typeof options === "boolean" ? undefined : options.range;
	const normalizedLabel = label.trim();
	if (!compact) {
		return normalizedLabel;
	}

	if (!range && normalizedLabel.length <= 8) {
		return normalizedLabel;
	}

	if (range === "second") {
		const timeMatch = normalizedLabel.match(/(\d{1,2}:\d{2}:\d{2})$/);
		if (timeMatch) {
			return timeMatch[1];
		}
	}

	if (range === "minute" || range === "hour") {
		const timeMatch = normalizedLabel.match(/(\d{1,2}:\d{2})$/);
		if (timeMatch) {
			return timeMatch[1];
		}
	}

	if (
		(range === "day" || range === "month") &&
		/^\d{2}-\d{2}(?:\s+\d{1,2}:\d{2})?$/.test(normalizedLabel)
	) {
		const dayMatch = normalizedLabel.match(/^(\d{2}-\d{2})(?:\s+\d{1,2}:\d{2})?$/);
		if (dayMatch) {
			return dayMatch[1];
		}
	}

	if (range === "month") {
		const monthMatch = normalizedLabel.match(/(\d{4}-\d{2})$/);
		if (monthMatch) {
			return monthMatch[1];
		}
	}

	if (range === "year") {
		const monthMatch = normalizedLabel.match(/^(\d{4})-(\d{2})$/);
		if (monthMatch) {
			return monthMatch[2];
		}

		const yearMatch = normalizedLabel.match(/(\d{4})/);
		if (yearMatch) {
			return yearMatch[1];
		}
	}

	const parts = normalizedLabel.split(/\s+/);
	const lastPart = parts[parts.length - 1] ?? normalizedLabel;
	if (/^\d{1,2}:\d{2}$/.test(lastPart)) {
		return lastPart;
	}

	if (/^\d{2}-\d{2}$/.test(normalizedLabel) || /^\d{4}-\d{2}$/.test(normalizedLabel)) {
		return normalizedLabel;
	}

	return truncateLabel(normalizedLabel, 8);
}

type AdaptiveYAxisWidthOptions = {
	minWidth?: number;
	maxWidth?: number;
	padding?: number;
	perCharWidth?: number;
};

/**
 * Estimates axis width from formatted tick labels so long negatives are not clipped.
 */
export function getAdaptiveYAxisWidth(
	labels: string[],
	{
		minWidth = 52,
		maxWidth = 72,
		padding = 12,
		perCharWidth = 7,
	}: AdaptiveYAxisWidthOptions = {},
): number {
	const longestLabelLength = labels.reduce(
		(maxLength, label) => Math.max(maxLength, label.length),
		0,
	);
	const estimatedWidth = longestLabelLength * perCharWidth + padding;
	return clamp(estimatedWidth, minWidth, maxWidth);
}

type CategoryAxisLabelOptions = {
	compact?: boolean;
	compactMaxLength?: number;
	regularMaxLength?: number;
};

export function formatCategoryAxisLabel(
	label: string,
	{
		compact = false,
		compactMaxLength = 8,
		regularMaxLength = 14,
	}: CategoryAxisLabelOptions = {},
): string {
	const normalizedLabel = label.trim();
	if (!normalizedLabel) {
		return "";
	}

	return truncateLabel(normalizedLabel, compact ? compactMaxLength : regularMaxLength);
}

type AdaptiveCategoryAxisWidthOptions = AdaptiveYAxisWidthOptions & {
	compact?: boolean;
	compactMaxLength?: number;
	regularMaxLength?: number;
};

export function getAdaptiveCategoryAxisWidth(
	labels: string[],
	{
		compact = false,
		compactMaxLength = 8,
		regularMaxLength = 14,
		minWidth,
		maxWidth,
		padding = compact ? 20 : 24,
		perCharWidth = compact ? 9 : 8,
	}: AdaptiveCategoryAxisWidthOptions = {},
): number {
	const formattedLabels = labels.map((label) =>
		formatCategoryAxisLabel(label, {
			compact,
			compactMaxLength,
			regularMaxLength,
		}),
	);

	return getAdaptiveYAxisWidth(formattedLabels, {
		minWidth: minWidth ?? (compact ? 88 : 104),
		maxWidth: maxWidth ?? (compact ? 120 : 168),
		padding,
		perCharWidth,
	});
}

type ChartTickIntervalOptions = {
	compact?: boolean;
	minLabelSpacing?: number;
	minTickCount?: number;
	maxTickCount?: number;
};

function resolveTimelineTickCount(
	chartWidth: number,
	{
		compact = false,
		minTickCount = compact ? 3 : 4,
		maxTickCount = compact ? 5 : 8,
	}: ChartTickIntervalOptions = {},
): number {
	if (chartWidth <= 0) {
		return clamp(compact ? 4 : 6, minTickCount, maxTickCount);
	}

	let resolvedTickCount = compact ? 4 : 6;
	if (chartWidth <= 280) {
		resolvedTickCount = 3;
	} else if (chartWidth <= 420) {
		resolvedTickCount = 4;
	} else if (chartWidth <= 640) {
		resolvedTickCount = compact ? 4 : 5;
	} else if (chartWidth <= 860) {
		resolvedTickCount = compact ? 5 : 6;
	} else {
		resolvedTickCount = compact ? 5 : 7;
	}

	return clamp(resolvedTickCount, minTickCount, maxTickCount);
}

export function getAllocationDonutLayout(
	chartWidth: number,
): {
	height: number;
	innerRadius: number;
	outerRadius: number;
} {
	const safeWidth = chartWidth > 0 ? chartWidth : 260;
	const outerRadius = clamp(Math.floor((safeWidth - 24) / 2), 72, 102);
	const innerRadius = clamp(outerRadius - 30, 42, 72);

	return {
		height: clamp(outerRadius * 2 + 40, 220, 260),
		innerRadius,
		outerRadius,
	};
}

export type DynamicAxisLayout = {
	referenceValue: number;
	domain: [number, number];
	minValue: number;
	maxValue: number;
	tickValues: number[];
};

type DynamicAxisOptions = {
	referenceValue?: number;
	includeReference?: boolean;
	paddingRatio?: number;
	minSpan?: number;
	targetTickCount?: number;
};

export type TimelineReferenceMode = "series-start" | "zero";

type TimelineReferenceAxisOptions = Omit<DynamicAxisOptions, "referenceValue"> & {
	referenceMode?: TimelineReferenceMode;
	referenceValue?: number;
};

function resolveNiceStep(rawStep: number): number {
	if (!Number.isFinite(rawStep) || rawStep <= 0) {
		return 1;
	}

	const magnitude = 10 ** Math.floor(Math.log10(rawStep));
	const normalizedStep = rawStep / magnitude;
	if (normalizedStep <= 1) {
		return magnitude;
	}
	if (normalizedStep <= 2) {
		return 2 * magnitude;
	}
	if (normalizedStep <= 2.5) {
		return 2.5 * magnitude;
	}
	if (normalizedStep <= 5) {
		return 5 * magnitude;
	}
	return 10 * magnitude;
}

function buildAxisTicks(
	domainMin: number,
	domainMax: number,
	step: number,
): number[] {
	const tickValues: number[] = [];
	const safeStep = Math.max(step, 1e-9);
	const maxTickCount = 12;
	let currentValue = domainMin;
	let guard = 0;

	while (currentValue <= domainMax + safeStep / 2 && guard < maxTickCount) {
		tickValues.push(Number(currentValue.toFixed(6)));
		currentValue += safeStep;
		guard += 1;
	}

	if (tickValues.length === 0 || tickValues[tickValues.length - 1] !== domainMax) {
		tickValues.push(Number(domainMax.toFixed(6)));
	}

	return tickValues;
}

function pickEvenlyDistributedIndices(itemCount: number, targetTickCount: number): number[] {
	if (itemCount <= 0) {
		return [];
	}

	if (itemCount <= targetTickCount) {
		return Array.from({ length: itemCount }, (_, index) => index);
	}

	const lastIndex = itemCount - 1;
	const selectedIndices = new Set<number>();
	for (let tickIndex = 0; tickIndex < targetTickCount; tickIndex += 1) {
		selectedIndices.add(
			Math.round((lastIndex * tickIndex) / Math.max(targetTickCount - 1, 1)),
		);
	}

	while (selectedIndices.size < targetTickCount) {
		let bestIndex = 0;
		let bestDistance = -1;
		for (let candidateIndex = 0; candidateIndex < itemCount; candidateIndex += 1) {
			if (selectedIndices.has(candidateIndex)) {
				continue;
			}

			let nearestDistance = Number.POSITIVE_INFINITY;
			for (const selectedIndex of selectedIndices) {
				nearestDistance = Math.min(
					nearestDistance,
					Math.abs(candidateIndex - selectedIndex),
				);
			}

			if (nearestDistance > bestDistance) {
				bestDistance = nearestDistance;
				bestIndex = candidateIndex;
			}
		}

		selectedIndices.add(bestIndex);
	}

	return [...selectedIndices].sort((left, right) => left - right);
}

export function getTimelineChartTicks(
	series: Pick<TimelinePoint, "label">[],
	chartWidth: number,
	options: ChartTickIntervalOptions = {},
): string[] {
	const labels = series
		.map((point) => point.label.trim())
		.filter((label) => label.length > 0);
	if (labels.length <= 1) {
		return labels;
	}

	const targetTickCount = resolveTimelineTickCount(chartWidth, options);
	return pickEvenlyDistributedIndices(labels.length, targetTickCount).map(
		(index) => labels[index]!,
	);
}

export function getTimelineChartTickIndices(
	itemCount: number,
	chartWidth: number,
	options: ChartTickIntervalOptions = {},
): number[] {
	if (itemCount <= 0) {
		return [];
	}

	const targetTickCount = resolveTimelineTickCount(chartWidth, options);
	return pickEvenlyDistributedIndices(itemCount, targetTickCount);
}

/**
 * Builds a key-point-driven y-axis from period start/end, visible min/max, and the reference line.
 */
export function calculateDynamicAxisLayout(
	series: TimelinePoint[],
	{
		referenceValue,
		includeReference = true,
		paddingRatio = 0.12,
		minSpan = 1,
		targetTickCount = 5,
	}: DynamicAxisOptions = {},
): DynamicAxisLayout {
	const numericValues = series
		.map((point) => point.value)
		.filter((value) => Number.isFinite(value));

	if (numericValues.length === 0) {
		const fallbackReferenceValue = referenceValue ?? 0;
		return {
			referenceValue: fallbackReferenceValue,
			domain: [-1, 1],
			minValue: 0,
			maxValue: 0,
			tickValues: [-1, 0, 1],
		};
	}

	const startValue = series[0]?.value ?? numericValues[0]!;
	const endValue = series[series.length - 1]?.value ?? numericValues[numericValues.length - 1]!;
	const minValue = Math.min(...numericValues);
	const maxValue = Math.max(...numericValues);
	const safeMinSpan = Math.max(minSpan, 1e-6);
	const explicitReferenceValue =
		typeof referenceValue === "number" && Number.isFinite(referenceValue)
			? referenceValue
			: undefined;
	const resolvedReferenceValue = explicitReferenceValue ?? startValue;
	const anchorValues = [startValue, endValue, minValue, maxValue];
	if (includeReference) {
		anchorValues.push(resolvedReferenceValue);
	}

	const anchorMin = Math.min(...anchorValues);
	const anchorMax = Math.max(...anchorValues);
	const anchorSpan = Math.max(anchorMax - anchorMin, safeMinSpan);
	const edgePadding = Math.max(anchorSpan * Math.max(paddingRatio, 0), safeMinSpan * 0.45);
	let domainMin = anchorMin - edgePadding;
	let domainMax = anchorMax + edgePadding;

	const rawStep = Math.max(
		(domainMax - domainMin) / Math.max(targetTickCount - 1, 1),
		safeMinSpan / 2,
	);
	const step = resolveNiceStep(rawStep);
	domainMin = Math.floor(domainMin / step) * step;
	domainMax = Math.ceil(domainMax / step) * step;

	if (domainMin === domainMax) {
		domainMin -= safeMinSpan;
		domainMax += safeMinSpan;
	}

	return {
		referenceValue: resolvedReferenceValue,
		domain: [domainMin, domainMax],
		minValue,
		maxValue,
		tickValues: buildAxisTicks(domainMin, domainMax, step),
	};
}

/**
 * Builds a shared timeline y-axis layout so value and return charts use the same
 * dynamic padding around their reference line.
 */
export function calculateTimelineReferenceAxisLayout(
	series: TimelinePoint[],
	{
		referenceMode = "series-start",
		referenceValue,
		...options
	}: TimelineReferenceAxisOptions = {},
): DynamicAxisLayout {
	const resolvedReferenceValue =
		typeof referenceValue === "number" && Number.isFinite(referenceValue)
			? referenceValue
			: referenceMode === "zero"
				? 0
				: (series[0]?.value ?? 0);

	return calculateDynamicAxisLayout(series, {
		...options,
		referenceValue: resolvedReferenceValue,
	});
}
