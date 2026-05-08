import type {
	PortfolioAnalyticsData,
	TimelineRange,
} from "../../types/portfolioAnalytics";
import { AllocationChart } from "./AllocationChart";
import { PlatformBreakdownChart } from "./PlatformBreakdownChart";
import { PortfolioInsights } from "./PortfolioInsights";
import { PortfolioTrendChart } from "./PortfolioTrendChart";
import { ReturnTrendChart } from "./ReturnTrendChart";
import { createHoldingReturnOptions } from "./trendChartModels";
import "./analytics.css";

export type PortfolioAnalyticsProps = PortfolioAnalyticsData & {
	loading?: boolean;
	defaultRange?: TimelineRange;
	className?: string;
};

export function PortfolioAnalytics({
	total_value_cny,
	cash_accounts,
	holdings,
	fixed_assets,
	liabilities: _liabilities,
	other_assets,
	allocation,
	second_series = [],
	minute_series = [],
	hour_series,
	day_series,
	month_series,
	year_series,
	holdings_return_second_series = [],
	holdings_return_minute_series = [],
	holdings_return_hour_series,
	holdings_return_day_series,
	holdings_return_month_series,
	holdings_return_year_series,
	holding_return_series,
	recent_holding_transactions = [],
	loading = false,
	defaultRange = "hour",
	className,
}: PortfolioAnalyticsProps) {
	const wrapperClassName = className
		? `portfolio-analytics ${className}`
		: "portfolio-analytics";

	return (
		<section className={wrapperClassName}>
			<PortfolioInsights
				total_value_cny={total_value_cny}
				cash_accounts={cash_accounts}
				holdings={holdings}
			/>

			<div className="portfolio-analytics__columns">
				<div className="portfolio-analytics__column">
					<PortfolioTrendChart
						second_series={second_series}
						minute_series={minute_series}
						hour_series={hour_series}
						day_series={day_series}
						month_series={month_series}
						year_series={year_series}
						holdings_return_second_series={holdings_return_second_series}
						holdings_return_minute_series={holdings_return_minute_series}
						holdings_return_hour_series={holdings_return_hour_series}
						holdings_return_day_series={holdings_return_day_series}
						holdings_return_month_series={holdings_return_month_series}
						holdings_return_year_series={holdings_return_year_series}
						recentHoldingTransactions={recent_holding_transactions}
						loading={loading}
						defaultRange={defaultRange}
					/>
					<ReturnTrendChart
						title="单只持仓收益率"
						description="查看任一持仓收益率在分钟、小时、天、周、月和近一年内的变化。"
						seriesOptions={createHoldingReturnOptions(holding_return_series)}
						recentHoldingTransactions={recent_holding_transactions}
						loading={loading}
						defaultRange={defaultRange}
						selectorLabel="持仓"
						emptyMessage="暂无单只持仓收益率数据。"
						showCompoundedStepRate
					/>
				</div>
				<div className="portfolio-analytics__column">
					<AllocationChart
						total_value_cny={total_value_cny}
						allocation={allocation}
						cash_accounts={cash_accounts}
						holdings={holdings}
						fixed_assets={fixed_assets}
						other_assets={other_assets}
					/>
					<PlatformBreakdownChart
						cash_accounts={cash_accounts}
						holdings={holdings}
						fixed_assets={fixed_assets}
						liabilities={_liabilities}
						other_assets={other_assets}
					/>
				</div>
			</div>
		</section>
	);
}
