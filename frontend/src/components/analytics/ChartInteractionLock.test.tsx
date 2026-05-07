import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AllocationChart } from "./AllocationChart";
import { HoldingsBreakdownChart } from "./HoldingsBreakdownChart";
import { PlatformBreakdownChart } from "./PlatformBreakdownChart";
import { PortfolioTrendChart } from "./PortfolioTrendChart";
import { ReturnTrendChart, createAggregateReturnOption } from "./ReturnTrendChart";

const rechartsState = vi.hoisted(() => ({
	tooltips: [] as Array<Record<string, unknown>>,
}));

vi.mock("recharts", () => ({
	ResponsiveContainer: ({ children }: { children?: ReactNode }) => <>{children}</>,
	ComposedChart: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
	BarChart: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
	PieChart: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
	CartesianGrid: () => null,
	Tooltip: (props: Record<string, unknown>) => {
		rechartsState.tooltips.push(props);
		return null;
	},
	ReferenceLine: () => null,
	Area: () => null,
	Line: () => null,
	XAxis: () => null,
	YAxis: () => null,
	Pie: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
	Bar: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
	Cell: () => null,
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

describe("analytics chart interaction lock", () => {
	beforeEach(() => {
		rechartsState.tooltips.length = 0;
		vi.stubGlobal("ResizeObserver", MockResizeObserver);
		vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(() => ({
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
		document.documentElement.style.overflow = "";
		document.documentElement.style.overscrollBehavior = "";
		document.body.style.overflow = "";
		document.body.style.overscrollBehavior = "";
		vi.unstubAllGlobals();
		vi.restoreAllMocks();
		rechartsState.tooltips.length = 0;
	});

	it("applies interactive chart guards to every analytics chart and locks body scroll on touch", async () => {
		const { container } = render(
			<div>
				<AllocationChart
					total_value_cny={100}
					allocation={[{ label: "现金", value: 100 }]}
					cash_accounts={[
						{
							id: 1,
							name: "零钱",
							platform: "微信",
							balance: 100,
							currency: "CNY",
							account_type: "WECHAT",
							fx_to_cny: 1,
							value_cny: 100,
						},
					]}
					holdings={[]}
					fixed_assets={[]}
					other_assets={[]}
				/>
				<PortfolioTrendChart
					defaultRange="day"
					hour_series={[]}
					day_series={[
						{ label: "03-01", value: 100 },
						{ label: "03-02", value: 120 },
					]}
					month_series={[]}
					year_series={[]}
				/>
				<ReturnTrendChart
					title="收益"
					description="测试"
					defaultRange="day"
					seriesOptions={[
						createAggregateReturnOption(
							"非现金资产",
							[],
							[
								{ label: "03-01", value: 1 },
								{ label: "03-02", value: 2 },
							],
							[],
							[],
						),
					]}
				/>
				<HoldingsBreakdownChart
					holdings={[
						{
							id: 1,
							symbol: "0700.HK",
							name: "腾讯控股",
							quantity: 1,
							fallback_currency: "HKD",
							market: "HK",
							price: 100,
							price_currency: "HKD",
							fx_to_cny: 1,
							value_cny: 100,
							last_updated: null,
						},
					]}
				/>
				<PlatformBreakdownChart
					cash_accounts={[
						{
							id: 1,
							name: "零钱",
							platform: "微信",
							balance: 100,
							currency: "CNY",
							account_type: "WECHAT",
							fx_to_cny: 1,
							value_cny: 100,
						},
					]}
					holdings={[]}
					fixed_assets={[]}
					liabilities={[]}
					other_assets={[]}
				/>
			</div>,
		);

		const interactiveCharts = container.querySelectorAll(".analytics-chart--interactive");
		expect(interactiveCharts).toHaveLength(6);

		fireEvent.touchStart(interactiveCharts[0]!);

		await waitFor(() => {
			expect(document.documentElement.style.overflow).toBe("hidden");
			expect(document.documentElement.style.overscrollBehavior).toBe("none");
			expect(document.body.style.overflow).toBe("hidden");
			expect(document.body.style.overscrollBehavior).toBe("none");
		});

		fireEvent.touchEnd(interactiveCharts[0]!);

		await waitFor(() => {
			expect(document.documentElement.style.overflow).toBe("");
			expect(document.documentElement.style.overscrollBehavior).toBe("");
			expect(document.body.style.overflow).toBe("");
			expect(document.body.style.overscrollBehavior).toBe("");
		});
	});

	it("clears touch tooltips after the user lifts their finger", async () => {
		const { container } = render(
			<PortfolioTrendChart
				defaultRange="day"
				hour_series={[]}
				day_series={[
					{ label: "03-01", value: 100 },
					{ label: "03-02", value: 120 },
				]}
				month_series={[]}
				year_series={[]}
			/>,
		);

		const interactiveChart = container.querySelector(".analytics-chart--interactive");
		expect(interactiveChart).not.toBeNull();
		expect(rechartsState.tooltips.every((props) => props.active === undefined)).toBe(true);

		rechartsState.tooltips.length = 0;
		fireEvent.pointerDown(interactiveChart as Element, { pointerType: "touch" });

		await waitFor(() => {
			expect(rechartsState.tooltips.some((props) => props.active === true)).toBe(true);
		});

		rechartsState.tooltips.length = 0;
		fireEvent.pointerUp(interactiveChart as Element, { pointerType: "touch" });

		await waitFor(() => {
			expect(rechartsState.tooltips.some((props) => props.active === false)).toBe(true);
		});
	});

	it("keeps mouse hover tooltips uncontrolled", async () => {
		const { container } = render(
			<PortfolioTrendChart
				defaultRange="day"
				hour_series={[]}
				day_series={[
					{ label: "03-01", value: 100 },
					{ label: "03-02", value: 120 },
				]}
				month_series={[]}
				year_series={[]}
			/>,
		);

		const interactiveChart = container.querySelector(".analytics-chart--interactive");
		expect(interactiveChart).not.toBeNull();

		rechartsState.tooltips.length = 0;
		fireEvent.pointerMove(interactiveChart as Element, { pointerType: "mouse" });

		await waitFor(() => {
			expect(rechartsState.tooltips.length).toBeGreaterThan(0);
		});
		expect(rechartsState.tooltips.every((props) => props.active === undefined)).toBe(true);
	});
});
