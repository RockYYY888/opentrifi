import { useEffect, useRef, useState } from "react";

import { LoginScreen } from "./components/auth/LoginScreen";
import { AppDialogs } from "./app/AppDialogs";
import { AppHeroPanel } from "./app/AppHeroPanel";
import { AppRecoveryScreen } from "./app/AppRecoveryScreen";
import { AppWorkspaceSections } from "./app/AppWorkspaceSections";
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
	getMillisecondsUntilNextMinute,
	getMillisecondsUntilNextSecond,
	isDashboardSnapshotEmpty,
	readCachedDashboardSnapshot,
	toAssetManagerSeeds,
	writeCachedDashboardSnapshot,
} from "./app/dashboardRefresh";
import {
	DEFAULT_MOUNTED_WORKSPACES,
	type WorkspaceView,
} from "./app/workspaceTypes";
import { useFeedbackWorkspace } from "./app/useFeedbackWorkspace";
import { useAgentWorkspace } from "./app/useAgentWorkspace";
import { createAssetManagerController, defaultAssetApiClient } from "./lib/assetApi";
import {
	getAuthSession,
	loginWithPassword,
	logoutCurrentUser,
	registerWithPassword,
	resetPasswordWithEmail,
} from "./lib/authApi";
import { getDashboard } from "./lib/dashboardApi";
import { useHasActiveAutoRefreshGuards } from "./lib/autoRefreshGuards";
import type {
	AuthLoginCredentials,
	AuthRegisterCredentials,
	PasswordResetPayload,
} from "./types/auth";
import { EMPTY_DASHBOARD, type DashboardResponse } from "./types/dashboard";

const AGENT_AUDIT_BACKGROUND_REFRESH_DELAY_MS = 1500;
const INSIGHTS_DASHBOARD_REFRESH_INTERVAL_MS = 5 * 1000;
const DEFAULT_DASHBOARD_REFRESH_INTERVAL_MS = 60 * 1000;
const assetManagerController = createAssetManagerController(defaultAssetApiClient);

type DashboardLoadOptions = {
	initial?: boolean;
	forceRefresh?: boolean;
	auto?: boolean;
};

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
	const [activeWorkspaceView, setActiveWorkspaceView] = useState<WorkspaceView>("manage");
	const [mountedWorkspaceViews, setMountedWorkspaceViews] = useState<Record<WorkspaceView, boolean>>(
		DEFAULT_MOUNTED_WORKSPACES,
	);
	const dashboardRequestInFlightRef = useRef<number | null>(null);
	const latestDashboardRequestIdRef = useRef(0);
	const pendingDashboardRefreshRef = useRef(false);
	const pendingForceRefreshRef = useRef(false);
	const autoRefreshResumeRef = useRef(false);
	const currentUserIdRef = useRef<string | null>(currentUserId);
	const authStatusRef = useRef<AuthStatus>(authStatus);
	const isAutoRefreshBlocked = useHasActiveAutoRefreshGuards();
	const {
		agentApiKeyErrorMessage,
		agentApiKeyNoticeMessage,
		agentApiKeys,
		agentAuditErrorMessage,
		agentRecords,
		agentRegistrations,
		clearIssuedAgentApiKey,
		handleCreateAgentApiKey,
		handleRevokeAgentApiKey,
		hasLoadedAgentAuditRef,
		isCreatingAgentApiKey,
		isLoadingAgentAudit,
		issuedAgentApiKey,
		loadAgentAudit,
		resetAgentWorkspaceState,
		revokingAgentApiKeyId,
	} = useAgentWorkspace(currentUserId);
	const {
		adminInboxErrorMessage,
		adminInboxReleaseNotes,
		adminReleaseNotes,
		adminReleaseNotesErrorMessage,
		adminSystemFeedbackItems,
		adminUserFeedbackItems,
		closeAdminInbox,
		closeAdminReleaseNotes,
		closeEmailDialog,
		closeFeedbackDialog,
		closeUserInbox,
		emailDialogErrorMessage,
		emailNoticeMessage,
		feedbackErrorMessage,
		feedbackInboxCount,
		feedbackNoticeMessage,
		handleAdminInboxShowDismissedChange,
		handleCloseFeedbackItem,
		handleCreateReleaseNote,
		handleHideAdminFeedbackItem,
		handlePublishReleaseNote,
		handleReplyFeedbackItem,
		handleSubmitEmail,
		handleSubmitFeedback,
		isAdminInboxOpen,
		isAdminInboxShowingDismissed,
		isAdminReleaseNotesOpen,
		isEmailDialogOpen,
		isFeedbackOpen,
		isLoadingAdminInbox,
		isLoadingAdminReleaseNotes,
		isLoadingUserInbox,
		isSubmittingEmail,
		isSubmittingFeedback,
		isUserInboxOpen,
		openAdminInbox,
		openAdminReleaseNotes,
		openEmailDialog,
		openFeedbackDialog,
		openUserInbox,
		refreshFeedbackSummary,
		resetFeedbackWorkspaceState,
		userFeedbackItems,
		userInboxErrorMessage,
		userReleaseNotes,
	} = useFeedbackWorkspace({
		authStatus,
		currentUserId,
		onEmailUpdated: setCurrentUserEmail,
	});

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
		resetFeedbackWorkspaceState();
		setIsAssetRecordsOpen(false);
		setAssetRecordsDialogVersion(0);
		setAssetRecordRefreshToken(0);
		setActiveWorkspaceView("manage");
		setMountedWorkspaceViews(DEFAULT_MOUNTED_WORKSPACES);
		resetAgentWorkspaceState();
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
		resetFeedbackWorkspaceState();
		setIsAssetRecordsOpen(false);
		setAssetRecordsDialogVersion(0);
		setAssetRecordRefreshToken(0);
		setActiveWorkspaceView("manage");
		setMountedWorkspaceViews(DEFAULT_MOUNTED_WORKSPACES);
		resetAgentWorkspaceState();
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
			void loadDashboard({ auto: true });
			refreshTimer = window.setInterval(() => {
				if (document.visibilityState !== "visible") {
					return;
				}
				void loadDashboard({ auto: true });
			}, useSecondLevelRefresh
				? INSIGHTS_DASHBOARD_REFRESH_INTERVAL_MS
				: DEFAULT_DASHBOARD_REFRESH_INTERVAL_MS);
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

	async function loadDashboard(
		options: DashboardLoadOptions = {},
	): Promise<void> {
		if (authStatus !== "authenticated") {
			return;
		}

		if (dashboardRequestInFlightRef.current !== null) {
			if (options.auto && !options.forceRefresh) {
				return;
			}
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

	const isRecoveringSession = authStatus === "checking" && currentUserId !== null;

	if (isRecoveringSession) {
		return <AppRecoveryScreen />;
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

	const hasDashboardSeedData = lastUpdatedAt !== null;
	const assetManagerSeeds = hasDashboardSeedData ? toAssetManagerSeeds(dashboard) : null;

	return (
		<div className="app-shell">
			<AppHeroPanel
				currentUserId={currentUserId}
				currentUserEmail={currentUserEmail}
				dashboard={dashboard}
				feedbackInboxCount={feedbackInboxCount}
				isDashboardBusy={isDashboardBusy}
				isLoadingAdminInbox={isLoadingAdminInbox}
				isLoadingAdminReleaseNotes={isLoadingAdminReleaseNotes}
				isLoadingUserInbox={isLoadingUserInbox}
				isSubmittingEmail={isSubmittingEmail}
				lastUpdatedAt={lastUpdatedAt}
				showDashboardValuePlaceholder={showDashboardValuePlaceholder}
				onForceDashboardRefresh={() => void loadDashboard({ forceRefresh: true })}
				onOpenAdminInbox={() => void openAdminInbox()}
				onOpenAdminReleaseNotes={() => void openAdminReleaseNotes()}
				onOpenAssetRecords={openAssetRecordsDialog}
				onOpenEmail={openEmailDialog}
				onOpenFeedback={openFeedbackDialog}
				onOpenUserInbox={() => void openUserInbox()}
				onLogout={() => void handleLogout()}
			/>

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

			<AppWorkspaceSections
				activeWorkspaceView={activeWorkspaceView}
				agentApiKeyErrorMessage={agentApiKeyErrorMessage}
				agentApiKeyNoticeMessage={agentApiKeyNoticeMessage}
				agentApiKeys={agentApiKeys}
				agentAuditErrorMessage={agentAuditErrorMessage}
				agentRecords={agentRecords}
				agentRegistrations={agentRegistrations}
				assetManagerController={assetManagerController}
				assetManagerSeeds={assetManagerSeeds}
				dashboard={dashboard}
				isCreatingAgentApiKey={isCreatingAgentApiKey}
				isLoadingAgentAudit={isLoadingAgentAudit}
				isLoadingDashboard={isLoadingDashboard}
				issuedAgentApiKey={issuedAgentApiKey}
				mountedWorkspaceViews={mountedWorkspaceViews}
				revokingAgentApiKeyId={revokingAgentApiKeyId}
				onCreateAgentApiKey={(payload) => void handleCreateAgentApiKey(payload)}
				onDismissIssuedApiKey={clearIssuedAgentApiKey}
				onRecordsCommitted={() => {
					void loadDashboard();
					setAssetRecordRefreshToken((currentValue) => currentValue + 1);
				}}
				onRevokeAgentApiKey={(tokenId) => void handleRevokeAgentApiKey(tokenId)}
				onWorkspaceChange={setActiveWorkspaceView}
			/>

			<AppDialogs
				adminInboxErrorMessage={adminInboxErrorMessage}
				adminInboxReleaseNotes={adminInboxReleaseNotes}
				adminReleaseNotes={adminReleaseNotes}
				adminReleaseNotesErrorMessage={adminReleaseNotesErrorMessage}
				adminSystemFeedbackItems={adminSystemFeedbackItems}
				adminUserFeedbackItems={adminUserFeedbackItems}
				assetRecordRefreshToken={assetRecordRefreshToken}
				assetRecordsDialogVersion={assetRecordsDialogVersion}
				currentUserEmail={currentUserEmail}
				currentUserId={currentUserId}
				emailDialogErrorMessage={emailDialogErrorMessage}
				feedbackErrorMessage={feedbackErrorMessage}
				isAdminInboxOpen={isAdminInboxOpen}
				isAdminInboxShowingDismissed={isAdminInboxShowingDismissed}
				isAdminReleaseNotesOpen={isAdminReleaseNotesOpen}
				isAssetRecordsOpen={isAssetRecordsOpen}
				isEmailDialogOpen={isEmailDialogOpen}
				isFeedbackOpen={isFeedbackOpen}
				isLoadingAdminInbox={isLoadingAdminInbox}
				isLoadingAdminReleaseNotes={isLoadingAdminReleaseNotes}
				isLoadingUserInbox={isLoadingUserInbox}
				isSubmittingEmail={isSubmittingEmail}
				isSubmittingFeedback={isSubmittingFeedback}
				isUserInboxOpen={isUserInboxOpen}
				listAssetRecords={defaultAssetApiClient.listAssetRecords}
				userFeedbackItems={userFeedbackItems}
				userInboxErrorMessage={userInboxErrorMessage}
				userReleaseNotes={userReleaseNotes}
				onCloseAdminInbox={closeAdminInbox}
				onCloseAdminReleaseNotes={closeAdminReleaseNotes}
				onCloseAssetRecords={closeAssetRecordsDialog}
				onCloseEmail={closeEmailDialog}
				onCloseFeedback={closeFeedbackDialog}
				onCloseFeedbackItem={handleCloseFeedbackItem}
				onCloseUserInbox={closeUserInbox}
				onCreateReleaseNote={handleCreateReleaseNote}
				onHideAdminFeedbackItem={handleHideAdminFeedbackItem}
				onPublishReleaseNote={handlePublishReleaseNote}
				onReplyFeedbackItem={handleReplyFeedbackItem}
				onShowDismissedChange={handleAdminInboxShowDismissedChange}
				onSubmitEmail={handleSubmitEmail}
				onSubmitFeedback={handleSubmitFeedback}
			/>
		</div>
	);
}

export default App;
