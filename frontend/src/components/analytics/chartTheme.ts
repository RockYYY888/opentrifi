/**
 * CSS-variable backed trend colors used by SVG charts and tooltip text.
 * The actual palette lives in index.css so chart, button, and badge colors stay aligned.
 */
export const TREND_CHART_COLORS = {
	positiveFill: "var(--trend-positive-fill)",
	negativeFill: "var(--trend-negative-fill)",
	positiveStroke: "var(--trend-positive-stroke)",
	positiveMarker: "var(--trend-positive-marker)",
	negativeMarker: "var(--trend-negative-marker)",
	positiveText: "var(--trend-positive-text-strong)",
	negativeText: "var(--trend-negative-text-strong)",
} as const;
