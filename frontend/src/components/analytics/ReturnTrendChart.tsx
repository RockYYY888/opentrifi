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
	TimelineRange,
} from "../../types/portfolioAnalytics";
import {
	ANALYTICS_TOOLTIP_ITEM_STYLE,
	ANALYTICS_TOOLTIP_LABEL_STYLE,
	ANALYTICS_TOOLTIP_STYLE,
	buildDisplayTimelineSeriesByRange,
	calculateTimelineReferenceAxisLayout,
	formatTimelineAxisLabel,
	getAdaptiveYAxisWidth,
	getFirstSelectableTimelineRange,
	getTimelineChartTickIndices,
	formatCompactPercentMetric,
	formatPercentMetric,
	summarizeAverageStepDelta,
	summarizeTimeline,
} from "../../utils/portfolioAnalytics";
import "./analytics.css";
import {
	buildThresholdSegmentedCoordinateData,
	isThresholdSegmentedCrossingPoint,
	type ThresholdSegmentedCoordinatePoint,
} from "./chartSegmentation";
import { TREND_CHART_COLORS } from "./chartTheme";
import { buildChartTradeMarkers } from "./chartTradeMarkers";
import { TradeMarkerScatter } from "./TradeMarkerScatter";
import { TimelineRangeSelector } from "./TimelineRangeSelector";
import type { ReturnTrendSeriesOption } from "./trendChartModels";
import { useChartInteractionLock } from "./useChartInteractionLock";
import { useResponsiveChartFrame } from "./useResponsiveChartFrame";
import { useTimelineRangeSelection } from "./useTimelineRangeSelection";

type ReturnTrendChartProps = {
	title: string;
	description: string;
	seriesOptions: ReturnTrendSeriesOption[];
	defaultRange?: TimelineRange;
	loading?: boolean;
	selectorLabel?: string;
	emptyMessage?: string;
	showCompoundedStepRate?: boolean;
	recentHoldingTransactions?: HoldingTransactionRecord[];
};

const RANGE_LABELS: Record<TimelineRange, string> = {
	second: "分钟",
	minute: "小时",
	hour: "天",
	day: "周",
	month: "月",
	year: "年",
};

const STEP_DELTA_LABELS: Record<TimelineRange, string> = {
	second: "秒均变动",
	minute: "分钟均变动",
	hour: "小时均变动",
	day: "日均变动",
	month: "日均变动",
	year: "月均变动",
};

const POSITIVE_RETURN_FILL = TREND_CHART_COLORS.positiveFill;
const NEGATIVE_RETURN_FILL = TREND_CHART_COLORS.negativeFill;
const RETURN_LINE_COLOR = "rgba(230, 235, 241, 0.95)";
const ZERO_RETURN_THRESHOLD = 0;
type ReturnTrendRenderablePoint = ThresholdSegmentedCoordinatePoint;

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

type TooltipPayloadEntry = {
	dataKey?: string;
	value?: number;
	payload?: { value?: number };
};

function isInteractiveTrendPoint(
	point: { crossingPoint?: boolean } | null | undefined,
): boolean {
	return !isThresholdSegmentedCrossingPoint(point);
}

function getAnalyticsPillToneClass(value: number | null | undefined): string {
	if (value == null || !Number.isFinite(value) || value === 0) {
		return "analytics-pill";
	}
	return value > 0
		? "analytics-pill analytics-pill--positive"
		: "analytics-pill analytics-pill--negative";
}

export function ReturnTrendChart({
	title,
	description,
	seriesOptions,
	defaultRange = "hour",
	loading = false,
	selectorLabel = "标的",
	emptyMessage = "暂无可用的收益率历史数据。",
	showCompoundedStepRate = false,
	recentHoldingTransactions = [],
}: ReturnTrendChartProps) {
	const [range, setRange] = useState<TimelineRange>(defaultRange);
	const [selectedKey, setSelectedKey] = useState(seriesOptions[0]?.key ?? "");
	const lastAutoResolvedSeriesKeyRef = useRef<string | null>(null);

	const selectedOption = useMemo(() => {
		if (seriesOptions.length === 0) {
			return null;
		}

		return seriesOptions.find((option) => option.key === selectedKey) ?? seriesOptions[0];
	}, [selectedKey, seriesOptions]);

	useEffect(() => {
		if (seriesOptions.length === 0) {
			setSelectedKey("");
			return;
		}
		if (seriesOptions.some((option) => option.key === selectedKey)) {
			return;
		}
		setSelectedKey(seriesOptions[0]?.key ?? "");
	}, [selectedKey, seriesOptions]);

	const seriesByRange = useMemo(
		() =>
			selectedOption
				? buildDisplayTimelineSeriesByRange(
					selectedOption.second_series ?? [],
					selectedOption.minute_series ?? [],
					selectedOption.hour_series,
					selectedOption.day_series,
					selectedOption.month_series,
					selectedOption.year_series,
				)
				: {
					second: [],
					minute: [],
					hour: [],
					day: [],
					month: [],
					year: [],
				},
		[selectedOption],
	);
	const fallbackRange = useMemo(
		() => getFirstSelectableTimelineRange(seriesByRange),
		[seriesByRange],
	);
	const activeRange = range;
	const series = seriesByRange[activeRange];
	const axisLayout = useMemo(
		() =>
			calculateTimelineReferenceAxisLayout(series, {
				referenceMode: "zero",
				minSpan: 0.3,
			}),
		[series],
	);
	const { chartData, areaData } = useMemo(
		() => buildThresholdSegmentedCoordinateData(series, ZERO_RETURN_THRESHOLD),
		[series],
	);
	const { chartContainerRef, chartWidth, compactAxisMode } = useResponsiveChartFrame();
	const { chartInteractionHandlers, chartTooltipProps } = useChartInteractionLock();
	const intervalSelection = useTimelineRangeSelection(series);
	const hasData = intervalSelection.hasSelectableRange;
	const intervalSummary =
		intervalSelection.intervalPoints.length > 0
			? summarizeTimeline(intervalSelection.intervalPoints)
			: null;
	const intervalLatestValue = intervalSummary?.latestValue ?? 0;
	const intervalChangeValue = intervalSummary?.changeValue ?? 0;
	const visibleCompoundedStepRate = hasData
		? summarizeAverageStepDelta(intervalSelection.intervalPoints)
		: 0;
	const comparisonCardDescription = hasData
		? "以下选择器只用于下方指标比较，不改变上方图像。"
		: "当前区间数据不足时，下方指标会在累计后自动补齐。";
	const yAxisWidth = getAdaptiveYAxisWidth(
		[
			formatCompactPercentMetric(axisLayout.minValue),
			formatCompactPercentMetric(axisLayout.referenceValue),
			formatCompactPercentMetric(axisLayout.maxValue),
		],
		{
			minWidth: compactAxisMode ? 64 : 60,
			maxWidth: compactAxisMode ? 84 : 80,
		},
	);
	const xAxisTicks = getTimelineChartTickIndices(chartData.length, chartWidth, {
		compact: compactAxisMode,
		minTickCount: compactAxisMode ? 3 : 4,
		maxTickCount: compactAxisMode ? 5 : 7,
	}).map((index) => chartData[index]?.xValue ?? index);
	const xAxisLabelByValue = useMemo(
		() => new Map(chartData.map((point) => [point.xValue, point.label])),
		[chartData],
	);
	const xAxisDomain = useMemo(() => buildNumericXAxisDomain(chartData), [chartData]);
	const activeTradeMarkers = useMemo(
		() =>
			buildChartTradeMarkers({
				range: activeRange,
				series,
				chartPoints: chartData,
				transactions: recentHoldingTransactions,
				symbol: selectedOption?.key === "aggregate" ? undefined : selectedOption?.key,
			}),
		[activeRange, chartData, recentHoldingTransactions, selectedOption?.key, series],
	);
	const activeTradeMarkerByXValue = useMemo(
		() => new Map(activeTradeMarkers.map((marker) => [marker.xValue, marker])),
		[activeTradeMarkers],
	);
	const resolvedEmptyMessage =
		emptyMessage.trim().length > 0
			? `${emptyMessage} 当前所选周期的数据会在累计后补齐。`
			: "当前所选周期的收益率数据还在累计中。";
	const chartHeight = compactAxisMode ? 272 : 300;
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

	useEffect(() => {
		if (lastAutoResolvedSeriesKeyRef.current === (selectedOption?.key ?? null)) {
			return;
		}
		lastAutoResolvedSeriesKeyRef.current = selectedOption?.key ?? null;

		if (fallbackRange !== null && !intervalSelection.hasSelectableRange) {
			setRange(fallbackRange);
		}
	}, [fallbackRange, intervalSelection.hasSelectableRange, selectedOption?.key]);

	function renderActiveDot(props: {
		cx?: number;
		cy?: number;
		fill?: string;
		payload?: ReturnTrendRenderablePoint;
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
				fill={props.fill ?? RETURN_LINE_COLOR}
				stroke={tradeMarker?.stroke ?? props.stroke ?? "none"}
				strokeWidth={tradeMarker ? 1.6 : 0}
			/>
		);
	}

	return (
		<section className="analytics-card">
			<div className="analytics-card__header">
				<div>
					<p className="analytics-card__eyebrow">RETURN</p>
					<h2 className="analytics-card__title">{title}</h2>
					<p className="analytics-card__description">{description}</p>
				</div>
				<div className="analytics-segmented" role="tablist" aria-label="选择收益率周期">
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

			{seriesOptions.length > 1 ? (
				<label className="analytics-select">
					<span>{selectorLabel}</span>
					<select
						value={selectedOption?.key ?? ""}
						onChange={(event) => setSelectedKey(event.target.value)}
					>
						{seriesOptions.map((option) => (
							<option key={option.key} value={option.key}>
								{option.label}
							</option>
						))}
					</select>
				</label>
			) : null}
			{selectedOption?.quantityLabel ? (
				<div className="analytics-card__meta">
					<div className="analytics-pill">
						<span>当前持仓</span>
						<strong>{selectedOption.summaryLabel ?? selectedOption.label}</strong>
					</div>
					<div className="analytics-pill">
						<span>持有股数</span>
						<strong>{selectedOption.quantityLabel}</strong>
					</div>
				</div>
			) : null}

			{loading ? (
				<div className="analytics-empty-state">正在加载收益率数据...</div>
			) : !hasData ? (
				<div className="analytics-empty-state">{resolvedEmptyMessage}</div>
			) : (
				<div
					className="analytics-chart analytics-chart--interactive"
					ref={chartContainerRef}
					{...chartInteractionHandlers}
				>
					<ResponsiveContainer width="100%" height={chartHeight}>
						<ComposedChart
							data={chartData}
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
									)}
							/>
							<YAxis
								stroke="#d6d4cb"
								tickLine={false}
								axisLine={false}
								width={yAxisWidth}
								domain={axisLayout.domain}
								ticks={axisLayout.tickValues}
								tickMargin={compactAxisMode ? 8 : 6}
								tickFormatter={formatCompactPercentMetric}
							/>
							<ReferenceLine
								y={axisLayout.referenceValue}
								stroke={TREND_CHART_COLORS.positiveStroke}
							/>
							<Tooltip
								{...chartTooltipProps}
								content={({ active, payload }) => {
									if (!active || !payload || payload.length === 0) {
										return null;
									}

									const entries = payload as TooltipPayloadEntry[];
									const primaryEntry = entries.find((entry) => entry.dataKey === "value");
									const sourcePoint = primaryEntry?.payload as
										| ReturnTrendRenderablePoint
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
											<p style={ANALYTICS_TOOLTIP_LABEL_STYLE}>
												周期: {periodLabel}
											</p>
											<p style={ANALYTICS_TOOLTIP_ITEM_STYLE}>
												收益率: {formatPercentMetric(rawValue)}
											</p>
											<p style={ANALYTICS_TOOLTIP_ITEM_STYLE}>
												基准线: {formatPercentMetric(axisLayout.referenceValue)}
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
																		? TREND_CHART_COLORS.positiveText
																		: TREND_CHART_COLORS.negativeText,
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
								data={areaData}
								dataKey="positiveValue"
								stroke="none"
								fill={POSITIVE_RETURN_FILL}
								baseValue={ZERO_RETURN_THRESHOLD}
								tooltipType="none"
								activeDot={false}
								connectNulls
							/>
							<Area
								type="linear"
								data={areaData}
								dataKey="negativeValue"
								stroke="none"
								fill={NEGATIVE_RETURN_FILL}
								baseValue={ZERO_RETURN_THRESHOLD}
								tooltipType="none"
								activeDot={false}
								connectNulls
							/>
							<Line
								type="linear"
								dataKey="value"
								stroke={RETURN_LINE_COLOR}
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
					<div className="return-trend-legend" role="list" aria-label="收益图例">
						<span
							className="return-trend-legend__item return-trend-legend__item--positive"
							role="listitem"
						>
							基准线上方区域
						</span>
						<span
							className="return-trend-legend__item return-trend-legend__item--negative"
							role="listitem"
						>
							基准线下方区域
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
				<div className="analytics-card__meta">
					<div className={getAnalyticsPillToneClass(intervalLatestValue)}>
						<span>终点收益率</span>
						<strong>
							{intervalSummary
								? formatPercentMetric(intervalLatestValue)
								: "--"}
						</strong>
					</div>
					<div className={getAnalyticsPillToneClass(intervalChangeValue)}>
						<span>区间变化</span>
						<strong>
							{intervalSummary
								? formatPercentMetric(intervalChangeValue, true)
								: "--"}
						</strong>
					</div>
					{showCompoundedStepRate ? (
						<div
							className={getAnalyticsPillToneClass(
								intervalSelection.intervalPoints.length >= 2
									? visibleCompoundedStepRate
									: null,
							)}
						>
							<span>{`区间内${STEP_DELTA_LABELS[activeRange]}`}</span>
							<strong>
								{intervalSelection.intervalPoints.length >= 2
									? formatPercentMetric(visibleCompoundedStepRate, true)
									: "--"}
							</strong>
						</div>
					) : null}
				</div>
			</div>
		</section>
	);
}
