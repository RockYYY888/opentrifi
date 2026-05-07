import { lazy, Suspense } from "react";

import { AgentExecutionAuditPanel } from "../components/assets/AgentExecutionAuditPanel";
import { AssetManager } from "../components/assets";
import { WorkspaceShell } from "./WorkspaceShell";
import type { AssetManagerSeeds } from "./dashboardRefresh";
import type {
	AgentApiKeyIssueRecord,
	AgentApiKeyRecord,
	AgentRegistrationRecord,
	AssetManagerController,
	AssetRecordRecord,
	CreateAgentApiKeyInput,
} from "../types/assets";
import type { DashboardResponse } from "../types/dashboard";
import type { WorkspaceView } from "./workspaceTypes";

const PortfolioAnalytics = lazy(async () => {
	const module = await import("../components/analytics");
	return { default: module.PortfolioAnalytics };
});

export interface AppWorkspaceSectionsProps {
	activeWorkspaceView: WorkspaceView;
	agentApiKeyErrorMessage: string | null;
	agentApiKeyNoticeMessage: string | null;
	agentApiKeys: AgentApiKeyRecord[];
	agentAuditErrorMessage: string | null;
	agentRecords: AssetRecordRecord[];
	agentRegistrations: AgentRegistrationRecord[];
	assetManagerController: AssetManagerController;
	assetManagerSeeds: AssetManagerSeeds | null;
	dashboard: DashboardResponse;
	isCreatingAgentApiKey: boolean;
	isLoadingAgentAudit: boolean;
	isLoadingDashboard: boolean;
	issuedAgentApiKey: AgentApiKeyIssueRecord | null;
	mountedWorkspaceViews: Record<WorkspaceView, boolean>;
	revokingAgentApiKeyId: number | null;
	onCreateAgentApiKey: (payload: CreateAgentApiKeyInput) => void;
	onDismissIssuedApiKey: () => void;
	onRecordsCommitted: () => void;
	onRevokeAgentApiKey: (tokenId: number) => void;
	onWorkspaceChange: (view: WorkspaceView) => void;
}

export function AppWorkspaceSections({
	activeWorkspaceView,
	agentApiKeyErrorMessage,
	agentApiKeyNoticeMessage,
	agentApiKeys,
	agentAuditErrorMessage,
	agentRecords,
	agentRegistrations,
	assetManagerController,
	assetManagerSeeds,
	dashboard,
	isCreatingAgentApiKey,
	isLoadingAgentAudit,
	isLoadingDashboard,
	issuedAgentApiKey,
	mountedWorkspaceViews,
	revokingAgentApiKeyId,
	onCreateAgentApiKey,
	onDismissIssuedApiKey,
	onRecordsCommitted,
	onRevokeAgentApiKey,
	onWorkspaceChange,
}: AppWorkspaceSectionsProps) {
	return (
		<>
			<WorkspaceShell activeView={activeWorkspaceView} onChange={onWorkspaceChange} />

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
						onCreateApiKey={onCreateAgentApiKey}
						onRevokeApiKey={onRevokeAgentApiKey}
						onDismissIssuedApiKey={onDismissIssuedApiKey}
					/>
				</section>
			) : null}
			<div
				className="integrated-stack"
				hidden={activeWorkspaceView !== "manage"}
				aria-hidden={activeWorkspaceView !== "manage"}
			>
				<AssetManager
					initialCashAccounts={assetManagerSeeds?.cashAccounts}
					initialHoldings={assetManagerSeeds?.holdings}
					initialFixedAssets={assetManagerSeeds?.fixedAssets}
					initialLiabilities={assetManagerSeeds?.liabilities}
					initialOtherAssets={assetManagerSeeds?.otherAssets}
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
					onRecordsCommitted={onRecordsCommitted}
				/>
			</div>
		</>
	);
}
