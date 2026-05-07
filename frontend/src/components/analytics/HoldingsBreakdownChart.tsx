import {
	Bar,
	BarChart,
	CartesianGrid,
	Cell,
	ResponsiveContainer,
	Tooltip,
	XAxis,
	YAxis,
} from "recharts";

import type { ValuedHolding } from "../../types/portfolioAnalytics";
import {
	ANALYTICS_TOOLTIP_CURSOR_STYLE,
	ANALYTICS_TOOLTIP_ITEM_STYLE,
	ANALYTICS_TOOLTIP_LABEL_STYLE,
	ANALYTICS_TOOLTIP_STYLE,
	buildHoldingsBreakdown,
	formatCategoryAxisLabel,
	formatCompactCny,
	formatCny,
	formatPercentage,
	getAdaptiveCategoryAxisWidth,
	getBarChartHeight,
} from "../../utils/portfolioAnalytics";
import "./analytics.css";
import { useChartInteractionLock } from "./useChartInteractionLock";
import { useResponsiveChartFrame } from "./useResponsiveChartFrame";

type HoldingsBreakdownChartProps = {
	holdings: ValuedHolding[];
	title?: string;
	description?: string;
};

export function HoldingsBreakdownChart({
	holdings,
	title = "持仓拆解",
	description = "按持仓市值排序",
}: HoldingsBreakdownChartProps) {
	const breakdown = buildHoldingsBreakdown(holdings);
	const chartHeight = getBarChartHeight(breakdown.length);
	const visibleHoldingsCount = holdings.filter((holding) => holding.value_cny > 0).length;
	const { chartContainerRef, compactAxisMode } = useResponsiveChartFrame();
	const { chartInteractionHandlers, chartTooltipProps } = useChartInteractionLock();
	const categoryAxisWidth = getAdaptiveCategoryAxisWidth(
		breakdown.map((item) => item.label),
		{ compact: compactAxisMode },
	);

	return (
		<section className="analytics-card">
			<div className="analytics-card__header">
				<div>
					<p className="analytics-card__eyebrow">HOLDINGS</p>
					<h2 className="analytics-card__title">{title}</h2>
					<p className="analytics-card__description">{description}</p>
				</div>
				<span className="analytics-bar-note">共 {visibleHoldingsCount} 个有效仓位</span>
			</div>

			{breakdown.length === 0 ? (
				<div className="analytics-empty-state">
					暂无证券持仓。录入股票、ETF 或基金后，这里会自动形成头寸排名。
				</div>
			) : (
				<>
					<div
						className="analytics-chart analytics-chart--interactive"
						ref={chartContainerRef}
						{...chartInteractionHandlers}
					>
						<ResponsiveContainer width="100%" height={chartHeight}>
							<BarChart
								data={breakdown}
								layout="vertical"
								margin={{
									top: 4,
									right: compactAxisMode ? 8 : 12,
									left: compactAxisMode ? 4 : 8,
									bottom: 0,
								}}
							>
								<CartesianGrid
									strokeDasharray="3 3"
									horizontal={false}
									stroke="rgba(255,255,255,0.08)"
								/>
								<XAxis
									type="number"
									stroke="#d6d4cb"
									tickLine={false}
									axisLine={false}
									tickMargin={8}
									tickFormatter={formatCompactCny}
								/>
								<YAxis
									type="category"
									dataKey="label"
									width={categoryAxisWidth}
									stroke="#d6d4cb"
									tickLine={false}
									axisLine={false}
									tickMargin={6}
									tickFormatter={(label: string) =>
										formatCategoryAxisLabel(label, {
											compact: compactAxisMode,
										})}
								/>
								<Tooltip
									{...chartTooltipProps}
									cursor={ANALYTICS_TOOLTIP_CURSOR_STYLE}
									formatter={(value) => [
										formatCny(Number(value ?? 0)),
										"持仓市值",
									]}
									labelFormatter={(label) => `持仓: ${String(label ?? "")}`}
									contentStyle={ANALYTICS_TOOLTIP_STYLE}
									itemStyle={ANALYTICS_TOOLTIP_ITEM_STYLE}
									labelStyle={ANALYTICS_TOOLTIP_LABEL_STYLE}
								/>
								<Bar dataKey="value_cny" radius={[0, 12, 12, 0]}>
									{breakdown.map((item) => (
										<Cell
											key={`${item.label}-${item.value_cny}`}
											fill={item.color}
										/>
									))}
								</Bar>
							</BarChart>
						</ResponsiveContainer>
					</div>

					<div className="analytics-legend">
						{breakdown.map((item) => (
							<div className="analytics-legend__item" key={item.label}>
								<span
									className="analytics-legend__swatch"
									style={{ backgroundColor: item.color }}
								/>
								<div className="analytics-legend__label">
									<span>{item.label}</span>
									<small>{formatPercentage(item.percentage)}</small>
								</div>
								<div className="analytics-legend__value">{formatCny(item.value_cny)}</div>
							</div>
						))}
					</div>
				</>
			)}
		</section>
	);
}
