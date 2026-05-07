import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AgentExecutionAuditPanel } from "./AgentExecutionAuditPanel";

afterEach(() => {
	cleanup();
	vi.useRealTimers();
});

describe("AgentExecutionAuditPanel", () => {
	it("shows only registered agents on the main surface and keeps direct API records inside the records dialog", () => {
		render(
			<AgentExecutionAuditPanel
				apiKeys={[
					{
						id: 8,
						name: "portfolio-agent",
						token_hint: "sk-por**********",
						created_at: "2026-03-14T10:00:00.000Z",
						updated_at: "2026-03-14T10:00:00.000Z",
						last_used_at: "2026-03-14T10:05:00.000Z",
						expires_at: null,
						revoked_at: null,
					},
				]}
				registrations={[
					{
						id: 3,
						user_id: "alice",
						name: "quant-runner",
						status: "ACTIVE",
						request_count: 12,
						latest_api_key_name: "portfolio-agent",
						last_used_at: "2026-03-11T10:00:02.000Z",
						last_seen_at: "2026-03-11T10:00:03.000Z",
						created_at: "2026-03-10T10:00:00.000Z",
						updated_at: "2026-03-11T10:00:03.000Z",
					},
				]}
				records={[
					{
						id: 1,
						source: "AGENT",
						api_key_name: "portfolio-agent",
						agent_name: "quant-runner",
						agent_task_id: 7,
						asset_class: "cash",
						operation_kind: "TRANSFER",
						entity_type: "CASH_TRANSFER",
						entity_id: 3,
						title: "账户划转",
						summary: "账户 #2 → 账户 #9 · 80 CNY",
						effective_date: "2026-03-10",
						amount: 80,
						currency: "CNY",
						created_at: "2026-03-10T10:00:01.000Z",
					},
					{
						id: 2,
						source: "API",
						api_key_name: "local-cli",
						agent_name: null,
						asset_class: "cash",
						operation_kind: "NEW",
						entity_type: "CASH_ACCOUNT",
						entity_id: 18,
						title: "API Sandbox Wallet",
						summary: "直连 API 创建的演示账户",
						effective_date: "2026-03-14",
						amount: 20,
						currency: "CNY",
						created_at: "2026-03-14T10:05:01.000Z",
					},
				]}
				apiDocUrl="https://github.com/RockYYY888/opentrifi/blob/main/docs/agent-api.md"
			/>,
		);

		expect(screen.getByText("quant-runner")).toBeTruthy();
		expect(screen.getByText("请求次数")).toBeTruthy();
		expect(screen.queryByText("API Sandbox Wallet")).toBeNull();
		expect(screen.queryByText("Agent 任务")).toBeNull();
		expect(screen.queryByText(/Agent-Name/)).toBeNull();
		expect(screen.queryByText(/Bearer/)).toBeNull();

		const summary = screen.getByTestId("agent-workspace-summary");
		expect(within(summary).getByText("活跃 Agent")).toBeTruthy();
		expect(within(summary).getByText("3 天内到期")).toBeTruthy();
		expect(within(summary).queryByText("非活跃 Agent")).toBeNull();
		expect(within(summary).queryByText("有效 Key")).toBeNull();

		fireEvent.click(screen.getByRole("button", { name: "查看记录" }));

		expect(screen.getByRole("heading", { name: "记录" })).toBeTruthy();
		expect(screen.getByText("账户划转")).toBeTruthy();
		expect(screen.getByText("API Sandbox Wallet")).toBeTruthy();
		expect(screen.getAllByText("直连 API").length).toBeGreaterThan(0);
	});

	it("opens the key management dialog, discards inactive keys, and requires revoke confirmation", () => {
		const revokeApiKey = vi.fn();

		render(
			<AgentExecutionAuditPanel
				apiKeys={[
					{
						id: 8,
						name: "local-cli",
						token_hint: "sk-loc**********",
						created_at: "2026-03-14T10:00:00.000Z",
						updated_at: "2026-03-14T10:00:00.000Z",
						last_used_at: "2026-03-14T10:05:00.000Z",
						expires_at: "2027-03-22T03:17:00.000Z",
						revoked_at: null,
					},
					{
						id: 9,
						name: "discarded-key",
						token_hint: "sk-dis**********",
						created_at: "2026-03-10T10:00:00.000Z",
						updated_at: "2026-03-10T10:00:00.000Z",
						last_used_at: "2026-03-10T10:05:00.000Z",
						expires_at: null,
						revoked_at: "2026-03-11T10:05:00.000Z",
					},
				]}
				registrations={[]}
				records={[]}
				apiDocUrl="https://github.com/RockYYY888/opentrifi/blob/main/docs/agent-api.md"
				onRevokeApiKey={revokeApiKey}
			/>,
		);

		fireEvent.click(screen.getByRole("button", { name: "有效 Key 1 / 5" }));

		expect(screen.getByRole("heading", { name: "有效 Key" })).toBeTruthy();
		expect(
			screen.getByText(
				"有效 Key 1 / 5。删除后会立即失效并释放名额。已删除或已过期的 API Key 将自动移除。",
			),
		).toBeTruthy();
		expect(screen.getByText("local-cli")).toBeTruthy();
		expect(screen.getAllByText("sk-lo***********").length).toBeGreaterThan(0);
		expect(screen.queryByText("discarded-key")).toBeNull();
		expect(screen.queryByRole("button", { name: "复制到剪贴板" })).toBeNull();
		expect(screen.getByText(/2027/)).toBeTruthy();

		fireEvent.click(screen.getByRole("button", { name: "删除" }));
		expect(screen.getByRole("heading", { name: "删除 API Key" })).toBeTruthy();
		expect(screen.getAllByText("local-cli").length).toBeGreaterThan(0);
		expect(revokeApiKey).not.toHaveBeenCalled();

		fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
		expect(revokeApiKey).toHaveBeenCalledWith(8);
	});

	it("marks soon-to-expire keys with a warning badge and updates the active count on rerender", () => {
		const now = new Date("2026-03-25T12:00:00.000Z");
		vi.useFakeTimers();
		vi.setSystemTime(now);

		const expiringKey = {
			id: 11,
			name: "rotation-window",
			token_hint: "sk-rot**********",
			created_at: "2026-03-20T10:00:00.000Z",
			updated_at: "2026-03-20T10:00:00.000Z",
			last_used_at: "2026-03-24T10:05:00.000Z",
			expires_at: "2026-03-27T12:00:00.000Z",
			revoked_at: null,
		} as const;
		const { rerender } = render(
			<AgentExecutionAuditPanel
				apiKeys={[expiringKey]}
				registrations={[]}
				records={[]}
				apiDocUrl="https://github.com/RockYYY888/opentrifi/blob/main/docs/agent-api.md"
			/>,
		);

		fireEvent.click(screen.getByRole("button", { name: "有效 Key 1 / 5" }));

		const warningBadge = screen.getByText("即将到期");
		expect(warningBadge.className).toContain("asset-manager__badge--warning");
		expect(
			screen.getByText("这个 API Key 将在 2 天内到期。建议提前完成轮换并更新调用方配置。"),
		).toBeTruthy();
		expect(screen.getAllByText("sk-ro***********").length).toBeGreaterThan(0);

		rerender(
			<AgentExecutionAuditPanel
				apiKeys={[]}
				registrations={[]}
				records={[]}
				apiDocUrl="https://github.com/RockYYY888/opentrifi/blob/main/docs/agent-api.md"
			/>,
		);

		expect(screen.getByRole("button", { name: "有效 Key 0 / 5" })).toBeTruthy();
		expect(
			screen.getByText(
				"有效 Key 0 / 5。删除后会立即失效并释放名额。已删除或已过期的 API Key 将自动移除。",
			),
		).toBeTruthy();
		expect(screen.getByText("当前账号还没有有效的 API Key。")).toBeTruthy();
	});

	it("falls back to a generic sk- mask for malformed token hints", () => {
		render(
			<AgentExecutionAuditPanel
				apiKeys={[
					{
						id: 10,
						name: "local-cli",
						token_hint: "...abc123",
						created_at: "2026-03-14T10:00:00.000Z",
						updated_at: "2026-03-14T10:00:00.000Z",
						last_used_at: "2026-03-14T10:05:00.000Z",
						expires_at: null,
						revoked_at: null,
					},
				]}
				registrations={[
					{
						id: 4,
						user_id: "admin",
						name: "portfolio-agent",
						status: "ACTIVE",
						request_count: 2,
						latest_api_key_name: "local-cli",
						last_used_at: "2026-03-14T10:05:00.000Z",
						last_seen_at: "2026-03-14T10:05:00.000Z",
						created_at: "2026-03-14T10:00:00.000Z",
						updated_at: "2026-03-14T10:05:00.000Z",
					},
				]}
				records={[]}
				apiDocUrl="https://github.com/RockYYY888/opentrifi/blob/main/docs/agent-api.md"
			/>,
		);

		expect(screen.getAllByText("sk-xx***********").length).toBeGreaterThan(0);

		fireEvent.click(screen.getByRole("button", { name: "有效 Key 1 / 5" }));
		expect(screen.getAllByText("sk-xx***********").length).toBeGreaterThan(0);
	});

	it("validates key naming rules and includes expiry selection when creating a new api key", () => {
		const createApiKey = vi.fn();

		render(
			<AgentExecutionAuditPanel
				apiKeys={[]}
				registrations={[]}
				records={[]}
				apiDocUrl="https://github.com/RockYYY888/opentrifi/blob/main/docs/agent-api.md"
				onCreateApiKey={createApiKey}
			/>,
		);

		fireEvent.click(screen.getByRole("button", { name: "创建新的 API Key" }));
		fireEvent.change(screen.getByLabelText("Key 名称"), {
			target: { value: "nightly-1" },
		});
		expect(
			screen.getByText("API Key 名称只能使用小写字母和连字符，不支持数字、空格或其他符号。"),
		).toBeTruthy();

		fireEvent.change(screen.getByLabelText("Key 名称"), {
			target: { value: "nightly-worker" },
		});
		fireEvent.change(screen.getByLabelText("有效期"), {
			target: { value: "365" },
		});
		fireEvent.click(screen.getByRole("button", { name: "生成 API Key" }));

		expect(createApiKey).toHaveBeenCalledWith({
			name: "nightly-worker",
			expires_in_days: 365,
		});
	});

	it("creates and copies a newly issued api key through the create dialog", async () => {
		const dismissIssuedApiKey = vi.fn();
		const clipboardWriteText = vi.fn().mockResolvedValue(undefined);
		Object.assign(globalThis.navigator, {
			clipboard: {
				writeText: clipboardWriteText,
			},
		});

		render(
			<AgentExecutionAuditPanel
				apiKeys={[]}
				registrations={[]}
				records={[]}
				apiDocUrl="https://github.com/RockYYY888/opentrifi/blob/main/docs/agent-api.md"
				issuedApiKey={{
					id: 9,
					name: "nightly-worker",
					token_hint: "sk-nig**********",
					access_token: "sk_secret_key",
					created_at: "2026-03-14T10:10:00.000Z",
					updated_at: "2026-03-14T10:10:00.000Z",
					last_used_at: null,
					expires_at: "2026-04-14T10:10:00.000Z",
					revoked_at: null,
				}}
				onDismissIssuedApiKey={dismissIssuedApiKey}
			/>,
		);

		expect(screen.getByRole("heading", { name: "新 API Key" })).toBeTruthy();
		expect(screen.getByText("sk_secret_key")).toBeTruthy();
		expect(screen.getByText("sk-ni***********")).toBeTruthy();

		fireEvent.click(screen.getByRole("button", { name: "复制到剪贴板" }));
		expect(clipboardWriteText).toHaveBeenCalledWith("sk_secret_key");

		fireEvent.click(screen.getByRole("button", { name: "我已保存" }));
		expect(dismissIssuedApiKey).toHaveBeenCalledTimes(1);
	});
});
