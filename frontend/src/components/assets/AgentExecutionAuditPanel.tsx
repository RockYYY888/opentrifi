import { useEffect, useMemo, useState, type FormEvent } from "react";

import type {
	AgentApiKeyIssueRecord,
	AgentApiKeyRecord,
	AgentRegistrationRecord,
	AssetRecordRecord,
	CreateAgentApiKeyInput,
} from "../../types/assets";
import {
	API_KEY_NAME_PATTERN,
	copyTextToClipboard,
	getExpirySelectionValue,
	isApiKeyActive,
	isApiKeyExpiringSoon,
	MAX_ACTIVE_API_KEYS,
	parseExpirySelectionValue,
	type ActivityAssetClassFilter,
	type ActivitySourceFilter,
} from "./AgentExecutionAuditModel";
import { AgentActivityDialog } from "./AgentActivityDialog";
import { AgentApiKeyDialogs } from "./AgentApiKeyDialogs";
import { RegisteredAgentList } from "./RegisteredAgentList";
import "./asset-components.css";

export interface AgentExecutionAuditPanelProps {
	apiKeys: AgentApiKeyRecord[];
	registrations: AgentRegistrationRecord[];
	records: AssetRecordRecord[];
	apiDocUrl: string;
	loading?: boolean;
	errorMessage?: string | null;
	apiKeyErrorMessage?: string | null;
	apiKeyNoticeMessage?: string | null;
	issuedApiKey?: AgentApiKeyIssueRecord | null;
	isCreatingApiKey?: boolean;
	revokingApiKeyId?: number | null;
	onCreateApiKey?: (payload: CreateAgentApiKeyInput) => void;
	onRevokeApiKey?: (tokenId: number) => void;
	onDismissIssuedApiKey?: () => void;
}

export function AgentExecutionAuditPanel({
	apiKeys,
	registrations,
	records,
	apiDocUrl,
	loading = false,
	errorMessage = null,
	apiKeyErrorMessage = null,
	apiKeyNoticeMessage = null,
	issuedApiKey = null,
	isCreatingApiKey = false,
	revokingApiKeyId = null,
	onCreateApiKey,
	onRevokeApiKey,
	onDismissIssuedApiKey,
}: AgentExecutionAuditPanelProps) {
	const [draftApiKeyName, setDraftApiKeyName] = useState("");
	const [draftExpirySelection, setDraftExpirySelection] = useState("30");
	const [clipboardNotice, setClipboardNotice] = useState<string | null>(null);
	const [clipboardError, setClipboardError] = useState<string | null>(null);
	const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false);
	const [isManageKeysDialogOpen, setIsManageKeysDialogOpen] = useState(false);
	const [isActivityDialogOpen, setIsActivityDialogOpen] = useState(false);
	const [isInactiveAgentsOpen, setIsInactiveAgentsOpen] = useState(false);
	const [pendingRevokeApiKey, setPendingRevokeApiKey] = useState<AgentApiKeyRecord | null>(null);
	const [activitySourceFilter, setActivitySourceFilter] =
		useState<ActivitySourceFilter>("ALL");
	const [activityAssetClassFilter, setActivityAssetClassFilter] =
		useState<ActivityAssetClassFilter>("ALL");

	const activeApiKeys = useMemo(
		() => apiKeys.filter((apiKey) => isApiKeyActive(apiKey)),
		[apiKeys],
	);
	const apiKeyByName = useMemo(
		() => new Map(activeApiKeys.map((apiKey) => [apiKey.name, apiKey])),
		[activeApiKeys],
	);
	const activeRegistrations = useMemo(
		() => registrations.filter((registration) => registration.status === "ACTIVE"),
		[registrations],
	);
	const inactiveRegistrations = useMemo(
		() => registrations.filter((registration) => registration.status !== "ACTIVE"),
		[registrations],
	);
	const expiringApiKeys = useMemo(
		() => activeApiKeys.filter((apiKey) => isApiKeyExpiringSoon(apiKey)),
		[activeApiKeys],
	);
	const activeApiKeyCount = activeApiKeys.length;
	const expiringApiKeyCount = expiringApiKeys.length;
	const activeApiKeySummary = `有效 Key ${activeApiKeyCount} / ${MAX_ACTIVE_API_KEYS}`;
	const normalizedDraftApiKeyName = draftApiKeyName.trim();
	const isDraftNameValid = API_KEY_NAME_PATTERN.test(normalizedDraftApiKeyName);

	const filteredRecords = useMemo(() => {
		return records.filter((record) => {
			if (activitySourceFilter !== "ALL" && record.source !== activitySourceFilter) {
				return false;
			}
			if (activityAssetClassFilter !== "ALL" && record.asset_class !== activityAssetClassFilter) {
				return false;
			}
			return true;
		});
	}, [activityAssetClassFilter, activitySourceFilter, records]);

	useEffect(() => {
		if (!pendingRevokeApiKey) {
			return;
		}

		const stillExists = activeApiKeys.some((apiKey) => apiKey.id === pendingRevokeApiKey.id);
		if (!stillExists) {
			setPendingRevokeApiKey(null);
		}
	}, [activeApiKeys, pendingRevokeApiKey]);

	useEffect(() => {
		if (issuedApiKey) {
			setIsCreateDialogOpen(true);
			setDraftApiKeyName("");
			setDraftExpirySelection(getExpirySelectionValue(null));
		}
	}, [issuedApiKey]);

	function resetClipboardMessages(): void {
		setClipboardNotice(null);
		setClipboardError(null);
	}

	function closeCreateDialog(): void {
		setIsCreateDialogOpen(false);
		setDraftApiKeyName("");
		setDraftExpirySelection("30");
		resetClipboardMessages();
		if (issuedApiKey) {
			onDismissIssuedApiKey?.();
		}
	}

	function closeManageKeysDialog(): void {
		setIsManageKeysDialogOpen(false);
		setPendingRevokeApiKey(null);
	}

	function requestRevokeApiKey(apiKey: AgentApiKeyRecord): void {
		setPendingRevokeApiKey(apiKey);
	}

	function cancelRevokeApiKey(): void {
		setPendingRevokeApiKey(null);
	}

	function confirmRevokeApiKey(): void {
		if (!pendingRevokeApiKey) {
			return;
		}

		onRevokeApiKey?.(pendingRevokeApiKey.id);
		setPendingRevokeApiKey(null);
	}

	function handleCreateApiKeySubmit(event: FormEvent<HTMLFormElement>): void {
		event.preventDefault();
		resetClipboardMessages();
		if (!isDraftNameValid) {
			setClipboardError("API Key 名称只能使用小写字母和连字符，例如 daily-sync。");
			return;
		}
		onCreateApiKey?.({
			name: normalizedDraftApiKeyName,
			expires_in_days: parseExpirySelectionValue(draftExpirySelection),
		});
	}

	async function handleCopyIssuedApiKey(): Promise<void> {
		if (!issuedApiKey) {
			return;
		}

		try {
			await copyTextToClipboard(issuedApiKey.access_token);
			setClipboardError(null);
			setClipboardNotice("已复制到剪贴板。请立即保存，这串 API Key 关闭后不会再次显示。");
		} catch (error) {
			setClipboardNotice(null);
			setClipboardError(error instanceof Error ? error.message : "复制失败，请手动保存。");
		}
	}

	return (
		<section className="asset-manager__panel">
			<div className="asset-manager__list-head">
				<div>
					<p className="asset-manager__eyebrow">AGENT WORKSPACE</p>
					<h3>智能体工作台</h3>
					<p>查看已注册的活跃 Agent，并在“查看记录”里筛选直连 API 与 Agent 的落库记录。</p>
				</div>
				<div className="asset-manager__panel-actions">
					<button
						type="button"
						className="hero-note hero-note--action"
						onClick={() => setIsActivityDialogOpen(true)}
					>
						查看记录
					</button>
					<button
						type="button"
						className="hero-note hero-note--action"
						onClick={() => setIsManageKeysDialogOpen(true)}
					>
						{activeApiKeySummary}
					</button>
				</div>
			</div>

			<div className="agent-workspace__top-grid">
				<div className="asset-manager__helper-block">
					<strong>Agent API</strong>
					<p className="agent-workspace__doc-copy">
						文档已整理到 GitHub，供外部 Agent 或自动化服务按约定调用。普通前端登录仍使用账号密码；
						API Key 只用于外部调用鉴权。
					</p>
					<div className="agent-workspace__doc-actions">
						<a
							className="hero-note hero-note--action agent-workspace__doc-link"
							href={apiDocUrl}
							target="_blank"
							rel="noreferrer"
						>
							打开 API 文档
						</a>
						<button
							type="button"
							className="hero-note hero-note--action"
							onClick={() => {
								resetClipboardMessages();
								setIsCreateDialogOpen(true);
							}}
						>
							创建新的 API Key
						</button>
					</div>
				</div>
				<div
					className="asset-manager__summary agent-workspace__summary"
					data-testid="agent-workspace-summary"
				>
					<div className="asset-manager__summary-card">
						<span>活跃 Agent</span>
						<strong>{activeRegistrations.length}</strong>
					</div>
					<div className="asset-manager__summary-card">
						<span>3 天内到期</span>
						<strong>{expiringApiKeyCount}</strong>
					</div>
				</div>
			</div>

			{errorMessage ? (
				<div className="asset-manager__message asset-manager__message--error">
					{errorMessage}
				</div>
			) : null}
			{apiKeyErrorMessage ? (
				<div className="asset-manager__message asset-manager__message--error">
					{apiKeyErrorMessage}
				</div>
			) : null}
			{apiKeyNoticeMessage ? (
				<div className="asset-manager__status-note">{apiKeyNoticeMessage}</div>
			) : null}
			{expiringApiKeyCount > 0 ? (
				<div className="asset-manager__status-note asset-manager__status-note--warning">
					当前有 {expiringApiKeyCount} 个 API Key 将在 3 天内过期。建议提前轮换并更新调用方配置，
					避免自动化请求中断。
				</div>
			) : null}

			{loading ? (
				<div className="asset-manager__empty-state">正在加载智能体工作台...</div>
			) : (
				<div className="agent-workspace__sections">
					<section className="agent-workspace__section">
						<div className="asset-manager__list-head">
							<div>
								<p className="asset-manager__eyebrow">ACTIVE AGENTS</p>
								<h3>活跃 Agent</h3>
								<p>查看已注册的活跃 Agent。</p>
							</div>
						</div>
						<RegisteredAgentList
							registrations={activeRegistrations}
							apiKeyByName={apiKeyByName}
							emptyMessage="当前还没有活跃 Agent。"
						/>
					</section>

					<section className="agent-workspace__section">
						<button
							type="button"
							className={`asset-manager__summary-card agent-workspace__disclosure ${
								isInactiveAgentsOpen ? "is-active" : ""
							}`}
							onClick={() => setIsInactiveAgentsOpen((current) => !current)}
							aria-expanded={isInactiveAgentsOpen}
						>
							<span>非活跃 Agent</span>
							<strong>{inactiveRegistrations.length}</strong>
							<p>
								{isInactiveAgentsOpen
									? "收起非活跃 Agent 列表"
									: "点击展开查看已失活或已停止访问的 Agent"}
							</p>
						</button>
						{isInactiveAgentsOpen ? (
							<RegisteredAgentList
								registrations={inactiveRegistrations}
								apiKeyByName={apiKeyByName}
								emptyMessage="当前没有非活跃 Agent。"
							/>
						) : null}
					</section>
				</div>
			)}

			<AgentApiKeyDialogs
				activeApiKeyCount={activeApiKeyCount}
				activeApiKeySummary={activeApiKeySummary}
				activeApiKeys={activeApiKeys}
				apiKeyErrorMessage={apiKeyErrorMessage}
				apiKeyNoticeMessage={apiKeyNoticeMessage}
				clipboardError={clipboardError}
				clipboardNotice={clipboardNotice}
				draftApiKeyName={draftApiKeyName}
				draftExpirySelection={draftExpirySelection}
				expiringApiKeyCount={expiringApiKeyCount}
				isCreateDialogOpen={isCreateDialogOpen}
				isCreatingApiKey={isCreatingApiKey}
				isDraftNameValid={isDraftNameValid}
				isManageKeysDialogOpen={isManageKeysDialogOpen}
				issuedApiKey={issuedApiKey}
				normalizedDraftApiKeyName={normalizedDraftApiKeyName}
				pendingRevokeApiKey={pendingRevokeApiKey}
				revokingApiKeyId={revokingApiKeyId}
				onCancelRevokeApiKey={cancelRevokeApiKey}
				onCloseCreateDialog={closeCreateDialog}
				onCloseManageKeysDialog={closeManageKeysDialog}
				onConfirmRevokeApiKey={confirmRevokeApiKey}
				onCopyIssuedApiKey={() => void handleCopyIssuedApiKey()}
				onCreateApiKeySubmit={handleCreateApiKeySubmit}
				onDraftApiKeyNameChange={setDraftApiKeyName}
				onDraftExpirySelectionChange={setDraftExpirySelection}
				onRequestRevokeApiKey={requestRevokeApiKey}
			/>

			<AgentActivityDialog
				activityAssetClassFilter={activityAssetClassFilter}
				activitySourceFilter={activitySourceFilter}
				filteredRecords={filteredRecords}
				open={isActivityDialogOpen}
				onActivityAssetClassFilterChange={setActivityAssetClassFilter}
				onActivitySourceFilterChange={setActivitySourceFilter}
				onClose={() => setIsActivityDialogOpen(false)}
			/>
		</section>
	);
}
