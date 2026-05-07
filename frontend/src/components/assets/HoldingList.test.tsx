import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { HoldingList } from "./HoldingList";

afterEach(() => {
	cleanup();
});

describe("HoldingList actions", () => {
	it("renders separate buy and sell entry points and keeps delete out of the list", () => {
		const onCreateBuy = vi.fn();
		const onCreateSell = vi.fn();
		const onEdit = vi.fn();

		render(
			<HoldingList
				holdings={[
					{
						id: 1,
						side: "BUY",
						symbol: "AAPL",
						name: "Apple",
						quantity: 10,
						fallback_currency: "USD",
						market: "US",
						started_on: "2026-03-05",
						price: 185,
						price_currency: "USD",
						value_cny: 9000,
						return_pct: 5.2,
						last_updated: "2026-03-05T08:00:00Z",
					},
				]}
				onCreateBuy={onCreateBuy}
				onCreateSell={onCreateSell}
				onEdit={onEdit}
			/>,
		);

		fireEvent.click(screen.getByRole("button", { name: "新增买入" }));
		fireEvent.click(screen.getByRole("button", { name: "新增卖出" }));
		fireEvent.click(screen.getByRole("button", { name: "编辑" }));

		expect(screen.getByRole("button", { name: "新增买入" }).className).toContain(
			"asset-manager__button--primary",
		);
		expect(screen.getByRole("button", { name: "新增卖出" }).className).toContain(
			"asset-manager__button--danger",
		);

		expect(onCreateBuy).toHaveBeenCalledTimes(1);
		expect(onCreateSell).toHaveBeenCalledTimes(1);
		expect(onEdit).toHaveBeenCalledTimes(1);
		expect(screen.queryByRole("button", { name: "删除" })).toBeNull();
		expect(screen.queryByRole("button", { name: "记一笔" })).toBeNull();
	});

	it("keeps holdings visible while quotes refresh in the background", () => {
		render(
			<HoldingList
				loading
				holdings={[
					{
						id: 1,
						side: "BUY",
						symbol: "AAPL",
						name: "Apple",
						quantity: 10,
						fallback_currency: "USD",
						market: "US",
						started_on: "2026-03-05",
						price: 185,
						price_currency: "USD",
						value_cny: 9000,
						return_pct: 5.2,
						last_updated: "2026-03-05T08:00:00Z",
					},
				]}
			/>,
		);

		expect(screen.getByText("Apple")).not.toBeNull();
		expect(screen.getByText("正在更新投资类持仓...")).not.toBeNull();
		expect(screen.queryByText("正在加载投资类资产...")).toBeNull();
	});
});
