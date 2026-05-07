import { useEffect } from "react";
import { useBodyScrollLock } from "../../hooks/useBodyScrollLock";
import { useAutoRefreshGuard } from "../../lib/autoRefreshGuards";

export interface AssetDeleteDialogProps {
	open: boolean;
	busy?: boolean;
	title: string;
	description: string;
	impactItems: string[];
	confirmLabel?: string;
	cancelLabel?: string;
	onConfirm: () => void;
	onClose: () => void;
}

export function AssetDeleteDialog({
	open,
	busy = false,
	title,
	description,
	impactItems,
	confirmLabel = "确认删除",
	cancelLabel = "取消",
	onConfirm,
	onClose,
}: AssetDeleteDialogProps) {
	useBodyScrollLock(open);
	useAutoRefreshGuard(open, "asset-delete-dialog");

	useEffect(() => {
		if (!open) {
			return;
		}

		function handleKeyDown(event: KeyboardEvent): void {
			if (event.key === "Escape" && !busy) {
				onClose();
			}
		}

		window.addEventListener("keydown", handleKeyDown);
		return () => window.removeEventListener("keydown", handleKeyDown);
	}, [busy, onClose, open]);

	if (!open) {
		return null;
	}

	return (
		<div className="asset-manager__modal" role="dialog" aria-modal="true" aria-labelledby="asset-delete-title">
			<button
				type="button"
				className="asset-manager__modal-backdrop"
				onClick={busy ? undefined : onClose}
				aria-label="关闭删除确认窗口"
			/>
			<div className="asset-manager__modal-panel">
				<div className="asset-manager__modal-head">
					<div>
						<p className="asset-manager__eyebrow">DELETE CONFIRMATION</p>
						<h3 id="asset-delete-title">{title}</h3>
						<p>{description}</p>
					</div>
				</div>

				<div className="asset-manager__helper-block asset-manager__helper-block--danger">
					<strong>删除后不可恢复</strong>
					<ul className="asset-manager__danger-list">
						{impactItems.map((impactItem) => (
							<li key={impactItem}>{impactItem}</li>
						))}
					</ul>
				</div>

				<div className="asset-manager__form-actions">
					<button
						type="button"
						className="asset-manager__button asset-manager__button--secondary"
						onClick={onClose}
						disabled={busy}
					>
						{cancelLabel}
					</button>
					<button
						type="button"
						className="asset-manager__button asset-manager__button--danger"
						onClick={onConfirm}
						disabled={busy}
					>
						{busy ? "删除中..." : confirmLabel}
					</button>
				</div>
			</div>
		</div>
	);
}
