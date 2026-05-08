import type { FormEvent } from "react";

import { formatTimestampWithYear } from "../../lib/assetFormatting";
import type {
	AgentApiKeyIssueRecord,
	AgentApiKeyRecord,
} from "../../types/assets";
import {
	EXPIRY_OPTIONS,
	formatExpiryNotice,
	getApiKeyStatus,
	getVisibleTokenHint,
	MAX_ACTIVE_API_KEYS,
	MAX_DAILY_API_KEY_CREATIONS,
} from "./AgentExecutionAuditModel";
import { AgentWorkspaceDialog } from "./AgentWorkspaceDialog";

interface AgentApiKeyDialogsProps {
	activeApiKeyCount: number;
	activeApiKeySummary: string;
	activeApiKeys: AgentApiKeyRecord[];
	apiKeyErrorMessage: string | null;
	apiKeyNoticeMessage: string | null;
	clipboardError: string | null;
	clipboardNotice: string | null;
	draftApiKeyName: string;
	draftExpirySelection: string;
	expiringApiKeyCount: number;
	isCreateDialogOpen: boolean;
	isCreatingApiKey: boolean;
	isDraftNameValid: boolean;
	isManageKeysDialogOpen: boolean;
	issuedApiKey: AgentApiKeyIssueRecord | null;
	normalizedDraftApiKeyName: string;
	pendingRevokeApiKey: AgentApiKeyRecord | null;
	revokingApiKeyId: number | null;
	onCancelRevokeApiKey: () => void;
	onCloseCreateDialog: () => void;
	onCloseManageKeysDialog: () => void;
	onConfirmRevokeApiKey: () => void;
	onCopyIssuedApiKey: () => void;
	onCreateApiKeySubmit: (event: FormEvent<HTMLFormElement>) => void;
	onDraftApiKeyNameChange: (value: string) => void;
	onDraftExpirySelectionChange: (value: string) => void;
	onRequestRevokeApiKey: (apiKey: AgentApiKeyRecord) => void;
}

export function AgentApiKeyDialogs({
	activeApiKeyCount,
	activeApiKeySummary,
	activeApiKeys,
	apiKeyErrorMessage,
	apiKeyNoticeMessage,
	clipboardError,
	clipboardNotice,
	draftApiKeyName,
	draftExpirySelection,
	expiringApiKeyCount,
	isCreateDialogOpen,
	isCreatingApiKey,
	isDraftNameValid,
	isManageKeysDialogOpen,
	issuedApiKey,
	normalizedDraftApiKeyName,
	pendingRevokeApiKey,
	revokingApiKeyId,
	onCancelRevokeApiKey,
	onCloseCreateDialog,
	onCloseManageKeysDialog,
	onConfirmRevokeApiKey,
	onCopyIssuedApiKey,
	onCreateApiKeySubmit,
	onDraftApiKeyNameChange,
	onDraftExpirySelectionChange,
	onRequestRevokeApiKey,
}: AgentApiKeyDialogsProps) {
	return (
		<>
			<AgentWorkspaceDialog
				open={isCreateDialogOpen}
				onClose={onCloseCreateDialog}
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
									onClick={onCopyIssuedApiKey}
								>
									复制到剪贴板
								</button>
								<button
									type="button"
									className="asset-manager__button asset-manager__button--secondary"
									onClick={onCloseCreateDialog}
								>
									我已保存
								</button>
							</div>
						</div>
					) : (
						<form className="asset-manager__form" onSubmit={onCreateApiKeySubmit}>
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
									onChange={(event) => onDraftApiKeyNameChange(event.target.value)}
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
									onChange={(event) => onDraftExpirySelectionChange(event.target.value)}
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
								{EXPIRY_OPTIONS.find((option) => option.value === draftExpirySelection)?.description}
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
				onClose={onCloseManageKeysDialog}
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
														onClick={() => onRequestRevokeApiKey(apiKey)}
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
				onClose={onCancelRevokeApiKey}
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
							Key：{" "}
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
							onClick={onCancelRevokeApiKey}
							disabled={pendingRevokeApiKey !== null && revokingApiKeyId === pendingRevokeApiKey.id}
						>
							取消
						</button>
						<button
							type="button"
							className="asset-manager__button"
							onClick={onConfirmRevokeApiKey}
							disabled={pendingRevokeApiKey !== null && revokingApiKeyId === pendingRevokeApiKey.id}
						>
							{pendingRevokeApiKey !== null && revokingApiKeyId === pendingRevokeApiKey.id
								? "删除中..."
								: "确认删除"}
						</button>
					</div>
				</div>
			</AgentWorkspaceDialog>
		</>
	);
}
