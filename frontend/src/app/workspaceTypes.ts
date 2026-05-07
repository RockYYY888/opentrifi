export type WorkspaceView = "manage" | "agent" | "insights";

export const DEFAULT_MOUNTED_WORKSPACES: Record<WorkspaceView, boolean> = {
	manage: true,
	insights: false,
	agent: false,
};
