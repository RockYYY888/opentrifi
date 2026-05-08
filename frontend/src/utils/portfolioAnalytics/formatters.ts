import type { TimelinePoint } from "../../types/portfolioAnalytics";

/**
 * Formats numbers with the same CNY presentation used by the current dashboard.
 */
export function formatCny(value: number): string {
	return new Intl.NumberFormat("zh-CN", {
		style: "currency",
		currency: "CNY",
		maximumFractionDigits: 2,
	}).format(value);
}

/**
 * Formats large CNY values into compact, axis-friendly labels.
 */
export function formatCompactCny(value: number): string {
	const absoluteValue = Math.abs(value);
	if (absoluteValue >= 1_000_000_000) {
		return `${(value / 1_000_000_000).toFixed(1)}B`;
	}
	if (absoluteValue >= 1_000_000) {
		return `${(value / 1_000_000).toFixed(1)}M`;
	}
	if (absoluteValue >= 1_000) {
		return `${(value / 1_000).toFixed(0)}k`;
	}
	return `${Math.round(value)}`;
}

/**
 * Formats a ratio as a percentage with two decimal places.
 */
export function formatPercentage(value: number): string {
	return new Intl.NumberFormat("zh-CN", {
		style: "percent",
		minimumFractionDigits: 2,
		maximumFractionDigits: 2,
	}).format(Number.isFinite(value) ? value : 0);
}

export function formatPercentMetric(value: number, withSign = false): string {
	if (!Number.isFinite(value)) {
		return "0.00%";
	}

	const prefix = withSign && value > 0 ? "+" : "";
	return `${prefix}${value.toFixed(2)}%`;
}

export function formatCompactPercentMetric(value: number): string {
	if (!Number.isFinite(value)) {
		return "0.00%";
	}

	return `${value.toFixed(2)}%`;
}

export function truncateLabel(label: string, maxLength = 10): string {
	if (label.length <= maxLength) {
		return label;
	}
	return `${label.slice(0, maxLength - 1)}…`;
}

export function formatTimelinePointLabel(
	point: Pick<TimelinePoint, "label"> | null | undefined,
	fallbackLabel = "该点",
): string {
	const normalizedLabel = point?.label?.trim() ?? "";
	return normalizedLabel || fallbackLabel;
}

export function formatTimelineRangeLabel(
	startPoint: Pick<TimelinePoint, "label"> | null | undefined,
	endPoint: Pick<TimelinePoint, "label"> | null | undefined,
	endFallbackLabel = "该点",
): string {
	return `${formatTimelinePointLabel(startPoint, "起点")}→${formatTimelinePointLabel(
		endPoint,
		endFallbackLabel,
	)}`;
}
