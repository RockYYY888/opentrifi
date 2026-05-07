import { useEffect, useMemo, useRef, useState } from "react";
import {
	Area,
	CartesianGrid,
	ComposedChart,
	Line,
	ReferenceLine,
	ResponsiveContainer,
	Tooltip,
	XAxis,
	YAxis,
} from "recharts";

import type { HoldingTransactionRecord } from "../../types/assets";
import type {
	TimelinePoint,
	TimelineRange,
} from "../../types/portfolioAnalytics";
import {
	ANALYTICS_TOOLTIP_ITEM_STYLE,
	ANALYTICS_TOOLTIP_LABEL_STYLE,
	ANALYTICS_TOOLTIP_STYLE,
	buildDisplayTimelineSeriesByRange,
	calculateTimelineReferenceAxisLayout,
	formatCompactCny,
	formatCompactPercentMetric,
	formatCny,
	formatPercentMetric,
	formatPercentage,
	formatTimelineAxisLabel,
	getAdaptiveYAxisWidth,
	getFirstSelectableTimelineRange,
	getTimelineChartTickIndices,
	summarizeAverageStepDelta,
	summarizeCompoundedValueStepRate,
	summarizeTimeline,
} from "../../utils/portfolioAnalytics";
import "./analytics.css";
import {
	buildThresholdSegmentedAreaData,
	buildThresholdSegmentedChartData,
	buildThresholdSegmentedCoordinateData,
	isThresholdSegmentedCrossingPoint,
	type ThresholdSegmentedCoordinatePoint,
	type ThresholdSegmentedPoint,
} from "./chartSegmentation";
import {
	buildChartTradeMarkers,
} from "./chartTradeMarkers";
import { TradeMarkerScatter } from "./TradeMarkerScatter";
import { TimelineRangeSelector } from "./TimelineRangeSelector";
import { useChartInteractionLock } from "./useChartInteractionLock";
import { useResponsiveChartFrame } from "./useResponsiveChartFrame";
import { useTimelineRangeSelection } from "./useTimelineRangeSelection";

type PortfolioTrendChartProps = {
	second_series?: TimelinePoint[];
	minute_series?: TimelinePoint[];
	hour_series: TimelinePoint[];
	day_series: TimelinePoint[];
	month_series: TimelinePoint[];
	year_series: TimelinePoint[];
	holdings_return_second_series?: TimelinePoint[];
	holdings_return_minute_series?: TimelinePoint[];
	holdings_return_hour_series?: TimelinePoint[];
	holdings_return_day_series?: TimelinePoint[];
	holdings_return_month_series?: TimelinePoint[];
	holdings_return_year_series?: TimelinePoint[];
	recentHoldingTransactions?: HoldingTransactionRecord[];
	defaultRange?: TimelineRange;
	loading?: boolean;
	title?: string;
	description?: string;
};

type PortfolioTrendDisplayMode = "value" | "return";

type PortfolioTrendChartPoint = ThresholdSegmentedPoint;
type PortfolioTrendRenderablePoint = ThresholdSegmentedCoordinatePoint;

type TooltipPayloadEntry = {
	dataKey?: string;
	value?: number;
	payload?: { value?: number };
};

type TrendMetricConfig = {
	referenceMode: "series-start" | "zero";
	minSpan: number;
	valueFormatter: (value: number) => string;
	compactValueFormatter: (value: number) => string;
	tooltipLabel: string;
	referenceLabel: string;
	referenceLineStroke: string;
	positiveLegend: string;
	negativeLegend: string;
};

function buildNumericXAxisDomain(
	points: Array<{
		xValue: number;
	}>,
): [number, number] {
	if (points.length === 0) {
		return [0, 1];
	}

	const firstValue = points[0]?.xValue ?? 0;
	const lastValue = points[points.length - 1]?.xValue ?? firstValue;
	if (firstValue === lastValue) {
		return [firstValue - 1, lastValue + 1];
	}

	return [firstValue, lastValue];
}

const RANGE_LABELS: Record<TimelineRange, string> = {
	second: "分钟",
	minute: "小时",
	hour: "天",
	day: "周",
	month: "月",
	year: "年",
};

const MODE_LABELS: Record<PortfolioTrendDisplayMode, string> = {
	value: "资产总额",
	return: "投资类收益率",
};

const VALUE_STEP_LABELS: Record<TimelineRange, string> = {
	second: "秒均环比",
	minute: "分钟均环比",
	hour: "小时平均环比",
	day: "日均环比",
	month: "日均环比",
	year: "月均环比",
};

const RETURN_STEP_LABELS: Record<TimelineRange, string> = {
	second: "秒均变动",
	minute: "分钟均变动",
	hour: "小时均变动",
	day: "日均变动",
	month: "日均变动",
	year: "月均变动",
};

const POSITIVE_TREND_FILL = "rgba(0, 155, 193, 0.22)";
const NEGATIVE_TREND_FILL = "rgba(215, 51, 108, 0.22)";
const TREND_LINE_COLOR = "rgba(230, 235, 241, 0.95)";
const ZERO_RETURN_THRESHOLD = 0;

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

function formatSignedRatio(ratio: number | null): string {
	if (ratio === null || !Number.isFinite(ratio)) {
		return "--";
	}

	const prefix = ratio > 0 ? "+" : "";
	return `${prefix}${formatPercentage(ratio)}`;
}

function getChangeDirection(changeValue: number): string {
	if (changeValue > 0) {
		return "增加";
	}
	if (changeValue < 0) {
		return "减少";
	}
	return "变化";
}

function getAnalyticsPillToneClass(value: number | null | undefined): string {
	if (value == null || !Number.isFinite(value) || value === 0) {
		return "analytics-pill";
	}
	return value > 0
		? "analytics-pill analytics-pill--positive"
		: "analytics-pill analytics-pill--negative";
}

function isInteractiveTrendPoint(
	point: Pick<ThresholdSegmentedPoint, "crossingPoint" | "synthetic"> | null | undefined,
): boolean {
	return !isThresholdSegmentedCrossingPoint(point);
}

export function PortfolioTrendChart({
	second_series = [],
	minute_series = [],
	hour_series,
	day_series,
	month_series,
	year_series,
	holdings_return_second_series = [],
	holdings_return_minute_series = [],
	holdings_return_hour_series = [],
	holdings_return_day_series = [],
	holdings_return_month_series = [],
	holdings_return_year_series = [],
	recentHoldingTransactions = [],
	defaultRange = "hour",
	loading = false,
	title = "资产变化趋势",
	description = "查看资产总额与投资类收益率在分钟、小时、天、周、月和近一年内的变化。",
}: PortfolioTrendChartProps) {
	const [displayMode, setDisplayMode] =
		useState<PortfolioTrendDisplayMode>("value");
	const [range, setRange] = useState<TimelineRange>(defaultRange);
	const lastAutoResolvedModeRef = useRef<PortfolioTrendDisplayMode | null>(null);

	const valueSeriesByRange = useMemo(
		() =>
			buildDisplayTimelineSeriesByRange(
				second_series,
				minute_series,
				hour_series,
				day_series,
				month_series,
				year_series,
			),
		[day_series, hour_series, minute_series, month_series, second_series, year_series],
	);
	const returnSeriesByRange = useMemo(
		() =>
			buildDisplayTimelineSeriesByRange(
				holdings_return_second_series,
				holdings_return_minute_series,
				holdings_return_hour_series,
				holdings_return_day_series,
				holdings_return_month_series,
				holdings_return_year_series,
			),
		[
			holdings_return_day_series,
			holdings_return_hour_series,
			holdings_return_minute_series,
			holdings_return_month_series,
			holdings_return_second_series,
			holdings_return_year_series,
		],
	);
	const fallbackRangeByMode = useMemo(
		() => ({
			value: getFirstSelectableTimelineRange(valueSeriesByRange),
			return: getFirstSelectableTimelineRange(returnSeriesByRange),
		}),
		[returnSeriesByRange, valueSeriesByRange],
	);
	const activeSeriesByRange =
		displayMode === "value" ? valueSeriesByRange : returnSeriesByRange;
	const activeFallbackRange = fallbackRangeByMode[displayMode];
	const activeRange = range;
	const activeSeries = activeSeriesByRange[activeRange];
	const intervalSelection = useTimelineRangeSelection(activeSeries);
	const valueRangeSeries = valueSeriesByRange[activeRange];
	const valueRangeSummary = summarizeTimeline(valueRangeSeries);
	const valueBaseline = valueRangeSummary.startValue;
	const valueSegmentedData = useMemo(
		() => buildThresholdSegmentedCoordinateData(valueRangeSeries, valueBaseline),
		[valueBaseline, valueRangeSeries],
	);
	const valueChartData = valueSegmentedData.chartData;
	const valueAreaData = valueSegmentedData.areaData;
	const returnRangeSeries = returnSeriesByRange[activeRange];
	const returnSegmentedData = useMemo(
		() => buildThresholdSegmentedCoordinateData(returnRangeSeries, ZERO_RETURN_THRESHOLD),
		[returnRangeSeries],
	);
	const returnChartData = returnSegmentedData.chartData;
	const returnAreaData = returnSegmentedData.areaData;
	const activeChartData =
		displayMode === "value" ? valueChartData : returnChartData;
	const activeAreaData =
		displayMode === "value" ? valueAreaData : returnAreaData;
	const intervalSummary =
		intervalSelection.intervalPoints.length > 0
			? summarizeTimeline(intervalSelection.intervalPoints)
			: null;
	const hasActiveSummaryData = intervalSummary !== null;
	const hasActiveStepMetric = intervalSelection.intervalPoints.length >= 2;
	const intervalLatestValue = intervalSummary?.latestValue ?? 0;
	const intervalChangeValue = intervalSummary?.changeValue ?? 0;
	const intervalChangeRatio = intervalSummary?.changeRatio ?? null;

	const activeMetricConfig: TrendMetricConfig =
		displayMode === "value"
			? {
					referenceMode: "series-start",
					minSpan: Math.max(Math.abs(valueRangeSummary.latestValue) * 0.02, 100),
					valueFormatter: formatCny,
					compactValueFormatter: formatCompactCny,
					tooltipLabel: "资产总额",
					referenceLabel: "区间起点",
					referenceLineStroke: "rgba(214, 212, 203, 0.38)",
					positiveLegend: "区间起点上方区域",
					negativeLegend: "区间起点下方区域",
				}
			: {
					referenceMode: "zero",
					minSpan: 0.3,
					valueFormatter: (value) => formatPercentMetric(value),
					compactValueFormatter: formatCompactPercentMetric,
					tooltipLabel: "投资类收益率",
					referenceLabel: "基准线",
					referenceLineStroke: "rgba(0, 155, 193, 0.65)",
					positiveLegend: "基准线上方区域",
					negativeLegend: "基准线下方区域",
				};
	const axisLayout = useMemo(
		() =>
			calculateTimelineReferenceAxisLayout(activeSeries, {
				referenceMode: activeMetricConfig.referenceMode,
				referenceValue:
					displayMode === "value" ? valueBaseline : ZERO_RETURN_THRESHOLD,
				minSpan: activeMetricConfig.minSpan,
			}),
		[
			activeMetricConfig.minSpan,
			activeMetricConfig.referenceMode,
			activeSeries,
			displayMode,
			valueBaseline,
		],
	);
	const { chartContainerRef, chartWidth, compactAxisMode } =
		useResponsiveChartFrame();
	const { chartInteractionHandlers, chartTooltipProps } = useChartInteractionLock();
	const hasData = intervalSelection.hasSelectableRange;
	const yAxisWidth = getAdaptiveYAxisWidth(
		[
			activeMetricConfig.compactValueFormatter(axisLayout.minValue),
			activeMetricConfig.compactValueFormatter(axisLayout.referenceValue),
			activeMetricConfig.compactValueFormatter(axisLayout.maxValue),
		],
		{
			minWidth: compactAxisMode ? 64 : 60,
			maxWidth: compactAxisMode ? 84 : 80,
		},
	);
	const xAxisTicks = getTimelineChartTickIndices(activeChartData.length, chartWidth, {
		compact: compactAxisMode,
		minTickCount: compactAxisMode ? 3 : 4,
		maxTickCount: compactAxisMode ? 5 : 7,
	}).map((index) => activeChartData[index]?.xValue ?? index);
	const xAxisLabelByValue = useMemo(
		() => new Map(activeChartData.map((point) => [point.xValue, point.label])),
		[activeChartData],
	);
	const xAxisDomain = useMemo(
		() => buildNumericXAxisDomain(activeChartData),
		[activeChartData],
	);
	const activeTradeMarkers = useMemo(
		() =>
			displayMode === "return"
				? buildChartTradeMarkers({
					range: activeRange,
					series: activeSeries,
					chartPoints: activeChartData,
					transactions: recentHoldingTransactions,
				})
				: [],
		[
			activeChartData,
			activeRange,
			activeSeries,
			displayMode,
			recentHoldingTransactions,
		],
	);
	const activeTradeMarkerByXValue = useMemo(
		() => new Map(activeTradeMarkers.map((marker) => [marker.xValue, marker])),
		[activeTradeMarkers],
	);

	useEffect(() => {
		if (
			fallbackRangeByMode[displayMode] === null &&
			fallbackRangeByMode.value !== null
		) {
			setDisplayMode("value");
		}
	}, [displayMode, fallbackRangeByMode]);

	useEffect(() => {
		if (lastAutoResolvedModeRef.current === displayMode) {
			return;
		}
		lastAutoResolvedModeRef.current = displayMode;

		if (activeFallbackRange !== null && !intervalSelection.hasSelectableRange) {
			setRange(activeFallbackRange);
		}
	}, [activeFallbackRange, displayMode, intervalSelection.hasSelectableRange, range]);

	function renderActiveDot(props: {
		cx?: number;
		cy?: number;
		fill?: string;
		payload?: PortfolioTrendRenderablePoint;
		stroke?: string;
	}): JSX.Element | null {
		const sourcePoint = props.payload;
		if (
			typeof props.cx !== "number" ||
			typeof props.cy !== "number" ||
			sourcePoint === undefined ||
			!isInteractiveTrendPoint(sourcePoint)
		) {
			return null;
		}

		const tradeMarker = activeTradeMarkerByXValue.get(sourcePoint.xValue);
		return (
			<circle
				cx={props.cx}
				cy={props.cy}
				r={4}
				fill={props.fill ?? TREND_LINE_COLOR}
				stroke={tradeMarker?.stroke ?? props.stroke ?? "none"}
				strokeWidth={tradeMarker ? 1.6 : 0}
			/>
		);
	}

	const activeValueLabel =
		displayMode === "value"
			? "终点净值"
			: "终点投资类收益率";
	const activePeriodValue =
		!hasActiveSummaryData
			? "--"
			: displayMode === "value"
				? `${getChangeDirection(intervalChangeValue)}${formatCny(Math.abs(intervalChangeValue))} / ${formatSignedRatio(intervalChangeRatio)}`
				: formatPercentMetric(intervalChangeValue, true);
	const activeStepMetricLabel =
		displayMode === "value"
			? `区间内${VALUE_STEP_LABELS[activeRange]}`
			: `区间内${RETURN_STEP_LABELS[activeRange]}`;
	const activeTerminalToneValue =
		!hasActiveSummaryData
			? null
			: displayMode === "value"
				? intervalChangeValue
				: intervalLatestValue;
	const activeStepMetricNumericValue =
		!hasActiveStepMetric
			? null
			: displayMode === "value"
				? summarizeCompoundedValueStepRate(intervalSelection.intervalPoints)
				: summarizeAverageStepDelta(intervalSelection.intervalPoints);
	const activeStepMetricValue =
		activeStepMetricNumericValue === null
			? "--"
			: displayMode === "value"
				? formatPercentMetric(activeStepMetricNumericValue, true)
				: formatPercentMetric(activeStepMetricNumericValue, true);
	const comparisonCardDescription = hasData
		? "以下选择器只用于下方指标比较，不改变上方图像。"
		: "当前区间数据不足时，下方指标会在累计后自动补齐。";
	const chartHeight = compactAxisMode ? 280 : 320;
	const chartMargin = {
		top: 18,
		right: compactAxisMode ? 28 : 20,
		left: compactAxisMode ? 16 : 10,
		bottom: compactAxisMode ? 16 : 8,
	};
	const xAxisHeight = compactAxisMode ? 30 : 24;
	const tradeMarkerPlotLeft = chartMargin.left + yAxisWidth;
	const tradeMarkerPlotTop = chartMargin.top;
	const tradeMarkerPlotWidth = Math.max(
		chartWidth - tradeMarkerPlotLeft - chartMargin.right,
		0,
	);
	const tradeMarkerPlotHeight = Math.max(
		chartHeight - tradeMarkerPlotTop - chartMargin.bottom - xAxisHeight,
		0,
	);

	return (
		<section className="analytics-card">
			<div className="analytics-card__header">
				<div>
					<p className="analytics-card__eyebrow">TREND</p>
					<h2 className="analytics-card__title">{title}</h2>
					<p className="analytics-card__description">{description}</p>
				</div>
				<div className="analytics-card__controls">
					<div
						className="analytics-segmented"
						role="tablist"
						aria-label="选择趋势周期"
					>
						{(Object.keys(RANGE_LABELS) as TimelineRange[]).map((item) => (
							<button
								key={item}
								type="button"
								className={activeRange === item ? "active" : ""}
								onClick={() => setRange(item)}
							>
								{RANGE_LABELS[item]}
							</button>
						))}
					</div>
				</div>
			</div>

			<div
				className="analytics-segmented"
				role="tablist"
				aria-label="选择趋势维度"
			>
				{(Object.keys(MODE_LABELS) as PortfolioTrendDisplayMode[]).map((mode) => (
					<button
						key={mode}
						type="button"
						className={displayMode === mode ? "active" : ""}
						onClick={() => setDisplayMode(mode)}
						disabled={fallbackRangeByMode[mode] === null}
					>
						{MODE_LABELS[mode]}
					</button>
				))}
			</div>

			{loading ? (
				<div className="analytics-empty-state">正在加载趋势数据...</div>
			) : !hasData ? (
				<div className="analytics-empty-state">
					{displayMode === "value"
						? "当前所选周期的资产总额数据还在累计中。"
						: "当前所选周期的投资类收益率数据还在累计中。"}
				</div>
			) : (
				<div
					className="analytics-chart analytics-chart--interactive"
					ref={chartContainerRef}
					{...chartInteractionHandlers}
				>
					<ResponsiveContainer width="100%" height={chartHeight}>
						<ComposedChart
							data={activeChartData}
							margin={chartMargin}
						>
							<CartesianGrid stroke="rgba(255,255,255,0.08)" />
							<XAxis
								type="number"
								dataKey="xValue"
								stroke="#d6d4cb"
								tickLine={false}
								axisLine={false}
								domain={xAxisDomain}
								height={compactAxisMode ? 30 : 24}
								ticks={xAxisTicks}
								interval={0}
								minTickGap={compactAxisMode ? 24 : 12}
								tickMargin={compactAxisMode ? 10 : 8}
								padding={{ left: 0, right: 0 }}
								tickFormatter={(xValue: number) =>
									formatTimelineAxisLabel(
										xAxisLabelByValue.get(xValue) ?? "",
										{
										compact: compactAxisMode,
										range: activeRange,
										},
									)
								}
							/>
							<YAxis
								stroke="#d6d4cb"
								tickLine={false}
								axisLine={false}
								width={yAxisWidth}
								domain={axisLayout.domain}
								ticks={axisLayout.tickValues}
								tickMargin={compactAxisMode ? 8 : 6}
								tickFormatter={activeMetricConfig.compactValueFormatter}
							/>
							<ReferenceLine
								y={axisLayout.referenceValue}
								stroke={activeMetricConfig.referenceLineStroke}
							/>
							<Tooltip
								{...chartTooltipProps}
								content={({ active, payload }) => {
									if (!active || !payload || payload.length === 0) {
										return null;
									}

									const entries = payload as TooltipPayloadEntry[];
									const primaryEntry = entries.find(
										(entry) => entry.dataKey === "value",
									);
									const sourcePoint = primaryEntry?.payload as
										| PortfolioTrendRenderablePoint
										| undefined;
									if (
										primaryEntry === undefined ||
										sourcePoint === undefined ||
										!isInteractiveTrendPoint(sourcePoint)
									) {
										return null;
									}
									const rawValue = Number(
										primaryEntry.value ?? sourcePoint.value ?? 0,
									);
									const periodLabel =
										sourcePoint.label?.trim() ||
										xAxisLabelByValue.get(sourcePoint.xValue) ||
										"";
									if (!periodLabel) {
										return null;
									}
									const tradeMarker = activeTradeMarkerByXValue.get(sourcePoint.xValue);

									return (
										<div style={ANALYTICS_TOOLTIP_STYLE}>
											<p style={ANALYTICS_TOOLTIP_LABEL_STYLE}>周期: {periodLabel}</p>
											<p style={ANALYTICS_TOOLTIP_ITEM_STYLE}>
												{activeMetricConfig.tooltipLabel}:{" "}
												{activeMetricConfig.valueFormatter(rawValue)}
											</p>
											<p style={ANALYTICS_TOOLTIP_ITEM_STYLE}>
												{activeMetricConfig.referenceLabel}:{" "}
												{activeMetricConfig.valueFormatter(axisLayout.referenceValue)}
											</p>
											{tradeMarker ? (
												<>
													<p
														style={{
															...ANALYTICS_TOOLTIP_LABEL_STYLE,
															marginTop: "0.55rem",
														}}
													>
														交易事件
													</p>
													{tradeMarker.events.map((event) => (
														<p
															key={event.id}
															style={{
																...ANALYTICS_TOOLTIP_ITEM_STYLE,
																color:
																	event.side === "BUY"
																		? "rgba(181, 241, 255, 0.96)"
																		: "rgba(255, 196, 216, 0.96)",
															}}
														>
															{event.description}
														</p>
													))}
												</>
											) : null}
										</div>
									);
								}}
								contentStyle={ANALYTICS_TOOLTIP_STYLE}
								itemStyle={ANALYTICS_TOOLTIP_ITEM_STYLE}
								labelStyle={ANALYTICS_TOOLTIP_LABEL_STYLE}
							/>
							<Area
								type="linear"
								data={activeAreaData}
								dataKey="positiveValue"
								stroke="none"
								fill={POSITIVE_TREND_FILL}
								baseValue={axisLayout.referenceValue}
								tooltipType="none"
								activeDot={false}
								connectNulls
							/>
							<Area
								type="linear"
								data={activeAreaData}
								dataKey="negativeValue"
								stroke="none"
								fill={NEGATIVE_TREND_FILL}
								baseValue={axisLayout.referenceValue}
								tooltipType="none"
								activeDot={false}
								connectNulls
							/>
							<Line
								type="linear"
								dataKey="value"
								stroke={TREND_LINE_COLOR}
								strokeWidth={2.4}
								dot={false}
								activeDot={renderActiveDot}
							/>
						</ComposedChart>
					</ResponsiveContainer>
					<TradeMarkerScatter
						markers={activeTradeMarkers}
						chartWidth={chartWidth}
						chartHeight={chartHeight}
						plotLeft={tradeMarkerPlotLeft}
						plotTop={tradeMarkerPlotTop}
						plotWidth={tradeMarkerPlotWidth}
						plotHeight={tradeMarkerPlotHeight}
						xDomain={xAxisDomain}
						yDomain={axisLayout.domain}
					/>
					<div
						className="return-trend-legend"
						role="list"
						aria-label={displayMode === "value" ? "净值图例" : "投资类收益率图例"}
					>
						<span
							className="return-trend-legend__item return-trend-legend__item--positive"
							role="listitem"
						>
							{activeMetricConfig.positiveLegend}
						</span>
						<span
							className="return-trend-legend__item return-trend-legend__item--negative"
							role="listitem"
						>
							{activeMetricConfig.negativeLegend}
						</span>
					</div>
				</div>
			)}

			<div className="analytics-comparison-card">
				<div className="analytics-comparison-card__header">
					<div className="analytics-comparison-card__copy">
						<strong>指标比较区间</strong>
						<p>{comparisonCardDescription}</p>
					</div>
					{!intervalSelection.isFullRangeSelected ? (
						<button
							type="button"
							className="analytics-interval-selector__reset"
							onClick={intervalSelection.resetSelection}
						>
							恢复全区间
						</button>
					) : null}
				</div>
				<TimelineRangeSelector
					selectablePoints={intervalSelection.selectablePoints}
					startKey={intervalSelection.startKey}
					endKey={intervalSelection.endKey}
					isFullRangeSelected={intervalSelection.isFullRangeSelected}
					onStartChange={intervalSelection.handleStartKeyChange}
					onEndChange={intervalSelection.handleEndKeyChange}
					onReset={intervalSelection.resetSelection}
					showHeader={false}
					embedded
				/>
				<div className="analytics-card__meta analytics-card__meta--trend">
					<div className={getAnalyticsPillToneClass(activeTerminalToneValue)}>
						<span>{activeValueLabel}</span>
						<strong>
							{displayMode === "value"
								? hasActiveSummaryData
									? formatCny(intervalLatestValue)
									: "--"
								: hasActiveSummaryData
									? formatPercentMetric(intervalLatestValue)
									: "--"}
						</strong>
					</div>
					<div className={getAnalyticsPillToneClass(intervalChangeValue)}>
						<span>区间变化</span>
						<strong>{activePeriodValue}</strong>
					</div>
					<div className={getAnalyticsPillToneClass(activeStepMetricNumericValue)}>
						<span>{activeStepMetricLabel}</span>
						<strong>{activeStepMetricValue}</strong>
					</div>
				</div>
			</div>
		</section>
	);
}
