import { useState } from "react";
import "./asset-components.css";
import {
	formatCashAccountType,
	formatCnyAmount,
	formatDateValue,
	formatMoneyAmount,
} from "../../lib/assetFormatting";
import { toErrorMessage } from "../../lib/apiClient";
import type { CashAccountRecord, MaybePromise } from "../../types/assets";
import { getCollectionLoadingState } from "./loadingState";
import { AssetDeleteDialog } from "./AssetDeleteDialog";
import {
	CASH_ACCOUNT_DELETE_DESCRIPTION,
	CASH_ACCOUNT_DELETE_IMPACT_ITEMS,
} from "./cashAccountDeleteCopy";

export interface CashAccountListProps {
	accounts: CashAccountRecord[];
	title?: string;
	subtitle?: string;
	loading?: boolean;
	busy?: boolean;
	errorMessage?: string | null;
	emptyMessage?: string;
	onCreate?: () => void;
	onTransfer?: () => void;
	onEdit?: (account: CashAccountRecord) => void;
	onDelete?: (recordId: number) => MaybePromise<unknown>;
}

export function CashAccountList({
	accounts,
	title = "现金账户",
	subtitle,
	loading = false,
	busy = false,
	errorMessage = null,
	emptyMessage = "暂无现金账户，先录入一笔备用金或存款。",
	onCreate,
	onTransfer,
	onEdit,
	onDelete,
}: CashAccountListProps) {
	const [localError, setLocalError] = useState<string | null>(null);
	const [deletingId, setDeletingId] = useState<number | null>(null);
	const [pendingDeleteAccount, setPendingDeleteAccount] = useState<CashAccountRecord | null>(null);

	const effectiveError = localError ?? errorMessage;
	const isActionLocked = busy;
	const { showBlockingLoader, showRefreshingHint } = getCollectionLoadingState(
		loading,
		accounts.length,
	);

	async function handleDelete(recordId: number): Promise<boolean> {
		if (!onDelete) {
			return false;
		}

		setLocalError(null);
		setDeletingId(recordId);

		try {
			await onDelete(recordId);
			return true;
		} catch (error) {
			setLocalError(toErrorMessage(error, "删除现金账户失败，请稍后重试。"));
			return false;
		} finally {
			setDeletingId(null);
		}
	}

	function requestDelete(account: CashAccountRecord): void {
		setLocalError(null);
		setPendingDeleteAccount(account);
	}

	return (
		<>
			<section className="asset-manager__panel">
				<div className="asset-manager__list-head">
					<div>
						<p className="asset-manager__eyebrow">CASH LIST</p>
						<h3>{title}</h3>
						{subtitle ? <p>{subtitle}</p> : null}
					</div>
					<div className="asset-manager__mini-actions">
						{onTransfer ? (
							<button
								type="button"
								className="asset-manager__button asset-manager__button--secondary"
								onClick={onTransfer}
								disabled={isActionLocked || accounts.length < 2}
							>
								账户划转
							</button>
						) : null}
						{onCreate ? (
							<button
								type="button"
								className="asset-manager__button asset-manager__button--primary"
								onClick={onCreate}
								disabled={isActionLocked}
							>
								新增
							</button>
						) : null}
					</div>
				</div>

				{effectiveError ? (
					<div className="asset-manager__message asset-manager__message--error">
						{effectiveError}
					</div>
				) : null}

				{showRefreshingHint ? (
					<div className="asset-manager__status-note" role="status" aria-live="polite">
						正在更新现金账户...
					</div>
				) : null}

				{showBlockingLoader ? (
					<div className="asset-manager__empty-state">正在加载现金账户...</div>
				) : accounts.length === 0 ? (
					<div className="asset-manager__empty-state">{emptyMessage}</div>
				) : (
					<ul className="asset-manager__list">
						{accounts.map((account) => (
							<li key={account.id} className="asset-manager__card">
								<div className="asset-manager__card-top">
									<div className="asset-manager__card-title">
										<div className="asset-manager__badge-row">
											<span className="asset-manager__badge">
												{formatCashAccountType(account.account_type)}
											</span>
										</div>
										<h3>{account.name}</h3>
										<p className="asset-manager__card-note">
											{account.note?.trim() || `账户 ID #${account.id}`}
										</p>
									</div>
									<div className="asset-manager__card-actions">
										{onEdit ? (
											<button
												type="button"
												className="asset-manager__button asset-manager__button--secondary"
												onClick={() => onEdit(account)}
												disabled={isActionLocked}
											>
												编辑
											</button>
										) : null}
										{onDelete ? (
											<button
												type="button"
												className="asset-manager__button asset-manager__button--danger"
												onClick={() => requestDelete(account)}
												disabled={busy || deletingId === account.id}
											>
												{deletingId === account.id ? "删除中..." : "删除"}
											</button>
										) : null}
									</div>
								</div>

								<div className="asset-manager__metric-grid">
									<div className="asset-manager__metric">
										<span>账户余额</span>
										<strong>
											{formatMoneyAmount(account.balance, account.currency)}
										</strong>
									</div>
									<div className="asset-manager__metric">
										<span>折算人民币</span>
										<strong>{formatCnyAmount(account.value_cny)}</strong>
									</div>
									<div className="asset-manager__metric">
										<span>存入日</span>
										<strong>{formatDateValue(account.started_on)}</strong>
									</div>
								</div>
							</li>
						))}
					</ul>
				)}
			</section>

			<AssetDeleteDialog
				open={pendingDeleteAccount !== null}
				busy={pendingDeleteAccount !== null && deletingId === pendingDeleteAccount.id}
				title={`确认删除 ${pendingDeleteAccount?.name ?? "这个现金账户"}？`}
				description={CASH_ACCOUNT_DELETE_DESCRIPTION}
				impactItems={[...CASH_ACCOUNT_DELETE_IMPACT_ITEMS]}
				onClose={() => {
					if (deletingId === null) {
						setPendingDeleteAccount(null);
					}
				}}
				onConfirm={() => {
					if (pendingDeleteAccount == null) {
						return;
					}
					void handleDelete(pendingDeleteAccount.id).then((deleted) => {
						if (deleted) {
							setPendingDeleteAccount(null);
						}
					});
				}}
			/>
		</>
	);
}
