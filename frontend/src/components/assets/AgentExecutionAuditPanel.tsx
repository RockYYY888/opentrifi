import { useEffect, useMemo, useState, type FormEvent } from "react";

import {
	formatTimestamp,
	formatTimestampWithYear,
} from "../../lib/assetFormatting";
import { ASSET_CLASS_BADGE_LABELS } from "../../lib/assetRecordMeta";
import type {
	AgentApiKeyIssueRecord,
	AgentApiKeyRecord,
	AgentRegistrationRecord,
	AssetRecordAssetClass,
	AssetRecordRecord,
	CreateAgentApiKeyInput,
} from "../../types/assets";
import { AgentRecordList } from "./AgentRecordList";
import { AgentWorkspaceDialog } from "./AgentWorkspaceDialog";
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

const MAX_ACTIVE_API_KEYS = 5;
const MAX_DAILY_API_KEY_CREATIONS = 10;
const API_KEY_EXPIRY_WARNING_WINDOW_MS = 3 * 24 * 60 * 60 * 1000;
const API_KEY_NAME_PATTERN = /^[a-z]+(?:-[a-z]+)*$/;
const API_KEY_HINT_PREFIX = "sk-";
const API_KEY_HINT_VISIBLE_CHARS = 2;
const API_KEY_HINT_MASK = "*".repeat(11);
const GENERIC_API_KEY_HINT_PLACEHOLDER = `${API_KEY_HINT_PREFIX}xx${API_KEY_HINT_MASK}`;
const ASSET_CLASS_FILTERS: Array<{
	value: "ALL" | AssetRecordAssetClass;
	label: string;
}> = [
	{ value: "ALL", label: "全部类别" },
	{ value: "cash", label: ASSET_CLASS_BADGE_LABELS.cash },
	{ value: "investment", label: ASSET_CLASS_BADGE_LABELS.investment },
	{ value: "fixed", label: ASSET_CLASS_BADGE_LABELS.fixed },
	{ value: "liability", label: ASSET_CLASS_BADGE_LABELS.liability },
	{ value: "other", label: ASSET_CLASS_BADGE_LABELS.other },
];
const EXPIRY_OPTIONS: Array<{
	value: string;
	label: string;
	description: string;
}> = [
	{ value: "7", label: "7 天", description: "适合短期调试或临时自动化。" },
	{ value: "30", label: "30 天", description: "适合日常本地开发或轻量服务。" },
	{ value: "365", label: "365 天", description: "适合长期稳定的生产接入。" },
	{ value: "never", label: "不过期", description: "仅建议在你有轮换机制时使用。" },
];

type ActivitySourceFilter = "ALL" | "AGENT" | "API";
type ActivityAssetClassFilter = "ALL" | AssetRecordAssetClass;

function isApiKeyActive(apiKey: AgentApiKeyRecord): boolean {
	if (apiKey.revoked_at) {
		return false;
	}
	if (!apiKey.expires_at) {
		return true;
	}
	const expiresAt = Date.parse(apiKey.expires_at);
	return Number.isFinite(expiresAt) && expiresAt > Date.now();
}

function isApiKeyExpiringSoon(apiKey: AgentApiKeyRecord): boolean {
	if (!apiKey.expires_at || apiKey.revoked_at) {
		return false;
	}
	const expiresAt = Date.parse(apiKey.expires_at);
	if (!Number.isFinite(expiresAt)) {
		return false;
	}
	const remainingMs = expiresAt - Date.now();
	return remainingMs > 0 && remainingMs <= API_KEY_EXPIRY_WARNING_WINDOW_MS;
}

function getVisibleTokenHint(tokenHint: string): string {
	const normalized = tokenHint.trim();
	if (!normalized.startsWith(API_KEY_HINT_PREFIX)) {
		return GENERIC_API_KEY_HINT_PLACEHOLDER;
	}

	const visibleFragment = normalized
		.slice(API_KEY_HINT_PREFIX.length)
		.replace(/\*/g, "")
		.slice(0, API_KEY_HINT_VISIBLE_CHARS)
		.padEnd(API_KEY_HINT_VISIBLE_CHARS, "x");
	return `${API_KEY_HINT_PREFIX}${visibleFragment}${API_KEY_HINT_MASK}`;
}

function getApiKeyStatus(apiKey: AgentApiKeyRecord): {
	label: string;
	className: string;
} {
	if (apiKey.revoked_at) {
		return {
			label: "已删除",
			className: "asset-manager__badge asset-manager__badge--muted",
		};
	}
	if (!isApiKeyActive(apiKey)) {
		return {
			label: "已过期",
			className: "asset-manager__badge asset-manager__badge--muted",
		};
	}
	if (isApiKeyExpiringSoon(apiKey)) {
		return {
			label: "即将到期",
			className: "asset-manager__badge asset-manager__badge--warning",
		};
	}
	return {
		label: "有效",
		className: "asset-manager__badge asset-records__source-badge",
	};
}

function formatExpiryNotice(apiKey: AgentApiKeyRecord): string | null {
	if (!apiKey.expires_at || !isApiKeyExpiringSoon(apiKey)) {
		return null;
	}
	const expiresAt = Date.parse(apiKey.expires_at);
	if (!Number.isFinite(expiresAt)) {
		return null;
	}
	const remainingMs = expiresAt - Date.now();
	const remainingDays = Math.ceil(remainingMs / (24 * 60 * 60 * 1000));
	if (remainingDays <= 1) {
		return "这个 API Key 将在 24 小时内到期。请尽快轮换，避免自动化请求中断。";
	}
	return `这个 API Key 将在 ${remainingDays} 天内到期。建议提前完成轮换并更新调用方配置。`;
}

function getExpirySelectionValue(expiresInDays: number | null): string {
	if (expiresInDays === null) {
		return "never";
	}
	return String(expiresInDays);
}

function parseExpirySelectionValue(value: string): number | null {
	return value === "never" ? null : Number(value);
}

async function copyTextToClipboard(value: string): Promise<void> {
	if (
		typeof navigator !== "undefined"
		&& navigator.clipboard
		&& typeof navigator.clipboard.writeText === "function"
	) {
		await navigator.clipboard.writeText(value);
		return;
	}

	if (typeof document === "undefined") {
		throw new Error("当前环境不支持剪贴板复制。");
	}

	const textarea = document.createElement("textarea");
	textarea.value = value;
	textarea.setAttribute("readonly", "true");
	textarea.style.position = "absolute";
	textarea.style.opacity = "0";
	document.body.appendChild(textarea);
	textarea.select();
	try {
		document.execCommand("copy");
	} finally {
		document.body.removeChild(textarea);
	}
}

function RegisteredAgentList({
	registrations,
	apiKeyByName,
	emptyMessage,
}: {
	registrations: AgentRegistrationRecord[];
	apiKeyByName: Map<string, AgentApiKeyRecord>;
	emptyMessage: string;
}) {
	if (registrations.length === 0) {
		return <div className="asset-manager__empty-state">{emptyMessage}</div>;
	}

	return (
		<ul className="asset-manager__list">
			{registrations.map((registration) => {
				const latestApiKey = registration.latest_api_key_name
					? apiKeyByName.get(registration.latest_api_key_name) ?? null
					: null;
				const expiryNotice = latestApiKey ? formatExpiryNotice(latestApiKey) : null;

				return (
					<li key={`${registration.user_id}-${registration.id}`} className="asset-manager__card">
						<div className="asset-manager__card-top">
							<div className="asset-manager__card-title">
								<div className="asset-manager__badge-row">
									<span
										className={`asset-manager__badge ${
											registration.status === "ACTIVE"
												? "asset-records__source-badge"
												: "asset-manager__badge--muted"
										}`}
									>
										{registration.status === "ACTIVE" ? "活跃" : "非活跃"}
									</span>
									<span className="asset-manager__badge asset-manager__badge--muted">
										账号 {registration.user_id}
									</span>
									{latestApiKey ? (
										<span className="asset-manager__badge asset-manager__badge--muted">
											{getVisibleTokenHint(latestApiKey.token_hint)}
										</span>
									) : null}
								</div>
								<h3>{registration.name}</h3>
								<p className="asset-manager__card-note">
									查看这个 Agent 最近一次活跃时使用的 API Key 与接入时间。
								</p>
							</div>
						</div>
						{expiryNotice ? (
							<div className="asset-manager__status-note asset-manager__status-note--warning">
								{expiryNotice}
							</div>
						) : null}
						<div className="asset-manager__metric-grid">
							<div className="asset-manager__metric">
								<span>请求次数</span>
								<strong>{registration.request_count}</strong>
							</div>
								<div className="asset-manager__metric">
									<span>最近 API Key</span>
									<strong>{registration.latest_api_key_name ?? "—"}</strong>
								</div>
								<div className="asset-manager__metric">
									<span>Key</span>
									<strong>
										{latestApiKey ? getVisibleTokenHint(latestApiKey.token_hint) : "—"}
									</strong>
								</div>
							<div className="asset-manager__metric">
								<span>最近接入</span>
								<strong>{formatTimestamp(registration.last_seen_at)}</strong>
							</div>
							<div className="asset-manager__metric">
								<span>首次登记</span>
								<strong>{formatTimestamp(registration.created_at)}</strong>
							</div>
						</div>
					</li>
				);
			})}
		</ul>
	);
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

			<AgentWorkspaceDialog
				open={isCreateDialogOpen}
				onClose={closeCreateDialog}
				title={issuedApiKey ? "新 API Key" : "创建新的 API Key"}
				eyebrow="API KEY"
				description={
					issuedApiKey
						? "完整 Key 只会显示这一次。请立即复制并存入密码管理器、系统钥匙串或其他安全位置。"
						: `每个账号最多保留 ${MAX_ACTIVE_API_KEYS} 个有效 Key，每日最多生成 ${MAX_DAILY_API_KEY_CREATIONS} 次。新签发的 Key 统一以 sk- 开头。`
				}
				dialogScope="agent-workspace-create-key"
			>
				<div className="agent-workspace__modal-body">
					{apiKeyErrorMessage ? (
						<div className="asset-manager__message asset-manager__message--error">
							{apiKeyErrorMessage}
						</div>
					) : null}
					{clipboardError ? (
						<div className="asset-manager__message asset-manager__message--error">
							{clipboardError}
						</div>
					) : null}
					{clipboardNotice ? (
						<div className="asset-manager__status-note">{clipboardNotice}</div>
					) : null}
					{issuedApiKey ? (
						<div className="agent-workspace__one-time-secret">
							<div className="asset-manager__helper-block asset-manager__helper-block--highlight">
								<strong>{issuedApiKey.name}</strong>
								<p>这是完整密钥的唯一展示机会。关闭窗口后，平台只会保留 Key 预览。</p>
							</div>
							<pre className="asset-manager__code-block">{issuedApiKey.access_token}</pre>
							<div className="asset-manager__metric-grid">
								<div className="asset-manager__metric">
									<span>Key</span>
									<strong>{getVisibleTokenHint(issuedApiKey.token_hint)}</strong>
								</div>
								<div className="asset-manager__metric">
									<span>过期时间</span>
									<strong>
										{issuedApiKey.expires_at
											? formatTimestampWithYear(issuedApiKey.expires_at)
											: "永不过期"}
									</strong>
								</div>
							</div>
							<div className="asset-manager__form-actions">
								<button
									type="button"
									className="asset-manager__button"
									onClick={() => void handleCopyIssuedApiKey()}
								>
									复制到剪贴板
								</button>
								<button
									type="button"
									className="asset-manager__button asset-manager__button--secondary"
									onClick={closeCreateDialog}
								>
									我已保存
								</button>
							</div>
						</div>
					) : (
						<form className="asset-manager__form" onSubmit={handleCreateApiKeySubmit}>
							<div className="asset-manager__helper-block">
								<strong>命名规则</strong>
								<p>
									API Key 名称只能使用小写字母和连字符，例如 <code>daily-sync</code>、
									<code>local-cli</code> 或 <code>portfolio-agent</code>。
								</p>
							</div>
							<label className="asset-manager__field">
								<span>Key 名称</span>
								<input
									value={draftApiKeyName}
									onChange={(event) => setDraftApiKeyName(event.target.value)}
									placeholder="例如：daily-sync"
									maxLength={80}
									disabled={isCreatingApiKey || activeApiKeyCount >= MAX_ACTIVE_API_KEYS}
								/>
							</label>
							{normalizedDraftApiKeyName.length > 0 && !isDraftNameValid ? (
								<div className="asset-manager__message asset-manager__message--error">
									API Key 名称只能使用小写字母和连字符，不支持数字、空格或其他符号。
								</div>
							) : null}
							<label className="asset-manager__field">
								<span>有效期</span>
								<select
									value={draftExpirySelection}
									onChange={(event) => setDraftExpirySelection(event.target.value)}
									disabled={isCreatingApiKey || activeApiKeyCount >= MAX_ACTIVE_API_KEYS}
								>
									{EXPIRY_OPTIONS.map((option) => (
										<option key={option.value} value={option.value}>
											{option.label}
										</option>
									))}
								</select>
							</label>
							<p className="asset-manager__helper-text">
								{
									EXPIRY_OPTIONS.find((option) => option.value === draftExpirySelection)?.description
								}
							</p>
							<div className="asset-manager__form-actions">
								<button
									type="submit"
									className="asset-manager__button"
									disabled={
										isCreatingApiKey
										|| activeApiKeyCount >= MAX_ACTIVE_API_KEYS
										|| normalizedDraftApiKeyName.length < 3
										|| !isDraftNameValid
									}
								>
									{isCreatingApiKey ? "生成中..." : "生成 API Key"}
								</button>
							</div>
						</form>
					)}
				</div>
			</AgentWorkspaceDialog>

			<AgentWorkspaceDialog
				open={isManageKeysDialogOpen}
				onClose={closeManageKeysDialog}
				title="有效 Key"
				eyebrow="API KEYS"
				description="这里可以查看当前账号仍有效的 API Key 元信息并删除旧 Key。出于安全原因，完整 Key 不会再次显示，也不支持从这里复制。"
				dialogScope="agent-workspace-manage-keys"
			>
				<div className="agent-workspace__modal-body">
					{apiKeyNoticeMessage ? (
						<div className="asset-manager__status-note">{apiKeyNoticeMessage}</div>
					) : null}
					<div className="asset-manager__helper-block">
						<strong>当前状态</strong>
						<p>
							{activeApiKeySummary}。删除后会立即失效并释放名额。已删除或已过期的 API Key
							将自动移除。
						</p>
					</div>
					{expiringApiKeyCount > 0 ? (
						<div className="asset-manager__status-note asset-manager__status-note--warning">
							系统检测到 {expiringApiKeyCount} 个 API Key 将在 3 天内过期。建议尽快新建并轮换到新的 Key。
						</div>
					) : null}
					<div className="agent-workspace__scroll-region">
						{activeApiKeys.length === 0 ? (
							<div className="asset-manager__empty-state">当前账号还没有有效的 API Key。</div>
						) : (
							<ul className="asset-manager__list">
								{activeApiKeys.map((apiKey) => {
									const status = getApiKeyStatus(apiKey);
									const expiryNotice = formatExpiryNotice(apiKey);
									return (
										<li key={apiKey.id} className="asset-manager__card">
											<div className="asset-manager__card-top">
												<div className="asset-manager__card-title">
													<div className="asset-manager__badge-row">
														<span className="asset-manager__badge">API KEY</span>
														<span className={status.className}>{status.label}</span>
														<span className="asset-manager__badge asset-manager__badge--muted">
															{getVisibleTokenHint(apiKey.token_hint)}
														</span>
													</div>
													<h3>{apiKey.name}</h3>
													<p className="asset-manager__card-note">
														仅保留 Key 预览，完整密钥在创建后不会再次返回。
													</p>
												</div>
												<div className="asset-manager__card-actions">
													<button
														type="button"
														className="asset-manager__button asset-manager__button--secondary"
														onClick={() => requestRevokeApiKey(apiKey)}
														disabled={revokingApiKeyId === apiKey.id}
													>
														{revokingApiKeyId === apiKey.id ? "删除中..." : "删除"}
													</button>
												</div>
											</div>
											{expiryNotice ? (
												<div className="asset-manager__status-note asset-manager__status-note--warning">
													{expiryNotice}
												</div>
											) : null}
											<div className="asset-manager__metric-grid">
												<div className="asset-manager__metric">
													<span>Key</span>
													<strong>{getVisibleTokenHint(apiKey.token_hint)}</strong>
												</div>
												<div className="asset-manager__metric">
													<span>创建时间</span>
													<strong>{formatTimestampWithYear(apiKey.created_at)}</strong>
												</div>
												<div className="asset-manager__metric">
													<span>最近使用</span>
													<strong>{formatTimestampWithYear(apiKey.last_used_at)}</strong>
												</div>
												<div className="asset-manager__metric">
													<span>过期时间</span>
													<strong>
														{apiKey.expires_at
															? formatTimestampWithYear(apiKey.expires_at)
															: "永不过期"}
													</strong>
												</div>
											</div>
										</li>
									);
								})}
							</ul>
						)}
					</div>
				</div>
			</AgentWorkspaceDialog>

			<AgentWorkspaceDialog
				open={pendingRevokeApiKey !== null}
				onClose={cancelRevokeApiKey}
				title="删除 API Key"
				eyebrow="CONFIRM"
				description="删除后，这个 Key 会立刻失效，后续请求无法再通过它完成鉴权。这个操作不能撤销。"
				dialogScope="agent-workspace-confirm-revoke-key"
				panelClassName="agent-workspace__confirm-panel"
			>
				<div className="agent-workspace__modal-body">
						<div className="asset-manager__helper-block asset-manager__helper-block--highlight">
							<strong>{pendingRevokeApiKey?.name ?? "待删除 API Key"}</strong>
							<p>
								Key：
								{" "}
								<code>
									{pendingRevokeApiKey
									? getVisibleTokenHint(pendingRevokeApiKey.token_hint)
									: "—"}
							</code>
						</p>
					</div>
					<div className="asset-manager__form-actions">
						<button
							type="button"
							className="asset-manager__button asset-manager__button--secondary"
							onClick={cancelRevokeApiKey}
							disabled={pendingRevokeApiKey !== null && revokingApiKeyId === pendingRevokeApiKey.id}
						>
							取消
						</button>
						<button
							type="button"
							className="asset-manager__button"
							onClick={confirmRevokeApiKey}
							disabled={pendingRevokeApiKey !== null && revokingApiKeyId === pendingRevokeApiKey.id}
						>
							{pendingRevokeApiKey !== null && revokingApiKeyId === pendingRevokeApiKey.id
								? "删除中..."
								: "确认删除"}
						</button>
					</div>
				</div>
			</AgentWorkspaceDialog>

			<AgentWorkspaceDialog
				open={isActivityDialogOpen}
				onClose={() => setIsActivityDialogOpen(false)}
				title="记录"
				eyebrow="API ACTIVITY"
				description="按来源和资产类别查看 API 触发的真实落库记录。这里只读展示，不支持撤销。"
				dialogScope="agent-workspace-activity"
			>
				<div className="agent-workspace__modal-body">
					<div className="asset-records__filters">
						<div className="asset-records__filter-group">
							<span className="asset-records__filter-label">来源</span>
							<div className="asset-manager__filter-row">
								{([
									["ALL", "全部"],
									["AGENT", "Agent"],
									["API", "直连 API"],
								] as const).map(([value, label]) => (
									<button
										key={value}
										type="button"
										className={`asset-manager__filter-chip ${
											activitySourceFilter === value ? "is-active" : ""
										}`}
										onClick={() => setActivitySourceFilter(value)}
									>
										{label}
									</button>
								))}
							</div>
						</div>
						<div className="asset-records__filter-group">
							<span className="asset-records__filter-label">资产类别</span>
							<div className="asset-manager__filter-row">
								{ASSET_CLASS_FILTERS.map((option) => (
									<button
										key={option.value}
										type="button"
										className={`asset-manager__filter-chip ${
											activityAssetClassFilter === option.value ? "is-active" : ""
										}`}
										onClick={() => setActivityAssetClassFilter(option.value)}
									>
										{option.label}
									</button>
								))}
							</div>
						</div>
					</div>
					<div className="agent-workspace__scroll-region">
						<section className="agent-workspace__dialog-section">
							<div className="asset-manager__list-head">
								<div>
									<h3>落库记录</h3>
									<p>记录真实写入数据库的资产操作，并标明 API Key 名称与 Agent 名称。</p>
								</div>
							</div>
							<AgentRecordList
								records={filteredRecords}
								emptyMessage="当前筛选条件下还没有落库记录。"
							/>
						</section>
					</div>
				</div>
			</AgentWorkspaceDialog>
		</section>
	);
}
