import { formatQuantity } from "../../lib/assetFormatting";
import type {
	HoldingReturnSeries,
	TimelinePoint,
} from "../../types/portfolioAnalytics";
import {
	buildThresholdSegmentedAreaData,
	buildThresholdSegmentedChartData,
	type ThresholdSegmentedPoint,
} from "./chartSegmentation";

export type PortfolioTrendChartPoint = ThresholdSegmentedPoint;
export type ReturnTrendChartPoint = ThresholdSegmentedPoint;

export type ReturnTrendSeriesOption = {
	key: string;
	label: string;
	summaryLabel?: string;
	quantityLabel?: string;
	second_series?: TimelinePoint[];
	minute_series?: TimelinePoint[];
	hour_series: TimelinePoint[];
	day_series: TimelinePoint[];
	month_series: TimelinePoint[];
	year_series: TimelinePoint[];
};

export function buildPortfolioTrendChartData(
	series: TimelinePoint[],
	thresholdValue = 0,
): PortfolioTrendChartPoint[] {
	return buildThresholdSegmentedChartData(series, thresholdValue);
}

export function buildPortfolioTrendAreaData(
	series: TimelinePoint[],
	thresholdValue = 0,
): PortfolioTrendChartPoint[] {
	return buildThresholdSegmentedAreaData(series, thresholdValue);
}

export function buildReturnTrendChartData(
	series: TimelinePoint[],
	thresholdValue = 0,
): ReturnTrendChartPoint[] {
	return buildThresholdSegmentedChartData(series, thresholdValue);
}

export function buildReturnTrendAreaData(
	series: TimelinePoint[],
	thresholdValue = 0,
): ReturnTrendChartPoint[] {
	return buildThresholdSegmentedAreaData(series, thresholdValue);
}

function formatHoldingSelectorLabel(item: HoldingReturnSeries): string {
	return `${formatHoldingSummaryLabel(item)} · ${formatHoldingQuantityLabel(item)}`;
}

function formatHoldingSummaryLabel(item: HoldingReturnSeries): string {
	return `${item.name} (${item.symbol})`;
}

function formatHoldingQuantityLabel(item: HoldingReturnSeries): string {
	const quantity = Number.isFinite(item.quantity) ? formatQuantity(item.quantity) : "0";
	return `${quantity} 股/份`;
}

function toSeriesOptions(items: HoldingReturnSeries[]): ReturnTrendSeriesOption[] {
	return items.map((item) => ({
		key: item.symbol,
		label: formatHoldingSelectorLabel(item),
		summaryLabel: formatHoldingSummaryLabel(item),
		quantityLabel: formatHoldingQuantityLabel(item),
		second_series: item.second_series,
		minute_series: item.minute_series,
		hour_series: item.hour_series,
		day_series: item.day_series,
		month_series: item.month_series,
		year_series: item.year_series,
	}));
}

export function createAggregateReturnOption(
	label: string,
	second_or_hour_series: TimelinePoint[],
	minute_or_day_series: TimelinePoint[],
	hour_or_month_series: TimelinePoint[],
	day_or_year_series: TimelinePoint[],
	month_series?: TimelinePoint[],
	year_series?: TimelinePoint[],
): ReturnTrendSeriesOption {
	const second_series = year_series === undefined ? [] : second_or_hour_series;
	const minute_series = year_series === undefined ? [] : minute_or_day_series;
	const hour_series = year_series === undefined ? second_or_hour_series : hour_or_month_series;
	const day_series = year_series === undefined ? minute_or_day_series : day_or_year_series;
	const resolvedMonthSeries = year_series === undefined ? hour_or_month_series : (month_series ?? []);
	const resolvedYearSeries = year_series === undefined ? day_or_year_series : year_series;

	return {
		key: "aggregate",
		label,
		second_series,
		minute_series,
		hour_series,
		day_series,
		month_series: resolvedMonthSeries,
		year_series: resolvedYearSeries,
	};
}

export function createHoldingReturnOptions(
	items: HoldingReturnSeries[],
): ReturnTrendSeriesOption[] {
	return toSeriesOptions(items);
}
