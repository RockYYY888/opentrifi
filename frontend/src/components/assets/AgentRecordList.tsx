import {
	formatDateValue,
	formatMoneyAmount,
	formatPercentValue,
	formatPriceAmount,
	formatTimestamp,
} from "../../lib/assetFormatting";
import {
	ASSET_CLASS_BADGE_LABELS,
	OPERATION_BADGE_LABELS,
	SOURCE_BADGE_LABELS,
} from "../../lib/assetRecordMeta";
import type { AssetRecordRecord, AssetRecordSource } from "../../types/assets";

const REQUEST_SOURCE_LABELS: Record<AssetRecordSource, string> = {
	USER: "用户",
	SYSTEM: "系统",
	API: "直连 API",
	AGENT: "Agent",
};

function formatRecordAmount(record: AssetRecordRecord): string | null {
	if (record.amount == null || !Number.isFinite(record.amount)) {
		return null;
	}
	if (!record.currency) {
		return String(record.amount);
	}
	if (record.asset_class === "investment") {
		return formatPriceAmount(record.amount, record.currency);
	}
	return formatMoneyAmount(record.amount, record.currency);
}

function describeRequestIdentity(
	source: AssetRecordSource,
	agentName?: string | null,
): string {
	if (source === "AGENT") {
		return agentName?.trim() ? `Agent · ${agentName}` : "Agent";
	}
	return REQUEST_SOURCE_LABELS[source];
}

export function AgentRecordList({
	records,
	emptyMessage,
}: {
	records: AssetRecordRecord[];
	emptyMessage: string;
}) {
	if (records.length === 0) {
		return <div className="asset-manager__empty-state">{emptyMessage}</div>;
	}

	return (
		<ul className="asset-manager__list asset-records__list">
			{records.map((record) => {
				const amount = formatRecordAmount(record);
				const hasProfit =
					record.profit_amount != null
					&& record.profit_currency
					&& record.profit_rate_pct != null;
				const profitToneClass =
					(record.profit_amount ?? 0) >= 0
						? "asset-records__profit-chip--positive"
						: "asset-records__profit-chip--negative";

				return (
					<li key={`${record.id}-${record.entity_type}`} className="asset-manager__card">
						<div className="asset-manager__card-top">
							<div className="asset-manager__card-title">
								<div className="asset-manager__badge-row">
									<span className="asset-manager__badge asset-manager__badge--muted">
										{ASSET_CLASS_BADGE_LABELS[record.asset_class]}
									</span>
									<span className="asset-manager__badge">
										{OPERATION_BADGE_LABELS[record.operation_kind]}
									</span>
									<span className="asset-manager__badge asset-records__source-badge">
										{SOURCE_BADGE_LABELS[record.source]}
									</span>
									<span className="asset-manager__badge asset-manager__badge--muted">
										{describeRequestIdentity(record.source, record.agent_name)}
									</span>
								</div>
								<h3>{record.title}</h3>
								<p className="asset-manager__card-note">{record.summary}</p>
							</div>
						</div>

						<div className="asset-manager__metric-grid">
							<div className="asset-manager__metric">
								<span>API Key 名称</span>
								<strong>{record.api_key_name ?? "—"}</strong>
							</div>
							<div className="asset-manager__metric">
								<span>请求来源</span>
								<strong>{describeRequestIdentity(record.source, record.agent_name)}</strong>
							</div>
							<div className="asset-manager__metric">
								<span>生效日期</span>
								<strong>{formatDateValue(record.effective_date)}</strong>
							</div>
							<div className="asset-manager__metric">
								<span>记录时间</span>
								<strong>{formatTimestamp(record.created_at)}</strong>
							</div>
							{amount ? (
								<div className="asset-manager__metric">
									<span>记录值</span>
									<strong>{amount}</strong>
								</div>
							) : null}
							{record.agent_task_id ? (
								<div className="asset-manager__metric">
									<span>关联任务</span>
									<strong>#{record.agent_task_id}</strong>
								</div>
							) : null}
							{hasProfit ? (
								<div className={`asset-manager__metric asset-records__profit-chip ${profitToneClass}`}>
									<span>已实现盈利</span>
									<strong>
										{formatMoneyAmount(
											record.profit_amount ?? 0,
											record.profit_currency ?? "CNY",
										)}
									</strong>
									<p className="asset-records__profit-rate">
										收益率 {formatPercentValue(record.profit_rate_pct)}
									</p>
								</div>
							) : null}
						</div>
					</li>
				);
			})}
		</ul>
	);
}
