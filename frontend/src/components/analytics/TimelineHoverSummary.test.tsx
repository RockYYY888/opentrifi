import {
	cleanup,
	fireEvent,
	render,
	screen,
	waitFor,
	within,
} from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PortfolioTrendChart } from "./PortfolioTrendChart";
import { ReturnTrendChart } from "./ReturnTrendChart";
import { createAggregateReturnOption } from "./trendChartModels";

vi.mock("recharts", () => ({
	ResponsiveContainer: ({ children }: { children?: ReactNode }) => (
		<>{children}</>
	),
	ComposedChart: ({ children }: { children?: ReactNode }) => (
		<div data-testid="composed-chart">{children}</div>
	),
	CartesianGrid: () => null,
	Tooltip: () => null,
	XAxis: () => null,
	YAxis: () => null,
	ReferenceLine: () => null,
	Area: () => null,
	Line: () => null,
}));

class MockResizeObserver {
	private readonly callback: ResizeObserverCallback;

	constructor(callback: ResizeObserverCallback) {
		this.callback = callback;
	}

	observe(target: Element) {
		this.callback(
			[
				{
					target,
					contentRect: {
						width: 360,
						height: 260,
						x: 0,
						y: 0,
						top: 0,
						left: 0,
						bottom: 260,
						right: 360,
						toJSON() {
							return {};
						},
					},
				} as ResizeObserverEntry,
			],
			this as unknown as ResizeObserver,
		);
	}

	unobserve() {}

	disconnect() {}
}

function expectPillToContain(
	label: string,
	expectedText: string,
	occurrence = 0,
): void {
	const pill = screen.getAllByText(label)[occurrence]?.parentElement;
	expect(pill).not.toBeNull();
	expect(pill?.textContent).toContain(expectedText);
}

describe("timeline range summaries", () => {
	beforeEach(() => {
		vi.stubGlobal("ResizeObserver", MockResizeObserver);
		vi
			.spyOn(HTMLElement.prototype, "getBoundingClientRect")
			.mockImplementation(() => ({
				width: 360,
				height: 260,
				top: 0,
				left: 0,
				bottom: 260,
				right: 360,
				x: 0,
				y: 0,
				toJSON() {
					return {};
				},
			}));
	});

	afterEach(() => {
		cleanup();
		vi.unstubAllGlobals();
		vi.restoreAllMocks();
	});

	it("updates portfolio trend summary pills when selecting an arbitrary interval", async () => {
		render(
			<PortfolioTrendChart
				defaultRange="day"
				hour_series={[]}
				day_series={[
					{ label: "03-01", value: 100 },
					{ label: "03-02", value: 120 },
					{ label: "03-03", value: 150 },
				]}
				holdings_return_hour_series={[]}
				holdings_return_day_series={[
					{ label: "03-01", value: 10 },
					{ label: "03-02", value: 12 },
					{ label: "03-03", value: 15 },
				]}
				holdings_return_month_series={[]}
				month_series={[]}
				holdings_return_year_series={[]}
				year_series={[]}
			/>,
		);
		const portfolioCard = screen.getByText("资产变化趋势").closest("section");
		const portfolioChart = portfolioCard?.querySelector(".analytics-chart");
		const portfolioComparisonCard = portfolioCard?.querySelector(
			".analytics-comparison-card",
		);
		expect(portfolioCard).not.toBeNull();
		expect(portfolioChart).not.toBeNull();
		expect(portfolioComparisonCard).not.toBeNull();
		expect(
			Array.from(portfolioCard?.children ?? []).indexOf(portfolioChart as Element),
		).toBeLessThan(
			Array.from(portfolioCard?.children ?? []).indexOf(
				portfolioComparisonCard as Element,
			),
		);

		expect(
			screen.getByRole("button", { name: "选择起点时间点" }).textContent,
		).toContain("03-01");
		expect(
			screen.getByRole("button", { name: "选择终点时间点" }).textContent,
		).toContain("03-03");
		expect(screen.queryByText("当前区间")).toBeNull();
		expectPillToContain("终点净值", "¥150.00");
		expectPillToContain("区间变化", "增加¥50.00 / +50.00%", 0);
		expectPillToContain("区间内日均环比", "+22.47%");

		fireEvent.click(screen.getByRole("button", { name: "选择起点时间点" }));
		const startDialog = screen.getByRole("dialog", { name: "选择起点时间点" });
		expect(within(startDialog).queryByRole("button", { name: "03-03" })).toBeNull();
		fireEvent.click(within(startDialog).getByRole("button", { name: "03-02" }));

		await waitFor(() => {
			expectPillToContain("区间变化", "增加¥30.00 / +25.00%", 0);
		});
		expect(
			screen.getByRole("button", { name: "选择起点时间点" }).textContent,
		).toContain("03-02");
		expectPillToContain("终点净值", "¥150.00");
		expectPillToContain("区间内日均环比", "+25.00%");

		fireEvent.click(screen.getByRole("button", { name: "选择终点时间点" }));
		const endDialog = screen.getByRole("dialog", { name: "选择终点时间点" });
		expect(within(endDialog).queryByRole("button", { name: "03-02" })).toBeNull();
		expect(within(endDialog).getByRole("button", { name: "03-03" })).toBeDefined();

		expectPillToContain("区间变化", "增加¥30.00 / +25.00%", 0);
		expectPillToContain("区间内日均环比", "+25.00%");

		fireEvent.click(screen.getByRole("button", { name: "投资类收益率" }));

		await waitFor(() => {
			expectPillToContain("终点投资类收益率", "15.00%");
		});
		expectPillToContain("区间变化", "+3.00%", 0);
		expectPillToContain("区间内日均变动", "+3.00%");
	});

	it("updates return trend summary pills when start and end selections move across each other", async () => {
		render(
			<ReturnTrendChart
				title="收益趋势"
				description="测试"
				defaultRange="day"
				showCompoundedStepRate
				seriesOptions={[
					createAggregateReturnOption(
						"非现金资产",
						[],
						[
							{ label: "03-01", value: 10 },
							{ label: "03-02", value: 12 },
							{ label: "03-03", value: 15 },
						],
						[
							{ label: "2026-01", value: 8 },
							{ label: "2026-02", value: 9 },
							{ label: "2026-03", value: 11 },
						],
						[],
					),
				]}
			/>,
		);
		const returnCard = screen.getByText("收益趋势").closest("section");
		const returnChart = returnCard?.querySelector(".analytics-chart");
		const returnComparisonCard = returnCard?.querySelector(
			".analytics-comparison-card",
		);
		expect(returnCard).not.toBeNull();
		expect(returnChart).not.toBeNull();
		expect(returnComparisonCard).not.toBeNull();
		expect(
			Array.from(returnCard?.children ?? []).indexOf(returnChart as Element),
		).toBeLessThan(
			Array.from(returnCard?.children ?? []).indexOf(
				returnComparisonCard as Element,
			),
		);

		expect(
			screen.getByRole("button", { name: "选择起点时间点" }).textContent,
		).toContain("03-01");
		expect(
			screen.getByRole("button", { name: "选择终点时间点" }).textContent,
		).toContain("03-03");
		expect(screen.queryByText("当前区间")).toBeNull();
		expectPillToContain("终点收益率", "15.00%");
		expectPillToContain("区间变化", "+5.00%");
		expectPillToContain("区间内日均变动", "+2.50%");

		fireEvent.click(screen.getByRole("button", { name: "选择终点时间点" }));
		let endDialog = screen.getByRole("dialog", { name: "选择终点时间点" });
		expect(within(endDialog).queryByRole("button", { name: "03-01" })).toBeNull();
		fireEvent.click(within(endDialog).getByRole("button", { name: "03-02" }));

		await waitFor(() => {
			expectPillToContain("终点收益率", "12.00%");
		});
		expect(
			screen.getByRole("button", { name: "选择终点时间点" }).textContent,
		).toContain("03-02");
		expectPillToContain("区间变化", "+2.00%");
		expectPillToContain("区间内日均变动", "+2.00%");

		fireEvent.click(screen.getByRole("button", { name: "选择起点时间点" }));
		const startDialog = screen.getByRole("dialog", { name: "选择起点时间点" });
		expect(within(startDialog).queryByRole("button", { name: "03-02" })).toBeNull();
		expect(within(startDialog).queryByRole("button", { name: "03-03" })).toBeNull();
		fireEvent.click(screen.getByRole("button", { name: "选择终点时间点" }));
		endDialog = screen.getByRole("dialog", { name: "选择终点时间点" });
		fireEvent.click(within(endDialog).getByRole("button", { name: "03-03" }));

		await waitFor(() => {
			expectPillToContain("终点收益率", "15.00%");
		});
		expect(
			screen.getByRole("button", { name: "选择终点时间点" }).textContent,
		).toContain("03-03");
		expectPillToContain("区间变化", "+5.00%");
		expectPillToContain("区间内日均变动", "+2.50%");

		fireEvent.click(screen.getByRole("button", { name: "年" }));

		expect(
			screen.getByRole("button", { name: "选择起点时间点" }).textContent,
		).toContain("2026-01");
		expect(
			screen.getByRole("button", { name: "选择终点时间点" }).textContent,
		).toContain("2026-03");
		expect(screen.queryByText("当前区间")).toBeNull();
		expectPillToContain("终点收益率", "11.00%");
		expectPillToContain("区间变化", "+3.00%");
		expectPillToContain("区间内月均变动", "+1.50%");
	});

	it("shows the selected holding quantity directly and keeps interval summaries synced when switching holdings", async () => {
		render(
			<ReturnTrendChart
				title="单只持仓收益率"
				description="测试"
				defaultRange="day"
				selectorLabel="持仓"
				showCompoundedStepRate
				seriesOptions={[
					{
						key: "0700.HK",
						label: "腾讯控股 (0700.HK) · 120 股/份",
						summaryLabel: "腾讯控股 (0700.HK)",
						quantityLabel: "120 股/份",
						hour_series: [],
						day_series: [
							{ label: "03-01", value: 1 },
							{ label: "03-02", value: 2 },
							{ label: "03-03", value: 4 },
						],
						month_series: [],
						year_series: [],
					},
					{
						key: "AAPL",
						label: "苹果 (AAPL) · 3 股/份",
						summaryLabel: "苹果 (AAPL)",
						quantityLabel: "3 股/份",
						hour_series: [],
						day_series: [
							{ label: "03-01", value: 10 },
							{ label: "03-02", value: 11 },
							{ label: "03-03", value: 15 },
						],
						month_series: [],
						year_series: [],
					},
				]}
			/>,
		);

		expectPillToContain("当前持仓", "腾讯控股 (0700.HK)");
		expectPillToContain("持有股数", "120 股/份");
		expectPillToContain("终点收益率", "4.00%");
		expectPillToContain("区间变化", "+3.00%");

		fireEvent.click(screen.getByRole("button", { name: "选择终点时间点" }));
		const endDialog = screen.getByRole("dialog", { name: "选择终点时间点" });
		fireEvent.click(within(endDialog).getByRole("button", { name: "03-02" }));

		await waitFor(() => {
			expectPillToContain("终点收益率", "2.00%");
		});
		expectPillToContain("区间变化", "+1.00%");

		fireEvent.change(screen.getByLabelText("持仓"), {
			target: { value: "AAPL" },
		});

		await waitFor(() => {
			expectPillToContain("当前持仓", "苹果 (AAPL)");
		});
		expectPillToContain("持有股数", "3 股/份");
		expect(
			screen.getByRole("button", { name: "选择终点时间点" }).textContent,
		).toContain("03-02");
		expectPillToContain("终点收益率", "11.00%");
		expectPillToContain("区间变化", "+1.00%");
		expectPillToContain("区间内日均变动", "+1.00%");
	});
});
