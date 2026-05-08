const CHART_COLORS = [
	"#63e8ff",
	"#7a8cff",
	"#37f0c8",
	"#ffd166",
	"#ff8ab3",
	"#7ecbff",
];

export const ANALYTICS_TOOLTIP_STYLE = {
	backgroundColor: "rgba(8, 18, 34, 0.96)",
	border: "1px solid rgba(122,214,255,0.16)",
	borderRadius: 16,
	boxShadow: "0 18px 36px rgba(0, 0, 0, 0.32)",
	color: "#ecf7ff",
	padding: "0.85rem 1rem",
};

export const ANALYTICS_TOOLTIP_LABEL_STYLE = {
	color: "#ecf7ff",
	fontWeight: 600,
};

export const ANALYTICS_TOOLTIP_ITEM_STYLE = {
	color: "#d5eeff",
	fontSize: "0.92rem",
};

export const ANALYTICS_TOOLTIP_CURSOR_STYLE = {
	fill: "rgba(99, 232, 255, 0.10)",
	stroke: "rgba(99, 232, 255, 0.18)",
	strokeWidth: 1,
};

export function getChartColors(): string[] {
	return CHART_COLORS;
}
