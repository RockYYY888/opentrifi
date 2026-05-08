import { describe, expect, it } from "vitest";

import {
	buildPortfolioTrendAreaData,
	buildPortfolioTrendChartData,
} from "./trendChartModels";

describe("buildPortfolioTrendChartData", () => {
	it("keeps timeline points visible for both positive and negative values", () => {
		const source = [
			{ label: "03-01 10:00", value: 12_300 },
			{ label: "03-01 11:00", value: 0 },
			{ label: "03-01 12:00", value: -2_500 },
		];

		expect(buildPortfolioTrendChartData(source)).toEqual([
			{
				label: "03-01 10:00",
				value: 12_300,
				positiveValue: 12_300,
				negativeValue: 0,
			},
			{
				label: "03-01 11:00",
				value: 0,
				positiveValue: 0,
				negativeValue: 0,
			},
			{
				label: "03-01 12:00",
				value: -2_500,
				positiveValue: 0,
				negativeValue: -2_500,
			},
		]);
	});

	it("keeps only real timeline points when the series crosses the baseline", () => {
		const source = [
			{ label: "03-01 10:00", value: -8_000 },
			{ label: "03-01 11:00", value: 12_000 },
		];

		expect(buildPortfolioTrendChartData(source)).toEqual([
			{
				label: "03-01 10:00",
				value: -8_000,
				positiveValue: 0,
				negativeValue: -8_000,
			},
			{
				label: "03-01 11:00",
				value: 12_000,
				positiveValue: 12_000,
				negativeValue: 0,
			},
		]);
	});

	it("supports custom baselines without inserting synthetic crossing points", () => {
		const source = [
			{ label: "03-01 10:00", value: 120_000 },
			{ label: "03-01 11:00", value: 96_000 },
		];

		expect(buildPortfolioTrendChartData(source, 100_000)).toEqual([
			{
				label: "03-01 10:00",
				value: 120_000,
				positiveValue: 120_000,
				negativeValue: 100_000,
			},
			{
				label: "03-01 11:00",
				value: 96_000,
				positiveValue: 100_000,
				negativeValue: 96_000,
			},
		]);
	});

	it("adds baseline crossing points only for the shaded area data", () => {
		const source = [
			{ label: "03-01 10:00", value: 120_000 },
			{ label: "03-01 11:00", value: 96_000 },
		];

		expect(buildPortfolioTrendAreaData(source, 100_000)).toEqual([
			{
				label: "03-01 10:00",
				value: 120_000,
				positiveValue: 120_000,
				negativeValue: 100_000,
			},
			{
				label: "",
				value: 100_000,
				corrected: false,
				crossingPoint: true,
				positiveValue: 100_000,
				negativeValue: 100_000,
			},
			{
				label: "03-01 11:00",
				value: 96_000,
				positiveValue: 100_000,
				negativeValue: 96_000,
			},
		]);
	});
});
