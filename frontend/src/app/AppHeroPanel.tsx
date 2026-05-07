import {
	formatFxRate,
	formatLastUpdated,
	formatSummaryCny,
} from "./dashboardRefresh";
import type { DashboardResponse } from "../types/dashboard";
import { formatCny } from "../utils/portfolioAnalytics";

export interface AppHeroPanelProps {
	currentUserId: string;
	currentUserEmail: string | null;
	dashboard: DashboardResponse;
	feedbackInboxCount: number;
	isDashboardBusy: boolean;
	isLoadingAdminInbox: boolean;
	isLoadingAdminReleaseNotes: boolean;
	isLoadingUserInbox: boolean;
	isSubmittingEmail: boolean;
	lastUpdatedAt: string | null;
	showDashboardValuePlaceholder: boolean;
	onForceDashboardRefresh: () => void;
	onOpenAdminInbox: () => void;
	onOpenAdminReleaseNotes: () => void;
	onOpenAssetRecords: () => void;
	onOpenEmail: () => void;
	onOpenFeedback: () => void;
	onOpenUserInbox: () => void;
	onLogout: () => void;
}

export function AppHeroPanel({
	currentUserId,
	currentUserEmail,
	dashboard,
	feedbackInboxCount,
	isDashboardBusy,
	isLoadingAdminInbox,
	isLoadingAdminReleaseNotes,
	isLoadingUserInbox,
	isSubmittingEmail,
	lastUpdatedAt,
	showDashboardValuePlaceholder,
	onForceDashboardRefresh,
	onOpenAdminInbox,
	onOpenAdminReleaseNotes,
	onOpenAssetRecords,
	onOpenEmail,
	onOpenFeedback,
	onOpenUserInbox,
	onLogout,
}: AppHeroPanelProps) {
	function formatDashboardSummaryValue(value: number): string {
		return showDashboardValuePlaceholder ? "—" : formatSummaryCny(value);
	}

	function getDashboardSummaryTitle(value: number): string {
		return showDashboardValuePlaceholder ? "正在恢复数据" : formatCny(value);
	}

	return (
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
						onClick={onForceDashboardRefresh}
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
						onClick={onOpenEmail}
						disabled={isSubmittingEmail}
					>
						{currentUserEmail ? "修改邮箱" : "绑定邮箱"}
					</button>
					<button
						type="button"
						className="hero-note hero-note--action"
						onClick={currentUserId === "admin" ? onOpenAdminInbox : onOpenUserInbox}
						disabled={isLoadingAdminInbox || isLoadingUserInbox}
					>
						{feedbackInboxCount > 0 ? `消息 (${feedbackInboxCount})` : "消息"}
					</button>
					{currentUserId === "admin" ? (
						<button
							type="button"
							className="hero-note hero-note--action"
							onClick={onOpenAdminReleaseNotes}
							disabled={isLoadingAdminReleaseNotes}
						>
							更新日志
						</button>
					) : null}
					<button
						type="button"
						className="hero-note hero-note--action"
						onClick={onOpenAssetRecords}
					>
						记录
					</button>
					<button
						type="button"
						className="hero-note hero-note--action"
						onClick={onOpenFeedback}
					>
						反馈问题
					</button>
					<button
						type="button"
						className="hero-note hero-note--action"
						onClick={onLogout}
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
	);
}
