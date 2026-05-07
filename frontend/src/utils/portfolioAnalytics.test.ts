import { describe, expect, it } from "vitest";

import {
	buildSelectableTimelinePoints,
	buildDisplayTimelineSeriesByRange,
	calculateDynamicAxisLayout,
	calculateTimelineReferenceAxisLayout,
	formatCategoryAxisLabel,
	formatTimelineAxisLabel,
	getAdaptiveCategoryAxisWidth,
	getAdaptiveYAxisWidth,
	getAllocationDonutLayout,
	getTimelineChartTicks,
	prepareTimelineSeries,
	summarizeAverageStepDelta,
	summarizeCompoundedValueStepRate,
	summarizeTimeline,
} from "./portfolioAnalytics";

describe("calculateDynamicAxisLayout", () => {
	it("uses start/end/min/max and reference value to build the visible domain", () => {
		const layout = calculateDynamicAxisLayout(
			[
				{ label: "A", value: 100 },
				{ label: "B", value: 110 },
				{ label: "C", value: 140 },
				{ label: "D", value: 150 },
			],
			{ referenceValue: 100, paddingRatio: 0.12, minSpan: 1 },
		);

		expect(layout.referenceValue).toBe(100);
		expect(layout.domain[0]).toBeLessThanOrEqual(100);
		expect(layout.domain[1]).toBeGreaterThanOrEqual(150);
		expect(layout.tickValues.length).toBeGreaterThanOrEqual(4);
		expect(layout.tickValues[0]).toBe(layout.domain[0]);
		expect(layout.tickValues[layout.tickValues.length - 1]).toBe(layout.domain[1]);
	});

	it("keeps breathing room around a zero reference for positive return series", () => {
		const layout = calculateTimelineReferenceAxisLayout(
			[
				{ label: "A", value: 12 },
				{ label: "B", value: 15 },
				{ label: "C", value: 17 },
			],
			{ referenceMode: "zero", minSpan: 1 },
		);

		expect(layout.referenceValue).toBe(0);
		expect(layout.domain[0]).toBeLessThan(0);
		expect(layout.domain[1]).toBeGreaterThan(15);
	});

	it("keeps breathing room above a zero reference for negative return series", () => {
		const layout = calculateTimelineReferenceAxisLayout(
			[
				{ label: "A", value: -5 },
				{ label: "B", value: -8 },
				{ label: "C", value: -12 },
			],
			{ referenceMode: "zero", minSpan: 0.3 },
		);

		expect(layout.referenceValue).toBe(0);
		expect(layout.domain[0]).toBeLessThan(-12);
		expect(layout.domain[1]).toBeGreaterThan(0);
	});

	it("keeps a visible range for flat data with minSpan", () => {
		const layout = calculateDynamicAxisLayout(
			[
				{ label: "A", value: 10 },
				{ label: "B", value: 10 },
				{ label: "C", value: 10 },
			],
			{ referenceValue: 10, minSpan: 0.5 },
		);

		expect(layout.referenceValue).toBe(10);
		expect(layout.domain[1] - layout.domain[0]).toBeGreaterThanOrEqual(0.5);
		expect(layout.tickValues.length).toBeGreaterThanOrEqual(3);
	});

	it("keeps the visible peak in range when max value is far from the baseline", () => {
		const layout = calculateDynamicAxisLayout(
			[
				{ label: "P-0", value: 100 },
				{ label: "P-1", value: 115 },
				{ label: "P-2", value: 1000 },
				{ label: "P-3", value: 140 },
			],
			{
				referenceValue: 100,
				paddingRatio: 0.1,
				minSpan: 1,
			},
		);

		expect(layout.domain[0]).toBeLessThanOrEqual(100);
		expect(layout.domain[1]).toBeGreaterThanOrEqual(1000);
		expect(layout.maxValue).toBe(1000);
	});
});

describe("prepareTimelineSeries", () => {
	it("sorts timeline points by timestamp_utc", () => {
		const sorted = prepareTimelineSeries([
			{ label: "03-02", value: 120, timestamp_utc: "2026-03-02T00:00:00Z" },
			{ label: "03-01", value: 100, timestamp_utc: "2026-03-01T00:00:00Z" },
		]);

		expect(sorted.map((point) => point.label)).toEqual(["03-01", "03-02"]);
	});

	it("trims leading inactive zero points when active data follows", () => {
		const normalized = prepareTimelineSeries([
			{ label: "02-28", value: 0, timestamp_utc: "2026-02-28T00:00:00Z" },
			{ label: "03-01", value: 215_000, timestamp_utc: "2026-03-01T00:00:00Z" },
			{ label: "03-02", value: 220_000, timestamp_utc: "2026-03-02T00:00:00Z" },
		]);

		expect(normalized.map((point) => point.label)).toEqual(["03-01", "03-02"]);
	});

	it("trims low-value leading discontinuity points before a large jump", () => {
		const normalized = prepareTimelineSeries([
			{ label: "02-28 18:00", value: 111, timestamp_utc: "2026-02-28T10:00:00Z" },
			{ label: "02-28 19:00", value: 111, timestamp_utc: "2026-02-28T11:00:00Z" },
			{ label: "03-01 03:00", value: 243_088, timestamp_utc: "2026-02-28T19:00:00Z" },
			{ label: "03-01 04:00", value: 241_577, timestamp_utc: "2026-02-28T20:00:00Z" },
		]);

		expect(normalized.map((point) => point.label)).toEqual([
			"03-01 03:00",
			"03-01 04:00",
		]);
	});
});

describe("buildDisplayTimelineSeriesByRange", () => {
	it("derives a 1-day return window from sparse hourly history plus daily checkpoints", () => {
		const seriesByRange = buildDisplayTimelineSeriesByRange(
			[],
			[],
			[
				{
					label: "03-14 21:00",
					value: -7.56,
					timestamp_utc: "2026-03-14T13:00:00Z",
				},
			],
			[
				{
					label: "03-13",
					value: -6.82,
					timestamp_utc: "2026-03-12T16:00:00Z",
				},
				{
					label: "03-14",
					value: -7.12,
					timestamp_utc: "2026-03-13T16:00:00Z",
				},
			],
			[],
			[],
		);

		expect(seriesByRange.hour.length).toBe(25);
		expect(seriesByRange.hour[0]?.label).toBe("03-13 21:00");
		expect(seriesByRange.hour[0]?.value).toBe(-6.82);
		expect(seriesByRange.hour[0]?.synthetic).toBe(true);
		expect(seriesByRange.hour[3]?.label).toBe("03-14 00:00");
		expect(seriesByRange.hour[3]?.value).toBe(-7.12);
		expect(seriesByRange.hour[4]?.synthetic).toBe(true);
		expect(seriesByRange.hour[24]?.label).toBe("03-14 21:00");
	});

	it("derives a 1-hour window from the latest minute checkpoints", () => {
		const seriesByRange = buildDisplayTimelineSeriesByRange(
			[],
			[
				{
					label: "03-24 10:00",
					value: 231_000,
					timestamp_utc: "2026-03-24T02:00:00Z",
				},
				{
					label: "03-24 11:00",
					value: 233_000,
					timestamp_utc: "2026-03-24T03:00:00Z",
				},
				{
					label: "03-24 12:00",
					value: 235_500,
					timestamp_utc: "2026-03-24T04:00:00Z",
				},
			],
			[],
			[],
			[],
			[],
		);

		expect(seriesByRange.minute).toHaveLength(61);
		expect(seriesByRange.minute[0]?.label).toBe("03-24 11:00");
		expect(seriesByRange.minute[59]?.label).toBe("03-24 11:59");
		expect(seriesByRange.minute[60]?.label).toBe("03-24 12:00");
		expect(seriesByRange.minute[1]?.synthetic).toBe(true);
		expect(seriesByRange.minute.map((point) => point.value)).toEqual([
			233_000,
			...Array(59).fill(233_000),
			235_500,
		]);
	});

	it("forward-fills missing hourly buckets and keeps the latest duplicate in each bucket", () => {
		const seriesByRange = buildDisplayTimelineSeriesByRange(
			[],
			[],
			[
				{
					label: "03-24 11:00",
					value: 1.2,
					timestamp_utc: "2026-03-24T03:00:00Z",
				},
				{
					label: "03-24 12:00",
					value: 2.1,
					timestamp_utc: "2026-03-24T04:00:00Z",
				},
				{
					label: "03-24 12:00",
					value: 2.4,
					timestamp_utc: "2026-03-24T04:00:00Z",
				},
				{
					label: "03-24 16:00",
					value: 5.8,
					timestamp_utc: "2026-03-24T08:00:00Z",
				},
				{
					label: "03-24 17:00",
					value: 6.2,
					timestamp_utc: "2026-03-24T09:00:00Z",
				},
			],
			[],
			[],
			[],
		);

		const visibleHours = seriesByRange.hour.slice(-7);
		expect(visibleHours.map((point) => point.label)).toEqual([
			"03-24 11:00",
			"03-24 12:00",
			"03-24 13:00",
			"03-24 14:00",
			"03-24 15:00",
			"03-24 16:00",
			"03-24 17:00",
		]);
		expect(visibleHours.map((point) => point.value)).toEqual([1.2, 2.4, 2.4, 2.4, 2.4, 5.8, 6.2]);
		expect(visibleHours.slice(2, 5).every((point) => point.synthetic)).toBe(true);
	});

	it("builds week and month windows from the same daily history", () => {
		const dailySeries = Array.from({ length: 11 }, (_, index) => ({
			label: `03-${String(index + 4).padStart(2, "0")}`,
			value: 200_000 + index * 500,
			timestamp_utc: `2026-03-${String(index + 4).padStart(2, "0")}T00:00:00Z`,
		}));

		const seriesByRange = buildDisplayTimelineSeriesByRange(
			[],
			[],
			[],
			dailySeries,
			[],
			[],
		);

		expect(seriesByRange.day.length).toBeGreaterThanOrEqual(7);
		expect(seriesByRange.month).toHaveLength(11);
		expect(seriesByRange.day[0]?.label).toBe("03-07");
		expect(seriesByRange.month[0]?.label).toBe("03-04");
	});
});

describe("buildSelectableTimelinePoints", () => {
	it("keeps only real timeline points for explicit interval selection", () => {
		const selectablePoints = buildSelectableTimelinePoints([
			{
				label: "03-24 11:00",
				value: 1.2,
				timestamp_utc: "2026-03-24T03:00:00Z",
			},
			{
				label: "03-24 12:00",
				value: 1.2,
				timestamp_utc: "2026-03-24T04:00:00Z",
				synthetic: true,
			},
			{
				label: "03-24 16:00",
				value: 5.8,
				timestamp_utc: "2026-03-24T08:00:00Z",
			},
		]);

		expect(selectablePoints).toEqual([
			{
				key: "2026-03-24T03:00:00Z::0",
				label: "03-24 11:00",
				point: {
					label: "03-24 11:00",
					value: 1.2,
					timestamp_utc: "2026-03-24T03:00:00Z",
				},
				index: 0,
			},
			{
				key: "2026-03-24T08:00:00Z::2",
				label: "03-24 16:00",
				point: {
					label: "03-24 16:00",
					value: 5.8,
					timestamp_utc: "2026-03-24T08:00:00Z",
				},
				index: 2,
			},
		]);
	});

	it("builds unique selection keys when timeline points share a timestamp", () => {
		const selectablePoints = buildSelectableTimelinePoints([
			{
				label: "03-24 16:00",
				value: 5.8,
				timestamp_utc: "2026-03-24T08:00:00Z",
			},
			{
				label: "03-24 16:00 修正",
				value: 6.1,
				timestamp_utc: "2026-03-24T08:00:00Z",
			},
		]);

		expect(selectablePoints.map((point) => point.key)).toEqual([
			"2026-03-24T08:00:00Z::0",
			"2026-03-24T08:00:00Z::1",
		]);
	});
});

describe("summarizeTimeline", () => {
	it("computes change across the visible period", () => {
		const summary = summarizeTimeline([
			{ label: "03-01", value: 100 },
			{ label: "03-02", value: 120 },
			{ label: "03-03", value: 150 },
		]);

		expect(summary.startLabel).toBe("03-01");
		expect(summary.startValue).toBe(100);
		expect(summary.latestLabel).toBe("03-03");
		expect(summary.changeValue).toBe(50);
		expect(summary.changeRatio).toBe(0.5);
	});

	it("returns null ratio when period start value is zero", () => {
		const summary = summarizeTimeline([
			{ label: "2026-02", value: 0 },
			{ label: "2026-03", value: 239_687.62 },
		]);

		expect(summary.changeValue).toBe(239_687.62);
		expect(summary.changeRatio).toBeNull();
	});
});

describe("trend summary helpers", () => {
	it("calculates compounded step rate for positive value series", () => {
		expect(
			summarizeCompoundedValueStepRate([
				{ label: "03-01", value: 100 },
				{ label: "03-02", value: 120 },
				{ label: "03-03", value: 150 },
			]),
		).toBeCloseTo(22.4744, 3);
	});

	it("calculates average step delta for return series", () => {
		expect(
			summarizeAverageStepDelta([
				{ label: "03-01", value: 10 },
				{ label: "03-02", value: 12 },
				{ label: "03-03", value: 15 },
			]),
		).toBe(2.5);
	});
});

describe("formatTimelineAxisLabel", () => {
	it("keeps full label on regular viewport mode", () => {
		expect(formatTimelineAxisLabel("03-01 04:00", false)).toBe("03-01 04:00");
	});

	it("keeps time part in compact mode for datetime labels", () => {
		expect(
			formatTimelineAxisLabel("03-01 04:00", {
				compact: true,
				range: "hour",
			}),
		).toBe("04:00");
	});

	it("keeps date part in compact mode for day labels", () => {
		expect(
			formatTimelineAxisLabel("03-01 04:00", {
				compact: true,
				range: "day",
			}),
		).toBe("03-01");
	});

	it("reduces yearly labels to the year in compact mode", () => {
		expect(
			formatTimelineAxisLabel("2026-03", {
				compact: true,
				range: "year",
			}),
		).toBe("03");
	});

	it("truncates long custom labels in compact mode", () => {
		expect(formatTimelineAxisLabel("custom-label-long", true)).toBe("custom-…");
	});
});

describe("getAdaptiveYAxisWidth", () => {
	it("expands width for long negative labels and caps at max width", () => {
		expect(getAdaptiveYAxisWidth(["-12500k", "120k"], { minWidth: 52, maxWidth: 72 })).toBe(61);
		expect(getAdaptiveYAxisWidth(["-1234567890.12%"], { minWidth: 52, maxWidth: 72 })).toBe(72);
	});

	it("respects min width for short labels", () => {
		expect(getAdaptiveYAxisWidth(["0", "-1"], { minWidth: 56, maxWidth: 80 })).toBe(56);
	});
});

describe("formatCategoryAxisLabel", () => {
	it("shows more text on wider layouts while keeping compact mode tighter", () => {
		expect(formatCategoryAxisLabel("Global Brokerage Account", {})).toBe("Global Broker…");
		expect(formatCategoryAxisLabel("Global Brokerage Account", { compact: true })).toBe(
			"Global …",
		);
	});
});

describe("getAdaptiveCategoryAxisWidth", () => {
	it("grows category width within the configured bounds", () => {
		expect(
			getAdaptiveCategoryAxisWidth(["Global Brokerage Account", "现金管理"], {
				compact: false,
			}),
		).toBeGreaterThanOrEqual(104);
		expect(
			getAdaptiveCategoryAxisWidth(["Global Brokerage Account"], {
				compact: true,
			}),
		).toBeLessThanOrEqual(120);
	});
});

describe("getTimelineChartTicks", () => {
	it("keeps every label when the series is short", () => {
		expect(
			getTimelineChartTicks(
				[
					{ label: "03-01" },
					{ label: "03-02" },
					{ label: "03-03" },
					{ label: "03-04" },
					{ label: "03-05" },
				],
				560,
				{ compact: false },
			),
		).toEqual(["03-01", "03-02", "03-03", "03-04", "03-05"]);
	});

	it("selects evenly distributed ticks while preserving the first and last labels", () => {
		const tickLabels = getTimelineChartTicks(
			Array.from({ length: 24 }, (_, index) => ({
				label: `03-${String(index + 1).padStart(2, "0")}`,
			})),
			220,
			{ compact: true },
		);

		expect(tickLabels.length).toBe(3);
		expect(tickLabels[0]).toBe("03-01");
		expect(tickLabels[tickLabels.length - 1]).toBe("03-24");
	});
});

describe("getAllocationDonutLayout", () => {
	it("shrinks donut radii on narrow containers and caps them on wide layouts", () => {
		const narrowLayout = getAllocationDonutLayout(180);
		const wideLayout = getAllocationDonutLayout(520);

		expect(narrowLayout.outerRadius).toBeLessThan(wideLayout.outerRadius);
		expect(wideLayout.outerRadius).toBe(102);
		expect(narrowLayout.height).toBeLessThanOrEqual(260);
	});
});
