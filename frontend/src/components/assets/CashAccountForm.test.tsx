import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CashAccountForm } from "./CashAccountForm";
import { CashAccountList } from "./CashAccountList";
import { CASH_ACCOUNT_DELETE_DESCRIPTION } from "./cashAccountDeleteCopy";

afterEach(() => {
	cleanup();
});

describe("CashAccountForm reset behavior", () => {
	it("keeps user input when upstream value changes without a new editor session", () => {
		const { rerender } = render(
			<CashAccountForm
				resetKey={0}
				value={{
					name: "",
					currency: "CNY",
					balance: "",
				}}
			/>,
		);

		fireEvent.change(screen.getByLabelText("账户名称"), {
			target: { value: "旅行备用金" },
		});
		fireEvent.change(screen.getByLabelText("当前币种余额"), {
			target: { value: "2560" },
		});

		rerender(
			<CashAccountForm
				resetKey={0}
				value={{
					name: "",
					currency: "USD",
					balance: "",
				}}
			/>,
		);

		expect((screen.getByLabelText("账户名称") as HTMLInputElement).value).toBe("旅行备用金");
		expect((screen.getByLabelText("当前币种余额") as HTMLInputElement).value).toBe("2560");
	});

	it("resets draft only when a new editor session starts", () => {
		const { rerender } = render(
			<CashAccountForm
				resetKey={0}
				value={{
					name: "旧账户",
					currency: "CNY",
					balance: "1000",
				}}
			/>,
		);

		fireEvent.change(screen.getByLabelText("账户名称"), {
			target: { value: "临时输入" },
		});

		rerender(
			<CashAccountForm
				resetKey={1}
				value={{
					name: "新账户",
					currency: "USD",
					balance: "88",
				}}
			/>,
		);

		expect((screen.getByLabelText("账户名称") as HTMLInputElement).value).toBe("新账户");
		expect((screen.getByLabelText("当前币种") as HTMLSelectElement).value).toBe("USD");
	});

	it("shows readonly target cny valuation from the selected currency", () => {
		render(
			<CashAccountForm
				value={{
					name: "美元账户",
					currency: "USD",
					balance: "10",
				}}
				fxRates={{ USD: 7 }}
			/>,
		);

		expect((screen.getByLabelText("目标币种") as HTMLInputElement).value).toBe("CNY");
		expect((screen.getByLabelText("目标币种估值（CNY）") as HTMLInputElement).value).toBe("¥70.00");
	});
});

describe("Cash account button styling", () => {
	it("uses the investment palette in the cash account form", () => {
		const onEdit = vi.fn();
		const onDelete = vi.fn();

		render(
			<CashAccountForm
				mode="edit"
				recordId={1}
				value={{
					name: "主账户",
					currency: "CNY",
					balance: "1000",
				}}
				onEdit={onEdit}
				onDelete={onDelete}
				onCancel={vi.fn()}
			/>,
		);

		expect(screen.getByRole("button", { name: "编辑" }).className).toContain(
			"asset-manager__button--primary",
		);
		expect(screen.getByRole("button", { name: "删除账户" }).className).toContain(
			"asset-manager__button--danger",
		);
	});

	it("uses the investment palette in the cash account list", () => {
		const onCreate = vi.fn();
		const onTransfer = vi.fn();
		const onEdit = vi.fn();
		const onDelete = vi.fn();

		render(
			<CashAccountList
				accounts={[
					{
						id: 1,
						name: "主账户",
						platform: "Bank",
						currency: "CNY",
						balance: 1000,
						account_type: "BANK",
						value_cny: 1000,
					},
			]}
				onCreate={onCreate}
				onTransfer={onTransfer}
				onEdit={onEdit}
				onDelete={onDelete}
			/>,
		);

		expect(screen.getByRole("button", { name: "账户划转" }).className).toContain(
			"asset-manager__button--secondary",
		);
		expect(screen.getByRole("button", { name: "新增" }).className).toContain(
			"asset-manager__button--primary",
		);
		expect(screen.getByRole("button", { name: "删除" }).className).toContain(
			"asset-manager__button--danger",
		);
	});

	it("keeps cash accounts visible while the list refreshes", () => {
		render(
			<CashAccountList
				loading
				accounts={[
					{
						id: 1,
						name: "主账户",
						platform: "Bank",
						currency: "CNY",
						balance: 1000,
						account_type: "BANK",
						value_cny: 1000,
					},
				]}
			/>,
		);

		expect(screen.getByText("主账户")).not.toBeNull();
		expect(screen.getByText("正在更新现金账户...")).not.toBeNull();
		expect(screen.queryByText("正在加载现金账户...")).toBeNull();
	});

	it("keeps the cash account editor focused on the form only", () => {
		render(
			<CashAccountForm
				mode="edit"
				recordId={1}
				value={{
					name: "主账户",
					currency: "CNY",
					balance: "1000",
				}}
				activityAccount={{
					id: 1,
					name: "主账户",
					platform: "Bank",
					currency: "CNY",
					balance: 1000,
					account_type: "BANK",
				}}
				onEdit={vi.fn()}
				onCancel={vi.fn()}
			/>,
		);

		expect(screen.queryByRole("heading", { name: "账户变动记录" })).toBeNull();
		expect(screen.queryByRole("tab", { name: "全部" })).toBeNull();
	});

	it("asks for confirmation before deleting a cash account from the form", async () => {
		const onDelete = vi.fn().mockResolvedValue(undefined);

		render(
			<CashAccountForm
				mode="edit"
				recordId={1}
				value={{
					name: "主账户",
					currency: "CNY",
					balance: "1000",
				}}
				onEdit={vi.fn()}
				onDelete={onDelete}
				onCancel={vi.fn()}
			/>,
		);

		fireEvent.click(screen.getByRole("button", { name: "删除账户" }));

		expect(screen.getByRole("dialog")).not.toBeNull();
		expect(screen.getByText(CASH_ACCOUNT_DELETE_DESCRIPTION)).not.toBeNull();
		expect(screen.getByRole("button", { name: "取消" })).not.toBeNull();
		expect(onDelete).not.toHaveBeenCalled();

		fireEvent.click(screen.getByRole("button", { name: "确认删除" }));

		await waitFor(() => {
			expect(onDelete).toHaveBeenCalledWith(1);
		});
		await waitFor(() => {
			expect(screen.queryByRole("dialog")).toBeNull();
		});
	});

	it("asks for confirmation before deleting a cash account from the list", async () => {
		const onDelete = vi.fn().mockResolvedValue(undefined);

		render(
			<CashAccountList
				accounts={[
					{
						id: 1,
						name: "主账户",
						platform: "Bank",
						currency: "CNY",
						balance: 1000,
						account_type: "BANK",
						value_cny: 1000,
					},
				]}
				onDelete={onDelete}
			/>,
		);

		fireEvent.click(screen.getByRole("button", { name: "删除" }));

		expect(screen.getByRole("dialog")).not.toBeNull();
		expect(screen.getByText(CASH_ACCOUNT_DELETE_DESCRIPTION)).not.toBeNull();
		expect(screen.getByRole("button", { name: "取消" })).not.toBeNull();
		expect(onDelete).not.toHaveBeenCalled();

		fireEvent.click(screen.getByRole("button", { name: "确认删除" }));

		await waitFor(() => {
			expect(onDelete).toHaveBeenCalledWith(1);
		});
		await waitFor(() => {
			expect(screen.queryByRole("dialog")).toBeNull();
		});
	});

	it("keeps the delete dialog open when list deletion fails", async () => {
		const onDelete = vi.fn().mockRejectedValue(new Error("删除失败"));

		render(
			<CashAccountList
				accounts={[
					{
						id: 1,
						name: "主账户",
						platform: "Bank",
						currency: "CNY",
						balance: 1000,
						account_type: "BANK",
						value_cny: 1000,
					},
				]}
				onDelete={onDelete}
			/>,
		);

		fireEvent.click(screen.getByRole("button", { name: "删除" }));
		fireEvent.click(screen.getByRole("button", { name: "确认删除" }));

		await waitFor(() => {
			expect(onDelete).toHaveBeenCalledWith(1);
		});
		expect(screen.getByRole("dialog")).not.toBeNull();
		expect(screen.getByText("删除失败")).not.toBeNull();
	});
});
