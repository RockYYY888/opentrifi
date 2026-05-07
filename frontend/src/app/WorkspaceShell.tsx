import type { WorkspaceView } from "./workspaceTypes";

type WorkspaceShellProps = {
	activeView: WorkspaceView;
	onChange: (view: WorkspaceView) => void;
};

const WORKSPACE_TABS: Array<{ value: WorkspaceView; label: string }> = [
	{ value: "manage", label: "管理" },
	{ value: "insights", label: "洞察" },
	{ value: "agent", label: "智能体" },
];

export function WorkspaceShell({ activeView, onChange }: WorkspaceShellProps) {
	return (
		<section className="panel workspace-shell" aria-label="页面视图切换">
			<div className="workspace-switch" role="tablist" aria-label="页面视图">
				{WORKSPACE_TABS.map((tab) => (
					<button
						key={tab.value}
						type="button"
						role="tab"
						aria-selected={activeView === tab.value}
						className={`workspace-switch__button ${
							activeView === tab.value ? "is-active" : ""
						}`}
						onClick={() => onChange(tab.value)}
					>
						{tab.label}
					</button>
				))}
			</div>
		</section>
	);
}
