import { formatTimestamp } from "../../lib/assetFormatting";
import type { AgentApiKeyRecord, AgentRegistrationRecord } from "../../types/assets";
import {
	formatExpiryNotice,
	getVisibleTokenHint,
} from "./AgentExecutionAuditModel";

export function RegisteredAgentList({
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
