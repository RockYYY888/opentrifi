import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import {
	__resetAutoRefreshGuardsForTests,
	__setAutoRefreshGuardForTests,
} from "./lib/autoRefreshGuards";
import { EMPTY_DASHBOARD } from "./types/dashboard";

const STORAGE_KEY = "asset-tracker-last-session-user";
const DASHBOARD_CACHE_KEY_PREFIX = "asset-tracker-dashboard-cache:";

const authApiMocks = vi.hoisted(() => ({
	getAuthSession: vi.fn(),
	loginWithPassword: vi.fn(),
	logoutCurrentUser: vi.fn(),
	registerWithPassword: vi.fn(),
	resetPasswordWithEmail: vi.fn(),
	updateCurrentUserEmail: vi.fn(),
}));

const dashboardApiMocks = vi.hoisted(() => ({
	getDashboard: vi.fn(),
}));

const assetApiMocks = vi.hoisted(() => ({
	createAssetManagerController: vi.fn(() => ({})),
	createAgentApiKey: vi.fn(),
	listAgentApiKeys: vi.fn(),
	listAgentRegistrations: vi.fn(),
	listAgentTasks: vi.fn(),
	listAssetRecords: vi.fn(),
	revokeAgentApiKey: vi.fn(),
}));

const feedbackApiMocks = vi.hoisted(() => ({
	submitUserFeedback: vi.fn(),
	getFeedbackSummary: vi.fn(),
	listFeedbackForCurrentUser: vi.fn(),
	markFeedbackSeenForCurrentUser: vi.fn(),
	listUserFeedbackForAdmin: vi.fn(),
	listSystemFeedbackForAdmin: vi.fn(),
	replyToFeedbackForAdmin: vi.fn(),
	closeFeedbackForAdmin: vi.fn(),
	hideInboxMessageForCurrentUser: vi.fn(),
	listReleaseNotesForCurrentUser: vi.fn(),
	markReleaseNotesSeenForCurrentUser: vi.fn(),
	listReleaseNotesForAdmin: vi.fn(),
	createReleaseNoteForAdmin: vi.fn(),
	publishReleaseNoteForAdmin: vi.fn(),
}));

const assetRecordsDialogMocks = vi.hoisted(() => ({
	lastOpenState: false,
}));

const assetManagerMocks = vi.hoisted(() => ({
	lastProps: null as Record<string, unknown> | null,
}));

const analyticsMocks = vi.hoisted(() => ({
	lastProps: null as Record<string, unknown> | null,
}));

const agentAuditPanelMocks = vi.hoisted(() => ({
	lastProps: null as Record<string, unknown> | null,
}));

const adminFeedbackDialogMocks = vi.hoisted(() => ({
	lastProps: null as Record<string, unknown> | null,
}));

vi.mock("./lib/authApi", () => ({
	getAuthSession: authApiMocks.getAuthSession,
	loginWithPassword: authApiMocks.loginWithPassword,
	logoutCurrentUser: authApiMocks.logoutCurrentUser,
	registerWithPassword: authApiMocks.registerWithPassword,
	resetPasswordWithEmail: authApiMocks.resetPasswordWithEmail,
	updateCurrentUserEmail: authApiMocks.updateCurrentUserEmail,
}));

vi.mock("./lib/dashboardApi", () => ({
	getDashboard: dashboardApiMocks.getDashboard,
}));

vi.mock("./lib/assetApi", () => ({
	createAssetManagerController: assetApiMocks.createAssetManagerController,
	defaultAssetApiClient: {
		createAgentApiKey: assetApiMocks.createAgentApiKey,
		listAgentApiKeys: assetApiMocks.listAgentApiKeys,
		listAgentRegistrations: assetApiMocks.listAgentRegistrations,
		listAgentTasks: assetApiMocks.listAgentTasks,
		listAssetRecords: assetApiMocks.listAssetRecords,
		revokeAgentApiKey: assetApiMocks.revokeAgentApiKey,
	},
}));

vi.mock("./components/auth/LoginScreen", () => ({
	LoginScreen: ({
		onLogin,
		errorMessage,
		checkingSession,
	}: {
		onLogin: (payload: { user_id: string; password: string }) => Promise<void>;
		errorMessage?: string | null;
		checkingSession?: boolean;
	}) => (
		<div data-testid="login-screen">
			登录页
			{checkingSession ? <p>检查登录中</p> : null}
			{errorMessage ? <p>{errorMessage}</p> : null}
			<button
				type="button"
				onClick={() => void onLogin({ user_id: "bob", password: "secret-password" })}
			>
				模拟登录
			</button>
		</div>
	),
}));

vi.mock("./components/assets", () => ({
	AssetManager: (props: Record<string, unknown>) => {
		assetManagerMocks.lastProps = props;
		return <div data-testid="asset-manager">资产模块</div>;
	},
}));

vi.mock("./components/analytics", () => ({
	PortfolioAnalytics: (props: Record<string, unknown>) => {
		analyticsMocks.lastProps = props;
		return <div data-testid="portfolio-analytics">分析模块</div>;
	},
}));

vi.mock("./components/assets/AgentExecutionAuditPanel", () => ({
	AgentExecutionAuditPanel: (props: Record<string, unknown>) => {
		agentAuditPanelMocks.lastProps = props;
		const loading = props.loading === true;
		return (
			<div data-testid="agent-audit-panel">
				{loading ? "智能体加载中" : "智能体模块"}
				<button
					type="button"
					onClick={() => void (props.onCreateApiKey as ((payload: {
						name: string;
						expires_in_days: number | null;
					}) => void) | undefined)?.({
						name: "local-cli",
						expires_in_days: 30,
					})}
				>
					模拟创建 API Key
				</button>
				<button
					type="button"
					onClick={() => (props.onDismissIssuedApiKey as (() => void) | undefined)?.()}
				>
					模拟关闭新 Key
				</button>
			</div>
		);
	},
}));

vi.mock("./components/assets/AssetRecordsDialog", () => ({
	AssetRecordsDialog: ({ open }: { open: boolean }) => {
		assetRecordsDialogMocks.lastOpenState = open;
		return open ? <div data-testid="asset-records-dialog">记录弹窗</div> : null;
	},
}));

vi.mock("./components/feedback/FeedbackDialog", () => ({
	FeedbackDialog: () => null,
}));

vi.mock("./components/feedback/AdminFeedbackDialog", () => ({
	AdminFeedbackDialog: (props: Record<string, unknown>) => {
		adminFeedbackDialogMocks.lastProps = props;
		return props.open ? <div data-testid="admin-feedback-dialog">管理员消息</div> : null;
	},
}));
vi.mock("./components/feedback/AdminReleaseNotesDialog", () => ({
	AdminReleaseNotesDialog: () => null,
}));

vi.mock("./components/feedback/UserFeedbackInboxDialog", () => ({
	UserFeedbackInboxDialog: () => null,
}));

vi.mock("./lib/feedbackApi", () => ({
	submitUserFeedback: feedbackApiMocks.submitUserFeedback,
	getFeedbackSummary: feedbackApiMocks.getFeedbackSummary,
	listFeedbackForCurrentUser: feedbackApiMocks.listFeedbackForCurrentUser,
	markFeedbackSeenForCurrentUser: feedbackApiMocks.markFeedbackSeenForCurrentUser,
	listUserFeedbackForAdmin: feedbackApiMocks.listUserFeedbackForAdmin,
	listSystemFeedbackForAdmin: feedbackApiMocks.listSystemFeedbackForAdmin,
	replyToFeedbackForAdmin: feedbackApiMocks.replyToFeedbackForAdmin,
	closeFeedbackForAdmin: feedbackApiMocks.closeFeedbackForAdmin,
	hideInboxMessageForCurrentUser: feedbackApiMocks.hideInboxMessageForCurrentUser,
	listReleaseNotesForCurrentUser: feedbackApiMocks.listReleaseNotesForCurrentUser,
	markReleaseNotesSeenForCurrentUser: feedbackApiMocks.markReleaseNotesSeenForCurrentUser,
	listReleaseNotesForAdmin: feedbackApiMocks.listReleaseNotesForAdmin,
	createReleaseNoteForAdmin: feedbackApiMocks.createReleaseNoteForAdmin,
	publishReleaseNoteForAdmin: feedbackApiMocks.publishReleaseNoteForAdmin,
}));

function createDeferredPromise<T>() {
	let resolvePromise!: (value: T | PromiseLike<T>) => void;
	let rejectPromise!: (reason?: unknown) => void;

	const promise = new Promise<T>((resolve, reject) => {
		resolvePromise = resolve;
		rejectPromise = reject;
	});

	return {
		promise,
		resolve: resolvePromise,
		reject: rejectPromise,
	};
}

async function flushMicrotasks(): Promise<void> {
	await Promise.resolve();
	await Promise.resolve();
}

describe("App session restore", () => {
	afterEach(() => {
		cleanup();
	});

	beforeEach(() => {
		vi.clearAllMocks();
		vi.useRealTimers();
		assetRecordsDialogMocks.lastOpenState = false;
		assetManagerMocks.lastProps = null;
		analyticsMocks.lastProps = null;
		agentAuditPanelMocks.lastProps = null;
		adminFeedbackDialogMocks.lastProps = null;
		__resetAutoRefreshGuardsForTests();
		window.sessionStorage.clear();
		window.localStorage.clear();
		authApiMocks.loginWithPassword.mockResolvedValue({ user_id: "bob", email: null });
		authApiMocks.registerWithPassword.mockResolvedValue({ user_id: "bob", email: null });
		authApiMocks.resetPasswordWithEmail.mockResolvedValue({
			message: "密码已重置，请使用新密码登录。",
		});
		authApiMocks.logoutCurrentUser.mockResolvedValue(undefined);
		authApiMocks.updateCurrentUserEmail.mockResolvedValue({ user_id: "alice", email: null });
		assetApiMocks.createAgentApiKey.mockResolvedValue({
			id: 1,
			name: "local-cli",
			token_hint: "...abc123",
			access_token: "sk_demo_key",
			created_at: new Date().toISOString(),
			updated_at: new Date().toISOString(),
			last_used_at: null,
			expires_at: null,
			revoked_at: null,
		});
		assetApiMocks.listAgentApiKeys.mockResolvedValue([]);
		feedbackApiMocks.getFeedbackSummary.mockResolvedValue({
			inbox_count: 0,
			mode: "user-pending",
		});
		assetApiMocks.listAgentRegistrations.mockResolvedValue([]);
		assetApiMocks.listAgentTasks.mockResolvedValue([]);
		assetApiMocks.listAssetRecords.mockResolvedValue([]);
		feedbackApiMocks.listFeedbackForCurrentUser.mockResolvedValue([]);
		feedbackApiMocks.markFeedbackSeenForCurrentUser.mockResolvedValue(undefined);
		feedbackApiMocks.listUserFeedbackForAdmin.mockResolvedValue({
			items: [],
			total: 0,
			page: 1,
			page_size: 200,
			has_more: false,
		});
		feedbackApiMocks.listSystemFeedbackForAdmin.mockResolvedValue({
			items: [],
			total: 0,
			page: 1,
			page_size: 200,
			has_more: false,
		});
		feedbackApiMocks.hideInboxMessageForCurrentUser.mockResolvedValue(undefined);
		feedbackApiMocks.listReleaseNotesForCurrentUser.mockResolvedValue([]);
		feedbackApiMocks.markReleaseNotesSeenForCurrentUser.mockResolvedValue(undefined);
		feedbackApiMocks.listReleaseNotesForAdmin.mockResolvedValue([]);
		feedbackApiMocks.createReleaseNoteForAdmin.mockResolvedValue({
			id: 1,
			version: "0.2.0",
			title: "Release Notes",
			content: "Content",
			source_feedback_ids: [],
			created_by: "admin",
			created_at: new Date().toISOString(),
			published_at: null,
			delivery_count: 0,
		});
		feedbackApiMocks.publishReleaseNoteForAdmin.mockResolvedValue({
			id: 1,
			version: "0.2.0",
			title: "Release Notes",
			content: "Content",
			source_feedback_ids: [],
			created_by: "admin",
			created_at: new Date().toISOString(),
			published_at: new Date().toISOString(),
			delivery_count: 1,
		});
		feedbackApiMocks.replyToFeedbackForAdmin.mockResolvedValue({
			id: 1,
			user_id: "alice",
			message: "msg",
			category: "USER_REQUEST",
			priority: "MEDIUM",
			source: "USER",
			status: "IN_PROGRESS",
			is_system: false,
			reply_message: "reply",
			replied_at: new Date().toISOString(),
			replied_by: "admin",
			reply_seen_at: null,
			resolved_at: null,
			closed_by: null,
			created_at: new Date().toISOString(),
		});
		feedbackApiMocks.closeFeedbackForAdmin.mockResolvedValue({
			id: 1,
			user_id: "alice",
			message: "msg",
			category: "USER_REQUEST",
			priority: "MEDIUM",
			source: "USER",
			status: "RESOLVED",
			is_system: false,
			reply_message: null,
			replied_at: null,
			replied_by: null,
			reply_seen_at: null,
			resolved_at: null,
			closed_by: null,
			created_at: new Date().toISOString(),
		});
		dashboardApiMocks.getDashboard.mockResolvedValue({ ...EMPTY_DASHBOARD });
	});

	it("shows a neutral recovery shell while restoring a remembered session", async () => {
		const pendingSession = createDeferredPromise<{ user_id: string; email: string | null }>();
		authApiMocks.getAuthSession.mockReturnValue(pendingSession.promise);
		window.sessionStorage.setItem(STORAGE_KEY, "alice");

		render(<App />);

		expect(screen.queryByTestId("login-screen")).toBeNull();
		expect(screen.getByText("正在恢复登录状态")).not.toBeNull();
		expect(screen.queryByText("你好，alice")).toBeNull();
		expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(6);

		pendingSession.resolve({ user_id: "alice", email: null });

		await waitFor(() => {
			expect(dashboardApiMocks.getDashboard).toHaveBeenCalledWith(false);
		});
		expect(screen.getByText("你好，alice")).not.toBeNull();
	});

	it("keeps cached dashboard totals hidden until session confirmation completes", () => {
		const pendingSession = createDeferredPromise<{ user_id: string; email: string | null }>();
		authApiMocks.getAuthSession.mockReturnValue(pendingSession.promise);
		window.sessionStorage.setItem(STORAGE_KEY, "alice");
		window.sessionStorage.setItem(
			`${DASHBOARD_CACHE_KEY_PREFIX}alice`,
			JSON.stringify({
				dashboard: {
					...EMPTY_DASHBOARD,
					total_value_cny: 250_763.82,
					cash_value_cny: 14_255.51,
					holdings_value_cny: 236_508.31,
				},
				lastUpdatedAt: "2026-03-14T13:20:09.000Z",
			}),
		);

		render(<App />);

		expect(screen.getByText("正在恢复登录状态")).not.toBeNull();
		expect(screen.queryByText("¥25.08万")).toBeNull();
		expect(screen.queryByText("¥1.43万")).toBeNull();
		expect(screen.queryByText("¥23.65万")).toBeNull();
	});

	it("uses the cached dashboard snapshot after session confirmation while live refresh is pending", async () => {
		const pendingDashboard = createDeferredPromise<typeof EMPTY_DASHBOARD>();
		authApiMocks.getAuthSession.mockResolvedValue({ user_id: "alice", email: null });
		dashboardApiMocks.getDashboard.mockReturnValue(pendingDashboard.promise);
		window.sessionStorage.setItem(STORAGE_KEY, "alice");
		window.sessionStorage.setItem(
			`${DASHBOARD_CACHE_KEY_PREFIX}alice`,
			JSON.stringify({
				schemaVersion: 1,
				dashboard: {
					...EMPTY_DASHBOARD,
					total_value_cny: 250_763.82,
					cash_value_cny: 14_255.51,
					holdings_value_cny: 236_508.31,
				},
				lastUpdatedAt: "2026-03-14T13:20:09.000Z",
			}),
		);

		render(<App />);

		await waitFor(() => {
			expect(screen.getByText("你好，alice")).not.toBeNull();
		});
		expect(screen.getByText("¥25.08万")).not.toBeNull();
		expect(screen.getByText("¥1.43万")).not.toBeNull();
		expect(screen.getByText("¥23.65万")).not.toBeNull();
	});

	it("falls back to persistent dashboard cache when the tab cache is empty", async () => {
		const pendingDashboard = createDeferredPromise<typeof EMPTY_DASHBOARD>();
		authApiMocks.getAuthSession.mockResolvedValue({ user_id: "alice", email: null });
		dashboardApiMocks.getDashboard.mockReturnValue(pendingDashboard.promise);
		window.localStorage.setItem(STORAGE_KEY, "alice");
		window.localStorage.setItem(
			`${DASHBOARD_CACHE_KEY_PREFIX}alice`,
			JSON.stringify({
				dashboard: {
					...EMPTY_DASHBOARD,
					total_value_cny: 198_880.12,
					holdings_value_cny: 168_200.45,
					cash_value_cny: 30_679.67,
				},
				lastUpdatedAt: "2026-03-14T13:45:00.000Z",
			}),
		);

		render(<App />);

		await waitFor(() => {
			expect(screen.getByText("你好，alice")).not.toBeNull();
		});
		expect(screen.getByText("¥19.89万")).not.toBeNull();
		expect(screen.getByText("¥16.82万")).not.toBeNull();
		expect(screen.getByText("¥3.07万")).not.toBeNull();
	});

	it("shows the admin release notes entry in chinese", async () => {
		authApiMocks.getAuthSession.mockResolvedValue({ user_id: "admin", email: null });
		dashboardApiMocks.getDashboard.mockResolvedValue({ ...EMPTY_DASHBOARD });

		render(<App />);

		await waitFor(() => {
			expect(screen.getByText("你好，admin")).not.toBeNull();
		});

		expect(screen.getByRole("button", { name: "更新日志" })).not.toBeNull();
		expect(screen.queryByRole("button", { name: "Release Notes" })).toBeNull();
	});

	it("loads release notes into the admin inbox and marks them seen", async () => {
		authApiMocks.getAuthSession.mockResolvedValue({ user_id: "admin", email: null });
		feedbackApiMocks.listReleaseNotesForCurrentUser.mockResolvedValue([
			{
				delivery_id: 8,
				release_note_id: 1,
				version: "0.7.2",
				title: "Product Updates",
				content: "# Product Updates",
				source_feedback_ids: [3],
				published_at: "2026-03-26T09:00:00Z",
				delivered_at: "2026-03-26T09:01:00Z",
				seen_at: null,
			},
		]);

		render(<App />);

		await waitFor(() => {
			expect(screen.getByText("你好，admin")).not.toBeNull();
		});

		await act(async () => {
			screen.getByRole("button", { name: "消息" }).click();
		});

		await waitFor(() => {
			expect(screen.getByTestId("admin-feedback-dialog")).not.toBeNull();
		});

		expect(feedbackApiMocks.listReleaseNotesForCurrentUser).toHaveBeenCalledTimes(1);
		expect(feedbackApiMocks.markReleaseNotesSeenForCurrentUser).toHaveBeenCalledTimes(1);
		expect(adminFeedbackDialogMocks.lastProps?.releaseNotes).toEqual([
			expect.objectContaining({
				delivery_id: 8,
				version: "0.7.2",
			}),
		]);
	});

	it("passes recent holding transactions into the analytics workspace", async () => {
		authApiMocks.getAuthSession.mockResolvedValue({ user_id: "admin", email: null });
		dashboardApiMocks.getDashboard.mockResolvedValue({
			...EMPTY_DASHBOARD,
			recent_holding_transactions: [
				{
					id: 1,
					symbol: "9988.HK",
					name: "阿里巴巴-SW",
					side: "BUY",
					quantity: 400,
					price: 124.2,
					fallback_currency: "HKD",
					market: "HK",
					broker: "港股通",
					traded_on: "2026-03-24",
					created_at: "2026-03-25T06:47:09.244918Z",
					updated_at: "2026-03-25T06:47:09.244933Z",
				},
			],
		});

		render(<App />);

		await waitFor(() => {
			expect(screen.getByText("你好，admin")).not.toBeNull();
		});

		await act(async () => {
			screen.getByRole("tab", { name: "洞察" }).click();
		});

		await waitFor(() => {
			expect(screen.getByTestId("portfolio-analytics")).not.toBeNull();
		});

		expect(analyticsMocks.lastProps?.recent_holding_transactions).toEqual([
			expect.objectContaining({
				id: 1,
				symbol: "9988.HK",
				side: "BUY",
				traded_on: "2026-03-24",
			}),
		]);
	});

	it("shows placeholders instead of zero totals while remembered data is still loading", () => {
		const pendingSession = createDeferredPromise<{ user_id: string; email: string | null }>();
		authApiMocks.getAuthSession.mockReturnValue(pendingSession.promise);
		window.sessionStorage.setItem(STORAGE_KEY, "alice");

		render(<App />);

		expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(6);
		expect(screen.queryByText("¥0.00")).toBeNull();
	});

	it("writes the latest dashboard snapshot back to session storage after refresh", async () => {
		authApiMocks.getAuthSession.mockResolvedValue({ user_id: "alice", email: null });
		dashboardApiMocks.getDashboard.mockResolvedValue({
			...EMPTY_DASHBOARD,
			total_value_cny: 180_000,
			holdings_value_cny: 120_000,
			cash_value_cny: 60_000,
		});

		render(<App />);

		await waitFor(() => {
			expect(dashboardApiMocks.getDashboard).toHaveBeenCalledWith(false);
		});

		const cachedValue = window.sessionStorage.getItem(
			`${DASHBOARD_CACHE_KEY_PREFIX}alice`,
		);
		expect(cachedValue).not.toBeNull();
		expect(cachedValue).toContain("\"schemaVersion\":1");
		expect(cachedValue).toContain("\"holdings_value_cny\":120000");
		expect(
			window.localStorage.getItem(`${DASHBOARD_CACHE_KEY_PREFIX}alice`),
		).toContain("\"holdings_value_cny\":120000");
	});

	it("ignores malformed cached dashboard snapshots instead of crashing", async () => {
		const pendingDashboard = createDeferredPromise<typeof EMPTY_DASHBOARD>();
		authApiMocks.getAuthSession.mockResolvedValue({ user_id: "alice", email: null });
		dashboardApiMocks.getDashboard.mockReturnValue(pendingDashboard.promise);
		window.sessionStorage.setItem(STORAGE_KEY, "alice");
		window.sessionStorage.setItem(
			`${DASHBOARD_CACHE_KEY_PREFIX}alice`,
			JSON.stringify({
				schemaVersion: 1,
				dashboard: {
					total_value_cny: "bad-data",
					cash_accounts: [null, 1, "oops"],
				},
				lastUpdatedAt: "not-a-date",
			}),
		);

		render(<App />);

		await waitFor(() => {
			expect(screen.getByText("你好，alice")).not.toBeNull();
		});
		expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(6);
		expect(screen.queryByText("NaN")).toBeNull();
	});

	it("passes hydrated dashboard collections into the asset manager", async () => {
		authApiMocks.getAuthSession.mockResolvedValue({ user_id: "alice", email: null });
		dashboardApiMocks.getDashboard.mockResolvedValue({
			...EMPTY_DASHBOARD,
			server_today: "2026-03-10",
			cash_accounts: [
				{
					id: 1,
					name: "主账户",
					platform: "Bank",
					currency: "CNY",
					balance: 100,
					account_type: "BANK",
					value_cny: 100,
				},
			],
			holdings: [
				{
					id: 1,
					side: "BUY",
					symbol: "AAPL",
					name: "Apple",
					quantity: 2,
					fallback_currency: "USD",
					cost_basis_price: 180,
					market: "US",
					broker: "Futu",
					started_on: "2026-03-08",
					note: "长期",
					price: 188,
					price_currency: "USD",
					value_cny: 2710,
					return_pct: 4.44,
					last_updated: "2026-03-10T12:00:00Z",
				},
			],
			fixed_assets: [],
			liabilities: [],
			other_assets: [],
		});

		render(<App />);

		await waitFor(() => {
			expect(dashboardApiMocks.getDashboard).toHaveBeenCalledWith(false);
		});
		await waitFor(() => {
			expect(assetManagerMocks.lastProps).not.toBeNull();
		});

		expect(assetManagerMocks.lastProps).toMatchObject({
			maxStartedOnDate: "2026-03-10",
			initialCashAccounts: [
				expect.objectContaining({ id: 1, name: "主账户" }),
			],
			initialHoldings: [
				expect.objectContaining({ id: 1, symbol: "AAPL" }),
			],
		});
	});

	it("falls back to the login screen when session restore fails", async () => {
		authApiMocks.getAuthSession.mockRejectedValue(new Error("请先登录。"));
		window.sessionStorage.setItem(STORAGE_KEY, "alice");

		render(<App />);

		expect(screen.queryByTestId("login-screen")).toBeNull();

		await waitFor(() => {
			expect(screen.getByTestId("login-screen")).not.toBeNull();
		});

		expect(window.sessionStorage.getItem(STORAGE_KEY)).toBeNull();
	});

	it("keeps remembered session hints when restore fails with a transient server error", async () => {
		authApiMocks.getAuthSession.mockRejectedValue(
			new Error("服务器暂时不可用，请稍后再试。"),
		);
		window.sessionStorage.setItem(STORAGE_KEY, "alice");

		render(<App />);

		expect(screen.queryByTestId("login-screen")).toBeNull();

		await waitFor(() => {
			expect(screen.getByTestId("login-screen")).not.toBeNull();
		});

		expect(
			screen.getByText("服务器暂时不可用，请稍后再试。"),
		).not.toBeNull();
		expect(screen.queryByText("Internal Server Error")).toBeNull();
		expect(window.sessionStorage.getItem(STORAGE_KEY)).toBe("alice");
	});

	it("ignores a stale dashboard response after logging out and signing into another account", async () => {
		const aliceDashboard = createDeferredPromise<typeof EMPTY_DASHBOARD>();
		const bobDashboard = createDeferredPromise<typeof EMPTY_DASHBOARD>();
		authApiMocks.getAuthSession.mockResolvedValue({ user_id: "alice", email: null });
		authApiMocks.loginWithPassword.mockResolvedValue({ user_id: "bob", email: null });
		dashboardApiMocks.getDashboard
			.mockReturnValueOnce(aliceDashboard.promise)
			.mockReturnValueOnce(bobDashboard.promise);

		render(<App />);

		await waitFor(() => {
			expect(screen.getByText("你好，alice")).not.toBeNull();
		});

		await act(async () => {
			screen.getByRole("button", { name: "退出" }).click();
			await flushMicrotasks();
		});

		await waitFor(() => {
			expect(screen.getByTestId("login-screen")).not.toBeNull();
		});

		await act(async () => {
			screen.getByRole("button", { name: "模拟登录" }).click();
			await flushMicrotasks();
		});

		bobDashboard.resolve({
			...EMPTY_DASHBOARD,
			total_value_cny: 200_000,
		});

		await waitFor(() => {
			expect(screen.getByText("你好，bob")).not.toBeNull();
		});
		await waitFor(() => {
			expect(screen.getByText("¥20.00万")).not.toBeNull();
		});

		aliceDashboard.resolve({
			...EMPTY_DASHBOARD,
			total_value_cny: 100_000,
		});

		await act(async () => {
			await flushMicrotasks();
		});

		expect(screen.getByText("¥20.00万")).not.toBeNull();
		expect(screen.queryByText("¥10.00万")).toBeNull();
	});

	it("pauses timed dashboard refresh while user input is protected by a refresh guard", async () => {
		vi.useFakeTimers();
		authApiMocks.getAuthSession.mockResolvedValue({ user_id: "alice", email: null });

		render(<App />);

		await act(async () => {
			await flushMicrotasks();
		});
		expect(dashboardApiMocks.getDashboard).toHaveBeenCalledTimes(1);

		act(() => {
			__setAutoRefreshGuardForTests("test-editing", true);
		});

		await act(async () => {
			await vi.advanceTimersByTimeAsync(130000);
		});
		expect(dashboardApiMocks.getDashboard).toHaveBeenCalledTimes(1);
		const callCountBeforeResume = dashboardApiMocks.getDashboard.mock.calls.length;

		act(() => {
			__setAutoRefreshGuardForTests("test-editing", false);
		});

		await act(async () => {
			await flushMicrotasks();
		});
		expect(dashboardApiMocks.getDashboard.mock.calls.length).toBeGreaterThan(callCountBeforeResume);
	});

	it("drops overlapping insight auto refresh ticks while a dashboard request is pending", async () => {
		vi.useFakeTimers();
		vi.setSystemTime(new Date("2026-03-24T10:00:00.000Z"));
		const pendingDashboard = createDeferredPromise<typeof EMPTY_DASHBOARD>();
		authApiMocks.getAuthSession.mockResolvedValue({ user_id: "alice", email: null });
		dashboardApiMocks.getDashboard
			.mockReturnValueOnce(pendingDashboard.promise)
			.mockResolvedValue({ ...EMPTY_DASHBOARD });

		render(<App />);

		await act(async () => {
			await flushMicrotasks();
		});
		expect(dashboardApiMocks.getDashboard).toHaveBeenCalledTimes(1);

		await act(async () => {
			screen.getByRole("tab", { name: "洞察" }).click();
		});
		await act(async () => {
			await vi.advanceTimersByTimeAsync(11_000);
		});
		expect(dashboardApiMocks.getDashboard).toHaveBeenCalledTimes(1);

		pendingDashboard.resolve({ ...EMPTY_DASHBOARD });
		await act(async () => {
			await flushMicrotasks();
			await vi.advanceTimersByTimeAsync(5_000);
		});
		expect(dashboardApiMocks.getDashboard).toHaveBeenCalledTimes(2);
	});

	it("renders workspace tabs in manage insights agent order", async () => {
		authApiMocks.getAuthSession.mockResolvedValue({ user_id: "alice", email: null });

		render(<App />);

		await waitFor(() => {
			expect(dashboardApiMocks.getDashboard).toHaveBeenCalledWith(false);
		});

		const workspaceTabLists = screen.getAllByRole("tablist", { name: "页面视图" });
		const activeWorkspaceTabList = workspaceTabLists[workspaceTabLists.length - 1];

		expect(
			within(activeWorkspaceTabList)
				.getAllByRole("tab")
				.map((tab) => tab.textContent?.trim()),
		).toEqual(["管理", "洞察", "智能体"]);
	});

	it("keeps the manage workspace mounted while switching to other tabs", async () => {
		authApiMocks.getAuthSession.mockResolvedValue({ user_id: "alice", email: null });

		render(<App />);

		await waitFor(() => {
			expect(dashboardApiMocks.getDashboard).toHaveBeenCalledWith(false);
		});

		expect(screen.getByTestId("asset-manager")).not.toBeNull();

		await act(async () => {
			screen.getByRole("tab", { name: "洞察" }).click();
		});

		expect(screen.getByTestId("asset-manager")).not.toBeNull();
		await waitFor(() => {
			expect(screen.getByTestId("portfolio-analytics")).not.toBeNull();
		});

		await act(async () => {
			screen.getByRole("tab", { name: "管理" }).click();
		});

		expect(screen.getByTestId("asset-manager")).not.toBeNull();
	});

	it("keeps inactive workspaces mounted but hidden while switching tabs", async () => {
		authApiMocks.getAuthSession.mockResolvedValue({ user_id: "alice", email: null });

		render(<App />);

		await waitFor(() => {
			expect(dashboardApiMocks.getDashboard).toHaveBeenCalledWith(false);
		});

		const managePanel = screen.getByTestId("asset-manager").closest(".integrated-stack");
		expect(managePanel?.hasAttribute("hidden")).toBe(false);

		await act(async () => {
			screen.getByRole("tab", { name: "智能体" }).click();
		});

		await waitFor(() => {
			expect(screen.getByTestId("agent-audit-panel")).not.toBeNull();
		});

		const agentPanel = screen.getByTestId("agent-audit-panel").closest(".section-shell");
		expect(agentPanel?.hasAttribute("hidden")).toBe(false);
		expect(managePanel?.hasAttribute("hidden")).toBe(true);

		await act(async () => {
			screen.getByRole("tab", { name: "洞察" }).click();
		});

		await waitFor(() => {
			expect(screen.getByTestId("portfolio-analytics")).not.toBeNull();
		});

		const insightsPanel = screen.getByTestId("portfolio-analytics").closest(".section-shell");
		expect(insightsPanel?.hasAttribute("hidden")).toBe(false);
		expect(agentPanel?.hasAttribute("hidden")).toBe(true);
		expect(managePanel?.hasAttribute("hidden")).toBe(true);
	});

	it("refreshes the agent workspace in the background after login and reuses it", async () => {
		vi.useFakeTimers();
		authApiMocks.getAuthSession.mockResolvedValue({ user_id: "alice", email: null });

		render(<App />);

		await act(async () => {
			await flushMicrotasks();
		});
		expect(dashboardApiMocks.getDashboard).toHaveBeenCalledWith(false);
		expect(assetApiMocks.listAgentApiKeys).not.toHaveBeenCalled();
		expect(assetApiMocks.listAgentRegistrations).not.toHaveBeenCalled();
		expect(assetApiMocks.listAssetRecords).not.toHaveBeenCalled();

		await act(async () => {
			await vi.advanceTimersByTimeAsync(1499);
		});
		expect(assetApiMocks.listAgentApiKeys).not.toHaveBeenCalled();
		expect(assetApiMocks.listAgentRegistrations).not.toHaveBeenCalled();
		expect(assetApiMocks.listAssetRecords).not.toHaveBeenCalled();

		await act(async () => {
			await vi.advanceTimersByTimeAsync(1);
		});
		await act(async () => {
			await flushMicrotasks();
		});

		expect(assetApiMocks.listAgentApiKeys).toHaveBeenCalledTimes(1);
		expect(assetApiMocks.listAgentRegistrations).toHaveBeenCalledWith({
			includeAllUsers: false,
		});
		expect(assetApiMocks.listAssetRecords).toHaveBeenNthCalledWith(1, {
			source: "AGENT",
			limit: 200,
		});
		expect(assetApiMocks.listAssetRecords).toHaveBeenNthCalledWith(2, {
			source: "API",
			limit: 200,
		});

		await act(async () => {
			screen.getByRole("tab", { name: "智能体" }).click();
		});

		expect(screen.getByTestId("agent-audit-panel")).not.toBeNull();

		await act(async () => {
			screen.getByRole("tab", { name: "管理" }).click();
		});

		expect(screen.getByTestId("agent-audit-panel")).not.toBeNull();

		await act(async () => {
			screen.getByRole("tab", { name: "智能体" }).click();
		});

		expect(assetApiMocks.listAgentApiKeys).toHaveBeenCalledTimes(1);
		expect(assetApiMocks.listAgentRegistrations).toHaveBeenCalledTimes(1);
		expect(assetApiMocks.listAssetRecords).toHaveBeenCalledTimes(2);
	});

	it("keeps the one-time api key notice out of the workspace after key creation", async () => {
		authApiMocks.getAuthSession.mockResolvedValue({ user_id: "alice", email: null });

		render(<App />);

		await waitFor(() => {
			expect(dashboardApiMocks.getDashboard).toHaveBeenCalledWith(false);
		});

		await act(async () => {
			screen.getByRole("tab", { name: "智能体" }).click();
		});

		await waitFor(() => {
			expect(screen.getByTestId("agent-audit-panel")).not.toBeNull();
		});

		await act(async () => {
			screen.getByRole("button", { name: "模拟创建 API Key" }).click();
		});

		await waitFor(() => {
			expect(assetApiMocks.createAgentApiKey).toHaveBeenCalledWith({
				name: "local-cli",
				expires_in_days: 30,
			});
		});

		await waitFor(() => {
			expect(agentAuditPanelMocks.lastProps?.issuedApiKey).not.toBeNull();
		});
		expect(agentAuditPanelMocks.lastProps?.apiKeyNoticeMessage).toBeNull();

		await act(async () => {
			screen.getByRole("button", { name: "模拟关闭新 Key" }).click();
		});

		await waitFor(() => {
			expect(agentAuditPanelMocks.lastProps?.issuedApiKey).toBeNull();
		});
		expect(agentAuditPanelMocks.lastProps?.apiKeyNoticeMessage).toBeNull();
	});

	it("opens the asset records dialog from the hero actions", async () => {
		authApiMocks.getAuthSession.mockResolvedValue({ user_id: "alice", email: null });

		render(<App />);

		await waitFor(() => {
			expect(dashboardApiMocks.getDashboard).toHaveBeenCalledWith(false);
		});

		expect(screen.queryByTestId("asset-records-dialog")).toBeNull();

		await act(async () => {
			screen.getByRole("button", { name: "记录" }).click();
		});

		expect(screen.getByTestId("asset-records-dialog")).not.toBeNull();
		expect(assetRecordsDialogMocks.lastOpenState).toBe(true);
	});
});
