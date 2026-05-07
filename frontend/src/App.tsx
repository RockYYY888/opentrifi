import { lazy, Suspense, useEffect, useRef, useState } from "react";

import { AdminFeedbackDialog } from "./components/feedback/AdminFeedbackDialog";
import { AdminReleaseNotesDialog } from "./components/feedback/AdminReleaseNotesDialog";
import { AgentExecutionAuditPanel } from "./components/assets/AgentExecutionAuditPanel";
import { AssetRecordsDialog } from "./components/assets/AssetRecordsDialog";
import { EmailDialog } from "./components/auth/EmailDialog";
import { LoginScreen } from "./components/auth/LoginScreen";
import { AssetManager } from "./components/assets";
import { FeedbackDialog } from "./components/feedback/FeedbackDialog";
import { UserFeedbackInboxDialog } from "./components/feedback/UserFeedbackInboxDialog";
import {
	AUTH_SUBMISSION_TIMEOUT_MS,
	clearRememberedSessionUserId,
	type AuthStatus,
	isAuthenticationErrorMessage,
	readRememberedSessionUserId,
	rememberSessionUserId,
	SESSION_CHECK_TIMEOUT_MS,
	withTimeout,
} from "./app/authSession";
import {
	formatFxRate,
	formatLastUpdated,
	formatSummaryCny,
	getMillisecondsUntilNextMinute,
	getMillisecondsUntilNextSecond,
	isDashboardSnapshotEmpty,
	readCachedDashboardSnapshot,
	toAssetManagerSeeds,
	writeCachedDashboardSnapshot,
} from "./app/dashboardRefresh";
import { removeRecordById, replaceRecordById } from "./app/feedbackInbox";
import { WorkspaceShell } from "./app/WorkspaceShell";
import {
	DEFAULT_MOUNTED_WORKSPACES,
	type WorkspaceView,
} from "./app/workspaceTypes";
import { createAssetManagerController, defaultAssetApiClient } from "./lib/assetApi";
import {
	getAuthSession,
	loginWithPassword,
	logoutCurrentUser,
	registerWithPassword,
	resetPasswordWithEmail,
	updateCurrentUserEmail,
} from "./lib/authApi";
import { getDashboard } from "./lib/dashboardApi";
import { useHasActiveAutoRefreshGuards } from "./lib/autoRefreshGuards";
import {
	createReleaseNoteForAdmin,
	closeFeedbackForAdmin,
	getFeedbackSummary,
	hideInboxMessageForCurrentUser,
	listFeedbackForCurrentUser,
	listReleaseNotesForAdmin,
	listReleaseNotesForCurrentUser,
	listSystemFeedbackForAdmin,
	listUserFeedbackForAdmin,
	markFeedbackSeenForCurrentUser,
	markReleaseNotesSeenForCurrentUser,
	publishReleaseNoteForAdmin,
	replyToFeedbackForAdmin,
	submitUserFeedback,
} from "./lib/feedbackApi";
import type {
	AuthLoginCredentials,
	AuthRegisterCredentials,
	PasswordResetPayload,
	UserEmailUpdate,
} from "./types/auth";
import type {
	AgentApiKeyIssueRecord,
	AgentApiKeyRecord,
	AgentRegistrationRecord,
	AssetRecordRecord,
	CreateAgentApiKeyInput,
} from "./types/assets";
import { EMPTY_DASHBOARD, type DashboardResponse } from "./types/dashboard";
import type {
	AdminFeedbackRecord,
	ReleaseNoteDeliveryRecord,
	ReleaseNoteInput,
	ReleaseNoteRecord,
	UserFeedbackRecord,
} from "./types/feedback";
import { formatCny } from "./utils/portfolioAnalytics";

const AGENT_AUDIT_BACKGROUND_REFRESH_DELAY_MS = 1500;
const EMPTY_AGENT_REGISTRATIONS: AgentRegistrationRecord[] = [];
const EMPTY_AGENT_API_KEYS: AgentApiKeyRecord[] = [];
const EMPTY_AGENT_RECORDS: AssetRecordRecord[] = [];
const PortfolioAnalytics = lazy(async () => {
	const module = await import("./components/analytics");
	return { default: module.PortfolioAnalytics };
});
const assetManagerController = createAssetManagerController(defaultAssetApiClient);

function App() {
	const [authStatus, setAuthStatus] = useState<AuthStatus>("checking");
	const [currentUserId, setCurrentUserId] = useState<string | null>(() =>
		readRememberedSessionUserId()
	);
	const [currentUserEmail, setCurrentUserEmail] = useState<string | null>(null);
	const [authErrorMessage, setAuthErrorMessage] = useState<string | null>(null);
	const [authNoticeMessage, setAuthNoticeMessage] = useState<string | null>(null);
	const [isSubmittingAuth, setIsSubmittingAuth] = useState(false);
	const [dashboard, setDashboard] = useState<DashboardResponse>(() => {
		const rememberedUserId = readRememberedSessionUserId();
		const cachedDashboardSnapshot = rememberedUserId
			? readCachedDashboardSnapshot(rememberedUserId)
			: null;
		return cachedDashboardSnapshot?.lastUpdatedAt
			? cachedDashboardSnapshot.dashboard
			: EMPTY_DASHBOARD;
	});
	const [isLoadingDashboard, setIsLoadingDashboard] = useState(false);
	const [isRefreshingDashboard, setIsRefreshingDashboard] = useState(false);
	const [errorMessage, setErrorMessage] = useState<string | null>(null);
	const [lastUpdatedAt, setLastUpdatedAt] = useState<string | null>(() => {
		const rememberedUserId = readRememberedSessionUserId();
		return rememberedUserId
			? readCachedDashboardSnapshot(rememberedUserId)?.lastUpdatedAt ?? null
			: null;
	});
	const [isAssetRecordsOpen, setIsAssetRecordsOpen] = useState(false);
	const [assetRecordsDialogVersion, setAssetRecordsDialogVersion] = useState(0);
	const [assetRecordRefreshToken, setAssetRecordRefreshToken] = useState(0);
	const [isFeedbackOpen, setIsFeedbackOpen] = useState(false);
	const [isSubmittingFeedback, setIsSubmittingFeedback] = useState(false);
	const [feedbackErrorMessage, setFeedbackErrorMessage] = useState<string | null>(null);
	const [feedbackNoticeMessage, setFeedbackNoticeMessage] = useState<string | null>(null);
	const [feedbackInboxCount, setFeedbackInboxCount] = useState(0);
	const [activeWorkspaceView, setActiveWorkspaceView] = useState<WorkspaceView>("manage");
	const [mountedWorkspaceViews, setMountedWorkspaceViews] = useState<Record<WorkspaceView, boolean>>(
		DEFAULT_MOUNTED_WORKSPACES,
	);
	const [agentRegistrations, setAgentRegistrations] = useState<AgentRegistrationRecord[]>(
		EMPTY_AGENT_REGISTRATIONS,
	);
	const [agentApiKeys, setAgentApiKeys] = useState<AgentApiKeyRecord[]>(EMPTY_AGENT_API_KEYS);
	const [issuedAgentApiKey, setIssuedAgentApiKey] = useState<AgentApiKeyIssueRecord | null>(null);
	const [agentRecords, setAgentRecords] = useState<AssetRecordRecord[]>(EMPTY_AGENT_RECORDS);
	const [isLoadingAgentAudit, setIsLoadingAgentAudit] = useState(false);
	const [agentAuditErrorMessage, setAgentAuditErrorMessage] = useState<string | null>(null);
	const [isCreatingAgentApiKey, setIsCreatingAgentApiKey] = useState(false);
	const [revokingAgentApiKeyId, setRevokingAgentApiKeyId] = useState<number | null>(null);
	const [agentApiKeyErrorMessage, setAgentApiKeyErrorMessage] = useState<string | null>(null);
	const [agentApiKeyNoticeMessage, setAgentApiKeyNoticeMessage] = useState<string | null>(null);
	const [isAdminInboxOpen, setIsAdminInboxOpen] = useState(false);
	const [isAdminReleaseNotesOpen, setIsAdminReleaseNotesOpen] = useState(false);
	const [isUserInboxOpen, setIsUserInboxOpen] = useState(false);
	const [isLoadingAdminInbox, setIsLoadingAdminInbox] = useState(false);
	const [isAdminInboxShowingDismissed, setIsAdminInboxShowingDismissed] = useState(false);
	const [isLoadingAdminReleaseNotes, setIsLoadingAdminReleaseNotes] = useState(false);
	const [adminInboxErrorMessage, setAdminInboxErrorMessage] = useState<string | null>(null);
	const [adminReleaseNotesErrorMessage, setAdminReleaseNotesErrorMessage] = useState<string | null>(
		null,
	);
	const [adminUserFeedbackItems, setAdminUserFeedbackItems] = useState<AdminFeedbackRecord[]>([]);
	const [adminSystemFeedbackItems, setAdminSystemFeedbackItems] = useState<AdminFeedbackRecord[]>(
		[],
	);
	const [adminInboxReleaseNotes, setAdminInboxReleaseNotes] = useState<ReleaseNoteDeliveryRecord[]>(
		[],
	);
	const [adminReleaseNotes, setAdminReleaseNotes] = useState<ReleaseNoteRecord[]>([]);
	const [isLoadingUserInbox, setIsLoadingUserInbox] = useState(false);
	const [userInboxErrorMessage, setUserInboxErrorMessage] = useState<string | null>(null);
	const [userFeedbackItems, setUserFeedbackItems] = useState<UserFeedbackRecord[]>([]);
	const [userReleaseNotes, setUserReleaseNotes] = useState<ReleaseNoteDeliveryRecord[]>([]);
	const [isEmailDialogOpen, setIsEmailDialogOpen] = useState(false);
	const [isSubmittingEmail, setIsSubmittingEmail] = useState(false);
	const [emailDialogErrorMessage, setEmailDialogErrorMessage] = useState<string | null>(null);
	const [emailNoticeMessage, setEmailNoticeMessage] = useState<string | null>(null);
	const dashboardRequestInFlightRef = useRef<number | null>(null);
	const latestDashboardRequestIdRef = useRef(0);
	const pendingDashboardRefreshRef = useRef(false);
	const pendingForceRefreshRef = useRef(false);
	const autoRefreshResumeRef = useRef(false);
	const hasLoadedAgentAuditRef = useRef(false);
	const agentAuditRequestInFlightRef = useRef<Promise<void> | null>(null);
	const latestAgentAuditRequestIdRef = useRef(0);
	const currentUserIdRef = useRef<string | null>(currentUserId);
	const authStatusRef = useRef<AuthStatus>(authStatus);
	const isAutoRefreshBlocked = useHasActiveAutoRefreshGuards();

	useEffect(() => {
		currentUserIdRef.current = currentUserId;
	}, [currentUserId]);

	useEffect(() => {
		authStatusRef.current = authStatus;
	}, [authStatus]);

	function invalidateDashboardRequests(): void {
		latestDashboardRequestIdRef.current += 1;
		dashboardRequestInFlightRef.current = null;
		pendingDashboardRefreshRef.current = false;
		pendingForceRefreshRef.current = false;
	}

	function resetDashboardState(): void {
		setDashboard(EMPTY_DASHBOARD);
		setIsLoadingDashboard(false);
		setIsRefreshingDashboard(false);
		setErrorMessage(null);
		setLastUpdatedAt(null);
		invalidateDashboardRequests();
	}

	function markSignedInWithProfile(userId: string, email: string | null): void {
		const cachedDashboardSnapshot = readCachedDashboardSnapshot(userId);
		const hasUsableCachedDashboard = cachedDashboardSnapshot?.lastUpdatedAt !== null;
		rememberSessionUserId(userId);
		currentUserIdRef.current = userId;
		authStatusRef.current = "authenticated";
		setCurrentUserId(userId);
		setCurrentUserEmail(email);
		setAuthStatus("authenticated");
		setAuthErrorMessage(null);
		setAuthNoticeMessage(null);
		setFeedbackNoticeMessage(null);
		setFeedbackInboxCount(0);
		setFeedbackErrorMessage(null);
		setIsFeedbackOpen(false);
		setIsAssetRecordsOpen(false);
		setAssetRecordsDialogVersion(0);
		setAssetRecordRefreshToken(0);
		setActiveWorkspaceView("manage");
		setMountedWorkspaceViews(DEFAULT_MOUNTED_WORKSPACES);
		setAgentRegistrations(EMPTY_AGENT_REGISTRATIONS);
		setAgentApiKeys(EMPTY_AGENT_API_KEYS);
		setIssuedAgentApiKey(null);
		setAgentRecords(EMPTY_AGENT_RECORDS);
		setIsLoadingAgentAudit(false);
		setAgentAuditErrorMessage(null);
		setIsCreatingAgentApiKey(false);
		setRevokingAgentApiKeyId(null);
		setAgentApiKeyErrorMessage(null);
		setAgentApiKeyNoticeMessage(null);
		hasLoadedAgentAuditRef.current = false;
		agentAuditRequestInFlightRef.current = null;
		latestAgentAuditRequestIdRef.current += 1;
		setIsLoadingAdminInbox(false);
		setAdminInboxErrorMessage(null);
		setIsAdminInboxOpen(false);
		setIsLoadingAdminReleaseNotes(false);
		setAdminReleaseNotesErrorMessage(null);
		setIsAdminReleaseNotesOpen(false);
		setAdminUserFeedbackItems([]);
		setAdminSystemFeedbackItems([]);
		setAdminInboxReleaseNotes([]);
		setAdminReleaseNotes([]);
		setUserInboxErrorMessage(null);
		setIsUserInboxOpen(false);
		setUserFeedbackItems([]);
		setUserReleaseNotes([]);
		setEmailNoticeMessage(null);
		setEmailDialogErrorMessage(null);
		setIsEmailDialogOpen(false);
		invalidateDashboardRequests();
		setDashboard(
			hasUsableCachedDashboard && cachedDashboardSnapshot
				? cachedDashboardSnapshot.dashboard
				: EMPTY_DASHBOARD,
		);
		setLastUpdatedAt(
			hasUsableCachedDashboard && cachedDashboardSnapshot
				? cachedDashboardSnapshot.lastUpdatedAt
				: null,
		);
		setIsLoadingDashboard(!hasUsableCachedDashboard);
	}

	function markSignedOut(options: { clearRememberedSession?: boolean } = {}): void {
		if (options.clearRememberedSession ?? true) {
			clearRememberedSessionUserId();
		}
		currentUserIdRef.current = null;
		authStatusRef.current = "anonymous";
		setCurrentUserId(null);
		setCurrentUserEmail(null);
		setAuthStatus("anonymous");
		setAuthNoticeMessage(null);
		setFeedbackNoticeMessage(null);
		setFeedbackInboxCount(0);
		setFeedbackErrorMessage(null);
		setIsFeedbackOpen(false);
		setIsAssetRecordsOpen(false);
		setAssetRecordsDialogVersion(0);
		setAssetRecordRefreshToken(0);
		setActiveWorkspaceView("manage");
		setMountedWorkspaceViews(DEFAULT_MOUNTED_WORKSPACES);
		setAgentRegistrations(EMPTY_AGENT_REGISTRATIONS);
		setAgentApiKeys(EMPTY_AGENT_API_KEYS);
		setIssuedAgentApiKey(null);
		setAgentRecords(EMPTY_AGENT_RECORDS);
		setIsLoadingAgentAudit(false);
		setAgentAuditErrorMessage(null);
		setIsCreatingAgentApiKey(false);
		setRevokingAgentApiKeyId(null);
		setAgentApiKeyErrorMessage(null);
		setAgentApiKeyNoticeMessage(null);
		hasLoadedAgentAuditRef.current = false;
		agentAuditRequestInFlightRef.current = null;
		latestAgentAuditRequestIdRef.current += 1;
		setIsLoadingAdminInbox(false);
		setAdminInboxErrorMessage(null);
		setIsAdminInboxOpen(false);
		setIsLoadingAdminReleaseNotes(false);
		setAdminReleaseNotesErrorMessage(null);
		setIsAdminReleaseNotesOpen(false);
		setAdminUserFeedbackItems([]);
		setAdminSystemFeedbackItems([]);
		setAdminInboxReleaseNotes([]);
		setAdminReleaseNotes([]);
		setUserInboxErrorMessage(null);
		setIsUserInboxOpen(false);
		setUserFeedbackItems([]);
		setUserReleaseNotes([]);
		setEmailNoticeMessage(null);
		setEmailDialogErrorMessage(null);
		setIsEmailDialogOpen(false);
		resetDashboardState();
	}

	useEffect(() => {
		void hydrateSession();
	}, []);

	useEffect(() => {
		if (mountedWorkspaceViews[activeWorkspaceView]) {
			return;
		}

		setMountedWorkspaceViews((currentViews) => ({
			...currentViews,
			[activeWorkspaceView]: true,
		}));
	}, [activeWorkspaceView, mountedWorkspaceViews]);

	useEffect(() => {
		if (authStatus !== "authenticated" || !currentUserId) {
			return;
		}

		void loadDashboard({ initial: true });
		void refreshFeedbackSummary();
	}, [authStatus, currentUserId]);

	useEffect(() => {
		if (
			authStatus !== "authenticated" ||
			!currentUserId ||
			activeWorkspaceView === "agent" ||
			hasLoadedAgentAuditRef.current
		) {
			return;
		}

		let idleCallbackId: number | null = null;
		const timerId = window.setTimeout(() => {
			if ("requestIdleCallback" in window) {
				idleCallbackId = window.requestIdleCallback(() => {
					void loadAgentAudit();
				}, { timeout: AGENT_AUDIT_BACKGROUND_REFRESH_DELAY_MS });
				return;
			}

			void loadAgentAudit();
		}, AGENT_AUDIT_BACKGROUND_REFRESH_DELAY_MS);

		return () => {
			window.clearTimeout(timerId);
			if (idleCallbackId !== null && "cancelIdleCallback" in window) {
				window.cancelIdleCallback(idleCallbackId);
			}
		};
	}, [activeWorkspaceView, authStatus, currentUserId]);

	useEffect(() => {
		if (authStatus !== "authenticated") {
			return;
		}
		if (isAutoRefreshBlocked) {
			autoRefreshResumeRef.current = true;
			return;
		}

		let refreshTimer = 0;
		const useSecondLevelRefresh = activeWorkspaceView === "insights";
		const initialDelay = window.setTimeout(() => {
			void loadDashboard();
			refreshTimer = window.setInterval(() => {
				if (document.visibilityState !== "visible") {
					return;
				}
				void loadDashboard();
			}, useSecondLevelRefresh ? 1000 : 60 * 1000);
		}, useSecondLevelRefresh ? getMillisecondsUntilNextSecond() : getMillisecondsUntilNextMinute());

		return () => {
			window.clearTimeout(initialDelay);
			if (refreshTimer) {
				window.clearInterval(refreshTimer);
			}
		};
	}, [activeWorkspaceView, authStatus, isAutoRefreshBlocked]);

	useEffect(() => {
		if (
			authStatus !== "authenticated" ||
			isAutoRefreshBlocked ||
			!autoRefreshResumeRef.current
		) {
			return;
		}

		autoRefreshResumeRef.current = false;
		void loadDashboard();
	}, [authStatus, isAutoRefreshBlocked]);

	useEffect(() => {
		if (authStatus !== "authenticated" || isAutoRefreshBlocked) {
			return;
		}

		function handleVisibilityChange(): void {
			if (document.visibilityState === "visible") {
				void loadDashboard();
			}
		}

		document.addEventListener("visibilitychange", handleVisibilityChange);
		return () => document.removeEventListener("visibilitychange", handleVisibilityChange);
	}, [authStatus, isAutoRefreshBlocked]);

	async function hydrateSession(): Promise<void> {
		authStatusRef.current = "checking";
		setAuthStatus("checking");
		setAuthErrorMessage(null);
		setAuthNoticeMessage(null);

		try {
			const session = await withTimeout(
				getAuthSession(),
				SESSION_CHECK_TIMEOUT_MS,
				"会话检查超时",
			);
			markSignedInWithProfile(session.user_id, session.email ?? null);
		} catch (error) {
			const nextErrorMessage =
				error instanceof Error && error.message.trim()
					? error.message
					: "暂时无法验证登录状态，请稍后再试。";
			if (isAuthenticationErrorMessage(nextErrorMessage)) {
				markSignedOut();
				return;
			}

			markSignedOut({ clearRememberedSession: false });
			setAuthErrorMessage(nextErrorMessage);
		}
	}

	async function submitAuth(
		mode: "login" | "register",
		payload: AuthLoginCredentials | AuthRegisterCredentials,
	): Promise<void> {
		setIsSubmittingAuth(true);
		setAuthErrorMessage(null);
		setAuthNoticeMessage(null);
		setAuthStatus("anonymous");

		try {
			const session = await withTimeout(
				mode === "login"
					? loginWithPassword(payload as AuthLoginCredentials)
					: registerWithPassword(payload as AuthRegisterCredentials),
				AUTH_SUBMISSION_TIMEOUT_MS,
				"请求超时，请检查后端服务或网络后重试。",
			);
			markSignedInWithProfile(session.user_id, session.email ?? null);
		} catch (error) {
			setAuthErrorMessage(
				error instanceof Error ? error.message : "登录失败，请稍后再试。",
			);
			setAuthStatus("anonymous");
		} finally {
			setIsSubmittingAuth(false);
		}
	}

	async function submitPasswordReset(payload: PasswordResetPayload): Promise<void> {
		setIsSubmittingAuth(true);
		setAuthErrorMessage(null);
		setAuthNoticeMessage(null);
		setAuthStatus("anonymous");

		try {
			const result = await withTimeout(
				resetPasswordWithEmail(payload),
				AUTH_SUBMISSION_TIMEOUT_MS,
				"请求超时，请检查后端服务或网络后重试。",
			);
			setAuthNoticeMessage(result.message);
		} catch (error) {
			setAuthErrorMessage(
				error instanceof Error ? error.message : "密码重置失败，请稍后再试。",
			);
		} finally {
			setIsSubmittingAuth(false);
		}
	}

	async function handleLogout(): Promise<void> {
		try {
			await logoutCurrentUser();
		} finally {
			markSignedOut();
		}
	}

	function openFeedbackDialog(): void {
		if (authStatus !== "authenticated") {
			return;
		}

		setFeedbackErrorMessage(null);
		setFeedbackNoticeMessage(null);
		setIsFeedbackOpen(true);
	}

	function openAssetRecordsDialog(): void {
		if (authStatus !== "authenticated") {
			return;
		}

		setAssetRecordsDialogVersion((currentValue) => currentValue + 1);
		setIsAssetRecordsOpen(true);
	}

	function closeAssetRecordsDialog(): void {
		setIsAssetRecordsOpen(false);
	}

	function openEmailDialog(): void {
		if (authStatus !== "authenticated") {
			return;
		}

		setEmailDialogErrorMessage(null);
		setEmailNoticeMessage(null);
		setIsEmailDialogOpen(true);
	}

	function closeEmailDialog(): void {
		if (isSubmittingEmail) {
			return;
		}

		setEmailDialogErrorMessage(null);
		setIsEmailDialogOpen(false);
	}

	function closeFeedbackDialog(): void {
		if (isSubmittingFeedback) {
			return;
		}

		setFeedbackErrorMessage(null);
		setIsFeedbackOpen(false);
	}

	async function refreshFeedbackSummary(): Promise<void> {
		if (authStatus !== "authenticated") {
			setFeedbackInboxCount(0);
			return;
		}

		try {
			const summary = await getFeedbackSummary();
			setFeedbackInboxCount(summary.inbox_count);
		} catch {
			// Keep current badge value when summary refresh fails.
		}
	}

	async function loadAdminInbox(includeHidden: boolean, openDialog = true): Promise<void> {
		if (authStatus !== "authenticated" || currentUserId !== "admin") {
			return;
		}

		setAdminInboxErrorMessage(null);
		setIsAdminInboxShowingDismissed(includeHidden);
		if (openDialog) {
			setIsAdminInboxOpen(true);
		}
		setIsLoadingAdminInbox(true);

		try {
			const [userFeedbackItems, systemFeedbackItems, releaseNotes] = await Promise.all([
				listUserFeedbackForAdmin(includeHidden),
				listSystemFeedbackForAdmin(includeHidden),
				listReleaseNotesForCurrentUser(),
			]);
			setAdminUserFeedbackItems(userFeedbackItems.items);
			setAdminSystemFeedbackItems(systemFeedbackItems.items);
			setAdminInboxReleaseNotes(releaseNotes);
			await markReleaseNotesSeenForCurrentUser();
			await refreshFeedbackSummary();
		} catch (error) {
			setAdminInboxErrorMessage(
				error instanceof Error ? error.message : "消息加载失败，请稍后再试。",
			);
		} finally {
			setIsLoadingAdminInbox(false);
		}
	}

	async function openAdminInbox(): Promise<void> {
		await loadAdminInbox(false);
	}

	function closeAdminInbox(): void {
		if (isLoadingAdminInbox) {
			return;
		}

		setAdminInboxErrorMessage(null);
		setIsAdminInboxShowingDismissed(false);
		setIsAdminInboxOpen(false);
	}

	async function handleAdminInboxShowDismissedChange(showDismissed: boolean): Promise<void> {
		await loadAdminInbox(showDismissed, false);
	}

	async function openAdminReleaseNotes(): Promise<void> {
		if (authStatus !== "authenticated" || currentUserId !== "admin") {
			return;
		}

		setAdminReleaseNotesErrorMessage(null);
		setIsAdminReleaseNotesOpen(true);
		setIsLoadingAdminReleaseNotes(true);

		try {
			const releaseNotes = await listReleaseNotesForAdmin();
			setAdminReleaseNotes(releaseNotes);
		} catch (error) {
			setAdminReleaseNotesErrorMessage(
				error instanceof Error ? error.message : "更新日志加载失败，请稍后再试。",
			);
		} finally {
			setIsLoadingAdminReleaseNotes(false);
		}
	}

	function closeAdminReleaseNotes(): void {
		if (isLoadingAdminReleaseNotes) {
			return;
		}

		setAdminReleaseNotesErrorMessage(null);
		setIsAdminReleaseNotesOpen(false);
	}

	async function openUserInbox(): Promise<void> {
		if (authStatus !== "authenticated") {
			return;
		}

		setUserInboxErrorMessage(null);
		setIsUserInboxOpen(true);
		setIsLoadingUserInbox(true);

		try {
			const feedbackItems = await listFeedbackForCurrentUser();
			const releaseNotes = await listReleaseNotesForCurrentUser();
			setUserFeedbackItems(feedbackItems);
			setUserReleaseNotes(releaseNotes);
			await markFeedbackSeenForCurrentUser();
			await markReleaseNotesSeenForCurrentUser();
			await refreshFeedbackSummary();
		} catch (error) {
			setUserInboxErrorMessage(
				error instanceof Error ? error.message : "消息加载失败，请稍后再试。",
			);
		} finally {
			setIsLoadingUserInbox(false);
		}
	}

	function closeUserInbox(): void {
		if (isLoadingUserInbox) {
			return;
		}

		setUserInboxErrorMessage(null);
		setIsUserInboxOpen(false);
	}

	async function handleSubmitFeedback(message: string): Promise<void> {
		setIsSubmittingFeedback(true);
		setFeedbackErrorMessage(null);

		try {
			await submitUserFeedback({ message });
			setFeedbackNoticeMessage("问题反馈已记录。");
			setIsFeedbackOpen(false);
			await refreshFeedbackSummary();
		} catch (error) {
			setFeedbackErrorMessage(
				error instanceof Error ? error.message : "反馈提交失败，请稍后再试。",
			);
		} finally {
			setIsSubmittingFeedback(false);
		}
	}

	async function handleCloseFeedbackItem(feedbackId: number): Promise<void> {
		setIsLoadingAdminInbox(true);
		setAdminInboxErrorMessage(null);

		try {
			const updatedItem = await closeFeedbackForAdmin(feedbackId);
			setAdminUserFeedbackItems((currentItems) =>
				replaceRecordById(currentItems, updatedItem),
			);
			setAdminSystemFeedbackItems((currentItems) =>
				replaceRecordById(currentItems, updatedItem),
			);
			await refreshFeedbackSummary();
		} catch (error) {
			setAdminInboxErrorMessage(
				error instanceof Error ? error.message : "关闭反馈失败，请稍后再试。",
			);
		} finally {
			setIsLoadingAdminInbox(false);
		}
	}

	async function handleReplyFeedbackItem(
		feedbackId: number,
		replyMessage: string,
		close: boolean,
	): Promise<void> {
		setIsLoadingAdminInbox(true);
		setAdminInboxErrorMessage(null);

		try {
			const updatedItem = await replyToFeedbackForAdmin(feedbackId, {
				reply_message: replyMessage,
				close,
			});
			setAdminUserFeedbackItems((currentItems) =>
				replaceRecordById(currentItems, updatedItem),
			);
			setAdminSystemFeedbackItems((currentItems) =>
				replaceRecordById(currentItems, updatedItem),
			);
			await refreshFeedbackSummary();
		} catch (error) {
			setAdminInboxErrorMessage(
				error instanceof Error ? error.message : "回复失败，请稍后再试。",
			);
		} finally {
			setIsLoadingAdminInbox(false);
		}
	}

	async function handleHideAdminFeedbackItem(feedbackId: number): Promise<void> {
		setIsLoadingAdminInbox(true);
		setAdminInboxErrorMessage(null);

		try {
			await hideInboxMessageForCurrentUser({
				message_kind: "FEEDBACK",
				message_id: feedbackId,
			});
			if (isAdminInboxShowingDismissed) {
				const [userFeedbackItems, systemFeedbackItems] = await Promise.all([
					listUserFeedbackForAdmin(true),
					listSystemFeedbackForAdmin(true),
				]);
				setAdminUserFeedbackItems(userFeedbackItems.items);
				setAdminSystemFeedbackItems(systemFeedbackItems.items);
			} else {
				setAdminUserFeedbackItems((currentItems) =>
					removeRecordById(currentItems, feedbackId),
				);
				setAdminSystemFeedbackItems((currentItems) =>
					removeRecordById(currentItems, feedbackId),
				);
			}
			await refreshFeedbackSummary();
		} catch (error) {
			setAdminInboxErrorMessage(
				error instanceof Error ? error.message : "移除消息失败，请稍后再试。",
			);
		} finally {
			setIsLoadingAdminInbox(false);
		}
	}

	async function handleCreateReleaseNote(payload: ReleaseNoteInput): Promise<void> {
		setIsLoadingAdminReleaseNotes(true);
		setAdminReleaseNotesErrorMessage(null);

		try {
			const createdReleaseNote = await createReleaseNoteForAdmin(payload);
			setAdminReleaseNotes((currentItems) => [createdReleaseNote, ...currentItems]);
			await refreshFeedbackSummary();
		} catch (error) {
			setAdminReleaseNotesErrorMessage(
				error instanceof Error ? error.message : "创建更新日志失败，请稍后再试。",
			);
		} finally {
			setIsLoadingAdminReleaseNotes(false);
		}
	}

	async function handlePublishReleaseNote(releaseNoteId: number): Promise<void> {
		setIsLoadingAdminReleaseNotes(true);
		setAdminReleaseNotesErrorMessage(null);

		try {
			const publishedReleaseNote = await publishReleaseNoteForAdmin(releaseNoteId);
			setAdminReleaseNotes((currentItems) =>
				currentItems.map((item) =>
					item.id === publishedReleaseNote.id ? publishedReleaseNote : item
				),
			);
			await refreshFeedbackSummary();
		} catch (error) {
			setAdminReleaseNotesErrorMessage(
				error instanceof Error ? error.message : "发布更新日志失败，请稍后再试。",
			);
		} finally {
			setIsLoadingAdminReleaseNotes(false);
		}
	}

	async function handleSubmitEmail(payload: UserEmailUpdate): Promise<void> {
		setIsSubmittingEmail(true);
		setEmailDialogErrorMessage(null);
		setEmailNoticeMessage(null);

		try {
			const session = await updateCurrentUserEmail(payload);
			setCurrentUserEmail(session.email ?? null);
			setEmailNoticeMessage("邮箱已更新。");
			setIsEmailDialogOpen(false);
		} catch (error) {
			setEmailDialogErrorMessage(
				error instanceof Error ? error.message : "邮箱保存失败，请稍后再试。",
			);
		} finally {
			setIsSubmittingEmail(false);
		}
	}

	async function loadDashboard(
		options: { initial?: boolean; forceRefresh?: boolean } = {},
	): Promise<void> {
		if (authStatus !== "authenticated") {
			return;
		}

		if (dashboardRequestInFlightRef.current !== null) {
			pendingDashboardRefreshRef.current = true;
			pendingForceRefreshRef.current =
				pendingForceRefreshRef.current || Boolean(options.forceRefresh);
			return;
		}
		if (!currentUserId) {
			return;
		}

		if (options.initial) {
			setIsLoadingDashboard(true);
		}

		const requestUserId = currentUserId;
		const requestId = latestDashboardRequestIdRef.current + 1;
		latestDashboardRequestIdRef.current = requestId;
		dashboardRequestInFlightRef.current = requestId;
		setIsRefreshingDashboard(true);
		setErrorMessage(null);

		try {
			const nextDashboard = await getDashboard(Boolean(options.forceRefresh));
			const nextLastUpdatedAt = new Date().toISOString();
			if (
				latestDashboardRequestIdRef.current !== requestId ||
				currentUserIdRef.current !== requestUserId ||
				authStatusRef.current !== "authenticated"
			) {
				return;
			}
			setDashboard(nextDashboard);
			setLastUpdatedAt(nextLastUpdatedAt);
			writeCachedDashboardSnapshot(requestUserId, nextDashboard, nextLastUpdatedAt);
		} catch (error) {
			if (
				latestDashboardRequestIdRef.current !== requestId ||
				currentUserIdRef.current !== requestUserId ||
				authStatusRef.current !== "authenticated"
			) {
				return;
			}
			const nextErrorMessage = error instanceof Error
				? error.message
				: "无法加载资产总览，请确认后端服务是否启动。";
			if (isAuthenticationErrorMessage(nextErrorMessage)) {
				markSignedOut();
				return;
			}

			setErrorMessage(nextErrorMessage);
		} finally {
			if (dashboardRequestInFlightRef.current !== requestId) {
				return;
			}
			dashboardRequestInFlightRef.current = null;
			setIsRefreshingDashboard(false);
			setIsLoadingDashboard(false);
			if (pendingDashboardRefreshRef.current) {
				const shouldForceRefresh = pendingForceRefreshRef.current;
				pendingDashboardRefreshRef.current = false;
				pendingForceRefreshRef.current = false;
				void loadDashboard({ forceRefresh: shouldForceRefresh });
			}
		}
	}

	useEffect(() => {
		if (authStatus !== "authenticated" || !currentUserId || activeWorkspaceView !== "agent") {
			return;
		}
		if (hasLoadedAgentAuditRef.current && !agentAuditErrorMessage) {
			return;
		}

		void loadAgentAudit({ force: Boolean(agentAuditErrorMessage) });
	}, [activeWorkspaceView, agentAuditErrorMessage, authStatus, currentUserId]);

	async function loadAgentAudit(options: { force?: boolean } = {}): Promise<void> {
		if (!currentUserId) {
			return;
		}
		if (hasLoadedAgentAuditRef.current && !options.force) {
			return;
		}
		if (agentAuditRequestInFlightRef.current && !options.force) {
			await agentAuditRequestInFlightRef.current;
			return;
		}

		const requestId = latestAgentAuditRequestIdRef.current + 1;
		latestAgentAuditRequestIdRef.current = requestId;
		setIsLoadingAgentAudit(true);
		setAgentAuditErrorMessage(null);

		let requestPromise: Promise<void> | null = null;
		requestPromise = Promise.all([
			defaultAssetApiClient.listAgentRegistrations({
				includeAllUsers: currentUserId === "admin",
			}),
			defaultAssetApiClient.listAgentApiKeys(),
			defaultAssetApiClient.listAssetRecords({
				source: "AGENT",
				limit: 200,
			}),
			defaultAssetApiClient.listAssetRecords({
				source: "API",
				limit: 200,
			}),
		])
			.then(([registrations, apiKeys, agentRecords, directApiRecords]) => {
				if (latestAgentAuditRequestIdRef.current !== requestId) {
					return;
				}
				setAgentRegistrations(registrations);
				setAgentApiKeys(apiKeys);
				setAgentRecords(
					[...agentRecords, ...directApiRecords].sort((left, right) => {
						const leftTime = left.created_at ? Date.parse(left.created_at) : 0;
						const rightTime = right.created_at ? Date.parse(right.created_at) : 0;
						if (leftTime !== rightTime) {
							return rightTime - leftTime;
						}
						return right.id - left.id;
					}),
				);
				hasLoadedAgentAuditRef.current = true;
			})
			.catch((error) => {
				if (latestAgentAuditRequestIdRef.current !== requestId) {
					return;
				}
				hasLoadedAgentAuditRef.current = false;
				setAgentAuditErrorMessage(
					error instanceof Error ? error.message : "加载智能体审计失败。",
				);
			})
			.finally(() => {
				if (agentAuditRequestInFlightRef.current === requestPromise) {
					agentAuditRequestInFlightRef.current = null;
				}
				if (latestAgentAuditRequestIdRef.current === requestId) {
					setIsLoadingAgentAudit(false);
				}
			});
		agentAuditRequestInFlightRef.current = requestPromise;
		await requestPromise;
	}

	async function handleCreateAgentApiKey(payload: CreateAgentApiKeyInput): Promise<void> {
		const normalizedName = payload.name.trim();
		if (normalizedName.length < 3) {
			setAgentApiKeyErrorMessage("API Key 名称至少需要 3 个字符。");
			setAgentApiKeyNoticeMessage(null);
			return;
		}

		setIsCreatingAgentApiKey(true);
		setAgentApiKeyErrorMessage(null);
		setAgentApiKeyNoticeMessage(null);

		try {
			const issuedKey = await defaultAssetApiClient.createAgentApiKey({
				name: normalizedName,
				expires_in_days: payload.expires_in_days ?? null,
			});
			setIssuedAgentApiKey(issuedKey);
			await loadAgentAudit({ force: true });
		} catch (error) {
			setAgentApiKeyErrorMessage(
				error instanceof Error ? error.message : "API Key 创建失败，请稍后再试。",
			);
		} finally {
			setIsCreatingAgentApiKey(false);
		}
	}

	async function handleRevokeAgentApiKey(tokenId: number): Promise<void> {
		setRevokingAgentApiKeyId(tokenId);
		setAgentApiKeyErrorMessage(null);
		setAgentApiKeyNoticeMessage(null);

		try {
			await defaultAssetApiClient.revokeAgentApiKey(tokenId);
			setIssuedAgentApiKey((currentIssuedKey) =>
				currentIssuedKey?.id === tokenId ? null : currentIssuedKey,
			);
			setAgentApiKeyNoticeMessage("API Key 已撤销。");
			await loadAgentAudit({ force: true });
		} catch (error) {
			setAgentApiKeyErrorMessage(
				error instanceof Error ? error.message : "API Key 撤销失败，请稍后再试。",
			);
		} finally {
			setRevokingAgentApiKeyId(null);
		}
	}

	const isRecoveringSession = authStatus === "checking" && currentUserId !== null;

	if (isRecoveringSession) {
		return (
			<div className="app-shell">
				<header className="hero-panel">
					<div className="hero-copy-block">
						<div className="hero-copy-block__main">
							<p className="eyebrow">SESSION RESTORE</p>
							<h1>正在恢复登录状态</h1>
							<p className="hero-copy">确认当前会话之前，不展示本地缓存里的资产数据。</p>
							<p className="hero-subtle">验证通过后会继续回到你的工作区。</p>
						</div>
					</div>

					<div className="summary-grid" aria-label="恢复中的资产概览">
						{["总资产", "现金资产", "投资类", "固定资产", "其他", "负债"].map((label) => (
							<div key={label} className="stat-card neutral">
								<span>{label}</span>
								<strong>—</strong>
							</div>
						))}
					</div>
				</header>
			</div>
		);
	}

	if (!currentUserId || authStatus === "anonymous") {
		return (
			<LoginScreen
				loading={isSubmittingAuth}
				checkingSession={authStatus === "checking"}
				errorMessage={authErrorMessage}
				noticeMessage={authNoticeMessage}
				onLogin={(payload) => submitAuth("login", payload)}
				onRegister={(payload) => submitAuth("register", payload)}
				onResetPassword={submitPasswordReset}
			/>
		);
	}

	const hasAnyAsset =
		dashboard.cash_accounts.length > 0 ||
		dashboard.holdings.length > 0 ||
		dashboard.fixed_assets.length > 0 ||
		dashboard.liabilities.length > 0 ||
		dashboard.other_assets.length > 0;
	const isDashboardBusy = isLoadingDashboard || isRefreshingDashboard;
	const showDashboardValuePlaceholder =
		isLoadingDashboard &&
		lastUpdatedAt === null &&
		isDashboardSnapshotEmpty(dashboard);

	function formatDashboardSummaryValue(value: number): string {
		return showDashboardValuePlaceholder ? "—" : formatSummaryCny(value);
	}

	function getDashboardSummaryTitle(value: number): string {
		return showDashboardValuePlaceholder ? "正在恢复数据" : formatCny(value);
	}

	function requestDashboardRefresh(): void {
		void loadDashboard();
	}

	const hasDashboardSeedData = lastUpdatedAt !== null;
	const assetManagerSeeds = hasDashboardSeedData ? toAssetManagerSeeds(dashboard) : null;

	return (
		<div className="app-shell">
			<header className="hero-panel">
				<div className="hero-copy-block">
					<p className="eyebrow">OPEN TRAFI</p>
					<h1>你好，{currentUserId}</h1>
					<p className="hero-copy">你的资产与账户已隔离保存，并按分钟自动刷新。</p>
					<p className="hero-subtle">
						{currentUserEmail ? currentUserEmail : "未绑定邮箱，可用于找回密码。"}
					</p>
					<div className="hero-actions">
						<button
							type="button"
							className="hero-note hero-note--action"
							onClick={() => void loadDashboard({ forceRefresh: true })}
							disabled={isDashboardBusy}
						>
							<span
								className={`hero-note__status ${isDashboardBusy ? "is-active" : ""}`}
								aria-hidden="true"
							/>
							<span>
								{isDashboardBusy
									? "同步中..."
									: `最近更新：${formatLastUpdated(lastUpdatedAt)}`}
							</span>
						</button>
						<button
							type="button"
							className="hero-note hero-note--action"
							onClick={openEmailDialog}
							disabled={isSubmittingEmail}
						>
							{currentUserEmail ? "修改邮箱" : "绑定邮箱"}
						</button>
						<button
							type="button"
							className="hero-note hero-note--action"
							onClick={() =>
								currentUserId === "admin" ? void openAdminInbox() : void openUserInbox()
							}
							disabled={isLoadingAdminInbox || isLoadingUserInbox}
						>
							{feedbackInboxCount > 0 ? `消息 (${feedbackInboxCount})` : "消息"}
						</button>
						{currentUserId === "admin" ? (
							<button
								type="button"
								className="hero-note hero-note--action"
							onClick={() => void openAdminReleaseNotes()}
							disabled={isLoadingAdminReleaseNotes}
						>
							更新日志
						</button>
						) : null}
						<button
							type="button"
							className="hero-note hero-note--action"
							onClick={openAssetRecordsDialog}
						>
							记录
						</button>
						<button
							type="button"
							className="hero-note hero-note--action"
							onClick={openFeedbackDialog}
						>
							反馈问题
						</button>
						<button
							type="button"
							className="hero-note hero-note--action"
							onClick={() => void handleLogout()}
						>
							退出
						</button>
					</div>
					<div className="hero-rates" aria-label="实时汇率">
						<div className="rate-card">
							<span>USD/CNY</span>
							<strong>{formatFxRate(dashboard.usd_cny_rate)}</strong>
						</div>
						<div className="rate-card">
							<span>HKD/CNY</span>
							<strong>{formatFxRate(dashboard.hkd_cny_rate)}</strong>
						</div>
					</div>
				</div>

				<div className="summary-grid">
					<div className="stat-card coral">
						<span>总资产</span>
						<strong title={getDashboardSummaryTitle(dashboard.total_value_cny)}>
							{formatDashboardSummaryValue(dashboard.total_value_cny)}
						</strong>
					</div>
					<div className="stat-card blue">
						<span>现金资产</span>
						<strong title={getDashboardSummaryTitle(dashboard.cash_value_cny)}>
							{formatDashboardSummaryValue(dashboard.cash_value_cny)}
						</strong>
					</div>
					<div className="stat-card green">
						<span>投资类</span>
						<strong title={getDashboardSummaryTitle(dashboard.holdings_value_cny)}>
							{formatDashboardSummaryValue(dashboard.holdings_value_cny)}
						</strong>
					</div>
					<div className="stat-card violet">
						<span>固定资产</span>
						<strong title={getDashboardSummaryTitle(dashboard.fixed_assets_value_cny)}>
							{formatDashboardSummaryValue(dashboard.fixed_assets_value_cny)}
						</strong>
					</div>
					<div className="stat-card amber">
						<span>其他</span>
						<strong title={getDashboardSummaryTitle(dashboard.other_assets_value_cny)}>
							{formatDashboardSummaryValue(dashboard.other_assets_value_cny)}
						</strong>
					</div>
					<div className="stat-card danger">
						<span>负债</span>
						<strong title={getDashboardSummaryTitle(-dashboard.liabilities_value_cny)}>
							{formatDashboardSummaryValue(-dashboard.liabilities_value_cny)}
						</strong>
					</div>
				</div>
			</header>

			{feedbackNoticeMessage ? (
				<div className="banner info">
					<p>{feedbackNoticeMessage}</p>
				</div>
			) : null}

			{emailNoticeMessage ? (
				<div className="banner info">
					<p>{emailNoticeMessage}</p>
				</div>
			) : null}

			{errorMessage ? <div className="banner error">{errorMessage}</div> : null}

			{dashboard.warnings.length > 0 ? (
				<div className="banner warning">
					{dashboard.warnings.map((warning) => (
						<p key={warning}>{warning}</p>
					))}
				</div>
			) : null}

			{!hasAnyAsset && !isDashboardBusy && !errorMessage ? (
				<div className="banner info">暂无资产数据。</div>
			) : null}

			<WorkspaceShell activeView={activeWorkspaceView} onChange={setActiveWorkspaceView} />

			{mountedWorkspaceViews.insights ? (
				<section
					className="panel section-shell"
					hidden={activeWorkspaceView !== "insights"}
					aria-hidden={activeWorkspaceView !== "insights"}
				>
					<div className="section-head">
						<div>
							<p className="eyebrow">ANALYTICS</p>
							<h2>变化与分布</h2>
							<p className="section-copy">走势与结构。</p>
						</div>
					</div>

					<Suspense fallback={<div className="banner info">正在加载洞察模块...</div>}>
						<PortfolioAnalytics
							total_value_cny={dashboard.total_value_cny}
							cash_accounts={dashboard.cash_accounts}
							holdings={dashboard.holdings}
							fixed_assets={dashboard.fixed_assets}
							liabilities={dashboard.liabilities}
							other_assets={dashboard.other_assets}
							allocation={dashboard.allocation}
							second_series={dashboard.second_series}
							minute_series={dashboard.minute_series}
							hour_series={dashboard.hour_series}
							day_series={dashboard.day_series}
							month_series={dashboard.month_series}
							year_series={dashboard.year_series}
							holdings_return_second_series={dashboard.holdings_return_second_series}
							holdings_return_minute_series={dashboard.holdings_return_minute_series}
							holdings_return_hour_series={dashboard.holdings_return_hour_series}
							holdings_return_day_series={dashboard.holdings_return_day_series}
							holdings_return_month_series={dashboard.holdings_return_month_series}
							holdings_return_year_series={dashboard.holdings_return_year_series}
							holding_return_series={dashboard.holding_return_series}
							recent_holding_transactions={dashboard.recent_holding_transactions}
							loading={isLoadingDashboard}
						/>
					</Suspense>
				</section>
			) : null}
			{mountedWorkspaceViews.agent ? (
				<section
					className="panel section-shell"
					hidden={activeWorkspaceView !== "agent"}
					aria-hidden={activeWorkspaceView !== "agent"}
				>
					<div className="section-head">
						<div>
							<p className="eyebrow">AGENT</p>
							<h2>Agent 与 API</h2>
							<p className="section-copy">管理 API Key，查看活跃 Agent 与真实落库记录。</p>
						</div>
					</div>

					<AgentExecutionAuditPanel
						apiKeys={agentApiKeys}
						registrations={agentRegistrations}
						records={agentRecords}
						apiDocUrl="https://github.com/RockYYY888/opentrifi/blob/main/docs/agent-api.md"
						loading={isLoadingAgentAudit}
						errorMessage={agentAuditErrorMessage}
						apiKeyErrorMessage={agentApiKeyErrorMessage}
						apiKeyNoticeMessage={agentApiKeyNoticeMessage}
						issuedApiKey={issuedAgentApiKey}
						isCreatingApiKey={isCreatingAgentApiKey}
						revokingApiKeyId={revokingAgentApiKeyId}
						onCreateApiKey={(payload) => void handleCreateAgentApiKey(payload)}
						onRevokeApiKey={(tokenId) => void handleRevokeAgentApiKey(tokenId)}
						onDismissIssuedApiKey={() => {
							setIssuedAgentApiKey(null);
							setAgentApiKeyNoticeMessage(null);
						}}
					/>
				</section>
			) : null}
			<div
				className="integrated-stack"
				hidden={activeWorkspaceView !== "manage"}
				aria-hidden={activeWorkspaceView !== "manage"}
			>
				<AssetManager
					initialCashAccounts={
						assetManagerSeeds?.cashAccounts
					}
					initialHoldings={
						assetManagerSeeds?.holdings
					}
					initialFixedAssets={
						assetManagerSeeds?.fixedAssets
					}
					initialLiabilities={
						assetManagerSeeds?.liabilities
					}
					initialOtherAssets={
						assetManagerSeeds?.otherAssets
					}
					cashActions={assetManagerController.cashAccounts}
					cashTransferActions={assetManagerController.cashTransfers}
					holdingActions={assetManagerController.holdings}
					holdingTransactionActions={assetManagerController.holdingTransactions}
					fixedAssetActions={assetManagerController.fixedAssets}
					liabilityActions={assetManagerController.liabilities}
					otherAssetActions={assetManagerController.otherAssets}
					title="资产管理"
					description="自动同步。"
					loadOnMount
					maxStartedOnDate={dashboard.server_today || undefined}
					displayFxRates={{
						CNY: 1,
						USD: dashboard.usd_cny_rate,
						HKD: dashboard.hkd_cny_rate,
					}}
					onRecordsCommitted={() => {
						requestDashboardRefresh();
						setAssetRecordRefreshToken((currentValue) => currentValue + 1);
					}}
				/>
			</div>

			<FeedbackDialog
				open={isFeedbackOpen}
				busy={isSubmittingFeedback}
				errorMessage={feedbackErrorMessage}
				onClose={closeFeedbackDialog}
				onSubmit={handleSubmitFeedback}
			/>
			<AdminFeedbackDialog
				open={isAdminInboxOpen}
				busy={isLoadingAdminInbox}
				viewerUserId={currentUserId ?? "anonymous"}
				userItems={adminUserFeedbackItems}
				systemItems={adminSystemFeedbackItems}
				releaseNotes={adminInboxReleaseNotes}
				showDismissed={isAdminInboxShowingDismissed}
				errorMessage={adminInboxErrorMessage}
				onClose={closeAdminInbox}
				onShowDismissedChange={handleAdminInboxShowDismissedChange}
				onHideItem={handleHideAdminFeedbackItem}
				onCloseItem={handleCloseFeedbackItem}
				onReplyItem={handleReplyFeedbackItem}
			/>
			<AdminReleaseNotesDialog
				open={isAdminReleaseNotesOpen}
				busy={isLoadingAdminReleaseNotes}
				releaseNotes={adminReleaseNotes}
				errorMessage={adminReleaseNotesErrorMessage}
				onClose={closeAdminReleaseNotes}
				onCreateReleaseNote={handleCreateReleaseNote}
				onPublishReleaseNote={handlePublishReleaseNote}
			/>
			<UserFeedbackInboxDialog
				open={isUserInboxOpen}
				busy={isLoadingUserInbox}
				viewerUserId={currentUserId ?? "anonymous"}
				items={userFeedbackItems}
				releaseNotes={userReleaseNotes}
				errorMessage={userInboxErrorMessage}
				onClose={closeUserInbox}
			/>
			<EmailDialog
				open={isEmailDialogOpen}
				busy={isSubmittingEmail}
				initialEmail={currentUserEmail}
				errorMessage={emailDialogErrorMessage}
				onClose={closeEmailDialog}
				onSubmit={(email) => handleSubmitEmail({ email })}
			/>
			<AssetRecordsDialog
				key={assetRecordsDialogVersion}
				open={isAssetRecordsOpen}
				onClose={closeAssetRecordsDialog}
				onLoadRecords={defaultAssetApiClient.listAssetRecords}
				refreshToken={assetRecordRefreshToken}
			/>
		</div>
	);
}

export default App;
