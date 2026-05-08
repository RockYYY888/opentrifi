import { useEffect, type ReactNode } from "react";
import { createPortal } from "react-dom";

import { useBodyScrollLock } from "../../hooks/useBodyScrollLock";
import { useAutoRefreshGuard } from "../../lib/autoRefreshGuards";

export interface AgentWorkspaceDialogProps {
	open: boolean;
	onClose: () => void;
	title: string;
	eyebrow: string;
	description: string;
	children: ReactNode;
	panelClassName?: string;
	dialogScope: string;
}

export function AgentWorkspaceDialog({
	open,
	onClose,
	title,
	eyebrow,
	description,
	children,
	panelClassName,
	dialogScope,
}: AgentWorkspaceDialogProps) {
	useBodyScrollLock(open);
	useAutoRefreshGuard(open, dialogScope);

	useEffect(() => {
		if (!open) {
			return;
		}

		function handleKeyDown(event: KeyboardEvent): void {
			if (event.key === "Escape") {
				onClose();
			}
		}

		window.addEventListener("keydown", handleKeyDown);
		return () => window.removeEventListener("keydown", handleKeyDown);
	}, [onClose, open]);

	if (!open || typeof document === "undefined") {
		return null;
	}

	return createPortal(
		<div className="feedback-modal" role="dialog" aria-modal="true" aria-labelledby={`${dialogScope}-title`}>
			<button
				type="button"
				className="feedback-modal__backdrop"
				onClick={onClose}
				aria-label={`关闭${title}`}
			/>
			<div
				className={`feedback-modal__panel agent-workspace__modal-panel ${panelClassName ?? ""}`.trim()}
			>
				<div className="feedback-modal__head agent-workspace__modal-head">
					<div>
						<p className="eyebrow">{eyebrow}</p>
						<h2 id={`${dialogScope}-title`}>{title}</h2>
						<p className="feedback-modal__copy">{description}</p>
					</div>
					<button
						type="button"
						className="hero-note hero-note--action"
						onClick={onClose}
					>
						关闭
					</button>
				</div>
				{children}
			</div>
		</div>,
		document.body,
	);
}
