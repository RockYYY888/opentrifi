import { describe, expect, it, vi } from "vitest";

import { createApiClient, parseDecimalString } from "./apiClient";

describe("apiClient server error handling", () => {
	it("replaces generic 5xx error text with a friendly fallback", async () => {
		const fetcher = vi.fn().mockResolvedValue(
			new Response("Internal Server Error", {
				status: 500,
				headers: {
					"Content-Type": "text/plain",
				},
			}),
		);
		const client = createApiClient({ fetcher });

		await expect(client.request("/api/dashboard")).rejects.toThrow(
			"服务器暂时不可用，请稍后再试。",
		);
	});

	it("keeps custom server detail when the backend provides user-facing 5xx copy", async () => {
		const fetcher = vi.fn().mockResolvedValue(
			new Response(JSON.stringify({ detail: "系统维护中，请稍后重试。" }), {
				status: 503,
				headers: {
					"Content-Type": "application/json",
				},
			}),
		);
		const client = createApiClient({ fetcher });

		await expect(client.request("/api/dashboard")).rejects.toThrow(
			"系统维护中，请稍后重试。",
		);
	});

	it("preserves authentication errors instead of masking them as server errors", async () => {
		const fetcher = vi.fn().mockResolvedValue(
			new Response(JSON.stringify({ detail: "API Key 无效。" }), {
				status: 401,
				headers: {
					"Content-Type": "application/json",
				},
			}),
		);
		const client = createApiClient({ fetcher });

		await expect(client.request("/api/auth/session")).rejects.toThrow("API Key 无效。");
	});

	it("normalizes decimal string fields at the API boundary", async () => {
		const fetcher = vi.fn().mockResolvedValue(
			new Response(
				JSON.stringify({
					total_value_cny: "0.30",
					holdings: [
						{
							symbol: "00700.HK",
							quantity: "1200.00000000",
							price: "69.70000000",
						},
					],
					symbol: "00700.HK",
				}),
				{
					status: 200,
					headers: {
						"Content-Type": "application/json",
					},
				},
			),
		);
		const client = createApiClient({ fetcher });

		const payload = await client.request<{
			total_value_cny: number;
			holdings: Array<{ symbol: string; quantity: number; price: number }>;
			symbol: string;
		}>("/api/dashboard");

		expect(payload.total_value_cny).toBe(0.3);
		expect(payload.holdings[0].quantity).toBe(1200);
		expect(payload.holdings[0].price).toBe(69.7);
		expect(payload.holdings[0].symbol).toBe("00700.HK");
		expect(payload.symbol).toBe("00700.HK");
	});

	it("parses decimal strings explicitly for adapter tests", () => {
		expect(parseDecimalString("0.30")).toBe(0.3);
		expect(parseDecimalString(0.3)).toBe(0.3);
	});
});
