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

import type {
	ValuedCashAccount,
	ValuedFixedAsset,
	ValuedHolding,
	ValuedLiability,
	ValuedOtherAsset,
} from "../../types/portfolioAnalytics";
import {
	ANALYTICS_TOOLTIP_CURSOR_STYLE,
	ANALYTICS_TOOLTIP_ITEM_STYLE,
	ANALYTICS_TOOLTIP_LABEL_STYLE,
	ANALYTICS_TOOLTIP_STYLE,
	buildPlatformBreakdown,
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

type PlatformBreakdownChartProps = {
	cash_accounts: ValuedCashAccount[];
	holdings: ValuedHolding[];
	fixed_assets: ValuedFixedAsset[];
	liabilities: ValuedLiability[];
	other_assets: ValuedOtherAsset[];
	title?: string;
	description?: string;
};

export function PlatformBreakdownChart({
	cash_accounts,
	holdings,
	fixed_assets,
	liabilities,
	other_assets,
	title = "账户、来源与类别",
	description = "现金按平台、投资类按来源，其余按类别归口，负债按待偿金额单列。",
}: PlatformBreakdownChartProps) {
	const platformBreakdown = buildPlatformBreakdown(
		cash_accounts,
		holdings,
		fixed_assets,
		liabilities,
		other_assets,
	);
	const chartHeight = getBarChartHeight(platformBreakdown.length);
	const { chartContainerRef, compactAxisMode } = useResponsiveChartFrame();
	const { chartInteractionHandlers, chartTooltipProps } = useChartInteractionLock();
	const categoryAxisWidth = getAdaptiveCategoryAxisWidth(
		platformBreakdown.map((item) => item.label),
		{ compact: compactAxisMode },
	);

	return (
		<section className="analytics-card">
			<div className="analytics-card__header">
				<div>
					<p className="analytics-card__eyebrow">PLATFORMS</p>
					<h2 className="analytics-card__title">{title}</h2>
					<p className="analytics-card__description">{description}</p>
				</div>
				<span className="analytics-bar-note">覆盖 {platformBreakdown.length} 个入口</span>
			</div>

			{platformBreakdown.length === 0 ? (
				<div className="analytics-empty-state">暂无入口结构数据。</div>
			) : (
				<>
					<div
						className="analytics-chart analytics-chart--interactive"
						ref={chartContainerRef}
						{...chartInteractionHandlers}
					>
						<ResponsiveContainer width="100%" height={chartHeight}>
							<BarChart
								data={platformBreakdown}
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
										"归口金额",
									]}
									labelFormatter={(label) => `入口: ${String(label ?? "")}`}
									contentStyle={ANALYTICS_TOOLTIP_STYLE}
									itemStyle={ANALYTICS_TOOLTIP_ITEM_STYLE}
									labelStyle={ANALYTICS_TOOLTIP_LABEL_STYLE}
								/>
								<Bar dataKey="value_cny" radius={[0, 12, 12, 0]}>
									{platformBreakdown.map((item) => (
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
						{platformBreakdown.map((item) => (
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
