import type { AssetRecordRecord } from "../../types/assets";
import {
	ASSET_CLASS_FILTERS,
	type ActivityAssetClassFilter,
	type ActivitySourceFilter,
} from "./AgentExecutionAuditModel";
import { AgentRecordList } from "./AgentRecordList";
import { AgentWorkspaceDialog } from "./AgentWorkspaceDialog";

interface AgentActivityDialogProps {
	activityAssetClassFilter: ActivityAssetClassFilter;
	activitySourceFilter: ActivitySourceFilter;
	filteredRecords: AssetRecordRecord[];
	open: boolean;
	onActivityAssetClassFilterChange: (value: ActivityAssetClassFilter) => void;
	onActivitySourceFilterChange: (value: ActivitySourceFilter) => void;
	onClose: () => void;
}

export function AgentActivityDialog({
	activityAssetClassFilter,
	activitySourceFilter,
	filteredRecords,
	open,
	onActivityAssetClassFilterChange,
	onActivitySourceFilterChange,
	onClose,
}: AgentActivityDialogProps) {
	return (
		<AgentWorkspaceDialog
			open={open}
			onClose={onClose}
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
									onClick={() => onActivitySourceFilterChange(value)}
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
									onClick={() => onActivityAssetClassFilterChange(option.value)}
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
	);
}
